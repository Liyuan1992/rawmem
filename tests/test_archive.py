import io
import inspect
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rawmem.archive import (
    LedgerNotSealableError,
    iter_archive_events,
    list_archives,
    seal_ledger,
    verify_sealed_archive,
)
from rawmem.archive_format import (
    SealedArchiveError,
    archive_breakpoint_list_path,
    archive_manifest_path,
    archive_registry_path,
    archive_verify_report_path,
    sha256_file,
)
from rawmem.cli import main
from rawmem.ledger import (
    append_event,
    build_event,
    content_hash,
    iter_events,
    ledger_lock_path,
    ledger_state_path,
    read_events,
)
from rawmem.verification import verify_ledger


def _append_events(ledger: Path, count: int) -> None:
    for index in range(count):
        append_event(
            ledger,
            build_event(
                source="archive-test",
                event_type="synthetic",
                project="demo",
                raw_text=f"private synthetic body {index}",
                summary=f"synthetic summary {index}",
            ),
        )


def _introduce_previous_hash_breaks(ledger: Path, indexes: set[int]) -> None:
    events = [
        json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()
    ]
    previous: str | None = None
    for index, event in enumerate(events):
        event["previous_hash"] = f"{index + 1:064x}" if index in indexes else previous
        event["content_hash"] = content_hash(event)
        previous = event["content_hash"]
    ledger.write_text(
        "".join(
            json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
            for event in events
        ),
        encoding="utf-8",
        newline="\n",
    )


class ArchiveTests(unittest.TestCase):
    def test_archive_contract_fixture_matches_public_shapes_and_signature(self) -> None:
        fixture_path = Path(__file__).parent / "fixtures" / "archive_contract_v1.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "events.jsonl"
            archive = root / "archives" / "contract.jsonl"
            _append_events(ledger, 1)
            seal_ledger(ledger, destination=archive)
            manifest = json.loads(
                archive_manifest_path(archive).read_text(encoding="utf-8")
            )
            registry = list_archives(ledger)

        self.assertEqual(
            set(manifest),
            set(fixture["manifest"]["required_top_level_fields"]),
        )
        self.assertEqual(
            set(manifest["archive"]),
            set(fixture["manifest"]["required_archive_fields"]),
        )
        self.assertEqual(
            set(registry), set(fixture["registry"]["required_top_level_fields"])
        )
        self.assertEqual(
            set(registry["archives"][0]),
            set(fixture["registry"]["required_entry_fields"]),
        )
        signature = inspect.signature(iter_archive_events)
        self.assertEqual(
            list(signature.parameters), fixture["iter_archive_events"]["parameters"]
        )
        self.assertEqual(
            signature.parameters["max_bytes"].default,
            fixture["iter_archive_events"]["default_max_bytes"],
        )
        self.assertEqual(
            signature.parameters["projection"].default,
            fixture["iter_archive_events"]["default_projection"],
        )

    def test_verify_is_pure_read_only_without_lock_or_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "events.jsonl"
            event = build_event(source="test", event_type="note", raw_text="synthetic")
            event["previous_hash"] = None
            event["content_hash"] = content_hash(event)
            ledger.write_text(
                json.dumps(event, sort_keys=True) + "\n", encoding="utf-8"
            )
            before_names = sorted(path.name for path in root.iterdir())
            before_stat = ledger.stat()

            result = verify_ledger(ledger)

            self.assertTrue(result.valid, result.errors)
            self.assertEqual(sorted(path.name for path in root.iterdir()), before_names)
            self.assertFalse(ledger_lock_path(ledger).exists())
            self.assertFalse(ledger_state_path(ledger).exists())
            after_stat = ledger.stat()
            self.assertEqual(
                (after_stat.st_size, after_stat.st_mtime_ns),
                (before_stat.st_size, before_stat.st_mtime_ns),
            )

    def test_verify_does_not_refresh_existing_lock_or_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "events.jsonl"
            _append_events(ledger, 1)
            lock = ledger_lock_path(ledger)
            state = ledger_state_path(ledger)
            before = {
                lock: (lock.read_bytes(), lock.stat().st_mtime_ns),
                state: (state.read_bytes(), state.stat().st_mtime_ns),
            }

            self.assertTrue(verify_ledger(ledger).valid)

            for path, (payload, mtime_ns) in before.items():
                self.assertEqual(path.read_bytes(), payload)
                self.assertEqual(path.stat().st_mtime_ns, mtime_ns)

    def test_seal_preserves_bytes_starts_transition_and_refuses_archive_append(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "events.jsonl"
            archive = root / "archives" / "old.jsonl"
            _append_events(ledger, 2)
            source_bytes = ledger.read_bytes()

            result = seal_ledger(ledger, destination=archive)

            self.assertEqual(archive.read_bytes(), source_bytes)
            self.assertEqual(sha256_file(archive), result["archived_sha256"])
            self.assertTrue(result["archive_bytes_unchanged"])
            self.assertTrue(archive_manifest_path(archive).exists())
            self.assertTrue(archive_verify_report_path(archive).exists())
            self.assertTrue(archive_breakpoint_list_path(archive).exists())
            self.assertFalse(ledger_lock_path(archive).exists())
            transition = read_events(ledger)[0]
            self.assertEqual(transition["event_type"], "ledger_transition")
            self.assertIsNone(transition["previous_hash"])
            self.assertEqual(
                transition["payload"]["archive_manifest_ref"]["sha256"],
                result["archive_manifest_sha256"],
            )
            self.assertTrue(verify_ledger(ledger).valid)
            self.assertTrue(verify_sealed_archive(archive)["valid"])
            with self.assertRaises(SealedArchiveError):
                append_event(
                    archive, build_event(source="test", event_type="forbidden")
                )
            with self.assertRaises(SealedArchiveError):
                iter_events(archive)
            self.assertFalse(ledger_lock_path(archive).exists())

            appended = append_event(
                ledger, build_event(source="test", event_type="after-transition")
            )
            self.assertEqual(appended["previous_hash"], transition["content_hash"])
            self.assertTrue(verify_ledger(ledger).valid)

            registry = list_archives(ledger)
            self.assertTrue(registry["derived"])
            self.assertEqual(registry["authority"], "per_archive_manifest")
            self.assertEqual(
                registry["archives"][0]["archive_id"], result["archive_id"]
            )
            if os.name == "nt":
                readonly = getattr(stat, "FILE_ATTRIBUTE_READONLY", 1)
                self.assertTrue(archive.stat().st_file_attributes & readonly)

    def test_seal_accepts_only_recorded_previous_hash_breaks_and_archive_query_continues(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "events.jsonl"
            archive = root / "archives" / "history.jsonl"
            _append_events(ledger, 7)
            _introduce_previous_hash_breaks(ledger, {1, 4, 6})
            before = verify_ledger(ledger)
            self.assertEqual(
                [item["code"] for item in before.errors], ["previous_hash_mismatch"] * 3
            )

            result = seal_ledger(ledger, destination=archive)

            self.assertEqual(result["breakpoint_count"], 3)
            status = verify_sealed_archive(archive)
            self.assertTrue(status["valid"], status)
            metadata = iter_archive_events(archive, max_bytes=1024 * 1024)
            self.assertEqual(metadata.cursor_status, "ok")
            self.assertEqual(metadata.chain_status, "partial")
            self.assertEqual(len(metadata.events), 7)
            self.assertEqual(len(metadata.integrity_warnings), 3)
            self.assertNotIn("raw_text", metadata.events[0])
            self.assertNotIn("summary", metadata.events[0])
            self.assertNotIn("payload", metadata.events[0])

            summary = iter_archive_events(
                archive, projection="summary", max_bytes=1024 * 1024
            )
            self.assertIn("summary", summary.events[0])
            self.assertNotIn("raw_text", summary.events[0])
            full = iter_archive_events(archive, projection="full", limit=1)
            self.assertIn("raw_text", full.events[0])

    def test_non_breakpoint_integrity_error_fails_closed_without_moving_source(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "events.jsonl"
            archive = root / "archives" / "rejected.jsonl"
            _append_events(ledger, 2)
            event_lines = ledger.read_text(encoding="utf-8").splitlines()
            event = json.loads(event_lines[0])
            event["raw_text"] = "changed without rehash"
            event_lines[0] = json.dumps(event, sort_keys=True)
            ledger.write_text("\n".join(event_lines) + "\n", encoding="utf-8")
            source_bytes = ledger.read_bytes()
            state_bytes = ledger_state_path(ledger).read_bytes()

            with self.assertRaises(LedgerNotSealableError):
                seal_ledger(ledger, destination=archive)

            self.assertEqual(ledger.read_bytes(), source_bytes)
            self.assertEqual(ledger_state_path(ledger).read_bytes(), state_bytes)
            self.assertFalse(archive.exists())
            self.assertFalse(archive_manifest_path(archive).exists())
            self.assertFalse(archive_registry_path(ledger).exists())

    def test_json_schema_content_hash_and_duplicate_errors_all_fail_closed(
        self,
    ) -> None:
        mutators: dict[str, object] = {
            "invalid_json": lambda ledger, events: ledger.write_bytes(
                ledger.read_bytes() + b"{invalid\n"
            ),
            "schema_mismatch": lambda ledger, events: self._rewrite_first_event(
                ledger, events, {"schema": "rawmem.event.invalid"}, rehash=True
            ),
            "content_hash_mismatch": lambda ledger, events: self._rewrite_first_event(
                ledger, events, {"raw_text": "changed without hash"}, rehash=False
            ),
            "duplicate_event_id": lambda ledger, events: self._rewrite_second_event_id(
                ledger, events
            ),
        }
        for expected_code, mutator in mutators.items():
            with (
                self.subTest(expected_code=expected_code),
                tempfile.TemporaryDirectory() as tmp,
            ):
                root = Path(tmp)
                ledger = root / "events.jsonl"
                archive = root / "archives" / f"{expected_code}.jsonl"
                _append_events(ledger, 2)
                events = [
                    json.loads(line)
                    for line in ledger.read_text(encoding="utf-8").splitlines()
                ]
                mutator(ledger, events)  # type: ignore[operator]
                source_bytes = ledger.read_bytes()
                result = verify_ledger(ledger)
                self.assertIn(expected_code, {error["code"] for error in result.errors})

                with self.assertRaises(LedgerNotSealableError):
                    seal_ledger(ledger, destination=archive)

                self.assertEqual(ledger.read_bytes(), source_bytes)
                self.assertFalse(archive.exists())
                self.assertFalse(archive_manifest_path(archive).exists())

    @staticmethod
    def _write_event_list(ledger: Path, events: list[dict[str, object]]) -> None:
        ledger.write_text(
            "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
            encoding="utf-8",
            newline="\n",
        )

    def _rewrite_first_event(
        self,
        ledger: Path,
        events: list[dict[str, object]],
        updates: dict[str, object],
        *,
        rehash: bool,
    ) -> None:
        events[0].update(updates)
        if rehash:
            events[0]["content_hash"] = content_hash(events[0])
            events[1]["previous_hash"] = events[0]["content_hash"]
            events[1]["content_hash"] = content_hash(events[1])
        self._write_event_list(ledger, events)

    def _rewrite_second_event_id(
        self, ledger: Path, events: list[dict[str, object]]
    ) -> None:
        events[1]["event_id"] = events[0]["event_id"]
        events[1]["content_hash"] = content_hash(events[1])
        self._write_event_list(ledger, events)

    def test_mid_commit_failure_rolls_back_ledger_state_metadata_and_registry(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "events.jsonl"
            archive = root / "archives" / "rollback.jsonl"
            _append_events(ledger, 2)
            source_bytes = ledger.read_bytes()
            state_bytes = ledger_state_path(ledger).read_bytes()
            original_replace = os.replace
            failed = False

            def fail_new_active(
                source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
            ) -> None:
                nonlocal failed
                source_path = Path(source)
                destination_path = Path(destination)
                if (
                    not failed
                    and source_path.name == "new-events.jsonl"
                    and destination_path == ledger
                ):
                    failed = True
                    raise OSError("synthetic commit failure")
                original_replace(source, destination)

            with mock.patch("rawmem.archive.os.replace", side_effect=fail_new_active):
                with self.assertRaisesRegex(OSError, "synthetic commit failure"):
                    seal_ledger(ledger, destination=archive)

            self.assertEqual(ledger.read_bytes(), source_bytes)
            self.assertEqual(ledger_state_path(ledger).read_bytes(), state_bytes)
            self.assertFalse(archive.exists())
            self.assertFalse(archive_manifest_path(archive).exists())
            self.assertFalse(archive_verify_report_path(archive).exists())
            self.assertFalse(archive_breakpoint_list_path(archive).exists())
            self.assertFalse(archive_registry_path(ledger).exists())

    def test_second_seal_failure_restores_existing_registry_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "events.jsonl"
            first_archive = root / "archives" / "first.jsonl"
            second_archive = root / "archives" / "second.jsonl"
            _append_events(ledger, 1)
            seal_ledger(ledger, destination=first_archive)
            append_event(ledger, build_event(source="test", event_type="between-seals"))
            source_bytes = ledger.read_bytes()
            state_bytes = ledger_state_path(ledger).read_bytes()
            registry_path = archive_registry_path(ledger)
            registry_bytes = registry_path.read_bytes()
            original_replace = os.replace
            failed = False

            def fail_new_active(source: object, destination: object) -> None:
                nonlocal failed
                source_path = Path(source)  # type: ignore[arg-type]
                destination_path = Path(destination)  # type: ignore[arg-type]
                if (
                    not failed
                    and source_path.name == "new-events.jsonl"
                    and destination_path == ledger
                ):
                    failed = True
                    raise OSError("synthetic second-seal failure")
                original_replace(source, destination)  # type: ignore[arg-type]

            with mock.patch("rawmem.archive.os.replace", side_effect=fail_new_active):
                with self.assertRaisesRegex(OSError, "synthetic second-seal failure"):
                    seal_ledger(ledger, destination=second_archive)

            self.assertEqual(ledger.read_bytes(), source_bytes)
            self.assertEqual(ledger_state_path(ledger).read_bytes(), state_bytes)
            self.assertEqual(registry_path.read_bytes(), registry_bytes)
            self.assertTrue(verify_sealed_archive(first_archive)["valid"])
            self.assertFalse(second_archive.exists())

    def test_append_waits_for_seal_and_lands_after_transition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "events.jsonl"
            archive = root / "archives" / "concurrent.jsonl"
            _append_events(ledger, 1)
            entered = threading.Event()
            release = threading.Event()
            errors: list[BaseException] = []
            original_verify = verify_ledger
            first_call = True

            def blocking_verify(path: str | Path, **kwargs: object):
                nonlocal first_call
                result = original_verify(path, **kwargs)
                if first_call:
                    first_call = False
                    entered.set()
                    if not release.wait(10):
                        raise TimeoutError("test did not release seal verification")
                return result

            def run_seal() -> None:
                try:
                    seal_ledger(ledger, destination=archive)
                except BaseException as exc:  # pragma: no cover - surfaced below
                    errors.append(exc)

            def run_append() -> None:
                try:
                    append_event(
                        ledger,
                        build_event(source="concurrent", event_type="after-seal"),
                    )
                except BaseException as exc:  # pragma: no cover - surfaced below
                    errors.append(exc)

            with mock.patch(
                "rawmem.archive.verify_ledger", side_effect=blocking_verify
            ):
                seal_thread = threading.Thread(target=run_seal)
                seal_thread.start()
                self.assertTrue(entered.wait(10))
                append_thread = threading.Thread(target=run_append)
                append_thread.start()
                time.sleep(0.05)
                self.assertTrue(append_thread.is_alive())
                release.set()
                seal_thread.join(15)
                append_thread.join(15)

            self.assertFalse(errors, errors)
            self.assertFalse(seal_thread.is_alive())
            self.assertFalse(append_thread.is_alive())
            self.assertEqual(verify_ledger(archive).event_count, 1)
            active_events = read_events(ledger)
            self.assertEqual(
                [event["event_type"] for event in active_events],
                ["ledger_transition", "after-seal"],
            )
            self.assertTrue(verify_ledger(ledger).valid)

    def test_archive_registry_missing_is_read_only_and_cli_archive_defaults_to_metadata(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "events.jsonl"
            archive = root / "archives" / "cli.jsonl"
            registry_path = archive_registry_path(ledger)
            self.assertEqual(list_archives(ledger)["archives"], [])
            self.assertFalse(registry_path.exists())
            _append_events(ledger, 1)

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    main(
                        [
                            "seal",
                            "--ledger",
                            str(ledger),
                            "--destination",
                            str(archive),
                            "--yes",
                            "--json",
                        ]
                    ),
                    0,
                )
            self.assertEqual(json.loads(output.getvalue())["breakpoint_count"], 0)

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["export", "--archive", str(archive)]), 0)
            exported = json.loads(output.getvalue())
            self.assertNotIn("raw_text", exported["events"][0])
            self.assertNotIn("summary", exported["events"][0])

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    main(["verify", "--archive", str(archive), "--json"]), 0
                )
            self.assertTrue(json.loads(output.getvalue())["valid"])

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    main(["archives", "--ledger", str(ledger), "--json"]), 0
                )
            self.assertEqual(len(json.loads(output.getvalue())["archives"]), 1)

    def test_archive_query_and_verify_do_not_touch_archive_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "events.jsonl"
            archive = root / "archives" / "readonly-query.jsonl"
            _append_events(ledger, 2)
            seal_ledger(ledger, destination=archive)
            paths = [
                archive,
                ledger_state_path(archive),
                archive_manifest_path(archive),
                archive_verify_report_path(archive),
                archive_breakpoint_list_path(archive),
            ]
            before = {
                path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths
            }

            self.assertEqual(len(iter_archive_events(archive).events), 2)
            self.assertTrue(verify_sealed_archive(archive)["valid"])

            self.assertFalse(ledger_lock_path(archive).exists())
            for path, (payload, mtime_ns) in before.items():
                self.assertEqual(path.read_bytes(), payload)
                self.assertEqual(path.stat().st_mtime_ns, mtime_ns)

    def test_metadata_query_rejects_same_size_tampering_beyond_requested_limit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "events.jsonl"
            archive = root / "archives" / "same-size-tamper.jsonl"
            _append_events(ledger, 4)
            seal_ledger(ledger, destination=archive)
            if os.name == "nt":
                os.chmod(archive, stat.S_IREAD | stat.S_IWRITE)
            payload = bytearray(archive.read_bytes())
            tail_index = len(payload) - 8
            payload[tail_index] = (
                ord("0") if payload[tail_index] != ord("0") else ord("1")
            )
            archive.write_bytes(payload)

            with self.assertRaisesRegex(SealedArchiveError, "SHA-256"):
                iter_archive_events(
                    archive, projection="metadata", limit=1, max_bytes=1024
                )


if __name__ == "__main__":
    unittest.main()
