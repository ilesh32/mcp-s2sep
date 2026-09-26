"""execute_teardown: the destructive tool. Deletes are real; there is no dry run.

Before any delete call, in order (PRD §4):
  1. Guardrails — kill switch, non-empty, blast-radius cap, id format.
  2. Provenance check — every id must have appeared in a prior
     analyze_infrastructure response this session.
  3. Re-verification — re-run the Describe* checks per resource immediately
     before deleting; a resource that no longer matches (state changed, tag
     removed, already gone) is skipped, never force-deleted.

Dependency ordering: TerminateInstances -> wait -> DeleteVolume ->
DeleteLoadBalancer. One resource failing does not abort the rest; the result
reports deleted / skipped / failed per resource.

The audit log is fail-closed: the intent is written before the first delete
call, and if that write fails nothing is deleted.

IAM required: ec2:TerminateInstances, ec2:DeleteVolume,
elasticloadbalancing:DeleteLoadBalancer (plus the Describe* calls used by
analyze_infrastructure and ec2:DescribeInstances for the terminate waiter).
"""
import os
import threading
from datetime import datetime, timezone

from botocore.exceptions import ClientError, WaiterError

from mcp_server.analyzer import _is_demo_scoped, _tags_to_dict, count_empty_target_groups
from mcp_server.aws_client import get_client
from mcp_server.audit import log_event
from mcp_server.debug import logger

MAX_RESOURCES_PER_CALL = 10  # blast-radius cap, PRD §9

KILL_SWITCH_ENV_VAR = "TEARDOWN_DISABLED"

INSTANCE, VOLUME, LOAD_BALANCER = "instance", "volume", "load_balancer"

_NOT_FOUND_CODES = {"InvalidInstanceID.NotFound", "InvalidVolume.NotFound", "LoadBalancerNotFound"}

# Only one teardown at a time per server process: two concurrent calls could
# otherwise race each other's re-verification and delete calls.
_teardown_lock = threading.Lock()


class ProvenanceError(Exception):
    """Raised when a resource id was not returned by a prior analyze_infrastructure call."""


class TeardownDisabledError(RuntimeError):
    """Raised when the TEARDOWN_DISABLED kill switch is on."""


def check_kill_switch() -> None:
    if os.environ.get(KILL_SWITCH_ENV_VAR, "").lower() in ("1", "true", "yes"):
        raise TeardownDisabledError("execute_teardown is disabled via kill switch")


def check_provenance(resource_ids: list[str], known_resource_ids: set[str]) -> None:
    unknown = [rid for rid in resource_ids if rid not in known_resource_ids]
    if unknown:
        raise ProvenanceError(f"resource ids not seen in this session's analyze_infrastructure output: {unknown}")


def classify(resource_id: str) -> str:
    if resource_id.startswith("i-"):
        return INSTANCE
    if resource_id.startswith("vol-"):
        return VOLUME
    if resource_id.startswith("arn:aws:elasticloadbalancing:"):
        return LOAD_BALANCER
    raise ValueError(f"unsupported resource id (expected i-*, vol-* or a load balancer ARN): {resource_id!r}")


def _verify(kind: str, resource_id: str, ec2, elbv2) -> str | None:
    """Re-check the resource against the same criteria analyze_infrastructure used.
    Returns None if it is still safe to delete, else the reason to skip it."""
    try:
        if kind == INSTANCE:
            reservations = ec2.describe_instances(InstanceIds=[resource_id])["Reservations"]
            inst = reservations[0]["Instances"][0]
            state = inst["State"]["Name"]
            if state != "stopped":
                return f"instance state is {state!r}, expected 'stopped'"
            tags = _tags_to_dict(inst.get("Tags"))
        elif kind == VOLUME:
            vol = ec2.describe_volumes(VolumeIds=[resource_id])["Volumes"][0]
            if vol["State"] != "available":
                return f"volume state is {vol['State']!r}, expected 'available'"
            tags = _tags_to_dict(vol.get("Tags"))
        else:
            elbv2.describe_load_balancers(LoadBalancerArns=[resource_id])
            descriptions = elbv2.describe_tags(ResourceArns=[resource_id]).get("TagDescriptions", [])
            tags = _tags_to_dict(descriptions[0]["Tags"]) if descriptions else {}
            if _is_demo_scoped(tags) and count_empty_target_groups(elbv2, resource_id) == 0:
                return "load balancer no longer has an empty target group"
    except ClientError as exc:
        if exc.response["Error"]["Code"] in _NOT_FOUND_CODES:
            return "resource no longer exists"
        raise
    if not _is_demo_scoped(tags):
        return "resource is no longer tagged hackathon-demo=true"
    return None


def execute_teardown(resource_ids: list[str], known_resource_ids: set[str] | None = None) -> dict:
    check_kill_switch()

    resource_ids = list(dict.fromkeys(resource_ids))  # de-duplicate, keep order
    if not resource_ids:
        raise ValueError("execute_teardown requires at least one resource id")
    if len(resource_ids) > MAX_RESOURCES_PER_CALL:
        raise ValueError(f"execute_teardown accepts at most {MAX_RESOURCES_PER_CALL} resource ids per call")
    kinds = {rid: classify(rid) for rid in resource_ids}

    if known_resource_ids is not None:
        check_provenance(resource_ids, known_resource_ids)

    with _teardown_lock:
        result = _run_teardown(resource_ids, kinds)
        if known_resource_ids is not None:
            known_resource_ids.difference_update(item["id"] for item in result["deleted"])
        return result


def _run_teardown(resource_ids: list[str], kinds: dict[str, str]) -> dict:
    result = {"deleted": [], "skipped": [], "failed": [], "warnings": []}
    ec2 = get_client("ec2")
    elbv2 = get_client("elbv2")

    # Fail closed: record intent before any delete; if the audit log is
    # unwritable this raises and nothing has been touched.
    log_event({"event": "teardown_requested", "resource_ids": resource_ids})

    verified: dict[str, list[str]] = {INSTANCE: [], VOLUME: [], LOAD_BALANCER: []}
    for rid in resource_ids:
        try:
            reason = _verify(kinds[rid], rid, ec2, elbv2)
        except ClientError as exc:
            logger.error("re-verification of %s failed: %s", rid, exc)
            result["failed"].append({"id": rid, "error": f"re-verification failed: {exc}"})
            continue
        if reason:
            logger.warning("skipping %s: %s", rid, reason)
            result["skipped"].append({"id": rid, "reason": reason})
        else:
            verified[kinds[rid]].append(rid)

    def attempt(rid: str, action) -> bool:
        try:
            action()
        except ClientError as exc:
            logger.error("delete of %s failed: %s", rid, exc)
            result["failed"].append({"id": rid, "error": str(exc)})
            return False
        result["deleted"].append({"id": rid, "type": kinds[rid]})
        return True

    # 1. Instances first: terminate, then wait so dependent volumes are released.
    terminated = [rid for rid in verified[INSTANCE] if attempt(rid, lambda rid=rid: ec2.terminate_instances(InstanceIds=[rid]))]
    if terminated:
        try:
            ec2.get_waiter("instance_terminated").wait(
                InstanceIds=terminated, WaiterConfig={"Delay": 5, "MaxAttempts": 36}
            )
        except WaiterError as exc:
            result["warnings"].append(f"terminate requested but not confirmed terminated within 3 minutes: {exc}")
        except ClientError as exc:
            result["warnings"].append(f"could not confirm instance termination: {exc}")

    # 2. Volumes, then 3. load balancers.
    for rid in verified[VOLUME]:
        attempt(rid, lambda rid=rid: ec2.delete_volume(VolumeId=rid))
    for rid in verified[LOAD_BALANCER]:
        attempt(rid, lambda rid=rid: elbv2.delete_load_balancer(LoadBalancerArn=rid))

    result["executed_at"] = datetime.now(timezone.utc).isoformat()
    log_event({"event": "teardown_completed", "resource_ids": resource_ids, "result": result})
    return result
