"""Strategy 0 (Envelope Index) vs JXA fallback must agree on IDs.

The #102/#103 bug was two code paths implementing one contract with
nothing pinning them together: the Envelope Index fast path silently
returned [] for a Gmail INBOX while the JXA path would have returned
the mail. This test forces get_emails down each path for the same
mailbox and asserts they yield the same message IDs — so a future
divergence (e.g. returning message_id instead of ROWID, or mis-scoping
an account) fails loudly.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apple_mail_mcp.index.accounts import AccountMap


@pytest.fixture(autouse=True)
def _reset_map():
    AccountMap.get_instance().reset()
    yield
    AccountMap.get_instance().reset()


@pytest.fixture
def gmail_index(tmp_path: Path) -> Path:
    """Minimal Gmail-shaped Envelope Index (INBOX via labels only)."""
    db = tmp_path / "Envelope Index"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE subjects (ROWID INTEGER PRIMARY KEY, subject TEXT);
        CREATE TABLE addresses (
            ROWID INTEGER PRIMARY KEY, address TEXT, comment TEXT
        );
        CREATE TABLE mailboxes (ROWID INTEGER PRIMARY KEY, url TEXT);
        CREATE TABLE messages (
            ROWID INTEGER PRIMARY KEY, message_id INTEGER, sender INTEGER,
            subject INTEGER, date_received INTEGER, mailbox INTEGER,
            read INTEGER DEFAULT 0, flagged INTEGER DEFAULT 0,
            deleted INTEGER DEFAULT 0
        );
        CREATE TABLE labels (
            message_id INTEGER, mailbox_id INTEGER,
            PRIMARY KEY (message_id, mailbox_id)
        ) WITHOUT ROWID;
        INSERT INTO subjects VALUES (1, 'A'), (2, 'B');
        INSERT INTO addresses VALUES (1, 'x@y.com', '');
        INSERT INTO mailboxes VALUES
          (1, 'imap://ACCOUNT-G/%5BGmail%5D/All%20Mail'),
          (2, 'imap://ACCOUNT-G/INBOX');
        INSERT INTO messages VALUES
          (1, 100, 1, 1, 800000000, 1, 0, 0, 0),
          (2, 200, 1, 2, 800001000, 1, 1, 0, 0);
        INSERT INTO labels VALUES (1, 2), (2, 2);
        """
    )
    conn.commit()
    conn.close()
    return db


@pytest.mark.asyncio
async def test_strategy0_and_jxa_agree_on_ids(gmail_index, tmp_path):
    from apple_mail_mcp import server

    AccountMap.get_instance().load_from_jxa(
        [{"name": "Gmail", "id": "ACCOUNT-G"}]
    )

    # --- Strategy 0: real synthetic Envelope Index ---
    with (
        patch(
            "apple_mail_mcp.index.disk.find_mail_directory",
            return_value=tmp_path,
        ),
        patch(
            "apple_mail_mcp.index.envelope_direct.envelope_index_path",
            return_value=gmail_index,
        ),
    ):
        s0 = await server.get_emails(account="Gmail", mailbox="INBOX")
    s0_ids = {e["id"] for e in s0}

    # --- JXA fallback: force Strategy 0 unavailable, mock the query.
    # JXA's msg.id() returns the same ROWIDs Strategy 0 returns, so a
    # correct implementation yields the same id set. ---
    jxa_rows = [
        {
            "id": 1,
            "subject": "A",
            "sender": "x@y.com",
            "date_received": "1995-05-09T...",
            "read": False,
            "flagged": False,
        },
        {
            "id": 2,
            "subject": "B",
            "sender": "x@y.com",
            "date_received": "1995-05-09T...",
            "read": True,
            "flagged": False,
        },
    ]
    with (
        patch(
            "apple_mail_mcp.index.disk.find_mail_directory",
            return_value=tmp_path,
        ),
        patch(
            "apple_mail_mcp.index.envelope_direct.envelope_index_path",
            return_value=Path("/nonexistent/Envelope Index"),
        ),
        patch(
            "apple_mail_mcp.server.execute_query_async",
            new=AsyncMock(return_value=jxa_rows),
        ),
    ):
        s1 = await server.get_emails(account="Gmail", mailbox="INBOX")
    s1_ids = {e["id"] for e in s1}

    assert s0_ids == {1, 2}, "Strategy 0 must return ROWID-based IDs"
    assert s0_ids == s1_ids, (
        f"Fast path {s0_ids} and JXA path {s1_ids} disagree — "
        "the #103 divergence class has returned"
    )


@pytest.mark.parametrize("jxa_strategy", [1, 2, 3])
@pytest.mark.asyncio
async def test_get_email_recipients_agree_across_strategies(
    tmp_path, monkeypatch, jxa_strategy
):
    """get_email's To/Cc from the .emlx headers (Strategy 0) must match
    what each JXA strategy returns, and every JXA script must ask for
    them — all strategies share one response schema."""
    from apple_mail_mcp import server

    monkeypatch.setenv("APPLE_MAIL_INDEX_EXCLUDE_ACCOUNTS", "")
    AccountMap.get_instance().load_from_jxa([{"name": "Work", "id": "W"}])

    mime = (
        b"From: sender@example.com\n"
        b'To: "Doe, Jane" <jane@example.com>\n'
        b"Cc: peer@example.com\n\n"
        b"Body\n"
    )
    path = tmp_path / "42.emlx"
    path.write_bytes(f"{len(mime)}\n".encode() + mime)
    manager = MagicMock()
    manager.has_index.return_value = True
    manager.find_email_path.return_value = path
    manager.get_email_attachments.return_value = []

    # --- Strategy 0: real .emlx parse ---
    with patch.object(server, "_get_index_manager", return_value=manager):
        disk_result = await server.get_email(42, account="Work")

    # --- JXA: Strategy 0 misses; earlier JXA strategies fail ---
    manager.find_email_path.return_value = None
    manager.find_email_location.return_value = ("W", "Archive")
    jxa_result = {
        "id": 42,
        "to": [{"name": "Doe, Jane", "address": "jane@example.com"}],
        "cc": [{"name": "", "address": "peer@example.com"}],
    }
    failures = [RuntimeError("not found")] * (jxa_strategy - 1)
    with (
        patch.object(server, "_get_index_manager", return_value=manager),
        patch.object(
            server,
            "execute_with_core_async",
            new=AsyncMock(side_effect=[*failures, jxa_result]),
        ) as jxa,
    ):
        result = await server.get_email(42, account="Work")

    assert jxa.call_count == jxa_strategy
    for field in ("to", "cc"):
        assert result[field] == disk_result[field]
        for call in jxa.call_args_list:
            assert f"{field}: MailCore.getRecipients" in call.args[0]
