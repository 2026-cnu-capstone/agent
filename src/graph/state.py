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
    """현재 HITL 단계 (향후 사용)"""
