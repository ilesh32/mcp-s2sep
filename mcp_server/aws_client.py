"""boto3 session/client factory, scoped to one region and the demo tag."""
import os
from pathlib import Path

import boto3
from botocore.config import Config
from dotenv import load_dotenv

from mcp_server.debug import attach_aws_logging

# Load AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_REGION from the
# project-root .env. Real environment variables win (override=False), so
# Docker --env-file and orchestrator secrets keep working.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

DEMO_TAG_KEY = "hackathon-demo"
DEMO_TAG_VALUE = "true"


def get_region() -> str:
    region = os.environ.get("AWS_REGION")
    if not region:
        raise RuntimeError("AWS_REGION must be set (single-region scope per PRD §2)")
    return region


def get_session() -> boto3.Session:
    return boto3.Session(region_name=get_region())


# Bounded timeouts and retries so a slow/throttled AWS API can't hang a tool call.
_BOTO_CONFIG = Config(
    connect_timeout=5,
    read_timeout=30,
    retries={"max_attempts": 5, "mode": "standard"},
)


def get_client(service_name: str):
    client = get_session().client(service_name, config=_BOTO_CONFIG)
    attach_aws_logging(client)
    return client
