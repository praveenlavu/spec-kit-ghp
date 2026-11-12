"""T57: the rest of ado's trust-core minors (tests/test_trust_minors.py there), ported to the GitHub seam (rename table). One group per finding:
a question whose last showing was cut stays open; `decisions --sync` reports a synced verdict that no longer counts; trust keys in a config file that
is not the repo root are refused however that file is reached; the verbs that read the approval keys refuse a target they cannot read.

GitHub has no comment versions: the kit posts a NEW questions comment for every change, so ado's "version 2 of the comment" is a later comment here,
and an edited approver comment is a malformed newer line, never a verdict. ado tests ported elsewhere or not portable are listed in PLAN section 4.
Offline only; every id and text is fictional."""
import datetime
import json

import pytest

import ghp.__main__ as cli
from ghp import approvals, questions
from ghp.config import E_NONROOT_TRUST, ConfigInvalid, Trust, trusted
from conftest import commit, git
from test_approvals_sync import (COMMENT, HUMAN, OTHER, REF, S1, NoWriteGh, _dec_repo, _dsync, _kit, _posted, _qkit, _qledger, _qsync, _vote, thread,  # noqa: F401 (a fixture)
                                 trust_is)
from test_config import BASE, G1, _trust_repo

EDITED = {"edit_count": 2, "last_edited_at": "2026-10-01T10:30:00Z"}

# ---- M-1: a question whose NEWEST showing is cut stays open at wait-po-answers, as a decision does at wait-po-validation ----
LONGQ = {"id": "Q1", "question": "Should the export include " + "archived and deleted items " * 30 + "?"}
SHORTQ = {"id": "Q1", "question": "Include archived items?"}
Q2 = {"id": "Q2", "question": "Which locale?"}


def _wait(thread, items, trust=COMMENT, gh=None):
    thread.items = list(items)
    return approvals.wait_state(gh or NoWriteGh(), trust, REF, "wait-po-answers")


class ClockGh(NoWriteGh):
    """A Gh with a trusted server clock a month after the comments: every question would be due."""

    def server_now(self):
        return datetime.datetime(2026, 11, 1, tzinfo=datetime.timezone.utc)


def test_a_cut_question_stays_open_until_a_short_repost_is_answered(thread):
    cut = _qkit([LONGQ], cid=10, minute=0)
    assert cut.body.rstrip().endswith("…")  # the kit cut it at 500 characters
    assert _wait(thread, [cut]) == {"met": False, "open": ["Q1"], "bad_newer": 0, "cut": ["Q1"]}
    assert _wait(thread, [cut, _vote(11, "/answer Q1: yes", minute=1)]) == {"met": False, "open": ["Q1"], "bad_newer": 0, "cut": ["Q1"]}
    short = _qkit([SHORTQ], cid=12, minute=2)
    assert _wait(thread, [cut, _vote(11, "/answer Q1: yes", minute=1), short])["open"] == ["Q1"]  # an answer older than the short repost is no answer
    assert _wait(thread, [cut, _vote(11, "/answer Q1: yes", minute=1), short, _vote(13, "/answer Q1: yes", minute=3)]) == {"met": True, "open": [], "bad_newer": 0}


def test_a_cut_question_is_not_closed_by_the_assume_window_either(thread):
    hours = Trust([HUMAN], "comment", {"assume_after_hours": 1})
    assert _wait(thread, [_qkit([LONGQ])], trust=hours, gh=ClockGh())["open"] == ["Q1"]
    assert _wait(thread, [_qkit([SHORTQ])], trust=hours, gh=ClockGh())["met"] is True  # the honest flow: a short question is due


def test_sync_and_the_gate_agree_on_a_cut_question():
    md = _qledger("- [ ] Q1: %s → _(open)_ · owner: PO\n" % LONGQ["question"])
    new, rep = _qsync(md, [_qkit([LONGQ]), _vote(11, "/answer Q1: yes", minute=1)])
    assert rep["skipped"] == {"Q1": "posting-lossy"} and rep["answered"] == [] and new == md  # sync and gate: both leave it open


def test_the_gate_text_says_why_a_cut_question_stays_open(tmp_path, thread):
    thread.items = [_qkit([LONGQ]), _vote(11, "/answer Q1: yes", minute=1)]
    with pytest.raises(approvals.ApprovalMissing) as e:
        approvals.wait_check(NoWriteGh(), COMMENT, REF, "wait-po-answers", None, repo=str(tmp_path))
    assert str(e.value).endswith(approvals.E_CUT_OPEN.format(ids="Q1")) and "last shown cut" in str(e.value)
    assert "1 open (Q1)" in str(e.value)


def test_a_question_that_vanished_after_being_shown_cut_stays_open(thread):
    """The last showing counts, as for decisions: an /answer given to the cut text must not close it once the comments drop the question."""
    v1, v2 = _qkit([LONGQ, Q2], cid=10, minute=0), _qkit([Q2], cid=12, minute=2)
    answers = [_vote(11, "/answer Q1: yes", minute=1), _vote(13, "/answer Q2: en", minute=3)]
    assert _wait(thread, [v1, answers[0], v2, answers[1]]) == {"met": False, "open": ["Q1"], "bad_newer": 0, "cut": ["Q1"]}
    # honest flow: shown cut, then short, then dropped: the LAST showing was short, so an answer closes it as before
    short, v3 = _qkit([SHORTQ, Q2], cid=12, minute=1), _qkit([Q2], cid=14, minute=3)
    got = _wait(thread, [v1, short, _vote(13, "/answer Q1: yes", minute=2), v3, _vote(15, "/answer Q2: en", minute=4)])
    assert got == {"met": True, "open": [], "bad_newer": 0}


# ---- M-2 and m-1: `decisions --sync` reports a verdict whose `via` comment no longer counts ----
def _synced():
    md, body = _posted(S1)
    synced = _dsync(md, [_kit(body), _vote(11, "/approve D1")])[0]
    assert "via: 11" in synced
    return synced, body


def test_sync_reports_a_verdict_whose_via_comment_was_edited():
    synced, body = _synced()
    for text in ("/reject D1: no", "/approve D1"):  # whatever it says now: an edited comment is no verdict
        new, res = _dsync(synced, [_kit(body), _vote(11, text, **EDITED)])
        assert new == synced and res["skipped"] == {"D1": "no-verdict"} and res["no_longer_valid"] == ["D1"], text


def test_sync_reports_a_verdict_whose_approver_was_removed():
    synced, body = _synced()
    new, res = _dsync(synced, [_kit(body), _vote(11, "/approve D1")], approvers=(OTHER,))
    assert new == synced and res["no_longer_valid"] == ["D1"]


def test_sync_honest_flows_have_no_such_report():
    synced, body = _synced()
    new, res = _dsync(synced, [_kit(body), _vote(11, "/approve D1")])  # the comment still counts
    assert "no_longer_valid" not in res and res["skipped"] == {"D1": "up-to-date"}
    new, res = _dsync(synced, [_kit(body), _vote(11, "/approve D1"), _vote(12, "/reject D1: no", minute=9)])  # a newer valid command: rewritten, not lost
    assert "no_longer_valid" not in res and res["applied"] == ["D1"]
    md, body = _posted(S1)
    assert "no_longer_valid" not in _dsync(md, [_kit(body)])[1]  # an open line with no verdict yet: nothing was lost


def test_sync_verb_notes_the_lost_approval_on_stderr_exit_0(tmp_path, monkeypatch, thread, capsys):
    synced, body = _synced()
    _dec_repo(tmp_path, monkeypatch, synced)
    trust_is(monkeypatch, COMMENT)
    thread.items = [_kit(body), _vote(11, "/reject D1: no", **EDITED)]
    assert cli.main(["decisions", "7", "--sync"], gh=NoWriteGh()) == 0
    cap = capsys.readouterr()
    assert json.loads(cap.out)["no_longer_valid"] == ["D1"] and cap.err.strip() == "approval-no-longer-valid: D1"
    assert (tmp_path / "dod" / "Decisions_7.md").read_text(encoding="utf-8") == synced


def test_an_older_verdict_remaining_does_not_hide_the_lost_via():
    """A sync wrote `via: 11` while an approver's earlier verdict (9) older than the posting stays in the thread; comment 11 is then edited."""
    md, body = _posted(S1)
    pre = [_vote(9, "/approve D1", minute=0)]
    synced = _dsync(md, pre + [_kit(body, minute=1), _vote(11, "/approve D1")])[0]
    assert "via: 11" in synced
    new, res = _dsync(synced, pre + [_kit(body, minute=1), _vote(11, "/reject D1: no", **EDITED)])
    assert new == synced and res["skipped"] == {"D1": "bad-newer"} and res["no_longer_valid"] == ["D1"]  # was: silent in ado before the fix


def test_a_deleted_via_falling_back_to_an_older_verdict_is_reported():
    """Comment 11 behind `via: 11` is deleted; the only verdict left (9) is older than the posting, so the line is kept and reported."""
    md, body = _posted(S1)
    pre = [_vote(9, "/approve D1", minute=0)]
    synced = _dsync(md, pre + [_kit(body, minute=1), _vote(11, "/approve D1")])[0]
    new, res = _dsync(synced, pre + [_kit(body, minute=1)])
    assert new == synced and res["skipped"] == {"D1": "older-than-posting"} and res["no_longer_valid"] == ["D1"]


def test_a_via_line_skipped_for_a_posting_rule_is_not_reported_but_up_to_date_is_not_either():
    """A posting rule (here posting-edited) shows in `skipped` already and is not reported again; up-to-date is not a loss."""
    synced, body = _synced()
    new, res = _dsync(synced, [_kit(body, **EDITED), _vote(11, "/approve D1")])
    assert res["skipped"] == {"D1": "posting-edited"} and "no_longer_valid" not in res and new == synced
    new, res = _dsync(synced, [_kit(body), _vote(11, "/approve D1")])
    assert "no_longer_valid" not in res and res["skipped"] == {"D1": "up-to-date"}


# ---- M-4 and m-3: trust keys in a config file that is not the repo root are refused, however it is reached ----
def _nonroot(repo, data, monkeypatch, name="config/ghp.json"):
    p = repo / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data))
    monkeypatch.setenv("GHP_CONFIG", str(p))
    return p


def test_a_non_root_config_with_a_trust_key_is_invalid_with_no_origin_head_too(tmp_path, git_env, monkeypatch):
    repo = _trust_repo(tmp_path, BASE, origin=False)
    _nonroot(repo, {**BASE, "approvers": [G1]}, monkeypatch)
    with pytest.raises(ConfigInvalid) as e:
        trusted("main", repo=str(repo))
    assert str(e.value) == E_NONROOT_TRUST
    with pytest.raises(ConfigInvalid):  # and the verbs' own read, with no origin/HEAD to read a target from, refuses too
        approvals.target_trust(str(repo))


def test_the_root_file_is_recognised_however_it_is_reached(tmp_path, git_env, monkeypatch):
    repo = _trust_repo(tmp_path, {**BASE, "approvers": [G1]})
    (repo / "svc").mkdir()
    monkeypatch.setenv("GHP_CONFIG", str(repo / "ghp.config.json"))  # the root file by an absolute path, run from a subdirectory
    (repo / "ghp.config.json").write_text(json.dumps({**BASE, "approvers": [G1]}))
    assert trusted("main", repo=str(repo / "svc")).approvers == [G1]


def test_the_root_file_is_recognised_in_any_letter_case_on_a_case_insensitive_disk(tmp_path, git_env, monkeypatch):
    repo = _trust_repo(tmp_path, {**BASE, "approvers": [G1]})
    upper = repo / "GHP.CONFIG.JSON"
    if not upper.exists():
        pytest.skip("case-sensitive disk: GHP.CONFIG.JSON is another file here (the macOS default disk is case-insensitive)")
    monkeypatch.setenv("GHP_CONFIG", str(upper))
    assert trusted("main", repo=str(repo)).approvers == [G1]


def test_the_refusal_is_exit_2_for_the_verbs_that_read_the_keys_and_gate_is_unaffected(tmp_path, git_env, monkeypatch, capsys):
    """`gate` never reads the approval keys, so a non-root file with them keeps the PR check green; the sync verbs refuse."""
    repo = _trust_repo(tmp_path, BASE)
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    (repo / "dod").mkdir()
    (repo / "dod" / "DoD_7.md").write_text("## 3. AC\n", encoding="utf-8")
    other = _nonroot(repo, {**BASE, "approvers": [G1], "approval": "comment"}, monkeypatch)
    monkeypatch.chdir(repo)
    assert cli.main(["gate", "7", "--offline", "--src", str(repo)], gh=NoWriteGh()) == 0
    assert "error:" not in capsys.readouterr().err
    for argv in (["decisions", "7", "--sync"], ["questions", "7", "--sync"]):
        assert cli.main(argv, gh=NoWriteGh()) == 2, argv
        err = capsys.readouterr().err
        assert E_NONROOT_TRUST in err and str(other) not in err, argv


# ---- the verbs that read the approval keys read the target branch's config for every user, terminal mode included ----
@pytest.mark.parametrize("argv", [["approvals", "check", "7", "--gate", "review-spec"], ["approvals", "sync", "20261001-100000-ab12cd34"],
                                  ["questions", "7", "--sync"], ["decisions", "7", "--sync", "--if-comment-mode"],
                                  ["decisions", "7", "--resolve", "D1", "--validated"]])
def test_terminal_mode_verbs_read_the_target_config_and_refuse_a_dangling_origin_head(tmp_path, monkeypatch, capsys, git_env, argv):
    """No trust key anywhere, `origin/HEAD` pointing nowhere: exit 2 with the target-config refusal before any GitHub call."""
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "a", {"ghp.config.json": json.dumps({"owner": "octo", "repo": "demo"})})
    git(tmp_path, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/gone")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)

    class Silent:  # no request may be sent before the trust read
        def __getattr__(self, name):
            pytest.fail(f"request attempted: {name}")

    assert cli.main(argv, gh=Silent()) == 2
    err = capsys.readouterr().err
    assert "target branch" in err and "terminal" not in err
