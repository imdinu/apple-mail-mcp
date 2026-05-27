"""Apple Mail MCP — full-coverage FTS5 body search for Apple Mail.

Features:
- Disk-first email reading (~3ms via .emlx parsing, no JXA needed)
- Full-text body search via FTS5 index (~2ms with BM25 ranking)
- Reliable on large mailboxes (tested at ~72K messages) where
  AppleScript-based servers time out

Usage:
    apple-mail-mcp            # Run MCP server (default)
    apple-mail-mcp serve -r   # Run in read-only mode
    apple-mail-mcp --watch    # Run with real-time index updates
    apple-mail-mcp init       # Write a config.toml template
    apple-mail-mcp index      # Build search index from disk
    apple-mail-mcp status     # Show index statistics
    apple-mail-mcp rebuild    # Force rebuild index
"""

from .cli import main
from .server import mcp

__all__ = ["main", "mcp"]
