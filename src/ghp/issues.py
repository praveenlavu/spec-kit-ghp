"""Issue reads: `get`, `children`, `parent_chain`. REST only, so a `repo`-only token works: no call here selects project
data (a GraphQL query that merely names a project field fails as a whole without `read:project`, SPIKE Q3).

Which call supplies which field (REST shapes verified live 2026-11-09 (T13, docs/evidence/T13.md)):
- `GET repos/{o}/{r}/issues/{n}`: id, node_id, number, title, body, state, state_reason, labels[].name,
  milestone.title, user.{id,login,type}, html_url, `sub_issues_summary.{total,completed}` (sub_total, sub_done),
  `parent_issue_url` (parent; null or absent = no parent). A `pull_request` key means the number is a PR: NotAnIssue.
- `GET repos/{o}/{r}/issues/{n}/sub_issues?per_page=100&page=K`: the same issue shape per child. The child's ref comes
  from its `repository_url` (a child may live in another repo of the same owner) and `number`; `id` is the integer
  sub-issue calls take, never the number.
The body is the raw Markdown string exactly as GitHub returned it (CRLF, trailing whitespace and newlines kept).
Still UNVERIFIED live: that a `repo`-only token works for these reads (T13 ran with a `project`-scope token), and what GitHub's web
editor does to a typed or pasted body (the API stores it verbatim).
A child's `repository_url` must be the api.github.com/repos/{o}/{r} form (anything else is bad body).
The verb reads the configured repo only: a ref to another repo is refused (E_FOREIGN_REPO).
A response of an unexpected shape raises GhError("E_GH_BAD_BODY"), never KeyError or TypeError.

WRITES (`create`, `add_sub_issue`, `close`): primitives for the batch verbs, which arm `Gh` only after the human gate (D3).
Each raises WriteNotArmed with no call at all while `Gh` is unarmed, and `Gh` never repeats a write. After a failed write that
`maybe_applied` (timeout, no response, 5xx, bad body) each one RE-READS before it gives up, so nothing is ever sent twice. If
that re-read fails too, the ORIGINAL write error is raised (chained from the read error), so `maybe_applied` of what the caller
gets stays true and nothing is concluded from a read that did not happen.
REST shapes (SPIKE-RESULTS, live 2026-11-09; the write verbs re-checked live in T20, docs/evidence/T20.md): create `POST issues {title, body, labels, milestone, parent_issue_id}` (the
parent's integer `id`) -> 201; `POST issues/{n}/sub_issues {sub_issue_id: <integer id>}` (the number gives 404); close
`PATCH issues/{n} {state: "closed", state_reason}`. Verified live in T20: the reply of the
sub-issue POST is the PARENT issue (never parsed); a label that does not exist is CREATED by GitHub when the token has push access (the
issue carries it); a repeated sub-issue POST for a child already linked is HTTP 422 (the `message` was captured, the `errors[]` array was
not; `add_sub_issue` treats it as done only when the parent's children list shows the child); the newest-issues list lags a fresh issue by
about 2 to 4+ s (one sample per run, timed from polling start, T20 review I1), hence the bounded wait-and-reread in `create` (T20a); the lag
of the sub-issue list was not measured. UNVERIFIED live: the E_LABEL_DROPPED branch (a token WITHOUT push access: documented as dropped,
`create` reads back and raises either way when the stored labels are not exactly the asked ones).
"""
import dataclasses
import json
import re
import time
import uuid
from typing import List, Optional

from ghp.config import Config
from ghp.gh import UNKNOWN_OUTCOME, GhError, WriteNotArmed
from ghp.refs import _OWNER, _REPO, Ref, RefError, parse_ref

PAGE = 100
MAX_PAGES = 50  # a parent holds at most 100 sub-issues, so this only stops a server that never ends a list
_API_REPO = re.compile(r"https://api\.github\.com/repos/(%s)/(%s)" % (_OWNER, _REPO))  # ASCII GitHub names only
_API_ISSUE = re.compile(r"https://api\.github\.com/repos/(%s)/(%s)/issues/([1-9][0-9]*)" % (_OWNER, _REPO))


class NotAnIssue(ValueError):
    """The number is a pull request. Exit 2 in the CLI (a ValueError)."""
    code = "E_NOT_AN_ISSUE"

    def __init__(self, ref):
        super().__init__("%s: %s is a pull request, not an issue" % (self.code, ref))


@dataclasses.dataclass(frozen=True)
class Actor:
    id: int
    login: str
    type: str


@dataclasses.dataclass(frozen=True)
class Issue:
    ref: Ref
    node_id: str
    id: int
    title: str
    body: str
    state: str
    state_reason: Optional[str]
    labels: List[str]
    milestone: Optional[str]  # the milestone title
    author: Actor
    parent: Optional[Ref]
    sub_total: int
    sub_done: int
    url: str


def _bad(what):
    return GhError("E_GH_BAD_BODY", None, "unexpected issue response: " + what)


def _int(d, key):
    v = d.get(key)
    if type(v) is not int:
        raise _bad("'%s' is not an integer" % key)
    return v


def _str(d, key, optional=False):
    v = d.get(key)
    if isinstance(v, str) or (optional and v is None):
        return v
    raise _bad("'%s' is not a string" % key)


def _dict(v, what):
    if not isinstance(v, dict):
        raise _bad("%s is not an object" % what)
    return v


def _issue(raw, ref):
    """Map one REST issue. `ref` is the expected ref (get) or None (a child: built from repository_url + number)."""
    raw = _dict(raw, "the issue")
    number = _int(raw, "number")
    if ref is None:
        url = raw.get("repository_url")
        m = _API_REPO.fullmatch(url) if isinstance(url, str) else None
        if not m or m.group(2) in (".", ".."):
            raise _bad("'repository_url' is not an api.github.com/repos/{owner}/{repo} URL")
        ref = Ref(m.group(1), m.group(2), number)
    if number != ref.number:
        raise _bad("number %d is not the requested %d" % (number, ref.number))
    if "pull_request" in raw:
        raise NotAnIssue(ref)
    user = _dict(raw.get("user"), "'user'")
    labels = raw.get("labels")
    if not isinstance(labels, list):
        raise _bad("'labels' is not a list")
    names = [(x.get("name") if isinstance(x, dict) else x) for x in labels]
    if not all(isinstance(n, str) for n in names):
        raise _bad("a label has no name")
    ms = raw.get("milestone")
    milestone = None if ms is None else _str(_dict(ms, "'milestone'"), "title")
    summary = raw.get("sub_issues_summary")
    summary = {} if summary is None else _dict(summary, "'sub_issues_summary'")
    total, done = (summary.get(k, 0) for k in ("total", "completed"))
    if type(total) is not int or type(done) is not int:
        raise _bad("'sub_issues_summary' counts are not integers")
    parent_url = _str(raw, "parent_issue_url", optional=True)
    parent = None
    if parent_url is not None:
        m = _API_ISSUE.fullmatch(parent_url)
        if not m or m.group(2) in (".", ".."):
            raise _bad("'parent_issue_url' is not an issue API URL")
        parent = Ref(m.group(1), m.group(2), int(m.group(3)))
    return Issue(ref=ref, node_id=_str(raw, "node_id"), id=_int(raw, "id"), title=_str(raw, "title"),
                 body=_str(raw, "body", optional=True) or "", state=_str(raw, "state"),
                 state_reason=_str(raw, "state_reason", optional=True), labels=names, milestone=milestone,
                 author=Actor(_int(user, "id"), _str(user, "login"), _str(user, "type")), parent=parent,
                 sub_total=total, sub_done=done, url=_str(raw, "html_url"))


def _path(ref):
    return "repos/%s/%s/issues/%d" % (ref.owner, ref.repo, ref.number)


def get(gh, ref):
    return _issue(gh.rest("GET", _path(ref)), ref)


def children(gh, ref):
    """All sub-issues of `ref`, in GitHub's order, 100 per page until a short page."""
    out = []
    for page in range(1, MAX_PAGES + 1):
        batch = gh.rest("GET", "%s/sub_issues?per_page=%d&page=%d" % (_path(ref), PAGE, page))
        if not isinstance(batch, list):
            raise _bad("the sub-issue list is not a list")
        out += [_issue(x, None) for x in batch]
        if len(batch) < PAGE:
            return out
    raise _bad("the sub-issue list did not end after %d pages" % MAX_PAGES)


def parent_chain(gh, ref, limit=3):
    """The ancestors of `ref`, nearest first: at most `limit`, ending early at a root (or a loop)."""
    chain, seen = [], {ref}
    while len(chain) < limit:
        parent = (chain[-1] if chain else get(gh, ref)).parent
        if parent is None or parent in seen:
            break
        seen.add(parent)
        chain.append(get(gh, parent))
    return chain


# ---- writes ----------------------------------------------------------------------------------------------------------

MAX_TITLE = 256  # GitHub's limit
MAX_BODY = 59900  # characters: comments.MAX_COMMENT (60000, under the unverified true cap) less room for the marker
MAX_LABEL = 50  # GitHub's limit
CLOSE_REASONS = ("completed", "not_planned")
MARKER = "<!-- ghp:create:%s -->"
REREAD_WAITS = (1.0, 1.5, 2.0, 2.5, 3.0)  # seconds before re-reads 2..6: GitHub's issue list lagged a new issue by 2 to 4+ s live (T20)
_RECENT = "?state=all&sort=created&direction=desc&per_page=%d" % PAGE


class IssueError(ValueError):
    """A named refusal about an issue (exit 2 in the CLI): the message starts with the code. `issue` is set when the issue
    EXISTS (a create that read back wrong), so the caller never creates it a second time."""

    def __init__(self, code, detail, issue=None):
        self.code, self.issue = code, issue
        super().__init__("%s: %s" % (code, detail))


def maybe_applied(err):
    """True when a failed write may still have been applied: a timeout or no response, a 5xx, or a reply that was not what a
    success looks like. Then the caller re-reads before it concludes anything; a plain rejection (4xx, GraphQL errors, no gh,
    rate limit, scope) was not applied."""
    return err.code == "E_GH_BAD_BODY" or UNKNOWN_OUTCOME in err.detail or (err.status or 0) >= 500


def _nap(seconds):
    time.sleep(seconds)


def _show(value):
    from ghp.comments import _show as show  # comments imports this module: import at call time
    return show(value)


def reread(err, read):
    """`read()` after the write that failed with `err`. If the read fails too, raise `err` (chained from the read's error): the
    outcome stays unknown, and the caller must see that, not a read timeout that looks like a rejection."""
    try:
        return read()
    except (GhError, NotAnIssue) as again:  # a reply shaped like a pull request is as useless as a failed read
        raise err from again


def _armed(gh):
    """`close` reads before it writes, so the transport's own refusal would come too late (after a read). Anything but a real
    True (no flag, a method, a truthy object) is not armed."""
    if getattr(gh, "armed", False) is not True:
        raise WriteNotArmed()


def _fold(name):
    """GitHub names compare case-insensitively, but only in ASCII: str.lower() would fold U+212A (Kelvin) onto `k`."""
    return name.lower() if name.isascii() else name


def _same(a, b):
    return _same_repo(a, b) and a.number == b.number


def _plain(value, what, limit):
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > limit or not value.isprintable():
        raise ValueError("%s must be a non-empty single line of printable text of at most %d characters, with no space around it"
                         % (what, limit))
    return value


def _real(issue, what):
    if not isinstance(issue, Issue) or type(issue.id) is not int or issue.id < 1:
        raise ValueError("%s is not an issue with an integer id" % what)


def _same_repo(a, b):
    return (_fold(a.owner), _fold(a.repo)) == (_fold(b.owner), _fold(b.repo))


def _created(raw):
    """The Issue in a create reply. A reply that is not one may still have created something: an unknown outcome."""
    try:
        return _issue(raw, None)
    except GhError as e:
        raise GhError(e.code, e.status, "%s: %s" % (UNKNOWN_OUTCOME, e.detail)) from None


def _find(gh, base, marker):
    """The newest-100 issue whose body carries `marker`: None when none, E_MARKER_AMBIGUOUS when more than one does (the marker
    is visible to anyone who can read the issue, so a copy is possible; fail closed rather than adopt the wrong one)."""
    found = gh.rest("GET", base + _RECENT)
    if not isinstance(found, list):
        raise _bad("the newest-issues list is not a list")
    mine = [_issue(x, None) for x in found if isinstance(x, dict) and "pull_request" not in x
            and isinstance(x.get("body"), str) and marker in x["body"]]
    if len(mine) > 1:
        raise IssueError("E_MARKER_AMBIGUOUS", "issues %s all carry this create's marker; not adopting any" % ", ".join(
            "#%d" % i.ref.number for i in mine))
    return mine[0] if mine else None


def _find_waiting(gh, base, marker, sleep):
    """`_find` with a bounded wait: up to 1 + len(REREAD_WAITS) reads (6, over about 10 s), stopping at the first that finds the
    issue. A read that fails ends it at once (the caller's `reread` then keeps the original write error)."""
    for wait in (0,) + REREAD_WAITS:
        if wait:
            sleep(wait)
        made = _find(gh, base, marker)
        if made is not None:
            return made
    return None


def create(gh, cfg: Config, title, body, labels, parent: Optional[Issue], *, milestone: Optional[int] = None, sleep=None) -> Issue:
    """Create an issue in the configured repo and return it as read back. `parent` (an Issue in the same repo) is sent as
    `parent_issue_id` in the same request. `milestone` is a milestone NUMBER (optional, an addition to the plan's signature).
    A hidden marker `<!-- ghp:create:<token> -->` is appended to the body: if the POST fails with an unknown outcome the
    newest issues are searched for it (up to 6 reads over about 10 s, because the list lags a fresh issue by seconds; `sleep` is
    injectable), and a found issue is used instead of creating another; if it is still not found the ORIGINAL error is raised
    (the issue may exist: the caller must wait and read again before it creates it). Nothing is validated remotely
    before the POST, so an unarmed Gh refuses with no call. The read-back compares EXACT values: the labels (any order), the
    milestone and the parent; a difference raises IssueError (E_LABEL_DROPPED, E_MILESTONE_DROPPED, E_PARENT_NOT_ATTACHED)
    carrying the existing issue."""
    _plain(title, "the title", MAX_TITLE)
    if not isinstance(body, str) or len(body) > MAX_BODY:
        raise ValueError("the body must be text of at most %d characters" % MAX_BODY)
    if not isinstance(labels, (list, tuple)):
        raise ValueError("labels must be a list of names")
    labels = [_plain(x, "a label name", MAX_LABEL) for x in labels]  # every element is a str before anything hashes it
    if len(set(labels)) != len(labels):
        raise ValueError("labels must be distinct")
    if milestone is not None and (type(milestone) is not int or milestone < 1):
        raise ValueError("the milestone must be a positive integer (its number)")
    if parent is not None:
        _real(parent, "the parent")
        if not _same_repo(parent.ref, Ref(cfg.owner, cfg.repo, 1)):
            raise IssueError("E_FOREIGN_REPO", "the parent %s is not in %s/%s" % (_show(parent.ref), _show(cfg.owner), _show(cfg.repo)))
    marker = MARKER % uuid.uuid4().hex
    payload = {"title": title, "body": body + "\n\n" + marker if body else marker, "labels": labels}
    if parent is not None:
        payload["parent_issue_id"] = parent.id
    if milestone is not None:
        payload["milestone"] = milestone
    base = "repos/%s/%s/issues" % (cfg.owner, cfg.repo)
    try:
        made = _created(gh.rest("POST", base, payload))
    except GhError as e:
        if not maybe_applied(e):
            raise
        made = reread(e, lambda: _find_waiting(gh, base, marker, sleep or _nap))
        if made is None:
            raise
    ref = Ref(cfg.owner, cfg.repo, made.ref.number)
    try:
        got = get(gh, ref)
        want_ms = None if milestone is None else _milestone_title(gh, cfg, milestone)
    except GhError as e:
        raise GhError(e.code, e.status, "issue #%d was created but its read-back failed: %s" % (ref.number, e.detail)) from None
    if got.id != made.id:
        raise GhError("E_GH_BAD_BODY", None, "reading back #%d returned another issue than the one created" % ref.number)
    if sorted(got.labels) != sorted(labels):
        raise IssueError("E_LABEL_DROPPED", "#%d was created with labels [%s], asked for [%s]" % (
            ref.number, ", ".join(_show(x) for x in got.labels), ", ".join(_show(x) for x in labels)), got)
    if got.milestone != want_ms:
        raise IssueError("E_MILESTONE_DROPPED", "#%d has milestone %s, asked for %s" % (
            ref.number, _show(got.milestone), _show(want_ms)), got)
    if parent is not None and (got.parent is None or not _same(got.parent, parent.ref)):
        raise IssueError("E_PARENT_NOT_ATTACHED", "#%d was created but its parent is %s, asked for %s" % (
            ref.number, _show(got.parent), _show(parent.ref)), got)
    return got


def _milestone_title(gh, cfg, number):
    m = gh.rest("GET", "repos/%s/%s/milestones/%d" % (cfg.owner, cfg.repo, number))
    if not isinstance(m, dict) or m.get("number") != number or type(m.get("number")) is not int:
        raise _bad("the milestone is not the one asked for")
    return _str(m, "title")


def add_sub_issue(gh, parent: Issue, child: Issue):
    """Make `child` a sub-issue of `parent`. The request carries the child's integer `id`, never its number (the number is a 404
    live). One repo only (D7): a child of another repo, or the parent itself, is refused before any call (E_FOREIGN_REPO). After an
    unknown outcome the child is re-read: attached means done. A repeated POST for a child that is already linked is a 422 live
    (T20); on a 422 and only then the parent's children are read, and a child that is already under THIS parent is a success.
    Returns True in that case (it was already linked), else False. The POST is never repeated."""
    _real(parent, "the parent")
    _real(child, "the child")
    if not _same_repo(parent.ref, child.ref):
        raise IssueError("E_FOREIGN_REPO", "%s and %s are not in the same repository" % (_show(parent.ref), _show(child.ref)))
    if parent.id == child.id:
        raise ValueError("an issue cannot be its own sub-issue")
    try:
        gh.rest("POST", _path(parent.ref) + "/sub_issues", {"sub_issue_id": child.id})
    except GhError as e:
        if maybe_applied(e):
            now = reread(e, lambda: get(gh, child.ref)).parent
            if now is None or not _same(now, parent.ref):
                raise
        elif e.status == 422:
            if not any(k.id == child.id for k in reread(e, lambda: children(gh, parent.ref))):
                raise
            return True
        else:
            raise
    return False


def close(gh, issue: Issue, reason) -> Issue:
    """Close the issue with `state_reason` `completed` or `not_planned` (anything else is refused, no call) and return it as
    read back. The issue is read first: one that is already closed is returned untouched (no write, its reason is kept), and
    the caller's snapshot is never trusted. An unarmed Gh raises before even that read."""
    if type(reason) is not str or reason not in CLOSE_REASONS:  # an exact str: nothing with its own __eq__ passes the whitelist
        raise IssueError("E_CLOSE_REASON", "the reason must be one of %s" % ", ".join(CLOSE_REASONS))
    _real(issue, "the issue")
    _armed(gh)
    now = get(gh, issue.ref)
    if now.state == "closed":
        return now
    try:
        done = _issue(gh.rest("PATCH", _path(issue.ref), {"state": "closed", "state_reason": reason}), issue.ref)
    except GhError as e:
        if not maybe_applied(e):
            raise
        done = reread(e, lambda: get(gh, issue.ref))
        if done.state != "closed":
            raise
    if done.state != "closed" or done.state_reason != reason:
        raise IssueError("E_CLOSE_NOT_APPLIED", "#%d is %s (reason %s) after closing it as %s" % (
            issue.ref.number, _show(done.state), _show(done.state_reason), reason), done)
    return done


def register(sub):
    p = sub.add_parser("get", help="print one issue as JSON (read only)")
    p.add_argument("ref", help="5, #5, owner/repo#5 or an issue URL")


def _resolve(text):
    """One repo only (D7): the configured one. A bare number means it; a ref to another repo is E_FOREIGN_REPO (exit 2,
    before any call). A missing or invalid config is a ConfigError (exit 2), also for a full ref."""
    cfg = Config.load()
    try:
        return parse_ref(text, Ref(cfg.owner, cfg.repo, 1))
    except RefError as e:
        raise ValueError(str(e)) from None


def run(args, gh):
    issue = get(gh, _resolve(args.ref))  # NotAnIssue is a ValueError: exit 2
    d = dataclasses.asdict(issue)
    d["ref"], d["parent"] = str(issue.ref), None if issue.parent is None else str(issue.parent)
    print(json.dumps(d, indent=2))
    return 0
