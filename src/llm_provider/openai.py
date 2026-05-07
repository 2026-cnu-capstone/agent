"""OpenAI (GPT-4o) LLM 프로바이더"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import openai
import structlog

from config import LLMConfig
from constants import LLM_INITIAL_BACKOFF, LLM_MAX_RETRIES
from llm_provider.base import BaseLLMProvider, LLMResponse, ToolCall, ToolResult

logger = structlog.get_logger()


class OpenAIProvider(BaseLLMProvider):
    """OpenAI API를 사용하는 LLM 프로바이더"""

    def __init__(self, config: LLMConfig, api_key: str) -> None:
        kwargs: dict[str, Any] = {"api_key": api_key}
        if config.base_url:
            kwargs["base_url"] = config.base_url
        self._client = openai.AsyncOpenAI(**kwargs)
        self._config = config

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> LLMResponse:
        """OpenAI chat completions API 호출 (429 에러 시 자동 재시도)"""
        api_messages = list(messages)
        if system:
            api_messages.insert(0, {"role": "system", "content": system})

        kwargs: dict[str, Any] = {
            "model": self._config.model,
            "max_tokens": self._config.max_tokens,
            "temperature": self._config.temperature,
            "messages": api_messages,
        }
        if tools:
            kwargs["tools"] = tools

        for attempt in range(LLM_MAX_RETRIES):
            try:
                response = await self._client.chat.completions.create(**kwargs)
                return self._parse_response(response)
            except openai.RateLimitError as exc:
                if attempt == LLM_MAX_RETRIES - 1:
                    raise
                wait = LLM_INITIAL_BACKOFF * (2 ** attempt)
                logger.warning(
                    "rate_limit_retry",
                    attempt=attempt + 1,
                    wait_seconds=wait,
                    error=str(exc),
                )
                await asyncio.sleep(wait)

        response = await self._client.chat.completions.create(**kwargs)
        return self._parse_response(response)

    def _parse_response(self, response: Any) -> LLMResponse:
        """OpenAI 응답의 LLMResponse 변환"""
        choice = response.choices[0]
        message = choice.message

        tool_calls: list[ToolCall] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=json.loads(tc.function.arguments)
                        if tc.function.arguments
                        else {},
                    )
                )

        usage = {}
        if response.usage:
            usage = {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
            }

        return LLMResponse(
            content=message.content or "",
            tool_calls=tool_calls,
            stop_reason=choice.finish_reason or "",
            usage=usage,
        )

    def format_tool_result(self, result: ToolResult) -> dict[str, Any]:
        """OpenAI tool 메시지 변환"""
        return {
            "role": "tool",
            "tool_call_id": result.tool_call_id,
            "content": result.content,
        }
