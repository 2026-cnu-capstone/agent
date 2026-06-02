"""Manager Agent 단계별 그래프 및 오케스트레이션 함수

HITL 게이트는 run_agent.py에서 명시적으로 제어하므로,
각 단계(전략, 계획, 실행, 보고서)를 독립 함수로 제공
"""

from __future__ import annotations

from typing import Any, Protocol

import structlog

from agents.factory import AgentRegistry, create_default_registry
from agents.manager.nodes import (
    _extract_agent_name,
    planning_node,
    strategy_node,
)
from agents.report.graph import build_report_graph, create_report_state
from constants import EXECUTION_STEP_DELAY, MAX_FOLLOWUP_STEPS
from database.engine import get_session
from database.repository import (
    create_agent_run,
    create_analysis_session,
    create_plan_step,
    create_task_result,
    update_agent_run,
)
from llm_provider.base import BaseLLMProvider
from mcp_client.client import MCPClientManager
from node_graph import (
    add_followup_step,
    create_initial_graph,
    update_planning_done,
    update_report_done,
    update_report_started,
    update_step_done,
    update_step_started,
    update_strategy_done,
)
from prompts.report import build_dfxml_prompt
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
    new_state = {**state, **updates}
    graph = new_state.get("node_graph") or create_initial_graph()
    new_state["node_graph"] = update_strategy_done(
        graph, new_state.get("analysis_strategy", "")
    )
    return new_state


async def run_planning(
    state: ManagerState,
    llm: BaseLLMProvider,
    mcp: MCPClientManager,
) -> ManagerState:
    """계획 수립 단계 실행

    strategy_node에서 저장된 rag_context를 재사용하므로
    별도 rag_service 주입이 불필요

    Args:
        state: 전략이 확정된 Manager 상태
        llm: LLM 프로바이더
        mcp: MCP 클라이언트 매니저

    Returns:
        plan_steps가 채워진 상태
    """
    updates = await planning_node(state, llm=llm, mcp=mcp)
    new_state = {**state, **updates}
    graph = new_state.get("node_graph") or create_initial_graph()
    new_state["node_graph"] = update_planning_done(
        graph, new_state.get("plan_steps", [])
    )
    return new_state


async def run_execution(
    state: ManagerState,
    llm: BaseLLMProvider,
    mcp: MCPClientManager,
    callback: ExecutionCallback | None = None,
    registry: AgentRegistry | None = None,
    rag_service: RAGService | None = None,
    light_llm: BaseLLMProvider | None = None,
    db_engine: Any | None = None,
    cancel_check: Any | None = None,
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
        light_llm: 요약 등 단순 작업에 사용할 경량 LLM (None이면 메인 LLM 사용)
        db_engine: DB 엔진 (None이면 step 결과 DB 저장 건너뜀)

    Returns:
        task_results가 채워진 상태
    """
    import asyncio
    import uuid
    from datetime import datetime, timezone

    if registry is None:
        registry = create_default_registry(light_llm=light_llm)

    db_session_id: int | None = None
    db_plan_step_ids: dict[int, int] = {}  # step_index → plan_step.id

    plan_steps = list(state.get("plan_steps", []))

    if db_engine and state.get("case_id"):
        try:
            async with get_session(db_engine) as session:
                analysis_session = await create_analysis_session(
                    session,
                    case_id=state["case_id"],
                    phase="execute",
                    system_profile=state.get("system_profile"),
                    strategy=state.get("analysis_strategy"),
                    plan_text=state.get("analysis_plan"),
                )
                db_session_id = analysis_session.id
                for idx, step in enumerate(plan_steps):
                    ps = await create_plan_step(
                        session,
                        session_id=db_session_id,
                        case_id=state["case_id"],
                        step_index=idx,
                        mcp_server=step.get("mcp_server"),
                        purpose=step.get("purpose"),
                        hints=step.get("hints"),
                        artifacts=step.get("artifacts") if isinstance(step.get("artifacts"), dict) else None,
                    )
                    db_plan_step_ids[idx] = ps.id
        except Exception as exc:
            logger.warning("db_execution_setup_failed", error=str(exc))

    disk_image_path = state.get("disk_image_path") or ""
    results: list[TaskResult] = list(state.get("task_results", []))
    evidence_repo: list[dict[str, Any]] = []
    followup_count = 0
    total = len(plan_steps)
    graph = state.get("node_graph") or create_initial_graph()

    connected_servers = mcp.connected_servers
    default_server = connected_servers[0] if connected_servers else ""

    prefetch_cache: dict[str, str] = {}

    context = ""
    i = 0

    while i < len(plan_steps):
        if cancel_check and cancel_check():
            logger.info("execution_cancelled", step_index=i)
            break
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
            graph = update_step_done(graph, i, "skip")
            if callback:
                callback.on_step_skip(i, total, step)
            logger.info("manual_step", step=step.get("index"), purpose=purpose)
            i += 1
            continue

        graph = update_step_started(graph, i)
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

        last_result = results[-1]
        graph = update_step_done(
            graph, i, last_result.get("status", "error"),
            output_summary=last_result.get("output", ""),
        )

        dfxml_frag = ""
        if last_result.get("status") == "success":
            dfxml_llm = light_llm or llm
            try:
                dfxml_resp = await dfxml_llm.chat(
                    messages=[{
                        "role": "user",
                        "content": "이 단계의 결과를 DFXML로 변환해주세요.",
                    }],
                    tools=None,
                    system=build_dfxml_prompt([last_result]),
                )
                dfxml_frag = (
                    dfxml_resp.content
                    if isinstance(dfxml_resp.content, str) else ""
                )
            except Exception as exc:
                logger.warning(
                    "dfxml_fragment_generation_failed",
                    task_id=task["task_id"],
                    error=str(exc),
                )

            if dfxml_frag:
                evidence_repo.append({
                    "task_id": task["task_id"],
                    "agent_name": agent_name,
                    "server_name": server_name,
                    "artifact": dfxml_frag,
                    "format": "dfxml",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

        if db_engine and db_session_id and state.get("case_id"):
            try:
                # followup 스텝은 실행 중 동적으로 plan_step 레코드 생성
                if i not in db_plan_step_ids:
                    async with get_session(db_engine) as session:
                        ps = await create_plan_step(
                            session,
                            session_id=db_session_id,
                            case_id=state["case_id"],
                            step_index=i,
                            mcp_server=step.get("mcp_server"),
                            purpose=step.get("purpose"),
                            hints=step.get("hints"),
                            is_followup=True,
                        )
                        db_plan_step_ids[i] = ps.id

                async with get_session(db_engine) as session:
                    agent_run_rec = await create_agent_run(
                        session,
                        session_id=db_session_id,
                        plan_step_id=db_plan_step_ids[i],
                        agent_name=last_result.get("agent_name", agent_name),
                        tool=step.get("tool"),
                    )
                    await update_agent_run(
                        session,
                        agent_run_rec.id,
                        status=last_result.get("status", "error"),
                    )
                    await create_task_result(
                        session,
                        agent_run_id=agent_run_rec.id,
                        case_id=state["case_id"],
                        task_id=last_result.get("task_id", task["task_id"]),
                        status=last_result.get("status", "error"),
                        output=last_result.get("output", ""),
                        dfxml_fragment=dfxml_frag if last_result.get("status") == "success" else "",
                    )
            except Exception as exc:
                logger.warning(
                    "step_result_save_failed",
                    step_index=i,
                    error=str(exc),
                )

        if callback:
            step_result_for_cb = dict(last_result)
            if evidence_repo and evidence_repo[-1].get("task_id") == task["task_id"]:
                step_result_for_cb["dfxml_fragment"] = evidence_repo[-1]["artifact"]
            callback.on_step_done(i, total, step, agent_name, step_result_for_cb)

        follow_up = last_result.get("follow_up")
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
            graph = add_followup_step(
                graph, len(plan_steps) - 1, new_step["name"]
            )
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
        "node_graph": graph,
        "phase": "report",
    }


async def run_report(
    state: ManagerState,
    llm: BaseLLMProvider,
    light_llm: BaseLLMProvider | None = None,
) -> dict[str, Any]:
    """Report Agent 실행

    Args:
        state: 실행 결과가 포함된 Manager 상태
        llm: 메인 LLM 프로바이더
        light_llm: 경량 LLM 프로바이더 (DFXML 등 단순 작업용)

    Returns:
        summary, report, dfxml(통합), dfxml_fragments(step별) 포함 딕셔너리
    """
    task_results = state.get("task_results", [])
    case_description = ""
    if state.get("messages"):
        case_description = state["messages"][0].get("content", "")

    graph = state.get("node_graph") or create_initial_graph()
    graph = update_report_started(graph)

    report_graph = build_report_graph(llm, light_llm=light_llm)
    report_state = create_report_state(
        task_results=task_results,
        case_description=case_description,
        strategy=state.get("analysis_strategy", ""),
        evidence_repository=state.get("evidence_repository", []),
    )

    result = await report_graph.ainvoke(report_state)
    graph = update_report_done(graph)

    return {
        "summary": result.get("summary", ""),
        "report": result.get("report", ""),
        "dfxml": result.get("dfxml", ""),
        "dfxml_fragments": result.get("dfxml_fragments", {}),
        "node_graph": graph,
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
        node_graph=create_initial_graph(),
    )
