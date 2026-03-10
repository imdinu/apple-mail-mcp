"""Apple Mail MCP - Fast MCP server for Apple Mail.

Features:
- Disk-first email reading (~1-5ms via .emlx parsing, no JXA needed)
- 87x faster batch email fetching via JXA property fetching
- FTS5 full-text search index for 700-3500x faster body search

Usage:
    apple-mail-mcp            # Run MCP server (default)
    apple-mail-mcp index      # Build search index from disk
    apple-mail-mcp status     # Show index statistics
    apple-mail-mcp rebuild    # Force rebuild index
"""

from .cli import main
from .server import mcp

__all__ = ["main", "mcp"]
