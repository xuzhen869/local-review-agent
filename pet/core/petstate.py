"""宠物状态轮询（PySide6 版）：饥饿/心情/食物/番茄等状态的定时拉取与驱动。

对应原 main.py 的 _refresh_pet_state：
- 启动后 5 分钟自动轮询，操作后（喂食/打卡/番茄完成）即时刷新；
- api_client 是阻塞 urllib，必须放工作线程 —— 用 QThread + signal 回主线程；
- 状态到达后经 state_updated 信号分发，AppController 驱动 sprite 表情。

线程安全：PollWorker 仅在 run() 内调用 api；所有 Qt 对象只从主线程访问。
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QThread, QTimer, Signal

POLL_MS = 5 * 60 * 1000  # 自动轮询间隔


class _PollWorker(QThread):
    """单次状态拉取工作线程。结果经 result 信号回主线程。"""

    result = Signal(object, object)  # (result | None, err | None)

    def __init__(self, api, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._api = api

    def run(self) -> None:  # noqa: D102
        try:
            data = self._api.pet_state()
        except Exception as exc:  # noqa: BLE001
            self.result.emit(None, exc)
        else:
            self.result.emit(data, None)


class PetStatePoll(QObject):
    """宠物状态轮询控制器。

    state_updated(result)：成功拉取到状态 dict（含 mood/hunger/food/pomodoro…）。
    state_failed(err)：拉取失败（后端未启动等），静默容忍，不弹窗。
    """

    state_updated = Signal(dict)
    state_failed = Signal(object)

    def __init__(self, api, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._api = api
        self._worker: _PollWorker | None = None

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.refresh)

    # ------------------------------------------------------------------
    def start(self) -> None:
        """启动定时轮询，并立即拉取一次。"""
        self._timer.start()
        self.refresh()

    def stop(self) -> None:
        """停止轮询（退出时调用）。"""
        self._timer.stop()
        self._abort_worker()

    def refresh(self) -> None:
        """立即拉取一次（去重：上次未完成则跳过）。"""
        if self._worker is not None and self._worker.isRunning():
            return
        self._abort_worker()
        self._worker = _PollWorker(self._api, self)
        self._worker.result.connect(self._on_result)
        self._worker.start()

    # ------------------------------------------------------------------
    def _on_result(self, result, err) -> None:
        self._worker = None
        if err is not None:
            self.state_failed.emit(err)
            return
        if isinstance(result, dict):
            self.state_updated.emit(result)

    def _abort_worker(self) -> None:
        w = self._worker
        if w is not None:
            try:
                w.result.disconnect(self._on_result)
            except (RuntimeError, TypeError):
                pass
            if w.isRunning():
                w.requestInterruption()
                w.wait(500)
            w.deleteLater()
        self._worker = None
