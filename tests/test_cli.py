import io
import json
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


if __name__ == "__main__":
    unittest.main()
