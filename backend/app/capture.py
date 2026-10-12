"""截图捕获：保存 PNG + RapidOCR 文字识别 + 提纯判断 + 入库（source=screenshot）。

- OCR 引擎惰性单例初始化；OCR 为同步阻塞，调用方须放线程池（asyncio.to_thread）；
- 图片校验 magic bytes（PNG/JPEG），拒绝其他类型；
- 提纯：OCR 文本交由判断 Agent（LLM）一次调用，剔除界面按钮/控件噪声并判断清晰度；
  纯界面噪声（无知识）不入库（PNG 仍保存）；无 LLM Key / 异常降级保留原文（不丢数据）；
- OCR 失败不阻断入库（content 记为占位说明，可后续重识别）。
"""
from __future__ import annotations

import asyncio
import io
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from .config import DATA_DIR
from .db.store import EventRecord, store_event
from .logging_setup import get_logger

log = get_logger("capture")

_ocr_engine: Any = None
_ocr_lock = threading.Lock()
_ocr_retry_at = 0.0           # 引擎不可用时，距下一次重试的时间点（秒）
_OCR_RETRY_INTERVAL = 60      # 装好依赖后自动重试间隔


def ocr_available() -> bool:
    """RapidOCR 引擎是否已安装可用。"""
    try:
        from rapidocr_onnxruntime import RapidOCR  # noqa: F401
        return True
    except ImportError:
        return False


def _get_ocr_engine() -> Any:
    """惰性初始化引擎；曾不可用（False）时按间隔自动重试，装好后无需重启即生效。"""
    global _ocr_engine, _ocr_retry_at
    if _ocr_engine is None or (_ocr_engine is False and time.monotonic() >= _ocr_retry_at):
        with _ocr_lock:
            if _ocr_engine is None or (_ocr_engine is False and time.monotonic() >= _ocr_retry_at):
                try:
                    from rapidocr_onnxruntime import RapidOCR
                    _ocr_engine = RapidOCR()
                    log.info("OCR 引擎已就绪")
                except ImportError:
                    log.warning("RapidOCR 未安装，截图仅保存不识别文字（将自动重试）")
                    _ocr_engine = False
                    _ocr_retry_at = time.monotonic() + _OCR_RETRY_INTERVAL
    return _ocr_engine or None


def ocr_image(path: Path) -> str:
    """同步 OCR：返回识别文本（每行一条）。失败/引擎缺失返回空串并记日志。"""
    engine = _get_ocr_engine()
    if engine is None:
        return ""
    try:
        result, _ = engine(str(path))
        if not result:
            return ""
        lines = [item[1] for item in result if len(item) > 1]
        return "\n".join(lines)
    except Exception as exc:
        log.error("OCR 识别失败", detail={"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
        return ""


def _screenshots_dir(ts: datetime) -> Path:
    path = DATA_DIR / "screenshots" / ts.strftime("%Y-%m-%d")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _is_image(data: bytes) -> bool:
    return data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff")


def _verify_image(data: bytes) -> None:
    """解码校验：拒绝伪图片（改魔数的非图片 / 损坏文件），失败抛 ValueError。"""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
    except Exception as exc:
        raise ValueError(f"图片无法解码: {exc}") from exc


async def save_screenshot_and_ocr(image_bytes: bytes, db: Any, llm: Any = None) -> dict[str, Any]:
    """保存截图 + OCR + 提纯判断 + 入库。返回 {id, ocr_text, path, path_rel, inserted, gated}。

    - image_bytes 非 PNG/JPEG / 不可解码抛 ValueError；
    - path_rel 为相对 DATA_DIR 路径（绝对路径不出库、不进审计日志）；
    - 提纯判断：OCR 文本交由判断 Agent（LLM）剔除界面按钮/控件噪声并判断清晰度；
      纯界面噪声 → PNG 仍保存、事件**不入库**（返回 inserted=False + message）；
    - 门控：提纯后文本被判 needs_review → 截图保留、事件**不入库**，
      返回 pending_id 由前端弹确认，确认后经 /api/pending/decide 落库；
    - llm 传 None / 无 key / 提纯判断异常 → 降级保留原文直接入库（不丢数据）。
    """
    if not image_bytes:
        raise ValueError("空图片数据")
    if not _is_image(image_bytes):
        raise ValueError("仅支持 PNG/JPEG 图片")
    _verify_image(image_bytes)

    ts = datetime.now().astimezone()
    filename = f"{ts.strftime('%H%M%S')}_{uuid.uuid4().hex[:8]}.png"
    out_path = _screenshots_dir(ts) / filename
    out_path.write_bytes(image_bytes)

    ocr_text = await asyncio.to_thread(ocr_image, out_path)
    path_rel = str(out_path.relative_to(DATA_DIR))
    if not ocr_text.strip():
        log.info("截图未识别到文字，不入库", detail={"path_rel": path_rel})
        return {
            "id": "", "ocr_text": ocr_text, "purified_text": "", "path": str(out_path),
            "path_rel": path_rel, "inserted": False, "ocr_available": ocr_available(),
            "gated": False, "verdict": "skip", "message": "截图已保存，未识别到文字",
        }

    # 提纯判断 Agent：剔除界面按钮/控件噪声 + 清晰度判断（一次 LLM 调用）
    from .agent.router import route_screenshot_text
    gate = await route_screenshot_text(db, llm, ocr_text)
    if gate["verdict"] == "skip":
        log.info("截图纯界面噪声，不入库", detail={"path_rel": path_rel})
        return {
            "id": "", "ocr_text": ocr_text, "purified_text": "", "path": str(out_path),
            "path_rel": path_rel, "inserted": False, "ocr_available": ocr_available(),
            "gated": gate.get("gated", False), "verdict": "skip",
            "message": "截图已保存，仅识别到界面按钮文字",
        }
    if gate["verdict"] == "needs_review":
        log.info("截图 OCR 文本被门控打回", detail={"pending_id": gate.get("pending_id"),
                                               "concern": (gate.get("concern") or "")[:120]})
        return {
            "id": "", "ocr_text": ocr_text, "purified_text": gate.get("kept_text") or "",
            "path": str(out_path), "path_rel": path_rel,
            "inserted": False, "ocr_available": ocr_available(),
            "gated": True, "verdict": "needs_review", "pending_id": gate.get("pending_id"),
            "concern": gate.get("concern"),
        }

    # clear：提纯后的知识文本入库（kept_text 为空时回退原文，兜底不丢）
    content = (gate.get("kept_text") or ocr_text.strip()) or "（截图，未识别到有效文字）"
    rec = EventRecord(
        source="screenshot",
        source_ref=path_rel,          # 相对路径入库：避免绝对路径（含用户名）出库
        ts=ts.isoformat(timespec="seconds"),
        content=content,
        keywords_json='{"path": "' + path_rel.replace("\\", "/") + '"}',
    )
    inserted, event_id = await store_event(db, rec)
    log.info("截图已捕获并入库", extra={"event_id": event_id, "adapter": "screenshot", "action": "store"},
             detail={"path_rel": path_rel, "ocr_len": len(ocr_text), "content_len": len(content),
                     "inserted": inserted, "gated": gate.get("gated", False),
                     "degraded": gate.get("degraded", False)})
    return {
        "id": event_id, "ocr_text": ocr_text, "purified_text": content,
        "path": str(out_path), "path_rel": path_rel,
        "inserted": inserted, "ocr_available": ocr_available(),
        "gated": gate.get("gated", False), "verdict": "clear",
        "degraded": gate.get("degraded", False),
    }


async def save_shot_only(image_bytes: bytes) -> tuple[str, str]:
    """保存 PNG + OCR（不入库）。返回 (path_rel, ocr_text)。

    供「一键截图 → 判断 Agent 给方案 → 可编辑框确认后入库」流程使用：
    只做图片校验/保存/OCR，不触发提纯判断与自动入库，等主人确认方案再入库。
    - image_bytes 非 PNG/JPEG / 不可解码抛 ValueError；
    - path_rel 为相对 DATA_DIR 路径（绝对路径不出库、不进审计日志）；
    - OCR 失败/引擎缺失 → ocr_text 为空串（上层按"未识别到文字"处理）。
    """
    if not image_bytes:
        raise ValueError("空图片数据")
    if not _is_image(image_bytes):
        raise ValueError("仅支持 PNG/JPEG 图片")
    _verify_image(image_bytes)

    ts = datetime.now().astimezone()
    filename = f"{ts.strftime('%H%M%S')}_{uuid.uuid4().hex[:8]}.png"
    out_path = _screenshots_dir(ts) / filename
    out_path.write_bytes(image_bytes)

    ocr_text = await asyncio.to_thread(ocr_image, out_path)
    path_rel = str(out_path.relative_to(DATA_DIR))
    log.info("截图已保存（未入库，待确认方案）", detail={"path_rel": path_rel,
                                               "ocr_len": len(ocr_text)})
    return path_rel, ocr_text
