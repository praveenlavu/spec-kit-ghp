"""T21: `ghp branch` and the issue-number readers of branch names and PR text, ported from ado tests/test_branch.py (rename table: `ab<id>` is
`gh<n>`, `AB#<id>` is `#<n>`; the tracker seam is `FakeGh`, which must stay untouched: the verb makes no call).

Dropped from ado: tests/test_branch_link.py whole (24 tests: `branch --link` adds an ArtifactLink to an Azure Repos work item; D9 drops it, there is no
GitHub counterpart in 0.1, `createLinkedBranch` is UNVERIFIED), test_config_branch_prefix_default_and_validation (`valid_branch_prefix` and the
`branch_prefix` key are ghp's own T04 tests in test_config.py; the ado test used the Azure keys), test_valid_branch_prefix (same)."""
import json
import re
import subprocess

import pytest

import ghp.__main__ as cli
from conftest import FakeGh, commit, git
from ghp import branch, prs
from ghp.config import valid_branch_prefix


@pytest.mark.parametrize("title,expected", [
    ("Count active members", "count-active-members"),
    ("Café: Über-Größe!!", "cafe-uber-groe"),
    ("  --a__b--  ", "a-b"),
    ("x" * 60, "x" * 40),
    ("a" * 39 + " b", "a" * 39),  # cut at 40 never leaves a trailing dash
    ("Count active team members by status and role", "count-active-team-members-by-status-and"),  # no half word
    ("aaaa bbbb " + "c" * 40, "aaaa-bbbb"),  # a word longer than the room is dropped whole
    ("a" * 36 + " bbb ccc", "a" * 36 + "-bbb"),  # a word that ends exactly at 40 is kept
    ("日本語", "issue"),
    ("", "issue"),
    (None, "issue"),
    ("x\x1b[2J$(touch pwned) @bob\nline2", "x-2j-touch-pwned-bob-line2"),  # nothing but [a-z0-9-] survives
])
def test_slug(title, expected):
    assert branch.slug(title) == expected


CASES = [
    ("feature/gh2-count-active", "feature/", 2),
    ("refs/heads/feature/gh1001-x", "feature/", 1001),
    ("feature/gh12", "feature/", 12),
    ("feature/GH12-x", "feature/", 12),         # `gh` is case-insensitive
    ("gh12-x", "", 12),
    ("bugfix-#34-thing", "feature/", 34),
    ("fix/#34", "feature/", 34),
    ("#7", "", 7),
    ("feature/gh5-x-#9", "feature/", 5),        # prefix form wins over #N
    ("feature/x-#9", "feature/", 9),
    ("x#9", "feature/", None),                  # #N inside a word
    ("\u00e9#12", "feature/", None),            # a non-ASCII letter or digit is part of a word too
    ("fix-\u00e9#12", "feature/", None),
    ("fix-#12", "feature/", 12),
    ("gh12\n", "", None), ("feature/gh12\n", "feature/", None), ("gh12-x\n", "", 12),  # `$` would have matched before a final newline
    ("1#9", "feature/", None),
    ("feature/gh2147483647-x", "feature/", 2147483647),
    ("feature/gh2147483648-x", "feature/", None),  # over int32
    ("feature/gh" + "9" * 5000, "feature/", None),
    ("#" + "9" * 5000, "", None),
    # Spec Kit's own branches never read as an issue, whatever the prefix
    ("001-count-active-members", "feature/", None),
    ("001-count-active-members", "", None),
    ("100-x", "", None),
    ("feature/100-x", "feature/", None),
    ("feature/20261028-143022-x", "feature/", None),
    ("20261028-143022-x", "", None),
    ("feature/2-count-active", "feature/", None),  # plain <prefix><n> does not count
    ("feature/gh007-x", "feature/", None),
    ("#012", "feature/", None),
    ("feature/ghc-12", "feature/", None),
    ("feature/gh12x", "feature/", None),
    ("release/1.2", "feature/", None),
    ("main", "feature/", None),
    ("users/feature/gh3-x", "feature/", None),  # prefix only at the start
    ("feature/ｇｈ" + "5-x", "feature/", None),  # fullwidth letters are not the ASCII prefix
    ("feature/gh٥-x", "feature/", None),             # an Arabic-Indic digit is not an ASCII digit
    ("feature/ab5-x", "feature/", None),                  # ado's prefix is no longer ours
    ("AB#7", "", None),
]


@pytest.mark.parametrize("name,prefix,expected", CASES)
def test_issue_is_recovered_from_branch_or_text(name, prefix, expected):
    assert prs.issue_from_branch(name, prefix) == expected


def test_the_text_reader_takes_the_one_unquoted_reference_and_refuses_ambiguity():
    assert prs.issue_from_text("Closes #12 (see the notes)") == 12
    assert prs.issue_from_text("Closes #12 and #13") is None
    assert prs.issue_from_text("```\n#12\n```\nand #13") == 13
    assert prs.issue_from_text("gh12-x") is None  # a branch prefix is not text


# ---- `ghp branch` on a real temp git repo ----
@pytest.fixture
def repo(tmp_path, monkeypatch, git_env):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "init", {"README.md": "x\n", "ghp.config.json": json.dumps({"owner": "octo", "repo": "demo"})})
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    return tmp_path


def set_prefix(repo, prefix):
    (repo / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", "branch_prefix": prefix}))


def run(capsys, number="2", *more, title="Count active members"):
    gh = FakeGh()
    argv = ["branch", number, *more] + (["--title", title] if title is not None else [])
    code = cli.main(argv, gh=gh)
    out = capsys.readouterr().out
    assert gh.calls == [] and not gh.armed  # no network, ever
    return code, (json.loads(out) if out.strip() else None)


def current(repo):
    return git(repo, "symbolic-ref", "--short", "HEAD")


def feature_json(repo, spec_dir):
    (repo / ".specify").mkdir(exist_ok=True)
    (repo / ".specify" / "feature.json").write_text(json.dumps({"feature_directory": spec_dir}))


def test_branch_name_is_gh_n_slug(repo, capsys):
    assert run(capsys) == (0, {"branch": "feature/gh2-count-active-members", "action": "create", "applied": False})
    assert current(repo) == "main"
    assert run(capsys, "2", "--apply") == (0, {"branch": "feature/gh2-count-active-members", "action": "create", "applied": True})
    assert current(repo) == "feature/gh2-count-active-members"
    assert git(repo, "rev-parse", "main") == git(repo, "rev-parse", "HEAD")  # created from HEAD
    assert run(capsys) == (0, {"branch": "feature/gh2-count-active-members", "action": "exists", "applied": False})


def test_the_title_comes_from_the_ledger_when_no_title_is_given(repo, capsys):
    (repo / "dod").mkdir()
    (repo / "dod" / "DoD_2.md").write_text("# DoD — #2: Count active members\n\n- **Issue:** octo/demo#2\n")
    assert run(capsys, title=None)[1]["branch"] == "feature/gh2-count-active-members"
    (repo / "dod" / "DoD_2.md").write_text("# DoD — #3: Another issue\n")  # another issue's ledger is not this one's title
    assert run(capsys, title=None)[1]["branch"] == "feature/gh2-issue"
    (repo / "dod" / "DoD_2.md").unlink()
    assert run(capsys, title=None)[1]["branch"] == "feature/gh2-issue"  # no ledger: a fixed word, still no network


def test_a_ledger_that_is_a_symlink_is_not_read(repo, capsys):
    (repo / "dod").mkdir()
    (repo.parent / (repo.name + "-t.md")).write_text("# DoD — #2: Leaked title\n")
    (repo / "dod" / "DoD_2.md").symlink_to(repo.parent / (repo.name + "-t.md"))
    assert run(capsys, title=None)[1]["branch"] == "feature/gh2-issue"


def test_a_hostile_title_only_ever_becomes_a_slug(repo, capsys):
    out = run(capsys, "2", "--apply", title="x $(touch pwned) `touch pwned2` ; rm -rf ~\n--force @bob")[1]
    assert re.fullmatch(r"feature/gh2-[a-z0-9-]+", out["branch"]) and not (repo / "pwned").exists() and current(repo) == out["branch"]


def test_rename_speckit_branch_created_for_this_spec(repo, capsys):
    git(repo, "switch", "-q", "-c", "001-count-active-members")
    feature_json(repo, "specs/001-count-active-members")
    assert run(capsys, "2", "--apply") == (0, {"branch": "feature/gh2-count-active-members", "action": "rename",
                                              "from": "001-count-active-members", "applied": True})
    assert current(repo) == "feature/gh2-count-active-members"
    assert "001-count-active-members" not in git(repo, "branch", "--list")


def test_speckit_branch_of_another_spec_is_not_renamed(repo, capsys):
    git(repo, "switch", "-q", "-c", "001-older-feature")
    feature_json(repo, "specs/002-count-active-members")
    assert run(capsys, "2", "--apply")[1]["action"] == "create"
    assert "001-older-feature" in git(repo, "branch", "--list")


def test_no_feature_json_means_create_not_rename(repo, capsys):
    git(repo, "switch", "-q", "-c", "001-count-active-members")
    assert run(capsys)[1]["action"] == "create"


def test_switch_to_existing_issue_branch(repo, capsys):
    git(repo, "branch", "feature/gh2-old-title")
    assert run(capsys, "2", "--apply") == (0, {"branch": "feature/gh2-old-title", "action": "switch", "applied": True})
    assert current(repo) == "feature/gh2-old-title"


def test_the_planned_name_wins_among_several_tied_branches_and_otherwise_the_user_chooses(repo, capsys):
    git(repo, "branch", "feature/gh2-a")
    git(repo, "branch", "feature/gh2-count-active-members")
    assert run(capsys, "2", "--apply")[1]["branch"] == "feature/gh2-count-active-members"
    git(repo, "switch", "-q", "main")
    git(repo, "branch", "-D", "feature/gh2-count-active-members")
    git(repo, "branch", "feature/gh2-b")
    code = cli.main(["branch", "2", "--title", "Count active members", "--apply"], gh=FakeGh())
    assert code == 2 and "several local branches are tied to #2" in capsys.readouterr().err and current(repo) == "main"


def test_current_branch_already_tied_by_a_reference(repo, capsys):
    git(repo, "switch", "-q", "-c", "fix-#2")
    assert run(capsys, "2", "--apply") == (0, {"branch": "fix-#2", "action": "exists", "applied": True})
    assert current(repo) == "fix-#2"


def test_default_branch_is_never_renamed(repo, capsys):
    git(repo, "branch", "-m", "main", "001-count-active-members")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/001-count-active-members")
    feature_json(repo, "specs/001-count-active-members")
    assert run(capsys, "2", "--apply")[1]["action"] == "create"
    assert "001-count-active-members" in git(repo, "branch", "--list")


def test_empty_prefix(repo, capsys):
    set_prefix(repo, "")
    assert run(capsys)[1]["branch"] == "gh2-count-active-members"


def test_not_a_git_repo_fails_before_anything_else(tmp_path, monkeypatch, capsys, git_env):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo"}))
    gh = FakeGh()
    assert cli.main(["branch", "2", "--title", "x"], gh=gh) == 2
    assert "not a git repository" in capsys.readouterr().err and gh.calls == []


def test_round_trip_name_parses_back_to_the_issue(repo, capsys):
    for number in (1, 2, 1001, 2147483647):
        name = run(capsys, str(number))[1]["branch"]
        assert prs.issue_from_branch(name, "feature/") == number


def test_detached_head_creates_from_head(repo, capsys):
    git(repo, "switch", "-q", "--detach")
    out = run(capsys, "2", "--apply")[1]
    assert out["action"] == "create" and current(repo) == "feature/gh2-count-active-members"


def test_switch_blocked_by_local_changes_is_an_error(repo, capsys):
    git(repo, "switch", "-q", "-c", "feature/gh2-x")
    commit(repo, "on issue branch", {"README.md": "issue\n"})
    git(repo, "switch", "-q", "main")
    (repo / "README.md").write_text("dirty\n")
    assert cli.main(["branch", "2", "--apply"], gh=FakeGh()) == 2
    assert "git switch feature/gh2-x failed" in capsys.readouterr().err
    assert current(repo) == "main" and (repo / "README.md").read_text() == "dirty\n"


def test_speckit_branch_with_upstream_is_not_renamed(repo, capsys):
    git(repo, "switch", "-q", "-c", "001-count-active-members")
    feature_json(repo, "specs/001-count-active-members")
    git(repo, "config", "branch.001-count-active-members.remote", ".")
    git(repo, "config", "branch.001-count-active-members.merge", "refs/heads/main")
    assert run(capsys, "2", "--apply")[1]["action"] == "create"
    assert "001-count-active-members" in git(repo, "branch", "--list")


def test_unknown_default_branch_means_create_not_rename(tmp_path, monkeypatch, capsys, git_env):
    git(tmp_path, "init", "-q", "-b", "001-count-active-members")
    commit(tmp_path, "init", {"README.md": "x\n"})
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo"}))
    feature_json(tmp_path, "specs/001-count-active-members")
    assert run(capsys, "2", "--apply")[1]["action"] == "create"
    assert "001-count-active-members" in git(tmp_path, "branch", "--list")


def test_a_branch_name_that_is_not_plain_never_reaches_git(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
    for p in ({"action": "switch", "branch": "-x"}, {"action": "create", "branch": "--force"}, {"action": "rename", "from": "-b", "branch": "feature/gh2-x"},
              {"action": "rename", "from": "001-x", "branch": "a..b"}, {"action": "switch", "branch": "café"}):
        with pytest.raises(ValueError, match="invalid issue branch name"):
            branch.apply(p)
    assert calls == []


def test_the_title_of_a_ledger_is_read_confined(repo):
    from ghp.config import Config
    with pytest.raises(ValueError, match="E_DOD_DIR"):
        branch.title_from_ledger(Config("octo", "demo", dod_path="../elsewhere"), 2)


# ---- --id-from ----
# ported from ado tests/test_ci_trust.py (test_branch_id_from_base_uses_the_target_config, test_branch_id_from_advisory_or_absent_target_is_v02,
# test_strict_head_unresolvable_base_exit_3, test_advisory_head_unresolvable_base_keeps_v02, test_a_resolvable_base_with_an_unreadable_target_...).
# EXIT CODE DEVIATION: ado exits 3 where ghp exits 2 (the frame maps ConfigError and ValueError to 2; T35's CI templates must expect 2, not 3).
STRICT = {"owner": "octo", "repo": "demo", "mode": "strict"}


def pr_repo(repo, target, head):
    """`target` (a config dict, text, or None for no config file) committed as origin/main; then a PR commit whose working-tree config is `head`."""
    if target is None:
        git(repo, "rm", "-q", "ghp.config.json")
    else:
        (repo / "ghp.config.json").write_text(target if isinstance(target, str) else json.dumps(target))
        git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", "target")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "switch", "-q", "-c", "pr")
    commit(repo, "pr", {"ghp.config.json": head if isinstance(head, str) else json.dumps(head)})


def id_from(capsys, name, *more):
    code = cli.main(["branch", "--id-from", name, *more], gh=FakeGh())
    return code, capsys.readouterr()


def test_id_from_base_uses_the_target_config_and_fails_closed(repo, capsys):
    pr_repo(repo, {**STRICT, "branch_prefix": "team/"}, {"owner": "octo", "repo": "demo", "branch_prefix": "x/"})  # the PR re-points the prefix
    assert id_from(capsys, "team/gh1001-x")[0] == 1  # the PR's config alone: no issue number, a CI template would skip the gate
    code, seen = id_from(capsys, "team/gh1001-x", "--base", "origin/main")
    assert (code, seen.out.strip()) == (0, "1001")
    assert id_from(capsys, "x/gh1001-x", "--base", "origin/main")[0] == 1  # the PR's own prefix does not count under --base
    (repo / "ghp.config.json").unlink()  # the PR deletes its config: exit 2 without --base, the target still answers with it
    assert id_from(capsys, "team/gh1001-x")[0] == 2
    assert id_from(capsys, "team/gh1001-x", "--base", "origin/main")[0] == 0
    git(repo, "switch", "-q", "main")  # an invalid target: fail closed, never the PR's own prefix
    (repo / "ghp.config.json").write_text("{not json")
    git(repo, "commit", "-qam", "break")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "switch", "-q", "pr")
    (repo / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", "branch_prefix": "x/"}))  # a VALID working-tree config
    code, seen = id_from(capsys, "x/gh1001-x", "--base", "origin/main")
    assert code == 3 and seen.out == "" and seen.err.startswith("error: ") and "target branch" in seen.err  # 3: an invalid config under --id-from fails closed (T23, as ado; CI treats 3 as a failure)


@pytest.mark.parametrize("target", [None, {"owner": "octo", "repo": "demo"}])
def test_id_from_with_an_advisory_or_absent_target_keeps_the_heads_prefix(repo, capsys, target):
    pr_repo(repo, target, {"owner": "octo", "repo": "demo", "branch_prefix": "x/"})
    assert id_from(capsys, "feature/gh1001-x", "--base", "origin/main")[0] == 1
    code, seen = id_from(capsys, "x/gh7-thing", "--base", "origin/main")
    assert (code, seen.out.strip()) == (0, "7")


def test_id_from_with_an_unresolvable_base(repo, capsys):
    pr_repo(repo, STRICT, {"owner": "octo", "repo": "demo", "branch_prefix": "x/"})
    code, seen = id_from(capsys, "x/gh7-t", "--base", "origin/nosuch")  # advisory head: as without --base
    assert (code, seen.out.strip()) == (0, "7")
    (repo / "ghp.config.json").write_text(json.dumps({**STRICT, "branch_prefix": "x/"}))  # a strict head cannot be left to a base that is not there
    code, seen = id_from(capsys, "x/gh7-t", "--base", "origin/nosuch")
    assert code == 3 and seen.out == ""


def test_id_from_base_is_a_commit_with_an_unreadable_target_even_for_an_advisory_head(repo, capsys):
    pr_repo(repo, "{not json", {"owner": "octo", "repo": "demo"})
    assert id_from(capsys, "feature/gh7-t", "--base", "origin/main")[0] == 3


def test_cli_id_from(repo, capsys):
    gh = FakeGh()
    assert cli.main(["branch", "--id-from", "refs/heads/feature/gh1001-x"], gh=gh) == 0
    assert capsys.readouterr().out.strip() == "1001"
    assert cli.main(["branch", "--id-from", "001-count-active-members"], gh=gh) == 1
    assert capsys.readouterr().out.strip() == "null" and gh.calls == []


def test_id_from_with_base_takes_a_strict_targets_prefix(repo, capsys):
    (repo / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", "mode": "strict", "branch_prefix": "team/"}))
    git(repo, "commit", "-qam", "strict target")
    git(repo, "branch", "target")  # the target branch is strict with prefix team/ ...
    set_prefix(repo, "x/")  # ... while the PR's own config moves the prefix
    assert cli.main(["branch", "--id-from", "x/gh5-a"], gh=FakeGh()) == 0 and capsys.readouterr().out.strip() == "5"
    assert cli.main(["branch", "--id-from", "x/gh5-a", "--base", "target"], gh=FakeGh()) == 1  # the target's prefix judges, not the PR's
    assert cli.main(["branch", "--id-from", "team/gh5-a", "--base", "target"], gh=FakeGh()) == 0
    assert capsys.readouterr().out.split()[-1] == "5"


def test_id_from_with_an_advisory_target_keeps_the_working_trees_prefix(repo, capsys):
    git(repo, "branch", "target")  # advisory
    set_prefix(repo, "x/")
    assert cli.main(["branch", "--id-from", "x/gh5-a", "--base", "target"], gh=FakeGh()) == 0


@pytest.mark.parametrize("argv", [
    ["branch"], ["branch", "2", "--id-from", "x"], ["branch", "--id-from", "x", "--apply"], ["branch", "0"], ["branch", "2147483648"],
    ["branch", "2", "--base", "main"], ["branch", "#2x"], ["branch", "--id-from", "x", "--title", "t"],
])
def test_cli_branch_usage_errors(repo, argv, capsys):
    gh = FakeGh()
    assert cli.main(argv, gh=gh) == 2
    assert capsys.readouterr().err.startswith("error: ") and gh.calls == []
