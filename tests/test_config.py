"""Tests for the layered config loader (env > file > default)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from apple_mail_mcp import config
from apple_mail_mcp.config import (
    CONFIG_SCHEMA_VERSION,
    CONFIG_TEMPLATE,
    ConfigError,
    _invalidate_config_cache,
    get_default_account,
    get_default_mailbox,
    get_index_exclude_mailboxes,
    get_index_max_emails,
    get_index_path,
    get_index_staleness_hours,
    get_read_only_mode,
    set_read_only_mode,
)


@pytest.fixture
def config_file(monkeypatch, tmp_path):
    """Redirect CONFIG_FILE_PATH to a per-test tempfile and reset the cache."""
    path = tmp_path / "config.toml"
    monkeypatch.setattr(config, "CONFIG_FILE_PATH", path)
    _invalidate_config_cache()
    yield path
    _invalidate_config_cache()


@pytest.fixture(autouse=True)
def clear_env(monkeypatch):
    """Clear every APPLE_MAIL_* var so env doesn't leak between tests."""
    for key in list(os_environ_keys()):
        if key.startswith("APPLE_MAIL_"):
            monkeypatch.delenv(key, raising=False)
    set_read_only_mode(False)
    yield
    set_read_only_mode(False)


def os_environ_keys():
    import os

    return os.environ.keys()


def _write(path: Path, body: str) -> None:
    path.write_text(body)
    _invalidate_config_cache()


class TestNoFile:
    """No config.toml present — helpers return their hardcoded defaults."""

    def test_default_account(self, config_file):
        assert get_default_account() is None

    def test_default_mailbox(self, config_file):
        assert get_default_mailbox() == "INBOX"

    def test_index_path(self, config_file):
        assert get_index_path() == config.DEFAULT_INDEX_PATH

    def test_index_max_emails(self, config_file):
        assert get_index_max_emails() is None

    def test_index_exclude_mailboxes(self, config_file):
        assert get_index_exclude_mailboxes() == {"Drafts"}

    def test_index_staleness_hours(self, config_file):
        assert get_index_staleness_hours() == 24.0

    def test_read_only(self, config_file):
        assert get_read_only_mode() is False


class TestFileOnly:
    """When only config.toml is present, file values are returned."""

    def test_default_account(self, config_file):
        _write(
            config_file,
            f"""
config_version = {CONFIG_SCHEMA_VERSION}
[defaults]
account = "Work"
""",
        )
        assert get_default_account() == "Work"

    def test_default_mailbox(self, config_file):
        _write(
            config_file,
            f"""
config_version = {CONFIG_SCHEMA_VERSION}
[defaults]
mailbox = "Archive"
""",
        )
        assert get_default_mailbox() == "Archive"

    def test_index_path_expands_user(self, config_file):
        _write(
            config_file,
            f"""
config_version = {CONFIG_SCHEMA_VERSION}
[index]
path = "~/custom/index.db"
""",
        )
        assert get_index_path() == Path("~/custom/index.db").expanduser()

    def test_index_max_emails(self, config_file):
        _write(
            config_file,
            f"""
config_version = {CONFIG_SCHEMA_VERSION}
[index]
max_emails = 5000
""",
        )
        assert get_index_max_emails() == 5000

    def test_exclude_mailboxes(self, config_file):
        _write(
            config_file,
            f"""
config_version = {CONFIG_SCHEMA_VERSION}
[index]
exclude_mailboxes = ["Drafts", "Junk"]
""",
        )
        assert get_index_exclude_mailboxes() == {"Drafts", "Junk"}

    def test_exclude_mailboxes_empty_list_is_explicit_empty(self, config_file):
        """Empty list in TOML means 'no exclusions', not 'use default'."""
        _write(
            config_file,
            f"""
config_version = {CONFIG_SCHEMA_VERSION}
[index]
exclude_mailboxes = []
""",
        )
        assert get_index_exclude_mailboxes() == set()

    def test_exclude_mailboxes_omitted_uses_default(self, config_file):
        """Omitting the key falls back to the {'Drafts'} default."""
        _write(
            config_file,
            f"""
config_version = {CONFIG_SCHEMA_VERSION}
[index]
max_emails = 1000
""",
        )
        assert get_index_exclude_mailboxes() == {"Drafts"}

    def test_staleness_int_is_coerced_to_float(self, config_file):
        _write(
            config_file,
            f"""
config_version = {CONFIG_SCHEMA_VERSION}
[index]
staleness_hours = 48
""",
        )
        result = get_index_staleness_hours()
        assert result == 48.0
        assert isinstance(result, float)

    def test_read_only(self, config_file):
        _write(
            config_file,
            f"""
config_version = {CONFIG_SCHEMA_VERSION}
[server]
read_only = true
""",
        )
        assert get_read_only_mode() is True


class TestEnvOverridesFile:
    """Env var wins over TOML file value."""

    def test_default_account(self, config_file, monkeypatch):
        _write(
            config_file,
            f"""
config_version = {CONFIG_SCHEMA_VERSION}
[defaults]
account = "FromFile"
""",
        )
        monkeypatch.setenv("APPLE_MAIL_DEFAULT_ACCOUNT", "FromEnv")
        assert get_default_account() == "FromEnv"

    def test_exclude_mailboxes(self, config_file, monkeypatch):
        _write(
            config_file,
            f"""
config_version = {CONFIG_SCHEMA_VERSION}
[index]
exclude_mailboxes = ["FileOne", "FileTwo"]
""",
        )
        monkeypatch.setenv(
            "APPLE_MAIL_INDEX_EXCLUDE_MAILBOXES", "EnvOne,EnvTwo"
        )
        assert get_index_exclude_mailboxes() == {"EnvOne", "EnvTwo"}

    def test_env_empty_string_means_explicit_empty(
        self, config_file, monkeypatch
    ):
        """Env set to '' overrides file with an explicit empty set."""
        _write(
            config_file,
            f"""
config_version = {CONFIG_SCHEMA_VERSION}
[index]
exclude_mailboxes = ["Drafts"]
""",
        )
        monkeypatch.setenv("APPLE_MAIL_INDEX_EXCLUDE_MAILBOXES", "")
        assert get_index_exclude_mailboxes() == set()

    def test_read_only_env_overrides_file(self, config_file, monkeypatch):
        _write(
            config_file,
            f"""
config_version = {CONFIG_SCHEMA_VERSION}
[server]
read_only = true
""",
        )
        monkeypatch.setenv("APPLE_MAIL_READ_ONLY", "false")
        assert get_read_only_mode() is False


class TestProgrammaticOverridesAll:
    """set_read_only_mode(True) forces on regardless of env or file."""

    def test_overrides_env_false_and_file_false(self, config_file, monkeypatch):
        _write(
            config_file,
            f"""
config_version = {CONFIG_SCHEMA_VERSION}
[server]
read_only = false
""",
        )
        monkeypatch.setenv("APPLE_MAIL_READ_ONLY", "false")
        set_read_only_mode(True)
        assert get_read_only_mode() is True


class TestValidationErrors:
    """Malformed config.toml fails loud with file-path context."""

    def test_malformed_toml_raises(self, config_file):
        _write(config_file, "this is = not valid = toml")
        with pytest.raises(ConfigError, match="TOML syntax error"):
            get_default_account()

    def test_missing_config_version_raises(self, config_file):
        _write(
            config_file,
            """
[defaults]
account = "Work"
""",
        )
        with pytest.raises(ConfigError, match="missing required key"):
            get_default_account()

    def test_unsupported_config_version_raises(self, config_file):
        _write(
            config_file,
            """
config_version = 99
[defaults]
account = "Work"
""",
        )
        with pytest.raises(ConfigError, match="unsupported"):
            get_default_account()

    def test_unknown_top_level_key_raises(self, config_file):
        _write(
            config_file,
            f"""
config_version = {CONFIG_SCHEMA_VERSION}
[mystery]
foo = "bar"
""",
        )
        with pytest.raises(ConfigError, match="unknown top-level key"):
            get_default_account()

    def test_unknown_section_key_raises(self, config_file):
        """Typos like `mailbox` vs `mailboxes` should fail loud."""
        _write(
            config_file,
            f"""
config_version = {CONFIG_SCHEMA_VERSION}
[defaults]
mailboxes = "INBOX"
""",
        )
        with pytest.raises(ConfigError, match="unknown key"):
            get_default_account()

    def test_wrong_type_raises(self, config_file):
        _write(
            config_file,
            f"""
config_version = {CONFIG_SCHEMA_VERSION}
[defaults]
mailbox = 123
""",
        )
        with pytest.raises(ConfigError, match="expected str"):
            get_default_account()

    def test_bool_for_int_field_raises(self, config_file):
        """bool subclasses int in Python — guard against silent acceptance."""
        _write(
            config_file,
            f"""
config_version = {CONFIG_SCHEMA_VERSION}
[index]
max_emails = true
""",
        )
        with pytest.raises(ConfigError, match="got bool"):
            get_default_account()

    def test_negative_max_emails_raises(self, config_file):
        _write(
            config_file,
            f"""
config_version = {CONFIG_SCHEMA_VERSION}
[index]
max_emails = -5
""",
        )
        with pytest.raises(ConfigError, match="must be >= 0"):
            get_default_account()

    def test_negative_staleness_raises(self, config_file):
        _write(
            config_file,
            f"""
config_version = {CONFIG_SCHEMA_VERSION}
[index]
staleness_hours = -1
""",
        )
        with pytest.raises(ConfigError, match="must be >= 0"):
            get_default_account()

    def test_list_with_non_string_element_raises(self, config_file):
        _write(
            config_file,
            f"""
config_version = {CONFIG_SCHEMA_VERSION}
[index]
exclude_mailboxes = ["Drafts", 42]
""",
        )
        with pytest.raises(ConfigError, match="expected str"):
            get_default_account()


class TestInitTemplate:
    """The CONFIG_TEMPLATE that `apple-mail-mcp init` writes."""

    def test_template_parses_as_valid_toml(self):
        """Sanity: the template is well-formed TOML."""
        tomllib.loads(CONFIG_TEMPLATE)

    def test_template_declares_current_schema_version(self):
        parsed = tomllib.loads(CONFIG_TEMPLATE)
        assert parsed.get("config_version") == CONFIG_SCHEMA_VERSION

    def test_template_passes_loader_validation(self, config_file):
        """Round-trip: the template must load cleanly through the validator.

        This catches drift between CONFIG_SCHEMA and CONFIG_TEMPLATE — if
        we ever add a key to the schema and document it in the template
        with a typo, this test fails before users hit it.
        """
        config_file.write_text(CONFIG_TEMPLATE)
        _invalidate_config_cache()
        # No exception means validation accepted the template.
        get_default_account()

    def test_template_keys_all_commented_out(self):
        """Every documented key must be commented so defaults stay in effect."""
        parsed = tomllib.loads(CONFIG_TEMPLATE)
        # The only top-level key that should parse is config_version.
        # Sections may exist as empty tables but have no live keys.
        for section in ("defaults", "index", "server"):
            section_data = parsed.get(section, {})
            assert section_data == {}, (
                f"Template's [{section}] section has live keys {section_data}; "
                f"all keys should be commented out so users opt in explicitly."
            )


class TestInitCommand:
    """Behavior tests for `apple-mail-mcp init`."""

    def test_writes_template_to_config_path(self, config_file):
        from apple_mail_mcp.cli import cli_init

        cli_init()
        assert config_file.exists()
        assert config_file.read_text() == CONFIG_TEMPLATE

    def test_sets_owner_only_permissions(self, config_file):
        """Match the project's 0o600 posture for sensitive files."""
        from apple_mail_mcp.cli import cli_init

        cli_init()
        mode = config_file.stat().st_mode & 0o777
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"

    def test_creates_parent_directory_if_missing(self, monkeypatch, tmp_path):
        new_dir = tmp_path / "fresh" / "subdir"
        target = new_dir / "config.toml"
        monkeypatch.setattr(config, "CONFIG_FILE_PATH", target)
        from apple_mail_mcp.cli import cli_init

        cli_init()
        assert target.exists()
        assert new_dir.is_dir()

    def test_refuses_to_overwrite_existing_file(self, config_file):
        config_file.write_text("# user-edited config")
        from apple_mail_mcp.cli import cli_init

        with pytest.raises(SystemExit) as exc:
            cli_init(force=False)
        assert exc.value.code == 1
        # File contents are preserved.
        assert config_file.read_text() == "# user-edited config"

    def test_force_overwrites_existing_file(self, config_file):
        config_file.write_text("# user-edited config")
        from apple_mail_mcp.cli import cli_init

        cli_init(force=True)
        assert config_file.read_text() == CONFIG_TEMPLATE


class TestCacheInvalidation:
    """The loader caches per-process; invalidation forces a re-read."""

    def test_file_change_picked_up_after_invalidation(self, config_file):
        _write(
            config_file,
            f"""
config_version = {CONFIG_SCHEMA_VERSION}
[defaults]
account = "First"
""",
        )
        assert get_default_account() == "First"

        config_file.write_text(
            f"""
config_version = {CONFIG_SCHEMA_VERSION}
[defaults]
account = "Second"
"""
        )
        # Without invalidation, the cache returns the old value.
        assert get_default_account() == "First"

        _invalidate_config_cache()
        assert get_default_account() == "Second"

    def test_missing_then_present(self, config_file):
        """Going from no-file to file-exists requires invalidation."""
        assert get_default_account() is None
        config_file.write_text(
            f"""
config_version = {CONFIG_SCHEMA_VERSION}
[defaults]
account = "NowSet"
"""
        )
        # Cached "no file" reading persists until invalidation.
        assert get_default_account() is None
        _invalidate_config_cache()
        assert get_default_account() == "NowSet"
