# Getting Started

Get Apple Mail MCP running with Claude in under 2 minutes.

## Prerequisites

- **macOS** (Ventura or later)
- **Apple Mail** configured with at least one account
- **Python 3.11+** (for `pipx` or `uv`)
- An MCP client (Claude Desktop, Claude Code, etc.)

## Step 1: Add to Your MCP Client

=== "Claude Code"

    Install the plugin — it registers the server for you:

    ```bash
    claude plugin marketplace add imdinu/apple-mail-mcp
    claude plugin install apple-mail@imdinu
    ```

=== "Claude Desktop"

    Download `apple-mail-mcp-<version>.mcpb` from the [latest release](https://github.com/imdinu/apple-mail-mcp/releases/latest) and double-click it. Claude Desktop installs and registers the server.

=== "Any MCP client"

    Install the package, then register the command in your client's MCP config:

    ```bash
    pipx install apple-mail-mcp
    ```

    ```json
    {
      "mcpServers": {
        "mail": {
          "command": "apple-mail-mcp"
        }
      }
    }
    ```

The plugin and the bundle launch the server with `--watch`, so the index stays current as mail arrives. That background sync reads `~/Library/Mail/`, which needs Full Disk Access on the app that launches the server (Claude Code or Claude Desktop) — grant it in **System Settings → Privacy & Security → Full Disk Access**. Without it the server still works and warns at startup that the index is not being kept fresh.

## Step 2: Build the Search Index (Recommended)

The FTS5 index enables **full-text body search** (~2ms) — without it, only subject and sender search is available. It's optional but highly recommended.

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
    The MCP server can *serve* this index without Full Disk Access, but keeping it fresh needs FDA too: the startup sync reads the same protected `~/Library/Mail/` location the indexer does. Grant it to the app that launches the server (your MCP client); the server warns at startup when it can't read your mail.

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
- [Tools](tools.md) — full reference for all 11 MCP tools
