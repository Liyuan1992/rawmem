import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rawmem.ledger import append_event, build_event, file_artifact, read_events, resolve_ledger_path


class LedgerTests(unittest.TestCase):
    def test_append_event_links_previous_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "events.jsonl"
            first = append_event(
                ledger,
                build_event(source="test", event_type="note", project="demo", raw_text="first"),
            )
            second = append_event(
                ledger,
                build_event(source="test", event_type="note", project="demo", raw_text="second"),
            )

            events = read_events(ledger)
            self.assertEqual(len(events), 2)
            self.assertIsNone(first["previous_hash"])
            self.assertEqual(second["previous_hash"], first["content_hash"])
            self.assertEqual(events[1]["previous_hash"], events[0]["content_hash"])

    def test_file_artifact_records_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_text("hello", encoding="utf-8")
            artifact = file_artifact(path)
            self.assertTrue(artifact["exists"])
            self.assertEqual(artifact["size"], 5)
            self.assertEqual(len(artifact["sha256"]), 64)

    def test_resolve_ledger_path_can_be_local(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = resolve_ledger_path(local=True, cwd=tmp)
            self.assertEqual(path, Path(tmp) / ".rawmem" / "events.jsonl")

    def test_json_lines_are_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "events.jsonl"
            append_event(ledger, build_event(source="test", event_type="note", project="demo", raw_text="hello"))
            line = ledger.read_text(encoding="utf-8").strip()
            loaded = json.loads(line)
            self.assertEqual(loaded["schema"], "rawmem.event.v1")


if __name__ == "__main__":
    unittest.main()
