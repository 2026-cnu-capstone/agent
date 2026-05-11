"""포렌식 케이스 데이터 액세스 레이어"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AgentRun, Case, StepResult


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


async def create_agent_run(
    session: AsyncSession,
    case_id: int,
    agent_name: str,
) -> AgentRun:
    """새 에이전트 실행 이력 생성

    Args:
        session: 비동기 DB 세션
        case_id: 연관 케이스 ID
        agent_name: 실행할 에이전트 이름

    Returns:
        생성된 AgentRun 인스턴스
    """
    agent_run = AgentRun(case_id=case_id, agent_name=agent_name)
    session.add(agent_run)
    await session.commit()
    await session.refresh(agent_run)
    return agent_run


async def update_agent_run(
    session: AsyncSession,
    agent_run_id: int,
    status: str,
) -> AgentRun | None:
    """에이전트 실행 상태 업데이트

    Args:
        session: 비동기 DB 세션
        agent_run_id: 업데이트할 AgentRun ID
        status: 새 상태 (success, error)

    Returns:
        업데이트된 AgentRun 인스턴스 또는 None
    """
    from datetime import datetime, timezone

    agent_run = await session.get(AgentRun, agent_run_id)
    if agent_run is None:
        return None
    agent_run.status = status
    agent_run.finished_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(agent_run)
    return agent_run


async def create_step_result(
    session: AsyncSession,
    agent_run_id: int,
    step_index: int,
    tool_name: str,
    output_summary: str,
    raw_output: str = "",
) -> StepResult:
    """단계별 실행 결과 저장

    Args:
        session: 비동기 DB 세션
        agent_run_id: 연관 AgentRun ID
        step_index: 실행 단계 인덱스
        tool_name: 사용된 MCP 도구 이름
        output_summary: 요약된 출력
        raw_output: 원본 전체 출력

    Returns:
        생성된 StepResult 인스턴스
    """
    step_result = StepResult(
        agent_run_id=agent_run_id,
        step_index=step_index,
        tool_name=tool_name,
        output_summary=output_summary,
        raw_output=raw_output,
    )
    session.add(step_result)
    await session.commit()
    await session.refresh(step_result)
    return step_result


async def get_step_results_by_run(
    session: AsyncSession,
    agent_run_id: int,
) -> list[StepResult]:
    """AgentRun에 속한 단계별 결과 목록 조회

    Args:
        session: 비동기 DB 세션
        agent_run_id: 조회할 AgentRun ID

    Returns:
        단계 인덱스 오름차순 정렬된 StepResult 목록
    """
    result = await session.execute(
        select(StepResult)
        .where(StepResult.agent_run_id == agent_run_id)
        .order_by(StepResult.step_index)
    )
    return list(result.scalars().all())
