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
            f"{CACHE_DIR}/patrickfreyer-apple-mail-mcp"
            "/.venv/bin/mcp-apple-mail",
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
                "search_emails",
                {
                    "account": BENCHMARK_ACCOUNT,
                    "subject": SEARCH_QUERY,
                },
            ),
            "search_body": ToolCall(
                "search_emails",
                {
                    "account": BENCHMARK_ACCOUNT,
                    "body": SEARCH_QUERY,
                },
            ),
        },
    )
)

# 3. s-morgan-jeffries/apple-mail-mcp (Python, FastMCP)
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

# 4. like-a-freedom/rusty_apple_mail_mcp (Rust, reads Envelope Index)
_register(
    Competitor(
        name="rusty_apple_mail_mcp",
        key="rusty",
        command=[
            f"{CACHE_DIR}/rusty-apple-mail-mcp"
            "/target/release/rusty_apple_mail_mcp",
        ],
        tool_mapping={
            "list_accounts": ToolCall(
                "list_accounts", {"include_mailboxes": False}
            ),
            "get_emails": ToolCall(
                "search_messages",
                {"mailbox": "INBOX", "limit": 50},
            ),
            "get_email": ToolCall(
                "get_message", {"message_id": None}
            ),  # message_id is a string, not int
            "search_subject": ToolCall(
                "search_messages",
                {"subject_query": SEARCH_QUERY, "limit": 50},
            ),
        },
        notes="Rust binary, reads Apple Envelope Index directly",
    )
)

# 5. sweetrb/apple-mail-mcp (TypeScript, npm, AppleScript)
_register(
    Competitor(
        name="sweetrb/apple-mail-mcp",
        key="sweetrb",
        command=[
            "node",
            f"{CACHE_DIR}/sweetrb-apple-mail-mcp/build/index.js",
        ],
        tool_mapping={
            "list_accounts": ToolCall("list-accounts"),
            "get_emails": ToolCall(
                "list-messages",
                {"limit": 50},
            ),
            "get_email": ToolCall(
                "get-message",
                {"id": None},
            ),  # id discovered at runtime
            "search_subject": ToolCall(
                "search-messages",
                {"subject": SEARCH_QUERY, "limit": 50},
            ),
        },
        notes=(
            "TypeScript/AppleScript, 40+ tools, mail-merge. "
            "No body search — query filter is subject/sender only."
        ),
    )
)

# 6. BastianZim/apple-mail-mcp (Python, no AppleScript, SQLite + .emlx)
_register(
    Competitor(
        name="BastianZim/apple-mail-mcp",
        key="bastianzim",
        command=[
            f"{CACHE_DIR}/bastianzim-apple-mail-mcp/.venv/bin/python",
            "-m",
            "apple_mail_mcp.server",
        ],
        cwd=f"{CACHE_DIR}/bastianzim-apple-mail-mcp",
        tool_mapping={
            "list_accounts": ToolCall("list_accounts"),
            "get_emails": ToolCall(
                "search_emails",
                {"limit": 50},
            ),
            "get_email": ToolCall(
                "read_email",
                {"message_id": None},
            ),  # message_id discovered at runtime
            "search_subject": ToolCall(
                "search_emails",
                {"subject": SEARCH_QUERY, "limit": 50},
            ),
            "search_body": ToolCall(
                "search_emails",
                {"body": SEARCH_QUERY, "limit": 50},
            ),
        },
        notes=(
            "Reads Envelope Index SQLite + .emlx directly, no AppleScript. "
            "No FTS5 — body search live-scans up to 5000 .emlx files. "
            "Closest head-to-head for the indexing thesis."
        ),
    )
)

# 7. pl-lyfx/apple-mail-mcp (Python single-file, Envelope Index direct)
_register(
    Competitor(
        name="pl-lyfx/apple-mail-mcp",
        key="pl-lyfx",
        command=[
            "python3",
            f"{CACHE_DIR}/pl-lyfx-apple-mail-mcp/apple_mail_mcp.py",
        ],
        cwd=f"{CACHE_DIR}/pl-lyfx-apple-mail-mcp",
        tool_mapping={
            "list_accounts": ToolCall("mail_list_accounts"),
            "search_subject": ToolCall(
                "mail_search_by_subject", {"query": SEARCH_QUERY}
            ),
            "search_body": ToolCall("mail_search", {"query": SEARCH_QUERY}),
        },
        notes=(
            "Single-file Python, reads Envelope Index SQLite directly. "
            "No get_emails-list or get_email-by-id surface. "
            "Script hardcodes MAIL_DIR / EMAIL / MAIL_VERSION constants — "
            "edit top of apple_mail_mcp.py before benchmarking."
        ),
    )
)

# 8. titouancreach/apple-mail-mcp (Haskell, AppleScript-backed)
_register(
    Competitor(
        name="titouancreach/apple-mail-mcp",
        key="titouancreach",
        command=[
            f"{CACHE_DIR}/titouancreach-bin/apple-mail-mcp",
        ],
        tool_mapping={
            "list_accounts": ToolCall("mail", {"operation": "accounts"}),
            "get_emails": ToolCall(
                "mail", {"operation": "latest", "limit": 50}
            ),
            "search_subject": ToolCall(
                "mail",
                {"operation": "search", "searchTerm": SEARCH_QUERY},
            ),
        },
        notes=(
            "Haskell, single 'mail' tool with operation params. "
            "AppleScript-backed under the hood. "
            "Pre-compiled to a native binary via `cabal install` for "
            "fair cold-start comparison with Rust/Go entrants."
        ),
    )
)
