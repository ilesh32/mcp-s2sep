"""moto-mocked tests for list_resources — no real AWS calls."""
import os

import boto3
from moto import mock_aws

os.environ.setdefault("AWS_REGION", "us-east-1")

from mcp_server.inventory import list_resources  # noqa: E402

TAG = [{"Key": "hackathon-demo", "Value": "true"}]


@mock_aws
def test_lists_everything_regardless_of_state_or_tag(monkeypatch):
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(key, "testing")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    ec2 = boto3.client("ec2", region_name="us-east-1")

    running = ec2.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.micro")["Instances"][0]["InstanceId"]
    ec2.create_tags(Resources=[running], Tags=[{"Key": "Name", "Value": "web"}])
    stopped = ec2.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.micro")["Instances"][0]["InstanceId"]
    ec2.create_tags(Resources=[stopped], Tags=TAG)
    ec2.stop_instances(InstanceIds=[stopped])
    vol = ec2.create_volume(Size=5, AvailabilityZone="us-east-1a", VolumeType="gp3")["VolumeId"]

    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
    subnets = [ec2.create_subnet(VpcId=vpc, CidrBlock=f"10.0.{i}.0/24", AvailabilityZone=f"us-east-1{az}")["Subnet"]["SubnetId"] for i, az in enumerate("ab")]
    lb = boto3.client("elbv2", region_name="us-east-1").create_load_balancer(Name="lb1", Subnets=subnets, Tags=TAG)["LoadBalancers"][0]["LoadBalancerArn"]

    result = list_resources()

    instances = {i["id"]: i for i in result["instances"]}
    assert instances[running]["state"] == "running" and instances[running]["name"] == "web"
    assert instances[running]["in_scope"] is False  # untagged: listed, but out of teardown scope
    assert instances[stopped]["state"] == "stopped" and instances[stopped]["in_scope"] is True
    volumes = {v["id"]: v for v in result["volumes"]}
    assert volumes[vol]["in_scope"] is False and volumes[vol]["attached_to"] == []
    assert any(v["attached_to"] == [running] or v["attached_to"] == [stopped] for v in result["volumes"])  # root volumes
    assert [(l["arn"], l["in_scope"]) for l in result["load_balancers"]] == [(lb, True)]
