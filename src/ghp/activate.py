"""`ghp activate REF`: move a story's Project item to the configured active Status (`status_active`, default 'In Progress').
ado had no such verb; this is the GitHub seam (a Project item), built on `projects.set_status`. A dry run unless `--apply`;
`plan.arm_or_refuse` is the only gate.

- The target is ONLY `cfg.status_active`. It is refused when that option is the done option (by name and by option id, E_ACTIVE_IS_DONE):
  a Done move closes the issue and belongs to the `done` verb. `allow_done` is never passed, and `apply_activate` refuses any plan whose
  target is not the configured active option (E_TARGET_NOT_ACTIVE).
- An item already at the active option is a no-op: nothing is written. An issue not on the Project is added first (`projects.add_item`),
  then its Status is set: two requests (GitHub forbids both in one call).
- Refused with nothing written: a closed issue (E_ISSUE_CLOSED, whatever its state_reason), an item already in the done option
  (E_ALREADY_DONE: activate never moves Done back to active; do that by hand on the Project. For an OPEN issue the message says so: it is the
  reopened-issue case, whose card stays at Done; seen live in T26), a pull request, another repo (D7),
  no `project_number` (E_NO_PROJECT).
- One failed write ends the run. After an unknown outcome the message says so and to run `ghp activate` again, which reads first.
  Nothing is retried here.
- The item's Status is read once more right before the write (after the add too: `add_item` hands back an existing item): Done refuses
  (E_ALREADY_DONE), the active option is a no-op. Windows left: GitHub has no compare-and-set, so a change between that read and the
  write cannot be excluded (on the on-board path and on the add path alike); it is narrowed to one request, not closed.
Verified live in T20 (docs/evidence/T20.md, AC3): Todo -> In Progress with the Status read back, a second run that is a no-op, and an issue
that was not on the Project (added, then set). Whether the Project's own automation also left a Todo event before ours varied between two runs
(T20 finding 4, cause unverified), so a story that was not on the board goes straight to In Progress. UNVERIFIED live: whether Projects
automation reacts to a Status change on a closed issue; an archived item handed back by addProjectV2ItemById; activate by the token of a GitHub Actions run
(its identity is read through GraphQL `viewer`, T31a: REST `GET /user` is a 403 there).
"""
import hashlib
import json

from . import approvals, comments, issues, plan as write_gate, projects
from .config import Config
from .gh import GhError


class ActivateError(ValueError):
    """A named refusal (exit 2 in the CLI): the message starts with the code."""

    def __init__(self, code, detail):
        self.code = code
        super().__init__("%s: %s" % (code, detail))


def _cell(text):
    """Text from GitHub on one line, whole and injective: backslashes are doubled BEFORE the display escapes, so an invisible character
    and the literal text of its escape differ (the digest line covers whatever a display still loses)."""
    return comments._show(str(text).replace("\\", "\\\\"))


def _target(cfg, board=None):
    """The active option's name; refuses it when it is the done option (by name, and by option id when the board is known)."""
    to = cfg.status_active
    if to == cfg.status_done or (board is not None and board.options.get(to) == board.options.get(cfg.status_done)):
        raise ActivateError("E_ACTIVE_IS_DONE", "status_active '%s' is the done option; activate never moves an item to Done "
                            "(that closes the issue; only the done verb does it)" % _cell(to))
    return to


def _not_done(now, cfg, board, issue):
    """Refuse an item whose current Status is the done option, by option id or by name (either one)."""
    if now is not None and (now[1] == board.options[cfg.status_done] or now[0] == cfg.status_done):
        if issue.state == "open":  # the usual cause: the issue was reopened, and nothing in the Project moves its card back (T26b, seen live)
            raise ActivateError("E_ALREADY_DONE", "#%d is open but its Project item is at '%s'. Reopening an issue does not move its card, and ghp never "
                                "moves a card out of Done; move it back by hand in the Project, then run ghp activate again" % (issue.ref.number, _cell(now[0])))
        raise ActivateError("E_ALREADY_DONE", "the Project item of #%d is already at '%s'; activate does not move it back (do that by hand "
                            "on the Project)" % (issue.ref.number, _cell(now[0])))


def plan_activate(gh, cfg, board, issue):
    """The plan, read only: {action: activate | noop, issue, item_id, current: (name, id) | None, to, ...}."""
    to = _target(cfg, board)
    if issue.state != "open":
        raise ActivateError("E_ISSUE_CLOSED", "#%d is %s; activate does not move a closed issue" % (issue.ref.number, _cell(issue.state)))
    item = projects.item_for(gh, board, issue.node_id)
    now = None if item is None else projects._status_of(gh, item)
    _not_done(now, cfg, board, issue)
    noop = now == (to, board.options[to])
    return {"action": "noop" if noop else "activate", "issue": issue, "item_id": item, "current": now, "to": to,
            "to_id": board.options[to], "project_id": board.project_id}


def _digest(p):
    """SHA-256 of the exact data behind the plan (not its rendering), so --expect-plan covers it even where a display is lossy."""
    i = p["issue"]
    row = [p["action"], i.ref.number, i.id, i.node_id, i.title, i.state, p["item_id"], p["current"], p["to"], p["to_id"], p["project_id"]]
    return hashlib.sha256(json.dumps(row, ensure_ascii=True).encode("ascii")).hexdigest()


def render_plan(p):
    i, n = p["issue"], p["issue"].ref.number
    now = "(not on the Project)" if p["item_id"] is None else "(none)" if p["current"] is None else _cell(p["current"][0])
    lines = ["# Plan: activate issue #%d" % n, "", "Issue: #%d %s" % (n, _cell(i.title))]
    if p["action"] == "noop":
        lines += ["Status: %s" % _cell(p["to"]), "", "Action: nothing to do: already %s" % _cell(p["to"])]
    else:
        lines += ["Status: %s -> %s" % (now, _cell(p["to"])), ""]
        if p["item_id"] is None:
            lines.append("Step 1: add #%d to the Project (its own request)" % n)
        lines.append("Step %d: set Status to %s, then read it back" % (1 + (p["item_id"] is None), _cell(p["to"])))
    return "\n".join(lines + ["", "digest: " + _digest(p)]) + "\n"


def apply_activate(gh, cfg, board, p):
    """Do the plan: {added, done, error, unknown}. The guards come first and raise (nothing sent); a write failure is reported in the
    result, so a caller still learns that the item was added when its Status then failed."""
    if p["to"] != cfg.status_active:
        raise ActivateError("E_TARGET_NOT_ACTIVE", "activate sets only the configured active option '%s'" % _cell(cfg.status_active))
    _target(cfg, board)  # the alias-by-id refusal (E_ACTIVE_IS_DONE), as in the plan
    res = {"added": False, "done": p["action"] == "noop", "noop": p["action"] == "noop", "error": None, "unknown": False}
    if res["done"]:
        return res
    item = p["item_id"]
    try:
        if item is None:
            item = projects.add_item(gh, board, p["issue"])
            res["added"] = True
        now = projects._status_of(gh, item)  # the plan is older than this write: look again (an added item may be an existing one)
        _not_done(now, cfg, board, p["issue"])
        if now == (p["to"], p["to_id"]):
            res["done"] = res["noop"] = True
            return res
        projects.set_status(gh, board, item, p["to"])  # never allow_done: the done option is refused in there too
        res["done"] = True
    except ActivateError:
        raise
    except (GhError, ValueError) as e:
        res["error"], res["unknown"] = comments._show(e, 300), isinstance(e, GhError) and issues.maybe_applied(e)
    return res


def render_result(p, res):
    n = p["issue"].ref.number
    if res["done"]:
        return "nothing sent: already %s" % _cell(p["to"]) if res["noop"] else "moved #%d to %s" % (n, _cell(p["to"]))
    lines = ["activate stopped: " + res["error"]]
    if res["added"]:
        lines.append("#%d was added to the Project but its Status was not set" % n)
    lines.append("  " + ("outcome unknown: the write may have been applied; do not repeat it by hand, re-run ghp activate (it reads first)"
                         if res["unknown"] else "nothing was retried; fix the cause and re-run ghp activate"))
    return "\n".join(lines)


def register(sub):
    p = sub.add_parser("activate", help="move a story's Project item to the active Status (dry run unless --apply)")
    p.add_argument("ref", help="the story: 5, #5, owner/repo#5 or an issue URL")
    write_gate.add_write_flags(p)


def run(args, gh):
    ref = issues._resolve(args.ref)  # config, then the ref: a bad one is refused before any call
    cfg = Config.load()
    if cfg.project_number is None:
        raise ActivateError("E_NO_PROJECT", "activate needs a Project: set project_number in ghp.config.json")
    _target(cfg)  # a configured active option that is the done option is refused before the first call
    issue = issues.get(gh, ref)  # NotAnIssue is a ValueError: exit 2
    board = projects.discover(gh, cfg)
    p = plan_activate(gh, cfg, board, issue)
    text = render_plan(p)
    write_gate.arm_or_refuse(args, gh, text, approvals.approve_hook(gh, ref, "activate", text, p["action"] != "noop"))
    print(text)
    if not args.apply:
        return 0
    res = apply_activate(gh, cfg, board, p)
    print(render_result(p, res))
    return 0 if res["done"] else 1
