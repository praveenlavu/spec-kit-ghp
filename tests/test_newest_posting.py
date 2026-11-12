"""T28d port of ado tests/test_newest_posting.py (F2, P4-20 slice A, I-2): one "newest posting" rule for `decisions --sync`, the `wait-po-validation`
gate and the done-time cross-check. An approval counts only for the line of the NEWEST decisions posting that lists that D id; a file line set back to an
older posting never counts.

GitHub seam (rename table X5): ado's `raw_comment` / `_rows` are `comments.Comment` rows (test_approvals_sync.C), the HTML posting is the kit's Markdown
posting (`decisions.with_marker`, `<marker> <16 hex>` first line), "modified_by" is an edit (`last_edited_at`, `edit_count`), the kit posts as the token
identity (the wait gate needs one of the token's own), and `RouteClient` is the `comments.fetch` stub. Where GitHub differs:
- the wait gate refuses (E_DECISIONS) every decisions comment that is not a plain, unedited kit posting, so a disguised or hand-made "posting" by an approver
  (ado's comment 25 / 41 in the index test) makes the gate refuse; the index and `superseded` give the same answers as ado's per-line reading.
- an edited posting (ado: modified_by) is read the same: `posting-edited`, the wait gate refuses, the cross-check reports the marker.
- ghp-only rows (each exposed a fail-open of `decisions --sync`, fixed in T28d): a newer malformed (or edited, I2) approver line made the sync write the
  older verdict while the gate stayed open (now `bad-newer`: the line stays open, so the cross-check has nothing to report); a marked comment the sync
  cannot read (a disguised kit marker, a newer posting edited past its content hash, an unreadable time) made the index blind to a newer posting, so a
  reverted line was validated (now `posting-disguised`).

Tests not ported:
- `test_bypass_refusal_comes_before_the_decisions_file_read`, its bypass half: ADO's bypass bit has no GitHub counterpart. Its second half (the decisions
  file is read for `wait-po-validation` only) is ported below.
Offline only; every id and text is fictional."""
import dataclasses
import time

import pytest

from ghp import approvals, comments, decisions, strict
from ghp.comments import Comments

from test_approvals_sync import COMMENT, EXCL, HUMAN, N, REF, S1, TOKEN, C, NoWriteGh, Thread, _check, _dfile, _review_repo

X, Y = S1, S1.replace("cursor paging", "offset paging")
OPEN = "open D1"
REFUSED = "refused: " + approvals.E_DECISIONS


@pytest.fixture
def thread(monkeypatch):
    t = Thread()
    monkeypatch.setattr(comments, "fetch", t.fetch)
    return t


def _body(line):
    """The exact Markdown comment the kit would post for the decisions file holding `line`."""
    todo = decisions.unposted(decisions.parse_decisions(_dfile(line)))
    return decisions.with_marker("\n".join([decisions._HEAD] + [decisions._comment_line(d)[0] for d in todo]))


def _md(line, cid):
    md = _dfile(line)
    return decisions._marked(md, {d["id"]: decisions.marker_hash(d) for d in decisions.unposted(decisions.parse_decisions(md))}, cid)


def _post(line, cid, minute, author=TOKEN, **kw):
    return C(_body(line), cid, author=author, minute=minute, **kw)


def _vote(cid=30, text="/approve D1", minute=30, author=HUMAN, **kw):
    return C(text, cid, author=author, minute=minute, **kw)


EDIT = dict(edit_count=2, last_edited_at="2026-10-02T00:00:00Z")


def _paths(thread, md, rows):
    """(sync verdict, wait-gate verdict, cross-check rules) of one file text over one issue: the three paths, on one set of comments."""
    new, rep = decisions.sync(md, Comments(rows), {HUMAN}, EXCL)
    thread.items = list(rows)
    try:
        st = approvals.wait_state(NoWriteGh(), COMMENT, REF, "wait-po-validation", decisions_md=md)
        wait = "met" if st["met"] else "open " + ",".join(st["open"])
    except approvals.ApprovalMissing as e:  # a refusal is not an open id
        wait = "refused: " + str(e)
    xc = [f["rule"].rsplit("-", 1)[1] for f in strict.xcheck(new, Comments(rows), False, {HUMAN}, EXCL, N)]
    return rep["skipped"].get("D1", "applied"), wait, xc


LONG = "- [ ] D1: " + "x" * 600 + " → _(open)_ · kind: decision · why: w"
MATRIX = {  # name: (file text, comments) -> (sync, wait gate, cross-check)
    "unchanged": (lambda: (_md(X, 10), [_post(X, 10, 0), _vote(minute=20)]), ("applied", "met", [])),
    "reworded+repost, approve the new text": (lambda: (_md(Y, 20), [_post(X, 10, 0), _post(Y, 20, 10), _vote()]), ("applied", "met", [])),
    "repost+revert": (lambda: (_md(X, 10), [_post(X, 10, 0), _post(Y, 20, 10), _vote()]), ("posting-superseded", OPEN, ["dropped"])),
    "old text under the newest marker": (lambda: (_md(X, 10).replace("posted: 10", "posted: 20"), [_post(X, 10, 0), _post(Y, 20, 10), _vote()]),
                                         ("posting-mismatch", OPEN, ["marker"])),
    "edited posting": (lambda: (_md(X, 10), [_post(X, 10, 0, **EDIT), _vote(minute=20)]), ("posting-edited", REFUSED, ["marker"])),
    "repost edited, then revert": (lambda: (_md(X, 10), [_post(X, 10, 0), _post(Y, 20, 10, **EDIT), _vote()]),  # an edited newer posting is still the newest
                                   ("posting-superseded", REFUSED, ["marker", "dropped"])),
    "cut line": (lambda: (_md(LONG, 10), [_post(LONG, 10, 0), _vote(minute=20)]), ("posting-lossy", OPEN, ["marker"])),
    "missing posting": (lambda: (_md(X, 99), [_post(X, 10, 0), _vote(minute=20)]), ("posting-missing", OPEN, ["marker"])),
    # ghp-only rows
    "newer malformed approver line": (lambda: (_md(X, 10), [_post(X, 10, 0), _vote(minute=20), _vote(31, "/reject D1 nope", minute=40)]),
                                      ("bad-newer", OPEN, [])),  # the line stays open: an honest file, nothing for the cross-check to report
    "disguised kit marker by an approver": (lambda: (_md(X, 10), [_post(X, 10, 0), _vote(minute=20), _vote(31, "> " + _body(Y), minute=25)]),
                                            ("posting-disguised", REFUSED, ["marker"])),
    "newer posting edited past reading, then revert": (lambda: (_md(X, 10), [_post(X, 10, 0), C(_body(Y) + " (edited)", 20, author=TOKEN, minute=10, **EDIT),
                                                                            _vote()]), ("posting-disguised", REFUSED, ["marker"])),
    "unreadable time on a newer posting": (lambda: (_md(X, 10), [_post(X, 10, 0), dataclasses.replace(_post(Y, 20, 10), created_at="soon"), _vote()]),
                                           ("posting-disguised", REFUSED, ["marker"])),
}


@pytest.mark.parametrize("name", MATRIX)
def test_three_paths_agree_on_matrix(thread, name):
    make, want = MATRIX[name]
    md, rows = make()
    got = _paths(thread, md, rows)
    assert got == want  # exact: a refusal and an open id are different outcomes
    assert (got[0] == "applied") == (got[1] == "met")  # one verdict
    assert got[2] == [] if got[0] in ("applied", "bad-newer") else got[2]  # the cross-check reports every claim the file makes that does not hold


def _revert():
    return _md(X, 10), [_post(X, 10, 0), _post(Y, 20, 10), _vote()]


def test_reworded_repost_then_revert_not_validated_by_sync():
    md, rows = _revert()
    new, rep = decisions.sync(md, Comments(rows), {HUMAN}, EXCL)
    assert rep["applied"] == [] and rep["skipped"] == {"D1": "posting-superseded"} and new == md
    assert "posting-superseded" in decisions.SYNC_RULES


def test_reworded_repost_then_revert_wait_gate_open(thread):
    md, rows = _revert()
    thread.items = rows
    gh = NoWriteGh()
    assert approvals.wait_state(gh, COMMENT, REF, "wait-po-validation") == {"met": True, "open": [], "bad_newer": 0}  # GitHub alone: the approver saw Y
    assert approvals.wait_state(gh, COMMENT, REF, "wait-po-validation", decisions_md=md) == {"met": False, "open": ["D1"], "bad_newer": 0}
    assert not gh.armed


def test_reworded_repost_then_revert_xcheck_finding():
    md, rows = _revert()
    out = strict.xcheck(md, Comments(rows), False, {HUMAN}, EXCL, N)
    assert [f["rule"] for f in out] == ["decisions-xcheck-dropped"] and "D1" in out[0]["detail"]


def test_honest_repost_then_approve_new_text_passes_all_three(thread):
    make, _ = MATRIX["reworded+repost, approve the new text"]
    assert _paths(thread, *make()) == ("applied", "met", [])
    assert _paths(thread, _md(X, 10), [_post(X, 10, 0), _vote(minute=20)])[0] == "applied"  # one posting, never superseded


def test_a_wait_gate_ignores_unmarked_lines_and_an_unreadable_file_is_refused(tmp_path, monkeypatch, thread, capsys):
    md, rows = _revert()
    thread.items = rows
    assert approvals.wait_state(NoWriteGh(), COMMENT, REF, "wait-po-validation", decisions_md=_dfile(X))["met"] is True  # no marker: nothing to check
    _review_repo(tmp_path, monkeypatch)
    import test_approvals_sync
    test_approvals_sync.trust_is(monkeypatch, COMMENT)
    path = tmp_path / "dod" / "Decisions_7.md"
    path.write_bytes(b"\xff\xfe")
    code, _, err = _check(capsys, NoWriteGh(), "wait-po-validation")
    assert code == 2 and approvals.E_DECISIONS_FILE in err
    path.write_text(md, encoding="utf-8")
    code, _, err = _check(capsys, NoWriteGh(), "wait-po-validation")
    assert code == 2 and approvals.E_WAIT.format(gate="wait-po-validation", n=N, k=1, ids="D1", bad="") in err
    path.unlink()
    assert _check(capsys, NoWriteGh(), "wait-po-validation")[0] == 0  # no file: the comments alone


def test_the_decisions_file_is_read_for_wait_po_validation_only(thread):
    """ado's `test_bypass_refusal_comes_before_the_decisions_file_read`, second half (the bypass half is ADO only)."""
    thread.items = _revert()[1]
    seen = []
    approvals.wait_state(NoWriteGh(), COMMENT, REF, "wait-po-answers", decisions_md=lambda: seen.append(1))
    assert seen == []
    approvals.wait_state(NoWriteGh(), COMMENT, REF, "wait-po-validation", decisions_md=lambda: seen.append(1))
    assert seen == [1]


def _reference_superseded(d, rows):
    """The per-line reading the index replaced (ado F2 first version): re-read every marked comment for this one id."""
    n = int(d["id"][1:])
    best, key, shown = None, None, ()
    for c in rows:
        k = approvals._order(c)
        if k is None or (key is not None and k <= key) or not approvals.marked(c, decisions.MARKER):
            continue
        lines = tuple(" ".join(ln.split()) for ln in approvals.kit_lines(c.body, decisions.MARKER)
                      if (m := decisions.POSTED_LINE.match(ln)) and int(m.group(1)) == n)
        if lines:
            best, key, shown = c, k, lines
    return bool(d["posted"] and best is not None and (int(d["posted"]["comment_id"]) != best.id
                                                      or " ".join(decisions._comment_line(d)[0].split()) not in shown))


def test_index_gives_the_same_answers_as_the_per_line_reading():
    z = S1.replace("cursor paging", "page cursors")
    two = "- [ ] D2: Cap at 50 rows → _(open)_ · kind: assumption · why: the story is silent"
    rows = [_post(X, 10, 0), _post(Y, 20, 10), _post(X + "\n" + two, 25, 15, author=HUMAN), _post(z, 30, 20, **EDIT),
            _vote(40, "/approve D1", minute=30), C(decisions.MARKER + "\n<span>x</span>", 41, minute=30), C("> " + _body(z), 42, minute=31)]
    lines = [_md(X, 10), _md(Y, 20), _md(X, 20), _md(z, 30), _md(X, 99), _dfile(X), _md(X + "\n" + two, 25), _md(z, 42)]
    index = decisions.posting_index(rows)
    checked = 0
    for md in lines:
        for k in ("open", "validated", "rejected"):
            for d in decisions.parse_decisions(md)[k]:
                assert decisions.superseded(d, index) == _reference_superseded(d, rows), (md, d["id"])
                checked += 1
    assert checked == 9 and index[1][0].id == 30 and index[2][0].id == 25  # an edited or another author's posting is still a claim to check


def test_cost_is_linear_in_comment_bytes_not_decisions_times_comments(monkeypatch):
    calls, real = [], approvals.kit_lines
    monkeypatch.setattr(approvals, "kit_lines", lambda *a, **k: calls.append(1) or real(*a, **k))
    junk = decisions.with_marker("\n".join("not a decision line %d " % i + "x" * 90 for i in range(2000)))  # ~200 KB, a plain kit body, no D lines
    lines = ["- [ ] D%d: Item %d → _(open)_ · kind: decision · why: w" % (n, n) for n in range(1, 31)]
    md = _md("\n".join(lines), 10)
    rows = [_post("\n".join(lines), 10, 0)] + [C(junk, 100 + i, minute=10) for i in range(20)]
    t = time.monotonic()
    new, rep = decisions.sync(md, Comments(rows), {HUMAN}, EXCL)
    strict.xcheck(new, Comments(rows), False, {HUMAN}, EXCL, N)
    # each comment is read a constant number of times, however many decisions are asked about
    assert rep["applied"] == [] and len(calls) < 10 * len(rows), (len(calls), len(rows))
    assert time.monotonic() - t < 60  # last-resort sanity bound only; the call count is the real check


@pytest.mark.parametrize("size", [30_000, 300_000])
def test_hostile_postings_are_linear(size):
    """Linear-regex probe: a hostile marked body of 30,000 / 300,000 characters through the index, the sync and the cross-check."""
    bodies = [decisions.MARKER + " " + "D1 (decision): " * (size // 15), decisions.with_marker("D1 (decision): " + "·" * size),
              "> " * (size // 2) + decisions.MARKER, decisions.with_marker(decisions._HEAD + "\n" + ("D1 (decision): x\n" * (size // 17)))]
    rows = [_post(X, 10, 0)] + [C(b, 20 + i, minute=10) for i, b in enumerate(bodies)]
    t = time.monotonic()
    decisions.posting_index(rows)
    new, _ = decisions.sync(_md(X, 10), Comments(rows), {HUMAN}, EXCL)
    strict.xcheck(new, Comments(rows), False, {HUMAN}, EXCL, N)
    assert time.monotonic() - t < 2
