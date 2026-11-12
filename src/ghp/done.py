"""`ghp done REF`: close a finished story. The only verb that may move a Project item to Done (D4, D5). A dry run unless `--apply`;
`plan.arm_or_refuse` is the only gate.

What it does, in this order (D5), each step only when it is still needed:
1. the ledger comment: a summary of `dod/DoD_<n>.md` plus a link to that file at the merge commit (no attachment call: GitHub has none for
   issues). It carries the hidden marker `<!-- spec-kit-ghp:done:<n> -->`; a second run finds the marker in a comment written by the same
   identity (exact numeric user id, so a copied marker from anyone else does not count) and posts nothing.
2. the close: `issues.close(..., "completed")`.
3. a bounded wait (30 s, one read every 3 s, reads only) for the Project's own "Item closed" workflow to move the item to Done.
4. the Status: `projects.set_status(..., allow_done=True)` only if the item still is not at the Done option (option ids compared).

Refused with nothing written (exit 2): an unticked DoD box (E_DOD_NOT_TICKED, also a ledger that cannot be read as ready), a strict-mode finding
(`strict.check(..., "done")` runs first, before any GitHub call; findings are printed as JSON), an open issue with no merged pull request
(E_NOT_MERGED), a closer that is a pull request which is not merged (E_PR_NOT_MERGED), two PRs merged at the same instant (E_PR_AMBIGUOUS).
Exit 3: an invalid config or a ledger with more than one `## 2.` heading (CI fails closed). Exit 1: a GitHub call failed or a step stopped
after others had run; the message says which, and that nothing is retried (a lost reply is re-read first, then the ORIGINAL error is kept).

`--pr N` (T26a): GitHub ignores `Closes` when a PR is merged into a branch other than the default one, and then lists no closing reference, so the
merge check above would refuse with E_NOT_MERGED. With `--pr N` the PR is read by GraphQL and must be a merged pull request of this repository
(not from a fork) whose node id is the source of a CrossReferencedEvent on the issue (that event is only a MENTION: GitHub makes it for any mention in
the PR's text, and also for a mention in a comment on it) and whose own title or body carries a closing keyword for THIS issue (the scanner of
`prs.closing_targets`, the one `pr create` uses). It becomes the merge, its merge commit the ledger link; the plan says `(given with --pr; GitHub did
not link it)`, shows the PR's title, merge date and author, and the digest covers all of them.

An issue that is already closed (by a PR, a commit, the board or a person) is recorded, not closed again; only the missing comment and a
Status that is not yet Done are written. `closer`, the merged PRs and the sub-issue counts come from ONE GraphQL query; the REST
`commit_id` and `state_reason` of an event are never read. A PR from another repository is not a closer here (D7).

Digest: the plan ends with `digest:` = sha256 of the exact data behind it (full comment text, ids, unticked boxes), so `--expect-plan`
covers what a display escapes or shortens. Everything printed passes `comments._show`; the comment posted has no live @mention.

VERIFIED live in T26 (docs/evidence/T26.md): the Project's own "Item closed" workflow moved the item to Done 0.3 to 0.4 s after the close (the 30 s
bound is a choice); `ClosedEvent.closer` is a PullRequest for a merge, squash and rebase merge into the default branch (after a rebase `mergeCommit` is
the last rebased commit) and a Commit, with `repository.nameWithOwner`, for a direct push; for a merge into a NON-default branch the issue stays open
and `closedByPullRequestsReferences` (and the PR's `closingIssuesReferences`) is empty, which is why `--pr N` exists; the hidden marker survives
byte-exact in an issue comment; the linked `dod/` file exists at the merge commit when the ledger was committed on the PR branch (the link is not
fetched); `workflows` returned six nodes `{id, name, enabled, number}` that include both closing workflows; a card moved to Done through the API closed
the issue (closer ProjectV2); a reopened issue's item stays at Done.
VERIFIED live in T26f (docs/evidence/T26f.md): `done --pr` on a PR merged into a non-default branch (one CrossReferencedEvent, `willCloseTarget:
false`; the plan says `given with --pr; GitHub did not link it`; the comment and the close were written, the Project moved the item to Done, and the
ClosedEvent then has `closer: null`) and on one merged into the default branch (only the comment was written); a rerun of each sends nothing; the
refusals E_PR_NOT_MERGED, E_PR_NOT_LINKED (a mention with no closing keyword, and no mention) and E_NOT_A_PR, each with nothing sent. For a number with
nothing behind it GitHub answers `issueOrPullRequest` with a GraphQL NOT_FOUND error, not null; T26g maps that error to E_PR_NOT_FOUND (its `type`
and `path` are GitHub's usual error shape: the live record kept only the message).
UNVERIFIED: the shape of a closer commit's `repository` in a fork network; the shape of `workflows` after a toggle in the UI; closing keywords in
squash commit messages when the repository's squash-message setting is not the default; E_PR_FORK, E_PR_FOREIGN and E_CLOSER_FOREIGN (they need a fork
or a second repository).
"""
import argparse
import hashlib
import json
import re
import sys
import urllib.parse

from . import approvals, comments, decisions, dod, gate, issues, plan as write_gate, prs, projects, strict
from .config import Config, ConfigError, ConfigInvalid
from .gh import GhError, scrub
from .refs import _MAX as MAX_ID

MARKER = "<!-- spec-kit-ghp:done:%d -->"
WAIT = 30  # seconds the Project's own workflow gets to move the item to Done
POLL = 3  # seconds between the reads of that wait
CLOSING_WORKFLOWS = ("Auto-close issue", "Item closed")  # the two built-in workflows that make a board drag to Done a close
WORKFLOW_WARNING = ("warning: the Project workflow '%s' is enabled: dragging a card to Done on the board closes its issue without the gate "
                    "(ghp done is the only gate). Turn it off in the Project's Workflows screen; the API cannot.")
UNCLOSED = "ledger has an unclosed code fence or comment"
MAX_SHOWN = 20  # unticked boxes listed in the plan (the digest covers all of them)
_OID = re.compile(r"[0-9a-f]{40}")
_BOX = re.compile(r"^[ \t>]*(?:[-*+]|\d+[.)])[ \t]+\[(.)\][ \t]*(.*)$", re.M)  # linear: anchored at a line start, every quantifier ends at a fixed character

_PR = "number merged mergedAt baseRefName mergeCommit { oid } baseRepository { nameWithOwner }"
GIVEN = "(given with --pr; GitHub did not link it)"
GIVEN_LINKED = "(given with --pr)"
QUERIES = {
    "facts": "query($issue: ID!) { node(id: $issue) { ... on Issue { subIssuesSummary { total completed } "
             "timelineItems(last: 1, itemTypes: [CLOSED_EVENT]) { nodes { ... on ClosedEvent { closer { __typename ... on PullRequest { " + _PR
             + " } ... on Commit { oid repository { nameWithOwner } } } } } } closedByPullRequestsReferences(first: 50, includeClosedPrs: true) { pageInfo { hasNextPage } nodes { "
             + _PR + " } } } } }",
    # --pr N: the pull request (or the issue, or nothing) with that number, and the issue's cross-reference events (GitHub's own link, kept even
    # when the base is not the default branch and `Closes` was ignored). One read, GraphQL only.
    "pr_link": "query($owner: String!, $name: String!, $number: Int!, $issue: ID!) { repository(owner: $owner, name: $name) { issueOrPullRequest(number: $number) "
               "{ __typename ... on PullRequest { id isCrossRepository title body " + _PR + " author { __typename login ... on User { databaseId } "
               "... on Bot { databaseId } ... on Organization { databaseId } ... on Mannequin { databaseId } } } } } node(id: $issue) { ... on Issue { timelineItems(first: 100, "
               "itemTypes: [CROSS_REFERENCED_EVENT]) { pageInfo { hasNextPage } nodes { ... on CrossReferencedEvent { source { __typename ... on PullRequest { id } } } } } } } }",
}


class DoneError(ValueError):
    """A named refusal (exit 2 in the CLI): the message starts with the code."""

    def __init__(self, code, detail):
        self.code = code
        super().__init__("%s: %s" % (code, detail))


def _cell(text):
    """Text from GitHub or the ledger on one line, whole and injective: backslashes are doubled BEFORE the display escapes, so an invisible
    character and the literal text of its escape differ (the digest line covers whatever a display still loses)."""
    return comments._show(str(text).replace("\\", "\\\\"))


def check(ledger_md):
    """{ready, unticked, boxes, acs}: the checkboxes of ledger section 5 outside code fences and comments. A ledger that cannot be read as ready is not
    ready (an unclosed fence hides what follows; no section or no box). dod.LedgerError (more than one `## 2.`) propagates: exit 3."""
    if dod.unclosed(ledger_md):
        return {"ready": False, "unticked": [UNCLOSED], "boxes": 0, "acs": 0}
    body = dod.section(ledger_md, 5)
    if body is None:
        return {"ready": False, "unticked": ["section 5 missing"], "boxes": 0, "acs": 0}
    boxes = _BOX.findall(dod._visible(body)[0])
    if not boxes:
        return {"ready": False, "unticked": ["section 5 has no checklist"], "boxes": 0, "acs": 0}
    unticked = [t.strip() for mark, t in boxes if mark not in ("x", "X")]
    return {"ready": not unticked, "unticked": unticked, "boxes": len(boxes), "acs": len(dod.ac_lines(ledger_md))}


# ---- reads -------------------------------------------------------------------------------------------------------------

def _bad(what):
    return GhError("E_GH_BAD_BODY", None, "unexpected done response: " + what)


def _pull(d):
    """One PR of the facts query: {number, merged, oid, at, repo}; a shape that is not that is a bad body."""
    if not isinstance(d, dict) or type(d.get("number")) is not int or d["number"] < 1 or type(d.get("merged")) is not bool:
        raise _bad("a pull request has no number and merged flag")
    commit, repo, at, base = d.get("mergeCommit"), d.get("baseRepository"), d.get("mergedAt"), d.get("baseRefName")
    if not all(x is None or isinstance(x, dict) for x in (commit, repo)):
        raise _bad("a pull request has a merge commit or repository that is not an object")
    oid, name = commit and commit.get("oid"), repo and repo.get("nameWithOwner")
    if (oid is not None and not (isinstance(oid, str) and _OID.fullmatch(oid))) or not all(x is None or isinstance(x, str) for x in (name, at)):
        raise _bad("a pull request has a bad merge commit id, repository name or merge time")
    if d["merged"] and oid is None:
        raise _bad("a merged pull request has no merge commit")
    if not isinstance(base, str) or not base:
        raise _bad("a pull request has no base branch name")
    return {"number": d["number"], "merged": d["merged"], "oid": oid, "at": at or "", "repo": name, "base": base}


def _closer(node):
    """{kind: pr | commit | board | none, ...} of the newest ClosedEvent (GraphQL only)."""
    events = node.get("timelineItems")
    events = events.get("nodes") if isinstance(events, dict) else None
    if not isinstance(events, list) or len(events) > 1:
        raise _bad("the closed-event list is not a list of at most one")
    closer = events[0].get("closer") if events and isinstance(events[0], dict) else None
    if events and not isinstance(events[0], dict):
        raise _bad("a closed event is not an object")
    if closer is None:
        return {"kind": "none"}
    kind = closer.get("__typename") if isinstance(closer, dict) else None
    if kind == "PullRequest":
        return {"kind": "pr", "pr": _pull(closer)}
    if kind == "Commit":
        if not (isinstance(closer.get("oid"), str) and _OID.fullmatch(closer["oid"])):
            raise _bad("a closing commit has no id")
        repo = closer.get("repository")
        name = repo.get("nameWithOwner") if isinstance(repo, dict) else None
        if repo is not None and not isinstance(name, str):
            raise _bad("a closing commit has a repository without a name")
        return {"kind": "commit", "oid": closer["oid"], "repo": name}
    if kind == "ProjectV2":
        return {"kind": "board"}
    raise _bad("the closer is not a pull request, a commit or a Project")


def facts(gh, issue):
    """{closer, linked, subs}: from one GraphQL query. `closer` is the newest ClosedEvent's (meaningful only when the issue is closed), `linked`
    the pull requests GitHub ties to the issue (merged or not), `subs` = (total, completed) sub-issues."""
    node = gh.graphql(QUERIES["facts"], {"issue": issue.node_id})
    node = node.get("node") if isinstance(node, dict) else None
    if not isinstance(node, dict):
        raise _bad("the issue was not returned")
    summary = node.get("subIssuesSummary")
    if not isinstance(summary, dict) or type(summary.get("total")) is not int or type(summary.get("completed")) is not int:
        raise _bad("the sub-issue summary has no counts")
    refs = node.get("closedByPullRequestsReferences")
    nodes, info = (refs.get("nodes"), refs.get("pageInfo")) if isinstance(refs, dict) else (None, None)
    if not isinstance(nodes, list) or not isinstance(info, dict) or type(info.get("hasNextPage")) is not bool:
        raise _bad("the linked pull requests have no nodes and pageInfo")
    if info["hasNextPage"]:
        raise DoneError("E_TOO_MANY_PRS", "more than 50 pull requests are linked to this issue; ghp cannot tell which one merged it; nothing was sent")
    return {"closer": _closer(node), "linked": [_pull(x) for x in nodes], "subs": (summary["total"], summary["completed"])}


def _own(cfg, pull):
    return pull["repo"] is not None and issues._fold(pull["repo"]) == "%s/%s" % (issues._fold(cfg.owner), issues._fold(cfg.repo))


def _merge(pull):
    return {"pr": pull["number"], "oid": pull["oid"], "base": pull["base"]}


def merge_of(cfg, issue, f, given=None):
    """({pr, oid, base} | None, how the issue was closed). The merge check (AC6): an open issue needs a merged PR of this repository; a closer that is a
    PR of this repository must be merged; a closer commit of another repository is refused (E_CLOSER_FOREIGN). `closer` is read only for a closed issue (an open one may carry the event of an earlier close).
    `given`: the PR named with `--pr` and already checked (`given_pr`); it is the merge, whatever GitHub links, but the closer checks above still apply."""
    closer = f["closer"] if issue.state == "closed" else {"kind": "none"}
    if closer["kind"] == "pr" and not _own(cfg, closer["pr"]):
        closer = {"kind": "none"}  # D7: a PR of another repository says nothing about this one
    if closer["kind"] == "pr":
        if not closer["pr"]["merged"]:
            raise DoneError("E_PR_NOT_MERGED", "#%d was closed by PR #%d, which is not merged; nothing was sent" % (issue.ref.number, closer["pr"]["number"]))
        merge, how = _merge(closer["pr"]), "PR #%d" % closer["pr"]["number"]
    elif closer["kind"] == "commit":
        if not _own(cfg, closer):  # D7, as for a PR: a commit of another repository (or a fork) must not become this story's ledger link
            raise DoneError("E_CLOSER_FOREIGN", "#%d was closed by a commit that is not in %s/%s; nothing was sent" % (
                issue.ref.number, _cell(cfg.owner), _cell(cfg.repo)))
        merge, how = {"pr": None, "oid": closer["oid"], "base": None}, "commit " + closer["oid"][:12]
    else:
        how = {"board": "the Project board", "none": "a person or an unknown route"}[closer["kind"]]
        merged = [p for p in f["linked"] if p["merged"] and _own(cfg, p)]
        top = max((p["at"] for p in merged), default=None)
        latest = [p for p in merged if p["at"] == top]
        if given is None and len({p["oid"] for p in latest}) > 1:
            raise DoneError("E_PR_AMBIGUOUS", "PRs %s were merged at the same instant; ghp cannot tell which one carries the ledger; nothing was sent"
                            % ", ".join("#%d" % p["number"] for p in latest))
        if not latest:
            if issue.state == "open" and given is None:
                raise DoneError("E_NOT_MERGED", "no merged pull request of this repository is tied to #%d; merge the PR first, or, if it was merged into a "
                                "branch other than the default one (GitHub ignores `Closes` there), name it with `--pr N` (nothing was sent)" % issue.ref.number)
            merge = None
        else:
            merge = _merge(latest[0])
        how = how if issue.state == "closed" else "ghp"
    return (merge if given is None else _merge(given)), how


def _pr_number(text):
    """The value of --pr: a plain positive integer in ASCII digits (not 0, not signed, not bool-like, not above the largest issue number)."""
    if not re.fullmatch(r"[0-9]{1,10}", text) or not 0 < int(text) <= MAX_ID:
        raise argparse.ArgumentTypeError("%s is not a pull request number (a positive whole number)" % comments._show(text, 40))
    return int(text)


class _Once(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest, None) is not None:
            parser.error("%s was given twice; name one pull request" % option_string)
        setattr(namespace, self.dest, values)


def given_pr(gh, cfg, issue, number):
    """The PR named with --pr as `_pull` reads it. Refused, nothing sent: a number that is no PR (E_PR_NOT_FOUND, E_NOT_A_PR), a PR of another
    repository or from a fork (E_PR_FOREIGN, E_PR_FORK), one that is not merged (E_PR_NOT_MERGED), one the issue's timeline does not show as a reference
    (E_PR_NOT_LINKED: the issue's timeline holds no mention from this PR, compared by the PR's node id so a PR of the same number in another repository
    does not count, or the PR's own title and body carry no closing keyword for this issue; E_TOO_MANY_REFS: the first 100 events do not hold it and more
    exist). A CrossReferencedEvent proves only a mention (anyone can make one with a comment), so the keyword in the PR's own text, written by someone
    with push access (fork PRs are refused), is what ties the PR to the story. The other done paths check no author, only that the PR's base repository
    is this one (D7); the same rule is used here, and the plan shows the PR's title, merge date and author for the human gate."""
    try:
        data = gh.graphql(QUERIES["pr_link"], {"owner": cfg.owner, "name": cfg.repo, "number": number, "issue": issue.node_id})
    except GhError as e:  # live (T26f), GitHub answers a missing number with this error, not a null
        if e.code == "E_GH_GRAPHQL" and e.graphql_errors and all(x == ("NOT_FOUND", ("repository", "issueOrPullRequest")) for x in e.graphql_errors):
            raise DoneError("E_PR_NOT_FOUND", "there is no pull request #%d in %s/%s; nothing was sent" % (number, _cell(cfg.owner), _cell(cfg.repo))) from None
        raise
    repo, node = (data.get("repository"), data.get("node")) if isinstance(data, dict) else (None, None)
    if not isinstance(repo, dict) or not isinstance(node, dict):
        raise _bad("the pull request and the issue's references were not returned")
    d = repo.get("issueOrPullRequest")
    if d is None:
        raise DoneError("E_PR_NOT_FOUND", "there is no pull request #%d in %s/%s; nothing was sent" % (number, _cell(cfg.owner), _cell(cfg.repo)))
    if not isinstance(d, dict) or not isinstance(d.get("__typename"), str):
        raise _bad("the pull request has no type")
    if d["__typename"] != "PullRequest":
        raise DoneError("E_NOT_A_PR", "#%d is not a pull request (it is %s); nothing was sent" % (number, _cell(d["__typename"])))
    pid, cross = d.get("id"), d.get("isCrossRepository")
    if not (isinstance(pid, str) and pid) or type(cross) is not bool:
        raise _bad("the pull request has no id and fork flag")
    title, body, author = d.get("title"), d.get("body"), d.get("author")
    if not isinstance(title, str) or not (body is None or isinstance(body, str)) or not (author is None or isinstance(author, dict)):
        raise _bad("the pull request has no title, or a body or author of the wrong type")
    login = author.get("login") if author else None
    who = author.get("databaseId") if author else None
    if author and (not isinstance(login, str) or not (who is None or (type(who) is int and who > 0))):
        raise _bad("the pull request's author has no login, or an id that is not a number")
    pr = {**_pull(d), "title": title, "login": login, "author_id": who}
    timeline = node.get("timelineItems")
    nodes, info = (timeline.get("nodes"), timeline.get("pageInfo")) if isinstance(timeline, dict) else (None, None)
    if not isinstance(nodes, list) or not isinstance(info, dict) or type(info.get("hasNextPage")) is not bool:
        raise _bad("the issue's references have no nodes and pageInfo")
    if not all(isinstance(x, dict) and isinstance(x.get("source"), (dict, type(None))) for x in nodes):
        raise _bad("a reference is not an object")
    if not _own(cfg, pr):
        raise DoneError("E_PR_FOREIGN", "PR #%d is not a pull request of %s/%s; nothing was sent" % (number, _cell(cfg.owner), _cell(cfg.repo)))
    if cross:
        raise DoneError("E_PR_FORK", "PR #%d comes from a fork; ghp does not trust it to finish a story; nothing was sent" % number)
    if not pr["merged"]:
        raise DoneError("E_PR_NOT_MERGED", "PR #%d is not merged; nothing was sent" % number)
    if not any(isinstance(x["source"], dict) and x["source"].get("__typename") == "PullRequest" and x["source"].get("id") == pid for x in nodes):
        if info["hasNextPage"]:
            raise DoneError("E_TOO_MANY_REFS", "#%d has more than 100 references; ghp cannot confirm that PR #%d is linked to it; nothing was sent" % (
                issue.ref.number, number))
        raise DoneError("E_PR_NOT_LINKED", "GitHub shows no mention of #%d from PR #%d; nothing was sent" % (issue.ref.number, number))
    mine = (issues._fold(cfg.owner), issues._fold(cfg.repo), issue.ref.number)
    if not any((issues._fold(o), issues._fold(r), n) == mine for text in (title, body or "") for o, r, n in prs.closing_targets(text, cfg.owner, cfg.repo)):
        raise DoneError("E_PR_NOT_LINKED", "the title and body of PR #%d carry no closing keyword for #%d (such as `Closes #%d`); a mention is not enough; "
                        "edit the PR text, or close the story by hand; nothing was sent" % (number, issue.ref.number, issue.ref.number))
    return pr


def find_ledger_comment(gh, ref):
    """(id of the lowest comment that carries this story's marker and was written by the identity running ghp, number of comments dropped by the
    join of the two APIs). Identity is the exact integer user id (the viewer read, `Gh.viewer`, is asked only when a marker is present)."""
    found = comments.fetch(gh, ref)
    marker = MARKER % ref.number
    mine = [c for c in found if marker in c.body]
    if mine:
        me = gh.viewer().id
        mine = [c for c in mine if type(me) is int and type(c.author.id) is int and c.author.id == me]
    return (min(c.id for c in mine) if mine else None), len(found.notes)


# ---- the plan ----------------------------------------------------------------------------------------------------------

def _link(cfg, number, merge):
    path = dod.dod_file(dod.safe_dir(cfg.dod_path), number).as_posix()
    if merge is None or merge["oid"] is None:
        return "**Ledger:** no merge commit was found for #%d; the ledger is `%s` in the repository." % (number, comments._show(path.replace("`", "")))
    url = "https://github.com/%s/%s/blob/%s/%s" % (cfg.owner, cfg.repo, merge["oid"], urllib.parse.quote(path, safe="/"))
    return "**Ledger:** [%s](%s) at merge commit `%s`%s" % (
        "DoD_%d.md" % number, url, merge["oid"][:12], "" if merge["pr"] is None else " (PR #%d)" % merge["pr"])


def summary(cfg, number, chk):
    """The ledger counts, from the SAME `check` result the gate used (one reading of the checklist: the comment cannot say 0 of 0 where the gate saw
    ready). No ledger text is copied."""
    shown = comments._MENTION.sub(comments.NO_MENTION, comments._show(dod.dod_file(dod.safe_dir(cfg.dod_path), number), 200))
    return "**Ledger summary** (%s): %d acceptance criteria; DoD checklist %d of %d ticked." % (
        shown, chk["acs"], chk["boxes"] - len(chk["unticked"]), chk["boxes"])


def comment_text(cfg, number, merge, closed_by, chk):
    """The ledger comment: counts from the ledger (no ledger text), a link to the file at the merge commit, the hidden marker. No @mention can
    survive in it: the one user-controlled part, the ledger path, is shown escaped, percent-encoded in the link, and the whole is defanged."""
    lines = ["**DoD complete for #%d.**" % number, summary(cfg, number, chk), _link(cfg, number, merge)]
    if closed_by is not None:
        lines.append("**Closed by:** " + closed_by)
    text = comments._MENTION.sub(comments.NO_MENTION, "\n\n".join(lines)) + "\n\n" + MARKER % number
    if len(text) > comments.MAX_COMMENT:
        raise DoneError("E_COMMENT_TOO_LONG", "the ledger comment would be %d characters; nothing was sent" % len(text))
    return text


def _completed(issue):
    """A closed issue is done only if it was closed as completed (a not_planned or duplicate close, or none recorded, is not a finished story)."""
    if issue.state == "closed" and issue.state_reason != "completed":
        raise DoneError("E_CLOSED_NOT_COMPLETED", "#%d is closed as %s, not as completed; ghp done moves only a story closed as completed (reopen it, or "
                        "close it as completed); nothing was sent" % (issue.ref.number, _cell(issue.state_reason)))


def _still(now, closed):
    """The issue as read just before a write: `closed` says it must be closed now (it was at the plan, or ghp closed it). Raises DoneError."""
    _completed(now)
    if closed and now.state != "closed":
        raise DoneError("E_REOPENED", "#%d is not closed any more (it was reopened since the plan); nothing more was sent" % now.ref.number)


def plan_done(gh, cfg, issue, chk, pr=None):
    """The plan, read only. Raises DoneError for the merge check; GhError for a read that failed. `pr`: the number given with `--pr`."""
    ref = issue.ref
    _completed(issue)
    f = facts(gh, issue)
    given = None if pr is None else given_pr(gh, cfg, issue, pr)
    merge, how = merge_of(cfg, issue, f, given)
    closed = issue.state == "closed"
    comment_id, dropped = find_ledger_comment(gh, ref)
    closer_pr = f["closer"].get("pr") if closed else None  # an open issue may carry the event of an earlier close
    linked = given is not None and any(x["number"] == pr and _own(cfg, x) for x in f["linked"] + ([closer_pr] if closer_pr else []))
    p = {"issue": issue, "chk": chk, "merge": merge, "how": how if closed else None, "closed": closed, "subs": f["subs"], "dropped": dropped,
         "comment": comment_text(cfg, ref.number, merge, how if closed else None, chk), "comment_id": comment_id,
         "board": None, "item_id": None, "current": None, "to": cfg.status_done, "to_id": None, "project_id": None, "status": "no-project", "warned": [],
         "given": None if given is None else [pr, linked, given["title"], given["at"], given["login"], given["author_id"]]}
    if cfg.project_number is not None:
        board = p["board"] = projects.discover(gh, cfg)
        p.update(to_id=board.options[cfg.status_done], project_id=board.project_id, item_id=projects.item_for(gh, board, issue.node_id))
        p["warned"] = [w.name for w in projects.workflows(gh, cfg) if w.enabled and w.name in CLOSING_WORKFLOWS]
        if p["item_id"] is None:
            p["status"] = "off-board"
        else:
            p["current"] = projects._status_of(gh, p["item_id"])
            p["status"] = "done" if p["current"] is not None and p["current"][1] == p["to_id"] else "set"
    p["noop"] = comment_id is not None and closed and p["status"] != "set"
    return p


def _digest(p):
    """SHA-256 of the exact data behind the plan (not its rendering), so --expect-plan covers it even where a display is lossy."""
    i = p["issue"]
    row = [i.ref.number, i.id, i.node_id, i.title, i.state, i.state_reason, p["chk"], p["merge"], p["how"], p["comment"], p["comment_id"], p["subs"],
           p["dropped"], p["item_id"], p["current"], p["to"], p["to_id"], p["project_id"], p["status"], p["warned"], p["given"]]
    return hashlib.sha256(json.dumps(row, ensure_ascii=True).encode("ascii")).hexdigest()


def render_not_ready(ref, chk):
    shown = chk["unticked"][:MAX_SHOWN]
    lines = ["# Plan: done issue #%d" % ref.number, "", "DoD checklist: NOT READY, %d unticked: nothing will be sent, even with --apply" % len(chk["unticked"])]
    lines += ["- " + _cell(u) for u in shown] + (["... and %d more" % (len(chk["unticked"]) - len(shown))] if len(chk["unticked"]) > len(shown) else [])
    digest = hashlib.sha256(json.dumps([ref.number, chk], ensure_ascii=True).encode("ascii")).hexdigest()
    return "\n".join(lines + ["", "digest: " + digest]) + "\n"


def render_plan(p):
    i, n = p["issue"], p["issue"].ref.number
    m = p["merge"]
    lines = ["# Plan: done issue #%d" % n, "", "Issue: #%d %s" % (n, _cell(i.title)),
             "DoD checklist: ready, %d of %d ticked" % (p["chk"]["boxes"], p["chk"]["boxes"]),
             "Merge: " + ("none found (the issue is already closed)" if m is None else "PR #%d into %s, commit %s" % (m["pr"], _cell(m["base"]), m["oid"][:12]) if m["pr"] is not None
                          else "commit %s" % m["oid"][:12])
             + ("" if p["given"] is None else " " + (GIVEN_LINKED if p["given"][1] else GIVEN))]
    if p["given"] is not None:  # the human gate sees WHICH pull request was named: its title, merge time and author (all in the digest)
        number, _, title, at, login, who = p["given"]
        lines.append("Pull request #%d: %s, merged %s, by %s" % (number, _cell(title), _cell(at), "(no author)" if login is None else "%s (%s)" % (
            _cell(login), "no id" if who is None else "id %d" % who)))
    if p["noop"]:
        return "\n".join(lines + ["", "Action: nothing to do: the ledger comment is posted (comment %d), the issue is closed (%s) and the item is at %s"
                                  % (p["comment_id"], _cell(i.state_reason), "Done" if p["status"] == "done" else "no Project item"),
                                  "", "digest: " + _digest(p)]) + "\n"
    lines.append("Step 1: " + ("post the ledger comment (below)" if p["comment_id"] is None else
                               "skip: the ledger comment is already posted (comment %d)" % p["comment_id"]))
    lines.append("Step 2: " + ("close #%d as completed" % n if not p["closed"] else
                               "skip: already closed (%s) by %s; recorded, not closed again" % (_cell(i.state_reason), _cell(p["how"]))))
    now = "(not on the Project)" if p["item_id"] is None else "(none)" if p["current"] is None else _cell(p["current"][0])
    to = _cell(p["to"])
    lines.append("Step 3: " + {"no-project": "skip: no Project is configured (project_number)",
                               "off-board": "skip: #%d is not on the Project; ghp does not add it" % n,
                               "done": "skip: the Project item is already at %s" % to,
                               "set": ("wait up to %d s (reads only) for the Project's own workflow to move the item to %s, then set Status to %s "
                                       "if it is still not there" % (WAIT, to, to)) if not p["closed"] else
                               "set Status to %s if it is still not there (the issue is already closed)" % to}[p["status"]])
    lines.append("Status: %s -> %s" % (now, to) if p["status"] == "set" else "Status: %s" % now)
    if p["comment_id"] is None:
        lines += ["", "Ledger comment:"] + ["> " + _cell(x) if x else ">" for x in p["comment"].split("\n")]
    for name in p["warned"]:
        lines += ["", WORKFLOW_WARNING % _cell(name)]
    if p["subs"][0] > p["subs"][1]:
        lines += ["", "warning: %d of %d sub-issues are still open; done does not close them" % (p["subs"][0] - p["subs"][1], p["subs"][0])]
    if p["dropped"]:
        lines += ["", "warning: %d comment(s) could not be verified (the two APIs disagree about them) and were ignored when looking for the ledger comment"
                  % p["dropped"]]
    return "\n".join(lines + ["", "digest: " + _digest(p)]) + "\n"


# ---- the writes --------------------------------------------------------------------------------------------------------

def _nap(seconds):
    from .issues import _nap as nap  # tests patch issues._nap once for every wait
    nap(seconds)


def _at_done(gh, item, done_id, patient, sleep):
    """True once the item reads as the Done option. Reads only: one read, or (patient) one every POLL s for up to WAIT s."""
    for k in range(1 + (WAIT // POLL if patient else 0)):
        if k:
            sleep(POLL)
        now = projects._status_of(gh, item)
        if now is not None and now[1] == done_id:
            return True
    return False


class _Spy:
    """`gh` that notes whether a PATCH was sent: `issues.close` returns an issue someone else closed just before as it is, and only the caller can
    tell whether ghp closed it (so the 30 s wait and the "closed" line are for our own close only)."""

    def __init__(self, gh):
        self._gh, self.patched = gh, False

    def rest(self, method, path, body=None):
        self.patched = self.patched or str(method).upper() == "PATCH"
        return self._gh.rest(method, path, body)

    def __getattr__(self, name):
        return getattr(self._gh, name)


def apply_done(gh, cfg, p, sleep=None):
    """Do the plan: {commented, closed, status, error, unknown}. Guards come first and raise (nothing sent); a write failure is reported in the
    result so that the caller learns what already happened. One failed step ends the run; nothing is retried."""
    if not p["chk"]["ready"]:
        raise DoneError("E_DOD_NOT_TICKED", "the DoD checklist is not complete; nothing was sent")
    issue, ref = p["issue"], p["issue"].ref
    sleep = sleep or _nap
    res = {"commented": None, "closed": False, "status": None, "error": None, "unknown": False}
    try:
        _still(issues.get(gh, ref), p["closed"])  # the plan is older than every write: look at the issue again, before the first one
        if p["comment_id"] is None:
            cid = find_ledger_comment(gh, ref)[0]  # the plan is older than this write: look again
            if cid is None:
                try:
                    res["commented"] = comments.add(gh, ref, p["comment"])
                except GhError as e:
                    if not issues.maybe_applied(e) or issues.reread(e, lambda: find_ledger_comment(gh, ref)[0]) is None:
                        raise
                    res["commented"] = -1  # applied, and found by its marker
        if not p["closed"]:
            now = issues.get(gh, ref)
            _still(now, False)
            if now.state == "open":
                spy = _Spy(gh)
                issues.close(spy, now, "completed")
                res["closed"] = spy.patched
        if p["status"] == "set":
            item, done_id = p["item_id"], p["to_id"]
            if _at_done(gh, item, done_id, res["closed"], sleep):
                res["status"] = "workflow"
            else:
                _still(issues.get(gh, ref), True)  # Done only on an issue that is closed as completed right now
                projects.set_status(gh, p["board"], item, cfg.status_done, allow_done=True)
                res["status"] = "set"
    except (GhError, ValueError) as e:
        res["error"], res["unknown"] = comments._show(e, 300), isinstance(e, GhError) and issues.maybe_applied(e)
    return res


def render_result(p, res):
    n = p["issue"].ref.number
    if res["error"] is None:
        lines = []
        if res["commented"] is not None:
            lines.append("posted the ledger comment")
        if res["closed"]:
            lines.append("closed #%d as completed" % n)
        if res["status"]:
            lines.append("Status is %s (%s)" % (_cell(p["to"]), "moved by the Project's own workflow" if res["status"] == "workflow" else "set by ghp"))
        return "\n".join(lines) or "nothing sent: already done"
    lines = ["done stopped: " + res["error"]]
    for k, text in (("commented", "the ledger comment was posted"), ("closed", "#%d was closed" % n), ("status", "the Status was set")):
        if res[k]:
            lines.append(text)
    lines.append("  " + ("outcome unknown: the write may have been applied; do not repeat it by hand, re-run ghp done (it reads first)" if res["unknown"]
                         else "nothing was retried; fix the cause and re-run ghp done, which reads first"))
    return "\n".join(lines)


# ---- the verb ----------------------------------------------------------------------------------------------------------

def register(sub):
    p = sub.add_parser("done", help="close a finished story: ledger comment, close, Project Status Done (dry run unless --apply)")
    p.add_argument("ref", help="the story: 5, #5, owner/repo#5 or an issue URL")
    p.add_argument("--pr", metavar="N", type=_pr_number, action=_Once, default=None,
                   help="the merged pull request of this repository that finished the story, when GitHub did not link it (merged into a branch other than the "
                        "default one); it must be merged, from this repository, and referenced from the issue")
    write_gate.add_write_flags(p)


def _ledger(cfg, ref):
    path = dod.dod_file(dod.safe_dir(cfg.dod_path), ref.number)
    md = gate._read(path, ".", set()) if path.exists() or path.is_symlink() else None  # confined like the gate: no symlink, <= 1 MiB, inside the tree
    if md is None:
        raise DoneError("E_NO_LEDGER", "cannot read the ledger %s (missing, a symlink, outside the working tree, too large or not UTF-8); run `ghp dod %d` first"
                        % (_cell(path), ref.number))
    return md


E_DECISIONS_FILE = ("the decisions file is not a regular file inside the working tree (a symlink, outside it, a directory, over 1 MiB or not UTF-8); "
                    "refusing to judge it")


def _xcheck(gh, cfg, ref):
    """(findings, blocking) of done's cross-check (ported from ado, T28): none unless the TARGET branch (origin/HEAD, `approvals.target_trust`) names
    approvers. It blocks when this config or the target's is strict: a PR that sets `mode: advisory` in the same change must not turn its own cross-check
    into a warning. The decisions file is read confined; one that exists and cannot be read is refused (exit 2)."""
    import os
    trust = approvals.target_trust(".")
    if not trust.approvers:
        return [], False
    home = strict.default_base(".")
    target = Config.load_from_ref(home, repo=".") if home else None
    blocking = cfg.mode == "strict" or (target is not None and target.mode == "strict")
    dec = decisions.decisions_file(cfg.dod_path, ref.number)
    md = gate._read(dec, ".", set()) if os.path.lexists(dec) else ""
    if md is None:
        raise ValueError(E_DECISIONS_FILE)
    return strict.xcheck_run(gh, ref, md, trust.approvers), blocking


def _run(args, gh):
    ref = issues._resolve(args.ref)  # config, then the ref: a bad one is refused before any call
    cfg = Config.load()
    used = strict.require(strict.check(gh, cfg, ref.number, "done"))["overrides_used"] if cfg.mode == "strict" else []  # first: no call before it
    xf, blocking = _xcheck(gh, cfg, ref)
    if xf and blocking:
        raise strict.Refused({"phase": "done", "mode": "strict", "ok": False, "findings": xf, "overrides_used": []})
    for f in xf:  # advisory: shown, never a stop
        print("warning: %s: %s" % (f["rule"], f["detail"]), file=sys.stderr)
    chk = check(_ledger(cfg, ref))
    if not chk["ready"]:  # no GitHub call at all
        text = render_not_ready(ref, chk)
        if not args.apply:
            write_gate.arm_or_refuse(args, gh, text)
            print(text)
            return 0
        print(text)
        raise DoneError("E_DOD_NOT_TICKED", "%d DoD box(es) are not ticked (or the ledger is unreadable); nothing was sent" % len(chk["unticked"]))
    issue = issues.get(gh, ref)  # NotAnIssue is a ValueError: exit 2
    p = plan_done(gh, cfg, issue, chk, getattr(args, "pr", None))
    text = render_plan(p)
    write_gate.arm_or_refuse(args, gh, text, approvals.approve_hook(gh, ref, "done", text, not p["noop"]))
    print(text)
    if not args.apply:
        return 0
    if p["noop"]:
        print("nothing sent: already done")
        return 0
    if used:  # strict: stamp `used` on the overrides the check relied on; a failed write is a refusal with nothing sent
        decisions.apply_stamp(decisions.decisions_file(cfg.dod_path, ref.number), ref.number, used)
    res = apply_done(gh, cfg, p)
    print(render_result(p, res))
    return 0 if res["error"] is None else 1


def run(args, gh):
    """Exit 0 done or previewed; 2 refused with nothing written (a strict finding prints its JSON first); 3 an invalid config or an ambiguous
    ledger (CI fails closed); 1 (via the CLI) a GitHub call failed or a step stopped."""
    try:
        return _run(args, gh)
    except (ConfigError, ValueError) as e:
        if isinstance(e, strict.Refused):
            print(json.dumps(e.check, indent=2))
        print("error: " + comments._show(" ".join(scrub(e).split()), 500), file=sys.stderr)
        return 3 if isinstance(e, (ConfigInvalid, dod.LedgerError)) else 2
