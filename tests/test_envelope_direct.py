"""Tests for the Strategy-0 Envelope Index fast path."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from apple_mail_mcp.index.envelope_direct import (
    _core_data_to_iso,
    _parse_mailbox_url,
    envelope_index_path,
    fetch_recent_messages,
    list_account_uuids,
)

# ─── Pure helpers ────────────────────────────────────────────


class TestCoreDataToIso:
    def test_none_returns_empty(self):
        assert _core_data_to_iso(None) == ""

    def test_zero_is_core_data_epoch(self):
        # 0 seconds since 2001-01-01 -> ISO of that exact moment
        result = _core_data_to_iso(0)
        assert result.startswith("2001-01-01T00:00:00")

    def test_round_trip_known_date(self):
        # 2026-05-28 12:00:00 UTC -> Core Data ts -> ISO -> verify
        known = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)
        epoch = datetime(2001, 1, 1, tzinfo=UTC)
        ts = (known - epoch).total_seconds()
        assert "2026-05-28T12:00:00" in _core_data_to_iso(ts)

    def test_garbage_returns_empty(self):
        assert _core_data_to_iso("not-a-number") == ""  # type: ignore[arg-type]


class TestParseMailboxUrl:
    def test_ews_url(self):
        assert _parse_mailbox_url("ews://UUID-123/Inbox") == (
            "UUID-123",
            "Inbox",
        )

    def test_imap_url(self):
        assert _parse_mailbox_url("imap://UUID-456/Archive") == (
            "UUID-456",
            "Archive",
        )

    def test_url_with_percent_encoded_space(self):
        _, mb = _parse_mailbox_url("ews://UUID/Deleted%20Items")
        assert mb == "Deleted Items"

    def test_uuid_only(self):
        assert _parse_mailbox_url("ews://UUID-789/") == ("UUID-789", "")

    def test_empty(self):
        assert _parse_mailbox_url("") == ("", "")

    def test_malformed(self):
        assert _parse_mailbox_url("not-a-url") == ("", "")


class TestEnvelopeIndexPath:
    def test_path_construction(self, tmp_path):
        mail_dir = tmp_path / "V10"
        mail_dir.mkdir()
        result = envelope_index_path(mail_dir)
        assert result == mail_dir / "MailData" / "Envelope Index"


# ─── SQLite-backed tests ─────────────────────────────────────


@pytest.fixture
def fake_envelope(tmp_path: Path) -> Path:
    """Tiny in-place Envelope Index with the columns we read."""
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
            ROWID INTEGER PRIMARY KEY,
            message_id INTEGER,
            sender INTEGER,
            subject INTEGER,
            date_received INTEGER,
            mailbox INTEGER,
            read INTEGER DEFAULT 0,
            flagged INTEGER DEFAULT 0,
            deleted INTEGER DEFAULT 0
        );

        INSERT INTO subjects VALUES (1, 'Hello'), (2, 'World');
        INSERT INTO addresses VALUES
          (1, 'alice@example.com', ''),
          (2, 'bob@example.com', '');
        INSERT INTO mailboxes VALUES
          (1, 'ews://ACCOUNT-A/Inbox'),
          (2, 'ews://ACCOUNT-A/Sent'),
          (3, 'imap://ACCOUNT-B/Inbox');

        -- Three messages: two unread, one read/flagged, one deleted
        INSERT INTO messages VALUES
          (1, 100, 1, 1, 800000000, 1, 0, 0, 0),  -- Inbox A, unread
          (2, 200, 2, 2, 800001000, 1, 0, 1, 0),  -- Inbox A, unread, flagged
          (3, 300, 1, 1, 800002000, 3, 1, 0, 0),  -- Inbox B, read
          (4, 400, 2, 2, 800003000, 2, 0, 0, 1); -- deleted (excluded)
        """
    )
    conn.commit()
    conn.close()
    return db


class TestListAccountUuids:
    def test_returns_distinct_account_uuids(self, fake_envelope):
        uuids = list_account_uuids(fake_envelope)
        assert uuids == ["ACCOUNT-A", "ACCOUNT-B"]

    def test_raises_on_missing_table(self, tmp_path):
        empty = tmp_path / "empty.db"
        sqlite3.connect(empty).close()
        with pytest.raises(sqlite3.OperationalError):
            list_account_uuids(empty)


class TestFetchRecentMessages:
    def test_all_filter_excludes_deleted(self, fake_envelope):
        rows = fetch_recent_messages(
            fake_envelope,
            account_uuid=None,
            mailbox_name=None,
            filter_kind="all",
            limit=10,
        )
        # ROWID 4 has deleted=1 and must be excluded
        assert {r.message_id for r in rows} == {1, 2, 3}

    def test_orders_by_date_received_desc(self, fake_envelope):
        rows = fetch_recent_messages(
            fake_envelope,
            account_uuid=None,
            mailbox_name=None,
            filter_kind="all",
            limit=10,
        )
        # date_received: 800002000 > 800001000 > 800000000
        assert [r.message_id for r in rows] == [3, 2, 1]

    def test_unread_filter(self, fake_envelope):
        rows = fetch_recent_messages(
            fake_envelope,
            account_uuid=None,
            mailbox_name=None,
            filter_kind="unread",
            limit=10,
        )
        assert {r.message_id for r in rows} == {1, 2}
        assert all(not r.read for r in rows)

    def test_flagged_filter(self, fake_envelope):
        rows = fetch_recent_messages(
            fake_envelope,
            account_uuid=None,
            mailbox_name=None,
            filter_kind="flagged",
            limit=10,
        )
        assert {r.message_id for r in rows} == {2}
        assert rows[0].flagged

    def test_account_scoping(self, fake_envelope):
        rows = fetch_recent_messages(
            fake_envelope,
            account_uuid="ACCOUNT-A",
            mailbox_name=None,
            filter_kind="all",
            limit=10,
        )
        # ROWID 3 is in ACCOUNT-B, must be excluded
        assert {r.message_id for r in rows} == {1, 2}

    def test_mailbox_scoping(self, fake_envelope):
        rows = fetch_recent_messages(
            fake_envelope,
            account_uuid="ACCOUNT-A",
            mailbox_name="Inbox",
            filter_kind="all",
            limit=10,
        )
        # ROWIDs 1 and 2 are both in Inbox of ACCOUNT-A
        assert {r.message_id for r in rows} == {1, 2}

    def test_limit_caps_results(self, fake_envelope):
        rows = fetch_recent_messages(
            fake_envelope,
            account_uuid=None,
            mailbox_name=None,
            filter_kind="all",
            limit=1,
        )
        assert len(rows) == 1
        # Most recent (date_received=800002000)
        assert rows[0].message_id == 3  # ROWID of newest row

    def test_subject_and_sender_are_text(self, fake_envelope):
        rows = fetch_recent_messages(
            fake_envelope,
            account_uuid="ACCOUNT-A",
            mailbox_name="Inbox",
            filter_kind="all",
            limit=10,
        )
        by_id = {r.message_id: r for r in rows}
        # ROWID 1: subject FK 1 -> "Hello", sender FK 1 -> alice
        assert by_id[1].subject == "Hello"
        assert by_id[1].sender == "alice@example.com"

    def test_mailbox_url_parsed(self, fake_envelope):
        rows = fetch_recent_messages(
            fake_envelope,
            account_uuid="ACCOUNT-A",
            mailbox_name="Inbox",
            filter_kind="all",
            limit=10,
        )
        for r in rows:
            assert r.account_uuid == "ACCOUNT-A"
            assert r.mailbox_name == "Inbox"
