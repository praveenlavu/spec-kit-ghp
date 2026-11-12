"""T37 AC1: scripts/ci_smoke/run_local.py runs the CI rehearsal cases (a) to (d) with the real ghp code paths, on temp git repos and a fake tracker."""
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ghp.gh import GhError

SMOKE = Path(__file__).resolve().parent.parent / "scripts" / "ci_smoke"
_spec = importlib.util.spec_from_file_location("ci_smoke_run_local", SMOKE / "run_local.py")
run_local = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_local)


def test_run_local_prints_ok_for_cases_a_to_d_and_exits_0(tmp_path):
    """As documented: `python3 scripts/ci_smoke/run_local.py`, from any directory, on a Python without the package installed (-S)."""
    r = subprocess.run([sys.executable, "-S", str(SMOKE / "run_local.py")], capture_output=True, text=True, cwd=str(tmp_path),
                       env={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "GITHUB_ACTIONS": "true", "CI": "true"}, timeout=120)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert [ln.split(")")[0] for ln in r.stdout.splitlines()] == ["ok (a", "ok (b", "ok (c", "ok (d"]


def test_the_cases_are_the_four_the_plan_names():
    cases = run_local.load_cases()
    assert {k: v["title"] for k, v in cases.items()} == {
        "a": "a clean pull request passes", "b": "a missing AC trace fails", "c": "a forged config edit in the pull request is ignored",
        "d": "Closes into a non-default base is warned"}
    assert [s["exit"] for s in cases["b"]["steps"]] == [1] and cases["c"]["steps"][-1]["exit"] == 1


@pytest.mark.parametrize("name", list("abcd"))
def test_every_case_gives_the_expected_local_result(name):
    assert run_local.run_case(name, run_local.load_cases()[name]) == []


def test_a_wrong_expectation_is_reported_not_swallowed():
    case = run_local.load_cases()["b"]
    wrong = {**case, "steps": [{**case["steps"][0], "exit": 0}]}
    (bad,) = run_local.run_case("b", wrong)
    assert bad[2]["exit"] == 1


def test_case_c_fails_only_because_the_target_config_judges():
    """With an advisory target the PR's own (forged) config would judge, and the gate would pass: the case is not vacuous."""
    case = run_local.load_cases()["c"]
    bad = run_local.run_case("c", {**case, "target_config": "config.forged.json"})
    got = {tuple(argv[:2]): g for argv, _w, g in bad}
    assert got[("strict", "mode")]["json"] == {"mode": "advisory", "source": "pull request"} and got[("gate", "1001")]["exit"] == 0


def test_case_d_warns_only_for_a_non_default_base():
    case = run_local.load_cases()["d"]
    argv = [a if a != "release/1.0" else "main" for a in case["steps"][0]["argv"]]
    (bad,) = run_local.run_case("d", {**case, "steps": [{**case["steps"][0], "argv": argv}]})
    assert bad[2] == {"exit": 0, "contains": ["Closes #1001"]}


def test_case_b_fails_on_the_trace_and_case_a_passes_on_it():
    a, b = (run_local.load_cases()[k] for k in "ab")
    assert a["pr_files"]["dod/DoD_1001.md"] == "ledger_traced.md" and b["pr_files"]["dod/DoD_1001.md"] == "ledger_untraced.md"
    traced, untraced = ((SMOKE / "fixtures" / f).read_text(encoding="utf-8") for f in ("ledger_traced.md", "ledger_untraced.md"))
    assert traced.replace("test_a_purchase_earns_points |", "test_gone |") == untraced


def test_the_fake_tracker_answers_known_reads_and_refuses_everything_else():
    t = run_local.FakeTracker()
    assert t.rest("GET", "repos/octo/demo")["default_branch"] == "main"
    for method, path in (("GET", "repos/octo/demo/issues/2"), ("POST", "repos/octo/demo/issues/1001/comments"), ("PATCH", "repos/octo/demo/issues/1001")):
        with pytest.raises(GhError):
            t.rest(method, path)
    with pytest.raises(GhError):
        t.graphql("mutation { x }")
    assert not t.armed


def test_a_run_leaves_the_environment_and_the_directory_as_it_found_them(monkeypatch):
    monkeypatch.setenv("GHP_CONFIG", "/nowhere/ghp.config.json")
    before_env, before_cwd = dict(os.environ), os.getcwd()
    assert run_local.run_case("a", run_local.load_cases()["a"]) == []  # a GHP_CONFIG of the caller does not leak into the case
    assert dict(os.environ) == before_env and os.getcwd() == before_cwd


def test_main_exit_codes(capsys):
    assert run_local.main(["zzz"]) == 2 and "unknown case" in capsys.readouterr().err
    assert run_local.main(["b"]) == 0 and capsys.readouterr().out == "ok (b) a missing AC trace fails\n"
    cases = run_local.load_cases()
    run_local.load_cases = lambda: {**cases, "b": {**cases["b"], "steps": [{**cases["b"]["steps"][0], "exit": 0}]}}
    try:
        assert run_local.main(["b"]) == 1 and capsys.readouterr().out.startswith("FAIL (b)")
    finally:
        run_local.load_cases = lambda: json.loads((SMOKE / "cases.json").read_text(encoding="utf-8"))


def test_fixtures_hold_only_fictional_names():
    for f in (p for p in SMOKE.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        t = f.read_text(encoding="utf-8")
        for dom in re.findall(r"[\w.+-]+@([\w.-]+\.\w+)", t):
            assert dom == "example.invalid", (f, dom)
        assert "praveenlavu" not in t.lower() and "plavu" not in t.lower(), f
