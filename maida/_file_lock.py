"""Private local file locks using native backends loaded only when needed."""

from __future__ import annotations

import errno
import os
import time
from contextlib import contextmanager
from importlib import import_module
from pathlib import Path

_BACKEND = "msvcrt" if os.name == "nt" else "fcntl"


@contextmanager
def file_lock(path: Path, process_lock):
    """Serialize threads and processes; never replace or unlink the lock inode."""
    try:
        backend = import_module(_BACKEND)
    except ImportError as exc:
        # Callers can handle unavailable measurement storage like any other
        # local I/O failure, without preventing CLI imports on any platform.
        raise OSError("Native local file locking is unavailable") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0)
    with process_lock, os.fdopen(os.open(path, flags, 0o600), "r+b") as stream:
        if os.name == "posix":
            os.fchmod(stream.fileno(), 0o600)
        if _BACKEND == "msvcrt":
            # locking() locks bytes from the current position, even beyond EOF.
            # LK_LOCK stops retrying after ten seconds; use nonblocking retries
            # so another process holding the lock cannot silently lose updates.
            while True:
                stream.seek(0)
                try:
                    backend.locking(stream.fileno(), backend.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise
                    time.sleep(0.05)
        else:
            backend.flock(stream.fileno(), backend.LOCK_EX)
        try:
            yield
        finally:
            if _BACKEND == "msvcrt":
                stream.seek(0)
                backend.locking(stream.fileno(), backend.LK_UNLCK, 1)
            else:
                backend.flock(stream.fileno(), backend.LOCK_UN)
