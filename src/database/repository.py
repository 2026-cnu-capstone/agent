"""포렌식 케이스 데이터 액세스 레이어"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Case


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
