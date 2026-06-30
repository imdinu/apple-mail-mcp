"""Tests for account-level exclusion (APPLE_MAIL_INDEX_EXCLUDE_ACCOUNTS).

Covers the global-boundary behavior: configured accounts are skipped at
index time, filtered from search, and invisible to the live tools
(list_accounts / list_mailboxes / get_emails / get_email / search).

Server-tool gates short-circuit before any JXA, so they are tested by
asserting the JXA executors are never called for a hidden account.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from apple_mail_mcp import config
from apple_mail_mcp.config import (
    _invalidate_config_cache,
    get_index_exclude_accounts,
)
from apple_mail_mcp.index.accounts import (
    AccountMap,
    resolve_excluded_account_uuids,
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Reset the AccountMap singleton and config cache around each test."""
    AccountMap.get_instance().reset()
    _invalidate_config_cache()
    monkeypatch.delenv("APPLE_MAIL_INDEX_EXCLUDE_ACCOUNTS", raising=False)
    yield
    AccountMap.get_instance().reset()
    _invalidate_config_cache()


# ─── config resolution ───────────────────────────────────────


class TestConfigResolution:
    def test_default_is_empty(self):
        assert get_index_exclude_accounts() == set()

    def test_env_csv(self, monkeypatch):
        monkeypatch.setenv(
            "APPLE_MAIL_INDEX_EXCLUDE_ACCOUNTS", "Work PHI, Spouse ,"
        )
        assert get_index_exclude_accounts() == {"Work PHI", "Spouse"}

    def test_env_empty_string_is_explicit_none(self, monkeypatch):
        monkeypatch.setenv("APPLE_MAIL_INDEX_EXCLUDE_ACCOUNTS", "")
        assert get_index_exclude_accounts() == set()

    def test_toml_list(self, tmp_path, monkeypatch):
        from apple_mail_mcp.config import CONFIG_SCHEMA_VERSION

        path = tmp_path / "config.toml"
        path.write_text(
            f"config_version = {CONFIG_SCHEMA_VERSION}\n"
            '[index]\nexclude_accounts = ["Clinic"]\n'
        )
        monkeypatch.setattr(config, "CONFIG_FILE_PATH", path)
        _invalidate_config_cache()
        assert get_index_exclude_accounts() == {"Clinic"}


# ─── name → UUID resolution ──────────────────────────────────


class TestNameResolution:
    def test_names_to_uuids_resolves_known(self):
        m = AccountMap.get_instance()
        m.load_from_jxa(
            [{"name": "Work", "id": "UUID-W"}, {"name": "PHI", "id": "UUID-P"}]
        )
        assert m.names_to_uuids({"PHI"}) == {"UUID-P"}

    def test_names_to_uuids_warns_on_unknown(self, caplog):
        m = AccountMap.get_instance()
        m.load_from_jxa([{"name": "Work", "id": "UUID-W"}])
        with caplog.at_level("WARNING"):
            assert m.names_to_uuids({"Typo"}) == set()
        assert "Typo" in caplog.text
        assert "case-sensitive" in caplog.text

    def test_names_to_uuids_is_case_sensitive(self):
        m = AccountMap.get_instance()
        m.load_from_jxa([{"name": "Work", "id": "UUID-W"}])
        assert m.names_to_uuids({"work"}) == set()  # wrong case → no match

    def test_resolver_empty_skips_jxa(self):
        # No names → no JXA call, returns empty.
        with patch("apple_mail_mcp.executor.execute_with_core") as mock_jxa:
            assert resolve_excluded_account_uuids(set()) == set()
            mock_jxa.assert_not_called()

    def test_resolver_fetches_and_resolves(self):
        with patch(
            "apple_mail_mcp.executor.execute_with_core",
            return_value=[{"name": "PHI", "id": "UUID-P"}],
        ):
            assert resolve_excluded_account_uuids({"PHI"}) == {"UUID-P"}

    def test_resolver_degrades_on_jxa_failure(self, caplog):
        with patch(
            "apple_mail_mcp.executor.execute_with_core",
            side_effect=RuntimeError("osascript blew up"),
        ):
            with caplog.at_level("WARNING"):
                assert resolve_excluded_account_uuids({"PHI"}) == set()
            assert "Could not resolve excluded accounts" in caplog.text


# ─── index-time disk walk ────────────────────────────────────


class TestDiskWalkExclusion:
    def _make_tree(self, root: Path) -> None:
        for uuid, mbox in [("UUID-A", "INBOX"), ("UUID-B", "INBOX")]:
            d = root / uuid / f"{mbox}.mbox" / "Data" / "Messages"
            d.mkdir(parents=True)
            (d / "1.emlx").write_text("x")

    def test_excludes_account_by_uuid(self, tmp_path):
        from apple_mail_mcp.index.disk import scan_emlx_files

        self._make_tree(tmp_path)
        files = list(
            scan_emlx_files(
                tmp_path,
                exclude_mailboxes=set(),
                exclude_account_uuids={"UUID-A"},
            )
        )
        assert files  # UUID-B remains
        assert all("UUID-A" not in str(p) for p in files)
        assert all("UUID-B" in str(p) for p in files)


# ─── search SQL filter ───────────────────────────────────────


class TestSearchFilter:
    def test_clause_is_case_sensitive_uuid_match(self):
        from apple_mail_mcp.index.search import add_account_mailbox_filter

        params: list = []
        sql = add_account_mailbox_filter(
            "SELECT 1 WHERE 1=1",
            params,
            account=None,
            mailbox=None,
            exclude_accounts=["UUID-P", "UUID-Q"],
        )
        # No LOWER() wrapping (case-sensitive), values bound as-is.
        assert "account NOT IN (?, ?)" in sql
        assert "LOWER(e.account)" not in sql
        assert params == ["UUID-P", "UUID-Q"]

    def test_search_fts_excludes_account(self, tmp_path):
        from apple_mail_mcp.index.schema import (
            create_connection,
            get_schema_sql,
        )
        from apple_mail_mcp.index.search import search_fts

        conn = create_connection(":memory:")
        conn.executescript(get_schema_sql())
        rows = [
            (1, "UUID-A", "INBOX", "budget meeting", "a@x.com", "the budget"),
            (2, "UUID-P", "INBOX", "budget secret", "b@x.com", "phi budget"),
        ]
        conn.executemany(
            "INSERT INTO emails (message_id, account, mailbox, subject, "
            "sender, content, date_received, emlx_path) "
            "VALUES (?, ?, ?, ?, ?, ?, '2026-01-01', '/x')",
            rows,
        )
        conn.commit()

        all_hits = {r.id for r in search_fts(conn, "budget", limit=10)}
        assert all_hits == {1, 2}
        filtered = {
            r.id
            for r in search_fts(
                conn, "budget", limit=10, exclude_accounts=["UUID-P"]
            )
        }
        assert filtered == {1}  # the hidden account is gone
        conn.close()


# ─── server live-path gates ──────────────────────────────────


class TestServerGates:
    @pytest.mark.asyncio
    @patch(
        "apple_mail_mcp.server.execute_with_core_async", new_callable=AsyncMock
    )
    async def test_list_mailboxes_hidden_returns_empty_no_jxa(
        self, mock_jxa, monkeypatch
    ):
        monkeypatch.setenv("APPLE_MAIL_INDEX_EXCLUDE_ACCOUNTS", "PHI")
        from apple_mail_mcp.server import list_mailboxes

        assert await list_mailboxes("PHI") == []
        mock_jxa.assert_not_called()  # never reached JXA

    @pytest.mark.asyncio
    @patch(
        "apple_mail_mcp.server.execute_with_core_async", new_callable=AsyncMock
    )
    async def test_list_accounts_hides_excluded(self, mock_jxa, monkeypatch):
        monkeypatch.setenv("APPLE_MAIL_INDEX_EXCLUDE_ACCOUNTS", "PHI")
        mock_jxa.return_value = [
            {"name": "Work", "id": "UUID-W"},
            {"name": "PHI", "id": "UUID-P"},
        ]
        from apple_mail_mcp.server import list_accounts

        names = {a["name"] for a in await list_accounts()}
        assert names == {"Work"}

    @pytest.mark.asyncio
    @patch("apple_mail_mcp.server.execute_query_async", new_callable=AsyncMock)
    async def test_get_emails_hidden_returns_empty_no_jxa(
        self, mock_jxa, monkeypatch
    ):
        monkeypatch.setenv("APPLE_MAIL_INDEX_EXCLUDE_ACCOUNTS", "PHI")
        from apple_mail_mcp.server import get_emails

        assert await get_emails(account="PHI") == []
        mock_jxa.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_email_hidden_raises_not_found(self, monkeypatch):
        monkeypatch.setenv("APPLE_MAIL_INDEX_EXCLUDE_ACCOUNTS", "PHI")
        from apple_mail_mcp.server import get_email

        with pytest.raises(ValueError, match="not found"):
            await get_email(message_id=123, account="PHI")

    @pytest.mark.asyncio
    async def test_search_hidden_account_returns_empty(self, monkeypatch):
        monkeypatch.setenv("APPLE_MAIL_INDEX_EXCLUDE_ACCOUNTS", "PHI")
        from apple_mail_mcp.server import search

        assert await search("anything", account="PHI") == []
