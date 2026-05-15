"""Tests for environment-based configuration helpers."""

from __future__ import annotations

from apple_mail_mcp.config import (
    get_index_exclude_accounts,
    get_index_include_mailboxes,
)


def test_get_index_exclude_accounts_parses_csv(monkeypatch):
    monkeypatch.setenv(
        "APPLE_MAIL_INDEX_EXCLUDE_ACCOUNTS",
        "Extreme, work-uuid ,, iCloud",
    )

    assert get_index_exclude_accounts() == {
        "Extreme",
        "work-uuid",
        "iCloud",
    }


def test_get_index_include_mailboxes_parses_csv(monkeypatch):
    monkeypatch.setenv(
        "APPLE_MAIL_INDEX_INCLUDE_MAILBOXES",
        "INBOX, Yoshiko/Cubo ,, Remodel",
    )

    assert get_index_include_mailboxes() == {
        "INBOX",
        "Yoshiko/Cubo",
        "Remodel",
    }


def test_get_index_include_mailboxes_defaults_to_none(monkeypatch):
    monkeypatch.delenv("APPLE_MAIL_INDEX_INCLUDE_MAILBOXES", raising=False)

    assert get_index_include_mailboxes() is None
