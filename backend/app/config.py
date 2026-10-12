"""配置层：加载/保存 config.yaml，数据源采用用户填写式结构。

约定：
- 数据目录默认 `~/.local_review_agent/`（环境变量 LOCAL_REVIEW_AGENT_HOME 可覆盖），
  存放 config.yaml、data.db、secrets/、recovery 备份。
- 日志目录固定为项目根 `logs/`（面试可展示性）。
- 敏感字段（邮箱密码 / API Key）一律入 secrets 加密表，**禁止**写入 config.yaml。
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

from shared.contracts import (ClipboardSources, FsSources, GitSources,
                              IcsSources, ImapSources, Sources)

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------
ENV_AGENT_HOME = "LOCAL_REVIEW_AGENT_HOME"

PROJECT_ROOT = Path(__file__).resolve().parents[2]          # local-review-agent/
LOGS_DIR = PROJECT_ROOT / "logs"
DATA_DIR = Path(os.environ.get(ENV_AGENT_HOME, Path.home() / ".local_review_agent"))
SECRETS_DIR = DATA_DIR / "secrets"


# ---------------------------------------------------------------------------
# 配置模型（数据源 = 用户填写式；Sources 系列模型统一在 shared/contracts.py，
# 唯一事实源，避免配置模型与 API 契约双份漂移）
# ---------------------------------------------------------------------------
class DbConfig(BaseModel):
    """数据库连接参数（相对数据目录）。"""
    path: str = "data.db"
    busy_timeout_ms: int = Field(default=5000, ge=0)


class SecurityConfig(BaseModel):
    """安全参数。

    lock_memory: 是否开启 SQLCipher `cipher_memory_security`（mlock 锁内存防换页）。
                 注意：该操作要求 Windows `SeLockMemoryPrivilege`（管理员），
                 普通权限下 SQLCipher 原生层会崩溃（VirtualLock 失败），
                 默认必须为 False；置 True 时程序会先检测权限，无权限则降级。
    """
    lock_memory: bool = False
    recovery_iterations: int = Field(default=10, ge=1, le=32)   # Argon2id 迭代数（Keyring 层有 10 次底线）
    recovery_memory_mib: int = Field(default=64, ge=16, le=1024)  # Argon2id 内存(MiB)


class LoggingConfig(BaseModel):
    level: str = Field(default="INFO")
    max_bytes: int = Field(default=10 * 1024 * 1024, ge=1_000_000)
    backup_count: int = Field(default=10, ge=1)
    retention_days: int = Field(default=30, ge=1)


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"       # 只绑本地回环，不外网
    port: int = Field(default=8100, ge=1, le=65535)


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    db: DbConfig = Field(default_factory=DbConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    sources: Sources = Field(default_factory=Sources)

    @property
    def db_path(self) -> Path:
        return DATA_DIR / self.db.path

    @property
    def has_any_source(self) -> bool:
        return bool(self.sources.git.repos or self.sources.fs.dirs)


# ---------------------------------------------------------------------------
# 首次启动写入的模板（含中文填写说明）
# ---------------------------------------------------------------------------
DEFAULT_CONFIG_TEMPLATE = """# =============================================================
# 个人工作/学习回顾 Agent —— 配置文件
# 路径：~/.local_review_agent/config.yaml
# 说明：数据源全部在此处（或仪表盘"配置向导"页面）填写，保存后热重载。
#       敏感信息（邮箱密码、API Key）一律入 secrets 加密库，禁止写在这里。
# =============================================================

server:
  host: 127.0.0.1
  port: 8100

# ---- 数据库 ----
db:
  path: data.db
  busy_timeout_ms: 5000

# ---- 安全 ----
security:
  # 内存锁（mlock）。普通权限下开启会导致 SQLCipher 崩溃，保持 false
  lock_memory: false
  recovery_iterations: 10
  recovery_memory_mib: 64

logging:
  level: INFO
  max_bytes: 10485760
  backup_count: 10
  retention_days: 30

# =============================================================
# 数据源（用户填写式）
# =============================================================
sources:
  git:
    repos: []
      # - C:/Users/xu/Documents/repos/my-project
  fs:
    dirs: []
      # - C:/Users/xu/Documents/work
    debounce_seconds: 5
    poll_interval_seconds: 300
    max_file_bytes: 5242880
    ignore_exts: [".tmp", ".swp", ".lock", ".partial"]
    ignore_dirs: [".git", ".venv", "node_modules", "__pycache__", "venv", "dist", "build"]
  ics:
    files: []
    urls: []
  imap:
    enabled: false
    server: ""
    port: 993
    username: ""
    ssl: true
    poll_interval_seconds: 300
  clipboard:
    enabled: false
    poll_interval_seconds: 2
    daily_limit: 200
"""


class ConfigError(RuntimeError):
    """配置加载 / 校验失败。"""


class ConfigManager:
    """加载 / 保存 / 重载 config.yaml。线程安全（以锁兜底）。"""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (DATA_DIR / "config.yaml")
        self._lock = threading.Lock()
        self._config: AppConfig | None = None

    @property
    def path(self) -> Path:
        return self._path

    def ensure_default(self) -> None:
        """首次启动：创建数据目录与默认配置文件（含填写说明）。"""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")

    def load(self) -> AppConfig:
        with self._lock:
            self.ensure_default()
            raw: Any = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
            try:
                self._config = AppConfig.model_validate(raw)
            except ValidationError as exc:
                raise ConfigError(f"config.yaml 校验失败: {exc}") from exc
            return self._config

    def save(self, cfg: AppConfig) -> None:
        """将当前配置写回 config.yaml（数据源向导保存时调用）。"""
        with self._lock:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            dumped = yaml.safe_dump(
                cfg.model_dump(mode="json"), allow_unicode=True, sort_keys=False
            )
            self._path.write_text(dumped, encoding="utf-8")
            self._config = cfg
