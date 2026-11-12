"""Shared fakes: a fake clock, a fake subprocess runner (`FakeRunner`) and a fake transport (`FakeGh`) for later tasks;
the tracker-neutral helpers ported from ado (T10): stack `profile`/`approve`, real temp git repos, the typed approval code and a pty runner."""
import copy
import json
import os
import pty
import re
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ghp import stack
from ghp.gh import VIEWER_QUERY, WriteNotArmed, check_rest_request, is_graphql_write


class FakeClock:
    """`sleep` advances `now`, so pacing and waits are asserted without real time."""

    def __init__(self, now=1000.0):
        self.now, self.sleeps = now, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def http(status=200, body=None, headers=None, rc=None, stderr=""):
    """A runner result shaped like `gh api -i`: (returncode, stdout, stderr). rc defaults to 0 below 400, else 1 (as gh)."""
    lines = [f"HTTP/2.0 {status} X"] + [f"{k}: {v}" for k, v in (headers or {}).items()]
    text = body if isinstance(body, str) else ("" if body is None else json.dumps(body))
    return (rc if rc is not None else (0 if status < 400 else 1)), "\r\n".join(lines) + "\r\n\r\n" + text, stderr


def viewer_reply(db_id=22502189, login="octo", typename="User", scopes=None):
    """The reply to `Gh.viewer()`'s GraphQL `viewer` read (scopes: the X-OAuth-Scopes header text, or None for no header)."""
    return http(200, {"data": {"viewer": {"databaseId": db_id, "login": login, "__typename": typename}}},
                None if scopes is None else {"X-OAuth-Scopes": scopes})


def bot_runner(scopes=None, other=None):
    """A runner shaped like GitHub for the Actions token: REST `user` is 403, GraphQL `viewer` is github-actions[bot] (41898282, T31).
    Anything else gets `other` (default a 404). `.paths` records every request path."""
    def run(argv, stdin, timeout):
        run.paths.append(argv[4])
        if argv[4] == "user":
            return http(403, {"message": "Resource not accessible by integration"})
        if argv[4] == "graphql" and json.loads(stdin or b"{}").get("query") == VIEWER_QUERY:
            return viewer_reply(41898282, "github-actions[bot]", scopes=scopes)
        return other or http(404, {"message": "Not Found"})
    run.paths = []
    return run


class FakeRunner:
    """Plays queued results (tuple from `http()`, or an exception to raise) and records every spawn."""

    def __init__(self, *results):
        self.results, self.calls = list(results), []

    def __call__(self, argv, stdin, timeout):
        self.calls.append({"argv": list(argv), "stdin": stdin, "timeout": timeout})
        assert self.results, f"unexpected spawn: {argv}"
        nxt = self.results.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt


class FakeGh:
    """In-memory stand-in for `Gh` (same four public methods) for tests of modules that call it.
    `on_rest(method, path, result)` / `on_graphql(substring, result)`; a result may be a callable(body_or_variables).
    Unknown calls fail loudly. Writes before `arm()` raise WriteNotArmed, as the real one does."""

    def __init__(self, viewer=None):
        self.calls, self._armed, self._viewer = [], False, viewer
        self._rest, self._gql = {}, []

    def on_rest(self, method, path, result):
        self._rest[(method.upper(), path)] = result
        return self

    def on_graphql(self, needle, result):
        self._gql.append((needle, result))
        return self

    def arm(self):
        self._armed = True

    @property
    def armed(self):
        return self._armed

    def viewer(self):
        assert self._viewer is not None, "FakeGh.viewer not configured"
        return self._viewer

    def rest(self, method, path, body=None):
        method = method.upper() if isinstance(method, str) else method
        check_rest_request(method, path, body)  # the real transport's refusals, from the one function
        self._gate(method not in ("GET", "HEAD"))
        self.calls.append(("rest", method, path, body))
        assert (method, path) in self._rest, f"unexpected REST call: {method} {path}"
        return self._answer(self._rest[(method, path)], body)

    def graphql(self, query, variables=None):
        self._gate(is_graphql_write(query))
        self.calls.append(("graphql", query, variables))
        for needle, result in self._gql:
            if needle in query:
                return self._answer(result, variables)
        raise AssertionError(f"unexpected GraphQL call: {query[:80]}")

    def _gate(self, write):
        if write and not self._armed:
            raise WriteNotArmed()

    @staticmethod
    def _answer(result, arg):
        return result(arg) if callable(result) else result


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def cfg():
    from ghp.config import Config
    return Config("octo", "demo")


# ---- ported from ado tests/conftest.py (T10): the stack profile fixtures, git helpers and the approval-code terminal ----
def profile(**over):
    p = {"schema": 1, "version": 2, "status": "draft",
         "stacks": [{"id": "python", "root": ".",
                     "detected": {"confidence": "high", "evidence": ["pyproject.toml"]},
                     "commands": {"test": {"run": "PYTHONPATH=src python3 -m pytest -q", "source": "S1"},
                                  "lint": {"run": "ruff check .", "source": "unverified"},
                                  "org_only": [{"run": "sf apex run test --wait 10", "source": "S2"}]},
                     "conventions": [{"rule": "Test files are named test_*.py", "source": "S1"},
                                     {"rule": "Keep modules small", "source": "unverified"}],
                     "dod": [{"item": "No new lint findings", "source": "S1"}]}],
         "sources": {"S1": {"kind": "official", "url": "https://docs.pytest.org/en/stable/", "retrieved": "2026-01-15",
                            "evidence": "Run tests with python -m pytest."},
                     "S2": {"kind": "repo", "path": "package.json", "retrieved": "2026-01-15", "evidence": "scripts.test"}}}
    p.update(over)
    return p


def approve(p, **over):
    """`p` as a human's approval records it: status approved and the hash of everything but `approval`."""
    p = copy.deepcopy(p)
    p["status"] = "approved"
    p["approval"] = {"by": "tech lead", "date": "2026-01-16", "sha256": "0" * 64}
    p["approval"]["sha256"] = stack.profile_hash(p)
    p["approval"].update(over)
    return p


# ---- real temp git repos for the review_block_on checks (F11) ----
_IDENTITY = {f"GIT_{k}_{f}": v for k in ("AUTHOR", "COMMITTER") for f, v in (("NAME", "Test"), ("EMAIL", "test@example.invalid"))}


def git(repo, *a):
    # a commit needs an identity even in a bare container (no global git config); an identity already in the environment wins
    return subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True, env={**_IDENTITY, **os.environ}).stdout.strip()


def write_files(root, files):
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


def commit(root, msg, files=None):
    write_files(root, files or {})
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", msg)
    return git(root, "rev-parse", "--short", "HEAD")


def review_md(sha, blocks="no", superseded=False, blocker=0, major=0):
    tail = " (superseded)" if superseded else ""
    verdict = "yes — `review_block_on` = [blocker] and 1 such finding(s) remain" if blocks == "yes" else "no"
    return (f"### Review — 2026-10-28 — `main...feat` @ `{sha}`{tail}\n\n"
            f"- **Confirmed by severity:** blocker {blocker} · major {major} · minor 0\n"
            f"- **Blocks PR:** {verdict}\n\n")


def ledger_md(*blocks, spec_dir="specs/001-x"):
    from ghp import dod
    return (f"# DoD\n- **Spec dir:** {spec_dir}\n\n{dod.SECTION2}\n\n## 3. AC\n\n"
            "## 4. Gate & review log  (appended by speckit.ghp.gate and speckit.ghp.review)\n\n"
            + "".join(blocks) + "## 5. Checklist\n- [ ] x\n")


@pytest.fixture
def git_env(monkeypatch):
    """Isolate git from the user's config (signing, hooks) and give commits a fixed identity."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for k in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{k}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{k}_EMAIL", "test@example.invalid")


@pytest.fixture
def real_detect():
    """Opt-in marker (see `_stub_detect`): the test runs the real stack detection of the strict `stack-unprofiled` rule."""


@pytest.fixture(autouse=True)
def _stub_detect(request, monkeypatch):
    """Most strict tests run in a plain temporary directory that is no git repository; `stack-unprofiled` would call that a
    finding. Those tests are about other rules, so detection finds nothing there; a test of the rule asks for `real_detect`."""
    if "real_detect" not in request.fixturenames:
        monkeypatch.setattr("ghp.strict._detected", lambda repo, commit: {"outcome": "unknown", "stacks": [], "domain_hints": []})


@pytest.fixture(autouse=True)
def _not_in_ci():
    """Hosted CI sets GITHUB_ACTIONS / CI, and with no origin/HEAD `approvals.target_trust` then refuses every read of the trust (T28 review I4, T28d). The tests run in
    plain temporary directories; a test of that refusal sets the variables itself. This edits os.environ directly (not through the `monkeypatch` fixture): a test that calls
    `monkeypatch.undo()` would otherwise restore the runner's values mid-test, which is exactly how the first hosted CI runs failed `test_an_unchanged_head_still_applies`."""
    saved = {k: os.environ.pop(k) for k in ("GITHUB_ACTIONS", "CI") if k in os.environ}
    yield
    for k in ("GITHUB_ACTIONS", "CI"):
        os.environ.pop(k, None)
    os.environ.update(saved)


APPROVAL_CODE = re.compile(r"approval code: ([0-9a-f]{12})")
_SRC = str(Path(__file__).resolve().parent.parent / "src")


def shown_code(screen):
    """The approval code on a screen (what a human would read and type)."""
    found = APPROVAL_CODE.findall(screen)
    assert found, "no approval code on the screen"
    return found[-1]


class TypedStdin:
    """An in-process terminal stdin (`isatty` is True) whose `readline` answers like a human who reads the screen: by default it types the
    code printed so far on `sys.stdout` (pytest's capture, read without consuming it). `answer` is returned as the line instead
    (a newline alone is an empty line, `""` end of input, an exception class is raised). `before(code)` runs after the prompt and before the answer:
    the hook point for changing a file between the prompt and the typed code. `lines` counts the readline calls."""

    def __init__(self, answer=None, before=None):
        self.answer, self.before, self.lines = answer, before, 0

    def isatty(self):
        return True

    def readline(self, size=-1):
        self.lines += 1
        if self.before is not None:
            self.before(shown_code(sys.stdout.getvalue()))
        if isinstance(self.answer, type) and issubclass(self.answer, BaseException):
            raise self.answer
        return self.answer if self.answer is not None else shown_code(sys.stdout.getvalue()) + "\n"


def terminal(monkeypatch, answer=None, before=None):
    """stdin is a `TypedStdin` and stdout a terminal (use with capsys); returns the stdin."""
    stdin = TypedStdin(answer, before)
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    return stdin


def run_in_pty(args, cwd, answer=None, before=None, stdout_pty=True, stdin_pty=True, interrupt=False, early=None, timeout=60, argv=None):
    """`python -m ghp ARGS` as a real process with a pty on stdin (and on stdout unless `stdout_pty` is False). When the screen shows
    `approval code: <12 hex>` it calls `before(code)` (change a file between the prompt and the typed code), then types `answer` (default:
    the code; `""` sends Ctrl-D, end of input) or, with `interrupt`, sends SIGINT. `early` is typed right after the start, before any prompt. Returns `(returncode, screen, stderr)`; `screen` has
    CRLF turned into LF. Without a code on the screen nothing is typed. `timeout` is a deadline for the whole run: a child still running
    then is killed and the test fails. `argv` replaces `python -m ghp ARGS` (for testing the helper itself)."""
    env = {**os.environ, "PYTHONPATH": _SRC}
    env.pop("GHP_CONFIG", None)
    master, slave = pty.openpty()
    proc = subprocess.Popen(argv or [sys.executable, "-m", "ghp", *args], cwd=cwd, env=env, stdin=slave if stdin_pty else subprocess.DEVNULL,
                            stdout=slave if stdout_pty else subprocess.PIPE, stderr=subprocess.PIPE)
    os.close(slave)
    if early is not None:
        os.write(master, early.encode() + b"\n")
    out, typed, deadline = b"", False, time.monotonic() + timeout
    try:
        while True:
            if time.monotonic() > deadline:
                proc.kill()
                pytest.fail(f"run_in_pty: the child did not finish within {timeout} s and was killed (it waits for input that never comes?)")
            if select.select([master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
            elif proc.poll() is not None:
                break
            if not typed and APPROVAL_CODE.search(out.decode(errors="replace")):
                typed, code = True, shown_code(out.decode(errors="replace"))
                if before is not None:
                    before(code)
                if interrupt:
                    proc.send_signal(signal.SIGINT)
                else:
                    os.write(master, b"\x04" if answer == "" else (code if answer is None else answer).encode() + b"\n")
        piped, err = proc.communicate(timeout=timeout)
    finally:
        os.close(master)
        if proc.poll() is None:
            proc.kill()
            proc.communicate()  # reaps the killed child and closes its pipes
    return proc.returncode, (out if stdout_pty else piped).decode().replace("\r\n", "\n"), err.decode()


# ---- a REAL Spec Kit repository (P2-17a): `specify init`, `specify extension add --dev` for the toolkit and for a fictional kit, committed ----
# Hand-made fixtures held only `.specify/extensions/<kit>/`; a real repo also commits `.specify/init-options.json`, `integration.json`,
