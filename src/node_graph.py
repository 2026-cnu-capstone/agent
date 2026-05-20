"""노드 그래프 빌드 및 업데이트 모듈

프론트엔드 DAG 시각화를 위한 노드/엣지 구조를 생성하고 갱신.
각 오케스트레이션 단계(strategy, planning, execution, report) 완료 시
해당 노드의 상태와 엣지 데이터를 업데이트.
"""

from __future__ import annotations

from typing import Any

from state.messages import NodeEdge, NodeRelation


def _truncate(text: str, max_len: int = 300) -> str:
    """텍스트를 최대 길이로 자르기

    Args:
        text: 원본 텍스트
        max_len: 최대 길이

    Returns:
        잘린 텍스트
    """
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def create_initial_graph() -> dict[str, Any]:
    """초기 노드 그래프 생성

    strategy와 planning 노드만 포함된 초기 그래프 반환.
    execution step 노드는 planning 완료 후 동적 추가.

    Returns:
        초기 노드 그래프 (nodes, edges)
    """
    strategy_node: NodeRelation = {
        "id": "strategy",
        "label": "분석 전략",
        "type": "strategy",
        "row": 0,
        "col": 0,
        "status": "pending",
        "inputs": [],
        "outputs": [],
    }
    planning_node: NodeRelation = {
        "id": "planning",
        "label": "실행 계획",
        "type": "planning",
        "row": 1,
        "col": 0,
        "status": "pending",
        "inputs": [],
        "outputs": [],
    }

    edge: NodeEdge = {
        "from_node": "strategy",
        "to_node": "planning",
        "data_key": "analysis_strategy",
        "data_summary": "",
    }

    strategy_node["outputs"] = [edge]
    planning_node["inputs"] = [edge]

    return {
        "nodes": [strategy_node, planning_node],
        "edges": [edge],
    }


def _find_node(graph: dict[str, Any], node_id: str) -> NodeRelation | None:
    """그래프에서 노드 ID로 검색

    Args:
        graph: 노드 그래프
        node_id: 검색할 노드 ID

    Returns:
        노드 또는 None
    """
    for node in graph.get("nodes", []):
        if node["id"] == node_id:
            return node
    return None


def update_strategy_done(
    graph: dict[str, Any],
    strategy_text: str,
) -> dict[str, Any]:
    """전략 수립 완료 시 그래프 업데이트

    Args:
        graph: 현재 노드 그래프
        strategy_text: 확정된 전략 텍스트

    Returns:
        업데이트된 노드 그래프
    """
    strategy = _find_node(graph, "strategy")
    if strategy:
        strategy["status"] = "success"

    planning = _find_node(graph, "planning")
    if planning:
        planning["status"] = "running"

    for edge in graph.get("edges", []):
        if edge["from_node"] == "strategy" and edge["to_node"] == "planning":
            edge["data_summary"] = _truncate(strategy_text)

    return graph


def update_planning_done(
    graph: dict[str, Any],
    plan_steps: list[dict[str, Any]],
) -> dict[str, Any]:
    """계획 수립 완료 시 그래프 업데이트

    plan_steps 기반으로 execution 노드와 report 노드를 동적 생성

    Args:
        graph: 현재 노드 그래프
        plan_steps: 파싱된 실행 단계 목록

    Returns:
        업데이트된 노드 그래프
    """
    planning = _find_node(graph, "planning")
    if planning:
        planning["status"] = "success"

    nodes: list[NodeRelation] = list(graph.get("nodes", []))
    edges: list[NodeEdge] = list(graph.get("edges", []))

    execution_start_row = 2
    prev_node_id = "planning"

    for i, step in enumerate(plan_steps):
        step_id = f"step_{i}"
        step_name = step.get("name", step.get("purpose", f"단계 {i + 1}"))

        edge_from_prev: NodeEdge = {
            "from_node": prev_node_id,
            "to_node": step_id,
            "data_key": "plan_step" if prev_node_id == "planning" else "task_result",
            "data_summary": "",
        }

        step_node: NodeRelation = {
            "id": step_id,
            "label": step_name,
            "type": "execution",
            "row": execution_start_row + i,
            "col": 0,
            "status": "pending",
            "inputs": [edge_from_prev],
            "outputs": [],
        }

        prev = _find_node({"nodes": nodes}, prev_node_id)
        if prev:
            prev["outputs"].append(edge_from_prev)

        nodes.append(step_node)
        edges.append(edge_from_prev)
        prev_node_id = step_id

    report_row = execution_start_row + len(plan_steps)
    report_node: NodeRelation = {
        "id": "report",
        "label": "보고서 생성",
        "type": "report",
        "row": report_row,
        "col": 0,
        "status": "pending",
        "inputs": [],
        "outputs": [],
    }

    for i in range(len(plan_steps)):
        step_id = f"step_{i}"
        edge_to_report: NodeEdge = {
            "from_node": step_id,
            "to_node": "report",
            "data_key": "task_results",
            "data_summary": "",
        }
        step_node = _find_node({"nodes": nodes}, step_id)
        if step_node:
            step_node["outputs"].append(edge_to_report)
        report_node["inputs"].append(edge_to_report)
        edges.append(edge_to_report)

    nodes.append(report_node)

    return {"nodes": nodes, "edges": edges}


def update_step_started(
    graph: dict[str, Any],
    step_index: int,
) -> dict[str, Any]:
    """실행 단계 시작 시 그래프 업데이트

    Args:
        graph: 현재 노드 그래프
        step_index: 시작된 단계 인덱스

    Returns:
        업데이트된 노드 그래프
    """
    step_node = _find_node(graph, f"step_{step_index}")
    if step_node:
        step_node["status"] = "running"
    return graph


def update_step_done(
    graph: dict[str, Any],
    step_index: int,
    status: str,
    output_summary: str = "",
) -> dict[str, Any]:
    """실행 단계 완료 시 그래프 업데이트

    Args:
        graph: 현재 노드 그래프
        step_index: 완료된 단계 인덱스
        status: 결과 상태 (success | error | skip)
        output_summary: 출력 요약 (다음 노드 엣지 data_summary에 사용)

    Returns:
        업데이트된 노드 그래프
    """
    step_id = f"step_{step_index}"
    step_node = _find_node(graph, step_id)
    if step_node:
        step_node["status"] = status

    if output_summary:
        summary = _truncate(output_summary)
        for edge in graph.get("edges", []):
            if edge["from_node"] == step_id:
                edge["data_summary"] = summary

    return graph


def add_followup_step(
    graph: dict[str, Any],
    step_index: int,
    step_name: str,
) -> dict[str, Any]:
    """follow-up 단계 동적 추가

    Args:
        graph: 현재 노드 그래프
        step_index: 새 단계의 인덱스
        step_name: 단계 이름

    Returns:
        업데이트된 노드 그래프
    """
    nodes: list[NodeRelation] = graph.get("nodes", [])
    edges: list[NodeEdge] = graph.get("edges", [])

    report_node = _find_node(graph, "report")
    report_row = report_node["row"] if report_node else step_index + 2

    prev_step_id = f"step_{step_index - 1}" if step_index > 0 else "planning"
    step_id = f"step_{step_index}"

    edge_from_prev: NodeEdge = {
        "from_node": prev_step_id,
        "to_node": step_id,
        "data_key": "task_result",
        "data_summary": "",
    }
    edge_to_report: NodeEdge = {
        "from_node": step_id,
        "to_node": "report",
        "data_key": "task_results",
        "data_summary": "",
    }

    new_node: NodeRelation = {
        "id": step_id,
        "label": step_name,
        "type": "execution",
        "row": report_row,
        "col": 0,
        "status": "pending",
        "inputs": [edge_from_prev],
        "outputs": [edge_to_report],
    }

    prev_node = _find_node(graph, prev_step_id)
    if prev_node:
        prev_node["outputs"].append(edge_from_prev)

    if report_node:
        report_node["row"] = report_row + 1
        report_node["inputs"].append(edge_to_report)

    nodes.append(new_node)
    edges.extend([edge_from_prev, edge_to_report])

    return {"nodes": nodes, "edges": edges}


def update_report_started(graph: dict[str, Any]) -> dict[str, Any]:
    """보고서 생성 시작 시 그래프 업데이트

    Args:
        graph: 현재 노드 그래프

    Returns:
        업데이트된 노드 그래프
    """
    report = _find_node(graph, "report")
    if report:
        report["status"] = "running"
    return graph


def update_report_done(graph: dict[str, Any]) -> dict[str, Any]:
    """보고서 생성 완료 시 그래프 업데이트

    Args:
        graph: 현재 노드 그래프

    Returns:
        업데이트된 노드 그래프
    """
    report = _find_node(graph, "report")
    if report:
        report["status"] = "success"
    return graph


def get_graph_response(graph: dict[str, Any]) -> dict[str, Any]:
    """프론트엔드 API 응답용 그래프 데이터 변환

    nodes와 edges를 분리된 리스트로 반환

    Args:
        graph: 내부 노드 그래프

    Returns:
        API 응답 형식 (nodes, edges)
    """
    nodes = []
    for node in graph.get("nodes", []):
        nodes.append({
            "id": node["id"],
            "label": node["label"],
            "type": node["type"],
            "row": node["row"],
            "col": node["col"],
            "status": node["status"],
        })

    edges = []
    for edge in graph.get("edges", []):
        edges.append({
            "from_node": edge["from_node"],
            "to_node": edge["to_node"],
            "data_key": edge["data_key"],
            "data_summary": edge["data_summary"],
        })

    return {"nodes": nodes, "edges": edges}
