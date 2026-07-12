"""Privacy-aware event projections for ledger queries."""

from __future__ import annotations

from typing import Any


EVENT_PROJECTIONS = ("full", "summary", "metadata")

_METADATA_FIELDS = (
    "schema",
    "event_id",
    "ts",
    "source",
    "event_type",
    "project",
    "tags",
    "privacy",
    "previous_hash",
    "content_hash",
)


def project_event(event: dict[str, Any], projection: str) -> dict[str, Any]:
    """Return a new event object with private body fields excluded as requested."""

    if projection == "full":
        return dict(event)
    if projection not in EVENT_PROJECTIONS:
        raise ValueError(f"unsupported event projection: {projection}")
    result = {key: event.get(key) for key in _METADATA_FIELDS if key in event}
    if projection == "summary":
        result["summary"] = event.get("summary") or ""
    return result
