# rawmem Architecture

`rawmem` has one stable center: an append-only JSONL evidence ledger.

```text
adapters -> event schema -> append-only ledger -> review/derive/query layers
```

## Capture Philosophy: Passive Over Self-Report

The highest-value evidence (correction moments in AI sessions, failed
commands, reverts) already exists on disk in logs other tools write. Asking
an agent to report back after finishing a task is both awkward and lossy;
tailing its transcript is neither. So the priority order is:

1. Tail files other tools already write (agent transcripts, shell history).
2. Hook lifecycle points that fire anyway (git hooks).
3. Accept pushes from tools that want to send JSON (ingest/serve).
4. Manual capture as the fallback, never the primary path.

## Layers

1. Capture adapters
   - Passive tailers: Claude Code transcripts, Codex session rollouts,
     PSReadLine shell history (all run inside `rawmem daemon`)
   - Clipboard poller (off by default, opt-in, deduped, baselined at daemon start)
   - Global and repo-local Git hooks and snapshots
   - Polling file watcher
   - Token-protected localhost browser endpoint + MV3 extension
   - Manual CLI capture, generic JSON ingest, CLI command wrapper
   - App-specific exporters

2. Source ledger
   - Raw event envelope
   - Artifact references
   - Cross-process locked appends and lightweight hash chain
   - O(1)-steady-state last-hash sidecar
   - Incremental `rawmem.cursor.v1` readers and full-chain verification
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

## The Daemon

`rawmem daemon` is the single resident process for every background surface:
a scheduler loop where each surface (tailer, clipboard, watcher, HTTP
endpoint) is a periodic task with its own interval. One process, one autostart
entry, one token-protected browser/tool endpoint, one status file
(`~/.rawmem/daemon-status.json`), one state file for tail offsets
(`~/.rawmem/tailer-state.json`).

First-run behavior is baseline-not-backfill: existing history files get
their offsets set to end-of-file so a fresh install does not flood the
ledger; only new content is captured. `--backfill` opts into history.

## One-Time Setup

```text
pip install --user -e .            # `rawmem` on PATH everywhere
rawmem setup --global --install-startup --dry-run  # inspect planned writes
rawmem setup --global --yes        # ~/.rawmem/config.json + global git hooks
rawmem setup --install-startup --yes  # logon task running the daemon headless
rawmem doctor                      # verify storage, daemon, token, and hooks
```

Project-local `rawmem setup --all` still exists for per-repo stores, but the
global daemon is the primary path. `setup --install-powershell-profile` is
now mostly redundant: the PSReadLine history tailer covers shell commands
passively.

`rawmem uninstall` reverses the machine integrations while preserving the
append-only ledger. Deleting `~/.rawmem` is a separate, confirmed action.

## Operability Layer

`diagnostics.py` checks the effective config, ledger writability, daemon status
freshness, localhost health, token handshake, Windows startup task, global Git
hook configuration, and recent ledger events. These checks inspect the source
layer directly and do not promote or rewrite captured evidence.

## Coverage Strategy

Coverage is layered rather than tool-specific:

- If a tool writes a log, tail it from the daemon (preferred).
- If a tool modifies files, global Git hooks and the watcher catch it.
- If a tool can emit JSON, use `rawmem ingest` or POST to the daemon.
- If work happens in a browser, use the MV3 extension in `extension/`.
- If none of those fit, use `rawmem clip`/`rawmem capture` manually.

## Design Rule

Adapters may be messy because tools are messy. The ledger format should stay
boringly stable.
