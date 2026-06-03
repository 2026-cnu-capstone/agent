"""API 요청/응답 Pydantic 모델"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class CaseCreate(BaseModel):
    """케이스 생성 요청"""

    title: str
    description: str = ""
    analyst: str | None = None


class Case(BaseModel):
    """케이스 정보"""

    id: str
    name: str
    description: str
    created_at: str
    status: Literal["open", "closed", "running", "done", "failed"]


class AnalysisRequest(BaseModel):
    """분석 시작 요청"""

    case_id: str
    disk_image_path: str
    prompt: str


class StrategyApproval(BaseModel):
    """전략 승인/수정 요청"""

    approved: bool
    feedback: str = ""


class PlanApproval(BaseModel):
    """계획 승인/수정 요청"""

    approved: bool
    feedback: str = ""


class StepUpdate(BaseModel):
    """Sub-Agent 단계 진행 이벤트"""

    step_index: int
    total: int
    step_name: str
    agent_name: str
    status: str
    output: str = ""
    elapsed: str = ""
    dfxml_fragment: str = ""


class CaseDetailResponse(BaseModel):
    id: str
    title: str
    description: str | None
    analyst: str | None
    status: str
    disk_image_path: str | None
    user_prompt: str | None
    system_profile: str | None
    analysis_strategy: str | None
    analysis_plan: str | None
    report_summary: str | None
    report_text: str | None
    report_dfxml: str | None
    created_at: str
    updated_at: str


class CasePlanStepResponse(BaseModel):
    step_index: int
    name: str | None
    mcp_server: str | None
    purpose: str | None
    hints: str | None
    artifacts: Any
    is_followup: bool


class CasePlanResponse(BaseModel):
    plan_round: int
    plan_text: str
    steps: list[CasePlanStepResponse]


class CaseStepResultResponse(BaseModel):
    step_index: int
    task_id: str
    agent_name: str
    status: str
    content: str | None
    output: str | None
    raw_output_ref: str | None
    elapsed_ms: int | None
    artifacts: Any
    dfxml_fragment: str | None
    started_at: str
    completed_at: str | None
    created_at: str
