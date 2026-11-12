"""`ghp pr create`: open a pull request that closes an issue (ado's `prs`, port with the Azure Repos paths dropped, D9).

What changed from ado:
- The link is text, not an API object: the title carries `#N` and the body ends with `Closes #N` (`Closes #1, closes #2` for several
  issues). GitHub acts on the keyword only when the PR's base is the repository's DEFAULT branch; any other base gets a warning in the plan
  (`ghp done` closes the issue then). The live run (T26) confirmed it: a merge into any other base leaves the issue open.
- The body is the caller's text (defanged: `@name` becomes `[mention removed]`), then a ledger summary (counts only: no ledger text reaches the
  PR), then the `Closes` line. At most 60,000 characters; a longer body is refused, never cut.
- Creation is `POST repos/{o}/{r}/pulls` through `Gh.rest` (the JSON body on stdin), NOT `gh pr create`. PLAN AC4 says "apply uses
  `gh pr create` via the same arming gate"; the gate is structural in `Gh` (a write raises WriteNotArmed until the human gate armed it), and a
  `gh pr create` subprocess would be a second door that bypasses it. The decision is recorded in the T21 report.
- Nothing is ever sent twice. The plan reads the open PRs of the head branch: one that exists means "nothing to create". A create whose
  outcome is unknown (timeout, 5xx, a reply that is not a PR) re-reads the PR list for our head and base (bounded wait, the list may lag) and
  uses the PR it finds; if none shows up the original error is raised and the message says not to repeat the call by hand: re-run `ghp pr create`
  (its plan reads first).
- Unticked tasks refuse the PR unless `--allow-incomplete` (then a draft). A tasks.md without tasks while sub-issues carry task markers is
  read as "was it emptied?", as in ado.
- `--check-committed` is not here. Comment mode (T28): `--apply` needs an approver's `/approve approve-pr <hash>` of this plan (`approvals.approve_hook`,
  trust from origin/HEAD and the PR's `--base`, comment mode on either wins). Strict mode (T23): `strict.check(..., "pr")` judges the head branch first and refuses with its findings (JSON on stdout, exit 2); a
  pass stamps `used` on the overrides it relied on, after the human gate and just before anything is sent.

`MAX_ID`, `check_branch`, `issue_from_branch`/`issue_from_text` (ASCII `gh<n>` prefixes; a `#N` in text must be the only issue named outside
code and quotes), the git helpers and `review_blocks` (ledger read from the head commit, never the tree) are the ado code; `gate`, `strict`,
`status` and `done` import them (T22 to T25).

Commit messages (T26c/T21a): a closing keyword in a commit message of base..head closes issues on merge too (the live run saw closer = Commit for a
branch commit, a squash commit message and the PR title through the merge commit), so `pr create` also scans them with the SAME scanner
(`other_closes`): E_COMMITS_CLOSE_OTHERS unless `--allow-extra-closes`; at most MAX_COMMITS commits and MAX_MESSAGE_BYTES of messages are read
(E_TOO_MANY_COMMITS beyond that), and a log git cannot give is E_COMMITS_UNREADABLE (all exit 2, nothing sent).

VERIFIED live in T26 (docs/evidence/T26.md, scratch repository, user Project): the POST pulls reply is the full pull request object `_pr` reads; the
`head=owner:branch` list filter works (without the owner GitHub ignores it); the list did not lag a fresh PR in 18 samples (the re-read wait was never
needed); a draft PR was accepted on a private repository; GitHub closes the issue from a `Closes #N` line in the body of a PR merged into the default
branch (merge, squash and rebase), and ignores it for any other base; a second POST for the same head and base is a 422 (the reason is kept in the
error text since T26d). Every PR-text form that closed is found by the scanner, which also flags six forms GitHub ignores (by design).
UNVERIFIED: drafts on a repository of a plan that has none; the list lag under load; closing keywords in squash commit messages when the repository's
squash-message setting is not the default; the shape of a commit closer's `repository` in a fork network (see `ghp done`).
"""
import hashlib
import html
import json
import os
import re
import subprocess
import threading
import unicodedata
import urllib.parse
from pathlib import Path, PurePosixPath

from . import approvals, comments, decisions, dod, gate, issues, plan as write_gate, push as push_verb, tasks
from .config import Config, _rev, git_env  # noqa: F401  (git_env: re-exported, the one definition is config's)
from .dod import _visible  # noqa: F401  (the one fence/comment blanker lives with the ledger sections)
from .gh import UNKNOWN_OUTCOME, GhError, scrub
from .push import _cell
from .refs import _MAX as MAX_ID

MAX_BODY = comments.MAX_COMMENT  # characters; GitHub's own cap is higher and unverified, so ghp stays below it
MAX_ISSUES = 20  # `Closes #n` lines in one PR
MAX_LISTED = 20  # task ids named in the plan (the digest covers all of them)
PREPARED_ARGS = ".ghp/pr-args.json"
PREPARED_BODY = ".ghp/pr-body.md"
PULLS = "repos/%s/%s/pulls"


class PrError(ValueError):
    """A named refusal (exit 2 in the CLI): the message starts with the code."""

    def __init__(self, code, detail):
        self.code = code
        super().__init__("%s: %s" % (code, detail))


# ---- names and references ----------------------------------------------------------------------------------------

def with_ref(title, number):
    """`title` with `#number` in it: unchanged when the token is already there (not part of a longer word or number), else appended."""
    token = "#%d" % number
    return title if re.search(r"(?<![\w#&])%s(?!\w)" % re.escape(token), title) else "%s %s" % (title, token)


def closes_line(numbers):
    """`Closes #1, closes #2`: GitHub needs the keyword before EACH issue."""
    return ", ".join(("Closes" if i == 0 else "closes") + " #%d" % n for i, n in enumerate(numbers))


_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")


def check_branch(kind, name):
    """Refuse refspec tricks (+x, :x, a:b), options, and odd git ref forms before any push/POST."""
    if (not isinstance(name, str) or not _BRANCH.fullmatch(name) or ".." in name or "//" in name
            or name.endswith(("/", ".", ".lock"))):
        raise ValueError("invalid %s branch name %s: use letters, digits, '.', '_', '-', '/' only" % (kind, comments._show(name, 80)))


_OUR_ID = re.compile(r"[gG][hH]([1-9][0-9]{0,10})(?:-|\Z)")  # at most 11 digits: a longer run is no match at all, in linear time
_BRANCH_REF = re.compile(r"(?<![\w&])#([1-9][0-9]{0,10})(?![0-9])")
_TEXT_REF = re.compile(r"(?<![\w&/])#([1-9][0-9]{0,10})(?![0-9])")
_CODE_SPAN = re.compile(r"`[^`\n]*`")


def _number(digits):
    return int(digits) if int(digits) <= MAX_ID else None


def issue_from_branch(name, prefix):
    """The issue a branch is tied to: `<prefix>gh<n>-…` / `<prefix>gh<n>` at the start, else `#<n>` anywhere (not inside a word). Only
    ASCII digits count, 1 to 2**31-1. Spec Kit never emits `gh<digits>`, so its `001-x` / `20261028-143022-x` branches never count, whatever
    the prefix."""
    name = name[len("refs/heads/"):] if name.startswith("refs/heads/") else name
    m = (name.startswith(prefix) and _OUR_ID.match(name, len(prefix))) or _BRANCH_REF.search(name)
    return _number(m.group(1)) if m else None


def issue_from_text(text):
    """The ONE issue a text (a PR title or body) names as `#<n>`, or None: text in code fences, HTML comments, code spans and quoted lines
    is not read (a quoted or example `#7` is not this PR's issue), and two different numbers are ambiguous, so None too."""
    if not isinstance(text, str):
        return None
    lines = [l for l in _visible(text)[0].splitlines() if not l.lstrip().startswith(">")]
    found = {_number(m.group(1)) for l in lines for m in _TEXT_REF.finditer(_CODE_SPAN.sub("", l))}
    return next(iter(found)) if len(found) == 1 and None not in found else None


MAX_SHOWN = 10
WINDOW = 60  # characters after a closing keyword in which a reference counts as named by it
_MAX_REF = 260  # more than the longest reference the pattern below can match (a github.com URL with a 39 + 100 character owner/repo)
_UNSTABLE = ("", "", 0)  # a character-reference chain that does not settle: unreadable, so refused like a target
_ROUNDS = 8
_KEYWORD = re.compile(r"(?<![A-Za-z0-9])(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)(?![A-Za-z0-9])", re.I)
_PART = r"[A-Za-z0-9._-]"
# after normalisation (`:`, quotes, brackets, tags, emphasis are spaces): a github.com issue or pull URL (scheme and www are noise before it),
# `owner/repo#N`, `GH-N`, `#N`; leading zeros allowed (compared as integers). Names are length-bounded, so one search is linear in its window.
_REF = re.compile(r"github\.com/(%s{1,39})/(%s{1,100})/(?:issues|pull)/([0-9]{1,40})(?![0-9])"
                  r"|(%s{1,39})/(%s{1,100})#([0-9]{1,40})(?![0-9])|(?<![A-Za-z0-9])gh-([0-9]{1,40})(?![0-9])|#([0-9]{1,40})(?![0-9])"
                  % (_PART, _PART, _PART, _PART), re.I)
_PUNCT = re.compile(r"[*~`<>()\[\]|:\\\s]")


def _unescape(text):
    """`html.unescape` until the text stops changing, at most _ROUNDS times: (text, whether it settled)."""
    for _ in range(_ROUNDS):
        again = html.unescape(text)
        if again == text:
            return text, True
        text = again
    return text, html.unescape(text) == text


def _strip_invisible(text):
    """No Unicode format (Cf: zero width, bidi, soft hyphen, ...) or control (Cc) character; a tab or carriage return is a space, `\n` stays."""
    if text.isascii():
        return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text.replace("\t", " ").replace("\r", " "))
    return "".join(" " if c in "\t\r" else c for c in text if c in "\n\t\r" or unicodedata.category(c) not in ("Cf", "Cc"))


def _without_comments(text):
    """`text` with every <!-- ... --> removed (an unterminated one hides the rest), in linear time."""
    out, i = [], 0
    while True:
        j = text.find("<!--", i)
        if j < 0:
            out.append(text[i:])
            return "".join(out)
        out.append(text[i:j])
        k = text.find("-->", j + 4)
        if k < 0:
            return "".join(out)
        i = k + 3


def _flatten(text):
    """One line of plain words: emphasis (`*`, `~`; an underscore stays, it is part of names like jdoe_acme), brackets, quotes, tags, `:` and backslashes are spaces, whitespace collapsed."""
    return " ".join(_PUNCT.sub(" ", text).split())


def normalised(text):
    """([the readings of `text` to scan], whether its character references settled). GitHub's parser cannot be checked offline, so the text is
    read every way it might be: character references decoded (`&#35;`, `&num;`, `&nbsp;`), compatibility forms folded (NFKC), invisible characters
    dropped, then twice again: with the HTML comment markers blanked (so a comment's content is read) and with the comments cut out (so a
    comment inside a word joins it); each flattened to plain words."""
    text, settled = _unescape(text)
    text = _strip_invisible(unicodedata.normalize("NFKC", text))
    return [_flatten(text.replace("<!--", " ").replace("-->", " ")), _flatten(_without_comments(text))], settled


def closing_targets(text, owner, repo):
    """Every (owner, repo, number) that a closing keyword in `text` is followed by within WINDOW characters, a chain of references after it
    included (`Closes #9, #1`, `#9 and #1`, `#9/#1`, `#9 #1` name both). Keywords: close/closes/closed, fix/fixes/fixed, resolve/resolves/resolved, any
    case, word-bounded. References: `#N`, `GH-N`, `owner/repo#N`, a github.com issue or pull URL (with or without scheme, www, angle brackets);
    leading zeros count (`#099` is 99). Code, quotes, HTML comments and markup are read too. Over-reading only costs a refusal the user can lift
    (`--allow-extra-closes`): ordinary prose such as `this fixes the flow in section #3` IS flagged. A chain of character references that does not
    settle is returned as `_UNSTABLE`. Linear: each reading is scanned once, a keyword's window ends at the next keyword."""
    found = set()
    readings, settled = normalised(text)
    if not settled:
        found.add(_UNSTABLE)
    for flat in readings:
        keywords = list(_KEYWORD.finditer(flat))
        for i, kw in enumerate(keywords):
            limit = keywords[i + 1].start() if i + 1 < len(keywords) else len(flat)
            pos = kw.end()
            while pos < limit:
                m = _REF.search(flat, pos, min(limit, pos + WINDOW + _MAX_REF))
                if m is None or m.start() - pos > WINDOW:
                    break
                g = m.groups()
                o, r, n = g[0:3] if g[0] else g[3:6] if g[3] else (None, None, g[6] or g[7])
                if int(n) > 0:
                    found.add((o or owner, r or repo, int(n)))
                pos = m.end()
    return found


def without_generated(body, numbers):
    """`body` without the `Closes #n, closes #m` line that `compose` itself ends it with (an exact match of the generated line, at the end)."""
    line = closes_line(numbers) + "\n"
    return body[:-len(line)] if body.endswith(line) else body


def other_closes(texts, owner, repo, numbers):
    """Strings (sorted) for what the closing keywords in `texts` would close beyond the intended `numbers` of this repository: `owner/repo#N`. Compared
    as (owner, repo, number), case-folded; a cross-repository target is never the intended one, whatever its number."""
    intended = {(issues._fold(owner), issues._fold(repo), n) for n in numbers}
    found = set()
    for text in texts:
        found |= {t for t in closing_targets(text, owner, repo) if (issues._fold(t[0]), issues._fold(t[1]), t[2]) not in intended}
    return sorted("character references nested too deeply to read" if t == _UNSTABLE else "%s/%s#%d" % t for t in found)


# ---- git (ado's helpers) -------------------------------------------------------------------------------------------

class _Refuse(Exception):
    """A reason `ghp pr create` must refuse while review blocking is configured."""


# replace refs and old-style grafts rewrite history as git reports it: never let them hide a change
GIT_TIMEOUT = 30  # seconds per git call


def _git(repo, *args):
    try:  # bytes: file names need not be UTF-8
        return subprocess.run(["git", "--no-pager", "--no-replace-objects", "-C", repo, *args], check=False, capture_output=True,
                              stdin=subprocess.DEVNULL, timeout=GIT_TIMEOUT, env=git_env())
    except FileNotFoundError:
        raise _Refuse("cannot check the review: `git` is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        raise _Refuse("cannot check the review: git did not answer within %d s" % GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        raise _Refuse("cannot check the review: git could not be run")


MAX_COMMITS = 200  # commits of base..head the closing-keyword scan reads
MAX_MESSAGE_BYTES = 64 * 1024  # bytes of commit messages (git's own output, one NUL after each) it reads; beyond either bound it refuses


def _tip(repo, refs):
    """The full commit id of the first of `refs` that names a commit, or "" (a ref that does not exist, git that fails). Object ids only: the log below
    is asked for `<id>..<id>`, so a branch name can never be read as an option or as a path."""
    for ref in refs:
        r = _git(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", ref + "^{commit}")
        out = r.stdout.decode("ascii", "replace").strip() if r.returncode == 0 else ""
        if re.fullmatch(r"[0-9a-f]{40}", out):
            return out
    return ""


def commit_messages(base, head, repo="."):
    """The messages (str, in git's order) of the commits of `base..head`, for the closing-keyword scan: a keyword in a branch commit, a merge or squash
    commit message, or the PR title that becomes a merge commit message closes issues too (T26: closer = Commit). `base` is read from `origin/<base>`
    first (the branch GitHub compares with), then the local branch; `head` is the local branch, then `origin/<head>`. Same git runner as the rest of
    this module (sanitised environment, replace refs and grafts off, no prompt, GIT_TIMEOUT s); the log is read through a pipe and cut off, with the
    process killed, as soon as it holds more than MAX_MESSAGE_BYTES. PrError E_TOO_MANY_COMMITS (more than MAX_COMMITS commits or MAX_MESSAGE_BYTES
    bytes: nothing is scanned, never a part of it) and E_COMMITS_UNREADABLE (a branch git does not know, an unborn or missing repository, git absent,
    failing or too slow): both fail closed."""
    where = "between base '%s' and head '%s'" % (comments._show(base, 80), comments._show(head, 80))
    try:
        b = _tip(repo, ["refs/remotes/origin/" + base, "refs/heads/" + base])
        h = _tip(repo, ["refs/heads/" + head, "refs/remotes/origin/" + head])
        if not b or not h:
            raise PrError("E_COMMITS_UNREADABLE", "cannot read the commits %s: %s is not a branch git knows here (fetch it, or check the name); nothing was sent"
                          % (where, "the base" if not b else "the head"))
        argv = ["git", "--no-pager", "--no-replace-objects", "-c", "core.fsmonitor=false", "-C", repo, "log", "-z", "--format=%B", "--no-color",
                "--max-count=%d" % (MAX_COMMITS + 1), "%s..%s" % (b, h), "--"]
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=git_env())
    except _Refuse:
        raise PrError("E_COMMITS_UNREADABLE", "cannot read the commits %s: git is missing, failed or did not answer in %d s; nothing was sent" % (where, GIT_TIMEOUT)) from None
    except (OSError, subprocess.SubprocessError):
        raise PrError("E_COMMITS_UNREADABLE", "cannot read the commits %s: git could not be started; nothing was sent" % where) from None
    timer = threading.Timer(GIT_TIMEOUT, proc.kill)
    timer.start()
    try:
        data = proc.stdout.read(MAX_MESSAGE_BYTES + 1)
        if len(data) > MAX_MESSAGE_BYTES:
            raise PrError("E_TOO_MANY_COMMITS", "the commits %s hold more than %d KiB of messages; ghp cannot scan them all, so it sends nothing" % (where, MAX_MESSAGE_BYTES // 1024))
        rc = proc.wait()
    finally:
        timer.cancel()
        proc.kill()  # a process that already ended ignores this
        proc.wait()
        proc.stdout.close()
    if rc != 0:
        raise PrError("E_COMMITS_UNREADABLE", "cannot read the commits %s: git failed or did not answer in %d s; nothing was sent" % (where, GIT_TIMEOUT))
    records = data.split(b"\0")
    if records and records[-1] == b"":
        records.pop()
    if len(records) > MAX_COMMITS:
        raise PrError("E_TOO_MANY_COMMITS", "more than %d commits %s; ghp cannot scan them all, so it sends nothing" % (MAX_COMMITS, where))
    return [r.decode("utf-8", "replace") for r in records]


def _text(r, what):
    """stdout of a git run, or _Refuse: failed runs and non-UTF-8 output never pass silently."""
    if r.returncode:
        err = (r.stderr.decode("utf-8", "replace").strip().splitlines() or ["git failed"])[-1]
        raise _Refuse("cannot check the review: %s (%s)" % (what, err))
    try:
        return r.stdout.decode("utf-8")
    except UnicodeDecodeError:
        raise _Refuse("cannot check the review: %s (git output is not UTF-8)" % what)


def _repo_rel(repo, path):
    """cwd-relative `path` as a repo-root-relative posix path ('.' = root), or None if outside."""
    top = Path(_text(_git(repo, "rev-parse", "--show-toplevel"), "not a git repository").strip()).resolve()
    try:
        return Path(os.path.normpath(Path(repo).resolve() / path)).relative_to(top).as_posix()
    except ValueError:
        return None


def spec_dir_in_tree(spec_dir, dod_path, sha, repo="."):
    """The ledger's `Spec dir:` (untrusted) as a repo-root-relative path, or None unless it is
    relative, has no `..` part, is not `.`, is not dod_path or inside it, and commit `sha` has a
    regular-file (not symlink) spec.md or tasks.md directly in it."""
    if not spec_dir or PurePosixPath(spec_dir).is_absolute() or ".." in PurePosixPath(spec_dir).parts:
        return None
    rel, ledgers = _repo_rel(repo, spec_dir), _repo_rel(repo, dod_path)
    if rel in (None, ".") or (ledgers is not None and (rel + "/").startswith(ledgers + "/")):
        return None
    want = {"%s/spec.md" % rel, "%s/tasks.md" % rel}
    out = _text(_git(repo, "ls-tree", "--full-tree", "-z", sha, "--", *sorted(want)), "ls-tree %s" % sha)
    for entry in filter(None, out.split("\0")):  # "<mode> <type> <sha>\t<path>"; tree paths never follow symlinks
        meta, _, name = entry.partition("\t")
        if name in want and meta.split()[:2] in (["100644", "blob"], ["100755", "blob"]):
            return rel
    return None


def _section2_line(md):
    i = dod._section2_at(md)
    return -1 if i == -1 else len(md[:i].splitlines())


_REVIEW_HEADING = re.compile(r"^### Review\b.*$", re.M)
_HEADING_SHA = re.compile(r"@ `([^`]*)`")
_SHA_TEXT = re.compile(r"[0-9a-fA-F]{7,40}")
_BLOCKS_PR = re.compile(r"^- \*\*Blocks PR:\*\*\s*(yes|no)\b", re.M | re.I)
_SEVERITY = re.compile(r"^- \*\*Confirmed by severity:\*\*(.*)$", re.M)
_RERUN = " — re-run speckit.ghp.review"


def review_blocks(number, block_on, head, dod_path, repo=".", push=False, squash=False, at_commit=False):
    """Why `ghp pr create` must refuse under `review_block_on`, or None. Empty block_on → None
    (advisory). Blocking is opted into, so missing info (no review, no git, unknown head) refuses.
    The ledger is read from the head commit, never the working tree. `squash` (`ghp strict check --phase done`): the
    reviewed commit is no ancestor of a squash-merged head, so only the verdict and counts are checked, not the staleness;
    a detached HEAD is read as the commit it is, without the origin comparison. `at_commit` (`ghp strict check --head <sha>`, CI):
    `head` is a full commit id, read as that commit, without the origin comparison."""
    if not block_on:
        return None
    try:
        _check_review(number, block_on, head, dod_path, repo, push, squash, at_commit)
    except _Refuse as e:
        return str(e)
    return None


def _check_review(number, block_on, head, dod_path, repo, push, squash=False, at_commit=False):
    detached = False
    rev = None  # explicit refs/heads below: a tag named like the branch never shadows it
    if head == "HEAD":  # full ref, not --short: a tag named like the branch makes --short say heads/<name>
        sym = _git(repo, "symbolic-ref", "--quiet", "HEAD")
        detached = squash and sym.returncode == 1  # a CI checkout of the merged commit: there is no branch to compare
        if detached:
            rev = "HEAD^{commit}"
        else:
            ref = _text(sym, "HEAD is detached; pass a branch").strip()
            if not ref.startswith("refs/heads/"):
                raise _Refuse("cannot check the review: HEAD points at %r, not a branch" % ref)
            head = ref[len("refs/heads/"):]
    elif at_commit:
        if not re.fullmatch(r"[0-9a-f]{40}", head):
            raise _Refuse("unknown head %r" % head)
        detached, rev = True, "%s^{commit}" % head
    head_sha = _text(_git(repo, "rev-parse", "--verify", rev or "refs/heads/%s^{commit}" % head), "unknown head %r" % head).strip()
    if not push and not detached:  # without --push the PR opens from the remote branch, so it must be what we check
        r = _git(repo, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/%s^{commit}" % head)
        if r.returncode == 0 and r.stdout.decode("ascii", "replace").strip() != head_sha:
            raise _Refuse("remote branch origin/%s differs from the reviewed local branch; push or re-review" % head)
    dod_rel = _repo_rel(repo, dod_path)
    ledger_rel = "%s/DoD_%d.md" % (dod_rel, number) if dod_rel not in (None, ".") else "DoD_%d.md" % number
    r = _git(repo, "show", "%s:%s" % (head_sha, ledger_rel)) if dod_rel is not None else None
    if r is None or r.returncode:
        raise _Refuse("no review logged: %s has no committed ledger %s" % (head, ledger_rel) + _RERUN)
    ledger_md = _text(r, "ledger %s" % ledger_rel)
    # blank fences/comments in the whole ledger first, so a fenced fake `## 4.` can't be the section found
    visible, still_open = _visible(ledger_md)
    # `ghp dod` escapes issue fences (F26), so an opener here is an older ledger or a hand edit: refused, not repaired.
    # Equal `## 2.` counts too: an unclosed opener plus a fenced example can shift fence parity and forge a §2.
    if (still_open or _section2_line(ledger_md) != _section2_line(visible)
            or len(dod._SECTION2_RE.findall(ledger_md)) != len(dod._SECTION2_RE.findall(visible))):
        raise _Refuse("cannot check the review: ledger contains an unclosed code fence or HTML comment (or one "
                      "hiding a `## 2.` heading); re-pull §1 with `ghp dod %d --refresh` (it escapes "
                      "issue text), or edit the ledger" % number)
    try:
        log = dod.section(visible, 4) or ""
    except dod.LedgerError as e:  # more than one visible `## 2.`: the review log cannot be located
        raise _Refuse("cannot check the review: %s" % e)
    live = [m for m in _REVIEW_HEADING.finditer(log) if not m.group(0).rstrip().endswith("(superseded)")]
    if not live:
        msg = "no review logged in ledger section 4"
        if any(not m.group(0).rstrip().endswith("(superseded)") for m in _REVIEW_HEADING.finditer(visible)):
            msg += " (a `### Review` block sits outside section 4, probably below `## 5.`: move it under `## 4.`)"
        raise _Refuse(msg + _RERUN)
    heading = live[-1]
    rest = log[heading.end():]
    nxt = re.search(r"^#{1,3} ", rest, re.M)
    block = rest[:nxt.start()] if nxt else rest
    verdicts = [v.lower() for v in _BLOCKS_PR.findall(block)]
    if not verdicts:
        raise _Refuse("latest review has no `Blocks PR:` line" + _RERUN)
    if "yes" in verdicts:
        raise _Refuse("latest review blocks the PR (`Blocks PR: yes`) — fix the findings and re-run speckit.ghp.review")
    sev_lines = _SEVERITY.findall(block)
    if len(sev_lines) > 1:
        raise _Refuse("latest review has more than one `Confirmed by severity:` line (ambiguous)" + _RERUN)
    pairs = [(k.lower(), int(n)) for k, n in re.findall(r"([A-Za-z][\w-]*)\s+(\d+)", "".join(sev_lines))]
    counts = dict(pairs)
    if len(counts) != len(pairs):
        raise _Refuse("latest review repeats a severity on its `Confirmed by severity:` line (ambiguous)" + _RERUN)
    for s in block_on:  # the review's own verdict may predate this config, so recount
        if s.lower() not in counts:
            raise _Refuse("latest review does not report a `%s` count on its `Confirmed by severity:` line" % s + _RERUN)
        if counts[s.lower()]:
            raise _Refuse("latest review has %d confirmed `%s` finding(s) and "
                          "review_block_on includes `%s` — fix them and re-run speckit.ghp.review" % (counts[s.lower()], s, s))
    if squash:
        return
    sha_m = _HEADING_SHA.search(heading.group(0))
    if not sha_m:
        raise _Refuse("latest review records no commit SHA" + _RERUN)
    sha = sha_m.group(1)
    if not _SHA_TEXT.fullmatch(sha):
        raise _Refuse("latest review SHA %r is not 7-40 hex characters" % sha + _RERUN)
    # object lookup only: a branch or tag named like the short SHA must never stand in for it
    objs = _git(repo, "rev-parse", "--disambiguate=%s" % sha.lower()).stdout.decode("ascii", "replace").split()
    if len(objs) != 1 or _git(repo, "cat-file", "-t", objs[0]).stdout.strip() != b"commit":
        raise _Refuse("review SHA %s is ambiguous or not a commit" % sha + _RERUN)
    full = objs[0]
    if _git(repo, "merge-base", "--is-ancestor", full, head_sha).returncode:
        raise _Refuse("review is stale: review is for %s, which is not an ancestor of %s" % (sha, head) + _RERUN)
    # Spec dir as the ledger had it at the reviewed commit: a later commit cannot widen what counts as spec
    old = _git(repo, "show", "%s:%s" % (full, ledger_rel))
    old_spec = dod.ledger_spec_dir(_text(old, "ledger %s at %s" % (ledger_rel, sha))) if old.returncode == 0 else None
    outside = _outside_ledger(repo, full, head_sha, dod_path, old_spec, number, whole_dirs=True)
    if outside:
        raise _Refuse("review is stale: review is for %s; later commits change %d file(s) "
                      "outside the ledger/spec" % (sha, len(outside)) + _RERUN)


def _outside_ledger(repo, base_sha, head_sha, dod_path, spec_dir, number, whole_dirs):
    """The files changed from commit `base_sha` to `head_sha` that are not the issue's ledger material. `whole_dirs`
    (reviews): the ledger plus anything under `dod_path` or the valid spec dir. Else (verification records) exactly the
    ledger, `Decisions_<n>.md` and `<spec dir>/tasks.md`. The spec dir is the one valid at `base_sha`, or one that is new
    since (step 2a commits it after the review). Raises _Refuse when git cannot answer."""
    dod_rel = _repo_rel(repo, dod_path)
    if dod_rel is None:
        raise _Refuse("cannot check the review: dod_path is outside the repository")
    pre = "%s/" % dod_rel if dod_rel != "." else ""
    ledger_rel = "%sDoD_%d.md" % (pre, number)
    # --no-renames: a move out of src/ into the ledger dir must still list the deleted path
    names = _text(_git(repo, "diff", "--name-only", "--no-renames", "-z", base_sha, head_sha, "--"),
                  "git diff %s %s" % (base_sha[:12], head_sha[:12])).split("\0")
    spec = spec_dir_in_tree(spec_dir, dod_path, base_sha, repo)
    if spec is None:  # step 2a commits the spec dir after the review: count it only if it is new since then
        new = spec_dir_in_tree(spec_dir, dod_path, head_sha, repo)
        if new and not _text(_git(repo, "ls-tree", "--full-tree", "-z", base_sha, "--", new), "ls-tree %s" % base_sha[:12]):
            spec = new
    if whole_dirs:
        prefixes = [p for p in (dod_rel, spec) if p not in (None, ".")]
        return [f for f in names if f and f != ledger_rel and not any(f.startswith(p + "/") for p in prefixes)]
    allowed = {ledger_rel, "%sDecisions_%d.md" % (pre, number)} | ({"%s/tasks.md" % spec} if spec else set())
    return [f for f in names if f and f not in allowed]


def only_ledger_since(repo, base_sha, head, dod_path, spec_dir, number):
    """True when everything changed from commit `base_sha` to `head` is the issue's ledger, its decisions file or
    the spec dir's tasks.md (the exact files, never a directory's worth). Raises _Refuse when git cannot answer."""
    return not _outside_ledger(repo, base_sha, head, dod_path, spec_dir, number, whole_dirs=False)


def uncommitted(repo, number, dod_path, spec_dir, rules_ids=()):
    """How many paths of the issue have changes that are not committed: its ledger, its decisions file, the `Rules_<id>.md` files of `rules_ids` (the
    ledger's `Rules from:`, digits-only; other issues' rules files are not this issue's) in `dod_path`, and the ledger's `Spec dir:`. These are the paths
    `speckit.ghp.pr` step 2a would commit, judged as that step does: the spec dir (untrusted) counts only when it is relative, has no `..`, is not `.`,
    is not `dod_path` or inside it (so with `dod_path` `.` none is valid), and holds a regular-file `spec.md` or `tasks.md` in the working tree; untracked
    files count. Read-only (`--no-optional-locks`: git does not refresh the index). Raises _Refuse (fixed texts) when git cannot answer or `dod_path`
    is outside the repository. (Used by the comment-mode wiring, T28.)"""
    dod_rel = _repo_rel(repo, dod_path)
    if dod_rel is None:
        raise _Refuse("cannot check the commits: dod_path is outside the repository")
    pre = "%s/" % dod_rel if dod_rel != "." else ""
    specs = [":(literal)%sDoD_%d.md" % (pre, number), ":(literal)%sDecisions_%d.md" % (pre, number)] + [":(literal)%sRules_%d.md" % (pre, i) for i in rules_ids]
    if spec_dir and not PurePosixPath(spec_dir).is_absolute() and ".." not in PurePosixPath(spec_dir).parts:
        rel = _repo_rel(repo, spec_dir)
        top = Path(repo) / spec_dir
        if (rel not in (None, ".") and dod_rel != "." and not (rel + "/").startswith(dod_rel + "/")
                and any((top / n).is_file() and not (top / n).is_symlink() for n in ("spec.md", "tasks.md"))):
            specs.append(":(literal)%s" % rel)
    r = _git(repo, "--no-optional-locks", "status", "--porcelain", "-z", "--untracked-files=all", "--", *specs)
    if r.returncode:
        raise _Refuse("cannot check the commits: git status failed")
    return len([e for e in r.stdout.split(b"\0") if e])


_USERINFO = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]*@")


def push_branch(head):
    """`git push -u origin <head>`, list-form (no shell; `head` passed `check_branch`, so it cannot start with '-'). {} or {"error": one
    line, scrubbed, url credentials removed}."""
    check_branch("head", head)
    try:
        r = subprocess.run(["git", "push", "-u", "origin", head], check=False, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           timeout=120, env=git_env())
    except FileNotFoundError:
        raise ValueError("`git` is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        return {"error": "git push did not finish within 120 s"}
    if not r.returncode:
        return {}
    lines = r.stderr.strip().splitlines() or ["git push failed"]
    fatal = next((l for l in lines if l.startswith(("fatal:", "error:"))), lines[-1])
    return {"error": comments._show(scrub(_USERINFO.sub(r"\1", fatal)), 300)}


# ---- inputs --------------------------------------------------------------------------------------------------------

def _read(path, what):
    """A small regular file inside the current directory (a symlink, a device, a path outside it, over 1 MiB or not UTF-8 is refused: the
    file's text goes into a public PR)."""
    text = gate._read(path, ".", set())
    if text is None:
        raise ValueError("cannot read %s %s (missing, not a plain file inside the repository, over 1 MiB or not UTF-8)" % (what, comments._show(path, 200)))
    return text


def load_prepared():
    """Read what `speckit.ghp.pr --prepare-only` wrote: (title, base, head, body_file)."""
    try:
        d = json.loads(_read(PREPARED_ARGS, "the prepared arguments"))
    except (ValueError, RecursionError):
        raise ValueError("cannot read %s; run `speckit.ghp.pr --prepare-only` first" % PREPARED_ARGS) from None
    vals = [d.get(k) if isinstance(d, dict) else None for k in ("title", "base", "head")]
    if not all(isinstance(v, str) and v.strip() for v in vals):
        raise ValueError("%s must be a JSON object with non-empty string title, base and head" % PREPARED_ARGS)
    return (*vals, PREPARED_BODY)


def ledger_summary(cfg, number):
    """Counts from the working-tree ledger, as lines of Markdown: no ledger text is copied, so nothing the issue's author wrote reaches the PR."""
    path = dod.dod_file(dod.safe_dir(cfg.dod_path), number)
    shown = comments._MENTION.sub(comments.NO_MENTION, comments._show(path, 200))
    md = gate._read(path, ".", set())
    if md is None:
        return "**Ledger:** no readable ledger %s for #%d." % (shown, number)
    visible, still_open = _visible(md)
    if still_open:
        return "**Ledger:** %s has an unclosed code fence or comment; not summarised." % shown
    try:
        items = re.findall(r"^- \[( |x|X)\] ", dod.section(visible, 5) or "", re.M)
        n_ac = len(dod.ac_lines(md))
    except dod.LedgerError as e:
        return "**Ledger:** %s has more than one `## 2.` heading (%s); not summarised." % (shown, e.code)
    return "**Ledger summary** (%s): %d acceptance criteria; DoD checklist %d of %d ticked." % (
        shown, n_ac, sum(i != " " for i in items), len(items))


def compose(title, text, numbers, summary):
    """(the title sent, the body sent, how many @mentions were removed). The title has `#n` for every issue; the body is the caller's text,
    the summary and the `Closes` line. Mentions in the caller's text and title are defanged (nobody is notified by a PR the gate approved);
    the summary and the keyword line carry only numbers and our own fixed words. ValueError: a title that is not one printable line of at
    most 256 characters, a body over MAX_BODY."""
    cleaned = comments._MENTION.subn(comments.NO_MENTION, title)
    sent_title = cleaned[0]
    for n in numbers:
        sent_title = with_ref(sent_title, n)
    issues._plain(sent_title, "the title", issues.MAX_TITLE)
    cleaned_text = comments._MENTION.subn(comments.NO_MENTION, text)
    body = "\n\n".join(p for p in (cleaned_text[0].rstrip(), summary, closes_line(numbers)) if p) + "\n"
    if len(body) > MAX_BODY:
        raise PrError("E_BODY_TOO_LONG", "the PR body would be %d characters, over the limit of %s; nothing was sent: shorten it" % (len(body), format(MAX_BODY, ",")))
    return sent_title, body, cleaned[1] + cleaned_text[1]


def task_status(gh, cfg, ref):
    """(unticked task ids or None, plan row). A missing tasks.md or spec dir never blocks: it is only noted."""
    try:
        parsed = tasks.parse_tasks(push_verb._tasks_md(cfg, ref, None))
    except (ValueError, OSError):
        return None, ("tasks", "not checked (no tasks.md)")
    if not parsed:
        # An emptied tasks.md must not read as "nothing to check" while the story still has task sub-issues (ado R3 N3).
        try:
            marked = [push_verb._TASK_RE.findall(k.body) for k in issues.children(gh, ref) if not (k.state == "closed" and k.state_reason == "not_planned")]
            tracked = [ids[0] for ids in marked if ids]
        except (GhError, ValueError):
            tracked = []
        if tracked:
            return tracked, ("draft", "yes — tasks.md lists no tasks but %d task sub-issue(s) are tracked (%s); was it emptied?" % (len(tracked), _ids(tracked)))
        return None, ("tasks", "not checked (tasks.md lists no tasks)")
    open_ids = [t["id"] for t in parsed if not t["done"]]
    if not open_ids:
        return [], ("tasks", "all %d ticked" % len(parsed))
    return open_ids, ("draft", "yes — %d unticked task(s): %s" % (len(open_ids), _ids(open_ids)))


def _ids(ids):
    shown = ", ".join(comments._show(i, 20) for i in ids[:MAX_LISTED])
    return shown + (" and %d more" % (len(ids) - MAX_LISTED) if len(ids) > MAX_LISTED else "")


# ---- the plan --------------------------------------------------------------------------------------------------------

def _body_view(body):
    """The body for a reader: every unprintable or invisible character as a \\u escape, line breaks kept. The digest covers the exact text."""
    return "\n".join(comments._show(l) for l in body.split("\n"))


def _digest(title, body, base, head, head_sha, draft, numbers, tasks_row, warnings, existing, push, allow_extra=False):
    data = [title, body, base, head, head_sha, draft, list(numbers), list(tasks_row or ()), list(warnings), list(existing or ()), push, allow_extra]
    return hashlib.sha256(json.dumps(data, ensure_ascii=True).encode("ascii")).hexdigest()


def render_plan(title, body, base, head, numbers, tasks_row=None, head_sha=None, draft=False, warnings=(), existing=None, push=False, removed=0, allow_extra=False):
    """`head_sha`: the full commit id the head branch is at, when it is a local branch. Shown so a commit added after the approval changes the
    plan: `--apply --expect-plan` then refuses. `existing`: (number, url) of the open PR that already has this head and base: nothing is created
    then. The table and the body are DISPLAYS (escapes for what cannot be shown); the `digest:` line covers the exact data, so no two
    different PRs make the same plan (`allow_extra`, the --allow-extra-closes flag, is part of it). The body comes last: the gate shows this file."""
    preview = " ".join(body.split())  # escaped once, with the whole value, by _cell below
    if len(preview) > 120:
        preview = preview[:120] + "..."
    rows = [("title", title), ("base", base), ("head", head)]
    if head_sha:
        if not re.fullmatch(r"[0-9a-f]{40}", head_sha):
            raise ValueError("the head commit is not a full commit id")
        rows.append(("head commit", head_sha))
    rows.append(("issues", ", ".join("#%d" % n for n in numbers)))
    if tasks_row:
        rows.append(tasks_row)
    if push:
        rows.append(("push", "yes: git push -u origin %s, before the PR is created" % head))
    rows.append(("existing PR", "none" if not existing else "#%d is already open for this head and base; nothing will be created" % existing[0]))
    rows.append(("body", "%d chars: %s" % (len(body), preview)))
    lines = ["Plan for pull request", "", "| field | value |", "|---|---|"]
    lines += ["| %s | %s |" % (k, _cell(str(v))) for k, v in rows]
    lines += [""] + ["⚠ " + comments._show(w, 400) for w in warnings]
    if removed:
        lines.append("%d @mention(s) were removed from the title and body (shown as %s)." % (removed, comments.NO_MENTION))
    lines += ["digest: " + _digest(title, body, base, head, head_sha, draft, numbers, tasks_row, warnings, existing, push, allow_extra), "",
              "## PR body (as it will be sent; invisible and control characters are shown as \\u escapes)", ""]
    return "\n".join(lines) + "\n" + _body_view(body)


# ---- GitHub: reads, the create, the re-read ----------------------------------------------------------------------------

_PR_URL = re.compile(r"https://github\.com/([^/\s]+)/([^/\s]+)/pull/([1-9][0-9]*)")


def _bad(what):
    return GhError("E_GH_BAD_BODY", None, "unexpected pull request response: " + what)


def _pr(raw, cfg):
    """One PR from a REST reply: {number, url, draft, title, body, head, base, state}. GhError E_GH_BAD_BODY on any other shape."""
    if not isinstance(raw, dict):
        raise _bad("not an object")
    n, url = raw.get("number"), raw.get("html_url")
    head, base = raw.get("head"), raw.get("base")
    m = _PR_URL.fullmatch(url) if isinstance(url, str) else None
    if (type(n) is not int or n < 1 or not m or int(m.group(3)) != n
            or (issues._fold(m.group(1)), issues._fold(m.group(2))) != (issues._fold(cfg.owner), issues._fold(cfg.repo))):
        raise _bad("no number, or an address that is not this repository's pull request")
    if not (isinstance(head, dict) and isinstance(head.get("ref"), str) and isinstance(base, dict) and isinstance(base.get("ref"), str)):
        raise _bad("no head or base branch")
    repo = head.get("repo")
    full = repo.get("full_name") if isinstance(repo, dict) else None
    return {"number": n, "url": url, "draft": raw.get("draft") is True, "title": raw.get("title"), "body": raw.get("body"), "head": head["ref"], "base": base["ref"], "state": raw.get("state"),
            "same_repo": isinstance(full, str) and issues._fold(full) == issues._fold("%s/%s" % (cfg.owner, cfg.repo))}


def default_branch(gh, cfg):
    d = gh.rest("GET", "repos/%s/%s" % (cfg.owner, cfg.repo))
    name = d.get("default_branch") if isinstance(d, dict) else None
    if not isinstance(name, str) or not name:
        raise GhError("E_GH_BAD_BODY", None, "the repository has no default_branch in the reply")
    return name


def find_open(gh, cfg, head, base):
    """The open PR from `head` into `base` in this repository: a dict, or None. More than one is refused (GitHub allows one)."""
    q = "?head=%s&base=%s&state=open&per_page=100" % (urllib.parse.quote("%s:%s" % (cfg.owner, head), safe=":/"), urllib.parse.quote(base, safe="/"))
    found = gh.rest("GET", PULLS % (cfg.owner, cfg.repo) + q)
    if not isinstance(found, list):
        raise _bad("the pull request list is not a list")
    mine = [p for p in map(lambda raw: _pr(raw, cfg), found) if p["head"] == head and p["base"] == base and p["same_repo"] and p["state"] == "open"]
    if len(mine) > 1:
        raise PrError("E_PR_AMBIGUOUS", "pull requests %s are all open for this head and base; not choosing one" % ", ".join("#%d" % p["number"] for p in mine))
    return mine[0] if mine else None


def _find_waiting(gh, cfg, head, base, sleep):
    """`find_open` with a bounded wait (as `issues._find_waiting`): the list may lag a fresh PR. A failing read ends it at once."""
    for wait in (0,) + issues.REREAD_WAITS:
        if wait:
            sleep(wait)
        made = find_open(gh, cfg, head, base)
        if made is not None:
            return made
    return None


def _same_text(a, b):
    """Two bodies equal but for line ends (CRLF, CR, LF), blanks at the end of a line and at the end of the text: what GitHub may change."""
    flat = lambda t: "\n".join(l.rstrip() for l in t.replace("\r\n", "\n").replace("\r", "\n").split("\n")).rstrip("\n")  # noqa: E731
    return flat(a) == flat(b)


def create_pr(gh, cfg, title, body, base, head, draft, sleep=None):
    """Create the PR once; (the PR dict, "created" | "found" | "different"). A failure that may have been applied re-reads the PR list; a PR found
    is used (nothing is sent again) when its title, body and draft flag are what was planned, else the answer is "different" (someone else's PR, or
    one edited since: the caller reports it, never claims success); none found raises the original error with a note. A plain rejection (4xx)
    is raised as it is."""
    check_branch("base", base)
    check_branch("head", head)
    path = PULLS % (cfg.owner, cfg.repo)
    try:
        try:
            made = _pr(gh.rest("POST", path, {"title": title, "head": head, "base": base, "body": body, "draft": bool(draft)}), cfg)
        except GhError as e:
            if e.code != "E_GH_BAD_BODY" or UNKNOWN_OUTCOME in e.detail:
                raise
            raise GhError(e.code, e.status, "%s: %s" % (UNKNOWN_OUTCOME, e.detail)) from None
        if made["head"] != head or made["base"] != base:
            raise GhError("E_GH_BAD_BODY", None, "%s: the reply is another pull request than the one asked for" % UNKNOWN_OUTCOME)
        return made, "created"
    except GhError as e:
        if not issues.maybe_applied(e):
            raise
        found = issues.reread(e, lambda: _find_waiting(gh, cfg, head, base, sleep or issues._nap))
        if found is None:
            raise GhError(e.code, e.status, "outcome unknown and no open pull request from %s was found on re-read; nothing was retried: look at "
                          "the repository's pull requests before running again; %s" % (comments._show(head, 80), e.detail)) from None
        same = (found["title"] == title and isinstance(found["body"], str) and _same_text(found["body"], body)
                and found["draft"] == bool(draft))
        return found, "found" if same else "different"


# ---- the verb --------------------------------------------------------------------------------------------------------------

def register(sub):
    p = sub.add_parser("pr", help="open a pull request that closes an issue (dry run unless --apply)")
    psub = p.add_subparsers(dest="pr_command", metavar="ACTION", required=True)
    q = psub.add_parser("create", help="create a PR with `#N` in the title and `Closes #N` in the body (dry run unless --apply)",
                        description="Create a PR with `#N` in the title and `Closes #N` in the body (dry run unless --apply). The title, the body and the commit "
                                    "messages between base and head are checked for closing keywords that name other issues. What is scanned is what is "
                                    "on this computer now: the local head branch (without --push, a remote head that is ahead is not scanned), the remote-"
                                    "tracking origin/<base> (the local base when there is none). Commits pushed later, and a merge or squash message "
                                    "edited by hand when merging, are not scanned.")
    q.add_argument("refs", nargs="+", metavar="REF", help="the issue(s) the PR closes: 5, #5, owner/repo#5 or an issue URL; the first is the story "
                                                      "(its ledger is summarised, its tasks.md checked)")
    q.add_argument("--title")
    q.add_argument("--body-file", metavar="F")
    q.add_argument("--base")
    q.add_argument("--head")
    q.add_argument("--from-prepared", action="store_true", help="read title/base/head from %s and the body from %s" % (PREPARED_ARGS, PREPARED_BODY))
    q.add_argument("--push", action="store_true", help="with --from-prepared --apply: `git push -u origin <head>` before creating the PR")
    q.add_argument("--allow-incomplete", action="store_true", help="open the PR as a draft when tasks.md has unticked tasks (default: refuse)")
    q.add_argument("--allow-extra-closes", action="store_true", help="allow a closing keyword in the title, the body or a commit message of base..head that "
                                                                    "names an issue other than the ones given (default: refuse; the plan lists them and binds the flag)")
    write_gate.add_write_flags(q)


def _inputs(args):
    """(title, base, head, body text) from the flags or the prepared files; the branch names are checked."""
    if args.from_prepared:
        if any((args.title, args.body_file, args.base, args.head)):
            raise ValueError("--from-prepared cannot be combined with --title/--body-file/--base/--head")
        args.title, args.base, args.head, args.body_file = load_prepared()
    else:
        if args.push:
            raise ValueError("--push needs --from-prepared")
        if not all((args.title, args.body_file, args.base, args.head)):
            raise ValueError("--title, --body-file, --base and --head are required (or use --from-prepared)")
    check_branch("base", args.base)
    check_branch("head", args.head)
    return args.title, args.base, args.head, _read(args.body_file, "the body file")


def _targets(found):
    return ", ".join(comments._show(t, 150) for t in found[:MAX_SHOWN]) + (" and %d more" % (len(found) - MAX_SHOWN) if len(found) > MAX_SHOWN else "")


def _head_commit(head):
    """The full commit id of the local branch `head`, or "" when it is not a local branch (the plan then names no head commit)."""
    return _rev(".", "refs/heads/%s" % head)


def _strict_pr(gh, cfg, numbers, head, base, push):
    """Strict `pr create`: `strict.check(..., "pr")` for EVERY issue the PR closes (each gets `Closes #n`, so each must pass; fail closed if any fails).
    Returns `{number: overrides used}`. A refusal prints the findings as JSON first (with several issues each detail is prefixed `#n: `) and raises Refused."""
    from . import strict  # lazy: strict imports this module
    if not strict.head_is_checkout(".", head):
        raise ValueError("strict mode judges the working-tree ledger and decisions file: check out the --head branch first")
    used, findings = {}, []
    for n in numbers:
        res = strict.check(gh, cfg, n, "pr", head=head, base=strict.base_ref(".", base), push=push)
        if res["ok"]:
            used[n] = res["overrides_used"]
        else:
            findings += [{**f, "detail": "#%d: %s" % (n, f["detail"])} if len(numbers) > 1 else f for f in res["findings"]]
    if findings:
        refused = strict.Refused({"phase": "pr", "mode": "strict", "ok": False, "findings": findings, "overrides_used": []})
        print(json.dumps(refused.check, indent=2))  # the findings, as `strict check` prints them
        raise refused
    return used


def run(args, gh):
    refs = [issues._resolve(t) for t in args.refs]  # config, then the refs: a bad one is refused before any call
    cfg = Config.load()
    numbers = [r.number for r in refs]
    if len(set(numbers)) != len(numbers) or len(numbers) > MAX_ISSUES:
        raise ValueError("name each issue once, at most %d" % MAX_ISSUES)
    title, base, head, text = _inputs(args)
    used = {}
    strict_mode = cfg.mode == "strict"
    if strict_mode:  # first: any finding refuses before a plan file, a push or a request (the checks read GitHub with GETs only)
        used = _strict_pr(gh, cfg, numbers, head, base, args.push)
    for n in (numbers if strict_mode else numbers[:1]):  # strict judges EVERY issue the PR closes; advisory keeps the first (the story)
        if why := review_blocks(n, cfg.effective_review_block_on(), head, cfg.dod_path, push=args.push):
            raise ValueError("refusing to open a PR: " + ("#%d: " % n if len(numbers) > 1 else "") + comments._show(why, 400))
    sent_title, body, removed = compose(title, text, numbers, ledger_summary(cfg, numbers[0]))  # local: what is SENT is what is scanned
    extra = other_closes([sent_title, without_generated(body, numbers)], cfg.owner, cfg.repo, numbers)
    if extra and not args.allow_extra_closes:
        raise PrError("E_BODY_CLOSES_OTHERS", "the title or body would also close %s on merge (a closing keyword is followed by it); remove it, or pass "
                      "--allow-extra-closes to accept it; nothing was sent" % _targets(extra))
    extra_commits = other_closes(commit_messages(base, head), cfg.owner, cfg.repo, numbers)  # local; the same scanner as the body
    if extra_commits and not args.allow_extra_closes:
        raise PrError("E_COMMITS_CLOSE_OTHERS", "a commit message between base and head would also close %s on merge (a closing keyword is followed by it); "
                      "reword the commit, or pass --allow-extra-closes to accept it; nothing was sent" % _targets(extra_commits))
    stories = [issues.get(gh, r) for r in refs]  # a pull request number is NotAnIssue (a ValueError)
    open_ids, tasks_row = task_status(gh, cfg, refs[0])
    if open_ids and not args.allow_incomplete:
        raise ValueError("refusing to open a PR: %d unticked task(s) in tasks.md: %s. Finish them, or pass --allow-incomplete to open it as a draft" % (
            len(open_ids), _ids(open_ids)))
    draft = bool(open_ids)
    default = default_branch(gh, cfg)
    existing = find_open(gh, cfg, head, base)
    warnings = []
    if base != default:
        warnings.append("base '%s' is not the default branch '%s': Closes keywords are ignored; `ghp done` will close it" % (
            comments._show(base, 80), comments._show(default, 80)))
    warnings += ["#%d is already closed" % s.ref.number for s in stories if s.state == "closed"]
    tied = issue_from_branch(head, cfg.branch_prefix)
    if tied is not None and tied not in numbers:
        warnings.append("the head branch is tied to #%d, which this PR does not close" % tied)
    if extra:
        warnings.append("the title or body would also close %s on merge (accepted with --allow-extra-closes)" % _targets(extra))
    if extra_commits:
        warnings.append("commit messages would also close %s on merge (accepted with --allow-extra-closes)" % _targets(extra_commits))
    head_sha = _head_commit(head) or None  # the plan binds a commit added after the approval, in every mode
    plan = render_plan(sent_title, body, base, head, numbers, tasks_row, head_sha, draft, warnings, existing and (existing["number"], existing["url"]),
                       args.push, removed, args.allow_extra_closes)
    write_gate.arm_or_refuse(args, gh, plan, approvals.approve_hook(gh, refs[0], "pr-create", plan, not existing, base=base))
    print(plan)
    if not args.apply:
        return 0
    if existing:
        print("nothing sent: PR #%d is already open for this head and base" % existing["number"])
        return 0
    for n, ids in used.items():  # strict: stamp `used` on the overrides each check relied on; a failed write is a refusal with nothing sent
        if ids:
            decisions.apply_stamp(decisions.decisions_file(cfg.dod_path, n), n, ids)
    if args.push and (pushed := push_branch(head)).get("error"):
        print("git push failed: " + pushed["error"])
        return 1
    made, how = create_pr(gh, cfg, sent_title, body, base, head, draft)
    if how == "different":
        print("outcome unknown, and PR #%d (%s) is open for this head and base but its title, body or draft flag are not the approved ones; "
              "nothing was sent again: look at it before doing anything else" % (made["number"], comments._show(made["url"], 200)))
        return 1
    print(("created PR #%d: %s" if how == "created" else "found PR #%d (opened before the error; nothing sent again): %s") % (
        made["number"], comments._show(made["url"], 200)))
    return 0
