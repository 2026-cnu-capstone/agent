"""다중 MCP 서버 연결을 관리하는 클라이언트 매니저"""

from __future__ import annotations

import json
from contextlib import AsyncExitStack
from typing import Any

import structlog
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, Tool
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import MCPConfig, SSEServerConfig, StdioServerConfig
from mcp_client.tool_cache import ToolCache

logger = structlog.get_logger()


class MCPConnectionError(Exception):
    """MCP 서버 연결 실패"""


class MCPToolCallError(Exception):
    """MCP 도구 호출 실패"""


class MCPClientManager:
    """다중 MCP 서버 연결, 도구 조회 및 호출 관리"""

    def __init__(self, config: MCPConfig) -> None:
        self._config = config
        self._sessions: dict[str, ClientSession] = {}
        self._tool_to_server: dict[str, str] = {}
        self._tool_cache = ToolCache(ttl_seconds=config.tool_cache_ttl_seconds)
        self._exit_stack = AsyncExitStack()

    async def __aenter__(self) -> MCPClientManager:
        await self.connect_all()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.disconnect_all()

    async def connect_all(self) -> None:
        """설정된 모든 MCP 서버 순차 연결

        anyio TaskGroup/cancel scope는 생성된 task와 동일한 task에서
        종료되어야 하므로 병렬 연결 대신 순차 연결 사용
        """
        for name, config in self._config.servers.items():
            try:
                await self._connect_server(name, config)
            except Exception:
                logger.error(
                    "mcp_server_connection_failed",
                    server=name,
                    exc_info=True,
                )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(
            (ConnectionError, OSError, TimeoutError)
        ),
        reraise=True,
    )
    async def _connect_server(
        self, name: str, config: StdioServerConfig | SSEServerConfig
    ) -> None:
        """단일 MCP 서버 연결, 실패 시 재시도"""
        try:
            if config.transport == "stdio":
                params = StdioServerParameters(
                    command=config.command,
                    args=config.args,
                    env=config.env,
                )
                streams = await self._exit_stack.enter_async_context(
                    stdio_client(params)
                )
            else:
                streams = await self._exit_stack.enter_async_context(
                    sse_client(
                        url=config.url,
                        headers=config.headers or {},
                        timeout=config.timeout,
                        sse_read_timeout=config.sse_read_timeout,
                    )
                )

            read_stream, write_stream = streams
            session = await self._exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()
            self._sessions[name] = session
            logger.info("mcp_server_connected", server=name)

        except Exception as exc:
            raise MCPConnectionError(
                f"Failed to connect to MCP server '{name}': {exc}"
            ) from exc

    async def disconnect_all(self) -> None:
        """모든 MCP 서버 연결 종료"""
        await self._exit_stack.aclose()
        self._sessions.clear()
        self._tool_to_server.clear()
        self._tool_cache.invalidate()
        logger.info("all_mcp_servers_disconnected")

    async def list_tools(
        self, *, refresh: bool = False
    ) -> dict[str, Tool]:
        """연결된 모든 서버의 도구 목록 반환 (캐시 활용)"""
        if not refresh:
            cached = self._tool_cache.get_all()
            if cached is not None:
                return cached

        all_tools: dict[str, Tool] = {}
        self._tool_to_server.clear()

        for name, session in self._sessions.items():
            try:
                result = await session.list_tools()
                for tool in result.tools:
                    tool_key = f"{name}__{tool.name}"
                    all_tools[tool_key] = tool
                    self._tool_to_server[tool_key] = name
            except Exception:
                logger.warning(
                    "list_tools_failed", server=name, exc_info=True
                )

        self._tool_cache.update(all_tools)
        return all_tools

    async def call_tool(
        self, tool_key: str, arguments: dict[str, Any] | None = None
    ) -> CallToolResult:
        """도구 호출 및 결과 반환

        Args:
            tool_key: "{server_name}__{tool_name}" 형식의 도구 키
            arguments: 도구에 전달할 인자
        """
        server_name = self._tool_to_server.get(tool_key)
        if server_name is None:
            raise MCPToolCallError(f"Unknown tool: {tool_key}")

        session = self._sessions.get(server_name)
        if session is None:
            raise MCPToolCallError(
                f"Server not connected: {server_name}"
            )

        tool_name = tool_key.split("__", 1)[1]

        try:
            result = await session.call_tool(
                tool_name, arguments=arguments
            )
            logger.info(
                "tool_call_completed",
                tool=tool_key,
                is_error=result.isError,
            )
            return result
        except Exception as exc:
            raise MCPToolCallError(
                f"Tool call failed: {tool_key} - {exc}"
            ) from exc

    def get_tool_result_text(self, result: CallToolResult) -> str:
        """CallToolResult에서 텍스트 콘텐츠 추출"""
        parts: list[str] = []
        for content in result.content:
            if hasattr(content, "text"):
                parts.append(content.text)
            else:
                parts.append(
                    json.dumps(
                        content.model_dump(), ensure_ascii=False
                    )
                )
        return "\n".join(parts)

    @property
    def connected_servers(self) -> list[str]:
        """현재 연결된 서버 이름 목록"""
        return list(self._sessions.keys())
