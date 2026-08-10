"""포렌식 도구 탐지 및 MCP 서버 추천 패키지

로컬에 설치된 포렌식 도구를 스캔하고, 해당 도구를 다루는 팀 자체 MCP 서버와
공개 MCP 레지스트리 서버를 추천한 뒤, mcp_servers.json 반영 제안을 생성
"""

from discovery.catalog import (
    CATALOG_INDEX,
    FORENSIC_TOOLS,
    MCP_CATALOG,
    TOOL_INDEX,
    candidates_for_tool,
)
from discovery.installer import MCPInstaller
from discovery.proposal import apply_proposals, build_proposals
from discovery.recommender import MCPRecommender, filter_by_strategy
from discovery.registry_client import MCPRegistryClient
from discovery.scanner import ToolScanner, current_platform
from discovery.service import DiscoveryService, default_search_roots
from discovery.types import (
    DetectedTool,
    DetectionEvidence,
    DetectionMethod,
    DiscoveryReport,
    InstallOutcome,
    InstallState,
    MCPRecommendation,
    MCPServerCandidate,
    Platform,
    RecommendationSource,
    ServerProposal,
    ToolCategory,
    ToolSignature,
    VerificationResult,
)
from discovery.verifier import verify_recommendations, verify_stdio_server

__all__ = [
    "CATALOG_INDEX",
    "FORENSIC_TOOLS",
    "MCP_CATALOG",
    "TOOL_INDEX",
    "DetectedTool",
    "DetectionEvidence",
    "DetectionMethod",
    "DiscoveryReport",
    "DiscoveryService",
    "InstallOutcome",
    "InstallState",
    "MCPRecommendation",
    "MCPInstaller",
    "MCPRecommender",
    "MCPRegistryClient",
    "MCPServerCandidate",
    "Platform",
    "RecommendationSource",
    "ServerProposal",
    "ToolCategory",
    "ToolScanner",
    "ToolSignature",
    "VerificationResult",
    "apply_proposals",
    "build_proposals",
    "candidates_for_tool",
    "current_platform",
    "default_search_roots",
    "filter_by_strategy",
    "verify_recommendations",
    "verify_stdio_server",
]
