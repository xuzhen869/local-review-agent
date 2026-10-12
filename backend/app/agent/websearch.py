"""联网搜索（问答升级阶段 2）：Bing 网页搜索（cn.bing.com，国内可达，免费无 key）。

- 抓取 https://cn.bing.com/search?q=...（UA + Accept-Language + 超时 + 退避重试）；
- 正则按 <li class="b_algo"> 块解析标题/URL/摘要；
- 返回 {title, url, snippet}，调用方标注「网上搜索」来源；
- 安全：snippet 属不可信外部文本，调用方 prompt 必须明示仅参考、不得视为指令；
- 降级：超时/反爬/限流/解析失败 → 返回空列表，绝不抛出（问答回退纯本地）。

> 注：原方案 DuckDuckGo 在国内网络被墙（http 000），已按实测切换 Bing（2026-08-09）。
"""
from __future__ import annotations

import asyncio
import html
import re
from typing import Any

import httpx

from ..logging_setup import get_logger

log = get_logger("agent.websearch")

_BING_URL = "https://cn.bing.com/search"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_TIMEOUT = httpx.Timeout(12.0, connect=6.0)
_MAX_RESULTS = 5
_RETRIES = 2
_ALGO_RE = re.compile(r'<li class="b_algo"')
_H2A_RE = re.compile(r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_P_RE = re.compile(r'<p[^>]*>(.*?)</p>', re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")

_MIN_SNIPPET_CHARS = 12


def _clean(segment: str) -> str:
    """去 HTML 标签、解实体、压缩空白（Bing 高亮用 <strong>，一并去除）。"""
    text = _TAG_RE.sub(" ", segment)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_results(text: str, limit: int) -> list[dict[str, str]]:
    """按 <li class="b_algo"> 块解析 Bing 结果 → [{title, url, snippet}]。"""
    starts = [m.start() for m in _ALGO_RE.finditer(text)]
    out: list[dict[str, str]] = []
    for i, sp in enumerate(starts):
        ep = starts[i + 1] if i + 1 < len(starts) else len(text)
        block = text[sp:ep]
        m = _H2A_RE.search(block)
        if not m:
            continue
        url = m.group(1).strip()
        title = _clean(m.group(2))
        if not url or not title:
            continue
        p = _P_RE.search(block)
        snippet = _clean(p.group(1)) if p else ""
        if snippet and len(snippet) < _MIN_SNIPPET_CHARS:
            snippet = ""          # 过短摘要视为无，不强求
        out.append({"title": title[:200], "url": url[:500], "snippet": snippet[:300]})
        if len(out) >= limit:
            break
    return out


async def web_search(query: str, limit: int = _MAX_RESULTS) -> list[dict[str, Any]]:
    """搜索返回 [{title, url, snippet}]；任何失败返回空列表（不抛出）。"""
    query = (query or "").strip()
    if not query:
        return []
    params = {"q": query, "setmkt": "zh-CN", "setlang": "zh-hans"}
    headers = {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    last_error = ""
    for attempt in range(1, _RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
                resp = await client.get(_BING_URL, params=params, headers=headers)
            if resp.status_code == 200 and resp.text:
                results = _parse_results(resp.text, limit)
                if results:
                    log.info("联网搜索成功", detail={"query": query[:60], "hits": len(results)})
                    return results
                last_error = "结果页解析为空（可能被反爬）"
            else:
                last_error = f"HTTP {resp.status_code}"
        except httpx.TimeoutException:
            last_error = "超时"
        except httpx.HTTPStatusError as exc:
            last_error = f"HTTP {exc.response.status_code}"
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {str(exc)[:100]}"
        log.warning("联网搜索失败（重试）",
                    detail={"query": query[:60], "attempt": attempt, "reason": last_error})
        if attempt < _RETRIES:
            await asyncio.sleep(1.5 * attempt)
    log.warning("联网搜索最终失败，问答回退纯本地", detail={"query": query[:60], "reason": last_error})
    return []
