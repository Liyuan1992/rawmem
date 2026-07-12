# AGENTS.md - rawmem

This project is a local-first raw evidence ledger for AI and human workflows.

## Open-Source Generality And Privacy Boundary

- `rawmem` is a public, reusable library. Its core contracts must remain useful
  without DigitalSelf, memdsl, or any one user's machine, data layout, or
  workflow.
- Keep DigitalSelf-specific adapters, configuration, migration orchestration,
  product policy, and UI behavior in the DigitalSelf repository. A change may
  enter `rawmem` only when it can be expressed as a generic ledger/capture API
  with standalone documentation and tests.
- Do not import DigitalSelf or memdsl from the core package. Cross-project
  integration belongs in adapters at the consuming-project boundary.
- Never commit or publish real ledgers, sealed archives, event payloads, local
  configuration, `.env` files, credentials, user memory, logs, databases,
  backups, machine-specific absolute paths, or live IDs/hashes.
- Tests, examples, documentation, wheels, sdists, and extension archives must
  use small, explicitly synthetic fixtures with fictional identities and fake
  credentials. Inspect release contents before publishing; `.gitignore` alone
  is not sufficient evidence that a package is safe.
- If a requested feature is useful only for one private deployment, implement
  it outside this repository or pause and ask for an explicit public,
  implementation-neutral contract.

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
