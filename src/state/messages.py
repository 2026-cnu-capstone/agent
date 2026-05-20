"""에이전트 간 통신 메시지 타입 정의"""

from __future__ import annotations

from typing import Any

from typing_extensions import TypedDict


class NodeEdge(TypedDict):
    """노드 간 데이터 흐름 엣지

    그래프 시각화에서 두 노드 사이의 연결과 전달 데이터를 표현

    Attributes:
        from_node: 소스 노드 ID
        to_node: 타겟 노드 ID
        data_key: 전달 데이터 키 (예: analysis_strategy, task_result)
        data_summary: 전달 데이터 요약 (300자 이내)
    """

    from_node: str
    """소스 노드 ID"""

    to_node: str
    """타겟 노드 ID"""

    data_key: str
    """전달 데이터 키"""

    data_summary: str
    """전달 데이터 요약"""


class NodeRelation(TypedDict):
    """노드 관계 및 레이아웃 정보

    프론트엔드 DAG 시각화를 위한 노드 위치, 상태, 연결 정보

    Attributes:
        id: 노드 고유 ID (예: strategy, step_0, report)
        label: 표시명 (예: 레지스트리 분석)
        type: 노드 유형 (strategy | planning | execution | report)
        row: 그래프 Y축 위치 (0부터, 위에서 아래)
        col: 같은 row 내 X축 위치 (병렬 노드 대비)
        status: 노드 상태 (pending | running | success | error | skip)
        inputs: 이 노드로 들어오는 엣지 목록
        outputs: 이 노드에서 나가는 엣지 목록
    """

    id: str
    """노드 고유 ID"""

    label: str
    """표시명"""

    type: str
    """노드 유형"""

    row: int
    """그래프 Y축 위치"""

    col: int
    """같은 row 내 X축 위치"""

    status: str
    """노드 상태"""

    inputs: list[NodeEdge]
    """이 노드로 들어오는 엣지 목록"""

    outputs: list[NodeEdge]
    """이 노드에서 나가는 엣지 목록"""


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
