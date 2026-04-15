"""TTL 기반 MCP 도구 명세 인메모리 캐시"""

from __future__ import annotations

import time
from typing import Any


class ToolCache:
    """TTL 기반 MCP 도구 명세 캐시

    MCP 서버에서 조회한 Tool 객체를 메모리에 보관하고,
    TTL 만료 시 다음 조회에서 갱신 필요 여부를 판단
    """

    def __init__(self, ttl_seconds: int = 300) -> None:
        self._tools: dict[str, Any] = {}
        self._last_refresh: float = 0.0
        self._ttl = ttl_seconds

    @property
    def is_stale(self) -> bool:
        """캐시 만료 여부 확인"""
        if not self._tools:
            return True
        return (time.monotonic() - self._last_refresh) >= self._ttl

    def get_all(self) -> dict[str, Any] | None:
        """캐시된 도구 반환, 만료 시 None 반환"""
        if self.is_stale:
            return None
        return dict(self._tools)

    def update(self, tools: dict[str, Any]) -> None:
        """캐시 내용 교체 및 TTL 리셋"""
        self._tools = dict(tools)
        self._last_refresh = time.monotonic()

    def invalidate(self) -> None:
        """캐시 강제 만료"""
        self._last_refresh = 0.0

    def __len__(self) -> int:
        return len(self._tools)
