"""LLM 기반 도구 출력 요약

도구 실행 결과가 임계치를 초과할 경우 LLM으로 핵심 내용만 추출하여
다음 단계 프롬프트의 토큰 소비를 방지
"""

from __future__ import annotations

import structlog

from llm_provider.base import BaseLLMProvider

logger = structlog.get_logger()

SUMMARIZE_THRESHOLD = 5000
"""요약을 적용하는 출력 길이 임계치 (문자 수)"""

_SUMMARIZE_PROMPT = """\
당신은 디지털 포렌식 분석 AI입니다.
아래 도구 실행 결과에서 포렌식 분석에 유의미한 핵심 정보만 추출하세요.

## 도구 정보
- 도구: {tool_name}
- 목적: {purpose}

## 원본 출력
{output}

## 추출 지침
- 의심스러운 파일 경로, 레지스트리 키, 이벤트 로그 항목을 우선 추출
- 타임스탬프가 포함된 항목은 반드시 보존
- 정상/무관한 항목은 생략
- 추출 결과만 간결하게 출력 (설명 불필요)
- 최대 2000자 이내로 작성"""


async def summarize_output(
    output: str,
    tool_name: str,
    purpose: str,
    llm: BaseLLMProvider,
    threshold: int = SUMMARIZE_THRESHOLD,
) -> str:
    """도구 출력이 임계치를 초과하면 LLM으로 요약, 미만이면 원본 반환

    Args:
        output: 도구 실행 원본 출력
        tool_name: 실행된 도구 이름
        purpose: 이 도구 호출의 목적
        llm: LLM 프로바이더
        threshold: 요약 적용 임계치 (문자 수)

    Returns:
        요약된 출력 또는 원본 (threshold 미만 시)
    """
    if len(output) <= threshold:
        return output

    logger.info(
        "summarizing_output",
        tool=tool_name,
        original_length=len(output),
        threshold=threshold,
    )

    try:
        response = await llm.chat(
            messages=[{"role": "user", "content": "도구 출력을 요약해주세요."}],
            tools=None,
            system=_SUMMARIZE_PROMPT.format(
                tool_name=tool_name,
                purpose=purpose,
                output=output,
            ),
        )
        summary = response.content if isinstance(response.content, str) else output[:threshold]
        logger.info(
            "output_summarized",
            tool=tool_name,
            summary_length=len(summary),
        )
        return summary
    except Exception as exc:
        logger.error("summarize_failed", tool=tool_name, error=str(exc))
        return output[:threshold] + f"\n...(요약 실패, 총 {len(output)}자)"
