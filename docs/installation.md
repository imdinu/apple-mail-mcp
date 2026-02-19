# Installation

## No Installation Required

Run directly from PyPI without installing anything:

```bash
pipx run apple-mail-mcp
```

This downloads and runs the latest version in a temporary environment. Great for trying it out.

## With pipx (Recommended)

For faster startup on subsequent runs, install globally:

```bash
pipx install apple-mail-mcp
```

## With uv

```bash
# Run without installing
uvx apple-mail-mcp

# Or install into a managed tool environment
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
