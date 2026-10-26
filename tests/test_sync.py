"""Tests for disk-based sync functionality (v0.4.0)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from apple_mail_mcp.index.disk import get_disk_inventory
from apple_mail_mcp.index.schema import SCHEMA_VERSION, get_schema_sql
from apple_mail_mcp.index.sync import (
    SyncResult,
    get_db_inventory,
    sync_from_disk,
)
from apple_mail_mcp.index.watcher import PATH_PATTERN


class TestWatcherPathPattern:
    """Tests for watcher PATH_PATTERN regex (#39)."""

    def test_matches_regular_emlx(self):
        path = "/Users/x/Library/Mail/V10/acc/INBOX.mbox/Data/1/Messages/12345.emlx"
        m = PATH_PATTERN.search(path)
        assert m is not None
        assert m.group(1) == "acc"
        assert m.group(2) == "INBOX"
        assert m.group(3) == "12345"

    def test_matches_partial_emlx(self):
        path = "/Users/x/Library/Mail/V10/acc/INBOX.mbox/Data/1/Messages/67301.partial.emlx"
        m = PATH_PATTERN.search(path)
        assert m is not None
        assert m.group(1) == "acc"
        assert m.group(2) == "INBOX"
        assert m.group(3) == "67301"

    def test_rejects_non_emlx(self):
        path = (
            "/Users/x/Library/Mail/V10/acc/INBOX.mbox/Data/1/Messages/12345.txt"
        )
        assert PATH_PATTERN.search(path) is None


class TestSyncResult:
    """Tests for SyncResult dataclass."""

    def test_total_changes(self):
        result = SyncResult(added=5, deleted=3, moved=2, errors=1)
        assert result.total_changes == 10  # 5 + 3 + 2

    def test_zero_changes(self):
        result = SyncResult(added=0, deleted=0, moved=0, errors=0)
        assert result.total_changes == 0


class TestGetDbInventory:
    """Tests for database inventory function."""

    def test_empty_database(self, temp_db: sqlite3.Connection):
        inventory = get_db_inventory(temp_db)
        assert inventory == {}

    def test_returns_email_paths(self, temp_db: sqlite3.Connection):
        # Insert emails with paths
        temp_db.execute(
            """INSERT INTO emails
               (message_id, account, mailbox, subject, emlx_path)
               VALUES (1, 'acc1', 'INBOX', 'Test', '/path/to/1.emlx')"""
        )
        temp_db.execute(
            """INSERT INTO emails
               (message_id, account, mailbox, subject, emlx_path)
               VALUES (2, 'acc1', 'INBOX', 'Test2', '/path/to/2.emlx')"""
        )
        temp_db.commit()

        inventory = get_db_inventory(temp_db)
        assert len(inventory) == 2
        assert inventory[("acc1", "INBOX", 1)] == "/path/to/1.emlx"
        assert inventory[("acc1", "INBOX", 2)] == "/path/to/2.emlx"

    def test_handles_null_paths(self, temp_db: sqlite3.Connection):
        # Insert email without path (legacy data)
        temp_db.execute(
            """INSERT INTO emails
               (message_id, account, mailbox, subject)
               VALUES (1, 'acc1', 'INBOX', 'Test')"""
        )
        temp_db.commit()

        inventory = get_db_inventory(temp_db)
        assert inventory[("acc1", "INBOX", 1)] == ""


class TestGetDiskInventory:
    """Tests for disk inventory scanning."""

    def test_empty_directory(self, tmp_path: Path):
        mail_dir = tmp_path / "V10"
        mail_dir.mkdir()
        inventory = get_disk_inventory(mail_dir)
        assert inventory == {}

    def test_finds_emlx_files(self, tmp_path: Path):
        # Create mail directory structure
        mail_dir = tmp_path / "V10"
        mbox = mail_dir / "account-uuid" / "INBOX.mbox" / "Data" / "Messages"
        mbox.mkdir(parents=True)

        # Create emlx files
        (mbox / "12345.emlx").write_bytes(b"test")
        (mbox / "67890.emlx").write_bytes(b"test")

        inventory = get_disk_inventory(mail_dir)
        assert len(inventory) == 2
        assert ("account-uuid", "INBOX", 12345) in inventory
        assert ("account-uuid", "INBOX", 67890) in inventory

    def test_includes_partial_files(self, tmp_path: Path):
        """Partial .emlx files are now indexed (#39)."""
        mail_dir = tmp_path / "V10"
        mbox = mail_dir / "acc" / "INBOX.mbox" / "Data" / "Messages"
        mbox.mkdir(parents=True)

        # Create normal and partial files (same message ID)
        (mbox / "12345.emlx").write_bytes(b"test")
        (mbox / "12345.partial.emlx").write_bytes(b"partial")

        inventory = get_disk_inventory(mail_dir)
        # Both map to the same (acc, INBOX, 12345) key — last one wins
        assert ("acc", "INBOX", 12345) in inventory

    def test_handles_nested_mbox_structure(self, tmp_path: Path):
        mail_dir = tmp_path / "V10"
        # Deep nesting: acc/Folder.mbox/Subfolder.mbox/Data/Messages/
        mbox = (
            mail_dir
            / "acc"
            / "Folder.mbox"
            / "Subfolder.mbox"
            / "Data"
            / "Messages"
        )
        mbox.mkdir(parents=True)
        (mbox / "1.emlx").write_bytes(b"test")

        inventory = get_disk_inventory(mail_dir)
        assert len(inventory) == 1


class TestSyncFromDisk:
    """Tests for disk-based state reconciliation."""

    @pytest.fixture
    def sync_db(self, tmp_path: Path):
        """Create a database for sync testing."""
        db_path = tmp_path / "sync_test.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(get_schema_sql())
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)",
            (SCHEMA_VERSION,),
        )
        conn.commit()
        yield conn
        conn.close()

    @pytest.fixture
    def mail_dir(self, tmp_path: Path) -> Path:
        """Create a mock mail directory."""
        mail_dir = tmp_path / "Mail" / "V10"
        mail_dir.mkdir(parents=True)
        return mail_dir

    def _create_emlx(
        self, mail_dir: Path, account: str, mailbox: str, msg_id: int
    ) -> Path:
        """Helper to create a valid emlx file."""
        mbox = mail_dir / account / f"{mailbox}.mbox" / "Data" / "Messages"
        mbox.mkdir(parents=True, exist_ok=True)

        # Create minimal valid emlx
        emlx_path = mbox / f"{msg_id}.emlx"
        content = b"100\nFrom: test@test.com\nSubject: Test\n\nBody text"
        emlx_path.write_bytes(content)
        return emlx_path

    def test_sync_empty_both(self, sync_db: sqlite3.Connection, mail_dir: Path):
        """Sync with empty DB and empty disk should be no-op."""
        result = sync_from_disk(sync_db, mail_dir)
        assert result.added == 0
        assert result.deleted == 0
        assert result.moved == 0

    def test_sync_detects_new_emails(
        self, sync_db: sqlite3.Connection, mail_dir: Path
    ):
        """New files on disk should be added to DB."""
        self._create_emlx(mail_dir, "acc1", "INBOX", 1001)
        self._create_emlx(mail_dir, "acc1", "INBOX", 1002)

        result = sync_from_disk(sync_db, mail_dir)
        assert result.added == 2
        assert result.deleted == 0

        # Verify in DB
        cursor = sync_db.execute("SELECT COUNT(*) FROM emails")
        assert cursor.fetchone()[0] == 2

    def test_sync_detects_deleted_emails(
        self, sync_db: sqlite3.Connection, mail_dir: Path
    ):
        """Emails in DB but not on disk should be deleted."""
        # Pre-populate DB with an email that doesn't exist on disk
        sync_db.execute(
            """INSERT INTO emails
               (message_id, account, mailbox, subject, emlx_path)
               VALUES (999, 'ghost', 'INBOX', 'Deleted', '/gone.emlx')"""
        )
        sync_db.commit()

        result = sync_from_disk(sync_db, mail_dir)
        assert result.deleted == 1

        # Verify removed from DB
        cursor = sync_db.execute("SELECT COUNT(*) FROM emails")
        assert cursor.fetchone()[0] == 0

    def test_sync_detects_moved_emails(
        self, sync_db: sqlite3.Connection, mail_dir: Path
    ):
        """Emails with changed paths should be updated."""
        # Create file at new location
        new_path = self._create_emlx(mail_dir, "acc1", "Archive", 1001)

        # Pre-populate DB with old path
        sync_db.execute(
            """INSERT INTO emails
               (message_id, account, mailbox, subject, emlx_path)
               VALUES (1001, 'acc1', 'Archive', 'Moved', '/old/path.emlx')"""
        )
        sync_db.commit()

        result = sync_from_disk(sync_db, mail_dir)
        assert result.moved == 1

        # Verify path updated
        cursor = sync_db.execute(
            "SELECT emlx_path FROM emails WHERE message_id = 1001"
        )
        assert str(new_path) in cursor.fetchone()[0]

    def test_sync_sorts_new_by_mtime(
        self, sync_db: sqlite3.Connection, mail_dir: Path
    ):
        """With cap=1, the newer file should be indexed."""
        import os
        import time

        # Create older file first
        older = self._create_emlx(mail_dir, "acc1", "INBOX", 1001)
        time.sleep(0.05)
        # Create newer file
        self._create_emlx(mail_dir, "acc1", "INBOX", 1002)

        # Make sure mtime differs
        os.utime(older, (time.time() - 100, time.time() - 100))

        with patch(
            "apple_mail_mcp.index.sync.get_index_max_emails",
            return_value=1,
        ):
            result = sync_from_disk(sync_db, mail_dir)

        # Only 1 should be indexed (the newer one, msg 1002)
        assert result.added == 1
        cursor = sync_db.execute("SELECT message_id FROM emails")
        msg_id = cursor.fetchone()["message_id"]
        assert msg_id == 1002

    def test_sync_logs_cap_warning(
        self, sync_db: sqlite3.Connection, mail_dir: Path, caplog
    ):
        """Sync logs an aggregate cap warning when mailboxes hit limit."""
        import logging

        # Create more files than the cap
        for i in range(3):
            self._create_emlx(mail_dir, "acc1", "INBOX", 2000 + i)

        with (
            patch(
                "apple_mail_mcp.index.sync.get_index_max_emails",
                return_value=1,
            ),
            caplog.at_level(logging.WARNING),
        ):
            sync_from_disk(sync_db, mail_dir)

        assert "hit cap" in caplog.text


class TestSyncIsolatesHostileFiles:
    """One bad .emlx must not abort the reconcile or hold the lock (#125)."""

    PLIST = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<plist version="1.0"><dict>'
        b"<key>flags</key><integer>0</integer>"
        b"<key>date-received</key><real>1160347200</real>"
        b"</dict></plist>\n"
    )

    @pytest.fixture
    def sync_db(self, tmp_path: Path):
        db_path = tmp_path / "sync_test.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(get_schema_sql())
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)",
            (SCHEMA_VERSION,),
        )
        conn.commit()
        yield conn
        conn.close()

    @pytest.fixture
    def mail_dir(self, tmp_path: Path) -> Path:
        mail_dir = tmp_path / "Mail" / "V10"
        mail_dir.mkdir(parents=True)
        return mail_dir

    def _write(self, mail_dir: Path, msg_id: int, headers: bytes) -> Path:
        mbox = mail_dir / "acc1" / "INBOX.mbox" / "Data" / "Messages"
        mbox.mkdir(parents=True, exist_ok=True)
        msg = headers + b"Content-Type: text/plain; charset=us-ascii\n\nbody\n"
        path = mbox / f"{msg_id}.emlx"
        path.write_bytes(f"{len(msg)}\n".encode() + msg + self.PLIST)
        return path

    def _write_ok(self, mail_dir: Path, msg_id: int) -> Path:
        return self._write(
            mail_dir,
            msg_id,
            f"From: a@example.com\nSubject: {msg_id}\n".encode(),
        )

    def test_hostile_date_header_is_indexed_not_fatal(
        self, sync_db: sqlite3.Connection, mail_dir: Path
    ):
        self._write_ok(mail_dir, 1001)
        self._write(
            mail_dir,
            1002,
            b"From: a@example.com\nSubject: old\n"
            b"Date: Sun, 08 Oct 2006 23:34:16 \xef\xbf\xbd0100\n",
        )
        self._write(
            mail_dir,
            1003,
            b"From: =?utf-8?B?x?= <sender@example.com>\nSubject: spam\n",
        )

        result = sync_from_disk(sync_db, mail_dir)

        assert result.added == 3
        assert result.errors == 0
        assert not sync_db.in_transaction

    def test_unparseable_file_goes_to_dlq_and_sync_continues(
        self, sync_db: sqlite3.Connection, mail_dir: Path, monkeypatch
    ):
        from apple_mail_mcp.index import disk as disk_module

        self._write_ok(mail_dir, 1001)
        bad = self._write_ok(mail_dir, 1002)
        self._write_ok(mail_dir, 1003)

        real_parse = disk_module.parse_emlx

        def parse_or_explode(path: Path):
            if path == bad:
                # Not an EmlxParseError on purpose: whatever a future
                # parser throws must still be contained per file.
                raise AttributeError("'Header' object has no attribute 'split'")
            return real_parse(path)

        monkeypatch.setattr(disk_module, "parse_emlx", parse_or_explode)

        result = sync_from_disk(sync_db, mail_dir)

        assert result.added == 2
        assert result.errors == 1
        assert not sync_db.in_transaction
        rows = sync_db.execute(
            "SELECT emlx_path, error_type, error_message FROM failed_index_jobs"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["emlx_path"] == str(bad)
        assert rows[0]["error_type"] == "AttributeError"
        assert "split" in rows[0]["error_message"]
        # The other two files are in, and the sync is recorded as done.
        assert sync_db.execute("SELECT COUNT(*) FROM emails").fetchone()[0] == 2
        assert sync_db.execute("SELECT COUNT(*) FROM sync_state").fetchone()[0]

    def test_dlq_names_the_real_cause_not_the_wrapper(
        self, sync_db: sqlite3.Connection, mail_dir: Path, monkeypatch
    ):
        bad = self._write_ok(mail_dir, 1002)

        def denied(_self):
            raise PermissionError(1, "Operation not permitted")

        monkeypatch.setattr(Path, "read_bytes", denied)

        result = sync_from_disk(sync_db, mail_dir)

        assert result.added == 0
        assert result.errors == 1
        row = sync_db.execute(
            "SELECT emlx_path, error_type FROM failed_index_jobs"
        ).fetchone()
        assert row["emlx_path"] == str(bad)
        assert row["error_type"] == "PermissionError"

    def test_db_error_rolls_back_the_whole_reconcile(
        self, sync_db: sqlite3.Connection, mail_dir: Path, monkeypatch
    ):
        """A non-per-file failure propagates, but never leaves the
        connection inside its transaction (that held the write lock for
        the life of the process and turned into 'database is locked' for
        every other writer)."""
        from apple_mail_mcp.index import sync as sync_module

        self._write_ok(mail_dir, 1001)
        self._write_ok(mail_dir, 1002)

        real_row = sync_module.email_to_row
        calls = {"n": 0}

        def row_then_explode(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise sqlite3.OperationalError("database is locked")
            return real_row(*args, **kwargs)

        monkeypatch.setattr(sync_module, "email_to_row", row_then_explode)

        with pytest.raises(sqlite3.OperationalError):
            sync_from_disk(sync_db, mail_dir)

        assert not sync_db.in_transaction
        assert sync_db.execute("SELECT COUNT(*) FROM emails").fetchone()[0] == 0
        assert (
            sync_db.execute("SELECT COUNT(*) FROM sync_state").fetchone()[0]
            == 0
        )
