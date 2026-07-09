"""Opt-in clipboard polling.

Copying something is one of the strongest "this matters to me" signals a
human emits while working. The poller reads the Windows clipboard via
ctypes (no subprocess per poll), dedupes by content hash, and baselines the
current clipboard on first run so stale pre-daemon content is not captured.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any, Callable

from .ledger import build_event, summarize
from .tailers import TailState

CF_UNICODETEXT = 13


def read_clipboard_text() -> str | None:
    """Return clipboard text, or None when unavailable/non-text/locked."""
    if not sys.platform.startswith("win"):
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]

    if not user32.OpenClipboard(0):
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


class ClipboardTailer:
    """Poll the clipboard and emit an event when its text content changes."""

    name = "clipboard"

    def __init__(
        self,
        *,
        max_chars: int = 20000,
        reader: Callable[[], str | None] | None = None,
    ) -> None:
        self.max_chars = max_chars
        self.reader = reader or read_clipboard_text

    def poll(self, state: TailState) -> list[dict[str, Any]]:
        tstate = state.tailer(self.name)
        values = tstate["values"]
        try:
            text = self.reader()
        except OSError:
            return []
        if not text or not text.strip():
            return []
        digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        if digest == values.get("last_hash"):
            return []
        first_run = not tstate["initialized"]
        values["last_hash"] = digest
        tstate["initialized"] = True
        if first_run:
            # Baseline: whatever was on the clipboard before the daemon
            # started may be stale or private; only capture changes.
            return []
        truncated = len(text) > self.max_chars
        stored = text[: self.max_chars]
        return [
            build_event(
                source="clipboard",
                event_type="clipboard_clip",
                project="clipboard",
                cwd=Path.home(),
                summary=summarize(stored),
                raw_text=stored,
                tags=["clipboard"],
                payload={"chars": len(text), "truncated": truncated},
            )
        ]
