"""Small in-process TTL cache for search responses.

Agents often repeat the same query within a few seconds. Entries are keyed by the request
JSON plus a global *index version* that bumps on every ``document.*`` event fanned out by
:data:`kb.events.broker`, so a document finishing (re)indexing invalidates everything
cached in that process. TTL bounds staleness when no broker is running (CLI, MCP stdio).
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any

from kb.schemas import SearchResponse


class SearchCache:
    def __init__(self, ttl_s: float = 30.0, max_entries: int = 512) -> None:
        self.ttl_s = ttl_s
        self.max_entries = max_entries
        self.version = 0
        self._data: OrderedDict[str, tuple[float, int, SearchResponse]] = OrderedDict()

    def bump(self) -> None:
        self.version += 1
        self._data.clear()

    def clear(self) -> None:
        self._data.clear()

    def get(self, key: str) -> SearchResponse | None:
        if self.ttl_s <= 0:
            return None
        item = self._data.get(key)
        if item is None:
            return None
        stored_at, version, resp = item
        if version != self.version or time.monotonic() - stored_at > self.ttl_s:
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return resp.model_copy(deep=True)

    def put(self, key: str, resp: SearchResponse) -> None:
        if self.ttl_s <= 0:
            return
        self._data[key] = (time.monotonic(), self.version, resp.model_copy(deep=True))
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)


def attach_to_broker(cache: SearchCache) -> None:
    """Invalidate the cache whenever any document changes state in any process."""
    from kb.events import broker

    def invalidate(event: dict[str, Any]) -> None:
        if str(event.get("type", "")).startswith("document"):
            cache.bump()

    broker.add_listener(invalidate)
