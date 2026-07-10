"""Small cross-process file lock used by append-only rawmem stores."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator


class FileLockTimeout(TimeoutError):
    """Raised when a rawmem lock cannot be acquired before its deadline."""


def _try_lock(handle: BinaryIO) -> bool:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _unlock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def exclusive_file_lock(
    path: str | Path,
    *,
    timeout_seconds: float = 30.0,
    poll_seconds: float = 0.01,
) -> Iterator[None]:
    """Acquire an OS-backed exclusive lock without relying on lock-file deletion."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = target.open("a+b")
    acquired = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while not acquired:
            acquired = _try_lock(handle)
            if acquired:
                break
            if time.monotonic() >= deadline:
                raise FileLockTimeout(f"Timed out acquiring rawmem lock: {target}")
            time.sleep(max(0.001, poll_seconds))
        yield
    finally:
        if acquired:
            _unlock(handle)
        handle.close()
