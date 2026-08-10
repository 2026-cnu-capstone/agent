"""Manager Agent 최상위 오케스트레이션 상태 스키마"""

from __future__ import annotations

import operator
from typing import Annotated, Any

from typing_extensions import TypedDict

from state.messages import AgentMessage, TaskAssignment, TaskResult


class ManagerState(TypedDict):
    """Manager Agent의 LangGraph 상태

    전체 멀티 에이전트 오케스트레이션을 관리하는 최상위 상태.
    Sub-Agent 호출, HITL 게이트, 결과 수집을 포함.
    """

    messages: Annotated[list[dict[str, Any]], operator.add]
    """사용자 및 에이전트 대화 히스토리 (자동 누적)"""

    phase: str
    """현재 단계 (strategy | planning | execution | summary | report | done)"""

    case_id: int | None
    """DB에 저장된 케이스 ID"""

    disk_image_path: str | None
    """검증된 디스크 이미지 파일 경로"""

    disk_image_format: str | None
    """검증된 디스크 이미지 형식 (e01, dd, raw)"""

    system_profile: str | None
    """디스크 이미지 시스템 프로필 (OS, 호스트명, 사용자 목록 등)"""

    analysis_strategy: str
    """확정된 분석 전략 텍스트"""

    analysis_plan: str
    """확정된 세부 실행 계획 텍스트"""

    plan_steps: list[dict[str, Any]]
    """파싱된 실행 단계 목록 [{index, name, tool, purpose, ...}, ...]"""

    current_step_index: int
    """현재 실행 중인 단계 인덱스"""

    task_queue: list[TaskAssignment]
    """Sub-Agent에 할당 대기 중인 작업 큐"""

    task_results: Annotated[list[TaskResult], operator.add]
    """Sub-Agent 실행 결과 누적 목록"""

    agent_messages: Annotated[list[AgentMessage], operator.add]
    """에이전트 간 메시지 로그 (감사 추적용)"""

    active_agents: list[str]
    """현재 실행 중인 Sub-Agent 이름 목록"""

    hitl_pending: bool
    """HITL 승인 대기 여부"""

    hitl_type: str
    """대기 중인 HITL 유형 (strategy | mcp_install | plan | result | 빈 문자열)"""

    discovery_report: dict[str, Any] | None
    """포렌식 도구 탐지 및 MCP 추천 리포트 (DiscoveryReport 직렬화 결과)"""

    pending_mcp_installs: list[dict[str, Any]]
    """사용자 승인 대기 중인 MCP 설치 후보 목록"""

    installed_mcp_servers: list[str]
    """이번 세션에서 설치·등록된 MCP 서버 키 목록"""

    evidence_repository: Annotated[list[dict[str, Any]], operator.add]
    """Sub-Agent별 증거 산출물 누적 저장소 (artifact + format 구조)"""

    rag_context: str
    """RAG 검색 결과 텍스트 (전략/계획 수립 시 프롬프트에 주입)"""

    node_graph: dict[str, Any]
    """프론트엔드 DAG 시각화를 위한 노드 그래프 데이터 (nodes, edges)"""
