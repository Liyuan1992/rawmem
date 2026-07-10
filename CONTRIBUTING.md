# Contributing to rawmem

`rawmem` is a small local-first evidence ledger. Contributions should keep the
source layer append-only, dependency-light, inspectable, and separate from any
reviewed or derived memory layer.

## Development

The maintained development and CI platform is Windows. The dependency-free
core may work elsewhere, but startup integration and release gates are only
verified on Windows today.

```powershell
python -m pip install -e .
$env:PYTHONPATH = "src"
python -m compileall -q src tests scripts
python -m unittest discover -s tests
python scripts/open_source_audit.py
```

Run a real CLI smoke test with a temporary ledger before opening a pull request.

## Change Guidelines

- Prefer a small adapter that emits the shared event schema.
- Keep background capture opt-in and local by default.
- Do not add keylogging, network interception, screen recording, or automatic
  memory promotion to the core project.
- Preserve append-only ledger behavior. Corrections should be new events.
- Add focused tests for setup, capture, privacy, and uninstall behavior.
- Never commit ledgers, tokens, private transcripts, logs, or workstation paths.

Bug reports should include redacted `rawmem doctor` output when possible.
