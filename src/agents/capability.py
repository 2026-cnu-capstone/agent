"""MCP 서버 중립적 역량 수집 및 계획 검증

계획 단계가 환경에 없는 기능을 요구해 실행 시점에 실패하는 것을 막는다.
특정 MCP 서버에 의존하지 않도록 네 계층으로 나누어 근거를 모은다.

1. **도구명** — `list_tools()`는 MCP 프로토콜이 보장하므로 항상 검증 가능
2. **스키마 enum** — `inputSchema`에 `enum`이 선언된 파라미터의 허용값
3. **탐색 도구 프로브** — 설정에 선언한 도구를 호출해 하위 기능명 수집 (opt-in)
4. **실패 분류** — 실행 시 발생한 미존재 기능 오류를 역량 미충족으로 기록

검증은 **긍정적 지식이 있을 때만 거부**한다. 알 수 없는 서버에서 계획을 막으면
이식성이 오히려 나빠지므로, 수집 실패 시에는 통과시킨다
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()

PROBE_NAME_HINTS: tuple[str, ...] = (
    "list_plugins",
    "list_functions",
    "list_capabilities",
    "list_commands",
    "list_modules",
)
"""설정 선언이 없을 때 탐색 도구로 추정할 이름 (규약 기반 폴백)"""

MISSING_CAPABILITY_PATTERNS: tuple[str, ...] = (
    r"not found",
    r"unknown (tool|function|plugin)",
    r"no such (tool|function|plugin)",
    r"찾을 수 없",
)
"""실행 오류가 역량 미충족임을 나타내는 패턴"""


@dataclass
class Capabilities:
    """환경이 실제로 제공하는 기능 집합

    Attributes:
        tool_names: 연결된 모든 MCP 도구 이름 (`server__tool` 형식)
        bare_tool_names: 서버 접두사를 뗀 도구 이름
        enum_values: 도구별 파라미터 enum 허용값
        probed_names: 탐색 도구로 수집한 하위 기능명 (플러그인 등)
        probe_available: 탐색 도구 호출이 성공했는지 여부
    """

    tool_names: set[str] = field(default_factory=set)
    bare_tool_names: set[str] = field(default_factory=set)
    enum_values: dict[str, set[str]] = field(default_factory=dict)
    probed_names: set[str] = field(default_factory=set)
    probe_available: bool = False

    def knows_subfunction(self, name: str) -> bool | None:
        """하위 기능명의 존재 여부 판정

        Args:
            name: 플러그인/함수명

        Returns:
            존재하면 True, 없으면 False, 판정 근거가 없으면 None
        """
        if not self.probe_available or not self.probed_names:
            return None

        candidate = name.strip().lower()
        if not candidate:
            return None
        tail = candidate.rsplit(".", 1)[-1]
        for known in self.probed_names:
            lowered = known.lower()
            if candidate == lowered or tail == lowered.rsplit(".", 1)[-1]:
                return True
        return False


@dataclass
class PlanIssue:
    """계획 단계에서 발견한 역량 문제

    Attributes:
        step_index: 문제가 있는 단계 인덱스
        kind: 문제 유형 (unknown_tool | unknown_subfunction | unknown_server)
        name: 문제가 된 이름
        detail: 사용자에게 보여줄 설명
    """

    step_index: int
    kind: str
    name: str
    detail: str


async def collect_capabilities(
    mcp: Any,
    probes: dict[str, dict[str, Any]] | None = None,
    probe_args: dict[str, Any] | None = None,
) -> Capabilities:
    """연결된 MCP 서버에서 역량 정보 수집

    Args:
        mcp: MCP 클라이언트 매니저
        probes: 서버별 탐색 도구 선언 (`{"dissect": {"tool": "list_plugins"}}`)
        probe_args: 탐색 도구 호출 시 전달할 공통 인자 (예: `{"target_id": "..."}`)

    Returns:
        수집된 역량 집합
    """
    capabilities = Capabilities()

    try:
        tools = await mcp.list_tools()
    except Exception as exc:
        logger.warning("capability_tool_list_failed", error=str(exc))
        return capabilities

    for key, tool in tools.items():
        capabilities.tool_names.add(key)
        capabilities.bare_tool_names.add(key.split("__", 1)[-1])
        schema = getattr(tool, "inputSchema", None) or {}
        for param, spec in (schema.get("properties") or {}).items():
            if isinstance(spec, dict) and isinstance(spec.get("enum"), list):
                allowed = {str(v) for v in spec["enum"]}
                capabilities.enum_values[f"{key}.{param}"] = allowed

    for probe_key, call_args in _resolve_probe_tools(
        capabilities.tool_names, probes, probe_args
    ):
        try:
            result = await mcp.call_tool(probe_key, call_args)
            text = mcp.get_tool_result_text(result)
            failed = bool(getattr(result, "isError", False))
        except Exception as exc:
            logger.info("capability_probe_skipped", tool=probe_key, error=str(exc))
            continue

        if failed or _looks_like_error(text):
            logger.info(
                "capability_probe_returned_error",
                tool=probe_key,
                preview=text[:120],
            )
            continue

        names = _extract_names(text)
        if names:
            capabilities.probed_names |= names
            capabilities.probe_available = True

    logger.info(
        "capabilities_collected",
        tools=len(capabilities.tool_names),
        enums=len(capabilities.enum_values),
        probed=len(capabilities.probed_names),
    )
    return capabilities


def _resolve_probe_tools(
    tool_names: set[str],
    probes: dict[str, dict[str, Any]] | None,
    probe_args: dict[str, Any] | None,
) -> list[tuple[str, dict[str, Any]]]:
    """호출할 탐색 도구 키 목록 결정

    설정 선언을 우선하고, 없으면 이름 규약으로 추정한다

    Args:
        tool_names: 연결된 도구 키 집합
        probes: 서버별 탐색 도구 선언
        probe_args: 호출 시 전달할 공통 인자

    Returns:
        (도구 키, 호출 인자) 목록
    """
    base_args = dict(probe_args or {})
    resolved: list[tuple[str, dict[str, Any]]] = []

    for server, spec in (probes or {}).items():
        tool = spec.get("tool")
        if not tool:
            continue
        key = tool if "__" in tool else f"{server}__{tool}"
        if key not in tool_names:
            continue
        args = dict(base_args)
        declared = spec.get("args")
        if isinstance(declared, dict):
            args.update(declared)
        resolved.append((key, args))

    if resolved:
        return resolved

    for key in sorted(tool_names):
        if key.split("__", 1)[-1] in PROBE_NAME_HINTS:
            resolved.append((key, dict(base_args)))
    return resolved


def _looks_like_error(text: str) -> bool:
    """탐색 도구 응답이 오류인지 판별

    스키마 검증 실패 메시지를 기능명으로 오인해 정상 계획을 거부하는 것을
    막기 위한 방어. 오류로 판단되면 프로브를 사용하지 않는다

    Args:
        text: 탐색 도구 응답 텍스트

    Returns:
        오류로 보이면 True
    """
    lowered = text[:600].lower()
    markers = (
        "validation error",
        "field required",
        "error executing tool",
        '"error":',
        "traceback",
        "pydantic",
    )
    return any(marker in lowered for marker in markers)


def _extract_names(text: str) -> set[str]:
    """탐색 도구 출력에서 기능명 추출

    구조화된 JSON의 name 계열 키만 신뢰한다. 자유 텍스트를 정규식으로 긁으면
    오류 메시지를 기능명으로 오인해 정상 계획을 거부하게 되므로 폴백을 두지 않음

    Args:
        text: 탐색 도구가 반환한 텍스트

    Returns:
        수집된 기능명 집합
    """
    names: set[str] = set()

    def walk(node: Any) -> None:
        """중첩 구조를 순회하며 name 계열 값 수집"""
        if isinstance(node, dict):
            if node.get("is_callable") is False:
                return
            for key, value in node.items():
                if key in ("name", "plugin", "function", "id") and isinstance(
                    value, str
                ):
                    names.add(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for document in _iter_json_documents(text):
        walk(document)

    return names


def _iter_json_documents(text: str) -> list[Any]:
    """텍스트에서 JSON 문서를 순차적으로 파싱

    MCP 도구는 레코드를 콘텐츠 블록별로 반환하므로 결과 텍스트가 JSON 객체
    여러 개가 이어붙은 형태일 수 있다. 단일 `json.loads`로는 파싱되지 않으므로
    `raw_decode`로 앞에서부터 차례로 소비

    Args:
        text: 도구 응답 텍스트

    Returns:
        파싱된 JSON 문서 목록 (없으면 빈 리스트)
    """
    decoder = json.JSONDecoder()
    documents: list[Any] = []
    index = 0
    length = len(text)

    while index < length:
        while index < length and text[index] in " \t\r\n,":
            index += 1
        if index >= length:
            break
        try:
            document, end = decoder.raw_decode(text, index)
        except ValueError:
            break
        documents.append(document)
        index = end

    return documents


def validate_plan(
    steps: list[dict[str, Any]], capabilities: Capabilities
) -> list[PlanIssue]:
    """계획 단계가 요구하는 기능이 환경에 있는지 검증

    거부는 존재하지 않음을 확인했을 때만 한다. 판정 근거가 없으면 통과시킨다

    Args:
        steps: 계획 단계 목록
        capabilities: 수집된 역량 집합

    Returns:
        발견된 문제 목록 (없으면 빈 리스트)
    """
    issues: list[PlanIssue] = []

    for index, step in enumerate(steps):
        server = str(step.get("mcp_server") or "").strip()
        if server and server.lower() not in ("none", "manual", ""):
            known_servers = {k.split("__", 1)[0] for k in capabilities.tool_names}
            if known_servers and server not in known_servers:
                issues.append(
                    PlanIssue(
                        step_index=index,
                        kind="unknown_server",
                        name=server,
                        detail=(
                            f"'{server}' 서버가 연결되어 있지 않음 "
                            f"(연결된 서버: {', '.join(sorted(known_servers))})"
                        ),
                    )
                )

        for tool in _declared_tools(step):
            if not capabilities.bare_tool_names:
                continue
            if tool in capabilities.bare_tool_names:
                continue
            if tool in {k.split("__", 1)[-1] for k in capabilities.tool_names}:
                continue
            issues.append(
                PlanIssue(
                    step_index=index,
                    kind="unknown_tool",
                    name=tool,
                    detail=f"'{tool}' 도구가 연결된 MCP 서버에 없음",
                )
            )

        for sub in _declared_subfunctions(step):
            known = capabilities.knows_subfunction(sub)
            if known is False:
                issues.append(
                    PlanIssue(
                        step_index=index,
                        kind="unknown_subfunction",
                        name=sub,
                        detail=f"'{sub}' 기능이 서버가 제공하는 목록에 없음",
                    )
                )

    if issues:
        logger.info("plan_capability_issues", count=len(issues))
    return issues


def _declared_tools(step: dict[str, Any]) -> set[str]:
    """계획 단계가 명시한 도구명 추출

    Args:
        step: 계획 단계 정의

    Returns:
        도구명 집합
    """
    found: set[str] = set()
    tool = step.get("tool")
    if isinstance(tool, str) and tool.strip() and tool.strip().lower() != "none":
        found.add(tool.strip().split("__", 1)[-1])

    hints = str(step.get("hints") or "")
    match = re.search(r"도구\s*:\s*([^/\n]+)", hints)
    if match:
        for name in match.group(1).split(","):
            cleaned = name.strip()
            if cleaned:
                found.add(cleaned.split("__", 1)[-1])
    return found


def _declared_subfunctions(step: dict[str, Any]) -> set[str]:
    """계획 단계가 명시한 하위 기능명(플러그인 등) 추출

    Args:
        step: 계획 단계 정의

    Returns:
        기능명 집합
    """
    hints = str(step.get("hints") or "")
    match = re.search(r"(?:플러그인|기능)\s*:\s*([^/\n]+)", hints)
    if not match:
        return set()
    return {
        name.strip()
        for name in match.group(1).split(",")
        if name.strip()
    }


def classify_execution_failure(output: str) -> bool:
    """실행 실패가 역량 미충족 때문인지 판별

    Args:
        output: 단계 출력 텍스트

    Returns:
        미존재 기능으로 인한 실패면 True
    """
    lowered = output.lower()
    return any(
        re.search(pattern, lowered) for pattern in MISSING_CAPABILITY_PATTERNS
    )
