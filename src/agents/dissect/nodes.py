"""Dissect Sub-Agent 전용 노드

output summarizer를 연동하여 대용량 도구 출력을 자동 요약
"""

from __future__ import annotations

from typing import Any

import structlog

from constants import DEFAULT_MAX_ROWS, MAX_ROWS_PER_PLUGIN
from llm_provider.base import BaseLLMProvider, ToolResult
from mcp_client.client import MCPClientManager
from output.summarizer import summarize_output
from state.sub_agent import SubAgentState

logger = structlog.get_logger()

HEAVY_PLUGINS = {
    "os.windows.regf.regf",
    "os.windows.log.evtx.evtx",
}

METADATA_TOOLS = {
    "dissect__list_plugins",
    "dissect__list_targets",
    "dissect__open_target",
    "dissect__close_target",
}


def _enforce_limit(tool_name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """query_plugin 호출 시 대용량 플러그인에 limit 기본값 강제 주입

    Args:
        tool_name: MCP 도구 키
        arguments: 도구 호출 인자
    """
    args = dict(arguments) if arguments else {}
    plugin_name = args.get("plugin_name", "")

    if "query_plugin" in tool_name:
        if plugin_name in HEAVY_PLUGINS and not args.get("limit"):
            args["limit"] = DEFAULT_MAX_ROWS
            logger.info("limit_enforced", tool=tool_name, plugin=plugin_name, limit=DEFAULT_MAX_ROWS)
        elif not args.get("limit"):
            args["limit"] = DEFAULT_MAX_ROWS
            logger.info("limit_enforced", tool=tool_name, limit=DEFAULT_MAX_ROWS)

    return args


async def dissect_tool_node(
    state: SubAgentState,
    *,
    llm: BaseLLMProvider,
    mcp: MCPClientManager,
) -> dict[str, Any]:
    """Dissect 전용 도구 실행 노드

    기본 sub_agent_tool_node와 동일하되, 도구 결과에
    output summarizer를 자동 적용하고 대용량 플러그인에
    max_rows 기본값을 강제 주입

    Args:
        state: Sub-Agent 상태
        llm: LLM 프로바이더 (결과 포맷팅 및 요약용)
        mcp: Dissect MCP 클라이언트
    """
    from llm_provider.anthropic import AnthropicProvider

    results: list[ToolResult] = []
    summarized_chunks: list[str] = []

    for tc in state["pending_tool_calls"]:
        try:
            safe_args = _enforce_limit(tc["name"], tc.get("arguments"))
            call_result = await mcp.call_tool(tc["name"], safe_args)
            raw_content = mcp.get_tool_result_text(call_result)
            is_error = bool(call_result.isError)
        except Exception as exc:
            logger.error("dissect_tool_failed", tool=tc["name"], error=str(exc))
            raw_content = f"Error: {exc}"
            is_error = True

        if not is_error:
            purpose = state["task"].get("step", {}).get("purpose", "")
            content = await summarize_output(
                output=raw_content,
                tool_name=tc["name"],
                purpose=purpose,
                llm=llm,
            )
            if tc["name"] not in METADATA_TOOLS:
                summarized_chunks.append(content)
        else:
            content = raw_content

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
        "output_chunks": state["output_chunks"] + summarized_chunks,
    }
