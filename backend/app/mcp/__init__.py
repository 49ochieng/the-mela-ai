"""
Mela AI - Inbound MCP server (Phase 6A).

Mela exposes its own capabilities over the same MCP-over-HTTP shape it
uses to call workers.  External apps (Meeting Assistant, internal
tools, future SaaS integrations) get a uniform protocol to call into
Mela.

The wire shape mirrors Task Radar's MCP server:

    POST /mcp/v1
    Headers: X-Api-Key: <client-key>
    Body:    {"tool": "<tool_name>", "arguments": {...}}
    Returns: tool-specific JSON

Plus discovery:

    GET /mcp/v1/tools  → list of OpenAI-compatible function definitions
"""

from app.mcp.server import router as mcp_router
from app.mcp.tools import MELA_TOOL_DEFS

__all__ = ["mcp_router", "MELA_TOOL_DEFS"]
