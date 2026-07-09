# AGENTS.md - rawmem

This project is a local-first raw evidence ledger for AI and human workflows.

## Product Boundary

- Preserve raw evidence first; derive memories later.
- The append-only ledger is the source layer, not the reviewed memory layer.
- Do not auto-promote captured events into durable user memory.
- Keep capture explicit, local, and inspectable. Background integrations must be opt-in.
- Avoid invasive capture patterns such as keylogging, full-screen recording, or network interception unless the user explicitly requests a separate experiment.

## Engineering Defaults

- Keep the core package dependency-free unless a dependency removes meaningful complexity.
- Prefer small adapters that write into the shared event schema over one monolithic collector.
- Store private local runtime data under `.rawmem/` or `data/private/`, both ignored by Git.
- Use append-only writes for ledgers. If a fact is wrong, add a correction event instead of rewriting history.

## Verification

- Run `python -m unittest discover -s tests` after code changes.
- Run a real CLI smoke test with a temporary ledger before claiming the project works.
