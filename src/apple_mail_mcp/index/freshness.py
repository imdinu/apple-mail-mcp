"""Keeping the index current when more than one process is involved.

Only the process holding ``IndexLock`` may write the index (#106), and
a server started by an MCP host usually holds it for its lifetime. Two
things follow, and this module handles both:

* A server that cannot read ``~/Library/Mail`` (no Full Disk Access)
  must not take the lock: it can never sync, and holding the lock only
  stops a terminal that *can* read the mail from refreshing the index.
  ``mail_readable()`` is checked before acquiring.

* Anyone else who wants a fresh index (the ``index`` CLI, a passive
  server about to answer a search) asks the writer to sync instead of
  fighting it for the lock. The request is a timestamp written to
  ``<db>.sync-request``; the writer polls it, runs an incremental sync
  and writes ``<db>.sync-done`` as JSON
  ``{"request": <stamp>, "changes": <n>, "at": <time>, "error": ...}``.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .manager import IndexManager

logger = logging.getLogger(__name__)

# Serialises syncs inside one process (startup sync, request loop,
# sync-before-search). The cross-process guard is IndexLock.
SYNC_LOCK = threading.Lock()

# Set while this process holds the IndexLock (serve sets it on winning
# or promoting), so in-process code knows it may sync directly.
WRITER_LOCK_HELD = threading.Event()

_POLL_SEC = 0.25


def _request_path(db_path: Path) -> Path:
    return db_path.parent / (db_path.name + ".sync-request")


def _done_path(db_path: Path) -> Path:
    return db_path.parent / (db_path.name + ".sync-done")


def mail_readable() -> bool:
    """Whether this process can list the Mail store (Full Disk Access)."""
    from .disk import find_mail_directory

    try:
        os.listdir(find_mail_directory())
    except (OSError, PermissionError):
        return False
    return True


def lock_held_elsewhere(db_path: Path) -> bool:
    """Whether another process holds the index writer lock right now.

    Probes with a separate descriptor and releases at once, so it never
    keeps the lock. A caller that holds the lock itself sees False.
    """
    lock_path = db_path.parent / (db_path.name + ".lock")
    try:
        fd = os.open(lock_path, os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def request_sync(db_path: Path) -> float:
    """Ask the writer process to sync. Returns the request stamp."""
    stamp = time.time()
    _request_path(db_path).write_text(repr(stamp))
    return stamp


def read_done(db_path: Path) -> dict | None:
    try:
        return json.loads(_done_path(db_path).read_text())
    except (OSError, ValueError):
        return None


def wait_for_sync(db_path: Path, stamp: float, timeout: float) -> dict | None:
    """Wait for the writer to answer request ``stamp``; None on timeout."""
    deadline = time.monotonic() + timeout
    while True:
        done = read_done(db_path)
        if done and done.get("request", 0) >= stamp:
            return done
        if time.monotonic() >= deadline:
            return None
        time.sleep(_POLL_SEC)


def sync_now(manager: IndexManager) -> int:
    """Run one incremental sync, serialised within this process."""
    with SYNC_LOCK:
        return manager.sync_updates()


def serve_sync_requests(
    manager: IndexManager,
    interval: float = 1.0,
    stop: threading.Event | None = None,
) -> None:
    """Writer loop: answer sync requests until ``stop`` is set.

    Runs in a daemon thread of the process holding the writer lock.
    """
    req, handled = _request_path(manager.db_path), 0.0
    while not (stop and stop.is_set()):
        try:
            stamp = float(req.read_text() or 0)
        except (OSError, ValueError):
            stamp = 0.0
        if stamp > handled and manager.index_writer:
            handled = stamp
            done: dict = {"request": stamp}
            try:
                done["changes"] = sync_now(manager)
                if manager.last_sync_blocked:
                    done["error"] = (
                        f"writer cannot read the Mail store "
                        f"({manager.last_sync_blocked})"
                    )
            except Exception as e:  # reported to the requester
                logger.warning("Requested sync failed: %s", e)
                done["error"] = str(e)
            done["at"] = time.time()
            _done_path(manager.db_path).write_text(json.dumps(done))
        time.sleep(interval)
