"""本地语义检索（问答升级阶段 1）：BGE-small-zh ONNX 推理 + 文本切块 + 余弦检索。

- 模型：Xenova/bge-small-zh-v1.5 的 ONNX 导出（fp32），onnxruntime CPU 推理；
- tokenizer：tokenizers 库加载 tokenizer.json（BERT 中文，字级分词）；
- 向量：last_hidden_state 的 CLS token 向量 + L2 归一化；维度取模型实际输出（动态，勿硬编码）；
- query 指令前缀（BGE 官方）：为这个句子生成表示以用于检索相关文章：；
- 惰性单例加载（同 capture.ocr 模式），模型/tokenizer 缺失自动降级返回空检索；
- 存储：embeddings 表（text + vector JSON + model 标识），换模型按 model 重建索引。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from ..logging_setup import get_logger

log = get_logger("agent.embeddings")

_MODEL_DIR = Path(__file__).resolve().parent / "bge"
_MODEL_FILE = _MODEL_DIR / "model.onnx"
_TOKENIZER_FILE = _MODEL_DIR / "tokenizer.json"
_MODEL_TAG = "bge-small-zh-v1.5"   # embeddings.model 标识，换模型需按此重建
_MAX_TOKENS = 512                  # BERT 输入上限
_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："   # BGE 官方 query 指令
_RETRY_INTERVAL = 60.0             # 模型不可用时重试间隔（秒）
_MAX_VECTORS_LOAD = 200_000        # 内存余弦检索向量条数上限（防失控）
_CHUNK_MAX_CHARS = 400             # 切块字符上限（中文≈token，安全低于 512）
_CHUNK_OVERLAP = 48                # 块间重叠字符，保证语义连续
_SENT_BOUNDARIES = "。！？!?；;\n"

_session: Any = None
_tokenizer: Any = None
_session_retry_at = 0.0
_session_lock = asyncio.Lock()


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 可用性 / 惰性加载
# ---------------------------------------------------------------------------
def embedding_available() -> bool:
    """模型与 tokenizer 文件是否已就绪（未就绪时语义检索降级为纯关键词）。"""
    return _MODEL_FILE.exists() and _TOKENIZER_FILE.exists()


def _get_session() -> Any:
    """惰性初始化 onnxruntime 推理会话；曾失败按间隔重试，就绪后无需重启。"""
    global _session, _session_retry_at
    if _session is None and time.monotonic() >= _session_retry_at:
        try:
            import onnxruntime as ort
            _session = ort.InferenceSession(str(_MODEL_FILE), providers=["CPUExecutionProvider"])
            log.info("BGE 推理会话就绪", detail={"model": _MODEL_TAG,
                                               "inputs": [i.name for i in _session.get_inputs()],
                                               "outputs": [o.name for o in _session.get_outputs()]})
        except Exception as exc:
            log.warning("BGE 模型加载失败，语义检索降级为关键词",
                        detail={"error": f"{type(exc).__name__}: {exc}"})
            _session = False
            _session_retry_at = time.monotonic() + _RETRY_INTERVAL
    return _session or None


def _get_tokenizer() -> Any:
    """惰性加载 tokenizer（启用截断）；失败置 False，仅随模型重试周期一起恢复。"""
    global _tokenizer
    if _tokenizer is None:
        try:
            from tokenizers import Tokenizer
            tok = Tokenizer.from_file(str(_TOKENIZER_FILE))
            tok.enable_truncation(_MAX_TOKENS)   # tokenizers 库需先启用截断
            _tokenizer = tok
            log.info("BGE tokenizer 就绪")
        except Exception as exc:
            log.warning("BGE tokenizer 加载失败",
                        detail={"error": f"{type(exc).__name__}: {exc}"})
            _tokenizer = False
    return _tokenizer or None


def _encode(text: str) -> tuple[Any, Any, Any] | None:
    """编码为 (input_ids, attention_mask, token_type_ids)；截断由 tokenizer 启用。"""
    tok = _get_tokenizer()
    if tok is None:
        return None
    try:
        enc = tok.encode(text, add_special_tokens=True)
        ids = enc.ids
        mask = enc.attention_mask if hasattr(enc, "attention_mask") else [1] * len(ids)
        types = [0] * len(ids)   # 单段输入，segment id 全 0
        return ids, mask, types
    except Exception as exc:
        log.warning("BGE 文本编码失败", detail={"error": f"{type(exc).__name__}: {exc}"})
        return None


# ---------------------------------------------------------------------------
# 向量化
# ---------------------------------------------------------------------------
def embed_text(text: str) -> np.ndarray | None:
    """返回 L2 归一化向量（维度取模型实际 hidden_size，勿假设固定值）。

    模型不可用 / 推理失败返回 None。query 统一加 BGE 官方指令前缀提升召回。
    """
    sess = _get_session()
    if sess is None:
        return None
    encoded = _encode(_QUERY_PREFIX + text)
    if encoded is None:
        return None
    ids, mask, types = encoded
    try:
        feeds = {
            "input_ids": np.array([ids], dtype=np.int64),
            "attention_mask": np.array([mask], dtype=np.int64),
            "token_type_ids": np.array([types], dtype=np.int64),
        }
        outs = sess.run(None, feeds)
        hidden = outs[0]                       # [1, seq_len, hidden_size]
        vec = np.asarray(hidden[0, 0, :], dtype=np.float32)   # CLS token
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm > 0 else vec
    except Exception as exc:
        log.warning("BGE 推理失败", detail={"error": f"{type(exc).__name__}: {exc}"})
        return None


# ---------------------------------------------------------------------------
# 切块
# ---------------------------------------------------------------------------
def chunk_text(text: str, max_chars: int = _CHUNK_MAX_CHARS,
               overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """按字符块切分，重叠 overlap 字；优先在句子边界断开。"""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            # 在块后半段找句子边界，避免切断语义
            cut = max((text.rfind(ch, start + max_chars // 2, end)
                       for ch in _SENT_BOUNDARIES), default=-1)
            if cut != -1:
                end = cut + 1
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks


# ---------------------------------------------------------------------------
# 入库 / 检索
# ---------------------------------------------------------------------------
async def index_event(db: Any, event_id: str, text: str) -> int:
    """将事件文本切块向量化入库；返回写入块数。模型不可用返回 0（不阻断主流程）。"""
    chunks = chunk_text(text)
    if not chunks:
        return 0
    inserted = 0
    for chunk in chunks:
        vec = await asyncio.to_thread(embed_text, chunk)
        if vec is None:
            break
        chunk_id = uuid.uuid4().hex
        await db.execute(
            "INSERT INTO embeddings (id, event_id, card_id, text, vector, model, created_at) "
            "VALUES (?, ?, NULL, ?, ?, ?, ?)",
            (chunk_id, event_id, chunk[:1000], json.dumps(vec.tolist()),
             _MODEL_TAG, _now_iso()))
        inserted += 1
    if inserted:
        await db.commit()
    return inserted


async def index_card(db: Any, card_id: str, text: str) -> int:
    """将复习卡片文本向量化入库（判断 Agent 沉淀确认后调用）。"""
    chunks = chunk_text(text)
    if not chunks:
        return 0
    inserted = 0
    for chunk in chunks:
        vec = await asyncio.to_thread(embed_text, chunk)
        if vec is None:
            break
        chunk_id = uuid.uuid4().hex
        await db.execute(
            "INSERT INTO embeddings (id, event_id, card_id, text, vector, model, created_at) "
            "VALUES (?, NULL, ?, ?, ?, ?, ?)",
            (chunk_id, card_id, chunk[:1000], json.dumps(vec.tolist()),
             _MODEL_TAG, _now_iso()))
        inserted += 1
    if inserted:
        await db.commit()
    return inserted


async def semantic_search(db: Any, query: str, top_k: int = 10) -> list[dict[str, Any]]:
    """余弦 top-k 检索（全量内存加载，万级条毫秒级）；模型不可用返回空列表。

    返回 [{event_id, card_id, text, score}]，按分数降序。
    """
    qvec = await asyncio.to_thread(embed_text, query)
    if qvec is None:
        return []
    rows = await db.fetchall(
        "SELECT id, event_id, card_id, text, vector FROM embeddings "
        "WHERE model = ? LIMIT ?", (_MODEL_TAG, _MAX_VECTORS_LOAD))
    if not rows:
        return []
    vecs: list[np.ndarray] = []
    valid: list[int] = []
    for i, r in enumerate(rows):
        try:
            v = np.asarray(json.loads(r[4]), dtype=np.float32)
        except (ValueError, TypeError):
            continue
        if v.size == 0:
            continue
        vecs.append(v)
        valid.append(i)
    if not vecs:
        return []
    matrix = np.vstack(vecs)                   # [N, hidden_size]，动态维度
    scores = matrix @ qvec                     # 均已 L2 归一化 → 余弦
    order = np.argsort(-scores)
    out: list[dict[str, Any]] = []
    for rank, idx in enumerate(order[:top_k]):
        if float(scores[idx]) <= 0.0:
            break
        row_i = valid[idx]
        out.append({"event_id": rows[row_i][1], "card_id": rows[row_i][2],
                    "text": rows[row_i][3], "score": float(scores[idx])})
    return out


async def rebuild_index(db: Any, events: list[dict[str, Any]]) -> dict[str, Any]:
    """全量重建：删除旧索引后重新向量化。events 需含 id/content 字段。"""
    await db.execute("DELETE FROM embeddings WHERE model = ?", (_MODEL_TAG,))
    await db.commit()
    indexed = 0
    for e in events:
        content = e.get("content")
        if not content or not content.strip():
            continue
        indexed += await index_event(db, e["id"], content)
    return {"indexed": indexed, "events": len(events)}
