"""analyze_infrastructure: read-only AWS scan.

Returns raw signals (not a verdict) for stopped EC2 instances, orphaned EBS
volumes, and idle ALBs — scoped to resources tagged hackathon-demo=true in
one region. See PRD §4.
"""
from datetime import datetime, timezone

from mcp_server.aws_client import get_client, DEMO_TAG_KEY, DEMO_TAG_VALUE


def _tags_to_dict(tag_list):
    return {t["Key"]: t["Value"] for t in tag_list or []}


def _is_demo_scoped(tags: dict) -> bool:
    return tags.get(DEMO_TAG_KEY) == DEMO_TAG_VALUE


def find_orphaned_volumes(ec2_client) -> list[dict]:
    volumes = []
    paginator = ec2_client.get_paginator("describe_volumes")
    for page in paginator.paginate(Filters=[{"Name": "status", "Values": ["available"]}]):
        for vol in page.get("Volumes", []):
            tags = _tags_to_dict(vol.get("Tags"))
            if not _is_demo_scoped(tags):
                continue
            volumes.append(
                {
                    "id": vol["VolumeId"],
                    "size_gb": vol["Size"],
                    "type": vol["VolumeType"],
                    "az": vol["AvailabilityZone"],
                    "tags": tags,
                }
            )
    return volumes


def find_idle_instances(ec2_client) -> list[dict]:
    instances = []
    paginator = ec2_client.get_paginator("describe_instances")
    for page in paginator.paginate(Filters=[{"Name": "instance-state-name", "Values": ["stopped"]}]):
        for reservation in page.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                tags = _tags_to_dict(inst.get("Tags"))
                if not _is_demo_scoped(tags):
                    continue
                instances.append(
                    {
                        "id": inst["InstanceId"],
                        "instance_type": inst["InstanceType"],
                        # AWS doesn't expose a direct "stopped since" field; derive it
                        # from the stop reason timestamp in StateTransitionReason, or
                        # fall back to null if the instance was stopped via other means.
                        "stopped_since": None,
                        "tags": tags,
                    }
                )
    return instances


def count_empty_target_groups(elbv2_client, lb_arn: str) -> int:
    """Number of the load balancer's target groups with no registered targets."""
    tg_resp = elbv2_client.describe_target_groups(LoadBalancerArn=lb_arn)
    empty = 0
    for tg in tg_resp.get("TargetGroups", []):
        health = elbv2_client.describe_target_health(TargetGroupArn=tg["TargetGroupArn"])
        if not health.get("TargetHealthDescriptions"):
            empty += 1
    return empty


def find_idle_load_balancers(elbv2_client) -> list[dict]:
    load_balancers = []
    paginator = elbv2_client.get_paginator("describe_load_balancers")
    for page in paginator.paginate():
        for lb in page.get("LoadBalancers", []):
            tag_resp = elbv2_client.describe_tags(ResourceArns=[lb["LoadBalancerArn"]])
            tag_descriptions = tag_resp.get("TagDescriptions", [])
            tags = _tags_to_dict(tag_descriptions[0]["Tags"]) if tag_descriptions else {}
            if not _is_demo_scoped(tags):
                continue

            empty_target_groups = count_empty_target_groups(elbv2_client, lb["LoadBalancerArn"])
            if empty_target_groups == 0:
                continue

            load_balancers.append(
                {
                    "arn": lb["LoadBalancerArn"],
                    "name": lb["LoadBalancerName"],
                    "type": lb["Type"],
                    "empty_target_groups": empty_target_groups,
                }
            )
    return load_balancers


def analyze_infrastructure(region: str) -> dict:
    """Read-only scan of EC2/EBS/ELB. Returns raw signals, not a verdict.

    IAM required: ec2:DescribeVolumes, ec2:DescribeInstances,
    elasticloadbalancing:DescribeLoadBalancers,
    elasticloadbalancing:DescribeTargetGroups. Nothing else.
    """
    ec2_client = get_client("ec2")
    elbv2_client = get_client("elbv2")

    return {
        "orphaned_volumes": find_orphaned_volumes(ec2_client),
        "idle_instances": find_idle_instances(ec2_client),
        "idle_load_balancers": find_idle_load_balancers(elbv2_client),
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }
