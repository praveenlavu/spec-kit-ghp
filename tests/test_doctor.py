"""T29 `ghp doctor`: rows `{id, state, message}`, state pass / warn / fail / unknown, read only, no token, fixed texts."""
import json
import os
import re
import shutil
import stat
import time
from pathlib import Path

import pytest

from conftest import FakeGh, approve, commit, git, profile
from ghp import __version__, doctor
from ghp.__main__ import main
from conftest import FakeRunner, bot_runner
from ghp.gh import Gh, GhError, GhMissing, RateLimited, ScopeMissing, Viewer, is_graphql_write

ME, OTHER = 4242, 777
VIEWER = Viewer(ME, "octocat", ["repo", "project"])
CFG = {"owner": "octo", "repo": "demo", "project_owner": "octo", "project_number": 3}
REPO_PATH, PROT_PATH = "repos/octo/demo", "repos/octo/demo/branches/main/protection"
ALL_FLOWS = ("Auto-add sub-issues to project", "Auto-close issue", "Item added to project", "Item closed", "Pull request linked to issue", "Pull request merged")
UPGRADE = "Upgrade to GitHub Pro or make this repository public to enable this feature."
SRC = Path(__file__).resolve().parent.parent / "src" / "ghp"

# every (row, state) a row can reach: the test for each is `test_doctor_<row>_<state>` (row dashes as underscores); a row not listed for a state cannot reach it
REACHABLE = {
    "git": ("pass", "fail", "unknown"), "specify": ("pass", "warn", "fail", "unknown"), "shallow": ("pass", "fail", "unknown"),
    "gh": ("pass", "fail", "unknown"), "identity": ("pass", "fail", "unknown"), "scopes": ("pass", "warn", "fail", "unknown"),
    "config": ("pass", "fail"), "config-target": ("pass", "warn", "fail", "unknown"),
    "extension": ("pass", "warn", "fail", "unknown"), "workflow": ("pass", "warn", "fail", "unknown"),
    "approvers": ("pass", "fail", "unknown"), "writers": ("pass", "warn"),
    "project-workflows": ("pass", "warn", "fail", "unknown"), "protection": ("pass", "warn", "fail", "unknown"),
    "overlap": ("pass", "warn", "unknown"), "stack": ("pass", "warn", "fail", "unknown"), "kits": ("pass", "warn", "fail"),
}
ROW_IDS = ["git", "specify", "shallow", "gh", "identity", "scopes", "config", "config-target", "extension", "workflow",
           "approvers", "writers", "project-workflows", "protection", "overlap", "stack", "kits"]


def tool(bindir, name, out="", code=0, body=None):
    path = Path(bindir) / name
    if path.is_symlink() or path.exists():
        path.unlink()  # never write through the symlink to the real git
    path.write_text(body or f"#!/bin/sh\nprintf '%s\\n' '{out}'\nexit {code}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def flows(enabled=(), disabled=ALL_FLOWS):
    nodes = [{"name": n, "enabled": True, "number": i} for i, n in enumerate(enabled, 1)]
    nodes += [{"name": n, "enabled": False, "number": 50 + i} for i, n in enumerate(disabled)]
    return {"user": {"projectV2": {"id": "PVT_x", "workflows": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}}


def boom(exc):
    def raise_it(_arg):
        raise exc
    return raise_it


def good_gh(viewer=VIEWER, repo=None, protection=None, workflows=None):
    gh = FakeGh(viewer)
    gh.on_rest("GET", REPO_PATH, repo if repo is not None else {"default_branch": "main", "private": True})
    gh.on_rest("GET", PROT_PATH, protection if protection is not None else {"url": "https://example.invalid/p"})
    gh.on_graphql("workflows(", workflows if workflows is not None else flows())
    return gh


def viewer_fails(exc):
    gh = good_gh()
    gh.viewer = lambda: (_ for _ in ()).throw(exc)
    return gh


@pytest.fixture
def env(tmp_path, monkeypatch, git_env):
    """A git repository with a valid config, a PATH holding only real git (a symlink) and a fake `gh` that prints a version."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "ghp.config.json").write_text(json.dumps(CFG))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    os.symlink(shutil.which("git"), bindir / "git")
    tool(bindir, "gh", "gh version 2.40.1 (2024-01-01)")
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    monkeypatch.chdir(repo)
    return repo, bindir


def rows(result):
    return {r["id"]: r for r in result["rows"]}


def run(repo, gh=None, **kw):
    return rows(doctor.run(str(repo), gh=good_gh() if gh is None else gh, **kw))


def target(repo, **over):
    """Commit `CFG + over` as the target branch's config and point origin/HEAD at it."""
    commit(repo, "target", {"ghp.config.json": json.dumps({**CFG, **over})})
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")


def no_target(repo):
    git(repo, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
    git(repo, "update-ref", "-d", "refs/remotes/origin/main")


def at(row, state):
    assert row["state"] == state, row
    return row["message"]


# ---- the pinned list: one test per reachable (row, state) ----
def test_every_reachable_row_state_has_its_test():
    names = {n for n, f in globals().items() if n.startswith("test_doctor_") and callable(f)}
    want = {"test_doctor_%s_%s" % (row.replace("-", "_"), state) for row, states in REACHABLE.items() for state in states}
    assert want <= names, sorted(want - names)
    assert list(REACHABLE) == ROW_IDS == list(doctor.ROW_IDS)


# git
def test_doctor_git_pass(env):
    tool(env[1], "git", "git version 2.24.0")
    at(run(env[0])["git"], "pass")


def test_doctor_git_fail(env):
    repo, bindir = env
    (bindir / "git").unlink()
    tool(bindir, "git", "git version 2.20.1")
    msg = at(run(repo)["git"], "fail")
    assert "2.20" in msg and "2.24" in msg
    (bindir / "git").unlink()
    at(run(repo)["git"], "fail")  # not installed


def test_doctor_git_unknown(env):
    tool(env[1], "git", "something odd")
    at(run(env[0])["git"], "unknown")
    tool(env[1], "git", "git version 2.40.0", code=1)
    at(run(env[0])["git"], "unknown")


# specify
def _ext(repo, version=__version__, floor=">=1.2.1", name="ghp"):
    d = repo / ".specify" / "extensions" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "extension.yml").write_text(f'schema_version: "1.0"\nextension:\n  id: "{name}"\n  version: "{version}"\nrequires:\n  speckit_version: "{floor}"\n')
    return d


def test_doctor_specify_pass(env):
    repo, bindir = env
    _ext(repo)
    tool(bindir, "specify", "specify 1.2.4.dev0")
    at(run(repo)["specify"], "pass")


def test_doctor_specify_warn(env):
    assert "specify" in at(run(env[0])["specify"], "warn")


def test_doctor_specify_fail(env):
    repo, bindir = env
    _ext(repo)
    tool(bindir, "specify", "specify 1.2.0")
    msg = at(run(repo)["specify"], "fail")
    assert "1.2.0" in msg and "1.2.1" in msg
    tool(bindir, "specify", "specify 0.1.0")
    _ext(repo, floor=">=0.0.1")  # a checkout cannot lower the built-in floor
    at(run(repo)["specify"], "fail")
    tool(bindir, "specify", "specify 1.2.1")
    _ext(repo, floor=">=2.0.0")  # but it can raise it
    at(run(repo)["specify"], "fail")


def test_doctor_specify_unknown(env):
    repo, bindir = env
    tool(bindir, "specify", "SECRETVALUE banner without a version")
    msg = at(run(repo)["specify"], "unknown")
    assert "SECRETVALUE" not in msg
    tool(bindir, "specify", "specify 1.2.4", code=1)
    at(run(repo)["specify"], "unknown")
    _ext(repo, floor="~1.2")
    tool(bindir, "specify", "specify 1.2.4")
    at(run(repo)["specify"], "unknown")  # a floor written in a form doctor cannot read


def test_specify_and_git_run_with_a_scrubbed_env_and_a_timeout(env, monkeypatch):
    repo, bindir = env
    seen = repo.parent / "seen.txt"
    tool(bindir, "specify", body=f"#!/bin/sh\nenv > '{seen}'\necho 'specify 1.2.4'\n")
    monkeypatch.setenv("MY_API_TOKEN", "xyz-secret")
    monkeypatch.setenv("GH_TOKEN", "CANARYVALUE")
    at(run(repo)["specify"], "pass")
    dump = seen.read_text()
    assert "CANARYVALUE" not in dump and "xyz-secret" not in dump and "GH_TOKEN" not in dump
    tool(bindir, "specify", body="#!/bin/sh\nsleep 5\n")
    monkeypatch.setattr(doctor, "TIMEOUT", 0.2)
    at(run(repo)["specify"], "unknown")


def test_specify_floor_is_the_plan_floor():
    assert doctor.SPECKIT_FLOOR == ">=1.2.1"  # X6


# shallow
def test_doctor_shallow_pass(env):
    commit(env[0], "one", {"a.txt": "1"})
    at(run(env[0])["shallow"], "pass")


def test_doctor_shallow_fail(env, tmp_path):
    repo, _ = env
    commit(repo, "one", {"a.txt": "1"})
    commit(repo, "two", {"a.txt": "2"})
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", "--depth", "1", f"file://{repo}", str(clone))
    (clone / "ghp.config.json").write_text(json.dumps(CFG))
    assert "git fetch --unshallow" in at(run(clone)["shallow"], "fail")


def test_doctor_shallow_unknown(env, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    at(run(plain)["shallow"], "unknown")


# gh (the CLI, version only)
def test_doctor_gh_pass(env):
    assert "2.40.1" in at(run(env[0])["gh"], "pass")


def test_doctor_gh_fail(env):
    repo, bindir = env
    (bindir / "gh").unlink()
    assert "install" in at(run(repo)["gh"], "fail")


def test_doctor_gh_unknown(env):
    repo, bindir = env
    tool(bindir, "gh", "SECRETVALUE no version")
    assert "SECRETVALUE" not in at(run(repo)["gh"], "unknown")
    tool(bindir, "gh", "gh version 2.40.1", code=1)
    at(run(repo)["gh"], "unknown")


def test_gh_row_runs_only_gh_version_never_gh_auth(env):
    repo, bindir = env
    log = repo.parent / "argv.txt"
    tool(bindir, "gh", body=f"#!/bin/sh\necho \"$@\" >> '{log}'\necho 'gh version 2.40.1'\n")
    run(repo)
    assert log.read_text().splitlines() == ["--version"]


# identity
def test_doctor_identity_pass(env):
    msg = at(run(env[0])["identity"], "pass")
    assert f"user id {ME}" in msg and "octocat" in msg
    odd = run(env[0], good_gh(viewer=Viewer(ME, "bad login\x1b[2J", ["repo", "project"])))["identity"]
    assert odd["state"] == "pass" and "\x1b" not in odd["message"] and "bad login" not in odd["message"]  # a login of a wrong shape is not shown


def test_doctor_identity_fail(env):
    for exc in (GhError("E_GH_HTTP", 401, "Bad credentials"), GhError("E_GH_HTTP", 403, "Forbidden")):
        msg = at(run(env[0], viewer_fails(exc))["identity"], "fail")
        assert "gh auth login" in msg and "Bad credentials" not in msg


def test_doctor_identity_unknown(env):
    repo, _ = env
    cases = [GhMissing(), RateLimited(30), GhError("E_GH_HTTP", 502, "SECRETVALUE"), GhError("E_GH_BAD_BODY", 200, "SECRETVALUE")]
    for exc in cases:
        r = run(repo, viewer_fails(exc))
        assert r["identity"]["state"] == "unknown" and r["scopes"]["state"] == "unknown", exc
        assert "SECRETVALUE" not in json.dumps(r)
    at(run(repo, good_gh(viewer=Viewer(0, "octocat", ["repo", "project"])))["identity"], "unknown")  # an id that is not a user id is never "pass"


# scopes
def test_doctor_scopes_pass(env):
    msg = at(run(env[0], good_gh(viewer=Viewer(ME, "octocat", ["read:org", "repo", "project"])))["scopes"], "pass")
    assert "repo" in msg and "project" in msg


def test_doctor_scopes_fail(env):
    test_scopes_missing_project_names_the_fix(env)
    msg = at(run(env[0], good_gh(viewer=Viewer(ME, "octocat", [])))["scopes"], "fail")  # header present, empty: a classic token with no scope
    assert "repo" in msg and "project" in msg


def test_doctor_scopes_unknown(env):
    msg = at(run(env[0], good_gh(viewer=Viewer(ME, "octocat", None)))["scopes"], "unknown")  # no X-OAuth-Scopes header (fine-grained token, app)
    assert "X-OAuth-Scopes" in msg


def test_scopes_missing_project_names_the_fix(env):
    r = run(env[0], good_gh(viewer=Viewer(ME, "octocat", ["repo"])))
    msg = at(r["scopes"], "fail")
    assert "project" in msg and "`gh auth refresh -s project`" in msg
    only_read = at(run(env[0], good_gh(viewer=Viewer(ME, "octocat", ["repo", "read:project"])))["scopes"], "fail")
    assert "`gh auth refresh -s project`" in only_read and "read:project" in only_read  # read:project cannot write
    both = at(run(env[0], good_gh(viewer=Viewer(ME, "octocat", ["gist"])))["scopes"], "fail")
    assert "`gh auth refresh -s repo,project`" in both


def test_doctor_scopes_warn(env):
    public = {"default_branch": "main", "private": False}
    msg = at(run(env[0], good_gh(viewer=Viewer(ME, "octocat", ["public_repo", "project"]), repo=public))["scopes"], "warn")
    assert "public_repo" in msg and "public repository" in msg and "private" in msg
    assert main(["doctor"], gh=good_gh(viewer=Viewer(ME, "octocat", ["public_repo", "project"]), repo=public)) == 0  # warn is not a fail


def test_public_repo_scope_is_enough_only_for_a_public_repository(env):
    repo, _ = env
    token = Viewer(ME, "octocat", ["public_repo", "project"])
    private = at(run(repo, good_gh(viewer=token, repo={"default_branch": "main", "private": True}))["scopes"], "fail")
    assert "private" in private and "`gh auth refresh -s repo`" in private
    for answer in ({"default_branch": "main"}, {"default_branch": "main", "private": "no"}, {"default_branch": "main", "private": None}):
        at(run(repo, good_gh(viewer=token, repo=answer))["scopes"], "unknown")  # visibility not known: never pass, never a guess
    at(run(repo, good_gh(viewer=token, repo=boom(GhError("E_GH_HTTP", 500, "x"))))["scopes"], "unknown")
    # project still missing: a fail whatever the visibility
    msg = at(run(repo, good_gh(viewer=Viewer(ME, "octocat", ["public_repo"]), repo={"default_branch": "main", "private": False}))["scopes"], "fail")
    assert "project" in msg and "`gh auth refresh -s repo,project`" in msg
    (repo / "ghp.config.json").unlink()
    at(run(repo, good_gh(viewer=token))["scopes"], "unknown")  # no config, no repository to look at


def test_the_scopes_hint_is_text_only_gh_auth_is_never_run(env):
    repo, bindir = env
    log = repo.parent / "argv.txt"
    tool(bindir, "gh", body=f"#!/bin/sh\necho \"$@\" >> '{log}'\necho 'gh version 2.40.1'\n")
    run(repo, good_gh(viewer=Viewer(ME, "octocat", ["repo"])))
    assert "auth" not in log.read_text()
    src = (SRC / "doctor.py").read_text()
    assert "gh\", \"auth" not in src and "'gh', 'auth" not in src and '"auth"' not in src


# config
def test_doctor_config_pass(env):
    at(run(env[0])["config"], "pass")


def test_doctor_config_fail(env):
    repo, _ = env
    (repo / "ghp.config.json").write_text(json.dumps({**CFG, "mode": "SECRETVALUE"}))
    r = run(repo)
    assert "'mode'" in at(r["config"], "fail") and "SECRETVALUE" not in json.dumps(r)
    (repo / "ghp.config.json").write_text(json.dumps({**CFG, "gate": {"block": ["SECRETVALUE"]}}))
    r = run(repo)
    assert "gate.block[0]" in r["config"]["message"] and "SECRETVALUE" not in json.dumps(r)
    (repo / "ghp.config.json").write_text('{"owner": "SECRETVALUE", oops')
    r = run(repo)
    assert r["config"]["state"] == "fail" and "SECRETVALUE" not in json.dumps(r)
    (repo / "ghp.config.json").write_text(json.dumps({**CFG, "strict": {"SECRETVALUE": 1}}))
    r = run(repo)
    assert "entry #1" in r["config"]["message"] and "SECRETVALUE" not in json.dumps(r)
    assert r["stack"]["state"] == "unknown"  # the mode is not known, so a stack problem is not called a warning
    (repo / "ghp.config.json").write_text(json.dumps({**CFG, "project_owner_type": "organization"}))
    assert "E_ORG_PROJECT_UNSUPPORTED" in run(repo)["config"]["message"]  # exit 3 of the verbs is a row here
    (repo / "ghp.config.json").write_text(json.dumps({**CFG, "approvers": ["octocat"]}))
    msg = run(repo)["config"]["message"]
    assert "numeric user id" in msg and "octocat" not in msg


def test_a_missing_config_is_a_fail_row_with_the_setup_fix(env):
    repo, _ = env
    (repo / "ghp.config.json").unlink()
    r = run(repo)
    assert "ghp setup" in at(r["config"], "fail")
    assert r["project-workflows"]["state"] == "unknown" and r["protection"]["state"] == "unknown"


# config-target
def test_doctor_config_target_pass(env):
    target(env[0])
    at(run(env[0])["config-target"], "pass")


def test_doctor_config_target_warn(env):
    repo, _ = env
    (repo / "a.txt").write_text("1")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-q", "-m", "x")  # the working-tree ghp.config.json is not committed
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    at(run(repo)["config-target"], "warn")  # the target has no ghp.config.json yet


def test_doctor_config_target_fail(env):
    repo, _ = env
    commit(repo, "t", {"ghp.config.json": "{ not json SECRETVALUE"})
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    (repo / "ghp.config.json").write_text(json.dumps(CFG))
    r = run(repo)
    assert r["config"]["state"] == "pass" and "SECRETVALUE" not in json.dumps(r)
    at(r["config-target"], "fail")
    target(repo, branch_prefix="bad..SECRETVALUE")
    r = run(repo)
    assert r["config-target"]["state"] == "fail" and "SECRETVALUE" not in json.dumps(r)


def test_doctor_config_target_unknown(env):
    assert "origin/HEAD" in at(run(env[0])["config-target"], "unknown")


# extension and workflow (versions must equal the CLI)
def _wf(repo, version=__version__):
    d = repo / ".specify" / "workflows" / "ghp-lifecycle"
    d.mkdir(parents=True, exist_ok=True)
    (d / "workflow.yml").write_text(f'schema_version: "1.0"\nworkflow:\n  id: "ghp-lifecycle"\n  version: "{version}"\n')
    return d


def test_doctor_extension_pass(env):
    _ext(env[0])
    at(run(env[0])["extension"], "pass")


def test_doctor_extension_warn(env):
    assert "not installed" in at(run(env[0])["extension"], "warn")


def test_doctor_extension_fail(env):
    _ext(env[0], version="0.0.1")
    msg = at(run(env[0])["extension"], "fail")
    assert "0.0.1" in msg and __version__ in msg


def test_doctor_extension_unknown(env):
    d = _ext(env[0])
    (d / "extension.yml").write_text("extension:\n  name: x\n")
    at(run(env[0])["extension"], "unknown")


def test_doctor_workflow_pass(env):
    _wf(env[0])
    at(run(env[0])["workflow"], "pass")


def test_doctor_workflow_warn(env):
    at(run(env[0])["workflow"], "warn")


def test_doctor_workflow_fail(env):
    _wf(env[0], version="9.9.9")
    msg = at(run(env[0])["workflow"], "fail")
    assert "9.9.9" in msg and __version__ in msg


def test_doctor_workflow_unknown(env, tmp_path):
    repo, _ = env
    real = tmp_path / "real.yml"
    real.write_text(f'workflow:\n  version: "{__version__}"\n')
    d = repo / ".specify" / "workflows" / "ghp-lifecycle"
    d.mkdir(parents=True)
    os.symlink(real, d / "workflow.yml")
    at(run(repo)["workflow"], "unknown")  # never pass through a symlink


# approvers
def test_doctor_approvers_pass(env):
    repo, _ = env
    assert at(run(repo)["approvers"], "pass") == "ok"  # no approvers, terminal mode
    target(repo, approval="comment", approvers=[OTHER])
    msg = at(run(repo)["approvers"], "pass")
    assert "separate human" in msg and str(OTHER) not in msg  # the one-human reminder, and no id echoed


def test_doctor_approvers_fail(env):
    repo, _ = env
    target(repo, approval="comment", approvers=[OTHER, ME])
    msg = at(run(repo)["approvers"], "fail")
    assert "identity of this token" in msg and str(ME) not in msg
    target(repo, approval="comment")
    assert "no approvers" in at(run(repo)["approvers"], "fail")  # nobody could approve
    (repo / "ghp.config.json").write_text(json.dumps({**CFG, "approvers": [OTHER]}))
    no_target(repo)
    msg = at(run(repo)["approvers"], "fail")  # a working-tree key with no target branch: the verbs refuse, so does doctor
    assert "origin/HEAD" in msg and str(OTHER) not in msg


def test_doctor_approvers_unknown(env):
    repo, _ = env
    target(repo, approvers=[OTHER])
    assert "identity" in at(run(repo, viewer_fails(GhError("E_GH_HTTP", 502, "x")))["approvers"], "unknown")
    at(run(repo, None, offline=True)["approvers"], "unknown")


def test_approver_equal_to_token_id_uses_the_exact_int_rule(env):
    repo, _ = env
    target(repo, approvers=[ME + 1])
    at(run(repo)["approvers"], "pass")
    target(repo, approvers=[ME])
    at(run(repo)["approvers"], "fail")
    seen = []
    from ghp import approvals
    real = approvals.approver_is_token
    approvals.approver_is_token = lambda a, t: seen.append((a, t)) or real(a, t)
    try:
        run(repo)
    finally:
        approvals.approver_is_token = real
    assert seen == [([ME], ME)]


def test_approvers_row_target_unreadable_is_a_fixed_refusal_text(env):
    repo, _ = env
    commit(repo, "t", {"ghp.config.json": "{ not json SECRETVALUE"})
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    (repo / "ghp.config.json").write_text(json.dumps(CFG))
    r = run(repo)
    assert r["approvers"]["state"] == "fail" and "SECRETVALUE" not in json.dumps(r) and "config-target" in r["approvers"]["message"]


def test_approvers_row_non_root_config_and_ci_without_a_target_are_fixed_texts(env, monkeypatch, tmp_path):
    repo, _ = env
    other = tmp_path / "elsewhere.json"
    other.write_text(json.dumps({**CFG, "approvers": [OTHER]}))
    monkeypatch.setenv("GHP_CONFIG", str(other))
    target(repo)
    msg = at(run(repo)["approvers"], "fail")
    assert "not the repo-root ghp.config.json" in msg and str(OTHER) not in msg
    monkeypatch.delenv("GHP_CONFIG")
    no_target(repo)
    monkeypatch.setenv("CI", "true")
    assert "E_TRUST_BASE_UNRESOLVED" in at(run(repo)["approvers"], "fail")


# writers
def test_doctor_writers_pass(env):
    at(run(env[0])["writers"], "pass")  # terminal mode: nobody approves by comment


def test_doctor_writers_warn(env):
    repo, _ = env
    target(repo, approval="comment", approvers=[OTHER])
    msg = at(run(repo)["writers"], "warn")
    assert "any writer" in msg and "edit or delete any comment" in msg
    (repo / "ghp.config.json").write_text(json.dumps({**CFG, "approval": "comment"}))
    no_target(repo)
    at(run(repo)["writers"], "warn")  # approval mode cannot be read: judged as comment mode, never pass


# project-workflows
def test_doctor_project_workflows_pass(env):
    assert "no workflow is enabled" in at(run(env[0])["project-workflows"], "pass")
    (env[0] / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo"}))
    assert "no Project" in at(run(env[0])["project-workflows"], "pass")


def test_doctor_project_workflows_warn(env):
    gh = good_gh(workflows=flows(enabled=("Item added to project", "Pull request merged", "Mystery\x1b[2Jflow"), disabled=()))
    msg = at(run(env[0], gh)["project-workflows"], "warn")
    assert "Item added to project" in msg and "Pull request merged" in msg and "\x1b" not in msg
    assert "gate bypass" not in msg  # neither of those two is a bypass


def test_doctor_project_workflows_fail(env):
    gh = good_gh(workflows=boom(__import__("ghp.projects", fromlist=["x"]).ProjectError("E_PROJECT_NOT_FOUND", "no user-owned Project found")))
    assert "project_number" in at(run(env[0], gh)["project-workflows"], "fail")


def test_doctor_project_workflows_unknown(env):
    gh = good_gh(workflows=boom(ScopeMissing(["read:project"])))
    assert "scopes" in at(run(env[0], gh)["project-workflows"], "unknown")
    for bad in ({"user": {"projectV2": {"id": "x", "workflows": {"pageInfo": {"hasNextPage": False}, "nodes": [{"name": "A", "enabled": "yes", "number": 1}]}}}},
                {"unexpected": 1}):
        at(run(env[0], good_gh(workflows=bad))["project-workflows"], "unknown")  # the shape after a UI toggle is unverified: never "pass" on a shape it does not know
    at(run(env[0], None, offline=True)["project-workflows"], "unknown")


def test_a_project_github_cannot_resolve_is_said_plainly_not_as_an_unexpected_http_200(env):
    e = GhError("E_GH_GRAPHQL", 200, "Could not resolve to a ProjectV2 with the number 3.")
    e.graphql_errors = [("NOT_FOUND", ("user", "projectV2"))]
    msg = at(run(env[0], good_gh(workflows=boom(e)))["project-workflows"], "unknown")
    assert "HTTP 200" not in msg and "unexpected" not in msg and "no such Project" in msg and "Could not" not in msg  # fixed text, not GitHub's words
    other = GhError("E_GH_GRAPHQL", 200, "something else")
    other.graphql_errors = [("FORBIDDEN", None)]
    assert "HTTP 200" not in at(run(env[0], good_gh(workflows=boom(other)))["project-workflows"], "unknown")


def test_workflow_row_flags_the_two_gate_bypass_workflows(env):
    gh = good_gh(workflows=flows(enabled=ALL_FLOWS, disabled=()))
    msg = at(run(env[0], gh)["project-workflows"], "warn")
    assert "Auto-close issue" in msg and "Item closed" in msg and "gate bypass" in msg
    assert "closes the issue without the gate" in msg and "drag" in msg
    # exactly those two are named as bypasses: the text before "other" names only them
    bypass_part = msg.split("Other enabled")[0]
    assert [n for n in ALL_FLOWS if n in bypass_part] == ["Auto-close issue", "Item closed"]
    only_closed = at(run(env[0], good_gh(workflows=flows(enabled=("Item closed",))))["project-workflows"], "warn")
    assert "Item closed" in only_closed and "Auto-close issue" not in only_closed
    # a lookalike name is not one of the two
    lookalike = at(run(env[0], good_gh(workflows=flows(enabled=("Auto-close issues", "item closed"))))["project-workflows"], "warn")
    assert "gate bypass" not in lookalike
    # a disabled bypass workflow is no warning
    at(run(env[0], good_gh(workflows=flows(enabled=(), disabled=ALL_FLOWS)))["project-workflows"], "pass")


# protection
def test_doctor_protection_pass(env):
    assert "protection" in at(run(env[0])["protection"], "pass")


def test_doctor_protection_warn(env):
    test_protection_row_warns_on_free_private_repo(env)
    gh = good_gh(protection=boom(GhError("E_GH_HTTP", 404, "Branch not protected")))
    assert "not protected" in at(run(env[0], gh)["protection"], "warn")


def test_doctor_protection_fail(env):
    gh = good_gh(repo=boom(GhError("E_GH_HTTP", 404, "Not Found")))
    assert "owner and repo" in at(run(env[0], gh)["protection"], "fail")


def test_doctor_protection_unknown(env):
    repo, _ = env
    for gh in (good_gh(protection=boom(GhError("E_GH_HTTP", 403, "Resource not accessible SECRETVALUE"))),
               good_gh(protection=boom(GhError("E_GH_HTTP", 500, "x"))),
               good_gh(repo={"private": True}), good_gh(repo={"default_branch": "bad\x1b[2J", "private": True}),
               good_gh(repo={"default_branch": "..", "private": True}), good_gh(repo=[])):
        r = run(repo, gh)
        assert r["protection"]["state"] == "unknown" and "SECRETVALUE" not in json.dumps(r), r["protection"]
        assert "unexpectedly" not in r["protection"]["message"], r["protection"]  # a refused branch name, not a crash of the request builder
    at(run(repo, None, offline=True)["protection"], "unknown")


def test_protection_row_warns_on_free_private_repo(env):
    gh = good_gh(protection=boom(GhError("E_GH_HTTP", 403, UPGRADE)))
    msg = at(run(env[0], gh)["protection"], "warn")
    assert "free" in msg.lower() and "private" in msg and UPGRADE not in msg
    assert gh.calls[-1][2] == PROT_PATH  # the protection read of the default branch GitHub named


def test_protection_branch_name_goes_into_the_path_quoted(env):
    gh = good_gh(repo={"default_branch": "release/1.0\u00e9", "private": False})
    gh.on_rest("GET", "repos/octo/demo/branches/release/1.0%C3%A9/protection", {"url": "u"})
    at(run(env[0], gh)["protection"], "pass")


# overlap
def _installed(repo, *names):
    reg = repo / ".specify" / "extensions"
    reg.mkdir(parents=True, exist_ok=True)
    (reg / ".registry").write_text(json.dumps({"extensions": {n: {"version": "0.1.0", "enabled": True, "registered_commands": {}} for n in names}}))


def test_doctor_overlap_pass(env):
    _installed(env[0], "contoso-kit")
    at(run(env[0])["overlap"], "pass")
    shutil.rmtree(env[0] / ".specify")
    at(run(env[0])["overlap"], "pass")


def test_doctor_overlap_warn(env):
    for names, said in ((("project",), "project"), (("tasks-to-project",), "tasks-to-project"), (("project", "tasks-to-project", "ghp"), "project, tasks-to-project")):
        _installed(env[0], *names)
        msg = at(run(env[0])["overlap"], "warn")
        assert said in msg
    _installed(env[0], "project-x", "myproject")
    at(run(env[0])["overlap"], "pass")  # exact ids only


@pytest.mark.parametrize("ext_id", ["github", "github-issues", "maqa-github-projects", "tasks-to-project"])
def test_doctor_overlap_warn_for_each_catalog_github_extension(env, ext_id):
    """T59: each catalog extension that also writes GitHub issues or Project items, installed alone, is a warn that names it."""
    _installed(env[0], "contoso-kit", ext_id)
    msg = at(run(env[0])["overlap"], "warn")
    assert ext_id in msg and "contoso-kit" not in msg


def test_doctor_overlap_unknown(env):
    (env[0] / ".specify" / "extensions").mkdir(parents=True)
    (env[0] / ".specify" / "extensions" / ".registry").write_text("{ broken")
    at(run(env[0])["overlap"], "unknown")  # a registry that cannot be read is never "pass"


# stack and kits
def test_doctor_stack_pass(env):
    (env[0] / "ghp.stack.json").write_text(json.dumps(approve(profile())))
    at(run(env[0])["stack"], "pass")


def test_doctor_stack_warn(env):
    assert at(run(env[0])["stack"], "warn") == "no stack profile yet (optional): run `ghp stack detect`"


def _strict(repo):
    (repo / "ghp.config.json").write_text(json.dumps({**CFG, "mode": "strict", "strict": {"test_command": "PYTHONPATH=src python3 -m pytest -q"}}))


def test_doctor_stack_fail(env):
    repo, _ = env
    _strict(repo)
    (repo / "ghp.stack.json").write_text(json.dumps(profile()))  # a draft
    assert "ghp stack show" in at(run(repo)["stack"], "fail")
    (repo / "ghp.stack.json").unlink()
    assert at(run(repo)["stack"], "fail") == "no stack profile yet: run `ghp stack detect`"


def test_doctor_stack_unknown(env, monkeypatch):
    repo, _ = env
    (repo / "ghp.config.json").write_text("{ broken")
    at(run(repo)["stack"], "unknown")
    (repo / "ghp.config.json").write_text(json.dumps(CFG))
    monkeypatch.setattr("ghp.stack.show", lambda *a: (_ for _ in ()).throw(RuntimeError("SECRETVALUE")))
    r = run(repo)
    assert "RuntimeError" in at(r["stack"], "unknown") and "SECRETVALUE" not in json.dumps(r)  # the class, never the message


def test_doctor_kits_pass(env):
    at(run(env[0])["kits"], "pass")


def _unpinned_kit(repo):
    reg = repo / ".specify" / "extensions"
    (reg / "contoso-kit").mkdir(parents=True)
    (reg / "contoso-kit" / "extension.yml").write_text("x")
    _installed(repo, "contoso-kit")


def test_doctor_kits_warn(env):
    repo, _ = env
    _unpinned_kit(repo)
    msg = at(run(repo)["kits"], "warn")
    assert "ghp kits verify" in msg and "contoso-kit" not in msg
    (repo / ".specify" / "extensions" / ".registry").write_text("{ broken")
    at(run(repo)["kits"], "warn")  # a registry that cannot be trusted is a finding, never pass


def test_doctor_kits_fail(env):
    repo, _ = env
    _strict(repo)
    (repo / "ghp.stack.json").write_text(json.dumps(profile()))
    _unpinned_kit(repo)
    assert "ghp kits verify" in at(run(repo)["kits"], "fail")


def test_stack_and_kits_reuse_the_existing_functions(env, monkeypatch):
    repo, _ = env
    seen = []
    monkeypatch.setattr("ghp.stack.show", lambda *a: seen.append(("stack", a)) or ({"status": "approved", "notes": []}, None, 0))
    monkeypatch.setattr("ghp.kits.verify", lambda root, lock: seen.append(("kits", root)) or {"findings": []})
    r = run(repo)
    assert r["stack"]["state"] == "pass" and r["kits"]["state"] == "pass" and [s[0] for s in seen] == ["stack", "kits"]


# ---- offline ----
def test_offline_marks_every_github_row_unknown_offline_and_calls_nothing(env):
    repo, _ = env
    gh = good_gh()
    r = rows(doctor.run(str(repo), gh=gh, offline=True))
    for row in ("identity", "scopes", "project-workflows", "protection"):
        assert (r[row]["state"], r[row]["message"]) == ("unknown", "unknown (offline)"), row
    assert gh.calls == []
    assert r["git"]["state"] in ("pass", "fail") and r["config"]["state"] == "pass"  # local rows are still judged
    assert rows(doctor.run(str(repo), gh=None))["identity"]["message"] == "unknown (offline)"  # no Gh at all is the same


def test_no_answer_from_github_is_offline_and_stops_further_github_calls(env):
    repo, _ = env
    for exc in (GhError("E_GH_NO_RESPONSE", None, "dial tcp: lookup api.github.com SECRETVALUE"), GhError("E_GH_TIMEOUT", None, "no answer")):
        gh = viewer_fails(exc)
        r = run(repo, gh)
        assert r["identity"]["state"] == "unknown" and r["identity"]["message"].startswith("unknown (offline)")
        for row in ("scopes", "project-workflows", "protection"):
            assert r[row]["message"] == "unknown (offline)", row
        assert gh.calls == [] and "SECRETVALUE" not in json.dumps(r)
    gh = good_gh(protection=boom(GhError("E_GH_NO_RESPONSE", None, "x")))
    r = run(repo, gh)
    assert r["protection"]["message"].startswith("unknown (offline)")


# ---- a signed-out gh is a fail, a missing network is not (through the real transport and a fake runner) ----
SIGN_IN = (4, "", "To get started with GitHub CLI, please run:  gh auth login\nAlternatively, populate the GH_TOKEN environment variable with a GitHub API authentication token.")


def test_a_signed_out_gh_is_an_identity_fail_with_the_sign_in_hint_and_exit_1(env, capsys):
    repo, _ = env
    runner = FakeRunner(SIGN_IN)
    r = rows(doctor.run(str(repo), gh=Gh(runner=runner)))
    msg = at(r["identity"], "fail")
    assert "`gh auth login`" in msg and "never runs it" in msg and "offline" not in msg and "GH_TOKEN" not in msg  # stderr is never echoed
    for row in ("scopes", "project-workflows", "protection"):
        assert (r[row]["state"], r[row]["message"]) == ("unknown", doctor.SIGNED_OUT), row
    assert len(runner.calls) == 1 and runner.calls[0]["argv"][:5] == ["gh", "api", "-X", "POST", "graphql"]  # one spawn (the viewer read): the other rows made none
    for text in ("You are not logged into any GitHub hosts. To log in, run: gh auth login", "PLEASE RUN  GH AUTH LOGIN"):
        assert rows(doctor.run(str(repo), gh=Gh(runner=FakeRunner((4, "", text)))))["identity"]["state"] == "fail"
    assert main(["doctor"], gh=Gh(runner=FakeRunner(SIGN_IN))) == 1
    assert "fail" in capsys.readouterr().out
    approvers = rows(doctor.run(str(repo), gh=Gh(runner=FakeRunner(SIGN_IN))))["approvers"]
    assert approvers["state"] == "pass"  # nothing to compare when no approver is configured


def test_a_signed_out_gh_makes_the_approvers_row_say_so_not_offline(env):
    repo, _ = env
    target(repo, approvers=[OTHER])
    r = rows(doctor.run(str(repo), gh=Gh(runner=FakeRunner(SIGN_IN))))
    assert (r["approvers"]["state"], r["approvers"]["message"]) == ("unknown", doctor.SIGNED_OUT)


def test_plain_no_network_stays_offline_unknown_and_exit_0(env):
    repo, _ = env
    runner = FakeRunner((1, "", "error connecting to api.github.com\ncheck your internet connection or https://githubstatus.com"))
    r = rows(doctor.run(str(repo), gh=Gh(runner=runner)))
    assert r["identity"]["state"] == "unknown" and r["identity"]["message"].startswith("unknown (offline)")
    assert r["protection"]["message"] == "unknown (offline)" and len(runner.calls) == 1
    assert main(["doctor"], gh=Gh(runner=FakeRunner((1, "", "error connecting to api.github.com")))) == 0


def test_a_sign_out_found_by_a_later_call_is_not_called_offline(env):
    repo, _ = env
    gh = good_gh(repo=boom(GhError("E_GH_NO_RESPONSE", None, "gh auth login")))
    r = run(repo, gh)
    assert r["protection"]["message"] == doctor.SIGNED_OUT and r["project-workflows"]["message"] == doctor.SIGNED_OUT
    assert r["identity"]["state"] == "pass"


# ---- the result, the text, the exit code ----
def test_result_shape_and_rendering(env):
    repo, _ = env
    result = doctor.run(str(repo), gh=good_gh())
    assert list(result) == ["rows"] and [r["id"] for r in result["rows"]] == ROW_IDS
    assert all(list(r) == ["id", "state", "message"] and r["state"] in ("pass", "warn", "fail", "unknown") and isinstance(r["message"], str)
               for r in result["rows"])
    lines = doctor.render(result).splitlines()
    assert len(lines) == len(ROW_IDS) and all(re.match(r"(pass|warn|fail|unknown) +\S+", ln) for ln in lines)
    assert doctor.failed({"rows": [{"state": "warn"}, {"state": "unknown"}]}) is False
    assert doctor.failed({"rows": [{"state": "fail"}]}) is True


def test_workflow_names_are_escaped_and_capped_where_they_are_listed():
    shown = doctor._names(["a\x1b[2Jb", "x" * 500] + [f"n{i}" for i in range(20)])
    assert "\x1b" not in shown and "x" * 61 not in shown and "and 12 more" in shown


def test_the_final_pass_escapes_any_message(env, monkeypatch):
    monkeypatch.setattr(doctor, "_git_row", lambda repo: ("pass", "a\x1b[2Jb\u202e\r\nfail"))
    text = doctor.render(doctor.run(str(env[0]), gh=good_gh()))
    assert "\x1b" not in text and "\u202e" not in text and text.count("\n") == len(ROW_IDS) - 1


def test_every_message_is_one_line_of_printable_text(env):
    repo, _ = env
    gh = good_gh(workflows=flows(enabled=("Evil\r\nfail  fake  row\x1b[2J‮",), disabled=()), viewer=Viewer(ME, "octocat", ["repo"]))
    text = doctor.render(doctor.run(str(repo), gh=gh))
    assert text.count("\n") == len(ROW_IDS) - 1 and "\x1b" not in text and "‮" not in text and "\r" not in text


def test_cli_text_json_and_exit_codes(env, capsys):
    repo, _ = env
    assert main(["doctor"], gh=good_gh()) == 0  # warn and unknown rows alone are exit 0
    out = capsys.readouterr().out
    assert "warn" in out and "project-workflows" in out and not out.lstrip().startswith("{")
    assert main(["doctor", "--json"], gh=good_gh()) == 0
    data = json.loads(capsys.readouterr().out)
    assert list(data) == ["rows"] and [r["id"] for r in data["rows"]] == ROW_IDS
    (repo / "ghp.config.json").write_text(json.dumps({**CFG, "mode": "nope"}))
    assert main(["doctor"], gh=good_gh()) == 1  # a fail row (invalid config) is exit 1, not a crash and not 3
    assert "fail" in capsys.readouterr().out
    assert main(["doctor", "--offline"], gh=FakeGh()) == 1


def test_offline_flag_makes_no_github_call(env, capsys):
    gh = good_gh()
    assert main(["doctor", "--offline", "--json"], gh=gh) == 0
    assert gh.calls == [] and json.loads(capsys.readouterr().out)["rows"][4]["message"] == "unknown (offline)"


def test_doctor_with_no_gh_double_that_answers_is_reported_by_class(env):
    r = rows(doctor.run(str(env[0]), gh=FakeGh()))  # FakeGh() has no viewer: an AssertionError inside a row is a row, not a crash
    assert r["identity"]["state"] == "unknown" and "AssertionError" in r["identity"]["message"]


def test_crash_in_any_row_names_the_class_never_the_message(env, monkeypatch):
    repo, _ = env
    monkeypatch.setattr("ghp.doctor._load_config", lambda repo: (_ for _ in ()).throw(OSError("SECRETVALUE")))
    r = run(repo)
    assert r["config"]["state"] == "unknown" and "OSError" in r["config"]["message"] and "SECRETVALUE" not in json.dumps(r)
    monkeypatch.setattr("ghp.kits.installed", lambda root: (_ for _ in ()).throw(RuntimeError("SECRETVALUE")))
    r = run(repo)
    assert r["overlap"]["state"] == "unknown" and "RuntimeError" in r["overlap"]["message"] and "SECRETVALUE" not in json.dumps(r)


# ---- read only, no token ----
def test_doctor_is_read_only_every_call_is_a_get_or_a_query_and_gh_is_never_armed(env):
    repo, _ = env
    gh = good_gh()
    _installed(repo)

    def snapshot():
        return sorted((str(p), p.stat().st_mtime_ns) for p in repo.rglob("*") if p.is_file() and ".git" not in p.relative_to(repo).parts)
    before = snapshot()
    result = doctor.run(str(repo), gh=gh)
    assert snapshot() == before and not gh.armed and not (repo / ".ghp").exists()
    assert gh.calls and all((c[0] == "rest" and c[1] == "GET") or (c[0] == "graphql" and not is_graphql_write(c[1])) for c in gh.calls), gh.calls
    assert result["rows"]
    assert git(repo, "status", "--porcelain").splitlines() == ["?? .specify/", "?? ghp.config.json"]


def test_doctor_never_reads_or_prints_a_token(env, monkeypatch, capsys):
    repo, _ = env
    canary = "gh" + "p_" + "A" * 36
    for var in ("GH_TOKEN", "GITHUB_TOKEN", "GHP_TOKEN"):
        monkeypatch.setenv(var, canary)
    tool(env[1], "gh", body=f"#!/bin/sh\nif [ -n \"$GH_TOKEN$GITHUB_TOKEN$GHP_TOKEN\" ]; then echo 'gh version 9.9.9 LEAKED'; else echo 'gh version 2.40.1'; fi\n")
    assert main(["doctor", "--json"], gh=good_gh()) == 0
    out = capsys.readouterr()
    assert canary not in out.out + out.err and "LEAKED" not in out.out and "2.40.1" in out.out  # the child never saw the variables
    src = (SRC / "doctor.py").read_text()
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        assert name not in src
    assert ".arm(" not in src and "arm_or_refuse" not in src and "mutation" not in src and "add_write_flags" not in src
    assert "os.environ[" not in src and "os.getenv" not in src and re.search(r"environ\.get\((?!\"HOME\")", src) is None


def test_doctor_output_holds_no_identity_beyond_the_viewers_id_and_login(env):
    repo, _ = env
    target(repo, approval="comment", approvers=[OTHER])
    text = json.dumps(doctor.run(str(repo), gh=good_gh()))
    assert str(OTHER) not in text and "example.invalid" not in text


# ---- linear regexes ----
@pytest.mark.parametrize("size", [30_000, 300_000])
def test_version_and_manifest_scans_are_linear(env, size):
    repo, bindir = env
    started = time.monotonic()
    for text in ("a" * size, " " * size + "x", "\n" * size, "git version " + "1." * size, "specify " + " " * size + "x", "gh version " + "9" * size):
        for rx in (doctor._GIT, doctor._SPECIFY, doctor._GH):
            rx.search(text)
    d = repo / ".specify" / "extensions" / "ghp"
    d.mkdir(parents=True)
    for body in (f"requires:\n  speckit_version: a{' ' * size}x'y\n", "extension:\n" + "  version: " + " " * size + "\n", "x" * size + ":" + " " * size + "z\n",
                 "extension:\n  version:" + " \t" * (size // 2) + "'\n"):
        (d / "extension.yml").write_text(body)
        doctor._manifest(repo, doctor.EXT_PATH, "requires", "speckit_version")
        doctor._manifest(repo, doctor.EXT_PATH, "extension", "version")
    assert time.monotonic() - started < 5


def test_the_manifest_reader_still_reads_the_documented_forms(env):
    repo, _ = env
    d = repo / ".specify" / "extensions" / "ghp"
    d.mkdir(parents=True)
    for line, want in (('  version: "0.1.0"', "0.1.0"), ("  version: '0.1.0' # note", "0.1.0"), ("  version: 0.1.0", "0.1.0"), ('  version: "0.1.0', "0.1.0")):
        (d / "extension.yml").write_text("extension:\n" + line + "\n")
        assert doctor._manifest(repo, doctor.EXT_PATH, "extension", "version") == ("ok", want), line
    (d / "extension.yml").write_text("other:\n  version: 1.0.0\nextension:\n  name: x\n")
    assert doctor._manifest(repo, doctor.EXT_PATH, "extension", "version") == ("unreadable", None)


def test_the_verb_is_registered_with_its_flags(capsys):
    import ghp.__main__ as cli
    assert "doctor" in cli.REGISTRY
    with pytest.raises(SystemExit):
        main(["doctor", "--help"])
    out = capsys.readouterr().out
    assert "--json" in out and "--offline" in out


# ---- T31a-AC3: the identity and scopes rows through the real transport where REST `GET /user` would be 403 (the Actions token) ----
def test_identity_and_scopes_rows_work_where_rest_user_is_403(env):
    runner = bot_runner(scopes="repo, project")
    r = rows(doctor.run(str(env[0]), gh=Gh(runner=runner)))
    assert "signed in as user id 41898282" in at(r["identity"], "pass")
    at(r["scopes"], "pass")
    assert "user" not in runner.paths and "graphql" in runner.paths
    no_header = rows(doctor.run(str(env[0]), gh=Gh(runner=bot_runner())))  # the Actions token sends no X-OAuth-Scopes
    at(no_header["identity"], "pass")
    assert "X-OAuth-Scopes" in at(no_header["scopes"], "unknown")
