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

# 2. Preview every machine-level write (nothing is changed)
rawmem setup --global --install-startup --dry-run

# 3. Global config + global git hooks for every repository
rawmem setup --global --yes

# 4. Start the daemon at every logon (headless pythonw scheduled task)
rawmem setup --install-startup --yes
rawmem setup --start-daemon   # start it right now

# 5. Browser extension token
rawmem config --show-browser-token
```

Check that it is alive:

```powershell
rawmem doctor
rawmem daemon --status
rawmem tail --limit 10
```

`rawmem doctor` checks the effective config, ledger writability, daemon status
freshness, localhost endpoint, token handshake, Windows startup task, global
Git hooks, and recent events. Warnings do not fail the command by default;
use `rawmem doctor --strict` when every optional integration is expected.

## Coverage Matrix

| Surface | How | Friction | What lands in the ledger |
| --- | --- | --- | --- |
| Claude Code sessions | daemon tailer | Zero | user/assistant turns with project, session, branch |
| Codex sessions | daemon tailer | Zero | user/assistant turns with project, session |
| Cursor agent transcripts | daemon tailer | Zero | user/assistant turns with workspace and session |
| PowerShell commands | daemon tailer (PSReadLine history) | Zero | every completed command line |
| Clipboard | daemon poller | Zero after opt-in | deduped clipboard text changes |
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
- `daemon.tailers.cursor.root`: override Cursor's default
  `~/.cursor/projects` transcript root.
- `daemon.tailers.clipboard.enabled`: disabled by default; set `true` or run
  `rawmem config --include-clipboard` to enable it. Run
  `rawmem config --disable-clipboard` to turn it off without changing global
  Git hook settings.
- `daemon.serve.port`: capture endpoint port (default 8765).
- `daemon.serve.token`: random local browser capture token. Rotate with
  `rawmem config --rotate-browser-token`.
- `privacy.project_allowlist` / `privacy.path_allowlist`: optional glob
  allowlists. Empty means no allowlist restriction; once populated, unmatched
  events are skipped and counted.
- `privacy.redaction`: common secret shapes are redacted by default; add local
  regex patterns when a tool emits another credential shape.
- `privacy.artifacts.mode`: defaults to `references_only`; embedded artifact
  content is dropped while path/size/hash metadata remains.

First run baselines existing files instead of ingesting months of history;
use `--backfill` if you want the history.

Logs: `~/.rawmem/daemon.log` (when headless), status in
`~/.rawmem/daemon-status.json`, tail offsets in `~/.rawmem/tailer-state.json`.

## Global Git Hooks

`rawmem setup --global --yes` writes hooks into `~/.rawmem/git-hooks` and points
`git config --global core.hooksPath` at it. Every repository on the machine
then records commit/checkout/merge/rewrite/push snapshots with no per-repo
setup. Each hook chains to the repository's own `.git/hooks/<name>` when one
exists, and repos that set a local `core.hooksPath` (husky etc.) are
unaffected because local config overrides global.

Remove with `rawmem setup --uninstall-global-git-hooks`.

## Browser Extension

Load `extension/` as an unpacked MV3 extension (Chrome/Edge:
`chrome://extensions` → Developer mode → Load unpacked). It talks to the
daemon's localhost endpoint. Open the extension options page and paste the
token from:

```powershell
rawmem config --show-browser-token
```

Click **Test connection** in the options page. A successful test proves both
that the daemon is reachable and that it accepts the configured token.

Capture via:

- right-click → "rawmem: save selection" / "rawmem: save page"
- `Alt+Shift+S` (selection) / `Alt+Shift+P` (page)
- toolbar button (selection if any, otherwise page)

The legacy bookmarklet (`rawmem bookmarklet`) still works but is blocked by
strict CSP and CORS on many sites; the extension is the reliable path.

## Background Capture Boundaries

`rawmem` does not install a keylogger, HTTPS proxy, screen recorder, or
browser history scraper. The broad capture surfaces are opt-in and local:

- tailers read logs your tools already write on this machine;
- the shell tailer records completed command lines, not keystrokes;
- Git hooks record repository state, not private browser/app data;
- the file watcher records paths and metadata, not file contents;
- the clipboard poller is disabled by default, dedupes content, and baselines
  at startup after it is enabled;
- browser capture only happens when you explicitly trigger the extension and
  the request includes the local capture token;
- raw events stay in the local ledger and remain review-required.

Pause everything: stop the daemon (`schtasks /End /TN rawmem-daemon` or kill
the pythonw process). Pause git hooks temporarily: set `RAWMEM_DISABLE=1`.

## Uninstall

Preview the removal first:

```powershell
rawmem uninstall --dry-run
```

Disable the startup task, global Git hook setting, and rawmem PowerShell
profile block while preserving all captured data:

```powershell
rawmem uninstall
```

Delete the local config, state, generated hooks, and ledger only when that is
explicitly intended:

```powershell
rawmem uninstall --remove-home --yes
```

## Legacy Per-Project Setup

`rawmem setup --all` still creates a project-local `.rawmem/` store, config,
helper scripts, bookmarklet text, and repo-local git hooks for projects that
want an isolated ledger. The PowerShell profile snippet
(`--install-powershell-profile --yes`) is now mostly redundant because the
history tailer covers shell commands passively.
