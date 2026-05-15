"""포렌식 케이스 SQLAlchemy 모델"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """SQLAlchemy 선언적 베이스 클래스"""


class Case(Base):
    """포렌식 분석 케이스

    사용자가 입력한 프롬프트 원문과 디스크 이미지 경로를 저장

    Attributes:
        id: 케이스 고유 식별자 (자동 증가)
        user_prompt: 사용자 입력 프롬프트 원문
        disk_image_path: 디스크 이미지 파일 경로
        disk_image_format: 검증된 이미지 형식 (e01, dd, raw)
        created_at: 케이스 생성 시각
    """

    __tablename__ = "agent"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    disk_image_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    disk_image_format: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        """케이스 문자열 표현"""
        return f"<Case(id={self.id}, format={self.disk_image_format})>"
