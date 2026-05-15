"""pgvector 기반 VectorStore 구현체

기존 PostgreSQL + SQLAlchemy async 인프라를 재사용하여
CaseEmbedding 테이블에 벡터를 저장하고 cosine distance로 검색.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select, delete, text
from sqlalchemy.ext.asyncio import AsyncEngine

from database.engine import get_session
from database.models import CaseEmbedding
from rag.base import BaseVectorStore
from rag.embedding import Embedder
from rag.types import RAGQuery, RAGResult

logger = structlog.get_logger()


class PgVectorStore(BaseVectorStore):
    """pgvector 기반 벡터 저장소

    Attributes:
        engine: SQLAlchemy 비동기 엔진
        embedder: 텍스트 → 벡터 변환기
    """

    def __init__(self, engine: AsyncEngine, embedder: Embedder) -> None:
        """pgvector 저장소 초기화

        Args:
            engine: SQLAlchemy 비동기 엔진 (기존 DB 엔진 재사용)
            embedder: BGE-M3 임베딩 생성기
        """
        self.engine = engine
        self.embedder = embedder

    async def ensure_extension(self) -> None:
        """pgvector 확장 활성화

        PostgreSQL에 vector 확장이 없으면 생성
        """
        async with self.engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

    async def search(self, query: RAGQuery) -> list[RAGResult]:
        """유사 문서 검색

        입력 텍스트를 임베딩한 후 cosine distance 기준으로
        가장 유사한 문서를 반환

        Args:
            query: 검색 요청 (쿼리 텍스트, top_k, 필터)

        Returns:
            유사도 순 정렬된 검색 결과 목록
        """
        query_vector = await self.embedder.embed(query.query)

        async with get_session(self.engine) as session:
            distance_expr = CaseEmbedding.embedding.cosine_distance(query_vector)

            stmt = (
                select(
                    CaseEmbedding.id,
                    CaseEmbedding.content,
                    CaseEmbedding.phase,
                    CaseEmbedding.case_id,
                    distance_expr.label("distance"),
                )
                .order_by(distance_expr)
                .limit(query.top_k)
            )

            if "phase" in query.filters:
                stmt = stmt.where(CaseEmbedding.phase == query.filters["phase"])
            if "case_id" in query.filters:
                stmt = stmt.where(
                    CaseEmbedding.case_id == int(query.filters["case_id"])
                )

            result = await session.execute(stmt)
            rows = result.all()

        results = []
        for row in rows:
            score = 1.0 - float(row.distance)
            results.append(
                RAGResult(
                    content=row.content,
                    score=score,
                    metadata={
                        "id": str(row.id),
                        "phase": row.phase,
                        "case_id": str(row.case_id),
                    },
                )
            )

        logger.info(
            "rag_search_complete",
            query_length=len(query.query),
            results=len(results),
            top_score=results[0].score if results else 0.0,
        )
        return results

    async def add_document(
        self,
        content: str,
        metadata: dict[str, str] | None = None,
    ) -> str:
        """문서 추가

        텍스트를 임베딩하여 CaseEmbedding 레코드로 저장

        Args:
            content: 저장할 문서 텍스트
            metadata: 문서 메타데이터 (case_id, phase 필수)

        Returns:
            생성된 레코드 ID (문자열)
        """
        meta = metadata or {}
        embedding = await self.embedder.embed(content)

        async with get_session(self.engine) as session:
            record = CaseEmbedding(
                case_id=int(meta.get("case_id", 0)),
                phase=meta.get("phase", "unknown"),
                content=content,
                embedding=embedding,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)

            logger.info(
                "rag_document_added",
                record_id=record.id,
                phase=record.phase,
                content_length=len(content),
            )
            return str(record.id)

    async def delete_document(self, document_id: str) -> bool:
        """문서 삭제

        Args:
            document_id: 삭제할 CaseEmbedding 레코드 ID

        Returns:
            삭제 성공 여부
        """
        async with get_session(self.engine) as session:
            stmt = delete(CaseEmbedding).where(
                CaseEmbedding.id == int(document_id)
            )
            result = await session.execute(stmt)
            await session.commit()
            deleted = result.rowcount > 0

            logger.info(
                "rag_document_deleted",
                document_id=document_id,
                success=deleted,
            )
            return deleted
