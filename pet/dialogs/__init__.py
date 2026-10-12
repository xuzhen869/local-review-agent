"""dialogs 包：弹窗面板（msgbox / key / todos / review / chat / pomodoro）。"""
from . import msgbox
from .msgbox import info, warn, error, confirm
from .key import KeyDialog, KnowledgeDialog, SummaryDialog
from .todos import TodoDialog
from .review import ReviewDialog
from .chat import ChatDialog
from .pomodoro import PomodoroDialog, TimerState

__all__ = [
    "msgbox", "info", "warn", "error", "confirm",
    "KeyDialog", "KnowledgeDialog", "SummaryDialog",
    "TodoDialog", "ReviewDialog", "ChatDialog",
    "PomodoroDialog", "TimerState",
]
