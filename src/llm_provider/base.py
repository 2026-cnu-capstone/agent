"""LLM 프로바이더 추상 인터페이스"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    """LLM이 요청한 도구 호출"""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    """도구 실행 결과"""

    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class LLMResponse:
    """LLM 응답"""

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""
    usage: dict[str, int] = field(default_factory=dict)


class BaseLLMProvider(ABC):
    """LLM 프로바이더 추상 클래스"""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> LLMResponse:
        """메시지 전송 및 응답 수신"""
        ...

    @abstractmethod
    def format_tool_result(self, result: ToolResult) -> dict[str, Any]:
        """ToolResult의 프로바이더 메시지 포맷 변환"""
        ...
