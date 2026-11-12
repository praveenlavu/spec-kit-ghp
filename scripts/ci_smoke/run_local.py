#!/usr/bin/env python3
"""The local half of the CI rehearsal (T37 AC1): cases (a) to (d) of the reusable workflows, without a runner and without GitHub.

Each case in cases.json is built as a throwaway git repository from the static files under fixtures/ (fictional names only): `main` (also
refs/remotes/origin/main and origin/HEAD, as the workflows' "Resolve the target branch" step leaves them) holds the target config, a branch holds
the pull request. Then the same `ghp` verbs the workflows run are called in this process, with the real code paths (gate, strict mode, the
`pr create` base-branch check and closing-keyword scanner), and the exit code and fixed markers of the output are compared with the case.
The one GitHub client is `FakeTracker`: it answers the reads in fixtures/tracker.json and raises on anything else (and on every write).

    python3 scripts/ci_smoke/run_local.py          # all cases: prints `ok (x) ...` per case, exit 0 only when every one gave its expected result
    python3 scripts/ci_smoke/run_local.py a c      # some cases

A pass shows the checks give the expected answer on a fixture; it does not show that a hosted runner or a real pull request behaves the same
way (that is T39). Needs git and this checkout (src/ is put on the path; nothing is installed). Stdlib only.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))
FIX = HERE / "fixtures"
from ghp.__main__ import main as ghp_main  # noqa: E402
from ghp.gh import GhError, Viewer  # noqa: E402

_GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
            **{f"GIT_{k}_{f}": v for k in ("AUTHOR", "COMMITTER") for f, v in (("NAME", "Smoke"), ("EMAIL", "smoke@example.invalid"))}}


def load_cases():
    return json.loads((HERE / "cases.json").read_text(encoding="utf-8"))


class FakeTracker:
    """Stands in for `ghp.gh.Gh`: reads from fixtures/tracker.json, anything else (and any write) raises GhError."""

    def __init__(self):
        self.answers = json.loads((FIX / "tracker.json").read_text(encoding="utf-8"))
        self._armed, self.calls = False, []

    def arm(self):
        self._armed = True

    @property
    def armed(self):
        return self._armed

    def viewer(self):
        return Viewer(4242, "smoke-bot", None, "Bot")  # the CI token's identity: no approver

    def rest(self, method, path, body=None):
        self.calls.append((method, path))
        key = f"{method.upper()} {path}"
        if method.upper() != "GET" or key not in self.answers:
            raise GhError("E_SMOKE", None, f"smoke tracker: no answer for {key}")
        return json.loads(json.dumps(self.answers[key]))

    def graphql(self, query, variables=None):
        self.calls.append(("GRAPHQL", query[:40]))
        raise GhError("E_SMOKE", None, "smoke tracker: no GraphQL answers")


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env={**os.environ, **_GIT_ENV}).stdout.strip()


def _write(repo, files):
    for rel, fixture in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIX / fixture, p)


def build(case, repo):
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "remote", "add", "origin", str(repo / "nowhere.git"))
    _write(repo, {"ghp.config.json": case["target_config"], "README.md": "body.md"})
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "#1001 base")
    for ref in ["main", *case.get("other_bases", [])]:
        _git(repo, "update-ref", f"refs/remotes/origin/{ref}", "HEAD")
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    _git(repo, "checkout", "-q", "-b", case["branch"])
    _write(repo, case["pr_files"])
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "#1001 the change")


def run(argv, repo):
    """One ghp verb in `repo`, as the workflow step runs it: (exit code, stdout, stderr). The working directory and GHP_CONFIG are restored."""
    out, err = io.StringIO(), io.StringIO()
    cwd, saved = os.getcwd(), os.environ.pop("GHP_CONFIG", None)
    try:
        os.chdir(repo)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ghp_main(argv, gh=FakeTracker())
    finally:
        os.chdir(cwd)
        if saved is not None:
            os.environ["GHP_CONFIG"] = saved
    return code, out.getvalue(), err.getvalue()


def _got(code, out, step):
    got = {"exit": code}
    if "json" in step:
        try:
            doc = json.loads(out)
        except ValueError:
            doc = {}
        got["json"] = {k: doc.get(k) for k in step["json"]}
    if "contains" in step:
        got["contains"] = [s for s in step["contains"] if s in out]
    return got


def run_case(name, case):
    """[(argv, expected, got)] for every step that differs; [] when the case gave its expected result."""
    bad = []
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d) / "repo"
        repo.mkdir()
        build(case, repo)
        for step in case["steps"]:
            code, out, _err = run(step["argv"], repo)
            want = {k: v for k, v in step.items() if k != "argv"}
            got = _got(code, out, step)
            if got != want:
                bad.append((step["argv"], want, got))
    return bad


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cases = load_cases()
    names = argv or sorted(cases)
    unknown = [n for n in names if n not in cases]
    if unknown:
        print("unknown case: " + ", ".join(unknown), file=sys.stderr)
        return 2
    failed = 0
    for name in names:
        bad = run_case(name, cases[name])
        if bad:
            failed += 1
            print(f"FAIL ({name}) {cases[name]['title']}")
            for step_argv, want, got in bad:
                print(f"  ghp {' '.join(step_argv)}: expected {json.dumps(want)}, got {json.dumps(got)}")
        else:
            print(f"ok ({name}) {cases[name]['title']}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
