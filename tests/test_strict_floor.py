"""T22b (controller ruling, T22 review F-1..F-8): under strict mode 'not checked' on a FLOOR check is a FINDING, so `strict check` fails where the advisory gate
only notes it. New tests (ado had the backstop for F-1..F-3 but not for F-6, and pinned none of it): the inputs a PR controls that turn the gate's
blocking checks into an advisory pass -- an issue number with no ledger (F-1), a deleted or re-pointed ledger (F-2), a ledger that is a symlink, a FIFO,
over 1 MiB or not UTF-8 (F-3), no AC row at all so traceability is 'not evaluated' (F-6) -- each must fail `strict check` in every phase, and the ambiguous
section 2 and the invalid config must exit 3. The unresolvable base (F-7) and a failed GitHub read are `base-unknown` / `board-unchecked`, pinned in test_strict.
F-4 (no `AC hash` line) and F-5 (a PR number) touch ac_drift only, which is not on the strict floor (ado parity, unchanged)."""
import json
import os
import signal

import pytest

from conftest import FakeGh
from ghp import __main__ as cli
from ghp import dod, strict
from test_strict import FILES, _check, _ledger, _project, _rules

PHASES = ("implement", "pr", "done")


@pytest.fixture(autouse=True)
def _deadline():
    """A hard 60 s deadline for every test here: reading a FIFO must never block."""
    signal.signal(signal.SIGALRM, lambda *a: (_ for _ in ()).throw(TimeoutError("test deadline")))
    signal.alarm(60)
    yield
    signal.alarm(0)


def _ledger_path(tmp_path):
    return tmp_path / "dod" / "DoD_1001.md"


def _floor(tmp_path, phase):
    """`strict.check` on a project whose ledger the caller has already spoiled (`ledger=False` then a file written)."""
    client = _project(tmp_path, ledger=False)
    return client, lambda: strict.check(client.gh, client.config, 1001, phase, repo=str(tmp_path))


@pytest.mark.parametrize("phase", PHASES)
def test_f1_an_issue_number_with_no_ledger_is_a_finding(tmp_path, phase):
    client = _project(tmp_path)  # issue 1001 has its ledger; the branch names 4242
    res = strict.check(client.gh, client.config, 4242, phase, repo=str(tmp_path))
    assert res["ok"] is False and _rules(res)[0] == "ledger-missing" and res["findings"][0]["fix"] == "ghp dod 4242"


@pytest.mark.parametrize("phase", PHASES)
def test_f2_a_deleted_ledger_is_a_finding(tmp_path, phase):
    client, run = _floor(tmp_path, phase)
    assert not _ledger_path(tmp_path).exists()
    res = run()
    assert res["ok"] is False and _rules(res) == ["ledger-missing"]  # nothing else can be judged without it


@pytest.mark.parametrize("phase", PHASES)
def test_f2_a_ledger_dir_re_pointed_by_dod_path_is_a_finding(tmp_path, phase):
    client = _project(tmp_path)
    client.config.dod_path = "elsewhere"  # the ledger is where dod_path said it was; now nothing is
    res = strict.check(client.gh, client.config, 1001, phase, repo=str(tmp_path))
    assert res["ok"] is False and "ledger-missing" in _rules(res)


def _symlink(path):
    target = path.parent / "real.md"
    target.write_text(_ledger(), encoding="utf-8")
    os.symlink(target, path)


def _fifo(path):
    os.mkfifo(path)


def _oversize(path):
    path.write_text(_ledger() + "x" * (1 << 20), encoding="utf-8")


def _not_utf8(path):
    path.write_bytes(b"\xff\xfe\x00 not a ledger")


@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("spoil", [_symlink, _fifo, _oversize, _not_utf8], ids=lambda f: f.__name__.strip("_"))
def test_f3_an_unreadable_ledger_is_a_finding(tmp_path, phase, spoil):
    client, run = _floor(tmp_path, phase)
    spoil(_ledger_path(tmp_path))
    res = run()
    assert res["ok"] is False and _rules(res) == ["ledger-missing"]


@pytest.mark.parametrize("phase", ["pr", "done"])
def test_f6_no_ac_row_and_no_ac_line_is_a_finding(tmp_path, phase):
    res, _ = _check(tmp_path, phase=phase, ledger=_ledger())  # section 3 has no row and section 1 no AC line: traceability is 'not evaluated'
    ev = [f for f in res["findings"] if f["rule"] == "evidence-missing"]
    assert res["ok"] is False and [f["detail"] for f in ev] == ["ledger section 3 has no AC row"]


def test_f6_one_row_or_one_ac_line_is_enough_to_have_something_to_check(tmp_path):
    for n, led in enumerate((_ledger().replace("## 1. Seeded\n", "## 1. Seeded\n\n### Acceptance criteria\nFirst thing\n"),
                             _ledger().replace("## 3. AC\n", "## 3. AC\n| AC | x | tests | ev | status |\n|---|---|---|---|---|\n| AC-1 | x | t | | green |\n"))):
        sub = tmp_path / str(n)
        sub.mkdir()
        res, _ = _check(sub, phase="pr", ledger=led)
        assert "ledger section 3 has no AC row" not in [f["detail"] for f in res["findings"]]  # the per-AC evidence finding speaks instead


def test_f6_verify_says_the_same(tmp_path):
    client = _project(tmp_path, ledger=_ledger())
    res = strict.verify(client.gh, client.config, 1001, record=False, repo=str(tmp_path))
    assert res["ok"] is False and "ledger section 3 has no AC row" in [f["detail"] for f in res["findings"]]


def test_the_cli_fails_strict_check_where_the_gate_only_notes(tmp_path, monkeypatch, capsys):
    """The pair the T22 review found: with no ledger the advisory gate exits 0 with a note; the strict backstop exits 2 with the finding."""
    _project(tmp_path, ledger=False)
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", "mode": "strict", "strict": {"test_command": "pytest -q"}}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    assert cli.main(["gate", "1001", "--offline"], gh=FakeGh()) == 0
    assert json.loads(capsys.readouterr().out)["ledger"] == "missing"
    assert cli.main(["strict", "check", "1001", "--phase", "pr", "--base", "refs/heads/nope"], gh=FakeGh()) == 3  # an unresolvable base under strict: 3, never a pass
    capsys.readouterr()
    assert cli.main(["strict", "check", "1001", "--phase", "implement"], gh=FakeGh()) == 2
    assert [f["rule"] for f in json.loads(capsys.readouterr().out)["findings"]] == ["ledger-missing"]


def test_an_ambiguous_ledger_is_exit_3_with_no_json_never_no_section(tmp_path, monkeypatch, capsys):
    client = _project(tmp_path, ledger=_ledger() + "\n" + dod.SECTION2 + "\nan old heading copied by hand\n")
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", "mode": "strict", "strict": {"test_command": "pytest -q"}}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    for phase in PHASES:
        with pytest.raises(dod.LedgerError):
            strict.check(client.gh, client.config, 1001, phase, repo=str(tmp_path))
        assert cli.main(["strict", "check", "1001", "--phase", phase], gh=client.gh) == 3
        cap = capsys.readouterr()
        assert cap.out == "" and "E_LEDGER_AMBIGUOUS_SECTION2" in cap.err and "Traceback" not in cap.err
    assert cli.main(["strict", "verify", "1001", "--no-record"], gh=client.gh) == 3
    capsys.readouterr()


def test_an_invalid_config_is_exit_3_for_every_strict_verb(tmp_path, monkeypatch, capsys):
    (tmp_path / "ghp.config.json").write_text("{not json")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    for argv in (["strict", "check", "1001", "--phase", "pr"], ["strict", "verify", "1001"], ["strict", "mode"]):
        assert cli.main(argv, gh=FakeGh()) == 3, argv
        cap = capsys.readouterr()
        assert cap.out == "" and cap.err.startswith("error: ")
    monkeypatch.chdir(tmp_path / "..")  # no config at all is a plain refusal (2), as for the gate
    assert cli.main(["strict", "mode"], gh=FakeGh()) == 2
    capsys.readouterr()


# ---- T23 fix round 1 (review I2): the order evidence of at most MAX_ROWS section 3 rows is judged; more is a finding, and git lookups are memoised ----
import subprocess  # noqa: E402
import time  # noqa: E402

from test_strict import RED_NOTE, _l1, _pair, _repo, _row  # noqa: E402


def _rows_ledger(n, status="red deadbee → green cafebab"):
    table = "| AC | x | tests | ev | status |\n|---|---|---|---|---|\n" + "".join(f"| AC-{i} | x | t | | {status} |\n" for i in range(1, n + 1))
    return _ledger().replace("## 3. AC\n", "## 3. AC\n" + table)


@pytest.fixture
def git_runs(monkeypatch):
    runs, real = [], subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda argv, *a, **k: runs.append(argv) or real(argv, *a, **k))
    return runs


def test_a_ledger_over_the_row_cap_is_one_finding_and_starts_no_git(tmp_path, git_runs):
    res, _ = _check(tmp_path, phase="pr", ledger=_rows_ledger(strict.MAX_ROWS + 1))
    l1 = [f for f in res["findings"] if f["rule"] == "l1-too-many-rows"]
    assert res["ok"] is False and len(l1) == 1 and str(strict.MAX_ROWS + 1) in l1[0]["detail"] and "sha-unknown" not in _rules(res)
    assert [a for a in git_runs if "rev-parse" in a or "cat-file" in a or "merge-base" in a] == []  # no per-row lookup (the L1 pass starts none)


def test_the_row_cap_is_a_boundary_not_a_truncation(tmp_path):
    at, _ = _check(tmp_path, phase="pr", ledger=_rows_ledger(strict.MAX_ROWS))
    assert "l1-too-many-rows" not in _rules(at) and _rules(at).count("sha-unknown") == strict.MAX_ROWS * 2  # every row is judged
    sub = tmp_path / "over"
    sub.mkdir()
    over, _ = _check(sub, phase="pr", ledger=_rows_ledger(strict.MAX_ROWS + 1))
    assert "sha-unknown" not in _rules(over) and over["ok"] is False  # nothing is judged "ok" by looking at the first 200


def test_verify_and_the_range_check_refuse_an_over_cap_ledger(tmp_path, git_env, git_runs):
    _pair(_repo(tmp_path, git_env))
    client = _project(tmp_path, ledger=_rows_ledger(strict.MAX_ROWS + 1))
    res = strict.verify(client.gh, client.config, 1001, record=False, base="main", repo=str(tmp_path))
    assert res["ok"] is False and [f["rule"] for f in res["findings"]] == ["l1-too-many-rows"] and res["recorded"] is False
    assert not [a for a in git_runs if any(str(x).startswith("--disambiguate") for x in a)]  # neither L1 nor the range check looked up a row's SHA


def test_a_hostile_15000_row_ledger_finishes_in_seconds(tmp_path, git_env):
    _pair(_repo(tmp_path, git_env))
    t0 = time.monotonic()
    res, _ = _check(tmp_path, phase="pr", ledger=_rows_ledger(15000))
    assert time.monotonic() - t0 < 10 and "l1-too-many-rows" in _rules(res) and res["ok"] is False
    t0 = time.monotonic()
    assert strict.check_l1([_row("red deadbee → green cafebab", f"AC-{i}") for i in range(15000)], str(tmp_path), "HEAD", {}, "dod", None, 1001)[0]["rule"] == "l1-too-many-rows"
    assert time.monotonic() - t0 < 2


def test_git_lookups_are_memoised_per_sha_within_a_pass(tmp_path, git_env, git_runs):
    repo = _repo(tmp_path, git_env)
    red, green = _pair(repo)
    counts = []
    for n in (1, strict.MAX_ROWS):
        del git_runs[:]
        rows = [_row(RED_NOTE.format(red, green), f"AC-{i}") for i in range(1, n + 1)]
        assert _l1(repo, rows, spec_dir=None) == []  # the pair is a valid red-then-green
        counts.append(len(git_runs))
    assert counts[0] == counts[1] and counts[0] < 25  # 200 identical pairs cost what 1 costs (without the cache: ~10 per row)


# ---- T23 fix round 1 (review M6): an OSError is a clean refusal, never a traceback ----
def test_an_os_error_is_a_clean_refusal_with_escaped_text(tmp_path, monkeypatch, capsys):
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", "mode": "strict", "strict": {"test_command": "true"}}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)

    def boom(*a, **k):
        raise PermissionError(13, "Read-only file system\x1b[31m", "dod/DoD_1001.md")
    monkeypatch.setattr(strict, "verify", boom)
    assert cli.main(["strict", "verify", "1001"], gh=FakeGh()) == 2
    cap = capsys.readouterr()
    assert cap.out == "" and cap.err.count("\n") == 1 and "Traceback" not in cap.err and "\x1b" not in cap.err
    assert cap.err.startswith("error: Read-only file system") and "dod/DoD_1001.md" in cap.err and "nothing written" in cap.err


def test_a_real_unwritable_decisions_dir_is_a_clean_refusal(tmp_path, monkeypatch, capsys):
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", "mode": "strict"}))
    (tmp_path / "dod").write_text("a file where the directory should be")  # Decisions_1001.md cannot be created
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    code = cli.main(["strict", "override", "1001", "l2", "--reason", "the lab runner has no network at all today", "--apply"], gh=FakeGh())
    cap = capsys.readouterr()
    assert code != 0 and "Traceback" not in cap.err and cap.err.startswith("error: ") and cap.err.count("\n") == 1
