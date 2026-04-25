"""포렌식 에이전트 실행 스크립트

흐름:
    이미지 경로 입력 → 사건 입력
    → 전략 수립(HITL) → 계획 수립(HITL) → 에이전트 실행
    → 결과 요약 → 보고서 생성 / 추가 분석 / 종료

Usage:
    python scripts/run_agent.py
    python scripts/run_agent.py --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.shared import Pt

import structlog

from config import LLMProvider, load_settings
from database.engine import get_engine, init_db
from disk_image_validator import validate_image_path
from graph.agent import (
    build_agent_graph,
    build_pipeline_graph,
    build_planning_graph,
    build_strategy_graph,
    create_execution_state,
    create_initial_state,
    create_planning_state,
    parse_plan_steps,
)
from llm_provider.base import BaseLLMProvider
from llm_provider.anthropic import AnthropicProvider
from llm_provider.openai import OpenAIProvider
from mcp_client.client import MCPClientManager
from prompts.system import build_report_prompt, build_summary_prompt


# ── 로깅 ─────────────────────────────────────────────────

def configure_logging(verbose: bool) -> None:
    """로그 레벨 설정

    verbose=True 이면 DEBUG, 아니면 WARNING 레벨로 설정.
    structlog, dissect, mcp 각각의 레벨도 통일.
    """
    level = logging.DEBUG if verbose else logging.WARNING
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(level))
    logging.basicConfig(level=level, stream=sys.stderr)
    logging.getLogger("dissect").setLevel(level)
    logging.getLogger("mcp").setLevel(level)


# ── 입출력 헬퍼 ──────────────────────────────────────────

async def async_input(prompt: str = "") -> str:
    """asyncio 이벤트 루프 내에서 UTF-8 stdin 읽기"""
    if prompt:
        sys.stdout.write(prompt)
        sys.stdout.flush()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: sys.stdin.buffer.readline().decode("utf-8").strip()
    )


def print_section(title: str, content: str) -> None:
    """제목과 내용을 구분선으로 감싸서 출력 (전략/계획/결과 표시에 사용)"""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)
    print(content)
    print("=" * 60 + "\n")


def print_tools(tools: dict, verbose: bool = False) -> None:
    """발견된 MCP 도구 목록 출력

    verbose=True 이면 각 도구의 설명도 함께 출력.
    도구가 없으면 경고 메시지 표시 (에이전트 실행은 가능하지만 도구 호출 불가).
    """
    print(f"[MCP] 발견된 도구 {len(tools)}개")
    if verbose:
        for name, tool in tools.items():
            print(f"  - {name}: {tool.description or '(설명 없음)'}")

    if not tools:
        print("[Warning] 사용 가능한 도구가 없습니다.")


def extract_last_assistant_message(messages: list) -> str | None:
    """메시지 히스토리에서 마지막 assistant 텍스트 응답을 추출

    에이전트 실행 완료 후 최종 분석 결과 텍스트를 가져오는 데 사용.
    tool_calls만 있고 content가 없는 메시지는 건너뜀.
    """
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and msg.get("content"):
            return msg["content"]
    return None


def create_llm_provider(settings) -> BaseLLMProvider:
    """설정에 따라 Anthropic 또는 OpenAI LLM 프로바이더 인스턴스 반환

    설정의 llm.provider 값이 OPENAI이면 OpenAIProvider,
    그 외(기본값 Anthropic)이면 AnthropicProvider를 반환.
    """
    if settings.llm.provider == LLMProvider.OPENAI:
        return OpenAIProvider(settings.llm, api_key=settings.llm_api_key)
    return AnthropicProvider(settings.llm, api_key=settings.llm_api_key)


# ── 디스크 이미지 입력 ────────────────────────────────────

async def prompt_disk_image_path() -> tuple[str, str] | None:
    """유효한 디스크 이미지 경로를 입력받을 때까지 반복"""
    print("분석할 디스크 이미지 경로를 입력하세요. (지원 형식: E01, dd, raw)\n")

    while True:
        path_input = await async_input("Image path: ")
        if not path_input or path_input.lower() in ("quit", "exit", "q"):
            return None

        result = validate_image_path(path_input)
        if result.is_valid:
            assert result.format is not None
            return path_input.strip("'\""), result.format.value
        print(f"[Error] {result.error_message}\n")


# ── HITL 루프 ────────────────────────────────────────────

async def run_strategy_hitl(strategy_graph, user_input: str) -> str:
    """전략 수립 HITL(Human-in-the-Loop) 루프

    흐름:
        1. strategy_graph를 호출해 분석 전략 생성
        2. 전략을 사용자에게 출력
        3. y → 전략 확정, 텍스트 반환
        4. n → 피드백을 필수 입력받아 이전 전략 + 수정 요청을 합쳐 재생성 (1로 반복)

    Returns:
        사용자가 승인한 분석 전략 텍스트
    """
    print("\n[1단계] 전략 수립 중...")
    state = create_initial_state(user_input)

    while True:
        result = await strategy_graph.ainvoke(state)
        strategy = result["analysis_strategy"]
        print_section("분석 전략 (조사 대상 아티팩트)", strategy)

        choice = (await async_input("전략을 수락하시겠습니까? (y: 수락 / n: 수정) > ")).lower()
        if choice == "y":
            print("전략이 확정되었습니다.")
            return strategy

        feedback = ""
        while not feedback:
            feedback = await async_input("수정 방향을 입력하세요 > ")
            if not feedback:
                print("[알림] 수정 방향을 반드시 입력해야 합니다.")

        state = create_initial_state(
            f"{user_input}\n\n[이전 전략]\n{strategy}\n\n"
            f"[수정 요청]: {feedback}\n위 수정 요청을 반영하여 새로운 분석 전략을 작성하세요."
        )
        print("전략 재수립 중...")


async def run_planning_hitl(
    planning_graph,
    user_input: str,
    strategy: str,
    tools: dict,
    accumulated_results: list[dict] | None = None,
) -> str:
    """계획 수립 HITL(Human-in-the-Loop) 루프

    흐름:
        1. 사건 설명 + 전략 + (추가 분석 시) 이미 완료된 결과를 컨텍스트로 합산
        2. planning_graph를 호출해 단계별 실행 계획 생성
        3. 계획을 사용자에게 출력
        4. y → 계획 확정, 텍스트 반환
        5. n → 피드백을 필수 입력받아 이전 계획 + 수정 요청을 합쳐 재생성 (2로 반복)

    Args:
        accumulated_results: 이전 분석 회차 결과 목록. 추가 분석(a 선택) 시 컨텍스트로 활용.

    Returns:
        사용자가 승인한 세부 실행 계획 텍스트
    """
    print("\n[2단계] 계획 수립 중...")

    context = user_input
    if accumulated_results:
        done = "\n".join(f"- {r['step']}단계 {r.get('name','')}: 완료" for r in accumulated_results)
        context += f"\n\n[이미 완료된 분석]\n{done}\n추가 분석 계획을 수립하세요."

    plan_state = create_planning_state(context, strategy, tools)

    while True:
        result = await planning_graph.ainvoke(plan_state)
        analysis_plan = result["analysis_plan"]
        print_section("세부 실행 계획", analysis_plan)

        choice = (await async_input("계획을 수락하시겠습니까? (y: 수락 / n: 수정) > ")).lower()
        if choice == "y":
            print("계획이 확정되었습니다.")
            return analysis_plan

        feedback = ""
        while not feedback:
            feedback = await async_input("수정 방향을 입력하세요 > ")
            if not feedback:
                print("[알림] 수정 방향을 반드시 입력해야 합니다.")

        plan_state = create_planning_state(
            f"{context}\n\n[이전 계획]\n{analysis_plan}\n\n"
            f"[수정 요청]: {feedback}\n위 수정 요청을 반영하여 새로운 계획을 작성하세요.",
            strategy,
            tools,
        )
        print("계획 재수립 중...")


# ── LLM 요약/보고서 ───────────────────────────────────────

async def generate_summary(llm: BaseLLMProvider, step_results: list[dict]) -> str:
    """에이전트 분석 결과를 바탕으로 간략한 요약 생성

    build_summary_prompt로 시스템 프롬프트를 구성하고 LLM에게 요약을 요청.
    도구 없이 순수 텍스트 생성 모드로 호출.

    Returns:
        핵심 발견사항 요약 텍스트 (실패 시 빈 문자열)
    """
    response = await llm.chat(
        messages=[{"role": "user", "content": "분석 결과를 요약해주세요."}],
        tools=None,
        system=build_summary_prompt(step_results),
    )
    return response.content if isinstance(response.content, str) else ""


async def generate_report(
    llm: BaseLLMProvider,
    user_input: str,
    strategy: str,
    step_results: list[dict],
) -> str:
    """전체 분석 내용을 바탕으로 포렌식 보고서 생성

    사건 설명, 분석 전략, 단계별 결과를 모두 포함한 시스템 프롬프트를 구성하고
    LLM에게 정식 보고서 작성을 요청.
    도구 없이 순수 텍스트 생성 모드로 호출.

    Returns:
        포렌식 분석 보고서 텍스트 (실패 시 빈 문자열)
    """
    response = await llm.chat(
        messages=[{"role": "user", "content": "분석 보고서를 작성해주세요."}],
        tools=None,
        system=build_report_prompt(user_input, strategy, step_results),
    )
    return response.content if isinstance(response.content, str) else ""


def save_report(report: str, output_dir: Path | None = None) -> Path:
    """보고서를 docs 디렉터리에 Word(.docx) 파일로 저장

    파일명: report_YYYYMMDD_HHMMSS.docx
    output_dir 미지정 시 프로젝트 루트의 docs/ 사용.

    마크다운 헤더(# / ##)는 Word Heading 스타일로,
    나머지 줄은 Normal 단락으로 변환.

    Returns:
        저장된 파일 경로
    """
    if output_dir is None:
        output_dir = Path(__file__).parent.parent / "docs"
    output_dir.mkdir(parents=True, exist_ok=True)

    doc = Document()

    for line in report.splitlines():
        if line.startswith("# "):
            doc.add_heading(line[2:].strip(), level=1)
        elif line.startswith("## "):
            doc.add_heading(line[3:].strip(), level=2)
        elif line.startswith("### "):
            doc.add_heading(line[4:].strip(), level=3)
        elif line.strip() == "":
            doc.add_paragraph()
        else:
            p = doc.add_paragraph(line.strip())
            for run in p.runs:
                run.font.size = Pt(11)

    filename = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
    file_path = output_dir / filename
    doc.save(str(file_path))
    return file_path


# ── 메인 ─────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    """커맨드라인 인자 파싱

    Returns:
        파싱된 인자
    """
    parser = argparse.ArgumentParser(description="포렌식 에이전트 실행")
    parser.add_argument("--verbose", "-v", action="store_true", help="로그 출력")
    return parser.parse_args()


async def main() -> None:
    """에이전트 초기화 및 대화형 루프 실행

    전체 실행 흐름:
        1. 설정 로드 (LLM, MCP, DB)
        2. DB 초기화, LLM 프로바이더 생성
        3. MCP 서버 연결 → 도구 목록 조회
        4. 디스크 이미지 경로 입력 및 검증 (반복)
        5. [사건별 루프]
           a. 사건 개요 입력
           b. 전략 수립 HITL (run_strategy_hitl)
           c. [분석 반복 루프]
              - 계획 수립 HITL (run_planning_hitl)
              - agent_graph 실행 (ReAct 루프: llm ↔ tools)
              - 결과 요약 출력
              - 게이트: r=보고서 생성 / a=추가 분석 / q=종료
    """
    args = parse_args()
    configure_logging(args.verbose)

    config_path = Path(__file__).parent.parent / "config" / "mcp_servers.json"
    settings = load_settings(config_path)

    print(f"[Config] LLM: {settings.llm.provider.value} / {settings.llm.model}")
    print(f"[Config] MCP servers: {list(settings.mcp.servers.keys())}")

    if not settings.llm_api_key:
        print("[Error] LLM_API_KEY가 설정되지 않았습니다.")
        return
    if not settings.database_url:
        print("[Error] DATABASE_URL이 설정되지 않았습니다.")
        return

    db_engine = get_engine(settings.database_url)
    await init_db(db_engine)
    print("[DB] 데이터베이스 초기화 완료")

    llm = create_llm_provider(settings)
    strategy_graph = build_strategy_graph(llm)
    planning_graph = build_planning_graph(llm)

    async with MCPClientManager(settings.mcp) as mcp:
        tools = await mcp.list_tools()
        print_tools(tools, verbose=args.verbose)

        agent_graph = build_agent_graph(llm, mcp, db_engine)
        pipeline_graph = build_pipeline_graph(llm, mcp, db_engine)

        print("\n===== Forensic Agent =====")

        # 디스크 이미지 경로 입력
        image_result = await prompt_disk_image_path()
        if image_result is None:
            print("종료합니다.")
            await db_engine.dispose()
            return
        image_path, image_format = image_result
        print(f"\n[Image] {image_path} ({image_format.upper()}) 등록 완료")

        # 사건별 루프
        while True:
            print("\n사건 개요를 입력하세요. (quit으로 종료)")
            user_input = await async_input("사건 입력 > ")
            if not user_input or user_input.lower() in ("quit", "exit", "q"):
                print("종료합니다.")
                break

            try:
                # ── 1단계: 전략 수립 (HITL) ──────────────────────
                strategy = await run_strategy_hitl(strategy_graph, user_input)

                # ── 분석 반복 루프 (계획 → 실행 → 요약 → 게이트) ──
                accumulated_results: list[dict] = []
                planning_context = user_input  # 추가 분석 시 요청 내용을 덧붙여 갱신

                while True:
                    # 2단계: 계획 수립 (HITL)
                    analysis_plan = await run_planning_hitl(
                        planning_graph,
                        planning_context,
                        strategy,
                        tools,
                        accumulated_results or None,
                    )

                    # 3단계: 파이프라인 실행
                    plan_steps = parse_plan_steps(analysis_plan)
                    if not plan_steps:
                        print("[Warning] 실행 가능한 단계가 없습니다.")
                        break

                    print(f"\n[3단계] 파이프라인 실행 중... ({len(plan_steps)}단계)")
                    exec_state = create_execution_state(
                        user_message=user_input,
                        analysis_plan=analysis_plan,
                        plan_steps=plan_steps,
                        disk_image_path=image_path,
                        disk_image_format=image_format,
                    )
                    exec_result = await pipeline_graph.ainvoke(exec_state)
                    step_results: list[dict] = exec_result.get("step_results", [])
                    accumulated_results.extend(step_results)

                    # 결과 요약 생성 및 출력
                    print("\n[4단계] 결과 요약 중...")
                    summary = await generate_summary(llm, step_results)
                    print_section("분석 결과 요약", summary)

                    # 게이트: r=보고서 생성 / a=추가 분석 / q=종료
                    gate = (
                        await async_input(
                            "다음 작업을 선택하세요 (r: 보고서 생성 / a: 추가 분석 / q: 종료) > "
                        )
                    ).lower()

                    if gate == "r":
                        print("\n[5단계] 최종 보고서 작성 중...")
                        report = await generate_report(
                            llm, user_input, strategy, accumulated_results
                        )
                        print_section("포렌식 분석 보고서", report)
                        saved_path = save_report(report)
                        print(f"[보고서 저장] {saved_path}")
                        break
                    elif gate == "a":
                        # 추가 조사할 내용을 입력받아 계획 컨텍스트에 반영
                        additional = ""
                        while not additional:
                            additional = await async_input(
                                "추가로 조사할 내용을 입력하세요 > "
                            )
                            if not additional:
                                print("[알림] 조사할 내용을 입력해야 합니다.")
                        planning_context = (
                            f"{user_input}\n\n[추가 조사 요청]: {additional}"
                        )
                        continue
                    else:
                        # q 또는 기타
                        break

            except Exception as e:
                print(f"\n[Error] {e}\n")

    await db_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
