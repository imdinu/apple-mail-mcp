"""Recipients/direction search, sync requests, and the no-FDA lock rule."""

from __future__ import annotations

import sqlite3
import threading
from unittest.mock import MagicMock, patch

from apple_mail_mcp.index import freshness
from apple_mail_mcp.index.disk import (
    external_attachment_paths,
    format_recipients,
)
from apple_mail_mcp.index.lock import IndexLock
from apple_mail_mcp.index.schema import INSERT_EMAIL_SQL, init_database
from apple_mail_mcp.index.search import (
    direction_of,
    search_fts,
    search_participants,
)


def _insert(conn, mid, mailbox, sender, recipients, subject="Contract"):
    conn.execute(
        INSERT_EMAIL_SQL,
        (
            mid,
            "acct",
            mailbox,
            subject,
            sender,
            "completion date",
            f"2026-11-{mid:02d}T10:00:00",
            None,
            0,
            recipients,
        ),
    )


def _db(temp_db):
    _insert(temp_db, 1, "INBOX", "Jo <jo@lawfirm.example>", "me@example.com")
    _insert(
        temp_db,
        2,
        "Sent Messages",
        "me@example.com",
        "Jo <jo@lawfirm.example>, Ann <ann@agency.example>",
    )
    _insert(
        temp_db, 3, "INBOX", "news@shop.com", "me@example.com", subject="Sale"
    )
    temp_db.commit()
    return temp_db


class TestRecipients:
    def test_format_recipients(self):
        to = [{"name": "Jo Smith", "address": "jo@lawfirm.example"}]
        cc = [{"name": "", "address": "a@b.com"}]
        assert (
            format_recipients(to, cc)
            == "Jo Smith <jo@lawfirm.example>, a@b.com"
        )
        assert format_recipients([], []) == ""

    def test_direction_of(self):
        assert direction_of("Sent Messages") == "outbound"
        assert direction_of("Outbox") == "outbound"
        assert direction_of("INBOX") == "inbound"

    def test_fts_recipient_and_direction_filters(self, temp_db):
        conn = _db(temp_db)
        hits = search_fts(conn, "completion", recipient="agency.example")
        assert [h.id for h in hits] == [2]
        assert hits[0].direction == "outbound"
        assert "ann@agency.example" in hits[0].recipients
        inbound = search_fts(conn, "completion", direction="inbound")
        assert {h.id for h in inbound} == {1, 3}

    def test_participant_matches_both_directions_newest_first(self, temp_db):
        conn = _db(temp_db)
        hits = search_participants(conn, "lawfirm.example")
        assert [h.id for h in hits] == [2, 1]

    def test_like_wildcards_are_literal(self, temp_db):
        conn = _db(temp_db)
        assert search_participants(conn, "%") == []


class TestMigration:
    def test_v5_db_gains_null_recipients(self, tmp_path):
        db = tmp_path / "old.db"
        init_database(db).close()
        conn = sqlite3.connect(db)
        conn.execute("ALTER TABLE emails DROP COLUMN recipients")
        conn.execute("UPDATE schema_version SET version = 5")
        conn.execute(
            "INSERT INTO emails (message_id, account, mailbox, subject) "
            "VALUES (1, 'a', 'INBOX', 's')"
        )
        conn.commit()
        conn.close()
        conn = init_database(db)
        assert (
            conn.execute("SELECT recipients FROM emails").fetchone()[0] is None
        )


class TestBackfill:
    def test_backfill_reads_to_and_cc(self, tmp_path):
        from apple_mail_mcp.index.manager import IndexManager

        msg = (
            b"From: jo@lawfirm.example\r\nTo: Sam <me@example.com>\r\n"
            b"Cc: ann@agency.example\r\nSubject: x\r\n\r\nbody\r\n"
        )
        emlx = tmp_path / "1.emlx"
        emlx.write_bytes(str(len(msg)).encode() + b"\n" + msg)
        mgr = IndexManager(db_path=tmp_path / "i.db")
        conn = mgr._get_conn()
        conn.execute(
            "INSERT INTO emails (message_id, account, mailbox, emlx_path, "
            "recipients) VALUES (1, 'a', 'INBOX', ?, NULL)",
            (str(emlx),),
        )
        assert mgr.backfill_recipients() == 1
        value = conn.execute("SELECT recipients FROM emails").fetchone()[0]
        assert value == "Sam <me@example.com>, ann@agency.example"
        assert mgr.backfill_recipients() == 0


class TestExternalAttachments:
    def test_finds_inline_body_image(self, tmp_path):
        emlx = tmp_path / "Messages" / "42.partial.emlx"
        emlx.parent.mkdir()
        emlx.write_bytes(b"")
        img = tmp_path / "Attachments" / "42" / "1.2" / "image.png"
        img.parent.mkdir(parents=True)
        img.write_bytes(b"png")
        assert external_attachment_paths(emlx) == {"image.png": img}

    def test_none_when_no_directory(self, tmp_path):
        assert external_attachment_paths(tmp_path / "7.emlx") == {}


class TestSyncRequests:
    def test_writer_answers_request(self, tmp_path):
        manager = MagicMock()
        manager.db_path = tmp_path / "index.db"
        manager.index_writer = True
        manager.last_sync_blocked = None
        manager.sync_updates.return_value = 3
        stop = threading.Event()
        t = threading.Thread(
            target=freshness.serve_sync_requests,
            args=(manager, 0.05, stop),
            daemon=True,
        )
        t.start()
        try:
            stamp = freshness.request_sync(manager.db_path)
            done = freshness.wait_for_sync(manager.db_path, stamp, 3.0)
        finally:
            stop.set()
            t.join(2)
        assert done is not None and done["changes"] == 3
        assert "error" not in done

    def test_wait_times_out_without_writer(self, tmp_path):
        stamp = freshness.request_sync(tmp_path / "index.db")
        assert (
            freshness.wait_for_sync(tmp_path / "index.db", stamp, 0.3) is None
        )

    def test_lock_held_elsewhere(self, tmp_path):
        db = tmp_path / "index.db"
        assert freshness.lock_held_elsewhere(db) is False
        holder = IndexLock(db)
        holder.try_acquire()
        try:
            assert freshness.lock_held_elsewhere(db) is True
        finally:
            holder.release()
        assert freshness.lock_held_elsewhere(db) is False


class TestNoFullDiskAccess:
    def test_server_without_mail_access_leaves_lock_free(self, tmp_path):
        """A server that cannot read mail must not block `index`."""
        manager = MagicMock()
        manager.has_index.return_value = True
        manager.db_path = tmp_path / "index.db"
        manager.index_writer = True
        seen = {}

        def probe():
            seen["held"] = freshness.lock_held_elsewhere(manager.db_path)

        mcp = MagicMock()
        mcp.run.side_effect = probe
        with (
            patch(
                "apple_mail_mcp.index.IndexManager.get_instance",
                return_value=manager,
            ),
            patch("apple_mail_mcp.server.mcp", mcp),
            patch("apple_mail_mcp.server._cleanup_old_attachments"),
            patch.object(freshness, "mail_readable", lambda: False),
        ):
            from apple_mail_mcp.cli import _run_serve

            _run_serve(watch=False)
        assert seen["held"] is False
        assert manager.index_writer is False
        manager.sync_updates.assert_not_called()
