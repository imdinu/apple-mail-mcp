"""Tests for v0.1.6 changes: #51, #50, #48, links."""

from __future__ import annotations

import email
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apple_mail_mcp.index.disk import (
    _extract_links_from_message,
    _infer_account_mailbox,
    get_email_links,
)
from apple_mail_mcp.index.watcher import PATH_PATTERN


# ========== #48: Nested mailbox regex ==========


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


class TestInferNestedMailbox:
    """_infer_account_mailbox should handle nested mailboxes."""

    def test_infer_simple_mailbox(self, tmp_path: Path):
        mail_dir = tmp_path / "V10"
        emlx = (
            mail_dir
            / "acc-uuid"
            / "INBOX.mbox"
            / "Data"
            / "Messages"
            / "1.emlx"
        )
        emlx.parent.mkdir(parents=True)
        emlx.touch()

        account, mailbox = _infer_account_mailbox(emlx, mail_dir)
        assert account == "acc-uuid"
        assert mailbox == "INBOX"

    def test_infer_nested_mailbox(self, tmp_path: Path):
        mail_dir = tmp_path / "V10"
        emlx = (
            mail_dir
            / "acc-uuid"
            / "Work"
            / "Projects.mbox"
            / "Data"
            / "Messages"
            / "1.emlx"
        )
        emlx.parent.mkdir(parents=True)
        emlx.touch()

        account, mailbox = _infer_account_mailbox(emlx, mail_dir)
        assert account == "acc-uuid"
        assert mailbox == "Work/Projects"

    def test_infer_deeply_nested_mailbox(self, tmp_path: Path):
        mail_dir = tmp_path / "V10"
        emlx = (
            mail_dir
            / "acc"
            / "A"
            / "B"
            / "C.mbox"
            / "Data"
            / "Messages"
            / "1.emlx"
        )
        emlx.parent.mkdir(parents=True)
        emlx.touch()

        account, mailbox = _infer_account_mailbox(emlx, mail_dir)
        assert account == "acc"
        assert mailbox == "A/B/C"

    def test_infer_no_mbox_suffix(self, tmp_path: Path):
        """Paths without .mbox return 'Unknown'."""
        mail_dir = tmp_path / "V10"
        emlx = mail_dir / "acc" / "SomeDir" / "1.emlx"
        emlx.parent.mkdir(parents=True)
        emlx.touch()

        account, mailbox = _infer_account_mailbox(emlx, mail_dir)
        assert account == "acc"
        assert mailbox == "Unknown"


# ========== #50: File-based attachment return ==========


class TestAttachmentSaveToFile:
    """get_attachment should save to disk and return file_path."""

    @pytest.mark.asyncio
    async def test_get_attachment_saves_to_file(self, tmp_path: Path):
        """Attachment bytes are written to disk, path returned."""
        mock_manager = MagicMock()
        mock_manager.has_index.return_value = True
        mock_manager.find_email_path.return_value = Path("/fake/path/42.emlx")

        fake_bytes = b"fake pdf content here"
        fake_result = (fake_bytes, "application/pdf")

        with (
            patch("apple_mail_mcp.server._get_index_manager") as mock_get,
            patch(
                "apple_mail_mcp.server.asyncio.to_thread",
                new_callable=AsyncMock,
            ) as mock_thread,
            patch(
                "apple_mail_mcp.server.ATTACHMENT_CACHE_DIR",
                tmp_path / "attachments",
            ),
        ):
            mock_get.return_value = mock_manager
            mock_thread.return_value = fake_result

            from apple_mail_mcp.server import get_attachment

            result = await get_attachment(42, "invoice.pdf")

            assert result["filename"] == "invoice.pdf"
            assert result["mime_type"] == "application/pdf"
            assert result["size"] == len(fake_bytes)
            assert "file_path" in result
            assert "content_base64" not in result

            # Verify file was actually written
            saved = Path(result["file_path"])
            assert saved.exists()
            assert saved.read_bytes() == fake_bytes

    @pytest.mark.asyncio
    async def test_get_attachment_safe_filename(self, tmp_path: Path):
        """Path traversal in filename is stripped."""
        mock_manager = MagicMock()
        mock_manager.has_index.return_value = True
        mock_manager.find_email_path.return_value = Path("/fake/path/42.emlx")

        fake_result = (b"data", "text/plain")

        with (
            patch("apple_mail_mcp.server._get_index_manager") as mock_get,
            patch(
                "apple_mail_mcp.server.asyncio.to_thread",
                new_callable=AsyncMock,
            ) as mock_thread,
            patch(
                "apple_mail_mcp.server.ATTACHMENT_CACHE_DIR",
                tmp_path / "attachments",
            ),
        ):
            mock_get.return_value = mock_manager
            mock_thread.return_value = fake_result

            from apple_mail_mcp.server import get_attachment

            result = await get_attachment(42, "../../evil.txt")

            # Should strip to just "evil.txt"
            assert result["filename"] == "evil.txt"
            assert Path(result["file_path"]).name == "evil.txt"


class TestCleanupOldAttachments:
    """_cleanup_old_attachments removes stale dirs."""

    def test_cleanup_removes_old_dirs(self, tmp_path: Path):
        cache_dir = tmp_path / "attachments"
        cache_dir.mkdir()

        # Create an old subdirectory
        old_dir = cache_dir / "old_extraction"
        old_dir.mkdir()
        (old_dir / "file.pdf").write_bytes(b"old data")
        # Set mtime to 48 hours ago
        old_time = time.time() - (48 * 3600)
        os.utime(old_dir, (old_time, old_time))

        with patch("apple_mail_mcp.server.ATTACHMENT_CACHE_DIR", cache_dir):
            from apple_mail_mcp.server import _cleanup_old_attachments

            _cleanup_old_attachments(max_age_hours=24)

        assert not old_dir.exists()

    def test_cleanup_preserves_recent_dirs(self, tmp_path: Path):
        cache_dir = tmp_path / "attachments"
        cache_dir.mkdir()

        # Create a recent subdirectory
        recent_dir = cache_dir / "recent_extraction"
        recent_dir.mkdir()
        (recent_dir / "file.pdf").write_bytes(b"recent data")

        with patch("apple_mail_mcp.server.ATTACHMENT_CACHE_DIR", cache_dir):
            from apple_mail_mcp.server import _cleanup_old_attachments

            _cleanup_old_attachments(max_age_hours=24)

        assert recent_dir.exists()

    def test_cleanup_handles_missing_dir(self):
        """No error when cache dir doesn't exist."""
        with patch(
            "apple_mail_mcp.server.ATTACHMENT_CACHE_DIR",
            Path("/nonexistent/path"),
        ):
            from apple_mail_mcp.server import _cleanup_old_attachments

            _cleanup_old_attachments()  # Should not raise


# ========== #51: Non-blocking startup ==========


class TestNonBlockingStartup:
    """_run_serve should not block on sync."""

    def test_run_serve_does_not_block(self):
        """mcp.run() is called immediately, not after sync."""
        mock_manager = MagicMock()
        mock_manager.has_index.return_value = True
        # sync_updates sleeps to simulate slow sync
        mock_manager.sync_updates.side_effect = lambda: (time.sleep(5) or 0)

        mock_mcp = MagicMock()

        with (
            patch(
                "apple_mail_mcp.index.IndexManager.get_instance",
                return_value=mock_manager,
            ),
            patch("apple_mail_mcp.server.mcp", mock_mcp),
            patch("apple_mail_mcp.server._cleanup_old_attachments"),
        ):
            from apple_mail_mcp.cli import _run_serve

            start = time.time()
            _run_serve(watch=False)
            elapsed = time.time() - start

            # mcp.run() should be called within ~1s, not 5s
            assert elapsed < 2.0
            mock_mcp.run.assert_called_once()

    def test_search_does_not_trigger_sync(self):
        """Verify _sync_lock and auto-sync were removed."""
        import apple_mail_mcp.server as srv

        assert not hasattr(srv, "_sync_lock")


# ========== Link extraction ==========


class TestExtractLinksFromMessage:
    """_extract_links_from_message parses HTML <a> tags."""

    def test_extracts_links_from_html(self):
        msg = email.message_from_string(
            "Content-Type: text/html\n\n"
            "<html><body>"
            '<a href="https://example.com">Example</a>'
            '<a href="https://other.com/page">Other</a>'
            "</body></html>"
        )
        links = _extract_links_from_message(msg)
        assert len(links) == 2
        assert links[0].url == "https://example.com"
        assert links[0].text == "Example"
        assert links[1].url == "https://other.com/page"

    def test_skips_mailto_links(self):
        msg = email.message_from_string(
            "Content-Type: text/html\n\n"
            '<a href="mailto:user@example.com">Email me</a>'
            '<a href="https://real.com">Real</a>'
        )
        links = _extract_links_from_message(msg)
        assert len(links) == 1
        assert links[0].url == "https://real.com"

    def test_skips_javascript_links(self):
        msg = email.message_from_string(
            'Content-Type: text/html\n\n<a href="javascript:alert(1)">XSS</a>'
        )
        links = _extract_links_from_message(msg)
        assert len(links) == 0

    def test_skips_long_tracking_urls(self):
        long_url = "https://click.track.com/" + "a" * 200
        msg = email.message_from_string(
            "Content-Type: text/html\n\n"
            f'<a href="{long_url}">Tracked</a>'
            '<a href="https://short.com">Short</a>'
        )
        links = _extract_links_from_message(msg)
        assert len(links) == 1
        assert links[0].url == "https://short.com"

    def test_deduplicates_by_url(self):
        msg = email.message_from_string(
            "Content-Type: text/html\n\n"
            '<a href="https://same.com">First</a>'
            '<a href="https://same.com">Second</a>'
        )
        links = _extract_links_from_message(msg)
        assert len(links) == 1
        assert links[0].text == "First"

    def test_handles_plain_text_email(self):
        msg = email.message_from_string(
            "Content-Type: text/plain\n\nNo HTML here https://example.com"
        )
        links = _extract_links_from_message(msg)
        assert len(links) == 0

    def test_handles_multipart_with_html(self):
        raw = (
            'Content-Type: multipart/alternative; boundary="b"\n\n'
            "--b\n"
            "Content-Type: text/plain\n\n"
            "Plain text\n"
            "--b\n"
            "Content-Type: text/html\n\n"
            '<a href="https://example.com">Link</a>\n'
            "--b--"
        )
        msg = email.message_from_string(raw)
        links = _extract_links_from_message(msg)
        assert len(links) == 1
        assert links[0].url == "https://example.com"

    def test_empty_href_skipped(self):
        msg = email.message_from_string(
            "Content-Type: text/html\n\n"
            '<a href="">Empty</a>'
            '<a href="  ">Whitespace</a>'
        )
        links = _extract_links_from_message(msg)
        assert len(links) == 0


class TestGetEmailLinks:
    """get_email_links reads .emlx file and extracts links."""

    def test_extracts_links_from_emlx(self, tmp_path: Path):
        html_body = (
            '<html><body><a href="https://example.com">Link</a></body></html>'
        )
        mime = ("Content-Type: text/html\n\n" + html_body).encode()
        emlx = tmp_path / "1.emlx"
        emlx.write_bytes(f"{len(mime)}\n".encode() + mime)

        links = get_email_links(emlx)
        assert len(links) == 1
        assert links[0].url == "https://example.com"

    def test_returns_empty_for_missing_file(self, tmp_path: Path):
        links = get_email_links(tmp_path / "nope.emlx")
        assert links == []


# ========== Empty-result hints ==========


class TestSearchEmptyResultHint:
    """search() returns a hint dict when no results found."""

    @pytest.mark.asyncio
    async def test_fts_empty_returns_hint(self):
        """FTS5 path returns hint when no results."""
        mock_manager = MagicMock()
        mock_manager.has_index.return_value = True
        mock_manager.search.return_value = []

        mock_acct_map = MagicMock()
        mock_acct_map.ensure_loaded = AsyncMock()

        with (
            patch("apple_mail_mcp.server._get_index_manager") as mock_get,
            patch("apple_mail_mcp.server._get_account_map") as mock_get_acct,
        ):
            mock_get.return_value = mock_manager
            mock_get_acct.return_value = mock_acct_map

            from apple_mail_mcp.server import search

            result = await search("xyznonexistent123")

            assert isinstance(result, dict)
            assert result["result"] == []
            assert "hint" in result
            assert "fewer keywords" in result["hint"]

    @pytest.mark.asyncio
    async def test_fts_with_results_returns_list(self):
        """FTS5 path returns plain list when results found."""
        from apple_mail_mcp.index.search import SearchResult

        mock_result = SearchResult(
            id=1,
            account="acc-uuid",
            mailbox="INBOX",
            subject="Test",
            sender="a@b.com",
            content_snippet="snippet",
            date_received="2024-01-01",
            score=1.0,
        )
        mock_manager = MagicMock()
        mock_manager.has_index.return_value = True
        mock_manager.search.return_value = [mock_result]

        mock_acct_map = MagicMock()
        mock_acct_map.ensure_loaded = AsyncMock()
        mock_acct_map.uuid_to_name.return_value = "Work"

        with (
            patch("apple_mail_mcp.server._get_index_manager") as mock_get,
            patch("apple_mail_mcp.server._get_account_map") as mock_get_acct,
        ):
            mock_get.return_value = mock_manager
            mock_get_acct.return_value = mock_acct_map

            from apple_mail_mcp.server import search

            result = await search("test")

            assert isinstance(result, list)
            assert len(result) == 1
            assert result[0]["id"] == 1


class TestGetAttachmentLinksMode:
    """get_attachment with filename=None returns links."""

    @pytest.mark.asyncio
    async def test_returns_links_when_no_filename(self, tmp_path: Path):
        from apple_mail_mcp.index.disk import LinkInfo

        mock_manager = MagicMock()
        mock_manager.has_index.return_value = True
        mock_manager.find_email_path.return_value = Path("/fake/42.emlx")

        fake_links = [
            LinkInfo(url="https://example.com", text="Example"),
            LinkInfo(url="https://other.com", text="Other"),
        ]

        with (
            patch("apple_mail_mcp.server._get_index_manager") as mock_get,
            patch(
                "apple_mail_mcp.server.asyncio.to_thread",
                new_callable=AsyncMock,
            ) as mock_thread,
        ):
            mock_get.return_value = mock_manager
            mock_thread.return_value = fake_links

            from apple_mail_mcp.server import get_attachment

            result = await get_attachment(42)

            assert "links" in result
            assert len(result["links"]) == 2
            assert result["links"][0]["url"] == "https://example.com"
            assert result["links"][0]["text"] == "Example"
            assert "file_path" not in result
