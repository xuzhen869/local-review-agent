#!/usr/bin/env python
"""回顾 Agent —— 桌面软件入口（支持开发模式与 PyInstaller 打包两种运行形态）。

双击运行（或打包后的 exe）即启动：
  1. 检测本地后端（127.0.0.1:8100）；未运行则拉起子进程；
  2. 等待后端就绪；
  3. 打开主窗口（Dashboard）与托盘柴犬（可开关）；
  4. 退出时统一终止后端子进程，无残留。

进程模型：
  - 开发模式：子进程 = venv python 跑 run.py；
  - 打包模式：PyInstaller onedir exe 内部没有独立解释器，
    子进程 = 当前 exe 追加 --run-backend（同一打包运行时跑 uvicorn）。

用法:
    python launch.py              # 桌面软件模式（默认真实数据目录 ~/.local_review_agent）
    python launch.py --test       # 测试数据目录（lra_test，开发/演示用）
    <exe> --run-backend           # 打包后子进程内部用，勿直接调用
"""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
BACKEND_DIR = PROJECT_ROOT / "backend"
LOGS_DIR = PROJECT_ROOT / "logs"
PYTHON = str(BACKEND_DIR / ".venv" / "Scripts" / "python.exe")
RUN_PY = str(PROJECT_ROOT / "run.py")
HEALTH_URL = "http://127.0.0.1:8100/api/health"
START_TIMEOUT = 30.0
# 测试环境数据目录（与 PROJECT_STATUS 测试环境一致）
TEST_HOME = "C:/Users/xu/AppData/Local/Temp/lra_test/home"

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_FROZEN = bool(getattr(sys, "frozen", False))


def _log(message: str) -> None:
    """安全打印：windowed/pyw 下 stdout 可能为 None 或非 UTF-8，一律容错。"""
    try:
        print(message)
    except Exception:  # noqa: BLE001
        pass


def _backend_dir() -> Path:
    """后端包 app 所在目录：打包后模块解包在 _MEIPASS 下。"""
    if _FROZEN:
        return Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
    return BACKEND_DIR


def run_backend_service() -> int:
    """打包后子进程入口：仅启动后端服务（uvicorn）。开发模式不用此分支。"""
    backend_dir = _backend_dir()
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    import uvicorn

    from app.config import ConfigManager
    from app.main import app

    cfg = ConfigManager().load()
    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, log_level="warning")
    return 0


class BackendManager:
    """本地后端进程管理：检测 / 无窗口拉起 / 等待就绪 / 退出终止。

    token 由 launch 生成并注入环境变量 REVIEW_AGENT_TOKEN，
    探测与子进程均携带该 token（后端所有 /api/* 要求 Bearer 认证）。
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self.token: str = os.environ.get("REVIEW_AGENT_TOKEN", "")

    # 探测：True=就绪且认证通过；"unauthorized"=旧实例 token 不匹配；False=未运行
    def _probe(self, timeout: float = 1.5):
        req = urllib.request.Request(
            HEALTH_URL, headers={"Authorization": f"Bearer {self.token}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
        except urllib.error.HTTPError as exc:
            return "unauthorized" if exc.code == 401 else False
        except Exception:  # noqa: BLE001
            return False

    def is_up(self) -> bool:
        return self._probe() is True

    def ensure_running(self, test_home: bool = False) -> bool | str:
        """确保后端就绪。返回 True / False / "conflict"（旧实例 token 不匹配）。"""
        probe = self._probe()
        if probe is True:
            return True
        if probe == "unauthorized":
            return "conflict"
        env = dict(os.environ)
        if test_home:
            env["LOCAL_REVIEW_AGENT_HOME"] = TEST_HOME
        if _FROZEN:
            cmd = [sys.executable, "--run-backend"]
        else:
            cmd = [PYTHON, RUN_PY]
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOGS_DIR / "backend_stdout.log", "ab") as out, \
                open(LOGS_DIR / "backend_stderr.log", "ab") as err:
            self._proc = subprocess.Popen(
                cmd, cwd=str(PROJECT_ROOT), env=env,
                stdout=out, stderr=err, creationflags=_NO_WINDOW,
            )
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            if self._probe() is True:
                return True
            if self._proc.poll() is not None:
                tail = self._read_log_tail(LOGS_DIR / "backend_stderr.log")
                _log(f"[launch] 后端启动失败（退出码 {self._proc.returncode}）\n{tail}")
                return False
            time.sleep(0.5)
        _log("[launch] 后端启动超时")
        return False

    @staticmethod
    def _read_log_tail(path: Path, max_bytes: int = 2000) -> str:
        try:
            return path.read_bytes()[-max_bytes:].decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return ""

    def stop(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=4)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass


_SINGLE_INSTANCE_LOCK = None


def _acquire_single_instance() -> bool:
    """单实例锁：Windows 文件锁（msvcrt），进程退出自动释放。

    防止多个实例同时显示宠物窗口造成重叠（本机曾出现后台命令被重复
    执行、或手动多次启动的情况）。返回 False 表示已有实例在运行。
    """
    global _SINGLE_INSTANCE_LOCK
    try:
        import msvcrt
    except ImportError:
        return True   # 非 Windows 不强制单实例
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    lock_file = open(LOGS_DIR / "app.lock", "a+")
    try:
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock_file.close()
        return False
    # 保持引用，防止文件对象被 GC 关闭导致锁提前释放
    _SINGLE_INSTANCE_LOCK = lock_file
    return True


def main() -> int:
    # windowed/GBK 控制台下 stdout 可能为 None 或非 UTF-8，强制容错
    try:
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass

    # 打包后子进程分支：仅起后端，不起 GUI（token 由父进程经 env 注入，此处不再生成）
    if "--run-backend" in sys.argv:
        return run_backend_service()

    if not _acquire_single_instance():
        _log("[launch] 检测到已运行的回顾 Agent 实例，本次启动自动退出")
        return 0

    # 生成 / 复用本地 API 认证 token：注入环境变量，后端子进程与 GUI 同享
    if not os.environ.get("REVIEW_AGENT_TOKEN"):
        os.environ["REVIEW_AGENT_TOKEN"] = secrets.token_hex(32)

    test_home = "--test" in sys.argv
    manager = BackendManager()
    status = manager.ensure_running(test_home)
    if status == "conflict":
        return _fatal("检测到已运行的回顾 Agent 后端（认证不匹配）。\n"
                      "请先退出已运行的实例，再重新启动。")
    if status is not True:
        return _fatal("本地服务启动失败，请查看 logs/backend_stderr.log")

    sys.path.insert(0, str(PROJECT_ROOT))
    sys.path.insert(0, str(BACKEND_DIR))

    return _run_gui(manager)


def _fatal(message: str) -> int:
    """Qt 错误弹窗后退出（不进入 GUI 主循环）。"""
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication(sys.argv)
    box = QMessageBox(app.activeWindow())
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle("回顾 Agent")
    box.setText(message)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()
    return 1


def _run_gui(manager: BackendManager) -> int:
    """Qt 主循环：全局 QSS + AppController 装配。"""
    from PySide6.QtWidgets import QApplication

    from pet import app as petapp
    from pet.widgets import theme as T

    T.set_theme("space")
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName(T.APP_NAME)
    qt_app.setApplicationVersion(T.APP_VERSION)
    qt_app.setStyleSheet(T.build_qss())
    T.ensure_fonts()   # QApplication 就绪后注册 Anton/Condiment 私有字体

    controller = petapp.AppController(backend_stopper=manager.stop)
    try:
        return qt_app.exec()
    finally:
        manager.stop()


if __name__ == "__main__":
    sys.exit(main())
