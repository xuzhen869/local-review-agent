"""core 包：宠物本体层（sprite 柴犬精灵 / speech 台词气泡 / petstate 状态轮询）。"""
from .sprite import Sprite
from .speech import SpeechBubble, random_line, LINES
from .petstate import PetStatePoll

__all__ = ["Sprite", "SpeechBubble", "random_line", "LINES", "PetStatePoll"]
