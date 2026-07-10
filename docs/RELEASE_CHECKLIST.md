# Release Checklist

Use this before publishing `rawmem` publicly.

## Required Gates

```powershell
$env:PYTHONPATH = "src"
python -m compileall -q src tests scripts
python -m unittest discover -s tests
python scripts/open_source_audit.py
rawmem --version
rawmem setup --global --install-startup --dry-run
rawmem uninstall --dry-run
```

## Manual Checks

- README install instructions do not use a private workstation path.
- `PRIVACY.md` and `SECURITY.md` match the current defaults.
- `rawmem setup --global` refuses to run without `--yes`.
- Clipboard polling is off in `default_global_config()`.
- Browser capture requires `daemon.serve.token` by default.
- HTTP capture responses do not expose the local ledger path.
- `rawmem config --disable-clipboard` does not install global Git hooks.
- `rawmem setup --dry-run` performs no writes and does not require `--yes`.
- `rawmem uninstall` preserves the ledger by default.
- `rawmem uninstall --remove-home` refuses to run without `--yes`.
- `rawmem doctor` hides the browser token and returns nonzero for hard failures.
- The extension connection test distinguishes an unreachable daemon from a
  rejected token.
- The global Git hook integration test performs a real `git commit`.
- `.rawmem/`, private ledgers, logs, caches, and generated artifacts are not
  tracked by Git.
- Browser extension permissions are limited to context menus, active tab,
  scripting, storage, and localhost host permissions.

## Current Non-Goals

- No cloud sync.
- No memory promotion.
- No keylogging, HTTPS proxying, screen recording, or browser-history scrape.
- CI is Windows-only until another platform has an active maintainer and a
  verified startup integration.
