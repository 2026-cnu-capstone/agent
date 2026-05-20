"""포렌식 케이스 SQLAlchemy 모델"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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
        events: 케이스에 속한 분석 이벤트 목록
    """

    __tablename__ = "agent"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    disk_image_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    disk_image_format: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    events: Mapped[list[AnalysisEvent]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        """케이스 문자열 표현"""
        return f"<Case(id={self.id}, format={self.disk_image_format})>"


class AnalysisEvent(Base):
    """분석 진행 이벤트 로그

    WebSocket으로 전송되는 이벤트를 DB에 영속화하여
    사용자 재접속 시 진행 상황 복원에 사용

    Attributes:
        id: 이벤트 고유 식별자 (자동 증가)
        case_id: 소속 케이스 ID
        event_type: 이벤트 유형 (strategy_ready, step_started, step_completed 등)
        payload: 이벤트 데이터 (JSONB)
        created_at: 이벤트 발생 시각
        case: 소속 케이스 관계
    """

    __tablename__ = "analysis_event"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("agent.id"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    case: Mapped[Case] = relationship(back_populates="events")

    def __repr__(self) -> str:
        """이벤트 문자열 표현"""
        return f"<AnalysisEvent(id={self.id}, type={self.event_type})>"

    def to_dict(self) -> dict[str, Any]:
        """직렬화 가능한 딕셔너리 변환

        Returns:
            이벤트 데이터 딕셔너리
        """
        return {
            "id": self.id,
            "case_id": self.case_id,
            "event_type": self.event_type,
            "payload": self.payload,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
