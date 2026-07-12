"""Atomic sealed-archive lifecycle for append-only rawmem ledgers."""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .archive_format import (
    ARCHIVE_MANIFEST_SCHEMA,
    ARCHIVE_REGISTRY_SCHEMA,
    ARCHIVE_VERIFY_REPORT_SCHEMA,
    BREAKPOINT_LIST_SCHEMA,
    LEDGER_TRANSITION_SCHEMA,
    SEAL_RESULT_SCHEMA,
    SealedArchiveError,
    archive_breakpoint_list_path,
    archive_manifest_path,
    archive_registry_path,
    archive_verify_report_path,
    assert_active_ledger,
    load_archive_query_context,
    load_archive_registry,
    sha256_file,
)
from .ledger import (
    DEFAULT_BATCH_BYTES,
    LEDGER_STATE_SCHEMA,
    EventBatch,
    LedgerCursor,
    _iter_events_snapshot,
    build_event,
    content_hash,
    ledger_lock_path,
    ledger_state_path,
    utc_now_iso,
)
from .locking import exclusive_file_lock
from .projection import EVENT_PROJECTIONS
from .verification import VerificationResult, verify_ledger


ARCHIVE_VERIFY_STATUS_SCHEMA = "rawmem.archive_verify_status.v1"


class LedgerNotSealableError(RuntimeError):
    """Raised when a ledger has integrity errors that cannot be archived as-is."""


class SealRollbackError(RuntimeError):
    """Raised when a seal fails and restoring the original layout also fails."""


def _default_archive_path(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    token = uuid.uuid4().hex[:8]
    return path.parent / "archives" / f"{path.stem}.{stamp}.{token}.sealed{path.suffix}"


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    _write_bytes(path, _json_bytes(value))


def _remove_file(path: Path) -> None:
    if not path.exists():
        return
    _clear_read_only(path)
    path.unlink()


def _mark_read_only(path: Path) -> None:
    if os.name == "nt":
        os.chmod(path, stat.S_IREAD)


def _clear_read_only(path: Path) -> None:
    if os.name == "nt" and path.exists():
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)


def _is_read_only(path: Path) -> bool:
    if os.name != "nt":
        return False
    readonly = getattr(stat, "FILE_ATTRIBUTE_READONLY", 1)
    return bool(path.stat().st_file_attributes & readonly)


def _same_filesystem(left_parent: Path, right_parent: Path) -> bool:
    return left_parent.stat().st_dev == right_parent.stat().st_dev


def _assert_targets_absent(paths: Iterable[Path]) -> None:
    for path in paths:
        if path.exists():
            raise FileExistsError(f"seal destination metadata already exists: {path}")


def _restore_registry(path: Path, original: bytes | None) -> None:
    if original is None:
        _remove_file(path)
        return
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.rollback.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(original)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _archive_registry_entry(
    *,
    archive_id: str,
    archive: Path,
    manifest: Path,
    manifest_sha256: str,
    verification: VerificationResult,
    sealed_at: str,
) -> dict[str, Any]:
    return {
        "archive_id": archive_id,
        "archive_path": str(archive),
        "manifest_path": str(manifest),
        "manifest_sha256": manifest_sha256,
        "sealed_at": sealed_at,
        "ledger_id": verification.ledger_id,
        "byte_size": verification.byte_size,
        "ledger_sha256": verification.snapshot_sha256,
        "event_count": verification.event_count,
        "breakpoint_count": len(verification.breakpoints),
        "status": "sealed_read_only",
    }


def seal_ledger(
    ledger_path: str | Path,
    *,
    destination: str | Path | None = None,
) -> dict[str, Any]:
    """Seal an existing active ledger and start a linked active chain.

    The source ledger is never rewritten.  Every write and rename occurs while
    holding the same lock used by ``append_event``.  Only historical
    ``previous_hash_mismatch`` errors are eligible for sealing; every other
    verification error fails closed before the source path is moved.
    """

    active = Path(ledger_path).expanduser()
    assert_active_ledger(active)
    archive = (
        Path(destination).expanduser() if destination else _default_archive_path(active)
    )
    if active.resolve() == archive.resolve():
        raise ValueError(
            "sealed archive destination must differ from the active ledger"
        )
    if not active.exists() or not active.is_file():
        raise FileNotFoundError(f"active ledger does not exist: {active}")

    archive.parent.mkdir(parents=True, exist_ok=True)
    active.parent.mkdir(parents=True, exist_ok=True)
    if not _same_filesystem(active.parent, archive.parent):
        raise ValueError(
            "atomic sealing requires the archive and active ledger on the same filesystem"
        )

    active_state = ledger_state_path(active)
    archive_state = ledger_state_path(archive)
    verify_path = archive_verify_report_path(archive)
    breakpoint_path = archive_breakpoint_list_path(archive)
    manifest_path = archive_manifest_path(archive)
    registry_path = archive_registry_path(active)
    final_archive_files = [
        archive,
        archive_state,
        verify_path,
        breakpoint_path,
        manifest_path,
    ]
    _assert_targets_absent(final_archive_files)

    stage = Path(tempfile.mkdtemp(prefix=".rawmem-seal-", dir=archive.parent))
    moved_archive = False
    moved_state = False
    published: list[Path] = []
    marked_read_only: list[Path] = []
    registry_original: bytes | None = None
    registry_published = False
    active_published = False
    active_state_published = False
    try:
        with exclusive_file_lock(ledger_lock_path(active)):
            assert_active_ledger(active)
            _assert_targets_absent(final_archive_files)
            registry_original = (
                registry_path.read_bytes() if registry_path.exists() else None
            )
            registry = load_archive_registry(active)

            verification = verify_ledger(active)
            fatal_errors = [
                error
                for error in verification.errors
                if error.get("code") != "previous_hash_mismatch"
            ]
            if fatal_errors:
                codes = sorted(
                    {str(error.get("code") or "unknown") for error in fatal_errors}
                )
                raise LedgerNotSealableError(
                    "ledger has non-archivable integrity errors: " + ", ".join(codes)
                )
            if verification.byte_size != active.stat().st_size:
                raise LedgerNotSealableError(
                    "ledger size changed before the seal transaction"
                )

            sealed_at = utc_now_iso()
            archive_id = f"archive_{uuid.uuid4().hex}"
            report_payload = {
                "schema_version": ARCHIVE_VERIFY_REPORT_SCHEMA,
                "generated_at": sealed_at,
                "active_ledger": str(active),
                "archive_ledger": str(archive),
                "seal_eligible": True,
                "accepted_error_codes": ["previous_hash_mismatch"],
                "verification": verification.as_dict(),
            }
            breakpoint_payload = {
                "schema_version": BREAKPOINT_LIST_SCHEMA,
                "generated_at": sealed_at,
                "archive_id": archive_id,
                "ledger_id": verification.ledger_id,
                "ledger_sha256": verification.snapshot_sha256,
                "byte_size": verification.byte_size,
                "count": len(verification.breakpoints),
                "breakpoints": verification.breakpoints,
            }
            staged_verify = stage / verify_path.name
            staged_breakpoints = stage / breakpoint_path.name
            _write_json(staged_verify, report_payload)
            _write_json(staged_breakpoints, breakpoint_payload)
            verify_sha256 = sha256_file(staged_verify)
            breakpoints_sha256 = sha256_file(staged_breakpoints)

            state_ref: dict[str, Any] | None = None
            if active_state.exists():
                state_ref = {
                    "path": str(archive_state),
                    "sha256": sha256_file(active_state),
                    "byte_size": active_state.stat().st_size,
                    "derived": True,
                }
            manifest_payload: dict[str, Any] = {
                "schema_version": ARCHIVE_MANIFEST_SCHEMA,
                "archive_id": archive_id,
                "sealed_at": sealed_at,
                "source_active_ledger": str(active),
                "archive": {
                    "path": str(archive),
                    "ledger_id": verification.ledger_id,
                    "byte_size": verification.byte_size,
                    "sha256": verification.snapshot_sha256,
                    "event_count": verification.event_count,
                    "first_content_hash": verification.first_content_hash,
                    "last_event_id": verification.last_event_id,
                    "last_content_hash": verification.last_content_hash,
                },
                "verification": {
                    "path": str(verify_path),
                    "sha256": verify_sha256,
                    "valid_without_breakpoint_policy": verification.valid,
                    "error_count": len(verification.errors),
                    "accepted_breakpoint_count": len(verification.breakpoints),
                    "fatal_error_count": 0,
                },
                "breakpoints": {
                    "path": str(breakpoint_path),
                    "sha256": breakpoints_sha256,
                    "count": len(verification.breakpoints),
                },
                "state_sidecar": state_ref,
                "append_policy": "sealed_read_only",
                "query_policy": "explicit_archive_only",
            }
            staged_manifest = stage / manifest_path.name
            _write_json(staged_manifest, manifest_payload)
            manifest_sha256 = sha256_file(staged_manifest)

            new_ledger_id = f"ledger_{uuid.uuid4().hex}"
            transition = build_event(
                source="rawmem",
                event_type="ledger_transition",
                project=active.parent.name,
                cwd=active.parent,
                summary="Started a new active ledger after sealing the previous ledger.",
                raw_text="",
                tags=("ledger", "transition", "sealed-archive"),
                payload={
                    "schema_version": LEDGER_TRANSITION_SCHEMA,
                    "transition": "sealed_archive",
                    "archive_id": archive_id,
                    "archive_ledger_ref": {
                        "path": str(archive),
                        "ledger_id": verification.ledger_id,
                        "sha256": verification.snapshot_sha256,
                        "byte_size": verification.byte_size,
                    },
                    "archive_manifest_ref": {
                        "path": str(manifest_path),
                        "sha256": manifest_sha256,
                    },
                    "breakpoint_count": len(verification.breakpoints),
                },
            )
            transition["previous_hash"] = None
            transition["content_hash"] = content_hash(transition)
            active_payload = (
                json.dumps(transition, ensure_ascii=False, sort_keys=True) + "\n"
            ).encode("utf-8")
            staged_active = stage / f"new-{active.name}"
            _write_bytes(staged_active, active_payload)
            staged_active_stat = staged_active.stat()
            new_state_payload = {
                "schema_version": LEDGER_STATE_SCHEMA,
                "ledger_id": new_ledger_id,
                "file_size": staged_active_stat.st_size,
                "file_mtime_ns": staged_active_stat.st_mtime_ns,
                "first_content_hash": transition["content_hash"],
                "last_event_id": transition["event_id"],
                "last_content_hash": transition["content_hash"],
                "event_count": 1,
                "updated_at": sealed_at,
            }
            staged_active_state = stage / f"new-{active_state.name}"
            _write_json(staged_active_state, new_state_payload)

            registry_entry = _archive_registry_entry(
                archive_id=archive_id,
                archive=archive,
                manifest=manifest_path,
                manifest_sha256=manifest_sha256,
                verification=verification,
                sealed_at=sealed_at,
            )
            archives = list(registry.get("archives") or [])
            archives.append(registry_entry)
            registry_payload = {
                "schema_version": ARCHIVE_REGISTRY_SCHEMA,
                "active_ledger": str(active),
                "updated_at": sealed_at,
                "derived": True,
                "rebuildable_from": "archive_manifests",
                "authority": "per_archive_manifest",
                "archives": archives,
            }
            staged_registry = stage / f"new-{registry_path.name}"
            _write_json(staged_registry, registry_payload)

            for staged, final in (
                (staged_verify, verify_path),
                (staged_breakpoints, breakpoint_path),
                (staged_manifest, manifest_path),
            ):
                os.replace(staged, final)
                published.append(final)
            # Publish the sealed marker before the archive path becomes visible.
            # An out-of-band append aimed at the destination therefore fails
            # closed even during the short rename portion of the transaction.
            os.replace(active, archive)
            moved_archive = True
            if active_state.exists():
                os.replace(active_state, archive_state)
                moved_state = True
            os.replace(staged_active, active)
            active_published = True
            os.replace(staged_active_state, active_state)
            active_state_published = True
            os.replace(staged_registry, registry_path)
            registry_published = True

            if sha256_file(archive) != verification.snapshot_sha256:
                raise RuntimeError("archive bytes changed during the seal transaction")
            active_verification = verify_ledger(active, ledger_id=new_ledger_id)
            if not active_verification.valid or active_verification.event_count != 1:
                raise RuntimeError(
                    "new active ledger failed transition-chain verification"
                )

            for target in (
                archive,
                verify_path,
                breakpoint_path,
                manifest_path,
                archive_state,
            ):
                if target.exists():
                    _mark_read_only(target)
                    marked_read_only.append(target)

            return {
                "schema_version": SEAL_RESULT_SCHEMA,
                "sealed_at": sealed_at,
                "archive_id": archive_id,
                "archived_ledger": str(archive),
                "archived_ledger_id": verification.ledger_id,
                "archived_bytes": verification.byte_size,
                "archived_sha256": verification.snapshot_sha256,
                "archive_manifest": str(manifest_path),
                "archive_manifest_sha256": manifest_sha256,
                "verify_report": str(verify_path),
                "breakpoint_list": str(breakpoint_path),
                "breakpoint_count": len(verification.breakpoints),
                "archive_bytes_unchanged": True,
                "archive_read_only": _is_read_only(archive),
                "new_ledger": str(active),
                "new_ledger_id": new_ledger_id,
                "transition_event_id": transition["event_id"],
                "transition_content_hash": transition["content_hash"],
                "archive_registry": str(registry_path),
            }
    except Exception as original:
        rollback_errors: list[str] = []
        try:
            for target in reversed(marked_read_only):
                _clear_read_only(target)
            if registry_published:
                _restore_registry(registry_path, registry_original)
            if active_state_published:
                _remove_file(active_state)
            if active_published:
                _remove_file(active)
            for target in reversed(published):
                _remove_file(target)
            if moved_state and archive_state.exists():
                _clear_read_only(archive_state)
                os.replace(archive_state, active_state)
            if moved_archive and archive.exists():
                _clear_read_only(archive)
                os.replace(archive, active)
        except Exception as rollback:
            rollback_errors.append(str(rollback))
        if rollback_errors:
            raise SealRollbackError(
                f"seal failed ({original}); rollback also failed ({'; '.join(rollback_errors)})"
            ) from original
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def iter_archive_events(
    archive_path: str | Path,
    *,
    after_cursor: LedgerCursor | dict[str, Any] | None = None,
    sources: Iterable[str] | None = None,
    event_types: Iterable[str] | None = None,
    projects: Iterable[str] | None = None,
    limit: int | None = None,
    max_bytes: int = DEFAULT_BATCH_BYTES,
    projection: str = "metadata",
) -> EventBatch:
    """Explicitly query a sealed archive and continue across pinned breakpoints."""

    if projection not in EVENT_PROJECTIONS:
        raise ValueError(f"unsupported event projection: {projection}")
    path = Path(archive_path)
    context = load_archive_query_context(path)
    return _iter_events_snapshot(
        path,
        ledger_id=context.ledger_id,
        after_cursor=after_cursor,
        sources=sources,
        event_types=event_types,
        projects=projects,
        limit=limit,
        max_bytes=max_bytes,
        projection=projection,
        recorded_breakpoints=context.recorded_breakpoints,
    )


def verify_sealed_archive(archive_path: str | Path) -> dict[str, Any]:
    """Purely verify an archive, its pinned bytes, and its recorded breaks."""

    path = Path(archive_path)
    context = load_archive_query_context(path)
    result = verify_ledger(path, ledger_id=context.ledger_id)
    archive = context.manifest["archive"]
    expected_breakpoints = context.recorded_breakpoints
    actual_breakpoints = {int(item["byte_offset"]): item for item in result.breakpoints}
    fatal_errors = [
        error
        for error in result.errors
        if error.get("code") != "previous_hash_mismatch"
    ]
    breakpoint_match = actual_breakpoints == expected_breakpoints
    digest_match = result.snapshot_sha256 == archive.get("sha256")
    manifest_path = archive_manifest_path(path)
    manifest_sha256 = sha256_file(manifest_path)
    source_active = Path(str(context.manifest.get("source_active_ledger") or ""))
    registry_path = archive_registry_path(source_active)
    registry_pin_match: bool | None = None
    if registry_path.exists():
        registry = load_archive_registry(source_active)
        matching = [
            item
            for item in registry["archives"]
            if item.get("archive_id") == context.manifest.get("archive_id")
        ]
        registry_pin_match = (
            len(matching) == 1
            and matching[0].get("manifest_sha256") == manifest_sha256
            and Path(str(matching[0].get("archive_path") or "")).resolve()
            == path.resolve()
        )
    state_ref = context.manifest.get("state_sidecar")
    state_match: bool | None = None
    if isinstance(state_ref, dict):
        state_path = ledger_state_path(path)
        state_match = (
            state_path.exists()
            and state_ref.get("sha256") == sha256_file(state_path)
            and state_ref.get("byte_size") == state_path.stat().st_size
        )
    return {
        "schema_version": ARCHIVE_VERIFY_STATUS_SCHEMA,
        "archive_path": str(path),
        "archive_id": context.manifest.get("archive_id"),
        "ledger_id": context.ledger_id,
        "valid": (
            not fatal_errors
            and breakpoint_match
            and digest_match
            and registry_pin_match is not False
            and state_match is not False
        ),
        "ledger_sha256_matches_manifest": digest_match,
        "manifest_sha256": manifest_sha256,
        "manifest_sha256_matches_registry": registry_pin_match,
        "state_sidecar_matches_manifest": state_match,
        "recorded_breakpoints_match": breakpoint_match,
        "fatal_error_count": len(fatal_errors),
        "accepted_breakpoint_count": len(result.breakpoints),
        "verification": result.as_dict(),
    }


def list_archives(active_ledger_path: str | Path) -> dict[str, Any]:
    """Return the metadata-only archive registry without touching disk."""

    return load_archive_registry(active_ledger_path)


__all__ = [
    "LedgerNotSealableError",
    "SealRollbackError",
    "SealedArchiveError",
    "iter_archive_events",
    "list_archives",
    "seal_ledger",
    "verify_sealed_archive",
]
