# rawmem Ledger Protocol

Version: rawmem 0.6.0

The JSONL event schema remains `rawmem.event.v1`. Version 0.6 adds stable
reader, cursor, verification, and writer-coordination contracts without
changing existing event fields.

## Writer contract

- Every append acquires an OS-backed exclusive lock on `<ledger>.lock`.
- The event `previous_hash` is taken from `<ledger>.state.json` when that
  sidecar still matches file size and mtime.
- A missing or stale sidecar is repaired from the first and last complete
  event; steady-state append does not scan the ledger.
- The JSON event line and sidecar are flushed durably before success returns.
- The lock file is persistent metadata. Its existence does not mean a process
  currently owns the lock.

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

- `events`: matching events only.
- `next_cursor`: advances across every verified event scanned, including
  events excluded by filters.
- `chain_status`: `verified`, `partial`, `failed`, or `not_checked`.
- `cursor_status`: `ok`, `ledger_changed`, `truncated`, or `invalid`.
- `truncated`: more complete data remains after the configured event/byte
  budget.

Consumers must persist `next_cursor` only when `cursor_status=ok` and
`chain_status` is not `failed`.

## Truncation and rotation

- Same-identity file shrink: `cursor_status=truncated`.
- First-event identity change or an explicitly rotated ledger:
  `cursor_status=ledger_changed`.
- Same-size replacement or boundary tampering: `cursor_status=invalid`.

Recovery is explicit: preserve the old cursor for audit, reset to the returned
zero cursor, and decide whether to backfill the new ledger. rawmem never
silently rewinds a consumer.

## Verification

`rawmem verify` checks every event object, event schema, duplicate event id,
`previous_hash`, and `content_hash`. Its JSON output uses
`rawmem.verify.v1`. Verification reads a locked snapshot and does not rewrite
events; on success it refreshes only the derived sidecar metadata.

## Python API

```python
from rawmem import iter_events, verify_ledger

batch = iter_events("events.jsonl", after_cursor=None, limit=100)
assert batch.cursor_status == "ok"
print(batch.next_cursor.as_dict())

result = verify_ledger("events.jsonl")
assert result.valid
```
