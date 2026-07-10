"""Local-first raw evidence ledger."""

from .ledger import (
    CURSOR_SCHEMA,
    EVENT_BATCH_SCHEMA,
    SCHEMA,
    EventBatch,
    LedgerCursor,
    VerificationResult,
    append_event,
    iter_events,
    ledger_identity,
    verify_ledger,
)

__version__ = "0.6.0"

__all__ = [
    "SCHEMA",
    "CURSOR_SCHEMA",
    "EVENT_BATCH_SCHEMA",
    "LedgerCursor",
    "EventBatch",
    "VerificationResult",
    "append_event",
    "iter_events",
    "ledger_identity",
    "verify_ledger",
    "__version__",
]
