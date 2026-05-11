"""Dissect Sub-Agent subgraph 빌드

base.build_sub_agent_graph 팩토리를 기반으로
Dissect 전용 tool_node(output summarizer 연동)와
DFXML 프래그먼트 생성 finalize_node를 주입
"""

from __future__ import annotations

import structlog
from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph

from agents.base import (
    DEFAULT_MAX_ITERATIONS,
    create_sub_agent_state,
    sub_agent_finalize_node,
    sub_agent_llm_node,
)
from agents.dissect.nodes import dissect_tool_node
from prompts.dissect import build_dfxml_fragment_prompt, build_dissect_prompt, format_tool_docs
from llm_provider.base import BaseLLMProvider
from mcp_client.client import MCPClientManager
from state.sub_agent import SubAgentState

logger = structlog.get_logger()


FOLLOWUP_MARKER = "[FOLLOWUP_NEEDED]"


def _parse_followup(output: str) -> tuple[str, dict[str, Any] | None]:
    """출력에서 [FOLLOWUP_NEEDED] 마커를 파싱하여 분리

    Args:
        output: Sub-Agent의 최종 출력 텍스트

    Returns:
        (마커 제거된 출력, follow_up dict 또는 None)
    """
    if FOLLOWUP_MARKER not in output:
        return output, None

    parts = output.split(FOLLOWUP_MARKER, 1)
    clean_output = parts[0].strip()
    followup_text = parts[1].strip()

    follow_up: dict[str, Any] = {"reason": "", "suggested_step": {}}
    for line in followup_text.splitlines():
        line = line.strip()
        if line.startswith("이유:"):
            follow_up["reason"] = line[3:].strip()
        elif line.startswith("목적:"):
            follow_up["suggested_step"]["purpose"] = line[3:].strip()
        elif line.startswith("힌트:"):
            follow_up["suggested_step"]["hints"] = line[3:].strip()

    if not follow_up["reason"]:
        return output, None

    follow_up["suggested_step"].setdefault("name", f"추가 조사: {follow_up['reason'][:30]}")
    return clean_output, follow_up


async def dissect_finalize_node(
    state: SubAgentState,
    *,
    llm: BaseLLMProvider,
) -> dict[str, Any]:
    """Dissect Sub-Agent finalize 노드

    base finalize로 TaskResult를 생성한 후,
    follow-up 마커를 파싱하고 DFXML 프래그먼트를 생성

    Args:
        state: Sub-Agent 상태
        llm: LLM 프로바이더 (DFXML 생성용)
    """
    base_updates = await sub_agent_finalize_node(state)

    result = base_updates.get("result")
    if not result or result.get("status") == "error":
        return {**base_updates, "dfxml_fragment": ""}

    task = state["task"]
    purpose = task.get("step", {}).get("purpose", "")
    output = result.get("output", "")

    clean_output, follow_up = _parse_followup(output)
    if follow_up:
        follow_up["suggested_step"].setdefault(
            "mcp_server", task.get("agent_name", "dissect")
        )
    result["output"] = clean_output
    result["follow_up"] = follow_up

    dfxml_fragment = ""
    if clean_output.strip():
        try:
            response = await llm.chat(
                messages=[{"role": "user", "content": "분석 결과를 DFXML 프래그먼트로 변환해주세요."}],
                tools=None,
                system=build_dfxml_fragment_prompt(
                    agent_name=task.get("agent_name", "dissect"),
                    task_purpose=purpose,
                    analysis_output=clean_output,
                ),
            )
            dfxml_fragment = response.content if isinstance(response.content, str) else ""
            logger.info("dfxml_fragment_generated", task_id=task.get("task_id"), length=len(dfxml_fragment))
        except Exception as exc:
            logger.warning("dfxml_fragment_failed", error=str(exc))

    return {**base_updates, "dfxml_fragment": dfxml_fragment}


def _should_continue(state: SubAgentState) -> str:
    """도구 호출이 있고 반복 제한 미도달이면 tools, 아니면 finalize"""
    if (
        state["pending_tool_calls"]
        and state["iteration_count"] < state["max_iterations"]
    ):
        return "tools"
    return "finalize"


def build_dissect_graph(
    llm: BaseLLMProvider,
    mcp: MCPClientManager,
    purpose: str = "",
    available_plugins: str = "",
) -> Any:
    """Dissect Sub-Agent subgraph 빌드

    그래프 토폴로지:
        START → llm → [_should_continue] → tools → llm → ... → finalize → END

    base 팩토리와 차이점:
        - tool_node에 output summarizer가 연동된 dissect_tool_node 사용
        - MCP 도구 스펙에서 동적 생성된 프롬프트 적용

    Args:
        llm: LLM 프로바이더
        mcp: Dissect MCP 서버 전용 클라이언트 매니저
        purpose: 현재 작업 목적 (프롬프트에 주입)
        available_plugins: 사전 조회된 아티팩트 플러그인 목록

    Returns:
        컴파일된 LangGraph subgraph
    """
    tool_docs = ""
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            tools = mcp._tool_cache.get_all() or {}
        else:
            tools = loop.run_until_complete(mcp.list_tools())
        tool_docs = format_tool_docs(tools)
    except Exception:
        logger.warning("tool_docs_generation_failed")

    system_prompt = build_dissect_prompt(purpose, available_plugins, tool_docs=tool_docs)

    graph = StateGraph(SubAgentState)

    graph.add_node(
        "llm",
        partial(sub_agent_llm_node, llm=llm, mcp=mcp, system_prompt=system_prompt),
    )
    graph.add_node(
        "tools",
        partial(dissect_tool_node, llm=llm, mcp=mcp),
    )
    graph.add_node(
        "finalize",
        partial(dissect_finalize_node, llm=llm),
    )

    graph.add_edge(START, "llm")
    graph.add_conditional_edges(
        "llm",
        _should_continue,
        {"tools": "tools", "finalize": "finalize"},
    )
    graph.add_edge("tools", "llm")
    graph.add_edge("finalize", END)

    return graph.compile()


def create_dissect_state(
    task: dict[str, Any],
    tools: dict[str, Any] | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> SubAgentState:
    """Dissect Sub-Agent 초기 상태 생성

    Args:
        task: TaskAssignment 딕셔너리
        tools: Dissect MCP 도구 명세
        max_iterations: 최대 ReAct 루프 반복 횟수
    """
    return create_sub_agent_state(task, tools, max_iterations)
