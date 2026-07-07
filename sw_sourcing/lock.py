"""Prevents overlapping runs of the same command via a non-blocking file
lock.

A slow scan (large backlog, many sequential vision calls) can still be
running when cron fires the next one. Without this, two processes could
race on the same SQLite file -- both checking "have I seen this listing?"
before either records the answer -- and double-process it.
"""

from __future__ import annotations

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def acquire(lock_path: Path | str) -> Iterator[bool]:
    """Yields True if the lock was acquired, False if another process
    already holds it. The caller decides what to do with False (e.g. skip
    this run and exit cleanly).
    """
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    file_obj = path.open("w")
    acquired = False
    try:
        try:
            fcntl.flock(file_obj, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            acquired = False
        yield acquired
    finally:
        if acquired:
            fcntl.flock(file_obj, fcntl.LOCK_UN)
        file_obj.close()
