"""Tests for the Strategy-0 Envelope Index fast path."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from apple_mail_mcp.index.envelope_direct import (
    MailboxNotFoundError,
    _parse_mailbox_url,
    _unix_ts_to_iso,
    envelope_index_path,
    fetch_recent_messages,
    list_account_uuids,
)

# ─── Pure helpers ────────────────────────────────────────────


class TestUnixTsToIso:
    def test_none_returns_empty(self):
        assert _unix_ts_to_iso(None) == ""

    def test_zero_is_unix_epoch(self):
        # 0 seconds since 1970-01-01 -> ISO of that exact moment
        result = _unix_ts_to_iso(0)
        assert result.startswith("1970-01-01T00:00:00")

    def test_round_trip_known_date(self):
        # 2026-05-28 12:00:00 UTC -> Unix ts -> ISO -> verify
        known = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)
        ts = known.timestamp()
        assert "2026-05-28T12:00:00" in _unix_ts_to_iso(ts)

    def test_real_envelope_value_decodes_to_current_era(self):
        # Regression test for the +31y epoch bug. 1779996962 is a
        # real `messages.date_received` value observed in macOS 14
        # / Mail V10. Interpreted as Unix epoch it is 2026-05-28;
        # interpreted as Core Data epoch it would be 2057. If this
        # ever fails with "2057-...", the Core Data offset has
        # been reintroduced.
        assert _unix_ts_to_iso(1779996962).startswith("2026-05-28")

    def test_garbage_returns_empty(self):
        assert _unix_ts_to_iso("not-a-number") == ""  # type: ignore[arg-type]


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

    def test_url_with_percent_encoded_brackets(self):
        _, mb = _parse_mailbox_url("imap://UUID/%5BGmail%5D/All%20Mail")
        assert mb == "[Gmail]/All Mail"

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


# ─── Gmail label-backed mailbox membership ───────────────────


@pytest.fixture
def gmail_envelope(tmp_path: Path) -> Path:
    """Envelope Index shaped like a Gmail-backed account.

    Mail.app keeps every Gmail message in `[Gmail]/All Mail`;
    membership in INBOX and other label-backed mailboxes is
    recorded only in the `labels` table, and mailbox URLs are
    stored percent-encoded.
    """
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
        CREATE TABLE labels (
            message_id INTEGER,
            mailbox_id INTEGER,
            PRIMARY KEY (message_id, mailbox_id)
        ) WITHOUT ROWID;

        INSERT INTO subjects VALUES
          (1, 'Invoice'), (2, 'Newsletter'), (3, 'Receipt');
        INSERT INTO addresses VALUES
          (1, 'vendor@example.com', ''),
          (2, 'news@example.com', '');
        INSERT INTO mailboxes VALUES
          (1, 'imap://ACCOUNT-G/%5BGmail%5D/All%20Mail'),
          (2, 'imap://ACCOUNT-G/INBOX'),
          (3, 'imap://ACCOUNT-G/%5BGmail%5D/Sent%20Mail'),
          (4, 'imap://ACCOUNT-H/INBOX');

        -- Every ACCOUNT-G message lives in All Mail (mailbox 1);
        -- INBOX / Sent Mail membership exists only as label rows.
        -- ACCOUNT-H is a plain IMAP account with direct membership,
        -- and message 4 also carries a redundant label row to prove
        -- the two membership paths don't double-count.
        INSERT INTO messages VALUES
          (1, 100, 1, 1, 800000000, 1, 0, 0, 0),
          (2, 200, 2, 2, 800001000, 1, 1, 0, 0),
          (3, 300, 1, 3, 800002000, 1, 1, 0, 0),
          (4, 400, 2, 1, 800003000, 4, 0, 0, 0);
        INSERT INTO labels VALUES (1, 2), (2, 2), (3, 3), (4, 4);
        """
    )
    conn.commit()
    conn.close()
    return db


class TestGmailLabelMembership:
    def test_inbox_membership_via_labels(self, gmail_envelope):
        # The INBOX mailbox row has no direct messages.mailbox
        # members; messages 1 and 2 are INBOX only via labels.
        rows = fetch_recent_messages(
            gmail_envelope,
            account_uuid="ACCOUNT-G",
            mailbox_name="INBOX",
            filter_kind="all",
            limit=10,
        )
        assert {r.message_id for r in rows} == {1, 2}

    def test_percent_encoded_mailbox_by_display_name(self, gmail_envelope):
        # "Sent Mail" is stored as %5BGmail%5D/Sent%20Mail and is
        # addressed by its bare display name.
        rows = fetch_recent_messages(
            gmail_envelope,
            account_uuid="ACCOUNT-G",
            mailbox_name="Sent Mail",
            filter_kind="all",
            limit=10,
        )
        assert {r.message_id for r in rows} == {3}

    def test_full_decoded_path_also_matches(self, gmail_envelope):
        rows = fetch_recent_messages(
            gmail_envelope,
            account_uuid="ACCOUNT-G",
            mailbox_name="[Gmail]/Sent Mail",
            filter_kind="all",
            limit=10,
        )
        assert {r.message_id for r in rows} == {3}

    def test_mailbox_match_is_case_insensitive(self, gmail_envelope):
        # The configured default mailbox is "Inbox"; IMAP stores
        # "INBOX".
        rows = fetch_recent_messages(
            gmail_envelope,
            account_uuid="ACCOUNT-G",
            mailbox_name="Inbox",
            filter_kind="all",
            limit=10,
        )
        assert {r.message_id for r in rows} == {1, 2}

    def test_unknown_mailbox_raises_not_empty(self, gmail_envelope):
        # An unknown mailbox must be distinguishable from an empty
        # one so the caller can fall back to JXA.
        with pytest.raises(MailboxNotFoundError):
            fetch_recent_messages(
                gmail_envelope,
                account_uuid="ACCOUNT-G",
                mailbox_name="No Such Mailbox",
                filter_kind="all",
                limit=10,
            )

    def test_no_cross_account_leakage(self, gmail_envelope):
        # ACCOUNT-H's INBOX message must not appear in ACCOUNT-G's.
        rows = fetch_recent_messages(
            gmail_envelope,
            account_uuid="ACCOUNT-G",
            mailbox_name="INBOX",
            filter_kind="all",
            limit=10,
        )
        assert 4 not in {r.message_id for r in rows}

    def test_direct_and_label_membership_not_double_counted(
        self, gmail_envelope
    ):
        # Message 4 is in ACCOUNT-H's INBOX both directly and via a
        # label row; it must appear exactly once.
        rows = fetch_recent_messages(
            gmail_envelope,
            account_uuid="ACCOUNT-H",
            mailbox_name="INBOX",
            filter_kind="all",
            limit=10,
        )
        assert [r.message_id for r in rows] == [4]

    def test_unread_filter_composes_with_labels(self, gmail_envelope):
        # Message 1 is unread, message 2 is read; both are
        # INBOX-labeled.
        rows = fetch_recent_messages(
            gmail_envelope,
            account_uuid="ACCOUNT-G",
            mailbox_name="INBOX",
            filter_kind="unread",
            limit=10,
        )
        assert {r.message_id for r in rows} == {1}

    def test_no_account_scopes_by_mailbox_across_accounts(self, gmail_envelope):
        # With no account, a named mailbox must still scope results
        # (all accounts' INBOXes), never the whole message table.
        rows = fetch_recent_messages(
            gmail_envelope,
            account_uuid=None,
            mailbox_name="INBOX",
            filter_kind="all",
            limit=10,
        )
        # Message 3 is Sent Mail only and must be excluded.
        assert {r.message_id for r in rows} == {1, 2, 4}
