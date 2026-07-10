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
    rotate_ledger,
    verify_ledger,
)
from .privacy import CaptureDecision, CapturePolicy

__version__ = "0.6.1"

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
    "rotate_ledger",
    "verify_ledger",
    "CaptureDecision",
    "CapturePolicy",
    "__version__",
]
