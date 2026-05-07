"""Sub-Agent 기본 클래스 및 ReAct loop subgraph 팩토리"""

from __future__ import annotations

import json
from dataclasses import asdict
from functools import partial
from typing import Any

import structlog
from langgraph.graph import END, START, StateGraph

from llm_provider.anthropic import AnthropicProvider
from llm_provider.base import BaseLLMProvider, ToolResult
from llm_provider.tool_converter import mcp_tools_to_anthropic, mcp_tools_to_openai
from constants import DEFAULT_MAX_ITERATIONS
from mcp_client.client import MCPClientManager
from state.messages import TaskResult as TaskResultType
from state.sub_agent import SubAgentState

logger = structlog.get_logger()


async def sub_agent_llm_node(
    state: SubAgentState,
    *,
    llm: BaseLLMProvider,
    mcp: MCPClientManager,
    system_prompt: str,
) -> dict[str, Any]:
    """Sub-Agent LLM 호출 노드

    할당된 task의 목적과 컨텍스트를 바탕으로 LLM이
    텍스트 응답 또는 도구 호출을 생성

    Args:
        state: Sub-Agent 상태
        llm: LLM 프로바이더
        mcp: 이 Sub-Agent 전용 MCP 클라이언트
        system_prompt: 에이전트별 시스템 프롬프트
    """
    tools = await mcp.list_tools()
    if isinstance(llm, AnthropicProvider):
        tool_params = mcp_tools_to_anthropic(tools)
    else:
        tool_params = mcp_tools_to_openai(tools)

    response = await llm.chat(
        messages=state["messages"],
        tools=tool_params if tool_params else None,
        system=system_prompt,
    )

    assistant_message: dict[str, Any] = {
        "role": "assistant",
        "content": response.content,
    }

    if response.tool_calls:
        if isinstance(llm, AnthropicProvider):
            serialized = [asdict(tc) for tc in response.tool_calls]
        else:
            serialized = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in response.tool_calls
            ]
        assistant_message["tool_calls"] = serialized
        return {
            "messages": [assistant_message],
            "pending_tool_calls": [asdict(tc) for tc in response.tool_calls],
        }

    return {
        "messages": [assistant_message],
        "pending_tool_calls": [],
    }


async def sub_agent_tool_node(
    state: SubAgentState,
    *,
    llm: BaseLLMProvider,
    mcp: MCPClientManager,
) -> dict[str, Any]:
    """Sub-Agent 도구 실행 노드

    pending_tool_calls의 각 호출을 MCP를 통해 실행하고
    결과를 메시지 히스토리에 추가

    Args:
        state: Sub-Agent 상태
        llm: LLM 프로바이더 (결과 포맷팅용)
        mcp: 이 Sub-Agent 전용 MCP 클라이언트
    """
    results: list[ToolResult] = []

    for tc in state["pending_tool_calls"]:
        try:
            call_result = await mcp.call_tool(tc["name"], tc.get("arguments"))
            content = mcp.get_tool_result_text(call_result)
            is_error = bool(call_result.isError)
        except Exception as exc:
            logger.error("sub_agent_tool_failed", tool=tc["name"], error=str(exc))
            content = f"Error: {exc}"
            is_error = True

        results.append(
            ToolResult(
                tool_call_id=tc["id"],
                content=content,
                is_error=is_error,
            )
        )

    if isinstance(llm, AnthropicProvider):
        tool_messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [llm.format_tool_result(r) for r in results],
            }
        ]
    else:
        tool_messages = [llm.format_tool_result(r) for r in results]

    output_chunks = [r.content for r in results if not r.is_error]

    return {
        "messages": tool_messages,
        "pending_tool_calls": [],
        "iteration_count": state["iteration_count"] + 1,
        "output_chunks": state["output_chunks"] + output_chunks,
    }


def _is_error_chunk(chunk: str) -> bool:
    """도구 출력 청크가 에러 응답인지 판별

    MCP 도구가 isError=false로 반환하지만 실제로는 에러인 경우를 탐지
    """
    if "Error:" in chunk:
        return True

    try:
        import json as _json
        data = _json.loads(chunk)
        if isinstance(data, dict) and "error" in data:
            return True
    except (ValueError, TypeError):
        pass

    return False


async def sub_agent_finalize_node(
    state: SubAgentState,
) -> dict[str, Any]:
    """Sub-Agent 실행 완료 후 TaskResult 생성

    LLM의 최종 분석 메시지를 우선 사용하고,
    도구 출력은 보조 데이터로 활용
    """
    task = state["task"]
    chunks = state["output_chunks"]

    last_content = ""
    for msg in reversed(state["messages"]):
        if msg.get("role") == "assistant" and msg.get("content"):
            content = msg["content"]
            if isinstance(content, str) and content.strip():
                last_content = content
                break

    valid_chunks = [c for c in chunks if not _is_error_chunk(c)]
    error_chunks = [c for c in chunks if _is_error_chunk(c)]

    if last_content:
        output = last_content
    elif valid_chunks:
        output = "\n---\n".join(valid_chunks)
    else:
        output = "\n---\n".join(chunks) if chunks else ""

    has_error = bool(error_chunks) and not valid_chunks and not last_content

    result: TaskResultType = {
        "task_id": task["task_id"],
        "agent_name": task["agent_name"],
        "status": "error" if has_error else "success",
        "output": output,
        "raw_output_ref": "",
        "artifacts": [],
        "follow_up": None,
    }

    return {"result": result}


def _should_continue(state: SubAgentState) -> str:
    """도구 호출이 있고 반복 제한 미도달이면 tools, 아니면 finalize"""
    if (
        state["pending_tool_calls"]
        and state["iteration_count"] < state["max_iterations"]
    ):
        return "tools"
    return "finalize"


def build_sub_agent_graph(
    llm: BaseLLMProvider,
    mcp: MCPClientManager,
    system_prompt: str,
) -> Any:
    """Sub-Agent ReAct loop subgraph 빌드

    그래프 토폴로지:
        START → llm → [_should_continue] → tools → llm → ... → finalize → END

    각 Sub-Agent는 이 팩토리로 고유한 system_prompt와 MCP 연결을 주입받아
    독립된 subgraph를 구성

    Args:
        llm: LLM 프로바이더
        mcp: 이 Sub-Agent 전용 MCP 클라이언트 매니저
        system_prompt: 에이전트별 시스템 프롬프트

    Returns:
        컴파일된 LangGraph subgraph
    """
    graph = StateGraph(SubAgentState)

    graph.add_node(
        "llm",
        partial(sub_agent_llm_node, llm=llm, mcp=mcp, system_prompt=system_prompt),
    )
    graph.add_node(
        "tools",
        partial(sub_agent_tool_node, llm=llm, mcp=mcp),
    )
    graph.add_node("finalize", sub_agent_finalize_node)

    graph.add_edge(START, "llm")
    graph.add_conditional_edges(
        "llm",
        _should_continue,
        {"tools": "tools", "finalize": "finalize"},
    )
    graph.add_edge("tools", "llm")
    graph.add_edge("finalize", END)

    return graph.compile()


def create_sub_agent_state(
    task: dict[str, Any],
    tools: dict[str, Any] | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> SubAgentState:
    """TaskAssignment로부터 Sub-Agent 초기 상태 생성

    Manager의 routing_node가 호출하여 subgraph 입력 상태를 구성

    Args:
        task: TaskAssignment 딕셔너리
        tools: 이 Sub-Agent가 사용할 MCP 도구 명세
        max_iterations: 최대 ReAct 루프 반복 횟수
    """
    step = task.get("step", {})
    context = task.get("context", "")
    purpose = step.get("purpose", "")
    hints = step.get("hints", "")
    artifacts = step.get("artifacts", [])
    disk_image_path = task.get("disk_image_path", "")

    parts = [f"작업: {purpose}"]
    if artifacts:
        parts.append(f"분석 대상 아티팩트: {', '.join(artifacts)}")
    if hints:
        parts.append(f"힌트: {hints}")
    if disk_image_path:
        parts.append(f"디스크 이미지 경로: {disk_image_path}")
    if context:
        parts.append(f"이전 단계 결과:\n{context}")

    initial_message = "\n".join(parts)

    return SubAgentState(
        messages=[{"role": "user", "content": initial_message}],
        task=task,
        tools=tools or {},
        pending_tool_calls=[],
        iteration_count=0,
        max_iterations=max_iterations,
        output_chunks=[],
        result=None,
        dfxml_fragment="",
    )
