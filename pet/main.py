"""桌面宠物入口（PySide6 版）：可爱柴犬 + 截图 / 知识点 / 今日周报 / 喂养 / 番茄 / 回顾。

用法：python -m pet.main    （需先启动后端 python run.py）

真实统一入口是项目根 launch.py（自动拉起后端进程）；本模块供独立调试用。
对应原 tkinter main.py；QApplication + 全局 QSS + AppController 装配。
"""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from . import app as petapp
from .widgets import theme as T


def main() -> int:
    T.set_theme("space")
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName(T.APP_NAME)
    qt_app.setApplicationVersion(T.APP_VERSION)

    # 全局 QSS（深空太空主题）
    qt_app.setStyleSheet(T.build_qss())

    T.ensure_fonts()   # QApplication 就绪后注册 Anton/Condiment 私有字体

    # backend_stopper 由 launch.py 注入；独立调试时为 None（不管理后端进程）
    controller = petapp.AppController(backend_stopper=None)
    # controller 是 main() 局部变量，exec() 阻塞期间不会被 GC，安全持有
    return qt_app.exec()


if __name__ == "__main__":
    sys.exit(main())
