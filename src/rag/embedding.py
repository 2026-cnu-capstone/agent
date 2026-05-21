"""BGE-M3 임베딩 유틸리티

SentenceTransformer 기반 텍스트 → 벡터 변환.
동기 라이브러리를 asyncio 환경에서 사용하기 위해
run_in_executor 패턴으로 async 래핑.
"""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Any

import structlog

logger = structlog.get_logger()


class Embedder:
    """텍스트 임베딩 생성기

    Attributes:
        model_name: HuggingFace 모델 ID
        dimension: 출력 벡터 차원 수
    """

    def __init__(self, model_name: str = "BAAI/bge-m3", preload: bool = True) -> None:
        """임베딩 모델 초기화

        Args:
            model_name: HuggingFace 모델 ID
            preload: True이면 생성 시점에 모델을 즉시 로드
        """
        self.model_name = model_name
        self._model: Any = None
        self.dimension: int = 1024
        if preload:
            self._load_model()

    def _load_model(self) -> Any:
        """SentenceTransformer 모델 지연 로드

        Returns:
            로드된 SentenceTransformer 모델 인스턴스
        """
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
            self.dimension = self._model.get_embedding_dimension()
            logger.info(
                "embedding_model_loaded",
                model=self.model_name,
                dimension=self.dimension,
            )
        return self._model

    def _encode_sync(self, texts: list[str]) -> list[list[float]]:
        """동기 배치 인코딩

        Args:
            texts: 임베딩할 텍스트 리스트

        Returns:
            벡터 리스트
        """
        model = self._load_model()
        embeddings = model.encode(texts, normalize_embeddings=True)
        return embeddings.tolist()

    async def embed(self, text: str) -> list[float]:
        """단일 텍스트 비동기 임베딩

        Args:
            text: 임베딩할 텍스트

        Returns:
            정규화된 벡터
        """
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None, partial(self._encode_sync, [text])
        )
        return results[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """배치 텍스트 비동기 임베딩

        Args:
            texts: 임베딩할 텍스트 리스트

        Returns:
            정규화된 벡터 리스트
        """
        if not texts:
            return []
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, partial(self._encode_sync, texts)
        )
