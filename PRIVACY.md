# Privacy Model

`rawmem` is local-first evidence capture. It is designed to help you keep raw
work evidence without uploading it or turning it into long-term memory
automatically.

## What Is Stored

Depending on enabled capture surfaces, the ledger can contain:

- AI session turns from Codex and Claude Code transcript files.
- PowerShell command history lines.
- Git repository snapshots at lifecycle hooks.
- Browser selections or page text when the extension is triggered.
- Clipboard text only when clipboard polling is explicitly enabled.
- Manual notes, JSON adapter payloads, command summaries, and artifact paths.

## What Is Not Done

`rawmem` does not:

- upload by default;
- record keystrokes;
- intercept HTTPS traffic;
- screen-record;
- scrape full browser history;
- promote raw events into durable memory automatically.

## Defaults

- The main ledger is local: `~/.rawmem/events.jsonl`.
- Existing logs are baselined on first run; history is not backfilled unless
  `--backfill` is used.
- Clipboard polling is disabled by default.
- Browser capture is explicit: you trigger the extension or send JSON to the
  localhost endpoint.
- Global Git hook installation requires `--yes`.

## Pause And Remove

Pause Git hooks temporarily:

```powershell
$env:RAWMEM_DISABLE = "1"
```

Remove global Git hooks:

```powershell
rawmem setup --uninstall-global-git-hooks
```

Disable clipboard polling while keeping the rest of the global config:

```powershell
rawmem setup --global --yes --disable-clipboard
```

Stop the Windows startup task:

```powershell
schtasks /End /TN rawmem-daemon
rawmem setup --uninstall-startup
```

Delete local data by removing `~/.rawmem/` after stopping the daemon.
