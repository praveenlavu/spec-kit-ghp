import collections
import json
import os
import re
import subprocess
import unicodedata
from pathlib import Path

OWNER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}")  # a GitHub login: goes into API paths
REPO_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")  # a GitHub repository name; "." and ".." are refused in valid_repo
REVIEW_SEVERITIES = ("blocker", "major", "minor")  # review_block_on entries (case-sensitive)
MODES = ("advisory", "strict")
STRICT_GATE_BLOCK = ("ac_traceability", "assertion", "open_questions", "commits_without_story")  # gate.block floor in strict mode
STRICT_RULES_GATE_BLOCK = ("rule_coverage", "rule_drift")  # added to that floor when rules are enabled
STRICT_REVIEW_BLOCK = ("blocker",)  # review_block_on floor in strict mode
STRICT_DEFAULTS = {"test_command": None, "test_timeout": 300}
RULES_DEFAULTS = {"enabled": False, "types": ["Epic", "Feature"]}  # business rules (R§ config); off = baseline
STORY_TYPE = "Story"  # the story label is `ghp:story` (D14); rules.types may not name it
MAX_REF_CONFIG = 1024 * 1024  # bytes: a config read from a git ref is the PR's neighbour's text; the same cap as the ledger
MAX_APPROVERS = 20
APPROVAL_MODES = ("terminal", "comment")
UNATTENDED_DEFAULTS = {"assume_after_hours": None, "approval_requests": False}  # approval_requests: owner ruling Q4, off by default
TRUST_KEYS = ("approvers", "approval", "unattended")  # read only from the target branch (`trusted`), never from the working tree
DROPPED_KEYS = ("organization", "project", "api_version", "story_type", "task_type", "repo_host")  # ado keys ghp does not have
CONFIG_NAME = "ghp.config.json"
REF_LABEL = "ghp.config.json at the target branch"  # names the file in messages, never the ref (it is a CLI value)
E_ORG_PROJECT = "E_ORG_PROJECT_UNSUPPORTED"  # an organization-owned Project is not supported in 0.1 (D8)
LOGIN_HINT = "use the numeric user id (`gh api users/<login> --jq .id`)"

BRANCH_PREFIX_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")


def valid_owner(owner):
    return isinstance(owner, str) and OWNER_RE.fullmatch(owner) is not None


def valid_repo(repo):
    return isinstance(repo, str) and REPO_RE.fullmatch(repo) is not None and repo not in (".", "..")


def valid_branch_prefix(prefix):
    """Empty, or a git-ref-safe start of a branch name (`<prefix>gh<n>-<slug>` must stay a valid ref)."""
    return isinstance(prefix, str) and (prefix == "" or (
        BRANCH_PREFIX_RE.fullmatch(prefix) is not None
        and not any(bad in prefix for bad in ("..", "//", "/.", ".lock/"))))


_HARMLESS_FORMAT = "\u200c\u200d\u200e\u200f"  # ZWNJ, ZWJ, LRM, RLM: Persian/Indic names, emoji sequences, mixed-RTL text


_FIELDS = frozenset({  # fixed config field names: safe to print
    "owner", "repo", "project_owner", "project_owner_type", "project_number", "status_todo", "status_active", "status_done",
    "dod_path", "gate", "tests", "block", "attachments_allow", "review_block_on",
    "done_state", "repository", "removed_state", "branch_prefix", "stack", "kit",
    "phases", "dod_gate", "mode", "strict", "test_command", "test_timeout", "rules", "enabled", "types",
    "approvers", "approval", "unattended", "assume_after_hours", "approval_requests"})


def _has_control(s):
    """Control (Cc), line/paragraph separators (Zl, Zp), surrogates, private use and format characters, except the
    four joiner/direction marks above. Cn (unassigned) is deliberately accepted: it depends on the Unicode DB version."""
    return any(unicodedata.category(c) in ("Cc", "Zl", "Zp", "Cs", "Co")
               or (unicodedata.category(c) == "Cf" and c not in _HARMLESS_FORMAT) for c in s)


def reject_control_chars(node, where, path):
    """The config comes from the PR in CI, so no string (value or key, any depth) may carry a control character
    (it could start a new CI log line). `where` is the key path ("" at the top). Names the key path, never the offending text."""
    if isinstance(node, str):
        if _has_control(node):
            raise ConfigInvalid(f"{path}: {where} contains a control character")
    elif isinstance(node, dict):
        for n, (k, v) in enumerate(node.items(), 1):
            if _has_control(k):
                raise ConfigInvalid(f"{path}: {where or 'top level'}: a key contains a control character")
            # a key that is not one of the fixed field names is user text: name it by position
            label = k if k in _FIELDS else f"<entry #{n}>"
            reject_control_chars(v, f"{where}.{label}" if where else label, path)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            reject_control_chars(v, f"{where}[{i}]", path)


class ConfigError(Exception):
    """No usable ghp.config.json found."""


class ConfigInvalid(ConfigError):
    """A config file is present but invalid (explicit misconfiguration; `ghp gate` exits 3, CI fails)."""


class Config:
    def __init__(self, owner, repo, project_owner=None, project_owner_type="user", project_number=None,
                 status_todo="Todo", status_active="In Progress", status_done="Done",
                 dod_path="dod", gate=None, attachments_allow=None, review_block_on=None,
                 done_state="Done", repository="",
                 removed_state="Removed", branch_prefix="feature/",
                 mode="advisory", strict=None, rules=None, approvers=None, approval="terminal", unattended=None):
        self.owner = owner
        self.repo = repo
        self.project_owner = project_owner or owner
        self.project_owner_type = project_owner_type
        self.project_number = project_number
        self.status_todo = status_todo
        self.status_active = status_active
        self.status_done = status_done
        self.dod_path = dod_path
        self.gate = {"tests": {}, "block": [], **(gate or {})}  # partial gate keeps defaults
        self.attachments_allow = (attachments_allow if attachments_allow is not None
                                  else [".md", ".svg", ".txt"])
        self.review_block_on = review_block_on or []
        self.done_state = done_state
        self.repository = repository
        self.removed_state = removed_state
        self.branch_prefix = branch_prefix
        self.mode = mode
        self.strict = {**STRICT_DEFAULTS, **(strict or {})}
        self.rules = {**RULES_DEFAULTS, **(rules or {})}
        self.rules["types"] = list(self.rules["types"])
        self.approvers = list(approvers or [])  # numeric user ids; only the target branch's value may be trusted (`trusted`)
        self.approval = approval
        self.unattended = {**UNATTENDED_DEFAULTS, **(unattended or {})}
        self.source_commit = None  # the 40-hex target commit load_from_ref resolved; None for a config read from disk
        self.from_target = False  # True when CI took this config from the PR's target branch (`ghp gate --base`)
        self.target_profile_blob = None  # `strict.read_target_trust` sets the target commit's ghp.stack.json text here (None: absent or not read); `stack.profile_for` reads it
        self.target_lock_blob = None  # the same for ghp.kits.lock.json

    def _effective(self, project_list, floor):
        out = list(project_list)
        if self.mode == "strict":
            out += [x for x in floor if x not in out]
        return out

    def effective_gate_block(self):
        """gate.block in force: the project's list, plus the strict floor when mode is strict (and `rule_coverage` and `rule_drift` when rules are enabled)."""
        return self._effective(self.gate["block"], STRICT_GATE_BLOCK + (STRICT_RULES_GATE_BLOCK if self.rules["enabled"] else ()))

    def effective_review_block_on(self):
        """review_block_on in force: the project's list, plus the strict floor when mode is strict."""
        return self._effective(self.review_block_on, STRICT_REVIEW_BLOCK)

    @classmethod
    def _from_data(cls, data, path):
        """The one validator behind `load` and `load_from_ref`; `path` only labels the messages (the PR chooses the content: never the text)."""
        try:
            return cls._validate(data, path)
        except RecursionError:  # a value nested too deeply for the validator
            raise ConfigInvalid(f"{path}: nested too deeply") from None

    @classmethod
    def _validate(cls, data, path):
        if not isinstance(data, dict):
            raise ConfigInvalid(f"{path}: top level must be a JSON object")
        reject_control_chars(data, "", path)
        for n, k in enumerate(data, 1):
            if k in DROPPED_KEYS:  # a fixed name from this module: safe to print
                raise ConfigInvalid(f"{path}: '{k}' is not a ghp key (it was an ado key; ghp uses owner, repo and project_number)")
            if "token" in k.casefold():  # PR text: named by position
                raise ConfigInvalid(f"{path}: top-level entry #{n}: token settings are not supported (ghp never reads a token; gh does its own auth)")
        missing = [k for k in ("owner", "repo") if not data.get(k)]
        if missing:
            raise ConfigInvalid(f"{path}: missing required key(s): {', '.join(missing)}")
        if "dod_gate" in data:
            raise ConfigInvalid(f"{path}: `dod_gate` was renamed to `gate`; "
                              "move `dod_gate.block` to `gate.block`")
        for k in ("gate", "stack", "rules", "unattended"):
            if k in data and not isinstance(data[k], dict):
                raise ConfigInvalid(f"{path}: '{k}' must be a JSON object")
        if not valid_owner(data["owner"]):
            raise ConfigInvalid(f"{path}: 'owner' must match {OWNER_RE.pattern}")
        if not valid_repo(data["repo"]):
            raise ConfigInvalid(f"{path}: 'repo' must match {REPO_RE.pattern} (and not be '.' or '..')")
        if "project_owner" in data and not valid_owner(data["project_owner"]):
            raise ConfigInvalid(f"{path}: 'project_owner' must match {OWNER_RE.pattern}")
        if "project_owner_type" in data and data["project_owner_type"] != "user":
            raise ConfigInvalid(f"{path}: {E_ORG_PROJECT}: 'project_owner_type' must be \"user\"; "
                              "organization-owned Projects are not supported in 0.1")
        pn = data.get("project_number")
        if "project_number" in data and not (isinstance(pn, int) and not isinstance(pn, bool) and pn >= 1):
            raise ConfigInvalid(f"{path}: 'project_number' must be a positive integer")
        from .gate import GATE_CHECKS, valid_glob  # local: gate -> comments/dod/issues -> config; the one definition of both lives in gate
        gate = data.get("gate", {})
        if "tests" in gate and not isinstance(gate["tests"], dict):
            raise ConfigInvalid(f"{path}: 'gate.tests' must be a JSON object")
        for n, (glob, rx) in enumerate(gate.get("tests", {}).items(), 1):
            # entries are named by position: the glob is PR-controlled text and must not reach the CI log
            if not (isinstance(rx, str) and 0 < len(rx) <= 500):
                raise ConfigInvalid(f"{path}: 'gate.tests' entry #{n}: the value must be a regex string of 1-500 characters")
            if not valid_glob(glob):
                raise ConfigInvalid(f"{path}: 'gate.tests' entry #{n}: the glob must be 1-200 characters "
                                  "with balanced '[...]' classes and at most 8 wildcards")
            try:
                re.compile(rx)
            except re.error:
                raise ConfigInvalid(f"{path}: 'gate.tests' entry #{n}: the regex is invalid") from None
        dod_path = data.get("dod_path", "dod")
        if not (isinstance(dod_path, str) and dod_path):
            raise ConfigInvalid(f"{path}: 'dod_path' must be a non-empty string")
        block = gate.get("block", [])
        if not (isinstance(block, list) and all(isinstance(b, str) for b in block)):
            raise ConfigInvalid(f"{path}: 'gate.block' must be a list of strings")
        for i, b in enumerate(block):
            if b not in GATE_CHECKS:
                raise ConfigInvalid(f"{path}: gate.block[{i}]: unknown check "
                                  f"(valid: {', '.join(GATE_CHECKS)})")
        for k in ("removed_state", "done_state", "status_todo", "status_active", "status_done"):
            if k in data and not (isinstance(data[k], str) and data[k]):
                raise ConfigInvalid(f"{path}: '{k}' must be a non-empty string")
        if not valid_branch_prefix(data.get("branch_prefix", "feature/")):
            raise ConfigInvalid(f"{path}: 'branch_prefix' must be empty or a git-ref-safe string "
                              "(letters, digits, '.', '_', '-', '/'; no '..', '//', '/.', '.lock/')")
        allow = data.get("attachments_allow")
        if allow is not None and not (
                isinstance(allow, list) and all(isinstance(a, str) for a in allow)):
            raise ConfigInvalid(f"{path}: 'attachments_allow' must be a list of strings")
        rbo = data.get("review_block_on", [])
        if not (isinstance(rbo, list) and all(isinstance(r, str) for r in rbo)):
            raise ConfigInvalid(f"{path}: 'review_block_on' must be a list of strings")
        for i, r in enumerate(rbo):
            if r not in REVIEW_SEVERITIES:
                raise ConfigInvalid(f"{path}: review_block_on[{i}]: unknown severity "
                                  f"(valid: {', '.join(REVIEW_SEVERITIES)})")
        if "mode" in data and data["mode"] not in MODES:
            raise ConfigInvalid(f"{path}: 'mode' must be \"advisory\" or \"strict\"")
        strict = data.get("strict", {})
        if not isinstance(strict, dict):
            raise ConfigInvalid(f"{path}: 'strict' must be a JSON object")
        for n, k in enumerate(strict, 1):
            if k not in STRICT_DEFAULTS:  # named by position: the key is PR-controlled text
                raise ConfigInvalid(f"{path}: strict entry #{n}: unknown key "
                                  f"(valid: {', '.join(STRICT_DEFAULTS)})")
        cmd = strict.get("test_command")
        if "test_command" in strict and not (isinstance(cmd, str) and 0 < len(cmd) <= 500):
            raise ConfigInvalid(f"{path}: 'strict.test_command' must be a string of 1-500 characters")
        tt = strict.get("test_timeout")
        if "test_timeout" in strict and not (isinstance(tt, int) and not isinstance(tt, bool) and 1 <= tt <= 3600):
            raise ConfigInvalid(f"{path}: 'strict.test_timeout' must be an integer from 1 to 3600")
        rules = data.get("rules", {})
        for n, k in enumerate(rules, 1):
            if k not in RULES_DEFAULTS:  # named by position: the key is PR-controlled text
                raise ConfigInvalid(f"{path}: rules entry #{n}: unknown key (valid: {', '.join(RULES_DEFAULTS)})")
        if "enabled" in rules and not isinstance(rules["enabled"], bool):
            raise ConfigInvalid(f"{path}: 'rules.enabled' must be true or false")
        types = rules.get("types")
        if "types" in rules:
            if not (isinstance(types, list) and 1 <= len(types) <= 10
                    and all(isinstance(t, str) and 0 < len(t) <= 64 for t in types)):
                raise ConfigInvalid(f"{path}: 'rules.types' must be a list of 1-10 non-empty strings of at most 64 characters")
            for i, t in enumerate(types):
                if t.casefold() == STORY_TYPE.casefold():
                    raise ConfigInvalid(f"{path}: rules.types[{i}]: a story type cannot carry rules")
        approvers = cls._validate_trust_keys(data, path)
        return cls(
            owner=data["owner"],
            repo=data["repo"],
            project_owner=data.get("project_owner"),
            project_number=pn,
            status_todo=data.get("status_todo", "Todo"),
            status_active=data.get("status_active", "In Progress"),
            status_done=data.get("status_done", "Done"),
            dod_path=dod_path,
            gate=data.get("gate"),
            attachments_allow=allow,
            review_block_on=rbo,
            done_state=data.get("done_state", "Done"),
            repository=data.get("repository", ""),
            removed_state=data.get("removed_state", "Removed"),
            branch_prefix=data.get("branch_prefix", "feature/"),
            mode=data.get("mode", "advisory"),
            strict=strict,
            rules=rules,
            approvers=approvers,
            approval=data.get("approval", "terminal"),
            unattended=data.get("unattended"),
        )

    @staticmethod
    def _validate_trust_keys(data, path):
        """`approvers`, `approval`, `unattended`. Errors name the key and the position, never a value. Returns the approvers (numeric
        user ids, in order). A duplicate is rejected (not collapsed), as every duplicate id is in this kit."""
        approvers = data.get("approvers", [])
        if not (isinstance(approvers, list) and len(approvers) <= MAX_APPROVERS):
            raise ConfigInvalid(f"{path}: 'approvers' must be a list of at most {MAX_APPROVERS} numeric user ids")
        out = []
        for i, a in enumerate(approvers):
            if not (isinstance(a, int) and not isinstance(a, bool) and a >= 1):  # a bool is not an integer; a login is a string
                raise ConfigInvalid(f"{path}: approvers[{i}]: not a numeric user id; {LOGIN_HINT}")
            if a in out:
                raise ConfigInvalid(f"{path}: approvers[{i}]: duplicate of an earlier entry")
            out.append(a)
        if "approval" in data and data["approval"] not in APPROVAL_MODES:
            raise ConfigInvalid(f"{path}: 'approval' must be \"terminal\" or \"comment\"")
        unattended = data.get("unattended", {})
        for n, k in enumerate(unattended, 1):
            if k not in UNATTENDED_DEFAULTS:  # named by position: the key is PR-controlled text
                raise ConfigInvalid(f"{path}: unattended entry #{n}: unknown key (valid: {', '.join(UNATTENDED_DEFAULTS)})")
        hours = unattended.get("assume_after_hours")
        if "assume_after_hours" in unattended and not (isinstance(hours, int) and not isinstance(hours, bool) and 1 <= hours <= 720):
            raise ConfigInvalid(f"{path}: 'unattended.assume_after_hours' must be an integer from 1 to 720")
        if "approval_requests" in unattended and not isinstance(unattended["approval_requests"], bool):
            raise ConfigInvalid(f"{path}: 'unattended.approval_requests' must be true or false")
        return out

    @classmethod
    def load_from_ref(cls, ref, repo="."):
        """The config at `ref` (the PR's target branch), read with git and checked by the same validator as `load`: a Config, or None
        when the repository has no `ghp.config.json` at that ref. Anything else (a ref that is no commit, git missing or failing, a
        non-regular or over 1 MiB file, bad content) raises ConfigInvalid with text that never echoes the ref: CI fails closed.
        `ref` is resolved by `_resolve_base`: refs/remotes/<ref>, refs/heads/<ref>, a full commit id; never a tag."""
        def fail(why):
            return ConfigInvalid(f"{REF_LABEL}: {why}")
        commit = _resolve_base(repo, ref) if isinstance(ref, str) else ""
        if not commit:
            raise fail("the base ref is not a commit in this repository (or git could not be run); fetch it (fetch-depth: 0)")
        raw = _blob_at(repo, commit, CONFIG_NAME, MAX_REF_CONFIG, REF_LABEL)
        if raw is None:
            return None  # the path does not exist at that commit
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError as e:  # includes UnicodeDecodeError
            raise fail(f"invalid JSON: {e}")
        except RecursionError:
            raise fail("nested too deeply") from None
        cfg = cls._from_data(data, REF_LABEL)
        cfg.source_commit = commit
        return cfg

    @classmethod
    def load(cls, start_dir=None):
        candidates = []
        env_path = os.environ.get("GHP_CONFIG")
        if env_path:
            candidates.append(Path(env_path))
        candidates.append(Path(start_dir or os.getcwd()) / CONFIG_NAME)
        for path in candidates:
            if path.is_file():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except OSError as e:
                    raise ConfigInvalid(f"{path}: cannot read ({e})")
                except ValueError as e:  # includes UnicodeDecodeError
                    raise ConfigInvalid(f"{path}: invalid JSON: {e}")
                except RecursionError:
                    raise ConfigInvalid(f"{path}: nested too deeply") from None
                return cls._from_data(data, path)
        raise ConfigError(
            "No ghp config found. Run `ghp setup` to create ghp.config.json here, "
            "or point $GHP_CONFIG at a config file."
        )


# ---- reading a file at a git ref (the target branch) ----
_GIT_ENV = {"GIT_NO_REPLACE_OBJECTS": "1", "GIT_GRAFT_FILE": os.devnull, "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "GIT_OPTIONAL_LOCKS": "0"}
# an inherited selector (a hook's GIT_INDEX_FILE, an outer GIT_DIR) must not point git at another repository or index
_GIT_SELECTORS = {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                  "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_PREFIX", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT", "GIT_EXEC_PATH"}


def git_env(base=None):
    """`base` (default: this process's environment) for a git run: no inherited repository selector or config injection, replace refs and grafts
    off, no pager, no prompt, no optional locks. The one environment for every git call of ghp (`prs.git_env` is this)."""
    env = {k: v for k, v in (os.environ if base is None else base).items()
           if k not in _GIT_SELECTORS and not k.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))}
    return {**env, **_GIT_ENV}


def _git(repo, *args, env=None, timeout=30, stdin=None):
    """A finished git run (bytes), or None when git is missing, cannot start or runs past `timeout` s (30): a failure, never a pass. The one git runner
    of `config`, `strict`, `kits` and `stack` (T23 merged their copies). `env` defaults to `git_env()`; `stdin` (bytes) is fed to git, else stdin is closed."""
    try:
        return subprocess.run(["git", "--no-pager", "--no-replace-objects", "-c", "core.fsmonitor=false", "-C", str(repo), *args],
                              check=False, capture_output=True, timeout=timeout, **({"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}),
                              env=git_env() if env is None else env)
    except (OSError, subprocess.SubprocessError):
        return None


def _out(repo, *args, **kw):
    """stdout (bytes) of a successful git run, else None."""
    r = _git(repo, *args, **kw)
    return r.stdout if r is not None and r.returncode == 0 else None


def _rev(repo, rev):
    return (_out(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{rev}^{{commit}}") or b"").decode().strip()


def _commit(repo, sha, **kw):
    """Full id of the one commit whose id starts with the hex `sha`, else None. Object lookup only: a branch or tag
    named like the SHA never stands in, and an ambiguous prefix or a non-commit object is refused."""
    out = _out(repo, "rev-parse", f"--disambiguate={sha.lower()}", **kw)
    objs = out.decode("ascii", "replace").split() if out is not None else []
    return objs[0] if len(objs) == 1 and _out(repo, "cat-file", "-t", objs[0], **kw) == b"commit\n" else None


def _resolve_base(repo, base):
    """Full commit id of a base: `refs/remotes/<base>`, then `refs/heads/<base>`, then a full 40-hex commit id (or a full
    `refs/heads/` or `refs/remotes/` name). Never a tag or a short name: a tag named like a branch must not move the base. "" when none."""
    if base.startswith(("refs/heads/", "refs/remotes/")):
        return _rev(repo, base)
    full = _rev(repo, f"refs/remotes/{base}") or _rev(repo, f"refs/heads/{base}")
    return full or (_commit(repo, base) or "" if re.fullmatch(r"[0-9a-f]{40}", base) else "")


def _blob_at(repo, commit, path, cap, label):
    """The bytes of regular file `path` at the already-resolved 40-hex `commit` (ValueError otherwise), or None when the path does
    not exist there. Anything else (git missing or failing, a symlink, submodule or directory, more than `cap` bytes) raises
    ConfigInvalid(f"{label}: ...") with a fixed text that never echoes the path or the commit: CI fails closed."""
    if not (isinstance(commit, str) and re.fullmatch(r"[0-9a-fA-F]{40}", commit)):
        raise ValueError("commit must be a resolved 40-hex commit id")

    def fail(why):
        return ConfigInvalid(f"{label}: {why}")
    listed = _git(repo, "ls-tree", "-l", "--full-tree", "--end-of-options", commit, "--", path)
    if listed is None or listed.returncode:
        raise fail("git could not read the tree")
    if not listed.stdout.strip():
        return None
    meta = listed.stdout.decode("utf-8", "replace").split("\t", 1)[0].split()
    if len(meta) != 4 or meta[0] not in ("100644", "100755") or meta[1] != "blob" or not meta[3].isdigit():
        raise fail("the file is not a regular file")
    limit = f"{cap // 1048576} MiB" if cap % 1048576 == 0 else f"{cap} bytes"
    if int(meta[3]) > cap:
        raise fail(f"the file is larger than {limit}")
    shown = _git(repo, "show", "--no-textconv", "--end-of-options", f"{commit}:{path}")
    if shown is None or shown.returncode or len(shown.stdout) > cap:
        raise fail("git could not read the file")
    return shown.stdout


# ---- trust: the target branch's approvers, approval and unattended ----
Trust = collections.namedtuple("Trust", "approvers approval unattended")  # what `trusted` returns: nothing but the three trust keys
_BAD_BASE = re.compile(r"[~^:\s\x00-\x1f\x7f]|@\{|\.\.|^-")  # a revision expression, a range or an option, not a plain ref name

E_NONROOT_TRUST = ("the config file in use is not the repo-root ghp.config.json and sets approvers, approval or unattended: those keys are "
                   "read only from ghp.config.json on the target branch; move them there")


def _has_trust_keys(p):
    """Does the regular file `p` (a symlink is followed: it is only searched for the three keys) hold a trust key? Over 1 MiB, unreadable or not a JSON
    object: ConfigInvalid (when it cannot be read, nobody can say it holds none)."""
    try:
        with p.open("rb") as f:
            raw = f.read(MAX_REF_CONFIG + 1)
        data = json.loads(raw.decode("utf-8")) if len(raw) <= MAX_REF_CONFIG else None
    except (OSError, ValueError, RecursionError):
        data = None
    if not isinstance(data, dict):
        raise ConfigInvalid("the config file cannot be read as a JSON object of at most 1 MiB, so it cannot be checked for the approval keys")
    return any(k in data for k in TRUST_KEYS)


def _top_config(repo):
    """The git top level's `ghp.config.json` (`repo`'s own when git cannot say)."""
    top = _out(str(repo), "rev-parse", "--show-toplevel")
    return Path(top.decode("utf-8", "replace").strip() if top else repo) / CONFIG_NAME


def _same_file(a, b):
    """Same file on disk (a symlink, `..`, another spelling or another letter case on a case-insensitive disk included); a missing one is not."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def trusted(base, repo="."):
    """`Trust(approvers, approval, unattended)` that may be believed: the TARGET branch's (`git show <base>:ghp.config.json`), never the
    working tree's (the PR edits that). A target without a config = the defaults; anything unreadable or invalid raises ConfigInvalid
    (`approval: "comment"` is valid since T27 and wired in by T28: `approvals.target_trust`, `approvals.require`). A `base` that is not a plain ref name (a revision expression such as
    `main@{1}` or `origin/main~1`, a range, whitespace, a leading `-`) is refused: it could pick an older target config. A config in
    use that is not the repo-root file and sets a trust key is refused too, never silently dropped.
    Residual: rewriting `refs/remotes/origin/<base>` locally changes what the local CLI trusts; CI fetches fresh."""
    used = next((p for p in (Path(os.environ.get("GHP_CONFIG") or "\0"), Path(repo) / CONFIG_NAME) if p.is_file()), None)
    if used is not None and not _same_file(used, _top_config(repo)) and _has_trust_keys(used):
        raise ConfigInvalid(E_NONROOT_TRUST)
    if not isinstance(base, str) or _BAD_BASE.search(base):
        raise ConfigInvalid("the base is not a plain branch or ref name (no revision expressions, ranges, spaces or leading dash)")
    cfg = Config.load_from_ref(base, repo=str(repo)) or Config("", "")  # no config at the target: the defaults
    return Trust(list(cfg.approvers), cfg.approval, dict(cfg.unattended))
