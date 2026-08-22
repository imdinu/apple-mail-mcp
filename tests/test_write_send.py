"""Tests for the send_email write tool (#22).

Safety model under test: DRAFT BY DEFAULT — the JXA script only calls
``msg.send()`` when ``confirm=True``; otherwise it calls ``msg.save()``.
All JXA is mocked; the generated script text is the contract.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from apple_mail_mcp import config
from apple_mail_mcp.config import _invalidate_config_cache, set_read_only_mode
from apple_mail_mcp.index.accounts import AccountMap
from apple_mail_mcp.server import (
    MAX_WRITE_BATCH,
    _validate_addresses,
    send_email,
)

JXA = "apple_mail_mcp.server.execute_with_core_async"


def _draft_result(**overrides) -> dict:
    base = {
        "status": "draft",
        "account": "iCloud",
        "to": ["a@example.com"],
        "cc": [],
        "bcc": [],
        "subject": "Hi",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Never read-only, never a hidden account, never the developer's
    real config.toml / env — each test starts from a clean slate."""
    AccountMap.get_instance().reset()
    monkeypatch.setattr(config, "CONFIG_FILE_PATH", tmp_path / "no.toml")
    monkeypatch.delenv("APPLE_MAIL_INDEX_EXCLUDE_ACCOUNTS", raising=False)
    monkeypatch.delenv("APPLE_MAIL_DEFAULT_ACCOUNT", raising=False)
    monkeypatch.delenv("APPLE_MAIL_READ_ONLY", raising=False)
    _invalidate_config_cache()
    set_read_only_mode(False)
    yield
    set_read_only_mode(False)
    AccountMap.get_instance().reset()


# ─── (a) read-only ───────────────────────────────────────────────


class TestReadOnly:
    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_read_only_raises_before_jxa(self, mock_jxa):
        set_read_only_mode(True)
        with pytest.raises(PermissionError, match="read-only"):
            await send_email(["a@example.com"], "Hi", "Body")
        mock_jxa.assert_not_called()

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_read_only_blocks_confirm_too(self, mock_jxa):
        set_read_only_mode(True)
        with pytest.raises(PermissionError):
            await send_email(["a@example.com"], "Hi", "Body", confirm=True)
        mock_jxa.assert_not_called()


# ─── (b) hidden account ──────────────────────────────────────────


class TestHiddenAccount:
    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_hidden_account_raises_no_jxa(self, mock_jxa, monkeypatch):
        monkeypatch.setenv("APPLE_MAIL_INDEX_EXCLUDE_ACCOUNTS", "PHI")
        with pytest.raises(ValueError, match="not found"):
            await send_email(["a@example.com"], "Hi", "Body", account="PHI")
        mock_jxa.assert_not_called()

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_hidden_account_with_confirm_never_sends(
        self, mock_jxa, monkeypatch
    ):
        monkeypatch.setenv("APPLE_MAIL_INDEX_EXCLUDE_ACCOUNTS", "PHI")
        with pytest.raises(ValueError):
            await send_email(
                ["a@example.com"], "Hi", "Body", account="PHI", confirm=True
            )
        mock_jxa.assert_not_called()


# ─── (c) validation ──────────────────────────────────────────────


class TestValidation:
    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_empty_to(self, mock_jxa):
        with pytest.raises(ValueError, match=r"^to "):
            await send_email([], "Hi", "Body")
        mock_jxa.assert_not_called()

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_blank_only_to_is_empty(self, mock_jxa):
        with pytest.raises(ValueError, match="at least one"):
            await send_email(["", "   "], "Hi", "Body")
        mock_jxa.assert_not_called()

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_address_without_at(self, mock_jxa):
        with pytest.raises(ValueError, match="to: invalid address"):
            await send_email(["nobody"], "Hi", "Body")
        mock_jxa.assert_not_called()

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_address_with_newline(self, mock_jxa):
        with pytest.raises(ValueError, match="to: invalid address"):
            await send_email(["a@example.com\nBcc: x@y.z"], "Hi", "Body")
        mock_jxa.assert_not_called()

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_display_name_form_rejected(self, mock_jxa):
        with pytest.raises(ValueError, match="to: invalid address"):
            await send_email(["Name <a@b.com>"], "Hi", "Body")
        mock_jxa.assert_not_called()

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_bad_cc_names_field(self, mock_jxa):
        with pytest.raises(ValueError, match="cc: invalid address"):
            await send_email(["a@b.com"], "Hi", "Body", cc=["@nodomain"])
        mock_jxa.assert_not_called()

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_bad_bcc_names_field(self, mock_jxa):
        with pytest.raises(ValueError, match="bcc: invalid address"):
            await send_email(["a@b.com"], "Hi", "Body", bcc=["nolocal@"])
        mock_jxa.assert_not_called()

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_non_string_address(self, mock_jxa):
        with pytest.raises(ValueError, match="to: addresses must be strings"):
            await send_email([123], "Hi", "Body")  # type: ignore[list-item]
        mock_jxa.assert_not_called()

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_too_many_recipients_raises_not_clamps(self, mock_jxa):
        to = [f"t{i}@example.com" for i in range(5)]
        cc = [f"c{i}@example.com" for i in range(3)]
        bcc = [f"b{i}@example.com" for i in range(3)]  # 11 total
        assert len(to) + len(cc) + len(bcc) == MAX_WRITE_BATCH + 1
        with pytest.raises(ValueError, match="Too many recipients"):
            await send_email(to, "Hi", "Body", cc=cc, bcc=bcc)
        mock_jxa.assert_not_called()

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_exactly_max_recipients_allowed(self, mock_jxa):
        to = [f"t{i}@example.com" for i in range(MAX_WRITE_BATCH)]
        mock_jxa.return_value = _draft_result(to=to)
        await send_email(to, "Hi", "Body")
        mock_jxa.assert_called_once()

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_subject_must_be_str(self, mock_jxa):
        with pytest.raises(ValueError, match="subject must be a string"):
            await send_email(["a@b.com"], None, "Body")  # type: ignore[arg-type]
        mock_jxa.assert_not_called()

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_body_must_be_str(self, mock_jxa):
        with pytest.raises(ValueError, match="body must be a string"):
            await send_email(["a@b.com"], "Hi", 42)  # type: ignore[arg-type]
        mock_jxa.assert_not_called()

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_empty_subject_and_body_allowed(self, mock_jxa):
        mock_jxa.return_value = _draft_result(subject="")
        result = await send_email(["a@example.com"], "", "")
        assert result["status"] == "draft"
        mock_jxa.assert_called_once()

    def test_validate_addresses_helper(self):
        assert _validate_addresses("to", None) == []
        assert _validate_addresses("to", [" a@b.com ", ""]) == ["a@b.com"]
        with pytest.raises(ValueError, match="to must be a list"):
            _validate_addresses("to", "a@b.com")  # type: ignore[arg-type]
        for bad in ("a@@b", "a b@c.com", "a@b,c@d", 'a"@b.com', "a\t@b"):
            with pytest.raises(ValueError, match="invalid address"):
                _validate_addresses("cc", [bad])


# ─── (d) draft by default / (e) confirm ──────────────────────────


class TestDraftVsSend:
    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_default_saves_draft_never_sends(self, mock_jxa):
        mock_jxa.return_value = _draft_result()
        result = await send_email(["a@example.com"], "Hi", "Body")

        script = mock_jxa.call_args.args[0]
        assert "msg.save()" in script
        assert "msg.send()" not in script
        assert json.dumps(["a@example.com"]) in script
        assert json.dumps("Hi") in script
        assert json.dumps("Body") in script
        assert ".send(" not in script  # no send path at all in a draft
        assert 'status: "draft"' in script
        assert "visible: false" in script  # no compose window pops up
        assert result["status"] == "draft"
        assert result["to"] == ["a@example.com"]

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_confirm_sends(self, mock_jxa):
        mock_jxa.return_value = _draft_result(status="sent")
        result = await send_email(["a@example.com"], "Hi", "Body", confirm=True)

        script = mock_jxa.call_args.args[0]
        assert "msg.send()" in script
        assert "msg.save()" not in script
        assert "visible: false" in script
        assert 'status: "sent"' in script
        assert result["status"] == "sent"

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_account_routed_through_getaccount(self, mock_jxa):
        mock_jxa.return_value = _draft_result(account="Work")
        await send_email(["a@example.com"], "Hi", "Body", account="Work")
        script = mock_jxa.call_args.args[0]
        assert 'MailCore.getAccount("Work")' in script
        assert "Mail.accounts()[0]" not in script

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_no_account_passes_null(self, mock_jxa):
        mock_jxa.return_value = _draft_result()
        await send_email(["a@example.com"], "Hi", "Body")
        script = mock_jxa.call_args.args[0]
        assert "MailCore.getAccount(null)" in script

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_unknown_account_maps_1728(self, mock_jxa):
        mock_jxa.side_effect = RuntimeError(
            "JXA script failed: Error: Can't get object. (-1728)"
        )
        with pytest.raises(ValueError, match="Account 'Nope' not found"):
            await send_email(["a@example.com"], "Hi", "Body", account="Nope")

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_timeout_is_explained_not_blank(self, mock_jxa):
        # TimeoutError stringifies to "" — seen live as "Error: " on a
        # send while Mail.app was busy syncing. Must name the state.
        mock_jxa.side_effect = TimeoutError()
        with pytest.raises(RuntimeError, match=r"did not respond.*sending"):
            await send_email(["a@example.com"], "Hi", "Body", confirm=True)
        mock_jxa.side_effect = TimeoutError()
        with pytest.raises(RuntimeError, match=r"did not respond.*saving"):
            await send_email(["a@example.com"], "Hi", "Body")

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_other_jxa_errors_reraised(self, mock_jxa):
        mock_jxa.side_effect = RuntimeError("Mail.app refused to send")
        with pytest.raises(RuntimeError, match="refused"):
            await send_email(["a@example.com"], "Hi", "Body", confirm=True)


# ─── (f) injection ───────────────────────────────────────────────


class TestInjection:
    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_subject_and_body_are_json_literals(self, mock_jxa):
        subject = '"); Mail.quit(); ("'
        body = "line1\nline2 `tick` ${x} </script>\r\n"
        mock_jxa.return_value = _draft_result(subject=subject)
        await send_email(["a@example.com"], subject, body)

        script = mock_jxa.call_args.args[0]
        assert json.dumps(subject) in script
        assert json.dumps(body) in script
        # The raw, unescaped payload must not appear as code.
        assert subject not in script
        assert body not in script
        assert "Mail.quit()" not in script.replace(json.dumps(subject), "")

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_account_name_is_json_literal(self, mock_jxa):
        account = 'x"); Mail.quit(); ("'
        mock_jxa.return_value = _draft_result(account=account)
        await send_email(["a@example.com"], "Hi", "Body", account=account)
        script = mock_jxa.call_args.args[0]
        assert f"MailCore.getAccount({json.dumps(account)})" in script


# ─── (g) cc/bcc defaults ─────────────────────────────────────────


class TestOptionalRecipients:
    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_none_cc_bcc_become_empty(self, mock_jxa):
        mock_jxa.return_value = _draft_result()
        result = await send_email(["a@example.com"], "Hi", "Body")

        script = mock_jxa.call_args.args[0]
        assert "for (const a of [])" in script
        assert script.count("for (const a of [])") == 2
        assert result["cc"] == []
        assert result["bcc"] == []

    @pytest.mark.asyncio
    @patch(JXA, new_callable=AsyncMock)
    async def test_cc_bcc_forwarded(self, mock_jxa):
        cc = ["c@example.com"]
        bcc = ["b1@example.com", "b2@example.com"]
        mock_jxa.return_value = _draft_result(cc=cc, bcc=bcc)
        result = await send_email(
            ["a@example.com"], "Hi", "Body", cc=cc, bcc=bcc
        )

        script = mock_jxa.call_args.args[0]
        assert json.dumps(cc) in script
        assert json.dumps(bcc) in script
        assert "Mail.CcRecipient" in script
        assert "Mail.BccRecipient" in script
        assert result["cc"] == cc
        assert result["bcc"] == bcc
