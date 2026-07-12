"""Sealed-archive metadata paths and read-only query validation.

This module intentionally has no dependency on :mod:`rawmem.ledger`.  Both the
writer and the verifier can therefore use the sealed marker without creating a
circular import or touching ledger state.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ARCHIVE_MANIFEST_SCHEMA = "rawmem.archive_manifest.v1"
ARCHIVE_VERIFY_REPORT_SCHEMA = "rawmem.archive_verify_report.v1"
BREAKPOINT_LIST_SCHEMA = "rawmem.breakpoint_list.v1"
SEAL_RESULT_SCHEMA = "rawmem.seal_result.v1"
LEDGER_TRANSITION_SCHEMA = "rawmem.ledger_transition.v1"
ARCHIVE_REGISTRY_SCHEMA = "rawmem.archive_registry.v1"


class SealedArchiveError(RuntimeError):
    """Raised when an active-ledger operation targets a sealed archive."""


@dataclass(frozen=True)
class ArchiveQueryContext:
    ledger_id: str
    recorded_breakpoints: dict[int, dict[str, Any]]
    manifest: dict[str, Any]


def archive_manifest_path(ledger_path: str | Path) -> Path:
    path = Path(ledger_path)
    return path.with_name(f"{path.name}.manifest.json")


def archive_verify_report_path(ledger_path: str | Path) -> Path:
    path = Path(ledger_path)
    return path.with_name(f"{path.name}.verify.json")


def archive_breakpoint_list_path(ledger_path: str | Path) -> Path:
    path = Path(ledger_path)
    return path.with_name(f"{path.name}.breakpoints.json")


def archive_registry_path(active_ledger_path: str | Path) -> Path:
    path = Path(active_ledger_path)
    return path.with_name(f"{path.name}.archives.json")


def has_archive_marker(ledger_path: str | Path) -> bool:
    """Return true for any manifest sidecar, even if that marker is damaged.

    A malformed marker must fail closed for append.  Explicit archive readers
    perform the stronger schema and hash checks in ``load_archive_query_context``.
    """

    return archive_manifest_path(ledger_path).exists()


def assert_active_ledger(ledger_path: str | Path) -> None:
    if has_archive_marker(ledger_path):
        raise SealedArchiveError(
            f"sealed archive is read-only and must be queried explicitly: {Path(ledger_path)}"
        )


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SealedArchiveError(
            f"invalid sealed-archive metadata {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise SealedArchiveError(
            f"invalid sealed-archive metadata {path}: expected an object"
        )
    return value


def load_archive_manifest(ledger_path: str | Path) -> dict[str, Any]:
    path = Path(ledger_path)
    marker = archive_manifest_path(path)
    if not marker.exists():
        raise SealedArchiveError(f"not a sealed archive (manifest missing): {path}")
    manifest = _read_object(marker)
    if manifest.get("schema_version") != ARCHIVE_MANIFEST_SCHEMA:
        raise SealedArchiveError(
            f"unsupported sealed-archive manifest schema: {manifest.get('schema_version')}"
        )
    archive = manifest.get("archive")
    if not isinstance(archive, dict):
        raise SealedArchiveError("sealed-archive manifest is missing archive metadata")
    ledger_id = str(archive.get("ledger_id") or "").strip()
    expected_size = archive.get("byte_size")
    if not ledger_id or not isinstance(expected_size, int) or expected_size < 0:
        raise SealedArchiveError(
            "sealed-archive manifest has invalid ledger identity or size"
        )
    recorded_path = archive.get("path")
    if (
        not isinstance(recorded_path, str)
        or Path(recorded_path).resolve() != path.resolve()
    ):
        raise SealedArchiveError("sealed archive path does not match its manifest")
    expected_digest = str(archive.get("sha256") or "").strip().lower()
    if len(expected_digest) != 64 or any(
        character not in "0123456789abcdef" for character in expected_digest
    ):
        raise SealedArchiveError("sealed-archive manifest has an invalid ledger digest")
    if not path.exists() or path.stat().st_size != expected_size:
        raise SealedArchiveError("sealed archive size no longer matches its manifest")
    return manifest


def load_archive_query_context(ledger_path: str | Path) -> ArchiveQueryContext:
    path = Path(ledger_path)
    manifest = load_archive_manifest(path)
    archive = manifest["archive"]
    if sha256_file(path) != archive.get("sha256"):
        raise SealedArchiveError(
            "sealed archive SHA-256 no longer matches its manifest"
        )
    verification_ref = manifest.get("verification")
    if not isinstance(verification_ref, dict):
        raise SealedArchiveError(
            "sealed-archive manifest is missing verification metadata"
        )
    verification_path = archive_verify_report_path(path)
    expected_verification_hash = (
        str(verification_ref.get("sha256") or "").strip().lower()
    )
    if not verification_path.exists() or len(expected_verification_hash) != 64:
        raise SealedArchiveError(
            "sealed-archive verification report is missing or unpinned"
        )
    if sha256_file(verification_path) != expected_verification_hash:
        raise SealedArchiveError(
            "sealed-archive verification report hash does not match its manifest"
        )
    breakpoint_ref = manifest.get("breakpoints")
    if not isinstance(breakpoint_ref, dict):
        raise SealedArchiveError(
            "sealed-archive manifest is missing breakpoint metadata"
        )

    breakpoint_path = archive_breakpoint_list_path(path)
    expected_hash = str(breakpoint_ref.get("sha256") or "").strip().lower()
    if not breakpoint_path.exists() or len(expected_hash) != 64:
        raise SealedArchiveError(
            "sealed-archive breakpoint list is missing or unpinned"
        )
    if sha256_file(breakpoint_path) != expected_hash:
        raise SealedArchiveError(
            "sealed-archive breakpoint list hash does not match its manifest"
        )

    payload = _read_object(breakpoint_path)
    if payload.get("schema_version") != BREAKPOINT_LIST_SCHEMA:
        raise SealedArchiveError(
            f"unsupported breakpoint-list schema: {payload.get('schema_version')}"
        )
    raw_breakpoints = payload.get("breakpoints")
    if not isinstance(raw_breakpoints, list):
        raise SealedArchiveError("sealed-archive breakpoint list is invalid")
    declared_count = breakpoint_ref.get("count")
    if not isinstance(declared_count, int) or declared_count != len(raw_breakpoints):
        raise SealedArchiveError(
            "sealed-archive breakpoint count does not match its manifest"
        )

    recorded: dict[int, dict[str, Any]] = {}
    for item in raw_breakpoints:
        if not isinstance(item, dict) or item.get("code") != "previous_hash_mismatch":
            raise SealedArchiveError(
                "sealed archive contains an unsupported breakpoint record"
            )
        offset = item.get("byte_offset")
        if not isinstance(offset, int) or offset < 0 or offset in recorded:
            raise SealedArchiveError(
                "sealed archive contains an invalid breakpoint offset"
            )
        recorded[offset] = item
    return ArchiveQueryContext(
        ledger_id=str(archive["ledger_id"]),
        recorded_breakpoints=recorded,
        manifest=manifest,
    )


def load_archive_registry(active_ledger_path: str | Path) -> dict[str, Any]:
    """Read the derived archive registry without creating it when absent."""

    path = archive_registry_path(active_ledger_path)
    if not path.exists():
        return {
            "schema_version": ARCHIVE_REGISTRY_SCHEMA,
            "active_ledger": str(Path(active_ledger_path)),
            "derived": True,
            "rebuildable_from": "archive_manifests",
            "authority": "per_archive_manifest",
            "archives": [],
        }
    value = _read_object(path)
    if value.get("schema_version") != ARCHIVE_REGISTRY_SCHEMA:
        raise SealedArchiveError(
            f"unsupported archive-registry schema: {value.get('schema_version')}"
        )
    archives = value.get("archives")
    if not isinstance(archives, list) or not all(
        isinstance(item, dict) for item in archives
    ):
        raise SealedArchiveError("archive registry contains an invalid archives list")
    return value
