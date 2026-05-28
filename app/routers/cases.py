import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.models import Case, CaseCreate

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
                    title=data.name,
                    description=data.description,
                )
                return _db_case_to_response(db_case)
    except Exception:
        pass

    # DB 미설정 fallback
    case = Case(
        id=str(uuid.uuid4()),
        name=data.name,
        description=data.description,
        created_at=datetime.now(timezone.utc).isoformat(),
        status="open",
    )
    _cases[case.id] = case
    return case
