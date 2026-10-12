"""结构化日志体系（§8）。

- 业务日志：logs/app.log，JSON Lines，RotatingFileHandler 轮转。
- 审计日志：logs/audit.log，独立 logger 与 handler，append-only 语义，全程脱敏。
- 所有 msg / detail / 异常信息在写入前强制脱敏（desensitize）。
- 日志行携带 event_id / run_id 锚点，支撑全链路溯源。
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from datetime import datetime, timedelta
from typing import Any

from .config import LOGS_DIR
from .security.desensitize import desensitize, desensitize_object

_EXTRA_KEYS = ("event_id", "run_id", "adapter", "action")


class JsonLinesFormatter(logging.Formatter):
    """业务日志：一行一条 JSON，字段含 ts/level/logger/msg + 溯源锚点。"""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
        }
        for key in _EXTRA_KEYS:
            value = getattr(record, key, None)
            if value is not None:
                entry[key] = value
        if record.exc_info and record.exc_info[0]:
            entry["error"] = desensitize(self.formatException(record.exc_info))
        detail = getattr(record, "detail", None)
        if detail is not None:
            entry["detail"] = desensitize_object(detail)
        entry["msg"] = desensitize(record.getMessage())
        return json.dumps(entry, ensure_ascii=False, default=str)


class AuditFormatter(logging.Formatter):
    """审计日志：固定结构，字段含 actor/action/object/result。"""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": "audit",
            "actor": getattr(record, "actor", "system"),
            "action": getattr(record, "action", "unknown"),
            "object_type": getattr(record, "object_type", ""),
            "object_id": getattr(record, "object_id", ""),
            "result": getattr(record, "result", ""),
        }
        detail = getattr(record, "detail", None)
        if detail is not None:
            entry["detail"] = desensitize_object(detail)
        return json.dumps(entry, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    """控制台简洁格式：便于 python run.py 直接观察。"""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).astimezone().strftime("%H:%M:%S")
        anchor = ""
        for key in ("event_id", "run_id"):
            value = getattr(record, key, None)
            if value:
                anchor += f" {key}={value[:8]}"
        exc = ""
        if record.exc_info and record.exc_info[0]:
            exc = " | " + desensitize(self.formatException(record.exc_info)).replace("\n", " ")
        return f"{ts} {record.levelname:<5} {record.name}{anchor} {desensitize(record.getMessage())}{exc}"


def _merge_extra(extra: dict[str, Any] | None, detail: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    """合并 detail 到 extra：detail 非空时并入 extra["detail"]，其余 kwargs 原样透传。"""
    if detail is not None:
        extra = dict(extra or {})
        extra["detail"] = detail
    if extra is not None:
        kwargs["extra"] = extra
    return kwargs


class _AppLogger(logging.Logger):
    """业务 logger：原生支持 `detail=` 关键字（自动并入 extra，避免 TypeError）。"""

    def debug(self, msg: object, *args: Any, detail: Any = None,
              extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().debug(msg, *args, **self._merge_extra(extra, detail, kwargs))

    def info(self, msg: object, *args: Any, detail: Any = None,
             extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().info(msg, *args, **self._merge_extra(extra, detail, kwargs))

    def warning(self, msg: object, *args: Any, detail: Any = None,
                extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().warning(msg, *args, **self._merge_extra(extra, detail, kwargs))

    def error(self, msg: object, *args: Any, detail: Any = None,
              extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().error(msg, *args, **self._merge_extra(extra, detail, kwargs))

    def critical(self, msg: object, *args: Any, detail: Any = None,
                 extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().critical(msg, *args, **self._merge_extra(extra, detail, kwargs))

    def _merge_extra(self, extra: dict[str, Any] | None, detail: Any,
                     kwargs: dict[str, Any]) -> dict[str, Any]:
        return _merge_extra(extra, detail, kwargs)


class _AuditLogger(_AppLogger):
    """审计 logger：带 audit() 便捷方法。"""

    def audit(
        self,
        actor: str,
        action: str,
        object_type: str = "",
        object_id: str = "",
        result: str = "ok",
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.info(
            "",
            extra={
                "actor": actor,
                "action": action,
                "object_type": object_type,
                "object_id": object_id,
                "result": result,
                "detail": detail,
            },
        )


logging.setLoggerClass(_AuditLogger)


def get_logger(name: str) -> _AppLogger:
    """获取业务 logger（统一 app. 前缀）。"""
    return logging.getLogger(f"app.{name}")  # type: ignore[return-value]


def get_audit_logger() -> _AuditLogger:
    """获取审计 logger（loggerClass 已切换为 _AuditLogger）。"""
    return logging.getLogger("audit")  # type: ignore[return-value]


def _prune_old_logs(dir_path: os.PathLike[str], retention_days: int) -> None:
    """清理超过保留天数的轮转日志文件。"""
    cutoff = datetime.now().astimezone() - timedelta(days=retention_days)
    for name in os.listdir(dir_path):
        if not name.endswith(".log"):
            continue
        path = os.path.join(dir_path, name)
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(path)).astimezone()
            if mtime < cutoff:
                os.remove(path)
        except OSError:
            continue


def setup_logging(level: str = "INFO", max_bytes: int = 10 * 1024 * 1024,
                  backup_count: int = 10, retention_days: int = 30) -> None:
    """初始化业务日志 + 审计日志。启动时调用一次。"""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    _prune_old_logs(LOGS_DIR, retention_days)

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(numeric_level)

    # ---- 业务日志（文件，JSON Lines，轮转）----
    file_handler = logging.handlers.RotatingFileHandler(
        LOGS_DIR / "app.log", maxBytes=max_bytes, backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(JsonLinesFormatter())
    file_handler.setLevel(logging.DEBUG)
    root.addHandler(file_handler)

    # ---- 控制台（人类可读，便于 run.py 观察）----
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(ConsoleFormatter())
    console_handler.setLevel(logging.DEBUG)
    root.addHandler(console_handler)

    # ---- 审计日志（独立 logger + handler，propagate=False 不进业务日志）----
    audit = get_audit_logger()
    audit.setLevel(logging.INFO)
    audit.propagate = False
    audit_file = logging.handlers.RotatingFileHandler(
        LOGS_DIR / "audit.log", maxBytes=max_bytes, backupCount=backup_count,
        encoding="utf-8",
    )
    audit_file.setFormatter(AuditFormatter())
    audit.addHandler(audit_file)

    get_logger("logging_setup").info(
        "结构化日志已初始化", detail={"app_log": str(LOGS_DIR / "app.log"),
                                   "audit_log": str(LOGS_DIR / "audit.log")}
    )
