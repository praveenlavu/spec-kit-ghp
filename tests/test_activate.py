"""T19: `ghp activate N`, move a story's Project item to the configured active Status (ado had no such verb; ghp's seam is the Project item).
`Hub` is the stateful fake GitHub of the push tests (issues, one Project) behind the FakeGh write gate."""
import json
from collections import Counter

import pytest

import ghp.__main__ as cli
from ghp import activate, issues, projects
from ghp.config import Config
from ghp.gh import GhError, GhTimeout, is_graphql_write
from ghp.refs import Ref
from test_push import OPTIONS, Hub

CFG = Config("octo", "demo", project_number=3)
ITEM = "PVTI_0011"


def repo(tmp_path, monkeypatch, **cfg):
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", "project_number": 3, **cfg}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)


def hub_with(status="Todo", on_board=True, state="open", title=None, **kw):
    hub = Hub()
    hub.seed("T001", 11, title, on_board=on_board, status=status, state=state, **kw)
    return hub


def status_writes(hub):
    return [c[2] for c in hub.calls if c[0] == "graphql" and "updateProjectV2ItemFieldValue" in c[1]]


def apply(hub, *more, n="11"):
    return cli.main(["activate", n, "--apply", *more], gh=hub)


# ---- AC1: preview shows the move, apply sets it and reads back ----
def test_activate_preview_then_apply(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = hub_with("Todo")
    assert cli.main(["activate", "11", "--plan-out", "plan.md"], gh=hub) == 0
    out = capsys.readouterr().out
    assert "Status: Todo -> In Progress" in out and "#11" in out
    assert (tmp_path / "plan.md").read_text() + "\n" == out
    assert not hub.armed and hub.sent() == [] and hub.status[ITEM] == "Todo"  # a preview sends nothing
    assert apply(hub, "--expect-plan", "plan.md") == 0
    assert hub.status[ITEM] == "In Progress" and hub.armed
    assert status_writes(hub) == [{"project": "PVT_kwFictional0003", "item": ITEM, "field": "PVTSSF_FictionalStatus", "option": OPTIONS["In Progress"]}]
    kinds = [c[0] + ("W" if c[0] == "graphql" and is_graphql_write(c[1]) else "") for c in hub.calls]
    last_write = max(i for i, k in enumerate(kinds) if k == "graphqlW")
    assert any("fieldValueByName" in c[1] for c in hub.calls[last_write + 1:] if c[0] == "graphql")  # set_status read it back
    assert "moved #11 to In Progress" in capsys.readouterr().out


def test_an_item_with_no_status_is_moved_too(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = hub_with(None)
    assert cli.main(["activate", "11"], gh=hub) == 0
    assert "Status: (none) -> In Progress" in capsys.readouterr().out
    assert apply(hub) == 0 and hub.status[ITEM] == "In Progress"


# ---- AC2: never the done option ----
def test_activate_refuses_the_done_option_even_if_configured(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch, status_active="Done")
    hub = hub_with("Todo")
    assert cli.main(["activate", "11"], gh=hub) == 2
    assert "E_ACTIVE_IS_DONE" in capsys.readouterr().err
    assert apply(hub) == 2 and hub.sent() == [] and not hub.armed and hub.status[ITEM] == "Todo" and hub.calls == []  # before any call at all


def test_an_alias_of_the_done_option_is_refused_by_id():
    board = projects.Board("P", "F", {"Todo": "a", "Doing": "d", "Done": "d"}, "Done")  # two names, one id
    issue = issues._issue(Hub().issues[1], Ref("octo", "demo", 1))
    with pytest.raises(activate.ActivateError) as e:
        activate.plan_activate(Hub(), Config("octo", "demo", project_number=3, status_active="Doing"), board, issue)
    assert e.value.code == "E_ACTIVE_IS_DONE"


def test_apply_refuses_any_target_but_the_active_option():
    hub = hub_with("Todo")
    board = projects.discover(hub, CFG)
    issue = issues.get(hub, Ref("octo", "demo", 11))
    for target in ("Done", "Todo", "Nope", "In Progress "):
        plan = activate.plan_activate(hub, CFG, board, issue)
        plan["to"] = target
        hub.arm()
        with pytest.raises(activate.ActivateError) as e:
            activate.apply_activate(hub, CFG, board, plan)
        assert e.value.code == "E_TARGET_NOT_ACTIVE"
    assert hub.sent() == []


def test_done_is_never_sent_to_the_project(tmp_path, monkeypatch):
    repo(tmp_path, monkeypatch)
    hub = hub_with("Todo")
    apply(hub)
    assert [w["option"] for w in status_writes(hub)] == [OPTIONS["In Progress"]]


# ---- AC3: an issue not on the board is added first, in its own request ----
def test_activate_adds_missing_item_first(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = Hub()  # issue 1 is the story; it is not on the board
    assert cli.main(["activate", "1"], gh=hub) == 0
    out = capsys.readouterr().out
    assert "Status: (not on the Project) -> In Progress" in out and "add #1 to the Project" in out and hub.sent() == []
    assert apply(hub, n="1") == 0
    assert hub.writes_of() == ["add_item", "set_status"]  # in that order
    writes = [c for c in hub.calls if c[0] == "graphql" and is_graphql_write(c[1])]
    assert len(writes) == 2 and "addProjectV2ItemById" in writes[0][1] and "updateProjectV2ItemFieldValue" not in writes[0][1]
    assert "addProjectV2ItemById" not in writes[1][1]  # never one request for both
    assert hub.status["PVTI_0001"] == "In Progress"


def test_a_failed_add_stops_before_the_status_write(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = Hub()
    hub.fail[("add_item", 1)] = GhError("E_GH_HTTP", 404, "not found")
    assert apply(hub, n="1") == 1
    out = capsys.readouterr().out
    assert hub.count == Counter(add_item=1) and "nothing was retried" in out and "E_GH_HTTP" in out


def test_a_status_failure_after_the_add_says_the_item_is_on_the_project(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = Hub()
    hub.fail[("set_status", 1)] = GhError("E_GH_HTTP", 422, "nope")
    assert apply(hub, n="1") == 1
    out = capsys.readouterr().out
    assert "was added to the Project" in out and "Status was not set" in out and hub.count == Counter(add_item=1, set_status=1)


# ---- AC4: already active is a no-op ----
def test_activate_is_idempotent(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = hub_with("In Progress")
    assert cli.main(["activate", "11"], gh=hub) == 0
    assert "nothing to do" in capsys.readouterr().out
    assert apply(hub) == 0 and hub.sent() == [] and hub.status[ITEM] == "In Progress"
    assert "nothing to do" in capsys.readouterr().out
    hub2 = hub_with("Todo")  # and twice in a row from Todo writes once
    assert apply(hub2) == 0 and apply(hub2) == 0 and hub2.count == Counter(set_status=1)


# ---- refusals ----
def test_a_closed_issue_is_not_activated(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    for state_reason in ("completed", "not_planned", None):
        hub = hub_with("Todo", state="closed")
        hub.issues[11]["state_reason"] = state_reason
        assert apply(hub) == 2
        assert "E_ISSUE_CLOSED" in capsys.readouterr().err and hub.sent() == []


def test_an_item_already_in_done_is_not_moved_back(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = hub_with("Done")  # an open issue whose item says Done: do not silently re-open the work
    assert apply(hub) == 2
    assert "E_ALREADY_DONE" in capsys.readouterr().err and hub.sent() == [] and hub.status[ITEM] == "Done"


def test_an_open_issue_at_done_is_told_plainly_what_happened_and_what_to_do(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = hub_with("Done")  # reopened: GitHub moves nothing back
    assert apply(hub) == 2
    err = " ".join(capsys.readouterr().err.split())
    assert "E_ALREADY_DONE" in err and "#11 is open but its Project item is at 'Done'" in err
    assert "moved to Done without closing the issue, or the issue was reopened" in err and "move it back by hand in the Project" in err and "ghp never moves a card out of Done" in err
    assert hub.sent() == [] and hub.status[ITEM] == "Done"  # still refused, nothing written
    assert cli.main(["activate", "11"], gh=hub_with("Done")) == 2  # the preview refuses too


def test_the_open_at_done_message_escapes_the_column_name(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = hub_with("Todo")
    with_value(hub, "Fin\x1b[31m\u202eished", OPTIONS["Done"])
    assert apply(hub) == 2
    err = capsys.readouterr().err
    assert "\x1b" not in err and "\u202e" not in err and "Fin\\u001b[31m\\u202eished" in err and "is open but" in err


def test_the_race_message_for_an_open_issue_says_so_too():
    hub = hub_with("Todo")
    board, p = planned(hub)
    hub.status[ITEM] = "Done"
    hub.arm()
    with pytest.raises(activate.ActivateError) as e:
        activate.apply_activate(hub, CFG, board, p)
    assert e.value.code == "E_ALREADY_DONE" and "is open but its Project item is at 'Done'" in str(e.value) and "by hand in the Project" in str(e.value)


def test_a_pull_request_is_refused(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = hub_with("Todo", pr=True)
    assert apply(hub) == 2 and "pull request" in capsys.readouterr().err and hub.sent() == []


def test_a_foreign_repo_is_refused_before_any_call(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = hub_with("Todo")
    assert apply(hub, n="other/repo#11") == 2
    assert "E_FOREIGN_REPO" in capsys.readouterr().err and hub.calls == [] and not hub.armed


def test_no_project_configured_is_a_named_error(tmp_path, monkeypatch, capsys):
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    hub = hub_with("Todo")
    assert apply(hub) == 2
    assert "E_NO_PROJECT" in capsys.readouterr().err and hub.calls == [] and not hub.armed


def test_a_missing_active_option_refuses_before_any_write(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch, status_active="Doing")
    hub = hub_with("Todo")
    assert apply(hub) == 2
    assert "E_STATUS_OPTION_MISSING" in capsys.readouterr().err and hub.sent() == []


# ---- unknown outcome ----
def test_an_unknown_outcome_says_to_rerun_and_the_rerun_reads_first(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = hub_with("Todo")
    hub.fail[("set_status", 1)] = GhTimeout(write=True)  # the write never landed, and the re-read shows it
    assert apply(hub) == 1
    out = capsys.readouterr().out
    assert "outcome unknown" in out and "re-run ghp activate" in out and hub.count == Counter(set_status=1)  # nothing was retried
    assert apply(hub) == 0 and hub.status[ITEM] == "In Progress" and hub.count == Counter(set_status=2)


def test_a_write_that_landed_but_lost_its_reply_is_accepted(tmp_path, monkeypatch):
    repo(tmp_path, monkeypatch)
    hub = hub_with("Todo")
    hub.fail_after[("set_status", 1)] = GhTimeout(write=True)
    assert apply(hub) == 0 and hub.status[ITEM] == "In Progress" and hub.count == Counter(set_status=1)


# ---- the gate ----
def test_expect_plan_binds_the_plan(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    hub = hub_with("Todo")
    cli.main(["activate", "11", "--plan-out", "plan.md"], gh=hub)
    del hub.status[ITEM]  # the world changed after the human approved: the item lost its Status
    assert apply(hub, "--expect-plan", "plan.md") == 2 and hub.sent() == [] and not hub.armed
    assert (tmp_path / "plan.md.new").exists()
    hub2 = hub_with("Todo")  # and a plan for #11 cannot approve #12
    hub2.seed("T002", 12, None, on_board=True, status="Todo")
    assert apply(hub2, "--expect-plan", "plan.md", n="12") == 2 and hub2.sent() == []
    assert apply(hub_with("Todo"), "--expect-plan", "") == 2


# ---- untrusted text ----
def test_texts_from_github_are_escaped_and_the_digest_tells_lookalikes_apart(tmp_path, monkeypatch, capsys):
    repo(tmp_path, monkeypatch)
    plans = []
    for title in ("Hours\x1b[31m red‮", "Hours\\u001b[31m red\\u202e"):  # an escape sequence, then the literal text of its display
        hub = hub_with("Todo", title=title)
        assert cli.main(["activate", "11"], gh=hub) == 0
        plans.append(capsys.readouterr().out)
    assert "\x1b" not in plans[0] and "‮" not in plans[0] and "\\u001b" in plans[0]
    digest = [next(x for x in p.splitlines() if x.startswith("digest: ")) for p in plans]
    assert digest[0] != digest[1]  # both display alike; the exact data differs, so --expect-plan tells them apart


def test_a_cell_shows_no_control_character_and_keeps_backslashes_apart():
    assert activate._cell("a\x1b[2Jb\u202e") == "a\\u001b[2Jb\\u202e"
    assert activate._cell("a\\u001b") == "a\\\\u001b"


# ---- review round 1 ----
def planned(hub, cfg=CFG, n=11):
    board = projects.discover(hub, cfg)
    return board, activate.plan_activate(hub, cfg, board, issues.get(hub, Ref("octo", "demo", n)))


def with_value(hub, name, option):
    """The item's Status reads as (name, option id) whatever the hub holds: a value the option names alone cannot make."""
    real = hub.graphql

    def graphql(query, variables=None):
        if "fieldValueByName" in query and not hub.count["set_status"]:  # until the first write
            hub.calls.append(("graphql", query, variables))
            return {"node": {"fieldValueByName": {"name": name, "optionId": option}}}
        return real(query, variables)
    hub.graphql = graphql


def test_race_on_board_the_item_went_done_after_the_plan_is_not_moved_back():
    hub = hub_with("Todo")
    board, p = planned(hub)
    hub.status[ITEM] = "Done"  # a human, or the Project's own workflow, between the plan read and the apply
    hub.arm()
    with pytest.raises(activate.ActivateError) as e:
        activate.apply_activate(hub, CFG, board, p)
    assert e.value.code == "E_ALREADY_DONE" and hub.count["set_status"] == 0 and status_writes(hub) == [] and hub.status[ITEM] == "Done"


def test_race_add_hands_back_an_existing_done_item_and_it_is_not_moved_back():
    hub = Hub()
    board, p = planned(hub, n=1)
    assert p["item_id"] is None
    hub.items[hub.issues[1]["node_id"]] = "PVTI_0001"  # added by someone else meanwhile, and already Done
    hub.status["PVTI_0001"] = "Done"
    hub.arm()
    with pytest.raises(activate.ActivateError) as e:
        activate.apply_activate(hub, CFG, board, p)
    assert e.value.code == "E_ALREADY_DONE" and hub.count["set_status"] == 0 and hub.status["PVTI_0001"] == "Done"


@pytest.mark.parametrize("on_board", [True, False])
def test_race_item_went_active_meanwhile_is_a_no_op(on_board):
    hub = hub_with("Todo") if on_board else Hub()
    n = 11 if on_board else 1
    board, p = planned(hub, n=n)
    item = ITEM if on_board else "PVTI_0001"
    hub.items[hub.issues[n]["node_id"]] = item
    hub.status[item] = "In Progress"
    hub.arm()
    res = activate.apply_activate(hub, CFG, board, p)
    assert res["done"] and res["noop"] and hub.count["set_status"] == 0
    assert "nothing sent: already In Progress" in activate.render_result(p, res)


# done / active matched by only one of name and id
@pytest.mark.parametrize("name,option", [("Finished", OPTIONS["Done"]), ("Done", "zz000000")])
def test_done_is_recognised_by_id_alone_and_by_name_alone(tmp_path, monkeypatch, capsys, name, option):
    repo(tmp_path, monkeypatch)
    hub = hub_with("Todo")
    with_value(hub, name, option)
    assert apply(hub) == 2
    assert "E_ALREADY_DONE" in capsys.readouterr().err and hub.sent() == []


@pytest.mark.parametrize("name,option", [("In Progress", "dead0001"), ("Doing", OPTIONS["In Progress"])])
def test_active_needs_both_name_and_id_to_be_a_no_op(tmp_path, monkeypatch, name, option):
    repo(tmp_path, monkeypatch)
    hub = hub_with("Todo")
    with_value(hub, name, option)
    board, p = planned(hub)
    assert p["action"] == "activate"  # a value on a dead or renamed option is moved to the live one, never left
    assert apply(hub) == 0 and hub.count["set_status"] == 1


# allow_done is never passed, and the deepest guard stands alone
def test_allow_done_is_never_passed_to_set_status(tmp_path, monkeypatch):
    repo(tmp_path, monkeypatch)
    seen = []
    real = projects.set_status
    monkeypatch.setattr(projects, "set_status", lambda *a, **kw: (seen.append(kw), real(*a, **kw))[1])
    hub = hub_with("Todo")
    assert apply(hub) == 0 and seen == [{}]


def test_set_status_itself_refuses_done_when_every_earlier_guard_is_bypassed(monkeypatch):
    hub = hub_with("Todo")
    board, p = planned(hub)
    cfg = Config("octo", "demo", project_number=3, status_active="Done")
    p.update(to="Done", to_id=OPTIONS["Done"])
    monkeypatch.setattr(activate, "_target", lambda c, b=None: c.status_active)
    hub.arm()
    res = activate.apply_activate(hub, cfg, board, p)
    assert not res["done"] and "E_DONE_NEEDS_THE_GATE" in res["error"] and hub.sent() == []


# every digest field is pinned
def test_each_field_of_the_digest_changes_it():
    import dataclasses
    hub = hub_with("Todo")
    _, p = planned(hub)
    base = activate._digest(p)
    changes = {"item_id": ITEM + "x", "current": ("Todo", "other000"), "to_id": "other000", "to": "Doing", "project_id": "PVT_other",
               "action": "noop", "issue": dataclasses.replace(p["issue"], id=p["issue"].id + 1)}
    for field, value in changes.items():
        assert activate._digest(dict(p, **{field: value})) != base, field
    for field in ("number", "node_id", "title", "state"):
        other = dataclasses.replace(p["issue"], **{field: p["issue"].node_id + "x" if field == "node_id" else p["issue"].title + "x" if field == "title"
                                                   else p["issue"].state + "x"}) if field != "number" else dataclasses.replace(
            p["issue"], ref=Ref("octo", "demo", 12))
        assert activate._digest(dict(p, issue=other)) != base, field


# a ProjectError after the write started is exit 1 like a GhError (a write may have happened), not exit 2
@pytest.mark.parametrize("code", ["E_ITEM_ARCHIVED", "E_STATUS_READBACK_MISMATCH"])
def test_a_project_refusal_during_the_writes_exits_1_like_a_gh_failure(tmp_path, monkeypatch, capsys, code):
    repo(tmp_path, monkeypatch)
    hub = Hub() if code == "E_ITEM_ARCHIVED" else hub_with("Todo")
    real = hub.graphql

    def graphql(query, variables=None):
        if code == "E_ITEM_ARCHIVED" and "addProjectV2ItemById" in query:
            return {"addProjectV2ItemById": {"item": {"id": "PVTI_0001", "isArchived": True}}}
        if code == "E_STATUS_READBACK_MISMATCH" and "fieldValueByName" in query and hub.count["set_status"]:
            return {"node": {"fieldValueByName": {"name": "Todo", "optionId": OPTIONS["Todo"]}}}
        return real(query, variables)
    hub.graphql = graphql
    assert apply(hub, n="1" if code == "E_ITEM_ARCHIVED" else "11") == 1
    assert code in capsys.readouterr().out
