import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rawmem.cli import main


class CliTests(unittest.TestCase):
    def test_capture_writes_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "events.jsonl"
            code = main(
                [
                    "capture",
                    "--ledger",
                    str(ledger),
                    "--source",
                    "unit",
                    "--type",
                    "note",
                    "--project",
                    "demo",
                    "--text",
                    "hello world",
                    "--tag",
                    "smoke",
                    "--field",
                    "status=ok",
                ]
            )
            self.assertEqual(code, 0)
            event = json.loads(ledger.read_text(encoding="utf-8").strip())
            self.assertEqual(event["source"], "unit")
            self.assertEqual(event["event_type"], "note")
            self.assertEqual(event["payload"]["status"], "ok")
            self.assertEqual(event["tags"], ["smoke"])

    def test_tail_prints_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "events.jsonl"
            self.assertEqual(
                main(["capture", "--ledger", str(ledger), "--source", "unit", "--type", "note", "--text", "tail me"]),
                0,
            )
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["tail", "--ledger", str(ledger), "--limit", "1"])
            self.assertEqual(code, 0)
            self.assertIn("unit/note", out.getvalue())
            self.assertIn("tail me", out.getvalue())

    def test_tail_zero_prints_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "events.jsonl"
            self.assertEqual(main(["capture", "--ledger", str(ledger), "--text", "hidden"]), 0)
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["tail", "--ledger", str(ledger), "--limit", "0"])
            self.assertEqual(code, 0)
            self.assertEqual(out.getvalue(), "")

    def test_run_records_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "events.jsonl"
            code = main(
                [
                    "run",
                    "--ledger",
                    str(ledger),
                    "--project",
                    "demo",
                    "--",
                    sys.executable,
                    "-c",
                    "print('ok')",
                ]
            )
            self.assertEqual(code, 0)
            event = json.loads(ledger.read_text(encoding="utf-8").strip())
            self.assertEqual(event["event_type"], "command_run")
            self.assertEqual(event["payload"]["exit_code"], 0)
            self.assertIn("ok", event["payload"]["stdout_tail"])

    def test_ingest_accepts_adapter_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "events.jsonl"
            payload = Path(tmp) / "payload.json"
            payload.write_text(
                json.dumps(
                    {
                        "source": "workbuddy",
                        "event_type": "task_done",
                        "project": "demo",
                        "raw_text": "adapter event",
                        "payload": {"status": "done"},
                    }
                ),
                encoding="utf-8",
            )
            code = main(["ingest", "--ledger", str(ledger), "--file", str(payload)])
            self.assertEqual(code, 0)
            event = json.loads(ledger.read_text(encoding="utf-8").strip())
            self.assertEqual(event["source"], "workbuddy")
            self.assertEqual(event["event_type"], "task_done")
            self.assertEqual(event["payload"]["status"], "done")

    def test_clip_can_capture_stdin_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "events.jsonl"
            old_stdin = sys.stdin
            try:
                sys.stdin = io.StringIO("selected text")
                code = main(["clip", "--ledger", str(ledger), "--stdin", "--url", "https://example.test"])
            finally:
                sys.stdin = old_stdin
            self.assertEqual(code, 0)
            event = json.loads(ledger.read_text(encoding="utf-8").strip())
            self.assertEqual(event["event_type"], "clipboard_clip")
            self.assertEqual(event["payload"]["url"], "https://example.test")
            self.assertEqual(event["raw_text"], "selected text")

    def test_setup_writes_project_files_and_git_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
            code = main(["setup", "--project-root", tmp, "--install-git-hooks"])
            self.assertEqual(code, 0)
            root = Path(tmp)
            self.assertTrue((root / ".rawmem" / "config.json").exists())
            self.assertTrue((root / ".rawmem" / "scripts" / "rawmem-powershell-profile.ps1").exists())
            self.assertTrue((root / ".rawmem" / "scripts" / "start-watch.ps1").exists())
            self.assertTrue((root / ".rawmem" / "scripts" / "browser-bookmarklet.txt").exists())
            hook = root / ".git" / "hooks" / "post-commit"
            self.assertTrue(hook.exists())
            self.assertIn("rawmem git hook", hook.read_text(encoding="utf-8"))

    def test_watch_once_records_baseline_and_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "events.jsonl"
            watched = root / "note.txt"
            watched.write_text("first", encoding="utf-8")
            self.assertEqual(main(["watch", "--root", str(root), "--ledger", str(ledger), "--once"]), 0)
            watched.write_text("second", encoding="utf-8")
            self.assertEqual(main(["watch", "--root", str(root), "--ledger", str(ledger), "--once"]), 0)
            events = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events[0]["event_type"], "watch_baseline")
            self.assertEqual(events[1]["event_type"], "file_change_batch")
            self.assertIn("note.txt", events[1]["payload"]["changes"]["modified"])

    def test_bookmarklet_prints_capture_endpoint(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["bookmarklet", "--endpoint", "http://127.0.0.1:9999/capture"])
        self.assertEqual(code, 0)
        self.assertIn("javascript:", out.getvalue())
        self.assertIn("127.0.0.1:9999/capture", out.getvalue())

    def test_verify_and_incremental_export_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "events.jsonl"
            cursor = Path(tmp) / "cursor.json"
            for text in ("one", "two"):
                self.assertEqual(
                    main(["capture", "--ledger", str(ledger), "--source", "unit", "--text", text]),
                    0,
                )
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["verify", "--ledger", str(ledger), "--json"]), 0)
            self.assertTrue(json.loads(out.getvalue())["valid"])

            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(
                    main(
                        [
                            "export",
                            "--ledger",
                            str(ledger),
                            "--cursor-file",
                            str(cursor),
                            "--limit",
                            "1",
                        ]
                    ),
                    0,
                )
            first = json.loads(out.getvalue())
            self.assertEqual(len(first["events"]), 1)
            self.assertTrue(cursor.exists())

            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(
                    main(["export", "--ledger", str(ledger), "--cursor-file", str(cursor)]),
                    0,
                )
            second = json.loads(out.getvalue())
            self.assertEqual([event["raw_text"] for event in second["events"]], ["two"])


if __name__ == "__main__":
    unittest.main()
