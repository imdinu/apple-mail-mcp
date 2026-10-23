"""Resolve a caller's mailbox name to the full paths stored in the index.

The index stores every mailbox by its full path (``Ablage/Nebenkosten``,
``[Gmail]/Spam``, #105), while callers name mailboxes however they see
them: a full path, a parent, or just the leaf. This module is the one
place that maps such a value onto concrete stored paths. Callers pass
the result as an ``IN`` list into their existing exact-match SQL.

Two entry points encode the caller's intent:

- :func:`resolve_for_search` — the mailbox *selects* a result set:
  exact full path → subtree → unambiguous leaf.
- :func:`resolve_for_identity` — the mailbox *disambiguates* one
  message_id: exact full path → unambiguous leaf, never a subtree.
  On a DELETE, subtree expansion would widen one row into many.

An ambiguous leaf raises :class:`AmbiguousMailboxError` with candidates
the caller can copy verbatim. Exclusions (:func:`resolve_exclusions`)
union every match instead: excluding too much beats leaking.

Comparison is case-insensitive and Unicode-normalized: macOS stores
mailbox names decomposed (NFD) while callers type them composed (NFC),
so a byte-exact compare misses every non-ASCII name (#120).
"""

from __future__ import annotations

import functools
import json
import sqlite3
import unicodedata
from collections.abc import Iterable


class AmbiguousMailboxError(ValueError):
    """A bare mailbox name matches the leaf of several stored paths."""

    def __init__(self, value: str, candidates: list[str]) -> None:
        self.value = value
        self.candidates = candidates
        # JSON string literals: a quote or backslash in a folder name
        # stays escaped, so each option can be pasted back verbatim.
        options = ", ".join(f"mailbox={_quote(c)}" for c in candidates)
        super().__init__(
            f"Ambiguous mailbox {_quote(value)} — specify one of: {options}"
        )


def _quote(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def normalize_mailbox(name: str) -> str:
    """Canonical comparison key for a mailbox name or path.

    NFC first: macOS may mix decomposed and precomposed characters
    within one name, so neither side can be trusted to already be in a
    given form. Then lower() rather than casefold(), which would map
    "ß" to "ss" and merge distinct folders like "Straße"/"Strasse".
    """
    return unicodedata.normalize("NFC", name).lower()


def _stored_mailboxes(
    conn: sqlite3.Connection,
    account: str | None,
    exclude_accounts: Iterable[str] | None,
) -> dict[str, list[str]]:
    """Map each normalized stored path to its raw stored spellings.

    One normalized path can have several raw spellings, e.g. "INBOX"
    in one account and "Inbox" in another. All of them belong in the
    ``IN`` list, since the SQL compares raw values exactly.
    """
    # DISTINCT over (account, mailbox) walks the covering index
    # idx_emails_account_mailbox; DISTINCT mailbox alone adds a temp
    # B-tree and takes twice as long (~19 vs ~10 ms at 79k rows).
    sql = "SELECT DISTINCT account, mailbox FROM emails WHERE 1=1"
    params: list = []
    if account:
        sql += " AND account = ?"
        params.append(account)
    excluded = list(exclude_accounts or ())
    if excluded:
        sql += f" AND account NOT IN ({', '.join('?' for _ in excluded)})"
        params.extend(excluded)

    by_key: dict[str, list[str]] = {}
    for _, raw in conn.execute(sql, params):
        spellings = by_key.setdefault(normalize_mailbox(raw), [])
        if raw not in spellings:
            spellings.append(raw)
    return by_key


def _resolve(
    stored: dict[str, list[str]],
    value: str,
    *,
    subtree: bool,
    union_ambiguous: bool,
) -> list[str]:
    want = normalize_mailbox(value)

    # 1. Exact full path. Wins outright: IMAP servers nest every folder
    #    below INBOX, so "INBOX" must not widen to "INBOX/Junk".
    if want in stored:
        return sorted(stored[want])

    # 2. Subtree — only reached when the parent itself holds no mail,
    #    like Gmail's "[Gmail]" container.
    if subtree:
        prefix = want + "/"
        keys = [k for k in stored if k.startswith(prefix)]
        if keys:
            return sorted(raw for k in keys for raw in stored[k])

    # 3. Leaf.
    keys = [k for k in stored if k.rsplit("/", 1)[-1] == want]
    if len(keys) > 1 and not union_ambiguous:
        candidates = sorted(stored[k][0] for k in keys)
        raise AmbiguousMailboxError(value, candidates)
    return sorted(raw for k in keys for raw in stored[k])


def resolve_for_search(
    conn: sqlite3.Connection,
    value: str,
    *,
    account: str | None = None,
    exclude_accounts: Iterable[str] | None = None,
) -> list[str]:
    """Resolve a mailbox filter that selects a result set.

    Order: exact full path → subtree (``value/…``) → unambiguous leaf.

    Args:
        conn: Index connection
        value: Mailbox as the caller named it
        account: Restrict candidates to this account (UUID)
        exclude_accounts: Account UUIDs whose mailboxes are never
            candidates — keeps hidden accounts out of error messages

    Returns:
        Stored mailbox paths, sorted; empty if nothing matches.

    Raises:
        AmbiguousMailboxError: The value is only a leaf, of several paths.
    """
    stored = _stored_mailboxes(conn, account, exclude_accounts)
    return _resolve(stored, value, subtree=True, union_ambiguous=False)


def resolve_exact(
    conn: sqlite3.Connection,
    value: str,
    *,
    account: str | None = None,
) -> list[str]:
    """Full-path match only — no subtree, no leaf. For destructive
    calls naming a mailbox outright (``rebuild --mailbox``), where a
    leaf fallback could pick some other folder.
    """
    stored = _stored_mailboxes(conn, account, None)
    return sorted(stored.get(normalize_mailbox(value), []))


def resolve_for_identity(
    conn: sqlite3.Connection,
    value: str,
    *,
    account: str | None = None,
    exclude_accounts: Iterable[str] | None = None,
) -> list[str]:
    """Resolve a mailbox that pins down a single message.

    Order: exact full path → unambiguous leaf. Never expands to a
    subtree. Several paths only come back when they differ in case
    alone (one per account, e.g. "INBOX" and "Inbox").

    Raises:
        AmbiguousMailboxError: The value is only a leaf, of several paths.
    """
    stored = _stored_mailboxes(conn, account, exclude_accounts)
    return _resolve(stored, value, subtree=False, union_ambiguous=False)


def resolve_exclusions(
    conn: sqlite3.Connection,
    values: Iterable[str],
    *,
    account: str | None = None,
    exclude_accounts: Iterable[str] | None = None,
) -> list[str]:
    """Resolve ``exclude_mailboxes``: the union of exact, subtree and
    leaf matches (see :func:`excludes_mailbox`). Never raises —
    excluding too much beats leaking.
    """
    stored = _stored_mailboxes(conn, account, exclude_accounts)
    return _resolve_union(stored, values)


def excludes_mailbox(key: str, wants: Iterable[str]) -> bool:
    """Whether normalized path ``key`` falls under any normalized
    exclusion in ``wants``.

    Unlike search, the steps do not short-circuit: an exact match in
    one account must not shadow a leaf match in another ("Drafts" vs
    "[Gmail]/Drafts"). A path is excluded when it is the named mailbox
    or lies in its subtree. A value containing "/" names one full path;
    only a bare name also matches as a folder name at any depth.
    """
    for want in wants:
        if key == want or key.startswith(want + "/"):
            return True
        if "/" not in want and (key.endswith("/" + want) or f"/{want}/" in key):
            return True
    return False


def _resolve_union(
    stored: dict[str, list[str]], values: Iterable[str]
) -> list[str]:
    wants = [normalize_mailbox(v) for v in values]
    return sorted(
        raw
        for key, raws in stored.items()
        if excludes_mailbox(key, wants)
        for raw in raws
    )


@functools.lru_cache(maxsize=32)
def _parse_wants(wants_json: str) -> tuple[str, ...]:
    return tuple(json.loads(wants_json))


# Pure, and called once per distinct mailbox on every search that
# excludes (default ["Drafts"]): ~12 µs uncached, ~1.5k mailboxes.
@functools.lru_cache(maxsize=16384)
def _sql_mailbox_excluded(mailbox: str | None, wants_json: str) -> bool:
    if mailbox is None:
        return False
    return excludes_mailbox(
        normalize_mailbox(mailbox), _parse_wants(wants_json)
    )


def register_sql_functions(conn: sqlite3.Connection) -> None:
    """Register the resolver's SQL functions on a new connection.

    Called by the connection factory: SQLite refuses to redefine a
    function while any statement on the connection is active, and the
    index connection is shared with the sync thread.
    """
    conn.create_function(
        "mailbox_excluded", 2, _sql_mailbox_excluded, deterministic=True
    )


def _ensure_sql_functions(conn: sqlite3.Connection) -> None:
    # Connections from schema.create_connection already have it; only
    # ones opened elsewhere get it here, defined once, never redefined.
    try:
        conn.execute("SELECT mailbox_excluded(NULL, '[]')")
    except sqlite3.OperationalError:
        register_sql_functions(conn)


def exclusion_clause(
    conn: sqlite3.Connection, column: str, values: Iterable[str]
) -> tuple[str, list[str]]:
    """SQL fragment excluding every mailbox :func:`resolve_exclusions`
    would return, resolved inside the caller's own statement.

    Resolving in Python first and querying afterwards leaves a gap:
    mail committed to a newly seen excluded mailbox in between slips
    through, and an empty resolution dropped the predicate entirely.
    One statement reads one snapshot. An explicit read transaction
    around both is no option — the index connection is shared with
    the background sync thread.
    """
    _ensure_sql_functions(conn)
    wants = json.dumps(sorted({normalize_mailbox(v) for v in values}))
    # Candidates as in _stored_mailboxes: DISTINCT over the covering
    # index, then the match logic once per distinct path.
    sql = (
        f"{column} NOT IN (SELECT mailbox FROM "
        "(SELECT DISTINCT account, mailbox FROM emails) "
        "WHERE mailbox_excluded(mailbox, ?))"
    )
    return sql, [wants]
