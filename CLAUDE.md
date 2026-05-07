# Apple Mail MCP - Project Instructions

## Project Overview

The only Apple Mail MCP server with full-coverage FTS5 body search. Reliable on large mailboxes (tested at ~72K messages) where AppleScript-based servers timeout, and the only one whose body search has no recency cap. Disk-first email reads (~3ms via .emlx parsing), batch JXA property fetching, and an FTS5 search index for full-text body search (~20ms).

## Project Structure

```
src/apple_mail_mcp/
├── __init__.py         # CLI entry point, exports main()
├── cli.py              # CLI commands (index, status, rebuild, serve)
├── server.py           # FastMCP server with 8 MCP tools
├── config.py           # Environment variable configuration
├── builders.py         # QueryBuilder, AccountsQueryBuilder
├── executor.py         # run_jxa(), execute_with_core(), execute_query()
├── index/              # FTS5 search index module
│   ├── __init__.py     # Exports IndexManager
│   ├── schema.py       # SQLite schema v5 (DLQ + attachments)
│   ├── manager.py      # IndexManager class (disk-based sync)
│   ├── disk.py         # .emlx reading + get_disk_inventory()
│   ├── sync.py         # Disk-based state reconciliation
│   ├── search.py       # FTS5 search functions
│   └── watcher.py      # Real-time file watcher
└── jxa/
    ├── __init__.py     # Exports MAIL_CORE_JS
    └── mail_core.js    # Shared JXA utilities (MailCore object)
```

## MCP Tools (8 total)

| Tool | Purpose | Key Parameters |
|------|---------|----------------|
| `list_accounts()` | List email accounts | - |
| `list_mailboxes(account?)` | List mailboxes | account (optional) |
| `get_emails(...)` | Unified listing | filter: all/unread/flagged/today/last_7_days |
| `get_email(id)` | Full email content + attachments | message_id |
| `search(query, ...)` | Unified search | scope, before, after, offset, highlight |
| `get_email_links(id)` | Extract links from an email | message_id |
| `get_email_attachment(id, filename)` | Extract attachment content | message_id, filename |
| `get_attachment(id, filename)` | *Deprecated* — use `get_email_attachment()` | message_id, filename |

## MCP Resources (1 total)

| URI | Purpose | MIME |
|-----|---------|------|
| `index://status` | Read-only JSON snapshot of FTS5 index health: `email_count`, `mailbox_count`, `attachment_count`, `disk_email_count`, `db_size_mb`, `capped_mailboxes`, `failed_jobs_count`, `last_sync`, `staleness_hours`. Lets MCP clients assess index state without invoking a tool. | `application/json` |

### get_emails() Filters

```python
get_emails()                      # All emails (default)
get_emails(filter="unread")       # Unread only
get_emails(filter="flagged")      # Flagged only
get_emails(filter="today")        # Received today
get_emails(filter="last_7_days")  # Last 7 days
```

### search() Scopes

```python
search("invoice")                          # Search everywhere (FTS5)
search("john@", scope="sender")            # Sender only (JXA)
search("meeting", scope="subject")         # Subject only (JXA)
search("deadline", scope="body")           # Body only (FTS5)
search("pdf", scope="attachments")         # By attachment filename (SQL)
search("invoice", after="2025-01-01")      # Date-range filtering
search("meeting", highlight=True)          # Highlighted results
search("meeting", limit=20, offset=20)    # Page 2 of results
```

## Architecture

### Disk-First Sync

**Problem:** JXA-based sync was timing out at 60s for large mailboxes.

**Solution:** State reconciliation via filesystem scanning:

```
Startup Sync Flow:
1. Get DB inventory: {(account, mailbox, msg_id): emlx_path}  ← from SQLite
2. Get Disk inventory: {(account, mailbox, msg_id): emlx_path}  ← fast walk
3. Calculate diff:
   - NEW: on disk, not in DB → parse & insert
   - DELETED: in DB, not on disk → remove from DB
   - MOVED: same ID, different path → update path
```

**Performance:**

| Operation | JXA (old) | Disk (new) | Speedup |
|-----------|-----------|------------|---------|
| Startup sync | 60s timeout | <5s | **12x** |
| Handles deletions | No | Yes | - |
| Handles moves | No | Yes | - |

### Layer Separation

1. **cli.py** - CLI entry point, commands for indexing
2. **server.py** - 8 MCP tools, uses builders and index
3. **builders.py** - Constructs JXA scripts from Python, type-safe
4. **executor.py** - Runs scripts via osascript, handles JSON parsing
5. **index/** - FTS5 search index with disk-based sync
6. **jxa/mail_core.js** - Shared JS utilities injected into all scripts

### Data Flow (JXA Path)

```
MCP Tool → QueryBuilder.build() → executor.execute_query()
                                        ↓
                           MAIL_CORE_JS + script body
                                        ↓
                              osascript -l JavaScript
                                        ↓
                              JSON.parse(stdout)
```

### Data Flow (Disk Sync)

```
Server startup → IndexManager.sync_updates()
                        ↓
         sync.sync_from_disk(conn, mail_dir)
                        ↓
    disk.get_disk_inventory() → walk filesystem
    sync.get_db_inventory()   → query SQLite
                        ↓
              Calculate diff: NEW, DELETED, MOVED
                        ↓
    NEW → parse_emlx() → INSERT
    DELETED → DELETE from DB
    MOVED → UPDATE emlx_path
```

### Hybrid Access Pattern

| Access Method | Use Case | Latency | When Used |
|---------------|----------|---------|-----------|
| **Disk (Single)** | Read single email by ID | ~1-5ms | `get_email()` Strategy 0 |
| **JXA (Live)** | Real-time ops, small queries | ~100-300ms | `get_email()` Strategies 1-3, `list_mailboxes()` |
| **FTS5 (Cached)** | Body search, complex filtering | ~2-10ms | `search()` |
| **Disk (Batch)** | Indexing, sync | ~15ms/100 emails | startup, `apple-mail-mcp index` |

### get_email() Strategy Cascade

```
Strategy 0: Disk read (.emlx)     ← fastest, requires index
    ↓ fail
Strategy 1: JXA specified mailbox ← uses account + mailbox params
    ↓ fail
Strategy 2: Index lookup + JXA   ← finds mailbox via SQLite, then JXA
    ↓ fail
Strategy 3: Iterate all mailboxes ← slowest, always works (with timeout)
```

All strategies return identical response schema. Strategy 0 extracts read/flagged
from plist footer flags bitmask (bit 0 = read, bit 4 = flagged) and date_sent,
reply_to, message_id from MIME headers.

### Design Patterns

| Pattern | Location | Purpose |
|---------|----------|---------|
| **Builder** | `QueryBuilder` | Safe JXA script construction, prevents injection |
| **Singleton** | `IndexManager` | Single SQLite writer, one file watcher |
| **Facade** | `MailCore` JS | Clean API over verbose Apple Events |
| **Factory** | `create_connection()` | Consistent DB configuration |
| **State Reconciliation** | `sync_from_disk()` | Fast diff-based sync |

## FTS5 Search Index

### Database Schema (v5)

```sql
-- Email content cache
CREATE TABLE emails (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,     -- Mail.app ID (per-mailbox only)
    account TEXT NOT NULL,
    mailbox TEXT NOT NULL,
    subject TEXT,
    sender TEXT,
    content TEXT,                    -- Body text
    date_received TEXT,
    emlx_path TEXT,                  -- Path for sync
    attachment_count INTEGER DEFAULT 0,
    indexed_at TEXT DEFAULT (datetime('now')),
    UNIQUE(account, mailbox, message_id)
);

CREATE INDEX idx_emails_path ON emails(emlx_path);

-- Attachment metadata (one-to-many from emails)
CREATE TABLE attachments (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    email_rowid INTEGER NOT NULL REFERENCES emails(rowid) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    mime_type TEXT,
    file_size INTEGER,
    content_id TEXT
);
CREATE INDEX idx_attachments_email ON attachments(email_rowid);
CREATE INDEX idx_attachments_filename ON attachments(filename);

-- FTS5 index (external content - shares storage with emails table)
CREATE VIRTUAL TABLE emails_fts USING fts5(
    subject, sender, content,
    content='emails',
    content_rowid='rowid',
    tokenize='porter unicode61'
);

-- Triggers keep FTS in sync automatically
-- Sync state tracking per mailbox
CREATE TABLE sync_state (
    account TEXT NOT NULL,
    mailbox TEXT NOT NULL,
    last_sync TEXT,
    message_count INTEGER DEFAULT 0,
    PRIMARY KEY(account, mailbox)
);

-- Dead letter queue for `.emlx` parse failures (added v5).
-- Populated by the watcher and disk-sync paths so operators can
-- audit which messages are missing from the index. Cleared
-- automatically on a successful re-parse of the same path.
CREATE TABLE failed_index_jobs (
    emlx_path TEXT PRIMARY KEY,
    account TEXT NOT NULL,
    mailbox TEXT NOT NULL,
    error_type TEXT NOT NULL,
    error_message TEXT NOT NULL,
    first_seen TEXT DEFAULT (datetime('now')),
    last_seen TEXT DEFAULT (datetime('now')),
    attempt_count INTEGER DEFAULT 1
);
CREATE INDEX idx_failed_jobs_mailbox
    ON failed_index_jobs(account, mailbox);
```

### IndexManager API

```python
from apple_mail_mcp.index import IndexManager

manager = IndexManager.get_instance()

# Build index from disk (requires Full Disk Access)
manager.build_from_disk(progress_callback=None)

# Disk-based sync (fast, <5s)
changes = manager.sync_updates()  # Returns total changes count

# Search indexed content
results = manager.search(query, account=None, mailbox=None, limit=20)

# Get statistics (includes failed_jobs_count from DLQ)
stats = manager.get_stats()  # IndexStats dataclass

# Check staleness
if manager.is_stale():
    manager.sync_updates()

# Single-row primitives (added v0.3.0)
manager.delete_email(message_id, account=None, mailbox=None)
manager.record_parse_failure(emlx_path, account, mailbox, error)
manager.clear_parse_failure(emlx_path)  # called on successful re-parse
```

### Disk Functions

```python
from apple_mail_mcp.index.disk import (
    find_mail_directory,      # → ~/Library/Mail/V10/
    parse_emlx,               # Parse single .emlx file
    scan_all_emails,          # Iterator over all emails (with content)
    get_disk_inventory,       # Fast walk, NO content parsing → dict
    iter_disk_inventory,      # Streaming variant → generator (added v0.3.0)
    read_envelope_index,      # Query metadata DB
)

# Fast inventory (for sync)
inventory = get_disk_inventory(mail_dir)
# Returns: {(account, mailbox, msg_id): "/path/to/email.emlx", ...}
```

### Sync Functions

```python
from apple_mail_mcp.index.sync import (
    get_db_inventory,     # Get {(account, mailbox, msg_id): path} from DB
    sync_from_disk,       # State reconciliation
    SyncResult,           # Dataclass with added/deleted/moved counts
)

result = sync_from_disk(conn, mail_dir, progress_callback)
# result.added, result.deleted, result.moved, result.errors
```

## Coding Standards

- **Python 3.11+**, type hints required
- **Formatter**: `uv run ruff format src/`
- **Linter**: `uv run ruff check src/`
- Line length: 80 characters

## Adding New Query Tools

With the consolidated API, extend `get_emails()` filters or `search()` scopes:

```python
# In server.py - adding a new filter
@mcp.tool
async def get_emails(
    ...
    filter: Literal["all", "unread", "flagged", "today", "last_7_days", "starred"] = "all",
    ...
):
    ...
    elif filter == "starred":
        query = query.where("data.flaggedStatus[i] === true")
```

For completely new operations, use `execute_with_core_async()`:

```python
from .executor import execute_with_core_async

@mcp.tool
async def mark_as_read(message_id: int) -> dict:
    """Mark a message as read."""
    script = f"""
const msg = Mail.messages.byId({message_id});
msg.readStatus = true;
JSON.stringify({{success: true, id: {message_id}}});
"""
    return await execute_with_core_async(script)
```

## MailCore Date Helpers

```javascript
// Get today at midnight
MailCore.today()  // Date

// Get N days ago at midnight
MailCore.daysAgo(7)  // Date (for "last_7_days" filter)

// Format for JSON
MailCore.formatDate(date)  // ISO string or null
```

## CLI Commands

```bash
apple-mail-mcp              # Run MCP server (default)
apple-mail-mcp serve        # Run MCP server explicitly
apple-mail-mcp serve -r     # Run in read-only mode
apple-mail-mcp --watch      # Run with real-time index updates
apple-mail-mcp index        # Build search index from disk
apple-mail-mcp status       # Show index statistics
apple-mail-mcp rebuild      # Force rebuild index
apple-mail-mcp search       # Search emails (JSON output)
apple-mail-mcp read         # Read a single email (JSON output)
apple-mail-mcp emails       # List emails (JSON output)
apple-mail-mcp accounts     # List accounts (JSON output)
apple-mail-mcp mailboxes    # List mailboxes (JSON output)
apple-mail-mcp extract      # Extract attachment (JSON output)
apple-mail-mcp integrate claude  # Generate a Claude Code skill file
```

## Testing

### Unit Tests

```bash
# Run all tests
uv run pytest

# Run with verbose output
uv run pytest -v

# Run specific test file
uv run pytest tests/test_search.py
```

### Manual Testing

```bash
# Import test
uv run python -c "from apple_mail_mcp import mcp; print('OK')"

# Test index
uv run python -c "
from apple_mail_mcp.index import IndexManager
m = IndexManager.get_instance()
if m.has_index():
    stats = m.get_stats()
    print(f'Emails: {stats.email_count}')
"
```

## Git Workflow & CI/CD

### Branching: Trunk-Based

- Commit directly to `main` for small changes (bug fixes, housekeeping, single-file edits)
- Use short-lived feature branches (`feat/write-ops`, `fix/search-filter`) for multi-commit work
- Merge back to `main` via fast-forward or squash merge
- No long-lived `dev` branch. Tags mark releases.

### CI Workflows (`.github/workflows/`)

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| `lint.yml` | Push/PR to `main` | `ruff check src/` + `ruff format --check src/` |
| `release.yml` | Tag push (`v*`) | `uv build` → PyPI publish → GitHub Release |

### Releasing

A single tag push triggers the full pipeline: **build → PyPI publish → GitHub Release**.

**Pre-release checklist** (all version strings must match):
1. `pyproject.toml` → `version = "0.X.Y"`
2. `server.json` → `"version"` and `packages[0].version`
3. Run lint + format + tests (see Pre-push Checklist)
4. Commit, tag, and push:

```bash
git add pyproject.toml server.json
git commit -m "Bump version to 0.X.Y"
git tag v0.X.Y
git push origin main v0.X.Y
```

**What happens automatically:**
1. `build` job — `uv build` creates sdist + wheel
2. `publish` job — uploads to PyPI via OIDC trusted publisher (no tokens)
3. `github-release` job — creates a GitHub Release with auto-generated notes

Both PyPI and GitHub Releases stay in sync from a single `git push`.

**Trusted Publishers:** PyPI is configured to trust `release.yml` in the `pypi` GitHub environment — no API tokens needed. If this breaks, check:
- PyPI project settings → Trusted Publishers
- GitHub repo → Settings → Environments → `pypi`

### Pre-push Checklist

```bash
uv run ruff check src/       # Lint
uv run ruff format --check src/  # Format check
uv run pytest                 # Tests (requires macOS + Mail.app)
```

## Critical: JXA Performance

**ALWAYS use batch property fetching.** Never iterate messages individually:

```javascript
// WRONG - 87x slower
for (let msg of inbox.messages()) {
    results.push({ from: msg.sender() });  // IPC per message
}

// RIGHT - Use MailCore.batchFetch
const data = MailCore.batchFetch(msgs, ["sender", "subject"]);
for (let i = 0; i < data.sender.length; i++) {
    results.push({ from: data.sender[i] });
}
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `APPLE_MAIL_DEFAULT_ACCOUNT` | First account | Default email account |
| `APPLE_MAIL_DEFAULT_MAILBOX` | `INBOX` | Default mailbox |
| `APPLE_MAIL_INDEX_PATH` | `~/.apple-mail-mcp/index.db` | Index database location |
| `APPLE_MAIL_INDEX_MAX_EMAILS` | `5000` | Max emails per mailbox |
| `APPLE_MAIL_INDEX_STALENESS_HOURS` | `24` | Hours before refresh |
| `APPLE_MAIL_INDEX_EXCLUDE_MAILBOXES` | `Drafts` | Comma-separated mailboxes to skip |
| `APPLE_MAIL_READ_ONLY` | `false` | Disable write operations |

## Benchmarks

Competitive benchmarks live in `benchmarks/` and compare against 7 other Apple Mail MCP servers.

```bash
# Install all competitors
bash benchmarks/setup.sh

# Run all benchmarks (outputs JSON to benchmarks/results/)
uv run --group bench python -m benchmarks.run

# Generate Plotly charts (PNG to repo root, HTML to results/)
uv run --group bench python -m benchmarks.charts

# Single competitor or scenario
uv run --group bench python -m benchmarks.run --competitor imdinu
uv run --group bench python -m benchmarks.run --scenario search_body
```

Key files:
- `benchmarks/harness.py` — MCP client + timing engine (JSON-RPC over stdio)
- `benchmarks/competitors.py` — Competitor configs (commands, tool name mappings)
- `benchmarks/run.py` — CLI runner (argparse, outputs JSON + stdout summary)
- `benchmarks/charts.py` — Plotly horizontal bar charts (PNG + HTML)
- `benchmarks/setup.sh` — Install all competitors to `~/.cache/apple-mail-mcp-bench/`
- `BENCHMARKS.md` — Results document with embedded chart PNGs

Chart PNGs are committed (they ARE the results). JSON and HTML in `benchmarks/results/` are gitignored.

## Known Limitations

1. **macOS Only** - Requires Apple Mail and `osascript`
2. **Mail Version** - Auto-detects highest `~/Library/Mail/V*/` directory (dynamic V10+ detection)
3. **Full Disk Access** - Required for disk-based indexing and sync

## Security

### Implemented Protections

| Threat | Mitigation | Location |
|--------|------------|----------|
| **SQL Injection** | Parameterized queries with `?` placeholders | search.py, sync.py |
| **JXA Injection** | `json.dumps()` serialization for all strings | sync.py |
| **FTS5 Query Injection** | Special character escaping via regex | search.py |
| **XSS via HTML Emails** | BeautifulSoup HTML parsing (not regex) | disk.py |
| **DoS via Large Files** | 25 MB file size limit (`MAX_EMLX_SIZE`) | disk.py |
| **DoS via Spam** | Max emails per mailbox limit (configurable) | manager.py |
| **Path Traversal** | Path validation in file watcher | watcher.py |
| **Data Exposure** | Database created with 0600 permissions | schema.py |
| **Unbounded Memory** | Pending changes limit in watcher | watcher.py |
