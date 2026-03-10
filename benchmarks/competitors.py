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

# Default account name for competitors that require it
BENCHMARK_ACCOUNT = "iCloud"


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
            "uv",
            "run",
            "apple-mail-mcp",
            "serve",
        ],
        tool_mapping={
            "list_accounts": ToolCall("list_accounts"),
            "get_emails": ToolCall("get_emails", {"limit": 50}),
            "get_email": ToolCall(
                "get_email", {"message_id": None}
            ),  # message_id discovered at runtime
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
            "get_email": ToolCall(
                "get_email",
                {"email_id": None},
            ),  # email_id discovered at runtime
            "search_subject": ToolCall(
                "search_email_content",
                {
                    "account": BENCHMARK_ACCOUNT,
                    "search_text": SEARCH_QUERY,
                },
            ),
            "search_body": ToolCall(
                "search_email_content",
                {
                    "account": BENCHMARK_ACCOUNT,
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

# 4. supermemoryai/apple-mcp (dhravya, archived Jan 2026)
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
                {
                    "operation": "search",
                    "searchTerm": SEARCH_QUERY,
                },
            ),
        },
        notes="Archived Jan 2026, historical baseline",
    )
)

# 5. s-morgan-jeffries/apple-mail-mcp (Python, FastMCP)
_register(
    Competitor(
        name="s-morgan-jeffries/apple-mail-mcp",
        key="smorgan",
        command=[
            f"{CACHE_DIR}/smorgan-apple-mail-mcp/.venv/bin/python",
            "-m",
            "apple_mail_mcp.server",
        ],
        cwd=f"{CACHE_DIR}/smorgan-apple-mail-mcp",
        tool_mapping={
            "get_emails": ToolCall(
                "search_messages",
                {
                    "account": BENCHMARK_ACCOUNT,
                    "limit": 50,
                },
            ),
            "search_subject": ToolCall(
                "search_messages",
                {
                    "account": BENCHMARK_ACCOUNT,
                    "subject_contains": SEARCH_QUERY,
                },
            ),
        },
        notes="No list_accounts or body search",
    )
)

# 6. attilagyorffy/apple-mail-mcp (Go, single binary)
_register(
    Competitor(
        name="attilagyorffy/apple-mail-mcp",
        key="attilagyorffy",
        command=[
            f"{CACHE_DIR}/attilagyorffy-apple-mail-mcp/bin/apple-mail-mcp",
        ],
        tool_mapping={
            "get_emails": ToolCall(
                "search_messages",
                {
                    "account": BENCHMARK_ACCOUNT,
                    "limit": 50,
                },
            ),
            "search_subject": ToolCall(
                "search_messages",
                {
                    "account": BENCHMARK_ACCOUNT,
                    "subject_contains": SEARCH_QUERY,
                },
            ),
        },
        notes="Go binary, no list_accounts or body search",
    )
)
