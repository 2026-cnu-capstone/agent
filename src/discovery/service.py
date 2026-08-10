"""도구 탐지 → MCP 추천 → 설정 제안 오케스트레이션

`DiscoveryService.discover()` 한 번 호출로 `DiscoveryReport`를 반환하며,
결과는 `model_dump(mode="json")`으로 그대로 API 응답에 사용 가능
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog

from config import DiscoveryConfig, MCPConfig
from discovery.proposal import build_proposals, probe_names, resolve_install_state
from discovery.recommender import MCPRecommender
from discovery.registry_client import MCPRegistryClient
from discovery.scanner import ToolScanner, current_platform
from discovery.types import (
    DiscoveryReport,
    MCPRecommendation,
    Platform,
    RecommendationSource,
)
from discovery.verifier import verify_recommendations

logger = structlog.get_logger()


def default_search_roots(
    config_path: Path | None = None,
    configured_roots: list[str] | None = None,
) -> tuple[Path, ...]:
    """팀 MCP 서버 venv를 찾기 위한 탐색 루트 산출

    `DISCOVERY_SEARCH_ROOTS`로 지정한 루트를 최우선으로 두고, 저장소 위치에서
    유추한 루트를 뒤에 덧붙임. 저장소가 `<work>/capstone/agent` 구조일 때
    형제 저장소 `<work>/capstone/dissect-mcp`와 `<work>/dissect-mcp`를 모두 포함

    Args:
        config_path: mcp_servers.json 경로 (None이면 현재 파일 기준 추정)
        configured_roots: 설정에서 명시한 탐색 루트 문자열 목록

    Returns:
        중복이 제거된 탐색 루트 튜플 (설정값 우선)
    """
    repo_root = (
        config_path.parent.parent
        if config_path
        else Path(__file__).resolve().parents[2]
    )
    roots = [Path(entry) for entry in (configured_roots or [])]
    roots += [
        repo_root.parent,
        repo_root.parent.parent,
        Path.home() / ".local",
    ]
    seen: dict[str, Path] = {}
    for root in roots:
        resolved = root.expanduser()
        if resolved.is_dir():
            seen.setdefault(str(resolved), resolved)
    return tuple(seen.values())


class DiscoveryService:
    """포렌식 도구 탐지 및 MCP 추천 서비스

    Attributes:
        mcp_config: 현재 등록된 MCP 설정 (중복 추천 방지에 사용)
        config_path: mcp_servers.json 경로
        use_registry: 공개 MCP 레지스트리 조회 여부
    """

    def __init__(
        self,
        mcp_config: MCPConfig | None = None,
        config_path: Path | None = None,
        *,
        use_registry: bool = True,
        platform: Platform | None = None,
        search_roots: tuple[Path, ...] | None = None,
        registry: MCPRegistryClient | None = None,
        discovery_config: DiscoveryConfig | None = None,
    ) -> None:
        """탐지 서비스 초기화

        Args:
            mcp_config: 현재 MCP 설정, None이면 등록된 서버가 없다고 간주
            config_path: mcp_servers.json 경로
            use_registry: 공개 레지스트리 조회 활성화 여부
            platform: 강제 지정할 플랫폼 (None이면 자동 판별)
            search_roots: 실행 파일 추가 탐색 루트 (None이면 설정값 + 기본값)
            registry: 주입할 레지스트리 클라이언트 (테스트용)
            discovery_config: 탐색 루트/타임아웃 설정
        """
        self.mcp_config = mcp_config
        self.config_path = config_path
        self._config = discovery_config or DiscoveryConfig()
        self.use_registry = use_registry and self._config.use_public_registry
        self._platform = platform or current_platform()
        self._search_roots = (
            search_roots
            if search_roots is not None
            else default_search_roots(config_path, self._config.search_roots)
        )
        self._registry = registry or (
            MCPRegistryClient(timeout=self._config.registry_timeout)
            if self.use_registry
            else None
        )

    async def discover(self, *, verify: bool = False) -> DiscoveryReport:
        """전체 탐지 파이프라인 실행

        Args:
            verify: 실행 경로가 확인된 서버를 실제로 띄워 동작 검증할지 여부.
                프로세스를 생성하므로 사용자에게 설치를 제안하기 직전 등
                필요한 시점에만 활성화

        Returns:
            탐지 도구, 추천, 설정 제안이 채워진 리포트
        """
        scanner = ToolScanner(
            platform=self._platform, search_roots=self._search_roots
        )
        detected = await scanner.scan()

        configured = (
            frozenset(self.mcp_config.servers) if self.mcp_config else frozenset()
        )
        recommender = MCPRecommender(
            registry=self._registry, configured_servers=configured
        )
        recommendations = await recommender.recommend(detected)

        await self._resolve_local_installs(scanner, recommendations)
        if verify:
            await verify_recommendations(
                recommendations, timeout=self._config.verify_timeout
            )
        proposals = build_proposals(recommendations)

        report = DiscoveryReport(
            platform=self._platform,
            scanned_at=datetime.now(timezone.utc).isoformat(),
            detected_tools=detected,
            recommendations=recommendations,
            proposals=proposals,
            configured_servers=sorted(configured),
            registry_available=(
                self._registry.available if self._registry else False
            ),
            warnings=scanner.warnings,
        )
        logger.info(
            "discovery_completed",
            detected=len(detected),
            recommended=len(recommendations),
            proposals=len(proposals),
            registry_available=report.registry_available,
        )
        return report

    async def _resolve_local_installs(
        self,
        scanner: ToolScanner,
        recommendations: list[MCPRecommendation],
    ) -> None:
        """각 추천 서버의 실행 파일을 로컬에서 탐색해 설치 상태 확정

        Args:
            scanner: 실행 파일 탐색에 재사용할 스캐너
            recommendations: 상태를 갱신할 추천 목록 (제자리 수정)
        """
        configured_commands = self._configured_commands()

        for recommendation in recommendations:
            resolved: str | None = configured_commands.get(
                recommendation.candidate.server_id
            )
            prefer_roots = (
                recommendation.candidate.source is RecommendationSource.TEAM
            )
            if resolved is None:
                for name in probe_names(recommendation.candidate):
                    resolved = await scanner.find_executable(
                        name, prefer_search_roots=prefer_roots
                    )
                    if resolved:
                        break
            resolve_install_state(recommendation, resolved)

    def _configured_commands(self) -> dict[str, str]:
        """등록된 stdio 서버의 실행 경로 매핑 반환

        Returns:
            서버 키 → command 문자열
        """
        if not self.mcp_config:
            return {}
        return {
            name: server.command
            for name, server in self.mcp_config.servers.items()
            if server.transport == "stdio"
        }
