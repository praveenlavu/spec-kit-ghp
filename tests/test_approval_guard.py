"""T27 tests: the comment-approval guard (`approvals.counts`, `pin`, `recheck`, `still_present`, `approver_is_token`), ported in behaviour from ado's guard
(`excluded-author`, `not-approver`, `edited`) plus the rules GitHub adds (bot type, minimized, performed via an App, `author_association` never read).

Note on the plan's wording: T27 says `last_edited_at is None and edit_count == 1`; the live-verified fact (T13) is that a never-edited comment has
`userContentEdits.totalCount` 0, so "not edited" is `not Comment.edited` (`last_edited_at is None and edit_count == 0`), and these tests use that.
Offline only: every comment is a `comments.Comment` built here with fictional numeric ids."""
import dataclasses
import time

import pytest

from ghp import approvals, comments
from ghp.comments import Comment, Comments, Note
from ghp.issues import Actor

TOKEN, HUMAN, OTHER = 900001, 900003, 900005
APPROVERS = {HUMAN}


def _c(body="/approve D1", cid=7, author=HUMAN, typ="User", **kw):
    kw.setdefault("last_edited_at", None)
    kw.setdefault("edit_count", 0)
    kw.setdefault("minimized", False)
    kw.setdefault("app", None)
    kw.setdefault("viewer_did_author", False)
    return Comment(id=cid, node_id="IC_%d" % cid, author=Actor(author, "login%d" % author, typ), body=body, created_at="2026-10-01T10:00:00Z", **kw)


def _counts(c, approvers=APPROVERS, token=TOKEN):
    return approvals.counts(c, approvers, token)


# ---- AC2: a good comment counts -----------------------------------------------------------------------------------------------------------------

def test_valid_approver_comment_counts():
    assert _counts(_c()) == (True, "ok")
    assert _counts(_c("/approve plan-gate " + "ab" * 32 + ": fine by me")) == (True, "ok")
    assert _counts(_c("/reject D2: no")) == (True, "ok")  # the verb is the caller's: a trusted command is a trusted command
    assert _counts(_c("  /approve D1  \nthanks")) == (True, "ok")
    assert _counts(_c(), approvers=frozenset({HUMAN, OTHER})) == (True, "ok")


# ---- AC1: one test per rule, each failing exactly that rule -------------------------------------------------------------------------------------

def test_bot_author_never_counts():
    for typ in ("Bot", "Organization", "Mannequin", "user", "USER", "User ", "", None):
        assert _counts(_c(typ=typ)) == (False, "bot-author"), typ


def test_author_not_in_approvers_never_counts():
    assert _counts(_c(author=OTHER)) == (False, "not-approver")
    assert _counts(_c(), approvers=set()) == (False, "not-approver")


def test_token_own_comment_never_counts():
    assert _counts(_c(author=TOKEN), approvers={TOKEN}) == (False, "excluded-author")  # even when someone put the token id into approvers
    assert _counts(_c(author=TOKEN), approvers={HUMAN, TOKEN}, token=TOKEN)[0] is False


def test_edited_comment_never_counts():
    for kw in ({"last_edited_at": "2026-10-01T11:00:00Z"}, {"edit_count": 2}, {"edit_count": 1}, {"last_edited_at": "x", "edit_count": 3}):
        assert _counts(_c(**kw)) == (False, "edited"), kw
    for kw in ({"edit_count": None}, {"edit_count": -1}, {"edit_count": False}, {"edit_count": 0.0}, {"edit_count": "0"}, {"last_edited_at": ""}):
        assert _counts(_c(**kw)) == (False, "edited"), kw  # fail closed: only an exact int 0 with no timestamp is "never edited"
    assert _c(edit_count=0, last_edited_at=None).edited is False  # the live-verified never-edited shape (T13): totalCount 0


def test_minimized_comment_never_counts():
    assert _counts(_c(minimized=True)) == (False, "minimized")
    for odd in (None, 0, "false", 1):
        assert _counts(_c(minimized=odd)) == (False, "minimized"), odd  # fail closed: only a real False is "not minimized"


def test_app_performed_comment_never_counts():
    assert _counts(_c(app={"id": 1, "slug": "some-app"})) == (False, "app-performed")
    assert _counts(_c(app={})) == (False, "app-performed")  # an empty object is still "performed via an app"


def test_a_comment_that_did_not_say_whether_an_app_made_it_never_counts():
    assert _counts(_c(app=comments.APP_UNKNOWN)) == (False, "app-unknown")
    assert comments.APP_UNKNOWN is not None and not isinstance(comments.APP_UNKNOWN, type(None))


def test_the_comment_the_token_wrote_never_counts_even_with_a_wrong_token_id():
    """GitHub's own `viewerDidAuthor` is belt and braces for a `token_id` that was read wrongly."""
    for v in (True, None, 1, "false"):
        assert _counts(_c(viewer_did_author=v)) == (False, "excluded-author"), v
    assert approvals.newest(Comments([_c(viewer_did_author=True)]), "D1", {HUMAN}, {TOKEN}) is None


def test_counts_never_raises_on_a_comment_without_an_author():
    for author in (None, "x", 5, {"id": HUMAN, "type": "User"}):
        c = dataclasses.replace(_c(), author=author)
        assert _counts(c) == (False, "bot-author"), repr(author)


class _NoAssociation(Comment):
    """A Comment whose `author_association` raises if anything reads it (the guard must not look at it)."""

    @property
    def author_association(self):
        raise AssertionError("the guard read author_association")


def test_author_association_owner_is_not_enough():
    """OWNER alone never counts: not being in `approvers` is decisive, and the attribute is never read (a comment whose access raises still evaluates)."""
    shallow = lambda c: {f.name: getattr(c, f.name) for f in dataclasses.fields(c)}  # noqa: E731
    ghost = _NoAssociation(**{**shallow(_c()), "author": Actor(OTHER, "owner-login", "User")})
    assert _counts(ghost) == (False, "not-approver")  # would raise AssertionError if the attribute were read
    good = _NoAssociation(**shallow(_c()))
    assert _counts(good) == (True, "ok")
    # a plain object that claims OWNER is not a Comment at all
    fake = type("Fake", (), {"author_association": "OWNER", "author": Actor(OTHER, "o", "User"), "body": "/approve D1"})()
    assert _counts(fake) == (False, "not-a-comment")
    assert "author_association" not in dir(Comment)  # the data model has no such field to consult


# ---- AC3: identity is the numeric id ---------------------------------------------------------------------------------------------------------------

def test_login_lookalike_does_not_count():
    """A different id with the approver's old login, or a login like the approver's, is not the approver."""
    for login in ("login%d" % HUMAN, "LOGIN%d" % HUMAN, "login%d " % HUMAN, "1ogin%d" % HUMAN):
        c = dataclasses.replace(_c(author=OTHER), author=Actor(OTHER, login, "User"))
        assert _counts(c) == (False, "not-approver"), login
    renamed = dataclasses.replace(_c(), author=Actor(HUMAN, "brand-new-login", "User"))
    assert _counts(renamed) == (True, "ok")  # a rename changes nothing: the id is the identity
    for bad_id in ("900003", True, 900003.0, None, " 900003"):
        c = dataclasses.replace(_c(), author=Actor(bad_id, "login", "User"))
        assert _counts(c)[0] is False, repr(bad_id)
    assert _counts(dataclasses.replace(_c(), author=Actor(True, "x", "User")), approvers={1}) == (False, "not-approver")  # bool is not the int 1


def test_login_case_is_irrelevant():
    assert _counts(dataclasses.replace(_c(), author=Actor(HUMAN, "OctoCat", "User"))) == _counts(dataclasses.replace(_c(), author=Actor(HUMAN, "octocat", "User")))
    assert _counts(dataclasses.replace(_c(author=OTHER), author=Actor(OTHER, "OCTOCAT", "User"))) == (False, "not-approver")


def test_a_login_in_approvers_or_a_bad_token_refuses():
    for bad in ({"octocat"}, {HUMAN, "900003"}, {True}, {HUMAN, 1.0}, {None}):
        assert _counts(_c(), approvers=bad) == (False, "bad-approvers"), bad
    for bad in ({0}, {-5}, {HUMAN, 0}):  # an approver id is a positive int, as config requires
        assert _counts(_c(author=0), approvers=bad) == (False, "bad-approvers"), bad
    for bad in ((HUMAN,), [HUMAN], "900003", None):  # not a set
        assert _counts(_c(), approvers=bad) == (False, "bad-approvers"), bad
    assert _counts(_c(author=1), approvers={True}) == (False, "bad-approvers")  # True == 1 must never make author 1 an approver
    for bad in (None, "900001", True, 0, -1, 1.5):
        assert _counts(_c(), token=bad) == (False, "bad-token"), bad
    assert _counts(object()) == (False, "not-a-comment")


# ---- AC4: edits revoke; the hash and last_edited_at are pinned at sync ---------------------------------------------------------------------------

def test_edit_after_approval_revokes_it():
    """The same comment, edited by anyone (a writer rewriting the approver's comment leaves an edit history), stops counting."""
    before = _c("/approve D1")
    assert _counts(before) == (True, "ok")
    for edit in ({"last_edited_at": "2026-10-02T09:00:00Z"}, {"edit_count": 2}, {"last_edited_at": "2026-10-02T09:00:00Z", "edit_count": 2}):
        after = dataclasses.replace(before, **edit)
        assert _counts(after) == (False, "edited"), edit
        # the same text, rewritten and restored, is still an edit
        assert _counts(dataclasses.replace(after, body=before.body)) == (False, "edited")
    # and through `newest`: the edited approval is skipped, so it is not an approval
    assert approvals.newest(Comments([dataclasses.replace(before, edit_count=2)]), "D1", {HUMAN}, {TOKEN}) is None
    assert approvals.newest(Comments([before]), "D1", {HUMAN}, {TOKEN})["verb"] == "approve"


def test_done_recheck_rejects_a_comment_edited_since_sync():
    synced = _c("/approve D1")
    pinned = approvals.pin(synced)
    assert set(pinned) == {"id", "author", "body_sha256", "last_edited_at"} and pinned["last_edited_at"] is None and len(pinned["body_sha256"]) == 64
    assert approvals.recheck(pinned, Comments([synced]), APPROVERS, TOKEN) == (True, "ok")
    # edited since sync: last_edited_at moved (the marker GitHub leaves)
    edited = dataclasses.replace(synced, last_edited_at="2026-10-02T09:00:00Z", edit_count=2)
    assert approvals.recheck(pinned, Comments([edited]), APPROVERS, TOKEN) == (False, "changed-since-sync")
    # the text differs from the pinned hash even though no edit marker shows (a stale or forged read): the hash is what was pinned
    assert approvals.recheck(pinned, Comments([dataclasses.replace(synced, body="/approve D1 ")]), APPROVERS, TOKEN) == (False, "changed-since-sync")
    assert approvals.recheck(pinned, Comments([dataclasses.replace(synced, body="/approve D2")]), APPROVERS, TOKEN) == (False, "changed-since-sync")
    # a different author id under the same comment id
    other = dataclasses.replace(synced, author=Actor(OTHER, "x", "User"))
    assert approvals.recheck(pinned, Comments([other]), {HUMAN, OTHER}, TOKEN) == (False, "changed-since-sync")
    # unchanged comment that no longer passes the guard (the approver was removed from approvers on the target branch, or got minimized)
    assert approvals.recheck(pinned, Comments([synced]), set(), TOKEN) == (False, "not-approver")
    assert approvals.recheck(pinned, Comments([dataclasses.replace(synced, minimized=True)]), APPROVERS, TOKEN) == (False, "minimized")
    # two comments with the pinned id: ambiguous, refused
    assert approvals.recheck(pinned, Comments([synced, synced]), APPROVERS, TOKEN) == (False, "changed-since-sync")
    # a pinned edit marker also has to be equal (a pin made of an already edited comment never passes counts, and a moved marker is a change)
    p2 = dict(pinned, last_edited_at="2026-10-02T09:00:00Z")
    assert approvals.recheck(p2, Comments([synced]), APPROVERS, TOKEN) == (False, "changed-since-sync")


def test_recheck_refuses_a_list_that_dropped_comments():
    """A dropped comment could be the approver's newer /reject: the list cannot be relied on (comments.py contract)."""
    synced = _c()
    pinned = approvals.pin(synced)
    found = Comments([synced], notes=[Note("E_COMMENT_JOIN", 8, "only in REST")])
    assert approvals.recheck(pinned, found, APPROVERS, TOKEN) == (False, "dropped")
    assert approvals.recheck(pinned, Comments([], notes=[Note("E_COMMENT_JOIN", 7, "only in REST")]), APPROVERS, TOKEN) == (False, "dropped")  # not "deleted"
    assert approvals.recheck(pinned, Comments([synced]), APPROVERS, TOKEN) == (True, "ok")
    assert "newest" in approvals.recheck.__doc__ and "NECESSARY, NOT SUFFICIENT" in approvals.recheck.__doc__


def test_recheck_is_blind_to_a_newer_reject_so_t28_must_run_newest_too():
    """Documents the limit: `recheck` alone says ok while a newer /reject exists; `newest` on the same fetch is what shows it."""
    synced = _c("/approve D1", cid=7)
    later = dataclasses.replace(_c("/reject D1", cid=8), created_at="2026-10-01T11:00:00Z")
    found = Comments([synced, later])
    assert approvals.recheck(approvals.pin(synced), found, APPROVERS, TOKEN) == (True, "ok")
    assert approvals.newest(found, "D1", {HUMAN}, {TOKEN})["verb"] == "reject"


def test_recheck_refuses_a_malformed_pin():
    good = approvals.pin(_c())
    for bad in (None, {}, [], "x", {**good, "id": True}, {**good, "id": "7"}, {**good, "author": "900003"}, {**good, "body_sha256": None},
                {k: v for k, v in good.items() if k != "last_edited_at"}):
        assert approvals.recheck(bad, Comments([_c()]), APPROVERS, TOKEN) == (False, "bad-pin"), repr(bad)


# ---- AC5: invisible and bidi characters in the approve line ------------------------------------------------------------------------------------------

BAD_CHARS = ["​", "‌", "‍", "⁠", "﻿", "­", "‮", "‪", "‬", "⁦", "⁩", "‎", "‏", "؜",
             "᠎", "͏", "ㅤ", "⠀", "\U000e0041", "️", " ", " ", " ", "\x00", "\x1b", "", "\ud800", "͸"]


@pytest.mark.parametrize("ch", BAD_CHARS)
def test_invisible_characters_in_approve_line_do_not_count(ch):
    for body in ("/approve D1" + ch, ch + "/approve D1", "/appr" + ch + "ove D1", "/approve" + ch + "D1", "/approve D" + ch + "1", "/approve D1: ok" + ch + "ok"):
        assert _counts(_c(body)) == (False, "not-a-command"), repr(body)
    assert _counts(_c("/approve D1")) == (True, "ok")  # control


def test_non_ascii_digits_and_spaces_do_not_count():
    for body in ("/approve D１", "/approve D١", "/approve　D1", "/ａpprove D1", "／approve D1", "/approve\tD1  ", "/аpprove D1"):
        assert _counts(_c(body))[0] is False, repr(body)


def test_only_a_first_line_command_counts():
    for body in ("", "   ", "thanks\n/approve D1", "> /approve D1", "```\n/approve D1\n```", "    /approve D1", "<!-- x -->\n/approve D1", "/approve", "/approve D1 extra words",
                 "/approve d1", "approve D1", "/approveD1", "/approve\u00a0D1", "/answer D1: x", None, 5, b"/approve D1"):
        assert _counts(_c(body)) == (False, "not-a-command"), repr(body)


def test_hostile_bodies_are_fast_and_do_not_count():
    """Linear work only: 30,000 and 300,000 characters (the second is over MAX_RAW and never parsed)."""
    for n in (30_000, 300_000):
        for body in ("/approve D1" + " " * n + "x", "/approve D1" + "​" * n, " " * n + "/approve D1" + "\n" + "y" * n, "/approve " + "D" * n, "/" + "a" * n,
                     "\n" * n + "/approve D1", "/approve D1: " + "x" * n):
            t = time.perf_counter()
            ok, why = _counts(_c(body))
            assert time.perf_counter() - t < 2.0, (n, body[:20])
            assert why in approvals.REASONS
    assert _counts(_c("/approve D1\n" + "y" * 30_000)) == (True, "ok")  # a long tail after a valid first line is not read


# ---- AC6: a deleted approval ----------------------------------------------------------------------------------------------------------------------

def test_deleted_approval_does_not_count():
    synced = _c("/approve D1", cid=7)
    pinned = approvals.pin(synced)
    fresh = Comments([_c("chatter", cid=8, author=OTHER)])  # the approval's id is gone from the fetched list
    assert approvals.still_present(pinned, Comments([synced])) is True
    assert approvals.still_present(pinned, fresh) is False
    assert approvals.recheck(pinned, fresh, APPROVERS, TOKEN) == (False, "deleted")
    assert approvals.recheck(pinned, Comments([]), APPROVERS, TOKEN) == (False, "deleted")
    # the gate re-closes: with the comment gone, no valid command is left for the target
    assert approvals.newest(Comments([synced]), "D1", {HUMAN}, {TOKEN}) is not None
    assert approvals.newest(fresh, "D1", {HUMAN}, {TOKEN}) is None
    # a different comment that has the same text but another id is not the pinned approval
    assert approvals.recheck(pinned, Comments([_c("/approve D1", cid=9)]), APPROVERS, TOKEN) == (False, "deleted")


# ---- AC7: doctor's pure check (T28 removed E_APPROVAL_NOT_WIRED; comment mode is tested in tests/test_approvals_sync.py) ---------------------------------------------------------------------------------

def test_approver_is_token():
    assert approvals.approver_is_token([HUMAN, TOKEN], TOKEN) is True
    assert approvals.approver_is_token({HUMAN}, TOKEN) is False
    assert approvals.approver_is_token([], TOKEN) is False
    assert approvals.approver_is_token([True], 1) is False  # a bool is not the id 1 (config refuses it anyway)
    assert approvals.approver_is_token([HUMAN], "900001") is True and approvals.approver_is_token([HUMAN], None) is True  # cannot be shown to differ: fail closed
    assert approvals.approver_is_token([HUMAN], True) is True


# ---- every returned reason is a documented code ----------------------------------------------------------------------------------------------------

def test_every_reason_is_in_the_fixed_set():
    assert len(set(approvals.REASONS)) == len(approvals.REASONS) and "ok" in approvals.REASONS
    seen = set()
    cases = [_c(), _c(typ="Bot"), _c(author=OTHER), _c(author=TOKEN), _c(edit_count=2), _c(minimized=True), _c(app={"id": 1}), _c(app=comments.APP_UNKNOWN), _c("hi"), object()]
    for c in cases:
        seen.add(_counts(c)[1])
    seen.add(_counts(_c(), approvers={"x"})[1])
    seen.add(_counts(_c(), token=None)[1])
    pinned = approvals.pin(_c())
    for found in (Comments([]), Comments([_c()]), Comments([_c(body="/approve D2")])):
        seen.add(approvals.recheck(pinned, found, APPROVERS, TOKEN)[1])
    seen.add(approvals.recheck(None, [], APPROVERS, TOKEN)[1])
    seen.add(approvals.recheck(pinned, Comments([_c()], notes=[Note("E_COMMENT_JOIN", 9, "x")]), APPROVERS, TOKEN)[1])
    assert seen <= set(approvals.REASONS)
    assert seen == set(approvals.REASONS), set(approvals.REASONS) - seen  # and each code is reachable


# ---- AC8: the limits are said in plain words -----------------------------------------------------------------------------------------------------------

def test_limits_are_in_help_and_docs(capsys):
    from pathlib import Path
    from ghp.__main__ import main
    with pytest.raises(SystemExit):
        main(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "any writer on the repository can edit or delete any comment" in out and "tamper-evident, not tamper-proof" in out
    assert "tamper-evident, not tamper-proof" in approvals.LIMITS and "edit or delete any comment" in approvals.LIMITS
    docs = " ".join((Path(__file__).resolve().parent.parent / "docs" / "trust-and-limits.md").read_text(encoding="utf-8").split())
    assert "## Approvals by comment" in docs and "any writer" in docs.lower() and "lastEditedAt" in docs and "tamper-evident" in docs and "tamper-proof" in docs


# ---- AC8 (round 1, I1): strict output states the limits when approval is "comment" ---------------------------------------------------------------------

def _strict_project(tmp_path, monkeypatch, approval):
    import json
    cfg = {"owner": "octo", "repo": "demo", "mode": "strict", "strict": {"test_command": "pytest -q"}}
    if approval:
        cfg["approval"] = approval
    (tmp_path / "ghp.config.json").write_text(json.dumps(cfg))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)


@pytest.mark.parametrize("argv", [["strict", "mode"], ["strict", "check", "1001", "--phase", "implement"], ["strict", "verify", "1001", "--no-record"]])
def test_strict_output_states_the_limits_in_comment_mode(tmp_path, monkeypatch, capsys, argv):
    import json
    from conftest import FakeGh
    from ghp.__main__ import main
    _strict_project(tmp_path, monkeypatch, "comment")
    main(argv, gh=FakeGh())
    cap = capsys.readouterr()
    out = json.loads(cap.out)
    assert out["notes"] == [approvals.LIMITS]
    assert "tamper-evident, not tamper-proof" in cap.err and "can edit or delete any comment" in cap.err and "note: " in cap.err
    assert "deletes an approver" in cap.err  # the deletion limit too (round 1, I2)


@pytest.mark.parametrize("approval", [None, "terminal"])
def test_strict_output_has_no_limits_note_in_terminal_mode(tmp_path, monkeypatch, capsys, approval):
    import json
    from conftest import FakeGh
    from ghp.__main__ import main
    _strict_project(tmp_path, monkeypatch, approval)
    for argv in (["strict", "mode"], ["strict", "check", "1001", "--phase", "implement"]):
        main(argv, gh=FakeGh())
        cap = capsys.readouterr()
        assert "notes" not in json.loads(cap.out) and "tamper" not in cap.err
