"""분석 계획 수립 테스트 스크립트

사건 정보를 입력하면 AI가 분석 계획을 출력합니다.
MCP 서버 없이 LLM만으로 동작합니다.

Usage:
    python scripts/run_planning.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config import LLMProvider, load_settings
from graph.agent import build_planning_graph, create_initial_state
from llm_provider.anthropic import AnthropicProvider
from llm_provider.openai import OpenAIProvider


def create_llm_provider(settings):
    if settings.llm.provider == LLMProvider.OPENAI:
        return OpenAIProvider(settings.llm, api_key=settings.llm_api_key)
    return AnthropicProvider(settings.llm, api_key=settings.llm_api_key)


def print_plan(plan: str) -> None:
    print("\n" + "=" * 60)
    print("  분석 계획")
    print("=" * 60)
    print(plan)
    print("=" * 60 + "\n")


async def main() -> None:
    settings = load_settings()

    if not settings.llm_api_key:
        print("[Error] LLM_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")
        return

    print(f"[Config] {settings.llm.provider.value} / {settings.llm.model}")

    llm = create_llm_provider(settings)
    graph = build_planning_graph(llm)

    print("\n===== 포렌식 분석 계획 수립 =====")
    print("사건 정보를 입력하세요. (quit으로 종료)\n")

    while True:
        sys.stdout.write("사건 입력 > ")
        sys.stdout.flush()
        user_input = sys.stdin.buffer.readline().decode("utf-8").strip()

        if not user_input or user_input.lower() in ("quit", "exit", "q"):
            print("종료합니다.")
            break

        print("\n계획 수립 중...")
        try:
            state = create_initial_state(user_input)
            result = await graph.ainvoke(state)
            print_plan(result["analysis_plan"])
        except Exception as e:
            print(f"\n[Error] {e}\n")


if __name__ == "__main__":
    asyncio.run(main())
