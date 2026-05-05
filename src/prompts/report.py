"""Report Agent 프롬프트

사용처:
    - src/agents/report/nodes.py → summary_node, report_node, dfxml_node
"""

from __future__ import annotations


def _format_step_results(step_results: list[dict]) -> str:
    """분석 결과를 프롬프트용 텍스트로 포맷팅

    에러 결과와 성공 결과를 명시적으로 구분하여 출력

    Args:
        step_results: TaskResult 형태 딕셔너리 목록
    """
    parts = []
    for i, r in enumerate(step_results):
        step_label = r.get("step", r.get("task_id", i + 1))
        name = r.get("name", r.get("agent_name", ""))
        status = r.get("status", "unknown")
        output = r.get("output", "").strip()

        if status == "error" or not output:
            parts.append(f"[{step_label}단계 {name}] (실패 — 결과 없음)")
        else:
            parts.append(f"[{step_label}단계 {name}]\n{output}")

    return "\n\n".join(parts)


def build_summary_prompt(step_results: list[dict]) -> str:
    """분석 결과 요약용 시스템 프롬프트

    Args:
        step_results: 각 분석 단계 결과 [{step, name, tool, output}, ...] 또는
                      TaskResult 형태 [{task_id, agent_name, status, output, ...}, ...]
    """
    results_text = _format_step_results(step_results)
    return f"""당신은 디지털 포렌식 분석 전문가입니다.
아래 분석 결과를 바탕으로 핵심 발견사항을 간략하게 요약하세요.

## 분석 결과
{results_text}

## 요약 지침
- 실제로 발견된 사실만 기술 (에러나 실패 결과는 "확인 불가"로 간결하게 처리)
- 핵심 발견사항 위주로 3~5문장 이내로 요약
- 의심스러운 행위, 타임라인, 침해 범위를 중심으로 작성
- JSON 원시 데이터가 있으면 핵심만 추출하여 자연어로 서술
- 불확실한 사항은 '추정' 또는 '가능성'으로 명시"""


def build_report_prompt(
    case_description: str,
    strategy: str,
    step_results: list[dict],
) -> str:
    """포렌식 분석 보고서 생성용 시스템 프롬프트

    Args:
        case_description: 사용자가 입력한 사건 개요
        strategy: 확정된 분석 전략 텍스트
        step_results: 각 분석 단계 결과 목록
    """
    results_text = _format_step_results(step_results)
    return f"""당신은 디지털 포렌식 분석 전문가입니다.
아래 정보를 바탕으로 공식 포렌식 분석 보고서를 작성하세요.

## 사건 개요
{case_description}

## 분석 전략
{strategy}

## 분석 결과
{results_text}

## 보고서 작성 지침
- 실제로 발견된 증거와 사실만 기술
- "오류가 발생했습니다"를 반복하지 말고, 실패한 분석은 "한계 및 주의사항"에서 한 번만 언급
- JSON 원시 데이터가 있으면 핵심만 추출하여 자연어로 서술
- 발견사항이 적더라도 발견된 내용을 깊이 있게 분석
- 법적 증거 능력이 유지되도록 객관적 사실과 추론을 명확히 구분

## 보고서 형식
1. 사건 개요 요약
2. 분석 방법론
3. 핵심 발견사항
4. 타임라인 재구성 (가능한 경우)
5. 결론 및 권고사항
6. 한계 및 주의사항"""


def build_dfxml_prompt(step_results: list[dict]) -> str:
    """분석 결과를 DFXML 스키마로 변환하는 시스템 프롬프트

    Args:
        step_results: 각 분석 단계 결과 목록
    """
    results_text = _format_step_results(step_results)
    return f"""당신은 디지털 포렌식 데이터 변환 전문가입니다.
아래 분석 결과를 DFXML(Digital Forensics XML) 스키마로 변환하세요.

## 분석 결과
{results_text}

## DFXML 변환 지침
- DFXML 1.x 네임스페이스 사용: xmlns="http://www.forensicswiki.org/wiki/Category:Digital_Forensics_XML"
- 각 발견 항목을 <fileobject>, <volume>, <diskimage> 등 적절한 요소로 매핑
- 타임스탬프는 ISO 8601 형식 사용
- 파일 해시가 있으면 <hashdigest type="sha256"> 포함
- 출력은 유효한 XML만 반환 (설명 텍스트 없이)"""


def build_dfxml_merge_prompt(fragments: list[str]) -> str:
    """DFXML 프래그먼트들을 완전한 DFXML 문서로 병합하는 프롬프트

    Args:
        fragments: 각 Sub-Agent가 생성한 DFXML 프래그먼트 목록
    """
    fragments_text = "\n\n".join(
        f"--- Fragment {i + 1} ---\n{frag}"
        for i, frag in enumerate(fragments)
    )
    return f"""당신은 디지털 포렌식 데이터 변환 전문가입니다.
아래 DFXML 프래그먼트들을 하나의 완전한 DFXML 문서로 병합하세요.

## DFXML 프래그먼트 목록
{fragments_text}

## 병합 지침
- 최상위 <dfxml> 루트 요소로 감싸기
- DFXML 1.x 네임스페이스 사용: xmlns="http://www.forensicswiki.org/wiki/Category:Digital_Forensics_XML"
- 중복 항목 제거
- 타임스탬프 기준 정렬 (가능한 경우)
- 출력은 유효한 XML만 반환 (설명 텍스트 없이)"""
