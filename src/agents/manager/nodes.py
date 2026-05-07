"""Manager Agent 노드 — 전략, 계획, 라우팅, 집계, HITL 게이트"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import structlog

from prompts.manager import build_planning_prompt, build_strategy_prompt
from llm_provider.base import BaseLLMProvider
from mcp_client.client import MCPClientManager
from state.manager import ManagerState
from state.messages import TaskAssignment

logger = structlog.get_logger()


async def strategy_node(
    state: ManagerState,
    *,
    llm: BaseLLMProvider,
    system_profile: str = "",
) -> dict[str, Any]:
    """사용자 사건 입력을 바탕으로 분석 전략 수립

    Args:
        state: Manager 상태
        llm: LLM 프로바이더
        system_profile: 디스크 이미지 시스템 프로필 (OS 정보 등)
    """
    disk_image_format = state.get("disk_image_format", "")
    response = await llm.chat(
        messages=state["messages"],
        tools=None,
        system=build_strategy_prompt(
            disk_image_format=disk_image_format,
            system_profile=system_profile,
        ),
    )
    strategy_text = response.content if isinstance(response.content, str) else ""
    logger.info("strategy_created", length=len(strategy_text))

    return {
        "messages": [{"role": "assistant", "content": response.content}],
        "analysis_strategy": strategy_text,
        "phase": "planning",
    }


async def planning_node(
    state: ManagerState,
    *,
    llm: BaseLLMProvider,
    mcp: MCPClientManager,
) -> dict[str, Any]:
    """수립된 전략을 바탕으로 세부 실행 계획 수립

    Args:
        state: Manager 상태
        llm: LLM 프로바이더
        mcp: MCP 클라이언트 매니저 (도구 목록 조회용)
    """
    server_names = mcp.connected_servers
    server_list = "\n".join(f"- {name}" for name in server_names) if server_names else ""

    messages = list(state["messages"])
    if messages and messages[-1].get("role") == "assistant":
        messages.append({"role": "user", "content": "위 전략을 바탕으로 세부 실행 계획을 수립해주세요."})

    response = await llm.chat(
        messages=messages,
        tools=None,
        system=build_planning_prompt(
            state.get("analysis_strategy", ""),
            server_list,
        ),
    )
    plan_text = response.content if isinstance(response.content, str) else ""
    plan_steps = _parse_plan_steps(plan_text)
    logger.info("plan_created", length=len(plan_text), steps=len(plan_steps))

    return {
        "messages": [{"role": "assistant", "content": response.content}],
        "analysis_plan": plan_text,
        "plan_steps": plan_steps,
        "phase": "execution",
    }


def routing_node(state: ManagerState) -> dict[str, Any]:
    """plan_steps를 기반으로 TaskAssignment 생성 및 큐에 적재

    각 step의 tool 필드에서 MCP 서버명(__ 앞부분)을 추출하여
    해당 Sub-Agent에 할당
    """
    plan_steps = state.get("plan_steps", [])
    current_idx = state.get("current_step_index", 0)
    task_results = state.get("task_results", [])
    disk_image_path = state.get("disk_image_path") or ""

    context = ""
    if task_results:
        context = "\n".join(
            f"[{r['agent_name']}] {r['output'][:500]}"
            for r in task_results[-3:]
        )

    task_queue: list[TaskAssignment] = []
    for step in plan_steps[current_idx:]:
        tool_key = step.get("tool", "none")
        agent_name = _extract_agent_name(tool_key)

        assignment: TaskAssignment = {
            "task_id": str(uuid.uuid4())[:8],
            "agent_name": agent_name,
            "step": step,
            "context": context,
            "disk_image_path": disk_image_path,
        }
        task_queue.append(assignment)

    logger.info("tasks_routed", count=len(task_queue))

    return {
        "task_queue": task_queue,
        "active_agents": list({a["agent_name"] for a in task_queue}),
    }


def aggregation_node(state: ManagerState) -> dict[str, Any]:
    """TaskResult 수집 완료 후 다음 단계 결정

    모든 작업이 완료되면 phase를 report로 전환
    """
    task_results = state.get("task_results", [])
    task_queue = state.get("task_queue", [])

    completed = len(task_results)
    total = len(task_queue)

    logger.info("aggregation", completed=completed, total=total)

    if completed >= total:
        return {
            "phase": "report",
            "active_agents": [],
            "current_step_index": total,
        }

    return {
        "current_step_index": completed,
    }


def hitl_strategy_gate(state: ManagerState) -> dict[str, Any]:
    """전략 수립 후 HITL 승인 대기 상태 설정"""
    return {
        "hitl_pending": True,
        "hitl_type": "strategy",
    }


def hitl_plan_gate(state: ManagerState) -> dict[str, Any]:
    """계획 수립 후 HITL 승인 대기 상태 설정"""
    return {
        "hitl_pending": True,
        "hitl_type": "plan",
    }


def hitl_result_gate(state: ManagerState) -> dict[str, Any]:
    """분석 결과 확인 후 HITL 승인 대기 상태 설정"""
    return {
        "hitl_pending": True,
        "hitl_type": "result",
    }


def _parse_plan_steps(plan_text: str) -> list[dict[str, Any]]:
    """계획 텍스트에서 JSON 단계 목록 파싱

    ```json ... ``` 코드 블록에서 steps 배열을 추출.
    중첩 중괄호를 처리하기 위해 마지막 } 기준으로 매칭.
    JSON 블록이 없으면 빈 목록 반환.
    """
    match = re.search(r"```json\s*(\{[\s\S]*\})\s*```", plan_text)
    if not match:
        json_start = plan_text.find('{"steps"')
        if json_start == -1:
            logger.warning("plan_steps_json_not_found")
            return []
        json_end = plan_text.rfind("}") + 1
        raw_json = plan_text[json_start:json_end]
    else:
        raw_json = match.group(1)

    try:
        data = json.loads(raw_json)
        return data.get("steps", [])
    except json.JSONDecodeError:
        try:
            repaired = raw_json.rstrip()
            if not repaired.endswith("}"):
                repaired += "]}"
            elif repaired.endswith("},") or repaired.endswith("}"):
                if '"steps"' in repaired and not repaired.rstrip().endswith("]}"):
                    repaired = repaired.rstrip().rstrip(",") + "]}"
            data = json.loads(repaired)
            return data.get("steps", [])
        except json.JSONDecodeError:
            logger.warning("plan_steps_json_parse_failed", raw_length=len(raw_json))
            return []


def _extract_agent_name(step: dict[str, Any]) -> str:
    """계획 단계에서 담당 에이전트 이름 추출

    plan step의 mcp_server 필드를 우선 사용하고,
    하위 호환을 위해 tool 필드도 폴백으로 지원

    Args:
        step: 계획 단계 딕셔너리
    """
    server = step.get("mcp_server", "")
    if server and server.lower() != "none":
        return server

    tool_key = step.get("tool", "")
    if "__" in tool_key:
        return tool_key.split("__", 1)[0]
    if not tool_key or tool_key.lower() == "none":
        return "manual"
    return tool_key
