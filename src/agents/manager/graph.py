"""Manager Agent 단계별 그래프 및 오케스트레이션 함수

HITL 게이트는 run_agent.py에서 명시적으로 제어하므로,
각 단계(전략, 계획, 실행, 보고서)를 독립 함수로 제공
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

from agents.factory import AgentRegistry, create_default_registry
from constants import EXECUTION_STEP_DELAY, MAX_FOLLOWUP_STEPS
from agents.manager.nodes import (
    _extract_agent_name,
    _parse_plan_steps,
    strategy_node,
    planning_node,
)
from agents.report.graph import build_report_graph, create_report_state
from llm_provider.base import BaseLLMProvider
from mcp_client.client import MCPClientManager
from rag.service import RAGService
from state.manager import ManagerState
from state.messages import TaskAssignment, TaskResult

logger = structlog.get_logger()


class ExecutionCallback(Protocol):
    """Sub-Agent 실행 진행 상황 콜백 프로토콜"""

    def on_step_start(self, step_index: int, total: int, step: dict, agent_name: str) -> None:
        """단계 시작 시 호출"""
        ...

    def on_step_done(self, step_index: int, total: int, step: dict, agent_name: str, result: TaskResult) -> None:
        """단계 완료 시 호출"""
        ...

    def on_step_skip(self, step_index: int, total: int, step: dict) -> None:
        """수동 단계 건너뛸 때 호출"""
        ...


async def run_strategy(
    state: ManagerState,
    llm: BaseLLMProvider,
    rag_service: RAGService | None = None,
) -> ManagerState:
    """전략 수립 단계 실행

    Args:
        state: Manager 상태 (system_profile 포함)
        llm: LLM 프로바이더
        rag_service: RAG 서비스 (None이면 RAG 비활성)

    Returns:
        analysis_strategy가 채워진 상태
    """
    system_profile = state.get("system_profile") or ""
    updates = await strategy_node(
        state, llm=llm, system_profile=system_profile, rag_service=rag_service
    )
    return {**state, **updates}


async def run_planning(
    state: ManagerState,
    llm: BaseLLMProvider,
    mcp: MCPClientManager,
    rag_service: RAGService | None = None,
) -> ManagerState:
    """계획 수립 단계 실행

    Args:
        state: 전략이 확정된 Manager 상태
        llm: LLM 프로바이더
        mcp: MCP 클라이언트 매니저
        rag_service: RAG 서비스 (None이면 RAG 비활성)

    Returns:
        plan_steps가 채워진 상태
    """
    updates = await planning_node(state, llm=llm, mcp=mcp, rag_service=rag_service)
    return {**state, **updates}


async def run_execution(
    state: ManagerState,
    llm: BaseLLMProvider,
    mcp: MCPClientManager,
    callback: ExecutionCallback | None = None,
    registry: AgentRegistry | None = None,
    rag_service: RAGService | None = None,
) -> ManagerState:
    """Sub-Agent 실행 단계

    plan_steps의 각 단계를 순차적으로 Sub-Agent에 동적 dispatch하고 결과 수집.
    AgentRegistry를 통해 MCP 서버명 기반으로 전용/범용 에이전트를 자동 선택.
    실행 완료 후 RAG 서비스가 활성화되어 있으면 결과를 벡터 저장소에 저장.

    Args:
        state: 계획이 확정된 Manager 상태
        llm: LLM 프로바이더
        mcp: MCP 클라이언트 매니저
        callback: 진행 상황 콜백 (None이면 무시)
        registry: Sub-Agent 레지스트리 (None이면 기본 레지스트리 사용)
        rag_service: RAG 서비스 (None이면 결과 저장 건너뜀)

    Returns:
        task_results가 채워진 상태
    """
    import asyncio
    import uuid
    from datetime import datetime, timezone

    if registry is None:
        registry = create_default_registry()

    plan_steps = list(state.get("plan_steps", []))
    disk_image_path = state.get("disk_image_path") or ""
    results: list[TaskResult] = list(state.get("task_results", []))
    evidence_repo: list[dict[str, Any]] = []
    followup_count = 0
    total = len(plan_steps)

    connected_servers = mcp.connected_servers
    default_server = connected_servers[0] if connected_servers else ""

    prefetch_cache: dict[str, str] = {}

    context = ""
    i = 0

    while i < len(plan_steps):
        step = plan_steps[i]
        if i > 0:
            await asyncio.sleep(EXECUTION_STEP_DELAY)
        agent_name = _extract_agent_name(step)
        if agent_name == "manual" and default_server:
            agent_name = default_server
        purpose = step.get("purpose", "")
        hints = step.get("hints", "")

        server_name = registry.resolve_server(agent_name)

        task: TaskAssignment = {
            "task_id": str(uuid.uuid4())[:8],
            "agent_name": agent_name,
            "step": step,
            "context": context,
            "disk_image_path": disk_image_path,
        }

        full_purpose = f"{purpose}\n힌트: {hints}" if hints else purpose

        if agent_name == "manual":
            result: TaskResult = {
                "task_id": task["task_id"],
                "agent_name": "manual",
                "status": "success",
                "output": f"[수동 단계] {purpose}",
                "raw_output_ref": "",
                "artifacts": [],
            }
            results.append(result)
            if callback:
                callback.on_step_skip(i, total, step)
            logger.info("manual_step", step=step.get("index"), purpose=purpose)
            i += 1
            continue

        if callback:
            callback.on_step_start(i, total, step, agent_name)

        if server_name not in prefetch_cache:
            prefetch_cache[server_name] = await registry.prefetch(server_name, mcp)

        logger.info(
            "sub_agent_dispatch",
            agent=agent_name,
            server=server_name,
            step=step.get("index"),
        )

        sub_graph = registry.build_graph(
            server_name, llm, mcp,
            purpose=full_purpose,
            extra_context=prefetch_cache.get(server_name, ""),
        )
        sub_state = registry.build_state(server_name, task)

        try:
            sub_result = await sub_graph.ainvoke(sub_state)
            if sub_result.get("result"):
                results.append(sub_result["result"])
                context = sub_result["result"].get("output", "")[:500]

                artifact_data = sub_result.get("dfxml_fragment", "")
                if artifact_data:
                    evidence_repo.append({
                        "task_id": task["task_id"],
                        "agent_name": agent_name,
                        "server_name": server_name,
                        "artifact": artifact_data,
                        "format": "dfxml",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
            else:
                error_result: TaskResult = {
                    "task_id": task["task_id"],
                    "agent_name": agent_name,
                    "status": "error",
                    "output": "Sub-Agent가 결과를 반환하지 않았습니다.",
                    "raw_output_ref": "",
                    "artifacts": [],
                }
                results.append(error_result)
        except Exception as exc:
            logger.error("sub_agent_failed", agent=agent_name, error=str(exc))
            error_result = {
                "task_id": task["task_id"],
                "agent_name": agent_name,
                "status": "error",
                "output": f"Error: {exc}",
                "raw_output_ref": "",
                "artifacts": [],
            }
            results.append(error_result)

        if callback:
            step_result_for_cb = dict(results[-1])
            if evidence_repo and evidence_repo[-1].get("task_id") == task["task_id"]:
                step_result_for_cb["artifact"] = evidence_repo[-1]["artifact"]
            callback.on_step_done(i, total, step, agent_name, step_result_for_cb)

        follow_up = results[-1].get("follow_up")
        if follow_up and followup_count < MAX_FOLLOWUP_STEPS:
            suggested = follow_up.get("suggested_step", {})
            new_step = {
                "index": total + followup_count + 1,
                "name": suggested.get("name", "추가 조사"),
                "mcp_server": suggested.get("mcp_server", agent_name),
                "purpose": suggested.get("purpose", follow_up.get("reason", "")),
                "artifacts": [],
                "hints": suggested.get("hints", ""),
            }
            plan_steps.append(new_step)
            total = len(plan_steps)
            followup_count += 1
            logger.info(
                "followup_step_added",
                reason=follow_up.get("reason"),
                new_total=total,
            )

        i += 1

    if rag_service and state.get("case_id"):
        results_summary = "\n".join(
            f"[{r.get('agent_name', '')}] {r.get('output', '')[:300]}"
            for r in results
            if r.get("status") == "success"
        )
        case_description = ""
        if state.get("messages"):
            case_description = state["messages"][0].get("content", "")
        try:
            await rag_service.store_case_result(
                case_id=state["case_id"],
                strategy=state.get("analysis_strategy", ""),
                plan=state.get("analysis_plan", ""),
                results_summary=results_summary,
                case_description=case_description,
            )
        except Exception as exc:
            logger.warning("rag_store_failed", error=str(exc))

    return {
        **state,
        "task_results": results,
        "evidence_repository": evidence_repo,
        "phase": "report",
    }


async def run_report(
    state: ManagerState,
    llm: BaseLLMProvider,
) -> dict[str, str]:
    """Report Agent 실행

    Args:
        state: 실행 결과가 포함된 Manager 상태
        llm: LLM 프로바이더

    Returns:
        {"summary": ..., "report": ..., "dfxml": ...}
    """
    task_results = state.get("task_results", [])
    case_description = ""
    if state.get("messages"):
        case_description = state["messages"][0].get("content", "")

    report_graph = build_report_graph(llm)
    report_state = create_report_state(
        task_results=task_results,
        case_description=case_description,
        strategy=state.get("analysis_strategy", ""),
        evidence_repository=state.get("evidence_repository", []),
    )

    result = await report_graph.ainvoke(report_state)
    return {
        "summary": result.get("summary", ""),
        "report": result.get("report", ""),
        "dfxml": result.get("dfxml", ""),
    }


def create_manager_state(
    user_message: str,
    disk_image_path: str | None = None,
    disk_image_format: str | None = None,
    system_profile: str | None = None,
) -> ManagerState:
    """Manager Agent 초기 상태 생성

    Args:
        user_message: 사용자 사건 개요 입력
        disk_image_path: 검증된 디스크 이미지 경로
        disk_image_format: 디스크 이미지 형식
        system_profile: 사전 추출된 시스템 프로필
    """
    return ManagerState(
        messages=[{"role": "user", "content": user_message}],
        phase="strategy",
        case_id=None,
        disk_image_path=disk_image_path,
        disk_image_format=disk_image_format,
        system_profile=system_profile,
        analysis_strategy="",
        analysis_plan="",
        plan_steps=[],
        current_step_index=0,
        task_queue=[],
        task_results=[],
        agent_messages=[],
        active_agents=[],
        hitl_pending=False,
        hitl_type="",
        evidence_repository=[],
        rag_context="",
    )
