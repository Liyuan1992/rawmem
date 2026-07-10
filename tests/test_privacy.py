from rawmem.ledger import build_event
from rawmem.privacy import CapturePolicy


def test_project_allowlist_rejects_other_projects():
    policy = CapturePolicy(project_allowlist=("allowed-*",))
    event = build_event(source="test", event_type="note", project="blocked", raw_text="hello")
    decision = policy.apply(event)
    assert decision.accepted is False
    assert decision.reason == "project_not_allowlisted"


def test_redaction_removes_common_secret_shapes():
    policy = CapturePolicy()
    event = build_event(
        source="test",
        event_type="note",
        project="allowed",
        raw_text="API_KEY=fake-value Bearer abcdefghijklmnopqrstuvwxyz",
        payload={"password": "password=hunter2"},
    )
    decision = policy.apply(event)
    assert decision.accepted is True
    text = str(decision.event)
    assert "fake-value" not in text
    assert "hunter2" not in text
    assert "abcdefghijklmnopqrstuvwxyz" not in text
    assert decision.redaction_count >= 3
    assert decision.event["payload"]["redaction"]["count"] == decision.redaction_count


def test_artifact_policy_keeps_references_not_embedded_content():
    policy = CapturePolicy(artifact_mode="references_only", artifact_max_size=10)
    event = build_event(
        source="test",
        event_type="artifact",
        project="allowed",
        artifacts=[
            {
                "kind": "file",
                "path": "fixtures/output.txt",
                "exists": True,
                "size": 20,
                "sha256": "abc",
                "content": "private embedded body",
            }
        ],
    )
    decision = policy.apply(event)
    artifact = decision.event["artifacts"][0]
    assert "content" not in artifact
    assert artifact["path"] == "fixtures/output.txt"
    assert artifact["policy_status"] == "oversize_reference"
