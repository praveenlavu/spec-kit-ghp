import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import ghp.__main__ as cli
from conftest import FakeGh
from ghp import plan
from ghp.gh import GhError

# The verbs registered in ghp.__main__.REGISTRY. Every task that adds a verb adds its name here (and, if it needs
# arguments to run, an entry in VERB_ARGV).
VERBS = ["get", "comments", "project", "stack", "dod", "rules", "questions", "decisions", "push", "activate", "branch", "pr", "gate", "calibrate", "kits", "strict", "done", "status", "approvals", "doctor", "setup"]
VERB_ARGV = {"get": ["not-a-ref"], "comments": ["not-a-ref"], "project": ["show"], "stack": ["init", "--seed", "nope"], "dod": ["not-a-ref"], "rules": ["not-a-ref"], "questions": ["not-a-ref"], "decisions": ["not-a-ref"], "push": ["not-a-ref"], "activate": ["not-a-ref"], "branch": ["not-a-ref"], "pr": ["create", "not-a-ref"], "gate": ["not-a-ref"], "calibrate": ["--score", ".ghp/a.json"], "kits": ["verify"], "strict": ["mode"], "done": ["not-a-ref"], "status": ["not-a-ref"], "approvals": ["check", "not-a-ref", "--gate", "review-spec"]}  # refused before any call: the canary loop runs every verb against an empty FakeGh


def _help_verbs(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    section = out.split("verbs:", 1)[1] if "verbs:" in out else ""
    return re.findall(r"^    (\S+)", section, re.M)


def test_help_lists_exactly_the_registered_verbs(capsys):
    assert sorted(_help_verbs(capsys)) == sorted(VERBS)
    assert sorted(cli.REGISTRY) == sorted(VERBS)  # nothing is registered that the list above does not know


def test_version_flag(capsys):
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out == "ghp 0.1.1\n"


def test_version_flag_via_module():
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parent.parent / "src"))
    r = subprocess.run([sys.executable, "-m", "ghp", "--version"],
                       capture_output=True, text=True, env=env)
    assert (r.returncode, r.stdout) == (0, "ghp 0.1.1\n")


# ---- the frame: test-only verbs are put in the registry by the tests (monkeypatch), never shipped ----
CREATE = ("POST", "repos/octo/demo/issues")


def _register(monkeypatch, name, run, write=False):
    def reg(sub):
        p = sub.add_parser(name, help="test verb")
        if write:
            plan.add_write_flags(p)
        return p
    monkeypatch.setitem(cli.REGISTRY, name, (reg, run))


def _write_verb(args, gh):
    plan.arm_or_refuse(args, gh, "create issue 'Fictional'\n")
    if args.apply:
        gh.rest(*CREATE, {"title": "Fictional"})
    return 0


def test_apply_flag_is_the_only_thing_that_arms_gh(tmp_path, monkeypatch):
    _register(monkeypatch, "_w", _write_verb, write=True)
    gh = FakeGh().on_rest(*CREATE, {"id": 1})
    assert cli.main(["_w"], gh=gh) == 0
    assert not gh.armed and gh.calls == []
    gh = FakeGh().on_rest(*CREATE, {"id": 1})
    assert cli.main(["_w", "--apply"], gh=gh) == 0
    assert gh.armed and [c[1:3] for c in gh.calls] == [CREATE]
    gh = FakeGh().on_rest(*CREATE, {"id": 1})
    assert cli.main(["_w", "--workflow", "--plan-out", str(tmp_path / "p.md")], gh=gh) == 0  # neither flag arms
    assert not gh.armed


def test_expect_plan_mismatch_exits_2_and_writes_new_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _register(monkeypatch, "_w", _write_verb, write=True)
    (tmp_path / "p.md").write_text("an older plan\n")
    gh = FakeGh().on_rest(*CREATE, {"id": 1})
    assert cli.main(["_w", "--apply", "--expect-plan", "p.md"], gh=gh) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: plan changed since approval") and err.count("\n") == 1 and "Traceback" not in err
    assert not gh.armed and gh.calls == []
    assert (tmp_path / "p.md").read_text() == "an older plan\n"
    assert (tmp_path / "p.md.new").read_text() == "create issue 'Fictional'\n"


def test_expect_plan_proceeds_when_the_plan_matches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _register(monkeypatch, "_w", _write_verb, write=True)
    assert cli.main(["_w", "--plan-out", "p.md"], gh=FakeGh()) == 0
    gh = FakeGh().on_rest(*CREATE, {"id": 1})
    assert cli.main(["_w", "--apply", "--expect-plan", "p.md"], gh=gh) == 0
    assert gh.armed and len(gh.calls) == 1


def test_expect_plan_needs_apply(monkeypatch, capsys):
    _register(monkeypatch, "_w", _write_verb, write=True)
    with pytest.raises(SystemExit) as e:
        cli.main(["_w", "--expect-plan", "p.md"], gh=FakeGh())
    assert e.value.code == 2 and "--expect-plan needs --apply" in capsys.readouterr().err


def test_unknown_verb_exits_2(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["nope"])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "nope" in err and "Traceback" not in err
    with pytest.raises(SystemExit) as e:  # no verb at all is the same refusal
        cli.main([])
    assert e.value.code == 2


def test_gh_error_prints_one_line_no_traceback(monkeypatch, capsys):
    def boom(args, gh):
        raise GhError("E_GH_HTTP", 404, "Not Found")
    _register(monkeypatch, "_boom", boom)
    assert cli.main(["_boom"], gh=FakeGh()) == 1
    seen = capsys.readouterr()
    assert seen.out == "" and seen.err == "error: E_GH_HTTP (HTTP 404): Not Found\n"


def test_main_maps_invalid_config_to_error_exit_2(tmp_path, monkeypatch, capsys):
    from ghp.config import Config
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", "mode": "bogus"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    _register(monkeypatch, "_cfg", lambda args, gh: (Config.load(), 0)[1])
    assert cli.main(["_cfg"], gh=FakeGh()) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: ") and err.count("\n") == 1 and "Traceback" not in err
    assert "bogus" not in err


def test_exit_codes_are_pinned(monkeypatch):
    """0 done, 1 GitHub failed (GhError), 2 refused with nothing written (plan changed, invalid config, usage)."""
    _register(monkeypatch, "_ok", lambda a, g: 0)
    _register(monkeypatch, "_one", lambda a, g: 1)
    assert (cli.main(["_ok"], gh=FakeGh()), cli.main(["_one"], gh=FakeGh())) == (0, 1)


CANARY = "gh" + "p_" + "A" * 36  # built at run time so this file holds no token-shaped string


def _everything_under(root):
    return "".join(p.read_text(errors="replace") for p in Path(root).rglob("*") if p.is_file())


def test_no_verb_output_contains_the_canary_token(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for var in ("GH_TOKEN", "GITHUB_TOKEN", "GHP_TOKEN"):
        monkeypatch.setenv(var, CANARY)

    def leak(args, gh):  # a failing call whose GitHub message echoes the token must come out scrubbed
        raise GhError("E_GH_HTTP", 401, "Authorization: token " + CANARY + " rejected " + CANARY)
    _register(monkeypatch, "_leak", leak)
    runs = [[v] + VERB_ARGV.get(v, []) for v in VERBS] + [["_leak"], ["--version"]]
    for argv in runs:
        try:
            cli.main(argv, gh=FakeGh())
        except SystemExit:
            pass
        seen = capsys.readouterr()
        assert CANARY not in seen.out + seen.err, argv
    assert CANARY not in _everything_under(tmp_path)  # .ghp/ logs and anything else the run wrote


def test_empty_expect_plan_exits_2_and_arms_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _register(monkeypatch, "_w", _write_verb, write=True)
    gh = FakeGh().on_rest(*CREATE, {"id": 1})
    assert cli.main(["_w", "--apply", "--expect-plan", ""], gh=gh) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: ") and err.count("\n") == 1 and not gh.armed and gh.calls == []
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(SystemExit) as e:  # without --apply the empty value is still caught at parse time
        cli.main(["_w", "--expect-plan", ""], gh=FakeGh())
    assert e.value.code == 2


def test_unwritable_plan_out_exits_2_one_line(tmp_path, monkeypatch, capsys):
    (tmp_path / "file").write_text("x")
    _register(monkeypatch, "_w", _write_verb, write=True)
    gh = FakeGh().on_rest(*CREATE, {"id": 1})
    assert cli.main(["_w", "--apply", "--plan-out", str(tmp_path / "file" / "p.md")], gh=gh) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: ") and err.count("\n") == 1 and "Traceback" not in err
    assert not gh.armed and gh.calls == []


def test_value_error_exits_2_one_line_and_scrubbed(monkeypatch, capsys):
    def bad(args, gh):
        raise ValueError("bad value " + CANARY + "\nsecond line")
    _register(monkeypatch, "_bad", bad)
    assert cli.main(["_bad"], gh=FakeGh()) == 2
    err = capsys.readouterr().err
    assert err == "error: bad value *** second line\n"
