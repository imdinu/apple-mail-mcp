"""Cross-process single-writer lock for the index database.

SQLite's WAL mode serializes writes at the storage layer, but it cannot
stop two *processes* from each running reconciliation and a file watcher
against the same database — they treat each other's writes as drift and
revert them in a ping-pong loop (#106). Claude Desktop spawns every MCP
server twice, so this is the default deployment, not an edge case.

IndexLock is an advisory ``fcntl.flock`` on a lockfile next to the
database. The process holding it is the sole index writer; other server
instances run index-passive and periodically retry (promotion). The
kernel releases the lock when the holding process exits or crashes, so
there is no stale-lockfile cleanup.

The lock is per-database (keyed on the db path), so a custom
``APPLE_MAIL_INDEX_PATH`` gets its own lock. It is orthogonal to the
``read_only`` server flag, which gates JXA mail mutations — an
index-passive instance still performs mail actions normally.
"""

import fcntl
import logging
import os
import time
from pathlib import Path
from types import TracebackType

logger = logging.getLogger(__name__)

# Poll interval for the blocking acquire (flock has no native timeout).
_POLL_INTERVAL_SEC = 0.2


class IndexLock:
    """Advisory exclusive lock on ``<db_path>.lock``.

    The open file descriptor IS the lock: flock is tied to the open
    file description, so the instance must be kept alive for as long
    as the lock is held — dropping the last reference closes the fd
    and silently releases the lock.
    """

    def __init__(self, db_path: Path):
        self.lock_path = db_path.parent / (db_path.name + ".lock")
        self._fd: int | None = None

    @property
    def locked(self) -> bool:
        """Whether this instance currently holds the lock."""
        return self._fd is not None

    def try_acquire(self) -> bool:
        """Attempt to acquire the lock without blocking.

        Returns True if acquired (or already held by this instance),
        False if another process holds it.
        """
        if self._fd is not None:
            return True
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def acquire(self, timeout: float) -> bool:
        """Acquire the lock, waiting up to ``timeout`` seconds.

        flock offers blocking or instant-fail but no timeout, so this
        polls ``try_acquire`` until the deadline. Returns True on
        success, False on timeout.
        """
        deadline = time.monotonic() + timeout
        while True:
            if self.try_acquire():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(_POLL_INTERVAL_SEC)

    def release(self) -> None:
        """Release the lock. Safe to call when not held.

        Closing the fd both unlocks and frees the descriptor; the
        lockfile itself is left in place for the next contender.
        """
        if self._fd is None:
            return
        os.close(self._fd)
        self._fd = None

    def __enter__(self) -> "IndexLock":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
