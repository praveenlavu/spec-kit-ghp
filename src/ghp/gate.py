"""The DoD gate (ado's gate.py, ported): AC <-> test traceability, assertions, AC drift, evidence, open questions and decisions, commit
references, business rules. `run_gate` and `render_summary` are pure; `evaluate` does the reads and `run` is the `ghp gate` verb.

What changed from ado:
- The issue is read with `issues.get` (GET only: the gate sends no write, `test_gate_sends_no_write`); a GitHub error or an answer that is not an issue
  means "drift not checked", never a block (missing information does not block). An issue whose AC heading is missing or doubled is AC drift.
- The commit check looks for `#<n>` (`prs._TEXT_REF`: not `AB#`, not `owner/repo#n`) in `<base>..HEAD` messages and ignores merge commits.
- No token is read here (`ghp` never reads one): `render_error` redacts token-shaped text with `gh.scrub` instead of an env value.
- The verb lives here (ado had it in `__main__`): `register`, `run`, `evaluate`. Exit codes: 0 no blocking finding; 1 a blocking finding or a
  GitHub error; 2 refused (bad usage, a missing config, a ValueError); 3 an invalid config OR an ambiguous ledger (`dod.LedgerError`) (CI fails closed: a workflow must treat 3 as a failure in every mode, not as the advisory 2).

`GATE_CHECKS` and `valid_glob` are defined here and imported by `config` (lazily: config -> gate -> decisions -> ... -> config). `MAX_READ`, `_read`
and `_AC_RE` are the T10/T12 seam code, unchanged."""
import ast
import errno
import functools
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

from . import comments, dod, issues
from .config import Config, ConfigError, ConfigInvalid, _resolve_base
from .dod import _visible, ac_hash, ac_lines, ledger_ac_hash, ledger_rules_from, ledger_rules_reviewed, section, unclosed
from .gh import GhError, scrub
from .refs import Ref, RefError, parse_ref

_PY = r"\bassert\b|pytest\.raises|self\.assert"
_JS = r"\bexpect\(|\bassert\b"
_JVM = r"\bassert|\bverify\("
_CS = r"\bAssert\.|Should\("
_GO = r"\bt\.(Error|Fatal)|\bassert\.|\brequire\."
_APEX = r"System\.assert|\bAssert\."
DEFAULT_TESTS = {
    "**/test_*.py": _PY, "**/*_test.py": _PY,
    "**/*.test.[jt]s": _JS, "**/*.spec.[jt]s": _JS, "**/*.test.[jt]sx": _JS,
    "**/*Test.java": _JVM, "**/*Test.kt": _JVM,
    "**/*Tests.cs": _CS, "**/*Test.cs": _CS,
    "**/*_test.go": _GO,
    "**/*Test.cls": _APEX, "**/*_Test.cls": _APEX,
}
_AC_RE = re.compile(r"^AC-\d+$")


def _cell(cells, i):
    c = cells[i] if len(cells) > i else ""
    return "" if "_(" in c else c  # untouched template placeholder -> empty


def parse_section3(md):
    """Section 3 rows tagged AC-<n> -> [{'ac','tests','evidence','status'}]. Graceful: missing/placeholder -> empty."""
    rows = []
    for line in _visible(section(md, 3) or "")[0].splitlines():  # a table in a fence or comment is an example
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not _AC_RE.match(cells[0]):
            continue
        rows.append({"ac": cells[0],
                     "tests": [t.strip(" `") for t in _cell(cells, 2).split(",") if t.strip(" `")],  # `id` is Markdown code, as the template shows it
                     "evidence": _cell(cells, 3), "status": _cell(cells, 4)})
    return rows


MAX_READ = 1 << 20  # bytes; a larger test file is skipped, not read
SKIPPED_NOTE = "{} file(s) skipped: symlink, outside --src, not a regular file, or over 1 MiB"


def _read(path, root, skipped):
    """Text of a small regular file under `root`, else None (also used for the gate's ledger). The PR controls the tree and gate.tests, so a read is
    a content oracle: a symlink (even one pointing inside root), a path whose real path leaves root, a FIFO or
    device, or a file over MAX_READ is never read; it is counted into the `skipped` set, never named.
    Unreadable or non-UTF-8 files return None uncounted. shortcut: only the final component is O_NOFOLLOW; the
    realpath re-check after open narrows, but cannot close, a swap of a parent directory by a concurrent writer
    (the checkout is static while the gate runs); upgrade to dir-fd openat if the gate ever scans a live tree."""
    try:
        real, top = os.path.realpath(path), os.path.realpath(root)  # resolve and confine BEFORE open: nothing outside is ever opened,
        if not os.path.islink(path) and os.path.commonpath([real, top]) == top:  # and a missing target there counts like an existing one
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)  # NONBLOCK: a FIFO must not hang open()
            with os.fdopen(os.open(path, flags), "rb") as f:
                st = os.fstat(f.fileno())
                if (os.path.realpath(path) == real and stat.S_ISREG(st.st_mode)
                        and st.st_size <= MAX_READ and os.path.samestat(st, os.stat(real))):
                    data = f.read(MAX_READ + 1)
                    if len(data) <= MAX_READ:
                        return data.decode("utf-8")
        skipped.add(os.path.abspath(path))  # a set (deduped); only its size is ever reported
    except OSError as e:
        if e.errno == errno.ELOOP:  # O_NOFOLLOW met a symlink (swapped in after the islink check)
            skipped.add(os.path.abspath(path))
    except (UnicodeDecodeError, ValueError):  # ValueError: commonpath across drives (Windows)
        pass
    return None


_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".tox", "dist", "build", ".specify"}


MAX_WILDCARDS = 8  # per gate.tests glob (valid_glob); the matcher below does not need it (it is linear), it is defence in depth
E_GLOB = "invalid glob in gate.tests"  # the pattern is config text: never echoed


@functools.lru_cache(maxsize=512)
def _compile_glob(pattern):
    """The glob as an NFA `(moves, eps, last, wildcards, tail)`: state `i` has `moves[i]` = [(char predicate, next state)] and `eps[i]` = [next states without
    reading a char]; state 0 starts and the last state accepts. Same language as ado's regex translation (`**/` = zero or more dirs, other `**` =
    anything, `*` = no `/`, `?` = one char but `/`, `[...]` / `[!...]` a class), right-anchored like PurePath.match (leading dirs tolerated). ValueError
    for an unbalanced or ill-formed class. A path is matched by `_glob_match` by advancing the set of states one character at a time: no
    backtracking, O(len(path) x states) whatever the pattern holds."""
    moves, eps = [[]], [[]]

    def new():
        moves.append([])
        eps.append([])
        return len(moves) - 1

    def dirs(cur):  # `(?:.*/)?`: optional, any chars then a slash (the loop state is apart from `cur`, so the slash is the only way out of it)
        loop, nxt = new(), new()
        eps[cur] += [nxt, loop]
        moves[loop] += [(_not_nl, loop), (_is_slash, nxt)]
        return nxt

    cur, i, wild, tail = dirs(0), 0, 0, ""  # tail: the literal run after the last wildcard (every match ends with it: a cheap reject)
    while i < len(pattern):
        if pattern.startswith("**/", i):
            cur, i, wild, tail = dirs(cur), i + 3, wild + 1, ""
        elif pattern.startswith("**", i):
            nxt = new()
            eps[cur].append(nxt)
            moves[cur].append((_not_nl, cur))
            cur, i, wild, tail = nxt, i + 2, wild + 1, ""
        elif pattern[i] == "*":
            nxt = new()
            eps[cur].append(nxt)
            moves[cur].append((_not_slash, cur))
            cur, i, wild, tail = nxt, i + 1, wild + 1, ""
        elif pattern[i] == "?":
            nxt = new()
            moves[cur].append((_not_slash, nxt))
            cur, i, wild, tail = nxt, i + 1, wild + 1, ""
        elif pattern[i] == "[":
            j = pattern.find("]", i + 2)
            if j == -1:
                raise ValueError(E_GLOB)  # unbalanced class
            cls = pattern[i + 1:j]
            try:
                rx = re.compile("[" + ("^" + cls[1:] if cls[0] == "!" else cls) + "]")
            except re.error:
                raise ValueError(E_GLOB) from None
            nxt = new()
            moves[cur].append((lambda c, rx=rx: rx.fullmatch(c) is not None, nxt))
            cur, i, wild, tail = nxt, j + 1, wild + 1, ""
        else:
            nxt, ch = new(), pattern[i]
            moves[cur].append((lambda c, ch=ch: c == ch, nxt))
            cur, i, tail = nxt, i + 1, tail + ch
    return tuple(map(tuple, moves)), tuple(map(tuple, eps)), cur, wild, tail


def _not_nl(c):
    return c != "\n"  # `.` of the regex ado used


def _is_slash(c):
    return c == "/"


def _not_slash(c):
    return c != "/"  # `[^/]`


def _close(states, eps):
    todo, out = list(states), set(states)
    while todo:
        for t in eps[todo.pop()]:
            if t not in out:
                out.add(t)
                todo.append(t)
    return out


def _glob_match(path, pattern):
    """`path` (a POSIX path; `\\` counts as `/`) against `pattern` (see `_compile_glob`). Linear in len(path) x len(pattern): no pattern, however many
    wildcards it holds, makes it backtrack. ValueError (a fixed text) for a malformed glob."""
    moves, eps, last, _, tail = _compile_glob(pattern)
    path = path.replace("\\", "/")
    if not path.endswith(tail):
        return False
    states = _close({0}, eps)
    for c in path:
        nxt = {t for st in states for ok, t in moves[st] if ok(c)}
        if not nxt:
            return False
        states = _close(nxt, eps)
    return last in states


def valid_glob(pattern):
    """A gate.tests key the matcher accepts: 1-200 chars, no NUL/newline, classes balanced and well-formed, at most MAX_WILDCARDS wildcards (`**/`,
    `**`, `*`, `?` or a class each count one)."""
    if not pattern or len(pattern) > 200 or "\0" in pattern or "\n" in pattern:
        return False
    try:
        return _compile_glob(pattern)[3] <= MAX_WILDCARDS
    except ValueError:
        return False


def discover_tests(src, tests, skipped=None):
    """{path: text} of files under src matching any test glob; vendored dirs pruned, unreadable files skipped,
    symlinks / non-regular / oversize files skipped and counted into `skipped` (see _read)."""
    skipped = set() if skipped is None else skipped
    found = {}
    for g in tests:
        _glob_match("", g)  # fail fast on a malformed glob, even when no files exist
    for root, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            p = Path(root, name)
            full = Path(os.path.abspath(p)).as_posix()
            if any(_glob_match(full, g) for g in tests) and (t := _read(p, src, skipped)) is not None:
                found[str(p)] = t
    return found


def _path_match(fname, path):
    f = fname.replace("\\", "/")
    return f == path or f.endswith("/" + path)


def test_present(test_id, sources):
    """Every segment of the ID must occur in the same file. `path::rest` pins the file by path suffix."""
    path, sep, rest = test_id.partition("::")
    if not sep:
        path, rest = "", test_id
    segs = [s.strip() for part in re.split(r"::| > ", rest) for s in part.split(".") if s.strip()]
    if not segs:
        return False  # nothing to look for (empty id, or `path::` with no name)
    path = path.replace("\\", "/").removeprefix("./")
    if sep and not path:
        return False  # `./::name` names no file
    # whole identifiers only: `test_add` must not be found in `test_add_many`
    rxs = [re.compile(rf"(?<![A-Za-z0-9_]){re.escape(s)}(?![A-Za-z0-9_])") for s in segs]
    return any(
        (not path or _path_match(fname, path)) and all(rx.search(text if path else fname + "\n" + text) for rx in rxs)
        for fname, text in sources.items()
    )


test_present.__test__ = False  # not a pytest test, despite the name


def check_traceability(rows, sources, ac_count=0):
    """ac_count = AC lines in ledger section 1; AC-1..AC-n with no section 3 row are unmapped."""
    have = {r["ac"] for r in rows}
    absent = [f"AC-{n}" for n in range(1, ac_count + 1) if f"AC-{n}" not in have]
    if not rows and not ac_count:
        return {"evaluated": False, "mapped": [], "unmapped": [],
                "note": "no AC-tagged rows in section 3 — insufficient to evaluate traceability"}
    mapped, unmapped = [], []
    for r in rows:
        (mapped if any(test_present(t, sources) for t in r["tests"]) else unmapped).append(r["ac"])
    unmapped += absent
    note = "all AC mapped" if not unmapped else f"{len(unmapped)} AC without a known test"
    if absent:
        note += f" ({', '.join(absent)}: no section 3 row)"
    return {"evaluated": True, "mapped": mapped, "unmapped": unmapped, "note": note}


def scan_assertions(changed, tests):
    """Changed test files (by glob) with no assertion match. File-level, not method-level."""
    gaps = []
    for fname, text in changed.items():
        regexes = [rx for g, rx in tests.items() if _glob_match(fname, g)]
        if regexes and not any(re.search(rx, text) for rx in regexes):
            gaps.append({"file": fname, "issue": "no assertions"})
    return gaps


_ASSERT_CALLS = {"raises", "warns", "fail", "deprecated_call"}


def _callee(call):
    f = call.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""


def _has_assertion(fn, helpers=frozenset()):
    """A def whose body (nested statements included) asserts: `assert`, `raise AssertionError`, a call to
    `assert*`/raises/warns/fail (bare or attribute, any import alias of the module), or to a same-module helper that does."""
    for n in ast.walk(fn):
        if isinstance(n, (ast.Assert, ast.Raise)) and (isinstance(n, ast.Assert) or _exc_name(n.exc) == "AssertionError"):
            return True
        if isinstance(n, ast.Call) and (c := _callee(n)) and (
                c.startswith("assert") or c in _ASSERT_CALLS or c in helpers):
            return True
    return False


def _exc_name(e):
    e = e.func if isinstance(e, ast.Call) else e
    return e.id if isinstance(e, ast.Name) else ""


def _py_index(tree):
    """`{name: [(class name or None, def)]}` for the functions at module level or in a class body, in source order: built ONCE per file (ado walked the
    tree again for every test id)."""
    scopes = [(None, tree)] + [(n.name, n) for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    out = {}
    for c, sc in scopes:
        for f in sc.body:
            if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.setdefault(f.name, []).append((c, f))
    return out


def check_test_assertions(rows, sources):
    """Per mapped test: Python tests are parsed with ast and flagged when their own body asserts nothing.
    Returns (gaps, notes); other languages stay file-level (scan_assertions) and get one note."""
    gaps, notes, trees, other = [], [], {}, False
    for tid in dict.fromkeys(t for r in rows for t in r["tests"]):
        path, sep, rest = tid.partition("::")
        if not sep:
            path, rest = "", tid
        segs = [s.strip() for part in re.split(r"::| > ", rest) for s in part.split(".") if s.strip()]
        if not segs:
            continue
        path = path.replace("\\", "/").removeprefix("./")
        name, cls = segs[-1], (segs[-2] if len(segs) > 1 else None)
        verdicts, unparsed = [], False
        for f, text in sources.items():
            if not f.endswith(".py") or (path and not _path_match(f, path)) or name not in text:
                continue
            if f not in trees:  # parsed, indexed and its helpers found once per file, not once per (test id, file)
                try:
                    tree = ast.parse(text)
                    trees[f] = (_py_index(tree), {n.name for n in ast.walk(tree)  # helpers, one level: defs that assert directly
                                                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _has_assertion(n)})
                except (SyntaxError, ValueError, RecursionError, MemoryError):
                    trees[f] = None
                    notes.append(f"could not parse {f}; per-test assertion check skipped for it")
            if trees[f] is None:
                unparsed = True
                continue
            index, helpers = trees[f]
            verdicts += [_has_assertion(d, helpers) for c, d in index.get(name, ()) if cls is None or c == cls]
        if verdicts and not any(verdicts):
            gaps.append({"test": tid, "issue": "no assertions"})
        elif not verdicts and not unparsed:
            other = other or test_present(tid, {f: t for f, t in sources.items() if not f.endswith(".py")})
    if other:
        notes.append("per-test assertion checking covers Python only; other languages are checked per file")
    return gaps, notes


UNREACHABLE = "GitHub not reachable or gh not signed in — drift not checked"


def check_drift(ledger_md, issue, reason=UNREACHABLE):
    """`issue`: the live `issues.Issue`, or None when it was not read. `reason`: the note when there is none (offline / GitHub unreachable).
    An issue whose AC heading is missing or doubled (`AcError`) has no readable AC now: that is drift, never a pass."""
    recorded = ledger_ac_hash(ledger_md)
    if recorded is None:
        return {"evaluated": False, "drift": False, "note": "no AC hash in the ledger — drift not checked"}
    if issue is None:
        return {"evaluated": False, "drift": False, "note": reason}
    try:
        return {"evaluated": True, "drift": ac_hash(issue) != recorded}
    except dod.AcError:
        return {"evaluated": True, "drift": True}


def missing_evidence(rows):
    return [r["ac"] for r in rows if "green" in r["status"].lower() and not r["evidence"]]


COMMITS_NOT_CHECKED = "commits not checked"


def check_commits(repo, number, base=None):
    """(short shas in <base>..HEAD whose message lacks `#<number>`, note). Merge commits are ignored.
    `base` is a ref (CI passes origin/<target>); default is the origin/HEAD branch. A base that cannot be resolved
    (or starts with `-`) checks nothing and says so in the note: missing information never blocks."""
    from . import prs  # lazy: prs imports this module

    def git(*args):  # stdout; None for "not found" (exit 1, no stderr); OSError for a real failure
        r = subprocess.run(["git", "--no-pager", "--no-replace-objects", "-C", repo, *args], capture_output=True, timeout=30,
                           check=False, stdin=subprocess.DEVNULL, env=prs.git_env())
        err = r.stderr.decode("utf-8", "replace").strip()
        if r.returncode == 1 and not err:
            return None
        if r.returncode:
            raise OSError(err[:200])
        return r.stdout.decode("utf-8", "replace").strip()

    try:
        if base is None:
            ref = git("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
            sha = ref and git("rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}")
            label = "no default branch (origin/HEAD)"
        elif base.startswith("-"):
            return [], f"base {comments._show(base, 100)!r} is not a ref — {COMMITS_NOT_CHECKED}"
        else:
            sha = git("rev-parse", "--verify", "--quiet", "--end-of-options", f"{base}^{{commit}}")
            label = f"base {comments._show(base, 100)} not found"
        if not sha:
            return [], f"{label} — {COMMITS_NOT_CHECKED}"
        log = git("log", "--no-merges", "-z", "--format=%h%x1f%B", f"{sha}..HEAD")
    except (OSError, subprocess.SubprocessError) as e:
        return [], f"git failed ({comments._show(' '.join(str(e).split())[:100] or type(e).__name__)}) — {COMMITS_NOT_CHECKED}"
    out = []
    for rec in filter(None, (log or "").split("\0")):
        sha, _, msg = rec.partition("\x1f")
        if not any(int(m.group(1)) == number for m in prs._TEXT_REF.finditer(msg)):
            out.append(sha.strip())
    return out, None


LEDGER_UNCLOSED_NOTE = "the ledger has an unclosed code fence or comment — sections after it are not evaluated"

# Every check run_gate can report. config.py rejects gate.block names outside this set (it imports them from here), and
# tests/test_config.py pins it to run_gate's output keys, so a new check must be added here too.
GATE_CHECKS = ("ac_traceability", "assertion", "ac_drift", "open_questions", "commits_without_story", "open_decisions", "rule_coverage", "rule_drift")


def rule_findings(cov):
    """What `rule_coverage` reports for a `rules.coverage` result: the uncovered, unknown, unconfirmed and changed ids, then one `?` for each of
    problems / missing / format / header that is not empty. Empty is a pass."""
    return [*cov["uncovered"], *cov["unknown"], *cov["unconfirmed"], *cov["changed"], *("?" for k in ("problems", "missing", "format", "header") if cov[k])]


def drift_findings(rl):
    """What `rule_drift` reports for a `rules` result: the drifted rule ids, `chain` / `problems`, and one `?` when the live rules were not read
    (GitHub unreachable, or `--offline`). Empty is a pass."""
    return [*rl.get("drift", []), *(["?"] if "drift_note" in rl else [])]


MAX_TEST_IDS = 2000  # distinct test ids read from section 3; more is a finding (each id is searched for in every source file)
E_TOO_MANY_IDS = f"more than {MAX_TEST_IDS} test ids in section 3: the rest are not checked and count as unmapped"


def _cap_test_ids(rows):
    """`(rows, over)`: `rows` with only the first MAX_TEST_IDS distinct test ids kept (the others dropped, so an AC that relied on them has no known test:
    fail closed), and whether any id was dropped. The ledger is PR content: the cost of the checks is ids x source bytes."""
    seen, over, out = set(), False, []
    for r in rows:
        keep = []
        for t in r["tests"]:
            if t in seen or len(seen) < MAX_TEST_IDS:
                seen.add(t)
                keep.append(t)
            else:
                over = True
        out.append({**r, "tests": keep})
    return out, over


def run_gate(ledger_md, sources, changed, issue=None, block=(), tests=None, reason=UNREACHABLE, commits=None,
             skipped=0, decisions_md=None, decisions_unreadable=False, rules_ctx=None):
    """`issue`: the live `issues.Issue` for the drift check, or None. `decisions_md`: text of Decisions_<n>.md, or None when there is no such file; `decisions_unreadable`: the file
    exists but could not be read (symlink, outside --src, oversize, not UTF-8): counted as unresolved, never skipped.
    `rules_ctx`: None when business rules are off (no `rules` key, no `rule_coverage` question: the v0.2 output exactly); else
    `{"files": {issue number: rules.parse_file result}, "drift": rules.drift(...) result}` for the files `Rules from:` names that could be read
    (the GETs of the drift check are made by the caller; a context without `drift` counts as not checked), and the result gains
    `rules` = `rules.coverage(...)` at phase `pr` plus `drift`, `unreachable` and, when the live rules were not read, `drift_note`; with `reconfirmed` in
    the context (`--base`: a list from `rules.reconfirmed`, or a fixed note text from `rules.RECONFIRM_NOTES` when it cannot be compared) also `reconfirmed` or `reconfirmed_note`."""
    from . import decisions, questions  # lazy: both import this module
    rows = parse_section3(ledger_md)
    checked, over = _cap_test_ids(rows)
    trace = check_traceability(checked, sources, len(ac_lines(ledger_md)))
    gaps = scan_assertions(changed, DEFAULT_TESTS if tests is None else tests)
    test_gaps, notes = check_test_assertions(checked, sources)
    gaps += test_gaps
    if over:  # fail closed: a finding under `assertion`, and the AC whose tests were dropped are unmapped
        gaps.append({"test": "?", "issue": "too many test ids"})
        notes.append(E_TOO_MANY_IDS)
    drift = check_drift(ledger_md, issue, reason)
    # An AC row with no test, or none that exists, fails; no rows at all stays "not evaluated".
    qs = questions.parse_questions(ledger_md)
    open_q = [q["id"] for q in qs["open"]]
    ds = decisions.parse_decisions(decisions_md or "")  # open and rejected are both unresolved (a decision a live `supersedes` answers is not); reported only when the file has D lines
    eff = decisions.effective(ds)
    rejected = eff["rejected"]
    if rules_ctx is not None:  # business rules on: a rejected `out of scope:` line is carried out by rule_coverage (the rule is back in scope), not an open decision
        from . import rules  # lazy: rules imports this module
        rejected = [d for d in rejected if not rules.rejected_exclusion(d)]
    open_d = [d["id"] for d in sorted(eff["open"] + rejected, key=lambda d: int(d["id"][1:]))]
    open_d += ["?"] * (ds["unparsed"] or decisions_unreadable or decisions.supersede_problem(ds))  # fail closed: a malformed D line, an unclosed fence or comment, an unreadable file, a bad supersedes
    no_story, commits_note = commits or ([], None)
    open_ledger = unclosed(ledger_md)  # fail closed: counts as an open-questions finding, so `gate.block` can stop on it
    found = {"ac_traceability": trace["unmapped"], "assertion": gaps, "ac_drift": drift["drift"],
             "open_questions": open_q or open_ledger, "commits_without_story": no_story, "open_decisions": open_d}
    cov = None
    if rules_ctx is not None:
        from . import rules  # lazy: rules imports this module
        cov = rules.coverage(ledger_md, sources, rules_ctx["files"], ledger_rules_from(ledger_md), ledger_rules_reviewed(ledger_md), ds, "pr", trace)
        found["rule_coverage"] = rule_findings(cov)
        d = rules_ctx.get("drift") or rules.DRIFT_OFFLINE
        cov["drift"], cov["unreachable"] = list(d["drift"]), bool(d["unreachable"])
        if not d["evaluated"]:  # a fixed text, whatever the caller passed
            cov["drift_note"] = rules.DRIFT_UNREACHABLE if cov["unreachable"] else rules.DRIFT_OFFLINE_NOTE
        found["rule_drift"] = drift_findings(cov)
        if "reconfirmed" in rules_ctx:  # CI (`--base`): the target branch's `Rules reviewed:` against this one; a note, never in `found` (nothing blocks on it)
            if not isinstance(rules_ctx["reconfirmed"], list):  # not comparable: no list (not [], that would read as a pass), one of the fixed texts
                cov["reconfirmed_note"] = rules_ctx["reconfirmed"] if rules_ctx["reconfirmed"] in rules.RECONFIRM_NOTES else rules.RECONFIRM_UNREADABLE
            else:
                cov["reconfirmed"] = list(rules_ctx["reconfirmed"])
    blocked_by = [k for k in found if k in block and found[k]]
    return {"traceability": trace, "assertion_gaps": gaps, "assertion_notes": notes, "drift": drift,
            "missing_evidence": missing_evidence(rows), "open_questions": open_q,
            "commits_without_story": no_story, **({"open_decisions": open_d} if open_d or decisions.has_decision_lines(decisions_md or "") else {}),
            **({"rules": cov} if cov is not None else {}),
            **({"commits_note": commits_note} if commits_note else {}),
            **({"skipped_note": SKIPPED_NOTE.format(skipped)} if skipped else {}),
            **({"open_questions_note": questions.UNPARSED_NOTE} if qs["unparsed"] else {}),
            **({"ledger_unclosed": LEDGER_UNCLOSED_NOTE} if open_ledger else {}), "blocked_by": blocked_by, "blocking": bool(blocked_by)}


def _code(s, cap=200):
    """Untrusted text as one inline-code span: no backticks or newlines, length-capped."""
    return "`" + (" ".join(str(s).replace("`", "'").split())[:cap] or "?") + "`"


TARGET_CONFIG_NOTE = "target branch (strict)"  # `result["config"]` when CI judged the PR with the target branch's config
PR_CONFIG_NOTE = "pull request"  # ... or with the PR's own strict config, when `--base` was given
TRUST_NOTE = "trust-introducing PR: review ghp.stack.json as code"  # this PR edits the profile, the lockfile or strict.test_command
KITS_TRUST_NOTE = "trust-introducing PR: review ghp.kits.lock.json as code"  # this PR adds the first kits lockfile (the target has none)
IGNORED_ROOTS_NOTE = re.compile(r"[0-9]{1,6} detected root\(s\) cannot be named in a profile and are not covered by it: review them as code")


def ignored_roots_note(n):
    """The one fixed warning for `n` detected roots a profile cannot name (a count, never a path); `render_summary` prints it only in this shape."""
    return f"{n} detected root(s) cannot be named in a profile and are not covered by it: review them as code"


def _ids(values, cap=20):
    more = [f"…and {len(values) - cap} more"] if len(values) > cap else []
    return ", ".join([*map(_code, values[:cap]), *more])


def _rules_line(rl):
    """The one summary line for a `rule_coverage` result: rule ids, issue numbers and counts, with fixed words (never file or ledger text)."""
    unknown = [x for x in rl["unknown"] if x != "?"]
    parts = [(rl["uncovered"], lambda v: "not cited by an AC row whose test exists: " + _ids(v)),
             (unknown, lambda v: "cited but in no rules file: " + _ids(v)),
             (["?"] * (len(rl["unknown"]) - len(unknown)), lambda v: f"{len(v)} malformed `BR-` tag(s) in section 3"),
             (rl["unconfirmed"], lambda v: "not confirmed yet (read the rule, then run `ghp rules <issue number> --confirm` yourself, no terminal needed): " + _ids(v)),
             (rl["changed"], lambda v: ("cannot be judged while the `Rules reviewed:` line is malformed (fix it by hand; then a human re-confirms with `ghp rules <issue number> --confirm` in their own terminal): "
                                        if "rules-reviewed" in rl["header"] else
                                        "changed since confirmed, or its text no longer matches its hash (re-pull with `ghp rules <issue number>` if the file was edited by hand; "
                                        "a human re-confirms with `ghp rules <issue number> --confirm` in their own terminal after reading the change): ") + _ids(v)),
             (rl["problems"], lambda v: "error problem(s) in a rules file: " + _ids(v)),
             (rl["missing"], lambda v: "no usable rules file for issue " + _ids(v) + " (`ghp rules <issue number>`)"),
             (rl["format"], lambda v: "a rules file in another format for issue " + _ids(v) + " (re-pull with `ghp rules <issue number>`)"),
             (["rules-from"] if "rules-from" in rl["header"] else [], lambda v: "the ledger has no valid `Rules from:` line (`ghp dod <issue number> --refresh`)"),
             (["rules-reviewed"] if "rules-reviewed" in rl["header"] else [], lambda v: "the ledger's `Rules reviewed:` line is malformed (fix it by hand)")]
    return "- Business rules: " + "; ".join(f(v) for v, f in parts if v)


def _drift_line(rl):
    """The one summary line for a `rule_drift` result: rule ids and fixed words (never rule text)."""
    ids = [x for x in rl["drift"] if x not in ("chain", "problems")]
    parts = [(ids, lambda v: "the committed rules differ from the rules live in GitHub: " + _ids(v) + " (re-pull with `ghp rules <issue number>`)"),
             (["chain"] if "chain" in rl["drift"] else [], lambda v: "`Rules from:` is not the Epic/Feature chain in GitHub (`ghp dod <issue number> --refresh`)"),
             (["problems"] if "problems" in rl["drift"] else [], lambda v: "the Problems lines of a rules file differ from GitHub (re-pull with `ghp rules <issue number>`)"),
             (["x"] if rl.get("unreachable") else [], lambda v: "rules-unreachable: the rules in GitHub could not be read, so drift is not checked "
                                                              "(the gh login the gate runs under must be able to read the Epic/Feature issues of this repository)"),
             (["x"] if "drift_note" in rl and not rl.get("unreachable") else [], lambda v: "drift not checked (offline, or the live check was not run)")]
    return "- Rule drift: " + "; ".join(f(v) for v, f in parts if v)


def render_summary(result):
    """Markdown PR comment for a gate result (what `--summary-out` writes)."""
    head = "### ghp gate\n\n"
    source = f"config: {result['config']}\n" if result.get("config") in (TARGET_CONFIG_NOTE, PR_CONFIG_NOTE) else ""
    source += f"{TRUST_NOTE}\n" if result.get("trust_note") == TRUST_NOTE else ""
    source += f"{KITS_TRUST_NOTE}\n" if result.get("kits_trust_note") == KITS_TRUST_NOTE else ""
    note = result.get("ignored_roots_note")
    source += f"{note}\n" if isinstance(note, str) and IGNORED_ROOTS_NOTE.fullmatch(note) else ""
    if "traceability" not in result:  # no / unreadable ledger: advisory note only
        return head + f"Advisory: {_code(result.get('note', ''))}\n" + source
    trace, drift = result["traceability"], result["drift"]
    drift_line = ("AC changed in GitHub since the ledger was seeded" if drift["drift"]
                  else "no drift" if drift["evaluated"] else drift.get("note", "drift not checked"))
    lists = [("ac_traceability", "AC without a known test", trace["unmapped"]),
             ("assertion", "Test files or mapped tests with no assertions",
              [g.get("file") or g["test"] for g in result["assertion_gaps"]]),
             ("evidence", "Green AC without evidence", result["missing_evidence"]),
             ("open_questions", "Open questions in ledger section 2", result.get("open_questions", [])),
             ("open_decisions", "Open decisions in the decisions file", result.get("open_decisions", []))]
    found = {k: f"- {label}: {', '.join(map(_code, v))}" for k, label, v in lists if v}
    if cs := result.get("commits_without_story"):
        more = [f"…and {len(cs) - 20} more"] if len(cs) > 20 else []
        found["commits_without_story"] = ("- Commits without the issue's `#<n>` reference: "
                                          + ", ".join([*map(_code, cs[:20]), *more]))
    if isinstance(result.get("rules"), dict) and rule_findings(result["rules"]):
        found["rule_coverage"] = _rules_line(result["rules"])
    if isinstance(result.get("rules"), dict) and drift_findings(result["rules"]):
        found["rule_drift"] = _drift_line(result["rules"])
    if isinstance(result.get("rules"), dict) and (result["rules"].get("reconfirmed") or "reconfirmed_note" in result["rules"]):
        from . import rules  # lazy: rules imports this module
        note = result["rules"].get("reconfirmed_note")
        found["rules_reconfirmed"] = ("- " + (note if note in rules.RECONFIRM_NOTES else rules.RECONFIRM_UNREADABLE) if "reconfirmed_note" in result["rules"] else
                                      f"- rules reconfirmed: {rules.reconfirmed_text(result['rules']['reconfirmed'])}; {rules.RECONFIRM_NOTE}")
    if note := result.get("ledger_unclosed"):  # stands in for the open-questions line when that list is empty
        found["open_questions" if "open_questions" not in found else "ledger_unclosed"] = f"- {_code(note)}"
    if not trace["evaluated"]:
        found["trace_note"] = "- AC traceability not evaluated (no AC-tagged rows in section 3)"
    blocked = [found.pop(k, f"- {drift_line}") for k in result.get("blocked_by", [])]
    out = []
    if blocked:
        out += ["**Blocking - this check fails the PR:**", *blocked, ""]
    if found:
        out += ["**Advisory:**", *found.values(), ""]
    if not blocked and not found:
        out += ["No findings.", ""]
    tail = [f"AC drift: {drift_line}"]
    if result.get("open_decisions") == []:
        tail.append("Open decisions: none")
    if note := result.get("open_questions_note"):
        tail.append("Open questions: " + _code(note))
    if note := result.get("commits_note"):
        tail.append("Commit check: " + _code(note))
    if note := result.get("skipped_note"):
        tail.append("Files skipped: " + _code(note))
    if notes := result.get("assertion_notes"):
        tail.append("Assertion check: " + "; ".join(map(_code, notes)))  # informational, not a finding
    return head + "\n".join(out + tail) + "\n" + source


def render_error(reason):
    """Markdown body for a gate that could not run (or, for an invalid config or an unreadable ledger, FAILED). Token-shaped text is redacted
    (`gh.scrub`) before capping."""
    invalid, refused = isinstance(reason, ConfigInvalid), isinstance(reason, dod.LedgerError)
    reason = " ".join(scrub(reason).split())
    if invalid and str(reason).startswith(("ghp.stack.json at the target branch", "ghp.kits.lock.json at the target branch")):
        return f"### ghp gate\n\n**ghp gate FAILED: target branch trust files are invalid:** {_code(reason, 500)}\n\nFix the file on the target branch; an invalid one fails this check.\n"
    if invalid:
        return f"### ghp gate\n\n**ghp gate FAILED: ghp.config.json is invalid:** {_code(reason, 500)}\n\nFix the config; an invalid config fails this check.\n"
    if refused:
        return f"### ghp gate\n\n**ghp gate FAILED: the ledger cannot be read safely:** {_code(reason, 500)}\n\nFix the ledger; this check fails until it can be read.\n"
    return f"### ghp gate\n\nThe gate could not run: {_code(reason, 500)}\n\nThis check is advisory and does not fail the PR.\n"


# ---- the verb: `ghp gate <issue>` ----
def _strict():
    from . import strict  # lazy: strict imports this module
    return strict


def target_config(base, src):
    """The config this run uses (`gate`, `strict check`, `strict verify`, `strict mode`, `branch --id-from`). With a `base` REF the target branch's `ghp.config.json` is read first: when it says `mode: strict`, the whole run
    uses it and the PR's own file is never read (a PR cannot weaken its own strictness), and the target's profile and lockfile are read and validated
    too (`strict.read_target_trust`; ConfigInvalid fails closed). A target with no config, or an advisory one, leaves the PR's config in charge. A REF
    that is no commit here (a shallow clone) cannot say what the target holds: with an advisory PR config that is the v0.2 path (the gate goes on);
    with a strict one, or an invalid one, it fails closed."""
    if base is not None:
        try:
            target = Config.load_from_ref(base, repo=src)
        except ConfigInvalid:
            if _resolve_base(src, base):
                raise  # the base is a commit and its config is the problem
            head = Config.load()
            if head.mode == "strict":
                raise
            return head
        if target is not None and target.mode == "strict":
            target.from_target = True
            _strict().read_target_trust(target, src)  # the profile and the lockfile of the target commit; ConfigInvalid fails closed
            return target
    return Config.load()


def _config(args):
    return target_config(args.base, args.src)


def _ref(cfg, text):
    """The issue named on the command line: this repository only (the configured one). ValueError (exit 2) for anything else."""
    try:
        return parse_ref(text, Ref(cfg.owner, cfg.repo, 1))
    except RefError as e:
        raise ValueError(str(e)) from None


def _gate_base(cfg, args):
    """The ref `check_commits` walks from: the `--base` as given, but in strict mode the full commit id it resolves to (a tag named like
    the base must not move the range the strict floor's `commits_without_story` judges)."""
    return (_resolve_base(args.src, args.base) or args.base) if args.base is not None and cfg.mode == "strict" else args.base


def _rules_ctx(cfg, ledger_md, src):
    """None when rules are off (nothing is read: the v0.2 gate exactly). Else the parsed rules files that the ledger's `Rules from:` names
    (`rules.local_files`: confined reads, digits-only ids; an unreadable one is simply absent, and the gate reports it as missing)."""
    from . import rules  # lazy: rules imports this module
    return {"files": rules.local_files(cfg, ledger_md, src)} if cfg.rules["enabled"] else None


def evaluate(gh, cfg, ref, args):
    """The gate's result for issue `ref` under config `cfg` (a dict; the verb prints it). READ-ONLY: the only GitHub calls are GETs of the issue (the
    AC-drift read) and, with rules on, of its ancestors (`rules.drift`); `--offline` makes none. `args`: src, changed, base, offline.
    A ledger that is missing or unreadable is an advisory note (exit 0, as ado); an ambiguous one (`dod.LedgerError`) is not caught here."""
    from . import decisions  # lazy: decisions imports this module
    src, offline = args.src, getattr(args, "offline", False)
    path = dod.dod_file(cfg.dod_path, ref.number)
    if not path.exists():
        return {"ledger": "missing", "blocking": False, "note": f"no ledger at {path} — run `ghp dod {ref.number}` to enable tracking"}
    ledger_md = _read(path, src, set())  # dod_path comes from the PR's config: read the ledger as confined as any file under --src
    if ledger_md is None:
        return {"ledger": "unreadable", "blocking": False, "note": f"ledger at {path} exists but is unreadable — advisory only"}
    dpath = decisions.decisions_file(cfg.dod_path, ref.number)
    decisions_md = _read(dpath, src, set()) if os.path.lexists(dpath) else None  # confined like the ledger
    unreadable = os.path.lexists(dpath) and decisions_md is None
    tests = {**DEFAULT_TESTS, **cfg.gate["tests"]}
    issue, reason = None, UNREACHABLE
    if offline:
        reason = "offline — drift not checked"
    else:
        try:
            issue = issues.get(gh, ref)
        except (GhError, ValueError):
            pass  # missing info (no issue, not signed in, not an issue) never blocks: drift is simply not checked
    rules_ctx = _rules_ctx(cfg, ledger_md, src)
    if rules_ctx is not None:  # the live check: GETs only, from the issue given here (never a number read from the ledger or a file)
        from . import rules
        rules_ctx["drift"] = rules.DRIFT_OFFLINE if offline else rules.drift(
            gh, ref, cfg, ledger_md, rules_ctx["files"], decisions.parse_decisions(decisions_md or ""))
        if args.base is not None:  # CI: the target branch's own ledger (hardened git read), judged from the issue given here
            rules_ctx["reconfirmed"] = _strict().reconfirmed_vs_base(cfg.dod_path, src, args.base, ref.number, dod.ledger_rules_reviewed(ledger_md)["hashes"])
    skipped = set()
    sources = discover_tests(src, tests, skipped)
    changed = {f: t for f in (args.changed or []) if (t := _read(f, src, skipped)) is not None}
    return run_gate(ledger_md, sources, changed, issue, skipped=len(skipped), block=cfg.effective_gate_block(), tests=tests, reason=reason,
                    commits=check_commits(src, ref.number, _gate_base(cfg, args)),
                    decisions_md=decisions_md, decisions_unreadable=unreadable, rules_ctx=rules_ctx)


def _notes(cfg, args, out):
    """Additive notes for the CI comment: only when the target's config judged this run (`from_target`), or a strict PR config under --base."""
    if cfg.from_target:
        out["config"] = TARGET_CONFIG_NOTE
        if _strict().edits_trust_files(cfg, args.src):  # the PR edits the profile, lockfile or test command: say so in the comment
            out["trust_note"] = TRUST_NOTE
        if note := _strict().ignored_roots_note(args.src):  # roots no profile can name are outside the stack rules: say so, never block
            out["ignored_roots_note"] = note
        if _strict().adds_kits_lock(cfg, args.src):  # the first kits lockfile: nobody has reviewed its pins as code
            out["kits_trust_note"] = KITS_TRUST_NOTE
    elif args.base is not None and cfg.mode == "strict":
        out["config"] = PR_CONFIG_NOTE
        stack_note, kits_note = _strict().adoption_notes(cfg, args.src, args.base)  # the target is not strict: this PR turns it on
        if stack_note:
            out["trust_note"] = TRUST_NOTE
        if kits_note:
            out["kits_trust_note"] = KITS_TRUST_NOTE


def register(sub):
    p = sub.add_parser("gate", help="Advisory DoD gate: AC<->test traceability, assertions, AC drift, evidence (read-only; exit 1 on a blocking finding)")
    p.add_argument("ref", help="5, #5, owner/repo#5 or an issue URL")
    p.add_argument("--src", default=".", help="repo dir to scan for existing tests")
    p.add_argument("--changed", nargs="*", default=[], help="changed files (PR diff)")
    p.add_argument("--base", metavar="REF", help="ref the PR targets, e.g. origin/main: commits in REF..HEAD must reference the issue (default: origin/HEAD)")
    p.add_argument("--offline", action="store_true", help="skip the GitHub reads (AC drift and rule drift not checked)")
    p.add_argument("--summary-out", metavar="PATH", help="write a markdown summary here (also when the gate cannot run)")


def run(args, gh):
    err = out = None
    try:
        cfg = _config(args)
        out = evaluate(gh, cfg, _ref(cfg, args.ref), args)
        _notes(cfg, args, out)
    except (ConfigError, ValueError) as e:  # nothing is judged, the gate fails
        # 3 lets CI fail closed: an invalid config, and an ambiguous ledger (LedgerError: it can hide section 3, so it must never look like a plain refusal);
        # 2 is a refusal (bad usage, a missing config, a ref of another repository, any other ValueError)
        err, code = e, 3 if isinstance(e, (ConfigInvalid, dod.LedgerError)) else 2
    except GhError as e:
        err, code = e, 1
    if err is not None:
        print("error: " + comments._show(" ".join(scrub(err).split()), 500), file=sys.stderr)  # one line, token-shaped text removed, escapes for control characters
    else:
        print(json.dumps(out, indent=2))
    if args.summary_out:  # after the JSON, so a write failure cannot hide a blocking result
        summary = Path(args.summary_out)
        try:
            summary.parent.mkdir(parents=True, exist_ok=True)
            summary.write_text(render_error(err) if err is not None else render_summary(out), encoding="utf-8")
        except OSError:  # advisory output only: keep the gate's own exit code
            print("warning: could not write the --summary-out file", file=sys.stderr)
    if err is not None:
        return code
    return 1 if out.get("blocking") else 0
