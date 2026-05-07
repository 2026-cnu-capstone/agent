"""에이전트 간 통신 메시지 타입 정의"""

from __future__ import annotations

from typing import Any

from typing_extensions import TypedDict


class TaskAssignment(TypedDict):
    """Manager가 Sub-Agent에 전달하는 작업 할당

    Manager의 routing_node가 plan_steps를 기반으로 생성하여
    각 Sub-Agent subgraph에 입력으로 전달
    """

    task_id: str
    """고유 작업 식별자"""

    agent_name: str
    """할당 대상 Sub-Agent 이름 (예: dissect, sleuthkit)"""

    step: dict[str, Any]
    """계획 단계 정보 {index, name, tool, purpose, output_hint, input_hint}"""

    context: str
    """이전 단계 결과 요약 등 작업에 필요한 컨텍스트"""

    disk_image_path: str
    """분석 대상 디스크 이미지 경로"""


class TaskResult(TypedDict):
    """Sub-Agent가 Manager에 반환하는 작업 결과"""

    task_id: str
    """TaskAssignment의 task_id와 동일"""

    agent_name: str
    """결과를 생성한 Sub-Agent 이름"""

    status: str
    """실행 결과 상태 ("success" | "error" | "partial")"""

    output: str
    """요약된 출력 (LLM summarizer 적용 후)"""

    raw_output_ref: str
    """원본 전체 출력 참조 (DB ID 또는 파일 경로)"""

    artifacts: list[dict[str, Any]]
    """추출된 구조화 아티팩트 목록"""

    follow_up: dict[str, Any] | None
    """추가 조사 요청 (None이면 불필요)"""


class AgentMessage(TypedDict):
    """에이전트 간 일반 메시지 (감사 로그 및 상태 전달용)"""

    source: str
    """송신 에이전트 이름"""

    target: str
    """수신 에이전트 이름"""

    content: str
    """메시지 본문"""

    data: dict[str, Any]
    """구조화 데이터 페이로드"""

    timestamp: str
    """ISO 8601 형식 타임스탬프"""
