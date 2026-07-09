# rawmem

`rawmem` is a tiny local-first evidence ledger for AI and human workflows.

The idea is deliberately small:

> Store now, understand later.

AI memory systems often start by summarizing. `rawmem` starts one layer lower:
append-only raw evidence. A chat turn, a 2px UI tweak, a bug fix, a command run,
a git snapshot, a browser clip, or a coding-agent completion can all become the
same kind of local event.

The hard boundary is simple: capture is not recall. The ledger is a source
layer. Review, filtering, MemoryDSL conversion, preference mining, and project
rules can be derived later.

## Why This Exists

Small facts look noisy alone. Repeated across time and projects, they become
patterns:

- "This icon is too small; make it 2px bigger."
- "The AI forgot to update the desktop package again."
- "This project prefers append-only bug records."
- "The same class of boundary bug appeared in three repos."

Disk is cheap. Missing history is expensive.

## Install For Development

```powershell
cd D:\Dev\Projects\rawmem
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

The runtime has no third-party dependencies.

## Quick Start Without Installing

```powershell
cd D:\Dev\Projects\rawmem
$env:PYTHONPATH = "src"
python -m rawmem init --local
python -m rawmem capture --local --source manual --type note --text "First rawmem event"
python -m rawmem tail --local
```

## Commands

Capture a manual event:

```powershell
python -m rawmem capture --local --source codex --type task_note --project rawmem --text "User asked for a local-first raw evidence ledger."
```

Capture stdin:

```powershell
"The button should be 2px larger." | python -m rawmem capture --local --source clipboard --type ui_feedback --stdin
```

Wrap a command and record its result:

```powershell
python -m rawmem run --local --source terminal --project rawmem -- python --version
```

Record a git snapshot:

```powershell
python -m rawmem git-snapshot --local --project rawmem
```

Show recent events:

```powershell
python -m rawmem tail --local --limit 10
```

## Storage

By default, events are written to:

```text
~/.rawmem/events.jsonl
```

Use `--local` to write to the current project's private ledger:

```text
.rawmem/events.jsonl
```

Use `--ledger <path>` or `RAWMEM_LEDGER` for an explicit ledger path.

`.rawmem/` is ignored by Git because raw evidence can contain private text,
paths, command output, and local work context.

## Event Shape

Each line is JSON:

```json
{
  "schema": "rawmem.event.v1",
  "event_id": "evt_...",
  "ts": "2026-07-09T00:00:00Z",
  "source": "codex",
  "event_type": "task_note",
  "project": "rawmem",
  "cwd": "D:/Dev/Projects/rawmem",
  "summary": "User asked for a local-first raw evidence ledger.",
  "raw_text": "User asked for a local-first raw evidence ledger.",
  "tags": [],
  "artifacts": [],
  "payload": {},
  "privacy": {
    "scope": "local_only",
    "review_required": true
  },
  "previous_hash": null,
  "content_hash": "..."
}
```

The `previous_hash` / `content_hash` chain is a lightweight tamper-evidence
mechanism. It is not a security boundary, but it makes accidental rewrites more
visible.

## Adapter Strategy

`rawmem` should not chase every AI tool with one giant integration. The stable
piece is the ledger. Capture adapters can be small and optional:

- CLI wrappers for Codex, Claude Code, shell commands, and build scripts.
- Git/file watchers for tools that ultimately change a repo.
- Browser extensions for web AI chats, issue pages, docs, and web clips.
- App-specific adapters when a tool exposes logs, exports, plugins, or APIs.
- Manual hotkey/clipboard capture as a low-friction fallback.

All adapters should emit the same event schema.

## Privacy Principles

- Local first.
- No upload by default.
- No automatic memory promotion.
- Background capture must be opt-in.
- Store raw events separately from reviewed or derived memory.
- Prefer allowlists for browser/app capture.

## Development

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests
```
