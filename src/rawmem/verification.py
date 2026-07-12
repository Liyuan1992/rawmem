"""Pure, side-effect-free verification for rawmem JSONL ledgers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .archive_format import archive_manifest_path


EVENT_SCHEMA = "rawmem.event.v1"
VERIFY_SCHEMA = "rawmem.verify.v1"
LEDGER_STATE_SCHEMA = "rawmem.ledger_state.v1"


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _canonical_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content_hash(event: dict[str, Any]) -> str:
    payload = {key: value for key, value in event.items() if key != "content_hash"}
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _safe_identifier(value: Any) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    if len(text) <= 128 and all(
        character.isalnum() or character in "_.:-" for character in text
    ):
        return text
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    return f"invalid_identifier_sha256:{digest}"


def _safe_hash(value: Any) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    lowered = text.lower()
    if len(lowered) == 64 and all(
        character in "0123456789abcdef" for character in lowered
    ):
        return lowered
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    return f"invalid_hash_sha256:{digest}"


def _state_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.state.json")


def _read_known_ledger_id(path: Path) -> str | None:
    manifest_path = archive_manifest_path(path)
    candidates = ((manifest_path, "archive"), (_state_path(path), "state"))
    for metadata_path, kind in candidates:
        if not metadata_path.exists():
            continue
        try:
            value = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict):
            continue
        if kind == "archive":
            archive = value.get("archive")
            candidate = archive.get("ledger_id") if isinstance(archive, dict) else None
        elif value.get("schema_version") == LEDGER_STATE_SCHEMA:
            candidate = value.get("ledger_id")
        else:
            candidate = None
        safe = _safe_identifier(candidate)
        if safe is not None and not safe.startswith("invalid_identifier_sha256:"):
            return safe
    return None


def _derived_ledger_id(path: Path, snapshot_sha256: str) -> str:
    seed = f"{path.resolve()}\0{snapshot_sha256}".encode("utf-8", errors="replace")
    return f"ledger_{hashlib.sha256(seed).hexdigest()[:32]}"


@dataclass
class VerificationResult:
    valid: bool
    ledger_id: str
    event_count: int
    byte_size: int
    snapshot_sha256: str
    first_content_hash: str | None = None
    last_event_id: str | None = None
    last_content_hash: str | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    breakpoints: list[dict[str, Any]] = field(default_factory=list)
    verified_at: str = field(default_factory=_utc_now_iso)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": VERIFY_SCHEMA,
            "valid": self.valid,
            "ledger_id": self.ledger_id,
            "event_count": self.event_count,
            "byte_size": self.byte_size,
            "snapshot_sha256": self.snapshot_sha256,
            "first_content_hash": self.first_content_hash,
            "last_event_id": self.last_event_id,
            "last_content_hash": self.last_content_hash,
            "error_count": len(self.errors),
            "breakpoint_count": len(self.breakpoints),
            "errors": self.errors,
            "breakpoints": self.breakpoints,
            "verified_at": self.verified_at,
            "read_only": True,
        }


def verify_ledger(
    ledger_path: str | Path,
    *,
    ledger_id: str | None = None,
) -> VerificationResult:
    """Verify a ledger without creating or modifying any file or directory.

    The verifier deliberately does not acquire ``<ledger>.lock`` because doing
    so would create persistent metadata for a previously untouched ledger.  A
    caller that already owns the active writer lock (notably ``seal_ledger``)
    gets a stable snapshot.  Other callers receive a
    ``ledger_changed_during_verification`` error if the file changes while it
    is being scanned.
    """

    path = Path(ledger_path)
    before = path.stat() if path.exists() else None
    digest = hashlib.sha256()
    errors: list[dict[str, Any]] = []
    breakpoints: list[dict[str, Any]] = []
    previous_hash: str | None = None
    previous_event_id: str | None = None
    first_hash: str | None = None
    last_id: str | None = None
    seen_ids: set[str] = set()
    count = 0
    byte_offset = 0

    if path.exists():
        with path.open("rb") as handle:
            for line_number, raw in enumerate(handle, start=1):
                line_offset = byte_offset
                byte_offset += len(raw)
                digest.update(raw)
                if not raw.strip():
                    continue
                try:
                    decoded = raw.decode("utf-8")
                    event = json.loads(decoded)
                    if not isinstance(event, dict):
                        raise ValueError("expected a JSON object")
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    errors.append(
                        {
                            "line": line_number,
                            "byte_offset": line_offset,
                            "code": "invalid_json",
                            "message": f"event line is not a valid UTF-8 JSON object: {exc}",
                        }
                    )
                    continue

                count += 1
                event_id = _safe_identifier(event.get("event_id"))
                stored_hash = _safe_hash(event.get("content_hash"))
                actual_previous = _safe_hash(event.get("previous_hash"))
                if event.get("schema") != EVENT_SCHEMA:
                    errors.append(
                        {
                            "line": line_number,
                            "byte_offset": line_offset,
                            "code": "schema_mismatch",
                            "message": f"expected schema {EVENT_SCHEMA}",
                        }
                    )
                if event_id is None:
                    errors.append(
                        {
                            "line": line_number,
                            "byte_offset": line_offset,
                            "code": "missing_event_id",
                            "message": "event_id is missing",
                        }
                    )
                elif event_id in seen_ids:
                    errors.append(
                        {
                            "line": line_number,
                            "byte_offset": line_offset,
                            "code": "duplicate_event_id",
                            "message": "event_id is duplicated",
                            "event_id": event_id,
                        }
                    )
                else:
                    seen_ids.add(event_id)

                if event.get("previous_hash") != previous_hash:
                    breakpoint = {
                        "line": line_number,
                        "byte_offset": line_offset,
                        "code": "previous_hash_mismatch",
                        "message": "previous_hash does not match the preceding content_hash",
                        "event_id": event_id,
                        "previous_event_id": previous_event_id,
                        "expected_previous_hash": _safe_hash(previous_hash),
                        "actual_previous_hash": actual_previous,
                        "content_hash": stored_hash,
                    }
                    errors.append(breakpoint)
                    breakpoints.append(dict(breakpoint))

                calculated = _content_hash(event)
                raw_stored_hash = _optional_text(event.get("content_hash"))
                if raw_stored_hash != calculated:
                    errors.append(
                        {
                            "line": line_number,
                            "byte_offset": line_offset,
                            "code": "content_hash_mismatch",
                            "message": "stored content_hash does not match the canonical event hash",
                            "event_id": event_id,
                            "stored_content_hash": stored_hash,
                            "calculated_content_hash": calculated,
                        }
                    )
                previous_hash = raw_stored_hash
                previous_event_id = event_id
                first_hash = first_hash or stored_hash
                last_id = event_id

    after = path.stat() if path.exists() else None
    changed = (before is None) != (after is None) or (
        before is not None
        and after is not None
        and (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
    )
    if changed:
        errors.append(
            {
                "line": None,
                "byte_offset": None,
                "code": "ledger_changed_during_verification",
                "message": "ledger changed while the read-only verification snapshot was scanned",
            }
        )

    snapshot_sha256 = digest.hexdigest()
    size = after.st_size if after is not None else 0
    known_id = (
        _safe_identifier(ledger_id)
        if ledger_id is not None
        else _read_known_ledger_id(path)
    )
    resolved_id = (
        known_id
        if known_id is not None
        and not known_id.startswith("invalid_identifier_sha256:")
        else _derived_ledger_id(path, snapshot_sha256)
    )
    return VerificationResult(
        valid=not errors,
        ledger_id=resolved_id,
        event_count=count,
        byte_size=size,
        snapshot_sha256=snapshot_sha256,
        first_content_hash=first_hash,
        last_event_id=last_id,
        last_content_hash=_safe_hash(previous_hash),
        errors=errors,
        breakpoints=breakpoints,
    )
