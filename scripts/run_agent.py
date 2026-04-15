"""에이전트 실행 스크립트

Usage:
    uv run python scripts/run_agent.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from config import LLMProvider, load_settings
from graph.agent import build_agent_graph, create_initial_state
from llm_provider.anthropic import AnthropicProvider
from llm_provider.openai import OpenAIProvider
from mcp_client.client import MCPClientManager


async def async_input(prompt: str) -> str:
    """asyncio 호환 stdin 입력

    asyncio 이벤트 루프 내에서 blocking stdin을 읽기 위해
    executor에서 실행하며, 바이트 레벨 UTF-8 디코딩으로
    한글 인코딩 문제를 우회

    Args:
        prompt: 사용하지 않으나 인터페이스 호환을 위해 유지

    Returns:
        사용자가 입력한 문자열 (양쪽 공백 제거)
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: sys.stdin.buffer.readline().decode("utf-8").strip()
    )


def create_llm_provider(settings):
    """설정에 따라 적절한 LLM 프로바이더를 생성

    Args:
        settings: 로드된 Settings 인스턴스

    Returns:
        AnthropicProvider 또는 OpenAIProvider 인스턴스
    """
    if settings.llm.provider == LLMProvider.OPENAI:
        return OpenAIProvider(settings.llm, api_key=settings.llm_api_key)
    return AnthropicProvider(settings.llm, api_key=settings.llm_api_key)


def print_tools(tools):
    """발견된 MCP 도구 목록을 출력

    Args:
        tools: 도구 이름을 키로, Tool 객체를 값으로 갖는 딕셔너리
    """
    print(f"[MCP] 발견된 도구 {len(tools)}개:")
    for name, tool in tools.items():
        print(f"  - {name}: {tool.description or '(설명 없음)'}")

    if not tools:
        print("[Warning] 사용 가능한 도구가 없습니다.")


def extract_last_assistant_message(messages):
    """메시지 목록에서 마지막 assistant 응답을 추출

    Args:
        messages: 대화 메시지 딕셔너리 목록

    Returns:
        마지막 assistant 메시지의 content 문자열, 없으면 None
    """
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and msg.get("content"):
            return msg["content"]
    return None


async def main() -> None:
    """에이전트 초기화 및 대화형 루프 실행"""
    config_path = Path(__file__).parent.parent / "config" / "mcp_servers.json"
    settings = load_settings(config_path)

    print(f"[Config] LLM: {settings.llm.provider.value} / {settings.llm.model}")
    print(f"[Config] MCP servers: {list(settings.mcp.servers.keys())}")

    if not settings.llm_api_key:
        print("[Error] LLM_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")
        return

    llm = create_llm_provider(settings)

    async with MCPClientManager(settings.mcp) as mcp:
        tools = await mcp.list_tools()
        print_tools(tools)

        graph = build_agent_graph(llm, mcp)

        print("\n===== Forensic Agent =====")
        print("디스크 이미지 분석 요청을 입력하세요. (quit으로 종료)\n")

        while True:
            sys.stdout.write("You: ")
            sys.stdout.flush()
            user_input = await async_input("")
            if not user_input or user_input.lower() in ("quit", "exit", "q"):
                print("종료합니다.")
                break

            state = create_initial_state(user_input)
            try:
                result = await graph.ainvoke(state)
                reply = extract_last_assistant_message(result["messages"])
                if reply:
                    print(f"\nAgent: {reply}\n")
            except Exception as e:
                print(f"\n[Error] {e}\n")


if __name__ == "__main__":
    asyncio.run(main())
