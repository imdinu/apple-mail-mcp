# Tools

Apple Mail MCP provides **11 MCP tools** — a consolidated API designed for AI assistants.

## Overview

| Tool | Purpose | Parameters |
|------|---------|------------|
| `list_accounts()` | List email accounts | — |
| `list_mailboxes()` | List mailboxes | `account?` |
| `get_emails()` | Get emails with filtering | `account?`, `mailbox?`, `filter?`, `limit?` |
| `get_email()` | Get single email with content + attachments | `message_id`, `account?`, `mailbox?` |
| `search()` | Search emails | `query`, `account?`, `mailbox?`, `scope?`, `limit?`, `exclude_mailboxes?`, `before?`, `after?`, `highlight?` |
| `get_email_links()` | Extract links from an email | `message_id`, `account?`, `mailbox?` |
| `get_email_attachment()` | Extract attachment content | `message_id`, `filename`, `account?`, `mailbox?` |
| `get_attachment()` | *Deprecated* — use `get_email_attachment()` | `message_id`, `filename`, `account?`, `mailbox?` |
| `update_email_status()` | **Write.** Mark read/unread, flag/unflag | `message_ids`, `read?`, `flagged?`, `account?`, `mailbox?` |
| `move_email()` | **Write.** Move / archive / trash | `message_ids`, `target_mailbox`, `account?`, `mailbox?` |
| `send_email()` | **Write.** Draft by default; send with `confirm` | `to`, `subject`, `body`, `cc?`, `bcc?`, `account?`, `confirm?` |

---

## `list_accounts()`

List all configured email accounts in Apple Mail.

**Parameters:** None

**Returns:** List of accounts with `name` and `id` fields.

```python
list_accounts()
# → [{"name": "Work", "id": "abc123"}, {"name": "Personal", "id": "def456"}]
```

---

## `list_mailboxes()`

List all mailboxes for an email account.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `account` | `string?` | env default | Account name |

**Returns:** List of mailboxes with `name` and `unreadCount` fields.

```python
list_mailboxes()
# → [{"name": "INBOX", "unreadCount": 5}, {"name": "Sent", "unreadCount": 0}]

list_mailboxes("Work")
# → [{"name": "INBOX", "unreadCount": 12}, ...]
```

---

## `get_emails()`

Get emails from a mailbox with optional filtering. This is the primary tool for listing emails.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `account` | `string?` | env default | Account name |
| `mailbox` | `string?` | `INBOX` | Mailbox name |
| `filter` | `string?` | `all` | Filter type (see below) |
| `limit` | `int?` | `50` | Max emails to return |

**Filters:**

| Filter | Description |
|--------|-------------|
| `all` | All emails (default) |
| `unread` | Only unread emails |
| `flagged` | Only flagged emails |
| `today` | Emails received today |
| `last_7_days` | Emails from the last 7 days |
| `this_week` | Alias for `last_7_days` |

**Returns:** List of email summaries sorted by date (newest first), each with: `id`, `subject`, `sender`, `date_received`, `read`, `flagged`.

```python
get_emails()
# All emails from default mailbox

get_emails(filter="unread", limit=10)
# 10 most recent unread emails

get_emails("Work", "INBOX", filter="today")
# Today's work emails
```

---

## `get_email()`

Get a single email with full content. Uses a 3-strategy cascade to find the message:

1. Try the specified mailbox directly
2. Look up the email's location in the FTS5 index
3. Iterate all mailboxes with per-mailbox error handling

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `message_id` | `int` | *required* | Email ID (from list/search results) |
| `account` | `string?` | env default | Helps find the message faster |
| `mailbox` | `string?` | `INBOX` | Helps find the message faster |

**Returns:** Full email with: `id`, `subject`, `sender`, `content` (full body text), `date_received`, `date_sent`, `read`, `flagged`, `reply_to`, `message_id` (RFC 822 Message-ID header), `attachments` (list of `{filename, mime_type, size}`).

```python
get_email(12345)
# → {"id": 12345, "subject": "Meeting notes", "content": "Hi team,...",
#    "attachments": [{"filename": "notes.pdf", "mime_type": "application/pdf", "size": 52340}], ...}
```

!!! tip
    If `account` and `mailbox` are not provided, the server searches all mailboxes in the default account to find the message.

!!! note
    The `attachments` list comes from JXA and only reports file attachments visible in Mail.app's UI. For reliable extraction (including inline images), use `get_email_attachment()`.

---

## `search()`

Search emails with automatic FTS5 optimization. Uses the FTS5 index for fast search (~2ms) when available, falls back to JXA-based search otherwise.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | `string` | *required* | Search term or phrase |
| `account` | `string?` | `None` | Account filter. `None` = search all (FTS) or default (JXA) |
| `mailbox` | `string?` | `None` | Mailbox filter. `None` = search all (FTS) or default (JXA) |
| `scope` | `string?` | `all` | Search scope (see below) |
| `limit` | `int?` | `20` | Max results |
| `offset` | `int?` | `0` | Skip first N results (for pagination) |
| `exclude_mailboxes` | `list?` | `["Drafts"]` | Mailboxes to exclude (FTS/attachment scopes only) |
| `before` | `string?` | `None` | Only return emails before this date (YYYY-MM-DD) |
| `after` | `string?` | `None` | Only return emails after this date (YYYY-MM-DD) |
| `highlight` | `bool?` | `False` | Highlight matching terms in results |

**Scopes:**

| Scope | Searches | Engine |
|-------|----------|--------|
| `all` | Subject + sender + body | FTS5 (if indexed) |
| `subject` | Subject line only | FTS5 column filter (if indexed) |
| `sender` | Sender field only | FTS5 column filter (if indexed) |
| `body` | Body content only | FTS5 (if indexed) |
| `attachments` | Attachment filenames | SQL (requires index) |

**Returns:** List of results sorted by relevance (FTS5) or date (JXA fallback), each with: `id`, `subject`, `sender`, `date_received`, `score`, `matched_in`, and optionally `content_snippet`, `account`, `mailbox`.

**When nothing is found**, the return is `{"result": [], "hint": "..."}` instead of a list. The hint distinguishes three different situations, so a caller never mistakes an unusable index for an empty mailbox:

| Situation | What the hint says |
|-----------|--------------------|
| Genuine zero match | Try fewer keywords, check spelling, widen the scope |
| No index built | Subject and sender were searched live; **body text was not**. Names the index path and `apple-mail-mcp index` |
| Index exists but holds 0 emails | The build could not read your mail — names the index path and the Full Disk Access prerequisite |

**Scopes only the index can serve raise instead of returning empty**: `scope="body"`, `scope="attachments"`, and any `before`/`after` filter fail with a message naming the index path and how to build it. Subject and sender searches keep their live fallback.

```python
search("invoice")
# Search everywhere — uses FTS5 for instant results

search("john@example.com", scope="sender")
# Find emails from a specific sender

search("meeting notes", scope="body")
# Search body content only

search("pdf", scope="attachments")
# Find emails with PDF attachments

search("deadline", limit=5)
# Top 5 results

search("invoice", after="2025-01-01", before="2025-12-31")
# Emails from 2025 only

search("meeting", limit=20, offset=20)
# Page 2 of results (skip first 20)

search("meeting", highlight=True)
# Results with matching terms highlighted
```

---

## `get_email_links()`

Extract all links (URLs) from an email's body content.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `message_id` | `int` | *required* | Email ID |
| `account` | `string?` | `None` | Account (helps disambiguate) |
| `mailbox` | `string?` | `None` | Mailbox (helps disambiguate) |

**Returns:** List of links found in the email body.

```python
get_email_links(12345)
# → [{"url": "https://example.com/invoice", "text": "View Invoice"}, ...]
```

---

## `get_email_attachment()`

Extract attachment content from an email. Parses the raw `.emlx` MIME structure, so it works for all attachment types including inline images.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `message_id` | `int` | *required* | Email ID |
| `filename` | `string` | *required* | Attachment filename to extract |
| `account` | `string?` | `None` | Account (helps disambiguate) |
| `mailbox` | `string?` | `None` | Mailbox (helps disambiguate) |

**Returns:** Dictionary with `filename`, `mime_type`, `size`, and `content_base64`. If the attachment exceeds 10 MB, returns metadata only with `truncated: true`.

```python
get_email_attachment(12345, "invoice.pdf")
# → {"filename": "invoice.pdf", "mime_type": "application/pdf",
#    "size": 52340, "content_base64": "JVBERi0x..."}
```

!!! note
    Requires the FTS5 search index. If upgrading from v0.1.x, run `apple-mail-mcp rebuild` to populate attachment metadata.

---

## `get_attachment()` *(Deprecated)*

!!! warning
    `get_attachment()` is deprecated since v0.2.0. Use `get_email_attachment()` instead. The old name still works but may be removed in a future release.

Identical to `get_email_attachment()`. See above for parameters and return value.

---

## Write tools

The three tools below mutate Mail.app state. They share one contract:

- **Off in read-only mode** — `APPLE_MAIL_READ_ONLY=true`, `[server] read_only = true`, or `apple-mail-mcp serve -r` makes every write tool raise `PermissionError` before touching Mail.app.
- **Hidden accounts stay hidden** — an account listed in `exclude_accounts` is reported as not found; the default account is never silently substituted with a hidden one.
- **Bounded batches** — `message_ids` is deduplicated and clamped to 10 ids per call; extra ids are dropped, an empty list is an error.
- **The result is the new state**, not `{"success": true}`, so the caller can verify what Mail.app reports after the change.

---

## `update_email_status()`

Mark messages read/unread and/or flagged/unflagged. Pass only the flags you want to change.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `message_ids` | `list[int]` | *required* | Message IDs (≤ 10) |
| `read` | `bool?` | `None` | `True` marks read, `False` unread, `None` unchanged |
| `flagged` | `bool?` | `None` | `True` flags, `False` unflags, `None` unchanged |
| `account` | `string?` | env default | Account name |
| `mailbox` | `string?` | `INBOX` | Mailbox holding the messages |

**Returns:** One `{"id", "read", "flagged"}` per message, as reported by Mail.app after the update. Read/flagged flags live in the Envelope Index and the `.emlx` plist footer, not in the FTS5 index, so no index write happens.

```python
update_email_status([12345, 12346], read=True)
# → [{"id": 12345, "read": true, "flagged": false}, ...]
update_email_status([12345], flagged=True, read=False)
```

---

## `move_email()`

Move messages to another mailbox. Archiving and trashing are just moves to `Archive` / `Trash`.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `message_ids` | `list[int]` | *required* | Message IDs (≤ 10) |
| `target_mailbox` | `string` | *required* | Destination — `Archive`, `Trash`, `Work/Projects`, … (alias-aware) |
| `account` | `string?` | env default | Account name |
| `mailbox` | `string?` | `INBOX` | Source mailbox holding the messages |

**Returns:** One `{"id", "account", "mailbox"}` per message with its new location. The target is resolved before any message moves; an unknown target raises `ValueError` and nothing changes.

**Index coherence:** once Mail.app confirms the move, the stale FTS5 row for the source mailbox is evicted immediately so a follow-up `search()` cannot return a ghost result. The file watcher (or the next sync) indexes the message under its new mailbox.

```python
move_email([12345], "Archive")
# → [{"id": 12345, "account": "iCloud", "mailbox": "Archive"}]
move_email([12345, 12346], "Trash")
```

---

## `send_email()`

Compose an email. **Saves a draft by default**; only `confirm=True` transmits.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `to` | `list[string]` | *required* | Recipient addresses (≥ 1) |
| `subject` | `string` | *required* | Subject line |
| `body` | `string` | *required* | Plain-text body |
| `cc` | `list[string]?` | `None` | CC addresses |
| `bcc` | `list[string]?` | `None` | BCC addresses |
| `account` | `string?` | env default | Account to send from (its primary address is the sender) |
| `confirm` | `bool` | `False` | `False` saves a draft in Mail.app; `True` sends now |

**Returns:** `{"status": "draft" | "sent", "account", "to", "cc", "bcc", "subject"}`.

```python
send_email(["a@example.com"], "Lunch?", "Thursday works for me.")
# → {"status": "draft", ...}   # review it in Mail.app's Drafts
send_email(["a@example.com"], "Lunch?", "Thursday works for me.", confirm=True)
# → {"status": "sent", ...}
```

!!! warning
    `confirm=True` sends immediately with no further prompt. Agents should surface the draft to the user and only confirm on explicit approval.

---

## MCP Resources

Tools are model-invoked (the LLM calls them). **Resources** are typically client-polled — read-only data the MCP client can pull as context without an LLM round-trip.

### `index://status` *(added v0.3.0)*

Read-only JSON snapshot of FTS5 search-index health. Lets clients render an "index OK" indicator or surface staleness without invoking a tool.

**MIME type:** `application/json`

**Payload (when index exists):**

| Field | Type | Description |
|-------|------|-------------|
| `has_index` | `bool` | Always `true` when index file is present |
| `email_count` | `int` | Number of indexed emails |
| `mailbox_count` | `int` | Number of distinct (account, mailbox) pairs |
| `attachment_count` | `int` | Total attachment metadata rows |
| `disk_email_count` | `int?` | Total `.emlx` files on disk (best-effort; `null` if Full Disk Access denied) |
| `db_size_mb` | `float` | Size of index DB on disk, rounded to 0.01 MB |
| `capped_mailboxes` | `int` | Number of mailboxes that hit `APPLE_MAIL_INDEX_MAX_EMAILS` cap |
| `failed_jobs_count` | `int` | Rows in the dead-letter queue (`.emlx` parses that failed) |
| `last_sync` | `string?` | ISO-8601 of last sync, or `null` if never synced |
| `staleness_hours` | `float?` | Hours since `last_sync`, rounded to 0.01 |

**Payload (when no index):**

```json
{
  "has_index": false,
  "message": "No index found. Run 'apple-mail-mcp index' to build it."
}
```
