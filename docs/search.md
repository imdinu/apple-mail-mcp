# Search & Indexing

Apple Mail MCP includes an optional **FTS5 search index** that makes body search **700–3500x faster** — ~2ms instead of ~7s.

## How It Works

```
┌─────────────────────┐     ┌──────────────────┐
│  ~/Library/Mail/V10 │     │  ~/.apple-mail-mcp│
│  ├── account-uuid/  │────▶│  └── index.db     │
│  │   └── mailbox/   │     │      (SQLite+FTS5)│
│  │       └── *.emlx │     └──────────────────┘
│  └── ...            │              │
└─────────────────────┘              ▼
                              Fast search (~2ms)
```

1. **Build from disk** — `apple-mail-mcp index` reads `.emlx` files directly (~30x faster than JXA)
2. **Startup sync** — index is reconciled with disk when the server starts (<5s)
3. **Real-time updates** — `--watch` flag monitors for new emails
4. **Fast search** — queries use SQLite FTS5 with BM25 ranking

## Building the Index

### Requirements

Building requires **Full Disk Access** for your terminal:

1. Open **System Settings**
2. Go to **Privacy & Security → Full Disk Access**
3. Add and enable **Terminal.app** (or your terminal emulator)
4. Restart your terminal

!!! note
    The MCP server itself does **not** need Full Disk Access. It uses disk-based sync to keep the index updated.

### Commands

```bash
# Build the index (first time)
apple-mail-mcp index --verbose

# Check index status
apple-mail-mcp status

# Force rebuild from scratch
apple-mail-mcp rebuild
```

### What Gets Indexed

For each email, the index stores:

| Field | Source | Searchable via FTS5 |
|-------|--------|:---:|
| `message_id` | Mail.app ID | — |
| `account` | Folder path UUID | — |
| `mailbox` | Folder path | — |
| `subject` | `.emlx` header | Yes |
| `sender` | `.emlx` header | Yes |
| `content` | `.emlx` body (HTML → text) | Yes |
| `date_received` | `.emlx` header | — |
| `emlx_path` | Filesystem path | — |
| `attachment_count` | MIME parsing | — |

Attachment metadata (filename, MIME type, file size) is stored in a separate `attachments` table, enabling `search(scope="attachments")` queries.

!!! note
    All FTS5-backed scopes (`all`, `body`, `subject`, `sender`) cover the full set of indexed emails. By default the index is uncapped — every email in every mailbox is searchable. Setting `APPLE_MAIL_INDEX_MAX_EMAILS` introduces an optional per-mailbox ceiling (newest by file mtime kept). When no index is available, subject and sender search fall back to live JXA queries against a single mailbox.

### Account UUIDs vs Friendly Names

The `account` column stores filesystem UUIDs (e.g., `24E569DF-5E45-...`), not friendly names like `"Work"`. This is intentional — the sync engine diffs `get_disk_inventory()` (UUID-keyed) against `get_db_inventory()` to detect new, deleted, and moved emails. Storing friendly names would break the diff, causing a full re-index on every sync cycle.

Instead, translation happens at search time via `AccountMap` (`index/accounts.py`), which maps names to UUIDs using JXA's `Mail.accounts.id()`. The mapping is cached for 5 minutes and seeded automatically when `list_accounts()` is called.

## Database Schema

The index uses SQLite with FTS5 external content tables:

```sql
-- Email content cache (schema v5)
CREATE TABLE emails (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    account TEXT NOT NULL,
    mailbox TEXT NOT NULL,
    subject TEXT,
    sender TEXT,
    content TEXT,
    date_received TEXT,
    emlx_path TEXT,
    attachment_count INTEGER DEFAULT 0,
    indexed_at TEXT DEFAULT (datetime('now')),
    UNIQUE(account, mailbox, message_id)
);

-- Attachment metadata (one-to-many from emails)
CREATE TABLE attachments (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    email_rowid INTEGER NOT NULL REFERENCES emails(rowid) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    mime_type TEXT,
    file_size INTEGER,
    content_id TEXT
);

-- FTS5 index (external content — shares storage with emails table)
CREATE VIRTUAL TABLE emails_fts USING fts5(
    subject, sender, content,
    content='emails',
    content_rowid='rowid',
    tokenize='porter unicode61'
);

-- Sync state tracking per mailbox
CREATE TABLE sync_state (
    account TEXT NOT NULL,
    mailbox TEXT NOT NULL,
    last_sync TEXT,
    message_count INTEGER DEFAULT 0,
    PRIMARY KEY(account, mailbox)
);

-- Dead letter queue for `.emlx` parse failures (added v0.3.0).
-- Populated by the watcher and disk-sync paths so operators can
-- audit which messages are missing from the index. Cleared on a
-- successful re-parse of the same path.
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
```

## Parse Failure Tracking (DLQ)

Files that fail to parse during sync or live watching aren't silently dropped — they land in `failed_index_jobs` (above). The schema captures error type, message, and first/last seen timestamps so operators can audit which messages are missing from the index.

- **View counts**: `apple-mail-mcp status` displays `Failed parse: N (.emlx files in DLQ)` when N > 0. Same number is exposed via the `index://status` MCP resource as `failed_jobs_count`.
- **Self-healing**: a successful re-parse of the same path automatically clears the DLQ entry. So transient causes (Mail.app mid-write, permissions glitches) resolve on the next watcher tick without operator intervention.
- **Failure of the DLQ itself** (disk full, DB corrupted): logged at `ERROR` level rather than swallowed. Look for `"DLQ write failed"` in logs.

The `porter unicode61` tokenizer provides:

- **Porter stemming** — "running" matches "run", "runs", "runner"
- **Unicode support** — handles international characters correctly

## Startup Sync

Every time the server starts, it runs a fast **state reconciliation** against the filesystem:

```
1. Get DB inventory:   {(account, mailbox, msg_id): emlx_path}  ← from SQLite
2. Get Disk inventory: {(account, mailbox, msg_id): emlx_path}  ← fast walk
3. Calculate diff:
   - NEW:     on disk, not in DB → parse & insert
   - DELETED: in DB, not on disk → remove from DB
   - MOVED:   same ID, different path → update path
```

This takes **<5s** even for 20,000+ emails (vs. 60s+ timeout with the old JXA-based sync).

## Real-Time Updates

Enable automatic index updates with the `--watch` flag:

```bash
apple-mail-mcp --watch
```

The file watcher monitors `~/Library/Mail/V10/` for:

- New `.emlx` files → parse and insert into index
- Deleted `.emlx` files → remove from index
- Moved `.emlx` files → update path in index

## Date-Range Filtering

Use the `before` and `after` parameters to restrict search results to a date range. Both accept dates in `YYYY-MM-DD` format.

```python
# Emails from Q1 2025
search("invoice", after="2025-01-01", before="2025-03-31")

# Emails received after a specific date
search("shipping confirmation", after="2025-06-01")

# Emails received before a specific date
search("contract", before="2024-12-31")
```

Date filtering is applied at the SQL level when using the FTS5 index, so it does not impact search performance.

## Highlighted Results

Set `highlight=True` to have matching terms wrapped in highlight markers in the result snippets. This is useful for displaying search results in a UI or for quickly identifying why a result matched.

```python
search("quarterly report", highlight=True)
# Results include highlighted snippets, e.g.:
# "Please find the **quarterly report** attached..."
```

When highlighting is enabled, the `content_snippet` field in each result contains the matched terms wrapped in markers.

## Pagination

Use `offset` with `limit` to page through large result sets:

```python
# First page (results 1-20)
search("meeting", limit=20)

# Second page (results 21-40)
search("meeting", limit=20, offset=20)

# Third page (results 41-60)
search("meeting", limit=20, offset=40)
```

Pagination works with all scopes, date filters, and highlighting.

## Performance

### Search Speed

| Query | Results | Time |
|-------|---------|------|
| "invoice" | 20 | 2.5ms |
| "meeting tomorrow" | 20 | 1.3ms |
| "password reset" | 20 | 0.6ms |
| "shipping confirmation" | 10 | 4.1ms |

### With vs. Without Index

| Operation | Without Index | With Index | Speedup |
|-----------|---------------|------------|---------|
| Body search | ~7,000ms | ~2–10ms | **700–3500x** |
| Startup sync | 60s timeout | <5s | **12x** |
| Initial build | — | ~1–2 min | One-time |
| Disk usage | — | ~6 KB/email | — |

