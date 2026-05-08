"""Disk-based sync for email index.

Syncs the index with the current state of emails on disk using
state reconciliation (comparing disk inventory with DB inventory).

SECURITY NOTE: All strings passed to JXA are serialized via json.dumps()
to prevent injection attacks.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import get_index_max_emails
from .schema import (
    CLEAR_PARSE_FAILURE_SQL,
    INSERT_EMAIL_SQL,
    RECORD_PARSE_FAILURE_SQL,
    email_to_row,
    insert_attachments,
    parse_failure_row,
)

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Result of a disk-based sync operation."""

    added: int
    deleted: int
    moved: int
    errors: int

    @property
    def total_changes(self) -> int:
        return self.added + self.deleted + self.moved


def get_db_inventory(
    conn: sqlite3.Connection,
) -> dict[tuple[str, str, int], str]:
    """
    Get inventory of all emails in the database.

    Args:
        conn: Database connection

    Returns:
        Dict mapping (account, mailbox, msg_id) -> emlx_path (or "" if NULL)
    """
    cursor = conn.execute(
        "SELECT account, mailbox, message_id, emlx_path FROM emails"
    )

    inventory: dict[tuple[str, str, int], str] = {}
    for row in cursor:
        key = (row["account"], row["mailbox"], row["message_id"])
        inventory[key] = row["emlx_path"] or ""

    return inventory


def sync_from_disk(
    conn: sqlite3.Connection,
    mail_dir: Path,
    progress_callback: Callable[[int, int | None, str], None] | None = None,
) -> SyncResult:
    """
    Sync index with disk using state reconciliation.

    This is the PRIMARY sync method (replaces JXA-based sync).
    Compares disk inventory with DB inventory via SQL diffing on a
    temp table to detect:
    - NEW: on disk, not in DB → parse & insert
    - DELETED: in DB, not on disk → remove from DB
    - MOVED: same ID, different path → update path

    Memory: streams disk entries directly into a TEMP table rather than
    materializing the full inventory in a Python dict. SQLite handles
    the diffing on disk (or in WAL paging), so peak RAM is bounded by
    the size of the result sets (NEW/DELETED/MOVED) rather than the
    full mailbox inventory. This matters at 200K+ emails where the
    Python-dict approach used 50-100 MB.

    Args:
        conn: Database connection
        mail_dir: Path to ~/Library/Mail/V10/
        progress_callback: Optional callback(current, total, message)

    Returns:
        SyncResult with counts of added/deleted/moved emails
    """
    from .disk import iter_disk_inventory, parse_emlx

    if progress_callback:
        progress_callback(0, None, "Scanning disk inventory...")

    # Build a TEMP table of (account, mailbox, msg_id, emlx_path) tuples
    # by streaming the disk walk. WITHOUT ROWID + composite PK lets the
    # diff queries below use the index for the JOINs.
    conn.executescript("""
        CREATE TEMP TABLE IF NOT EXISTS disk_inventory_temp (
            account TEXT NOT NULL,
            mailbox TEXT NOT NULL,
            message_id INTEGER NOT NULL,
            emlx_path TEXT NOT NULL,
            PRIMARY KEY(account, mailbox, message_id)
        ) WITHOUT ROWID;
        DELETE FROM disk_inventory_temp;
    """)

    INSERT_TEMP_SQL = (
        "INSERT OR REPLACE INTO disk_inventory_temp "
        "(account, mailbox, message_id, emlx_path) VALUES (?, ?, ?, ?)"
    )
    BATCH_SIZE = 1000
    batch: list[tuple[str, str, int, str]] = []
    inserted = 0
    for entry in iter_disk_inventory(mail_dir):
        batch.append(entry)
        if len(batch) >= BATCH_SIZE:
            conn.executemany(INSERT_TEMP_SQL, batch)
            inserted += len(batch)
            batch.clear()
            if progress_callback and inserted % 5000 == 0:
                progress_callback(
                    inserted, None, f"Scanned {inserted} files..."
                )
    if batch:
        conn.executemany(INSERT_TEMP_SQL, batch)
        inserted += len(batch)

    if progress_callback:
        progress_callback(inserted, None, "Computing diff...")

    # NEW: in disk, not in DB
    new_rows = list(
        conn.execute("""
            SELECT t.account, t.mailbox, t.message_id, t.emlx_path
            FROM disk_inventory_temp t
            LEFT JOIN emails e ON
                e.account = t.account AND
                e.mailbox = t.mailbox AND
                e.message_id = t.message_id
            WHERE e.rowid IS NULL
        """)
    )

    # DELETED: in DB, not in disk
    deleted_rows = list(
        conn.execute("""
            SELECT e.account, e.mailbox, e.message_id
            FROM emails e
            LEFT JOIN disk_inventory_temp t ON
                t.account = e.account AND
                t.mailbox = e.mailbox AND
                t.message_id = e.message_id
            WHERE t.message_id IS NULL
        """)
    )

    # MOVED: in both, paths differ (and DB had a non-NULL path to begin with)
    moved_rows = list(
        conn.execute("""
            SELECT t.account, t.mailbox, t.message_id, t.emlx_path
            FROM disk_inventory_temp t
            INNER JOIN emails e ON
                e.account = t.account AND
                e.mailbox = t.mailbox AND
                e.message_id = t.message_id
            WHERE e.emlx_path IS NOT NULL
              AND e.emlx_path != t.emlx_path
        """)
    )

    total_ops = len(new_rows) + len(deleted_rows) + len(moved_rows)

    if progress_callback:
        progress_callback(
            0,
            total_ops,
            f"Syncing: {len(new_rows)} new, {len(deleted_rows)} deleted, "
            f"{len(moved_rows)} moved",
        )

    logger.info(
        "Sync diff: %d new, %d deleted, %d moved",
        len(new_rows),
        len(deleted_rows),
        len(moved_rows),
    )

    added = 0
    deleted = 0
    moved = 0
    errors = 0
    processed = 0

    max_per_mailbox = get_index_max_emails()
    mailbox_counts: dict[tuple[str, str], int] = {}

    # Get current counts per mailbox
    cursor = conn.execute(
        "SELECT account, mailbox, COUNT(*) as cnt FROM emails "
        "GROUP BY account, mailbox"
    )
    for row in cursor:
        mailbox_counts[(row["account"], row["mailbox"])] = row["cnt"]

    # Sort new emails by mtime (newest first) so the per-mailbox cap keeps
    # the recent ones. Race-tolerant: files deleted between discovery and
    # sort get mtime=0 and sink to the bottom.
    def _safe_mtime(p: str) -> float:
        try:
            return Path(p).stat().st_mtime
        except OSError:
            return 0

    sorted_new = sorted(
        new_rows,
        key=lambda r: _safe_mtime(r["emlx_path"]),
        reverse=True,
    )

    skipped_per_mailbox: dict[tuple[str, str], int] = {}

    # Process NEW emails (parse content and insert)
    for row in sorted_new:
        account = row["account"]
        mailbox = row["mailbox"]
        path = row["emlx_path"]

        # Check mailbox limit (None means uncapped)
        mb_key = (account, mailbox)
        current_count = mailbox_counts.get(mb_key, 0)
        if max_per_mailbox is not None and current_count >= max_per_mailbox:
            skipped_per_mailbox[mb_key] = skipped_per_mailbox.get(mb_key, 0) + 1
            continue

        try:
            parsed = parse_emlx(Path(path))
            if parsed:
                attachments = parsed.attachments or []
                insert_row = email_to_row(
                    {
                        "id": parsed.id,
                        "subject": parsed.subject,
                        "sender": parsed.sender,
                        "content": parsed.content,
                        "date_received": parsed.date_received,
                    },
                    account,
                    mailbox,
                    path,
                    attachment_count=len(attachments),
                )
                conn.execute(INSERT_EMAIL_SQL, insert_row)

                # Insert attachment metadata
                if attachments:
                    rowid = conn.execute(
                        "SELECT last_insert_rowid()"
                    ).fetchone()[0]
                    insert_attachments(conn, rowid, attachments)

                # Clear any prior DLQ entry — this path parses now.
                conn.execute(CLEAR_PARSE_FAILURE_SQL, (path,))

                added += 1
                mailbox_counts[mb_key] = current_count + 1
        except (OSError, ValueError, UnicodeDecodeError) as e:
            logger.debug("Failed to parse %s: %s", path, e)
            errors += 1
            # Record into the DLQ for visibility / future retry.
            try:
                conn.execute(
                    RECORD_PARSE_FAILURE_SQL,
                    parse_failure_row(path, account, mailbox, e),
                )
            except sqlite3.Error as dlq_err:
                # The DLQ insert itself failed — the failure signal is
                # now lost. Likely indicates a deeper problem (disk
                # full, DB corruption, schema-version mismatch). Log at
                # ERROR so it surfaces in default-config logging
                # instead of being swallowed silently. (#77)
                logger.error(
                    "DLQ write failed for %s — parse failure signal "
                    "lost (cause: %s). Check disk space and DB "
                    "integrity.",
                    path,
                    dlq_err,
                )

        processed += 1
        if progress_callback and processed % 100 == 0:
            progress_callback(processed, total_ops, f"Added {added} emails...")

    # Log aggregate cap warning with summary + per-mailbox detail
    if skipped_per_mailbox:
        total_skipped = sum(skipped_per_mailbox.values())
        logger.warning(
            "%d mailbox(es) hit cap (%d), %d new emails skipped. "
            "Increase APPLE_MAIL_INDEX_MAX_EMAILS to index more.",
            len(skipped_per_mailbox),
            max_per_mailbox,
            total_skipped,
        )
        for mb_key, skipped in skipped_per_mailbox.items():
            logger.debug(
                "  Cap detail: %s/%s — %d skipped",
                mb_key[0],
                mb_key[1],
                skipped,
            )

    # Process DELETED emails (remove from DB)
    for row in deleted_rows:
        account = row["account"]
        mailbox = row["mailbox"]
        msg_id = row["message_id"]
        try:
            conn.execute(
                "DELETE FROM emails WHERE account = ? AND mailbox = ? "
                "AND message_id = ?",
                (account, mailbox, msg_id),
            )
            deleted += 1
        except sqlite3.Error as e:
            logger.debug(
                "Failed to delete (%s, %s, %s): %s",
                account,
                mailbox,
                msg_id,
                e,
            )
            errors += 1

        processed += 1

    # Process MOVED emails (update path)
    for row in moved_rows:
        account = row["account"]
        mailbox = row["mailbox"]
        msg_id = row["message_id"]
        new_path = row["emlx_path"]
        try:
            conn.execute(
                "UPDATE emails SET emlx_path = ? WHERE account = ? "
                "AND mailbox = ? AND message_id = ?",
                (new_path, account, mailbox, msg_id),
            )
            moved += 1
        except sqlite3.Error as e:
            logger.debug(
                "Failed to update path for (%s, %s, %s): %s",
                account,
                mailbox,
                msg_id,
                e,
            )
            errors += 1

        processed += 1

    # Update sync state
    now = datetime.now().isoformat()
    affected_mailboxes: set[tuple[str, str]] = set()
    for row in new_rows:
        affected_mailboxes.add((row["account"], row["mailbox"]))
    for row in deleted_rows:
        affected_mailboxes.add((row["account"], row["mailbox"]))
    for row in moved_rows:
        affected_mailboxes.add((row["account"], row["mailbox"]))

    for account, mailbox in affected_mailboxes:
        count = mailbox_counts.get((account, mailbox), 0)
        conn.execute(
            """INSERT OR REPLACE INTO sync_state
               (account, mailbox, last_sync, message_count)
               VALUES (?, ?, ?, ?)""",
            (account, mailbox, now, count),
        )

    # If no changes but we did a sync, still record a global timestamp
    if not affected_mailboxes:
        conn.execute(
            """INSERT OR REPLACE INTO sync_state
               (account, mailbox, last_sync, message_count)
               VALUES (?, ?, ?, ?)""",
            ("_global", "_sync", now, 0),
        )

    # Free the temp table contents so a long-lived connection doesn't
    # keep the temp pages around between syncs.
    conn.execute("DELETE FROM disk_inventory_temp")

    conn.commit()

    if progress_callback:
        progress_callback(
            total_ops,
            total_ops,
            f"Sync complete: +{added} -{deleted} ~{moved}",
        )

    logger.info(
        "Sync complete: added=%d, deleted=%d, moved=%d, errors=%d",
        added,
        deleted,
        moved,
        errors,
    )

    return SyncResult(added=added, deleted=deleted, moved=moved, errors=errors)
