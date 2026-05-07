"""Report Agent 노드 — 요약, 보고서, DFXML 변환"""

from __future__ import annotations

from typing import Any

import structlog

from prompts.report import (
    build_dfxml_merge_prompt,
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

    Args:
        state: Report Agent 상태 (task_results, case_description, strategy 포함)
        llm: LLM 프로바이더
    """
    task_results = state.get("task_results", [])
    case_description = state.get("case_description", "")
    strategy = state.get("strategy", "")

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

    Evidence Repository에 프래그먼트가 있으면 병합,
    없으면 task_results에서 전체 생성 (폴백)

    Args:
        state: Report Agent 상태 (task_results, evidence_repository 포함)
        llm: LLM 프로바이더
    """
    evidence_repo = state.get("evidence_repository", [])
    fragments = [
        e.get("artifact") or e.get("dfxml_fragment", "")
        for e in evidence_repo
        if e.get("artifact") or e.get("dfxml_fragment")
    ]

    if fragments:
        response = await llm.chat(
            messages=[{"role": "user", "content": "DFXML 프래그먼트를 병합해주세요."}],
            tools=None,
            system=build_dfxml_merge_prompt(fragments),
        )
    else:
        task_results = state.get("task_results", [])
        response = await llm.chat(
            messages=[{"role": "user", "content": "분석 결과를 DFXML로 변환해주세요."}],
            tools=None,
            system=build_dfxml_prompt(task_results),
        )

    dfxml = response.content if isinstance(response.content, str) else ""
    logger.info("dfxml_generated", length=len(dfxml), from_fragments=bool(fragments))

    return {"dfxml": dfxml}
