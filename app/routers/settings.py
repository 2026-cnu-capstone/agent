"""LLM 설정 API 라우터"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agent_bridge import get_api_choice, get_llm_options, get_settings, set_api_choice

router = APIRouter(prefix="/api/settings", tags=["settings"])


class LlmChoiceRequest(BaseModel):
    """LLM 선택 변경 요청"""

    choice: str


@router.get("/llm")
async def api_get_llm_options():
    """사용 가능한 LLM 옵션 및 현재 선택값 조회"""
    return await get_llm_options()


@router.put("/llm")
async def api_set_llm_choice(req: LlmChoiceRequest):
    """메인 LLM 선택 변경"""
    options = await get_llm_options()
    valid_ids = {o["id"] for o in options["options"]}
    if req.choice not in valid_ids:
        raise HTTPException(
            status_code=400,
            detail=f"유효하지 않은 선택: {req.choice} (가능: {valid_ids})",
        )
    set_api_choice(req.choice)
    return {"current": get_api_choice()}


@router.get("/execution")
async def api_get_execution_options():
    """실행 방식 설정 조회

    프론트엔드가 워크플로 캔버스를 병렬/순차 레이아웃 중 무엇으로 그릴지
    결정하는 데 사용

    Returns:
        Sub-Agent 동시 실행 수와 병렬 여부
    """
    settings = await get_settings()
    max_parallel = settings.execution_max_parallel
    return {"max_parallel": max_parallel, "parallel": max_parallel > 1}

