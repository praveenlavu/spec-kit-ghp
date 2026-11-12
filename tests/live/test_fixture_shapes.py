"""T13-AC4: the hand-authored fixtures in tests/fixtures/gh against the live responses they stand for.

A key set is the set of key paths (`user.login`, `labels[].name`); a list contributes the union of its items, and a path under
a null or empty value (in EVERY item of a list) on either side is not compared (a null says nothing about what it would hold). Two diffs per fixture:
- fixture_only: paths the fixture has and the live response lacks (a key the fixture invented, or GitHub dropped): asserted
  against EXPECTED_FIXTURE_ONLY, which holds every known case, so any new one fails the test.
- live_only: paths live has and the fixture lacks. The fixtures are deliberately minimal (they hold what the code reads),
  so this list is long for REST; the test asserts that every key the CODE reads is in the fixture and prints the rest
  (`-rA` shows it; docs/evidence/T13.md records it).
GraphQL fixtures are compared with a query that selects every field the fixture holds (a GraphQL response holds only what was
selected), and the code's own query must return only paths the fixture holds.
"""
import json
from pathlib import Path

import pytest

from ghp import comments, projects
from live.conftest import OWNER, REPO, REPO_PATH

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "gh"

# `fixture path -> why`, per fixture file. Empty means the fixture's keys all exist live.
EXPECTED_FIXTURE_ONLY = {
    "issue_story.json": {},
    "issue_task.json": {},
    "issue_pull_request.json": {},
    "comments_rest.json": {},
    "comments_graphql.json": {},
    "project_fields.json": {},
    "project_workflows.json": {},
    "project_items.json": {},
    "status_events.json": {},
}
# fixture paths with keys of their own that live leaves null or empty in the response compared with, so nothing under them is compared
UNCOMPARED_OK = {
    "issue_pull_request.json": {"milestone": "no pull request with a milestone exists in the scratch repo; issues._issue raises NotAnIssue "
                                             "before it reads a milestone, so the code never reads one from a PR",
                              "labels": "PR #18 has no labels; the code never reads labels of a PR (NotAnIssue comes first)"},
    "comments_rest.json": {"[].performed_via_github_app": "the live story has only a human comment, so the app object shape is NOT "
                                                          "compared (UNVERIFIED live; the code only checks dict-or-null)"},
}
# the keys the code reads, per fixture (a subset of the live keys; the fixture must hold all of them)
READ_BY_CODE = {
    "issue_story.json": ["id", "node_id", "number", "title", "body", "state", "state_reason", "labels[].name", "milestone.title",
                         "user.id", "user.login", "user.type", "html_url", "sub_issues_summary.total", "sub_issues_summary.completed",
                         "repository_url"],
    "issue_task.json": ["id", "node_id", "number", "title", "body", "state", "state_reason", "labels", "milestone", "user.id", "user.login",
                        "user.type", "html_url", "sub_issues_summary.total", "sub_issues_summary.completed", "parent_issue_url",
                        "repository_url"],
    "issue_pull_request.json": ["pull_request", "number"],
    "comments_rest.json": ["[].id", "[].node_id", "[].user.id", "[].user.login", "[].user.type", "[].performed_via_github_app"],
}


def key_paths(value, prefix=""):
    """(paths, opaque): every key path, and the paths whose value is null or an empty list or dict (nothing below them is known)."""
    paths, opaque = set(), set()
    if isinstance(value, dict):
        for k, v in value.items():
            p = prefix + "." + k if prefix else k
            paths.add(p)
            if v is None or v == [] or v == {}:
                opaque.add(p)
            else:
                sub, op = key_paths(v, p)
                paths |= sub
                opaque |= op
    elif isinstance(value, list):
        per_item = [key_paths(item, prefix + "[]") for item in value]
        paths = set().union(*(sub for sub, _ in per_item))
        opaque = set.intersection(*(op for _, op in per_item)) if per_item else set()  # null/empty in EVERY item, else some item shows what is below
    return paths, opaque


def below(path, roots):
    return any(path.startswith(r + ".") or path.startswith(r + "[]") for r in roots)


def diff(fixture, live):
    fp, fo = key_paths(fixture)
    lp, lo = key_paths(live)
    return sorted(p for p in fp - lp if not below(p, lo)), sorted(p for p in lp - fp if not below(p, fo))


def load(name):
    return json.loads((FIXTURES / name).read_text())


def uncompared(fixture, live):
    """Fixture paths that hold keys of their own but whose live value is null or empty: nothing below them was compared."""
    fp, _ = key_paths(fixture)
    _, lo = key_paths(live)
    return sorted(p for p in lo if p in fp and any(q.startswith(p + ".") or q.startswith(p + "[]") for q in fp))


def check(name, live, capsys, fails, graphql=False):
    """Appends a message to `fails` per problem. `graphql`: the live response comes from a query selecting the fixture's own
    fields, so it must hold no path the fixture lacks either."""
    fixture_only, live_only = diff(load(name), live)
    roots = [p for p in live_only if not below(p, live_only)]  # a new object is printed once, not once per key
    with capsys.disabled():
        print("\n[%s] fixture_only=%s\n[%s] live_only=%s" % (name, fixture_only, name, roots))
    if fixture_only != sorted(EXPECTED_FIXTURE_ONLY[name]):
        fails.append("%s: fixture-only keys %s, expected %s (a fixture key live does not have, or a known one that is gone)"
                     % (name, fixture_only, sorted(EXPECTED_FIXTURE_ONLY[name])))
    if graphql and live_only:
        fails.append("%s: live holds paths the fixture lacks: %s" % (name, roots))
    empty = uncompared(load(name), live)
    if empty != sorted(UNCOMPARED_OK.get(name, {})):
        fails.append("%s: live is null or empty where the fixture has keys, so they were not compared: %s (allowed: %s)"
                     % (name, empty, sorted(UNCOMPARED_OK.get(name, {}))))
    fx_paths, _ = key_paths(load(name))
    missing = [p for p in READ_BY_CODE.get(name, []) if p not in fx_paths]
    if missing:
        fails.append("%s: the code reads %s, which the fixture lacks" % (name, missing))


def with_milestone(gh):
    found = gh.rest("GET", REPO_PATH + "/issues?milestone=*&state=all&per_page=1")
    if not found:
        pytest.fail("no issue with a milestone in the scratch repo: the milestone shape cannot be compared")
    return found[0]["number"]


def test_fixture_keys_match_live(gh, scratch, capsys):
    fails = []
    story, sub, pr = scratch["story"], scratch["sub"], scratch["pr"]
    issue = lambda n: gh.rest("GET", "%s/issues/%d" % (REPO_PATH, n))
    check("issue_story.json", issue(with_milestone(gh)), capsys, fails)
    check("issue_task.json", issue(sub), capsys, fails)
    item = gh.rest("GET", "%s/issues/%d/sub_issues" % (REPO_PATH, story))[0]  # a sub_issues item is the issue shape plus `repository`
    got = issue(sub)
    assert item["parent_issue_url"] == got["parent_issue_url"] and item["repository_url"] == got["repository_url"]
    assert sorted(set(item) - set(got)) == ["repository"] and "parent_issue_url" not in issue(story)
    check("issue_pull_request.json", issue(pr), capsys, fails)
    check("comments_rest.json", gh.rest("GET", "%s/issues/%d/comments" % (REPO_PATH, story)), capsys, fails)
    node = issue(story)["node_id"]

    gql = lambda q, v: gh.graphql(q, v)
    comments_all = """query($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
      repository(owner: $owner, name: $repo) { issue(number: $number) { comments(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { id databaseId fullDatabaseId author { __typename login ... on User { databaseId } ... on Bot { databaseId } }
                authorAssociation body createdAt lastEditedAt includesCreatedEdit isMinimized minimizedReason
                userContentEdits(first: 1) { totalCount } viewerCanDelete viewerCanMinimize viewerCanUpdate viewerDidAuthor } } } } }"""
    check("comments_graphql.json", gql(comments_all, {"owner": OWNER, "repo": REPO, "number": story, "cursor": None}), capsys, fails, graphql=True)
    own = gql(comments._QUERY, {"owner": OWNER, "repo": REPO, "number": story, "cursor": None})
    assert not diff(load("comments_graphql.json"), own)[1], "the code's comments query returns a path the fixture lacks"

    pv = {"owner": OWNER, "number": 3, "cursor": None}
    check("project_fields.json", gql(projects.QUERIES["fields"], pv), capsys, fails, graphql=True)
    check("project_workflows.json", gql(projects.QUERIES["workflows"], pv), capsys, fails, graphql=True)
    check("project_items.json", gql(projects.QUERIES["items"], {"issue": node, "cursor": None}), capsys, fails, graphql=True)
    events_all = """query($issue: ID!, $cursor: String) { node(id: $issue) { ... on Issue {
      timelineItems(first: 100, after: $cursor, itemTypes: [PROJECT_V2_ITEM_STATUS_CHANGED_EVENT]) { pageInfo { hasNextPage endCursor }
        nodes { __typename ... on ProjectV2ItemStatusChangedEvent { actor { login } previousStatus status wasAutomated createdAt } } } } } }"""
    check("status_events.json", gql(events_all, {"issue": node, "cursor": None}), capsys, fails, graphql=True)
    own = gql(projects.QUERIES["events"], {"issue": node, "cursor": None})
    assert not diff(load("status_events.json"), own)[1], "the code's events query returns a path the fixture lacks"
    assert not fails, "\n".join(fails)
