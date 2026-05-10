"""Tests for the --profile flag wiring in cli.py (#issue-followup-from-#60).

The flag wraps `index` / `rebuild` operations in cProfile when set.
We test the helper directly rather than the full CLI command — the
helper *is* the new behavior; the CLI surface around it is unchanged.
"""

from __future__ import annotations

import pstats
from pathlib import Path

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
