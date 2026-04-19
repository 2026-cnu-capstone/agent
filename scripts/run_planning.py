"""분석 전략 수립 및 계획 생성 스크립트

1단계: 사건 입력 → 분석 전략 수립 → 수락/거부
2단계: 전략 수락 → 세부 실행 계획 생성

Usage:
    python scripts/run_planning.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config import LLMProvider, load_settings
from graph.agent import (
    build_planning_graph,
    build_strategy_graph,
    create_initial_state,
    create_planning_state,
)
from llm_provider.anthropic import AnthropicProvider
from llm_provider.openai import OpenAIProvider


def create_llm_provider(settings):
    if settings.llm.provider == LLMProvider.OPENAI:
        return OpenAIProvider(settings.llm, api_key=settings.llm_api_key)
    return AnthropicProvider(settings.llm, api_key=settings.llm_api_key)


def read_input() -> str:
    sys.stdout.flush()
    return sys.stdin.buffer.readline().decode("utf-8").strip()


def print_section(title: str, content: str) -> None:
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)
    print(content)
    print("=" * 60 + "\n")


async def main() -> None:
    settings = load_settings()

    if not settings.llm_api_key:
        print("[Error] LLM_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")
        return

    print(f"[Config] {settings.llm.provider.value} / {settings.llm.model}")

    llm = create_llm_provider(settings)
    strategy_graph = build_strategy_graph(llm)
    planning_graph = build_planning_graph(llm)

    print("\n===== 포렌식 분석 전략 및 계획 수립 =====")
    print("사건 정보를 입력하세요. (quit으로 종료)\n")

    while True:
        sys.stdout.write("사건 입력 > ")
        user_input = read_input()

        if not user_input or user_input.lower() in ("quit", "exit", "q"):
            print("종료합니다.")
            break

        try:
            # ── 1단계: 전략 수립 ──────────────────────────────
            print("\n전략 수립 중...")
            state = create_initial_state(user_input)

            while True:
                result = await strategy_graph.ainvoke(state)
                strategy = result["analysis_strategy"]
                print_section("분석 전략", strategy)

                sys.stdout.write("전략을 수락하시겠습니까? (y: 수락 / n: 재생성) > ")
                choice = read_input().lower()

                if choice == "y":
                    print("\n전략이 확정되었습니다.")
                    break

                sys.stdout.write("수정 요청사항을 입력하세요 (없으면 Enter) > ")
                feedback = read_input()

                if feedback:
                    revised = (
                        f"{user_input}\n\n"
                        f"[이전 전략]\n{strategy}\n\n"
                        f"[수정 요청]: {feedback}\n"
                        f"위 수정 요청을 반영하여 이전 전략과 다른 새로운 분석 전략을 작성하세요."
                    )
                else:
                    revised = (
                        f"{user_input}\n\n"
                        f"[이전 전략]\n{strategy}\n\n"
                        f"이전 전략과 다른 접근 방식으로 새로운 분석 전략을 작성하세요. "
                        f"조사 우선순위와 방법론을 바꿔보세요."
                    )

                print("\n전략 재수립 중...")
                state = create_initial_state(revised)

            # ── 2단계: 계획 수립 ──────────────────────────────
            print("\n전략을 바탕으로 세부 계획 수립 중...")
            plan_state = create_planning_state(user_input, strategy)
            plan_result = await planning_graph.ainvoke(plan_state)
            print_section("세부 실행 계획", plan_result["analysis_plan"])

        except Exception as e:
            print(f"\n[Error] {e}\n")


if __name__ == "__main__":
    asyncio.run(main())
