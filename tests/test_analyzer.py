"""moto-mocked tests for analyze_infrastructure — no real AWS calls."""
import os

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("AWS_REGION", "us-east-1")

from mcp_server.analyzer import analyze_infrastructure  # noqa: E402


@pytest.fixture
def aws_credentials():
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"


@mock_aws
def test_finds_tagged_orphaned_volume(aws_credentials):
    ec2 = boto3.client("ec2", region_name="us-east-1")
    vol = ec2.create_volume(Size=37, AvailabilityZone="us-east-1a", VolumeType="gp3")
    ec2.create_tags(Resources=[vol["VolumeId"]], Tags=[{"Key": "hackathon-demo", "Value": "true"}])

    # untagged volume must not be returned
    ec2.create_volume(Size=100, AvailabilityZone="us-east-1a", VolumeType="gp3")

    result = analyze_infrastructure(region="us-east-1")

    ids = [v["id"] for v in result["orphaned_volumes"]]
    assert vol["VolumeId"] in ids
    assert len(result["orphaned_volumes"]) == 1


@mock_aws
def test_finds_tagged_stopped_instance(aws_credentials):
    ec2 = boto3.client("ec2", region_name="us-east-1")
    run = ec2.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.micro")
    instance_id = run["Instances"][0]["InstanceId"]
    ec2.create_tags(Resources=[instance_id], Tags=[{"Key": "hackathon-demo", "Value": "true"}])
    ec2.stop_instances(InstanceIds=[instance_id])

    result = analyze_infrastructure(region="us-east-1")

    ids = [i["id"] for i in result["idle_instances"]]
    assert instance_id in ids


@mock_aws
def test_returns_empty_when_nothing_tagged(aws_credentials):
    ec2 = boto3.client("ec2", region_name="us-east-1")
    ec2.create_volume(Size=50, AvailabilityZone="us-east-1a", VolumeType="gp3")

    result = analyze_infrastructure(region="us-east-1")

    assert result["orphaned_volumes"] == []
    assert result["idle_instances"] == []
    assert result["idle_load_balancers"] == []
