"""Pending 确认弹窗（问答升级阶段 3/4）：门控打回 + 沉淀确认。

- 门控打回（kind=review_ingest）：原文本 + 问题说明 + 修改建议 → [采纳修改 / 保持原样 / 丢弃]；
- 沉淀确认（kind=memory_save）：候选知识点卡片 + 判断依据 → [确认存入 / 拒绝]；
- 返回动作字符串（approve_modified / approve_raw / reject / approve / reject），
  由调用方调 /api/pending/decide 完成实际落库；用户关闭对话框返回 None（暂不处理，7 天过期）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout, QWidget,
)

from ..widgets import theme as T
from ..widgets.buttons import primary_button, ghost_button


class PendingDialog(QDialog):
    """Pending 确认弹窗基类：暗色 QDialog + 内容分区 + 底部动作栏。

    静态工厂 review_ingest() / memory_save() 构造并按 kind 布局，
    exec() 后通过返回值拿到用户选择的动作字符串（或 None=关闭对话框）。
    """

    def __init__(self, parent: QWidget | None, title: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(560)
        self.setStyleSheet(
            f"QDialog {{ background-color: {T.BG}; }}"
            f"QDialog QLabel {{ background: transparent; }}"
            f"QDialog QPlainTextEdit {{ background-color: {T.CARD}; color: {T.INK};"
            f"  border: 1px solid {T.BORDER}; border-radius: 8px;"
            f"  font-family: \"{T.F_SANS}\"; font-size: 10pt; padding: 8px; }}"
        )
        self._action: str | None = None
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(22, 18, 22, 18)
        self._lay.setSpacing(10)
        if parent is not None:
            self._center_over(parent.window())

    # ------------------------------------------------------------------
    # 布局辅助
    # ------------------------------------------------------------------
    def _center_over(self, win: QWidget) -> None:
        """父窗口居中（先 show 一次测量尺寸，再移动）。"""
        self.show()
        geo = self.frameGeometry()
        pg = win.frameGeometry()
        geo.moveCenter(pg.center())
        self.move(geo.topLeft())
        self.hide()

    def _label(self, text: str, color: str = T.INK2, *, bold: bool = False) -> QLabel:
        lbl = QLabel(text, self)
        weight = "bold" if bold else "normal"
        lbl.setStyleSheet(f"color: {color}; background: transparent;")
        lbl.setFont(T.font((T.F_SANS, 10, weight)))
        lbl.setWordWrap(True)
        return lbl

    def _section(self, text: str) -> None:
        self._lay.addWidget(self._label(text, T.MUTED, bold=True))

    def _readonly_box(self, text: str, max_height: int = 130) -> QPlainTextEdit:
        box = QPlainTextEdit(self)
        box.setReadOnly(True)
        box.setPlainText(text)
        box.setMaximumHeight(max_height)
        return box

    def _action_bar(self, actions: list[tuple[str, str, bool]]) -> None:
        """底部动作栏：[(动作值, 文案, 是否主按钮)]，点击 accept() 并记下动作值。"""
        row = QHBoxLayout()
        row.setSpacing(10)

        def _click(value: str) -> None:
            self._action = value
            self.accept()

        for value, text, primary in actions:
            btn = (primary_button(self, text, lambda v=value: _click(v))
                   if primary else ghost_button(self, text, lambda v=value: _click(v)))
            row.addWidget(btn)
        row.addStretch(1)
        self._lay.addLayout(row)

    # ------------------------------------------------------------------
    # 门控打回（review_ingest）
    # ------------------------------------------------------------------
    @classmethod
    def review_ingest(cls, parent: QWidget | None, item: dict) -> str | None:
        """路由门控打回确认：原文本 + 问题说明 + 可选修改建议。

        返回 approve_modified / approve_raw / reject，或 None（用户关闭）。
        """
        dlg = cls(parent, "内容需确认")
        payload = item.get("payload") or {}
        original = payload.get("original_text") or ""
        concern = payload.get("concern") or ""
        solution = payload.get("solution") or ""

        dlg._section("原文本")
        dlg._lay.addWidget(dlg._readonly_box(original, 110))

        dlg._section("问题说明")
        dlg._lay.addWidget(dlg._label(concern or "内容质量待确认（无法自动判断）"))

        if solution:
            dlg._section("修改建议")
            dlg._lay.addWidget(dlg._readonly_box(solution, 100))
            dlg._action_bar([
                ("approve_modified", "采纳修改", True),
                ("approve_raw", "保持原样", False),
                ("reject", "丢弃", False),
            ])
        else:
            dlg._action_bar([
                ("approve_raw", "保持原样", True),
                ("reject", "丢弃", False),
            ])
        dlg.exec()
        return dlg._action

    # ------------------------------------------------------------------
    # 沉淀确认（memory_save）
    # ------------------------------------------------------------------
    @classmethod
    def memory_save(cls, parent: QWidget | None, item: dict) -> str | None:
        """判断 Agent 沉淀确认：候选知识点列表 + 判断依据。

        返回 approve / reject，或 None（用户关闭）。
        """
        dlg = cls(parent, "发现可沉淀的知识点")
        payload = item.get("payload") or {}
        concepts = payload.get("concepts") or []
        reason = payload.get("reason") or ""
        if not concepts:
            concepts = [{"concept": "未命名知识点", "explanation": ""}]

        if reason:
            dlg._section("判断依据")
            dlg._lay.addWidget(dlg._label(reason))

        dlg._section(f"候选知识点（{len(concepts)} 条）")
        for c in concepts:
            dlg._lay.addWidget(dlg._concept_row(c))

        dlg._action_bar([
            ("approve", "确认存入", True),
            ("reject", "拒绝", False),
        ])
        dlg.exec()
        return dlg._action

    def _concept_row(self, c: dict) -> QFrame:
        frame = QFrame(self)
        frame.setStyleSheet(
            f"background-color: {T.CARD}; border: 1px solid {T.BORDER};"
            f" border-radius: 8px;")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(4)
        name = self._label(str(c.get("concept") or ""), T.ACCENT, bold=True)
        name.setFont(T.font(T.F_BODY))
        lay.addWidget(name)
        expl = str(c.get("explanation") or "").strip()
        if expl:
            lay.addWidget(self._label(expl, T.INK2))
        return frame
