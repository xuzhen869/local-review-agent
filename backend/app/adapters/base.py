"""适配器统一契约（§4.0）。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class AdapterState(str, Enum):
    IDLE = "IDLE"
    CONNECTING = "CONNECTING"
    RUNNING = "RUNNING"
    ERROR = "ERROR"
    DISABLED = "DISABLED"
    PAUSED = "PAUSED"


@dataclass
class RawEvent:
    """适配器产物（未入库、未脱敏）。

    debounce_key: 非 None 时该事件进入防抖合并（如 fs 按路径 5s 合并）；
    immediate: True 跳过防抖立即投递（如文件删除，避免漏记）；
    critical: True 时队列满阻塞投递不丢弃（git/fs 视为关键源）。
    """
    source: str
    source_ref: str
    ts: datetime
    content: str
    meta: dict[str, Any] = field(default_factory=dict)
    debounce_key: str | None = None
    immediate: bool = False
    critical: bool = False


class AdapterError(RuntimeError):
    """适配器级错误（连接/同步失败）。"""


class BaseAdapter(ABC):
    """所有数据源适配器的基类。

    健壮性约束（§4.0）：
    - 异常永不冒泡出 run/主循环；捕获后置 state=ERROR + last_error，继续运行；
    - connect 失败由外层（调度器）按指数退避重试，多次失败置 DISABLED。
    """

    name: str = "base"

    def __init__(self) -> None:
        self.state: AdapterState = AdapterState.IDLE
        self.last_error: str | None = None
        self.enabled: bool = True

    @abstractmethod
    async def connect(self) -> None:
        """校验数据源可达性；失败抛 AdapterError（外层退避重试）。"""

    @abstractmethod
    async def sync(self, since: datetime) -> list[RawEvent]:
        """一次性增量拉取，返回 RawEvent 列表（游标持久化由适配器自行维护）。"""

    @abstractmethod
    async def run(self) -> None:
        """启动持续监听（watch/轮询）。异常捕获后置 ERROR 并降级重试。"""

    @abstractmethod
    async def stop(self) -> None:
        """停止监听，释放资源。"""

    def mark_error(self, exc: BaseException) -> None:
        self.state = AdapterState.ERROR
        self.last_error = f"{type(exc).__name__}: {exc}"

    def mark_disabled(self, reason: str) -> None:
        self.state = AdapterState.DISABLED
        self.last_error = reason
        self.enabled = False
