import copy
import json
from pathlib import Path

import pytest

from conftest import FakeGh
from ghp import issues
from ghp.config import Config
from ghp.gh import GhError, GhTimeout
from ghp.refs import Ref

FIX = Path(__file__).parent / "fixtures" / "gh"
CFG = Config("octo", "demo")
BASE = "repos/octo/demo/issues"
LIST = BASE + "?state=all&sort=created&direction=desc&per_page=100"
MARKER = "<!-- ghp:create:"


@pytest.fixture(autouse=True)
def _no_real_waiting(monkeypatch):
    monkeypatch.setattr(issues, "_nap", lambda _s: None)  # the shipped re-read waits are asserted through an injected sleep


def fx(name):
    return json.loads((FIX / (name + ".json")).read_text(encoding="utf-8"))


def raw(n, **over):
    """A REST issue (open, no labels) numbered n, from the committed task fixture."""
    d = copy.deepcopy(fx("issue_task"))
    d.update(number=n, id=9100000000 + n, node_id=f"I_kwDOFictional{n:04d}", title=f"Task {n}", state="open",
             state_reason=None, parent_issue_url=None, url=f"https://api.github.com/repos/octo/demo/issues/{n}",
             html_url=f"https://github.com/octo/demo/issues/{n}")
    d.update(over)
    return d


def issue(n, **over):
    return issues._issue(raw(n, **over), Ref("octo", "demo", n))


def labelled(names):
    return [{"id": 1, "name": n} for n in names]


class Repo:
    """A tiny GitHub: POST creates issue 7 from what was sent (minus whatever `drop` removes), GET serves it back."""

    def __init__(self, drop=(), milestone=None, parent_url=None):
        self.drop, self.ms, self.parent_url, self.posts, self.sent = set(drop), milestone, parent_url, 0, None
        self.gh = FakeGh()
        self.gh.on_rest("POST", BASE, self.post)
        self.gh.on_rest("GET", BASE + "/7", lambda _b: self.stored)
        self.gh.on_rest("GET", "repos/octo/demo/milestones/3", {"number": 3, "title": "Spike M1"})
        self.gh.arm()

    def post(self, body):
        self.posts += 1
        self.sent = body
        names = [n for n in body["labels"] if n not in self.drop]
        self.stored = raw(7, title=body["title"], body=body["body"], labels=labelled(names),
                          milestone=self.ms if "milestone" in body else None, parent_issue_url=self.parent_url)
        return self.stored


# ---- create: the request ----
def test_create_posts_title_body_labels_and_the_parent_id_then_reads_back():
    parent = issue(1)
    r = Repo(parent_url="https://api.github.com/repos/octo/demo/issues/1")
    got = issues.create(r.gh, CFG, "Render the hours", "Some body", ["ghp:task", "ghp:ui"], parent)
    assert isinstance(got, issues.Issue) and got.ref == Ref("octo", "demo", 7) and got.labels == ["ghp:task", "ghp:ui"]
    sent = r.sent
    assert sent["title"] == "Render the hours" and sent["labels"] == ["ghp:task", "ghp:ui"]
    assert sent["parent_issue_id"] == parent.id and "milestone" not in sent
    assert sent["body"].startswith("Some body\n\n" + MARKER) and sent["body"].endswith(" -->")
    assert [c[1:3] for c in r.gh.calls] == [("POST", BASE), ("GET", BASE + "/7")]  # one write, then the read-back


def test_create_without_a_body_sends_only_the_marker_and_without_a_parent_no_parent_key():
    r = Repo()
    issues.create(r.gh, CFG, "T", "", [], None)
    assert r.sent["body"].startswith(MARKER) and "parent_issue_id" not in r.sent and r.sent["labels"] == []


def test_create_detects_a_silently_dropped_label():
    r = Repo(drop=["ghp:ui"])
    with pytest.raises(issues.IssueError) as e:
        issues.create(r.gh, CFG, "T", "b", ["ghp:task", "ghp:ui"], None)
    assert e.value.code == "E_LABEL_DROPPED" and str(e.value).startswith("E_LABEL_DROPPED")
    assert "ghp:ui" in str(e.value) and isinstance(e.value, ValueError)
    assert e.value.issue.ref.number == 7  # the issue exists: the caller must not create it again
    assert r.posts == 1


def test_create_compares_labels_exactly_an_extra_or_recased_label_is_not_what_was_asked():
    r = Repo()
    r.gh.on_rest("GET", BASE + "/7", lambda _b: dict(r.stored, labels=labelled(["Ghp:Task"])))
    with pytest.raises(issues.IssueError) as e:
        issues.create(r.gh, CFG, "T", "b", ["ghp:task"], None)
    assert e.value.code == "E_LABEL_DROPPED"


def test_create_sets_and_checks_the_milestone():
    r = Repo(milestone={"number": 3, "title": "Spike M1"})
    got = issues.create(r.gh, CFG, "T", "b", [], None, milestone=3)
    assert r.sent["milestone"] == 3 and got.milestone == "Spike M1"
    r = Repo(milestone=None)  # GitHub stores it without the milestone (silently dropped) although it was asked for
    with pytest.raises(issues.IssueError) as e:
        issues.create(r.gh, CFG, "T", "b", [], None, milestone=3)
    assert e.value.code == "E_MILESTONE_DROPPED" and e.value.issue.ref.number == 7


def test_create_checks_that_the_parent_was_attached():
    r = Repo(parent_url=None)
    with pytest.raises(issues.IssueError) as e:
        issues.create(r.gh, CFG, "T", "b", [], issue(1))
    assert e.value.code == "E_PARENT_NOT_ATTACHED"


def test_create_reads_back_the_same_issue_it_made():
    r = Repo()
    r.gh.on_rest("GET", BASE + "/7", lambda _b: dict(r.stored, id=1))
    with pytest.raises(GhError) as e:
        issues.create(r.gh, CFG, "T", "b", [], None)
    assert e.value.code == "E_GH_BAD_BODY"


def test_a_failed_read_back_still_names_the_issue_that_was_created():
    r = Repo()

    def boom(_b):
        raise GhError("E_GH_HTTP", 502, "bad gateway")
    r.gh.on_rest("GET", BASE + "/7", boom)
    with pytest.raises(GhError) as e:
        issues.create(r.gh, CFG, "T", "b", [], None)
    assert e.value.code == "E_GH_HTTP" and "#7" in str(e.value) and "created" in str(e.value)


@pytest.mark.parametrize("args", [
    ("", "b", [], None), ("  ", "b", [], None), ("a\nb", "b", [], None), ("a\x1b[31m", "b", [], None), ("x" * 257, "b", [], None),
    (None, "b", [], None), ("T", None, [], None), ("T", "x" * 59901, [], None),
    ("T", "b", "ghp:task", None), ("T", "b", [""], None), ("T", "b", [" a"], None), ("T", "b", ["a", "a"], None),
    ("T", "b", [5], None), ("T", "b", ["a\nb"], None), ("T", "b", ["x" * 51], None), ("T", "b", [None], None),
    ("T", "b", [], "not an issue"),
])
def test_create_refuses_bad_input_before_any_call(args):
    gh = FakeGh()
    gh.arm()
    with pytest.raises(ValueError):
        issues.create(gh, CFG, *args)
    assert gh.calls == []


@pytest.mark.parametrize("ms", [0, -1, True, "3", 1.5])
def test_create_refuses_a_milestone_that_is_not_a_positive_integer(ms):
    gh = FakeGh()
    gh.arm()
    with pytest.raises(ValueError):
        issues.create(gh, CFG, "T", "b", [], None, milestone=ms)
    assert gh.calls == []


def test_create_refuses_a_parent_from_another_repo():
    other = issues._issue(raw(1), Ref("octo", "elsewhere", 1))
    gh = FakeGh()
    gh.arm()
    with pytest.raises(ValueError, match="E_FOREIGN_REPO"):
        issues.create(gh, CFG, "T", "b", [], other)
    assert gh.calls == []


def test_a_title_with_markup_or_json_is_data_in_the_body_never_in_the_path():
    r = Repo()
    nasty = 'x"} ../../graphql <script> ${HOME} %2e'
    issues.create(r.gh, CFG, nasty, "b", [nasty[:20]], None)
    assert r.sent["title"] == nasty
    assert all(c[2].startswith(BASE) and nasty not in c[2] for c in r.gh.calls)


# ---- create: an unknown outcome is re-read, never blindly retried ----
def timeout_then(gh, listing):
    """POST times out after the issue was stored; the newest-issues list returns `listing(marker)`."""
    seen = {}

    def post(body):
        seen["body"] = body
        raise GhTimeout(write=True)
    gh.on_rest("POST", BASE, post)
    gh.on_rest("GET", LIST, lambda _b: listing(seen["body"]["body"]))
    return seen


def test_create_after_a_timeout_rereads_by_marker_and_never_posts_twice():
    gh = FakeGh()
    gh.arm()
    stored = raw(7, labels=labelled(["ghp:task"]))
    timeout_then(gh, lambda body: [raw(9, body="unrelated"), dict(stored, body=body)])
    gh.on_rest("GET", BASE + "/7", lambda _b: stored)
    got = issues.create(gh, CFG, "Task 7", "b", ["ghp:task"], None)
    assert got.ref.number == 7
    assert [c[1] for c in gh.calls].count("POST") == 1  # one POST only
    assert [c[1:3] for c in gh.calls] == [("POST", BASE), ("GET", LIST), ("GET", BASE + "/7")]


def test_create_after_a_timeout_with_nothing_found_raises_the_original_error_and_does_not_post_again():
    gh = FakeGh()
    gh.arm()
    timeout_then(gh, lambda body: [raw(9, body="unrelated"), raw(10, body=None), dict(raw(11), pull_request={})])
    with pytest.raises(GhError) as e:
        issues.create(gh, CFG, "T", "b", [], None)
    assert e.value.code == "E_GH_TIMEOUT" and [c[1] for c in gh.calls].count("POST") == 1


def test_a_pull_request_carrying_the_marker_is_not_adopted():
    gh = FakeGh()
    gh.arm()
    timeout_then(gh, lambda body: [dict(raw(11, body=body), pull_request={})])
    with pytest.raises(GhError) as e:
        issues.create(gh, CFG, "T", "b", [], None)
    assert e.value.code == "E_GH_TIMEOUT"


def test_a_reply_of_the_wrong_shape_is_an_unknown_outcome_and_is_reread():
    gh = FakeGh()
    gh.arm()
    gh.on_rest("POST", BASE, {"unexpected": True})
    gh.on_rest("GET", LIST, [])
    with pytest.raises(GhError) as e:
        issues.create(gh, CFG, "T", "b", [], None)
    assert e.value.code == "E_GH_BAD_BODY" and "unknown whether applied" in str(e.value)
    assert [c[1] for c in gh.calls].count("POST") == 1


def test_a_rejected_create_is_not_reread():
    gh = FakeGh()
    gh.arm()

    def rejected(_b):
        raise GhError("E_GH_HTTP", 422, "you can't add more than 7 layers of sub-issues")
    gh.on_rest("POST", BASE, rejected)
    with pytest.raises(GhError) as e:
        issues.create(gh, CFG, "T", "b", [], None)
    assert e.value.status == 422 and len(gh.calls) == 1


def test_a_server_error_on_create_is_reread():
    gh = FakeGh()
    gh.arm()

    def down(_b):
        raise GhError("E_GH_HTTP", 502, "bad gateway")
    gh.on_rest("POST", BASE, down)
    gh.on_rest("GET", LIST, [])
    with pytest.raises(GhError) as e:
        issues.create(gh, CFG, "T", "b", [], None)
    assert e.value.status == 502 and [c[1:3] for c in gh.calls] == [("POST", BASE)] + [("GET", LIST)] * 6  # the lagging list is re-read, the POST never


def lagging(gh, hidden_reads):
    """POST times out after the issue was stored; the newest-issues list shows it only from read number `hidden_reads` + 1 (GitHub's list lags)."""
    seen, reads = {}, [0]

    def post(body):
        seen["body"] = body
        raise GhTimeout(write=True)

    def listing(_b):
        reads[0] += 1
        stored = dict(raw(7, labels=labelled([])), body=seen["body"]["body"])
        return [raw(9, body="unrelated")] + ([] if reads[0] <= hidden_reads else [stored])
    gh.on_rest("POST", BASE, post)
    gh.on_rest("GET", LIST, listing)
    gh.on_rest("GET", BASE + "/7", lambda _b: dict(raw(7, labels=labelled([])), body=seen["body"]["body"]))
    return reads


def test_a_lagging_list_is_waited_for_and_the_issue_found_without_a_second_post(clock):
    gh = FakeGh()
    gh.arm()
    reads = lagging(gh, 3)  # absent at reads 1-3, listed at the 4th
    got = issues.create(gh, CFG, "T", "b", [], None, sleep=clock.sleep)
    assert got.ref.number == 7 and reads[0] == 4 and [c[1] for c in gh.calls].count("POST") == 1
    assert len(clock.sleeps) == 3 and all(s > 0 for s in clock.sleeps)  # a wait before every re-read, none before the first


def test_the_reread_budget_is_six_reads_over_about_ten_seconds_then_the_original_error(clock):
    gh = FakeGh()
    gh.arm()
    reads = lagging(gh, 99)  # never listed
    with pytest.raises(GhError) as e:
        issues.create(gh, CFG, "T", "b", [], None, sleep=clock.sleep)
    assert e.value.code == "E_GH_TIMEOUT" and issues.maybe_applied(e.value) and "unknown whether applied" in str(e.value)
    assert reads[0] == 6 and len(clock.sleeps) == 5 and 8 <= sum(clock.sleeps) <= 12
    assert [c[1] for c in gh.calls].count("POST") == 1 and clock.sleeps == sorted(clock.sleeps)  # backoff: waits never shrink


def test_a_reread_that_fails_in_the_middle_of_the_waiting_keeps_the_original_error(clock):
    gh = FakeGh()
    gh.arm()
    reads = lagging(gh, 99)
    ok, calls = gh._rest[("GET", LIST)], [0]

    def second_read_dies(b):
        calls[0] += 1
        if calls[0] == 1:
            return ok(b)
        raise GhTimeout()
    gh.on_rest("GET", LIST, second_read_dies)
    with pytest.raises(GhError) as e:
        issues.create(gh, CFG, "T", "b", [], None, sleep=clock.sleep)
    assert e.value.code == "E_GH_TIMEOUT" and "unknown whether applied" in str(e.value) and isinstance(e.value.__cause__, GhError)
    assert len(clock.sleeps) == 1  # it stopped at the failed read, no more waiting, no second POST
    assert [c[1] for c in gh.calls].count("POST") == 1


def test_a_found_issue_is_not_waited_for(clock):
    gh = FakeGh()
    gh.arm()
    lagging(gh, 0)
    issues.create(gh, CFG, "T", "b", [], None, sleep=clock.sleep)
    assert clock.sleeps == []


# ---- add_sub_issue ----
def test_add_sub_issue_sends_the_id_not_the_number():
    parent, child = issue(1), issue(2)
    assert child.id != child.ref.number
    gh = FakeGh().on_rest("POST", BASE + "/1/sub_issues", {"id": parent.id})
    gh.arm()
    issues.add_sub_issue(gh, parent, child)
    assert gh.calls == [("rest", "POST", BASE + "/1/sub_issues", {"sub_issue_id": child.id})]
    assert type(gh.calls[0][3]["sub_issue_id"]) is int and gh.calls[0][3]["sub_issue_id"] != child.ref.number


@pytest.mark.parametrize("bad", [True, 0, -4, "9100000002", 1.0, None])
def test_add_sub_issue_refuses_an_id_that_is_not_an_exact_positive_int(bad):
    parent, child = issue(1), issue(2)
    gh = FakeGh()
    gh.arm()
    for p, c in ((parent, issues.Issue(**dict(vars(child), id=bad))), (issues.Issue(**dict(vars(parent), id=bad)), child)):
        with pytest.raises(ValueError):
            issues.add_sub_issue(gh, p, c)
    assert gh.calls == []


def test_add_sub_issue_refuses_itself_another_owner_and_non_issues():
    gh = FakeGh()
    gh.arm()
    foreign = issues._issue(raw(2), Ref("someone-else", "demo", 2))
    for p, c in ((issue(1), issue(1)), (issue(1), foreign), (issue(1), "2"), (None, issue(2))):
        with pytest.raises(ValueError):
            issues.add_sub_issue(gh, p, c)
    assert gh.calls == []


def test_add_sub_issue_after_an_unknown_outcome_rereads_the_child():
    parent, child = issue(1), issue(2)
    gh = FakeGh().on_rest("POST", BASE + "/1/sub_issues", lambda _b: (_ for _ in ()).throw(GhTimeout(write=True)))
    gh.on_rest("GET", BASE + "/2", raw(2, parent_issue_url="https://api.github.com/repos/octo/demo/issues/1"))
    gh.arm()
    issues.add_sub_issue(gh, parent, child)  # it was applied: no error, no second POST
    assert [c[1] for c in gh.calls] == ["POST", "GET"]
    gh = FakeGh().on_rest("POST", BASE + "/1/sub_issues", lambda _b: (_ for _ in ()).throw(GhTimeout(write=True)))
    gh.on_rest("GET", BASE + "/2", raw(2))
    gh.arm()
    with pytest.raises(GhError) as e:
        issues.add_sub_issue(gh, parent, child)
    assert e.value.code == "E_GH_TIMEOUT" and [c[1] for c in gh.calls] == ["POST", "GET"]


def test_a_rejected_sub_issue_call_is_not_reread():
    gh = FakeGh().on_rest("POST", BASE + "/1/sub_issues", lambda _b: (_ for _ in ()).throw(GhError("E_GH_HTTP", 404, "Not Found")))
    gh.arm()
    with pytest.raises(GhError):
        issues.add_sub_issue(gh, issue(1), issue(2))
    assert len(gh.calls) == 1


CHILDREN = BASE + "/1/sub_issues?per_page=100&page=1"


def refused_422(_b):
    raise GhError("E_GH_HTTP", 422, "Issue may not contain duplicate sub-issues and Sub issue may only have one parent")


def child_of_1(n):
    return raw(n, parent_issue_url="https://api.github.com/repos/octo/demo/issues/1")


def test_a_repeated_sub_issue_link_is_a_success_when_the_child_is_already_under_this_parent():
    parent, child = issue(1), issue(2)
    gh = FakeGh().on_rest("POST", BASE + "/1/sub_issues", refused_422).on_rest("GET", CHILDREN, [child_of_1(3), child_of_1(2)])
    gh.arm()
    assert issues.add_sub_issue(gh, parent, child) is True  # noted: it was already linked
    assert [c[1:3] for c in gh.calls] == [("POST", BASE + "/1/sub_issues"), ("GET", CHILDREN)]  # one POST, never repeated
    ok = FakeGh().on_rest("POST", BASE + "/1/sub_issues", {"id": parent.id})
    ok.arm()
    assert issues.add_sub_issue(ok, parent, child) is False  # a fresh link is not "already"


@pytest.mark.parametrize("listed", [[], [child_of_1(3)]])
def test_a_422_for_another_reason_still_raises_the_original_error(listed):
    gh = FakeGh().on_rest("POST", BASE + "/1/sub_issues", refused_422).on_rest("GET", CHILDREN, listed)  # not under this parent
    gh.arm()
    with pytest.raises(GhError) as e:
        issues.add_sub_issue(gh, issue(1), issue(2))
    assert e.value.status == 422 and "duplicate sub-issues" in str(e.value) and [c[1] for c in gh.calls] == ["POST", "GET"]


def test_the_same_number_with_another_id_is_not_the_child():
    gh = FakeGh().on_rest("POST", BASE + "/1/sub_issues", refused_422)
    gh.on_rest("GET", CHILDREN, [dict(child_of_1(2), id=5)])
    gh.arm()
    with pytest.raises(GhError) as e:
        issues.add_sub_issue(gh, issue(1), issue(2))
    assert e.value.status == 422


@pytest.mark.parametrize("answer", [GhTimeout(), "not a list", [dict(child_of_1(2), pull_request={})], [{"number": 2}]])
def test_a_422_whose_children_read_fails_keeps_the_original_error(answer):
    def children(_b):
        if isinstance(answer, BaseException):
            raise answer
        return answer
    gh = FakeGh().on_rest("POST", BASE + "/1/sub_issues", refused_422).on_rest("GET", CHILDREN, children)
    gh.arm()
    with pytest.raises(GhError) as e:
        issues.add_sub_issue(gh, issue(1), issue(2))
    assert e.value.status == 422 and "duplicate sub-issues" in str(e.value) and e.value.__cause__ is not None
    assert [c[1] for c in gh.calls] == ["POST", "GET"]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 429])
def test_only_a_422_reads_the_children(status):
    gh = FakeGh().on_rest("POST", BASE + "/1/sub_issues", lambda _b: (_ for _ in ()).throw(GhError("E_GH_HTTP", status, "no")))
    gh.arm()
    with pytest.raises(GhError) as e:
        issues.add_sub_issue(gh, issue(1), issue(2))
    assert e.value.status == status and len(gh.calls) == 1


# ---- close ----
def closed(n, reason="completed"):
    return raw(n, state="closed", state_reason=reason)


def test_close_patches_state_and_reason_and_returns_the_closed_issue():
    gh = FakeGh().on_rest("GET", BASE + "/2", raw(2)).on_rest("PATCH", BASE + "/2", closed(2))
    gh.arm()
    got = issues.close(gh, issue(2), "completed")
    assert got.state == "closed" and got.state_reason == "completed"
    assert [c[1:3] for c in gh.calls] == [("GET", BASE + "/2"), ("PATCH", BASE + "/2")]  # read first, then one write
    assert gh.calls[1][3] == {"state": "closed", "state_reason": "completed"}


def test_close_with_not_planned():
    gh = FakeGh().on_rest("GET", BASE + "/2", raw(2)).on_rest("PATCH", BASE + "/2", closed(2, "not_planned"))
    gh.arm()
    assert issues.close(gh, issue(2), "not_planned").state_reason == "not_planned"


@pytest.mark.parametrize("reason", ["duplicate", "Completed", "", " completed", None, 1, "completed\n"])
def test_close_refuses_a_reason_outside_the_whitelist_before_any_call(reason):
    gh = FakeGh()
    gh.arm()
    with pytest.raises(issues.IssueError) as e:
        issues.close(gh, issue(2), reason)
    assert e.value.code == "E_CLOSE_REASON" and gh.calls == []


def test_close_is_idempotent_on_an_already_closed_issue():
    gh = FakeGh().on_rest("GET", BASE + "/2", closed(2, "not_planned"))  # no PATCH registered: one would fail loudly
    gh.arm()
    got = issues.close(gh, issue(2), "completed")
    assert got.state == "closed" and got.state_reason == "not_planned"  # left as it was
    assert [c[1] for c in gh.calls] == ["GET"]


def test_close_decides_on_a_fresh_read_not_on_the_callers_snapshot():
    stale_open = issue(2)
    gh = FakeGh().on_rest("GET", BASE + "/2", closed(2))
    gh.arm()
    issues.close(gh, stale_open, "completed")
    assert [c[1] for c in gh.calls] == ["GET"]


def test_close_raises_when_the_issue_did_not_end_up_closed_with_that_reason():
    for answer in (raw(2), closed(2, "not_planned")):
        gh = FakeGh().on_rest("GET", BASE + "/2", raw(2)).on_rest("PATCH", BASE + "/2", answer)
        gh.arm()
        with pytest.raises(issues.IssueError) as e:
            issues.close(gh, issue(2), "completed")
        assert e.value.code == "E_CLOSE_NOT_APPLIED"


def test_close_after_an_unknown_outcome_rereads():
    state = {"n": 0}

    def get(_b):
        state["n"] += 1
        return raw(2) if state["n"] == 1 else closed(2)
    gh = FakeGh().on_rest("GET", BASE + "/2", get).on_rest("PATCH", BASE + "/2", lambda _b: (_ for _ in ()).throw(GhTimeout(write=True)))
    gh.arm()
    assert issues.close(gh, issue(2), "completed").state == "closed"
    assert [c[1] for c in gh.calls] == ["GET", "PATCH", "GET"]
    state["n"] = 0
    gh = FakeGh().on_rest("GET", BASE + "/2", raw(2)).on_rest("PATCH", BASE + "/2", lambda _b: (_ for _ in ()).throw(GhTimeout(write=True)))
    gh.arm()
    with pytest.raises(GhError) as e:
        issues.close(gh, issue(2), "completed")
    assert e.value.code == "E_GH_TIMEOUT"


def test_close_refuses_a_pull_request():
    gh = FakeGh().on_rest("GET", BASE + "/2", dict(raw(2), pull_request={}))
    gh.arm()
    with pytest.raises(issues.NotAnIssue):
        issues.close(gh, issue(2), "completed")
    assert [c[1] for c in gh.calls] == ["GET"]


# ---- the one place that says whether a failed write may have been applied ----
@pytest.mark.parametrize("err,maybe", [
    (GhTimeout(write=True), True), (GhError("E_GH_NO_RESPONSE", None, "unknown whether applied; re-read before retrying: x"), True),
    (GhError("E_GH_HTTP", 502, "bad gateway"), True), (GhError("E_GH_BAD_BODY", 200, "the response was not a JSON object"), True),
    (GhError("E_GH_HTTP", 422, "Validation Failed"), False), (GhError("E_GH_HTTP", 404, "Not Found"), False),
    (GhError("E_GH_GRAPHQL", 200, "Could not resolve"), False), (GhError("E_GH_MISSING", None, "gh not found"), False),
])
def test_maybe_applied(err, maybe):
    assert issues.maybe_applied(err) is maybe


# ---- round 1 of the review ----
def _boom(err):
    def raiser(_b):
        raise err
    return raiser


def test_a_failed_reread_keeps_the_original_write_error_create():
    gh = FakeGh().on_rest("POST", BASE, _boom(GhTimeout(write=True))).on_rest("GET", LIST, _boom(GhTimeout(write=False)))
    gh.arm()
    with pytest.raises(GhError) as e:
        issues.create(gh, CFG, "T", "b", [], None)
    assert e.value.code == "E_GH_TIMEOUT" and issues.maybe_applied(e.value) and "unknown whether applied" in str(e.value)
    assert isinstance(e.value.__cause__, GhError) and not issues.maybe_applied(e.value.__cause__)  # chained from the read error
    assert [c[1] for c in gh.calls] == ["POST", "GET"]


def test_a_failed_reread_keeps_the_original_write_error_add_sub_issue_and_close():
    gh = FakeGh().on_rest("POST", BASE + "/1/sub_issues", _boom(GhTimeout(write=True))).on_rest("GET", BASE + "/2", _boom(GhTimeout(write=False)))
    gh.arm()
    with pytest.raises(GhError) as e:
        issues.add_sub_issue(gh, issue(1), issue(2))
    assert issues.maybe_applied(e.value) and "unknown whether applied" in str(e.value)
    reads = iter([raw(2)])
    gh = FakeGh().on_rest("GET", BASE + "/2", lambda _b: next(reads, None) or _boom(GhTimeout(write=False))(None))
    gh.on_rest("PATCH", BASE + "/2", _boom(GhTimeout(write=True)))
    gh.arm()
    with pytest.raises(GhError) as e:
        issues.close(gh, issue(2), "completed")
    assert issues.maybe_applied(e.value) and "unknown whether applied" in str(e.value)
    assert [c[1] for c in gh.calls] == ["GET", "PATCH", "GET"]


def test_add_sub_issue_refuses_a_child_or_parent_of_another_repo_of_the_same_owner():
    other = issues._issue(raw(2), Ref("octo", "other", 2))
    gh = FakeGh()
    gh.arm()
    for p, c in ((issue(1), other), (other, issue(1))):
        with pytest.raises(issues.IssueError) as e:
            issues.add_sub_issue(gh, p, c)
        assert e.value.code == "E_FOREIGN_REPO"
    assert gh.calls == []
    same_but_cased = issues._issue(raw(2), Ref("OCTO", "Demo", 2))
    gh.on_rest("POST", BASE + "/1/sub_issues", {})
    issues.add_sub_issue(gh, issue(1), same_but_cased)  # repo names compare case-insensitively
    assert len(gh.calls) == 1


# the label compare is set equality WITH multiplicity: the same labels, each as often as asked, in any order
@pytest.mark.parametrize("asked,stored", [(["a"], ["a", "extra"]), (["a"], ["a", "a"]), (["a", "b"], ["a"]), ([], ["x"]), (["a"], [])])
def test_create_fails_the_compare_on_extra_duplicate_or_missing_stored_labels(asked, stored):
    r = Repo()
    r.gh.on_rest("GET", BASE + "/7", lambda _b: dict(r.stored, labels=labelled(stored)))
    with pytest.raises(issues.IssueError) as e:
        issues.create(r.gh, CFG, "T", "b", asked, None)
    assert e.value.code == "E_LABEL_DROPPED"


def test_create_accepts_the_same_labels_in_another_order():
    r = Repo()
    r.gh.on_rest("GET", BASE + "/7", lambda _b: dict(r.stored, labels=labelled(["b", "a"])))
    assert sorted(issues.create(r.gh, CFG, "T", "b", ["a", "b"], None).labels) == ["a", "b"]


def test_every_create_has_its_own_marker_and_an_older_runs_marker_is_not_adopted():
    r = Repo()
    issues.create(r.gh, CFG, "T", "b", [], None)
    first = r.sent["body"]
    issues.create(r.gh, CFG, "T", "b", [], None)
    assert r.sent["body"] != first and first.split("\n\n")[-1].startswith(MARKER)
    gh = FakeGh()
    gh.arm()
    timeout_then(gh, lambda body: [raw(5, body=first)])  # an older issue carrying the previous run's marker
    with pytest.raises(GhError) as e:
        issues.create(gh, CFG, "T", "b", [], None)
    assert e.value.code == "E_GH_TIMEOUT"


def test_two_issues_carrying_the_marker_are_refused_not_adopted():
    gh = FakeGh()
    gh.arm()
    timeout_then(gh, lambda body: [raw(9, body=body), raw(8, body="copy: " + body)])
    with pytest.raises(issues.IssueError) as e:
        issues.create(gh, CFG, "T", "b", [], None)
    assert e.value.code == "E_MARKER_AMBIGUOUS" and "#9" in str(e.value) and "#8" in str(e.value)
    assert [c[1] for c in gh.calls].count("POST") == 1


@pytest.mark.parametrize("labels", [[["x"]], [None], [{}], [{"a": 1}], [b"x"], None, "ab", 5])
def test_an_unhashable_or_non_text_label_is_a_value_error(labels):
    gh = FakeGh()
    gh.arm()
    with pytest.raises(ValueError):
        issues.create(gh, CFG, "T", "b", labels, None)
    assert gh.calls == []


# ---- Gh.armed: a read-only property ----
def test_gh_armed_is_a_read_only_property_true_only_after_arm():
    from ghp.gh import Gh
    g = Gh(runner=lambda *a: None)
    assert g.armed is False and not callable(g.armed)
    with pytest.raises(AttributeError):
        g.armed = True
    g.arm()
    assert g.armed is True and isinstance(Gh.armed, property)
    f = FakeGh()
    assert f.armed is False and isinstance(FakeGh.armed, property)
    f.arm()
    assert f.armed is True


def test_close_refuses_anything_that_is_not_a_real_true_armed_flag():
    class Plain:
        calls = []

    class Method:
        def armed(self):  # a method is truthy: it must not count as armed
            return True

    class Truthy:
        armed = "yes"

    for gh in (Plain(), Method(), Truthy()):
        with pytest.raises(issues.WriteNotArmed):
            issues.close(gh, issue(2), "completed")


# ---- round 2 of the review ----
def test_a_pull_request_shaped_reread_keeps_the_original_write_error():
    pr = dict(raw(2), pull_request={})
    gh = FakeGh().on_rest("POST", BASE + "/1/sub_issues", _boom(GhTimeout(write=True))).on_rest("GET", BASE + "/2", pr)
    gh.arm()
    with pytest.raises(GhError) as e:
        issues.add_sub_issue(gh, issue(1), issue(2))
    assert e.value.code == "E_GH_TIMEOUT" and issues.maybe_applied(e.value) and isinstance(e.value.__cause__, issues.NotAnIssue)
    reads = iter([raw(2)])
    gh = FakeGh().on_rest("GET", BASE + "/2", lambda _b: next(reads, pr))
    gh.on_rest("PATCH", BASE + "/2", _boom(GhTimeout(write=True)))
    gh.arm()
    with pytest.raises(GhError) as e:
        issues.close(gh, issue(2), "completed")
    assert e.value.code == "E_GH_TIMEOUT" and issues.maybe_applied(e.value)


def test_the_marker_ambiguity_is_not_swallowed_by_the_reread_guard():
    gh = FakeGh()
    gh.arm()
    timeout_then(gh, lambda body: [raw(9, body=body), raw(8, body=body)])
    with pytest.raises(issues.IssueError):
        issues.create(gh, CFG, "T", "b", [], None)


def test_the_kelvin_sign_is_not_a_k():
    kube = "\u212aube"  # KELVIN SIGN
    assert kube.lower() == "kube"  # the trap
    a, b = Ref("octo", kube, 1), Ref("octo", "kube", 1)
    assert not issues._same_repo(a, b) and not issues._same(a, b)
    assert issues._same_repo(Ref("OCTO", "KUBE", 1), Ref("octo", "kube", 2))
    gh = FakeGh().on_rest("POST", "repos/octo/kube/issues/1/sub_issues", {})
    gh.arm()
    p, c = issues._issue(raw(1), Ref("octo", "kube", 1)), issues._issue(raw(2), Ref("octo", kube, 2))
    with pytest.raises(issues.IssueError) as e:
        issues.add_sub_issue(gh, p, c)
    assert e.value.code == "E_FOREIGN_REPO" and gh.calls == []
    with pytest.raises(issues.IssueError):
        issues.create(gh, Config("octo", "kube"), "T", "b", [], c)


@pytest.mark.parametrize("url", ["https://api.github.com/repos/octo/\u212aube/issues/1", "https://api.github.com/repos/octo/de mo/issues/1",
                                 "https://api.github.com/repos/ocтo/demo/issues/1", "https://api.github.com/repos/octo/../issues/1"])
def test_a_server_url_with_a_non_ascii_or_odd_name_is_a_bad_body(url):
    with pytest.raises(GhError) as e:
        issues._issue(raw(2, parent_issue_url=url), Ref("octo", "demo", 2))
    assert e.value.code == "E_GH_BAD_BODY"
    with pytest.raises(GhError):
        issues._issue(raw(2, repository_url=url.rsplit("/issues", 1)[0]), None)


def test_the_foreign_repo_messages_are_escaped():
    odd = issues._issue(raw(1), Ref("octo", "el\x1b[31mse", 1))
    gh = FakeGh()
    gh.arm()
    with pytest.raises(issues.IssueError) as e:
        issues.create(gh, CFG, "T", "b", [], odd)
    assert "\x1b" not in str(e.value) and "\\u001b" in str(e.value)
    with pytest.raises(issues.IssueError) as e:
        issues.add_sub_issue(gh, issue(2), odd)
    assert "\x1b" not in str(e.value) and "\\u001b" in str(e.value)
    with pytest.raises(issues.IssueError) as e:
        issues.create(gh, Config("octo", "demo"), "T", "b", [], odd)
    assert gh.calls == []


class _EqStr(str):
    def __eq__(self, other):
        return True

    __hash__ = str.__hash__


def test_close_takes_only_an_exact_str_reason():
    gh = FakeGh()
    gh.arm()
    with pytest.raises(issues.IssueError) as e:
        issues.close(gh, issue(2), _EqStr("whatever"))
    assert e.value.code == "E_CLOSE_REASON" and gh.calls == []
