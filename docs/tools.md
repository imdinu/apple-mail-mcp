# Tools

Apple Mail MCP provides **8 MCP tools** — a consolidated API designed for AI assistants.

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
    The `attachments` list comes from JXA and only reports file attachments visible in Mail.app's UI. For reliable extraction (including inline images), use `get_attachment()`.

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
