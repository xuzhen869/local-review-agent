# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules

datas = []
hiddenimports = []
datas += collect_data_files('rapidocr_onnxruntime')
hiddenimports += collect_submodules('rapidocr_onnxruntime')

# ---- BGE 语义检索模型（backend/app/agent/bge/）----------------------------
# 运行时模块归档路径为 _MEIPASS/app/agent/...（pathex=['backend','pet']，无 backend
# 前缀——见 launch.py _backend_dir 以 _MEIPASS 为 sys.path 即可 import app）。
# embeddings.py 按 Path(__file__).parent/'bge' 解析，故数据目标必须与之一致。
# 模型未就绪时语义检索自动降级纯关键词。
for _f in ('config.json', 'model.onnx', 'tokenizer.json'):
    datas += [(os.path.join('backend', 'app', 'agent', 'bge', _f),
               'app/agent/bge')]
hiddenimports += ['onnxruntime', 'tokenizers', 'numpy']

# ---- PySide6 运行时插件（平台 / 图像格式 / QtMultimedia ffmpeg 解码后端） ----
# PyInstaller 对 PySide6 有内置 hook（hooks-contrib），此处显式收集 Qt/plugins
# 兜底：QtMultimedia 的 ffmpeg 解码后端插件缺失时 QMediaPlayer 将无法播放视频。
try:
    datas += collect_data_files('PySide6', includes=['**/plugins/**'])
    hiddenimports += collect_submodules('PySide6.QtMultimedia')
    hiddenimports += collect_submodules('PySide6.QtMultimediaWidgets')
except Exception:  # noqa: BLE001
    pass

# ---- Orbis 资产：字体 / 视频 / 星云背景 / 图标 --------------------------
# 逐目录遍历，未来往 assets/videos 放 mp4 后重打包自动包含（排除 screenshots）。
_ASSET_FILES = [("assets/background.png", "assets"),
                ("assets/icon.ico", "assets")]
for _sub in ("fonts", "videos"):
    _d = os.path.join("assets", _sub)
    if os.path.isdir(_d):
        _ASSET_FILES += [(os.path.join(_d, f), _d)
                         for f in os.listdir(_d)]
datas += _ASSET_FILES

# ---- 宠物资源：鸭子动画帧 + 动作表（pet/resources/）----------------------
# sprite.py 按 __file__.parents[1]/resources/duck 定位，打包后模块归档路径为
# _MEIPASS/pet/...，故数据目标须落在 _MEIPASS/pet/resources/...（无多余前缀）。
for _root, _dirs, _files in os.walk("pet/resources"):
    _rel = os.path.relpath(_root, "pet")          # resources/duck
    for _f in _files:
        datas += [(os.path.join(_root, _f), os.path.join("pet", _rel))]


a = Analysis(
    ['launch.py'],
    pathex=['backend', 'pet', 'shared'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ReviewAgent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ReviewAgent',
)
