"""`ghp push REF`: mirror the tasks of `tasks.md` into sub-issues of a story and put each on the Project at the configured Todo
(ado's `push`, rewritten for GitHub sub-issues and Project items). A dry run unless `--apply`; `plan.arm_or_refuse` is the only gate.

What changed from ado:
- A task is matched to its issue by a body marker, never by title: `<!-- spec-kit-ghp:task:T001 -->`. A second marker
  `<!-- spec-kit-ghp:story:<n> -->` says which story pushed it (task ids repeat from story to story), so an issue that was created but
  never linked is found again in the newest issues and linked, instead of being created twice. Only the markers and the title matter: the
  body is never compared or rewritten (a human may edit it), and labels are never compared (so case folding does not come up).
- The issue is created with the story as its parent in the same request (`issues.create`), label `ghp:task`, then added to the Project
  and set to the configured Todo (`projects.add_item`, `projects.set_status`: two requests). Without `project_number` push goes on without
  Project items; with one that cannot be read it refuses before any write.
- Update means: a changed title (PATCH of the title), a missing sub-issue link, a missing Project item or a Project item without a Status.
  Push never opens or closes an issue and never moves an item to Done: a checked box changes nothing (the `done` verb owns that).
- Fail closed: the same marker on two adopted issues, one issue with two task markers, a marker on a child in another repo or on an
  unlinked one under another parent, a task id that is not `T` and 3-9 ASCII digits, a duplicate task id in tasks.md, more than 100
  tasks (or sub-issues) all refuse before any write. The markers can be copied by anyone, so an UNLINKED issue is adopted only when the
  pushing identity wrote it or it has the `ghp:task` label; PRs, the story itself and other people's copies are ignored, not errors.
- The first failed write ends the run (ado carried on). After an unknown outcome (a lost reply that the re-read could not settle) the
  message says the write may have been applied and to run `ghp push` again, which reads first and resumes. Nothing is ever retried here.
- Writes are paced (`WRITE_GAP` seconds apart, at most 48 a minute), on top of `Gh`'s own 1 s gap.
Verified live in T20 (docs/evidence/T20.md), 3 tasks against a scratch repo: preview, apply (create, sub-issue link, Project item, Status Todo),
a second run that sends nothing, and the hidden-marker round trip; the writes came out at about 30 a minute. The label `ghp:task` is
created by GitHub when the token has push access (it was absent before the first push), and the plan warns when it does not exist (T20a).
UNVERIFIED live: the E_LABEL_DROPPED branch with a token without push access; that the newest 300 issues hold every unlinked task of an
earlier run on a busy repo (the plan's search does not wait for GitHub's list lag; only `issues.create`'s own re-read does); a push of
many tasks (100 is extrapolated); a push by the token of a GitHub Actions run (its identity is read through GraphQL `viewer`, T31a: REST `GET /user` is a 403 there).
"""
import hashlib
import json
import re
import textwrap
import time
import urllib.parse
from pathlib import Path

from . import approvals, comments, gate, issues, plan as write_gate, projects
from .config import Config
from .dod import dod_file, ledger_spec_dir, safe_dir
from .gh import GhError
from .tasks import not_tasks, parse_tasks

MAX_TASKS = 100  # GitHub's limit of sub-issues under one parent
MAX_TITLE = issues.MAX_TITLE
LABEL = "ghp:task"
WRITE_GAP = 1.25  # seconds between our writes: 48 a minute at most (the rule is fewer than 60)
SEARCH_PAGES = 3  # newest issues read to find a task that was created and never linked
MAX_CELL = 1024  # a title is at most 256 characters (ours are cut, GitHub's are capped there); anything longer is refused, never cut in the plan
_PATH = "?state=all&sort=created&direction=desc&per_page=%d&page=%d"
_TASK_MARK, _STORY_MARK = "<!-- spec-kit-ghp:task:%s -->", "<!-- spec-kit-ghp:story:%d -->"
_TASK_RE = re.compile(r"<!-- spec-kit-ghp:task:(T[0-9]{3,9}) -->")
_ID = re.compile(r"T[0-9]{3,9}")  # ASCII only: tasks.py accepts any Unicode digit, which no marker could match
_HINT = "spec-kit-ghp:task:"


class PushError(ValueError):
    """A named refusal (exit 2 in the CLI): the message starts with the code."""

    def __init__(self, code, detail):
        self.code = code
        super().__init__("%s: %s" % (code, detail))


class Pacer:
    """Call it before each write: it sleeps until `gap` seconds have passed since the previous one. `clock` and `sleep` are injectable."""

    def __init__(self, clock=time.monotonic, sleep=time.sleep, gap=None):
        self.clock, self.sleep, self.gap, self.last = clock, sleep, WRITE_GAP if gap is None else gap, None

    def __call__(self):
        if self.last is not None:
            wait = self.gap - (self.clock() - self.last)
            if wait > 0:
                self.sleep(wait)
        self.last = self.clock()


def _check_tasks(tasks):
    if len(tasks) > MAX_TASKS:
        raise PushError("E_TOO_MANY_TASKS", "tasks.md has %d tasks; GitHub allows %d sub-issues under one issue; nothing was sent" % (len(tasks), MAX_TASKS))
    ids = [t["id"] for t in tasks]
    bad = [i for i in ids if not _ID.fullmatch(i)]
    if bad:
        raise PushError("E_BAD_TASK_ID", "task id %s is not T and 3 to 9 ASCII digits" % comments._show(bad[0], 20))
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise PushError("E_DUPLICATE_TASK_ID", "tasks.md lists %s more than once" % ", ".join(dupes[:5]))


def _title(task):
    """(`T001 <text>`, was it cut): no unprintable character, no live @mention, one line, at most MAX_TITLE characters (cut with …)."""
    text = "".join(c if c.isprintable() else " " for c in "%s %s" % (task["id"], task["title"][:4 * MAX_TITLE]))
    text = " ".join(comments._MENTION.sub(comments.NO_MENTION, text).split())
    return (text, False) if len(text) <= MAX_TITLE else (text[:MAX_TITLE - 1].rstrip() + "…", True)


def _body(story, tid):
    return "Task %s of #%d, pushed from tasks.md by ghp.\n\n%s\n%s" % (tid, story.ref.number, _TASK_MARK % tid, _STORY_MARK % story.ref.number)


def _existing(gh, cfg, story):
    """({task id: Issue}, number of children): the story's sub-issues that carry a task marker, plus unlinked issues that carry this story's
    marker (found in the newest SEARCH_PAGES x 100 issues). The markers are plain text anyone can copy, so an unlinked issue is adopted only
    when the pushing identity wrote it or it carries the `ghp:task` label (only push access sets labels; the author test also covers a label
    that was dropped); pull requests, the story itself and anyone else's copy are ignored. Ambiguity among adopted ones refuses."""
    kids = issues.children(gh, story.ref)
    found, me = {}, []

    def take(issue, ids):
        if len(ids) > 1:
            raise PushError("E_TASK_MARKER_AMBIGUOUS", "#%d carries the markers of %s; not adopting it" % (issue.ref.number, ", ".join(sorted(ids))))
        (tid,) = ids
        if tid in found:
            raise PushError("E_TASK_MARKER_AMBIGUOUS", "#%d and #%d both carry the marker of %s; not adopting either" % (
                found[tid].ref.number, issue.ref.number, tid))
        if not issues._same_repo(issue.ref, story.ref):
            raise PushError("E_FOREIGN_REPO", "#%d carries the marker of %s but is in %s/%s" % (
                issue.ref.number, tid, comments._show(issue.ref.owner), comments._show(issue.ref.repo)))
        found[tid] = issue

    for kid in kids:
        ids = set(_TASK_RE.findall(kid.body))
        if ids:
            take(kid, ids)
    kid_ids, mine = {k.id for k in kids}, _STORY_MARK % story.ref.number
    for page in range(1, SEARCH_PAGES + 1):
        batch = gh.rest("GET", "repos/%s/%s/issues" % (cfg.owner, cfg.repo) + _PATH % (issues.PAGE, page))
        if not isinstance(batch, list):
            raise GhError("E_GH_BAD_BODY", None, "the newest-issues list is not a list")
        for d in batch:
            body = d.get("body") if isinstance(d, dict) else None
            if not isinstance(body, str) or _HINT not in body or mine not in body or not (ids := set(_TASK_RE.findall(body))):
                continue
            if "pull_request" in d or d.get("id") == story.id:
                continue
            issue = issues._issue(d, None)
            if issue.id in kid_ids:
                continue
            if not me:
                me.append(gh.viewer().id)
            if issue.author.id != me[0] and LABEL not in (issues._fold(x) for x in issue.labels):
                continue
            if issue.parent is not None and not issues._same(issue.parent, story.ref):
                raise PushError("E_TASK_ELSEWHERE", "#%d carries a task marker of this story but is a sub-issue of %s" % (issue.ref.number, comments._show(issue.parent)))
            take(issue, ids)
        if len(batch) < issues.PAGE:
            break
    return found, len(kids)


def _op(action, task_id, title, issue=None, **more):
    return {"action": action, "task_id": task_id, "title": title, "issue": issue, "number": None if issue is None else issue.ref.number,
            "old_title": None, "retitle": False, "cut": False, "rel": "new", "item": "-", "item_id": None, **more}


def plan_push(gh, cfg, story, tasks, board):
    """The ops, read only, one per task in tasks.md order (create | update | unchanged) and then one `orphan` per marked issue that tasks.md
    no longer lists (reported, never touched). `board` is None when no Project is configured."""
    _check_tasks(tasks)
    found, n_kids = _existing(gh, cfg, story)
    ops = []
    for t in tasks:
        (title, cut), issue = _title(t), found.get(t["id"])
        if issue is None:
            ops.append(_op("create", t["id"], title, cut=cut, item="add" if board else "-"))
            continue
        op = _op("unchanged", t["id"], title, issue, cut=cut, rel="linked" if issue.parent is not None else "link")
        if issue.title != title:
            op.update(retitle=True, old_title=issue.title)
        if board is not None:
            op["item_id"] = projects.item_for(gh, board, issue.node_id)
            if op["item_id"] is None:
                op["item"] = "add"
            elif projects._status_of(gh, op["item_id"]) is None:  # a resume: the item was added and the Status write never happened
                op["item"] = "status"
            else:
                op["item"] = "ok"
        if op["retitle"] or op["rel"] == "link" or op["item"] in ("add", "status"):
            op["action"] = "update"
        ops.append(op)
    listed = {t["id"] for t in tasks}
    ops += [_op("orphan", tid, i.title, i, rel="linked" if i.parent is not None else "link") for tid, i in found.items() if tid not in listed]
    adding = sum(o["rel"] in ("new", "link") for o in ops if o["action"] in ("create", "update"))
    if n_kids + adding > MAX_TASKS:
        raise PushError("E_SUB_ISSUE_LIMIT", "#%d has %d sub-issues and this push would add %d; GitHub allows %d; nothing was sent" % (
            story.ref.number, n_kids, adding, MAX_TASKS))
    return ops


def _cell(text):
    """A title as one table cell, whole and injective (the plan is what --expect-plan binds): backslashes are doubled BEFORE the display
    escapes, so an invisible character and the literal text of its escape differ; nothing is cut (too long is refused)."""
    if len(text) > MAX_CELL:
        raise PushError("E_TITLE_TOO_LONG", "a title of %d characters cannot be shown in full in the plan; nothing was sent" % len(text))
    text = text.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")  # `_show` would turn all three into a blank
    return comments._show(text).replace("|", "\\|")


def _digest(ops, label_missing=False):
    """SHA-256 of the exact data behind the plan (not its rendering), so --expect-plan covers it even where a display is lossy;
    the label warning is part of it (a plan approved with the label present no longer matches once it is gone)."""
    rows = [[o[k] for k in ("task_id", "action", "title", "old_title", "number", "rel", "item", "item_id", "cut")] for o in ops]
    if label_missing:
        rows.append(["label_missing", LABEL])
    return hashlib.sha256(json.dumps(rows, ensure_ascii=True).encode("ascii")).hexdigest()


def _rel(o, story):
    return {"new": "new sub-issue of #%d" % story.ref.number, "linked": "sub-issue of #%d" % story.ref.number,
            "link": "link to #%d" % story.ref.number}[o["rel"]]


def _item(o, cfg_todo):
    return {"add": "add to the Project, %s" % cfg_todo, "status": "set %s" % cfg_todo, "ok": "on the Project", "-": "none"}[o["item"]]


def label_missing(gh, cfg, ops):
    """True when the plan creates issues and the repo has no label `ghp:task` (one read, none when nothing is created). GitHub creates a
    label named in an issue only when the token has push access; without it `issues.create` would raise E_LABEL_DROPPED AFTER creating the
    issue. 404 is the only answer that means 'missing'; any other failure, or a reply that is not that label, refuses the plan."""
    if not any(o["action"] == "create" for o in ops):
        return False
    try:
        found = gh.rest("GET", "repos/%s/%s/labels/%s" % (cfg.owner, cfg.repo, urllib.parse.quote(LABEL, safe="")))
    except GhError as e:
        if e.status == 404:
            return True
        raise
    name = found.get("name") if isinstance(found, dict) else None
    if not isinstance(name, str) or issues._fold(name) != issues._fold(LABEL):
        raise GhError("E_GH_BAD_BODY", None, "the label lookup did not return the label %s" % LABEL)
    return False


def render_plan(story, ops, board, todo="Todo", label_missing=False, skipped=()):
    """Counts, then every row that writes or needs a look, then the unchanged ids on one line (the gate shows 190 lines)."""
    counts = {a: sum(o["action"] == a for o in ops) for a in ("create", "update", "unchanged", "orphan")}
    lines = ["# Plan: push tasks to #%d as sub-issues" % story.ref.number, " · ".join("%s %d" % kv for kv in counts.items()), ""]
    if counts["orphan"] and not any(counts[a] for a in ("create", "update", "unchanged")):
        lines += ["\u26a0 tasks.md lists none of the %d task(s) already on the story: was it emptied or rewritten? Check it before approving." % counts["orphan"], ""]
    lines.append("Project: new items start at '%s'" % comments._show(todo) if board is not None
                 else "Project: no Project is configured; no Project items are added")
    if label_missing:
        lines += ["\u26a0 label %s does not exist; GitHub creates it only when the token has push access" % LABEL, ""]
    for i, line in skipped[:20]:  # `skipped`: tasks.not_tasks of tasks.md; nothing is planned for them
        lines += ["⚠ tasks.md line %d is not read as a task (a task is `- [ ] T` and 3 to 9 digits, then a space): %s" % (i, _cell(line[:200]))]
    if len(skipped) > 20:
        lines += ["⚠ ... and %d more such lines" % (len(skipped) - 20)]
    lines += ["", "| action | task | title | issue | sub-issue | project item |", "|---|---|---|---|---|---|"]
    for o in ops:
        if o["action"] == "unchanged":
            continue
        title = _cell(o["title"]) if o["old_title"] is None else "%s → %s" % (_cell(o["old_title"]), _cell(o["title"]))
        title += " (cut to %d)" % MAX_TITLE if o["cut"] else ""
        lines.append("| %s | %s | %s | %s | %s | %s |" % (o["action"], o["task_id"], title, "-" if o["number"] is None else "#%d" % o["number"],
                                                        _rel(o, story), "-" if o["action"] == "orphan" else _item(o, todo)))
    same = [o["task_id"] for o in ops if o["action"] == "unchanged"]
    if same:
        lines += ["", *textwrap.wrap("unchanged: %s (%d)" % (", ".join(same), len(same)), width=100, break_long_words=False, break_on_hyphens=False)]
    lines += ["", "digest: " + _digest(ops, label_missing)]
    return "\n".join(lines) + "\n"


def _retitle(gh, issue, title):
    """PATCH the title only; an unknown outcome is settled by reading the issue (as `issues.close`)."""
    try:
        done = issues._issue(gh.rest("PATCH", issues._path(issue.ref), {"title": title}), issue.ref)
    except GhError as e:
        if not issues.maybe_applied(e):
            raise
        done = issues.reread(e, lambda: issues.get(gh, issue.ref))
        if done.title != title:
            raise
    if done.title != title:
        raise issues.IssueError("E_TITLE_NOT_APPLIED", "#%d has title %s after the change" % (issue.ref.number, comments._show(done.title, 60)), done)
    return done


def _step(op, issue, number, e):
    n = getattr(getattr(e, "issue", None), "ref", None) or getattr(issue, "ref", None)
    return {"task_id": op["task_id"], "number": number if n is None else n.number,
            "error": comments._show(e, 300), "unknown": isinstance(e, GhError) and issues.maybe_applied(e)}


def apply_push(gh, cfg, story, ops, board, pace=None):
    """Do the create and update ops, pacing every write; stop at the first failure. {created, updated, failed}: a failure names the
    issue that exists (if any) so nothing is created twice on the re-run."""
    pace = pace or Pacer()
    res = {"created": [], "updated": [], "failed": []}
    for op in ops:
        if op["action"] not in ("create", "update"):
            continue
        issue, number = op["issue"], op["number"]
        try:
            if op["action"] == "create":
                pace()
                try:
                    issue = issues.create(gh, cfg, op["title"], _body(story, op["task_id"]), [LABEL], story)
                except issues.IssueError as e:
                    if e.code != "E_PARENT_NOT_ATTACHED" or e.issue is None:  # created, parent missing: link it now
                        raise
                    issue = e.issue
                    number = issue.ref.number
                    pace()
                    issues.add_sub_issue(gh, story, issue)
                number = issue.ref.number
            else:
                if op["retitle"]:
                    pace()
                    issue = _retitle(gh, issue, op["title"])
                if op["rel"] == "link":
                    pace()
                    issues.add_sub_issue(gh, story, issue)
            if board is not None and op["item"] in ("add", "status"):
                item = op["item_id"]
                if op["item"] == "add":
                    pace()
                    item = projects.add_item(gh, board, issue)
                pace()
                projects.set_status(gh, board, item, cfg.status_todo)  # Todo, never Done (set_status refuses Done without the gate)
        except (GhError, ValueError) as e:
            res["failed"].append(_step(op, issue, number, e))
            return res
        res["created" if op["action"] == "create" else "updated"].append({"task_id": op["task_id"], "number": number})
    return res


def render_result(res):
    lines = ["created %d, updated %d, failed %d" % tuple(len(res[k]) for k in ("created", "updated", "failed"))]
    for r in res["created"] + res["updated"]:
        lines.append("  %s #%d" % (r["task_id"], r["number"]))
    for f in res["failed"]:
        lines.append("push stopped at %s%s: %s" % (f["task_id"], "" if f["number"] is None else " (issue #%d exists)" % f["number"], f["error"]))
        lines.append("  " + ("outcome unknown: the write may have been applied (GitHub's issue list can lag a few seconds); do not repeat it by hand, wait about 30 s, then re-run ghp push (it reads first and resumes)" if f["unknown"]
                             else "nothing was retried; fix the cause and re-run ghp push, which resumes"))
    return "\n".join(lines)


def register(sub):
    p = sub.add_parser("push", help="mirror tasks.md into sub-issues of a story and add them to the Project (dry run unless --apply)")
    p.add_argument("ref", help="the story: 5, #5, owner/repo#5 or an issue URL")
    p.add_argument("--from", dest="source", metavar="PATH", help="tasks.md (default: the spec dir named in the story's ledger)")
    write_gate.add_write_flags(p)


def _tasks_md(cfg, ref, source):
    """The text of tasks.md, read confined to the current directory (symlinks, devices and big files refused): the PR controls both
    the ledger's spec dir and the file."""
    if source:
        path = Path(source)
    else:
        ledger = dod_file(safe_dir(cfg.dod_path), ref.number)
        md = gate._read(ledger, ".", set())
        spec = ledger_spec_dir(md) if md is not None else None
        if not spec:
            raise ValueError("no --from given and the ledger %s has no readable spec dir; pass --from PATH or run `ghp dod %d --spec-dir DIR`" % (
                comments._show(ledger), ref.number))
        path = Path(spec) / "tasks.md"
    text = gate._read(path, ".", set())
    if text is None:
        raise ValueError("cannot read %s (missing, not a plain file inside the repository, over 1 MiB or not UTF-8)" % comments._show(path, 200))
    return text


def run(args, gh):
    ref = issues._resolve(args.ref)  # config, then the ref: a bad one is refused before any call
    cfg = Config.load()
    try:
        md = _tasks_md(cfg, ref, args.source)
    except ValueError:
        if args.source is None:  # no ledger: a pull request number is named as one, not sent to `ghp dod` (which would then refuse it)
            try:
                issues.get(gh, ref)  # NotAnIssue (E_NOT_AN_ISSUE) for a pull request
            except GhError:
                pass  # unreadable: the ledger refusal stands
        raise
    tasks = parse_tasks(md)
    _check_tasks(tasks)  # too many or bad tasks: refused before the first call
    story = issues.get(gh, ref)  # NotAnIssue is a ValueError: exit 2
    board = None if cfg.project_number is None else projects.discover(gh, cfg)  # one that is set but unreadable refuses here
    ops = plan_push(gh, cfg, story, tasks, board)
    text = render_plan(story, ops, board, cfg.status_todo, label_missing(gh, cfg, ops), not_tasks(md))
    write_gate.arm_or_refuse(args, gh, text, approvals.approve_hook(gh, ref, "push", text))
    print(text)
    if not args.apply:
        return 0
    res = apply_push(gh, cfg, story, ops, board)
    print(render_result(res))
    return 1 if res["failed"] else 0
