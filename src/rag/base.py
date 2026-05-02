"""VectorStore 추상 인터페이스

구체적인 VectorDB 구현(ChromaDB, Pinecone 등)은 이 인터페이스를
상속하여 구현. 현재는 인터페이스 정의만 제공.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from rag.types import RAGQuery, RAGResult


class BaseVectorStore(ABC):
    """벡터 DB 추상 인터페이스

    과거 포렌식 분석 사례를 저장하고 유사 사례를 검색하는
    RAG 파이프라인의 저장소 계층
    """

    @abstractmethod
    async def search(self, query: RAGQuery) -> list[RAGResult]:
        """유사 문서 검색

        Args:
            query: 검색 요청 (쿼리 텍스트, top_k, 필터)

        Returns:
            유사도 순 정렬된 검색 결과 목록
        """

    @abstractmethod
    async def add_document(
        self,
        content: str,
        metadata: dict[str, str] | None = None,
    ) -> str:
        """문서 추가

        Args:
            content: 저장할 문서 텍스트
            metadata: 문서 메타데이터

        Returns:
            생성된 문서 ID
        """

    @abstractmethod
    async def delete_document(self, document_id: str) -> bool:
        """문서 삭제

        Args:
            document_id: 삭제할 문서 ID

        Returns:
            삭제 성공 여부
        """
