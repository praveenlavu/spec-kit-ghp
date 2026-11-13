"""`ghp branch`: tie the local feature branch to the issue as `<branch_prefix>gh<n>-<slug>` (local git only, never pushes, no network).

What changed from ado: the name is `gh<n>` (ado: `ab<id>`); the slug comes from `--title` or, without it, from the first line of the issue's
ledger (`# DoD — #<n>: <title>`, read confined to the repository) because no call is made; `branch --link` (Azure Repos) is not ported (D9);
`--id-from NAME` prints the issue number a branch name is tied to, and with `--base REF` takes the branch prefix from the PR's TARGET branch when
that one is strict (a PR must not change the prefix it is judged by). Where several local branches are tied to the issue and none is the
planned name, ado took the first; this refuses (the user chooses). A branch name that reaches `git switch` / `git branch -m` always passed
`prs.check_branch` (letters, digits, `.`, `_`, `-`, `/`; never a leading `-`).
"""
import json
import re
import subprocess
import unicodedata
from pathlib import Path

from . import comments, dod, gate, issues, prs
from .config import Config, ConfigInvalid, _resolve_base

SLUG_MAX = 40
# a Spec Kit feature segment: sequential `001-x` or timestamp `20260319-143022-x`
_SPECKIT_SEGMENT = re.compile(r"[0-9]{3,}-|[0-9]{8}-[0-9]{6}-")
_TITLE_LINE = re.compile(r"# DoD — #([1-9][0-9]{0,9}): ([^\n]*)")


def slug(title):
    """ASCII-folded, lowercase `[a-z0-9-]`, dashes collapsed, at most 40 chars, cut at a word boundary when it has one; never empty."""
    folded = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode().lower()
    full = re.sub(r"[^a-z0-9]+", "-", folded).strip("-")
    cut = full[:SLUG_MAX]
    if len(full) > SLUG_MAX and full[SLUG_MAX] != "-" and "-" in cut:  # mid-word: drop the partial word
        cut = cut.rsplit("-", 1)[0]
    return cut.rstrip("-") or "issue"


def _git(*args):
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise ValueError("`git` is not installed or not on PATH")


def _out(*args):
    r = _git(*args)
    return None if r.returncode else r.stdout.strip()


def require_work_tree():
    """Before anything else: `ghp branch` only works inside a git work tree."""
    if _out("rev-parse", "--is-inside-work-tree") != "true":
        raise ValueError("not a git repository (or not inside its work tree)")


def _default_branches(heads):
    """The default branch's possible names: origin/HEAD, init.defaultBranch, a local main/master. May be empty."""
    names = {_out("config", "init.defaultBranch")} | {h for h in heads if h in ("main", "master")}
    origin_head = _out("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    if origin_head:
        names.add(origin_head.removeprefix("refs/remotes/origin/"))
    return names - {None, ""}


def _renamable(current, heads):
    """Rename only a Spec Kit branch made for this spec, with no upstream, when the default branch is known
    and it is not that branch. Anything else gets a new branch instead."""
    defaults = _default_branches(heads)
    return (bool(defaults) and current not in defaults and _made_for_current_spec(current)
            and not _out("for-each-ref", "--format=%(upstream)", "refs/heads/%s" % current))


def _made_for_current_spec(current):
    """A Spec Kit branch whose feature segment is the spec dir `.specify/feature.json` points at (this spec)."""
    segment = current.rsplit("/", 1)[-1]
    if not _SPECKIT_SEGMENT.match(segment):
        return False
    try:
        data = json.loads((Path(_out("rev-parse", "--show-toplevel")) / ".specify" / "feature.json")
                          .read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, TypeError):
        return False
    spec_dir = data.get("feature_directory") if isinstance(data, dict) else None
    return isinstance(spec_dir, str) and Path(spec_dir).name == segment


def title_from_ledger(cfg, number):
    """The issue title as `ghp dod` wrote it on the ledger's first line, or "" (no ledger, unreadable, another issue's). Untrusted text: it only
    ever becomes a slug."""
    md = gate._read(dod.dod_file(dod.safe_dir(cfg.dod_path), number), ".", set())
    m = _TITLE_LINE.match(md.split("\n", 1)[0]) if md else None
    return m.group(2) if m and int(m.group(1)) == number else ""


def plan(number, title, prefix):
    """{"branch", "action": exists|switch|rename|create[, "from"]} for tying HEAD to the issue."""
    target = "%sgh%d-%s" % (prefix, number, slug(title))
    prs.check_branch("issue", target)
    current = _out("symbolic-ref", "--quiet", "--short", "HEAD")  # None when detached
    if current and prs.issue_from_branch(current, prefix) == number:
        return {"branch": current, "action": "exists"}
    heads = [r.removeprefix("refs/heads/")
             for r in (_out("for-each-ref", "--format=%(refname)", "refs/heads") or "").splitlines()]
    mine = [h for h in heads if prs.issue_from_branch(h, prefix) == number]
    if target in mine:
        return {"branch": target, "action": "switch"}
    if len(mine) > 1:
        raise ValueError("several local branches are tied to #%d (%s); switch to the right one yourself" % (
            number, ", ".join(comments._show(h, 80) for h in mine[:5])))
    if mine:
        return {"branch": mine[0], "action": "switch"}
    if current and _renamable(current, heads):
        return {"branch": target, "action": "rename", "from": current}
    return {"branch": target, "action": "create"}


def apply(p):
    cmd = {"switch": ["switch", p["branch"]], "create": ["switch", "-c", p["branch"]],
           "rename": ["branch", "-m", p.get("from"), p["branch"]]}.get(p["action"])
    if cmd:
        for name in ([p.get("from"), p["branch"]] if p["action"] == "rename" else [p["branch"]]):
            prs.check_branch("issue", name)  # no leading '-', no refspec or revision syntax reaches git
        r = _git(*cmd)
        if r.returncode:
            err = (r.stderr.strip().splitlines() or ["git failed"])[-1]
            raise ValueError("git %s failed: %s" % (" ".join(cmd), comments._show(err, 200)))
    return {**p, "applied": True}


def _prefix(base):
    """The branch prefix `--id-from` judges by: the working tree's, unless `base` (the PR's target) has a strict config, whose prefix wins. It is `gate.target_config`
    (T23): a target config, profile or lockfile that cannot be read fails closed when the base is a real commit or the working tree's config is strict."""
    from . import gate  # lazy: gate imports this package's modules
    return gate.target_config(base, ".").branch_prefix


def register(sub):
    p = sub.add_parser("branch", help="name the local feature branch <branch_prefix>gh<n>-<slug> from the issue (local, no network; "
                                      "dry run unless --apply)")
    p.add_argument("ref", nargs="?", help="the issue: 5, #5 or owner/repo#5")
    p.add_argument("--title", help="the title the slug is made from (default: the title on the issue's ledger, else `issue`)")
    p.add_argument("--apply", action="store_true", help="create, rename (a Spec Kit branch made for this spec, with no upstream) or switch to "
                                                        "the branch; the default branch is never renamed")
    p.add_argument("--id-from", metavar="NAME", help="print the issue number the branch NAME is tied to (exit 1 and `null` if none)")
    p.add_argument("--base", metavar="REF", help="with --id-from: the ref the PR targets (CI: origin/<target>); when its ghp.config.json is strict, "
                                                 "its branch_prefix is used, not the PR's")


def run(args, gh):
    if args.id_from is not None:  # local only: no config needed for the name, no request
        if args.ref is not None or args.apply or args.title is not None:
            raise ValueError("--id-from takes no issue, --title or --apply")
        n = prs.issue_from_branch(args.id_from, _prefix(args.base))
        print(json.dumps(n))
        return 0 if n else 1
    if args.ref is None or args.base is not None:
        raise ValueError("give the issue (`ghp branch 5`), or --id-from NAME" if args.ref is None else "--base goes with --id-from")
    ref = issues._resolve(args.ref)  # config, then the ref: a bad one is refused before any call
    cfg = Config.load()
    require_work_tree()
    title = args.title if args.title is not None else title_from_ledger(cfg, ref.number)
    p = plan(ref.number, title, cfg.branch_prefix)
    out = apply(p) if args.apply else {**p, "applied": False}
    if not args.apply and not title and p["action"] in ("create", "rename"):  # the fallback slug: a spec must not record this name as the branch
        out.update(final=False, note="not the final name: there is no ledger title yet, so the slug is the fallback 'issue'; after `ghp dod %d` "
                                     "this names the branch from the issue title" % ref.number)
    print(json.dumps(out))
    return 0
