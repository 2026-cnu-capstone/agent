"""포렌식 도구 시그니처 및 도구↔MCP 서버 매핑 카탈로그

두 개의 정적 테이블로 구성:

- ``FORENSIC_TOOLS``: 로컬 스캔에 사용할 도구 탐지 시그니처
- ``MCP_CATALOG``: 팀 자체 MCP 서버 + 검증된 공개 MCP 서버 후보

공개 MCP 레지스트리(registry.modelcontextprotocol.io)는 포렌식 도메인 커버리지가
희박하고 검색이 부분 문자열 매칭이라 오탐이 많아, 카탈로그를 1차 근거로 두고
레지스트리 조회는 보강 수단으로만 사용
"""

from __future__ import annotations

from discovery.types import (
    MCPServerCandidate,
    RecommendationSource,
    ToolCategory,
    ToolSignature,
)

FORENSIC_TOOLS: tuple[ToolSignature, ...] = (
    ToolSignature(
        tool_id="sleuthkit",
        display_name="The Sleuth Kit",
        category=ToolCategory.DISK_IMAGE,
        description="파일시스템 및 디스크 이미지 분석 CLI 모음",
        executables=(
            "fls",
            "mmls",
            "icat",
            "img_stat",
            "fsstat",
            "tsk_recover",
            "blkls",
        ),
        homebrew_formulae=("sleuthkit",),
        windows_display_names=("The Sleuth Kit",),
        python_distributions=("pytsk3",),
        registry_keywords=("sleuthkit", "tsk", "disk forensics"),
        relevance_terms=(
            "sleuthkit",
            "sleuth",
            "tsk",
            "filesystem",
            "forensic",
            "disk",
        ),
    ),
    ToolSignature(
        tool_id="autopsy",
        display_name="Autopsy",
        category=ToolCategory.DISK_IMAGE,
        description="Sleuth Kit 기반 통합 디스크 포렌식 GUI 플랫폼",
        executables=("autopsy",),
        app_bundles=("Autopsy.app",),
        windows_display_names=("Autopsy",),
        windows_relative_paths=("Autopsy/bin/autopsy64.exe",),
        registry_keywords=("autopsy",),
        relevance_terms=("autopsy", "forensic", "sleuthkit"),
    ),
    ToolSignature(
        tool_id="dissect",
        display_name="Dissect",
        category=ToolCategory.DISK_IMAGE,
        description="Fox-IT 디스크 이미지/아티팩트 추출 프레임워크",
        executables=(
            "target-query",
            "target-shell",
            "target-fs",
            "target-info",
            "rdump",
        ),
        python_distributions=("dissect.target", "dissect"),
        registry_keywords=("dissect", "target-query"),
        relevance_terms=("dissect", "forensic", "artifact", "disk", "target"),
    ),
    ToolSignature(
        tool_id="libewf",
        display_name="libewf",
        category=ToolCategory.DISK_IMAGE,
        description="EWF(E01) 증거 이미지 읽기/변환/마운트 도구",
        executables=("ewfinfo", "ewfmount", "ewfexport", "ewfacquire", "ewfverify"),
        homebrew_formulae=("libewf",),
        registry_keywords=("ewf", "e01", "expert witness format"),
        relevance_terms=("ewf", "e01", "evidence", "image", "forensic"),
    ),
    ToolSignature(
        tool_id="afflib",
        display_name="AFFLIB",
        category=ToolCategory.DISK_IMAGE,
        description="AFF 증거 이미지 포맷 처리 라이브러리 및 CLI",
        executables=("affinfo", "affconvert", "affuse"),
        homebrew_formulae=("afflib",),
        registry_keywords=("afflib", "advanced forensic format"),
        relevance_terms=("aff", "forensic", "image"),
    ),
    ToolSignature(
        tool_id="ftk_imager",
        display_name="FTK Imager",
        category=ToolCategory.DISK_IMAGE,
        description="Exterro 디스크 이미징 및 미리보기 도구",
        windows_display_names=("FTK Imager",),
        windows_relative_paths=("AccessData/FTK Imager/FTK Imager.exe",),
        registry_keywords=("ftk imager",),
        relevance_terms=("ftk", "imager", "forensic", "acquisition"),
    ),
    ToolSignature(
        tool_id="volatility3",
        display_name="Volatility 3",
        category=ToolCategory.MEMORY,
        description="메모리 덤프 분석 프레임워크",
        executables=("vol", "volatility3", "vol.py"),
        python_distributions=("volatility3",),
        registry_keywords=("volatility", "memory forensics"),
        relevance_terms=("volatility", "memory", "dump", "forensic", "ram"),
    ),
    ToolSignature(
        tool_id="plaso",
        display_name="Plaso / log2timeline",
        category=ToolCategory.TIMELINE,
        description="슈퍼 타임라인 생성 및 정렬 도구",
        executables=("log2timeline.py", "psort.py", "pinfo.py", "psteal.py"),
        python_distributions=("plaso",),
        registry_keywords=("plaso", "log2timeline", "timeline forensics"),
        relevance_terms=("plaso", "log2timeline", "timeline", "forensic"),
    ),
    ToolSignature(
        tool_id="yara",
        display_name="YARA",
        category=ToolCategory.MALWARE,
        description="악성코드 패턴 시그니처 매칭 엔진",
        executables=("yara", "yarac"),
        homebrew_formulae=("yara",),
        python_distributions=("yara-python",),
        registry_keywords=("yara",),
        relevance_terms=("yara", "malware", "signature", "rule", "scan"),
    ),
    ToolSignature(
        tool_id="ghidra",
        display_name="Ghidra",
        category=ToolCategory.MALWARE,
        description="NSA 리버스 엔지니어링 및 디컴파일 플랫폼",
        executables=("ghidraRun", "analyzeHeadless"),
        app_bundles=("Ghidra.app",),
        homebrew_formulae=("ghidra",),
        windows_display_names=("Ghidra",),
        registry_keywords=("ghidra",),
        relevance_terms=("ghidra", "decompile", "reverse", "binary", "disassemble"),
    ),
    ToolSignature(
        tool_id="wireshark",
        display_name="Wireshark / tshark",
        category=ToolCategory.NETWORK,
        description="패킷 캡처(PCAP) 분석 도구",
        executables=("tshark", "wireshark", "capinfos", "editcap"),
        app_bundles=("Wireshark.app",),
        homebrew_formulae=("wireshark",),
        windows_display_names=("Wireshark",),
        registry_keywords=("wireshark", "tshark", "pcap"),
        relevance_terms=("wireshark", "tshark", "pcap", "packet", "network", "capture"),
    ),
    ToolSignature(
        tool_id="nmap",
        display_name="Nmap",
        category=ToolCategory.NETWORK,
        description="네트워크 호스트 및 서비스 스캐너",
        executables=("nmap",),
        homebrew_formulae=("nmap",),
        windows_display_names=("Nmap",),
        registry_keywords=("nmap",),
        relevance_terms=("nmap", "port", "scan", "network", "recon"),
    ),
    ToolSignature(
        tool_id="libpff",
        display_name="libpff",
        category=ToolCategory.EMAIL,
        description="Outlook PST/OST 메일 저장소 파싱 라이브러리",
        executables=("pffinfo", "pffexport"),
        homebrew_formulae=("libpff",),
        python_distributions=("libpff-python", "pypff"),
        registry_keywords=("pst", "ost", "outlook forensics"),
        relevance_terms=("pst", "ost", "outlook", "mailbox", "email", "pff"),
    ),
    ToolSignature(
        tool_id="libpst",
        display_name="libpst / readpst",
        category=ToolCategory.EMAIL,
        description="PST 파일을 mbox/EML로 변환하는 도구",
        executables=("readpst", "lspst"),
        homebrew_formulae=("libpst",),
        registry_keywords=("readpst", "pst converter"),
        relevance_terms=("pst", "readpst", "mbox", "email", "mail"),
    ),
    ToolSignature(
        tool_id="regripper",
        display_name="RegRipper",
        category=ToolCategory.REGISTRY,
        description="Windows 레지스트리 하이브 플러그인 기반 파서",
        executables=("rip.pl", "rip.exe", "regripper"),
        windows_display_names=("RegRipper",),
        registry_keywords=("regripper", "windows registry"),
        relevance_terms=("regripper", "registry", "hive", "windows", "ntuser"),
    ),
    ToolSignature(
        tool_id="eric_zimmerman_tools",
        display_name="Eric Zimmerman Tools",
        category=ToolCategory.REGISTRY,
        description="EvtxECmd/MFTECmd/RECmd 등 Windows 아티팩트 파서 모음",
        executables=("EvtxECmd", "MFTECmd", "RECmd", "AmcacheParser", "PECmd"),
        windows_display_names=("Eric Zimmerman",),
        windows_relative_paths=("EZTools/EvtxECmd/EvtxECmd.exe",),
        registry_keywords=("evtx", "mft", "windows artifact"),
        relevance_terms=("evtx", "mft", "amcache", "prefetch", "windows", "artifact"),
    ),
    ToolSignature(
        tool_id="kape",
        display_name="KAPE",
        category=ToolCategory.ENDPOINT,
        description="타깃 아티팩트 수집 및 모듈 실행 프레임워크",
        executables=("kape.exe", "gkape.exe"),
        windows_display_names=("KAPE",),
        registry_keywords=("kape", "artifact collection"),
        relevance_terms=("kape", "triage", "collection", "artifact"),
    ),
    ToolSignature(
        tool_id="velociraptor",
        display_name="Velociraptor",
        category=ToolCategory.ENDPOINT,
        description="엔드포인트 가시성 및 원격 포렌식 수집 플랫폼",
        executables=("velociraptor",),
        homebrew_formulae=("velociraptor",),
        windows_display_names=("Velociraptor",),
        registry_keywords=("velociraptor",),
        relevance_terms=("velociraptor", "endpoint", "dfir", "hunt", "vql"),
    ),
    ToolSignature(
        tool_id="osquery",
        display_name="osquery",
        category=ToolCategory.ENDPOINT,
        description="운영체제 상태를 SQL로 조회하는 엔드포인트 에이전트",
        executables=("osqueryi", "osqueryd"),
        homebrew_formulae=("osquery",),
        windows_display_names=("osquery",),
        registry_keywords=("osquery",),
        relevance_terms=("osquery", "endpoint", "sql", "telemetry"),
    ),
    ToolSignature(
        tool_id="sqlite",
        display_name="SQLite",
        category=ToolCategory.DATABASE,
        description="브라우저/앱 아티팩트가 저장되는 SQLite DB 조회 도구",
        executables=("sqlite3",),
        app_bundles=("DB Browser for SQLite.app",),
        homebrew_formulae=("sqlite", "db-browser-for-sqlite"),
        windows_display_names=("DB Browser for SQLite",),
        registry_keywords=("sqlite",),
        relevance_terms=("sqlite", "database", "query", "db"),
    ),
    ToolSignature(
        tool_id="exiftool",
        display_name="ExifTool",
        category=ToolCategory.UTILITY,
        description="파일 메타데이터 추출 및 편집 도구",
        executables=("exiftool",),
        homebrew_formulae=("exiftool",),
        windows_display_names=("ExifTool",),
        registry_keywords=("exiftool",),
        relevance_terms=("exiftool", "exif", "metadata"),
    ),
    ToolSignature(
        tool_id="binwalk",
        display_name="binwalk",
        category=ToolCategory.UTILITY,
        description="펌웨어/바이너리 내 임베디드 파일 카빙 도구",
        executables=("binwalk",),
        homebrew_formulae=("binwalk",),
        registry_keywords=("binwalk", "carving"),
        relevance_terms=("binwalk", "firmware", "carve", "binary", "extract"),
    ),
    ToolSignature(
        tool_id="bulk_extractor",
        display_name="bulk_extractor",
        category=ToolCategory.UTILITY,
        description="디스크 이미지에서 이메일/URL/카드번호 등 특징 추출",
        executables=("bulk_extractor",),
        homebrew_formulae=("bulk_extractor",),
        registry_keywords=("bulk extractor",),
        relevance_terms=("bulk", "extractor", "feature", "forensic", "carve"),
    ),
    ToolSignature(
        tool_id="hashdeep",
        display_name="hashdeep / md5deep",
        category=ToolCategory.UTILITY,
        description="증거 무결성 검증용 재귀 해시 계산 도구",
        executables=("hashdeep", "md5deep", "sha256deep"),
        homebrew_formulae=("hashdeep", "md5deep"),
        registry_keywords=("hashdeep", "md5deep"),
        relevance_terms=("hashdeep", "md5deep", "md5", "sha256", "checksum"),
    ),
    ToolSignature(
        tool_id="hex_editor",
        display_name="Hex Editor",
        category=ToolCategory.UTILITY,
        description="바이너리 수동 검토용 헥스 에디터",
        executables=("hexyl",),
        app_bundles=("Hex Fiend.app", "010 Editor.app"),
        homebrew_formulae=("hexyl",),
        windows_display_names=("HxD", "010 Editor"),
        registry_keywords=("hex editor",),
        relevance_terms=("hex", "editor", "binary", "dump"),
    ),
)

TOOL_INDEX: dict[str, ToolSignature] = {sig.tool_id: sig for sig in FORENSIC_TOOLS}

MCP_CATALOG: tuple[MCPServerCandidate, ...] = (
    MCPServerCandidate(
        server_id="dissect",
        display_name="dissect-mcp (팀 자체)",
        description="Dissect 기반 디스크 이미지 아티팩트 추출 MCP 서버",
        source=RecommendationSource.TEAM,
        homepage="https://github.com/2026-cnu-capstone/dissect-mcp",
        install_hint="dissect-mcp 저장소를 클론한 뒤 `uv sync`로 .venv 생성",
        probe_executables=("dissect-mcp",),
        covered_tool_ids=("dissect", "sleuthkit", "libewf", "afflib", "autopsy"),
    ),
    MCPServerCandidate(
        server_id="pst",
        display_name="pst-mcp (팀 자체)",
        description="Outlook PST/OST 메일 아티팩트 분석 MCP 서버",
        source=RecommendationSource.TEAM,
        homepage="https://github.com/2026-cnu-capstone/pst-mcp",
        install_hint="pst-mcp 저장소를 클론한 뒤 `uv sync`로 .venv 생성",
        probe_executables=("pst-mcp",),
        covered_tool_ids=("libpff", "libpst"),
    ),
    MCPServerCandidate(
        server_id="mcpcap",
        display_name="mcpcap",
        description="PCAP 캡처 파일 분석 MCP 서버",
        source=RecommendationSource.CURATED,
        registry_name="ai.mcpcap/mcpcap",
        homepage="https://github.com/mcpcap/mcpcap",
        install_hint="uvx mcpcap",
        command="uvx",
        args=("mcpcap",),
        probe_executables=("mcpcap",),
        covered_tool_ids=("wireshark",),
    ),
    MCPServerCandidate(
        server_id="wireshark",
        display_name="Wireshark MCP",
        description="tshark 기반 네트워크 트래픽 심층 분석 MCP 서버",
        source=RecommendationSource.CURATED,
        registry_name="io.github.bx33661/wireshark-mcp",
        homepage="https://github.com/bx33661/Wireshark-MCP",
        install_hint="uvx wireshark-mcp",
        command="uvx",
        args=("wireshark-mcp",),
        probe_executables=("wireshark-mcp",),
        covered_tool_ids=("wireshark",),
    ),
    MCPServerCandidate(
        server_id="exiftool",
        display_name="ExifTool MCP",
        description="ExifTool로 파일 메타데이터를 읽고 쓰는 MCP 서버",
        source=RecommendationSource.CURATED,
        registry_name="io.github.joshmsimpson/exiftool",
        homepage="https://github.com/joshmsimpson/exiftool_mcp",
        install_hint="uvx exiftool-mcp",
        command="uvx",
        args=("exiftool-mcp",),
        probe_executables=("exiftool-mcp",),
        covered_tool_ids=("exiftool",),
    ),
    MCPServerCandidate(
        server_id="pyghidra",
        display_name="pyghidra-lite",
        description="Ghidra 헤드리스 디컴파일/역참조 조회 MCP 서버",
        source=RecommendationSource.CURATED,
        registry_name="io.github.johnzfitch/pyghidra-lite",
        homepage="https://github.com/johnzfitch/pyghidra-lite",
        install_hint="uvx pyghidra-lite",
        command="uvx",
        args=("pyghidra-lite",),
        probe_executables=("pyghidra-lite",),
        covered_tool_ids=("ghidra",),
    ),
    MCPServerCandidate(
        server_id="sqlite",
        display_name="SQLite MCP",
        description="SQLite 데이터베이스 탐색 및 쿼리 MCP 서버",
        source=RecommendationSource.CURATED,
        registry_name="io.github.neverinfamous/sqlite-mcp-server",
        homepage="https://github.com/neverinfamous/sqlite-mcp-server",
        install_hint="uvx sqlite-mcp-server-enhanced",
        command="uvx",
        args=("sqlite-mcp-server-enhanced",),
        probe_executables=("sqlite-mcp-server-enhanced",),
        covered_tool_ids=("sqlite",),
    ),
)

CATALOG_INDEX: dict[str, MCPServerCandidate] = {
    candidate.server_id: candidate for candidate in MCP_CATALOG
}


def signatures_for_platform(platform: str) -> tuple[ToolSignature, ...]:
    """해당 플랫폼에서 탐지 가능한 시그니처만 반환

    Windows 전용 필드만 가진 시그니처는 비Windows 스캔에서 제외해
    불필요한 탐색을 줄임

    Args:
        platform: Platform enum 값 문자열

    Returns:
        스캔 대상 시그니처 튜플
    """
    if platform == "windows":
        return FORENSIC_TOOLS

    result = []
    for sig in FORENSIC_TOOLS:
        windows_only = (
            not sig.executables
            and not sig.app_bundles
            and not sig.homebrew_formulae
            and not sig.python_distributions
        )
        if not windows_only:
            result.append(sig)
    return tuple(result)


def candidates_for_tool(tool_id: str) -> tuple[MCPServerCandidate, ...]:
    """특정 도구를 커버하는 카탈로그 MCP 후보 반환

    Args:
        tool_id: 탐지된 도구의 tool_id

    Returns:
        해당 도구를 다루는 MCP 서버 후보 튜플
    """
    return tuple(c for c in MCP_CATALOG if tool_id in c.covered_tool_ids)
