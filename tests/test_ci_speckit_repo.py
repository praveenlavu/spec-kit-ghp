# T56: the CI kit rules and trust notes on a REAL Spec Kit repository (`specify init` + `specify extension add --dev`, committed as a user would),
# ported from spec-kit-ado (rename table). Hand-built fixtures hold only `.specify/extensions/<kit>/`; a real repository also tracks the rest of
# `.specify/`, the agent files and the toolkit's own `ghp` extension, and none of that may read as a replaced `.specify` or an unpinned kit.
# Opt-in like tests/test_speckit_contract.py: skipped unless SPECKIT_CONTRACT=1; opted in with no `specify` is a failure, never a vacuous green.
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from ghp import __main__ as m
from ghp import kits, strict
from ghp.__main__ import main
from ghp.config import Config
from conftest import approve, commit, git, profile
from test_strict import STACK_CMD, _hub, _project
from test_strict_wire import spy_config

SPECKIT_KIT = "contoso-kit"
KIT = f".specify/extensions/{SPECKIT_KIT}"
NOTE = "trust-introducing PR: review ghp.stack.json as code"
KNOTE = "trust-introducing PR: review ghp.kits.lock.json as code"
REPLACED = "is a symlink, a submodule or a file at the head commit"
ADVISORY = {"owner": "octo", "repo": "demo"}
ROOT = Path(__file__).resolve().parent.parent
_EXT_PARTS = ("extension.yml", "commands", "rules", "templates", "README.md", "CHANGELOG.md", "LICENSE", "AGENTS.md", "ghp.config.template.json")
_KIT_MANIFEST = f"""schema_version: "1.0"

extension:
  id: "{SPECKIT_KIT}"
  name: "Contoso kit (fictional)"
  version: "0.1.0"
  description: "A fictional one-command kit."
  author: "Dana Fictional"
  repository: "https://example.invalid/contoso/{SPECKIT_KIT}"
  license: "MIT"

requires:
  speckit_version: ">=0.9.0"

provides:
  commands:
    - name: "speckit.{SPECKIT_KIT}.hello"
      file: "commands/hello.md"
      description: "Print a short reminder."
"""


def require_specify():
    """Path of the `specify` CLI (the first of SPECKIT_CONTRACT_SPECIFY, else the one on PATH). Not opted in (SPECKIT_CONTRACT=1): skip.
    Opted in with none: fail, so the real-repository tests cannot vanish silently from a run that asked for them."""
    if os.environ.get("SPECKIT_CONTRACT") != "1":
        pytest.skip("needs SPECKIT_CONTRACT=1 (and the specify CLI on PATH, or SPECKIT_CONTRACT_SPECIFY)")
    listed = [p for p in os.environ.get("SPECKIT_CONTRACT_SPECIFY", "").split(os.pathsep) if p]
    specify = listed[0] if listed else shutil.which("specify")
    if specify is None:
        pytest.fail("SPECKIT_CONTRACT=1 but no specify executable: none on PATH and SPECKIT_CONTRACT_SPECIFY is empty")
    return specify


@pytest.fixture(scope="session")
def speckit_template(tmp_path_factory):
    """A committed Spec Kit repository on `main`: strict `ghp.config.json`, an approved `ghp.stack.json`, and a `ghp.kits.lock.json` pinning
    `contoso-kit` to its committed files (the toolkit's own `ghp` is tracked and unpinned, as in a normal install)."""
    specify = require_specify()
    root = tmp_path_factory.mktemp("speckit-template")
    repo, kit_src, ext_src, home = root / "repo", root / "kit-src", root / "ext-src", root / "home"
    for d in (repo, kit_src / "commands", ext_src, home):
        d.mkdir(parents=True)
    (kit_src / "extension.yml").write_text(_KIT_MANIFEST)
    (kit_src / "commands" / "hello.md").write_text("---\ndescription: Print a short reminder.\n---\n\nPrint a short reminder.\n")
    for part in _EXT_PARTS:
        (shutil.copytree if (ROOT / part).is_dir() else shutil.copy)(ROOT / part, ext_src / part)
    env = {**os.environ, "HOME": str(home), "XDG_CACHE_HOME": str(home / ".cache"), "XDG_CONFIG_HOME": str(home / ".config"),
           "XDG_DATA_HOME": str(home / ".local" / "share"), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "NO_COLOR": "1", "TERM": "dumb",
           **{f"GIT_{k}_{f}": v for k in ("AUTHOR", "COMMITTER") for f, v in (("NAME", "Test"), ("EMAIL", "test@example.invalid"))}}

    def run(*cmd):
        r = subprocess.run(cmd, cwd=repo, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180)
        assert r.returncode == 0, f"{' '.join(map(str, cmd[:3]))} exited {r.returncode}\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}"
    run("git", "init", "-q", "-b", "main")
    run("git", "config", "gc.auto", "0")  # no background repack while the tests copy this template
    run("git", "config", "maintenance.auto", "false")
    # --ignore-agent-tools: Spec Kit refuses to init when the agent's CLI is not on PATH (as on a hosted runner)
    run(specify, "init", "--here", "--force", "--non-interactive", "--integration", "claude", "--script", "sh", "--ignore-agent-tools")
    run(specify, "extension", "add", "--dev", str(ext_src))
    run(specify, "extension", "add", "--dev", str(kit_src))
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "spec kit and two extensions")
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], env=env, capture_output=True, text=True, check=True).stdout.strip()
    pin = {"version": "0.1.0", "source": None, "catalog_sha256": None, "dir_digest": kits.dir_digest_at(str(repo), sha, SPECKIT_KIT), "agents_sha256": "a" * 64}
    (repo / "ghp.kits.lock.json").write_text(json.dumps({"schema": 1, "kits": {SPECKIT_KIT: pin}}))
    (repo / "ghp.stack.json").write_text(json.dumps(approve(profile()), indent=1))
    (repo / "ghp.config.json").write_text(json.dumps({**ADVISORY, "mode": "strict", "strict": {"test_command": STACK_CMD}}))
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "adopt strict mode")
    return repo


@pytest.fixture
def speckit_repo(speckit_template, tmp_path, git_env):
    """A private copy of `speckit_template` (a git repository on `main`)."""
    repo = tmp_path / "repo"
    shutil.copytree(speckit_template, repo, symlinks=True)
    return repo


def _pr(repo, monkeypatch, head_files=None, target="main"):
    """`target` (a branch of the template, strict, the kit pinned) is the PR's target; `feature` (checked out) is one commit on top of it that
    adds the ledger and the spec files and `head_files` (path -> text, None deletes). The cwd is the repo; a fake GitHub answers the board."""
    git(repo, "update-ref", "refs/remotes/origin/main", target)
    git(repo, "checkout", "-q", "-b", "feature", target)
    _project(repo, prof=None)
    commit(repo, "#1001 pr", {k: v for k, v in (head_files or {}).items() if v is not None})
    for gone in (k for k, v in (head_files or {}).items() if v is None):
        git(repo, "rm", "-q", gone)
        git(repo, "commit", "-q", "-m", "#1001 delete")
    monkeypatch.chdir(repo)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    holder = SimpleNamespace(gh=_hub("T001", "T002"), config=None)
    spy_config(monkeypatch, holder)
    monkeypatch.setattr(m, "Gh", lambda: holder.gh)


def _track(repo, path, text="x\n", mode="100644"):
    """Stage `path` in the index only (never in the working tree: a case-insensitive file system folds variants)."""
    blob = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "--stdin"], input=text, capture_output=True, text=True, check=True).stdout.strip()
    git(repo, "config", "core.ignorecase", "false")
    git(repo, "update-index", "--add", "--cacheinfo", f"{mode},{blob},{path}")


def _check(capsys, *argv, phase="implement"):
    rc = main(["strict", "check", "1001", "--phase", phase, *(argv or ("--base", "origin/main"))])
    return rc, json.loads(capsys.readouterr().out)


def _kit_rules(out):
    return [f["rule"] for f in out["findings"] if f["rule"].startswith("kit-")]


def _kit(out, rule):
    got = [f for f in out["findings"] if f["rule"] == rule]
    assert len(got) == 1 and set(got[0]) == {"rule", "detail", "fix"}, out["findings"]
    return got[0]["detail"]


# ---- a normal repository, any PR ----
def test_the_template_is_a_real_spec_kit_repo(speckit_repo):
    tracked = git(speckit_repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
    for path in (".specify/init-options.json", ".specify/integration.json", ".specify/extensions.yml", ".specify/.gitignore",
                 ".specify/extensions/ghp/extension.yml", f"{KIT}/extension.yml", "ghp.kits.lock.json"):
        assert path in tracked, path


@pytest.mark.parametrize("phase", ["implement", "pr", "done"])
def test_a_pr_that_changes_an_unrelated_file_has_no_kit_finding(speckit_repo, monkeypatch, capsys, phase):
    _pr(speckit_repo, monkeypatch, {"src/app.py": "x = 1\n"})
    rc, out = _check(capsys, phase=phase)
    assert _kit_rules(out) == [] and "warnings" not in out, out
    if phase == "implement":
        assert (rc, out["ok"], out["findings"]) == (0, True, [])


def test_the_shipped_workflows_pr_step_invocation_passes(speckit_repo, monkeypatch, capsys):
    """`speckit.ghp.pr` runs `ghp strict check <n> --phase pr --head <branch> --base main` (a local `main`, no origin)."""
    _pr(speckit_repo, monkeypatch, {"src/app.py": "x = 1\n"})
    rc, out = _check(capsys, "--head", "feature", "--base", "main", phase="pr")
    assert _kit_rules(out) == [], out["findings"]
    assert REPLACED not in json.dumps(out)


def test_one_byte_of_a_pinned_kit_file_is_kit_changed(speckit_repo, monkeypatch, capsys):
    _pr(speckit_repo, monkeypatch, {f"{KIT}/commands/hello.md": "Print a short reminder.\n.\n"})
    rc, out = _check(capsys)
    assert rc == 2 and _kit_rules(out) == ["kit-changed"]
    detail = _kit(out, "kit-changed")
    assert SPECKIT_KIT in detail and REPLACED not in detail


def test_a_new_unpinned_kit_is_kit_unpinned_and_the_toolkit_is_not_one(speckit_repo, monkeypatch, capsys):
    _pr(speckit_repo, monkeypatch, {".specify/extensions/fabrikam-kit/extension.yml": "id: fabrikam-kit\n"})
    rc, out = _check(capsys)
    assert rc == 2 and _kit_rules(out) == ["kit-unpinned"]
    detail = _kit(out, "kit-unpinned")
    assert "fabrikam-kit" in detail and "1 kit(s)" in detail and "ghp" not in detail.replace("ghp.kits.lock.json", "")


def test_the_tracked_toolkit_extension_is_not_a_kit(speckit_repo, monkeypatch, capsys):
    _pr(speckit_repo, monkeypatch, {".specify/extensions/ghp/commands/extra.md": "a change to the toolkit's own files\n"})
    rc, out = _check(capsys)
    assert (rc, out["ok"], _kit_rules(out)) == (0, True, [])  # the toolkit's own files are reviewed as code, not pinned
    assert not any("kit dir without" in w for w in out.get("warnings", []))


def test_a_case_variant_of_the_toolkit_extension_is_still_reported(speckit_repo, monkeypatch, capsys):
    _pr(speckit_repo, monkeypatch)
    for name in ("GHP", "Ghp", "ｇhp"):
        _track(speckit_repo, f".specify/extensions/{name}/extension.yml")
        git(speckit_repo, "commit", "-q", "-m", "#1001 variant")
        rc, out = _check(capsys)
        assert rc == 2 and _kit_rules(out) == ["kit-unpinned"], name
        assert "#1" in _kit(out, "kit-unpinned") and name not in json.dumps(out, ensure_ascii=False), name
        git(speckit_repo, "reset", "-q", "--hard", "HEAD~1")


def test_the_toolkit_extension_replaced_by_a_symlink_is_out_of_scope(speckit_repo, monkeypatch, capsys):
    """The toolkit's own files are reviewed as code: `ghp` as a symlink is not a kit finding."""
    _pr(speckit_repo, monkeypatch)
    git(speckit_repo, "rm", "-rq", "--cached", ".specify/extensions/ghp")
    _track(speckit_repo, ".specify/extensions/ghp", "../../elsewhere", mode="120000")
    git(speckit_repo, "commit", "-q", "-m", "#1001 link")
    assert _kit_rules(_check(capsys)[1]) == []


# ---- the replaced-tree and lookalike detections, on the real layout ----
@pytest.mark.parametrize("name", ["extensions-gitlink", "extensions-file", "specify-gitlink", "specify-file", "specify-link", "extensions-link"])
def test_replaced_specify_or_extensions_is_still_refused_on_the_real_layout(speckit_repo, monkeypatch, capsys, name):
    _pr(speckit_repo, monkeypatch)
    path = ".specify/extensions" if name.startswith("extensions") else ".specify"
    git(speckit_repo, "rm", "-rq", "--cached", path)
    kind = name.split("-")[1]
    _track(speckit_repo, path, "../evil" if kind == "link" else "x\n", mode={"gitlink": "160000", "file": "100644", "link": "120000"}[kind])
    if kind == "gitlink":
        git(speckit_repo, "update-index", "--force-remove", path)
        git(speckit_repo, "update-index", "--add", "--cacheinfo", f"160000,{'4' * 40},{path}")
    git(speckit_repo, "commit", "-q", "-m", "#1001 replace")
    rc, out = _check(capsys)
    assert rc == 2 and _kit_rules(out) == ["kit-changed"] and REPLACED in _kit(out, "kit-changed"), (name, out["findings"])


LOOKALIKES = {".Specify/extensions/x/y.md": ("kit-changed", ".Specify"), ".specify/Extensions/x/y.md": ("kit-changed", "Extensions"),
              ".SPECIFY/extensions/contoso-kit/y.md": ("kit-changed", "SPECIFY"), ".specify/extensions/SF/y.md": ("kit-unpinned", "SF"),
              ".specify/extensions/Contoso-Kit/y.md": ("kit-unpinned", "Contoso-Kit")}


@pytest.mark.parametrize("path", list(LOOKALIKES))
def test_lookalike_paths_are_still_reported_on_the_real_layout(speckit_repo, monkeypatch, capsys, path):
    _pr(speckit_repo, monkeypatch)
    _track(speckit_repo, path)
    git(speckit_repo, "commit", "-q", "-m", "#1001 variant")
    rc, out = _check(capsys)
    rule, token = LOOKALIKES[path]
    assert rc == 2 and _kit_rules(out) == [rule], (path, out["findings"])
    assert REPLACED not in json.dumps(out) and token not in json.dumps(out)  # no repository name is echoed


def test_symlink_or_submodule_inside_a_kit_is_still_changed_on_the_real_layout(speckit_repo, monkeypatch, capsys):
    _pr(speckit_repo, monkeypatch)
    _track(speckit_repo, f"{KIT}/commands/link.md", "../../../README.md", mode="120000")
    git(speckit_repo, "commit", "-q", "-m", "#1001 link")
    out = _check(capsys)[1]
    assert _kit_rules(out) == ["kit-changed"] and REPLACED not in json.dumps(out)
    _track(speckit_repo, ".specify/extensions/vendor", "../../elsewhere", mode="120000")
    git(speckit_repo, "commit", "-q", "-m", "#1001 unpinned link")
    out = _check(capsys)[1]
    assert set(_kit_rules(out)) == {"kit-changed", "kit-unpinned"} and "vendor" in _kit(out, "kit-unpinned") and REPLACED not in json.dumps(out)


# ---- the adoption PR (the target is advisory) gets the trust notes ----
def _adoption(repo, monkeypatch, target_cfg=ADVISORY, files=()):
    """`main` becomes an advisory project without profile or lockfile (`target_cfg` None: no config at all); `feature` adds them back."""
    strict_files = {p: (repo / p).read_text() for p in ("ghp.config.json", "ghp.stack.json", "ghp.kits.lock.json")}
    git(repo, "rm", "-q", "ghp.stack.json", "ghp.kits.lock.json", *(["ghp.config.json"] if target_cfg is None else []))
    if target_cfg is not None:
        (repo / "ghp.config.json").write_text(json.dumps(target_cfg))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "advisory main")
    _pr(repo, monkeypatch, {p: strict_files[p] for p in strict_files if p in files or not files})


@pytest.mark.parametrize("target_cfg", [ADVISORY, None], ids=["advisory-target", "no-config-target"])
def test_the_adoption_pr_gets_both_trust_notes(speckit_repo, monkeypatch, capsys, target_cfg):
    _adoption(speckit_repo, monkeypatch, target_cfg)
    rc, out = _check(capsys)
    assert NOTE in out.get("warnings", []) and KNOTE in out.get("warnings", []), out
    assert all(f["rule"] != "config-invalid" for f in out["findings"])  # a note, never a finding of its own
    summary = speckit_repo / "out" / "gate.md"
    rc = main(["gate", "1001", "--offline", "--src", ".", "--base", "origin/main", "--summary-out", str(summary)])
    gate = json.loads(capsys.readouterr().out)
    assert gate["config"] == "pull request" and gate["trust_note"] == NOTE and gate["kits_trust_note"] == KNOTE
    lines = summary.read_text().splitlines()
    assert lines.count(NOTE) == 1 and lines.count(KNOTE) == 1


def test_the_adoption_pr_with_only_the_mode_switch_gets_the_stack_note(speckit_repo, monkeypatch, capsys):
    _adoption(speckit_repo, monkeypatch, files=("ghp.config.json",))
    out = _check(capsys)[1]
    assert NOTE in out.get("warnings", []) and KNOTE not in out.get("warnings", [])


def test_no_trust_note_when_the_target_is_strict_or_the_pr_is_advisory(speckit_repo, monkeypatch, capsys):
    _pr(speckit_repo, monkeypatch, {"src/app.py": "x = 1\n"})  # a strict target and an unrelated change: nothing to note
    out = _check(capsys)[1]
    assert NOTE not in out.get("warnings", []) and KNOTE not in out.get("warnings", [])
    main(["gate", "1001", "--offline", "--src", ".", "--base", "origin/main"])
    got = json.loads(capsys.readouterr().out)
    assert "trust_note" not in got and "kits_trust_note" not in got and got["config"] == "target branch (strict)"


def test_the_notes_need_a_base(speckit_repo, monkeypatch, capsys):
    _adoption(speckit_repo, monkeypatch)
    main(["gate", "1001", "--offline", "--src", "."])  # no --base: nothing is compared to a target
    got = json.loads(capsys.readouterr().out)
    assert "trust_note" not in got and "kits_trust_note" not in got


def test_adoption_notes_unit(speckit_repo, monkeypatch):
    """`strict.adoption_notes(cfg, repo, base, commit)` -> (stack note?, kits note?) from the commits only."""
    _adoption(speckit_repo, monkeypatch)
    cfg = Config("octo", "demo", mode="strict")
    assert strict.adoption_notes(cfg, str(speckit_repo), "origin/main", "HEAD") == (True, True)
    assert strict.adoption_notes(cfg, str(speckit_repo), "", "HEAD") == (False, False)
    assert strict.adoption_notes(cfg, str(speckit_repo), "no-such-branch", "HEAD") == (False, False)
    cfg.from_target = True
    assert strict.adoption_notes(cfg, str(speckit_repo), "origin/main", "HEAD") == (False, False)


def test_missing_specify_skips_unless_opted_in_and_then_fails(monkeypatch):
    """Runs everywhere (no `specify` needed): the opt-in that keeps these tests out of the PR job cannot hide them from a run that asked."""
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.delenv("SPECKIT_CONTRACT_SPECIFY", raising=False)
    monkeypatch.delenv("SPECKIT_CONTRACT", raising=False)
    with pytest.raises(pytest.skip.Exception):
        require_specify()
    monkeypatch.setenv("SPECKIT_CONTRACT", "1")
    with pytest.raises(pytest.fail.Exception):
        require_specify()
    monkeypatch.setattr("shutil.which", lambda name: "/bin/specify")
    assert require_specify() == "/bin/specify"
    monkeypatch.setenv("SPECKIT_CONTRACT_SPECIFY", os.pathsep.join(["/opt/a/specify", "/opt/b/specify"]))
    assert require_specify() == "/opt/a/specify"
