"""Tests for IndexManager class.

Tests the central orchestration class for the FTS5 search index:
- Singleton pattern
- Index existence checking
- Sync operations
- Staleness detection
- Search delegation
- Statistics
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from apple_mail_mcp.index.manager import IndexManager, IndexStats


class TestIndexManagerSingleton:
    """Tests for singleton pattern."""

    def teardown_method(self):
        """Reset singleton after each test."""
        IndexManager._instance = None

    def test_get_instance_returns_same_object(self):
        """get_instance returns the same IndexManager object."""
        m1 = IndexManager.get_instance()
        m2 = IndexManager.get_instance()
        assert m1 is m2

    def test_reset_clears_singleton(self):
        """Resetting _instance creates new manager."""
        m1 = IndexManager.get_instance()
        IndexManager._instance = None
        m2 = IndexManager.get_instance()
        assert m1 is not m2

    def test_custom_db_path_is_used(self, tmp_path):
        """Custom db_path is used when provided."""
        custom_path = tmp_path / "custom.db"
        manager = IndexManager(db_path=custom_path)
        assert manager.db_path == custom_path


class TestHasIndex:
    """Tests for index existence checking."""

    def teardown_method(self):
        IndexManager._instance = None

    @pytest.mark.parametrize(
        "file_exists, expected", [(False, False), (True, True)]
    )
    def test_has_index_reflects_db_existence(
        self, tmp_path, file_exists, expected
    ):
        """has_index returns True iff the database file exists."""
        db_path = tmp_path / "test.db"
        if file_exists:
            db_path.touch()
        manager = IndexManager(db_path=db_path)
        assert manager.has_index() is expected


class TestGetStats:
    """Tests for index statistics."""

    def teardown_method(self):
        IndexManager._instance = None

    def test_get_stats_returns_index_stats(self, temp_db_path):
        """get_stats returns IndexStats dataclass."""
        manager = IndexManager(db_path=temp_db_path)

        # Initialize the database by getting connection
        conn = manager._get_conn()
        conn.execute(
            "INSERT INTO emails (message_id, account, mailbox, subject) "
            "VALUES (1, 'test', 'INBOX', 'Test')"
        )
        conn.commit()

        stats = manager.get_stats()

        assert isinstance(stats, IndexStats)
        assert stats.email_count == 1
        assert stats.mailbox_count == 1

    def test_get_stats_reports_zero_for_empty_index(self, temp_db_path):
        """get_stats reports zero counts for empty index."""
        manager = IndexManager(db_path=temp_db_path)
        manager._get_conn()  # Initialize DB

        stats = manager.get_stats()

        assert stats.email_count == 0
        assert stats.mailbox_count == 0
        assert stats.last_sync is None

    def test_get_stats_calculates_staleness(self, temp_db_path):
        """get_stats calculates staleness hours from last_sync."""
        manager = IndexManager(db_path=temp_db_path)
        conn = manager._get_conn()

        # Set a sync time 2 hours ago
        two_hours_ago = (datetime.now() - timedelta(hours=2)).isoformat()
        conn.execute(
            "INSERT INTO sync_state (account, mailbox, last_sync) "
            "VALUES ('test', 'INBOX', ?)",
            (two_hours_ago,),
        )
        conn.commit()

        stats = manager.get_stats()

        assert stats.staleness_hours is not None
        assert 1.9 < stats.staleness_hours < 2.1  # Allow small timing variance


class TestIsStale:
    """Tests for staleness detection."""

    def teardown_method(self):
        IndexManager._instance = None

    def test_is_stale_returns_true_when_never_synced(self, temp_db_path):
        """is_stale returns True when no sync has occurred."""
        manager = IndexManager(db_path=temp_db_path)
        manager._get_conn()  # Initialize DB

        assert manager.is_stale() is True

    def test_is_stale_returns_true_when_old(self, temp_db_path):
        """is_stale returns True when last sync exceeds threshold."""
        manager = IndexManager(db_path=temp_db_path)
        conn = manager._get_conn()

        # Set sync time beyond default staleness threshold (24h)
        old_time = (datetime.now() - timedelta(hours=25)).isoformat()
        conn.execute(
            "INSERT INTO sync_state (account, mailbox, last_sync) "
            "VALUES ('test', 'INBOX', ?)",
            (old_time,),
        )
        conn.commit()

        assert manager.is_stale() is True

    def test_is_stale_returns_false_when_recent(self, temp_db_path):
        """is_stale returns False when last sync is recent."""
        manager = IndexManager(db_path=temp_db_path)
        conn = manager._get_conn()

        # Set recent sync time
        recent_time = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO sync_state (account, mailbox, last_sync) "
            "VALUES ('test', 'INBOX', ?)",
            (recent_time,),
        )
        conn.commit()

        assert manager.is_stale() is False


class TestSyncUpdates:
    """Tests for disk-based sync."""

    def teardown_method(self):
        IndexManager._instance = None

    @patch("apple_mail_mcp.index.sync.sync_from_disk")
    @patch("apple_mail_mcp.index.disk.find_mail_directory")
    def test_sync_updates_calls_disk_sync(
        self, mock_find, mock_sync, temp_db_path
    ):
        """sync_updates calls sync_from_disk with correct args."""
        mock_find.return_value = Path("/fake/mail")
        mock_result = MagicMock()
        mock_result.total_changes = 5
        mock_sync.return_value = mock_result

        manager = IndexManager(db_path=temp_db_path)
        result = manager.sync_updates()

        assert result == 5
        mock_sync.assert_called_once()

    @pytest.mark.parametrize("error_cls", [FileNotFoundError, PermissionError])
    @patch("apple_mail_mcp.index.disk.find_mail_directory")
    def test_sync_updates_handles_inaccessible_mail_dir(
        self, mock_find, error_cls, temp_db_path
    ):
        """sync_updates returns 0 when mail directory is inaccessible."""
        mock_find.side_effect = error_cls("Cannot access")

        manager = IndexManager(db_path=temp_db_path)
        assert manager.sync_updates() == 0


class TestSearch:
    """Tests for search delegation."""

    def teardown_method(self):
        IndexManager._instance = None

    @patch("apple_mail_mcp.index.search.search_fts")
    def test_search_delegates_to_search_fts(self, mock_search, temp_db_path):
        """search delegates to search_fts function."""
        mock_search.return_value = []

        manager = IndexManager(db_path=temp_db_path)
        manager._get_conn()  # Initialize connection

        manager.search("invoice", account="Work", mailbox="INBOX", limit=10)

        mock_search.assert_called_once()
        call_args = mock_search.call_args
        assert call_args[0][1] == "invoice"  # query
        assert call_args[1]["account"] == "Work"
        assert call_args[1]["mailbox"] == "INBOX"
        assert call_args[1]["limit"] == 10


class TestClose:
    """Tests for connection management."""

    def teardown_method(self):
        IndexManager._instance = None

    def test_close_is_idempotent(self, temp_db_path):
        """close() closes the connection and can be called repeatedly."""
        manager = IndexManager(db_path=temp_db_path)
        manager._get_conn()

        manager.close()
        assert manager._conn is None

        manager.close()  # Should not raise
        assert manager._conn is None


class TestGetIndexedMessageIds:
    """Tests for get_indexed_message_ids."""

    def teardown_method(self):
        IndexManager._instance = None

    def test_returns_empty_set_when_no_emails(self, temp_db_path):
        """get_indexed_message_ids returns empty set for empty index."""
        manager = IndexManager(db_path=temp_db_path)
        manager._get_conn()

        ids = manager.get_indexed_message_ids()

        assert ids == set()

    def test_returns_all_message_ids(self, temp_db_path):
        """get_indexed_message_ids returns all IDs when no filter."""
        manager = IndexManager(db_path=temp_db_path)
        conn = manager._get_conn()

        # Insert test emails
        conn.execute(
            "INSERT INTO emails (message_id, account, mailbox) "
            "VALUES (1, 'a', 'm1'), (2, 'a', 'm1'), (3, 'b', 'm2')"
        )
        conn.commit()

        ids = manager.get_indexed_message_ids()

        assert ids == {1, 2, 3}

    def test_filters_by_account(self, temp_db_path):
        """get_indexed_message_ids filters by account."""
        manager = IndexManager(db_path=temp_db_path)
        conn = manager._get_conn()

        conn.execute(
            "INSERT INTO emails (message_id, account, mailbox) "
            "VALUES (1, 'a', 'm1'), (2, 'a', 'm1'), (3, 'b', 'm2')"
        )
        conn.commit()

        ids = manager.get_indexed_message_ids(account="a")

        assert ids == {1, 2}

    def test_filters_by_account_and_mailbox(self, temp_db_path):
        """get_indexed_message_ids filters by both account and mailbox."""
        manager = IndexManager(db_path=temp_db_path)
        conn = manager._get_conn()

        conn.execute(
            "INSERT INTO emails (message_id, account, mailbox) "
            "VALUES (1, 'a', 'm1'), (2, 'a', 'm2'), (3, 'b', 'm1')"
        )
        conn.commit()

        ids = manager.get_indexed_message_ids(account="a", mailbox="m1")

        assert ids == {1}


class TestFindEmailLocation:
    """Tests for find_email_location (#37)."""

    def teardown_method(self):
        IndexManager._instance = None

    def test_found(self, temp_db_path):
        manager = IndexManager(db_path=temp_db_path)
        conn = manager._get_conn()
        conn.execute(
            "INSERT INTO emails (message_id, account, mailbox) "
            "VALUES (42, 'uuid-1', 'INBOX')"
        )
        conn.commit()

        result = manager.find_email_location(42)
        assert result == ("uuid-1", "INBOX")

    def test_not_found(self, temp_db_path):
        manager = IndexManager(db_path=temp_db_path)
        manager._get_conn()

        assert manager.find_email_location(999) is None

    def test_with_filters(self, temp_db_path):
        manager = IndexManager(db_path=temp_db_path)
        conn = manager._get_conn()
        conn.execute(
            "INSERT INTO emails (message_id, account, mailbox) "
            "VALUES (42, 'uuid-1', 'INBOX')"
        )
        conn.execute(
            "INSERT INTO emails (message_id, account, mailbox) "
            "VALUES (42, 'uuid-2', 'Sent')"
        )
        conn.commit()

        result = manager.find_email_location(
            42, account="uuid-2", mailbox="Sent"
        )
        assert result == ("uuid-2", "Sent")


class TestFindEmailPath:
    """Tests for find_email_path (#37)."""

    def teardown_method(self):
        IndexManager._instance = None

    def test_found(self, temp_db_path):
        manager = IndexManager(db_path=temp_db_path)
        conn = manager._get_conn()
        conn.execute(
            "INSERT INTO emails "
            "(message_id, account, mailbox, emlx_path) "
            "VALUES (42, 'uuid-1', 'INBOX', '/path/to/42.emlx')"
        )
        conn.commit()

        result = manager.find_email_path(42)
        assert result is not None
        assert str(result) == "/path/to/42.emlx"

    def test_null_path(self, temp_db_path):
        manager = IndexManager(db_path=temp_db_path)
        conn = manager._get_conn()
        conn.execute(
            "INSERT INTO emails (message_id, account, mailbox) "
            "VALUES (42, 'uuid-1', 'INBOX')"
        )
        conn.commit()

        assert manager.find_email_path(42) is None


class TestDeleteEmail:
    """Tests for delete_email (#74)."""

    def teardown_method(self):
        IndexManager._instance = None

    def test_deletes_matching_row(self, temp_db_path):
        manager = IndexManager(db_path=temp_db_path)
        conn = manager._get_conn()
        conn.execute(
            "INSERT INTO emails "
            "(message_id, account, mailbox, subject, sender, content) "
            "VALUES (42, 'uuid-1', 'INBOX', 'Hello', 'a@b.com', 'body')"
        )
        conn.commit()

        deleted = manager.delete_email(42)

        assert deleted == 1
        assert manager.find_email_path(42) is None
        # FTS5 row should be gone too via the AFTER DELETE trigger
        fts_count = conn.execute(
            "SELECT COUNT(*) AS n FROM emails_fts WHERE subject MATCH 'Hello'"
        ).fetchone()["n"]
        assert fts_count == 0

    def test_returns_zero_when_no_match(self, temp_db_path):
        manager = IndexManager(db_path=temp_db_path)
        assert manager.delete_email(999) == 0

    def test_scopes_by_account_and_mailbox(self, temp_db_path):
        manager = IndexManager(db_path=temp_db_path)
        conn = manager._get_conn()
        conn.execute(
            "INSERT INTO emails (message_id, account, mailbox) "
            "VALUES (42, 'uuid-A', 'INBOX')"
        )
        conn.execute(
            "INSERT INTO emails (message_id, account, mailbox) "
            "VALUES (42, 'uuid-B', 'INBOX')"
        )
        conn.commit()

        deleted = manager.delete_email(42, account="uuid-A", mailbox="INBOX")

        assert deleted == 1
        # The uuid-B row survives
        remaining = conn.execute(
            "SELECT account FROM emails WHERE message_id = 42"
        ).fetchall()
        assert len(remaining) == 1
        assert remaining[0]["account"] == "uuid-B"


class TestBuildFromDiskTriggers:
    """Verify FTS5 triggers are reactivated after build_from_disk.

    Regression for the watcher race: if triggers were recreated AFTER
    rebuild_fts_index (the old order), any INSERT that landed between
    rebuild and recreation would never enter emails_fts. The reorder
    in build_from_disk fixes that — this test verifies the invariant
    that any INSERT after build_from_disk fires the trigger.
    """

    def teardown_method(self):
        IndexManager._instance = None

    def test_after_insert_trigger_active_post_build(
        self, tmp_path, temp_db_path, monkeypatch
    ):
        manager = IndexManager(db_path=temp_db_path)

        # Empty mail dir — build_from_disk traverses nothing.
        empty_mail = tmp_path / "Mail" / "V10"
        empty_mail.mkdir(parents=True)
        monkeypatch.setattr(
            "apple_mail_mcp.index.disk.find_mail_directory",
            lambda: empty_mail,
        )
        manager.build_from_disk()

        conn = manager._get_conn()

        # Post-build INSERT should fire the AFTER INSERT trigger and
        # land in emails_fts.
        conn.execute(
            "INSERT INTO emails "
            "(message_id, account, mailbox, subject, sender, content) "
            "VALUES (1, 'acc', 'INBOX', 'Hello world', 's@x.com', 'Body')"
        )
        conn.commit()

        match_count = conn.execute(
            "SELECT COUNT(*) AS n FROM emails_fts "
            "WHERE emails_fts MATCH 'Hello'"
        ).fetchone()["n"]
        assert match_count == 1, (
            "AFTER INSERT trigger missing — emails_fts not populated. "
            "Likely the trigger recreation order regressed."
        )

    def test_after_delete_trigger_active_post_build(
        self, tmp_path, temp_db_path, monkeypatch
    ):
        manager = IndexManager(db_path=temp_db_path)

        empty_mail = tmp_path / "Mail" / "V10"
        empty_mail.mkdir(parents=True)
        monkeypatch.setattr(
            "apple_mail_mcp.index.disk.find_mail_directory",
            lambda: empty_mail,
        )
        manager.build_from_disk()

        conn = manager._get_conn()
        conn.execute(
            "INSERT INTO emails "
            "(message_id, account, mailbox, subject, sender, content) "
            "VALUES (2, 'acc', 'INBOX', 'Goodbye', 's@x.com', 'Body')"
        )
        conn.execute("DELETE FROM emails WHERE message_id = 2")
        conn.commit()

        match_count = conn.execute(
            "SELECT COUNT(*) AS n FROM emails_fts "
            "WHERE emails_fts MATCH 'Goodbye'"
        ).fetchone()["n"]
        assert match_count == 0, (
            "AFTER DELETE trigger missing — emails_fts retained a "
            "deleted row."
        )

    def test_triggers_present_when_rebuild_fts_runs(
        self, tmp_path, temp_db_path, monkeypatch
    ):
        """Order regression: triggers must be recreated BEFORE
        rebuild_fts_index runs. If the order is reversed, any
        concurrent INSERT during the rebuild window lands in `emails`
        but never reaches `emails_fts`.
        """
        manager = IndexManager(db_path=temp_db_path)

        # Fake mail dir with one valid .emlx so total_indexed > 0
        # and rebuild_fts_index actually runs.
        mail_dir = tmp_path / "Mail" / "V10"
        mbox = mail_dir / "acc1" / "INBOX.mbox" / "Data" / "Messages"
        mbox.mkdir(parents=True)
        emlx = mbox / "1001.emlx"
        emlx.write_bytes(
            b"100\nFrom: t@t.com\nSubject: Test\n\nBody"
        )

        monkeypatch.setattr(
            "apple_mail_mcp.index.disk.find_mail_directory",
            lambda: mail_dir,
        )

        triggers_at_rebuild_time: list[str] = []

        def hook_rebuild(conn):
            rows = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='trigger' "
                "AND name IN ('emails_ai', 'emails_ad', 'emails_au')"
            ).fetchall()
            triggers_at_rebuild_time.extend(r[0] for r in rows)
            # Skip the actual rebuild — we only want to inspect state.

        monkeypatch.setattr(
            "apple_mail_mcp.index.manager.rebuild_fts_index",
            hook_rebuild,
        )

        manager.build_from_disk()

        assert "emails_ai" in triggers_at_rebuild_time, (
            "AFTER INSERT trigger missing when rebuild_fts_index ran "
            "— trigger recreation order regressed."
        )
        assert "emails_ad" in triggers_at_rebuild_time
        assert "emails_au" in triggers_at_rebuild_time


class TestParseFailureDLQ:
    """Tests for record_parse_failure / clear_parse_failure (#58)."""

    def teardown_method(self):
        IndexManager._instance = None

    def test_record_inserts_new_failure(self, temp_db_path):
        manager = IndexManager(db_path=temp_db_path)

        manager.record_parse_failure(
            "/Library/Mail/V10/uuid/INBOX/Messages/42.emlx",
            "uuid-1",
            "INBOX",
            ValueError("malformed plist"),
        )

        conn = manager._get_conn()
        row = conn.execute(
            "SELECT account, mailbox, error_type, error_message, "
            "attempt_count FROM failed_index_jobs"
        ).fetchone()
        assert row["account"] == "uuid-1"
        assert row["mailbox"] == "INBOX"
        assert row["error_type"] == "ValueError"
        assert row["error_message"] == "malformed plist"
        assert row["attempt_count"] == 1

    def test_record_idempotent_increments_attempt_count(
        self, temp_db_path
    ):
        manager = IndexManager(db_path=temp_db_path)
        path = "/path/42.emlx"

        manager.record_parse_failure(
            path, "uuid-1", "INBOX", ValueError("first")
        )
        manager.record_parse_failure(
            path, "uuid-1", "INBOX", OSError("second")
        )
        manager.record_parse_failure(
            path, "uuid-1", "INBOX", OSError("third")
        )

        conn = manager._get_conn()
        row = conn.execute(
            "SELECT attempt_count, error_type, error_message, "
            "first_seen, last_seen FROM failed_index_jobs"
        ).fetchone()
        assert row["attempt_count"] == 3
        # Latest error wins on type/message; first_seen survives
        assert row["error_type"] == "OSError"
        assert row["error_message"] == "third"

    def test_record_truncates_long_messages(self, temp_db_path):
        manager = IndexManager(db_path=temp_db_path)
        long_msg = "x" * 1000

        manager.record_parse_failure(
            "/path/42.emlx",
            "uuid-1",
            "INBOX",
            ValueError(long_msg),
        )

        conn = manager._get_conn()
        stored = conn.execute(
            "SELECT error_message FROM failed_index_jobs"
        ).fetchone()["error_message"]
        assert len(stored) == 500

    def test_clear_removes_entry(self, temp_db_path):
        manager = IndexManager(db_path=temp_db_path)
        path = "/path/42.emlx"
        manager.record_parse_failure(
            path, "uuid-1", "INBOX", ValueError("oops")
        )

        deleted = manager.clear_parse_failure(path)

        assert deleted == 1
        conn = manager._get_conn()
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM failed_index_jobs"
        ).fetchone()["n"]
        assert count == 0

    def test_clear_returns_zero_when_absent(self, temp_db_path):
        manager = IndexManager(db_path=temp_db_path)
        assert manager.clear_parse_failure("/never/seen.emlx") == 0

    def test_get_stats_includes_failed_jobs_count(self, temp_db_path):
        manager = IndexManager(db_path=temp_db_path)
        manager.record_parse_failure(
            "/a.emlx", "u", "INBOX", ValueError("a")
        )
        manager.record_parse_failure(
            "/b.emlx", "u", "INBOX", ValueError("b")
        )

        stats = manager.get_stats()
        assert stats.failed_jobs_count == 2


class TestSearchAttachments:
    """Tests for search_attachments (#37)."""

    def teardown_method(self):
        IndexManager._instance = None

    def test_basic(self, temp_db_path):
        manager = IndexManager(db_path=temp_db_path)
        conn = manager._get_conn()
        conn.execute(
            "INSERT INTO emails "
            "(message_id, account, mailbox, subject, sender, "
            "date_received, attachment_count) "
            "VALUES (1, 'acc', 'INBOX', 'Test', 'a@b.com', "
            "'2024-01-01', 1)"
        )
        rowid = conn.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO attachments "
            "(email_rowid, filename, mime_type, file_size) "
            "VALUES (?, 'invoice.pdf', 'application/pdf', 100)",
            (rowid,),
        )
        conn.commit()

        results = manager.search_attachments("invoice")
        assert len(results) == 1
        assert results[0]["filename"] == "invoice.pdf"

    def test_with_filters(self, temp_db_path):
        manager = IndexManager(db_path=temp_db_path)
        conn = manager._get_conn()
        conn.execute(
            "INSERT INTO emails "
            "(message_id, account, mailbox, subject, sender, "
            "date_received, attachment_count) "
            "VALUES (1, 'acc1', 'INBOX', 'Test', 'a@b.com', "
            "'2024-01-01', 1)"
        )
        rowid = conn.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO attachments "
            "(email_rowid, filename) VALUES (?, 'doc.pdf')",
            (rowid,),
        )
        conn.commit()

        # Should find with matching account
        results = manager.search_attachments("doc", account="acc1")
        assert len(results) == 1

        # Should not find with wrong account
        results = manager.search_attachments("doc", account="other")
        assert len(results) == 0


class TestGetEmailAttachments:
    """Tests for get_email_attachments (#36)."""

    def teardown_method(self):
        IndexManager._instance = None

    def test_found(self, temp_db_path):
        manager = IndexManager(db_path=temp_db_path)
        conn = manager._get_conn()
        conn.execute(
            "INSERT INTO emails "
            "(message_id, account, mailbox, subject) "
            "VALUES (42, 'acc', 'INBOX', 'Test')"
        )
        rowid = conn.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO attachments "
            "(email_rowid, filename, mime_type, file_size, content_id) "
            "VALUES (?, 'doc.pdf', 'application/pdf', 500, NULL)",
            (rowid,),
        )
        conn.commit()

        result = manager.get_email_attachments(42)
        assert result is not None
        assert len(result) == 1
        assert result[0]["filename"] == "doc.pdf"
        assert result[0]["size"] == 500

    def test_not_found(self, temp_db_path):
        manager = IndexManager(db_path=temp_db_path)
        manager._get_conn()

        assert manager.get_email_attachments(999) is None


class TestGetStatsWithCapped:
    """Tests for capped_mailboxes in IndexStats (#17)."""

    def teardown_method(self):
        IndexManager._instance = None

    def test_get_stats_includes_capped_mailboxes(self, temp_db_path):
        manager = IndexManager(db_path=temp_db_path)
        conn = manager._get_conn()

        # Insert emails to hit the cap (default 5000)
        # Use a smaller cap via env override
        for i in range(3):
            conn.execute(
                "INSERT INTO emails (message_id, account, mailbox) "
                f"VALUES ({i}, 'acc', 'INBOX')"
            )
        conn.commit()

        with patch(
            "apple_mail_mcp.index.manager.get_index_max_emails",
            return_value=3,
        ):
            stats = manager.get_stats()

        assert stats.capped_mailboxes == 1

    def test_no_capped_mailboxes(self, temp_db_path):
        manager = IndexManager(db_path=temp_db_path)
        conn = manager._get_conn()
        conn.execute(
            "INSERT INTO emails (message_id, account, mailbox) "
            "VALUES (1, 'acc', 'INBOX')"
        )
        conn.commit()

        stats = manager.get_stats()
        assert stats.capped_mailboxes == 0


class TestDiskCountCache:
    """Tests for the get_stats() disk inventory TTL cache (#78)."""

    def teardown_method(self):
        IndexManager._instance = None

    def test_disk_count_cached_across_calls(self, temp_db_path, monkeypatch):
        manager = IndexManager(db_path=temp_db_path)

        call_count = {"n": 0}

        def fake_inventory(mail_dir):
            call_count["n"] += 1
            return {("acc", "INBOX", i): "/p" for i in range(7)}

        monkeypatch.setattr(
            "apple_mail_mcp.index.disk.find_mail_directory",
            lambda: "/fake",
        )
        monkeypatch.setattr(
            "apple_mail_mcp.index.disk.get_disk_inventory",
            fake_inventory,
        )

        # First call walks; second call returns cached value.
        s1 = manager.get_stats()
        s2 = manager.get_stats()
        assert s1.disk_email_count == 7
        assert s2.disk_email_count == 7
        assert call_count["n"] == 1, "second get_stats should hit cache"

    def test_disk_count_cache_expires(self, temp_db_path, monkeypatch):
        manager = IndexManager(db_path=temp_db_path)
        manager._DISK_COUNT_TTL_SEC = 0.05

        call_count = {"n": 0}

        def fake_inventory(mail_dir):
            call_count["n"] += 1
            return {("acc", "INBOX", i): "/p" for i in range(3)}

        monkeypatch.setattr(
            "apple_mail_mcp.index.disk.find_mail_directory",
            lambda: "/fake",
        )
        monkeypatch.setattr(
            "apple_mail_mcp.index.disk.get_disk_inventory",
            fake_inventory,
        )

        manager.get_stats()
        assert call_count["n"] == 1
        time.sleep(0.06)
        manager.get_stats()
        assert call_count["n"] == 2, "cache should re-fetch after TTL expiry"

    def test_disk_count_failure_not_cached(self, temp_db_path, monkeypatch):
        # Permission errors must not be cached — the next call should
        # retry in case Full Disk Access has since been granted.
        manager = IndexManager(db_path=temp_db_path)

        call_count = {"n": 0}

        def boom(_):
            call_count["n"] += 1
            raise PermissionError("no FDA")

        monkeypatch.setattr(
            "apple_mail_mcp.index.disk.find_mail_directory",
            lambda: "/fake",
        )
        monkeypatch.setattr(
            "apple_mail_mcp.index.disk.get_disk_inventory",
            boom,
        )

        s1 = manager.get_stats()
        s2 = manager.get_stats()
        assert s1.disk_email_count is None
        assert s2.disk_email_count is None
        assert call_count["n"] == 2, "failures must not be cached"

    def test_invalidate_disk_count_cache(self, temp_db_path, monkeypatch):
        manager = IndexManager(db_path=temp_db_path)

        call_count = {"n": 0}

        def fake_inventory(_):
            call_count["n"] += 1
            return {("acc", "INBOX", i): "/p" for i in range(5)}

        monkeypatch.setattr(
            "apple_mail_mcp.index.disk.find_mail_directory",
            lambda: "/fake",
        )
        monkeypatch.setattr(
            "apple_mail_mcp.index.disk.get_disk_inventory",
            fake_inventory,
        )

        manager.get_stats()
        manager.invalidate_disk_count_cache()
        manager.get_stats()
        assert call_count["n"] == 2, "invalidate should force a re-walk"


class TestWatcher:
    """Tests for file watcher integration."""

    def teardown_method(self):
        IndexManager._instance = None

    def test_watcher_not_running_initially_and_stop_is_safe(self, temp_db_path):
        """Watcher is not running initially; stop_watcher is a no-op."""
        manager = IndexManager(db_path=temp_db_path)
        assert manager.watcher_running is False
        manager.stop_watcher()  # Should not raise
        assert manager.watcher_running is False
