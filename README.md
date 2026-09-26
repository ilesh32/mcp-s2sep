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
├── server.py       # registers analyze_infrastructure + execute_teardown
├── aws_client.py   # boto3 session/client factory, region config
├── analyzer.py     # analyze_infrastructure logic
├── teardown.py     # execute_teardown: provenance check, re-verify, dependency ordering
└── audit.py        # append-only JSONL logger
tests/
├── test_analyzer.py   # moto-mocked, no real AWS calls
└── test_teardown.py   # asserts dry_run path never calls Delete*/Terminate*
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
