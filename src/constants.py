"""포렌식 멀티 에이전트 시스템 전역 상수

각 모듈에 산재된 튜닝 가능한 숫자 상수를 한 곳에서 관리
"""

DEFAULT_MAX_ITERATIONS: int = 10
"""Sub-Agent ReAct 루프 최대 반복 횟수"""

DEFAULT_MAX_ROWS: int = 200
"""대용량 플러그인 호출 시 기본 max_rows 제한"""

MAX_ROWS_PER_PLUGIN: int = 50
"""다중 플러그인 동시 실행 시 플러그인당 최대 행 수"""

SUMMARIZE_THRESHOLD: int = 5000
"""출력 요약을 적용하는 문자 수 임계치"""

CHUNK_SIZE: int = 8000
"""Map-Reduce 청크당 최대 문자 수"""

CHUNK_THRESHOLD: int = 20000
"""Map-Reduce를 적용하는 출력 길이 임계치"""

CALL_TOOL_TIMEOUT: int = 300
"""MCP 도구 호출 타임아웃 (초)"""

EXECUTION_STEP_DELAY: int = 60
"""Sub-Agent 실행 단계 간 대기 시간 (초)"""

LLM_MAX_RETRIES: int = 5
"""LLM API 호출 최대 재시도 횟수"""

LLM_INITIAL_BACKOFF: float = 2.0
"""LLM API 재시도 초기 백오프 (초)"""

MAX_FOLLOWUP_STEPS: int = 3
"""Sub-Agent가 동적으로 생성할 수 있는 최대 추가 조사 단계 수"""
