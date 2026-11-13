"""T30 `ghp setup`: asks no secret, writes names only, three profiles, never overwrites without --force."""
import argparse
import json
import os
import re
import stat
from pathlib import Path

import pytest

from conftest import FakeGh, approve, git, profile
from ghp import setup_cmd
from ghp.__main__ import main
from ghp.config import Config
from ghp.gh import GhError, GhMissing, Viewer

ME = 4242
VIEWER = Viewer(ME, "octocat", ["repo", "project"])
SRC = Path(__file__).resolve().parent.parent / "src" / "ghp"
BASE = ["owner", "repo", "project_owner", "project_number", "status_todo", "status_active", "status_done"]
# the prompts of an interactive run, in order: owner, repo, project number, project owner, three status names, profile
FIELDS = ("", "demo", "3", "", "", "", "", "")  # owner and project owner default to the signed-in user, the statuses to Todo / In Progress / Done


def parse(argv):
    p = argparse.ArgumentParser(prog="ghp")
    setup_cmd.register(p.add_subparsers(dest="command"))
    return p.parse_args(["setup", *argv])


class Gh(FakeGh):
    def __init__(self, viewer=VIEWER):
        super().__init__(viewer)
        self.viewer_calls = 0

    def viewer(self):
        self.viewer_calls += 1
        return super().viewer()


@pytest.fixture
def cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    return tmp_path


def go(argv=(), answers=FIELDS, interactive=True, gh=None, prompts=None):
    """run_setup with scripted answers; a question beyond the script raises StopIteration (the test then fails: setup asked more than it should)."""
    it, seen = iter(answers), prompts if prompts is not None else []

    def inp(prompt):
        seen.append(prompt)
        return next(it)
    return setup_cmd.run_setup(parse(argv), gh or Gh(), input_fn=inp, interactive=interactive)


def written(cwd):
    return json.loads((cwd / "ghp.config.json").read_text())


# ---- AC1: no secret, names only ----
def test_setup_asks_no_secret_and_writes_names_only(cwd, capsys, monkeypatch):
    canary = "gh" + "p_" + "A" * 36
    for var in ("GH_TOKEN", "GITHUB_TOKEN", "GHP_TOKEN"):
        monkeypatch.setenv(var, canary)
    gh, prompts = Gh(), []
    assert go(answers=("octocat", "demo", "3", "octocat", "Backlog", "Doing", "Shipped", "starter"), gh=gh, prompts=prompts) == 0
    assert gh.viewer_calls == 1 and gh.calls == [] and not gh.armed  # `GET /user` once; nothing else asked of GitHub
    data = written(cwd)
    assert data == {"owner": "octocat", "repo": "demo", "project_owner": "octocat", "project_number": 3,
                    "status_todo": "Backlog", "status_active": "Doing", "status_done": "Shipped", "mode": "advisory"}
    text = (cwd / "ghp.config.json").read_text()
    assert canary not in text and not re.search(r"token|PVT_|node_id|\"id\"", text, re.I)  # names and numbers the person typed, no id, no token
    assert len(prompts) == 8 and not any(re.search(r"token|password|secret|credential|\bPAT\b|key", p, re.I) for p in prompts)
    out = capsys.readouterr().out
    assert canary not in out and "never asks for a token" in out and "user id 4242" in out
    assert Config.load(str(cwd)).status_done == "Shipped"


def test_setup_says_in_plain_words_that_only_user_owned_projects_work(cwd):
    prompts = []
    go(prompts=prompts)
    ask = next(p for p in prompts if "roject number" in p)
    assert "user account" in ask and "organization" in ask.lower()


def test_defaults_are_the_signed_in_user_and_the_three_standard_status_names(cwd):
    go()
    assert written(cwd) == {"owner": "octocat", "repo": "demo", "project_owner": "octocat", "project_number": 3, "status_todo": "Todo",
                            "status_active": "In Progress", "status_done": "Done", "mode": "advisory"}


def test_the_project_owner_defaults_to_the_signed_in_user_not_to_the_repository_owner(cwd):
    go(answers=("acme-demo", "  demo  ", " 3 ", "", " ", "", "", ""))  # answers are trimmed; a blank one takes the default
    data = written(cwd)
    assert (data["owner"], data["repo"], data["project_owner"], data["project_number"], data["status_todo"]) == ("acme-demo", "demo", "octocat", 3, "Todo")


def test_a_blank_project_number_leaves_the_board_out(cwd):
    go(answers=("", "demo", "", "", "", "", "starter"))  # no project owner question without a project number
    assert written(cwd) == {"owner": "octocat", "repo": "demo", "status_todo": "Todo", "status_active": "In Progress", "status_done": "Done", "mode": "advisory"}


def test_flags_answer_every_question_and_nothing_is_asked(cwd):
    argv = ["--owner", "acme-demo", "--repo", "hello-spec", "--project", "7", "--project-owner", "octocat", "--status-todo", "Todo 1",
            "--status-active", "Doing", "--status-done", "Finished", "--profile", "starter"]
    assert go(argv, answers=(), interactive=False) == 0
    assert written(cwd)["owner"] == "acme-demo" and written(cwd)["project_owner"] == "octocat" and written(cwd)["status_todo"] == "Todo 1"


def test_without_a_terminal_and_without_flags_it_refuses_with_a_fixed_message(cwd, capsys):
    gh = Gh()
    for argv in ([], ["--owner", "acme"], ["--repo", "demo"], ["--profile", "team"]):
        with pytest.raises(ValueError, match="needs a terminal") as e:
            go(argv, answers=(), interactive=False, gh=gh)
        assert "--owner and --repo" in str(e.value)
    assert not (cwd / "ghp.config.json").exists() and gh.viewer_calls == 0
    assert main(["setup"], gh=Gh()) == 2  # under pytest stdin is not a terminal
    assert capsys.readouterr().err.startswith("error: E_TERMINAL: ghp setup asks questions and needs a terminal")


def test_every_refusal_names_its_code_like_the_other_verbs(cwd, capsys):
    codes = {k: v for k, v in vars(setup_cmd).items() if k.startswith("E_")}
    assert codes and all(v.startswith(k + ": ") for k, v in codes.items())
    (cwd / "ghp.config.json").write_text("{}")
    assert main(["setup", "--owner", "acme", "--repo", "demo"], gh=Gh()) == 2
    assert capsys.readouterr().err.startswith("error: E_EXISTS: ")


def test_a_missing_or_signed_out_gh_stops_before_any_question(cwd, capsys):
    for exc in (GhMissing(), GhError("E_GH_HTTP", 401, "Bad credentials (not signed in? run `gh auth login`)")):
        gh = Gh()
        gh.viewer = lambda exc=exc: (_ for _ in ()).throw(exc)
        assert main(["setup", "--owner", "acme", "--repo", "demo"], gh=gh) == 1
        err = capsys.readouterr().err
        assert err.startswith("error: E_GH_") and ("gh auth login" in err or "cli.github.com" in err)
    assert not (cwd / "ghp.config.json").exists()


def test_the_signed_in_login_is_printed_escaped(cwd, capsys):
    go(gh=Gh(Viewer(ME, "evil\x1b[2J‮login", [])), answers=("acme", "demo", "", "", "", "", "starter"))
    assert "\x1b" not in capsys.readouterr().out


def test_setup_never_reads_a_token_or_shells_out():
    src = (SRC / "setup_cmd.py").read_text()
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "getpass", "subprocess"):
        assert name not in src, name
    assert src.count("os.environ") == src.count('os.environ.get("GHP_CONFIG")') == 1  # the one variable ghp itself reads (where Config.load looks)
    assert ".arm(" not in src and "arm_or_refuse" not in src and "mutation" not in src


# ---- validation: nothing is written for a bad answer, and the answer is never echoed ----
def bad_case(**over):
    base = dict(owner="octocat", repo="demo", project="3", project_owner="octocat", todo="Todo", active="In Progress", done="Done")
    base.update(over)
    return (base["owner"], base["repo"], base["project"], base["project_owner"], base["todo"], base["active"], base["done"], "starter")


BAD = [
    ("owner", bad_case(owner="-bad"), "owner"), ("owner", bad_case(owner="a" * 40), "owner"), ("owner", bad_case(owner="a/b"), "owner"),
    ("owner", bad_case(owner="a\x1bb"), "owner"), ("repo", bad_case(repo=".."), "repo"), ("repo", bad_case(repo="a b"), "repo"),
    ("repo", bad_case(repo="x" * 101), "repo"), ("repo", bad_case(repo="a\nb"), "repo"),
    ("project", bad_case(project="0"), "project number"), ("project", bad_case(project="-2"), "project number"),
    ("project", bad_case(project="1.5"), "project number"), ("project", bad_case(project="abc"), "project number"),
    ("project", bad_case(project="٣"), "project number"), ("project", bad_case(project="1" * 10), "project number"),
    ("project", bad_case(project="3; rm"), "project number"),
    ("project_owner", bad_case(project_owner="x y"), "owner"),
    ("status", bad_case(todo="a\x1b[2Jb"), "status name"), ("status", bad_case(active="x" * 51), "status name"),
    ("status", bad_case(done="two\nlines"), "status name"), ("status", bad_case(todo="Same", active="Same"), "different"),
    ("status", bad_case(done="‮Done"), "status name"),
]


@pytest.mark.parametrize("what,answers,said", BAD, ids=[f"{w}-{i}" for i, (w, _, _) in enumerate(BAD)])
def test_a_bad_answer_is_refused_with_a_fixed_text_and_nothing_is_written(cwd, what, answers, said):
    with pytest.raises(ValueError) as e:
        go(answers=answers)
    assert said in str(e.value) and "\x1b" not in str(e.value) and not any(a and a in str(e.value) for a in answers if len(a) > 3)
    assert list(cwd.iterdir()) == []


@pytest.mark.parametrize("flag,value", [("--owner", "-bad"), ("--repo", ".."), ("--project", "0"), ("--project-owner", "a b"), ("--status-todo", "x\x01"),
                                       ("--profile", "bogus")])
def test_the_flags_are_validated_like_the_answers(cwd, flag, value, capsys):
    argv = ["--owner", "acme", "--repo", "demo", "--project", "3"] + [f"{flag}={value}"]
    if flag == "--profile":
        with pytest.raises(SystemExit):
            parse(argv)
        return
    with pytest.raises(ValueError):
        go(argv, answers=(), interactive=False)
    assert list(cwd.iterdir()) == []


def test_end_of_input_or_ctrl_c_writes_nothing(cwd):
    for exc in (EOFError, KeyboardInterrupt):
        def inp(prompt, exc=exc):
            raise exc
        with pytest.raises(ValueError, match="nothing was written"):
            setup_cmd.run_setup(parse([]), Gh(), input_fn=inp, interactive=True)
    assert list(cwd.iterdir()) == []


# ---- AC2: the three profiles ----
def test_setup_profiles_starter(cwd):
    go(answers=FIELDS[:-1] + ("starter",))
    assert written(cwd) == {"owner": "octocat", "repo": "demo", "project_owner": "octocat", "project_number": 3, "status_todo": "Todo",
                            "status_active": "In Progress", "status_done": "Done", "mode": "advisory"}


def test_setup_profiles_team_without_approvers_leaves_a_placeholder_and_stays_in_terminal_mode(cwd, capsys):
    prompts = []
    go(answers=FIELDS[:-1] + ("team", ""), prompts=prompts)
    data = written(cwd)
    assert data["mode"] == "advisory" and data["gate"] == {"block": ["ac_traceability", "open_questions"]} and data["review_block_on"] == ["blocker"]
    assert data["approvers"] == [] and "approval" not in data
    out = capsys.readouterr().out
    assert "gh api users/<login> --jq .id" in out and "numeric" in out and ("target branch" in out or "branch you merge into" in out)
    ask = prompts[-1]
    assert "numeric" in ask and "login" in ask
    cfg = Config.load(str(cwd))
    assert cfg.approvers == [] and cfg.approval == "terminal"


def test_setup_profiles_team_with_approvers_turns_comment_mode_on(cwd):
    go(answers=FIELDS[:-1] + ("team", "777, 888 999"))
    data = written(cwd)
    assert data["approval"] == "comment" and data["approvers"] == [777, 888, 999] and all(type(a) is int for a in data["approvers"])
    cfg = Config.load(str(cwd))
    assert cfg.approvers == [777, 888, 999] and cfg.approval == "comment"


def test_the_approver_flag_is_repeatable_and_numeric_only(cwd):
    base = ["--owner", "acme", "--repo", "demo", "--profile", "team"]
    go(base + ["--approver", "11", "--approver", "12"], answers=(), interactive=False)
    assert written(cwd)["approvers"] == [11, 12]


@pytest.mark.parametrize("typed", ["octocat", "@octocat", "12 octocat", "0", "-5", "1.5", "12,12", "٣", "1" * 17, "12;", ",".join(str(i) for i in range(1, 22))])
def test_an_approver_that_is_not_a_numeric_user_id_is_refused_and_never_written(cwd, typed):
    with pytest.raises(ValueError) as e:
        go(answers=FIELDS[:-1] + ("team", typed))
    assert "numeric user id" in str(e.value) and "octocat" not in str(e.value)
    assert list(cwd.iterdir()) == []


def test_an_approver_cannot_be_the_signed_in_identity(cwd):
    with pytest.raises(ValueError, match="signed in as"):
        go(answers=FIELDS[:-1] + ("team", f"5, {ME}"))
    assert list(cwd.iterdir()) == []


def test_setup_never_guesses_an_approver_id(cwd):
    go(answers=FIELDS[:-1] + ("team", ""))
    assert str(ME) not in (cwd / "ghp.config.json").read_text()  # the signed-in id is not offered as an approver


def test_setup_profiles_strict_asks_for_the_test_command(cwd):
    prompts = []
    go(answers=FIELDS[:-1] + ("strict", "python3 -m pytest -q", ""), prompts=prompts)
    data = written(cwd)
    assert data["mode"] == "strict" and data["strict"] == {"test_command": "python3 -m pytest -q"} and data["approvers"] == [] and "approval" not in data
    assert "test" in prompts[-2] and "numeric" in prompts[-1] and Config.load(str(cwd)).mode == "strict"


def test_setup_profiles_strict_blank_command_is_left_unset_and_said(cwd, capsys):
    go(answers=FIELDS[:-1] + ("strict", "", ""))
    assert "strict" not in written(cwd) or "test_command" not in written(cwd)["strict"]
    assert "strict.test_command" in capsys.readouterr().out


def test_setup_profiles_strict_leaves_the_command_to_an_approved_stack_profile(cwd, capsys):
    (cwd / "ghp.stack.json").write_text(json.dumps(approve(profile())))
    go(answers=FIELDS[:-1] + ("strict", ""))  # no test-command question; the approvers question is asked
    assert "test_command" not in json.dumps(written(cwd))
    out = capsys.readouterr().out
    assert "ghp stack approve --apply" in out and "NOT in the new file" in out


@pytest.mark.parametrize("cmd", ["x" * 501, "run\x1b[2J", "a\nb"])
def test_a_bad_test_command_is_refused(cwd, cmd):
    with pytest.raises(ValueError, match="test command"):
        go(answers=FIELDS[:-1] + ("strict", cmd))
    assert list(cwd.iterdir()) == []


def test_setup_profiles_unknown_name_is_refused_in_the_prompt_and_the_flag(cwd):
    with pytest.raises(ValueError, match="profile"):
        go(answers=FIELDS[:-1] + ("bogus",))
    assert list(cwd.iterdir()) == []


def test_every_profile_loads_as_a_valid_config(cwd):
    for name, extra in (("starter", ()), ("team", ("",)), ("strict", ("", ""))):
        (cwd / "ghp.config.json").unlink(missing_ok=True)
        go(answers=FIELDS[:-1] + (name,) + extra)
        assert isinstance(Config.load(str(cwd)), Config)
    assert set(setup_cmd.PROFILES) == {"starter", "team", "strict"}


# ---- AC3: no overwrite without --force ----
def test_setup_does_not_overwrite_without_force(cwd, capsys):
    path = cwd / "ghp.config.json"
    path.write_text('{"owner": "keep", "repo": "me"}\n')
    before, gh = path.read_bytes(), Gh()
    with pytest.raises(ValueError, match="--force"):
        go(answers=(), gh=gh)
    assert path.read_bytes() == before and gh.viewer_calls == 0 and sorted(p.name for p in cwd.iterdir()) == ["ghp.config.json"]
    assert main(["setup", "--owner", "a", "--repo", "b"], gh=gh) == 2
    assert path.read_bytes() == before and "--force" in capsys.readouterr().err
    for bad in ("", "{ not json", "[]"):  # whatever the file holds, it is not replaced
        path.write_text(bad)
        with pytest.raises(ValueError, match="--force"):
            go(answers=())
        assert path.read_text() == bad


def test_force_replaces_the_file_whole_and_says_so(cwd, capsys):
    path = cwd / "ghp.config.json"
    path.write_text(json.dumps({"owner": "old", "repo": "old", "approvers": [5], "approval": "comment", "extra": 1}))
    assert go(["--force"]) == 0
    assert written(cwd)["owner"] == "octocat" and "approvers" not in written(cwd) and "extra" not in written(cwd)
    assert "replaced" in capsys.readouterr().out
    assert [p.name for p in cwd.iterdir()] == ["ghp.config.json"]


def test_a_dangling_symlink_and_a_directory_are_not_overwritten_without_force(cwd):
    os.symlink(cwd / "nowhere", cwd / "ghp.config.json")
    with pytest.raises(ValueError, match="--force"):
        go(answers=())
    assert (cwd / "ghp.config.json").is_symlink()
    (cwd / "ghp.config.json").unlink()
    (cwd / "ghp.config.json").mkdir()
    with pytest.raises(ValueError, match="--force"):
        go(answers=())
    with pytest.raises(ValueError, match="could not be written"):
        go(["--force"])  # a directory cannot be replaced by a file
    assert (cwd / "ghp.config.json").is_dir() and [p.name for p in cwd.iterdir()] == ["ghp.config.json"]


def test_force_on_a_symlink_replaces_the_link_not_its_target(cwd):
    target = cwd / "elsewhere.json"
    target.write_text("keep")
    os.symlink(target, cwd / "ghp.config.json")
    go(["--force"])
    assert target.read_text() == "keep" and not (cwd / "ghp.config.json").is_symlink() and written(cwd)["repo"] == "demo"


def test_a_file_that_appears_while_the_questions_are_answered_is_not_overwritten(cwd):
    def create(prompt):
        (cwd / "ghp.config.json").write_text("someone else's\n")
        return ""
    answers = iter(FIELDS)
    with pytest.raises(ValueError, match="--force"):
        setup_cmd.run_setup(parse([]), Gh(), input_fn=lambda p: (create(p), next(answers))[1], interactive=True)
    assert (cwd / "ghp.config.json").read_text() == "someone else's\n" and [p.name for p in cwd.iterdir()] == ["ghp.config.json"]


# ---- the write: 0644, atomic, nothing left behind ----
def test_the_file_is_mode_0644_whatever_the_umask_and_no_temp_file_is_left(cwd):
    old = os.umask(0o077)
    try:
        go()
    finally:
        os.umask(old)
    path = cwd / "ghp.config.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o644 and [p.name for p in cwd.iterdir()] == ["ghp.config.json"]
    assert path.read_text().endswith("}\n")


def test_a_failed_write_leaves_the_old_file_and_no_temp_file(cwd, monkeypatch):
    path = cwd / "ghp.config.json"
    path.write_text("old\n")
    with monkeypatch.context() as m:
        m.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(ValueError, match="could not be written") as e:
            go(["--force"])
    assert "disk full" not in str(e.value) and path.read_text() == "old\n" and [p.name for p in cwd.iterdir()] == ["ghp.config.json"]
    path.unlink()
    with monkeypatch.context() as m:
        m.setattr(os, "link", lambda *a: (_ for _ in ()).throw(PermissionError("no links here")))
        with pytest.raises(ValueError, match="could not be written"):
            go()
    assert list(cwd.iterdir()) == []


def test_what_is_written_is_checked_by_the_config_validator_first(cwd, monkeypatch):
    monkeypatch.setitem(setup_cmd.PROFILES, "starter", {"mode": "bogus"})
    with pytest.raises(Exception) as e:
        go()
    assert "bogus" not in str(e.value) and list(cwd.iterdir()) == []


def test_the_written_names_hold_no_control_character(cwd):
    go(answers=("octocat", "demo", "3", "", "Thé Todo", "En cours", "Fini", "starter"))
    assert written(cwd)["status_todo"] == "Thé Todo"
    assert Config.load(str(cwd)).status_todo == "Thé Todo"


# ---- wiring ----
def test_the_verb_is_registered_with_its_flags(capsys):
    import ghp.__main__ as cli
    assert "setup" in cli.REGISTRY
    with pytest.raises(SystemExit):
        main(["setup", "--help"])
    out = capsys.readouterr().out
    for flag in ("--owner", "--repo", "--project", "--project-owner", "--status-todo", "--status-active", "--status-done", "--profile", "--approver", "--force"):
        assert flag in out
    assert "organization" in out.lower()  # the help says only user-owned Projects work


def test_setup_end_to_end_through_main_with_flags(cwd, capsys):
    gh = Gh()
    assert main(["setup", "--owner", "acme-demo", "--repo", "hello-spec", "--project", "3", "--project-owner", "octocat"], gh=gh) == 0
    assert written(cwd)["project_number"] == 3 and not gh.armed
    assert "ghp doctor" in capsys.readouterr().out


# ---- review round 1: I2 (strict asks approvers; --approver is never silently ignored) ----
def test_setup_profiles_strict_asks_for_approvers_like_team(cwd, capsys):
    prompts = []
    go(answers=FIELDS[:-1] + ("strict", "python3 -m pytest -q", "777, 888"), prompts=prompts)
    data = written(cwd)
    assert data["mode"] == "strict" and data["approval"] == "comment" and data["approvers"] == [777, 888] and all(type(a) is int for a in data["approvers"])
    assert "numeric" in prompts[-1] and "login" in prompts[-1]
    assert "gh api users/<login> --jq .id" in capsys.readouterr().out
    cfg = Config.load(str(cwd))
    assert cfg.approvers == [777, 888] and cfg.mode == "strict"


@pytest.mark.parametrize("typed", ["octocat", "@octocat", "12 octocat", "0", "-5", "12,12", "\u0663", "1" * 17, ",".join(str(i) for i in range(1, 22))])
def test_strict_refuses_the_same_bad_approvers_as_team_and_writes_nothing(cwd, typed):
    with pytest.raises(ValueError, match="numeric user id") as e:
        go(answers=FIELDS[:-1] + ("strict", "", typed))
    assert "octocat" not in str(e.value) and list(cwd.iterdir()) == []


def test_strict_refuses_the_signed_in_identity_as_an_approver(cwd):
    with pytest.raises(ValueError, match="signed in as"):
        go(answers=FIELDS[:-1] + ("strict", "", f"{ME}"))
    assert list(cwd.iterdir()) == []


def test_the_approver_flag_works_for_strict_and_team(cwd):
    for profile_name in ("strict", "team"):
        (cwd / "ghp.config.json").unlink(missing_ok=True)
        go(["--owner", "acme", "--repo", "demo", "--profile", profile_name, "--approver", "11"], answers=(), interactive=False)
        assert written(cwd)["approvers"] == [11] and written(cwd)["approval"] == "comment"


def test_the_approver_flag_with_the_starter_profile_is_refused_not_ignored(cwd):
    base = ["--owner", "acme", "--repo", "demo", "--approver", "11"]
    for extra in (["--profile", "starter"], []):  # an unset profile is starter when nothing is asked
        with pytest.raises(ValueError, match="--approver is for the team and strict profiles"):
            go(base + extra, answers=(), interactive=False)
    with pytest.raises(ValueError, match="--approver is for the team and strict profiles"):  # a profile answered at the prompt too
        go(["--approver", "11"], answers=FIELDS[:-1] + ("starter",))
    assert list(cwd.iterdir()) == []


# ---- M1/M2: the stack profile is read from the current folder; --force keeps or says it dropped strict.test_command ----
def approved_stack(cwd):
    (cwd / "ghp.stack.json").write_text(json.dumps(approve(profile())))


def test_force_keeps_a_valid_test_command_of_the_old_file_when_the_stack_is_approved(cwd, capsys):
    approved_stack(cwd)
    (cwd / "ghp.config.json").write_text(json.dumps({"owner": "old", "repo": "old", "mode": "strict", "strict": {"test_command": "make test", "test_timeout": 900}}))
    go(["--force"], answers=FIELDS[:-1] + ("strict", ""))
    assert written(cwd)["strict"] == {"test_command": "make test"}  # the command only; nothing else of the old file
    out = capsys.readouterr().out
    assert "was kept" in out and "NOT in the new file" not in out


@pytest.mark.parametrize("old", [{"strict": {"test_command": "x" * 501}}, {"strict": {"test_command": "a\x1bb"}}, {"strict": {"test_command": 5}},
                                 {"strict": {"test_command": ""}}, {"strict": []}, {"strict": "make"}, {"mode": "strict"}, []])
def test_force_drops_an_invalid_or_missing_old_command_and_says_so(cwd, capsys, old):
    approved_stack(cwd)
    (cwd / "ghp.config.json").write_text(json.dumps(old))
    go(["--force"], answers=FIELDS[:-1] + ("strict", ""))
    assert "test_command" not in json.dumps(written(cwd))
    out = capsys.readouterr().out
    assert "NOT in the new file" in out and "was kept" not in out and "ghp stack approve --apply" in out


def test_force_does_not_keep_the_command_of_a_symlinked_old_file(cwd, capsys):
    approved_stack(cwd)
    other = cwd / "elsewhere.json"
    other.write_text(json.dumps({"strict": {"test_command": "make test"}}))
    os.symlink(other, cwd / "ghp.config.json")
    go(["--force"], answers=FIELDS[:-1] + ("strict", ""))
    assert "test_command" not in json.dumps(written(cwd)) and "NOT in the new file" in capsys.readouterr().out


def test_without_an_approved_stack_the_old_command_is_not_reused_and_the_question_is_asked(cwd):
    (cwd / "ghp.config.json").write_text(json.dumps({"strict": {"test_command": "make old"}}))
    go(["--force"], answers=FIELDS[:-1] + ("strict", "make new", ""))
    assert written(cwd)["strict"] == {"test_command": "make new"}


def test_the_stack_status_is_read_from_the_current_folder_not_from_a_symlink_target(cwd, capsys):
    elsewhere = cwd / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "ghp.stack.json").write_text(json.dumps(approve(profile())))  # approved, but not in the folder setup runs in
    (elsewhere / "ghp.config.json").write_text("{}")
    work = cwd / "work"
    work.mkdir()
    os.symlink(elsewhere / "ghp.config.json", work / "ghp.config.json")
    os.chdir(work)
    prompts = []
    go(["--force"], answers=FIELDS[:-1] + ("strict", "make test", ""), prompts=prompts)
    assert any("tests in strict mode" in p for p in prompts) and written(work)["strict"] == {"test_command": "make test"}


def test_the_stack_read_names_the_current_folder_in_the_source(cwd):
    # same property, pinned in the source: the folder is Path.cwd()
    assert 'read_profile(str(Path.cwd()))["status"] == "approved"' in (SRC / "setup_cmd.py").read_text()


# ---- M4: flag values are not trimmed silently, and status names differ case-insensitively ----
@pytest.mark.parametrize("flag,value", [("--status-done", " Done"), ("--status-done", "Done "), ("--status-todo", "\tTodo"), ("--status-active", "Doing\n"),
                                       ("--owner", " acme"), ("--repo", "demo "), ("--project", " 3"), ("--project-owner", "octocat\u00a0"), ("--status-done", "Do\x01ne")])
def test_a_flag_value_with_edge_whitespace_or_a_control_character_is_refused(cwd, flag, value):
    argv = ["--owner", "acme", "--repo", "demo", "--project", "3"] + [f"{flag}={value}"]
    with pytest.raises(ValueError, match="must not start or end with a space") as e:
        go(argv, answers=(), interactive=False)
    assert value not in str(e.value) and list(cwd.iterdir()) == []


def test_status_names_that_differ_only_in_case_are_the_same_name(cwd):
    for names in (("Todo", "todo", "Done"), ("Todo", "Doing", "doing"), ("Todo", "Doing", "Todo")):
        with pytest.raises(ValueError, match="different"):
            go(["--owner", "acme", "--repo", "demo", "--status-todo", names[0], "--status-active", names[1], "--status-done", names[2]], answers=(), interactive=False)
    with pytest.raises(ValueError, match="different"):
        go(answers=("octocat", "demo", "", "Same", "same", "Other", "starter"))
    assert list(cwd.iterdir()) == []


# ---- M5: where the file is written and where Config.load will read ----
def printed(capsys):
    return capsys.readouterr().out


def test_outside_a_git_repository_setup_says_so_in_one_line(cwd, capsys):
    go()
    notes = [ln for ln in printed(capsys).splitlines() if ln.startswith("Note: no git repository")]
    assert len(notes) == 1 and "top folder" in notes[0]


def test_in_the_top_folder_of_a_repository_there_is_no_location_note(cwd, capsys, git_env):
    git(cwd, "init", "-q", "-b", "main")
    go()
    out = printed(capsys)
    assert "no git repository" not in out and "not the top folder" not in out


def test_in_a_subfolder_of_a_repository_setup_says_it_is_not_the_top_folder(cwd, capsys, git_env):
    git(cwd, "init", "-q", "-b", "main")
    sub = cwd / "pkg"
    sub.mkdir()
    os.chdir(sub)
    go()
    notes = [ln for ln in printed(capsys).splitlines() if "not the top folder" in ln]
    assert len(notes) == 1 and os.path.realpath(cwd) in notes[0]


def test_with_ghp_config_set_setup_says_which_file_ghp_will_read(cwd, capsys, monkeypatch, git_env):
    git(cwd, "init", "-q", "-b", "main")
    monkeypatch.setenv("GHP_CONFIG", str(cwd / "other.json"))
    go()
    out = printed(capsys)
    notes = [ln for ln in out.splitlines() if "GHP_CONFIG is set" in ln]
    assert len(notes) == 1 and "other.json" in notes[0] and "NOT the file written here" in notes[0]
    assert f"Wrote {os.path.join(os.getcwd(), 'ghp.config.json')}:" in out  # the written path is printed in full


def test_without_ghp_config_there_is_no_such_note_and_the_written_path_is_full(cwd, capsys, monkeypatch, git_env):
    git(cwd, "init", "-q", "-b", "main")
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    go()
    out = printed(capsys)
    assert "GHP_CONFIG" not in out
    wrote = next(ln for ln in out.splitlines() if ln.startswith("Wrote "))
    assert wrote.startswith(f"Wrote {os.path.join(os.getcwd(), 'ghp.config.json')}:")


def test_the_location_notes_are_escaped(cwd, capsys, monkeypatch):
    monkeypatch.setenv("GHP_CONFIG", "/x/\x1b[2Jy\u202e.json")
    go()
    out = printed(capsys)
    assert "\x1b" not in out and "\u202e" not in out


def test_where_notes_escape_the_path_themselves(cwd, monkeypatch):
    monkeypatch.setenv("GHP_CONFIG", "/x/\x1b[2Jy‮.json")
    notes = [n for n in setup_cmd._where_notes() if "GHP_CONFIG" in n]
    assert len(notes) == 1 and "\x1b" not in notes[0] and "‮" not in notes[0]
