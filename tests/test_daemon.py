from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from rawmem.config import default_global_config, deep_merge, load_global_config
from rawmem.daemon import run_daemon
from rawmem.ledger import read_events


class ConfigTests(unittest.TestCase):
    def test_deep_merge_overrides_nested_keys(self) -> None:
        merged = deep_merge(
            default_global_config(),
            {"daemon": {"serve": {"port": 9999}, "tailers": {"clipboard": {"enabled": False}}}},
        )
        self.assertEqual(merged["daemon"]["serve"]["port"], 9999)
        self.assertEqual(merged["daemon"]["serve"]["host"], "127.0.0.1")
        self.assertFalse(merged["daemon"]["tailers"]["clipboard"]["enabled"])
        self.assertTrue(merged["daemon"]["tailers"]["claude_code"]["enabled"])

    def test_load_global_config_without_file_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_global_config(Path(tmp) / "missing.json")
            self.assertEqual(config["schema"], "rawmem.config.v2")


class DaemonOnceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._old_home = os.environ.get("RAWMEM_HOME")
        os.environ["RAWMEM_HOME"] = str(self.home)

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("RAWMEM_HOME", None)
        else:
            os.environ["RAWMEM_HOME"] = self._old_home
        self._tmp.cleanup()

    def _config(self, claude_root: Path) -> dict:
        config = default_global_config()
        daemon = config["daemon"]
        daemon["serve"]["enabled"] = False
        daemon["tailers"]["claude_code"]["root"] = str(claude_root)
        daemon["tailers"]["codex"]["enabled"] = False
        daemon["tailers"]["powershell_history"]["enabled"] = False
        daemon["tailers"]["clipboard"]["enabled"] = False
        return config

    def test_once_baselines_then_captures_new_turns(self) -> None:
        claude_root = self.home / "claude-projects"
        project = claude_root / "demo"
        project.mkdir(parents=True)
        transcript = project / "s.jsonl"
        line = {
            "type": "user",
            "message": {"role": "user", "content": "old"},
            "cwd": str(project),
            "sessionId": "s1",
            "timestamp": "t",
        }
        transcript.write_text(json.dumps(line) + "\n", encoding="utf-8")
        config = self._config(claude_root)

        self.assertEqual(run_daemon(once=True, serve=False, config=config), 0)
        ledger = self.home / "events.jsonl"
        self.assertEqual(read_events(ledger), [])

        line["message"]["content"] = "new turn after baseline"
        with transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")
        self.assertEqual(run_daemon(once=True, serve=False, config=config), 0)
        events = read_events(ledger)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["raw_text"], "new turn after baseline")
        self.assertEqual(events[0]["source"], "claude-code")

        status = json.loads((self.home / "daemon-status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["ledger"], str(ledger))
        self.assertTrue(any(task["name"] == "claude-code" for task in status["tasks"]))

    def test_watch_task_records_changes(self) -> None:
        claude_root = self.home / "claude-projects"
        claude_root.mkdir(parents=True)
        watch_root = self.home / "workspace"
        watch_root.mkdir()
        (watch_root / "a.txt").write_text("hello", encoding="utf-8")
        config = self._config(claude_root)
        config["daemon"]["watch"] = {
            "enabled": True,
            "roots": [str(watch_root)],
            "interval_seconds": 1,
            "ignore_globs": [],
        }
        self.assertEqual(run_daemon(once=True, serve=False, config=config), 0)
        events = read_events(self.home / "events.jsonl")
        self.assertTrue(any(event["event_type"] == "watch_baseline" for event in events))


if __name__ == "__main__":
    unittest.main()
