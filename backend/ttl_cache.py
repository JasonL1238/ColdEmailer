"""One time-to-live cache, shared by the modules that were each writing it.

Every entry is `key -> (expires_at, value)`. Two rules hold everywhere:

- A miss may only ever cost work, never change an answer. Callers must treat
  eviction as "ask again", which is why the cap is enforced by clearing the
  whole dict rather than by tracking insertion order.
- Nothing does I/O under the lock, so a single module-level lock serialising
  every caller is cheap.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional

LOCK = threading.Lock()


def cache_get(cache: Dict, key: str, *, clock: Callable[[], float] = time.time) -> Optional[Any]:
    with LOCK:
        entry = cache.get(key)
        if not entry:
            return None
        expires, value = entry
        if clock() >= expires:
            cache.pop(key, None)
            return None
        return value


def cache_put(cache: Dict, key: str, value: Any, ttl: float, cap: int,
              *, clock: Callable[[], float] = time.time) -> None:
    with LOCK:
        if len(cache) >= cap:
            # Dropping the cache only ever costs re-work, never an answer.
            cache.clear()
        cache[key] = (clock() + ttl, value)


def cache_clear(*caches: Dict) -> None:
    with LOCK:
        for cache in caches:
            cache.clear()
