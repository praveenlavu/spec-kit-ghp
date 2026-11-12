"""T11 ledger tests: ported from ado tests/test_dod.py (rename table, tracker-seam fixtures: a work item became an `Issue` with a Markdown
body, the AC is the section under `## Acceptance criteria`), plus the T11 tests of the new rules (copy, header, heading rules, path, output). T12 un-skipped the three ancestor-walk tests (rules on).
The helpers `_wi`, `raw_issue` and `run_dod` replace ado's work-item dict and FakeClient."""
import copy
import hashlib
import json
import os
import re
import time
from pathlib import Path

import pytest

import ghp.__main__ as cli_main
from conftest import FakeGh, approve, profile
from ghp import dod, issues
from ghp.gh import GhError
from ghp.dod import (AC_THIN_CHARS, AcError, DodExists, SECTION2, ac_hash, ac_lines, ac_stats, dod_file, header_block, ledger_ac_hash,
                     ledger_rules_from, ledger_rules_reviewed, ledger_spec_dir, render_dod, stack_dod_items, write_dod)
from ghp.refs import Ref
from rules_support import desc as rules_desc, fail, raw_issue as holder, serve
from test_stack_show import put

FIX = Path(__file__).parent / "fixtures" / "gh"
URL = "https://github.com/octo/demo/issues/1001"


def _wi(ac="", n=1001, title="T", state="open", before="", after="", body=None, url=None):
    """An Issue whose body is `before` + the AC section (`ac` under the heading) + `after` (starts with its own `## ` heading)."""
    if body is None:
        body = (before + "\n\n" if before else "") + "## Acceptance criteria\n" + (ac + "\n" if ac else "") + (("\n" + after + "\n") if after else "")
    return issues.Issue(ref=Ref("octo", "demo", n), node_id="I_kwDOFictional%04d" % n, id=9100000000 + n, title=title, body=body, state=state,
                        state_reason=None, labels=[], milestone=None, author=issues.Actor(1001, "octo-author", "User"), parent=None,
                        sub_total=0, sub_done=0, url=url or "https://github.com/octo/demo/issues/%d" % n)


# ---- the CLI seam: a cwd whose ghp.config.json names octo/demo, and a FakeGh that serves one issue ----
def path(n=1001):
    return f"repos/octo/demo/issues/{n}"


def raw_issue(n=1001, body="## Acceptance criteria\n", title="T", **over):
    d = json.loads((FIX / "issue_story.json").read_text(encoding="utf-8"))
    d.update(number=n, id=9100000000 + n, title=title, body=body, html_url="https://github.com/octo/demo/issues/%d" % n)
    d.update(over)
    return d


def set_config(root, **extra):
    (root / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", **extra}))


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    set_config(tmp_path)
    return tmp_path


def run_dod(capsys, *args, n=1001, gh=None, **issue):
    """`ghp dod <n> ARGS` against a FakeGh serving the issue; (exit code, stdout, stderr)."""
    gh = gh or FakeGh().on_rest("GET", path(n), raw_issue(n, **issue))
    code = cli_main.main(["dod", str(n), *args], gh=gh)
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def _ledger(root, n=1001):
    return (root / "dod" / f"DoD_{n}.md").read_bytes().decode("utf-8")


# ============================ ported: rendering ============================
def test_render_seeds_ac_verbatim():
    md = render_dod(_wi("AC one"), synced="2026-07-14")
    assert "# DoD — #1001: T" in md
    assert URL in md
    assert "Last synced:** 2026-07-14" in md
    assert "AC one" in md


def test_render_has_all_five_sections():
    md = render_dod(_wi(), synced="2026-07-14")
    for marker in ["## 1. Seeded from the issue", "## 2. Brainstorm & plan",
                   "## 3. AC", "## 4. Gate & review log", "## 5. Definition of Done"]:
        assert marker in md
    assert "coverage meets the project's bar" in md
    assert "≥ 90%" not in md


def test_render_is_deterministic():
    assert render_dod(_wi(), synced="2026-07-14") == render_dod(_wi(), synced="2026-07-14")


def test_render_handles_missing_ac():
    assert "_(none in the issue — improve it)_" in render_dod(_wi(), synced="2026-07-14")


_FULL_AC = (
    "Given a user with an active plan, when the nightly job runs, then eligibility "
    "is recalculated and written back to the record. Given a lapsed plan, the user "
    "is excluded and an audit row is written."
)


def test_thin_ac_gets_callout_and_stays_verbatim():
    """AC-1: below the floor -> callout naming the count; AC still rendered verbatim."""
    md = render_dod(_wi("should work"), synced="2026-07-14")
    assert "Thin acceptance criteria" in md
    assert "11 chars" in md          # the measured count is named
    assert "should work" in md       # verbatim seeding is unchanged


def test_full_ac_has_no_callout():
    """AC-2: at/above the floor -> no callout, rendering otherwise unchanged."""
    assert len(_FULL_AC) >= AC_THIN_CHARS
    md = render_dod(_wi(_FULL_AC), synced="2026-07-14")
    assert "Thin acceptance criteria" not in md
    assert "eligibility is recalculated" in md


def test_empty_ac_keeps_placeholder_and_is_thin():
    """AC-3: empty -> existing placeholder preserved AND flagged."""
    md = render_dod(_wi(), synced="2026-07-14")
    assert "_(none in the issue — improve it)_" in md
    assert "Thin acceptance criteria" in md
    assert ac_stats(_wi())["ac_thin"] is True


def test_ac_stats_reports_full_ac_as_not_thin():
    stats = ac_stats(_wi(_FULL_AC))
    assert stats["ac_thin"] is False
    assert stats["ac_chars"] == len(_FULL_AC)


# ============================ ported: write / force / refresh ============================
def test_write_creates_file(tmp_path):
    p = write_dod(_wi(), str(tmp_path), synced="2026-07-14")
    assert p == dod_file(str(tmp_path), 1001)
    assert p.name == "DoD_1001.md"
    assert "## 1. Seeded from the issue" in p.read_text()


def test_write_refuses_clobber_without_force(tmp_path):
    write_dod(_wi(), str(tmp_path), synced="2026-07-14")
    with pytest.raises(DodExists, match="--force"):
        write_dod(_wi(), str(tmp_path), synced="2026-07-14")


def test_force_overwrites(tmp_path):
    write_dod(_wi(title="old"), str(tmp_path), synced="2026-07-14")
    p = write_dod(_wi(title="new"), str(tmp_path), synced="2026-07-14", force=True)
    assert "#1001: new" in p.read_text()


def test_refresh_replaces_section1_preserves_authored(tmp_path):
    p = write_dod(_wi("old AC"), str(tmp_path), synced="2026-07-14")
    text = p.read_text().replace("_(fill in)_", "MY REAL PLAN", 1)  # simulate authored content in section 2+
    p.write_text(text)
    write_dod(_wi("new AC"), str(tmp_path), synced="2026-07-15", refresh=True)
    out = p.read_text()
    assert "new AC" in out and "old AC" not in out     # S1 refreshed
    assert "MY REAL PLAN" in out                        # S2+ preserved
    assert "Last synced:** 2026-07-15" in out           # header re-stamped


def test_refresh_refuses_when_marker_missing(tmp_path):
    write_dod(_wi(), str(tmp_path), synced="2026-07-14")
    p = dod_file(str(tmp_path), 1001)
    p.write_text("hand-mangled file, no section-2 header\nMY WORK")
    with pytest.raises(DodExists, match="refusing to --refresh"):
        write_dod(_wi(), str(tmp_path), synced="2026-07-14", refresh=True)
    assert "MY WORK" in p.read_text()  # authored content preserved, not clobbered


def test_ledger_written_as_utf8(tmp_path):
    p = write_dod(_wi(), str(tmp_path), synced="2026-07-14")
    text = p.read_bytes().decode("utf-8")  # raises if not valid utf-8
    assert "—" in text and "≥" not in text


def test_render_section3_has_ac_tag_convention(tmp_path):
    md = render_dod(_wi(), synced="2026-07-14")
    assert "| AC | acceptance criterion | tests | commit/PR | status |" in md
    assert "AC-1" in md
    assert "tag each AC" in md


# ============================ ported: AC hash, spec dir ============================
def test_ac_hash_empty():
    assert ac_hash(_wi()) == hashlib.sha256(b"").hexdigest()


def test_ac_hash_non_ascii():
    h = ac_hash(_wi("Café ✓"))
    assert h == ac_hash(_wi("Café ✓"))
    assert h == hashlib.sha256("Café ✓".encode("utf-8")).hexdigest()


def test_render_has_hash_and_spec_dir_lines():
    wi = _wi("AC one")
    md = render_dod(wi, synced="2026-07-14", spec_dir="specs/001-x")
    assert ledger_ac_hash(md) == ac_hash(wi)
    assert ledger_spec_dir(md) == "specs/001-x"
    # header lines sit above section 2 and the placeholder reads as None
    assert md.index("**AC hash:**") < md.index(SECTION2)
    unset = render_dod(wi, synced="2026-07-14")
    assert "- **Spec dir:** _(set by speckit.ghp.pull)_" in unset
    assert ledger_spec_dir(unset) is None
    assert ledger_ac_hash("no header here") is None
    assert ledger_spec_dir("no header here") is None


def test_refresh_keeps_spec_dir_when_not_given(tmp_path):
    write_dod(_wi("old"), str(tmp_path), synced="d1", spec_dir="specs/001-x")
    p = write_dod(_wi("new"), str(tmp_path), synced="d2", refresh=True)
    out = p.read_text()
    assert ledger_spec_dir(out) == "specs/001-x"
    assert ledger_ac_hash(out) == ac_hash(_wi("new"))
    p = write_dod(_wi("new"), str(tmp_path), synced="d3", refresh=True, spec_dir="specs/002-y")
    assert ledger_spec_dir(p.read_text()) == "specs/002-y"


def test_section3_has_evidence_column():
    md = render_dod(_wi(), synced="d")
    assert "| AC | acceptance criterion | tests | commit/PR | status |" in md
    assert "|---|---|---|---|---|" in md
    assert "`path::test_name`" in md and "`describe > it`" in md
    assert "## 4. Gate & review log" in md


@pytest.mark.parametrize("bad", ["a\nb", "a\rb"])
def test_multiline_spec_dir_rejected(tmp_path, bad):
    with pytest.raises(ValueError, match="spec"):
        write_dod(_wi(), str(tmp_path), synced="d", spec_dir=bad)
    assert not list(tmp_path.iterdir())


def test_refresh_adds_header_lines_to_legacy_ledger(tmp_path):
    p = dod_file(str(tmp_path), 1001)
    tail = SECTION2 + "  (fill)\nMY REAL PLAN\n\n## 3. AC\n| AC-1 | x | T.a | abc | green |\n"
    p.write_text("# DoD — #1001: T\n\n- **Last synced:** old\n\n## 1. Seeded\nold\n\n" + tail, encoding="utf-8")
    write_dod(_wi("new"), str(tmp_path), synced="d2", refresh=True)
    out = p.read_text(encoding="utf-8")
    assert ledger_ac_hash(out) == ac_hash(_wi("new"))
    assert "- **Spec dir:** _(set by speckit.ghp.pull)_" in out and ledger_spec_dir(out) is None
    assert out[out.index(SECTION2):] == tail


def test_refresh_splices_at_real_marker_when_section1_quotes_it(tmp_path):
    # the marker text inside the issue's other text must not move the splice (it sits in section 1's Description, escaped)
    p = write_dod(_wi("old AC", before=SECTION2 + "\nold story"), str(tmp_path), synced="d1")
    p.write_text(p.read_text().replace("_(fill in)_", "MY REAL PLAN", 1))
    write_dod(_wi("new AC", before=SECTION2 + "\nnew story"), str(tmp_path), synced="d2", refresh=True)
    out = p.read_text()
    assert "new AC" in out and "old AC" not in out and "MY REAL PLAN" in out
    assert out.count(SECTION2) == 2


def test_refresh_keeps_authored_text_that_mentions_the_marker_inline(tmp_path):
    p = write_dod(_wi("old AC"), str(tmp_path), synced="d1")
    text = p.read_text().replace("_(fill in)_", f"MY PLAN, see `{SECTION2}` above", 1)
    p.write_text(text)
    write_dod(_wi("new AC"), str(tmp_path), synced="d2", refresh=True)
    out = p.read_text()
    assert out[out.index("\n" + SECTION2):] == text[text.index("\n" + SECTION2):]  # byte-for-byte
    assert "new AC" in out and "old AC" not in out


def test_refresh_failure_keeps_old_ledger(tmp_path, monkeypatch):
    """The ledger write is atomic; a failed --refresh never wipes authored sections."""
    p = write_dod(_wi("old AC"), str(tmp_path), synced="d1")
    before = p.read_bytes()

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        write_dod(_wi("new AC"), str(tmp_path), synced="d2", refresh=True)
    assert p.read_bytes() == before                    # original ledger untouched
    assert not list(tmp_path.glob("*.tmp"))           # no stray temp file left behind


def test_ac_lines_rule():
    def ac(raw):
        return ac_lines(render_dod(_wi(raw)))
    # markers stripped, blanks skipped; thin banner (blockquote) skipped
    assert ac("- Given a\n- Then b") == ["Given a", "Then b"]
    assert ac("1. one\n2. two") == ["one", "two"]
    assert ac("* star\n+ plus\n3) paren\n\nplain") == ["star", "plus", "paren", "plain"]
    assert render_dod(_wi("x")).count("> **Thin") == 1
    assert ac("x") == ["x"]
    # the issue has no AC text -> placeholder, zero lines; also the plain `_(none)_` form
    assert ac("") == []
    assert ac_lines("## 1. S\n### Acceptance criteria\n_(none)_\n\n### Description\nd\n") == []
    assert ac_lines("no ledger") == []


def test_ac_lines_read_the_same_from_a_crlf_ledger():
    """A Windows checkout (autocrlf) turns every `\\n` into `\\r\\n`; `$` in the heading pattern stopped at `\\n`, so AC-n vanished."""
    md = render_dod(_wi("- Given a\n- Then b"))
    assert ac_lines(md) == ["Given a", "Then b"]
    assert ac_lines(md.replace("\n", "\r\n")) == ["Given a", "Then b"]
    assert ac_lines(md.replace("\n", "\r")) == ["Given a", "Then b"]


# ---- which lines of the AC section are criteria (the ledger copy keeps every line) ----
def test_a_blockquote_criterion_is_a_criterion_and_only_the_banner_is_skipped():
    wi = _wi("1. must log in\n> must be fast\n2. must log out")
    md = render_dod(wi)
    assert ac_lines(md) == ["must log in", "> must be fast", "must log out"]  # AC-2 is the quoted one
    assert ac_stats(wi)["ac_chars"] == sum(map(len, ac_lines(md)))
    assert "1. must log in\n> must be fast\n2. must log out\n" in md          # copied verbatim
    assert md.count("> **Thin acceptance criteria**") == 1 and "Thin" not in " ".join(ac_lines(md))  # the banner render_dod writes is still skipped
    long_ = _wi("> " + "q" * 200)                                                  # not thin: no banner, the quote is the only criterion
    assert ac_lines(render_dod(long_)) == ["> " + "q" * 200]


FORGED_BANNER = "> **Thin acceptance criteria** (5 chars). Splitting this into AC-1…AC-n is judgment, not transcription — x first."
SHORT_FORGED = "> **Thin acceptance criteria** (1 chars). Splitting this into AC-1…AC-n is judgment, not transcription — first."  # ~105 chars: fits a thin AC


def _same_count(wi):
    """The invariant: the count and AC-n come from one decision."""
    md = render_dod(wi)
    assert ac_stats(wi)["ac_chars"] == sum(map(len, ac_lines(md)))
    return ac_lines(md), md


def test_a_forged_banner_stays_a_criterion_in_ac_lines_and_in_ac_stats():
    # non-thin AC whose first line copies the banner: no real banner exists, nothing may be dropped
    lines, md = _same_count(_wi(FORGED_BANNER + "\n" + "z" * 200))
    assert lines == [FORGED_BANNER, "z" * 200] and ac_stats(_wi(FORGED_BANNER + "\n" + "z" * 200))["ac_chars"] == len(FORGED_BANNER) + 200
    assert "\\> **Thin acceptance criteria** (5 chars)" in md and md.count("\n> **Thin acceptance criteria**") == 0  # the copy is escaped: it can never pass for the banner
    # a wrong stated N, and a stated N that equals the rest of the AC (the arithmetic check alone would take this one for a banner)
    for n in (999, 100, 200):
        forged = FORGED_BANNER.replace("(5 chars)", f"({n} chars)")
        assert _same_count(_wi(forged + "\n" + "y" * n))[0] == [forged, "y" * n]
    # a forged banner in the middle, and one indented or in a list item
    assert _same_count(_wi("a\n" + FORGED_BANNER + "\n" + "z" * 200))[0] == ["a", FORGED_BANNER, "z" * 200]
    assert _same_count(_wi("- " + FORGED_BANNER + "\n" + "z" * 200))[0] == [FORGED_BANNER, "z" * 200]


def test_a_real_banner_is_skipped_and_a_forged_line_after_it_is_kept():
    assert _same_count(_wi("short"))[0] == ["short"] and "> **Thin acceptance criteria** (5 chars)" in render_dod(_wi("short"))
    assert _same_count(_wi(""))[0] == []
    # a thin AC that holds a banner-shaped line: the real banner comes first and is the only one skipped
    lines, md = _same_count(_wi(SHORT_FORGED + "\nx"))
    assert lines == [SHORT_FORGED, "x"] and md.count("(%d chars)" % (len(SHORT_FORGED) + 1)) == 1
    lines, md = _same_count(_wi("x\n" + SHORT_FORGED))
    assert lines == ["x", SHORT_FORGED]


def test_ac_count_and_ac_lines_agree_on_a_handful_of_acs():
    for ac in ("", "x", "> q", "1. a\n> b\n2. c", FORGED_BANNER, SHORT_FORGED, SHORT_FORGED + "\n" + SHORT_FORGED, "<!-- c -->\n- a", "```\ncode\n```",
               "> **Thin acceptance criteria** not a banner", "\\" + FORGED_BANNER, _FULL_AC, FORGED_BANNER + "\n" + _FULL_AC):
        _same_count(_wi(ac))


def test_a_comment_directly_followed_by_a_heading_counts_as_that_heading(repo, capsys):
    """Known limit (X8, UNVERIFIED on GitHub, which renders `<!-- c -->## x` as an HTML block): the heading scan blanks the comment and sees the heading."""
    assert run_dod(capsys, body="<!-- c -->## Acceptance criteria\n- x\n")[0] == 0 and ac_lines(_ledger(repo)) == ["x"]
    assert run_dod(capsys, "--force", body="## Acceptance criteria\n- a\n<!-- c -->## Notes\nafter\n")[0] == 0
    assert ac_lines(_ledger(repo)) == ["a"]


def test_comment_and_fence_delimiter_lines_are_no_criteria_but_stay_in_the_copy():
    raw = ("<!-- list each criterion -->\n- real one\n<!--\nhidden a\nhidden b\n-->\n```text\ncode criterion\n<!-- code, not a comment -->\n```\n"
           "~~~\ntilde code\n~~~\n- last <!-- trailing hint -->\n- also <!-- spans\nlines --> kept\n<!-- never closed\nmore hidden")
    wi = _wi(raw)
    md = render_dod(wi)
    want = ["real one", "code criterion", "<!-- code, not a comment -->", "tilde code", "last", "also", "kept"]
    assert ac_lines(md) == want
    assert ac_stats(wi)["ac_chars"] == sum(map(len, want))
    for line in raw.split("\n"):                                     # nothing is dropped from the copy: every line is there (hazards escaped)
        assert dod._esc(line) in md.split("### Description")[0], line
    assert ac_hash(wi) == hashlib.sha256(raw.encode()).hexdigest()   # the hash covers the whole AC text, hidden lines included
    assert ac_lines(md.replace("\n", "\r\n")) == want


@pytest.mark.parametrize("end", ["# H1", "  ## Notes", "   # H", "##\tNotes", "##", "#\tx", "# ", "## Notes ##", "   ##"])
def test_the_ac_section_ends_at_any_visible_atx_heading_of_level_1_or_2(repo, capsys, end):
    code, out, err = run_dod(capsys, body=f"## Acceptance criteria\n- a\n{end}\nafter\n")
    assert code == 0
    md = _ledger(repo)
    assert ac_lines(md) == ["a"] and "after" in md.split("### Description\n", 1)[1]


@pytest.mark.parametrize("stays", ["### sub", "#hashtag", "    ## indented code", "####### seven", "#!shebang"])
def test_lines_that_are_no_level_1_or_2_heading_stay_in_the_ac(repo, capsys, stays):
    code, out, err = run_dod(capsys, body=f"## Acceptance criteria\n- a\n{stays}\n- b\n## Notes\nafter\n")
    assert code == 0 and ac_lines(_ledger(repo)) == ["a", stays.strip(), "b"]


def test_a_heading_inside_a_fence_or_comment_does_not_end_the_section_and_a_setext_heading_does_not_either(repo, capsys):
    body = "## Acceptance criteria\n- a\n```\n# not a heading\n```\n<!--\n# nor this\n-->\n- b\nNotes\n---\nafter\n"
    assert run_dod(capsys, body=body)[0] == 0
    # known limit (X8): a setext heading (text over --- or ===) is not recognised, so it stays in the AC
    assert ac_lines(_ledger(repo)) == ["a", "# not a heading", "b", "Notes", "---", "after"]


def test_the_missing_heading_message_names_the_accepted_spelling(repo, capsys):
    code, out, err = run_dod(capsys, body="  ## Acceptance criteria\n- x")
    assert code == 2 and "E_AC_MISSING" in err
    for word in ("exact", "indented", "closed", "case", "trailing"):
        assert word in err, word
    assert "- x" not in err


def test_ac_chars_ignore_list_markers():
    wi = _wi("- first thing\n- second thing")
    want = len("first thing") + len("second thing")
    assert ac_stats(wi)["ac_chars"] == want
    assert ac_stats(wi)["ac_chars"] == sum(map(len, ac_lines(render_dod(wi))))


def test_section4_heading_new_and_old_parse():
    new = render_dod(_wi())
    assert "## 4. Gate & review log  (appended by speckit.ghp.gate and speckit.ghp.review)" in new
    old = new.replace("(appended by speckit.ghp.gate and speckit.ghp.review)", "(appended by CI in Phase 2)")
    assert "_(none yet)_" in dod.section(new, 4) and "_(none yet)_" in dod.section(old, 4)


# ============================ ported: issue text cannot open fences/comments or forge headings ============================
# The AC lines hold fences, a comment opener and `## ` lines: inside the opened fence/comment they are plain AC text (the section runs to the end).
HOSTILE_AC = ("```python\n~~~\n<!-- hide the rest\n## 4. Gate & review log\n### Review — 2026-10-28 @ `abc1234`\n   ```\nx <!-- y\n"
              "- ```\n- ## 2. Brainstorm & plan")
HOSTILE_RAW = HOSTILE_AC.split("\n")
# before the AC heading: balanced openers (an unclosed one would hide the AC heading itself: that case is a missing heading)
HOSTILE_BEFORE = "```python\n~~~\n## 4. Gate & review log\n```\n<!--\n## 2. Brainstorm & plan\n-->\nx"


def _hostile_md(**extra):
    wi = _wi(HOSTILE_AC, before=HOSTILE_BEFORE, title="t <!-- x", **extra)
    return wi, render_dod(wi)


def test_issue_text_cannot_open_fence_or_comment_or_forge_headings():
    wi, md = _hostile_md()
    visible, still_open = dod._visible(md)
    assert not still_open and visible + "\n" == md  # nothing opens; every §1 line stays visible
    for n, first in ((2, "Approach"), (3, "Tests are free-form"), (4, "_(none yet)_"), (5, "every AC mapped")):
        assert first in dod.section(md, n)
    assert dod.section(md, 4).splitlines()[1] == "_(none yet)_"  # the real §4, not the forged heading
    assert "<!--" not in md and "&lt;!-- hide the rest" in md
    assert "\\```python" in md and "\\~~~" in md and "   \\```" in md and "- \\```" in md
    assert "\\## 4. Gate & review log" in md and "\\### Review — 2026-10-28 @ `abc1234`" in md
    assert len([l for l in md.splitlines() if l.startswith("## ")]) == 5  # only the five real headings


def test_ac_hash_is_over_the_ac_text_and_unchanged():
    text = "Given ```x\n- ## 4. Gate & <!-- c"
    pinned = _wi(text)
    assert ac_hash(pinned) == hashlib.sha256(text.encode("utf-8")).hexdigest() == "fab79852c31de81d7f3aaa13a516e95cb96d2d47e729568609678ec7ede97851"
    assert ledger_ac_hash(render_dod(pinned)) == ac_hash(pinned)
    assert ac_hash(_wi(text.replace("\n", "\r\n"), body="## Acceptance criteria\r\n" + text.replace("\n", "\r\n"))) == ac_hash(pinned)  # CRLF is no edit


def test_ac_lines_and_thin_count_ignore_escapes():
    want = ["### 4. Gate", "a  c", "#tag", "x \\# y"]
    wi = _wi("### 4. Gate\na <!-- b --> c\n#tag\n- x \\# y")  # the escape is undone; a comment inside a line is cut out
    md = render_dod(wi)
    assert "\\### 4. Gate\n" in md and "\\#tag\n" in md and ac_lines(md) == want
    assert ac_stats(wi)["ac_chars"] == sum(map(len, want))
    assert "Thin acceptance criteria** (%d chars)" % sum(map(len, want)) in md


def test_unicode_and_control_line_breaks_cannot_open_fence():
    before = "a\u2028```\nb\u2029~~~\nc\x0c<!-- x -->\nd\x85```"
    wi = _wi("x\u2028```", before=before, title="t\r```\n<!--")
    md = render_dod(wi)
    visible, still_open = dod._visible(md)
    assert not still_open and visible + "\n" == md
    assert dod.section(md, 4).splitlines()[1] == "_(none yet)_"
    assert md.splitlines()[0] == "# DoD — #1001: t ``` &lt;!--"  # title collapsed to one line, escaped
    assert ac_hash(wi) == ac_hash(_wi("x ```"))  # a line break only Python sees is no line break: it is a space in the text we hash and copy
    assert not re.search("[\x0b\x0c\x1c-\x1e\x85\u2028\u2029\r]", md)  # no line break only Python's splitlines sees survives into the ledger


# ============================ ported: a heading inside a fence or comment is never a heading ============================
HIDERS = {"fence": ("```\n", "```\n"), "tilde": ("~~~\n", "~~~\n"), "comment": ("<!--\n", "-->\n")}


def _authored(hide=None, extra=""):
    """Real ledger whose §4 holds `hide`: a copy of the `## 2.` / `## 5.` headings inside a fence or comment."""
    o, c = HIDERS[hide] if hide else ("", "")
    copy_ = f"{o}{SECTION2}\nFAKE PLAN\n## 5. Definition of Done\n- [x] fake\n{c}" if hide else ""
    return (render_dod(_wi()).replace("_(none yet)_", "_(none yet)_\n" + copy_ + extra, 1)
            .replace("_(fill in)_", "MY REAL PLAN", 1))


@pytest.mark.parametrize("hide", HIDERS)
def test_section_and_marker_ignore_hidden_headings(hide):
    md = _authored(hide)
    assert md.index(SECTION2, dod._section2_at(md) + 1) > dod._section2_at(md)  # a later raw copy exists, and loses
    assert "MY REAL PLAN" in dod.section(md, 2) and "FAKE PLAN" not in dod.section(md, 2)
    assert "- [x] fake" in dod.section(md, 4) and "- [x] fake" not in dod.section(md, 5)  # authored text stays content of its section
    assert "every AC mapped" in dod.section(md, 5)


@pytest.mark.parametrize("hide", HIDERS)
def test_refresh_keeps_sections_2_to_5_when_a_hidden_copy_of_the_marker_sits_later(tmp_path, hide):
    md = _authored(hide)
    p = tmp_path / "DoD_1001.md"
    p.write_text(md, encoding="utf-8")
    write_dod(_wi("brand new AC"), tmp_path, refresh=True)
    out = p.read_text(encoding="utf-8")
    assert out[out.index("\n" + SECTION2):] == md[md.index("\n" + SECTION2):]  # §2-§5 byte-equal
    assert "brand new AC" in out and "MY REAL PLAN" in out


def test_ac_lines_ignore_a_hidden_ac_heading_and_marker():
    md = render_dod(_wi("real one"))
    fenced = md.replace("### Acceptance criteria", "```\n### Acceptance criteria\nfake\n```\n### Acceptance criteria", 1)
    assert ac_lines(fenced) == ["real one"]
    hidden_marker = md + f"<!--\n{SECTION2}\n-->\n"
    assert ac_lines(hidden_marker) == ["real one"]


@pytest.mark.parametrize("opener", ["```\n", "~~~\n", "<!--\n"])
def test_refresh_refuses_an_unclosed_opener(tmp_path, opener):
    md = _authored(extra=opener)
    p = tmp_path / "DoD_1001.md"
    p.write_text(md, encoding="utf-8")
    with pytest.raises(DodExists, match="unclosed code fence or comment"):
        write_dod(_wi("new"), tmp_path, refresh=True)
    assert p.read_text(encoding="utf-8") == md


def test_mask_is_length_preserving_and_visible_is_unchanged():
    for md in ("a\r\nb\n```\nx\r\n```\nc <!-- q\nr --> d\n", "x\x0cy ```\nz\n", "```\nunclosed", "t\n\n"):
        assert len(dod._mask(md)) == len(md)
        assert dod._visible(md)[0].count("\n") == len(md.splitlines()) - 1
    assert dod._mask("a <!-- b --> c\n```\nd\n```\n") == "a " + " " * 10 + " c\n" + " " * 3 + "\n \n" + " " * 3 + "\n"


# ---- --refresh repairs only an opener in §1, spliced at the FIRST raw marker ----
def _s1(opener, extra=""):
    """Old-style ledger (the text was not escaped yet): an unclosed `opener` in §1, a real plan in §2."""
    md = render_dod(_wi()).replace("_(fill in)_", "MY PLAN", 1)
    return md.replace("### Description\n_(none)_", f"### Description\n{extra}{opener}\nsee code", 1)


def _copy(md, opener=""):
    """A pasted copy of the §2 heading (after `opener`, if any) inside the real §2."""
    return md.replace("- **Assumptions:**", f"{opener}\n{SECTION2}\n- example\n- **Assumptions:**", 1)


def _refresh(tmp_path, md, **fields):
    p = tmp_path / "DoD_1001.md"
    p.write_bytes(md.encode("utf-8"))
    write_dod(_wi(**fields), tmp_path, refresh=True)
    return p.read_bytes().decode("utf-8")


def _refused(tmp_path, md):
    p = tmp_path / "DoD_1001.md"
    p.write_bytes(md.encode("utf-8"))
    with pytest.raises(DodExists, match="unclosed code fence or comment"):
        write_dod(_wi(), tmp_path, refresh=True)
    assert p.read_bytes().decode("utf-8") == md  # byte-unchanged: the real §2 text is never dropped


@pytest.mark.parametrize("crlf", [False, True])
@pytest.mark.parametrize("opener", ["```", "~~~", "<!--"])
def test_refresh_repairs_an_unclosed_opener_in_section_1(tmp_path, opener, crlf):
    md = _s1(opener)
    md = md.replace("\n", "\r\n") if crlf else md
    assert dod.unclosed(md)
    out = _refresh(tmp_path, md, ac="new AC", after=f"## Notes\n{opener}\nnew story")
    # §2-§5 byte-identical (write_dod reads with universal newlines, so CRLF comes back as LF: as in ado)
    assert out[out.index(SECTION2):] == md[md.index(SECTION2):].replace("\r\n", "\n")
    assert not dod.unclosed(out) and "new AC" in out and "MY PLAN" in out and "see code" not in out
    assert ("&lt;!--" if opener == "<!--" else f"\\{opener}") in out  # the new §1 is escaped


def test_refresh_repairs_two_openers_in_section_1(tmp_path):
    md = _s1("```\n~~~")
    out = _refresh(tmp_path, md)
    assert out[out.index(SECTION2):] == md[md.index(SECTION2):] and "MY PLAN" in out


OPENERS = [("```", "~~~"), ("~~~", "```"), ("```", "<!--"), ("<!--", "```")]


@pytest.mark.parametrize("o1,o2", OPENERS)
def test_refresh_refuses_openers_in_section_1_and_2_before_a_marker_copy(tmp_path, o1, o2):
    _refused(tmp_path, _copy(_s1(o1), o2))


@pytest.mark.parametrize("opener", ["```", "~~~", "<!--"])
def test_refresh_refuses_an_opener_in_section_2_before_a_pasted_marker_copy(tmp_path, opener):
    _refused(tmp_path, _copy(render_dod(_wi()).replace("_(fill in)_", "MY PLAN", 1), opener))


@pytest.mark.parametrize("opener", ["```", "~~~", "<!--"])
def test_refresh_refuses_an_opener_in_section_4_with_a_marker_copy(tmp_path, opener):
    _refused(tmp_path, render_dod(_wi()).replace("_(none yet)_", f"{opener}\n{SECTION2}", 1))


@pytest.mark.parametrize("o1,o2", [("```", "~~~"), ("~~~", "```"), ("<!--", "```"), ("```", "<!--")])
def test_refresh_refuses_when_the_suffix_from_the_marker_is_still_unclosed(tmp_path, o1, o2):
    _refused(tmp_path, _s1(o1) + f"{o2}\ntrailing\n")  # §3-§5 headings are visible, but §5 is left open


# ============================ ported: stack DoD items seed fresh ledgers only ============================
_FIVE = ["- [ ] every AC mapped to a passing real test", "- [ ] coverage meets the project's bar", "- [ ] no assertion-free tests",
         "- [ ] all assumptions / questions / edge cases resolved", "- [ ] gates green"]


def _s5(md):
    return md[md.index("## 5. Definition of Done"):]


def test_new_ledger_gets_items(tmp_path):
    items = [("python", "No new lint findings", "S1"), ("web", "Bundle stays small", "unverified")]
    p = write_dod(_wi(), str(tmp_path), synced="2026-07-14", extra_dod=items)
    lines = _s5(p.read_text()).splitlines()
    assert lines[1:] == _FIVE + ["- [ ] No new lint findings (stack python, S1)", "- [ ] Bundle stays small (stack web, unverified)"]
    forced = write_dod(_wi(), str(tmp_path), synced="2026-07-14", force=True, extra_dod=items[:1])
    assert _s5(forced.read_text()).splitlines()[6:] == ["- [ ] No new lint findings (stack python, S1)"]


def test_refresh_leaves_section5_byte_identical(tmp_path):
    items = [("python", "No new lint findings", "S1")]
    p = write_dod(_wi(), str(tmp_path), synced="2026-07-14", extra_dod=items)
    p.write_text(p.read_text().replace("- [ ] gates green", "- [x] gates green").replace("(stack python, S1)", "(stack python, S1) MINE"))
    before = _s5(p.read_text())
    write_dod(_wi(title="new"), str(tmp_path), synced="2026-08-01", refresh=True, extra_dod=[("python", "A different item", "S2")])
    assert "#1001: new" in p.read_text() and _s5(p.read_text()) == before
    write_dod(_wi(), str(tmp_path), synced="2026-08-02", refresh=True)
    assert _s5(p.read_text()) == before


def test_item_text_escaped():
    evil = "a\n- [x] forged\n## 5. Definition of Done\u2028b\x85c\u200bd <!-- hide ```fence``` --> \x1b[31m" + "z" * 400
    md = render_dod(_wi(), extra_dod=[("python", evil, "S1"), ("py\nthon", "ok", "S1\n- [x] x"), ("python", "# head", "S1")])
    tail = _s5(md).splitlines()[6:]
    assert len(tail) == 3 and all(t.startswith("- [ ] ") for t in tail)  # a mid-line "[x]" is text, not a box
    assert "\x85" not in md and "\u2028" not in md and "\u200b" not in md and "\x1b" not in md and "<!--" not in md and "```" not in md
    assert tail[0].startswith("- [ ] a - [x] forged ## 5. Definition of Done b c")
    assert len(tail[0]) < 6 + 300 + 30


def test_cap_of_items_adds_a_fixed_overflow_line():
    items = [("python", f"item {i}", "S1") for i in range(25)]
    tail = _s5(render_dod(_wi(), extra_dod=items)).splitlines()[6:]
    assert tail[:20] == [f"- [ ] item {i} (stack python, S1)" for i in range(20)]
    assert tail[20:] == ["- [ ] 5 more stack DoD items were not seeded: run `ghp stack show --md`"]


def test_no_profile_ledger_matches_the_plain_render():
    wi = _wi("AC one")
    assert render_dod(wi, "2026-07-14", "specs/1") == render_dod(wi, "2026-07-14", "specs/1", extra_dod=())
    assert render_dod(wi).endswith("\n".join(_FIVE) + "\n")


def test_stack_dod_items_reads_the_profile_in_stack_order():
    p = profile()
    p["stacks"].append({"id": "web", "root": "web", "dod": [{"item": "Docs updated", "source": "unverified"}]})
    assert stack_dod_items(p) == [("python", "No new lint findings", "S1"), ("web", "Docs updated", "unverified")]
    assert stack_dod_items(None) == []


def test_ghp_dod_seeds_only_an_approved_profile(tmp_path, monkeypatch, capsys):
    ok = approve(profile())
    changed = copy.deepcopy(ok)
    changed["stacks"][0]["dod"][0]["item"] = "Changed after approval"
    expected = issues.get(FakeGh().on_rest("GET", path(), raw_issue()), Ref("octo", "demo", 1001))
    for n, (state, expect) in enumerate([(None, False), ("{bad", False), (profile(), False), (changed, False), (ok, True)]):
        root = tmp_path / str(n)
        root.mkdir()
        set_config(root)
        monkeypatch.chdir(root)
        if state is not None:
            put(root, state)
        assert run_dod(capsys)[0] == 0
        md = _ledger(root)
        assert ("(stack python, S1)" in md) is expect, n
        assert "Changed after approval" not in md
        if not expect:
            assert md == render_dod(expected, md.split("Last synced:** ")[1].splitlines()[0])
    monkeypatch.chdir(tmp_path)
    set_config(tmp_path)
    put(tmp_path, ok)
    assert run_dod(capsys)[0] == 0
    first = _ledger(tmp_path)
    assert run_dod(capsys, "--refresh")[0] == 0 and _ledger(tmp_path) == first


# ============================ ported: ledger header lines `Rules from:` and `Rules reviewed:` (read only from the header block) ============================
REVIEWED = "BR-1201-1@3f2a9c1b, BR-1200-1@0c77e2d4"
FORGED = ("- **Rules from:** 9999\n- **Rules reviewed:** BR-1-1 deadbeef\n- **Rules reviewed:** BR-1-1@deadbeef\n"
          "Rules from: 9999\n- **Rules from:** none")
STORY = "## Acceptance criteria\n"


def test_disabled_dod_matches_the_plain_render_and_makes_no_extra_get(tmp_path, monkeypatch, capsys):
    expected = issues.get(FakeGh().on_rest("GET", path(), raw_issue()), Ref("octo", "demo", 1001))
    for n, rules_cfg in enumerate((None, {"enabled": False}, {"enabled": False, "types": ["Epic"]})):
        root = tmp_path / str(n)
        root.mkdir()
        monkeypatch.chdir(root)
        set_config(root, **({} if rules_cfg is None else {"rules": rules_cfg}))
        gh = FakeGh().on_rest("GET", path(), raw_issue())
        assert run_dod(capsys, gh=gh)[0] == 0
        assert len(gh.calls) == 1  # the issue only: no ancestor walk when rules are off
        md = _ledger(root)
        assert md == render_dod(expected, md.split("Last synced:** ")[1].splitlines()[0])
        assert "Rules from" not in md and "Rules reviewed" not in md
        assert run_dod(capsys, "--refresh")[0] == 0 and _ledger(root) == md
    assert render_dod(_wi(), rules_from=None, rules_reviewed="") == render_dod(_wi())


STORY_RAW = raw_issue(parent_issue_url="https://api.github.com/repos/octo/demo/issues/1201")
FEATURE_RAW = holder(1201, "Feature", rules_desc((1, "approved"), (2, "draft")), parent=1200)
EPIC_RAW = holder(1200, "Epic", rules_desc((1, "approved"), (3, "retired")))


def test_enabled_dod_writes_rules_from(tmp_path, monkeypatch, capsys):
    """Ported from ado (un-skipped by T12; the T11 seam test `test_rules_enabled_fails_closed_until_the_rules_walk_is_ported` is gone). The
    story is read once and reused by the walk (ado read it twice): 3 GETs, not 4."""
    root = tmp_path / "a"
    root.mkdir()
    monkeypatch.chdir(root)
    set_config(root, rules={"enabled": True})
    gh = serve(STORY_RAW, FEATURE_RAW, EPIC_RAW)
    assert run_dod(capsys, gh=gh)[0] == 0
    assert [c[2] for c in gh.calls] == [path(1001), path(1201), path(1200)] and all(c[:2] == ("rest", "GET") and c[3] is None for c in gh.calls)
    md = _ledger(root)
    assert "- **Spec dir:** _(set by speckit.ghp.pull)_\n- **Rules from:** 1201, 1200\n\n## 1. " in md
    assert ledger_rules_from(md) == {"present": True, "valid": True, "ids": [1201, 1200]}
    assert ledger_rules_reviewed(md) == {"present": False, "valid": True, "hashes": {}}
    # no ancestor with a section: `none`
    other = tmp_path / "x"
    other.mkdir()
    monkeypatch.chdir(other)
    set_config(other, rules={"enabled": True})
    assert run_dod(capsys, gh=serve(STORY_RAW, holder(1201, "Feature", "no rules")))[0] == 0
    assert ledger_rules_from(_ledger(other)) == {"present": True, "valid": True, "ids": []}
    assert "- **Rules from:** none\n" in _ledger(other)


def test_ancestor_get_failure_exit_1_ledger_untouched(tmp_path, monkeypatch, capsys):
    for refresh in (False, True):
        root = tmp_path / str(refresh)
        root.mkdir()
        monkeypatch.chdir(root)
        set_config(root, rules={"enabled": True})
        gh = fail(GhError("E_GH_HTTP", 404, "ancestor"), 1200, gh=serve(STORY_RAW, FEATURE_RAW))
        if refresh:
            (root / "dod").mkdir()
            old = render_dod(_wi(), "old", rules_from=[7], rules_reviewed=REVIEWED)
            (root / "dod" / "DoD_1001.md").write_bytes(old.encode("utf-8"))
        code, out, err = run_dod(capsys, *(["--refresh"] if refresh else []), gh=gh)
        assert code == 1 and "404" in err
        if refresh:
            assert _ledger(root) == old and list((root / "dod").iterdir()) == [root / "dod" / "DoD_1001.md"]
        else:
            assert not (root / "dod").exists()


def _edit_header(root, line):
    p = root / "dod" / "DoD_1001.md"
    p.write_bytes(p.read_bytes().replace(b"\n\n## 1. ", f"\n{line}\n\n## 1. ".encode(), 1))


def test_refresh_recomputes_rules_from_and_keeps_rules_reviewed(repo, capsys):
    """Ported from ado (the T11 rules-off leg is its third leg; the ancestor-walk legs are un-skipped by T12)."""
    set_config(repo, rules={"enabled": True})
    assert run_dod(capsys, gh=serve(STORY_RAW, FEATURE_RAW, EPIC_RAW))[0] == 0
    _edit_header(repo, f"- **Rules reviewed:** {REVIEWED}")
    p = repo / "dod" / "DoD_1001.md"
    p.write_bytes(p.read_bytes().replace(b"- **Rules from:** 1201, 1200", b"- **Rules from:** 7"))  # a stale value
    p.write_bytes(p.read_bytes().replace(b"_(fill in)_", b"MY REAL PLAN", 1))
    assert run_dod(capsys, "--refresh", gh=serve(STORY_RAW, FEATURE_RAW, EPIC_RAW))[0] == 0
    md = _ledger(repo)
    assert ledger_rules_from(md)["ids"] == [1201, 1200] and "MY REAL PLAN" in md
    assert f"\n- **Rules reviewed:** {REVIEWED}\n" in md and md.count("Rules reviewed") == 1
    # the walk changed (the Epic is gone): recomputed, not kept
    assert run_dod(capsys, "--refresh", gh=serve(STORY_RAW, holder(1201, "Feature", rules_desc((1, "approved")))))[0] == 0
    md2 = _ledger(repo)
    assert ledger_rules_from(md2)["ids"] == [1201] and f"\n- **Rules reviewed:** {REVIEWED}\n" in md2
    # rules switched off: no Rules from line (baseline), Rules reviewed still kept byte for byte, no walk GET
    set_config(repo)
    gh = FakeGh().on_rest("GET", path(), raw_issue())
    assert run_dod(capsys, "--refresh", gh=gh)[0] == 0 and len(gh.calls) == 1  # the issue only: no walk GET when rules are off
    md3 = _ledger(repo)
    assert "Rules from" not in md3 and f"\n- **Rules reviewed:** {REVIEWED}\n" in md3
    expected = issues.get(FakeGh().on_rest("GET", path(), raw_issue()), Ref("octo", "demo", 1001))
    assert md3.replace(f"- **Rules reviewed:** {REVIEWED}\n", "") == render_dod(expected, md3.split("Last synced:** ")[1].splitlines()[0]).replace(
        "_(fill in)_", "MY REAL PLAN", 1)


def test_write_dod_recomputes_rules_from_and_keeps_rules_reviewed(tmp_path):
    p = write_dod(_wi(), tmp_path, rules_from=[1201, 1200], rules_reviewed=REVIEWED)
    p.write_bytes(p.read_bytes().replace(b"- **Rules from:** 1201, 1200", b"- **Rules from:** 7"))  # a stale value
    write_dod(_wi(), tmp_path, refresh=True, rules_from=[1201])
    md = p.read_text(encoding="utf-8")
    assert ledger_rules_from(md)["ids"] == [1201] and f"\n- **Rules reviewed:** {REVIEWED}\n" in md and md.count("Rules reviewed") == 1


@pytest.mark.parametrize("bad", ["BR-1201-1@3f2a9c1b,BR-1200-1@0c77e2d4", "BR-1201-1@3f2a9c1b, BR-1201-1@0c77e2d4", "BR-1201-1", "BR-1-1@deadbeef0",
                                 "br-1201-1@3f2a9c1b", "BR-1201-01@3f2a9c1b", "BR-../1-1@3f2a9c1b", ""])
def test_refresh_refuses_an_invalid_rules_reviewed_line(tmp_path, bad):
    md = render_dod(_wi()).replace("\n\n## 1. ", f"\n- **Rules reviewed:** {bad}\n\n## 1. ", 1)
    p = tmp_path / "DoD_1001.md"
    p.write_bytes(md.encode("utf-8"))
    with pytest.raises(DodExists, match="Rules reviewed"):
        write_dod(_wi(), tmp_path, refresh=True)
    assert p.read_bytes().decode("utf-8") == md


REVIEWED_LINES = [f"- **Rules reviewed:** {REVIEWED}", "- **Rules reviewed:** none", "- **Rules reviewed:** BR-1201-1", "- **Rules reviewed:** ",
                  "- **Rules reviewed:** BR-1201-1@3f2a9c1b\n- **Rules reviewed:** BR-1200-1@0c77e2d4"]  # valid, none, malformed, empty, repeated


@pytest.mark.parametrize("line", REVIEWED_LINES)
def test_force_refuses_with_reviewed_line_and_leaves_ledger_untouched(repo, capsys, line):
    (repo / "dod").mkdir()
    old = render_dod(_wi(), "old").replace("\n\n## 1. ", f"\n{line}\n\n## 1. ", 1)
    p = repo / "dod" / "DoD_1001.md"
    p.write_bytes(old.encode("utf-8"))
    code, out, err = run_dod(capsys, "--force")
    assert code == 2 and "refusing to --force" in err and "ghp rules <n> --confirm" in err and "Rules reviewed" in err
    assert _ledger(repo) == old and list((repo / "dod").iterdir()) == [p]  # untouched, no temp file left
    assert "The branch history belongs to the PR author: a squash or rebase before pushing erases this trace." in err
    with pytest.raises(DodExists, match="refusing to --force"):
        write_dod(_wi(), repo / "dod", force=True)
    assert run_dod(capsys, "--refresh")[0] in (0, 2)  # --refresh is the way that keeps the line (a malformed one is refused, as before)


def test_force_refuses_a_reviewed_line_in_an_untrusted_header_and_hostile_bytes(tmp_path):
    p = tmp_path / "DoD_1001.md"
    for old in (b"# t <!--\n- **Rules reviewed:** BR-1201-1@3f2a9c1b\n\n## 1. x\n", b"# t \xff\xfe\n\n- **Rules reviewed:** BR-1201-1@3f2a9c1b\n\n## 1. x\n"):
        p.write_bytes(old)
        with pytest.raises(DodExists, match="refusing to --force"):
            write_dod(_wi(), tmp_path, force=True)
        assert p.read_bytes() == old


def test_advice_never_says_use_force_without_the_reviewed_line_caveat(tmp_path):
    """Every message that points to --force says it refuses while a `Rules reviewed:` line exists.
    (ado also asserted the README sentence; ghp has no README until T38, which re-adds that check.)"""
    p = tmp_path / "DoD_1001.md"
    base = render_dod(_wi())
    p.write_bytes(base.encode("utf-8"))
    with pytest.raises(DodExists) as e:
        write_dod(_wi(), tmp_path)
    texts = [str(e.value)]
    p.write_bytes(base.replace("## 2. Brainstorm", "<!-- ## 2. Brainstorm", 1).encode("utf-8"))  # unclosed comment outside section 1
    with pytest.raises(DodExists) as e:
        write_dod(_wi(), tmp_path, refresh=True)
    texts.append(str(e.value))
    p.write_bytes(base.replace("## 2. Brainstorm & plan", "## 2. other", 1).encode("utf-8"))  # no section 2 marker
    with pytest.raises(DodExists) as e:
        write_dod(_wi(), tmp_path, refresh=True)
    texts.append(str(e.value))
    for t in texts:
        assert "--force" in t and "Rules reviewed" in t and "removal is traced" in t, t


def test_force_without_reviewed_line_unchanged(tmp_path):
    p = tmp_path / "DoD_1001.md"
    for old in (render_dod(_wi(), "old"), render_dod(_wi(), "old", rules_from=[7]),  # no line; only Rules from
                render_dod(_wi(title="Rules reviewed: x - **Rules reviewed:** y"), "old")):  # the title mentions it
        p.write_bytes(old.encode("utf-8"))
        write_dod(_wi(), tmp_path, synced="2026-07-14", force=True)
        assert p.read_text(encoding="utf-8") == render_dod(_wi(), "2026-07-14")


HOSTILE_HDR = FORGED + "\n - **Rules from:** 9999\n```\n- **Rules from:** 9999\n```\n<!--\n- **Rules reviewed:** BR-1-1@deadbeef\n-->\n## 2. Brainstorm & plan\n- **Rules from:** 9999"


@pytest.mark.parametrize("real", [None, [1201, 1200]])
def test_section1_line_cannot_forge_header(real):
    wi = _wi(HOSTILE_HDR, before=HOSTILE_HDR, title="Points\r\n" + FORGED)
    md = render_dod(wi, rules_from=real, rules_reviewed=REVIEWED if real else "")
    head = header_block(md)
    body = head.split("\n", 1)[1]  # the title line holds the issue title (one line, so it forges nothing)
    assert "9999" not in body and "deadbeef" not in body and body.count("Rules from") == (1 if real else 0)
    assert ledger_rules_from(md) == ({"present": True, "valid": True, "ids": real} if real else {"present": False, "valid": True, "ids": []})
    assert ledger_rules_reviewed(md) == ({"present": True, "valid": True, "hashes": {"BR-1201-1": "3f2a9c1b", "BR-1200-1": "0c77e2d4"}} if real
                                         else {"present": False, "valid": True, "hashes": {}})
    assert "9999" in md  # the text is in section 1 (escaped where it matters), just never in the header block
    # header lines are also not read from fences, comments or after the first heading of a hand-edited file
    for hidden in ("```\n{}\n```", "<!--\n{}\n-->", "<!-- {} -->"):
        h = md.replace("\n\n## 1. ", "\n" + hidden.format("- **Rules from:** 9999\n- **Rules reviewed:** BR-1-1@deadbeef") + "\n\n## 1. ", 1)
        assert ledger_rules_from(h) == ledger_rules_from(md) and ledger_rules_reviewed(h) == ledger_rules_reviewed(md)
    late = "## 3. x\n- **Rules from:** 9999\n- **Rules reviewed:** BR-1-1@deadbeef\n"
    assert ledger_rules_from(md + late) == ledger_rules_from(md) and ledger_rules_reviewed(md + late) == ledger_rules_reviewed(md)


def test_a_title_naming_the_labels_is_not_a_header_line():
    md = render_dod(_wi(title="Rules from: marketing - **Rules from:** 5 Rules reviewed:"))
    assert ledger_rules_from(md)["present"] is False and ledger_rules_reviewed(md)["present"] is False


def _hdr(*lines, crlf=False):
    md = "# DoD — #1001: T\n\n- **Issue:** o/r#1001\n" + "".join(l + "\n" for l in lines) + "\n## 1. Seeded\n\n- **Rules from:** 9999\n"
    return md.replace("\n", "\r\n") if crlf else md


@pytest.mark.parametrize("value", ["../1", "1e3", "1234567890", "1201, 1200, 1100, 1000", "1201, 1201", "1201,1200", "", "none, 1201", "0", "007", "-1",
                                   "1201 1200", "１２０１", "1201,", "None", "1201 ", " 1201"])
def test_invalid_rules_from(value):
    out = ledger_rules_from(_hdr(f"- **Rules from:** {value}"))
    assert out == {"present": True, "valid": False, "ids": []}, value


def test_invalid_rules_from_forms():
    bad = {"present": True, "valid": False, "ids": []}
    assert ledger_rules_from(_hdr("- **Rules from:** 1201", "- **Rules from:** 1200")) == bad  # two lines
    assert ledger_rules_from(_hdr("- **Rules from:** 1201", "- **Rules from:** 1201")) == bad
    assert ledger_rules_from(_hdr("- **Rules from:**1201")) == bad
    assert ledger_rules_from(_hdr("- **Rules from**: 1201")) == bad
    assert ledger_rules_from(_hdr("* **rules from:** 1201")) == bad
    assert ledger_rules_from(_hdr("- **Rules from:** 1201\u2028- **Rules from:** 1200")) == bad  # a line break the file's readers also see
    assert ledger_rules_from(_hdr("<!-- never closed", "- **Rules from:** 1201")) == bad  # an unclosed opener hides the rest of the header
    assert ledger_rules_from(_hdr("- **Rules from:** 1201", "- **Rules from:** none")) == bad
    ok = ledger_rules_from(_hdr("- **Rules from:** 999999999, 1, 2"))
    assert ok == {"present": True, "valid": True, "ids": [999999999, 1, 2]}
    assert ledger_rules_from(_hdr("- **Rules from:** none")) == {"present": True, "valid": True, "ids": []}
    assert ledger_rules_from(_hdr("- **Spec dir:** Rules from: 1201")) == {"present": False, "valid": True, "ids": []}
    bad = {"present": True, "valid": False, "hashes": {}}
    for line in ("- **Rules reviewed:** BR-1201-1@3f2a9c1b, BR-1201-1@3f2a9c1b", "- **Rules reviewed:** BR-1201-1@3F2A9C1B", "- **Rules reviewed:** BR-1201-1@3f2a9c1",
                 "- **Rules reviewed:** BR-1201-1@3f2a9c1b ", "- **Rules reviewed:** BR-0-1@3f2a9c1b", "- **Rules reviewed:** BR-1201-0@3f2a9c1b",
                 "- **Rules reviewed:** BR-1201-10000@3f2a9c1b", "- **Rules reviewed:** x BR-1201-1@3f2a9c1b", "- **Rules reviewed:** BR-1201-1 deadbeef"):
        assert ledger_rules_reviewed(_hdr(line)) == bad, line
    assert ledger_rules_reviewed(_hdr("- **Rules reviewed:** BR-1201-1@3f2a9c1b", "- **Rules reviewed:** BR-1200-1@3f2a9c1b")) == bad
    assert ledger_rules_reviewed(_hdr("- **Rules reviewed:** none")) == {"present": True, "valid": True, "hashes": {}}


def test_crlf_ledger_header_values_parse():
    md = _hdr("- **Rules from:** 1201, 1200", f"- **Rules reviewed:** {REVIEWED}", crlf=True)
    assert "\r\n" in md and ledger_rules_from(md) == {"present": True, "valid": True, "ids": [1201, 1200]}
    assert ledger_rules_reviewed(md) == {"present": True, "valid": True, "hashes": {"BR-1201-1": "3f2a9c1b", "BR-1200-1": "0c77e2d4"}}
    assert "9999" not in header_block(md) and "\r" not in header_block(md)
    lone = render_dod(_wi(), rules_from=[1201], rules_reviewed=REVIEWED).replace("\n", "\r")  # old-Mac line ends
    assert ledger_rules_from(lone)["ids"] == [1201] and ledger_rules_reviewed(lone)["valid"] is True


def test_render_validates_what_it_writes():
    for bad in ([1, 1], [1, 2, 3, 4], [0], [10 ** 9], ["1201"], [True], [1.0], 5):
        with pytest.raises(ValueError, match="rules_from"):
            render_dod(_wi(), rules_from=bad)
    for bad in ("BR-1201-1", "BR-1201-1@3f2a9c1b\n- **Rules from:** 9", "x", " BR-1201-1@3f2a9c1b"):
        with pytest.raises(ValueError, match="rules_reviewed"):
            render_dod(_wi(), rules_from=[1], rules_reviewed=bad)
    assert "- **Rules from:** none\n" in render_dod(_wi(), rules_from=[])
    assert header_block(render_dod(_wi(), rules_from=[5, 4], rules_reviewed=REVIEWED)).endswith(
        f"- **Rules from:** 5, 4\n- **Rules reviewed:** {REVIEWED}\n\n")


def test_refresh_still_repairs_a_legacy_ledger_whose_title_left_a_comment_open(tmp_path):
    md = render_dod(_wi()).replace("# DoD — #1001: T", "# DoD — #1001: T <!-- legacy", 1)  # a ledger that did not escape the title
    assert dod.unclosed(md)
    out = _refresh(tmp_path, md)  # no Rules reviewed line can be read from an open header: rebuilt as before, not refused
    assert not dod.unclosed(out) and "legacy" not in out and "Rules reviewed" not in out


def test_refresh_of_a_crlf_ledger_keeps_rules_reviewed(tmp_path):
    md = render_dod(_wi(), rules_from=[7], rules_reviewed=REVIEWED).replace("\n", "\r\n")
    p = tmp_path / "DoD_1001.md"
    p.write_bytes(md.encode("utf-8"))
    write_dod(_wi(), tmp_path, refresh=True, rules_from=[1201])
    out = p.read_bytes().decode("utf-8")
    assert ledger_rules_reviewed(out)["hashes"] == {"BR-1201-1": "3f2a9c1b", "BR-1200-1": "0c77e2d4"} and ledger_rules_from(out)["ids"] == [1201]
    assert f"\n- **Rules reviewed:** {REVIEWED}\n" in out  # (write_dod reads with universal newlines: the file comes back with LF, as before)


# ---- header values are escaped; an untrusted header fails closed ----
_FORGE_DESC = "-->\n- **Rules from:** 9999\n- **Rules reviewed:** BR-1-1@deadbeef"
_REAL_FROM = {"present": True, "valid": True, "ids": [1201]}
_REAL_REV = {"present": True, "valid": True, "hashes": {"BR-1201-1": "3f2a9c1b"}}


def _header_untrusted(md):
    return not ledger_rules_from(md)["valid"]  # an untrusted header reads as present and invalid, even with no Rules line


@pytest.mark.parametrize("real", [False, True])
@pytest.mark.parametrize("where", ["state", "title", "url", "spec_dir", "synced"])
def test_an_opener_in_a_header_value_cannot_forge_the_header(where, real):
    """`<!--` in a header value plus `-->` in the issue text used to hide the real lines and let the text stand in for them."""
    opener = "x <!--"
    over = {"state": {"state": opener}, "title": {"title": opener}, "url": {"url": "https://github.com/o/r/issues/1?" + opener}}.get(where, {})
    kw = {"spec_dir": opener} if where == "spec_dir" else {"synced": opener} if where == "synced" else {}
    md = render_dod(_wi(before=_FORGE_DESC, **over), rules_from=[1201] if real else None, rules_reviewed="BR-1201-1@3f2a9c1b" if real else "", **kw)
    assert not _header_untrusted(md)
    assert "9999" not in header_block(md) and "deadbeef" not in header_block(md)
    assert ledger_rules_from(md) == (_REAL_FROM if real else {"present": False, "valid": True, "ids": []})
    assert ledger_rules_reviewed(md) == (_REAL_REV if real else {"present": False, "valid": True, "hashes": {}})
    assert "&lt;!--" in header_block(md)


def test_header_values_with_a_line_break_stay_on_one_line():
    md = render_dod(_wi(state="Active\n## x - **Rules from:** 9999", url="https://github.com/o/r/issues/1\r\n- **Rules from:** 9999"),
                    spec_dir="a - **Rules from:** 9999", rules_from=[1201])
    assert header_block(md).count("\n") == len(header_block(md).splitlines()) and ledger_rules_from(md) == _REAL_FROM
    assert "- **State:** Active ## x - **Rules from:** 9999\n" in md
    assert "- **URL:** https://github.com/o/r/issues/1 - **Rules from:** 9999\n" in md


def test_header_values_without_special_characters_are_unchanged():
    md = render_dod(_wi(), "2026-07-14", "specs/001-loyalty")
    assert header_block(md) == (
        "# DoD — #1001: T\n\n- **Issue:** octo/demo#1001\n- **URL:** https://github.com/octo/demo/issues/1001\n- **State:** open\n"
        f"- **Last synced:** 2026-07-14\n- **AC hash:** {ac_hash(_wi())}\n- **Spec dir:** specs/001-loyalty\n\n")


_LEGACY_DESC = "-->\n- **Rules reviewed:** BR-1-1@deadbeef"


def _legacy():
    """A ledger from before the escaping: the title opened `<!--` (not escaped yet) and the Description closes it."""
    wi = _wi(before=_LEGACY_DESC)
    return wi, render_dod(wi).replace("# DoD — #1001: T", "# DoD — #1001: T <!--", 1)


def test_a_comment_opened_in_the_title_and_closed_in_the_description_makes_the_header_untrusted():
    _, md = _legacy()
    assert ledger_rules_reviewed(md) == {"present": True, "valid": False, "hashes": {}}
    assert ledger_rules_from(md) == {"present": True, "valid": False, "ids": []}


def test_refresh_refuses_to_launder_a_forged_rules_reviewed_through_an_untrusted_header(tmp_path):
    wi, md = _legacy()
    p = tmp_path / "DoD_1001.md"
    p.write_bytes(md.encode("utf-8"))
    with pytest.raises(DodExists, match="Rules reviewed"):
        write_dod(wi, tmp_path, refresh=True)
    assert p.read_bytes().decode("utf-8") == md and list(tmp_path.iterdir()) == [p]


def test_cli_refresh_of_a_laundering_ledger_exits_2_with_the_ledger_untouched(repo, capsys):
    _, md = _legacy()
    (repo / "dod").mkdir()
    (repo / "dod" / "DoD_1001.md").write_bytes(md.encode("utf-8"))
    code, out, err = run_dod(capsys, "--refresh", body=_LEGACY_DESC + "\n\n## Acceptance criteria\n")
    assert code == 2 and "Rules reviewed" in err and "deadbeef" not in err
    assert _ledger(repo) == md


HIDDEN_FIRST_H = ["<!--\n## 1. S\n-->", "```\n## 1. S\n```", "~~~\n## 1. S\n~~~", "<!--\n## 1. S"]


@pytest.mark.parametrize("hide", HIDDEN_FIRST_H)
def test_a_first_heading_hidden_by_a_comment_or_fence_makes_the_header_untrusted(hide):
    md = f"# DoD — #1001: T\n\n- **Issue:** u\n- **Rules from:** 1201\n- **Rules reviewed:** {REVIEWED}\n{hide}\n## 2. Brainstorm & plan\n"
    assert ledger_rules_from(md) == {"present": True, "valid": False, "ids": []}
    assert ledger_rules_reviewed(md) == {"present": True, "valid": False, "hashes": {}}
    ok = md.replace(hide + "\n", "## 1. S\n" + hide + "\n", 1)  # a visible first heading: trusted again
    assert ledger_rules_from(ok)["ids"] == [1201] and ledger_rules_reviewed(ok)["valid"] is True


@pytest.mark.parametrize("prefix", ["> ", ">", "> > ", ">   "])
@pytest.mark.parametrize("line,reader", [("- **Rules from:** 1201", ledger_rules_from), (f"- **Rules reviewed:** {REVIEWED}", ledger_rules_reviewed)])
def test_a_blockquote_prefixed_header_line_is_invalid_not_absent(prefix, line, reader):
    out = reader(_hdr(prefix + line))
    assert out["present"] is True and out["valid"] is False


@pytest.mark.parametrize("given,recorded", [("specs/my feature", "specs/my feature"), ("specs/café", "specs/café"), ("specs/a&b", "specs/a&b"),
                                            ("specs/a <!--x", "specs/a &lt;!--x"), ("#odd", "\\#odd")])
def test_spec_dir_round_trips_and_is_stable_across_refreshes(tmp_path, given, recorded):
    p = write_dod(_wi(), tmp_path, synced="2026-07-14", spec_dir=given)
    first = p.read_text(encoding="utf-8")
    assert ledger_spec_dir(first) == recorded
    for _ in range(2):  # a refresh without --spec-dir re-reads the recorded value and writes it back unchanged
        write_dod(_wi(), tmp_path, synced="2026-07-14", refresh=True)
        assert p.read_text(encoding="utf-8") == first and ledger_spec_dir(first) == recorded
    assert "<!--" not in first and (recorded == given or ledger_spec_dir(first) != given)  # an escaped one is not the given path: fails closed


# ============================ T11 AC1: the AC is copied verbatim ============================
def test_ac_is_copied_verbatim(repo, capsys):
    # the shared fixture: CRLF body, a non-ASCII AC line, blank lines after it
    gh = FakeGh().on_rest("GET", path(1), json.loads((FIX / "issue_story.json").read_text(encoding="utf-8")))
    code, out, err = run_dod(capsys, n=1, gh=gh)
    assert code == 0 and err == ""
    md = _ledger(repo, 1)
    assert "\n1. AC1 café ☕\n" in md and ac_lines(md) == ["AC1 café ☕"]
    # the exact bytes of every line between the heading and the next `## ` heading: terminators (CRLF and LF), trailing blanks, tabs, nesting, blank lines
    ac = ("- first  \r\n"                      # two trailing blanks (a Markdown line break)
          "\r\n"
          "    - nested\twith a tab\r\n"
          "2. café ☕ — “quoted” | x\n"         # a bare LF line among CRLF ones
          "   * odd   indent   \r\n"
          "- [ ] task item\r\n"
          "- " + "a long criterion that lifts the section over the thin floor, " * 3)
    body = "# Story\r\n\r\nintro\r\n\r\n## Acceptance criteria\r\n" + ac + "\r\n\r\n## Notes\r\nafter\r\n"
    code, out, err = run_dod(capsys, "--force", body=body)
    assert code == 0 and err == ""
    md = _ledger(repo)
    assert "### Acceptance criteria\n" + ac + "\n\n### Description\n" in md          # the lines, byte for byte, between the ledger's two headings
    assert ac_lines(md)[:5] == ["first", "nested\twith a tab", "café ☕ — “quoted” | x", "odd   indent", "[ ] task item"]
    desc = md.split("### Description\n", 1)[1]
    assert "after" in desc and "intro" in desc and "first" not in desc  # the text outside the section is the Description, not AC


def test_ac_section_runs_to_the_next_h2_or_the_end_and_subheadings_stay_inside(repo, capsys):
    pad = "- " + "p" * 150 + "\n"  # over the thin floor, so no banner sits in the block
    body = "## Acceptance criteria\n" + pad + "- a\n### Scenario one\n- b\n#### deeper\n- c\n## Out of scope\nnot ac\n"
    assert run_dod(capsys, body=body)[0] == 0
    md = _ledger(repo)
    ac_part = md.split("### Acceptance criteria\n", 1)[1].split("\n### Description\n", 1)[0]
    assert ac_part == pad + "- a\n\\### Scenario one\n- b\n\\#### deeper\n- c\n"  # sub-headings: kept, escaped so they cannot forge a ledger heading
    assert "not ac" not in ac_part and "not ac" in md
    assert ac_lines(md) == ["p" * 150, "a", "### Scenario one", "b", "#### deeper", "c"]       # the escape is undone when the AC is read back
    assert ac_hash(_wi(body=body)) == hashlib.sha256((pad + "- a\n### Scenario one\n- b\n#### deeper\n- c").encode()).hexdigest()


def test_ac_heading_with_trailing_blanks_matches_and_lookalikes_do_not(repo, capsys):
    assert run_dod(capsys, body="## Acceptance criteria \t \r\n- x\r\n")[0] == 0 and "- x" in _ledger(repo)
    for n, bad in enumerate(["### Acceptance criteria\n- x", "## Acceptance Criteria\n- x", "## acceptance criteria\n- x", "# Acceptance criteria\n- x",
                             "## Acceptance criteria:\n- x", " ## Acceptance criteria\n- x", "Acceptance criteria\n===\n- x", "Acceptance criteria\n---\n- x",
                             "## Acceptance criteria ##\n- x", "## Acceptance  criteria\n- x", "##Acceptance criteria\n- x", "## Acceptance criteriaX\n- x"]):
        code, out, err = run_dod(capsys, "--force", body=bad)
        assert code == 2 and "E_AC_MISSING" in err, bad


# ============================ T11 AC2: the header ============================
def test_header_has_canonical_ref_and_url(repo, capsys):
    code, out, err = run_dod(capsys, title="Opening hours page")
    assert code == 0
    md = _ledger(repo)
    head = header_block(md)
    assert head.splitlines()[0] == "# DoD — #1001: Opening hours page"
    assert "- **Issue:** octo/demo#1001\n" in head
    assert "- **URL:** https://github.com/octo/demo/issues/1001\n" in head
    assert "System." not in md and "Microsoft.VSTS" not in md and "dev.azure.com" not in md and "ADO" not in md
    assert ledger_ac_hash(md) == hashlib.sha256(b"").hexdigest()
    assert json.loads(out) == {"ref": "octo/demo#1001", "path": "dod/DoD_1001.md", "action": "created", "ac_chars": 0, "ac_thin": True}


# ============================ T11 AC3: the heading rules ============================
def _nothing_written(repo):
    return not (repo / "dod").exists()


def test_missing_ac_heading_writes_nothing(repo, capsys):
    for body in ("", "No criteria here.\n", "# Story\n\n- a\n", "## Description\nx\n"):
        code, out, err = run_dod(capsys, body=body)
        assert code == 2 and err.startswith("error: E_AC_MISSING") and err.count("\n") == 1 and out == ""
        assert _nothing_written(repo)
    # an existing ledger is neither refreshed, forced nor touched
    (repo / "dod").mkdir()
    old = render_dod(_wi("old"), "old")
    (repo / "dod" / "DoD_1001.md").write_bytes(old.encode("utf-8"))
    for flag in ([], ["--refresh"], ["--force"]):
        code, out, err = run_dod(capsys, *flag, body="no heading")
        assert code == 2 and "E_AC_MISSING" in err
        assert _ledger(repo) == old and [p.name for p in (repo / "dod").iterdir()] == ["DoD_1001.md"]


def test_duplicate_ac_heading_writes_nothing(repo, capsys):
    body = "## Acceptance criteria\n- one\n\n## Acceptance criteria\n- two\n"
    code, out, err = run_dod(capsys, body=body)
    assert code == 2 and err.startswith("error: E_AC_DUPLICATE") and err.count("\n") == 1 and out == ""
    assert "- one" not in err and "- two" not in err  # the issue's text is never echoed
    assert _nothing_written(repo)
    (repo / "dod").mkdir()
    old = render_dod(_wi("old"), "old")
    (repo / "dod" / "DoD_1001.md").write_bytes(old.encode("utf-8"))
    for flag in ([], ["--refresh"], ["--force"]):
        assert run_dod(capsys, *flag, body=body)[0] == 2 and _ledger(repo) == old
    with pytest.raises(AcError) as e:
        render_dod(_wi(body=body))
    assert e.value.code == "E_AC_DUPLICATE" and isinstance(e.value, ValueError)
    with pytest.raises(AcError):
        write_dod(_wi(body=body), repo / "elsewhere")
    assert not (repo / "elsewhere").exists()
    # two real headings far apart, one with trailing blanks: still two
    assert run_dod(capsys, "--force", body="## Acceptance criteria\n- a\n## Notes\nx\n## Acceptance criteria  \n- b")[2].startswith("error: E_AC_DUPLICATE")


def test_commented_out_ac_heading_is_ignored(repo, capsys):
    # the only heading is inside an HTML comment: it does not count, so the heading is missing
    for body in ("<!-- ## Acceptance criteria\n- x\n-->\n", "<!--\n## Acceptance criteria\n- x\n-->", "a <!-- ## Acceptance criteria --> b\n- x",
                 "<!-- ## Acceptance criteria\n- x"):  # the last one never closes
        code, out, err = run_dod(capsys, body=body)
        assert code == 2 and err.startswith("error: E_AC_MISSING") and out == "", body
        assert _nothing_written(repo)
    # a commented-out heading next to the real one does not make a duplicate, and its text is not the AC
    body = "<!-- ## Acceptance criteria\n- hidden\n-->\n\n## Acceptance criteria\n- real\n"
    code, out, err = run_dod(capsys, body=body)
    assert code == 0 and err == ""
    md = _ledger(repo)
    ac_part = md.split("### Acceptance criteria\n", 1)[1].split("\n### Description\n", 1)[0]
    assert ac_part.endswith("- real\n") and "hidden" not in ac_part
    assert ac_lines(md) == ["real"] and "&lt;!-- ## Acceptance criteria" in md.split("### Description\n", 1)[1]  # the comment is kept as inert text


def test_a_heading_inside_a_code_fence_is_not_a_heading(repo, capsys):
    """Decision: a fenced `## Acceptance criteria` is code, like a commented one. Only fence-only bodies are 'missing'."""
    for body in ("```\n## Acceptance criteria\n- x\n```\n", "~~~md\n## Acceptance criteria\n~~~\n", "```\n## Acceptance criteria\n- never closed"):
        code, out, err = run_dod(capsys, body=body)
        assert code == 2 and "E_AC_MISSING" in err
        assert _nothing_written(repo)
    code, out, err = run_dod(capsys, body="```\n## Acceptance criteria\n- fake\n```\n\n## Acceptance criteria\n- real\n")
    assert code == 0 and ac_lines(_ledger(repo)) == ["real"]
    # a `## ` line inside a fence inside the AC section does not end the section
    code, out, err = run_dod(capsys, "--force", body="## Acceptance criteria\n- a\n```\n## not a heading\n```\n- b\n## Next\nx\n")
    assert code == 0 and ac_lines(_ledger(repo)) == ["a", "## not a heading", "b"]  # the fence delimiters are no criteria, the code line is


def test_empty_ac_section_is_not_a_missing_heading(repo, capsys):
    code, out, err = run_dod(capsys, body="## Acceptance criteria\n\n\n## Next\nx\n")
    assert code == 0
    assert "_(none in the issue — improve it)_" in _ledger(repo) and json.loads(out)["ac_thin"] is True


# ============================ T11: the issue is untrusted text ============================
def test_a_pull_request_number_is_refused_and_nothing_is_written(repo, capsys):
    code, out, err = run_dod(capsys, **{"pull_request": {"url": "x"}})
    assert code == 2 and "E_NOT_AN_ISSUE" in err and _nothing_written(repo)


def test_a_failed_read_writes_nothing(repo, capsys):
    from ghp.gh import GhError
    gh = FakeGh().on_rest("GET", path(), lambda body: (_ for _ in ()).throw(GhError("E_GH_HTTP", 404, "Not Found")))
    code, out, err = run_dod(capsys, gh=gh)
    assert code == 1 and err.startswith("error: ") and _nothing_written(repo)


def test_the_ledger_path_is_built_only_from_the_validated_number(repo, capsys, monkeypatch):
    # nothing but a positive integer reaches the file name; the other forms are refused before any call
    for bad in ("../../x", "1001/../../x", "0", "-1", "1001; rm", "DoD_1001.md", "../1001", "1e3", ""):
        gh = FakeGh()
        assert cli_main.main(["dod", bad], gh=gh) == 2 and gh.calls == [] and _nothing_written(repo), bad
        capsys.readouterr()
    # the issue number the server returns must be the requested one (issues.get); its title and body never reach the path
    assert run_dod(capsys, title="../../evil/../x", body="## Acceptance criteria\n- ../../x\n")[0] == 0
    assert sorted(p.name for p in repo.rglob("*") if p.is_file() and p.name != "ghp.config.json") == ["DoD_1001.md"]
    assert dod_file("dod", 7) == Path("dod/DoD_7.md")


def test_the_ledger_stays_inside_the_repo_dod_dir(repo, capsys, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    for bad in ("../out", str(outside), "a/../../out"):
        set_config(repo, dod_path=bad)
        code, out, err = run_dod(capsys)
        assert code == 2 and "dod" in err and not any(outside.iterdir()), bad
        assert not (repo.parent / "out").exists()
    set_config(repo)
    for bad in ("../out", str(outside)):
        assert run_dod(capsys, "--dir", bad)[0] == 2 and not any(outside.iterdir())
    (repo / "link").symlink_to(outside, target_is_directory=True)  # a symlinked directory that leaves the repo
    assert run_dod(capsys, "--dir", "link")[0] == 2 and not any(outside.iterdir())
    set_config(repo, dod_path="docs/ledger")
    assert run_dod(capsys)[0] == 0 and (repo / "docs" / "ledger" / "DoD_1001.md").is_file()
    assert run_dod(capsys, "--force", "--dir", "other")[0] == 0 and (repo / "other" / "DoD_1001.md").is_file()


def test_no_terminal_escape_reaches_the_output(repo, capsys):
    esc = "\x1b[31mred\x1b]0;pwn\x07"
    code, out, err = run_dod(capsys, title="T " + esc, body="## Acceptance criteria\n- " + esc + "\n")
    assert code == 0 and "\x1b" not in out + err and "\x07" not in out + err
    assert "\x1b" not in header_block(_ledger(repo))                    # the title, a header value, is cleaned in the file
    for body in ("x " + esc, "## Acceptance criteria\n" + esc + "\n## Acceptance criteria\n"):
        code, out, err = run_dod(capsys, "--force", title="T " + esc, body=body)
        assert code == 2 and "\x1b" not in out + err and "\x07" not in out + err


def test_a_hostile_body_is_read_in_linear_time(repo, capsys):
    """GitHub bodies run to 65,536 characters: long runs of openers, fences and blanks must not make the scan quadratic."""
    for hostile in ("<!--" * 16384, "`" * 65536, "\n" * 65536, "<" * 65536, " " * 65536, "```\n" * 16384, "## Acceptance criteria\n" + "<!--" * 16000, "<!-- -->" * 8000, "## Acceptance criteria\n" + "a <!-- b --> c\n" * 4000):
        t = time.monotonic()
        run_dod(capsys, "--force", body=hostile)
        assert time.monotonic() - t < 2.0


# ============================ T11b: linear ledger label match (ReDoS) ============================
# `_look(label)` decides which header lines "name" a label in any spelling. The accepted set is pinned here BEFORE the matcher was made linear.
_LABEL_TABLE = [
    # (line, label, accepted)
    ("Rules from", "Rules from", True), ("rules from", "Rules from", True), ("RULES FROM", "Rules from", True), ("Rules  from", "Rules from", True),
    ("Rules\tfrom", "Rules from", True), ("Rules from", "Rules from", True), ("Rules from:", "Rules from", True), ("Rules from-x", "Rules from", True),
    ("- **Rules from:** 1201", "Rules from", True), ("-**Rules from:**", "Rules from", True), ("- ** Rules from", "Rules from", True),
    ("  - **Rules from:** x", "Rules from", True), ("\t* Rules from", "Rules from", True), ("+ Rules from", "Rules from", True),
    ("1. Rules from", "Rules from", True), ("10) Rules from", "Rules from", True), ("1.Rules from", "Rules from", True),
    ("> Rules from", "Rules from", True), (">Rules from", "Rules from", True), (">> Rules from", "Rules from", True), ("> > **Rules from**", "Rules from", True),
    ("> - **Rules from:**", "Rules from", True), (">- Rules from", "Rules from", True), ("> 1. Rules from", "Rules from", True),
    ("_Rules from", "Rules from", True), ("_Rules from_", "Rules from", False), ("*_Rules from", "Rules from", True), ("- _*_ Rules from", "Rules from", True), (" Rules from", "Rules from", True),
    (" \t > \t - \t ** \t Rules from", "Rules from", True),
    ("**Rules reviewed:** x", "Rules reviewed", True), ("- Rules   reviewed", "Rules reviewed", True),
    ("Rules reviewed", "Rules from", False), ("Rules fromage", "Rules from", False), ("Rules from2", "Rules from", False), ("Rules from_x", "Rules from", False),
    ("RulesFrom", "Rules from", False), ("Rules", "Rules from", False), ("", "Rules from", False), ("   ", "Rules from", False),
    ("# Rules from", "Rules from", False), ("x Rules from", "Rules from", False), ("- - Rules from", "Rules from", False), ("** - Rules from", "Rules from", False),
    ("1.2. Rules from", "Rules from", False), ("-1. Rules from", "Rules from", False), ("`Rules from`", "Rules from", False), ("<b>Rules from", "Rules from", False),
    ("- [x] Rules from", "Rules from", False), ("- **Spec dir:** Rules from", "Rules from", False), ("a > Rules from", "Rules from", False),
    ("- **Rules from", "Rules reviewed", False),
]


@pytest.mark.parametrize("line,label,accepted", _LABEL_TABLE)
def test_the_label_matcher_accepts_exactly_the_pinned_spellings(line, label, accepted):
    assert bool(dod._look(label).match(line)) is accepted


HOSTILE_N = 200_000
_HALF = HOSTILE_N // 2
# one hostile line in the header block of a ledger (the ledger is PR-controlled); each shape ends in a character that makes the match fail late
HOSTILE_LINES = {
    "blanks": " " * HOSTILE_N, "blanks+x": " " * HOSTILE_N + "x", "space-tab": " \t" * _HALF, "space-tab+x": " \t" * _HALF + "x",
    "dash-space": "- " * _HALF, "hashes": "#" * HOSTILE_N, "quotes": "> " * _HALF, "quote+blanks+x": ">" + " " * HOSTILE_N + "x", "stars": "*" * HOSTILE_N,
    "star-underscore": "*_" * _HALF, "numbers": "1. " * (HOSTILE_N // 3), "dash-star": "- * " * (HOSTILE_N // 4),
    "dash,blanks,star,blanks,x": "-" + " " * _HALF + "*" + " " * _HALF + "x", "bold-open,blanks": "- ** " + " " * _HALF + "Rules from" + " " * _HALF + "x",
    "label+blanks+x": "Rules from" + " " * HOSTILE_N + "x", "label,tabs,x": "Rules" + " \t" * _HALF + "x", "bold label+blanks+x": "- **Rules from" + " " * HOSTILE_N + "x",
    "bold label:+blanks+x": "- **Rules from:**" + " " * HOSTILE_N + "x", "bold value,blanks,y": "- **Rules from:** a" + " " * HOSTILE_N + "y",
    "bold reviewed+blanks+x": "- **Rules reviewed:**" + " \t" * _HALF + "x", "bold bold": "**" * _HALF,
}
FAST = 1.0


def _took(fn, *a):
    t = time.monotonic()
    fn(*a)
    return time.monotonic() - t


@pytest.mark.parametrize("shape", HOSTILE_LINES)
def test_header_readers_are_linear_on_a_hostile_header_line(shape):
    md = "# DoD\n\n- **Issue:** u\n" + HOSTILE_LINES[shape] + "\n\n## 1. Seeded\n"
    for fn, args in ((ledger_rules_from, ()), (dod.ledger_rules_reviewed, ()), (dod._labelled, ("Rules from",)), (dod._labelled, ("Rules reviewed",))):
        assert _took(fn, md, *args) < FAST, (fn.__name__, shape)
    assert _took(dod._reviewed_line, md) < FAST
    untrusted = "# DoD\n<!--\n" + HOSTILE_LINES[shape] + "\n## 1. S\n"  # the header is untrusted: _reviewed_line scans every raw line
    assert _took(dod._reviewed_line, untrusted) < FAST


@pytest.mark.parametrize("shape", HOSTILE_LINES)
def test_ledger_value_readers_are_linear_on_a_hostile_value(shape):
    for label, fn in (("AC hash", ledger_ac_hash), ("Spec dir", ledger_spec_dir)):
        assert _took(fn, f"- **{label}:** x" + HOSTILE_LINES[shape] + "\n") < FAST, (label, shape)
        assert _took(fn, f"- **{label}:** " + HOSTILE_LINES[shape] + "\n") < FAST, (label, shape)


@pytest.mark.parametrize("shape", HOSTILE_LINES)
def test_the_ac_scan_and_ac_lines_are_linear_on_a_hostile_line(shape):
    line = HOSTILE_LINES[shape]
    wi = _wi(body="## Acceptance criteria\n" + line + "\n- last")
    assert _took(dod._ac_section, wi.body) < FAST
    assert _took(ac_hash, wi) < FAST and _took(ac_stats, wi) < FAST
    md = render_dod(wi)
    assert _took(ac_lines, md) < FAST
    assert _took(ac_lines, "### Acceptance criteria\n" + line + "\n" + SECTION2 + "\n") < FAST  # a hand-written ledger
    assert _took(render_dod, _wi(body=line + "\n\n## Acceptance criteria\n- x")) < FAST  # the same line as Description text


def test_the_label_matcher_does_not_change_the_ledger_readers():
    md = "# DoD\n\n- **Issue:** u\n- **Rules from:** 1201, 1200\n- **Rules reviewed:** none\n\n## 1. S\n"
    assert ledger_rules_from(md) == {"present": True, "valid": True, "ids": [1201, 1200]}
    assert dod.ledger_rules_reviewed(md) == {"present": True, "valid": True, "hashes": {}}
    for bad in ("  - **Rules from:** 1201", "> - **Rules from:** 1201", "- **rules from:** 1201", "- **Rules from:**  1201", "- **Rules from:** 1201 "):
        assert ledger_rules_from(md.replace("- **Rules from:** 1201, 1200", bad))["valid"] is False
    assert ledger_ac_hash("- **AC hash:**   abc \t \n") == "abc" and ledger_spec_dir("- **Spec dir:** \t a  b \t\n") == "a  b"
    assert ledger_ac_hash("- **AC hash:**   \n") is None and ledger_ac_hash("- **AC hash:** x\r\n") == "x\r"  # as before: `.` takes a `\r`


def test_repair_is_linear_on_many_section_3_headings(tmp_path):
    md = "<!--\n" + SECTION2 + "\n" + "## 3.\n" * (HOSTILE_N // 6)  # an opener in §1, a real §2, §3 headings without a §4
    assert _took(dod._repair_at, md) < FAST
    p = tmp_path / "DoD_1001.md"
    p.write_bytes(md.encode("utf-8"))
    t = time.monotonic()
    with pytest.raises(DodExists):
        write_dod(_wi(), tmp_path, refresh=True)
    assert time.monotonic() - t < FAST and p.read_bytes() == md.encode("utf-8")


@pytest.mark.parametrize("hostile", ["<!-- -->" * (HOSTILE_N // 8), "<!--" * (HOSTILE_N // 4), "`" * HOSTILE_N, "x `" * (HOSTILE_N // 3), "```\n" * (HOSTILE_N // 4)])
def test_the_fence_and_comment_scan_is_linear(hostile):
    assert _took(dod._scan, hostile, "") < FAST and _took(dod._mask, hostile) < FAST
    assert _took(ac_lines, "### Acceptance criteria\n" + hostile + "\n") < FAST


# ============================ T11b: --refresh never splices at the wrong `## 2.` ============================
def _extra_marker_in_section_4(md, text=None):
    return md.replace("_(none yet)_", "_(none yet)_\n" + (text or SECTION2 + "  quoted by a reviewer SECRETLEDGERTEXT"), 1)


def _authored_ledger(tmp_path, **fields):
    p = write_dod(_wi("old AC", **fields), tmp_path, synced="d1")
    p.write_bytes(p.read_bytes().decode("utf-8").replace("_(fill in)_", "MY PLAN", 1).replace("| _(what it must do)_ |", "| section 3 kept |", 1).encode("utf-8"))
    return p


def test_a_second_visible_section_2_heading_makes_refresh_refuse_and_leaves_the_file_alone(tmp_path):
    p = _authored_ledger(tmp_path)
    md = _extra_marker_in_section_4(p.read_text(encoding="utf-8"))
    p.write_bytes(md.encode("utf-8"))
    with pytest.raises(DodExists, match="E_LEDGER_AMBIGUOUS_SECTION2") as e:
        write_dod(_wi("new AC"), tmp_path, synced="d2", refresh=True)
    assert "SECRETLEDGERTEXT" not in str(e.value) and "\n" not in str(e.value) and "--force" in str(e.value)
    assert p.read_bytes() == md.encode("utf-8") and not list(tmp_path.glob("*.tmp"))


def test_cli_refresh_with_a_second_section_2_heading_exits_2_with_one_line(repo, capsys):
    run_dod(capsys, body="## Acceptance criteria\nold\n")
    p = repo / "dod" / "DoD_1001.md"
    md = _extra_marker_in_section_4(p.read_text(encoding="utf-8"))
    p.write_bytes(md.encode("utf-8"))
    code, out, err = run_dod(capsys, "--refresh", body="## Acceptance criteria\nnew\n")
    assert code == 2 and out == "" and err.count("\n") == 1 and "E_LEDGER_AMBIGUOUS_SECTION2" in err and "SECRETLEDGERTEXT" not in err
    assert _ledger(repo) == md


def test_force_still_rewrites_a_ledger_with_two_section_2_headings(tmp_path):
    p = _authored_ledger(tmp_path)
    p.write_bytes(_extra_marker_in_section_4(p.read_text(encoding="utf-8")).encode("utf-8"))
    write_dod(_wi("new AC"), tmp_path, synced="d2", force=True)
    out = p.read_text(encoding="utf-8")
    assert "new AC" in out and "MY PLAN" not in out and out.count(SECTION2) == 1


def test_one_visible_marker_still_splices_and_keeps_sections_2_to_5_byte_for_byte(tmp_path):
    p = _authored_ledger(tmp_path)
    text = p.read_bytes().decode("utf-8")
    write_dod(_wi("new AC"), tmp_path, synced="d2", refresh=True)
    out = p.read_bytes().decode("utf-8")
    assert out[out.index("\n" + SECTION2):] == text[text.index("\n" + SECTION2):] and "MY PLAN" in out and "section 3 kept" in out
    assert "new AC" in out and "old AC" not in out
    p.write_bytes(out.replace("\n", "\r\n").encode("utf-8"))  # a CRLF checkout of the same ledger
    write_dod(_wi("newer AC"), tmp_path, synced="d3", refresh=True)
    assert "MY PLAN" in p.read_text(encoding="utf-8")


@pytest.mark.parametrize("text", ["\\" + SECTION2, "> " + SECTION2, "  " + SECTION2, "see `" + SECTION2 + "` above", "x " + SECTION2])
def test_text_that_only_contains_the_marker_is_no_second_heading(tmp_path, text):
    p = _authored_ledger(tmp_path)
    p.write_bytes(_extra_marker_in_section_4(p.read_text(encoding="utf-8"), text).encode("utf-8"))
    write_dod(_wi("new AC"), tmp_path, synced="d2", refresh=True)
    assert "new AC" in p.read_text(encoding="utf-8") and "MY PLAN" in p.read_text(encoding="utf-8")


def test_no_marker_keeps_todays_refusal_and_the_file_untouched(tmp_path):
    p = tmp_path / "DoD_1001.md"
    p.write_text("no heading\nMY WORK", encoding="utf-8")
    with pytest.raises(DodExists, match="missing the") as e:
        write_dod(_wi(), tmp_path, refresh=True)
    assert "E_LEDGER_AMBIGUOUS_SECTION2" not in str(e.value) and p.read_text(encoding="utf-8") == "no heading\nMY WORK"


def test_a_forged_heading_copied_into_section_1_is_refused_not_spliced(tmp_path):
    # an old ledger whose §1 holds an unescaped `## 2.` line (before the issue text was escaped): two visible headings, so refresh cannot tell which is real
    p = _authored_ledger(tmp_path)
    md = p.read_text(encoding="utf-8").replace("### Description\n_(none)_", "### Description\n" + SECTION2 + "\nFAKE PLAN", 1)
    p.write_bytes(md.encode("utf-8"))
    assert md.count("\n" + SECTION2) == 2
    with pytest.raises(DodExists, match="E_LEDGER_AMBIGUOUS_SECTION2"):
        write_dod(_wi("new AC"), tmp_path, refresh=True)
    assert p.read_bytes() == md.encode("utf-8")


def test_a_fresh_render_has_exactly_one_visible_section_2_heading_whatever_the_issue_says(tmp_path):
    evil = SECTION2 + "\n  " + SECTION2 + "\n- " + SECTION2 + "\n" + SECTION2 + " " + SECTION2
    wi = _wi("```\n" + SECTION2 + "\n```\n" + SECTION2 + "\n- " + SECTION2, title=SECTION2 + "\r\n" + SECTION2, before=evil, after=SECTION2 + "\n" + evil, url="u\n" + SECTION2)
    md = render_dod(wi, synced=SECTION2 + "\n" + SECTION2, spec_dir=SECTION2)
    assert len(dod._SECTION2_RE.findall(dod._mask(md))) == 1 and len([l for l in md.splitlines() if l.startswith(SECTION2)]) == 1
    p = write_dod(wi, tmp_path, synced="d1", spec_dir=SECTION2)
    p.write_bytes(p.read_bytes().decode("utf-8").replace("_(fill in)_", "MY PLAN", 1).encode("utf-8"))
    write_dod(_wi("round trip"), tmp_path, synced="d2", refresh=True)  # a fresh write then a refresh round-trips
    assert "MY PLAN" in p.read_text(encoding="utf-8") and "round trip" in p.read_text(encoding="utf-8")
    write_dod(wi, tmp_path, synced="d3", refresh=True)
    assert "MY PLAN" in p.read_text(encoding="utf-8")


# ============================ T11c: the readers fail closed on an ambiguous section 2 ============================
AMBIG = "E_LEDGER_AMBIGUOUS_SECTION2"


def _two_markers(text=None):
    """A real ledger plus a second visible `## 2.` heading in §4 (a human quoting it): which one starts the authored sections cannot be told."""
    return _extra_marker_in_section_4(_authored(), text)


def _readers(md):
    return {"span2": lambda: dod.section_span(md, 2), "span3": lambda: dod.section_span(md, 3), "span4": lambda: dod.section_span(md, 4),
            "section2": lambda: dod.section(md, 2), "section3": lambda: dod.section(md, 3), "section4": lambda: dod.section(md, 4),
            "section5": lambda: dod.section(md, 5), "ac_lines": lambda: ac_lines(md)}


@pytest.mark.parametrize("reader", _readers("").keys())
def test_every_ledger_reader_raises_on_a_second_visible_section_2_heading(reader):
    md = _two_markers()
    with pytest.raises(dod.LedgerError) as e:
        _readers(md)[reader]()
    assert e.value.code == AMBIG and AMBIG in str(e.value) and "SECRETLEDGERTEXT" not in str(e.value) and "\n" not in str(e.value)
    assert isinstance(e.value, ValueError)  # exit 2 in the CLI


@pytest.mark.parametrize("reader", _readers("").keys())
def test_a_forged_heading_in_section_1_is_ambiguous_for_every_reader(reader):
    md = render_dod(_wi("an AC")).replace("### Description\n_(none)_", "### Description\n" + SECTION2 + "\nFAKE PLAN", 1)
    assert md.count("\n" + SECTION2) == 2
    with pytest.raises(dod.LedgerError, match=AMBIG):
        _readers(md)[reader]()


@pytest.mark.parametrize("reader", _readers("").keys())
@pytest.mark.parametrize("hidden", ["```\n{m}\n```\n", "~~~\n{m}\n~~~\n", "<!--\n{m}\n-->\n", "> {m}\n", "  {m}\n", "\\{m}\n", "text {m}\n", "<!-- c --> {m}\n"])
def test_a_marker_that_is_not_visible_is_not_counted(reader, hidden):
    md = _authored().replace("_(none yet)_", "_(none yet)_\n" + hidden.format(m=SECTION2), 1)
    _readers(md)[reader]()  # no LedgerError
    assert "MY REAL PLAN" in dod.section(md, 2) and dod.section(md, 4) is not None and ac_lines(md) == []


def test_one_marker_reads_exactly_as_before():
    md = _authored()
    assert "MY REAL PLAN" in dod.section(md, 2) and "every AC mapped" in dod.section(md, 5)
    assert dod.section_span(md, 4) and ac_lines(render_dod(_wi("an AC"))) == ["an AC"]
    assert dod.section(md.replace(SECTION2, "no marker here", 1), 2) is None  # absent stays None, not an error


def test_the_writer_helper_still_takes_the_last_marker_and_refuse_logic_is_shared():
    md = _two_markers()
    assert dod._section2_at(md) > dod._section2_at(_authored())  # unchanged: write_dod's own check refuses before using it
    with pytest.raises(dod.LedgerError, match=AMBIG):
        dod._section2_one(md)


def test_ac_stats_reads_the_issue_not_the_ledger_and_is_unchanged():
    assert ac_stats(_wi("an AC"))["ac_chars"] == len("an AC")


def test_questions_parse_raises_not_no_questions_on_an_ambiguous_ledger():
    from ghp.questions import parse_questions
    with pytest.raises(dod.LedgerError, match=AMBIG):
        parse_questions(_two_markers())


def test_rules_tags_raises_not_no_rows_on_an_ambiguous_ledger():
    from ghp import rules
    with pytest.raises(dod.LedgerError, match=AMBIG):
        rules.tags(_two_markers())


def test_ghp_questions_refuses_an_ambiguous_ledger_with_one_line_and_exit_2(repo, capsys):
    run_dod(capsys, body="## Acceptance criteria\nold\n")
    p = repo / "dod" / "DoD_1001.md"
    p.write_bytes(_extra_marker_in_section_4(p.read_text(encoding="utf-8")).encode("utf-8"))
    code = cli_main.main(["questions", "1001"], gh=FakeGh())
    cap = capsys.readouterr()
    assert code == 2 and cap.out == "" and cap.err.count("\n") == 1 and AMBIG in cap.err and "SECRETLEDGERTEXT" not in cap.err


def test_ghp_rules_confirm_refuses_an_ambiguous_ledger_and_writes_nothing(repo, capsys):
    write_dod(_wi("old"), repo / "dod", synced="d1", rules_from=[])
    p = repo / "dod" / "DoD_1001.md"
    good = p.read_bytes()
    assert cli_main.main(["rules", "1001", "--confirm"], gh=FakeGh()) == 0  # a one-marker ledger confirms (the control)
    capsys.readouterr()
    p.write_bytes(_extra_marker_in_section_4(good.decode("utf-8")).encode("utf-8"))
    before = p.read_bytes()
    code = cli_main.main(["rules", "1001", "--confirm"], gh=FakeGh())
    cap = capsys.readouterr()
    assert code == 2 and cap.out == "" and cap.err.count("\n") == 1 and AMBIG in cap.err and "SECRETLEDGERTEXT" not in cap.err
    assert p.read_bytes() == before and not list((repo / "dod").glob("*.tmp"))


def test_ghp_dod_refresh_still_refuses_with_the_same_code(repo, capsys):
    run_dod(capsys, body="## Acceptance criteria\nold\n")
    p = repo / "dod" / "DoD_1001.md"
    p.write_bytes(_extra_marker_in_section_4(p.read_text(encoding="utf-8")).encode("utf-8"))
    code, out, err = run_dod(capsys, "--refresh", body="## Acceptance criteria\nnew\n")
    assert code == 2 and AMBIG in err


# ---- T11c AC4: linear time ----
def _ambiguous_big(n_markers):
    return "# DoD\n\n" + (SECTION2 + "\n") * n_markers + "## 3. AC\n"


def test_the_readers_are_linear_on_many_marker_lines():
    md = _ambiguous_big(13_000)  # 300,000 characters, 13,000 visible markers: refused after one scan
    assert len(md) > 299_000
    for fn in (lambda: dod.section(md, 2), lambda: dod.section_span(md, 4), lambda: ac_lines(md)):
        t = time.monotonic()
        with pytest.raises(dod.LedgerError):
            fn()
        assert time.monotonic() - t < FAST


@pytest.mark.parametrize("shape", ["short", "headings", "hashes", "glued", "unclosed"])
def test_the_readers_are_linear_on_300k_chars_of_heading_like_lines(shape):
    md = {"short": "## 2.\n" * 50_000, "headings": "## \n" * 100_000, "hashes": "#" * 300_000, "glued": SECTION2 * 13_000,
          "unclosed": "```\n" + "## 2. Brainstorm & plan\n" * 13_000}[shape]
    for fn in (lambda: dod.section(md, 2), lambda: dod.section(md, 5), lambda: ac_lines(md), lambda: dod.section_span(md, 3)):
        assert _took(fn) < FAST
