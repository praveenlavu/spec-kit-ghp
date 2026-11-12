"""`ghp stack approve` (V3 piece 2, P2-4): the human's approval of a profile, and the one write of `strict.test_command`.

It is the step that changes what strict CI runs, so these tests check: a terminal is needed before any file is read (a guard against
accidents, not a security boundary), a non-terminal preview never prints profile text, a terminal preview shows every command verbatim,
the config keeps every other key, value and order, a refusal leaves every file as it was, and a failed step is rolled back."""
import copy
import datetime
import importlib.util
import json
import os
import pty
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from ghp import decisions, stack
from ghp.__main__ import main
from ghp.config import Config
from conftest import TypedStdin, approve, git, profile, run_in_pty, shown_code, terminal
from test_stack_show import MARKER, put

ROOT = Path(__file__).resolve().parent.parent
TODAY = datetime.date(2026, 10, 31)
REAL_TODAY = stack._today  # a real child process uses the real clock: a test that reads its result back must not pin the date
# T10 deviation: tests that need a module a later task ports are gated on that module, not rewritten; each gate lifts itself when the module lands.
needs_setup_cmd = pytest.mark.skipif(not (Path(__file__).resolve().parent.parent / "src/ghp/setup_cmd.py").exists(), reason="needs ghp.setup_cmd (T30)")
needs_setup_md = pytest.mark.skipif(not (Path(__file__).resolve().parent.parent / "commands/setup.md").exists(), reason="needs commands/setup.md (T32)")
CONFIG = {"owner": "example-org", "repo": "example-repo", "mode": "strict", "strict": {"test_timeout": 900}}


@pytest.fixture
def repo(tmp_path, monkeypatch, git_env):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    monkeypatch.setattr(stack, "_today", lambda: TODAY)
    git(tmp_path, "init", "-q")
    return tmp_path


def tty(monkeypatch, on=True, out=None):
    """stdin (and, unless `out` says otherwise, stdout) is a terminal or not. A terminal stdin types the approval code it reads off the screen."""
    if on:
        monkeypatch.setattr(sys, "stdin", TypedStdin())
    else:
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: on if out is None else out)


def config_text(cfg=CONFIG, indent=2, eol="\n", final=True, ascii_only=False):
    text = json.dumps(cfg, indent=indent, ensure_ascii=ascii_only).replace("\n", eol)
    return text + (eol if final else "")


def setup(repo, p=None, cfg=CONFIG, **layout):
    """A draft profile and a config in `repo`; returns the config text written."""
    put(repo, profile() if p is None else p)
    text = cfg if isinstance(cfg, str) else config_text(cfg, **layout)
    with open(repo / "ghp.config.json", "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return text


def snapshot(repo):
    return {str(p.relative_to(repo)): p.read_bytes() for p in sorted(repo.rglob("*")) if p.is_file() and ".git" not in p.parts}


def approve_cli(capsys, *flags):
    code = main(["stack", "approve", *flags])
    out = capsys.readouterr()
    return code, out.out, out.err


def result(out):
    """The JSON result that `--apply` prints after the preview and the typed code."""
    return json.loads(out[out.rindex('{\n  "applied"'):])


def read_config(repo):
    return json.loads((repo / "ghp.config.json").read_text(encoding="utf-8"))


def ordered(v):
    """`v` with every object as a list of (key, value) pairs, so a comparison sees key order too."""
    if isinstance(v, dict):
        return [(k, ordered(x)) for k, x in v.items()]
    return [ordered(x) for x in v] if isinstance(v, list) else v


def parse_ordered(text):
    return ordered(json.loads(text, object_pairs_hook=lambda pairs: dict(pairs)))


def all_lines(md):
    parsed = decisions.parse_decisions(md)
    return sorted(parsed["open"] + parsed["validated"] + parsed["rejected"], key=lambda d: int(d["id"][1:]))


def two_stack_profile(run_a="echo a", run_b="echo b"):
    return profile(stacks=[
        {"id": "api", "root": "backend", "commands": {"test": {"run": run_a, "source": "S1"}, "org_only": [{"run": "sf apex run test", "source": "unverified"}]},
         "conventions": [{"rule": "Keep modules small", "source": "S1"}]},
        {"id": "web", "root": "web/app", "commands": {"test": {"run": run_b, "source": "unverified"}}}])


# ---- the terminal rule ----
def test_apply_refuses_without_tty(repo, monkeypatch, capsys):
    setup(repo)
    before = snapshot(repo)

    def boom(*a, **k):
        raise AssertionError("a file or the config was read before the terminal check")
    for target in ("read_profile", "plan_approve", "apply_approve"):
        monkeypatch.setattr(stack, target, boom)
    monkeypatch.setattr(Config, "load", boom)
    tty(monkeypatch, False)
    code, out, err = approve_cli(capsys, "--apply")
    assert code == 2 and out == ""
    assert "only a human may do" in err and "not a security boundary" in err
    assert snapshot(repo) == before


def test_apply_refuses_without_tty_in_a_real_process(repo):
    setup(repo)
    before = snapshot(repo)
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    env.pop("GHP_CONFIG", None)
    res = subprocess.run([sys.executable, "-m", "ghp", "stack", "approve", "--apply"], cwd=repo, env=env, stdin=subprocess.DEVNULL,
                         capture_output=True, text=True)
    assert res.returncode == 2 and res.stdout == "" and "only a human may do" in res.stderr
    assert snapshot(repo) == before


def test_apply_refuses_with_ghp_config_env(repo, monkeypatch, capsys, tmp_path_factory):
    setup(repo)
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "ghp.config.json"
    elsewhere.write_text(config_text(), encoding="utf-8")
    monkeypatch.setenv("GHP_CONFIG", str(elsewhere))
    tty(monkeypatch)
    before, other = snapshot(repo), elsewhere.read_bytes()
    code, out, err = approve_cli(capsys, "--apply")
    assert code == 2 and "GHP_CONFIG" in err and "ghp.config.json" in err
    assert snapshot(repo) == before and elsewhere.read_bytes() == other
    with pytest.raises(ValueError, match="GHP_CONFIG"):  # the library refuses too, not only the CLI
        stack.apply_approve(".", "ghp.config.json")
    assert snapshot(repo) == before


# ---- the preview ----
def test_preview_non_tty_prints_no_text(repo, monkeypatch, capsys):
    p = two_stack_profile(run_a=f"echo {MARKER}")
    p["stacks"][0]["conventions"][0]["rule"] = f"rule {MARKER}"
    p["sources"]["S1"]["url"] = f"https://docs.pytest.org/{MARKER}"
    setup(repo, p)
    before = snapshot(repo)
    tty(monkeypatch, False)
    code, out, err = approve_cli(capsys)
    assert code == 0 and err == "" and MARKER not in out and "echo" not in out and "docs.pytest.org" not in out and "backend" not in out
    shown = result(out)
    composed = stack.compose_test_command(p)
    assert shown["applied"] is False and shown["version"] == 3 and shown["unverified"] == 2
    assert shown["counts"]["stacks"] == 2 and shown["counts"]["commands"] == 3
    assert shown["test_command"] == {"set": True, "length": len(composed)}
    assert snapshot(repo) == before


def test_preview_tty_renders_commands_verbatim(repo, monkeypatch, capsys):
    hostile = "true) ; exit 0 ; (true"  # passes the validator; must be shown quoted, in full
    p = two_stack_profile(run_a=hostile)
    p["stacks"][1]["commands"]["lint"] = {"run": "echo 'a -- b' ~~~ x --> y", "source": "S1"}
    p["stacks"][0]["conventions"][0]["rule"] = "Keep‎ modules️ small"
    setup(repo, p)
    before = snapshot(repo)
    tty(monkeypatch)
    code, out, err = approve_cli(capsys)
    assert code == 0 and err == ""
    composed = stack.compose_test_command(p)
    assert hostile in out and "sh -c 'true) ; exit 0 ; (true'" in composed
    assert composed in out  # the whole composed line, not a cut one
    for run in ("echo b", "echo 'a -- b' ~~~ x --> y", "sf apex run test"):  # every stack's commands, verbatim (no markup stripping)
        assert run in out, run
    org = next(line for line in out.splitlines() if "org_only[0]" in line)
    assert "explicit yes" in org and "live org" in org
    assert "unverified" in out.lower() and "official" in out and "https://docs.pytest.org/en/stable/" in out
    assert "Keep modules small" in out and "‎" not in out and "️" not in out
    assert "is not a security boundary" in out and "record" in out
    assert snapshot(repo) == before  # the text is printed, never stored


def test_preview_tty_says_what_it_will_write_and_what_it_removes(repo, monkeypatch, capsys):
    p = profile()
    p["stacks"][0]["commands"]["test"] = {"run": None, "why": "Apex only"}
    setup(repo, p, {**CONFIG, "strict": {"test_command": "make old", "test_timeout": 900}})
    tty(monkeypatch)
    code, out, err = approve_cli(capsys)
    assert code == 0 and "left unset" in out and "no stack defines a test command" in out and "make old" in out and "removed" in out


def test_preview_text_strips_terminal_escapes_from_any_profile_text():
    dirty = profile()
    dirty["stacks"][0]["conventions"][0]["rule"] = "a\x1b[31mred\x1b[0m\x9b2J\r\nfake heading x‮"
    dirty["stacks"][0]["commands"]["test"]["run"] = "echo \x1b]0;title\x07 hi"
    dirty["sources"]["S1"]["evidence"] = "e\x00\x08v"
    text = stack.preview_text(dirty, "echo \x1b[2J x", None, 3, None, "tech lead")
    assert not any(ord(c) < 32 and c != "\n" or 0x7f <= ord(c) <= 0x9f or c in "  ‮" for c in text)
    assert "\nfake heading" not in text  # a line break in a value cannot start a fake line


def test_plan_approve_returns_the_documented_fields(repo):
    setup(repo)
    plan = stack.plan_approve(".", Config.load())
    assert {"version", "test_command", "counts", "unverified"} <= set(plan)
    assert plan["version"] == 3 and plan["test_command"] == "PYTHONPATH=src python3 -m pytest -q" and plan["unverified"] == 2
    assert plan["counts"]["stacks"] == 1


@pytest.mark.parametrize("p", [None, "{not json", profile(version=0)], ids=["no file", "unparsable", "invalid"])
def test_plan_and_preview_refuse_without_a_usable_profile(repo, monkeypatch, capsys, p):
    if p is not None:
        put(repo, p)
    (repo / "ghp.config.json").write_text(config_text(), encoding="utf-8")
    for on in (False, True):
        tty(monkeypatch, on)
        code, out, err = approve_cli(capsys)
        assert code == 2 and out == "" and "ghp stack show" in err


# ---- the write ----
LAYOUTS = {
    "lf-2": dict(indent=2), "crlf-2": dict(indent=2, eol="\r\n"), "lf-4": dict(indent=4), "crlf-4-no-final": dict(indent=4, eol="\r\n", final=False),
    "tab": dict(indent="\t"), "compact": dict(indent=None), "ascii-escapes": dict(indent=2, ascii_only=True),
}
ODD = {"mode": "strict", "strict": {"test_timeout": 900}, "repo": "example-repo", "owner": "example-org", "x-note": {"b": 1, "a": [3, 2, 1], "f": 1.5},
       "gate": {"block": ["assertion"], "tests": {"**/*_test.py": "def test_"}}, "status_done": "Tâche", "review_block_on": ["blocker"], "big": 12345678901234567890}


@pytest.mark.parametrize("layout", LAYOUTS, ids=list(LAYOUTS))
def test_other_keys_values_and_order_identical(repo, monkeypatch, capsys, layout):
    old = setup(repo, cfg=ODD, **LAYOUTS[layout])
    tty(monkeypatch)
    assert approve_cli(capsys, "--apply")[0] == 0
    new = (repo / "ghp.config.json").read_bytes().decode("utf-8")
    composed = stack.compose_test_command(profile())
    before, after = parse_ordered(old), parse_ordered(new)
    assert dict(after)["strict"][-1] == ("test_command", composed)  # a new key goes last in strict

    def without(pairs):
        return [(k, [kv for kv in v if kv[0] != "test_command"] if k == "strict" else v) for k, v in pairs]
    assert without(after) == without(before)  # parsed values and key order of everything else: identical
    eol = "\r\n" if "\r\n" in old else "\n"
    assert new.endswith(eol) == old.endswith(eol)
    assert "\n" not in new.replace(eol, "") and "\r" not in new.replace(eol, "")  # one line terminator style throughout
    indent = LAYOUTS[layout]["indent"]
    indent = " " * indent if isinstance(indent, int) else indent
    if indent is not None:
        assert new.split(eol)[1][:len(indent)] == indent and not new.split(eol)[1].startswith(indent * 2)
    else:
        assert "\n" not in new.rstrip("\r\n")
    assert ("\\u00e2" in new) == (LAYOUTS[layout].get("ascii_only", False))
    Config.load(repo)  # the config the CLI wrote is a valid config


def test_existing_test_command_keeps_its_place_and_an_absent_strict_goes_last(repo, monkeypatch, capsys):
    tty(monkeypatch)
    setup(repo, cfg={"strict": {"test_command": "make old", "test_timeout": 900}, "owner": "example-org", "repo": "example-repo"})
    assert approve_cli(capsys, "--apply")[0] == 0
    assert [k for k in read_config(repo)] == ["strict", "owner", "repo"]
    assert list(read_config(repo)["strict"]) == ["test_command", "test_timeout"]
    assert read_config(repo)["strict"]["test_command"] == stack.compose_test_command(profile())
    setup(repo, cfg={"owner": "example-org", "repo": "example-repo"})
    assert approve_cli(capsys, "--apply")[0] == 0
    assert list(read_config(repo)) == ["owner", "repo", "strict"]


def test_file_mode_kept_and_no_temp_file_left(repo, monkeypatch, capsys):
    setup(repo)
    os.chmod(repo / "ghp.config.json", 0o600)
    os.chmod(repo / "ghp.stack.json", 0o640)
    tty(monkeypatch)
    assert approve_cli(capsys, "--apply")[0] == 0
    assert stat.S_IMODE(os.stat(repo / "ghp.config.json").st_mode) == 0o600 and stat.S_IMODE(os.stat(repo / "ghp.stack.json").st_mode) == 0o640
    assert sorted(p.name for p in repo.iterdir() if p.name != ".git") == ["ghp.config.json", "ghp.stack.json"]


def test_test_command_removed_when_no_stack_defines_one(repo, monkeypatch, capsys):
    p = profile()
    p["stacks"][0]["commands"]["test"] = {"run": None, "why": "Apex only: tests need an org"}
    old = setup(repo, p, {"owner": "example-org", "strict": {"test_command": "make old", "test_timeout": 900}, "repo": "example-repo"})
    tty(monkeypatch)
    code, out, err = approve_cli(capsys, "--apply")
    assert code == 0 and result(out)["test_command"] == "removed"
    cfg = read_config(repo)
    assert cfg["strict"] == {"test_timeout": 900} and list(cfg) == ["owner", "strict", "repo"]
    assert stack.read_profile(repo)["status"] == "approved"
    # nothing to remove: the config is not rewritten at all
    old = setup(repo, p, cfg='{"owner":"example-org","repo":"example-repo"}')
    assert approve_cli(capsys, "--apply")[0] == 0 and (repo / "ghp.config.json").read_text(encoding="utf-8") == old


def test_unchanged_command_leaves_the_config_bytes_alone(repo, monkeypatch, capsys):
    composed = stack.compose_test_command(profile())
    old = setup(repo, cfg={"owner": "example-org", "repo": "example-repo", "strict": {"test_command": composed}}, indent=8)
    tty(monkeypatch)
    code, out, err = approve_cli(capsys, "--apply")
    assert code == 0 and result(out)["test_command"] == "unchanged"
    assert (repo / "ghp.config.json").read_text(encoding="utf-8") == old


@pytest.mark.parametrize("text", [
    "{not json", "[]", '{"owner": "example-org"}', '{"owner": "example-org", "repo": "example-repo", "mode": "loose"}',
    '{"owner": "example-org", "owner": "other", "repo": "example-repo"}',
    '{"owner": "example-org", "repo": "example-repo", "strict": {"zzz": 1}}',
    '{"owner": "example-org", "repo": "example-repo", "dod_gate": {}}', "﻿" + config_text(), config_text().replace("}", "\x01}"),
], ids=["not json", "list", "no project", "bad mode", "duplicate key", "unknown strict key", "renamed key", "bom", "control char"])
def test_invalid_existing_config_refused_untouched(repo, monkeypatch, capsys, text):
    setup(repo, cfg=text)
    before = snapshot(repo)
    tty(monkeypatch)
    code, out, err = approve_cli(capsys, "--apply")
    assert code == 2 and out == ""
    assert snapshot(repo) == before
    with pytest.raises(ValueError):  # also when the library is called directly (the CLI's own Config.load is not the only guard)
        stack.apply_approve(".", "ghp.config.json")
    assert snapshot(repo) == before and stack.read_profile(repo)["status"] == "draft"


def test_missing_config_refused(repo, monkeypatch, capsys):
    put(repo, profile())
    tty(monkeypatch)
    code, out, err = approve_cli(capsys, "--apply")
    assert code == 2 and not (repo / "ghp.config.json").exists() and stack.read_profile(repo)["status"] == "draft"
    with pytest.raises(ValueError, match="ghp setup"):
        stack.apply_approve(".", "ghp.config.json")


def test_config_symlink_refused(repo, monkeypatch, capsys):
    setup(repo)
    real = repo / "real-config.json"
    (repo / "ghp.config.json").rename(real)
    os.symlink("real-config.json", repo / "ghp.config.json")
    before = snapshot(repo)
    tty(monkeypatch)
    code, out, err = approve_cli(capsys, "--apply")
    assert code == 2 and "symlink" in err
    assert (repo / "ghp.config.json").is_symlink() and snapshot(repo) == before
    with pytest.raises(ValueError, match="symlink"):
        stack.apply_approve(".", "ghp.config.json")
    assert snapshot(repo) == before


def test_config_hard_link_and_non_regular_refused(repo, monkeypatch, capsys):
    setup(repo)
    os.link(repo / "ghp.config.json", repo / "other-name.json")
    before = snapshot(repo)
    tty(monkeypatch)
    assert approve_cli(capsys, "--apply")[0] == 2 and snapshot(repo) == before
    os.unlink(repo / "other-name.json")
    os.unlink(repo / "ghp.config.json")
    os.mkfifo(repo / "ghp.config.json")
    with pytest.raises(ValueError, match="regular"):
        stack.apply_approve(".", "ghp.config.json")
    assert stack.read_profile(repo)["status"] == "draft"


def test_symlinked_profile_refused(repo, monkeypatch, capsys):
    setup(repo)
    (repo / "ghp.stack.json").rename(repo / "real-profile.json")
    os.symlink("real-profile.json", repo / "ghp.stack.json")
    before = snapshot(repo)
    tty(monkeypatch)
    assert approve_cli(capsys, "--apply")[0] == 2 and snapshot(repo) == before


def test_version_increments_and_hash_matches(repo, monkeypatch, capsys):
    setup(repo)
    tty(monkeypatch)
    code, out, err = approve_cli(capsys, "--apply", "--by", "Release Manager")
    assert code == 0 and err == ""
    shown = result(out)
    res = stack.read_profile(repo)
    assert res["status"] == "approved"
    written = res["profile"]
    assert written["version"] == 3 and written["status"] == "approved" and shown["version"] == 3
    assert written["approval"] == {"by": "Release Manager", "date": "2026-10-31", "sha256": stack.profile_hash(written)}
    assert shown["sha256"] == written["approval"]["sha256"] and shown["by"] == "Release Manager"
    assert {k: v for k, v in written.items() if k not in ("version", "status", "approval")} == {k: v for k, v in profile().items() if k not in ("version", "status")}
    assert (repo / "ghp.stack.json").read_text(encoding="utf-8") == json.dumps(written, indent=2, ensure_ascii=False) + "\n"
    assert MARKER not in out[out.rindex('{\n  "applied"'):]  # the result holds the written command and no other profile text
    assert shown["command"] == read_config(repo)["strict"]["test_command"]
    # approving again (to bring a hand-edited command back in line) is a new version
    cfg = read_config(repo)
    cfg["strict"]["test_command"] = "true"
    (repo / "ghp.config.json").write_text(json.dumps(cfg), encoding="utf-8")
    assert approve_cli(capsys, "--apply")[0] == 0
    assert stack.read_profile(repo)["profile"]["version"] == 4
    assert read_config(repo)["strict"]["test_command"] == stack.compose_test_command(profile())


def test_default_by_and_a_changed_profile_can_be_reapproved(repo, monkeypatch, capsys):
    setup(repo, approve(profile(version=5)))
    p = stack.read_profile(repo)["profile"]
    p["stacks"][0]["commands"]["lint"]["run"] = "ruff check --fix ."
    put(repo, p)
    assert stack.read_profile(repo)["status"] == "changed-since-approval"
    tty(monkeypatch)
    assert approve_cli(capsys, "--apply")[0] == 0
    res = stack.read_profile(repo)
    assert res["status"] == "approved" and res["profile"]["version"] == 6 and res["profile"]["approval"]["by"] == "tech lead"
    assert res["profile"]["stacks"][0]["commands"]["lint"]["run"] == "ruff check --fix ."


@pytest.mark.parametrize("by", ["", "a" * 41, "tech lead; rm", "Dana\nFictional", "tech-lead", "téch lead", "NEW INSTRUCTION: x"])
def test_by_is_a_role(repo, monkeypatch, capsys, by):
    setup(repo)
    before = snapshot(repo)
    tty(monkeypatch)
    with pytest.raises(SystemExit) as e:
        main(["stack", "approve", "--apply", "--by", by])
    assert e.value.code == 2 and "--by" in capsys.readouterr().err and snapshot(repo) == before


def test_compose_over_500_refuses_before_any_write(repo, monkeypatch, capsys):
    p = two_stack_profile(run_a="echo " + "a" * 295, run_b="echo " + "b" * 295)
    assert all(len(st["commands"]["test"]["run"]) <= 500 for st in p["stacks"])
    with pytest.raises(ValueError, match="500"):
        stack.compose_test_command(p)
    setup(repo, p)
    before = snapshot(repo)
    tty(monkeypatch)
    for flags in (("--apply",), ("--apply", "--story", "1001"), ()):
        code, out, err = approve_cli(capsys, *flags)
        assert code == 2 and out == "" and "500" in err and "kind `repo`" in err
        assert snapshot(repo) == before  # no decisions file, no config, no profile
    assert not (repo / "dod").exists()


# ---- the decision line, and a failure leaving nothing half done ----
def test_story_adds_one_decision_line(repo, monkeypatch, capsys):
    setup(repo)
    tty(monkeypatch)
    code, out, err = approve_cli(capsys, "--apply", "--story", "1001")
    assert code == 0
    md = (repo / "dod" / "Decisions_1001.md").read_text(encoding="utf-8")
    parsed = all_lines(md)
    assert [(d["id"], d["kind"], d["status"]) for d in parsed] == [("D1", "decision", "open")]
    res = stack.read_profile(repo)
    digest = res["profile"]["approval"]["sha256"]
    assert parsed[0]["text"] == f"Stack profile v3 approved, sha256 {digest[:12]}" and MARKER not in md and "pytest" not in md
    assert res["profile"]["approval"]["decision"] == "US1001 D1" and result(out)["decision"] == "US1001 D1"


def test_story_appends_to_an_existing_file_and_uses_dod_path(repo, monkeypatch, capsys):
    setup(repo, cfg={**CONFIG, "dod_path": "docs/dod"})
    existing = decisions.render_file("1001") + "- [ ] D1: Use Postgres → _(open)_ · kind: decision · why: speed\r\n".replace("\r\n", "\n")
    (repo / "docs" / "dod").mkdir(parents=True)
    (repo / "docs" / "dod" / "Decisions_1001.md").write_text(existing, encoding="utf-8")
    tty(monkeypatch)
    assert approve_cli(capsys, "--apply", "--story", "1001")[0] == 0
    md = (repo / "docs" / "dod" / "Decisions_1001.md").read_text(encoding="utf-8")
    assert md.startswith(existing) and [d["id"] for d in all_lines(md)] == ["D1", "D2"]
    assert stack.read_profile(repo)["profile"]["approval"]["decision"] == "US1001 D2" and not (repo / "dod").exists()


@pytest.mark.parametrize("story", ["abc", "10 01", "1001; x", "-1", "0", "01", "٣", "99999999999", "1e3"])
def test_story_is_digits_only(repo, monkeypatch, capsys, story):
    setup(repo)
    before = snapshot(repo)
    tty(monkeypatch)
    with pytest.raises(SystemExit) as e:
        main(["stack", "approve", "--apply", "--story", story])
    assert e.value.code == 2 and "--story" in capsys.readouterr().err and snapshot(repo) == before


def test_a_decision_that_cannot_be_added_leaves_everything_as_it_was(repo, monkeypatch, capsys):
    setup(repo)
    (repo / "dod").mkdir()
    (repo / "dod" / "Decisions_1001.md").write_text(decisions.render_file("1001") + "```\nopen fence\n", encoding="utf-8")  # hides a new line
    before = snapshot(repo)
    tty(monkeypatch)
    code, out, err = approve_cli(capsys, "--apply", "--story", "1001")
    assert code == 2 and "decision" in err.lower() and snapshot(repo) == before
    assert stack.read_profile(repo)["status"] == "draft"


def test_a_failed_profile_write_rolls_back_the_config_and_the_decision(repo, monkeypatch, capsys):
    setup(repo)
    (repo / "dod").mkdir()
    (repo / "dod" / "Decisions_1001.md").write_text(decisions.render_file("1001"), encoding="utf-8")
    before = snapshot(repo)
    real = os.replace

    def replace(src, dst, *a, **k):
        if str(dst).endswith("ghp.stack.json"):
            raise OSError("disk full")
        return real(src, dst, *a, **k)
    monkeypatch.setattr(os, "replace", replace)
    tty(monkeypatch)
    code, out, err = approve_cli(capsys, "--apply", "--story", "1001")
    assert code == 2 and "nothing was approved" in err and "disk full" not in err
    monkeypatch.undo()
    assert snapshot(repo) == before


def test_a_failed_config_write_rolls_back_the_decision_and_new_file(repo, monkeypatch, capsys):
    setup(repo)
    before = snapshot(repo)
    real = os.replace

    def replace(src, dst, *a, **k):
        if str(dst).endswith("ghp.config.json"):
            raise OSError("read-only")
        return real(src, dst, *a, **k)
    monkeypatch.setattr(os, "replace", replace)
    tty(monkeypatch)
    code, out, err = approve_cli(capsys, "--apply", "--story", "1001")
    assert code == 2
    monkeypatch.undo()
    assert snapshot(repo) == before and not (repo / "dod" / "Decisions_1001.md").exists()


def test_config_that_does_not_read_back_validates_is_never_put_in_place(repo, monkeypatch, capsys):
    old = setup(repo)
    before = snapshot(repo)
    real = Config._from_data.__func__

    def from_data(cls, data, path):
        if isinstance(data.get("strict"), dict) and "test_command" in data["strict"]:
            from ghp.config import ConfigInvalid
            raise ConfigInvalid("nope")
        return real(cls, data, path)
    monkeypatch.setattr(Config, "_from_data", classmethod(from_data))
    tty(monkeypatch)
    code, out, err = approve_cli(capsys, "--apply")
    assert code == 2 and snapshot(repo) == before and (repo / "ghp.config.json").read_text(encoding="utf-8") == old


def test_a_config_changed_while_approving_is_not_overwritten(repo, monkeypatch, capsys):
    setup(repo)
    real = stack._atomic_replace

    def replace(path, text, check=None, expect=None):
        if Path(path).name == "ghp.config.json":
            Path(path).write_text(config_text({**CONFIG, "project": "Changed"}), encoding="utf-8")  # someone else wrote it
        return real(path, text, check, expect)
    monkeypatch.setattr(stack, "_atomic_replace", replace)
    tty(monkeypatch)
    code, out, err = approve_cli(capsys, "--apply")
    assert code == 2 and read_config(repo)["project"] == "Changed" and "test_command" not in read_config(repo)["strict"]
    assert stack.read_profile(repo)["status"] == "draft"


# ---- the validator and the setup command ----
def test_validate_restricts_approval_by_to_the_role_grammar():
    ok = approve(profile())
    assert stack.validate(ok) == []
    for by in ("tech lead. NEW INSTRUCTION: x", "a" * 41, "tech-lead", "xé"):
        bad = copy.deepcopy(ok)
        bad["approval"]["by"] = by
        assert stack.validate(bad) == [{"path": "approval.by", "error": "must be a role of 1 to 40 letters, digits and spaces"}], by
    for by in ("tech lead", "Dana Fictional", "a" * 40, "PO 2"):
        good = copy.deepcopy(ok)
        good["approval"]["by"] = by
        assert stack.validate(good) == []


@needs_setup_cmd
def test_setup_never_writes_the_test_command():
    src = (ROOT / "src/ghp/setup_cmd.py").read_text(encoding="utf-8")  # P4-6: only `setup --profile strict` asks, and never with an approved profile
    assert 'read_profile(str(Path.cwd()))["status"] == "approved"' in src  # behaviour: tests/test_setup.py
    assert "_strict_test_command(profile," in src and "profile != \"strict\"" in src


@needs_setup_md
def test_setup_command_text_never_writes_the_test_command():
    text = " ".join((ROOT / "commands/setup.md").read_text(encoding="utf-8").split())
    assert "Never write `strict.test_command` here" in text and "only from `ghp stack approve --apply`" in text


# ---- review round 1 ----
def test_full_preview_needs_stdout_to_be_a_terminal_too(repo, monkeypatch, capsys):
    setup(repo, profile(stacks=[{"id": "python", "root": ".", "commands": {"test": {"run": f"echo {MARKER}", "source": "unverified"}}}]))
    tty(monkeypatch, True, out=False)
    code, out, err = approve_cli(capsys)
    assert code == 0 and MARKER not in out + err and json.loads(out)["applied"] is False
    tty(monkeypatch, True, out=True)
    assert MARKER in approve_cli(capsys)[1]


def test_stdin_terminal_stdout_piped_prints_no_profile_text_in_a_real_process(repo):
    setup(repo, profile(stacks=[{"id": "python", "root": ".", "commands": {"test": {"run": f"echo {MARKER}", "source": "unverified"}}}]))
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    env.pop("GHP_CONFIG", None)
    master, slave = pty.openpty()  # stdin is a terminal, stdout is a pipe (`ghp stack approve | tee x`)
    try:
        res = subprocess.run([sys.executable, "-m", "ghp", "stack", "approve"], cwd=repo, env=env, stdin=slave, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, timeout=60)
    finally:
        os.close(slave)
        os.close(master)
    assert res.returncode == 0 and MARKER not in res.stdout + res.stderr and json.loads(res.stdout)["counts"]["stacks"] == 1


def test_apply_needs_both_stdin_and_stdout_terminals(repo, monkeypatch, capsys):
    setup(repo)
    before = snapshot(repo)

    def boom(*a, **k):
        raise AssertionError("a file or the config was read before the terminal check")
    for target in ("read_profile", "plan_approve", "prepare_approve", "apply_approve"):
        monkeypatch.setattr(stack, target, boom)
    monkeypatch.setattr(Config, "load", boom)
    for stdin_tty, stdout_tty in ((False, False), (True, False), (False, True)):
        tty(monkeypatch, stdin_tty, stdout_tty)
        code, out, err = approve_cli(capsys, "--apply")
        assert code == 2 and out == "" and "only a human may do" in err and "stdin and stdout must both be a terminal" in err
        assert "not a security boundary" in err and snapshot(repo) == before
    monkeypatch.undo()
    for kw in ({"stdin_pty": False, "stdout_pty": False}, {"stdin_pty": False}, {"stdout_pty": False}):  # `< /dev/null`, `| tee x`, both
        code, out, err = run_in_pty(["stack", "approve", "--apply"], repo, **kw)
        assert code == 2 and "only a human may do" in err and "approval code" not in out and snapshot(repo) == before
    code, out, err = run_in_pty(["stack", "approve", "--apply"], repo)  # both terminals: the code is asked for and typed
    assert code == 0, err
    assert stack.read_profile(repo)["status"] == "approved"


def test_the_approval_code_covers_what_is_written(repo, monkeypatch, capsys):
    setup(repo)
    seen = []
    for story, by, run in ((None, "tech lead", "echo a"), (None, "tech lead", "echo b"), (None, "po", "echo a"), ("1001", "tech lead", "echo a")):
        put(repo, profile(stacks=[{"id": "python", "root": ".", "commands": {"test": {"run": run, "source": "S1"}}}]))
        seen.append(stack.prepare_approve(repo, str(repo / "ghp.config.json"), by, story)["code"])
    assert len(set(seen)) == 4 and all(re.fullmatch(r"[0-9a-f]{12}", c) for c in seen)
    prep = stack.prepare_approve(repo, str(repo / "ghp.config.json"))
    assert prep["code"] == stack.prepare_approve(repo, str(repo / "ghp.config.json"))["code"]  # the same data, the same code
    assert snapshot(repo) == snapshot(repo) and stack.read_profile(repo)["status"] == "draft"  # preparing writes nothing
    put(repo, profile(sources={"S1": {"kind": "official", "url": "https://example.com/other", "retrieved": "2026-10-30", "evidence": "other"},
                       "S2": profile()["sources"]["S2"]}))
    assert stack.prepare_approve(repo, str(repo / "ghp.config.json"))["code"] != prep["code"]  # a note edited: the profile hash moves
    (repo / "ghp.config.json").write_text(config_text({**CONFIG, "mode": "advisory"}))
    assert stack.prepare_approve(repo, str(repo / "ghp.config.json"))["code"] not in seen


def test_apply_requires_the_typed_code(repo, monkeypatch, capsys):
    setup(repo)
    before = snapshot(repo)
    for answer in ("nope\n", "\n", "   \n", "", KeyboardInterrupt, "x" * 5000, "\x00\n", "0" * 12 + "\n"):
        stdin = terminal(monkeypatch, answer)
        code, out, err = approve_cli(capsys, "--apply", "--story", "1001")
        assert code == 2 and err == "error: not confirmed: nothing was approved\n" and stdin.lines == 1, (answer, err)
        assert out.index("# Stack profile approval preview") < out.index("approval code: ") and '"applied"' not in out
        assert snapshot(repo) == before and not (repo / "dod").exists()  # config, profile, decisions untouched
    for kw in ({"answer": "nope"}, {"answer": ""}, {"interrupt": True}):  # a real process: wrong text, end of input, Ctrl-C
        code, out, err = run_in_pty(["stack", "approve", "--apply"], repo, **kw)
        assert code == 2 and err.endswith("error: not confirmed: nothing was approved\n") and snapshot(repo) == before, err


def test_a_code_typed_before_the_prompt_is_discarded(repo, monkeypatch):
    """The code is computable beforehand (anyone can run the same hash); what is typed into the terminal before the prompt is flushed."""
    monkeypatch.setattr(stack, "_today", REAL_TODAY)  # the child approves with the real date; the pinned 2026-10-31 would call it "in the future" after UTC midnight
    setup(repo)
    before = snapshot(repo)
    early = stack.prepare_approve(repo, str(repo / "ghp.config.json"))["code"]
    code, out, err = run_in_pty(["stack", "approve", "--apply"], repo, early=early, answer="")  # then end of input at the prompt
    assert code == 2 and err.endswith("error: not confirmed: nothing was approved\n") and snapshot(repo) == before, err
    code, out, err = run_in_pty(["stack", "approve", "--apply"], repo)  # the same code typed after the prompt still approves
    assert code == 0 and stack.read_profile(repo)["status"] == "approved", err


def test_the_code_is_never_read_from_a_pipe_or_a_file_and_never_shown_to_one(monkeypatch, capsys):
    import io
    for source in (io.StringIO("0123456789ab\n"), io.StringIO("")):  # a pipe or a file: isatty() is False
        monkeypatch.setattr(sys, "stdin", source)
        assert stack.confirm_code("0123456789ab") is False and capsys.readouterr().out == "" and source.tell() == 0


def test_apply_with_the_right_code_writes_and_prints_the_command(repo, monkeypatch, capsys):
    setup(repo, two_stack_profile())
    stdin = terminal(monkeypatch, None)
    code, out, err = approve_cli(capsys, "--apply", "--story", "1001", "--by", "Tech Lead")
    assert code == 0 and stdin.lines == 1 and err == ""
    preview, _, result = out.partition("approval code: ")
    assert "# Stack profile approval preview" in preview and "echo a" in preview  # the full preview comes first
    typed, _, result = result.partition("\n")
    assert typed == shown_code(out) and typed in out
    doc = json.loads(result[result.index("{"):])
    composed = read_config(repo)["strict"]["test_command"]
    assert doc["applied"] is True and doc["command"] == composed and "sh -c" in composed and doc["test_command"] == "set"
    assert stack.read_profile(repo)["status"] == "approved" and doc["decision"] == stack.read_profile(repo)["profile"]["approval"]["decision"]
    assert doc["sha256"] == stack.read_profile(repo)["profile"]["approval"]["sha256"]
    # whitespace around the code is ignored; a real process types it too
    terminal(monkeypatch, None)
    setup(repo)
    stdin_ws = terminal(monkeypatch, None)
    stdin_ws.readline = lambda size=-1: "  \t" + shown_code(sys.stdout.getvalue()) + "  \n"
    assert approve_cli(capsys, "--apply")[0] == 0
    setup(repo)
    code, out, err = run_in_pty(["stack", "approve", "--apply"], repo)
    assert code == 0 and json.loads(out[out.rindex("{"):])["command"] == read_config(repo)["strict"]["test_command"]


def test_profile_edited_after_the_prompt_is_refused(repo, monkeypatch, capsys):
    """The edit happens between the prompt and the typed code: in-process through the `before` hook of the fake terminal, in a real process
    by the test rewriting the file while the child waits for its line."""
    setup(repo, profile(stacks=[{"id": "python", "root": ".", "commands": {"test": {"run": "PYTHONPATH=src python3 -m pytest -q", "source": "S1"}}}]))
    msg = "error: the profile changed while you were reading it: nothing was approved\n"
    other = profile(stacks=[{"id": "python", "root": ".", "commands": {"test": {"run": "make evil-test", "source": "S1"}}}])
    before = snapshot(repo)
    terminal(monkeypatch, None, before=lambda code: put(repo, other))
    code, out, err = approve_cli(capsys, "--apply")
    assert code == 2 and err == msg and "evil" not in out and '"applied"' not in out
    after = snapshot(repo)
    assert after["ghp.config.json"] == before["ghp.config.json"] and stack.read_profile(repo)["status"] == "draft"
    assert b"evil" in after["ghp.stack.json"]  # the edited file is as the editor left it: nothing was approved over it
    setup(repo)
    before = snapshot(repo)
    code, out, err = run_in_pty(["stack", "approve", "--apply"], repo, before=lambda c: put(repo, other))
    assert code == 2 and err.endswith(msg) and snapshot(repo)["ghp.config.json"] == before["ghp.config.json"]
    assert stack.read_profile(repo)["status"] == "draft"
    # the same swap at the config: another test command key or mode edited while reading changes what is written
    setup(repo)
    before = snapshot(repo)
    terminal(monkeypatch, None, before=lambda code: (repo / "ghp.config.json").write_text(config_text({**CONFIG, "mode": "advisory"})))
    assert approve_cli(capsys, "--apply")[2] == msg and stack.read_profile(repo)["status"] == "draft"


def test_decision_line_written_last_and_absent_on_failure(repo, monkeypatch, capsys):
    setup(repo)
    dec = repo / "dod" / "Decisions_1001.md"
    order, failing, real = [], [None], os.replace

    def replace(src, dst, *a, **k):
        order.append(Path(dst).name)
        if Path(dst).name == failing[0]:
            raise OSError("disk full")
        return real(src, dst, *a, **k)
    monkeypatch.setattr(os, "replace", replace)
    terminal(monkeypatch, None)
    assert approve_cli(capsys, "--apply", "--story", "1001")[0] == 0
    assert order == ["ghp.config.json", "ghp.stack.json", "Decisions_1001.md"] and "Stack profile v3 approved" in dec.read_text()
    # a failure at the config write, then at the profile write: no decision line, every file as it was
    for failing[0] in ("ghp.config.json", "ghp.stack.json"):
        setup(repo)
        dec.unlink(missing_ok=True)
        before = snapshot(repo)
        code, out, err = approve_cli(capsys, "--apply", "--story", "1001")
        assert code == 2 and "nothing was approved" in err and not dec.exists() and snapshot(repo) == before, failing[0]
    # a failure of the decision write itself (the last step) puts the config and the profile back
    failing[0] = "Decisions_1001.md"
    setup(repo)
    dec.unlink(missing_ok=True)
    before = snapshot(repo)
    code, out, err = approve_cli(capsys, "--apply", "--story", "1001")
    assert code == 2 and "nothing was approved" in err and not dec.exists() and snapshot(repo) == before
    assert stack.read_profile(repo)["status"] == "draft"


@pytest.mark.parametrize("nth", [1, 2, 3])
def test_an_interrupt_between_writes_leaves_every_file_as_it_was(repo, monkeypatch, capsys, nth):
    setup(repo)
    (repo / "dod").mkdir()
    (repo / "dod" / "Decisions_1001.md").write_bytes(decisions.render_file("1001").replace("\n", "\r\n").encode())
    before = snapshot(repo)
    real, calls = os.replace, []

    def replace(src, dst, *a, **k):
        calls.append(dst)
        if len(calls) == nth:
            raise KeyboardInterrupt
        return real(src, dst, *a, **k)
    monkeypatch.setattr(os, "replace", replace)
    tty(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        main(["stack", "approve", "--apply", "--story", "1001"])
    monkeypatch.undo()
    assert snapshot(repo) == before


def test_a_decision_line_added_with_an_unexpected_id_is_rolled_back(repo, monkeypatch, capsys):
    setup(repo)
    before = snapshot(repo)
    real = decisions.apply_add
    monkeypatch.setattr(decisions, "apply_add", lambda *a, **k: (real(*a, **k), "D99")[1])  # the line is written, the id is not the planned one
    tty(monkeypatch)
    code, out, err = approve_cli(capsys, "--apply", "--story", "1001")
    assert code == 2 and "nothing was approved" in err
    monkeypatch.undo()
    assert snapshot(repo) == before and not (repo / "dod" / "Decisions_1001.md").exists()


def test_a_deeply_nested_config_is_a_bad_config_not_a_traceback(repo):
    for depth in (500, 700, 800, 900, 950, 990):
        text = '{"owner": "example-org", "repo": "example-repo", "x": ' + "[" * depth + "]" * depth + "}"
        setup(repo, cfg=text)
        before = snapshot(repo)
        try:
            stack.apply_approve(".", "ghp.config.json")
        except ValueError:
            assert snapshot(repo) == before
        else:
            assert stack.read_profile(repo)["status"] == "approved"


def test_preview_shows_the_current_status(repo, monkeypatch, capsys):
    tty(monkeypatch)
    setup(repo)
    assert "Current status: draft" in approve_cli(capsys)[1]
    put(repo, approve(profile(version=5)))
    assert "Current status: approved, version 5 (approving again makes version 6)" in approve_cli(capsys)[1]
    p = approve(profile(version=5))
    p["stacks"][0]["commands"]["lint"]["run"] = "ruff check --fix ."
    put(repo, p)
    assert "Current status: changed since the approval of version 5" in approve_cli(capsys)[1]


def test_preview_warns_when_ghpconfig_is_set_and_apply_refuses(repo, monkeypatch, capsys, tmp_path_factory):
    setup(repo)
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "ghp.config.json"
    elsewhere.write_text(config_text({**CONFIG, "strict": {"test_command": "make other"}}), encoding="utf-8")
    monkeypatch.setenv("GHP_CONFIG", str(elsewhere))
    tty(monkeypatch)
    code, out, err = approve_cli(capsys)
    assert code == 0 and "WARNING: GHP_CONFIG is set" in out and "make other" in out and "refuse" in out
    assert approve_cli(capsys, "--apply")[0] == 2


def test_by_of_only_spaces_is_refused(repo, monkeypatch, capsys):
    setup(repo)
    before = snapshot(repo)
    tty(monkeypatch)
    for by in (" ", "   "):
        with pytest.raises(SystemExit):
            main(["stack", "approve", "--apply", "--by", by])
        assert snapshot(repo) == before
        with pytest.raises(ValueError):
            stack.apply_approve(".", "ghp.config.json", by=by)
    bad = approve(profile(), by="   ")
    assert stack.validate(bad) == [{"path": "approval.by", "error": "must be a role of 1 to 40 letters, digits and spaces"}]


def test_a_file_starting_with_blank_space_keeps_its_real_indent(repo, monkeypatch, capsys):
    setup(repo, cfg='  \n {\n    "owner": "example-org",\n    "repo": "example-repo"\n}\n')
    tty(monkeypatch)
    assert approve_cli(capsys, "--apply")[0] == 0
    lines = (repo / "ghp.config.json").read_text(encoding="utf-8").split("\n")
    assert lines[0] == "{" and lines[1] == '    "owner": "example-org",'


# ---- P2-16d: the timeout line, the quoting sentence for several stacks only, one role text ----
SENTENCE = "Strict runs use strict.test_timeout seconds (config says {n}, default 300); package-manager based test commands usually need 900 or more."


def test_preview_names_the_strict_timeout(repo, monkeypatch, capsys):
    setup(repo, two_stack_profile())  # CONFIG says strict.test_timeout 900
    tty(monkeypatch, True)
    code, out, err = approve_cli(capsys)
    assert code == 0 and SENTENCE.format(n=900) in out and out.index("strict.test_command to be written") < out.index(SENTENCE.format(n=900))
    setup(repo, two_stack_profile(), cfg={k: v for k, v in CONFIG.items() if k != "strict"})  # no strict object: the default 300
    assert SENTENCE.format(n=300) in approve_cli(capsys)[1]
    # a profile with no test command writes no command, so there is nothing to time
    setup(repo, profile(stacks=[{"id": "python", "root": ".", "commands": {"test": {"run": None, "why": "none", "source": "unverified"}}}]))
    assert "strict.test_timeout" not in approve_cli(capsys)[1]
    assert stack.timeout_text(None).endswith("usually need 900 or more.") and "config says nothing here" in stack.timeout_text(None)


def test_approve_summaries_carry_the_timeout_sentence(repo, monkeypatch, capsys):
    setup(repo, two_stack_profile())
    tty(monkeypatch, False)
    doc = json.loads(approve_cli(capsys)[1])  # no terminal: counts and lengths only, plus the sentence
    assert doc["test_timeout"] == SENTENCE.format(n=900) and doc["test_command"]["set"] is True
    setup(repo, profile(stacks=[{"id": "python", "root": ".", "commands": {"test": {"run": None, "why": "none", "source": "unverified"}}}]))
    assert json.loads(approve_cli(capsys)[1])["test_timeout"] is None
    setup(repo, two_stack_profile())
    terminal(monkeypatch, None)
    code, out, err = approve_cli(capsys, "--apply")
    assert code == 0 and SENTENCE.format(n=900) in out and result(out)["test_timeout"] == SENTENCE.format(n=900)


def test_quoting_sentence_only_for_several_stacks(repo):
    several = "with several stacks each run is quoted into `sh -c`"
    two = two_stack_profile()
    one = profile(stacks=[{"id": "python", "root": ".", "commands": {"test": {"run": "echo a", "source": "S1"}}}])
    assert several in stack.preview_text(two, stack.compose_test_command(two), None, 1, None, "po")
    text = stack.preview_text(one, stack.compose_test_command(one), None, 1, None, "po")
    assert several not in text and "(6 characters)" in text
    one_nested = profile(stacks=[{"id": "python", "root": "svc", "commands": {"test": {"run": "echo a", "source": "S1"}}}])  # one stack, still quoted
    assert several not in stack.preview_text(one_nested, stack.compose_test_command(one_nested), None, 1, None, "po")


def test_the_role_error_is_one_text_everywhere(repo, monkeypatch, capsys):
    text = "--by must be a role of 1 to 40 letters, digits and spaces"
    assert stack.E_ROLE == text
    from ghp import kits
    for args in (["stack", "approve", "--by", "a;b"], ["kits", "pin", "sf", "--by", "a;b"]):
        with pytest.raises(SystemExit) as e:
            main(args)
        assert e.value.code == 2 and text in capsys.readouterr().err
    setup(repo)
    with pytest.raises(ValueError, match=re.escape(text)):
        stack._prepare_approve(repo, str(repo / "ghp.config.json"), "a;b", None, "dod")
    with pytest.raises(ValueError, match=re.escape(text)):
        kits.apply_pin(repo, {"id": "sf"}, "a;b")
    src = "".join((ROOT / "src/ghp" / f).read_text(encoding="utf-8") for f in ("kits.py", "__main__.py", "stack.py"))
    assert src.count("1 to 40 letters, digits and spaces") == 2  # E_ROLE and the validator's own approval.by text, nowhere else
