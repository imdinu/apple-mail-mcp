# Troubleshooting

Common issues and their solutions.

## Full Disk Access

**Symptom:** `apple-mail-mcp index` fails with permission errors, or the index has 0 emails.

**Cause:** The indexer reads `.emlx` files from `~/Library/Mail/V10/`, which macOS protects.

**Fix:**

1. Open **System Settings**
2. Go to **Privacy & Security → Full Disk Access**
3. Add and enable your terminal app (Terminal.app, iTerm2, Warp, etc.)
4. **Restart your terminal** (required for changes to take effect)

!!! note
    The MCP server itself does **not** need Full Disk Access — only the `index` and `rebuild` commands do. Once the index is built, the server uses disk-based sync which works without FDA.

## Empty Search Results

**Symptom:** `search()` returns no results for queries you know should match.

**Possible causes:**

1. **No index built yet.** Run `apple-mail-mcp index --verbose` first. Without the index, only JXA-based search is available (limited to a single mailbox).

2. **Too many keywords.** FTS5 uses AND semantics — all terms must match. Use 2–3 specific keywords instead of full sentences.

    ```
    Bad:  "Can you find the email about the quarterly budget meeting?"
    Good: "quarterly budget"
    ```

3. **Index is stale.** Check with `apple-mail-mcp status`. If the index is old, run `apple-mail-mcp rebuild` or start the server with `--watch` for real-time updates.

4. **Mailbox excluded.** By default, `Drafts` is excluded from indexing. Check `APPLE_MAIL_INDEX_EXCLUDE_MAILBOXES` (env) or `[index] exclude_mailboxes` in `~/.apple-mail-mcp/config.toml`.

## Startup Timeout (v0.1.5 and earlier)

**Symptom:** The MCP server hangs for 60+ seconds on startup, or times out entirely. Common with large mailboxes (100K+ emails).

**Cause:** In v0.1.5 and earlier, the startup sync was **blocking** — the server waited for the full index reconciliation before accepting tool calls.

**Fix:** Upgrade to v0.1.6+, which runs sync in a **background thread**. The server starts immediately and search results become available within seconds as the sync completes.

```bash
pipx upgrade apple-mail-mcp
```

## Index Rebuild After Upgrade

**Symptom:** After upgrading, search returns unexpected results or `get_attachment()` doesn't work.

**Cause:** Schema changes between versions (e.g., v0.1.3 added attachment metadata in schema v4; v0.3.0 added the failed-parse DLQ in schema v5). Migrations are forward-only and run automatically; a manual rebuild is only needed if existing rows lack new columns (attachments, paths).

**Fix:**

```bash
apple-mail-mcp rebuild
```

This drops and recreates the index from scratch.

## Config File Errors (v0.4.0+)

**Symptom:** The server refuses to start with an error like
`config.toml: TOML syntax error: ...`, `unknown key`,
`expected str`, or `unsupported config_version`.

**Cause:** `~/.apple-mail-mcp/config.toml` exists but doesn't validate
against the schema. The loader fails loud on syntax errors, unknown
keys (typos like `mailboxes` vs `mailbox`), type mismatches, and
unsupported `config_version` values — refusing to start beats
silently using degraded config.

**Fix:**

- Read the error message — it includes the file path and the
  specific key that failed.
- For a typo, correct it and restart the server.
- To start over from a clean template, overwrite the file:

    ```bash
    apple-mail-mcp init --force
    ```

    This writes a commented template documenting every available key.

- If you see `unsupported config_version`, your config was written
  by a newer version of the server than the one currently installed.
  Either upgrade `apple-mail-mcp` or hand-edit `config_version` back
  to your installed version's schema.

## Failed Parse Counter ("Failed parse: N (.emlx files in DLQ)")

**Symptom:** `apple-mail-mcp status` shows a non-zero `Failed parse:` line, or the `index://status` MCP resource reports `failed_jobs_count > 0`.

**Cause:** One or more `.emlx` files couldn't be parsed during sync or by the live watcher (corrupt content, unsupported MIME structure, disk read errors, etc.). They're recorded in the DLQ (`failed_index_jobs` table) so operators have visibility into what's missing from the index.

**Fix options:**

- **Wait for self-healing.** Successful re-parses clear DLQ entries automatically. If the cause was transient (Mail.app was mid-writing the file), the next watcher tick will resolve it.
- **Inspect the DLQ** to see error types:
  ```sql
  -- ~/.apple-mail-mcp/index.db
  SELECT emlx_path, error_type, error_message, attempt_count
  FROM failed_index_jobs
  ORDER BY last_seen DESC;
  ```
- **Force a retry** by rebuilding the index: `apple-mail-mcp rebuild`. This re-parses every `.emlx` from disk; entries that succeed are removed from the DLQ.
- **DLQ writes themselves failing** (logged at `ERROR` level with `"DLQ write failed"`): indicates a deeper problem — disk full or DB corruption. Check disk space and SQLite integrity (`PRAGMA integrity_check;`).

## Mail.app Not Running

**Symptom:** JXA-fallback tools (`list_mailboxes`, `get_email` cascade strategies 1–3, cold `list_accounts()` calls, and `get_emails()` when the Envelope Index path is unavailable) fail with AppleScript errors.

**Cause:** When the JXA fallback runs, Apple Mail must be running so `osascript` can communicate with it.

**Fix:** Open Mail.app. It can be minimized — it just needs to be running.

!!! tip
    As of 0.4, `list_accounts()` serves repeat calls from a cache (no JXA round-trip for ~5 min after the first call), and `get_emails()` reads Apple's Envelope Index SQLite directly when accessible (`~/Library/Mail/V*/MailData/Envelope Index`) — both work without Mail.app running. JXA only enters the picture on the cold `list_accounts()` call (to seed the account-name cache) and as a correctness fallback if the Envelope Index can't be read. FTS5-based search (`search()` with scope `all`, `subject`, `sender`, or `body`) also works fully offline since it queries the local SQLite index.

## `osascript` Errors

**Symptom:** Errors mentioning `osascript` or "script execution timed out."

**Possible causes:**

1. **Large mailbox.** Operations on mailboxes with thousands of messages can be slow via JXA. Use `limit` to restrict results:

    ```
    get_emails(limit=20)
    ```

2. **Mail.app is busy.** If Mail is syncing or processing rules, JXA calls may time out. Wait and retry.

3. **macOS permission prompt.** The first time `osascript` accesses Mail, macOS may show a permission dialog. Check for any pending prompts.
