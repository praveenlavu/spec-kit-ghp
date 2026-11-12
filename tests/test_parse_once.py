"""T15 tests: every comment is parsed once per run, however many targets are asked about, and a comment over the cap is never read. Ported from ado
tests/test_parse_once.py (F4, P4-20 I-4); the tracker seam is a `comments.Comment` with a Markdown body.
Ported by T28: the `_decide` check (below). Not ported: `test_sync_wait_gate_and_cross_check_parse_each_comment_once` (decisions sync, `wait_state`, `strict.xcheck`: T16, T23, T28)."""
import dataclasses
import time

import pytest

from ghp import approvals
from ghp.comments import Comment, Comments, Note
from ghp.issues import Actor

HUMAN, OTHER, ME = 900003, 900005, 900001  # fictional numeric user ids
T = "2026-10-01T10:00:0{}Z"


def row(cid, body, sec=0, author=HUMAN, **kw):
    kw.setdefault("last_edited_at", None)
    kw.setdefault("edit_count", 0)
    return Comment(id=cid, node_id="IC_%d" % cid, author=Actor(author, "login%d" % author, "User"), body=body, created_at=kw.pop("date", T.format(sec)),
                   minimized=False, app=None, viewer_did_author=False, **kw)


def _count_parse(monkeypatch):
    calls = []
    real = approvals._parse
    monkeypatch.setattr(approvals, "_parse", lambda b: (calls.append(1), real(b))[1])
    return calls


def test_each_comment_parsed_once_for_many_targets(monkeypatch):
    rows = [row(i, f"/approve D{i % 30 + 1}", sec=i % 9, author=HUMAN if i % 2 else OTHER) for i in range(1, 41)]
    calls = _count_parse(monkeypatch)
    found = approvals.cached(rows)
    for n in range(1, 31):
        approvals.newest(found, f"D{n}", {HUMAN}, {ME})
    assert len(calls) == 40  # N, not 30 x N
    calls.clear()
    for n in range(1, 4):  # a plain list is parsed per target, as before (callers that care wrap it with `cached`)
        approvals.newest(rows, f"D{n}", {HUMAN}, {ME})
    assert len(calls) == 120


def test_decide_parses_each_comment_once_and_ignores_an_oversized_non_approver_comment(monkeypatch):
    """ado's `_decide` check (T28): three gates, then a re-scan of the comments newer than the winner; each comment is parsed once, and an oversized
    comment by a non-approver does not block the gate."""
    from ghp import comments
    from ghp.config import Trust
    from ghp.gh import Viewer
    from ghp.refs import Ref
    gates = sorted(approvals.VERB_GATES["questions-post"])
    hashes = {g: approvals.gate_hash(g, 7, b"plan") for g in gates}
    rows = [row(i, "chatter %d" % i, sec=1) for i in range(1, 30)] + [row(40, "/approve %s %s" % (gates[0], hashes[gates[0]]), sec=5),
                                                                      row(41, "/reject " + "x" * (approvals.MAX_RAW + 5), sec=6, author=OTHER)]
    monkeypatch.setattr(comments, "fetch", lambda gh, ref: Comments(rows))
    calls = _count_parse(monkeypatch)

    class G:
        def viewer(self):
            return Viewer(ME, "agent", ["repo"])
    d = approvals._decide(G(), Trust([HUMAN], "comment", {}), Ref("octo", "demo", 7), lambda: hashes)
    assert d["verdict"] == "approve" and d["bad"] == 0 and len(calls) == len(rows)


def test_the_cache_follows_the_row_not_the_module(monkeypatch):
    rows = approvals.cached([row(1, "/approve D1")])
    calls = _count_parse(monkeypatch)
    approvals.newest(rows, "D1", {HUMAN}, set())
    rows[0] = dataclasses.replace(rows[0], body="/reject D1")  # an edited row is read again
    assert approvals.newest(rows, "D1", {HUMAN}, set())["verb"] == "reject" and len(calls) == 2
    assert approvals.newest(approvals.cached([row(1, "/approve D1")]), "D1", {HUMAN}, set())["verb"] == "approve" and len(calls) == 3  # a new run


def test_oversized_comment_cannot_be_an_approval():
    cap = approvals.MAX_RAW
    pad = lambda n: "/approve D3\n" + "x" * (n - len("/approve D3\n"))  # noqa: E731
    assert len(pad(cap)) == cap
    ok = approvals.newest([row(1, pad(cap))], "D3", {HUMAN}, set())
    assert ok and ok["verb"] == "approve"  # exactly the cap still reads
    tally = {}
    assert approvals.newest([row(1, pad(cap + 1))], "D3", {HUMAN}, set(), tally=tally) is None
    assert tally == {"bad": 1, "bad_newer": 1, "bad_newer_by": {"malformed": 1}}  # named: an approver's unreadable line, counted for the caller to show (fixed text, a number)
    assert approvals.parse_command(pad(cap + 1)) is None
    # an earlier valid approval stands, with the oversized later line counted as a newer malformed one (it may be a retraction)
    got = approvals.newest([row(1, "/approve D3"), row(2, pad(cap + 1), sec=1)], "D3", {HUMAN}, set())
    assert got["id"] == 1 and got["bad_newer"] == 1
    # a non-approver's is only counted as `bad`
    tally = {}
    assert approvals.newest([row(1, pad(cap + 1), author=OTHER)], "D3", {HUMAN}, set(), tally=tally) is None and tally == {"bad": 1}


def test_oversized_non_approver_comment_leaves_an_approval_unchanged():
    big = "x" * (approvals.MAX_RAW + 1)
    rows = [row(1, "/approve D3"), row(2, big, sec=5, author=OTHER), row(3, "/reject D3: no", sec=6, author=OTHER)]
    got = approvals.newest(rows, "D3", {HUMAN}, set())
    assert got["id"] == 1 and got["verb"] == "approve" and got["bad_newer"] == 0  # a later oversized comment by a non-approver is not a retraction
    # the same comment by the approver does cancel it (fail closed: it may be a retraction)
    rows[1] = row(2, big, sec=5)
    assert approvals.newest(rows, "D3", {HUMAN}, set())["bad_newer"] == 1


def test_oversized_non_approver_comment_does_not_stall():
    big = "> " * 350000  # ~700 KB
    rows = approvals.cached([row(100 + i, big, sec=i % 9, author=OTHER) for i in range(20)])
    t = time.time()
    for n in range(1, 31):
        assert approvals.newest(rows, f"D{n}", {HUMAN}, {ME}) is None
    assert time.time() - t < 5.0


def _reference_newest(comments, target, approvers, excluded, tally=None):
    """The per-target parse as it was before F4 (a copy of `approvals.newest` that calls `_parse` itself for every comment)."""
    tally = {} if tally is None else tally
    count = lambda k, n=1: tally.__setitem__(k, tally.get(k, 0) + n)  # noqa: E731
    wanted, skip, found, bads = set(approvers), set(excluded), [], []
    for c in comments:
        cmd, bad = approvals._parse(c.body) if isinstance(c, Comment) else (None, None)
        if bad is not None:
            count("bad")
            if c.author.id in wanted and c.author.id not in skip and not approvals._elsewhere(bad, target):
                bads.append(approvals._order(c))
        elif isinstance(c, Comment) and c.author.id in wanted and c.author.id not in skip and (c.edited or c.minimized is not False):
            bads.append(approvals._order(c))  # T28 review I2: an approver's edited or hidden comment counts as a newer malformed line
        if cmd is None or cmd["target"] != target:
            continue
        key = approvals._order(c)
        if key is None:
            count("date")
            continue
        found.append((key, cmd, c))
    out = None
    for key, cmd, c in sorted(found, key=lambda t: t[0], reverse=True):
        why = approvals.guard(c, wanted, skip)
        if why is None:
            out = (key, {**cmd, "id": c.id, "created": c.created_at, "author": c.author.id})
            break
        count(why)
    newer = sum(1 for k in bads if k is None or out is None or k > out[0])
    if newer:
        count("bad_newer", newer)
    return None if out is None else {**out[1], "bad_newer": newer}


def test_results_identical_to_before_for_normal_comments():
    h = "ab" * 32
    rows = [row(1, "/approve D1"), row(2, "/reject D1: no", sec=1), row(3, "/approve D1", sec=2, author=OTHER), row(4, "> /reject D2", sec=1),
            row(5, "/approve D2", sec=2), row(6, "/reject D2.", sec=3), row(7, f"/approve review-spec {h}", sec=1), row(8, "/answer Q1: yes", sec=1),
            row(9, "/answer Q1 oops", sec=2), row(10, "/approve D3", sec=1, author=ME), row(11, "/approve D3", sec=2, author=ME), row(12, "/reject D3: x", sec=3, edit_count=2),
            row(13, "/approve D4\nthanks", sec=1), row(14, "/approve D4", sec=1, date="not a date"), row(15, "hello", sec=1), row(16, None, sec=1),
            row(17, "/approve D5" + " " * 70000, sec=1), row(18, "/reject D5", sec=2, author=ME), row(19, "\u200b", sec=2), row(20, "/approve D6: a@b.example", sec=1)]
    rows.append("not a row")
    targets = ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "Q1", "review-spec", "review-plan"]
    cached = approvals.cached(rows)
    for t in targets:
        for approvers in ({HUMAN}, {HUMAN, OTHER}, {ME}, set()):
            t1, t2 = {}, {}
            want = _reference_newest(rows, t, approvers, {ME}, tally=t1)
            assert approvals.newest(cached, t, approvers, {ME}, tally=t2) == want, (t, approvers)
            by = t2.pop("bad_newer_by", {})  # T31c: the reason split of bad_newer, new since the reference
            assert t1 == t2 and sum(by.values()) == t2.get("bad_newer", 0), (t, approvers)


def test_rows_keep_what_fetch_dropped():
    found = Comments([row(1, "/approve D1")], [Note("E_COMMENT_JOIN", 2, "only in REST")])
    assert approvals.cached(found).notes == found.notes and approvals.Rows(found, {}).notes == found.notes
    assert approvals.cached([row(1, "/approve D1")]).notes == []
