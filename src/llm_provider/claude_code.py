"""Claude Code 헤드리스 CLI 기반 LLM 프로바이더

API 키로 Anthropic/OpenAI를 직접 호출하는 대신 로컬에 설치된 `claude` CLI를
`--print` 모드로 실행해 동일한 `BaseLLMProvider` 계약을 제공. Claude Code
구독 인증을 그대로 쓰므로 별도 API 키가 필요 없음

두 가지 어댑팅이 필요:

1. **도구 호출** — 헤드리스 CLI는 자체 도구 루프를 돌고 우리 MCP 도구의
   tool_use 블록을 돌려주지 않음. 그래서 도구 명세를 시스템 프롬프트에 넣고
   JSON 한 덩어리로 응답하게 한 뒤 `ToolCall`로 파싱
2. **환경 격리** — `--tools ""`만으로는 사용자 전역 MCP 서버(Obsidian 등)가
   그대로 주입되어 포렌식 분석 중 무관한 도구가 호출될 수 있음.
   `--mcp-config '{"mcpServers":{}}' --strict-mcp-config`로 완전 차단

`is_anthropic`을 True로 보고해 Anthropic 포맷 메시지/도구 명세를 그대로 받고,
CLI 전달용 텍스트 변환은 이 프로바이더 내부에서만 수행
"""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from typing import Any

import structlog

from config import ClaudeCodeConfig, LLMConfig
from constants import LLM_INITIAL_BACKOFF, LLM_MAX_RETRIES
from llm_provider.base import BaseLLMProvider, LLMResponse, ToolCall, ToolResult

logger = structlog.get_logger()

MAX_SYSTEM_PROMPT_ARGV = 200_000
"""시스템 프롬프트를 argv로 넘기는 상한 (초과 시 stdin 본문에 포함)"""

TOOL_PROTOCOL_TEMPLATE = """
## 도구 호출 규약

너는 도구를 직접 실행할 수 없다. 도구를 호출하려면 **JSON 객체 하나만**
출력한다. 설명 문장이나 마크다운 코드펜스를 덧붙이지 않는다.

도구를 호출할 때:
{{"action": "call", "calls": [{{"name": "<도구명>", "arguments": {{...}}}}]}}

도구가 필요 없고 최종 답변을 할 때:
{{"action": "answer", "text": "<답변 전문>"}}

`arguments`는 각 도구의 입력 스키마를 정확히 따른다.
여러 도구를 동시에 호출해야 하면 `calls` 배열에 나열한다.

## 사용 가능한 도구

{tool_specs}
"""


class ClaudeCodeError(Exception):
    """Claude Code CLI 실행 실패"""


class ClaudeCodeProvider(BaseLLMProvider):
    """`claude -p` 헤드리스 실행을 사용하는 LLM 프로바이더

    Attributes:
        cli_path: 확인된 claude 실행 파일 경로
    """

    def __init__(
        self,
        config: LLMConfig,
        cli_config: ClaudeCodeConfig | None = None,
    ) -> None:
        """프로바이더 초기화

        Args:
            config: 모델명 등 LLM 설정
            cli_config: CLI 실행 설정 (None이면 기본값)

        Raises:
            ClaudeCodeError: claude CLI를 찾을 수 없을 때
        """
        self._config = config
        self._cli = cli_config or ClaudeCodeConfig()
        resolved = shutil.which(self._cli.cli_path)
        if resolved is None:
            raise ClaudeCodeError(
                f"claude CLI를 찾을 수 없음: {self._cli.cli_path}. "
                "Claude Code 설치 후 PATH에 포함되어야 함"
            )
        self.cli_path = resolved

    @property
    def is_anthropic(self) -> bool:
        """Anthropic 포맷 메시지/도구 명세를 사용하므로 True"""
        return True

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> LLMResponse:
        """헤드리스 CLI 호출 및 응답 파싱

        Args:
            messages: Anthropic 포맷 메시지 목록
            tools: Anthropic 포맷 도구 명세 목록
            system: 시스템 프롬프트

        Returns:
            텍스트와 도구 호출이 채워진 응답

        Raises:
            ClaudeCodeError: 재시도 후에도 CLI 실행이 실패할 때
        """
        system_prompt = _compose_system_prompt(system, tools)
        prompt = _flatten_messages(messages)

        if len(system_prompt) > MAX_SYSTEM_PROMPT_ARGV:
            prompt = f"# 지시사항\n\n{system_prompt}\n\n# 대화\n\n{prompt}"
            system_prompt = ""

        payload = await self._run_cli(prompt, system_prompt)
        return _parse_cli_payload(payload, expects_tools=bool(tools))

    async def _run_cli(self, prompt: str, system_prompt: str) -> dict[str, Any]:
        """claude CLI 실행 및 JSON 응답 반환

        일시적 실패(비정상 종료, 타임아웃)는 지수 백오프로 재시도

        Args:
            prompt: stdin으로 전달할 대화 본문
            system_prompt: `--system-prompt`로 전달할 시스템 프롬프트

        Returns:
            파싱된 CLI JSON 응답

        Raises:
            ClaudeCodeError: 모든 재시도가 실패할 때
        """
        args = self._build_args(system_prompt)
        last_error = ""

        for attempt in range(LLM_MAX_RETRIES):
            try:
                return await self._invoke_once(args, prompt)
            except ClaudeCodeError as exc:
                last_error = str(exc)
                if attempt == LLM_MAX_RETRIES - 1:
                    raise
                wait = LLM_INITIAL_BACKOFF * (2**attempt)
                logger.warning(
                    "claude_code_retry",
                    attempt=attempt + 1,
                    wait_seconds=wait,
                    error=last_error,
                )
                await asyncio.sleep(wait)

        raise ClaudeCodeError(last_error)

    async def _invoke_once(self, args: list[str], prompt: str) -> dict[str, Any]:
        """CLI 서브프로세스 단일 실행

        Args:
            args: claude 실행 인자 목록
            prompt: stdin 본문

        Returns:
            파싱된 CLI JSON 응답

        Raises:
            ClaudeCodeError: 타임아웃, 비정상 종료, JSON 파싱 실패 시
        """
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(prompt.encode()),
                timeout=self._cli.timeout,
            )
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            raise ClaudeCodeError(
                f"claude CLI 응답 타임아웃 ({self._cli.timeout}초)"
            ) from exc

        if process.returncode != 0:
            raise ClaudeCodeError(
                f"claude CLI 종료 코드 {process.returncode}: "
                f"{stderr.decode(errors='replace')[:500]}"
            )

        try:
            payload = json.loads(stdout.decode(errors="replace"))
        except json.JSONDecodeError as exc:
            raise ClaudeCodeError(
                f"claude CLI JSON 파싱 실패: {stdout[:300]!r}"
            ) from exc

        if not isinstance(payload, dict):
            raise ClaudeCodeError(f"예상치 못한 CLI 응답 형식: {type(payload)}")

        if payload.get("is_error"):
            raise ClaudeCodeError(
                f"claude CLI 오류: {payload.get('result') or payload.get('subtype')}"
            )

        return payload

    def _build_args(self, system_prompt: str) -> list[str]:
        """claude 실행 인자 구성

        Args:
            system_prompt: 시스템 프롬프트 (빈 문자열이면 생략)

        Returns:
            서브프로세스 실행 인자 목록
        """
        args = [
            self.cli_path,
            "--print",
            "--output-format",
            "json",
            "--model",
            self._config.model,
            "--max-turns",
            str(self._cli.max_turns),
        ]
        if system_prompt:
            args += ["--system-prompt", system_prompt]
        if self._cli.isolate_tools:
            args += [
                "--tools",
                "",
                "--mcp-config",
                json.dumps({"mcpServers": {}}),
                "--strict-mcp-config",
            ]
        args += list(self._cli.extra_args)
        return args

    def format_tool_result(self, result: ToolResult) -> dict[str, Any]:
        """Anthropic tool_result 콘텐츠 블록 변환"""
        return {
            "type": "tool_result",
            "tool_use_id": result.tool_call_id,
            "content": result.content,
            **({"is_error": True} if result.is_error else {}),
        }

    def build_tool_results_message(self, results: list[ToolResult]) -> dict[str, Any]:
        """여러 ToolResult의 단일 user 메시지 병합"""
        return {
            "role": "user",
            "content": [self.format_tool_result(r) for r in results],
        }


def _compose_system_prompt(
    system: str | None, tools: list[dict[str, Any]] | None
) -> str:
    """시스템 프롬프트에 도구 호출 규약 결합

    Args:
        system: 원본 시스템 프롬프트
        tools: Anthropic 포맷 도구 명세 목록

    Returns:
        결합된 시스템 프롬프트
    """
    base = system or ""
    if not tools:
        return base

    specs = "\n".join(_format_tool_spec(tool) for tool in tools)
    protocol = TOOL_PROTOCOL_TEMPLATE.format(tool_specs=specs)
    return f"{base}\n{protocol}" if base else protocol


def _format_tool_spec(tool: dict[str, Any]) -> str:
    """도구 명세 한 건을 프롬프트용 텍스트로 변환

    Args:
        tool: Anthropic 포맷 도구 명세

    Returns:
        이름, 설명, 입력 스키마가 담긴 텍스트
    """
    name = tool.get("name", "unknown")
    description = tool.get("description") or ""
    schema = tool.get("input_schema") or {}
    schema_text = json.dumps(schema, ensure_ascii=False)
    return f"### {name}\n{description}\n입력 스키마: {schema_text}"


def _flatten_messages(messages: list[dict[str, Any]]) -> str:
    """Anthropic 포맷 메시지 목록을 단일 텍스트 대화록으로 변환

    헤드리스 CLI는 프롬프트 문자열 하나만 받으므로 역할별 구분자를 넣어
    대화 흐름과 도구 호출/결과를 텍스트로 재현

    Args:
        messages: Anthropic 포맷 메시지 목록

    Returns:
        평탄화된 대화록 텍스트
    """
    tool_names: dict[str, str] = {}
    lines: list[str] = []

    for message in messages:
        role = str(message.get("role", "user"))
        content = message.get("content")
        rendered = _render_content(content, tool_names)
        if rendered:
            lines.append(f"[{role}]\n{rendered}")

    return "\n\n".join(lines)


def _render_content(content: Any, tool_names: dict[str, str]) -> str:
    """메시지 content를 텍스트로 변환

    Args:
        content: 문자열 또는 Anthropic 콘텐츠 블록 목록
        tool_names: tool_use_id → 도구명 누적 매핑 (제자리 갱신)

    Returns:
        변환된 텍스트
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return "" if content is None else str(content)

    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            parts.append(str(block))
            continue

        block_type = block.get("type")
        if block_type == "text":
            parts.append(str(block.get("text", "")))
        elif block_type == "tool_use":
            name = str(block.get("name", "unknown"))
            block_id = str(block.get("id", ""))
            if block_id:
                tool_names[block_id] = name
            arguments = json.dumps(block.get("input") or {}, ensure_ascii=False)
            parts.append(f"→ 도구 호출: {name}({arguments})")
        elif block_type == "tool_result":
            call_id = str(block.get("tool_use_id", ""))
            name = tool_names.get(call_id, call_id or "unknown")
            status = " (오류)" if block.get("is_error") else ""
            body = _render_result_content(block.get("content"))
            parts.append(f"← 도구 결과{status} [{name}]:\n{body}")
        else:
            parts.append(json.dumps(block, ensure_ascii=False))

    return "\n".join(part for part in parts if part)


def _render_result_content(content: Any) -> str:
    """tool_result의 content 필드를 텍스트로 변환

    Args:
        content: 문자열 또는 콘텐츠 블록 목록

    Returns:
        변환된 텍스트
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                chunks.append(str(item.get("text", "")))
            else:
                chunks.append(json.dumps(item, ensure_ascii=False))
        return "\n".join(chunks)
    return "" if content is None else str(content)


def _parse_cli_payload(
    payload: dict[str, Any], *, expects_tools: bool
) -> LLMResponse:
    """CLI JSON 응답을 LLMResponse로 변환

    Args:
        payload: CLI가 반환한 JSON 객체
        expects_tools: 도구 호출 규약 응답을 기대하는지 여부

    Returns:
        변환된 LLM 응답
    """
    text = payload.get("result")
    text = text if isinstance(text, str) else ""
    usage = _parse_usage(payload.get("usage"))

    if not expects_tools:
        return LLMResponse(
            content=text,
            stop_reason=str(payload.get("stop_reason") or "end_turn"),
            usage=usage,
        )

    stop_reason = str(payload.get("stop_reason") or "end_turn")
    protocol = _extract_json_object(text)

    tool_calls = _extract_tool_calls(protocol) if protocol else []
    answer = ""
    if protocol:
        for key in ("text", "content", "answer", "message"):
            value = protocol.get(key)
            if isinstance(value, str) and value.strip():
                answer = value
                break

    if tool_calls:
        return LLMResponse(
            content=answer,
            tool_calls=tool_calls,
            stop_reason="tool_use",
            usage=usage,
        )

    if answer:
        return LLMResponse(content=answer, stop_reason=stop_reason, usage=usage)

    logger.warning(
        "claude_code_protocol_unmatched",
        had_json=protocol is not None,
        keys=sorted(protocol)[:8] if protocol else [],
        preview=text[:400],
    )
    return LLMResponse(content=text, stop_reason=stop_reason, usage=usage)


CALL_LIST_KEYS = ("calls", "tool_calls", "tools", "actions")
CALL_NAME_KEYS = ("name", "tool", "tool_name", "function")
CALL_ARG_KEYS = ("arguments", "args", "input", "parameters", "params")


def _extract_tool_calls(protocol: dict[str, Any]) -> list[ToolCall]:
    """규약 객체에서 도구 호출 목록 추출

    모델이 키 이름을 바꿔 쓰거나(`tool`, `args`) 배열 없이 단일 호출을 그대로
    출력하는 경우가 있어 여러 변형을 허용. 형식이 조금 달라도 호출을 살려내지
    못하면 단계 전체가 산출물 없이 끝나므로 관용적으로 파싱

    Args:
        protocol: 응답에서 추출한 JSON 객체

    Returns:
        파싱된 도구 호출 목록 (없으면 빈 리스트)
    """
    raw_calls: list[Any] = []
    for key in CALL_LIST_KEYS:
        value = protocol.get(key)
        if isinstance(value, list):
            raw_calls = value
            break
        if isinstance(value, dict):
            raw_calls = [value]
            break

    if not raw_calls and any(k in protocol for k in CALL_NAME_KEYS):
        raw_calls = [protocol]

    tool_calls: list[ToolCall] = []
    for index, call in enumerate(raw_calls):
        if not isinstance(call, dict):
            continue

        name = None
        for key in CALL_NAME_KEYS:
            value = call.get(key)
            if isinstance(value, str) and value.strip():
                name = value.strip()
                break
            if isinstance(value, dict):
                nested = value.get("name")
                if isinstance(nested, str) and nested.strip():
                    name = nested.strip()
                    break
        if not name:
            continue

        arguments: dict[str, Any] = {}
        for key in CALL_ARG_KEYS:
            value = call.get(key)
            if isinstance(value, dict):
                arguments = value
                break
            if isinstance(value, str) and value.strip():
                try:
                    parsed = json.loads(value)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    arguments = parsed
                    break

        tool_calls.append(
            ToolCall(
                id=f"cc_{index}_{uuid.uuid4().hex[:8]}",
                name=name,
                arguments=arguments,
            )
        )

    return tool_calls


def _parse_usage(raw: Any) -> dict[str, int]:
    """CLI usage 객체를 토큰 사용량 dict로 변환

    Args:
        raw: CLI 응답의 usage 필드

    Returns:
        input_tokens / output_tokens 키를 가진 dict
    """
    if not isinstance(raw, dict):
        return {}
    result: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens"):
        value = raw.get(key)
        if isinstance(value, int):
            result[key] = value
    cache_read = raw.get("cache_read_input_tokens")
    cache_create = raw.get("cache_creation_input_tokens")
    if isinstance(cache_read, int):
        result["cache_read_input_tokens"] = cache_read
    if isinstance(cache_create, int):
        result["cache_creation_input_tokens"] = cache_create
    return result


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """텍스트에서 최외곽 JSON 객체 추출

    모델이 지시를 어기고 마크다운 코드펜스나 설명을 덧붙이는 경우가 있어
    첫 `{`부터 짝이 맞는 `}`까지를 잘라 파싱

    Args:
        text: 모델 응답 텍스트

    Returns:
        파싱된 dict, 실패 시 None
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        newline = stripped.find("\n")
        if newline != -1:
            stripped = stripped[newline + 1 :]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
        stripped = stripped.strip()

    start = stripped.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(stripped)):
        char = stripped[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = stripped[start : index + 1]
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None
