"""Tests for the --profile flag wiring in cli.py (#issue-followup-from-#60).

The flag wraps `index` / `rebuild` operations in cProfile when set.
We test the helper directly rather than the full CLI command — the
helper *is* the new behavior; the CLI surface around it is unchanged.
"""

from __future__ import annotations

import pstats
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from apple_mail_mcp.cli import _run_optionally_profiled


def test_profile_path_none_runs_op_without_profiling(tmp_path: Path) -> None:
    calls = []

    def op() -> str:
        calls.append("ran")
        return "result"

    out = _run_optionally_profiled(op, profile_path=None)
    assert out == "result"
    assert calls == ["ran"]


def test_profile_path_set_writes_pstats_dump(tmp_path: Path) -> None:
    profile_file = tmp_path / "out.prof"
    calls = []

    def op() -> int:
        calls.append("ran")
        # Do something measurable so the dump has content.
        return sum(range(10_000))

    out = _run_optionally_profiled(op, profile_path=profile_file)

    assert out == sum(range(10_000))
    assert calls == ["ran"]
    assert profile_file.exists(), "profile dump should be written to disk"
    assert profile_file.stat().st_size > 0, "profile dump should not be empty"

    # cProfile dumps are marshal-encoded; pstats.Stats is the
    # canonical loader. Just verifying it parses without error
    # confirms the dump is valid.
    stats = pstats.Stats(str(profile_file))
    assert stats.total_calls > 0


def test_profile_propagates_op_return_value(tmp_path: Path) -> None:
    # Regression: the cProfile path uses a list-holder pattern to
    # capture the return value across cProfile's exec scope. Verify
    # that propagation doesn't drop or mutate the value.
    profile_file = tmp_path / "out.prof"

    def op() -> dict:
        return {"k": "v", "n": 42}

    out = _run_optionally_profiled(op, profile_path=profile_file)
    assert out == {"k": "v", "n": 42}


class TestNonBlockingStartup:
    """_run_serve should not block on sync."""

    def test_run_serve_does_not_block(self, tmp_path):
        """mcp.run() is called immediately, not after sync."""
        mock_manager = MagicMock()
        mock_manager.has_index.return_value = True
        # Real path so the IndexLock (#106) can create its lockfile.
        mock_manager.db_path = tmp_path / "index.db"
        # sync_updates sleeps to simulate slow sync
        mock_manager.sync_updates.side_effect = lambda: (time.sleep(5) or 0)

        mock_mcp = MagicMock()

        with (
            patch(
                "apple_mail_mcp.index.IndexManager.get_instance",
                return_value=mock_manager,
            ),
            patch("apple_mail_mcp.server.mcp", mock_mcp),
            patch("apple_mail_mcp.server._cleanup_old_attachments"),
        ):
            from apple_mail_mcp.cli import _run_serve

            start = time.time()
            _run_serve(watch=False)
            elapsed = time.time() - start

            # mcp.run() should be called within ~1s, not 5s
            assert elapsed < 2.0
            mock_mcp.run.assert_called_once()

    def test_search_does_not_trigger_sync(self):
        """Verify _sync_lock and auto-sync were removed."""
        import apple_mail_mcp.server as srv

        assert not hasattr(srv, "_sync_lock")


class TestSyncBlockedWarning:
    """Startup must not report a blocked sync as a healthy one (#110)."""

    def setup_method(self):
        import apple_mail_mcp.cli as cli

        cli._sync_blocked_warned = False

    def test_permission_block_names_full_disk_access(self, capsys):
        from apple_mail_mcp.cli import _warn_sync_blocked

        _warn_sync_blocked("permission")

        err = capsys.readouterr().err
        assert "NOT up to date" in err
        assert "Full Disk Access" in err

    def test_missing_mail_directory_does_not_blame_permissions(self, capsys):
        from apple_mail_mcp.cli import _warn_sync_blocked

        _warn_sync_blocked("missing")

        err = capsys.readouterr().err
        assert "not found" in err
        assert "Full Disk Access" not in err

    def test_warning_is_printed_once_per_process(self, capsys):
        """A promoted writer re-runs the same sync (#106)."""
        from apple_mail_mcp.cli import _warn_sync_blocked

        _warn_sync_blocked("permission")
        first = capsys.readouterr().err
        _warn_sync_blocked("permission")
        second = capsys.readouterr().err

        assert "Full Disk Access" in first
        assert second == ""


class TestServeWithoutIndex:
    """Serving with no index at all is legal but must be visible."""

    def test_startup_says_body_search_is_unavailable(self, tmp_path, capsys):
        mock_manager = MagicMock()
        mock_manager.has_index.return_value = False
        mock_manager.db_path = tmp_path / "index.db"

        mock_mcp = MagicMock()

        with (
            patch(
                "apple_mail_mcp.index.IndexManager.get_instance",
                return_value=mock_manager,
            ),
            patch("apple_mail_mcp.server.mcp", mock_mcp),
            patch("apple_mail_mcp.server._cleanup_old_attachments"),
        ):
            from apple_mail_mcp.cli import _run_serve

            _run_serve(watch=False)

        err = capsys.readouterr().err
        assert "No search index found" in err
        assert "apple-mail-mcp index" in err
        mock_mcp.run.assert_called_once()


class TestNoIndexLockAcquisition:
    """A server with no index still takes the writer lock (#106).

    Opportunistic index writes (stale-entry cleanup, DLQ records)
    create the database file on first use, so an unlocked no-index
    server could materialize an empty index concurrently with a
    legitimate `apple-mail-mcp index` build.
    """

    def _run(self, tmp_path, on_run):
        mock_manager = MagicMock()
        mock_manager.has_index.return_value = False
        mock_manager.db_path = tmp_path / "index.db"
        mock_manager.index_writer = True

        mock_mcp = MagicMock()
        mock_mcp.run.side_effect = on_run

        with (
            patch(
                "apple_mail_mcp.index.IndexManager.get_instance",
                return_value=mock_manager,
            ),
            patch("apple_mail_mcp.server.mcp", mock_mcp),
            patch("apple_mail_mcp.server._cleanup_old_attachments"),
        ):
            from apple_mail_mcp.cli import _run_serve

            _run_serve(watch=False)
        return mock_manager

    def test_no_index_server_holds_the_lock(self, tmp_path):
        """While serving, a contender cannot take the lock."""
        from apple_mail_mcp.index.lock import IndexLock

        state = {}

        def probe():
            contender = IndexLock(tmp_path / "index.db")
            state["contended"] = not contender.try_acquire()
            contender.release()

        manager = self._run(tmp_path, probe)
        assert state["contended"] is True
        assert manager.index_writer is True

    def test_no_index_server_goes_passive_when_lock_held(self, tmp_path):
        """Lock already taken (an index build, another server): the
        no-index server must not stay a writer."""
        from apple_mail_mcp.index.lock import IndexLock

        holder = IndexLock(tmp_path / "index.db")
        assert holder.try_acquire() is True
        state = {}

        def probe():
            state["index_writer"] = state["manager"].index_writer

        try:
            mock_manager = MagicMock()
            mock_manager.has_index.return_value = False
            mock_manager.db_path = tmp_path / "index.db"
            mock_manager.index_writer = True
            state["manager"] = mock_manager

            mock_mcp = MagicMock()
            mock_mcp.run.side_effect = probe

            with (
                patch(
                    "apple_mail_mcp.index.IndexManager.get_instance",
                    return_value=mock_manager,
                ),
                patch("apple_mail_mcp.server.mcp", mock_mcp),
                patch("apple_mail_mcp.server._cleanup_old_attachments"),
            ):
                from apple_mail_mcp.cli import _run_serve

                _run_serve(watch=False)
        finally:
            holder.release()

        assert state["index_writer"] is False
