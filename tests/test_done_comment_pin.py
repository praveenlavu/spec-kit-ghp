"""Comment mode can close a story: `ghp approvals check <n> --gate approve-done` pins a trusted approver's `/approve approve-done <hash>` of the
`ghp done` preview in `.ghp/plan-done.md`, and `ghp done --apply --expect-plan` then closes the story through the usual done path. Every refusal
(a stranger, another hash, the agent itself, an edit or a deletion after the pin, another gate, no approval) writes nothing. Offline; fictional data."""
import json
import os

import pytest

import test_done
from test_approvals_sync import C, COMMENT, HUMAN, OTHER, TERMINAL, TOKEN, Thread, trust_is
from ghp import approvals, comments, questions
from ghp.config import Trust

PLAN = ".ghp/plan-done.md"
N = test_done.N


@pytest.fixture
def thread(monkeypatch):
    t = Thread()
    monkeypatch.setattr(comments, "fetch", t.fetch)
    return t


@pytest.fixture
def story(tmp_path, monkeypatch, thread, capsys):
    """A ready story in comment mode with its `ghp done` preview in `.ghp/plan-done.md`; returns (hub, the plan's approve-done hash)."""
    trust_is(monkeypatch, COMMENT)
    test_done.repo(tmp_path, monkeypatch)
    monkeypatch.setattr(questions, "_me", lambda gh: TOKEN)
    hub = test_done.DoneHub()
    assert test_done.run(hub, capsys, "--plan-out", PLAN)[0] == 0
    return hub, approvals.gate_hash("approve-done", N, (tmp_path / PLAN).read_text(encoding="utf-8").encode("utf-8"))


def check(hub, capsys):
    return test_done.cli.main(["approvals", "check", str(N), "--gate", "approve-done"], gh=hub), capsys.readouterr()


def apply(hub, capsys):
    return test_done.apply(hub, capsys, "--expect-plan", PLAN)


def pin_file(tmp_path):
    return tmp_path / ".ghp" / "approvals" / ("%d.json" % N)


def refused(hub, capsys, tmp_path, *needles):
    """`approvals check` exits 2 and pins nothing; the apply then exits 2 and writes nothing."""
    code, cap = check(hub, capsys)
    assert code == 2 and cap.out == "" and not pin_file(tmp_path).exists()
    assert "no valid approval for gate approve-done on issue #%d" % N in cap.err  # read and refused, not "not checked here"
    code, out, err = apply(hub, capsys)
    assert code == 2 and not hub.armed and hub.writes == [] and hub.sent() == []
    for needle in needles:
        assert needle in err
    return err


# ---- the supported route -------------------------------------------------------------------------------------------------------------------------

def test_check_pins_approve_done_and_done_apply_closes_the_story(story, thread, tmp_path, capsys):
    hub, x = story
    thread.items = [C("/approve approve-done %s" % x, 11)]
    code, cap = check(hub, capsys)
    assert code == 0 and '"pinned": 11' in cap.out and not hub.armed and hub.sent() == []
    assert approvals.read_pins(str(tmp_path), N)["approve-done"]["approval"]["id"] == 11
    code, out, err = apply(hub, capsys)
    assert code == 0 and err == "" and hub.kinds() == ["comment", "close", "set_status"]  # exactly the terminal-mode done path
    assert hub.issues[N]["state"] == "closed" and "posted the ledger comment" in out and "closed #%d as completed" % N in out


# ---- refusals: nothing pinned, nothing written ---------------------------------------------------------------------------------------------------

def test_an_approval_by_a_non_approver_pins_nothing(story, thread, tmp_path, capsys):
    hub, x = story
    thread.items = [C("/approve approve-done %s" % x, 11, author=OTHER)]
    refused(hub, capsys, tmp_path)


def test_an_approval_of_another_hash_pins_nothing(story, thread, tmp_path, capsys):
    hub, _ = story
    thread.items = [C("/approve approve-done %s" % approvals.gate_hash("approve-done", N, b"an older plan"), 11)]
    refused(hub, capsys, tmp_path)


def test_an_approval_posted_by_the_agent_pins_nothing(story, thread, tmp_path, capsys, monkeypatch):
    hub, x = story
    trust_is(monkeypatch, Trust([HUMAN, TOKEN], "comment", COMMENT.unattended))  # even when the agent's own id is wrongly listed as an approver
    thread.items = [C("/approve approve-done %s" % x, 11, author=TOKEN)]
    refused(hub, capsys, tmp_path)


def test_an_approval_edited_before_the_check_pins_nothing(story, thread, tmp_path, capsys):
    hub, x = story
    thread.items = [C("/approve approve-done %s" % x, 11, last_edited_at="2026-10-01T11:00:00Z", edit_count=1)]
    refused(hub, capsys, tmp_path)


def test_an_approval_edited_after_the_pin_refuses_the_apply(story, thread, tmp_path, capsys):
    hub, x = story
    thread.items = [C("/approve approve-done %s" % x, 11)]
    assert check(hub, capsys)[0] == 0
    # a newer valid approval of the same plan is there (an older one would not do: an edited approver comment newer than the approval already
    # refuses it), so the decision approves and only the pin can refuse
    thread.items = [C("/approve approve-done %s" % x, 11, last_edited_at="2026-10-01T11:00:00Z", edit_count=1), C("/approve approve-done %s" % x, 12)]
    code, out, err = apply(hub, capsys)
    assert code == 2 and "gone or changed" in err and not hub.armed and hub.writes == [] and hub.sent() == []


def test_a_repin_after_the_pinned_comment_was_deleted_refuses(story, thread, tmp_path, capsys):
    hub, x = story
    thread.items = [C("/approve approve-done %s" % x, 11)]
    assert check(hub, capsys)[0] == 0
    before = pin_file(tmp_path).read_text()
    (tmp_path / PLAN).write_text("a revised plan\n", encoding="utf-8")
    thread.items = [C("/approve approve-done %s" % approvals.gate_hash("approve-done", N, b"a revised plan\n"), 12)]  # 11 is gone
    code, cap = check(hub, capsys)
    assert code == 2 and "gone or changed" in cap.err and pin_file(tmp_path).read_text() == before


def test_a_recheck_after_a_newer_approval_refuses(story, thread, tmp_path, capsys):
    hub, x = story
    thread.items = [C("/approve approve-done %s" % x, 11)]
    assert check(hub, capsys)[0] == 0
    before = pin_file(tmp_path).read_text()
    thread.items.append(C("/approve approve-done %s" % x, 12))
    code, cap = check(hub, capsys)
    assert code == 2 and "a newer command than the pinned approval" in cap.err and pin_file(tmp_path).read_text() == before


def test_a_newer_approval_after_the_pin_refuses_the_apply(story, thread, tmp_path, capsys):
    hub, x = story
    thread.items = [C("/approve approve-done %s" % x, 11)]
    assert check(hub, capsys)[0] == 0
    thread.items.append(C("/approve approve-done %s" % x, 12))
    code, out, err = apply(hub, capsys)
    assert code == 2 and "a newer command than the pinned approval" in err and not hub.armed and hub.sent() == []


def test_a_pin_that_no_longer_matches_its_approval_refuses_the_apply(story, thread, tmp_path, capsys):
    hub, x = story
    thread.items = [C("/approve approve-done %s" % x, 11)]
    assert check(hub, capsys)[0] == 0
    data = json.loads(pin_file(tmp_path).read_text())
    entry = data["gates"]["approve-done"]
    entry["seen"], entry["approval"]["body_sha256"] = [], "0" * 64  # a pin file changed by hand: the recorded approval is not the live comment
    pin_file(tmp_path).write_text(json.dumps(data))
    code, out, err = apply(hub, capsys)
    assert code == 2 and "no longer holds" in err and not hub.armed and hub.sent() == []


def test_a_pinned_reject_deleted_after_the_pin_refuses_the_apply(story, thread, tmp_path, capsys):
    hub, x = story
    thread.items = [C("/reject approve-done %s" % x, 10), C("/approve approve-done %s" % x, 11)]
    assert check(hub, capsys)[0] == 0
    thread.items = [C("/approve approve-done %s" % x, 11)]  # the approver's earlier /reject is gone
    code, out, err = apply(hub, capsys)
    assert code == 2 and "gone or changed" in err and not hub.armed and hub.writes == [] and hub.sent() == []


def test_an_approval_of_another_gate_pins_nothing(story, thread, tmp_path, capsys):
    hub, _ = story
    plan = (tmp_path / PLAN).read_bytes()
    thread.items = [C("/approve approve-pr %s" % approvals.gate_hash("approve-pr", N, plan), 11)]
    refused(hub, capsys, tmp_path)
    code, cap = test_done.cli.main(["approvals", "check", str(N), "--gate", "approve-activate"], gh=hub), capsys.readouterr()
    assert code == 2 and "is not checked here" in cap.err  # the other apply gates are still checked only by their own --apply


def test_no_approval_pins_nothing_and_shows_the_hash(story, thread, tmp_path, capsys):
    hub, x = story
    code, cap = check(hub, capsys)
    assert code == 2 and "/approve approve-done %s" % x in cap.err and not pin_file(tmp_path).exists()
    code, out, err = apply(hub, capsys)
    assert code == 2 and not hub.armed and hub.sent() == []


def test_an_unpinned_approval_names_the_route_that_pins_it(story, thread, tmp_path, capsys):
    hub, x = story
    thread.items = [C("/approve approve-done %s" % x, 11)]
    code, out, err = apply(hub, capsys)
    assert code == 2 and "no approval of this plan was pinned" in err and not hub.armed and hub.sent() == []
    assert "ghp approvals check %d --gate approve-done" % N in err and "approvals sync" not in err


def test_terminal_mode_is_unchanged(tmp_path, monkeypatch, thread, capsys):
    trust_is(monkeypatch, TERMINAL)
    test_done.repo(tmp_path, monkeypatch)
    hub = test_done.DoneHub()
    assert test_done.run(hub, capsys, "--plan-out", PLAN)[0] == 0
    reads = thread.reads
    code, cap = check(hub, capsys)
    assert code == 0 and '"mode": "terminal"' in cap.out and thread.reads == reads  # the check reads nothing in terminal mode
    code, out, err = apply(hub, capsys)
    assert code == 0 and hub.kinds() == ["comment", "close", "set_status"] and not os.path.exists(pin_file(tmp_path))
