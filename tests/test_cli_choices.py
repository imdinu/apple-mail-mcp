"""CLI option choices match the MCP tool parameters they forward to.

`emails --filter` and `search --scope` are Literal-typed, so cyclopts
rejects anything outside the set at parse time. Both surfaces take
their choices from apple_mail_mcp.choices; these tests fail if either
side stops doing so.
"""

from __future__ import annotations

from typing import get_args, get_type_hints
from unittest.mock import AsyncMock, patch

import pytest
from cyclopts.exceptions import CycloptsError

from apple_mail_mcp import cli, server


def _cli_choices(func, param: str) -> tuple[str, ...]:
    # Annotated[Literal[...], Parameter(...)] -> the Literal's values.
    hint = get_type_hints(func, include_extras=True)[param]
    return get_args(get_args(hint)[0])


def _server_choices(func, param: str) -> tuple[str, ...]:
    return get_args(get_type_hints(func)[param])


def test_emails_filter_choices_match_server() -> None:
    assert _cli_choices(cli.cli_emails, "filter") == _server_choices(
        server.get_emails, "filter"
    )


def test_search_scope_choices_match_server() -> None:
    assert _cli_choices(cli.cli_search, "scope") == _server_choices(
        server.search, "scope"
    )


def test_emails_accepts_this_week() -> None:
    _, bound, _ = cli.app.parse_args(
        ["emails", "--filter", "this_week"],
        exit_on_error=False,
        print_error=False,
    )
    assert bound.arguments["filter"] == "this_week"


def test_emails_rejects_unknown_filter() -> None:
    with pytest.raises(CycloptsError):
        cli.app.parse_args(
            ["emails", "--filter", "yesterday"],
            exit_on_error=False,
            print_error=False,
        )


def test_emails_forwards_this_week_to_server(capsys) -> None:
    mock = AsyncMock(return_value=[])
    with patch("apple_mail_mcp.server.get_emails", mock):
        cli.app(
            ["emails", "--filter", "this_week"],
            exit_on_error=False,
            result_action="return_value",
        )
    assert capsys.readouterr().out.strip() == "[]"
    assert mock.await_args is not None
    assert mock.await_args.kwargs["filter"] == "this_week"
