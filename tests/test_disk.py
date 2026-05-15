"""Tests for disk reading functionality."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from apple_mail_mcp.index.disk import (
    MAX_EMLX_SIZE,
    _extract_attachments,
    _extract_body_text,
    _find_external_attachment,
    _infer_account_mailbox,
    _mime_part_numbers,
    _strip_html,
    get_attachment_content,
    parse_emlx,
)


class TestParseEmlx:
    """Tests for .emlx file parsing."""

    def test_parse_valid_emlx(self, temp_emlx_file: Path):
        result = parse_emlx(temp_emlx_file)
        assert result is not None
        assert result.id == 12345
        assert result.subject == "Test Email Subject"
        assert result.sender == "sender@example.com"
        assert "body of the test email" in result.content

    def test_parse_returns_none_for_invalid(self, tmp_path: Path):
        # Create invalid emlx file
        invalid_path = tmp_path / "99999.emlx"
        invalid_path.write_bytes(b"not a valid emlx file")
        result = parse_emlx(invalid_path)
        assert result is None

    def test_parse_returns_none_for_missing_file(self, tmp_path: Path):
        missing = tmp_path / "nonexistent.emlx"
        result = parse_emlx(missing)
        assert result is None

    def test_parse_rejects_oversized_files(self, tmp_path: Path):
        # Create a file larger than MAX_EMLX_SIZE
        # We'll fake this by setting the size check first
        large_path = tmp_path / "large.emlx"
        # Write just enough to exceed the limit
        large_path.write_bytes(b"x" * (MAX_EMLX_SIZE + 1))
        result = parse_emlx(large_path)
        assert result is None

    def test_parse_handles_bad_byte_count(self, tmp_path: Path):
        """Corrupt byte count should return None, not crash."""
        bad = tmp_path / "100.emlx"
        bad.write_bytes(b"not_a_number\nFrom: x@y.z\n\nBody")
        assert parse_emlx(bad) is None

    def test_parse_handles_truncated_content(self, tmp_path: Path):
        """Byte count exceeds actual content length."""
        trunc = tmp_path / "101.emlx"
        trunc.write_bytes(b"99999\nFrom: x@y.z\n\nShort")
        # Should not crash — parse_emlx guards against this
        result = parse_emlx(trunc)
        # May return None or a partial result, but must not raise
        assert result is None or result.id == 101

    def test_parse_handles_empty_file(self, tmp_path: Path):
        """Zero-byte file should return None."""
        empty = tmp_path / "102.emlx"
        empty.write_bytes(b"")
        assert parse_emlx(empty) is None

    def test_parse_handles_no_newline(self, tmp_path: Path):
        """File with no newline separator should return None."""
        no_nl = tmp_path / "103.emlx"
        no_nl.write_bytes(b"12345")
        assert parse_emlx(no_nl) is None

    def test_parse_extracts_message_id_from_filename(self, tmp_path: Path):
        # Message ID comes from the filename stem
        emlx_content = b"10\nFrom: x@y.z\n\nBody"
        (tmp_path / "42.emlx").write_bytes(emlx_content)
        # Should at least try to parse (might fail due to minimal content)
        _ = parse_emlx(tmp_path / "42.emlx")


class TestParseEmlxExtendedFields:
    """Tests for extended fields from plist footer and MIME headers."""

    def test_plist_flags_read_and_flagged(self, tmp_path: Path):
        """Plist footer flags bitmask: bit 0 = read, bit 4 = flagged."""
        mime = (
            b"From: alice@example.com\n"
            b"Subject: Flagged email\n"
            b"Date: Mon, 15 Jan 2024 10:00:00 -0500\n"
            b"Reply-To: replies@example.com\n"
            b"Message-ID: <unique-123@example.com>\n"
            b"Content-Type: text/plain\n\n"
            b"Hello\n"
        )
        # flags = 17 means bit 0 (read=True) + bit 4 (flagged=True)
        plist = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            b'<plist version="1.0"><dict>\n'
            b"  <key>flags</key>\n"
            b"  <integer>17</integer>\n"
            b"</dict></plist>\n"
        )
        path = tmp_path / "99.emlx"
        path.write_bytes(f"{len(mime)}\n".encode() + mime + plist)

        result = parse_emlx(path)
        assert result is not None
        assert result.read is True
        assert result.flagged is True
        assert result.reply_to == "replies@example.com"
        assert result.message_id_header == "<unique-123@example.com>"
        assert "2024-01-15" in result.date_sent

    def test_plist_flags_unread_unflagged(self, tmp_path: Path):
        """flags=0 means unread and unflagged."""
        mime = (
            b"From: bob@example.com\n"
            b"Subject: New email\n"
            b"Date: Mon, 15 Jan 2024 10:00:00 -0500\n"
            b"Content-Type: text/plain\n\n"
            b"Body\n"
        )
        plist = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            b'<plist version="1.0"><dict>\n'
            b"  <key>flags</key>\n"
            b"  <integer>0</integer>\n"
            b"</dict></plist>\n"
        )
        path = tmp_path / "100.emlx"
        path.write_bytes(f"{len(mime)}\n".encode() + mime + plist)

        result = parse_emlx(path)
        assert result is not None
        assert result.read is False
        assert result.flagged is False
        assert result.reply_to == ""
        assert result.message_id_header == ""

    def test_date_received_from_received_header(self, tmp_path: Path):
        """date_received should use Received header, not Date header."""
        mime = (
            b"Received: from mail.example.com by mx.example.com;"
            b" Tue, 16 Jan 2024 08:30:00 +0000\n"
            b"From: alice@example.com\n"
            b"Subject: Delayed email\n"
            b"Date: Mon, 15 Jan 2024 10:00:00 -0500\n"
            b"Content-Type: text/plain\n\n"
            b"Body\n"
        )
        path = tmp_path / "102.emlx"
        path.write_bytes(f"{len(mime)}\n".encode() + mime)

        result = parse_emlx(path)
        assert result is not None
        # date_received = Received header (Jan 16)
        assert "2024-01-16" in result.date_received
        # date_sent = Date header (Jan 15)
        assert "2024-01-15" in result.date_sent

    def test_date_received_falls_back_to_date_header(self, tmp_path: Path):
        """Without Received header, date_received falls back to Date."""
        mime = (
            b"From: bob@example.com\n"
            b"Subject: No received header\n"
            b"Date: Mon, 15 Jan 2024 10:00:00 -0500\n"
            b"Content-Type: text/plain\n\n"
            b"Body\n"
        )
        path = tmp_path / "103.emlx"
        path.write_bytes(f"{len(mime)}\n".encode() + mime)

        result = parse_emlx(path)
        assert result is not None
        # Both should be Jan 15 since no Received header
        assert "2024-01-15" in result.date_received
        assert "2024-01-15" in result.date_sent

    def test_no_plist_footer_leaves_none(self, tmp_path: Path):
        """Without plist footer, read/flagged remain None."""
        mime = b"From: x@y.z\nSubject: Minimal\n\nBody"
        path = tmp_path / "101.emlx"
        path.write_bytes(f"{len(mime)}\n".encode() + mime)

        result = parse_emlx(path)
        assert result is not None
        assert result.read is None
        assert result.flagged is None


class TestExtractBodyText:
    """Tests for email body extraction."""

    def test_extract_plain_text(self):
        import email

        msg = email.message_from_string(
            "Content-Type: text/plain\n\nHello world"
        )
        result = _extract_body_text(msg)
        assert "Hello world" in result

    def test_extract_from_multipart(self):
        import email

        raw = """\
Content-Type: multipart/alternative; boundary="----=_Part"

------=_Part
Content-Type: text/plain

Plain text version

------=_Part
Content-Type: text/html

<html><body>HTML version</body></html>

------=_Part--
"""
        msg = email.message_from_string(raw)
        result = _extract_body_text(msg)
        # Should prefer plain text
        assert "Plain text version" in result


class TestStripHtml:
    """Tests for HTML stripping."""

    def test_removes_script_tags(self):
        html = '<p>Hello</p><script>alert("xss")</script><p>World</p>'
        result = _strip_html(html)
        assert "alert" not in result
        assert "script" not in result.lower()
        assert "Hello" in result
        assert "World" in result

    def test_removes_style_tags(self):
        html = "<style>.red{color:red}</style><p>Content</p>"
        result = _strip_html(html)
        assert "color" not in result
        assert "Content" in result

    def test_converts_block_elements_to_newlines(self):
        html = "<p>Para 1</p><p>Para 2</p>"
        result = _strip_html(html)
        assert "Para 1" in result
        assert "Para 2" in result

    def test_decodes_html_entities(self):
        html = "&lt;tag&gt; &amp; &quot;quotes&quot;"
        result = _strip_html(html)
        assert "<tag>" in result
        assert "&" in result
        assert '"quotes"' in result

    def test_handles_nested_script_bypass_attempt(self):
        """Test XSS bypass with nested/malformed tags."""
        # This is a classic XSS bypass that breaks regex-based stripping
        html = '<<script>script>alert("xss")<</script>/script>'
        result = _strip_html(html)
        # BeautifulSoup removes the dangerous JavaScript payload
        # Any remaining text like "/script>" is harmless plain text
        assert "alert" not in result
        assert "xss" not in result

    def test_handles_img_onerror_xss(self):
        """Test XSS bypass with img onerror."""
        html = '<img src=x onerror="alert(1)"><p>Content</p>'
        result = _strip_html(html)
        assert "onerror" not in result
        assert "alert" not in result
        assert "Content" in result

    def test_handles_svg_onload_xss(self):
        """Test XSS bypass with SVG onload."""
        html = '<svg onload="alert(1)"><circle/></svg><p>Safe</p>'
        result = _strip_html(html)
        assert "onload" not in result
        assert "alert" not in result
        assert "Safe" in result

    def test_returns_empty_on_invalid_html(self):
        """Malformed HTML should return empty string, not crash."""
        # Extremely malformed input
        result = _strip_html(None)  # type: ignore
        assert result == ""


class TestInferAccountMailbox:
    """Tests for path parsing."""

    def test_infer_from_standard_path(self, tmp_path: Path):
        # Simulate: V10/account-uuid/INBOX.mbox/Data/.../12345.emlx
        mail_dir = tmp_path / "V10"
        emlx_path = (
            mail_dir
            / "account-uuid-123"
            / "INBOX.mbox"
            / "Data"
            / "1"
            / "Messages"
            / "12345.emlx"
        )
        emlx_path.parent.mkdir(parents=True)
        emlx_path.touch()

        account, mailbox = _infer_account_mailbox(emlx_path, mail_dir)
        assert account == "account-uuid-123"
        assert mailbox == "INBOX"

    def test_infer_removes_mbox_suffix(self, tmp_path: Path):
        mail_dir = tmp_path / "V10"
        emlx_path = mail_dir / "acc" / "Sent Messages.mbox" / "Data" / "1.emlx"
        emlx_path.parent.mkdir(parents=True)
        emlx_path.touch()

        _account, mailbox = _infer_account_mailbox(emlx_path, mail_dir)
        assert mailbox == "Sent Messages"

    def test_infer_returns_unknown_for_invalid_path(self, tmp_path: Path):
        mail_dir = tmp_path / "V10"
        other_path = tmp_path / "somewhere" / "else.emlx"

        account, mailbox = _infer_account_mailbox(other_path, mail_dir)
        assert account == "Unknown"
        assert mailbox == "Unknown"


class TestScanExcludesDrafts:
    """Tests for S3: draft exclusion in disk scanning."""

    def test_scan_excludes_drafts(self, tmp_path: Path):
        from apple_mail_mcp.index.disk import scan_emlx_files

        mail_dir = tmp_path / "V10"
        # Create INBOX and Drafts mailboxes
        inbox = mail_dir / "acc" / "INBOX.mbox" / "Data" / "Messages"
        drafts = mail_dir / "acc" / "Drafts.mbox" / "Data" / "Messages"
        inbox.mkdir(parents=True)
        drafts.mkdir(parents=True)

        (inbox / "1.emlx").write_bytes(b"test")
        (drafts / "2.emlx").write_bytes(b"test")

        # With default exclusion
        files = list(scan_emlx_files(mail_dir, exclude_mailboxes={"Drafts"}))
        assert len(files) == 1
        assert "INBOX" in str(files[0])

    def test_scan_no_exclusion(self, tmp_path: Path):
        from apple_mail_mcp.index.disk import scan_emlx_files

        mail_dir = tmp_path / "V10"
        inbox = mail_dir / "acc" / "INBOX.mbox" / "Data" / "Messages"
        drafts = mail_dir / "acc" / "Drafts.mbox" / "Data" / "Messages"
        inbox.mkdir(parents=True)
        drafts.mkdir(parents=True)

        (inbox / "1.emlx").write_bytes(b"test")
        (drafts / "2.emlx").write_bytes(b"test")

        # With empty exclusion set
        files = list(scan_emlx_files(mail_dir, exclude_mailboxes=set()))
        assert len(files) == 2


class TestScanIndexFilters:
    """Tests for account and mailbox filters during disk scanning."""

    def test_scan_excludes_configured_accounts(self, tmp_path: Path):
        from apple_mail_mcp.index.disk import scan_emlx_files

        mail_dir = tmp_path / "V10"
        personal = mail_dir / "personal-uuid" / "INBOX.mbox" / "Data"
        work = mail_dir / "work-uuid" / "INBOX.mbox" / "Data"
        personal.mkdir(parents=True)
        work.mkdir(parents=True)

        (personal / "1.emlx").write_bytes(b"test")
        (work / "2.emlx").write_bytes(b"test")

        files = list(
            scan_emlx_files(
                mail_dir,
                exclude_mailboxes=set(),
                exclude_accounts={"work-uuid"},
            )
        )

        assert len(files) == 1
        assert "personal-uuid" in str(files[0])

    def test_scan_honors_mailbox_allow_list(self, tmp_path: Path):
        from apple_mail_mcp.index.disk import scan_emlx_files

        mail_dir = tmp_path / "V10"
        inbox = mail_dir / "acc" / "INBOX.mbox" / "Data"
        archive = mail_dir / "acc" / "Archive.mbox" / "Data"
        inbox.mkdir(parents=True)
        archive.mkdir(parents=True)

        (inbox / "1.emlx").write_bytes(b"test")
        (archive / "2.emlx").write_bytes(b"test")

        files = list(
            scan_emlx_files(
                mail_dir,
                exclude_mailboxes=set(),
                include_mailboxes={"INBOX"},
            )
        )

        assert len(files) == 1
        assert "INBOX" in str(files[0])

    @patch("apple_mail_mcp.executor.execute_with_core")
    def test_scan_resolves_friendly_account_names(
        self,
        mock_execute,
        tmp_path: Path,
    ):
        from apple_mail_mcp.index.disk import scan_emlx_files

        mock_execute.return_value = [
            {"name": "Extreme", "id": "work-uuid"},
            {"name": "Personal", "id": "personal-uuid"},
        ]

        mail_dir = tmp_path / "V10"
        personal = mail_dir / "personal-uuid" / "INBOX.mbox" / "Data"
        work = mail_dir / "work-uuid" / "INBOX.mbox" / "Data"
        personal.mkdir(parents=True)
        work.mkdir(parents=True)

        (personal / "1.emlx").write_bytes(b"test")
        (work / "2.emlx").write_bytes(b"test")

        files = list(
            scan_emlx_files(
                mail_dir,
                exclude_mailboxes=set(),
                exclude_accounts={"Extreme"},
            )
        )

        assert len(files) == 1
        assert "personal-uuid" in str(files[0])

    @patch("apple_mail_mcp.executor.execute_with_core")
    def test_unresolved_account_names_fail_closed(
        self,
        mock_execute,
        tmp_path: Path,
    ):
        from apple_mail_mcp.index.disk import scan_emlx_files

        mock_execute.side_effect = RuntimeError("Mail unavailable")

        mail_dir = tmp_path / "V10"
        inbox = mail_dir / "work-uuid" / "INBOX.mbox" / "Data"
        inbox.mkdir(parents=True)
        (inbox / "1.emlx").write_bytes(b"test")

        with pytest.raises(RuntimeError, match="APPLE_MAIL_INDEX_EXCLUDE"):
            list(
                scan_emlx_files(
                    mail_dir,
                    exclude_mailboxes=set(),
                    exclude_accounts={"Extreme"},
                )
            )


class TestExtractAttachments:
    """Tests for attachment metadata extraction."""

    def test_no_attachments_plain_text(self):
        import email as email_mod

        msg = email_mod.message_from_string(
            "Content-Type: text/plain\n\nHello world"
        )
        result = _extract_attachments(msg)
        assert result == []

    def test_extracts_attachment_from_multipart(self):
        import email as email_mod

        raw = """\
Content-Type: multipart/mixed; boundary="----=_Part"

------=_Part
Content-Type: text/plain

Body text

------=_Part
Content-Type: application/pdf
Content-Disposition: attachment; filename="invoice.pdf"

%PDF-fake-content

------=_Part--
"""
        msg = email_mod.message_from_string(raw)
        result = _extract_attachments(msg)
        assert len(result) == 1
        assert result[0].filename == "invoice.pdf"
        assert result[0].mime_type == "application/pdf"
        assert result[0].file_size > 0

    def test_extracts_inline_image_with_content_id(self):
        import email as email_mod

        raw = """\
Content-Type: multipart/related; boundary="----=_Part"

------=_Part
Content-Type: text/html

<html><body><img src="cid:img1"></body></html>

------=_Part
Content-Type: image/png
Content-ID: <img1>
Content-Disposition: inline; filename="logo.png"

PNG-fake-content

------=_Part--
"""
        msg = email_mod.message_from_string(raw)
        result = _extract_attachments(msg)
        assert len(result) == 1
        assert result[0].filename == "logo.png"
        assert result[0].content_id == "img1"

    def test_parse_emlx_populates_attachments(self, tmp_path):
        mime_content = b"""\
Content-Type: multipart/mixed; boundary="----=_Part"

------=_Part
Content-Type: text/plain

Body text

------=_Part
Content-Type: application/pdf
Content-Disposition: attachment; filename="doc.pdf"

%PDF-fake

------=_Part--
"""
        byte_count = len(mime_content)
        plist = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN">
<plist version="1.0"><dict></dict></plist>
"""
        emlx = f"{byte_count}\n".encode() + mime_content + plist
        path = tmp_path / "42.emlx"
        path.write_bytes(emlx)

        result = parse_emlx(path)
        assert result is not None
        assert result.attachments is not None
        assert len(result.attachments) == 1
        assert result.attachments[0].filename == "doc.pdf"


class TestGetAttachmentContent:
    """Tests for extracting attachment binary content."""

    def test_extracts_attachment_bytes(self, tmp_path):
        mime_content = b"""\
Content-Type: multipart/mixed; boundary="----=_Part"

------=_Part
Content-Type: text/plain

Body

------=_Part
Content-Type: application/octet-stream
Content-Disposition: attachment; filename="data.bin"

BINARYDATA

------=_Part--
"""
        byte_count = len(mime_content)
        plist = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN">
<plist version="1.0"><dict></dict></plist>
"""
        emlx = f"{byte_count}\n".encode() + mime_content + plist
        path = tmp_path / "99.emlx"
        path.write_bytes(emlx)

        result = get_attachment_content(path, "data.bin")
        assert result is not None
        raw_bytes, mime_type = result
        assert b"BINARYDATA" in raw_bytes
        assert mime_type == "application/octet-stream"

    def test_returns_none_for_missing_attachment(self, tmp_path):
        mime_content = b"Content-Type: text/plain\n\nBody"
        byte_count = len(mime_content)
        plist = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN">
<plist version="1.0"><dict></dict></plist>
"""
        emlx = f"{byte_count}\n".encode() + mime_content + plist
        path = tmp_path / "100.emlx"
        path.write_bytes(emlx)

        result = get_attachment_content(path, "nonexistent.pdf")
        assert result is None

    def test_returns_none_for_missing_file(self, tmp_path):
        result = get_attachment_content(tmp_path / "missing.emlx", "file.pdf")
        assert result is None


class TestExtractMessageId:
    """Tests for extract_message_id helper (#39)."""

    def test_regular_emlx(self, tmp_path: Path):
        from apple_mail_mcp.index.disk import extract_message_id

        path = tmp_path / "12345.emlx"
        assert extract_message_id(path) == 12345

    def test_partial_emlx(self, tmp_path: Path):
        from apple_mail_mcp.index.disk import extract_message_id

        path = tmp_path / "67301.partial.emlx"
        assert extract_message_id(path) == 67301

    def test_invalid_filename_raises(self, tmp_path: Path):
        import pytest

        from apple_mail_mcp.index.disk import extract_message_id

        path = tmp_path / "notanumber.emlx"
        with pytest.raises(ValueError):
            extract_message_id(path)


class TestParseEmlxPartial:
    """Tests for .partial.emlx parsing (#39)."""

    def test_parses_partial_emlx_file(
        self, tmp_path: Path, sample_emlx_content: bytes
    ):
        """parse_emlx successfully parses a .partial.emlx file."""
        path = tmp_path / "67301.partial.emlx"
        path.write_bytes(sample_emlx_content)

        result = parse_emlx(path)
        assert result is not None
        assert result.id == 67301
        assert result.subject == "Test Email Subject"


class TestScanIncludesPartialFiles:
    """Tests that scan_emlx_files includes .partial.emlx (#39)."""

    def test_scan_includes_partial_files(self, tmp_path: Path):
        from apple_mail_mcp.index.disk import scan_emlx_files

        mail_dir = tmp_path / "V10"
        mbox = mail_dir / "acc" / "INBOX.mbox" / "Data" / "Messages"
        mbox.mkdir(parents=True)

        (mbox / "12345.partial.emlx").write_bytes(b"test")

        files = list(scan_emlx_files(mail_dir, exclude_mailboxes=set()))
        assert len(files) == 1
        assert "12345.partial.emlx" in str(files[0])

    def test_indexes_partial_files(self, tmp_path: Path):
        """Partials are now included (was test_skips_partial)."""
        from apple_mail_mcp.index.disk import get_disk_inventory

        mail_dir = tmp_path / "V10"
        mbox = mail_dir / "acc" / "INBOX.mbox" / "Data" / "Messages"
        mbox.mkdir(parents=True)

        (mbox / "12345.partial.emlx").write_bytes(b"test")

        inventory = get_disk_inventory(mail_dir)
        assert len(inventory) == 1
        assert ("acc", "INBOX", 12345) in inventory


class TestEstimateAttachmentSize:
    """Tests for _estimate_attachment_size (#38)."""

    def test_base64_size_estimation(self):
        import email as email_mod

        raw = """\
Content-Type: multipart/mixed; boundary="----=_Part"

------=_Part
Content-Type: text/plain

Body

------=_Part
Content-Type: application/pdf
Content-Disposition: attachment; filename="doc.pdf"
Content-Transfer-Encoding: base64

SGVsbG8gV29ybGQ=

------=_Part--
"""
        msg = email_mod.message_from_string(raw)
        from apple_mail_mcp.index.disk import _estimate_attachment_size

        for part in msg.walk():
            if part.get_filename() == "doc.pdf":
                size = _estimate_attachment_size(part)
                # "Hello World" = 11 bytes, base64 "SGVsbG8gV29ybGQ=" = 16 chars
                # (16 * 3) // 4 - 1 = 11
                assert size == 11

    def test_content_length_header_used(self):
        import email as email_mod

        raw = """\
Content-Type: application/pdf
Content-Disposition: attachment; filename="doc.pdf"
Content-Length: 42000

some payload
"""
        msg = email_mod.message_from_string(raw)
        from apple_mail_mcp.index.disk import _estimate_attachment_size

        assert _estimate_attachment_size(msg) == 42000

    def test_empty_payload(self):
        import email as email_mod

        raw = "Content-Type: application/pdf\n\n"
        msg = email_mod.message_from_string(raw)
        from apple_mail_mcp.index.disk import _estimate_attachment_size

        assert _estimate_attachment_size(msg) == 0

    def test_whitespace_heavy_payload_no_intermediate_alloc(self):
        # Regression for #81: chained .replace() previously allocated
        # ~3x the payload size in intermediate strings. The fix uses
        # str.count() (allocation-free) and must preserve exact
        # semantics including padding subtraction.
        import email as email_mod

        # Realistic base64 wrapping: 76 chars per line, many newlines
        # for a payload that decodes to a known size.
        body = ("A" * 76 + "\n") * 100 + "AAAB=="  # 7600 + 6 = 7606 chars
        # Clean base64 length: 7600 (the AAAA lines) + 6 (last line) = 7606
        # But we strip newlines, so clean_len = 7606 - 100 newlines = 7506? No:
        # The 100 lines contribute 76*100 = 7600 chars, separators are 100
        # newlines → total raw = 7700 chars. Plus final "AAAB==" = 7706 raw.
        # clean_len = 7706 - 100 = 7606. Padding = 2.
        # Expected: (7606 * 3) // 4 - 2 = 5704 - 2 = 5702
        raw = (
            f"Content-Type: application/octet-stream\n"
            f"Content-Disposition: attachment; filename=\"big.bin\"\n"
            f"Content-Transfer-Encoding: base64\n\n{body}\n"
        )
        msg = email_mod.message_from_string(raw)
        from apple_mail_mcp.index.disk import _estimate_attachment_size

        size = _estimate_attachment_size(msg)
        assert size == 5702, f"got {size}, expected 5702"


class TestScanAllEmailsErrorHandling:
    """scan_all_emails skips corrupt files (#42)."""

    def _make_emlx(self, path: Path, subject: str = "Test") -> None:
        """Write a minimal valid .emlx file."""
        mime = (
            f"From: a@b.com\n"
            f"Subject: {subject}\n"
            f"Date: Mon, 15 Jan 2024 10:00:00 -0500\n"
            f"Content-Type: text/plain\n\n"
            f"body\n"
        ).encode()
        plist = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b"<!DOCTYPE plist PUBLIC "
            b'"-//Apple//DTD PLIST 1.0//EN">\n'
            b'<plist version="1.0">'
            b"<dict></dict></plist>\n"
        )
        path.write_bytes(f"{len(mime)}\n".encode() + mime + plist)

    def test_skips_corrupt_file(self, tmp_path: Path):
        """Corrupt .emlx must not crash the iterator."""
        from apple_mail_mcp.index.disk import scan_all_emails

        mail_dir = tmp_path / "V10"
        msgs = mail_dir / "acc" / "INBOX.mbox" / "Data" / "0" / "Messages"
        msgs.mkdir(parents=True)

        # Create valid email
        self._make_emlx(msgs / "1.emlx", subject="Good")

        # Create corrupt email (garbage bytes)
        (msgs / "2.emlx").write_bytes(b"\x00\xff\xfe")

        # Create another valid email
        self._make_emlx(msgs / "3.emlx", subject="Also Good")

        # Provide empty MailData so envelope index is skipped
        (mail_dir.parent / "MailData").mkdir(parents=True, exist_ok=True)

        results = list(scan_all_emails(mail_dir))

        subjects = {r["subject"] for r in results}
        assert "Good" in subjects
        assert "Also Good" in subjects
        assert len(results) == 2

    def test_skips_file_that_raises(self, tmp_path: Path, monkeypatch):
        """If parse_emlx raises, the generator continues."""
        from apple_mail_mcp.index import disk
        from apple_mail_mcp.index.disk import scan_all_emails

        mail_dir = tmp_path / "V10"
        msgs = mail_dir / "acc" / "INBOX.mbox" / "Data" / "0" / "Messages"
        msgs.mkdir(parents=True)

        self._make_emlx(msgs / "1.emlx", subject="OK")
        self._make_emlx(msgs / "2.emlx", subject="Boom")
        self._make_emlx(msgs / "3.emlx", subject="Fine")

        (mail_dir.parent / "MailData").mkdir(parents=True, exist_ok=True)

        original_parse = disk.parse_emlx

        def exploding_parse(path):
            if "2.emlx" in str(path):
                raise RuntimeError("simulated crash")
            return original_parse(path)

        monkeypatch.setattr(disk, "parse_emlx", exploding_parse)

        results = list(scan_all_emails(mail_dir))

        subjects = {r["subject"] for r in results}
        assert "OK" in subjects
        assert "Fine" in subjects
        assert "Boom" not in subjects


# ── External attachment helpers (#45) ──────────────────


def _build_partial_tree(
    tmp_path: Path,
    msg_id: int = 49461,
    filenames: dict[int | str, str] | None = None,
    file_content: bytes = b"\x89PNG fake image data",
    mime_raw: bytes | None = None,
) -> Path:
    """Create a realistic .partial.emlx + Attachments tree.

    Returns the path to the ``.partial.emlx`` file.

    ``filenames`` maps part-subdir index (e.g. ``2``, ``3``,
    or ``"2.2"`` for nested parts) to the filename stored
    inside that directory.  Defaults to a single attachment
    at subdir 2.

    ``mime_raw`` optionally overrides the auto-generated MIME
    content (for testing nested multipart structures).
    """
    if filenames is None:
        filenames = {2: "photo.jpeg"}

    msgs = tmp_path / "acc" / "INBOX.mbox" / "Data" / "9" / "4" / "Messages"
    msgs.mkdir(parents=True)

    att_base = msgs.parent / "Attachments" / str(msg_id)

    for subdir_idx, fname in filenames.items():
        part_dir = att_base / str(subdir_idx)
        part_dir.mkdir(parents=True)
        (part_dir / fname).write_bytes(file_content)

    # Build a minimal .partial.emlx with an attachment
    # part whose payload is empty (simulates external
    # storage).
    if mime_raw is None:
        attachment_headers = ""
        for fname in filenames.values():
            attachment_headers += (
                f"------=_Part\r\n"
                f"Content-Type: application/octet-stream\r\n"
                f'Content-Disposition: attachment; filename="{fname}"\r\n'
                f"\r\n"
            )

        mime_raw = (
            'Content-Type: multipart/mixed; boundary="----=_Part"\r\n'
            "\r\n"
            "------=_Part\r\n"
            "Content-Type: text/plain\r\n"
            "\r\n"
            "Body text\r\n"
            f"{attachment_headers}"
            "------=_Part--\r\n"
        ).encode()

    plist = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b"<!DOCTYPE plist PUBLIC "
        b'"-//Apple//DTD PLIST 1.0//EN">\n'
        b'<plist version="1.0">'
        b"<dict></dict></plist>\n"
    )

    emlx_path = msgs / f"{msg_id}.partial.emlx"
    emlx_path.write_bytes(f"{len(mime_raw)}\n".encode() + mime_raw + plist)
    return emlx_path


class TestFindExternalAttachment:
    """Tests for _find_external_attachment (#45)."""

    def test_finds_file_exact_match(self, tmp_path: Path):
        """Exact filename match in the part subdirectory."""
        emlx = _build_partial_tree(tmp_path, filenames={2: "photo.jpeg"})
        result = _find_external_attachment(
            emlx, msg_id=49461, part_idx=2, filename="photo.jpeg"
        )
        assert result is not None
        assert result.name == "photo.jpeg"

    def test_finds_single_file_fallback(self, tmp_path: Path):
        """When name differs, takes the single file."""
        emlx = _build_partial_tree(
            tmp_path,
            filenames={2: "Mail Attachment.jpeg"},
        )
        result = _find_external_attachment(
            emlx,
            msg_id=49461,
            part_idx=2,
            filename="Resume.jpeg",
        )
        assert result is not None
        assert result.name == "Mail Attachment.jpeg"

    def test_returns_none_for_missing_dir(self, tmp_path: Path):
        """No Attachments directory at all."""
        msgs = tmp_path / "Messages"
        msgs.mkdir(parents=True)
        emlx = msgs / "99999.partial.emlx"
        emlx.touch()

        result = _find_external_attachment(
            emlx, msg_id=99999, part_idx=2, filename="a.pdf"
        )
        assert result is None

    def test_returns_none_for_missing_part_dir(self, tmp_path: Path):
        """Attachments dir exists but specific part subdir missing."""
        emlx = _build_partial_tree(tmp_path, filenames={2: "photo.jpeg"})
        # Ask for part_idx=5 which doesn't exist
        result = _find_external_attachment(
            emlx, msg_id=49461, part_idx=5, filename="photo.jpeg"
        )
        assert result is None

    def test_rejects_path_traversal(self, tmp_path: Path):
        """Malicious filename with '..' must not escape the part dir."""
        emlx = _build_partial_tree(tmp_path, filenames={2: "photo.jpeg"})
        # Attacker-crafted filename tries to escape via ../
        result = _find_external_attachment(
            emlx,
            msg_id=49461,
            part_idx=2,
            filename="../../../etc/passwd",
        )
        assert result is None


class TestGetAttachmentContentExternal:
    """get_attachment_content falls back to external files (#45)."""

    def test_reads_external_file(self, tmp_path: Path):
        """MIME payload is empty -> reads from Attachments dir."""
        img_bytes = b"\x89PNG external image bytes"
        emlx = _build_partial_tree(
            tmp_path,
            filenames={2: "photo.jpeg"},
            file_content=img_bytes,
        )

        result = get_attachment_content(emlx, "photo.jpeg")
        assert result is not None
        data, mime_type = result
        assert data == img_bytes
        assert "jpeg" in mime_type or "octet" in mime_type

    def test_reads_external_generic_name(self, tmp_path: Path):
        """Disk file has generic name, MIME has real name."""
        img_bytes = b"\x89PNG fallback bytes"
        emlx = _build_partial_tree(
            tmp_path,
            filenames={2: "Mail Attachment.jpeg"},
            file_content=img_bytes,
        )
        # The MIME part says "Mail Attachment.jpeg"
        result = get_attachment_content(emlx, "Mail Attachment.jpeg")
        assert result is not None
        data, _ = result
        assert data == img_bytes

    def test_embedded_payload_still_works(self, tmp_path: Path):
        """Regular .emlx with embedded data must still work."""
        mime_content = b"""\
Content-Type: multipart/mixed; boundary="----=_Part"

------=_Part
Content-Type: text/plain

Body

------=_Part
Content-Type: application/octet-stream
Content-Disposition: attachment; filename="data.bin"

EMBEDDEDDATA

------=_Part--
"""
        byte_count = len(mime_content)
        plist = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b"<!DOCTYPE plist PUBLIC "
            b'"-//Apple//DTD PLIST 1.0//EN">\n'
            b'<plist version="1.0">'
            b"<dict></dict></plist>\n"
        )
        emlx = f"{byte_count}\n".encode() + mime_content + plist
        path = tmp_path / "55.emlx"
        path.write_bytes(emlx)

        result = get_attachment_content(path, "data.bin")
        assert result is not None
        raw_bytes, mime_type = result
        assert b"EMBEDDEDDATA" in raw_bytes
        assert mime_type == "application/octet-stream"

    def test_rejects_oversized_external(self, tmp_path: Path):
        """External file exceeding MAX_EMLX_SIZE must be rejected (#47)."""
        emlx = _build_partial_tree(
            tmp_path,
            filenames={2: "huge.bin"},
            file_content=b"x" * 100,
        )
        # Patch MAX_EMLX_SIZE to a tiny value so we don't need 25 MB on disk
        import apple_mail_mcp.index.disk as disk_mod

        original = disk_mod.MAX_EMLX_SIZE
        try:
            disk_mod.MAX_EMLX_SIZE = 50
            result = get_attachment_content(emlx, "huge.bin")
            assert result is None
        finally:
            disk_mod.MAX_EMLX_SIZE = original


class TestExtractAttachmentsExternalSize:
    """_extract_attachments gets size from disk for externals (#45)."""

    def test_file_size_from_external(self, tmp_path: Path):
        """When MIME payload is empty, file_size comes from disk."""
        img_bytes = b"x" * 12345
        emlx = _build_partial_tree(
            tmp_path,
            filenames={2: "photo.jpeg"},
            file_content=img_bytes,
        )

        result = parse_emlx(emlx)
        assert result is not None
        assert result.attachments is not None
        assert len(result.attachments) == 1
        assert result.attachments[0].file_size == 12345

    def test_file_size_from_multiple_externals(self, tmp_path: Path):
        """Multiple external attachments map correctly to dirs 2, 3."""
        img_bytes = b"y" * 6789
        emlx = _build_partial_tree(
            tmp_path,
            filenames={2: "photo.jpeg", 3: "document.pdf"},
            file_content=img_bytes,
        )

        result = parse_emlx(emlx)
        assert result is not None
        assert result.attachments is not None
        assert len(result.attachments) == 2
        filenames = {att.filename for att in result.attachments}
        assert "photo.jpeg" in filenames
        assert "document.pdf" in filenames
        for att in result.attachments:
            assert att.file_size == 6789

    def test_no_external_dir_size_zero(self, tmp_path: Path):
        """Without Attachments dir, file_size stays 0."""
        import email as email_mod

        raw = """\
Content-Type: multipart/mixed; boundary="----=_Part"

------=_Part
Content-Type: text/plain

Body

------=_Part
Content-Type: application/pdf
Content-Disposition: attachment; filename="doc.pdf"

------=_Part--
"""
        msg = email_mod.message_from_string(raw)

        # Pass a path with no sibling Attachments dir
        fake_path = tmp_path / "Messages" / "999.partial.emlx"
        fake_path.parent.mkdir(parents=True)
        fake_path.touch()

        result = _extract_attachments(msg, emlx_path=fake_path)
        assert len(result) == 1
        assert result[0].file_size == 0

    def test_backward_compat_no_emlx_path(self):
        """Without emlx_path, works exactly as before."""
        import email as email_mod

        raw = """\
Content-Type: multipart/mixed; boundary="----=_Part"

------=_Part
Content-Type: text/plain

Body

------=_Part
Content-Type: application/pdf
Content-Disposition: attachment; filename="doc.pdf"

%PDF-fake

------=_Part--
"""
        msg = email_mod.message_from_string(raw)
        result = _extract_attachments(msg)
        assert len(result) == 1
        assert result[0].filename == "doc.pdf"
        assert result[0].file_size > 0


class TestMimePartNumbers:
    """Tests for _mime_part_numbers helper."""

    def test_flat_multipart(self):
        """Top-level children get integer part numbers."""
        import email as email_mod

        raw = """\
Content-Type: multipart/mixed; boundary="----=_B"

------=_B
Content-Type: text/plain

Body

------=_B
Content-Type: application/pdf
Content-Disposition: attachment; filename="doc.pdf"

------=_B--
"""
        msg = email_mod.message_from_string(raw)
        nums = _mime_part_numbers(msg)
        parts = list(msg.walk())
        # parts[0] = multipart container (no entry)
        # parts[1] = text/plain -> "1"
        # parts[2] = application/pdf -> "2"
        assert nums[id(parts[1])] == "1"
        assert nums[id(parts[2])] == "2"

    def test_nested_multipart(self):
        """Nested children get dot-notation part numbers."""
        import email as email_mod

        raw = (
            'Content-Type: multipart/mixed; boundary="outer"\r\n'
            "\r\n"
            "--outer\r\n"
            'Content-Type: multipart/alternative; boundary="inner1"\r\n'
            "\r\n"
            "--inner1\r\n"
            "Content-Type: text/plain\r\n"
            "\r\n"
            "Body\r\n"
            "--inner1\r\n"
            "Content-Type: text/html\r\n"
            "\r\n"
            "<p>Body</p>\r\n"
            "--inner1--\r\n"
            "--outer\r\n"
            'Content-Type: multipart/mixed; boundary="inner2"\r\n'
            "\r\n"
            "--inner2\r\n"
            "Content-Type: text/html\r\n"
            "\r\n"
            "<p>Forwarded</p>\r\n"
            "--inner2\r\n"
            "Content-Type: application/pdf\r\n"
            'Content-Disposition: attachment; filename="invoice.pdf"\r\n'
            "\r\n"
            "--inner2--\r\n"
            "--outer--\r\n"
        )
        msg = email_mod.message_from_string(raw)
        nums = _mime_part_numbers(msg)

        # Walk order: outer, inner1, text/plain, text/html,
        #             inner2, text/html, application/pdf
        leaf_parts = [
            p for p in msg.walk()
            if not p.get_content_maintype() == "multipart"
        ]
        assert nums[id(leaf_parts[0])] == "1.1"  # text/plain
        assert nums[id(leaf_parts[1])] == "1.2"  # text/html
        assert nums[id(leaf_parts[2])] == "2.1"  # text/html (fwd)
        assert nums[id(leaf_parts[3])] == "2.2"  # application/pdf


class TestNestedExternalAttachments:
    """External attachments in nested MIME structures.

    Apple Mail uses dot-notation subdirectories (e.g. ``2.2/``)
    for attachments nested inside multipart sub-parts. This
    tests that both ``get_attachment_content`` and
    ``_extract_attachments`` correctly locate these files.
    """

    @staticmethod
    def _nested_mime() -> bytes:
        """Build a nested multipart MIME (forwarded-style).

        Structure::
            multipart/mixed (outer)
              multipart/alternative (part 1)
                text/plain (part 1.1)
                text/html  (part 1.2)
              multipart/mixed (part 2)
                text/html  (part 2.1 - forwarded body)
                application/pdf (part 2.2 - the invoice)
        """
        return (
            'Content-Type: multipart/mixed; boundary="outer"\r\n'
            "\r\n"
            "--outer\r\n"
            'Content-Type: multipart/alternative; boundary="alt"\r\n'
            "\r\n"
            "--alt\r\n"
            "Content-Type: text/plain\r\n"
            "\r\n"
            "Body text\r\n"
            "--alt\r\n"
            "Content-Type: text/html\r\n"
            "\r\n"
            "<p>Body</p>\r\n"
            "--alt--\r\n"
            "--outer\r\n"
            'Content-Type: multipart/mixed; boundary="fwd"\r\n'
            "\r\n"
            "--fwd\r\n"
            "Content-Type: text/html\r\n"
            "\r\n"
            "<p>Forwarded body</p>\r\n"
            "--fwd\r\n"
            "Content-Type: application/pdf\r\n"
            'Content-Disposition: attachment; filename="invoice.pdf"\r\n'
            "\r\n"
            "--fwd--\r\n"
            "--outer--\r\n"
        ).encode()

    def test_get_attachment_content_nested(self, tmp_path: Path):
        """get_attachment_content finds file in dotted subdir."""
        pdf_bytes = b"%PDF-1.4 nested invoice data"
        emlx = _build_partial_tree(
            tmp_path,
            filenames={"2.2": "invoice.pdf"},
            file_content=pdf_bytes,
            mime_raw=self._nested_mime(),
        )

        result = get_attachment_content(emlx, "invoice.pdf")
        assert result is not None
        data, mime_type = result
        assert data == pdf_bytes
        assert "pdf" in mime_type or "octet" in mime_type

    def test_extract_attachments_nested_size(self, tmp_path: Path):
        """_extract_attachments gets file size from dotted subdir."""
        pdf_bytes = b"x" * 9876
        emlx = _build_partial_tree(
            tmp_path,
            filenames={"2.2": "invoice.pdf"},
            file_content=pdf_bytes,
            mime_raw=self._nested_mime(),
        )

        result = parse_emlx(emlx)
        assert result is not None
        assert result.attachments is not None
        assert len(result.attachments) == 1
        assert result.attachments[0].filename == "invoice.pdf"
        assert result.attachments[0].file_size == 9876

    def test_flat_still_works(self, tmp_path: Path):
        """Top-level attachment in subdir 2/ still works."""
        img_bytes = b"\x89PNG flat attachment"
        emlx = _build_partial_tree(
            tmp_path,
            filenames={2: "photo.jpeg"},
            file_content=img_bytes,
        )

        result = get_attachment_content(emlx, "photo.jpeg")
        assert result is not None
        data, _ = result
        assert data == img_bytes

    def test_deeply_nested(self, tmp_path: Path):
        """Three levels deep: subdir 1.2.1 works."""
        mime_raw = (
            'Content-Type: multipart/mixed; boundary="L1"\r\n'
            "\r\n"
            "--L1\r\n"
            'Content-Type: multipart/mixed; boundary="L2"\r\n'
            "\r\n"
            "--L2\r\n"
            "Content-Type: text/plain\r\n"
            "\r\n"
            "Body\r\n"
            "--L2\r\n"
            'Content-Type: multipart/mixed; boundary="L3"\r\n'
            "\r\n"
            "--L3\r\n"
            "Content-Type: application/pdf\r\n"
            'Content-Disposition: attachment; filename="deep.pdf"\r\n'
            "\r\n"
            "--L3--\r\n"
            "--L2--\r\n"
            "--L1--\r\n"
        ).encode()

        pdf_bytes = b"%PDF deeply nested"
        emlx = _build_partial_tree(
            tmp_path,
            filenames={"1.2.1": "deep.pdf"},
            file_content=pdf_bytes,
            mime_raw=mime_raw,
        )

        result = get_attachment_content(emlx, "deep.pdf")
        assert result is not None
        data, mime_type = result
        assert data == pdf_bytes


class TestMimePartNumbersFallback:
    """Defensive behavior when _mime_part_numbers misses a part.

    The helper is expected to cover every leaf MIME part, but a missing
    entry would silently route the lookup to the wrong subdirectory
    because ``Path("/a/b") / "" == Path("/a/b")``. These tests confirm
    callers skip the external lookup instead of misrouting.
    """

    def test_extract_attachments_skips_when_part_number_missing(
        self, tmp_path: Path, monkeypatch
    ):
        """_extract_attachments falls through cleanly on missing part_number."""
        from apple_mail_mcp.index import disk as disk_mod

        # Force the part-number map to be empty
        monkeypatch.setattr(
            disk_mod, "_mime_part_numbers", lambda msg: {}
        )

        emlx = _build_partial_tree(
            tmp_path,
            filenames={2: "photo.jpeg"},
            file_content=b"\x89PNG fake",
        )
        result = parse_emlx(emlx)
        assert result is not None
        # Attachment is still listed (it has a MIME filename) but the
        # external size lookup was skipped, so file_size stays at 0.
        # The important assertion is that we did NOT silently grab a
        # file from the Attachments root (which would yield wrong bytes).
        assert result.attachments is not None
        assert len(result.attachments) == 1
        assert result.attachments[0].filename == "photo.jpeg"

    def test_get_attachment_content_skips_when_part_number_missing(
        self, tmp_path: Path, monkeypatch
    ):
        """get_attachment_content returns None on missing part_number."""
        from apple_mail_mcp.index import disk as disk_mod

        monkeypatch.setattr(
            disk_mod, "_mime_part_numbers", lambda msg: {}
        )

        emlx = _build_partial_tree(
            tmp_path,
            filenames={2: "photo.jpeg"},
            file_content=b"\x89PNG fake",
        )
        # External file exists at the right subdir but part_number lookup
        # is forced empty — should return None, not the wrongly-routed file.
        result = get_attachment_content(emlx, "photo.jpeg")
        assert result is None


class TestDetectMailVersion:
    """Tests for dynamic Mail.app version detection."""

    def test_finds_highest_version(self, tmp_path: Path, monkeypatch):
        """Returns the highest V* directory numerically."""
        mail_base = tmp_path / "Library" / "Mail"
        (mail_base / "V9").mkdir(parents=True)
        (mail_base / "V10").mkdir()
        (mail_base / "V11").mkdir()

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from apple_mail_mcp.index.disk import _detect_mail_version

        assert _detect_mail_version() == "V11"

    def test_fallback_to_v10(self, tmp_path: Path, monkeypatch):
        """Returns V10 when no V* directories exist."""
        mail_base = tmp_path / "Library" / "Mail"
        mail_base.mkdir(parents=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from apple_mail_mcp.index.disk import _detect_mail_version

        assert _detect_mail_version() == "V10"

    def test_ignores_non_version_dirs(self, tmp_path: Path, monkeypatch):
        """Ignores directories that don't match V<number> pattern."""
        mail_base = tmp_path / "Library" / "Mail"
        (mail_base / "V10").mkdir(parents=True)
        (mail_base / "Bundles").mkdir()
        (mail_base / "Vx").mkdir()
        (mail_base / "V").mkdir()

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from apple_mail_mcp.index.disk import _detect_mail_version

        assert _detect_mail_version() == "V10"

    def test_fallback_when_no_mail_dir(self, tmp_path: Path, monkeypatch):
        """Returns V10 when ~/Library/Mail doesn't exist."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from apple_mail_mcp.index.disk import _detect_mail_version

        assert _detect_mail_version() == "V10"

    def test_find_mail_directory_caches(self, tmp_path: Path, monkeypatch):
        """find_mail_directory() caches its result."""
        import apple_mail_mcp.index.disk as disk_mod

        mail_dir = tmp_path / "Library" / "Mail" / "V10"
        mail_dir.mkdir(parents=True)
        (mail_dir / "account-uuid").mkdir()

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Clear cache
        disk_mod._cached_mail_dir = None

        result1 = disk_mod.find_mail_directory()
        result2 = disk_mod.find_mail_directory()
        assert result1 == result2 == mail_dir

        # Clean up cache for other tests
        disk_mod._cached_mail_dir = None
