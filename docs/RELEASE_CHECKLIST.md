# Release Checklist

Use this before publishing `rawmem` publicly.

## Required Gates

```powershell
$env:PYTHONPATH = "src"
python -m compileall -q src tests scripts
python -m unittest discover -s tests
python scripts/open_source_audit.py
rawmem --version
```

## Manual Checks

- README install instructions do not use a private workstation path.
- `PRIVACY.md` and `SECURITY.md` match the current defaults.
- `rawmem setup --global` refuses to run without `--yes`.
- Clipboard polling is off in `default_global_config()`.
- The global Git hook integration test performs a real `git commit`.
- `.rawmem/`, private ledgers, logs, caches, and generated artifacts are not
  tracked by Git.
- Browser extension permissions are limited to context menus, active tab,
  scripting, storage, and localhost host permissions.

## Current Non-Goals

- No cloud sync.
- No memory promotion.
- No keylogging, HTTPS proxying, screen recording, or browser-history scrape.
