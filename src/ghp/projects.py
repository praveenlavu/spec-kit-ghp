"""Project reads (G3): the Status field and its option ids, the built-in workflows, an issue's project item and its
Status history; and two writes (T17): `add_item` and `set_status`. The only `mutation` documents are in MUTATIONS; every
read is in QUERIES (a test pins both).

Only user-owned Projects (D8): `user(login: <project_owner>) { projectV2(number: N) }`; config already refuses an
organization owner. Option ids are discovered by NAME at run time; the names are config keys (`status_todo`,
`status_active`, `status_done`), matched case-sensitively, and a missing one is E_STATUS_OPTION_MISSING.

Every query here SELECTS project fields, which fails as a whole under a repo-only token (INSUFFICIENT_SCOPES, SPIKE
Q3), so each read raises ScopeMissing naming `read:project` (GitHub's own list is kept when it names a scope).
Automation is told by `actor.login == "github-project-automation"`, NEVER by `wasAutomated` (live: false on every
automated move, so this module does not even select it).
Query and field names were checked offline against the saved public schema (tests/test_projects.py); the shapes in
tests/fixtures/gh were hand-authored from the spike's projects and automation captures and are checked against live shapes
(2026-11-09, T13 and T13b, docs/evidence/T13.md; no key a fixture holds is missing live).
A response of an unexpected shape raises GhError("E_GH_BAD_BODY"), never KeyError or TypeError.

Writes (SPIKE-RESULTS, live 2026-11-09; re-checked in T20, docs/evidence/T20.md): `addProjectV2ItemById` twice for the same issue returns the
same item id; `updateProjectV2ItemFieldValue` with `singleSelectOptionId` then a read through `fieldValueByName("Status")` matched; GitHub forbids
adding and updating in one call, so they are two requests. Both raise WriteNotArmed with no call while `Gh` is unarmed
(D3), and a failed write that `maybe_applied` is re-read before it is reported. Moving an item to the Done option closes the issue
(the Project's own workflow), so `set_status(to=<status_done>)` raises E_DONE_NEEDS_THE_GATE unless `allow_done=True`: only the
`done` verb passes it (D4). Archived items are ignored by `item_for` (`includeArchived: false`), and `add_item` refuses an archived
item it is handed back (E_ITEM_ARCHIVED). UNVERIFIED live: that GitHub hands back the archived item for a repeated add.
"""
from collections import Counter
from typing import Dict, List, NamedTuple, Optional

from ghp.comments import _show
from ghp.config import Config
from ghp.gh import UNKNOWN_OUTCOME, GhError, ScopeMissing
from ghp.issues import Issue, maybe_applied, reread

STATUS_FIELD = "Status"
AUTOMATION = "github-project-automation"
SCOPE = "read:project"
MAX_PAGES = 50  # 5,000 nodes per list; only stops a server that never ends one

_PAGE = "pageInfo { hasNextPage endCursor }"
_PROJECT = "query($owner: String!, $number: Int!, $cursor: String) { user(login: $owner) { projectV2(number: $number) { id %s } } }"
QUERIES = {
    "fields": _PROJECT % ("fields(first: 100, after: $cursor) { " + _PAGE + """ nodes { __typename
      ... on ProjectV2SingleSelectField { id name options { id name } } } }"""),
    "workflows": _PROJECT % ("workflows(first: 100, after: $cursor) { " + _PAGE + " nodes { name enabled number } }"),
    "items": "query($issue: ID!, $cursor: String) { node(id: $issue) { ... on Issue { projectItems(first: 100, after: $cursor, includeArchived: false) { "
             + _PAGE + " nodes { id project { id } } } } } }",
    "events": """query($issue: ID!, $cursor: String) { node(id: $issue) { ... on Issue {
      timelineItems(first: 100, after: $cursor, itemTypes: [PROJECT_V2_ITEM_STATUS_CHANGED_EVENT]) { """ + _PAGE + """
        nodes { ... on ProjectV2ItemStatusChangedEvent { actor { login } previousStatus status createdAt } } } } } }""",
    "status_of": 'query($item: ID!) { node(id: $item) { ... on ProjectV2Item { fieldValueByName(name: "Status") { '
                 '... on ProjectV2ItemFieldSingleSelectValue { name optionId } } } } }',
}
MUTATIONS = {
    "add": "mutation($project: ID!, $content: ID!) { addProjectV2ItemById(input: {projectId: $project, contentId: $content}) "
           "{ item { id isArchived } } }",
    "status": "mutation($project: ID!, $item: ID!, $field: ID!, $option: String!) { updateProjectV2ItemFieldValue(input: "
              "{projectId: $project, itemId: $item, fieldId: $field, value: {singleSelectOptionId: $option}}) { projectV2Item { id } } }",
}


class ProjectError(ValueError):
    """A named refusal about the Project (exit 2 in the CLI): the message starts with the code."""

    def __init__(self, code, detail):
        self.code = code
        super().__init__("%s: %s" % (code, detail))


class Board(NamedTuple):
    project_id: str
    status_field_id: str
    options: Dict[str, str]  # every Status option name -> its id
    status_done: Optional[str] = None  # the configured name of the Done option, set by `discover`; `set_status` refuses a Board without it


class Workflow(NamedTuple):
    name: str
    enabled: bool
    number: int


class StatusEvent(NamedTuple):
    actor_login: Optional[str]  # None when GitHub reports no actor
    from_: str  # "" for the first status
    to: str
    at: str

    @property
    def is_automation(self):
        return self.actor_login == AUTOMATION


def _bad(what):
    return GhError("E_GH_BAD_BODY", None, "unexpected project response: " + what)


def _dig(data, path):
    """Follow `path` through nested objects. A null `user` or `projectV2` is a missing Project; any other break is a bad body."""
    for key in path:
        if not isinstance(data, dict):
            raise _bad("expected an object before '%s'" % key)
        present, data = key in data, data.get(key)
        if present and data is None and key in ("user", "projectV2"):
            raise ProjectError("E_PROJECT_NOT_FOUND", "no user-owned Project found for this owner and project_number")
    if not isinstance(data, dict):
        raise _bad("'%s' is not an object" % path[-1])
    return data


def _query(gh, name, variables):
    """One read; a ScopeMissing that names no scope is made to name `read:project` (GitHub's own list is kept)."""
    try:
        return gh.graphql(QUERIES[name], variables)
    except ScopeMissing as e:
        if e.scopes:
            raise
        raise ScopeMissing([SCOPE]) from None


def _pages(gh, name, variables, path):
    """Yield (object holding the list, its nodes) per page of the connection at `path`+[last], following pageInfo."""
    cursor, seen = None, set()
    for _ in range(MAX_PAGES):
        data = _query(gh, name, dict(variables, cursor=cursor))
        holder = _dig(data, path[:-1])
        c = _dig(holder, path[-1:])
        nodes, info = c.get("nodes"), c.get("pageInfo")
        if not isinstance(nodes, list) or not isinstance(info, dict) or type(info.get("hasNextPage")) is not bool:
            raise _bad("'%s' has no nodes list and pageInfo" % path[-1])
        yield holder, nodes
        if not info["hasNextPage"]:
            return
        cursor = info.get("endCursor")
        if not isinstance(cursor, str) or cursor in seen:
            raise _bad("'%s' pageInfo does not advance" % path[-1])
        seen.add(cursor)
    raise _bad("'%s' did not end after %d pages" % (path[-1], MAX_PAGES))


def _str(d, key, what, optional=False):
    v = d.get(key)
    if isinstance(v, str) or (optional and v is None):
        return v
    raise _bad("%s '%s' is not a string" % (what, key))


def _owner_vars(cfg):
    if cfg.project_number is None:
        raise ProjectError("E_PROJECT_NUMBER_MISSING", "set project_number in ghp.config.json")
    return {"owner": cfg.project_owner, "number": cfg.project_number}


def _board(gh, cfg):
    """The Status field's id and ALL its options, unchecked against the config."""
    path = ("user", "projectV2", "fields")
    project_id, singles = None, []
    for holder, nodes in _pages(gh, "fields", _owner_vars(cfg), path):
        project_id = _str(holder, "id", "the project")
        for n in nodes:
            if not isinstance(n, dict):
                raise _bad("a field is not an object")
            if n.get("__typename") != "ProjectV2SingleSelectField":
                continue
            options = n.get("options")
            if not isinstance(options, list) or not all(isinstance(o, dict) for o in options):
                raise _bad("a field's options are not a list of objects")
            found = {_str(o, "name", "an option"): _str(o, "id", "an option") for o in options}
            if _str(n, "name", "a field") == STATUS_FIELD:
                if len(found) != len(options):
                    counts = Counter(o["name"] for o in options)
                    raise ProjectError("E_STATUS_OPTION_DUPLICATE", "the Status field has more than one option named %s" % ", ".join(
                        "'%s'" % _show(x) for x, k in counts.items() if k > 1))
                return Board(project_id, _str(n, "id", "the Status field"), found)
            singles.append(n["name"])
    raise ProjectError("E_STATUS_FIELD_MISSING", "the Project has no single-select field named '%s' (single-select fields "
                       "found: %s)" % (STATUS_FIELD, ", ".join(_show(x) for x in singles) or "none"))


def discover(gh, cfg: Config) -> Board:
    """The Board, after checking that the three configured Status option names exist."""
    board = _board(gh, cfg)._replace(status_done=cfg.status_done)
    wanted = (("status_todo", cfg.status_todo), ("status_active", cfg.status_active), ("status_done", cfg.status_done))
    missing = ["'%s' (%s)" % (_show(name), key) for key, name in wanted if name not in board.options]
    if missing:
        raise ProjectError("E_STATUS_OPTION_MISSING", "Status option %s not found; options found: %s" % (
            ", ".join(missing), ", ".join("'%s'" % _show(n) for n in board.options) or "none"))
    return board


def workflows(gh, cfg: Config) -> List[Workflow]:
    out = []
    for _, nodes in _pages(gh, "workflows", _owner_vars(cfg), ("user", "projectV2", "workflows")):
        for n in nodes:
            if not isinstance(n, dict) or type(n.get("enabled")) is not bool or type(n.get("number")) is not int:
                raise _bad("a workflow has no name, enabled flag and number")
            out.append(Workflow(_str(n, "name", "a workflow"), n["enabled"], n["number"]))
    return out


def item_for(gh, board: Board, issue_node_id: str) -> Optional[str]:
    """The id of the issue's item on this board, or None when the issue is not on it."""
    for _, nodes in _pages(gh, "items", {"issue": issue_node_id}, ("node", "projectItems")):
        for n in nodes:
            if not isinstance(n, dict) or not isinstance(n.get("project"), dict):
                raise _bad("a project item has no project")
            if _str(n["project"], "id", "an item's project") == board.project_id:
                return _str(n, "id", "a project item")
    return None


def status_events(gh, issue_node_id: str) -> List[StatusEvent]:
    """Every Status change on the issue's timeline, oldest first (all Projects the issue is on)."""
    out = []
    for _, nodes in _pages(gh, "events", {"issue": issue_node_id}, ("node", "timelineItems")):
        for n in nodes:
            if not isinstance(n, dict) or not isinstance(n.get("status"), str):
                raise _bad("a timeline item is not a Status change")
            actor = n.get("actor")
            if actor is not None and not isinstance(actor, dict):
                raise _bad("an event's actor is not an object")
            out.append(StatusEvent(None if actor is None else _str(actor, "login", "an actor"),
                                   _str(n, "previousStatus", "an event"), n["status"], _str(n, "createdAt", "an event")))
    return out


def _bad_write(what):
    return GhError("E_GH_BAD_BODY", None, "%s: unexpected project response: %s" % (UNKNOWN_OUTCOME, what))


def _added(data):
    """(item id, archived) from the addProjectV2ItemById reply; anything else is an unknown outcome (the item may exist)."""
    added = data.get("addProjectV2ItemById") if isinstance(data, dict) else None
    item = added.get("item") if isinstance(added, dict) else None
    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] or type(item.get("isArchived")) is not bool:
        raise _bad_write("the added item has no id and isArchived")
    return item["id"], item["isArchived"]


ALREADY_ON_BOARD = "Content already exists in this project"  # live reply (T34): the Project's auto-add workflow added it first


def _auto_added(err):
    """True only for GitHub's exact single-error reply that the issue is already on the board; every other error stands."""
    return err.code == "E_GH_GRAPHQL" and err.detail == ALREADY_ON_BOARD


def add_item(gh, board: Board, issue: Issue) -> str:
    """Add the issue to the Project and return its item id. Idempotent: GitHub returns the existing item for a repeated add, so
    no read comes first (and an unarmed Gh sends nothing). Status is NOT set here: that is a second request (`set_status`).
    An archived item is refused (E_ITEM_ARCHIVED); after an unknown outcome, or GitHub's "already exists" reply when the Project's
    `Auto-add sub-issues to project` workflow won the race, the board is re-read and the item found is returned (none found: the error stands)."""
    if not isinstance(issue, Issue) or not isinstance(issue.node_id, str) or not issue.node_id:
        raise ValueError("the issue has no node id")
    try:
        item, archived = _added(gh.graphql(MUTATIONS["add"], {"project": board.project_id, "content": issue.node_id}))
    except GhError as e:
        if not (maybe_applied(e) or _auto_added(e)):
            raise
        item = reread(e, lambda: item_for(gh, board, issue.node_id))
        if item is None:
            raise
        return item
    if archived:
        raise ProjectError("E_ITEM_ARCHIVED", "item %s for #%d is archived in the Project; un-archive it there (ghp does not)" % (
            _show(item), issue.ref.number))
    return item


def _status_of(gh, item_id):
    """(option name, option id) of the item's Status, or None when it has none."""
    node = _query(gh, "status_of", {"item": item_id}).get("node")
    if not isinstance(node, dict) or "fieldValueByName" not in node:
        raise _bad("the item has no fieldValueByName")
    value = node["fieldValueByName"]
    if value is None:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("name"), str) or not isinstance(value.get("optionId"), str):
        raise _bad("the Status value has no name and optionId")
    return value["name"], value["optionId"]


def set_status(gh, board: Board, item_id, to, *, allow_done=False):
    """Set the item's Status to the option named `to` (an exact name from `board.options`), then read it back: a name AND id that
    differ raise E_STATUS_READBACK_MISMATCH. Naming the Done option raises E_DONE_NEEDS_THE_GATE unless `allow_done is True` (exactly:
    1 or "yes" do not count); only the `done` verb (T26) may pass it (D4). The guard compares option IDS (an alias name for the done option is refused too) and fails
    closed: a Board whose `status_done` is not one of its options is E_BOARD_NO_DONE, nothing sent. The refusals come before any
    request. After an unknown outcome the value is re-read and accepted if it is the one asked for."""
    if type(item_id) is not str or not item_id:
        raise ValueError("the item id must be a non-empty string")
    done_id = board.options.get(board.status_done) if isinstance(board.status_done, str) else None
    if done_id is None:
        raise ProjectError("E_BOARD_NO_DONE", "this Board does not say which option is Done (build it with discover); nothing sent")
    if type(to) is not str or to not in board.options:
        raise ProjectError("E_STATUS_OPTION_MISSING", "Status option '%s' not found; options found: %s" % (
            _show(to), ", ".join("'%s'" % _show(n) for n in board.options) or "none"))
    if board.options[to] == done_id and allow_done is not True:  # only the real True opens the gate, never a truthy value
        raise ProjectError("E_DONE_NEEDS_THE_GATE", "moving an item to '%s' closes its issue; only the done verb, after the human "
                           "gate, may do it" % _show(to))
    want = (to, board.options[to])
    try:
        gh.graphql(MUTATIONS["status"], {"project": board.project_id, "item": item_id, "field": board.status_field_id, "option": want[1]})
    except GhError as e:
        if not maybe_applied(e):
            raise
        if reread(e, lambda: _status_of(gh, item_id)) != want:
            raise
        return
    now = _status_of(gh, item_id)
    if now != want:
        raise ProjectError("E_STATUS_READBACK_MISMATCH", "item %s: wrote '%s' (%s) but the Project reports %s" % (
            _show(item_id), _show(to), _show(want[1]), "no Status" if now is None else "'%s' (%s)" % (_show(now[0]), _show(now[1]))))


def register(sub):
    p = sub.add_parser("project", help="Project reads: `project show` (read only)")
    ps = p.add_subparsers(dest="project_command", metavar="ACTION", required=True)
    ps.add_parser("show", help="print the Status options and the built-in workflows (read only)")


def run(args, gh):
    cfg = Config.load()
    board, flows = discover(gh, cfg), workflows(gh, cfg)
    roles = {cfg.status_todo: "status_todo", cfg.status_active: "status_active", cfg.status_done: "status_done"}
    print("Project: %s #%d (%s)" % (cfg.project_owner, cfg.project_number, _show(board.project_id)))
    print("Status options (field %s):" % _show(board.status_field_id))
    for name, oid in board.options.items():
        print("  %-20s %s%s" % (_show(name), _show(oid), "  <- " + roles[name] if name in roles else ""))
    print("Workflows:")
    for w in sorted(flows, key=lambda w: w.number):
        print("  #%d %-34s %s" % (w.number, _show(w.name), "enabled" if w.enabled else "disabled"))
    return 0
