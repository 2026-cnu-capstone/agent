"""시스템 프롬프트 빌더 (포렌식 도메인)"""

from __future__ import annotations

FORENSIC_SYSTEM_PROMPT = """\
You are a digital forensics analysis agent specializing in Windows disk image forensics (DFIR).

## Role
- Analyze Windows disk images (E01, dd, raw formats)
- Identify and investigate artifacts: registry hives, event logs (EVTX), prefetch, MFT, browser history, USB traces
- Construct timelines of attacker/user activity
- Follow forensic best practices: preserve evidence integrity, document chain of custody
"""
def build_strategy_prompt() -> str:
    """사건 정보를 바탕으로 조사할 아티팩트 목록만 간단히 도출

    세부 계획 없이, 어떤 아티팩트를 조사할지 항목 형태로만 출력.
    """
    return """당신은 디지털 포렌식 분석 전문가 AI입니다.

사용자가 제공한 사건 정보를 바탕으로, 이 사건에서 조사해야 할 아티팩트 목록만 작성하세요.
설명이나 분석 방법은 쓰지 말고, 아티팩트 이름과 조사 이유를 한 줄씩 나열하세요.

출력 형식 (이 형식만 사용):

## 조사 대상 아티팩트
- [아티팩트명]: [조사 이유 한 줄]
- [아티팩트명]: [조사 이유 한 줄]
...

예시:
- Registry (NTUSER.DAT): 사용자 최근 실행 파일 및 USB 연결 흔적 확인
- Event Log (Security.evtx): 로그인/로그오프 및 계정 변경 이력 확인
- Prefetch: 실행된 프로그램 목록 및 실행 시각 확인
- MFT: 파일 생성·삭제·수정 타임라인 재구성
- Browser History: 방문 URL 및 다운로드 파일 확인

사건과 관련된 아티팩트만 선별하고, 불필요한 항목은 포함하지 마세요."""


def build_planning_prompt(strategy: str, tool_summaries: str = "") -> str:
    """수립된 전략을 바탕으로 세부 실행 계획 수립 프롬프트

    Args:
        strategy: strategy_node가 수립한 분석 전략 텍스트
        tool_summaries: 사용 가능한 MCP 도구 요약 문자열
    """
    return f"""당신은 디지털 포렌식 분석 전문가 AI입니다.

아래 조사 대상 아티팩트 목록을 바탕으로 단계별 실행 계획을 작성하세요.
각 단계마다 어떤 MCP 도구를 사용할지 반드시 명시하세요.

[조사 대상 아티팩트]
{strategy}

[사용 가능한 MCP 도구]
{tool_summaries if tool_summaries else "(연결된 도구 없음 - 도구 없이 수동 분석 계획 작성)"}

---

아래 형식으로 작성하세요. 각 단계는 하나의 아티팩트 또는 연관 아티팩트 묶음에 대응합니다.

## 단계별 실행 계획

| 단계 | 분석 대상 | 목적 | 사용 MCP 도구 |
|------|----------|------|--------------|
| 1 | [아티팩트명] | [확인할 내용] | [도구명 또는 없음] |
| 2 | ... | ... | ... |

## 단계별 상세

### 1단계: [아티팩트명]
- **목적**: 무엇을 확인하는가
- **MCP 도구**: [도구명] — [이 도구를 쓰는 이유]
- **주요 확인 항목**: 구체적으로 볼 값/경로/키

### 2단계: ...

---

도구가 없는 단계는 MCP 도구란에 "(없음 - 수동)"으로 표기하세요.

마지막으로 아래 JSON을 반드시 코드 블록 안에 포함하세요:

```json
{{
  "steps": [
    {{
      "index": 1,
      "name": "단계명",
      "tool": "사용할_MCP_도구명 또는 none",
      "purpose": "목적 한 줄",
      "output_hint": {{
        "필드명": "이 단계 실행 후 다음 단계에서 사용할 값 설명"
      }},
      "input_hint": "이전 단계의 output_hint 중 어떤 필드를 이 도구의 어떤 파라미터로 넘길지"
    }}
  ]
}}
```"""


def build_step_mapper_prompt(
    step: dict,
    previous_steps: list[dict],
    previous_results: list[dict],
    tool_schema: str,
    disk_image_path: str = "",
    validation_error: str = "",
) -> str:
    """이전 단계 Raw 출력에서 다음 도구 파라미터를 추출하는 프롬프트

    Extraction → Reasoning → Validation 실패 시 재시도 흐름에서 사용.

    Args:
        step: 현재 단계 {index, name, tool, purpose, output_hint, input_hint}
        previous_steps: 이전 단계들의 계획 정보 (output_hint 포함)
        previous_results: 이전 단계들의 실제 실행 결과 (Raw 텍스트)
        tool_schema: 현재 도구의 inputSchema (JSON 문자열)
        disk_image_path: 분석 대상 디스크 이미지 경로
        validation_error: 직전 시도 검증 실패 메시지 (재시도 시 전달)
    """
    # 이전 단계 output_hint + 실제 출력을 함께 구성
    if previous_steps and previous_results:
        prev_section = "\n\n".join(
            f"[{r['step']}단계 - {r['name']}]\n"
            f"output_hint: {previous_steps[i].get('output_hint', {})}\n"
            f"실제 출력:\n{r['output']}"
            for i, r in enumerate(previous_results)
            if i < len(previous_steps)
        )
    elif previous_results:
        prev_section = "\n\n".join(
            f"[{r['step']}단계 - {r['name']}]\n실제 출력:\n{r['output']}"
            for r in previous_results
        )
    else:
        prev_section = "(첫 번째 단계, 이전 결과 없음)"

    current_output_hint = step.get("output_hint", {})
    input_hint = step.get("input_hint", "")

    image_section = f"\n## 분석 대상 디스크 이미지 경로\n{disk_image_path}\n" if disk_image_path else ""
    retry_section = f"\n## 이전 시도 검증 오류 (반드시 수정)\n{validation_error}\n" if validation_error else ""

    return f"""당신은 디지털 포렌식 분석 AI입니다.
아래 이전 단계의 Raw 출력에서 현재 도구 호출에 필요한 파라미터 값을 추출하세요.
{image_section}{retry_section}
## [Extraction] 이전 단계 Raw 출력
{prev_section}

## [Reasoning] 현재 도구 호출 정보
- 단계: {step['index']}단계 - {step['name']}
- 도구: {step['tool']}
- 목적: {step['purpose']}
- input_hint: {input_hint}

## [Validation 기준] 현재 도구 inputSchema
{tool_schema}

---

추출 규칙:
1. path / image_path 등 이미지 경로 파라미터 → 반드시 위의 디스크 이미지 경로 사용
2. 나머지 파라미터 → 이전 단계 Raw 출력과 input_hint를 참고하여 실제 값 추출
3. schema의 required 필드는 반드시 포함
4. 값을 찾을 수 없는 선택 파라미터는 생략

설명 없이 JSON 객체만 출력합니다.
예시: {{"path": "/image.dd", "log_path": "C:/Windows/System32/winevt/Logs/Security.evtx"}}"""


def build_summary_prompt(step_results: list[dict]) -> str:
    """에이전트 분석 결과 요약용 시스템 프롬프트

    step_results의 단계별 출력을 텍스트로 나열하고,
    LLM이 핵심 발견사항만 간략하게 요약하도록 지시.

    Args:
        step_results: 각 분석 회차 결과 목록 [{step, name, tool, output}, ...]
    """
    results_text = "\n\n".join(
        f"[{r.get('step', i + 1)}단계 {r.get('name', '')}]\n{r.get('output', '')}"
        for i, r in enumerate(step_results)
    )
    return f"""당신은 디지털 포렌식 분석 전문가입니다.
아래 분석 결과를 바탕으로 핵심 발견사항을 간략하게 요약하세요.

## 분석 결과
{results_text}

## 요약 지침
- 핵심 발견사항 위주로 3~5문장 이내로 요약
- 의심스러운 행위, 타임라인, 침해 범위를 중심으로 작성
- 불확실한 사항은 '추정' 또는 '가능성'으로 명시"""


def build_report_prompt(
    case_description: str,
    strategy: str,
    step_results: list[dict],
) -> str:
    """포렌식 분석 보고서 생성용 시스템 프롬프트

    사건 설명, 분석 전략, 전체 단계별 결과를 포함한 컨텍스트를 구성하고
    LLM이 정식 포렌식 보고서 형식으로 작성하도록 지시.

    Args:
        case_description: 사용자가 입력한 사건 개요 텍스트
        strategy: HITL에서 확정된 분석 전략 텍스트
        step_results: 각 분석 회차 결과 목록 [{step, name, tool, output}, ...]
    """
    results_text = "\n\n".join(
        f"[{r.get('step', i + 1)}단계 {r.get('name', '')}]\n{r.get('output', '')}"
        for i, r in enumerate(step_results)
    )
    return f"""당신은 디지털 포렌식 분석 전문가입니다.
아래 정보를 바탕으로 공식 포렌식 분석 보고서를 작성하세요.

## 사건 개요
{case_description}

## 분석 전략
{strategy}

## 분석 결과
{results_text}

## 보고서 형식
1. 사건 개요 요약
2. 분석 방법론
3. 핵심 발견사항
4. 타임라인 재구성 (가능한 경우)
5. 결론 및 권고사항
6. 한계 및 주의사항

법적 증거 능력이 유지되도록 객관적 사실과 추론을 명확히 구분하여 작성하세요."""


def build_system_prompt(tool_summaries: str, analysis_plan: str = "") -> str:
    """실행 단계 시스템 프롬프트

    Args:
        tool_summaries: 사용 가능한 MCP 도구 요약 문자열
        analysis_plan: 계획 단계에서 수립된 분석 계획 텍스트
    """
    plan_section = (
        f"\n\n## 수립된 분석 계획\n{analysis_plan}"
        if analysis_plan
        else ""
    )

    return f"""당신은 디지털 포렌식 분석 전문 AI입니다.
MCP 도구를 활용하여 분석 계획에 따라 체계적으로 분석을 수행하세요.{plan_section}

## 사용 가능한 도구
{tool_summaries if tool_summaries else "(사용 가능한 도구 없음)"}

## 분석 원칙
- 증거 무결성을 최우선으로 유지하세요
- 각 분석 단계의 결과를 명확히 기록하세요
- 발견된 증거는 법적 효력이 있도록 문서화하세요
- 분석 계획의 단계를 순서대로 실행하세요"""
