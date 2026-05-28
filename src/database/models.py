"""포렌식 케이스 SQLAlchemy 모델 — init.sql 스키마 기준"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    disk_image_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    analyst: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    sessions: Mapped[list["AnalysisSession"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    events: Mapped[list["AnalysisEvent"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    embeddings: Mapped[list["CaseEmbedding"]] = relationship(back_populates="case")
    reports: Mapped[list["Report"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )


class AnalysisSession(Base):
    __tablename__ = "analysis_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    phase: Mapped[str] = mapped_column(String(20), nullable=False)
    system_profile: Mapped[str | None] = mapped_column(Text, nullable=True)
    strategy: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    case: Mapped["Case"] = relationship(back_populates="sessions")
    plan_steps: Mapped[list["PlanStep"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    agent_runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class PlanStep(Base):
    __tablename__ = "plan_steps"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("analysis_sessions.id", ondelete="CASCADE"), nullable=False
    )
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id"), nullable=False
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    mcp_server: Mapped[str | None] = mapped_column(String(64), nullable=True)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    hints: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifacts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_followup: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    session: Mapped["AnalysisSession"] = relationship(back_populates="plan_steps")
    agent_runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="plan_step", cascade="all, delete-orphan"
    )


class AgentRun(Base):
    __tablename__ = "agent_run"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("analysis_sessions.id", ondelete="CASCADE"), nullable=False
    )
    plan_step_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("plan_steps.id", ondelete="CASCADE"), nullable=False
    )
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    tool: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    plan_round: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    session: Mapped["AnalysisSession"] = relationship(back_populates="agent_runs")
    plan_step: Mapped["PlanStep"] = relationship(back_populates="agent_runs")
    task_results: Mapped[list["TaskResult"]] = relationship(
        back_populates="agent_run", cascade="all, delete-orphan"
    )


class TaskResult(Base):
    __tablename__ = "task_results"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    agent_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("agent_run.id", ondelete="CASCADE"), nullable=False
    )
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id"), nullable=False
    )
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_output_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    artifacts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    agent_run: Mapped["AgentRun"] = relationship(back_populates="task_results")
    dfxml_fragments: Mapped[list["DfxmlFragment"]] = relationship(
        back_populates="task_result", cascade="all, delete-orphan"
    )


class DfxmlFragment(Base):
    __tablename__ = "dfxml_fragments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_result_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("task_results.id", ondelete="CASCADE"), nullable=False
    )
    agent_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dfxml_fragment: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    task_result: Mapped["TaskResult"] = relationship(back_populates="dfxml_fragments")


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    dfxml: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    case: Mapped["Case"] = relationship(back_populates="reports")


EMBEDDING_DIMENSION = 1024


class CaseEmbedding(Base):
    __tablename__ = "case_embedding"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    case_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="SET NULL"), nullable=True
    )
    phase: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(Vector(EMBEDDING_DIMENSION), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    case: Mapped["Case | None"] = relationship(back_populates="embeddings")


class AnalysisEvent(Base):
    __tablename__ = "analysis_event"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    case: Mapped["Case"] = relationship(back_populates="events")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "case_id": self.case_id,
            "event_type": self.event_type,
            "payload": self.payload,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
