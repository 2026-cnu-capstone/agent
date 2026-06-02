"""포렌식 케이스 데이터 액세스 레이어"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    AgentRun,
    AnalysisEvent,
    AnalysisSession,
    Case,
    DfxmlFragment,
    PlanStep,
    Report,
    TaskResult,
)


async def create_case(
    session: AsyncSession,
    title: str,
    user_prompt: str | None = None,
    disk_image_path: str | None = None,
    description: str | None = None,
    analyst: str | None = None,
) -> Case:
    case = Case(
        title=title,
        description=description,
        user_prompt=user_prompt,
        disk_image_path=disk_image_path,
        analyst=analyst,
    )
    session.add(case)
    await session.commit()
    await session.refresh(case)
    return case


async def update_case_analysis_info(
    session: AsyncSession,
    case_id: str,
    user_prompt: str,
    disk_image_path: str,
) -> Case | None:
    case = await session.get(Case, case_id)
    if case is None:
        return None
    case.user_prompt = user_prompt
    case.disk_image_path = disk_image_path
    case.status = "running"
    await session.commit()
    await session.refresh(case)
    return case


async def update_case_status(
    session: AsyncSession,
    case_id: str,
    status: str,
) -> Case | None:
    case = await session.get(Case, case_id)
    if case is None:
        return None
    case.status = status
    await session.commit()
    await session.refresh(case)
    return case


async def get_case(session: AsyncSession, case_id: str) -> Case | None:
    return await session.get(Case, case_id)


async def list_cases(session: AsyncSession) -> list[Case]:
    result = await session.execute(
        select(Case).order_by(Case.created_at.desc())
    )
    return list(result.scalars().all())


async def create_analysis_session(
    session: AsyncSession,
    case_id: str,
    phase: str,
    system_profile: str | None = None,
    strategy: str | None = None,
    plan_text: str | None = None,
) -> AnalysisSession:
    analysis_session = AnalysisSession(
        case_id=case_id,
        phase=phase,
        system_profile=system_profile,
        strategy=strategy,
        plan_text=plan_text,
    )
    session.add(analysis_session)
    await session.commit()
    await session.refresh(analysis_session)
    return analysis_session


async def create_plan_step(
    session: AsyncSession,
    session_id: int,
    case_id: str,
    step_index: int,
    mcp_server: str | None = None,
    purpose: str | None = None,
    hints: str | None = None,
    artifacts: dict | None = None,
    is_followup: bool = False,
) -> PlanStep:
    step = PlanStep(
        session_id=session_id,
        case_id=case_id,
        step_index=step_index,
        mcp_server=mcp_server,
        purpose=purpose,
        hints=hints,
        artifacts=artifacts,
        is_followup=is_followup,
    )
    session.add(step)
    await session.commit()
    await session.refresh(step)
    return step


async def create_agent_run(
    session: AsyncSession,
    session_id: int,
    plan_step_id: int,
    agent_name: str,
    tool: str | None = None,
) -> AgentRun:
    agent_run = AgentRun(
        session_id=session_id,
        plan_step_id=plan_step_id,
        agent_name=agent_name,
        tool=tool,
    )
    session.add(agent_run)
    await session.commit()
    await session.refresh(agent_run)
    return agent_run


async def update_agent_run(
    session: AsyncSession,
    agent_run_id: int,
    status: str,
) -> AgentRun | None:
    agent_run = await session.get(AgentRun, agent_run_id)
    if agent_run is None:
        return None
    agent_run.status = status
    agent_run.finished_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(agent_run)
    return agent_run


async def create_task_result(
    session: AsyncSession,
    agent_run_id: int,
    case_id: str,
    task_id: str,
    status: str,
    output: str = "",
    dfxml_fragment: str = "",
    elapsed_ms: int | None = None,
) -> TaskResult:
    task_result = TaskResult(
        agent_run_id=agent_run_id,
        case_id=case_id,
        task_id=task_id,
        status=status,
        output=output,
        completed_at=datetime.now(timezone.utc),
        elapsed_ms=elapsed_ms,
    )
    session.add(task_result)
    await session.commit()
    await session.refresh(task_result)

    if dfxml_fragment:
        frag = DfxmlFragment(
            task_result_id=task_result.id,
            dfxml_fragment=dfxml_fragment,
        )
        session.add(frag)
        await session.commit()

    return task_result


async def get_task_results_by_run(
    session: AsyncSession,
    agent_run_id: int,
) -> list[TaskResult]:
    result = await session.execute(
        select(TaskResult)
        .where(TaskResult.agent_run_id == agent_run_id)
        .order_by(TaskResult.started_at)
    )
    return list(result.scalars().all())


async def save_event(
    session: AsyncSession,
    case_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> AnalysisEvent:
    event = AnalysisEvent(
        case_id=case_id,
        event_type=event_type,
        payload=payload,
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


async def create_report(
    session: AsyncSession,
    case_id: str,
    summary: str | None = None,
    report_text: str | None = None,
    dfxml: str | None = None,
    status: str = "done",
) -> Report:
    report = Report(
        case_id=case_id,
        summary=summary,
        report_text=report_text,
        dfxml=dfxml,
        status=status,
    )
    session.add(report)
    await session.commit()
    await session.refresh(report)
    return report


async def get_events_after(
    session: AsyncSession,
    case_id: str,
    after_id: int = 0,
) -> list[AnalysisEvent]:
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
    case_id: str,
    event_type: str,
) -> list[AnalysisEvent]:
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
