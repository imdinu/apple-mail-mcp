"""Command-line interface for apple-mail-mcp.

Provides commands for:
- index: Build search index from disk (requires Full Disk Access)
- status: Show index statistics
- rebuild: Force rebuild the index
- serve: Run the MCP server (default)

Usage:
    apple-mail-mcp            # Run MCP server (default)
    apple-mail-mcp serve      # Run MCP server explicitly
    apple-mail-mcp --watch    # Run with real-time index updates
    apple-mail-mcp index      # Build index from disk
    apple-mail-mcp status     # Show index status
    apple-mail-mcp rebuild    # Force rebuild index
"""

import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, TypeVar

import cyclopts

from .config import get_index_path

T = TypeVar("T")


def _run_optionally_profiled(
    op: Callable[[], T], profile_path: Path | None
) -> T:
    """Run `op()` directly, or wrap it in cProfile if a path is given.

    cProfile is stdlib — no extra dependencies. Profile the whole
    callable so the dump captures end-to-end cost (walk, parse,
    SQL inserts, FTS rebuild). Documented in docs/profiling.md.
    """
    if profile_path is None:
        return op()
    import cProfile

    result_holder: list[T] = []

    def runner() -> None:
        result_holder.append(op())

    cProfile.runctx("runner()", globals(), locals(), str(profile_path))
    return result_holder[0]


app = cyclopts.App(
    name="apple-mail-mcp",
    help="Fast MCP server for Apple Mail with FTS5 search index.",
)


def _format_size(size_mb: float) -> str:
    """Format file size for display."""
    if size_mb < 1:
        return f"{size_mb * 1024:.1f} KB"
    return f"{size_mb:.1f} MB"


def _format_time(seconds: float) -> str:
    """Format duration for display."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}m {secs:.1f}s"


def _progress_bar(current: int, total: int | None, width: int = 40) -> str:
    """Create a progress bar string."""
    if total is None or total == 0:
        # Indeterminate progress
        return f"[{'=' * (current % width)}>]"

    pct = min(current / total, 1.0)
    filled = int(width * pct)
    bar = "=" * filled + "-" * (width - filled)
    return f"[{bar}] {pct * 100:.0f}%"


def _run_serve(watch: bool = False, read_only: bool = False) -> None:
    """Internal function to run the MCP server."""
    import threading

    from .config import set_read_only_mode
    from .index import IndexManager
    from .server import mcp

    if read_only:
        set_read_only_mode(True)
        print("Read-only mode enabled", file=sys.stderr)

    manager = IndexManager.get_instance()

    # Clean up old attachment files
    try:
        from .server import _cleanup_old_attachments

        _cleanup_old_attachments()
    except Exception:
        pass

    if manager.has_index():

        def _background_sync() -> None:
            try:
                start = time.time()
                count = manager.sync_updates()
                elapsed = time.time() - start
                if count > 0:
                    print(
                        f"Background sync: {count} changes "
                        f"({_format_time(elapsed)})",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"Index up to date ({_format_time(elapsed)})",
                        file=sys.stderr,
                    )
            except Exception as e:
                print(
                    f"Warning: Background sync failed: {e}",
                    file=sys.stderr,
                )

            # Start watcher only after sync completes
            if watch:
                try:

                    def on_update(added: int, removed: int) -> None:
                        if added or removed:
                            print(
                                f"Index updated: +{added} -{removed}",
                                file=sys.stderr,
                            )

                    if manager.start_watcher(on_update=on_update):
                        print("File watcher started", file=sys.stderr)
                except Exception as e:
                    print(
                        f"Warning: File watcher failed: {e}",
                        file=sys.stderr,
                    )

        sync_thread = threading.Thread(target=_background_sync, daemon=True)
        sync_thread.start()
        print(
            "Syncing index in background...",
            file=sys.stderr,
            flush=True,
        )

    mcp.run()


@app.command
def serve(
    watch: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--watch", "-w"],
            help="Watch for new emails and update index in real-time",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--verbose", "-v"],
            help="Enable verbose output",
        ),
    ] = False,
    read_only: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--read-only", "-r"],
            help="Disable write operations (v0.3.0+)",
        ),
    ] = False,
) -> None:
    """
    Run the MCP server.

    This is the default command when no subcommand is specified.
    The server provides email search and access tools to MCP clients.

    At startup, the index is automatically synced with disk (fast, <5s).
    Use --watch to enable real-time index updates when emails arrive.
    Requires Full Disk Access for the terminal.
    """
    _run_serve(watch=watch, read_only=read_only)


@app.command
def index(
    verbose: Annotated[
        bool,
        cyclopts.Parameter(name=["--verbose", "-v"], help="Show progress"),
    ] = False,
    profile: Annotated[
        Path | None,
        cyclopts.Parameter(
            name=["--profile"],
            help="Write a cProfile dump to this path for performance analysis",
        ),
    ] = None,
) -> None:
    """
    Build the search index from disk.

    Reads .emlx files directly from ~/Library/Mail/V10/ for fast indexing.
    This is much faster than fetching via JXA (~30x faster).

    IMPORTANT: Requires Full Disk Access permission for Terminal.
    Grant access in System Settings → Privacy & Security → Full Disk Access.
    """
    from .index import IndexManager

    print("Building search index from disk...")
    print(f"Index location: {get_index_path()}")
    if profile:
        print(f"Profiling: writing cProfile dump to {profile}")
    print()

    manager = IndexManager()
    start = time.time()
    last_report = start

    def progress(current: int, total: int | None, message: str) -> None:
        nonlocal last_report
        now = time.time()

        # Throttle updates to avoid spam
        if now - last_report < 0.5 and total is None:
            return
        last_report = now

        if verbose:
            if total:
                bar = _progress_bar(current, total)
                print(f"\r{bar} {message}", end="", flush=True)
            else:
                print(f"\r{message}", end="", flush=True)

    try:
        callback = progress if verbose else None
        count = _run_optionally_profiled(
            lambda: manager.build_from_disk(progress_callback=callback),
            profile_path=profile,
        )
        elapsed = time.time() - start

        if verbose:
            print()  # Newline after progress

        print()
        print(f"✓ Indexed {count:,} emails in {_format_time(elapsed)}")

        stats = manager.get_stats()
        print(f"  Mailboxes: {stats.mailbox_count}")
        print(f"  Database size: {_format_size(stats.db_size_mb)}")

    except PermissionError as e:
        print(f"\n✗ Permission denied: {e}", file=sys.stderr)
        print("\nTo fix this:", file=sys.stderr)
        print("  1. Open System Settings", file=sys.stderr)
        print("  2. Privacy & Security → Full Disk Access", file=sys.stderr)
        print("  3. Add and enable Terminal.app", file=sys.stderr)
        print("  4. Restart terminal and try again", file=sys.stderr)
        sys.exit(1)

    except FileNotFoundError as e:
        print(f"\n✗ Not found: {e}", file=sys.stderr)
        sys.exit(1)

    except Exception as e:
        print(f"\n✗ Error: {e}", file=sys.stderr)
        sys.exit(1)


@app.command
def status(
    verbose: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--verbose", "-v"],
            help="Enable verbose output",
        ),
    ] = False,
) -> None:
    """
    Show index statistics.

    Displays:
    - Email count and mailbox count
    - Last sync time and staleness
    - Database file size
    """
    from .index import IndexManager

    manager = IndexManager()

    if not manager.has_index():
        print("No index found.")
        print(f"Expected location: {get_index_path()}")
        print()
        print("Run 'apple-mail-mcp index' to build the index.")
        sys.exit(1)

    stats = manager.get_stats()

    print("Apple Mail MCP Index Status")
    print("=" * 40)
    print(f"Location:     {get_index_path()}")
    print(f"Emails:       {stats.email_count:,}")
    if stats.disk_email_count is not None:
        coverage = (
            stats.email_count / stats.disk_email_count * 100
            if stats.disk_email_count > 0
            else 0
        )
        print(
            f"On disk:      {stats.disk_email_count:,}"
            f" ({coverage:.0f}% indexed)"
        )
    print(f"Attachments:  {stats.attachment_count:,}")
    print(f"Mailboxes:    {stats.mailbox_count}")
    print(f"Database:     {_format_size(stats.db_size_mb)}")
    if stats.failed_jobs_count > 0:
        print(f"Failed parse: {stats.failed_jobs_count:,} (.emlx files in DLQ)")
    if stats.capped_mailboxes > 0:
        print(
            f"Capped:       {stats.capped_mailboxes} mailbox(es) at"
            " APPLE_MAIL_INDEX_MAX_EMAILS — raise or unset to index more"
        )
    print()

    if stats.last_sync:
        print(f"Last sync:    {stats.last_sync.strftime('%Y-%m-%d %H:%M:%S')}")
        if stats.staleness_hours is not None:
            if stats.staleness_hours < 1:
                staleness = f"{stats.staleness_hours * 60:.0f} minutes ago"
            elif stats.staleness_hours < 24:
                staleness = f"{stats.staleness_hours:.1f} hours ago"
            else:
                staleness = f"{stats.staleness_hours / 24:.1f} days ago"
            print(f"Staleness:    {staleness}")

            if manager.is_stale():
                print()
                print(
                    "⚠ Index is stale. Run 'apple-mail-mcp index' to refresh."
                )
    else:
        print("Last sync:    Never")
        print()
        print("⚠ No sync recorded. Run 'apple-mail-mcp index' to build.")


@app.command
def rebuild(
    account: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--account", "-a"],
            help="Rebuild only this account (all if not specified)",
        ),
    ] = None,
    mailbox: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--mailbox", "-m"],
            help="Rebuild only this mailbox (requires --account)",
        ),
    ] = None,
    verbose: Annotated[
        bool,
        cyclopts.Parameter(name=["--verbose", "-v"], help="Show progress"),
    ] = False,
    profile: Annotated[
        Path | None,
        cyclopts.Parameter(
            name=["--profile"],
            help="Write a cProfile dump to this path for performance analysis",
        ),
    ] = None,
) -> None:
    """
    Force rebuild the search index.

    Clears existing data and rebuilds from disk.
    Optionally scope to a specific account or mailbox.
    """
    if mailbox and not account:
        print("Error: --mailbox requires --account", file=sys.stderr)
        sys.exit(1)

    from .index import IndexManager

    scope = "entire index"
    if account and mailbox:
        scope = f"{account}/{mailbox}"
    elif account:
        scope = f"account {account}"

    print(f"Rebuilding {scope}...")
    if profile:
        print(f"Profiling: writing cProfile dump to {profile}")

    manager = IndexManager()
    start = time.time()

    def progress(current: int, total: int | None, message: str) -> None:
        if verbose:
            print(f"\r{message}", end="", flush=True)

    try:
        count = _run_optionally_profiled(
            lambda: manager.rebuild(
                account=account,
                mailbox=mailbox,
                progress_callback=progress if verbose else None,
            ),
            profile_path=profile,
        )
        elapsed = time.time() - start

        if verbose:
            print()

        print(f"✓ Rebuilt {count:,} emails in {_format_time(elapsed)}")

    except Exception as e:
        print(f"\n✗ Error: {e}", file=sys.stderr)
        sys.exit(1)


@app.default
def default_handler(
    watch: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--watch", "-w"],
            help="Watch for new emails and update index in real-time",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--verbose", "-v"],
            help="Enable verbose output",
        ),
    ] = False,
    read_only: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--read-only", "-r"],
            help="Disable write operations (v0.3.0+)",
        ),
    ] = False,
) -> None:
    """Run the MCP server (default when no command specified)."""
    _run_serve(watch=watch, read_only=read_only)


# ========== Integration Generator ==========

integrate_app = cyclopts.App(
    name="integrate",
    help="Generate integration files for AI tools.",
)
app.command(integrate_app)


_CLAUDE_SKILL = """\
---
name: mail
description: Search and read Apple Mail emails via CLI
---

Use `apple-mail-mcp` CLI to access emails on this Mac.

## Search emails

```
!apple-mail-mcp search "keyword" --limit 20
```

Options:
- `--scope` / `-s`: all (default), subject, sender, body, attachments
- `--account` / `-a`: filter to a specific email account
- `--after`: only emails on/after this date (YYYY-MM-DD)
- `--before`: only emails before this date (YYYY-MM-DD)
- `--limit` / `-n`: max results (default: 20)
- `--no-highlight`: disable **term** markers in results

Examples:
```
!apple-mail-mcp search "quarterly report" --scope subject
!apple-mail-mcp search "invoice" --after 2026-01-01 --before 2026-04-01
!apple-mail-mcp search "Kim Foulds" --account Work
```

## Read full email

```
!apple-mail-mcp read <message_id>
```

Use the `id` from search results. Returns full content, attachments list, \
and metadata.

## List emails

```
!apple-mail-mcp emails --filter unread --limit 10
```

Filters: all, unread, flagged, today, last_7_days

## List accounts and mailboxes

```
!apple-mail-mcp accounts
!apple-mail-mcp mailboxes --account Work
```

## Extract attachment or links

```
!apple-mail-mcp extract <message_id> <filename>
!apple-mail-mcp extract <message_id>  # links mode
```

## Output format

All commands return JSON. Use jq for filtering:
```
!apple-mail-mcp search "budget" | jq '.[].subject'
```
"""


@integrate_app.command
def claude() -> None:
    """Generate a Claude Code skill file.

    Outputs markdown to stdout. Pipe to a file:

        apple-mail-mcp integrate claude \\
            > ~/.claude/skills/apple-mail.md
    """
    print(_CLAUDE_SKILL)


# ========== CLI Wrappers for MCP Tools ==========


def _run_async(coro):
    """Run an async MCP tool function synchronously."""
    import asyncio

    return asyncio.run(coro)


def _print_json(data):
    """Print data as formatted JSON to stdout."""
    import json

    print(json.dumps(data, indent=2, ensure_ascii=False))


@app.command(name="search")
def cli_search(
    query: str,
    scope: Annotated[
        str,
        cyclopts.Parameter(
            name=["--scope", "-s"],
            help="all, subject, sender, body, attachments",
        ),
    ] = "all",
    account: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--account", "-a"],
            help="Filter to specific account",
        ),
    ] = None,
    mailbox: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--mailbox", "-m"],
            help="Filter to specific mailbox",
        ),
    ] = None,
    limit: Annotated[
        int,
        cyclopts.Parameter(name=["--limit", "-n"], help="Max results"),
    ] = 20,
    offset: Annotated[
        int,
        cyclopts.Parameter(name="--offset", help="Skip first N results"),
    ] = 0,
    before: Annotated[
        str | None,
        cyclopts.Parameter(help="Before date (YYYY-MM-DD)"),
    ] = None,
    after: Annotated[
        str | None,
        cyclopts.Parameter(help="After date (YYYY-MM-DD)"),
    ] = None,
    highlight: Annotated[
        bool,
        cyclopts.Parameter(help="Highlight matched terms"),
    ] = True,
) -> None:
    """Search emails using the FTS5 index."""
    from .server import search as _search

    try:
        result = _run_async(
            _search(
                query,
                account=account,
                mailbox=mailbox,
                scope=scope,
                limit=limit,
                offset=offset,
                before=before,
                after=after,
                highlight=highlight,
            )
        )
        _print_json(result)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


@app.command(name="read")
def cli_read(
    message_id: int,
    account: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--account", "-a"],
            help="Account name (speeds up lookup)",
        ),
    ] = None,
    mailbox: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--mailbox", "-m"],
            help="Mailbox name (speeds up lookup)",
        ),
    ] = None,
) -> None:
    """Read a single email with full content."""
    from .server import get_email

    try:
        result = _run_async(
            get_email(message_id, account=account, mailbox=mailbox)
        )
        _print_json(result)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


@app.command(name="emails")
def cli_emails(
    account: Annotated[
        str | None,
        cyclopts.Parameter(name=["--account", "-a"], help="Account name"),
    ] = None,
    mailbox: Annotated[
        str | None,
        cyclopts.Parameter(name=["--mailbox", "-m"], help="Mailbox name"),
    ] = None,
    filter: Annotated[
        str,
        cyclopts.Parameter(
            name=["--filter", "-f"],
            help="all, unread, flagged, today, last_7_days",
        ),
    ] = "all",
    limit: Annotated[
        int,
        cyclopts.Parameter(name=["--limit", "-n"], help="Max results"),
    ] = 50,
) -> None:
    """List emails from a mailbox."""
    from .server import get_emails

    try:
        result = _run_async(
            get_emails(account, mailbox, filter=filter, limit=limit)
        )
        _print_json(result)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


@app.command(name="accounts")
def cli_accounts() -> None:
    """List all email accounts."""
    from .server import list_accounts

    try:
        result = _run_async(list_accounts())
        _print_json(result)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


@app.command(name="mailboxes")
def cli_mailboxes(
    account: Annotated[
        str | None,
        cyclopts.Parameter(name=["--account", "-a"], help="Account name"),
    ] = None,
) -> None:
    """List mailboxes for an account."""
    from .server import list_mailboxes

    try:
        result = _run_async(list_mailboxes(account))
        _print_json(result)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


@app.command(name="extract")
def cli_extract(
    message_id: int,
    filename: Annotated[
        str | None,
        cyclopts.Parameter(
            help="Attachment filename (omit for links)",
        ),
    ] = None,
    account: Annotated[
        str | None,
        cyclopts.Parameter(name=["--account", "-a"], help="Account name"),
    ] = None,
    mailbox: Annotated[
        str | None,
        cyclopts.Parameter(name=["--mailbox", "-m"], help="Mailbox name"),
    ] = None,
) -> None:
    """Extract attachment or links from an email."""
    from .server import get_email_attachment, get_email_links

    try:
        if filename:
            result = _run_async(
                get_email_attachment(
                    message_id, filename, account=account, mailbox=mailbox
                )
            )
        else:
            result = _run_async(
                get_email_links(message_id, account=account, mailbox=mailbox)
            )
        _print_json(result)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    """Entry point for the CLI."""
    app()
