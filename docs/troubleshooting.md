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

4. **Index scope excludes it.** By default, `Drafts` is excluded from the index. Also check `APPLE_MAIL_INDEX_EXCLUDE_ACCOUNTS`, `APPLE_MAIL_INDEX_INCLUDE_MAILBOXES`, and `APPLE_MAIL_INDEX_EXCLUDE_MAILBOXES` in your configuration.

## Account Exclusion Fails During Indexing

**Symptom:** `apple-mail-mcp index` or `apple-mail-mcp rebuild` reports that it
could not resolve `APPLE_MAIL_INDEX_EXCLUDE_ACCOUNTS`.

**Cause:** Friendly account names must be resolved to Mail's on-disk account
UUIDs before indexing starts. If Mail account metadata is unavailable, the
indexer fails closed instead of risking indexing an account you meant to
exclude.

**Fix:** Run `apple-mail-mcp accounts` and set
`APPLE_MAIL_INDEX_EXCLUDE_ACCOUNTS` to the account UUID, or make sure Mail's
account metadata is available in the environment where you run the indexer.

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

**Symptom:** JXA-based tools (`list_accounts`, `list_mailboxes`, `get_emails`, `get_email`) fail with errors.

**Cause:** Apple Mail must be running for JXA (JavaScript for Automation) to communicate with it.

**Fix:** Open Mail.app. It can be minimized — it just needs to be running.

!!! tip
    FTS5-based search (`search()` with scope `all`, `subject`, `sender`, or `body`) works even when Mail.app is closed, since it queries the local SQLite index.

## `osascript` Errors

**Symptom:** Errors mentioning `osascript` or "script execution timed out."

**Possible causes:**

1. **Large mailbox.** Operations on mailboxes with thousands of messages can be slow via JXA. Use `limit` to restrict results:

    ```
    get_emails(limit=20)
    ```

2. **Mail.app is busy.** If Mail is syncing or processing rules, JXA calls may time out. Wait and retry.

3. **macOS permission prompt.** The first time `osascript` accesses Mail, macOS may show a permission dialog. Check for any pending prompts.
