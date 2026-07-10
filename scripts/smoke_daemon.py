"""Controlled daemon smoke using fictional Claude, Codex, and Cursor transcripts."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rawmem.config import default_global_config, save_config
from rawmem.daemon import run_daemon
from rawmem.diagnostics import diagnostics_exit_code, run_diagnostics
from rawmem.ledger import read_events, verify_ledger


def copy_fixture(name: str, destination: Path) -> None:
    source = PROJECT_ROOT / "tests" / "fixtures" / "tailers" / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def run(root: Path) -> dict:
    home = root / "home"
    claude_root = root / "claude-projects"
    codex_root = root / "codex-sessions"
    cursor_root = root / "cursor-projects"
    ledger = home / "events.jsonl"
    copy_fixture("claude-code.jsonl", claude_root / "fictional" / "session.jsonl")
    copy_fixture("codex.jsonl", codex_root / "2026" / "07" / "10" / "rollout.jsonl")
    copy_fixture(
        "cursor.jsonl",
        cursor_root / "fictional" / "agent-transcripts" / "session.jsonl",
    )

    config = default_global_config()
    config["ledger"] = str(ledger)
    daemon = config["daemon"]
    daemon["serve"]["enabled"] = False
    daemon["watch"]["enabled"] = False
    daemon["tailers"]["claude_code"]["root"] = str(claude_root)
    daemon["tailers"]["codex"]["root"] = str(codex_root)
    daemon["tailers"]["cursor"]["root"] = str(cursor_root)
    daemon["tailers"]["powershell_history"]["enabled"] = False
    daemon["tailers"]["clipboard"]["enabled"] = False

    previous_home = os.environ.get("RAWMEM_HOME")
    os.environ["RAWMEM_HOME"] = str(home)
    try:
        save_config(home / "config.json", config)
        exit_code = run_daemon(once=True, serve=False, backfill=True, config=config)
        events = read_events(ledger)
        verification = verify_ledger(ledger)
        status = json.loads((home / "daemon-status.json").read_text(encoding="utf-8"))
        checks = run_diagnostics(config_path=home / "config.json", timeout=0.05)
    finally:
        if previous_home is None:
            os.environ.pop("RAWMEM_HOME", None)
        else:
            os.environ["RAWMEM_HOME"] = previous_home
    return {
        "schema_version": "rawmem.daemon_smoke.v1",
        "exit_code": exit_code,
        "event_count": len(events),
        "sources": sorted({event["source"] for event in events}),
        "event_types": sorted({event["event_type"] for event in events}),
        "verify_valid": verification.valid,
        "verify_event_count": verification.event_count,
        "source_coverage": status.get("source_coverage"),
        "tasks": status.get("tasks"),
        "doctor_exit_code": diagnostics_exit_code(checks),
        "doctor": [check.as_dict() for check in checks],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", help="Explicit smoke directory; it must not contain valuable data.")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    cleanup = False
    if args.root:
        root = Path(args.root).resolve()
        if root.exists() and any(root.iterdir()):
            parser.error(f"--root must be absent or empty: {root}")
        root.mkdir(parents=True, exist_ok=True)
    else:
        root = Path(tempfile.mkdtemp(prefix="rawmem-daemon-smoke-"))
        cleanup = not args.keep
    try:
        result = run(root)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        expected_sources = {"claude-code", "codex", "cursor"}
        return 0 if (
            result["exit_code"] == 0
            and result["verify_valid"]
            and result["doctor_exit_code"] == 0
            and set(result["sources"]) == expected_sources
            and result["event_count"] == 6
        ) else 1
    finally:
        if cleanup:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
