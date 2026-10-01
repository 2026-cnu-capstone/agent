"""MCP 서버 관리 API 라우터

mcp_servers.json을 단일 진실 원천으로 두고, 변경 후에는 전체 재연결로 반영
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from app.agent_bridge import get_mcp, get_mcp_config_path, get_settings, reconnect_mcp

router = APIRouter(prefix="/api/mcp-servers", tags=["mcp-servers"])


class McpServerCreate(BaseModel):
    """MCP 서버 등록 요청"""

    name: str
    transport: Literal["stdio", "sse"] = "stdio"
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] | None = None


def _read_config() -> dict[str, Any]:
    """mcp_servers.json 원본 로드

    Returns:
        파일 전체 dict, 파일이 없으면 빈 mcpServers 구조
    """
    path = get_mcp_config_path()
    if not path.exists():
        return {"mcpServers": {}}
    with open(path) as f:
        return json.load(f)


def _write_config(raw: dict[str, Any]) -> None:
    """mcp_servers.json 원자적 저장

    임시 파일에 쓴 뒤 교체해 부분 기록을 방지

    Args:
        raw: 저장할 설정 전체 dict
    """
    path = get_mcp_config_path()
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


async def _list_servers() -> list[dict[str, Any]]:
    """등록 서버별 연결 여부와 도구 수 집계

    Returns:
        프론트엔드 McpServerDTO 형식의 서버 목록
    """
    settings = await get_settings()
    mcp = await get_mcp()
    tools = await mcp.list_tools()
    connected = set(mcp.connected_servers)

    result = []
    for name, cfg in settings.mcp.servers.items():
        item: dict[str, Any] = {
            "name": name,
            "transport": cfg.transport,
            "connected": name in connected,
            "tool_count": sum(1 for k in tools if k.startswith(f"{name}__")),
        }
        if cfg.transport == "stdio":
            item.update(command=cfg.command, args=cfg.args)
        else:
            item["url"] = cfg.url
        result.append(item)
    return result


@router.get("")
async def list_mcp_servers():
    """등록된 MCP 서버와 연결 상태 조회

    Returns:
        서버 목록
    """
    return await _list_servers()


@router.post("")
async def create_mcp_server(req: McpServerCreate):
    """MCP 서버 등록 후 재연결

    Args:
        req: 등록할 서버 설정

    Returns:
        등록된 서버의 연결 상태

    Raises:
        HTTPException: 필수 필드 누락(400), 이름 중복(409), 등록 후 조회 실패(500)
    """
    if req.transport == "stdio" and not req.command:
        raise HTTPException(status_code=400, detail="stdio 서버는 command가 필요합니다")
    if req.transport == "sse" and not req.url:
        raise HTTPException(status_code=400, detail="sse 서버는 url이 필요합니다")

    raw = _read_config()
    servers = raw.setdefault("mcpServers", {})
    if req.name in servers:
        raise HTTPException(status_code=409, detail=f"이미 등록된 서버: {req.name}")

    if req.transport == "stdio":
        entry: dict[str, Any] = {"command": req.command, "args": req.args or []}
        if req.env:
            entry["env"] = req.env
        if req.cwd:
            entry["cwd"] = req.cwd
    else:
        entry = {"transport": "sse", "url": req.url}
        if req.headers:
            entry["headers"] = req.headers
    servers[req.name] = entry
    _write_config(raw)

    await reconnect_mcp()
    for item in await _list_servers():
        if item["name"] == req.name:
            return item
    raise HTTPException(status_code=500, detail="등록 후 서버를 찾을 수 없습니다")


@router.post("/reconnect")
async def reconnect_mcp_servers():
    """mcp_servers.json을 다시 읽어 전체 재연결

    Returns:
        재연결 후 서버 목록
    """
    await reconnect_mcp()
    return await _list_servers()


@router.get("/tools")
async def list_mcp_tools():
    """연결된 서버의 도구 목록 조회

    Returns:
        서버·도구 이름과 입력 스키마 목록
    """
    mcp = await get_mcp()
    tools = await mcp.list_tools(refresh=True)
    result = []
    for key, tool in tools.items():
        server, _, name = key.partition("__")
        result.append({
            "server": server,
            "name": name,
            "qualified_name": key,
            "description": tool.description or "",
            "input_schema": tool.inputSchema,
        })
    return result


@router.delete("/{name}", status_code=204)
async def delete_mcp_server(name: str):
    """MCP 서버 등록 해제 후 재연결

    Args:
        name: 해제할 서버 이름

    Returns:
        본문 없는 204 응답

    Raises:
        HTTPException: 등록되지 않은 서버(404)
    """
    raw = _read_config()
    servers = raw.get("mcpServers", {})
    if name not in servers:
        raise HTTPException(status_code=404, detail=f"등록되지 않은 서버: {name}")
    del servers[name]
    raw.get("capabilityProbes", {}).pop(name, None)
    _write_config(raw)
    await reconnect_mcp()
    return Response(status_code=204)
