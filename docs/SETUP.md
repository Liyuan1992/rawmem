# One-Time Setup

`rawmem` is most useful when it is configured once and then quietly captures
normal work evidence in the background.

The broad setup command is:

```powershell
cd D:\Dev\Projects\rawmem
$env:PYTHONPATH = "src"
python -m rawmem setup --all
```

That creates:

- `.rawmem/config.json`
- `.rawmem/scripts/rawmem-powershell-profile.ps1`
- `.rawmem/scripts/start-watch.ps1`
- `.rawmem/scripts/browser-bookmarklet.txt`
- repo-local Git hooks for commit, checkout, merge, rewrite, and push events

It does not edit your global PowerShell profile unless you explicitly request
that:

```powershell
python -m rawmem setup --install-powershell-profile --yes
```

## Coverage Matrix

| Surface | Command | Friction | What It Captures |
| --- | --- | --- | --- |
| Manual notes | `rawmem capture` | Explicit | Any typed event |
| Adapter JSON | `rawmem ingest` | Low | Any tool that can emit JSON |
| Clipboard/selection | `rawmem clip` | Low | Clipboard, selected text, URL/title metadata |
| Command wrapper | `rawmem run -- <cmd>` | Medium | Command, cwd, exit code, duration, stdout/stderr |
| Git lifecycle | `rawmem setup --install-git-hooks` | One-time | post-commit, checkout, merge, rewrite, pre-push snapshots |
| File changes | `rawmem watch` | Background | Batched created/modified/deleted file paths |
| Browser clips | `rawmem serve` + bookmarklet | One-time | Current page title, URL, selected text |
| PowerShell history | generated profile snippet | One-time | Completed shell commands and cwd |

## Background Capture Boundaries

`rawmem` does not install a keylogger, HTTPS proxy, screen recorder, or browser
history scraper. The broad capture surfaces are opt-in and local:

- shell prompt hook records completed command lines, not keystrokes;
- Git hooks record repository state, not private browser/app data;
- file watcher records paths and file metadata, not full file contents;
- browser capture only runs when the local server and bookmarklet/adapter are used;
- raw events stay in the local ledger and remain review-required.

## Browser Capture

Start the local endpoint:

```powershell
python -m rawmem serve --local
```

Then print or open the bookmarklet text:

```powershell
python -m rawmem bookmarklet
Get-Content .rawmem\scripts\browser-bookmarklet.txt
```

Create a browser bookmark with that JavaScript URL. When you click it, the
selected text, page title, and URL are posted to `localhost` and stored in the
ledger.

## File Watcher

Run once:

```powershell
python -m rawmem watch --local --once
```

Run continuously:

```powershell
python -m rawmem watch --local --interval 5
```

The first scan writes a baseline event. Later scans write `file_change_batch`
events.

## PowerShell Profile Snippet

For the current shell only:

```powershell
. .\.rawmem\scripts\rawmem-powershell-profile.ps1
```

For future shells:

```powershell
python -m rawmem setup --install-powershell-profile --yes
```

Temporarily pause shell capture:

```powershell
$env:RAWMEM_DISABLE = "1"
```

Resume:

```powershell
Remove-Item Env:\RAWMEM_DISABLE
```
