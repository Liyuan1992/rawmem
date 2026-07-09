# One-Time Setup

`rawmem` is most useful when it is configured once and then quietly captures
normal work evidence in the background. The design principle: **passive over
self-report** — tail what other tools already write instead of asking humans
or agents to do anything per event.

## The Three Commands

```powershell
# 1. Install the command (from the repo root)
python -m pip install --user -e .
# make sure the user Scripts dir is on PATH, e.g.
# %APPDATA%\Python\Python312\Scripts

# 2. Global config + global git hooks for every repository
rawmem setup --global

# 3. Start the daemon at every logon (headless pythonw scheduled task)
rawmem setup --install-startup --yes
rawmem setup --start-daemon   # start it right now
```

Check that it is alive:

```powershell
rawmem daemon --status
rawmem tail --limit 10
```

## Coverage Matrix

| Surface | How | Friction | What lands in the ledger |
| --- | --- | --- | --- |
| Claude Code sessions | daemon tailer | Zero | user/assistant turns with project, session, branch |
| Codex sessions | daemon tailer | Zero | user/assistant turns with project, session |
| PowerShell commands | daemon tailer (PSReadLine history) | Zero | every completed command line |
| Clipboard | daemon poller | Zero (opt-out) | deduped clipboard text changes |
| Git lifecycle, all repos | `setup --global` (core.hooksPath) | One-time | commit/checkout/merge/rewrite/push snapshots |
| File changes | daemon watcher (`daemon.watch.roots`) | One-time | batched created/modified/deleted paths |
| Browser pages | MV3 extension in `extension/` | One-time | selection or page text, title, URL |
| Any tool with JSON | `rawmem ingest` / POST `/capture` | Low | arbitrary adapter events |
| Manual notes | `rawmem capture` / `rawmem clip` | Explicit | any typed or clipped text |

## The Daemon

One process runs every background surface. Useful commands:

```powershell
rawmem daemon              # run in the foreground
rawmem daemon --status     # inspect task counters and errors
rawmem sync                # one manual tailer pass, no server
rawmem sync --backfill     # first run only: ingest existing history too
```

Configuration lives in `~/.rawmem/config.json` (deep-merged over defaults).
Notable knobs:

- `daemon.watch.roots`: directories to watch (off by default until set).
- `daemon.tailers.claude_code.include_assistant`: set `false` to keep only
  your own turns.
- `daemon.tailers.clipboard.enabled`: set `false` to disable clipboard capture.
- `daemon.serve.port`: capture endpoint port (default 8765).

First run baselines existing files instead of ingesting months of history;
use `--backfill` if you want the history.

Logs: `~/.rawmem/daemon.log` (when headless), status in
`~/.rawmem/daemon-status.json`, tail offsets in `~/.rawmem/tailer-state.json`.

## Global Git Hooks

`rawmem setup --global` writes hooks into `~/.rawmem/git-hooks` and points
`git config --global core.hooksPath` at it. Every repository on the machine
then records commit/checkout/merge/rewrite/push snapshots with no per-repo
setup. Each hook chains to the repository's own `.git/hooks/<name>` when one
exists, and repos that set a local `core.hooksPath` (husky etc.) are
unaffected because local config overrides global.

Remove with `rawmem setup --uninstall-global-git-hooks`.

## Browser Extension

Load `extension/` as an unpacked MV3 extension (Chrome/Edge:
`chrome://extensions` → Developer mode → Load unpacked). It talks to the
daemon's localhost endpoint. Capture via:

- right-click → "rawmem: save selection" / "rawmem: save page"
- `Alt+Shift+S` (selection) / `Alt+Shift+P` (page)
- toolbar button (selection if any, otherwise page)

The legacy bookmarklet (`rawmem bookmarklet`) still works but is blocked by
strict CSP on some sites; the extension is the reliable path.

## Background Capture Boundaries

`rawmem` does not install a keylogger, HTTPS proxy, screen recorder, or
browser history scraper. The broad capture surfaces are opt-in and local:

- tailers read logs your tools already write on this machine;
- the shell tailer records completed command lines, not keystrokes;
- Git hooks record repository state, not private browser/app data;
- the file watcher records paths and metadata, not file contents;
- the clipboard poller baselines at startup and can be disabled in config;
- browser capture only happens when you explicitly trigger the extension;
- raw events stay in the local ledger and remain review-required.

Pause everything: stop the daemon (`schtasks /End /TN rawmem-daemon` or kill
the pythonw process). Pause git hooks temporarily: set `RAWMEM_DISABLE=1`.

## Legacy Per-Project Setup

`rawmem setup --all` still creates a project-local `.rawmem/` store, config,
helper scripts, bookmarklet text, and repo-local git hooks for projects that
want an isolated ledger. The PowerShell profile snippet
(`--install-powershell-profile --yes`) is now mostly redundant because the
history tailer covers shell commands passively.
