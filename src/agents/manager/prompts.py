"""Manager Agent 전용 프롬프트

기존 prompts/system.py의 strategy, planning 프롬프트를 이전
"""

from __future__ import annotations


def build_strategy_prompt(disk_image_format: str = "", system_profile: str = "") -> str:
    """사건 정보를 바탕으로 조사할 아티팩트 목록 도출

    Args:
        disk_image_format: 디스크 이미지 형식 (e01, dd 등)
        system_profile: 시스템 프로필 정보 (OS, 호스트명 등)
    """
    context_parts = []
    if disk_image_format:
        context_parts.append(f"디스크 이미지 형식: {disk_image_format}")
    if system_profile:
        context_parts.append(f"시스템 정보:\n{system_profile}")
    context_block = "\n".join(context_parts)

    return f"""당신은 디지털 포렌식 분석 전문가 AI입니다.

사용자가 제공한 사건 정보를 바탕으로, 이 사건에서 조사해야 할 아티팩트 목록만 작성하세요.
설명이나 분석 방법은 쓰지 말고, 아티팩트 이름과 조사 이유를 한 줄씩 나열하세요.

{f"[디스크 이미지 정보]{chr(10)}{context_block}{chr(10)}" if context_block else ""}출력 형식 (이 형식만 사용):

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


def build_planning_prompt(strategy: str, mcp_servers: str = "") -> str:
    """수립된 전략을 바탕으로 세부 실행 계획 수립 프롬프트

    각 단계에서 어떤 MCP 서버(= Sub-Agent)를 사용할지만 지정.
    구체적인 MCP 도구 선택은 Sub-Agent가 실행 중 자율 결정.

    Args:
        strategy: strategy_node가 수립한 분석 전략 텍스트
        mcp_servers: 사용 가능한 MCP 서버 목록 문자열
    """
    return f"""당신은 디지털 포렌식 분석 전문가 AI입니다.

아래 조사 대상 아티팩트 목록을 바탕으로 단계별 실행 계획을 작성하세요.
각 단계마다 어떤 MCP 서버를 사용할지 지정하세요.
**구체적인 도구는 지정하지 마세요.** 각 단계를 담당하는 Sub-Agent가 실행 중 최적의 도구를 자율 선택합니다.

[조사 대상 아티팩트]
{strategy}

[사용 가능한 MCP 서버]
{mcp_servers if mcp_servers else "(연결된 서버 없음)"}

---

아래 형식으로 작성하세요. 각 단계는 하나의 아티팩트 또는 연관 아티팩트 묶음에 대응합니다.

## 단계별 실행 계획

| 단계 | 분석 대상 | 목적 | 담당 MCP 서버 |
|------|----------|------|--------------|
| 1 | [아티팩트명] | [확인할 내용] | [서버명 또는 없음] |
| 2 | ... | ... | ... |

## 단계별 상세

### 1단계: [아티팩트명]
- **목적**: 무엇을 확인하는가
- **MCP 서버**: [서버명] — [이 서버가 적합한 이유]
- **주요 확인 항목**: 구체적으로 볼 값/경로/키
- **Sub-Agent 지시사항**: Sub-Agent가 분석 시 참고할 구체적 힌트

### 2단계: ...

---

MCP 서버가 필요 없는 단계는 담당 MCP 서버란에 "none"으로 표기하세요.

마지막으로 아래 JSON을 반드시 코드 블록 안에 포함하세요.
**위 계획의 모든 단계를 steps 배열에 빠짐없이 포함해야 합니다. 일부만 넣으면 나머지 단계가 실행되지 않습니다.**

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
    }},
    {{
      "index": 2,
      "name": "단계명",
      "mcp_server": "담당 MCP 서버명 또는 none",
      "purpose": "목적",
      "artifacts": [],
      "hints": ""
    }},
    {{ "...모든 단계를 끝까지 포함..." }}
  ]
}}
```"""
