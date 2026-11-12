"""`ghp strict check`: the rules of strict SDD+TDD mode (design D§4). Read-only: it never writes and never runs tests
(`run_at` below is the L2 test runner, called only by `verify`, which writes the verification record `check` validates).

Each phase is its own list of rule functions in PHASES; a new rule is appended to a list without touching `check`.
A rule takes a _Ctx and returns findings `{"rule", "detail", "fix"}`. The ledger and the spec dir come from the PR
in CI, so every file is read through gate._read (regular, <= 1 MiB, confined to the repo, never a symlink), and a
`detail` carries only ids, file names and counts: never ledger, spec or GitHub text."""
import hashlib
import json
import os
import posixpath
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from datetime import date
from pathlib import Path

from . import approvals, comments, decisions, dod, gate, issues, kits, projects, prs, push, questions, rules, stack, tasks
# the git helpers are config's (T23 merged the copies); they are module names here so `strict._git` and friends stay the seam the callers use
from .config import Config, ConfigError, ConfigInvalid, _blob_at, _commit, _git, _has_control, _out, _resolve_base, _rev  # noqa: F401
from .gh import GhError, scrub
from .refs import Ref

_PLACEHOLDER = re.compile(r"_\(\s*fill\s+in", re.I)
_BULLETS = (("Approach", "Approach"), ("Assumptions", "Assumptions"), ("Use cases & edge cases", "Use cases"))
_LABEL = re.compile(r"^\s{0,3}[-*+]\s+\*\*[^*]*\*\*")
_NEXT = re.compile(r"[-*+]\s|#")  # a column-0 bullet or heading ends the bullet being read


def _finding(rule, detail, fix):
    return {"rule": rule, "detail": detail, "fix": fix}


class Refused(ValueError):
    """A strict check that found something: `ghp pr create` and `ghp done` exit 2 with these findings (`.check`) before they write or send."""

    def __init__(self, check):
        self.check = check
        super().__init__("strict check failed: " + "; ".join(f"{f['rule']} ({f['fix']})" for f in check["findings"]))


def require(check):
    """The check result when it is ok, else raises Refused."""
    if not check["ok"]:
        raise Refused(check)
    return check


def base_ref(repo, base):
    """The full ref the PR range starts at: `refs/remotes/origin/<base>` when it exists (a CI checkout has no local branch), else
    `refs/heads/<base>`, else `<base>` unchanged (then unresolvable). Never a short name, so a tag cannot stand in for it."""
    return next((ref for ref in (f"refs/remotes/origin/{base}", f"refs/heads/{base}") if _rev(repo, ref)), base)


def default_base(repo):
    """`strict check --phase pr` without --base: the full ref `origin/HEAD` points at (`refs/remotes/origin/<b>`), or "" when it is not set."""
    out = _out(repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    return out.decode("utf-8", "replace").strip() if out else ""


def head_is_checkout(repo, head):
    """True when the local branch `head` is the commit HEAD is at: strict mode judges the working-tree ledger and decisions file."""
    full = _rev(repo, f"refs/heads/{head}")
    return bool(full) and full == _rev(repo, "HEAD")


class _Ctx:
    def __init__(self, gh, cfg, story_id, repo, phase=None, head="HEAD", base=None, push=False):
        self.gh, self.id, self.repo, self.cfg, self.phase, self.head = gh, story_id, repo, cfg, phase, head
        self.push, self.base = push, base
        self.base_full = _resolve_base(repo, base) if base is not None else ""  # "" = no base given, or one that is no commit here
        # `head` is HEAD, a branch (`pr create --head`) or a commit id (CI): review_head is what prs.review_blocks is given, rev the
        # commit L1 and the record are judged at. A hex id that is no branch becomes the full SHA (resolved by object id, never by a tag).
        self.review_head, self.rev, self.at_commit = head, head, False
        if head != "HEAD":
            full = _rev(repo, f"refs/heads/{head}")
            if not full and re.fullmatch(_HEX, head):
                full = _commit(repo, head) or ""
                self.review_head, self.at_commit = full or head, bool(full)
            self.rev = full or head
        self.ledger = gate._read(Path(repo) / dod.dod_file(self.cfg.dod_path, story_id), repo, set())
        spec_dir = dod.ledger_spec_dir(self.ledger) if self.ledger is not None else None
        self.spec_dir = spec_dir.strip() or None if spec_dir else None  # a CRLF ledger leaves `\r` on the header value
        self._files, self._stack, self._head, self._kits, self._detect = {}, None, None, None, None
        path = Path(repo) / decisions.decisions_file(self.cfg.dod_path, story_id)
        md = gate._read(path, repo, set()) if os.path.lexists(path) else None  # confined like the ledger: no symlink, <= 1 MiB
        self.decisions_md = md or ""
        self.decisions = decisions.parse_decisions(self.decisions_md)
        self.decisions_ok = md is not None or not os.path.lexists(path)  # a missing file is no decisions; an unreadable one is a finding

    def file(self, name):
        """Text of <spec dir>/<name>, or None (no spec dir, missing, symlink, outside the repo, too big, not UTF-8)."""
        if self.spec_dir is None:
            return None
        if name not in self._files:
            self._files[name] = gate._read(Path(self.repo) / self.spec_dir / name, self.repo, set())
        return self._files[name]


def _ledger(c):
    if c.ledger is None:
        return [_finding("ledger-missing", "the ledger is missing or unreadable", f"ghp dod {c.id}")]
    return []


_SPEC_FILES = (("spec.md", "spec-missing", "run speckit.specify (or the stack kit's specify command)"),
               ("plan.md", "plan-missing", "run speckit.ghp.plan"),
               ("tasks.md", "tasks-missing", "run speckit.ghp.tasks"))


def _spec_files(c):
    if c.ledger is None:
        return []
    out = []
    for name, rule, fix in _SPEC_FILES:
        if c.spec_dir is None:
            out.append(_finding(rule, f"the ledger records no Spec dir, so {name} cannot be found",
                                f"set the `Spec dir:` line in the ledger header, then {fix}"))
        elif c.file(name) is None:
            out.append(_finding(rule, f"{name} is missing or unreadable in the spec dir", fix))
    return out


def _bullet_problem(lines, label):
    """True when the section 2 bullet is absent, empty (nothing but its label) or still holds `_(fill in`."""
    i = next((i for i, line in enumerate(lines) if re.match(rf"\s{{0,3}}[-*+]\s+\*\*{re.escape(label)}", line)), None)
    if i is None:
        return True
    text = [_LABEL.sub("", lines[i])]
    for line in lines[i + 1:]:
        if _NEXT.match(line):
            break
        text.append(line)
    body = "\n".join(text)
    return not body.strip() or _PLACEHOLDER.search(body) is not None


def _placeholders(c):
    if c.ledger is None:
        return []
    lines = dod._visible(dod.section(c.ledger, 2) or "")[0].splitlines()  # fences and comments render as nothing
    bad = [name for name, label in _BULLETS if _bullet_problem(lines, label)]
    if not bad:
        return []
    return [_finding("placeholder", f"ledger section 2 is missing, empty or still a placeholder for: {', '.join(bad)}",
                     "fill them in with speckit.ghp.plan; no `_(fill in` may be left in Approach, Assumptions or "
                     "Use cases & edge cases")]


def _open_questions(c):
    if c.ledger is None:
        return []
    n = len(questions.parse_questions(c.ledger)["open"])  # an `assumed:` answer is closed; an unclosed fence counts as open
    if not n:
        return []
    return [_finding("open-question", f"{n} open question(s) in ledger section 2",
                     f"get them answered (ghp questions {c.id} --post), or record the answer as `assumed: ...` and tick the line")]


_BOARD_FIX = "check access with `ghp doctor`, then re-run: strict mode needs the issue, its sub-issues and the board to be readable"


def _board(c):
    text = c.file("tasks.md")
    if text is None:
        return []  # already reported as tasks-missing
    parsed = tasks.parse_tasks(text)
    if not parsed:
        return [_finding("tasks-missing", "tasks.md lists no tasks", "run speckit.ghp.tasks")]
    ids = [t["id"] for t in parsed]
    if len(set(ids)) != len(ids):
        return [_finding("board-unchecked", "tasks.md has duplicate task ids, so the board was not checked",
                         "make every task id in tasks.md unique, then re-run")]
    try:  # read-only: GETs only (the issue, its sub-issues, the newest issues, the board items)
        story = issues.get(c.gh, Ref(c.cfg.owner, c.cfg.repo, c.id))
        board = None if c.cfg.project_number is None else projects.discover(c.gh, c.cfg)
        ops = push.plan_push(c.gh, c.cfg, story, parsed, board)
    except (GhError, ValueError, KeyError, TypeError, OSError) as e:  # ValueError: NotAnIssue, PushError, ProjectError
        return [_finding("board-unchecked", f"the issue, its sub-issues or the board could not be read ({e.__class__.__name__})", _BOARD_FIX)]
    n = sum(op["action"] == "create" for op in ops)
    if not n:
        return []
    return [_finding("board-task-missing", f"{n} task(s) in tasks.md have no task issue", f"ghp push {c.id} --apply")]


def _override_l2(c):
    return f"a human runs `ghp strict override {c.id} l2 --reason \"<why>\"` in their own terminal window"


def _test_command_fix(c):
    """Where a profile exists, strict.test_command is the profile's (a hand edit is drift): approval sets it, or a human waives the rule."""
    res = stack.profile_for(c.cfg, c.repo)
    if res["status"] == "none":
        return "set strict.test_command in ghp.config.json"
    try:
        sets = res["status"] == "approved" and stack.compose_test_command(res["profile"]) is not None
    except ValueError:
        sets = False
    return (f"{_APPROVE} to set it from the profile, or " if sets else "") + _override_l2(c)


def _test_command(c):
    if c.cfg.strict.get("test_command"):
        return []
    return [_finding("test-command", "strict.test_command is not set", _test_command_fix(c))]


# ---- stack profile rules (D§5): `ghp.stack.json` must be approved, unchanged since, and agree with strict.test_command ----
# In CI (cfg.from_target) the profile is the TARGET branch's blob (read once by read_target_trust), never the PR's file; a local config
# reads the working tree. Not overridable (not in _COVERS): an unknown or unapproved stack stops the run. Process enforcement, not a sandbox.
_PROFILE_LABEL = "ghp.stack.json at the target branch"
_LOCK_LABEL = "ghp.kits.lock.json at the target branch"
_HEAD_LABEL = "ghp.stack.json at the head commit"
_APPROVE = "a human runs `ghp stack approve --apply` in their own terminal window (`ghp stack approve` previews it)"


def read_target_trust(cfg, repo):
    """For a strict config taken from the target branch (`cfg.from_target`): reads `ghp.stack.json` and `ghp.kits.lock.json` at
    `cfg.source_commit` through `_blob_at` (256 KiB each) into `cfg.target_profile_blob` / `cfg.target_lock_blob` (None when absent) and
    raises ConfigInvalid, with fixed texts that carry none of the file, when either is unreadable or invalid. A draft or changed profile
    is valid here (the rule refuses it); the lockfile is judged by `stack.lock_error`."""
    raw = _blob_at(repo, cfg.source_commit, stack.PROFILE_FILE, stack.MAX_PROFILE, _PROFILE_LABEL)
    if raw is not None and (res := stack.read_profile(blob=raw))["status"] == "invalid":
        raise ConfigInvalid(f"{_PROFILE_LABEL}: the profile is invalid ({len(res['errors'])} error(s)); fix it on the target branch")
    cfg.target_profile_blob = raw
    raw = _blob_at(repo, cfg.source_commit, "ghp.kits.lock.json", stack.MAX_PROFILE, _LOCK_LABEL)
    if raw is not None and (why := stack.lock_error(raw)):
        raise ConfigInvalid(f"{_LOCK_LABEL}: the lockfile {why}")
    cfg.target_lock_blob = raw


def _head_commit(c):
    if c._head is None:
        c._head = _rev(c.repo, c.rev)
    return c._head


_UNREADABLE = object()  # a head file that cannot be read (symlink, directory, oversize, git failure): never equal to the target's bytes


def _head_blob(repo, head, path, label):
    try:
        return _blob_at(repo, head, path, stack.MAX_PROFILE, label) if head else _UNREADABLE
    except ConfigInvalid:
        return _UNREADABLE


def _head_trust(cfg, repo, head):
    """For a strict target config: what the head commit holds of the trust files, against the target's. `{"profile": read_profile-like dict,
    "differs": the profile or strict.test_command differs from the target's, "changed": that or the lockfile differs, "lock": the head's lockfile
    (bytes, None when absent, _UNREADABLE), "cmd": the head config's strict.test_command, "config_invalid": the head's ghp.config.json
    exists but cannot be read or fails validation}`. An absent head config counts as no command; an unreadable file as different."""
    raw = _head_blob(repo, head, stack.PROFILE_FILE, _HEAD_LABEL)
    lock = _head_blob(repo, head, "ghp.kits.lock.json", "ghp.kits.lock.json at the head commit")
    try:
        hc, bad = (Config.load_from_ref(head, repo) if head else None), False
    except ConfigInvalid:  # present but unreadable or invalid (the text is never kept): `config-invalid`, not "no command"
        hc, bad = None, True
    cmd = hc.strict.get("test_command") if hc is not None else None
    if raw is _UNREADABLE:
        res = {"status": "unreadable", "profile": None, "errors": []}
    else:
        res = stack.read_profile(blob=raw) if raw is not None else {"status": "none", "profile": None, "errors": []}
    differs = raw != cfg.target_profile_blob or cmd != cfg.strict.get("test_command")
    return {"profile": res, "lock": lock, "differs": differs, "changed": differs or lock != cfg.target_lock_blob, "cmd": cmd,
            "config_invalid": bad}


def _stack_note(cfg, trust):
    """Whether the stack note applies to `_head_trust` result `trust`: the head differs from the target in the profile, `strict.test_command` or
    the lockfile, except that the first lockfile (the target has none) carries its own note instead (`adds_kits_lock`)."""
    return trust["differs"] or (trust["changed"] and not (cfg.target_lock_blob is None and trust["lock"] is not None))


def edits_trust_files(cfg, repo, commit="HEAD"):
    """True when `commit` (the PR head) differs from the target branch in `ghp.stack.json`, `ghp.kits.lock.json` or `strict.test_command`
    (only for a config taken from the target; a first lockfile is `adds_kits_lock`'s instead): the PR comment and `warnings` say to review
    it as code."""
    full = _rev(repo, commit) if cfg.from_target else ""
    return bool(full) and _stack_note(cfg, _head_trust(cfg, repo, full))


def adds_kits_lock(cfg, repo, commit="HEAD"):
    """True when a config taken from the target branch has no `ghp.kits.lock.json` there and `commit` (the PR head) has an entry for it:
    the first lockfile, whose pins nobody has reviewed as code yet (bootstrap)."""
    full = _rev(repo, commit) if cfg.from_target and cfg.target_lock_blob is None else ""
    return bool(full) and _head_blob(repo, full, "ghp.kits.lock.json", "ghp.kits.lock.json at the head commit") is not None


def adoption_notes(cfg, repo, base, commit="HEAD"):
    """`(stack note, kits note)` for a run under the PR's OWN strict config (`cfg.from_target` False) when `base` (the PR's target ref) holds no
    strict config: this PR is the one that turns strict mode on, so its `ghp.stack.json`, `strict.test_command` and `ghp.kits.lock.json` are
    trust files nobody has reviewed as code. The stack note always applies then; the kits note when `commit` holds an `ghp.kits.lock.json` and
    the target has none (as `adds_kits_lock`). `(False, False)` for a target-judged run, an advisory run, no `base` or one that is no commit
    here. Only commits are read; a target config that cannot be read counts as no strict config (a note too many, never one too few)."""
    full = _resolve_base(repo, base) if base and not cfg.from_target and cfg.mode == "strict" else ""
    head = _rev(repo, commit) if full else ""
    if not (full and head):
        return False, False
    try:
        target = Config.load_from_ref(full, repo)
    except ConfigInvalid:
        target = None
    if target is not None and target.mode == "strict":  # the target judges, and `edits_trust_files` covers a PR that edits its files
        return False, False
    lock = "ghp.kits.lock.json"
    return True, _head_blob(repo, head, lock, "ghp.kits.lock.json at the head commit") is not None and _head_blob(repo, full, lock, "ghp.kits.lock.json at the target branch") is None


def _stack_state(c):
    """The profile that judges this run, cached: `{"bootstrap", "status", "profile", "errors"}` (the target's blob in CI, else the working
    tree) plus, in CI, `"head"`: `_head_trust` of the head commit."""
    if c._stack is None:
        cfg = c.cfg
        c._stack = {"bootstrap": bool(cfg.from_target and cfg.target_profile_blob is None), **stack.profile_for(cfg, c.repo), "head": None}
        if cfg.from_target:
            c._stack["head"] = _head_trust(cfg, c.repo, _head_commit(c))
    return c._stack


def _no_test_fix(c):
    return f"remove strict.test_command from ghp.config.json ({_override_l2(c)} waives the unset command), or add a test command to the profile; then {_APPROVE}"


_DRAFT_IT = ("draft it for every detected stack: run `ghp stack init --seed <a> --seed <b>` (one --seed per stack), "
             "or edit a draft and use `--from` (or run speckit.ghp.stack)")
_APPROVE_IT = f"review the draft; then {_APPROVE}"  # a draft exists: it needs approval, not another draft


_CONFIG_BAD = ("ghp.config.json at the head commit exists but cannot be read, or is invalid: merged, it would fail every later run",
               "fix ghp.config.json in the PR; any `ghp` command run on the PR branch names the problem")


def _head_findings(c, s):
    """`config-invalid` when the head's ghp.config.json is present but invalid (its text is never echoed), then `_head_profile_findings`."""
    h = s["head"]
    bad = [_finding("config-invalid", *_CONFIG_BAD)] if h and h["config_invalid"] else []
    return bad + _head_profile_findings(c, s)


def _head_profile_findings(c, s):
    """The head commit's copy, which becomes the target's on merge: when it differs from the target's (or the head config's
    strict.test_command does), it must itself be a valid, approved profile that agrees with the head's strict.test_command. Only adds
    findings; the target's verdict is never replaced. A head without a profile is fine at bootstrap (nothing to lock)."""
    h = s["head"]
    if h is None or not h["differs"]:
        return []
    st, at = h["profile"]["status"], "ghp.stack.json at the head commit"
    n = len(h["profile"]["errors"])
    if st in ("unreadable", "invalid"):
        return [_finding("stack-profile-invalid", f"{at} cannot be read, or is invalid" + ("" if st == "unreadable" else f" ({n} error(s))"),
                         "run `ghp stack show` for the error positions and fix the file: once merged it would fail every later PR")]
    if st == "none":
        return [] if s["bootstrap"] else [_finding("stack-profile-missing", f"{at} is missing: the PR deletes the profile",
                                                  f"restore it, or {_DRAFT_IT}; then {_APPROVE}")]
    if st == "draft":
        return [_finding("stack-profile-draft", f"{at} is a draft: once merged it would fail every later PR",
                         _APPROVE_IT)]
    if st == "changed-since-approval":
        return [_finding("stack-profile-changed", f"{at} changed after it was approved (the recorded hash no longer matches)",
                         f"review the change; then {_APPROVE} to approve it again")]
    try:
        composed = stack.compose_test_command(h["profile"]["profile"])
    except ValueError:
        too_long, composed = True, None
    else:
        too_long = False
    if too_long:
        return [_finding("stack-profile-drift", f"strict.test_command at the head commit differs from the command {at} composes (which is over 500 characters)",
                         f"{_APPROVE} to set it from the profile; do not hand-edit it")]
    if (h["cmd"] or None) == composed:
        return []
    if composed is None:
        return [_finding("stack-profile-drift", f"strict.test_command at the head commit is set, but {at} {stack.N_NO_TEST}", _no_test_fix(c))]
    return [_finding("stack-profile-drift", f"strict.test_command at the head commit differs from the command {at} composes",
                     f"{_APPROVE} to set it from the profile; do not hand-edit it")]


def _stack_profile(c):
    s = _stack_state(c)
    if s["bootstrap"]:
        return _head_findings(c, s)  # no profile on the target yet: the first one is judged for being sound, not against a target
    status, n = s["status"], len(s["errors"])
    if status == "none":
        out = [_finding("stack-profile-missing", "ghp.stack.json is missing", f"{_DRAFT_IT}; then {_APPROVE}")]
    elif status == "invalid":
        out = [_finding("stack-profile-invalid", f"ghp.stack.json is invalid ({n} error(s))",
                        "run `ghp stack show` for the error positions and fix the file")]
    elif status == "draft":
        out = [_finding("stack-profile-draft", "ghp.stack.json is a draft: nobody has approved it", _APPROVE_IT)]
    elif status == "changed-since-approval":
        out = [_finding("stack-profile-changed", "ghp.stack.json changed after it was approved (the recorded hash no longer matches)",
                        f"review the change; then {_APPROVE} to approve it again")]
    else:
        out = []
        try:
            composed = stack.compose_test_command(s["profile"])
        except ValueError:
            out = [_finding("stack-profile-drift", "the command the approved profile composes is longer than 500 characters",
                            "point `test` at a repo script (kind `repo`) in the profile, then re-approve it")]
        else:
            if (c.cfg.strict.get("test_command") or None) != composed:  # no test command in the profile: any command in the config is a hand edit
                out = [_finding("stack-profile-drift", "strict.test_command differs from the command the approved profile composes" if composed is not None
                                else f"strict.test_command is set, but the approved profile {stack.N_NO_TEST}",
                                f"{_APPROVE} to set it from the profile; do not hand-edit it" if composed is not None else _no_test_fix(c))]
    return out + _head_findings(c, s)


def _detected(repo, commit):
    return stack.detect(repo, commit)


def _detect_state(c):
    """`stack.detect` at the commit being judged, once per run; False when git cannot answer."""
    if c._detect is None:
        try:
            c._detect = _detected(c.repo, c.rev)
        except ValueError:
            c._detect = False
    return c._detect


def _ignored_roots_warning(n):
    return gate.ignored_roots_note(n) if n else None


def ignored_roots_note(repo, commit="HEAD"):
    """The warning for the roots at `commit` that no profile can name (detect's `ignored_roots`), or None: a count, no path. It is never a
    finding, so it cannot block a PR; it says the project is outside the profile and should be reviewed as code."""
    try:
        return _ignored_roots_warning(_detected(repo, commit).get("ignored_roots", {}).get("count", 0))
    except ValueError:
        return None


def _stack_unprofiled(c):
    """A high-confidence (root, stack) at the head commit that no profile stack covers: compared by root and id family (see _FAMILY); a
    medium-confidence root is no finding. Printed only through `stack.safe_path`."""
    s = _stack_state(c)
    if s["bootstrap"] or s["profile"] is None:
        return []  # bootstrap: nothing to compare to; no profile: stack-profile-* already says so
    fix = (f"two PRs: first add the root to ghp.stack.json ({_DRAFT_IT}; then {_APPROVE}) and merge it, then add the code")
    found = _detect_state(c)
    if found is False:
        return [_finding("stack-unprofiled", "the stacks at the head commit could not be detected (git could not answer)",
                         "run in a full clone of the repository (fetch-depth: 0), then re-run")]
    new = stack.uncovered(s["profile"], found)
    if not new:
        return []
    shown = ", ".join(f"{stack.safe_path(r, n)} ({i})" for n, (r, i) in enumerate(new[:5], 1))
    return [_finding("stack-unprofiled", f"{len(new)} high-confidence stack root(s) have no stack in the approved profile: {shown}"
                     + (", ..." if len(new) > 5 else ""), fix)]


# ---- kit rules (D§7): `kit-unpinned` / `kit-changed`; not overridable (not in _COVERS). Process enforcement, not a security boundary ----
# Local config: `kits.verify` over the registry, the kit dirs and the agent copies of the working tree against the working-tree lockfile.
# CI (cfg.from_target): the TARGET branch's lockfile (read once by read_target_trust) judges the files git TRACKS at the PR head; the
# registry, the agent copies and untracked files are not read there. No target lockfile (bootstrap): no finding, a note. Findings are
# fixed texts: kit ids match [a-z0-9-]{1,40} or are named by position (#n); no kit, lock or path text is echoed.
_KIT_PIN = "review the kit, then a human runs `ghp kits pin <id> --apply` in their own terminal window (`ghp kits pin <id>` previews it)"
_KIT_REPIN = "review what changed, then a human re-pins it with `ghp kits pin <id> --apply` in their own terminal window"
_KIT_CI_ORDER = "; in CI merge the pin in a PR of its own before the kit change, because the lockfile that judges is the target branch's"
KIT_UNTRACKED = "kit not in the repository: checked locally only"
KIT_NO_MANIFEST = "kit dir without extension.yml"  # a note, never a finding: Spec Kit cannot load it, so nothing runs from it
_KIT_LOCK_BAD = "ghp.kits.lock.json at the head commit cannot be read, or is not a lockfile this version can trust: merged, it would fail every later run"
_KIT_LOCK_GONE = "the PR deletes ghp.kits.lock.json: merged, it would turn these rules into a note (write an empty `kits` object to unpin everything)"
_KIT_LOCK_ENTRY = "ghp.kits.lock.json at the target branch has an entry that cannot be read as a pin"
_KIT_REPLACED = "`.specify` or `.specify/extensions` is a symlink, a submodule or a file at the head commit: the kits cannot be judged"
_KIT_LOOKALIKE = "a tracked path differs from `.specify` or `.specify/extensions` only in case or Unicode form: on a case-insensitive checkout it lands in the kit dirs"
_KIT_GIT = "the kit files of the head commit cannot be read (git could not answer)"


def _kit_ids(labels):
    return ", ".join(labels[:5]) + (f", ... ({len(labels)} in all)" if len(labels) > 5 else "")


def _kit_result(unpinned, changed, problems=(), warnings=(), ci=False):
    """`{"findings", "warnings"}`: one kit-unpinned and one kit-changed finding at most, however many kits (ids, counts and fixed words only)."""
    out = []
    if unpinned:
        out.append(_finding("kit-unpinned", f"{len(unpinned)} kit(s) not pinned in ghp.kits.lock.json: {_kit_ids(unpinned)}", _KIT_PIN + (_KIT_CI_ORDER if ci else "")))
    if changed or problems:
        out.append(_finding("kit-changed", "; ".join(([f"{len(changed)} kit(s) differ from their pin in ghp.kits.lock.json: {_kit_ids(changed)}"] if changed else [])
                                                      + list(problems)), _KIT_REPIN + (_KIT_CI_ORDER if ci else "")))
    return {"findings": out, "warnings": list(warnings)}


def _kits_local(c):
    try:
        res = kits.verify(c.repo, kits.load_lock(c.repo))
    except (kits.KitError, ConfigInvalid):  # an untrustworthy registry, kit file or lockfile is a finding, never a pass
        return _kit_result([], [], ["the kit registry, a kit file or ghp.kits.lock.json cannot be trusted: run `ghp kits verify`"])
    return _kit_result([f["kit"] for f in res["findings"] if f["rule"] == "kit-unpinned"], [f["kit"] for f in res["findings"] if f["rule"] == "kit-changed"])


def _lock_problem(raw):
    """True when the head's lockfile bytes (or the unreadable sentinel) are not a lockfile this version can read."""
    if raw is _UNREADABLE:
        return True
    try:
        kits.read_lock(raw)
    except ConfigInvalid:
        return True
    return False


def _fold(name):
    return unicodedata.normalize("NFKC", name).casefold()


def _lookalikes(repo, head, args, want):
    """True when the tree listing `args` (git ls-tree names) holds an entry that equals `want` only after case and Unicode folding (a
    case-insensitive checkout would put it in the same place); None when git cannot answer."""
    r = _git(repo, "ls-tree", "-z", "--name-only", "--full-tree", "--end-of-options", head, *args)
    if r is None or r.returncode:
        return None
    names = (rec.decode("utf-8", "replace").rsplit("/", 1)[-1] for rec in r.stdout.split(b"\0") if rec)
    return any(n != want and _fold(n) == want for n in names)


def _tracked_kits(repo, head):
    """From git's tree of the 40-hex `head`: (names with a tracked file below `.specify/extensions/`, names with a tracked
    `<name>/extension.yml`, {name: mode of an entry that is itself the name: a file, symlink or submodule where a kit dir would be}, whether
    `.specify` or `extensions` has a case/Unicode lookalike), or "replaced" when `.specify` or `.specify/extensions` is itself a symlink,
    submodule or file (the kits would live elsewhere), or None when git cannot answer. Names are decoded leniently (an odd one never equals
    a kit id)."""
    for path in (".specify", ".specify/extensions"):  # one pathspec per call: two make git list the children of `.specify` too
        top = _git(repo, "ls-tree", "-z", "--full-tree", "--end-of-options", head, "--", path)
        if top is None or top.returncode:
            return None
        for rec in (r for r in top.stdout.split(b"\0") if r):
            meta, _, name = rec.partition(b"\t")
            if name != path.encode():  # git answered about something else: not an answer
                return None
            if meta.split(b" ")[0] != b"040000":
                return "replaced"
    roots, inner = _lookalikes(repo, head, (), ".specify"), _lookalikes(repo, head, ("--", ".specify/"), "extensions")
    if roots is None or inner is None:
        return None
    r = _git(repo, "ls-tree", "-r", "-z", "--full-tree", "--end-of-options", head, "--", ".specify/extensions/")
    if r is None or r.returncode:
        return None
    present, manifests, entries = set(), set(), {}
    for rec in r.stdout.split(b"\0"):
        if not rec:
            continue
        meta, _, raw = rec.partition(b"\t")
        parts = raw.decode("utf-8", "replace").split("/")
        if len(parts) < 3 or parts[:2] != [".specify", "extensions"]:
            return None
        if len(parts) == 3:
            entries[parts[2]] = meta.split(b" ")[0]
            continue
        present.add(parts[2])
        if len(parts) == 4 and parts[3] == "extension.yml":
            manifests.add(parts[2])
    return present, manifests, entries, roots or inner


def _kits_ci(c):
    cfg, head = c.cfg, _head_commit(c)
    hl = _stack_state(c)["head"]["lock"]
    if cfg.target_lock_blob is None:  # bootstrap: nothing to judge against, so a note; the first lockfile must still be sound
        if hl is None:
            return _kit_result([], [])
        return _kit_result([], [], [_KIT_LOCK_BAD] if _lock_problem(hl) else [], [gate.KITS_TRUST_NOTE], ci=True)
    problems = []
    if hl != cfg.target_lock_blob:  # the PR's copy becomes the target's on merge
        if hl is None:
            problems.append(_KIT_LOCK_GONE)
        elif _lock_problem(hl):
            problems.append(_KIT_LOCK_BAD)
    try:
        lock = kits.read_lock(cfg.target_lock_blob)["kits"]
    except ConfigInvalid:
        return _kit_result([], [], problems + [_KIT_LOCK_ENTRY], ci=True)
    tracked = _tracked_kits(c.repo, head) if head else None
    if tracked is None:
        return _kit_result([], [], problems + [_KIT_GIT], ci=True)
    if tracked == "replaced":
        return _kit_result([], [], problems + [_KIT_REPLACED], ci=True)
    present, manifests, entries, lookalike = tracked
    if lookalike:
        problems.append(_KIT_LOOKALIKE)
    own = kits.OWN_EXTENSION  # the toolkit's own `ghp` is reviewed as code, not pinned (a case variant such as `GHP` is no id and stays reported)
    present, manifests = present - {own}, manifests - {own}
    odd = {n for n, mode in entries.items() if mode in (b"120000", b"160000") and not n.startswith(".") and n != own}  # symlinked or submodule dirs
    # a tracked dir whose name is no kit id (`SF`, a fullwidth or Cyrillic lookalike) would land in a pinned kit dir on a case-insensitive
    # checkout, with or without a manifest; dot names (.cache) and plain files at the top are not kit dirs
    odd |= {n for n in present if not n.startswith(".") and not stack._ID.fullmatch(n)}
    names = sorted((manifests | odd) - set(lock))
    unpinned = [n if stack._ID.fullmatch(n) else f"#{i}" for i, n in enumerate(names, 1)]
    changed, untracked = [], []
    for kit_id, pin in lock.items():
        if kit_id in entries:  # a file, symlink or submodule where the locked kit dir was
            changed.append(kit_id)
        elif kit_id not in present:
            untracked.append(kit_id)
        else:
            try:
                digest = kits.dir_digest_at(c.repo, head, kit_id)
            except kits.KitError:
                digest = "changed"
            if digest is None:
                untracked.append(kit_id)
            elif digest != pin["dir_digest"]:  # includes "changed": a symlink or submodule inside the kit dir, an odd mode or path
                changed.append(kit_id)
    warn = [f"{KIT_UNTRACKED} ({_kit_ids(untracked)})"] if untracked else []
    bare = sorted(n for n in present if stack._ID.fullmatch(n) and n not in manifests and n not in lock)  # locked ones are judged by their digest
    warn += [f"{KIT_NO_MANIFEST} ({_kit_ids(bare)})"] if bare else []
    return _kit_result(unpinned, changed, problems, warn, ci=True)


def _kits_state(c):
    if c._kits is None:
        c._kits = _kits_ci(c) if c.cfg.from_target else _kits_local(c)
    return c._kits


def _kits(c):
    return _kits_state(c)["findings"]


# ---- L1 order evidence (D§5): red <sha> → green <sha> per AC, checked against git ----
_HEX = r"[0-9a-fA-F]{7,40}"
_EVIDENCE = re.compile(rf"(?<![0-9A-Za-z])red[\s:]+({_HEX})(?![0-9A-Za-z])\s*(?:→|->)\s*"
                       rf"green[\s:]+({_HEX})(?![0-9A-Za-z])", re.I)
_TAGGED = re.compile(r"(?<![0-9A-Za-z])(?:red|green)[\s:]+[0-9a-f]{4,}", re.I)  # a second pair makes the cell ambiguous


def parse_evidence(status_cell):
    """`red <sha> → green <sha>` (or `->`; backticks, bold and trailing words tolerated) -> {"red","green"}, else None.
    The cell is PR text: a SHA is 7-40 hex characters or the cell is not evidence, so nothing else reaches git."""
    text = re.sub(r"[`*_]", "", status_cell or "")
    m = _EVIDENCE.search(text)
    return {"red": m[1], "green": m[2]} if m and len(_TAGGED.findall(text)) == 2 else None


def _is_merge(repo, full):
    """True for a merge commit, and when its parents cannot be read (fail closed)."""
    out = _out(repo, "rev-list", "--no-walk", "--parents", full)
    return out is None or len(out.split()) > 2


def _is_ancestor(repo, older, newer):
    r = _git(repo, "merge-base", "--is-ancestor", older, newer)
    return r is not None and r.returncode == 0  # 1 = no, anything else = error: both count as no


def _changed(repo, full, diff_filter=None):
    """Paths the commit changes, NUL-delimited (names are untrusted), or None when git failed."""
    out = _out(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "--root", "--no-renames",
               *([f"--diff-filter={diff_filter}"] if diff_filter else []), full, "--")
    return [n for n in out.decode("utf-8", "replace").split("\0") if n] if out is not None else None


def _dod_files(dod_path, story_id):
    """`<dod_path>/DoD_<id>.md` and `<dod_path>/Decisions_<id>.md` as cwd-relative paths; none for an absolute or `..` dod_path."""
    if not isinstance(dod_path, str) or not dod_path.strip():
        return []
    p = dod_path.strip().replace("\\", "/")
    if p.startswith("/") or ".." in p.split("/"):
        return []
    p = posixpath.normpath(p)
    return [(f"{p}/{n}" if p != "." else n) for n in (f"DoD_{story_id}.md", f"Decisions_{story_id}.md")]


def _spec_tasks(repo, spec_dir, dod_path, head_full):
    """`<spec dir>/tasks.md` (repo-root-relative) when the ledger's untrusted `Spec dir:` is valid in HEAD's tree, else None."""
    try:
        spec = prs.spec_dir_in_tree(spec_dir, dod_path, head_full, str(repo)) if head_full else None
    except prs._Refuse:
        spec = None
    return f"{spec}/tasks.md" if spec else None


def _l1_finding(rule, ac, detail, fix):
    return _finding(rule, f"{ac}: {detail}", fix)


_EVIDENCE_FIX = ("record the commits in the ledger section 3 status cell as `red <sha> → green <sha>`: a red commit with "
                 "only tests, then a green commit with the code")


MAX_ROWS = 200  # section 3 rows L1 will judge: each real row costs git processes, and the ledger (<= 1 MiB) can hold ~15,000. More is one finding, never a truncation


def _too_many_rows(rows):
    """The finding for a ledger over MAX_ROWS rows (fail closed: nothing is judged, no git process starts), else []."""
    if len(rows) <= MAX_ROWS:
        return []
    return [_finding("l1-too-many-rows", f"ledger section 3 has {len(rows)} rows; the order evidence of at most {MAX_ROWS} is checked",
                     f"merge rows so that section 3 has at most {MAX_ROWS} (one row per acceptance criterion)")]


def _memo(cache, fn, *args):
    """`fn(*args)` once per distinct arguments within one L1 pass (the same SHAs recur across rows; each lookup is a git process)."""
    key = (fn.__name__, args)
    if key not in cache:
        cache[key] = fn(*args)
    return cache[key]


def check_l1(rows, repo, head, tests, dod_path, spec_dir, story_id, ac_count=0):
    """L1 for each section 3 row (and each AC-1..AC-<ac_count> that has no row). A `detail` names the AC, the rule and
    a count; it echoes at most 12 hex characters of a SHA and never a file name (PR text and git output are untrusted).
    A red commit may change test files and exactly three others: the story's ledger and decisions file and the valid
    spec dir's tasks.md (a tick); never a directory's worth of files."""
    if over := _too_many_rows(rows):
        return over
    out, cache = [], {}
    head_full = (_out(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{head}^{{commit}}") or b"").decode().strip()
    prefix = (_out(repo, "rev-parse", "--show-prefix") or b"").decode("utf-8", "replace").strip()  # repo below the toplevel
    allowed = {prefix + f for f in _dod_files(dod_path, story_id)} | {_spec_tasks(repo, spec_dir, dod_path, head_full)} - {None}
    is_test = lambda f: any(gate._glob_match(f, g) for g in tests)
    for row in rows:
        ac, ev = row["ac"], parse_evidence(row["status"])
        if ev is None:
            out.append(_l1_finding("evidence-missing", ac, "the status cell has no `red <sha> → green <sha>` pair", _EVIDENCE_FIX))
            continue
        full = {k: _memo(cache, _commit, repo, ev[k]) for k in ("red", "green")}
        bad = [k for k in full if full[k] is None]
        for k in bad:
            out.append(_l1_finding("sha-unknown", ac, f"the {k} commit {ev[k][:12].lower()} is not a commit in this repository "
                                   "(or git could not be run)", "use the SHAs of the real red and green commits (`git log`)"))
        if bad:
            continue
        red, green = full["red"], full["green"]
        merges = [k for k in full if _memo(cache, _is_merge, repo, full[k])]
        for k in merges:
            out.append(_l1_finding("merge-commit", ac, f"the {k} commit is a merge commit (or its parents could not be read)",
                                   "record plain commits: tests only for red, code for green"))
        if merges:
            continue
        if red == green or not _memo(cache, _is_ancestor, repo, red, green):
            why = "red and green are the same commit" if red == green else "the red commit is not an ancestor of the green commit"
            out.append(_l1_finding("red-not-ancestor", ac, why, "commit the failing tests first (red), then the code (green), on one branch"))
        else:
            names, added = _memo(cache, _changed, repo, red), _memo(cache, _changed, repo, red, "AMT")
            if names is None or added is None:
                out.append(_l1_finding("sha-unknown", ac, "the files of the red commit could not be read", "re-run in a full clone"))
            else:
                code = [f for f in names if not is_test(f) and f not in allowed]
                if code:
                    out.append(_l1_finding("red-touches-code", ac, f"the red commit changes {len(code)} non-test file(s) besides the "
                                           "ledger, decisions file and tasks.md", "move the code into the green commit; red holds tests only "
                                           "(and the ledger, decisions file and tasks.md tick)"))
                if not any(is_test(f) for f in added):
                    out.append(_l1_finding("red-no-test", ac, "the red commit adds or changes no test file",
                                           "the red commit must add the failing test"))
        if not (head_full and _memo(cache, _is_ancestor, repo, green, head_full)):
            out.append(_l1_finding("green-not-on-branch", ac, "the green commit is not an ancestor of HEAD" if head_full
                                   else "HEAD does not resolve to a commit", "record a green commit that is on this branch"))
    return out + _rows_missing(rows, ac_count)


def _rows_missing(rows, ac_count):
    have = {r["ac"] for r in rows}
    return [_l1_finding("evidence-missing", f"AC-{n}", "no section 3 row", _EVIDENCE_FIX)
            for n in range(1, ac_count + 1) if f"AC-{n}" not in have]


def _base(c):
    """`base-unknown`: a base was asked for (`--base`, `pr create --base`) and is no commit here. Never overridable, never a pass."""
    if c.base is None or c.base_full:
        return []
    return [_finding("base-unknown", "the base ref is not a commit in this repository (or git could not be run), so the range of the "
                     "branch cannot be told", "pass --base <ref> (CI: origin/<target branch>), or fetch the base branch (a full clone, fetch-depth: 0; `git remote set-head origin -a` sets origin/HEAD)")]


def _no_ac(rows, ac_count):
    """T22b: a ledger with no section 3 row and no AC line has nothing to trace, which the gate reads as `traceability not evaluated` (advisory). Under
    strict that 'not checked' on a floor check is a finding: otherwise deleting the rows would pass every row-driven rule. (the original's `verify` had this; `check` did not.)"""
    return [] if rows or ac_count else [_finding("evidence-missing", "ledger section 3 has no AC row", _EVIDENCE_FIX)]


def _l1(c):
    if c.ledger is None:
        return []  # reported by _ledger
    tests = {**gate.DEFAULT_TESTS, **c.cfg.gate["tests"]}
    rows, n = gate.parse_section3(c.ledger), len(dod.ac_lines(c.ledger))
    return check_l1(rows, c.repo, c.rev, tests, c.cfg.dod_path, c.spec_dir, c.id, n) + _no_ac(rows, n) \
        + (_range_findings(rows, c.repo, c.base_full) if c.base_full else [])  # red and green must lie in base..head


def _evidence(c):
    """The git-free part of L1, for `done` (after a squash merge the red and green commits may be gone): every AC has a row
    whose status cell is a `red <sha> → green <sha>` pair. The record check validates those SHAs against the record."""
    if c.ledger is None:
        return []  # reported by _ledger
    rows, n = gate.parse_section3(c.ledger), len(dod.ac_lines(c.ledger))
    return [_l1_finding("evidence-missing", r["ac"], "the status cell has no `red <sha> → green <sha>` pair", _EVIDENCE_FIX)
            for r in rows if parse_evidence(r["status"]) is None] + _rows_missing(rows, n) + _no_ac(rows, n)


# ---- verification record (D§5): written by `verify`, only validated by `check` ----
_V_HEADING = re.compile(r"^### Verification\b[^\r\n]*", re.M)
_V_HEAD = re.compile(r"### Verification — (\d{4}-\d{2}-\d{2}) @ ([0-9a-f]{7,40})")
_V_AC = re.compile(r"- (AC-\d+): red ([0-9a-f]{12}) exit (\d{1,3}) · green ([0-9a-f]{12}) exit (\d{1,3}) · L1 ok")
_V_COMMAND = re.compile(r"- command-sha256: ([0-9a-fA-F]{12})")
_V_HEADLINE = re.compile(r"- head: ([0-9a-f]{12}) exit (\d{1,3})")
_SUPERSEDED = " (superseded)"
_PR_HEAD = re.compile(rf"(?<![0-9A-Za-z])PR head[\s:]+({_HEX})(?![0-9A-Za-z])", re.I)


def _range_findings(rows, repo, base):
    """`not-in-range` when `base` is no commit here, or a red or green commit is already in `base` (not this branch's work)."""
    base_full = (_out(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{base}^{{commit}}") or b"").decode().strip() \
        if base.strip() else ""
    if not base_full:
        return [_finding("not-in-range", "the base ref is not a commit in this repository (or git could not be run)",
                         "pass an existing branch, tag or commit as --base")]
    if len(rows) > MAX_ROWS:
        return []  # L1 (`_l1`, `verify`) has already refused such a ledger with `l1-too-many-rows`; no git process per row here
    out, cache = [], {}
    for row in rows:
        for k, sha in (parse_evidence(row["status"]) or {}).items():
            full = _memo(cache, _commit, repo, sha)
            if full and _memo(cache, _is_ancestor, repo, full, base_full):
                out.append(_l1_finding("not-in-range", row["ac"], f"the {k} commit is already in the base",
                                       "record red and green commits made after the base: this branch's own work"))
    return out


def _same_sha(cell_sha, recorded12):
    n = min(len(cell_sha), 12)
    return cell_sha[:n].lower() == recorded12[:n]


def _record_stale(repo, head, recorded, dod_path, spec_dir, story_id):
    """Why the record (made at the commit `recorded`) no longer covers `head`, or None when it does: head is that commit
    or a descendant whose later commits touch only the ledger, the decisions file and tasks.md."""
    head_full = (_out(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{head}^{{commit}}") or b"").decode().strip()
    rec_full = _commit(repo, recorded)
    if not head_full:
        return "HEAD does not resolve to a commit"
    if rec_full is None:
        return "the commit the record was made at is not in this repository (or git could not be run)"
    if rec_full == head_full:
        return None
    if not _is_ancestor(repo, rec_full, head_full):
        return "the commit the record was made at is not an ancestor of HEAD"
    try:
        if prs.only_ledger_since(str(repo), rec_full, head_full, dod_path, spec_dir, story_id):
            return None
    except prs._Refuse:
        return "git could not compare the record's commit with HEAD"
    return "later commits change files other than the ledger, the decisions file and tasks.md"


def validate_record(ledger_md, rows, repo, head, dod_path, spec_dir, story_id, base=None, squash_ok=False, command=False):
    """Findings for the latest live `### Verification` block of ledger section 4 (a block in a fence or comment is not
    one): `record-missing`, `record-stale`, `record-incomplete`, `record-failed`, and `not-in-range` with `base`. It runs
    no test and reads only the ledger and git. `squash_ok` (the `done` phase, after a squash merge the red and green
    commits are no ancestors of HEAD): no staleness or range check, only completeness, SHAs against the status cells and exit codes."""
    redo = f"run `ghp strict verify {story_id}` and commit the ledger"
    log = dod._visible(dod.section(ledger_md, 4) or "")[0]
    live = [m for m in _V_HEADING.finditer(log) if not m.group(0).rstrip().endswith(_SUPERSEDED.strip())]
    head_m = _V_HEAD.fullmatch(live[-1].group(0).rstrip()) if live else None
    if head_m is None:
        return [_finding("record-missing", "ledger section 4 has no verification record (a `### Verification — <date> @ <sha>` block)", redo)]
    rest = log[live[-1].end():]
    nxt = re.search(r"^#{1,3} ", rest, re.M)
    found, heads, shas = {}, [], []
    for line in (rest[:nxt.start()] if nxt else rest).splitlines():
        if m := _V_COMMAND.fullmatch(line.strip()):
            shas.append(m[1].lower())
        m = _V_AC.fullmatch(line.strip())
        if m:
            found.setdefault(m[1], []).append(m.groups()[1:])
        if m := _V_HEADLINE.fullmatch(line.strip()):
            heads.append(m.groups())
    out = []
    if command is not False and shas != [_command_hash(command)]:  # `command`: the test command in force (None = unset); False = not judged
        out.append(_finding("record-command-mismatch", "the verification record was not made with the test command in force (the "
                            "target branch's in CI): a weaker command could have passed it", redo))
    if not squash_ok:
        if why := _record_stale(repo, head, head_m[2], dod_path, spec_dir, story_id):
            return [_finding("record-stale", f"the verification record is stale: {why}", redo)]
        if base is not None:
            out += _range_findings(rows, repo, base)
    if squash_ok:  # a `PR head <sha>` in a section 3 commit/PR cell names the commit the PR was made at: the record must be for it
        pr_heads = [h for row in rows for h in _PR_HEAD.findall(re.sub(r"[`*_]", "", row["evidence"]))]
        if any(not _same_sha(h, head_m[2][:12]) for h in pr_heads):
            out.append(_finding("record-incomplete", "the verification record is for a different commit than the PR head recorded in "
                                "ledger section 3", redo))
    if len(heads) != 1 or not _same_sha(head_m[2], heads[0][0]):
        out.append(_finding("record-incomplete", "the verification record has no single `- head:` line naming the recorded head", redo))
    elif int(heads[0][1]) != 0:
        out.append(_finding("record-failed", "the record shows the tests failing at the recorded HEAD",
                            "fix the code so the tests pass at HEAD, then " + redo))
    for row in rows:
        ac, ev, got = row["ac"], parse_evidence(row["status"]), found.get(row["ac"], [])
        if len(got) != 1:
            out.append(_l1_finding("record-incomplete", ac, "the verification record has no line for it" if not got
                                   else "the verification record has more than one line for it", redo))
        elif ev is None or not (_same_sha(ev["red"], got[0][0]) and _same_sha(ev["green"], got[0][2])):
            out.append(_l1_finding("record-incomplete", ac, "the recorded red and green SHAs differ from the section 3 status cell", redo))
        elif int(got[0][1]) == 0 or int(got[0][3]) != 0:
            out.append(_l1_finding("record-failed", ac, "the record shows a red run that passed or a green run that failed",
                                   "make the red tests fail and the green tests pass, then " + redo))
    return out


def _command_hash(command):
    """The 12 hex a record's `- command-sha256:` line holds; unset (None) has none."""
    return hashlib.sha256(command.encode()).hexdigest()[:12] if command else None


def _record(c):
    if c.ledger is None:
        return []  # reported by _ledger
    return validate_record(c.ledger, gate.parse_section3(c.ledger), c.repo, c.rev, c.cfg.dod_path, c.spec_dir, c.id,
                           command=c.cfg.strict.get("test_command"))


def _record_done(c):
    if c.ledger is None:
        return []
    return validate_record(c.ledger, gate.parse_section3(c.ledger), c.repo, "HEAD", c.cfg.dod_path, c.spec_dir, c.id, squash_ok=True,
                           command=c.cfg.strict.get("test_command"))


# ---- decisions and review (D§6, §7): the rules `pr` and `done` add ----
_D_TOKEN = re.compile(r"\bD\d+\b")
_STATUS_ARROW = re.compile(r"(?:→|->)\s*(?:_\(open\)_|validated|rejected)")  # both searches are linear, whatever the line


def _hidden_lines(md, story_id):
    """True when a decision line could be read differently by a person than by `parse_decisions`: a duplicate `D` id (the first
    wins, so a `validated` copy can hide a `rejected` one) or a visible `D<n> … → status` line that is not a decision line
    (`1. `, `> ` or other prefix). The format note `render_file` writes is exempt."""
    note = set(decisions.render_file(story_id).splitlines())
    lines, ids, odd = dod._visible(md)[0].splitlines(), [], False
    for ln in lines:
        m = decisions._CAND.match(ln)
        if m:
            ids.append(m.group(2))
        elif ln not in note:
            d = _D_TOKEN.search(ln)
            odd = odd or decisions.SUP_HIDDEN.search(ln) is not None or (  # a supersedes line behind `1. ` or `> ` is not read
                ("→" in ln or "->" in ln) and d is not None and _STATUS_ARROW.search(ln, d.end()) is not None)
    return odd or len(ids) != len(set(ids))


def _decisions_ok(c):
    if (c.decisions_ok and not c.decisions["unparsed"] and not _hidden_lines(c.decisions_md, c.id)
            and not decisions.supersede_problem(c.decisions)):
        return []
    return [_finding("decisions-invalid", "the decisions file is unreadable (a symlink, outside the repository, over 1 MiB or not UTF-8), "
                     "a D line breaks the grammar or repeats an id, a line that looks like a decision is not one, a `supersedes` line "
                     "names no decision, an override or a higher id, or a code fence or comment is left open in it, so its lines cannot all be judged",
                     f"repair `{decisions.decisions_file(c.cfg.dod_path, c.id)}` (`ghp decisions {c.id}` shows what parses)")]


def _ids_detail(ds):
    return ", ".join(d["id"] for d in ds)


def _decisions_pr(c):
    """`decision-rejected` (any rejected line) and `override-unposted` (an override line without a `posted:` marker that
    still matches its text: the PO must have seen it). Ids only, never decision text."""
    out = _decisions_ok(c)
    rejected = decisions.effective(c.decisions)["rejected"]  # a rejected line a live `supersedes` answers no longer blocks
    if c.cfg.rules["enabled"]:  # a rejected `out of scope:` line is carried out by `_rules` (the rule is back in scope), not a finding of its own
        rejected = [d for d in rejected if not rules.rejected_exclusion(d)]
    if rejected:
        out.append(_finding("decision-rejected", f"the PO rejected: {_ids_detail(rejected)}",
                            "change the work to match the PO's answer, or record a new decision; never re-word or delete a rejected line"))
    unposted = [d for d in decisions.unposted(c.decisions) if d["kind"] == "override"]  # the predicate `--post` uses
    if unposted:
        out.append(_finding("override-unposted", f"override(s) not posted to the story: {_ids_detail(unposted)}",
                            f"post them with `ghp decisions {c.id} --post` (preview, yes, then --apply)"))
    answers = decisions.supersede_unposted(c.decisions)  # every superseder, whatever its status, and every open line it retires
    if answers:
        out.append(_finding("supersedes-unposted", f"decision(s) in a supersedes relation not posted to the story (no `posted:` marker that "
                            f"matches the line): {_ids_detail(answers)}",
                            f"post an open line with `ghp decisions {c.id} --post` (preview, yes, then --apply) before it is resolved; a "
                            "resolved line that was never posted is recorded again as a new open decision"))
    return out


def _decisions_done(c):
    still = decisions.effective(c.decisions)["open"]
    if not still:
        return []
    return [_finding("decision-open", f"decision(s) the PO has not validated or rejected yet: {_ids_detail(still)}",
                     "wait for the PO's reply (`ghp comments`); a human records it with `ghp decisions "
                     f"{c.id} --resolve D<n> --validated|--rejected`")]


# ---- done-time cross-check (ported by T28): with approvers configured, every verdict of the PR-controlled decisions file must be what `--sync` would write now ----
_XC_LINE = decisions.POSTED_LINE
_XC_ORDER = ("marker", "dropped", "verdict")


def _xc_is_posting(c):
    """Does a comment look like a decisions posting, or try to? (`approvals.marked`: a disguised posting is invalid (`-marker`), never ignored.)"""
    return approvals.marked(c, decisions.MARKER)


def _xc_text(sid):
    """(detail, fix) per rule: fixed texts, ids only; nothing of the file or of GitHub is ever echoed."""
    return {
        "marker": ("a `posted:` marker names a comment that is missing, not the kit's, edited, altered or that no longer holds the line as it reads now, or a "
                   "decisions comment is not a plain kit comment",
                   "re-post the lines the PO must see with a non-approver identity (the agent token, never an approver: a poster becomes a kit author and is "
                   "excluded): `ghp decisions %s --post` (preview, yes, then --apply); the approvers then answer the new comment" % sid),
        "dropped": ("a decision a posted comment lists is missing from the decisions file, a line points at an older posting than the newest one, "
                    "a verdict line lost its `via`, or the file cannot be read line by line",
                    "restore the line from git history; never delete or re-word a posted decision, record a change as a new decision"),
        "verdict": ("a validated or rejected line has no approver comment behind it that `ghp decisions --sync` would take now (no `via`, a `via` that "
                    "is not the newest valid command for that decision, the other verb, older than the posting, not an approver's, or a later malformed command)",
                    "an approver comments `/approve D<n>` or `/reject D<n>`, then `ghp decisions %s --sync`; with approvers configured a hand-written verdict "
                    "is not accepted" % sid),
        "truncated": ("not every comment of the issue was read (dropped at the join), so a missing approval or an edit cannot be ruled out",
                      "run it again; the cross-check needs the whole comment list"),
        "unreachable": ("GitHub could not be read for the cross-check (a request failed, an answer was malformed, or the token identity is unknown)",
                        "check access, then run it again: with approvers configured, done needs the issue's comments"),
    }


def _xc_out(rule, sid, ids=()):
    detail, fix = _xc_text(sid)[rule]
    shown = _ids(["D%d" % n for n in sorted(ids) if n])
    return _finding("decisions-xcheck-%s" % rule, "%s: %s" % (detail, shown) if shown else detail, fix)


def _xc_hits(md, rows, approvers, excluded):
    """{rule: {decision numbers (0: no decision)}} (ValueError propagates: the caller fails closed)."""
    hits = {}
    hit = lambda rule, n: hits.setdefault(rule, set()).add(n)  # noqa: E731
    parsed = decisions.parse_decisions(md)
    ids = ["D%s" % m.group(2) for ln in dod._visible(md)[0].splitlines() if (m := decisions._CAND.match(ln))]
    if parsed["unparsed"] or len(ids) != len(set(ids)) or not all(re.fullmatch(r"D[0-9]{1,9}", i) for i in ids):
        return {"dropped": {0}}  # hidden or repeated lines: nothing else can be judged
    rows = approvals.cached(rows)
    byid, newest_post = {c.id: c for c in rows}, {}
    index = decisions.posting_index(rows)
    for c in rows:  # every decisions comment of the issue: its lines must still be in the file
        if not _xc_is_posting(c):
            continue
        lines, key = approvals.kit_lines(c.body, decisions.MARKER), approvals._order(c)
        if not lines or key is None or c.edited:  # not a plain kit comment, an unreadable time or an edit: the lines it held cannot be told
            hit("marker", 0)
            continue
        for ln in lines:
            m = _XC_LINE.match(ln)
            if m:
                n = int(m.group(1))
                newest_post[n] = max(newest_post.get(n, key), key)
    for n in newest_post:
        if "D%d" % n not in ids:
            hit("dropped", n)
    for d in sorted((d for k in ("open", "validated", "rejected") for d in parsed[k]), key=lambda d: int(d["id"][1:])):
        n, resolved = int(d["id"][1:]), d["status"] != "open"
        rule, post = decisions._posting_rule(d, byid, excluded, index)
        if rule == "posting-superseded":  # set back to an older posting
            hit("dropped", n)
            rule = None
        if rule == "not-posted":
            if resolved:  # never shown to the PO: no verdict on it can be an approver's
                hit("verdict", n)
            continue
        if rule:
            hit("marker", n)
            continue
        if newest_post.get(n, approvals._order(post)) > approvals._order(post):  # the line was set back to an older posting
            hit("dropped", n)
        if not resolved:
            continue
        tally = {}
        win = approvals.newest(rows, d["id"], approvers, excluded, tally=tally)
        verdict = decisions._VERDICT.get(win["verb"]) if win else None
        if not d.get("via"):
            hit("verdict", n)
            if verdict == d["status"]:  # an approver's verdict exists: the line was synced and its `via` taken away
                hit("dropped", n)
            continue
        key, pkey = (approvals._wkey(win) if win else None), decisions._posting_key(post)
        if (verdict != d["status"] or str(win["id"]) != d["via"] or tally.get("bad_newer") or key is None or pkey is None or key <= pkey
                or d["by"] != decisions.SYNC_BY or d["note"] not in (decisions._sync_note(win["text"]), "")):
            hit("verdict", n)
    return hits


def xcheck(md, comment_rows, truncated, approvers, excluded, number):
    """Findings (`decisions-xcheck-marker|dropped|verdict|truncated|unreachable`, ids only) for the decisions file text `md` against the issue's comments
    (`comments.fetch`; `truncated`: it dropped comments), the TARGET branch's `approvers` (a set) and `excluded` (`approvals.excluded`: the token identity,
    the kit's authors and the CI identity). Marker: every `posted:` marker names an unedited, non-lossy, plain kit comment of the kit that holds the line as
    it reads now (`decisions._posting_rule`). Dropped: every `D<n>` a decisions comment lists is in the file, pointing at its newest posting. Verdict: every
    resolved line is what `decisions.sync` would write now. A hand-written ledger ANSWER is not checked (accepted residual). An error is a finding."""
    if truncated:
        return [_xc_out("truncated", number)]
    try:
        hits = _xc_hits(md, comment_rows, approvers, excluded)
    except (GhError, ValueError):
        return [_xc_out("unreachable", number)]
    return [_xc_out(rule, number, hits[rule]) for rule in _XC_ORDER if hits.get(rule)]


def xcheck_run(gh, ref, md, approvers, ci_identity=None):
    """`xcheck` over a fresh read: the comments fetched once (`.notes` read first: dropped comments are `-truncated`), the token identity and `ci_identity`
    excluded. Any failure is the `-unreachable` finding."""
    try:
        rows = comments.fetch(gh, ref)
        if rows.notes:
            return [_xc_out("truncated", ref.number)]
        excluded = approvals.excluded(questions._me(gh), rows, ci_identity)
    except (GhError, ValueError):
        return [_xc_out("unreachable", ref.number)]
    return xcheck(md, rows, False, set(approvers), excluded, ref.number)

# ---- business rules (R§7, P3-10): `rules.coverage` over the PR's own files, offline; none of these can be overridden (not in _COVERS) ----
_SAFE = re.compile(r"[A-Za-z0-9:?-]{1,60}")  # rule ids, issue numbers and problem keys only; anything else is shown as `?`
_MAX_IDS = 20


def _ids(items):
    shown = [str(i) if _SAFE.fullmatch(str(i)) else "?" for i in items[:_MAX_IDS]]
    return ", ".join(shown) + (f" and {len(items) - _MAX_IDS} more" if len(items) > _MAX_IDS else "")


def _rules_fixes(sid):
    """The fixed fix text of each rules finding: the commands to run, never any text from a file."""
    return {
        "rule-uncovered": (f"tag the AC row that implements the rule (`[BR-<id>-<n>]` at the end of its criterion cell, with a test that exists); if no AC "
                           f"reflects it the story's AC are extended on GitHub, then `ghp dod {sid} --refresh`; or the PO validates a posted "
                           f"`out of scope: BR-<id>-<n>` decision (`ghp decisions {sid} --add`, then `--post`); never overridable"),
        "rule-unknown": (f"correct or remove the tag in the AC row; if the rule exists on GitHub, pull it with `ghp rules {sid}` and commit the file; "
                         "a tag is exactly `BR-<issue number>-<number>`"),
        "rule-unconfirmed": (f"read the rule in its rules file, then run `ghp rules {sid} --confirm` yourself (the first confirm of a new rule is free: "
                             "no terminal needed)"),
        "rule-changed": (f"STOP: a human re-confirms it with `ghp rules {sid} --confirm` in their own terminal after reading the change in the rules file "
                         f"(re-pull with `ghp rules {sid}` first if the file was edited by hand); re-confirming a changed or dropped hash is never the agent's"),
        "rules-problem": f"fix the Business rules section of the parent issue on GitHub, then `ghp rules {sid}` and commit the file",
        "rules-bad-exclusion": (f"add a correct line, exactly `out of scope: BR-<id>-<n>[, BR-<id>-<n>]`, with `ghp decisions {sid} --add` and post it with "
                                f"`--post` (`ghp decisions {sid}` shows the lines; never edit a line the PO answered); a malformed line excludes nothing"),
        "rules-missing": f"pull the rules with `ghp rules {sid}` and commit the file(s)",
        "rules-format": f"re-pull the file with `ghp rules {sid}` (it is in another rules format) and commit it",
        "rules-from": f"`ghp dod {sid} --refresh` writes the missing or malformed `Rules from:` line",
        "rules-reviewed": f"repair the malformed `Rules reviewed:` line by hand, then a human runs `ghp rules {sid} --confirm` in their own terminal",
    }


def _rules(c):
    """The offline business-rules findings of `rules.coverage` (R§7), only with `rules.enabled` (the target branch's config in CI, like every
    strict rule): `rule-uncovered`, `rule-unknown`, `rule-unconfirmed` (no recorded hash yet: free to confirm), `rule-changed` (changed since confirmed, also a rule kept in scope only by a hash mismatch), `rules-problem` (an error
    problem of a file, or a malformed `out of scope:` line named by its D id), `rules-missing`, `rules-format`, `rules-header`. Inputs are the
    ones `ghp gate` uses (the ledger, the rules files `Rules from:` names, the decisions file, the test files by the gate's globs). The live
    drift check is the gate's: this one makes no request. A detail carries ids, issue numbers and counts only, never rule or ledger text."""
    if not c.cfg.rules["enabled"] or c.ledger is None:  # a missing ledger is reported by _ledger
        return []
    files = rules.local_files(c.cfg, c.ledger, c.repo, c.repo)
    sources = gate.discover_tests(c.repo, {**gate.DEFAULT_TESTS, **c.cfg.gate["tests"]}, set())
    cov = rules.coverage(c.ledger, sources, files, dod.ledger_rules_from(c.ledger), dod.ledger_rules_reviewed(c.ledger), c.decisions, c.phase)
    fix, out = _rules_fixes(c.id), []
    if cov["uncovered"]:
        out.append(_finding("rule-uncovered", "rule(s) that bind the story are cited by no AC row with a test that exists, and no counted exclusion "
                            f"lifts them: {_ids(cov['uncovered'])}", fix["rule-uncovered"]))
    named = [r for r in cov["unknown"] if r != "?"]
    if cov["unknown"]:
        out.append(_finding("rule-unknown", "cited rule id(s) found in no rules file: " + (_ids(named) or "none") +
                            f"; malformed `BR-` tag(s): {len(cov['unknown']) - len(named)}", fix["rule-unknown"]))
    if cov["unconfirmed"]:
        out.append(_finding("rule-unconfirmed", f"rule(s) in scope not confirmed yet: {_ids(cov['unconfirmed'])}", fix["rule-unconfirmed"]))
    if cov["changed"]:
        out.append(_finding("rule-changed", "rule(s) changed since confirmed, or whose text no longer matches the hash in their "
                            f"heading: {_ids(cov['changed'])}", fix["rule-changed"]))
    bad = rules.scope({}, [], c.decisions, c.phase)["bad_exclusions"]
    if cov["problems"] or bad:  # one finding, so the id shows once in the refusal text
        out.append(_finding("rules-problem", "; ".join(
            ([f"error problem(s) in a rules file (<id>:<key>): {_ids(cov['problems'])}"] if cov["problems"] else []) +
            ([f"malformed `out of scope:` decision line(s), which exclude nothing: {_ids(bad)}"] if bad else [])),
            " / ".join(([fix["rules-problem"]] if cov["problems"] else []) + ([fix["rules-bad-exclusion"]] if bad else []))))
    if cov["missing"]:
        out.append(_finding("rules-missing", f"no usable rules file for issue(s): {_ids(cov['missing'])}", fix["rules-missing"]))
    if cov["format"]:
        out.append(_finding("rules-format", f"rules file(s) in another rules-format for issue(s): {_ids(cov['format'])}", fix["rules-format"]))
    if cov["header"]:
        out.append(_finding("rules-header", "malformed or missing ledger header line(s): " + _ids(cov["header"]),
                            "; ".join(fix[h] for h in cov["header"] if h in fix)))
    return out


_TARGET_LEDGER_LABEL = "the story ledger at the target branch"
_DOD_PATH = re.compile(r"[\w. /-]+")  # plain names only: no pathspec magic (`:(glob)`), glob characters or control characters


def _ledger_rel(dod_path, story_id):
    """The repo-relative path of the story's ledger, or None when `dod_path` could name anything but a plain path inside the repository."""
    rel = posixpath.normpath(dod.dod_file(dod_path, story_id).as_posix())
    return rel if _DOD_PATH.fullmatch(rel) and not rel.startswith(("/", "../")) else None


def _reviewed_at(repo, commit, rel):
    """`(ledger exists, {rule id: hash8})` of the story ledger `rel` at the resolved `commit` (`_blob_at`: 1 MiB, regular file; hashes `{}` for no line
    or an untrusted header). ConfigInvalid / UnicodeDecodeError when it cannot be read."""
    raw = _blob_at(repo, commit, rel, gate.MAX_READ, _TARGET_LEDGER_LABEL)
    return (False, {}) if raw is None else (True, dod.ledger_rules_reviewed(raw.decode("utf-8"))["hashes"])


def target_reviewed(dod_path, repo, base, story_id):
    """`{rule id: hash8}` of the `Rules reviewed:` line in the story's ledger on the PR's target `base` (resolved as `_resolve_base` does: a
    branch or a full commit id, never a tag), read through `_blob_at` (1 MiB, regular file). `{}` when the target has no ledger, no such line
    or a header that cannot be trusted (nothing recorded, never a record). None when it cannot be read (git failing, a symlink, oversize,
    not UTF-8, a `dod_path` outside the repository, a base that is no commit here): the caller reports that, it is never a pass."""
    commit, rel = _resolve_base(repo, base), _ledger_rel(dod_path, story_id)
    if not (commit and rel):
        return None
    try:
        return _reviewed_at(repo, commit, rel)[1]
    except (ConfigInvalid, UnicodeDecodeError):
        return None


MAX_HISTORY = 200  # ledger versions of `base..head` that are compared; more is the fixed RECONFIRM_TOO_LONG note, never a partial answer


def reconfirmed_vs_base(dod_path, repo, base, story_id, new_hashes, head="HEAD"):
    """What `rules.reconfirmed` is for the PR range `base..head`: the list `rules.reconfirmed(old, new_hashes)`, or a FIXED note text (str) when
    that cannot be answered. `old` is, per rule id, the first record that differs from the head's across the target branch's ledger and every version of the ledger that
    a commit of `git log --full-history base..head -- <ledger>` made (a first PR has no ledger on the target: the branch's own history is the
    only record, so a commit that deleted the confirmed line and a later free `--confirm` shows). Merge commits are ignored (CI judges the PR's merge
    checkout). Notes: `RECONFIRM_UNREADABLE` (the target ledger: git failing, a symlink, over 1 MiB, not UTF-8, a bad `dod_path`, a base or head that
    is no commit), `RECONFIRM_BRANCH_UNREADABLE` (the log, or a version in the range), `RECONFIRM_TOO_LONG` (more than MAX_HISTORY commits touch
    the ledger) and `RECONFIRM_FIRST_PR` (no ledger on the target, no earlier record in the range, and the head records a rule)."""
    commit, tip, rel = _resolve_base(repo, base), _rev(repo, head), _ledger_rel(dod_path, story_id)
    if not (commit and tip and rel):
        return rules.RECONFIRM_UNREADABLE
    # --no-merges: a merge commit (CI checks out the PR's synthetic merge) repeats the head's record and is nobody's earlier record; the walk still
    # goes through merges, so a record on a merged-in side branch is found
    listed = _out(repo, "log", "--full-history", "--no-merges", "--topo-order", "--reverse", "--format=%H", f"--max-count={MAX_HISTORY + 1}", "--end-of-options",
                  f"{commit}..{tip}", "--", rel)
    shas = listed.decode("ascii", "replace").split() if listed is not None else None
    if shas is None or any(not re.fullmatch(r"[0-9a-f]{40}", x) for x in shas):
        return rules.RECONFIRM_BRANCH_UNREADABLE
    if len(shas) > MAX_HISTORY:
        return rules.RECONFIRM_TOO_LONG
    try:
        target_has_ledger, recorded = _reviewed_at(repo, commit, rel)
    except (ConfigInvalid, UnicodeDecodeError):
        return rules.RECONFIRM_UNREADABLE
    try:
        versions = [recorded] + [_reviewed_at(repo, sha, rel)[1] for sha in shas]  # oldest first: the first record of an id is the baseline
    except (ConfigInvalid, UnicodeDecodeError):
        return rules.RECONFIRM_BRANCH_UNREADABLE
    # An id is listed when ANY earlier version records it with another hash than the head (the target's value first, then the oldest): the order of
    # sibling branches in the log is not a history, so the first value is never trusted to be "the" earlier one.
    old = {}
    for v in versions:
        for rid, h in v.items():
            if h != new_hashes.get(rid):
                old.setdefault(rid, h)
    if not target_has_ledger:  # the head's own commit is no earlier record of it: with no other recorded version there is nothing to compare
        own = 1 if new_hashes and any(v == new_hashes for v in versions[1:]) else 0
        if sum(1 for v in versions if v) - own <= 0:
            return rules.RECONFIRM_FIRST_PR if new_hashes else []  # worth a note only when the head records a rule
    return rules.reconfirmed(old, new_hashes)


def _reconfirmed_warnings(c, target):
    """For `strict check --phase pr|done --base REF` with rules enabled: one warning naming each rule id the target branch's ledger or an earlier
    ledger version of the PR records that this ledger lacks or records with another hash (check the PR diff), or the fixed note text. A note,
    never a finding. [] otherwise (rules off, no `--base`, no ledger, nothing reconfirmed): the output is the baseline."""
    if target is None or not c.cfg.rules["enabled"] or c.ledger is None or c.phase not in ("pr", "done"):
        return []
    res = reconfirmed_vs_base(c.cfg.dod_path, c.repo, target, c.id, dod.ledger_rules_reviewed(c.ledger)["hashes"], c.rev)
    if isinstance(res, str):
        return [res]
    return [f"rules reconfirmed: {rules.reconfirmed_text(res)}; {rules.RECONFIRM_NOTE}"] if res else []


_REVIEW_FIX = "run speckit.ghp.review, fix what it blocks on, commit the ledger"


def _review(c):
    """`review-missing` / `review-blocks` from prs.review_blocks with the strict review list (the project's plus `blocker`). The
    reason it returns can quote PR text, so the finding carries a fixed text: re-run the PR preview for the exact one."""
    if c.ledger is None:
        return []  # reported by _ledger
    why = prs.review_blocks(c.id, c.cfg.effective_review_block_on(), c.review_head, c.cfg.dod_path, c.repo, push=c.push,
                           squash=c.phase == "done", at_commit=c.at_commit)
    if why is None:
        return []
    if why.startswith("no review logged"):
        return [_finding("review-missing", "no review is logged in ledger section 4 of the head commit", _REVIEW_FIX)]
    return [_finding("review-blocks", "the latest review blocks the PR, is stale, or could not be checked (git or ledger problem)", _REVIEW_FIX)]


# ---- L3 fidelity verdicts (D§5): written by the review plane `AC fidelity`, only read by `check` ----
_F_HEADING = re.compile(r"^### AC fidelity\b[^\r\n]*", re.M)
_F_HEAD = re.compile(r"### AC fidelity — (\d{4}-\d{2}-\d{2}) @ ([0-9a-f]{7,40})")
_F_LINE = re.compile(r"- (AC-[0-9]{1,6}): (asserts|partial|does not assert) — (\S(?:.{0,198}\S)?)")  # reason: 1 to 200 characters
_FIDELITY_FIX = "run speckit.ghp.review (its AC fidelity plane writes the block) and commit the ledger"


def parse_fidelity(ledger_md):
    """The latest live `### AC fidelity — <date> @ <sha>` block of ledger section 4 (older ones end in ` (superseded)`; a block in
    a fence, comment, quote or list is not one). Returns `{"block": {"date","sha"} or None, "verdicts": {"AC-1": {"verdict",
    "reason"}}, "duplicates": [ids repeated], "unparsed": bool}`. `unparsed` (fail closed): an unclosed fence or comment, a live
    heading that is not exactly the form above, or any block line that is not exactly `- AC-<n>: asserts|partial|does not
    assert — <reason, 1 to 200 characters>` (a list or quote prefix, another dash, other prose, a fence or comment inside it)."""
    out = {"block": None, "verdicts": {}, "duplicates": [], "unparsed": False}
    sec = dod.section(ledger_md, 4)
    if dod.unclosed(ledger_md):
        out["unparsed"] = True
    if out["unparsed"] or sec is None:
        return out
    raw, log = sec.splitlines(), dod._visible(sec)[0].splitlines()  # 1:1 lines; a fence or comment is blank in `log`
    live = [i for i, line in enumerate(log) if _F_HEADING.match(line) and not line.rstrip().endswith(_SUPERSEDED.strip())]
    if not live:
        return out
    head = _F_HEAD.fullmatch(log[live[-1]].rstrip())
    if head is None:
        out["unparsed"] = True
        return out
    out["block"] = {"date": head[1], "sha": head[2]}
    seen = {}
    for i in range(live[-1] + 1, len(log)):
        if re.match(r"#{1,3} ", log[i]):
            break
        if raw[i].strip() != log[i].strip():  # something in the block is hidden by a fence or comment
            out["unparsed"] = True
        m = _F_LINE.fullmatch(log[i].rstrip())
        if m:
            seen[m[1]] = seen.get(m[1], 0) + 1
            out["verdicts"].setdefault(m[1], {"verdict": m[2], "reason": m[3]})
        elif log[i].strip():
            out["unparsed"] = True
    out["duplicates"] = sorted((k for k, n in seen.items() if n > 1), key=lambda k: int(k[3:]))
    return out


def _fidelity_acs(c):
    """The ACs that need a verdict: every section 3 row and AC-1..AC-<n> for the n AC lines of section 1."""
    rows = {r["ac"] for r in gate.parse_section3(c.ledger)} | {f"AC-{n}" for n in range(1, len(dod.ac_lines(c.ledger)) + 1)}
    return sorted(rows, key=lambda a: (len(a), a))  # section 3 ids come from gate (any length): no int(), shown cut below


def _fidelity(c, stale):
    if c.ledger is None:
        return []  # reported by _ledger
    f = parse_fidelity(c.ledger)
    if f["unparsed"] or f["duplicates"]:  # never overridable and never read as "first wins"
        return [_finding("fidelity-invalid", "the AC fidelity block in ledger section 4 is malformed, repeats an AC "
                         f"({len(f['duplicates'])}), or hides lines in a code fence or comment", "re-run speckit.ghp.review: it rewrites "
                         "the block (one `- AC-<n>: asserts|partial|does not assert — <reason>` line per AC, nothing else)")]
    if f["block"] is None:
        return [_finding("fidelity-missing", "ledger section 4 has no AC fidelity block (a `### AC fidelity — <date> @ <sha>` block)",
                         _FIDELITY_FIX)]
    out = []
    missing = [a for a in _fidelity_acs(c) if a not in f["verdicts"]]
    if missing:
        out.append(_finding("fidelity-missing", f"{len(missing)} AC(s) have no fidelity verdict: {', '.join(a[:12] for a in missing[:20])}", _FIDELITY_FIX))
    fails = [a for a, v in f["verdicts"].items() if v["verdict"] == "does not assert"]
    if fails:
        out.append(_finding("fidelity-fails", f"{len(fails)} AC(s) whose test does not assert the stated behaviour: "
                            f"{', '.join(sorted(fails, key=lambda a: int(a[3:])))}",
                            "make the test assert the AC's stated behaviour (a new red then green), then " + _FIDELITY_FIX))
    if stale and (why := _record_stale(c.repo, c.rev, f["block"]["sha"], c.cfg.dod_path, c.spec_dir, c.id)):
        out.append(_finding("fidelity-stale", f"the AC fidelity verdicts are stale: {why}", _FIDELITY_FIX))
    return out


def _fidelity_pr(c):
    return _fidelity(c, True)


def _fidelity_done(c):  # after a squash merge the reviewed commit is no ancestor of HEAD: no staleness, no git
    return _fidelity(c, False)


def _fidelity_warnings(c):
    """`partial` is allowed: one warning per such AC, not a finding."""
    f = parse_fidelity(c.ledger) if c.ledger is not None else None
    if f is None or f["unparsed"] or f["duplicates"]:
        return []
    return [{"rule": "fidelity-partial", "ac": a} for a, v in sorted(f["verdicts"].items(), key=lambda kv: int(kv[0][3:]))
            if v["verdict"] == "partial"]


PHASES = {
    "implement": [_ledger, _spec_files, _placeholders, _open_questions, _board, _test_command, _stack_profile, _stack_unprofiled, _kits],
    "pr": [_ledger, _base, _l1, _record, _decisions_pr, _rules, _review, _fidelity_pr, _stack_profile, _stack_unprofiled, _kits],
    "done": [_ledger, _evidence, _record_done, _decisions_pr, _decisions_done, _rules, _review, _fidelity_done, _stack_profile, _stack_unprofiled, _kits],
}

# ---- override (D§7): `skip <rule>` lines of kind override; only these findings, never L1 or a missing spec/plan/tasks ----
_COVERS = {"test-command": ("test-command",),
           "l2": ("test-command", "record-missing", "record-stale", "record-incomplete", "record-failed", "head-fails", "green-fails", "red-passes",
                  "timeout", "runner-error", "record-command-mismatch"),
           "open-question": ("open-question",),
           "placeholder": ("placeholder",)}


def overrides(c):
    """{finding rule: id} of the override lines in force: kind override, text exactly `skip <rule>` for an overridable rule, any
    status but rejected; the lowest id wins per rule. Covers every check of the story until it is done."""
    out = {}
    for d in sorted(c.decisions["open"] + c.decisions["validated"], key=lambda d: int(d["id"][1:]), reverse=True):
        rule = d["text"][5:] if d["text"].startswith("skip ") else None
        if d["kind"] == "override" and rule in _COVERS:
            out.update({f: d["id"] for f in _COVERS[rule]})
    return out


def _used(ids):
    return sorted(ids, key=lambda i: int(i[1:]))


def check(gh, cfg, story_id, phase, repo=".", head="HEAD", base=None, push=False, target=None):
    """Read-only. `overrides_used`: the ids of the override lines that let a finding through (the apply paths stamp them used).
    `head`: HEAD, a branch or a commit id; `base` (pr): the ref the PR targets, red and green must lie in base..head; `push`: as
    `pr create --push` (the remote head is not compared); `target`: the CLI's `--base REF` (CI), which adds the adoption notes of
    `adoption_notes` to `warnings` (the default base of the `pr` phase is not a CI target and adds none)."""
    if phase not in PHASES:
        raise ValueError(f"unknown phase (valid: {', '.join(PHASES)})")
    mode = cfg.mode
    if mode != "strict":
        return {"phase": phase, "mode": mode, "ok": True, "findings": [], "note": "mode is advisory"}
    ctx = _Ctx(gh, cfg, story_id, repo, phase, head, base, push)
    cover = overrides(ctx)
    findings, used = [], set()
    for f in (f for rule in PHASES[phase] for f in rule(ctx)):
        if f["rule"] in cover:
            used.add(cover[f["rule"]])
        else:
            findings.append(f)
    out = {"phase": phase, "mode": mode, "ok": not findings, "findings": findings,
           "overrides_used": sorted(used, key=lambda i: int(i[1:]))}
    warnings = _fidelity_warnings(ctx) if phase in ("pr", "done") else []
    warnings += _reconfirmed_warnings(ctx, target)
    head = _stack_state(ctx)["head"]
    adopts, adopts_kits = adoption_notes(ctx.cfg, repo, target, ctx.rev)  # the target is not strict: this PR turns strict mode on
    if adopts or (head and _stack_note(ctx.cfg, head)):  # the PR edits the profile, the lockfile or strict.test_command: review it as code
        warnings.append(gate.TRUST_NOTE)
    warnings += _kits_state(ctx)["warnings"] + ([gate.KITS_TRUST_NOTE] if adopts_kits else [])
    found = _detect_state(ctx)
    if found and (note := _ignored_roots_warning(found.get("ignored_roots", {}).get("count", 0))):  # roots no profile can name: outside the stack rules
        warnings.append(note)
    return {**out, "warnings": warnings} if warnings else out


# ---- L2 test runner (D§5): the project's test command at one commit, in a throwaway checkout ----
# The command is code from the project under test. It runs in a throwaway clone outside the repo with an allow-listed
# environment, stdin closed, a timeout and its own process group; nothing it prints is returned or logged (counts only).
# Toolchain locations only, never a credential directory: CARGO_HOME (credentials.toml), GRADLE_USER_HOME (gradle.properties)
# and PUB_CACHE (older Dart kept credentials.json there) are deliberately absent. Profiles cannot add names.
_ENV_ALLOW = ("PATH", "LANG", "TERM", "TMPDIR", "VIRTUAL_ENV", "JAVA_HOME", "GOPATH", "NODE_PATH", "GOROOT", "FLUTTER_ROOT",
              "DOTNET_ROOT", "ANDROID_HOME", "ANDROID_SDK_ROOT", "RUSTUP_HOME")
_ENV_LC = frozenset(("LC_ADDRESS", "LC_ALL", "LC_COLLATE", "LC_CTYPE", "LC_IDENTIFICATION", "LC_MEASUREMENT", "LC_MESSAGES",
                     "LC_MONETARY", "LC_NAME", "LC_NUMERIC", "LC_PAPER", "LC_TELEPHONE", "LC_TIME"))  # not any LC_* (LC_SFDX_AUTH_URL leaked)
_ENV_SDK_PATHS = ("GOROOT", "FLUTTER_ROOT", "DOTNET_ROOT", "ANDROID_HOME", "ANDROID_SDK_ROOT", "RUSTUP_HOME")  # value must be an absolute path
_ENV_DENY_NAMES = {"SSH_AUTH_SOCK"}  # token-shaped names are caught by the suffix list (X3: no source names a token variable)
_ENV_DENY_SUFFIX = ("_TOKEN", "_PAT", "_SECRET", "_KEY", "_PASSWORD", "_CREDENTIALS")


def scrubbed_env(env, home="/nonexistent"):
    """The environment a test run gets: only the allow-list (PATH, LANG, the standard LC_ locale names, TERM, TMPDIR and the toolchain variables
    that are present), then a deny-list pass for secret-looking names (defence in depth). HOME is `home`, never the real one."""
    out = {k: v for k, v in env.items() if k in _ENV_ALLOW or k in _ENV_LC}
    out = {k: v for k, v in out.items() if k not in _ENV_SDK_PATHS or (os.path.isabs(v) and not _has_control(v))}
    out = {k: v for k, v in out.items() if not (k.upper() in _ENV_DENY_NAMES or k.upper().startswith("AWS_")
                                                  or k.upper().endswith(_ENV_DENY_SUFFIX))}
    return {**out, "HOME": home, "PYTHONDONTWRITEBYTECODE": "1"}


def _git_env(env, home="/nonexistent"):
    """`scrubbed_env` for every git call and for the test command: no GIT_* from the parent (GIT_INDEX_FILE, GIT_DIR,
    GIT_ASKPASS, GIT_SSH*, GIT_CONFIG_*), no system or global git config, no prompts, no replace refs."""
    return {**scrubbed_env(env, home), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_REPLACE_OBJECTS": "1"}


def _kill_group(p):
    # shortcut: after a normal exit the pid is already reaped; its reuse as a group id within microseconds is ignored
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _count_lines(fd, counts, i):
    """Read `fd` in 64 KiB chunks until EOF, keeping only the number of lines (memory stays constant); owns and closes `fd`."""
    n, last = 0, b"\n"
    try:
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                return
            n, last = n + chunk.count(b"\n"), chunk[-1:]
            counts[i] = n + (last != b"\n")
    except OSError:
        pass
    finally:
        os.close(fd)


def _run_group(command, cwd, env, timeout):
    """(exit or None, timed_out, stdout lines, stderr lines, seconds to the process exit); the whole process group dies on a timeout, an interrupt or exit.
    shortcut: a descendant that calls setsid escapes the group; if it also holds the output pipes the drain gives up after
    10 s (its later output is not counted) and the thread stays until it exits."""
    (r_out, w_out), (r_err, w_err), counts, t0 = os.pipe(), os.pipe(), [0, 0], time.monotonic()
    try:
        try:
            p = subprocess.Popen(["/bin/sh", "-c", command], cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=w_out,
                                 stderr=w_err, start_new_session=True)
        finally:
            os.close(w_out)
            os.close(w_err)
    except BaseException:
        os.close(r_out)
        os.close(r_err)
        raise
    readers = [threading.Thread(target=_count_lines, args=(fd, counts, i), daemon=True) for i, fd in enumerate((r_out, r_err))]
    for t in readers:
        t.start()
    timed_out = False
    with p:
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(p)
            p.wait()
        except BaseException:
            _kill_group(p)
            raise
        _kill_group(p)  # background processes the command left behind
    elapsed = time.monotonic() - t0  # not the drain wait below
    end = time.monotonic() + 10
    for t in readers:
        t.join(max(0.0, end - time.monotonic()))
    return (None if timed_out else p.returncode), timed_out, counts[0], counts[1], elapsed


def _rmtree(path):
    """Remove a tree including read-only and mode-000 directories (Go's module cache is 555)."""
    try:
        os.chmod(path, 0o700)
        with os.scandir(path) as it:
            entries = list(it)
    except OSError:
        entries = []
    for e in entries:
        try:
            if e.is_dir(follow_symlinks=False):
                _rmtree(e.path)
            else:
                os.unlink(e.path)
        except OSError:
            pass
    try:
        os.rmdir(path)
    except OSError:
        pass


def run_at(repo, sha, command, timeout):
    """Run `command` (a shell string from the project's config, never built from anything else) at commit `sha` in a
    throwaway clone (`git clone --shared --no-checkout`, origin removed, never fetched) that shares no config, hooks or index
    with the user's repo. Every git call and the command get a scrubbed environment. Returns `{"sha", "exit", "timed_out",
    "seconds", "stdout_lines", "stderr_lines"}`; the output itself is dropped so test output cannot leak a secret. Raises
    ValueError (fixed text) when `sha` is not 7-40 hex characters naming a commit here, or the checkout fails. The temp dir
    is always removed."""
    if not (isinstance(sha, str) and re.fullmatch(_HEX, sha)):
        raise ValueError("sha must be 7 to 40 hex characters")
    if not (isinstance(command, str) and command.strip()) or isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
        raise ValueError("a command and a timeout of at least 1 second are required")
    genv = _git_env(os.environ)
    full = _commit(repo, sha, env=genv)
    top = _out(repo, "rev-parse", "--show-toplevel", env=genv)
    if full is None or top is None:
        raise ValueError("the sha is not a commit in this repository (a shallow clone may lack it, or git could not be run)")
    top = os.path.realpath(top.decode("utf-8", "replace").strip())
    tmp = tempfile.mkdtemp(prefix="ghp-strict-")
    wt, home = os.path.join(tmp, "wt"), os.path.join(tmp, "home")
    try:
        if os.path.commonpath([os.path.realpath(tmp), top]) == top:
            raise ValueError("the temporary directory must be outside the repository")
        os.mkdir(home)
        no_hooks = ["-c", f"core.hooksPath={os.devnull}"]  # a post-checkout hook would run with the environment
        steps = [(top, "clone", "--shared", "--no-checkout", "--quiet", "--end-of-options", top, wt),
                 (wt, "remote", "remove", "origin"),
                 (wt, "checkout", "--quiet", "--detach", full)]  # `full` is git's own 40-hex output; checkout --detach rejects --end-of-options before git 2.4x
        for where, *args in steps:
            r = _git(where, *no_hooks, *args, env=genv, timeout=120)
            if r is None or r.returncode != 0:
                raise ValueError("could not create a temporary checkout of the commit")
        code, timed_out, n_out, n_err, secs = _run_group(command, wt, _git_env(os.environ, home), timeout)
        return {"sha": full, "exit": code, "timed_out": timed_out, "seconds": round(secs, 2),
                "stdout_lines": n_out, "stderr_lines": n_err}
    finally:
        try:
            _rmtree(tmp)
        except RecursionError:  # an absurdly deep tree
            shutil.rmtree(tmp, ignore_errors=True)


def run_configured(cfg, repo, sha):
    """run_at with `strict.test_command` and `strict.test_timeout` from the (already validated) config."""
    return run_at(repo, sha, cfg.strict.get("test_command"), cfg.strict.get("test_timeout"))


# ---- `ghp strict verify` (D§5): L1, then the test command at every red and green commit, then the record ----
def _with_record(md, heading, lines):
    """`md` with the earlier live `### Verification` headings of section 4 marked superseded and the new block appended at
    the end of section 4 (before `## 5.`), in the ledger's own line ending. Nothing else changes."""
    span = dod.section_span(md, 4)
    if span is None or dod.unclosed(md):
        raise ValueError("the ledger has no section 4 (`## 4.`), or an unclosed code fence or comment; nothing written")
    start, end = span
    mask = dod._mask(md)  # same length as md: offsets found here slice the original
    for m in reversed([m for m in _V_HEADING.finditer(mask, start, end) if not m.group(0).rstrip().endswith(_SUPERSEDED.strip())]):
        md, end = md[:m.end()] + _SUPERSEDED + md[m.end():], end + len(_SUPERSEDED)
    eol = "\r\n" if "\r\n" in md else "\n"
    before = md[:end]
    pre = "" if before.endswith(("\n\n", "\r\n\r\n")) else eol if before.endswith("\n") else eol * 2
    return before + pre + eol.join([heading, *lines, ""]) + eol + md[end:]


def _write_record(path, repo, old, heading, lines):
    """Append the record to the ledger at `path` through a temp file + os.replace; refuses (nothing written) when the
    ledger changed while the tests ran."""
    new = _with_record(old, heading, lines)
    if gate._read(path, repo, set()) != old:
        raise ValueError("the ledger changed while the tests ran; nothing written, run it again")
    try:
        decisions._atomic_write(Path(path), new)
    except (OSError, UnicodeError) as e:
        raise ValueError(f"cannot write the ledger ({e.__class__.__name__}); nothing written")


def verify(gh, cfg, story_id, record=True, base=None, repo=".", allow_override=False):
    """L1 for every AC, then the test command at each AC's red commit (must exit non-zero) and green commit (must exit 0),
    once per distinct commit. Strict mode only. With `record` and a pass, appends the verification record to ledger
    section 4. With `record` False (CI) an override in force is the finding `override-in-force` unless `allow_override`. It applies no stack
    or kit rule (D§5): those bind at `check` (`implement`, `pr`, `done`). Returns `{"mode","ok","findings","recorded",...}`; nothing is returned or logged from the tests' output."""
    mode = cfg.mode
    if mode != "strict":
        return {"mode": mode, "ok": True, "findings": [], "note": "mode is advisory"}
    c = _Ctx(gh, cfg, story_id, repo)
    rows = gate.parse_section3(c.ledger) if c.ledger is not None else []
    findings = _ledger(c) + _l1(c) + _test_command(c)
    if base is not None:
        findings += _range_findings(rows, repo, _resolve_base(repo, base))  # the same resolver as `check`: never a tag
    cover, used = overrides(c), set()  # like `check`: the findings an override covers are dropped and the entry reported
    findings = [f for f in findings if f["rule"] not in cover or used.add(cover[f["rule"]])]
    skipped = "test-command" in cover or "record-missing" in cover  # an `l2` / `test-command` override: no run, and no record to fake
    if skipped and not findings:
        used |= {cover[r] for r in ("test-command", "record-missing") if r in cover}
    if used and not record and not allow_override:  # the CI re-run: an override would skip the very run it exists for
        findings.append(_finding("override-in-force", f"the override(s) {', '.join(_used(used))} would let this re-run pass without it",
                                 "remove the override line, or pass --allow-override (the template input `allow-override`) to accept it"))
    if findings:
        return {"mode": mode, "ok": False, "findings": findings, "recorded": False, "overrides_used": _used(used)}
    if skipped:
        return {"mode": mode, "ok": True, "findings": [], "recorded": False, "overrides_used": _used(used),
                "note": "verification skipped: overridden"}
    head = (_out(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", "HEAD^{commit}") or b"").decode().strip()
    if not head:
        raise ValueError("cannot resolve HEAD to a commit; nothing written")
    runs, timeout = {}, c.cfg.strict.get("test_timeout")  # a failed run is cached too: one failure, not one per AC

    def run(full):
        if full not in runs:
            try:
                runs[full] = run_configured(c.cfg, repo, full)
            except ValueError:
                runs[full] = None
        return runs[full]
    lines = []
    for row in rows:
        ac, ev, shown = row["ac"], parse_evidence(row["status"]), []
        for kind in ("red", "green"):
            full = _commit(repo, ev[kind])
            r = run(full)
            if r is None:
                findings.append(_l1_finding("runner-error", ac, f"the {kind} run could not be started: the checkout of the commit failed "
                                            "or git could not be run", "use a full clone (fetch-depth: 0) with a working git, then re-run"))
            elif r["timed_out"]:
                findings.append(_l1_finding("timeout", ac, f"the {kind} run timed out after {timeout} s",
                                            "make the tests faster, or raise strict.test_timeout in ghp.config.json"))
            elif kind == "red" and r["exit"] == 0:
                findings.append(_l1_finding("red-passes", ac, f"the tests pass at the red commit {full[:12]}: a red commit must fail",
                                            "make the red commit's test fail without the code (assert the new behaviour), "
                                            "or correct the red SHA or strict.test_command"))
            elif kind == "green" and r["exit"] != 0:
                findings.append(_l1_finding("green-fails", ac, f"the tests fail at the green commit {full[:12]} (exit {r['exit']})",
                                            "make the green commit pass the test command, or correct the green SHA"))
            else:
                shown.append(f"{kind} {full[:12]} exit {r['exit']}")
        if len(shown) == 2:
            lines.append(f"- {ac}: {' · '.join(shown)} · L1 ok")
    r = run(head)  # the record says `@ <head>`: the tests must pass there too (cached when HEAD is a recorded green)
    if head not in {_commit(repo, parse_evidence(row["status"])["green"]) for row in rows}:  # a green HEAD was judged above
        if r is None:
            findings.append(_finding("runner-error", "the HEAD run could not be started: the checkout of the commit failed or git "
                                     "could not be run", "use a full clone (fetch-depth: 0) with a working git, then re-run"))
        elif r["timed_out"]:
            findings.append(_finding("timeout", f"the HEAD run timed out after {timeout} s",
                                     "make the tests faster, or raise strict.test_timeout in ghp.config.json"))
        elif r["exit"] != 0:
            findings.append(_finding("head-fails", f"the tests fail at HEAD {head[:12]} (exit {r['exit']}): a later commit broke them",
                                     "fix the code on this branch so the test command passes at HEAD, then re-run verify"))
    seconds = round(sum(r["seconds"] for r in runs.values() if r), 2)
    out = {"mode": mode, "ok": not findings, "findings": findings, "recorded": False, "runs": len(runs), "seconds": seconds,
           "overrides_used": _used(used)}
    if findings or not record:
        return out
    cmd = _command_hash(c.cfg.strict["test_command"])
    _write_record(Path(repo) / dod.dod_file(c.cfg.dod_path, story_id), repo, c.ledger,
                  f"### Verification — {date.today().isoformat()} @ {head[:12]}",
                  [*lines, f"- head: {head[:12]} exit {runs[head]['exit']}", f"- command-sha256: {cmd}", f"- seconds: {seconds}"])
    return {**out, "recorded": True}


# ---- the verbs: `ghp strict check|verify|override|mode` ----
def register(sub):
    p = sub.add_parser("strict", help="Strict SDD+TDD mode: phase checks (read-only) and `verify` (runs the tests, records the result)")
    ssub = p.add_subparsers(dest="strict_command", metavar="ACTION", required=True)
    q = ssub.add_parser("check", help="Check the strict-mode rules of a phase (read-only; exit 2 lists the findings as JSON; exit 3: invalid config or ambiguous ledger)")
    q.add_argument("ref", help="5, #5, owner/repo#5 or an issue URL (the story)")
    q.add_argument("--phase", required=True, choices=sorted(PHASES), help="implement, pr or done")
    q.add_argument("--base", metavar="REF", help="pr: the ref the PR targets, e.g. origin/main (resolved as refs/remotes/REF, refs/heads/REF, "
                   "then a full commit id, never a tag); red and green must lie in REF..head (an unknown REF is the finding base-unknown; "
                   "default: the branch origin/HEAD points at)")
    q.add_argument("--head", metavar="REF", default="HEAD", help="the branch or commit the PR is made from (default HEAD; CI passes the head SHA, "
                   "so a detached checkout works)")
    q = ssub.add_parser("mode", help="Which mode CI judges a PR by (read-only, no token, no network): prints {\"mode\", \"source\"}; exit 3 when the "
                        "config cannot be trusted (CI fails closed)")
    q.add_argument("--base", metavar="REF", help="the ref the PR targets, e.g. origin/main: a strict config there decides (source: target branch), "
                   "else the PR's own config (source: pull request)")
    q = ssub.add_parser("override", help="A human, in their own terminal, records a `kind: override` decision that lets ONE strict rule pass for "
                        "this story (a local write of Decisions_<n>.md; refuses without a terminal; not a security boundary; the entry must be posted "
                        "before `pr`); without --apply prints the line it would record")
    q.add_argument("ref", help="5, #5, owner/repo#5 or an issue URL (the story)")
    q.add_argument("rule", choices=decisions.OVERRIDE_RULES, help="the rule to skip: test-command, l2 (the record and test-run rules), open-question or placeholder")
    q.add_argument("--reason", required=True, metavar="TEXT", help="why, 20 to 300 characters (no control characters, `<!--`, `-->` or code fences)")
    q.add_argument("--apply", action="store_true", help="write the line (default: print it)")
    q = ssub.add_parser("verify", help="Run L1, then strict.test_command at each AC's red (must fail) and green (must pass) commit; "
                        "records the result in ledger section 4 (exit 2 lists the findings as JSON)")
    q.add_argument("ref", help="5, #5, owner/repo#5 or an issue URL (the story)")
    rec = q.add_mutually_exclusive_group()
    rec.add_argument("--record", dest="record", action="store_true", default=True, help="append the verification record to the ledger (default)")
    rec.add_argument("--no-record", dest="record", action="store_false", help="write nothing, only set the exit code (CI)")
    q.add_argument("--base", metavar="REF", help="refuse a red or green commit that is already in REF (it must be this branch's own work); "
                   "a strict config on REF decides (a PR cannot weaken its own strictness)")
    q.add_argument("--allow-override", action="store_true",
                   help="with --no-record: accept an override in force (default: it is the finding override-in-force, since it skips the run)")


def _override(args):
    """`strict override`: a human records a `kind: override` decision that lets one strict rule pass. Tested before anything is
    read or written (no config, no file), with or without --apply."""
    if not (sys.stdin and sys.stdin.isatty()):
        raise ValueError("an override waives a strict rule, which only a human may do: run it in your own terminal "
                         "(this shell has no terminal on stdin; a guard against accidents, not a security boundary)")
    cfg = Config.load()
    number = gate._ref(cfg, args.ref).number
    path = decisions.decisions_file(cfg.dod_path, number)
    if args.apply:
        d_id = decisions.apply_override(path, number, args.rule, args.reason)
    else:
        try:
            md = decisions._read(path) if path.exists() else decisions.render_file(number)
        except (OSError, UnicodeError) as e:
            raise ValueError(f"cannot read the decisions file {comments._show(path)} ({e.__class__.__name__})")
        d_id = decisions.add_override(md, args.rule, args.reason)[1]
    return {"ok": True, "applied": bool(args.apply), "id": d_id, "rule": args.rule, "file": comments._show(path),
            "line": decisions.render_line({"id": d_id, "kind": "override", "text": f"skip {args.rule}", "why": args.reason.strip(),
                                           "status": "open", "by": "", "note": "", "posted": None, "used": ""})}


def _with_limits(cfg, out):
    """Adds `notes` (the limits of approval by comment, in plain words) to the output of a strict verb when the config says approval is "comment"."""
    return {**out, "notes": [approvals.LIMITS]} if cfg.mode == "strict" and cfg.approval == "comment" else out


def _dispatch(args, gh):
    cmd = args.strict_command
    if cmd == "override":  # local only: no GitHub call, no config at first
        return _override(args)
    cfg = gate.target_config(args.base, ".")  # a strict target config decides; its profile and lockfile are read and validated too
    if cmd == "mode":
        return _with_limits(cfg, {"mode": cfg.mode, "source": "target branch" if cfg.from_target else "pull request"})
    number = gate._ref(cfg, args.ref).number
    if cmd == "verify":
        return _with_limits(cfg, verify(gh, cfg, number, record=args.record, base=args.base, allow_override=args.allow_override))
    base = args.base
    if base is None and args.phase == "pr" and cfg.mode == "strict":
        base = default_base(".")  # "" when origin/HEAD is not set: the finding base-unknown, never a pass
    return _with_limits(cfg, check(gh, cfg, number, args.phase, head=args.head, base=base, target=args.base))


def run(args, gh):
    """Exit 0: done (and, for check and verify, ok). 2: findings (JSON on stdout) or a refusal (bad usage, a missing config, another ValueError). 3: an
    invalid config or an ambiguous ledger (`dod.LedgerError`) in every strict verb: CI fails closed, a workflow treats 3 as a failure. 1: a GitHub error."""
    try:
        out = _dispatch(args, gh)
    except (ConfigError, ValueError) as e:
        if isinstance(e, Refused):
            print(json.dumps(e.check, indent=2))
        print("error: " + comments._show(" ".join(scrub(e).split()), 500), file=sys.stderr)
        return 3 if isinstance(e, (ConfigInvalid, dod.LedgerError)) else 2
    except OSError as e:  # a write or read that failed (read-only ledger, full disk, permissions): a clean refusal, never a traceback
        print("error: " + comments._show("%s%s; nothing written" % (e.strerror or e.__class__.__name__, ": " + str(e.filename) if e.filename else ""), 300), file=sys.stderr)
        return 2
    except GhError as e:
        print("error: " + comments._show(" ".join(scrub(e).split()), 500), file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2))
    for note in out.get("notes", ()):
        print("note: " + comments._show(note, 500), file=sys.stderr)
    if args.strict_command in ("check", "verify") and not out["ok"]:
        print(f"error: strict {args.strict_command} failed: {len(out['findings'])} finding(s)", file=sys.stderr)
        return 2
    return 0
