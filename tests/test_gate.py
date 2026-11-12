"""T22: the DoD gate (ado tests/test_gate.py, ported with the rename table).
Tracker seam: ado's work-item dict and FakeClient became an `issues.Issue` (`test_dod._wi`: the AC is the Markdown under `## Acceptance criteria`)
and a FakeGh; ado's `dispatch(client, args)` of the gate is `gate.evaluate(gh, cfg, ref, args)` (`_eval`). The commit check looks for `#<n>` (not `AB#`).
Not ported (reason): `test_render_error_redacts_token` (ghp reads no token: the redaction is `gh.scrub`, `test_render_error_redacts_token_shaped_text`),
`test_command_commit_examples_carry_story_ref` (commands/*.md land in T32; skipped with that owner), the HTML-nesting AC test (the AC is Markdown here;
its list shapes are test_dod's). New: the AC1-3 tests of the plan (`test_commits_without_the_issue_reference_are_listed`, `test_gate_sends_no_write`),
the ambiguous-ledger and AC-drift-error cases, `test_gate_checks_match_run_gate_output_keys`."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import FakeGh, http
from ghp import gate
from ghp.config import Config, ConfigInvalid
from ghp.dod import SECTION2, LedgerError, ac_hash, render_dod
from ghp.gate import (DEFAULT_TESTS, check_commits, check_drift, check_traceability, discover_tests,
                      missing_evidence, parse_section3, render_summary, run_gate, scan_assertions,
                      test_present)
from ghp.gh import Gh, GhError
from ghp.refs import Ref
from rules_support import desc, fail, make_issue, raw_issue, serve
from test_dod import _wi as _issue

HDR = "| AC | acceptance criterion | tests | commit/PR | status |\n|---|---|---|---|---|\n"
LEDGER = f"""# DoD — US1001
{SECTION2}
## 3. AC → tests (traceability)
{HDR}| AC-1 | does X | FooTest.testX | abc123 | green |
| AC-2 | does Y | BarTest.testY, BarTest.testY2 |  | red |
## 4. Gate log
"""
APEX = "@IsTest private class FooTest { @IsTest static void testX(){ System.assert(true); } }"
SOURCES = {"FooTest.cls": APEX}


def _wi(ac):
    """The live issue whose acceptance criteria are `ac` (Markdown lines under `## Acceptance criteria`)."""
    return _issue(ac)


def _ledger_for(ac):
    return render_dod(_wi(ac), synced="2026-07-14")


# ---- ported graceful-degradation cases ----
def test_parse_section3_extracts_rows():
    assert parse_section3(LEDGER) == [
        {"ac": "AC-1", "tests": ["FooTest.testX"], "evidence": "abc123", "status": "green"},
        {"ac": "AC-2", "tests": ["BarTest.testY", "BarTest.testY2"], "evidence": "", "status": "red"},
    ]


def test_parse_section3_graceful_on_missing_or_placeholder():
    assert parse_section3("no section 3 here") == []
    row = f"{SECTION2}\n## 3. AC\n| AC-1 | x | _(test IDs, …)_ | _(commit / PR)_ | _(red→green)_ |"
    assert parse_section3(row) == [{"ac": "AC-1", "tests": [], "evidence": "", "status": ""}]


def test_check_traceability_maps_and_flags():
    r = check_traceability(parse_section3(LEDGER), SOURCES)
    assert r["evaluated"] is True
    assert r["mapped"] == ["AC-1"]
    assert r["unmapped"] == ["AC-2"]


def test_check_traceability_graceful_when_no_rows():
    r = check_traceability([], SOURCES)
    assert r["evaluated"] is False
    assert "insufficient" in r["note"].lower()
    assert r["unmapped"] == []


def test_run_gate_advisory_by_default_even_with_gaps():
    r = run_gate(LEDGER, SOURCES, {"BTest.cls": "@IsTest class BTest{ @IsTest static void m(){} }"})
    assert r["traceability"]["unmapped"] == ["AC-2"]
    assert r["assertion_gaps"] == [{"file": "BTest.cls", "issue": "no assertions"}]
    assert r["blocking"] is False


def test_run_gate_blocks_only_opted_in_present_failure():
    assert run_gate(LEDGER, SOURCES, {}, block=["ac_traceability"])["blocking"] is True


def test_run_gate_untouched_template_blocks_only_on_traceability():
    ledger = _ledger_for("x")
    assert run_gate(ledger, {}, {}, block=["ac_traceability"])["blocking"] is True  # AC-1 has no test
    r = run_gate(ledger, {}, {}, block=["assertion", "ac_drift"])
    assert r["blocking"] is False
    assert r["missing_evidence"] == []      # placeholder status "_(red→green)_" is not green


def test_run_gate_blocks_only_named_missing_test():
    ledger = f"{SECTION2}\n## 3. AC\n{HDR}| AC-1 | x | Gone.testX | | red |\n"
    assert run_gate(ledger, {}, {}, block=["ac_traceability"])["blocking"] is True
    assert run_gate(ledger, {"Gone.cls": "class Gone { testX }"}, {}, block=["ac_traceability"])["blocking"] is False


# ---- test_present ----
def test_trace_pytest_id():
    assert test_present("tests/test_calc.py::test_add", {"tests/test_calc.py": "def test_add(): assert 1"})
    assert not test_present("tests/test_calc.py::test_sub", {"tests/test_calc.py": "def test_add(): assert 1"})
    assert not test_present("tests/test_calc.py::test_add", {"tests/other.py": "def test_add(): assert 1"})


def test_trace_jest_describe_it():
    src = {"cart.test.ts": "describe('Cart', () => { it('adds item', () => { expect(1) }) })"}
    assert test_present("Cart > adds item", src)
    assert not test_present("Cart > removes item", src)


def test_trace_apex_class_method():
    assert test_present("FooTest.testX", SOURCES)
    assert not test_present("FooTest.testZ", SOURCES)


def test_segments_must_share_a_file():
    assert not test_present("Foo.testX", {"a.txt": "Foo", "b.txt": "testX"})
    assert not test_present("", SOURCES)


# ---- scan_assertions ----
def test_unknown_language_changed_file_ignored():
    assert scan_assertions({"notes.txt": "nothing", "src/app.py": "x = 1"}, DEFAULT_TESTS) == []


@pytest.mark.parametrize("path", [
    "tests/test_a.py", "web/cart.test.ts", "src/CartTest.java",
    "src/CartTests.cs", "pkg/cart_test.go", "classes/CartTest.cls",
])
def test_no_assertions_flagged_per_language(path):
    assert scan_assertions({path: "// nothing checked here\n"}, DEFAULT_TESTS) == [
        {"file": path, "issue": "no assertions"}]


@pytest.mark.parametrize("path,text", [
    ("tests/test_a.py", "assert 1"), ("web/cart.test.ts", "expect(1)"),
    ("src/CartTest.java", "assertEquals(1,1)"), ("src/CartTests.cs", "Assert.Equal(1,1)"),
    ("pkg/cart_test.go", "t.Error(1)"), ("classes/CartTest.cls", "System.assert(true)"),
])
def test_assertions_present_not_flagged(path, text):
    assert scan_assertions({path: text}, DEFAULT_TESTS) == []


def test_discover_tests_globs_and_skips_unreadable(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("assert 1")
    (tmp_path / "test_top.py").write_text("assert 2")
    (tmp_path / "tests" / "test_bad.py").write_bytes(b"\xff\xfe\x00bad")
    (tmp_path / "app.py").write_text("x")
    found = discover_tests(str(tmp_path), DEFAULT_TESTS)
    assert sorted(p.rsplit("/", 1)[-1] for p in found) == ["test_a.py", "test_top.py"]


# ---- SEC3: file confinement (a PR must not use gate.tests regexes as a content oracle) ----
def _sandbox(tmp_path):
    src, outside = tmp_path / "src", tmp_path / "outside"
    src.mkdir()
    outside.mkdir()
    return src, outside


def test_discover_tests_never_reads_symlinks_or_outside_files(tmp_path):
    src, outside = _sandbox(tmp_path)
    (outside / "secret.txt").write_text("ALPHA")
    (src / "real.py").write_text("assert 1")
    (src / "inside_target.txt").write_text("assert 2")
    (src / "test_out.py").symlink_to(outside / "secret.txt")  # leaves --src
    (src / "test_in.py").symlink_to(src / "inside_target.txt")  # stays inside, still skipped
    (src / "linkdir").symlink_to(outside, target_is_directory=True)
    (outside / "test_viadir.py").write_text("assert 3")
    skipped = set()
    found = discover_tests(str(src), {"**/*.py": "assert"}, skipped)
    assert [p.rsplit("/", 1)[-1] for p in found] == ["real.py"]
    assert len(skipped) == 2  # linkdir is never descended, so it is not even a candidate


def test_discover_tests_skips_fifo_and_oversize_without_blocking(tmp_path):
    import os
    from ghp.gate import MAX_READ
    src, _ = _sandbox(tmp_path)
    os.mkfifo(src / "test_fifo.py")  # open() on it would hang without O_NONBLOCK
    (src / "test_big.py").write_text("assert 1\n" + "#" * MAX_READ)
    (src / "test_ok.py").write_text("assert 1")
    skipped = set()
    found = discover_tests(str(src), {"**/*.py": "assert"}, skipped)
    assert [p.rsplit("/", 1)[-1] for p in found] == ["test_ok.py"] and len(skipped) == 2


def test_read_returns_a_file_of_exactly_the_cap_and_rejects_one_byte_more(tmp_path):
    from ghp.gate import MAX_READ, _read
    (tmp_path / "ok.py").write_text("a" * MAX_READ)
    (tmp_path / "big.py").write_text("a" * (MAX_READ + 1))
    skipped = set()
    assert _read(tmp_path / "ok.py", str(tmp_path), skipped) == "a" * MAX_READ and not skipped
    assert _read(tmp_path / "big.py", str(tmp_path), skipped) is None and len(skipped) == 1


def test_read_skips_a_path_through_a_symlinked_directory_that_leaves_src(tmp_path):
    from ghp.gate import _read
    src, outside = _sandbox(tmp_path)
    (outside / "x.py").write_text("assert 1")
    (src / "d").symlink_to(outside, target_is_directory=True)
    skipped = set()
    assert _read(src / "d" / "x.py", str(src), skipped) is None and len(skipped) == 1


def test_read_tolerates_a_swapped_in_symlink_via_o_nofollow(tmp_path, monkeypatch):
    from ghp import gate
    src, outside = _sandbox(tmp_path)
    (outside / "x.txt").write_text("ALPHA")
    (src / "t.py").symlink_to(outside / "x.txt")
    monkeypatch.setattr(gate.os.path, "islink", lambda p: False)  # the check lost the race: O_NOFOLLOW must still refuse
    skipped = set()
    assert gate._read(src / "t.py", str(src), skipped) is None and len(skipped) == 1


def test_oracle_probe_leaks_nothing(cfg, tmp_path, monkeypatch):
    """Two outside files starting A-M and N-Z, symlinked into the repo; regexes that would tell them apart."""
    src, outside = _sandbox(tmp_path)
    (outside / "one").write_text("ALPHA")
    (outside / "two").write_text("ZULU")
    for n in ("one", "two"):
        (src / f"test_{n}.py").symlink_to(outside / n)
    (src / "DoD_7.md").write_text(_ledger_for("x"))
    cfg.dod_path = str(src)
    cfg.gate["tests"] = {"**/test_*.py": "[A-M]", "**/test_two.py": "[N-Z]"}
    monkeypatch.chdir(src)
    out = _run(cfg, _args(src=".", changed=["./test_one.py", "./test_two.py"], offline=True))
    assert out["assertion_gaps"] == [] and out["assertion_notes"] == []
    assert out["skipped_note"] == "2 file(s) skipped: symlink, outside --src, not a regular file, or over 1 MiB"
    text = render_summary(out)
    assert "Files skipped:" in text and not any(w in text for w in ("test_one.py", "test_two.py", str(outside), "ALPHA", "ZULU"))
    # a regular file with the same content is still judged
    (src / "test_real.py").write_text("ZZZ")
    out = _run(cfg, _args(src=".", changed=["./test_real.py"], offline=True))
    assert out["assertion_gaps"] == [{"file": "./test_real.py", "issue": "no assertions"}]


def test_changed_path_outside_src_is_not_read(cfg, tmp_path):
    src, outside = _sandbox(tmp_path)
    (outside / "test_x.py").write_text("no asserts here")
    (src / "DoD_7.md").write_text(_ledger_for("x"))
    cfg.dod_path = str(src)
    out = _run(cfg, _args(src=str(src), changed=[str(outside / "test_x.py")], offline=True))
    assert out["assertion_gaps"] == [] and "1 file(s) skipped" in out["skipped_note"]


def test_symlinked_parent_counts_a_missing_and_an_existing_target_alike(tmp_path):
    from ghp.gate import _read
    src, outside = _sandbox(tmp_path)
    (outside / "secret").write_text("ALPHA")
    (src / "dlink").symlink_to(outside, target_is_directory=True)
    a, b = set(), set()
    assert _read(src / "dlink" / "secret", str(src), a) is None
    assert _read(src / "dlink" / "nope", str(src), b) is None
    assert len(a) == len(b) == 1
    c = set()  # a missing file that really is under --src stays uncounted, as before
    assert _read(src / "gone.py", str(src), c) is None and not c


def test_changed_through_a_symlinked_parent_gives_the_same_note_for_existing_and_missing(cfg, tmp_path):
    src, outside = _sandbox(tmp_path)
    (outside / "secret").write_text("ALPHA")
    (src / "dlink").symlink_to(outside, target_is_directory=True)
    (src / "DoD_7.md").write_text(_ledger_for("x"))
    cfg.dod_path = str(src)
    notes = [_run(cfg, _args(src=str(src), changed=[str(src / "dlink" / n)], offline=True))["skipped_note"]
             for n in ("secret", "nope")]
    assert notes[0] == notes[1] and notes[0].startswith("1 file(s) skipped")


def _ledger_cases(tmp_path):
    import os
    src, outside = _sandbox(tmp_path)
    (outside / "ledger.md").write_text(LEDGER)  # ledger-shaped content outside --src
    os.mkfifo(src / "fifo.md")
    return src, {"outside-symlink": outside / "ledger.md", "dev-zero": Path("/dev/zero"), "fifo": src / "fifo.md"}


@pytest.mark.parametrize("kind", ["outside-symlink", "dev-zero", "fifo"])
def test_gate_ledger_is_read_confined(cfg, tmp_path, kind):
    src, targets = _ledger_cases(tmp_path)
    if kind != "fifo":
        (src / "DoD_7.md").symlink_to(targets[kind])
    else:
        (src / "DoD_7.md").unlink(missing_ok=True)
        (src / "fifo.md").rename(src / "DoD_7.md")
    cfg.dod_path = str(src)
    out = _run(cfg, _args(src=str(src), offline=True))  # a hang here would be the bug
    assert out["ledger"] == "unreadable" and out["blocking"] is False
    assert "unreadable" in out["note"] and "FooTest" not in str(out) and "AC-1" not in str(out)
    assert str(targets[kind]) not in out["note"] or kind == "fifo"  # the configured path may be named; a symlink target never


def test_gate_ledger_inside_src_still_reads(cfg, tmp_path):
    (tmp_path / "DoD_7.md").write_text(LEDGER)
    cfg.dod_path = str(tmp_path)
    out = _run(cfg, _args(src=str(tmp_path), offline=True))
    assert "traceability" in out


# ---- drift / evidence ----
def test_drift_detected():
    ledger = _ledger_for("old criteria")
    assert check_drift(ledger, _wi("new criteria")) == {"evaluated": True, "drift": True}
    assert check_drift(ledger, _wi("old criteria"))["drift"] is False


def test_drift_not_evaluated_without_hash():
    assert check_drift("no header", _wi("x"))["evaluated"] is False
    assert check_drift(_ledger_for("x"), None)["evaluated"] is False


def test_missing_evidence():
    assert missing_evidence(parse_section3(f"{SECTION2}\n## 3.\n{HDR}| AC-1 | a | T.x |  | green |\n"
                                           "| AC-2 | b | T.y | pr-1 | green |\n"
                                           "| AC-3 | c | T.z |  | red |\n")) == ["AC-1"]


def test_block_ac_drift_sets_blocking():
    ledger = _ledger_for("old")
    assert run_gate(ledger, {}, {}, _wi("new"), block=["ac_drift"])["blocking"] is True
    assert run_gate(ledger, {}, {}, _wi("new"), block=[])["blocking"] is False
    assert run_gate(ledger, {}, {}, _wi("old"), block=["ac_drift"])["blocking"] is False


def test_missing_info_never_blocks():
    r = run_gate("", {}, {}, None, block=["ac_traceability", "assertion", "ac_drift"])
    assert r["blocking"] is False
    assert r["drift"]["evaluated"] is False


# ---- CLI ----
REF7 = Ref("octo", "demo", 7)


def _args(**kw):
    class Args:
        command, ref, src, changed, offline, base, summary_out = "gate", "7", ".", [], False, None, None
    for k, v in kw.items():
        setattr(Args, k, v)
    return Args()


def _run(cfg, args, gh=None):
    """ado's `dispatch(FakeClient(cfg), args)`: the gate's result dict for issue 7. A FakeGh with no route fails loudly on any request."""
    return gate.evaluate(FakeGh() if gh is None else gh, cfg, REF7, args)


@pytest.fixture
def cfg():
    return Config(owner="octo", repo="demo")


def _live(ac):
    """A FakeGh that serves issue 7 with acceptance criteria `ac`."""
    return serve(raw_issue(7, body="## Acceptance criteria\n" + ac + "\n"))


def test_config_tests_override_glob(cfg, tmp_path):
    (tmp_path / "DoD_7.md").write_text("## 3\n")
    f = tmp_path / "test_a.py"
    f.write_text("assert 1")
    cfg.dod_path = str(tmp_path)
    cfg.gate["tests"] = {"**/test_*.py": "MAGIC"}
    out = _run(cfg, _args(src=str(tmp_path), changed=[str(f)], offline=True))
    assert out["assertion_gaps"] == [{"file": str(f), "issue": "no assertions"}]


def test_gate_cli_offline_does_not_fetch(cfg, tmp_path):
    (tmp_path / "DoD_7.md").write_text(_ledger_for("x"))
    cfg.dod_path = str(tmp_path)
    gh = FakeGh()
    out = _run(cfg, _args(src=str(tmp_path), offline=True), gh)
    assert gh.calls == [] and out["drift"]["evaluated"] is False


@pytest.mark.parametrize("err", [GhError("E_GH_HTTP", 502, "down"), GhError("E_GH_HTTP", 404, "gone"), ValueError("not an issue")])
def test_gate_cli_fetch_failure_degrades(cfg, tmp_path, err):
    (tmp_path / "DoD_7.md").write_text(_ledger_for("x"))
    cfg.dod_path = str(tmp_path)
    cfg.gate["block"] = ["ac_drift"]
    out = _run(cfg, _args(src=str(tmp_path)), fail(err, 7))
    assert out["drift"]["evaluated"] is False and out["blocking"] is False


def test_gate_cli_drift_blocks(cfg, tmp_path):
    (tmp_path / "DoD_7.md").write_text(_ledger_for("old"))
    cfg.dod_path = str(tmp_path)
    cfg.gate["block"] = ["ac_drift"]
    assert _run(cfg, _args(src=str(tmp_path)), _live("new"))["blocking"] is True
    assert _run(cfg, _args(src=str(tmp_path)), _live("old"))["blocking"] is False
    # the live issue lost its AC heading (or has it twice): no readable AC is drift, never a pass and never a crash
    for body in ("no heading at all", "## Acceptance criteria\nold\n## Acceptance criteria\nold\n"):
        out = _run(cfg, _args(src=str(tmp_path)), serve(raw_issue(7, body=body)))
        assert out["drift"] == {"evaluated": True, "drift": True} and out["blocked_by"] == ["ac_drift"] and out["blocking"] is True


# ---- fix round 1 ----
def test_discover_tests_prunes_vendored_dirs(tmp_path):
    for d in ("tests", "node_modules/x", ".venv/lib/site", "a/.git", "build"):
        (tmp_path / d).mkdir(parents=True)
        (tmp_path / d / "test_calc.py").write_text("def test_add(): assert 1")
    found = discover_tests(str(tmp_path), DEFAULT_TESTS)
    assert [p.replace(str(tmp_path), "") for p in found] == ["/tests/test_calc.py"]


def test_path_id_requires_path_boundary():
    src = {"tests/my_test_calc.py": "def test_add(): assert 1"}
    assert not test_present("test_calc.py::test_add", src)
    assert test_present("my_test_calc.py::test_add", src)
    assert test_present("tests/my_test_calc.py::test_add", src)
    assert test_present("test_calc.py::test_add", {"/abs/tests/test_calc.py": "def test_add(): pass"})
    assert test_present("test_calc.py::test_add", {"test_calc.py": "def test_add(): pass"})


def test_run_gate_graceful_never_blocks_info_poor():
    r = run_gate("no section 3", {}, {}, block=["ac_traceability", "assertion", "ac_drift"])
    assert r["traceability"]["evaluated"] is False
    assert r["blocking"] is False
    assert scan_assertions({}, DEFAULT_TESTS) == []


# ---- fix round 2 ----
@pytest.mark.parametrize("pattern,path,ok", [
    ("tests/**/test_*.py", "tests/test_x.py", True),
    ("tests/**/test_*.py", "tests/a/b/test_x.py", True),
    ("tests/**/test_*.py", "tests/a/b/x.py", False),
    ("src/**/*.spec.ts", "src/a/b/x.spec.ts", True),
    ("src/**/*.spec.ts", "lib/x.spec.ts", False),
    ("**/*.test.[jt]s", "a.test.js", True),
    ("**/*.test.[jt]s", "d/a.test.ts", True),
    ("**/*.test.[jt]s", "d/a.test.py", False),
    ("**/test_*.py", "/abs/proj/tests/test_x.py", True),
    ("tests/**", "tests/a/b.py", True),
    ("**/*Test.java", "src/AppTest.java.bak", False),
    ("**/**/test_*.py", "a/b/test_x.py", True),  # a run of `**/` is one `**/` (ado did not collapse it)
    ("**/**/**/test_*.py", "test_x.py", True),
    ("**/**/test_*.py", "a/b/x.py", False),
])
def test_glob_match(pattern, path, ok):
    from ghp.gate import _glob_match
    assert _glob_match(path, pattern) is ok


def test_discover_tests_interior_doublestar(tmp_path):
    for d in ("tests", "tests/a/b", "lib"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    for f in ("tests/test_x.py", "tests/a/b/test_y.py", "lib/test_z.py"):
        (tmp_path / f).write_text("assert 1")
    found = discover_tests(str(tmp_path), {"tests/**/test_*.py": "assert"})
    assert sorted(p.replace(str(tmp_path), "") for p in found) == ["/tests/a/b/test_y.py", "/tests/test_x.py"]


def test_changed_interior_doublestar_config_glob():
    tests = {"tests/**/check_*.py": "MAGIC"}
    assert scan_assertions({"tests/a/b/check_x.py": "assert 1"}, tests) == [
        {"file": "tests/a/b/check_x.py", "issue": "no assertions"}]
    assert scan_assertions({"lib/check_x.py": "assert 1"}, tests) == []


def test_empty_normalised_path_id_is_not_present():
    assert not test_present("./::test_x", {"a.py": "def test_x(): pass"})
    assert not test_present("::test_x", {"a.py": "def test_x(): pass"})


# ---- fix round 3 ----
def test_discover_tests_src_is_the_literal_leading_dir(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.spec.ts").write_text("expect(1)")
    found = discover_tests(str(tmp_path / "src"), {"src/*.spec.ts": r"\bexpect\("})
    assert [p.replace(str(tmp_path), "") for p in found] == ["/src/x.spec.ts"]


def test_malformed_glob_raises_value_error(tmp_path):
    with pytest.raises(ValueError, match="invalid glob in gate.tests"):
        discover_tests(str(tmp_path), {"**/[!]": "x"})
    with pytest.raises(ValueError):
        scan_assertions({"a.py": "x"}, {"**/[!]": "x"})


def test_empty_rest_after_path_is_not_present():
    src = {"tests/test_calc.py": "def test_add(): assert 1"}
    assert not test_present("tests/test_calc.py::", src)
    assert not test_present("tests/test_calc.py:: > .", src)


# ---- PR #1 review fix A: AC rows only from the authored section 3 ----
def test_parse_section3_ignores_ac_table_in_section1():
    md = render_dod(_wi("| AC-1 | Given x | test_anything | abc | green |"))
    assert "| AC-1 | Given x |" in md               # the trap is really in section 1
    assert [r["ac"] for r in parse_section3(md)] == ["AC-1"]
    assert parse_section3(md)[0]["tests"] == []   # the template row, not the copied one


def test_parse_section3_stops_at_next_heading():
    md = f"{SECTION2}\n## 3. AC\n{HDR}| AC-1 | a | T.x | | red |\n## 4. Log\n| AC-9 | b | T.y | | red |\n"
    assert [r["ac"] for r in parse_section3(md)] == ["AC-1"]


# ---- PR #1 review fix C: an AC row with no test blocks under ac_traceability ----
def test_run_gate_empty_or_placeholder_tests_cell_blocks():
    assert run_gate(_ledger_for("x"), {}, {}, block=["ac_traceability"])["blocking"] is True
    empty = f"{SECTION2}\n## 3. AC\n{HDR}| AC-1 | x |  |  | red |\n"
    assert run_gate(empty, {}, {}, block=["ac_traceability"])["blocking"] is True
    assert run_gate(empty, {}, {}, block=[])["blocking"] is False


def test_run_gate_no_ac_rows_not_evaluated_never_blocks():
    r = run_gate(f"{SECTION2}\n## 3. AC\n{HDR}", {}, {}, block=["ac_traceability"])
    assert r["traceability"]["evaluated"] is False and r["blocking"] is False


def test_missing_section3_row_is_unmapped():
    ac = "- Given a\n- Then b\n- And c"
    row = "| AC-1 | _(what it must do)_ | _(test IDs, …)_ | _(commit / PR)_ | _(red→green)_ |"
    def ledger(*ids):
        return _ledger_for(ac).replace(row, "\n".join(f"| {i} | x | FooTest.testX | | red |" for i in ids))
    assert run_gate(ledger("AC-1", "AC-2", "AC-3"), SOURCES, {})["traceability"]["unmapped"] == []
    t = run_gate(ledger("AC-1", "AC-2"), SOURCES, {}, block=["ac_traceability"])  # AC-3 row deleted
    assert t["traceability"]["unmapped"] == ["AC-3"] and "AC-3" in t["traceability"]["note"]
    assert t["blocking"] is True
    assert check_traceability([], SOURCES, ac_count=2)["unmapped"] == ["AC-1", "AC-2"]
    # a row beyond n is evaluated normally
    assert run_gate(ledger("AC-1", "AC-2", "AC-3", "AC-9"), SOURCES, {})["traceability"]["unmapped"] == []
    assert run_gate(ledger("AC-1", "AC-2", "AC-3").replace("AC-3 | x | FooTest.testX", "AC-9 | x | Gone.t"),
                    SOURCES, {})["traceability"]["unmapped"] == ["AC-9", "AC-3"]


def test_nested_block_html_ac_rows_pass_blocking_traceability():
    row = "| AC-1 | _(what it must do)_ | _(test IDs, …)_ | _(commit / PR)_ | _(red→green)_ |"
    for ac in ("<ul><li><div>Given a</div></li><li><div>Then b</div></li></ul>",
               "<ol><li><p>Given a</p></li><li><p>Then b</p></li></ol>"):
        md = _ledger_for(ac).replace(row, "| AC-1 | a | FooTest.testX | | red |\n| AC-2 | b | FooTest.testX | | red |")
        r = run_gate(md, SOURCES, {}, block=["ac_traceability"])
        assert r["traceability"]["unmapped"] == [] and r["blocking"] is False


# ---- PR #1 review fix B: test ids match whole identifiers, not substrings ----
def test_test_id_segments_match_whole_identifiers():
    src = {"tests/test_cart.py": "def test_add_many(): assert 1\nclass LoginTest:\n def testLoginFails(): pass"}
    assert not test_present("tests/test_cart.py::test_add", src)
    assert not test_present("test_add", src)
    assert not test_present("LoginTest.testLogin", src)
    assert test_present("tests/test_cart.py::test_add_many", src)
    assert test_present("LoginTest.testLoginFails", src)


# ---- fix round 2: an inline marker mention in section 4 must not hide section 3 ----
def test_inline_marker_in_section4_does_not_hide_section3():
    md = (f"{SECTION2}\n## 3. AC\n{HDR}| AC-1 | x | Gone.testX | | red |\n"
          f"## 4. Gate & review log\n- reviewer: re-read `{SECTION2}` before merging\n")
    assert [r["ac"] for r in parse_section3(md)] == ["AC-1"]
    assert run_gate(md, {}, {}, block=["ac_traceability"])["blocking"] is True


# ---- H5: render_summary / render_error drive the CI comment ----
def test_summary_blocking_lists_blocking_findings():
    from ghp.gate import render_summary
    md = render_summary(run_gate(LEDGER, SOURCES, {}, block=["ac_traceability"]))
    blocking, _, advisory = md.partition("**Advisory")
    assert "**Blocking" in blocking and "`AC-2`" in blocking
    assert "`AC-2`" not in advisory


def test_summary_advisory_only():
    from ghp.gate import render_summary
    r = run_gate(LEDGER.replace("abc123", ""), SOURCES, {"BTest.cls": "@IsTest class BTest{}"})
    md = render_summary(r)
    assert "**Blocking" not in md
    assert "`AC-2`" in md and "`BTest.cls`" in md and "`AC-1`" in md  # unmapped, gap, no evidence
    assert "drift not checked" in md


def test_summary_all_clean():
    from ghp.gate import render_summary
    clean = f"{SECTION2}\n## 3. AC\n{HDR}| AC-1 | x | FooTest.testX | abc | green |\n"
    md = render_summary(run_gate(clean, SOURCES, {}))
    assert "**Blocking" not in md and "**Advisory" not in md and "No findings" in md


@pytest.mark.parametrize("now,block,line", [
    ("new", [], "AC changed in GitHub since the ledger was seeded"),
    ("old", [], "no drift"),
    (None, [], "drift not checked"),
])
def test_summary_drift_line(now, block, line):
    from ghp.gate import render_summary
    md = render_summary(run_gate(_ledger_for("old"), {}, {}, now and _wi(now), block=block))
    assert line in md


def test_summary_drift_blocking_is_listed_as_blocking():
    from ghp.gate import render_summary
    md = render_summary(run_gate(_ledger_for("old"), {}, {}, _wi("new"), block=["ac_drift"]))
    assert "AC changed in GitHub" in md.partition("**Advisory")[0].partition("**Blocking")[2]


def test_summary_missing_ledger_is_advisory_note():
    from ghp.gate import render_summary
    md = render_summary({"ledger": "missing", "blocking": False, "note": "no ledger at dod/DoD_7.md"})
    assert "no ledger" in md and "**Blocking" not in md


def test_summary_untrusted_text_is_one_capped_code_span():
    from ghp.gate import render_summary
    evil = "x`y\n## pwn | z" + "a" * 500
    r = run_gate("", {}, {}) | {"assertion_gaps": [{"file": evil, "issue": "no assertions"}]}
    md = render_summary(r)
    (line,) = [ln for ln in md.splitlines() if "pwn" in ln]
    assert line.count("`") == 2 and "## pwn | z" in line
    assert "\n## pwn" not in md and len(line) < 300


def test_render_error_redacts_token_shaped_text():
    """ado redacted the value of $ADO_TOKEN; ghp reads no token, so token-shaped text is scrubbed (`gh.scrub`) instead."""
    from ghp.gate import render_error
    canary = "gh" + "p_" + "A1b2C3d4E5" * 4  # runtime-built: a token-shaped string that appears in no file
    md = render_error(f"401 for {canary}\nat https://api.github.com/repos/octo/demo")
    assert canary not in md and "could not run" in md and "***" in md
    assert "advisory" in md


def test_render_error_fails_for_a_bad_config_and_an_unreadable_ledger():
    from ghp.gate import render_error
    assert "ghp gate FAILED: ghp.config.json is invalid" in render_error(ConfigInvalid("x"))
    assert "ghp gate FAILED: target branch trust files are invalid" in render_error(ConfigInvalid("ghp.stack.json at the target branch is bad"))
    md = render_error(LedgerError("E_LEDGER_AMBIGUOUS_SECTION2", "two headings"))
    assert "FAILED: the ledger cannot be read safely" in md and "advisory" not in md
    assert "advisory" in render_error(ValueError("x")) and "FAILED" not in render_error(GhError("E_GH_HTTP", 502, "down"))


# ---- F14: per-test assertion check (Python via ast) ----
def _ledger_tests(*ids):
    return f"{SECTION2}\n## 3. AC → tests (traceability)\n{HDR}| AC-1 | a | {', '.join(ids)} |  | red |\n"


def _gate_py(src, *ids, path="tests/test_x.py"):
    return run_gate(_ledger_tests(*ids), {path: src}, {})


def test_assertion_free_mapped_test_flagged_in_asserting_file():
    src = "def test_x(): pass\n\ndef test_y():\n    assert 1\n"
    r = _gate_py(src, "tests/test_x.py::test_x", "tests/test_x.py::test_y")
    assert r["assertion_gaps"] == [{"test": "tests/test_x.py::test_x", "issue": "no assertions"}]


def test_class_method_form_checked_per_test():
    src = ("class TestA:\n    def test_m(self): pass\n"
           "class TestB:\n    def test_m(self): assert 1\n")
    r = _gate_py(src, "TestA.test_m", "tests/test_x.py::TestB::test_m")
    assert r["assertion_gaps"] == [{"test": "TestA.test_m", "issue": "no assertions"}]


@pytest.mark.parametrize("body", [
    "with pytest.raises(ValueError):\n        int('x')",
    "pytest.fail('no')",
    "self.assertEqual(1, 1)",
    "m.assert_called_once()",
    "if True:\n        assert 1",
    "with raises(ValueError):\n        int('x')",
    "assert_allclose(1, 1)",
    "assert_that(1).is_equal_to(1)",
    "with pt.raises(ValueError):\n        int('x')",
    "with pytest.warns(UserWarning):\n        pass",
    "self.fail('no')",
    "raise AssertionError('no')",
    "raise AssertionError",
    "self._chk()",
    "check(1)",
])
def test_assertion_forms_count(body):
    src = ("import pytest\nimport pytest as pt\nfrom pytest import raises\n"
           "def check(x):\n    assert x\n"
           f"class T:\n    def _chk(self):\n        self.assertTrue(1)\n\n    def test_a(self):\n        {body}\n")
    assert _gate_py(src, "T.test_a")["assertion_gaps"] == []


def test_helper_without_assertion_does_not_count():
    src = "def check(x): return x\ndef test_a(): check(1)\n"
    assert _gate_py(src, "test_a")["assertion_gaps"] == [{"test": "test_a", "issue": "no assertions"}]


def test_deeply_nested_source_notes_not_crash():
    r = _gate_py("def test_a(): x = " + "(" * 5000 + "1" + ")" * 5000, "test_a")
    assert r["assertion_gaps"] == [] and any("parse" in n for n in r["assertion_notes"])


def test_unparseable_python_notes_not_crash_or_block():
    r = run_gate(_ledger_tests("tests/test_x.py::test_a"), {"tests/test_x.py": "def test_a(:\n"}, {}, block=["assertion"])
    assert r["assertion_gaps"] == [] and r["blocking"] is False
    assert any("test_x.py" in n and "parse" in n for n in r["assertion_notes"])


def test_jest_mapped_test_is_note_only():
    src = "describe('Cart', () => { it('adds', () => { }); });"
    r = run_gate(_ledger_tests("Cart > adds"), {"web/cart.test.ts": src}, {}, block=["assertion"])
    assert r["assertion_gaps"] == [] and r["blocking"] is False
    assert any("Python only" in n for n in r["assertion_notes"])


def test_python_only_mapping_has_no_note():
    assert _gate_py("def test_x(): assert 1", "test_x")["assertion_notes"] == []


def test_summary_shows_per_test_gap_and_note():
    from ghp.gate import render_summary
    r = _gate_py("def test_x(): pass", "test_x") | {"assertion_notes": ["per-test checking covers Python only"]}
    md = render_summary(r)
    assert "`test_x`" in md and "Python only" in md


def test_offline_drift_note():
    from ghp.gate import render_summary
    led = _ledger_for("x")
    assert check_drift(led, None, "offline — drift not checked")["note"] == "offline — drift not checked"
    assert check_drift(led, None)["note"] == "GitHub not reachable or gh not signed in — drift not checked"
    assert check_drift("no header", _wi("x"))["note"] == "no AC hash in the ledger — drift not checked"
    md = render_summary(run_gate(led, {}, {}, None, reason="offline — drift not checked"))
    assert "AC drift: offline — drift not checked" in md


# ---- commits_without_story (N5) ----
ROOT = Path(__file__).resolve().parent.parent


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True).stdout.strip()


def _commit(repo, msg, name="f"):
    (repo / name).write_text(msg)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "--short", "HEAD")


@pytest.fixture
def crepo(tmp_path, git_env):
    _git(tmp_path, "init", "-q", "-b", "main")
    _commit(tmp_path, "init")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(tmp_path, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    _git(tmp_path, "switch", "-q", "-c", "feature/gh2-x")
    return tmp_path


def test_commits_without_the_issue_reference_are_listed(crepo):
    """T22 AC2: `#<n>` (not `AB#<n>`) in `<base>..HEAD` messages; merge commits are ignored (`test_merge_commits_ignored`)."""
    ok = _commit(crepo, "feat: a\n\nbody #2", "a")
    bad = _commit(crepo, "feat: b", "b")
    other = _commit(crepo, "feat: c #3", "c")
    inword = _commit(crepo, "feat: d x#2", "d")
    longer = _commit(crepo, "feat: e #22", "e")
    subj = _commit(crepo, "feat: f #2", "f")
    ado_style = _commit(crepo, "feat: g AB#2", "g")  # the Azure Boards form names no GitHub issue
    foreign = _commit(crepo, "feat: h octo/other#2", "h")  # an issue of another repository is not this one
    closes = _commit(crepo, "feat: i\n\nCloses #2.", "i")
    entity = _commit(crepo, "feat: j &#2;", "j")
    got, note = check_commits(str(crepo), 2)
    assert note is None and {ok, subj, closes}.isdisjoint(got)
    assert set(got) == {bad, other, inword, longer, ado_style, foreign, entity}


def test_merge_commits_ignored(crepo):
    _git(crepo, "switch", "-q", "-c", "side")
    _commit(crepo, "side work #2", "s")
    _git(crepo, "switch", "-q", "feature/gh2-x")
    _commit(crepo, "main work #2", "m")
    _git(crepo, "merge", "-q", "--no-ff", "-m", "Merge branch side", "side")
    assert check_commits(str(crepo), 2) == ([], None)


def test_no_base_is_a_note_not_a_block(tmp_path, git_env):
    _git(tmp_path, "init", "-q", "-b", "main")
    _commit(tmp_path, "no ref here")
    got, note = check_commits(str(tmp_path), 2)
    r = run_gate(LEDGER, SOURCES, {}, None, block=["commits_without_story"], commits=(got, note))
    assert not r["blocking"] and r["commits_note"] == note
    assert "Commit check: " in render_summary(r)
    assert got == [] and note == "no default branch (origin/HEAD) — commits not checked"
    assert check_commits(str(tmp_path / "nope"), 2)[1].startswith("git failed")


def test_commits_blocking_only_when_configured(crepo):
    bad = _commit(crepo, "feat: b")
    commits = check_commits(str(crepo), 2)
    assert commits == ([bad], None)
    adv = run_gate(LEDGER, SOURCES, {}, None, commits=commits)
    assert adv["commits_without_story"] == [bad] and not adv["blocking"]
    blk = run_gate(LEDGER, SOURCES, {}, None, block=["commits_without_story"], commits=commits)
    assert blk["blocking"] and blk["blocked_by"] == ["commits_without_story"]
    assert "Blocking" in render_summary(blk) and f"`{bad}`" in render_summary(blk)
    assert run_gate(LEDGER, SOURCES, {}, None)["commits_without_story"] == []


def test_explicit_base_gives_the_right_range(crepo):
    # PR targets a non-default branch `dev`: only commits after dev count, not those dev already holds
    old = _commit(crepo, "dev-only work, no ref", "o")
    _git(crepo, "branch", "dev")
    _git(crepo, "update-ref", "refs/remotes/origin/dev", "dev")
    bad = _commit(crepo, "feat: after dev", "p")
    assert check_commits(str(crepo), 2, "origin/dev") == ([bad], None)
    assert set(check_commits(str(crepo), 2)[0]) == {old, bad}  # default (origin/HEAD) range is wider


def test_bad_base_is_a_note_never_a_block(crepo):
    _commit(crepo, "feat: b")
    for base in ("--output=x", "-x"):
        got, note = check_commits(str(crepo), 2, base)
        assert got == [] and "not a ref" in note and note.endswith("commits not checked")
    assert not (crepo / "x").exists()
    assert check_commits(str(crepo), 2, "origin/nope") == ([], "base origin/nope not found — commits not checked")


def test_summary_caps_commit_list_at_20():
    shas = [f"{i:07x}" for i in range(25)]
    md = render_summary(run_gate(LEDGER, SOURCES, {}, None, commits=(shas, None)))
    assert "`0000013`" in md and "`0000014`" not in md and "…and 5 more" in md


@pytest.mark.skipif(not (ROOT / "commands").is_dir(), reason="commands/*.md land in T32 (the 13 slash commands): lift there")
@pytest.mark.parametrize("cmd", ["implement", "pr", "review", "gate"])
def test_command_commit_examples_carry_story_ref(cmd):
    text = (ROOT / "commands" / f"{cmd}.md").read_text(encoding="utf-8")
    # implement writes its message (it holds a task title) to a file: the shell line is `git commit -F`, the message carries `#<n>`
    assert re.search(r"git commit -m \"[^\"]*#<n>[^\"]*\"", text) or (cmd == "implement" and "git commit -F .ghp/commit-msg.txt" in text and "#<n>`" in text)


# ---- LEDGER5: gate inputs ignore hidden headings and fail closed on an open fence ----
def test_gate_inputs_ignore_a_fenced_section3_and_marker():
    led = (f"# DoD\n## 1. S\n### Acceptance criteria\nreal AC\n\n{SECTION2}\n```\n## 3. AC\n{HDR}| AC-1 | x | FooTest.testX | | green |\n```\n"
           "## 3. AC\n" + HDR + "| AC-2 | y |  |  | red |\n")
    assert [r["ac"] for r in parse_section3(led)] == ["AC-2"]
    r = run_gate(led, SOURCES, {})
    assert "AC-1" in r["traceability"]["unmapped"]  # the fenced AC-1 row maps nothing


@pytest.mark.parametrize("opener", ["```", "~~~", "<!--"])
def test_gate_unclosed_opener_in_the_ledger_is_a_blocking_ledger_note(opener):
    from ghp.gate import LEDGER_UNCLOSED_NOTE
    led = LEDGER + f"{opener}\n## 5. Definition of Done\n- [x] a\n"
    r = run_gate(led, SOURCES, {}, block=["open_questions"])
    assert r["ledger_unclosed"] == LEDGER_UNCLOSED_NOTE and r["open_questions"] == [] and "open_questions_note" not in r
    assert r["blocking"] and r["blocked_by"] == ["open_questions"]
    assert LEDGER_UNCLOSED_NOTE in render_summary(r) and "Blocking" in render_summary(r)
    advisory = run_gate(led, SOURCES, {})  # advises unless listed in gate.block
    assert not advisory["blocking"] and "Advisory" in render_summary(advisory)
    assert "ledger_unclosed" not in run_gate(LEDGER, SOURCES, {})


# ---- V3-5 / V3-5b: open_decisions, read from <dod_path>/Decisions_<id>.md ----
_D_OPEN = "- [ ] D1: Use cursor paging → _(open)_ · kind: decision · why: the story names no page size"
_D_VAL = "- [x] D2: Cap at 50 rows → validated · by: PO · note: agreed"
_D_REJ = "- [x] D3: Skip audit log → rejected · by: PO · note: not acceptable"
_D_OPEN10 = "- [ ] D10: Retry twice → _(open)_ · kind: assumption · why: unspecified"
V02_KEYS = {"traceability", "assertion_gaps", "assertion_notes", "drift", "missing_evidence", "open_questions",
            "commits_without_story", "blocked_by", "blocking"}  # the literal v0.2 advisory key set


def _dfile(*lines):
    from ghp.decisions import render_file
    return render_file(1001) + "\n".join(lines) + "\n"


def _gd(*lines, **kw):
    return run_gate(LEDGER, SOURCES, {}, decisions_md=_dfile(*lines), **kw)


def test_open_decisions_absent_without_a_file_or_decision_lines():
    assert set(run_gate(LEDGER, SOURCES, {})) == V02_KEYS
    assert set(run_gate(_ledger_for("x"), {}, {})) == V02_KEYS
    assert set(_gd()) == V02_KEYS  # a fresh file: title and format note only
    assert "open_decisions" not in render_summary(_gd())
    # a D line inside the LEDGER is not read any more (V3-2..V3-5 unreleased form)
    old = LEDGER.replace("## 3. AC", _D_OPEN + "\n## 3. AC", 1)
    assert set(run_gate(old, SOURCES, {}, block=["open_decisions"])) == V02_KEYS


def test_open_decisions_lists_ids_and_rejected_counts_as_open():
    r = _gd(_D_OPEN10, _D_VAL, _D_REJ, _D_OPEN)
    assert r["open_decisions"] == ["D1", "D3", "D10"]  # numeric order; validated is resolved
    assert set(r) == V02_KEYS | {"open_decisions"}
    assert _gd(_D_VAL)["open_decisions"] == []  # present, nothing open
    assert _gd("- [ ] D4 not in the grammar")["open_decisions"] == ["?"]  # unparsed D line: fail closed


def test_open_decisions_blocks_only_when_listed():
    assert _gd(_D_OPEN)["blocking"] is False
    r = _gd(_D_OPEN, block=["open_decisions"])
    assert r["blocked_by"] == ["open_decisions"] and r["blocking"] is True
    assert _gd(_D_VAL, block=["open_decisions"])["blocking"] is False
    assert run_gate(LEDGER, SOURCES, {}, block=["open_decisions"])["blocking"] is False  # no file


def test_summary_line():
    open_md = render_summary(_gd(_D_OPEN, _D_REJ))
    assert "- Open decisions in the decisions file: `D1`, `D3`" in open_md.partition("**Advisory:**")[2]
    blocked = render_summary(_gd(_D_OPEN, block=["open_decisions"]))
    assert "`D1`" in blocked.partition("**Blocking")[2].partition("**Advisory")[0]
    assert "Open decisions: none" in render_summary(_gd(_D_VAL))


def test_open_decisions_fail_closed_on_malformed_hidden_or_unreadable():
    r = _gd("- [ ] D4 not in the grammar", block=["open_decisions"])
    assert r["open_decisions"] == ["?"] and r["blocked_by"] == ["open_decisions"] and r["blocking"] is True
    assert _gd("- [ ] D4 not in the grammar")["blocking"] is False  # advisory unless listed
    for hidden in (("```", _D_OPEN), ("<!--", _D_OPEN)):  # an unclosed fence or comment hides the D line
        r = _gd(*hidden, block=["open_decisions"])
        assert r["open_decisions"] == ["?"] and r["blocked_by"] == ["open_decisions"] and r["blocking"] is True
    r = run_gate(LEDGER, SOURCES, {}, decisions_unreadable=True, block=["open_decisions"])
    assert r["open_decisions"] == ["?"] and r["blocking"] is True
    assert run_gate(LEDGER, SOURCES, {}, decisions_unreadable=True)["blocking"] is False


def test_summary_open_decisions_fail_closed_lines():
    for lines in (("- [ ] D4 not in the grammar",), ("```", _D_OPEN)):
        md = render_summary(_gd(*lines, block=["open_decisions"]))
        blocking = md.partition("**Blocking")[2].partition("**Advisory")[0]
        assert "- Open decisions in the decisions file: `?`" in blocking
        assert "AC changed" not in blocking and "no drift" not in blocking and "drift not checked" not in blocking


def test_gate_reads_decisions_file_confined_and_key_absent_without_file(cfg, tmp_path):
    dod = tmp_path / "dod"
    dod.mkdir()
    (dod / "DoD_7.md").write_text(_ledger_for("x"))
    cfg.dod_path = str(dod)
    cfg.gate["block"] = ["open_decisions"]
    run = lambda: _run(cfg, _args(src=str(tmp_path), offline=True))  # noqa: E731
    assert "open_decisions" not in run() and run()["blocking"] is False  # no file
    f = dod / "Decisions_7.md"
    f.write_text(_dfile(_D_OPEN, _D_VAL))
    out = run()
    assert out["open_decisions"] == ["D1"] and out["blocking"] is True and out["blocked_by"] == ["open_decisions"]
    f.write_text(_dfile(_D_VAL))
    out = run()
    assert out["open_decisions"] == [] and out["blocking"] is False
    f.write_bytes(b"\xff\xfe")  # unreadable text: fail closed
    assert run()["open_decisions"] == ["?"]
    f.unlink()
    outside = tmp_path.parent / (tmp_path.name + "-secret.md")
    outside.write_text(_dfile(_D_VAL))
    f.symlink_to(outside)  # a symlink is never followed, even to a harmless file: counted as unreadable
    out = run()
    assert out["open_decisions"] == ["?"] and out["blocking"] is True
    f.unlink()
    f.write_text(_dfile(_D_VAL) + "x" * (1 << 20))  # over MAX_READ
    assert run()["open_decisions"] == ["?"]
    f.write_text(_dfile(_D_VAL))
    assert run()["open_decisions"] == []


def test_open_decisions_use_the_effective_set_and_fail_closed_on_a_bad_supersedes():
    rej, answer = _D_REJ, "- [ ] D5: supersedes D3: answered → _(open)_ · kind: decision · why: w"
    assert _gd(rej)["open_decisions"] == ["D3"]
    assert _gd(rej, answer)["open_decisions"] == ["D5"]  # D3 is answered: only the new decision is open
    assert _gd(rej, answer, block=["open_decisions"])["blocking"] is True  # D5 itself is still open
    assert _gd(rej, "- [x] D5: supersedes D3: answered → validated · by: PO · note: ok")["open_decisions"] == []
    assert _gd(rej, "- [x] D5: supersedes D3: answered → rejected · by: PO · note: no")["open_decisions"] == ["D3", "D5"]
    bad = _gd("- [ ] D1: supersedes D2: x → _(open)_ · kind: decision · why: w", _D_REJ.replace("D3", "D2"), block=["open_decisions"])
    assert "?" in bad["open_decisions"] and bad["blocking"] is True  # a lower-id superseder is invalid: fail closed
    assert "?" in _gd("- [ ] D5: supersedes D9: x → _(open)_ · kind: decision · why: w")["open_decisions"]


# ---- P3-8: the `rule_coverage` check (local, from committed files); the JSON key exists only when rules are enabled ----
from ghp import decisions, dod, rules  # noqa: E402

_AC3 = "- earn points\n- gift cards earn nothing\n- show the balance"
_ROW0 = "| AC-1 | _(what it must do)_ | _(test IDs, …)_ | _(commit / PR)_ | _(red→green)_ |"


def _rules_md(wid, *specs, extra=""):
    """A rules file as `ghp rules` writes it: (n, status) per rule, every label present (no warnings). `extra` is Markdown after the last rule."""
    lines, probs = rules.normalise(desc(*specs, text="Customers earn {n} points.", extra=extra))
    return rules.render_file(make_issue(wid, title="Points", kind="Feature"), rules.parse(lines, wid, probs), "2026-11-03", "0.3.0", "Feature")


F1201 = _rules_md(1201, (1, "approved"), (2, "approved"), (3, "draft"), (4, "retired"))
F1200 = _rules_md(1200, (1, "approved"))
H = {r: v["hash"] for md, w in ((F1201, 1201), (F1200, 1200)) for r, v in rules.parse_file(md, w)["rules"].items()}
ALL = f"BR-1200-1@{H['BR-1200-1']}, BR-1201-1@{H['BR-1201-1']}, BR-1201-2@{H['BR-1201-2']}"


def _files(**md):
    return {int(k[1:]): rules.parse_file(v, int(k[1:])) for k, v in md.items() if v is not None}


def _gl(*rows, frm=(1201,), reviewed="", ac=_AC3):
    """A ledger: three AC lines, the given section 3 rows, the header lines `Rules from:` (None: absent) and `Rules reviewed:`."""
    md = render_dod(_wi(ac), synced="2026-07-14", rules_from=None if frm is None else list(frm), rules_reviewed=reviewed)
    assert _ROW0 in md
    return md.replace(_ROW0, "\n".join(rows))


ROWS = ("| AC-1 | earn points [BR-1201-1] | FooTest.testX | abc | green |",
        "| AC-2 | gift cards earn nothing [BR-1201-2] | FooTest.testX | | green |",
        "| AC-3 | show the balance | FooTest.testX | | green |")


def _cov(ledger, files=None, decisions_md="", phase="pr", sources=SOURCES):
    return rules.coverage(ledger, sources, _files(f1201=F1201, f1200=F1200) if files is None else files,
                          dod.ledger_rules_from(ledger), dod.ledger_rules_reviewed(ledger), decisions.parse_decisions(decisions_md), phase)


def _clean():
    return _gl(*ROWS, reviewed=ALL.replace(f", BR-1200-1@{H['BR-1200-1']}", "").replace(f"BR-1200-1@{H['BR-1200-1']}, ", ""))


def test_coverage_keys_and_a_clean_story():
    c = _cov(_clean())
    assert list(c) == ["in_scope", "covered", "excluded", "uncovered", "unknown", "unconfirmed", "changed", "problems", "missing", "format", "header"]
    assert c["in_scope"] == c["covered"] == ["BR-1201-1", "BR-1201-2"]  # the draft and the retired rule are never in scope
    assert not any(c[k] for k in ("excluded", "uncovered", "unknown", "unconfirmed", "changed", "problems", "missing", "format", "header"))


def test_uncovered_rule_untagged_or_tagged_on_an_ac_with_no_present_test():
    c = _cov(_gl(ROWS[0], "| AC-2 | gift cards earn nothing | FooTest.testX | | |", ROWS[2], reviewed=_clean().split("reviewed:** ")[1].split("\n")[0]))
    assert c["covered"] == ["BR-1201-1"] and c["uncovered"] == ["BR-1201-2"]
    gone = _cov(_gl(ROWS[0], "| AC-2 | gift cards [BR-1201-2] | Gone.test | | |", ROWS[2], reviewed=ALL.split(", BR-1201-2")[0] + f", BR-1201-2@{H['BR-1201-2']}"))
    assert gone["uncovered"] == ["BR-1201-2"]
    empty = _cov(_gl(ROWS[0], "| AC-2 | gift cards [BR-1201-2] |  | | |", ROWS[2]))
    assert "BR-1201-2" in empty["uncovered"]


def test_extra_ac_row_does_not_cover():
    c = _cov(_gl(ROWS[0], "| AC-99 | extra [BR-1201-2] | FooTest.testX | | |", ROWS[2], "| AC-0 | zero [BR-1201-2] | FooTest.testX | | |",
                 "| AC-4 | beyond the three [BR-1201-2] | FooTest.testX | | |", "| AC-01 | not AC-1 [BR-1201-2] | FooTest.testX | | |"))
    assert c["covered"] == ["BR-1201-1"] and c["uncovered"] == ["BR-1201-2"]
    assert not c["unknown"]  # a known id cited on a row that does not count is not an unknown id


def test_row_with_absent_test_does_not_cover_even_if_other_row_mapped():
    # AC-2 is mapped but carries no tag; the tag sits on AC-3 whose test does not exist
    c = _cov(_gl(ROWS[0], "| AC-2 | y | FooTest.testX | | |", "| AC-3 | z [BR-1201-2] | Gone.test | | |"))
    assert c["uncovered"] == ["BR-1201-2"]
    # a duplicate AC-1 row: the tag is on the row whose test is absent; the other AC-1 row is mapped. tags() merges the two rows, so any absent duplicate disqualifies
    dup = _cov(_gl("| AC-1 | a | FooTest.testX | | |", "| AC-1 | a [BR-1201-1] [BR-1201-2] | Gone.test | | |", "| AC-2 | b | FooTest.testX | | |"))
    assert dup["uncovered"] == ["BR-1201-1", "BR-1201-2"]
    # one of several tests present is enough for a row
    ok = _cov(_gl("| AC-1 | a [BR-1201-1] | Gone.test, FooTest.testX | | |", "| AC-2 | b [BR-1201-2] | FooTest.testX | | |"))
    assert ok["covered"] == ["BR-1201-1", "BR-1201-2"]


def test_rule_in_a_fence_or_comment_row_is_not_cited():
    led = _gl(ROWS[0], "```", "| AC-2 | hidden [BR-1201-2] | FooTest.testX | | |", "```", "<!--", "| AC-3 | c [BR-1201-2] | FooTest.testX | | |", "-->")
    assert _cov(led)["uncovered"] == ["BR-1201-2"]


def test_unknown_ids_are_ids_or_a_question_mark_never_the_token_text():
    c = _cov(_gl(ROWS[0], "| AC-2 | b [BR-1201-2] [BR-1201-9] [BR-777-1] [BR-1201-0] xBR-1-1y | FooTest.testX | | |", ROWS[2]))
    assert c["unknown"] == ["BR-777-1", "BR-1201-9", "?", "?"]  # ids by (work item, number), then one `?` per malformed token
    assert "BR-1201-0" not in repr(c) and "xBR" not in repr(c)
    # draft and retired rules exist in the file: citing them is not an unknown id, and not a coverage claim
    d = _cov(_gl(ROWS[0], ROWS[1], "| AC-3 | c [BR-1201-3] [BR-1201-4] | FooTest.testX | | |"))
    assert d["unknown"] == [] and d["in_scope"] == ["BR-1201-1", "BR-1201-2"]


def test_first_pull_is_all_unconfirmed_none_changed():
    for led in (_gl(*ROWS), _gl(*ROWS, reviewed="none")):  # no `Rules reviewed:` line at all, or an empty record: no confirmed hash
        c = _cov(led)
        assert c["unconfirmed"] == ["BR-1201-1", "BR-1201-2"] and c["changed"] == []


def test_a_recorded_different_hash_is_changed_only():
    c = _cov(_gl(*ROWS, reviewed=f"BR-1201-1@{H['BR-1201-1']}, BR-1201-2@deadbeef"))
    assert c["changed"] == ["BR-1201-2"] and c["unconfirmed"] == []


def test_a_rule_with_no_recorded_hash_is_unconfirmed_while_the_rest_is_confirmed():
    c = _cov(_gl(*ROWS, reviewed=f"BR-1201-1@{H['BR-1201-1']}"))
    assert c["unconfirmed"] == ["BR-1201-2"] and c["changed"] == []


def test_a_mix_of_both_keeps_the_two_lists_separate():
    c = _cov(_gl(*ROWS, frm=(1201, 1200), reviewed=f"BR-1201-1@deadbeef"), files=_files(f1201=F1201, f1200=F1200))
    assert c["changed"] == ["BR-1201-1"] and c["unconfirmed"] == ["BR-1200-1", "BR-1201-2"]
    assert not set(c["changed"]) & set(c["unconfirmed"])


def test_an_unrecorded_rule_whose_text_no_longer_matches_its_hash_is_changed_not_unconfirmed():
    # `--confirm` refuses such a rule (the file text is not what was reviewed), so it is a human's: never "free to confirm"
    edited = F1201.replace("- Rule: Customers earn 1 points.", "- Rule: Customers earn nothing.")
    assert edited != F1201
    c = _cov(_gl(*ROWS, reviewed=f"BR-1201-2@{H['BR-1201-2']}"), files=_files(f1201=edited))
    assert c["changed"] == ["BR-1201-1"] and c["unconfirmed"] == []
    down = F1201.replace(f"### BR-1201-2 · approved · {H['BR-1201-2']}", f"### BR-1201-2 · draft · {H['BR-1201-2']}")
    d = _cov(_gl(*ROWS), files=_files(f1201=down))
    assert d["changed"] == ["BR-1201-2"] and d["unconfirmed"] == ["BR-1201-1"]


def test_a_malformed_record_makes_nothing_free_to_confirm():
    # `--confirm` refuses a malformed `Rules reviewed:` line (a human repairs it), so an agent must not be told to run it
    bad = _gl(*ROWS, reviewed=ALL).replace("@" + H["BR-1201-1"], "@XYZ")
    c = _cov(bad)
    assert c["header"] == ["rules-reviewed"] and c["unconfirmed"] == [] and c["changed"] == ["BR-1201-1", "BR-1201-2"]


def test_changed_summary_words_follow_the_cause():
    """P3-13d: a hash mismatch names the re-pull; a malformed `Rules reviewed:` line is not 'changed since confirmed' (ids only in both)."""
    def line(led):
        return next(x for x in render_summary(_rg(led, block=["rule_coverage"])).splitlines() if x.startswith("- Business rules:"))
    mismatch = line(_gl(*ROWS, reviewed=f"BR-1201-1@deadbeef, BR-1201-2@{H['BR-1201-2']}"))
    assert "changed since confirmed" in mismatch and "re-pull with `ghp rules <issue number>` if the file was edited by hand" in mismatch and "`BR-1201-1`" in mismatch
    assert "malformed" not in mismatch and "BR-1201-2" not in mismatch
    bad = line(_gl(*ROWS, reviewed=ALL).replace("@" + H["BR-1201-1"], "@XYZ"))
    assert "changed since confirmed" not in bad and "cannot be judged while the `Rules reviewed:` line is malformed" in bad
    assert "`BR-1201-1`" in bad and "`BR-1201-2`" in bad and "XYZ" not in bad


def test_an_excluded_or_draft_rule_without_a_record_is_neither_list():
    ex = decisions.render_file(1001) + _excl("BR-1201-2") + "\n"
    c = _cov(_gl(*ROWS, reviewed=f"BR-1201-1@{H['BR-1201-1']}"), decisions_md=ex)
    assert c["unconfirmed"] == [] and c["changed"] == []  # not in scope, nothing recorded: nothing to confirm
    assert "BR-1201-3" not in _cov(_gl(*ROWS))["unconfirmed"]


def test_changed_judges_every_recorded_id_that_is_still_approved_in_scope_or_not():
    ex = decisions.render_file(1001) + _excl("BR-1201-2") + "\n"
    led = _gl(*ROWS, reviewed=f"BR-1201-1@{H['BR-1201-1']}, BR-1201-2@deadbeef")
    c = _cov(led, decisions_md=ex)
    assert c["excluded"] == ["BR-1201-2"] and "BR-1201-2" not in c["in_scope"]
    assert c["changed"] == ["BR-1201-2"]  # excluded, but its recorded hash differs from the approved rule in the file
    ok = _cov(_gl(*ROWS, reviewed=f"BR-1201-1@{H['BR-1201-1']}, BR-1201-2@{H['BR-1201-2']}"), decisions_md=ex)
    assert ok["changed"] == []
    # a recorded id whose rule is a draft in the file now is not judged here (a status change is the live drift check's)
    drafted = _cov(_gl(*ROWS, reviewed=f"BR-1201-1@{H['BR-1201-1']}, BR-1201-3@deadbeef"))
    assert "BR-1201-3" not in drafted["changed"]


def _excl(*ids, status="open", posted=True, n=3):
    d = {"id": f"D{n}", "kind": "decision", "text": "out of scope: " + ", ".join(ids), "why": "the Loyalty product owner agreed", "status": status,
         "by": "" if status == "open" else "Loyalty product owner", "note": "", "posted": None, "used": ""}
    if posted:
        d["posted"] = {"comment_id": 7, "hash": decisions.marker_hash(d)}
    return decisions.render_line(d)


def test_exclusions_count_only_when_posted_and_not_rejected_and_phase_fits():
    led = _gl(ROWS[0], ROWS[2], reviewed=f"BR-1201-1@{H['BR-1201-1']}, BR-1201-2@{H['BR-1201-2']}")
    dec = lambda *lines: decisions.render_file(1001) + "\n".join(lines) + "\n"  # noqa: E731
    posted = _cov(led, decisions_md=dec(_excl("BR-1201-2")))
    assert posted["excluded"] == ["BR-1201-2"] and posted["uncovered"] == [] and posted["in_scope"] == ["BR-1201-1"]
    assert _cov(led, decisions_md=dec(_excl("BR-1201-2", posted=False)))["uncovered"] == ["BR-1201-2"]  # unposted: not counted
    assert _cov(led, decisions_md=dec(_excl("BR-1201-2", status="rejected")))["uncovered"] == ["BR-1201-2"]  # rejected: back in scope
    assert _cov(led, decisions_md=dec(_excl("BR-1201-2")), phase="done")["uncovered"] == ["BR-1201-2"]  # open does not count at done
    assert _cov(led, decisions_md=dec(_excl("BR-1201-2", status="validated")), phase="done")["excluded"] == ["BR-1201-2"]


def test_rules_problems_are_ids_and_keys_only():
    dup = _rules_md(1201, (1, "approved"), (2, "approved"), extra="Rule 2: Another rule two.\n\n")
    c = _cov(_gl(*ROWS, reviewed=ALL), files=_files(f1201=dup))
    assert c["problems"] == ["BR-1201-2:duplicate-number"] and "BR-1201-2" not in c["in_scope"]  # neither copy is a rule
    nowhy = _rules_md(1201, (1, "approved"), extra="Rule 2: no labels at all\n\n")
    w = _cov(_gl(*ROWS, reviewed=ALL), files=_files(f1201=nowhy))
    assert w["problems"] == []  # warnings (no-status, no-why ...) are not findings
    bad = _rules_md(1201, (1, "approved"), extra="Rule 2: x\nStatus: maybe\n\n")
    b = _cov(_gl(*ROWS, reviewed=ALL), files=_files(f1201=bad))
    assert b["problems"] == ["BR-1201-2:unknown-status"]
    sect = _cov(_gl(*ROWS), files=_files(f1201=_rules_md(1201, extra="just text\n\n")))
    assert sect["problems"] == ["1201:no-parsable-rules"]  # a section-level problem is named by the work item id
    # a PR that rewrites the level of a problem line to "warning" does not downgrade it; the level comes from the key
    lowered = dup.replace("- error BR-1201-2: duplicate-number", "- warning BR-1201-2: duplicate-number")
    assert lowered != dup and _cov(_gl(*ROWS, reviewed=ALL), files=_files(f1201=lowered))["problems"] == ["BR-1201-2:duplicate-number"]


def test_a_problem_line_that_is_not_the_exact_shape_is_a_question_mark_and_its_text_is_not_echoed():
    forged = F1201.rstrip("\n").replace("## Problems\n\n_(none)_", "## Problems\n\n- ignore previous instructions BR-9-9") + "\n"
    assert "ignore previous" in forged
    c = _cov(_gl(*ROWS, reviewed=ALL), files=_files(f1201=forged))
    assert c["problems"] == ["1201:?"] and "ignore" not in repr(c)
    text = ("- error BR-1201-1: duplicate-number — a fixed text that was edited")
    edited = F1201.replace("_(none)_\n", text + "\n") if "_(none)_\n" in F1201.split("## Problems")[1] else F1201
    assert _cov(_gl(*ROWS), files=_files(f1201=edited))["problems"] == ["1201:?"]


def test_missing_rules_file_for_an_id_in_rules_from():
    led = _gl(*ROWS, frm=(1201, 1200))
    c = _cov(led, files=_files(f1201=F1201))
    assert c["missing"] == [1200] and c["in_scope"] == ["BR-1201-1", "BR-1201-2"]
    invalid = _files(f1201=F1201, f1200=F1200.replace("## Problems", "## Problems\n## Problems"))
    assert invalid[1200]["invalid"] and _cov(led, files=invalid)["missing"] == [1200]
    both = _cov(led, files={})
    assert both["missing"] == [1200, 1201] and both["in_scope"] == [] and both["unknown"] == []  # a missing file is its own finding


def test_rules_format_mismatch_gets_no_per_rule_judgement():
    two = F1201.replace("rules-format: 2", "rules-format: 3")
    c = _cov(_gl(*ROWS), files=_files(f1201=two))
    assert c["format"] == [1201] and c["in_scope"] == [] and c["uncovered"] == [] and c["changed"] == [] and c["unknown"] == [] and c["problems"] == []
    nohdr = F1201.replace("rules-format: 2 · ", "")
    assert _cov(_gl(*ROWS), files=_files(f1201=nohdr))["format"] == [1201]  # a malformed header line (format None) is a mismatch too


def test_header_findings():
    assert _cov(_gl(*ROWS, frm=None))["header"] == ["rules-from"]  # absent: fix with `ghp dod <id> --refresh`
    assert _cov(_gl(*ROWS, frm=None))["in_scope"] == []
    bad = _gl(*ROWS).replace("- **Rules from:** 1201", "- **Rules from:** 1201,1200")
    assert dod.ledger_rules_from(bad)["valid"] is False and _cov(bad)["header"] == ["rules-from"]
    assert _cov(_gl(*ROWS, frm=()))["header"] == []  # `none` is valid: the story has no ancestors with rules
    badrev = _gl(*ROWS, reviewed=ALL).replace("@" + H["BR-1201-1"], "@XYZ")
    assert _cov(badrev)["header"] == ["rules-reviewed"]
    both = _gl(*ROWS, frm=None, reviewed=ALL).replace("- **Rules reviewed:** ", "- **Rules reviewed:**  ")
    assert _cov(both)["header"] == ["rules-from", "rules-reviewed"]


def test_hostile_files_and_ledger_fail_closed_not_open():
    # body edited under an unchanged heading: the rule stays in scope and is `changed`
    edited = F1201.replace("- Rule: Customers earn 1 points.", "- Rule: Customers earn nothing.")
    assert edited != F1201
    c = _cov(_gl(*ROWS, reviewed=ALL), files=_files(f1201=edited))
    assert c["changed"] == ["BR-1201-1"] and "BR-1201-1" in c["in_scope"]
    # status edited to draft under the old hash: still in scope, still reported
    down = F1201.replace(f"### BR-1201-2 · approved · {H['BR-1201-2']}", f"### BR-1201-2 · draft · {H['BR-1201-2']}")
    d = _cov(_gl(*ROWS, reviewed=ALL), files=_files(f1201=down))
    assert d["changed"] == ["BR-1201-2"] and "BR-1201-2" in d["in_scope"]


# ---- run_gate: the JSON key, the finding list, the summary line ----
def _rg(ledger, files=None, block=(), decisions_md=None, **kw):
    return run_gate(ledger, SOURCES, {}, None, block=block, decisions_md=decisions_md,
                    rules_ctx={"files": _files(f1201=F1201, f1200=F1200) if files is None else files}, **kw)


def test_rules_key_and_findings_only_with_a_rules_ctx():
    led = _clean()
    off = run_gate(led, SOURCES, {}, None)
    assert set(off) == V02_KEYS and "rules" not in off
    on = _rg(led)
    assert set(on) == V02_KEYS | {"rules"} and {k: on["rules"][k] for k in _cov(led)} == _cov(led) and on["blocking"] is False  # advisory: reported, not blocking
    assert on["rules"]["covered"] == ["BR-1201-1", "BR-1201-2"]


def test_disabled_gate_json_key_set_equals_baseline():
    led = _gl(*ROWS, frm=None)  # a header problem exists, but rules are not enabled: no key, no line, no question
    asked = []

    class _Block:
        def __contains__(self, key):
            asked.append(key)
            return False
    off = run_gate(led, SOURCES, {}, None, block=_Block())
    assert set(off) == V02_KEYS and "rule_coverage" not in asked and "rule_drift" not in asked and "rules" not in off
    assert "Business rules" not in render_summary(off) and "rule" not in render_summary(off).lower().replace("traceability", "")
    run_gate(led, SOURCES, {}, None, block=_Block(), rules_ctx={"files": {}})
    assert "rule_coverage" in asked and "rule_drift" in asked


def test_findings_list_blocks_only_when_named_and_marks_each_class_with_a_question_mark():
    uncovered = _gl(ROWS[0], ROWS[2], reviewed=ALL)
    assert _rg(uncovered)["blocking"] is False
    r = _rg(uncovered, block=["rule_coverage"])
    assert r["blocked_by"] == ["rule_coverage"] and r["blocking"] is True
    for led, files in ((_gl(*ROWS, frm=None), None), (_gl(*ROWS), None), (_gl(*ROWS, frm=(1201, 1200), reviewed=ALL), _files(f1201=F1201)),
                       (_gl(*ROWS, reviewed=ALL), _files(f1201=F1201.replace("rules-format: 2", "rules-format: 3")))):
        assert _rg(led, files, block=["rule_coverage"])["blocking"] is True
    assert _rg(_clean(), block=["rule_coverage"])["blocking"] is False


def test_blocked_rule_coverage_prints_its_own_line():
    r = _rg(_gl(ROWS[0], ROWS[2], reviewed=ALL), block=["rule_coverage"])
    md = render_summary(r)
    blocking = md.partition("**Blocking")[2].partition("**Advisory")[0]
    assert "`BR-1201-2`" in blocking and "AC changed" not in blocking and "no drift" not in blocking and "drift not checked" not in blocking
    adv = render_summary(_rg(_gl(ROWS[0], ROWS[2], reviewed=ALL)))
    assert "Blocking" not in adv and "`BR-1201-2`" in adv.partition("**Advisory:**")[2]
    assert "Business rules" not in render_summary(_rg(_clean()))  # nothing to report: no line


def test_gate_json_summary_and_findings_split_unconfirmed_from_changed():
    led = _gl(*ROWS, frm=(1201, 1200), reviewed=f"BR-1201-1@deadbeef")
    files = _files(f1201=F1201, f1200=F1200)
    r = _rg(led, files, block=["rule_coverage"])
    assert r["rules"]["changed"] == ["BR-1201-1"] and r["rules"]["unconfirmed"] == ["BR-1200-1", "BR-1201-2"]
    assert r["blocked_by"] == ["rule_coverage"]
    line = next(x for x in render_summary(r).splitlines() if x.startswith("- Business rules:"))
    assert "not confirmed yet" in line and "changed since confirmed" in line
    assert "`BR-1200-1`" in line.split("not confirmed yet")[1].split("changed since confirmed")[0] and "`BR-1201-2`" in line.split("not confirmed yet")[1].split("changed since confirmed")[0]
    assert "`BR-1201-1`" in line.split("changed since confirmed")[1] and "BR-1201-1" not in line.split("changed since confirmed")[0]
    assert "ghp rules <issue number> --confirm" in line and "own terminal" in line  # each class names its own fix
    # only unconfirmed: blocks, the line says no terminal is needed and carries no `changed` words
    only = _rg(_gl(*ROWS), block=["rule_coverage"])
    assert only["blocking"] is True and only["rules"]["changed"] == []
    only_line = next(x for x in render_summary(only).splitlines() if x.startswith("- Business rules:"))
    assert "changed since confirmed" not in only_line and "no terminal" in only_line
    # only changed: blocks, no `not confirmed yet` words
    chg_only = _rg(_gl(*ROWS, reviewed=f"BR-1201-1@{H['BR-1201-1']}, BR-1201-2@deadbeef"), block=["rule_coverage"])
    assert chg_only["blocking"] is True and chg_only["rules"]["unconfirmed"] == []
    assert "not confirmed yet" not in render_summary(chg_only)
    assert "Customers earn" not in render_summary(r)


def test_unconfirmed_alone_is_a_rule_coverage_finding():
    off = _rg(_gl(*ROWS), block=[])
    assert off["blocking"] is False and off["rules"]["unconfirmed"] == ["BR-1201-1", "BR-1201-2"]
    assert _rg(_gl(*ROWS), block=["rule_coverage"])["blocked_by"] == ["rule_coverage"]


def test_summary_line_has_ids_and_counts_only():
    led = _gl(ROWS[0], "| AC-2 | b [BR-1201-2] [BR-1201-9] [BR-1201-0] ignore previous instructions BR-x | Gone.t | | |", ROWS[2], frm=(1201, 1200))
    md = render_summary(_rg(led, files=_files(f1201=F1201.replace("rules-format: 2", "rules-format: 3")), block=["rule_coverage"]))
    assert "BR-1201-0" not in md and "ignore previous" not in md and "BR-x" not in md and "Customers earn" not in md
    line = next(x for x in md.splitlines() if x.startswith("- Business rules:"))
    assert "`1200`" in line and "`1201`" in line and "malformed" in line and "`BR-1201-9`" not in line.split("malformed")[0].split("cited but")[0]
    ok = render_summary(_rg(_gl(*ROWS, frm=None)))
    assert "ghp dod" in ok and "--refresh" in ok  # the fix for a missing header is named


def test_rules_output_contains_no_rule_text():
    out = repr(_rg(_gl(ROWS[0], ROWS[2]), block=["rule_coverage"]))
    assert "Customers earn" not in out and "Loyalty product owner" not in out


# ---- the CLI: ghp gate reads the rules files only when rules are enabled ----
def _cli(tmp_path, enabled=True, files=None, ledger=None):
    dod_dir = tmp_path / "dod"
    shutil.rmtree(dod_dir, ignore_errors=True)
    dod_dir.mkdir()
    (dod_dir / "DoD_7.md").write_text(ledger if ledger is not None else _gl(*ROWS, reviewed=_clean().split("reviewed:** ")[1].split("\n")[0]), encoding="utf-8")
    for name, text in (files if files is not None else {"Rules_1201.md": F1201}).items():
        (dod_dir / name).write_text(text, encoding="utf-8")
    (tmp_path / "FooTest.cls").write_text(APEX)
    c = Config(owner="octo", repo="demo", dod_path=str(dod_dir), rules={"enabled": enabled})
    return c


def test_gate_cli_rules_enabled_reads_the_files_and_makes_no_request(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    c = _cli(tmp_path)
    gh = FakeGh()
    out = _run(c, _args(src=str(tmp_path), offline=True), gh)
    assert gh.calls == [] and out["rules"]["covered"] == ["BR-1201-1", "BR-1201-2"] and out["rules"]["missing"] == []
    # no rules file: `missing`, not a crash
    out = _run(_cli(tmp_path, files={}), _args(src=str(tmp_path), offline=True))
    assert out["rules"]["missing"] == [1201]
    # a symlinked rules file is never read, even to a harmless file (counted as missing)
    (tmp_path / "dod" / "Rules_1201.md").symlink_to(tmp_path / "elsewhere.md")
    (tmp_path / "elsewhere.md").write_text(F1201)
    out = _run(_cli(tmp_path, files={}), _args(src=str(tmp_path), offline=True))
    assert out["rules"]["missing"] == [1201]


def test_gate_cli_rules_disabled_or_absent_reads_nothing_and_adds_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for enabled in (False, None):
        c = _cli(tmp_path, enabled=bool(enabled))
        if enabled is None:
            c.rules = Config(owner="o", repo="p").rules  # the default: rules absent
        out = _run(c, _args(src=str(tmp_path), offline=True))
        assert "rules" not in out and set(out) - {"commits_note"} == V02_KEYS  # commits_note: the temp dir is no git repository


def test_gate_cli_rules_ledger_without_header_is_a_header_finding(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    c = _cli(tmp_path, ledger=_gl(*ROWS, frm=None))
    c.gate["block"] = ["rule_coverage"]
    out = _run(c, _args(src=str(tmp_path), offline=True))
    assert out["rules"]["header"] == ["rules-from"] and out["blocking"] is True


def test_gate_cli_ids_in_the_ledger_never_build_a_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    led = _gl(*ROWS).replace("- **Rules from:** 1201", "- **Rules from:** ../../etc/passwd")
    c = _cli(tmp_path, ledger=led)
    out = _run(c, _args(src=str(tmp_path), offline=True))
    assert out["rules"]["header"] == ["rules-from"] and out["rules"]["missing"] == []


# ---- P3-10: a rejected exclusion is not an open decision when rules are enabled (the coverage check carries out the PO's answer) ----
def test_rejected_exclusion_exempt_in_strict_and_gate_but_rule_uncovered():
    led = _gl(ROWS[0], ROWS[2], reviewed=f"BR-1201-1@{H['BR-1201-1']}, BR-1201-2@{H['BR-1201-2']}")
    dec = _dfile(_excl("BR-1201-2", status="rejected"))
    on = _rg(led, decisions_md=dec, block=["open_decisions", "rule_coverage"])
    assert on["open_decisions"] == [] and on["rules"]["uncovered"] == ["BR-1201-2"]  # exempt from open_decisions, enforced by coverage
    assert on["blocked_by"] == ["rule_coverage"] and "D3" not in render_summary(on)
    off = run_gate(led, SOURCES, {}, None, block=["open_decisions"], decisions_md=dec)
    assert off["open_decisions"] == ["D3"] and off["blocked_by"] == ["open_decisions"]  # rules off: the baseline
    only = _rg(_gl(*ROWS, reviewed=ALL.replace(f", BR-1200-1@{H['BR-1200-1']}", "").replace(f"BR-1200-1@{H['BR-1200-1']}, ", "")),
               decisions_md=dec, block=["open_decisions", "rule_coverage"])
    assert only["blocking"] is False and only["open_decisions"] == []  # the rule is tagged: the rejected line alone blocks nothing


def test_other_rejected_decision_still_blocks_the_gate():
    led = _clean()
    bad = [_excl("BR-1201-2", status="rejected", n=1), _excl("BR-1201-2", status="rejected", n=6, posted=False), _D_REJ, "- [x] D4: Out of scope: BR-1201-2 → rejected · by: PO · note: no",
           decisions.render_line({"id": "D5", "kind": "override", "text": "out of scope: BR-1201-2", "why": "x", "status": "rejected", "by": "PO",
                                  "note": "", "posted": None, "used": ""})]
    on = _rg(led, decisions_md=_dfile(*bad))
    assert on["open_decisions"] == ["D3", "D4", "D5", "D6"]  # D1, the posted well-formed rejected exclusion, is the only exempt line (D6 was never posted)
    sup = _rg(led, decisions_md=_dfile(_excl("BR-1201-2", status="rejected", n=1), _D_OPEN10))
    assert sup["open_decisions"] == ["D10"]  # an open decision is still open


# ================================ T22: the verb (`ghp gate <issue>`), read-only, fail closed ================================
import json  # noqa: E402
import subprocess as _sp  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402

import ghp.__main__ as cli  # noqa: E402


@pytest.fixture
def proj(tmp_path, monkeypatch):
    """A cwd with a ghp.config.json for octo/demo, a ledger for issue 7 (one AC, mapped to a real test) and the test file."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo"}))
    (tmp_path / "dod").mkdir()
    led = _ledger_for("does X").replace(_ROW0, "| AC-1 | does X | FooTest.testX | abc | green |")
    (tmp_path / "dod" / "DoD_7.md").write_text(led)
    (tmp_path / "FooTest.cls").write_text(APEX)
    return tmp_path


def _gate_cfg(root, **extra):
    (root / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", **extra}))


def test_gate_verb_prints_the_result_and_exits_0(proj, capsys):
    rc = cli.main(["gate", "7", "--offline"], gh=FakeGh())
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["traceability"]["mapped"] == ["AC-1"] and out["blocking"] is False and out["drift"]["evaluated"] is False


def test_gate_verb_exits_1_on_a_blocking_finding(proj, capsys):
    _gate_cfg(proj, gate={"block": ["commits_without_story"]})
    (proj / "dod" / "DoD_7.md").write_text(_ledger_for("does X"))  # the template row: AC-1 has no test
    _gate_cfg(proj, gate={"block": ["ac_traceability"]})
    assert cli.main(["gate", "#7", "--offline"], gh=FakeGh()) == 1
    assert json.loads(capsys.readouterr().out)["blocked_by"] == ["ac_traceability"]


def test_gate_verb_summary_out_is_written_on_success_and_on_failure(proj, capsys):
    summary = proj / "out" / "sum.md"
    assert cli.main(["gate", "7", "--offline", "--summary-out", str(summary)], gh=FakeGh()) == 0
    assert summary.read_text().startswith("### ghp gate\n") and "No findings" in summary.read_text()
    (proj / "dod" / "DoD_7.md").write_text((proj / "dod" / "DoD_7.md").read_text() + "## 2. Brainstorm & plan\nsecond heading\n")
    assert cli.main(["gate", "7", "--offline", "--summary-out", str(summary)], gh=FakeGh()) == 3  # not 2: ado's workflow reads 2 as advisory
    assert "FAILED: the ledger cannot be read safely" in summary.read_text()
    capsys.readouterr()


def test_an_ambiguous_ledger_fails_the_gate_it_never_passes_as_no_section(proj, capsys):
    """T11c: more than one visible `## 2.` heading raises LedgerError; section 3 would otherwise read as absent (nothing to block on)."""
    _gate_cfg(proj, gate={"block": ["ac_traceability", "assertion", "open_questions", "open_decisions"]})
    led = (proj / "dod" / "DoD_7.md").read_text()
    (proj / "dod" / "DoD_7.md").write_text(led + "\n## 2. Brainstorm & plan\nan old heading copied by hand\n")
    rc = cli.main(["gate", "7", "--offline"], gh=FakeGh())
    cap = capsys.readouterr()
    assert rc == 3 and cap.out == "" and "E_LEDGER_AMBIGUOUS_SECTION2" in cap.err and "Traceback" not in cap.err  # 3 = fail closed in every mode (2 is advisory in CI)
    # the same through the pure entry points: they raise, they do not return "no rows"
    for fn in (parse_section3, lambda md: run_gate(md, {}, {})):
        with pytest.raises(LedgerError):
            fn(led + "\n## 2. Brainstorm & plan\nx\n")


def test_an_invalid_config_is_exit_3_and_a_failed_summary(proj, capsys):
    (proj / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", "gate": {"block": ["bogus"]}}))
    summary = proj / "s.md"
    assert cli.main(["gate", "7", "--offline", "--summary-out", str(summary)], gh=FakeGh()) == 3
    assert "ghp gate FAILED: ghp.config.json is invalid" in summary.read_text()
    assert capsys.readouterr().out == ""
    (proj / "ghp.config.json").unlink()
    assert cli.main(["gate", "7", "--offline"], gh=FakeGh()) == 2  # no config at all: a refusal, not a pass
    capsys.readouterr()


def test_the_ref_is_this_repository_only_and_errors_are_escaped(proj, capsys):
    assert cli.main(["gate", "other/repo#7", "--offline"], gh=FakeGh()) == 2
    assert "E_FOREIGN_REPO" in capsys.readouterr().err
    assert cli.main(["gate", "\x1b[31mred\x1b]0;title\x07", "--offline"], gh=FakeGh()) == 2
    err = capsys.readouterr().err
    assert "\x1b" not in err and "\x07" not in err and err.count("\n") == 1 and err.startswith("error: ")
    assert cli.main(["gate", "0", "--offline"], gh=FakeGh()) == 2
    capsys.readouterr()


def test_missing_and_unreadable_ledgers_are_advisory_notes(proj, capsys):
    (proj / "dod" / "DoD_7.md").unlink()
    assert cli.main(["gate", "7", "--offline"], gh=FakeGh()) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ledger"] == "missing" and out["blocking"] is False and "ghp dod 7" in out["note"]
    assert "Advisory" in render_summary(out)


def test_an_unwritable_summary_keeps_the_gate_exit_code(proj, capsys):
    (proj / "file").write_text("x")
    assert cli.main(["gate", "7", "--offline", "--summary-out", str(proj / "file" / "s.md")], gh=FakeGh()) == 0
    assert "could not write the --summary-out file" in capsys.readouterr().err


def _answering(raws):
    """A runner for a real `Gh` (so the arming gate and the argv shape are the product's): answers GET of a known issue path, 404 otherwise."""
    calls = []

    def runner(argv, stdin, timeout):
        calls.append({"argv": list(argv), "stdin": stdin})
        path = argv[argv.index("-X") + 2]
        raw = raws.get(path)
        return http(200, raw) if raw is not None else http(404, {"message": "Not Found"})
    return runner, calls


def test_gate_sends_no_write(proj, monkeypatch, capsys):
    """T22 AC3: the gate is read-only. A real `Gh`, never armed, behind a runner that records every spawn: with rules on (so the live rule-drift
    walk runs too) every spawn is `gh api -X GET <path>` with no body, nothing is armed, and there is no spawn of anything else."""
    _gate_cfg(proj, rules={"enabled": True}, mode="strict")
    story = raw_issue(7, None, "## Acceptance criteria\ndoes X\n", parent=1201)
    story["body"] = "## Acceptance criteria\ndoes X\n"
    raws = {f"repos/octo/demo/issues/{r['number']}": r for r in (story, raw_issue(1201, "Feature", desc((1, "approved")), parent=1200), raw_issue(1200, "Epic", desc((1, "approved"))))}
    runner, calls = _answering(raws)
    gh = Gh(runner=runner, sleep=lambda s: None)
    summary = proj / "s.md"
    rc = cli.main(["gate", "7", "--summary-out", str(summary)], gh=gh)
    json.loads(capsys.readouterr().out)
    assert rc in (0, 1)  # strict + rules: findings may block; what matters is what was sent
    assert calls, "the AC-drift read and the walk must have run"
    assert all(c["argv"][:4] == ["gh", "api", "-X", "GET"] and c["stdin"] is None and "--input" not in c["argv"] for c in calls)
    assert {c["argv"][4] for c in calls} == set(raws) and gh.armed is False
    # the same on the FakeGh double (a double that raises WriteNotArmed on any write): the verb runs to the end
    fake = serve(story, raw_issue(1201, "Feature", desc((1, "approved")), parent=1200), raw_issue(1200, "Epic", desc((1, "approved"))))
    cli.main(["gate", "7"], gh=fake)
    capsys.readouterr()
    assert fake.calls and {c[1] for c in fake.calls} == {"GET"} and not fake.armed


def test_gate_makes_no_request_with_offline_or_without_a_ledger(proj, capsys):
    runner, calls = _answering({})
    gh = Gh(runner=runner, sleep=lambda s: None)
    assert cli.main(["gate", "7", "--offline"], gh=gh) == 0
    (proj / "dod" / "DoD_7.md").unlink()
    assert cli.main(["gate", "7"], gh=gh) == 0  # no ledger: nothing to compare, nothing to fetch
    capsys.readouterr()
    assert calls == []


def test_a_non_issue_or_failing_read_never_blocks_but_says_so(proj, capsys):
    _gate_cfg(proj, gate={"block": ["ac_drift"]})
    for runner_raws in ({}, {"repos/octo/demo/issues/7": dict(raw_issue(7), pull_request={"url": "x"})}):
        runner, _ = _answering(runner_raws)
        rc = cli.main(["gate", "7"], gh=Gh(runner=runner, sleep=lambda s: None))
        out = json.loads(capsys.readouterr().out)
        assert rc == 0 and out["drift"]["evaluated"] is False and out["drift"]["note"].startswith("GitHub not reachable")


def test_base_with_a_strict_target_is_judged_by_the_targets_strict_config(proj, git_env, capsys):
    """T23: the E_STRICT_UNBUILT refusal is gone. `--base` with a strict target config judges the whole run with it (the PR's weaker copy does not count),
    and says so; the target-branch trust (CI) tests are in test_ci_trust."""
    _gate_cfg(proj, mode="strict")
    _git(proj, "init", "-q", "-b", "main")
    _commit(proj, "init #7", "f")
    _git(proj, "update-ref", "refs/remotes/origin/main", "HEAD")
    _gate_cfg(proj)  # the PR's own copy weakens the mode: the target's strict config must win
    summary = proj / "s.md"
    rc = cli.main(["gate", "7", "--offline", "--base", "origin/main", "--summary-out", str(summary)], gh=FakeGh())
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["config"] == "target branch (strict)" and "E_STRICT_UNBUILT" not in summary.read_text()


def test_base_with_an_advisory_target_leaves_the_pr_config_in_charge(proj, git_env, capsys):
    _git(proj, "init", "-q", "-b", "main")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-q", "-m", "init")
    _git(proj, "update-ref", "refs/remotes/origin/main", "HEAD")
    bad = _commit(proj, "no reference", "g")
    _gate_cfg(proj, gate={"block": ["commits_without_story"]})
    rc = cli.main(["gate", "7", "--offline", "--base", "origin/main"], gh=FakeGh())
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["commits_without_story"] == [bad] and out["blocked_by"] == ["commits_without_story"] and "config" not in out
    # an unresolvable base with an advisory PR config is the v0.2 path (a note, no failure); with a strict one it fails closed
    assert cli.main(["gate", "7", "--offline", "--base", "origin/nope"], gh=FakeGh()) == 0
    assert "not found" in json.loads(capsys.readouterr().out)["commits_note"]
    _gate_cfg(proj, mode="strict")
    assert cli.main(["gate", "7", "--offline", "--base", "origin/nope"], gh=FakeGh()) == 3
    capsys.readouterr()


# ---- the module graph: gate, decisions, questions and prs import one another; no import order may fail ----
@pytest.mark.parametrize("first", ["gate", "decisions", "questions", "prs", "rules", "dod", "config", "calibrate", "stack", "__main__"])
def test_every_import_order_works(first):
    env = {**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")}
    r = _sp.run([sys.executable, "-c", f"import ghp.{first}; import ghp.gate, ghp.calibrate, ghp.decisions, ghp.questions, ghp.prs"],
                capture_output=True, text=True, env=env, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr[-400:]


# ---- hostile, PR-controlled text of 300,000 characters: linear, capped, escaped ----
BIG = 300_000


def _timed(fn, limit=5.0):
    t0 = time.monotonic()
    out = fn()
    took = time.monotonic() - t0
    assert took < limit, f"{took:.1f}s"
    return out, took


def test_hostile_ledger_and_commit_text_stay_linear():
    rows = ("| AC-1 | " + "a" * BIG + " | FooTest.testX | " + "e" * BIG + " | green |\n"
            "| AC-2 | " + " |" * BIG + "\n"
            "| AC-3 | " + "AC-" * (BIG // 3) + " | " + ", ".join(f"T{i}.t" for i in range(BIG // 12)) + " | | red |\n")
    led = f"{SECTION2}\n- [ ] Q1: " + "?" * BIG + f" → _(open)_\n## 3. AC → tests (traceability)\n{HDR}{rows}## 4. Gate log\n"
    r, took = _timed(lambda: run_gate(led, SOURCES, {"BTest.cls": "x" * BIG}, block=["ac_traceability"], commits=(["abc1234"] * 50, None)))
    assert r["blocking"] is True
    md, took2 = _timed(lambda: render_summary(r))
    assert all(len(line) < 1500 for line in md.splitlines())


def test_hostile_commit_message_of_300k_characters(crepo):
    msg = ("#" * BIG) + "\n\n" + " #" * (BIG // 2) + "\n" + "&#" * (BIG // 2) + "\n" + "9" * BIG
    (crepo / "big").write_text("x")
    (crepo / "msg.txt").write_text("feat: " + msg)  # too long for argv: `git commit -F`
    _git(crepo, "add", "-A")
    _git(crepo, "commit", "-q", "-F", "msg.txt")
    bad = _git(crepo, "rev-parse", "--short", "HEAD")
    got, took = _timed(lambda: check_commits(str(crepo), 2))
    assert got == ([bad], None)


def test_hostile_changed_and_test_text_is_scanned_once():
    tests = {"**/*.py": r"assert\b"}
    text = "x" * BIG + "\n" + "assert" * 10
    gaps, took = _timed(lambda: scan_assertions({"a.py": "x " * BIG, "b.py": text}, tests))
    assert gaps == [{"file": "a.py", "issue": "no assertions"}]
    deep = "def test_a():\n" + "    if x:\n" * 400 + "        pass\n"
    r, _ = _timed(lambda: check_test_assertions_for(deep))
    assert r  # parsed or noted, never a crash


def check_test_assertions_for(src):
    return gate.check_test_assertions([{"ac": "AC-1", "tests": ["test_a"], "evidence": "", "status": ""}], {"tests/test_x.py": src})


def test_summary_escapes_hostile_names_everywhere():
    evil = "\x1b[31m`x`\n## pwn\u202e"
    r = run_gate("", {}, {}) | {"commits_without_story": [evil], "open_questions": [evil], "assertion_gaps": [{"test": evil, "issue": "no assertions"}],
                                 "assertion_notes": [evil], "commits_note": evil, "skipped_note": evil, "traceability": {"evaluated": True, "mapped": [], "unmapped": [evil], "note": ""}}
    md = render_summary(r)
    assert "\n## pwn" not in md and md.count("\n") < 40
    assert all(line.count("`") % 2 == 0 for line in md.splitlines())


# ================================ fix round 1: real deadlines, linear glob matcher, capped test ids ================================
import random  # noqa: E402


def _deadline(code, seconds):
    """Run `code` in a fresh interpreter and fail (never hang) when it does not finish in `seconds`: the regex engine and a long Python loop cannot be
    interrupted from inside the test process. Returns the stdout."""
    prog = f"import sys\nsys.path.insert(0, {str(ROOT / 'src')!r})\n" + code
    try:
        r = _sp.run([sys.executable, "-I", "-W", "ignore", "-c", prog], capture_output=True, text=True, timeout=seconds)
    except _sp.TimeoutExpired:
        pytest.fail(f"did not finish within {seconds} s (a quadratic or exponential path is back)")
    assert r.returncode == 0, r.stderr[-600:]
    return r.stdout


def test_glob_matcher_is_linear_for_hostile_globs():
    out = _deadline("""
from ghp import gate
deep = "/".join(["d"] * 40) + "/x.py"
assert gate._glob_match(deep, "**/d/" * 10 + "z.py") is False        # 10 non-adjacent `**/`: 67 s per file with the regex translation
assert gate._glob_match(deep, "**/d/" * 7 + "x.py") is True
assert gate._glob_match("a" * 255, "*a" * 5 + "*b") is False           # `*a*a*a*a*a*b` on a 255-character name: 43 s with the regex
assert gate._glob_match("a" * 254 + "b", "*a" * 5 + "*b") is True
assert gate._glob_match("a" * 255, "*a" * 8 + "*b") is False
assert gate._glob_match("/".join(["d"] * 1500) + "/x.py", "**/d/**/d/**/d/**/x.py") is True
assert gate._glob_match(deep, "**/" * 60 + "z.py") is False
print("ok")
""", 20)
    assert out.strip() == "ok"


def _reference_match(path, pattern):
    """ado's translation (glob -> regex), kept here as the oracle for the differential test; the patterns used are well-formed."""
    path, i, rx = path.replace("\\", "/"), 0, ""
    while i < len(pattern):
        if pattern.startswith("**/", i):
            rx, i = rx + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            rx, i = rx + ".*", i + 2
        elif pattern[i] == "*":
            rx, i = rx + "[^/]*", i + 1
        elif pattern[i] == "?":
            rx, i = rx + "[^/]", i + 1
        elif pattern[i] == "[" and (j := pattern.find("]", i + 2)) != -1:
            cls = pattern[i + 1:j]
            rx, i = rx + "[" + ("^" + cls[1:] if cls[0] == "!" else cls) + "]", j + 1
        else:
            rx, i = rx + re.escape(pattern[i]), i + 1
    return re.fullmatch("(?:.*/)?" + rx, path) is not None


def test_glob_matcher_equals_the_regex_translation_on_random_small_cases():
    rng = random.Random(22)
    atoms = ["*", "**", "**/", "?", "a", "b", "/", "[ab]", "[!a]", "[a-c]", ".", "_", "x/", "[/]", "\\", "(", "+"]
    chars = "ab/_.\n\\xc(+"
    for _ in range(20000):
        pat = "".join(rng.choice(atoms) for _ in range(rng.randint(1, 7)))
        path = "".join(rng.choice(chars) for _ in range(rng.randint(0, 10)))
        assert gate._glob_match(path, pat) is _reference_match(path, pat), (pat, path)


def test_a_class_is_judged_alone_and_a_malformed_one_is_refused():
    """ado joined the classes into one regex, so `[!]` followed by a later `]` was silently read as another class; here each class stands alone and a bad
    one is a ValueError (the safer side). The cases of ado's own tests are unchanged."""
    for bad in ("**/[!]", "[a", "x[", "[!]_\\[!a]"):
        with pytest.raises(ValueError, match="invalid glob in gate.tests"):
            gate._glob_match("a", bad)
        assert gate.valid_glob(bad) is False
    assert gate._glob_match("a.py", "[!b].py") and not gate._glob_match("b.py", "[!b].py") and gate._glob_match("]", "[]]")


def test_valid_glob_caps_the_wildcards_with_a_named_config_error(tmp_path):
    ok = "**/" + "*a" * 7  # 8 wildcards
    assert gate.valid_glob(ok) and not gate.valid_glob("**/" + "*a" * 8) and not gate.valid_glob("?" * 9) and gate.valid_glob("?" * 8)
    for n, glob in enumerate(("**/" + "*a" * 8, "[a][b][c][d][e][f][g][h][i]")):
        (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "o", "repo": "r", "gate": {"tests": {glob: "x"}}}))
        with pytest.raises(ConfigInvalid, match=r"entry #1: the glob must be 1-200 characters with balanced '\[\.\.\.\]' classes and at most 8 wildcards") as e:
            Config.load(tmp_path)
        assert glob not in str(e.value)


def test_the_literal_tail_reject_never_changes_an_answer():
    for pat, path, want in (("**/", "", True), ("**/", "a/", True), ("a**/b", "ab", True), ("a**/b", "a/x/b", True), ("*.py", "x.pyc", False),
                            ("x/**", "x/", True), ("**/x", "x", True), ("a?", "a/", False), ("[ab]c", "bc", True)):
        assert gate._glob_match(path, pat) is want is _reference_match(path, pat), (pat, path)


# ---- I-1: the per-test assertion check is parsed/indexed once per file, and the ids read from a ledger are capped ----
def test_many_defs_and_many_ids_finish_within_a_deadline():
    out = _deadline("""
import time
from ghp import gate
src = "".join(f"def test_{i}():\\n    assert {i}\\n" for i in range(20000))     # one 450 kB file of 20,000 defs
ids = [f"tests/test_big.py::test_{i}" for i in range(2000)]
rows = [{"ac": "AC-1", "tests": ids, "evidence": "", "status": ""}]
t = time.monotonic()
gaps, notes = gate.check_test_assertions(rows, {"tests/test_big.py": src})
print(round(time.monotonic() - t, 1), gaps, notes)    # 377 s before the per-file cache
""", 60)
    took = float(out.split()[0])
    assert took < 20 and out.split(None, 1)[1].strip() == "[] []"


def test_a_class_method_and_helper_are_still_judged_per_test_after_the_index():
    src = ("import pytest\nclass A:\n    def test_m(self): pass\nclass B:\n    def test_m(self): assert 1\n"
           "def test_m(): pass\ndef helper(): assert 1\ndef test_h(): helper()\n")
    rows = [{"ac": "AC-1", "tests": ["tests/t.py::A::test_m", "tests/t.py::B::test_m", "test_h", "test_m"], "evidence": "", "status": ""}]
    gaps, _ = gate.check_test_assertions(rows, {"tests/t.py": src})
    assert [g["test"] for g in gaps] == ["tests/t.py::A::test_m"]  # a bare `test_m` names three defs and one of them asserts; `test_h` asserts through a helper


def test_more_than_the_cap_of_test_ids_is_a_finding_and_the_rest_are_unmapped():
    n = gate.MAX_TEST_IDS
    ids = [f"FooTest.t{i}" for i in range(n + 5)]
    ledger = f"{SECTION2}\n## 3. AC\n{HDR}| AC-1 | a | {', '.join(ids[:n])} | | red |\n| AC-2 | b | {', '.join(ids[n:])} | | red |\n"
    sources = {"FooTest.cls": "class FooTest { " + " ".join(f"t{i}" for i in range(n + 5)) + " }"}
    r = run_gate(ledger, sources, {}, block=["ac_traceability", "assertion"])
    assert r["traceability"]["unmapped"] == ["AC-2"]  # its ids were past the cap: no known test (the file does hold them)
    assert {"test": "?", "issue": "too many test ids"} in r["assertion_gaps"] and gate.E_TOO_MANY_IDS in r["assertion_notes"]
    assert set(r["blocked_by"]) == {"ac_traceability", "assertion"}
    exact = run_gate(ledger.replace(", ".join(ids[n:]), "FooTest.t0"), sources, {})  # a repeat of an id already counted costs nothing
    assert exact["traceability"]["unmapped"] == [] and exact["assertion_gaps"] == []
    assert "too many" not in render_summary(exact) and "more than" in render_summary(r)


# ---- I-4: `_config`'s fail-closed branches (mutants M20-M22 of the review) ----
def _target_repo(proj, target_cfg):
    """`proj` as a git repository whose commit `origin/main` holds ghp.config.json = `target_cfg`; the working tree then gets a valid advisory config."""
    (proj / "ghp.config.json").write_text(json.dumps(target_cfg))
    _git(proj, "init", "-q", "-b", "main")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-q", "-m", "init")
    _git(proj, "update-ref", "refs/remotes/origin/main", "HEAD")
    _gate_cfg(proj)


def test_an_invalid_config_at_a_resolvable_base_is_exit_3_never_judged_with_the_pr_config(proj, git_env, capsys):
    _target_repo(proj, {"owner": "octo", "repo": "demo", "gate": {"block": ["bogus"]}})
    summary = proj / "s.md"
    assert cli.main(["gate", "7", "--offline", "--base", "origin/main", "--summary-out", str(summary)], gh=FakeGh()) == 3
    cap = capsys.readouterr()
    assert cap.out == "" and "at the target branch" in cap.err and "unknown check" in cap.err and "ghp gate FAILED" in summary.read_text()
    # the same target with a VALID advisory config: the PR's config judges, exit 0
    (proj / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo"}))
    _git(proj, "commit", "-q", "-am", "valid")
    _git(proj, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert cli.main(["gate", "7", "--offline", "--base", "origin/main"], gh=FakeGh()) == 0
    capsys.readouterr()


def test_an_unresolvable_base_with_a_strict_head_fails_with_the_base_error_not_a_later_one(proj, git_env, capsys):
    _target_repo(proj, {"owner": "octo", "repo": "demo"})
    _gate_cfg(proj, mode="strict")
    assert cli.main(["gate", "7", "--offline", "--base", "origin/nope"], gh=FakeGh()) == 3
    err = capsys.readouterr().err
    assert "not a commit" in err  # `_config` refuses first


def test_the_commit_range_base_is_the_full_commit_id_only_in_strict_mode(proj, git_env):
    _target_repo(proj, {"owner": "octo", "repo": "demo"})
    sha = _git(proj, "rev-parse", "HEAD")
    args = _args(src=str(proj), base="origin/main")
    assert gate._gate_base(Config(owner="o", repo="r", mode="strict"), args) == sha
    assert gate._gate_base(Config(owner="o", repo="r"), args) == "origin/main"
    assert gate._gate_base(Config(owner="o", repo="r", mode="strict"), _args(src=str(proj), base="origin/nope")) == "origin/nope"  # unresolvable: as given
    assert gate._gate_base(Config(owner="o", repo="r", mode="strict"), _args(src=str(proj), base=None)) is None


# ---- M-2: the hardening that had no test ----
def test_git_runs_with_a_clean_environment_and_a_closed_stdin(crepo, monkeypatch):
    bad = _commit(crepo, "feat: b", "b")
    monkeypatch.setenv("GIT_DIR", "/nonexistent-elsewhere")  # a repository selector from the environment must not redirect the check
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.pager")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "false")
    real, seen = _sp.run, []

    def spy(*a, **kw):
        seen.append(kw)
        return real(*a, **kw)
    monkeypatch.setattr(gate.subprocess, "run", spy)
    assert check_commits(str(crepo), 2) == ([bad], None)  # GIT_DIR was ignored: the repository at `crepo` was read
    assert seen and all(kw["stdin"] == _sp.DEVNULL and "GIT_DIR" not in kw["env"] and "GIT_CONFIG_KEY_0" not in kw["env"]
                        and kw["env"]["GIT_NO_REPLACE_OBJECTS"] == "1" for kw in seen)


def test_the_base_in_a_commit_note_is_escaped(crepo):
    for base in ("origin/\x1b[31mnope\x07", "-\x1b[31mx"):
        got, note = check_commits(str(crepo), 2, base)
        assert got == [] and "\x1b" not in note and "\x07" not in note and note.endswith("commits not checked")


def test_the_verb_scrubs_and_escapes_what_it_prints(proj, monkeypatch, capsys):
    canary = "gh" + "p_" + "A1b2C3d4E5" * 4
    for text in (f"boom {canary} \x1b[31mred\x07", f"Authorization: token {canary}"):
        monkeypatch.setattr(gate, "evaluate", lambda *a, _t=text: (_ for _ in ()).throw(ValueError(_t)))
        assert cli.main(["gate", "7", "--offline", "--summary-out", str(proj / "s.md")], gh=FakeGh()) == 2
        err = capsys.readouterr().err
        assert canary not in err and "\x1b" not in err and "\x07" not in err and err.startswith("error: ") and err.count("\n") == 1
        assert canary not in (proj / "s.md").read_text()


def test_the_unreachable_rules_line_names_gh_not_a_tracker_token():
    items_ctx = {"files": {}, "drift": {"evaluated": False, "drift": [], "unreachable": True, "note": rules.DRIFT_UNREACHABLE}}
    md = render_summary(run_gate(LEDGER, SOURCES, {}, None, block=["rule_drift"], rules_ctx=items_ctx))
    line = next(x for x in md.splitlines() if "rules-unreachable" in x)
    assert "gh login" in line and "Work Items" not in line and "ADO" not in md and "ado" not in md
