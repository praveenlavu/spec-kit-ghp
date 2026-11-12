import copy
import json
import re
from pathlib import Path

import pytest

import ghp.__main__ as cli
from conftest import FakeGh, FakeRunner, http
from ghp import projects
from ghp.config import Config
from ghp.gh import Gh, GhError, ScopeMissing

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "fixtures" / "gh"
NODE = "I_kwDOFictional0002"
CFG = Config("octo", "demo", project_number=3)
FIELDS = ("user", "projectV2", "fields")
FLOWS = ("user", "projectV2", "workflows")
ITEMS = ("node", "projectItems")
EVENTS = ("node", "timelineItems")


def fx(name):
    return json.loads((FIX / (name + ".json")).read_text(encoding="utf-8"))


def conn(data, path):
    for k in path:
        data = data[k]
    return data


def paged(data, path):
    """A fake answer that serves the connection at `path` one node per page (first:1), chained by cursors c0, c1..."""
    full = copy.deepcopy(data)
    nodes, seen = conn(full, path)["nodes"], []

    def answer(variables):
        i = 0 if variables.get("cursor") is None else int(variables["cursor"][1:]) + 1
        seen.append(i)
        page = copy.deepcopy(full)
        c = conn(page, path)
        c["nodes"] = [nodes[i]]
        c["pageInfo"] = {"hasNextPage": i < len(nodes) - 1, "endCursor": "c%d" % i}
        return page
    answer.seen = seen
    return answer


def board_gh():
    return FakeGh().on_graphql("fields(", fx("project_fields"))


# ---- AC1, AC2 ----
def test_discover_finds_option_ids_by_name():
    gh = board_gh()
    b = projects.discover(gh, CFG)
    assert b == projects.Board("PVT_kwFictional0003", "PVTSSF_FictionalStatus",
                               {"Todo": "f75ad846", "In Progress": "47fc9ee4", "Done": "98236657"}, "Done")
    q, v = gh.calls[0][1:]
    assert v["owner"] == "octo" and v["number"] == 3 and "mutation" not in q


def test_discover_uses_the_configured_names_case_sensitively():
    cfg = Config("octo", "demo", project_number=3, status_todo="Backlog")
    with pytest.raises(projects.ProjectError) as e:
        projects.discover(board_gh(), cfg)
    assert e.value.code == "E_STATUS_OPTION_MISSING" and "'Backlog'" in str(e.value)
    cfg = Config("octo", "demo", project_number=3, status_done="DONE")  # case differs
    with pytest.raises(projects.ProjectError) as e:
        projects.discover(board_gh(), cfg)
    assert e.value.code == "E_STATUS_OPTION_MISSING"


def test_renamed_status_option_is_a_named_error():
    data = fx("project_fields")
    opts = conn(data, FIELDS)["nodes"][2]["options"]
    opts[2]["name"] = "Finished"
    with pytest.raises(projects.ProjectError) as e:
        projects.discover(FakeGh().on_graphql("fields(", data), CFG)
    msg = str(e.value)
    assert e.value.code == "E_STATUS_OPTION_MISSING" and msg.startswith("E_STATUS_OPTION_MISSING")
    assert "'Done'" in msg and "Todo" in msg and "In Progress" in msg and "Finished" in msg
    assert isinstance(e.value, ValueError)  # exit 2 in the CLI


def test_missing_status_field_is_a_named_error():
    data = fx("project_fields")
    del conn(data, FIELDS)["nodes"][2]
    with pytest.raises(projects.ProjectError) as e:
        projects.discover(FakeGh().on_graphql("fields(", data), CFG)
    assert e.value.code == "E_STATUS_FIELD_MISSING" and "Priority" in str(e.value)


def test_missing_project_and_project_number_are_named():
    gh = FakeGh().on_graphql("fields(", {"user": {"projectV2": None}})
    with pytest.raises(projects.ProjectError) as e:
        projects.discover(gh, CFG)
    assert e.value.code == "E_PROJECT_NOT_FOUND"
    gh = FakeGh().on_graphql("fields(", {"user": None})
    with pytest.raises(projects.ProjectError) as e:
        projects.discover(gh, CFG)
    assert e.value.code == "E_PROJECT_NOT_FOUND"
    gh = FakeGh()
    with pytest.raises(projects.ProjectError) as e:
        projects.discover(gh, Config("octo", "demo"))
    assert e.value.code == "E_PROJECT_NUMBER_MISSING" and gh.calls == []


def test_project_owner_is_the_user_login():
    gh = board_gh()
    projects.discover(gh, Config("octo", "demo", project_owner="octo-two", project_number=3))
    assert gh.calls[0][2]["owner"] == "octo-two"


# ---- AC3 ----
def test_connections_page_with_cursors():
    fake = paged(fx("project_fields"), FIELDS)
    assert projects.discover(FakeGh().on_graphql("fields(", fake), CFG).status_field_id == "PVTSSF_FictionalStatus"
    assert fake.seen == [0, 1, 2]
    fake = paged(fx("project_workflows"), FLOWS)
    got = projects.workflows(FakeGh().on_graphql("workflows(", fake), CFG)
    assert fake.seen == [0, 1, 2, 3, 4, 5] and [w.number for w in got] == [1, 2, 3, 4, 5, 6]
    assert got[2] == projects.Workflow("Auto-close issue", False, 3)
    fake = paged(fx("project_items"), ITEMS)
    gh = FakeGh().on_graphql("projectItems(", fake)
    assert projects.item_for(gh, projects.Board("PVT_kwFictional0003", "s", {}), NODE) == "PVTI_FictionalItem3"
    assert fake.seen == [0, 1, 2] and [c[2]["issue"] for c in gh.calls] == [NODE] * 3
    fake = paged(fx("status_events"), EVENTS)
    got = projects.status_events(FakeGh().on_graphql("timelineItems(", fake), NODE)
    assert fake.seen == [0, 1, 2] and [e.to for e in got] == ["Todo", "In Progress", "Done"]


def test_a_list_that_never_ends_stops():
    def forever(variables):
        d = fx("project_workflows")
        conn(d, FLOWS)["pageInfo"] = {"hasNextPage": True, "endCursor": "n%d" % len(gh.calls)}
        return d
    gh = FakeGh().on_graphql("workflows(", forever)
    with pytest.raises(GhError) as e:
        projects.workflows(gh, CFG)
    assert e.value.code == "E_GH_BAD_BODY" and len(gh.calls) == projects.MAX_PAGES
    same = fx("project_workflows")
    conn(same, FLOWS)["pageInfo"] = {"hasNextPage": True, "endCursor": "stuck"}
    gh = FakeGh().on_graphql("workflows(", same)  # the same cursor again: stop at once, not after MAX_PAGES
    with pytest.raises(GhError) as e:
        projects.workflows(gh, CFG)
    assert e.value.code == "E_GH_BAD_BODY" and len(gh.calls) == 2


# ---- AC4 ----
def test_item_for_unlisted_issue_is_none():
    board = projects.Board("PVT_kwFictionalElsewhere", "s", {})
    gh = FakeGh().on_graphql("projectItems(", fx("project_items"))
    assert projects.item_for(gh, board, NODE) is None
    empty = {"node": {"projectItems": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}}}
    assert projects.item_for(FakeGh().on_graphql("projectItems(", empty), board, NODE) is None
    assert projects.item_for(FakeGh().on_graphql("projectItems(", fx("project_items")),
                             projects.Board("PVT_kwFictional0003", "s", {}), NODE) == "PVTI_FictionalItem3"


# ---- AC5 ----
def test_automation_is_told_by_actor_not_by_was_automated():
    got = projects.status_events(FakeGh().on_graphql("timelineItems(", fx("status_events")), NODE)
    assert got == [
        projects.StatusEvent("github-project-automation", "", "Todo", "2026-01-01T10:00:00Z"),
        projects.StatusEvent("octo-author", "Todo", "In Progress", "2026-01-02T10:00:00Z"),
        projects.StatusEvent(None, "In Progress", "Done", "2026-01-03T10:00:00Z")]
    # the fixture's wasAutomated is false on the automation move and true on the human-less one: it must not matter
    assert [e.is_automation for e in got] == [True, False, False]
    assert "wasAutomated" not in projects.QUERIES["events"] and "was_automated" not in projects.StatusEvent._fields


# ---- AC6 ----
SCOPE_ERR = {"data": None, "errors": [{"type": "INSUFFICIENT_SCOPES", "message":
             "Your token has not been granted the required scopes to execute this query. The 'id' field requires "
             "one of the following scopes: ['read:project'], but your token has only been granted the: ['repo'] scopes."}]}
CALLS = [lambda gh: projects.discover(gh, CFG), lambda gh: projects.workflows(gh, CFG),
         lambda gh: projects.item_for(gh, projects.Board("p", "s", {}), NODE), lambda gh: projects.status_events(gh, NODE)]


@pytest.mark.parametrize("call", CALLS)
def test_missing_project_scope_is_named(call):
    err = SCOPE_ERR
    gh = Gh(runner=FakeRunner(http(200, err)), sleep=lambda s: None)
    with pytest.raises(ScopeMissing) as e:
        call(gh)
    assert "read:project" in e.value.scopes and "read:project" in str(e.value)
    nameless = {"data": None, "errors": [{"type": "INSUFFICIENT_SCOPES", "message": "no scope list here"}]}
    gh = Gh(runner=FakeRunner(http(200, nameless)), sleep=lambda s: None)
    with pytest.raises(ScopeMissing) as e:  # GitHub did not name the scope, but the query touches project fields
        call(gh)
    assert e.value.scopes == ["read:project"]


def test_a_scope_error_naming_another_scope_is_left_alone():
    def boom(v):
        raise ScopeMissing(["repo"])
    with pytest.raises(ScopeMissing) as e:
        projects.workflows(FakeGh().on_graphql("workflows(", boom), CFG)
    assert e.value.scopes == ["repo"]


# ---- malformed responses ----
BAD = [None, [], {}, {"user": []}, {"user": {"projectV2": {"id": 7}}}, {"user": {"projectV2": {"id": "p"}}},
       {"user": {"projectV2": {"id": "p", "fields": []}}},
       {"user": {"projectV2": {"id": "p", "fields": {"nodes": {}, "pageInfo": {"hasNextPage": False}}}}},
       {"user": {"projectV2": {"id": "p", "fields": {"nodes": [], "pageInfo": {"hasNextPage": "no"}}}}},
       {"user": {"projectV2": {"id": "p", "fields": {"nodes": [], "pageInfo": {"hasNextPage": True}}}}}]


@pytest.mark.parametrize("data", BAD)
def test_malformed_field_responses_are_named_errors(data):
    with pytest.raises(GhError) as e:
        projects.discover(FakeGh().on_graphql("fields(", data), CFG)
    assert e.value.code == "E_GH_BAD_BODY"


def test_malformed_option_workflow_item_and_event_nodes():
    d = fx("project_fields")
    conn(d, FIELDS)["nodes"][2]["options"] = [{"id": 5, "name": "Todo"}]
    with pytest.raises(GhError):
        projects.discover(FakeGh().on_graphql("fields(", d), CFG)
    d = fx("project_fields")
    conn(d, FIELDS)["nodes"][2]["options"] = None
    with pytest.raises(GhError):
        projects.discover(FakeGh().on_graphql("fields(", d), CFG)
    d = fx("project_workflows")
    conn(d, FLOWS)["nodes"][0]["enabled"] = "yes"
    with pytest.raises(GhError):
        projects.workflows(FakeGh().on_graphql("workflows(", d), CFG)
    d = fx("project_items")
    del conn(d, ITEMS)["nodes"][0]["project"]
    with pytest.raises(GhError):
        projects.item_for(FakeGh().on_graphql("projectItems(", d), projects.Board("p", "s", {}), NODE)
    with pytest.raises(GhError):  # the node is not an Issue (the fragment matched nothing)
        projects.item_for(FakeGh().on_graphql("projectItems(", {"node": {}}), projects.Board("p", "s", {}), NODE)
    d = fx("status_events")
    del conn(d, EVENTS)["nodes"][1]["status"]
    with pytest.raises(GhError):
        projects.status_events(FakeGh().on_graphql("timelineItems(", d), NODE)
    d = fx("status_events")
    conn(d, EVENTS)["nodes"][1]["actor"] = {"login": 5}
    with pytest.raises(GhError):
        projects.status_events(FakeGh().on_graphql("timelineItems(", d), NODE)


# ---- AC7 ----
@pytest.fixture
def cwd(tmp_path, monkeypatch):
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", "project_number": 3}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)


def test_project_show_lists_options_and_workflows(cwd, capsys):
    gh = FakeGh().on_graphql("fields(", fx("project_fields")).on_graphql("workflows(", fx("project_workflows"))
    assert cli.main(["project", "show"], gh=gh) == 0
    out = capsys.readouterr().out
    for s in ("Todo", "47fc9ee4", "In Progress", "Done", "98236657"):
        assert s in out
    lines = out.splitlines()
    for name, state in (("Item closed", "enabled"), ("Pull request merged", "enabled"), ("Auto-close issue", "disabled"),
                        ("Auto-add sub-issues to project", "enabled"), ("Pull request linked to issue", "enabled"),
                        ("Item added to project", "disabled")):
        assert any(name in l and state in l.split() for l in lines), name
    assert not gh.armed and all(c[0] == "graphql" and "mutation" not in c[1] for c in gh.calls)


def test_project_show_needs_a_subcommand_and_a_config(cwd, tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["project"], gh=FakeGh())
    assert e.value.code == 2
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo"}))
    assert cli.main(["project", "show"], gh=FakeGh()) == 2  # no project_number: refused before any call
    assert "E_PROJECT_NUMBER_MISSING" in capsys.readouterr().err


def test_project_show_names_a_renamed_option_in_one_line(cwd, capsys):
    d = fx("project_fields")
    conn(d, FIELDS)["nodes"][2]["options"][2]["name"] = "Finished"
    assert cli.main(["project", "show"], gh=FakeGh().on_graphql("fields(", d)) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: E_STATUS_OPTION_MISSING") and err.count("\n") == 1


# ---- AC8: offline, against the tracked subset of the public schema (tests/fixtures/gh/schema.subset.graphql, T17b) ----
SCHEMA_TEXT = (ROOT / "tests" / "fixtures" / "gh" / "schema.subset.graphql").read_text(encoding="utf-8")


def parse_schema():
    text = re.sub(r'"""(?:.|\n)*?"""', "", SCHEMA_TEXT)
    types = {}
    for m in re.finditer(r"^(type|interface|union|enum) (\w+)([^{=]*?)(?:\{([^\n]*\n(?:.*\n)*?)\}|=((?:\s*\|\s*\w+)+))", text, re.M):
        kind, name, head, body, members = m.groups()
        t = {"kind": kind, "head": head}
        if body is not None:
            t["fields"] = {f: [] for f in re.findall(r"^  (\w+)", body, re.M)}
            for f in re.finditer(r"^  (\w+)\(((?:.|\n)*?)^  \)", body, re.M):
                t["fields"][f.group(1)] = re.findall(r"^    (\w+):", f.group(2), re.M)
        if members is not None:
            t["members"] = re.findall(r"\w+", members)
        types[name] = t
    return types


QUERIES = projects.QUERIES
USED = [  # (type, field, arguments the query passes, the query that uses it)
    ("User", "projectV2", ["number"], "fields"), ("ProjectV2", "id", [], "fields"),
    ("ProjectV2", "fields", ["first", "after"], "fields"), ("ProjectV2", "workflows", ["first", "after"], "workflows"),
    ("ProjectV2SingleSelectField", "id", [], "fields"), ("ProjectV2SingleSelectField", "name", [], "fields"),
    ("ProjectV2SingleSelectField", "options", [], "fields"),
    ("ProjectV2SingleSelectFieldOption", "id", [], "fields"), ("ProjectV2SingleSelectFieldOption", "name", [], "fields"),
    ("ProjectV2Workflow", "name", [], "workflows"), ("ProjectV2Workflow", "enabled", [], "workflows"),
    ("ProjectV2Workflow", "number", [], "workflows"),
    ("Query", "node", ["id"], "items"), ("Issue", "projectItems", ["first", "after"], "items"),
    ("ProjectV2Item", "id", [], "items"), ("ProjectV2Item", "project", [], "items"),
    ("Issue", "timelineItems", ["first", "after", "itemTypes"], "events"),
    ("ProjectV2ItemStatusChangedEvent", "actor", [], "events"), ("ProjectV2ItemStatusChangedEvent", "previousStatus", [], "events"),
    ("ProjectV2ItemStatusChangedEvent", "status", [], "events"), ("ProjectV2ItemStatusChangedEvent", "createdAt", [], "events"),
    ("Actor", "login", [], "events"), ("PageInfo", "hasNextPage", [], "fields"), ("PageInfo", "endCursor", [], "fields"),
]


@pytest.mark.parametrize("typ,field,args,query", USED)
def test_query_names_exist_in_the_public_schema(typ, field, args, query):
    schema = parse_schema()
    assert typ in schema and field in schema[typ]["fields"], (typ, field)
    assert set(args) <= set(schema[typ]["fields"][field]), (typ, field, args)
    assert re.search(r"\b%s\b" % field, QUERIES[query]), "the query no longer uses %s" % field
    for a in args:
        assert re.search(r"\b%s:" % a, QUERIES[query]), a


def test_union_enum_and_interface_names_exist_in_the_public_schema():
    s = parse_schema()
    assert "ProjectV2SingleSelectField" in s["ProjectV2FieldConfiguration"]["members"]
    assert "ProjectV2ItemStatusChangedEvent" in s["IssueTimelineItems"]["members"]
    assert "PROJECT_V2_ITEM_STATUS_CHANGED_EVENT" in SCHEMA_TEXT
    assert "Node" in s["Issue"]["head"] and "Node" in s["ProjectV2"]["head"]
    assert "Actor" in s["User"]["head"]
    for q in ("ProjectV2SingleSelectField", "ProjectV2ItemStatusChangedEvent",
              "PROJECT_V2_ITEM_STATUS_CHANGED_EVENT"):
        assert q in "".join(QUERIES.values()), q


def test_no_query_in_this_module_writes():
    from ghp.gh import is_graphql_write
    assert not any(is_graphql_write(q) or "mutation" in q.lower() for q in QUERIES.values())
