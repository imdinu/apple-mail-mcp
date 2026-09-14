"""Tests for the move_email() write tool (#65) and its optimistic index
eviction (#66).

All JXA is mocked via `apple_mail_mcp.server.execute_with_core_async`;
the generated script text is the contract under test. The index
manager and account map are patched at their server-side accessors so
no SQLite or JXA account lookup runs.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apple_mail_mcp import config
from apple_mail_mcp.config import _invalidate_config_cache, set_read_only_mode
from apple_mail_mcp.index.accounts import AccountMap

JXA = "apple_mail_mcp.server.execute_with_core_async"
INDEX = "apple_mail_mcp.server._get_index_manager"
ACCOUNTS = "apple_mail_mcp.server._get_account_map"

UUID = "UUID-ICLOUD"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Fresh AccountMap, no read-only, no developer config/env leaking
    defaults (a real `defaults.mailbox` would change the script text)."""
    AccountMap.get_instance().reset()
    monkeypatch.setattr(config, "CONFIG_FILE_PATH", tmp_path / "no.toml")
    for var in (
        "APPLE_MAIL_INDEX_EXCLUDE_ACCOUNTS",
        "APPLE_MAIL_DEFAULT_ACCOUNT",
        "APPLE_MAIL_DEFAULT_MAILBOX",
        "APPLE_MAIL_READ_ONLY",
    ):
        monkeypatch.delenv(var, raising=False)
    _invalidate_config_cache()
    set_read_only_mode(False)
    monkeypatch.setattr(
        "apple_mail_mcp.index.envelope_direct.envelope_index_path",
        lambda mail_dir: Path("/nonexistent/Envelope Index"),
    )
    yield
    set_read_only_mode(False)
    AccountMap.get_instance().reset()
    _invalidate_config_cache()


def _index_mock(has_index: bool = True) -> MagicMock:
    mgr = MagicMock()
    mgr.has_index.return_value = has_index
    mgr.delete_email.return_value = 1
    return mgr


def _account_map_mock(uuid: str | None = UUID) -> MagicMock:
    acct_map = MagicMock()
    acct_map.ensure_loaded = AsyncMock()
    acct_map.name_to_uuid.return_value = uuid
    return acct_map


def _jxa_error(text: str) -> Exception:
    from apple_mail_mcp.executor import JXAError

    return JXAError(text)


# ─── guards: nothing reaches JXA ─────────────────────────────


class TestMoveEmailGuards:
    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_read_only_raises_permission_error(self, mock_jxa):
        from apple_mail_mcp.server import move_email

        set_read_only_mode(True)
        with pytest.raises(PermissionError, match="read-only"):
            await move_email([1], "Archive")
        mock_jxa.assert_not_called()

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_hidden_account_raises_not_found(self, mock_jxa, monkeypatch):
        monkeypatch.setenv("APPLE_MAIL_INDEX_EXCLUDE_ACCOUNTS", "PHI")
        from apple_mail_mcp.server import move_email

        with pytest.raises(ValueError, match="not found"):
            await move_email([1], "Archive", account="PHI")
        mock_jxa.assert_not_called()

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_empty_ids_raises(self, mock_jxa):
        from apple_mail_mcp.server import move_email

        with pytest.raises(ValueError, match="at least one id"):
            await move_email([], "Archive", account="iCloud")
        mock_jxa.assert_not_called()

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    @pytest.mark.parametrize("target", ["", "   ", "\t\n"])
    async def test_blank_target_raises(self, mock_jxa, target):
        from apple_mail_mcp.server import move_email

        with pytest.raises(ValueError, match="target_mailbox"):
            await move_email([1], target, account="iCloud")
        mock_jxa.assert_not_called()


# ─── happy path ──────────────────────────────────────────────


class TestMoveEmailHappyPath:
    @pytest.mark.asyncio
    @patch(ACCOUNTS)
    @patch(INDEX)
    @patch(JXA, new_callable=AsyncMock)
    async def test_moves_and_evicts_index_rows(
        self, mock_jxa, mock_index, mock_accounts
    ):
        mgr = _index_mock()
        mock_index.return_value = mgr
        acct_map = _account_map_mock()
        mock_accounts.return_value = acct_map
        mock_jxa.return_value = {
            "account": "iCloud",
            "mailbox": "Archive",
            "ids": [1, 2],
        }
        from apple_mail_mcp.server import move_email

        result = await move_email(
            [1, 2], "Archive", account="iCloud", mailbox="INBOX"
        )

        assert result == [
            {"id": 1, "account": "iCloud", "mailbox": "Archive"},
            {"id": 2, "account": "iCloud", "mailbox": "Archive"},
        ]

        mock_jxa.assert_called_once()
        script = mock_jxa.call_args.args[0]
        # Source mailbox setup + json-dumped ids.
        assert 'MailCore.getAccount("iCloud")' in script
        assert 'MailCore.getMailbox(account, "INBOX")' in script
        assert "[1, 2]" in script
        # Target is resolved (and forced) before the move command.
        target_pos = script.index('MailCore.getMailbox(account, "Archive")')
        move_pos = script.index("Mail.move(")
        assert target_pos < move_pos
        # Every id is validated before any move.
        assert script.index("Message not found with ID") < move_pos

        # Optimistic eviction keyed by account UUID + SOURCE mailbox.
        acct_map.name_to_uuid.assert_called_once_with("iCloud")
        assert mgr.delete_email.call_count == 2
        mgr.delete_email.assert_any_call(1, account=UUID, mailbox="INBOX")
        mgr.delete_email.assert_any_call(2, account=UUID, mailbox="INBOX")

    @pytest.mark.asyncio
    @patch(ACCOUNTS)
    @patch(INDEX)
    @patch(JXA, new_callable=AsyncMock)
    async def test_default_account_uses_jxa_reported_name(
        self, mock_jxa, mock_index, mock_accounts
    ):
        """With no account given, JXA picks the first one; its reported
        name feeds both the return value and the UUID lookup."""
        mgr = _index_mock()
        mock_index.return_value = mgr
        acct_map = _account_map_mock()
        mock_accounts.return_value = acct_map
        mock_jxa.return_value = {
            "account": "Personal",
            "mailbox": "Deleted Messages",
            "ids": [7],
        }
        from apple_mail_mcp.server import move_email

        result = await move_email([7], "Trash")

        assert result == [
            {"id": 7, "account": "Personal", "mailbox": "Deleted Messages"}
        ]
        script = mock_jxa.call_args.args[0]
        assert "MailCore.getAccount(null)" in script
        assert 'MailCore.getMailbox(account, "INBOX")' in script
        assert 'MailCore.getMailbox(account, "Trash")' in script
        acct_map.name_to_uuid.assert_called_once_with("Personal")
        mgr.delete_email.assert_called_once_with(
            7, account=UUID, mailbox="INBOX"
        )

    @pytest.mark.asyncio
    @patch(ACCOUNTS)
    @patch(INDEX)
    @patch(JXA, new_callable=AsyncMock)
    async def test_strips_target_and_escapes_strings(
        self, mock_jxa, mock_index, mock_accounts
    ):
        mock_index.return_value = _index_mock(has_index=False)
        mock_accounts.return_value = _account_map_mock()
        mock_jxa.return_value = {
            "account": "iCloud",
            "mailbox": "Work/Projects",
            "ids": [1],
        }
        from apple_mail_mcp.server import move_email

        await move_email(
            [1], "  Work/Projects ", account='Ev"il', mailbox="In\\box"
        )
        script = mock_jxa.call_args.args[0]
        assert 'MailCore.getMailbox(account, "Work/Projects")' in script
        assert 'MailCore.getAccount("Ev\\"il")' in script
        assert 'MailCore.getMailbox(account, "In\\\\box")' in script

    @pytest.mark.asyncio
    @patch(ACCOUNTS)
    @patch(INDEX)
    @patch(JXA, new_callable=AsyncMock)
    async def test_batch_is_deduped_and_clamped(
        self, mock_jxa, mock_index, mock_accounts
    ):
        from apple_mail_mcp.server import MAX_WRITE_BATCH, move_email

        mgr = _index_mock()
        mock_index.return_value = mgr
        mock_accounts.return_value = _account_map_mock()
        mock_jxa.return_value = {
            "account": "iCloud",
            "mailbox": "Archive",
            "ids": [],
        }

        # 15 ids with duplicates interleaved → 12 unique → first 10 kept.
        raw = [1, 2, 2, 3, 4, 1, 5, 6, 7, 8, 9, 10, 11, 12, 3]
        expected = list(range(1, MAX_WRITE_BATCH + 1))

        result = await move_email(raw, "Archive", account="iCloud")

        assert [r["id"] for r in result] == expected
        script = mock_jxa.call_args.args[0]
        assert f"const wanted = {expected};" in script
        assert "11" not in script.split("const wanted")[1].split(";")[0]
        assert mgr.delete_email.call_count == MAX_WRITE_BATCH


# ─── JXA failures ────────────────────────────────────────────


class TestMoveEmailErrors:
    @pytest.mark.asyncio
    @patch(ACCOUNTS)
    @patch(INDEX)
    @patch(JXA, new_callable=AsyncMock)
    async def test_unknown_target_mailbox_is_clean_value_error(
        self, mock_jxa, mock_index, mock_accounts
    ):
        mgr = _index_mock()
        mock_index.return_value = mgr
        mock_accounts.return_value = _account_map_mock()
        mock_jxa.side_effect = _jxa_error(
            "execution error: Error: Error: Target mailbox not found: "
            "NoSuchMailbox (-2700)"
        )
        from apple_mail_mcp.server import move_email

        with pytest.raises(ValueError) as exc_info:
            await move_email([1], "NoSuchMailbox", account="iCloud")
        assert "'NoSuchMailbox'" in str(exc_info.value)
        assert "'iCloud'" in str(exc_info.value)
        mgr.delete_email.assert_not_called()

    @pytest.mark.asyncio
    @patch(ACCOUNTS)
    @patch(INDEX)
    @patch(JXA, new_callable=AsyncMock)
    async def test_raw_1728_maps_to_target_mailbox_error(
        self, mock_jxa, mock_index, mock_accounts
    ):
        mgr = _index_mock()
        mock_index.return_value = mgr
        mock_accounts.return_value = _account_map_mock()
        mock_jxa.side_effect = _jxa_error(
            "execution error: Error: Can't get object. (-1728)"
        )
        from apple_mail_mcp.server import move_email

        with pytest.raises(ValueError, match="'Nope' not found"):
            await move_email([1], "Nope", account="iCloud")
        mgr.delete_email.assert_not_called()

    @pytest.mark.asyncio
    @patch(ACCOUNTS)
    @patch(INDEX)
    @patch(JXA, new_callable=AsyncMock)
    async def test_unknown_source_mailbox_names_source(
        self, mock_jxa, mock_index, mock_accounts
    ):
        mgr = _index_mock()
        mock_index.return_value = mgr
        mock_accounts.return_value = _account_map_mock()
        mock_jxa.side_effect = _jxa_error(
            "execution error: Error: Error: Source mailbox not found: "
            "Ghost (-2700)"
        )
        from apple_mail_mcp.server import move_email

        with pytest.raises(ValueError, match="'Ghost' not found"):
            await move_email([1], "Archive", account="iCloud", mailbox="Ghost")
        mgr.delete_email.assert_not_called()

    @pytest.mark.asyncio
    @patch(ACCOUNTS)
    @patch(INDEX)
    @patch(JXA, new_callable=AsyncMock)
    async def test_missing_message_is_clean_value_error(
        self, mock_jxa, mock_index, mock_accounts
    ):
        mgr = _index_mock()
        mock_index.return_value = mgr
        mock_accounts.return_value = _account_map_mock()
        mock_jxa.side_effect = _jxa_error(
            "execution error: Error: Error: Message not found with ID: "
            "999 (-2700)"
        )
        from apple_mail_mcp.server import move_email

        with pytest.raises(ValueError, match=r"^Message 999 not found\.$"):
            await move_email([1, 999], "Archive", account="iCloud")
        mgr.delete_email.assert_not_called()

    @pytest.mark.asyncio
    @patch(ACCOUNTS)
    @patch(INDEX)
    @patch(JXA, new_callable=AsyncMock)
    async def test_other_jxa_errors_propagate(
        self, mock_jxa, mock_index, mock_accounts
    ):
        mgr = _index_mock()
        mock_index.return_value = mgr
        mock_accounts.return_value = _account_map_mock()
        mock_jxa.side_effect = _jxa_error("Mail got an error: Connection lost")
        from apple_mail_mcp.executor import JXAError
        from apple_mail_mcp.server import move_email

        with pytest.raises(JXAError, match="Connection lost"):
            await move_email([1], "Archive", account="iCloud")
        mgr.delete_email.assert_not_called()


# ─── index eviction resilience (#66) ─────────────────────────


class TestMoveEmailIndexEviction:
    @pytest.mark.asyncio
    @patch(ACCOUNTS)
    @patch(INDEX)
    @patch(JXA, new_callable=AsyncMock)
    async def test_delete_failure_does_not_fail_move(
        self, mock_jxa, mock_index, mock_accounts, caplog
    ):
        mgr = _index_mock()
        mgr.delete_email.side_effect = RuntimeError("database is locked")
        mock_index.return_value = mgr
        mock_accounts.return_value = _account_map_mock()
        mock_jxa.return_value = {
            "account": "iCloud",
            "mailbox": "Archive",
            "ids": [1],
        }
        from apple_mail_mcp.server import move_email

        with caplog.at_level("WARNING", logger="apple_mail_mcp.server"):
            result = await move_email([1], "Archive", account="iCloud")

        assert result == [{"id": 1, "account": "iCloud", "mailbox": "Archive"}]
        assert any(
            "evict" in rec.getMessage().lower() for rec in caplog.records
        )

    @pytest.mark.asyncio
    @patch(ACCOUNTS)
    @patch(INDEX)
    @patch(JXA, new_callable=AsyncMock)
    async def test_no_index_skips_eviction(
        self, mock_jxa, mock_index, mock_accounts
    ):
        mgr = _index_mock(has_index=False)
        mock_index.return_value = mgr
        acct_map = _account_map_mock()
        mock_accounts.return_value = acct_map
        mock_jxa.return_value = {
            "account": "iCloud",
            "mailbox": "Archive",
            "ids": [1],
        }
        from apple_mail_mcp.server import move_email

        result = await move_email([1], "Archive", account="iCloud")

        assert result == [{"id": 1, "account": "iCloud", "mailbox": "Archive"}]
        mgr.delete_email.assert_not_called()
        acct_map.ensure_loaded.assert_not_called()

    @pytest.mark.asyncio
    @patch(ACCOUNTS)
    @patch(INDEX)
    @patch(JXA, new_callable=AsyncMock)
    async def test_unresolvable_account_uuid_skips_eviction(
        self, mock_jxa, mock_index, mock_accounts, caplog
    ):
        """Never delete by bare message id: ids are only unique per
        mailbox, so an account-less delete could hit another account."""
        mgr = _index_mock()
        mock_index.return_value = mgr
        mock_accounts.return_value = _account_map_mock(uuid=None)
        mock_jxa.return_value = {
            "account": "iCloud",
            "mailbox": "Archive",
            "ids": [1],
        }
        from apple_mail_mcp.server import move_email

        with caplog.at_level("WARNING", logger="apple_mail_mcp.server"):
            result = await move_email([1], "Archive", account="iCloud")

        assert result[0]["mailbox"] == "Archive"
        mgr.delete_email.assert_not_called()
        assert any("no uuid" in r.getMessage().lower() for r in caplog.records)

    @pytest.mark.asyncio
    @patch(ACCOUNTS)
    @patch(INDEX)
    @patch(JXA, new_callable=AsyncMock)
    async def test_account_map_failure_does_not_fail_move(
        self, mock_jxa, mock_index, mock_accounts
    ):
        mgr = _index_mock()
        mock_index.return_value = mgr
        acct_map = _account_map_mock()
        acct_map.ensure_loaded.side_effect = RuntimeError("JXA down")
        mock_accounts.return_value = acct_map
        mock_jxa.return_value = {
            "account": "iCloud",
            "mailbox": "Archive",
            "ids": [1],
        }
        from apple_mail_mcp.server import move_email

        result = await move_email([1], "Archive", account="iCloud")

        assert result == [{"id": 1, "account": "iCloud", "mailbox": "Archive"}]
        mgr.delete_email.assert_not_called()
