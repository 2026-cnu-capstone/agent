"""도구 탐지 및 MCP 추천 데이터 타입

frontend 설정 화면 확장을 고려해 모든 모델을 Pydantic으로 정의하며,
`DiscoveryReport.model_dump(mode="json")` 한 번으로 직렬화 가능
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Platform(str, Enum):
    """스캐너가 구분하는 운영체제"""

    MACOS = "macos"
    WINDOWS = "windows"
    LINUX = "linux"
    UNKNOWN = "unknown"


class ToolCategory(str, Enum):
    """포렌식 도구 분류"""

    DISK_IMAGE = "disk_image"
    MEMORY = "memory"
    NETWORK = "network"
    EMAIL = "email"
    REGISTRY = "registry"
    TIMELINE = "timeline"
    MALWARE = "malware"
    DATABASE = "database"
    ENDPOINT = "endpoint"
    UTILITY = "utility"


class DetectionMethod(str, Enum):
    """도구를 발견한 경로"""

    PATH = "path"
    APPLICATION_BUNDLE = "application_bundle"
    HOMEBREW = "homebrew"
    WINDOWS_REGISTRY = "windows_registry"
    PROGRAM_FILES = "program_files"
    PYTHON_DISTRIBUTION = "python_distribution"
    SEARCH_ROOT = "search_root"


class RecommendationSource(str, Enum):
    """MCP 서버 후보의 출처"""

    TEAM = "team"
    CURATED = "curated"
    PUBLIC_REGISTRY = "public_registry"


class InstallState(str, Enum):
    """MCP 서버의 로컬 설치/등록 상태"""

    CONFIGURED = "configured"
    INSTALLED = "installed"
    NOT_INSTALLED = "not_installed"


class ToolSignature(BaseModel):
    """포렌식 도구 하나를 식별하기 위한 탐지 시그니처

    Attributes:
        tool_id: 카탈로그 내 고유 식별자 (snake_case)
        display_name: 사용자에게 보여줄 도구 이름
        category: 도구 분류
        description: 도구 설명 (한국어)
        executables: PATH에서 찾을 실행 파일명 목록
        app_bundles: macOS `.app` 번들명 목록 (확장자 포함)
        homebrew_formulae: Homebrew formula/cask 이름 목록
        windows_display_names: Windows 언인스톨 레지스트리 DisplayName 부분 문자열
        windows_relative_paths: Program Files 하위 상대 경로 목록
        python_distributions: 설치 여부를 확인할 PyPI 배포판 이름 목록
        registry_keywords: 공개 MCP 레지스트리 검색에 사용할 키워드
        relevance_terms: 레지스트리 결과 관련성 채점에 사용할 용어
    """

    tool_id: str
    display_name: str
    category: ToolCategory
    description: str
    executables: tuple[str, ...] = ()
    app_bundles: tuple[str, ...] = ()
    homebrew_formulae: tuple[str, ...] = ()
    windows_display_names: tuple[str, ...] = ()
    windows_relative_paths: tuple[str, ...] = ()
    python_distributions: tuple[str, ...] = ()
    registry_keywords: tuple[str, ...] = ()
    relevance_terms: tuple[str, ...] = ()


class DetectionEvidence(BaseModel):
    """도구 발견 근거 한 건

    Attributes:
        method: 탐지 방법
        location: 실제 경로 또는 레지스트리 항목명
        detail: 버전 등 부가 정보
    """

    method: DetectionMethod
    location: str
    detail: str | None = None


class DetectedTool(BaseModel):
    """로컬 시스템에서 발견된 포렌식 도구

    Attributes:
        tool_id: 대응하는 ToolSignature의 tool_id
        display_name: 도구 이름
        category: 도구 분류
        evidence: 발견 근거 목록 (탐지 방법별 실제 경로/버전)
        version: 확인된 버전 (미확인 시 None)
    """

    tool_id: str
    display_name: str
    category: ToolCategory
    evidence: list[DetectionEvidence] = Field(default_factory=list)
    version: str | None = None

    @property
    def primary_path(self) -> str | None:
        """가장 신뢰도 높은 발견 경로 반환"""
        return self.evidence[0].location if self.evidence else None


class MCPServerCandidate(BaseModel):
    """추천 대상이 되는 MCP 서버 후보

    Attributes:
        server_id: mcp_servers.json에 등록될 서버 키
        display_name: 사용자에게 보여줄 이름
        description: 서버 설명
        source: 후보 출처 (팀/큐레이션/공개 레지스트리)
        registry_name: 공개 레지스트리상의 정규 이름 (있는 경우)
        homepage: 저장소 또는 문서 URL
        transport: MCP transport 유형
        install_hint: 설치 방법 안내 문자열
        command: 등록 시 사용할 실행 명령 (알려진 경우)
        args: 등록 시 사용할 인자 목록
        url: SSE transport 서버의 원격 endpoint URL
        probe_executables: 로컬 설치 여부 확인용 실행 파일명 목록
        covered_tool_ids: 이 서버가 다루는 도구의 tool_id 목록
    """

    server_id: str
    display_name: str
    description: str
    source: RecommendationSource
    registry_name: str | None = None
    homepage: str | None = None
    transport: str = "stdio"
    install_hint: str | None = None
    command: str | None = None
    args: tuple[str, ...] = ()
    url: str | None = None
    probe_executables: tuple[str, ...] = ()
    covered_tool_ids: tuple[str, ...] = ()


class VerificationResult(BaseModel):
    """MCP 서버 핸드셰이크 검증 결과

    Attributes:
        ok: initialize + list_tools 성공 여부
        server_name: 서버가 보고한 이름
        server_version: 서버가 보고한 버전
        tool_count: 노출하는 도구 개수
        tool_names: 도구 이름 목록 (최대 20개)
        error: 실패 사유
    """

    ok: bool
    server_name: str | None = None
    server_version: str | None = None
    tool_count: int = 0
    tool_names: list[str] = Field(default_factory=list)
    error: str | None = None


class MCPRecommendation(BaseModel):
    """탐지된 도구에 대한 MCP 서버 추천 한 건

    Attributes:
        candidate: 추천된 MCP 서버 후보
        matched_tools: 추천 근거가 된 탐지 도구의 tool_id 목록
        score: 관련성 점수 (0.0~1.0, 높을수록 강한 추천)
        install_state: 로컬 설치/등록 상태
        resolved_command: 로컬에서 실제로 발견된 실행 파일 경로
        reason: 추천 사유 (한국어 설명)
        verification: 실제 동작 검증 결과 (미검증 시 None)
    """

    candidate: MCPServerCandidate
    matched_tools: list[str] = Field(default_factory=list)
    score: float = 0.0
    install_state: InstallState = InstallState.NOT_INSTALLED
    resolved_command: str | None = None
    reason: str = ""
    verification: VerificationResult | None = None


class InstallOutcome(BaseModel):
    """MCP 서버 설치 실행 결과

    Attributes:
        server_id: 설치 대상 서버 키
        ok: 설치 성공 여부
        resolved_command: 설치 후 확인된 실행 파일 경로
        error: 실패 사유
        log: 설치 명령 출력 (디버깅용)
    """

    server_id: str
    ok: bool
    resolved_command: str | None = None
    error: str | None = None
    log: str | None = None


class ServerProposal(BaseModel):
    """mcp_servers.json에 추가할 서버 등록 제안

    자동 반영하지 않고 사용자 승인 후 `apply_proposals`로 적용

    Attributes:
        server_id: 등록될 서버 키
        entry: mcpServers[server_id]에 삽입될 JSON 객체
        ready: 실행 경로가 확정되어 즉시 적용 가능한지 여부
        blocked_reason: ready가 False인 사유
    """

    server_id: str
    entry: dict[str, object]
    ready: bool
    blocked_reason: str | None = None


class DiscoveryReport(BaseModel):
    """도구 탐지 + MCP 추천 전체 결과

    Attributes:
        platform: 스캔이 수행된 운영체제
        scanned_at: 스캔 완료 시각 (ISO 8601 UTC)
        detected_tools: 발견된 포렌식 도구 목록
        recommendations: 점수 내림차순 MCP 추천 목록
        proposals: mcp_servers.json 반영 제안 목록
        configured_servers: 이미 등록된 MCP 서버 키 목록
        registry_available: 공개 레지스트리 조회 성공 여부
        warnings: 스캔/조회 중 발생한 경고 메시지
    """

    platform: Platform
    scanned_at: str
    detected_tools: list[DetectedTool] = Field(default_factory=list)
    recommendations: list[MCPRecommendation] = Field(default_factory=list)
    proposals: list[ServerProposal] = Field(default_factory=list)
    configured_servers: list[str] = Field(default_factory=list)
    registry_available: bool = False
    warnings: list[str] = Field(default_factory=list)
