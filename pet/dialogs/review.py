"""学习回顾复习弹窗（PySide6 版）：到期卡片 → 回忆 → 显示解释 → SM-2 打分。

对应原 tkinter review.py 的 ReviewDialog，流程不变：
先展示概念让主人回忆，点「显示解释」看答案，再打分（忘记/困难/记得/简单），
后端按 SM-2 更新复习间隔。异步加载用 QThread。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QLabel, QVBoxLayout, QHBoxLayout, QFrame, QPushButton,
    QSizePolicy,
)

from ..api_client import ApiClient
from ..widgets import theme as T
from ..widgets.buttons import primary_button

_GRADE_LABEL = {"again": "忘记", "hard": "困难", "good": "记得", "easy": "简单"}


def _grade_color(grade: str) -> str:
    return {"again": T.GRADE_AGAIN, "hard": T.GRADE_HARD,
            "good": T.GRADE_GOOD, "easy": T.GRADE_EASY}.get(grade, T.ERR)


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


class ReviewDialog(QDialog):
    def __init__(self, api: ApiClient) -> None:
        super().__init__(None)
        self.api = api
        self._cards: list[dict] = []
        self._idx = 0
        self._revealed = False
        self._worker: _ApiWorker | None = None

        self.setWindowTitle("学习回顾复习")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(520, 460)
        self.setMinimumSize(440, 400)
        self.setStyleSheet(f"background-color: {T.BG};")

        self._build()
        self._load()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(8)

        self._progress = QLabel("加载中…", self)
        self._progress.setStyleSheet(f"color: {T.INK2};")
        self._progress.setFont(T.font(T.F_SMALL))
        lay.addWidget(self._progress)

        # 概念卡
        self._concept = QFrame(self)
        self._concept.setStyleSheet(
            f"background-color: {T.CARD}; border: 1px solid {T.BORDER};"
            f" border-radius: 10px; padding: 12px;"
        )
        c_lay = QVBoxLayout(self._concept)
        c_lay.setContentsMargins(14, 12, 14, 12)
        self._concept_lbl = QLabel("", self._concept)
        self._concept_lbl.setWordWrap(True)
        self._concept_lbl.setStyleSheet("background: transparent; color: #EFF4FF;")
        self._concept_lbl.setFont(T.font(T.F_TITLE))
        c_lay.addWidget(self._concept_lbl)
        lay.addWidget(self._concept)

        # 解释区
        self._explanation = QLabel(
            "🤔 先回忆一下这是什么，然后点「显示解释」", self)
        self._explanation.setWordWrap(True)
        self._explanation.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._explanation.setStyleSheet(f"color: {T.INK2};")
        self._explanation.setFont(T.font(T.F_BODY))
        self._explanation.setSizePolicy(QSizePolicy.Policy.Expanding,
                                        QSizePolicy.Policy.Expanding)
        lay.addWidget(self._explanation, 1)

        self._msg = QLabel("", self)
        self._msg.setStyleSheet(f"color: {T.INK2};")
        self._msg.setFont(T.font(T.F_SMALL))
        lay.addWidget(self._msg)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._reveal_btn = primary_button(self, "显示解释", self._reveal)
        btn_row.addWidget(self._reveal_btn)
        btn_row.addStretch(1)

        self._grade_btns: dict[str, QPushButton] = {}
        for grade, label in (("again", "忘记"), ("hard", "困难"),
                             ("good", "记得"), ("easy", "简单")):
            color = _grade_color(grade)
            btn = QPushButton(label, self)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {color}; color: {T.ON_ACCENT};"
                f"  border: none; border-radius: 8px; padding: 6px 14px;"
                f"  font-family: \"{T.F_SANS}\"; font-weight: bold; font-size: 9pt; }}"
                f"QPushButton:hover {{ opacity: 0.85; }}"
                f"QPushButton:disabled {{ background-color: {T.CARD_ACTIVE};"
                f"  color: {T.MUTED}; }}"
            )
            btn.clicked.connect(lambda _c=False, g=grade: self._grade(g))
            btn.setEnabled(False)
            self._grade_btns[grade] = btn
            btn_row.addWidget(btn)
        lay.addLayout(btn_row)

    # ------------------------------------------------------------------
    def _load(self) -> None:
        self._run(self.api.learning_due, self._load_done)

    def _load_done(self, result, err) -> None:
        if err:
            self._progress.setText(f"加载失败：{err}")
            self._progress.setStyleSheet(f"color: {T.ERR};")
            return
        self._cards = (result or {}).get("items") or []
        if not self._cards:
            self._concept_lbl.setText("暂无到期的复习卡片")
            self._explanation.setText(
                "主人可以去柴犬菜单「学习回顾」提取新卡片，或稍后再来。")
            self._reveal_btn.setEnabled(False)
            return
        self._idx = 0
        self._show()

    def _show(self) -> None:
        card = self._cards[self._idx]
        self._revealed = False
        self._progress.setText(
            f"第 {self._idx + 1} / {len(self._cards)} 张 · "
            f"复习 {card.get('reps', 0)} 次 · 间隔 {card.get('interval_days', 0)} 天")
        self._progress.setStyleSheet(f"color: {T.INK2};")
        self._concept_lbl.setText(card.get("concept", ""))
        self._explanation.setText("🤔 先回忆一下这是什么，然后点「显示解释」")
        self._explanation.setStyleSheet(f"color: {T.INK2};")
        self._reveal_btn.setEnabled(True)
        for btn in self._grade_btns.values():
            btn.setEnabled(False)
        self._msg.setText("")

    def _reveal(self) -> None:
        if self._idx >= len(self._cards):
            return
        card = self._cards[self._idx]
        self._explanation.setText(card.get("explanation", ""))
        self._explanation.setStyleSheet(f"color: {T.INK};")
        self._revealed = True
        self._reveal_btn.setEnabled(False)
        for btn in self._grade_btns.values():
            btn.setEnabled(True)

    def _grade(self, grade: str) -> None:
        if not self._revealed or self._idx >= len(self._cards):
            return
        card = self._cards[self._idx]
        self._run(lambda: self.api.learning_review(card["id"], grade), self._grade_done)

    def _grade_done(self, result, err) -> None:
        if err:
            self._msg.setText(f"打分失败：{err}")
            self._msg.setStyleSheet(f"color: {T.ERR};")
            return
        r = result or {}
        self._msg.setText(
            f"已记录（{r.get('grade_label', '')}）· 下次复习间隔 {r.get('interval_days', 0)} 天")
        self._msg.setStyleSheet(f"color: {T.OK};")
        self._idx += 1
        if self._idx < len(self._cards):
            self._show()
        else:
            self._progress.setText(f"完成！本次复习 {len(self._cards)} 张卡片 🎉")
            self._progress.setStyleSheet(f"color: {T.INK2};")
            self._concept_lbl.setText("全部复习完毕")
            self._explanation.setText(
                "主人可以去菜单「学习回顾」提取新卡片，或点击「刷新」再来一轮。")
            self._reveal_btn.setEnabled(False)
            for btn in self._grade_btns.values():
                btn.setEnabled(False)

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
