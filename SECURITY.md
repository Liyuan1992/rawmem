# Security Policy

## Reporting

Please report security or privacy issues privately before public disclosure.
If this repository is mirrored to GitHub, use the repository owner's preferred
private contact method or GitHub private vulnerability reporting when enabled.

## Scope

Sensitive areas include:

- unintended upload or network sync;
- global Git hook installation and chaining;
- Windows scheduled task startup behavior;
- browser extension capture behavior;
- clipboard capture behavior;
- ledger writes that accidentally include secrets.

## Design Constraints

- No network upload is enabled by default.
- The HTTP capture endpoint binds to `127.0.0.1` by default.
- Global machine changes require explicit commands.
- Clipboard polling is disabled by default.
- Raw captured events remain review-required; they are not accepted memory.

## Maintainer Checklist

Before publishing a release:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests
python scripts/open_source_audit.py
```
