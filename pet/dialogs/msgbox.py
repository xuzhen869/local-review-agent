"""统一消息框（info / warn / error / confirm）—— PySide6 版。

对应原 tkinter dialogs.py 的 info / warn / error（tkinter.messagebox 封装）。
Qt 版用 QMessageBox，全中文按钮、暗色 QSS、父窗口居中、模态。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMessageBox, QWidget

from ..widgets import theme as T


def _box(parent: QWidget | None, icon: QMessageBox.Icon, title: str, message: str,
         buttons: QMessageBox.StandardButtons) -> QMessageBox:
    """构造并返回一个已样式化的 QMessageBox（调用方负责 exec）。"""
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(title)
    box.setInformativeText(message)
    box.setStandardButtons(buttons)
    box.setWindowModality(Qt.WindowModality.WindowModal)
    if parent is not None:
        # 父窗口居中
        pw = parent.window()
        box.show()
        geo = box.frameGeometry()
        pg = pw.frameGeometry()
        geo.moveCenter(pg.center())
        box.move(geo.topLeft())
        box.hide()
    _apply_style(box)
    _localize(box, buttons)
    return box


def _localize(box: QMessageBox, buttons: QMessageBox.StandardButtons) -> None:
    """Qt 默认按钮为英文，逐个替换为中文文本。"""
    mapping = {
        QMessageBox.StandardButton.Ok: "确定",
        QMessageBox.StandardButton.Cancel: "取消",
        QMessageBox.StandardButton.Yes: "是",
        QMessageBox.StandardButton.No: "否",
        QMessageBox.StandardButton.Close: "关闭",
        QMessageBox.StandardButton.Save: "保存",
        QMessageBox.StandardButton.Discard: "放弃",
    }
    btns = box.buttons()
    for btn in btns:
        role = box.standardButton(btn)
        zh = mapping.get(role)
        if zh:
            btn.setText(zh)


def _apply_style(box: QMessageBox) -> None:
    box.setStyleSheet(
        f"QMessageBox {{ background-color: {T.BG}; }}"
        f"QMessageBox QLabel {{ color: {T.INK}; font-family: \"{T.F_SANS}\";"
        f"  font-size: 10pt; background: transparent; }}"
        f"QMessageBox QPushButton {{ background-color: {T.CARD}; color: {T.INK};"
        f"  border: 1px solid {T.BORDER}; border-radius: 6px;"
        f"  padding: 5px 18px; font-family: \"{T.F_SANS}\"; font-size: 10pt; }}"
        f"QMessageBox QPushButton:hover {{ background-color: {T.CARD_HOVER};"
        f"  color: {T.INK}; }}"
        f"QMessageBox QPushButton:default {{ background-color: {T.ACCENT};"
        f"  color: {T.ON_ACCENT}; border-color: {T.ACCENT}; }}"
    )


def info(parent: QWidget | None, title: str, message: str) -> None:
    """信息框：单个「确定」按钮。"""
    box = _box(parent, QMessageBox.Icon.Information, title, message,
               QMessageBox.StandardButton.Ok)
    box.exec()


def warn(parent: QWidget | None, title: str, message: str) -> None:
    """警告框：单个「确定」按钮。"""
    box = _box(parent, QMessageBox.Icon.Warning, title, message,
               QMessageBox.StandardButton.Ok)
    box.exec()


def error(parent: QWidget | None, title: str, message: str) -> None:
    """错误框：单个「确定」按钮。"""
    box = _box(parent, QMessageBox.Icon.Critical, title, message,
               QMessageBox.StandardButton.Ok)
    box.exec()


def confirm(parent: QWidget | None, title: str, message: str,
            default_yes: bool = True) -> bool:
    """确认框：是 / 否。返回 True=是。default_yes=False 时默认焦点在「否」。"""
    buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    box = _box(parent, QMessageBox.Icon.Question, title, message, buttons)
    if not default_yes:
        no_btn = box.button(QMessageBox.StandardButton.No)
        if no_btn:
            no_btn.setFocus()
    return box.exec() == QMessageBox.StandardButton.Yes
