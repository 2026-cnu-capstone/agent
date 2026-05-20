"""분석 이벤트 영속화 및 브로드캐스트 모듈

WebSocket 전송과 DB 저장을 통합 관리.
DB 저장 실패가 이벤트 전송을 블로킹하지 않도록 비동기 분리 처리.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Protocol

import structlog
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from database.repository import get_events_after, save_event

logger = structlog.get_logger()


class EventListener(Protocol):
    """이벤트 수신 리스너 프로토콜

    WebSocket 연결이나 기타 전송 채널이 구현
    """

    async def on_event(self, event_type: str, data: dict[str, Any]) -> None:
        """이벤트 수신 시 호출

        Args:
            event_type: 이벤트 유형
            data: 이벤트 페이로드
        """
        ...


class AnalysisEventStore:
    """분석 이벤트 영속화 및 브로드캐스트 관리자

    이벤트 발행 시 DB 저장과 리스너 전송을 동시에 수행.
    case_id별 리스너를 관리하며, 재접속 시 미수신 이벤트 복원 지원.

    Attributes:
        _engine: SQLAlchemy 비동기 엔진
        _session_factory: 세션 팩토리
        _listeners: case_id별 리스너 목록
        _lock: 리스너 목록 동시성 보호 락
    """

    def __init__(
        self,
        engine: AsyncEngine,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        """AnalysisEventStore 초기화

        Args:
            engine: SQLAlchemy 비동기 엔진
            session_factory: 세션 팩토리 (None이면 엔진 기반 자동 생성)
        """
        self._engine = engine
        self._session_factory = session_factory or async_sessionmaker(
            engine, expire_on_commit=False
        )
        self._listeners: dict[int, list[EventListener]] = {}
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def _get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """내부 세션 컨텍스트 매니저"""
        async with self._session_factory() as session:
            yield session

    async def add_listener(self, case_id: int, listener: EventListener) -> None:
        """case_id에 리스너 등록

        Args:
            case_id: 케이스 ID
            listener: 이벤트 리스너
        """
        async with self._lock:
            if case_id not in self._listeners:
                self._listeners[case_id] = []
            self._listeners[case_id].append(listener)

    async def remove_listener(self, case_id: int, listener: EventListener) -> None:
        """case_id에서 리스너 제거

        Args:
            case_id: 케이스 ID
            listener: 제거할 리스너
        """
        async with self._lock:
            listeners = self._listeners.get(case_id, [])
            if listener in listeners:
                listeners.remove(listener)
            if not listeners:
                self._listeners.pop(case_id, None)

    async def emit(
        self,
        case_id: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> int | None:
        """이벤트 발행 (DB 저장 + 리스너 브로드캐스트)

        DB 저장 실패 시에도 리스너 전송은 정상 수행.

        Args:
            case_id: 케이스 ID
            event_type: 이벤트 유형
            payload: 이벤트 데이터

        Returns:
            저장된 이벤트 ID (DB 저장 실패 시 None)
        """
        event_id = await self._persist(case_id, event_type, payload)

        broadcast_data = {
            "type": event_type,
            **payload,
        }
        if event_id is not None:
            broadcast_data["event_id"] = event_id

        await self._broadcast(case_id, event_type, broadcast_data)
        return event_id

    async def replay(
        self,
        case_id: int,
        listener: EventListener,
        after_id: int = 0,
    ) -> int:
        """미수신 이벤트 재전송

        사용자 재접속 시 after_id 이후의 이벤트를 조회하여 리스너에 전송

        Args:
            case_id: 케이스 ID
            listener: 재전송 대상 리스너
            after_id: 이 ID 이후의 이벤트만 재전송 (0이면 전체)

        Returns:
            재전송된 이벤트 수
        """
        try:
            async with self._get_session() as session:
                events = await get_events_after(session, case_id, after_id)
        except Exception:
            logger.exception("event_replay_query_failed", case_id=case_id)
            return 0

        for event in events:
            replay_data = {
                "type": event.event_type,
                "event_id": event.id,
                "replay": True,
                **event.payload,
            }
            try:
                await listener.on_event(event.event_type, replay_data)
            except Exception:
                logger.exception(
                    "event_replay_send_failed",
                    case_id=case_id,
                    event_id=event.id,
                )
                break

        return len(events)

    async def _persist(
        self,
        case_id: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> int | None:
        """이벤트 DB 저장 (실패 시 None 반환, 예외 전파 차단)

        Args:
            case_id: 케이스 ID
            event_type: 이벤트 유형
            payload: 이벤트 데이터

        Returns:
            저장된 이벤트 ID 또는 None
        """
        try:
            async with self._get_session() as session:
                event = await save_event(session, case_id, event_type, payload)
                return event.id
        except Exception:
            logger.exception(
                "event_persist_failed",
                case_id=case_id,
                event_type=event_type,
            )
            return None

    async def _broadcast(
        self,
        case_id: int,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """등록된 리스너에 이벤트 브로드캐스트

        개별 리스너 전송 실패는 다른 리스너에 영향을 주지 않음

        Args:
            case_id: 케이스 ID
            event_type: 이벤트 유형
            data: 전송할 데이터
        """
        async with self._lock:
            listeners = list(self._listeners.get(case_id, []))

        for listener in listeners:
            try:
                await listener.on_event(event_type, data)
            except Exception:
                logger.exception(
                    "event_broadcast_failed",
                    case_id=case_id,
                    event_type=event_type,
                )
