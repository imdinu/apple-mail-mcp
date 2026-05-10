# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.2] - 2026-05-10

### Added

- **`--profile PATH` flag on `index` and `rebuild`** — wraps the operation in `cProfile` and writes a `pstats`-format dump to the given path. Stdlib-only (no new runtime dependencies). Intended for users diagnosing slow indexing on their own data and for sharing actionable performance traces in bug reports. Documented in `docs/profiling.md` along with recommended visualizers (`flameprof` for flame charts, `gprof2dot` for call graphs, `snakeviz` for interactive exploration). Surfaced in response to community feedback on #60 — wall-clock invariance at 100k+ mailbox scale was hard to diagnose without a contributor-friendly profiling path. `flameprof>=0.4` added to the `dev` dependency group so contributors get a flame-chart renderer on `uv sync`.
- **`docs/profiling.md`** — methodology page covering when to profile, how to capture, how to read the two halves of a flame chart (cumulative vs self-time), and what patterns in a profile signal which optimization strategies. Includes a reference breakdown from a real ~60k-message mailbox showing balanced overhead across the parse pipeline (no single dominant bottleneck on this dataset). Wired into the mkdocs nav.

### Fixed

- **`index://status` resource no longer walks the disk on every call** — `IndexManager.get_stats()` now caches `disk_email_count` with a 60-second TTL via a new `_get_disk_email_count_cached()` helper. Previously, every read of the `index://status` MCP resource triggered a full `get_disk_inventory()` filesystem walk under `~/Library/Mail/V*/`, which is O(N files) and would dominate response latency for clients polling the resource on a tight loop. The cache is automatically invalidated at the end of `build_from_disk()` and `sync_updates()` so the next status call after a sync reflects truth. Failures (`PermissionError`, `FileNotFoundError`) are deliberately *not* cached so subsequent calls retry in case Full Disk Access has since been granted. New public `invalidate_disk_count_cache()` method for callers that need to force a fresh read. (#78)
- **Memory allocation in `_estimate_attachment_size`** — replaced chained `raw.replace("\n", "").replace("\r", "").replace(" ", "")` with `str.count()` (allocation-free) followed by a bounded trailing-padding scan. Verified via `tracemalloc` on a 19.3 MB base64 payload: peak allocation drops from **38.4 MB → 19.3 MB** (2x reduction; new impl peak equals input size, allocating nothing additional). The original issue's "80 MB" estimate assumed all three `.replace()` calls would each allocate a full copy, but CPython short-circuits `str.replace` when no replacements occur — so the realistic peak is 2x not 4x. Still real, still GC-pressure-inducing during bulk indexing of attachment-heavy mailboxes. The fix preserves exact semantics — including the padding subtraction that the originally-proposed Option B (`int(len(raw) * 0.75)`) would have lost — verified by the existing `test_base64_size_estimation` test plus a new whitespace-heavy regression test. (#81)

## [0.3.1] - 2026-05-08

### Changed

- **`APPLE_MAIL_INDEX_MAX_EMAILS` is now uncapped by default** — the per-mailbox ceiling that silently truncated large mailboxes at 5000 messages is gone. The env var still works as an opt-in ceiling for users who want to bound disk/memory usage; setting it to an integer enforces the same per-mailbox limit as before. `get_index_max_emails()` returns `int | None` and all consumers (sync, build_from_disk, get_stats) treat `None` as no cap. The 5000 default predated disk-first sync (when JXA timed out at 60s and 5000 was the realistic indexable count); the bottleneck has long since moved, and the silent default no longer matched the README's "full-coverage body search" claim. Existing indexes built under the old default will *not* automatically backfill older messages — run `apple-mail-mcp rebuild` to re-index without the cap.
- **`apple-mail-mcp status` surfaces capped mailboxes** — when `APPLE_MAIL_INDEX_MAX_EMAILS` is set and any mailbox is at the ceiling, `status` now prints a "Capped: N mailbox(es)" line with a hint to raise or unset the env var. Previously this state was only visible via the `index://status` MCP resource.

### Documentation

- **Removed the "Migrating from apple-mcp?" README section** — the `supermemoryai/apple-mcp` migration table was kept for compatibility framing during the early v0.x cycle. The reference is no longer load-bearing for new users; dropping it tightens the README without losing information that's still findable in v0.3.0 release notes if needed.

## [0.3.0] - 2026-05-07

### Fixed

- **Watcher race during `build_from_disk()`** — in `IndexManager.build_from_disk` the FTS5 sync triggers were dropped for the bulk-insert pass and recreated *after* `rebuild_fts_index()`. Any concurrent INSERT (file watcher, separate process holding `--watch`) that landed between the FTS rebuild and the trigger recreation entered `emails` but never reached `emails_fts`, leaving the row permanently unsearchable. The trigger recreation now happens *before* the FTS rebuild — concurrent inserts during the rebuild fire the recreated trigger, and the rebuild itself re-syncs everything in `emails`, double-covering the window. (Surfaced via Gemini code review.)
- **Stale FTS5 entry auto-cleanup** — when `get_email()` Strategy 0 finds an indexed `.emlx` path that no longer exists on disk (the message was deleted or moved between syncs), the dead row is now removed from the index and a clear `"deleted or moved"` error is returned. Previously the cascade fell through to Strategy 3 and timed out (~1.3% of `get_email` calls in observed traffic). Adds `IndexManager.delete_email()` primitive. (#74)
- **Dead letter queue for `.emlx` parse failures** — files that fail to parse in the watcher or disk-sync paths are now recorded in a new `failed_index_jobs` table (path, account, mailbox, error type/message, first/last seen, attempt count). Previously such failures were swallowed silently after the v0.1.8 watcher hardening. Successful re-parses automatically clear the entry. Surfaced in `apple-mail-mcp status` and the `index://status` resource via a new `failed_jobs_count`. Schema bumped to v5 with a forward-only migration. (#58)
- **DLQ write failures now log at ERROR level** — when the `failed_index_jobs` INSERT itself fails (disk full, DB corruption, schema mismatch), both the watcher and disk-sync paths now log at ERROR with diagnostic context instead of swallowing silently (sync) or emitting WARNING (watcher). Surfaces operationally-significant failure modes that were previously invisible. (#77)

### Changed

- **`cyclopts` constraint relaxed to stable** — was `>=5.0.0a1` (pre-release), now `>=4.10`. Removes the need for `--prerelease=allow` in `claude_desktop_config.json` and other install configs. No API changes; the cyclopts surface used by `cli.py` is identical between 4.x and 5.x. (#75)
- **HTML stripping during indexing now uses selectolax** (lexbor C parser) for ~5x faster `_strip_html()` on realistic email HTML (5-25 KB body parts). BeautifulSoup is kept as a fallback if selectolax raises or fails to import. All existing XSS-bypass tests pass under both paths. New `selectolax>=0.4.8` dependency. (#59)
- **`sync_from_disk()` now uses a SQL temp table for diffing** instead of materializing the full disk and DB inventories as Python dicts. Memory at sync time stays flat (~2-3 MB delta) regardless of mailbox size; previously the dicts grew linearly to ~116 MB at 200K emails. Time cost is ~1.8x (sub-second even at 200K). Adds `iter_disk_inventory()` streaming variant of `get_disk_inventory()` in `disk.py`. All existing sync tests pass — behavior is preserved (added/deleted/moved counts, mtime sort, per-mailbox cap). (#60)

### Added

- **`index://status` MCP resource** — read-only JSON snapshot of the FTS5 search index (counts, size, last sync, staleness). Lets MCP clients assess index health without invoking a tool. (#12)
- **Benchmark suite expansion + refresh** — added `sweetrb/apple-mail-mcp` (TypeScript, AppleScript-based, 40+ tools, npm) and `BastianZim/apple-mail-mcp` (Python, reads Envelope Index SQLite + `.emlx` directly, no AppleScript) to the competitor list, then re-ran the full sweep on a 72K-message mailbox. Charts and the benchmarks doc are refreshed; positioning copy updated to reflect that the FTS5 differentiator is now precisely "full-coverage body search" — BastianZim implements a body parameter but caps live-scanning at the 5000 most recent messages (silent miss on older mail). Per-scenario charts now mark BastianZim as "5K cap" in the capability matrix and exclude it from the body-search bar chart so the comparison stays apples-to-apples.
- **`server.json` declares `runtimeHint: "uvx"`** — spec-compliant signal to MCP registries that the canonical launch command is `uvx apple-mail-mcp`. No effect on existing clients that already invoke the package directly.

### Documentation

- **Discovery descriptions refreshed** — `pyproject.toml`, `server.json`, and `mkdocs.yml` all now describe the project as "Apple Mail MCP server with full-coverage FTS5 body search. Reliable on large mailboxes where AppleScript-based servers timeout." Replaces the older "the only one that works reliably" wording, which the v0.3.0 bench refresh showed was no longer uniquely ours (BastianZim also handles large mailboxes — just with a 5000-message body-search cap).
- **Schema-version references updated to v5** across `CLAUDE.md`, `docs/architecture.md`, and `docs/search.md`.
- **New documentation sections** for the `index://status` MCP resource (`CLAUDE.md`, `docs/architecture.md`, `docs/tools.md`) and the `failed_index_jobs` DLQ (`docs/search.md`, `docs/troubleshooting.md`).

## [0.2.2] - 2026-04-13

### Added

- **Mailbox name alias resolution** — JXA `getMailbox()` now resolves common cross-provider aliases (e.g., `Sent Messages` → `Sent Items` on Outlook, `Trash` → `Deleted Items`) and falls back to case-insensitive matching. (#73)
- **Configurable Strategy 3 timeout** — Strategy 3 (iterate-all-mailboxes fallback in `get_email`) now exposes `APPLE_MAIL_STRATEGY3_TIMEOUT` and `APPLE_MAIL_STRATEGY3_MAX_MAILBOXES` environment variables.

### Fixed

- **Bare wildcard `*` query** — no longer crashes FTS5 with a syntax error. (#72)

### Tests

- Added watcher tests for noisy events, nested mbox, V11 directory layouts, and pending-changes limits.
- Added corrupt `.emlx` parser tests (bad byte counts, truncated content, empty files, missing newline). +10 tests; total 325 passing.

## [0.2.1] - 2026-04-05

### Added

- **Search pagination** — new `offset` parameter on `search()` for paginated results. Use with `limit` to page through large result sets. (#8)
- **Status command completeness** — `apple-mail-mcp status` now reports attachment count, disk email count, and index coverage percentage. (#43)

### Changed

- **Strategy 3 JXA moved to builders.py** — the inline JXA script for iterating all mailboxes is now a `GetEmailBuilder` class, consistent with the existing builder pattern. (#56)

### Fixed

- **Case-insensitive attachment filename matching** — `get_email_attachment` now matches filenames regardless of case or whitespace, fixing failures when LLM clients re-serialize filenames with minor differences. (#71)

## [0.2.0] - 2026-04-01

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

[0.3.2]: https://github.com/imdinu/apple-mail-mcp/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/imdinu/apple-mail-mcp/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/imdinu/apple-mail-mcp/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/imdinu/apple-mail-mcp/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/imdinu/apple-mail-mcp/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/imdinu/apple-mail-mcp/compare/v0.1.8...v0.2.0
[0.1.8]: https://github.com/imdinu/apple-mail-mcp/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/imdinu/apple-mail-mcp/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/imdinu/apple-mail-mcp/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/imdinu/apple-mail-mcp/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/imdinu/apple-mail-mcp/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/imdinu/apple-mail-mcp/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/imdinu/apple-mail-mcp/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/imdinu/apple-mail-mcp/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/imdinu/apple-mail-mcp/releases/tag/v0.1.0
