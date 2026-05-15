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


def build_planning_prompt(
    strategy: str,
    mcp_servers: str = "",
    rag_context: str = "",
) -> str:
    """수립된 전략을 바탕으로 세부 실행 계획 수립 프롬프트

    각 단계에서 어떤 MCP 서버(= Sub-Agent)를 사용할지만 지정.
    구체적인 MCP 도구 선택은 Sub-Agent가 실행 중 자율 결정.

    Args:
        strategy: strategy_node가 수립한 분석 전략 텍스트
        mcp_servers: 사용 가능한 MCP 서버 목록 문자열
        rag_context: RAG로 검색된 유사 사례 실행 계획 텍스트
    """
    rag_section = ""
    if rag_context:
        rag_section = f"""
[유사 사건의 실행 계획 참고]
{rag_context}
"""

    return f"""당신은 디지털 포렌식 분석 전문가 AI입니다.

아래 조사 대상 아티팩트 목록을 바탕으로 단계별 실행 계획을 작성하세요.
각 단계마다 어떤 MCP 서버를 사용할지 지정하세요.
**구체적인 도구는 지정하지 마세요.** 각 단계를 담당하는 Sub-Agent가 실행 중 최적의 도구를 자율 선택합니다.

[조사 대상 아티팩트]
{strategy}

[사용 가능한 MCP 서버]
{mcp_servers if mcp_servers else "(연결된 서버 없음)"}
{rag_section}
---

**가장 먼저** 아래 JSON을 코드 블록으로 출력하세요. (상세 설명보다 반드시 앞에 위치)
위 계획의 모든 단계를 steps 배열에 빠짐없이 포함해야 합니다.
MCP 서버가 필요 없는 단계는 mcp_server에 "none"을 넣으세요.

```json
{{
  "steps": [
    {{
      "index": 1,
      "name": "단계명",
      "mcp_server": "담당 MCP 서버명 또는 none",
      "purpose": "이 단계에서 확인할 내용 (Sub-Agent에게 전달됨)",
      "artifacts": ["조사 대상 아티팩트 목록"],
      "hints": "Sub-Agent가 참고할 구체적 힌트 (경로, 키, 키워드 등)"
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
