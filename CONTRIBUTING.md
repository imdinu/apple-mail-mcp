# Contributing to Apple Mail MCP

Thanks for your interest in contributing! This guide will help you get started.

## Development Setup

1. **Prerequisites**: macOS with Apple Mail configured, Python 3.11+, [uv](https://docs.astral.sh/uv/)

2. **Clone and install**:
   ```bash
   git clone https://github.com/imdinu/apple-mail-mcp.git
   cd apple-mail-mcp
   uv sync
   ```

3. **Build the search index** (requires Full Disk Access for your terminal):
   ```bash
   uv run apple-mail-mcp index
   ```

4. **Run tests**:
   ```bash
   uv run pytest
   ```

## Project Structure

```
src/apple_mail_mcp/
├── server.py           # MCP tools (the public API)
├── builders.py         # JXA script construction
├── executor.py         # osascript execution
├── index/
│   ├── disk.py         # .emlx file parsing
│   ├── manager.py      # IndexManager (SQLite index)
│   ├── search.py       # FTS5 search
│   ├── sync.py         # Disk-based state reconciliation
│   └── watcher.py      # Real-time file watcher
└── jxa/
    └── mail_core.js    # Shared JXA utilities
```

## Making Changes

### Branching

- Create a feature branch from `main`: `git checkout -b feat/your-feature`
- Keep branches short-lived and focused on a single change

### Code Style

- **Formatter**: `uv run ruff format src/`
- **Linter**: `uv run ruff check src/`
- Line length: 80 characters
- Type hints required (Python 3.11+ syntax)

### Testing

All changes should include tests. Run the full suite before submitting:

```bash
uv run ruff check src/          # Lint
uv run ruff format --check src/ # Format check
uv run pytest -v                # Tests
```

Tests use `pytest` with `pytest-asyncio`. Most tests mock JXA execution so they run without Apple Mail.

### Architecture Notes

- **`server.py`** contains the 6 MCP tools — this is the public API surface. Changes here affect what LLMs see and call.
- **`get_email()` uses a strategy cascade**: Strategy 0 (disk) → Strategy 1 (JXA specified mailbox) → Strategy 2 (index lookup) → Strategy 3 (iterate all). Each must return the same response schema.
- **`parse_emlx()`** in `disk.py` handles the `.emlx` format: byte count line, MIME content, plist footer. The plist footer contains Apple Mail metadata (flags bitmask, date-received timestamp).
- **JXA scripts must use batch property fetching** via `MailCore.batchFetch()` — never iterate messages individually (87x performance difference).

## Submitting a PR

1. Ensure all checks pass (`ruff check`, `ruff format`, `pytest`)
2. Write a clear PR description explaining *what* and *why*
3. Keep the diff focused — avoid unrelated changes in the same PR
4. PRs are typically squash-merged into `main`

## Reporting Issues

Open an issue on [GitHub](https://github.com/imdinu/apple-mail-mcp/issues) with:
- What you expected vs what happened
- macOS version and Mail.app configuration (number of accounts, rough mailbox sizes)
- Any error output or logs

## License

By contributing, you agree that your contributions will be licensed under the [GPL-3.0](LICENSE) license.
