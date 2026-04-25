"""LangGraph 기반 포렌식 에이전트 그래프 정의"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from functools import partial
from typing import Any

import structlog
from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncEngine

from database.engine import get_session
from database.repository import create_case
from graph.state import AgentState
from llm_provider.anthropic import AnthropicProvider
from llm_provider.base import BaseLLMProvider, ToolResult
from llm_provider.tool_converter import (
    format_tool_summaries,
    mcp_tools_to_anthropic,
    mcp_tools_to_openai,
)
from mcp_client.client import MCPClientManager
from prompts.system import (
    build_planning_prompt,
    build_step_mapper_prompt,
    build_strategy_prompt,
    build_system_prompt,
)

logger = structlog.get_logger()

MAX_ITERATIONS = 20


async def strategy_node(
    state: AgentState,
    *,
    llm: BaseLLMProvider,
) -> dict[str, Any]:
    """사용자 사건 입력을 바탕으로 분석 전략 수립

    무엇을 조사할지, 어떤 방향으로 접근할지 고수준 전략 도출
    수립된 전략은 state["analysis_strategy"]에 저장됨
    """
    response = await llm.chat(
        messages=state["messages"],
        tools=None,
        system=build_strategy_prompt(),
    )

    strategy_text = response.content if isinstance(response.content, str) else ""
    logger.info("analysis_strategy_created", length=len(strategy_text))

    return {
        "messages": [{"role": "assistant", "content": response.content}],
        "analysis_strategy": strategy_text,
        "pending_tool_calls": [],
        "phase": "planning",
    }


async def planning_node(
    state: AgentState,
    *,
    llm: BaseLLMProvider,
) -> dict[str, Any]:
    """수립된 전략을 바탕으로 세부 실행 계획 수립

    strategy_node의 결과를 입력으로 받아 단계별 실행 계획 생성
    수립된 계획은 state["analysis_plan"]에 저장됨
    """
    response = await llm.chat(
        messages=state["messages"],
        tools=None,
        system=build_planning_prompt(
            state.get("analysis_strategy", ""),
            format_tool_summaries(state.get("tools") or {}),
        ),
    )

    plan_text = response.content if isinstance(response.content, str) else ""
    logger.info("analysis_plan_created", length=len(plan_text))

    return {
        "messages": [{"role": "assistant", "content": response.content}],
        "analysis_plan": plan_text,
        "pending_tool_calls": [],
        "phase": "execution",
    }


def _serialize_tool_calls(
    tool_calls: list[Any], llm: BaseLLMProvider
) -> list[dict[str, Any]]:
    """프로바이더별 tool_calls 메시지 직렬화

    OpenAI는 type/function 중첩 구조, Anthropic은 플랫 구조

    Args:
        tool_calls: ToolCall 객체 목록
        llm: LLM 프로바이더 인스턴스

    Returns:
        직렬화된 tool_calls 목록
    """
    if isinstance(llm, AnthropicProvider):
        return [asdict(tc) for tc in tool_calls]

    return [
        {
            "id": tc.id,
            "type": "function",
            "function": {
                "name": tc.name,
                "arguments": json.dumps(tc.arguments),
            },
        }
        for tc in tool_calls
    ]


async def llm_node(
    state: AgentState,
    *,
    llm: BaseLLMProvider,
    mcp: MCPClientManager,
) -> dict[str, Any]:
    """LLM 호출을 통한 응답 또는 도구 호출 생성"""
    tools = await mcp.list_tools()
    if isinstance(llm, AnthropicProvider):
        tool_params = mcp_tools_to_anthropic(tools)
    else:
        tool_params = mcp_tools_to_openai(tools)
    system = build_system_prompt(format_tool_summaries(tools), state.get("analysis_plan", ""))

    response = await llm.chat(
        messages=state["messages"],
        tools=tool_params if tool_params else None,
        system=system,
    )

    assistant_message: dict[str, Any] = {
        "role": "assistant",
        "content": response.content,
    }

    if response.tool_calls:
        serialized = _serialize_tool_calls(response.tool_calls, llm)
        assistant_message["tool_calls"] = serialized
        return {
            "messages": [assistant_message],
            "pending_tool_calls": [asdict(tc) for tc in response.tool_calls],
        }

    return {
        "messages": [assistant_message],
        "pending_tool_calls": [],
    }


async def tool_node(
    state: AgentState,
    *,
    llm: BaseLLMProvider,
    mcp: MCPClientManager,
) -> dict[str, Any]:
    """대기 중인 도구 호출의 MCP 실행"""
    results: list[ToolResult] = []

    for tc in state["pending_tool_calls"]:
        try:
            call_result = await mcp.call_tool(tc["name"], tc.get("arguments"))
            content = mcp.get_tool_result_text(call_result)
            is_error = bool(call_result.isError)
        except Exception as exc:
            logger.error("tool_execution_failed", tool=tc["name"], error=str(exc))
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

    return {
        "messages": tool_messages,
        "pending_tool_calls": [],
        "iteration_count": state["iteration_count"] + 1,
    }


async def intake_node(
    state: AgentState,
    *,
    db_engine: AsyncEngine,
) -> dict[str, Any]:
    """사건 정보 수신 및 디스크 이미지 등록

    state에 사전 설정된 디스크 이미지 경로와 사용자 프롬프트를
    DB에 저장하고 분석 단계로 전환

    Args:
        state: 현재 에이전트 상태
        db_engine: SQLAlchemy 비동기 엔진

    Returns:
        상태 업데이트 딕셔너리
    """
    last_message = state["messages"][-1]
    user_text = last_message.get("content", "")
    image_path = state.get("disk_image_path") or ""
    fmt = state.get("disk_image_format") or ""

    if not image_path or not fmt:
        logger.warning("no_image_path_in_state")
        return {
            "messages": [
                {
                    "role": "assistant",
                    "content": "디스크 이미지 경로가 설정되지 않았습니다.",
                }
            ],
        }

    async with get_session(db_engine) as session:
        case = await create_case(
            session=session,
            user_prompt=user_text,
            disk_image_path=image_path,
            disk_image_format=fmt,
        )
        case_id = case.id

    logger.info(
        "case_created",
        case_id=case_id,
        image_path=image_path,
        image_format=fmt,
    )

    return {
        "messages": [
            {
                "role": "assistant",
                "content": f"케이스가 등록되었습니다 (ID: {case_id}). "
                f"디스크 이미지: {image_path} (형식: {fmt.upper()}). "
                f"분석을 시작합니다.",
            }
        ],
        "case_id": case_id,
        "phase": "analysis",
    }


def intake_router(state: AgentState) -> str:
    """intake 단계 라우팅

    디스크 이미지 검증 성공 시 분석 단계로, 실패 시 종료(사용자 재입력 대기)

    Args:
        state: 현재 에이전트 상태

    Returns:
        다음 노드 이름 ("llm" 또는 "end")
    """
    if state.get("phase") == "analysis" and state.get("case_id") is not None:
        return "llm"
    return "end"


def should_continue(state: AgentState) -> str:
    """도구 호출이 있으면 tool_node로, 없으면 종료"""
    if state["pending_tool_calls"] and state["iteration_count"] < MAX_ITERATIONS:
        return "tools"
    return "end"


def build_strategy_graph(llm: BaseLLMProvider) -> Any:
    """전략 수립 전용 그래프 (MCP 불필요)

    START → strategy → END
    result["analysis_strategy"]에 전략 텍스트가 담겨 반환됨
    """
    graph = StateGraph(AgentState)
    graph.add_node("strategy", partial(strategy_node, llm=llm))
    graph.add_edge(START, "strategy")
    graph.add_edge("strategy", END)
    return graph.compile()


def build_planning_graph(llm: BaseLLMProvider) -> Any:
    """계획 수립 전용 그래프 (MCP 불필요)

    START → planning → END
    state["analysis_strategy"]를 바탕으로 세부 계획 생성
    result["analysis_plan"]에 계획 텍스트가 담겨 반환됨
    """
    graph = StateGraph(AgentState)
    graph.add_node("planning", partial(planning_node, llm=llm))
    graph.add_edge(START, "planning")
    graph.add_edge("planning", END)
    return graph.compile()


def build_agent_graph(
    llm: BaseLLMProvider,
    mcp: MCPClientManager,
    db_engine: AsyncEngine,
) -> Any:
    """포렌식 에이전트 LangGraph 구성 및 컴파일

    그래프 토폴로지:
        START → intake → [intake_router] → END (검증 실패)
                                         → llm (검증 성공)
        llm → [should_continue] → tools → llm → ... → END

    Args:
        llm: LLM 프로바이더 인스턴스
        mcp: MCP 클라이언트 매니저
        db_engine: SQLAlchemy 비동기 엔진
    """
    graph = StateGraph(AgentState)

    graph.add_node("intake", partial(intake_node, db_engine=db_engine))
    graph.add_node("llm", partial(llm_node, llm=llm, mcp=mcp))
    graph.add_node("tools", partial(tool_node, llm=llm, mcp=mcp))

    graph.add_edge(START, "intake")
    graph.add_conditional_edges(
        "intake",
        intake_router,
        {"llm": "llm", "end": END},
    )
    graph.add_conditional_edges(
        "llm",
        should_continue,
        {"tools": "tools", "end": END},
    )
    graph.add_edge("tools", "llm")

    return graph.compile()


def create_initial_state(
    user_message: str,
    disk_image_path: str | None = None,
    disk_image_format: str | None = None,
    analysis_plan: str = "",
) -> AgentState:
    """사용자 메시지 기반 초기 에이전트 상태 생성

    Args:
        user_message: 사용자 입력 메시지
        disk_image_path: 사전 검증된 디스크 이미지 경로
        disk_image_format: 디스크 이미지 형식 (e01, dd, raw)
        analysis_plan: 확정된 분석 계획 텍스트 (실행 단계에서 시스템 프롬프트에 주입)
    """
    return AgentState(
        messages=[{"role": "user", "content": user_message}],
        tools={},
        pending_tool_calls=[],
        iteration_count=0,
        phase="intake",
        case_id=None,
        disk_image_path=disk_image_path,
        disk_image_format=disk_image_format,
        analysis_strategy="",
        analysis_plan=analysis_plan,
        plan_steps=[],
        current_step_index=0,
        step_results=[],
    )


def create_planning_state(
    user_message: str,
    strategy: str,
    tools: dict | None = None,
) -> AgentState:
    """전략이 확정된 후 계획 수립용 상태 생성"""
    return AgentState(
        messages=[{"role": "user", "content": user_message}],
        tools=tools or {},
        pending_tool_calls=[],
        iteration_count=0,
        phase="planning",
        case_id=None,
        disk_image_path=None,
        disk_image_format=None,
        analysis_strategy=strategy,
        analysis_plan="",
        plan_steps=[],
        current_step_index=0,
        step_results=[],
    )


# ── 파이프라인 실행 ────────────────────────────────────────


def parse_plan_steps(plan_text: str) -> list[dict[str, Any]]:
    """계획 텍스트에서 JSON 단계 목록 파싱

    build_planning_prompt이 생성한 ```json ... ``` 블록을 추출하여 파싱.
    파싱 실패 시 빈 리스트 반환.

    Returns:
        [{index, name, tool, purpose, input_hint}, ...] 형태의 단계 목록
    """
    match = re.search(r"```json\s*(\{.*?\})\s*```", plan_text, re.DOTALL)
    if not match:
        logger.warning("plan_steps_json_not_found")
        return []
    try:
        data = json.loads(match.group(1))
        return data.get("steps", [])
    except json.JSONDecodeError:
        logger.warning("plan_steps_json_parse_failed")
        return []


def _extract_json_from_text(text: str) -> dict[str, Any]:
    """LLM 응답 텍스트에서 JSON 객체 추출

    순수 JSON 응답이 아닌 경우(설명 포함)도 처리.
    추출 실패 시 빈 딕셔너리 반환.
    """
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


async def step_executor_node(
    state: AgentState,
    *,
    llm: BaseLLMProvider,
    mcp: MCPClientManager,
) -> dict[str, Any]:
    """파이프라인의 현재 단계 실행

    흐름:
        1. plan_steps[current_step_index]에서 현재 단계 정보 가져옴
        2. 도구가 'none'이면 MCP 호출 없이 수동 단계로 기록
        3. 도구가 있으면:
           a. MCP에서 도구 스키마 조회
           b. LLM(build_step_mapper_prompt)으로 이전 결과 → 도구 인자 변환
           c. MCP 도구 호출
        4. 결과를 step_results에 추가하고 current_step_index 증가
    """
    steps = state["plan_steps"]
    idx = state["current_step_index"]
    step = steps[idx]
    tool_key = step.get("tool", "none")
    previous_results = state["step_results"]

    logger.info("step_executor_start", index=idx, step_name=step.get("name"), tool=tool_key)

    if not tool_key or tool_key.lower() == "none":
        # 도구 없는 수동 단계 — LLM 호출 없이 목적만 기록
        output = f"[수동 단계] {step.get('purpose', '')}"
    else:
        # 도구 스키마 조회
        tools = await mcp.list_tools()
        tool_obj = tools.get(tool_key)
        tool_schema = (
            json.dumps(tool_obj.inputSchema, ensure_ascii=False)
            if tool_obj and hasattr(tool_obj, "inputSchema")
            else "{}"
        )

        # 이전 단계들의 계획 정보 (output_hint 포함)
        previous_steps = steps[:idx]

        # LLM으로 이전 결과 → 현재 도구 인자 매핑
        # output_hint(계획 시 정의)와 실제 출력을 함께 참조
        mapper_response = await llm.chat(
            messages=[{"role": "user", "content": "도구 호출 인자를 생성해주세요."}],
            tools=None,
            system=build_step_mapper_prompt(
                step,
                previous_steps,
                previous_results,
                tool_schema,
                disk_image_path=state.get("disk_image_path") or "",
            ),
        )
        raw = mapper_response.content if isinstance(mapper_response.content, str) else "{}"
        arguments = _extract_json_from_text(raw)
        logger.info("step_mapper_result", tool=tool_key, arguments=arguments)

        # MCP 도구 호출
        try:
            call_result = await mcp.call_tool(tool_key, arguments)
            output = mcp.get_tool_result_text(call_result)
        except Exception as exc:
            logger.error("step_tool_call_failed", tool=tool_key, error=str(exc))
            output = f"Error: {exc}"

    step_result: dict[str, Any] = {
        "step": step["index"],
        "name": step.get("name", ""),
        "tool": tool_key,
        "output": output,
    }

    return {
        "messages": [
            {
                "role": "assistant",
                "content": f"[{step['index']}단계 완료] {step.get('name', '')}: {output[:300]}",
            }
        ],
        "step_results": previous_results + [step_result],
        "current_step_index": idx + 1,
    }


def pipeline_router(state: AgentState) -> str:
    """실행할 단계가 남아 있으면 계속, 없으면 종료"""
    if state["current_step_index"] < len(state["plan_steps"]):
        return "step"
    return "end"


def build_pipeline_graph(
    llm: BaseLLMProvider,
    mcp: MCPClientManager,
    db_engine: AsyncEngine,
) -> Any:
    """파이프라인 실행 그래프

    그래프 토폴로지:
        START → intake → [intake_router] → END (검증 실패)
                                         → step (검증 성공)
        step → [pipeline_router] → step (다음 단계)
                                 → END (모든 단계 완료)

    plan_steps를 순서대로 실행하며, 각 단계마다 LLM이
    이전 결과를 현재 도구 인자로 매핑.
    """
    graph = StateGraph(AgentState)

    graph.add_node("intake", partial(intake_node, db_engine=db_engine))
    graph.add_node("step", partial(step_executor_node, llm=llm, mcp=mcp))

    graph.add_edge(START, "intake")
    graph.add_conditional_edges(
        "intake",
        intake_router,
        {"llm": "step", "end": END},
    )
    graph.add_conditional_edges(
        "step",
        pipeline_router,
        {"step": "step", "end": END},
    )

    return graph.compile()


def create_execution_state(
    user_message: str,
    analysis_plan: str,
    plan_steps: list[dict[str, Any]],
    disk_image_path: str | None = None,
    disk_image_format: str | None = None,
) -> AgentState:
    """파이프라인 실행용 초기 상태 생성

    parse_plan_steps()로 파싱된 plan_steps를 받아
    step_executor_node가 순서대로 소비할 수 있도록 설정.
    """
    return AgentState(
        messages=[{"role": "user", "content": user_message}],
        tools={},
        pending_tool_calls=[],
        iteration_count=0,
        phase="intake",
        case_id=None,
        disk_image_path=disk_image_path,
        disk_image_format=disk_image_format,
        analysis_strategy="",
        analysis_plan=analysis_plan,
        plan_steps=plan_steps,
        current_step_index=0,
        step_results=[],
    )
