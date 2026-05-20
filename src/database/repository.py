"""포렌식 케이스 데이터 액세스 레이어"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AgentRun, AnalysisEvent, Case, StepResult


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
    dfxml_fragment: str = "",
) -> StepResult:
    """단계별 실행 결과 저장

    Args:
        session: 비동기 DB 세션
        agent_run_id: 연관 AgentRun ID
        step_index: 실행 단계 인덱스
        tool_name: 사용된 MCP 도구 이름
        output_summary: 요약된 출력
        raw_output: 원본 전체 출력
        dfxml_fragment: 해당 step의 DFXML 프래그먼트

    Returns:
        생성된 StepResult 인스턴스
    """
    step_result = StepResult(
        agent_run_id=agent_run_id,
        step_index=step_index,
        tool_name=tool_name,
        output_summary=output_summary,
        raw_output=raw_output,
        dfxml_fragment=dfxml_fragment,
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


async def get_dfxml_fragment(
    session: AsyncSession,
    agent_run_id: int,
    step_index: int,
) -> str | None:
    """특정 step의 DFXML 프래그먼트 조회

    Args:
        session: 비동기 DB 세션
        agent_run_id: AgentRun ID
        step_index: 조회할 step 인덱스

    Returns:
        DFXML 프래그먼트 문자열 또는 None
    """
    result = await session.execute(
        select(StepResult.dfxml_fragment)
        .where(
            StepResult.agent_run_id == agent_run_id,
            StepResult.step_index == step_index,
        )
    )
    row = result.scalar_one_or_none()
    return row if row else None
