"""T25: `ghp status` (ado's P4-4, ported): the first unmet stage of a story and the one command that moves it on.

Every scenario is a real temporary git repository with a ledger, a spec dir and commits (the gate, review and verification record are judged against
git) and a `Hub` (the stateful fake GitHub of the done tests: the story, its sub-issue tasks, one Project, comments, linked pull requests) that answers
the few reads status makes and fails the test on any write. Run awareness (`.specify` run state, resume notes) is tested in tests/test_status_run.py."""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

import ghp.__main__ as cli
from conftest import FakeGh, approve, commit, git, profile, review_md
from ghp import decisions, dod, projects, questions, rules, status
from ghp.config import Config, ConfigError
from ghp.gh import GhError, ScopeMissing, is_graphql_write
from test_done import DoneHub, ITEM, N, pull
from test_projects import fx
from test_push import STORY_URL, T
from rules_support import make_issue

ID = N  # the story is issue 11 in the done tests' hub
UNTRUSTED = "ZZ-GH-TEXT"
COMMAND = "PYTHONPATH=src python3 -m pytest -q"  # what the approved test profile composes
TASKS = {"ticked": "- [x] T001 Do it\n", "open": "- [ ] T001 Do it\n"}
_BODY = ["Customers earn points."]
RULE_HASH = rules.rule_hash(1, "approved", _BODY)
RULES_FILE = rules.render_file(make_issue(900, kind="Feature", title="Points"),
                               {"found": True, "rules": [{"id": "BR-900-1", "n": 1, "status": "approved", "body": _BODY, "hash": RULE_HASH}],
                                "duplicates": [], "problems": [], "notes": []}, "2026-11-03", "0.1.0")
NEXT = {  # the exact next command of every stage
    "ledger": f"speckit.ghp.pull {ID}", "questions-posted": f"ghp questions {ID} --post --plan-out .ghp/plan-questions.md",
    "questions-answered": f"speckit.ghp.plan {ID}", "plan": f"speckit.ghp.plan {ID}", "tasks": f"speckit.ghp.tasks {ID}",
    "board-tasks": f"ghp push {ID} --plan-out .ghp/plan-push.md", "tasks-ticked": f"speckit.ghp.implement {ID}",
    "board-sync": f"ghp push {ID} --plan-out .ghp/plan-sync.md",
    "decisions-posted": f"ghp decisions {ID} --post --if-any --plan-out .ghp/plan-decisions.md",
    "verified": f"ghp strict verify {ID}", "gate": f"speckit.ghp.gate {ID}", "review": f"speckit.ghp.review {ID}",
    "pr-link": f"speckit.ghp.pr {ID}", "merged": "git fetch origin", "done-state": f"speckit.ghp.done {ID}",
    "decisions-closed": f"ghp decisions {ID}",
}
BOARD_STAGES = {"open-at-done", "questions-posted", "board-tasks", "board-sync", "decisions-posted", "pr-link", "done-state"}


class Hub(DoneHub):
    """The story (closed as completed, at Done on the Project) with one ticked task as a sub-issue on the Project, a merged PR tied to it, and
    nothing else. `down`: an exception every call raises (no network); `no_scope`: every Project read fails for want of the scope. Any write
    fails the test; `subs` is the sub-issue summary."""

    def __init__(self, tasks=(("T001", "Do it", "Done"),), state="closed", status="Done", **kw):
        super().__init__(state=state, status=status, **kw)
        self.down, self.no_scope = None, False
        for i, (tid, title, st) in enumerate(tasks):
            self.seed(tid, 50 + i, f"{tid} {title}", parent=STORY_URL.replace("/1", f"/{ID}"), story=ID, on_board=st is not None, status=st)
        self.subs = (len(tasks), len(tasks))

    def rest(self, method, path, body=None):
        if self.down:
            self.calls.append(("rest", method, path, body))
            raise self.down
        return super().rest(method, path, body)

    def graphql(self, query, variables=None):
        if self.down:
            self.calls.append(("graphql", query, variables))
            raise self.down
        if self.no_scope and any(k in query for k in ("fields(", "projectItems(", "fieldValueByName", "workflows(")):
            self.calls.append(("graphql", query, variables))
            raise ScopeMissing([])
        if "subIssuesSummary" in query and "closedByPullRequestsReferences" not in query:
            self._gate(False)
            self.calls.append(("graphql", query, variables))
            return {"node": {"subIssuesSummary": {"total": self.subs[0], "completed": self.subs[1]}}}
        return super().graphql(query, variables)

    def reads_only(self):
        return (not self.armed and self.sent() == []
                and all(c[1] == "GET" for c in self.calls if c[0] == "rest")
                and not any(is_graphql_write(c[1]) for c in self.calls if c[0] == "graphql"))


def questions_comment(env, *, open_texts=None):
    """The comment `ghp questions --post` would write for the ledger as it is now (or for other question texts), as the viewer (1001) wrote it."""
    if open_texts is None:
        parsed = questions.parse_questions((env / "dod" / f"DoD_{ID}.md").read_text())
        return questions.with_marker(questions.comment_text(parsed["open"], questions._assumed(parsed["answered"])))
    return questions.with_marker(questions.comment_text([{"id": f"Q{i}", "question": t, "owner": "PO"} for i, t in enumerate(open_texts, 1)]))


def ledger_text(sha, *, spec_dir="specs/001-x", qlines="- [x] Q1: what? → yes · owner: PO\n", gate="ok", review="ok", record="", header="",
                red="aaaaaaa", green="bbbbbbb", old="ccccccc"):
    blocks = ""
    if gate:
        blocks += (f"### Gate — 2026-10-28 — `main...HEAD` @ `{old if gate == 'stale' else sha}`\n- traceability: ok\n"
                   f"- blocking: {'yes' if gate == 'blocking' else 'no'}\n\n")
    if review:
        blocks += review_md(old if review == "stale" else sha, blocks="yes" if review == "blocks" else "no")
    blocks += record
    return (f"# DoD — #{ID}: title\n\n- **Spec dir:** {spec_dir}\n{header}\n## 1. Seeded from GitHub\n\n### Acceptance Criteria\nit works\n\n"
            f"{dod.SECTION2}\n- **Approach:** a\n- **Open questions → answers:**\n{qlines}\n"
            "## 3. AC → tests\n\n| AC | acceptance criterion | tests | commit/PR | status |\n|---|---|---|---|---|\n"
            f"| AC-1 | it works | tests/test_a.py::test_a | {green} | red {red} → green {green} |\n\n"
            f"## 4. Gate & review log\n{blocks}\n## 5. Definition of Done\n- [x] a\n")


def record_block(head, red, green, command):
    import hashlib
    return (f"### Verification — 2026-10-30 @ {head[:12]}\n- AC-1: red {red[:12]} exit 1 · green {green[:12]} exit 0 · L1 ok\n"
            f"- head: {head[:12]} exit 0\n- command-sha256: {hashlib.sha256(command.encode()).hexdigest()[:12]}\n- seconds: 1.0\n\n")


@pytest.fixture
def env(tmp_path, monkeypatch, git_env):
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("ghp.stack.detect", lambda root: {"outcome": "unknown", "stacks": [], "domain_hints": []})
    return tmp_path


def world(repo, *, mode="advisory", cfg=None, questions="answered", plan=True, tasks="ticked", spec=True, decisions_md=None, gate="ok", review="ok",
          record=False, merged=True, ledger=True, header="", stack_profile="approved", early_ledger=False, project=3):
    """A complete story by default (every stage met); each keyword breaks one thing. Returns the Config."""
    git(repo, "init", "-q", "-b", "main")
    init = commit(repo, "chore: init", {"README.md": "x\n"})
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    if early_ledger:  # the ledger was pulled on the default branch before the work started
        commit(repo, f"docs: pull #{ID}", {f"dod/DoD_{ID}.md": ledger_text("abc1234", gate=None, review=None)})
        git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "checkout", "-q", "-b", f"feat/gh{ID}-x")
    command = COMMAND
    data = {"owner": "octo", "repo": "demo", **({"project_number": project} if project else {}),
            **({"mode": "strict", "strict": {"test_command": command}} if mode == "strict" else {}), **(cfg or {})}
    files = {"ghp.config.json": json.dumps(data)}
    if spec:
        files["specs/001-x/spec.md"] = "# spec\n"
        if plan:
            files["specs/001-x/plan.md"] = "# plan\n"
        if tasks:
            files["specs/001-x/tasks.md"] = TASKS[tasks]
    if decisions_md is not None:
        files[f"dod/Decisions_{ID}.md"] = decisions_md
    if mode == "strict" and stack_profile != "none":
        files["ghp.stack.json"] = json.dumps(approve(profile()) if stack_profile == "approved" else profile())
    red = commit(repo, f"test: red #{ID}", {"tests/test_a.py": "def test_a():\n    assert 0\n", **files})
    green = commit(repo, f"feat: green #{ID}", {"src/app.py": "x = 1\n"})
    head = git(repo, "rev-parse", "HEAD")
    if ledger:
        rec = record_block(head, git(repo, "rev-parse", red), git(repo, "rev-parse", green), command) if record else ""
        qlines = {"answered": "- [x] Q1: what? → yes · owner: PO\n", "open": "- [ ] Q1: what? → _(open)_ · owner: PO\n", "none": ""}[questions]
        commit(repo, f"docs: ledger #{ID}", {f"dod/DoD_{ID}.md": ledger_text(green, qlines=qlines, gate=gate, review=review, record=rec, header=header,
                                                                            red=red, green=green, old=init)})
    if merged:
        git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return Config.load(str(repo))


def world_again(env, **kw):
    """A second world in the same directory: start over."""
    for child in env.iterdir():
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    return world(env, **kw)


def check(repo, hub=None, stage=None, why=None, nxt=None, **kw):
    try:
        cfg = Config.load(str(repo))
    except ConfigError:
        cfg = None
    res = status.evaluate(hub if hub is not None else (Hub() if cfg else None), ID, str(repo), **kw)
    if stage is not None:
        assert res["stage"] == stage, res
        if nxt or stage in NEXT:
            assert res["next"] == (nxt or NEXT[stage]), res
    if why is not None:
        assert why in res["why"], res
    return res


# ---- AC1: the ported stage logic, with the mocked tracker ----
def test_a_complete_story_is_done(env):
    world(env)
    hub = Hub()
    res = check(env, hub, "done")
    assert res["next"] == "none" and res["board_checked"] is True and res["not_checked"] == [] and res["problem"] is None
    assert hub.reads_only()


def test_stages_are_data_in_the_documented_order():
    ids = [s[0] for s in status.STAGES]
    assert ids == ["config", "open-at-done", "ledger", "stack-profile", "rules", "questions-posted", "questions-answered", "plan", "tasks", "board-tasks",
                   "tasks-ticked", "board-sync", "decisions-posted", "verified", "gate", "review", "pr-link", "merged", "done-state",
                   "decisions-closed", "done"]
    assert all(len(s) == 4 and isinstance(s[1], bool) and callable(s[2]) and callable(s[3]) for s in status.STAGES)
    assert {s[0] for s in status.STAGES if s[1]} == BOARD_STAGES
    assert set(status.PROJECT_STAGES) <= BOARD_STAGES


# ---- one test per stage ----
def test_stage_config(env):
    check(env, None, "config", "no ghp.config.json")
    assert check(env)["next"] == "ghp setup"
    (env / "ghp.config.json").write_text("{not json")
    res = check(env, None, "config", "invalid")
    assert res["next"] == "ghp doctor" and res["problem"] == "config-invalid"
    assert "E_ORG" not in res["why"]
    (env / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", "project_owner_type": "organization"}))
    res = check(env, None, "config", "E_ORG_PROJECT_UNSUPPORTED")  # named like every other verb names it
    assert res["problem"] == "config-invalid"


def test_stage_ledger(env):
    world(env, ledger=False)
    check(env, None, "ledger")
    # a symlinked ledger is never read: fail closed
    (env / "dod").mkdir(exist_ok=True)
    (env / "outside.md").write_text(ledger_text("abc1234"))
    os.symlink(env / "outside.md", env / "dod" / f"DoD_{ID}.md")
    check(env, None, "ledger")


def test_stage_stack_profile(env):
    world(env, mode="strict", stack_profile="none")
    check(env, Hub(), "stack-profile")
    assert check(env)["next"] == "speckit.ghp.stack"
    world_again(env, mode="strict", stack_profile="draft")
    assert check(env, Hub(), "stack-profile")["next"] == "ghp stack approve"


def test_a_complete_strict_story_is_done(env):
    world(env, mode="strict", record=True)
    check(env, Hub(), "done")


def test_stage_rules(env):
    world(env, cfg={"rules": {"enabled": True}})  # a ledger with no `Rules from:` line
    res = check(env, Hub(), "rules")
    assert res["next"] == f"ghp dod {ID} --refresh"
    p = env / "dod" / f"DoD_{ID}.md"
    p.write_text(p.read_text().replace("- **Spec dir:** specs/001-x\n", "- **Spec dir:** specs/001-x\n- **Rules from:** 900\n"))
    assert check(env, Hub(), "rules")["next"] == f"ghp rules {ID}"  # the file Rules_900.md is not there
    (env / "dod" / "Rules_900.md").write_text(RULES_FILE)
    assert check(env, Hub(), "rules")["next"] == f"ghp rules {ID} --confirm"  # first confirm: no hash recorded yet
    p.write_text(p.read_text().replace("- **Rules from:** 900\n", f"- **Rules from:** 900\n- **Rules reviewed:** BR-900-1@{RULE_HASH}\n"))
    assert check(env, Hub())["stage"] != "rules"
    p.write_text(p.read_text().replace(RULE_HASH, "deadbeef"))
    res = check(env, Hub(), "rules")  # a recorded hash that is not the rule's: a human re-confirms
    assert res["next"] == f"ghp rules {ID} --confirm" and res["why"] == "1 rule(s) differ from the hash recorded in `Rules reviewed:`: a human re-confirms them in their own terminal"


def test_rules_unconfirmed_is_free_changed_is_a_humans(env):
    world(env, cfg={"rules": {"enabled": True}}, header="- **Rules from:** 900\n")
    (env / "dod" / "Rules_900.md").write_text(RULES_FILE)
    res = check(env, Hub(), "rules", nxt=f"ghp rules {ID} --confirm")
    assert res["why"] == "1 in-scope rule(s) are not confirmed yet: run the confirm yourself, no terminal is needed"


def test_rules_stage_only_when_enabled(env):
    cfg = world(env)  # rules off: no Rules lines, and the stage is not even looked at
    assert check(env, Hub(), "done")["stage"] == "done"
    assert "rules" not in status.applicable(cfg)
    assert "rules" in status.applicable(Config("octo", "demo", rules={"enabled": True}))


def test_strict_only_stages_skipped_in_advisory(env):
    cfg = world(env, gate="ok")  # advisory: no profile, no verification record, open decision lines
    (env / "dod" / f"Decisions_{ID}.md").write_text("- [x] D1: a → rejected · by: PO · note: no\n")
    assert check(env, Hub(), "done")["stage"] == "done"
    ids = status.applicable(cfg)
    assert not {"stack-profile", "verified", "decisions-closed"} & set(ids)
    strict_ids = status.applicable(Config("octo", "demo", mode="strict"))
    assert {"stack-profile", "verified", "decisions-closed"} <= set(strict_ids)


def test_stage_questions_posted(env):
    world(env, questions="open")
    check(env, Hub(), "questions-posted")
    # a posted comment that no longer matches section 2 is out of date too
    hub = Hub()
    hub.post(questions_comment(env, open_texts=["something else"]))
    check(env, hub, "questions-posted")


def test_stage_questions_answered(env):
    world(env, questions="open")
    hub = Hub()
    hub.post(questions_comment(env))
    res = check(env, hub, "questions-answered", "1 open question")
    assert res["board_checked"] is True


def test_stage_plan(env):
    world(env, plan=False)
    check(env, Hub(), "plan")


def test_stage_tasks(env):
    world(env, tasks=None)
    check(env, Hub(), "tasks")
    (env / "specs/001-x/tasks.md").write_text("no tasks here\n")
    check(env, Hub(), "tasks")
    (env / "specs/001-x/tasks.md").unlink()  # a symlinked tasks.md is never read
    (env / "elsewhere.md").write_text(TASKS["ticked"])
    os.symlink(env / "elsewhere.md", env / "specs/001-x/tasks.md")
    check(env, Hub(), "tasks")


def test_stage_board_tasks(env):
    world(env)
    check(env, Hub(tasks=()), "board-tasks", "1 task")
    (env / "specs/001-x/tasks.md").write_text("- [x] T001 a\n- [x] T001 b\n")
    check(env, Hub(), "board-tasks", "repeats")


def test_board_tasks_when_push_refuses_the_tasks_names_the_code_only(env):
    world(env)
    (env / "specs/001-x/tasks.md").write_text("- [x] T١٢٣ a\n")  # Arabic-Indic digits: tasks.md parses it, push refuses it
    res = check(env, Hub(), "board-tasks")
    assert "E_BAD_TASK_ID" in res["why"] and res["board"] is None and res["board_checked"] is True


def test_stage_tasks_ticked(env):
    world(env, tasks="open")
    check(env, Hub(tasks=(("T001", "Do it", "Todo"),)), "tasks-ticked", "1 unticked")


def test_stage_board_sync(env):
    world(env)  # the sub-issue was retitled by a person: push would write the title back
    hub = Hub()
    hub.issues[50]["title"] = "T001 something else"
    check(env, hub, "board-sync", "1 task")
    hub = Hub(tasks=(("T001", "Do it", None),))  # the sub-issue is not on the Project
    check(env, hub, "board-sync", "1 task")


DECISION = "- [ ] D1: use sqlite → _(open)_ · kind: decision · why: simple"
POSTED = f"{DECISION} · posted: 9:{decisions.line_hash('use sqlite')}\n"


def decisions_comment(hub, cid=9):
    hub.next_comment = cid - 1
    return hub.post(f"{decisions.MARKER}\nD1 (decision): use sqlite")


def test_stage_decisions_posted(env):
    world(env, decisions_md=DECISION + "\n")
    check(env, Hub(), "decisions-posted", "1 decision")
    # a marker the file carries must match a decisions comment that is really on the issue
    (env / "dod" / f"Decisions_{ID}.md").write_text(POSTED)
    check(env, Hub(), "decisions-posted", "not on the issue")
    hub = Hub()
    hub.next_comment = 8
    hub.post("an unrelated comment")
    check(env, hub, "decisions-posted", "not on the issue")
    hub = Hub()
    decisions_comment(hub)
    assert check(env, hub)["stage"] != "decisions-posted"


def test_stage_verified(env):
    world(env, mode="strict", record=False)
    res = check(env, Hub(), "verified", "no verification record")
    assert res["next"] == f"ghp strict verify {ID}"


def test_stage_verified_stale(env):
    world(env, mode="strict", record=True, merged=False)
    commit(env, f"feat: more #{ID}", {"src/more.py": "y = 2\n"})  # code after the record
    check(env, Hub(), "verified", "stale")


def test_stage_gate(env):
    world(env, gate=None)
    check(env, Hub(), "gate", "no gate result")


def test_stage_gate_stale(env):
    world(env, gate="stale", merged=False)
    check(env, Hub(), "gate", "stale")


def test_stage_gate_blocking(env):
    world(env, gate="blocking")
    check(env, Hub(), "gate", "blocking")


def test_stage_review_missing(env):
    world(env, review=None)
    check(env, Hub(), "review", "no review")


def test_stage_review_blocks(env):
    world(env, review="blocks")
    check(env, Hub(), "review", "blocks")


def test_stage_review_stale(env):
    world(env, review="stale", merged=False)
    check(env, Hub(), "review", "stale")


def test_stage_pr_link(env):
    world(env)
    check(env, Hub(linked=[]), "pr-link")
    check(env, Hub(linked=[pull(repo="octo/other")]), "pr-link")  # a pull request of another repository says nothing about this one (D7)
    check(env, Hub(linked=[pull(repo=None)]), "pr-link")


def test_pr_link_detected_from_a_linked_pull_request_of_this_repository(env):
    world(env)
    for linked in ([pull()], [pull(merged=False, oid=None)], [pull(repo="OCTO/Demo")], [pull(repo="octo/other"), pull(number=41)]):
        assert check(env, Hub(linked=linked))["stage"] == "done"


def test_stage_merged(env):
    world(env, merged=False)
    check(env, Hub(), "merged")
    # the PR is still open: a fetch cannot help yet, the merge comes first (a person merges it)
    check(env, Hub(linked=[pull(merged=False, oid=None)]), "merged", nxt="a person merges PR #40 on GitHub, then: git fetch origin")
    check(env, None, "merged", offline=True)  # GitHub not read: the fetch, as before
    git(env, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")  # nothing to compare with: not met, never guessed
    check(env, Hub(), "merged", "origin/HEAD", nxt="git remote set-head origin --auto")


def test_an_earlier_commit_naming_the_story_is_not_a_merge(env):
    # the ledger was pulled on main (a commit naming the story) and the PR is not merged: the default branch lacks the review/gate headings
    world(env, merged=False, early_ledger=True)
    assert f"#{ID}" in git(env, "log", "--format=%s", "origin/main")
    check(env, Hub(), "merged")
    # a sibling commit on main naming a longer number changes nothing either
    git(env, "update-ref", "refs/remotes/origin/main", git(env, "commit-tree", "-m", f"feat: other #{ID}2", "-p", "origin/main", "origin/main^{tree}"))
    check(env, Hub(), "merged")


@pytest.mark.parametrize("how", ["ff", "merge", "squash"])
@pytest.mark.parametrize("mode", ["advisory", "strict"])
def test_merged_story_is_done_on_main_however_it_was_merged(env, how, mode):
    # after a merge the user checks out main; a squash or rebase leaves the recorded SHAs unreachable, which must not re-open gate, review, verified
    world(env, mode=mode, record=mode == "strict", merged=False)
    git(env, "checkout", "-q", "main")
    if how == "squash":
        git(env, "merge", "--squash", f"feat/gh{ID}-x")
        git(env, "commit", "-q", "-m", f"Add feature #{ID} (#5)")
    else:
        git(env, "merge", "-q", *(["--ff-only"] if how == "ff" else ["--no-ff", "-m", f"Merge pull request #{ID}"]), f"feat/gh{ID}-x")
    git(env, "update-ref", "refs/remotes/origin/main", "HEAD")
    check(env, Hub(), "done")


def test_squash_merge_without_a_fetch_is_not_merged_yet(env):
    world(env, merged=False)
    git(env, "checkout", "-q", "main")
    git(env, "merge", "--squash", f"feat/gh{ID}-x")
    git(env, "commit", "-q", "-m", f"Add feature #{ID} (#5)")  # origin/main still at the first commit
    res = check(env, Hub())
    assert res["stage"] == "gate", res  # not on the default branch as far as git knows, so the old SHAs are judged as they are


def test_stage_done_state(env):
    world(env)
    check(env, Hub(state="open", status="In Progress"), "done-state", "still open")
    hub = Hub()
    hub.issues[ID]["state_reason"] = "not_planned"
    check(env, hub, "done-state", "not as completed")
    check(env, Hub(status="In Progress"), "done-state", "done Status")  # closed, but the board card was not moved
    hub = Hub()
    hub.status.pop(ITEM)  # an item with no Status at all
    check(env, hub, "done-state", "done Status")


def test_done_state_with_no_project_or_an_item_off_the_board_needs_only_the_close(env):
    world(env, project=None)
    check(env, Hub(), "done")
    world_again(env)
    check(env, Hub(on_board=False), "done")  # `ghp done` adds nothing to the Project, so there is nothing to wait for


def test_stage_decisions_closed(env):
    world(env, mode="strict", record=True, decisions_md=POSTED)
    hub = Hub()
    decisions_comment(hub)
    res = check(env, hub, "decisions-closed")
    assert "1 decision" in res["why"]


def test_done_state_next_in_strict_points_at_open_decisions(env):
    # `ghp done` refuses while a decision is open, so the next command is the one that closes them
    world(env, mode="strict", record=True, decisions_md=POSTED)
    hub = Hub(state="open", status="In Progress")
    decisions_comment(hub)
    check(env, hub, "done-state", nxt=f"ghp decisions {ID}")


def test_stage_done(env):
    world(env)
    res = check(env, Hub())
    assert res["stage"] == "done" and res["next"] == "none"


# ---- offline, network errors, read only, no GitHub text ----
def test_offline_skips_board_stages_says_not_checked(env):
    world(env, questions="open")
    hub = Hub()
    res = check(env, hub, "questions-answered", offline=True)  # the local stage after the skipped questions-posted
    assert hub.calls == [] and res["board_checked"] is False and res["not_checked"][:2] == ["open-at-done", "questions-posted"] and res["board"] == "offline"


def test_offline_with_everything_local_met_is_never_done(env):
    world(env)
    hub = Hub()
    res = status.evaluate(hub, ID, str(env), offline=True)
    assert res["stage"] == "board-not-checked" and res["board_checked"] is False and hub.calls == []
    assert res["next"] == f"ghp status {ID}" and set(res["not_checked"]) == BOARD_STAGES


@pytest.mark.parametrize("fail", [GhError("E_GH_HTTP", 500, f"HTTP 500 {UNTRUSTED}"), GhError("E_GH_NETWORK", None, "down"), ValueError("x")])
def test_network_error_never_reports_board_met(env, fail):
    world(env)
    hub = Hub()
    hub.down = fail
    res = status.evaluate(hub, ID, str(env))
    assert res["stage"] == "board-not-checked" and res["board_checked"] is False and "questions-posted" in res["not_checked"] and res["board"] == "error"
    assert res["next"] == "ghp doctor" and len(hub.calls) == 1  # one failure ends the reads; the other board stages are not tried
    assert UNTRUSTED not in json.dumps(res)


def test_network_error_still_reports_an_earlier_local_stage(env):
    world(env, plan=False, questions="none")
    hub = Hub()
    hub.down = GhError("E_GH_NETWORK", None, "down")
    res = status.evaluate(hub, ID, str(env))
    assert res["stage"] == "plan" and res["board_checked"] is False and res["not_checked"] == ["open-at-done", "questions-posted"]
    assert status.render(res, ID).splitlines()[-1] == "board: not checked (GitHub could not be read): open-at-done, questions-posted"


def test_no_client_is_not_checked(env):
    world(env)
    res = status.evaluate(None, ID, str(env))
    assert res["stage"] == "board-not-checked"


def test_status_never_writes(env):
    world(env, questions="open", tasks="open")
    hub = Hub(tasks=(("T001", "Do it", "Todo"),))
    for kw in ({}, {"offline": True}, {"with_catch_up": True}):
        status.evaluate(hub, ID, str(env), **kw)
    assert hub.calls and hub.reads_only() and hub.writes == [] and hub.armed is False


def test_status_through_the_verb_arms_nothing_and_every_call_is_a_read(env, capsys):
    world(env, questions="open", tasks="open")
    hub = Hub(tasks=(("T001", "Do it", None),))
    for flags in ([], ["--json"], ["--catch-up"]):
        assert cli.main(["status", str(ID), *flags], gh=hub) == 0
    capsys.readouterr()
    assert hub.calls and hub.reads_only() and hub.armed is False and hub.writes == []


def test_no_github_text_in_output(env, capsys):
    # the issue title, comment text and sub-issue titles carry a marker; so do the question and the failing request's message
    world(env, questions="open")
    p = env / "dod" / f"DoD_{ID}.md"
    p.write_text(p.read_text().replace("what?", f"{UNTRUSTED}-question?"))
    hub = Hub()
    hub.issues[ID]["title"] = UNTRUSTED
    hub.issues[50]["title"] = f"T001 {UNTRUSTED}"
    hub.post(questions_comment(env, open_texts=[f"{UNTRUSTED}-question?"]) + UNTRUSTED)
    failing = Hub()
    failing.down = GhError("E_GH_HTTP", 500, UNTRUSTED)
    for gh in (hub, failing):
        for flags in ([], ["--json"], ["--offline"], ["--catch-up"]):
            cli.main(["status", str(ID), *flags], gh=gh)
            cap = capsys.readouterr()
            assert UNTRUSTED not in cap.out + cap.err, flags


# ---- AC2: child progress from subIssuesSummary, the board column from the Status option name ----
def test_child_progress_and_board_column(env, capsys):
    world(env)
    hub = Hub(status="In Progress")
    hub.subs = (5, 2)
    res = status.evaluate(hub, ID, str(env))
    assert res["progress"] == {"total": 5, "completed": 2} and res["column"] == "In Progress"
    # the counts come from one GraphQL query for subIssuesSummary (not from the REST summary the story was read with)
    queries = [c for c in hub.calls if c[0] == "graphql" and c[1] == status.QUERIES["progress"]]
    assert len(queries) == 1 and queries[0][2] == {"issue": hub.issues[ID]["node_id"]}
    assert "subIssuesSummary { total completed }" in status.QUERIES["progress"]
    text = status.render(res, ID)
    assert "Sub-issues: 2 of 5 closed" in text.splitlines() and "Board column: In Progress" in text.splitlines()
    assert cli.main(["status", str(ID), "--json"], gh=hub) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["progress"] == {"total": 5, "completed": 2} and out["column"] == "In Progress"
    hub.status[ITEM] = "Done"  # the column is the NAME of the option the item is at now
    assert status.evaluate(hub, ID, str(env))["column"] == "Done"


def test_no_progress_or_column_when_nothing_to_show(env):
    world(env, project=None)  # no Project: no column
    res = status.evaluate(Hub(), ID, str(env))
    assert res["column"] is None and res["progress"] == {"total": 1, "completed": 1}
    assert not any(line.startswith("Board column") for line in status.render(res, ID).splitlines())
    world_again(env)
    res = status.evaluate(Hub(on_board=False), ID, str(env))  # not on the Project: no column
    assert res["column"] is None
    res = status.evaluate(None, ID, str(env), offline=True)
    assert res["progress"] is None and res["column"] is None


@pytest.mark.parametrize("summary", [None, [], {}, {"total": 3}, {"total": "3", "completed": 1}, {"total": 3, "completed": True}, {"total": 1, "completed": 2},
                                     {"total": -1, "completed": -2}, {"total": 2 ** 70, "completed": 1.5}])
def _lying_progress(hub, answer):
    real = hub.graphql

    def lying(query, variables=None):
        if query == status.QUERIES["progress"]:
            hub.calls.append(("graphql", query, variables))
            if isinstance(answer, BaseException):
                raise answer
            return {"node": {"subIssuesSummary": answer}}
        return real(query, variables)
    hub.graphql = lying
    return hub


@pytest.mark.parametrize("summary", [None, [], {}, {"total": 3}, {"total": "3", "completed": 1}, {"total": 3, "completed": True}, {"total": 1, "completed": 2},
                                     {"total": -1, "completed": -2}, {"total": 2 ** 70, "completed": 1.5},
                                     GhError("E_GH_HTTP", 500, UNTRUSTED), GhError("E_GH_NETWORK", None, "down"), ValueError(UNTRUSTED)])
def test_a_failed_or_malformed_summary_blanks_only_the_count_line(env, summary, capsys):
    """M2: the count is display-only: no number is shown, but no stage is degraded (the board stays checked, the answer stays `done`)."""
    world(env)
    hub = _lying_progress(Hub(), summary)
    res = status.evaluate(hub, ID, str(env))
    assert res["progress"] is None and res["notes"] == ["Sub-issues: not shown (unreadable)"]
    assert res["board"] is None and res["board_checked"] is True and res["not_checked"] == [] and res["stage"] == "done" and res["column"] == "Done"
    assert cli.main(["status", str(ID)], gh=hub) == 0
    out = capsys.readouterr().out
    assert "Sub-issues: not shown (unreadable)" in out.splitlines() and "board: not checked" not in out and UNTRUSTED not in out
    assert not any(line.startswith("Sub-issues:") and "closed" in line for line in out.splitlines())


def test_an_unexpected_exception_in_the_count_shows_its_class_only_and_goes_on(env):
    """M3: nothing escapes `evaluate` as a traceback."""
    world(env)
    res = status.evaluate(_lying_progress(Hub(), RuntimeError(UNTRUSTED)), ID, str(env))
    assert res["notes"] == ["Sub-issues: not shown (RuntimeError)"] and res["stage"] == "done" and res["board_checked"] is True
    assert UNTRUSTED not in json.dumps(res)


def test_an_unexpected_exception_in_the_column_read_shows_its_class_only_and_fails_closed(env, monkeypatch, capsys):
    world(env)

    def boom(gh, item):
        raise IndexError(UNTRUSTED)
    monkeypatch.setattr(projects, "_status_of", boom)
    hub = Hub()
    res = status.evaluate(hub, ID, str(env))
    assert res["notes"] == ["Board column: not shown (IndexError)"] and res["column"] is None
    assert res["board"] == "error" and res["board_checked"] is False and res["stage"] == "board-not-checked"  # never `done`
    assert cli.main(["status", str(ID)], gh=hub) == 0
    cap = capsys.readouterr()
    assert UNTRUSTED not in cap.out + cap.err and "Board column: not shown (IndexError)" in cap.out.splitlines()


def test_the_column_name_is_escaped_for_display(env, monkeypatch):
    world(env)
    monkeypatch.setattr(projects, "_status_of", lambda gh, item: ("In\x1b[31m Progress‮", "x"))
    res = status.evaluate(Hub(), ID, str(env))
    assert res["column"] == "In\\u001b[31m Progress\\u202e"
    text = status.render(res, ID)
    assert "\x1b" not in text and "‮" not in text


# ---- AC3: no network or no `project` scope: board stages degrade to a named note, local stages still print ----
def test_status_degrades_without_project_scope(env, capsys):
    world(env)
    hub = Hub()
    hub.no_scope = True
    res = status.evaluate(hub, ID, str(env))
    # the Project stages are not checked; the issue-level board stages (questions, decisions, PR link) still were
    assert res["board"] == "scope" and res["board_checked"] is False and res["stage"] == "board-not-checked"
    assert res["not_checked"] == ["open-at-done", "board-tasks", "board-sync", "done-state"] and res["why"] == status.SCOPE_FIX
    assert res["column"] is None and res["progress"] == {"total": 1, "completed": 1}
    assert hub.reads_only()
    assert cli.main(["status", str(ID)], gh=hub) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"Issue #{ID}: board not checked. Next: ghp doctor"
    assert "board: not checked (no project scope): open-at-done, board-tasks, board-sync, done-state" in out
    # a local stage that is unmet is still the answer, with the note beside it
    world_again(env, plan=False)
    hub.calls.clear()
    assert cli.main(["status", str(ID)], gh=hub) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"Issue #{ID}: waiting on stage plan. Next: speckit.ghp.plan {ID}" and out[-1] == "board: not checked (no project scope): open-at-done"


def test_without_the_project_scope_an_issue_level_board_stage_is_still_judged(env):
    world(env, questions="open")
    hub = Hub()
    hub.no_scope = True
    res = status.evaluate(hub, ID, str(env))
    assert res["stage"] == "questions-posted" and res["board"] == "scope"  # read with the repo scope alone


def test_without_a_project_configured_the_missing_scope_is_never_met(env):
    world(env, project=None)
    hub = Hub()
    hub.no_scope = True
    res = status.evaluate(hub, ID, str(env))
    assert res["stage"] == "done" and res["board"] is None and res["board_checked"] is True  # nothing in this setup needs the scope


def test_a_scope_error_elsewhere_is_a_plain_board_error(env):
    world(env)
    hub = Hub()
    real = hub.rest

    seen = []

    def sso(method, path, body=None):
        if path.endswith(f"issues/{ID}"):
            seen.append(path)
            raise ScopeMissing(["repo"])
        return real(method, path, body)
    hub.rest = sso
    res = status.evaluate(hub, ID, str(env))
    assert res["board"] == "error" and len(seen) == 1  # only a Project read can say "no project scope"; the failure ends the reads at once


def test_a_stage_that_crashes_names_the_exception_class_only(env, monkeypatch):
    world(env)

    def boom(c):
        raise RuntimeError(f"{UNTRUSTED} \x1b[31m")
    monkeypatch.setitem(status._STAGE, "plan", ("plan", False, boom, lambda c: "x"))
    res = status.evaluate(Hub(), ID, str(env))
    assert res["stage"] == "plan" and res["why"] == "this stage could not be checked (RuntimeError)" and UNTRUSTED not in json.dumps(res)


# ---- M4: a Project that does not match the config is not "GitHub could not be read" ----
@pytest.mark.parametrize("cfg", [{"status_done": "Complete"}, {"status_active": "Doing"}, {"status_todo": "Backlog"}])
def test_a_status_option_missing_from_the_project_is_a_config_note(env, cfg, capsys):
    world(env, cfg=cfg)
    hub = Hub()
    res = status.evaluate(hub, ID, str(env))
    assert res["board"] == "config" and res["stage"] == "board-not-checked" and res["next"] == "ghp doctor" and res["why"] == status.CONFIG_FIX
    assert res["not_checked"] == ["open-at-done", "board-tasks", "board-sync", "done-state"] and res["board_checked"] is False  # the issue-level stages were read
    assert hub.reads_only()
    assert cli.main(["status", str(ID)], gh=hub) == 0
    out = capsys.readouterr().out.splitlines()
    assert "board: not checked (the Project does not match the config): open-at-done, board-tasks, board-sync, done-state" in out
    assert not any("GitHub could not be read" in line for line in out)


def test_a_project_that_is_not_found_is_a_config_note_and_a_local_stage_still_prints(env):
    world(env, plan=False)
    hub = Hub()
    real = hub.graphql

    def gone(query, variables=None):
        if "fields(" in query:
            return {"user": None}
        return real(query, variables)
    hub.graphql = gone
    res = status.evaluate(hub, ID, str(env))
    assert res["stage"] == "plan" and res["board"] == "config"
    assert status.render(res, ID).splitlines()[-1] == "board: not checked (the Project does not match the config): open-at-done"


def test_a_project_error_outside_a_project_read_is_a_plain_error(env):
    world(env)
    hub = Hub()
    real = hub.rest

    def odd(method, path, body=None):
        if path.endswith(f"issues/{ID}"):
            raise projects.ProjectError("E_PROJECT_NOT_FOUND", "x")
        return real(method, path, body)
    hub.rest = odd
    assert status.evaluate(hub, ID, str(env))["board"] == "error"


# ---- M1: an item that is not on the Project is marked, though done-state counts it as met ----
def test_an_issue_off_the_project_is_marked_in_the_json_and_the_text(env, capsys):
    world(env)
    hub = Hub(on_board=False)
    res = status.evaluate(hub, ID, str(env))
    assert res["stage"] == "done" and res["on_board"] is False and res["column"] is None
    assert "Board: this issue is not on the Project (ghp done adds nothing there)" in status.render(res, ID).splitlines()
    assert cli.main(["status", str(ID), "--json"], gh=hub) == 0
    assert json.loads(capsys.readouterr().out)["on_board"] is False
    assert status.evaluate(Hub(), ID, str(env))["on_board"] is True
    assert not any(line.startswith("Board:") for line in status.render(status.evaluate(Hub(), ID, str(env)), ID).splitlines())


def test_on_board_is_unknown_when_not_read_or_not_configured(env):
    world(env)
    assert status.evaluate(None, ID, str(env), offline=True)["on_board"] is None
    hub = Hub()
    hub.no_scope = True
    assert status.evaluate(hub, ID, str(env))["on_board"] is None
    world_again(env, project=None)
    assert status.evaluate(Hub(), ID, str(env))["on_board"] is None


# ---- M6: the final display escape, and the ambiguous ledger online ----
def test_the_final_print_escapes_even_a_fixed_text_that_somehow_holds_control_characters(env, monkeypatch, capsys):
    world(env, plan=False)
    monkeypatch.setitem(status._WHY, "plan", "bad \x1b[31m red \u202e")
    assert cli.main(["status", str(ID)], gh=Hub()) == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\u202e" not in out and "bad \\u001b[31m red \\u202e" in out
    assert cli.main(["status", str(ID), "--json"], gh=Hub()) == 0
    assert "\x1b" not in capsys.readouterr().out


def test_an_ambiguous_ledger_is_not_a_github_failure_online(env):
    world(env)
    p = env / "dod" / f"DoD_{ID}.md"
    p.write_text(p.read_text() + f"\n{dod.SECTION2}\n- a second one\n")
    res = status.evaluate(Hub(), ID, str(env))
    assert res["problem"] == "ledger-ambiguous" and res["board"] is None and res["board_checked"] is True
    world_again(env, questions="open")
    p = env / "dod" / f"DoD_{ID}.md"
    p.write_text(p.read_text() + f"\n{dod.SECTION2}\n- a second one\n")
    res = status.evaluate(Hub(), ID, str(env))
    assert res["problem"] == "ledger-ambiguous" and res["board"] is None  # the questions-posted read parses the ledger outside the board read


def test_offline_flag_degrades_to_a_named_note(env, capsys):
    world(env, plan=False)
    hub = Hub()
    assert cli.main(["status", str(ID), "--offline"], gh=hub) == 0
    out = capsys.readouterr().out
    assert hub.calls == [] and "board: not checked (offline): open-at-done, questions-posted" in out.splitlines()


def test_a_degraded_board_never_prints_the_raw_error(env, capsys):
    world(env)
    for err in (GhError("E_GH_HTTP", 403, f"{UNTRUSTED} \x1b[31m token ghp_{'a' * 30}"), ScopeMissing([f"{UNTRUSTED}\x1b"])):
        hub = Hub()
        hub.down = err
        for flags in ([], ["--json"]):
            cli.main(["status", str(ID), *flags], gh=hub)
            cap = capsys.readouterr()
            assert UNTRUSTED not in cap.out + cap.err and "\x1b" not in cap.out and "ghp_" not in cap.out


# ---- the verb ----
def run(env, hub, *flags, number=str(ID)):
    return cli.main(["status", number, *flags], gh=hub)


def test_verb_prints_the_one_line_answer(env, capsys):
    world(env, plan=False)
    assert run(env, Hub()) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"Issue #{ID}: waiting on stage plan. Next: speckit.ghp.plan {ID}"
    assert out[1] == "Why: no plan.md in the spec dir"


def test_verb_json(env, capsys):
    world(env, plan=False)
    assert run(env, Hub(), "--json") == 0
    res = json.loads(capsys.readouterr().out)
    assert res["stage"] == "plan" and res["next"] == f"speckit.ghp.plan {ID}" and res["board_checked"] is True
    assert set(res) == {"stage", "next", "why", "board_checked", "not_checked", "board", "progress", "column", "on_board", "notes", "problem", "run", "run_notes"}
    assert res["on_board"] is True and res["notes"] == []


def test_verb_offline_builds_no_client_and_prints_what_was_not_checked(env, capsys):
    world(env, plan=False, questions="open")
    assert cli.main(["status", str(ID), "--offline"], gh=FakeGh()) == 0  # a FakeGh with no answers: any call would fail the test
    out = capsys.readouterr().out
    assert out.startswith(f"Issue #{ID}: waiting on stage questions-answered. Next: speckit.ghp.plan {ID}\n") and "board: not checked (offline): open-at-done, questions-posted" in out


def test_verb_without_a_config_says_setup(env, capsys):
    assert run(env, FakeGh()) == 0  # a bare number needs no default repository: the report says setup
    assert capsys.readouterr().out.splitlines()[0] == f"Issue #{ID}: waiting on stage config. Next: ghp setup"
    assert run(env, FakeGh(), number="octo/demo#11") == 0
    assert capsys.readouterr().out.splitlines()[0] == f"Issue #{ID}: waiting on stage config. Next: ghp setup"


def test_verb_with_an_invalid_config_prints_the_report_and_exits_3(env, capsys):
    (env / "ghp.config.json").write_text("{not json")
    assert run(env, FakeGh()) == 3
    cap = capsys.readouterr()
    assert cap.out.splitlines()[0] == f"Issue #{ID}: waiting on stage config. Next: ghp doctor"


def test_verb_with_an_ambiguous_ledger_prints_the_report_and_exits_3(env, capsys):
    world(env)
    p = env / "dod" / f"DoD_{ID}.md"
    p.write_text(p.read_text() + f"\n{dod.SECTION2}\n- a second one\n")
    code = run(env, Hub(), "--offline")
    out = capsys.readouterr().out
    assert code == 3 and "more than one `## 2.` heading" in out and out.startswith("Issue #")
    assert json.loads((run(env, Hub(), "--offline", "--json"), capsys.readouterr().out)[1])["problem"] == "ledger-ambiguous"


def test_verb_all_met_text(env, capsys):
    world(env)
    assert run(env, Hub()) == 0
    assert capsys.readouterr().out.splitlines()[0] == f"Issue #{ID}: done. Next: none"


def test_verb_board_not_checked_text(env, capsys):
    world(env)
    hub = Hub()
    hub.down = GhError("E_GH_NETWORK", None, "x")
    assert run(env, hub) == 0
    assert capsys.readouterr().out.splitlines()[0] == f"Issue #{ID}: board not checked. Next: ghp doctor"


@pytest.mark.parametrize("bad", ["0", "-3", "abc", "2147483648", "1.5", "", " 11", "octo/other#11", "https://github.com/octo/demo/pull/11"])
def test_verb_rejects_a_bad_reference_before_any_call(env, capsys, bad):
    world(env)
    hub = Hub()
    assert run(env, hub, number=bad) == 2
    assert "error" in capsys.readouterr().err and hub.calls == []


def test_board_checked_is_false_offline_even_for_a_local_stage(env):
    world(env, ledger=False)
    res = check(env, None, "ledger", offline=True)
    assert res["board"] == "offline" and res["board_checked"] is False
    res = check(env, Hub(), "ledger")
    assert res["board"] is None and res["board_checked"] is True


def test_a_number_that_is_not_an_issue_number_is_refused(env):
    world(env)
    for bad in (0, -1, 2 ** 31, True, "11", 1.0, None):
        with pytest.raises(ValueError):
            status.evaluate(Hub(), bad, str(env))


def test_unclosed_fence_next_is_a_refresh_only_when_it_can_repair(env):
    world(env)
    p = env / "dod" / f"DoD_{ID}.md"
    good = p.read_text()
    p.write_text(good.replace("it works\n", "```\nit works\n", 1))  # opened in section 1: `ghp dod --refresh` splices from section 2
    check(env, Hub(), "ledger", "section 1", nxt=f"ghp dod {ID} --refresh")
    p.write_text(good + "\n```\n")  # opened after section 2: a refresh refuses, so it is by hand
    check(env, Hub(), "ledger", "by hand", nxt=f"ghp status {ID}")


# ---- catch-up ----
def test_catch_up_order_and_only_unmet_stages(env):
    world(env, mode="strict", plan=False, tasks="open")  # plan.md missing, tasks unticked, no verification record: three unmet stages
    res = status.evaluate(Hub(), ID, str(env), with_catch_up=True)
    # only the unmet stages, in the workflow's order (implement, then verify); met stages (tasks.md, the gate, the review, the PR link) list nothing
    assert res["catch_up"] == [f"speckit.ghp.plan {ID}", f"speckit.ghp.implement {ID}", f"ghp strict verify {ID}"]
    out = status.render(res, ID).splitlines()
    assert out[-5:] == ["Catch-up (in order; after each preview read it and decide before the apply):", f"1. speckit.ghp.plan {ID}",
                        f"2. speckit.ghp.implement {ID}", f"3. ghp strict verify {ID}", status.CATCH_UP_AGAIN.format(id=ID)]
    assert out[-1].startswith(f"Run `ghp status {ID}` again after each step:") and "`wait-po-answers`" in out[-1] and "not modelled" in out[-1]


def test_catch_up_never_lists_a_generation_command_for_met_stages(env):
    world(env, mode="strict", ledger=False)
    res = status.evaluate(Hub(), ID, str(env), with_catch_up=True)
    cmds = res["catch_up"]
    order = [f"speckit.ghp.implement {ID}", f"ghp decisions {ID} --post --if-any --plan-out .ghp/plan-decisions.md", f"ghp strict verify {ID}",
             f"ghp push {ID} --plan-out .ghp/plan-sync.md", f"speckit.ghp.gate {ID}", f"speckit.ghp.review {ID}"]
    present = [cmds.index(x) for x in order if x in cmds]
    assert present == sorted(present) and f"ghp strict verify {ID}" in cmds


def test_catch_up_lists_only_the_gate_and_pr_when_those_are_unmet_and_skips_strict_only_steps_in_advisory(env):
    world(env, gate=None)
    res = status.evaluate(Hub(linked=[]), ID, str(env), with_catch_up=True)
    assert res["stage"] == "gate"
    assert res["catch_up"] == [f"speckit.ghp.gate {ID}", f"speckit.ghp.pr {ID}"]  # the review is met; no `ghp strict verify` in advisory mode


def test_catch_up_when_the_story_is_current_is_empty(env):
    world(env)
    res = status.evaluate(Hub(), ID, str(env), with_catch_up=True)
    assert res["catch_up"] == [] and "Catch-up: nothing to catch up." in status.render(res, ID).splitlines()


def test_catch_up_offline_on_a_done_story_lists_no_generation_command(env):
    world(env)  # plan.md, tasks.md ticked, gate, review: everything local is met
    res = status.evaluate(None, ID, str(env), offline=True, with_catch_up=True)
    assert res["stage"] == "board-not-checked" and res["catch_up"] == []
    text = status.render(res, ID)
    assert f"Catch-up: local stages are all met; board stages not checked: run `ghp status {ID}` online." in text.splitlines()
    assert "speckit.ghp" not in text.split("Catch-up")[1]


def test_catch_up_online_error_on_a_done_story_is_the_same(env):
    world(env)
    hub = Hub()
    hub.down = GhError("E_GH_NETWORK", None, "down")
    res = status.evaluate(hub, ID, str(env), with_catch_up=True)
    assert res["catch_up"] == [] and "board stages not checked" in status.render(res, ID)


def test_catch_up_a_late_unposted_question_lists_only_that_stages_preview_and_apply(env):
    world(env)  # complete, then the posted questions comment no longer matches section 2
    hub = Hub()
    hub.post(questions_comment(env, open_texts=["something else"]))
    res = status.evaluate(hub, ID, str(env), with_catch_up=True)
    assert res["stage"] == "questions-posted"
    assert res["catch_up"] == [f"ghp questions {ID} --post --plan-out .ghp/plan-questions.md", f"ghp questions {ID} --post --apply --expect-plan .ghp/plan-questions.md"]


def test_catch_up_an_open_unposted_question_waits_for_the_post_before_plan(env):
    world(env, questions="open")
    res = status.evaluate(Hub(), ID, str(env), with_catch_up=True)
    assert res["catch_up"] == [f"ghp questions {ID} --post --plan-out .ghp/plan-questions.md", f"ghp questions {ID} --post --apply --expect-plan .ghp/plan-questions.md"]


def test_catch_up_offline_with_a_local_gap_lists_that_stage_and_the_unread_board_previews_but_never_pr(env):
    world(env, plan=False)
    res = status.evaluate(None, ID, str(env), offline=True, with_catch_up=True)
    cmds = res["catch_up"]
    assert f"speckit.ghp.plan {ID}" in cmds and f"ghp push {ID} --plan-out .ghp/plan-push.md" in cmds
    assert f"speckit.ghp.tasks {ID}" not in cmds and f"speckit.ghp.implement {ID}" not in cmds and f"speckit.ghp.pr {ID}" not in cmds  # their evidence exists / PR may exist


def test_catch_up_items_are_fixed_templates(env):
    world(env, mode="strict", ledger=False)
    cmds = status.evaluate(Hub(), ID, str(env), with_catch_up=True)["catch_up"]
    assert cmds and all(re.fullmatch(r"(speckit\.ghp\.[a-z]+|ghp [a-z]+)( [a-z]+)? 11( --[a-z-]+( \.ghp/[a-z-]+\.md)?)*", c) for c in cmds), cmds


def test_catch_up_without_a_config_is_the_setup_command(env):
    res = status.evaluate(None, ID, str(env), offline=True, with_catch_up=True)
    assert res["stage"] == "config" and res["catch_up"] == ["ghp setup"]


def test_catch_up_without_a_ledger_starts_with_pull(env):
    world(env, ledger=False)
    res = status.evaluate(Hub(), ID, str(env), with_catch_up=True)
    assert res["stage"] == "ledger" and res["catch_up"][0] == f"speckit.ghp.pull {ID}"


def test_verb_catch_up_prints_the_ordered_list_and_json_key(env, capsys):
    world(env, plan=False)
    assert run(env, Hub(), "--catch-up") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"Issue #{ID}: waiting on stage plan. Next: speckit.ghp.plan {ID}" and f"1. speckit.ghp.plan {ID}" in out and not any("implement" in ln for ln in out)
    assert run(env, Hub(), "--catch-up", "--json") == 0
    assert json.loads(capsys.readouterr().out)["catch_up"][0] == f"speckit.ghp.plan {ID}"


def test_without_catch_up_the_result_has_no_catch_up_key(env):
    world(env, plan=False)
    res = status.evaluate(Hub(), ID, str(env))
    assert "catch_up" not in res
    assert status.render(res, ID).splitlines()[:2] == [f"Issue #{ID}: waiting on stage plan. Next: speckit.ghp.plan {ID}", "Why: no plan.md in the spec dir"]


def test_the_first_line_says_the_stage_is_waiting_not_reached():
    """T34 live: `Issue #196: merged.` read as "is merged" while the PR was open; an unmet stage is named as waiting."""
    res = {"stage": "merged", "next": "git fetch origin", "why": status._WHY["merged"], "progress": None, "column": None, "on_board": None,
           "notes": [], "board": None, "not_checked": [], "run_notes": []}
    assert status.render(res, 196).splitlines()[0] == "Issue #196: waiting on stage merged. Next: git fetch origin"
    for stage, first in (("done", "Issue #196: done. Next: none"), (status.BOARD_NOT_CHECKED, "Issue #196: board not checked. Next: none")):
        assert status.render(dict(res, stage=stage, next="none"), 196).splitlines()[0] == first  # nothing waits there


# ---- linear-time regexes (the gate heading and the blocking line read ledger text the PR controls) ----
@pytest.mark.parametrize("n", [30_000, 300_000])
@pytest.mark.parametrize("make", [lambda n: "### Gate" + " x" * n, lambda n: "- blocking:" + " " * n + "x", lambda n: "- blocking: yes" + " " * n + "x",
                                  lambda n: "\n- blocking:" * n, lambda n: "### Gate\n" * n, lambda n: "- blocking:" + "\n" * n])
def test_status_regexes_are_linear(n, make):
    big = make(n)
    start = time.perf_counter()
    status._GATE_HEADING.findall(big)
    status._BLOCKING.findall(big)
    assert time.perf_counter() - start < 2.0


def test_the_module_reads_no_token_and_makes_no_write_call():
    src = (Path(status.__file__)).read_text(encoding="utf-8")
    assert "GH_TOKEN" not in src and "GITHUB_TOKEN" not in src and ".arm(" not in src and "arm_or_refuse" not in src and "mutation" not in src
    assert not any("mutation" in q for q in status.QUERIES.values())


def test_status_runs_as_a_module_with_no_config_and_no_network(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parent.parent / "src"))
    env.pop("GHP_CONFIG", None)
    r = subprocess.run([sys.executable, "-m", "ghp", "status", "7", "--offline"], capture_output=True, text=True, env=env, cwd=tmp_path)
    assert r.returncode == 0 and r.stdout.splitlines()[0] == "Issue #7: waiting on stage config. Next: ghp setup", (r.stdout, r.stderr)


# ---- T26b: an open issue whose Project item is at Done (a reopened issue: nothing moves the card back) ----
OPEN_AT_DONE_NEXT = f"move #{ID} out of Done by hand in the Project, then run ghp activate {ID}"


def test_an_open_issue_at_done_is_a_named_problem_first(env):
    world(env)
    hub = Hub(state="open", status="Done")
    res = check(env, hub, "open-at-done", nxt=OPEN_AT_DONE_NEXT)
    assert res["why"] == ("the issue is open but its Project item is at Done: the card was moved to Done without closing the issue, or the issue was "
                          "reopened (reopening does not move its card); ghp never moves a card out of Done")
    assert res["board_checked"] is True and res["problem"] is None
    assert hub.reads_only()
    # it comes before every other stage, however early that one is unmet
    world_again(env, plan=False, questions="none")
    check(env, Hub(state="open", status="Done"), "open-at-done")


def test_done_state_does_not_count_an_open_issue_at_done_as_met(env):
    world(env)
    c = status.Ctx(Hub(state="open", status="Done"), ID, str(env), False)
    assert status._judge(c, "done-state") == (False, "the issue is still open")
    assert status._judge(c, "open-at-done")[0] is False
    c = status.Ctx(Hub(state="closed", status="Done"), ID, str(env), False)
    assert status._judge(c, "done-state")[0] is True and status._judge(c, "open-at-done")[0] is True


@pytest.mark.parametrize("state,column", [("open", "In Progress"), ("open", "Todo"), ("open", None), ("closed", "Done"), ("closed", "In Progress")])
def test_only_open_at_done_is_the_problem(env, state, column):
    world(env)
    hub = Hub(state=state, status=column)
    if column is None:
        hub.status.pop(ITEM, None)
    res = status.evaluate(hub, ID, str(env))
    assert res["stage"] != "open-at-done", res


def test_an_open_issue_that_is_not_on_the_board_is_not_the_problem(env):
    world(env)
    assert check(env, Hub(state="open", on_board=False))["stage"] != "open-at-done"


def test_no_project_means_no_open_at_done_check_and_no_project_read(env):
    world(env, project=None)
    hub = Hub(state="open", status="Done")
    res = status.evaluate(hub, ID, str(env))
    assert res["stage"] != "open-at-done" and res["board_checked"] is True
    assert not any("projectItems" in c[1] or "fields(" in c[1] for c in hub.calls if c[0] == "graphql")


def test_open_at_done_is_not_checked_when_the_project_cannot_be_read(env):
    world(env)
    hub = Hub(state="open", status="Done")
    hub.no_scope = True
    res = status.evaluate(hub, ID, str(env))
    assert res["stage"] == "board-not-checked" and "open-at-done" in res["not_checked"]
    assert status.evaluate(None, ID, str(env), offline=True)["not_checked"][0] == "open-at-done"


def test_open_at_done_text_is_fixed_and_the_verb_prints_it(env, capsys):
    world(env)
    hub = Hub(state="open", status="Done")
    hub.issues[ID]["title"] = UNTRUSTED
    assert cli.main(["status", str(ID)], gh=hub) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"Issue #{ID}: waiting on stage open-at-done. Next: {OPEN_AT_DONE_NEXT}"
    assert out[1] == "Why: " + status.OPEN_AT_DONE and "Board column: Done" in out and UNTRUSTED not in "\n".join(out)
    assert cli.main(["status", str(ID), "--json"], gh=hub) == 0
    assert json.loads(capsys.readouterr().out)["stage"] == "open-at-done"
    assert hub.reads_only()
