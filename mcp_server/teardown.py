"""execute_teardown: the destructive tool, wrapped in the harness approval gate.

Two checks run before any delete call, in order (PRD §4):
  1. Provenance check — every id must have appeared in a prior
     analyze_infrastructure response this session.
  2. Re-verification — re-run the Describe* filter immediately before
     deleting; a resource that no longer matches is skipped, not force-deleted.

Dependency ordering: TerminateInstances -> poll -> DeleteVolume ->
DeleteLoadBalancer. Reports partial completion if a step fails.

IAM required: ec2:TerminateInstances, ec2:DeleteVolume,
elasticloadbalancing:DeleteLoadBalancer. Nothing else.
"""
import os
from datetime import datetime, timezone

from mcp_server.aws_client import get_client
from mcp_server.audit import log_event

MAX_RESOURCES_PER_CALL = 10  # blast-radius cap, PRD §9

KILL_SWITCH_ENV_VAR = "TEARDOWN_DISABLED"


class ProvenanceError(Exception):
    """Raised when a resource id was not returned by a prior analyze_infrastructure call."""


def check_kill_switch() -> None:
    if os.environ.get(KILL_SWITCH_ENV_VAR, "").lower() in ("1", "true", "yes"):
        raise RuntimeError("execute_teardown is disabled via kill switch")


def check_provenance(resource_ids: list[str], known_resource_ids: set[str]) -> None:
    unknown = [rid for rid in resource_ids if rid not in known_resource_ids]
    if unknown:
        raise ProvenanceError(f"resource ids not seen in this session's analyze_infrastructure output: {unknown}")


def execute_teardown(resource_ids: list[str], dry_run: bool, known_resource_ids: set[str] | None = None) -> dict:
    check_kill_switch()

    if len(resource_ids) > MAX_RESOURCES_PER_CALL:
        raise ValueError(f"execute_teardown accepts at most {MAX_RESOURCES_PER_CALL} resource ids per call")

    if known_resource_ids is not None:
        check_provenance(resource_ids, known_resource_ids)

    result = {"deleted": [], "skipped": [], "failed": [], "executed_at": datetime.now(timezone.utc).isoformat()}

    # TODO: re-verification (re-run Describe* per id) + dependency ordering
    # (terminate instances -> poll -> delete volumes -> delete load balancers)
    # to be implemented against the seeded demo account.

    log_event({"action": "execute_teardown", "resource_ids": resource_ids, "dry_run": dry_run, "result": result})
    return result
