from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .locking import exclusive_file_lock


SCHEMA = "rawmem.event.v1"
CURSOR_SCHEMA = "rawmem.cursor.v1"
EVENT_BATCH_SCHEMA = "rawmem.event_batch.v1"
VERIFY_SCHEMA = "rawmem.verify.v1"
LEDGER_STATE_SCHEMA = "rawmem.ledger_state.v1"
DEFAULT_BATCH_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class LedgerCursor:
    ledger_id: str
    byte_offset: int = 0
    last_event_id: str | None = None
    last_content_hash: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "LedgerCursor":
        schema = value.get("schema_version") or value.get("schema")
        if schema not in (None, CURSOR_SCHEMA):
            raise ValueError(f"Unsupported cursor schema: {schema}")
        ledger_id = str(value.get("ledger_id") or "").strip()
        if not ledger_id:
            raise ValueError("Cursor ledger_id is required")
        offset = int(value.get("byte_offset", 0))
        if offset < 0:
            raise ValueError("Cursor byte_offset cannot be negative")
        return cls(
            ledger_id=ledger_id,
            byte_offset=offset,
            last_event_id=_optional_text(value.get("last_event_id")),
            last_content_hash=_optional_text(value.get("last_content_hash")),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CURSOR_SCHEMA,
            "ledger_id": self.ledger_id,
            "byte_offset": self.byte_offset,
            "last_event_id": self.last_event_id,
            "last_content_hash": self.last_content_hash,
        }


@dataclass
class EventBatch:
    events: list[dict[str, Any]]
    next_cursor: LedgerCursor
    chain_status: str
    cursor_status: str = "ok"
    truncated: bool = False
    bytes_read: int = 0
    ledger_size: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": EVENT_BATCH_SCHEMA,
            "events": self.events,
            "next_cursor": self.next_cursor.as_dict(),
            "chain_status": self.chain_status,
            "cursor_status": self.cursor_status,
            "truncated": self.truncated,
            "bytes_read": self.bytes_read,
            "ledger_size": self.ledger_size,
            "warnings": self.warnings,
        }


@dataclass
class VerificationResult:
    valid: bool
    ledger_id: str
    event_count: int
    byte_size: int
    last_event_id: str | None = None
    last_content_hash: str | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": VERIFY_SCHEMA,
            "valid": self.valid,
            "ledger_id": self.ledger_id,
            "event_count": self.event_count,
            "byte_size": self.byte_size,
            "last_event_id": self.last_event_id,
            "last_content_hash": self.last_content_hash,
            "errors": self.errors,
        }


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def load_cursor(path: str | Path) -> LedgerCursor | None:
    target = Path(path)
    if not target.exists():
        return None
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid cursor file {target}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Invalid cursor file {target}: expected an object")
    return LedgerCursor.from_dict(value)


def save_cursor(path: str | Path, cursor: LedgerCursor) -> None:
    _atomic_write_json(Path(path), cursor.as_dict())


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex}"


def default_home() -> Path:
    rawmem_home = os.environ.get("RAWMEM_HOME")
    if rawmem_home:
        return Path(rawmem_home).expanduser()
    return Path.home() / ".rawmem"


def resolve_ledger_path(
    explicit: str | Path | None = None,
    *,
    local: bool = False,
    cwd: str | Path | None = None,
) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    env_path = os.environ.get("RAWMEM_LEDGER")
    if env_path:
        return Path(env_path).expanduser()
    base_cwd = Path(cwd or os.getcwd())
    if local:
        return base_cwd / ".rawmem" / "events.jsonl"
    return default_home() / "events.jsonl"


def infer_project(cwd: str | Path | None = None) -> str:
    base = Path(cwd or os.getcwd())
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=base,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip()).name
    except OSError:
        pass
    return base.resolve().name


def parse_key_value_pairs(items: Iterable[str]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"Expected KEY=VALUE, got: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Empty key in field: {item}")
        payload[key] = value
    return payload


def file_artifact(path: str | Path, *, kind: str = "file") -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    artifact: dict[str, Any] = {
        "kind": kind,
        "path": str(resolved),
        "exists": resolved.exists(),
    }
    if resolved.is_file():
        artifact["size"] = resolved.stat().st_size
        artifact["sha256"] = sha256_file(resolved)
    return artifact


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(event: dict[str, Any]) -> str:
    payload = {key: value for key, value in event.items() if key != "content_hash"}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def ledger_lock_path(ledger_path: str | Path) -> Path:
    path = Path(ledger_path)
    return path.with_name(f"{path.name}.lock")


def ledger_state_path(ledger_path: str | Path) -> Path:
    path = Path(ledger_path)
    return path.with_name(f"{path.name}.state.json")


def _load_ledger_state(path: Path) -> dict[str, Any] | None:
    state_path = ledger_state_path(path)
    if not state_path.exists():
        return None
    try:
        value = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("schema_version") != LEDGER_STATE_SCHEMA:
        return None
    return value


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _decode_event_line(path: Path, raw: bytes, *, location: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid JSON in {path} at {location}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Invalid event object in {path} at {location}")
    return value


def _read_first_event_unlocked(path: Path) -> dict[str, Any] | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    with path.open("rb") as handle:
        for raw in handle:
            if raw.strip():
                return _decode_event_line(path, raw, location="first event")
    return None


def _read_last_event_unlocked(path: Path, *, before_offset: int | None = None) -> dict[str, Any] | None:
    if not path.exists():
        return None
    size = path.stat().st_size
    position = size if before_offset is None else min(max(0, before_offset), size)
    if position == 0:
        return None
    with path.open("rb") as handle:
        while position > 0:
            handle.seek(position - 1)
            if handle.read(1) in (b"\n", b"\r"):
                position -= 1
                continue
            break
        if position == 0:
            return None
        chunks: list[bytes] = []
        while position > 0:
            start = max(0, position - 65536)
            handle.seek(start)
            chunk = handle.read(position - start)
            newline = chunk.rfind(b"\n")
            if newline >= 0:
                chunks.append(chunk[newline + 1 :])
                break
            chunks.append(chunk)
            position = start
        raw = b"".join(reversed(chunks)).strip()
    if not raw:
        return None
    return _decode_event_line(path, raw, location=f"offset {before_offset or size}")


def _state_matches_file(state: dict[str, Any], path: Path) -> bool:
    if not path.exists():
        return int(state.get("file_size", 0)) == 0
    stat = path.stat()
    return (
        int(state.get("file_size", -1)) == stat.st_size
        and int(state.get("file_mtime_ns", -1)) == stat.st_mtime_ns
    )


def _ensure_state_unlocked(path: Path) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    state = _load_ledger_state(path)
    if state is not None and _state_matches_file(state, path):
        return state

    first = _read_first_event_unlocked(path)
    last = _read_last_event_unlocked(path)
    previous_first_hash = _optional_text((state or {}).get("first_content_hash"))
    current_first_hash = _optional_text((first or {}).get("content_hash"))
    ledger_id = _optional_text((state or {}).get("ledger_id"))
    if ledger_id is None or (
        previous_first_hash is not None
        and current_first_hash is not None
        and previous_first_hash != current_first_hash
    ):
        ledger_id = f"ledger_{uuid.uuid4().hex}"
    stat = path.stat() if path.exists() else None
    rebuilt = {
        "schema_version": LEDGER_STATE_SCHEMA,
        "ledger_id": ledger_id,
        "file_size": stat.st_size if stat else 0,
        "file_mtime_ns": stat.st_mtime_ns if stat else 0,
        "first_content_hash": current_first_hash,
        "last_event_id": _optional_text((last or {}).get("event_id")),
        "last_content_hash": _optional_text((last or {}).get("content_hash")),
        "event_count": None,
        "updated_at": utc_now_iso(),
    }
    _atomic_write_json(ledger_state_path(path), rebuilt)
    return rebuilt


def ledger_identity(ledger_path: str | Path) -> str:
    path = Path(ledger_path)
    with exclusive_file_lock(ledger_lock_path(path)):
        return str(_ensure_state_unlocked(path)["ledger_id"])


def read_events(ledger_path: str | Path) -> list[dict[str, Any]]:
    path = Path(ledger_path)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with exclusive_file_lock(ledger_lock_path(path)):
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSON in {path} line {line_number}: {exc}") from exc
                if not isinstance(value, dict):
                    raise ValueError(f"Invalid event object in {path} line {line_number}")
                events.append(value)
    return events


def last_event(ledger_path: str | Path) -> dict[str, Any] | None:
    path = Path(ledger_path)
    with exclusive_file_lock(ledger_lock_path(path)):
        return _read_last_event_unlocked(path)


def build_event(
    *,
    source: str,
    event_type: str,
    project: str | None = None,
    cwd: str | Path | None = None,
    summary: str | None = None,
    raw_text: str | None = None,
    tags: Iterable[str] = (),
    artifacts: Iterable[dict[str, Any]] = (),
    payload: dict[str, Any] | None = None,
    privacy_scope: str = "local_only",
    review_required: bool = True,
) -> dict[str, Any]:
    base_cwd = Path(cwd or os.getcwd()).resolve()
    return {
        "schema": SCHEMA,
        "event_id": new_event_id(),
        "ts": utc_now_iso(),
        "source": source,
        "event_type": event_type,
        "project": project or infer_project(base_cwd),
        "cwd": str(base_cwd),
        "summary": summary or summarize(raw_text),
        "raw_text": raw_text or "",
        "tags": list(tags),
        "artifacts": list(artifacts),
        "payload": payload or {},
        "privacy": {
            "scope": privacy_scope,
            "review_required": review_required,
        },
        "previous_hash": None,
    }


def summarize(text: str | None, *, limit: int = 120) -> str:
    if not text:
        return ""
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "..."


def append_event(ledger_path: str | Path, event: dict[str, Any]) -> dict[str, Any]:
    path = Path(ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(ledger_lock_path(path)):
        state = _ensure_state_unlocked(path)
        event = dict(event)
        event["previous_hash"] = state.get("last_content_hash")
        event["content_hash"] = content_hash(event)
        raw = (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        with path.open("ab") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        stat = path.stat()
        count = state.get("event_count")
        state.update(
            {
                "file_size": stat.st_size,
                "file_mtime_ns": stat.st_mtime_ns,
                "first_content_hash": state.get("first_content_hash") or event["content_hash"],
                "last_event_id": event.get("event_id"),
                "last_content_hash": event["content_hash"],
                "event_count": count + 1 if isinstance(count, int) else None,
                "updated_at": utc_now_iso(),
            }
        )
        _atomic_write_json(ledger_state_path(path), state)
        return event


def verify_ledger(ledger_path: str | Path) -> VerificationResult:
    """Verify every JSON event, content hash, link, and event id in a ledger."""

    path = Path(ledger_path)
    with exclusive_file_lock(ledger_lock_path(path)):
        state = _ensure_state_unlocked(path)
        errors: list[dict[str, Any]] = []
        previous_hash: str | None = None
        first_hash: str | None = None
        last_id: str | None = None
        seen_ids: set[str] = set()
        count = 0
        if path.exists():
            with path.open("rb") as handle:
                for line_number, raw in enumerate(handle, start=1):
                    if not raw.strip():
                        continue
                    try:
                        event = _decode_event_line(path, raw, location=f"line {line_number}")
                    except ValueError as exc:
                        errors.append(
                            {"line": line_number, "code": "invalid_json", "message": str(exc)}
                        )
                        break
                    count += 1
                    event_id = _optional_text(event.get("event_id"))
                    stored_hash = _optional_text(event.get("content_hash"))
                    if event.get("schema") != SCHEMA:
                        errors.append(
                            {
                                "line": line_number,
                                "code": "schema_mismatch",
                                "message": f"Expected {SCHEMA}, got {event.get('schema')}",
                            }
                        )
                    if event_id is None:
                        errors.append(
                            {"line": line_number, "code": "missing_event_id", "message": "event_id missing"}
                        )
                    elif event_id in seen_ids:
                        errors.append(
                            {
                                "line": line_number,
                                "code": "duplicate_event_id",
                                "message": f"Duplicate event_id: {event_id}",
                            }
                        )
                    else:
                        seen_ids.add(event_id)
                    if event.get("previous_hash") != previous_hash:
                        errors.append(
                            {
                                "line": line_number,
                                "code": "previous_hash_mismatch",
                                "message": "previous_hash does not match the preceding content_hash",
                            }
                        )
                    calculated = content_hash(event)
                    if stored_hash != calculated:
                        errors.append(
                            {
                                "line": line_number,
                                "code": "content_hash_mismatch",
                                "message": f"Stored {stored_hash}, calculated {calculated}",
                            }
                        )
                    previous_hash = stored_hash
                    first_hash = first_hash or stored_hash
                    last_id = event_id
        size = path.stat().st_size if path.exists() else 0
        valid = not errors
        if valid:
            stat = path.stat() if path.exists() else None
            state.update(
                {
                    "file_size": size,
                    "file_mtime_ns": stat.st_mtime_ns if stat else 0,
                    "first_content_hash": first_hash,
                    "last_event_id": last_id,
                    "last_content_hash": previous_hash,
                    "event_count": count,
                    "updated_at": utc_now_iso(),
                }
            )
            _atomic_write_json(ledger_state_path(path), state)
        return VerificationResult(
            valid=valid,
            ledger_id=str(state["ledger_id"]),
            event_count=count,
            byte_size=size,
            last_event_id=last_id,
            last_content_hash=previous_hash,
            errors=errors,
        )


def _coerce_cursor(value: LedgerCursor | dict[str, Any] | None, ledger_id: str) -> LedgerCursor:
    if value is None:
        return LedgerCursor(ledger_id=ledger_id)
    if isinstance(value, LedgerCursor):
        return value
    if isinstance(value, dict):
        return LedgerCursor.from_dict(value)
    raise TypeError("after_cursor must be a LedgerCursor, dict, or None")


def _matches_filter(value: Any, allowed: set[str] | None) -> bool:
    return allowed is None or str(value or "") in allowed


def iter_events(
    ledger_path: str | Path,
    *,
    after_cursor: LedgerCursor | dict[str, Any] | None = None,
    sources: Iterable[str] | None = None,
    event_types: Iterable[str] | None = None,
    projects: Iterable[str] | None = None,
    limit: int | None = None,
    max_bytes: int = DEFAULT_BATCH_BYTES,
) -> EventBatch:
    """Read complete new events without loading the whole ledger into memory."""

    if limit is not None and limit < 0:
        raise ValueError("limit cannot be negative")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    path = Path(ledger_path)
    source_filter = set(sources) if sources is not None else None
    type_filter = set(event_types) if event_types is not None else None
    project_filter = set(projects) if projects is not None else None
    with exclusive_file_lock(ledger_lock_path(path)):
        state = _ensure_state_unlocked(path)
        ledger_id = str(state["ledger_id"])
        cursor = _coerce_cursor(after_cursor, ledger_id)
        size = path.stat().st_size if path.exists() else 0
        reset_cursor = LedgerCursor(ledger_id=ledger_id)
        if cursor.ledger_id != ledger_id:
            return EventBatch(
                events=[],
                next_cursor=reset_cursor,
                chain_status="not_checked",
                cursor_status="ledger_changed",
                ledger_size=size,
                warnings=["Cursor belongs to a different ledger identity; reset is required."],
            )
        if cursor.byte_offset > size:
            return EventBatch(
                events=[],
                next_cursor=reset_cursor,
                chain_status="not_checked",
                cursor_status="truncated",
                ledger_size=size,
                warnings=["Ledger is shorter than the cursor offset; reset or recovery is required."],
            )
        expected_previous = cursor.last_content_hash
        if cursor.byte_offset > 0:
            if not cursor.last_event_id or not cursor.last_content_hash:
                return EventBatch(
                    events=[],
                    next_cursor=reset_cursor,
                    chain_status="failed",
                    cursor_status="invalid",
                    ledger_size=size,
                    warnings=["Non-zero cursor is missing the boundary event id or content hash."],
                )
            boundary = _read_last_event_unlocked(path, before_offset=cursor.byte_offset)
            if (
                boundary is None
                or boundary.get("event_id") != cursor.last_event_id
                or boundary.get("content_hash") != cursor.last_content_hash
            ):
                return EventBatch(
                    events=[],
                    next_cursor=reset_cursor,
                    chain_status="failed",
                    cursor_status="invalid",
                    ledger_size=size,
                    warnings=["Cursor boundary no longer matches the ledger; reset is required."],
                )
        if limit == 0 or not path.exists() or cursor.byte_offset == size:
            return EventBatch(
                events=[],
                next_cursor=cursor,
                chain_status="verified" if cursor.byte_offset == 0 else "partial",
                ledger_size=size,
            )

        selected: list[dict[str, Any]] = []
        warnings: list[str] = []
        offset = cursor.byte_offset
        last_id = cursor.last_event_id
        last_hash = cursor.last_content_hash
        failed = False
        stopped_early = False
        with path.open("rb") as handle:
            handle.seek(offset)
            while offset < size:
                if offset - cursor.byte_offset >= max_bytes:
                    stopped_early = True
                    break
                line_start = offset
                raw = handle.readline(min(size - offset, max_bytes - (offset - cursor.byte_offset)))
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    warnings.append("Ignored an incomplete trailing event line.")
                    stopped_early = True
                    break
                offset += len(raw)
                if not raw.strip():
                    continue
                try:
                    event = _decode_event_line(path, raw, location=f"byte {line_start}")
                except ValueError as exc:
                    warnings.append(str(exc))
                    failed = True
                    offset = line_start
                    break
                stored_hash = _optional_text(event.get("content_hash"))
                if event.get("previous_hash") != expected_previous or stored_hash != content_hash(event):
                    warnings.append(f"Hash chain verification failed at byte {line_start}.")
                    failed = True
                    offset = line_start
                    break
                expected_previous = stored_hash
                last_hash = stored_hash
                last_id = _optional_text(event.get("event_id"))
                if (
                    _matches_filter(event.get("source"), source_filter)
                    and _matches_filter(event.get("event_type"), type_filter)
                    and _matches_filter(event.get("project"), project_filter)
                ):
                    selected.append(event)
                    if limit is not None and len(selected) >= limit:
                        stopped_early = offset < size
                        break
        next_cursor = LedgerCursor(
            ledger_id=ledger_id,
            byte_offset=offset,
            last_event_id=last_id,
            last_content_hash=last_hash,
        )
        if failed:
            chain_status = "failed"
        elif cursor.byte_offset == 0 and offset == size:
            chain_status = "verified"
        else:
            chain_status = "partial"
        return EventBatch(
            events=selected,
            next_cursor=next_cursor,
            chain_status=chain_status,
            cursor_status="invalid" if failed else "ok",
            truncated=stopped_early,
            bytes_read=offset - cursor.byte_offset,
            ledger_size=size,
            warnings=warnings,
        )


def init_local_store(cwd: str | Path | None = None) -> Path:
    base = Path(cwd or os.getcwd())
    store = base / ".rawmem"
    store.mkdir(parents=True, exist_ok=True)
    gitignore = store / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")
    return store / "events.jsonl"


def artifact_dir_for(ledger_path: str | Path, event_id: str) -> Path:
    path = Path(ledger_path)
    return path.parent / "artifacts" / event_id
