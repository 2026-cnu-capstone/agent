"""LangGraph 에이전트 상태 스키마"""

from __future__ import annotations

import operator
from typing import Annotated, Any

from typing_extensions import TypedDict


class AgentState(TypedDict):
    """포렌식 에이전트의 LangGraph 상태

    향후 HITL 승인 게이트, DAG 파이프라인 등으로 확장될 기본 상태
    """

    messages: Annotated[list[dict[str, Any]], operator.add]
    """대화 메시지 히스토리 (자동 누적)"""

    tools: dict[str, Any]
    """현재 사용 가능한 MCP 도구 명세"""

    pending_tool_calls: list[dict[str, Any]]
    """실행 대기 중인 도구 호출 목록"""

    iteration_count: int
    """현재 에이전트 루프 반복 횟수 (안전 제한용)"""

    phase: str
    """현재 단계 (strategy | planning | intake | analysis | done)"""

    case_id: int | None
    """DB에 저장된 케이스 ID"""

    disk_image_path: str | None
    """검증된 디스크 이미지 파일 경로"""

    disk_image_format: str | None
    """검증된 디스크 이미지 형식 (e01, dd, raw)"""

    analysis_strategy: str
    """strategy_node가 수립한 분석 전략 텍스트"""

    analysis_plan: str
    """planning_node가 수립한 세부 실행 계획 텍스트"""

    plan_steps: list[dict[str, Any]]
    """파싱된 실행 단계 목록 [{index, name, tool, purpose, input_hint}, ...]"""

    current_step_index: int
    """현재 실행 중인 단계 인덱스"""

    step_results: list[dict[str, Any]]
    """각 단계 실행 결과 [{step, name, tool, output}, ...]"""
