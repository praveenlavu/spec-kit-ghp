import importlib.util
import subprocess
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "scrub_check", Path(__file__).resolve().parent.parent / "scripts" / "scrub_check.py"
)
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)

TERM = "acmehealth"


def git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        cwd=repo, check=True, capture_output=True,
    )


def make_repo(tmp_path, content):
    git(tmp_path, "init", "-q")
    (tmp_path / "f.md").write_text(content)
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "c1")
    return tmp_path


def test_denylist_case_insensitive_and_substring():
    hits = sc.scan_text("a.md", "x\nWelcome to AcmeHealthCo portal", ["acmehealth"])
    assert hits == [("a.md", 2, "denylist#1")]


def test_hit_never_contains_term(tmp_path, monkeypatch, capsys):
    make_repo(tmp_path, "see AcmeHealth here\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCRUB_DENYLIST", TERM)
    assert sc.main([]) == 1
    out = capsys.readouterr().out
    assert TERM not in out.lower()
    assert "f.md:1:denylist#1" in out


def test_secret_patterns():
    assert sc.scan_text("f", "token ghp_" + "a" * 36, [])[0][2] == "secret:github-token"
    assert sc.scan_text("f", "a" * 52, [])[0][2] == "secret:ado-pat"
    assert sc.scan_text("f", "github_pat_" + "a" * 22, [])[0][2] == "secret:github-pat"
    assert sc.scan_text("f", "AKIA" + "A" * 16, [])[0][2] == "secret:aws-key"
    assert sc.scan_text("f", "-----BEGIN RSA " + "PRIVATE KEY-----", [])[0][2] == "secret:private-key"
    assert sc.scan_text("f", "nothing to see", []) == []


def test_env_denylist_wins(tmp_path):
    (tmp_path / "d").write_text("filelist\n")
    assert sc.load_denylist({"SCRUB_DENYLIST": "# c\nEnvTerm\n\n"}, tmp_path / "d") == ["envterm"]
    assert sc.load_denylist({}, tmp_path / "d") == ["filelist"]
    assert sc.load_denylist({}, tmp_path / "missing") == []


def test_history_mode_finds_removed_term(tmp_path, monkeypatch, capsys):
    make_repo(tmp_path, "AcmeHealth\n")
    (tmp_path / "f.md").write_text("clean\n")
    git(tmp_path, "commit", "-q", "-am", "c2")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCRUB_DENYLIST", TERM)
    assert sc.main(["--history"]) == 1
    out = capsys.readouterr().out
    assert "history:" in out and TERM not in out.lower()
    assert sc.main([]) == 0


def test_skips_binary_and_denylist_file(tmp_path, monkeypatch):
    make_repo(tmp_path, "ok\n")
    (tmp_path / "b.bin").write_bytes(b"\x00AcmeHealth")
    (tmp_path / ".scrub-denylist").write_text(TERM + "\n")
    git(tmp_path, "add", "-f", ".")
    git(tmp_path, "commit", "-q", "-m", "c2")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCRUB_DENYLIST", TERM)
    assert sc.main([]) == 0


def test_require_denylist_exits_2_when_empty(tmp_path, monkeypatch):
    make_repo(tmp_path, "ok\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SCRUB_DENYLIST", raising=False)
    assert sc.main(["--require-denylist"]) == 2
    assert sc.main([]) == 0


def test_scans_staged_blob_not_working_copy(tmp_path, monkeypatch, capsys):
    make_repo(tmp_path, "ok\n")
    (tmp_path / "f.md").write_text("AcmeHealth\n")
    git(tmp_path, "add", "f.md")
    (tmp_path / "f.md").write_text("clean\n")  # edited out on disk only
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCRUB_DENYLIST", TERM)
    assert sc.main([]) == 1
    assert "f.md:1:denylist#1" in capsys.readouterr().out


def test_git_failure_exits_3_without_term(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # not a git repo
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    monkeypatch.setenv("SCRUB_DENYLIST", TERM)
    assert sc.main([]) == 3
    captured = capsys.readouterr()
    assert captured.err.strip() and TERM not in (captured.out + captured.err).lower()


def test_history_scans_author_email_and_message(tmp_path, monkeypatch, capsys):
    make_repo(tmp_path, "ok\n")
    (tmp_path / "g.md").write_text("more\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "-c", "user.email=x@acmehealth.com", "commit", "-q", "-m", "c2")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCRUB_DENYLIST", TERM)
    assert sc.main([]) == 0
    assert sc.main(["--history"]) == 1
    assert TERM not in capsys.readouterr().out.lower()
    git(tmp_path, "commit", "-q", "--amend", "--reset-author", "-m", "mentions AcmeHealth")
    assert sc.main(["--history"]) == 1


def test_history_scan_finds_a_term_in_an_old_commit(tmp_path, monkeypatch, capsys):
    """AC2: --history reaches a term that only the first of three commits contains."""
    make_repo(tmp_path, "AcmeHealth\n")
    for n in (2, 3):
        (tmp_path / "f.md").write_text(f"clean {n}\n")
        git(tmp_path, "commit", "-q", "-am", f"c{n}")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCRUB_DENYLIST", TERM)
    assert sc.main([]) == 0  # tip is clean
    assert sc.main(["--history"]) == 1
    out = capsys.readouterr().out
    assert "history:" in out and TERM not in out.lower()


def test_ghp_identifiers_do_not_trigger_the_token_pattern():
    """AC3: the CLI name shares the classic-token prefix; its identifiers stay clean, a real-shaped token still fires."""
    for ident in ("GHP_PROJECT_TOKEN", "ghp_config", "ghp-gate", "export GHP_PROJECT_TOKEN=ghp_config"):
        assert sc.scan_text("f", ident, []) == [], ident
    canary = "gh" + "p_" + "A" * 36  # built at run time: no token-shaped literal is committed
    assert sc.scan_text("f", "x " + canary, [])[0][2] == "secret:github-token"


def test_history_authors_are_only_the_noreply_identity():
    """AC4: every author and committer in this repo's history is the praveenlavu no-reply identity."""
    root = Path(__file__).resolve().parent.parent
    p = subprocess.run(
        ["git", "log", "--all", "--format=%ae%n%ce"], cwd=root, capture_output=True, text=True
    )
    emails = {e for e in p.stdout.split() if e}
    if p.returncode or not emails:
        pytest.skip("git log is empty or unavailable: nothing to check")
    assert emails == {"22502189+praveenlavu@users.noreply.github.com"}


def test_secret_lists_equal():
    """`ghp.stack.SECRETS` is a copy of the standalone script's list (the script runs without the package): the two must not drift."""
    from ghp import stack

    assert [(n, p.pattern, p.flags) for n, p in stack.SECRETS] == [(n, p.pattern, p.flags) for n, p in sc.SECRETS]


def test_tracked_tree_is_clean_of_the_owners_denylist(monkeypatch, capsys):
    """T44-AC1/AC3: with the owner's denylist (gitignored `.scrub-denylist` or SCRUB_DENYLIST) the tracked tree has no hit."""
    root = Path(__file__).resolve().parent.parent
    if not sc.load_denylist(sc.os.environ, root / sc.DENYLIST_FILE):
        pytest.skip("no .scrub-denylist or SCRUB_DENYLIST supplied: the owner's name scan cannot run")
    monkeypatch.chdir(root)
    assert sc.main(["--require-denylist"]) == 0, capsys.readouterr().out  # path:line:kind only, never the term
