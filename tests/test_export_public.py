"""T46: `scripts/export_public.py <dir>` writes HEAD's tracked files minus the internal list into a new folder with one fresh commit (AC1),
which scrub_check passes (AC3). It never adds a remote, never pushes and never calls gh. The export runs once per module; when this suite
itself runs inside an export, the same checks hold for the export of the export."""
import fnmatch
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "export_public.py"
INTERNAL = ["SPIKE-*.md", "spike/", "docs/plans/"]  # the list AC1 names; the script must hold the same
NEVER = {".superpowers", ".scrub-denylist", ".venv", "__pycache__", "dod", ".pytest_cache", ".ado", ".ghp"}
TRAILER = "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
FICTIONAL = ("Fictional Owner", "1+fictional@users.noreply.github.com")
LOCAL_ONLY = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd, *args, env=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env=env).stdout


def internal(rel):
    return any(rel.startswith(p) if p.endswith("/") else ("/" not in rel and fnmatch.fnmatch(rel, p)) for p in INTERNAL)


def identity():
    """The source repository's own identity (never the global one); hosted CI checkouts have none, so a fictional one is injected."""
    env = {**os.environ, **LOCAL_ONLY}
    got = [subprocess.run(["git", "config", "--get", k], cwd=ROOT, capture_output=True, text=True, env=env).stdout.strip()
           for k in ("user.name", "user.email")]
    if all(got):
        return tuple(got), env
    return FICTIONAL, {**env, "GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "user.name", "GIT_CONFIG_VALUE_0": FICTIONAL[0],
                       "GIT_CONFIG_KEY_1": "user.email", "GIT_CONFIG_VALUE_1": FICTIONAL[1]}


def run(out, env=None):
    return subprocess.run([sys.executable, str(SCRIPT), str(out)], cwd=ROOT, capture_output=True, text=True, env=env or identity()[1])


@pytest.fixture(scope="module")
def export(tmp_path_factory):
    out = tmp_path_factory.mktemp("export") / "public"
    p = run(out)
    assert p.returncode == 0, p.stderr
    return out


def test_the_export_holds_head_minus_the_internal_list(export):
    head = set(git(ROOT, "ls-tree", "-r", "--name-only", "HEAD").splitlines())
    want = {f for f in head if not internal(f)}
    assert set(git(export, "ls-files").splitlines()) == want
    on_disk = {p.relative_to(export).as_posix() for p in export.rglob("*") if p.is_file() and ".git" not in p.relative_to(export).parts}
    assert on_disk == want
    for kept in ("README.md", "docs/evidence/CI-RUN.md", "docs/evidence/T31.md", "scripts/scrub_check.py"):
        assert (export / kept).is_file(), kept
    assert not any(internal(f) for f in on_disk)


def test_the_internal_list_matches_the_scripts_own_list():
    text = SCRIPT.read_text(encoding="utf-8")
    assert re.search(r"INTERNAL = \(%s\)" % re.escape(", ".join('"%s"' % p for p in INTERNAL)), text)


def test_the_export_holds_no_private_folder(export):
    names = {part for p in export.rglob("*") if p.relative_to(export).parts[0] != ".git" for part in p.relative_to(export).parts}
    assert not names & NEVER, names & NEVER
    assert not (export / "spike").exists() and not (export / "docs" / "plans").exists()  # not even an empty folder


def test_one_commit_by_the_repositorys_own_identity_and_no_remote(export):
    (name, email), _ = identity()
    log = git(export, "log", "--format=%an|%ae|%cn|%ce|%B%x00", env={**os.environ, **LOCAL_ONLY}).split("\0")
    commits = [c.strip() for c in log if c.strip()]
    assert len(commits) == 1
    who, _, body = commits[0].partition("|%s|%s|" % (name, email))
    assert who == "%s|%s" % (name, email), commits[0].split("\n")[0]
    assert body.splitlines()[0] == "Initial public release candidate" and body.splitlines()[-1] == TRAILER
    assert git(export, "remote").strip() == ""
    assert "[remote" not in (export / ".git" / "config").read_text(encoding="utf-8")
    assert git(export, "status", "--porcelain").strip() == ""


def test_the_script_never_pushes_never_adds_a_remote_and_never_calls_gh():
    text = SCRIPT.read_text(encoding="utf-8")
    assert not re.search(r"""["'](push|remote|gh|fetch|clone)["']""", text)
    assert not re.search(r"^\s*(import|from) (urllib|http|socket)", text, re.M)


def test_it_refuses_a_folder_that_is_not_empty(tmp_path):
    (tmp_path / "keep.txt").write_text("x")
    p = run(tmp_path)
    assert p.returncode != 0 and "not empty" in p.stderr
    assert sorted(x.name for x in tmp_path.iterdir()) == ["keep.txt"]


def test_it_refuses_a_folder_inside_the_repository():
    out = ROOT / "build" / "t46-export-must-be-refused"
    try:
        p = run(out)
        assert p.returncode != 0 and "inside" in p.stderr and not out.exists()
    finally:
        shutil.rmtree(out, ignore_errors=True)  # a broken script must not leave a nested repository behind


def test_it_refuses_without_the_repositorys_own_identity(tmp_path):
    """Command-scope config wins over the repository's own, so an empty name stands in for a checkout without one."""
    env = {**os.environ, "GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "user.name", "GIT_CONFIG_VALUE_0": "",
           "GIT_CONFIG_KEY_1": "user.email", "GIT_CONFIG_VALUE_1": ""}
    p = run(tmp_path / "out", env=env)
    assert p.returncode != 0 and "user.name" in p.stderr and not (tmp_path / "out").exists()


def test_scrub_check_passes_in_the_export(export):
    """AC3. Run as the owner would, from the export's top folder; the owner's list is passed through when this shell has one."""
    env = {k: v for k, v in os.environ.items()}
    if not env.get("SCRUB_DENYLIST") and (ROOT / ".scrub-denylist").is_file():
        env["SCRUB_DENYLIST"] = (ROOT / ".scrub-denylist").read_text(encoding="utf-8")
    p = subprocess.run([sys.executable, str(export / "scripts" / "scrub_check.py"), "--history"], cwd=export, capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stdout


def test_no_exported_page_links_a_file_that_is_not_exported(export):
    for f in export.rglob("*.md"):
        if ".git" in f.relative_to(export).parts:
            continue
        for target in re.findall(r"\]\(([^)\s]+)\)", f.read_text(encoding="utf-8")):
            if re.match(r"^(\w+:|#)", target) or re.search(r"[<{$]", target):
                continue
            assert (f.parent / target.split("#")[0]).exists(), "%s links %s" % (f.relative_to(export), target)


def scratch_repo(tmp_path, local_identity):
    """A one-commit repository holding a copy of the script, with a global identity that must never be used."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "export_public.py").write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    (repo / ".gitignore").write_text("build/\n", encoding="utf-8")
    (repo / "build").mkdir()
    (repo / "build" / "kept.txt").write_text("tracked although ignored\n", encoding="utf-8")
    home = tmp_path / "gitconfig"
    home.write_text("[user]\n\tname = Global Other\n\temail = other@example.com\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_GLOBAL": str(home), "GIT_CONFIG_NOSYSTEM": "1"})
    git(repo, "init", "-q", env=env)
    if local_identity:
        git(repo, "config", "user.name", FICTIONAL[0], env=env)
        git(repo, "config", "user.email", FICTIONAL[1], env=env)
    git(repo, "add", "-A", "-f", env=env)
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "c", env=env)
    run = lambda out: subprocess.run([sys.executable, str(repo / "scripts" / "export_public.py"), str(out)], cwd=repo, capture_output=True, text=True, env=env)
    return run, env


def test_it_never_takes_the_global_identity(tmp_path):
    """A checkout with no identity of its own is refused even when the global git config has one (the global name may be another account's)."""
    run_it, _ = scratch_repo(tmp_path, local_identity=False)
    p = run_it(tmp_path / "out")
    assert p.returncode == 2 and "user.name" in p.stderr and not (tmp_path / "out").exists()


def test_a_tracked_file_that_gitignore_matches_is_still_exported(tmp_path):
    run_it, env = scratch_repo(tmp_path, local_identity=True)
    p = run_it(tmp_path / "out")
    assert p.returncode == 0, p.stderr
    assert git(tmp_path / "out", "ls-files", env=env).split() == [".gitignore", "build/kept.txt", "scripts/export_public.py"]
    assert git(tmp_path / "out", "log", "--format=%an <%ae>", env=env).strip() == "%s <%s>" % FICTIONAL


def test_the_plan_readers_run_in_the_full_repository():
    """The skip in the plan readers is for the export only: where the plan is tracked, reading it must not skip."""
    if "docs/plans/PLAN-0.1.md" not in git(ROOT, "ls-files").splitlines():
        pytest.skip("public export: the plan is not here")
    from test_x8_verified import plan
    try:
        text = plan()
    except pytest.skip.Exception:
        pytest.fail("the plan readers skipped although the plan is tracked here")
    assert "## 9. Discovered work" in text
