"""Competitor definitions for the benchmarking suite.

Each competitor is defined as a dict with:
- name: display name
- key: short identifier
- command: list[str] to spawn the MCP server
- tool_mapping: maps standard operations to (tool_name, arguments) pairs
- supported_ops: set of operations this competitor supports
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

CACHE_DIR = os.path.expanduser("~/.cache/apple-mail-mcp-bench")

# Default search query used across all benchmarks
SEARCH_QUERY = "meeting"


@dataclass
class ToolCall:
    """A tool invocation: name + JSON arguments."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class Competitor:
    """A competitor MCP server to benchmark."""

    name: str
    key: str
    command: list[str]
    tool_mapping: dict[str, ToolCall]
    cwd: str | None = None
    is_ours: bool = False
    notes: str = ""

    @property
    def supported_ops(self) -> set[str]:
        return set(self.tool_mapping.keys())


# ─── AppleScript payloads for generic executors ───────────────

APPLESCRIPT_LIST_ACCOUNTS = (
    'tell application "Mail" to get name of every account'
)
APPLESCRIPT_GET_EMAILS = """\
tell application "Mail"
    set msgs to messages 1 thru 50 of inbox
    set results to {}
    repeat with m in msgs
        set end of results to \u00ac
            {subject of m, sender of m, date received of m}
    end repeat
    return results
end tell"""
APPLESCRIPT_SEARCH_SUBJECT = """\
tell application "Mail"
    set results to {}
    set msgs to (every message of inbox \u00ac
        whose subject contains "meeting")
    repeat with m in msgs
        set end of results to {subject of m, sender of m}
    end repeat
    return results
end tell"""

# ─── Competitor definitions ───────────────────────────────────

COMPETITORS: dict[str, Competitor] = {}


def _register(c: Competitor) -> None:
    COMPETITORS[c.key] = c


# 1. imdinu/apple-mail-mcp (ours)
_register(
    Competitor(
        name="apple-mail-mcp (ours)",
        key="imdinu",
        command=[
            "uvx",
            "--prerelease=allow",
            "apple-mail-mcp",
            "serve",
        ],
        tool_mapping={
            "list_accounts": ToolCall("list_accounts"),
            "get_emails": ToolCall("get_emails", {"limit": 50}),
            "search_subject": ToolCall(
                "search",
                {"query": SEARCH_QUERY, "scope": "subject"},
            ),
            "search_body": ToolCall(
                "search",
                {"query": SEARCH_QUERY, "scope": "body"},
            ),
        },
        is_ours=True,
    )
)

# 2. patrickfreyer/apple-mail-mcp
_register(
    Competitor(
        name="patrickfreyer/apple-mail-mcp",
        key="patrickfreyer",
        command=[
            f"{CACHE_DIR}/patrickfreyer-apple-mail-mcp/.venv/bin/python",
            "apple_mail_mcp.py",
        ],
        cwd=f"{CACHE_DIR}/patrickfreyer-apple-mail-mcp",
        tool_mapping={
            "list_accounts": ToolCall("list_accounts"),
            "get_emails": ToolCall("list_inbox_emails", {"max_emails": 50}),
            "search_subject": ToolCall(
                "search_email_content",
                {"account": "iCloud", "search_text": SEARCH_QUERY},
            ),
            "search_body": ToolCall(
                "search_email_content",
                {
                    "account": "iCloud",
                    "search_text": SEARCH_QUERY,
                    "search_subject": False,
                    "search_body": True,
                },
            ),
        },
    )
)

# 3. kiki830621/che-apple-mail-mcp (Swift)
_register(
    Competitor(
        name="kiki830621/che-apple-mail-mcp",
        key="che-apple-mail",
        command=[
            f"{CACHE_DIR}/che-apple-mail-mcp/.build/release/CheAppleMailMCP",
        ],
        tool_mapping={
            "list_accounts": ToolCall("list_accounts"),
            "get_emails": ToolCall("list_emails", {"limit": 50}),
            "search_subject": ToolCall(
                "search_emails", {"query": SEARCH_QUERY}
            ),
        },
    )
)

# 4. fatbobman/mail-mcp-bridge
_register(
    Competitor(
        name="fatbobman/mail-mcp-bridge",
        key="fatbobman",
        command=[
            f"{CACHE_DIR}/mail-mcp-bridge/.venv/bin/python3",
            "src/mail_mcp_server.py",
        ],
        cwd=f"{CACHE_DIR}/mail-mcp-bridge",
        tool_mapping={},
        notes="Bridge server - path-based ops only, cold start only",
    )
)

# 5. supermemoryai/apple-mcp (dhravya, archived)
_register(
    Competitor(
        name="dhravya/apple-mcp",
        key="dhravya",
        command=["npx", "apple-mcp@latest"],
        tool_mapping={
            "list_accounts": ToolCall(
                "mail",
                {"operation": "accounts"},
            ),
            "get_emails": ToolCall(
                "mail",
                {"operation": "unread"},
            ),
            "search_subject": ToolCall(
                "mail",
                {"operation": "search", "searchTerm": SEARCH_QUERY},
            ),
        },
        notes="Archived, may fail",
    )
)

# 6. steipete/macos-automator-mcp
_register(
    Competitor(
        name="steipete/macos-automator-mcp",
        key="steipete",
        command=[
            "npx",
            "@steipete/macos-automator-mcp",
        ],
        tool_mapping={
            "list_accounts": ToolCall(
                "execute_script",
                {
                    "script_content": APPLESCRIPT_LIST_ACCOUNTS,
                    "language": "applescript",
                },
            ),
            "get_emails": ToolCall(
                "execute_script",
                {
                    "script_content": APPLESCRIPT_GET_EMAILS,
                    "language": "applescript",
                },
            ),
            "search_subject": ToolCall(
                "execute_script",
                {
                    "script_content": APPLESCRIPT_SEARCH_SUBJECT,
                    "language": "applescript",
                },
            ),
        },
        notes="Generic executor - uses AppleScript payload",
    )
)

# 7. PeakMojo/applescript-mcp
_register(
    Competitor(
        name="PeakMojo/applescript-mcp",
        key="peakmojo",
        command=["npx", "@peakmojo/applescript-mcp"],
        tool_mapping={
            "list_accounts": ToolCall(
                "applescript_execute",
                {"code_snippet": APPLESCRIPT_LIST_ACCOUNTS},
            ),
            "get_emails": ToolCall(
                "applescript_execute",
                {"code_snippet": APPLESCRIPT_GET_EMAILS},
            ),
            "search_subject": ToolCall(
                "applescript_execute",
                {"code_snippet": APPLESCRIPT_SEARCH_SUBJECT},
            ),
        },
        notes="Generic executor - uses AppleScript payload",
    )
)

# 8. 54yyyu/pyapple-mcp
_register(
    Competitor(
        name="54yyyu/pyapple-mcp",
        key="pyapple",
        command=[
            f"{CACHE_DIR}/pyapple-mcp/.venv/bin/pyapple-mcp",
        ],
        tool_mapping={
            "list_accounts": ToolCall(
                "mail",
                {"operation": "accounts"},
            ),
            "get_emails": ToolCall(
                "mail",
                {"operation": "unread"},
            ),
            "search_subject": ToolCall(
                "mail",
                {"operation": "search", "query": SEARCH_QUERY},
            ),
        },
        notes="Multi-app MCP server",
    )
)
