"""MCP Tool 명세의 LLM 프로바이더별 tool_use 포맷 변환"""

from __future__ import annotations

from typing import Any

from mcp.types import Tool


def mcp_tools_to_anthropic(tools: dict[str, Tool]) -> list[dict[str, Any]]:
    """MCP Tool → Anthropic ToolParam 포맷"""
    return [
        {
            "name": name,
            "description": tool.description or "",
            "input_schema": tool.inputSchema or {"type": "object"},
        }
        for name, tool in tools.items()
    ]


def mcp_tools_to_openai(tools: dict[str, Tool]) -> list[dict[str, Any]]:
    """MCP Tool → OpenAI function calling 포맷"""
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": tool.description or "",
                "parameters": tool.inputSchema or {"type": "object"},
            },
        }
        for name, tool in tools.items()
    ]


def format_tool_summaries(tools: dict[str, Tool]) -> str:
    """도구 목록의 시스템 프롬프트용 요약 텍스트 변환"""
    if not tools:
        return "(사용 가능한 도구 없음)"

    lines: list[str] = []
    for name, tool in tools.items():
        desc = tool.description or "설명 없음"
        lines.append(f"- {name}: {desc}")
    return "\n".join(lines)
