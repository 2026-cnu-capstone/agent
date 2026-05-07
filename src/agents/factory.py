"""Sub-Agent 팩토리 — MCP 서버명 기반 동적 에이전트 생성

MCP 서버명 또는 agent_mapping 설정을 기반으로
전용 그래프 빌더가 등록된 서버는 전용 그래프를,
미등록 서버는 범용 ReAct 그래프를 동적 생성
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

import structlog

from agents.base import build_sub_agent_graph, create_sub_agent_state
from llm_provider.base import BaseLLMProvider
from mcp_client.client import MCPClientManager
from state.sub_agent import SubAgentState

logger = structlog.get_logger()

GraphBuilder = Callable[
    [BaseLLMProvider, MCPClientManager, str, str],
    Any,
]
"""(llm, mcp, purpose, extra_context) → 컴파일된 subgraph"""

StateBuilder = Callable[
    [dict[str, Any]],
    SubAgentState,
]
"""(task) → SubAgentState"""


@dataclass
class SubAgentConfig:
    """서버 전용 Sub-Agent 구성

    Attributes:
        build_graph: 전용 subgraph 빌더 함수
        build_state: 전용 초기 상태 빌더 (None이면 범용 사용)
        prefetch_tool: 실행 전 사전 호출할 도구 키 (예: "dissect__list_artifact_plugins")
    """

    build_graph: GraphBuilder
    build_state: StateBuilder | None = None
    prefetch_tool: str | None = None


@dataclass
class AgentRegistry:
    """MCP 서버명 → SubAgentConfig 매핑 레지스트리

    Attributes:
        _configs: 서버별 전용 설정
        _agent_mapping: config.mcp.agent_mapping에서 로드된 서버-에이전트 매핑
    """

    _configs: dict[str, SubAgentConfig] = field(default_factory=dict)
    _agent_mapping: dict[str, list[str]] = field(default_factory=dict)

    def register(self, server_name: str, config: SubAgentConfig) -> None:
        """전용 Sub-Agent 설정 등록

        Args:
            server_name: MCP 서버명
            config: 전용 Sub-Agent 구성
        """
        self._configs[server_name] = config
        logger.info("agent_registered", server=server_name)

    def set_agent_mapping(self, mapping: dict[str, list[str]]) -> None:
        """config.mcp.agent_mapping 로드

        Args:
            mapping: 에이전트명 → MCP 서버명 목록
        """
        self._agent_mapping = dict(mapping)

    def resolve_server(self, agent_name: str) -> str:
        """에이전트명을 실제 MCP 서버명으로 해석

        agent_mapping에 매핑이 있으면 첫 번째 서버를 반환하고,
        없으면 에이전트명 == 서버명으로 간주

        Args:
            agent_name: 계획 단계에서 추출된 에이전트명
        """
        servers = self._agent_mapping.get(agent_name)
        if servers:
            return servers[0]
        return agent_name

    def get_config(self, server_name: str) -> SubAgentConfig | None:
        """서버명으로 전용 설정 조회

        Args:
            server_name: MCP 서버명
        """
        return self._configs.get(server_name)

    async def prefetch(
        self, server_name: str, mcp: MCPClientManager
    ) -> str:
        """서버 전용 사전 데이터 조회

        SubAgentConfig.prefetch_tool이 설정된 경우 해당 도구를 호출하여
        결과 텍스트를 반환. 미설정이면 빈 문자열 반환.

        Args:
            server_name: MCP 서버명
            mcp: MCP 클라이언트 매니저
        """
        config = self._configs.get(server_name)
        if not config or not config.prefetch_tool:
            return ""
        try:
            result = await mcp.call_tool(config.prefetch_tool, {})
            return mcp.get_tool_result_text(result)
        except Exception as exc:
            logger.warning("prefetch_failed", server=server_name, error=str(exc))
            return ""

    def build_graph(
        self,
        server_name: str,
        llm: BaseLLMProvider,
        mcp: MCPClientManager,
        purpose: str = "",
        extra_context: str = "",
    ) -> Any:
        """서버명 기반 subgraph 동적 생성

        전용 설정이 있으면 전용 빌더 사용,
        없으면 범용 ReAct 그래프 폴백

        Args:
            server_name: MCP 서버명
            llm: LLM 프로바이더
            mcp: MCP 클라이언트 매니저
            purpose: 현재 작업 목적
            extra_context: 사전 조회 데이터 등 추가 컨텍스트
        """
        config = self._configs.get(server_name)
        if config:
            return config.build_graph(llm, mcp, purpose, extra_context)

        system_prompt = (
            f"당신은 {server_name} MCP 서버의 도구를 사용하여 "
            f"디지털 포렌식 분석을 수행하는 에이전트입니다.\n\n"
            f"작업 목적: {purpose}"
        )
        return build_sub_agent_graph(llm, mcp, system_prompt)

    def build_state(
        self, server_name: str, task: dict[str, Any]
    ) -> SubAgentState:
        """서버명 기반 초기 상태 생성

        전용 상태 빌더가 등록되어 있으면 사용,
        없으면 범용 create_sub_agent_state 폴백

        Args:
            server_name: MCP 서버명
            task: TaskAssignment 딕셔너리
        """
        config = self._configs.get(server_name)
        if config and config.build_state:
            return config.build_state(task)
        return create_sub_agent_state(task)


def create_default_registry() -> AgentRegistry:
    """Dissect 전용 설정이 등록된 기본 레지스트리 생성"""
    from agents.dissect.graph import build_dissect_graph, create_dissect_state

    registry = AgentRegistry()

    def _dissect_graph_adapter(
        llm: BaseLLMProvider,
        mcp: MCPClientManager,
        purpose: str,
        extra_context: str,
    ) -> Any:
        """Dissect 그래프 빌더를 GraphBuilder 시그니처에 맞게 변환"""
        return build_dissect_graph(
            llm, mcp, purpose=purpose, available_plugins=extra_context
        )

    registry.register(
        "dissect",
        SubAgentConfig(
            build_graph=_dissect_graph_adapter,
            build_state=create_dissect_state,
            prefetch_tool="dissect__list_artifact_plugins",
        ),
    )

    return registry
