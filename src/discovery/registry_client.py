"""공개 MCP 레지스트리(registry.modelcontextprotocol.io) 조회 클라이언트

`GET /v0/servers?search=<keyword>&version=latest` 응답을 파싱해
`MCPServerCandidate`로 정규화. 레지스트리 검색은 이름/설명 부분 문자열
매칭이라 오탐이 많으므로, 필터링은 호출 측(recommender)에서 수행

네트워크 실패 시 예외를 전파하지 않고 빈 결과를 반환해
카탈로그 기반 추천으로 자연스럽게 폴백
"""

from __future__ import annotations

import asyncio
from typing import Any, NamedTuple

import httpx
import structlog

from discovery.types import MCPServerCandidate, RecommendationSource

logger = structlog.get_logger()

DEFAULT_REGISTRY_URL = "https://registry.modelcontextprotocol.io"
REGISTRY_META_KEY = "io.modelcontextprotocol.registry/official"

PACKAGE_RUNNERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "npm": ("npx", ("-y",)),
    "pypi": ("uvx", ()),
    "oci": ("docker", ("run", "-i", "--rm")),
}


class MCPRegistryClient:
    """공개 MCP 레지스트리 검색 클라이언트

    Attributes:
        base_url: 레지스트리 베이스 URL
        timeout: 단일 요청 타임아웃 (초)
        limit: 키워드당 최대 조회 개수
    """

    def __init__(
        self,
        base_url: str = DEFAULT_REGISTRY_URL,
        timeout: float = 5.0,
        limit: int = 10,
    ) -> None:
        """레지스트리 클라이언트 초기화

        Args:
            base_url: 레지스트리 베이스 URL
            timeout: 단일 요청 타임아웃 (초)
            limit: 키워드당 최대 조회 개수
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.limit = limit
        self._cache: dict[str, list[MCPServerCandidate]] = {}
        self._available = False

    @property
    def available(self) -> bool:
        """직전 조회에서 레지스트리 응답을 최소 1회 받았는지 여부"""
        return self._available

    async def search_many(
        self, keywords: tuple[str, ...]
    ) -> dict[str, list[MCPServerCandidate]]:
        """여러 키워드를 동시 조회

        Args:
            keywords: 검색 키워드 목록

        Returns:
            키워드 → 후보 목록
        """
        unique = tuple(dict.fromkeys(keywords))
        if not unique:
            return {}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            results = await asyncio.gather(
                *(self._search_one(client, keyword) for keyword in unique),
                return_exceptions=True,
            )

        output: dict[str, list[MCPServerCandidate]] = {}
        for keyword, result in zip(unique, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning(
                    "registry_search_failed", keyword=keyword, error=str(result)
                )
                output[keyword] = []
            else:
                output[keyword] = result
        return output

    async def _search_one(
        self, client: httpx.AsyncClient, keyword: str
    ) -> list[MCPServerCandidate]:
        """단일 키워드 검색

        Args:
            client: 재사용할 HTTP 클라이언트
            keyword: 검색 키워드

        Returns:
            정규화된 후보 목록 (실패 시 빈 리스트)
        """
        if keyword in self._cache:
            return self._cache[keyword]

        try:
            response = await client.get(
                f"{self.base_url}/v0/servers",
                params={
                    "search": keyword,
                    "version": "latest",
                    "limit": self.limit,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("registry_request_failed", keyword=keyword, error=str(exc))
            self._cache[keyword] = []
            return []

        self._available = True
        candidates = [
            candidate
            for entry in payload.get("servers", [])
            if (candidate := _parse_entry(entry)) is not None
        ]
        self._cache[keyword] = candidates
        logger.debug(
            "registry_search_completed", keyword=keyword, results=len(candidates)
        )
        return candidates


def _parse_entry(entry: dict[str, Any]) -> MCPServerCandidate | None:
    """레지스트리 응답 항목 하나를 MCPServerCandidate로 변환

    Args:
        entry: `servers[]` 배열의 단일 항목

    Returns:
        변환된 후보, 필수 필드 누락이나 비활성 서버면 None
    """
    server = entry.get("server")
    if not isinstance(server, dict):
        return None

    name = server.get("name")
    if not isinstance(name, str) or not name:
        return None

    meta = entry.get("_meta", {}).get(REGISTRY_META_KEY, {})
    if meta.get("status") not in (None, "active"):
        return None

    install = _resolve_install(server)
    repository = server.get("repository") or {}

    return MCPServerCandidate(
        server_id=_derive_server_id(name),
        display_name=server.get("title") or name,
        description=server.get("description") or "",
        source=RecommendationSource.PUBLIC_REGISTRY,
        registry_name=name,
        homepage=server.get("websiteUrl") or repository.get("url"),
        transport=install.transport,
        install_hint=install.hint,
        command=install.command,
        args=install.args,
        url=install.url,
    )


class _InstallInfo(NamedTuple):
    """레지스트리 항목에서 추론한 실행 방법

    Attributes:
        transport: MCP transport 유형
        command: stdio 실행 명령
        args: stdio 실행 인자
        url: SSE 원격 endpoint URL
        hint: 사용자에게 보여줄 설치 안내
    """

    transport: str
    command: str | None
    args: tuple[str, ...]
    url: str | None
    hint: str | None


def _resolve_install(server: dict[str, Any]) -> _InstallInfo:
    """레지스트리 서버 정의에서 실행 방법 추론

    packages 항목이 있으면 registryType에 맞는 러너(npx/uvx/docker)를 붙이고,
    remotes만 있으면 SSE transport로 처리

    Args:
        server: 레지스트리 `server` 객체

    Returns:
        추론된 실행 방법
    """
    packages = server.get("packages") or []
    for package in packages:
        registry_type = package.get("registryType")
        identifier = package.get("identifier")
        runner = PACKAGE_RUNNERS.get(str(registry_type))
        if not runner or not identifier:
            continue
        command, prefix = runner
        args = (*prefix, str(identifier))
        return _InstallInfo(
            transport="stdio",
            command=command,
            args=args,
            url=None,
            hint=f"{command} {' '.join(args)}",
        )

    remotes = server.get("remotes") or []
    for remote in remotes:
        url = remote.get("url")
        if url:
            return _InstallInfo(
                transport="sse",
                command=None,
                args=(),
                url=str(url),
                hint=f"원격 endpoint: {url}",
            )

    return _InstallInfo(
        transport="stdio", command=None, args=(), url=None, hint=None
    )


def _derive_server_id(registry_name: str) -> str:
    """레지스트리 정규 이름에서 mcp_servers.json용 키 생성

    `io.github.owner/wireshark-mcp` → `wireshark_mcp`

    Args:
        registry_name: 레지스트리 정규 이름

    Returns:
        영숫자와 밑줄로 구성된 서버 키
    """
    tail = registry_name.rsplit("/", 1)[-1]
    normalized = "".join(ch if ch.isalnum() else "_" for ch in tail).strip("_")
    return normalized.lower() or "mcp_server"
