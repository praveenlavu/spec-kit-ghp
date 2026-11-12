"""T22/T23 (X5): ado's tests/test_whole_delta.py, ported with the rename table. Every test drives the STRICT checks (`strict verify --no-record`, `strict check`
with the record's command hash, the CI trust helpers of test_ci_trust / test_strict / test_strict_wire); the module skip of T22 is lifted by T23.
Tracker seam: ado's FakeClient is `test_strict.Client` (a Config and a fake GitHub). Not ported: the M2 block (`ghp update <id> --state <done_state>` refused
in strict mode): ghp has no generic `update` verb; closing is `ghp done` (T24), whose strict refusal is covered by its own tests."""
import hashlib
import json

import pytest

from ghp import __main__ as m
from ghp import decisions, strict
from conftest import commit
from test_ci_trust import STRICT, _cli, _target
from test_strict import CMD, DEC, LEDGER, _d, _pr, _scenario, _story, _verify
from test_strict_wire import _full, _wire


# ---- M1: `verify --no-record` fails while an override is in force ----
def _override(tmp_path, rule="skip l2"):
    DEC(tmp_path).parent.mkdir(exist_ok=True)
    DEC(tmp_path).write_text(decisions.render_file(1001) + _d(1, rule, kind="override"))  # open, unposted: hand-written


def test_no_record_verify_is_not_satisfied_by_an_override(tmp_path, git_env):
    client, _ = _scenario(tmp_path, git_env, command="exit 7")  # every run would fail
    _override(tmp_path)
    res = _verify(tmp_path, client, record=False)
    assert res["ok"] is False and [f["rule"] for f in res["findings"]] == ["override-in-force"]
    assert "D1" in res["findings"][0]["detail"] and "allow-override" in res["findings"][0]["fix"]
    assert res["overrides_used"] == ["D1"]


def test_allow_override_and_record_mode_keep_the_override(tmp_path, git_env):
    client, _ = _scenario(tmp_path, git_env, command="exit 7")
    _override(tmp_path)
    res = _verify(tmp_path, client, record=False, allow_override=True)
    assert res["ok"] is True and res["note"] == "verification skipped: overridden"
    assert _verify(tmp_path, client)["ok"] is True  # the local run (record mode) is as before


def test_no_record_verify_without_an_override_is_unchanged(tmp_path, git_env):
    client, _ = _scenario(tmp_path, git_env)
    assert _verify(tmp_path, client, record=False)["ok"] is True


def test_no_record_verify_names_every_override_that_let_a_finding_through(tmp_path, git_env):
    client, _ = _scenario(tmp_path, git_env, command=None)  # test-command unset: covered by the override
    _override(tmp_path, "skip test-command")
    res = _verify(tmp_path, client, record=False)
    assert res["ok"] is False and [f["rule"] for f in res["findings"]] == ["override-in-force"] and res["overrides_used"] == ["D1"]


def test_cli_verify_no_record_exit_2_unless_allow_override(tmp_path, monkeypatch, capsys, git_env):
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env, record=False)
    _override(tmp_path)
    commit(tmp_path, "override")
    rc, out = _cli(mod, capsys, "strict", "verify", "1001", "--no-record")
    assert rc == 2 and [f["rule"] for f in out["findings"]] == ["override-in-force"]
    rc, out = _cli(mod, capsys, "strict", "verify", "1001", "--no-record", "--allow-override")
    assert rc == 0 and out["ok"] is True


# ---- S1: the record's command hash is checked ----
def _rules(res):
    return [f["rule"] for f in res["findings"]]


@pytest.mark.parametrize("phase", ["pr", "done"])
def test_a_record_made_under_another_command_is_a_mismatch(tmp_path, git_env, phase):
    client, _ = _story(tmp_path, git_env)
    assert _pr(tmp_path, client, phase)["ok"] is True
    client.config.strict["test_command"] = "true"  # a locally weakened (or just different) command
    res = _pr(tmp_path, client, phase)  # (the story's approved profile composes the recorded command, so the weaker one is also drift)
    assert res["ok"] is False and _rules(res) == ["record-command-mismatch", "stack-profile-drift"]
    assert "ghp strict verify 1001" in res["findings"][0]["fix"]
    assert hashlib.sha256(b"true").hexdigest()[:12] not in json.dumps(res)  # no hash is echoed


@pytest.mark.parametrize("phase", ["pr", "done"])
def test_an_unset_command_with_a_record_present_is_a_mismatch(tmp_path, git_env, phase):
    client, _ = _story(tmp_path, git_env)
    del client.config.strict["test_command"]
    assert _rules(_pr(tmp_path, client, phase)) == ["record-command-mismatch", "stack-profile-drift"]  # an unset command differs from the profile's too


def test_a_record_with_no_command_line_is_a_mismatch(tmp_path, git_env):
    client, _ = _story(tmp_path, git_env)
    text = LEDGER(tmp_path).read_text()
    LEDGER(tmp_path).write_text("\n".join(l for l in text.split("\n") if not l.startswith("- command-sha256:")))
    commit(tmp_path, "drop the hash")
    assert "record-command-mismatch" in _rules(_pr(tmp_path, client, "pr"))


def test_an_l2_override_covers_the_mismatch(tmp_path, git_env):
    client, _ = _story(tmp_path, git_env)
    client.config.strict["test_command"] = "true"
    DEC(tmp_path).write_text(decisions.render_file(1001) + _d(1, "skip l2", kind="override", posted=True))
    commit(tmp_path, "override")
    res = _pr(tmp_path, client, "pr")  # the override covers the record; the stack drift (the profile composes the recorded command) is not overridable
    assert _rules(res) == ["stack-profile-drift"] and res["overrides_used"] == ["D1"]


def test_ci_judges_the_record_under_the_target_branchs_command(tmp_path, monkeypatch, capsys, git_env):
    """The PR weakens strict.test_command in its own config; the target's command in force makes the record a mismatch."""
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env)
    weak = {"owner": "octo", "repo": "demo", "mode": "strict", "strict": {"test_command": "true"}}
    (tmp_path / "ghp.config.json").write_text(json.dumps(weak))
    argv = ["strict", "check", "1001", "--phase", "pr"]
    _target(tmp_path, {**STRICT, "strict": {"test_command": CMD}}, _full(tmp_path, s.base))
    rc, out = _cli(mod, capsys, *argv, "--base", "origin/trunk")  # the recorded command is the target's: no mismatch (the weak head copy is stack drift)
    assert rc == 2 and _rules(out) == ["stack-profile-drift"] and "at the head commit" in out["findings"][0]["detail"], out
    _target(tmp_path, {**STRICT, "strict": {"test_command": "pytest -q"}}, _full(tmp_path, s.base))
    rc, out = _cli(mod, capsys, *argv, "--base", "origin/trunk")  # the target's command is another one: the record is a mismatch
    assert rc == 2 and _rules(out) == ["record-command-mismatch", "stack-profile-drift"]  # the head copy's config (here none) differs from its profile too
