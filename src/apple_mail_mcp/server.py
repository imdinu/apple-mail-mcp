"""
Apple Mail MCP Server

Provides MCP tools for interacting with Apple Mail via optimized JXA scripts.
Uses batch property fetching for 87x faster performance.
Includes FTS5 search index for 700-3500x faster body search.

TOOLS (6 total):
- list_accounts() - List email accounts
- list_mailboxes(account?) - List mailboxes
- get_emails(..., filter?) - Unified email listing with filters
- get_email(id) - Get single email with content
- search(query, ...) - Unified search with FTS5 support
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import time
from pathlib import Path as _Path
from typing import Literal, TypedDict

from fastmcp import FastMCP

from .builders import AccountsQueryBuilder, QueryBuilder
from .config import get_default_account, get_default_mailbox
from .executor import (
    build_mailbox_setup_js,
    execute_query_async,
    execute_with_core_async,
)

mcp = FastMCP("Apple Mail")

# Attachment cache directory
ATTACHMENT_CACHE_DIR = _Path.home() / ".apple-mail-mcp" / "attachments"


def _cleanup_old_attachments(max_age_hours: int = 24) -> None:
    """Remove attachment files older than max_age_hours."""
    if not ATTACHMENT_CACHE_DIR.exists():
        return
    cutoff = time.time() - (max_age_hours * 3600)
    for subdir in ATTACHMENT_CACHE_DIR.iterdir():
        if subdir.is_dir():
            try:
                if subdir.stat().st_mtime < cutoff:
                    shutil.rmtree(subdir)
            except OSError:
                pass


# Strategy 3 safety limits for get_email's all-mailbox scan
STRATEGY3_TIMEOUT = 15  # seconds
STRATEGY3_MAX_MAILBOXES = 50


# ========== Response Type Definitions ==========


class Account(TypedDict):
    """An email account in Apple Mail."""

    name: str
    id: str


class Mailbox(TypedDict):
    """A mailbox within an email account."""

    name: str
    unreadCount: int


class EmailSummary(TypedDict):
    """Summary of an email (used in list/search results)."""

    id: int
    subject: str
    sender: str
    date_received: str
    read: bool
    flagged: bool


class SearchResult(TypedDict, total=False):
    """Result from search operations."""

    id: int
    subject: str
    sender: str
    date_received: str
    score: float
    matched_in: str
    content_snippet: str
    account: str
    mailbox: str


class AttachmentSummary(TypedDict):
    """Summary of an email attachment."""

    filename: str
    mime_type: str
    size: int


class EmailFull(TypedDict, total=False):
    """Complete email with full content."""

    id: int
    subject: str
    sender: str
    content: str
    date_received: str
    date_sent: str
    read: bool
    flagged: bool
    reply_to: str
    message_id: str
    attachments: list[AttachmentSummary]


# ========== Helper Functions ==========


def _get_index_manager():
    """Get the IndexManager singleton, lazily imported."""
    from .index import IndexManager

    return IndexManager.get_instance()


def _get_account_map():
    """Get the AccountMap singleton, lazily imported."""
    from .index.accounts import AccountMap

    return AccountMap.get_instance()


def _resolve_account(account: str | None) -> str | None:
    """Resolve account, using default from env if not specified."""
    return account if account is not None else get_default_account()


def _resolve_mailbox(mailbox: str | None) -> str:
    """Resolve mailbox, using default from env if not specified."""
    return mailbox if mailbox is not None else get_default_mailbox()


def _detect_matched_columns(query: str, result) -> str:
    """Delegate to search.detect_matched_columns."""
    from .index.search import detect_matched_columns

    return detect_matched_columns(query, result)


# ========== MCP Tools (6 total) ==========


@mcp.tool
async def list_accounts() -> list[Account]:
    """
    List all configured email accounts in Apple Mail.

    Returns:
        List of account dictionaries with 'name' and 'id' fields.

    Example:
        >>> list_accounts()
        [{"name": "Work", "id": "abc123"}, {"name": "Personal", "id": "def456"}]
    """
    script = AccountsQueryBuilder().list_accounts()
    accounts = await execute_with_core_async(script)

    # Seed the account name↔UUID cache for search filtering
    _get_account_map().load_from_jxa(accounts)

    return accounts


@mcp.tool
async def list_mailboxes(account: str | None = None) -> list[Mailbox]:
    """
    List all mailboxes for an email account.

    Args:
        account: Account name. Uses APPLE_MAIL_DEFAULT_ACCOUNT env var or
                 first account if not specified.

    Returns:
        List of mailbox dictionaries with 'name' and 'unreadCount' fields.

    Example:
        >>> list_mailboxes("Work")
        [{"name": "INBOX", "unreadCount": 5}, ...]
    """
    script = AccountsQueryBuilder().list_mailboxes(_resolve_account(account))
    return await execute_with_core_async(script)


@mcp.tool
async def get_emails(
    account: str | None = None,
    mailbox: str | None = None,
    filter: Literal["all", "unread", "flagged", "today", "this_week"] = "all",
    limit: int = 50,
) -> list[EmailSummary]:
    """
    Get emails from a specific mailbox with optional filtering.

    Note: This tool lists emails from a single mailbox. To search
    across all mailboxes, use the search() tool instead.

    Args:
        account: Account name. Uses APPLE_MAIL_DEFAULT_ACCOUNT env var or
                 first account if not specified.
        mailbox: Mailbox name. Uses APPLE_MAIL_DEFAULT_MAILBOX env var or
                 "Inbox" if not specified.
        filter: Filter type:
            - "all": All emails (default)
            - "unread": Only unread emails
            - "flagged": Only flagged emails
            - "today": Emails received today
            - "this_week": Emails received in the last 7 days
        limit: Maximum number of emails to return (default: 50)

    Returns:
        List of email dictionaries sorted by date (newest first).

    Examples:
        >>> get_emails()  # All emails from default mailbox
        >>> get_emails(filter="unread", limit=10)  # Unread emails
        >>> get_emails("Work", "INBOX", filter="today")  # Today's work emails
    """
    query = (
        QueryBuilder()
        .from_mailbox(_resolve_account(account), _resolve_mailbox(mailbox))
        .select("standard")
    )

    # Apply filter
    if filter == "unread":
        query = query.where("data.readStatus[i] === false")
    elif filter == "flagged":
        query = query.where("data.flaggedStatus[i] === true")
    elif filter == "today":
        query = query.where("data.dateReceived[i] >= MailCore.today()")
    elif filter == "this_week":
        query = query.where("data.dateReceived[i] >= MailCore.daysAgo(7)")

    query = query.order_by("date_received", descending=True).limit(limit)

    return await execute_query_async(query)


def _build_attachment_js() -> str:
    """Return JXA snippet to extract attachment metadata from `msg`."""
    return """
let attachments = [];
try {
    const atts = msg.mailAttachments();
    if (atts && atts.length > 0) {
        for (let a of atts) {
            try {
                attachments.push({
                    filename: a.name(),
                    mime_type: a.mimeType() || 'application/octet-stream',
                    size: a.fileSize() || 0
                });
            } catch(ae) {}
        }
    }
} catch(e) {}
"""


def _build_get_email_script(message_id: int, mailbox_setup: str) -> str:
    """Build JXA script to fetch a single email by ID.

    Extracted to avoid duplication between the primary and
    fallback fetch strategies.
    """
    att_js = _build_attachment_js()
    return f"""
const targetId = {message_id};
let msg = null;
{mailbox_setup}

const ids = mailbox.messages.id();
const idx = ids.indexOf(targetId);
if (idx !== -1) {{
    msg = mailbox.messages[idx];
}}

if (!msg) {{
    throw new Error('Message not found with ID: ' + targetId);
}}

{att_js}

JSON.stringify({{
    id: msg.id(),
    subject: msg.subject(),
    sender: msg.sender(),
    content: msg.content(),
    date_received: MailCore.formatDate(msg.dateReceived()),
    date_sent: MailCore.formatDate(msg.dateSent()),
    read: msg.readStatus(),
    flagged: msg.flaggedStatus(),
    reply_to: msg.replyTo(),
    message_id: msg.messageId(),
    attachments: attachments
}});
"""


@mcp.tool
async def get_email(
    message_id: int,
    account: str | None = None,
    mailbox: str | None = None,
) -> EmailFull:
    """
    Get a single email with full content.

    Looks up the email across all accounts using a cascade strategy.
    Just pass the message_id — account/mailbox are optional hints
    that speed up lookup but are not required.

    Args:
        message_id: The email's unique ID (from search results)
        account: Optional hint (speeds up lookup, not required)
        mailbox: Optional hint (speeds up lookup, not required)

    Returns:
        Email dictionary with full content including:
        - id, subject, sender, date_received, date_sent
        - content: Full plain text body
        - read, flagged status
        - reply_to, message_id (email Message-ID header)
        - attachments: List of {filename, mime_type, size}

    Note:
        The attachments list comes from JXA's mailAttachments(),
        which only reports file attachments visible in Mail.app's
        UI. Inline images, S/MIME signatures, and attachments in
        sent/bounce-back emails may not appear. Use get_attachment
        with a known filename for reliable extraction from disk.

    Example:
        >>> get_email(12345)
        {"id": 12345, "subject": "Meeting notes",
         "content": "Hi team,\\n\\nHere are the notes...", ...}
    """
    resolved_account = _resolve_account(account)
    resolved_mailbox = _resolve_mailbox(mailbox)

    def _enrich_attachments(result: dict) -> dict:
        """Replace JXA attachments with richer index data when available."""
        try:
            mgr = _get_index_manager()
            if mgr.has_index():
                idx_atts = mgr.get_email_attachments(message_id)
                if idx_atts and len(idx_atts) > len(
                    result.get("attachments", [])
                ):
                    result["attachments"] = idx_atts
        except Exception:
            pass
        return result

    # Strategy 1: Try specified mailbox
    mailbox_setup = build_mailbox_setup_js(resolved_account, resolved_mailbox)
    script = _build_get_email_script(message_id, mailbox_setup)

    try:
        result = await execute_with_core_async(script)
        return _enrich_attachments(result)
    except Exception:
        pass  # Fall through to strategy 2

    # Strategy 2: Index lookup — find the email's real location
    # Only scope by account/mailbox when the caller explicitly provided them
    # (not when they were filled in from defaults — strategy 1 already tried
    # the default location and failed).
    try:
        manager = _get_index_manager()
        if manager.has_index():
            acct_map = _get_account_map()
            await acct_map.ensure_loaded()

            idx_acct = None
            if account is not None:
                idx_acct = acct_map.name_to_uuid(account)
            idx_mb = mailbox

            location = manager.find_email_location(
                message_id, account=idx_acct, mailbox=idx_mb
            )
            if location:
                idx_account, idx_mailbox = location
                friendly_account = acct_map.uuid_to_name(idx_account)

                setup = build_mailbox_setup_js(friendly_account, idx_mailbox)
                script = _build_get_email_script(message_id, setup)
                try:
                    result = await execute_with_core_async(script)
                    return _enrich_attachments(result)
                except Exception:
                    pass  # Fall through to strategy 3
    except Exception:
        pass  # Index unavailable, fall through

    # Strategy 3: Iterate all mailboxes with per-mailbox error handling
    # Guarded with a timeout and mailbox limit to prevent runaway scans
    acct_setup = (
        f"const account = Mail.accounts.byName({json.dumps(resolved_account)});"
        if resolved_account
        else "const account = Mail.accounts[0];"
    )
    att_js = _build_attachment_js()
    script = f"""
const targetId = {message_id};
let msg = null;
{acct_setup}

const allMailboxes = account.mailboxes();
const mbLimit = Math.min(allMailboxes.length, {STRATEGY3_MAX_MAILBOXES});
for (let i = 0; i < mbLimit && !msg; i++) {{
    try {{
        const mb = allMailboxes[i];
        const mbIds = mb.messages.id();
        const mbIdx = mbIds.indexOf(targetId);
        if (mbIdx !== -1) {{
            msg = mb.messages[mbIdx];
        }}
    }} catch(e) {{
        // Skip inaccessible mailboxes (Junk/Drafts -1728)
    }}
}}

if (!msg) {{
    throw new Error('Message not found with ID: ' + targetId);
}}

{att_js}

JSON.stringify({{
    id: msg.id(),
    subject: msg.subject(),
    sender: msg.sender(),
    content: msg.content(),
    date_received: MailCore.formatDate(msg.dateReceived()),
    date_sent: MailCore.formatDate(msg.dateSent()),
    read: msg.readStatus(),
    flagged: msg.flaggedStatus(),
    reply_to: msg.replyTo(),
    message_id: msg.messageId(),
    attachments: attachments
}});
"""
    try:
        result = await execute_with_core_async(
            script, timeout=STRATEGY3_TIMEOUT
        )
        return _enrich_attachments(result)
    except TimeoutError:
        raise TimeoutError(
            f"Strategy 3 timed out after {STRATEGY3_TIMEOUT}s "
            f"searching up to {STRATEGY3_MAX_MAILBOXES} mailboxes "
            f"for message {message_id}. "
            f"Provide account/mailbox for faster lookup."
        ) from None


class LinkResult(TypedDict):
    """A hyperlink extracted from an email."""

    url: str
    text: str


class AttachmentContent(TypedDict, total=False):
    """Content returned by get_attachment."""

    filename: str
    mime_type: str
    size: int
    file_path: str
    links: list[LinkResult]


@mcp.tool
async def get_attachment(
    message_id: int,
    filename: str | None = None,
    account: str | None = None,
    mailbox: str | None = None,
) -> AttachmentContent:
    """
    Extract resources from an email: attachments or links.

    This tool has two modes based on the filename parameter:

    **Attachment mode** (filename provided):
    Extracts the named file attachment and saves it to disk under
    ~/.apple-mail-mcp/attachments/. Parses the raw MIME structure,
    so it works for all attachment types including inline images
    and S/MIME signatures.

    **Links mode** (filename omitted):
    Extracts all hyperlinks from the email's HTML content.
    Filters out mailto:, javascript:, and long tracking URLs.
    Returns deduplicated links with their anchor text.

    Requires the search index. If upgrading from v0.1.2, run
    'apple-mail-mcp rebuild' to populate attachment metadata.

    Args:
        message_id: The email's unique ID
        filename: Attachment filename to extract. If omitted,
            returns links instead.
        account: Account name (optional, used for index lookup)
        mailbox: Mailbox name (optional, used for index lookup)

    Returns:
        With filename: dict with filename, mime_type, size,
            and file_path pointing to the saved file.
        Without filename: dict with links list, each having
            url and text fields.

    Examples:
        >>> get_attachment(12345, "invoice.pdf")
        {"filename": "invoice.pdf", ...}
        >>> get_attachment(12345)
        {"links": [{"url": "https://...", "text": "Click"}]}
    """
    # Look up emlx_path from the index, scoped by account/mailbox
    # when provided (message_id is only unique within a mailbox)
    manager = _get_index_manager()
    if not manager.has_index():
        raise ValueError("No search index. Run 'apple-mail-mcp index'.")

    idx_acct = None
    if account:
        acct_map = _get_account_map()
        await acct_map.ensure_loaded()
        idx_acct = acct_map.name_to_uuid(account) or account

    emlx_path = manager.find_email_path(
        message_id, account=idx_acct, mailbox=mailbox
    )
    if not emlx_path:
        raise ValueError(f"Email {message_id} not found in index.")

    # Links mode: extract hyperlinks from HTML parts
    if filename is None:
        from .index.disk import get_email_links

        link_infos = await asyncio.to_thread(get_email_links, emlx_path)
        return {
            "links": [{"url": li.url, "text": li.text} for li in link_infos],
        }

    # Attachment mode: extract and save file
    from .index.disk import get_attachment_content

    result = await asyncio.to_thread(
        get_attachment_content, emlx_path, filename
    )
    if result is None:
        raise ValueError(
            f"Attachment '{filename}' not found in email {message_id}."
        )

    raw_bytes, mime_type = result

    # Save to unique subdirectory
    ATTACHMENT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    save_dir = _Path(tempfile.mkdtemp(dir=ATTACHMENT_CACHE_DIR))
    safe_name = _Path(filename).name  # strip directory components
    file_path = save_dir / safe_name
    file_path.write_bytes(raw_bytes)

    return {
        "filename": safe_name,
        "mime_type": mime_type,
        "size": len(raw_bytes),
        "file_path": str(file_path),
    }


@mcp.tool
async def search(
    query: str,
    account: str | None = None,
    mailbox: str | None = None,
    scope: Literal["all", "subject", "sender", "body", "attachments"] = "all",
    limit: int = 20,
    exclude_mailboxes: list[str] | None = None,
) -> list[SearchResult] | dict:
    """
    Search emails across all accounts and mailboxes.

    Uses full-text search index for fast results (~2ms). All scopes
    search across every account and mailbox unless filtered.

    Query tips:
    - Use 2-3 specific keywords, not full sentences
    - Terms are AND-ed: "budget Q1" finds emails with BOTH words
    - Use quotes for exact phrases: '"quarterly report"'
    - Prefix search: "meet*" matches meeting, meetings, etc.
    - The default scope "all" searches subject + sender + body together

    Args:
        query: Search keywords (2-3 specific terms work best).
            Do NOT use long natural-language phrases.
        account: Filter to specific account name (optional).
        mailbox: Filter to specific mailbox (optional).
        scope: Where to search:
            - "all": Subject + sender + body (default, recommended)
            - "subject": Subject line only
            - "sender": Sender name/email only
            - "body": Body text only
            - "attachments": Attachment filenames
        limit: Maximum results (default: 20)
        exclude_mailboxes: Mailboxes to exclude (default: ["Drafts"])

    Returns:
        List of matching emails with id, subject, sender,
        date_received, score, matched_in, content_snippet,
        account, and mailbox fields.

    Examples:
        >>> search("Kim Foulds")  # Find person across all fields
        >>> search("quarterly budget")  # Keywords, not sentences
        >>> search('"project update"')  # Exact phrase
        >>> search("invoice.pdf", scope="attachments")
    """
    if exclude_mailboxes is None:
        exclude_mailboxes = ["Drafts"]

    _EMPTY_HINT = (
        "No results. Try fewer keywords (2-3 specific terms), "
        "check spelling, or use scope='all' to search everywhere."
    )

    def _maybe_hint(results: list) -> list | dict:
        if not results:
            return {"result": [], "hint": _EMPTY_HINT}
        return results

    # Attachment filename search (SQL LIKE query, no JXA needed)
    if scope == "attachments":
        manager = _get_index_manager()
        if not manager.has_index():
            return []

        search_acct = None
        if account:
            acct_map = _get_account_map()
            await acct_map.ensure_loaded()
            search_acct = acct_map.name_to_uuid(account) or account

        rows = manager.search_attachments(
            query,
            account=search_acct,
            mailbox=mailbox,
            limit=limit,
            exclude_mailboxes=exclude_mailboxes,
        )

        acct_map = _get_account_map()
        await acct_map.ensure_loaded()

        return _maybe_hint(
            [
                {
                    "id": row["message_id"],
                    "subject": row["subject"],
                    "sender": row["sender"],
                    "date_received": row["date_received"],
                    "score": 1.0,
                    "matched_in": f"attachment: {row['filename']}",
                    "account": acct_map.uuid_to_name(row["account"]),
                    "mailbox": row["mailbox"],
                }
                for row in rows
            ]
        )

    # S5: Split FTS5 vs JXA resolution
    # FTS5: None = search all accounts/mailboxes
    fts_account = account
    fts_mailbox = mailbox
    # JXA: resolve defaults (needs a concrete target)
    jxa_account = _resolve_account(account)
    jxa_mailbox = _resolve_mailbox(mailbox)

    # Try FTS5 index for all searchable scopes
    if scope in ("all", "body", "subject", "sender"):
        manager = _get_index_manager()
        if manager.has_index():
            # Translate friendly name → UUID for index lookup
            acct_map = _get_account_map()
            await acct_map.ensure_loaded()

            search_account = None
            if fts_account:
                search_account = (
                    acct_map.name_to_uuid(fts_account)
                    or fts_account  # fallback: maybe already UUID
                )

            # Map scope to FTS5 column filter
            fts_column = None
            if scope == "subject":
                fts_column = "subject"
            elif scope == "sender":
                fts_column = "sender"

            results = manager.search(
                query,
                account=search_account,
                mailbox=fts_mailbox,
                limit=limit,
                exclude_mailboxes=exclude_mailboxes,
                column=fts_column,
            )
            return _maybe_hint(
                [
                    {
                        "id": r.id,
                        "subject": r.subject,
                        "sender": r.sender,
                        "date_received": r.date_received,
                        "score": r.score,
                        "matched_in": (
                            scope
                            if scope in ("subject", "sender")
                            else _detect_matched_columns(query, r)
                        ),
                        "content_snippet": r.content_snippet,
                        "account": acct_map.uuid_to_name(r.account),
                        "mailbox": r.mailbox,
                    }
                    for r in results
                ]
            )

    # JXA-based search for subject/sender or when no index
    safe_query_js = json.dumps(query.lower())

    if scope == "subject":
        filter_expr = (
            f"(data.subject[i] || '').toLowerCase().includes({safe_query_js})"
        )
    elif scope == "sender":
        filter_expr = (
            f"(data.sender[i] || '').toLowerCase().includes({safe_query_js})"
        )
    else:
        # "all" without index - search subject and sender
        filter_expr = f"""(
            (data.subject[i] || '').toLowerCase().includes({safe_query_js}) ||
            (data.sender[i] || '').toLowerCase().includes({safe_query_js})
        )"""

    q = (
        QueryBuilder()
        .from_mailbox(jxa_account, jxa_mailbox)
        .select("standard")
        .where(filter_expr)
        .order_by("date_received", descending=True)
        .limit(limit)
    )

    emails = await execute_query_async(q)

    # Convert to SearchResult format
    return _maybe_hint(
        [
            {
                "id": e["id"],
                "subject": e["subject"],
                "sender": e["sender"],
                "date_received": e["date_received"],
                "score": 1.0,  # No ranking for JXA search
                "matched_in": scope if scope != "all" else "metadata",
            }
            for e in emails
        ]
    )


if __name__ == "__main__":
    mcp.run()
