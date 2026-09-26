"""Append-only JSONL audit logger for every teardown attempt. No AWS credentials."""
import json
import os
from datetime import datetime, timezone

AUDIT_LOG_PATH = os.environ.get("AUDIT_LOG_PATH", "audit_log.jsonl")


def log_event(event: dict) -> None:
    record = {"logged_at": datetime.now(timezone.utc).isoformat(), **event}
    with open(AUDIT_LOG_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")
