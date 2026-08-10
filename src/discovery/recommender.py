"""탐지된 포렌식 도구를 MCP 서버 후보에 매칭하는 추천기

추천 근거는 두 갈래:

1. 카탈로그 매칭 — 팀 자체 서버 및 검증된 공개 서버를 도구별로 직접 매핑
2. 공개 레지스트리 매칭 — 도구 키워드로 검색 후 관련성 채점으로 오탐 제거

레지스트리 검색은 서버 이름/설명 부분 문자열 매칭이라
`tsk` 검색에 팟캐스트 서버가, `yara` 검색에 사용자명이 `Vijayarao`인 서버가
걸리는 식의 오탐이 잦아, 토큰 단위 용어 일치 개수를 임계값으로 사용
"""

from __future__ import annotations

import re

import structlog

from discovery.catalog import MCP_CATALOG, TOOL_INDEX, candidates_for_tool
from discovery.registry_client import MCPRegistryClient
from discovery.types import (
    DetectedTool,
    InstallState,
    MCPRecommendation,
    MCPServerCandidate,
    RecommendationSource,
    ToolSignature,
)

logger = structlog.get_logger()

SOURCE_BASE_SCORE: dict[RecommendationSource, float] = {
    RecommendationSource.TEAM: 0.95,
    RecommendationSource.CURATED: 0.8,
    RecommendationSource.PUBLIC_REGISTRY: 0.0,
}

MULTI_TOOL_BONUS = 0.02
"""후보가 추가로 커버하는 도구 하나당 가산점"""

MIN_REGISTRY_SCORE = 0.4
"""공개 레지스트리 후보를 추천에 포함하는 최소 점수"""

MAX_REGISTRY_PER_TOOL = 3
"""도구 하나당 채택할 공개 레지스트리 후보 최대 개수"""

GENERIC_TERMS: frozenset[str] = frozenset(
    {
        "analysis",
        "binary",
        "capture",
        "data",
        "database",
        "db",
        "disk",
        "dump",
        "email",
        "extract",
        "file",
        "files",
        "image",
        "images",
        "mail",
        "memory",
        "metadata",
        "network",
        "query",
        "registry",
        "scan",
        "server",
        "timeline",
        "tool",
    }
)
"""도메인 식별력이 없어 단독 일치로는 추천 근거가 될 수 없는 용어"""

FORENSIC_CONTEXT_TERMS: frozenset[str] = frozenset(
    {
        "forensic",
        "forensics",
        "dfir",
        "incident",
        "investigation",
        "artifact",
        "artifacts",
        "evidence",
        "malware",
        "triage",
        "acquisition",
    }
)

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


class MCPRecommender:
    """탐지 도구 기반 MCP 서버 추천기

    Attributes:
        registry: 공개 레지스트리 클라이언트 (None이면 카탈로그만 사용)
        configured_servers: 이미 등록된 MCP 서버 키 집합
    """

    def __init__(
        self,
        registry: MCPRegistryClient | None = None,
        configured_servers: frozenset[str] = frozenset(),
    ) -> None:
        """추천기 초기화

        Args:
            registry: 공개 레지스트리 클라이언트, None이면 레지스트리 조회 생략
            configured_servers: mcp_servers.json에 이미 등록된 서버 키 집합
        """
        self.registry = registry
        self.configured_servers = configured_servers

    async def recommend(
        self, detected: list[DetectedTool]
    ) -> list[MCPRecommendation]:
        """탐지된 도구에 대한 MCP 추천 목록 생성

        Args:
            detected: 스캐너가 발견한 도구 목록

        Returns:
            점수 내림차순 정렬된 추천 목록
        """
        if not detected:
            return []

        merged: dict[str, MCPRecommendation] = {}
        self._collect_catalog_matches(detected, merged)
        self._collect_configured_catalog_servers(merged)
        await self._collect_registry_matches(detected, merged)

        recommendations = sorted(
            merged.values(),
            key=lambda item: (item.score, len(item.matched_tools)),
            reverse=True,
        )
        logger.info(
            "mcp_recommendation_completed",
            detected=len(detected),
            recommended=len(recommendations),
        )
        return recommendations

    def _collect_catalog_matches(
        self,
        detected: list[DetectedTool],
        merged: dict[str, MCPRecommendation],
    ) -> None:
        """카탈로그 정적 매핑으로 후보 수집

        Args:
            detected: 탐지된 도구 목록
            merged: 서버 키 기준 추천 누적 딕셔너리 (제자리 갱신)
        """
        for tool in detected:
            for candidate in candidates_for_tool(tool.tool_id):
                self._merge(merged, candidate, tool.tool_id)

        for recommendation in merged.values():
            base = SOURCE_BASE_SCORE[recommendation.candidate.source]
            extra = max(0, len(recommendation.matched_tools) - 1)
            recommendation.score = round(min(1.0, base + extra * MULTI_TOOL_BONUS), 3)
            recommendation.reason = _catalog_reason(recommendation)

    def _collect_configured_catalog_servers(
        self, merged: dict[str, MCPRecommendation]
    ) -> None:
        """이미 등록된 카탈로그 서버를 도구 탐지 여부와 무관하게 포함

        pst-mcp처럼 대상 도구(libpff 등)가 로컬에 없어도 서버 자체는 등록되어
        운영 중일 수 있으므로, 설정 화면에서 현황이 누락되지 않도록 보강

        Args:
            merged: 서버 키 기준 추천 누적 딕셔너리 (제자리 갱신)
        """
        for candidate in MCP_CATALOG:
            if candidate.server_id not in self.configured_servers:
                continue
            if candidate.server_id in merged:
                continue
            merged[candidate.server_id] = MCPRecommendation(
                candidate=candidate,
                score=SOURCE_BASE_SCORE[candidate.source],
                install_state=InstallState.CONFIGURED,
                reason="이미 등록된 MCP 서버 (대응 도구는 로컬에서 탐지되지 않음)",
            )

    async def _collect_registry_matches(
        self,
        detected: list[DetectedTool],
        merged: dict[str, MCPRecommendation],
    ) -> None:
        """공개 레지스트리 조회 결과로 후보 보강

        Args:
            detected: 탐지된 도구 목록
            merged: 서버 키 기준 추천 누적 딕셔너리 (제자리 갱신)
        """
        if self.registry is None:
            return

        signatures = [
            TOOL_INDEX[tool.tool_id]
            for tool in detected
            if tool.tool_id in TOOL_INDEX
        ]
        keywords = tuple(
            keyword
            for signature in signatures
            for keyword in signature.registry_keywords
        )
        if not keywords:
            return

        results = await self.registry.search_many(keywords)
        known_registry_names = {
            item.candidate.registry_name
            for item in merged.values()
            if item.candidate.registry_name
        }

        for signature in signatures:
            scored: list[tuple[float, str, MCPServerCandidate]] = []
            for keyword in signature.registry_keywords:
                for candidate in results.get(keyword, []):
                    if candidate.registry_name in known_registry_names:
                        continue
                    score = _score_registry_candidate(candidate, signature)
                    if score >= MIN_REGISTRY_SCORE:
                        scored.append((score, keyword, candidate))

            scored.sort(key=lambda item: item[0], reverse=True)
            for score, keyword, candidate in scored[:MAX_REGISTRY_PER_TOOL]:
                recommendation = self._merge(merged, candidate, signature.tool_id)
                if score > recommendation.score:
                    recommendation.score = round(score, 3)
                    recommendation.reason = (
                        f"공개 MCP 레지스트리에서 '{keyword}' 검색으로 발견 "
                        f"({signature.display_name} 관련)"
                    )

    def _merge(
        self,
        merged: dict[str, MCPRecommendation],
        candidate: MCPServerCandidate,
        tool_id: str,
    ) -> MCPRecommendation:
        """후보를 추천 딕셔너리에 병합하고 매칭 도구 누적

        Args:
            merged: 서버 키 기준 추천 누적 딕셔너리
            candidate: 병합할 MCP 서버 후보
            tool_id: 매칭 근거가 된 도구 ID

        Returns:
            병합 후의 추천 객체
        """
        existing = merged.get(candidate.server_id)
        if existing is None:
            existing = MCPRecommendation(
                candidate=candidate,
                install_state=(
                    InstallState.CONFIGURED
                    if candidate.server_id in self.configured_servers
                    else InstallState.NOT_INSTALLED
                ),
            )
            merged[candidate.server_id] = existing

        if tool_id not in existing.matched_tools:
            existing.matched_tools.append(tool_id)
        return existing


def _score_registry_candidate(
    candidate: MCPServerCandidate, signature: ToolSignature
) -> float:
    """레지스트리 후보와 도구 시그니처의 관련성 채점

    서버 이름/설명을 토큰화해 시그니처의 `relevance_terms`와 겹치는 개수로
    점수를 산출하되, 두 가지 조건으로 오탐을 배제:

    - 일치 용어가 모두 `GENERIC_TERMS`면 탈락
      (ExifTool 검색에 `metadata`, `image`만 겹치는 NFT 서버 차단)
    - 식별력 있는 용어가 1개만 겹치면 포렌식 도메인 용어가 함께 있어야 통과
      (Volatility 검색에 걸리는 금융 변동성 데이터 서버 차단)

    Args:
        candidate: 레지스트리에서 가져온 후보
        signature: 비교 대상 도구 시그니처

    Returns:
        0.0~1.0 관련성 점수
    """
    terms = set(signature.relevance_terms)
    if not terms:
        return 0.0

    name_text = f"{candidate.registry_name or ''} {candidate.display_name}".lower()
    name_tokens = set(_TOKEN_PATTERN.findall(name_text))
    all_tokens = name_tokens | set(
        _TOKEN_PATTERN.findall(candidate.description.lower())
    )

    hits = terms & all_tokens
    distinctive = hits - GENERIC_TERMS
    if not distinctive:
        return 0.0

    has_context = bool(FORENSIC_CONTEXT_TERMS & all_tokens)
    if len(hits) == 1 and not has_context:
        return 0.0

    score = 0.35 + 0.45 * (len(hits) / len(terms))
    if name_tokens & distinctive:
        score += 0.1
    if has_context:
        score += 0.05
    return min(score, 0.79)


def filter_by_strategy(
    recommendations: list[MCPRecommendation],
    strategy_text: str,
    *,
    keep_configured: bool = True,
) -> list[MCPRecommendation]:
    """전략 텍스트와 관련된 추천만 남김

    전략 수립 이후 단계에서 호출해, 이번 사건에 쓰이지 않을 도구의 설치
    알림이 사용자에게 노출되는 것을 방지. 전략 텍스트가 비어 있으면
    필터링하지 않고 그대로 반환

    Args:
        recommendations: 필터링 대상 추천 목록
        strategy_text: 전략 수립 단계가 생성한 분석 전략 텍스트
        keep_configured: 이미 등록된 서버는 관련성과 무관하게 유지할지 여부

    Returns:
        관련성이 확인된 추천 목록 (입력 순서 유지)
    """
    if not strategy_text.strip():
        return recommendations

    tokens = set(_TOKEN_PATTERN.findall(strategy_text.lower()))
    kept: list[MCPRecommendation] = []

    for recommendation in recommendations:
        if keep_configured and (
            recommendation.install_state is InstallState.CONFIGURED
        ):
            kept.append(recommendation)
            continue
        if _matches_strategy(recommendation, tokens, strategy_text.lower()):
            kept.append(recommendation)

    logger.info(
        "recommendations_filtered_by_strategy",
        before=len(recommendations),
        after=len(kept),
    )
    return kept


def _matches_strategy(
    recommendation: MCPRecommendation,
    tokens: set[str],
    lowered_strategy: str,
) -> bool:
    """추천이 전략과 관련 있는지 판정

    매칭된 도구의 관련 용어 또는 카테고리명이 전략 텍스트에 등장하면 관련으로 간주

    Args:
        recommendation: 판정 대상 추천
        tokens: 전략 텍스트의 토큰 집합
        lowered_strategy: 소문자화된 전략 텍스트

    Returns:
        관련 있으면 True
    """
    for tool_id in recommendation.matched_tools:
        signature = TOOL_INDEX.get(tool_id)
        if signature is None:
            continue
        if set(signature.relevance_terms) & tokens:
            return True
        if signature.display_name.lower() in lowered_strategy:
            return True
        if signature.category.value.replace("_", " ") in lowered_strategy:
            return True
    return False


def _catalog_reason(recommendation: MCPRecommendation) -> str:
    """카탈로그 매칭 추천의 사유 문구 생성

    Args:
        recommendation: 사유를 채울 추천 객체

    Returns:
        한국어 추천 사유 문구
    """
    names = [
        TOOL_INDEX[tool_id].display_name
        for tool_id in recommendation.matched_tools
        if tool_id in TOOL_INDEX
    ]
    joined = ", ".join(names) if names else "탐지된 도구"
    if recommendation.candidate.source is RecommendationSource.TEAM:
        return f"{joined} 분석을 담당하는 팀 자체 MCP 서버"
    return f"{joined} 연동을 제공하는 검증된 공개 MCP 서버"
