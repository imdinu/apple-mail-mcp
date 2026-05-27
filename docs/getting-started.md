# Getting Started

Get Apple Mail MCP running with Claude in under 2 minutes.

## Prerequisites

- **macOS** (Ventura or later)
- **Apple Mail** configured with at least one account
- **Python 3.11+** (for `pipx` or `uv`)
- An MCP client (Claude Desktop, Claude Code, etc.)

## Step 1: Add to Your MCP Client

=== "Claude Desktop"

    Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

    ```json
    {
      "mcpServers": {
        "mail": {
          "command": "pipx",
          "args": ["run", "apple-mail-mcp"]
        }
      }
    }
    ```

=== "Claude Code"

    Edit `.mcp.json` in your project or `~/.claude/mcp.json` globally:

    ```json
    {
      "mcpServers": {
        "mail": {
          "command": "apple-mail-mcp"
        }
      }
    }
    ```

## Step 2: Build the Search Index (Recommended)

The FTS5 index enables **full-text body search** (~20ms) — without it, only subject and sender search is available. It's optional but highly recommended.

### Grant Full Disk Access

The indexer reads `.emlx` files directly from `~/Library/Mail/V10/`, which requires Full Disk Access:

1. Open **System Settings**
2. Go to **Privacy & Security → Full Disk Access**
3. Add and enable **Terminal.app** (or your terminal emulator)
4. Restart your terminal

### Build the Index

```bash
apple-mail-mcp index --verbose
# → Indexed 22,696 emails in 1m 7.6s
# → Database size: 130.5 MB
```

!!! note
    The MCP server itself does **not** need Full Disk Access — it uses disk-based sync at startup to keep the index fresh.

## Step 3: Use It

Once configured, talk to Claude naturally:

- *"Show me today's unread emails"*
- *"Search for emails about invoices"*
- *"Get the full content of email 12345"*
- *"List my email accounts"*

## Optional: Real-Time Index Updates

Keep the index automatically up-to-date as new emails arrive:

```bash
apple-mail-mcp --watch
```

This monitors `~/Library/Mail/V10/` for new `.emlx` files and indexes them in real-time.

## Alternative: CLI Without MCP

Don't need an MCP server? Use the CLI directly:

```bash
apple-mail-mcp search "quarterly report" --after 2026-01-01
apple-mail-mcp read 12345
apple-mail-mcp emails --filter unread --limit 10
```

Generate a Claude Code skill for CLI-based access:

```bash
apple-mail-mcp integrate claude > ~/.claude/skills/apple-mail.md
```

See [Configuration](configuration.md#cli-commands) for the full command list.

## Next Steps

- [Installation](installation.md) — alternative install methods (pipx, uv, from source)
- [Configuration](configuration.md) — TOML config, environment variables, and precedence
- [Tools](tools.md) — full reference for all 8 MCP tools
