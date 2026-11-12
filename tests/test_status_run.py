"""T25b: run awareness of `ghp status` (ado's P4-5 tests, ported mechanically). The newest Spec Kit run state of the `ghp-lifecycle` workflow for the
issue (`.specify/workflows/runs/<run id>/state.json`, a file the agent can edit) says whether a run is paused, failed, aborted or stuck and what to run.

Rename table applied: ado-lifecycle -> ghp-lifecycle, inputs.story_id -> inputs.issue_number, `Story N` -> `Issue #N`, speckit.ado -> speckit.ghp,
`ado approvals sync` -> `ghp approvals sync`, AdoError -> GhError, the fake Board -> the Hub of test_status. The step ids are `approvals.STEP_IDS` (the
62 ids of workflows/ghp-lifecycle, pinned there by tests/test_workflow.py), the gates `approvals.GATES` without `approve-done` and `approve-activate`.

Ado run cases that cannot apply:
- `test_shipped_step_ids_match_the_workflow_file` and the order test of the catch-up list are NOT repeated here: tests/test_workflow.py already pins
  `approvals.STEP_IDS` and the gates to workflows/ghp-lifecycle/workflow.yml, and `status.SHIPPED_STEPS` / `SHIPPED_GATES` are asserted below to be those
  very constants.
- ado's `server_now` (an Azure DevOps server clock) has no GitHub equivalent yet: `Gh` has no `server_now` (approvals and questions say the same), so
  against the real `Gh` a running run always says "its age was not checked (no server time)". The over-1-hour branch is tested with a Hub that has a clock.
- The fixtures are ado's sanitized real runs re-labelled for ghp-lifecycle (workflow id, `issue_number`); no real ghp run was captured yet (T34)."""
import datetime
import json
import os
import re
import shutil
import time
from pathlib import Path

import pytest

import ghp.__main__ as cli
from conftest import commit, git
from ghp import approvals, comments, status
from ghp.config import Config
from ghp.gh import GhError
from test_status import ID, Hub, check, env, world  # noqa: F401 - `env` is the fixture of test_status

BOARD = status.BOARD_NOT_CHECKED
ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "runs"
OUTPUT = "ZZ-OUTPUT-TEXT"  # every fixture step holds this in its `output`: it must never be read or printed
NOW = datetime.datetime(2026, 11, 2, 12, 0, 0, tzinfo=datetime.timezone.utc)
APPROVER = 66666666


class Clocked(Hub):
    """A hub that also knows the server's time (the only clock the 'running for over 1 h' rule may use)."""

    def __init__(self, *a, now=NOW, **kw):
        super().__init__(*a, **kw)
        self.now = now

    def server_now(self):
        return self.now


def runs_dir(repo):
    return Path(repo) / ".specify" / "workflows" / "runs"


def put_run(repo, fixture, rdir=None, **over):
    """Copy a sanitized fixture to <repo>/.specify/workflows/runs/<run_id>/state.json, with top-level keys overridden (None deletes one)."""
    data = json.loads((FIXTURES / f"{fixture}.json").read_text(encoding="utf-8"))
    rid = rdir or data["run_id"]
    data.update({"run_id": rid, **over})
    for k in [k for k, v in data.items() if v is None and k in over]:
        del data[k]
    d = runs_dir(repo) / rid
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(data), encoding="utf-8")
    return d / "state.json"


def ago(**kw):
    return (NOW - datetime.timedelta(**kw)).isoformat()


def lines(res):
    return status.render(res, ID).splitlines()


def test_the_constants_are_the_shipped_workflows():
    assert status.SHIPPED_STEPS == approvals.STEP_IDS and len(status.SHIPPED_STEPS) == 62
    assert status.SHIPPED_GATES == tuple(g for g in approvals.GATES if g not in ("approve-done", "approve-activate")) and len(status.SHIPPED_GATES) == 12
    assert set(status.SHIPPED_GATES) <= set(status.SHIPPED_STEPS)
    assert (status.RUN_WORKFLOW, status.RUN_ID, status.RUN_STATUSES, status.RUNS_DIR) == (approvals.RUN_WORKFLOW, approvals.RUN_ID, approvals.RUN_STATUSES, approvals.RUNS_DIR)
    assert status.RUN_WORKFLOW == "ghp-lifecycle"


@pytest.mark.parametrize("fixture", sorted(p.stem for p in FIXTURES.glob("*.json")))
def test_every_fixture_is_a_readable_run_of_this_issue(env, fixture):
    put_run(env, fixture)
    run = status.read_run(str(env), ID)
    assert run is not None and set(run) == {"run_id", "status", "step", "updated_at"}


def test_paused_prints_resume(env):
    put_run(env, "paused-gate")
    run = status.read_run(str(env), ID)
    assert run == {"run_id": "aaaa0006", "status": "paused", "step": "review-spec", "updated_at": "2026-10-31T03:59:16.620745+00:00"}
    world(env, plan=False)
    res = check(env, Hub(), "plan")
    assert res["run"] == run
    text = status.render(res, ID)
    assert "Run aaaa0006: paused at gate review-spec. Answer it in a terminal: specify workflow resume aaaa0006" in text.splitlines()
    assert text.splitlines()[0] == f"Issue #{ID}: waiting on stage plan. Next: speckit.ghp.plan {ID}"  # the stage answer is unchanged


def _comment_mode_target(repo):
    """Commit `approval: comment` (with an approver) to the target branch (origin/main), then go back to the feature branch."""
    git(repo, "checkout", "-q", "main")
    commit(repo, "target", {"ghp.config.json": json.dumps({"owner": "octo", "repo": "demo", "approval": "comment", "approvers": [APPROVER]})})
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "checkout", "-q", f"feat/gh{ID}-x")


def test_paused_gate_in_comment_mode_points_at_the_approver_and_the_poller(env):
    put_run(env, "paused-gate")
    world(env, plan=False)
    _comment_mode_target(env)
    out = lines(check(env, Hub()))
    assert "Run aaaa0006: paused at gate review-spec. Approve with the approver's comment, then: ghp approvals sync aaaa0006 --resume" in out
    assert not any("terminal" in ln for ln in out)
    put_run(env, "paused-gate", current_step_id="implement")  # a pause that is not a gate keeps today's line, whatever the mode
    assert "Run aaaa0006: paused at implement. Resume: specify workflow resume aaaa0006" in lines(check(env, Hub()))


def test_paused_gate_with_an_unreadable_target_keeps_the_terminal_line(env):
    put_run(env, "paused-gate")
    world(env, plan=False, cfg={"approval": "comment"})  # a working-tree key and a target that cannot vouch for it: the trust read refuses
    git(env, "update-ref", "-d", "refs/remotes/origin/HEAD")
    assert "Run aaaa0006: paused at gate review-spec. Answer it in a terminal: specify workflow resume aaaa0006" in lines(check(env, Hub()))


def test_the_resume_verb_and_flag_exist():
    """The text printed names `ghp approvals sync <run> --resume`: that verb, action and flag must be the real ones."""
    p = __import__("argparse").ArgumentParser()
    status_sub = p.add_subparsers()
    approvals.register(status_sub)
    ns = p.parse_args(["approvals", "sync", "aaaa0006", "--resume"])
    assert (ns.action, ns.run_id, ns.resume) == ("sync", "aaaa0006", True)


def test_failed_names_step_and_resume(env):
    put_run(env, "failed-pull-cli")
    res = check(env, None, offline=True)
    assert res["run"]["status"] == "failed" and res["run"]["step"] == "pull"
    assert "Run aaaa0002: failed at pull. Resume (it re-runs that step and everything after it): specify workflow resume aaaa0002" in lines(res)


@pytest.mark.parametrize("step", ["push-loop:push-preview:1", "push-loop:approve-push:12", "pr-loop:pr-apply:3"])
def test_nested_loop_step_id_accepted(env, step):
    put_run(env, "failed-after-loop", current_step_id=step)
    assert status.read_run(str(env), ID)["step"] == step  # `<loop>:<step>:<n>` with both ids shipped


@pytest.mark.parametrize("step", ["nope", "push-loop:nope:1", "nope:push-preview:1", "push-loop:push-preview:x", "push-loop:push-preview:", "push-loop:push-preview:1:2",
                                  "push-loop:push-preview:-1", "push-loop:push-preview:1\nrm -rf", "push-loop:push-preview:١", "PULL", "pull; id", "", None, 7, ["pull"]])
def test_nested_id_with_unknown_part_ignored(env, step):
    put_run(env, "failed-after-loop", current_step_id=step)
    assert status.read_run(str(env), ID) is None
    res = status.evaluate(None, ID, str(env), offline=True)
    assert res["run"] is None and res["run_notes"] == [status.RUN_UNREADABLE.format(n=1)]


def test_running_over_1h_prints_killed_run_advice(env):
    put_run(env, "paused-gate", status="running", current_step_id="implement", updated_at=ago(hours=2))
    world(env, plan=False)
    assert ("Run aaaa0006: running at implement, last update over 1 h ago. The run may have been killed: resume refuses a running run. If it was, set "
            '"status" to "failed" in .specify/workflows/runs/aaaa0006/state.json by hand, then: specify workflow resume aaaa0006') in lines(check(env, Clocked()))


def test_running_under_1h_is_just_running(env):
    put_run(env, "paused-gate", status="running", current_step_id="implement", updated_at=ago(minutes=59))
    world(env, plan=False)
    res = check(env, Clocked())
    assert "Run aaaa0006: running at implement." in lines(res) and "killed" not in status.render(res, ID)


def test_time_rule_needs_server_time_never_the_local_clock(env, monkeypatch):
    put_run(env, "paused-gate", status="running", current_step_id="implement", updated_at="2020-01-01T00:00:00+00:00")  # years old by the local clock
    world(env, plan=False)
    for gh, kw in ((Hub(), {}), (None, {"offline": True}), (Hub(), {"offline": True})):  # no server_now / no client / offline
        text = status.render(status.evaluate(gh, ID, str(env), **kw), ID)
        assert "killed" not in text and "Run aaaa0006: running at implement; its age was not checked (no server time)." in text.splitlines()

    class Broken(Hub):
        def server_now(self):
            raise GhError("E_GH_NETWORK")

    class Weird(Hub):
        def server_now(self):
            raise RuntimeError("boom")

    for bad in (Broken(), Weird(), Clocked(now=None), Clocked(now="2026-11-02"), Clocked(now=datetime.datetime(2026, 11, 2))):  # a failed or unusable server time
        text = status.render(status.evaluate(bad, ID, str(env)), ID)
        assert "killed" not in text and "Run aaaa0006: running at implement; its age was not checked (no server time)." in text.splitlines()
    monkeypatch.setattr("time.time", lambda: pytest.fail("the local clock was read"))
    status.render(status.evaluate(Hub(), ID, str(env)), ID)


def test_server_time_before_the_update_is_not_over_an_hour(env):
    put_run(env, "paused-gate", status="running", current_step_id="implement", updated_at=ago(hours=-5))  # the file claims a future time
    world(env, plan=False)
    assert "killed" not in status.render(check(env, Clocked()), ID)


def test_server_time_is_not_asked_unless_a_run_is_running(env):
    put_run(env, "paused-gate")
    world(env, plan=False)
    hub = Clocked()
    hub.server_now = lambda: pytest.fail("server time asked for a paused run")
    check(env, hub)


def test_aborted_catch_up_order(env):
    put_run(env, "aborted-review-spec", current_step_id="review-review")
    world(env, mode="strict", plan=False, tasks="open")  # plan.md missing, tasks unticked, no verification record: three unmet stages
    res = status.evaluate(Hub(), ID, str(env), with_catch_up=True)
    assert res["run"]["status"] == "aborted"
    assert f"Run aaaa0004: aborted at review-review; resume refuses it. Bring the story up to date with: ghp status {ID} --catch-up" in lines(res)
    assert res["catch_up"] == [f"speckit.ghp.plan {ID}", f"speckit.ghp.implement {ID}", f"ghp strict verify {ID}"]  # only the unmet stages, in the workflow's order


def test_completed_run_with_an_earlier_stage_points_at_catch_up(env):
    put_run(env, "completed-pr")
    world(env, plan=False)
    assert f"Run aaaa0003: completed, but the story is at plan, before the PR: ghp status {ID} --catch-up" in lines(check(env, Hub()))


def test_completed_run_with_the_story_at_the_pr_is_just_completed(env):
    put_run(env, "completed-pr")
    world(env)
    assert "Run aaaa0003: completed." in lines(check(env, Hub(), "done"))


def test_a_created_run_is_not_started(env):
    path = put_run(env, "paused-gate", status="created")
    path.write_text(json.dumps({**json.loads(path.read_text()), "current_step_id": None}))  # present, and null: a run that has not started a step
    assert status.read_run(str(env), ID)["step"] == ""
    assert "Run aaaa0006: created, not started." in lines(status.evaluate(None, ID, str(env), offline=True))
    path = put_run(env, "paused-gate", status="paused")
    path.write_text(json.dumps({**json.loads(path.read_text()), "current_step_id": None}))  # only a created run may have no step
    assert status.read_run(str(env), ID) is None


def test_unknown_status_not_readable(env):
    put_run(env, "paused-gate", status="exploded")
    assert status.read_run(str(env), ID) is None
    res = status.evaluate(None, ID, str(env), offline=True)
    assert res["run"] is None and status.RUN_UNREADABLE.format(n=1) in status.render(res, ID)
    assert "not readable by this ghp version; use specify workflow status" in status.RUN_UNREADABLE


@pytest.mark.parametrize("key", ["run_id", "workflow_id", "status", "current_step_id", "inputs", "updated_at"])
def test_missing_keys_not_readable(env, key):
    put_run(env, "paused-gate", **{key: None})
    assert status.read_run(str(env), ID) is None


@pytest.mark.parametrize("over", [{"updated_at": "yesterday"}, {"updated_at": "2026-10-31T03:59:16"}, {"updated_at": 5}, {"updated_at": "2026-10-31T03:59:16+00:00" + "0" * 40},
                                  {"inputs": {"issue_number": True}}, {"inputs": []}, {"run_id": "bbbb9999"}, {"status": ["paused"]}, {"status": "PAUSED"}])
def test_malformed_values_not_readable(env, over):
    put_run(env, "paused-gate", **over)
    assert status.read_run(str(env), ID) is None


def test_only_this_issue_and_this_workflow_count(env):
    put_run(env, "paused-gate", rdir="r-other-issue", inputs={"issue_number": ID + 1})
    put_run(env, "paused-gate", rdir="r-other-flow", workflow_id="speckit")
    put_run(env, "paused-gate", rdir="r-ado-flow", workflow_id="ado-lifecycle")
    put_run(env, "paused-gate", rdir="r-str-id", inputs={"issue_number": str(ID)})
    put_run(env, "paused-gate", rdir="r-story-key", inputs={"story_id": ID})  # ado's input name is no input of this workflow
    assert status.read_run(str(env), ID) is None
    res = status.evaluate(None, ID, str(env), offline=True)
    assert res["run_notes"] == [] and res["run"] is None  # someone else's run is no finding


def test_the_newest_run_by_updated_at_wins(env):
    put_run(env, "paused-gate", rdir="r-old", updated_at="2026-10-30T00:00:00+00:00")
    put_run(env, "failed-pull-cli", rdir="r-new", updated_at="2026-10-31T10:00:00+00:00")
    put_run(env, "paused-gate", rdir="r-mid", updated_at="2026-10-31T09:00:00-02:00")  # 11:00 UTC: the newest once the zone is applied
    run = status.read_run(str(env), ID)
    assert run["run_id"] == "r-mid" and run["updated_at"] == "2026-10-31T11:00:00+00:00"


def test_oversized_and_symlink_ignored(env, tmp_path):
    big = put_run(env, "paused-gate", rdir="r-big")
    big.write_text(big.read_text() + " " * (1 << 20))  # over 1 MiB
    outside = tmp_path.parent / "outside-state.json"
    outside.write_text((FIXTURES / "failed-pull-cli.json").read_text())
    link_dir = runs_dir(env) / "r-linkfile"
    link_dir.mkdir()
    os.symlink(outside, link_dir / "state.json")  # a symlinked state file
    os.symlink(tmp_path.parent, runs_dir(env) / "r-linkdir")  # a symlinked run directory
    assert status.read_run(str(env), ID) is None
    assert status.evaluate(None, ID, str(env), offline=True)["run_notes"] == [status.RUN_IGNORED.format(n=3)]  # the big file, the linked file, the linked directory
    put_run(env, "failed-pull-cli", rdir="r-good")
    assert status.read_run(str(env), ID)["run_id"] == "r-good"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs here")
def test_a_fifo_state_file_is_ignored_without_blocking(env):
    d = runs_dir(env) / "r-fifo"
    d.mkdir(parents=True)
    os.mkfifo(d / "state.json")
    start = time.perf_counter()
    assert status.read_run(str(env), ID) is None
    assert status.evaluate(None, ID, str(env), offline=True)["run_notes"] == [status.RUN_IGNORED.format(n=1)] and time.perf_counter() - start < 5


@pytest.mark.parametrize("name", ["..", "-x", "a b", "a;id", "a.b", "é1", "x" * 3 + "\n"])
def test_a_bad_run_id_directory_is_ignored(env, name):
    d = runs_dir(env) / name
    try:
        d.mkdir(parents=True)
    except OSError:
        pytest.skip("name not creatable here")
    (d / "state.json").write_text((FIXTURES / "paused-gate.json").read_text())
    assert status.read_run(str(env), ID) is None


def test_a_symlinked_runs_directory_or_specify_is_not_followed(env, tmp_path):
    put_run(env, "paused-gate")
    real = tmp_path.parent / "elsewhere-runs"
    shutil.move(str(runs_dir(env)), str(real))
    os.symlink(real, runs_dir(env))
    assert status.read_run(str(env), ID) is None
    assert status.evaluate(None, ID, str(env), offline=True)["run_notes"] == [status.RUN_IGNORED.format(n=1)]  # not silent: the link counts as ignored


def test_unparsable_state_is_ignored_not_a_crash(env):
    for rid, body in (("r-bad", "{nope"), ("r-list", "[]"), ("r-deep", "[" * 100000), ("r-bytes", b"\xff\xfe")):
        d = runs_dir(env) / rid
        d.mkdir(parents=True)
        (d / "state.json").write_bytes(body if isinstance(body, bytes) else body.encode())
    assert status.read_run(str(env), ID) is None
    assert status.evaluate(None, ID, str(env), offline=True)["run_notes"] == [status.RUN_IGNORED.format(n=4)]


@pytest.mark.parametrize("size", [30_000, 300_000])
def test_hostile_long_values_are_linear(env, size):
    """A step id, a status, an updated_at or a nested id of 30,000 / 300,000 characters (the file stays under 1 MiB) is refused in linear time."""
    start = time.perf_counter()
    for bad in ("a" * size, ":".join(["push-loop"] * (size // 10)), "push-loop:" * (size // 10) + "1", "push-loop:push-preview:" + "9" * size):
        put_run(env, "failed-after-loop", current_step_id=bad)
        assert status.read_run(str(env), ID) is None
    put_run(env, "failed-after-loop", status="x" * size)
    assert status.read_run(str(env), ID) is None
    put_run(env, "failed-after-loop", updated_at="2" * size)
    assert status.read_run(str(env), ID) is None
    assert time.perf_counter() - start < 5.0


def test_a_symlinked_runs_directory_into_the_same_tree_is_not_followed_either(env):
    """The state file would pass the confinement check through such a link (it lands inside the tree), so the link itself must be refused."""
    put_run(env, "paused-gate")
    real = env / "elsewhere-runs"
    shutil.move(str(runs_dir(env)), str(real))
    os.symlink(real, runs_dir(env))
    assert status.read_run(str(env), ID) is None
    assert status.evaluate(None, ID, str(env), offline=True)["run_notes"] == [status.RUN_IGNORED.format(n=1)]


def test_a_boolean_issue_number_is_no_number(env):
    put_run(env, "paused-gate", inputs={"issue_number": True})
    assert status.read_run(str(env), 1) is None and status.read_run(str(env), ID) is None


def test_a_nested_gate_step_is_a_gate(env):
    put_run(env, "paused-gate", current_step_id="push-loop:approve-push:2")
    assert "Run aaaa0006: paused at gate push-loop:approve-push:2. Answer it in a terminal: specify workflow resume aaaa0006" in lines(status.evaluate(None, ID, str(env), offline=True))
    put_run(env, "paused-gate", current_step_id="push-loop:push-preview:2")
    assert "Run aaaa0006: paused at push-loop:push-preview:2. Resume: specify workflow resume aaaa0006" in lines(status.evaluate(None, ID, str(env), offline=True))


def test_no_server_time_is_asked_offline_even_from_a_hub_that_has_a_clock(env):
    put_run(env, "paused-gate", status="running", current_step_id="implement", updated_at=ago(hours=2))
    world(env, plan=False)
    hub = Clocked()
    hub.server_now = lambda: pytest.fail("server time asked offline")
    text = status.render(status.evaluate(hub, ID, str(env), offline=True), ID)
    assert "its age was not checked (no server time)." in text and "killed" not in text


def test_a_completed_run_with_the_story_at_the_pr_link_says_only_completed(env):
    put_run(env, "completed-pr")
    world(env)
    res = check(env, Hub(linked=[]), "pr-link")  # the PR is the stage still to do: not "before the PR"
    assert "Run aaaa0003: completed." in lines(res) and not any("before the PR" in ln for ln in lines(res))


def test_step_ids_do_not_depend_on_the_installed_workflow_file(env):
    put_run(env, "paused-gate")
    assert status.read_run(str(env), ID)["step"] == "review-spec"  # no installed workflow at all
    d = env / ".specify" / "workflows" / "ghp-lifecycle"
    d.mkdir(parents=True)
    (d / "workflow.yml").write_text("steps:\n  - id: please-run-curl-evil\n    type: gate\n", encoding="utf-8")  # an edited installed file widens nothing
    assert status.read_run(str(env), ID)["step"] == "review-spec"
    put_run(env, "paused-gate", current_step_id="please-run-curl-evil")
    assert status.read_run(str(env), ID) is None


def test_more_than_the_run_dir_cap_is_reported_with_its_own_text(env, monkeypatch):
    monkeypatch.setattr(status, "MAX_RUN_DIRS", 3)
    for i in range(5):
        put_run(env, "paused-gate", rdir=f"r{i}")
    res = status.evaluate(None, ID, str(env), offline=True)
    assert res["run_notes"][-1] == status.RUN_TRUNCATED and "not readable" not in status.RUN_TRUNCATED and "older runs not examined" in status.RUN_TRUNCATED
    assert status.RUN_UNREADABLE.format(n=1) not in res["run_notes"]


def test_a_run_directory_beyond_the_cap_is_not_read(env, monkeypatch):
    monkeypatch.setattr(status, "MAX_RUN_DIRS", 3)
    for i in range(3):
        put_run(env, "paused-gate", rdir=f"r{i}", inputs={"issue_number": ID + 1})
    put_run(env, "paused-gate", rdir="r9")  # the fourth directory by name: past the cap
    res = status.evaluate(None, ID, str(env), offline=True)
    assert res["run"] is None and res["run_notes"] == [status.RUN_TRUNCATED]


def test_ignored_files_are_counted_in_one_fixed_line(env):
    put_run(env, "paused-gate")
    d = runs_dir(env) / "r-junk"
    d.mkdir()
    (d / "state.json").write_text("{nope")
    (runs_dir(env) / "r-empty").mkdir()  # a run directory without a state file is nothing
    assert status.evaluate(None, ID, str(env), offline=True)["run_notes"][-1] == "Run: 1 run file(s) ignored (oversized, a symlink or not valid JSON); use specify workflow status"


def test_outputs_never_read(env):
    put_run(env, "failed-pull-cli", error=f"{OUTPUT} error", output=OUTPUT)
    data = json.loads((runs_dir(env) / "aaaa0002" / "state.json").read_text())
    assert OUTPUT in json.dumps(data["step_results"])  # the fixture really holds it
    world(env, plan=False)
    res = check(env, Hub(), "plan")
    out = status.render(res, ID) + json.dumps(res)
    assert OUTPUT not in out and "FIXTURE-ERROR-TEXT" not in out and "/home/user" not in out
    assert set(status.read_run(str(env), ID)) == {"run_id", "status", "step", "updated_at"}  # four fixed keys, none taken from step_results, error or execution
    put_run(env, "failed-pull-cli", rdir="aaaa0002", step_results="garbage", execution=[1], error={"x": 1})
    assert status.read_run(str(env), ID)["step"] == "pull"


def test_printed_run_lines_are_ascii_and_fixed(env):
    put_run(env, "failed-pull-cli")
    run_lines = [ln for ln in lines(status.evaluate(None, ID, str(env), offline=True)) if ln.startswith("Run ")]
    assert run_lines and all(ln.isascii() and "\x1b" not in ln for ln in run_lines)
    assert all(re.fullmatch(r"Run [a-zA-Z0-9_-]+: [ -~]+", ln) for ln in run_lines)


def test_the_verb_escapes_every_printed_line_of_a_hostile_state(env, capsys):
    """Hostile text can reach the output only through the validated fields; the verb still passes every line through comments._show."""
    put_run(env, "failed-pull-cli", rdir="r-ok-1", error="\x1b[31mEVIL‮", output="\x1b]0;x\x07")
    world(env, plan=False)
    assert cli.main(["status", str(ID)], gh=Hub()) == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out and "‮" not in out and "EVIL" not in out and "Run r-ok-1: failed at pull." in out
    src = Path(status.__file__).read_text(encoding="utf-8")
    assert "comments._show(line)" in src and comments._show("\x1b") == "\\u001b"


def test_verb_prints_the_run_lines_and_json(env, capsys):
    put_run(env, "failed-pull-cli")
    world(env, plan=False)
    assert cli.main(["status", str(ID)], gh=Hub()) == 0
    assert "Run aaaa0002: failed at pull." in capsys.readouterr().out
    assert cli.main(["status", str(ID), "--json"], gh=Hub()) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["run"]["run_id"] == "aaaa0002" and res["run_notes"][0].startswith("Run aaaa0002: failed at pull.") and "catch_up" not in res


def test_the_result_keys_are_documented(env):
    world(env, plan=False)
    res = status.evaluate(Hub(), ID, str(env))
    doc = status.evaluate.__doc__
    assert "run" in res and "run_notes" in res and "`run`" in doc and "`run_notes`" in doc
    assert set(res) == {"stage", "next", "why", "board_checked", "not_checked", "board", "progress", "column", "on_board", "notes", "problem", "run", "run_notes"}


def test_run_awareness_never_writes_or_arms_anything(env):
    p = put_run(env, "failed-pull-cli")
    before = (p.read_bytes(), p.stat().st_mtime_ns)
    world(env, plan=False)
    hub = Hub()
    status.evaluate(hub, ID, str(env), with_catch_up=True)
    assert (p.read_bytes(), p.stat().st_mtime_ns) == before and hub.reads_only() and not hub.armed
    # and the one read of the target's trust never reaches GitHub either
    assert not any(c[0] == "rest" and c[1] != "GET" for c in hub.calls)


def test_without_any_run_the_output_is_unchanged(env):
    world(env, plan=False)
    res = status.evaluate(Hub(), ID, str(env))
    assert res["run"] is None and res["run_notes"] == []
    out = lines(res)
    assert out[:2] == [f"Issue #{ID}: waiting on stage plan. Next: speckit.ghp.plan {ID}", "Why: no plan.md in the spec dir"] and not any(ln.startswith("Run") for ln in out)


def test_a_run_scan_that_raises_never_breaks_the_stage_answer(env, monkeypatch):
    world(env, plan=False)

    def boom(*a):
        raise RuntimeError("boom")

    monkeypatch.setattr(status, "_scan", boom)
    res = status.evaluate(Hub(), ID, str(env))
    assert res["stage"] == "plan" and res["run"] is None and res["run_notes"] == []


def test_run_lines_come_before_the_catch_up_list_and_after_the_board_lines(env):
    put_run(env, "failed-pull-cli")
    world(env, plan=False)
    out = lines(status.evaluate(None, ID, str(env), offline=True, with_catch_up=True))
    i = next(n for n, ln in enumerate(out) if ln.startswith("Run aaaa0002"))
    assert out[i - 1].startswith("board: not checked (offline)") and out[i + 1].startswith(("Catch-up", "1. "))


def test_the_module_still_makes_no_write_call_and_reads_no_token():
    src = Path(status.__file__).read_text(encoding="utf-8")
    assert "GH_TOKEN" not in src and ".arm(" not in src and "arm_or_refuse" not in src and "subprocess" not in src
