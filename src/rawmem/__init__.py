"""Local-first raw evidence ledger."""

from .archive import (
    iter_archive_events,
    list_archives,
    seal_ledger,
    verify_sealed_archive,
)
from .archive_format import SealedArchiveError
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

__version__ = "0.6.2"

__all__ = [
    "SCHEMA",
    "CURSOR_SCHEMA",
    "EVENT_BATCH_SCHEMA",
    "LedgerCursor",
    "EventBatch",
    "VerificationResult",
    "SealedArchiveError",
    "append_event",
    "iter_archive_events",
    "iter_events",
    "ledger_identity",
    "list_archives",
    "rotate_ledger",
    "seal_ledger",
    "verify_ledger",
    "verify_sealed_archive",
    "CaptureDecision",
    "CapturePolicy",
    "__version__",
]
