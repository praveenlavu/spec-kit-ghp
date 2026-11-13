"""T13-AC2, AC3, AC5: the read verbs, the `gh api --input -` GraphQL transport and the query names, live against the scratch repo.
Everything is read only (no arm()). The fictional story, its sub-issue and its comment were created by hand (docs/evidence/T13.md)."""
import json

import pytest

from ghp import comments, projects
from ghp.__main__ import main
from ghp.gh import Gh, subprocess_runner
from live.conftest import OWNER, REPO, REPO_PATH


def run(capsys, gh, *argv):
    """(exit code, stdout, stderr) of `ghp <argv>` with the real transport."""
    code = main(list(argv), gh)
    out, err = capsys.readouterr()
    return code, out, err


# ---- AC2 ----
def test_get_story_and_its_sub_issue(gh, scratch, config_dir, capsys):
    code, out, _ = run(capsys, gh, "get", str(scratch["story"]))
    story = json.loads(out)
    assert code == 0 and story["ref"] == "%s/%s#%d" % (OWNER, REPO, scratch["story"])
    assert "ghp:live-test" in story["labels"] and story["sub_total"] == 1 and story["parent"] is None
    assert "## Acceptance criteria" in story["body"]
    code, out, _ = run(capsys, gh, "get", "#%d" % scratch["sub"])
    sub = json.loads(out)
    assert code == 0 and sub["parent"] == story["ref"] and type(sub["id"]) is int


def test_a_body_written_through_the_api_comes_back_as_written(gh, scratch):
    body = gh.rest("GET", "%s/issues/%d" % (REPO_PATH, scratch["story"]))["body"]
    assert "\r" not in body and "opening hours.  \n" in body and "item\twith tab" in body and body.endswith("\n")


def test_get_refuses_a_pull_request(gh, scratch, config_dir, capsys):
    code, out, err = run(capsys, gh, "get", str(scratch["pr"]))
    assert code == 2 and out == "" and "E_NOT_AN_ISSUE" in err


def test_comments(gh, scratch, config_dir, capsys):
    code, out, err = run(capsys, gh, "comments", str(scratch["story"]))
    assert code == 0 and "/approve D1" in out and "(User)" in out and "edits 0" in out and err == ""
    assert (config_dir / ".ghp" / "context" / ("%d.md" % scratch["story"])).is_file()


def test_project_show(gh, config_dir, capsys):
    """Any switch state: the six built-in workflows are listed, each as enabled or disabled exactly as GitHub reports it
    (the switches are set in the Project's settings by hand and may be on or off)."""
    code, out, _ = run(capsys, gh, "project", "show")
    assert code == 0
    for name, role in (("Todo", "status_todo"), ("In Progress", "status_active"), ("Done", "status_done")):
        assert any(l.split()[:len(name.split())] == name.split() and l.endswith("<- " + role) for l in out.splitlines()), name
    names = {"Item closed", "Pull request merged", "Auto-close issue", "Auto-add sub-issues to project",
             "Pull request linked to issue", "Item added to project"}
    flows = projects.workflows(gh, _cfg(config_dir))
    assert {w.name for w in flows} == names
    for w in flows:
        assert any(l.split()[:1] == ["#%d" % w.number] and l.endswith(" enabled" if w.enabled else " disabled")
                   for l in out.splitlines()), (w, out)


def _cfg(config_dir):
    from ghp.config import Config
    return Config.load(str(config_dir))


def test_dod_and_rules(gh, scratch, config_dir, capsys):
    code, out, _ = run(capsys, gh, "dod", str(scratch["story"]))
    assert code == 0 and json.loads(out)["action"] == "created" and (config_dir / "dod" / ("DoD_%d.md" % scratch["story"])).is_file()
    assert "AC1 the hours page" in (config_dir / "dod" / ("DoD_%d.md" % scratch["story"])).read_text()
    code, out, _ = run(capsys, gh, "rules", str(scratch["sub"]))
    assert code == 0 and json.loads(out)["problems"] == []


def test_status_events_and_project_item_of_the_story(gh, scratch, config_dir):
    cfg = _cfg(config_dir)
    board = projects.discover(gh, cfg)
    node = gh.rest("GET", "%s/issues/%d" % (REPO_PATH, scratch["story"]))["node_id"]
    assert projects.item_for(gh, board, node)  # the story was put on Project 3 by hand
    events = projects.status_events(gh, node)
    # the "Item added to project" workflow set Todo, the owner set In Progress; closing the issue (cleanup) adds a Done by automation
    mine = [e for e in events if e.to == "In Progress"]
    assert mine and mine[0].from_ == "Todo" and mine[0].actor_login == OWNER and not mine[0].is_automation
    assert events[0].to == "Todo" and events[0].is_automation


# ---- AC3 ----
def test_input_dash_carries_a_graphql_json_body_through_the_real_transport(gh):  # `gh`: the guard has run
    seen = []

    def spy(argv, stdin, timeout):
        seen.append((argv, stdin))
        return subprocess_runner(argv, stdin, timeout)

    query = "query($login: String!) { user(login: $login) { login databaseId } }"
    data = Gh(runner=spy).graphql(query, {"login": OWNER})
    assert data["user"]["login"] == OWNER and type(data["user"]["databaseId"]) is int
    (argv, stdin), = seen
    assert argv[-2:] == ["--input", "-"] and not {"-f", "-F", "--field", "--raw-field"} & set(argv)
    assert not any(query in a or OWNER in a for a in argv)  # no query text and no variable in argv (X3)
    assert json.loads(stdin) == {"query": query, "variables": {"login": OWNER}}


# ---- AC5 ----
def introspect(gh, type_name):
    t = gh.graphql('query($n: String!) { __type(name: $n) { fields { name } enumValues { name } } }', {"n": type_name})["__type"]
    assert t, "type %s is not in the live schema" % type_name
    return {x["name"] for x in (t["fields"] or t["enumValues"] or [])}


EXPECTED_NAMES = {
    "Issue": {"projectItems", "comments", "timelineItems"},
    "User": {"projectV2"},
    "Repository": {"issue"},
    "ProjectV2": {"id", "fields", "workflows"},
    "ProjectV2Workflow": {"name", "enabled", "number"},
    "ProjectV2ItemConnection": {"nodes", "pageInfo"},
    "ProjectV2Item": {"id", "project"},
    "ProjectV2SingleSelectField": {"id", "name", "options"},
    "ProjectV2SingleSelectFieldOption": {"id", "name"},
    "ProjectV2ItemStatusChangedEvent": {"actor", "previousStatus", "status", "createdAt", "wasAutomated"},
    "IssueTimelineItemsItemType": {"PROJECT_V2_ITEM_STATUS_CHANGED_EVENT"},
    "IssueComment": {"id", "databaseId", "body", "createdAt", "lastEditedAt", "isMinimized", "viewerDidAuthor", "author", "userContentEdits"},
    "PageInfo": {"hasNextPage", "endCursor"},
}


@pytest.mark.parametrize("type_name", sorted(EXPECTED_NAMES))
def test_query_names_exist_in_the_live_schema(gh, type_name):
    assert EXPECTED_NAMES[type_name] <= introspect(gh, type_name)


def test_every_query_in_the_code_is_valid_live(gh, scratch):
    # GraphQL validates the whole document: a name that is not in the schema fails the call (E_GH_GRAPHQL)
    node = gh.rest("GET", "%s/issues/%d" % (REPO_PATH, scratch["story"]))["node_id"]
    base = {"owner": OWNER, "number": 3, "cursor": None}
    for name, variables in (("fields", base), ("workflows", base), ("items", {"issue": node, "cursor": None}),
                            ("events", {"issue": node, "cursor": None})):
        assert gh.graphql(projects.QUERIES[name], variables)
    assert gh.graphql(comments._QUERY, {"owner": OWNER, "repo": REPO, "number": scratch["story"], "cursor": None})
