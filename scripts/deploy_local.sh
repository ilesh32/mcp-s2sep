#!/usr/bin/env bash
# Build the image and run the MCP server in Docker on this machine.
# Usage: scripts/deploy_local.sh [up|down|logs]
set -euo pipefail

cd "$(dirname "$0")/.."

IMAGE=cloud-cost-janitor-mcp
NAME=cloud-cost-janitor-mcp
PORT="${MCP_PORT:-8000}"

case "${1:-up}" in
  up)
    [ -f .env ] || { echo ".env missing (copy .env.example and fill in AWS creds)" >&2; exit 1; }
    # Bearer token clients must present. Use MCP_AUTH_TOKEN from the environment or
    # .env if set; otherwise generate one once and keep it in .mcp_token (gitignored).
    TOKEN="${MCP_AUTH_TOKEN:-$(sed -n 's/^MCP_AUTH_TOKEN=//p' .env | tail -1)}"
    if [ -z "$TOKEN" ]; then
      [ -s .mcp_token ] || { openssl rand -hex 32 > .mcp_token; chmod 600 .mcp_token; }
      TOKEN="$(cat .mcp_token)"
    fi
    docker build -t "$IMAGE" .
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    # AUDIT_LOG_PATH / MCP_* come from the image; a named volume persists the audit log.
    # Explicit -e overrides so values in .env meant for local runs don't break the container.
    docker run -d --name "$NAME" \
      --env-file .env \
      -e AUDIT_LOG_PATH=/app/data/audit_log.jsonl \
      -e MCP_TRANSPORT=streamable-http \
      -e MCP_HOST=0.0.0.0 \
      -e MCP_PORT=8000 \
      -e MCP_AUTH_TOKEN="$TOKEN" \
      -e MCP_DEBUG="${MCP_DEBUG:-true}" \
      -v cloud-cost-janitor-data:/app/data \
      -p "$PORT:8000" \
      "$IMAGE"
    echo "Waiting for http://localhost:$PORT/healthz ..."
    for _ in $(seq 1 30); do
      code=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$PORT/healthz" || true)
      if [ "$code" = "200" ]; then
        echo "Server is up. Auth: Authorization: Bearer <token> (token in .mcp_token or your MCP_AUTH_TOKEN)."
        exit 0
      fi
      sleep 1
    done
    echo "Server did not become ready; container logs:" >&2
    docker logs "$NAME" >&2
    exit 1
    ;;
  down) docker rm -f "$NAME" ;;
  logs) docker logs -f "$NAME" ;;
  *) echo "usage: $0 [up|down|logs]" >&2; exit 2 ;;
esac
