"""RAG 서비스 — 포렌식 도메인 RAG 로직 캡슐화

VectorStore 위에 포렌식 분석 특화 검색/저장/포맷팅 기능 제공.
Manager Agent의 전략/계획 수립 단계에서 사용.
"""

from __future__ import annotations

import structlog

from rag.base import BaseVectorStore
from rag.types import RAGQuery, RAGResult

logger = structlog.get_logger()


class RAGService:
    """포렌식 분석 RAG 서비스

    Attributes:
        store: 벡터 저장소 구현체
        top_k: 검색 시 반환할 최대 결과 수
        similarity_threshold: 유사도 필터 임계값
    """

    def __init__(
        self,
        store: BaseVectorStore,
        top_k: int = 3,
        similarity_threshold: float = 0.5,
    ) -> None:
        """RAG 서비스 초기화

        Args:
            store: 벡터 저장소 (PgVectorStore 등)
            top_k: 검색 결과 상위 K개
            similarity_threshold: 유사도 임계값 (이하 결과 제외)
        """
        self.store = store
        self.top_k = top_k
        self.similarity_threshold = similarity_threshold

    async def search_similar_cases(
        self, case_description: str, top_k: int | None = None
    ) -> list[RAGResult]:
        """유사 케이스의 분석 전략 검색 (strategy 단계용)

        Args:
            case_description: 사용자 사건 설명 텍스트
            top_k: 반환 결과 수 (None이면 기본값 사용)

        Returns:
            유사도 임계값을 넘는 결과 목록
        """
        results = await self.store.search(
            RAGQuery(
                query=case_description,
                top_k=top_k or self.top_k,
                filters={"phase": "strategy"},
            )
        )
        filtered = [r for r in results if r.score >= self.similarity_threshold]
        logger.info(
            "rag_similar_cases",
            total=len(results),
            above_threshold=len(filtered),
        )
        return filtered

    async def search_similar_plans(
        self, strategy_text: str, top_k: int | None = None
    ) -> list[RAGResult]:
        """유사 케이스의 실행 계획 검색 (planning 단계용)

        Args:
            strategy_text: 분석 전략 텍스트
            top_k: 반환 결과 수 (None이면 기본값 사용)

        Returns:
            유사도 임계값을 넘는 결과 목록
        """
        results = await self.store.search(
            RAGQuery(
                query=strategy_text,
                top_k=top_k or self.top_k,
                filters={"phase": "planning"},
            )
        )
        filtered = [r for r in results if r.score >= self.similarity_threshold]
        logger.info(
            "rag_similar_plans",
            total=len(results),
            above_threshold=len(filtered),
        )
        return filtered

    async def store_case_result(
        self,
        case_id: int,
        strategy: str,
        plan: str,
        results_summary: str,
        case_description: str = "",
    ) -> None:
        """분석 완료 후 케이스 결과를 벡터 저장소에 저장

        전략, 계획, 실행 결과 요약을 각각 별도 문서로 저장하여
        향후 유사 케이스 검색에 활용.
        전략은 사건 설명과 함께 저장하여 검색 정확도 향상.

        Args:
            case_id: 케이스 ID
            strategy: 분석 전략 텍스트
            plan: 실행 계획 텍스트
            results_summary: 실행 결과 요약 텍스트
            case_description: 사용자 사건 설명 원문
        """
        case_id_str = str(case_id)

        if strategy:
            content = strategy
            if case_description:
                content = f"[사건 설명]\n{case_description}\n\n[분석 전략]\n{strategy}"
            await self.store.add_document(
                content=content,
                metadata={"case_id": case_id_str, "phase": "strategy"},
            )
        if plan:
            await self.store.add_document(
                content=plan,
                metadata={"case_id": case_id_str, "phase": "planning"},
            )
        if results_summary:
            await self.store.add_document(
                content=results_summary,
                metadata={"case_id": case_id_str, "phase": "execution"},
            )

        logger.info("rag_case_stored", case_id=case_id)

    @staticmethod
    def format_rag_context(results: list[RAGResult]) -> str:
        """RAG 검색 결과를 프롬프트 삽입용 텍스트로 포맷팅

        Args:
            results: RAG 검색 결과 리스트

        Returns:
            프롬프트에 삽입할 포맷팅된 텍스트 (결과 없으면 빈 문자열)
        """
        if not results:
            return ""

        lines = []
        for i, r in enumerate(results, 1):
            case_id = r.metadata.get("case_id", "?")
            score = f"{r.score:.2f}"
            lines.append(f"### 유사 사례 {i} (케이스 #{case_id}, 유사도: {score})")
            lines.append(r.content)
            lines.append("")

        return "\n".join(lines)
