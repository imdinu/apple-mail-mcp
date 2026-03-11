# Apple Mail MCP

**A fast MCP server for Apple Mail** — disk-first email reading, batch JXA property fetching for **87x faster** multi-email performance, and an FTS5 search index for **700–3500x faster** body search.

---

## Why Apple Mail MCP?

| | Apple Mail MCP | Typical alternatives |
|---|---|---|
| **Fetch single email** | 6ms (disk) | unsupported |
| **Fetch 50 emails** | 301ms | 13,800ms+ (or crash) |
| **Search (subject)** | 10ms (FTS5) | 148–166ms |
| **Search (body)** | 22ms (FTS5) | unsupported |
| **Architecture** | Disk-first + batch JXA + FTS5 | Per-message AppleScript |

> Benchmarked against [5 other Apple Mail MCP servers](benchmarks.md). We're the **fastest at search** across all scenarios and the **only one with body search**.

## Key Features

- **6 focused MCP tools** — list accounts, list mailboxes, get emails, get email, search, get attachment
- **Unified filtering** — unread, flagged, today, this week
- **FTS5 search index** — full-text body search in ~2ms with BM25 ranking
- **Real-time updates** — `--watch` flag for automatic index updates
- **Disk-first sync** — fast filesystem scanning instead of slow JXA queries
- **Type-safe** — full Python type hints with PEP 561 `py.typed` marker

## Quick Install

```bash
# No install required — run directly
pipx run apple-mail-mcp

# Or install globally
pipx install apple-mail-mcp
```

## Claude Desktop Setup

```json
{
  "mcpServers": {
    "mail": {
      "command": "apple-mail-mcp"
    }
  }
}
```

That's it. Ask Claude to search your emails, get today's messages, or find unread mail.

## Next Steps

- [Getting Started](getting-started.md) — first-use walkthrough
- [Installation](installation.md) — all installation methods
- [Tools](tools.md) — full API reference for all 6 tools
- [Search & Indexing](search.md) — FTS5 deep dive
- [Architecture](architecture.md) — how it works under the hood
- [Architecture Deep Dive](architecture-deep-dive.md) — `.emlx` format, JXA IPC, FTS5 index design
- [Benchmarks](benchmarks.md) — competitive performance data
