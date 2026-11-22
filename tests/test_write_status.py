"""Tests for the update_email_status write tool (#64).

All JXA is mocked via ``apple_mail_mcp.server.execute_with_core_async``;
the generated script text is the contract under test (see CLAUDE.md
"Testing"). Read-only mode is reset after every test like
``TestEnsureWritable`` in test_server.py, and hidden accounts use the
env-var pattern from test_account_exclusion.py.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from apple_mail_mcp import config
from apple_mail_mcp.config import _invalidate_config_cache, set_read_only_mode
from apple_mail_mcp.index.accounts import AccountMap

JXA = "apple_mail_mcp.server.execute_with_core_async"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Keep each test off the developer's real config/env and singletons."""
    AccountMap.get_instance().reset()
    monkeypatch.setattr(config, "CONFIG_FILE_PATH", tmp_path / "no.toml")
    monkeypatch.delenv("APPLE_MAIL_INDEX_EXCLUDE_ACCOUNTS", raising=False)
    monkeypatch.delenv("APPLE_MAIL_DEFAULT_ACCOUNT", raising=False)
    monkeypatch.delenv("APPLE_MAIL_DEFAULT_MAILBOX", raising=False)
    monkeypatch.delenv("APPLE_MAIL_READ_ONLY", raising=False)
    _invalidate_config_cache()
    set_read_only_mode(False)
    yield
    set_read_only_mode(False)
    AccountMap.get_instance().reset()
    _invalidate_config_cache()


def _script(mock_jxa) -> str:
    """The JXA script text handed to the (single) mocked executor call."""
    mock_jxa.assert_called_once()
    return mock_jxa.call_args.args[0]


class TestUpdateEmailStatusGates:
    """Guards that must fire before any JXA runs."""

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_read_only_mode_raises_permission_error(self, mock_jxa):
        from apple_mail_mcp.server import update_email_status

        set_read_only_mode(True)
        with pytest.raises(PermissionError, match="read-only"):
            await update_email_status([1], read=True)
        mock_jxa.assert_not_called()

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_hidden_account_raises_no_jxa(self, mock_jxa, monkeypatch):
        monkeypatch.setenv("APPLE_MAIL_INDEX_EXCLUDE_ACCOUNTS", "PHI")
        from apple_mail_mcp.server import update_email_status

        with pytest.raises(ValueError, match="not found"):
            await update_email_status([1], read=True, account="PHI")
        mock_jxa.assert_not_called()

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_neither_flag_raises(self, mock_jxa):
        from apple_mail_mcp.server import update_email_status

        with pytest.raises(ValueError, match="read and/or flagged"):
            await update_email_status([1])
        mock_jxa.assert_not_called()

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_empty_ids_raises(self, mock_jxa):
        from apple_mail_mcp.server import update_email_status

        with pytest.raises(ValueError, match="at least one id"):
            await update_email_status([], read=True)
        mock_jxa.assert_not_called()


class TestUpdateEmailStatusScript:
    """Happy path: the generated script is the JXA contract."""

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_mark_read_returns_new_state(self, mock_jxa):
        from apple_mail_mcp.server import update_email_status

        expected = [{"id": 1, "read": True, "flagged": False}]
        mock_jxa.return_value = expected

        result = await update_email_status(
            [1], read=True, account="Work", mailbox="INBOX"
        )

        assert result == expected
        script = _script(mock_jxa)
        # ids and flags enter the script via json.dumps()
        assert f"const targetIds = {json.dumps([1])};" in script
        assert "msg.readStatus = true;" in script
        # mailbox setup for the requested account/mailbox
        assert 'MailCore.getAccount("Work")' in script
        assert 'MailCore.getMailbox(account, "INBOX")' in script
        # flagged=None must leave the flag untouched
        assert "msg.flaggedStatus =" not in script
        # ...but the resulting state is still read back
        assert "flagged: msg.flaggedStatus()" in script
        assert "read: msg.readStatus()" in script

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_unflag_only_leaves_read_untouched(self, mock_jxa):
        from apple_mail_mcp.server import update_email_status

        mock_jxa.return_value = [{"id": 7, "read": False, "flagged": False}]

        await update_email_status([7], flagged=False)

        script = _script(mock_jxa)
        assert "msg.flaggedStatus = false;" in script
        assert "msg.readStatus =" not in script

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_both_flags_emit_both_assignments(self, mock_jxa):
        from apple_mail_mcp.server import update_email_status

        mock_jxa.return_value = [{"id": 3, "read": False, "flagged": True}]

        await update_email_status([3], read=False, flagged=True)

        script = _script(mock_jxa)
        assert "msg.readStatus = false;" in script
        assert "msg.flaggedStatus = true;" in script

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_validates_all_ids_before_mutating(self, mock_jxa):
        """Missing ids throw from the lookup loop, before any assignment."""
        from apple_mail_mcp.server import update_email_status

        mock_jxa.return_value = []
        await update_email_status([1, 2], read=True)

        script = _script(mock_jxa)
        # single id() fetch, indexOf per target, throw before mutation
        assert script.count("mailbox.messages.id()") == 1
        assert "ids.indexOf(targetId)" in script
        throw_at = script.index("Message not found with ID")
        mutate_at = script.index("msg.readStatus = true")
        assert throw_at < mutate_at

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_batch_is_deduped_and_clamped(self, mock_jxa):
        from apple_mail_mcp.server import MAX_WRITE_BATCH, update_email_status

        assert MAX_WRITE_BATCH == 10
        mock_jxa.return_value = []
        # 15 ids with duplicates: 1,1,2,2,...  → unique 1..8 then 9..15
        ids = [1, 1, 2, 2, 3, 3, 4, 4, 5, 6, 7, 8, 9, 10, 11]

        await update_email_status(ids, read=True)

        script = _script(mock_jxa)
        expected = list(range(1, 11))  # first 10 unique, order-preserving
        assert f"const targetIds = {json.dumps(expected)};" in script
        assert "11" not in script.split("const targetIds")[1].split(";")[0]

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_uses_default_account_and_mailbox(
        self, mock_jxa, monkeypatch
    ):
        monkeypatch.setenv("APPLE_MAIL_DEFAULT_ACCOUNT", "Personal")
        monkeypatch.setenv("APPLE_MAIL_DEFAULT_MAILBOX", "Archive")
        _invalidate_config_cache()
        from apple_mail_mcp.server import update_email_status

        mock_jxa.return_value = []
        await update_email_status([1], read=True)

        script = _script(mock_jxa)
        assert 'MailCore.getAccount("Personal")' in script
        assert 'MailCore.getMailbox(account, "Archive")' in script


class TestUpdateEmailStatusErrors:
    """JXA failures are mapped to clean, model-friendly ValueErrors."""

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_unknown_mailbox_maps_to_value_error(self, mock_jxa):
        from apple_mail_mcp.server import update_email_status

        mock_jxa.side_effect = RuntimeError(
            "JXA script failed: Error: Can't get object. (-1728)"
        )

        with pytest.raises(ValueError, match="Mailbox 'Nope' not found") as ei:
            await update_email_status(
                [1], read=True, account="Work", mailbox="Nope"
            )
        assert "'Work'" in str(ei.value)

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_missing_message_maps_to_value_error(self, mock_jxa):
        from apple_mail_mcp.server import update_email_status

        mock_jxa.side_effect = RuntimeError(
            "JXA script failed: Error: Error: Message not found with ID: 42"
        )

        with pytest.raises(ValueError, match=r"^Message 42 not found\.$"):
            await update_email_status([1, 42], flagged=True)

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_other_errors_are_reraised(self, mock_jxa):
        from apple_mail_mcp.server import update_email_status

        mock_jxa.side_effect = RuntimeError("osascript timed out")

        with pytest.raises(RuntimeError, match="timed out"):
            await update_email_status([1], read=True)
