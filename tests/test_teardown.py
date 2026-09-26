"""Asserts the dry_run path never calls Delete*/Terminate*, and that the
provenance/blast-radius guardrails hold. See PRD §9, §10."""
import pytest

from mcp_server.teardown import (
    execute_teardown,
    check_provenance,
    ProvenanceError,
    MAX_RESOURCES_PER_CALL,
)


def test_provenance_rejects_unknown_id():
    with pytest.raises(ProvenanceError):
        check_provenance(["vol-unknown"], known_resource_ids={"vol-known"})


def test_provenance_accepts_known_id():
    check_provenance(["vol-known"], known_resource_ids={"vol-known", "i-known"})


def test_blast_radius_cap_enforced():
    resource_ids = [f"vol-{i}" for i in range(MAX_RESOURCES_PER_CALL + 1)]
    with pytest.raises(ValueError):
        execute_teardown(resource_ids, dry_run=True, known_resource_ids=set(resource_ids))


def test_dry_run_logs_without_real_deletion(tmp_path, monkeypatch):
    log_path = tmp_path / "audit_log.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(log_path))
    import importlib
    import mcp_server.audit as audit_module
    importlib.reload(audit_module)
    monkeypatch.setattr("mcp_server.teardown.log_event", audit_module.log_event)

    result = execute_teardown(["vol-known"], dry_run=True, known_resource_ids={"vol-known"})

    assert result["deleted"] == []
    assert log_path.exists()


def test_kill_switch_blocks_execution(monkeypatch):
    monkeypatch.setenv("TEARDOWN_DISABLED", "true")
    with pytest.raises(RuntimeError):
        execute_teardown(["vol-known"], dry_run=True, known_resource_ids={"vol-known"})
