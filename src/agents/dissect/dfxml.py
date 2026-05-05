"""Sub-Agent 분석 결과의 DFXML 프래그먼트 생성 프롬프트"""

from __future__ import annotations

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
