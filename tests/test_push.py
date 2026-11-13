"""T18: `ghp push`, tasks.md mirrored as sub-issues of a story (ado's push, rewritten for sub-issues and Project items).
`Hub` is a tiny stateful GitHub (issues, sub-issues, one Project) behind the FakeGh gate, so a re-run really sees what the first run did."""
import copy
import json
import os
import re
import time
from collections import Counter

import pytest

import ghp.__main__ as cli
from conftest import FakeGh
from ghp import issues, projects, push
from ghp.config import Config
from ghp.gh import GhError, GhTimeout, Viewer, check_rest_request, is_graphql_write
from ghp.refs import Ref
from ghp.tasks import parse_tasks
from test_issues_write import labelled, raw
from test_projects import fx

CFG = Config("octo", "demo", project_number=3)
CFG_NO_BOARD = Config("octo", "demo")
BASE = "repos/octo/demo/issues"
PROJECT = "PVT_kwFictional0003"
OPTIONS = {"Todo": "f75ad846", "In Progress": "47fc9ee4", "Done": "98236657"}
STORY_URL = "https://api.github.com/repos/octo/demo/issues/1"
LABEL_PATH = "repos/octo/demo/labels/ghp%3Atask"
GAP = push.WRITE_GAP  # the shipped value, read before the autouse fixture below zeroes it for the CLI tests


@pytest.fixture(autouse=True)
def _no_real_waiting(monkeypatch):
    monkeypatch.setattr(push, "WRITE_GAP", 0)


@pytest.fixture(autouse=True)
def _no_real_re_read_waiting(monkeypatch):
    monkeypatch.setattr(issues, "_nap", lambda _s: None)


def T(id, title, done=False):
    return {"id": id, "title": title, "done": done}


def marked(tid, story=1):
    return "Task %s\n\n<!-- spec-kit-ghp:task:%s -->\n<!-- spec-kit-ghp:story:%d -->" % (tid, tid, story)


class Hub(FakeGh):
    """Issue 1 is the story. `fail[(kind, k)] = exc` raises on the k-th write of `kind` (create, link, retitle, add_item, set_status)
    before it is applied; `fail_after` raises after it was applied (the reply was lost)."""

    def __init__(self, clock=None):
        super().__init__(viewer=Viewer(1001, "octo-author", None))  # 1001 wrote every seeded issue (the fixture's author)
        self.issues, self.next, self.items, self.status = {}, 10, {}, {}
        self.fail, self.fail_after, self.count, self.writes, self.clock = {}, {}, Counter(), [], clock
        self.find_broken = False
        self.label = {"id": 1, "name": "ghp:task", "color": "ededed"}  # None = the repo has no such label (404)
        self.made = set()  # numbers of the issues this hub created
        self.lag = 0  # the newest-issues list hides issues of this run for this many reads (GitHub's list lags)
        self.issues[1] = raw(1, title="A fictional story", body="the story")

    # -- seeding
    def seed(self, tid, n, title=None, *, parent=True, story=1, on_board=False, status=None, state="open", body=None, pr=False, author=1001, labels=()):
        d = raw(n, title=title or "%s task" % tid, body=marked(tid, story) if body is None else body, state=state,
                parent_issue_url=STORY_URL if parent is True else parent or None)
        d["user"]["id"] = author
        d["labels"] = labelled(labels)
        if pr:
            d["pull_request"] = {"url": "x"}
        self.issues[n] = d
        if on_board:
            self.items[d["node_id"]] = "PVTI_%04d" % n
            if status:
                self.status["PVTI_%04d" % n] = status
        return d

    # -- the transport
    def _write(self, kind):
        self.count[kind] += 1
        self.writes.append((self.clock.now if self.clock else None, kind))
        exc = self.fail.pop((kind, self.count[kind]), None)
        if exc:
            raise exc

    def _after(self, kind):
        exc = self.fail_after.pop((kind, self.count[kind]), None)
        if exc:
            raise exc

    def rest(self, method, path, body=None):
        method = method.upper()
        check_rest_request(method, path, body)
        self._gate(method != "GET")
        self.calls.append(("rest", method, path, body))
        if path == LABEL_PATH:
            assert method == "GET"
            if self.label is None:
                raise GhError("E_GH_HTTP", 404, "Not Found")
            return copy.deepcopy(self.label)
        m = re.fullmatch(r"repos/octo/demo/issues(?:/(\d+))?(/sub_issues)?(?:\?(.*))?", path)
        assert m, "unexpected path " + path
        n, sub, query = m.groups()
        page = int(re.search(r"(?:^|&)page=(\d+)", query).group(1)) if query and re.search(r"(?:^|&)page=", query) else 1
        if method == "GET" and sub:
            kids = [d for k, d in sorted(self.issues.items()) if d.get("parent_issue_url") == "https://api.github.com/repos/octo/demo/issues/" + n]
            return copy.deepcopy(kids[(page - 1) * 100: page * 100])
        if method == "GET" and n is None:
            if self.find_broken and not re.search(r"(?:^|&)page=", query):
                raise GhTimeout()
            newest = [d for _, d in sorted(self.issues.items(), reverse=True)]
            if self.lag > 0:
                self.lag -= 1
                newest = [d for d in newest if d["number"] not in self.made]
            return copy.deepcopy(newest[(page - 1) * 100: page * 100])
        if method == "GET":
            return copy.deepcopy(self.issues[int(n)])
        if method == "POST" and n is None:
            self._write("create")
            parent = next((d for d in self.issues.values() if d["id"] == body.get("parent_issue_id")), None)
            num, self.next = self.next, self.next + 1
            d = raw(num, title=body["title"], body=body["body"], labels=labelled(body["labels"]),
                    parent_issue_url=None if parent is None else "https://api.github.com/repos/octo/demo/issues/%d" % parent["number"])
            self.issues[num] = d
            self.made.add(num)
            self._after("create")
            return copy.deepcopy(d)
        if method == "POST":
            self._write("link")
            child = next(d for d in self.issues.values() if d["id"] == body["sub_issue_id"])
            child["parent_issue_url"] = "https://api.github.com/repos/octo/demo/issues/" + n
            self._after("link")
            return copy.deepcopy(self.issues[int(n)])
        assert method == "PATCH", method
        self._write("retitle")
        assert set(body) == {"title"}, body
        self.issues[int(n)]["title"] = body["title"]
        self._after("retitle")
        return copy.deepcopy(self.issues[int(n)])

    def graphql(self, query, variables=None):
        self._gate(is_graphql_write(query))
        self.calls.append(("graphql", query, variables))
        if "addProjectV2ItemById" in query:
            self._write("add_item")
            item = self.items.setdefault(variables["content"], "PVTI_" + variables["content"][-4:])
            self._after("add_item")
            return {"addProjectV2ItemById": {"item": {"id": item, "isArchived": False}}}
        if "updateProjectV2ItemFieldValue" in query:
            self._write("set_status")
            self.status[variables["item"]] = next(k for k, v in OPTIONS.items() if v == variables["option"])
            self._after("set_status")
            return {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": variables["item"]}}}
        if "projectItems(" in query:
            item = self.items.get(variables["issue"])
            nodes = [] if item is None else [{"id": item, "project": {"id": PROJECT}}]
            return {"node": {"projectItems": {"pageInfo": {"hasNextPage": False, "endCursor": "c"}, "nodes": nodes}}}
        if "fieldValueByName" in query:
            name = self.status.get(variables["item"])
            return {"node": {"fieldValueByName": None if name is None else {"name": name, "optionId": OPTIONS[name]}}}
        assert "fields(" in query, query[:60]
        return fx("project_fields")

    # -- what a test asks
    def writes_of(self, kind=None):
        return [k for _, k in self.writes if kind in (None, k)]

    def sent(self):
        return [c for c in self.calls if (c[0] == "rest" and c[1] != "GET") or (c[0] == "graphql" and is_graphql_write(c[1]))]

    def children(self):
        return {d["number"]: d for d in self.issues.values() if d.get("parent_issue_url") == STORY_URL}


def story_of(hub):
    return issues._issue(hub.issues[1], Ref("octo", "demo", 1))


def plan(hub, tasks, cfg=CFG):
    board = projects.discover(hub, cfg) if cfg.project_number else None
    return push.plan_push(hub, cfg, story_of(hub), tasks, board), board


def push_all(hub, tasks, cfg=CFG, pace=None):
    ops, board = plan(hub, tasks, cfg)
    hub.arm()
    return push.apply_push(hub, cfg, story_of(hub), ops, board, pace or push.Pacer())


def actions(ops):
    return {o["task_id"]: o["action"] for o in ops}


# ---- AC1: the plan, matched by marker ----
def test_plan_matches_tasks_by_marker():
    hub = Hub()
    hub.seed("T001", 11, "A title nobody wrote in tasks.md", on_board=True, status="Todo")  # marker matches, title does not
    hub.seed("T002", 12, "T002 Wire the service", on_board=True, status="Todo")             # exact title and board item: unchanged
    hub.issues[13] = raw(13, title="T003 Build the view", body="a human made this one, no marker", parent_issue_url=STORY_URL)  # same title as a task, no marker
    ops, _ = plan(hub, [T("T001", "Create model"), T("T002", "Wire the service"), T("T003", "Build the view")])
    got = {o["task_id"]: o for o in ops}
    assert actions(ops) == {"T001": "update", "T002": "unchanged", "T003": "create"}
    assert got["T001"]["number"] == 11 and got["T001"]["old_title"] == "A title nobody wrote in tasks.md" and got["T001"]["title"] == "T001 Create model"
    assert got["T002"]["number"] == 12 and got["T003"]["number"] is None
    assert [c for c in hub.calls if c[1] != "GET" and c[0] == "rest"] == []  # planning is read only


def test_plan_names_the_sub_issue_relation_and_the_project_item():
    hub = Hub()
    hub.seed("T001", 11, "T001 A", on_board=True, status="Todo")
    hub.seed("T002", 12, "T002 B", parent=False, on_board=False)  # created, never linked, not on the board
    hub.seed("T003", 13, "T003 C", on_board=True)                  # on the board with no Status
    ops, board = plan(hub, [T("T001", "A"), T("T002", "B"), T("T003", "C"), T("T004", "D")])
    got = {o["task_id"]: o for o in ops}
    assert [(got[t]["rel"], got[t]["item"]) for t in ("T001", "T002", "T003", "T004")] == [
        ("linked", "ok"), ("link", "add"), ("linked", "status"), ("new", "add")]
    text = push.render_plan(story_of(hub), ops, board)
    assert "| create | T004 |" in text and "| update | T002 |" in text and "| update | T003 |" in text
    row = next(l for l in text.splitlines() if l.startswith("| create | T004"))
    assert "new sub-issue of #1" in row and "add to the Project, Todo" in row
    assert "link to #1" in next(l for l in text.splitlines() if l.startswith("| update | T002"))
    assert "T001" in text.split("unchanged:")[1]
    assert text == push.render_plan(story_of(hub), ops, board)  # deterministic: --expect-plan compares it


def test_body_is_not_compared_so_the_create_marker_and_human_edits_change_nothing():
    hub = Hub()
    hub.seed("T001", 11, "T001 A", on_board=True, status="Todo", body=marked("T001") + "\n\nmy own notes\n\n<!-- ghp:create:0123456789abcdef -->")
    ops, _ = plan(hub, [T("T001", "A")])
    assert actions(ops) == {"T001": "unchanged"}


def test_a_closed_child_is_matched_not_recreated_and_checked_tasks_are_not_closed():
    hub = Hub()
    hub.seed("T001", 11, "T001 A", on_board=True, status="Todo", state="closed")
    ops, _ = plan(hub, [T("T001", "A", done=True), T("T002", "B", done=True)])
    assert actions(ops) == {"T001": "unchanged", "T002": "create"}
    push_all(hub, [T("T001", "A", done=True), T("T002", "B", done=True)])
    assert hub.writes_of("retitle") == [] and not any(c[1] == "PATCH" for c in hub.calls if c[0] == "rest")  # push never opens or closes
    assert hub.status.get("PVTI_0010") == "Todo"


def test_orphan_is_reported_and_never_touched():
    hub = Hub()
    hub.seed("T009", 19, "T009 Gone", on_board=True, status="Todo")
    ops, board = plan(hub, [T("T001", "A")])
    assert actions(ops) == {"T001": "create", "T009": "orphan"}
    assert "orphan 1" in push.render_plan(story_of(hub), ops, board)
    push_all(hub, [T("T001", "A")])
    assert hub.issues[19]["title"] == "T009 Gone" and hub.writes_of("retitle") == []


def test_the_same_marker_on_two_issues_refuses_the_push_before_any_write():
    hub = Hub()
    hub.seed("T001", 11, "T001 A")
    hub.seed("T001", 12, "T001 A copy")
    with pytest.raises(push.PushError) as e:
        plan(hub, [T("T001", "A")])
    assert e.value.code == "E_TASK_MARKER_AMBIGUOUS" and "#11" in str(e.value) and "#12" in str(e.value)
    assert hub.sent() == []


def test_one_issue_with_two_task_markers_is_refused():
    hub = Hub()
    hub.seed("T001", 11, body=marked("T001") + "\n<!-- spec-kit-ghp:task:T002 -->")
    with pytest.raises(push.PushError) as e:
        plan(hub, [T("T001", "A")])
    assert e.value.code == "E_TASK_MARKER_AMBIGUOUS"


def test_a_stranger_pull_request_with_a_task_marker_does_not_block_the_push():
    hub = Hub()
    hub.seed("T001", 11, "T001 A", parent=False, pr=True, author=999999)
    hub.seed("T001", 12, "T001 A", parent=False, pr=True)  # even one of ours: a PR is never a task
    ops, _ = plan(hub, [T("T001", "A")])
    assert actions(ops) == {"T001": "create"}


def test_a_task_issue_that_sits_under_another_parent_refuses_the_push():
    hub = Hub()
    hub.seed("T001", 11, "T001 A", parent="https://api.github.com/repos/octo/demo/issues/5")  # ours (same author), so it counts
    with pytest.raises(push.PushError) as e:
        plan(hub, [T("T001", "A")])
    assert e.value.code == "E_TASK_ELSEWHERE"


def test_a_child_in_another_repo_with_a_task_marker_refuses_the_push():
    hub = Hub()
    d = hub.seed("T001", 11, "T001 A")
    d["repository_url"] = "https://api.github.com/repos/octo/other"
    with pytest.raises(push.PushError) as e:
        plan(hub, [T("T001", "A")])
    assert e.value.code == "E_FOREIGN_REPO"


def test_another_storys_task_with_the_same_id_is_not_adopted():
    hub = Hub()
    hub.seed("T001", 11, "T001 Other story's", parent=False, story=7)  # unlinked, marked for story 7
    ops, _ = plan(hub, [T("T001", "A")])
    assert actions(ops) == {"T001": "create"}


def test_a_marked_issue_of_a_broken_shape_is_refused_and_unmarked_ones_are_ignored():
    hub = Hub()
    hub.issues[11] = raw(11, body=None)
    hub.issues[12] = {"number": 12, "body": "<!-- spec-kit-ghp:task:T001 --><!-- spec-kit-ghp:story:1 -->"}  # marked but not an issue shape
    with pytest.raises(GhError) as e:  # a marked issue GitHub sent in a broken shape: refuse, never guess
        plan(hub, [T("T001", "A")])
    assert e.value.code == "E_GH_BAD_BODY"
    del hub.issues[12]
    ops, _ = plan(hub, [T("T001", "A")])
    assert actions(ops) == {"T001": "create"}


def test_duplicate_task_ids_and_non_ascii_ids_are_refused_before_any_call():
    hub = Hub()
    for tasks in ([T("T001", "A"), T("T001", "B")], [T("T١٢٣", "A")], [T("T" + "1" * 30, "A")]):
        with pytest.raises(ValueError):
            push.plan_push(hub, CFG, story_of(hub), tasks, None)
    assert hub.calls == []


def test_plan_escapes_untrusted_titles_and_caps_their_size():
    hub = Hub()
    nasty = "evil\x1b[31m‮RTL | pipe \\ back\nnewline " + "x" * 400
    ops, board = plan(hub, [T("T001", nasty)])
    text = push.render_plan(story_of(hub), ops, board)
    title = ops[0]["title"]
    assert "\x1b" not in text and "‮" not in text
    hub.seed("T001", 11, "old\x1b[31m‮ name", on_board=True, status="Todo")  # a title that came from GitHub is escaped, not trusted
    ops2, board = plan(hub, [T("T001", "A")])
    text2 = push.render_plan(story_of(hub), ops2, board)
    assert "\x1b" not in text2 and "‮" not in text2 and "\\u001b" in text2 and "\\u202e" in text2
    row = next(l for l in text.splitlines() if l.startswith("| create"))
    assert len(re.findall(r"(?<!\\)(?:\\\\)*\|", row)) == 7 and "\n" not in row and len(row) < 400
    assert len(title) == 256 and title.endswith("…") and title.isprintable()


def test_the_plan_is_cut_for_the_gate_not_for_the_reader_unchanged_ids_share_one_line():
    hub = Hub()
    for i in range(1, 91):
        hub.seed("T%03d" % i, 100 + i, "T%03d t" % i, on_board=True, status="Todo")
    ops, board = plan(hub, [T("T%03d" % i, "t") for i in range(1, 91)] + [T("T091", "new")])
    assert len(push.render_plan(story_of(hub), ops, board).splitlines()) < 30


# ---- who may carry a marker: the markers are plain text anyone can copy ----
def test_a_strangers_issue_with_copied_markers_is_not_adopted_retitled_or_linked():
    hub = Hub()
    hub.seed("T002", 50, "their own title", parent=False, author=999999, body="pls fix\n" + marked("T002"))
    tasks = [T("T001", "A"), T("T002", "B")]
    ops, _ = plan(hub, tasks)
    assert actions(ops) == {"T001": "create", "T002": "create"}  # a new T002 is made; #50 is left alone
    push_all(hub, tasks)
    assert hub.issues[50]["title"] == "their own title" and 50 not in hub.children() and hub.count["retitle"] == 0 and hub.count["create"] == 2


def test_the_story_whose_own_body_carries_the_markers_is_never_patched():
    hub = Hub()
    hub.issues[1]["body"] = "the story\n" + marked("T001")
    ops, _ = plan(hub, [T("T001", "A")])
    assert actions(ops) == {"T001": "create"}
    push_all(hub, [T("T001", "A")])
    assert hub.issues[1]["title"] == "A fictional story" and hub.count["retitle"] == 0


def test_own_issue_whose_label_was_dropped_is_still_resumed_and_a_labelled_strangers_one_is_too():
    hub = Hub()
    hub.seed("T001", 11, "T001 A", parent=False, labels=())  # ours (viewer 1001), no label
    hub.seed("T002", 12, "T002 B", parent=False, author=999999, labels=("GHP:Task",))  # label set by someone with push access, any case
    ops, _ = plan(hub, [T("T001", "A"), T("T002", "B")])
    assert [(o["task_id"], o["action"], o["rel"]) for o in ops] == [("T001", "update", "link"), ("T002", "update", "link")]
    push_all(hub, [T("T001", "A"), T("T002", "B")])
    assert hub.count["create"] == 0 and sorted(hub.children()) == [11, 12]


def test_a_strangers_copy_beside_our_issue_is_ignored_but_two_eligible_ones_refuse():
    hub = Hub()
    hub.seed("T001", 11, "T001 A", parent=False)
    hub.seed("T001", 12, "copy", parent=False, author=999999)
    ops, _ = plan(hub, [T("T001", "A")])
    assert ops[0]["number"] == 11
    hub.seed("T001", 13, "T001 A again", parent=False, labels=("ghp:task",))
    with pytest.raises(push.PushError) as e:
        plan(hub, [T("T001", "A")])
    assert e.value.code == "E_TASK_MARKER_AMBIGUOUS" and hub.sent() == []


def test_the_viewer_is_read_only_when_an_unlinked_candidate_needs_it():
    hub = Hub()
    hub._viewer = None  # would assert if asked
    plan(hub, [T("T001", "A")])


# ---- AC2: dry run, apply ----
def cli_repo(tmp_path, monkeypatch, tasks, project=3):
    cfg = {"owner": "octo", "repo": "demo"}
    if project:
        cfg["project_number"] = project
    (tmp_path / "ghp.config.json").write_text(json.dumps(cfg))
    (tmp_path / "tasks.md").write_text("".join("- [%s] %s %s\n" % ("x" if t["done"] else " ", t["id"], t["title"]) for t in tasks))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)


TASKS = [T("T001", "Create model"), T("T002", "Wire service"), T("T003", "Build view")]


def test_dry_run_sends_nothing(tmp_path, monkeypatch, capsys):
    cli_repo(tmp_path, monkeypatch, TASKS)
    hub = Hub()
    assert cli.main(["push", "1", "--from", "tasks.md", "--plan-out", "plan.md"], gh=hub) == 0
    out = capsys.readouterr().out
    assert not hub.armed and hub.sent() == [] and hub.writes == []
    assert "create 3" in out and "| create | T001 |" in out and (tmp_path / "plan.md").read_text() + "\n" == out
    assert not any(c[0] == "graphql" and is_graphql_write(c[1]) for c in hub.calls)


def test_a_task_line_that_is_not_read_as_a_task_is_named_in_the_preview(tmp_path, monkeypatch, capsys):
    cli_repo(tmp_path, monkeypatch, TASKS)
    with open(tmp_path / "tasks.md", "a") as f:
        f.write("- [ ] TX1 Bad id\n- [x]T004 no space\n- [ ] not a task at all\n")
    hub = Hub()
    assert cli.main(["push", "1", "--from", "tasks.md"], gh=hub) == 0
    out = capsys.readouterr().out
    assert "create 3" in out and hub.sent() == []  # what is written does not change
    assert "line 4 is not read as a task" in out and "TX1 Bad id" in out and "line 5" in out and "line 6" not in out


def test_apply_creates_links_and_adds_items(tmp_path, monkeypatch, capsys):
    cli_repo(tmp_path, monkeypatch, TASKS)
    hub = Hub()
    assert cli.main(["push", "1", "--from", "tasks.md", "--apply"], gh=hub) == 0
    assert hub.armed and hub.count == Counter(create=3, add_item=3, set_status=3)
    creates = [c[3] for c in hub.calls if c[:2] == ("rest", "POST")]
    assert [b["title"] for b in creates] == ["T001 Create model", "T002 Wire service", "T003 Build view"]
    assert all(b["labels"] == ["ghp:task"] and b["parent_issue_id"] == hub.issues[1]["id"] for b in creates)
    assert all(re.search(r"<!-- spec-kit-ghp:task:T00\d -->", b["body"]) and "<!-- spec-kit-ghp:story:1 -->" in b["body"] for b in creates)
    assert sorted(hub.children()) == [10, 11, 12]  # linked as sub-issues of the story
    assert set(hub.status.values()) == {"Todo"} and len(hub.status) == 3  # every new item starts at the configured Todo, never Done
    out = capsys.readouterr().out
    assert "created 3" in out
    # a second apply changes nothing
    hub2_calls = len(hub.sent())
    assert cli.main(["push", "1", "--from", "tasks.md", "--apply"], gh=hub) == 0 and len(hub.sent()) == hub2_calls


def test_todo_is_the_configured_name_and_done_is_never_sent(tmp_path, monkeypatch):
    cli_repo(tmp_path, monkeypatch, TASKS[:1])
    hub = Hub()
    cli.main(["push", "1", "--from", "tasks.md", "--apply"], gh=hub)
    statuses = [c[2]["option"] for c in hub.calls if c[0] == "graphql" and "updateProjectV2ItemFieldValue" in c[1]]
    assert statuses == [OPTIONS["Todo"]]


def test_without_a_configured_project_push_proceeds_and_adds_no_items(tmp_path, monkeypatch, capsys):
    cli_repo(tmp_path, monkeypatch, TASKS, project=None)
    hub = Hub()
    assert cli.main(["push", "1", "--from", "tasks.md", "--apply"], gh=hub) == 0
    assert hub.count == Counter(create=3) and not any(c[0] == "graphql" for c in hub.calls)
    assert "no Project is configured" in capsys.readouterr().out


def test_a_configured_project_that_cannot_be_read_refuses_before_any_write(tmp_path, monkeypatch, capsys):
    cli_repo(tmp_path, monkeypatch, TASKS)
    hub = Hub()
    data = fx("project_fields")
    data["user"]["projectV2"]["fields"]["nodes"][2]["options"] = [{"id": "f75ad846", "name": "Todo"}]  # no In Progress, no Done
    hub.graphql = lambda q, v=None: data  # type: ignore
    assert cli.main(["push", "1", "--from", "tasks.md", "--apply"], gh=hub) == 2
    assert "E_STATUS_OPTION_MISSING" in capsys.readouterr().err and hub.sent() == [] and not hub.armed


def test_apply_with_unwritable_status_stops_and_says_what_exists(tmp_path, monkeypatch, capsys):
    cli_repo(tmp_path, monkeypatch, TASKS)
    hub = Hub()
    hub.fail[("set_status", 1)] = GhError("E_GH_HTTP", 403, "forbidden")
    assert cli.main(["push", "1", "--from", "tasks.md", "--apply"], gh=hub) == 1
    out = capsys.readouterr().out
    assert "T001" in out and "#10" in out and "stopped" in out
    assert hub.count == Counter(create=1, add_item=1, set_status=1)  # stopped at the first failure: nothing else was written


def test_the_story_must_be_an_issue_and_the_tasks_file_must_be_a_confined_regular_file(tmp_path, monkeypatch, capsys):
    cli_repo(tmp_path, monkeypatch, TASKS)
    hub = Hub()
    hub.issues[1]["pull_request"] = {"url": "x"}
    assert cli.main(["push", "1", "--from", "tasks.md"], gh=hub) == 2
    del hub.issues[1]["pull_request"]
    outside = tmp_path.parent / ("outside-%d.md" % os.getpid())
    outside.write_text("- [ ] T001 x\n")
    (tmp_path / "link.md").symlink_to(outside)
    for bad in ("link.md", str(outside), "missing.md"):
        hub2 = Hub()
        assert cli.main(["push", "1", "--from", bad], gh=hub2) == 2 and hub2.calls == []
    outside.unlink()


def test_a_pull_request_number_without_a_ledger_is_named_as_one(tmp_path, monkeypatch, capsys):
    cli_repo(tmp_path, monkeypatch, TASKS)  # no ledger, no --from
    hub = Hub()
    hub.issues[1]["pull_request"] = {"url": "x"}
    assert cli.main(["push", "1"], gh=hub) == 2 and hub.writes == []
    err = capsys.readouterr().err
    assert "E_NOT_AN_ISSUE" in err and "ghp dod" not in err  # not the advice to seed a ledger for a pull request
    del hub.issues[1]["pull_request"]
    assert cli.main(["push", "1"], gh=hub) == 2 and "ghp dod 1 --spec-dir" in capsys.readouterr().err  # an issue still gets that advice


def test_tasks_file_found_through_the_ledger_spec_dir(tmp_path, monkeypatch):
    cli_repo(tmp_path, monkeypatch, TASKS)
    (tmp_path / "specs" / "001").mkdir(parents=True)
    (tmp_path / "tasks.md").rename(tmp_path / "specs" / "001" / "tasks.md")
    (tmp_path / "dod").mkdir()
    (tmp_path / "dod" / "DoD_1.md").write_text("# DoD\n\n- **Spec dir:** specs/001\n\n## 1. Story\n")
    hub = Hub()
    assert cli.main(["push", "1"], gh=hub) == 0 and not hub.armed and hub.sent() == []
    assert [c for c in hub.calls if c[0] == "rest"][0][2] == BASE + "/1"
    hub2 = Hub()
    hub2.issues[2] = raw(2, title="Another story", body="b")
    assert cli.main(["push", "2"], gh=hub2) == 2  # no ledger for #2 and no --from


# ---- AC3: the 100 limit ----
def test_more_than_100_tasks_is_refused_with_no_write(tmp_path, monkeypatch, capsys):
    many = [T("T%03d" % i, "task %d" % i) for i in range(1, 102)]
    cli_repo(tmp_path, monkeypatch, many)
    hub = Hub()
    assert cli.main(["push", "1", "--from", "tasks.md", "--apply"], gh=hub) == 2
    err = capsys.readouterr().err
    assert "E_TOO_MANY_TASKS" in err and "101" in err and hub.calls == [] and not hub.armed  # not even a read
    cli_repo(tmp_path, monkeypatch, many[:100])
    assert cli.main(["push", "1", "--from", "tasks.md"], gh=Hub()) == 0  # exactly 100 is fine


def test_existing_children_over_100_are_reported():
    hub = Hub()
    for i in range(101):
        hub.seed("T%03d" % (i + 1), 200 + i, "T%03d t" % (i + 1))
    with pytest.raises(push.PushError) as e:
        plan(hub, [T("T001", "t")])
    assert e.value.code == "E_SUB_ISSUE_LIMIT" and "101" in str(e.value) and hub.sent() == []


def test_a_story_full_of_children_cannot_take_one_more_but_can_be_resumed():
    hub = Hub()
    for i in range(100):  # 100 children, none of them in tasks.md any more
        hub.seed("T%03d" % (i + 101), 200 + i, "T%03d t" % (i + 101), on_board=True, status="Todo")
    with pytest.raises(push.PushError) as e:
        plan(hub, [T("T001", "one more")])
    assert e.value.code == "E_SUB_ISSUE_LIMIT" and "100" in str(e.value) and hub.sent() == []
    ops, _ = plan(hub, [T("T101", "t")])  # nothing new to link: fine
    assert actions(ops)["T101"] == "unchanged"


# ---- AC4: resume ----
def test_rerun_after_partial_failure_resumes():
    tasks = [T("T001", "A"), T("T002", "B"), T("T003", "C")]
    hub = Hub()
    hub.fail[("add_item", 2)] = GhError("E_GH_HTTP", 403, "forbidden")  # T002 is created and linked, then the board refuses
    res = push_all(hub, tasks)
    assert [c["task_id"] for c in res["created"]] == ["T001"] and res["failed"][0]["task_id"] == "T002"
    assert res["failed"][0]["number"] == 11 and not res["failed"][0]["unknown"]
    assert hub.count["create"] == 2 and sorted(hub.children()) == [10, 11]
    res2 = push_all(hub, tasks)  # the re-run
    assert res2["failed"] == [] and hub.count["create"] == 3  # T001 and T002 were not created again, T003 was
    assert sorted(hub.children()) == [10, 11, 12] and sorted(hub.items.values()) == ["PVTI_0010", "PVTI_0011", "PVTI_0012"]
    assert set(hub.status.values()) == {"Todo"} and len(hub.status) == 3
    assert push_all(hub, tasks)["created"] == [] and hub.count["create"] == 3  # and a third run does nothing
    assert hub.count["add_item"] == 4  # the one failed add is the only repeat


def test_created_but_unlinked_tasks_are_linked_not_created_again():
    hub = Hub()
    hub.seed("T001", 11, "T001 A", parent=False)  # exists, carries the markers, is not a child (the link never happened)
    res = push_all(hub, [T("T001", "A")])
    assert hub.count == Counter(link=1, add_item=1, set_status=1) and res["failed"] == []
    link = next(c for c in hub.calls if c[:2] == ("rest", "POST"))
    assert link[2] == BASE + "/1/sub_issues" and link[3] == {"sub_issue_id": hub.issues[11]["id"]}  # the integer id, not the number
    assert sorted(hub.children()) == [11]


def test_a_create_that_came_back_without_its_parent_is_linked_in_the_same_run():
    hub = Hub()
    real = hub.rest

    def no_parent(method, path, body=None):
        if method == "POST" and path == BASE:
            body = {k: v for k, v in body.items() if k != "parent_issue_id"}
        return real(method, path, body)
    hub.rest = no_parent  # type: ignore
    res = push_all(hub, [T("T001", "A")])
    assert res["failed"] == [] and hub.count == Counter(create=1, link=1, add_item=1, set_status=1) and sorted(hub.children()) == [10]


def test_an_unknown_outcome_stops_the_push_and_the_rerun_does_not_create_again():
    tasks = [T("T001", "A"), T("T002", "B")]
    hub = Hub()
    hub.fail_after[("create", 1)] = GhTimeout(write=True)  # created, the reply was lost
    hub.find_broken = True  # and the re-read for it fails too: the original error comes back
    res = push_all(hub, tasks)
    (failed,) = res["failed"]
    assert failed["task_id"] == "T001" and failed["unknown"] is True and hub.count["create"] == 1  # T002 was not tried
    assert "re-run" in push.render_result(res)
    hub.find_broken = False
    res2 = push_all(hub, tasks)
    assert res2["failed"] == [] and hub.count["create"] == 2 and sorted(hub.children()) == [10, 11]  # T001 was found, T002 made once


def test_a_create_whose_reply_was_lost_is_found_in_a_lagging_list_not_created_again():
    hub = Hub()
    hub.fail_after[("create", 1)] = GhTimeout(write=True)  # created, the reply was lost
    hub.lag = 3  # and GitHub's newest-issues list hides it for the first 3 reads (live: about 2 to 4+ s)
    res = push_all(hub, [T("T001", "A")])
    assert res["failed"] == [] and hub.count["create"] == 1 and sorted(hub.children()) == [10]  # found by its marker after a wait


def test_a_list_that_still_lags_after_the_budget_ends_the_push_and_says_to_wait_then_rerun():
    hub = Hub()
    hub.fail_after[("create", 1)] = GhTimeout(write=True)
    hub.lag = 99
    res = push_all(hub, [T("T001", "A"), T("T002", "B")])
    (failed,) = res["failed"]
    text = push.render_result(res)
    assert failed["unknown"] is True and hub.count["create"] == 1  # nothing was sent twice, T002 was not tried
    assert "wait" in text and "30 s" in text and "re-run ghp push" in text and "reads first" in text
    hub.lag = 0  # half a minute later the list shows it, and the re-run adopts it
    again = push_all(hub, [T("T001", "A"), T("T002", "B")])
    assert again["failed"] == [] and hub.count["create"] == 2 and sorted(hub.children()) == [10, 11]


def test_an_unknown_outcome_on_the_board_write_is_not_repeated():
    hub = Hub()
    hub.fail_after[("add_item", 1)] = GhTimeout(write=True)
    res = push_all(hub, [T("T001", "A")])
    assert hub.count["add_item"] == 1  # add_item re-read the board and found the item: no second add in this run
    assert res["failed"] == [] and hub.status


def test_the_auto_add_race_on_the_board_write_does_not_stop_the_push():
    hub = Hub()  # the Project's auto-add workflow put #10 on the board between ghp's create and its add (T34 live)
    hub.fail_after[("add_item", 1)] = GhError("E_GH_GRAPHQL", 200, "Content already exists in this project")
    res = push_all(hub, [T("T001", "A"), T("T002", "B")])
    assert res["failed"] == [] and [c["number"] for c in res["created"]] == [10, 11]
    assert hub.count == Counter(create=2, add_item=2, set_status=2)  # the add was not repeated; both items set to Todo
    assert set(hub.status.values()) == {"Todo"} and len(hub.status) == 2


def test_a_dropped_label_stops_the_push_and_names_the_issue_that_exists():
    hub = Hub()
    real = hub.rest

    def drop(method, path, body=None):
        if method == "POST" and path == BASE:
            body = dict(body, labels=[])
        return real(method, path, body)
    hub.rest = drop  # type: ignore
    res = push_all(hub, [T("T001", "A"), T("T002", "B")])
    (failed,) = res["failed"]
    assert failed["number"] == 10 and hub.count["create"] == 1 and "E_LABEL_DROPPED" in failed["error"]
    hub.rest = real  # type: ignore
    again = push_all(hub, [T("T001", "A"), T("T002", "B")])
    assert hub.count["create"] == 2 and again["failed"] == []  # #10 was found by its marker, only T002 was created


def test_retitle_is_a_patch_of_the_title_only_and_is_read_back():
    hub = Hub()
    hub.seed("T001", 11, "old name", on_board=True, status="Todo")
    res = push_all(hub, [T("T001", "New name")])
    assert hub.issues[11]["title"] == "T001 New name" and [c[2:] for c in hub.calls if c[:2] == ("rest", "PATCH")] == [(BASE + "/11", {"title": "T001 New name"})]
    assert [c["task_id"] for c in res["updated"]] == ["T001"]
    assert push_all(hub, [T("T001", "New name")])["updated"] == []


def test_a_retitle_with_a_lost_reply_is_checked_by_reading_the_issue():
    hub = Hub()
    hub.seed("T001", 11, "old name", on_board=True, status="Todo")
    hub.fail_after[("retitle", 1)] = GhTimeout(write=True)
    assert push_all(hub, [T("T001", "New name")])["failed"] == [] and hub.count["retitle"] == 1


# ---- AC5: pacing ----
def test_push_of_25_tasks_stays_under_60_writes_a_minute(clock):
    hub = Hub(clock)
    tasks = [T("T%03d" % i, "task %d" % i) for i in range(1, 26)]
    res = push_all(hub, tasks, pace=push.Pacer(clock, clock.sleep, GAP))
    assert res["failed"] == [] and len(hub.writes) == 75 and len(hub.children()) == 25
    times = [t for t, _ in hub.writes]
    assert all(b - a >= GAP - 1e-9 for a, b in zip(times, times[1:]))
    assert max(sum(t <= s < t + 60 for s in times) for t in times) < 60  # no 60-second window holds 60 writes
    assert clock.sleeps  # it waited, on the fake clock


def test_the_shipped_gap_allows_fewer_than_60_a_minute():
    assert 60 / GAP < 60 and GAP >= 1.0


def test_the_pacer_waits_only_for_the_rest_of_the_gap(clock):
    pace = push.Pacer(clock, clock.sleep, 2.0)
    pace()
    clock.now += 0.5
    pace()
    pace()
    assert clock.sleeps == [1.5, 2.0]


# ---- AC6: --expect-plan ----
def test_push_expect_plan_mismatch_writes_nothing(tmp_path, monkeypatch, capsys):
    cli_repo(tmp_path, monkeypatch, TASKS)
    hub = Hub()
    assert cli.main(["push", "1", "--from", "tasks.md", "--plan-out", "plan.md"], gh=hub) == 0
    capsys.readouterr()
    (tmp_path / "tasks.md").write_text((tmp_path / "tasks.md").read_text() + "- [ ] T004 Added after approval\n")
    hub2 = Hub()
    assert cli.main(["push", "1", "--from", "tasks.md", "--apply", "--expect-plan", "plan.md"], gh=hub2) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: plan changed since approval") and not hub2.armed and hub2.sent() == [] and hub2.writes == []
    assert "T004" in (tmp_path / "plan.md.new").read_text()
    hub3 = Hub()
    assert cli.main(["push", "1", "--from", "tasks.md", "--apply", "--expect-plan", ""], gh=hub3) == 2 and not hub3.armed and hub3.sent() == []
    hub4 = Hub()  # the approved plan, unchanged world: applies
    (tmp_path / "tasks.md").write_text("".join("- [ ] %s %s\n" % (t["id"], t["title"]) for t in TASKS))
    assert cli.main(["push", "1", "--from", "tasks.md", "--apply", "--expect-plan", "plan.md"], gh=hub4) == 0 and hub4.count["create"] == 3


def test_expect_plan_also_catches_a_changed_world(tmp_path, monkeypatch, capsys):
    cli_repo(tmp_path, monkeypatch, TASKS)
    assert cli.main(["push", "1", "--from", "tasks.md", "--plan-out", "plan.md"], gh=Hub()) == 0
    hub = Hub()
    hub.seed("T001", 11, "T001 Create model", on_board=True, status="Todo")  # someone pushed T001 since the preview
    assert cli.main(["push", "1", "--from", "tasks.md", "--apply", "--expect-plan", "plan.md"], gh=hub) == 2 and hub.sent() == []


# ---- hostile input ----
@pytest.mark.parametrize("make", [
    lambda n: "- [ ] T001 " + " " * n + "x",
    lambda n: "- [ ] T001 " + "[P] " * (n // 4),
    lambda n: "- [ ] T" + "1" * n,
])
def test_hostile_tasks_text_is_handled_in_linear_time(make):
    for n in (30000, 300000):
        start = time.monotonic()
        try:
            hub = Hub()
            push.plan_push(hub, CFG_NO_BOARD, story_of(hub), parse_tasks(make(n)), None)
        except ValueError:
            pass
        assert time.monotonic() - start < 2.0


@pytest.mark.parametrize("n", [30000, 300000])
def test_hostile_issue_bodies_are_scanned_in_linear_time(n):
    hub = Hub()
    hub.seed("T001", 11, "T001 A", body="<!-- spec-kit-ghp:task:" * (n // 23) + " <!-- spec-kit-ghp:story:" * (n // 25))
    hub.seed("T002", 12, "T002 B", body="<!-- spec-kit-ghp:task:T002 " * 10 + "-" * n)
    start = time.monotonic()
    plan(hub, [T("T001", "A")], CFG_NO_BOARD)
    assert time.monotonic() - start < 2.0


def test_no_title_reaches_argv_or_a_path(tmp_path, monkeypatch):
    cli_repo(tmp_path, monkeypatch, [T("T001", "../../etc/passwd $(rm -rf x) -X DELETE")])
    hub = Hub()
    assert cli.main(["push", "1", "--from", "tasks.md", "--apply"], gh=hub) == 0
    assert all(c[2].startswith("repos/octo/demo/issues") or c[2] == LABEL_PATH for c in hub.calls if c[0] == "rest")  # LABEL_PATH: the one fixed label preflight
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ghp.config.json", "tasks.md"]  # nothing was written to a path made of a title


def test_push_is_a_registered_verb():
    assert "push" in cli.REGISTRY


# ---- the plan binds the whole text that will be sent (--expect-plan compares it) ----
def test_titles_that_differ_after_character_100_make_different_plans():
    hub = Hub()
    a, b = [push.render_plan(story_of(hub), *plan(hub, [T("T001", "y" * 120 + tail)])) for tail in (" safe", " EVIL")]
    assert a != b and "safe" in a and "EVIL" in b


def shown(text):
    """The plan without its digest line: what a reader sees (the display must be injective on its own, the digest is the second net)."""
    return "\n".join(text.splitlines()[:-2])


def test_a_retitle_row_shows_both_whole_titles():
    hub = Hub()
    old = "T001 " + "x" * 200 + " one"  # longer than 150
    hub.seed("T001", 11, old, on_board=True, status="Todo")
    ops, board = plan(hub, [T("T001", "y" * 400 + " two")])  # cut to 256 on our side
    text = push.render_plan(story_of(hub), ops, board)
    row = next(l for l in text.splitlines() if l.startswith("| update"))
    shown_old, shown_new = row.split("| ")[3].strip(" ").split(" → ")
    assert shown_old == old and shown_new.startswith("T001 yyy") and len(shown_new.split(" (cut")[0]) == 256 and "(cut to 256)" in row


def test_invisible_titles_are_never_cut_in_the_plan():
    hub = Hub()
    a, b = [shown(push.render_plan(story_of(hub), *plan(hub, [T("T001", "\u2800" * 60 + tail)]))) for tail in (" safe", " EVIL")]
    assert a != b and "safe" in a and "EVIL" in b and "\u2800" not in a  # the escapes, not the characters, are shown
    hub.seed("T002", 12, "T002 " + "\u2800" * 60 + "one", on_board=True, status="Todo")
    hub.seed("T003", 13, "T003 " + "\u2800" * 60 + "two", on_board=True, status="Todo")
    ops, board = plan(hub, [T("T002", "z"), T("T003", "z")])
    assert "one" in push.render_plan(story_of(hub), ops, board) and "two" in push.render_plan(story_of(hub), ops, board)


def test_an_invisible_character_and_the_literal_text_of_its_escape_render_differently():
    hub = Hub()
    a, b = [shown(push.render_plan(story_of(hub), *plan(hub, [T("T001", t)]))) for t in ("x\u2800y", "x\\u2800y")]
    assert a != b
    hub.seed("T002", 12, "T002 x\u2800y", on_board=True, status="Todo")
    hub.seed("T003", 13, "T003 x\\u2800y", on_board=True, status="Todo")
    ops, board = plan(hub, [T("T002", "z"), T("T003", "z")])
    rows = [l for l in push.render_plan(story_of(hub), ops, board).splitlines() if l.startswith("| update")]
    assert rows[0].split("| ")[3] != rows[1].split("| ")[3]


def test_a_title_over_1024_characters_in_the_plan_is_refused_not_cut():
    hub = Hub()
    hub.seed("T001", 11, "T001 " + "x" * 2000, on_board=True, status="Todo")  # GitHub caps titles at 256; a hostile reply is refused
    ops, board = plan(hub, [T("T001", "A")])
    with pytest.raises(push.PushError) as e:
        push.render_plan(story_of(hub), ops, board)
    assert e.value.code == "E_TITLE_TOO_LONG"


def test_the_last_plan_line_is_a_digest_of_the_exact_data():
    hub = Hub()
    plans = [push.render_plan(story_of(hub), *plan(hub, [T("T001", t)])) for t in ("a b", "a  b")]  # sent titles collapse blanks, so these are the same plan
    assert plans[0] == plans[1] and plans[0].splitlines()[-1].startswith("digest: ") and len(plans[0].splitlines()[-1]) == len("digest: ") + 64
    ops, board = plan(hub, [T("T001", "A")])
    text = push.render_plan(story_of(hub), ops, board)
    ops[0]["old_title"] = "something the table would not show\t"  # data the rendering collapses still changes the digest
    assert push.render_plan(story_of(hub), ops, board).splitlines()[-1] != text.splitlines()[-1]
    ops[0]["old_title"] = None
    ops[0]["item_id"] = "PVTI_other"
    assert push.render_plan(story_of(hub), ops, board).splitlines()[-1] != text.splitlines()[-1]


ALPHABET = ["x", "y", " ", "\u2800", "\u200b", "\\", "|", "\n", "\t", "\u00a0", "\u0301", "\\u2800", "\\", "u2800", "\u202e", "@a"]


def test_two_different_title_lists_never_give_equal_plans():
    import random
    rnd = random.Random(20260109)
    hub = Hub()
    story = story_of(hub)
    seen = {}
    for _ in range(400):
        tasks = [T("T%03d" % (i + 1), "".join(rnd.choice(ALPHABET) for _ in range(rnd.randint(0, 12)))) for i in range(rnd.randint(1, 3))]
        ops, board = plan(hub, tasks)
        key = tuple(o["title"] for o in ops)
        text = shown(push.render_plan(story, ops, board))
        assert seen.setdefault(text, key) == key, (tasks, seen[text])  # one plan text, one set of titles sent
    # and for an existing title that GitHub holds (the old side of a retitle)
    seen = {}
    for _ in range(300):
        old = "".join(rnd.choice(ALPHABET) for _ in range(rnd.randint(0, 12)))
        h = Hub()
        h.seed("T001", 11, old or "T001", on_board=True, status="Todo")
        ops, board = plan(h, [T("T001", "new")])
        text = shown(push.render_plan(story_of(h), ops, board))
        assert seen.setdefault(text, ops[0]["old_title"]) == ops[0]["old_title"], old


# ---- mentions, cut titles, the lost ado guards ----
def test_mentions_in_titles_are_defanged_before_they_are_sent_and_shown():
    hub = Hub()
    ops, board = plan(hub, [T("T001", "ping @octocat and @org/team")])
    assert "@" not in ops[0]["title"] and "[mention removed]" in ops[0]["title"]
    assert "@" not in push.render_plan(story_of(hub), ops, board)
    push_all(hub, [T("T001", "ping @octocat")])
    assert all("@" not in c[3]["title"] for c in hub.calls if c[:2] == ("rest", "POST"))


def test_title_of_exactly_256_is_not_cut_and_257_is_cut_and_marked():
    hub = Hub()
    ok = T("T001", "y" * (256 - len("T001 ")))
    over = T("T002", "y" * (257 - len("T002 ")))
    ops, board = plan(hub, [ok, over])
    assert len(ops[0]["title"]) == 256 and not ops[0]["title"].endswith("…") and not ops[0]["cut"]
    assert len(ops[1]["title"]) == 256 and ops[1]["title"].endswith("…") and ops[1]["cut"]
    text = push.render_plan(story_of(hub), ops, board)
    assert text.count("(cut to 256)") == 1 and "(cut to 256)" in next(l for l in text.splitlines() if "| T002 |" in l)
    push_all(hub, [ok, over])  # the cut title is stable: a re-run changes nothing
    assert set(actions(plan(hub, [ok, over])[0]).values()) == {"unchanged"}


def test_plan_warns_when_every_task_on_the_story_is_an_orphan():
    hub = Hub()
    hub.seed("T001", 11, "T001 A", on_board=True, status="Todo")
    hub.seed("T002", 12, "T002 B", on_board=True, status="Todo")
    ops, board = plan(hub, [])
    out = push.render_plan(story_of(hub), ops, board)
    assert "\u26a0 tasks.md lists none of the 2 task(s)" in out
    ops, board = plan(hub, [T("T001", "A")])
    assert "\u26a0" not in push.render_plan(story_of(hub), ops, board)


def test_unchanged_ids_are_wrapped_at_100_columns():
    hub = Hub()
    for i in range(1, 61):
        hub.seed("T%03d" % i, 100 + i, "T%03d t" % i, on_board=True, status="Todo")
    ops, board = plan(hub, [T("T%03d" % i, "t") for i in range(1, 61)])
    lines = push.render_plan(story_of(hub), ops, board).splitlines()
    wrapped = [l for l in lines if l.startswith("T0") or l.startswith("unchanged:")]
    assert len(wrapped) > 1 and max(len(l) for l in wrapped) <= 100


def test_a_retitle_whose_read_back_is_another_title_is_an_error():
    hub = Hub()
    hub.seed("T001", 11, "old", on_board=True, status="Todo")
    real = hub.rest

    def lie(method, path, body=None):
        out = real(method, path, body)
        if method == "PATCH":
            out["title"] = "something else"
        return out
    hub.rest = lie  # type: ignore
    (failed,) = push_all(hub, [T("T001", "New")])["failed"]
    assert "E_TITLE_NOT_APPLIED" in failed["error"] and failed["number"] == 11 and not failed["unknown"]


def test_the_unknown_outcome_text_says_the_write_may_have_applied_not_that_a_create_was_lost():
    hub = Hub()
    hub.seed("T001", 11, "old", on_board=True, status="Todo")
    hub.fail_after[("retitle", 1)] = GhTimeout(write=True)
    real = hub.rest

    def deaf(method, path, body=None):  # the read that should settle it fails too
        if method == "GET" and path == BASE + "/11" and hub.count["retitle"]:
            raise GhTimeout()
        return real(method, path, body)
    hub.rest = deaf  # type: ignore
    res = push_all(hub, [T("T001", "New")])
    text = push.render_result(res)
    assert res["failed"][0]["unknown"] and "may have been applied" in text and "re-run" in text and "create it again" not in text


# ---- the label `ghp:task` preflight (read only, in the plan) ----
WARNING = "label ghp:task does not exist; GitHub creates it only when the token has push access"


def label_state(hub, tasks):
    ops, board = plan(hub, tasks)
    missing = push.label_missing(hub, CFG, ops)
    return ops, board, missing, push.render_plan(story_of(hub), ops, board, label_missing=missing)


def test_the_plan_warns_when_the_label_does_not_exist_and_the_digest_covers_the_warning():
    hub = Hub()
    hub.label = None
    ops, board, missing, text = label_state(hub, [T("T001", "A")])
    assert missing is True and any(line.endswith(WARNING) for line in text.splitlines())
    hub.label = Hub().label
    _, _, missing2, text2 = label_state(hub, [T("T001", "A")])
    assert missing2 is False and WARNING not in text2
    assert text.splitlines()[-1] != text2.splitlines()[-1]  # --expect-plan binds the warning: same ops, different digest
    assert [c[1:3] for c in hub.calls if c[2] == LABEL_PATH] == [("GET", LABEL_PATH)] * 2 and hub.sent() == []  # read only


def test_the_label_is_not_looked_up_when_the_plan_creates_nothing():
    hub = Hub()
    hub.seed("T001", 11, "T001 A", on_board=True, status="Todo")
    hub.label = None
    ops, _ = plan(hub, [T("T001", "A")])
    assert actions(ops) == {"T001": "unchanged"} and push.label_missing(hub, CFG, ops) is False
    assert all(c[2] != LABEL_PATH for c in hub.calls)


@pytest.mark.parametrize("bad", [[], "ghp:task", 5, {}, {"name": 5}, {"name": "ghp:other"}, {"name": None}, {"id": 1}, 0])
def test_a_hostile_label_response_fails_closed(bad):
    hub = Hub()
    hub.label = bad
    ops, _ = plan(hub, [T("T001", "A")])
    with pytest.raises(GhError) as e:
        push.label_missing(hub, CFG, ops)
    assert e.value.code == "E_GH_BAD_BODY"


def test_a_label_read_that_fails_for_any_reason_but_404_refuses_the_plan():
    hub = Hub()
    real = hub.rest

    def denied(method, path, body=None):
        if path == LABEL_PATH:
            raise GhError("E_GH_HTTP", 403, "Forbidden")
        return real(method, path, body)
    hub.rest = denied  # type: ignore
    ops, _ = plan(hub, [T("T001", "A")])
    with pytest.raises(GhError) as e:
        push.label_missing(hub, CFG, ops)
    assert e.value.status == 403


def test_a_label_in_another_case_is_the_label(tmp_path, monkeypatch):
    hub = Hub()
    hub.label = {"name": "GHP:Task"}  # GitHub label names are case-insensitive
    ops, _ = plan(hub, [T("T001", "A")])
    assert push.label_missing(hub, CFG, ops) is False


def test_the_cli_dry_run_prints_the_label_warning_and_writes_nothing(tmp_path, monkeypatch, capsys):
    cli_repo(tmp_path, monkeypatch, TASKS)
    hub = Hub()
    hub.label = None
    assert cli.main(["push", "1", "--from", "tasks.md"], gh=hub) == 0
    out = capsys.readouterr().out
    assert WARNING in out and hub.sent() == [] and not hub.armed


def test_a_plan_approved_with_the_label_is_refused_when_the_label_vanished(tmp_path, monkeypatch, capsys):
    cli_repo(tmp_path, monkeypatch, TASKS)
    assert cli.main(["push", "1", "--from", "tasks.md", "--plan-out", "plan.md"], gh=Hub()) == 0
    hub = Hub()
    hub.label = None  # the label was deleted after the human read the plan: the warning changes the plan and its digest
    assert cli.main(["push", "1", "--from", "tasks.md", "--apply", "--expect-plan", "plan.md"], gh=hub) == 2
    assert not hub.armed and hub.sent() == []


# ---- authority details and ordering ----
def test_label_match_is_ascii_case_folding_only_not_lower_not_strip():
    hub = Hub()
    for n, label in ((11, "ghp:tas\u212a"), (12, "ghp:task "), (13, " ghp:task"), (14, "GHP:TASK")):  # Kelvin sign, trailing and leading space, upper case
        hub.seed("T%03d" % n, n, "T%03d t" % n, parent=False, author=999999, labels=(label,))
    ops, _ = plan(hub, [T("T%03d" % n, "t") for n in (11, 12, 13, 14)], CFG_NO_BOARD)
    assert actions(ops) == {"T011": "create", "T012": "create", "T013": "create", "T014": "update"}


def test_the_viewer_is_read_once_however_many_candidates_there_are():
    hub = Hub()
    reads = []
    real = hub.viewer
    hub.viewer = lambda: reads.append(1) or real()  # type: ignore
    for n in range(11, 16):
        hub.seed("T001", n, "copy", parent=False, author=999999)
    hub.seed("T002", 20, "T002 B", parent=False)
    plan(hub, [T("T001", "A"), T("T002", "B")])
    assert reads == [1]


def test_mentions_are_defanged_before_the_cut_so_a_cut_title_is_short_enough_and_stable():
    hub = Hub()
    tasks = [T("T001", "@octocat " * 60)]
    ops, _ = plan(hub, tasks)
    title = ops[0]["title"]
    assert len(title) <= 256 and title.endswith("…") and "@" not in title and ops[0]["cut"]
    push_all(hub, tasks)
    assert hub.issues[10]["title"] == title and set(actions(plan(hub, tasks)[0]).values()) == {"unchanged"}
