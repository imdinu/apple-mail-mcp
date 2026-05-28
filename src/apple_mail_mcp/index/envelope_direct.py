"""Direct reads against Apple's Envelope Index SQLite.

Used by the Strategy-0 fast paths in `list_accounts()` and
`get_emails()`. Skips JXA round-trips entirely by querying the
same SQLite database BastianZim/rusty/pl-lyfx read.

The Envelope Index lives at
`~/Library/Mail/V<N>/MailData/Envelope Index` and is maintained
by Mail.app. It uses lookup tables for high-cardinality string
columns (`messages.subject` is an FK into `subjects.ROWID`;
`messages.sender` is an FK into `addresses.ROWID`), and stores
read/flagged/deleted as direct integer columns on `messages`,
which lets us serve every `get_emails()` filter without a JXA
fallback for live state.

Account display names are NOT stored in this database. The
caller (server.py) resolves account name -> mailbox-URL UUID via
the AccountMap singleton, which is hydrated from the
`list_accounts()` tool's JXA call. So this module deals in UUIDs
on the way in (when resolving a target mailbox) and emits
display names on the way out via the same AccountMap.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EnvelopeMessageRow:
    """One row from the Envelope Index, normalized to text fields."""

    message_id: int
    subject: str
    sender: str
    date_received: str  # ISO format
    mailbox_url: str
    account_uuid: str
    mailbox_name: str
    read: bool
    flagged: bool


def _unix_ts_to_iso(ts: int | float | None) -> str:
    """Convert Mail V10's Envelope Index date_received to ISO 8601.

    On macOS 14+ / Mail V10, `messages.date_received` is a Unix
    timestamp (seconds since 1970-01-01 UTC), not a Core Data
    timestamp. Empirically verified by cross-checking against the
    `Date:` MIME header parsed from the corresponding .emlx file.
    """
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(float(ts), tz=UTC).isoformat()
    except (ValueError, OverflowError):
        return ""


def _parse_mailbox_url(url: str) -> tuple[str, str]:
    """Pull account UUID and mailbox name out of a mailbox URL.

    Mail.app URLs look like `ews://<UUID>/<mailbox-name>` or
    `imap://<UUID>/<mailbox-name>`. The scheme varies; the shape
    after `://` is consistent.

    Returns ("", "") if the URL doesn't parse.
    """
    if not url or "://" not in url:
        return ("", "")
    _, _, rest = url.partition("://")
    parts = rest.split("/", 1)
    if len(parts) == 2:
        return (parts[0], parts[1].replace("%20", " "))
    return (parts[0], "")


def envelope_index_path(mail_dir: Path) -> Path:
    """Return the canonical Envelope Index location.

    `mail_dir` is `~/Library/Mail/V<N>/` (the version-specific
    root). The Envelope Index lives in its sibling `MailData/`.
    """
    return mail_dir / "MailData" / "Envelope Index"


def list_account_uuids(envelope_path: Path) -> list[str]:
    """Enumerate account UUIDs by scanning distinct mailbox URLs.

    Each mailbox URL embeds its account UUID. We DISTINCT over
    the leading authority component of the URL. Returns an
    ordered list (sorted by UUID) for stable enumeration.

    Raises sqlite3.OperationalError on a schema mismatch — the
    caller falls back to JXA.
    """
    conn = sqlite3.connect(f"file:{envelope_path}?mode=ro", uri=True)
    try:
        cur = conn.execute(
            "SELECT DISTINCT url FROM mailboxes WHERE url IS NOT NULL"
        )
        uuids: set[str] = set()
        for (url,) in cur:
            uuid, _ = _parse_mailbox_url(url)
            if uuid:
                uuids.add(uuid)
        return sorted(uuids)
    finally:
        conn.close()


def fetch_recent_messages(
    envelope_path: Path,
    *,
    account_uuid: str | None,
    mailbox_name: str | None,
    filter_kind: str,
    limit: int,
) -> list[EnvelopeMessageRow]:
    """Read up to `limit` recent messages via direct SQL.

    Joins through `subjects` and `addresses` to materialize text
    columns; honors read/flagged/date filters as WHERE clauses on
    the direct integer columns of `messages`.

    Args:
        envelope_path: Path to Apple's Envelope Index SQLite.
        account_uuid: Account UUID to restrict to, or None for
            all accounts.
        mailbox_name: Mailbox name to restrict to (matched as a
            suffix of the mailbox URL), or None for all mailboxes
            within the account.
        filter_kind: One of "all", "unread", "flagged", "today",
            "last_7_days", "this_week".
        limit: Maximum rows to return.

    Returns:
        List of EnvelopeMessageRow ordered by date_received DESC.

    Raises:
        sqlite3.OperationalError: On schema mismatch. Caller
            should fall back to JXA.
    """
    where_clauses: list[str] = ["m.deleted = 0"]
    params: list[object] = []

    if account_uuid:
        if mailbox_name:
            where_clauses.append("mb.url LIKE ?")
            params.append(f"%://{account_uuid}/{mailbox_name}%")
        else:
            where_clauses.append("mb.url LIKE ?")
            params.append(f"%://{account_uuid}/%")

    if filter_kind == "unread":
        where_clauses.append("m.read = 0")
    elif filter_kind == "flagged":
        where_clauses.append("m.flagged = 1")
    elif filter_kind in ("today", "last_7_days", "this_week"):
        # date_received is Unix epoch (see _unix_ts_to_iso); compare
        # against a Unix-epoch threshold of "now - delta".
        now_ts = datetime.now(tz=UTC).timestamp()
        delta_seconds = 86400 if filter_kind == "today" else 7 * 86400
        where_clauses.append("m.date_received >= ?")
        params.append(now_ts - delta_seconds)
    # "all": no extra clause

    where_sql = " AND ".join(where_clauses)
    # We return `m.ROWID`, not `m.message_id`. Mail.app's
    # JXA `msg.id()` returns the SQLite ROWID (a small,
    # locally-assigned integer Mail.app uses as the primary
    # internal message identifier). The Envelope Index's
    # `message_id` column is a 63-bit hash of the RFC822
    # Message-ID header — useful for global identity, but
    # would (a) lose precision through JSON → JXA float
    # coercion and (b) not round-trip through get_email(),
    # which expects Mail.app's small-integer ID.
    sql = f"""
        SELECT
            m.ROWID       AS message_id,
            s.subject     AS subject,
            a.address     AS sender,
            m.date_received AS date_received,
            mb.url        AS mailbox_url,
            m.read        AS read_flag,
            m.flagged     AS flagged_flag
        FROM messages m
        LEFT JOIN subjects  s  ON m.subject = s.ROWID
        LEFT JOIN addresses a  ON m.sender  = a.ROWID
        LEFT JOIN mailboxes mb ON m.mailbox = mb.ROWID
        WHERE {where_sql}
        ORDER BY m.date_received DESC
        LIMIT ?
    """
    params.append(int(limit))

    conn = sqlite3.connect(f"file:{envelope_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(sql, params)
        rows: list[EnvelopeMessageRow] = []
        for r in cur:
            uuid, mailbox_n = _parse_mailbox_url(r["mailbox_url"] or "")
            rows.append(
                EnvelopeMessageRow(
                    message_id=int(r["message_id"]) if r["message_id"] else 0,
                    subject=r["subject"] or "",
                    sender=r["sender"] or "",
                    date_received=_unix_ts_to_iso(r["date_received"]),
                    mailbox_url=r["mailbox_url"] or "",
                    account_uuid=uuid,
                    mailbox_name=mailbox_n,
                    read=bool(r["read_flag"]),
                    flagged=bool(r["flagged_flag"]),
                )
            )
        return rows
    finally:
        conn.close()
