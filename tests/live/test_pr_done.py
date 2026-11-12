"""T26 (LIVE): pull requests and `ghp done` against the scratch repo `praveenlavu/ghp-spike-scratch` (default branch `main`) and its user
Project 3, account `praveenlavu` only.

The run clones the scratch repo into a temp directory (git gets a credential helper that asks `gh auth token -u praveenlavu`; every
other helper is reset, so no other account's credential can be used and no token is in argv or a file), creates FRESH fictional issues
(label ghp:live-test), branches named `ghp-live/t26-<stamp>-*` and `feature/gh<n>-*`, pull requests, and merges them. The tests run in
file order and share one `world`; the teardown closes every PR and issue the run left open (issues as not_planned) and deletes every
branch it pushed. Merged commits stay on `main` (files under `t26/<stamp>/`); the Project items stay on the board.
Findings are collected in RECORD and dumped to $GHP_T26_RECORD (a path) for docs/evidence/T26.md. Every wait is bounded (<= 60 s).
T26f (docs/evidence/T26f.md) adds `done --pr N` live: AC5 below, a default-branch merge, and the --pr refusals; every `done` write there goes
a dry run with --plan-out, then --expect-plan --apply (`done_gated`); `pr create` uses the same two steps.
"""
import json
import os
import subprocess
import time
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest

from ghp import done, issues, projects, prs
from ghp.config import Config
from ghp.gh import Gh, GhError
from ghp.refs import Ref
from live.conftest import OWNER, PROJECT, REPO, REPO_PATH
from live.test_write_path import logged_gh

STAMP = time.strftime("%Y%m%d-%H%M%S")
FULL = "%s/%s" % (OWNER, REPO)
URL = "https://github.com/%s.git" % FULL
HELPER = "!f() { echo username=%s; echo password=$(gh auth token -u %s); }; f" % (OWNER, OWNER)  # the token never leaves the helper
GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0")
IDENTITY = ("-c", "user.name=%s" % OWNER, "-c", "user.email=22502189+%s@users.noreply.github.com" % OWNER)
WAIT = 60  # the longest any poll here waits
NEGATIVE_WAIT = 30  # how long "the issue stays open" is watched
STORY_BODY = "# Story\n\nAs a fictional visitor I book a pickup slot (T26 live test).\n\n## Acceptance criteria\n\n1. AC1 a slot can be chosen\n"
RECORD = {"stamp": STAMP}

INFO = """query($id: ID!) { node(id: $id) { ... on Issue { state stateReason
  timelineItems(last: 1, itemTypes: [CLOSED_EVENT]) { nodes { ... on ClosedEvent { createdAt stateReason actor { login }
    closer { __typename ... on PullRequest { number merged baseRefName mergeCommit { oid } }
             ... on Commit { oid messageHeadline repository { nameWithOwner } } ... on ProjectV2 { number } } } } }
  closedByPullRequestsReferences(first: 20, includeClosedPrs: true) { nodes { number merged state baseRefName } } } } }"""
LINKS = """query($id: ID!, $owner: String!, $repo: String!, $pr: Int!) {
  node(id: $id) { ... on Issue { timelineItems(first: 50, itemTypes: [CROSS_REFERENCED_EVENT, CONNECTED_EVENT]) { nodes { __typename
    ... on CrossReferencedEvent { willCloseTarget source { __typename ... on PullRequest { number merged baseRefName } } }
    ... on ConnectedEvent { subject { __typename ... on PullRequest { number } } } } } } }
  repository(owner: $owner, name: $repo) { pullRequest(number: $pr) { closingIssuesReferences(first: 10) { nodes { number } } } } }"""


def git(w, *args, cwd=None, check=True):
    r = subprocess.run(["git", "-c", "credential.helper=", "-c", "credential.helper=" + HELPER, *IDENTITY, *args], cwd=str(cwd or w.clone),
                       capture_output=True, text=True, timeout=120, env=GIT_ENV)
    assert r.returncode == 0 or not check, (args, r.stderr[-400:])
    return r.stdout.strip()


@pytest.fixture(scope="module")
def world(gh, tmp_path_factory):
    w = SimpleNamespace(issues=[], branches=[], prs=[], root=tmp_path_factory.mktemp("t26"))
    w.ag = Gh()  # setup, probes and teardown; a verb under test gets an UNARMED Gh (its gate arms it)
    w.ag.arm()
    w.clone = w.root / "scratch"
    git(w, "clone", "-q", URL, str(w.clone), cwd=w.root)
    (w.clone / "ghp.config.json").write_text('{"owner": "%s", "repo": "%s", "project_owner": "%s", "project_number": %d}' % (
        OWNER, REPO, OWNER, PROJECT))  # untracked: never committed to the scratch repo
    w.cfg = Config.load(str(w.clone))
    w.board = projects.discover(w.ag, w.cfg)
    try:
        yield w
    finally:
        for n in w.prs:
            if w.ag.rest("GET", "%s/pulls/%d" % (REPO_PATH, n))["state"] == "open":
                w.ag.rest("PATCH", "%s/pulls/%d" % (REPO_PATH, n), {"state": "closed"})
                RECORD.setdefault("cleanup_prs_closed", []).append(n)
        for b in dict.fromkeys(w.branches):
            try:
                w.ag.rest("DELETE", "%s/git/refs/heads/%s" % (REPO_PATH, urllib.parse.quote(b, safe="/")))
                RECORD.setdefault("cleanup_branches_deleted", []).append(b)
            except GhError as e:
                RECORD.setdefault("cleanup_branches_not_deleted", []).append([b, e.status])
        for n in w.issues:
            if w.ag.rest("GET", "%s/issues/%d" % (REPO_PATH, n))["state"] != "closed":
                w.ag.rest("PATCH", "%s/issues/%d" % (REPO_PATH, n), {"state": "closed", "state_reason": "not_planned"})
                RECORD.setdefault("cleanup_issues_closed_not_planned", []).append(n)
        RECORD["issues_created"] = w.issues
        RECORD["prs_created"] = w.prs
        if os.environ.get("GHP_T26_RECORD"):
            Path(os.environ["GHP_T26_RECORD"]).write_text(json.dumps(RECORD, indent=2, default=str))


@pytest.fixture(autouse=True)
def _in_clone(world, monkeypatch):
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    monkeypatch.chdir(world.clone)


def run(capsys, *argv, gh=None):
    from ghp.__main__ import main
    g, log = gh if gh else logged_gh()
    code = main(list(argv), g)
    out, err = capsys.readouterr()
    return code, out, err, log


# ---- helpers ---------------------------------------------------------------------------------------------------------

def new_issue(w, title, body="Fictional issue for the T26 live test.", board=False):
    made = w.ag.rest("POST", REPO_PATH + "/issues", {"title": "%s (fictional) %s" % (title, STAMP), "body": body, "labels": ["ghp:live-test"]})
    w.issues.append(made["number"])
    issue = issues.get(w.ag, Ref(OWNER, REPO, made["number"]))
    item = projects.add_item(w.ag, w.board, issue) if board else None
    return issue, item


def poll(fn, timeout=WAIT, every=2.0):
    """(the first truthy value of fn() or the last value, seconds until it was truthy or None)."""
    start = time.monotonic()
    while True:
        v = fn()
        if v:
            return v, round(time.monotonic() - start, 1)
        if time.monotonic() - start >= timeout:
            return v, None
        time.sleep(every)


def state(w, n):
    i = w.ag.rest("GET", "%s/issues/%d" % (REPO_PATH, n))
    return i["state"], i["state_reason"]


def info(w, node_id):
    return w.ag.graphql(INFO, {"id": node_id})["node"]


def closed_event(w, node_id):
    nodes = info(w, node_id)["timelineItems"]["nodes"]
    return nodes[0] if nodes else None


def status(w, item):
    return projects._status_of(w.ag, item)


def set_status_raw(w, item, name):
    """The board 'drag': updateProjectV2ItemFieldValue with the option id, outside the product's Done guard."""
    w.ag.graphql(projects.MUTATIONS["status"], {"project": w.board.project_id, "item": item, "field": w.board.status_field_id,
                                                "option": w.board.options[name]})


def fresh_main(w):
    git(w, "fetch", "-q", "origin")
    git(w, "switch", "-q", "--detach", "origin/main")


def commit_file(w, tag, message, extra=()):
    f = w.clone / "t26" / STAMP / (tag + ".txt")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("Fictional T26 probe file: %s\n" % tag)
    git(w, "add", str(f), *extra)
    git(w, "commit", "-q", "-m", message)


def probe_branch(w, tag, message=None):
    name = "ghp-live/t26-%s-%s" % (STAMP, tag)
    fresh_main(w)
    git(w, "switch", "-q", "-c", name)
    commit_file(w, tag, message or "T26 probe %s (fictional)" % tag)
    return name


def push(w, *names):
    w.branches += list(names)
    git(w, "push", "-q", "origin", *names)


def open_pr(w, head, base, title, body, draft=False):
    """POST pulls (raw); samples how long until the head filter lists it (polls every 0.25 s, at most 10 s)."""
    reply = w.ag.rest("POST", REPO_PATH + "/pulls", {"title": title, "head": head, "base": base, "body": body, "draft": draft})
    after = time.monotonic()
    w.prs.append(reply["number"])
    polls, listed = 0, False
    while not listed and time.monotonic() - after < 10:
        polls += 1
        listed = reply["number"] in [p["number"] for p in w.ag.rest("GET", "%s/pulls?head=%s&base=%s&state=open&per_page=100" % (
            REPO_PATH, urllib.parse.quote("%s:%s" % (OWNER, head), safe=":/"), base))]
        if not listed:
            time.sleep(0.25)
    RECORD.setdefault("pr_list_lag", []).append({"pr": reply["number"], "listed": listed, "polls": polls,
                                                 "seconds_after_post_returned": round(time.monotonic() - after, 2)})
    return reply


def merge(w, pr, method, title=None, message=None):
    body = {"merge_method": method}
    if title is not None:
        body["commit_title"] = title
    if message is not None:
        body["commit_message"] = message
    for attempt in range(6):  # 405 / 409: the base moved since GitHub computed mergeability; nothing was merged, so retrying is safe
        try:
            return w.ag.rest("PUT", "%s/pulls/%d/merge" % (REPO_PATH, pr), body)
        except GhError as e:
            if e.status not in (405, 409) or attempt == 5:
                raise
            RECORD.setdefault("merge_retries", []).append([pr, e.status, str(e)[:120]])
            time.sleep(3)


def gh_pr_merge(w, pr, flag="--merge"):
    r = subprocess.run(["gh", "pr", "merge", str(pr), "-R", FULL, flag], cwd=str(w.root), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-400:]


def make_ledger(w, capsys, n):
    code, out, err, _ = run(capsys, "dod", str(n))
    assert code == 0, err
    ledger = w.clone / "dod" / ("DoD_%d.md" % n)
    ledger.write_text(ledger.read_text().replace("- [ ] ", "- [x] "))  # every box ticked: the story is finished
    return ledger


def story_branch(w, capsys, n, title):
    """`ghp branch N --apply` from the tip of origin/main, one commit (the ledger and a file), pushed."""
    fresh_main(w)
    ledger = make_ledger(w, capsys, n)
    code, out, err, _ = run(capsys, "branch", str(n), "--title", title, "--apply")
    assert code == 0, err
    b = json.loads(out)
    assert b["applied"] and b["action"] == "create" and b["branch"].startswith("feature/gh%d-" % n), b
    assert git(w, "symbolic-ref", "--short", "HEAD") == b["branch"]
    commit_file(w, "story-%d" % n, "T26 story %d: %s (fictional)" % (n, title), extra=(str(ledger),))
    push(w, b["branch"])
    return b["branch"]


def pr_create(w, capsys, n, title, head, base):
    body = w.clone / ".ghp" / ("t26-body-%d.md" % n)  # untracked; the body file must be inside the working tree
    body.parent.mkdir(exist_ok=True)
    body.write_text("Fictional change for the T26 live test.\n")
    argv = ("pr", "create", str(n), "--title", title, "--body-file", str(body.relative_to(w.clone)), "--base", base, "--head", head)
    approved = w.root / ("pr-plan-%d.md" % n)
    code, plan, err, log = run(capsys, *argv, "--plan-out", str(approved))
    assert code == 0 and log.writes == [], (plan, err)
    code, out, err, log = run(capsys, *argv, "--expect-plan", str(approved), "--apply", gh=logged_gh())
    assert code == 0 and "created PR #" in out, (out, err)
    assert [c[2] for c in log.writes] == ["POST repos/%s/pulls" % FULL], log.writes
    number = int(out.split("created PR #", 1)[1].split(":", 1)[0])
    w.prs.append(number)
    return number, plan


def done_writes(log):
    return [c[2] for c in log.writes]


def done_gated(w, capsys, n, *extra):
    """`ghp done` the repository's way: a dry run with --plan-out (0 writes), then --expect-plan on that file with --apply. (code, plan, out, err, writes)."""
    approved = w.root / ("done-plan-%d-%d.md" % (n, len(RECORD.setdefault("done_gated", []))))
    code, plan, err, log = run(capsys, "done", str(n), *extra, "--plan-out", str(approved))
    assert code == 0 and log.writes == [], (plan, err)
    code, out, err, log = run(capsys, "done", str(n), *extra, "--expect-plan", str(approved), "--apply", gh=logged_gh())
    RECORD["done_gated"].append({"issue": n, "argv": list(extra), "exit": code, "writes": done_writes(log), "result": out[len(plan):].strip()})
    return code, plan, out, err, done_writes(log)


def marked(w, n):
    return [c["id"] for c in w.ag.rest("GET", "%s/issues/%d/comments?per_page=100" % (REPO_PATH, n)) if done.MARKER % n in c["body"]]


# ---- AC1: branch, pr create, merge by the owner, closed by the PR, item Done ----
def test_ac1_branch_pr_create_merge_closes_with_closer_pull_request_and_item_done(world, capsys):
    w = world
    issue, item = new_issue(w, "T26 live story A: pickup slots", STORY_BODY, board=True)
    n = w.a = issue.ref.number
    w.a_item = item
    head = story_branch(w, capsys, n, "pickup slots")
    pr, plan = pr_create(w, capsys, n, "Pickup slots", head, "main")
    w.a_pr = pr
    got = w.ag.rest("GET", "%s/pulls/%d" % (REPO_PATH, pr))
    assert got["body"].rstrip().endswith("Closes #%d" % n) and "#%d" % n in got["title"] and got["base"]["ref"] == "main"
    RECORD["ac1"] = {"issue": n, "item": item, "branch": head, "pr": pr, "pr_title": got["title"], "pr_body": got["body"],
                     "plan_warnings": [l for l in plan.splitlines() if l.startswith("⚠")]}
    gh_pr_merge(w, pr)
    closed, secs = poll(lambda: state(w, n)[0] == "closed")
    assert closed, "#%d is not closed %d s after the merge" % (n, WAIT)
    ev = closed_event(w, issue.node_id)
    RECORD["ac1"].update(closed_after_s=secs, state=state(w, n), closed_event=ev)
    assert ev["closer"]["__typename"] == "PullRequest" and ev["closer"]["number"] == pr and ev["closer"]["merged"] is True
    f = done.facts(w.ag, issues.get(w.ag, issue.ref))
    assert f["closer"]["kind"] == "pr" and f["closer"]["pr"]["number"] == pr
    at_done, secs = poll(lambda: (status(w, item) or ("", ""))[0] == "Done")
    RECORD["ac1"]["item_done_after_close_s"] = secs
    RECORD["ac1"]["status_events"] = [[e.from_, e.to, e.actor_login] for e in projects.status_events(w.ag, issue.node_id)]
    assert at_done, "the item of #%d is not at Done %d s after the close" % (n, WAIT)


# ---- AC2: done after the merge: one ledger comment, byte-exact marker, idempotent ----
def test_ac2_done_after_the_merge_posts_the_ledger_comment_once(world, capsys):
    w, n = world, world.a
    code, plan, err, log = run(capsys, "done", str(n))
    assert code == 0 and log.writes == [] and "Step 1: post the ledger comment" in plan, (plan, err)
    RECORD["ac2_dry_run"] = {"calls": len(log.calls), "writes": 0,
                             "workflow_warnings": [l for l in plan.splitlines() if l.startswith("warning: the Project workflow")]}
    issue = issues.get(w.ag, Ref(OWNER, REPO, n))
    chk = done.check((w.clone / "dod" / ("DoD_%d.md" % n)).read_text())
    expected = done.plan_done(Gh(), w.cfg, issue, chk)["comment"]  # what the apply will post (reads only)
    code, out, err, log = run(capsys, "done", str(n), "--apply", gh=logged_gh())
    assert code == 0 and "posted the ledger comment" in out and "closed #" not in out, (out, err)
    assert done_writes(log) == ["POST repos/%s/issues/%d/comments" % (FULL, n)], log.writes
    marked = [c for c in w.ag.rest("GET", "%s/issues/%d/comments?per_page=100" % (REPO_PATH, n)) if done.MARKER % n in c["body"]]
    assert len(marked) == 1
    body = marked[0]["body"]
    RECORD["ac2"] = {"comment_id": marked[0]["id"], "byte_exact": body == expected, "ends_with_marker": body.endswith(done.MARKER % n),
                     "body": body, "apply_output": out}
    assert body == expected and body.endswith("\n\n" + done.MARKER % n)
    oid = done.facts(w.ag, issue)["closer"]["pr"]["oid"]
    at_merge = w.ag.rest("GET", "%s/contents/dod/DoD_%d.md?ref=%s" % (REPO_PATH, n, oid))
    RECORD["ac2"]["ledger_at_merge_commit"] = {"oid": oid, "path": at_merge["path"], "size": at_merge["size"]}
    code, out, err, log = run(capsys, "done", str(n), "--apply", gh=logged_gh())
    assert code == 0 and "nothing sent: already done" in out and log.writes == [], (out, err)
    assert len([c for c in w.ag.rest("GET", "%s/issues/%d/comments?per_page=100" % (REPO_PATH, n)) if done.MARKER % n in c["body"]]) == 1
    RECORD["ac2"]["second_apply"] = {"output": out.strip().splitlines()[-1], "writes": 0}


# ---- AC3 (+ the reopen probe): an open story with a merged PR; done closes it ----
def test_ac3_done_closes_an_open_story_and_the_item_reaches_done(world, capsys):
    w = world
    issue, item = new_issue(w, "T26 live story B: slot reminders", STORY_BODY, board=True)
    n = w.b = issue.ref.number
    head = story_branch(w, capsys, n, "slot reminders")
    pr, _ = pr_create(w, capsys, n, "Slot reminders", head, "main")
    gh_pr_merge(w, pr)
    assert poll(lambda: state(w, n)[0] == "closed")[0]
    at_done, secs = poll(lambda: (status(w, item) or ("", ""))[0] == "Done")
    rec = RECORD["ac3"] = {"issue": n, "item": item, "pr": pr, "done_after_merge_close_s": secs}
    # reopen: does the Project's automation move the item off Done? (watched for NEGATIVE_WAIT s)
    w.ag.rest("PATCH", "%s/issues/%d" % (REPO_PATH, n), {"state": "open"})
    moved, secs = poll(lambda: (status(w, item) or ("", ""))[0] != "Done", timeout=NEGATIVE_WAIT)
    rec["reopen"] = {"item_left_done": bool(moved), "after_s": secs, "status_now": status(w, item)}
    if (status(w, item) or ("", ""))[0] != "In Progress":  # the story is being worked on again (setup write, outside ghp)
        set_status_raw(w, item, "In Progress")
        rec["reopen"]["set_in_progress_by_test"] = True
    code, plan, err, log = run(capsys, "done", str(n))
    assert code == 0 and log.writes == [] and "Step 2: close #%d as completed" % n in plan, (plan, err)
    code, out, err, log = run(capsys, "done", str(n), "--apply", gh=logged_gh())
    rec.update(apply_output=out, apply_writes=done_writes(log))
    assert code == 0 and "closed #%d as completed" % n in out and "Status is Done" in out, (out, err)
    rec["status_set_by"] = "automation" if "moved by the Project's own workflow" in out else "ghp"
    assert state(w, n) == ("closed", "completed") and status(w, item)[0] == "Done"
    rec["closed_event"] = closed_event(w, issue.node_id)
    rec["status_events"] = [[e.from_, e.to, e.actor_login] for e in projects.status_events(w.ag, issue.node_id)]


# ---- AC4: a board drag to Done closes the issue without the gate ----
def test_ac4_board_drag_to_done_closes_the_story_without_the_gate(world):
    w = world
    issue, item = new_issue(w, "T26 live story D: board drag", STORY_BODY, board=True)
    n = issue.ref.number
    before = status(w, item)
    set_status_raw(w, item, "Done")
    closed, secs = poll(lambda: state(w, n)[0] == "closed")
    RECORD["ac4"] = {"issue": n, "item": item, "status_before": before, "closed": bool(closed), "closed_after_s": secs,
                     "state": state(w, n), "closed_event": closed_event(w, issue.node_id),
                     "workflows_enabled": [[x.name, x.enabled] for x in projects.workflows(w.ag, w.cfg) if x.name in done.CLOSING_WORKFLOWS]}
    assert closed, "#%d is still open %d s after its item was set to Done" % (n, WAIT)


# ---- AC5: a merge into a non-default branch ignores Closes; done closes ----
def test_ac5_merge_into_a_non_default_branch_ignores_closes_and_done_closes(world, capsys):
    w = world
    side = "ghp-live/t26-%s-side" % STAMP
    fresh_main(w)
    git(w, "switch", "-q", "-c", side)
    push(w, side)
    issue, item = new_issue(w, "T26 live story C: side branch", STORY_BODY, board=True)
    n = issue.ref.number
    head = story_branch(w, capsys, n, "side branch")
    pr, plan = pr_create(w, capsys, n, "Side branch", head, side)
    rec = RECORD["ac5"] = {"issue": n, "item": item, "base": side, "pr": pr, "plan_warnings": [l for l in plan.splitlines() if l.startswith("⚠")]}
    assert any("is not the default branch" in l for l in rec["plan_warnings"])
    gh_pr_merge(w, pr)
    assert w.ag.rest("GET", "%s/pulls/%d" % (REPO_PATH, pr))["merged"] is True
    closed, _ = poll(lambda: state(w, n)[0] == "closed", timeout=NEGATIVE_WAIT)
    rec["closed_by_the_merge"] = bool(closed)
    rec["linked"] = info(w, issue.node_id)["closedByPullRequestsReferences"]["nodes"]
    assert not closed, "#%d was closed by a merge into %s" % (n, side)
    rec["other_links"] = w.ag.graphql(LINKS, {"id": issue.node_id, "owner": OWNER, "repo": REPO, "pr": pr})
    assert rec["linked"] == []  # GitHub links nothing (T26a): without --pr, done finds no merge and sends nothing
    code, out, err, log = run(capsys, "done", str(n))
    rec["without_pr"] = {"exit": code, "error": err.strip(), "writes": done_writes(log)}
    assert code == 2 and "E_NOT_MERGED" in err and "--pr N" in err and log.writes == [], (code, out, err)
    # T26f: name the PR; dry run with --plan-out, then --expect-plan --apply
    code, plan, out, err, writes = done_gated(w, capsys, n, "--pr", str(pr))
    rec.update(done_exit=code, done_plan=plan, done_output=out[len(plan):].strip(), done_error=err.strip(), done_writes=writes)
    assert code == 0 and done.GIVEN in plan and "Merge: PR #%d into %s" % (pr, side) in plan, (plan, err)
    assert "posted the ledger comment" in out and "closed #%d as completed" % n in out and "Status is Done" in out, (out, err)
    assert writes[:2] == ["POST repos/%s/issues/%d/comments" % (FULL, n), "PATCH repos/%s/issues/%d" % (FULL, n)], writes
    assert state(w, n) == ("closed", "completed") and status(w, item)[0] == "Done" and len(marked(w, n)) == 1
    rec["closed_event"] = closed_event(w, issue.node_id)
    rec["status_events"] = [[e.from_, e.to, e.actor_login] for e in projects.status_events(w.ag, issue.node_id)]
    code, plan, out, err, writes = done_gated(w, capsys, n, "--pr", str(pr))  # rerun: idempotent
    rec["rerun"] = {"exit": code, "output": out.strip().splitlines()[-1], "writes": writes}
    assert code == 0 and "nothing sent: already done" in out and writes == [] and len(marked(w, n)) == 1, (out, err)


# ---- T26f: done --pr on a merge into the default branch (GitHub closed it; done records it and posts the ledger once) ----
def test_t26f_done_pr_after_a_default_branch_merge_posts_only_the_ledger_comment(world, capsys):
    w = world
    issue, item = new_issue(w, "T26f live story E: pickup notes", STORY_BODY, board=True)
    n = issue.ref.number
    head = story_branch(w, capsys, n, "pickup notes")
    pr, _ = pr_create(w, capsys, n, "Pickup notes", head, "main")
    gh_pr_merge(w, pr)
    assert poll(lambda: state(w, n)[0] == "closed")[0]
    ev = closed_event(w, issue.node_id)
    at_done, secs = poll(lambda: (status(w, item) or ("", ""))[0] == "Done")
    rec = RECORD["t26f_default"] = {"issue": n, "item": item, "pr": pr, "closed_event": ev, "done_after_close_s": secs}
    assert ev["closer"]["__typename"] == "PullRequest" and ev["closer"]["number"] == pr and at_done
    code, plan, out, err, writes = done_gated(w, capsys, n, "--pr", str(pr))
    rec.update(done_plan=plan, done_output=out[len(plan):].strip(), done_writes=writes)
    assert code == 0 and done.GIVEN_LINKED in plan and done.GIVEN not in plan, (plan, err)
    assert writes == ["POST repos/%s/issues/%d/comments" % (FULL, n)] and "posted the ledger comment" in out, (writes, out, err)
    code, plan, out, err, writes = done_gated(w, capsys, n, "--pr", str(pr))
    rec["rerun"] = {"exit": code, "output": out.strip().splitlines()[-1], "writes": writes}
    assert code == 0 and "nothing sent: already done" in out and writes == [] and len(marked(w, n)) == 1
    assert state(w, n) == ("closed", "completed") and status(w, item)[0] == "Done"
    rec["status_events"] = [[e.from_, e.to, e.actor_login] for e in projects.status_events(w.ag, issue.node_id)]


# ---- T26f: done --pr refusals, live (each a dry run: exit 2, nothing sent) ----
def test_t26f_done_pr_refusals(world, capsys):
    w = world
    x, _ = new_issue(w, "T26f live story X: refusals", STORY_BODY)
    y, _ = new_issue(w, "T26f live story Y: not mentioned", STORY_BODY)
    fresh_main(w)
    for i in (x, y):
        make_ledger(w, capsys, i.ref.number)  # untracked ledgers, every box ticked
    nx, ny = x.ref.number, y.ref.number
    shut, said = probe_branch(w, "t26f-open"), probe_branch(w, "t26f-mention")
    push(w, shut, said)
    unmerged = open_pr(w, shut, "main", "T26f probe: open PR (fictional)", "Closes #%d" % nx)["number"]
    mention = open_pr(w, said, "main", "T26f probe: mention only (fictional)", "Fictional change that mentions #%d only." % nx)["number"]
    merge(w, mention, "merge")
    rec = RECORD["t26f_refusals"] = {"x": nx, "y": ny, "open_pr": unmerged, "mention_pr": mention}

    def refused(n, number):
        code, out, err, log = run(capsys, "done", str(n), "--pr", str(number))
        return {"exit": code, "error": err.strip(), "writes": done_writes(log), "calls": len(log.calls)}

    got, secs = poll(lambda: (lambda r: r if "closing keyword" in r["error"] else None)(refused(nx, mention)))  # the mention event may lag
    rec["mention_only"], rec["mention_event_after_s"] = got or refused(nx, mention), secs
    rec["not_merged"] = refused(nx, unmerged)
    rec["not_mentioned"] = refused(ny, mention)
    rec["an_issue"] = refused(nx, ny)
    rec["no_such_number"] = refused(nx, 99999)
    for key, code in (("mention_only", "E_PR_NOT_LINKED"), ("not_merged", "E_PR_NOT_MERGED"), ("not_mentioned", "E_PR_NOT_LINKED"), ("an_issue", "E_NOT_A_PR")):
        assert rec[key]["exit"] == 2 and code in rec[key]["error"] and rec[key]["writes"] == [], (key, rec[key])
    assert "closing keyword" in rec["mention_only"]["error"] and "no mention" in rec["not_mentioned"]["error"]
    assert rec["no_such_number"]["exit"] in (1, 2) and rec["no_such_number"]["writes"] == [], rec["no_such_number"]  # recorded: null or a GraphQL error
    assert state(w, nx)[0] == "open" and state(w, ny)[0] == "open" and not marked(w, nx) and not marked(w, ny)


# ---- probe (b): PR plumbing ----
def test_probe_pr_plumbing_reply_shape_head_filter_draft_and_a_second_post(world):
    w = world
    a, d = probe_branch(w, "p01"), probe_branch(w, "p02")
    push(w, a, d)
    reply = open_pr(w, a, "main", "T26 probe p01 (fictional)", "Probe body (fictional).")
    pr = prs._pr(reply, w.cfg)  # the keys ghp reads parse
    rec = RECORD["plumbing"] = {"post_reply_keys": sorted(reply), "parsed": pr, "head_repo_full_name": reply["head"]["repo"]["full_name"]}
    assert pr["number"] == reply["number"] and pr["same_repo"] and pr["head"] == a and pr["base"] == "main" and pr["draft"] is False
    assert prs.find_open(w.ag, w.cfg, a, "main")["number"] == pr["number"]
    rec["head_filter_other_branch"] = len(w.ag.rest("GET", "%s/pulls?head=%s:%s&state=open" % (REPO_PATH, OWNER, "ghp-live/none-%s" % STAMP)))
    rec["head_filter_without_owner"] = len(w.ag.rest("GET", "%s/pulls?head=%s&state=all&per_page=100" % (REPO_PATH, urllib.parse.quote(a, safe="/"))))
    rec["all_prs_listed"] = len(w.ag.rest("GET", "%s/pulls?state=all&per_page=100" % REPO_PATH))
    try:
        again = w.ag.rest("POST", REPO_PATH + "/pulls", {"title": "T26 probe p01 again (fictional)", "head": a, "base": "main", "body": "x"})
        w.prs.append(again["number"])
        rec["second_post"] = {"outcome": "accepted", "number": again["number"]}
    except GhError as e:
        rec["second_post"] = {"outcome": "rejected", "code": e.code, "status": e.status, "detail": str(e)[:300]}
    try:
        dr = open_pr(w, d, "main", "T26 probe p02 draft (fictional)", "Probe body (fictional).", draft=True)
        rec["draft"] = {"outcome": "accepted", "number": dr["number"], "draft": dr["draft"], "parsed_draft": prs._pr(dr, w.cfg)["draft"]}
    except GhError as e:
        rec["draft"] = {"outcome": "rejected", "code": e.code, "status": e.status, "detail": str(e)[:300]}
    assert rec["second_post"]["outcome"] == "rejected"


# ---- probes (a) and (c): which closing forms close, and the closer of each merge method ----
FORMS = [  # tag, what it is, PR title, PR body, merge method, squash commit title, squash commit message, branch commit message
    ("f01", "closes: #N (colon)", None, "closes: #{n}", "merge", None, None, None),
    ("f02", "Fixes:#N (colon, no space)", None, "Fixes:#{n}", "merge", None, None, None),
    ("f03", "resolves #N", None, "resolves #{n}", "merge", None, None, None),
    ("f04", "Closes <full issue URL>", None, "Closes https://github.com/%s/issues/{n}" % FULL, "merge", None, None, None),
    ("f05", "Closes owner/repo#N", None, "Closes %s#{n}" % FULL, "merge", None, None, None),
    ("f06", "Closes, line break, #N", None, "Closes\n#{n}", "merge", None, None, None),
    ("f07", "keyword on the second line", None, "Fictional first line.\nCloses #{n}", "merge", None, None, None),
    ("f08", "inside a code span", None, "`Closes #{n}`", "merge", None, None, None),
    ("f09", "inside a fenced block", None, "```\nCloses #{n}\n```", "merge", None, None, None),
    ("f10", "inside an HTML comment", None, "<!-- Closes #{n} -->", "merge", None, None, None),
    ("f11", "in the PR title only (merge commit)", "Closes #{n} T26 probe f11 (fictional)", "Probe body without a keyword (fictional).", "merge", None, None, None),
    ("f12", "in a branch commit message only (merge commit)", None, "Probe body without a keyword (fictional).", "merge", None, None,
     "T26 probe f12 (fictional)\n\nFixes #{n}"),
    ("f13", "squash: keyword only in the squash commit message", None, "Probe body without a keyword (fictional).", "squash",
     "T26 probe f13 squash (fictional)", "Fixes #{n}", None),
    ("f14", "squash: keyword only in the PR body (commit title and message without it)", None, "Closes #{n}", "squash",
     "T26 probe f14 squash (fictional)", "Fictional squash message.", None),
    ("f15", "rebase merge, Closes #N in the body", None, "Closes #{n}", "rebase", None, None, None),
    ("f16", "squash with GitHub's default commit text (one commit: its own title), keyword only in the PR title",
     "Closes #{n} T26 probe f16 (fictional)", "Probe body without a keyword (fictional).", "squash", None, None, None),
]


def test_probe_closing_forms_and_the_closer_of_each_merge_method(world):
    w = world
    rows = []
    for tag, what, title, body, method, c_title, c_msg, b_msg in FORMS:
        issue, _ = new_issue(w, "T26 probe %s: issue for one closing form" % tag)
        n = issue.ref.number
        fmt = lambda s: None if s is None else s.replace("{n}", str(n))  # noqa: E731
        branch = probe_branch(w, tag, fmt(b_msg))
        rows.append({"tag": tag, "form": what, "issue": n, "node": issue.node_id, "branch": branch, "method": method,
                     "title": fmt(title) or "T26 probe %s (fictional)" % tag, "body": fmt(body), "commit_title": fmt(c_title),
                     "commit_message": fmt(c_msg), "branch_commit_message": fmt(b_msg)})
    push(w, *[r["branch"] for r in rows])
    for r in rows:
        r["pr"] = open_pr(w, r["branch"], "main", r["title"], r["body"])["number"]
        texts = [r["title"], r["body"]]
        r["scanner_flags_it"] = any((OWNER, REPO, r["issue"]) in prs.closing_targets(t, OWNER, REPO) for t in texts)
    for r in rows:
        m = merge(w, r["pr"], r["method"], r["commit_title"], r["commit_message"])
        r["merge_sha"] = m["sha"]
    start, pending = time.monotonic(), {r["issue"] for r in rows}
    while pending and time.monotonic() - start < WAIT:
        for r in rows:
            if r["issue"] in pending and state(w, r["issue"])[0] == "closed":
                r["closed_after_s"] = round(time.monotonic() - start, 1)
                pending.discard(r["issue"])
        if pending:
            time.sleep(3)
    for r in rows:
        node = info(w, r.pop("node"))
        ev = node["timelineItems"]["nodes"]
        r["closed"] = node["state"] == "CLOSED"
        r["state_reason"] = node["stateReason"]
        r["closer"] = ev[0]["closer"] if ev else None
        r["linked"] = node["closedByPullRequestsReferences"]["nodes"]
        pull = w.ag.rest("GET", "%s/pulls/%d" % (REPO_PATH, r["pr"]))
        r["pr_merge_commit_sha"] = pull["merge_commit_sha"]
        c = w.ag.rest("GET", "%s/commits/%s" % (REPO_PATH, pull["merge_commit_sha"]))
        r["merge_commit_message"] = c["commit"]["message"]
        r["merge_commit_parents"] = len(c["parents"])
    RECORD["forms"] = rows
    assert all("pr" in r and "merge_sha" in r for r in rows)


# ---- probe (c): a commit with `Fixes #N` pushed straight to main; the closer is a Commit ----
def test_probe_commit_closer_pushed_to_the_default_branch(world):
    w = world
    issue, _ = new_issue(w, "T26 probe commit closer: issue closed by a direct commit")
    n = issue.ref.number
    fresh_main(w)
    commit_file(w, "commit-closer", "T26 direct commit probe (fictional)\n\nFixes #%d" % n)
    git(w, "push", "-q", "origin", "HEAD:main")
    oid = git(w, "rev-parse", "HEAD")
    closed, secs = poll(lambda: state(w, n)[0] == "closed")
    rec = RECORD["commit_closer"] = {"issue": n, "commit": oid, "closed": bool(closed), "closed_after_s": secs,
                                     "closed_event": closed_event(w, issue.node_id)}
    assert closed
    f = done.facts(w.ag, issues.get(w.ag, issue.ref))
    rec["parsed"] = f["closer"]
    assert f["closer"]["kind"] == "commit" and f["closer"]["oid"] == oid and f["closer"]["repo"] == FULL


# ---- probe (c): the Project's built-in workflows as the product reads them ----
def test_probe_workflows_shape(world):
    w = world
    raw = w.ag.graphql("query { user(login: \"%s\") { projectV2(number: %d) { workflows(first: 50) { totalCount nodes { id name enabled number } } } } }"
                       % (OWNER, PROJECT))["user"]["projectV2"]["workflows"]
    parsed = projects.workflows(w.ag, w.cfg)
    RECORD["workflows"] = {"raw": raw, "parsed": [list(x) for x in parsed]}
    assert {x.name for x in parsed} >= set(done.CLOSING_WORKFLOWS) and len(parsed) == raw["totalCount"]
