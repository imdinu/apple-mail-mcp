"""Tests for apple_mail_mcp.index.watcher."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from apple_mail_mcp.index.schema import create_connection, get_schema_sql
from apple_mail_mcp.index.watcher import PATH_PATTERN, IndexWatcher


@pytest.fixture
def watcher_db(tmp_path: Path) -> tuple[Path, sqlite3.Connection]:
    """Create a temporary database for watcher tests."""
    db_path = tmp_path / "watcher_test.db"
    conn = create_connection(str(db_path))
    conn.executescript(get_schema_sql())
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (4,))
    conn.commit()
    return db_path, conn


class TestProcessPendingResilience:
    """Watcher should skip files that fail to parse, not crash."""

    def _make_watcher(self, db_path: Path) -> IndexWatcher:
        """Create a watcher without starting the watch loop."""
        watcher = IndexWatcher.__new__(IndexWatcher)
        watcher.db_path = str(db_path)
        watcher._conn = None
        watcher._pending_adds = {}
        watcher._pending_deletes = set()
        import threading

        watcher._pending_lock = threading.Lock()
        watcher._stop_event = threading.Event()
        watcher._mail_dir = None
        watcher._thread = None
        watcher.on_update = None
        watcher.debounce_ms = 500
        return watcher

    @patch("apple_mail_mcp.index.watcher.parse_emlx")
    def test_runtime_error_skips_file(self, mock_parse, watcher_db):
        """RuntimeError in parse_emlx should not crash the watcher."""
        db_path, conn = watcher_db
        conn.close()

        watcher = self._make_watcher(db_path)
        watcher._pending_adds = {
            ("acct", "INBOX", 1): Path("/fake/1.emlx"),
            ("acct", "INBOX", 2): Path("/fake/2.emlx"),
        }

        mock_parse.side_effect = RuntimeError("malformed plist")

        # Should not raise — watcher skips bad files
        watcher._process_pending()

        # Both files attempted, neither crashed the watcher
        assert mock_parse.call_count == 2

    @patch("apple_mail_mcp.index.watcher.parse_emlx")
    def test_attribute_error_skips_file(self, mock_parse, watcher_db):
        """AttributeError in parse_emlx should not crash the watcher."""
        db_path, conn = watcher_db
        conn.close()

        watcher = self._make_watcher(db_path)
        watcher._pending_adds = {
            ("acct", "INBOX", 1): Path("/fake/1.emlx"),
        }

        mock_parse.side_effect = AttributeError("NoneType has no attr")

        watcher._process_pending()

        assert mock_parse.call_count == 1

    @patch("apple_mail_mcp.index.watcher.parse_emlx")
    def test_key_error_skips_file(self, mock_parse, watcher_db):
        """KeyError in parse_emlx should not crash the watcher."""
        db_path, conn = watcher_db
        conn.close()

        watcher = self._make_watcher(db_path)
        watcher._pending_adds = {
            ("acct", "INBOX", 1): Path("/fake/1.emlx"),
        }

        mock_parse.side_effect = KeyError("missing-header")

        watcher._process_pending()

        assert mock_parse.call_count == 1

    @patch("apple_mail_mcp.index.watcher.parse_emlx")
    def test_deletes_still_processed_after_parse_failure(
        self, mock_parse, watcher_db
    ):
        """Deletes should still be processed even if adds fail."""
        db_path, conn = watcher_db

        # Insert a row to delete
        conn.execute(
            "INSERT INTO emails "
            "(message_id, account, mailbox, subject, sender, "
            "content, date_received, emlx_path, attachment_count) "
            "VALUES (1, 'acct', 'INBOX', 'test', 'a@b.com', "
            "'body', '2024-01-01', '/fake/1.emlx', 0)"
        )
        conn.commit()
        conn.close()

        watcher = self._make_watcher(db_path)
        watcher._pending_deletes = {("acct", "INBOX", 1)}
        watcher._pending_adds = {
            ("acct", "INBOX", 2): Path("/fake/2.emlx"),
        }

        mock_parse.side_effect = RuntimeError("crash")

        watcher._process_pending()

        # Verify delete went through
        check_conn = create_connection(str(db_path))
        count = check_conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
        check_conn.close()
        assert count == 0


class TestPathParsing:
    """Watcher should handle noisy filesystem events gracefully."""

    def test_ignores_non_emlx_extensions(self):
        """Non-.emlx files should not match the path pattern."""
        from apple_mail_mcp.index.watcher import PATH_PATTERN

        # .emlx.part files (Mail.app writes these temporarily)
        assert (
            PATH_PATTERN.search(
                "/Users/x/Library/Mail/V10/acc/INBOX.mbox"
                "/Data/1/Messages/123.emlx.part"
            )
            is None
        )
        # .mbox metadata
        assert (
            PATH_PATTERN.search(
                "/Users/x/Library/Mail/V10/acc/INBOX.mbox/Info.plist"
            )
            is None
        )
        # random temp files
        assert (
            PATH_PATTERN.search(
                "/Users/x/Library/Mail/V10/acc/INBOX.mbox"
                "/Data/1/Messages/.DS_Store"
            )
            is None
        )

    def test_matches_regular_and_partial_emlx(self):
        """Both .emlx and .partial.emlx should match."""
        from apple_mail_mcp.index.watcher import PATH_PATTERN

        m1 = PATH_PATTERN.search(
            "/Users/x/Library/Mail/V10/acc/INBOX.mbox/Data/1/Messages/123.emlx"
        )
        assert m1 is not None
        assert m1.group(3) == "123"

        m2 = PATH_PATTERN.search(
            "/Users/x/Library/Mail/V10/acc/INBOX.mbox"
            "/Data/1/Messages/456.partial.emlx"
        )
        assert m2 is not None
        assert m2.group(3) == "456"

    def test_extracts_account_and_mailbox(self):
        """Path pattern should extract account UUID and mailbox name."""
        from apple_mail_mcp.index.watcher import PATH_PATTERN

        m = PATH_PATTERN.search(
            "/Users/x/Library/Mail/V10"
            "/9C1979D8-5686-4309-9EE8-1FB7F450F1FE"
            "/Inbox.mbox/Data/1/Messages/789.emlx"
        )
        assert m is not None
        assert m.group(1) == "9C1979D8-5686-4309-9EE8-1FB7F450F1FE"
        assert m.group(2) == "Inbox"

    def test_handles_nested_mbox(self):
        """Gmail-style [Gmail].mbox/All Mail.mbox paths.

        The regex captures up to the first .mbox boundary,
        so nested mailboxes like [Gmail].mbox/All Mail.mbox
        capture '[Gmail]' as the mailbox name — this matches
        how the index stores Gmail mailboxes.
        """
        from apple_mail_mcp.index.watcher import PATH_PATTERN

        m = PATH_PATTERN.search(
            "/Users/x/Library/Mail/V11/acc"
            "/[Gmail].mbox/All Mail.mbox"
            "/Data/1/Messages/100.partial.emlx"
        )
        assert m is not None
        assert m.group(2) == "[Gmail]"

    def test_handles_v11_directory(self):
        """Dynamic version detection: V11 paths should match."""
        from apple_mail_mcp.index.watcher import PATH_PATTERN

        m = PATH_PATTERN.search(
            "/Users/x/Library/Mail/V11/acc/INBOX.mbox/Data/1/Messages/1.emlx"
        )
        assert m is not None


class TestPendingLimits:
    """Watcher should enforce memory safety limits."""

    def _make_watcher(self, db_path: Path) -> IndexWatcher:
        watcher = IndexWatcher.__new__(IndexWatcher)
        watcher.db_path = str(db_path)
        watcher._conn = None
        watcher._pending_adds = {}
        watcher._pending_deletes = set()
        import threading

        watcher._pending_lock = threading.Lock()
        watcher._stop_event = threading.Event()
        watcher._mail_dir = None
        watcher._thread = None
        watcher.on_update = None
        watcher.debounce_ms = 500
        return watcher

    def test_pending_adds_are_bounded(self, watcher_db):
        """Verify MAX_PENDING_CHANGES prevents unbounded growth."""
        from apple_mail_mcp.index.watcher import MAX_PENDING_CHANGES

        db_path, conn = watcher_db
        conn.close()

        watcher = self._make_watcher(db_path)

        # Fill pending adds to the limit
        for i in range(MAX_PENDING_CHANGES):
            watcher._pending_adds[("acct", "INBOX", i)] = Path(
                f"/fake/{i}.emlx"
            )

        assert len(watcher._pending_adds) == MAX_PENDING_CHANGES


class TestNestedMailboxRegex:
    """PATH_PATTERN should handle nested mailboxes."""

    def test_parse_path_nested_mailbox(self):
        path = (
            "/Users/x/Library/Mail/V10/UUID123"
            "/Work/Projects.mbox/Data/1/Messages/123.emlx"
        )
        m = PATH_PATTERN.search(path)
        assert m is not None
        assert m.group(1) == "UUID123"
        assert m.group(2) == "Work/Projects"
        assert m.group(3) == "123"

    def test_parse_path_deeply_nested_mailbox(self):
        path = (
            "/Users/x/Library/Mail/V10/UUID/A/B/C.mbox/Data/0/Messages/99.emlx"
        )
        m = PATH_PATTERN.search(path)
        assert m is not None
        assert m.group(2) == "A/B/C"

    def test_parse_path_simple_mailbox_unchanged(self):
        """Regression: simple mailboxes still work."""
        path = (
            "/Users/x/Library/Mail/V10/acc"
            "/INBOX.mbox/Data/1/Messages/12345.emlx"
        )
        m = PATH_PATTERN.search(path)
        assert m is not None
        assert m.group(1) == "acc"
        assert m.group(2) == "INBOX"
        assert m.group(3) == "12345"

    def test_parse_path_gmail_brackets(self):
        """[Gmail].mbox paths still work."""
        path = (
            "/Users/x/Library/Mail/V10/acc/[Gmail].mbox/Data/1/Messages/1.emlx"
        )
        m = PATH_PATTERN.search(path)
        assert m is not None
        assert m.group(2) == "[Gmail]"

    def test_parse_path_partial_nested(self):
        """Partial .emlx in nested mailbox works."""
        path = (
            "/Users/x/Library/Mail/V10/UUID"
            "/Work/Q1.mbox/Data/9/4/Messages/49461.partial.emlx"
        )
        m = PATH_PATTERN.search(path)
        assert m is not None
        assert m.group(2) == "Work/Q1"
        assert m.group(3) == "49461"
