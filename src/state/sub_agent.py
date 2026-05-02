"""Sub-Agent 공용 상태 스키마"""

from __future__ import annotations

import operator
from typing import Annotated, Any

from typing_extensions import TypedDict

from state.messages import TaskAssignment, TaskResult


class SubAgentState(TypedDict):
    """개별 Sub-Agent의 ReAct loop 내부 상태

    Manager가 TaskAssignment로부터 생성하여 subgraph에 전달하고,
    subgraph 완료 시 result 필드에서 TaskResult를 수집
    """

    messages: Annotated[list[dict[str, Any]], operator.add]
    """Sub-Agent 내부 대화 히스토리 (자동 누적)"""

    task: TaskAssignment
    """Manager로부터 할당받은 작업 정보"""

    tools: dict[str, Any]
    """이 Sub-Agent 전용 MCP 도구 명세 (해당 서버 도구만 포함)"""

    pending_tool_calls: list[dict[str, Any]]
    """실행 대기 중인 도구 호출 목록"""

    iteration_count: int
    """현재 ReAct 루프 반복 횟수"""

    max_iterations: int
    """최대 허용 반복 횟수"""

    output_chunks: list[str]
    """대용량 출력 청크 누적 (summarizer/chunker 입력용)"""

    result: TaskResult | None
    """Sub-Agent 실행 완료 후 최종 결과"""
