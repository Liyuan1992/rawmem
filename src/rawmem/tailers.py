"""Passive tailers: pull evidence from files other tools already write.

The design principle is "capture without asking": agents and shells should not
have to self-report. Each tailer keeps a byte offset per source file in a
shared state file, and only complete new lines are consumed. On the first run
a tailer baselines existing files (offset = size) so a fresh install does not
flood the ledger with months of history; pass backfill=True to ingest history.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Iterable

from .ledger import build_event, summarize

STATE_SCHEMA = "rawmem.tailer_state.v1"
MAX_READ_BYTES = 8 * 1024 * 1024


class TailState:
    """Persistent per-tailer file offsets and scalar values."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = {"schema": STATE_SCHEMA, "tailers": {}}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and isinstance(loaded.get("tailers"), dict):
                    self.data = loaded
            except (OSError, json.JSONDecodeError):
                pass

    def tailer(self, name: str) -> dict[str, Any]:
        tailers = self.data.setdefault("tailers", {})
        entry = tailers.setdefault(name, {})
        entry.setdefault("initialized", False)
        entry.setdefault("files", {})
        entry.setdefault("values", {})
        return entry

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def read_new_lines(path: Path, entry: dict[str, Any], *, max_bytes: int = MAX_READ_BYTES) -> list[str]:
    """Return complete new lines since the stored offset, advancing it."""
    try:
        size = path.stat().st_size
    except OSError:
        return []
    offset = int(entry.get("offset", 0))
    if size < offset:
        offset = 0
    if size == offset:
        entry["offset"] = offset
        return []
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read(max_bytes)
    except OSError:
        return []
    newline_index = chunk.rfind(b"\n")
    if newline_index < 0:
        # Incomplete trailing line; consume it only if the chunk is full,
        # otherwise wait for the writer to finish the line.
        if len(chunk) < max_bytes:
            entry["offset"] = offset
            return []
        newline_index = len(chunk) - 1
    consumed = chunk[: newline_index + 1]
    entry["offset"] = offset + len(consumed)
    text = consumed.decode("utf-8", errors="replace")
    return [line for line in text.splitlines() if line.strip()]


class FileTailer:
    """Base class: discover files, baseline on first run, parse new lines."""

    name = "file-tailer"

    def __init__(self, *, backfill: bool = False) -> None:
        self.backfill = backfill

    def discover(self) -> Iterable[Path]:  # pragma: no cover - overridden
        return []

    def parse_line(self, line: str, entry: dict[str, Any], path: Path) -> dict[str, Any] | None:
        raise NotImplementedError

    def register_file(self, path: Path, entry: dict[str, Any]) -> None:
        """Hook for one-time metadata extraction when a file is first seen."""

    def poll(self, state: TailState) -> list[dict[str, Any]]:
        tstate = state.tailer(self.name)
        first_run = not tstate["initialized"]
        files: dict[str, Any] = tstate["files"]
        events: list[dict[str, Any]] = []
        for path in self.discover():
            key = str(path)
            entry = files.get(key)
            if entry is None:
                entry = {"offset": 0, "meta": {}}
                files[key] = entry
                self.register_file(path, entry)
                if first_run and not self.backfill:
                    try:
                        entry["offset"] = path.stat().st_size
                    except OSError:
                        pass
                    continue
            for line in read_new_lines(path, entry):
                try:
                    event = self.parse_line(line, entry, path)
                except (ValueError, KeyError, TypeError):
                    event = None
                if event is not None:
                    events.append(event)
        tstate["initialized"] = True
        return events

    def coverage(self, state: TailState) -> dict[str, int]:
        files = state.tailer(self.name).get("files") or {}
        tracked = len(files)
        available = sum(1 for path in files if Path(path).is_file())
        return {"tracked_files": tracked, "available_files": available}


def _extract_text_blocks(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text)
        return "\n\n".join(parts)
    return ""


class ClaudeCodeTailer(FileTailer):
    """Tail Claude Code session transcripts under ~/.claude/projects."""

    name = "claude-code"

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        include_assistant: bool = True,
        include_sidechain: bool = False,
        max_chars: int = 6000,
        backfill: bool = False,
    ) -> None:
        super().__init__(backfill=backfill)
        self.root = Path(root) if root else Path.home() / ".claude" / "projects"
        self.include_assistant = include_assistant
        self.include_sidechain = include_sidechain
        self.max_chars = max_chars

    def discover(self) -> Iterable[Path]:
        if not self.root.is_dir():
            return []
        return sorted(self.root.glob("*/*.jsonl"))

    def parse_line(self, line: str, entry: dict[str, Any], path: Path) -> dict[str, Any] | None:
        data = json.loads(line)
        if not isinstance(data, dict) or data.get("isMeta"):
            return None
        kind = data.get("type")
        if kind not in ("user", "assistant"):
            return None
        sidechain = bool(data.get("isSidechain"))
        if sidechain and not self.include_sidechain:
            return None
        if kind == "assistant" and not self.include_assistant:
            return None
        message = data.get("message") or {}
        text = _extract_text_blocks(message.get("content"))
        if not text.strip():
            return None
        truncated = len(text) > self.max_chars
        text = text[: self.max_chars]
        cwd = data.get("cwd")
        project = Path(cwd).name if cwd else "unknown"
        tags = ["agent", "claude-code"]
        if sidechain:
            tags.append("sidechain")
        return build_event(
            source="claude-code",
            event_type=f"agent_{kind}_turn",
            project=project,
            cwd=cwd or Path.home(),
            summary=summarize(text),
            raw_text=text,
            tags=tags,
            payload={
                "session_id": data.get("sessionId"),
                "uuid": data.get("uuid"),
                "orig_ts": data.get("timestamp"),
                "git_branch": data.get("gitBranch"),
                "transcript": str(path),
                "truncated": truncated,
            },
        )


class CodexTailer(FileTailer):
    """Tail Codex CLI/Desktop session rollouts under ~/.codex/sessions."""

    name = "codex"

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        include_assistant: bool = True,
        max_chars: int = 6000,
        backfill: bool = False,
    ) -> None:
        super().__init__(backfill=backfill)
        self.root = Path(root) if root else Path.home() / ".codex" / "sessions"
        self.include_assistant = include_assistant
        self.max_chars = max_chars

    def discover(self) -> Iterable[Path]:
        if not self.root.is_dir():
            return []
        return sorted(self.root.rglob("*.jsonl"))

    def register_file(self, path: Path, entry: dict[str, Any]) -> None:
        # The first line is session_meta with cwd; grab it even when the
        # baseline skips historical content so later turns get a project.
        try:
            with path.open("rb") as handle:
                first = handle.readline(65536)
            data = json.loads(first.decode("utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError, ValueError):
            return
        if isinstance(data, dict) and data.get("type") == "session_meta":
            payload = data.get("payload") or {}
            entry["meta"]["cwd"] = payload.get("cwd")
            entry["meta"]["session_id"] = payload.get("id") or payload.get("session_id")

    def parse_line(self, line: str, entry: dict[str, Any], path: Path) -> dict[str, Any] | None:
        data = json.loads(line)
        if not isinstance(data, dict):
            return None
        payload = data.get("payload") or {}
        if data.get("type") == "session_meta":
            entry["meta"]["cwd"] = payload.get("cwd")
            entry["meta"]["session_id"] = payload.get("id") or payload.get("session_id")
            return None
        if data.get("type") != "event_msg":
            return None
        msg_type = payload.get("type")
        if msg_type == "user_message":
            role = "user"
        elif msg_type == "agent_message":
            if not self.include_assistant:
                return None
            role = "assistant"
        else:
            return None
        text = payload.get("message")
        if not isinstance(text, str) or not text.strip():
            return None
        truncated = len(text) > self.max_chars
        text = text[: self.max_chars]
        cwd = entry["meta"].get("cwd")
        project = Path(cwd).name if cwd else "unknown"
        return build_event(
            source="codex",
            event_type=f"agent_{role}_turn",
            project=project,
            cwd=cwd or Path.home(),
            summary=summarize(text),
            raw_text=text,
            tags=["agent", "codex"],
            payload={
                "session_id": entry["meta"].get("session_id"),
                "orig_ts": data.get("timestamp"),
                "transcript": str(path),
                "truncated": truncated,
            },
        )


class CursorTailer(FileTailer):
    """Tail Cursor agent transcripts under ~/.cursor/projects."""

    name = "cursor"

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        include_assistant: bool = True,
        max_chars: int = 6000,
        backfill: bool = False,
    ) -> None:
        super().__init__(backfill=backfill)
        self.root = Path(root) if root else Path.home() / ".cursor" / "projects"
        self.include_assistant = include_assistant
        self.max_chars = max_chars

    def discover(self) -> Iterable[Path]:
        if not self.root.is_dir():
            return []
        paths: list[Path] = []
        for project_dir in self.root.iterdir():
            transcripts = project_dir / "agent-transcripts"
            if transcripts.is_dir():
                paths.extend(transcripts.rglob("*.jsonl"))
        return sorted(paths)

    def register_file(self, path: Path, entry: dict[str, Any]) -> None:
        project_dir = next(
            (parent for parent in path.parents if parent.parent == self.root),
            None,
        )
        entry["meta"]["project"] = project_dir.name if project_dir else path.parent.name
        entry["meta"]["session_id"] = path.stem

    def parse_line(self, line: str, entry: dict[str, Any], path: Path) -> dict[str, Any] | None:
        data = json.loads(line)
        if not isinstance(data, dict):
            return None
        message = data.get("message") if isinstance(data.get("message"), dict) else data
        role = str(data.get("role") or message.get("role") or "").lower()
        if role not in ("user", "assistant"):
            return None
        if role == "assistant" and not self.include_assistant:
            return None
        text = _extract_text_blocks(message.get("content"))
        if not text.strip():
            return None
        truncated = len(text) > self.max_chars
        text = text[: self.max_chars]
        project = str(entry["meta"].get("project") or "unknown")
        session_id = (
            data.get("sessionId")
            or data.get("session_id")
            or message.get("sessionId")
            or entry["meta"].get("session_id")
        )
        return build_event(
            source="cursor",
            event_type=f"agent_{role}_turn",
            project=project,
            cwd=Path.home(),
            summary=summarize(text),
            raw_text=text,
            tags=["agent", "cursor"],
            payload={
                "session_id": session_id,
                "orig_ts": data.get("timestamp") or data.get("ts") or message.get("timestamp"),
                "transcript": str(path),
                "workspace_key": project,
                "truncated": truncated,
            },
        )


class PowerShellHistoryTailer(FileTailer):
    """Tail the PSReadLine history file; covers every shell with no profile edit."""

    name = "powershell-history"

    def __init__(
        self,
        *,
        path: str | Path | None = None,
        batch_threshold: int = 20,
        backfill: bool = False,
    ) -> None:
        super().__init__(backfill=backfill)
        if path:
            self.history_path = Path(path)
        else:
            appdata = os.environ.get("APPDATA")
            base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
            self.history_path = (
                base / "Microsoft" / "Windows" / "PowerShell" / "PSReadLine" / "ConsoleHost_history.txt"
            )
        self.batch_threshold = batch_threshold

    def discover(self) -> Iterable[Path]:
        return [self.history_path] if self.history_path.is_file() else []

    def poll(self, state: TailState) -> list[dict[str, Any]]:
        tstate = state.tailer(self.name)
        first_run = not tstate["initialized"]
        files: dict[str, Any] = tstate["files"]
        events: list[dict[str, Any]] = []
        for path in self.discover():
            key = str(path)
            entry = files.get(key)
            if entry is None:
                entry = {"offset": 0, "meta": {}}
                files[key] = entry
                if first_run and not self.backfill:
                    try:
                        entry["offset"] = path.stat().st_size
                    except OSError:
                        pass
                    continue
            commands = join_continuations(read_new_lines(path, entry))
            commands = [cmd for cmd in commands if cmd.strip() and "rawmem" not in cmd]
            if not commands:
                continue
            if len(commands) > self.batch_threshold:
                raw_text = "\n".join(commands)
                events.append(
                    build_event(
                        source="powershell-history",
                        event_type="shell_history_batch",
                        project="shell",
                        cwd=Path.home(),
                        summary=f"Shell history batch: {len(commands)} commands",
                        raw_text=raw_text,
                        tags=["shell", "history"],
                        payload={"count": len(commands), "history_file": key},
                    )
                )
            else:
                for cmd in commands:
                    events.append(
                        build_event(
                            source="powershell-history",
                            event_type="shell_command",
                            project="shell",
                            cwd=Path.home(),
                            summary=summarize(cmd),
                            raw_text=cmd,
                            tags=["shell", "history"],
                            payload={"history_file": key},
                        )
                    )
        tstate["initialized"] = True
        return events


def join_continuations(lines: list[str]) -> list[str]:
    """PSReadLine stores multi-line commands as lines ending with a backtick."""
    commands: list[str] = []
    buffer: list[str] = []
    for line in lines:
        if line.endswith("`"):
            buffer.append(line[:-1])
            continue
        buffer.append(line)
        commands.append("\n".join(buffer))
        buffer = []
    if buffer:
        commands.append("\n".join(buffer))
    return commands


def build_tailers_from_config(
    tailer_config: dict[str, Any],
    *,
    backfill: bool = False,
) -> list[Any]:
    """Instantiate enabled tailers from the daemon.tailers config section."""
    tailers: list[Any] = []
    claude_cfg = tailer_config.get("claude_code") or {}
    if claude_cfg.get("enabled", True):
        tailers.append(
            ClaudeCodeTailer(
                root=claude_cfg.get("root"),
                include_assistant=claude_cfg.get("include_assistant", True),
                include_sidechain=claude_cfg.get("include_sidechain", False),
                max_chars=int(claude_cfg.get("max_chars", 6000)),
                backfill=backfill,
            )
        )
    codex_cfg = tailer_config.get("codex") or {}
    if codex_cfg.get("enabled", True):
        tailers.append(
            CodexTailer(
                root=codex_cfg.get("root"),
                include_assistant=codex_cfg.get("include_assistant", True),
                max_chars=int(codex_cfg.get("max_chars", 6000)),
                backfill=backfill,
            )
        )
    cursor_cfg = tailer_config.get("cursor") or {}
    if cursor_cfg.get("enabled", True):
        tailers.append(
            CursorTailer(
                root=cursor_cfg.get("root"),
                include_assistant=cursor_cfg.get("include_assistant", True),
                max_chars=int(cursor_cfg.get("max_chars", 6000)),
                backfill=backfill,
            )
        )
    ps_cfg = tailer_config.get("powershell_history") or {}
    if ps_cfg.get("enabled", True):
        tailers.append(
            PowerShellHistoryTailer(
                path=ps_cfg.get("path"),
                batch_threshold=int(ps_cfg.get("batch_threshold", 20)),
                backfill=backfill,
            )
        )
    return tailers
