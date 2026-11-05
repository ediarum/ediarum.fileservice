"""One lock per project, so a burst of posts, a check's comparison or a
reload can't work on the same checkout at once. The app keeps two sets: one for
the mount (``POST /files`` takes it), one for git operations (see
:mod:`ediarum_fileservice.check`).
"""

import threading
from contextlib import contextmanager


class ProjectLocks:
    "A lock per project, created on first use."

    def __init__(self):
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock_for(self, project: str) -> threading.Lock:
        "The project's lock, created if needed."
        with self._guard:
            return self._locks.setdefault(project, threading.Lock())

    @contextmanager
    def acquire(self, project: str, timeout: float = 30.0):
        "Hold the project's lock; ``TimeoutError`` after ``timeout`` seconds."
        lock = self._lock_for(project)
        if not lock.acquire(timeout=timeout):
            raise TimeoutError(f"{project}: a sync is already in progress")
        try:
            yield
        finally:
            lock.release()
