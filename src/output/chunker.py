"""대용량 출력 청크 분할 및 Map-Reduce 요약

레지스트리 덤프 등 수십만 자 이상의 출력을 청크 단위로 분할하고
각 청크를 개별 요약한 뒤 최종 병합하는 파이프라인
"""

from __future__ import annotations

import structlog

from llm_provider.base import BaseLLMProvider

logger = structlog.get_logger()

CHUNK_SIZE = 8000
"""청크당 최대 문자 수"""

CHUNK_THRESHOLD = 20000
"""Map-Reduce를 적용하는 출력 길이 임계치"""

_MAP_PROMPT = """\
당신은 디지털 포렌식 분석 AI입니다.
아래는 도구 출력의 일부분(청크 {chunk_index}/{total_chunks})입니다.
이 청크에서 포렌식 분석에 유의미한 항목만 추출하세요.

## 도구: {tool_name}
## 목적: {purpose}

## 청크 내용
{chunk}

타임스탬프, 의심 경로, 주요 아티팩트 위주로 간결하게 추출하세요."""

_REDUCE_PROMPT = """\
당신은 디지털 포렌식 분석 AI입니다.
아래는 동일 도구 출력의 여러 청크에서 추출한 요약들입니다.
이를 하나의 통합 요약으로 병합하세요.

## 도구: {tool_name}
## 목적: {purpose}

## 청크별 요약
{summaries}

## 병합 지침
- 중복 항목 제거
- 시간순으로 정렬 (가능한 경우)
- 핵심 발견사항 위주로 최대 3000자 이내 작성"""


def split_into_chunks(text: str, chunk_size: int = CHUNK_SIZE) -> list[str]:
    """텍스트를 줄 단위로 청크 분할

    줄 단위 분할을 우선하여 문맥 단절을 최소화.
    단일 줄이 chunk_size를 초과하면 강제 분할.

    Args:
        text: 분할할 원본 텍스트
        chunk_size: 청크당 최대 문자 수

    Returns:
        청크 문자열 목록
    """
    lines = text.split("\n")
    chunks: list[str] = []
    current_chunk: list[str] = []
    current_length = 0

    for line in lines:
        line_length = len(line) + 1
        if current_length + line_length > chunk_size and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = []
            current_length = 0
        current_chunk.append(line)
        current_length += line_length

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks


async def chunk_and_summarize(
    output: str,
    tool_name: str,
    purpose: str,
    llm: BaseLLMProvider,
    chunk_size: int = CHUNK_SIZE,
    threshold: int = CHUNK_THRESHOLD,
) -> str:
    """대용량 출력을 Map-Reduce 방식으로 요약

    1. Map: 출력을 청크로 분할하여 각각 개별 요약
    2. Reduce: 청크별 요약을 하나의 통합 요약으로 병합

    threshold 미만이면 원본을 그대로 반환.

    Args:
        output: 도구 실행 원본 출력
        tool_name: 실행된 도구 이름
        purpose: 이 도구 호출의 목적
        llm: LLM 프로바이더
        chunk_size: 청크당 최대 문자 수
        threshold: Map-Reduce 적용 임계치

    Returns:
        병합 요약 텍스트
    """
    if len(output) <= threshold:
        return output

    chunks = split_into_chunks(output, chunk_size)
    total = len(chunks)
    logger.info(
        "chunk_and_summarize_start",
        tool=tool_name,
        total_chunks=total,
        original_length=len(output),
    )

    chunk_summaries: list[str] = []
    for i, chunk in enumerate(chunks):
        try:
            response = await llm.chat(
                messages=[{"role": "user", "content": "청크를 요약해주세요."}],
                tools=None,
                system=_MAP_PROMPT.format(
                    chunk_index=i + 1,
                    total_chunks=total,
                    tool_name=tool_name,
                    purpose=purpose,
                    chunk=chunk,
                ),
            )
            summary = response.content if isinstance(response.content, str) else ""
            chunk_summaries.append(f"[청크 {i + 1}/{total}]\n{summary}")
        except Exception as exc:
            logger.error("chunk_map_failed", chunk=i + 1, error=str(exc))
            chunk_summaries.append(f"[청크 {i + 1}/{total}] 요약 실패: {exc}")

    merged_summaries = "\n\n".join(chunk_summaries)

    try:
        response = await llm.chat(
            messages=[{"role": "user", "content": "청크 요약을 병합해주세요."}],
            tools=None,
            system=_REDUCE_PROMPT.format(
                tool_name=tool_name,
                purpose=purpose,
                summaries=merged_summaries,
            ),
        )
        result = response.content if isinstance(response.content, str) else merged_summaries
        logger.info("chunk_and_summarize_done", tool=tool_name, result_length=len(result))
        return result
    except Exception as exc:
        logger.error("chunk_reduce_failed", error=str(exc))
        return merged_summaries
