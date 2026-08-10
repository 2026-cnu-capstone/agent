"""OS별 포렌식 도구 로컬 스캐너

탐지 경로:
    - 공통: PATH 실행 파일, 설치된 PyPI 배포판, 추가 탐색 루트
    - macOS: /Applications 번들, Homebrew formula/cask
    - Linux: Homebrew(linuxbrew) formula
    - Windows: 언인스톨 레지스트리, Program Files 하위 경로

파일시스템/서브프로세스 접근은 모두 블로킹이므로 `asyncio.to_thread`로 감싸
이벤트 루프를 막지 않음
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from importlib import import_module, metadata
from pathlib import Path

import structlog

from discovery.catalog import signatures_for_platform
from discovery.types import (
    DetectedTool,
    DetectionEvidence,
    DetectionMethod,
    Platform,
    ToolSignature,
)

logger = structlog.get_logger()

WINDOWS_UNINSTALL_KEYS: tuple[str, ...] = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
)


def current_platform() -> Platform:
    """실행 중인 운영체제 판별"""
    if sys.platform == "darwin":
        return Platform.MACOS
    if sys.platform.startswith("win"):
        return Platform.WINDOWS
    if sys.platform.startswith("linux"):
        return Platform.LINUX
    return Platform.UNKNOWN


class ToolScanner:
    """로컬 시스템의 포렌식 도구 탐지기

    Attributes:
        platform: 스캔 대상 플랫폼
        search_roots: venv 실행 파일 탐색에 사용할 추가 루트 디렉터리
    """

    def __init__(
        self,
        platform: Platform | None = None,
        search_roots: tuple[Path, ...] = (),
    ) -> None:
        """스캐너 초기화

        Args:
            platform: 강제 지정할 플랫폼 (None이면 자동 판별)
            search_roots: PATH 밖의 실행 파일을 찾을 추가 루트 디렉터리
        """
        self.platform = platform or current_platform()
        self.search_roots = search_roots
        self._warnings: list[str] = []

    @property
    def warnings(self) -> list[str]:
        """스캔 중 누적된 경고 메시지"""
        return list(self._warnings)

    async def scan(self) -> list[DetectedTool]:
        """카탈로그 전체에 대해 도구 탐지 수행

        Returns:
            발견된 도구 목록 (카탈로그 정의 순서)
        """
        self._warnings.clear()
        signatures = signatures_for_platform(self.platform.value)

        brew_packages = await asyncio.to_thread(self._collect_homebrew_packages)
        distributions = await asyncio.to_thread(self._collect_distributions)
        app_bundles = await asyncio.to_thread(self._collect_app_bundles)
        registry_entries = await asyncio.to_thread(self._collect_windows_registry)

        detected: list[DetectedTool] = []
        for signature in signatures:
            evidence = await asyncio.to_thread(
                self._match_signature,
                signature,
                brew_packages,
                distributions,
                app_bundles,
                registry_entries,
            )
            if evidence:
                detected.append(
                    DetectedTool(
                        tool_id=signature.tool_id,
                        display_name=signature.display_name,
                        category=signature.category,
                        evidence=evidence,
                        version=_first_detail(evidence),
                    )
                )

        logger.info(
            "tool_scan_completed",
            platform=self.platform.value,
            detected=len(detected),
            scanned=len(signatures),
        )
        return detected

    async def find_executable(
        self, name: str, *, prefer_search_roots: bool = False
    ) -> str | None:
        """PATH와 추가 탐색 루트에서 실행 파일 경로 탐색

        Args:
            name: 실행 파일 이름
            prefer_search_roots: 탐색 루트를 PATH보다 우선할지 여부.
                팀 자체 MCP 서버는 프로젝트 venv 실행 파일이 정본이므로,
                전역에 설치된 동명 바이너리보다 우선해야 함

        Returns:
            발견된 절대 경로, 없으면 None
        """
        return await asyncio.to_thread(
            self._locate_executable, name, prefer_search_roots
        )

    def _match_signature(
        self,
        signature: ToolSignature,
        brew_packages: dict[str, str],
        distributions: dict[str, str],
        app_bundles: dict[str, Path],
        registry_entries: dict[str, str],
    ) -> list[DetectionEvidence]:
        """단일 시그니처에 대한 모든 탐지 근거 수집

        Args:
            signature: 검사할 도구 시그니처
            brew_packages: Homebrew 패키지명 → 버전
            distributions: PyPI 배포판명(소문자) → 버전
            app_bundles: 발견된 `.app` 번들명 → 경로
            registry_entries: Windows DisplayName → DisplayVersion

        Returns:
            탐지 근거 목록 (없으면 빈 리스트)
        """
        evidence: list[DetectionEvidence] = []

        for executable in signature.executables:
            path = self._locate_executable(executable)
            if path:
                method = (
                    DetectionMethod.PATH
                    if shutil.which(executable)
                    else DetectionMethod.SEARCH_ROOT
                )
                evidence.append(
                    DetectionEvidence(method=method, location=path, detail=executable)
                )

        for bundle in signature.app_bundles:
            found = app_bundles.get(bundle.lower())
            if found:
                evidence.append(
                    DetectionEvidence(
                        method=DetectionMethod.APPLICATION_BUNDLE,
                        location=str(found),
                    )
                )

        for formula in signature.homebrew_formulae:
            version = brew_packages.get(formula)
            if version is not None:
                evidence.append(
                    DetectionEvidence(
                        method=DetectionMethod.HOMEBREW,
                        location=formula,
                        detail=version or None,
                    )
                )

        for distribution in signature.python_distributions:
            version = distributions.get(distribution.lower().replace("_", "-"))
            if version is not None:
                evidence.append(
                    DetectionEvidence(
                        method=DetectionMethod.PYTHON_DISTRIBUTION,
                        location=distribution,
                        detail=version or None,
                    )
                )

        for display_name in signature.windows_display_names:
            for entry, version in registry_entries.items():
                if display_name.lower() in entry.lower():
                    evidence.append(
                        DetectionEvidence(
                            method=DetectionMethod.WINDOWS_REGISTRY,
                            location=entry,
                            detail=version or None,
                        )
                    )
                    break

        for relative in signature.windows_relative_paths:
            found = self._locate_program_files(relative)
            if found:
                evidence.append(
                    DetectionEvidence(
                        method=DetectionMethod.PROGRAM_FILES,
                        location=str(found),
                    )
                )

        return evidence

    def _locate_executable(
        self, name: str, prefer_search_roots: bool = False
    ) -> str | None:
        """PATH와 추가 탐색 루트에서 실행 파일 위치 확인

        Args:
            name: 실행 파일 이름
            prefer_search_roots: 탐색 루트를 PATH보다 먼저 확인할지 여부

        Returns:
            실행 가능한 절대 경로, 없으면 None
        """
        if not prefer_search_roots:
            found = shutil.which(name)
            if found:
                return found

        in_roots = self._search_roots_for(name)
        if in_roots:
            return in_roots

        return shutil.which(name) if prefer_search_roots else None

    def _search_roots_for(self, name: str) -> str | None:
        """추가 탐색 루트에서만 실행 파일 위치 확인

        Args:
            name: 실행 파일 이름

        Returns:
            실행 가능한 절대 경로, 없으면 None
        """
        for root in self.search_roots:
            patterns = (
                name,
                f"*/.venv/bin/{name}",
                f"*/venv/bin/{name}",
                f"bin/{name}",
            )
            for pattern in patterns:
                try:
                    for candidate in root.glob(pattern):
                        if candidate.is_file() and os.access(candidate, os.X_OK):
                            return str(candidate)
                except OSError:
                    continue
        return None

    def _locate_program_files(self, relative: str) -> Path | None:
        """Windows Program Files 계열 디렉터리에서 상대 경로 확인

        Args:
            relative: Program Files 기준 상대 경로

        Returns:
            존재하는 절대 경로, 없으면 None
        """
        if self.platform is not Platform.WINDOWS:
            return None

        roots = [
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            os.environ.get("ProgramW6432"),
        ]
        for root in roots:
            if not root:
                continue
            path = Path(root) / relative
            if path.exists():
                return path
        return None

    def _collect_homebrew_packages(self) -> dict[str, str]:
        """설치된 Homebrew formula/cask 목록과 버전 조회

        Returns:
            패키지명 → 버전 문자열 (Homebrew 미설치 시 빈 dict)
        """
        if self.platform is Platform.WINDOWS:
            return {}

        brew = shutil.which("brew")
        if not brew:
            return {}

        packages: dict[str, str] = {}
        for extra_args in (["--formula"], ["--cask"]):
            try:
                proc = subprocess.run(
                    [brew, "list", "--versions", *extra_args],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
            except (subprocess.SubprocessError, OSError) as exc:
                self._warnings.append(f"Homebrew 목록 조회 실패: {exc}")
                continue

            for line in proc.stdout.splitlines():
                parts = line.split()
                if parts:
                    packages[parts[0]] = parts[1] if len(parts) > 1 else ""
        return packages

    def _collect_distributions(self) -> dict[str, str]:
        """현재 인터프리터에 설치된 PyPI 배포판 목록 조회

        `find_spec` 대신 배포판 메타데이터를 사용해 패키지 import 부작용 회피

        Returns:
            정규화된 배포판명 → 버전
        """
        result: dict[str, str] = {}
        try:
            for dist in metadata.distributions():
                name = dist.metadata["Name"]
                if name:
                    result[name.lower().replace("_", "-")] = dist.version or ""
        except Exception as exc:
            self._warnings.append(f"Python 배포판 목록 조회 실패: {exc}")
        return result

    def _collect_app_bundles(self) -> dict[str, Path]:
        """macOS 애플리케이션 번들 목록 수집

        Returns:
            소문자 번들명 → 경로 (macOS가 아니면 빈 dict)
        """
        if self.platform is not Platform.MACOS:
            return {}

        bundles: dict[str, Path] = {}
        roots = (
            Path("/Applications"),
            Path("/Applications/Utilities"),
            Path.home() / "Applications",
        )
        for root in roots:
            if not root.is_dir():
                continue
            try:
                for entry in root.iterdir():
                    if entry.suffix == ".app":
                        bundles.setdefault(entry.name.lower(), entry)
            except OSError as exc:
                self._warnings.append(f"{root} 조회 실패: {exc}")
        return bundles

    def _collect_windows_registry(self) -> dict[str, str]:
        """Windows 언인스톨 레지스트리에서 설치 프로그램 목록 수집

        `winreg`는 Windows 전용 모듈이라 타입 스텁이 다른 플랫폼에서 해석되지
        않으므로 `import_module`로 동적 로드

        Returns:
            DisplayName → DisplayVersion (Windows가 아니면 빈 dict)
        """
        if self.platform is not Platform.WINDOWS:
            return {}

        try:
            winreg = import_module("winreg")
        except ImportError:
            self._warnings.append("winreg 모듈을 사용할 수 없어 레지스트리 스캔 생략")
            return {}

        entries: dict[str, str] = {}
        hives = (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER)

        for hive in hives:
            for key_path in WINDOWS_UNINSTALL_KEYS:
                try:
                    root = winreg.OpenKey(hive, key_path)
                except OSError:
                    continue

                with root:
                    index = 0
                    while True:
                        try:
                            subkey_name = winreg.EnumKey(root, index)
                        except OSError:
                            break
                        index += 1
                        try:
                            with winreg.OpenKey(root, subkey_name) as subkey:
                                name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                                try:
                                    version = winreg.QueryValueEx(
                                        subkey, "DisplayVersion"
                                    )[0]
                                except OSError:
                                    version = ""
                                entries[str(name)] = str(version)
                        except OSError:
                            continue
        return entries


def _first_detail(evidence: list[DetectionEvidence]) -> str | None:
    """근거 목록에서 버전으로 쓸 만한 첫 detail 반환

    Args:
        evidence: 탐지 근거 목록

    Returns:
        Homebrew/PyPI 근거의 detail 값, 없으면 None
    """
    version_methods = (
        DetectionMethod.HOMEBREW,
        DetectionMethod.PYTHON_DISTRIBUTION,
        DetectionMethod.WINDOWS_REGISTRY,
    )
    for item in evidence:
        if item.method in version_methods and item.detail:
            return item.detail
    return None
