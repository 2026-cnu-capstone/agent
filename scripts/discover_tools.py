"""포렌식 도구 탐지 및 MCP 추천 실행 스크립트

로컬 시스템을 스캔해 설치된 포렌식 도구를 찾고, 대응하는 MCP 서버를
추천한 뒤 mcp_servers.json 반영 제안을 출력. 제안은 `--apply` 옵션으로
사용자가 명시적으로 승인했을 때만 기록

Usage:
    python scripts/discover_tools.py
    python scripts/discover_tools.py --json
    python scripts/discover_tools.py --no-registry
    python scripts/discover_tools.py --apply dissect --apply pst
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import structlog

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import load_settings  # noqa: E402
from discovery import DiscoveryService, apply_proposals  # noqa: E402
from discovery.types import (  # noqa: E402
    DiscoveryReport,
    InstallState,
    RecommendationSource,
)

DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "mcp_servers.json"
)

STATE_LABEL: dict[InstallState, str] = {
    InstallState.CONFIGURED: "등록됨",
    InstallState.INSTALLED: "설치됨(미등록)",
    InstallState.NOT_INSTALLED: "미설치",
}

SOURCE_LABEL: dict[RecommendationSource, str] = {
    RecommendationSource.TEAM: "팀",
    RecommendationSource.CURATED: "큐레이션",
    RecommendationSource.PUBLIC_REGISTRY: "공개 레지스트리",
}


def parse_args() -> argparse.Namespace:
    """명령행 인자 파싱

    Returns:
        파싱된 인자 네임스페이스
    """
    parser = argparse.ArgumentParser(
        description="설치된 포렌식 도구 탐지 및 MCP 서버 추천"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="mcp_servers.json 경로",
    )
    parser.add_argument(
        "--json", action="store_true", help="리포트를 JSON으로 출력"
    )
    parser.add_argument(
        "--no-registry",
        action="store_true",
        help="공개 MCP 레지스트리 조회 생략 (오프라인 모드)",
    )
    parser.add_argument(
        "--apply",
        action="append",
        default=[],
        metavar="SERVER_ID",
        help="승인할 서버 키 (반복 지정 가능). 미지정 시 제안만 출력",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="발견된 MCP 서버를 실제로 띄워 동작 검증 (프로세스 생성)",
    )
    parser.add_argument("--verbose", action="store_true", help="디버그 로그 출력")
    return parser.parse_args()


def configure_logging(verbose: bool) -> None:
    """structlog 출력 레벨 설정

    Args:
        verbose: 디버그 로그 활성화 여부
    """
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(level=level, stream=sys.stderr, format="%(message)s")
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(sys.stderr),
    )


def print_report(report: DiscoveryReport) -> None:
    """사람이 읽을 형태로 리포트 출력

    Args:
        report: 출력할 탐지 리포트
    """
    print(f"\n플랫폼: {report.platform.value}")
    print(
        "공개 레지스트리: "
        + ("응답 확인" if report.registry_available else "조회 실패/생략")
    )

    print(f"\n[탐지된 포렌식 도구] {len(report.detected_tools)}개")
    for tool in report.detected_tools:
        version = f" v{tool.version}" if tool.version else ""
        print(f"  - {tool.display_name}{version}  ({tool.category.value})")
        for item in tool.evidence:
            detail = f" — {item.detail}" if item.detail else ""
            print(f"      {item.method.value}: {item.location}{detail}")

    print(f"\n[MCP 서버 추천] {len(report.recommendations)}개")
    for rec in report.recommendations:
        candidate = rec.candidate
        print(
            f"  - {candidate.display_name} "
            f"[{SOURCE_LABEL[candidate.source]}] "
            f"score={rec.score} / {STATE_LABEL[rec.install_state]}"
        )
        print(f"      {rec.reason}")
        if rec.resolved_command:
            print(f"      실행 경로: {rec.resolved_command}")
        elif candidate.install_hint:
            print(f"      설치: {candidate.install_hint}")
        if rec.verification is not None:
            if rec.verification.ok:
                print(
                    f"      동작 검증: OK — {rec.verification.server_name} "
                    f"v{rec.verification.server_version}, "
                    f"도구 {rec.verification.tool_count}개"
                )
            else:
                print(f"      동작 검증: 실패 — {rec.verification.error}")

    pending = [p for p in report.proposals if p.ready]
    blocked = [p for p in report.proposals if not p.ready]

    print(f"\n[mcp_servers.json 반영 제안] 적용 가능 {len(pending)}건")
    for proposal in pending:
        entry = json.dumps(proposal.entry, ensure_ascii=False)
        print(f"  + {proposal.server_id}: {entry}")
    if blocked:
        print(f"  적용 불가 {len(blocked)}건")
        for proposal in blocked:
            print(f"  - {proposal.server_id}: {proposal.blocked_reason}")
    if pending:
        keys = " ".join(f"--apply {p.server_id}" for p in pending)
        print(f"\n  승인하려면: python scripts/discover_tools.py {keys}")

    for warning in report.warnings:
        print(f"\n경고: {warning}")


async def main() -> int:
    """스크립트 진입점

    Returns:
        프로세스 종료 코드
    """
    args = parse_args()
    configure_logging(args.verbose)

    settings = load_settings(args.config)
    service = DiscoveryService(
        mcp_config=settings.mcp,
        config_path=args.config,
        use_registry=not args.no_registry,
        discovery_config=settings.discovery,
    )
    report = await service.discover(verify=args.verify)

    if args.json:
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        print_report(report)

    if not args.apply:
        return 0

    approved = [p for p in report.proposals if p.server_id in set(args.apply)]
    unknown = set(args.apply) - {p.server_id for p in report.proposals}
    if unknown:
        print(f"\n알 수 없는 서버 키: {', '.join(sorted(unknown))}", file=sys.stderr)

    added = apply_proposals(args.config, approved)
    if added:
        print(f"\n{args.config}에 추가됨: {', '.join(added)}")
    else:
        print("\n적용된 제안 없음 (이미 등록되었거나 적용 불가 상태)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
