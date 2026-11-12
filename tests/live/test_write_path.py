"""T20 (LIVE): the write path against the scratch repo `praveenlavu/ghp-spike-scratch` and its user Project 3, account `praveenlavu`.

One FRESH fictional story per run (label ghp:live-test, a body with an AC section), created over REST with the JSON body on stdin.
The tests run in file order and share that story; at the end every issue the run created is closed (state_reason not_planned).
Nothing here writes outside the scratch repo and Project 3. Every `ghp` verb is driven through `ghp.__main__.main` with a REAL
`Gh` whose runner logs each `gh api` call (time, method, path, write or read), so "zero writes in a dry run" is counted at the
process boundary, not inferred. Findings are collected in RECORD and dumped to $GHP_T20_RECORD (a path) for docs/evidence/T20.md.
"""
import json
import os
import re
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from ghp import comments, issues, projects
from ghp.__main__ import main
from ghp.config import Config
from ghp.gh import Gh, is_graphql_write, subprocess_runner
from ghp.refs import Ref
from live.conftest import OWNER, PROJECT, REPO, REPO_PATH

STAMP = time.strftime("%Y%m%d-%H%M%S")
STORY_BODY = ("# Story\n\nAs a fictional visitor I see the shop's weekend hours (T20 live write-path test).\n\n"
              "## Acceptance criteria\n\n1. AC1 the page lists Saturday and Sunday\n2. AC2 a holiday note is shown\n")
TASKS_MD = "# Tasks\n\n- [ ] T001 Render the weekend hours\n- [ ] T002 [P] Add the holiday note\n- [ ] T003 Check the layout\n"
RECORD = {}


class Log:
    """The calls of one Gh: (epoch seconds at spawn, method, path or `graphql <what>`, is it a write)."""

    def __init__(self):
        self.calls = []

    def runner(self, argv, stdin, timeout):
        i = argv.index("-X")
        method, path = argv[i + 1], argv[i + 2]
        write, label = method not in ("GET", "HEAD"), "%s %s" % (method, path)
        if path == "graphql":
            query = json.loads(stdin)["query"]
            write = is_graphql_write(query)
            m = re.search(r"(?:mutation[^{]*)?\{\s*(\w+)", query)
            label = "graphql %s %s" % ("mutation" if write else "query", m.group(1) if m else "?")
        self.calls.append((time.time(), method, label, write))
        return subprocess_runner(argv, stdin, timeout)

    @property
    def writes(self):
        return [c for c in self.calls if c[3]]


def logged_gh():
    log = Log()
    return Gh(runner=log.runner), log


@pytest.fixture(scope="module")
def world(gh, tmp_path_factory):
    """The fresh story, the working directory with ghp.config.json, and the cleanup. `gh` is the guard (session, account checked)."""
    w = SimpleNamespace(created=[], work=tmp_path_factory.mktemp("t20"), extra_labels=[])
    (w.work / "ghp.config.json").write_text('{"owner": "%s", "repo": "%s", "project_owner": "%s", "project_number": %d}' % (
        OWNER, REPO, OWNER, PROJECT))
    w.ag = Gh()  # setup, direct writes and teardown; a verb under test always gets an UNARMED Gh (the gate arms it)
    w.ag.arm()
    labels_before = {x["name"] for x in w.ag.rest("GET", REPO_PATH + "/labels?per_page=100")}
    RECORD["label_ghp_task_existed_before"] = "ghp:task" in labels_before
    RECORD["label_ghp_live_test_existed_before"] = "ghp:live-test" in labels_before
    made = w.ag.rest("POST", REPO_PATH + "/issues", {"title": "T20 live story: weekend hours (fictional) %s" % STAMP,
                                                     "body": STORY_BODY, "labels": ["ghp:live-test"]})
    w.story_number = made["number"]
    w.created.append(made["number"])
    w.ref = Ref(OWNER, REPO, w.story_number)
    RECORD["story"] = {"number": made["number"], "id": made["id"], "node_id": made["node_id"]}
    try:
        yield w
    finally:  # close the story's sub-issues, every other issue the run created, then the story (state_reason not_planned)
        kids = [i["number"] for i in w.ag.rest("GET", "%s/issues/%d/sub_issues?per_page=100" % (REPO_PATH, w.story_number))]
        for n in dict.fromkeys(kids + w.created[::-1]):
            if w.ag.rest("GET", "%s/issues/%d" % (REPO_PATH, n))["state"] != "closed":
                w.ag.rest("PATCH", "%s/issues/%d" % (REPO_PATH, n), {"state": "closed", "state_reason": "not_planned"})
            RECORD.setdefault("closed", []).append(n)
        for name in w.extra_labels:  # a label this run made up only to see what GitHub does with an unknown one
            if name in {x["name"] for x in w.ag.rest("GET", REPO_PATH + "/labels?per_page=100")}:
                w.ag.rest("DELETE", "%s/labels/%s" % (REPO_PATH, name.replace(":", "%3A")))
                RECORD.setdefault("labels_deleted", []).append(name)
        if os.environ.get("GHP_T20_RECORD"):
            Path(os.environ["GHP_T20_RECORD"]).write_text(json.dumps(RECORD, indent=2, default=str))


@pytest.fixture(autouse=True)
def _in_work_dir(world, monkeypatch):
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    monkeypatch.chdir(world.work)


def run(capsys, *argv, gh=None):
    """(exit code, stdout, stderr, the request log) of `ghp <argv>` with a fresh, UNARMED real Gh (or the one given with its log)."""
    g, log = gh if gh else logged_gh()
    code = main(list(argv), g)
    out, err = capsys.readouterr()
    return code, out, err, log


def comment_list(world):
    return world.ag.rest("GET", "%s/issues/%d/comments?per_page=100" % (REPO_PATH, world.story_number))


def sub_issues(world):
    return world.ag.rest("GET", "%s/issues/%d/sub_issues?per_page=100" % (REPO_PATH, world.story_number))


def board_of(world):
    return projects.discover(world.ag, Config.load(str(world.work)))


def status_of_issue(world, board, number):
    node = world.ag.rest("GET", "%s/issues/%d" % (REPO_PATH, number))["node_id"]
    item = projects.item_for(world.ag, board, node)
    return item, (None if item is None else projects._status_of(world.ag, item))


COMMENT_POST = "POST repos/%s/%s/issues/%d/comments"


# ---- AC1 + AC4 (questions, decisions) ----
def test_ac1_questions_post_then_nothing_the_second_time(world, capsys):
    n = world.story_number
    code, out, err, _ = run(capsys, "dod", str(n))
    assert code == 0 and json.loads(out)["action"] == "created", err
    ledger = world.work / "dod" / ("DoD_%d.md" % n)
    text, anchor = ledger.read_text(), "- **Use cases & edge cases:**"
    assert anchor in text
    ledger.write_text(text.replace(anchor, "- [ ] Q1: Are the weekend hours the same in summer? → _(open)_ · owner: PO\n"
                                           "- [ ] Q2: Is the holiday note translated? → _(open)_ · owner: PO\n" + anchor, 1))
    before = len(comment_list(world))
    # AC4: the dry run sends no write (counted at the process boundary) and posts nothing
    code, out, err, log = run(capsys, "questions", str(n), "--post")
    assert code == 0 and "ADD a new comment" in out and err == ""
    assert log.writes == [] and log.calls and len(comment_list(world)) == before
    RECORD["dry_run_questions"] = {"calls": len(log.calls), "writes": len(log.writes)}
    # apply: exactly one write, one comment
    code, out, err, log = run(capsys, "questions", str(n), "--post", "--apply", gh=logged_gh())
    assert code == 0 and "posted comment" in out, (out, err)
    assert [c[2] for c in log.writes] == [COMMENT_POST % (OWNER, REPO, n)]
    mine = [c for c in comment_list(world) if c["body"].startswith("[spec-kit-ghp:questions] ")]
    assert len(mine) == 1 and "Q1: Are the weekend hours" in mine[0]["body"] and "Q2:" in mine[0]["body"]
    RECORD["questions_comment"] = {"id": mine[0]["id"], "writes_first_apply": len(log.writes)}
    # the second run posts nothing: the preview says so and no write is sent even with --apply
    code, out, err, log = run(capsys, "questions", str(n), "--post")
    assert code == 0 and "unchanged; nothing to post" in out and log.writes == []
    code, out, err, log = run(capsys, "questions", str(n), "--post", "--apply", gh=logged_gh())
    assert code == 0 and "nothing sent: unchanged" in out and log.writes == []
    assert len([c for c in comment_list(world) if c["body"].startswith("[spec-kit-ghp:questions] ")]) == 1
    RECORD["questions_second_apply_writes"] = len(log.writes)


def test_ac1_decisions_post_then_nothing_the_second_time(world, capsys):
    n = world.story_number
    code, out, err, _ = run(capsys, "decisions", str(n), "--add", "Show the hours as a table", "--kind", "decision", "--why", "fictional T20 test")
    assert code == 0, err
    before = len(comment_list(world))
    code, out, err, log = run(capsys, "decisions", str(n), "--post")
    assert code == 0 and "ADD" in out.upper() and log.writes == [] and len(comment_list(world)) == before, (out, err)
    RECORD["dry_run_decisions"] = {"calls": len(log.calls), "writes": len(log.writes)}
    code, out, err, log = run(capsys, "decisions", str(n), "--post", "--apply", gh=logged_gh())
    assert code == 0 and "posted comment" in out, (out, err)
    assert [c[2] for c in log.writes] == [COMMENT_POST % (OWNER, REPO, n)]
    mine = [c for c in comment_list(world) if c["body"].startswith("[spec-kit-ghp:decisions] ")]
    assert len(mine) == 1 and "D1 (decision): Show the hours as a table" in mine[0]["body"]
    RECORD["decisions_comment"] = {"id": mine[0]["id"], "writes_first_apply": len(log.writes)}
    code, out, err, log = run(capsys, "decisions", str(n), "--post", "--apply", gh=logged_gh())
    assert code == 0 and "nothing sent: no unposted decisions" in out and log.writes == []
    assert len([c for c in comment_list(world) if c["body"].startswith("[spec-kit-ghp:decisions] ")]) == 1
    RECORD["decisions_second_apply_writes"] = len(log.writes)


# ---- T14a: the comment POST reply, the fetch right after it, a 60,000 character body ----
def test_t14a_comment_reply_shape_fetch_after_post_and_a_60000_character_body(world):
    g = world.ag
    reply = g.rest("POST", "%s/issues/%d/comments" % (REPO_PATH, world.story_number), {"body": "T20 fictional comment: reply shape check."})
    # what comments.py reads from a comment: add() the integer id; fetch() id, node_id, user.{id,login,type}, performed_via_github_app
    assert type(reply["id"]) is int and isinstance(reply["node_id"], str) and "performed_via_github_app" in reply
    assert type(reply["user"]["id"]) is int and reply["user"]["type"] == "User" and reply["user"]["login"] == OWNER
    RECORD["comment_reply_keys"] = sorted(reply)
    RECORD["comment_reply_performed_via_github_app"] = reply["performed_via_github_app"]
    seen = comments.fetch(g, world.ref)  # immediately: the REST + GraphQL join must see the new comment
    assert reply["id"] in [c.id for c in seen] and not seen.notes, seen.notes
    mine = [c for c in seen if c.id == reply["id"]][0]
    assert mine.author.id == reply["user"]["id"] and mine.viewer_did_author and mine.edit_count == 0 and mine.app is None
    RECORD["fetch_after_post"] = {"sees_new_comment": True, "notes": [str(x) for x in seen.notes]}
    # 60,000 characters: the largest body ghp posts (comments.MAX_COMMENT), once, through the product's own writer
    big = ("T20 fictional filler line.\n" * 2400)[:comments.MAX_COMMENT]
    assert len(big) == 60000
    big_id = comments.add(g, world.ref, big)
    got = [c for c in comments.fetch(g, world.ref) if c.id == big_id]
    assert len(got) == 1 and len(got[0].body) == 60000
    RECORD["comment_60000"] = {"id": big_id, "stored_length": len(got[0].body), "equal": got[0].body == big}


# ---- AC2 + AC4 + AC5: push ----
def test_ac2_push_preview_apply_three_tasks_then_nothing(world, capsys):
    n = world.story_number
    (world.work / "tasks.md").write_text(TASKS_MD)
    board = board_of(world)
    assert len(sub_issues(world)) == 0
    # dry run: previews three creates, sends no write
    code, out, err, log = run(capsys, "push", str(n), "--from", "tasks.md")
    assert code == 0 and "T001" in out and "T002" in out and "T003" in out and log.writes == [], (out, err)
    RECORD["push_preview"] = out
    assert len(sub_issues(world)) == 0
    RECORD["dry_run_push"] = {"calls": len(log.calls), "writes": len(log.writes)}
    # apply
    code, out, err, log = run(capsys, "push", str(n), "--from", "tasks.md", "--apply", gh=logged_gh())
    assert code == 0 and "created 3, updated 0, failed 0" in out, (out, err)
    RECORD["push_apply_output"] = out
    kids = sub_issues(world)
    assert len(kids) == 3
    world.created += [k["number"] for k in kids]
    kids.sort(key=lambda k: k["title"])
    assert [k["title"] for k in kids] == ["T001 Render the weekend hours", "T002 Add the holiday note", "T003 Check the layout"]
    for k in kids:  # every task is on the board at Todo (an independent read)
        item, status = status_of_issue(world, board, k["number"])
        assert item and status and status[0] == "Todo", (k["number"], status)
    # AC5: the timestamp of each write
    w = log.writes
    gaps = [b[0] - a[0] for a, b in zip(w, w[1:])]
    span = w[-1][0] - w[0][0]
    RECORD["push_writes"] = {"count": len(w), "offsets_s_and_calls": [(round(t - w[0][0], 2), label) for t, _, label, _ in w],
                             "span_s": round(span, 2), "min_gap_s": round(min(gaps), 2),
                             "max_in_any_60s_window": max(sum(1 for t, *_ in w if s <= t < s + 60) for s, *_ in w),
                             "extrapolated_per_minute": round(len(gaps) / span * 60, 1), "reads": len(log.calls) - len(w)}
    assert min(gaps) >= 1.0 and RECORD["push_writes"]["max_in_any_60s_window"] < 60 and RECORD["push_writes"]["extrapolated_per_minute"] < 60
    node = world.ag.rest("GET", "%s/issues/%d" % (REPO_PATH, kids[2]["number"]))["node_id"]  # who set Todo: automation, push, or both
    RECORD["task_status_events"] = [[e.from_, e.to, e.actor_login, e.is_automation] for e in projects.status_events(world.ag, node)]
    RECORD["sub_issue_ids"] = {k["number"]: k["id"] for k in kids}
    # (e) the hidden markers survive the API round trip, byte for byte
    for k, tid in zip(kids, ("T001", "T002", "T003")):
        assert k["body"].startswith("Task %s of #%d, pushed from tasks.md by ghp.\n\n<!-- spec-kit-ghp:task:%s -->\n<!-- spec-kit-ghp:story:%d -->"
                                    % (tid, n, tid, n)), k["body"]
        assert re.search(r"\n\n<!-- ghp:create:[0-9a-f]{32} -->$", k["body"]), k["body"]
        assert [x["name"] for x in k["labels"]] == ["ghp:task"]
    RECORD["task_body_example"] = kids[0]["body"]
    # second apply: creates nothing, sends no write
    code, out, err, log = run(capsys, "push", str(n), "--from", "tasks.md")
    assert code == 0 and log.writes == []
    code, out, err, log = run(capsys, "push", str(n), "--from", "tasks.md", "--apply", gh=logged_gh())
    assert code == 0 and "created 0, updated 0, failed 0" in out, (out, err)
    assert log.writes == [] and len(sub_issues(world)) == 3
    RECORD["push_second_apply_writes"] = len(log.writes)


# ---- AC3 + AC4: activate ----
def test_ac3_activate_moves_todo_to_in_progress_with_read_back_and_a_second_run_is_a_noop(world, capsys):
    board = board_of(world)
    t1 = sorted(sub_issues(world), key=lambda k: k["title"])[0]["number"]
    item, now = status_of_issue(world, board, t1)
    assert now[0] == "Todo"
    code, out, err, log = run(capsys, "activate", str(t1))  # dry run
    assert code == 0 and "Status: Todo -> In Progress" in out and log.writes == [], (out, err)
    assert status_of_issue(world, board, t1)[1][0] == "Todo"
    RECORD["dry_run_activate"] = {"calls": len(log.calls), "writes": len(log.writes)}
    code, out, err, log = run(capsys, "activate", str(t1), "--apply", gh=logged_gh())
    assert code == 0 and "moved #%d to In Progress" % t1 in out, (out, err)
    assert [c[2] for c in log.writes] == ["graphql mutation updateProjectV2ItemFieldValue"]
    item2, now = status_of_issue(world, board, t1)
    assert item2 == item and now == ("In Progress", board.options["In Progress"])  # an independent read-back
    RECORD["activate_task"] = {"number": t1, "item": item, "writes": [c[2] for c in log.writes]}
    # already In Progress: a no-op, no write
    code, out, err, log = run(capsys, "activate", str(t1), "--apply", gh=logged_gh())
    assert code == 0 and "nothing sent: already In Progress" in out and log.writes == []
    RECORD["activate_noop_writes"] = len(log.writes)
    # the story itself is not on the board yet: added (the board's own workflow may set Todo), then In Progress
    n = world.story_number
    assert status_of_issue(world, board, n)[0] is None
    code, out, err, log = run(capsys, "activate", str(n), "--apply", gh=logged_gh())
    assert code == 0 and "moved #%d to In Progress" % n in out, (out, err)
    assert [c[2] for c in log.writes] == ["graphql mutation addProjectV2ItemById", "graphql mutation updateProjectV2ItemFieldValue"]
    assert status_of_issue(world, board, n)[1][0] == "In Progress"
    node = world.ag.rest("GET", "%s/issues/%d" % (REPO_PATH, n))["node_id"]
    RECORD["story_status_events"] = [[e.from_, e.to, e.actor_login, e.is_automation] for e in projects.status_events(world.ag, node)]
    RECORD["activate_story_writes"] = [c[2] for c in log.writes]


# ---- T17: what GitHub does with the primitives ----
def test_t17_unknown_label_repeated_sub_issue_post_list_lag_and_a_repeated_add_to_the_board(world):
    g, cfg = world.ag, Config.load(str(world.work))
    story = issues.get(g, world.ref)
    label = "ghp:t20-unknown-%s" % uuid.uuid4().hex[:8]
    world.extra_labels.append(label)  # deleted at the end if GitHub created it
    try:
        made = issues.create(g, cfg, "T20 standalone issue: unknown label (fictional)", "Fictional body.", ["ghp:live-test", label], None)
        RECORD["unknown_label"] = {"outcome": "created", "labels": made.labels}
    except issues.IssueError as e:
        made = e.issue
        RECORD["unknown_label"] = {"outcome": "dropped", "code": e.code, "detail": str(e)}
    world.created.append(made.ref.number)
    RECORD["unknown_label"]["label_exists_after"] = label in {x["name"] for x in g.rest("GET", REPO_PATH + "/labels?per_page=100")}
    # issue list lag: is the issue created a moment ago in the newest-100 list? Poll every 0.25 s for up to 10 s
    newest, started, polls = [], time.monotonic(), 0
    while time.monotonic() - started < 10:
        polls += 1
        newest = [i["number"] for i in g.rest("GET", REPO_PATH + "/issues?state=all&sort=created&direction=desc&per_page=100")]
        if made.ref.number in newest:
            break
        time.sleep(0.25)
    RECORD["list_lag"] = {"first_read_had_it": polls == 1 and made.ref.number in newest, "polls": polls,
                          "seconds_until_listed": round(time.monotonic() - started, 2) if made.ref.number in newest else None,
                          "position": newest.index(made.ref.number) if made.ref.number in newest else None,
                          "page_size": issues.PAGE, "push_search_window": push_window()}
    assert made.ref.number in newest
    # a repeated sub-issue POST (the same child twice): reply shape and status of both
    path = "%s/issues/%d/sub_issues" % (REPO_PATH, story.ref.number)
    first = g.rest("POST", path, {"sub_issue_id": made.id})
    RECORD["sub_issue_post_first"] = {"keys": sorted(first), "number": first.get("number")}
    try:
        again = g.rest("POST", path, {"sub_issue_id": made.id})
        RECORD["sub_issue_post_second"] = {"outcome": "accepted", "keys": sorted(again) if isinstance(again, dict) else again}
    except Exception as e:  # a GhError: record its status and text
        RECORD["sub_issue_post_second"] = {"outcome": "rejected", "status": getattr(e, "status", None), "error": str(e)}
    RECORD["sub_issue_post_second"]["times_listed_as_child"] = [k["number"] for k in sub_issues(world)].count(made.ref.number)
    # addProjectV2ItemById on an issue already on the board returns the same item id
    board = board_of(world)
    t2 = [k for k in sub_issues(world) if k["title"].startswith("T002")][0]
    item_before = projects.item_for(g, board, t2["node_id"])
    item_again = projects.add_item(g, board, issues.get(g, Ref(OWNER, REPO, t2["number"])))
    RECORD["add_item_twice"] = {"item_before": item_before, "item_from_second_add": item_again, "same": item_before == item_again}
    assert item_before == item_again


def push_window():
    from ghp import push
    return push.SEARCH_PAGES * issues.PAGE


# ---- T18 (d): the token ----
def test_t18_get_user_works_with_this_token(gh):
    me = gh.viewer()
    assert me.login == OWNER and type(me.id) is int
    RECORD["get_user"] = {"login": me.login, "id": me.id, "scopes": me.scopes}


# ---- AC4 over the whole run ----
def test_ac4_every_dry_run_recorded_zero_writes():
    for key in ("dry_run_questions", "dry_run_decisions", "dry_run_push", "dry_run_activate"):
        assert RECORD[key]["writes"] == 0 and RECORD[key]["calls"] > 0, key
