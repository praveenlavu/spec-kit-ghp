"""T35/T36: the reusable workflows a consumer repository calls (ghp-gate, ghp-branch-guard, ghp-strict-verify, ghp-link-on-merge).

PyYAML is not allowed (X1), so `load_yaml` is a small strict reader for the one shape these files use: block mappings and block lists of 2-space
indentation, plain, double-quoted and single-quoted scalars, one-line flow lists and `|` / `|-` block scalars. A tab, an odd indent, a duplicate key,
a flow mapping, an anchor, an alias, a tag or a `>` scalar is an error, never a guess. `on` stays the string "on".

Besides the structure, the shell of the steps is run in a real bash (macOS bash 3.2 and Linux bash 5 alike) with the real `ghp` on temp git repos, or
with stand-ins for `gh` / `ghp` that log their argv. Hosted behaviour (actions/checkout, the runner, GitHub's own closing) is T39's, not shown here."""
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import commit, git

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows"
GATE, GUARD, VERIFY, LINK = "ghp-gate", "ghp-branch-guard", "ghp-strict-verify", "ghp-link-on-merge"
REUSABLE = [GATE, GUARD, VERIFY, LINK]
CHECKOUT = "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"
SETUP_PYTHON = "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065"
PINS = {CHECKOUT: "v4.2.2", SETUP_PYTHON: "v5.6.0"}
SRC = ROOT / "src"


# ---- the reader ----------------------------------------------------------------------------------------------------------------------------------
class YamlError(ValueError):
    pass


_KEY = re.compile(r"([A-Za-z_][\w.-]*):(?: (.*))?$")


def _strip_comment(t):
    return re.sub(r"\s+#.*$", "", t)


def _scalar(t, n):
    t = t.strip()
    if t.startswith('"'):
        m = re.match(r'"((?:[^"\\]|\\.)*)"(?:\s+#.*)?$', t)
        if not m:
            raise YamlError("line %d: a bad double-quoted string" % n)
        return json.loads('"%s"' % m.group(1))
    if t.startswith("'"):
        m = re.match(r"'((?:[^']|'')*)'(?:\s+#.*)?$", t)
        if not m:
            raise YamlError("line %d: a bad single-quoted string" % n)
        return m.group(1).replace("''", "'")
    if t.startswith("["):
        m = re.match(r"\[([^\[\]{}]*)\](?:\s+#.*)?$", t)
        if not m:
            raise YamlError("line %d: only a one-line flow list of scalars is read" % n)
        items = m.group(1).split(",")
        if items == [""]:
            return []
        if any(not x.strip() for x in items):
            raise YamlError("line %d: an empty flow-list item" % n)
        return [_scalar(x, n) for x in items]
    if not t or t[0] in "&*!{}|>%@`],?" or (t[0] == "-" and t[1:2] in ("", " ")):
        raise YamlError("line %d: not read: %r" % (n, t))
    t = _strip_comment(t)
    if ": " in t or t.endswith(":") or " #" in t:
        raise YamlError("line %d: a plain scalar with ': ' or '#': %r" % (n, t))
    if t in ("true", "false"):
        return t == "true"
    if re.fullmatch(r"-?[0-9]+", t):
        return int(t)
    return t


def _rows(text):
    """(line number, indent, text, block) per content line; a `key: |` row carries its block scalar as `block`."""
    lines, rows, i = text.split("\n"), [], 0
    while i < len(lines):
        raw, n = lines[i], i + 1
        i += 1
        if "\t" in raw:
            raise YamlError("line %d: a tab" % n)
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent % 2:
            raise YamlError("line %d: an odd indent" % n)
        block = None
        m = re.match(r"^(.*:) (\|-?)$", _strip_comment(s))
        if m:
            key_indent = indent + (2 if s.startswith("- ") else 0)
            body = []
            while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip(" ")) > key_indent):
                if "\t" in lines[i]:
                    raise YamlError("line %d: a tab" % (i + 1))
                body.append(lines[i])
                i += 1
            while body and not body[-1].strip():
                body.pop()
            if not body:
                raise YamlError("line %d: an empty block scalar" % n)
            cut = min(len(b) - len(b.lstrip(" ")) for b in body if b.strip())
            block = "\n".join(b[cut:] for b in body) + ("" if m.group(2) == "|-" else "\n")
            s = m.group(1)
        rows.append([n, indent, s, block])
    return rows


def _block(rows, i, indent):
    if i >= len(rows) or rows[i][1] != indent:
        raise YamlError("line %d: bad indent" % (rows[i][0] if i < len(rows) else 0))
    if rows[i][2].startswith("- ") or rows[i][2] == "-":
        out = []
        while i < len(rows) and rows[i][1] == indent and rows[i][2].startswith("- "):
            n, _, body, block = rows[i]
            rest = body[2:].strip()
            if _KEY.match(rest):  # "- key: v" opens a mapping at indent + 2
                rows[i] = [n, indent + 2, rest, block]
                item, i = _block(rows, i, indent + 2)
                out.append(item)
            else:
                out.append(_scalar(rest, n))
                i += 1
        return out, i
    out = {}
    while i < len(rows) and rows[i][1] == indent:
        n, _, body, block = rows[i]
        m = _KEY.match(body)
        if not m:
            raise YamlError("line %d: not a key: %r" % (n, body))
        key, rest = m.group(1), m.group(2)
        if key in out:
            raise YamlError("line %d: duplicate key %r" % (n, key))
        i += 1
        if block is not None:
            out[key] = block
        elif rest is None or not _strip_comment(rest).strip():
            if i < len(rows) and rows[i][1] > indent:
                if rows[i][1] != indent + 2:
                    raise YamlError("line %d: a nested block must be indented by 2" % rows[i][0])
                out[key], i = _block(rows, i, indent + 2)
            else:
                out[key] = None
        else:
            out[key] = _scalar(rest, n)
    return out, i


def load_yaml(text):
    rows = _rows(text)
    if not rows:
        return {}
    value, i = _block(rows, 0, 0)
    if i != len(rows):
        raise YamlError("line %d: unexpected content" % rows[i][0])
    return value


def test_the_reader_reads_the_shapes_the_workflows_use():
    doc = load_yaml("on:\n  workflow_call:\n    inputs:\n      x:\n        type: boolean\n        default: false\n"
                    "jobs:\n  a:\n    if: github.event.pull_request.merged == true\n    steps:\n"
                    "      - uses: o/a@0123 # v1.2.3\n        with:\n          python-version: \"3.13\"\n"
                    "      - name: one\n        env:\n          A: ${{ inputs.x }}\n        run: |\n          echo \"$A\"\n\n          if x; then\n            y\n          fi\n"
                    "      - run: echo 'a b'\n    matrix: [\"3.9\", '3.13', x]\n")
    assert doc["on"] == {"workflow_call": {"inputs": {"x": {"type": "boolean", "default": False}}}}
    steps = doc["jobs"]["a"]["steps"]
    assert steps[0] == {"uses": "o/a@0123", "with": {"python-version": "3.13"}}
    assert steps[1]["run"] == 'echo "$A"\n\nif x; then\n  y\nfi\n' and steps[1]["env"] == {"A": "${{ inputs.x }}"}
    assert steps[2] == {"run": "echo 'a b'"} and doc["jobs"]["a"]["matrix"] == ["3.9", "3.13", "x"]
    assert doc["jobs"]["a"]["if"] == "github.event.pull_request.merged == true"


@pytest.mark.parametrize("text", ["a: 1\na: 2\n", "a:\n\tb: 1\n", "a:\n   b: 1\n", "a: &x 1\n", "a: *x\n", "a: !t 1\n", "a: {b: 1}\n",
                                  "a: >\n  x\n", "a: b: c\n", "a:\n    b: 1\n", "a: [b, [c]]\n", "a: \"x\n", "- a\nb: 1\n"])
def test_the_reader_refuses_what_it_does_not_read(text):
    with pytest.raises(YamlError):
        load_yaml(text)


# ---- helpers ---------------------------------------------------------------------------------------------------------------------------------------
def text_of(name):
    return (WF / f"{name}.yml").read_text(encoding="utf-8")


def load(name):
    return load_yaml(text_of(name))


def every_workflow():
    return sorted(p.stem for p in WF.glob("*.yml"))


def steps_of(name):
    return [(job, s) for job, j in load(name)["jobs"].items() for s in j["steps"]]


def step(name, title):
    (s,) = [s for _, s in steps_of(name) if s.get("name") == title]
    return s


def make_bin(tmp_path, **scripts):
    """A directory of stand-in executables; each body is a POSIX sh script."""
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    for name, body in scripts.items():
        p = d / name
        p.write_text("#!/bin/sh\n" + body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return d


def real_ghp(tmp_path):
    """`ghp` on PATH = this checkout's src/ (the console script has no cwd on sys.path; neither has this)."""
    return make_bin(tmp_path, ghp=f'PYTHONPATH="{SRC}" exec "{sys.executable}" -I -c "import sys;sys.path.insert(0, \\"{SRC}\\");'
                                  f'from ghp.__main__ import main;sys.exit(main())" "$@"\n')


def run_step(tmp_path, s, env, cwd=None, bindir=None, timeout=60):
    """Run a step's `run:` text the way `shell: bash` does (bash --noprofile --norc -eo pipefail), with a fresh RUNNER_TEMP / GITHUB_OUTPUT."""
    temp = tmp_path / "runner_temp"
    temp.mkdir(exist_ok=True)
    out, summary = temp / "github_output", temp / "step_summary"
    out.write_text("")
    script = tmp_path / "step.sh"
    script.write_text(s["run"])
    path = (f"{bindir}{os.pathsep}" if bindir else "") + os.environ.get("PATH", "")
    full = {"PATH": path, "HOME": os.environ.get("HOME", ""), "RUNNER_TEMP": str(temp), "GITHUB_OUTPUT": str(out),
            "GITHUB_STEP_SUMMARY": str(summary), "GITHUB_REPOSITORY": "octo/demo", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "GITHUB_ACTIONS": "true", "CI": "true", **env}
    r = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)], cwd=str(cwd or tmp_path), env=full,
                       capture_output=True, text=True, timeout=timeout)
    outputs = dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line)
    return r, outputs


# ---- AC1: workflow_call only, pull_request only, no PR code with secrets -------------------------------------------------------------------------
def test_the_reusable_workflows_exist_and_parse():
    for name in REUSABLE:
        assert (WF / f"{name}.yml").is_file(), name
    for name in every_workflow():
        load(name)  # every workflow of this repo is in the shape the reader reads


def _checkouts(job):
    return [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout@")]


def _mentions_secrets(job):
    return "secrets." in json.dumps(job)


def test_no_pull_request_target_and_no_secret_checkout():
    for name in every_workflow():
        assert "pull_request_target" not in text_of(name), name
    for name in REUSABLE:
        wf = load(name)
        assert list(wf["on"]) == ["workflow_call"], name
        for job_name, job in wf["jobs"].items():
            for c in _checkouts(job):
                assert c["with"]["persist-credentials"] is False, (name, job_name)
            if _mentions_secrets(job) and _checkouts(job):  # only the merged commit, only after the merge: it is the base branch's code by then
                assert job["if"] == "github.event.pull_request.merged == true", (name, job_name)
                for c in _checkouts(job):
                    assert c["with"]["ref"] == "${{ github.event.pull_request.merge_commit_sha }}", (name, job_name)
    for name in (GATE, GUARD, VERIFY):  # these check out the pull request's code: no secret at all, none declared
        assert "secrets." not in text_of(name) and "secrets" not in load(name)["on"]["workflow_call"], name
    for name in REUSABLE:  # each job checks the event, so a workflow_dispatch or a privileged caller fails closed
        assert '[ "$EVENT_NAME" != pull_request ]' in step(name, "Resolve the target branch")["run"], name
    assert '[ "$EVENT_NAME" != pull_request ]' in step(LINK, "Extract the issue numbers")["run"]  # its first step, before any write


def test_a_non_pull_request_event_fails_the_job(tmp_path):
    r, _ = run_step(tmp_path, step(GATE, "Resolve the target branch"), {"EVENT_NAME": "pull_request_target", "BASE_REF": "main", "DEFAULT_BRANCH": "main"})
    assert r.returncode == 1 and "pull_request event only" in r.stdout


# ---- AC2: the trust config is read from the base ref -------------------------------------------------------------------------------------------
ORG = {"owner": "octo", "repo": "demo"}


def _pr_repo(tmp_path, target, head, branch="feature/gh1001-x", default="main", base="main"):
    """`default` (and `base`) as refs/remotes/origin/*, holding `target`; the checked-out PR branch holds `head` (None: no config change)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", default)
    git(repo, "remote", "add", "origin", str(tmp_path / "nowhere.git"))
    commit(repo, "#1001 base", {"README.md": "x\n", **({"ghp.config.json": json.dumps(target)} if target is not None else {})})
    for b in {default, base}:
        git(repo, "update-ref", f"refs/remotes/origin/{b}", "HEAD")
    git(repo, "checkout", "-q", "-b", branch)
    commit(repo, "#1001 feature", {"src.txt": "y\n", **({"ghp.config.json": json.dumps(head)} if head is not None else {})})
    return repo


def test_gate_reads_trust_from_base(tmp_path, git_env):
    for name in REUSABLE:
        wf = load(name)
        resolve = step(name, "Resolve the target branch")
        assert resolve["env"]["BASE_REF"] == "${{ github.base_ref }}" and resolve["env"]["DEFAULT_BRANCH"] == "${{ github.event.repository.default_branch }}"
        assert 'git remote set-head origin "$DEFAULT_BRANCH"' in resolve["run"], name  # actions/checkout makes no origin/HEAD (E_TRUST_BASE_UNRESOLVED)
        for job in wf["jobs"].values():
            for c in _checkouts(job):
                assert c["with"]["fetch-depth"] == 0, name  # every branch is fetched, so origin/<target> exists
        for _, s in steps_of(name):  # every ghp verb that judges against a target is given it
            for cmd in re.findall(r"^\s*(?:GHP_CONFIG=\S+ )?ghp (?:gate|strict \w+|branch)\b.*$", s.get("run", ""), re.M):
                assert '--base "origin/${BASE_REF}"' in cmd, (name, cmd)
    # the real ghp: a PR that turns its own config advisory is still judged strict, because the target branch says so
    repo = _pr_repo(tmp_path, {**ORG, "mode": "strict"}, {**ORG, "mode": "advisory"})
    bindir = real_ghp(tmp_path)
    env = {"EVENT_NAME": "pull_request", "BASE_REF": "main", "DEFAULT_BRANCH": "main"}
    r, _ = run_step(tmp_path, step(GATE, "Resolve the target branch"), env, cwd=repo, bindir=bindir)
    assert r.returncode == 0, r.stderr
    assert git(repo, "symbolic-ref", "refs/remotes/origin/HEAD") == "refs/remotes/origin/main"
    r, out = run_step(tmp_path, step(GATE, "Resolve the mode"), env, cwd=repo, bindir=bindir)
    assert r.returncode == 0 and out["mode"] == "strict", (r.stdout, r.stderr)
    r, out = run_step(tmp_path, step(VERIFY, "Resolve the mode"), env, cwd=repo, bindir=bindir)
    assert r.returncode == 0 and out["rerun"] == "yes", (r.stdout, r.stderr)


def test_a_strict_config_only_in_the_pr_does_not_run_the_pr_s_test_command(tmp_path, git_env):
    repo = _pr_repo(tmp_path, ORG, {**ORG, "mode": "strict"})
    r, out = run_step(tmp_path, step(VERIFY, "Resolve the mode"), {"BASE_REF": "main"}, cwd=repo, bindir=real_ghp(tmp_path))
    assert r.returncode == 0 and out["rerun"] == "no" and "not strict" in r.stdout


@pytest.mark.parametrize("base, default, missing", [("main", "main", "main"), ("release/1.0", "main", "release/1.0")])
def test_an_unresolved_target_fails_closed(tmp_path, git_env, base, default, missing):
    repo = _pr_repo(tmp_path, ORG, None, default=default, base=base)
    git(repo, "update-ref", "-d", f"refs/remotes/origin/{missing}")
    r, _ = run_step(tmp_path, step(GATE, "Resolve the target branch"), {"EVENT_NAME": "pull_request", "BASE_REF": base, "DEFAULT_BRANCH": default}, cwd=repo)
    assert r.returncode == 1 and "does not resolve" in r.stdout


@pytest.mark.parametrize("bad", ["", "-x", "a..b", "a b", "a;b", "$(x)", "a\nb"])
def test_a_branch_name_outside_the_whitelist_fails_closed(tmp_path, bad):
    r, _ = run_step(tmp_path, step(GATE, "Resolve the target branch"), {"EVENT_NAME": "pull_request", "BASE_REF": bad, "DEFAULT_BRANCH": "main"})
    assert r.returncode == 1 and "does not accept" in r.stdout


def test_the_shared_steps_are_identical_in_every_workflow():
    for title in ("Install ghp", "Resolve the target branch"):
        runs = {name: step(name, title)["run"] for name in REUSABLE}
        assert len(set(runs.values())) == 1, title
    assert step(GATE, "Resolve the mode")["run"].split("case")[0] == step(VERIFY, "Resolve the mode")["run"].split("case")[0]


def test_the_branch_id_step_fails_a_strict_target_and_skips_an_advisory_one(tmp_path, git_env):
    repo = _pr_repo(tmp_path, ORG, None, branch="feature/no-issue")
    bindir = real_ghp(tmp_path)
    s = step(GATE, "Resolve the issue from the branch")
    assert s["env"]["BRANCH"] == "${{ github.head_ref }}"
    r, out = run_step(tmp_path, s, {"BRANCH": "feature/no-issue", "BASE_REF": "main", "MODE": "advisory"}, cwd=repo, bindir=bindir)
    assert r.returncode == 0 and out["id"] == "" and "skipped" in r.stdout
    r, _ = run_step(tmp_path, s, {"BRANCH": "feature/no-issue", "BASE_REF": "main", "MODE": "strict"}, cwd=repo, bindir=bindir)
    assert r.returncode == 1 and "cannot be skipped" in r.stdout
    r, out = run_step(tmp_path, s, {"BRANCH": "feature/gh1001-x", "BASE_REF": "main", "MODE": "strict"}, cwd=repo, bindir=bindir)
    assert r.returncode == 0 and out["id"] == "1001", (r.stdout, r.stderr)
    assert step(VERIFY, "Resolve the issue from the branch")["env"]["MODE"] == "strict"  # verify runs only for a strict target
    for bad in ("-h", "a..b", "a b", "a;b", "$(x)", ""):  # the PR's branch name is checked too (fail closed), before ghp sees it
        r, _ = run_step(tmp_path, s, {"BRANCH": bad, "BASE_REF": "main", "MODE": "advisory"}, cwd=repo, bindir=bindir)
        assert r.returncode == 1 and "does not accept" in r.stdout, bad
    r, out = run_step(tmp_path, s, {"BRANCH": "#1001", "BASE_REF": "main", "MODE": "strict"}, cwd=repo, bindir=bindir)
    assert r.returncode == 0 and out["id"] == "1001"  # the `#<n>` form ghp reads


def test_every_step_that_reads_the_pr_branch_checks_it_first():
    for name, title in ((GATE, "Resolve the issue from the branch"), (VERIFY, "Resolve the issue from the branch"),
                        (GUARD, "Check the branch names an existing issue")):
        run = step(name, title)["run"]
        assert run.index('[[ "$BRANCH" =~ ^[A-Za-z0-9._#][A-Za-z0-9._/#-]*$ ]]') < run.index("ghp branch"), name


def test_a_fake_ghp_cannot_inject_into_the_step_outputs(tmp_path, git_env):
    repo = _pr_repo(tmp_path, ORG, None)
    fake = make_bin(tmp_path, ghp='printf "1\\nmode=advisory\\n"; exit 0\n')
    r, out = run_step(tmp_path, step(GATE, "Resolve the issue from the branch"), {"BRANCH": "feature/gh1-x", "BASE_REF": "main", "MODE": "strict"},
                      cwd=repo, bindir=fake)
    assert r.returncode == 1 and (tmp_path / "runner_temp" / "github_output").read_text() == ""


def test_the_branch_guard_fails_without_an_issue_and_reads_the_issue_with_the_target_config(tmp_path, git_env):
    repo = _pr_repo(tmp_path, ORG, {"owner": "evil", "repo": "elsewhere"}, branch="gh1001-x")
    log = tmp_path / "calls"
    fake = make_bin(tmp_path, ghp=f'if [ "$1" = get ]; then cat "$GHP_CONFIG" >> "{log}"; echo "$*" >> "{log}"; exit "${{GET_RC:-0}}"; fi\n'
                                  f'if [ "$1" = branch ]; then echo 1001; exit 0; fi\nexit 9\n')
    s = step(GUARD, "Check the branch names an existing issue")
    assert s["env"]["GH_TOKEN"] == "${{ github.token }}"
    r, _ = run_step(tmp_path, s, {"BRANCH": "gh1001-x", "BASE_REF": "main"}, cwd=repo, bindir=fake)
    assert r.returncode == 0 and "#1001 exists" in r.stdout
    assert '"owner": "octo"' in log.read_text() and "evil" not in log.read_text() and "get 1001" in log.read_text()
    r, _ = run_step(tmp_path, s, {"BRANCH": "gh1001-x", "BASE_REF": "main", "GET_RC": "1"}, cwd=repo, bindir=fake)
    assert r.returncode == 1 and "could not be read" in r.stdout
    r, _ = run_step(tmp_path, s, {"BRANCH": "feature/x", "BASE_REF": "main"}, cwd=repo, bindir=real_ghp(tmp_path))
    assert r.returncode == 1 and "names no issue" in r.stdout


# ---- every non-zero exit of ghp fails the job (strict: 1, 2 and 3 are all failures) ----------------------------------------------------------------
VERDICT_STEPS = [(GATE, "Resolve the mode", {"BASE_REF": "main"}), (VERIFY, "Resolve the mode", {"BASE_REF": "main"}),
                 (GATE, "Resolve the issue from the branch", {"BRANCH": "gh1-x", "BASE_REF": "main", "MODE": "advisory"}),
                 (GATE, "Run the gate", {"STORY_ID": "1", "BASE_REF": "main"}),
                 (GATE, "Run the strict check", {"STORY_ID": "1", "BASE_REF": "main", "HEAD_SHA": "0" * 40}),
                 (VERIFY, "Re-run the recorded red and green commits", {"STORY_ID": "1", "BASE_REF": "main", "ALLOW_OVERRIDE": "false"})]


@pytest.mark.parametrize("name, title, env", VERDICT_STEPS, ids=lambda v: v if isinstance(v, str) else "")
@pytest.mark.parametrize("code", [2, 3, 4, 127])
def test_every_ghp_failure_fails_the_step(tmp_path, git_env, name, title, env, code):
    repo = _pr_repo(tmp_path, ORG, None)
    fake = make_bin(tmp_path, ghp=f'echo \'{{"mode": "strict", "source": "target branch", "findings": [{{"rule": "r-1"}}]}}\'; '
                                  f'printf "::error::forged\\nx\\r::error::forged\\n" >&2; exit {code}\n')
    r, _ = run_step(tmp_path, step(name, title), env, cwd=repo, bindir=fake)
    assert r.returncode == 1, (name, title, r.stdout)
    lines = re.split(r"[\r\n]", r.stdout)
    assert not any(ln.startswith("::error::forged") for ln in lines)  # ghp's stderr reaches the log with a prefix, \r included: no workflow command


def test_a_blocking_gate_fails_and_its_summary_reaches_the_job_summary(tmp_path, git_env):
    repo = _pr_repo(tmp_path, ORG, None)
    log = tmp_path / "argv"
    fake = make_bin(tmp_path, ghp=f'for a in "$@"; do printf "%s\\n" "$a"; done > "{log}"\n'
                                  'while [ $# -gt 0 ]; do [ "$1" = --summary-out ] && echo "### ghp gate" > "$2"; shift; done; exit 1\n')
    commit(repo, "#1001 odd names", {"-rf": "x\n", "a b.txt": "y\n"})
    r, _ = run_step(tmp_path, step(GATE, "Run the gate"), {"STORY_ID": "1001", "BASE_REF": "main"}, cwd=repo, bindir=fake)
    assert r.returncode == 1 and "blocking finding" in r.stdout
    argv = log.read_text().splitlines()
    assert argv[:2] == ["gate", "1001"] and "./-rf" in argv and "./a b.txt" in argv and "-rf" not in argv  # NUL-separated, ./-prefixed
    assert "### ghp gate" in (tmp_path / "runner_temp" / "step_summary").read_text()


def test_the_gate_and_the_strict_check_pass_cleanly(tmp_path, git_env):
    repo = _pr_repo(tmp_path, ORG, None)
    fake = make_bin(tmp_path, ghp='echo \'{"ok": true}\'; exit 0\n')
    for title, env in (("Run the gate", {"STORY_ID": "1", "BASE_REF": "main"}),
                       ("Run the strict check", {"STORY_ID": "1", "BASE_REF": "main", "HEAD_SHA": "a" * 40})):
        r, _ = run_step(tmp_path, step(GATE, title), env, cwd=repo, bindir=fake)
        assert r.returncode == 0, (title, r.stdout, r.stderr)


def test_the_strict_check_reports_rule_ids_only(tmp_path, git_env):
    repo = _pr_repo(tmp_path, ORG, None)
    fake = make_bin(tmp_path, ghp='echo \'{"findings": [{"rule": "ac-$(x)", "detail": "SECRET DETAIL"}]}\'; exit 2\n')
    r, _ = run_step(tmp_path, step(GATE, "Run the strict check"), {"STORY_ID": "1", "BASE_REF": "main", "HEAD_SHA": "a" * 40}, cwd=repo, bindir=fake)
    assert r.returncode == 1 and "rule ids: ac-x" in r.stdout and "SECRET" not in r.stdout
    r, _ = run_step(tmp_path, step(GATE, "Run the strict check"), {"STORY_ID": "1", "BASE_REF": "main", "HEAD_SHA": "HEAD"}, cwd=repo, bindir=fake)
    assert r.returncode == 1 and "not a commit id" in r.stdout


def test_the_gate_runs_the_strict_check_only_under_strict():
    s = step(GATE, "Run the strict check")
    assert s["if"] == "steps.story.outputs.id != '' && steps.mode.outputs.mode == 'strict'"
    assert step(GATE, "Run the gate")["if"] == "steps.story.outputs.id != ''"
    assert 'ghp strict check "$STORY_ID" --phase pr --head "$HEAD_SHA" --base "origin/${BASE_REF}"' in s["run"]
    v = step(VERIFY, "Re-run the recorded red and green commits")
    assert v["if"] == "steps.mode.outputs.rerun == 'yes'" and "--no-record" in v["run"]


# ---- installing ghp: a pinned ref, outside the checkout ---------------------------------------------------------------------------------------
def test_ghp_is_installed_from_a_pinned_ref_before_any_pr_file_exists():
    for name in REUSABLE:
        wf = load(name)
        ref = wf["on"]["workflow_call"]["inputs"]["ghp_ref"]
        assert ref["type"] == "string" and ref["required"] is True and "default" not in ref, name  # a consumer must pin it, no mutable default
        assert "pin a full commit SHA" in text_of(name).replace("\n# ", " ") or "Pin both to one full commit SHA" in text_of(name), name
        assert "needs the toolkit repository to be readable by the runner" in text_of(name).replace("\n# ", " "), name
        assert "setuptools (>=61) from PyPI unpinned" in text_of(name).replace("\n# ", " "), name
        for job in wf["jobs"].values():
            titles = [s.get("name") or s["uses"] for s in job["steps"]]
            assert titles.index("Install ghp") < min(i for i, s in enumerate(job["steps"]) if str(s.get("uses", "")).startswith("actions/checkout@")), name
        s = step(name, "Install ghp")
        assert s["env"] == {"GHP_REF": "${{ inputs.ghp_ref }}"}
        assert 'cd "$RUNNER_TEMP"' in s["run"] and "python -I -m pip install" in s["run"]
        assert '"spec-kit-ghp @ git+https://github.com/praveenlavu/spec-kit-ghp.git@${GHP_REF}"' in s["run"]


@pytest.mark.parametrize("ref", ["main", "v1", "v1.2", "1.2.3", "0" * 39, "A" * 40, "v1.2.3; x", "v1.2.3\nx", "", "--upgrade"])
def test_an_unpinned_ghp_ref_is_refused(tmp_path, ref):
    fake = make_bin(tmp_path, python="echo PIP-RAN; exit 0\n", ghp="exit 0\n")
    r, _ = run_step(tmp_path, step(GATE, "Install ghp"), {"GHP_REF": ref}, bindir=fake)
    assert r.returncode == 1 and "PIP-RAN" not in r.stdout and "ghp_ref must be" in r.stdout


@pytest.mark.parametrize("ref", ["v0.1.0", "0123456789abcdef0123456789abcdef01234567"])
def test_a_pinned_ghp_ref_is_installed(tmp_path, ref):
    log = tmp_path / "pip"
    fake = make_bin(tmp_path, python=f'pwd > "{log}"; echo "$*" >> "{log}"\n', ghp="echo ghp 0.1.0\n")
    r, _ = run_step(tmp_path, step(GATE, "Install ghp"), {"GHP_REF": ref}, bindir=fake)
    cwd, argv = log.read_text().splitlines()
    assert r.returncode == 0 and Path(cwd).resolve() == (tmp_path / "runner_temp").resolve()
    assert argv.endswith(f"spec-kit-ghp @ git+https://github.com/praveenlavu/spec-kit-ghp.git@{ref}")


def install_requirement(name):
    """The quoted `<dist> @ git+...` requirement of a workflow's Install ghp step."""
    (req,) = re.findall(r'"([^"]+ @ git\+https://github\.com/praveenlavu/spec-kit-ghp\.git@\$\{GHP_REF\}")', step(name, "Install ghp")["run"])
    return req


def pyproject_name():
    try:
        import tomllib  # 3.11+
    except ImportError:  # dev-only fallback for 3.9/3.10; tomli is in the dev venv
        import tomli as tomllib
    with open(ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]["name"]


def test_the_install_line_names_the_pyproject_distribution():
    """pip refuses `<a> @ <url>` when the metadata at <url> names another distribution, so the two must never drift."""
    for name in REUSABLE:
        assert install_requirement(name).split(" @ ", 1)[0] == pyproject_name(), name


def test_pip_accepts_the_install_line_against_a_local_build(tmp_path):
    """pip's own name check, offline: each workflow's requirement with the git URL swapped for a copy of this source tree.
    shortcut: the git fetch at a tag is not exercised (no network); the hosted consumer run covers it."""
    import shutil
    src = tmp_path / "src-copy"
    shutil.copytree(ROOT / "src", src / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    for f in ("pyproject.toml", "README.md", "LICENSE", "MANIFEST.in"):
        shutil.copy2(ROOT / f, src / f)
    for name in REUSABLE:
        req = install_requirement(name).split(" @ ", 1)[0] + " @ " + src.as_uri()
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--dry-run", "--no-index", "--no-build-isolation", "--no-deps",
                            "--disable-pip-version-check", req], capture_output=True, text=True, cwd=tmp_path)
        assert r.returncode == 0, (name, r.stdout[-2000:], r.stderr[-2000:])


# ---- AC3: pinned actions; untrusted text only through env --------------------------------------------------------------------------------------
def test_actions_are_sha_pinned():
    for name in every_workflow():
        uses = re.findall(r"^\s*(?:- )?uses:\s*(\S+)(.*)$", text_of(name), re.M)
        for ref, comment in uses:
            assert ref in PINS, (name, ref)  # only the two reviewed commits
            assert comment.strip() == f"# {PINS[ref]}", (name, ref)
        for _, s in steps_of(name):
            assert "uses" not in s or s["uses"] in PINS, (name, s)


UNTRUSTED = re.compile(r"github\.event\.(?:pull_request\.(?:title|body|head\.(?:ref|label|repo))|issue\.|comment\.|review\.|head_commit\.|commits)"
                       r"|github\.head_ref|inputs\.")


def _where(obj, path=()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _where(v, path + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _where(v, path + (i,))
    elif isinstance(obj, str):
        yield path, obj


def test_untrusted_text_is_env_only():
    for name in every_workflow():
        wf = load(name)
        for _, s in steps_of(name):
            assert "${{" not in s.get("run", ""), (name, s.get("name"))  # no expression is ever pasted into a script
        for path, value in _where(wf):
            for expr in re.findall(r"\$\{\{(.*?)\}\}", value):
                if UNTRUSTED.search(expr) and path[-1] != "if":  # PR text, the branch name and the caller's inputs: an env value of its own, nothing else
                    assert path[-2] == "env" and value == "${{%s}}" % expr, (name, path, value)
        for _, s in steps_of(name):  # and each such variable is used, and only inside double quotes
            for var, val in s.get("env", {}).items():
                if UNTRUSTED.search(str(val)):
                    uses = [(line, m.start()) for line in s.get("run", "").splitlines() for m in re.finditer(r"\$\{?%s\b" % var, line)]
                    by_python = 'os.environ.get("%s"' % var in s.get("run", "")  # or read by name inside `python3 -I -c`, never pasted
                    assert (uses or by_python) and all(_in_double_quotes(line, pos) for line, pos in uses), (name, var)


def _in_double_quotes(line, pos):
    return len(re.findall(r'(?<!\\)"', line[:pos])) % 2 == 1


def test_the_env_rule_knows_the_untrusted_contexts():
    assert _in_double_quotes('echo "a $X"', 8) and not _in_double_quotes("echo $X", 5)
    assert UNTRUSTED.search(" github.event.pull_request.body ") and UNTRUSTED.search(" github.head_ref ") and not UNTRUSTED.search(" github.base_ref ")


# ---- AC4: least privilege ----------------------------------------------------------------------------------------------------------------------
ALLOWED = {GATE: {"contents": "read", "issues": "read"}, GUARD: {"contents": "read", "issues": "read"}, VERIFY: {"contents": "read"},
           LINK: {"contents": "read", "issues": "write"}}


def test_permissions_are_minimal():
    for name, want in ALLOWED.items():
        wf = load(name)
        assert wf["permissions"] == want, name
        for job_name, job in wf["jobs"].items():
            assert "permissions" not in job, (name, job_name)  # a job never widens the top level
            assert job["concurrency"]["group"].startswith("${{ github.workflow }}-%s-" % name), (name, job_name)
            assert job["runs-on"] == "ubuntu-24.04" and isinstance(job["timeout-minutes"], int)
    for name in every_workflow():
        assert "write-all" not in text_of(name) and "read-all" not in text_of(name), name
        assert ": write" not in (text_of(name).replace("issues: write", "") if name == LINK else text_of(name)), name
    override = load(VERIFY)["on"]["workflow_call"]["inputs"]["allow-override"]
    assert override["type"] == "boolean" and override["default"] is False


def test_every_step_runs_in_bash_with_pipefail():
    for name in REUSABLE:
        assert load(name)["defaults"] == {"run": {"shell": "bash"}}, name


def test_every_shell_block_parses_with_bash_n(tmp_path):
    for name in every_workflow():
        for _, s in steps_of(name):
            if "run" in s:
                p = tmp_path / "b.sh"
                p.write_text(s["run"])
                r = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True)
                assert r.returncode == 0, (name, s.get("name"), r.stderr)



# ---- T36: link on merge ------------------------------------------------------------------------------------------------------------------------
DONE_IF = "inputs.close-story && env.HAS_PROJECT_TOKEN == 'true' && steps.ids.outputs.ids != ''"
FAKE_GH = """echo "$*" >> "$LOG"
case "$1 $2" in
  "api "*/comments) printf '%s' "$SEEN"; exit 0 ;;
  "api "*) [ "${VIEW_RC:-0}" = 0 ] && echo "${KIND:-issue} ${STATE:-open}"; exit "${VIEW_RC:-0}" ;;
  "issue comment") while [ $# -gt 0 ]; do [ "$1" = --body-file ] && sed 's/^/BODY: /' "$2" >> "$LOG"; shift; done; exit "${COMMENT_RC:-0}" ;;
  "issue close") exit 0 ;;
esac
exit 9
"""


def _link(tmp_path, ids="7", **env):
    log = tmp_path / "gh.log"
    log.write_text("")
    bindir = make_bin(tmp_path, gh=FAKE_GH)
    full = {"LOG": str(log), "IDS": ids, "PR_NUM": "42", "BASE_REF": "main", "DEFAULT_BRANCH": "main", "CLOSE_STORY": "false",
            "HAS_PROJECT_TOKEN": "false", "GH_TOKEN": "t", "SEEN": "", **env}
    r, out = run_step(tmp_path, step(LINK, "Comment on and close the issues"), full, bindir=bindir)
    _link.outputs = out
    return r, log.read_text()


def test_project_move_requires_the_project_secret(tmp_path):
    wf = load(LINK)
    assert wf["on"]["workflow_call"]["secrets"] == {"GHP_PROJECT_TOKEN": {"description": wf["on"]["workflow_call"]["secrets"]["GHP_PROJECT_TOKEN"]["description"],
                                                                           "required": False}}
    (job,) = wf["jobs"].values()
    assert job["env"] == {"HAS_PROJECT_TOKEN": "${{ secrets.GHP_PROJECT_TOKEN != '' }}"}  # a boolean only: the token itself is in no job env
    code = "\n".join(ln for ln in text_of(LINK).splitlines() if not ln.lstrip().startswith("#"))
    assert code.count("secrets.GHP_PROJECT_TOKEN") == 2 and code.count("secrets.") == 2
    done = step(LINK, "Close the stories with ghp done")
    assert done["env"]["GH_TOKEN"] == "${{ secrets.GHP_PROJECT_TOKEN }}" and done["if"] == DONE_IF
    assert done["env"]["IDS"] == "${{ steps.comment.outputs.issues }}"  # only the numbers the comment step read as issues
    assert [s.get("name") for _, s in steps_of(LINK) if re.search(r"^\s*ghp \"?done\"? ", s.get("run", ""), re.M)] == ["Close the stories with ghp done"]
    for _, s in steps_of(LINK):  # the setup steps of the done path run under the same condition, and only the done step holds the token
        if s.get("name") != done["name"] and "secrets." in json.dumps(s):
            raise AssertionError(s)
    comment = step(LINK, "Comment on and close the issues")
    assert comment["env"]["GH_TOKEN"] == "${{ github.token }}"
    for word in ("gh project", "graphql", "item-edit", "issue reopen", "issue edit", "-X ", "--method"):  # the Actions token never touches the board and only reads with gh api
        assert word not in comment["run"].lower(), word
    log = tmp_path / "ghp.log"
    fake = make_bin(tmp_path, ghp=f'echo "$*" >> "{log}"; exit 0\n')
    r, _ = run_step(tmp_path, done, {"GH_TOKEN": "", "IDS": "7", "PR_NUM": "42", "BASE_REF": "main", "DEFAULT_BRANCH": "main"}, bindir=fake)
    assert r.returncode == 1 and not log.exists()  # no token: nothing runs
    r, _ = run_step(tmp_path, done, {"GH_TOKEN": "t", "IDS": "7 9", "PR_NUM": "42", "BASE_REF": "main", "DEFAULT_BRANCH": "main"}, bindir=fake)
    assert r.returncode == 0 and log.read_text().splitlines() == ["done 7 --apply", "done 9 --apply"]
    log.unlink()
    r, _ = run_step(tmp_path, done, {"GH_TOKEN": "t", "IDS": "7", "PR_NUM": "42", "BASE_REF": "release/1.0", "DEFAULT_BRANCH": "main"}, bindir=fake)
    assert r.returncode == 0 and log.read_text().splitlines() == ["done 7 --pr 42 --apply"]  # GitHub ignored Closes: ghp done --pr rules


@pytest.mark.parametrize("code", [1, 2, 3])
def test_a_refused_ghp_done_fails_the_job_after_every_story(tmp_path, code):
    log = tmp_path / "ghp.log"
    fake = make_bin(tmp_path, ghp=f'echo "$*" >> "{log}"; echo "::error::forged" >&2; exit {code}\n')
    r, _ = run_step(tmp_path, step(LINK, "Close the stories with ghp done"), {"GH_TOKEN": "t", "IDS": "7 9", "PR_NUM": "42", "BASE_REF": "main",
                                                                             "DEFAULT_BRANCH": "main"}, bindir=fake)
    assert r.returncode == 1 and len(log.read_text().splitlines()) == 2 and "\n::error::forged" not in "\n" + r.stdout


def test_job_degrades_without_the_project_secret(tmp_path):
    for close_story in ("true", "false"):
        r, log = _link(tmp_path, CLOSE_STORY=close_story)
        assert r.returncode == 0, (r.stdout, r.stderr)
        assert "BODY: <!-- spec-kit-ghp:merged:42 -->" in log and "BODY: Merged in #42." in log
        assert "BODY: The Project board was not moved by this workflow: the GHP_PROJECT_TOKEN secret is not set." in log
        assert "issue close 7 --repo octo/demo --reason completed" in log  # the issue is still closed, with the Actions token
    r, log = _link(tmp_path, HAS_PROJECT_TOKEN="true")
    assert r.returncode == 0 and "BODY: The Project board was not moved by this workflow: close-story is off." in log and "issue close 7" in log
    r, log = _link(tmp_path, HAS_PROJECT_TOKEN="true", CLOSE_STORY="true")
    assert r.returncode == 0 and "issue close" not in log  # left to ghp done, which closes and moves the board
    assert "BODY: ghp done will try to close the story next" in log and "if it refuses, the issue stays open" in log  # never claims it before it ran


def test_job_runs_only_when_merged(tmp_path):
    wf = load(LINK)
    for job in wf["jobs"].values():
        assert job["if"] == "github.event.pull_request.merged == true"
        assert job["concurrency"]["cancel-in-progress"] is False  # a half-done close is never cancelled by a newer run
    assert "issue reopen" not in text_of(LINK) and "issue edit" not in text_of(LINK)
    r, log = _link(tmp_path, BASE_REF="release/1.0")  # GitHub ignores Closes into a non-default base: comment, never close here
    assert r.returncode == 0 and "issue close" not in log and "ghp done 7 --pr 42" in r.stdout and "BODY: Merged in #42." in log
    for state in ("closed",):  # closed by the merge (or as not planned, or by hand): never closed again, never reopened
        r, log = _link(tmp_path, STATE=state)
        assert r.returncode == 0 and "issue close" not in log and "already closed" in r.stdout
    r, log = _link(tmp_path, SEEN="123")  # a re-run finds its own marker comment: no second comment
    assert r.returncode == 0 and "issue comment" not in log and "issue close 7" in log
    api = [ln for ln in log.splitlines() if ln.startswith("api ") and ln.split()[1].endswith("/comments")]
    assert api == ['api repos/octo/demo/issues/7/comments --paginate --jq .[] | select(.user.id == 41898282 and (.body | startswith("<!-- spec-kit-ghp:merged:42 -->"))) | .id']


@pytest.mark.parametrize("env", [{"VIEW_RC": "1"}, {"COMMENT_RC": "1"}, {"PR_NUM": "4 2"}, {"IDS": "7 x"}])
def test_a_failed_read_or_write_fails_the_job(tmp_path, env):
    r, log = _link(tmp_path, ids=env.pop("IDS", "7"), **env)
    assert r.returncode == 1


def test_close_story_is_off_by_default():
    i = load(LINK)["on"]["workflow_call"]["inputs"]["close-story"]
    assert i["type"] == "boolean" and i["default"] is False
    assert step(LINK, "Comment on and close the issues")["env"]["CLOSE_STORY"] == "${{ inputs.close-story }}"
    for title in ("Install ghp", "Resolve the target branch", "Close the stories with ghp done"):
        assert step(LINK, title)["if"] == DONE_IF, title
    (c,) = [s for _, s in steps_of(LINK) if str(s.get("uses", "")).startswith("actions/checkout@")]
    assert c["if"] == DONE_IF and c["with"]["ref"] == "${{ github.event.pull_request.merge_commit_sha }}"


def _extract(tmp_path, title, body="", event="pull_request", timeout=60, head_repo="octo/demo"):
    s = step(LINK, "Extract the issue numbers")
    assert s["env"] == {"EVENT_NAME": "${{ github.event_name }}", "HEAD_REPO": "${{ github.event.pull_request.head.repo.full_name }}",
                        "PR_TITLE": "${{ github.event.pull_request.title }}", "PR_BODY": "${{ github.event.pull_request.body }}"}
    return run_step(tmp_path, s, {"EVENT_NAME": event, "HEAD_REPO": head_repo, "PR_TITLE": title, "PR_BODY": body}, timeout=timeout)


@pytest.mark.parametrize("head_repo", ["someone/demo", "", "octo/demo-fork", "$(touch PWNED)"])
def test_a_fork_pr_writes_nothing_and_does_not_fail(tmp_path, head_repo):
    r, out = _extract(tmp_path, "Closes #7", "Fixes #8", head_repo=head_repo)
    assert r.returncode == 0 and out["ids"] == "" and "from a fork" in r.stdout and not (tmp_path / "PWNED").exists()
    assert step(LINK, "Comment on and close the issues")["if"] == "steps.ids.outputs.ids != ''"  # so no later step writes anything
    s = step(LINK, "Extract the issue numbers")["run"]
    assert s.index('"$HEAD_REPO" != "$GITHUB_REPOSITORY"') < s.index("python3")


def test_a_pull_request_number_is_skipped(tmp_path):
    r, log = _link(tmp_path, ids="7 9", KIND="pr", STATE="open")
    assert r.returncode == 0 and "issue comment" not in log and "issue close" not in log and "is a pull request" in r.stdout
    assert _link.outputs["issues"] == ""
    r, log = _link(tmp_path, ids="7 9")
    assert r.returncode == 0 and _link.outputs["issues"] == "7 9"
    r, log = _link(tmp_path, KIND="weird")
    assert r.returncode == 1


@pytest.mark.parametrize("title, body, ids", [
    ("Add x #7", "Closes #7", "7"), ("x", "closes: #3\nFixes #12, resolved #4", "3 4 12"), ("x", "FIXED #5 and fix #5", "5"),
    ("x", "Closes #7, #8", "7"),  # GitHub needs the keyword before each issue; under-reading is the safe side
    ("x", "Closes other/repo#9 and closes https://github.com/o/r/issues/10", ""), ("x", "Closes #0 closes #012 closes #12a closes GH-3", ""),
    ("x", "preclose #4 enclosed #5 closes#6 closes_#7", ""), ("x", "Closes #1234567890", ""),
    ("x", "```\nCloses #3\n```\n`closes #4`\n> closes #5\n<!-- closes #6 -->\ncloses #8", "8"),
    ("x", "Closes #3\n```\nunclosed fence closes #4", "3"),
    ("Closes #2", "", "2"), ("x", "", ""), ("x", "fixes\n#13", ""), ("x", "fixes\r\n#13 closes\x0b#14 closes\x0c#15", ""),
])
def test_issue_numbers_extracted_injection_safe(tmp_path, title, body, ids):
    r, out = _extract(tmp_path, title, body)
    assert r.returncode == 0 and out["ids"] == ids, (r.stdout, r.stderr)


@pytest.mark.parametrize("hostile", ['$(touch PWNED) closes #1', '`touch PWNED` closes #1', '"; touch PWNED; " closes #1', "closes #1\n::set-output name=ids::666\n",
                                     "closes #1\nids=666", "closes #1 $IFS ${PATH} %0Aids=666 \\ ' \""])
def test_hostile_pr_text_runs_nothing_and_yields_only_digits(tmp_path, hostile):
    r, out = _extract(tmp_path, hostile, hostile)
    assert r.returncode == 0 and out["ids"] == "1" and not (tmp_path / "PWNED").exists() and "666" not in r.stdout
    assert (tmp_path / "runner_temp" / "github_output").read_text() == "ids=1\n"


def test_more_than_twenty_issues_or_another_event_is_refused(tmp_path):
    r, _ = _extract(tmp_path, "x", " ".join(f"closes #{n}" for n in range(1, 22)))
    assert r.returncode == 1 and "more than 20" in r.stdout
    r, _ = _extract(tmp_path, "x", "closes #1", event="push")
    assert r.returncode == 1 and "pull_request event only" in r.stdout


SHAPES = {"spaces": lambda n: "closes" + " " * n, "keywords": lambda n: "close" * (n // 5), "hashes": lambda n: "closes #" * (n // 8),
          "ticks": lambda n: "`" * n, "comments": lambda n: "<!--" * (n // 4), "fences": lambda n: "```" * (n // 3),
          "quotes": lambda n: "> closes #1\n" * (n // 12), "digits": lambda n: "closes #" + "9" * n}


@pytest.mark.parametrize("size", [30_000, 300_000])
@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_extraction_is_linear_on_hostile_sizes(tmp_path, monkeypatch, capsys, size, shape):
    """30,000 characters run the whole step in bash. 300,000 do not fit in one environment string on Linux (128 KiB), so the step's own Python
    (cut out of the run block, byte for byte) runs in this process on them; GitHub caps a PR body at 65,536 characters."""
    import time
    body = SHAPES[shape](size)
    t0 = time.monotonic()
    if size <= 30_000:
        r, out = _extract(tmp_path, "x", body, timeout=20)
        assert r.returncode == 0 and out["ids"] == "", (r.stdout[-300:], r.stderr[-300:])
    else:
        monkeypatch.setenv("PR_TITLE", "x")
        monkeypatch.setenv("PR_BODY", body)
        exec(compile(_extraction_code(), "extract", "exec"), {"__name__": "__main__"})
        assert capsys.readouterr().out == "\n"
    assert time.monotonic() - t0 < 10


def _extraction_code():
    run = step(LINK, "Extract the issue numbers")["run"]
    return run.split("python3 -I -c '", 1)[1].split("') ||", 1)[0]


def test_the_cut_out_extraction_code_is_the_one_the_step_runs(monkeypatch, capsys):
    monkeypatch.setenv("PR_TITLE", "Closes #3")
    monkeypatch.setenv("PR_BODY", "fixes #1")
    exec(compile(_extraction_code(), "extract", "exec"), {"__name__": "__main__"})
    assert capsys.readouterr().out == "1 3\n"


def test_no_bare_done_word_as_an_argument_in_any_shell_step():
    """The first hosted actionlint run failed with shellcheck SC1010 ("Use semicolon or linefeed before 'done'") on `ghp done "$id" ...`:
    a bare `done` as an argument looks like the end of a loop. Quote the verb: `ghp "done" ...`."""
    for name in every_workflow():
        for _, s in steps_of(name):
            assert not re.search(r"^\s*ghp done\b", s.get("run", ""), re.M), (name, s.get("name"))
