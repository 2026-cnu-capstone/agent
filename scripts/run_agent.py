"""에이전트 실행 스크립트

Usage:
    uv run python scripts/run_agent.py
    uv run python scripts/run_agent.py --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import structlog

from config import LLMProvider, load_settings
from database.engine import get_engine, init_db
from disk_image_validator import validate_image_path
from graph.agent import build_agent_graph, create_initial_state
from llm_provider.anthropic import AnthropicProvider
from llm_provider.openai import OpenAIProvider
from mcp_client.client import MCPClientManager


def configure_logging(verbose: bool) -> None:
    """로그 출력 레벨 설정

    Args:
        verbose: True면 전체 로그 출력, False면 WARNING 이상만 출력
    """
    level = logging.DEBUG if verbose else logging.WARNING

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
    )

    logging.basicConfig(level=level, stream=sys.stderr)
    logging.getLogger("dissect").setLevel(level)
    logging.getLogger("mcp").setLevel(level)


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
        None, lambda: sys.stdin.readline().strip()
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


def print_tools(tools, verbose: bool = False):
    """발견된 MCP 도구 목록을 출력

    Args:
        tools: 도구 이름을 키로, Tool 객체를 값으로 갖는 딕셔너리
        verbose: 상세 출력 여부
    """
    print(f"[MCP] 발견된 도구 {len(tools)}개")
    if verbose:
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


async def prompt_disk_image_path() -> tuple[str, str] | None:
    """디스크 이미지 경로를 사용자로부터 입력받아 검증

    경로가 유효할 때까지 반복 질의하며,
    quit 입력 시 None을 반환하여 종료 신호 전달

    Returns:
        (검증된 경로, 형식) 튜플 또는 None (종료 시)
    """
    print("분석할 디스크 이미지 경로를 입력하세요. (지원 형식: E01, dd, raw)\n")

    while True:
        sys.stdout.write("Image path: ")
        sys.stdout.flush()
        path_input = await async_input("")
        if not path_input or path_input.lower() in ("quit", "exit", "q"):
            return None

        result = validate_image_path(path_input)
        if result.is_valid:
            assert result.format is not None
            cleaned_path = path_input.strip("'\"")
            return cleaned_path, result.format.value

        print(f"[Error] {result.error_message}")
        print("다시 입력해주세요.\n")


def parse_args() -> argparse.Namespace:
    """커맨드라인 인자 파싱

    Returns:
        파싱된 인자
    """
    parser = argparse.ArgumentParser(description="포렌식 에이전트 실행")
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="시스템 로그 메시지 출력 (기본: 숨김)",
    )
    return parser.parse_args()


async def main() -> None:
    """에이전트 초기화 및 대화형 루프 실행"""
    args = parse_args()
    configure_logging(args.verbose)

    config_path = Path(__file__).parent.parent / "config" / "mcp_servers.json"
    settings = load_settings(config_path)

    print(f"[Config] LLM: {settings.llm.provider.value} / {settings.llm.model}")
    print(f"[Config] MCP servers: {list(settings.mcp.servers.keys())}")

    if not settings.llm_api_key:
        print("[Error] LLM_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")
        return

    if not settings.database_url:
        print("[Error] DATABASE_URL이 설정되지 않았습니다. .env 파일을 확인하세요.")
        return

    db_engine = get_engine(settings.database_url)
    await init_db(db_engine)
    print("[DB] 데이터베이스 연결 및 테이블 초기화 완료")

    llm = create_llm_provider(settings)

    async with MCPClientManager(settings.mcp) as mcp:
        tools = await mcp.list_tools()
        print_tools(tools, verbose=args.verbose)

        graph = build_agent_graph(llm, mcp, db_engine)

        print("\n===== Forensic Agent =====")

        image_result = await prompt_disk_image_path()
        if image_result is None:
            print("종료합니다.")
            return

        image_path, image_format = image_result
        print(f"\n[Image] {image_path} (형식: {image_format.upper()}) 등록 완료")
        print("사건 개요를 입력하세요. (quit으로 종료)\n")

        while True:
            sys.stdout.write("You: ")
            sys.stdout.flush()
            user_input = await async_input("")
            if not user_input or user_input.lower() in ("quit", "exit", "q"):
                print("종료합니다.")
                break

            state = create_initial_state(
                user_message=user_input,
                disk_image_path=image_path,
                disk_image_format=image_format,
            )
            try:
                result = await graph.ainvoke(state)
                reply = extract_last_assistant_message(result["messages"])
                if reply:
                    print(f"\nAgent: {reply}\n")
            except Exception as e:
                print(f"\n[Error] {e}\n")

    await db_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
