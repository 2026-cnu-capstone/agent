"""WebSocket 연결 관리 (asyncio Lock 기반 동시성 보호)"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog
from fastapi import WebSocket

logger = structlog.get_logger()


class ConnectionManager:
    """케이스별 WebSocket 연결 관리"""

    def __init__(self) -> None:
        self._connections: dict[str, list[WebSocket]] = {}
        self._lock = asyncio.Lock()
        self._db_engine = None

    def set_engine(self, engine: Any) -> None:
        """DB 엔진 설정 — analysis_event 저장에 사용"""
        self._db_engine = engine

    async def connect(self, case_id: str, websocket: WebSocket) -> None:
        """새 WebSocket 연결 등록"""
        await websocket.accept()
        async with self._lock:
            if case_id not in self._connections:
                self._connections[case_id] = []
            self._connections[case_id].append(websocket)

    async def disconnect(self, case_id: str, websocket: WebSocket) -> None:
        """WebSocket 연결 해제"""
        async with self._lock:
            if case_id in self._connections:
                self._connections[case_id] = [
                    ws for ws in self._connections[case_id] if ws != websocket
                ]
                if not self._connections[case_id]:
                    del self._connections[case_id]

    async def send_event(self, case_id: str, event_type: str, data: dict[str, Any]) -> None:
        """특정 케이스의 모든 연결에 이벤트 전송하고 DB에 저장"""
        message = json.dumps({"type": event_type, **data}, ensure_ascii=False)

        if self._db_engine:
            try:
                import sys
                from pathlib import Path
                _src = Path(__file__).parent.parent / "src"
                if str(_src) not in sys.path:
                    sys.path.insert(0, str(_src))
                from database.engine import get_session
                from database.repository import save_event
                async with get_session(self._db_engine) as _session:
                    await save_event(_session, case_id, event_type, data)
            except Exception as exc:
                logger.warning("event_persist_failed", case_id=case_id, event_type=event_type, error=str(exc))

        async with self._lock:
            connections = list(self._connections.get(case_id, []))

        disconnected: list[WebSocket] = []
        for ws in connections:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)

        if disconnected:
            async with self._lock:
                for ws in disconnected:
                    if case_id in self._connections:
                        self._connections[case_id] = [
                            c for c in self._connections[case_id] if c != ws
                        ]


manager = ConnectionManager()
