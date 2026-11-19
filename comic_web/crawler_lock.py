from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional


@contextmanager
def crawler_lock(
    path: Optional[Path],
    stop_event: Optional[threading.Event] = None,
) -> Iterator[bool]:
    """Serialize image_crawler subprocesses across download workers."""
    if path is None:
        yield True
        return

    lock_path = path.expanduser().resolve()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    acquired = False
    try:
        try:
            import fcntl
        except ImportError:
            yield True
            return

        while True:
            if stop_event is not None and stop_event.is_set():
                yield False
                return
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                time.sleep(0.2)
            except OSError:
                yield False
                return
        yield True
    finally:
        if acquired:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
        handle.close()
