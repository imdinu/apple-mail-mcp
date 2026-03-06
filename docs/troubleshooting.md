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

4. **Mailbox excluded.** By default, `Drafts` is excluded from search. Check `APPLE_MAIL_INDEX_EXCLUDE_MAILBOXES` in your configuration.

## Startup Timeout (v0.1.5 and earlier)

**Symptom:** The MCP server hangs for 60+ seconds on startup, or times out entirely. Common with large mailboxes (100K+ emails).

**Cause:** In v0.1.5 and earlier, the startup sync was **blocking** — the server waited for the full index reconciliation before accepting tool calls.

**Fix:** Upgrade to v0.1.6+, which runs sync in a **background thread**. The server starts immediately and search results become available within seconds as the sync completes.

```bash
pipx upgrade apple-mail-mcp
```

## Index Rebuild After Upgrade

**Symptom:** After upgrading, search returns unexpected results or `get_attachment()` doesn't work.

**Cause:** Schema changes between versions (e.g., v0.1.3 added attachment metadata in schema v4).

**Fix:**

```bash
apple-mail-mcp rebuild
```

This drops and recreates the index from scratch.

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
