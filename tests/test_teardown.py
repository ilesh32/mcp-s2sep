"""moto-mocked tests for execute_teardown: guardrails and real deletion. No real AWS calls."""
import json
import os

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("AWS_REGION", "us-east-1")

from mcp_server.teardown import (  # noqa: E402
    MAX_RESOURCES_PER_CALL,
    ProvenanceError,
    check_provenance,
    execute_teardown,
)

TAG = [{"Key": "hackathon-demo", "Value": "true"}]


@pytest.fixture(autouse=True)
def aws_env(monkeypatch, tmp_path):
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(key, "testing")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.delenv("TEARDOWN_DISABLED", raising=False)
    monkeypatch.setattr("mcp_server.audit.AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    return tmp_path / "audit.jsonl"


def _volume(ec2, tagged=True):
    vol = ec2.create_volume(Size=1, AvailabilityZone="us-east-1a", VolumeType="gp3")
    if tagged:
        ec2.create_tags(Resources=[vol["VolumeId"]], Tags=TAG)
    return vol["VolumeId"]


def _instance(ec2, stop=True):
    inst = ec2.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.micro")["Instances"][0]
    ec2.create_tags(Resources=[inst["InstanceId"]], Tags=TAG)
    if stop:
        ec2.stop_instances(InstanceIds=[inst["InstanceId"]])
    return inst["InstanceId"]


def _volume_ids(ec2):
    return {v["VolumeId"] for v in ec2.describe_volumes()["Volumes"]}


# --- guardrails (no AWS needed) -------------------------------------------------

def test_provenance_rejects_unknown_id():
    with pytest.raises(ProvenanceError):
        check_provenance(["vol-unknown"], known_resource_ids={"vol-known"})


def test_provenance_accepts_known_id():
    check_provenance(["vol-known"], known_resource_ids={"vol-known", "i-known"})


def test_blast_radius_cap_enforced():
    ids = [f"vol-{i}" for i in range(MAX_RESOURCES_PER_CALL + 1)]
    with pytest.raises(ValueError, match="at most"):
        execute_teardown(ids, known_resource_ids=set(ids))


def test_empty_request_rejected():
    with pytest.raises(ValueError, match="at least one"):
        execute_teardown([], known_resource_ids=set())


def test_unsupported_id_rejected():
    with pytest.raises(ValueError, match="unsupported resource id"):
        execute_teardown(["sg-123"], known_resource_ids={"sg-123"})


def test_kill_switch_blocks_execution(monkeypatch):
    from mcp_server.teardown import TeardownDisabledError

    monkeypatch.setenv("TEARDOWN_DISABLED", "true")
    with pytest.raises(TeardownDisabledError):
        execute_teardown(["vol-known"], known_resource_ids={"vol-known"})


def test_dry_run_is_not_supported():
    with pytest.raises(TypeError):
        execute_teardown(["vol-1"], dry_run=True, known_resource_ids={"vol-1"})


# --- real deletion against moto -------------------------------------------------

@mock_aws
def test_deletes_tagged_orphaned_volume_and_audits(aws_env):
    ec2 = boto3.client("ec2", region_name="us-east-1")
    vol_id, other_id = _volume(ec2), _volume(ec2, tagged=False)
    known = {vol_id}

    result = execute_teardown([vol_id], known_resource_ids=known)

    assert result["deleted"] == [{"id": vol_id, "type": "volume"}]
    assert result["skipped"] == result["failed"] == []
    assert _volume_ids(ec2) == {other_id}  # only the requested volume is gone
    assert vol_id not in known  # can't be re-submitted

    events = [json.loads(line) for line in aws_env.read_text().splitlines()]
    assert [e["event"] for e in events] == ["teardown_requested", "teardown_completed"]
    assert events[1]["result"]["deleted"][0]["id"] == vol_id


@mock_aws
def test_skips_volume_that_lost_its_tag_since_scan():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    vol_id = _volume(ec2)
    ec2.delete_tags(Resources=[vol_id], Tags=TAG)

    result = execute_teardown([vol_id], known_resource_ids={vol_id})

    assert result["deleted"] == []
    assert "no longer tagged" in result["skipped"][0]["reason"]
    assert vol_id in _volume_ids(ec2)


@mock_aws
def test_skips_volume_that_got_attached_since_scan():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    vol_id = _volume(ec2)
    inst_id = _instance(ec2, stop=False)
    ec2.attach_volume(VolumeId=vol_id, InstanceId=inst_id, Device="/dev/sdf")

    result = execute_teardown([vol_id], known_resource_ids={vol_id})

    assert result["deleted"] == []
    assert "in-use" in result["skipped"][0]["reason"]
    assert vol_id in _volume_ids(ec2)


@mock_aws
def test_skips_resource_that_no_longer_exists():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    vol_id = _volume(ec2)
    ec2.delete_volume(VolumeId=vol_id)

    result = execute_teardown([vol_id], known_resource_ids={vol_id})

    assert result["skipped"] == [{"id": vol_id, "reason": "resource no longer exists"}]


@mock_aws
def test_terminates_stopped_instance_but_not_running_one():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    stopped, running = _instance(ec2), _instance(ec2, stop=False)

    result = execute_teardown([stopped, running], known_resource_ids={stopped, running})

    assert result["deleted"] == [{"id": stopped, "type": "instance"}]
    assert running in [s["id"] for s in result["skipped"]]
    states = {
        i["InstanceId"]: i["State"]["Name"]
        for r in ec2.describe_instances()["Reservations"]
        for i in r["Instances"]
    }
    assert states[stopped] == "terminated"
    assert states[running] == "running"


@mock_aws
def test_deletes_idle_load_balancer_and_skips_busy_one():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    elbv2 = boto3.client("elbv2", region_name="us-east-1")
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
    subnets = [
        ec2.create_subnet(VpcId=vpc, CidrBlock=f"10.0.{i}.0/24", AvailabilityZone=f"us-east-1{az}")["Subnet"]["SubnetId"]
        for i, az in enumerate("ab")
    ]

    def lb_with_target_group(name, register_target):
        lb = elbv2.create_load_balancer(Name=name, Subnets=subnets, Tags=TAG)["LoadBalancers"][0]["LoadBalancerArn"]
        tg = elbv2.create_target_group(Name=f"tg-{name}", Protocol="HTTP", Port=80, VpcId=vpc)["TargetGroups"][0]["TargetGroupArn"]
        elbv2.create_listener(LoadBalancerArn=lb, Protocol="HTTP", Port=80,
                              DefaultActions=[{"Type": "forward", "TargetGroupArn": tg}])
        if register_target:
            inst = _instance(ec2, stop=False)
            elbv2.register_targets(TargetGroupArn=tg, Targets=[{"Id": inst}])
        return lb

    idle, busy = lb_with_target_group("idle", False), lb_with_target_group("busy", True)

    result = execute_teardown([idle, busy], known_resource_ids={idle, busy})

    assert result["deleted"] == [{"id": idle, "type": "load_balancer"}]
    assert [s["id"] for s in result["skipped"]] == [busy]
    remaining = {lb["LoadBalancerArn"] for lb in elbv2.describe_load_balancers()["LoadBalancers"]}
    assert remaining == {busy}


@mock_aws
def test_partial_failure_does_not_abort_remaining_resources(monkeypatch):
    from botocore.exceptions import ClientError

    ec2 = boto3.client("ec2", region_name="us-east-1")
    first, second = _volume(ec2), _volume(ec2)

    real_get_client = __import__("mcp_server.teardown", fromlist=["get_client"]).get_client

    def get_client(service):
        client = real_get_client(service)
        if service == "ec2":
            original = client.delete_volume

            def delete_volume(VolumeId):
                if VolumeId == first:
                    raise ClientError({"Error": {"Code": "VolumeInUse", "Message": "boom"}}, "DeleteVolume")
                return original(VolumeId=VolumeId)

            client.delete_volume = delete_volume
        return client

    monkeypatch.setattr("mcp_server.teardown.get_client", get_client)

    result = execute_teardown([first, second], known_resource_ids={first, second})

    assert [f["id"] for f in result["failed"]] == [first]
    assert result["deleted"] == [{"id": second, "type": "volume"}]


@mock_aws
def test_audit_failure_blocks_deletion(monkeypatch, tmp_path):
    ec2 = boto3.client("ec2", region_name="us-east-1")
    vol_id = _volume(ec2)
    monkeypatch.setattr("mcp_server.audit.AUDIT_LOG_PATH", str(tmp_path / "missing-dir" / "audit.jsonl"))

    with pytest.raises(OSError):
        execute_teardown([vol_id], known_resource_ids={vol_id})

    assert vol_id in _volume_ids(ec2)  # fail closed: nothing deleted
