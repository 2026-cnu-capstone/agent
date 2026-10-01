"""분석 워크플로우 API 라우터"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.agent_bridge import (
    approve_mcp_install,
    approve_plan,
    approve_strategy,
    execute_analysis,
    generate_report,
    get_db_engine,
    get_node_graph,
    get_session_status,
    request_cancel,
    start_analysis,
)
from app.models import (
    AnalysisRequest,
    McpInstallApproval,
    PlanApproval,
    StrategyApproval,
)
from app.ws_manager import manager as ws_manager

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


async def _set_case_status(case_id: str, status: str) -> None:
    """케이스 상태 기록 (DB 미사용·실패 시 무시)

    Args:
        case_id: 분석 케이스 ID
        status: 기록할 cases.status 값
    """
    try:
        from database.engine import get_session
        from database.repository import update_case_status
        engine = await get_db_engine()
        if engine:
            async with get_session(engine) as session:
                await update_case_status(session, case_id, status)
    except Exception:
        pass


async def _mark_case_failed(case_id: str) -> None:
    """케이스 상태를 failed로 기록

    Args:
        case_id: 분석 케이스 ID
    """
    await _set_case_status(case_id, "failed")


@router.post("/start")
async def api_start_analysis(req: AnalysisRequest):
    """분석 시작 — 시스템 프로필 추출 + 전략 생성"""
    try:
        result = await start_analysis(req.case_id, req.disk_image_path, req.prompt)
        await ws_manager.send_event(req.case_id, "strategy_ready", result)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{case_id}/strategy/approve")
async def api_approve_strategy(case_id: str, req: StrategyApproval):
    """전략 승인/수정

    승인 시 계획 수립 전에 포렌식 도구 탐지를 수행하며, 설치 가능한 MCP
    서버가 있으면 `mcp_install_pending`으로 응답해 사용자 승인을 요청
    """
    try:
        result = await approve_strategy(case_id, req.approved, req.feedback)
        if result.get("mcp_install_pending"):
            return result
        if result.get("plan_ready"):
            await ws_manager.send_event(case_id, "plan_ready", result)
        else:
            await ws_manager.send_event(case_id, "strategy_ready", result)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{case_id}/mcp-install/approve")
async def api_approve_mcp_install(case_id: str, req: McpInstallApproval):
    """MCP 서버 설치 승인/거부

    승인하면 설치 → mcp_servers.json 등록 → MCP 재연결을 거친 뒤
    계획을 수립하므로, 새로 추가된 서버가 계획에 반영됨
    """
    try:
        result = await approve_mcp_install(case_id, req.approved, req.server_ids)
        if result.get("plan_ready"):
            await ws_manager.send_event(case_id, "plan_ready", result)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{case_id}/plan/approve")
async def api_approve_plan(case_id: str, req: PlanApproval):
    """계획 승인/수정"""
    try:
        result = await approve_plan(case_id, req.approved, req.feedback)
        if not req.approved:
            await ws_manager.send_event(case_id, "plan_ready", result)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{case_id}/execute")
async def api_execute_analysis(case_id: str):
    """승인된 계획 실행 (WebSocket으로 진행상황 push)

    완료 시 cases.status를 executed로 기록해 리포트 생성 전 재진입해도
    완료 상태로 복원되게 함
    """
    try:
        result = await execute_analysis(case_id)
        event_data = {"task_results": result["task_results"]}
        if result.get("node_graph"):
            event_data["node_graph"] = result["node_graph"]
        await _set_case_status(case_id, "executed")
        await ws_manager.send_event(case_id, "execution_done", event_data)
        return {"status": "done", "total_steps": len(result["task_results"])}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        await _mark_case_failed(case_id)
        await ws_manager.send_event(case_id, "error", {"message": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{case_id}/pause")
async def api_pause_analysis(case_id: str):
    """실행 중단 요청"""
    request_cancel(case_id)
    try:
        from database.engine import get_session
        from database.repository import update_case_status
        engine = await get_db_engine()
        if engine:
            async with get_session(engine) as session:
                await update_case_status(session, case_id, "open")
    except Exception:
        pass
    return {"status": "paused"}


@router.post("/{case_id}/report")
async def api_generate_report(case_id: str):
    """보고서 생성"""
    try:
        result = await generate_report(case_id)
        await ws_manager.send_event(case_id, "report_ready", result)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        await _mark_case_failed(case_id)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{case_id}/status")
async def api_get_status(case_id: str):
    """현재 분석 상태 조회"""
    return get_session_status(case_id)


@router.get("/{case_id}/graph")
async def api_get_graph(case_id: str):
    """노드 그래프 조회"""
    result = get_node_graph(case_id)
    if result is None:
        raise HTTPException(status_code=404, detail="세션 또는 그래프 없음")
    return result


@router.websocket("/ws/{case_id}")
async def websocket_analysis(websocket: WebSocket, case_id: str):
    """실시간 분석 이벤트 WebSocket"""
    await ws_manager.connect(case_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await ws_manager.disconnect(case_id, websocket)
    except Exception:
        await ws_manager.disconnect(case_id, websocket)
