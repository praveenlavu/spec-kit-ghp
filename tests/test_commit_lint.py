import importlib.util
import io
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "commit_lint", Path(__file__).resolve().parent.parent / "scripts" / "commit_lint.py"
)
cl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cl)


@pytest.mark.parametrize("subject", [
    "T38a Plain-English README and quickstart: AC 1,2,3",
    "T07 Issue reads and ghp get: AC 1",
    "T100 A three-digit task: AC 2",
    "docs: fix a typo",
    "chore: bump a pin",
    "Merge 1a2b3c into 4d5e6f",
])
def test_good_subjects_pass(subject):
    assert cl.ok(subject)


@pytest.mark.parametrize("subject", [
    "fix stuff",
    "T7 One-digit task: AC 1",
    "T1234 Four digits: AC 1",
    "T38 no AC part",
    "T38 Missing space after colon:AC 1",
    "T38AB Two letters: AC 1",
    "t38 lower case: AC 1",
    "docs fix without colon",
    "Merged something",
    " T38 Leading space: AC 1",
    "",
])
def test_bad_subjects_fail(subject):
    assert not cl.ok(subject)


def run(text, capsys):
    code = cl.main(io.StringIO(text))
    return code, capsys.readouterr()


def test_main_exits_0_when_every_subject_passes(capsys):
    code, out = run("T01 One: AC 1\ndocs: two\n", capsys)
    assert code == 0 and "2 commit" in out.out


def test_main_lists_each_bad_subject_and_exits_1(capsys):
    code, out = run("T01 One: AC 1\nwip\noops\n", capsys)
    assert code == 1
    assert out.out.count("::error::") == 2 and "wip" in out.out and "oops" in out.out


def test_main_fails_closed_on_no_commits(capsys):
    code, out = run("", capsys)
    assert code == 1 and "no commits" in out.out


def test_bad_subject_is_printed_safely(capsys):
    # a subject is PR text: control characters must not reach the log as workflow commands
    code, out = run("bad\x1b[31m::set-output name=x::y\x07\n", capsys)
    assert code == 1 and "\x1b" not in out.out and "\x07" not in out.out
    assert all(line.startswith("::error::") for line in out.out.splitlines()[:-1])
    long_code, long_out = run("x" * 1000 + "\n", capsys)
    assert long_code == 1 and len(long_out.out.splitlines()[0]) < 300
