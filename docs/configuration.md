# Configuration

Apple Mail MCP can be configured via a TOML config file at
`~/.apple-mail-mcp/config.toml`, environment variables, or both. All
settings have sensible defaults — no configuration is required to get
started.

## Quick start

Generate a commented config file:

```bash
apple-mail-mcp init
```

This writes `~/.apple-mail-mcp/config.toml` with every available key
documented inline. All values are commented out, so defaults remain in
effect — uncomment what you want to override. Pass `--force` to
overwrite an existing config file.

## Configuration file

The TOML file at `~/.apple-mail-mcp/config.toml` is loaded on every
server start. All sections and keys are optional.

```toml
config_version = 1

[defaults]
# account = "Personal"          # Default email account
# mailbox = "INBOX"             # Default mailbox

[index]
# path = "~/.apple-mail-mcp/index.db"   # FTS5 database location
# max_emails = 5000             # Per-mailbox ceiling (omit for uncapped)
# staleness_hours = 24.0        # Hours before re-sync
# exclude_mailboxes = ["Drafts"]   # Mailboxes to skip during indexing

[server]
# read_only = false             # Disable write operations
```

**Empty list semantics**: `exclude_mailboxes = []` explicitly means "no
exclusions" — different from omitting the key, which uses the
`["Drafts"]` default.

**Validation**: malformed TOML, unknown keys, type mismatches, and
`config_version` mismatches all raise a clear error with the file path
and the offending key. The server refuses to start rather than silently
using degraded config.

## Environment variables

Every config key can also be set via an environment variable. Env vars
override TOML file values, which is useful for per-deployment overrides
in CI or in MCP client launch configs.

| Variable | Default | Description |
|----------|---------|-------------|
| `APPLE_MAIL_DEFAULT_ACCOUNT` | First account | Default email account for all tools |
| `APPLE_MAIL_DEFAULT_MAILBOX` | `INBOX` | Default mailbox when none specified |
| `APPLE_MAIL_INDEX_PATH` | `~/.apple-mail-mcp/index.db` | SQLite index database location |
| `APPLE_MAIL_INDEX_MAX_EMAILS` | _unset_ | Optional per-mailbox ceiling (default: uncapped) |
| `APPLE_MAIL_INDEX_STALENESS_HOURS` | `24` | Hours before index is considered stale |
| `APPLE_MAIL_INDEX_EXCLUDE_MAILBOXES` | `Drafts` | Comma-separated mailboxes to skip in search |
| `APPLE_MAIL_READ_ONLY` | `false` | When `true`, disables any write operations |

## Precedence

When the same value is set in multiple places, the highest-precedence
source wins. From highest to lowest:

1. **CLI flags** (e.g. `apple-mail-mcp serve -r`)
2. **Environment variables** (`APPLE_MAIL_*`)
3. **TOML config file** (`~/.apple-mail-mcp/config.toml`)
4. **Built-in defaults**

For multi-client deployments (Claude Desktop + Cursor + Cline + ...),
the TOML file is the cleanest place for durable user policy — set it
once instead of pasting the same `env: {}` block into every client.

## Per-mailbox email limit (optional)

By default the index covers every email in every mailbox — there is no
per-mailbox cap. Set `APPLE_MAIL_INDEX_MAX_EMAILS` (or `[index] max_emails`
in `config.toml`) to opt in to a ceiling, useful if you want to bound
index size on machines with many large mailboxes:

```bash
export APPLE_MAIL_INDEX_MAX_EMAILS=10000
apple-mail-mcp rebuild
```

When a cap is set and a mailbox exceeds it, the most recent emails by
file modification time are kept. `apple-mail-mcp rebuild` reports how
many mailboxes hit the cap, and `apple-mail-mcp status` surfaces the
same information after the fact.

## Read-only mode

Use `--read-only` (or `-r`) on the `serve` command to prevent any write
operations. This can also be set via the `APPLE_MAIL_READ_ONLY`
environment variable or `[server] read_only = true` in `config.toml`.

```bash
apple-mail-mcp serve --read-only
```

Or via environment variable:

```bash
export APPLE_MAIL_READ_ONLY=true
apple-mail-mcp serve
```

When read-only mode is active, write-capable MCP tools raise
`PermissionError` with a clear message rather than silently no-oping.

## Index location

The FTS5 index database is stored at `~/.apple-mail-mcp/index.db` by
default. Override via the env var or TOML key:

```bash
export APPLE_MAIL_INDEX_PATH="/path/to/custom/index.db"
```

```toml
[index]
path = "/path/to/custom/index.db"
```

The database file is created with `0600` permissions (owner read/write
only) for security. The same posture applies to `config.toml` (created
by `apple-mail-mcp init`) and the attachment cache.

## MCP client configuration

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mail": {
      "command": "apple-mail-mcp",
      "env": {
        "APPLE_MAIL_DEFAULT_ACCOUNT": "Work",
        "APPLE_MAIL_DEFAULT_MAILBOX": "INBOX"
      }
    }
  }
}
```

If you have multiple MCP clients, consider setting durable defaults in
`~/.apple-mail-mcp/config.toml` instead, and keep the `env` block here
for per-client overrides only.

### Claude Code

Edit `.mcp.json` in your project root or `~/.claude/mcp.json` globally:

```json
{
  "mcpServers": {
    "mail": {
      "command": "apple-mail-mcp",
      "env": {
        "APPLE_MAIL_DEFAULT_ACCOUNT": "Work"
      }
    }
  }
}
```

### With real-time indexing

To keep the search index automatically updated:

```json
{
  "mcpServers": {
    "mail": {
      "command": "apple-mail-mcp",
      "args": ["--watch"],
      "env": {
        "APPLE_MAIL_DEFAULT_ACCOUNT": "Work"
      }
    }
  }
}
```

## CLI commands

```bash
apple-mail-mcp              # Run MCP server (default)
apple-mail-mcp serve        # Run MCP server explicitly
apple-mail-mcp serve -r     # Run in read-only mode
apple-mail-mcp --watch      # Run with real-time index updates
apple-mail-mcp init         # Write a config.toml template
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
