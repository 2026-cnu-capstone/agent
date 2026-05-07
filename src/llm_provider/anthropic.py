"""Anthropic (Claude) LLM 프로바이더"""

from __future__ import annotations

from typing import Any

import anthropic

from config import LLMConfig
from llm_provider.base import BaseLLMProvider, LLMResponse, ToolCall, ToolResult


class AnthropicProvider(BaseLLMProvider):
    """Claude API를 사용하는 LLM 프로바이더"""

    def __init__(self, config: LLMConfig, api_key: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._config = config

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> LLMResponse:
        """Claude messages API 호출"""
        kwargs: dict[str, Any] = {
            "model": self._config.model,
            "max_tokens": self._config.max_tokens,
            "temperature": self._config.temperature,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        response = await self._client.messages.create(**kwargs)
        return self._parse_response(response)

    def _parse_response(self, response: Any) -> LLMResponse:
        """Anthropic 응답의 LLMResponse 변환"""
        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                content_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=block.input if isinstance(block.input, dict) else {},
                    )
                )

        return LLMResponse(
            content="\n".join(content_parts),
            tool_calls=tool_calls,
            stop_reason=response.stop_reason or "",
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        )

    def format_tool_result(self, result: ToolResult) -> dict[str, Any]:
        """Anthropic tool_result 콘텐츠 블록 변환"""
        return {
            "type": "tool_result",
            "tool_use_id": result.tool_call_id,
            "content": result.content,
            **({"is_error": True} if result.is_error else {}),
        }

    def build_tool_results_message(self, results: list[ToolResult]) -> dict[str, Any]:
        """여러 ToolResult의 단일 user 메시지 병합"""
        return {
            "role": "user",
            "content": [self.format_tool_result(r) for r in results],
        }
