"""Bounded, read-only service contract for MCP hosts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .archive import list_archives
from .ledger import resolve_ledger_path
from .projection import EVENT_PROJECTIONS, project_event
from .verification import verify_ledger

TOOL_NAMES = ("rawmem_status", "rawmem_recent", "rawmem_archives")
DEFAULT_SCOPES = frozenset({"read:summary"})
MAX_LIMIT = 100
MAX_SCAN_BYTES = 8 * 1024 * 1024


class RawmemMCPService:
    """Side-effect-free raw evidence queries with explicit disclosure scopes."""

    def __init__(
        self,
        ledger: str | Path | None = None,
        *,
        local: bool = False,
        cwd: str | Path | None = None,
        scopes: str | Iterable[str] | None = None,
    ) -> None:
        self.ledger = resolve_ledger_path(ledger, local=local, cwd=cwd)
        if scopes is None:
            self.scopes = set(DEFAULT_SCOPES)
        elif isinstance(scopes, str):
            self.scopes = {item.strip() for item in scopes.split(",") if item.strip()}
        else:
            self.scopes = {str(item).strip() for item in scopes if str(item).strip()}

    def status(self) -> dict[str, Any]:
        denied = self._require("read:summary")
        if denied:
            return denied
        result = verify_ledger(self.ledger)
        verification = result.as_dict()
        exists = self.ledger.is_file()
        return {
            "ok": exists and result.valid,
            "schema": "rawmem.mcp_status.v1",
            "read_only": True,
            "ledger_exists": exists,
            "scopes": sorted(self.scopes),
            "verification": verification,
        }

    def recent(
        self,
        *,
        source: str = "",
        event_type: str = "",
        project: str = "",
        limit: int = 20,
        projection: str = "summary",
        max_scan_bytes: int = MAX_SCAN_BYTES,
    ) -> dict[str, Any]:
        required = "read:full" if projection == "full" else "read:summary"
        denied = self._require(required)
        if denied:
            return denied
        if projection not in EVENT_PROJECTIONS:
            return self._error(
                "invalid_projection",
                f"projection must be one of {EVENT_PROJECTIONS}",
            )
        if limit < 1 or limit > MAX_LIMIT:
            return self._error(
                "invalid_limit", f"limit must be between 1 and {MAX_LIMIT}"
            )
        if max_scan_bytes < 1 or max_scan_bytes > MAX_SCAN_BYTES:
            return self._error(
                "invalid_scan_budget",
                f"max_scan_bytes must be between 1 and {MAX_SCAN_BYTES}",
            )

        events, scanned_bytes, scan_truncated = _read_recent_events(
            self.ledger,
            source=source or None,
            event_type=event_type or None,
            project=project or None,
            limit=limit,
            projection=projection,
            max_scan_bytes=max_scan_bytes,
        )
        return {
            "ok": True,
            "schema": "rawmem.mcp_recent.v1",
            "read_only": True,
            "projection": projection,
            "events": events,
            "returned": len(events),
            "scanned_bytes": scanned_bytes,
            "scan_truncated": scan_truncated,
            "integrity": "not_checked",
            "integrity_hint": (
                "Call rawmem_status before treating results as verified evidence."
            ),
        }

    def archives(self, *, limit: int = 50) -> dict[str, Any]:
        denied = self._require("read:summary")
        if denied:
            return denied
        if limit < 1 or limit > MAX_LIMIT:
            return self._error(
                "invalid_limit", f"limit must be between 1 and {MAX_LIMIT}"
            )
        registry = list_archives(self.ledger)
        safe_fields = (
            "archive_id",
            "sealed_at",
            "event_count",
            "byte_size",
            "breakpoint_count",
            "ledger_id",
            "ledger_sha256",
        )
        items = [
            {key: item.get(key) for key in safe_fields if key in item}
            for item in (registry.get("archives") or [])[-limit:]
            if isinstance(item, dict)
        ]
        return {
            "ok": True,
            "schema": "rawmem.mcp_archives.v1",
            "read_only": True,
            "archives": items,
            "returned": len(items),
        }

    def _require(self, scope: str) -> dict[str, Any] | None:
        if scope in self.scopes:
            return None
        return self._error(
            "scope_denied",
            f"scope {scope} is required",
            required_scope=scope,
        )

    @staticmethod
    def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
        return {
            "ok": False,
            "schema": "rawmem.mcp_error.v1",
            "error": {"code": code, "message": message, **extra},
        }


def _read_recent_events(
    path: Path,
    *,
    source: str | None,
    event_type: str | None,
    project: str | None,
    limit: int,
    projection: str,
    max_scan_bytes: int,
) -> tuple[list[dict[str, Any]], int, bool]:
    if not path.is_file():
        return [], 0, False
    size = path.stat().st_size
    start = max(0, size - max_scan_bytes)
    with path.open("rb") as handle:
        handle.seek(start)
        raw = handle.read(size - start)
    if start:
        newline = raw.find(b"\n")
        raw = raw[newline + 1 :] if newline >= 0 else b""
    selected: list[dict[str, Any]] = []
    for line in reversed(raw.splitlines()):
        if not line.strip():
            continue
        try:
            event = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(event, dict):
            continue
        if source and event.get("source") != source:
            continue
        if event_type and event.get("event_type") != event_type:
            continue
        if project and event.get("project") != project:
            continue
        selected.append(project_event(event, projection))
        if len(selected) >= limit:
            break
    selected.reverse()
    return selected, size - start, start > 0


__all__ = ["DEFAULT_SCOPES", "RawmemMCPService", "TOOL_NAMES"]
