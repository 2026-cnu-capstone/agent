"""Report Agent 노드 — 요약, 보고서, DFXML 변환"""

from __future__ import annotations

from typing import Any

import structlog

from prompts.report import (
    build_dfxml_prompt,
    build_report_prompt,
    build_summary_prompt,
)
from llm_provider.base import BaseLLMProvider

logger = structlog.get_logger()


class ReportState:
    """Report Agent 내부 상태 키 상수"""

    SUMMARY = "summary"
    REPORT = "report"
    DFXML = "dfxml"


async def summary_node(
    state: dict[str, Any],
    *,
    llm: BaseLLMProvider,
) -> dict[str, Any]:
    """전체 TaskResult 기반 요약 생성

    Args:
        state: Report Agent 상태 (task_results, case_description 포함)
        llm: LLM 프로바이더
    """
    task_results = state.get("task_results", [])

    response = await llm.chat(
        messages=[{"role": "user", "content": "분석 결과를 요약해주세요."}],
        tools=None,
        system=build_summary_prompt(task_results),
    )
    summary = response.content if isinstance(response.content, str) else ""
    logger.info("summary_generated", length=len(summary))

    return {"summary": summary}


async def report_node(
    state: dict[str, Any],
    *,
    llm: BaseLLMProvider,
) -> dict[str, Any]:
    """포렌식 분석 보고서 생성

    summary_node가 생성한 요약이 있으면 이를 활용하여
    task_results 전문 재전달로 인한 토큰 낭비를 방지

    Args:
        state: Report Agent 상태 (task_results, case_description, strategy, summary 포함)
        llm: LLM 프로바이더
    """
    case_description = state.get("case_description", "")
    strategy = state.get("strategy", "")
    summary = state.get("summary", "")

    if summary:
        task_results = [{"task_id": "summary", "agent_name": "summary", "status": "success", "output": summary}]
    else:
        task_results = state.get("task_results", [])

    response = await llm.chat(
        messages=[{"role": "user", "content": "분석 보고서를 작성해주세요."}],
        tools=None,
        system=build_report_prompt(case_description, strategy, task_results),
    )
    report = response.content if isinstance(response.content, str) else ""
    logger.info("report_generated", length=len(report))

    return {"report": report}


async def dfxml_node(
    state: dict[str, Any],
    *,
    llm: BaseLLMProvider,
) -> dict[str, Any]:
    """분석 결과를 DFXML 스키마로 변환

    summary_node의 요약 결과를 기반으로 DFXML을 일괄 생성.
    per-agent DFXML 프래그먼트 생성이 제거되었으므로
    항상 요약 또는 task_results에서 직접 변환.

    Args:
        state: Report Agent 상태 (task_results, summary 포함)
        llm: LLM 프로바이더
    """
    summary = state.get("summary", "")

    if summary:
        task_results = [{"task_id": "summary", "agent_name": "summary", "status": "success", "output": summary}]
    else:
        task_results = state.get("task_results", [])

    response = await llm.chat(
        messages=[{"role": "user", "content": "분석 결과를 DFXML로 변환해주세요."}],
        tools=None,
        system=build_dfxml_prompt(task_results),
    )

    dfxml = response.content if isinstance(response.content, str) else ""
    logger.info("dfxml_generated", length=len(dfxml))

    return {"dfxml": dfxml}
