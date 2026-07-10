from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rawmem.clipboard import ClipboardTailer
from rawmem.tailers import (
    ClaudeCodeTailer,
    CodexTailer,
    CursorTailer,
    PowerShellHistoryTailer,
    TailState,
    join_continuations,
    read_new_lines,
)

FIXTURES = Path(__file__).parent / "fixtures" / "tailers"


def claude_line(kind: str, text, *, sidechain: bool = False, meta: bool = False) -> str:
    return json.dumps(
        {
            "type": kind,
            "isSidechain": sidechain,
            "isMeta": meta,
            "message": {"role": kind, "content": text},
            "cwd": "D:\\Dev\\Projects\\demo",
            "sessionId": "sess-1",
            "uuid": "uuid-1",
            "timestamp": "2026-07-09T00:00:00.000Z",
            "gitBranch": "main",
        }
    )


class TailStateTests(unittest.TestCase):
    def test_read_new_lines_advances_and_handles_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "log.txt"
            target.write_text("one\ntwo\n", encoding="utf-8")
            entry = {"offset": 0}
            self.assertEqual(read_new_lines(target, entry), ["one", "two"])
            self.assertEqual(read_new_lines(target, entry), [])
            with target.open("a", encoding="utf-8") as handle:
                handle.write("three\npartial")
            self.assertEqual(read_new_lines(target, entry), ["three"])
            # Truncated file resets the offset instead of skipping forever.
            target.write_text("new\n", encoding="utf-8")
            self.assertEqual(read_new_lines(target, entry), ["new"])


class ClaudeCodeTailerTests(unittest.TestCase):
    def test_baseline_then_incremental(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            project = root / "D--Dev-Projects-demo"
            project.mkdir(parents=True)
            transcript = project / "session.jsonl"
            transcript.write_text(claude_line("user", "old question") + "\n", encoding="utf-8")

            state = TailState(Path(tmp) / "state.json")
            tailer = ClaudeCodeTailer(root=root)
            self.assertEqual(tailer.poll(state), [])  # baseline skips history

            with transcript.open("a", encoding="utf-8") as handle:
                handle.write(claude_line("user", "make the icon 2px bigger") + "\n")
                handle.write(claude_line("assistant", [{"type": "text", "text": "done"}]) + "\n")
                handle.write(claude_line("assistant", [{"type": "tool_use", "name": "Bash"}]) + "\n")
                handle.write(claude_line("user", "sidechain text", sidechain=True) + "\n")
                handle.write(json.dumps({"type": "queue-operation"}) + "\n")
            events = tailer.poll(state)
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0]["event_type"], "agent_user_turn")
            self.assertEqual(events[0]["raw_text"], "make the icon 2px bigger")
            self.assertEqual(events[0]["project"], "demo")
            self.assertEqual(events[1]["event_type"], "agent_assistant_turn")
            self.assertEqual(tailer.poll(state), [])

    def test_backfill_ingests_existing_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            project = root / "demo"
            project.mkdir(parents=True)
            (project / "s.jsonl").write_text(claude_line("user", "history") + "\n", encoding="utf-8")
            state = TailState(Path(tmp) / "state.json")
            events = ClaudeCodeTailer(root=root, backfill=True).poll(state)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["raw_text"], "history")

    def test_truncates_long_turns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            (root / "demo").mkdir(parents=True)
            (root / "demo" / "s.jsonl").write_text(
                claude_line("user", "x" * 500) + "\n", encoding="utf-8"
            )
            state = TailState(Path(tmp) / "state.json")
            events = ClaudeCodeTailer(root=root, backfill=True, max_chars=100).poll(state)
            self.assertEqual(len(events[0]["raw_text"]), 100)
            self.assertTrue(events[0]["payload"]["truncated"])


class CodexTailerTests(unittest.TestCase):
    def test_parses_event_msgs_and_session_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            day = root / "2026" / "07" / "09"
            day.mkdir(parents=True)
            rollout = day / "rollout-1.jsonl"
            lines = [
                json.dumps(
                    {
                        "timestamp": "t0",
                        "type": "session_meta",
                        "payload": {"id": "sess-9", "cwd": "D:\\Dev\\Projects\\nexus"},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "t1",
                        "type": "event_msg",
                        "payload": {"type": "user_message", "message": "fix the empty button"},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "t2",
                        "type": "event_msg",
                        "payload": {"type": "agent_message", "message": "fixed"},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "t3",
                        "type": "response_item",
                        "payload": {"type": "function_call", "name": "shell"},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "t4",
                        "type": "event_msg",
                        "payload": {"type": "token_count", "info": {}},
                    }
                ),
            ]
            rollout.write_text("\n".join(lines) + "\n", encoding="utf-8")
            state = TailState(Path(tmp) / "state.json")
            events = CodexTailer(root=root, backfill=True).poll(state)
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0]["event_type"], "agent_user_turn")
            self.assertEqual(events[0]["project"], "nexus")
            self.assertEqual(events[0]["payload"]["session_id"], "sess-9")
            self.assertEqual(events[1]["event_type"], "agent_assistant_turn")

    def test_baseline_still_primes_session_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            root.mkdir(parents=True)
            rollout = root / "rollout-1.jsonl"
            rollout.write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {"id": "sess-2", "cwd": "D:\\Dev\\Projects\\alpha"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            state = TailState(Path(tmp) / "state.json")
            tailer = CodexTailer(root=root)
            self.assertEqual(tailer.poll(state), [])
            with rollout.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "type": "event_msg",
                            "payload": {"type": "user_message", "message": "later turn"},
                        }
                    )
                    + "\n"
                )
            events = tailer.poll(state)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["project"], "alpha")


class CursorTailerTests(unittest.TestCase):
    def test_parses_cursor_agent_transcripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            transcript_dir = root / "fictional-project" / "agent-transcripts" / "session"
            transcript_dir.mkdir(parents=True)
            transcript = transcript_dir / "cursor-fixture.jsonl"
            transcript.write_text(
                (FIXTURES / "cursor.jsonl").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            state = TailState(Path(tmp) / "state.json")
            events = CursorTailer(root=root, backfill=True).poll(state)
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0]["source"], "cursor")
            self.assertEqual(events[0]["project"], "fictional-project")
            self.assertEqual(events[0]["event_type"], "agent_user_turn")
            self.assertEqual(events[0]["payload"]["session_id"], "cursor-fixture")
            self.assertEqual(events[1]["event_type"], "agent_assistant_turn")

    def test_baseline_then_incremental(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            transcript_dir = root / "fictional" / "agent-transcripts"
            transcript_dir.mkdir(parents=True)
            transcript = transcript_dir / "session.jsonl"
            transcript.write_text(
                json.dumps({"role": "user", "message": {"content": "old"}}) + "\n",
                encoding="utf-8",
            )
            state = TailState(Path(tmp) / "state.json")
            tailer = CursorTailer(root=root)
            self.assertEqual(tailer.poll(state), [])
            with transcript.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps({"role": "user", "message": {"content": "new"}}) + "\n"
                )
            events = tailer.poll(state)
            self.assertEqual([event["raw_text"] for event in events], ["new"])


class AgentTailerParityTests(unittest.TestCase):
    def test_claude_codex_cursor_share_the_event_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            claude_root = base / "claude"
            (claude_root / "fictional").mkdir(parents=True)
            (claude_root / "fictional" / "session.jsonl").write_text(
                (FIXTURES / "claude-code.jsonl").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            codex_root = base / "codex"
            codex_root.mkdir()
            (codex_root / "rollout.jsonl").write_text(
                (FIXTURES / "codex.jsonl").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            cursor_root = base / "cursor"
            cursor_dir = cursor_root / "fictional" / "agent-transcripts"
            cursor_dir.mkdir(parents=True)
            (cursor_dir / "session.jsonl").write_text(
                (FIXTURES / "cursor.jsonl").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            cases = [
                ClaudeCodeTailer(root=claude_root, backfill=True),
                CodexTailer(root=codex_root, backfill=True),
                CursorTailer(root=cursor_root, backfill=True),
            ]
            for index, tailer in enumerate(cases):
                events = tailer.poll(TailState(base / f"state-{index}.json"))
                self.assertEqual([event["event_type"] for event in events], [
                    "agent_user_turn",
                    "agent_assistant_turn",
                ])
                for event in events:
                    self.assertEqual(event["schema"], "rawmem.event.v1")
                    self.assertTrue(event["payload"].get("session_id"))
                    self.assertIn("transcript", event["payload"])
                    self.assertIn("truncated", event["payload"])


class PowerShellHistoryTailerTests(unittest.TestCase):
    def test_joins_continuations_and_filters_rawmem(self) -> None:
        self.assertEqual(join_continuations(["git st`", "atus"]), ["git st\natus"])
        with tempfile.TemporaryDirectory() as tmp:
            history = Path(tmp) / "history.txt"
            history.write_text("old command\n", encoding="utf-8")
            state = TailState(Path(tmp) / "state.json")
            tailer = PowerShellHistoryTailer(path=history)
            self.assertEqual(tailer.poll(state), [])
            with history.open("a", encoding="utf-8") as handle:
                handle.write("git status\nrawmem tail --limit 5\npython -m pytest\n")
            events = tailer.poll(state)
            self.assertEqual([event["raw_text"] for event in events], ["git status", "python -m pytest"])
            self.assertEqual(events[0]["event_type"], "shell_command")

    def test_batches_large_backlogs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            history = Path(tmp) / "history.txt"
            history.write_text("", encoding="utf-8")
            state = TailState(Path(tmp) / "state.json")
            tailer = PowerShellHistoryTailer(path=history, batch_threshold=3)
            tailer.poll(state)
            with history.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(f"cmd{i}" for i in range(10)) + "\n")
            events = tailer.poll(state)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event_type"], "shell_history_batch")
            self.assertEqual(events[0]["payload"]["count"], 10)


class ClipboardTailerTests(unittest.TestCase):
    def test_baseline_dedupe_and_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = TailState(Path(tmp) / "state.json")
            values = ["first", "first", "second", "second", "x" * 50]
            tailer = ClipboardTailer(max_chars=10, reader=lambda: values.pop(0))
            self.assertEqual(tailer.poll(state), [])  # baseline swallows current content
            self.assertEqual(tailer.poll(state), [])  # unchanged
            events = tailer.poll(state)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["raw_text"], "second")
            self.assertEqual(tailer.poll(state), [])
            events = tailer.poll(state)
            self.assertEqual(events[0]["raw_text"], "x" * 10)
            self.assertTrue(events[0]["payload"]["truncated"])

    def test_state_survives_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state = TailState(state_path)
            tailer = ClipboardTailer(reader=lambda: "same text")
            tailer.poll(state)
            state.save()
            fresh = TailState(state_path)
            self.assertEqual(ClipboardTailer(reader=lambda: "same text").poll(fresh), [])


if __name__ == "__main__":
    unittest.main()
