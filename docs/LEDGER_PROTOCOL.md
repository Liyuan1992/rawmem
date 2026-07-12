# rawmem Ledger Protocol

Version: rawmem 0.6.2

The JSONL event schema remains `rawmem.event.v1`. Version 0.6.2 adds a sealed
archive lifecycle without rewriting historical event bytes. The active ledger
keeps its configured path; archives are explicit, read-only evidence sources.

## Writer contract

- Every append acquires an OS-backed exclusive lock on `<ledger>.lock`.
- The event `previous_hash` is taken from `<ledger>.state.json` when that
  sidecar still matches file size and mtime.
- A missing or stale sidecar is repaired from the first and last complete
  event; steady-state append does not scan the ledger.
- The JSON event line and sidecar are flushed durably before success returns.
- The lock file is persistent metadata. Its existence does not mean a process
  currently owns the lock.
- An archive manifest next to a ledger is a fail-closed sealed marker.
  `append_event()` and ordinary active readers reject that path before creating
  a lock or state sidecar.

## Cursor contract

Schema: `rawmem.cursor.v1`

```json
{
  "schema_version": "rawmem.cursor.v1",
  "ledger_id": "ledger_...",
  "byte_offset": 0,
  "last_event_id": null,
  "last_content_hash": null
}
```

The cursor binds a consumer to one ledger identity and one verified event
boundary. `iter_events()` validates that boundary before continuing. A cursor
is not a line number and must not be copied to a different ledger.

## Batch contract

Schema: `rawmem.event_batch.v1`

Important fields:

- `events`: matching, projected events only.
- `next_cursor`: advances across every verified event scanned, including
  events excluded by filters.
- `chain_status`: `verified`, `partial`, `failed`, or `not_checked`.
- `cursor_status`: `ok`, `ledger_changed`, `truncated`, or `invalid`.
- `truncated`: more complete data remains after the configured event/byte
  budget.
- `integrity_warnings`: structured, manifest-pinned archive breakpoints crossed
  during this batch.

Consumers must persist `next_cursor` only when `cursor_status=ok` and
`chain_status` is not `failed`.

## Truncation and transition

- Same-identity file shrink: `cursor_status=truncated`.
- First-event identity change or a sealed-ledger transition:
  `cursor_status=ledger_changed`.
- Same-size replacement or boundary tampering: `cursor_status=invalid`.

Recovery is explicit: preserve the old cursor for audit, reset to the returned
zero cursor, and decide whether to backfill the new ledger. rawmem never
silently rewinds a consumer.

## Verification

`rawmem verify` checks every event object, event schema, duplicate event id,
`previous_hash`, and `content_hash`. Its JSON output uses
`rawmem.verify.v1`.

Verification is strictly read-only in 0.6.2:

- it does not create, open for write, or refresh `<ledger>.lock`;
- it does not create or refresh `<ledger>.state.json`;
- it does not create the parent directory for a missing ledger;
- it records a snapshot SHA-256 and reports
  `ledger_changed_during_verification` if size or mtime changes during a scan.

`seal_ledger()` owns the active writer lock before calling the same verifier,
so the archive decision is made from one stable snapshot.

## Sealed archive contract

```powershell
rawmem seal --yes --json
rawmem seal --ledger path\to\events.jsonl --destination path\to\archives\old.jsonl --yes --json
```

Default archives are created under an `archives/` directory beside the active
ledger. A seal requires the active ledger and destination to be on the same
filesystem so the path transitions can use atomic replacement.

While holding the same `<active>.lock` used by every append, rawmem:

1. performs a complete pure verification;
2. refuses JSON, schema, duplicate-id, content-hash, concurrent-change, and all
   other integrity failures;
3. permits historical `previous_hash_mismatch` records only, and copies every
   one into a pinned breakpoint list;
4. writes and fsyncs the verification report, breakpoint list, manifest, new
   transition ledger, new state, and registry update in staging files;
5. publishes the sealed marker before the archive path becomes visible;
6. moves the original JSONL bytes unchanged to the archive path;
7. creates the new active ledger at the original path;
8. verifies both the archive SHA-256 and the new transition chain;
9. atomically replaces the derived registry; and
10. marks the archive and its authoritative sidecars ReadOnly on Windows.

If any commit step fails, rawmem restores the original ledger, state, registry,
and destination layout before releasing the writer lock. A racing append is
serialized either before the archive snapshot or after the new transition
event; it is never lost between them.

### Archive files

For archive `old.jsonl`, the authoritative evidence set is:

```text
old.jsonl
old.jsonl.manifest.json       rawmem.archive_manifest.v1
old.jsonl.verify.json         rawmem.archive_verify_report.v1
old.jsonl.breakpoints.json    rawmem.breakpoint_list.v1
old.jsonl.state.json          optional derived state from the old active ledger
```

The manifest records the archive ledger identity, exact byte size and SHA-256,
event count, first/last hash boundary, verification-report hash,
breakpoint-list hash, and explicit `sealed_read_only` /
`explicit_archive_only` policies. It contains no event body.

The active ledger's first event after sealing is `ledger_transition` with
schema `rawmem.ledger_transition.v1`, `previous_hash=null`, and pinned refs to
the archive ledger and manifest. New capture continues at the same active path.

### Archive registry

`<active>.archives.json` uses `rawmem.archive_registry.v1`. It is a small,
metadata-only index for discovery and is explicitly marked `derived=true`,
`rebuildable_from=archive_manifests`, and
`authority=per_archive_manifest`. The per-archive manifest remains the
authority. Missing registry reads return an empty in-memory registry and do not
create a file.

```powershell
rawmem archives --json
```

## Explicit archive queries

Ordinary `iter_events()`, `tail`, `export`, capture, and daemon paths target the
active ledger only. A sealed path is never discovered or queried implicitly.

```powershell
rawmem verify --archive path\to\old.jsonl --json
rawmem export --archive path\to\old.jsonl                 # metadata projection
rawmem export --archive path\to\old.jsonl --projection summary
rawmem export --archive path\to\old.jsonl --projection full
rawmem tail --archive path\to\old.jsonl --projection summary
```

Before returning any archive event—even for a limited batch—rawmem hashes the
complete archive and compares it with the manifest. It then verifies each
scanned event's content hash. At a mismatch pinned by the manifest's
breakpoint-list hash, the reader continues and returns a structured
`integrity_warnings` entry. Unrecorded chain breaks and content-hash changes
fail closed. A complete scan containing recorded breaks has
`chain_status=partial`, `cursor_status=ok`.

Projection boundaries are:

- `metadata` (archive default): integrity and routing fields only; excludes
  `summary`, `raw_text`, `payload`, artifacts, and cwd;
- `summary`: metadata plus `summary`; still excludes raw body and payload;
- `full`: the complete event, requested explicitly.

## Python API

```python
from rawmem import (
    iter_archive_events,
    iter_events,
    list_archives,
    seal_ledger,
    verify_ledger,
    verify_sealed_archive,
)

batch = iter_events("events.jsonl", after_cursor=None, limit=100)
assert batch.cursor_status == "ok"

result = verify_ledger("events.jsonl")
assert result.valid

sealed = seal_ledger("events.jsonl")
archive = sealed["archived_ledger"]
assert verify_sealed_archive(archive)["valid"]

metadata = iter_archive_events(archive)  # explicit; metadata-only by default
registry = list_archives("events.jsonl")
```

`rotate_ledger()` and `rawmem rotate --yes` remain compatibility aliases for
the sealed-archive transaction; new integrations should use `seal`.
