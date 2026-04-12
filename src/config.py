"""설정 시스템: 환경변수 + JSON 기반 MCP/LLM 설정 로더"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TransportType(str, Enum):
    """MCP 서버 transport 유형"""

    STDIO = "stdio"
    SSE = "sse"


class StdioServerConfig(BaseModel):
    """stdio transport MCP 서버 설정"""

    transport: Literal["stdio"]
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None


class SSEServerConfig(BaseModel):
    """SSE transport MCP 서버 설정"""

    transport: Literal["sse"]
    url: str
    headers: dict[str, str] | None = None
    timeout: float = 5.0
    sse_read_timeout: float = 300.0


MCPServerConfig = Annotated[
    StdioServerConfig | SSEServerConfig,
    Field(discriminator="transport"),
]


class MCPConfig(BaseModel):
    """MCP 전체 설정"""

    servers: dict[str, StdioServerConfig | SSEServerConfig] = Field(
        default_factory=dict
    )
    tool_cache_ttl_seconds: int = 300


class LLMProvider(str, Enum):
    """지원하는 LLM 프로바이더"""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class LLMConfig(BaseModel):
    """LLM 프로바이더 설정"""

    provider: LLMProvider = LLMProvider.ANTHROPIC
    model: str = "claude-sonnet-4-20250514"
    temperature: float = 0.0
    max_tokens: int = 4096
    base_url: str | None = None


class Settings(BaseSettings):
    """애플리케이션 전체 설정

    환경변수 매핑:
        LLM_API_KEY  → llm_api_key
        LLM_MODEL    → llm.model (load_settings에서 처리)
        LLM_BASE_URL → llm.base_url (load_settings에서 처리)
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm: LLMConfig = Field(default_factory=LLMConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)

    llm_api_key: str = ""
    llm_model: str = ""
    llm_base_url: str = ""


def _load_mcp_from_json(path: Path) -> MCPConfig:
    """Claude Desktop 호환 JSON의 MCP 설정 로드

    Args:
        path: mcp_servers.json 파일 경로

    Returns:
        파싱된 MCPConfig 인스턴스

    JSON 형식::

        {
          "mcpServers": {
            "name": {
              "command": "...",
              "args": [...],
              "env": {...}
            }
          }
        }

    transport 키가 없으면 stdio로 간주
    """
    with open(path) as f:
        raw = json.load(f)

    servers_raw = raw.get("mcpServers", {})
    servers: dict[str, StdioServerConfig | SSEServerConfig] = {}

    for name, cfg in servers_raw.items():
        transport = cfg.get("transport", "stdio")
        if transport == "sse":
            servers[name] = SSEServerConfig(**cfg)
        else:
            servers[name] = StdioServerConfig(
                transport="stdio",
                command=cfg["command"],
                args=cfg.get("args", []),
                env=cfg.get("env"),
                cwd=cfg.get("cwd"),
            )

    return MCPConfig(servers=servers)


def load_settings(config_path: Path | None = None) -> Settings:
    """설정 로드

    .env에서 LLM_API_KEY, LLM_MODEL, LLM_BASE_URL을 읽고,
    config_path에서 MCP 서버 설정을 로드 (Claude Desktop 호환 JSON)

    LLM_MODEL / LLM_BASE_URL이 설정되면 LLMConfig에 반영하며,
    모델 이름이 gpt/o1/o3으로 시작하면 OpenAI 프로바이더로 자동 감지

    Args:
        config_path: MCP 서버 설정 JSON 파일 경로

    Returns:
        로드된 Settings 인스턴스
    """
    overrides: dict[str, Any] = {}

    if config_path and config_path.exists():
        overrides["mcp"] = _load_mcp_from_json(config_path)

    settings = Settings(**overrides)

    if settings.llm_model:
        settings.llm.model = settings.llm_model
    if settings.llm_base_url:
        settings.llm.base_url = settings.llm_base_url

    model = settings.llm.model.lower()
    if model.startswith(("gpt", "o1", "o3")):
        settings.llm.provider = LLMProvider.OPENAI

    return settings
