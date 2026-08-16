# Installation

## Claude Code Plugin

```bash
claude plugin marketplace add imdinu/apple-mail-mcp
claude plugin install apple-mail@imdinu
```

The plugin registers the MCP server automatically via a thin launcher that runs the released PyPI package (uvx → pipx → private venv, whichever is available on your machine).

## Claude Desktop Bundle

Download `apple-mail-mcp-<version>.mcpb` from the [latest release](https://github.com/imdinu/apple-mail-mcp/releases/latest) and double-click it. Claude Desktop installs and registers the server; the bundle uses the same launcher as the Claude Code plugin.

## With pipx

```bash
pipx install apple-mail-mcp
```

The FTS5 search index (`~/.apple-mail-mcp/index.db`) is keyed to your home directory, not the install method — every install surface above shares the same index. A persistent install just avoids the small per-launch resolution overhead of ephemeral runners like `pipx run` or `uvx`.

## With uv

```bash
uv tool install apple-mail-mcp
```

## With pip

```bash
pip install apple-mail-mcp
```

## From Source

For development or to run the latest unreleased version:

```bash
git clone https://github.com/imdinu/apple-mail-mcp
cd apple-mail-mcp
uv sync
```

Run with:

```bash
uv run apple-mail-mcp
```

## Prerelease Versions

To install a prerelease (e.g., `v0.2.0a1`):

```bash
pipx install apple-mail-mcp --pip-args='--pre'
# or
uv tool install apple-mail-mcp --prerelease=allow
```

## Verify Installation

```bash
apple-mail-mcp status
```

This prints the index status. If you see output (even "no index found"), the installation is working.

## Requirements

| Requirement | Version |
|-------------|---------|
| **macOS** | Ventura or later |
| **Python** | 3.11+ |
| **Apple Mail** | Configured with ≥1 account |

!!! note
    Apple Mail MCP is macOS-only. It requires Apple Mail and the `osascript` runtime for JXA execution.
