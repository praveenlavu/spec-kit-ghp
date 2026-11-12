"""T33 AC4: contract tests against the REAL `specify` CLI (never imported: it has its own interpreter). Ported from spec-kit-ado (rename table; ado's
`status.read_run` checks are not ported, ghp's status has no run awareness yet; PyYAML is replaced by tests/test_workflow.py's reader, and the mini
workflows are written as JSON, which Spec Kit's YAML loader reads).

They pin what spec-kit-ghp relies on in Spec Kit's workflow engine: a gate pauses without a TTY, `verdict_input` plus `resume --input` completes it, a
resume restarts at the current nested step, a verdict persists into a later loop iteration until a resume resets it, the `state.json` keys
`ghp approvals sync` reads, `execution.version`, that the shipped workflow validates and installs, and that the shipped one-pass loops never apply a
plan that changed after the gate.

Skipped unless `specify` is on PATH and SPECKIT_CONTRACT=1. Every test runs once per Spec Kit CLI: `SPECKIT_CONTRACT_SPECIFY` may list several
`specify` executables separated by the path separator (the weekly CI job installs the minimum, 1.2.1, and the latest side by side); the default is the
`specify` on PATH. Each test runs `specify` in its own temp project with a private HOME, no TTY and no network; no step calls the real `ghp`.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ghp import approvals
from test_workflow import load_workflow

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "contract-workflow.yml"
SHIPPED = ROOT / "workflows" / "ghp-lifecycle" / "workflow.yml"
_LISTED = [p for p in os.environ.get("SPECKIT_CONTRACT_SPECIFY", "").split(os.pathsep) if p]
SPECIFYS = _LISTED or ([shutil.which("specify")] if shutil.which("specify") else [])

OPTED_IN = os.environ.get("SPECKIT_CONTRACT") == "1"
FLOOR = (1, 2, 1)  # requires.speckit_version of extension.yml and the workflow

# Opted in with no `specify` to run is a FAILURE, never a vacuous green (review round 1, M2).
pytestmark = pytest.mark.skipif(not OPTED_IN, reason="needs SPECKIT_CONTRACT=1 (and the specify CLI on PATH, or SPECKIT_CONTRACT_SPECIFY)")


def _version(path):
    """The `CLI Version` that `specify version` prints (private HOME, no TTY), or None when it cannot be run or read."""
    import re
    import tempfile
    with tempfile.TemporaryDirectory() as home:
        env = {**os.environ, "HOME": home, "XDG_CACHE_HOME": home, "XDG_CONFIG_HOME": home, "NO_COLOR": "1", "TERM": "dumb"}
        try:
            cp = subprocess.run([path, "version"], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return None
    m = re.search(r"CLI Version\s+([0-9][0-9A-Za-z.+-]*)", cp.stdout)
    return m.group(1) if cp.returncode == 0 and m else None


def _tuple(version):
    import re
    return tuple(int(x) for x in re.findall(r"^(\d+)\.(\d+)\.(\d+)", version)[0])


VERSIONS = {p: _version(p) for p in SPECIFYS} if OPTED_IN else {}


def test_the_contract_runs_on_a_real_specify_of_a_supported_version():
    assert SPECIFYS, "SPECKIT_CONTRACT=1 but no specify executable: none on PATH and SPECKIT_CONTRACT_SPECIFY is empty"
    for path, version in VERSIONS.items():
        print("contract run on %s: specify %s" % (path, version))  # the record (pytest -rA / -s shows it; the test ids carry it too)
        assert version is not None, "%s: `specify version` cannot be run or read" % path
        assert _tuple(version) >= FLOOR, "%s: specify %s is older than the supported floor %s" % (path, version, ".".join(map(str, FLOOR)))

ISSUE = 42
GATE, NESTED = "review-spec", "approve-questions"  # a top-level gate and a gate inside the do-while `questions-loop`


class Project:
    """A temp Spec Kit project directory; every call is `specify ...` with no TTY (stdin is /dev/null)."""

    def __init__(self, tmp_path, specify):
        self.specify = specify
        self.dir = tmp_path / "proj"
        self.dir.mkdir()
        home = tmp_path / "home"
        home.mkdir()
        self.env = {**os.environ, "HOME": str(home), "XDG_CACHE_HOME": str(home / ".cache"), "XDG_CONFIG_HOME": str(home / ".config"),
                    "XDG_DATA_HOME": str(home / ".local" / "share"), "NO_COLOR": "1", "TERM": "dumb"}

    def sp(self, *args):
        return subprocess.run([self.specify, *args], cwd=self.dir, env=self.env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)

    def start(self, path=FIXTURE, issue=ISSUE):
        cp = self.sp("workflow", "run", str(path), "--input", "issue_number=%s" % issue, "--json")
        return cp, json.loads(cp.stdout)

    def resume(self, run_id, gate, verdict):
        """The resume exactly as the poller builds it (`approvals.resume_argv`): the real form is what the contract is checked against."""
        argv = approvals.resume_argv(run_id, gate, verdict)
        assert argv[:4] == ["specify", "workflow", "resume", run_id] and argv.count("--input") == len(approvals.GATES) == len(argv[4:]) // 2
        return self.sp(*argv[1:])

    def state(self, run_id):
        return json.loads((self.dir / ".specify" / "workflows" / "runs" / run_id / "state.json").read_text(encoding="utf-8"))

    def trace(self):
        path = self.dir / "trace.txt"
        return path.read_text(encoding="utf-8").split() if path.exists() else []

    def mark(self, name, on=True):
        (self.dir / name).touch() if on else (self.dir / name).unlink()


@pytest.fixture(params=SPECIFYS or [None], ids=lambda p: "specify-%s" % VERSIONS.get(p) if p else "no-specify")
def proj(tmp_path, request):
    if request.param is None or VERSIONS.get(request.param) is None:
        pytest.fail("SPECKIT_CONTRACT=1 but no runnable specify (%s)" % request.param)
    return Project(tmp_path, request.param)


def _nested(step):
    return step.split(":")[-2] if ":" in step else step


def _paused_at_nested_gate(proj):
    _, out = proj.start()
    assert proj.resume(out["run_id"], GATE, "approve").returncode == 0
    return out["run_id"]


def test_a_gate_without_a_tty_pauses_with_exit_0(proj):
    cp, out = proj.start()
    assert cp.returncode == 0
    assert (out["status"], out["current_step_id"], out["gate"]["step_id"], out["gate"]["options"]) == ("paused", GATE, GATE, ["approve", "reject"])
    st = proj.state(out["run_id"])
    assert (st["status"], st["current_step_id"], st["step_results"][GATE]["status"]) == ("paused", GATE, "paused")
    assert proj.trace() == ["pull"]


def test_verdict_input_with_resume_completes_the_gate(proj):
    _, out = proj.start()
    rid = out["run_id"]
    cp = proj.resume(rid, GATE, "approve")
    st = proj.state(rid)
    assert cp.returncode == 0 and st["status"] == "paused"
    assert st["step_results"][GATE]["status"] == "completed" and st["step_results"][GATE]["output"]["choice"] == "approve"
    assert _nested(st["current_step_id"]) == NESTED  # now at the gate in the loop
    assert proj.trace() == ["pull", "preview"]  # `pull` did not run again
    assert st["inputs"]["verdict_review_spec"] == "approve" and st["inputs"]["verdict_approve_questions"] == ""
    assert "verdict_approve_done" not in st["inputs"]  # the poller's resets of gates the workflow does not have are dropped, not refused
    assert proj.resume(rid, NESTED, "approve").returncode == 0
    st = proj.state(rid)
    assert st["status"] == "completed" and proj.trace() == ["pull", "preview", "apply", "applied"]


def test_a_reject_verdict_ends_the_run_without_running_later_steps(proj):
    _, out = proj.start()
    cp = proj.resume(out["run_id"], GATE, "reject")
    st = proj.state(out["run_id"])
    assert cp.returncode != 0 and st["status"] in ("aborted", "failed")
    assert proj.trace() == ["pull"]


def test_resume_restarts_at_the_current_nested_step(proj):
    rid = _paused_at_nested_gate(proj)
    proj.mark("fail-apply")
    cp = proj.resume(rid, NESTED, "approve")
    st = proj.state(rid)
    assert cp.returncode == 1 and st["status"] == "failed"  # a step exiting 2 stops the run
    assert _nested(st["current_step_id"]) == "questions-apply"
    proj.mark("fail-apply", on=False)
    cp = proj.sp("workflow", "resume", rid)  # a plain resume: the failed nested step again, not the loop head and not the gate
    assert cp.returncode == 0 and proj.state(rid)["status"] == "completed"
    assert proj.trace() == ["pull", "preview", "apply", "apply", "applied"]


def test_a_persisted_verdict_passes_a_later_iteration(proj):
    rid = _paused_at_nested_gate(proj)
    proj.mark("again")
    assert proj.resume(rid, NESTED, "approve").returncode == 0
    st = proj.state(rid)
    # iteration 2 passed its gate with the verdict left in the inputs, with no prompt: the hazard the shipped one-pass cap guards
    assert st["status"] == "completed" and st["inputs"]["verdict_approve_questions"] == "approve"
    assert proj.trace() == ["pull", "preview", "apply", "preview", "apply", "applied"]


def test_an_empty_input_resets_a_persisted_verdict(proj):
    rid = _paused_at_nested_gate(proj)
    proj.mark("fail-apply")
    assert proj.resume(rid, NESTED, "approve").returncode == 1
    assert proj.state(rid)["inputs"]["verdict_approve_questions"] == "approve"
    proj.mark("fail-apply", on=False)
    proj.mark("again")
    cp = proj.resume(rid, GATE, "approve")  # the resume of ANOTHER gate resets this one: iteration 2 pauses at its gate
    st = proj.state(rid)
    assert cp.returncode == 0 and st["status"] == "paused" and st["inputs"]["verdict_approve_questions"] == ""
    assert st["current_step_id"].endswith(":1") and _nested(st["current_step_id"]) == NESTED  # `<loop>:<step>:<n>`
    assert proj.trace() == ["pull", "preview", "apply", "apply", "preview"]


def test_state_json_has_the_keys_approvals_sync_reads(proj):
    _, out = proj.start()
    rid = out["run_id"]
    st = proj.state(rid)
    assert {"run_id", "workflow_id", "status", "current_step_id", "updated_at", "inputs", "step_results", "execution"} <= set(st)
    assert (st["run_id"], st["workflow_id"], st["status"]) == (rid, approvals.RUN_WORKFLOW, "paused")
    assert type(st["inputs"][approvals.RUN_INPUT]) is int and st["inputs"][approvals.RUN_INPUT] == ISSUE
    polled = approvals.read_run(str(proj.dir), rid)  # the poller's reader accepts the real file (it raises ValueError on anything it cannot trust)
    assert polled == {"run_id": rid, "status": "paused", "step": GATE, "number": ISSUE}
    assert proj.resume(rid, GATE, "approve").returncode == 0
    step = proj.state(rid)["current_step_id"]
    assert _nested(step) == NESTED and approvals.read_run(str(proj.dir), rid)["step"] == step
    proj.mark("again")
    proj.resume(rid, NESTED, "approve")  # leaves a later iteration behind; every id is still one approvals trusts
    assert all(approvals._step_ok(sid) for sid in proj.state(rid)["step_results"])


def test_execution_version_is_2(proj):
    _, out = proj.start()
    execution = proj.state(out["run_id"])["execution"]
    assert isinstance(execution, dict) and execution["version"] == 2


def test_shipped_workflow_validates_with_specify(proj):
    (proj.dir / ".specify" / "workflows").mkdir(parents=True)  # `add --dev <dir>` needs it (a `specify init` project has it)
    cp = proj.sp("workflow", "add", "--dev", str(SHIPPED.parent))  # validates before installing (exit 1 and no install on an invalid workflow)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert (proj.dir / ".specify" / "workflows" / "ghp-lifecycle" / "workflow.yml").read_bytes() == SHIPPED.read_bytes()
    cp = proj.sp("workflow", "info", "ghp-lifecycle")
    shipped = load_workflow(SHIPPED.read_text(encoding="utf-8"))
    assert cp.returncode == 0 and "ghp-lifecycle" in cp.stdout
    for name in shipped["inputs"]:
        assert name in cp.stdout
    assert "Steps (%d)" % len(shipped["steps"]) in cp.stdout


def test_an_invalid_verdict_input_is_refused(proj):  # the validator really runs: a gate naming an undeclared input must not install
    (proj.dir / ".specify").mkdir()
    bad = proj.dir / "bad.yml"
    bad.write_text(FIXTURE.read_text(encoding="utf-8").replace('verdict_input: "verdict_review_spec"', 'verdict_input: "undeclared"'), encoding="utf-8")
    cp = proj.sp("workflow", "add", str(bad))
    assert cp.returncode == 1 and "undeclared" in cp.stdout + cp.stderr


def test_a_non_number_issue_is_refused_by_the_engine(proj):
    cp = proj.sp("workflow", "run", str(FIXTURE), "--input", "issue_number=5;touch pwned", "--json")
    assert cp.returncode != 0 and not (proj.dir / "pwned").exists() and proj.trace() == []


# ---- the shipped one-pass loops against a stub `ghp` ----
LOOPS = ["questions", "plan-questions", "answers-questions", "push", "decisions", "sync", "pr"]
STUB_GHP = """#!/bin/sh
# Stand-in for `ghp` (issue 42): --plan-out writes a plan, --expect-plan applies it. With the file `changes` present the FIRST apply finds the world
# changed since the preview (exit 2 + <file>.new, as the real command does); later previews write the new plan.
plan=""; expect=""
echo "argv $*" >> argv.txt
while [ $# -gt 0 ]; do case "$1" in --plan-out) plan="$2"; shift;; --expect-plan) expect="$2"; shift;; esac; shift; done
if [ -n "$plan" ]; then
  mkdir -p "$(dirname "$plan")"; rm -f "$plan.new"; echo preview >> trace.txt
  n=$(cat .calls 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > .calls
  if [ -e changes ] && [ "$n" -gt 1 ]; then echo "plan B" > "$plan"; else echo "plan A" > "$plan"; fi
  exit 0
fi
if [ -n "$expect" ]; then
  echo apply >> trace.txt
  if [ -e changes ] && [ ! -e .refused ]; then touch .refused; echo "plan B" > "$expect.new"; echo "plan changed since approval" >&2; exit 2; fi
  exit 0
fi
exit 0
"""


def _mini(proj, step_ids, issue=ISSUE):
    """The SHIPPED steps `step_ids` with the shipped inputs, run by the real engine against the stub `ghp`."""
    wf = load_workflow(SHIPPED.read_text(encoding="utf-8"))
    steps = {s["id"]: s for s in wf["steps"]}
    mini = {"schema_version": wf["schema_version"], "workflow": wf["workflow"], "inputs": wf["inputs"], "steps": [steps[i] for i in step_ids]}
    path = proj.dir.parent / "mini.yml"
    path.write_text(json.dumps(mini, indent=1), encoding="utf-8")
    bindir = proj.dir.parent / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "ghp").write_text(STUB_GHP, encoding="utf-8")
    (bindir / "ghp").chmod(0o755)
    proj.env["PATH"] = "%s%s%s" % (bindir, os.pathsep, proj.env["PATH"])
    return proj.start(path, issue)


@pytest.mark.parametrize("loop", LOOPS)
def test_a_supplied_verdict_cannot_approve_a_changed_plan(proj, loop):
    proj.mark("changes")
    cp, out = _mini(proj, ["%s-loop" % loop, "%s-applied" % loop])
    rid = out["run_id"]
    assert out["status"] == "paused" and out["gate"]["step_id"] == "approve-%s" % loop
    assert proj.resume(rid, "approve-%s" % loop, "approve").returncode != 0
    st = proj.state(rid)
    # the apply refused plan B; the run ends FAILED with the gate passed once, never a second time on a plan nobody saw
    assert st["status"] == "failed" and proj.trace() == ["preview", "apply"]


@pytest.mark.parametrize("loop", LOOPS)
def test_an_unchanged_plan_applies_on_the_first_pass(proj, loop):
    cp, out = _mini(proj, ["%s-loop" % loop, "%s-applied" % loop])
    rid = out["run_id"]
    assert proj.resume(rid, "approve-%s" % loop, "approve").returncode == 0
    assert proj.state(rid)["status"] == "completed" and proj.trace() == ["preview", "apply"]
    lines = (proj.dir / "argv.txt").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and all(" %d " % ISSUE in ln for ln in lines)  # the guard passed the number through, as one argument


def test_a_plain_resume_of_a_refused_apply_never_applies_the_new_plan(proj):
    proj.mark("changes")
    _, out = _mini(proj, ["questions-loop", "questions-applied"])
    rid = out["run_id"]
    assert proj.resume(rid, "approve-questions", "approve").returncode != 0
    assert proj.sp("workflow", "resume", rid).returncode != 0
    assert proj.state(rid)["status"] == "failed" and proj.trace() == ["preview", "apply"]


@pytest.mark.parametrize("issue", ["-1", "1.5"])
def test_the_guard_stops_a_number_that_is_not_an_issue_number(proj, issue):
    cp, out = _mini(proj, ["check-review-spec"], issue)
    assert out["status"] == "failed" and not (proj.dir / "argv.txt").exists()


def test_base_check_stops_the_run_without_origin_head(proj):
    subprocess.run(["git", "init", "-q", str(proj.dir)], check=True, env=proj.env)
    cp, out = _mini(proj, ["base-check", "check-review-spec"])
    assert cp.returncode != 0 and out["status"] == "failed" and not (proj.dir / "argv.txt").exists()


def test_the_built_extension_file_installs_from_a_download_address(proj, tmp_path):
    """T50-AC3: the file `scripts/build_extension_zip.py` builds installs with `specify extension add --from <url>` (served on a loopback address,
    which Spec Kit allows over plain HTTP) and registers the 13 commands and 2 hooks. The untrusted-source question is answered `y` on stdin."""
    import functools
    import http.server
    import sys
    import threading
    out = tmp_path / "dist"
    cp = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_extension_zip.py"), str(out)], cwd=ROOT, capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr
    built = Path(cp.stdout.strip())
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
    handler.log_message = lambda *a, **k: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        (proj.dir / ".specify").mkdir(exist_ok=True)
        url = "http://127.0.0.1:%d/%s" % (server.server_address[1], built.name)
        cp = subprocess.run([proj.specify, "extension", "add", "ghp", "--from", url], cwd=proj.dir, env=proj.env, input="y\n", capture_output=True,
                            text=True, timeout=120)
    finally:
        server.shutdown()
    assert cp.returncode == 0 and "installed successfully" in cp.stdout, cp.stdout + cp.stderr
    listed = proj.sp("extension", "list")
    assert listed.returncode == 0 and re.search(r"Commands: 13 \| Hooks: 2", listed.stdout), listed.stdout
    installed = proj.dir / ".specify" / "extensions" / "ghp"
    assert (installed / "extension.yml").read_bytes() == (ROOT / "extension.yml").read_bytes()
    assert not (installed / "src").exists() and not (installed / "workflows").exists()
