"""T24: `ghp done N`, the only verb that moves a Project item to Done (D4, D5). `DoneHub` is the stateful fake GitHub of the push tests
(issues, one Project) plus comments, a close, the GraphQL facts query and an optional "Item closed" automation, behind the FakeGh write gate."""
import copy
import json
import re
import time
from pathlib import Path

import pytest

import ghp.__main__ as cli
from ghp import comments, dod, done, issues, projects
from ghp.config import Config
from ghp.gh import GhError, GhTimeout, check_rest_request, is_graphql_write
from ghp.refs import Ref
from test_projects import fx
from test_push import OPTIONS, Hub

N = 11
ITEM = "PVTI_0011"
OID = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
OID2 = "b1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
LEDGER = "dod/DoD_11.md"


def pull(number=40, merged=True, oid=OID, at="2026-11-09T10:00:00Z", repo="octo/demo", base="main"):
    return {"number": number, "merged": merged, "mergedAt": at if merged else None, "baseRefName": base,
            "mergeCommit": None if oid is None else {"oid": oid}, "baseRepository": None if repo is None else {"nameWithOwner": repo}}


def ledger(*marks, extra=""):
    boxes = "".join("- [%s] item %d\n" % (m, i) for i, m in enumerate(marks, 1))
    return "# DoD\n- **Spec dir:** specs/001-x\n\n%s\n\n## 3. AC\n\n## 4. Gate & review log\n\n## 5. DoD\n%s%s" % (dod.SECTION2, boxes, extra)


def repo(tmp_path, monkeypatch, marks=("x", "x"), cfg=None, **more):
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", "project_number": 3, **(cfg or {}), **more}))
    (tmp_path / "dod").mkdir(exist_ok=True)
    (tmp_path / LEDGER).write_text(ledger(*marks), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)


@pytest.fixture(autouse=True)
def naps(monkeypatch):
    """Every wait is instant and recorded."""
    seen = []
    monkeypatch.setattr(issues, "_nap", seen.append)
    return seen


class DoneHub(Hub):
    """Issue 11 is the story on the board at In Progress. `linked` are the PRs GraphQL ties to it, `closer` the newest ClosedEvent's closer;
    `auto_after=k`: after our close, the k-th Status read finds the item moved to Done by the Project's workflow (None: it never moves)."""

    def __init__(self, status="In Progress", on_board=True, state="open", linked=None, closer=None, workflows=None, auto_after=None, number=N, **kw):
        super().__init__()
        self.n = number
        self.seed("T001", number, "Ship the loyalty points", parent=False, on_board=on_board, status=status, state=state, body="the story", **kw)
        self.linked = [pull()] if linked is None else linked
        self.closer, self.auto_after, self.reads = closer, auto_after, None
        self.workflows = workflows if workflows is not None else [("Item closed", False), ("Auto-close issue", False), ("Pull request merged", True)]
        self.comments, self.next_comment = [], 7000
        self.subs = (0, 0)
        self.rest_paths = []
        self.prs, self.xrefs, self.xrefs_more = {}, [], False  # --pr: issueOrPullRequest by number, the issue's CrossReferencedEvents
        if state == "closed":
            self.issues[number]["state_reason"] = "completed"

    # -- comments
    def post(self, body, user=1001):
        self.next_comment += 1
        self.comments.append({"id": self.next_comment, "user": user, "body": body, "edited": False})
        return self.next_comment

    def _gql_comment(self, c):
        return {"id": "IC_%d" % c["id"], "databaseId": c["id"], "body": c["body"], "createdAt": "2026-11-09T10:00:00Z", "lastEditedAt": None,
                "isMinimized": False, "viewerDidAuthor": c["user"] == 1001, "author": {"__typename": "User", "databaseId": c["user"]},
                "userContentEdits": {"totalCount": 0}}

    def _rest_comment(self, c):
        return {"id": c["id"], "node_id": "IC_%d" % c["id"], "user": {"id": c["user"], "login": "u%d" % c["user"], "type": "User"},
                "performed_via_github_app": None}

    def _rec(self, *call):
        self.calls.append(call)

    # -- the transport
    def rest(self, method, path, body=None):
        method = method.upper()
        self.rest_paths.append((method, path))
        m = re.fullmatch(r"repos/octo/demo/issues/%d/comments(?:\?per_page=100&page=(\d+))?" % self.n, path)
        if m:
            check_rest_request(method, path, body)
            self._gate(method != "GET")
            self._rec("rest", method, path, body)
            if method == "GET":
                return [self._rest_comment(c) for c in self.comments] if (m.group(1) or "1") == "1" else []
            self._write("comment")
            cid = self.post(body["body"])
            self._after("comment")
            return {"id": cid}
        if method == "PATCH" and isinstance(body, dict) and "state" in body:
            check_rest_request(method, path, body)
            self._gate(True)
            self._rec("rest", method, path, body)
            self._write("close")
            self.issues[self.n].update(state="closed", state_reason=body["state_reason"])
            self.reads = 0 if self.auto_after is not None else None
            self._after("close")
            return copy.deepcopy(self.issues[self.n])
        return super().rest(method, path, body)

    def graphql(self, query, variables=None):
        if "userContentEdits" in query:
            self._gate(False)
            self._rec("graphql", query, variables)
            return {"repository": {"issue": {"comments": {"pageInfo": {"hasNextPage": False, "endCursor": "c"},
                                                          "nodes": [self._gql_comment(c) for c in self.comments]}}}}
        if "issueOrPullRequest" in query:  # --pr N: the PR and the issue's cross-reference events, one read
            self._gate(False)
            self._rec("graphql", query, variables)
            return {"repository": {"issueOrPullRequest": copy.deepcopy(self.prs.get(variables["number"]))},
                    "node": {"timelineItems": {"pageInfo": {"hasNextPage": self.xrefs_more}, "nodes": copy.deepcopy(self.xrefs)}}}
        if "closedByPullRequestsReferences" in query:
            self._gate(False)
            self._rec("graphql", query, variables)
            closed = self.issues[self.n]["state"] == "closed"
            return {"node": {"subIssuesSummary": {"total": self.subs[0], "completed": self.subs[1]},
                             "timelineItems": {"nodes": [{"closer": self.closer}] if closed or self.closer else []},  # a reopened issue keeps its old event
                             "closedByPullRequestsReferences": {"pageInfo": {"hasNextPage": False}, "nodes": copy.deepcopy(self.linked)}}}
        if "workflows(" in query:
            self._gate(False)
            self._rec("graphql", query, variables)
            data = fx("project_workflows")
            data["user"]["projectV2"]["workflows"]["nodes"] = [{"name": n, "enabled": e, "number": i} for i, (n, e) in enumerate(self.workflows, 1)]
            return data
        if "fieldValueByName" in query and self.reads is not None:
            self.reads += 1
            if self.reads >= self.auto_after:  # the Project's own workflow moved the item
                self.status[ITEM], self.reads, self.auto_after = "Done", None, None
        return super().graphql(query, variables)

    # -- what a test asks
    def kinds(self):
        return self.writes_of()

    def ledger_comments(self):
        return [c for c in self.comments if done.MARKER % self.n in c["body"]]


def run(hub, capsys, *argv, n=str(N)):
    code = cli.main(["done", n, *argv], gh=hub)
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def apply(hub, capsys, *argv):
    return run(hub, capsys, "--apply", *argv)


def status_writes(hub):
    return [c[2] for c in hub.calls if c[0] == "graphql" and "updateProjectV2ItemFieldValue" in c[1]]


# ---- AC1: the preview shows the checklist, the comment, the close and the Status move; nothing is written without --apply ----
def test_done_dry_run_sends_nothing(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    code, out, err = run(hub, capsys, "--plan-out", "plan.md")
    assert code == 0 and err == ""
    assert "DoD checklist: ready, 2 of 2 ticked" in out
    assert "Step 1: post the ledger comment" in out and "**DoD complete for #11.**" in out and done.MARKER % N in out
    assert "Step 2: close #11 as completed" in out
    assert "Status: In Progress -> Done" in out and "wait up to 30 s" in out
    assert "PR #40" in out and OID[:12] in out
    assert not hub.armed and hub.sent() == [] and hub.writes == [] and hub.status[ITEM] == "In Progress" and hub.issues[N]["state"] == "open"
    assert (tmp_path / "plan.md").read_text() + "\n" == out
    assert re.search(r"^digest: [0-9a-f]{64}$", out, re.M)
    assert hub.rest_paths and all(m == "GET" for m, _ in hub.rest_paths)  # reads only


def test_a_dry_run_with_unticked_boxes_lists_them_and_reads_nothing(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch, marks=("x", " ", " "))
    hub = DoneHub()
    code, out, _ = run(hub, capsys)
    assert code == 0 and "NOT READY, 2 unticked" in out and "- item 2" in out and "- item 3" in out
    assert hub.calls == []


def test_every_line_printed_is_escaped_and_the_plan_is_injective(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    hub.issues[N]["title"] = "Ship \x1b[31mred\x1b[0m ‮ points\\u001b"
    code, out, _ = run(hub, capsys)
    assert code == 0 and "\x1b" not in out and "‮" not in out
    assert "\\u001b[31mred" in out and "\\u202e" in out and "\\\\u001b" in out  # the literal text of an escape differs from the character
    hub2 = DoneHub()
    hub2.issues[N]["title"] = "Ship \\u001b[31mred\\u001b[0m \\u202e points\\u001b"  # the literal escapes
    _, out2, _ = run(hub2, capsys)
    assert out != out2


# ---- AC2: the gate comes first ----
@pytest.mark.parametrize("marks", [("x", " "), ("x", "-"), (" ",)])
def test_unticked_dod_blocks_apply(tmp_path, monkeypatch, capsys, marks):
    repo(tmp_path, monkeypatch, marks=marks)
    hub = DoneHub()
    code, out, err = apply(hub, capsys)
    assert code == 2 and "E_DOD_NOT_TICKED" in err and "NOT READY" in out
    assert hub.sent() == [] and not hub.armed and hub.calls == [] and hub.issues[N]["state"] == "open"


@pytest.mark.parametrize("ledger_text", ["# DoD\n\n## 2. Brainstorm & plan\n\n## 5. DoD\nno boxes here\n", "# DoD\n\n## 2. Brainstorm & plan\n\n## 3. AC\n",
                                         ledger("x") + "\n```\nunclosed fence\n- [ ] after\n"])
def test_a_ledger_that_cannot_be_read_as_ready_blocks_apply(tmp_path, monkeypatch, capsys, ledger_text):
    repo(tmp_path, monkeypatch)
    (tmp_path / LEDGER).write_text(ledger_text, encoding="utf-8")
    hub = DoneHub()
    assert apply(hub, capsys)[0] == 2 and hub.calls == [] and not hub.armed


def test_a_ticked_example_in_a_fence_or_comment_is_not_a_box(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch, marks=(" ",))
    (tmp_path / LEDGER).write_text(ledger(" ", extra="```\n- [x] fake\n```\n<!--\n- [x] hidden\n-->\n"), encoding="utf-8")
    assert apply(DoneHub(), capsys)[0] == 2


def test_a_missing_or_symlinked_ledger_is_refused(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    (tmp_path / LEDGER).rename(tmp_path / "real.md")
    hub = DoneHub()
    code, _, err = apply(hub, capsys)
    assert code == 2 and "E_NO_LEDGER" in err and hub.calls == []
    (tmp_path / LEDGER).symlink_to(tmp_path / "real.md")
    assert apply(hub, capsys)[0] == 2 and hub.calls == []


def test_an_ambiguous_ledger_exits_3(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    (tmp_path / LEDGER).write_text(ledger("x") + "\n## 2. Brainstorm & plan\n", encoding="utf-8")
    hub = DoneHub()
    code, _, err = apply(hub, capsys)
    assert code == 3 and "E_LEDGER_AMBIGUOUS_SECTION2" in err and hub.calls == []


def test_an_invalid_config_exits_3_and_a_missing_one_exits_2(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", "mode": "bogus"}))
    hub = DoneHub()
    assert run(hub, capsys)[0] == 3 and hub.calls == []
    (tmp_path / "ghp.config.json").unlink()
    assert run(hub, capsys)[0] == 2 and hub.calls == []


# ---- AC3: comment, close, wait (reads only), then Status with allow_done=True only if still not Done ----
def test_done_order_comment_close_wait_then_status(tmp_path, monkeypatch, capsys, naps):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    code, out, err = apply(hub, capsys)
    assert code == 0 and err == ""
    assert hub.kinds() == ["comment", "close", "set_status"]  # in that order
    assert [c[1] for c in hub.sent() if c[0] == "rest"] == ["POST", "PATCH"]
    assert hub.issues[N]["state"] == "closed" and hub.issues[N]["state_reason"] == "completed"
    assert hub.status[ITEM] == "Done" and status_writes(hub) == [{"project": "PVT_kwFictional0003", "item": ITEM, "field": "PVTSSF_FictionalStatus",
                                                                  "option": OPTIONS["Done"]}]
    assert naps == [3] * 10 and (done.WAIT, done.POLL) == (30, 3)  # the whole 30 s wait, in reads only
    kinds = [("w" if (c[0] == "rest" and c[1] != "GET") or (c[0] == "graphql" and is_graphql_write(c[1])) else "r") + (c[1] if c[0] == "rest" else "g") for c in hub.calls]
    close_at = kinds.index("wPATCH")
    status_at = max(i for i, k in enumerate(kinds) if k == "wg")
    between = kinds[close_at + 1:status_at]
    assert between.count("rg") >= done.WAIT // done.POLL + 1 and all(k.startswith("r") for k in between)  # only reads between the close and the Status
    assert "posted the ledger comment" in out and "closed #11 as completed" in out and "set by ghp" in out and hub.armed


def test_done_skips_status_write_when_automation_moved_it(tmp_path, monkeypatch, capsys, naps):
    repo(tmp_path, monkeypatch)
    hub = DoneHub(auto_after=3)  # the workflow moves it at the third read after the close
    code, out, _ = apply(hub, capsys)
    assert code == 0 and hub.kinds() == ["comment", "close"] and status_writes(hub) == []
    assert hub.status[ITEM] == "Done" and naps == [3] * 2
    assert "moved by the Project's own workflow" in out


def test_the_status_write_passes_the_real_true(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    seen = []
    real = projects.set_status
    monkeypatch.setattr(projects, "set_status", lambda *a, **k: seen.append(k) or real(*a, **k))
    assert apply(DoneHub(), capsys)[0] == 0
    assert len(seen) == 1 and seen[0]["allow_done"] is True


def test_done_is_never_set_by_another_verb_path_and_the_gate_still_refuses_truthy(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    board = projects.discover(hub, Config("octo", "demo", project_number=3))
    hub.arm()
    for truthy in (1, "yes", [1]):
        with pytest.raises(projects.ProjectError) as e:
            projects.set_status(hub, board, ITEM, "Done", allow_done=truthy)
        assert e.value.code == "E_DONE_NEEDS_THE_GATE"
    assert status_writes(hub) == []


def test_a_failed_comment_stops_before_the_close(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    hub.fail[("comment", 1)] = GhError("E_GH_HTTP", 403, "forbidden")
    code, out, _ = apply(hub, capsys)
    assert code == 1 and hub.kinds() == ["comment"] and hub.issues[N]["state"] == "open" and "nothing was retried" in out and "E_GH_HTTP" in out
    assert status_writes(hub) == []


def test_a_failed_close_stops_before_the_status_and_keeps_the_comment(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    hub.fail[("close", 1)] = GhError("E_GH_HTTP", 422, "no")
    code, out, _ = apply(hub, capsys)
    assert code == 1 and hub.kinds() == ["comment", "close"] and status_writes(hub) == []
    assert "the ledger comment was posted" in out and "nothing was retried" in out
    code, out, _ = apply(hub, capsys)  # the re-run reads first: no second comment, then close, then Status
    assert code == 0 and hub.kinds() == ["comment", "close", "close", "set_status"] and len(hub.ledger_comments()) == 1


def test_a_lost_comment_reply_is_found_by_its_marker_and_not_posted_twice(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    hub.fail_after[("comment", 1)] = GhTimeout(write=True)
    code, out, _ = apply(hub, capsys)
    assert code == 0 and hub.kinds().count("comment") == 1 and len(hub.ledger_comments()) == 1 and hub.kinds()[1:] == ["close", "set_status"]


def test_a_lost_comment_reply_that_was_not_applied_keeps_the_original_error(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    hub.fail[("comment", 1)] = GhTimeout(write=True)  # raised before it is applied
    code, out, _ = apply(hub, capsys)
    assert code == 1 and "outcome unknown" in out and hub.kinds() == ["comment"] and hub.ledger_comments() == [] and hub.issues[N]["state"] == "open"


def test_a_lost_comment_reply_whose_reread_fails_keeps_the_original_error(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    hub.fail_after[("comment", 1)] = GhTimeout(write=True)
    real = hub.rest

    def rest(method, path, body=None):
        if method == "GET" and "/comments" in path and hub.count["comment"]:
            raise GhError("E_GH_HTTP", 502, "bad gateway")
        return real(method, path, body)
    hub.rest = rest
    code, out, _ = apply(hub, capsys)
    assert code == 1 and "outcome unknown" in out and hub.kinds() == ["comment"]


def test_a_failed_status_write_reports_what_already_happened(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    hub.fail[("set_status", 1)] = GhError("E_GH_HTTP", 422, "nope")
    code, out, _ = apply(hub, capsys)
    assert code == 1 and "the ledger comment was posted" in out and "#11 was closed" in out and hub.kinds() == ["comment", "close", "set_status"]


def test_a_close_that_did_not_take_is_reported(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    real = hub.rest

    def rest(method, path, body=None):
        out = real(method, path, body)
        if method == "PATCH":
            out["state_reason"] = "not_planned"
        return out
    hub.rest = rest
    code, out, _ = apply(hub, capsys)
    assert code == 1 and "E_CLOSE_NOT_APPLIED" in out and status_writes(hub) == []


# ---- AC4: already closed (by a PR, a commit, the board or a person) is recorded, not closed again ----
CLOSERS = {
    "pr": ({"__typename": "PullRequest", **pull(41, oid=OID2)}, "PR #41", OID2),
    "commit": ({"__typename": "Commit", "oid": OID2, "repository": {"nameWithOwner": "octo/demo"}}, "commit " + OID2[:12], OID2),
    "board": ({"__typename": "ProjectV2"}, "the Project board", OID),
    "person": (None, "a person or an unknown route", OID),
}


@pytest.mark.parametrize("who", list(CLOSERS))
def test_already_closed_issue_is_recorded_not_reclosed(tmp_path, monkeypatch, capsys, who):
    closer, label, oid = CLOSERS[who]
    repo(tmp_path, monkeypatch)
    hub = DoneHub(state="closed", status="Done", closer=closer)
    code, out, _ = apply(hub, capsys)
    assert code == 0 and hub.kinds() == ["comment"]  # nothing but the missing comment
    assert not [c for c in hub.sent() if c[0] == "rest" and c[1] == "PATCH"] and status_writes(hub) == []
    assert "Step 2: skip: already closed (completed) by " + label in out and "closed #11 as completed" not in out
    [c] = hub.ledger_comments()
    assert "**Closed by:** " + label in c["body"] and oid in c["body"]


def test_a_closed_issue_whose_item_is_not_done_still_gets_its_status(tmp_path, monkeypatch, capsys, naps):
    repo(tmp_path, monkeypatch)
    hub = DoneHub(state="closed", status="In Progress", closer=None)
    hub.post(done.comment_text(Config("octo", "demo"), N, {"pr": 40, "oid": OID, "base": "main"}, None, done.check(ledger("x", "x"))))  # the comment is already there
    code, out, _ = apply(hub, capsys)
    assert code == 0 and hub.kinds() == ["set_status"] and naps == []  # no close of ours to wait for


def test_a_closed_issue_with_no_merged_pr_has_a_comment_without_a_link(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub(state="closed", status="Done", closer=None, linked=[])
    code, out, _ = apply(hub, capsys)
    assert code == 0 and hub.kinds() == ["comment"] and "Merge: none found" in out
    assert "no merge commit was found" in hub.ledger_comments()[0]["body"] and "blob/" not in hub.ledger_comments()[0]["body"]


def _merged(tmp_path, marks, text=None):
    """A git repo whose one commit holds the ledger with `marks`; returns its id (the merge commit the comment links)."""
    import subprocess

    def git(*a):
        return subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *a],
                              check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    (tmp_path / LEDGER).write_text(text or ledger(*marks), encoding="utf-8")
    git("add", LEDGER)
    git("commit", "-q", "-m", "ledger")
    return git("rev-parse", "HEAD")


def test_the_comment_says_when_the_linked_ledger_lacks_the_ticks(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    oid = _merged(tmp_path, (" ", " "))  # merged unticked
    (tmp_path / LEDGER).write_text(ledger("x", "x"), encoding="utf-8")  # ticked after the merge, not committed
    hub = DoneHub(linked=[pull(oid=oid)])
    code, out, _ = run(hub, capsys)
    assert code == 0 and "blob/" + oid in out
    assert "The linked file at the merge commit has 0 of 2 ticked" in out  # in the comment the reader sees next to the link
    assert "warning: the ledger at merge commit %s has 0 of 2 boxes ticked" % oid[:12] in out


def test_the_comment_has_no_note_when_the_linked_ledger_is_the_one_counted(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    oid = _merged(tmp_path, ("x", "x"))
    hub = DoneHub(linked=[pull(oid=oid)])
    code, out, _ = run(hub, capsys)
    assert code == 0 and "blob/" + oid in out and "The linked file" not in out and "warning: the ledger" not in out


def _with_acs(n, marks):
    return "### Acceptance criteria\n" + "".join("- criterion %d\n" % i for i in range(1, n + 1)) + "\n" + ledger(*marks)


def test_a_different_ac_count_with_the_same_boxes_gives_no_note(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    oid = _merged(tmp_path, (), _with_acs(2, ("x", "x")))
    (tmp_path / LEDGER).write_text(_with_acs(3, ("x", "x")), encoding="utf-8")  # one more criterion after the merge, the same 2 of 2 boxes
    assert done.check(_with_acs(3, ("x", "x")))["acs"] != done.check(_with_acs(2, ("x", "x")))["acs"]
    code, out, _ = run(DoneHub(linked=[pull(oid=oid)]), capsys)
    assert code == 0 and "blob/" + oid in out and "The linked file" not in out and "warning: the ledger" not in out


def test_a_merge_commit_that_cannot_be_read_is_not_called_missing(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    _merged(tmp_path, ("x", "x"))
    code, out, _ = run(DoneHub(linked=[pull(oid=OID)]), capsys)  # OID is not in this repository
    assert code == 0 and "warning: the ledger at merge commit %s could not be read here" % OID[:12] in out
    assert "does not hold this file" not in out and "has no ledger file" not in out


def test_a_merge_commit_without_the_ledger_is_named_in_the_comment(tmp_path, monkeypatch, capsys):
    import subprocess
    repo(tmp_path, monkeypatch)
    git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run(git + ["init", "-q"], check=True)
    (tmp_path / "other.txt").write_text("x")
    subprocess.run(git + ["add", "other.txt"], check=True)
    subprocess.run(git + ["commit", "-qm", "no ledger"], check=True)
    oid = subprocess.run(git + ["rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    code, out, _ = run(DoneHub(linked=[pull(oid=oid)]), capsys)
    assert code == 0 and "The linked merge commit does not hold this file" in out
    assert "warning: merge commit %s has no ledger file" % oid[:12] in out and "could not be read here" not in out


def test_the_issue_closed_between_the_plan_and_the_write_is_not_closed_again(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    real = hub.rest

    def rest(method, path, body=None):
        out = real(method, path, body)
        if method == "POST" and path.endswith("/comments"):
            hub.issues[N].update(state="closed", state_reason="completed")  # a person closed it just now
        return out
    hub.rest = rest
    code, out, _ = apply(hub, capsys)
    assert code == 0 and hub.kinds() == ["comment", "set_status"] and "closed #11" not in out


# ---- AC5: a second run is a no-op ----
def test_done_second_run_is_idempotent(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    assert apply(hub, capsys)[0] == 0
    before = list(hub.writes)
    code, out, _ = apply(hub, capsys)
    assert code == 0 and hub.writes == before and "nothing to do" in out and "nothing sent: already done" in out
    assert len(hub.ledger_comments()) == 1
    code, out, _ = run(hub, capsys)  # and the preview says the same
    assert code == 0 and "nothing to do" in out


def test_a_marker_copied_by_someone_else_does_not_count(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    hub.post("Looks good " + done.MARKER % N, user=2002)  # another identity quotes the marker
    code, out, _ = apply(hub, capsys)
    assert code == 0 and hub.kinds()[0] == "comment" and len(hub.ledger_comments()) == 2
    assert [c["user"] for c in hub.ledger_comments()] == [2002, 1001]


def test_a_marker_for_another_story_does_not_count(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    hub.post("<!-- spec-kit-ghp:done:12 -->")
    assert apply(hub, capsys)[0] == 0 and hub.kinds()[0] == "comment"


def test_the_ledger_comment_carries_the_marker(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    apply(hub, capsys)
    assert hub.ledger_comments()[0]["body"].endswith("\n\n<!-- spec-kit-ghp:done:11 -->")


def test_the_viewer_is_not_asked_when_no_comment_has_a_marker(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    hub.post("plain")
    hub._viewer = None  # would assert if asked
    assert run(hub, capsys)[0] == 0


def test_comments_that_could_not_be_joined_are_reported(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    cid = hub.post("a comment of a deleted account")
    real = hub._rest_comment
    hub._rest_comment = lambda c: {**real(c), "user": None}
    code, out, _ = run(hub, capsys)
    assert code == 0 and "1 comment(s) could not be verified" in out and cid


# ---- AC6: the closer and the merged PR come from GraphQL; ghp closes it otherwise ----
def test_closer_is_read_from_graphql_not_rest(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub(state="closed", status="Done", closer={"__typename": "PullRequest", **pull(41, oid=OID2)})
    hub.issues[N].update(commit_id=OID, closed_by={"login": "someone"})  # REST fields that must never be used for the closer
    code, out, _ = apply(hub, capsys)
    assert code == 0 and OID2[:12] in out and OID[:12] not in out and "PR #41" in out
    assert [c for c in hub.calls if c[0] == "graphql" and "closedByPullRequestsReferences" in c[1] and "closer" in c[1]]
    paths = {p for _, p in hub.rest_paths}
    assert not [p for p in paths if "events" in p or "timeline" in p or "commit" in p]  # no REST event, timeline or commit read
    assert "commit_id" not in done.QUERIES["facts"] and "commit_id" not in open(done.__file__).read().replace("`commit_id`", "")


def test_an_open_issue_needs_a_merged_pr(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    for linked in ([], [pull(merged=False, oid=None)], [pull(repo="octo/other")], [pull(repo=None)]):
        hub = DoneHub(linked=linked)
        code, out, err = apply(hub, capsys)
        assert code == 2 and "E_NOT_MERGED" in err and hub.sent() == [] and not hub.armed


def test_a_closer_pr_that_is_not_merged_is_refused(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub(state="closed", status="Done", closer={"__typename": "PullRequest", **pull(41, merged=False, oid=None)})
    code, _, err = apply(hub, capsys)
    assert code == 2 and "E_PR_NOT_MERGED" in err and hub.sent() == []


def test_a_closer_pr_of_another_repository_is_not_this_repos_merge(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub(state="closed", status="Done", closer={"__typename": "PullRequest", **pull(41, oid=OID2, repo="octo/other")}, linked=[pull(40)])
    code, out, _ = run(hub, capsys)
    assert code == 0 and "PR #40" in out and OID2[:12] not in out and "a person or an unknown route" in out


def test_the_latest_merged_pr_is_the_merge_and_a_tie_is_refused(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub(linked=[pull(40, at="2026-11-01T10:00:00Z"), pull(42, oid=OID2, at="2026-11-05T10:00:00Z"), pull(43, merged=False, oid=None)])
    code, out, _ = run(hub, capsys)
    assert code == 0 and "PR #42" in out and OID2[:12] in out
    hub = DoneHub(linked=[pull(40, at="2026-11-05T10:00:00Z"), pull(42, oid=OID2, at="2026-11-05T10:00:00Z")])
    code, _, err = apply(hub, capsys)
    assert code == 2 and "E_PR_AMBIGUOUS" in err and hub.sent() == []


def test_an_older_closed_event_of_a_reopened_issue_is_not_a_closer(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub(closer={"__typename": "PullRequest", **pull(41, merged=False, oid=None)})  # the event of an earlier close; the issue is open now
    code, out, _ = run(hub, capsys)
    assert code == 0 and "PR #40" in out


@pytest.mark.parametrize("bad", [
    lambda d: d["node"].pop("subIssuesSummary"), lambda d: d["node"].update(closedByPullRequestsReferences=None),
    lambda d: d["node"]["closedByPullRequestsReferences"]["nodes"].append({"number": "x", "merged": True}),
    lambda d: d["node"]["closedByPullRequestsReferences"]["nodes"].__setitem__(0, pull(oid="nothex")),
    lambda d: d["node"]["closedByPullRequestsReferences"]["nodes"].__setitem__(0, {**pull(), "mergeCommit": None}),
    lambda d: d.update(node=None)])
def test_a_facts_reply_of_the_wrong_shape_is_a_bad_body_and_sends_nothing(tmp_path, monkeypatch, capsys, bad):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    real = hub.graphql

    def graphql(q, v=None):
        out = real(q, v)
        if "closedByPullRequestsReferences" in q:
            out = copy.deepcopy(out)
            bad(out)
        return out
    hub.graphql = graphql
    code, _, err = apply(hub, capsys)
    assert code == 1 and "E_GH_BAD_BODY" in err and hub.sent() == [] and not hub.armed


def test_more_than_fifty_linked_prs_are_refused(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    real = hub.graphql
    hub.graphql = lambda q, v=None: ({**real(q, v), "node": {**real(q, v)["node"], "closedByPullRequestsReferences": {"pageInfo": {"hasNextPage": True}, "nodes": []}}}
                                     if "closedByPullRequestsReferences" in q else real(q, v))
    assert apply(hub, capsys)[0] == 2 and hub.sent() == []


# ---- AC7: a comment with a summary and a link; no attachment call ----
def test_ledger_is_a_comment_and_a_link(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    assert apply(hub, capsys)[0] == 0
    [c] = hub.ledger_comments()
    assert "**Ledger summary** (dod/DoD_11.md): 0 acceptance criteria; DoD checklist 2 of 2 ticked." in c["body"]
    assert "[DoD_11.md](https://github.com/octo/demo/blob/%s/dod/DoD_11.md) at merge commit `%s` (PR #40)" % (OID, OID[:12]) in c["body"]
    writes = [(m, p) for m, p in hub.rest_paths if m != "GET"]
    assert writes == [("POST", "repos/octo/demo/issues/11/comments"), ("PATCH", "repos/octo/demo/issues/11")]  # no upload, attachment or asset call
    assert "ledger text" not in c["body"] and "item 1" not in c["body"]  # counts only: nothing the ledger says is copied


def test_the_comment_has_no_live_mention_and_the_path_is_encoded(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    (tmp_path / "dod" / "@team").mkdir()
    (tmp_path / "dod" / "@team" / "DoD_11.md").write_text(ledger("x"), encoding="utf-8")
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", "project_number": 3, "dod_path": "dod/@team"}))
    hub = DoneHub()
    code, out, _ = apply(hub, capsys)
    [c] = hub.ledger_comments()
    assert code == 0 and "@" not in c["body"] and "dod/%40team/DoD_11.md" in c["body"] and comments.NO_MENTION in c["body"]
    assert "@" not in out


def test_the_ledger_path_cannot_break_out_of_the_link(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    weird = "dod/a) [x](http://evil.invalid"
    (tmp_path / "dod" / "a) [x](http:").mkdir(parents=True)
    (tmp_path / "dod" / "a) [x](http:" / "evil.invalid").mkdir()
    (tmp_path / "dod" / "a) [x](http:" / "evil.invalid" / "DoD_11.md").write_text(ledger("x"), encoding="utf-8")
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", "project_number": 3, "dod_path": weird}))
    hub = DoneHub()
    assert apply(hub, capsys)[0] == 0
    body = hub.ledger_comments()[0]["body"]
    assert "(http://evil.invalid" not in body and "%29%20%5Bx%5D%28http%3A/evil.invalid" in body


def test_the_dod_dir_must_stay_inside_the_repository(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch, dod_path="../elsewhere")
    hub = DoneHub()
    code, _, err = apply(hub, capsys)
    assert code == 2 and "E_DOD_DIR" in err and hub.calls == []


def test_the_defang_is_the_last_word_on_the_comment(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    (tmp_path / "dod" / "@team").mkdir()
    (tmp_path / "dod" / "@team" / "DoD_11.md").write_text(ledger("x"), encoding="utf-8")
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", "dod_path": "dod/@team"}))
    hub = DoneHub(state="closed", linked=[])  # no merge commit: the path is shown as text
    assert apply(hub, capsys)[0] == 0 and "@" not in hub.ledger_comments()[0]["body"]


# ---- AC8: the doctor-style warning when a workflow turns a board drag into a close ----
def test_done_warns_when_closing_workflows_are_enabled(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub(workflows=[("Item closed", True), ("Auto-close issue", True), ("Pull request merged", True), ("Item reopened", True)])
    code, out, _ = run(hub, capsys)
    assert code == 0
    for name in done.CLOSING_WORKFLOWS:
        assert done.WORKFLOW_WARNING % name in out
    assert out.count("warning: the Project workflow") == 2 and "closes its issue without the gate" in out
    code, out, _ = apply(hub, capsys)  # printed on apply too
    assert code == 0 and out.count("warning: the Project workflow") == 2
    only = DoneHub(workflows=[("Auto-close issue", True), ("Item closed", False)])
    assert done.WORKFLOW_WARNING % "Auto-close issue" in run(only, capsys)[1]
    assert "warning: the Project workflow" not in run(DoneHub(), capsys)[1]  # none enabled: no warning


def test_a_warning_is_part_of_the_approved_plan(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    assert run(DoneHub(), capsys, "--plan-out", "plan.md")[0] == 0
    hub = DoneHub(workflows=[("Item closed", True)])
    code, _, err = apply(hub, capsys, "--expect-plan", "plan.md")
    assert code == 2 and "plan changed since approval" in err and hub.sent() == [] and not hub.armed


# ---- the plan binds what is sent ----
def test_expect_plan_binds_the_apply(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    assert run(hub, capsys, "--plan-out", "plan.md")[0] == 0
    hub.comments.clear()
    assert apply(hub, capsys, "--expect-plan", "plan.md")[0] == 0 and hub.kinds() == ["comment", "close", "set_status"]
    hub = DoneHub()
    hub.linked = [pull(40, oid=OID2)]  # the merge commit changed after the approval
    code, _, err = apply(hub, capsys, "--expect-plan", "plan.md")
    assert code == 2 and "plan changed since approval" in err and hub.sent() == [] and (tmp_path / "plan.md.new").exists()


def test_the_digest_covers_the_exact_data(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    outs = []
    for mutate in (lambda h: None, lambda h: h.issues[N].update(title="Ship the loyalty points "), lambda h: setattr(h, "linked", [pull(40, oid=OID2)]),
                   lambda h: h.status.update({ITEM: "Todo"}), lambda h: setattr(h, "subs", (3, 1))):
        hub = DoneHub()
        mutate(hub)
        outs.append(re.search(r"digest: (\w+)", run(hub, capsys)[1]).group(1))
    assert len(set(outs)) == len(outs)


# ---- the Project ----
def test_no_project_means_close_and_comment_only(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo"}))
    hub = DoneHub()
    code, out, _ = apply(hub, capsys)
    assert code == 0 and hub.kinds() == ["comment", "close"] and "no Project is configured" in out and not [c for c in hub.calls if "projectItems" in str(c)]


def test_an_issue_not_on_the_board_is_not_added(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub(on_board=False)
    code, out, _ = apply(hub, capsys)
    assert code == 0 and hub.kinds() == ["comment", "close"] and "is not on the Project; ghp does not add it" in out


def test_an_item_with_no_status_is_set_to_done(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub(status=None)
    code, out, _ = apply(hub, capsys)
    assert code == 0 and "Status: (none) -> Done" in out and hub.status[ITEM] == "Done"


def test_a_board_that_cannot_be_read_stops_before_any_write(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    real = hub.graphql

    def graphql(q, v=None):
        if "fields(" in q:
            raise GhError("E_GH_HTTP", 403, "scope")
        return real(q, v)
    hub.graphql = graphql
    code, _, err = apply(hub, capsys)
    assert code == 1 and hub.sent() == [] and not hub.armed


def test_sub_issues_still_open_are_a_warning_not_a_block(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    hub.subs = (4, 1)
    code, out, _ = apply(hub, capsys)
    assert code == 0 and "warning: 3 of 4 sub-issues are still open; done does not close them" in out and hub.issues[N]["state"] == "closed"


def test_a_pull_request_number_is_refused(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    (tmp_path / "dod" / "DoD_12.md").write_text(ledger("x"), encoding="utf-8")
    hub = DoneHub()
    hub.seed("T002", 12, "A PR", pr=True)
    code, _, err = run(hub, capsys, "--apply", n="12")
    assert code == 2 and "pull request" in err and hub.sent() == []


# ---- review round 1 ----
ISSUE_PATH = "repos/octo/demo/issues/11"


def on_rest(hub, when):
    """Run `when(method, path, hub)` after every REST call of the hub (a person acting in the window between two of ghp's calls)."""
    real = hub.rest

    def rest(method, path, body=None):
        out = real(method, path, body)
        when(method.upper(), path, hub)
        return out
    hub.rest = rest


@pytest.mark.parametrize("reason", ["not_planned", "duplicate", None])
@pytest.mark.parametrize("argv", [(), ("--apply",)])
def test_a_closed_issue_that_was_not_completed_is_refused(tmp_path, monkeypatch, capsys, reason, argv):
    repo(tmp_path, monkeypatch)
    hub = DoneHub(state="closed", status="In Progress", closer=None)
    hub.issues[N]["state_reason"] = reason
    code, out, err = run(hub, capsys, *argv)
    assert code == 2 and "E_CLOSED_NOT_COMPLETED" in err and hub.sent() == [] and not hub.armed and hub.writes == []
    assert "closed as %s" % reason in err and not hub.comments


def test_closed_as_not_planned_in_the_window_before_the_apply_sends_nothing(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    cfg = Config.load()
    p = done.plan_done(hub, cfg, issues.get(hub, Ref("octo", "demo", N)), done.check(ledger("x")))
    hub.issues[N].update(state="closed", state_reason="not_planned")
    hub.arm()
    res = done.apply_done(hub, cfg, p)
    assert "E_CLOSED_NOT_COMPLETED" in res["error"] and hub.sent() == [] and res["unknown"] is False


def test_closed_as_not_planned_between_the_comment_and_the_close_stops_before_the_close(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    on_rest(hub, lambda m, path, h: m == "POST" and h.issues[N].update(state="closed", state_reason="not_planned"))
    code, out, _ = apply(hub, capsys)
    assert code == 1 and "E_CLOSED_NOT_COMPLETED" in out and hub.kinds() == ["comment"] and status_writes(hub) == []


def test_closed_as_not_planned_in_the_window_is_reported_even_without_a_project(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo"}))
    hub = DoneHub()
    on_rest(hub, lambda m, path, h: m == "POST" and h.issues[N].update(state="closed", state_reason="not_planned"))
    code, out, _ = apply(hub, capsys)
    assert code == 1 and "E_CLOSED_NOT_COMPLETED" in out and hub.kinds() == ["comment"]


def test_reopened_between_the_plan_and_the_apply_sends_nothing(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub(state="closed", status="In Progress", closer=None)
    cfg = Config.load()
    p = done.plan_done(hub, cfg, issues.get(hub, Ref("octo", "demo", N)), done.check(ledger("x")))
    hub.issues[N].update(state="open", state_reason="reopened")
    hub.arm()
    res = done.apply_done(hub, cfg, p)
    assert "E_REOPENED" in res["error"] and hub.sent() == []


def test_reopened_after_the_comment_never_gets_a_done_write(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub(state="closed", status="In Progress", closer=None)
    on_rest(hub, lambda m, path, h: m == "POST" and h.issues[N].update(state="open", state_reason="reopened"))
    code, out, _ = apply(hub, capsys)
    assert code == 1 and "E_REOPENED" in out and hub.kinds() == ["comment"] and status_writes(hub) == [] and hub.status[ITEM] == "In Progress"


def test_reopened_during_the_wait_never_gets_a_done_write(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    real = hub.graphql

    def graphql(q, v=None):
        if "fieldValueByName" in q and hub.issues[N]["state"] == "closed":
            hub.issues[N].update(state="open", state_reason="reopened")  # a person reopens it while ghp waits
        return real(q, v)
    hub.graphql = graphql
    code, out, _ = apply(hub, capsys)
    assert code == 1 and "E_REOPENED" in out and hub.kinds() == ["comment", "close"] and status_writes(hub) == [] and hub.status[ITEM] == "In Progress"


def test_closed_as_not_planned_during_the_wait_never_gets_a_done_write(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    real = hub.graphql

    def graphql(q, v=None):
        if "fieldValueByName" in q and hub.issues[N]["state_reason"] == "completed":
            hub.issues[N].update(state="closed", state_reason="not_planned")
        return real(q, v)
    hub.graphql = graphql
    code, out, _ = apply(hub, capsys)
    assert code == 1 and "E_CLOSED_NOT_COMPLETED" in out and status_writes(hub) == []


def test_the_status_step_still_passes_the_real_true_after_the_reread(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub(state="closed", status="In Progress", closer=None)
    hub.post(done.comment_text(Config("octo", "demo"), N, {"pr": 40, "oid": OID, "base": "main"}, None, done.check(ledger("x", "x"))))
    assert apply(hub, capsys)[0] == 0 and hub.status[ITEM] == "Done"


def test_an_issue_someone_else_just_closed_is_not_our_close_and_not_waited_for(tmp_path, monkeypatch, capsys, naps):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    real, gets = hub.rest, []

    def rest(method, path, body=None):
        if method == "GET" and path == ISSUE_PATH:
            gets.append(1)
            if len(gets) == 4:  # the plan, the first look, the look before the close, then the read inside issues.close: a person closed it just now
                hub.issues[N].update(state="closed", state_reason="completed")
        return real(method, path, body)
    hub.rest = rest
    code, out, _ = apply(hub, capsys)
    assert len(gets) >= 4 and code == 0 and hub.kinds() == ["comment", "set_status"] and "closed #11" not in out and naps == []
    assert not [c for c in hub.sent() if c[0] == "rest" and c[1] == "PATCH"]


def test_a_commit_closer_of_another_repository_is_refused(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    for repository in ({"nameWithOwner": "octo/other"}, None, {"nameWithOwner": "fork-owner/demo"}):
        hub = DoneHub(state="closed", status="Done", closer={"__typename": "Commit", "oid": OID2, "repository": repository})
        for argv in ((), ("--apply",)):
            code, out, err = run(hub, capsys, *argv)
            assert code == 2 and "E_CLOSER_FOREIGN" in err and hub.sent() == [] and not hub.comments and OID2[:12] not in out


def test_a_commit_closer_of_this_repository_is_accepted_whatever_the_case(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub(state="closed", status="Done", closer={"__typename": "Commit", "oid": OID2, "repository": {"nameWithOwner": "OCTO/Demo"}})
    code, out, _ = apply(hub, capsys)
    assert code == 0 and "blob/%s/" % OID2 in hub.ledger_comments()[0]["body"]


@pytest.mark.parametrize("closer", [{"__typename": "Commit", "oid": "x", "repository": {"nameWithOwner": "octo/demo"}},
                                    {"__typename": "Commit", "oid": OID2.upper(), "repository": {"nameWithOwner": "octo/demo"}},
                                    {"__typename": "Commit", "oid": OID2, "repository": {"nameWithOwner": 5}},
                                    {"__typename": "Issue"}, {"__typename": "Commit", "oid": 7}])
def test_a_closer_of_the_wrong_shape_is_a_bad_body(tmp_path, monkeypatch, capsys, closer):
    repo(tmp_path, monkeypatch)
    hub = DoneHub(state="closed", status="Done", closer=closer)
    code, _, err = apply(hub, capsys)
    assert code == 1 and "E_GH_BAD_BODY" in err and hub.sent() == []


@pytest.mark.parametrize("marks", [("*", "x"), ("+", "x")])
def test_the_comment_counts_boxes_as_the_gate_does(tmp_path, monkeypatch, capsys, marks):
    repo(tmp_path, monkeypatch)
    (tmp_path / LEDGER).write_text(ledger("x", extra="") .replace("- [x] item 1\n", "* [x] a\n  - [x] b\n> - [x] c\n1. [X] d\n+ [x] e\n"), encoding="utf-8")
    hub = DoneHub()
    code, out, _ = apply(hub, capsys)
    assert code == 0 and "DoD checklist: ready, 5 of 5 ticked" in out
    assert "DoD checklist 5 of 5 ticked." in hub.ledger_comments()[0]["body"] and "0 of 0" not in hub.ledger_comments()[0]["body"]


def test_the_comment_counts_follow_the_gate_for_acs_too(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    chk = done.check(ledger("x", "x"))
    assert chk["boxes"] == 2 and chk["unticked"] == [] and chk["acs"] == 0
    assert "2 of 2 ticked" in done.summary(Config("octo", "demo"), N, chk)


@pytest.mark.parametrize("box", ["> - [ ] quoted", ">> * [ ] nested", "  1. [ ] numbered", "+ [-] other mark"])
def test_an_unticked_box_in_any_list_form_blocks_apply(tmp_path, monkeypatch, capsys, box):
    repo(tmp_path, monkeypatch)
    (tmp_path / LEDGER).write_text(ledger("x", extra=box + "\n"), encoding="utf-8")
    hub = DoneHub()
    code, out, err = apply(hub, capsys)
    assert code == 2 and "E_DOD_NOT_TICKED" in err and hub.calls == []


def test_the_plan_names_the_base_of_the_merged_pr(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    assert "Merge: PR #40 into main, commit " in run(DoneHub(), capsys)[1]
    out = run(DoneHub(linked=[pull(base="release/1.x")]), capsys)[1]
    assert "Merge: PR #40 into release/1.x, commit " in out
    hub = DoneHub(state="closed", status="Done", closer={"__typename": "PullRequest", **pull(41, oid=OID2, base="dev")})
    assert "Merge: PR #41 into dev, commit " in run(hub, capsys)[1]
    assert re.search(r"digest: (\w+)", run(DoneHub(linked=[pull(base="release/1.x")]), capsys)[1]).group(1) != re.search(
        r"digest: (\w+)", run(DoneHub(), capsys)[1]).group(1)


@pytest.mark.parametrize("base", [None, "", 5])
def test_a_pr_without_a_base_branch_name_is_a_bad_body(tmp_path, monkeypatch, capsys, base):
    repo(tmp_path, monkeypatch)
    hub = DoneHub(linked=[{**pull(), "baseRefName": base}])
    assert apply(hub, capsys)[0] == 1 and hub.sent() == []


def test_the_schema_subset_knows_the_new_fields():
    text = (Path(__file__).parent / "fixtures" / "gh" / "schema.subset.graphql").read_text(encoding="utf-8")
    block = lambda name: re.search(r"^(?:type|union) %s\b.*?^(?:}|\S)" % name, text, re.M | re.S).group(0)
    assert "repository: Repository!" in block("Commit") and "oid:" in block("Commit")
    assert "baseRefName: String!" in block("PullRequest")
    assert "repository { nameWithOwner }" in done.QUERIES["facts"] and "baseRefName" in done.QUERIES["facts"]


# ---- the checklist reader: linear on hostile input ----
@pytest.mark.parametrize("size", [30_000, 300_000])
@pytest.mark.parametrize("make", [lambda n: " " * n, lambda n: "-" + " " * n, lambda n: "1" * n, lambda n: "> " * (n // 2), lambda n: "- [" + " " * n,
                                  lambda n: "- [x]" + " " * n + "z", lambda n: ("- [ ]" + " " * 20 + "\n") * (n // 25), lambda n: "\t" * n + "- ", lambda n: "1" * n + "\n- ["])
def test_the_checklist_reader_is_linear(size, make):
    md = "%s\n\n## 5. DoD\n%s\n" % (dod.SECTION2, make(size))
    t = time.perf_counter()
    done.check(md)
    assert time.perf_counter() - t < 3.0


def test_the_digest_covers_every_field_it_is_given():
    base = {"issue": issues._issue(DoneHub().issues[N], Ref("octo", "demo", N)), "chk": {"ready": True, "unticked": [], "boxes": 1}, "merge": None, "how": None,
            "comment": "é", "comment_id": None, "subs": (0, 0), "dropped": 0, "item_id": None, "current": None, "to": "Done", "to_id": None,
            "project_id": None, "status": "no-project", "warned": [], "given": None}
    assert re.fullmatch(r"[0-9a-f]{64}", done._digest(base))
    for key, other in (("comment", "e"), ("comment_id", 5), ("merge", {"pr": 1, "oid": OID, "base": "main"}), ("how", "PR #1"), ("subs", (1, 0)), ("dropped", 1), ("item_id", "x"),
                       ("current", ("Todo", "a")), ("to_id", "b"), ("project_id", "P"), ("status", "set"), ("warned", ["Item closed"]),
                       ("chk", {"ready": True, "unticked": ["a"], "boxes": 1}), ("given", [44, False]), ("at_merge", "missing")):
        assert done._digest({**base, key: other}) != done._digest(base), key


def test_apply_refuses_a_plan_that_is_not_ready_and_looks_again_before_it_posts(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = DoneHub()
    cfg = Config.load()
    p = done.plan_done(hub, cfg, issues.get(hub, Ref("octo", "demo", N)), done.check(ledger("x")))
    hub.arm()
    with pytest.raises(done.DoneError) as e:
        done.apply_done(hub, cfg, {**p, "chk": {"ready": False, "unticked": ["a"], "boxes": 1}})
    assert e.value.code == "E_DOD_NOT_TICKED" and hub.sent() == []
    hub.post(p["comment"])  # the same story was finished by another run after this plan was read
    res = done.apply_done(hub, cfg, p)
    assert res["error"] is None and hub.kinds() == ["close", "set_status"] and len(hub.ledger_comments()) == 1


def test_the_verb_is_registered_and_help_lists_it(capsys):
    assert "done" in cli.REGISTRY and cli.REGISTRY["done"][1] is done.run


# ---- T26a: `--pr N` finishes a story whose PR was merged into a branch GitHub does not close from (the live finding) ----
SIDE_OID = "c1b2c3d4e5f60718293a4b5c6d7e8f9012345678"


def pr_obj(number=44, **kw):
    """What `repository.issueOrPullRequest(number)` returns for a PR: merged into a side branch of this repository."""
    return {"__typename": "PullRequest", "id": "PR_%d" % number, "isCrossRepository": False, "title": "Pickup slots", "body": "Closes #11\n",
            "author": {"__typename": "User", "login": "octo-dev", "databaseId": 1001},
            **pull(number, oid=SIDE_OID, base="release/1.x", at="2026-11-09T11:00:00Z"), **kw}


def xref(number=44, **kw):
    """A CrossReferencedEvent on the issue made by PR `number` (GitHub keeps it, willCloseTarget false, when the base is not the default branch)."""
    return {"willCloseTarget": False, "source": {"__typename": "PullRequest", "id": "PR_%d" % number, **kw}}


def side(number=44, prs=None, xrefs=None, **kw):
    """The live AC5 story: nothing is linked by GitHub (`closedByPullRequestsReferences` is empty); PR `number` is merged into release/1.x."""
    hub = DoneHub(linked=[], **kw)
    hub.prs = {number: pr_obj(number)} if prs is None else prs
    hub.xrefs = [xref(number)] if xrefs is None else xrefs
    return hub


GIVEN = "(given with --pr; GitHub did not link it)"


def test_a_pr_merged_into_a_side_branch_is_refused_without_pr_and_the_refusal_names_the_flag(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = side()
    code, _, err = apply(hub, capsys)
    assert code == 2 and "E_NOT_MERGED" in err and "--pr N" in err and hub.sent() == [] and not hub.armed
    assert not any("issueOrPullRequest" in c[1] for c in hub.calls if c[0] == "graphql")  # without the flag no PR is looked up


def test_pr_finishes_a_story_merged_into_a_side_branch(tmp_path, monkeypatch, capsys, naps):
    repo(tmp_path, monkeypatch)
    hub = side()
    code, out, err = run(hub, capsys, "--pr", "44")
    assert code == 0 and err == "" and hub.sent() == [] and not hub.armed
    assert "Merge: PR #44 into release/1.x, commit " + SIDE_OID[:12] + " " + GIVEN in out
    assert "Step 2: close #11 as completed" in out
    code, out, err = apply(hub, capsys, "--pr", "44")
    assert code == 0 and err == "" and hub.kinds() == ["comment", "close", "set_status"]
    [c] = hub.ledger_comments()
    assert "blob/%s/dod/DoD_11.md" % SIDE_OID in c["body"] and "(PR #44)" in c["body"] and OID not in c["body"]  # the link is the PR's merge commit
    assert hub.issues[N]["state"] == "closed" and hub.status[ITEM] == "Done"


def test_pr_reads_only_through_graphql_and_never_rest_commit_fields(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = side()
    assert run(hub, capsys, "--pr", "44")[0] == 0
    [q] = [c for c in hub.calls if c[0] == "graphql" and "issueOrPullRequest" in c[1]]
    assert q[2]["number"] == 44 and type(q[2]["number"]) is int and q[2]["owner"] == "octo" and q[2]["name"] == "demo" and q[2]["issue"]
    assert not is_graphql_write(q[1]) and "commit_id" not in q[1] and "state_reason" not in q[1]
    assert not any("pulls" in p for _, p in hub.rest_paths)  # no REST pull request call


def test_pr_that_github_did_link_says_so_and_a_pr_it_did_not_link_wins_the_link(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = side()
    hub.linked = [pull(40, oid=OID2)]  # GitHub links another merged PR: the named one is the ledger link and is said not to be linked
    out = run(hub, capsys, "--pr", "44")[1]
    assert "Merge: PR #44 into release/1.x, commit " + SIDE_OID[:12] + " " + GIVEN in out and OID2[:12] not in out
    hub = side()
    hub.linked = [pull(44, oid=SIDE_OID, base="release/1.x")]
    out = run(hub, capsys, "--pr", "44")[1]
    assert "commit " + SIDE_OID[:12] + " (given with --pr)\n" in out and "GitHub did not link" not in out
    hub = side(state="closed", status="Done")
    hub.closer = {"__typename": "PullRequest", **pull(44, oid=SIDE_OID, base="release/1.x")}  # the closer is that PR: linked too
    assert "(given with --pr)\n" in run(hub, capsys, "--pr", "44")[1]


def test_pr_resolves_two_prs_merged_at_the_same_instant(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = side()
    hub.linked = [pull(40, oid=OID), pull(41, oid=OID2)]
    assert "E_PR_AMBIGUOUS" in apply(hub, capsys)[2] and hub.sent() == []
    code, out, _ = apply(hub, capsys, "--pr", "44")
    assert code == 0 and hub.kinds() == ["comment", "close", "set_status"]


def test_pr_on_an_already_closed_issue_records_it_and_does_not_close_again(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = side(state="closed", status="Done")
    hub.closer = None  # closed by a person: no closer to read
    code, out, _ = apply(hub, capsys, "--pr", "44")
    assert code == 0 and hub.kinds() == ["comment"] and not [c for c in hub.sent() if c[0] == "rest" and c[1] == "PATCH"] and status_writes(hub) == []
    assert "Step 2: skip: already closed (completed)" in out and "Merge: PR #44 into release/1.x" in out and GIVEN in out
    [c] = hub.ledger_comments()
    assert "blob/%s/" % SIDE_OID in c["body"]
    # an unmerged closer PR is still refused, whatever --pr says
    hub = side(state="closed", status="Done")
    hub.closer = {"__typename": "PullRequest", **pull(41, merged=False, oid=None)}
    assert "E_PR_NOT_MERGED" in apply(hub, capsys, "--pr", "44")[2] and hub.sent() == []


def test_pr_is_bound_by_the_plan_digest_and_expect_plan(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    digest = lambda h, *a: re.search(r"digest: (\w+)", run(h, capsys, *a)[1]).group(1)  # noqa: E731
    both = lambda: side(prs={44: pr_obj(44), 45: pr_obj(45)}, xrefs=[xref(44), xref(45)])  # noqa: E731
    assert digest(both(), "--pr", "44") != digest(both(), "--pr", "45")
    linked = both()
    linked.linked = [pull(44, oid=SIDE_OID, base="release/1.x")]
    assert digest(linked, "--pr", "44") != digest(linked)  # the same merge, but named by hand: another plan
    assert run(both(), capsys, "--pr", "44", "--plan-out", "plan.md")[0] == 0
    hub = both()
    code, _, err = apply(hub, capsys, "--pr", "45", "--expect-plan", "plan.md")
    assert code == 2 and "plan changed since approval" in err and hub.sent() == []
    hub = both()
    code, _, err = apply(hub, capsys, "--expect-plan", "plan.md")  # without the flag it is not even the same plan
    assert code == 2 and hub.sent() == []
    assert apply(both(), capsys, "--pr", "44", "--expect-plan", "plan.md")[0] == 0


@pytest.mark.parametrize("name,mutate,code", [
    ("not merged", lambda h: h.prs.update({44: pr_obj(merged=False, mergeCommit=None, mergedAt=None)}), "E_PR_NOT_MERGED"),
    ("another repository", lambda h: h.prs.update({44: pr_obj(baseRepository={"nameWithOwner": "octo/other"})}), "E_PR_FOREIGN"),
    ("no repository", lambda h: h.prs.update({44: pr_obj(baseRepository=None)}), "E_PR_FOREIGN"),
    ("from a fork", lambda h: h.prs.update({44: pr_obj(isCrossRepository=True)}), "E_PR_FORK"),
    ("no cross-reference event", lambda h: setattr(h, "xrefs", []), "E_PR_NOT_LINKED"),
    ("the event is another pr's", lambda h: setattr(h, "xrefs", [xref(45)]), "E_PR_NOT_LINKED"),
    ("the event's source is an issue", lambda h: setattr(h, "xrefs", [{"willCloseTarget": False, "source": {"__typename": "Issue", "id": "PR_44"}}]), "E_PR_NOT_LINKED"),
    ("the event has no source", lambda h: setattr(h, "xrefs", [{"willCloseTarget": False, "source": None}]), "E_PR_NOT_LINKED"),
    ("a bare #11 with no closing keyword", lambda h: h.prs.update({44: pr_obj(title="Pickup slots #11", body="see #11 and #11 again")}), "E_PR_NOT_LINKED"),
    ("a keyword for another issue", lambda h: h.prs.update({44: pr_obj(body="Closes #12\n")}), "E_PR_NOT_LINKED"),
    ("a keyword for the same number in another repository", lambda h: h.prs.update({44: pr_obj(body="Closes evil/x#11\n")}), "E_PR_NOT_LINKED"),
    ("a keyword for the same number in a sibling repository", lambda h: h.prs.update({44: pr_obj(body="Closes octo/other#11\n")}), "E_PR_NOT_LINKED"),
    ("a stranger's comment mention only (the event exists, the PR text never closes it)", lambda h: h.prs.update({44: pr_obj(title="Unrelated", body=None)}), "E_PR_NOT_LINKED"),
    ("character references that never settle", lambda h: h.prs.update({44: pr_obj(body="Closes &" + "amp;" * 9 + "#35;11")}), "E_PR_NOT_LINKED"),
    ("same number, other repository (the id differs)", lambda h: setattr(h, "xrefs", [{"willCloseTarget": False, "source": {"__typename": "PullRequest", "id": "PR_ELSEWHERE"}}]), "E_PR_NOT_LINKED"),
    ("an issue number", lambda h: h.prs.update({44: {"__typename": "Issue"}}), "E_NOT_A_PR"),
    ("no such number", lambda h: h.prs.clear(), "E_PR_NOT_FOUND"),
    ("more references than were read", lambda h: (setattr(h, "xrefs", [xref(45)]), setattr(h, "xrefs_more", True)), "E_TOO_MANY_REFS"),
])
def test_pr_refusals_send_nothing(tmp_path, monkeypatch, capsys, name, mutate, code):
    repo(tmp_path, monkeypatch)
    hub = side()
    mutate(hub)
    got, out, err = apply(hub, capsys, "--pr", "44")
    assert got == 2 and code in err and hub.sent() == [] and not hub.armed, name
    assert "no ledger" not in out and "Closes" not in out


PR_PATH = ["repository", "issueOrPullRequest"]
NOT_FOUND = {"type": "NOT_FOUND", "path": PR_PATH, "message": "Could not resolve to an issue or pull request with the number of 44."}


def graphql_failure(*errors):
    """The GhError the real `Gh` raises for this GraphQL error reply (through the conftest FakeRunner, as GitHub answered in T26f)."""
    from conftest import FakeRunner, http
    from ghp.gh import Gh
    with pytest.raises(GhError) as e:
        Gh(runner=FakeRunner(http(200, {"data": None, "errors": list(errors)}))).graphql("{ repository { issueOrPullRequest { id } } }")
    return e.value


@pytest.mark.parametrize("errors,code,exit_code", [
    ([NOT_FOUND], "E_PR_NOT_FOUND", 2),
    ([NOT_FOUND, NOT_FOUND], "E_PR_NOT_FOUND", 2),
    ([{**NOT_FOUND, "path": ["repository"]}], "E_GH_GRAPHQL", 1),  # the repository itself is missing: not this refusal
    ([{**NOT_FOUND, "path": None}], "E_GH_GRAPHQL", 1),
    ([{**NOT_FOUND, "type": "FORBIDDEN"}], "E_GH_GRAPHQL", 1),
    ([NOT_FOUND, {"type": "SERVICE_UNAVAILABLE", "message": "try later"}], "E_GH_GRAPHQL", 1),
    ([{"message": "Something went wrong"}], "E_GH_GRAPHQL", 1),
])
def test_pr_number_github_cannot_resolve_is_a_refusal_other_graphql_errors_stay_failures(tmp_path, monkeypatch, capsys, errors, code, exit_code):
    """T26g: live, GitHub answers a missing number with a GraphQL NOT_FOUND error, not a null (T26f-a)."""
    repo(tmp_path, monkeypatch)
    hub = side()
    failure, real = graphql_failure(*errors), hub.graphql

    def graphql(query, variables=None):
        if "issueOrPullRequest" in query:
            raise failure
        return real(query, variables)
    hub.graphql = graphql
    got, _, err = apply(hub, capsys, "--pr", "44")
    assert got == exit_code and code in err and hub.sent() == [] and not hub.armed


def test_the_event_may_be_anywhere_in_the_first_page(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = side(xrefs=[xref(i) for i in range(50, 60)] + [xref(44)])
    hub.xrefs_more = True  # more exist, but the link was found: that is enough
    assert apply(hub, capsys, "--pr", "44")[0] == 0 and hub.kinds() == ["comment", "close", "set_status"]


@pytest.mark.parametrize("bad", [
    {"repository": None, "node": {"timelineItems": {"pageInfo": {"hasNextPage": False}, "nodes": []}}},
    {"repository": {"issueOrPullRequest": {"__typename": "PullRequest", "id": 5, "isCrossRepository": False}}},
    {"repository": {"issueOrPullRequest": {**pr_obj(), "isCrossRepository": None}}},
    {"repository": {"issueOrPullRequest": {**pr_obj(), "isCrossRepository": "false"}}},
    {"repository": {"issueOrPullRequest": {**pr_obj(), "id": None}}},
    {"repository": {"issueOrPullRequest": {**pr_obj(), "merged": "true"}}},
    {"repository": {"issueOrPullRequest": {**pr_obj(), "mergeCommit": None}}},
    {"repository": {"issueOrPullRequest": pr_obj()}, "node": None},
    {"repository": {"issueOrPullRequest": pr_obj()}, "node": {"timelineItems": None}},
    {"repository": {"issueOrPullRequest": pr_obj()}, "node": {"timelineItems": {"pageInfo": {}, "nodes": []}}},
    {"repository": {"issueOrPullRequest": pr_obj()}, "node": {"timelineItems": {"pageInfo": {"hasNextPage": False}, "nodes": {}}}},
    {"repository": {"issueOrPullRequest": pr_obj()}, "node": {"timelineItems": {"pageInfo": {"hasNextPage": False}, "nodes": ["x"]}}},
    {"repository": {"issueOrPullRequest": pr_obj()}, "node": {"timelineItems": {"pageInfo": {"hasNextPage": False}, "nodes": [{"source": []}]}}},
    {"repository": {"issueOrPullRequest": []}, "node": {"timelineItems": {"pageInfo": {"hasNextPage": False}, "nodes": []}}},
    {"repository": {"issueOrPullRequest": {"__typename": 7}}, "node": {"timelineItems": {"pageInfo": {"hasNextPage": False}, "nodes": []}}},
])
def test_a_pr_reply_of_the_wrong_shape_is_a_bad_body_and_sends_nothing(tmp_path, monkeypatch, capsys, bad):
    repo(tmp_path, monkeypatch)
    hub = side()
    real = hub.graphql
    hub.graphql = lambda q, v=None: bad if "issueOrPullRequest" in q else real(q, v)
    code, _, err = apply(hub, capsys, "--pr", "44")
    assert code == 1 and "E_GH_BAD_BODY" in err and hub.sent() == [] and not hub.armed


@pytest.mark.parametrize("argv", [["--pr", "0"], ["--pr", "-5"], ["--pr", "true"], ["--pr", "True"], ["--pr", "1.0"], ["--pr", "+4"], ["--pr", " 4"], ["--pr", "4 "],
                                  ["--pr", ""], ["--pr", "0x10"], ["--pr", "1e2"], ["--pr", "٤٤"], ["--pr", "2147483648"], ["--pr", "9" * 40], ["--pr", "#44"],
                                  ["--pr", "44", "--pr", "44"], ["--pr", "44", "--pr", "45"], ["--pr"], ["--pr=0"], ["--pr", "007x"]])
def test_a_bad_pr_value_is_refused_by_the_parser_before_any_call(tmp_path, monkeypatch, capsys, argv):
    repo(tmp_path, monkeypatch)
    hub = side()
    with pytest.raises(SystemExit) as e:
        cli.main(["done", str(N), *argv, "--apply"], gh=hub)
    assert e.value.code == 2 and hub.calls == []


@pytest.mark.parametrize("value", ["44", "000044", "2147483647"])
def test_a_plain_positive_integer_is_a_pr_number(value):
    assert done._pr_number(value) == int(value)


@pytest.mark.parametrize("size", [30_000, 300_000])
def test_the_pr_number_reader_is_linear(size):
    for text in ("9" * size, "0" * size + "x", " " * size, "٤" * size):
        t = time.perf_counter()
        with pytest.raises(Exception):
            done._pr_number(text)
        assert time.perf_counter() - t < 1.0


def test_pr_text_from_github_is_escaped(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = side(prs={44: pr_obj(baseRefName="rel\x1b[31m‮")})
    code, out, _ = run(hub, capsys, "--pr", "44")
    assert code == 0 and "\x1b" not in out and "‮" not in out and "\\u001b[31m" in out


def test_the_digest_covers_the_pr_given_by_hand():
    hub = DoneHub()
    base = {"issue": issues._issue(hub.issues[N], Ref("octo", "demo", N)), "chk": {"ready": True, "unticked": [], "boxes": 1}, "merge": None, "how": None,
            "comment": "e", "comment_id": None, "subs": (0, 0), "dropped": 0, "item_id": None, "current": None, "to": "Done", "to_id": None,
            "project_id": None, "status": "no-project", "warned": [], "given": None}
    assert done._digest({**base, "given": 44}) != done._digest(base) and done._digest({**base, "given": 44}) != done._digest({**base, "given": 45})


def test_the_schema_subset_knows_the_pr_fields():
    text = (Path(__file__).parent / "fixtures" / "gh" / "schema.subset.graphql").read_text(encoding="utf-8")
    block = lambda name: re.search(r"^(?:type|union) %s\b.*?(?:^}|\Z|^\S)" % name, text, re.M | re.S).group(0)
    assert "source: ReferencedSubject!" in block("CrossReferencedEvent") and "willCloseTarget: Boolean!" in block("CrossReferencedEvent")
    assert "isCrossRepository: Boolean!" in block("PullRequest") and "id: ID!" in block("PullRequest")
    assert "issueOrPullRequest(" in block("Repository") and "IssueOrPullRequest" in text and "| PullRequest" in block("ReferencedSubject")
    assert "CROSS_REFERENCED_EVENT" in text and "pageInfo" in block("IssueTimelineItemsConnection")
    assert "issueOrPullRequest" in done.QUERIES["pr_link"] and "CROSS_REFERENCED_EVENT" in done.QUERIES["pr_link"]


# ---- review round 1: a CrossReferencedEvent proves only a MENTION, so the PR's own title or body must close THIS issue (I1) ----
@pytest.mark.parametrize("title,body", [("Pickup slots", "Closes #11\n"), ("Pickup slots, fixes #11", None), ("Pickup slots", "work\n\nResolves octo/demo#11"),
                                        ("Pickup slots", "closes OCTO/DEMO#011"), ("Pickup slots", "Fixes https://github.com/octo/demo/issues/11"),
                                        ("Pickup slots", "Closes #12, closes #11")])
def test_a_closing_keyword_for_this_issue_in_the_prs_title_or_body_is_enough(tmp_path, monkeypatch, capsys, title, body):
    repo(tmp_path, monkeypatch)
    hub = side(prs={44: pr_obj(title=title, body=body)})
    assert apply(hub, capsys, "--pr", "44")[0] == 0 and hub.kinds() == ["comment", "close", "set_status"]


def test_the_refusal_for_a_missing_keyword_says_what_is_missing(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = side(prs={44: pr_obj(body="see #11")})
    code, _, err = apply(hub, capsys, "--pr", "44")
    assert code == 2 and "E_PR_NOT_LINKED" in err and "closing keyword" in err and "Closes #11" in err and "nothing was sent" in err
    hub = side(xrefs=[])
    err = apply(hub, capsys, "--pr", "44")[2]
    assert "E_PR_NOT_LINKED" in err and "mention" in err and "own link" not in err and "only says" not in err


def test_the_pr_body_is_read_by_the_same_scanner_in_linear_time(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    for body in ("Closes " * 40_000, "#" * 300_000, "&amp;" * 60_000, "<!--" * 75_000, "Closes" + " " * 300_000 + "#11"):
        hub = side(prs={44: pr_obj(body=body)})
        t = time.perf_counter()
        assert apply(hub, capsys, "--pr", "44")[0] in (0, 2)
        assert time.perf_counter() - t < 15


def test_the_plan_shows_the_pr_title_merge_date_and_author_escaped(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = side(prs={44: pr_obj(title="Pickup \x1b[31mslots‮ Closes #11", author={"__typename": "User", "login": "octo\x1b-dev", "databaseId": 4242})})
    code, out, _ = run(hub, capsys, "--pr", "44")
    assert code == 0 and "\x1b" not in out and "‮" not in out
    assert "Pull request #44: " in out and "Pickup \\u001b[31mslots\\u202e Closes #11" in out and "merged 2026-11-09T11:00:00Z" in out
    assert "by octo\\u001b-dev (id 4242)" in out


def test_a_pr_without_an_author_is_shown_as_unknown_not_refused(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    out = run(side(prs={44: pr_obj(author=None)}), capsys, "--pr", "44")[1]
    assert "by (no author)" in out
    out = run(side(prs={44: pr_obj(author={"__typename": "Mannequin", "login": "ghost"})}), capsys, "--pr", "44")[1]
    assert "by ghost (no id)" in out


@pytest.mark.parametrize("name,change", [
    ("title", {"title": "Pickup slots (renamed) closes #11"}), ("merge date", {"mergedAt": "2026-11-09T11:00:01Z"}),
    ("author login", {"author": {"__typename": "User", "login": "someone-else", "databaseId": 1001}}),
    ("author id", {"author": {"__typename": "User", "login": "octo-dev", "databaseId": 1002}}), ("no author", {"author": None}),
])
def test_the_digest_changes_when_the_prs_title_date_or_author_change(tmp_path, monkeypatch, capsys, name, change):
    repo(tmp_path, monkeypatch)
    digest = lambda h: re.search(r"digest: (\w+)", run(h, capsys, "--pr", "44")[1]).group(1)  # noqa: E731
    assert digest(side()) == digest(side()) != digest(side(prs={44: pr_obj(**change)})), name
    assert run(side(prs={44: pr_obj()}), capsys, "--pr", "44", "--plan-out", "plan.md")[0] == 0
    code, _, err = apply(side(prs={44: pr_obj(**change)}), capsys, "--pr", "44", "--expect-plan", "plan.md")
    assert code == 2 and "plan changed since approval" in err


@pytest.mark.parametrize("bad", [{"title": None}, {"title": 5}, {"body": 5}, {"body": ["x"]}, {"author": "octo"}, {"author": {"login": 5}},
                                 {"author": {"__typename": "User"}}, {"author": {"__typename": "User", "login": "x", "databaseId": "7"}},
                                 {"author": {"__typename": "User", "login": "x", "databaseId": True}}])
def test_a_pr_with_a_title_body_or_author_of_the_wrong_shape_is_a_bad_body(tmp_path, monkeypatch, capsys, bad):
    repo(tmp_path, monkeypatch)
    hub = side(prs={44: pr_obj(**bad)})
    code, _, err = apply(hub, capsys, "--pr", "44")
    assert code == 1 and "E_GH_BAD_BODY" in err and hub.sent() == [] and not hub.armed


# M1: the label compares the PR itself, not just its number
def test_the_given_label_does_not_claim_a_link_that_is_another_prs(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = side()
    hub.linked = [pull(44, oid=OID2, repo="octo/other")]  # a PR with the same number in another repository
    assert GIVEN in run(hub, capsys, "--pr", "44")[1]
    hub = side()
    hub.closer = {"__typename": "PullRequest", **pull(44, oid=OID2, base="main")}  # the stale closer of an earlier close, the issue is open now
    assert hub.issues[N]["state"] == "open" and GIVEN in run(hub, capsys, "--pr", "44")[1]
    hub = side(state="closed", status="Done")
    hub.closer = {"__typename": "PullRequest", **pull(44, oid=OID2, repo="octo/other")}
    assert GIVEN in run(hub, capsys, "--pr", "44")[1]


# M2: --pr keeps every refusal of the base path
@pytest.mark.parametrize("repository", [{"nameWithOwner": "octo/other"}, None, {"nameWithOwner": "fork-owner/demo"}])
def test_pr_keeps_the_foreign_commit_closer_refusal(tmp_path, monkeypatch, capsys, repository):
    repo(tmp_path, monkeypatch)
    hub = side(state="closed", status="Done")
    hub.closer = {"__typename": "Commit", "oid": OID2, "repository": repository}
    for argv in ((), ("--apply",)):
        code, out, err = run(hub, capsys, "--pr", "44", *argv)
        assert code == 2 and "E_CLOSER_FOREIGN" in err and hub.sent() == [] and not hub.comments and SIDE_OID[:12] not in out


@pytest.mark.parametrize("reason", ["not_planned", "duplicate", None])
def test_pr_keeps_the_closed_not_completed_refusal(tmp_path, monkeypatch, capsys, reason):
    repo(tmp_path, monkeypatch)
    hub = side(state="closed", status="In Progress")
    hub.issues[N]["state_reason"] = reason
    code, _, err = apply(hub, capsys, "--pr", "44")
    assert code == 2 and "E_CLOSED_NOT_COMPLETED" in err and hub.sent() == [] and not hub.armed and not hub.comments


def test_pr_keeps_the_reopened_check_before_every_write(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = side(state="closed", status="In Progress")
    hub.closer = None
    cfg = Config.load()
    p = done.plan_done(hub, cfg, issues.get(hub, Ref("octo", "demo", N)), done.check(ledger("x")), 44)
    hub.issues[N].update(state="open", state_reason="reopened")
    hub.arm()
    res = done.apply_done(hub, cfg, p)
    assert "E_REOPENED" in res["error"] and hub.sent() == []
