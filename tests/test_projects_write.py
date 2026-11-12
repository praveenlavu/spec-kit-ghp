import json
import re

import pytest

from conftest import FakeGh, FakeRunner, http
from ghp import issues, projects
from ghp.config import Config
from ghp.gh import Gh, GhError, GhTimeout, WriteNotArmed
from ghp.refs import Ref
from test_projects import FIELDS, SCHEMA_TEXT, conn, fx, parse_schema

CFG = Config("octo", "demo", project_number=3)
BOARD = projects.Board("PVT_kwFictional0003", "PVTSSF_FictionalStatus",
                       {"Todo": "f75ad846", "In Progress": "47fc9ee4", "Done": "98236657"}, "Done")
NODE = "I_kwDOFictional0002"
ITEM = "PVTI_FictionalItem3"
ADD, SET, NOW, ITEMS = "addProjectV2ItemById", "updateProjectV2ItemFieldValue", "fieldValueByName", "projectItems("


def an_issue(n=2):
    d = fx("issue_task")
    d.update(number=n, id=9100000000 + n, node_id=NODE, state="open", state_reason=None)
    return issues._issue(d, Ref("octo", "demo", n))


def added(item=ITEM, archived=False):
    return {ADD: {"item": {"id": item, "isArchived": archived}}}


def status_now(name, option):
    return {"node": {NOW: None if name is None else {"name": name, "optionId": option}}}


def writes(gh):
    return [c for c in gh.calls if c[0] == "graphql" and "mutation" in c[1]]


# ---- discover: duplicate Status option names ----
def test_duplicate_status_option_names_are_a_named_error():
    data = fx("project_fields")
    conn(data, FIELDS)["nodes"][2]["options"].append({"id": "dup00001", "name": "Done"})
    with pytest.raises(projects.ProjectError) as e:
        projects.discover(FakeGh().on_graphql("fields(", data), CFG)
    assert e.value.code == "E_STATUS_OPTION_DUPLICATE" and str(e.value).startswith("E_STATUS_OPTION_DUPLICATE")
    assert "'Done'" in str(e.value)


def test_option_names_in_errors_are_escaped_for_the_terminal():
    data = fx("project_fields")
    opts = conn(data, FIELDS)["nodes"][2]["options"]
    opts[2]["name"] = "Fin\x1b[31mished‮"
    with pytest.raises(projects.ProjectError) as e:
        projects.discover(FakeGh().on_graphql("fields(", data), CFG)
    assert "\x1b" not in str(e.value) and "‮" not in str(e.value) and "\\u001b" in str(e.value)


def test_discover_records_which_option_is_done():
    cfg = Config("octo", "demo", project_number=3, status_done="Done")
    assert projects.discover(FakeGh().on_graphql("fields(", fx("project_fields")), cfg).status_done == "Done"
    data = fx("project_fields")
    conn(data, FIELDS)["nodes"][2]["options"][2]["name"] = "Shipped"
    cfg = Config("octo", "demo", project_number=3, status_done="Shipped")
    assert projects.discover(FakeGh().on_graphql("fields(", data), cfg).status_done == "Shipped"


# ---- archived items ----
def test_items_are_read_without_archived_ones():
    assert "includeArchived: false" in projects.QUERIES["items"]
    gh = FakeGh().on_graphql(ITEMS, fx("project_items"))
    projects.item_for(gh, BOARD, NODE)
    assert "includeArchived: false" in gh.calls[0][1]


def test_add_item_refuses_an_archived_item():
    gh = FakeGh().on_graphql(ADD, added(archived=True))
    gh.arm()
    with pytest.raises(projects.ProjectError) as e:
        projects.add_item(gh, BOARD, an_issue())
    assert e.value.code == "E_ITEM_ARCHIVED" and ITEM in str(e.value)


# ---- add_item ----
def test_add_item_and_set_status_are_separate_requests():
    gh = FakeGh().on_graphql(ADD, added()).on_graphql(SET, {SET: {"projectV2Item": {"id": ITEM}}})
    gh.on_graphql(NOW, status_now("In Progress", "47fc9ee4"))
    gh.arm()
    item = projects.add_item(gh, BOARD, an_issue())
    assert len(writes(gh)) == 1 and ADD in writes(gh)[0][1] and SET not in writes(gh)[0][1]
    projects.set_status(gh, BOARD, item, "In Progress")
    w = writes(gh)
    assert len(w) == 2 and SET in w[1][1] and ADD not in w[1][1]  # never both in one request
    assert all(not (ADD in c[1] and SET in c[1]) for c in gh.calls if c[0] == "graphql")
    assert w[0][2] == {"project": BOARD.project_id, "content": NODE}
    assert w[1][2] == {"project": BOARD.project_id, "item": ITEM, "field": BOARD.status_field_id, "option": "47fc9ee4"}


def test_add_item_is_idempotent():
    gh = FakeGh().on_graphql(ADD, added())  # GitHub returns the existing item for a repeated add (SPIKE, live)
    gh.arm()
    first = projects.add_item(gh, BOARD, an_issue())
    second = projects.add_item(gh, BOARD, an_issue())
    assert first == second == ITEM and len(writes(gh)) == 2
    assert gh.calls[0][2] == gh.calls[1][2]


@pytest.mark.parametrize("reply", [{}, {ADD: None}, {ADD: {}}, {ADD: {"item": None}}, {ADD: {"item": {"id": ""}}},
                                   {ADD: {"item": {"id": 5, "isArchived": False}}}, {ADD: {"item": {"id": "x"}}},
                                   {ADD: {"item": {"id": "x", "isArchived": "no"}}}])
def test_add_item_with_a_reply_of_the_wrong_shape_is_an_unknown_outcome_and_is_reread(reply):
    gh = FakeGh().on_graphql(ADD, reply).on_graphql(ITEMS, {"node": {"projectItems": {
        "pageInfo": {"hasNextPage": False, "endCursor": "c"}, "nodes": []}}})
    gh.arm()
    with pytest.raises(GhError) as e:
        projects.add_item(gh, BOARD, an_issue())
    assert e.value.code == "E_GH_BAD_BODY" and "unknown whether applied" in str(e.value)
    assert len(writes(gh)) == 1 and sum(ITEMS in c[1] for c in gh.calls if c[0] == "graphql") == 1


def test_add_item_after_a_timeout_returns_the_item_found_on_the_board():
    def timeout(_v):
        raise GhTimeout(write=True)
    gh = FakeGh().on_graphql(ADD, timeout).on_graphql(ITEMS, fx("project_items"))
    gh.arm()
    assert projects.add_item(gh, BOARD, an_issue()) == ITEM
    assert len(writes(gh)) == 1  # no second add


def test_add_item_after_a_timeout_with_the_item_not_found_raises_the_original_error():
    def timeout(_v):
        raise GhTimeout(write=True)
    other = fx("project_items")
    conn(other, ("node", "projectItems"))["nodes"].pop()
    gh = FakeGh().on_graphql(ADD, timeout).on_graphql(ITEMS, other)
    gh.arm()
    with pytest.raises(GhError) as e:
        projects.add_item(gh, BOARD, an_issue())
    assert e.value.code == "E_GH_TIMEOUT" and len(writes(gh)) == 1


def test_a_rejected_add_is_not_reread():
    def rejected(_v):
        raise GhError("E_GH_GRAPHQL", 200, "Could not resolve to a node")
    gh = FakeGh().on_graphql(ADD, rejected)
    gh.arm()
    with pytest.raises(GhError):
        projects.add_item(gh, BOARD, an_issue())
    assert len(gh.calls) == 1


RACE = "Content already exists in this project"  # live reply when the Project's auto-add workflow added the issue first (T34)
RACE_REPLY = {"data": {ADD: None}, "errors": [{"type": "UNPROCESSABLE", "path": [ADD], "locations": [{"line": 1, "column": 48}],
                                               "message": RACE}]}


def test_the_auto_add_race_reply_rereads_the_board_and_returns_the_item():
    runner = FakeRunner(http(200, RACE_REPLY), http(200, {"data": fx("project_items")}))
    gh = Gh(runner=runner, sleep=lambda s: None)
    gh.arm()
    assert projects.add_item(gh, BOARD, an_issue()) == ITEM
    sent = [json.loads(c["stdin"])["query"] for c in runner.calls]
    assert len(sent) == 2 and ADD in sent[0] and ITEMS in sent[1]  # one add, then a read; the add is never repeated


def test_the_auto_add_race_reply_with_no_item_on_the_board_raises_it():
    other = fx("project_items")
    conn(other, ("node", "projectItems"))["nodes"].pop()
    gh = FakeGh().on_graphql(ADD, _boom(GhError("E_GH_GRAPHQL", 200, RACE))).on_graphql(ITEMS, other)
    gh.arm()
    with pytest.raises(GhError) as e:
        projects.add_item(gh, BOARD, an_issue())
    assert e.value.code == "E_GH_GRAPHQL" and RACE in str(e.value) and len(writes(gh)) == 1


@pytest.mark.parametrize("err", [GhError("E_GH_GRAPHQL", 200, "Could not resolve to a node"),
                                 GhError("E_GH_GRAPHQL", 200, RACE + "."), GhError("E_GH_GRAPHQL", 200, RACE.lower()),
                                 GhError("E_GH_GRAPHQL", 200, RACE + "; Something else"),
                                 GhError("E_GH_HTTP", 422, RACE)])
def test_other_add_errors_still_raise_without_a_reread(err):
    gh = FakeGh().on_graphql(ADD, _boom(err)).on_graphql(ITEMS, fx("project_items"))
    gh.arm()
    with pytest.raises(GhError) as e:
        projects.add_item(gh, BOARD, an_issue())
    assert e.value is err and len(gh.calls) == 1


@pytest.mark.parametrize("node", ["", None, 5])
def test_add_item_refuses_an_issue_without_a_node_id(node):
    bad = issues.Issue(**dict(vars(an_issue()), node_id=node))
    gh = FakeGh()
    gh.arm()
    with pytest.raises(ValueError):
        projects.add_item(gh, BOARD, bad)
    assert gh.calls == []


# ---- set_status ----
def set_gh(now):
    gh = FakeGh().on_graphql(SET, {SET: {"projectV2Item": {"id": ITEM}}}).on_graphql(NOW, now)
    gh.arm()
    return gh


def test_set_status_writes_the_option_id_by_name_then_reads_it_back():
    gh = set_gh(status_now("In Progress", "47fc9ee4"))
    projects.set_status(gh, BOARD, ITEM, "In Progress")
    assert [("mutation" in c[1]) for c in gh.calls] == [True, False]
    assert gh.calls[1][2] == {"item": ITEM}


def test_set_status_reads_back_and_raises_on_mismatch():
    for now in (status_now("Todo", "f75ad846"),          # another option: the write did not take
                status_now("In Progress", "f75ad846"),   # right name, wrong id
                status_now("In Progress", "47fc9ee5"),
                status_now(None, None)):                 # no Status value at all
        gh = set_gh(now)
        with pytest.raises(projects.ProjectError) as e:
            projects.set_status(gh, BOARD, ITEM, "In Progress")
        assert e.value.code == "E_STATUS_READBACK_MISMATCH" and str(e.value).startswith("E_STATUS_READBACK_MISMATCH")
        assert ITEM in str(e.value)


@pytest.mark.parametrize("now", [{}, {"node": None}, {"node": {}}, {"node": {NOW: "Done"}}, {"node": {NOW: {"name": "x"}}},
                                 {"node": {NOW: {"name": 1, "optionId": "x"}}}, {"node": {NOW: {}}}])
def test_set_status_with_a_read_back_of_the_wrong_shape_fails_closed(now):
    gh = set_gh(now)
    with pytest.raises(GhError) as e:
        projects.set_status(gh, BOARD, ITEM, "In Progress")
    assert e.value.code == "E_GH_BAD_BODY"


def test_set_status_compares_names_exactly():
    gh = set_gh(status_now("in progress", "47fc9ee4"))
    with pytest.raises(projects.ProjectError) as e:
        projects.set_status(gh, BOARD, ITEM, "In Progress")
    assert e.value.code == "E_STATUS_READBACK_MISMATCH"
    gh = set_gh(None)
    for to in ("in progress", "In Progress ", "", None, 5, "Nope"):
        with pytest.raises(projects.ProjectError) as e:
            projects.set_status(gh, BOARD, ITEM, to)
        assert e.value.code == "E_STATUS_OPTION_MISSING"
    assert gh.calls == []


@pytest.mark.parametrize("item", ["", None, 5])
def test_set_status_refuses_an_item_id_that_is_not_a_non_empty_string(item):
    gh = set_gh(None)
    with pytest.raises(ValueError):
        projects.set_status(gh, BOARD, item, "Todo")
    assert gh.calls == []


def test_moving_to_done_is_refused_outside_done():
    cfg = Config("octo", "demo", project_number=3)
    board = projects.discover(FakeGh().on_graphql("fields(", fx("project_fields")), cfg)
    gh = set_gh(status_now("Done", "98236657"))
    with pytest.raises(projects.ProjectError) as e:
        projects.set_status(gh, board, ITEM, cfg.status_done)
    assert e.value.code == "E_DONE_NEEDS_THE_GATE" and str(e.value).startswith("E_DONE_NEEDS_THE_GATE")
    assert gh.calls == []  # refused before any request
    with pytest.raises(TypeError):
        projects.set_status(gh, board, ITEM, cfg.status_done, True)  # allow_done is keyword-only
    projects.set_status(gh, board, ITEM, cfg.status_done, allow_done=True)
    assert len(writes(gh)) == 1


def test_the_done_guard_follows_the_configured_name_not_the_word_done():
    data = fx("project_fields")
    conn(data, FIELDS)["nodes"][2]["options"][2]["name"] = "Shipped"
    cfg = Config("octo", "demo", project_number=3, status_done="Shipped")
    board = projects.discover(FakeGh().on_graphql("fields(", data), cfg)
    gh = set_gh(status_now("Shipped", "98236657"))
    with pytest.raises(projects.ProjectError) as e:
        projects.set_status(gh, board, ITEM, "Shipped")
    assert e.value.code == "E_DONE_NEEDS_THE_GATE"


def test_set_status_after_an_unknown_outcome_rereads_and_accepts_what_it_finds():
    def timeout(_v):
        raise GhTimeout(write=True)
    gh = FakeGh().on_graphql(SET, timeout).on_graphql(NOW, status_now("In Progress", "47fc9ee4"))
    gh.arm()
    projects.set_status(gh, BOARD, ITEM, "In Progress")  # it was applied
    assert len(writes(gh)) == 1
    gh = FakeGh().on_graphql(SET, timeout).on_graphql(NOW, status_now("Todo", "f75ad846"))
    gh.arm()
    with pytest.raises(GhError) as e:
        projects.set_status(gh, BOARD, ITEM, "In Progress")
    assert e.value.code == "E_GH_TIMEOUT" and len(writes(gh)) == 1


def test_a_rejected_status_write_is_not_reread():
    def rejected(_v):
        raise GhError("E_GH_GRAPHQL", 200, "Could not resolve to a node")
    gh = FakeGh().on_graphql(SET, rejected)
    gh.arm()
    with pytest.raises(GhError):
        projects.set_status(gh, BOARD, ITEM, "Todo")
    assert len(gh.calls) == 1


# ---- AC7: nothing is sent before the human gate armed Gh ----
def unarmed_real():
    runner = FakeRunner()
    return Gh(runner=runner), (lambda: runner.calls)


def unarmed_fake():
    gh = FakeGh()
    return gh, (lambda: gh.calls)


@pytest.mark.parametrize("make", [unarmed_real, unarmed_fake], ids=["real Gh", "FakeGh"])
def test_primitives_refuse_when_gh_is_not_armed(make):
    parent, child = an_issue(1), an_issue(2)
    calls = [
        lambda gh: issues.create(gh, CFG, "T", "b", ["ghp:task"], None),
        lambda gh: issues.create(gh, CFG, "T", "b", ["ghp:task"], parent, milestone=3),
        lambda gh: issues.add_sub_issue(gh, parent, child),
        lambda gh: issues.close(gh, child, "completed"),
        lambda gh: projects.add_item(gh, BOARD, child),
        lambda gh: projects.set_status(gh, BOARD, ITEM, "In Progress"),
    ]
    for call in calls:
        gh, spawned = make()
        with pytest.raises(WriteNotArmed):
            call(gh)
        assert spawned() == []  # zero spawns: not even a read


# ---- the mutations are the ones in the tracked subset of the public schema (T17b) ----
SCHEMA = SCHEMA_TEXT


def input_fields(name):
    body = re.search(r"^input %s\b[^{]*\{((?:.|\n)*?)^\}" % name, SCHEMA, re.M).group(1)
    return set(re.findall(r"^  (\w+):", body, re.M))


def test_mutation_and_read_back_names_exist_in_the_public_schema():
    s = parse_schema()
    assert {"projectId", "contentId"} <= input_fields("AddProjectV2ItemByIdInput") and "item" in s["AddProjectV2ItemByIdPayload"]["fields"]
    assert {"projectId", "itemId", "fieldId", "value"} <= input_fields("UpdateProjectV2ItemFieldValueInput")
    assert "singleSelectOptionId" in input_fields("ProjectV2FieldValue")
    assert {"isArchived", "id", "fieldValueByName"} <= set(s["ProjectV2Item"]["fields"])
    assert {"name", "optionId"} <= set(s["ProjectV2ItemFieldSingleSelectValue"]["fields"])
    assert "includeArchived" in s["Issue"]["fields"]["projectItems"]
    assert {ADD, SET} <= set(s["Mutation"]["fields"])
    text = " ".join(projects.MUTATIONS.values())
    for used in ("projectId:", "contentId:", "itemId:", "fieldId:", "value:", "singleSelectOptionId:", "isArchived"):
        assert used in text, used


def test_the_mutations_are_the_only_writes_in_this_module():
    from ghp.gh import is_graphql_write
    assert all(is_graphql_write(q) for q in projects.MUTATIONS.values())
    assert not any(is_graphql_write(q) for q in projects.QUERIES.values())


# ---- round 1 of the review ----
def _boom(err):
    def raiser(_v):
        raise err
    return raiser


def test_a_failed_reread_keeps_the_original_write_error_add_item_and_set_status():
    gh = FakeGh().on_graphql(ADD, _boom(GhTimeout(write=True))).on_graphql(ITEMS, _boom(GhTimeout(write=False)))
    gh.arm()
    with pytest.raises(GhError) as e:
        projects.add_item(gh, BOARD, an_issue())
    assert issues.maybe_applied(e.value) and "unknown whether applied" in str(e.value) and e.value.__cause__ is not None
    assert len(writes(gh)) == 1
    gh = FakeGh().on_graphql(SET, _boom(GhTimeout(write=True))).on_graphql(NOW, _boom(GhTimeout(write=False)))
    gh.arm()
    with pytest.raises(GhError) as e:
        projects.set_status(gh, BOARD, ITEM, "Todo")
    assert issues.maybe_applied(e.value) and "unknown whether applied" in str(e.value) and e.value.__cause__ is not None


@pytest.mark.parametrize("reply", [{ADD: "x"}, {ADD: [1]}, {ADD: 5}, {ADD: None}, {ADD: {"item": "x"}}, {ADD: {"item": [1]}},
                                   {ADD: {"item": {"isArchived": False}}}, {ADD: {"item": {"id": ["a"], "isArchived": False}}},
                                   {ADD: {"item": {"id": "", "isArchived": False}}}])
def test_no_reply_shape_after_an_add_escapes_as_anything_but_a_named_unknown_outcome(reply):
    nothing = {"node": {"projectItems": {"pageInfo": {"hasNextPage": False, "endCursor": "c"}, "nodes": []}}}
    gh = FakeGh().on_graphql(ADD, reply).on_graphql(ITEMS, nothing)
    gh.arm()
    with pytest.raises(GhError) as e:
        projects.add_item(gh, BOARD, an_issue())
    assert e.value.code == "E_GH_BAD_BODY" and "unknown whether applied" in str(e.value)
    assert sum(ITEMS in c[1] for c in gh.calls if c[0] == "graphql") == 1  # it re-read first
    gh = FakeGh().on_graphql(ADD, reply).on_graphql(ITEMS, fx("project_items"))
    gh.arm()
    assert projects.add_item(gh, BOARD, an_issue()) == ITEM  # and the re-read found the item


# ---- the done guard fails closed and compares option ids ----
def test_a_board_without_a_done_option_is_refused_and_nothing_is_sent():
    shipped = {"Todo": "1", "Shipped": "9"}
    for board in (projects.Board("p", "f", shipped),                       # built by hand, 3 arguments
                  projects.Board("p", "f", shipped, None),
                  projects.Board("p", "f", shipped, "Done"),               # a name that is not an option
                  projects.Board("p", "f", shipped, ""), projects.Board("p", "f", shipped, 5)):
        gh = set_gh(status_now("Shipped", "9"))
        for to in ("Shipped", "Todo"):
            with pytest.raises(projects.ProjectError) as e:
                projects.set_status(gh, board, ITEM, to)
            assert e.value.code == "E_BOARD_NO_DONE"
            with pytest.raises(projects.ProjectError):
                projects.set_status(gh, board, ITEM, to, allow_done=True)  # not even the gate may pass a Board with no Done
        assert gh.calls == []
    assert projects.Board._field_defaults.get("status_done") is None  # no default NAME


def test_the_guard_refuses_the_done_option_under_any_name_for_it():
    board = projects.Board("p", "f", {"Todo": "1", "Closed": "9", "Shipped": "9"}, "Shipped")  # two names, one option id
    gh = set_gh(status_now("Closed", "9"))
    for to in ("Shipped", "Closed"):
        with pytest.raises(projects.ProjectError) as e:
            projects.set_status(gh, board, ITEM, to)
        assert e.value.code == "E_DONE_NEEDS_THE_GATE"
    assert gh.calls == []
    projects.set_status(gh, board, ITEM, "Closed", allow_done=True)
    assert len(writes(gh)) == 1


@pytest.mark.parametrize("to", ["98236657", "done", "DONE", "Done ", " Done", "Donе", "D​one", "Done\n", "Ｄone"])
def test_the_done_id_and_look_alike_names_are_not_options(to):
    gh = set_gh(status_now("Done", "98236657"))
    with pytest.raises(projects.ProjectError) as e:
        projects.set_status(gh, BOARD, ITEM, to)
    assert e.value.code == "E_STATUS_OPTION_MISSING" and gh.calls == []


# ---- escaping and scope ----
def test_server_text_in_messages_is_escaped():
    data = fx("project_fields")
    conn(data, FIELDS)["nodes"].pop()
    conn(data, FIELDS)["nodes"][1]["name"] = "Prio\x1b[31mrity"
    with pytest.raises(projects.ProjectError) as e:
        projects.discover(FakeGh().on_graphql("fields(", data), CFG)
    assert e.value.code == "E_STATUS_FIELD_MISSING" and "\x1b" not in str(e.value) and "\\u001b" in str(e.value)
    gh = set_gh(status_now("Todo", "f75ad846\x1b[2J"))
    with pytest.raises(projects.ProjectError) as e:
        projects.set_status(gh, BOARD, ITEM, "In Progress")
    assert "\x1b" not in str(e.value) and "\\u001b" in str(e.value)


def test_project_show_escapes_workflow_names_and_ids(tmp_path, monkeypatch, capsys):
    import json as _json
    (tmp_path / "ghp.config.json").write_text(_json.dumps({"owner": "octo", "repo": "demo", "project_number": 3}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    flows = fx("project_workflows")
    conn(flows, ("user", "projectV2", "workflows"))["nodes"][0]["name"] = "Item\x1b[31m closed"
    gh = FakeGh().on_graphql("fields(", fx("project_fields")).on_graphql("workflows(", flows)
    assert projects.run(None, gh) == 0
    assert "\x1b" not in capsys.readouterr().out


def test_a_scope_less_scope_error_on_the_status_read_names_read_project():
    from ghp.gh import ScopeMissing
    gh = FakeGh().on_graphql(SET, {}).on_graphql(NOW, _boom(ScopeMissing([])))
    gh.arm()
    with pytest.raises(ScopeMissing) as e:
        projects.set_status(gh, BOARD, ITEM, "Todo")
    assert e.value.scopes == ["read:project"]


# ---- round 2 of the review ----
class _AlwaysEqual(str):
    def __eq__(self, other):
        return True

    __hash__ = str.__hash__


@pytest.mark.parametrize("flag", [1, 2, "yes", "True", "", [0], (1,), object(), _AlwaysEqual("x"), 1.0, b"1", {"a": 1}])
def test_only_the_real_true_opens_the_done_gate(flag):
    gh = set_gh(status_now("Done", "98236657"))
    with pytest.raises(projects.ProjectError) as e:
        projects.set_status(gh, BOARD, ITEM, "Done", allow_done=flag)
    assert e.value.code == "E_DONE_NEEDS_THE_GATE" and gh.calls == []
    projects.set_status(gh, BOARD, ITEM, "Done", allow_done=True)
    assert len(writes(gh)) == 1


def test_a_mock_like_flag_and_a_mock_like_name_do_not_open_the_gate():
    from unittest import mock
    gh = set_gh(status_now("Done", "98236657"))
    with pytest.raises(projects.ProjectError) as e:
        projects.set_status(gh, BOARD, ITEM, "Done", allow_done=mock.MagicMock())
    assert e.value.code == "E_DONE_NEEDS_THE_GATE"
    for bad in (mock.MagicMock(), _AlwaysEqual("Done")):  # `to` and the item id are exact strs too
        with pytest.raises(projects.ProjectError):
            projects.set_status(gh, BOARD, ITEM, bad, allow_done=True)
        with pytest.raises(ValueError):
            projects.set_status(gh, BOARD, bad, "Todo")
    assert gh.calls == []


def test_the_read_back_mismatch_message_escapes_the_option_id_asked_for():
    board = projects.Board("p", "f", {"Todo": "f7\x1b[2Jx", "Done": "9"}, "Done")
    gh = set_gh(status_now("Done", "9"))
    with pytest.raises(projects.ProjectError) as e:
        projects.set_status(gh, board, ITEM, "Todo")
    assert e.value.code == "E_STATUS_READBACK_MISMATCH" and "\x1b" not in str(e.value) and "\\u001b" in str(e.value)


def test_project_show_escapes_ids_and_option_ids(tmp_path, monkeypatch, capsys):
    import json as _json
    (tmp_path / "ghp.config.json").write_text(_json.dumps({"owner": "octo", "repo": "demo", "project_number": 3}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    data = fx("project_fields")
    project = data["user"]["projectV2"]
    project["id"] = "PVT_\x1b[31mp"
    status = conn(data, FIELDS)["nodes"][2]
    status["id"] = "PVTSSF_\x1b[31mf"
    status["options"][0]["id"] = "opt\x1b[31mo"
    gh = FakeGh().on_graphql("fields(", data).on_graphql("workflows(", fx("project_workflows"))
    assert projects.run(None, gh) == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out and out.count("\\u001b") == 3
