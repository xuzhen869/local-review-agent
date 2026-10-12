"""panels 包：主界面面板（dashboard 七页 + media 视频组件）。"""
from .media import VideoSurface, video_path
from .dashboard import Dashboard

__all__ = ["VideoSurface", "video_path", "Dashboard"]
