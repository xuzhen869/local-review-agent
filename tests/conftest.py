# -*- coding: utf-8 -*-
"""pytest 全局配置：打通 import 路径（项目根含 shared、backend 含 app）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / 'backend'):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
