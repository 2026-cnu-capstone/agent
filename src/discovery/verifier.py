"""MCP 서버 실제 동작 검증

실행 파일이 존재한다는 사실만으로는 서버가 동작한다고 보장할 수 없어,
stdio 프로세스를 띄우고 `initialize` + `list_tools` 핸드셰이크까지 수행해
"사용 가능" 여부를 실증. 사용자에게 설치를 제안하기 전에 이 검증을 통과한
서버만 노출하면 잘못된 안내를 막을 수 있음

프로세스를 실제로 띄우므로 비용이 있어 명시적으로 요청할 때만 수행
"""

from __future__ import annotations

import asyncio

import structlog
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from discovery.types import MCPRecommendation, VerificationResult

logger = structlog.get_logger()

DEFAULT_VERIFY_TIMEOUT = 20.0


async def verify_stdio_server(
    command: str,
    args: tuple[str, ...] = (),
    timeout: float = DEFAULT_VERIFY_TIMEOUT,
) -> VerificationResult:
    """stdio MCP 서버를 띄워 핸드셰이크 성공 여부 확인

    Args:
        command: 서버 실행 명령
        args: 실행 인자
        timeout: 핸드셰이크 전체 타임아웃 (초)

    Returns:
        도구 개수와 성공 여부가 담긴 검증 결과
    """
    try:
        return await asyncio.wait_for(
            _handshake(command, args), timeout=timeout
        )
    except asyncio.TimeoutError:
        logger.warning("mcp_verify_timeout", command=command, timeout=timeout)
        return VerificationResult(
            ok=False, error=f"{timeout}초 내 핸드셰이크 미완료"
        )
    except Exception as exc:
        logger.warning("mcp_verify_failed", command=command, error=str(exc))
        return VerificationResult(ok=False, error=str(exc))


async def _handshake(command: str, args: tuple[str, ...]) -> VerificationResult:
    """MCP initialize + list_tools 수행

    Args:
        command: 서버 실행 명령
        args: 실행 인자

    Returns:
        검증 결과
    """
    params = StdioServerParameters(command=command, args=list(args))
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            init = await session.initialize()
            listed = await session.list_tools()
            tool_names = [tool.name for tool in listed.tools]
            return VerificationResult(
                ok=True,
                server_name=init.serverInfo.name,
                server_version=init.serverInfo.version,
                tool_count=len(tool_names),
                tool_names=tool_names[:20],
            )


async def verify_recommendations(
    recommendations: list[MCPRecommendation],
    timeout: float = DEFAULT_VERIFY_TIMEOUT,
) -> None:
    """실행 경로가 확인된 추천들을 순차 검증해 결과 반영

    anyio cancel scope가 생성 task에 묶이므로 병렬 대신 순차 검증
    (`MCPClientManager.connect_all`과 동일한 제약)

    Args:
        recommendations: 검증 대상 추천 목록 (제자리 수정)
        timeout: 서버당 핸드셰이크 타임아웃 (초)
    """
    for recommendation in recommendations:
        if not recommendation.resolved_command:
            continue
        result = await verify_stdio_server(
            recommendation.resolved_command,
            recommendation.candidate.args,
            timeout=timeout,
        )
        recommendation.verification = result
        logger.info(
            "mcp_verified",
            server=recommendation.candidate.server_id,
            ok=result.ok,
            tools=result.tool_count,
        )
