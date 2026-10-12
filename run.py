#!/usr/bin/env python
"""本地 Agent 一键启动入口。

用法:
    python run.py             # 启动服务（默认 127.0.0.1:8100）
    python run.py --deploy    # 构建报告并部署 GitHub Pages（M4 阶段）

首次启动会自动创建 ~/.local_review_agent/config.yaml（含数据源填写说明）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import ConfigManager  # noqa: E402


def main() -> int:
    if "--deploy" in sys.argv:
        print("[deploy] 报告部署功能将在 M4 里程碑实现。")
        return 0

    try:
        cfg = ConfigManager().load()
    except Exception as exc:
        print(f"[FATAL] 配置加载失败: {exc}", file=sys.stderr)
        return 1

    import uvicorn
    from app.main import app

    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
