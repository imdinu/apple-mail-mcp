"""Configuration for Apple Mail MCP server."""

import os
from pathlib import Path

# Default index location
DEFAULT_INDEX_PATH = Path.home() / ".apple-mail-mcp" / "index.db"


def get_default_account() -> str | None:
    """
    Get the default account from environment variable.

    Set APPLE_MAIL_DEFAULT_ACCOUNT to use a specific account by default.
    If not set, the first account in Apple Mail will be used.

    Returns:
        Account name or None to use first account.
    """
    return os.environ.get("APPLE_MAIL_DEFAULT_ACCOUNT")


def get_default_mailbox() -> str:
    """
    Get the default mailbox from environment variable.

    Set APPLE_MAIL_DEFAULT_MAILBOX to use a specific mailbox by default.
    Defaults to "INBOX".

    Returns:
        Mailbox name.
    """
    return os.environ.get("APPLE_MAIL_DEFAULT_MAILBOX", "INBOX")


# ========== Index Configuration ==========


def _parse_csv_env(name: str) -> set[str]:
    """Parse a comma-separated environment variable into non-empty values."""
    raw = os.environ.get(name)
    if raw is None:
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def get_index_path() -> Path:
    """
    Get the FTS5 index database path.

    Set APPLE_MAIL_INDEX_PATH to customize the location.
    Defaults to ~/.apple-mail-mcp/index.db

    Returns:
        Path to the index database file.
    """
    env_path = os.environ.get("APPLE_MAIL_INDEX_PATH")
    if env_path:
        return Path(env_path).expanduser()
    return DEFAULT_INDEX_PATH


def get_index_max_emails() -> int | None:
    """
    Get the maximum number of emails to index per mailbox.

    Set APPLE_MAIL_INDEX_MAX_EMAILS to opt in to a per-mailbox ceiling.
    Defaults to None (uncapped) — the index covers the entire mailbox.

    Returns:
        Maximum emails per mailbox, or None for no cap.
    """
    raw = os.environ.get("APPLE_MAIL_INDEX_MAX_EMAILS")
    if raw is None or raw == "":
        return None
    return int(raw)


def get_index_exclude_mailboxes() -> set[str]:
    """
    Get mailboxes to exclude from indexing.

    Set APPLE_MAIL_INDEX_EXCLUDE_MAILBOXES to a comma-separated list.
    Defaults to "Drafts".

    Returns:
        Set of mailbox names to exclude.
    """
    env_val = os.environ.get("APPLE_MAIL_INDEX_EXCLUDE_MAILBOXES")
    if env_val is not None:
        return {m.strip() for m in env_val.split(",") if m.strip()}
    return {"Drafts"}


def get_index_include_mailboxes() -> set[str] | None:
    """
    Get the mailbox allow-list for indexing.

    Set APPLE_MAIL_INDEX_INCLUDE_MAILBOXES to a comma-separated list of
    mailbox names. When unset, all non-excluded mailboxes are indexed.

    Returns:
        Set of mailbox names to include, or None to include all.
    """
    values = _parse_csv_env("APPLE_MAIL_INDEX_INCLUDE_MAILBOXES")
    return values or None


def get_index_exclude_accounts() -> set[str]:
    """
    Get accounts to exclude from indexing.

    Set APPLE_MAIL_INDEX_EXCLUDE_ACCOUNTS to a comma-separated list of
    account names or account UUIDs. UUIDs are matched directly against
    Mail.app account directory names; friendly names are resolved when
    Apple Mail account metadata is available.

    Returns:
        Set of account names or UUIDs to exclude.
    """
    return _parse_csv_env("APPLE_MAIL_INDEX_EXCLUDE_ACCOUNTS")


def get_index_staleness_hours() -> float:
    """
    Get the staleness threshold for the index.

    After this many hours without a sync, the index is considered stale
    and should be refreshed.

    Set APPLE_MAIL_INDEX_STALENESS_HOURS to customize.
    Defaults to 24 hours.

    Returns:
        Staleness threshold in hours.
    """
    return float(os.environ.get("APPLE_MAIL_INDEX_STALENESS_HOURS", "24"))


# ========== Server Mode ==========

_read_only_mode: bool = False


def get_read_only_mode() -> bool:
    """
    Check if write operations are disabled.

    Set ``APPLE_MAIL_READ_ONLY`` environment variable or call
    :func:`set_read_only_mode` to enable.  When enabled, write
    tools (v0.3.0+) will refuse to execute.

    Returns:
        True if read-only mode is active.
    """
    if _read_only_mode:
        return True
    return os.environ.get("APPLE_MAIL_READ_ONLY", "").lower() in (
        "1",
        "true",
        "yes",
    )


def set_read_only_mode(value: bool) -> None:
    """Enable or disable read-only mode programmatically."""
    global _read_only_mode
    _read_only_mode = value
