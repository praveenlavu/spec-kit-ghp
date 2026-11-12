import argparse

import pytest

from conftest import FakeGh
from ghp import plan


def _args(*argv):
    p = argparse.ArgumentParser()
    plan.add_write_flags(p)
    return p.parse_args(list(argv))


def test_write_flags_default_to_off():
    a = _args()
    assert (a.apply, a.plan_out, a.expect_plan, a.workflow) == (False, None, None, False)
    a = _args("--apply", "--plan-out", "p.md", "--expect-plan", "q.md", "--workflow")
    assert (a.apply, a.plan_out, a.expect_plan, a.workflow) == (True, "p.md", "q.md", True)


def test_without_apply_it_does_not_arm_and_plan_out_is_written(tmp_path):
    gh, out = FakeGh(), tmp_path / "sub" / "p.md"
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "p.md.new").write_text("stale")
    plan.arm_or_refuse(_args("--plan-out", str(out)), gh, "the plan\n")
    assert not gh.armed and out.read_text() == "the plan\n" and not (tmp_path / "sub" / "p.md.new").exists()


def test_apply_arms(tmp_path):
    gh = FakeGh()
    plan.arm_or_refuse(_args("--apply"), gh, "the plan\n")
    assert gh.armed


def test_apply_with_matching_expected_plan_arms(tmp_path):
    gh, f = FakeGh(), tmp_path / "p.md"
    f.write_text("the plan\n")
    plan.arm_or_refuse(_args("--apply", "--expect-plan", str(f)), gh, "the plan\n")
    assert gh.armed and not (tmp_path / "p.md.new").exists()


@pytest.mark.parametrize("approved", ["an older plan\n", None])
def test_mismatch_or_missing_refuses_writes_new_file_and_keeps_approved(tmp_path, approved):
    gh, f = FakeGh(), tmp_path / "p.md"
    if approved is not None:
        f.write_text(approved)
    with pytest.raises(plan.PlanRefused) as e:
        plan.arm_or_refuse(_args("--apply", "--expect-plan", str(f)), gh, "the plan\n")
    assert not gh.armed and gh.calls == []
    assert (tmp_path / "p.md.new").read_text() == "the plan\n"
    assert (f.read_text() == approved) if approved is not None else not f.exists()
    assert "plan changed since approval" in str(e.value) and "\n" not in str(e.value)


def test_a_long_plan_gets_a_warning_first_line_and_still_matches_itself(tmp_path):
    text = "".join(f"line {i}\n" for i in range(plan.GATE_VISIBLE_LINES + 1))
    f = tmp_path / "p.md"
    plan.arm_or_refuse(_args("--plan-out", str(f)), FakeGh(), text)
    shown = f.read_text()
    assert shown.splitlines()[0].startswith("⚠ Preview longer than the approval gate can show") and shown.endswith(text)
    gh = FakeGh()
    plan.arm_or_refuse(_args("--apply", "--expect-plan", str(f)), gh, text)  # the same plan re-renders identical
    assert gh.armed
    assert plan.gate_preview("short\n", "x") == "short\n"


def test_empty_expect_plan_path_refuses_and_writes_no_new_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gh = FakeGh()
    with pytest.raises(plan.PlanRefused) as e:
        plan.arm_or_refuse(_args("--apply", "--expect-plan", ""), gh, "the plan\n")
    assert not gh.armed and gh.calls == [] and "\n" not in str(e.value)
    assert list(tmp_path.iterdir()) == []  # no `.new`, nothing at all


def test_empty_plan_out_path_refuses(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gh = FakeGh()
    with pytest.raises(plan.PlanRefused):
        plan.arm_or_refuse(_args("--apply", "--plan-out", ""), gh, "the plan\n")
    assert not gh.armed and list(tmp_path.iterdir()) == []


def test_unwritable_paths_are_a_one_line_refusal_and_arm_nothing(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")  # a file where a directory is needed: mkdir/write fails with OSError on every platform
    for argv in (["--plan-out", str(blocker / "p.md")],
                 ["--apply", "--plan-out", str(blocker / "p.md")],
                 ["--apply", "--expect-plan", str(blocker / "p.md")]):
        gh = FakeGh()
        with pytest.raises(plan.PlanRefused) as e:
            plan.arm_or_refuse(_args(*argv), gh, "the plan\n")
        assert not gh.armed and "\n" not in str(e.value) and str(tmp_path) not in str(e.value).replace(str(blocker), "")


def test_arm_or_refuse_itself_refuses_expect_plan_without_apply(tmp_path):
    f = tmp_path / "p.md"
    f.write_text("the plan\n")
    gh = FakeGh()
    with pytest.raises(plan.PlanRefused, match="--expect-plan needs --apply"):
        plan.arm_or_refuse(_args("--expect-plan", str(f)), gh, "the plan\n")
    with pytest.raises(plan.PlanRefused, match="--expect-plan needs --apply"):
        plan.arm_or_refuse(_args("--expect-plan", ""), gh, "the plan\n")
    assert not gh.armed
