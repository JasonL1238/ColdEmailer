"""
DuckDuckGo text search wrapper.

Uses the supported `ddgs` package and degrades to an empty result list when it
cannot search (rate limits, network, missing dependency) — callers must treat
[] as "no signal", not an error.
"""
import threading
import time
from typing import Dict, List

_lock = threading.Lock()
_last_call = 0.0
_MIN_INTERVAL = 0.75  # push harder; DDG still rate-limits if we go much lower


def _throttle():
    global _last_call
    with _lock:
        elapsed = time.time() - _last_call
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        _last_call = time.time()


def ddg_text_search(query: str, max_results: int = 8) -> List[Dict[str, str]]:
    """Return [{'title', 'href', 'body'}] — empty list on any failure."""
    _throttle()
    try:
        from ddgs import DDGS  # type: ignore
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
            return _normalize(results)
    except Exception as e:
        print(f"[ddg] search failed ({type(e).__name__}): {e}")
        return []


def _normalize(results) -> List[Dict[str, str]]:
    out = []
    for r in results or []:
        if not isinstance(r, dict):
            continue
        out.append({
            "title": r.get("title") or "",
            "href": r.get("href") or r.get("url") or r.get("link") or "",
            "body": r.get("body") or r.get("snippet") or "",
        })
    return [r for r in out if r["href"]]
