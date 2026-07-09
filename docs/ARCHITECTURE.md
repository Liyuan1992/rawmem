# rawmem Architecture

`rawmem` has one stable center: an append-only JSONL evidence ledger.

```text
adapters -> event schema -> append-only ledger -> review/derive/query layers
```

## Layers

1. Capture adapters
   - Manual CLI capture
   - Generic JSON ingest
   - CLI command wrapper
   - Git hooks and snapshots
   - Polling file watcher
   - Clipboard/selection capture
   - Localhost browser endpoint and bookmarklet
   - App-specific exporters
   - Shell profile snippets

2. Source ledger
   - Raw event envelope
   - Artifact references
   - Lightweight hash chain
   - Local/private by default

3. Derived layers
   - Review queue
   - MemoryDSL conversion
   - Bug-pattern mining
   - User preference and project-rule candidates

## Non-Goals For The Core

- It is not a vector database.
- It is not an automatic user-profile writer.
- It is not a browser history recorder.
- It is not a keylogger or network interceptor.
- It does not decide what deserves recall.

## One-Time Setup

`rawmem setup --all` creates a project-local `.rawmem/` store, config file,
helper scripts, bookmarklet text, and Git hooks. It does not edit global user
profiles unless `--install-powershell-profile --yes` is passed.

This keeps the first-run path broad but auditable:

```text
setup --all
  -> .rawmem/config.json
  -> .rawmem/scripts/start-watch.ps1
  -> .rawmem/scripts/rawmem-powershell-profile.ps1
  -> .rawmem/scripts/browser-bookmarklet.txt
  -> .git/hooks/post-commit
  -> .git/hooks/post-checkout
  -> .git/hooks/post-merge
  -> .git/hooks/post-rewrite
  -> .git/hooks/pre-push
```

## Coverage Strategy

Coverage is layered rather than tool-specific:

- If a tool can emit JSON, use `rawmem ingest`.
- If a tool runs in a shell, use the PowerShell prompt snippet or `rawmem run`.
- If a tool modifies files, use Git hooks and `rawmem watch`.
- If work happens in a browser, use `rawmem serve` plus the bookmarklet.
- If none of those fit, use `rawmem clip` or a future app-specific adapter.

## Design Rule

Adapters may be messy because tools are messy. The ledger format should stay
boringly stable.
