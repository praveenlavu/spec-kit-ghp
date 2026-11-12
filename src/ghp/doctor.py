"""`ghp doctor`: what is wrong with this setup, as rows `{id, state, message}`. State is `pass`, `warn`, `fail` or `unknown`; `unknown` means a probe
could not tell, and a probe that cannot tell never says `pass`. Nothing here writes. Local reads, `git --version`, `git rev-parse`, `gh --version` and
`specify --version` (scrubbed environment, a timeout, only a parsed version is ever kept from their output), and GitHub READS through the injected
transport: the GraphQL `viewer` read (`Gh.viewer`: the identity and the `X-OAuth-Scopes` header of that reply), `GET repos/<owner>/<repo>`, `GET .../branches/<default>/protection`
and the Project workflows query. `Gh` is never armed and nothing here runs `gh auth ...`: the `gh auth refresh -s project` hint is TEXT for the human.
Row messages are fixed: numeric ids and versions only, never a token, an email, a config value or tool output; the few GitHub strings shown (a
workflow name, a login of the GitHub login shape) go through `comments._show` and a cap. Config errors name the key by position (the validator's own texts).

Offline (`--offline`, or no `Gh`): every row that needs GitHub is `unknown (offline)`. A GitHub call that gets no answer at all (no network, a timeout) turns the
rest of the run offline, so a dead network costs one timeout, not four. Exit code: 1 when any row is `fail`, else 0; an invalid config or an unreadable
target branch is a row, never a crash and never exit 3.
UNVERIFIED live: the shape of the Project workflows list after a toggle in the Project's UI (an unexpected shape is `unknown`, never `pass`); the header
GitHub sends for fine-grained tokens and apps (absent is `unknown`); the exact 403 text of the branch-protection read on a private repository of a free
account (`Upgrade to GitHub Pro`, matched case-insensitively).
Process help, not a security boundary: a hostile checkout can shape the local rows it controls."""
import json
import os
import re
import subprocess
import urllib.parse

from . import __version__, approvals, comments, config, kits, projects, stack, strict
from .config import Config, ConfigError, ConfigInvalid, valid_owner
from .done import CLOSING_WORKFLOWS
from .gh import GhError, GhMissing, RateLimited, ScopeMissing

GIT_FLOOR = (2, 24)  # `--end-of-options`
SPECKIT_FLOOR = ">=1.2.1"  # X6; used when the installed extension.yml cannot say
TIMEOUT = 10  # seconds per git/gh/specify call
MAX_MANIFEST = 256 * 1024
EXT_PATH = ".specify/extensions/ghp/extension.yml"
WF_PATH = ".specify/workflows/ghp-lifecycle/workflow.yml"
# Spec Kit extensions that also turn tasks into issues or Project items: `project` (the plan names it) and the GitHub ones of the catalog copy.
# UNVERIFIED: github, github-issues and maqa-github-projects come from a local copy of the catalog; the ids were not re-checked against the live catalog.
OVERLAP_IDS = ("project", "github", "github-issues", "maqa-github-projects", "tasks-to-project")
NEEDED_SCOPES = ("repo", "project")
OFFLINE = "unknown (offline)"
SIGNED_OUT = "not checked: gh is not signed in (see the identity row)"
NO_ANSWER = ("E_GH_NO_RESPONSE", "E_GH_TIMEOUT", "E_GH_SPAWN")
MAX_NAME = 60  # characters of a workflow name shown
MAX_NAMES = 10
ROW_IDS = ("git", "specify", "shallow", "gh", "identity", "scopes", "config", "config-target", "extension", "workflow",
           "approvers", "writers", "project-workflows", "protection", "overlap", "stack", "kits")
_VER = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_SPECIFY = re.compile(r"^specify(?:[ -]cli)?[ \t]+v?(\d+)\.(\d+)\.(\d+)", re.M | re.I)
_GIT = re.compile(r"^git version (\d+)\.(\d+)(?:\.(\d+))?", re.M)
_GH = re.compile(r"^gh version (\d+)\.(\d+)\.(\d+)", re.M)
_MANIFEST_VERSION = re.compile(r"[0-9A-Za-z.+-]{1,40}")
_TOP = re.compile(r"([A-Za-z_]+):[ \t]*(?:#.*)?")


class _Missing(Exception):
    """The tool is not installed."""


class _Unusable(Exception):
    """The tool ran but gave no usable answer (timeout, crash, non-zero exit)."""


def _run(argv, cwd=None, git=False):
    """stdout (text) of a successful run: scrubbed environment (no token or secret-looking variable), no stdin, a timeout."""
    home = os.environ.get("HOME", "/nonexistent")
    env = strict._git_env(os.environ, home) if git else {**strict.scrubbed_env(os.environ, home), "NO_COLOR": "1"}
    try:
        r = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=TIMEOUT, check=False)
    except FileNotFoundError:
        raise _Missing from None
    except (OSError, subprocess.SubprocessError):
        raise _Unusable from None
    if r.returncode:
        raise _Unusable
    return r.stdout.decode("utf-8", "replace")


def _vers(m):
    return tuple(int(g or 0) for g in m.groups())


def _dotted(v):
    return ".".join(str(n) for n in v)


def _git_row(repo):
    try:
        m = _GIT.search(_run(["git", "--version"], git=True))
    except _Missing:
        return "fail", "git is not installed or not on PATH: install git 2.24 or newer"
    except _Unusable:
        return "unknown", "`git --version` gave no usable answer"
    if not m:
        return "unknown", "`git --version` gave an answer that could not be read"
    found = _vers(m)[:2]
    if found < GIT_FLOOR:
        return "fail", f"git {_dotted(found)} is older than {_dotted(GIT_FLOOR)}: upgrade git"
    return "pass", "ok"


def _gh_row(repo):
    """The CLI only: `gh --version`. No sign-in is looked at here (the identity row asks GitHub), and no token is read."""
    try:
        m = _GH.search(_run(["gh", "--version"]))
    except _Missing:
        return "fail", "the GitHub CLI (`gh`) is not installed or not on PATH: install it from https://cli.github.com"
    except _Unusable:
        return "unknown", "`gh --version` gave no usable answer"
    if not m:
        return "unknown", "`gh --version` gave an answer that could not be read"
    return "pass", f"gh {_dotted(_vers(m))}"


def _shallow_row(repo):
    try:
        said = _run(["git", "-C", str(repo), "rev-parse", "--is-shallow-repository"], git=True).strip()
    except (_Missing, _Unusable):
        return "unknown", "git could not tell whether this is a shallow clone (not a git repository?)"
    if said == "true":
        return "fail", "this clone is shallow, so base and merge-base checks fail: run `git fetch --unshallow`"
    return ("pass", "ok") if said == "false" else ("unknown", "git gave an answer about the clone depth that could not be read")


def _value(line, key):
    """The value of a `  key: value` line (quotes and a trailing `# note` dropped), else None. String operations, not a regex: linear."""
    prefix = f"  {key}:"
    if not line.startswith(prefix):
        return None
    rest = line[len(prefix):].split("#", 1)[0].strip()
    if rest[:1] in ("'", '"'):
        rest = rest[1:]
    if rest[-1:] in ("'", '"'):
        rest = rest[:-1]
    rest = rest.strip()
    return rest if rest and "'" not in rest and '"' not in rest else None


def _manifest(repo, rel, section, key):
    """(status, value): a `  key: value` line inside the top-level `section:` of a manifest read as confined as a kit file
    (no symlink, 256 KiB). status is `ok`, `absent` or `unreadable`. A line scan, not YAML (stdlib only)."""
    try:
        raw = kits._read_regular(repo, rel, MAX_MANIFEST, missing_ok=True)
    except kits.KitError:
        return "unreadable", None
    if raw is None:
        return "absent", None
    inside = False
    for line in raw.decode("utf-8", "replace").splitlines():
        top = _TOP.fullmatch(line)
        if top or line[:1].strip():
            inside = bool(top) and top.group(1) == section
        elif inside and (value := _value(line, key)) is not None:
            return "ok", value
    return "unreadable", None


def _specify_row(repo):
    status, floor = _manifest(repo, EXT_PATH, "requires", "speckit_version")
    wanted = [SPECKIT_FLOOR] + ([floor] if status == "ok" else [])
    needs = [re.fullmatch(r">=\s*(\d+\.\d+\.\d+)", w) for w in wanted]
    if not all(needs):
        return "unknown", "the Spec Kit version the extension requires is written in a form doctor cannot read"
    need = max(_vers(_VER.fullmatch(n.group(1))) for n in needs)  # a checkout can raise the built-in floor, never lower it
    try:
        m = _SPECIFY.search(_run(["specify", "--version"]))
    except _Missing:
        return "warn", "the `specify` CLI is not on PATH: install Spec Kit (`uv tool install specify-cli`) to use the lifecycle workflow"
    except _Unusable:
        return "unknown", "`specify --version` gave no usable answer"
    if not m:
        return "unknown", "`specify --version` gave an answer that could not be read"
    if _vers(m) < need:
        return "fail", f"specify {_dotted(_vers(m))} is older than {_dotted(need)}, which the extension requires: upgrade Spec Kit"
    return "pass", "ok"


def _version_row(repo, rel, what):
    """`what` is the manifest's top-level section too (`extension` or `workflow`)."""
    status, version = _manifest(repo, rel, what, "version")
    if status == "absent":
        return "warn", f"the {what} is not installed in this project (the CLI works without it)"
    if status != "ok" or not _MANIFEST_VERSION.fullmatch(version or ""):
        return "unknown", f"the installed {what} has no version doctor can read"
    if version != __version__:
        return "fail", f"the installed {what} is version {version} but the CLI is {__version__}: reinstall the {what} or the CLI so they match"
    return "pass", "ok"


def _config_text(msg, what):
    """The validator's own text, minus its file label: fixed wording that names keys by position and never a value."""
    tail = msg.split(": ", 1)[1] if ": " in msg else msg
    if tail.startswith("invalid JSON"):
        tail = "is not valid JSON"
    elif tail.startswith("cannot read"):
        tail = "cannot be read"
    return re.sub(r"[^\x20-\x7e]", "?", f"{what} {tail}".strip())[:300]


def _load_config(repo):
    """(Config or None, (state, message) for the `config` row)."""
    try:
        return Config.load(str(repo)), ("pass", "ok")
    except ConfigInvalid as e:
        return None, ("fail", _config_text(str(e), "ghp.config.json:") + ": fix it, then run `ghp doctor` again")
    except ConfigError:
        return None, ("fail", "no ghp.config.json found: run `ghp setup`")


def _load_target(repo):
    """(target Config or None, (state, message) for `config-target`)."""
    base = strict.default_base(str(repo))
    if not base:
        return None, ("unknown", "no origin/HEAD, so the target branch is unknown (CI reads the config there): add the remote and run `git remote set-head origin -a`")
    try:
        target = Config.load_from_ref(base, repo=str(repo))
    except ConfigInvalid as e:
        return None, ("fail", _config_text(str(e), "") + ": CI cannot trust the target branch's config until this is fixed")
    if target is None:
        return None, ("warn", "the target branch has no ghp.config.json yet, so CI judges a PR by its own: merge the setup")
    return target, ("pass", "ok")


class _Net:
    """The one GitHub door of a run. `offline` starts True for `--offline` or no `Gh`, and turns True when a call gets no answer."""

    def __init__(self, gh, offline):
        self.gh, self.offline = gh, bool(offline) or gh is None
        self.signed_out = False  # `gh` answered that nobody is signed in: not a network failure, and every GitHub row says so


def _skip(net):
    """The row of a GitHub read that is not made at all (offline, or gh is signed out), else None."""
    if net.offline:
        return "unknown", OFFLINE
    if net.signed_out:
        return "unknown", SIGNED_OUT
    return None


def _signed_out(e):
    """`gh api` run signed out exits 4 with no HTTP response and says `gh auth login` on stderr (kept in the error's detail, never shown): not a network failure."""
    return e.code == "E_GH_NO_RESPONSE" and "gh auth login" in e.detail.lower()


def _problem(e, net):
    """(state, message) for a failed GitHub read; fixed texts, never GitHub's own words. A call with no answer at all turns the run offline."""
    if isinstance(e, GhMissing):
        return "unknown", "not checked: gh is not installed (see the gh row)"
    if _signed_out(e):
        net.signed_out = True
        return "unknown", SIGNED_OUT
    if e.code in NO_ANSWER:
        net.offline = True
        return "unknown", OFFLINE + ": GitHub gave no answer (no network, or gh is not signed in: run `gh auth login` yourself)"
    if isinstance(e, RateLimited):
        return "unknown", "GitHub's rate limit is reached: run `ghp doctor` again later"
    if isinstance(e.status, int):
        return "unknown", f"GitHub answered HTTP {e.status} (unexpected)"
    return "unknown", "GitHub gave no usable answer"


def _identity(net):
    """(identity row, scopes row or None when it is judged later by `_scopes_row`, the Viewer or None). One viewer read."""
    skipped = _skip(net)
    if skipped:
        return skipped, skipped, None
    try:
        who = net.gh.viewer()
    except GhError as e:
        if _signed_out(e):  # gh is not signed in: a fail (the most common setup failure), not "offline"
            net.signed_out = True
            return ("fail", "gh is not signed in: sign in yourself with `gh auth login` (doctor only prints this, it never runs it)"), ("unknown", SIGNED_OUT), None
        if e.status in (401, 403):
            return (("fail", f"GitHub did not accept the sign-in (HTTP {e.status}): sign in yourself with `gh auth login` (doctor never runs it)"),
                    ("unknown", "not checked: GitHub did not accept the sign-in"), None)
        row = _problem(e, net)
        return row, _skip(net) or ("unknown", "not checked: the identity could not be read"), None
    if type(who.id) is not int or who.id < 1:
        said = "GitHub did not return a user id"
        return ("unknown", said), ("unknown", "not checked: " + said), None
    login = f" ({who.login})" if valid_owner(who.login) else ""  # a login of the GitHub shape only: anything else is not shown
    ident = ("pass", f"signed in as user id {who.id}{login}")
    if who.scopes is None:
        return ident, ("unknown", "GitHub sent no X-OAuth-Scopes header (a fine-grained token or an app token has none), so the scopes cannot be checked: "
                                  "`ghp project show` shows whether the Project can be read"), who
    return ident, None, who


def _scopes_row(who, visibility):
    """`repo` and `project` are needed. `public_repo` stands in for `repo` on a PUBLIC repository only (a warn: it cannot reach a private one); on a private one it
    is not enough, and when the visibility is not known it is `unknown`, never a pass."""
    missing = [s for s in NEEDED_SCOPES if s not in who.scopes]
    if not missing:
        return "pass", "the token has the scopes repo and project"
    public = "repo" in missing and "public_repo" in who.scopes
    rest = [s for s in missing if not (s == "repo" and public)]
    hint = ("; `read:project` alone can read the Project but cannot change it" if "project" in missing and "read:project" in who.scopes else "")
    fix = f": run `gh auth refresh -s {','.join(missing)}` yourself, as the right account (doctor only prints this, it never runs it)"
    if public and not rest:
        if visibility == "public":
            return "warn", ("the token has public_repo instead of repo: that is enough for this public repository, but it cannot reach a private one "
                            "(the scope repo can; doctor only prints `gh auth refresh -s repo`, it never runs it)")
        if visibility is None:
            return "unknown", "the token has public_repo instead of repo, which is enough only for a public repository, and doctor could not tell whether this one is public"
        return "fail", "the token has public_repo, which cannot reach this private repository; it lacks the scope repo" + fix
    return "fail", f"the token lacks the scope {' and '.join(rest if public else missing)}{hint}" + fix


ONE_HUMAN = ("approvers must be separate human identities: identity binding protects against the agent only while an approver's credentials "
             "are not available to it (a separate account or browser profile, no approver token in the agent's environment)")
WRITERS = ("approval by comment is tamper-evident, not tamper-proof: any writer on the repository can edit or delete any comment, an approver's included; "
           "ghp sees an edit and stops counting that approval, but cannot see a deletion (docs/trust-and-limits.md)")
UNREADABLE_TRUST = ("a config file cannot be read (the target branch's ghp.config.json, the working-tree one, or the file GHP_CONFIG names: see the config and "
                    "config-target rows), so the approval keys cannot be trusted")


def _trust(repo):
    """(Trust, "") from `approvals.target_trust` (the verbs' own path: the target branch only), or (None, a fixed refusal text). The validator's text is never echoed."""
    try:
        return approvals.target_trust(str(repo)), ""
    except ConfigInvalid as e:
        return None, (str(e) if str(e) in (config.E_NONROOT_TRUST, approvals.E_NO_TARGET) else UNREADABLE_TRUST)  # code-defined constants
    except approvals.ApprovalRefused as e:
        return None, (str(e) if str(e) in (approvals.E_TRUST_BASE_UNRESOLVED, approvals.E_APPROVERS_DIFFER) else UNREADABLE_TRUST)


def _approvers_row(trust, why, who, net):
    """The `approvers` and `approval` keys come only from the target branch, read as the verbs read them. An approver equal to the token identity fails
    (the agent's own comments would be the approver's). Not checked here: the CI token identity (its own run)."""
    if trust is None:
        return "fail", why
    if trust.approval == "comment" and not trust.approvers:
        return "fail", 'approval is "comment" on the target branch but no approvers are configured, so nobody can approve: add `approvers` (numeric user ids) there'
    if not trust.approvers:
        return "pass", "ok"
    if who is None:
        return _skip(net) or ("unknown", "approvers are configured, but the token identity is unknown, so doctor cannot compare them")
    if approvals.approver_is_token(trust.approvers, who.id):
        return "fail", "an approver is the identity of this token, so the agent could approve itself: run the agent with a token that is not an approver"
    return "pass", ONE_HUMAN


def _writers_row(trust):
    """Only comment mode relies on comments; an approval mode that cannot be read is judged as comment mode (the warning is never skipped on a doubt)."""
    if trust is not None and trust.approval != "comment":
        return "pass", "approval is typed in a terminal, so edits to comments approve nothing"
    return "warn", WRITERS


def _names(found):
    shown = [f"'{comments._show(n, MAX_NAME)}'" for n in found[:MAX_NAMES]]
    return ", ".join(shown) + (f" and {len(found) - MAX_NAMES} more" if len(found) > MAX_NAMES else "")


BYPASS = {
    "Auto-close issue": "'Auto-close issue' is enabled: dragging a card to Done on the board closes the issue without the gate (ghp done is the only gate)",
    "Item closed": "'Item closed' is enabled: closing an issue moves its card to Done on its own (seen live a fraction of a second later), "
                   "so the board can say Done before ghp done has run",
}
assert tuple(BYPASS) == tuple(CLOSING_WORKFLOWS)  # one definition of the two names: done.py


def _workflows_row(cfg, net):
    if cfg is None:
        return "unknown", "not checked: no usable ghp.config.json, so the Project is unknown"
    if cfg.project_number is None:
        return "pass", "no Project is configured (project_number), so no Project workflow can close an issue"
    skipped = _skip(net)
    if skipped:
        return skipped
    try:
        found = projects.workflows(net.gh, cfg)
    except ScopeMissing:
        return "unknown", "the token cannot read the Project (see the scopes row)"
    except projects.ProjectError as e:
        if e.code == "E_PROJECT_NOT_FOUND":
            return "fail", "no Project of a user account was found: check project_owner and project_number in ghp.config.json"
        return "unknown", "the Project could not be read as configured"
    except GhError as e:
        if e.code == "E_GH_BAD_BODY":
            return "unknown", "GitHub's list of the Project's workflows was not in the shape ghp expects (unverified after a change in the Project's own settings)"
        return _problem(e, net)
    enabled = [w.name for w in found if w.enabled]
    if not enabled:
        return "pass", "no workflow is enabled on the Project"
    bypass = [n for n in BYPASS if n in enabled]
    other = [n for n in enabled if n not in BYPASS]
    parts = []
    if bypass:
        parts.append("found a gate bypass: " + "; ".join(BYPASS[n] for n in bypass) + ". Turn it off in the Project's Workflows screen; the API cannot")
    if other:
        parts.append("Other enabled workflows add or move cards on their own: " + _names(other) + ". Check in the Project's Workflows screen that none of them sets Done")
    return "warn", ". ".join(parts)


def _branch_ok(name):
    return (isinstance(name, str) and 0 < len(name) <= 255 and not config._has_control(name) and " " not in name and not name.startswith("-")
            and not any(seg in ("", ".", "..") for seg in name.split("/")))


def _repo(cfg, net):
    """("ok", GET repos/<owner>/<repo> as GitHub answered it) or ("row", the (state, message) of why it was not read). One call, shared by `scopes` and `protection`."""
    if cfg is None:
        return "row", ("unknown", "not checked: no usable ghp.config.json, so the repository is unknown")
    skipped = _skip(net)
    if skipped:
        return "row", skipped
    try:
        return "ok", net.gh.rest("GET", f"repos/{cfg.owner}/{cfg.repo}")
    except GhError as e:
        if e.status == 404:
            return "row", ("fail", "GitHub does not know this repository, or the token cannot see it: check owner and repo in ghp.config.json")
        return "row", _problem(e, net)


def _protection_row(cfg, net, repo_res):
    kind, info = repo_res
    if kind == "row":
        return info
    base = f"repos/{cfg.owner}/{cfg.repo}"
    branch = info.get("default_branch") if isinstance(info, dict) else None
    if not _branch_ok(branch):
        return "unknown", "GitHub did not name a default branch doctor can use"
    try:
        net.gh.rest("GET", f"{base}/branches/{urllib.parse.quote(branch, safe='/')}/protection")
    except GhError as e:
        if e.status == 403 and "upgrade to github pro" in e.detail.lower():
            return "warn", ("branch protection is not available on a private repository of a free GitHub account, so nothing on GitHub makes a pull request "
                            "wait for your checks before it merges: the gate runs in ghp and in your own CI, but GitHub will not enforce it")
        if e.status == 404:
            return "warn", ("the default branch is not protected, or the token cannot read its protection: without a rule, a pull request can merge "
                            "before your checks pass")
        if e.status == 403:
            return "unknown", "the token cannot read the default branch's protection (HTTP 403)"
        return _problem(e, net)
    return "pass", "the default branch has protection rules (doctor does not judge them)"


def _overlap_row(repo):
    try:
        ids = {k["id"] for k in kits.installed(str(repo))}
    except kits.KitError:
        return "unknown", "the Spec Kit extension registry cannot be read, so extensions that overlap with ghp cannot be listed: run `ghp kits verify`"
    hit = [n for n in OVERLAP_IDS if n in ids]
    if not hit:
        return "pass", "ok"
    return "warn", (f"the Spec Kit extension(s) {', '.join(hit)} are installed and also turn tasks into issues or Project items: "
                    "two tools writing the same things can duplicate them, so use one of them per story")


def _bad(mode):
    return {"strict": "fail", "advisory": "warn"}.get(mode, "unknown")


def _stack_row(repo, cfg, mode):
    result, _, _ = stack.show(str(repo), mode or "advisory", cfg.strict["test_command"] if cfg else None)
    if result["status"] == "approved" and not result["notes"]:
        return "pass", "ok"
    if result["status"] == "none":
        return _bad(mode), f"no stack profile yet{' (optional)' if mode == 'advisory' else ''}: run `ghp stack detect`"
    what = f"ghp.stack.json is {result['status']}" if result["status"] != "approved" else f"the approved profile has {len(result['notes'])} note(s)"
    return _bad(mode), f"{what}: run `ghp stack show` for the next step"


def _kits_row(repo, mode):
    try:
        findings = kits.verify(str(repo), kits.load_lock(str(repo)))["findings"]
    except (kits.KitError, ConfigInvalid):
        return _bad(mode), "the kit registry or ghp.kits.lock.json cannot be trusted: run `ghp kits verify`"
    if findings:
        return _bad(mode), f"{len(findings)} kit finding(s) (unpinned or changed): run `ghp kits verify`"
    return "pass", "ok"


def _crash(e):
    """A fixed text plus the exception CLASS name (code-defined; never the message, which may carry user text)."""
    return "unknown", f"this check failed unexpectedly ({type(e).__name__}) and gave no result"


def _safe(fn, wrap, *args):
    """fn(*args), or `wrap(crash row)` when it raises: a diagnostic must report, not crash."""
    try:
        return fn(*args)
    except Exception as e:
        return wrap(_crash(e))


def run(repo=".", gh=None, offline=False):
    """`{"rows": [{"id", "state", "message"}]}` in `ROW_IDS` order. `gh` is the transport (reads only; never armed); None or `offline` reads nothing from GitHub."""
    out, net, row = {}, _Net(gh, offline), (lambda r: r)
    out["git"] = _safe(_git_row, row, repo)
    out["specify"] = _safe(_specify_row, row, repo)
    out["shallow"] = _safe(_shallow_row, row, repo)
    out["gh"] = _safe(_gh_row, row, repo)
    out["identity"], early, who = _safe(_identity, lambda r: (r, ("unknown", "not checked: the identity check failed"), None), net)
    cfg, out["config"] = _safe(_load_config, lambda r: (None, r), repo)
    repo_res = _safe(_repo, lambda r: ("row", r), cfg, net)
    private = repo_res[1].get("private") if repo_res[0] == "ok" and isinstance(repo_res[1], dict) else None
    out["scopes"] = early or _safe(_scopes_row, row, who, None if type(private) is not bool else ("private" if private else "public"))
    target, out["config-target"] = _safe(_load_target, lambda r: (None, r), repo)
    out["extension"] = _safe(_version_row, row, repo, EXT_PATH, "extension")
    out["workflow"] = _safe(_version_row, row, repo, WF_PATH, "workflow")
    trust, why = _safe(_trust, lambda r: (None, "approval settings could not be read"), repo)
    out["approvers"] = _safe(_approvers_row, row, trust, why, who, net)
    out["writers"] = _safe(_writers_row, row, trust)
    out["project-workflows"] = _safe(_workflows_row, row, cfg, net)
    out["protection"] = _safe(_protection_row, row, cfg, net, repo_res)
    out["overlap"] = _safe(_overlap_row, row, repo)
    # the mode a stack or kit problem is judged by: strict if either config says so; unknown when the working-tree config is invalid
    invalid = cfg is None and out["config"][1].startswith("ghp.config.json:")
    mode = "strict" if "strict" in (getattr(cfg, "mode", None), getattr(target, "mode", None)) else (None if invalid else "advisory")
    out["stack"] = _safe(_stack_row, row, repo, cfg, mode)
    out["kits"] = _safe(_kits_row, row, repo, mode)
    return {"rows": [{"id": i, "state": out[i][0], "message": comments._show(out[i][1])} for i in ROW_IDS]}


def failed(result):
    return any(r["state"] == "fail" for r in result["rows"])


def render(result):
    """The text form: one line per row, `state  id  message`."""
    return "\n".join(f"{r['state']:<8}{r['id']:<19}{r['message']}".rstrip() for r in result["rows"])


def register(sub):
    p = sub.add_parser("doctor", help="what is wrong with this setup: git, gh, sign-in, scopes, config, approvers, Project workflows, branch protection "
                                      "(read only; exit 1 when a row fails)")
    p.add_argument("--json", action="store_true", help="print the rows as JSON")
    p.add_argument("--offline", action="store_true", help="do not read GitHub: its rows are 'unknown (offline)'")


def run_verb(args, gh):
    result = run(".", None if args.offline else gh)
    print(json.dumps(result, indent=2) if args.json else render(result))
    return 1 if failed(result) else 0
