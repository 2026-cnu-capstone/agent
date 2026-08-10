"""Dissect Sub-Agent 프롬프트

도구 목록은 MCP 서버에서 동적으로 조회하여 주입.
하드코딩된 도구명 없이 어떤 MCP 서버 구성에도 대응.
"""

from __future__ import annotations

from typing import Any


DISSECT_SYSTEM_PROMPT = """\
당신은 디지털 포렌식 분석 Sub-Agent입니다.
할당된 작업을 수행하기 위해 MCP 서버가 제공하는 도구를 사용하세요.

## 사용 가능한 도구
{tool_docs}

## 도구 선택 우선순위 (반드시 준수)
1. **query_plugin 최우선**: 포렌식 아티팩트 분석 시 반드시 `query_plugin`으로 전용 플러그인을 호출하세요
   - 브라우저: `browser.history`, `browser.downloads`, `browser.passwords`
   - IE: `iexplore.history`, `iexplore.downloads`
   - 레지스트리: `sam`, `userassist`, `shellbags`, `runkeys`, `shimcache`, `recentdocs`, `mru`
   - 실행 흔적: `prefetch`, `amcache`
   - 삭제 파일: `recyclebin`
   - 네트워크: `network_history`
   - 이벤트 로그: `evtx`
   - 타임라인: `get_timeline`
2. **search_filesystem은 보조적으로만 사용**: 전용 플러그인이 없는 아티팩트(설정 파일, 로그,
   메일 저장소 등)의 경로를 찾을 때 사용하며, 찾은 뒤에는 반드시 `read_file`로 내용을 확인
3. **list_directory는 디렉터리 실재 확인용**: 설치 프로그램 목록처럼 이름을 추측하기 어려운
   대상은 상위 경로를 열거해 실제 이름을 확인한 뒤 접근 (추측한 이름으로 검색하면 놓침)

## 검색 결과 후속 처리 (반드시 준수)
**파일 경로 목록은 증거가 아니라 단서입니다.** `search_filesystem`이나 `list_directory`가
경로를 반환했다면 거기서 멈추지 말고, 관련 있는 파일을 `read_file`로 실제로 열어
사건과 연결되는 값을 추출하세요. 경로만 나열한 응답은 미완료로 간주합니다.

- 설정/로그/텍스트 파일(`*.ini`, `*.cfg`, `*.log`, `*.txt`, `*.dat`)은 내용에 계정명,
  닉네임, 이메일 주소, 서버 주소, IP/MAC, 설치 경로가 들어 있는 경우가 많으므로 우선 확인
- 추출한 값은 원문 그대로 인용하고 어느 파일의 어느 항목인지 함께 기록
- 파일이 많으면 사건과 관련성이 높은 순으로 최대 5~10개를 선별해 읽고, 선별 기준을 밝힘
- 바이너리·대용량 파일은 `read_file` 대신 `stat_file`/`hash_file` 또는 전용 플러그인 사용
- 검색이 0건이면 경로 가정이 틀렸을 수 있으므로 `list_directory`로 상위 경로를 확인한 뒤
  패턴을 고쳐 재시도

## 분석 원칙
1. 디스크 이미지를 열어야 하는 도구가 있다면 반드시 먼저 이미지를 열고, 반환된 식별자를 이후 호출에 전달
2. 대용량 출력이 예상되는 도구는 결과 행 수를 제한 (limit, max_rows 등)
3. 레지스트리나 특정 값을 찾을 때는 전체 덤프 대신 키 경로를 지정하여 조회
4. 파라미터 값은 이전 단계 출력에서 추출하고, 찾을 수 없으면 null 반환
5. 증거 무결성을 최우선으로 유지
6. 각 발견 사항에 출처(파일 경로, 레지스트리 키, 이벤트 ID 등)를 명시
7. 타임스탬프는 시:분:초 단위까지 정확하게 기록
8. 최종 응답은 도구 출력 원문 나열이 아니라, 추출한 값과 그 의미를 정리한 분석 결과로 작성

## 추가 조사 판단
분석 완료 후 아래 조건에 해당하면 응답 마지막에 추가 조사를 제안하세요:
- 암호화되거나 접근 불가한 데이터 발견
- 의심스러운 항목이 발견되었으나 충분히 분석하지 못한 경우
- 관련 아티팩트를 추가로 조사해야 전체 그림이 완성되는 경우

추가 조사가 필요하면 분석 결과 뒤에 아래 형식을 **반드시** 추가하세요:

[FOLLOWUP_NEEDED]
이유: (한 줄 설명)
목적: (추가로 확인할 내용)
힌트: (구체적 경로, 키워드, 플러그인 등)

추가 조사가 불필요하면 위 마커를 출력하지 마세요."""


DFXML_FRAGMENT_PROMPT = """\
당신은 디지털 포렌식 데이터 변환 전문가입니다.
아래 분석 결과를 DFXML 프래그먼트로 변환하세요.

## 분석 정보
- 에이전트: {agent_name}
- 작업 목적: {task_purpose}

## 분석 결과
{analysis_output}

## 변환 지침
- <dfxml_fragment> 루트 요소로 감싸기
- 발견된 각 항목을 <fileobject>, <registry_object>, <event> 등 적절한 요소로 매핑
- 타임스탬프는 ISO 8601 형식 사용
- 발견된 증거가 없으면 빈 <dfxml_fragment/> 반환
- 유효한 XML만 반환 (설명 텍스트 없이)"""


def format_tool_docs(tools: dict[str, Any]) -> str:
    """MCP 도구 스펙에서 프롬프트용 도구 문서 동적 생성

    Args:
        tools: {tool_key: Tool} 딕셔너리 (mcp.list_tools() 반환값)

    Returns:
        도구 이름, 설명, 필수 파라미터를 포함한 문서 텍스트
    """
    if not tools:
        return "(사용 가능한 도구 없음)"

    lines = []
    for name, tool in sorted(tools.items()):
        short_name = name.split("__", 1)[-1] if "__" in name else name
        desc = (tool.description or "").split("\n")[0].strip()[:120]
        schema = tool.inputSchema if hasattr(tool, "inputSchema") else {}
        required = schema.get("required", [])
        props = schema.get("properties", {})

        param_parts = []
        for p in required:
            p_type = props.get(p, {}).get("type", "")
            param_parts.append(f"{p}: {p_type}" if p_type else p)
        param_str = f" ({', '.join(param_parts)})" if param_parts else ""

        lines.append(f"- `{short_name}`{param_str} — {desc}")

    return "\n".join(lines)


def build_dissect_prompt(
    purpose: str = "",
    available_plugins: str = "",
    tool_docs: str = "",
) -> str:
    """작업 목적과 도구 문서를 포함한 Sub-Agent 시스템 프롬프트 생성

    Args:
        purpose: 현재 작업의 목적
        available_plugins: 사전 조회된 플러그인 목록
        tool_docs: format_tool_docs()로 생성된 도구 문서
    """
    prompt = DISSECT_SYSTEM_PROMPT.format(
        tool_docs=tool_docs or "(도구 목록은 function calling 스펙을 참조하세요)"
    )

    parts = [prompt]
    if available_plugins:
        parts.append(
            f"## 사용 가능한 플러그인 (사전 조회 완료)\n{available_plugins}"
        )
    if purpose:
        parts.append(f"## 현재 작업 목적\n{purpose}")
    return "\n\n".join(parts)


def build_dfxml_fragment_prompt(
    agent_name: str,
    task_purpose: str,
    analysis_output: str,
) -> str:
    """단일 Sub-Agent 분석 결과를 DFXML 프래그먼트로 변환하는 프롬프트

    Args:
        agent_name: Sub-Agent 이름
        task_purpose: 작업 목적
        analysis_output: 분석 결과 텍스트
    """
    return DFXML_FRAGMENT_PROMPT.format(
        agent_name=agent_name,
        task_purpose=task_purpose,
        analysis_output=analysis_output[:3000],
    )
