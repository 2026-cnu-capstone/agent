"""RAG 쿼리 및 결과 데이터 타입"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RAGQuery:
    """벡터 DB 검색 요청

    Args:
        query: 검색 쿼리 텍스트
        top_k: 반환할 최대 결과 수
        filters: 메타데이터 필터 (예: {"case_type": "ransomware"})
    """

    query: str
    top_k: int = 5
    filters: dict[str, str] = field(default_factory=dict)


@dataclass
class RAGResult:
    """벡터 DB 검색 결과 항목

    Args:
        content: 검색된 문서 텍스트
        score: 유사도 점수 (0.0 ~ 1.0)
        metadata: 문서 메타데이터 (출처, 사건 유형 등)
    """

    content: str
    score: float
    metadata: dict[str, str] = field(default_factory=dict)
