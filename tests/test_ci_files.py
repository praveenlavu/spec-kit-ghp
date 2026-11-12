"""This repo's own workflows, checked as text (no YAML parser: stdlib only)."""
import re
from pathlib import Path

import pytest

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows"
FILES = sorted(WF.glob("*.yml"))


def text(p):
    return p.read_text(encoding="utf-8")


def run_blocks(t):
    """The text of every `run:` step: the inline value, or the indented lines of a `run: |` block."""
    lines, out = t.splitlines(), []
    for i, ln in enumerate(lines):
        m = re.match(r"^(\s*)(?:- )?run:\s*(.*)$", ln)
        if not m:
            continue
        indent, rest = len(m.group(1)), m.group(2)
        if rest not in ("|", ">", "|-", ">-"):
            out.append(rest)
            continue
        body = []
        for nxt in lines[i + 1:]:
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                break
            body.append(nxt)
        out.append("\n".join(body))
    return out


def test_the_workflows_exist():
    assert {p.name for p in FILES} == {"ci.yml", "commit-lint.yml", "ghp-gate.yml", "ghp-branch-guard.yml", "ghp-strict-verify.yml",
                                         "ghp-link-on-merge.yml", "speckit-contract.yml"}


def test_ci_matrix_has_the_floor_and_latest():
    t = text(WF / "ci.yml")
    m = re.search(r"python-version:\s*\[([^\]]*)\]", t)
    assert m and {'"3.9"', '"3.13"'} <= {v.strip() for v in m.group(1).split(",")}
    assert "runs-on: ubuntu-24.04" in t and "fetch-depth: 0" in t and "persist-credentials: false" in t


@pytest.mark.parametrize("wf", FILES, ids=lambda p: p.name)
def test_every_action_is_pinned_to_a_full_sha(wf):
    uses = re.findall(r"^\s*(?:- )?uses:\s*(\S+)(.*)$", text(wf), re.M)
    assert uses
    for ref, comment in uses:
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), ref
        assert re.match(r"\s+# v\d+\.\d+\.\d+", comment), ref


@pytest.mark.parametrize("wf", FILES, ids=lambda p: p.name)
def test_no_pull_request_target(wf):
    assert "pull_request_target" not in text(wf)


@pytest.mark.parametrize("wf", FILES, ids=lambda p: p.name)
def test_no_event_text_inside_run(wf):
    blocks = run_blocks(text(wf))
    assert blocks
    for b in blocks:
        assert "${{ github.event" not in b and "${{github.event" not in b, b


@pytest.mark.parametrize("wf", FILES, ids=lambda p: p.name)
def test_permissions_are_set_at_the_top_level(wf):
    assert re.search(r"^permissions:\n  contents: read$", text(wf), re.M)


def test_ci_runs_the_tests_scrub_and_build():
    t = text(WF / "ci.yml")
    assert re.search(r"^name: Tests$", t, re.M)
    for name in ("Install", "Run the tests", "Check for leaked names", "Check the package builds"):
        assert "- name: %s\n" % name in t, name
    assert "python -m pytest -q" in t and "python scripts/scrub_check.py" in t
    assert re.search(r"^concurrency:\n  group: .+\n  cancel-in-progress: true$", t, re.M)


def _scrub_step(tmp_path, denylist):
    """Run ci.yml's scrub step with a stub `python` that records its arguments: (exit code, the argument lines)."""
    import os
    import subprocess
    (step,) = [s for s in _ci()["jobs"]["test"]["steps"] if s.get("name") == "Check for leaked names"]
    assert step["env"] == {"SCRUB_DENYLIST": "${{ secrets.SCRUB_DENYLIST }}"}  # the secret reaches the step only as an environment variable
    bindir, log = tmp_path / "bin", tmp_path / "args"
    bindir.mkdir()
    for name in ("python", "python3"):
        (bindir / name).write_text('#!/bin/sh\necho "%s $*" >> "%s"\n' % (name, log))
        (bindir / name).chmod(0o755)
    (tmp_path / "s.sh").write_text(step["run"])
    env = {"PATH": f"{bindir}:/usr/bin:/bin", **({"SCRUB_DENYLIST": denylist} if denylist is not None else {})}
    r = subprocess.run(["bash", "-e", str(tmp_path / "s.sh")], capture_output=True, text=True, env=env)
    return r.returncode, log.read_text().splitlines() if log.exists() else []


@pytest.mark.parametrize("denylist,want", [("fictional-name\nother-name", "python scripts/scrub_check.py --require-denylist --history"),
                                           (None, "python scripts/scrub_check.py"), ("", "python scripts/scrub_check.py")])
def test_ci_scrub_scans_the_history_with_the_list_when_the_secret_is_set(tmp_path, denylist, want):
    """T60: with SCRUB_DENYLIST set the tracked tree and the whole history are scanned for its names (an empty list fails); without it the
    generic scan of the tracked tree (secret patterns) runs, as before."""
    code, args = _scrub_step(tmp_path, denylist)
    assert code == 0 and args == [want], args


def test_commit_lint_uses_the_script_and_env_only():
    t = text(WF / "commit-lint.yml")
    assert re.search(r"^name: Commit messages$", t, re.M)
    assert "scripts/commit_lint.py" in t and "BASE_SHA: ${{ github.event.pull_request.base.sha }}" in t


def test_run_blocks_helper_sees_inline_and_block_steps():
    t = "    steps:\n      - run: echo ${{ github.event.x }}\n      - name: a\n        run: |\n          one\n          two\n      - name: b\n"
    assert run_blocks(t) == ["echo ${{ github.event.x }}", "          one\n          two"]


# ---- T37: actionlint, the weekly contract run, the consumer example ----
ACTIONLINT_SHA256 = "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8"


def _ci():
    from test_github_workflows import load_yaml
    return load_yaml(text(WF / "ci.yml"))


def test_actionlint_runs_in_ci_pinned_and_digest_checked():
    job = _ci()["jobs"]["actionlint"]
    dl, lint = job["steps"][1], job["steps"][2]
    assert dl["env"] == {"ACTIONLINT_VERSION": "1.7.12", "ACTIONLINT_SHA256": ACTIONLINT_SHA256}
    assert "914e7df21a07ef503a81201c76d2b11c789d3fca" in text(WF / "ci.yml")  # the tag's commit, cited
    assert 'echo "${ACTIONLINT_SHA256}  $RUNNER_TEMP/$tarball" | sha256sum --strict -c -' in dl["run"] and "set -euo pipefail" in dl["run"]
    assert dl["run"].index('[[ "$ACTIONLINT_SHA256" =~ ^[0-9a-f]{64}$ ]]') < dl["run"].index("curl ")
    assert "curl -fsSL --proto '=https' --proto-redir '=https'" in dl["run"]
    assert dl["run"].index("sha256sum --strict -c") < dl["run"].index("tar -xzf") and '"$RUNNER_TEMP/actionlint/actionlint"' in lint["run"]
    assert "releases/download/v${ACTIONLINT_VERSION}/${tarball}" in dl["run"] and "actionlint_${ACTIONLINT_VERSION}_linux_amd64.tar.gz" in dl["run"]


def _download(tmp_path, digest):
    import hashlib, os, stat, subprocess
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "log"
    scripts = {"curl": 'while [ $# -gt 0 ]; do [ "$1" = -o ] && printf "not actionlint" > "$2"; shift; done\n',
               "tar": 'echo TAR-RAN >> "%s"\n' % log,
               "sha256sum": 'exec shasum -a 256 "$@"\n'}  # macOS has shasum only; Linux CI has sha256sum and uses this one too
    for name, body in scripts.items():
        (bindir / name).write_text("#!/bin/sh\n" + body)
        (bindir / name).chmod(0o755)
    real = __import__("shutil").which("sha256sum", path="/usr/bin:/bin")
    if real:
        (bindir / "sha256sum").unlink()
    step = _ci()["jobs"]["actionlint"]["steps"][1]
    (tmp_path / "s.sh").write_text(step["run"])
    (tmp_path / "t").mkdir()
    r = subprocess.run(["bash", str(tmp_path / "s.sh")], capture_output=True, text=True,
                       env={"PATH": f"{bindir}:{os.environ['PATH']}", "RUNNER_TEMP": str(tmp_path / "t"), "ACTIONLINT_VERSION": "1.7.12", "ACTIONLINT_SHA256": digest})
    return r, log.exists()


def test_a_download_with_another_digest_fails_before_it_is_unpacked(tmp_path):
    import hashlib
    r, unpacked = _download(tmp_path, ACTIONLINT_SHA256)
    assert r.returncode != 0 and not unpacked
    ok = tmp_path / "ok"
    ok.mkdir()
    r, unpacked = _download(ok, hashlib.sha256(b"not actionlint").hexdigest())  # the check itself works: the matching digest passes
    assert r.returncode == 0 and unpacked, r.stderr
    for bad in ("", "x", hashlib.sha256(b"not actionlint").hexdigest().upper(), hashlib.sha256(b"not actionlint").hexdigest()[:63]):
        d = tmp_path / ("bad%d" % len(bad))
        d.mkdir()
        r, unpacked = _download(d, bad)  # an empty or malformed digest never reaches sha256sum (BSD sha256sum exits 0 on an empty line)
        assert r.returncode != 0 and not unpacked and "not 64 hex" in r.stdout, bad


def test_the_speckit_contract_runs_weekly_on_the_minimum_and_the_latest():
    from test_github_workflows import load_yaml
    wf = load_yaml(text(WF / "speckit-contract.yml"))
    assert wf["on"] == {"schedule": [{"cron": "17 5 * * 1"}], "workflow_dispatch": None}
    job = wf["jobs"]["contract"]
    assert job["strategy"]["matrix"]["include"] == [{"speckit": "minimum", "ref": "9dca9bf42a031765ee5e5f2371137f2f26967e11"},
                                                    {"speckit": "latest", "ref": ""}]  # v1.2.1 by commit, and the newest release tag
    runs = "\n".join(s.get("run", "") for s in job["steps"])
    assert 'pip install --quiet "git+https://github.com/github/spec-kit.git@${SPECKIT_REF}"' in runs
    assert 'echo "SPECKIT_CONTRACT_SPECIFY=$RUNNER_TEMP/speckit/bin/specify" >> "$GITHUB_ENV"' in runs
    assert "tests/test_speckit_contract.py tests/test_workflow.py" in runs and "set -euo pipefail" in runs
    (run_tests,) = [s for s in job["steps"] if s.get("name") == "Run the contract tests"]
    assert run_tests["env"] == {"SPECKIT_CONTRACT": "1"}
    assert "grep -Eq '[0-9]+ skipped'" in runs  # a skipped contract test is a failure, never a vacuous green


@pytest.mark.parametrize("name", ["speckit-contract.yml", "ghp-gate.yml", "ghp-branch-guard.yml", "ghp-strict-verify.yml", "ghp-link-on-merge.yml"])
def test_the_new_workflows_set_concurrency_and_check_out_without_credentials(name):
    t = text(WF / name)
    assert re.search(r"^ *concurrency:\n +group: .+\n +cancel-in-progress: (true|false)$", t, re.M), name
    assert t.count("uses: actions/checkout@") == t.count("persist-credentials: false"), name


def test_the_consumer_example_reruns_on_a_base_switch():
    from test_github_workflows import load_yaml
    ex = load_yaml((Path(__file__).parent / "fixtures" / "consumer-example.yml").read_text(encoding="utf-8"))
    assert set(ex["on"]["pull_request"]["types"]) == {"opened", "synchronize", "reopened", "edited", "closed"}
    for job in ("gate", "branch-guard", "strict-verify"):
        assert ex["jobs"][job]["if"] == "github.event.action != 'closed'", job  # so `edited` reaches the checks, not the link job
    for name in ("ghp-gate.yml", "ghp-branch-guard.yml", "ghp-strict-verify.yml"):
        assert "types: [opened, synchronize, reopened, edited]" in text(WF / name) and "`edited`" in text(WF / name), name


def test_the_contract_tag_lookup_survives_pipefail():
    t = text(WF / "speckit-contract.yml")
    assert "| awk 'NR == 1')" in t and "head -n 1" not in t


def test_the_consumer_example_calls_real_workflows_inputs_and_secrets():
    from test_github_workflows import load_yaml
    ex_text = (Path(__file__).parent / "fixtures" / "consumer-example.yml").read_text(encoding="utf-8")
    ex = load_yaml(ex_text)
    assert list(ex["on"]) == ["pull_request"] and "pull_request_target" not in ex_text
    called = set()
    for job_name, job in ex["jobs"].items():
        m = re.fullmatch(r"praveenlavu/spec-kit-ghp/\.github/workflows/(ghp-[a-z-]+\.yml)@REPLACE-WITH-ONE-REVIEWED-COMMIT-SHA", job["uses"])
        assert m, job_name
        wf = load_yaml(text(WF / m.group(1)))  # a real file
        called.add(m.group(1))
        decl = wf["on"]["workflow_call"]
        assert set(job["with"]) <= set(decl["inputs"]) and job["with"]["ghp_ref"] == "REPLACE-WITH-ONE-REVIEWED-COMMIT-SHA", job_name
        assert set(job.get("secrets") or {}) <= set(decl.get("secrets") or {}), job_name
        assert job["permissions"] == wf["permissions"], job_name  # exactly what the called workflow declares
    assert called == {"ghp-gate.yml", "ghp-branch-guard.yml", "ghp-strict-verify.yml", "ghp-link-on-merge.yml"}
    assert ex["jobs"]["link"]["if"] == "github.event.action == 'closed' && github.event.pull_request.merged == true"
    assert ex["jobs"]["link"]["secrets"] == {"GHP_PROJECT_TOKEN": "${{ secrets.GHP_PROJECT_TOKEN }}"}
    for name in called:  # and each reusable workflow points at the example
        assert "tests/fixtures/consumer-example.yml is the full example" in text(WF / name)
