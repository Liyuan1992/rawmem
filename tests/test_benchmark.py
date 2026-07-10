import json
import subprocess
import sys
from pathlib import Path


def test_ledger_benchmark_smoke(tmp_path):
    root = Path(__file__).resolve().parents[1]
    ledger = tmp_path / "benchmark.jsonl"
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "benchmark_ledger.py"),
            "--ledger",
            str(ledger),
            "--events",
            "200",
            "--samples",
            "3",
            "--verify",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "rawmem.ledger_benchmark.v1"
    assert payload["verify_valid"] is True
    assert payload["verify_event_count"] == 203
    assert payload["cursor_batch_schema"] == "rawmem.event_batch.v1"
