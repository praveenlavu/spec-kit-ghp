"""`ghp status REF`: the first unmet stage of a story and the one command that moves it on. Read-only: local files, git and a few GitHub reads
(the issue, its comments, the sub-issue summary, the pull requests tied to it, the Project item's Status); nothing is written anywhere and the
write gate is never armed.

`STAGES` is data: `(id, needs_board, met(ctx), next(ctx))`, in lifecycle order. A board stage needs GitHub: offline, with no `gh`, or after one
failed read it is NOT CHECKED (listed in `not_checked`), never met. A read of the Project that fails for want of the `project` scope marks only the
Project stages not checked (`board` = "scope"), as does a Project that does not match the config (a `ProjectError`: `board` = "config"); every other
failure ends all board reads (`board` = "error"). Every text printed is fixed: stage ids, counts, the issue number (a validated int) and the board
column's NAME, which comes from GitHub and passes `comments._show`; no ledger, spec, decision or comment text and no error message is ever printed (a
degraded board shows one of four fixed codes, see BOARD_NOTES). The sub-issue count is display-only: its failure blanks that line and degrades nothing. A file that cannot
be read (a symlink, outside the working tree, over 1 MiB, not UTF-8) leaves its stage unmet. Process help, not a security boundary: the ledger, the
spec dir and the decisions file come from the branch and can be edited.

`open-at-done` (T26b) is the first stage after `config`: an OPEN issue whose Project item is at Done (reopened: nothing in the Project moves its card
back; `ghp activate` refuses it) is named at once, with a fixed text; `done-state` never counts such an issue as met (it is open).

Child progress is `subIssuesSummary { total completed }` (GraphQL, QUERIES["progress"]); the board column is the NAME of the Status option the
issue's Project item is at (`projects._status_of`). Both are information only: they never decide a stage.

Run awareness (T25b, ado's P4-5): the newest Spec Kit run state of this issue (`.specify/workflows/runs/<run id>/state.json`, a file the agent can edit) says
whether a `ghp-lifecycle` run is paused, failed, aborted or stuck, and the exact command that resumes it. That file is UNTRUSTED: read confined
(`gate._read`: no symlink, no FIFO, a regular file under the working tree, 1 MiB cap), the run directory only under a real `.specify/workflows/runs`, the run
id by Spec Kit's pattern, only known statuses and the shipped step ids (`approvals.STEP_IDS`) accepted, the issue number (`inputs.issue_number`) an int equal
to this issue's (a run of another issue is nothing), `output`, `error`, `execution` and every other field never read, and every printed command a fixed
template (issue number: validated int; run id: regex). The reader and its constants are the ones `ghp approvals sync` uses (`approvals`). `Gh` has no server
clock yet (`approvals._server_now`: the same shortcut), so a running run's age is said to be unchecked unless the client offers `server_now`.

UNVERIFIED live (T26/T34 list): (1) the shape of `subIssuesSummary` on a real issue (the offline schema and the T24 fixtures say `{total, completed}`);
(2) that a token without the `project` scope fails a Project read with INSUFFICIENT_SCOPES (SPIKE Q3 saw it for the field query; the item and
Status queries are assumed to fail alike); (3) that `closedByPullRequestsReferences` lists a PR that was opened with `Closes #n` before it merged
(the `pr-link` stage reads it)."""
import datetime
import json
import os
import re

from . import approvals, comments, decisions, dod, done, gate as gate_mod, issues, prs, projects, push, questions, rules, stack, strict, tasks
from .config import E_ORG_PROJECT, Config, ConfigError, ConfigInvalid
from .gh import GhError, ScopeMissing
from .refs import Ref, RefError, parse_ref

BOARD_NOT_CHECKED = "board-not-checked"  # the answer when every stage that could be read is met but a board stage could not be
STRICT_ONLY = ("stack-profile", "verified", "decisions-closed")
PROJECT_STAGES = ("open-at-done", "board-tasks", "board-sync", "done-state")  # board stages that read the Project (only when project_number is set)
_BOARD_ERRORS = (GhError, ValueError, KeyError, TypeError, AttributeError)  # GitHub unreachable, or an answer that is not what was asked for
BOARD_NOTES = {  # the whole of what is printed about a degraded board: four fixed codes, never an error message
    "offline": "board: not checked (offline)",
    "scope": "board: not checked (no project scope)",
    "config": "board: not checked (the Project does not match the config)",
    "error": "board: not checked (GitHub could not be read)",
}
PROJECT_DOWN = ("scope", "config")  # why only the Project stages are not checked (every other board stage is still read)
CONFIG_FIX = "the Project does not match ghp.config.json (project_number, project_owner or a status_* name): run ghp doctor, then ghp status again"
SCOPE_FIX = "the token lacks the project scope: run `gh auth refresh -s read:project` as the right account, then ghp status again"
QUERIES = {
    "progress": "query($issue: ID!) { node(id: $issue) { ... on Issue { subIssuesSummary { total completed } } } }",
}
_GATE_HEADING = re.compile(r"^### Gate\b[^\r\n]*", re.M)  # linear: anchored at a line start, every quantifier ends at a fixed character
_BLOCKING = re.compile(r"^- blocking:\s*(yes|no)\s*$", re.M | re.I)
_CODE = re.compile(r"E_[A-Z_]{1,60}")
# Run awareness. Constants, not read from the installed workflow.yml (the agent can edit that file): tests/test_workflow.py pins them to workflows/ghp-lifecycle.
RUN_WORKFLOW = approvals.RUN_WORKFLOW
RUN_ID = approvals.RUN_ID
RUN_STATUSES = approvals.RUN_STATUSES
RUNS_DIR = approvals.RUNS_DIR
SHIPPED_STEPS = approvals.STEP_IDS  # the 62 step ids of the shipped workflow (a nested loop step is `<loop>:<step>:<n>`)
SHIPPED_GATES = tuple(g for g in approvals.GATES if g not in ("approve-done", "approve-activate"))  # the 12 `type: gate` steps; done and activate are applied by hand
MAX_RUN_DIRS = 2000
STUCK_AFTER = datetime.timedelta(hours=1)
RUN_UNREADABLE = "Run: {n} run file(s) of this issue are not readable by this ghp version; use specify workflow status"
RUN_IGNORED = "Run: {n} run file(s) ignored (oversized, a symlink or not valid JSON); use specify workflow status"
RUN_TRUNCATED = "Run: older runs not examined (more than 2000 run directories); use specify workflow status"


class _Down(Exception):
    """A board read failed (or the board is not read at all): the board stages are not checked."""


class Ctx:
    def __init__(self, gh, number, repo, offline):
        self.id, self.repo, self.gh, self.reason, self._memo = number, repo, gh, "", {}
        self.down = "offline" if offline or gh is None else None  # why the board is not read: offline | error | scope (Project reads only)
        self.problem = None  # config-invalid | ledger-ambiguous: the verb exits 3 (CI fails closed) after printing the report
        self.config_state, self.org_project = "ok", False
        try:
            self.cfg = Config.load(repo)
        except ConfigInvalid as e:
            self.cfg, self.config_state, self.problem = None, "invalid", "config-invalid"
            self.org_project = E_ORG_PROJECT in str(e)  # named like every other verb names it; the rest of the text is not shown
        except ConfigError:
            self.cfg, self.config_state = None, "missing"
        self.strict_mode = self.cfg is not None and self.cfg.mode == "strict"
        self.ref = None if self.cfg is None else Ref(self.cfg.owner, self.cfg.repo, number)
        if self.ref is None:
            self.down = self.down or "offline"  # nobody to ask without a config

    @property
    def s(self):
        """The strict check's own context: confined reads of the ledger, the spec dir files and the decisions file."""
        if "s" not in self._memo:
            self._memo["s"] = strict._Ctx(None, self.cfg, self.id, self.repo)
        return self._memo["s"]

    def memo(self, key, fn):
        if key not in self._memo:
            self._memo[key] = fn()
        return self._memo[key]

    def board(self, fn, project=False):
        """fn() for a board read. A failure ends all board reads for this run, except a missing Project scope or a Project that does not match the
        config (ProjectError), which end the Project reads only (the stage is then not checked)."""
        if self.down in ("offline", "error") or (project and self.down in PROJECT_DOWN):
            raise _Down
        try:
            return fn()
        except _BOARD_ERRORS as e:
            kind = "scope" if isinstance(e, ScopeMissing) else "config" if isinstance(e, projects.ProjectError) else "error"
            self.down = kind if project and self.down is None else "error"
            raise _Down from None

    def story(self):
        return self.memo("story", lambda: self.board(lambda: issues.get(self.gh, self.ref)))

    def facts(self):
        """The pull requests GitHub ties to the issue (and the rest of the T24 facts query), once."""
        return self.memo("facts", lambda: self.board(lambda: done.facts(self.gh, self.story())))

    def has_project(self):
        return self.cfg.project_number is not None

    def project(self):
        """The Board (Status options checked against the config names), once. Only when project_number is set."""
        return self.memo("project", lambda: self.board(lambda: projects.discover(self.gh, self.cfg), project=True))

    def card(self):
        """`(board, item id | None, (option name, option id) | None)` of the issue's Project item; None when no Project is configured."""
        if not self.has_project():
            return None

        def go():
            board = self.project()
            item = self.board(lambda: projects.item_for(self.gh, board, self.story().node_id), project=True)
            now = None if item is None else self.board(lambda: projects._status_of(self.gh, item), project=True)
            return board, item, now
        return self.memo("card", go)

    def progress(self):
        """`{"total", "completed"}` from the issue's `subIssuesSummary`, once; None when that one query fails or is malformed: the count is display-only,
        so its failure blanks the line and never degrades a stage (the issue read before it is a real board read)."""
        node_id = self.story().node_id

        def read():
            node = self.gh.graphql(QUERIES["progress"], {"issue": node_id})
            node = node.get("node") if isinstance(node, dict) else None
            summary = node.get("subIssuesSummary") if isinstance(node, dict) else None
            if (not isinstance(summary, dict) or type(summary.get("total")) is not int or type(summary.get("completed")) is not int
                    or not 0 <= summary["completed"] <= summary["total"]):
                raise GhError("E_GH_BAD_BODY", None, "unexpected status response: the sub-issue summary has no counts")
            return {"total": summary["total"], "completed": summary["completed"]}
        def safe():
            try:
                return read()
            except _BOARD_ERRORS:
                return None
        return self.memo("progress", safe)

    def tasks(self):
        text = self.s.file("tasks.md")
        return None if text is None else tasks.parse_tasks(text)

    def ops(self):
        """The dry-run plan of `ghp push` (reads only), once; `{"refused": code}` when push itself refuses the tasks."""
        def go():
            board = self.project() if self.has_project() else None
            try:
                return push.plan_push(self.gh, self.cfg, self.story(), self.tasks(), board)
            except push.PushError as e:
                return {"refused": e.code if _CODE.fullmatch(e.code) else "E_PUSH"}
        return self.memo("ops", lambda: self.board(go, project=self.has_project()))


def _n(count, what):
    return f"{count} {what}"


# ---- met(ctx): True, or False with ctx.reason set to a fixed text (else the stage's default in _WHY) ----
def _config(c):
    c.reason = ("no ghp.config.json in this directory" if c.config_state != "invalid" else
                "ghp.config.json is invalid (%s: only a Project owned by a user account is supported)" % E_ORG_PROJECT if c.org_project else
                "ghp.config.json is invalid")
    return c.cfg is not None


def _ledger(c):
    if c.s.ledger is None:
        return False
    if dod.unclosed(c.s.ledger):
        c.reason = ("the ledger has an unclosed code fence or comment in section 1, so the sections after it cannot be read" if dod._repair_at(c.s.ledger) != -1
                    else "the ledger has an unclosed code fence or comment outside section 1: close it by hand (a refresh refuses it)")
        return False
    return True


def _ledger_next(c):
    if c.s.ledger is None:
        return f"speckit.ghp.pull {c.id}"
    return f"ghp dod {c.id} --refresh" if dod._repair_at(c.s.ledger) != -1 else f"ghp status {c.id}"  # by hand, then look again


def _stack_profile(c):
    result, _, findings = stack.show(c.repo, "strict", c.cfg.strict["test_command"])
    c.memo("stack_status", lambda: result["status"])
    return findings == 0


def _stack_profile_next(c):
    return "ghp stack approve" if c.memo("stack_status", lambda: "none") in ("draft", "changed-since-approval") else "speckit.ghp.stack"


def _rules(c):
    led = c.s.ledger
    frm, rev = dod.ledger_rules_from(led), dod.ledger_rules_reviewed(led)
    files = rules.local_files(c.cfg, led, c.repo, base=c.repo)
    cov = rules.coverage(led, {}, files, frm, rev, c.s.decisions, "pr")
    c.memo("rules_cov", lambda: cov)
    if cov["header"] or cov["missing"] or cov["format"]:
        c.reason = ("the ledger has no valid `Rules from:` line" if "rules-from" in cov["header"] else
                    _n(len(cov["missing"]) + len(cov["format"]), "rules file(s) named by `Rules from:` are missing, unusable or in another format"))
    elif "rules-reviewed" in cov["header"]:
        c.reason = "`Rules reviewed:` is malformed: a human repairs the ledger header by hand, then confirms"
    elif cov["changed"]:
        c.reason = _n(len(cov["changed"]), "rule(s) differ from the hash recorded in `Rules reviewed:`: a human re-confirms them in their own terminal")
    else:
        c.reason = _n(len(cov["unconfirmed"]), "in-scope rule(s) are not confirmed yet: run the confirm yourself, no terminal is needed")
    return not (cov["header"] or cov["missing"] or cov["format"] or cov["unconfirmed"] or cov["changed"])


def _rules_next(c):
    cov = c._memo["rules_cov"]
    if "rules-from" in cov["header"]:
        return f"ghp dod {c.id} --refresh"
    if cov["missing"] or cov["format"]:
        return f"ghp rules {c.id}"
    return f"ghp rules {c.id} --confirm"


def _questions_posted(c):
    parsed = questions.parse_questions(c.s.ledger)  # outside the board read: an ambiguous ledger (LedgerError) is not a GitHub failure
    plan = c.board(lambda: questions.plan_post(c.gh, c.ref, parsed))
    return plan["action"] in ("none", "unchanged")


def _questions_answered(c):
    opened = questions.parse_questions(c.s.ledger)["open"]
    if any(q["id"] == "?" for q in opened):
        c.reason = "ledger section 2 has an unclosed code fence or comment, so its questions cannot be read"
    else:
        c.reason = _n(len(opened), "open question(s) in ledger section 2 wait for an answer")
    return not opened


def _plan(c):
    if c.s.spec_dir is None:
        c.reason = "the ledger records no Spec dir, so plan.md cannot be found"
    return c.s.file("plan.md") is not None


def _tasks(c):
    return bool(c.tasks())


def _push_plan(c):
    """The push ops, or False with the reason set when push refuses the tasks."""
    ops = c.ops()
    if isinstance(ops, dict):
        c.reason = f"ghp push refuses tasks.md ({ops['refused']}): fix it, then run ghp push"
        return None
    return ops


def _board_tasks(c):
    ids = [t["id"] for t in c.tasks()]
    if len(set(ids)) != len(ids):
        c.reason = "tasks.md repeats a task id, so the issues cannot be compared"
        return False
    ops = _push_plan(c)
    if ops is None:
        return False
    n = sum(op["action"] == "create" for op in ops)
    c.reason = _n(n, "task(s) in tasks.md have no sub-issue")
    return n == 0


def _tasks_ticked(c):
    n = sum(not t["done"] for t in c.tasks())
    c.reason = _n(n, "unticked task(s) in tasks.md")
    return n == 0


def _board_sync(c):
    ops = _push_plan(c)
    if ops is None:
        return False
    n = sum(op["action"] == "update" for op in ops)
    c.reason = _n(n, "task(s) differ between tasks.md and GitHub (title, sub-issue link or Project item)")
    return n == 0


def _decisions_posted(c):
    s = c.s
    if not s.decisions_ok or s.decisions["unparsed"]:
        c.reason = "the decisions file cannot be read in full (unreadable, a line breaks the grammar, or a fence or comment is open)"
        return False
    todo = decisions.unposted(s.decisions)
    if todo:
        c.reason = _n(len(todo), "decision line(s) not posted to the issue")
        return False
    lines = s.decisions["open"] + [d for d in s.decisions["validated"] if d["kind"] == "override"]
    wanted = {d["posted"]["comment_id"] for d in lines if d["posted"]}
    if not wanted:
        return True
    # a `posted:` marker is a local claim: it counts only when a decisions comment with that id is on the issue (not proof of who wrote it)
    found = c.board(lambda: {x.id for x in comments.fetch(c.gh, c.ref) if x.body.startswith(decisions.MARKER)})
    c.reason = _n(len(wanted - found), "posted marker(s) name a decisions comment that is not on the issue")
    return wanted <= found


def _verified(c):
    found = strict._record_done(c.s) if _landed(c) else strict._record(c.s)
    rule = found[0]["rule"] if found else ""
    c.reason = {"record-missing": "ledger section 4 has no verification record",
                "record-stale": "the verification record is stale: the code changed after it"}.get(
                    rule, "the verification record is incomplete, failing or made with another test command")
    return not found


def _headings(ledger_md, kind):
    """The `### <kind>` heading lines of ledger section 4 (outside fences and comments), a trailing ` (superseded)` removed."""
    log = dod._visible(dod.section(ledger_md, 4) or "")[0]
    return [re.sub(r" \(superseded\)$", "", m.group(0).rstrip()) for m in re.finditer(rf"^### {kind}\b[^\r\n]*", log, re.M)]


def _landed(c):
    """The work is on the default branch: the ledger as committed there (`git show`, read through `strict._blob_at`) holds the latest review (else gate)
    heading of this ledger. A commit that merely names the issue does not count. After a squash or rebase merge the recorded SHAs are no ancestors of
    HEAD, so the staleness of the gate, review and verification record is not judged then (as `ghp done` does)."""
    def go():
        base = strict.default_base(c.repo)
        commit, rel = (strict._resolve_base(c.repo, base) if base else ""), strict._ledger_rel(c.cfg.dod_path, c.id)
        mine = (_headings(c.s.ledger, "Review") or _headings(c.s.ledger, "Gate"))[-1:]
        if not (commit and rel and mine):
            return False
        try:
            raw = strict._blob_at(c.repo, commit, rel, gate_mod.MAX_READ, "the issue ledger at the default branch")
            there = _headings(raw.decode("utf-8"), "Review") + _headings(raw.decode("utf-8"), "Gate") if raw is not None else []
        except (ConfigError, ValueError):  # ConfigInvalid is a ConfigError; UnicodeDecodeError is a ValueError
            return False
        return mine[0] in there
    return c.memo("landed", go)


def _logged_sha(c, heading):
    sha = prs._HEADING_SHA.search(heading.group(0))
    return sha.group(1) if sha and prs._SHA_TEXT.fullmatch(sha.group(1)) else None


def _gate(c):
    log = dod._visible(dod.section(c.s.ledger, 4) or "")[0]
    live = list(_GATE_HEADING.finditer(log))
    if not live:
        c.reason = "ledger section 4 has no gate result"
        return False
    rest = log[live[-1].end():]
    nxt = re.search(r"^#{1,3} ", rest, re.M)
    verdicts = _BLOCKING.findall(rest[:nxt.start()] if nxt else rest)
    sha = _logged_sha(c, live[-1])
    if sha is None or len(verdicts) != 1:
        c.reason = "the latest gate result has no commit or no single `blocking:` line"
        return False
    if verdicts[0].lower() == "yes":
        c.reason = "the latest gate result is blocking"
        return False
    if not _landed(c) and strict._record_stale(c.repo, "HEAD", sha, c.cfg.dod_path, c.s.spec_dir, c.id):
        c.reason = "the gate result is stale: the code changed after it"
        return False
    return True


def _review(c):
    why = prs.review_blocks(c.id, c.cfg.effective_review_block_on() or ["blocker"], "HEAD", c.cfg.dod_path, c.repo, push=True, squash=_landed(c))
    if why is None:
        return True
    c.reason = ("no review is logged in ledger section 4 of HEAD" if why.startswith("no review logged")
                else "the latest review blocks the PR" if why.startswith("latest review blocks")
                else "the review is stale: the code changed after it" if why.startswith("review is stale")
                else "the review could not be checked (a ledger or git problem)")
    return False


def _pr_link(c):
    """A pull request of this repository is tied to the issue (`closedByPullRequestsReferences`, merged or not)."""
    return any(done._own(c.cfg, p) for p in c.facts()["linked"])


def _merged(c):
    """The work has landed on the default branch `origin/HEAD` points at (see `_landed`). Local only: fetch after the merge."""
    if not strict.default_base(c.repo):
        c.reason = "origin/HEAD is not set, so the default branch cannot be compared"
        return False
    c.reason = "the default branch does not hold this issue's latest review or gate result yet (merge the PR, then fetch)"
    return _landed(c)


def _merged_next(c):
    """`git fetch origin`, unless GitHub was read and every pull request of this repository tied to the issue is still open: then the merge comes first."""
    if not strict.default_base(c.repo):
        return "git remote set-head origin --auto"
    try:
        own = [p for p in c.facts()["linked"] if done._own(c.cfg, p)]
    except _Down:
        own = []
    if own and not any(p["merged"] for p in own):
        return "a person merges PR #%d on GitHub, then: git fetch origin" % own[0]["number"]
    return "git fetch origin"


OPEN_AT_DONE = ("the issue is open but its Project item is at Done: the card was moved to Done without closing the issue, or the issue was "
                "reopened (reopening does not move its card); ghp never moves a card out of Done")


def _open_at_done(c):
    """Not met for an OPEN issue whose Project item is at the done option (option id): a reopened story, whose card nothing moves back (seen live,
    T26). `ghp activate` refuses such an item; the card is moved by hand. No Project, an item off the board or without a Status, or a closed issue: met."""
    if c.cfg is None or not c.has_project():
        return True
    issue = c.story()
    card = c.card()
    if issue.state != "open" or card is None or card[2] is None:  # no item has no Status either
        return True
    c.reason = OPEN_AT_DONE
    return card[2][1] != card[0].options[c.cfg.status_done]


def _done_state(c):
    """Closed as completed and, with a Project, the item is at the done option (or is not on the Project: `ghp done` adds nothing there)."""
    issue = c.story()
    if issue.state != "closed":
        c.reason = "the issue is still open"
        return False
    if issue.state_reason != "completed":
        c.reason = "the issue is closed, but not as completed"
        return False
    card = c.card()
    if card is None or card[1] is None:
        return True
    board, _, now = card
    c.reason = "the Project item is not at the done Status"
    return now is not None and now[1] == board.options[c.cfg.status_done]


def _done_state_next(c):
    # `ghp done` refuses in strict mode while a decision is open: the command that closes them comes first
    return f"ghp decisions {c.id}" if c.strict_mode and _open_decisions(c) else f"speckit.ghp.done {c.id}"


def _open_decisions(c):
    s = c.s
    if not s.decisions_ok or s.decisions["unparsed"]:
        return -1
    eff = decisions.effective(s.decisions)
    return len(eff["open"]) + len(eff["rejected"])


def _decisions_closed(c):
    n = _open_decisions(c)
    c.reason = ("the decisions file cannot be read in full" if n < 0 else _n(n, "decision(s) are open or rejected"))
    return n == 0


_WHY = {
    "config": "no valid ghp.config.json", "open-at-done": OPEN_AT_DONE, "ledger": "no readable ledger for this issue (missing, a symlink, outside the working tree, over 1 MiB or not UTF-8)",
    "stack-profile": "the stack profile is not approved, drifts from strict.test_command, or leaves a detected root uncovered",
    "rules": "the business rules are not pulled, or not confirmed in the ledger",
    "questions-posted": "the open questions are not (or no longer) posted to the issue",
    "questions-answered": "questions wait for an answer: the product owner replies on the issue and the next command reads it in, or close them by hand as assumed (docs/quickstart.md)", "plan": "no plan.md in the spec dir", "tasks": "no tasks.md with tasks in the spec dir",
    "board-tasks": "tasks have no sub-issue", "tasks-ticked": "tasks are unticked", "board-sync": "sub-issues differ from tasks.md",
    "decisions-posted": "decisions are not posted to the issue", "verified": "no current verification record",
    "gate": "no current, non-blocking gate result in the ledger", "review": "no current, non-blocking review in the ledger",
    "pr-link": "the issue has no pull request tied to it", "merged": "the pull request is not merged",
    "done-state": "the issue is not closed as completed and at the done Status", "decisions-closed": "decisions are open or rejected",
}
STAGES = [
    ("config", False, _config, lambda c: "ghp doctor" if c.config_state == "invalid" else "ghp setup"),
    ("open-at-done", True, _open_at_done, lambda c: f"move #{c.id} out of Done by hand in the Project, then run ghp activate {c.id}"),
    ("ledger", False, _ledger, _ledger_next),
    ("stack-profile", False, _stack_profile, _stack_profile_next),
    ("rules", False, _rules, _rules_next),
    ("questions-posted", True, _questions_posted, lambda c: f"ghp questions {c.id} --post --plan-out .ghp/plan-questions.md"),
    ("questions-answered", False, _questions_answered, lambda c: f"speckit.ghp.plan {c.id}"),
    ("plan", False, _plan, lambda c: f"speckit.ghp.plan {c.id}"),
    ("tasks", False, _tasks, lambda c: f"speckit.ghp.tasks {c.id}"),
    ("board-tasks", True, _board_tasks, lambda c: f"ghp push {c.id} --plan-out .ghp/plan-push.md"),
    ("tasks-ticked", False, _tasks_ticked, lambda c: f"speckit.ghp.implement {c.id}"),
    ("board-sync", True, _board_sync, lambda c: f"ghp push {c.id} --plan-out .ghp/plan-sync.md"),
    ("decisions-posted", True, _decisions_posted, lambda c: f"ghp decisions {c.id} --post --if-any --plan-out .ghp/plan-decisions.md"),
    ("verified", False, _verified, lambda c: f"ghp strict verify {c.id}"),
    ("gate", False, _gate, lambda c: f"speckit.ghp.gate {c.id}"),
    ("review", False, _review, lambda c: f"speckit.ghp.review {c.id}"),
    ("pr-link", True, _pr_link, lambda c: f"speckit.ghp.pr {c.id}"),
    ("merged", False, _merged, _merged_next),
    ("done-state", True, _done_state, _done_state_next),
    ("decisions-closed", False, _decisions_closed, lambda c: f"ghp decisions {c.id}"),
    ("done", False, lambda c: True, lambda c: "none"),
]


# ---- run awareness: Spec Kit's run state, read as untrusted data ----
def _when(text):
    """A timezone-aware ISO time as UTC, else None."""
    when = approvals._run_when(text)
    return None if when is None else when.astimezone(datetime.timezone.utc)


def _scan(repo, number):
    """`(newest run of this issue or None, unreadable, ignored, truncated)`. A run file that is oversized, a symlink, a FIFO, unparsable or not tied to this
    issue is ignored (the unreadable and unparsable ones are counted into `ignored`); one that is tied to it (workflow id and issue number) but has an unknown
    status, a missing key or an unknown step id counts as `unreadable`. `truncated`: more than MAX_RUN_DIRS run directories, the rest unexamined."""
    base = os.path.realpath(repo)
    runs = os.path.join(base, *RUNS_DIR)
    if os.path.realpath(runs) != runs:  # .specify, workflows or runs is a link: not followed, and counted as one ignored entry
        return None, 0, 1, False
    names, unreadable, ignored, best = [], 0, 0, None
    try:
        with os.scandir(runs) as it:
            for e in it:
                if approvals.valid_run_id(e.name):
                    if e.is_symlink():
                        ignored += 1
                    elif e.is_dir(follow_symlinks=False):
                        names.append(e.name)
    except OSError:
        return None, 0, 0, False
    truncated = len(names) > MAX_RUN_DIRS
    for name in sorted(names)[:MAX_RUN_DIRS]:
        path = os.path.join(runs, name, "state.json")
        if not os.path.lexists(path):
            continue
        text = gate_mod._read(path, base, set())
        try:
            d = json.loads(text) if text is not None else None
        except (ValueError, RecursionError):
            d = None
        if not isinstance(d, dict):
            ignored += 1
            continue
        inp = d.get("inputs")
        n = inp.get(approvals.RUN_INPUT) if isinstance(inp, dict) else None
        if not (d.get("workflow_id") == RUN_WORKFLOW and type(n) is int and n == number):
            continue
        when, state, step = _when(d.get("updated_at")), d.get("status"), d.get("current_step_id", False)
        if (d.get("run_id") != name or not isinstance(state, str) or state not in RUN_STATUSES or when is None
                or not (approvals._step_ok(step, SHIPPED_STEPS) or (step is None and state == "created"))):
            unreadable += 1
            continue
        if best is None or (when, name) > best[:2]:
            best = (when, name, {"run_id": name, "status": state, "step": step or "", "updated_at": when.isoformat()})
    return (best[2] if best else None), unreadable, ignored, truncated


def read_run(repo, number):
    """`{"run_id", "status", "step", "updated_at"}` of the newest `ghp-lifecycle` run of this issue, else None (see `_scan`). The values are validated: the
    run id by Spec Kit's pattern, the status from a fixed list, the step among the shipped workflow's ids (`SHIPPED_STEPS`), `updated_at` re-formatted from
    a parsed time. Nothing else of the file is read or kept."""
    return _scan(repo, number)[0]


def _stuck(c, run):
    """True/False: the run was last updated over 1 h before the SERVER's time; None when there is no server time (never the local clock)."""
    fn = None if c.down else getattr(c.gh, "server_now", None)
    try:
        now = fn() if fn else None
    except Exception:  # noqa: BLE001 - any failure to get the server time means the age is not judged
        return None
    if not isinstance(now, datetime.datetime) or now.tzinfo is None:
        return None
    return now - _when(run["updated_at"]) > STUCK_AFTER


def _comment_mode(repo):
    """True when the TARGET branch says `approval: "comment"` (`approvals.target_trust`); any refusal keeps the terminal wording."""
    try:
        return approvals.target_trust(repo).approval == "comment"
    except Exception:  # noqa: BLE001 - a target that cannot vouch for comment mode is terminal mode
        return False


def _run_notes(c, run, counts, stage):
    notes = []
    if run:
        rid, st, step = run["run_id"], run["status"], run["step"]
        at = f" at {step}" if step else ""
        resume = f"specify workflow resume {rid}"
        if st == "paused":
            gate = step.split(":")[-2 if ":" in step else 0] in SHIPPED_GATES  # a nested step is `<loop>:<step>:<n>`
            if gate and _comment_mode(c.repo):
                notes.append(f"Run {rid}: paused at gate {step}. Approve with the approver's comment, then: ghp approvals sync {rid} --resume")
            else:
                notes.append(f"Run {rid}: paused at gate {step}. Answer it in a terminal: {resume}" if gate else f"Run {rid}: paused{at}. Resume: {resume}")
        elif st == "failed":
            notes.append(f"Run {rid}: failed{at}. Resume (it re-runs that step and everything after it): {resume}")
        elif st == "running":
            over = _stuck(c, run)
            notes.append(f"Run {rid}: running{at}; its age was not checked (no server time)." if over is None else
                         f"Run {rid}: running{at}, last update over 1 h ago. The run may have been killed: resume refuses a running run. If it was, set "
                         f'"status" to "failed" in .specify/workflows/runs/{rid}/state.json by hand, then: {resume}' if over else f"Run {rid}: running{at}.")
        elif st == "aborted":
            notes.append(f"Run {rid}: aborted{at}; resume refuses it. Bring the story up to date with: ghp status {c.id} --catch-up")
        elif st == "completed":
            order = [sid for sid, *_ in STAGES]
            early = stage in order and order.index(stage) < order.index("pr-link")
            notes.append(f"Run {rid}: completed, but the story is at {stage}, before the PR: ghp status {c.id} --catch-up" if early else f"Run {rid}: completed.")
        else:
            notes.append(f"Run {rid}: created, not started.")
    unreadable, ignored, truncated = counts
    notes += [RUN_UNREADABLE.format(n=unreadable)] * bool(unreadable) + [RUN_IGNORED.format(n=ignored)] * bool(ignored) + [RUN_TRUNCATED] * truncated
    return notes


def applicable(cfg):
    """The stage ids that apply to a config: the strict-only stages in strict mode, `rules` when `rules.enabled`."""
    return [sid for sid, *_ in STAGES if (sid not in STRICT_ONLY or cfg.mode == "strict") and (sid != "rules" or cfg.rules["enabled"])]


_STAGE = {row[0]: row for row in STAGES}


def _judge(c, sid):
    """`(ok, reason)` of one stage, once per Ctx: ok is True (met), False (unmet) or None (a board stage that could not be read)."""
    judged = c.memo("judged", dict)
    if sid not in judged:
        _, needs_board, met, _ = _STAGE[sid]
        c.reason = ""
        try:
            if needs_board and (c.down in ("offline", "error") or (c.down in PROJECT_DOWN and sid in PROJECT_STAGES and c.cfg is not None and c.has_project())):
                raise _Down
            judged[sid] = (bool(met(c)), c.reason)
        except _Down:
            judged[sid] = (None, "")
        except dod.LedgerError:  # more than one `## 2.` heading: CI fails closed (the verb exits 3 after printing the report)
            c.problem = "ledger-ambiguous"
            judged[sid] = (False, "the ledger has more than one `## 2.` heading outside code and comments, so its sections cannot be told apart: fix it by hand")
        except Exception as e:  # noqa: BLE001 - a stage that cannot be judged is unmet, with the exception CLASS only
            judged[sid] = (False, f"this stage could not be checked ({type(e).__name__})")
    return judged[sid]


def _follow(c, sid):
    try:
        return _STAGE[sid][3](c)
    except Exception:  # noqa: BLE001
        return "ghp doctor"


def _walk(c):
    """(stage id or None, its next command, why, skipped board stage ids): the first unmet stage in order; a board stage that cannot be read is skipped."""
    keep = set(applicable(c.cfg)) if c.cfg else set(sid for sid, *_ in STAGES)
    skipped = []
    for sid, *_ in STAGES:
        if sid not in keep:
            continue
        ok, why = _judge(c, sid)
        if ok is None:
            skipped.append(sid)
        elif not ok:
            return sid, _follow(c, sid), why or _WHY[sid], skipped
    return None, "", "", skipped


# Catch-up, in the order the workflow runs the steps (implement, decisions, verify, sync, gate, review, PR). A GitHub write is a preview, then the human's
# yes, then the apply with --expect-plan; a stage not in the table lists its own next command.
CATCH_ORDER = ("ledger", "stack-profile", "rules", "questions-posted", "questions-answered", "plan", "tasks", "board-tasks", "tasks-ticked",
               "decisions-posted", "verified", "board-sync", "gate", "review", "pr-link")
CATCH_UP = {
    "questions-posted": ("ghp questions {id} --post --plan-out .ghp/plan-questions.md", "ghp questions {id} --post --apply --expect-plan .ghp/plan-questions.md"),
    "questions-answered": ("speckit.ghp.plan {id}",), "plan": ("speckit.ghp.plan {id}",), "tasks": ("speckit.ghp.tasks {id}",),
    "board-tasks": ("ghp push {id} --plan-out .ghp/plan-push.md", "ghp push {id} --apply --expect-plan .ghp/plan-push.md"),
    "tasks-ticked": ("speckit.ghp.implement {id}",),
    "decisions-posted": ("ghp decisions {id} --post --if-any --plan-out .ghp/plan-decisions.md",
                         "ghp decisions {id} --post --if-any --apply --expect-plan .ghp/plan-decisions.md"),
    "verified": ("ghp strict verify {id}",),
    "board-sync": ("ghp push {id} --plan-out .ghp/plan-sync.md", "ghp push {id} --apply --expect-plan .ghp/plan-sync.md"),
    "gate": ("speckit.ghp.gate {id}",), "review": ("speckit.ghp.review {id}",), "pr-link": ("speckit.ghp.pr {id}",),
}
CATCH_UP_AGAIN = "Run `ghp status {id}` again after each step: the plan-questions round and the `wait-po-answers` gate are not modelled here."
CATCH_UP_UNREAD = "Catch-up: local stages are all met; board stages not checked: run `ghp status {id}` online."


def catch_up(c):
    """The ordered commands that bring the issue to an open PR, each a fixed template with the issue number: only stages that are NOT met. A local stage
    that is met lists nothing (its file is the evidence; a generation command would overwrite it); a board stage that is unmet, or could not be read,
    lists only its preview and apply (a `pr-link` that could not be read lists nothing: the PR may exist). Board stages that could not be read are
    listed only when something else is known to be unmet; with every local stage met the list is empty (the renderer says the board was not checked).
    Strict-only stages appear in strict mode only; an issue past the PR link gets an empty list; without a config the setup command alone."""
    sid, follow, _, _ = c.memo("walk", lambda: _walk(c))
    if c.cfg is None:
        return [follow] if sid else []
    todo, unread = [], []  # (stage, commands) known unmet, and board stages that could not be read
    for stage in CATCH_ORDER:
        if stage not in applicable(c.cfg):
            continue
        ok, _ = _judge(c, stage)
        if ok is None:
            if stage != "pr-link":
                unread.append(stage)
        elif not ok:
            todo.append(stage)
    if "questions-posted" in todo and "questions-answered" in todo:
        todo.remove("questions-answered")  # the product owner answers only after the post: `ghp status` says so again after the apply
    out = []
    for stage in CATCH_ORDER:
        if stage in todo or (todo and stage in unread):
            for cmd in CATCH_UP.get(stage) or (_follow(c, stage),):
                cmd = cmd.format(id=c.id) if stage in CATCH_UP else cmd
                if not out or out[-1] != cmd:
                    out.append(cmd)
    return out


def _extras(c):
    """`(progress, column, on_board, notes)`: the sub-issue counts, the NAME of the Project item's Status option (escaped for display), whether the issue
    is on the Project (None: no Project configured or not read), each None when it could not be read or does not exist, and fixed notes for what is not
    shown. Information only: an unexpected exception is shown as its CLASS only and never escapes; a failed Project read degrades the board like any
    other (the stages then say not checked)."""
    progress = column = on_board = None
    notes = []
    try:
        progress = c.progress()
        if progress is None:
            notes.append("Sub-issues: not shown (unreadable)")
    except _Down:
        pass
    except Exception as e:  # noqa: BLE001 - display only: the class name, never the message
        notes.append(f"Sub-issues: not shown ({type(e).__name__})")
    try:
        card = c.card()
        column = None if card is None or card[2] is None else comments._show(card[2][0])
        on_board = None if card is None else card[1] is not None
    except _Down:
        pass
    except Exception as e:  # noqa: BLE001 - the stages that need the Project must not claim to have been checked
        c.down = c.down or "error"
        notes.append(f"Board column: not shown ({type(e).__name__})")
    return progress, column, on_board, notes


def evaluate(gh, number, repo=".", offline=False, with_catch_up=False):
    """`{"stage", "next", "why", "board_checked", "not_checked", "board", "progress", "column", "on_board", "notes", "problem", "run", "run_notes"}` (plus `"catch_up"`, a list of commands, when
    asked). `stage` is the first stage that is unmet; a board stage that could not be read is skipped and listed in `not_checked`; when nothing readable
    is unmet but a board stage was skipped the stage is `board-not-checked`, never `done`. `board` is why the board was not read (`offline`, `scope`, `config` or
    `error`) or None. `progress` is `{"total", "completed"}` (sub-issues) or None, `column` the Status option name or None, `on_board` False when the
    issue has no item on the Project (the `done-state` stage still counts that as met, as `ghp done` adds nothing there), None when not known, `notes`
    fixed lines about what could not be shown. `problem` is None,
    `config-invalid` or `ledger-ambiguous` (the verb exits 3). `run` is `read_run`'s answer (or None) and `run_notes` the fixed lines about it (see
    `_run_notes`; read-only, fixed templates, the run id a regex match and the issue number an int)."""
    if type(number) is not int or not 1 <= number <= prs.MAX_ID:
        raise ValueError("the issue number must be between 1 and %d" % prs.MAX_ID)
    c = Ctx(gh, number, repo, offline)
    progress, column, on_board, notes = _extras(c) if c.down != "offline" else (None, None, None, [])  # first: a failed read here ends the board reads before any stage is judged
    sid, follow, why, skipped = c.memo("walk", lambda: _walk(c))
    if sid is None and skipped:
        sid, follow = BOARD_NOT_CHECKED, f"ghp status {number}" if c.down == "offline" else "ghp doctor"
        why = {"offline": "the board was not read (offline), so its stages are not checked",
               "scope": SCOPE_FIX, "config": CONFIG_FIX, "error": "GitHub could not be read, so the board stages are not checked"}[c.down]
    elif sid is None:
        sid, follow, why = "done", "none", "every stage is met"
    try:
        run, *counts = _scan(repo, number)
    except Exception:  # noqa: BLE001 - run awareness never breaks the stage answer
        run, counts = None, [0, 0, False]
    todo = catch_up(c) if with_catch_up else None
    res = {"stage": sid, "next": follow, "why": why, "board_checked": c.down is None and not skipped, "not_checked": skipped, "board": c.down,
           "progress": progress, "column": column, "on_board": on_board, "notes": notes, "problem": c.problem, "run": run,
           "run_notes": _run_notes(c, run, counts, sid)}
    if with_catch_up:
        res["catch_up"] = todo
    return res


def render(res, number):
    stage = {BOARD_NOT_CHECKED: "board not checked", "done": "done"}.get(res["stage"], "waiting on stage " + res["stage"])  # an unmet stage, not one reached
    lines = [f"Issue #{number}: {stage}. Next: {res['next']}", f"Why: {res['why']}"]
    if res["progress"] is not None:
        lines.append(f"Sub-issues: {res['progress']['completed']} of {res['progress']['total']} closed")
    if res["column"] is not None:
        lines.append(f"Board column: {res['column']}")
    if res["on_board"] is False:
        lines.append("Board: this issue is not on the Project (ghp done adds nothing there)")
    lines += res["notes"]
    if res["board"]:  # a degraded board is always said, with the stages it left unchecked
        lines.append(BOARD_NOTES[res["board"]] + (f": {', '.join(res['not_checked'])}" if res["not_checked"] else ""))
    lines += res["run_notes"]
    if "catch_up" in res:
        if res["catch_up"]:
            lines += ["Catch-up (in order; after each preview read it and decide before the apply):", *(f"{n}. {cmd}" for n, cmd in enumerate(res["catch_up"], 1)),
                      CATCH_UP_AGAIN.format(id=number)]
        else:
            lines.append(CATCH_UP_UNREAD.format(id=number) if res["board"] else "Catch-up: nothing to catch up.")
    return "\n".join(lines)


def register(sub):
    p = sub.add_parser("status", help="which stage an issue is at and the one command that moves it on (read-only; board stages need GitHub)")
    p.add_argument("ref", help="the story: 5, #5, owner/repo#5 or an issue URL")
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    p.add_argument("--offline", action="store_true", help="do not read GitHub: the board stages are 'not checked', never met")
    p.add_argument("--catch-up", action="store_true", help="also print the ordered commands that bring the issue from its first unmet stage to an open PR")


def _number(text):
    """The issue number. The config is optional here (a report: a missing one is the `config` stage), so a bare number needs no default repository."""
    try:
        cfg = Config.load()
        default = Ref(cfg.owner, cfg.repo, 1)
    except ConfigError:
        default = None
    try:
        try:
            return parse_ref(text, default).number
        except RefError as e:
            if e.code != "E_NEEDS_DEFAULT":
                raise
            return parse_ref(text, Ref("-", "-", 1)).number
    except RefError as e:
        raise ValueError(str(e)) from None


def run(args, gh):
    """Exit 0: it reports, it does not judge. 3: an invalid config or an ambiguous ledger (CI fails closed), after the report is printed. 2: a bad
    reference. Never writes: `gh` stays unarmed."""
    number = _number(args.ref)
    result = evaluate(None if args.offline else gh, number, ".", offline=args.offline, with_catch_up=args.catch_up)
    text = json.dumps(result, indent=2) if args.json else render(result, number)
    print("\n".join(comments._show(line) for line in text.splitlines()))  # everything printed passes the display escape
    return 3 if result["problem"] else 0
