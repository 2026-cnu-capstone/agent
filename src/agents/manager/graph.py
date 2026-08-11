"""Manager Agent 단계별 그래프 및 오케스트레이션 함수

HITL 게이트는 run_agent.py에서 명시적으로 제어하므로,
각 단계(전략, 계획, 실행, 보고서)를 독립 함수로 제공
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Protocol

import structlog
from pydantic import ValidationError

from agents.capability import classify_execution_failure
from agents.factory import AgentRegistry, create_default_registry
from agents.manager.nodes import (
    _extract_agent_name,
    discovery_node,
    hitl_mcp_install_gate,
    planning_node,
    strategy_node,
)
from agents.report.graph import build_report_graph, create_report_state
from config import DiscoveryConfig, MCPConfig
from constants import EXECUTION_STEP_DELAY, MAX_FOLLOWUP_STEPS
from database.engine import get_session
from database.repository import (
    create_agent_run,
    create_analysis_session,
    create_plan_step,
    create_task_result,
    save_workflow_graph,
    update_agent_run,
    update_analysis_session,
)
from discovery.installer import MCPInstaller
from discovery.proposal import apply_proposals
from discovery.types import InstallOutcome, MCPRecommendation, ServerProposal
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


async def run_discovery(
    state: ManagerState,
    mcp_config: MCPConfig | None = None,
    config_path: Path | None = None,
    discovery_config: DiscoveryConfig | None = None,
) -> ManagerState:
    """전략 확정 후 포렌식 도구 탐지 및 MCP 설치 후보 산출

    계획 수립 직전에 실행. 승인 후보가 있으면 HITL 게이트를 대기 상태로 두어
    `apply_mcp_installs` 호출을 기다림

    Args:
        state: 전략이 확정된 Manager 상태
        mcp_config: 현재 등록된 MCP 설정
        config_path: mcp_servers.json 경로
        discovery_config: 탐색 루트/타임아웃 설정

    Returns:
        discovery_report와 HITL 대기 여부가 반영된 상태
    """
    updates = await discovery_node(
        state,
        mcp_config=mcp_config,
        config_path=config_path,
        discovery_config=discovery_config,
    )
    new_state = {**state, **updates}
    new_state.update(hitl_mcp_install_gate(new_state))
    return new_state


async def apply_mcp_installs(
    state: ManagerState,
    server_ids: list[str],
    config_path: Path,
    install_root: Path,
    progress: Any | None = None,
) -> tuple[ManagerState, list[InstallOutcome]]:
    """사용자가 승인한 MCP 서버를 설치하고 설정에 등록

    설치가 필요 없는(이미 로컬에 있는) 서버는 등록만 수행.
    설치 실패는 예외로 올리지 않고 결과 목록으로 반환해, 일부 실패가
    분석 진행 자체를 막지 않도록 함

    Args:
        state: MCP 설치 승인 대기 중인 상태
        server_ids: 사용자가 승인한 서버 키 목록
        config_path: mcp_servers.json 경로
        install_root: 팀 서버 저장소를 클론할 상위 디렉터리
        progress: 진행 상황 알림 콜백

    Returns:
        (갱신된 상태, 서버별 설치 결과 목록)
    """
    report = state.get("discovery_report") or {}
    recommendations = _rebuild_recommendations(report, set(server_ids))

    installer = MCPInstaller(install_root=install_root, progress=progress)
    outcomes: list[InstallOutcome] = []
    proposals: list[ServerProposal] = []

    for recommendation in recommendations:
        resolved = recommendation.resolved_command
        if not resolved:
            outcome = await installer.install(recommendation)
            outcomes.append(outcome)
            if not outcome.ok:
                continue
            resolved = outcome.resolved_command
        else:
            outcomes.append(
                InstallOutcome(
                    server_id=recommendation.candidate.server_id,
                    ok=True,
                    resolved_command=resolved,
                )
            )

        proposals.append(
            ServerProposal(
                server_id=recommendation.candidate.server_id,
                entry={
                    "command": resolved,
                    "args": list(recommendation.candidate.args),
                },
                ready=True,
            )
        )

    added = apply_proposals(config_path, proposals) if proposals else []
    logger.info(
        "mcp_installs_applied",
        approved=server_ids,
        registered=added,
        failures=[o.server_id for o in outcomes if not o.ok],
    )

    new_state: ManagerState = {
        **state,
        "installed_mcp_servers": [
            *(state.get("installed_mcp_servers") or []),
            *added,
        ],
        "pending_mcp_installs": [],
        "hitl_pending": False,
        "hitl_type": "",
    }
    return new_state, outcomes


def _rebuild_recommendations(
    report: dict[str, Any], server_ids: set[str]
) -> list[MCPRecommendation]:
    """직렬화된 리포트에서 승인된 추천 객체 복원

    Args:
        report: DiscoveryReport 직렬화 결과
        server_ids: 승인된 서버 키 집합

    Returns:
        복원된 추천 목록
    """
    restored: list[MCPRecommendation] = []
    for raw in report.get("recommendations", []):
        try:
            recommendation = MCPRecommendation.model_validate(raw)
        except ValidationError:
            logger.warning("recommendation_restore_failed", raw=raw)
            continue
        if recommendation.candidate.server_id in server_ids:
            restored.append(recommendation)
    return restored


FINDING_PATTERNS: tuple[str, ...] = (
    r"(?:/(?:c:|sysvol)|[A-Za-z]:)[\\/][^\n,;`\"']{3,120}",
    r"HK(?:LM|CU|U|CR|CC)\\[^\n,;`\"']{3,120}",
    r"\b\d{1,3}(?:\.\d{1,3}){3}\b",
    r"\b[0-9a-fA-F]{12}\b",
    r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b",
    r"\b\w+=[^\s,;\n]{2,}",
)
"""단계 출력에서 다음 단계에 넘길 값으로 뽑을 패턴 (경로·키·IP·MAC·메일·설정값)"""

MEMO_MAX_ITEMS = 40
"""공유 메모에 유지할 최대 항목 수"""


def extract_findings(step_name: str, output: str, limit: int = 6) -> list[str]:
    """단계 출력에서 다음 단계가 참조할 핵심 값 추출

    직전 단계 출력 앞 500자를 그대로 넘기는 방식은 정보 밀도가 낮고 병렬
    실행에서 끊기므로, 식별 가능한 값만 뽑아 누적하는 방식으로 대체

    Args:
        step_name: 값을 추출한 단계 이름
        output: 단계 출력 텍스트
        limit: 단계당 최대 추출 개수

    Returns:
        "단계명: 값" 형식의 문자열 목록
    """
    if not output:
        return []

    seen: list[str] = []
    for pattern in FINDING_PATTERNS:
        for match in re.findall(pattern, output):
            value = match.split("  ")[0].strip().rstrip(".,;)")
            if len(value) < 4 or value in seen:
                continue
            seen.append(value)
            if len(seen) >= limit:
                break
        if len(seen) >= limit:
            break

    return [f"{step_name}: {value}" for value in seen]


def format_findings_memo(memo: list[str]) -> str:
    """공유 메모를 프롬프트용 텍스트로 변환

    Args:
        memo: 누적된 발견값 목록

    Returns:
        줄바꿈으로 구분된 텍스트
    """
    return "\n".join(f"- {item}" for item in memo[-MEMO_MAX_ITEMS:])


async def run_planning(
    state: ManagerState,
    llm: BaseLLMProvider,
    mcp: MCPClientManager,
    mcp_config: MCPConfig | None = None,
    max_steps: int = 12,
) -> ManagerState:
    """계획 수립 단계 실행

    strategy_node에서 저장된 rag_context를 재사용하므로
    별도 rag_service 주입이 불필요

    Args:
        state: 전략이 확정된 Manager 상태
        llm: LLM 프로바이더
        mcp: MCP 클라이언트 매니저
        mcp_config: MCP 설정 (역량 탐색 프로브 선언 포함)
        max_steps: 계획 단계 수 상한

    Returns:
        plan_steps가 채워진 상태
    """
    updates = await planning_node(
        state, llm=llm, mcp=mcp, mcp_config=mcp_config, max_steps=max_steps
    )
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
    max_parallel: int = 1,
) -> ManagerState:
    """Sub-Agent 실행 단계

    plan_steps의 각 단계를 Sub-Agent에 동적 dispatch하고 결과 수집.
    AgentRegistry를 통해 MCP 서버명 기반으로 전용/범용 에이전트를 자동 선택.
    실행 완료 후 RAG 서비스가 활성화되어 있으면 결과를 벡터 저장소에 저장.

    `max_parallel`이 1보다 크면 Sub-Agent 호출을 동시 실행한다. 실행 시간의 대부분은
    LLM 왕복이고 MCP 도구 호출은 동시 요청에 안전하므로 병렬화 이득이 크다.
    다만 각 단계가 직전 단계 출력을 `context`로 전달받는 연결이 끊기므로,
    단계 간 의존이 있는 계획에서는 순차 실행이 필요하다.

    결과 집계(노드 그래프 갱신, DFXML 생성, DB 기록, 콜백)는 병렬 모드에서도
    단계 순서대로 수행해 기존 동작과 산출물 순서를 동일하게 유지한다.

    Args:
        state: 계획이 확정된 Manager 상태
        llm: LLM 프로바이더
        mcp: MCP 클라이언트 매니저
        callback: 진행 상황 콜백 (None이면 무시)
        registry: Sub-Agent 레지스트리 (None이면 기본 레지스트리 사용)
        rag_service: RAG 서비스 (None이면 결과 저장 건너뜀)
        light_llm: 요약 등 단순 작업에 사용할 경량 LLM (None이면 메인 LLM 사용)
        db_engine: DB 엔진 (None이면 step 결과 DB 저장 건너뜀)
        cancel_check: 중단 요청 여부를 반환하는 콜러블
        max_parallel: 동시 실행할 Sub-Agent 수 (1이면 순차 실행)

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

    await mcp.list_tools()

    if disk_image_path:
        for server in connected_servers:
            try:
                open_result = await mcp.call_tool(
                    f"{server}__open_target", {"path": disk_image_path}
                )
                target_text = mcp.get_tool_result_text(open_result)
                import json as _json
                try:
                    target_data = _json.loads(target_text)
                    target_id = target_data.get("target_id", "")
                except (ValueError, TypeError):
                    target_id = ""
                if target_id:
                    plugin_result = await mcp.call_tool(
                        f"{server}__list_plugins", {"target_id": target_id}
                    )
                    prefetch_cache[server] = mcp.get_tool_result_text(plugin_result)
                    logger.info("plugins_prefetched", server=server, target_id=target_id)
            except Exception as exc:
                logger.debug("prefetch_open_skipped", server=server, error=str(exc))

    context = ""
    findings_memo: list[str] = list(state.get("findings_memo") or [])
    i = 0

    async def dispatch_step(
        step_index: int, step_def: dict[str, Any], shared_context: str
    ) -> dict[str, Any]:
        """단일 단계를 Sub-Agent에 dispatch하고 원시 결과 반환

        집계나 상태 변경은 하지 않고 호출만 담당하므로 동시 실행에 안전

        Args:
            step_index: 단계 인덱스
            step_def: 계획 단계 정의
            shared_context: 이전 단계 요약 컨텍스트

        Returns:
            task, agent/server 이름, 시작 시각, sub_result 또는 error를 담은 dict
        """
        agent = _extract_agent_name(step_def)
        if agent == "manual" and default_server:
            agent = default_server
        server = registry.resolve_server(agent)

        dispatch_task: TaskAssignment = {
            "task_id": str(uuid.uuid4())[:8],
            "agent_name": agent,
            "step": step_def,
            "context": shared_context,
            "disk_image_path": disk_image_path,
        }

        purpose_text = step_def.get("purpose", "")
        hint_text = step_def.get("hints", "")
        combined = f"{purpose_text}\n힌트: {hint_text}" if hint_text else purpose_text

        started = datetime.now(timezone.utc)
        payload: dict[str, Any] = {
            "task": dispatch_task,
            "agent_name": agent,
            "server_name": server,
            "started": started,
            "sub_result": None,
            "error": None,
        }
        try:
            memo_text = format_findings_memo(findings_memo)
            graph_impl = registry.build_graph(
                server, llm, mcp,
                purpose=combined,
                extra_context=prefetch_cache.get(server, ""),
                findings_memo=memo_text,
            )
            payload["sub_result"] = await graph_impl.ainvoke(
                registry.build_state(server, dispatch_task)
            )
        except Exception as exc:
            logger.error("sub_agent_failed", agent=agent, error=str(exc))
            payload["error"] = str(exc)
        return payload

    precomputed: dict[int, dict[str, Any]] = {}

    if max_parallel > 1 and plan_steps:
        dispatchable = [
            (idx, sd)
            for idx, sd in enumerate(plan_steps)
            if _extract_agent_name(sd) != "manual" or default_server
        ]
        for idx, sd in dispatchable:
            server = registry.resolve_server(_extract_agent_name(sd))
            if server not in prefetch_cache:
                prefetch_cache[server] = await registry.prefetch(server, mcp)

        limiter = asyncio.Semaphore(max_parallel)

        async def guarded(idx: int, sd: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            """동시 실행 수를 제한하며 단계를 dispatch

            Args:
                idx: 단계 인덱스
                sd: 계획 단계 정의

            Returns:
                (단계 인덱스, dispatch 결과) 튜플
            """
            async with limiter:
                return idx, await dispatch_step(idx, sd, "")

        logger.info(
            "parallel_execution_started",
            steps=len(dispatchable),
            max_parallel=max_parallel,
        )
        gathered = await asyncio.gather(
            *(guarded(idx, sd) for idx, sd in dispatchable)
        )
        precomputed = dict(gathered)

    while i < len(plan_steps):
        if cancel_check and cancel_check():
            logger.info("execution_cancelled", step_index=i)
            break
        step = plan_steps[i]
        if i > 0 and i not in precomputed:
            await asyncio.sleep(EXECUTION_STEP_DELAY)
        agent_name = _extract_agent_name(step)
        if agent_name == "manual" and default_server:
            agent_name = default_server
        purpose = step.get("purpose", "")

        server_name = registry.resolve_server(agent_name)

        task: TaskAssignment = {
            "task_id": str(uuid.uuid4())[:8],
            "agent_name": agent_name,
            "step": step,
            "context": context,
            "disk_image_path": disk_image_path,
        }

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

        dispatched = precomputed.pop(i, None)

        if dispatched is None:
            if server_name not in prefetch_cache:
                prefetch_cache[server_name] = await registry.prefetch(
                    server_name, mcp
                )

            logger.info(
                "sub_agent_dispatch",
                agent=agent_name,
                server=server_name,
                step=step.get("index"),
            )
            dispatched = await dispatch_step(i, step, context)

        task = dispatched["task"]
        agent_name = dispatched["agent_name"]
        server_name = dispatched["server_name"]
        step_start_time = dispatched["started"]
        sub_result = dispatched["sub_result"]

        if dispatched["error"] is not None:
            results.append({
                "task_id": task["task_id"],
                "agent_name": agent_name,
                "status": "error",
                "output": f"Error: {dispatched['error']}",
                "raw_output_ref": "",
                "artifacts": [],
            })
        elif sub_result and sub_result.get("result"):
            results.append(sub_result["result"])
            step_output = sub_result["result"].get("output", "")
            context = step_output[:500]
            if sub_result["result"].get("status") == "success":
                findings_memo.extend(
                    extract_findings(
                        str(step.get("name") or f"step{i + 1}"), step_output
                    )
                )
        else:
            results.append({
                "task_id": task["task_id"],
                "agent_name": agent_name,
                "status": "error",
                "output": "Sub-Agent가 결과를 반환하지 않았습니다.",
                "raw_output_ref": "",
                "artifacts": [],
            })

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
                    step_elapsed_ms = int(
                        (datetime.now(timezone.utc) - step_start_time).total_seconds() * 1000
                    )
                    await create_task_result(
                        session,
                        agent_run_id=agent_run_rec.id,
                        case_id=state["case_id"],
                        task_id=last_result.get("task_id", task["task_id"]),
                        status=last_result.get("status", "error"),
                        output=last_result.get("output", ""),
                        dfxml_fragment=dfxml_frag if last_result.get("status") == "success" else "",
                        elapsed_ms=step_elapsed_ms,
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

    if db_engine and db_session_id:
        results_text = "\n".join(
            f"[{r.get('agent_name', '')}] {r.get('output', '')[:300]}"
            for r in results
            if r.get("status") == "success"
        )
        try:
            async with get_session(db_engine) as session:
                await update_analysis_session(
                    session,
                    session_id=db_session_id,
                    phase="report",
                    result_summary=results_text[:5000],
                )
        except Exception as exc:
            logger.warning("analysis_session_update_failed", error=str(exc))

    if db_engine and state.get("case_id"):
        try:
            async with get_session(db_engine) as session:
                await save_workflow_graph(session, state["case_id"], graph)
        except Exception as exc:
            logger.warning("workflow_graph_save_failed", error=str(exc))

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

    capability_failures = [
        {"step_index": idx, "output": (r.get("output") or "")[:200]}
        for idx, r in enumerate(results)
        if r.get("status") == "error"
        and classify_execution_failure(r.get("output") or "")
    ]

    return {
        **state,
        "task_results": results,
        "evidence_repository": evidence_repo,
        "node_graph": graph,
        "findings_memo": findings_memo,
        "plan_issues": [
            *(state.get("plan_issues") or []),
            *[
                {
                    "step_index": f["step_index"],
                    "kind": "runtime_missing_capability",
                    "name": "",
                    "detail": f["output"],
                }
                for f in capability_failures
            ],
        ],
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
        discovery_report=None,
        pending_mcp_installs=[],
        installed_mcp_servers=[],
        findings_memo=[],
        plan_issues=[],
    )
