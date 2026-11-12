"""T28 tests: comment mode wired in (`approvals.require` behind every `--apply`, `ghp approvals check`, the pins, `ghp done`), plus the sync half
(`questions --sync`, `decisions --sync`, `ghp approvals sync <run>`) further down.

ado's comment-mode tests of `require` and `check` (tests/test_approvals.py: test_apply_*, test_stale_hash_refused, test_comment_older_than_request_refused,
test_agent_kit_and_strangers_never_approve, test_bad_newer_is_reported_and_refuses, test_newer_valid_reject_refuses, test_the_verbs_gates_are_one_target_for_this_plan,
test_review_*, test_artifacts_refuse_what_they_cannot_read, test_terminal_mode_noop_check, test_unknown_gate_refused_and_apply_gates_not_checked_here) are
ported here with the tracker seam: a comment is a `comments.Comment` (numeric ids, Markdown body) and `comments.fetch` is replaced by the thread below.
Dropped (ADO only): the bypass bit, `authorized_as`, `on-behalf`, comment versions. Offline only; every id and text is fictional."""
import json
import os
import subprocess

import pytest

import ghp.__main__ as cli
from ghp import approvals, comments, dod, plan as write_gate, questions
from ghp.comments import Comment, Comments, Note
from ghp.config import Config, Trust
from ghp.issues import Actor
from ghp.refs import Ref

TOKEN, HUMAN, OTHER, KIT, CI = 900001, 900003, 900005, 900007, 900009
N = 7
REF = Ref("octo", "demo", N)
COMMENT = Trust([HUMAN], "comment", {"assume_after_hours": None, "approval_requests": False})
TERMINAL = Trust([HUMAN], "terminal", {"assume_after_hours": None, "approval_requests": False})


def C(body, cid, author=HUMAN, typ="User", minute=None, **kw):
    kw.setdefault("last_edited_at", None)
    kw.setdefault("edit_count", 0)
    kw.setdefault("minimized", False)
    kw.setdefault("app", None)
    kw.setdefault("viewer_did_author", author == TOKEN)
    m = cid % 60 if minute is None else minute
    return Comment(id=cid, node_id="IC_%d" % cid, author=Actor(author, "login%d" % author, typ), body=body,
                   created_at="2026-10-01T10:%02d:%02dZ" % (m, cid % 60), **kw)


class Thread:
    """What `comments.fetch` returns for the issue; `notes` makes it a list that dropped comments."""

    def __init__(self, *items, notes=()):
        self.items, self.notes, self.reads = list(items), list(notes), 0

    def fetch(self, gh, ref):
        self.reads += 1
        return Comments(list(self.items), list(self.notes))


class NoWriteGh:
    """A Gh that may be read (viewer) and armed, but records whether it was."""

    def __init__(self):
        self.armed, self.calls = False, []

    def arm(self):
        self.armed = True

    def viewer(self):
        from ghp.gh import Viewer
        return Viewer(TOKEN, "the-agent", ["repo"])


@pytest.fixture
def thread(monkeypatch):
    t = Thread()
    monkeypatch.setattr(comments, "fetch", t.fetch)
    return t


def trust_is(monkeypatch, trust):
    seen = []
    fake = lambda repo=".", base=None: seen.append(base) or trust  # noqa: E731
    fake.keep = True  # test_prs.no_subprocess keeps it
    monkeypatch.setattr(approvals, "target_trust", fake)
    return seen


def h(gate, data, n=N):
    return approvals.gate_hash(gate, n, data)


def require(gh, data=b"plan", verb="push", trust=COMMENT, repo="."):
    return approvals.require(gh, trust, REF, verb, data, repo=repo)


# ---- AC3 at the function: `require` in comment mode ------------------------------------------------------------------------------------------------

def test_require_passes_with_any_gate_of_the_verb_set(thread, tmp_path):
    for verb, gates in sorted(approvals.VERB_GATES.items()):
        if verb == "done":
            continue  # needs a pin: below
        for g in sorted(gates):
            thread.items = [C("/approve %s %s" % (g, h(g, b"plan")), 11)]
            d = require(NoWriteGh(), verb=verb, repo=tmp_path)
            assert d["verdict"] == "approve" and d["best"]["id"] == 11


def test_apply_refuses_without_comment_in_comment_mode(thread, tmp_path):
    for verb in sorted(approvals.VERB_GATES):
        with pytest.raises(approvals.ApprovalMissing):
            require(NoWriteGh(), verb=verb, repo=tmp_path)


def test_gate_outside_verb_set_and_stale_hash_refused(thread, tmp_path):
    thread.items = [C("/approve approve-pr %s" % h("approve-pr", b"plan"), 11)]
    with pytest.raises(approvals.ApprovalMissing):
        require(NoWriteGh(), verb="push", repo=tmp_path)
    thread.items = [C("/approve approve-push %s" % h("approve-push", b"old plan"), 11)]
    with pytest.raises(approvals.ApprovalMissing) as e:
        require(NoWriteGh(), verb="push", repo=tmp_path)
    assert h("approve-push", b"plan") in str(e.value) and "/approve <gate> <hash>" in str(e.value)  # the hash to approve is shown, nothing else


def test_hash_binds_the_issue_number_and_every_byte(thread, tmp_path):
    thread.items = [C("/approve approve-push %s" % h("approve-push", b"plan", n=8), 11)]
    with pytest.raises(approvals.ApprovalMissing):
        require(NoWriteGh(), repo=tmp_path)
    thread.items = [C("/approve approve-push %s" % h("approve-push", b"plan\n"), 11)]
    with pytest.raises(approvals.ApprovalMissing):
        require(NoWriteGh(), repo=tmp_path)


def test_comment_older_than_request_refused(thread, tmp_path):
    g, x = "approve-push", h("approve-push", b"plan")
    thread.items = [C("/approve %s %s" % (g, x), 11), C("[spec-kit-ghp:approval-request] %s %s" % (g, x), 12, author=TOKEN)]
    with pytest.raises(approvals.ApprovalMissing):
        require(NoWriteGh(), repo=tmp_path)
    thread.items.append(C("/approve %s %s" % (g, x), 13))
    assert require(NoWriteGh(), repo=tmp_path)["best"]["id"] == 13


def test_newer_valid_reject_refuses_and_a_later_approve_wins_again(thread, tmp_path):
    g, x = "approve-push", h("approve-push", b"plan")
    thread.items = [C("/approve %s %s" % (g, x), 11), C("/reject %s %s: not yet" % (g, x), 12)]
    with pytest.raises(approvals.ApprovalMissing):
        require(NoWriteGh(), repo=tmp_path)
    thread.items.append(C("/approve %s %s" % (g, x), 13))
    assert require(NoWriteGh(), repo=tmp_path)["verdict"] == "approve"


def test_the_verbs_gates_are_one_target_for_this_plan(thread, tmp_path):
    xp, xs = h("approve-push", b"plan"), h("approve-sync", b"plan")
    thread.items = [C("/approve approve-push %s" % xp, 11), C("/reject approve-sync %s" % xs, 12)]
    with pytest.raises(approvals.ApprovalMissing):  # the newest command over the set is a reject
        require(NoWriteGh(), repo=tmp_path)
    thread.items = [C("/reject approve-push %s" % xp, 11), C("/approve approve-sync %s" % xs, 12)]
    assert require(NoWriteGh(), repo=tmp_path)["best"]["target"] == "approve-sync"


def test_bad_newer_is_reported_and_refuses(thread, tmp_path):
    g, x = "approve-push", h("approve-push", b"plan")
    thread.items = [C("/approve %s %s" % (g, x), 11), C("/reject %s" % g, 12)]  # a reject without the hash: malformed, about this gate
    with pytest.raises(approvals.ApprovalMissing) as e:
        require(NoWriteGh(), repo=tmp_path)
    assert "1 newer malformed command line(s)" in str(e.value)


def test_refused_without_approvers_dropped_comments_or_unknown_identity(thread, tmp_path):
    g, x = "approve-push", h("approve-push", b"plan")
    thread.items = [C("/approve %s %s" % (g, x), 11)]
    with pytest.raises(approvals.ApprovalMissing, match="no approvers"):
        require(NoWriteGh(), trust=Trust([], "comment", {}), repo=tmp_path)
    thread.notes = [Note(comments.JOIN, 12, "only in REST")]
    with pytest.raises(approvals.ApprovalMissing) as e:
        require(NoWriteGh(), repo=tmp_path)
    assert str(e.value) == approvals.E_UNREADABLE
    thread.notes = []

    class Blind(NoWriteGh):
        def viewer(self):
            from ghp.gh import GhError
            raise GhError("E_GH_HTTP", 401, "SEKRET token text")
    with pytest.raises(approvals.ApprovalMissing) as e:
        require(Blind(), repo=tmp_path)
    assert str(e.value) == approvals.E_IDENTITY and "SEKRET" not in str(e.value)


def test_terminal_mode_noop_reads_nothing(thread, tmp_path):
    for verb in sorted(approvals.VERB_GATES):
        assert require(None, verb=verb, trust=TERMINAL, repo=tmp_path) is None
    assert thread.reads == 0
    for bad in ("auto", "", None, "Comment"):
        with pytest.raises(approvals.ApprovalRefused, match="E_APPROVAL_MODE"):
            require(None, trust=Trust([HUMAN], bad, {}), repo=tmp_path)


# ---- AC2: an agent, bot or kit-authored /approve never counts ------------------------------------------------------------------------------------

def test_agent_authored_approve_is_refused(thread, tmp_path):
    g, x = "approve-push", h("approve-push", b"plan")
    line = "/approve %s %s" % (g, x)
    everyone = Trust([HUMAN, TOKEN, KIT, CI, OTHER], "comment", {})
    for item in (C(line, 11, author=TOKEN),  # the agent that runs ghp is the token identity, even when someone put it into approvers
                 C(line, 11, author=OTHER, viewer_did_author=True),  # GitHub says the token wrote it
                 C(line, 11, author=OTHER, typ="Bot"),
                 C(line, 11, author=OTHER, app={"slug": "an-app"}),
                 C(line, 11, author=OTHER, app=comments.APP_UNKNOWN)):
        thread.items = [item]
        with pytest.raises(approvals.ApprovalMissing):
            require(NoWriteGh(), trust=everyone, repo=tmp_path)
    thread.items = [C("[spec-kit-ghp:questions] abc\nQ1: x", 10, author=KIT), C(line, 11, author=KIT)]  # a kit posting identity
    with pytest.raises(approvals.ApprovalMissing):
        require(NoWriteGh(), trust=everyone, repo=tmp_path)
    thread.items = [C(line, 11, author=CI)]
    with pytest.raises(approvals.ApprovalMissing):
        approvals.require(NoWriteGh(), everyone, REF, "push", b"plan", ci_identity=CI, repo=tmp_path)
    thread.items = [C(line, 11, author=OTHER)]  # a stranger
    with pytest.raises(approvals.ApprovalMissing):
        require(NoWriteGh(), repo=tmp_path)
    thread.items = [C(line, 11, author=HUMAN)]
    assert require(NoWriteGh(), repo=tmp_path)["verdict"] == "approve"


def test_an_agent_approve_after_a_human_reject_does_not_cancel_it(thread, tmp_path):
    g, x = "approve-push", h("approve-push", b"plan")
    thread.items = [C("/reject %s %s" % (g, x), 11), C("/approve %s %s" % (g, x), 12, author=TOKEN)]
    with pytest.raises(approvals.ApprovalMissing):
        require(NoWriteGh(), trust=Trust([HUMAN, TOKEN], "comment", {}), repo=tmp_path)


def test_the_approvers_are_passed_as_a_set_of_ints(thread, tmp_path):
    g, x = "approve-push", h("approve-push", b"plan")
    thread.items = [C("/approve %s %s" % (g, x), 11)]
    d = require(NoWriteGh(), repo=tmp_path)
    approvals.record_pins(tmp_path, N, g, d, {HUMAN})
    assert require(NoWriteGh(), trust=Trust([HUMAN], "comment", {}), repo=tmp_path)["verdict"] == "approve"  # a list in Trust: works (set inside)


# ---- the pins (T27 must-list 1, 2) ----------------------------------------------------------------------------------------------------------------

def _pinned(thread, tmp_path, items, gate="approve-push", data=b"plan"):
    thread.items = list(items)
    d = require(NoWriteGh(), data=data, repo=tmp_path)
    return approvals.record_pins(tmp_path, N, gate, d, {HUMAN})


def test_every_approver_command_is_pinned_and_a_deleted_reject_refuses(thread, tmp_path):
    g, x = "approve-push", h("approve-push", b"plan")
    a, r, a2 = C("/approve %s %s" % (g, x), 11), C("/reject %s %s" % (g, x), 12), C("/approve %s %s" % (g, x), 13)
    other = C("/approve D1", 14)  # another target: not pinned
    entry = _pinned(thread, tmp_path, [a, r, a2, other, C("/approve %s %s" % (g, x), 15, author=OTHER)])
    assert [p["id"] for p in entry["seen"]] == [11, 12, 13] and entry["approval"]["id"] == 13
    thread.items = [a, a2, other]  # the /reject (12) was deleted: a2 still wins on its own, but the pin says something is gone
    with pytest.raises(approvals.ApprovalMissing) as e:
        require(NoWriteGh(), repo=tmp_path)
    assert str(e.value) == approvals.E_PIN_GONE
    thread.items = [a, a2, other]
    os.remove(approvals.pin_path(tmp_path, N))
    assert require(NoWriteGh(), repo=tmp_path)["verdict"] == "approve"  # without a pin only the live decision runs (and `done` refuses)


def test_a_pinned_later_reject_that_is_deleted_cannot_bring_the_approve_back(thread, tmp_path):
    g, x = "approve-push", h("approve-push", b"plan")
    a = C("/approve %s %s" % (g, x), 11)
    _pinned(thread, tmp_path, [a])
    thread.items = [a, C("/reject %s %s" % (g, x), 12)]
    with pytest.raises(approvals.ApprovalMissing):  # a newer valid reject: the live decision refuses
        require(NoWriteGh(), repo=tmp_path)


def test_an_edited_pinned_comment_refuses(thread, tmp_path):
    g, x = "approve-push", h("approve-push", b"plan")
    a, r, a2 = C("/approve %s %s" % (g, x), 11), C("/reject %s %s" % (g, x), 12), C("/approve %s %s" % (g, x), 13)
    _pinned(thread, tmp_path, [a, r, a2])
    import dataclasses
    thread.items = [a, dataclasses.replace(r, body="ok", last_edited_at="2026-10-02T00:00:00Z", edit_count=2), a2]
    with pytest.raises(approvals.ApprovalMissing, match="gone or changed"):
        require(NoWriteGh(), repo=tmp_path)


def test_the_pinned_approval_must_still_be_the_newest(thread, tmp_path, monkeypatch):
    g, x = "approve-push", h("approve-push", b"plan")
    a = C("/approve %s %s" % (g, x), 11)
    _pinned(thread, tmp_path, [a])
    thread.items = [a, C("/approve %s %s" % (g, x), 12)]  # a newer approval of the same plan: not the one the sync read
    with pytest.raises(approvals.ApprovalMissing) as e:
        require(NoWriteGh(), repo=tmp_path)
    assert str(e.value) == approvals.E_PIN_NEWER


def test_recheck_runs_on_the_same_fetch_as_the_decision(thread, tmp_path):
    g, x = "approve-push", h("approve-push", b"plan")
    _pinned(thread, tmp_path, [C("/approve %s %s" % (g, x), 11)])
    thread.reads = 0
    require(NoWriteGh(), repo=tmp_path)
    assert thread.reads == 1


def test_a_pin_of_another_plan_refuses_and_a_broken_pin_file_refuses(thread, tmp_path):
    """T28 review I1: a pin of a gate of the set whose hash is not this plan's is never ignored silently (editing the hash would neutralise it)."""
    g = "approve-push"
    _pinned(thread, tmp_path, [C("/approve %s %s" % (g, h(g, b"old")), 11)], data=b"old")
    thread.items = [C("/approve %s %s" % (g, h(g, b"new")), 12)]
    with pytest.raises(approvals.ApprovalMissing) as e:
        require(NoWriteGh(), data=b"new", repo=tmp_path)
    assert str(e.value) == approvals.E_PIN_OTHER
    path = approvals.pin_path(tmp_path, N)
    for bad in ("{", "[]", json.dumps({"version": 2, "issue": N, "gates": {}}), json.dumps({"version": 1, "issue": 8, "gates": {}}),
                json.dumps({"version": 1, "issue": True, "gates": {}}) if N == 1 else json.dumps({"version": True, "issue": N, "gates": {}}),
                json.dumps({"version": 1, "issue": float(N), "gates": {}}),
                json.dumps({"version": 1, "issue": N, "gates": {g: {"hash": "x", "approval": {}, "seen": []}}})):
        with open(path, "w") as f:
            f.write(bad)
        with pytest.raises(approvals.ApprovalMissing, match="pin file"):
            require(NoWriteGh(), data=b"new", repo=tmp_path)
    os.remove(path)
    os.symlink(os.devnull, path)
    with pytest.raises(approvals.ApprovalMissing, match="pin file"):
        require(NoWriteGh(), data=b"new", repo=tmp_path)


def test_done_needs_a_pinned_approval(thread, tmp_path):
    g, x = "approve-done", h("approve-done", b"plan")
    thread.items = [C("/approve %s %s" % (g, x), 11)]
    with pytest.raises(approvals.ApprovalMissing) as e:
        require(NoWriteGh(), verb="done", repo=tmp_path)
    assert "no approval of this plan was pinned" in str(e.value)
    d = approvals._decide(NoWriteGh(), COMMENT, REF, lambda: {g: x})
    approvals.record_pins(tmp_path, N, g, d, {HUMAN})
    assert require(NoWriteGh(), verb="done", repo=tmp_path)["verdict"] == "approve"
    with pytest.raises(approvals.ApprovalMissing):  # a changed plan is not the pinned one
        require(NoWriteGh(), data=b"plan 2", verb="done", repo=tmp_path)


# ---- AC3 and AC6 through the verbs: nothing written, Gh never armed ------------------------------------------------------------------------------

def _questions_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo"}))
    (tmp_path / "dod").mkdir()
    md = "# DoD\n%s\n- [ ] Q1: Are contractors in scope? → _(open)_ · owner: PO\n\n## 3. AC\n" % dod.SECTION2
    (tmp_path / "dod" / "DoD_7.md").write_text(md, encoding="utf-8")


class PostGh(NoWriteGh):
    def rest(self, method, path, body=None):
        assert self.armed, "a write before arming"
        self.calls.append((method, path, body))
        return {"id": 8000, "body": body["body"]}


def test_apply_refuses_without_a_matching_approval(tmp_path, monkeypatch, thread, capsys):
    _questions_repo(tmp_path, monkeypatch)
    trust_is(monkeypatch, COMMENT)
    gh = PostGh()
    assert cli.main(["questions", "7", "--post", "--apply"], gh=gh) == 2
    err = capsys.readouterr().err
    assert "no valid approval for this plan on issue #7" in err and not gh.armed and gh.calls == []
    # the approver approves exactly the plan that was shown: then it posts
    assert cli.main(["questions", "7", "--post"], gh=gh) == 0
    shown = capsys.readouterr().out
    text = shown[:-1]  # print adds the newline
    thread.items = [C("/approve approve-questions %s" % h("approve-questions", text.encode()), 11)]
    assert cli.main(["questions", "7", "--post", "--apply"], gh=gh) == 0
    assert gh.armed and len(gh.calls) == 1
    # in terminal mode nothing changed: no comment read for the approval, the gate is the terminal's
    trust_is(monkeypatch, TERMINAL)
    thread.items, thread.reads = [], 0
    gh2 = PostGh()
    assert cli.main(["questions", "7", "--post", "--apply"], gh=gh2) == 0 and gh2.armed


def test_a_changed_plan_is_not_approved(tmp_path, monkeypatch, thread, capsys):
    _questions_repo(tmp_path, monkeypatch)
    trust_is(monkeypatch, COMMENT)
    gh = PostGh()
    cli.main(["questions", "7", "--post"], gh=gh)
    text = capsys.readouterr().out[:-1]
    thread.items = [C("/approve approve-questions %s" % h("approve-questions", text.encode()), 11)]
    (tmp_path / "dod" / "DoD_7.md").write_text((tmp_path / "dod" / "DoD_7.md").read_text().replace("contractors", "vendors"), encoding="utf-8")
    assert cli.main(["questions", "7", "--post", "--apply"], gh=gh) == 2 and not gh.armed and gh.calls == []


def test_every_apply_verb_runs_the_comment_gate_before_arming(tmp_path, monkeypatch):
    """push, decisions --post, pr create and done hand `arm_or_refuse` the comment-mode gate of their own verb, over the plan they render."""
    seen = []
    real = write_gate.arm_or_refuse

    def spy(args, gh, rendered, approve=None):
        seen.append(approve)
        return real(args, gh, rendered, approve)
    monkeypatch.setattr(write_gate, "arm_or_refuse", spy)
    calls = []
    monkeypatch.setattr(approvals, "require", lambda gh, trust, ref, verb, data, ci_identity=None, repo=".": calls.append((verb, ref.number, data))
                        or (_ for _ in ()).throw(approvals.ApprovalMissing("refused")))
    trust_is(monkeypatch, COMMENT)
    import test_push
    test_push.cli_repo(tmp_path, monkeypatch, test_push.TASKS)
    monkeypatch.setattr(test_push.push, "WRITE_GAP", 0)
    hub = test_push.Hub()
    assert cli.main(["push", "1", "--from", "tasks.md", "--apply"], gh=hub) == 2
    assert not hub.armed and hub.writes == [] and calls[-1][0] == "push" and calls[-1][1] == 1 and b"| create | T001 |" in calls[-1][2]
    for verb, mod in (("pr-create", "prs"), ("done", "done"), ("decisions-post", "decisions"), ("questions-post", "questions")):
        src = open(os.path.join(os.path.dirname(cli.__file__), mod + ".py")).read()
        assert 'approvals.approve_hook(gh, ' in src and '"%s"' % verb in src, verb


def test_pr_create_refuses_unarmed_in_comment_mode(tmp_path, monkeypatch, thread, capsys):
    trust_is(monkeypatch, COMMENT)
    import test_prs
    test_prs.env(tmp_path, monkeypatch)
    gh = test_prs.world()
    test_prs.no_subprocess(monkeypatch)
    monkeypatch.setattr(questions, "_me", lambda gh: TOKEN)
    assert cli.main(test_prs.ARGS + ["--apply"], gh=gh) == 2
    assert not gh.armed and test_prs.posts(gh) == [] and "no valid approval" in capsys.readouterr().err


def test_done_refuses_unarmed_in_comment_mode(tmp_path, monkeypatch, thread, capsys):
    trust_is(monkeypatch, COMMENT)
    import test_done
    monkeypatch.setattr(test_done.issues, "_nap", lambda _s: None)
    test_done.repo(tmp_path, monkeypatch)
    hub = test_done.DoneHub()
    monkeypatch.setattr(questions, "_me", lambda gh: TOKEN)
    code, out, err = test_done.run(hub, capsys, "--plan-out", "plan.md")
    assert code == 0
    text = (tmp_path / "plan.md").read_text()
    thread.items = [C("/approve approve-done %s" % h("approve-done", text.encode(), n=test_done.N), 11)]
    code, out, err = test_done.apply(hub, capsys)
    assert code == 2 and "no approval of this plan was pinned" in err and not hub.armed and hub.writes == []


def test_refusals_never_arm_gh(tmp_path, monkeypatch, thread, capsys):
    """Every refusal of the comment gate (no approval, a reject, a dropped comment, no approvers, an unknown identity, a broken pin file, a missing pin,
    a wrong mode) comes out of `arm_or_refuse` with `gh` unarmed."""
    g, x = "approve-push", h("approve-push", b"plan")

    class Args:
        apply, expect_plan, plan_out = True, None, None

    cases = [(COMMENT, [], ()), (COMMENT, [C("/reject %s %s" % (g, x), 11)], ()), (COMMENT, [C("/approve %s %s" % (g, x), 11)], [Note(comments.JOIN, 3, "x")]),
             (Trust([], "comment", {}), [C("/approve %s %s" % (g, x), 11)], ()), (Trust([HUMAN], "auto", {}), [], ())]
    for trust, items, notes in cases:
        thread.items, thread.notes = items, list(notes)
        gh = NoWriteGh()
        with pytest.raises(approvals.ApprovalRefused):
            write_gate.arm_or_refuse(Args, gh, "plan", lambda: approvals.require(gh, trust, REF, "push", b"plan", repo=tmp_path))
        assert not gh.armed
    thread.items, thread.notes = [C("/approve approve-done %s" % h("approve-done", b"plan"), 11)], []
    gh = NoWriteGh()
    with pytest.raises(approvals.ApprovalMissing):
        write_gate.arm_or_refuse(Args, gh, "plan", lambda: approvals.require(gh, COMMENT, REF, "done", b"plan", repo=tmp_path))
    assert not gh.armed
    (tmp_path / ".ghp" / "approvals").mkdir(parents=True)
    (tmp_path / ".ghp" / "approvals" / "7.json").write_text("not json")
    thread.items = [C("/approve %s %s" % (g, x), 11)]
    gh = NoWriteGh()
    with pytest.raises(approvals.ApprovalMissing):
        write_gate.arm_or_refuse(Args, gh, "plan", lambda: approvals.require(gh, COMMENT, REF, "push", b"plan", repo=tmp_path))
    assert not gh.armed


# ---- AC1: `ghp approvals check` ----------------------------------------------------------------------------------------------------------------

def _review_repo(tmp_path, monkeypatch, spec="# Spec\nthe spec\n"):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo"}))
    (tmp_path / "dod").mkdir()
    (tmp_path / "specs" / "001-x").mkdir(parents=True)
    (tmp_path / "specs" / "001-x" / "spec.md").write_text(spec, encoding="utf-8")
    (tmp_path / "specs" / "001-x" / "plan.md").write_text("# Plan\n", encoding="utf-8")
    md = "# DoD\n- **Spec dir:** specs/001-x\n%s\n- [x] Q1: x → y · owner: PO\n\n## 3. AC\n" % dod.SECTION2
    (tmp_path / "dod" / "DoD_7.md").write_text(md, encoding="utf-8")


def _check(capsys, gh, gate="review-spec"):
    code = cli.main(["approvals", "check", "7", "--gate", gate], gh=gh)
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_check_exits_2_until_a_valid_approve_exists(tmp_path, monkeypatch, thread, capsys):
    _review_repo(tmp_path, monkeypatch)
    trust_is(monkeypatch, COMMENT)
    gh = NoWriteGh()
    want = h("review-spec", approvals.artifacts("review-spec", N, Config.load(), "."))
    code, out, err = _check(capsys, gh)
    assert code == 2 and out == "" and "no valid approval for gate review-spec on issue #7" in err and want in err
    thread.items = [C("/approve review-spec %s" % ("0" * 64), 11), C("/approve review-spec %s" % want, 12, author=OTHER),
                    C("/approve review-spec %s" % want, 13, author=TOKEN)]
    assert _check(capsys, gh)[0] == 2  # another hash, a stranger, the agent
    thread.items.append(C("/approve review-spec %s" % want, 14))
    code, out, err = _check(capsys, gh)
    assert code == 0 and json.loads(out) == {"gate": "review-spec", "mode": "comment", "met": True, "pinned": 14} and not gh.armed
    assert approvals.read_pins(".", N)["review-spec"]["approval"]["id"] == 14
    (tmp_path / "specs" / "001-x" / "spec.md").write_text("# Spec\nthe spec, changed\n", encoding="utf-8")
    assert _check(capsys, gh)[0] == 2  # the CURRENT artifacts: the old approval is of other text


def test_check_is_noop_in_terminal_mode(tmp_path, monkeypatch, thread, capsys):
    _review_repo(tmp_path, monkeypatch)
    trust_is(monkeypatch, TERMINAL)

    class Untouchable:
        def __getattr__(self, name):
            raise AssertionError("terminal mode touched gh." + name)
    for gate in approvals.GATES:
        code, out, err = _check(capsys, Untouchable(), gate)
        assert code == 0 and json.loads(out)["mode"] == "terminal" and "terminal mode" in json.loads(out)["note"] and err == ""
    assert thread.reads == 0


def test_unknown_gate_refused_and_apply_gates_not_checked_here(tmp_path, monkeypatch, thread, capsys):
    _review_repo(tmp_path, monkeypatch)
    seen = trust_is(monkeypatch, COMMENT)
    code, _, err = _check(capsys, NoWriteGh(), "review-everything")
    assert code == 2 and approvals.E_GATE in err and seen == []  # refused before the trust is read
    for gate in ("approve-push", "approve-pr", "approve-done"):
        code, _, err = _check(capsys, NoWriteGh(), gate)
        assert code == 2 and "is not checked here" in err


def test_review_gate_hash_covers_listed_artifacts(tmp_path, monkeypatch):
    _review_repo(tmp_path, monkeypatch)
    cfg = Config.load()
    spec = approvals.artifacts("review-spec", N, cfg, ".")
    assert spec.startswith(b"spec.md 16\n# Spec\nthe spec\n\nledger-2 ") and b"Q1: x" in spec
    plan = approvals.artifacts("review-plan", N, cfg, ".")
    assert plan.startswith(b"plan.md 7\n# Plan\n\nspec.md 16\n")
    (tmp_path / "dod" / "DoD_7.md").write_text("# DoD\n- **Spec dir:** specs/001-x\n%s\n```\n" % dod.SECTION2, encoding="utf-8")
    with pytest.raises(ValueError, match="cannot be read"):
        approvals.artifacts("review-spec", N, cfg, ".")
    (tmp_path / "dod" / "DoD_7.md").unlink()
    os.symlink(os.devnull, tmp_path / "dod" / "DoD_7.md")
    with pytest.raises(ValueError, match="cannot be read"):
        approvals.artifacts("review-spec", N, cfg, ".")


def test_target_trust_is_read_from_the_target_branch_never_the_working_tree(tmp_path, monkeypatch):
    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True,
                       env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
                            "GIT_COMMITTER_EMAIL": "t@example.invalid"})
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    git("init", "-q", "-b", "main")
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", "approval": "comment", "approvers": [HUMAN]}))
    git("add", ".")
    git("commit", "-q", "-m", "x")
    with pytest.raises(Exception):  # no origin/HEAD and the working tree sets trust keys: nothing to check them against
        approvals.target_trust(str(tmp_path))
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", "approval": "terminal", "approvers": [OTHER]}))
    t = approvals.target_trust(str(tmp_path))
    assert t.approval == "comment" and t.approvers == [HUMAN]  # the working tree's edit is not believed
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo"}))
    git("commit", "-qam", "terminal")
    git("update-ref", "refs/heads/feature", "HEAD")
    assert approvals.target_trust(str(tmp_path)).approval == "comment"  # origin/HEAD still says comment
    assert approvals.target_trust(str(tmp_path), base="feature").approval == "comment"  # comment mode on either side wins


# =============================== AC4: `decisions --sync` (ported from ado tests/test_decisions.py, GitHub seam) ===============================
from ghp import decisions  # noqa: E402

S1 = "- [ ] D1: Use cursor paging → _(open)_ · kind: decision · why: the story names no page size"
S2 = "- [ ] D2: Cap at 50 rows → _(open)_ · kind: assumption · why: the story is silent"
S3 = "- [ ] D3: Skip audit log → _(open)_ · kind: decision · why: out of scope"
EXCL = {TOKEN, KIT}


def _dfile(body):
    return decisions.render_file(N) + body + "\n"


def _posted(*lines, cid=10):
    """(the decisions file with every line marked as posted in comment `cid`, the exact comment body the kit would have posted)."""
    md = _dfile("\n".join(lines))
    todo = decisions.unposted(decisions.parse_decisions(md))
    body = decisions.with_marker("\n".join([decisions._HEAD] + [decisions._comment_line(d)[0] for d in todo]))
    return decisions._marked(md, {d["id"]: decisions.marker_hash(d) for d in todo}, cid), body


def _kit(body, cid=10, minute=0, author=KIT, **kw):
    return C(body, cid, author=author, minute=minute, **kw)


def _vote(cid, text, minute=5, author=HUMAN, **kw):
    return C(text, cid, author=author, minute=minute, **kw)


def _dsync(md, rows, approvers=(HUMAN,), excluded=EXCL):
    return decisions.sync(md, Comments(rows), set(approvers), set(excluded))


def test_sync_validates_and_rejects_with_via():
    md, body = _posted(S1, S2, S3)
    new, res = _dsync(md, [_kit(body), _vote(11, "/approve D1"), _vote(12, "/reject D2: not now <!-- x -->", minute=6)])
    assert res["applied"] == ["D1"] and res["skipped"] == {"D2": "no-verdict", "D3": "no-verdict"}  # `<` is not plain ASCII: a malformed line
    new, res = _dsync(md, [_kit(body), _vote(11, "/approve D1"), _vote(12, "/reject D2: not now", minute=6)])
    assert res["applied"] == ["D1", "D2"] and res["skipped"] == {"D3": "no-verdict"} and res["bad_newer"]["D1"] == 0
    got = decisions.parse_decisions(new)
    d1, d2 = got["validated"][0], got["rejected"][0]
    assert (d1["id"], d1["by"], d1["via"]) == ("D1", "approver", "11") and d1["posted"]["comment_id"] == 10
    assert (d2["id"], d2["via"], d2["note"]) == ("D2", "12", "not now") and "via" not in got["open"][0]


def test_sync_skips_unposted_or_edited_line():
    old, body = _posted(S2.replace("D2", "D5").replace("Cap at 50 rows", "Retry twice"), S2, cid=10)
    d2_edited = [ln for ln in old.splitlines() if ln.startswith("- [ ] D2")][0].replace("Cap at 50 rows", "Cap at 5000 rows")
    h5 = decisions.line_hash("Retry thrice")
    lines = [S1, d2_edited, S3 + " · posted: 99:%s" % decisions.line_hash("Skip audit log"),
             "- [ ] D4: Old news → _(open)_ · kind: decision · why: w · posted: 20:%s" % decisions.line_hash("Old news"),
             "- [ ] D5: Retry thrice → _(open)_ · kind: assumption · why: the story is silent · posted: 10:%s" % h5]
    rows = [_kit(body), C("an old note", 20, minute=1)] + [_vote(30 + n, "/approve D%d" % n) for n in range(1, 6)]
    new, res = _dsync(_dfile("\n".join(lines)), rows)
    assert res["applied"] == [] and new == _dfile("\n".join(lines))
    assert res["skipped"] == {"D1": "not-posted", "D2": "edited", "D3": "posting-missing", "D4": "posting-mismatch", "D5": "posting-mismatch"}


def test_sync_posting_comment_must_be_the_kits_unedited_and_hold_the_line():
    md, body = _posted(S1)
    for rows, rule in (([_kit(body, author=HUMAN)], "posting-mismatch"),  # not by a kit identity
                       ([_kit(body.replace("Use cursor paging", "Use offset paging"))], "posting-markup"),  # the content hash no longer matches
                       ([_kit("looks fine")], "posting-mismatch"),
                       ([_kit(body, edit_count=2, last_edited_at="2026-10-01T10:01:00Z")], "posting-edited"),
                       ([_kit(body.replace("\n", "\n‮", 1))], "posting-markup")):  # a bidi control in a kit comment: fail closed
        new, res = _dsync(md, rows + [_vote(11, "/approve D1", minute=7)])
        assert res["applied"] == [] and new == md and res["skipped"] == {"D1": rule}, rule


def test_sync_skips_verdict_older_than_posting():
    md, body = _posted(S1, S2, S3)
    rows = [_kit(body, cid=10, minute=5), _vote(8, "/approve D1", minute=4), _vote(9, "/approve D2", minute=5), _vote(11, "/approve D3", minute=5)]
    new, res = _dsync(md, rows)
    assert res["applied"] == ["D3"] and res["skipped"] == {"D1": "older-than-posting", "D2": "older-than-posting"}  # same time: then the id


def test_sync_ignores_commands_that_do_not_count():
    md, body = _posted(S1, S2, S3)
    rows = [_kit(body), _vote(11, "/approve D1", author=TOKEN), _vote(12, "/approve D2", author=OTHER), _vote(13, "> /approve D3"),
            _vote(14, "thanks!\n/approve D3"), _vote(15, "/approve D3", typ="Bot"), _vote(16, "/approve D1", author=KIT)]
    new, res = _dsync(md, rows, approvers=(HUMAN, TOKEN, KIT))
    assert res["applied"] == [] and new == md and set(res["skipped"].values()) == {"no-verdict"}


def test_newer_reject_overrides_synced_approve():
    md, body = _posted(S1, S2)
    base = [_kit(body), _vote(11, "/approve D1"), _vote(12, "/approve D2")]
    md2, res = _dsync(md, base)
    assert res["applied"] == ["D1", "D2"]
    same, res = _dsync(md2, base)
    assert same == md2 and res["skipped"] == {"D1": "up-to-date", "D2": "up-to-date"}
    later = base + [_vote(13, "/reject D1: changed my mind", minute=9), _vote(14, "/reject D2", minute=9, author=TOKEN)]
    md3, res = _dsync(md2, later)
    assert res["applied"] == ["D1"] and res["skipped"] == {"D2": "up-to-date"}
    got = decisions.parse_decisions(md3)
    assert [(d["id"], d["via"], d["note"]) for d in got["rejected"]] == [("D1", "13", "changed my mind")]
    md4, res = _dsync(md3, later + [_vote(15, "/approve D1", minute=10)])
    assert {d["id"]: d["via"] for d in decisions.parse_decisions(md4)["validated"]} == {"D1": "15", "D2": "12"}


def test_an_edited_later_reject_does_not_override_a_synced_approve():
    md, body = _posted(S1)
    synced = _dsync(md, [_kit(body), _vote(11, "/approve D1")])[0]
    edited = [_kit(body), _vote(11, "/approve D1"), _vote(12, "/reject D1", minute=9, edit_count=2, last_edited_at="2026-10-01T10:10:00Z")]
    new, res = _dsync(synced, edited)
    # never rewritten; since T28d the edited newer comment is reported (I2: it could have been a retraction), as the gate and the cross-check do
    assert new == synced and res["skipped"] == {"D1": "bad-newer"} and res["no_longer_valid"] == ["D1"] and res["bad_newer"] == {"D1": 1}


def test_sync_never_overwrites_a_verdict_written_in_a_terminal():
    md, body = _posted(S1)
    md = decisions.resolve(md, "D1", "validated", "PO", "agreed", False)
    new, res = _dsync(md, [_kit(body), _vote(11, "/reject D1", minute=6)])
    assert new == md and res["skipped"] == {"D1": "resolved-locally"}


def test_sync_refuses_a_malformed_or_ambiguous_file():
    md, body = _posted(S1)
    for bad in (md + S1.replace("D1", "D1") + "\n", md + "- [ ] D9 broken\n", md + "```\n"):
        with pytest.raises(ValueError, match="malformed"):
            _dsync(bad, [_kit(body)])


def test_sync_reports_a_synced_verdict_no_longer_backed():
    md, body = _posted(S1)
    synced = _dsync(md, [_kit(body), _vote(11, "/approve D1")])[0]
    new, res = _dsync(synced, [_kit(body)])  # the approval comment is gone
    assert new == synced and res["no_longer_valid"] == ["D1"]


def test_sync_refuses_when_a_synced_verdict_is_contradicted_but_cannot_be_rewritten():
    md, body = _posted(S1)
    synced = _dsync(md, [_kit(body), _vote(11, "/approve D1")])[0]
    with pytest.raises(ValueError, match="cannot be written"):  # the posting is now edited: the newer reject cannot land, and must not be dropped
        _dsync(synced, [_kit(body, edit_count=2, last_edited_at="2026-10-01T10:30:00Z"), _vote(11, "/approve D1"), _vote(12, "/reject D1", minute=9)])


def _dec_repo(tmp_path, monkeypatch, md):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo"}))
    (tmp_path / "dod").mkdir()
    (tmp_path / "dod" / "Decisions_7.md").write_text(md, encoding="utf-8")


def test_sync_verb_writes_the_verdicts_locally_and_never_writes_to_github(tmp_path, monkeypatch, thread, capsys):
    md, body = _posted(S1, S2)
    _dec_repo(tmp_path, monkeypatch, md)
    trust_is(monkeypatch, COMMENT)
    thread.items = [_kit(body, author=TOKEN), _vote(11, "/approve D1"), _vote(12, "/reject D2")]
    gh = NoWriteGh()
    assert cli.main(["decisions", "7", "--sync"], gh=gh) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["applied"] == ["D1", "D2"] and not gh.armed
    got = decisions.parse_decisions((tmp_path / "dod" / "Decisions_7.md").read_text())
    assert [d["id"] for d in got["validated"]] == ["D1"] and [d["id"] for d in got["rejected"]] == ["D2"]


def test_sync_verb_refusals_exit_2_and_write_nothing(tmp_path, monkeypatch, thread, capsys):
    md, body = _posted(S1)
    _dec_repo(tmp_path, monkeypatch, md)
    thread.items = [_kit(body, author=TOKEN), _vote(11, "/approve D1")]
    for trust, notes, want in ((Trust([], "comment", {}), (), "needs approvers"), (COMMENT, [Note(comments.JOIN, 3, "x")], "dropped at the join")):
        trust_is(monkeypatch, trust)
        thread.notes = list(notes)
        assert cli.main(["decisions", "7", "--sync"], gh=NoWriteGh()) == 2
        assert want in capsys.readouterr().err and (tmp_path / "dod" / "Decisions_7.md").read_text() == md
    thread.notes = []
    trust_is(monkeypatch, TERMINAL)  # terminal mode on the target: a no-op that says so, nothing read
    thread.reads = 0
    assert cli.main(["decisions", "7", "--sync"], gh=NoWriteGh()) == 0
    assert json.loads(capsys.readouterr().out)["synced"] is False and thread.reads == 0


def test_resolve_refused_when_approvers_set(tmp_path, monkeypatch, capsys):
    md, _ = _posted(S1)
    _dec_repo(tmp_path, monkeypatch, md)
    trust_is(monkeypatch, COMMENT)
    monkeypatch.setattr("sys.stdin", type("T", (), {"isatty": lambda self: True})())
    assert cli.main(["decisions", "7", "--resolve", "D1", "--validated"], gh=NoWriteGh()) == 2
    assert "approvers are configured on the target branch" in capsys.readouterr().err
    assert (tmp_path / "dod" / "Decisions_7.md").read_text() == md


# =============================== AC4: `questions --sync` (ported from ado tests/test_questions.py, GitHub seam) ===============================

def _qledger(body):
    return "# DoD\n%s\n%s\n## 3. AC\n" % (dod.SECTION2, body)


Q1 = "- [ ] Q1: Are contractors in scope? → _(open)_ · owner: PO\n"
Q2 = "- [ ] Q2: Retention? → _(open)_ · owner: PO\n"


def _qkit(parsed_open, cid=10, minute=0, author=TOKEN, **kw):
    return C(questions.with_marker(questions.comment_text(parsed_open)), cid, author=author, minute=minute, **kw)


def _qsync(md, rows, now=None, hours=None):
    found = Comments(rows)
    asked = questions.ages(questions.check_ledger(md)["open"], found, TOKEN)
    return questions.sync(md, found, {HUMAN}, {TOKEN}, now, hours, asked)


def test_answer_synced_with_comment_id():
    md = _qledger(Q1 + Q2)
    shown = questions.parse_questions(md)["open"]
    new, res = _qsync(md, [_qkit(shown), _vote(11, "/answer Q1: yes, all of them")])
    assert res["answered"] == ["Q1"] and res["open"] == ["Q2"] and res["skipped"] == {"Q2": "no-answer"}
    a = questions.parse_questions(new)["answered"][0]
    assert a["id"] == "Q1" and a["answer"] == "yes, all of them (PO comment 11)" and a["owner"] == "PO"
    assert new.count("\n") == md.count("\n")


def test_an_answer_older_than_the_posting_or_not_shown_is_skipped():
    md = _qledger(Q1 + Q2)
    shown = questions.parse_questions(md)["open"]
    new, res = _qsync(md, [_vote(9, "/answer Q1: early", minute=0), _qkit(shown[:1], cid=10, minute=1)])
    assert res["skipped"] == {"Q1": "older-than-posting", "Q2": "not-posted"} and new == md


def test_a_changed_question_restarts_its_age():
    md = _qledger(Q1)
    old = questions.parse_questions(_qledger(Q1.replace("contractors", "vendors")))["open"]
    now = questions.parse_questions(md)["open"]
    rows = [_qkit(old, cid=10, minute=0), _vote(11, "/answer Q1: yes", minute=1), _qkit(now, cid=12, minute=2)]
    new, res = _qsync(md, rows)
    assert res["skipped"] == {"Q1": "older-than-posting"}  # the answer is about the old words
    new, res = _qsync(md, rows + [_vote(13, "/answer Q1: still yes", minute=3)])
    assert res["answered"] == ["Q1"]


def test_agent_and_strangers_cannot_answer():
    md = _qledger(Q1)
    shown = questions.parse_questions(md)["open"]
    for v in (_vote(11, "/answer Q1: yes", author=TOKEN), _vote(11, "/answer Q1: yes", author=OTHER), _vote(11, "/answer Q1: yes", typ="Bot")):
        new, res = _qsync(md, [_qkit(shown), v])
        assert res["skipped"] == {"Q1": "no-answer"} and new == md


def test_an_edited_kit_questions_comment_refuses_the_age():
    md = _qledger(Q1)
    shown = questions.parse_questions(md)["open"]
    with pytest.raises(ValueError, match="edited"):
        _qsync(md, [_qkit(shown, edit_count=2, last_edited_at="2026-10-01T10:00:30Z"), _vote(11, "/answer Q1: yes")])


def test_answered_question_is_never_replaced_but_a_newer_answer_is_reported():
    md = _qledger(Q1)
    shown = questions.parse_questions(md)["open"]
    md2, _ = _qsync(md, [_qkit(shown), _vote(11, "/answer Q1: yes")])
    new, res = _qsync(md2, [_qkit(shown), _vote(11, "/answer Q1: yes"), _vote(12, "/answer Q1: no", minute=9)])
    assert new == md2 and res["ignored"] == ["Q1"]


def test_due_questions_need_the_server_clock():
    import datetime as dt
    md = _qledger(Q1)
    shown = questions.parse_questions(md)["open"]
    with pytest.raises(ValueError, match="server time"):
        _qsync(md, [_qkit(shown)], now=None, hours=24)
    _, res = _qsync(md, [_qkit(shown)], now=dt.datetime(2026, 10, 3, tzinfo=dt.timezone.utc), hours=24)
    assert res["due"] == ["Q1"]


def test_questions_sync_verb(tmp_path, monkeypatch, thread, capsys):
    _questions_repo(tmp_path, monkeypatch)
    path = tmp_path / "dod" / "DoD_7.md"
    md = path.read_text()
    shown = questions.parse_questions(md)["open"]
    trust_is(monkeypatch, COMMENT)
    thread.items = [_qkit(shown), _vote(11, "/answer Q1: yes")]
    gh = NoWriteGh()
    assert cli.main(["questions", "7", "--sync"], gh=gh) == 0 and not gh.armed
    assert json.loads(capsys.readouterr().out)["answered"] == ["Q1"] and "(PO comment 11)" in path.read_text()
    trust_is(monkeypatch, Trust([HUMAN], "comment", {"assume_after_hours": 24}))  # no server clock on Gh: refused, nothing written
    path.write_text(md)
    assert cli.main(["questions", "7", "--sync"], gh=NoWriteGh()) == 2 and path.read_text() == md and "server time" in capsys.readouterr().err
    trust_is(monkeypatch, TERMINAL)
    assert cli.main(["questions", "7", "--sync"], gh=NoWriteGh()) == 0 and json.loads(capsys.readouterr().out)["synced"] is False
    assert cli.main(["questions", "7", "--sync", "--post"], gh=NoWriteGh()) == 2


# =============================== `approvals check` on the wait gates (ported from ado, GitHub seam) ===============================

def test_wait_answers_from_comments_not_ledger(tmp_path, monkeypatch, thread, capsys):
    _review_repo(tmp_path, monkeypatch)
    trust_is(monkeypatch, COMMENT)
    q = [{"id": "Q1", "question": "Are contractors in scope?"}]
    assert _check(capsys, NoWriteGh(), "wait-po-answers")[0] == 0  # no questions comment: nothing to wait for
    thread.items = [_qkit(q)]
    code, _, err = _check(capsys, NoWriteGh(), "wait-po-answers")
    assert code == 2 and "1 open (Q1)" in err
    thread.items.append(_vote(11, "/answer Q1: yes", author=TOKEN))  # the agent cannot meet it
    assert _check(capsys, NoWriteGh(), "wait-po-answers")[0] == 2
    thread.items.append(_vote(12, "/answer Q1: yes"))
    assert _check(capsys, NoWriteGh(), "wait-po-answers")[0] == 0
    thread.items.append(_qkit(q, cid=13, author=OTHER))  # a questions comment that is not the token's own
    code, _, err = _check(capsys, NoWriteGh(), "wait-po-answers")
    assert code == 2 and "cannot be trusted" in err


def test_wait_validation_from_comments_not_decisions_file(tmp_path, monkeypatch, thread, capsys):
    _review_repo(tmp_path, monkeypatch)
    trust_is(monkeypatch, COMMENT)
    md, body = _posted(S1, S2)
    thread.items = [_kit(body, author=TOKEN), _vote(11, "/approve D1")]
    code, _, err = _check(capsys, NoWriteGh(), "wait-po-validation")
    assert code == 2 and "1 open (D2)" in err
    thread.items.append(_vote(12, "/reject D2"))
    assert _check(capsys, NoWriteGh(), "wait-po-validation")[0] == 0
    thread.items[0] = _kit(body, author=TOKEN, edit_count=2, last_edited_at="2026-10-01T10:00:30Z")
    code, _, err = _check(capsys, NoWriteGh(), "wait-po-validation")
    assert code == 2 and "cannot be trusted" in err


# =============================== AC5: `approvals sync <run> [--resume]` (ported from ado, GitHub seam) ===============================

def _run(tmp_path, step="review-spec", status="paused", n=N, run_id="run13", **over):
    d = tmp_path / ".specify" / "workflows" / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    state = {"workflow_id": "ghp-lifecycle", "run_id": run_id, "status": status, "current_step_id": step, "inputs": {"issue_number": n},
             "updated_at": "2026-10-01T10:00:00+00:00", **over}
    (d / "state.json").write_text(json.dumps(state))
    return run_id


def _cfg():
    return Config.load()


def test_sync_never_counts_itself_as_an_approver(tmp_path, monkeypatch, thread):
    """The poller's own token identity is excluded even when it is in `approvers` (ado: the poller identity is the CI identity)."""
    _review_repo(tmp_path, monkeypatch)
    _run(tmp_path)
    want = h("review-spec", approvals.artifacts("review-spec", N, _cfg(), "."))
    thread.items = [C("/approve review-spec %s" % want, 11, author=TOKEN)]
    out = approvals.sync(NoWriteGh(), Trust([TOKEN, HUMAN], "comment", {}), "run13", _cfg(), ".")
    assert out["verdict"] == "waiting" and "pinned" not in out
    thread.items.append(C("/approve review-spec %s" % want, 12))
    out = approvals.sync(NoWriteGh(), Trust([TOKEN, HUMAN], "comment", {}), "run13", _cfg(), ".")
    assert out == {"gate": "review-spec", "verdict": "approve", "pinned": 12}


def test_poller_hashes_what_check_hashes_and_apply_gates_hash_the_plan_text(tmp_path, monkeypatch, thread):
    _review_repo(tmp_path, monkeypatch)
    _run(tmp_path, step="push-loop:approve-push:2")
    (tmp_path / ".ghp").mkdir()
    long = "\n".join("line %d" % i for i in range(300))
    (tmp_path / ".ghp" / "plan-push.md").write_text(write_gate.gate_preview(long, ".ghp/plan-push.md"))
    thread.items = [C("/approve approve-push %s" % h("approve-push", long.encode()), 11)]  # the rendered text, without the warning line
    out = approvals.sync(NoWriteGh(), COMMENT, "run13", _cfg(), ".")
    assert out["verdict"] == "approve" and out["pinned"] == 11
    # and the apply then accepts exactly that text, with its pin
    assert approvals.require(NoWriteGh(), COMMENT, REF, "push", long.encode(), repo=".")["verdict"] == "approve"
    (tmp_path / ".ghp" / "plan-push.md").unlink()
    with pytest.raises(ValueError, match="cannot be read"):
        approvals.sync(NoWriteGh(), COMMENT, "run13", _cfg(), ".")


def test_wait_gate_resumes_on_state(tmp_path, monkeypatch, thread):
    _review_repo(tmp_path, monkeypatch)
    _run(tmp_path, step="wait-po-answers")
    out = approvals.sync(NoWriteGh(), COMMENT, "run13", _cfg(), ".")
    assert out == {"gate": "wait-po-answers", "verdict": "approve"}  # nothing to wait for


def test_not_paused_does_nothing_and_unknown_gate_is_refused(tmp_path, monkeypatch, thread):
    _review_repo(tmp_path, monkeypatch)
    _run(tmp_path, status="running", step="implement")
    assert approvals.sync(None, COMMENT, "run13", _cfg(), ".") == {"gate": None, "verdict": "waiting", "paused": False}
    _run(tmp_path, step="implement")
    with pytest.raises(ValueError, match="not one of the gates"):
        approvals.sync(None, COMMENT, "run13", _cfg(), ".")
    assert thread.reads == 0


def test_run_id_validated_before_any_path(tmp_path, monkeypatch, capsys):
    _review_repo(tmp_path, monkeypatch)
    seen = trust_is(monkeypatch, COMMENT)
    for bad in ("../x", "a/b", "x" * 129, "", ".hidden", "r\u00e9"):
        assert cli.main(["approvals", "sync", bad], gh=NoWriteGh()) == 2
        assert "not a Spec Kit run id" in capsys.readouterr().err
    assert seen == []  # refused before the trust is read


def test_state_json_is_untrusted(tmp_path, monkeypatch, thread):
    _review_repo(tmp_path, monkeypatch)
    for over in ({"workflow_id": "ado-lifecycle"}, {"run_id": "other"}, {"status": "weird"}, {"inputs": {"issue_number": "7"}}, {"inputs": {"issue_number": True}},
                 {"inputs": {"issue_number": 0}}, {"inputs": {}}, {"inputs": {"issue": N}}, {"updated_at": "yesterday"}, {"current_step_id": "Bad Step"}, {"current_step_id": "a:b"}):
        _run(tmp_path, **over)
        with pytest.raises(ValueError, match="state.json cannot be read"):
            approvals.read_run(".", "run13")
    d = tmp_path / ".specify" / "workflows" / "runs" / "run13"
    (d / "state.json").write_text("{" * 100000)
    with pytest.raises(ValueError, match="state.json"):
        approvals.read_run(".", "run13")
    (d / "state.json").unlink()
    os.symlink(tmp_path / "ghp.config.json", d / "state.json")
    with pytest.raises(ValueError, match="state.json"):
        approvals.read_run(".", "run13")
    (d / "state.json").unlink()
    (d / "state.json").write_text("x" * (2 << 20))
    with pytest.raises(ValueError, match="state.json"):
        approvals.read_run(".", "run13")
    assert thread.reads == 0


def test_step_ids_are_the_shipped_ones(tmp_path, monkeypatch):
    assert approvals._step_ok("review-spec") and approvals._step_ok("push-loop:approve-push:2") and not approvals._step_ok("push-loop:approve-push:x")
    assert not approvals._step_ok("unknown-step") and not approvals._step_ok("push-loop:unknown:1") and approvals.RUN_INPUT == "issue_number"
    assert not approvals._step_ok("pr-loop:approve-push:1") and not approvals._step_ok("review-spec:approve-push:1")  # the step must be that loop's
    assert not approvals._step_ok("push-loop:push-loop:1") and approvals._step_ok("pr-loop:pr-apply:0")
    monkeypatch.setattr(approvals, "STEP_IDS", ("review-spec",))
    assert approvals._step_ok("review-spec") and not approvals._step_ok("implement")


def test_resume_args_reset_other_verdicts_and_need_the_same_step(tmp_path, monkeypatch, thread):
    argv = approvals.resume_argv("run13", "review-spec", "approve")
    assert argv[:4] == ["specify", "workflow", "resume", "run13"] and argv[4:6] == ["--input", "verdict_review_spec=approve"]
    assert all(argv[i] == "--input" for i in range(4, len(argv), 2)) and len(argv) == 4 + 2 * len(approvals.GATES)
    assert "verdict_approve_push=" in argv
    for bad in (("../x", "review-spec", "approve"), ("run13", "nope", "approve"), ("run13", "review-spec", "maybe")):
        with pytest.raises(ValueError):
            approvals.resume_argv(*bad)
    _review_repo(tmp_path, monkeypatch)
    _run(tmp_path)
    want = h("review-spec", approvals.artifacts("review-spec", N, _cfg(), "."))
    thread.items = [C("/approve review-spec %s" % want, 11)]
    calls = []
    monkeypatch.setattr(approvals, "_resume", lambda repo, run_id, gate_id, verdict: calls.append((run_id, gate_id, verdict)))
    assert approvals.sync(NoWriteGh(), COMMENT, "run13", _cfg(), ".", resume=True)["resumed"] is True and calls == [("run13", "review-spec", "approve")]
    assert approvals.sync(NoWriteGh(), COMMENT, "run13", _cfg(), ".", resume=True, dry_run=True)["resumed"] is False and len(calls) == 1


def test_resume_runs_specify_on_a_safe_path_only(tmp_path, monkeypatch):
    bin_in = tmp_path / "bin"
    bin_in.mkdir()
    for tool in ("specify", "ghp", "git"):
        (bin_in / tool).write_text("#!/bin/sh\n")
        (bin_in / tool).chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(bin_in))  # inside the project: ignored
    with pytest.raises(approvals.ResumeFailed, match="not found on a safe PATH"):
        approvals._resume(str(tmp_path), "run13", "review-spec", "approve")
    outside = tmp_path.parent / ("safe-bin-%d" % os.getpid())
    outside.mkdir(exist_ok=True)
    for tool in ("specify", "ghp", "git"):
        (outside / tool).write_text("#!/bin/sh\n")
        (outside / tool).chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_in), "", "rel", str(outside)]))
    seen = []
    monkeypatch.setattr(approvals, "_resume_run", lambda argv, **kw: seen.append((argv, kw)) or subprocess.CompletedProcess(argv, 0))
    approvals._resume(str(tmp_path), "run13", "review-spec", "approve")
    argv, kw = seen[0]
    assert argv[0] == str(outside / "specify") and kw["env"]["PATH"] == str(outside) and kw["shell"] is False and kw["stdin"] == subprocess.DEVNULL
    monkeypatch.setattr(approvals, "_resume_run", lambda argv, **kw: subprocess.CompletedProcess(argv, 3))
    with pytest.raises(approvals.ResumeFailed, match="exit status 3"):
        approvals._resume(str(tmp_path), "run13", "review-spec", "approve")


def test_cli_sync_terminal_mode_is_refused_and_prints_the_verdict(tmp_path, monkeypatch, thread, capsys):
    _review_repo(tmp_path, monkeypatch)
    _run(tmp_path)
    trust_is(monkeypatch, TERMINAL)
    assert cli.main(["approvals", "sync", "run13"], gh=NoWriteGh()) == 2 and "comment mode only" in capsys.readouterr().err and thread.reads == 0
    trust_is(monkeypatch, COMMENT)
    assert cli.main(["approvals", "sync", "run13"], gh=NoWriteGh()) == 0
    assert json.loads(capsys.readouterr().out) == {"gate": "review-spec", "verdict": "waiting"}


def test_hostile_bodies_are_linear_in_the_sync_readers():
    import time
    md, body = _posted(S1)
    for n in (30000, 300000):
        hostile = [C("[spec-kit-ghp:decisions] " + " " * n + "x", 50), C("/approve D1" + " " * n + ":", 51), C("​" * n, 52),
                   C(decisions.MARKER + " 0\n" + "D1 (decision): " * (n // 15), 53, author=KIT), C("> " * n, 54),
                   C(questions.MARKER + " " + "a" * 16 + "\n" + "Q1: x\n" * (n // 6), 55, author=TOKEN)]
        t = time.monotonic()
        rows = Comments([_kit(body)] + hostile + [_vote(11, "/approve D1", minute=59)])
        _dsync(md, rows)
        decisions.posting_index(rows)
        for c in hostile:
            approvals.marked(c, decisions.MARKER)
            approvals.kit_lines(c.body, decisions.MARKER)
            questions.posted_open(c.body)
        questions._answer_text("PO " * n + "comment")
        assert time.monotonic() - t < 2, n


def test_a_kit_comment_with_a_bidi_or_invisible_character_lists_nothing_even_with_a_matching_hash():
    for bad in ("‮", "​", "\x07", " "):
        body = decisions.with_marker(decisions._HEAD + "\nD1 (decision): Use cursor" + bad + " paging")
        assert approvals.kit_lines(body, decisions.MARKER) == []
    ok = decisions.with_marker(decisions._HEAD + "\nD1 (decision): Use cursor paging")
    assert approvals.kit_lines(ok, decisions.MARKER) == [decisions.MARKER, decisions._HEAD, "D1 (decision): Use cursor paging"]


def test_an_answer_at_the_same_second_as_the_posting_loses():
    md = _qledger(Q1)
    shown = questions.parse_questions(md)["open"]
    post = _qkit(shown, cid=10, minute=0)  # 10:00:10
    tie = C("/answer Q1: yes", 70, minute=0)  # 10:00:10 too, a higher id
    assert tie.created_at == post.created_at
    _, res = _qsync(md, [post, tie])
    assert res["skipped"] == {"Q1": "older-than-posting"}


def test_resume_needs_the_very_same_step_a_loop_iteration_included(tmp_path, monkeypatch, thread):
    _review_repo(tmp_path, monkeypatch)
    _run(tmp_path, step="push-loop:approve-push:2")
    (tmp_path / ".ghp").mkdir()
    (tmp_path / ".ghp" / "plan-push.md").write_text("plan")
    thread.items = [C("/approve approve-push %s" % h("approve-push", b"plan"), 11)]
    real, reads = approvals.read_run, []

    def moved(repo, run_id):
        reads.append(run_id)
        r = real(repo, run_id)
        return r if len(reads) == 1 else {**r, "step": "push-loop:approve-push:3"}
    monkeypatch.setattr(approvals, "read_run", moved)
    calls = []
    monkeypatch.setattr(approvals, "_resume", lambda *a: calls.append(a))
    out = approvals.sync(NoWriteGh(), COMMENT, "run13", _cfg(), ".", resume=True)
    assert out["verdict"] == "approve" and out["resumed"] is False and calls == [] and len(reads) == 2


def test_the_poller_refuses_terminal_mode_itself():
    with pytest.raises(approvals.ApprovalRefused, match="comment mode only"):
        approvals.sync(None, TERMINAL, "run13", None, ".")


# =============================== T28 review round 1 (regressions from the reviewer's probes P1-P6 and mutants R8, R9, R14, R27) ===============================

def test_activate_apply_needs_an_approval_in_comment_mode(tmp_path, monkeypatch, thread, capsys):
    """C1 (probe P1 inverted): `activate --apply` is a GitHub write like the others; terminal mode is unchanged."""
    import test_activate as ta
    ta.repo(tmp_path, monkeypatch)
    trust_is(monkeypatch, COMMENT)
    monkeypatch.setattr(questions, "_me", lambda gh: TOKEN)
    hub = ta.hub_with()
    assert ta.apply(hub) == 2
    assert not hub.armed and ta.status_writes(hub) == [] and "no valid approval for this plan on issue #11" in capsys.readouterr().err
    assert cli.main(["activate", "11", "--plan-out", "plan.md"], gh=hub) == 0
    text = (tmp_path / "plan.md").read_text()
    thread.items = [C("/approve approve-activate %s" % h("approve-activate", text.encode(), n=11), 31)]
    assert ta.apply(hub) == 0 and hub.armed and ta.status_writes(hub)
    trust_is(monkeypatch, TERMINAL)
    thread.items, thread.reads = [], 0
    hub2 = ta.hub_with()
    assert ta.apply(hub2) == 0 and hub2.armed and thread.reads == 0


def test_every_write_call_site_passes_the_comment_gate():
    """Every `arm_or_refuse` call that can arm for a write hands it the approval hook (the not-ready `done` path is a dry run only)."""
    import re as _re
    src_dir = os.path.dirname(cli.__file__)
    sites = []
    for name in sorted(os.listdir(src_dir)):
        if name.endswith(".py") and name != "plan.py":
            for line in open(os.path.join(src_dir, name)).read().splitlines():
                if "arm_or_refuse(" in line:
                    sites.append((name, "approve_hook" in line))
    assert sorted(n for n, hooked in sites if hooked) == ["activate.py", "decisions.py", "done.py", "prs.py", "push.py", "questions.py"]
    assert [n for n, hooked in sites if not hooked] == ["done.py"]  # `if not args.apply:` branch of a not-ready ledger
    assert not any(_re.search(r"\bgh\.arm\(\)", open(os.path.join(src_dir, n)).read()) for n in os.listdir(src_dir) if n.endswith(".py") and n != "plan.py")


def test_a_pin_whose_hash_was_changed_refuses(thread, tmp_path):
    """I1 (probe P4): changing a pin entry's hash no longer turns it into another plan's, ignored silently."""
    g, x = "approve-push", h("approve-push", b"plan")
    a, r, a2 = C("/approve %s %s" % (g, x), 11), C("/reject %s %s" % (g, x), 12), C("/approve %s %s" % (g, x), 13)
    _pinned(thread, tmp_path, [a, r, a2])
    thread.items = [a, a2]
    path = approvals.pin_path(tmp_path, N)
    data = json.load(open(path))
    data["gates"][g]["hash"] = "0" * 64
    open(path, "w").write(json.dumps(data))
    with pytest.raises(approvals.ApprovalMissing) as e:
        require(NoWriteGh(), repo=tmp_path)
    assert str(e.value) == approvals.E_PIN_OTHER


def test_a_sync_of_one_gate_replaces_the_pins_of_the_verbs_other_gates(thread, tmp_path):
    xp, xs = h("approve-push", b"plan"), h("approve-sync", b"plan 2")
    _pinned(thread, tmp_path, [C("/approve approve-push %s" % xp, 11)])
    thread.items = [C("/approve approve-sync %s" % xs, 12)]
    with pytest.raises(approvals.ApprovalMissing):  # the old approve-push pin is of another plan: refused until a sync replaces it
        require(NoWriteGh(), data=b"plan 2", repo=tmp_path)
    d = approvals._decide(NoWriteGh(), COMMENT, REF, lambda: {"approve-push": h("approve-push", b"plan 2"), "approve-sync": xs})  # what sync does
    approvals.record_pins(tmp_path, N, "approve-sync", d, {HUMAN})
    assert set(approvals.read_pins(tmp_path, N)) == {"approve-sync"}
    assert require(NoWriteGh(), data=b"plan 2", repo=tmp_path)["best"]["id"] == 12


def test_pins_are_advisory_a_forged_pin_is_documented_not_proof(thread, tmp_path):
    """I1 (probes P2, P3): whoever can write the working tree can drop a deleted /reject from `seen` or forge a pin of a live approval. That is the
    documented limit (pins are hardening, not proof; T28e), pinned here so the docs and the code say the same thing."""
    g, x = "approve-done", h("approve-done", b"plan")
    a = C("/approve %s %s" % (g, x), 11)
    thread.items = [a]
    os.makedirs(os.path.dirname(approvals.pin_path(tmp_path, N)))
    with open(approvals.pin_path(tmp_path, N), "w") as f:
        f.write(json.dumps({"version": 1, "issue": N, "gates": {g: {"hash": x, "approval": approvals.pin(a), "seen": []}}}))
    assert require(NoWriteGh(), verb="done", repo=tmp_path)["verdict"] == "approve"
    doc = open(os.path.join(os.path.dirname(cli.__file__), "..", "..", "docs", "trust-and-limits.md")).read()
    assert "not proof" in doc and "forging gives nothing" not in approvals.__doc__ and "not proof" in approvals.__doc__.lower()


def test_an_edited_or_hidden_later_reject_does_not_bring_the_approve_back(thread, tmp_path):
    """I2 (probe P5 inverted): an approver's comment newer than the winning approval that is edited or minimized refuses, whatever it says now."""
    g, x = "approve-push", h("approve-push", b"plan")
    a = C("/approve %s %s" % (g, x), 11)
    edited, hidden = r"1 newer approver comment\(s\) edited after posting", r"1 newer approver comment\(s\) hidden \(minimized\)"  # T31c wording
    for later, words in ((C("/reject %s %s" % (g, x), 12, last_edited_at="2026-10-02T00:00:00Z", edit_count=1), edited),
                         (C("ok", 12, last_edited_at="2026-10-02T00:00:00Z", edit_count=1), edited),
                         (C("/reject %s %s" % (g, x), 12, minimized=True), hidden),
                         (C("thanks", 12, minimized=None), hidden)):
        thread.items = [a, later]
        with pytest.raises(approvals.ApprovalMissing, match=words):
            require(NoWriteGh(), repo=tmp_path)
    thread.items = [C("old note", 10, edit_count=2, last_edited_at="2026-10-02T00:00:00Z"), a]  # an OLDER edited comment changes nothing
    assert require(NoWriteGh(), repo=tmp_path)["verdict"] == "approve"
    thread.items = [a, C("x", 12, author=OTHER, edit_count=2, last_edited_at="2026-10-02T00:00:00Z")]  # a non-approver's edit changes nothing
    assert require(NoWriteGh(), repo=tmp_path)["verdict"] == "approve"


def test_a_deleted_malformed_newer_line_is_detected_by_the_pins(thread, tmp_path):
    """I5 (mutant R14): a malformed approver line that could be about the gate is pinned too, so its deletion refuses."""
    g, x = "approve-push", h("approve-push", b"plan")
    a, bad, a2 = C("/approve %s %s" % (g, x), 11), C("/reject %s" % g, 12), C("/approve %s %s" % (g, x), 13)
    entry = _pinned(thread, tmp_path, [a, bad, a2])
    assert 12 in [p["id"] for p in entry["seen"]]
    thread.items = [a, a2]
    with pytest.raises(approvals.ApprovalMissing) as e:
        require(NoWriteGh(), repo=tmp_path)
    assert str(e.value) == approvals.E_PIN_GONE


def test_repost_then_revert_three_paths_agree(thread):
    """I5 (probe P6; ado test_newest_posting 'repost+revert'): the approver saw Y, the file is set back to X: `--sync`, the wait gate and the cross-check
    all refuse to take the verdict for X."""
    X, Y = S1, S1.replace("cursor paging", "offset paging")
    md_x, body_x = _posted(X, cid=10)
    _, body_y = _posted(Y, cid=20)
    rows = [_kit(body_x, 10, minute=0, author=TOKEN), _kit(body_y, 20, minute=1, author=TOKEN), _vote(30, "/approve D1", minute=5)]
    new, rep = _dsync(md_x, rows)
    thread.items = rows
    st = approvals.wait_state(NoWriteGh(), COMMENT, REF, "wait-po-validation", decisions_md=md_x)
    from ghp import strict
    xc = [f["rule"] for f in strict.xcheck(new, Comments(rows), False, {HUMAN}, EXCL, N)]
    assert (rep["skipped"]["D1"], st["met"], st["open"], xc) == ("posting-superseded", False, ["D1"], ["decisions-xcheck-dropped"])


def _git_repo(tmp_path):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}

    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True, env=env)

    def commit_config(**trust):
        (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", **trust}))
        git("add", ".")
        git("commit", "-q", "--allow-empty", "-m", "c")
    git("init", "-q", "-b", "main")
    return git, commit_config


def test_comment_mode_on_the_prs_target_wins_over_a_terminal_default_branch(tmp_path, monkeypatch):
    """I3 (mutant R8): origin/HEAD is terminal, the PR's --base is comment: comment wins."""
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    git, commit_config = _git_repo(tmp_path)
    commit_config()
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    git("checkout", "-q", "-b", "release")
    commit_config(approval="comment", approvers=[HUMAN])
    git("checkout", "-q", "main")
    assert approvals.target_trust(str(tmp_path)).approval == "terminal"
    t = approvals.target_trust(str(tmp_path), base="release")
    assert t.approval == "comment" and t.approvers == [HUMAN]


def test_two_comment_targets_with_different_approvers_refuse(tmp_path, monkeypatch):
    """I3 (mutant R9)."""
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    git, commit_config = _git_repo(tmp_path)
    commit_config(approval="comment", approvers=[HUMAN])
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    git("checkout", "-q", "-b", "release")
    commit_config(approval="comment", approvers=[OTHER])
    with pytest.raises(approvals.ApprovalRefused) as e:
        approvals.target_trust(str(tmp_path), base="release")
    assert str(e.value) == approvals.E_APPROVERS_DIFFER
    commit_config(approval="comment", approvers=[HUMAN])  # the same set: fine
    assert approvals.target_trust(str(tmp_path), base="release").approvers == [HUMAN]


def test_pr_create_reads_the_trust_of_its_own_base(tmp_path, monkeypatch, thread, capsys):
    """I3 (mutant R27): the PR's --base reaches `target_trust` (test_prs.no_subprocess keeps a stub marked `keep`)."""
    seen = trust_is(monkeypatch, COMMENT)
    import test_prs
    test_prs.env(tmp_path, monkeypatch)
    test_prs.no_subprocess(monkeypatch)
    monkeypatch.setattr(questions, "_me", lambda gh: TOKEN)
    assert cli.main(test_prs.ARGS + ["--apply"], gh=test_prs.world()) == 2
    assert seen == ["main"]


@pytest.mark.parametrize("var", ["GITHUB_ACTIONS", "CI"])
def test_in_ci_an_unresolvable_target_refuses_every_apply(tmp_path, monkeypatch, thread, capsys, var):
    """I4: no origin/HEAD and no --base in CI: the trust cannot be read, so an apply refuses (exit 2, Gh unarmed; the syncs and checks too, T28d); outside CI the ado behaviour."""
    _questions_repo(tmp_path, monkeypatch)
    monkeypatch.setenv(var, "true")
    with pytest.raises(approvals.ApprovalRefused) as e:
        approvals.target_trust(str(tmp_path))
    assert str(e.value) == approvals.E_TRUST_BASE_UNRESOLVED and "E_TRUST_BASE_UNRESOLVED" in str(e.value)
    gh = PostGh()
    assert cli.main(["questions", "7", "--post", "--apply"], gh=gh) == 2
    assert "E_TRUST_BASE_UNRESOLVED" in capsys.readouterr().err and not gh.armed and gh.calls == []
    monkeypatch.setenv(var, "false")
    assert approvals.target_trust(str(tmp_path)).approval == "terminal"
    monkeypatch.delenv(var)
    gh = PostGh()
    assert cli.main(["questions", "7", "--post", "--apply"], gh=gh) == 0 and gh.armed


# =============================== T28d: N1 (check re-pins after a revision), the CI guard, an unknown --base ===============================

def test_check_re_pins_a_review_gate_after_its_artifacts_change(tmp_path, monkeypatch, thread, capsys):
    """T28d AC1 (re-review N1): the gate's OWN pin of older text is replaced by the next check, never a refusal; a pin of the same text still holds."""
    _review_repo(tmp_path, monkeypatch)
    trust_is(monkeypatch, COMMENT)
    gh, cfg = NoWriteGh(), Config.load()
    a = h("review-spec", approvals.artifacts("review-spec", N, cfg, "."))
    thread.items = [C("/approve review-spec %s" % a, 14)]
    assert _check(capsys, gh)[0] == 0 and approvals.read_pins(".", N)["review-spec"]["hash"] == a
    (tmp_path / "specs" / "001-x" / "spec.md").write_text("# Spec\nthe spec, revised\n", encoding="utf-8")
    b = h("review-spec", approvals.artifacts("review-spec", N, cfg, "."))
    thread.items.append(C("/approve review-spec %s" % b, 15))
    code, out, err = _check(capsys, gh)
    assert (code, err) == (0, "") and json.loads(out)["pinned"] == 15
    assert approvals.read_pins(".", N)["review-spec"]["hash"] == b and approvals.read_pins(".", N)["review-spec"]["approval"]["id"] == 15
    assert _check(capsys, gh)[0] == 0 and [p["id"] for p in approvals.read_pins(".", N)["review-spec"]["seen"]] == [14, 15]
    thread.items = [c for c in thread.items if c.id != 14]  # a comment pinned with the SAME text is deleted: still refused
    code, _, err = _check(capsys, gh)
    assert code == 2 and approvals.E_PIN_GONE in err


def test_a_pin_of_another_gate_of_the_verb_for_an_older_plan_still_refuses_the_apply(thread, tmp_path):
    """T28d AC1: the N1 fix is `check`'s only; `require` keeps E_PIN_OTHER for every gate of the verb's set."""
    _pinned(thread, tmp_path, [C("/approve approve-sync %s" % h("approve-sync", b"old plan"), 11)], gate="approve-sync", data=b"old plan")
    thread.items = [C("/approve approve-push %s" % h("approve-push", b"plan"), 12)]
    with pytest.raises(approvals.ApprovalMissing) as e:
        require(NoWriteGh(), repo=tmp_path)
    assert str(e.value) == approvals.E_PIN_OTHER
    os.unlink(approvals.pin_path(tmp_path, N))
    _pinned(thread, tmp_path, [C("/approve approve-push %s" % h("approve-push", b"old plan"), 13)], gate="approve-push", data=b"old plan")
    thread.items = [C("/approve approve-push %s" % h("approve-push", b"plan"), 14)]
    with pytest.raises(approvals.ApprovalMissing) as e:  # the approving gate's own older pin too
        require(NoWriteGh(), repo=tmp_path)
    assert str(e.value) == approvals.E_PIN_OTHER


def _origin_head(git):
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")


@pytest.mark.parametrize("var", ["GITHUB_ACTIONS", "CI"])
def test_in_ci_a_resolvable_target_is_read_and_ci_values_are_true_or_1(tmp_path, monkeypatch, var):
    """T28d AC2 (re-review N2, N4): CI with a readable origin/HEAD (or a readable --base) gives the trust; `true` and `1` in any case are CI."""
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    git, commit_config = _git_repo(tmp_path)
    commit_config(approval="comment", approvers=[HUMAN])
    git("update-ref", "refs/heads/release", "HEAD")
    for value in ("true", "TRUE", " True ", "1"):
        monkeypatch.setenv(var, value)
        with pytest.raises(approvals.ApprovalRefused) as e:
            approvals.target_trust(str(tmp_path))
        assert str(e.value) == approvals.E_TRUST_BASE_UNRESOLVED, value
        assert approvals.target_trust(str(tmp_path), base="release").approvers == [HUMAN]  # a readable --base alone is a target
    for value in ("false", "0", "", "yes"):
        monkeypatch.setenv(var, value)
        with pytest.raises(Exception) as e:  # not CI: the working tree sets trust keys and nothing can check them
            approvals.target_trust(str(tmp_path))
        assert str(e.value) == approvals.E_NO_TARGET, value
    monkeypatch.setenv(var, "true")
    _origin_head(git)
    t = approvals.target_trust(str(tmp_path))
    assert t.approval == "comment" and t.approvers == [HUMAN]


def test_an_unknown_base_is_refused_never_dropped(tmp_path, monkeypatch):
    """T28d AC3 (re-review N5): `pr create --base` naming no branch here refuses (exit 2 through the hook) when origin/HEAD resolves, instead of being
    dropped; with no origin/HEAD it is "no target" (CI refuses; locally the documented fallback)."""
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    git, commit_config = _git_repo(tmp_path)
    commit_config()
    _origin_head(git)
    assert approvals.target_trust(str(tmp_path), base="main").approval == "terminal"
    for base in ("release", "origin/nope", "0" * 40):
        with pytest.raises(approvals.ApprovalRefused) as e:
            approvals.target_trust(str(tmp_path), base=base)
        assert str(e.value) == approvals.E_BASE_UNKNOWN, base
    hook = approvals.approve_hook(NoWriteGh(), REF, "pr-create", "plan", True, repo=str(tmp_path), base="release")
    with pytest.raises(approvals.ApprovalRefused):
        hook()
    git("update-ref", "refs/heads/release", "HEAD")
    assert approvals.target_trust(str(tmp_path), base="release").approval == "terminal"
    git("symbolic-ref", "--delete", "refs/remotes/origin/HEAD")  # no origin/HEAD either: no target at all, the CI refusal or the local fallback
    assert approvals.target_trust(str(tmp_path), base="nope").approval == "terminal"
    monkeypatch.setenv("CI", "true")
    with pytest.raises(approvals.ApprovalRefused) as e:
        approvals.target_trust(str(tmp_path), base="nope")
    assert str(e.value) == approvals.E_TRUST_BASE_UNRESOLVED


def test_the_docs_say_how_wide_the_ci_refusal_is_and_what_to_do():
    """T28d AC2/AC4: the refusal is wider than an apply, `--base` helps `pr create` only, and the CI remedy is a fetch plus set-head."""
    doc = " ".join(open(os.path.join(os.path.dirname(cli.__file__), "..", "..", "docs", "trust-and-limits.md")).read().split())
    for words in ("`ghp done` (a dry run too", "`questions --sync`", "`decisions --sync`", "`decisions --resolve`", "`approvals check`",
                  "`approvals sync`", "terminal mode", "`git fetch origin <default>`", "`git remote set-head origin <default>`",
                  "only `pr create`", "`true` or `1`"):
        assert words in doc, words
    assert "every apply refuses" not in doc and "or pass `--base`" not in doc
    assert "--base" not in approvals.E_TRUST_BASE_UNRESOLVED


# =============================== T28d: the remaining ado `decisions --sync` / `questions --sync` cases (GitHub seam) ===============================
# Tests not ported (ADO only): test_sync_refused_when_bypass_bit (decisions) and test_sync_verb_refused_when_bypass_bit (questions): no bypass bit on GitHub;
# test_sync_verb_refuses_without_versions_even_without_assume and test_sync_verb_with_all_questions_answered_needs_no_versions (ADO comment versions; on
# GitHub each questions comment is a version, `own_comments`), test_sync_verb_uses_the_real_server_clock_not_the_local_one (no trusted server clock on Gh
# yet: T28b; ghp refuses instead, test_questions_sync_verb), test_sync_posting_markup_hides_nothing (ADO HTML; GitHub's kit body is checked whole by
# `kit_lines`, ported below as the exact-lines case), test_a_line_broken_by_an_entity_or_newline_inside_a_version_is_not_shown (HTML entities).

def test_sync_posting_lines_are_the_kits_exact_lines():
    """ado test_sync_posting_lines_are_split_at_br_only: a posting is the kit's body byte for byte (marker + content hash first); anything else is refused."""
    md, body = _posted(S1)
    marker, rest = body.split("\n", 1)
    for name, bad in {"marker-not-first": "x\n" + body, "look-alike-marker": body.replace(decisions.MARKER, "[spec-kit-ghp:questions]", 1),
                      "marker-text-first": "x " + body, "split-line": marker + "\n" + rest.replace("D1 (decision): ", "D1 (decision):\n", 1),
                      "quoted": "> " + body}.items():
        new, res = _dsync(md, [_kit(bad), _vote(11, "/approve D1", minute=7)])
        assert res["applied"] == [] and new == md and res["skipped"]["D1"] in ("posting-mismatch", "posting-markup"), name
    new, res = _dsync(md, [_kit(body.replace("\n", "\r\n")), _vote(11, "/approve D1", minute=7)])  # GitHub may return CRLF: the same posting
    assert res["applied"] == ["D1"]


def test_sync_ids_are_ascii_digits_only():
    md, body = _posted(S1)
    for bad in ("- [ ] D٣: x → _(open)_ · kind: decision · why: w", "- [ ] D" + "9" * 12 + ": x → _(open)_ · kind: decision · why: w"):
        with pytest.raises(ValueError, match="malformed"):
            _dsync(md + bad + "\n", [_kit(body)])


def test_bad_newer_is_reported_and_holds_the_line_open():
    """ado test_bad_newer_is_reported_next_to_the_verdict; GitHub difference (T28d fail-closed fix): ado wrote the older verdict with a notice, ghp keeps
    the line open (`bad-newer`) as the wait gate and the cross-check do. A malformed line naming D1 is not about D2."""
    md, body = _posted(S1, S2)
    new, res = _dsync(md, [_kit(body), _vote(11, "/approve D1"), _vote(12, "/reject D1 nope", minute=6), _vote(13, "/approve D2")])
    assert res["applied"] == ["D2"] and res["skipped"] == {"D1": "bad-newer"} and res["bad_newer"] == {"D1": 1, "D2": 0}
    assert all(type(v) is int for v in res["bad_newer"].values()) and decisions.parse_decisions(new)["open"][0]["id"] == "D1"


def test_sync_reads_an_edited_verdict_through_the_guards():
    md, body = _posted(S1)
    new, res = _dsync(md, [_kit(body), _vote(11, "/approve D1", edit_count=1, last_edited_at="2026-10-01T10:20:00Z")])
    assert new == md and res["skipped"] == {"D1": "no-verdict"}  # an edited comment never counts (on GitHub nobody can tell what it said before)


def test_sync_changes_only_target_lines_crlf_kept(tmp_path):
    md, body = _posted(S1, S2, S3)
    crlf = (md + "A trailing prose line\n").replace("\n", "\r\n") + "last line, no terminator"
    path = tmp_path / "Decisions_7.md"
    path.write_bytes(crlf.encode("utf-8"))
    rows = Comments([_kit(body), _vote(11, "/reject D2: no")])
    assert decisions.apply_sync(path, rows, {HUMAN}, EXCL)["applied"] == ["D2"]
    old, new = crlf.encode().split(b"\r\n"), path.read_bytes().split(b"\r\n")
    assert len(old) == len(new) and [i for i, (a, b) in enumerate(zip(old, new)) if a != b] == [next(i for i, ln in enumerate(old) if ln.startswith(b"- [ ] D2"))]
    assert b"\n" not in path.read_bytes().replace(b"\r\n", b"") and new[-1] == b"last line, no terminator" and list(tmp_path.glob("*.tmp")) == []
    before = path.stat().st_mtime_ns
    assert decisions.apply_sync(path, rows, {HUMAN}, EXCL)["applied"] == []  # nothing to apply: the file is not even rewritten
    assert path.stat().st_mtime_ns == before


def test_decisions_apply_sync_refuses_a_changed_or_symlinked_file(tmp_path, monkeypatch):
    md, body = _posted(S1)
    path = tmp_path / "Decisions_7.md"
    path.write_text(md, encoding="utf-8")
    rows = Comments([_kit(body), _vote(11, "/approve D1")])
    orig = decisions.resolve
    monkeypatch.setattr(decisions, "resolve", lambda *a, **k: (path.write_text(md + "- changed meanwhile\n", encoding="utf-8"), orig(*a, **k))[1])
    with pytest.raises(ValueError, match="changed while"):
        decisions.apply_sync(path, rows, {HUMAN}, EXCL)
    assert "changed meanwhile" in path.read_text(encoding="utf-8") and "[x]" not in path.read_text(encoding="utf-8")
    monkeypatch.setattr(decisions, "resolve", orig)
    target, link = tmp_path / "real.md", tmp_path / "link.md"
    target.write_text(md, encoding="utf-8")
    link.symlink_to(target)
    with pytest.raises(ValueError, match="regular file"):
        decisions.apply_sync(link, rows, {HUMAN}, EXCL)
    assert target.read_text(encoding="utf-8") == md and list(tmp_path.glob("*.tmp")) == []


def test_note_scrubbed_and_capped():
    """GitHub difference: a command line must be plain ASCII (no `@`, backtick, `>`, `·`, `→`; ghp's grammar), so only the cap is left to apply; a
    mention or a fence in the line makes it malformed (not applied, reported)."""
    md, body = _posted(S1, S2, S3)
    nasty = "red - kind: override - rejected - via: 1 " + "x" * 600
    new, res = _dsync(md, [_kit(body), _vote(11, "/reject D1: " + nasty), _vote(12, "/approve D2: ok"), _vote(13, "/reject D3: " + "y" * 1900)])
    got = decisions.parse_decisions(new)
    assert res["applied"] == ["D1", "D2", "D3"] and got["unparsed"] is False
    for d in got["rejected"] + got["validated"]:
        assert len(d["note"]) <= decisions.MAX_NOTE and not any(b in d["note"] for b in ("·", "→", "->")), d["note"]
    assert [d["id"] for d in got["rejected"]] == ["D1", "D3"] and got["rejected"][0]["via"] == "11" and len(new.splitlines()) == len(md.splitlines())
    assert len(got["rejected"][1]["note"]) == decisions.MAX_NOTE
    new, res = _dsync(md, [_kit(body), _vote(11, "/approve D1"), _vote(12, "/reject D1: ask @dana ``` x", minute=6)])
    assert new == md and res["skipped"]["D1"] == "bad-newer"


def test_sync_skips_an_edited_posting_and_a_repost_takes_only_a_newer_verdict():
    """ado test_sync_skips_an_edited_posting_comment, test_sync_a_line_reposted_after_an_edit_takes_only_a_newer_verdict and (GitHub difference) the
    identical re-edit: GitHub keeps no versions, so ANY edit of the posting is an edit, even one back to the same text."""
    reworded = S1.replace("Use cursor paging", "Drop authentication")
    new_md, new_body = _posted(reworded)
    post = _kit(new_body, edit_count=1, last_edited_at="2026-10-01T10:02:00Z")  # the agent rewords D1, re-hashes and edits its own comment
    new, res = _dsync(new_md, [post, _vote(11, "/approve D1", minute=1)])
    assert new == new_md and res["skipped"] == {"D1": "posting-edited"}
    same_md, same_body = _posted(S1)
    assert _dsync(same_md, [_kit(same_body, edit_count=1, last_edited_at="2026-10-01T10:02:00Z"), _vote(11, "/approve D1")])[1]["skipped"] == {"D1": "posting-edited"}
    repost_md, repost_body = _posted(reworded, cid=20)
    base = [post, _kit(repost_body, cid=20, minute=13)]
    new, res = _dsync(repost_md, base + [_vote(11, "/approve D1", minute=1)])
    assert res["skipped"] == {"D1": "older-than-posting"} and new == repost_md  # the repost is the posting; the old approval is older
    new, res = _dsync(repost_md, base + [_vote(12, "/approve D1", minute=14)])
    assert res["applied"] == ["D1"] and decisions.parse_decisions(new)["validated"][0]["via"] == "12"


def test_sync_skips_a_line_the_posted_comment_line_cannot_show_exactly():
    long_text = "Use cursor paging " + "x" * 600 + " END"
    md, body = _posted("- [ ] D1: %s → _(open)_ · kind: decision · why: w" % long_text)
    assert "END" not in body  # cut at 500 characters
    changed = md.replace("END", "DIFFERENT").replace("posted: 10:" + decisions.line_hash(long_text),
                                                     "posted: 10:" + decisions.line_hash(long_text.replace("END", "DIFFERENT")))
    for text in (md, changed):
        new, res = _dsync(text, [_kit(body), _vote(11, "/approve D1")])
        assert new == text and res["skipped"] == {"D1": "posting-lossy"}
    mention_md, mention_body = _posted("- [ ] D1: Ask @dana first → _(open)_ · kind: decision · why: w")
    assert _dsync(mention_md, [_kit(mention_body), _vote(11, "/approve D1")])[1]["skipped"] == {"D1": "posting-lossy"}


def test_a_contradicted_synced_verdict_that_cannot_be_rewritten_refuses_naming_only_its_id():
    md, body = _posted(S1, S2)
    synced = _dsync(md, [_kit(body), _vote(11, "/approve D1"), _vote(12, "/approve D2")])[0]
    later = [_kit(body), _vote(11, "/approve D1"), _vote(12, "/approve D2"), _vote(13, "/reject D1: no", minute=13)]
    hidden = next(ln for ln in synced.splitlines() if ln.startswith("- [x] D1"))
    for broken in (synced.replace(hidden, hidden + " <!-- x -->"), synced.replace("Use cursor paging", "Use offset paging")):
        with pytest.raises(ValueError, match=r"D1\b") as e:
            _dsync(broken, later)
        assert "D2" not in str(e.value) and "cursor" not in str(e.value) and "nothing written" in str(e.value)
    new, res = _dsync(synced.replace("Use cursor paging", "Use offset paging"), [_kit(body), _vote(11, "/approve D1"), _vote(12, "/approve D2")])
    assert res["skipped"]["D1"] == "edited" and res["applied"] == []  # nothing contradicts it: only a skip
    new, res = _dsync(md.replace("Use cursor paging", "Use offset paging"), [_kit(body), _vote(11, "/reject D1")])
    assert res["skipped"]["D1"] == "edited"  # an open line merely reworded stays open


def test_decisions_sync_verb_prints_applied_skipped_bad_newer_and_the_superseded_notice(tmp_path, monkeypatch, thread, capsys):
    md, body = _posted(S1, S2, S3, "- [ ] D5: supersedes D2: Cap at 20 rows → _(open)_ · kind: decision · why: w")
    _dec_repo(tmp_path, monkeypatch, md)
    trust_is(monkeypatch, COMMENT)
    thread.items = [_kit(body, author=TOKEN), _vote(11, "/approve D1"), _vote(12, "/reject D1 not ok", minute=6), _vote(13, "/approve D2")]
    gh = NoWriteGh()
    assert cli.main(["decisions", "7", "--sync"], gh=gh) == 0
    cap = capsys.readouterr()
    out = json.loads(cap.out)
    assert out["applied"] == ["D2"] and out["skipped"] == {"D1": "bad-newer", "D3": "no-verdict", "D5": "no-verdict"}
    assert out["bad_newer"] == {"D1": 1, "D2": 0, "D3": 0, "D5": 0} and out["status"] == {"D2": "validated"} and not gh.armed and cap.err == ""
    assert any(n.startswith("D1: 1 later malformed") for n in out["notice"])
    assert "D2 is superseded by D5: this verdict is masked while D5 stands (a rejected D5 would bring it back)" in out["notice"]
    assert "cursor" not in cap.out and "Cap at" not in cap.out  # ids only


def test_decisions_sync_verb_refusals_exit_2_or_1_and_write_nothing(tmp_path, monkeypatch, thread, capsys):
    from ghp.gh import GhError
    md, body = _posted(S1)
    trust_is(monkeypatch, COMMENT)
    good = [_kit(body, author=TOKEN), _vote(11, "/approve D1")]

    class NoIdentity(NoWriteGh):
        def viewer(self):
            raise GhError("E_GH_AUTH", 401, "SEKRET dana@example.invalid")

    for name, text in (("bad_file", md + "- [ ] D2: broken\n"), ("dup_file", md + S1 + "\n"), ("identity", md), ("missing", md), ("linked", md)):
        (tmp_path / name).mkdir()
        _dec_repo(tmp_path / name, monkeypatch, text)
        led = tmp_path / name / "dod" / "Decisions_7.md"
        if name == "missing":
            led.unlink()
        if name == "linked":
            real = led.with_name("real.md")
            real.write_text(text, encoding="utf-8")
            led.unlink()
            led.symlink_to(real)
        thread.items = list(good)
        assert cli.main(["decisions", "7", "--sync"], gh=NoIdentity() if name == "identity" else NoWriteGh()) == 2, name
        err = capsys.readouterr().err
        assert err.startswith("error:") and "SEKRET" not in err and "dana@" not in err, name
        assert (not os.path.lexists(led)) if name == "missing" else (led.read_text(encoding="utf-8") == text), name
    (tmp_path / "http").mkdir()
    _dec_repo(tmp_path / "http", monkeypatch, md)

    def boom(gh, ref):
        raise GhError("E_GH_HTTP", 500, "x")
    monkeypatch.setattr(comments, "fetch", boom)
    assert cli.main(["decisions", "7", "--sync"], gh=NoWriteGh()) == 1 and (tmp_path / "http" / "dod" / "Decisions_7.md").read_text() == md


def test_decisions_sync_verb_refuses_exit_2_when_a_reject_cannot_land(tmp_path, monkeypatch, thread, capsys):
    md, body = _posted(S1)
    synced = _dsync(md, [_kit(body), _vote(11, "/approve D1")])[0]
    hidden = next(ln for ln in synced.splitlines() if ln.startswith("- [x] D1"))
    broken = synced.replace(hidden, hidden + " <!-- x -->")
    _dec_repo(tmp_path, monkeypatch, broken)
    trust_is(monkeypatch, COMMENT)
    thread.items = [_kit(body, author=TOKEN), _vote(11, "/approve D1"), _vote(13, "/reject D1: no", minute=13)]
    assert cli.main(["decisions", "7", "--sync"], gh=NoWriteGh()) == 2
    err = capsys.readouterr().err
    assert err.startswith("error:") and "D1" in err and (tmp_path / "dod" / "Decisions_7.md").read_text(encoding="utf-8") == broken


@pytest.mark.parametrize("extra", [["--post"], ["--apply"], ["--plan-out", "p.md"], ["--add", "x", "--kind", "decision"], ["--resolve", "D1", "--validated"],
                                   ["--if-any"], ["--force"], ["--by", "PO"], ["--note", "n"], ["--validated"], ["--kind", "decision"], ["--supersedes", "D1"]])
def test_decisions_sync_flag_combinations_exit_2(tmp_path, monkeypatch, thread, capsys, extra):
    _dec_repo(tmp_path, monkeypatch, _posted(S1)[0])
    trust_is(monkeypatch, COMMENT)
    assert cli.main(["decisions", "7", "--sync", *extra], gh=NoWriteGh()) == 2 and thread.reads == 0
    assert capsys.readouterr().err.startswith("error:")


# ---- questions --sync ----

QA = "- [ ] Q1: Are contractors included? → _(open)_ · owner: PO\n"
QB = "- [ ] Q2: Which locale do we ship? → _(open)_ · owner: PO\n"
QC = "- [ ] Q3: Export format? → _(open)_ · owner: PO\n"


def test_a_line_a_rewrite_would_damage_blocks_the_questions_sync():
    md = _qledger("- [ ] Q1: Are contractors included? <!-- keep --> → _(open)_ · owner: PO\n" + QB)
    shown = questions.parse_questions(md)["open"]
    with pytest.raises(ValueError, match="Q1.*nothing written"):
        _qsync(md, [_qkit(shown), _vote(11, "/answer Q1: yes"), _vote(12, "/answer Q2: de-DE")])
    long = _qledger(" " * 1300 + "- [ ] Q1: %s → _(open)_ · owner: PO\n" % ("q" * 480))  # the answer would push the line over the cap
    shown = questions.parse_questions(long)["open"]
    with pytest.raises(ValueError, match="(Q1.*nothing written)|malformed"):
        _qsync(long, [_qkit(shown), _vote(11, "/answer Q1: " + "a " * 250)])


def test_questions_sync_keeps_crlf_and_changes_only_the_target_line():
    md = _qledger(QA + QB).replace("\n", "\r\n")
    shown = questions.parse_questions(md)["open"]
    new, res = _qsync(md, [_qkit(shown), _vote(11, "/answer Q2: de-DE")])
    assert new == md.replace(QB.rstrip("\n"), "- [x] Q2: Which locale do we ship? → de-DE (PO comment 11) · owner: PO") and "\n" not in new.replace("\r\n", "")
    assert res["answered"] == ["Q2"]


def test_questions_apply_sync_refuses_a_changed_symlinked_or_big_file(tmp_path):
    md = _qledger(QA)
    shown = questions.parse_questions(md)["open"]
    rows = Comments([_qkit(shown), _vote(11, "/answer Q1: yes")])
    args = (rows, {HUMAN}, {TOKEN}, None, None, questions.ages(shown, rows, TOKEN))
    f = tmp_path / "DoD_7.md"
    f.write_bytes(md.encode())
    assert questions.apply_sync(f, md, *args)["answered"] == ["Q1"] and "(PO comment 11)" in f.read_text(encoding="utf-8")
    f.write_bytes((md + "extra\n").encode())
    with pytest.raises(ValueError, match="nothing written"):
        questions.apply_sync(f, md, *args)
    assert f.read_bytes() == (md + "extra\n").encode()
    real, link, big = tmp_path / "real.md", tmp_path / "link.md", tmp_path / "big.md"
    real.write_bytes(md.encode())
    link.symlink_to(real)
    big.write_bytes(b"x" * ((1 << 20) + 1))
    for p in (link, big):
        with pytest.raises(ValueError, match="regular file"):
            questions.apply_sync(p, md, *args)
    assert real.read_bytes() == md.encode()


def test_a_newer_malformed_answer_line_holds_the_question_open():
    """T28d fail-closed fix: the wait gate holds a question open under a newer malformed approver line; the sync now does too (it wrote the answer)."""
    md = _qledger(QA)
    shown = questions.parse_questions(md)["open"]
    new, res = _qsync(md, [_qkit(shown), _vote(11, "/answer Q1: yes"), _vote(12, "/answer Q1 oops", minute=13)])
    assert new == md and res["skipped"] == {"Q1": "bad-newer"} and res["bad_newer"] == {"Q1": 1} and res["open"] == ["Q1"]


def _qrepo(tmp_path, monkeypatch, md):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo"}))
    (tmp_path / "dod").mkdir()
    (tmp_path / "dod" / "DoD_7.md").write_text(md, encoding="utf-8")
    return tmp_path / "dod" / "DoD_7.md"


def test_questions_sync_verb_prints_answered_open_bad_newer_and_ignored(tmp_path, monkeypatch, thread, capsys):
    md = _qledger(QA + QB + QC + "- [x] Q4: Which locale? → assumed: no · owner: PO\n")
    led = _qrepo(tmp_path, monkeypatch, md)
    trust_is(monkeypatch, COMMENT)
    shown = questions.parse_questions(md)["open"]
    thread.items = [_qkit(shown), _vote(11, "/answer Q1: yes"), _vote(12, "/answer Q3 oops", minute=13), _vote(14, "/answer Q4: yes they are", minute=14)]
    assert cli.main(["questions", "7", "--sync"], gh=NoWriteGh()) == 0
    cap = capsys.readouterr()
    out = json.loads(cap.out)
    assert out["id"] == 7 and out["answered"] == ["Q1"] and out["open"] == ["Q2", "Q3"] and out["due"] == [] and out["ignored"] == ["Q4"]
    assert out["bad_newer"] == {"Q1": 0, "Q2": 0, "Q3": 1} and any(n.startswith("Q3: 1 later malformed command line(s) by an approver") for n in out["notice"])
    assert "Q4: answered already: a later /answer was not applied; correct the ledger by hand" in out["notice"]
    assert "contractors" not in cap.out and "locale" not in cap.out and "yes they are" not in cap.out and cap.err == ""
    assert led.read_text(encoding="utf-8") == md.replace(QA.rstrip("\n"), "- [x] Q1: Are contractors included? → yes (PO comment 11) · owner: PO")


def test_questions_sync_verb_refusals_exit_2_or_1_and_write_nothing(tmp_path, monkeypatch, thread, capsys):
    from ghp.gh import GhError
    md = _qledger(QA)
    shown = questions.parse_questions(md)["open"]
    good = [_qkit(shown), _vote(11, "/answer Q1: yes")]

    class NoIdentity(NoWriteGh):
        def viewer(self):
            raise GhError("E_GH_AUTH", 401, "SEKRET dana@example.invalid")

    cases = {"no_approvers": (Trust([], "comment", {}), md, None, ()), "dropped": (COMMENT, md, None, [Note(comments.JOIN, 3, "x")]),
             "identity": (COMMENT, md, NoIdentity, ()), "bad_file": (COMMENT, _qledger(QA + "- [ ] Q2: broken\n```\n"), None, ()),
             "dup_file": (COMMENT, _qledger(QA + QA), None, ()), "missing": (COMMENT, md, None, ()), "linked": (COMMENT, md, None, ())}
    for name, (trust, text, ghcls, notes) in cases.items():
        (tmp_path / name).mkdir()
        led = _qrepo(tmp_path / name, monkeypatch, text)
        if name == "missing":
            led.unlink()
        if name == "linked":
            real = led.with_name("real.md")
            real.write_text(text, encoding="utf-8")
            led.unlink()
            led.symlink_to(real)
        trust_is(monkeypatch, trust)
        thread.items, thread.notes = list(good), list(notes)
        assert cli.main(["questions", "7", "--sync"], gh=(ghcls or NoWriteGh)()) == 2, name
        err = capsys.readouterr().err
        assert err.startswith("error:") and "SEKRET" not in err and "dana@" not in err, name
        assert (not os.path.lexists(led)) if name == "missing" else (led.read_text(encoding="utf-8") == text), name
        if name == "linked":
            assert "[x]" not in real.read_text(encoding="utf-8")
    thread.notes = []
    (tmp_path / "http").mkdir()
    led = _qrepo(tmp_path / "http", monkeypatch, md)

    def boom(gh, ref):
        raise GhError("E_GH_HTTP", 500, "x")
    monkeypatch.setattr(comments, "fetch", boom)
    assert cli.main(["questions", "7", "--sync"], gh=NoWriteGh()) == 1 and led.read_text() == md


@pytest.mark.parametrize("extra", [["--post"], ["--apply"], ["--plan-out", "p.md"], ["--expect-plan", "p.md"]])
def test_questions_sync_flag_combinations_exit_2(tmp_path, monkeypatch, thread, capsys, extra):
    _qrepo(tmp_path, monkeypatch, _qledger(QA))
    trust_is(monkeypatch, COMMENT)
    code = None
    try:
        code = cli.main(["questions", "7", "--sync", *extra], gh=NoWriteGh())
    except SystemExit as e:  # argparse: --expect-plan needs --apply
        code = e.code
    assert code == 2 and thread.reads == 0
    capsys.readouterr()


# =============================== T28e: a re-pin never forgives a deleted pinned comment ===============================

def _spec(tmp_path, text):
    (tmp_path / "specs" / "001-x" / "spec.md").write_text(text, encoding="utf-8")
    return h("review-spec", approvals.artifacts("review-spec", N, Config.load(), "."))


def test_check_refuses_when_an_older_text_pin_lost_a_comment(tmp_path, monkeypatch, thread, capsys):
    """T28e I-1 (reviewer's probe): B approved then rejected, revised to A and approved (pins A with 10, 11, 12), set back to B, 11 and 12 deleted: the
    old /approve B must not count again."""
    _review_repo(tmp_path, monkeypatch)
    trust_is(monkeypatch, COMMENT)
    gh = NoWriteGh()
    b = _spec(tmp_path, "# Spec\ntext B\n")
    thread.items = [C("/approve review-spec %s" % b, 10), C("/reject review-spec %s" % b, 11)]
    a = _spec(tmp_path, "# Spec\ntext A\n")
    thread.items.append(C("/approve review-spec %s" % a, 12))
    assert _check(capsys, gh)[0] == 0 and [p["id"] for p in approvals.read_pins(".", N)["review-spec"]["seen"]] == [10, 11, 12]
    assert _spec(tmp_path, "# Spec\ntext B\n") == b
    thread.items = [c for c in thread.items if c.id == 10]
    code, out, err = _check(capsys, gh)
    assert (code, out) == (2, "") and approvals.E_PIN_GONE in err
    assert approvals.read_pins(".", N)["review-spec"]["hash"] == a  # nothing re-pinned
    thread.items = [C("/approve review-spec %s" % b, 10), C("/reject review-spec %s" % b, 11),
                    C("/approve review-spec %s" % a, 12, last_edited_at="2026-10-02T00:00:00Z", edit_count=1)]
    assert _check(capsys, gh)[0] == 2  # a CHANGED pinned comment (edited) refuses too


def test_check_re_pins_after_a_revision_when_every_old_comment_is_still_there(tmp_path, monkeypatch, thread, capsys):
    """T28e: the legit back-and-forth still passes (N1): nothing deleted, B re-approved after A."""
    _review_repo(tmp_path, monkeypatch)
    trust_is(monkeypatch, COMMENT)
    gh = NoWriteGh()
    b = _spec(tmp_path, "# Spec\ntext B\n")
    thread.items = [C("/approve review-spec %s" % b, 10), C("/reject review-spec %s" % b, 11)]
    a = _spec(tmp_path, "# Spec\ntext A\n")
    thread.items.append(C("/approve review-spec %s" % a, 12))
    assert _check(capsys, gh)[0] == 0
    _spec(tmp_path, "# Spec\ntext B\n")
    assert _check(capsys, gh)[0] == 2  # the newest command is /approve A: B is not approved
    thread.items.append(C("/approve review-spec %s" % b, 13))
    code, out, _ = _check(capsys, gh)
    assert code == 0 and json.loads(out)["pinned"] == 13 and approvals.read_pins(".", N)["review-spec"]["hash"] == b


def test_sync_refuses_to_re_pin_when_a_pinned_comment_is_gone(tmp_path, monkeypatch, thread):
    """T28e I-2 (reviewer's probe): approve 10, reject 11, approve 12 (pinned), 11 and 12 deleted: sync refuses with the explicit remedy, pins nothing."""
    _review_repo(tmp_path, monkeypatch)
    _run(tmp_path)
    x = h("review-spec", approvals.artifacts("review-spec", N, _cfg(), "."))
    thread.items = [C("/approve review-spec %s" % x, 10), C("/reject review-spec %s" % x, 11), C("/approve review-spec %s" % x, 12)]
    assert approvals.sync(NoWriteGh(), COMMENT, "run13", _cfg(), ".")["pinned"] == 12
    before = open(approvals.pin_path(".", N)).read()
    full = list(thread.items)
    thread.items = [full[0], full[2]]  # only the pinned /reject is deleted: the approval itself is still there
    with pytest.raises(approvals.ApprovalMissing):
        approvals.sync(NoWriteGh(), COMMENT, "run13", _cfg(), ".")
    thread.items = full[:1]
    for dry in (False, True):
        with pytest.raises(approvals.ApprovalMissing) as e:
            approvals.sync(NoWriteGh(), COMMENT, "run13", _cfg(), ".", resume=True, dry_run=dry)
        assert str(e.value) == approvals.E_PIN_GONE_REPIN.format(n=N) and ".ghp/approvals/7.json" in str(e.value)
    assert open(approvals.pin_path(".", N)).read() == before
    os.unlink(approvals.pin_path(".", N))  # the explicit remedy: a human deletes the pin knowingly
    assert approvals.sync(NoWriteGh(), COMMENT, "run13", _cfg(), ".")["pinned"] == 10


def test_sync_is_the_remedy_for_a_same_text_re_decision(tmp_path, monkeypatch, thread, capsys):
    """T28e (the T28f case): pinned at 10, then /reject and /approve of the SAME hash: `check` refuses (E_PIN_NEWER: a newer command than the pin), and
    `approvals sync` re-pins because every pinned comment is still there; `check` then passes."""
    _review_repo(tmp_path, monkeypatch)
    _run(tmp_path)
    trust_is(monkeypatch, COMMENT)
    x = h("review-spec", approvals.artifacts("review-spec", N, _cfg(), "."))
    thread.items = [C("/approve review-spec %s" % x, 10)]
    assert _check(capsys, NoWriteGh())[0] == 0
    thread.items += [C("/reject review-spec %s" % x, 11), C("/approve review-spec %s" % x, 12)]
    code, _, err = _check(capsys, NoWriteGh())
    assert code == 2 and approvals.E_PIN_NEWER in err
    assert approvals.sync(NoWriteGh(), COMMENT, "run13", _cfg(), ".")["pinned"] == 12
    assert [p["id"] for p in approvals.read_pins(".", N)["review-spec"]["seen"]] == [10, 11, 12]
    assert _check(capsys, NoWriteGh())[0] == 0


def test_questions_sync_reports_a_synced_answer_no_longer_valid(tmp_path, monkeypatch, thread, capsys):
    """T28e M-1: as decisions --sync, a synced answer with a newer malformed / edited approver line is reported (stderr), never rewritten."""
    md = _qledger(QA)
    shown = questions.parse_questions(md)["open"]
    md2, _ = _qsync(md, [_qkit(shown), _vote(11, "/answer Q1: yes")])
    for later in (_vote(12, "/answer Q1 oops", minute=13), _vote(12, "thanks", minute=13, edit_count=1, last_edited_at="2026-10-02T00:00:00Z")):
        new, res = _qsync(md2, [_qkit(shown), _vote(11, "/answer Q1: yes"), later])
        assert new == md2 and res["no_longer_valid"] == ["Q1"] and res["ignored"] == []
    assert "no_longer_valid" not in _qsync(md2, [_qkit(shown), _vote(11, "/answer Q1: yes")])[1]
    led = _qrepo(tmp_path, monkeypatch, md2)
    trust_is(monkeypatch, COMMENT)
    thread.items = [_qkit(shown), _vote(11, "/answer Q1: yes"), _vote(12, "/answer Q1 oops", minute=13)]
    assert cli.main(["questions", "7", "--sync"], gh=NoWriteGh()) == 0
    cap = capsys.readouterr()
    assert json.loads(cap.out)["no_longer_valid"] == ["Q1"] and "answer-no-longer-valid: Q1" in cap.err and led.read_text() == md2
