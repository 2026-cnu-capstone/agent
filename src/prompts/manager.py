"""Manager Agent 프롬프트

사용처:
    - src/agents/manager/nodes.py → strategy_node
    - src/agents/manager/nodes.py → planning_node
"""

from __future__ import annotations


def build_strategy_prompt(
    disk_image_format: str = "",
    system_profile: str = "",
    rag_context: str = "",
) -> str:
    """사건 정보를 바탕으로 조사할 아티팩트 목록 도출

    Args:
        disk_image_format: 디스크 이미지 형식 (e01, dd 등)
        system_profile: 시스템 프로필 정보 (OS, 호스트명 등)
        rag_context: RAG로 검색된 유사 사례 전략 텍스트
    """
    context_parts = []
    if disk_image_format:
        context_parts.append(f"디스크 이미지 형식: {disk_image_format}")
    if system_profile:
        context_parts.append(f"시스템 정보:\n{system_profile}")
    context_block = "\n".join(context_parts)

    rag_section = ""
    if rag_context:
        rag_section = f"""
[유사 사건 조사 경험 (참고용)]
아래는 과거 유사 사건에서 사용된 조사 전략입니다. 참고하되 현재 사건에 맞게 판단하세요.

{rag_context}
"""

    return f"""당신은 디지털 포렌식 분석 전문가 AI입니다.

사용자가 제공한 사건 정보를 바탕으로, 이 사건에서 조사해야 할 아티팩트 목록만 작성하세요.
설명이나 분석 방법은 쓰지 말고, 아티팩트 이름과 조사 이유를 한 줄씩 나열하세요.

{f"[디스크 이미지 정보]{chr(10)}{context_block}{chr(10)}" if context_block else ""}{rag_section}출력 형식 (이 형식만 사용):

## 조사 대상 아티팩트
- [아티팩트명]: [조사 이유 한 줄]
- [아티팩트명]: [조사 이유 한 줄]
...

예시:
- Registry (NTUSER.DAT, SOFTWARE): 사용자 최근 실행 파일 및 암호화 소프트웨어 설치 흔적 확인
- Event Log (Security.evtx, Application.evtx): 로그인/로그오프 및 관련 이벤트 확인
- Prefetch + Amcache: 실행된 프로그램 목록 및 실행 시각 확인
- Browser History: 방문 URL 및 다운로드 파일 확인

사건과 관련된 아티팩트만 선별하고, 불필요한 항목은 포함하지 마세요."""


LEGACY_WINDOWS_MARKERS: tuple[str, ...] = (
    "nt 5.0",
    "nt 5.1",
    "nt 5.2",
    "windows xp",
    "windows 2000",
    "windows server 2003",
)
"""사용자 프로필 경로가 `Documents and Settings`인 Windows 버전 표식"""

LEGACY_WINDOWS_PATHS = r"""[대상 OS 경로 규칙 — Windows XP / 2000 / 2003 계열]
이 이미지는 Vista 이전 계열이므로 최신 Windows 경로를 사용하면 아무것도 찾지 못합니다.
- 사용자 프로필: `Documents and Settings/<user>` (`Users/<user>` 아님)
- 문서 폴더: `My Documents` (`Documents` 아님)
- 앱 데이터: `Documents and Settings/<user>/Local Settings/Application Data`
- 이벤트 로그: `WINDOWS/system32/config/*.evt` (`.evtx`, `winevt/Logs` 아님)
- 레지스트리 하이브: `WINDOWS/system32/config/{SOFTWARE,SYSTEM,SAM,SECURITY}`
- 사용자 하이브: `Documents and Settings/<user>/NTUSER.DAT`
- Prefetch: `WINDOWS/Prefetch/*.pf` (Amcache/SRUM은 이 버전에 없음)
- 휴지통: `RECYCLER/<SID>` (`$Recycle.Bin` 아님)"""

MODERN_WINDOWS_MARKERS: tuple[str, ...] = (
    "nt 6.",
    "nt 10.",
    "windows vista",
    "windows 7",
    "windows 8",
    "windows 10",
    "windows 11",
    "windows server 2008",
    "windows server 2012",
    "windows server 2016",
    "windows server 2019",
    "windows server 2022",
)
"""사용자 프로필 경로가 `Users`인 Windows 버전 표식"""

UNKNOWN_WINDOWS_PATHS = r"""[대상 OS 경로 규칙 — Windows 버전 미확인]
시스템 프로필에 OS 버전이 없어 경로 규약을 확정할 수 없습니다.
**첫 단계에서 get_target_info로 OS 버전을 먼저 확인하고, 그 결과에 맞는 경로만 사용하세요.**
버전을 확인하기 전에는 경로를 단정하지 말고 list_directory로 실재를 확인하세요.
- Vista 이상: `Users/<user>`, `AppData/{Local,Roaming}`,
  `Windows/System32/winevt/Logs/*.evtx`, `$Recycle.Bin`
- XP / 2000 / 2003: `Documents and Settings/<user>`, `My Documents`,
  `Local Settings/Application Data`, `WINDOWS/system32/config/*.evt`, `RECYCLER`
  (Amcache/SRUM 없음)"""

MODERN_WINDOWS_PATHS = r"""[대상 OS 경로 규칙 — Windows Vista 이상]
- 사용자 프로필: `Users/<user>`
- 앱 데이터: `Users/<user>/AppData/{Local,Roaming}`
- 이벤트 로그: `Windows/System32/winevt/Logs/*.evtx`
- 레지스트리 하이브: `Windows/System32/config/{SOFTWARE,SYSTEM,SAM,SECURITY}`
- Prefetch: `Windows/Prefetch/*.pf`, Amcache: `Windows/appcompat/Programs/Amcache.hve`
- 휴지통: `$Recycle.Bin/<SID>`"""

FILESYSTEM_SEARCH_RULES = r"""[파일시스템 검색 규칙]
- 디스크는 `/c:`와 `/sysvol` 두 마운트로 노출됩니다. 상대 경로 패턴은 두 마운트 모두에 매칭됩니다.
- 존재를 확신하지 못하는 경로는 search_filesystem 대신 list_directory로 먼저 상위 디렉터리를 확인하세요.
- 디렉터리명 추측은 실패하기 쉽습니다. 예를 들어 NetStumbler의 실제 설치 폴더명은 `Network Stumbler`입니다.
- 레지스트리/실행 흔적은 query_plugin이 더 정확합니다. 파일 검색은 보조 수단으로 쓰세요."""


def build_path_conventions(system_profile: str) -> str:
    """시스템 프로필에서 대상 OS에 맞는 경로 규칙 문자열 생성

    계획 단계 LLM이 Vista 이후 경로(`Users/`, `AppData`)를 XP 이미지에 적용해
    모든 검색이 빈 결과로 끝나는 문제를 막기 위함

    버전 표식이 없으면 어느 쪽으로도 단정하지 않고 확인을 요구하는 섹션을 반환.
    프로필이 `os: windows`만 담고 버전이 빠진 경우가 실제로 있어, 기본값을
    최신 계열로 두면 구버전 이미지에서 전 단계가 빈 결과로 끝남

    Args:
        system_profile: open_target이 반환한 시스템 프로필 텍스트

    Returns:
        프롬프트에 삽입할 경로 규칙 섹션 (Windows가 아니면 빈 문자열)
    """
    if not system_profile:
        return ""

    lowered = system_profile.lower()
    if "windows" not in lowered:
        return ""

    if any(marker in lowered for marker in LEGACY_WINDOWS_MARKERS):
        return LEGACY_WINDOWS_PATHS
    if any(marker in lowered for marker in MODERN_WINDOWS_MARKERS):
        return MODERN_WINDOWS_PATHS
    return UNKNOWN_WINDOWS_PATHS


def build_planning_prompt(
    strategy: str,
    mcp_servers: str = "",
    rag_context: str = "",
    system_profile: str = "",
) -> str:
    """수립된 전략을 바탕으로 세부 실행 계획 수립 프롬프트

    각 단계에서 어떤 MCP 서버(= Sub-Agent)를 사용할지만 지정.
    구체적인 MCP 도구 선택은 Sub-Agent가 실행 중 자율 결정.

    Args:
        strategy: strategy_node가 수립한 분석 전략 텍스트
        mcp_servers: 사용 가능한 MCP 서버 목록 문자열
        rag_context: RAG로 검색된 유사 사례 실행 계획 텍스트
        system_profile: 대상 이미지의 시스템 프로필 (OS별 경로 규칙 주입용)
    """
    rag_section = ""
    if rag_context:
        rag_section = f"""
[유사 사건의 실행 계획 참고]
{rag_context}
"""

    path_section = build_path_conventions(system_profile)
    if path_section:
        path_section = f"\n{path_section}\n\n{FILESYSTEM_SEARCH_RULES}\n"

    return f"""당신은 디지털 포렌식 분석 전문가 AI입니다.

아래 조사 대상 아티팩트 목록을 바탕으로 단계별 실행 계획을 작성하세요.
각 단계마다 어떤 MCP 서버를 사용할지 지정하세요.
**구체적인 도구는 지정하지 마세요.** 각 단계를 담당하는 Sub-Agent가 실행 중 최적의 도구를 자율 선택합니다.

[조사 대상 아티팩트]
{strategy}

[사용 가능한 MCP 서버]
{mcp_servers if mcp_servers else "(연결된 서버 없음)"}
{path_section}{rag_section}
---

**가장 먼저** 아래 JSON을 코드 블록으로 출력하세요. (상세 설명보다 반드시 앞에 위치)
위 계획의 모든 단계를 steps 배열에 빠짐없이 포함해야 합니다.
MCP 서버가 필요 없는 단계는 mcp_server에 "none"을 넣으세요.

**hints 필드에는 반드시 사용할 dissect 플러그인명을 포함하세요.**
Sub-Agent는 hints에 명시된 플러그인을 query_plugin으로 우선 호출합니다.
예: "플러그인: sam, userassist / 레지스트리 키: HKLM\\SAM"

```json
{{
  "steps": [
    {{
      "index": 1,
      "name": "단계명",
      "mcp_server": "담당 MCP 서버명 또는 none",
      "purpose": "이 단계에서 확인할 내용 (Sub-Agent에게 전달됨)",
      "artifacts": ["조사 대상 아티팩트 목록"],
      "hints": "플러그인: plugin1, plugin2 / 추가 힌트 (경로, 키워드 등)"
    }}
  ]
}}
```

JSON 출력 이후에 아래 형식으로 단계별 요약 테이블을 작성하세요.

## 단계별 실행 계획

| 단계 | 분석 대상 | 목적 | 담당 MCP 서버 |
|------|----------|------|--------------|
| 1 | [아티팩트명] | [확인할 내용] | [서버명 또는 없음] |
| 2 | ... | ... | ... |"""
