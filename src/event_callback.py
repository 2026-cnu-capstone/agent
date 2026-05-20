"""이벤트 저장소 기반 ExecutionCallback 구현"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from event_store import AnalysisEventStore
from state.messages import TaskResult


class PersistentExecutionCallback:
    """DB 영속화를 수행하는 ExecutionCallback 구현체

    Sub-Agent 실행 진행 상황을 AnalysisEventStore를 통해
    DB에 저장하고 리스너에 브로드캐스트

    Attributes:
        _event_store: 이벤트 저장소
        _case_id: 케이스 ID
        _step_start_times: 단계별 시작 시각 기록
    """

    def __init__(self, event_store: AnalysisEventStore, case_id: int) -> None:
        """PersistentExecutionCallback 초기화

        Args:
            event_store: 이벤트 저장소
            case_id: 케이스 ID
        """
        self._event_store = event_store
        self._case_id = case_id
        self._step_start_times: dict[int, float] = {}

    def on_step_start(
        self,
        step_index: int,
        total: int,
        step: dict[str, Any],
        agent_name: str,
    ) -> None:
        """단계 시작 이벤트 발행

        Args:
            step_index: 단계 인덱스
            total: 전체 단계 수
            step: 단계 정보
            agent_name: Sub-Agent 이름
        """
        self._step_start_times[step_index] = time.monotonic()
        asyncio.create_task(
            self._event_store.emit(
                self._case_id,
                "step_started",
                {
                    "step_index": step_index,
                    "total": total,
                    "step_name": step.get("name", ""),
                    "agent_name": agent_name,
                },
            )
        )

    def on_step_done(
        self,
        step_index: int,
        total: int,
        step: dict[str, Any],
        agent_name: str,
        result: TaskResult,
    ) -> None:
        """단계 완료 이벤트 발행

        Args:
            step_index: 단계 인덱스
            total: 전체 단계 수
            step: 단계 정보
            agent_name: Sub-Agent 이름
            result: 작업 결과
        """
        start = self._step_start_times.pop(step_index, None)
        elapsed = f"{time.monotonic() - start:.1f}s" if start else "N/A"

        asyncio.create_task(
            self._event_store.emit(
                self._case_id,
                "step_completed",
                {
                    "step_index": step_index,
                    "total": total,
                    "step_name": step.get("name", ""),
                    "agent_name": agent_name,
                    "status": result.get("status", ""),
                    "output_preview": result.get("output", "")[:300],
                    "elapsed": elapsed,
                },
            )
        )

    def on_step_skip(
        self,
        step_index: int,
        total: int,
        step: dict[str, Any],
    ) -> None:
        """단계 건너뛰기 이벤트 발행

        Args:
            step_index: 단계 인덱스
            total: 전체 단계 수
            step: 단계 정보
        """
        asyncio.create_task(
            self._event_store.emit(
                self._case_id,
                "step_completed",
                {
                    "step_index": step_index,
                    "total": total,
                    "step_name": step.get("name", ""),
                    "agent_name": "manual",
                    "status": "skip",
                    "output_preview": "",
                    "elapsed": "0s",
                },
            )
        )

    async def emit_phase_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """범용 단계 이벤트 발행

        strategy_ready, plan_ready, execution_done, report_ready 등
        ExecutionCallback 프로토콜 외의 이벤트 발행에 사용

        Args:
            event_type: 이벤트 유형
            payload: 이벤트 데이터
        """
        await self._event_store.emit(self._case_id, event_type, payload)
