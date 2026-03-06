# Architecture

Apple Mail MCP uses a **hybrid access pattern** — live JXA for real-time operations, FTS5 for search, and direct disk reads for indexing.

## Project Structure

```
src/apple_mail_mcp/
├── __init__.py         # CLI entry point, exports main()
├── cli.py              # CLI commands (index, status, rebuild, serve)
├── server.py           # FastMCP server with 6 MCP tools
├── config.py           # Environment variable configuration
├── builders.py         # QueryBuilder, AccountsQueryBuilder
├── executor.py         # run_jxa(), execute_with_core(), execute_query()
├── index/              # FTS5 search index module
│   ├── __init__.py     # Exports IndexManager
│   ├── schema.py       # SQLite schema v4 (attachment support)
│   ├── manager.py      # IndexManager class (singleton)
│   ├── disk.py         # .emlx reading + get_disk_inventory()
│   ├── sync.py         # Disk-based state reconciliation
│   ├── search.py       # FTS5 search functions
│   └── watcher.py      # Real-time file watcher
└── jxa/
    ├── __init__.py     # Exports MAIL_CORE_JS
    └── mail_core.js    # Shared JXA utilities (MailCore object)
```

## Hybrid Access Pattern

| Access Method | Use Case | Latency | When Used |
|---------------|----------|---------|-----------|
| **JXA (Live)** | Real-time ops, small queries | ~100–300ms | `get_email()`, `list_mailboxes()` |
| **FTS5 (Cached)** | Body search, complex filtering | ~2–10ms | `search()` |
| **Disk (Batch)** | Initial indexing, sync | ~15ms/100 emails | `index` command, startup |

## Layer Separation

### 1. MCP Tools (`server.py`)

The 6 MCP tools are the public API. Each tool resolves defaults, picks the right access method, and returns typed results.

### 2. Query Builder (`builders.py`)

Constructs JXA scripts from Python using a builder pattern. Prevents JXA injection by design — all user input is serialized via `json.dumps()`.

```python
query = (
    QueryBuilder()
    .from_mailbox("Work", "INBOX")
    .select("standard")
    .where("data.readStatus[i] === false")
    .order_by("date_received", descending=True)
    .limit(10)
)
```

### 3. JXA Executor (`executor.py`)

Runs JXA scripts via `osascript -l JavaScript` as async subprocesses. Every script gets `MAIL_CORE_JS` prepended — a shared library that provides batch property fetching and date helpers.

### 4. Index Module (`index/`)

Self-contained SQLite + FTS5 search system:

- **`manager.py`** — `IndexManager` singleton, orchestrates build/sync/search
- **`disk.py`** — reads `.emlx` files directly (30x faster than JXA)
- **`sync.py`** — state reconciliation between DB and filesystem
- **`search.py`** — FTS5 queries with BM25 ranking and special character escaping
- **`schema.py`** — DDL with migrations, creates DB with `0600` permissions
- **`watcher.py`** — `watchfiles`-based real-time monitor

## Data Flow

### JXA Path (Real-Time Operations)

```
MCP Tool
  → QueryBuilder.build()
    → executor.execute_query()
      → MAIL_CORE_JS + script body
        → osascript -l JavaScript
          → JSON.parse(stdout)
```

### Disk Sync Path (Startup)

```
Server startup
  → IndexManager.sync_updates()
    → sync.sync_from_disk(conn, mail_dir)
      → disk.get_disk_inventory()     # walk filesystem
      → sync.get_db_inventory()       # query SQLite
        → Calculate diff: NEW, DELETED, MOVED
          → NEW: parse_emlx() → INSERT
          → DELETED: DELETE from DB
          → MOVED: UPDATE emlx_path
```

### FTS5 Search Path

```
search(query, scope="all")
  → IndexManager.search()
    → search.fts5_search(conn, query, limit)
      → FTS5 MATCH with BM25 ranking
        → Return results with content snippets
```

## Design Patterns

| Pattern | Location | Purpose |
|---------|----------|---------|
| **Builder** | `QueryBuilder` | Safe JXA script construction |
| **Singleton** | `IndexManager` | Single SQLite writer, one file watcher |
| **Facade** | `MailCore` (JS) | Clean API over verbose Apple Events |
| **Factory** | `create_connection()` | Consistent DB configuration |
| **State Reconciliation** | `sync_from_disk()` | Fast diff-based sync |

## Batch Property Fetching

The key performance optimization. Naive JXA iteration triggers a separate Apple Event IPC round-trip for **each property of each message**. Batch fetching gets all values in a single call:

```javascript
// SLOW: 54s for 50 emails (1 IPC per property per message)
for (let msg of inbox.messages()) {
    results.push({ from: msg.sender() });
}

// FAST: 0.6s for 50 emails (1 IPC per property for ALL messages)
const data = MailCore.batchFetch(msgs, ["sender", "subject"]);
for (let i = 0; i < data.sender.length; i++) {
    results.push({ from: data.sender[i] });
}
```

This is **87x faster** because Apple Events uses a single array-return call instead of N individual round-trips.

## Security

| Threat | Mitigation | Location |
|--------|------------|----------|
| SQL Injection | Parameterized queries (`?` placeholders) | `search.py`, `sync.py` |
| JXA Injection | `json.dumps()` serialization | `executor.py`, `builders.py` |
| FTS5 Query Injection | Special character escaping | `search.py` |
| XSS via HTML Emails | BeautifulSoup HTML→text parsing | `disk.py` |
| DoS via Large Files | 25 MB file size limit | `disk.py` |
| Path Traversal | Path validation in watcher | `watcher.py` |
| Data Exposure | DB created with `0600` permissions | `schema.py` |
