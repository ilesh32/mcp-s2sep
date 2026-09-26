"""MCP server exposing analyze_infrastructure and execute_teardown.

Run directly for local/stdio use (what a harness entrypoint expects), or
via Docker with MCP_TRANSPORT=streamable-http for a network-reachable
deployment (see Dockerfile).
"""
import os

from mcp.server.mcpserver import MCPServer

from mcp_server.analyzer import analyze_infrastructure as _analyze_infrastructure
from mcp_server.teardown import execute_teardown as _execute_teardown

app = MCPServer("cloud-cost-janitor-mcp")

# Resource ids returned by analyze_infrastructure during this server's
# lifetime. execute_teardown's provenance check (PRD §4/§9) validates
# against this instead of trusting an id supplied by the caller, so a
# hallucinated or injected id can never reach a delete call.
_known_resource_ids: set[str] = set()


@app.tool()
def analyze_infrastructure(region: str) -> dict:
    """Read-only scan for stopped EC2 instances, orphaned EBS volumes, and
    idle ALBs tagged hackathon-demo=true. Returns raw signals, not a verdict."""
    result = _analyze_infrastructure(region=region)
    _known_resource_ids.update(r["id"] for r in result["orphaned_volumes"])
    _known_resource_ids.update(r["id"] for r in result["idle_instances"])
    _known_resource_ids.update(lb["arn"] for lb in result["idle_load_balancers"])
    return result


@app.tool()
def execute_teardown(resource_ids: list[str], dry_run: bool = True) -> dict:
    """Delete approved resources. Rejects any id not seen in a prior
    analyze_infrastructure call this session, and re-verifies each
    resource still matches before deleting it."""
    return _execute_teardown(resource_ids, dry_run=dry_run, known_resource_ids=_known_resource_ids)


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        app.run(transport="stdio")
    else:
        app.run(
            transport=transport,
            host=os.environ.get("MCP_HOST", "0.0.0.0"),
            port=int(os.environ.get("MCP_PORT", "8000")),
        )


if __name__ == "__main__":
    main()
