import copy
import json
from pathlib import Path

import pytest

import ghp.__main__ as cli
from conftest import FakeGh
from ghp import issues
from ghp.gh import GhError
from ghp.refs import Ref

FIX = Path(__file__).parent / "fixtures" / "gh"
R1, R2, R7 = (Ref("octo", "demo", n) for n in (1, 2, 7))


def fx(name):
    return json.loads((FIX / (name + ".json")).read_text(encoding="utf-8"))


def path(n):
    return f"repos/octo/demo/issues/{n}"


def sub_path(n, page):
    return f"{path(n)}/sub_issues?per_page=100&page={page}"


def item(n, **over):
    d = copy.deepcopy(fx("issue_task"))
    d.update(number=n, id=9100000000 + n, node_id=f"I_kwDOFictional{n:04d}", title=f"Task {n}",
             url=f"https://api.github.com/repos/octo/demo/issues/{n}")
    d.update(over)
    return d


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """A cwd whose ghp.config.json names octo/demo, the one repo `ghp get` reads (D7)."""
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)


def test_get_maps_fields_and_keeps_body_byte_exact():
    raw = fx("issue_story")
    gh = FakeGh().on_rest("GET", path(1), raw)
    got = issues.get(gh, R1)
    assert got == issues.Issue(
        ref=R1, node_id="I_kwDOFictional0001", id=9000000101, title="Opening hours page", body=raw["body"],
        state="open", state_reason=None, labels=["ghp:story", "area:web"], milestone="Release 1",
        author=issues.Actor(1001, "octo-author", "User"), parent=None, sub_total=3, sub_done=1,
        url="https://github.com/octo/demo/issues/1")
    assert got.body.encode("utf-8") == raw["body"].encode("utf-8")
    assert "\r\n" in got.body and got.body.endswith("\r\n\r\n\r\n") and "  \r\n" in got.body
    assert type(got.id) is int and type(got.author.id) is int
    assert gh.calls == [("rest", "GET", path(1), None)]


def test_get_maps_parent_closed_state_and_missing_summary():
    raw = fx("issue_task")
    got = issues.get(FakeGh().on_rest("GET", path(2), raw), R2)
    assert (got.parent, got.state, got.state_reason, got.labels, got.milestone) == (R1, "closed", "completed", [], None)
    del raw["sub_issues_summary"]
    got = issues.get(FakeGh().on_rest("GET", path(2), raw), R2)
    assert (got.sub_total, got.sub_done) == (0, 0)


def test_root_issue_has_no_parent_with_the_key_absent_or_null():
    raw = fx("issue_story")
    assert "parent_issue_url" not in raw  # live omits the key on a root issue (T13); the fixture matches
    assert issues.get(FakeGh().on_rest("GET", path(1), raw), R1).parent is None
    assert issues.get(FakeGh().on_rest("GET", path(1), dict(raw, parent_issue_url=None)), R1).parent is None


def test_pull_request_number_is_refused():
    gh = FakeGh().on_rest("GET", path(7), fx("issue_pull_request"))
    with pytest.raises(issues.NotAnIssue) as e:
        issues.get(gh, R7)
    assert e.value.code == "E_NOT_AN_ISSUE" and "E_NOT_AN_ISSUE" in str(e.value)


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("title"), lambda d: d.update(user=None), lambda d: d.update(user={"login": "x"}),
    lambda d: d.update(number="2"), lambda d: d.update(number=99), lambda d: d.update(id="9"),
    lambda d: d.update(labels="ghp:story"), lambda d: d.update(labels=[{"nope": 1}]),
    lambda d: d.update(milestone="Release 1"), lambda d: d.update(sub_issues_summary={"total": "x"}),
    lambda d: d.update(parent_issue_url="https://example.com/not-a-parent"), lambda d: d.update(body=5),
    lambda d: d.update(state=None),
])
def test_odd_issue_bodies_raise_a_named_error(mutate):
    raw = fx("issue_task")
    mutate(raw)
    with pytest.raises(GhError) as e:
        issues.get(FakeGh().on_rest("GET", path(2), raw), R2)
    assert e.value.code == "E_GH_BAD_BODY"


@pytest.mark.parametrize("body", [None, [], "text", 5])
def test_non_object_responses_raise_a_named_error(body):
    with pytest.raises(GhError) as e:
        issues.get(FakeGh().on_rest("GET", path(2), body), R2)
    assert e.value.code == "E_GH_BAD_BODY"


def test_children_pages_past_100():
    p1 = [item(n) for n in range(10, 110)]
    p2 = [item(n) for n in range(110, 115)]
    gh = FakeGh().on_rest("GET", sub_path(1, 1), p1).on_rest("GET", sub_path(1, 2), p2)
    got = issues.children(gh, R1)
    assert [c.ref.number for c in got] == list(range(10, 115))
    assert [c[2] for c in gh.calls] == [sub_path(1, 1), sub_path(1, 2)]  # stopped at the short page


def test_children_stop_after_one_short_page_and_an_empty_list():
    gh = FakeGh().on_rest("GET", sub_path(1, 1), [item(3)])
    assert len(issues.children(gh, R1)) == 1 and len(gh.calls) == 1
    assert issues.children(FakeGh().on_rest("GET", sub_path(1, 1), []), R1) == []


def test_children_that_never_end_are_refused():
    full = [item(n) for n in range(100)]
    gh = FakeGh()
    for page in range(1, 60):
        gh.on_rest("GET", sub_path(1, page), full)
    with pytest.raises(GhError) as e:
        issues.children(gh, R1)
    assert e.value.code == "E_GH_BAD_BODY"


def test_children_keep_id_and_number_apart():
    gh = FakeGh().on_rest("GET", sub_path(1, 1), [item(5), item(6, repository_url="https://api.github.com/repos/octo/other")])
    a, b = issues.children(gh, R1)
    assert (a.ref, a.id) == (Ref("octo", "demo", 5), 9100000005) and a.id != a.ref.number
    assert (b.ref, b.id) == (Ref("octo", "other", 6), 9100000006)
    with pytest.raises(GhError):
        issues.children(FakeGh().on_rest("GET", sub_path(1, 1), {"message": "x"}), R1)


def _chain_gh(parents):
    """parents: {number: parent number or None}; every issue lives in octo/demo."""
    gh = FakeGh()
    for n, p in parents.items():
        gh.on_rest("GET", path(n), item(n, parent_issue_url=None if p is None else f"https://api.github.com/repos/octo/demo/issues/{p}"))
    return gh


def test_parent_chain_stops_at_limit():
    gh = _chain_gh({10: 9, 9: 8, 8: 7, 7: 6, 6: None})
    got = issues.parent_chain(gh, Ref("octo", "demo", 10))
    assert [i.ref.number for i in got] == [9, 8, 7]  # nearest first, three of them
    assert [i.ref.number for i in issues.parent_chain(gh, Ref("octo", "demo", 10), limit=1)] == [9]
    assert issues.parent_chain(gh, Ref("octo", "demo", 10), limit=0) == []


def test_parent_chain_stops_at_the_root():
    gh = _chain_gh({3: 2, 2: 1, 1: None})
    assert [i.ref.number for i in issues.parent_chain(gh, Ref("octo", "demo", 3))] == [2, 1]
    assert issues.parent_chain(gh, Ref("octo", "demo", 1)) == []
    assert issues.parent_chain(_chain_gh({4: 5, 5: 4}), Ref("octo", "demo", 4)) == [  # a loop ends, it does not repeat
        issues.get(_chain_gh({4: 5, 5: 4}), Ref("octo", "demo", 5))]


def test_issue_query_selects_no_project_fields():
    """A `repo`-only token must work: no read here may mention project data (it fails as a whole without read:project)."""
    gh = _chain_gh({3: 2, 2: 1, 1: None}).on_rest("GET", sub_path(3, 1), [item(30)])
    issues.get(gh, Ref("octo", "demo", 3))
    issues.children(gh, Ref("octo", "demo", 3))
    issues.parent_chain(gh, Ref("octo", "demo", 3))
    assert gh.calls and all(c[0] == "rest" and c[1] == "GET" for c in gh.calls)  # only REST reads, no GraphQL at all
    for _, _, p, body in gh.calls:
        segments = p.split("?")[0].split("/")
        assert "projects" not in segments and "projectsV2" not in segments  # a repo named "project" is not a path segment here
        assert body is None and "?" not in p.replace("?per_page=100&page=", "")
    seen = json.dumps(gh.calls)
    assert "projectItems" not in seen and "projectV2" not in seen and "projectsV2" not in seen


def test_get_verb_prints_json_and_writes_nothing(cfg, capsys):
    raw = fx("issue_story")
    gh = FakeGh().on_rest("GET", path(1), raw)
    assert cli.main(["get", "octo/demo#1"], gh=gh) == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert (data["ref"], data["id"], data["parent"], data["author"]) == (
        "octo/demo#1", 9000000101, None, {"id": 1001, "login": "octo-author", "type": "User"})
    assert data["body"] == raw["body"] and data["sub_total"] == 3
    assert not gh.armed and all(c[0] == "rest" and c[1] == "GET" for c in gh.calls)


def test_get_verb_refuses_a_pull_request_with_exit_2(cfg, capsys):
    gh = FakeGh().on_rest("GET", path(7), fx("issue_pull_request"))
    assert cli.main(["get", "octo/demo#7"], gh=gh) == 2
    seen = capsys.readouterr()
    assert seen.out == "" and seen.err.startswith("error: E_NOT_AN_ISSUE") and seen.err.count("\n") == 1
    assert cli.main(["get", "https://github.com/octo/demo/pull/7"], gh=FakeGh()) == 2  # a PR URL never reaches GitHub
    assert "E_PULL_REQUEST_URL" in capsys.readouterr().err


def test_get_verb_resolves_a_bare_number_from_the_config(cfg, capsys):
    gh = FakeGh().on_rest("GET", path(2), fx("issue_task"))
    assert cli.main(["get", "#2"], gh=gh) == 0
    assert json.loads(capsys.readouterr().out)["parent"] == "octo/demo#1"


@pytest.mark.parametrize("text", ["octo/demo#2", "OCTO/Demo#2", "https://github.com/Octo/DEMO/issues/2"])
def test_get_verb_accepts_the_configured_repo_in_any_case(cfg, capsys, text):
    gh = FakeGh().on_rest("GET", path(2), fx("issue_task"))
    assert cli.main(["get", text], gh=gh) == 0
    assert json.loads(capsys.readouterr().out)["ref"] == "octo/demo#2"
    assert [c[2] for c in gh.calls] == [path(2)]


@pytest.mark.parametrize("text", ["someone/else#5", "https://github.com/someone/else/issues/5"])
def test_get_verb_refuses_a_foreign_repo_before_any_call(cfg, capsys, text):
    gh = FakeGh()
    assert cli.main(["get", text], gh=gh) == 2
    seen = capsys.readouterr()
    assert seen.out == "" and seen.err.startswith("error: E_FOREIGN_REPO") and seen.err.count("\n") == 1
    assert gh.calls == []


@pytest.mark.parametrize("config", [None, "{not json"])
def test_get_verb_needs_a_valid_config_even_for_a_full_ref(tmp_path, monkeypatch, capsys, config):
    if config is not None:
        (tmp_path / "ghp.config.json").write_text(config)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    gh = FakeGh()
    assert cli.main(["get", "octo/demo#1"], gh=gh) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: ") and err.count("\n") == 1 and "Traceback" not in err and gh.calls == []


@pytest.mark.parametrize("over", [{"repository_url": None}, {"repository_url": 5}, {"repository_url": "https://ghe.example/api/v3/repos/octo/demo"}])
def test_children_with_an_odd_repository_url_raise_a_named_error(over):
    gh = FakeGh().on_rest("GET", sub_path(1, 1), [item(5, **over)])
    with pytest.raises(GhError) as e:
        issues.children(gh, R1)
    assert e.value.code == "E_GH_BAD_BODY"
    raw = item(5)
    del raw["repository_url"]
    with pytest.raises(GhError):
        issues.children(FakeGh().on_rest("GET", sub_path(1, 1), [raw]), R1)
