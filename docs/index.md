# Apple Mail MCP

**A fast MCP server for Apple Mail** — optimized JXA scripts with batch property fetching for **87x faster** performance, plus an FTS5 search index for **700–3500x faster** body search.

---

## Why Apple Mail MCP?

| | Apple Mail MCP | Typical alternatives |
|---|---|---|
| **Fetch 50 emails** | 529ms | 15–60s (or timeout) |
| **Body search** | ~2ms (FTS5) | ~7s or unsupported |
| **Cold start** | 194ms | 60–800ms |
| **Architecture** | Batch JXA + disk sync | Per-message AppleScript |

> Benchmarked against [7 other Apple Mail MCP servers](benchmarks.md). We're the **fastest dedicated mail server** across all scenarios and the **only one with body search**.

## Key Features

- **5 focused MCP tools** — list accounts, list mailboxes, get emails, get email, search
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
- [Tools](tools.md) — full API reference for all 5 tools
- [Search & Indexing](search.md) — FTS5 deep dive
- [Architecture](architecture.md) — how it works under the hood
- [Benchmarks](benchmarks.md) — competitive performance data
