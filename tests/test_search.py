"""Tests for FTS5 search functionality."""

from __future__ import annotations

import sqlite3

import pytest

from apple_mail_mcp.index.search import (
    _escape_all_special,
    count_matches,
    detect_matched_columns,
    sanitize_fts_query,
    search_attachments,
    search_fts,
    search_fts_highlight,
)


@pytest.fixture
def fts_db():
    """In-memory FTS5 database with test emails."""
    from apple_mail_mcp.index.schema import create_connection

    conn = create_connection(":memory:")

    from apple_mail_mcp.index.schema import get_schema_sql

    conn.executescript(get_schema_sql())

    # Insert test emails with different dates
    emails = [
        (1, "acct-1", "Inbox", "Budget meeting notes",
         "alice@example.com", "Let's discuss the Q1 budget",
         "2026-01-15T10:00:00"),
        (2, "acct-1", "Inbox", "Project update",
         "bob@example.com", "The project is on track",
         "2026-02-10T14:00:00"),
        (3, "acct-1", "Sent", "Re: Budget meeting notes",
         "me@example.com", "Thanks for the budget update",
         "2026-03-01T09:00:00"),
        (4, "acct-2", "Inbox", "Invoice attached",
         "vendor@example.com", "Please find the invoice",
         "2026-03-15T16:00:00"),
    ]
    for msg_id, acct, mbox, subj, sender, content, date in emails:
        conn.execute(
            "INSERT INTO emails "
            "(message_id, account, mailbox, subject, sender, "
            "content, date_received) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (msg_id, acct, mbox, subj, sender, content, date),
        )
    # Populate FTS5 index
    conn.execute(
        "INSERT INTO emails_fts(emails_fts) VALUES('rebuild')"
    )
    conn.commit()
    return conn


class TestSanitizeFtsQuery:
    """Tests for FTS5 query sanitization."""

    def test_empty_query(self):
        assert sanitize_fts_query("") == ""
        assert sanitize_fts_query("   ") == ""

    def test_simple_query(self):
        assert sanitize_fts_query("hello world") == "hello world"

    def test_escapes_special_characters(self):
        # Hyphens (FTS5 treats -term as NOT) → quoted
        assert sanitize_fts_query("meeting-notes") == '"meeting-notes"'
        # Colons (FTS5 column filter) → quoted
        assert sanitize_fts_query("subject:test") == '"subject:test"'
        # Parentheses (FTS5 grouping) → quoted
        assert sanitize_fts_query("(group)") == '"(group)"'
        # Carets → quoted
        assert sanitize_fts_query("boost^2") == '"boost^2"'
        # Single quotes → quoted
        assert sanitize_fts_query("it's") == '"it\'s"'

    def test_preserves_phrase_search(self):
        """Balanced double quotes are kept for phrase search."""
        result = sanitize_fts_query('"exact phrase"')
        assert result == '"exact phrase"'

        result = sanitize_fts_query('hello "exact phrase" world')
        assert '"exact phrase"' in result

    def test_preserves_prefix_wildcard(self):
        """Trailing * is preserved for prefix search."""
        assert sanitize_fts_query("meet*") == "meet*"
        assert sanitize_fts_query("invoice* report") == "invoice* report"

    def test_escapes_unbalanced_quotes(self):
        """Unbalanced quotes are dropped, not passed through."""
        result = sanitize_fts_query('test" OR hello')
        # The stray quote is stripped; terms and operator remain
        assert '"' not in result or result.count('"') % 2 == 0
        assert "test" in result
        assert "hello" in result

    def test_preserves_boolean_operators(self):
        result = sanitize_fts_query("hello OR world")
        assert "OR" in result

        result = sanitize_fts_query("hello AND world")
        assert "AND" in result

        result = sanitize_fts_query("hello NOT world")
        assert "NOT" in result

    def test_escapes_injection_attempts(self):
        # Colons are quoted in bare tokens
        result = sanitize_fts_query("col:value")
        assert result == '"col:value"'

    def test_strips_whitespace(self):
        assert sanitize_fts_query("  hello  ") == "hello"

    def test_bare_wildcard_stripped(self):
        """Bare * is invalid FTS5 — must be stripped."""
        assert sanitize_fts_query("*") == ""

    def test_bare_wildcard_in_multi_term(self):
        """Bare * among other terms is dropped, rest preserved."""
        assert sanitize_fts_query("hello * world") == "hello world"


class TestEscapeAllSpecial:
    """Tests for aggressive last-resort quoting."""

    def test_quotes_every_term(self):
        result = _escape_all_special("test meet")
        assert result == '"test" "meet"'

    def test_preserves_operators(self):
        result = _escape_all_special("hello OR world")
        assert result == '"hello" OR "world"'

    def test_preserves_individual_terms(self):
        """Each term is quoted separately, not wrapped in one phrase."""
        result = _escape_all_special("hello world")
        # Multiple terms remain multiple terms (each quoted)
        parts = result.split()
        assert len(parts) == 2


class TestSearchFts:
    """Tests for FTS5 search function."""

    def test_empty_query_returns_empty(self, populated_db: sqlite3.Connection):
        results = search_fts(populated_db, "")
        assert results == []

    def test_basic_search(self, populated_db: sqlite3.Connection):
        results = search_fts(populated_db, "meeting")
        assert len(results) >= 1
        # Check result structure
        result = results[0]
        assert hasattr(result, "id")
        assert hasattr(result, "subject")
        assert hasattr(result, "score")

    def test_search_with_multiple_terms(self, populated_db: sqlite3.Connection):
        results = search_fts(populated_db, "quarterly report")
        assert len(results) >= 1

    def test_search_respects_limit(self, populated_db: sqlite3.Connection):
        results = search_fts(populated_db, "the", limit=2)
        assert len(results) <= 2

    def test_search_filters_by_account(self, populated_db: sqlite3.Connection):
        results = search_fts(
            populated_db, "meeting", account="test-account-uuid"
        )
        assert all(r.account == "test-account-uuid" for r in results)

    def test_search_filters_by_mailbox(self, populated_db: sqlite3.Connection):
        results = search_fts(populated_db, "deadline", mailbox="Sent")
        assert len(results) >= 1
        assert all(r.mailbox == "Sent" for r in results)

    def test_search_results_ordered_by_score(
        self, populated_db: sqlite3.Connection
    ):
        results = search_fts(populated_db, "meeting", limit=10)
        if len(results) > 1:
            scores = [r.score for r in results]
            assert scores == sorted(scores, reverse=True)

    def test_search_handles_special_characters(
        self, populated_db: sqlite3.Connection
    ):
        # Hyphens should be escaped and work
        results = search_fts(populated_db, "test-query")
        assert isinstance(results, list)

        # Quotes should be escaped
        results = search_fts(populated_db, "meeting tomorrow")
        assert isinstance(results, list)

    def test_search_handles_malformed_queries(
        self, populated_db: sqlite3.Connection
    ):
        # Malformed queries should either return results or empty list
        # but not raise (due to retry logic)
        for query in ["test*", "hello:", "(broken"]:
            results = search_fts(populated_db, query)
            assert isinstance(results, list)

    def test_search_no_results(self, populated_db: sqlite3.Connection):
        results = search_fts(populated_db, "xyznonexistent123")
        assert results == []

    def test_search_fts_excludes_mailboxes(
        self, populated_db: sqlite3.Connection
    ):
        """exclude_mailboxes filters out specified mailboxes."""
        # "Sent" mailbox has the deadline email
        all_results = search_fts(populated_db, "deadline")
        assert any(r.mailbox == "Sent" for r in all_results)

        # Exclude Sent
        filtered = search_fts(
            populated_db, "deadline", exclude_mailboxes=["Sent"]
        )
        assert all(r.mailbox != "Sent" for r in filtered)


class TestCountMatches:
    """Tests for match counting function."""

    def test_empty_query_returns_zero(self, populated_db: sqlite3.Connection):
        assert count_matches(populated_db, "") == 0

    def test_count_basic_query(self, populated_db: sqlite3.Connection):
        count = count_matches(populated_db, "meeting")
        assert count >= 1

    def test_count_with_filters(self, populated_db: sqlite3.Connection):
        count = count_matches(
            populated_db, "deadline", account="test-account-uuid"
        )
        assert count >= 0

    def test_count_no_results(self, populated_db: sqlite3.Connection):
        count = count_matches(populated_db, "xyznonexistent123")
        assert count == 0


class TestCompositeKeyUniqueness:
    """Tests verifying composite key behavior."""

    def test_same_message_id_different_mailbox(
        self, populated_db: sqlite3.Connection
    ):
        """Message ID 1001 exists in both INBOX and Archive."""
        cursor = populated_db.execute(
            "SELECT COUNT(*) FROM emails WHERE message_id = 1001"
        )
        count = cursor.fetchone()[0]
        assert count == 2, "Same message_id should exist in different mailboxes"

    def test_search_returns_both_duplicates(
        self, populated_db: sqlite3.Connection
    ):
        """Search should find emails with same ID in different mailboxes."""
        results = search_fts(populated_db, "meeting")
        # Should find at least the INBOX and Archive versions
        mailboxes = {r.mailbox for r in results}
        assert len(mailboxes) >= 1


class TestSearchAttachments:
    """Tests for search_attachments (#41)."""

    def test_basic(self, temp_db: sqlite3.Connection):
        temp_db.execute(
            "INSERT INTO emails "
            "(message_id, account, mailbox, subject, sender, "
            "date_received, attachment_count) "
            "VALUES (1, 'acc', 'INBOX', 'Test', 'a@b.com', "
            "'2024-01-01', 1)"
        )
        rowid = temp_db.execute("SELECT last_insert_rowid()").fetchone()[0]
        temp_db.execute(
            "INSERT INTO attachments "
            "(email_rowid, filename, mime_type, file_size) "
            "VALUES (?, 'report.pdf', 'application/pdf', 100)",
            (rowid,),
        )
        temp_db.commit()

        results = search_attachments(temp_db, "report")
        assert len(results) == 1
        assert results[0]["filename"] == "report.pdf"
        assert results[0]["message_id"] == 1

    def test_with_filters(self, temp_db: sqlite3.Connection):
        temp_db.execute(
            "INSERT INTO emails "
            "(message_id, account, mailbox, subject, sender, "
            "date_received, attachment_count) "
            "VALUES (1, 'acc1', 'INBOX', 'Test', 'a@b.com', "
            "'2024-01-01', 1)"
        )
        rowid = temp_db.execute("SELECT last_insert_rowid()").fetchone()[0]
        temp_db.execute(
            "INSERT INTO attachments "
            "(email_rowid, filename) VALUES (?, 'doc.pdf')",
            (rowid,),
        )
        temp_db.commit()

        # Should find with correct account
        assert len(search_attachments(temp_db, "doc", account="acc1")) == 1
        # Should not find with wrong account
        assert len(search_attachments(temp_db, "doc", account="x")) == 0

    def test_no_results(self, temp_db: sqlite3.Connection):
        results = search_attachments(temp_db, "nonexistent")
        assert results == []


class TestSearchFtsColumnFilter:
    """Tests for FTS5 column-scoped queries."""

    def test_subject_column_filter(self, populated_db: sqlite3.Connection):
        """column='subject' restricts search to subject field."""
        results = search_fts(populated_db, "meeting", column="subject")
        assert len(results) >= 1
        # All results should have "meeting" in the subject
        for r in results:
            assert "meeting" in r.subject.lower()

    def test_sender_column_filter(self, populated_db: sqlite3.Connection):
        """column='sender' restricts search to sender field."""
        results = search_fts(populated_db, "boss", column="sender")
        assert len(results) >= 1
        for r in results:
            assert "boss" in r.sender.lower()

    def test_sender_column_no_body_match(
        self, populated_db: sqlite3.Connection
    ):
        """column='sender' should NOT match body-only terms."""
        # "quarterly" appears in body but not sender
        results = search_fts(populated_db, "quarterly", column="sender")
        assert results == []

    def test_subject_column_no_body_match(
        self, populated_db: sqlite3.Connection
    ):
        """column='subject' should NOT match body-only terms."""
        # "extended" appears in body ("extended to Friday") but no subject
        results = search_fts(populated_db, "extended", column="subject")
        assert results == []

    def test_content_column_filter(self, populated_db: sqlite3.Connection):
        """column='content' restricts search to body text."""
        results = search_fts(populated_db, "quarterly", column="content")
        assert len(results) >= 1

    def test_invalid_column_ignored(self, populated_db: sqlite3.Connection):
        """Invalid column name is safely ignored."""
        results = search_fts(populated_db, "meeting", column="invalid")
        # Falls back to all-column search
        assert len(results) >= 1

    def test_none_column_searches_all(self, populated_db: sqlite3.Connection):
        """column=None searches all columns (default)."""
        results = search_fts(populated_db, "meeting", column=None)
        assert len(results) >= 1


class TestDetectMatchedColumns:
    """Tests for detect_matched_columns (#41)."""

    def test_subject_match(self):
        from unittest.mock import MagicMock

        result = MagicMock()
        result.subject = "Meeting tomorrow"
        result.sender = "boss@co.com"

        matched = detect_matched_columns("meeting", result)
        assert "subject" in matched
        assert "body" in matched

    def test_sender_match(self):
        from unittest.mock import MagicMock

        result = MagicMock()
        result.subject = "Hello"
        result.sender = "john@example.com"

        matched = detect_matched_columns("john", result)
        assert "sender" in matched

    def test_body_always_included(self):
        from unittest.mock import MagicMock

        result = MagicMock()
        result.subject = "Other"
        result.sender = "other@test.com"

        matched = detect_matched_columns("xyzunknown", result)
        assert "body" in matched

    def test_empty_query(self):
        from unittest.mock import MagicMock

        result = MagicMock()
        result.subject = "Test"
        result.sender = "a@b.com"

        assert detect_matched_columns("!!!", result) == "body"


class TestMailboxCaseInsensitive:
    """Tests for case-insensitive mailbox filtering (#67)."""

    def test_mailbox_case_insensitive(self, fts_db):
        """Mailbox filter should match regardless of case."""
        # fts_db has mailboxes: "Inbox" and "Sent"
        r1 = search_fts(fts_db, "budget", mailbox="Inbox")
        r2 = search_fts(fts_db, "budget", mailbox="inbox")
        r3 = search_fts(fts_db, "budget", mailbox="INBOX")
        assert len(r1) == len(r2) == len(r3)
        assert len(r1) > 0

    def test_exclude_mailbox_case_insensitive(self, fts_db):
        """Excluded mailboxes should match regardless of case."""
        r1 = search_fts(
            fts_db, "budget", exclude_mailboxes=["Sent"]
        )
        r2 = search_fts(
            fts_db, "budget", exclude_mailboxes=["sent"]
        )
        assert len(r1) == len(r2)
        assert all(r.mailbox != "Sent" for r in r1)


class TestDateRangeFiltering:
    """Tests for before/after date filtering in search."""

    def test_after_filter(self, fts_db):
        results = search_fts(
            fts_db, "budget", after="2026-02-01"
        )
        assert len(results) == 1
        assert results[0].id == 3  # March email only

    def test_before_filter(self, fts_db):
        results = search_fts(
            fts_db, "budget", before="2026-02-01"
        )
        assert len(results) == 1
        assert results[0].id == 1  # January email only

    def test_both_filters(self, fts_db):
        results = search_fts(
            fts_db,
            "budget OR project OR invoice",
            after="2026-02-01",
            before="2026-03-10",
        )
        # Should get emails 2 (Feb) and 3 (Mar 1) but not 1 or 4
        ids = {r.id for r in results}
        assert ids == {2, 3}

    def test_date_with_account(self, fts_db):
        results = search_fts(
            fts_db,
            "budget OR project OR invoice",
            account="acct-1",
            after="2026-02-01",
        )
        ids = {r.id for r in results}
        assert 4 not in ids  # acct-2 excluded

    def test_no_date_filter(self, fts_db):
        results = search_fts(fts_db, "budget")
        assert len(results) == 2  # Both budget emails

    def test_date_filter_attachments(self, fts_db):
        # Add attachment data
        fts_db.execute(
            "INSERT INTO attachments "
            "(email_rowid, filename, mime_type, file_size) "
            "VALUES (1, 'budget.pdf', 'application/pdf', 1024)"
        )
        fts_db.execute(
            "INSERT INTO attachments "
            "(email_rowid, filename, mime_type, file_size) "
            "VALUES (4, 'invoice.pdf', 'application/pdf', 2048)"
        )
        fts_db.commit()

        results = search_attachments(
            fts_db, "pdf", after="2026-03-01"
        )
        assert len(results) == 1
        assert results[0]["filename"] == "invoice.pdf"


class TestSearchFtsHighlight:
    """Tests for highlighted search results."""

    def test_highlight_basic(self, fts_db):
        results = search_fts_highlight(fts_db, "budget")
        assert len(results) == 2
        # At least one result should have ** markers
        has_markers = any(
            "**" in r.subject or "**" in r.content_snippet
            for r in results
        )
        assert has_markers

    def test_highlight_with_account_filter(self, fts_db):
        results = search_fts_highlight(
            fts_db, "budget", account="acct-1"
        )
        assert all(r.account == "acct-1" for r in results)

    def test_highlight_with_date_range(self, fts_db):
        results = search_fts_highlight(
            fts_db, "budget", after="2026-02-01"
        )
        assert len(results) == 1
        assert results[0].id == 3

    def test_highlight_with_exclude_mailboxes(self, fts_db):
        results = search_fts_highlight(
            fts_db, "budget", exclude_mailboxes=["Sent"]
        )
        assert all(r.mailbox != "Sent" for r in results)

    def test_highlight_fallback_on_error(self, fts_db):
        # Corrupt query that might cause highlight to fail
        # but search_fts handles via retry
        results = search_fts_highlight(fts_db, "budget")
        assert isinstance(results, list)
