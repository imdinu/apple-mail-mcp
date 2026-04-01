# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - Unreleased

### Added

- **Date-range filtering for search** — new `before` and `after` parameters (YYYY-MM-DD) on `search()`. Filter results by date across all scopes including attachments. (#9)
- **Highlighted search results** — new `highlight` parameter on `search()`. When enabled, matched terms are wrapped in `**markers**` in subject and content_snippet using FTS5 `highlight()` and `snippet()`. (#11)
- **`get_email_links()` tool** — extracts hyperlinks from an email's HTML content. Replaces the links mode of `get_attachment()`. (#55)
- **`get_email_attachment()` tool** — extracts a named file attachment and saves to disk. Replaces the attachment mode of `get_attachment()`. (#55)
- **CLI wrappers** — all MCP tools now accessible as CLI commands: `search`, `read`, `emails`, `accounts`, `mailboxes`, `extract`. Output JSON to stdout. (#61)
- **Skill generator** — `apple-mail-mcp integrate claude` generates a Claude Code skill file for CLI-based email access. (#62)
- **`--read-only` server flag** — `apple-mail-mcp serve --read-only` (or `APPLE_MAIL_READ_ONLY=true`) prepares for v0.3.0 write operations. (#63)
- **Dynamic Mail version detection** — auto-detects the highest `V*` directory under `~/Library/Mail/` instead of hardcoding `V10`. (#57)

### Changed

- **`get_attachment()` deprecated** — still registered for backwards compatibility, but delegates to `get_email_links()` or `get_email_attachment()`. Will be removed in v0.3.0.

### Fixed (from v0.1.8)

- **Watcher crash on file add** — `parse_emlx()` exceptions beyond `OSError`/`ValueError`/`UnicodeDecodeError` (e.g. malformed plist, missing headers) no longer kill the watcher thread. The watcher now skips unparseable files and continues processing.
- **Attachment cache leak** — `_cleanup_old_attachments()` is now called automatically when extracting attachments, preventing unbounded disk usage from cached files.
- **Attachment cache permissions** — cache directory is now created with `0o700` permissions to protect sensitive email attachment content.
- **Empty search error messages** — search index errors (corrupt DB, SQLite issues) now return actionable error messages instead of empty strings. Suggests `apple-mail-mcp rebuild` when the index is broken.
- **Misleading get_email timeout message** — when `get_email` times out, the error now checks whether account/mailbox were already provided and gives context-appropriate advice instead of always saying "Provide account/mailbox".
- **Renamed `this_week` filter to `last_7_days`** — `this_week` kept as alias for backwards compatibility. (#49)
- **`search_fts_highlight()` bugs** — fixed missing account/mailbox/exclude_mailboxes filters, integer row indexing, and missing FTS5 retry logic.
- **Case-sensitive mailbox filtering** — `search(mailbox="INBOX")` now matches `Inbox`, `inbox`, etc. Previously returned zero results on case mismatch. (#67)
- **Updated patrickfreyer benchmark config** and added `rusty_apple_mail_mcp` to benchmarks.

## [0.1.7] - 2026-03-11

### Added

- **Strategy 0 (disk read) for `get_email()`** — reads email content directly from `.emlx` files on disk, bypassing JXA/Apple Events entirely. Fastest path when the search index is available. Falls through to JXA strategies on failure. (Thanks to @vkostakos for the initial implementation in PR #53)
- Extracts read/flagged status from `.emlx` plist footer flags bitmask
- Extracts `date_sent`, `reply_to`, `Message-ID` from MIME headers for full schema parity
- `get_email` benchmark scenario with dynamic message ID discovery
- `CONTRIBUTING.md` for new contributors
- This changelog

### Fixed

- `date_received` now uses the `Received` header (delivery time) instead of `Date` header (composition time). Previously both `date_received` and `date_sent` were identical. Run `apple-mail-mcp rebuild` after upgrading to fix historical emails.

### Changed

- Updated project messaging across all descriptions to reflect disk-first architecture
- Re-ran competitive benchmarks with new `get_email` scenario
- Updated all docs, descriptions, and online listings for v0.1.7

## [0.1.6] - 2026-03-08

### Changed

- Hardened benchmark harness with error detection, probe screening, and crash guards
- Updated documentation and charts with corrected benchmark results
- Bumped `server.json` to 0.1.6

## [0.1.5] - 2026-03-06

### Added

- External attachment support (reads from `.mbox` sibling directories)
- Scan hardening for corrupt/oversized `.emlx` files
- Mailbox cap documentation and warnings

### Fixed

- Guard external attachment reads against oversized files
- Path traversal guard for attachment extraction

## [0.1.4] - 2026-03-04

### Fixed

- `.partial.emlx` file indexing
- Public API exports
- Attachment fidelity in parsed results
- Scan resilience for edge cases

## [0.1.3] - 2026-03-02

### Added

- Attachment support with FTS5 sanitizer rewrite
- 3-strategy `get_email()` cascade (specified mailbox, index lookup, iterate all)
- Schema v4 with `attachments` table

### Fixed

- Strategy 2 over-scoping by defaults
- Race-safe mtime sort
- FK pragma, `message_id` scoping, `exclude_mailboxes`

## [0.1.2] - 2026-02-28

### Added

- MCP Registry manifest (`server.json`)

### Fixed

- FTS5 search now respects account/mailbox filters (#4)
- FTS5 mailbox filter regression
- Async lock to prevent concurrent `ensure_loaded()` races

## [0.1.1] - 2026-02-25

### Added

- Documentation site (GitHub Pages)
- Competitive benchmarking suite against 7 Apple Mail MCP servers

## [0.1.0] - 2026-02-22

### Added

- Initial release
- Fast MCP server for Apple Mail with batch JXA (87x faster than naive iteration)
- FTS5 search index (700-3500x faster body search)
- 6 MCP tools: `list_accounts`, `list_mailboxes`, `get_emails`, `get_email`, `search`, `get_attachment`
- Disk-based sync for index building
- Real-time file watcher for index updates

[0.2.0]: https://github.com/imdinu/apple-mail-mcp/compare/v0.1.8...HEAD
[0.1.8]: https://github.com/imdinu/apple-mail-mcp/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/imdinu/apple-mail-mcp/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/imdinu/apple-mail-mcp/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/imdinu/apple-mail-mcp/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/imdinu/apple-mail-mcp/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/imdinu/apple-mail-mcp/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/imdinu/apple-mail-mcp/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/imdinu/apple-mail-mcp/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/imdinu/apple-mail-mcp/releases/tag/v0.1.0
