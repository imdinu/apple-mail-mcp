"""Tests for the shared mailbox resolver (#105, #107, #120)."""

from __future__ import annotations

import unicodedata

import pytest

from apple_mail_mcp.index.mailbox_resolver import (
    AmbiguousMailboxError,
    normalize_mailbox,
    resolve_exclusions,
    resolve_for_identity,
    resolve_for_search,
)
from apple_mail_mcp.index.schema import create_connection, get_schema_sql

# Stored the way macOS writes it: decomposed (NFD).
NFD_ARCHIV = unicodedata.normalize("NFD", "Archiv/Gelöscht")


@pytest.fixture
def conn():
    """Index with a nested hierarchy across two accounts."""
    db = create_connection(":memory:")
    db.executescript(get_schema_sql())
    rows = [
        ("acct-1", "INBOX"),
        ("acct-1", "INBOX/Junk"),
        ("acct-1", "Ablage/Nebenkosten"),
        ("acct-1", "Ablage/Steuer/2025"),
        ("acct-1", "Archive/Ablage"),
        ("acct-1", "[Gmail]/Spam"),
        ("acct-1", "[Gmail]/All Mail"),
        ("acct-1", NFD_ARCHIV),
        ("acct-2", "Inbox"),
        ("acct-2", "Projekte/Ablage"),
    ]
    for i, (acct, mbox) in enumerate(rows, start=1):
        db.execute(
            "INSERT INTO emails (message_id, account, mailbox) "
            "VALUES (?, ?, ?)",
            (i, acct, mbox),
        )
    db.commit()
    yield db
    db.close()


class TestNormalize:
    def test_nfd_and_nfc_compare_equal(self):
        nfc = unicodedata.normalize("NFC", "Gelöscht")
        nfd = unicodedata.normalize("NFD", "Gelöscht")
        assert nfc != nfd
        assert normalize_mailbox(nfc) == normalize_mailbox(nfd)

    def test_case_folded(self):
        assert normalize_mailbox("INBOX") == normalize_mailbox("Inbox")


class TestResolveForSearch:
    def test_exact_full_path(self, conn):
        assert resolve_for_search(conn, "[Gmail]/Spam") == ["[Gmail]/Spam"]

    def test_exact_is_case_insensitive_across_accounts(self, conn):
        assert sorted(resolve_for_search(conn, "inbox")) == ["INBOX", "Inbox"]

    def test_exact_match_does_not_pull_in_children(self, conn):
        """IMAP servers nest every folder below INBOX. — "INBOX" must
        not silently widen to Junk and friends."""
        assert "INBOX/Junk" not in resolve_for_search(conn, "INBOX")

    def test_subtree_when_parent_has_no_messages(self, conn):
        """The container itself holds no mail (#105): keep the old
        search(mailbox="[Gmail]") call working."""
        assert sorted(resolve_for_search(conn, "[Gmail]")) == [
            "[Gmail]/All Mail",
            "[Gmail]/Spam",
        ]

    def test_subtree_spans_several_levels(self, conn):
        assert sorted(resolve_for_search(conn, "ablage")) == [
            "Ablage/Nebenkosten",
            "Ablage/Steuer/2025",
        ]

    def test_subtree_needs_a_segment_boundary(self, conn):
        """ "Abl" is no parent of "Ablage/..."."""
        assert resolve_for_search(conn, "Abl") == []

    def test_unambiguous_leaf(self, conn):
        assert resolve_for_search(conn, "Nebenkosten") == ["Ablage/Nebenkosten"]

    def test_ambiguous_leaf_raises_with_copyable_candidates(self, conn):
        # Subtree wins over leaf: "Ablage" is a parent in acct-1, so
        # narrow to an account where it is only a leaf.
        conn.execute("DELETE FROM emails WHERE mailbox LIKE 'Ablage/%'")
        with pytest.raises(AmbiguousMailboxError) as exc:
            resolve_for_search(conn, "Ablage")
        assert str(exc.value) == (
            'Ambiguous mailbox "Ablage" — specify one of: '
            'mailbox="Archive/Ablage", mailbox="Projekte/Ablage"'
        )
        assert isinstance(exc.value, ValueError)

    def test_account_scope_removes_ambiguity(self, conn):
        conn.execute("DELETE FROM emails WHERE mailbox LIKE 'Ablage/%'")
        assert resolve_for_search(conn, "Ablage", account="acct-2") == [
            "Projekte/Ablage"
        ]

    def test_excluded_accounts_are_not_candidates(self, conn):
        """A hidden account's mailbox names must not leak through an
        ambiguity error (cf. #115)."""
        conn.execute("DELETE FROM emails WHERE mailbox LIKE 'Ablage/%'")
        assert resolve_for_search(
            conn, "Ablage", exclude_accounts=["acct-2"]
        ) == ["Archive/Ablage"]

    def test_no_match_resolves_to_nothing(self, conn):
        assert resolve_for_search(conn, "Nonexistent") == []

    def test_nfc_input_matches_nfd_storage(self, conn):
        nfc = unicodedata.normalize("NFC", "Archiv/Gelöscht")
        assert resolve_for_search(conn, nfc) == [NFD_ARCHIV]

    def test_nfc_leaf_matches_nfd_storage(self, conn):
        nfc = unicodedata.normalize("NFC", "Gelöscht")
        assert resolve_for_search(conn, nfc) == [NFD_ARCHIV]


class TestResolveForIdentity:
    def test_exact(self, conn):
        assert resolve_for_identity(conn, "Ablage/Nebenkosten") == [
            "Ablage/Nebenkosten"
        ]

    def test_case_insensitive(self, conn):
        assert resolve_for_identity(conn, "ablage/nebenkosten") == [
            "Ablage/Nebenkosten"
        ]

    def test_no_subtree(self, conn):
        """A parent name must never expand into its children: on a
        DELETE that would widen one row into a whole subtree."""
        assert resolve_for_identity(conn, "[Gmail]") == []
        assert resolve_for_identity(conn, "Ablage/Steuer") == []

    def test_unambiguous_leaf(self, conn):
        assert resolve_for_identity(conn, "Nebenkosten") == [
            "Ablage/Nebenkosten"
        ]

    def test_ambiguous_leaf_raises(self, conn):
        with pytest.raises(AmbiguousMailboxError, match="Archive/Ablage"):
            resolve_for_identity(conn, "Ablage")

    def test_nfc_input_matches_nfd_storage(self, conn):
        nfc = unicodedata.normalize("NFC", "Gelöscht")
        assert resolve_for_identity(conn, nfc) == [NFD_ARCHIV]


class TestResolveExclusions:
    def test_unions_ambiguous_leaf(self, conn):
        conn.execute("DELETE FROM emails WHERE mailbox LIKE 'Ablage/%'")
        assert sorted(resolve_exclusions(conn, ["Ablage"])) == [
            "Archive/Ablage",
            "Projekte/Ablage",
        ]

    def test_subtree_and_exact(self, conn):
        # Exclusions do not stop at the exact match: INBOX/Junk goes too.
        assert sorted(resolve_exclusions(conn, ["[Gmail]", "inbox"])) == [
            "INBOX",
            "INBOX/Junk",
            "Inbox",
            "[Gmail]/All Mail",
            "[Gmail]/Spam",
        ]

    def test_unknown_names_drop_out(self, conn):
        assert resolve_exclusions(conn, ["Nonexistent"]) == []


class TestReviewFindings:
    """Second review round on #107."""

    def _db(self, rows):
        db = create_connection(":memory:")
        db.executescript(get_schema_sql())
        for i, (acct, mbox) in enumerate(rows, start=1):
            db.execute(
                "INSERT INTO emails (message_id, account, mailbox) "
                "VALUES (?, ?, ?)",
                (i, acct, mbox),
            )
        return db

    def test_candidates_survive_a_copy_roundtrip(self):
        """Quotes and backslashes in a name must not break the
        copyable mailbox="..." form."""
        import json
        import re

        tricky = ['A/"Budget"/Archive', "B\\Back/Archive"]
        db = self._db([("a", p) for p in tricky])
        with pytest.raises(AmbiguousMailboxError) as exc:
            resolve_for_search(db, "Archive")
        literals = re.findall(r'mailbox=("(?:[^"\\]|\\.)*")', str(exc.value))
        assert sorted(json.loads(s) for s in literals) == sorted(tricky)
        # ...and each one resolves back to exactly its own path.
        for path in tricky:
            assert resolve_for_search(db, path) == [path]

    def test_sharp_s_is_not_folded_to_ss(self):
        """casefold() maps ß to ss and would merge distinct folders."""
        assert normalize_mailbox("Straße") != normalize_mailbox("Strasse")
        db = self._db([("a", "Straße"), ("a", "Strasse")])
        assert resolve_for_search(db, "STRASSE") == ["Strasse"]

    def test_exclusions_union_exact_subtree_and_leaf(self):
        """An exact match in one account must not shadow leaf or
        subtree matches elsewhere: exclude too much, never too little."""
        db = self._db(
            [
                ("imap", "Drafts"),
                ("gmail", "[Gmail]/Drafts"),
                ("gmail", "INBOX"),
                ("a", "Ablage"),
                ("a", "Ablage/Nebenkosten"),
                ("a", "Archive/Ablage/2024"),
            ]
        )
        assert resolve_exclusions(db, ["drafts"]) == [
            "Drafts",
            "[Gmail]/Drafts",
        ]
        assert resolve_exclusions(db, ["Ablage"]) == [
            "Ablage",
            "Ablage/Nebenkosten",
            "Archive/Ablage/2024",
        ]


class TestResolveExact:
    def test_exact_only(self, conn):
        from apple_mail_mcp.index.mailbox_resolver import resolve_exact

        assert resolve_exact(conn, "ablage/nebenkosten") == [
            "Ablage/Nebenkosten"
        ]
        assert resolve_exact(conn, "Nebenkosten") == []
        assert resolve_exact(conn, "[Gmail]") == []


class TestFullPathExclusion:
    """A value with "/" is a full path: exact match plus its subtree
    only. Leaf and parent-name rules apply to bare names alone."""

    def test_full_path_does_not_match_as_suffix(self):
        db = TestReviewFindings()._db(
            [
                ("a", p)
                for p in [
                    "A/B",
                    "A/B/Child",
                    "X/A/B",
                    "X/A/B/Child",
                    "A/BC",
                    "X/B",
                ]
            ]
        )
        assert resolve_exclusions(db, ["A/B"]) == ["A/B", "A/B/Child"]
