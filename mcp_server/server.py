"""Registers analyze_infrastructure and execute_teardown as MCP tools."""
from mcp_server.analyzer import analyze_infrastructure
from mcp_server.teardown import execute_teardown

# TODO: wrap with the actual MCP server SDK/decorators once the TrueForge
# MCP tool registration pattern is confirmed (PRD §14 open question).
TOOLS = {
    "analyze_infrastructure": analyze_infrastructure,
    "execute_teardown": execute_teardown,
}

if __name__ == "__main__":
    raise SystemExit("mcp_server.server is a library module; wire it into the MCP host process.")
