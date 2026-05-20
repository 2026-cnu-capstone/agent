"""포렌식 케이스 데이터 액세스 레이어"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AnalysisEvent, Case


async def create_case(
    session: AsyncSession,
    user_prompt: str,
    disk_image_path: str,
    disk_image_format: str,
) -> Case:
    """새 포렌식 케이스 생성

    Args:
        session: 비동기 DB 세션
        user_prompt: 사용자 입력 프롬프트 원문
        disk_image_path: 검증된 디스크 이미지 경로
        disk_image_format: 검증된 이미지 형식

    Returns:
        생성된 Case 인스턴스
    """
    case = Case(
        user_prompt=user_prompt,
        disk_image_path=disk_image_path,
        disk_image_format=disk_image_format,
    )
    session.add(case)
    await session.commit()
    await session.refresh(case)
    return case


async def get_case(session: AsyncSession, case_id: int) -> Case | None:
    """케이스 ID로 조회

    Args:
        session: 비동기 DB 세션
        case_id: 조회할 케이스 ID

    Returns:
        케이스 인스턴스 또는 None
    """
    return await session.get(Case, case_id)


async def list_cases(session: AsyncSession) -> list[Case]:
    """전체 케이스 목록 조회

    Args:
        session: 비동기 DB 세션

    Returns:
        케이스 목록 (생성일 내림차순)
    """
    result = await session.execute(
        select(Case).order_by(Case.created_at.desc())
    )
    return list(result.scalars().all())


async def save_event(
    session: AsyncSession,
    case_id: int,
    event_type: str,
    payload: dict[str, Any],
) -> AnalysisEvent:
    """분석 이벤트 저장

    Args:
        session: 비동기 DB 세션
        case_id: 케이스 ID
        event_type: 이벤트 유형
        payload: 이벤트 데이터

    Returns:
        생성된 AnalysisEvent 인스턴스
    """
    event = AnalysisEvent(
        case_id=case_id,
        event_type=event_type,
        payload=payload,
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


async def get_events_after(
    session: AsyncSession,
    case_id: int,
    after_id: int = 0,
) -> list[AnalysisEvent]:
    """특정 이벤트 ID 이후의 이벤트 목록 조회

    사용자 재접속 시 미수신 이벤트를 복원하기 위해 사용

    Args:
        session: 비동기 DB 세션
        case_id: 케이스 ID
        after_id: 이 ID 이후의 이벤트만 조회 (0이면 전체)

    Returns:
        이벤트 목록 (생성 순)
    """
    stmt = (
        select(AnalysisEvent)
        .where(
            AnalysisEvent.case_id == case_id,
            AnalysisEvent.id > after_id,
        )
        .order_by(AnalysisEvent.id.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_events_by_type(
    session: AsyncSession,
    case_id: int,
    event_type: str,
) -> list[AnalysisEvent]:
    """특정 유형의 이벤트 목록 조회

    Args:
        session: 비동기 DB 세션
        case_id: 케이스 ID
        event_type: 조회할 이벤트 유형

    Returns:
        해당 유형 이벤트 목록 (생성 순)
    """
    stmt = (
        select(AnalysisEvent)
        .where(
            AnalysisEvent.case_id == case_id,
            AnalysisEvent.event_type == event_type,
        )
        .order_by(AnalysisEvent.id.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
