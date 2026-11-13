"""T31c: a newer approver line that blocks a verdict is reported with the wording of ITS reason: malformed, edited or hidden (minimized). The
reason codes are stable (`bad_newer_by`: {"malformed"|"edited"|"minimized": n}); the total `bad_newer` is unchanged."""
import json

import pytest

import test_approvals_sync as S
from test_approvals_sync import thread  # noqa: F401 (the fixture)
import ghp.__main__ as cli
from ghp import approvals
from ghp.comments import Comments

EDIT = {"last_edited_at": "2026-10-02T00:00:00Z", "edit_count": 1}
WORDS = {"malformed": "1 newer malformed command line(s) by an approver",
         "edited": "1 newer approver comment(s) edited after posting",
         "minimized": "1 newer approver comment(s) hidden (minimized)"}


def later(reason, body="/reject D3"):
    if reason == "malformed":
        return S.C("/reject D3 not ok", 12)
    return S.C(body, 12, **(EDIT if reason == "edited" else {"minimized": True}))


@pytest.mark.parametrize("reason", sorted(WORDS))
def test_newest_counts_each_reason_under_its_code(reason):
    tally = {}
    win = approvals.newest(Comments([S.C("/approve D3", 11), later(reason)]), "D3", {S.HUMAN}, set(), tally=tally)
    assert win["bad_newer"] == 1 and tally["bad_newer"] == 1 and tally["bad_newer_by"] == {reason: 1}


def test_an_edited_and_hidden_comment_is_counted_once_as_edited():
    tally = {}
    approvals.newest(Comments([S.C("/approve D3", 11), S.C("x", 12, minimized=True, **EDIT)]), "D3", {S.HUMAN}, set(), tally=tally)
    assert tally["bad_newer"] == 1 and tally["bad_newer_by"] == {"edited": 1}  # the guard's order: edited before minimized


def test_each_reason_has_its_own_fixed_wording():
    for reason, words in WORDS.items():
        assert approvals.bad_newer_text({reason: 1}) == words
        others = [w for r, w in WORDS.items() if r != reason]
        assert all(o not in approvals._bad({reason: 1}) for o in others)
    assert approvals.bad_newer_text({"malformed": 2, "minimized": 1}, "later") == (
        "2 later malformed command line(s) by an approver; 1 later approver comment(s) hidden (minimized)")
    assert approvals._bad({}) == "" and approvals.bad_newer_text({}) == ""
    assert approvals._bad({"edited": 1}) == " 1 newer approver comment(s) edited after posting for this plan: check the thread."


@pytest.mark.parametrize("reason", sorted(WORDS))
def test_the_apply_refusal_names_the_reason(reason, thread, tmp_path):
    g, x = "approve-push", S.h("approve-push", b"plan")
    bad = S.C("/reject %s" % g, 12) if reason == "malformed" else later(reason, "/reject %s %s" % (g, x))
    thread.items = [S.C("/approve %s %s" % (g, x), 11), bad]
    with pytest.raises(approvals.ApprovalMissing) as e:
        S.require(S.NoWriteGh(), repo=tmp_path)
    assert WORDS[reason] in str(e.value) and all(w not in str(e.value) for r, w in WORDS.items() if r != reason)


@pytest.mark.parametrize("reason", sorted(WORDS))
def test_decisions_sync_reports_the_reason(reason):
    md, body = S._posted(S.S1)
    bad = S._vote(12, "/reject D1 not ok", minute=6) if reason == "malformed" else (
        S._vote(12, "/reject D1", minute=6, **EDIT) if reason == "edited" else S._vote(12, "/reject D1", minute=6, minimized=True))
    _, res = S._dsync(md, [S._kit(body), S._vote(11, "/approve D1"), bad])
    assert res["skipped"] == {"D1": "bad-newer"} and res["bad_newer"] == {"D1": 1} and res["bad_newer_by"] == {"D1": {reason: 1}}


def _later_vote(reason, cid, text, minute):
    if reason == "malformed":
        return S._vote(cid, text + " not ok", minute=minute)
    return S._vote(cid, text, minute=minute, **(EDIT if reason == "edited" else {"minimized": True}))


@pytest.mark.parametrize("reason", sorted(WORDS))
def test_the_decisions_sync_notice_uses_the_reason_wording(reason, tmp_path, monkeypatch, thread, capsys):
    md, body = S._posted(S.S1)
    S._dec_repo(tmp_path, monkeypatch, md)
    S.trust_is(monkeypatch, S.COMMENT)
    thread.items = [S._kit(body, author=S.TOKEN), S._vote(11, "/approve D1"), _later_vote(reason, 12, "/reject D1", 6)]
    assert cli.main(["decisions", "7", "--sync"], gh=S.NoWriteGh()) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["notice"] == ["D1: %s: check the thread" % WORDS[reason].replace("newer", "later")] and out["bad_newer_by"] == {"D1": {reason: 1}}


@pytest.mark.parametrize("reason", sorted(WORDS))
def test_the_questions_sync_notice_uses_the_reason_wording(reason, tmp_path, monkeypatch, thread, capsys):
    from ghp import questions
    md = S._qledger(S.QA)
    S._qrepo(tmp_path, monkeypatch, md)
    S.trust_is(monkeypatch, S.COMMENT)
    thread.items = [S._qkit(questions.parse_questions(md)["open"]), S._vote(11, "/answer Q1: yes"), _later_vote(reason, 12, "/answer Q1: no", 13)]
    if reason == "malformed":
        thread.items[-1] = S._vote(12, "/answer Q1 oops", minute=13)
    assert cli.main(["questions", "7", "--sync"], gh=S.NoWriteGh()) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["notice"] == ["Q1: %s: check the thread" % WORDS[reason].replace("newer", "later")] and out["bad_newer_by"]["Q1"] == {reason: 1}


def test_the_refusal_says_why_an_approvers_approve_did_not_count(thread, tmp_path):
    # the approver once posted a comment starting with the kit's marker: they are excluded on this issue, and the refusal says so
    g, x = "approve-push", S.h("approve-push", b"plan")
    thread.items = [S.C("[spec-kit-ghp:questions] copied by hand", 10), S.C("/approve %s %s" % (g, x), 11)]
    with pytest.raises(approvals.ApprovalMissing) as e:
        S.require(S.NoWriteGh(), repo=tmp_path)
    text = str(e.value)
    assert "1 /approve or /reject comment(s) for this plan's gates did not count" in text and "[spec-kit-ghp:" in text
    assert "copied by hand" not in text  # counts and fixed words only, never comment text
    thread.items = [S.C("/approve %s %s" % (g, x), 11, author=S.OTHER)]  # not an approver
    with pytest.raises(approvals.ApprovalMissing) as e:
        S.require(S.NoWriteGh(), repo=tmp_path)
    assert "1 by an account that is not an approver" in str(e.value)
    # a winning /approve read again past a newer malformed line: the set-aside command newer than it is counted once, not once per scan
    thread.items = [S.C("/approve %s %s" % (g, x), 11), S.C("/approve %s %s" % (g, x), 12, author=S.OTHER), S.C("/approve %s nothash" % g, 13)]
    with pytest.raises(approvals.ApprovalMissing) as e:
        S.require(S.NoWriteGh(), repo=tmp_path)
    assert "1 /approve or /reject comment(s) for this plan's gates did not count: 1 by an account" in str(e.value)
    thread.items = []  # nothing there: nothing to explain
    with pytest.raises(approvals.ApprovalMissing) as e:
        S.require(S.NoWriteGh(), repo=tmp_path)
    assert "did not count" not in str(e.value)
