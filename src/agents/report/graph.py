"""Report Agent subgraph — 요약 → 보고서 → DFXML 변환"""

from __future__ import annotations

from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from agents.report.nodes import dfxml_node, report_node, summary_node
from llm_provider.base import BaseLLMProvider


class ReportAgentState(TypedDict):
    """Report Agent subgraph 상태"""

    task_results: list[dict[str, Any]]
    """Sub-Agent들의 실행 결과 목록"""

    evidence_repository: list[dict[str, Any]]
    """Sub-Agent별 증거 산출물 목록 (artifact + format 구조)"""

    case_description: str
    """사건 개요 텍스트"""

    strategy: str
    """확정된 분석 전략 텍스트"""

    summary: str
    """생성된 요약 텍스트"""

    report: str
    """생성된 보고서 텍스트"""

    dfxml: str
    """생성된 DFXML XML 문자열"""


def build_report_graph(llm: BaseLLMProvider) -> Any:
    """Report Agent subgraph 빌드

    그래프 토폴로지:
        START → summary → report → dfxml → END

    Args:
        llm: LLM 프로바이더

    Returns:
        컴파일된 LangGraph subgraph
    """
    graph = StateGraph(ReportAgentState)

    graph.add_node("summary", partial(summary_node, llm=llm))
    graph.add_node("report", partial(report_node, llm=llm))
    graph.add_node("dfxml", partial(dfxml_node, llm=llm))

    graph.add_edge(START, "summary")
    graph.add_edge("summary", "report")
    graph.add_edge("report", "dfxml")
    graph.add_edge("dfxml", END)

    return graph.compile()


def create_report_state(
    task_results: list[dict[str, Any]],
    case_description: str = "",
    strategy: str = "",
    evidence_repository: list[dict[str, Any]] | None = None,
) -> ReportAgentState:
    """Report Agent 초기 상태 생성

    Args:
        task_results: Sub-Agent 실행 결과 목록
        case_description: 사건 개요
        strategy: 분석 전략
        evidence_repository: DFXML 프래그먼트 목록 (Evidence Repository)
    """
    return ReportAgentState(
        task_results=task_results,
        evidence_repository=evidence_repository or [],
        case_description=case_description,
        strategy=strategy,
        summary="",
        report="",
        dfxml="",
    )
