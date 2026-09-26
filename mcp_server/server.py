"""MCP server exposing analyze_infrastructure and execute_teardown.

Run directly for local/stdio use (what a harness entrypoint expects), or via
Docker with MCP_TRANSPORT=streamable-http for a network-reachable deployment
(see Dockerfile). The HTTP transport requires MCP_AUTH_TOKEN and serves an
unauthenticated GET /healthz for liveness probes.
"""
import functools
import os
import sys

from botocore.exceptions import BotoCoreError, ClientError
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from starlette.responses import PlainTextResponse

from mcp_server import debug
from mcp_server.analyzer import analyze_infrastructure as _analyze_infrastructure
from mcp_server.aws_client import get_region
from mcp_server.http_auth import HEALTH_PATH, BearerTokenMiddleware
from mcp_server.inventory import list_resources as _list_resources
from mcp_server.teardown import ProvenanceError, TeardownDisabledError, execute_teardown as _execute_teardown

debug.configure()
log = debug.logger

app = MCPServer(
    "cloud-cost-janitor-mcp",
    debug=debug.enabled(),
    log_level="DEBUG" if debug.enabled() else "INFO",
)

# Resource ids returned by analyze_infrastructure during this server's
# lifetime. execute_teardown's provenance check (PRD §4/§9) validates
# against this instead of trusting an id supplied by the caller, so a
# hallucinated or injected id can never reach a delete call.
_known_resource_ids: set[str] = set()


def reports_failures_to_caller(fn):
    """Expected failures (guardrail rejections, bad input, AWS API errors) are
    outcomes the caller needs to see, not crashes: surface the reason instead of
    the SDK's generic 'Error executing tool'. Anything else stays a crash."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ProvenanceError, TeardownDisabledError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        except ClientError as exc:
            error = exc.response.get("Error", {})
            raise ToolError(f"AWS error {error.get('Code')}: {error.get('Message')}") from exc
        except BotoCoreError as exc:
            raise ToolError(f"AWS client error: {exc}") from exc

    return wrapper


def _require_configured_region(region: str) -> None:
    configured = get_region()
    if region != configured:
        raise ValueError(f"this server is scoped to region {configured!r}, not {region!r}")


@app.tool()
@debug.logged_tool
@reports_failures_to_caller
def list_resources(region: str) -> dict:
    """Read-only inventory of ALL EC2 instances, EBS volumes and load balancers
    in the configured region, in any state, tagged or not, with their tags.
    `in_scope` is true for resources tagged hackathon-demo=true (the only ones
    execute_teardown can act on). Listing a resource does not make it eligible
    for teardown; run analyze_infrastructure for that."""
    _require_configured_region(region)
    return _list_resources()


@app.tool()
@debug.logged_tool
@reports_failures_to_caller
def analyze_infrastructure(region: str) -> dict:
    """Read-only scan for stopped EC2 instances, orphaned EBS volumes, and
    idle load balancers tagged hackathon-demo=true. Returns raw signals, not a
    verdict. `region` must equal the region this server is configured for."""
    _require_configured_region(region)
    result = _analyze_infrastructure(region=region)
    _known_resource_ids.update(r["id"] for r in result["orphaned_volumes"])
    _known_resource_ids.update(r["id"] for r in result["idle_instances"])
    _known_resource_ids.update(lb["arn"] for lb in result["idle_load_balancers"])
    log.debug("provenance allowlist now holds %d ids: %s", len(_known_resource_ids), sorted(_known_resource_ids))
    return result


@app.tool()
@debug.logged_tool
@reports_failures_to_caller
def execute_teardown(resource_ids: list[str]) -> dict:
    """PERMANENTLY delete resources (terminate instances, delete volumes and
    load balancers). At most 10 ids per call. Rejects any id not returned by a
    prior analyze_infrastructure call, and re-verifies each resource still
    matches immediately before deleting it. Returns per-resource
    deleted / skipped / failed lists; a partial failure does not abort the rest."""
    return _execute_teardown(resource_ids, known_resource_ids=_known_resource_ids)


@app.custom_route(HEALTH_PATH, methods=["GET"])
async def healthz(_request):
    return PlainTextResponse("ok")


def run_http(host: str, port: int) -> None:
    import uvicorn

    token = os.environ.get("MCP_AUTH_TOKEN", "")
    if len(token) < 16:
        sys.exit("MCP_AUTH_TOKEN (at least 16 characters) is required for the streamable-http transport")

    starlette_app = app.streamable_http_app(host=host)
    starlette_app.add_middleware(BearerTokenMiddleware, token=token)
    log.info("serving MCP on http://%s:%s/mcp (bearer auth required, health at %s)", host, port, HEALTH_PATH)
    uvicorn.run(starlette_app, host=host, port=port, log_level="debug" if debug.enabled() else "info")


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        app.run(transport="stdio")
    elif transport == "streamable-http":
        run_http(os.environ.get("MCP_HOST", "0.0.0.0"), int(os.environ.get("MCP_PORT", "8000")))
    else:
        sys.exit(f"unsupported MCP_TRANSPORT {transport!r}: use 'stdio' or 'streamable-http'")


if __name__ == "__main__":
    main()
