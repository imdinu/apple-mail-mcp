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
