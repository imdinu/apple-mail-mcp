"""Configuration for Apple Mail MCP server.

Resolution order for every value (highest precedence first):
  1. Programmatic override (e.g. ``set_read_only_mode``, CLI flags).
  2. Environment variable (``APPLE_MAIL_*``).
  3. ``~/.apple-mail-mcp/config.toml`` (schema v1).
  4. Hardcoded default.
"""

import os
import tomllib
from pathlib import Path

# Default index location
DEFAULT_INDEX_PATH = Path.home() / ".apple-mail-mcp" / "index.db"
CONFIG_FILE_PATH = Path.home() / ".apple-mail-mcp" / "config.toml"

CONFIG_SCHEMA_VERSION = 1

# Single source of truth for what the TOML file may contain. Used by the
# loader for validation and by `apple-mail-mcp init` to generate the template.
# Maps `(section, key)` -> tuple of accepted Python types.
CONFIG_SCHEMA: dict[str, dict[str, tuple[type, ...]]] = {
    "defaults": {
        "account": (str,),
        "mailbox": (str,),
    },
    "index": {
        "path": (str,),
        "max_emails": (int,),
        "staleness_hours": (int, float),
        "exclude_mailboxes": (list,),
        "exclude_accounts": (list,),
        "include_mailboxes": (list,),
    },
    "server": {
        "read_only": (bool,),
    },
}


class ConfigError(RuntimeError):
    """Raised when ``~/.apple-mail-mcp/config.toml`` is malformed or invalid."""


# Cache state. The sentinel distinguishes "not yet loaded" from "loaded as {}".
_NOT_LOADED = object()
_cached_config: object = _NOT_LOADED


def _load_config_file() -> dict:
    """Parse and validate the on-disk TOML config.

    Returns an empty dict when the file does not exist. Raises
    :class:`ConfigError` on syntax errors, type mismatches, unknown keys,
    or schema-version mismatch.

    The parsed result is cached process-wide. Call
    :func:`_invalidate_config_cache` to force a re-read.
    """
    global _cached_config
    if _cached_config is not _NOT_LOADED:
        return _cached_config  # type: ignore[return-value]

    path = CONFIG_FILE_PATH
    if not path.exists():
        _cached_config = {}
        return {}

    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: TOML syntax error: {e}") from e

    _validate(data, path)
    _cached_config = data
    return data


def _validate(data: dict, path: Path) -> None:
    """Run schema validation. Raises :class:`ConfigError` on any violation."""
    version = data.get("config_version")
    if version is None:
        raise ConfigError(
            f"{path}: missing required key `config_version`. "
            f"Add `config_version = {CONFIG_SCHEMA_VERSION}` at the top."
        )
    if version != CONFIG_SCHEMA_VERSION:
        raise ConfigError(
            f"{path}: unsupported `config_version = {version!r}`. "
            f"This release expects `config_version = {CONFIG_SCHEMA_VERSION}`."
        )

    allowed_top = {"config_version"} | set(CONFIG_SCHEMA.keys())
    for key in data:
        if key not in allowed_top:
            raise ConfigError(
                f"{path}: unknown top-level key `{key}`. "
                f"Allowed: {sorted(allowed_top)}."
            )

    for section, allowed_keys in CONFIG_SCHEMA.items():
        section_data = data.get(section)
        if section_data is None:
            continue
        if not isinstance(section_data, dict):
            raise ConfigError(
                f"{path}: `[{section}]` must be a table, "
                f"got {type(section_data).__name__}."
            )
        for key, value in section_data.items():
            if key not in allowed_keys:
                raise ConfigError(
                    f"{path}: unknown key `[{section}] {key}`. "
                    f"Allowed in [{section}]: {sorted(allowed_keys)}."
                )
            expected_types = allowed_keys[key]
            # bool is a subclass of int — guard against silent acceptance.
            is_bool_in_int_slot = (
                int in expected_types
                and bool not in expected_types
                and isinstance(value, bool)
            )
            if is_bool_in_int_slot:
                expected = " | ".join(t.__name__ for t in expected_types)
                raise ConfigError(
                    f"{path}: `[{section}] {key}` expected {expected}, "
                    f"got bool."
                )
            if not isinstance(value, expected_types):
                expected = " | ".join(t.__name__ for t in expected_types)
                raise ConfigError(
                    f"{path}: `[{section}] {key}` expected {expected}, "
                    f"got {type(value).__name__}."
                )
            if isinstance(value, list):
                for i, item in enumerate(value):
                    if not isinstance(item, str):
                        raise ConfigError(
                            f"{path}: `[{section}] {key}[{i}]` expected str, "
                            f"got {type(item).__name__}."
                        )

    index = data.get("index", {})
    if "max_emails" in index and index["max_emails"] < 0:
        raise ConfigError(
            f"{path}: `[index] max_emails` must be >= 0, "
            f"got {index['max_emails']}."
        )
    if "staleness_hours" in index and index["staleness_hours"] < 0:
        raise ConfigError(
            f"{path}: `[index] staleness_hours` must be >= 0, "
            f"got {index['staleness_hours']}."
        )


def _invalidate_config_cache() -> None:
    """Reset the in-memory config cache. Used by tests and after `init`."""
    global _cached_config
    _cached_config = _NOT_LOADED


def _from_toml(*path: str):
    """Walk into the loaded config dict.

    Returns ``None`` if any segment is missing.
    """
    val: object = _load_config_file()
    for k in path:
        if not isinstance(val, dict) or k not in val:
            return None
        val = val[k]
    return val


# ========== Defaults ==========


def get_default_account() -> str | None:
    """
    Get the default account.

    Resolution: ``APPLE_MAIL_DEFAULT_ACCOUNT`` env, then ``[defaults] account``
    in ``config.toml``, then ``None`` (first account in Apple Mail is used).
    """
    env = os.environ.get("APPLE_MAIL_DEFAULT_ACCOUNT")
    if env:
        return env
    val = _from_toml("defaults", "account")
    if val:
        return val  # type: ignore[no-any-return]
    return None


def get_default_mailbox() -> str:
    """
    Get the default mailbox.

    Resolution: ``APPLE_MAIL_DEFAULT_MAILBOX`` env, then ``[defaults] mailbox``
    in ``config.toml``, then ``"INBOX"``.
    """
    env = os.environ.get("APPLE_MAIL_DEFAULT_MAILBOX")
    if env:
        return env
    val = _from_toml("defaults", "mailbox")
    if val:
        return val  # type: ignore[no-any-return]
    return "INBOX"


# ========== Index Configuration ==========


def get_index_path() -> Path:
    """
    Get the FTS5 index database path.

    Resolution: ``APPLE_MAIL_INDEX_PATH`` env, then ``[index] path``
    in ``config.toml``, then ``~/.apple-mail-mcp/index.db``.
    """
    env = os.environ.get("APPLE_MAIL_INDEX_PATH")
    if env:
        return Path(env).expanduser()
    val = _from_toml("index", "path")
    if val:
        return Path(val).expanduser()
    return DEFAULT_INDEX_PATH


def get_index_max_emails() -> int | None:
    """
    Get the maximum number of emails to index per mailbox.

    Resolution: ``APPLE_MAIL_INDEX_MAX_EMAILS`` env, then ``[index] max_emails``
    in ``config.toml``, then ``None`` (uncapped).
    """
    raw = os.environ.get("APPLE_MAIL_INDEX_MAX_EMAILS")
    if raw is not None and raw != "":
        return int(raw)
    val = _from_toml("index", "max_emails")
    if val is not None:
        return int(val)
    return None


def get_index_exclude_mailboxes() -> set[str]:
    """
    Get mailboxes to exclude from indexing.

    Resolution: ``APPLE_MAIL_INDEX_EXCLUDE_MAILBOXES`` env (CSV; empty string
    = no exclusions), then ``[index] exclude_mailboxes`` in ``config.toml``
    (empty list = no exclusions), then ``{"Drafts"}``.
    """
    env = os.environ.get("APPLE_MAIL_INDEX_EXCLUDE_MAILBOXES")
    if env is not None:
        return {m.strip() for m in env.split(",") if m.strip()}
    val = _from_toml("index", "exclude_mailboxes")
    if val is not None:
        return {m for m in val if m}
    return {"Drafts"}


def get_index_staleness_hours() -> float:
    """
    Get the staleness threshold for the index, in hours.

    Resolution: ``APPLE_MAIL_INDEX_STALENESS_HOURS`` env, then
    ``[index] staleness_hours`` in ``config.toml``, then ``24.0``.
    """
    env = os.environ.get("APPLE_MAIL_INDEX_STALENESS_HOURS")
    if env is not None and env != "":
        return float(env)
    val = _from_toml("index", "staleness_hours")
    if val is not None:
        return float(val)
    return 24.0


# ========== Server Mode ==========

_read_only_mode: bool = False


def get_read_only_mode() -> bool:
    """
    Check if write operations are disabled.

    Resolution: programmatic flag (set via :func:`set_read_only_mode`, e.g.
    by ``apple-mail-mcp serve -r``), then ``APPLE_MAIL_READ_ONLY`` env, then
    ``[server] read_only`` in ``config.toml``, then ``False``.
    """
    if _read_only_mode:
        return True
    env = os.environ.get("APPLE_MAIL_READ_ONLY")
    if env is not None:
        return env.lower() in ("1", "true", "yes")
    val = _from_toml("server", "read_only")
    if val is not None:
        return bool(val)
    return False


def set_read_only_mode(value: bool) -> None:
    """Enable or disable read-only mode programmatically."""
    global _read_only_mode
    _read_only_mode = value


# Template emitted by `apple-mail-mcp init`. Every key is commented out so
# the file is documentation that happens to be machine-readable: a new user
# sees the full surface and the matching env-var name on each line, then
# uncomments what they want. Forward-compat keys for #89 are included.
CONFIG_TEMPLATE = """\
# Apple Mail MCP — configuration
# https://github.com/imdinu/apple-mail-mcp
#
# Resolution order (highest precedence first):
#   1. CLI flags  (e.g. `apple-mail-mcp serve -r`)
#   2. Environment variables  (APPLE_MAIL_*)
#   3. This file
#   4. Built-in defaults
#
# All keys are optional. Uncomment to override the default.

config_version = 1


[defaults]

# Default account for tools that don't specify one. When unset, the first
# account in Apple Mail is used.
# Env: APPLE_MAIL_DEFAULT_ACCOUNT
# account = "Personal"

# Default mailbox for tools that don't specify one.
# Env: APPLE_MAIL_DEFAULT_MAILBOX
# mailbox = "INBOX"


[index]

# Path to the FTS5 search index database.
# Env: APPLE_MAIL_INDEX_PATH
# path = "~/.apple-mail-mcp/index.db"

# Maximum emails to index per mailbox. Omit for uncapped (default).
# Env: APPLE_MAIL_INDEX_MAX_EMAILS
# max_emails = 5000

# Hours before the index is considered stale and should be re-synced.
# Env: APPLE_MAIL_INDEX_STALENESS_HOURS
# staleness_hours = 24.0

# Mailboxes to skip during indexing.
# Empty list ([]) explicitly disables all exclusions.
# Env: APPLE_MAIL_INDEX_EXCLUDE_MAILBOXES (comma-separated)
# exclude_mailboxes = ["Drafts"]


[server]

# Disable write operations at MCP tool boundaries.
# Env: APPLE_MAIL_READ_ONLY
# CLI: apple-mail-mcp serve -r
# read_only = false
"""
