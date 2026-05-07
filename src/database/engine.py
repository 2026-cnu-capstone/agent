"""비동기 데이터베이스 엔진 및 세션 관리"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from database.models import Base

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(database_url: str) -> AsyncEngine:
    """비동기 SQLAlchemy 엔진 반환

    싱글턴 패턴으로 동일 URL에 대해 하나의 엔진만 생성

    Args:
        database_url: PostgreSQL 연결 URL
            (예: postgresql+asyncpg://user:pass@host/db)

    Returns:
        비동기 SQLAlchemy 엔진
    """
    global _engine
    if _engine is None:
        _engine = create_async_engine(database_url, echo=False)
    return _engine


def get_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """비동기 세션 팩토리 반환

    Args:
        engine: SQLAlchemy 비동기 엔진

    Returns:
        비동기 세션 팩토리
    """
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return _session_factory


@asynccontextmanager
async def get_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """비동기 세션 컨텍스트 매니저

    Args:
        engine: SQLAlchemy 비동기 엔진

    Yields:
        비동기 데이터베이스 세션
    """
    factory = get_session_factory(engine)
    async with factory() as session:
        yield session


async def init_db(engine: AsyncEngine) -> None:
    """데이터베이스 테이블 초기화

    Base에 등록된 모든 모델의 테이블을 생성

    Args:
        engine: SQLAlchemy 비동기 엔진
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def reset_engine() -> None:
    """엔진 및 세션 팩토리 초기화

    테스트 환경에서 엔진 상태를 리셋할 때 사용
    """
    global _engine, _session_factory
    _engine = None
    _session_factory = None
