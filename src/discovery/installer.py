"""승인된 MCP 서버 설치 실행

`apply_proposals`는 mcp_servers.json에 항목을 "등록"할 뿐이므로, 서버가
로컬에 없을 때 실제로 받아오는 단계가 별도로 필요. 설치 유형은 두 가지:

- **팀 자체 서버** — 저장소를 클론하고 `uv sync`로 venv 구성
- **공개 패키지 서버** — `uv tool install` / `npm install -g`로 실행 파일 확보

설치는 네트워크와 파일시스템을 변경하는 비가역 작업이므로 사용자 승인
이후에만 호출해야 하며, 이 모듈은 승인 여부를 판단하지 않음
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

import structlog

from discovery.types import (
    InstallOutcome,
    MCPRecommendation,
    RecommendationSource,
)

logger = structlog.get_logger()

ProgressCallback = Callable[[str], Awaitable[None]]

DEFAULT_INSTALL_TIMEOUT = 600.0


class MCPInstaller:
    """MCP 서버 설치 실행기

    Attributes:
        install_root: 팀 서버를 클론할 상위 디렉터리
        timeout: 단일 명령 타임아웃 (초)
    """

    def __init__(
        self,
        install_root: Path,
        timeout: float = DEFAULT_INSTALL_TIMEOUT,
        progress: ProgressCallback | None = None,
    ) -> None:
        """설치 실행기 초기화

        Args:
            install_root: 팀 서버 저장소를 클론할 상위 디렉터리
            timeout: 단일 명령 타임아웃 (초)
            progress: 진행 상황 알림 콜백
        """
        self.install_root = install_root
        self.timeout = timeout
        self._progress = progress

    async def install(self, recommendation: MCPRecommendation) -> InstallOutcome:
        """추천 서버 설치 수행

        Args:
            recommendation: 사용자가 승인한 추천

        Returns:
            설치 결과 (성공 시 resolved_command 포함)
        """
        candidate = recommendation.candidate
        await self._notify(f"{candidate.display_name} 설치 시작")

        if candidate.source is RecommendationSource.TEAM:
            outcome = await self._install_team_server(recommendation)
        else:
            outcome = await self._install_package_server(recommendation)

        logger.info(
            "mcp_install_finished",
            server=candidate.server_id,
            ok=outcome.ok,
            command=outcome.resolved_command,
        )
        await self._notify(
            f"{candidate.display_name} 설치 "
            + ("완료" if outcome.ok else f"실패: {outcome.error}")
        )
        return outcome

    async def _install_team_server(
        self, recommendation: MCPRecommendation
    ) -> InstallOutcome:
        """팀 자체 서버를 클론 후 uv sync로 구성

        Args:
            recommendation: 팀 서버 추천

        Returns:
            설치 결과
        """
        candidate = recommendation.candidate
        repo_url = candidate.homepage
        if not repo_url:
            return InstallOutcome(
                server_id=candidate.server_id,
                ok=False,
                error="저장소 URL이 없어 자동 설치 불가",
            )

        uv = shutil.which("uv")
        git = shutil.which("git")
        if not git:
            return InstallOutcome(
                server_id=candidate.server_id, ok=False, error="git이 설치되지 않음"
            )
        if not uv:
            return InstallOutcome(
                server_id=candidate.server_id, ok=False, error="uv가 설치되지 않음"
            )

        repo_name = repo_url.rstrip("/").rsplit("/", 1)[-1]
        target = self.install_root / repo_name

        if not target.exists():
            self.install_root.mkdir(parents=True, exist_ok=True)
            await self._notify(f"{repo_name} 클론 중")
            ok, output = await self._run(
                [git, "clone", "--depth", "1", repo_url, str(target)]
            )
            if not ok:
                return InstallOutcome(
                    server_id=candidate.server_id,
                    ok=False,
                    error=f"git clone 실패: {output[:300]}",
                    log=output,
                )

        await self._notify(f"{repo_name} 의존성 설치 중 (uv sync)")
        ok, output = await self._run([uv, "sync"], cwd=target)
        if not ok:
            return InstallOutcome(
                server_id=candidate.server_id,
                ok=False,
                error=f"uv sync 실패: {output[:300]}",
                log=output,
            )

        for name in candidate.probe_executables:
            binary = target / ".venv" / "bin" / name
            if binary.is_file():
                return InstallOutcome(
                    server_id=candidate.server_id,
                    ok=True,
                    resolved_command=str(binary),
                    log=output,
                )

        return InstallOutcome(
            server_id=candidate.server_id,
            ok=False,
            error=f"{target}/.venv/bin 에서 실행 파일을 찾을 수 없음",
            log=output,
        )

    async def _install_package_server(
        self, recommendation: MCPRecommendation
    ) -> InstallOutcome:
        """공개 패키지 서버를 uv tool / npm으로 설치

        Args:
            recommendation: 공개 서버 추천

        Returns:
            설치 결과
        """
        candidate = recommendation.candidate
        if not candidate.args:
            return InstallOutcome(
                server_id=candidate.server_id,
                ok=False,
                error="설치할 패키지명을 알 수 없음",
            )

        package = candidate.args[-1]

        if candidate.command == "uvx":
            uv = shutil.which("uv")
            if not uv:
                return InstallOutcome(
                    server_id=candidate.server_id, ok=False, error="uv가 설치되지 않음"
                )
            command = [uv, "tool", "install", package]
        elif candidate.command == "npx":
            npm = shutil.which("npm")
            if not npm:
                return InstallOutcome(
                    server_id=candidate.server_id, ok=False, error="npm이 설치되지 않음"
                )
            command = [npm, "install", "-g", package]
        else:
            return InstallOutcome(
                server_id=candidate.server_id,
                ok=False,
                error=(
                    f"자동 설치를 지원하지 않는 실행 방식: {candidate.command}. "
                    f"수동 설치 필요: {candidate.install_hint or package}"
                ),
            )

        await self._notify(f"{package} 설치 중")
        ok, output = await self._run(command)
        if not ok:
            return InstallOutcome(
                server_id=candidate.server_id,
                ok=False,
                error=f"설치 명령 실패: {output[:300]}",
                log=output,
            )

        for name in candidate.probe_executables or (package,):
            found = shutil.which(name)
            if found:
                return InstallOutcome(
                    server_id=candidate.server_id,
                    ok=True,
                    resolved_command=found,
                    log=output,
                )

        return InstallOutcome(
            server_id=candidate.server_id,
            ok=False,
            error="설치는 성공했으나 실행 파일을 PATH에서 찾을 수 없음",
            log=output,
        )

    async def _run(
        self, command: list[str], cwd: Path | None = None
    ) -> tuple[bool, str]:
        """서브프로세스 실행 및 출력 수집

        Args:
            command: 실행할 명령 인자 목록
            cwd: 작업 디렉터리

        Returns:
            (성공 여부, 표준출력+표준오류 병합 텍스트)
        """
        logger.info("install_command_start", command=command, cwd=str(cwd or ""))
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(cwd) if cwd else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:
            return False, f"프로세스 생성 실패: {exc}"

        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return False, f"명령 타임아웃 ({self.timeout}초): {' '.join(command)}"

        output = stdout.decode(errors="replace")
        return process.returncode == 0, output

    async def _notify(self, message: str) -> None:
        """진행 상황 콜백 호출

        Args:
            message: 사용자에게 보여줄 진행 메시지
        """
        if self._progress is not None:
            await self._progress(message)
