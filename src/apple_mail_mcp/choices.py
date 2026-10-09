"""Argument choices shared by the MCP tools and the CLI.

server.py annotates its tool parameters with these and cli.py its
options, so the two surfaces accept the same values. Keep this module
free of heavy imports: cli.py loads it at import time and must not
pull in server.py or fastmcp to parse arguments.
"""

from typing import Literal

EmailFilter = Literal[
    "all", "unread", "flagged", "today", "last_7_days", "this_week"
]
"""get_emails() filter; "this_week" is an alias for "last_7_days"."""

SearchScope = Literal["all", "subject", "sender", "body", "attachments"]
"""search() scope."""
