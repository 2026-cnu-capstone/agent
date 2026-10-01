"""Agent 시스템 호출 브릿지

FastAPI 백엔드에서 agent src/ 모듈을 직접 import하여 호출
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

AGENT_ROOT = Path(__file__).parent.parent
AGENT_SRC = AGENT_ROOT / "src"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))

AGENT_ENV = AGENT_ROOT / ".env"
if AGENT_ENV.exists() and not os.environ.get("LLM_API_KEY"):
    from dotenv import load_dotenv
    load_dotenv(AGENT_ENV)

from agents.manager.graph import (
    ExecutionCallback,
    apply_mcp_installs,
    create_manager_state,
    run_discovery,
    run_execution,
    run_planning,
    run_report,
    run_strategy,
)
from config import LLMProvider, load_settings
from database.engine import get_engine, init_db
from database.repository import create_case, update_case_analysis_info, update_case_status
from llm_provider.base import BaseLLMProvider
from llm_provider.claude_code import ClaudeCodeProvider
from llm_provider.openai import OpenAIProvider
from llm_provider.anthropic import AnthropicProvider
from mcp_client.client import MCPClientManager
from rag.embedding import Embedder
from rag.pgvector_store import PgVectorStore
from rag.service import RAGService
from node_graph import get_graph_response
from state.manager import ManagerState

from app.ws_manager import manager as ws_manager

MAX_SESSIONS = 50
"""최대 동시 분석 세션 수"""

_settings = None
_mcp: MCPClientManager | None = None
_llm_cache: dict[str, BaseLLMProvider] = {}
_rag_service: RAGService | None = None
_analysis_states: OrderedDict[str, ManagerState] = OrderedDict()
_cancel_flags: dict[str, bool] = {}
_db_engine = None
_api_choice: str = "default"


def get_api_choice() -> str:
    """현재 LLM API 선택값 반환"""
    return _api_choice


def set_api_choice(choice: str) -> None:
    """LLM API 선택값 변경 및 캐시 초기화

    헤드리스 모드 전환 시 경량 LLM 프로바이더도 함께 바뀌므로
    "light" 캐시까지 비움
    """
    global _api_choice
    _api_choice = choice
    _llm_cache.pop("default", None)
    _llm_cache.pop("light", None)
    _llm_cache.pop(choice, None)


def _resolve_api_choice(settings) -> str:
    """현재 선택값의 실제 프로바이더 ID 해석

    "default"는 .env의 LLM 설정에 따라 claude_code/mindlogic/openai/anthropic 중
    하나로 결정됨

    Args:
        settings: 로드된 Settings 인스턴스

    Returns:
        실제 사용되는 프로바이더 ID
    """
    if _api_choice != "default":
        return _api_choice
    if settings.llm.provider == LLMProvider.CLAUDE_CODE:
        return "claude_code"
    if settings.llm.provider == LLMProvider.OPENAI and settings.llm.base_url:
        return "mindlogic" if settings.mindlogic_api_key else "openai"
    if settings.llm.provider == LLMProvider.OPENAI:
        return "openai"
    return "anthropic"


async def get_llm_options() -> dict[str, Any]:
    """사용 가능한 LLM 옵션 목록 반환"""
    settings = await get_settings()
    options = []
    if settings.openai_api_key:
        options.append({
            "id": "openai",
            "label": "OpenAI",
            "model": settings.openai_model,
            "available": True,
        })
    if settings.anthropic_api_key:
        options.append({
            "id": "anthropic",
            "label": "Anthropic",
            "model": settings.anthropic_model,
            "available": True,
        })
    if settings.mindlogic_api_key:
        options.append({
            "id": "mindlogic",
            "label": "Mindlogic",
            "model": settings.mindlogic_model,
            "available": True,
        })
    options.append({
        "id": "claude_code",
        "label": "Claude Code (헤드리스)",
        "model": settings.claude_code_model,
        "available": shutil.which(settings.claude_code.cli_path) is not None,
        "note": "API 키 대신 로컬 Claude Code 구독 인증 사용",
    })

    current = _resolve_api_choice(settings)

    light_available = bool(settings.anthropic_api_key)
    return {
        "current": current,
        "options": options,
        "light_llm": {
            "model": settings.light_llm.model,
            "available": light_available,
        },
    }


def get_mcp_config_path() -> Path:
    """MCP 서버 설정 파일 경로 반환"""
    return AGENT_SRC.parent / "config" / "mcp_servers.json"


async def get_settings():
    """설정 로드 (캐시)"""
    global _settings
    if _settings is None:
        _settings = load_settings(get_mcp_config_path())
    return _settings


async def reload_settings():
    """설정 캐시를 버리고 mcp_servers.json을 다시 로드

    MCP 서버를 새로 등록한 직후 호출해야 함. `reset_mcp()`만 호출하면
    캐시된 옛 설정으로 재연결되어 새 서버가 반영되지 않음

    Returns:
        새로 로드된 Settings 인스턴스
    """
    global _settings
    _settings = load_settings(get_mcp_config_path())
    _llm_cache.clear()
    return _settings


async def reconnect_mcp() -> MCPClientManager:
    """설정을 다시 읽어 MCP 서버 전체 재연결

    새로 등록된 서버를 실행 중 세션에 반영하기 위한 경로.
    `MCPClientManager`는 증분 연결 API가 없고 anyio cancel scope가 생성
    task에 묶여 있어, 전체 재연결이 가장 안전한 방식

    Returns:
        재연결된 MCP 클라이언트 매니저
    """
    await reset_mcp()
    await reload_settings()
    mcp = await get_mcp()
    logger_names = mcp.connected_servers
    import logging
    logging.getLogger("agent_bridge").info("mcp_reconnected: %s", logger_names)
    return mcp


async def get_db_engine():
    """DB 엔진 반환

    인증 실패·네트워크 단절로 초기화에 실패하면 None을 돌려줘
    호출부가 인메모리 fallback으로 계속 동작하게 함

    Returns:
        초기화된 AsyncEngine, database_url이 없거나 연결 실패 시 None
    """
    global _db_engine
    if _db_engine is not None:
        return _db_engine

    settings = await get_settings()
    if not getattr(settings, "database_url", None):
        return None

    engine = get_engine(settings.database_url)
    try:
        await init_db(engine)
    except Exception:
        import logging
        logging.getLogger("agent_bridge").warning("db_unavailable", exc_info=True)
        return None
    _db_engine = engine
    ws_manager.set_engine(engine)
    return engine


async def get_rag_service() -> RAGService | None:
    """RAG 서비스 인스턴스 반환 (설정 비활성 시 None)"""
    global _rag_service
    if _rag_service is not None:
        return _rag_service

    settings = await get_settings()
    if not settings.rag.enabled:
        return None

    engine = get_engine(settings.database_url)
    await init_db(engine)

    embedder = Embedder(settings.rag.embedding_model)
    store = PgVectorStore(engine, embedder)
    await store.ensure_extension()
    _rag_service = RAGService(
        store=store,
        top_k=settings.rag.search_top_k,
        similarity_threshold=settings.rag.similarity_threshold,
    )
    return _rag_service


async def get_llm(api_choice: str = "default") -> BaseLLMProvider:
    """LLM 프로바이더 인스턴스 반환 (api_choice별 캐시)"""
    if api_choice in _llm_cache:
        return _llm_cache[api_choice]

    settings = await get_settings()

    if api_choice == "claude_code" or (
        api_choice == "default"
        and settings.llm.provider == LLMProvider.CLAUDE_CODE
    ):
        from config import LLMConfig
        cc_config = LLMConfig(
            provider=LLMProvider.CLAUDE_CODE,
            model=settings.claude_code_model,
            max_tokens=settings.llm.max_tokens,
            temperature=settings.llm.temperature,
        )
        llm = ClaudeCodeProvider(cc_config, settings.claude_code)
    elif api_choice == "mindlogic" and settings.mindlogic_api_key:
        from config import LLMConfig
        mindlogic_config = LLMConfig(
            provider=LLMProvider.OPENAI,
            model=settings.mindlogic_model,
            base_url=settings.mindlogic_base_url,
        )
        llm = OpenAIProvider(mindlogic_config, api_key=settings.mindlogic_api_key)
    elif settings.llm.provider == LLMProvider.OPENAI:
        llm = OpenAIProvider(settings.llm, api_key=settings.openai_api_key)
    else:
        llm = AnthropicProvider(settings.llm, api_key=settings.anthropic_api_key)

    _llm_cache[api_choice] = llm
    return llm


async def get_light_llm() -> BaseLLMProvider | None:
    """경량 LLM 프로바이더 반환 (요약, DFXML 변환 등 단순 작업용)

    Claude Code 헤드리스 모드가 선택된 경우 API 키 없이도 경량 프로바이더를
    제공하므로 요약/DFXML 변환 경로가 비활성화되지 않음. Mindlogic 선택 시에는
    경량 모델도 같은 게이트웨이로 호출해 Anthropic 키 크레딧과 무관하게 동작
    """
    if "light" in _llm_cache:
        return _llm_cache["light"]

    settings = await get_settings()

    if _api_choice == "claude_code" or (
        _api_choice == "default"
        and settings.llm.provider == LLMProvider.CLAUDE_CODE
    ):
        from config import LLMConfig
        light_config = LLMConfig(
            provider=LLMProvider.CLAUDE_CODE,
            model=settings.claude_code_light_model,
            max_tokens=settings.light_llm.max_tokens,
        )
        llm: BaseLLMProvider = ClaudeCodeProvider(light_config, settings.claude_code)
        _llm_cache["light"] = llm
        return llm

    if _resolve_api_choice(settings) == "mindlogic" and settings.mindlogic_api_key:
        from config import LLMConfig
        light_config = LLMConfig(
            provider=LLMProvider.OPENAI,
            model=settings.light_llm.model,
            base_url=settings.mindlogic_base_url,
            max_tokens=settings.light_llm.max_tokens,
        )
        llm = OpenAIProvider(light_config, api_key=settings.mindlogic_api_key)
        _llm_cache["light"] = llm
        return llm

    if not settings.anthropic_api_key:
        return None

    llm = AnthropicProvider(settings.light_llm, api_key=settings.anthropic_api_key)
    _llm_cache["light"] = llm
    return llm


async def get_mcp() -> MCPClientManager:
    """MCP 클라이언트 매니저 인스턴스 반환 (싱글톤, 실패 시 재시도)"""
    global _mcp
    if _mcp is not None:
        return _mcp

    settings = await get_settings()
    mcp = MCPClientManager(settings.mcp)
    try:
        await mcp.connect_all()
    except Exception as exc:
        _mcp = None
        raise RuntimeError(f"MCP 서버 연결 실패: {exc}") from exc

    _mcp = mcp
    return _mcp


async def reset_mcp() -> None:
    """MCP 연결 초기화 (에러 복구용)"""
    global _mcp
    if _mcp is not None:
        try:
            await _mcp.disconnect_all()
        except Exception:
            pass
    _mcp = None


async def shutdown_mcp() -> None:
    """MCP 연결 종료 (앱 종료 시)"""
    global _mcp
    if _mcp is not None:
        await _mcp.disconnect_all()
        _mcp = None


def request_cancel(case_id: str) -> None:
    """실행 중단 요청"""
    _cancel_flags[case_id] = True


def is_cancelled(case_id: str) -> bool:
    """중단 요청 여부 확인"""
    return _cancel_flags.get(case_id, False)


def clear_cancel(case_id: str) -> None:
    """중단 플래그 초기화"""
    _cancel_flags.pop(case_id, None)


def cleanup_session(case_id: str) -> None:
    """분석 세션 정리"""
    _analysis_states.pop(case_id, None)
    _cancel_flags.pop(case_id, None)


def _enforce_session_limit() -> None:
    """세션 수 제한 초과 시 가장 오래된 세션 제거"""
    while len(_analysis_states) > MAX_SESSIONS:
        oldest_key = next(iter(_analysis_states))
        _analysis_states.pop(oldest_key)
        _cancel_flags.pop(oldest_key, None)


def get_session_status(case_id: str) -> dict[str, Any]:
    """분석 세션 현재 상태 조회"""
    state = _analysis_states.get(case_id)
    if not state:
        return {"exists": False}
    node_graph = state.get("node_graph")
    return {
        "exists": True,
        "phase": state.get("phase", "unknown"),
        "plan_steps_count": len(state.get("plan_steps", [])),
        "task_results_count": len(state.get("task_results", [])),
        "has_strategy": bool(state.get("analysis_strategy")),
        "has_plan": bool(state.get("plan_steps")),
        "node_graph": get_graph_response(node_graph) if node_graph else None,
    }


def get_node_graph(case_id: str) -> dict[str, Any] | None:
    """분석 세션의 노드 그래프 조회

    Args:
        case_id: 케이스 ID

    Returns:
        노드 그래프 API 응답 또는 None
    """
    state = _analysis_states.get(case_id)
    if not state:
        return None
    node_graph = state.get("node_graph")
    if not node_graph:
        return None
    return get_graph_response(node_graph)


class WebSocketExecutionCallback:
    """Sub-Agent 실행 진행을 WebSocket으로 전송하는 콜백"""

    def __init__(self, case_id: str) -> None:
        self._case_id = case_id
        self._step_starts: dict[int, float] = {}

    def on_step_start(self, step_index: int, total: int, step: dict, agent_name: str) -> None:
        """단계 시작 이벤트"""
        import asyncio
        self._step_starts[step_index] = time.time()
        asyncio.create_task(
            ws_manager.send_event(self._case_id, "step_started", {
                "step_index": step_index,
                "total": total,
                "step_name": step.get("name", step.get("purpose", "")),
                "agent_name": agent_name,
            })
        )

    def on_step_done(self, step_index: int, total: int, step: dict, agent_name: str, result: dict) -> None:
        """단계 완료 이벤트"""
        import asyncio
        elapsed = time.time() - self._step_starts.get(step_index, time.time())
        event_data: dict[str, Any] = {
            "step_index": step_index,
            "total": total,
            "step_name": step.get("name", step.get("purpose", "")),
            "agent_name": agent_name,
            "status": result.get("status", "error"),
            "output": result.get("output", "")[:300],
            "elapsed": f"{int(elapsed)}s",
            "dfxml_fragment": result.get("dfxml_fragment") or "",
        }
        asyncio.create_task(
            ws_manager.send_event(self._case_id, "step_completed", event_data)
        )

    def on_step_skip(self, step_index: int, total: int, step: dict) -> None:
        """수동 단계 건너뛰기 이벤트"""
        import asyncio
        asyncio.create_task(
            ws_manager.send_event(self._case_id, "step_completed", {
                "step_index": step_index,
                "total": total,
                "step_name": step.get("name", ""),
                "agent_name": "manual",
                "status": "skip",
                "output": "",
                "elapsed": "0s",
            })
        )


async def start_analysis(case_id: str, disk_image_path: str, prompt: str) -> dict[str, Any]:
    """분석 시작: 시스템 프로필 추출 + 전략 생성"""
    _enforce_session_limit()
    mcp = await get_mcp()
    llm = await get_llm(_api_choice)
    rag = await get_rag_service()

    system_profile = ""
    try:
        result = await mcp.call_tool(
            "dissect__extract_system_profile",
            {"image_path": disk_image_path},
        )
        system_profile = mcp.get_tool_result_text(result)
    except Exception:
        pass

    state = create_manager_state(
        user_message=prompt,
        disk_image_path=disk_image_path,
        system_profile=system_profile,
    )

    db_engine = await get_db_engine()
    if db_engine:
        try:
            from database.engine import get_session
            async with get_session(db_engine) as session:
                # case_id가 DB에 이미 존재하면 분석 정보 업데이트, 없으면 새로 생성
                updated = await update_case_analysis_info(
                    session,
                    case_id=case_id,
                    user_prompt=prompt,
                    disk_image_path=disk_image_path,
                )
                if updated is None:
                    db_case = await create_case(
                        session,
                        title=f"케이스 - {Path(disk_image_path).name}",
                        user_prompt=prompt,
                        disk_image_path=disk_image_path,
                    )
                    state["case_id"] = db_case.id
                else:
                    state["case_id"] = case_id
        except Exception:
            pass

    state = await run_strategy(state, llm, rag_service=rag)
    _analysis_states[case_id] = state

    node_graph = state.get("node_graph")
    return {
        "strategy": state.get("analysis_strategy", ""),
        "system_profile": system_profile,
        "node_graph": get_graph_response(node_graph) if node_graph else None,
    }


async def approve_strategy(case_id: str, approved: bool, feedback: str = "") -> dict[str, Any]:
    """전략 승인 또는 수정 후 계획 생성"""
    state = _analysis_states.get(case_id)
    if not state:
        raise ValueError(f"분석 세션 없음: {case_id}")

    llm = await get_llm(_api_choice)
    mcp = await get_mcp()
    rag = await get_rag_service()

    if not approved and feedback:
        strategy = state.get("analysis_strategy", "")
        user_msg = state["messages"][0].get("content", "")
        state = create_manager_state(
            user_message=(
                f"{user_msg}\n\n[이전 전략]\n{strategy}\n\n"
                f"[수정 요청]: {feedback}\n위 수정 요청을 반영하여 새로운 분석 전략을 작성하세요."
            ),
            disk_image_path=state.get("disk_image_path"),
            disk_image_format=state.get("disk_image_format"),
            system_profile=state.get("system_profile"),
        )
        state = await run_strategy(state, llm, rag_service=rag)
        _analysis_states[case_id] = state
        node_graph = state.get("node_graph")
        return {
            "strategy": state.get("analysis_strategy", ""),
            "plan_ready": False,
            "node_graph": get_graph_response(node_graph) if node_graph else None,
        }

    settings = await get_settings()
    state = await run_discovery(
        state,
        mcp_config=settings.mcp,
        config_path=get_mcp_config_path(),
        discovery_config=settings.discovery,
    )
    _analysis_states[case_id] = state

    if state.get("hitl_type") == "mcp_install" and state.get("hitl_pending"):
        pending = state.get("pending_mcp_installs") or []
        await ws_manager.send_event(
            case_id,
            "mcp_install_proposed",
            {"pending": pending},
        )
        return {
            "plan_ready": False,
            "mcp_install_pending": True,
            "pending_mcp_installs": pending,
            "discovery_report": state.get("discovery_report"),
        }

    return await _run_planning_and_respond(case_id, state, llm, mcp)


async def _run_planning_and_respond(
    case_id: str,
    state: ManagerState,
    llm: BaseLLMProvider,
    mcp: MCPClientManager,
) -> dict[str, Any]:
    """계획 수립 실행 후 API 응답 구성

    Args:
        case_id: 분석 케이스 ID
        state: 계획 수립 직전 상태
        llm: LLM 프로바이더
        mcp: MCP 클라이언트 매니저

    Returns:
        계획 텍스트와 단계 목록이 담긴 응답
    """
    state = await run_planning(state, llm, mcp)
    _analysis_states[case_id] = state

    steps = state.get("plan_steps", [])
    import logging
    logging.getLogger("agent_bridge").info(
        "plan_steps_sample: %s",
        [{k: s.get(k) for k in ("name", "mcp_server", "tool")} for s in steps[:3]],
    )

    node_graph = state.get("node_graph")
    return {
        "plan_text": state.get("analysis_plan", ""),
        "steps": steps,
        "plan_ready": True,
        "node_graph": get_graph_response(node_graph) if node_graph else None,
        "installed_mcp_servers": state.get("installed_mcp_servers") or [],
    }


async def approve_mcp_install(
    case_id: str,
    approved: bool,
    server_ids: list[str] | None = None,
) -> dict[str, Any]:
    """MCP 설치 제안 승인/거부 처리 후 계획 수립 진행

    승인 시 설치 → mcp_servers.json 등록 → MCP 재연결까지 수행하므로,
    이어지는 계획 수립이 새로 추가된 서버를 반영

    Args:
        case_id: 분석 케이스 ID
        approved: 설치 승인 여부
        server_ids: 승인한 서버 키 목록 (None이면 제안 전체)

    Returns:
        설치 결과와 계획 수립 결과가 담긴 응답

    Raises:
        ValueError: 분석 세션이 없을 때
    """
    state = _analysis_states.get(case_id)
    if not state:
        raise ValueError(f"분석 세션 없음: {case_id}")

    llm = await get_llm(_api_choice)
    settings = await get_settings()
    install_results: list[dict[str, Any]] = []

    if not approved:
        state = {
            **state,
            "pending_mcp_installs": [],
            "hitl_pending": False,
            "hitl_type": "",
        }
        _analysis_states[case_id] = state
        mcp = await get_mcp()
        response = await _run_planning_and_respond(case_id, state, llm, mcp)
        response["install_results"] = install_results
        return response

    pending = state.get("pending_mcp_installs") or []
    targets = server_ids or [item["server_id"] for item in pending]

    async def progress(message: str) -> None:
        """설치 진행 상황을 WebSocket으로 전달"""
        await ws_manager.send_event(
            case_id, "mcp_install_progress", {"message": message}
        )

    install_root = (
        Path(settings.discovery.search_roots[0]).expanduser()
        if settings.discovery.search_roots
        else AGENT_ROOT.parent
    )

    state, outcomes = await apply_mcp_installs(
        state,
        targets,
        config_path=get_mcp_config_path(),
        install_root=install_root,
        progress=progress,
    )
    _analysis_states[case_id] = state
    install_results = [outcome.model_dump(exclude={"log"}) for outcome in outcomes]

    if state.get("installed_mcp_servers"):
        mcp = await reconnect_mcp()
    else:
        mcp = await get_mcp()

    await ws_manager.send_event(
        case_id,
        "mcp_install_completed",
        {
            "results": install_results,
            "connected_servers": mcp.connected_servers,
        },
    )

    response = await _run_planning_and_respond(case_id, state, llm, mcp)
    response["install_results"] = install_results
    response["connected_servers"] = mcp.connected_servers
    return response


async def approve_plan(case_id: str, approved: bool, feedback: str = "") -> dict[str, Any]:
    """계획 승인 또는 수정 후 재계획"""
    state = _analysis_states.get(case_id)
    if not state:
        raise ValueError(f"분석 세션 없음: {case_id}")

    llm = await get_llm(_api_choice)
    mcp = await get_mcp()
    rag = await get_rag_service()

    if not approved and feedback:
        plan = state.get("analysis_plan", "")
        user_msg = state["messages"][0].get("content", "")
        state = {
            **state,
            "messages": [{
                "role": "user",
                "content": (
                    f"{user_msg}\n\n[이전 계획]\n{plan}\n\n"
                    f"[수정 요청]: {feedback}\n위 수정 요청을 반영하여 새로운 계획을 작성하세요."
                ),
            }],
        }
        state = await run_planning(state, llm, mcp)
        _analysis_states[case_id] = state
        node_graph = state.get("node_graph")
        return {
            "plan_text": state.get("analysis_plan", ""),
            "steps": state.get("plan_steps", []),
            "node_graph": get_graph_response(node_graph) if node_graph else None,
        }

    _analysis_states[case_id] = state
    return {"approved": True}


async def execute_analysis(case_id: str) -> dict[str, Any]:
    """승인된 계획 실행

    외장 드라이브 분리 등으로 이미지가 사라진 상태에서 모든 단계가 헛도는 것을
    막기 위해 실행 전에 디스크 이미지 존재 여부를 확인

    Args:
        case_id: 분석 케이스 ID

    Returns:
        task_results와 node_graph가 담긴 실행 결과

    Raises:
        ValueError: 분석 세션이 없을 때
        FileNotFoundError: 디스크 이미지에 접근할 수 없을 때
    """
    state = _analysis_states.get(case_id)
    if not state:
        raise ValueError(f"분석 세션 없음: {case_id}")

    image_path = state.get("disk_image_path")
    if image_path and not Path(image_path).exists():
        raise FileNotFoundError(f"디스크 이미지에 접근할 수 없습니다: {image_path}")

    clear_cancel(case_id)
    llm = await get_llm(_api_choice)
    mcp = await get_mcp()
    rag = await get_rag_service()
    db_engine = await get_db_engine()
    callback = WebSocketExecutionCallback(case_id)

    light_llm = await get_light_llm()
    settings = await get_settings()
    state = await run_execution(
        state, llm, mcp,
        callback=callback,
        rag_service=rag,
        light_llm=light_llm,
        db_engine=db_engine,
        cancel_check=lambda: is_cancelled(case_id),
        max_parallel=settings.execution_max_parallel,
    )
    _analysis_states[case_id] = state
    clear_cancel(case_id)

    node_graph = state.get("node_graph")
    return {
        "task_results": state.get("task_results", []),
        "evidence_repository": state.get("evidence_repository", []),
        "node_graph": get_graph_response(node_graph) if node_graph else None,
    }


async def generate_report(case_id: str) -> dict[str, str]:
    """보고서 생성"""
    state = _analysis_states.get(case_id)
    if not state:
        raise ValueError(f"분석 세션 없음: {case_id}")

    llm = await get_llm(_api_choice)
    light_llm = await get_light_llm()
    result = await run_report(state, llm, light_llm=light_llm)
    node_graph = result.get("node_graph")
    report_result = {
        "summary": result.get("summary", ""),
        "report": result.get("report", ""),
        "dfxml": result.get("dfxml", ""),
    }

    db_engine = await get_db_engine()
    if db_engine:
        try:
            from database.engine import get_session
            from database.repository import create_report
            async with get_session(db_engine) as session:
                await create_report(
                    session,
                    case_id=case_id,
                    summary=report_result["summary"],
                    report_text=report_result["report"],
                    dfxml=report_result["dfxml"],
                )
            async with get_session(db_engine) as session:
                await update_case_status(session, case_id, "done")
        except Exception as exc:
            import logging
            logging.getLogger("agent_bridge").warning("report_save_failed: %s", exc)

    if node_graph:
        report_result["node_graph"] = get_graph_response(node_graph)
    cleanup_session(case_id)
    return report_result
