"""boto3 session/client factory, scoped to one region and the demo tag."""
import os
import boto3

DEMO_TAG_KEY = "hackathon-demo"
DEMO_TAG_VALUE = "true"


def get_region() -> str:
    region = os.environ.get("AWS_REGION")
    if not region:
        raise RuntimeError("AWS_REGION must be set (single-region scope per PRD §2)")
    return region


def get_session() -> boto3.Session:
    return boto3.Session(region_name=get_region())


def get_client(service_name: str):
    return get_session().client(service_name)
