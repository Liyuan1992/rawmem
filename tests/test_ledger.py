import json
import multiprocessing
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rawmem.ledger import (
    append_event,
    build_event,
    file_artifact,
    iter_events,
    read_events,
    resolve_ledger_path,
    rotate_ledger,
    verify_ledger,
)


def _append_many(ledger_text: str, worker: int, count: int) -> None:
    ledger = Path(ledger_text)
    for index in range(count):
        append_event(
            ledger,
            build_event(
                source="process-test",
                event_type="concurrent",
                project="demo",
                raw_text=f"worker={worker} index={index}",
            ),
        )


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

    def test_append_uses_sidecar_last_hash_without_full_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "events.jsonl"
            append_event(ledger, build_event(source="test", event_type="note", raw_text="first"))
            with mock.patch("rawmem.ledger.read_events", side_effect=AssertionError("full read")):
                second = append_event(
                    ledger,
                    build_event(source="test", event_type="note", raw_text="second"),
                )
            self.assertIsNotNone(second["previous_hash"])
            self.assertTrue(Path(f"{ledger}.state.json").exists())

    def test_verify_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "events.jsonl"
            append_event(ledger, build_event(source="test", event_type="note", raw_text="first"))
            self.assertTrue(verify_ledger(ledger).valid)
            event = json.loads(ledger.read_text(encoding="utf-8"))
            event["raw_text"] = "tampered"
            ledger.write_text(json.dumps(event) + "\n", encoding="utf-8")
            result = verify_ledger(ledger)
            self.assertFalse(result.valid)
            self.assertTrue(any(error["code"] == "content_hash_mismatch" for error in result.errors))

    def test_incremental_cursor_resume_and_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "events.jsonl"
            for index in range(3):
                append_event(
                    ledger,
                    build_event(
                        source="codex" if index != 1 else "claude-code",
                        event_type="agent_user_turn",
                        project="demo",
                        raw_text=f"event {index}",
                    ),
                )
            first = iter_events(ledger, sources=["codex"], limit=1)
            self.assertEqual(len(first.events), 1)
            self.assertTrue(first.truncated)
            second = iter_events(ledger, after_cursor=first.next_cursor, sources=["codex"])
            self.assertEqual([event["raw_text"] for event in second.events], ["event 2"])
            self.assertEqual(second.next_cursor.byte_offset, ledger.stat().st_size)

            ledger.write_text("", encoding="utf-8")
            recovery = iter_events(ledger, after_cursor=second.next_cursor)
            self.assertEqual(recovery.cursor_status, "truncated")
            self.assertEqual(recovery.next_cursor.byte_offset, 0)

    def test_concurrent_process_appends_preserve_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "events.jsonl"
            processes = [
                multiprocessing.Process(target=_append_many, args=(str(ledger), worker, 20))
                for worker in range(4)
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join(20)
                self.assertEqual(process.exitcode, 0)
            result = verify_ledger(ledger)
            self.assertTrue(result.valid, result.errors)
            self.assertEqual(result.event_count, 80)

    def test_rotation_changes_identity_and_preserves_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "events.jsonl"
            append_event(ledger, build_event(source="test", event_type="note", raw_text="before"))
            cursor = iter_events(ledger).next_cursor
            archive = Path(tmp) / "archive.jsonl"
            result = rotate_ledger(ledger, destination=archive)
            self.assertTrue(archive.exists())
            self.assertNotEqual(result["archived_ledger_id"], result["new_ledger_id"])
            recovery = iter_events(ledger, after_cursor=cursor)
            self.assertEqual(recovery.cursor_status, "ledger_changed")
            self.assertTrue(verify_ledger(archive).valid)


if __name__ == "__main__":
    unittest.main()
