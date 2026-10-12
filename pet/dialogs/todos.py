"""待办清单弹窗（PySide6 版）：列表 + 添加 + 勾选 + 删除 + 到期时间。

对应原 tkinter todos.py 的 TodoDialog：
- `_parse_due` 纯函数原样保留（支持 `YYYY-MM-DD HH:MM` 与 ISO 串，留空无期限）；
- 表格从 ttk.Treeview 换 QTableWidget，行数据经 `Qt.UserRole` 直接携带 todo_id，
  根治原版「按文本反查 id」在重复文本时错删/错勾的缺陷；
- 异步加载用 QThread（复用 AppController.run_async 模式，本地自持 worker）。
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit, QComboBox,
    QLabel, QHBoxLayout, QVBoxLayout, QAbstractItemView,
)

from ..api_client import ApiClient
from ..widgets import theme as T
from ..widgets.buttons import ghost_button, primary_button

_PRIO_COLOR = {"高": T.ERR, "中": T.WARN, "低": T.OK}
_ID_ROLE = Qt.ItemDataRole.UserRole


def _parse_due(raw: str) -> str | None:
    """解析到期时间；无法解析返回 None。"""
    s = (raw or "").strip()
    if not s:
        return None
    if "T" in s:
        try:
            datetime.fromisoformat(s)
            return s
        except ValueError:
            return None
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M")
        return dt.astimezone().isoformat(timespec="seconds")
    except ValueError:
        return None


class _ApiWorker(QThread):
    """单次后端调用工作线程。done(result, err) 回主线程。"""

    done = Signal(object, object)

    def __init__(self, fn, parent=None) -> None:
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:  # noqa: D102
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001
            self.done.emit(None, exc)
        else:
            self.done.emit(result, None)


class TodoDialog(QDialog):
    def __init__(self, api: ApiClient) -> None:
        super().__init__(None)
        self.api = api
        self._worker: _ApiWorker | None = None

        self.setWindowTitle("待办清单")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(620, 460)
        self.setMinimumSize(520, 400)
        self.setStyleSheet(f"background-color: {T.BG};")

        self._build()
        self._load()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        self._table = QTableWidget(0, 4, self)
        self._table.setHorizontalHeaderLabels(["✓", "优先级", "内容", "到期"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 44)
        self._table.setColumnWidth(1, 64)
        self._table.setColumnWidth(3, 150)
        self._table.doubleClicked.connect(lambda _i: self._toggle())
        self._table.setStyleSheet(
            f"QTableWidget {{ background-color: {T.CARD}; color: {T.INK};"
            f"  border: 1px solid {T.BORDER}; border-radius: 6px; }}"
            f"QTableWidget::item:selected {{ background-color: {T.CARD_ACTIVE}; }}"
            f"QHeaderView::section {{ background-color: {T.SURFACE};"
            f"  color: {T.INK2}; border: none; padding: 4px; font-weight: bold; }}"
            f"QTableWidget {{ alternate-background-color: {T.ALT_ROW}; }}"
        )
        lay.addWidget(self._table, 1)

        # 输入行：内容 + 优先级 + 到期
        row = QHBoxLayout()
        row.setSpacing(6)
        self._entry = QLineEdit(self)
        self._entry.setPlaceholderText("新待办内容…")
        self._entry.setStyleSheet(f"background-color: {T.CARD}; color: {T.INK};")
        self._entry.returnPressed.connect(self._add)
        row.addWidget(self._entry, 1)

        self._prio = QComboBox(self)
        self._prio.addItems(["高", "中", "低"])
        self._prio.setCurrentText("中")
        self._prio.setStyleSheet(f"background-color: {T.CARD}; color: {T.INK};")
        row.addWidget(self._prio)

        self._due = QLineEdit(self)
        self._due.setPlaceholderText("到期 2026-08-06 18:00（可空）")
        self._due.setFixedWidth(190)
        self._due.setStyleSheet(f"background-color: {T.CARD}; color: {T.INK};")
        self._due.returnPressed.connect(self._add)
        row.addWidget(self._due)
        lay.addLayout(row)

        # 操作按钮
        btns = QHBoxLayout()
        btns.setSpacing(8)
        add = primary_button(self, "添加", self._add)
        btns.addWidget(add)
        btns.addWidget(ghost_button(self, "勾选", self._toggle))
        btns.addWidget(ghost_button(self, "删除", self._delete))
        btns.addWidget(ghost_button(self, "刷新", self._load))
        btns.addStretch(1)
        lay.addLayout(btns)

        self._msg = QLabel("到期格式：2026-08-06 18:00（可空）", self)
        self._msg.setStyleSheet(f"color: {T.MUTED};")
        self._msg.setFont(T.font(T.F_TINY))
        lay.addWidget(self._msg)

    # ------------------------------------------------------------------
    # 加载 / 渲染
    # ------------------------------------------------------------------
    def _load(self) -> None:
        self._run(self.api.todos_list, self._load_done)

    def _load_done(self, result, err) -> None:
        if err:
            self._msg.setText(f"加载失败：{err}")
            self._msg.setStyleSheet(f"color: {T.ERR};")
            return
        items = (result or {}).get("items") or []
        self._table.setRowCount(len(items))
        for r, it in enumerate(items):
            todo_id = it.get("id")
            done = bool(it.get("done"))
            prio = it.get("priority") or "中"
            text = it.get("text") or ""
            due = (it.get("due_at") or "")[:16].replace("T", " ")

            done_item = QTableWidgetItem("✓" if done else "")
            prio_item = QTableWidgetItem(prio)
            text_item = QTableWidgetItem(text)
            due_item = QTableWidgetItem(due)

            for col, item in enumerate((done_item, prio_item, text_item, due_item)):
                item.setData(_ID_ROLE, todo_id)          # 行 id 直达，防重名错删
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter
                                      if col in (0, 1, 3)
                                      else Qt.AlignmentFlag.AlignVCenter)
                if done:
                    item.setForeground(QColor(T.MUTED))
                self._table.setItem(r, col, item)

            prio_item.setForeground(QColor(_PRIO_COLOR.get(prio, T.INK2)))
        self._msg.setText(f"共 {len(items)} 条")
        self._msg.setStyleSheet(f"color: {T.INK2};")

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------
    def _selected_id(self) -> str | None:
        """当前选中行的 todo_id；无选中返回 None。"""
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, 2)
        if item is None:
            return None
        return item.data(_ID_ROLE)

    def _add(self) -> None:
        text = self._entry.text().strip()
        if not text:
            self._msg.setText("内容不能为空")
            self._msg.setStyleSheet(f"color: {T.ERR};")
            return
        due_raw = self._due.text()
        due = _parse_due(due_raw)
        if due_raw.strip() and due is None:
            self._msg.setText("到期格式不正确（示例：2026-08-06 18:00）")
            self._msg.setStyleSheet(f"color: {T.ERR};")
            return
        self._run(lambda: self.api.todo_create(text, self._prio.currentText(), due),
                  self._after_add)
        self._entry.clear()
        self._entry.setFocus()

    def _after_add(self, result, err) -> None:
        if err:
            self._msg.setText(f"添加失败：{err}")
            self._msg.setStyleSheet(f"color: {T.ERR};")
        self._load()

    def _toggle(self) -> None:
        todo_id = self._selected_id()
        if not todo_id:
            return
        row = self._table.currentRow()
        new_done = self._table.item(row, 0).text() != "✓"
        self._run(lambda: self.api.todo_update(todo_id, done=new_done),
                  lambda _r, _e: self._load())

    def _delete(self) -> None:
        todo_id = self._selected_id()
        if not todo_id:
            return
        self._run(lambda: self.api.todo_delete(todo_id), lambda _r, _e: self._load())

    # ------------------------------------------------------------------
    # 异步
    # ------------------------------------------------------------------
    def _run(self, fn, on_done) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        worker = _ApiWorker(fn, self)
        self._worker = worker
        worker.done.connect(on_done)
        worker.done.connect(self._on_worker_done)
        worker.start()

    def _on_worker_done(self, *_args) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(300)
        super().closeEvent(event)
