import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.models import (
    Case,
    CaseCreate,
    CaseDetailResponse,
    CasePlanResponse,
    CasePlanStepResponse,
    CaseStepResultResponse,
)

router = APIRouter()

# DB 없을 때 fallback용 인메모리 스토어
_cases: dict[str, Case] = {}


def _db_case_to_response(db_case) -> Case:
    return Case(
        id=db_case.id,
        name=db_case.title,
        description=db_case.description or "",
        created_at=db_case.created_at.isoformat() if db_case.created_at else "",
        status=db_case.status,
    )


@router.get("/cases", response_model=list[Case])
async def get_cases():
    try:
        from app.agent_bridge import get_db_engine
        from database.engine import get_session
        from database.repository import list_cases

        engine = await get_db_engine()
        if engine:
            async with get_session(engine) as session:
                cases = await list_cases(session)
                return [_db_case_to_response(c) for c in cases]
    except Exception:
        pass
    return list(_cases.values())


@router.get("/cases/{case_id}", response_model=Case)
async def get_case(case_id: str):
    try:
        from app.agent_bridge import get_db_engine
        from database.engine import get_session
        from database.repository import get_case as db_get_case

        engine = await get_db_engine()
        if engine:
            async with get_session(engine) as session:
                db_case = await db_get_case(session, case_id)
                if db_case:
                    return _db_case_to_response(db_case)
    except Exception:
        pass

    case = _cases.get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@router.delete("/cases/{case_id}", status_code=204)
async def delete_case(case_id: str):
    try:
        from app.agent_bridge import get_db_engine
        from database.engine import get_session

        engine = await get_db_engine()
        if engine:
            async with get_session(engine) as session:
                from database.models import Case as DbCase
                db_case = await session.get(DbCase, case_id)
                if db_case is None:
                    raise HTTPException(status_code=404, detail="Case not found")
                await session.delete(db_case)
                await session.commit()
                return
    except HTTPException:
        raise
    except Exception:
        pass

    if case_id not in _cases:
        raise HTTPException(status_code=404, detail="Case not found")
    del _cases[case_id]


@router.get("/cases/{case_id}/detail", response_model=CaseDetailResponse)
async def get_case_detail(case_id: str):
    try:
        from app.agent_bridge import get_db_engine
        from database.engine import get_session
        from database.models import AnalysisSession, Case as DbCase, Report

        engine = await get_db_engine()
        if engine:
            async with get_session(engine) as session:
                db_case = await session.get(DbCase, case_id)
                if db_case is None:
                    raise HTTPException(status_code=404, detail="Case not found")

                sess_row = (await session.execute(
                    select(AnalysisSession)
                    .where(AnalysisSession.case_id == case_id)
                    .order_by(AnalysisSession.id.desc())
                    .limit(1)
                )).scalar_one_or_none()

                report_row = (await session.execute(
                    select(Report)
                    .where(Report.case_id == case_id)
                    .order_by(Report.id.desc())
                    .limit(1)
                )).scalar_one_or_none()

                return CaseDetailResponse(
                    id=db_case.id,
                    title=db_case.title,
                    description=db_case.description,
                    analyst=db_case.analyst,
                    status=db_case.status,
                    disk_image_path=db_case.disk_image_path,
                    user_prompt=db_case.user_prompt,
                    system_profile=sess_row.system_profile if sess_row else None,
                    analysis_strategy=sess_row.strategy if sess_row else None,
                    analysis_plan=sess_row.plan_text if sess_row else None,
                    report_summary=report_row.summary if report_row else None,
                    report_text=report_row.report_text if report_row else None,
                    report_dfxml=report_row.dfxml if report_row else None,
                    created_at=db_case.created_at.isoformat() if db_case.created_at else "",
                    updated_at=db_case.updated_at.isoformat() if db_case.updated_at else "",
                )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    raise HTTPException(status_code=404, detail="Case not found")


@router.get("/cases/{case_id}/plan", response_model=CasePlanResponse)
async def get_case_plan(case_id: str):
    try:
        from app.agent_bridge import get_db_engine
        from database.engine import get_session
        from database.models import AnalysisSession, PlanStep

        engine = await get_db_engine()
        if engine:
            async with get_session(engine) as session:
                sess_row = (await session.execute(
                    select(AnalysisSession)
                    .where(AnalysisSession.case_id == case_id)
                    .order_by(AnalysisSession.id.desc())
                    .limit(1)
                )).scalar_one_or_none()

                if sess_row is None:
                    return CasePlanResponse(plan_round=1, plan_text="", steps=[])

                steps_rows = (await session.execute(
                    select(PlanStep)
                    .where(PlanStep.session_id == sess_row.id)
                    .order_by(PlanStep.step_index)
                )).scalars().all()

                steps = [
                    CasePlanStepResponse(
                        step_index=s.step_index,
                        name=s.purpose[:50] if s.purpose else None,
                        mcp_server=s.mcp_server,
                        purpose=s.purpose,
                        hints=s.hints,
                        artifacts=s.artifacts,
                        is_followup=s.is_followup,
                    )
                    for s in steps_rows
                ]
                plan_round = (await session.execute(
                    select(AnalysisSession.id)
                    .where(AnalysisSession.case_id == case_id)
                )).scalars().all()

                return CasePlanResponse(
                    plan_round=len(plan_round),
                    plan_text=sess_row.plan_text or "",
                    steps=steps,
                )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return CasePlanResponse(plan_round=1, plan_text="", steps=[])


@router.get("/cases/{case_id}/results", response_model=list[CaseStepResultResponse])
async def get_case_results(case_id: str):
    try:
        from app.agent_bridge import get_db_engine
        from database.engine import get_session
        from database.models import AgentRun, DfxmlFragment, PlanStep, TaskResult

        engine = await get_db_engine()
        if engine:
            async with get_session(engine) as session:
                task_rows = (await session.execute(
                    select(TaskResult)
                    .where(TaskResult.case_id == case_id)
                    .order_by(TaskResult.started_at)
                )).scalars().all()

                results = []
                for tr in task_rows:
                    agent_run = await session.get(AgentRun, tr.agent_run_id)
                    plan_step = await session.get(PlanStep, agent_run.plan_step_id) if agent_run else None

                    frag_row = (await session.execute(
                        select(DfxmlFragment)
                        .where(DfxmlFragment.task_result_id == tr.id)
                        .limit(1)
                    )).scalar_one_or_none()

                    results.append(CaseStepResultResponse(
                        step_index=plan_step.step_index if plan_step else 0,
                        task_id=tr.task_id,
                        agent_name=agent_run.agent_name if agent_run else "",
                        status=tr.status,
                        content=tr.content,
                        output=tr.output,
                        raw_output_ref=tr.raw_output_ref,
                        elapsed_ms=tr.elapsed_ms,
                        artifacts=tr.artifacts,
                        dfxml_fragment=frag_row.dfxml_fragment if frag_row else None,
                        started_at=tr.started_at.isoformat() if tr.started_at else "",
                        completed_at=tr.completed_at.isoformat() if tr.completed_at else None,
                        created_at=tr.created_at.isoformat() if tr.created_at else "",
                    ))
                return results
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return []


@router.post("/cases", response_model=Case, status_code=201)
async def create_case(data: CaseCreate):
    try:
        from app.agent_bridge import get_db_engine
        from database.engine import get_session
        from database.repository import create_case as db_create_case

        engine = await get_db_engine()
        if engine:
            async with get_session(engine) as session:
                db_case = await db_create_case(
                    session,
                    title=data.title,
                    description=data.description,
                    analyst=data.analyst,
                )
                return _db_case_to_response(db_case)
    except Exception:
        pass

    # DB 미설정 fallback
    case = Case(
        id=str(uuid.uuid4()),
        name=data.title,
        description=data.description,
        created_at=datetime.now(timezone.utc).isoformat(),
        status="open",
    )
    _cases[case.id] = case
    return case
