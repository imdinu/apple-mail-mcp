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
            # patrickfreyer's search_emails takes `subject_keyword`
            # (not `subject`) and `body_text` (not `body`); the wrong
            # names get silently dropped by FastMCP and the call
            # becomes a no-op listing of the 20 most recent INBOX
            # emails. Also: default mailbox is "INBOX" only — pass
            # "All" for fair coverage parity with the index-based
            # competitors. Body search at this scale exhausts the
            # 180s osascript timeout; the harness records that as
            # SKIPPED (matrix shows TIMEOUT), which is the honest
            # signal.
            "search_subject": ToolCall(
                "search_emails",
                {
                    "account": BENCHMARK_ACCOUNT,
                    "mailbox": "All",
                    "subject_keyword": SEARCH_QUERY,
                },
            ),
            "search_body": ToolCall(
                "search_emails",
                {
                    "account": BENCHMARK_ACCOUNT,
                    "mailbox": "All",
                    "body_text": SEARCH_QUERY,
                },
            ),
        },
    )
)

# 3. (slot vacated — s-morgan-jeffries demoted 2026-05-28.
#     Project is non-functional on macOS 26: both `get_emails` and
#     `search_subject` raise AppleScript errors -1726 and -1728 against
#     the new Mail.app object model. See docs/benchmarks.md "Also noted"
#     for the full demotion rationale. Re-promote if upstream updates
#     the AppleScript idioms.)

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
                "mail_search_by_subject", {"subject_text": SEARCH_QUERY}
            ),
            # pl-lyfx exposes a `mail_search` tool but it is not body
            # search: it LIKE-scans the `subject` and `sender` columns
            # of the Envelope Index `messages` table, which are integer
            # foreign-key rowids into `subjects` / `sender_addresses` —
            # any text query matches nothing. Verified by hand probe
            # (returns "No messages found" for "meeting"). Not mapped
            # to search_body to avoid a misleading no-op bar.
        },
        notes=(
            "Single-file Python, reads Envelope Index SQLite directly. "
            "No get_emails-list or get_email-by-id surface. No actual "
            "body search either — `mail_search` is misleadingly named. "
            "Defaults work for the three benchmarked scenarios; only "
            "PRIMARY_EMAIL_ADDRESS is a placeholder, consumed solely "
            "by tools we don't benchmark."
        ),
    )
)

# 8. titouancreach/apple-mail-mcp (Haskell, AppleScript-backed)
_register(
    Competitor(
        name="titouancreach/apple-mail-mcp",
        key="titouancreach",
        command=[
            f"{CACHE_DIR}/titouancreach-apple-mail-mcp/apple-mail-mcp.hs",
        ],
        tool_mapping={
            "list_accounts": ToolCall("mail", {"operation": "accounts"}),
            # get_emails (`operation=latest`) and search_subject
            # (`operation=search`) consistently exceed the probe
            # threshold by orders of magnitude on this mailbox
            # (25s and 54min wall-clock in successive runs before
            # the harness SKIPs). The underlying AppleScript walks
            # every message of every mailbox — not viable at 73K
            # scale regardless of how patient the harness is.
            # Treated as not-supported to keep run time bounded.
        },
        notes=(
            "Haskell, single 'mail' tool with operation params. "
            "AppleScript-backed under the hood. Cabal single-file "
            "script with `#!/usr/bin/env cabal` shebang — first "
            "invocation downloads + compiles deps (~1.5 min one-"
            "time), subsequent spawns use cabal's script-build "
            "cache (~90ms steady-state). Only cold_start and "
            "list_accounts are benchmarked; the AppleScript "
            "backend cannot complete get_emails or search_subject "
            "on a 73K mailbox within probe budget."
        ),
    )
)
