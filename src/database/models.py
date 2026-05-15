"""포렌식 케이스 SQLAlchemy 모델"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
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


class AgentRun(Base):
    """에이전트 실행 이력

    Attributes:
        id: 실행 고유 식별자 (자동 증가)
        case_id: 연관 케이스 ID (FK)
        agent_name: 실행된 에이전트 이름 (예: dissect, report)
        status: 실행 상태 (running, success, error)
        started_at: 실행 시작 시각
        finished_at: 실행 종료 시각
    """

    __tablename__ = "agent_run"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("agent.id"), nullable=False
    )
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running"
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    step_results: Mapped[list["StepResult"]] = relationship(
        back_populates="agent_run", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        """에이전트 실행 문자열 표현"""
        return f"<AgentRun(id={self.id}, agent={self.agent_name}, status={self.status})>"


class StepResult(Base):
    """단계별 실행 결과

    Attributes:
        id: 결과 고유 식별자 (자동 증가)
        agent_run_id: 연관 AgentRun ID (FK)
        step_index: 실행 단계 인덱스
        tool_name: 사용된 MCP 도구 이름
        output_summary: 요약된 출력 (summarizer 적용 후)
        raw_output: 원본 전체 출력
        created_at: 결과 저장 시각
    """

    __tablename__ = "step_result"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    agent_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("agent_run.id"), nullable=False
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False)
    output_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    raw_output: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    agent_run: Mapped["AgentRun"] = relationship(back_populates="step_results")

    def __repr__(self) -> str:
        """단계 결과 문자열 표현"""
        return f"<StepResult(id={self.id}, step={self.step_index}, tool={self.tool_name})>"


EMBEDDING_DIMENSION = 1024


class CaseEmbedding(Base):
    """케이스 분석 결과 임베딩 (pgvector)

    과거 분석 사례를 벡터로 저장하여 유사 케이스 RAG 검색에 사용

    Attributes:
        id: 임베딩 고유 식별자 (자동 증가)
        case_id: 연관 케이스 ID (FK)
        phase: 분석 단계 (strategy, planning, execution)
        content: 임베딩된 원본 텍스트
        embedding: pgvector 벡터 (1024차원, BGE-M3)
        created_at: 생성 시각
    """

    __tablename__ = "case_embedding"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    case_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("agent.id"), nullable=True
    )
    phase: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(Vector(EMBEDDING_DIMENSION), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        """케이스 임베딩 문자열 표현"""
        return f"<CaseEmbedding(id={self.id}, case_id={self.case_id}, phase={self.phase})>"
