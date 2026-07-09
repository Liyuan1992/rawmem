# rawmem Architecture

`rawmem` has one stable center: an append-only JSONL evidence ledger.

```text
adapters -> event schema -> append-only ledger -> review/derive/query layers
```

## Layers

1. Capture adapters
   - CLI wrapper
   - Git snapshot
   - Browser extension
   - App-specific exporters
   - Clipboard/hotkey fallback

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

## Design Rule

Adapters may be messy because tools are messy. The ledger format should stay
boringly stable.
