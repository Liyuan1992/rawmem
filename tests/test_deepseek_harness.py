from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rawmem.tailers import DeepSeekHarnessTailer, TailState


ROOT = Path(__file__).resolve().parents[1]


def _line(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8")


def _session_header() -> dict:
    return {
        "type": "session",
        "id": "session-fictional",
        "cwd": "/workspace/fictional-project",
        "createdAt": "2026-01-01T00:00:00Z",
        "agentPreset": "coding",
    }


def _user(text: str, *, source_kind: str = "user") -> dict:
    return {
        "type": "user/message",
        "data": {
            "id": "message-user",
            "source": {"kind": source_kind},
            "content": [{"type": "text", "text": text}],
        },
    }


def _assistant(text: str) -> dict:
    return {
        "type": "assistant/message",
        "data": {
            "turn": 1,
            "step": 2,
            "message": {
                "source": {"kind": "model"},
                "content": [
                    {"type": "reasoning", "text": "private chain"},
                    {"type": "text", "text": text},
                ],
            },
        },
    }


class DeepSeekHarnessPlainTailerTests(unittest.TestCase):
    def test_harness_overlay_uses_read_only_official_mcp_bridge_shape(self) -> None:
        overlay = ROOT / "examples" / "deepseek-harness" / "rawmem.cordis.yml"
        text = overlay.read_text(encoding="utf-8")
        self.assertIn("name: '@deepseek-ai/dsh-mcp-client'", text)
        self.assertIn("serverName: rawmem", text)
        self.assertIn("command: rawmem-mcp", text)
        self.assertIn("read:summary", text)
        self.assertNotIn("read:full", text)
        self.assertIn("failOnStartupError: true", text)

    def test_direct_turns_and_tool_metadata_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session-fictional"
            session.mkdir(parents=True)
            transcript = session / "session.jsonl"
            lines = [
                _session_header(),
                _user("Remember the fictional launch window."),
                _user("plugin injection", source_kind="plugin"),
                _assistant("The fictional launch window is recorded."),
                {
                    "type": "tool/call",
                    "data": {
                        "callId": "call-1",
                        "name": "shell",
                        "arguments": {"secret": "must-not-be-captured"},
                    },
                },
                {
                    "type": "tool/result",
                    "data": {
                        "message": {
                            "content": [
                                {
                                    "type": "tool_result",
                                    "toolCallId": "call-1",
                                    "content": "must-not-be-captured",
                                    "isError": False,
                                }
                            ]
                        }
                    },
                },
            ]
            transcript.write_bytes(b"".join(_line(item) for item in lines))
            events = DeepSeekHarnessTailer(root=root, backfill=True).poll(
                TailState(Path(tmp) / "state.json")
            )
            self.assertEqual(
                [event["event_type"] for event in events],
                [
                    "agent_user_turn",
                    "agent_assistant_turn",
                    "agent_tool_call",
                    "agent_tool_result",
                ],
            )
            self.assertEqual(events[0]["project"], "fictional-project")
            self.assertEqual(events[0]["payload"]["session_id"], "session-fictional")
            serialized = json.dumps(events, ensure_ascii=False)
            self.assertNotIn("private chain", serialized)
            self.assertNotIn("must-not-be-captured", serialized)

    def test_baseline_then_incremental_plain_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session-fictional"
            session.mkdir(parents=True)
            transcript = session / "session.jsonl"
            transcript.write_bytes(_line(_session_header()) + _line(_user("old")))
            state = TailState(Path(tmp) / "state.json")
            tailer = DeepSeekHarnessTailer(root=root)
            self.assertEqual(tailer.poll(state), [])
            with transcript.open("ab") as handle:
                handle.write(_line(_user("new")))
            events = tailer.poll(state)
            self.assertEqual([event["raw_text"] for event in events], ["new"])
            self.assertEqual(events[0]["project"], "fictional-project")


class DeepSeekHarnessZstdTailerTests(unittest.TestCase):
    def test_concatenated_frames_are_incremental(self) -> None:
        try:
            import zstandard
        except ImportError:
            self.skipTest("zstandard optional extra is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session-fictional"
            session.mkdir(parents=True)
            transcript = session / "session.jsonl.zstd"
            compressor = zstandard.ZstdCompressor()
            transcript.write_bytes(compressor.compress(_line(_session_header())))
            state = TailState(Path(tmp) / "state.json")
            tailer = DeepSeekHarnessTailer(root=root)
            self.assertEqual(tailer.poll(state), [])
            with transcript.open("ab") as handle:
                handle.write(compressor.compress(_line(_user("new compressed turn"))))
                handle.write(compressor.compress(_line(_assistant("compressed answer"))))
            events = tailer.poll(state)
            self.assertEqual(
                [event["raw_text"] for event in events],
                ["new compressed turn", "compressed answer"],
            )
            self.assertEqual(tailer.poll(state), [])


if __name__ == "__main__":
    unittest.main()
