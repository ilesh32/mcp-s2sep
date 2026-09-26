"""list_resources: read-only inventory of EC2 instances, EBS volumes and load
balancers in the configured region, in any state and with or without tags.

Unlike analyze_infrastructure this is not scoped to hackathon-demo=true and
makes no judgement about idleness. `in_scope` marks what the tag fence would
let execute_teardown act on. Listing does NOT make a resource eligible for
teardown: only analyze_infrastructure output enters the provenance allowlist.

IAM required: ec2:DescribeInstances, ec2:DescribeVolumes,
elasticloadbalancing:DescribeLoadBalancers, elasticloadbalancing:DescribeTags.
"""
from datetime import datetime, timezone

from mcp_server.analyzer import _is_demo_scoped, _tags_to_dict
from mcp_server.aws_client import get_client

_DESCRIBE_TAGS_BATCH = 20  # DescribeTags accepts at most 20 ARNs per call


def list_instances(ec2_client) -> list[dict]:
    instances = []
    for page in ec2_client.get_paginator("describe_instances").paginate():
        for reservation in page.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                tags = _tags_to_dict(inst.get("Tags"))
                instances.append(
                    {
                        "id": inst["InstanceId"],
                        "name": tags.get("Name"),
                        "state": inst["State"]["Name"],
                        "instance_type": inst["InstanceType"],
                        "az": inst.get("Placement", {}).get("AvailabilityZone"),
                        "launch_time": inst.get("LaunchTime"),
                        "tags": tags,
                        "in_scope": _is_demo_scoped(tags),
                    }
                )
    return instances


def list_volumes(ec2_client) -> list[dict]:
    volumes = []
    for page in ec2_client.get_paginator("describe_volumes").paginate():
        for vol in page.get("Volumes", []):
            tags = _tags_to_dict(vol.get("Tags"))
            volumes.append(
                {
                    "id": vol["VolumeId"],
                    "state": vol["State"],
                    "size_gb": vol["Size"],
                    "type": vol["VolumeType"],
                    "az": vol["AvailabilityZone"],
                    "attached_to": [a["InstanceId"] for a in vol.get("Attachments", [])],
                    "tags": tags,
                    "in_scope": _is_demo_scoped(tags),
                }
            )
    return volumes


def list_load_balancers(elbv2_client) -> list[dict]:
    lbs = []
    for page in elbv2_client.get_paginator("describe_load_balancers").paginate():
        lbs.extend(page.get("LoadBalancers", []))

    tags_by_arn: dict[str, dict] = {}
    for start in range(0, len(lbs), _DESCRIBE_TAGS_BATCH):
        arns = [lb["LoadBalancerArn"] for lb in lbs[start:start + _DESCRIBE_TAGS_BATCH]]
        for desc in elbv2_client.describe_tags(ResourceArns=arns).get("TagDescriptions", []):
            tags_by_arn[desc["ResourceArn"]] = _tags_to_dict(desc.get("Tags"))

    return [
        {
            "arn": lb["LoadBalancerArn"],
            "name": lb["LoadBalancerName"],
            "type": lb["Type"],
            "scheme": lb.get("Scheme"),
            "state": lb.get("State", {}).get("Code"),
            "tags": tags_by_arn.get(lb["LoadBalancerArn"], {}),
            "in_scope": _is_demo_scoped(tags_by_arn.get(lb["LoadBalancerArn"], {})),
        }
        for lb in lbs
    ]


def list_resources() -> dict:
    ec2_client = get_client("ec2")
    elbv2_client = get_client("elbv2")
    return {
        "instances": list_instances(ec2_client),
        "volumes": list_volumes(ec2_client),
        "load_balancers": list_load_balancers(elbv2_client),
        "listed_at": datetime.now(timezone.utc).isoformat(),
    }
