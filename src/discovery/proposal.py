"""mcp_servers.json 등록 제안 생성 및 승인 후 적용

추천 결과를 설정 파일에 자동 반영하지 않고 `ServerProposal` 형태로만 만들어
사용자(또는 frontend 설정 화면)의 승인을 받은 뒤 `apply_proposals`로 기록
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterable
from pathlib import Path

import structlog

from discovery.types import (
    InstallState,
    MCPRecommendation,
    MCPServerCandidate,
    RecommendationSource,
    ServerProposal,
)

logger = structlog.get_logger()


def build_proposals(
    recommendations: Iterable[MCPRecommendation],
) -> list[ServerProposal]:
    """추천 목록에서 설정 반영 제안 생성

    이미 등록된 서버(`InstallState.CONFIGURED`)는 제안 대상에서 제외

    Args:
        recommendations: 추천 목록

    Returns:
        서버 키 중복이 제거된 제안 목록
    """
    proposals: list[ServerProposal] = []
    seen: set[str] = set()

    for recommendation in recommendations:
        if recommendation.install_state is InstallState.CONFIGURED:
            continue
        if recommendation.candidate.server_id in seen:
            continue
        seen.add(recommendation.candidate.server_id)
        proposals.append(_build_one(recommendation))

    return proposals


def _build_one(recommendation: MCPRecommendation) -> ServerProposal:
    """추천 한 건을 mcp_servers.json 항목 제안으로 변환

    Args:
        recommendation: 변환할 추천

    Returns:
        생성된 제안 (실행 경로 미확정 시 ready=False)
    """
    candidate = recommendation.candidate
    server_id = candidate.server_id

    if candidate.transport == "sse":
        if not candidate.url:
            return ServerProposal(
                server_id=server_id,
                entry={},
                ready=False,
                blocked_reason="원격 endpoint URL을 확인할 수 없음",
            )
        entry: dict[str, object] = {"transport": "sse", "url": candidate.url}
        if candidate.source is RecommendationSource.PUBLIC_REGISTRY:
            return ServerProposal(
                server_id=server_id,
                entry=entry,
                ready=False,
                blocked_reason=(
                    "제3자 원격 endpoint로 증거 데이터가 전송될 수 있어 "
                    f"운영자 검증 후 수동 등록 필요: {candidate.url}"
                ),
            )
        return ServerProposal(server_id=server_id, entry=entry, ready=True)

    if recommendation.resolved_command:
        return ServerProposal(
            server_id=server_id,
            entry={
                "command": recommendation.resolved_command,
                "args": list(candidate.args),
            },
            ready=True,
        )

    if candidate.command:
        return ServerProposal(
            server_id=server_id,
            entry={
                "command": candidate.command,
                "args": list(candidate.args),
            },
            ready=False,
            blocked_reason=(
                f"미설치 상태. 먼저 `{candidate.install_hint or candidate.command}` "
                "실행 후 등록 필요"
            ),
        )

    return ServerProposal(
        server_id=server_id,
        entry={},
        ready=False,
        blocked_reason=(
            candidate.install_hint
            or "실행 명령을 알 수 없어 수동 설정 필요"
        ),
    )


def resolve_install_state(
    recommendation: MCPRecommendation,
    resolved_path: str | None,
) -> None:
    """탐색된 실행 경로를 추천에 반영해 설치 상태 갱신

    이미 `CONFIGURED`인 추천은 상태를 유지하되 실제 경로만 채움

    Args:
        recommendation: 갱신할 추천 객체 (제자리 수정)
        resolved_path: 로컬에서 발견된 실행 파일 절대 경로
    """
    recommendation.resolved_command = resolved_path
    if recommendation.install_state is InstallState.CONFIGURED:
        return
    if resolved_path:
        recommendation.install_state = InstallState.INSTALLED


def probe_names(candidate: MCPServerCandidate) -> tuple[str, ...]:
    """후보의 로컬 설치 여부를 확인할 실행 파일명 목록 반환

    `probe_executables`가 비어 있으면 uvx/npx 실행 인자에서 패키지명을 추론

    Args:
        candidate: MCP 서버 후보

    Returns:
        탐색할 실행 파일명 튜플
    """
    if candidate.probe_executables:
        return candidate.probe_executables
    if candidate.command in ("uvx", "npx") and candidate.args:
        return (candidate.args[-1],)
    return ()


def apply_proposals(
    config_path: Path,
    proposals: Iterable[ServerProposal],
    *,
    backup: bool = True,
) -> list[str]:
    """승인된 제안을 mcp_servers.json에 병합 기록

    `ready=False` 제안은 무시하며, 기존에 같은 키가 있으면 덮어쓰지 않음

    Args:
        config_path: mcp_servers.json 경로
        proposals: 사용자가 승인한 제안 목록
        backup: 기록 전 `.bak` 백업 생성 여부

    Returns:
        실제로 추가된 서버 키 목록

    Raises:
        ValueError: 설정 파일이 올바른 JSON 객체가 아닐 때
    """
    applicable = [p for p in proposals if p.ready and p.entry]
    if not applicable:
        return []

    if config_path.exists():
        with open(config_path, encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise ValueError(f"MCP 설정 파일 형식 오류: {config_path}")
    else:
        raw = {}

    servers = raw.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"mcpServers 필드 형식 오류: {config_path}")

    added: list[str] = []
    for proposal in applicable:
        if proposal.server_id in servers:
            logger.info("proposal_skipped_existing", server=proposal.server_id)
            continue
        servers[proposal.server_id] = proposal.entry
        added.append(proposal.server_id)

    if not added:
        return []

    if backup and config_path.exists():
        shutil.copy2(config_path, config_path.with_suffix(".json.bak"))

    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as handle:
        json.dump(raw, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    logger.info("proposals_applied", servers=added, path=str(config_path))
    return added
