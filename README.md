# mcp-s2sep

test-mcp-truefoundry

The MCP tool server for [Cloud Cost Janitor](https://github.com/ilesh32/hackculture-26sep):
two AWS tools, read-only scanning and gated teardown, no cost logic and no
LLM code. Extracted into its own repo so it runs as a standalone MCP
process rather than a locally-imported module — which is the shape MCP
servers are meant to run in.

## Tools

- **`analyze_infrastructure(region: str) -> JSON`** — read-only boto3 scan
  for stopped EC2 instances, orphaned EBS volumes, and idle ALBs, scoped to
  resources tagged `hackathon-demo=true`. Returns raw signals, not a
  verdict — the calling agent decides what's safe to flag.
- **`execute_teardown(resource_ids, dry_run) -> JSON`** — the destructive
  tool. Before any delete call it runs a provenance check (every id must
  have come from a prior `analyze_infrastructure` call this session) and a
  re-verification pass (re-runs the Describe* filter immediately before
  deleting). Hard-capped at 10 resources per call. Every attempt is logged
  to an append-only JSONL audit file.

## Repository structure

```
mcp_server/
├── server.py       # MCP server (mcp SDK v2): registers both tools, stdio/HTTP transport
├── aws_client.py   # boto3 session/client factory, region config
├── analyzer.py     # analyze_infrastructure logic
├── teardown.py     # execute_teardown: provenance check, re-verify, dependency ordering
└── audit.py        # append-only JSONL logger
tests/
├── test_analyzer.py   # moto-mocked, no real AWS calls
└── test_teardown.py   # asserts dry_run path never calls Delete*/Terminate*
Dockerfile            # container build for the streamable-http deployment
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# fill in AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_REGION
```

### AWS IAM — least privilege

Read-only tool needs only:
`ec2:DescribeVolumes`, `ec2:DescribeInstances`,
`elasticloadbalancing:DescribeLoadBalancers`,
`elasticloadbalancing:DescribeTargetGroups`,
`elasticloadbalancing:DescribeTargetHealth`,
`elasticloadbalancing:DescribeTags`.

Destructive tool needs only:
`ec2:TerminateInstances`, `ec2:DeleteVolume`,
`elasticloadbalancing:DeleteLoadBalancer`.

No `*` actions, no other services. See the parent project's README for a
ready-to-paste IAM policy JSON with tag-scoped conditions on the delete
actions.

## Running tests

```bash
pytest tests/
```

All tests run against [moto](https://github.com/getmoto/moto)-mocked AWS —
no real AWS calls, no credentials required.

## Safety guardrails

- **Provenance allowlist** — rejects any resource id not returned by
  `analyze_infrastructure` earlier in the session.
- **Blast-radius cap** — hard limit of 10 resources per `execute_teardown`
  call.
- **Region/tag scope fence** — both tools only ever see resources tagged
  `hackathon-demo=true` in the configured region.
- **Re-verification before delete** — re-runs the Describe* filter
  immediately before deleting.
- **Kill switch** — set `TEARDOWN_DISABLED=true` to hard-disable the
  destructive tool at any time, independent of any harness config.

## Using this from an agent harness

Point your harness's MCP tool registration at this repo (as an external
process, not a local import) and wire an approval gate in front of
`execute_teardown` — this server enforces its own safety invariants
(provenance, blast-radius cap, kill switch) but does **not** enforce human
approval itself. That belongs at the harness/orchestration layer.

The provenance check is tracked server-side: every id returned by
`analyze_infrastructure` during the server process's lifetime is
remembered, and `execute_teardown` rejects anything else. This means one
running container instance corresponds to one reasoning session — don't
share a single deployed instance across unrelated agent runs if you need
strict per-session provenance.

## Running with Docker

Build and run locally (stdio is the default transport, matching how a
harness spawns an MCP server as a subprocess):

```bash
docker build -t cloud-cost-janitor-mcp .
docker run -it --rm \
  --env-file .env \
  cloud-cost-janitor-mcp
```

For a network-reachable deployment, the image defaults to
`MCP_TRANSPORT=streamable-http` on `0.0.0.0:8000` (set in the Dockerfile),
so just publish the port:

```bash
docker run -d --name cloud-cost-janitor-mcp \
  -p 8000:8000 \
  --env-file .env \
  cloud-cost-janitor-mcp
```

The server is then reachable at `http://localhost:8000/mcp` (streamable
HTTP transport). Verify it's up:

```bash
curl -i -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke-test","version":"0.1"}}}'
```

A 200 response with the server's `capabilities` confirms it's serving
correctly. Environment variables the container reads:

| Variable | Default (in image) | Purpose |
| --- | --- | --- |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` | — (required) | scoped IAM credentials, see above |
| `MCP_TRANSPORT` | `streamable-http` | `stdio`, `sse`, or `streamable-http` |
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8000` | bind address for `sse`/`streamable-http` |
| `AUDIT_LOG_PATH` | `/app/data/audit_log.jsonl` | mount a volume at `/app/data` to persist the audit log across container restarts |
| `TEARDOWN_DISABLED` | unset | set to `true` as a kill switch |

The container runs as a non-root user and never bakes credentials into the
image — pass them at runtime via `--env-file` or your orchestrator's
secrets mechanism.
