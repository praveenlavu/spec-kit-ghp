"""T16 tests: `decisions` (list, add, post, resolve; `--sync` is a no-op until T28), ported from ado tests/test_decisions.py (rename table; tracker seam: a work
item became an issue, ado's FakeClient became the FakeGh thread of test_questions, the comment is Markdown with a marker line and the post is idempotent by
it). Not ported (owners): the whole `--sync` half (`sync`, `apply_sync`, `posting_index`, `superseded` and their ~35 tests, the sync verb tests) -> T28 (with
test_newest_posting); `test_resolve_refused_when_approvers_set` -> T28 (the target-branch approvers guard needs the comment mode); `test_dod_compiles_without_a_warning`
is kept. New: the post through `comments.add` (marker, adopt, re-read after a write that may have applied, 4xx, 60,000), the terminal-mode `--sync` note,
the hostile-input timings of the grammar regexes."""
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import pytest

import ghp.__main__ as cli
from conftest import FakeGh
from ghp import comments as cmod, decisions, issues
from ghp.dod import render_dod, write_dod
from ghp.gh import GhError, GhTimeout, Viewer, WriteNotArmed
from ghp.refs import Ref
from test_questions import HOSTILE, ME, OTHER, Issue1001, _name_after_at, comment

REF = Ref("octo", "demo", 1001)
OPEN = "- [ ] D1: Use cursor paging → _(open)_ · kind: decision · why: the story names no page size"
VAL = "- [x] D2: Cap at 50 rows → validated · by: PO · note: agreed in the thread"
REJ = "- [x] D3: Skip audit log → rejected · by: PO · note: not acceptable"


def _file(body):
    """A decisions file: the template (title and format note) followed by the entries."""
    return decisions.render_file(1001) + body + "\n"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A cwd whose ghp.config.json names octo/demo (no dod directory yet, no token, no config override)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo"}))
    return tmp_path


def main(argv):
    """The CLI over an empty FakeGh: a local verb never reaches it, and an unexpected call fails loudly."""
    return cli.main(argv, gh=FakeGh())


def _wi(ac="old AC", n=1001):
    return issues.Issue(ref=Ref("octo", "demo", n), node_id="I_kwDOFictional%04d" % n, id=9100000000 + n, title="T", body="## Acceptance criteria\n" + ac + "\n",
                        state="open", state_reason=None, labels=[], milestone=None, author=issues.Actor(1001, "octo-author", "User"), parent=None,
                        sub_total=0, sub_done=0, url="https://github.com/octo/demo/issues/%d" % n)


def test_parse_open_validated_rejected():
    r = decisions.parse_decisions(_file("\n".join([OPEN, VAL, REJ])))
    assert r["unparsed"] is False
    assert r["open"] == [{"id": "D1", "kind": "decision", "text": "Use cursor paging",
                          "why": "the story names no page size", "status": "open", "by": "", "note": "",
                          "posted": None, "used": ""}]
    assert [d["id"] for d in r["validated"]] == ["D2"] and r["validated"][0]["by"] == "PO"
    assert r["validated"][0]["note"] == "agreed in the thread" and r["validated"][0]["status"] == "validated"
    assert [d["id"] for d in r["rejected"]] == ["D3"] and r["rejected"][0]["status"] == "rejected"


def test_round_trip_render_line():
    lines = [OPEN, VAL, REJ,
             OPEN + " · posted: 42:" + decisions.line_hash("Use cursor paging"),
             "- [ ] D4: Skip the freeze → _(open)_ · kind: override · why: outage · used: 2026-11-02",
             "- [x] D5: a → b → c → validated · by: PO · note: ok · posted: 7:deadbeef"]
    for line in lines:
        r = decisions.parse_decisions(_file(line))
        d = (r["open"] + r["validated"] + r["rejected"])[0]
        assert decisions.render_line(d) == line, line


def test_parse_reads_whole_file_ignoring_fences():
    body = f"```\n{OPEN}\n```\n<!-- {VAL} -->\n<!--\n{REJ}\n-->\n- [ ] D9: real → _(open)_ · kind: assumption · why: w"
    r = decisions.parse_decisions(_file(body))
    assert [d["id"] for d in r["open"]] == ["D9"] and not r["validated"] and not r["rejected"]
    assert r["unparsed"] is False
    assert decisions.parse_decisions(_file("<!-- never closed\n" + OPEN))["unparsed"] is True
    # no `## ` heading is needed or looked for: lines after any heading are read too
    r = decisions.parse_decisions("# Decisions\n## Later\n" + OPEN + "\n## 3. x\n" + VAL + "\n")
    assert [d["id"] for d in r["open"]] == ["D1"] and [d["id"] for d in r["validated"]] == ["D2"]


def test_duplicate_id_first_wins():
    r = decisions.parse_decisions(_file(OPEN + "\n" + OPEN.replace("cursor paging", "offset paging") + "\n"
                                          + VAL.replace("D2", "D1")))
    assert [d["text"] for d in r["open"]] == ["Use cursor paging"] and r["validated"] == []


def test_malformed_line_sets_unparsed():
    for bad in ["- [ ] D1: no arrow", "- [ ] D1: t → _(open)_ · why: no kind",
                "- [ ] D1: t → _(open)_ · kind: guess · why: w", "- [ ] D1: t → validated · by: PO · note: n",
                "- [x] D1: t → _(open)_ · kind: decision · why: w", "- [x] D1: t → validated · note: no role",
                "- [ ] D1: t → _(open)_ · kind: decision · why: w · posted: nope"]:
        r = decisions.parse_decisions(_file(bad + "\n" + VAL))
        assert r["unparsed"] is True and r["open"] == [] and [d["id"] for d in r["validated"]] == ["D2"], bad
    assert decisions.parse_decisions("nothing") == {"open": [], "validated": [], "rejected": [], "unparsed": False}


def test_posted_marker_parsed_and_hash_changes_with_text():
    h = decisions.line_hash("Use cursor paging")
    assert len(h) == 8 and h == decisions.line_hash("Use  cursor\tpaging ") and h != decisions.line_hash("Use offset")
    r = decisions.parse_decisions(_file(f"{OPEN} · posted: 42:{h}"))
    assert r["open"][0]["posted"] == {"comment_id": 42, "hash": h}



def test_dod_template_no_longer_seeds_decisions_heading_and_points_to_file():
    md = render_dod(_wi(), synced="2026-07-14")
    assert "Decisions & assumptions" not in md and "D1:" not in md
    bullet = next(x for x in md.splitlines() if x.startswith("- **Assumptions:**"))
    assert "Decisions_1001.md" in bullet and "`ghp decisions 1001 --add`" in bullet
    assert md.count("Decisions_1001.md") == 1
    assert decisions.parse_decisions(md) == {"open": [], "validated": [], "rejected": [], "unparsed": False}


def test_file_template_has_title_and_format_note_that_does_not_parse():
    md = decisions.render_file(1001)
    assert md.startswith("# Decisions — US1001\n") and "<!--" not in md and "-->" not in md
    assert "`- [ ] D1: <text> → _(open)_ · kind: decision · why: <reason>`" in md
    assert decisions.parse_decisions(md) == {"open": [], "validated": [], "rejected": [], "unparsed": False}
    assert not decisions.has_decision_lines(md)
    assert decisions.decisions_file("dod", 1001) == Path("dod") / "Decisions_1001.md"



def test_refresh_neither_touches_nor_requires_the_decisions_file(tmp_path):
    p = write_dod(_wi("old AC"), str(tmp_path), synced="2026-07-14")
    write_dod(_wi("new AC"), str(tmp_path), synced="2026-07-15", refresh=True)
    assert "new AC" in p.read_text() and not (tmp_path / "Decisions_1001.md").exists()
    d = tmp_path / "Decisions_1001.md"
    d.write_text(_file(OPEN), encoding="utf-8")
    before = (d.read_bytes(), d.stat().st_mtime_ns)
    write_dod(_wi("newer AC"), str(tmp_path), synced="2026-07-16", refresh=True)
    assert (d.read_bytes(), d.stat().st_mtime_ns) == before and "newer AC" in p.read_text()


def test_cli_decisions_read_only_and_missing_file_exit_2(repo, capsys):
    assert main(["decisions", "1001"]) == 2
    assert "`ghp decisions 1001 --add " in capsys.readouterr().err
    (repo / "dod").mkdir()
    led = repo / "dod" / "Decisions_1001.md"
    led.write_text(_file(OPEN + "\n" + VAL), encoding="utf-8")
    before = led.read_bytes()
    assert main(["decisions", "1001"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [d["id"] for d in out["open"]] == ["D1"] and [d["id"] for d in out["validated"]] == ["D2"]
    assert led.read_bytes() == before


def test_the_decisions_file_is_read_confined_and_its_text_never_reaches_the_terminal_raw(repo, capsys):
    (repo / "dod").mkdir()
    (repo / "elsewhere.md").write_text(_file(OPEN), encoding="utf-8")
    (repo / "dod" / "Decisions_1001.md").symlink_to(repo / "elsewhere.md")  # a PR-controlled symlink is never followed
    assert main(["decisions", "1001"]) == 2 and "cannot read the decisions file" in capsys.readouterr().err
    (repo / "dod" / "Decisions_1001.md").unlink()
    (repo / "dod" / "Decisions_1001.md").write_text(_file("- [ ] D1: a\u202eb\x9b2J\u200bc → _(open)_ · kind: decision · why: w"), encoding="utf-8")
    assert main(["decisions", "1001"]) == 0
    out = capsys.readouterr().out
    assert out.isascii() and "\u202e" not in out and "\x9b" not in out
    assert json.loads(out)["open"][0]["text"] == "a\u202eb\x9b2J\u200bc"  # escaped, not lost


def test_pathological_whitespace_line_parses_fast():
    import time
    status = "- [ ] D1: t → _(open)_"
    for bad in (status + " " * 200_000 + "· kind: decision · why: w", "- [ ] D1: " + "x" * 200_000 + " → _(open)_ · kind: decision · why: w"):
        t = time.perf_counter()
        r = decisions.parse_decisions(_file(bad + "\n" + VAL))
        assert time.perf_counter() - t < 1
        assert r["unparsed"] is True and r["open"] == [] and [d["id"] for d in r["validated"]] == ["D2"]



def _timed(fn):
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start


@pytest.mark.parametrize("fill", [" ", "\t", " \t", "\u00a0", "\u3000"])
def test_every_grammar_pattern_is_linear_on_30000_characters_of_blanks(fill):
    """The patterns of `_line` run behind the MAX_LINE cap, but T23/T28 and the helpers may call them on a longer line, and ado's `_COLON`, `_STATUS`, `_SEP`
    (and `_MARK`) were quadratic on a run of blanks: 0.5 s, 5.6 s and 5.3 s at 12,000 blanks, so about 3 s, 35 s and 33 s at 30,000. Each is used here the way the
    module uses it, over shapes with the run in every position, and must stay under a second at 30,000 and show no growth beyond 4x for 4x the input."""
    def shapes(n):
        s = fill * n
        return {"_CAND.match": [s, "-" + s, s + "- [ ] D1", "- [ ]" + s, "- [ ] D" + "1" * n, "- [ ] D1" + s],
                "_COLON.match": [s, s + ":" + s, ":" + s + "x", s + "x", ":" + s + "x" + s],
                "_STATUS.finditer": [s, s + "\u2192" + s, "->" + s, s + "->" + s + "validated", "\u2192" + s + "validated" + s + "x", "->" * n, "\u2192 validated" * (n // 10),
                                     ("\u2192" + s[:10]) * (n // 10)],
                "_SEP.split": [s, s + "\u00b7" + s, "\u00b7" + s + "kind", s + "kind", "\u00b7" + s, "\u00b7" * n, ("\u00b7" + s[:10]) * (n // 10)],
                "_MARK.finditer": [s, s + "\u00b7" + s, "\u00b7" + s + "posted" + s + ":" + s + "5", "\u00b7 posted: 5:abcdef12" + s, ("\u00b7" + s[:10]) * (n // 10)],
                "_POSTED.fullmatch": ["5:" + s, "1" * n, "1" * n + ":"], "_VIA.fullmatch": ["1" * n, "1" * n + "x"]}
    use = {"_CAND.match": lambda x: decisions._CAND.match(x), "_COLON.match": lambda x: decisions._COLON.match(x),
           "_STATUS.finditer": lambda x: list(decisions._STATUS.finditer(x)), "_SEP.split": lambda x: decisions._SEP.split(x),
           "_MARK.finditer": lambda x: list(decisions._MARK.finditer(x)), "_POSTED.fullmatch": lambda x: decisions._POSTED.fullmatch(x),
           "_VIA.fullmatch": lambda x: decisions._VIA.fullmatch(x)}
    small, big = shapes(7500), shapes(30000)
    for name, fn in use.items():
        for a, b in zip(small[name], big[name]):
            t_small, t_big = _timed(lambda: fn(a)), _timed(lambda: fn(b))
            assert t_big < 1.0, (name, repr(b[:12]), t_big)
            assert t_big < max(0.05, 12 * t_small), (name, repr(b[:12]), t_small, t_big)  # linear is 4x; quadratic is 16x


def test_a_whole_line_of_30000_blanks_in_every_position_is_parsed_linearly_even_without_the_cap(monkeypatch):
    for fill in (" ", "\t"):
        blanks = fill * 30000
        lines = ["- [ ] D1" + blanks + ": t \u2192 _(open)_ \u00b7 kind: decision \u00b7 why: w", "- [ ] D1: t" + blanks + "\u2192 _(open)_ \u00b7 kind: decision \u00b7 why: w",
                 "- [ ] D1: t \u2192 _(open)_" + blanks + "\u00b7 kind: decision \u00b7 why: w", "- [ ] D1: t \u2192 _(open)_ \u00b7 kind: decision" + blanks + "\u00b7 why: w",
                 "- [ ] D1: t \u2192 _(open)_ \u00b7 kind: decision \u00b7 why: w" + blanks, "- [ ] D1: " + blanks + "t \u2192 _(open)_ \u00b7 kind: decision \u00b7 why: w",
                 "- [x] D1: t \u2192 validated \u00b7 by: PO \u00b7 note: n" + blanks + "\u00b7 posted: 1:" + blanks,
                 "- [ ] D1: a" + blanks + "b \u2192 _(open)_ \u00b7 kind: decision \u00b7 why: w \u00b7 posted: 5:abcdef12" + blanks + "\u00b7 used: 2026-01-01"]
        for ln in lines:
            t = _timed(lambda: decisions._line(ln))  # `_line` itself, below the MAX_LINE guard
            assert t < 1.0, (fill, ln[:40], t)


def _old_line(raw):
    """ado 0.3.1's `_line` with ado's patterns, verbatim: the reference the linear patterns must agree with."""
    cand = re.compile(r"^\s*(?:[-*+]\s+)?\[([ xX])\]\s*D(\d+)(.*)$")
    colon = re.compile(r"\s*:\s*(.*?)\s*$")
    status = re.compile(r"\s*(?:\u2192|->)\s*(_\(open\)_|validated|rejected)(?=\s*(?:\u00b7|$))")
    sep = re.compile(r"\s*\u00b7\s*(?=(?:" + "|".join(decisions._KEYS) + r")\s*:)")
    m = cand.match(raw)
    c = colon.match(m.group(3)) if m else None
    if not c:
        return None
    done = m.group(1) in "xX"
    rest = c.group(1)
    st = list(status.finditer(rest))
    if not st or (st[-1].group(1) == decisions._OPEN) == done:
        return None
    s = st[-1]
    text, tail = rest[:s.start()].strip(), rest[s.end():]
    first, *parts = sep.split(tail)
    kv = {}
    for p in parts:
        k, _, v = p.partition(":")
        if k.strip() in kv:
            return None
        kv[k.strip()] = v.strip()
    kind = kv.get("kind", "decision")
    posted = decisions._POSTED.fullmatch(kv["posted"]) if "posted" in kv else None
    bad = (first.strip() or not text or kind not in decisions.KINDS or ("posted" in kv and not posted)
           or (not done and not (kv.get("kind") and kv.get("why")))
           or (done and not kv.get("by")) or (not done and ("by" in kv or "note" in kv or "via" in kv))
           or ("via" in kv and not decisions._VIA.fullmatch(kv["via"])))
    if bad:
        return None
    sup = decisions._SUP.match(text)
    return {"id": f"D{m.group(2)}", "kind": kind, "text": text, "why": kv.get("why", ""),
            "status": "open" if not done else s.group(1), "by": kv.get("by", ""), "note": kv.get("note", ""),
            "posted": {"comment_id": int(posted.group(1)), "hash": posted.group(2)} if posted else None,
            "used": kv.get("used", ""), **({"supersedes": f"D{sup.group(1)}"} if sup else {}), **({"via": kv["via"]} if "via" in kv else {})}


def test_the_linear_patterns_read_every_line_as_ado_s_patterns_did():
    import random
    corpus = set(re.findall(r"- \[[ xX]\] D\d+[^\"'\n]*", Path(__file__).read_text(encoding="utf-8")))
    for f in sorted(Path(__file__).parent.glob("test_*.py")):
        corpus |= set(re.findall(r"- \[[ xX]\] D\d+[^\"'\n]*", f.read_text(encoding="utf-8")))
    assert len(corpus) > 60
    for ln in sorted(corpus):
        assert decisions._line(ln) == _old_line(ln), ln
    # a seeded fuzz: lines built from the grammar's own parts, with blanks of every kind (the characters `\\s` and `str.strip` must treat alike) and stray
    # arrows, middle dots and keys put in the text and between the parts
    rng = random.Random(20261109)
    blanks = [" ", "  ", "\t", "\u00a0", "\x1f", "\x85", "\u2003", "\u3000", ""]
    noise = ["\u2192", "->", " \u2192 ", "\u00b7", " \u00b7 ", "kind:", "why: x", ":", "_(open)_", "validated", "rejected", "posted: 5:abcdef12", "via: 3", "x", "a b", "\ufeff", "\u200b"]
    heads = ["- [ ] D1:", "- [x] D2:", "* [X] D3 :", "[ ] D4:", "  -   [ ]  D5  :", "- [x] D6"]
    statuses = ["\u2192 _(open)_", "-> validated", "\u2192 rejected", "\u2192 validated", "->_(open)_", "\u2192validated", "\u2192 _(open)_x", "\u2192 maybe"]
    parts = ["kind: decision", "kind: assumption", "kind: override", "kind: nope", "why: w", "why: a \u00b7 b", "by: PO", "note: n", "posted: 5:abcdef12", "posted: 5:ABCDEF12",
             "used: 2026-01-01", "via: 12", "via: 012", "kind:", "why :  w", "  kind  :  decision "]
    seen = {"none": 0, "line": 0}
    for _ in range(30000):
        pick = lambda xs: rng.choice(xs)  # noqa: E731
        text = "".join(pick(noise) if rng.random() < 0.3 else pick(blanks) + pick(["t", "use v2", "supersedes D3: y", "a b"]) for _ in range(rng.randint(1, 4)))
        tail = "".join(pick(blanks) + pick(["\u00b7", " \u00b7 ", "\u00b7"]) + pick(blanks) + pick(parts) for _ in range(rng.randint(0, 4)))
        ln = pick(heads) + pick(blanks) + text + pick(blanks) + pick(statuses) + tail + pick(blanks)
        if rng.random() < 0.3:  # a stray token anywhere
            i = rng.randrange(len(ln) + 1)
            ln = ln[:i] + pick(noise) + ln[i:]
        got = decisions._line(ln)
        assert got == _old_line(ln), repr(ln)
        seen["line" if got else "none"] += 1
    assert seen["line"] > 200 and seen["none"] > 3000  # the fuzz reaches both outcomes
    # valid lines with one to three mutations (a stray token, a blank swapped for another, a character cut): most still read, and read alike
    base = ["- [ ] D1: Use cursor paging \u2192 _(open)_ \u00b7 kind: decision \u00b7 why: the story names no page size",
            "- [x] D2: a \u2192 b -> c \u2192 validated \u00b7 by: PO \u00b7 note: agreed \u00b7 posted: 7:deadbeef \u00b7 used: 2026-01-01 \u00b7 via: 12",
            "- [ ] D3: supersedes D2: x -> y \u2192 _(open)_ \u00b7 kind: override \u00b7 why: z \u00b7 posted: 1:abcdef12", "* [X] D4 : t\t\u2192\trejected\t\u00b7\tby: PO\t\u00b7\tnote:"]
    for _ in range(20000):
        ln = rng.choice(base)
        for _ in range(rng.randint(1, 3)):
            i = rng.randrange(len(ln) + 1)
            how = rng.random()
            ln = (ln[:i] + rng.choice(noise) + ln[i:] if how < 0.4 else ln[:i] + rng.choice(blanks) + ln[i:] if how < 0.8 else ln[:i] + ln[i + 1:])
        got = decisions._line(ln)
        assert got == _old_line(ln), repr(ln)
        seen["line" if got else "none"] += 1
    assert seen["line"] > 5000


def test_a_marker_hidden_in_an_inline_comment_is_refused_not_a_traceback():
    line = "- [ ] D1: t \u2192 _(open)_ \u00b7 kind: decision \u00b7 why: w \u00b7 posted:<!-- x -->12:abcdef12"
    md = _file(line)
    assert decisions.parse_decisions(md)["open"][0]["posted"] == {"comment_id": 12, "hash": "abcdef12"}  # the grammar reads it through the comment
    with pytest.raises(ValueError, match="inline <!-- --> comment"):
        decisions._marked(md, {"D1": decisions.marker_hash(decisions.parse_decisions(md)["open"][0])}, 7)


def test_the_longest_legal_line_is_read_and_one_character_more_is_not():
    why = "w" * (decisions.MAX_LINE - len("- [ ] D1: t \u2192 _(open)_ \u00b7 kind: decision \u00b7 why: "))
    ok = f"- [ ] D1: t \u2192 _(open)_ \u00b7 kind: decision \u00b7 why: {why}"
    assert len(ok) == decisions.MAX_LINE and decisions.parse_decisions(_file(ok))["unparsed"] is False
    assert decisions.parse_decisions(_file(ok + "w"))["unparsed"] is True

# ---- post the unposted decision lines as one new comment (preview -> gate -> apply) ----
M = decisions.MARKER
HEAD = "Decisions and assumptions made during development; please validate or reject:"
H1 = decisions.line_hash("Use cursor paging")
H2 = decisions.line_hash("Retry twice")
OPEN2 = "- [ ] D4: Retry twice → _(open)_ · kind: assumption · why: the story is silent"
POSTS = "repos/octo/demo/issues/1001/comments"


def _sha(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _body(*lines):
    """The comment the verb posts for these lines: the marker and the hash of the rest on the first line."""
    content = "\n".join([HEAD, *lines])
    return f"{M} {_sha(content)}\n{content}"


class _Cli:
    def __init__(self, gh):
        self.gh = gh

    def main(self, argv):
        return cli.main(argv, gh=self.gh)


def _setup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo"}))
    return tmp_path


def _env(tmp_path, monkeypatch, body, posted_id=777, **kw):
    """(the CLI over a FakeGh thread, the thread, the decisions file): issue 1001 of octo/demo, `body` in dod/Decisions_1001.md, the next comment id `posted_id`."""
    _setup(tmp_path, monkeypatch)
    (tmp_path / "dod").mkdir(exist_ok=True)
    led = tmp_path / "dod" / "Decisions_1001.md"
    led.write_text(_file(body), encoding="utf-8")
    thread = Issue1001(**kw)
    thread.next_id = posted_id - 1
    return _Cli(thread.gh), thread, led


def _rc(call):
    """The exit code of a CLI call, SystemExit (argparse) included."""
    try:
        return call()
    except SystemExit as e:
        return e.code


def _plan_of(parsed, items=()):
    return decisions.plan_post(Issue1001(items).gh, REF, parsed)


def _plan(ledger_body, items=()):
    return _plan_of(decisions.parse_decisions(_file(ledger_body)), items)


def _writes(thread):
    return thread.posts


def test_plan_none_when_everything_posted():
    posted = f"{OPEN} · posted: 5:{H1}"
    t = Issue1001()
    plan = decisions.plan_post(t.gh, REF, decisions.parse_decisions(_file(posted)))
    assert plan["action"] == "none" and plan["body"] == "" and plan["lines"] == [] and t.gh.calls == []  # nothing to post: nothing is even read
    assert "nothing to post" in decisions.render_plan(REF, plan)


def test_preview_says_gets_for_one_line_and_get_for_several():
    one = decisions.render_plan(REF, _plan(OPEN))
    assert "After posting, D1 gets ` · posted: <comment id>:<hash>` in the decisions file." in one
    two = decisions.render_plan(REF, _plan(OPEN + "\n- [ ] D2: two → _(open)_ · kind: decision · why: w"))
    assert "After posting, D1, D2 get ` · posted:" in two


def test_plan_adds_one_new_comment_listing_unposted_lines_only():
    plan = _plan(f"{OPEN} · posted: 5:{H1}\n{OPEN2}\n{VAL}")
    assert plan["action"] == "add" and plan["lines"] == ["D4"] and plan["cut"] == []
    assert plan["body"] == _body("D4 (assumption): Retry twice — why: the story is silent")
    assert "D1" not in plan["body"]


def test_edited_line_after_posting_is_reposted():
    plan = _plan(f"- [ ] D1: Use cursor paging v2 → _(open)_ · kind: decision · why: w · posted: 5:{H1}")
    assert plan["lines"] == ["D1"] and "Use cursor paging v2" in plan["body"]
    assert plan["hashes"] == {"D1": decisions.line_hash("Use cursor paging v2")}


def test_resolved_lines_are_not_posted():
    assert _plan(VAL + "\n" + REJ)["action"] == "none"


def test_comment_never_contains_a_mention_or_an_address_and_is_escaped():
    # one behaviour: an @mention or address typed in a decision is defanged like `ghp comments` does, never refused
    body = ("- [ ] D1: Ask @dana or d@contoso.example about <a href=\"#\">Dana</a> "
            "→ _(open)_ · kind: decision · why: mail e@contoso.example <b> & more")
    text = _plan(body)["body"]
    assert "@" not in text and text.count(cmod.NO_MENTION) == 3 and "contoso" not in text
    assert "&lt;b&gt;" in text and "<b>" not in text and "<a " not in text and "&amp; more" in text


@pytest.mark.parametrize("hostile", HOSTILE)
def test_every_at_before_a_name_is_defanged_in_the_comment_line(hostile):
    d = {"id": "D1", "kind": "decision", "text": hostile, "why": hostile, "status": "open", "by": "", "note": "", "posted": None, "used": ""}
    line, _ = decisions._comment_line(d)
    assert not _name_after_at(line)


def test_long_line_is_cut_at_500_with_ellipsis():
    plan = _plan(f"- [ ] D1: {'x' * 600} → _(open)_ · kind: decision · why: w")
    line = plan["body"].split("\n")[2]
    assert len(line) == 500 and line.endswith("…") and plan["cut"] == ["D1"]
    assert "D1 cut to 500 characters" in decisions.render_plan(REF, plan)


def test_comment_line_escapes_what_a_terminal_would_act_on():
    body = "- [ ] D1: hide\x1b[8mme\x00 now\x7fthere ‮ ​ → _(open)_ · kind: decision · why: w"
    plan = _plan(body)
    for text in (plan["body"], decisions.render_plan(REF, plan)):
        assert not any(c in text for c in "\x1b\x00\x7f‮​") and "hide" in text and "\\u001b" in text and "\\u202e" in text
        assert not any(ord(c) < 32 and c != "\n" for c in text)


def test_the_comment_lines_are_the_lines_the_wait_gate_will_look_for():
    plan = _plan(OPEN + "\n" + OPEN2 + "\n- [ ] D5: skip l2 → _(open)_ · kind: override · why: a reason that is long enough")
    lines = plan["body"].split("\n")[2:]
    assert [decisions.POSTED_LINE.match(x).group(1) for x in lines] == ["1", "4", "5"]  # the shape T23 and T28 read back
    assert decisions.comment_line_is_lossy({**decisions.parse_decisions(_file(OPEN))["open"][0], "text": "ask @dana"}) is True
    assert decisions.comment_line_is_lossy(decisions.parse_decisions(_file(OPEN))["open"][0]) is False
    assert decisions.comment_line_is_lossy(decisions.parse_decisions(_file(f"- [ ] D1: {'x' * 600} → _(open)_ · kind: decision · why: w"))["open"][0]) is True


def test_dry_run_and_plan_out_write_nothing(tmp_path, monkeypatch, capsys):
    m, t, led = _env(tmp_path, monkeypatch, OPEN)
    before = led.read_bytes()
    assert m.main(["decisions", "1001", "--post"]) == 0
    out = capsys.readouterr().out
    assert "Action: ADD a new comment" in out and f"> {M} " in out and "> D1 (decision): Use cursor paging" in out
    assert m.main(["decisions", "1001", "--plan-out", "p.md"]) == 0
    plan = (tmp_path / "p.md").read_text()
    assert "ADD a new comment" in plan and f"> {M}" in plan and "> D1 (decision): Use cursor paging" in plan
    assert _writes(t) == [] and not t.gh.armed and led.read_bytes() == before


def test_apply_posts_exact_previewed_text_then_writes_marker(tmp_path, monkeypatch, capsys):
    m, t, led = _env(tmp_path, monkeypatch, f"{OPEN}\n{VAL}\n{OPEN2} · posted: 4:{'0' * 8}")
    assert m.main(["decisions", "1001", "--post", "--plan-out", "p.md"]) == 0
    capsys.readouterr()
    assert m.main(["decisions", "1001", "--post", "--apply", "--expect-plan", "p.md"]) == 0
    out = capsys.readouterr().out
    body = _body("D1 (decision): Use cursor paging — why: the story names no page size", "D4 (assumption): Retry twice — why: the story is silent")
    assert _writes(t) == [("rest", "POST", POSTS, {"body": body})] and "posted comment 777" in out and t.gh.armed
    lines = led.read_text().splitlines()
    assert f"{OPEN} · posted: 777:{H1}" in lines and f"{OPEN2} · posted: 777:{H2}" in lines and VAL in lines
    assert led.read_text().count("posted:") == 2  # the old marker was replaced, not stacked
    d = decisions.parse_decisions(led.read_text())
    assert d["unparsed"] is False and d["open"][0]["posted"] == {"comment_id": 777, "hash": H1}
    assert m.main(["decisions", "1001", "--post", "--plan-out", "q.md"]) == 0  # a second preview: nothing left
    assert "nothing to post" in (tmp_path / "q.md").read_text()


def test_decisions_post_is_idempotent(tmp_path, monkeypatch, capsys):
    """AC2: a dry run sends nothing; `--post --apply` posts once and records the `posted:` line; running it again sends nothing."""
    m, t, led = _env(tmp_path, monkeypatch, OPEN)
    assert m.main(["decisions", "1001", "--post"]) == 0 and _writes(t) == [] and "posted:" not in led.read_text()
    assert m.main(["decisions", "1001", "--post", "--apply"]) == 0
    assert len(_writes(t)) == 1 and f"· posted: 777:{H1}" in led.read_text()
    t.gh.calls.clear()
    capsys.readouterr()
    assert m.main(["decisions", "1001", "--post", "--apply"]) == 0
    assert "nothing to post" in capsys.readouterr().out and _writes(t) == [] and t.gh.calls == [] and len(t.bodies) == 1


def test_a_comment_posted_but_not_recorded_is_adopted_not_posted_again(tmp_path, monkeypatch, capsys):
    m, t, led = _env(tmp_path, monkeypatch, OPEN)
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    assert m.main(["decisions", "1001", "--post", "--apply"]) == 2
    err = capsys.readouterr().err
    assert "comment 777 was posted" in err and f"D1: · posted: 777:{H1}" in err and "disk full" in err
    assert led.read_bytes() == _file(OPEN).encode() and list(led.parent.glob("*.tmp")) == [] and len(_writes(t)) == 1
    monkeypatch.setattr(os, "replace", real)
    assert m.main(["decisions", "1001", "--post"]) == 0
    assert "ADOPT comment 777" in capsys.readouterr().out
    assert m.main(["decisions", "1001", "--post", "--apply"]) == 0
    assert len(_writes(t)) == 1 and f"· posted: 777:{H1}" in led.read_text()  # the existing comment is recorded; nothing is sent
    assert "found on the issue" in capsys.readouterr().out


def test_an_edited_own_comment_is_not_adopted_and_a_foreign_one_is_ignored(tmp_path, monkeypatch, capsys):
    body = _body("D1 (decision): Use cursor paging — why: the story names no page size")
    g, r = comment(9, body)
    g["lastEditedAt"] = "2026-01-02T10:00:00Z"
    m, t, led = _env(tmp_path, monkeypatch, OPEN, items=[(g, r)])
    assert m.main(["decisions", "1001", "--post"]) == 0 and "ADD a new comment" in capsys.readouterr().out
    m, t, led = _env(tmp_path, monkeypatch, OPEN, items=[comment(3, body, author=OTHER)])  # someone else posted exactly our text: it is not ours
    assert m.main(["decisions", "1001", "--post", "--apply"]) == 0 and len(_writes(t)) == 1


def test_expect_plan_mismatch_exit_2_no_write(tmp_path, monkeypatch, capsys):
    m, t, led = _env(tmp_path, monkeypatch, OPEN)
    assert m.main(["decisions", "1001", "--post", "--plan-out", "p.md"]) == 0
    approved = (tmp_path / "p.md").read_text()
    led.write_text(_file(OPEN + "\n" + OPEN2), encoding="utf-8")
    before = led.read_bytes()
    assert m.main(["decisions", "1001", "--post", "--apply", "--expect-plan", "p.md"]) == 2
    assert "plan changed since approval" in capsys.readouterr().err
    assert _writes(t) == [] and not t.gh.armed and led.read_bytes() == before and (tmp_path / "p.md").read_text() == approved
    assert "D4" in (tmp_path / "p.md.new").read_text()


def test_apply_needs_post_and_expect_plan_needs_apply(repo, capsys):
    (repo / "dod").mkdir()
    (repo / "dod" / "Decisions_1001.md").write_text(_file(OPEN), encoding="utf-8")
    t = Issue1001()
    assert cli.main(["decisions", "1001", "--apply"], gh=t.gh) == 2 and "--apply needs --post" in capsys.readouterr().err and t.gh.calls == []
    with pytest.raises(SystemExit) as e:
        cli.main(["decisions", "1001", "--post", "--expect-plan", "p.md"], gh=t.gh)
    assert e.value.code == 2 and t.gh.calls == [] and not t.gh.armed


def test_ledger_changed_since_plan_posts_nothing(tmp_path, monkeypatch):
    m, t, led = _env(tmp_path, monkeypatch, OPEN)
    plan = decisions.plan_post(t.gh, REF, decisions.parse_decisions(led.read_text()))
    led.write_text(_file(OPEN.replace("cursor", "offset")), encoding="utf-8")
    t.gh.arm()
    with pytest.raises(ValueError, match="changed since the plan"):
        decisions.apply_post(t.gh, REF, plan, led)
    assert _writes(t) == []


def test_a_decisions_file_that_vanishes_or_is_swapped_for_a_link_before_the_apply_posts_nothing(tmp_path, monkeypatch, capsys):
    m, t, led = _env(tmp_path, monkeypatch, OPEN)
    plan = decisions.plan_post(t.gh, REF, decisions.parse_decisions(led.read_text()))
    t.gh.arm()
    led.unlink()
    with pytest.raises(ValueError, match="cannot read the decisions file"):
        decisions.apply_post(t.gh, REF, plan, led)
    (tmp_path / "elsewhere.md").write_text(_file(OPEN), encoding="utf-8")
    led.symlink_to(tmp_path / "elsewhere.md")
    with pytest.raises(ValueError, match="not a symlink"):
        decisions.apply_post(t.gh, REF, plan, led)
    assert _writes(t) == []


def test_line_terminated_by_u2028_keeps_the_next_line_intact(tmp_path, monkeypatch):
    m, t, led = _env(tmp_path, monkeypatch, OPEN + " " + VAL)
    assert m.main(["decisions", "1001", "--post", "--apply"]) == 0
    md = led.read_text(encoding="utf-8")
    assert f"{OPEN} · posted: 777:{H1} {VAL}" in md
    d = decisions.parse_decisions(md)
    assert d["unparsed"] is False and [x["id"] for x in d["open"]] == ["D1"] and [x["id"] for x in d["validated"]] == ["D2"]


def test_crlf_ledger_round_trips(tmp_path, monkeypatch):
    m, t, led = _env(tmp_path, monkeypatch, OPEN + "\n" + VAL)
    led.write_bytes(led.read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8"))
    before = led.read_bytes()
    assert m.main(["decisions", "1001", "--post", "--apply"]) == 0
    after = led.read_bytes()
    assert after.count(b"\r\n") == before.count(b"\r\n") and after.count(b"\n") == before.count(b"\n")
    assert f"{OPEN} · posted: 777:{H1}\r\n{VAL}\r\n".encode("utf-8") in after


def test_marker_lookalike_in_the_text_stays_verbatim_and_is_posted_once(tmp_path, monkeypatch):
    text = "a · posted: 5:abcdef12 · b"
    line = f"- [ ] D1: {text} → _(open)_ · kind: decision · why: w"
    m, t, led = _env(tmp_path, monkeypatch, line)
    assert m.main(["decisions", "1001", "--post", "--apply"]) == 0
    md = led.read_text(encoding="utf-8")
    assert f"{line} · posted: 777:{decisions.line_hash(text)}" in md
    assert decisions.parse_decisions(md)["open"][0]["text"] == text
    assert _plan_of(decisions.parse_decisions(md))["action"] == "none"


def test_ledger_is_reread_after_the_post(tmp_path, monkeypatch):
    m, t, led = _env(tmp_path, monkeypatch, OPEN)
    plan = decisions.plan_post(t.gh, REF, decisions.parse_decisions(led.read_text()))
    post = t.post
    t.gh.on_rest("POST", POSTS, lambda b: (led.write_text(led.read_text() + "- added while posting\n", encoding="utf-8"), post(b))[1])
    t.gh.arm()
    assert decisions.apply_post(t.gh, REF, plan, led) == {"comment_id": 777, "how": "posted"}
    md = led.read_text(encoding="utf-8")
    assert "added while posting" in md and f"posted: 777:{H1}" in md


def test_targeted_line_edited_during_the_post_is_not_marked(tmp_path, monkeypatch):
    m, t, led = _env(tmp_path, monkeypatch, OPEN)
    plan = decisions.plan_post(t.gh, REF, decisions.parse_decisions(led.read_text()))
    post = t.post
    edited = _file(OPEN.replace("cursor", "offset"))
    t.gh.on_rest("POST", POSTS, lambda b: (led.write_text(edited, encoding="utf-8"), post(b))[1])
    t.gh.arm()
    with pytest.raises(ValueError, match=rf"comment 777 was posted.*D1: · posted: 777:{H1}"):
        decisions.apply_post(t.gh, REF, plan, led)
    assert led.read_text(encoding="utf-8") == edited


# ---- the write that may have been applied: read again, never send twice ----
def test_post_after_timeout_rereads_and_records_the_comment_it_finds(tmp_path, monkeypatch, capsys):
    m, t, led = _env(tmp_path, monkeypatch, OPEN, fail=GhTimeout(write=True), apply=True)  # the write timed out but GitHub did apply it
    assert m.main(["decisions", "1001", "--post", "--apply"]) == 0
    assert len(_writes(t)) == 1 and len(t.bodies) == 1  # never sent twice
    post = next(i for i, c in enumerate(t.gh.calls) if c[1] == "POST")
    assert any(c[0] == "graphql" for c in t.gh.calls[post + 1:])  # a read came after the write
    assert "found on re-read" in capsys.readouterr().out and f"· posted: 777:{H1}" in led.read_text()


def test_post_after_timeout_that_did_not_apply_is_reported_not_retried_and_records_nothing(tmp_path, monkeypatch, capsys):
    m, t, led = _env(tmp_path, monkeypatch, OPEN, fail=GhTimeout(write=True), apply=False)
    assert m.main(["decisions", "1001", "--post", "--apply"]) == 1
    err = capsys.readouterr().err
    assert len(_writes(t)) == 1 and t.bodies == [] and "E_GH_TIMEOUT" in err and "not found on re-read and not retried" in err
    assert led.read_bytes() == _file(OPEN).encode()


def test_a_definite_4xx_is_not_reread_or_retried(tmp_path, monkeypatch, capsys):
    m, t, led = _env(tmp_path, monkeypatch, OPEN, fail=GhError("E_GH_HTTP", 403, "Resource not accessible"), apply=False)
    assert m.main(["decisions", "1001", "--post", "--apply"]) == 1
    assert len(_writes(t)) == 1 and sum(c[0] == "graphql" for c in t.gh.calls) == 1 and "E_GH_HTTP" in capsys.readouterr().err  # the plan's read only
    assert led.read_bytes() == _file(OPEN).encode()


def test_a_5xx_write_is_reread_found_or_reported(tmp_path, monkeypatch, capsys):
    m, t, led = _env(tmp_path, monkeypatch, OPEN, fail=GhError("E_GH_HTTP", 502, "bad gateway"), apply=True)
    assert m.main(["decisions", "1001", "--post", "--apply"]) == 0 and len(_writes(t)) == 1 and "found on re-read" in capsys.readouterr().out
    m, t, led = _env(tmp_path, monkeypatch, OPEN, fail=GhError("E_GH_HTTP", 502, "m" * 390), apply=False)
    assert m.main(["decisions", "1001", "--post", "--apply"]) == 1
    assert "not found on re-read and not retried: check the issue" in capsys.readouterr().err and len(_writes(t)) == 1


def test_the_reread_does_not_accept_an_edited_comment_as_the_one_it_posted(tmp_path, monkeypatch, capsys):
    m, t, led = _env(tmp_path, monkeypatch, OPEN, fail=GhTimeout(write=True), apply=True)
    post = t.post

    def post_then_edit(body):
        try:
            return post(body)  # applies, then raises the timeout
        finally:
            t.items[-1][0]["lastEditedAt"] = "2026-01-02T10:00:00Z"  # someone edited the comment between the write and the re-read: it is no longer what was previewed
    t.gh.on_rest("POST", POSTS, post_then_edit)
    assert m.main(["decisions", "1001", "--post", "--apply"]) == 1
    assert "not found on re-read and not retried" in capsys.readouterr().err and len(_writes(t)) == 1
    assert led.read_bytes() == _file(OPEN).encode()


def test_the_reread_must_find_the_exact_text_not_just_the_marker(tmp_path, monkeypatch, capsys):
    older = f"{M} {_sha('x')}\nx"
    m, t, led = _env(tmp_path, monkeypatch, OPEN, items=[comment(4, older)], fail=GhTimeout(write=True), apply=False)
    assert m.main(["decisions", "1001", "--post", "--apply"]) == 1
    assert "found on re-read" not in capsys.readouterr().out and len(_writes(t)) == 1


def test_a_write_before_the_gate_armed_it_is_not_treated_as_an_unknown_outcome(tmp_path, monkeypatch):
    m, t, led = _env(tmp_path, monkeypatch, OPEN)
    plan = decisions.plan_post(t.gh, REF, decisions.parse_decisions(led.read_text()))
    n = len(t.gh.calls)
    with pytest.raises(WriteNotArmed):
        decisions.apply_post(t.gh, REF, plan, led)  # not armed: no re-read, no second attempt
    assert len(t.gh.calls) == n and led.read_bytes() == _file(OPEN).encode()


def test_unresolvable_identity_refuses_without_a_write(tmp_path, monkeypatch, capsys):
    m, t, led = _env(tmp_path, monkeypatch, OPEN)

    def broken():
        raise GhError("E_GH_HTTP", 403, "forbidden")
    t.gh.viewer = broken
    assert m.main(["decisions", "1001", "--post", "--apply"]) == 2
    assert "cannot tell which GitHub comments are yours" in capsys.readouterr().err and _writes(t) == []


def test_a_dropped_comment_makes_an_add_refuse(tmp_path, monkeypatch, capsys):
    items = [comment(5, "x")]
    items[0][1]["node_id"] = "IC_other"  # the two reads disagree: the comment is dropped with a note, and could have been our own
    m, t, led = _env(tmp_path, monkeypatch, OPEN, items=items)
    assert m.main(["decisions", "1001", "--post", "--apply"]) == 2
    assert "could not be read reliably" in capsys.readouterr().err and _writes(t) == []


def test_oversized_comment_is_refused_in_the_preview(tmp_path, monkeypatch, capsys):
    m, t, led = _env(tmp_path, monkeypatch, "\n".join(f"- [ ] D{n}: {'x' * 600} → _(open)_ · kind: decision · why: w" for n in range(1, 140)))
    for flags in (["--post"], ["--post", "--apply"]):
        assert m.main(["decisions", "1001", *flags]) == 2
        out, err = capsys.readouterr()
        assert "60,000" in err and _writes(t) == [] and "D1 (" not in out
    # the escaping can make a short line long: the cap is on the final body
    m, t, led = _env(tmp_path, monkeypatch, "\n".join(f"- [ ] D{n}: {'&' * 600} → _(open)_ · kind: decision · why: w" for n in range(1, 40)))
    assert m.main(["decisions", "1001", "--post"]) == 2 and "60,000" in capsys.readouterr().err


def test_ghp_decisions_add_and_post_work_for_a_feature_id(tmp_path, monkeypatch, capsys):
    """R§13 AC 9 (ado `test_ado_decisions_add_and_post_work_for_a_feature_id`, carried over from T12): the existing verbs take any issue number; the comment goes to the
    Feature issue, as for a story."""
    _setup(tmp_path, monkeypatch)
    posted = []
    gh = FakeGh(viewer=Viewer(ME, "octo-author", ["repo"]))
    gh.on_graphql("userContentEdits", {"repository": {"issue": {"comments": {"pageInfo": {"hasNextPage": False, "endCursor": "c"}, "nodes": []}}}})
    gh.on_rest("GET", "repos/octo/demo/issues/1201/comments?per_page=100&page=1", [])
    gh.on_rest("POST", "repos/octo/demo/issues/1201/comments", lambda b: (posted.append(b["body"]), {"id": 555})[1])
    run = lambda *argv: cli.main(["decisions", "1201", *argv], gh=gh)  # noqa: E731
    assert run("--add", "Points expire after 12 months", "--kind", "decision", "--why", "Loyalty product owner said so") == 0
    capsys.readouterr()
    assert (tmp_path / "dod" / "Decisions_1201.md").exists() and not (tmp_path / "dod" / "Decisions_1001.md").exists()
    assert run("--post", "--plan-out", "p.md") == 0
    assert "Points expire after 12 months" in (tmp_path / "p.md").read_text(encoding="utf-8") and "issue #1201" in (tmp_path / "p.md").read_text(encoding="utf-8")
    assert posted == [] and not gh.armed  # the preview sends nothing
    capsys.readouterr()
    assert run("--post", "--apply", "--expect-plan", "p.md") == 0
    assert "posted comment 555" in capsys.readouterr().out
    assert len(posted) == 1 and "D1 (decision): Points expire after 12 months" in posted[0] and posted[0].startswith(decisions.MARKER)
    assert decisions.parse_decisions((tmp_path / "dod" / "Decisions_1201.md").read_text(encoding="utf-8"))["open"][0]["posted"]["comment_id"] == 555


# ---- `--if-any`: a missing decisions file is a no-op for --post (the workflow's decisions loop) ----
def test_if_any_missing_file_is_a_noop_exit_0_and_writes_nothing(tmp_path, monkeypatch, capsys):
    m, t, led = _env(tmp_path, monkeypatch, OPEN)
    led.unlink()
    for argv in (["decisions", "1001", "--post", "--if-any"], ["decisions", "1001", "--if-any", "--plan-out", "p.md"]):
        assert m.main(argv) == 0
        out = capsys.readouterr().out
        assert "nothing to post" in out and "no decisions file" in out
    assert "nothing to post" in (tmp_path / "p.md").read_text(encoding="utf-8")
    assert m.main(["decisions", "1001", "--post", "--if-any", "--apply", "--expect-plan", "p.md"]) == 0
    capsys.readouterr()
    assert _writes(t) == [] and t.gh.calls == [] and not led.exists()  # the apply never creates the file either, and nothing was read


def test_if_any_apply_refuses_when_the_file_appeared_after_the_empty_preview(tmp_path, monkeypatch, capsys):
    m, t, led = _env(tmp_path, monkeypatch, OPEN)
    led.unlink()
    assert m.main(["decisions", "1001", "--post", "--if-any", "--plan-out", "p.md"]) == 0
    led.write_text(_file(OPEN), encoding="utf-8")  # a decision was recorded between the preview and the apply
    capsys.readouterr()
    assert m.main(["decisions", "1001", "--post", "--if-any", "--apply", "--expect-plan", "p.md"]) == 2
    assert "plan changed since approval" in capsys.readouterr().err and "D1" in (tmp_path / "p.md.new").read_text(encoding="utf-8")
    assert _writes(t) == []


def test_if_any_with_a_file_behaves_like_plain_post_and_without_it_a_missing_file_still_exits_2(tmp_path, monkeypatch, capsys):
    m, t, led = _env(tmp_path, monkeypatch, OPEN)
    assert m.main(["decisions", "1001", "--post", "--if-any"]) == 0
    out = capsys.readouterr().out
    assert "ADD a new comment" in out and "no decisions file" not in out
    led.unlink()
    assert m.main(["decisions", "1001", "--post"]) == 2
    assert "no decisions file" in capsys.readouterr().err


def test_if_any_flag_combinations_exit_2(tmp_path, monkeypatch):
    m, t, led = _env(tmp_path, monkeypatch, OPEN)
    for argv in (["decisions", "1001", "--if-any"], ["decisions", "1001", "--if-any", "--add", "x", "--kind", "decision"],
                 ["decisions", "1001", "--if-any", "--resolve", "D1", "--validated"]):
        assert _rc(lambda: m.main(argv)) == 2, argv


# ---- AC3: `--sync` is a no-op in terminal mode until T28 builds the comment mode ----
def test_sync_is_noop_in_terminal_mode(tmp_path, monkeypatch, capsys):
    m, t, led = _env(tmp_path, monkeypatch, OPEN)
    before = led.read_bytes()
    for argv in (["decisions", "1001", "--sync"], ["decisions", "1001", "--sync", "--if-comment-mode"]):
        assert m.main(argv) == 0
        out = json.loads(capsys.readouterr().out)
        assert out == {"id": 1001, "synced": False, "note": decisions.E_NOT_COMMENT_MODE}
    assert "terminal mode" in decisions.E_NOT_COMMENT_MODE and "nothing to sync" in decisions.E_NOT_COMMENT_MODE
    assert t.gh.calls == [] and not t.gh.armed and led.read_bytes() == before  # no read, no write, not even the viewer
    led.unlink()
    assert m.main(["decisions", "1001", "--sync"]) == 0 and json.loads(capsys.readouterr().out)["synced"] is False  # the note needs no file


def test_sync_flag_combinations_exit_2(tmp_path, monkeypatch):
    m, t, led = _env(tmp_path, monkeypatch, OPEN)
    for argv in (["--sync", "--post"], ["--sync", "--apply"], ["--sync", "--plan-out", "p.md"], ["--sync", "--if-any"], ["--sync", "--add", "t", "--kind", "decision"],
                 ["--sync", "--resolve", "D1", "--validated"], ["--sync", "--kind", "decision"], ["--if-comment-mode"], ["--post", "--if-comment-mode"]):
        assert _rc(lambda: m.main(["decisions", "1001", *argv])) == 2, argv
    assert t.gh.calls == [] and not (tmp_path / "p.md").exists()


# ---- `--resolve D<n> --validated|--rejected` (a human, in a terminal) ----
KEPT = "- [ ] D4: Wrap it → _(open)_ · kind: override · why: skip the rule · posted: 5:abcdef12 · used: 2026-01-01"


def _tty(monkeypatch, on=True):
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: on)


def _resolve(m, *extra):
    return m.main(["decisions", "1001", "--resolve", *extra])


def test_resolve_validates_only_that_line(tmp_path, monkeypatch, capsys):
    m, fake, led = _env(tmp_path, monkeypatch, "\n".join([OPEN, VAL, REJ]))
    _tty(monkeypatch)
    before = led.read_text(encoding="utf-8").splitlines()
    assert _resolve(m, "D1", "--validated") == 0
    assert json.loads(capsys.readouterr().out)["resolved"] == "D1"
    after = led.read_text(encoding="utf-8").splitlines()
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(before) == len(after) and len(changed) == 1 and after[changed[0]].startswith("- [x] D1:")
    d = decisions.parse_decisions(led.read_text(encoding="utf-8"))
    assert d["unparsed"] is False and [x["id"] for x in d["validated"]] == ["D1", "D2"] and d["open"] == []
    assert d["validated"][0]["by"] == "PO" and d["validated"][0]["note"] == ""
    assert fake.gh.calls == [] and list(led.parent.glob("*.tmp")) == []  # a local write: no request at all


def test_resolve_rejected_then_validated_allowed(tmp_path, monkeypatch):
    m, fake, led = _env(tmp_path, monkeypatch, "\n".join([OPEN, REJ]))
    _tty(monkeypatch)
    assert _resolve(m, "D1", "--rejected") == 0
    assert _resolve(m, "D3", "--validated", "--note", "changed my mind") == 2  # already resolved: needs --force
    assert _resolve(m, "D3", "--validated", "--note", "changed my mind", "--force") == 0
    d = decisions.parse_decisions(led.read_text(encoding="utf-8"))
    assert [x["id"] for x in d["rejected"]] == ["D1"] and [x["id"] for x in d["validated"]] == ["D3"]
    assert d["validated"][0]["note"] == "changed my mind"


def test_refuses_without_tty(tmp_path, monkeypatch, capsys):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN)
    _tty(monkeypatch, False)
    before = led.read_bytes()
    assert _resolve(m, "D1", "--validated") == 2
    assert "own terminal" in capsys.readouterr().err and led.read_bytes() == before
    led.unlink()  # checked before the ledger is even looked for
    assert _resolve(m, "D1", "--validated") == 2
    err = capsys.readouterr().err
    assert "own terminal" in err and "ghp dod" not in err


def test_unknown_id_exit_2(tmp_path, monkeypatch, capsys):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN)
    _tty(monkeypatch)
    before = led.read_bytes()
    for bad in ("D9", "1", "d1", "D1x"):
        assert _resolve(m, bad, "--validated") == 2
        assert "no decision" in capsys.readouterr().err and led.read_bytes() == before


def test_by_and_note_validated(tmp_path, monkeypatch, capsys):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN)
    _tty(monkeypatch)
    before = led.read_bytes()
    bad = [("--by", "Dana; PO"), ("--by", "x" * 41), ("--by", ""), ("--by", "P\nO"), ("--by", "P O"), ("--by", "PO\x1b"),
           ("--note", "n" * 301), ("--note", "a\nb"), ("--note", "a\rb"), ("--note", "a b"), ("--note", "a b"),
           ("--note", "a\x1b[8mb"), ("--note", "a\x00b"), ("--note", "a\x85b"), ("--note", "a\x7fb"),
           ("--note", "x · kind: assumption"), ("--note", "x → rejected")]
    for flag, val in bad:
        assert _resolve(m, "D1", "--validated", flag, val) == 2, (flag, val)
        assert capsys.readouterr().err.startswith("error:") and led.read_bytes() == before, (flag, val)
    assert _resolve(m, "D1", "--validated", "--by", "Product Owner 2", "--note", "n" * 300) == 0
    d = decisions.parse_decisions(led.read_text(encoding="utf-8"))["validated"][0]
    assert d["by"] == "Product Owner 2" and d["note"] == "n" * 300


def test_resolve_keeps_posted_marker_and_other_markers(tmp_path, monkeypatch):
    m, fake, led = _env(tmp_path, monkeypatch, KEPT + "\n" + VAL)
    _tty(monkeypatch)
    old = decisions.parse_decisions(led.read_text(encoding="utf-8"))["open"][0]
    assert _resolve(m, "D4", "--rejected", "--note", "no") == 0
    md = led.read_text(encoding="utf-8")
    line = next(x for x in md.splitlines() if "D4:" in x)
    assert line == "- [x] D4: Wrap it → rejected · by: PO · note: no · kind: override · why: skip the rule · posted: 5:abcdef12 · used: 2026-01-01"
    new = decisions.parse_decisions(md)["rejected"][0]
    assert decisions.render_line(new) == line
    assert {k: new[k] for k in ("id", "kind", "text", "why", "posted", "used")} == {k: old[k] for k in ("id", "kind", "text", "why", "posted", "used")}


def test_resolve_keeps_line_terminators(tmp_path, monkeypatch):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN + " " + VAL)
    _tty(monkeypatch)
    assert _resolve(m, "D1", "--validated") == 0
    md = led.read_text(encoding="utf-8")
    assert " - [x] D2:" in md and decisions.parse_decisions(md)["unparsed"] is False
    led.write_bytes(_file(OPEN + "\n" + VAL).replace("\n", "\r\n").encode("utf-8"))
    before = led.read_bytes()
    assert _resolve(m, "D2", "--rejected", "--by", "PO", "--force") == 0
    after = led.read_bytes()
    assert after.count(b"\r\n") == before.count(b"\r\n") == after.count(b"\n")
    assert [x["id"] for x in decisions.parse_decisions(after.decode("utf-8"))["rejected"]] == ["D2"]


def test_resolved_line_over_max_line_is_refused(tmp_path, monkeypatch, capsys):
    long_open = f"- [ ] D1: {'t' * 1800} → _(open)_ · kind: decision · why: w"
    m, fake, led = _env(tmp_path, monkeypatch, long_open)
    _tty(monkeypatch)
    before = led.read_bytes()
    assert _resolve(m, "D1", "--validated", "--note", "n" * 250) == 2
    assert str(decisions.MAX_LINE) in capsys.readouterr().err and led.read_bytes() == before


def test_resolve_flag_combinations_exit_2(tmp_path, monkeypatch):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN)
    _tty(monkeypatch)
    before = led.read_bytes()
    for argv in (["--resolve", "D1", "--validated", "--post"], ["--resolve", "D1", "--validated", "--apply"],
                 ["--resolve", "D1", "--validated", "--plan-out", "p.md"],
                 ["--resolve", "D1", "--validated", "--expect-plan", "p.md"],
                 ["--resolve", "D1"], ["--resolve", "D1", "--validated", "--rejected"],
                 ["--validated"], ["--by", "PO"], ["--note", "x"], ["--force"]):
        assert _rc(lambda: m.main(["decisions", "1001", *argv])) == 2, argv
    assert led.read_bytes() == before and not (tmp_path / "p.md").exists()


def test_resolve_write_is_atomic_and_refuses_a_ledger_changed_meanwhile(tmp_path, monkeypatch, capsys):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN)
    _tty(monkeypatch)
    before = led.read_bytes()
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    assert _resolve(m, "D1", "--validated") == 2
    assert "disk full" in capsys.readouterr().err and led.read_bytes() == before and list(led.parent.glob("*.tmp")) == []
    monkeypatch.setattr(os, "replace", real)
    pure = decisions.resolve
    monkeypatch.setattr(decisions, "resolve", lambda *a: (led.write_text(led.read_text() + "edited\n"), pure(*a))[1])
    assert _resolve(m, "D1", "--validated") == 2
    assert "changed" in capsys.readouterr().err and "edited" in led.read_text() and "[ ] D1" in led.read_text()


# ---- V3-4 fix round 1 ----
def test_note_with_comment_or_fence_markers_is_refused(tmp_path, monkeypatch, capsys):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN + "\n" + OPEN2)
    _tty(monkeypatch)
    before = led.read_bytes()
    for note in ("x <!-- y", "x --> y", "<!--", "a ``` b", "~~~", "x ````"):
        assert _resolve(m, "D1", "--validated", "--note", note) == 2, note
        assert capsys.readouterr().err.startswith("error:") and led.read_bytes() == before, note


def test_whole_ledger_is_verified_before_writing(monkeypatch):
    md = _file(OPEN + "\n" + OPEN2)
    monkeypatch.setattr(decisions, "_MARKUP", re.compile("(?!)"))  # as if the note check missed `<!--`
    with pytest.raises(ValueError, match="D1"):  # the line alone reads back, the ledger does not
        decisions.resolve(md, "D1", "validated", "PO", "x <!-- y")
    assert "- [x] D1:" in decisions.resolve(md, "D1", "validated", "PO", "fine")  # the happy path still works


def test_inline_comment_in_the_target_line_is_refused(tmp_path, monkeypatch, capsys):
    line = "- [ ] D1: Use <!-- hidden --> paging → _(open)_ · kind: decision · why: w"
    m, fake, led = _env(tmp_path, monkeypatch, line + "\n" + OPEN2)
    _tty(monkeypatch)
    before = led.read_bytes()
    assert _resolve(m, "D1", "--validated") == 2
    assert "D1" in capsys.readouterr().err and led.read_bytes() == before
    assert _resolve(m, "D4", "--validated") == 0  # a comment in another line does not matter


def test_re_resolving_needs_force(tmp_path, monkeypatch, capsys):
    m, fake, led = _env(tmp_path, monkeypatch, VAL)
    _tty(monkeypatch)
    before = led.read_bytes()
    assert _resolve(m, "D2", "--rejected") == 2
    assert "--force" in capsys.readouterr().err and led.read_bytes() == before
    assert _resolve(m, "D2", "--rejected", "--force") == 0
    assert decisions.parse_decisions(led.read_text(encoding="utf-8"))["rejected"][0]["id"] == "D2"


def test_empty_note_leaves_no_double_space(tmp_path, monkeypatch):
    m, fake, led = _env(tmp_path, monkeypatch, KEPT)
    _tty(monkeypatch)
    assert _resolve(m, "D4", "--validated") == 0
    line = next(x for x in led.read_text(encoding="utf-8").splitlines() if "D4:" in x)
    assert "  " not in line
    assert decisions.render_line(decisions.parse_decisions(led.read_text(encoding="utf-8"))["validated"][0]) == line


def test_temp_file_is_unique_and_the_mode_is_kept(tmp_path, monkeypatch):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN)
    _tty(monkeypatch)
    led.chmod(0o640)
    other = led.with_name(led.name + ".tmp")
    other.write_text("not mine", encoding="utf-8")
    assert _resolve(m, "D1", "--validated") == 0
    assert other.read_text(encoding="utf-8") == "not mine" and (led.stat().st_mode & 0o777) == 0o640
    other.unlink()
    assert list(led.parent.glob("*.tmp")) == []


def test_has_decision_lines():
    assert decisions.has_decision_lines(_file(OPEN)) and decisions.has_decision_lines(_file(VAL))
    assert decisions.has_decision_lines(_file("- [ ] D4 not in the grammar"))  # malformed still counts
    assert not decisions.has_decision_lines(_file("- [ ] Q1: which? → _(open)_ · owner: lead"))
    assert not decisions.has_decision_lines(_file("```\n" + OPEN + "\n```"))  # a fenced example is not a line
    assert not decisions.has_decision_lines(_file("<!-- " + OPEN + " -->"))
    assert not decisions.has_decision_lines("# DoD\nno section 2\n")



# ---- the decisions live in <dod_path>/Decisions_<n>.md; `--add` writes them ----
def _add_env(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)  # a local write: no token, no request (`main` runs over an empty FakeGh that fails on any call)
    return tmp_path / "dod" / "Decisions_1001.md"


def _add(*extra, text="Use cursor paging", kind="decision"):
    return main(["decisions", "1001", "--add", text, "--kind", kind, *extra])


def test_add_creates_file_and_appends_next_id(tmp_path, monkeypatch, capsys):
    f = _add_env(tmp_path, monkeypatch)
    assert _add("--why", "the story names no page size") == 0
    assert json.loads(capsys.readouterr().out) == {"id": "D1", "file": str(Path("dod") / "Decisions_1001.md")}
    assert f.read_text(encoding="utf-8") == decisions.render_file(1001) + OPEN + "\n"
    # next id = max existing + 1, even with gaps, a resolved line and a duplicate id
    f.write_text(decisions.render_file(1001) + "\n".join([OPEN, VAL.replace("D2", "D7"), OPEN.replace("paging", "x")]) + "\n")
    before = f.read_text()
    assert _add("--why", "w", text="Retry twice", kind="assumption") == 0
    assert json.loads(capsys.readouterr().out)["id"] == "D8"
    after = f.read_text()
    assert after == before + "- [ ] D8: Retry twice → _(open)_ · kind: assumption · why: w\n"
    d = decisions.parse_decisions(after)
    assert d["unparsed"] is False and [x["id"] for x in d["open"]][-1] == "D8"
    assert list(f.parent.glob("*.tmp")) == []


def test_add_defaults_why_adds_a_missing_final_newline_and_keeps_crlf(tmp_path, monkeypatch):
    f = _add_env(tmp_path, monkeypatch)
    assert _add() == 0
    assert "· kind: decision · why: unspecified\n" in f.read_text()
    f.write_bytes((decisions.render_file(1001) + OPEN).replace("\n", "\r\n").encode("utf-8"))  # no final newline
    assert _add(text="b", kind="assumption") == 0
    raw = f.read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n") and b"D2: b" in raw
    assert decisions.parse_decisions(raw.decode("utf-8"))["unparsed"] is False


def test_add_numbers_after_a_malformed_line_too(tmp_path, monkeypatch):
    f = _add_env(tmp_path, monkeypatch)
    f.parent.mkdir()
    f.write_text(decisions.render_file(1001) + "- [ ] D5 not in the grammar\n", encoding="utf-8")
    assert _add() == 0
    assert "D6: Use cursor paging" in f.read_text()


def test_add_scrubs_mentions_and_emails_and_trims(tmp_path, monkeypatch):
    f = _add_env(tmp_path, monkeypatch)
    assert _add("--why", "ask @dana", text="  mail d@contoso.example <b>x</b> ") == 0
    line = f.read_text().splitlines()[-1]
    assert line == f"- [ ] D1: mail d{cmod.NO_MENTION} <b>x</b> → _(open)_ · kind: decision · why: ask {cmod.NO_MENTION}"


def test_add_refuses_control_chars_comment_openers_and_long_text(tmp_path, monkeypatch, capsys):
    f = _add_env(tmp_path, monkeypatch)
    secret = "TOPSECRETTEXT"
    bad_text = ["a\nb", "a\rb", "a\tb", "a\x1bb", "a\x00b", "a\x7fb", "a\x85b", "a\u2028b", "a\u2029b",
                "x <!-- y", "x --> y", "a ``` b", "~~~", "x ````", "", "   ", "x" * 1501]
    for t in bad_text:
        assert _add(text=secret + t) == 2 if t.strip() else _add(text=t) == 2, repr(t)
        err = capsys.readouterr().err
        assert err.startswith("error:") and secret not in err and not f.exists(), repr(t)
    for why in ("a\nb", "a\x1bb", "w <!-- x", "w ```", "w" * 301, "w · kind: assumption"):
        assert _add("--why", why, text=secret) == 2, repr(why)
        err = capsys.readouterr().err
        assert err.startswith("error:") and secret not in err and "wwww" not in err and not f.exists(), repr(why)
    for kind in ("override", "Decision", "", "x"):
        assert _add(text=secret, kind=kind) == 2
        err = capsys.readouterr().err
        assert "decision or assumption" in err and secret not in err and not f.exists()
    assert _add(text="x" * 1500, kind="decision") == 0  # the limits themselves are fine
    assert _add("--why", "w" * 300) == 0


def test_add_refuses_when_the_new_line_would_be_hidden_and_writes_nothing(tmp_path, monkeypatch, capsys):
    f = _add_env(tmp_path, monkeypatch)
    f.parent.mkdir()
    f.write_text(decisions.render_file(1001) + "```\n" + OPEN + "\n", encoding="utf-8")  # unclosed fence
    before = f.read_bytes()
    assert _add() == 2
    assert "decisions file" in capsys.readouterr().err and f.read_bytes() == before and list(f.parent.glob("*.tmp")) == []
    f.write_bytes(b"\xff\xfe")
    assert _add() == 2 and f.read_bytes() == b"\xff\xfe"


def test_add_write_is_atomic(tmp_path, monkeypatch, capsys):
    f = _add_env(tmp_path, monkeypatch)
    assert _add() == 0
    before = f.read_bytes()
    f.chmod(0o640)
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    assert _add(text="more") == 2
    assert "disk full" in capsys.readouterr().err and f.read_bytes() == before and list(f.parent.glob("*.tmp")) == []
    monkeypatch.setattr(os, "replace", real)
    assert _add(text="more") == 0 and (f.stat().st_mode & 0o777) == 0o640
    f2 = f.with_name("Decisions_1002.md")
    assert main(["decisions", "1002", "--add", "t", "--kind", "decision"]) == 0 and f2.exists()


def test_add_flag_combinations_exit_2(tmp_path, monkeypatch):
    f = _add_env(tmp_path, monkeypatch)
    for argv in (["--add", "t"], ["--add", "t", "--kind", "decision", "--post"], ["--add", "t", "--kind", "decision", "--apply"],
                 ["--add", "t", "--kind", "decision", "--resolve", "D1", "--validated"],
                 ["--add", "t", "--kind", "decision", "--plan-out", "p.md"],
                 ["--add", "t", "--kind", "decision", "--validated"], ["--add", "t", "--kind", "decision", "--by", "PO"],
                 ["--kind", "decision"], ["--why", "w"]):
        assert _rc(lambda: main(["decisions", "1001", *argv])) == 2, argv
    assert not f.exists()


def test_post_and_resolve_use_the_file(tmp_path, monkeypatch, capsys):
    m, fake, f = _env(tmp_path, monkeypatch, OPEN + "\n" + OPEN2)
    other = tmp_path / "dod" / "DoD_1001.md"
    other.write_text("# DoD\n" + OPEN.replace("D1", "D9") + "\n", encoding="utf-8")  # a D line in a ledger is never read
    ledger_before = other.read_bytes()
    assert m.main(["decisions", "1001", "--post", "--apply"]) == 0
    assert "posted comment 777" in capsys.readouterr().out
    md = f.read_text(encoding="utf-8")
    assert f"posted: 777:{H1}" in md and f"posted: 777:{H2}" in md
    _tty(monkeypatch)
    assert _resolve(m, "D1", "--validated") == 0
    assert "- [x] D1:" in f.read_text(encoding="utf-8")
    assert _resolve(m, "D9", "--validated") == 2  # only in the ledger
    assert other.read_bytes() == ledger_before
    f.unlink()
    assert m.main(["decisions", "1001", "--post"]) == 2 and _resolve(m, "D1", "--validated") == 2
    assert "`ghp decisions 1001 --add " in capsys.readouterr().err


def test_add_counts_ids_hidden_in_comments_and_fences(tmp_path, monkeypatch):
    f = _add_env(tmp_path, monkeypatch)
    f.parent.mkdir()
    f.write_text(decisions.render_file(1001) + f"<!-- {VAL.replace('D2', 'D12')} -->\n```\n{REJ.replace('D3', 'D20')}\n```\n" + OPEN + "\n",
                 encoding="utf-8")
    assert _add() == 0
    assert "- [ ] D21: Use cursor paging" in f.read_text(encoding="utf-8")



# ---- `supersedes D<n>: ` answers a rejected (or open) decision ----
def _dl(n, text, status="open", kind="decision", posted=False):
    """One decisions-file line (exact grammar) for `D<n>`; `posted`: with the marker the PO's thread would give it."""
    d = {"id": f"D{n}", "kind": kind, "text": text, "why": "w", "status": status, "by": "" if status == "open" else "PO",
         "note": "", "posted": None, "used": ""}
    if posted:
        d["posted"] = {"comment_id": 7, "hash": decisions.marker_hash(_first(d))}
    return decisions.render_line(d) + "\n"


def _first(d):
    """The entry `parse_decisions` reads back from `d` (so a supersedes prefix is seen)."""
    p = decisions.parse_decisions(decisions.render_line(d) + "\n")
    return (p["open"] + p["validated"] + p["rejected"])[0]


def _parse(*lines):
    return decisions.parse_decisions(decisions.render_file(1001) + "".join(lines))


def _ids(parsed):
    return [d["id"] for k in ("open", "validated", "rejected") for d in parsed[k]]


def test_parse_extracts_supersedes_only_from_the_exact_prefix():
    r = _parse(_dl(3, "x", "rejected"), _dl(5, "supersedes D3: use v2"), _dl(6, "supersedes D999999: a"))
    [d5, d6] = r["open"]
    assert d5["supersedes"] == "D3" and d5["text"] == "supersedes D3: use v2" and d6["supersedes"] == "D999999"
    assert "supersedes" not in r["rejected"][0]  # no key at all on an ordinary line: v0.2 listings stay byte-identical
    for text in ("Supersedes D3: a", "supersedes d3: a", "supersedes  D3: a", "supersedes D3 : a", "supersedes D3:a", "supersedes D3 a",
                 "please supersedes D3: a", "supersedes D1234567: a", "supersedes D: a", "supersedes D３: a", "supersedes D٣: a",
                 "supersedes D3:"):
        assert _parse(_dl(5, text))["open"][0].get("supersedes") is None, text
    line = _dl(5, "supersedes D3: use v2").rstrip()
    assert decisions.render_line(_parse(line)["open"][0]) == line  # the grammar is unchanged: it round-trips


def test_effective_drops_the_superseded_entry_unless_the_superseder_is_rejected():
    base = (_dl(3, "x", "rejected"),)
    assert _ids(decisions.effective(_parse(*base, _dl(5, "supersedes D3: y")))) == ["D5"]  # open is live
    assert _ids(decisions.effective(_parse(*base, _dl(5, "supersedes D3: y", "validated")))) == ["D5"]
    assert _ids(decisions.effective(_parse(*base, _dl(5, "supersedes D3: y", "rejected")))) == ["D3", "D5"]  # re-activated
    plain = _parse(_dl(1, "a"), _dl(2, "b", "validated"), _dl(3, "c", "rejected"))
    assert decisions.effective(plain) == plain  # nothing superseded: the same entries
    assert decisions.effective(_parse(*base))["unparsed"] is False
    assert decisions.effective({**_parse(*base), "unparsed": True})["unparsed"] is True


def test_chain_and_cycle_and_self_reference_and_unknown_target():
    d3 = _dl(3, "x", "rejected")
    # chain: D7 answers D5 which answers D3; a live head clears the whole chain, a rejected head re-opens the one below it
    chain = lambda s5, s7: _parse(d3, _dl(5, "supersedes D3: a", s5), _dl(7, "supersedes D5: b", s7))
    assert _ids(decisions.effective(chain("open", "open"))) == ["D7"]
    assert _ids(decisions.effective(chain("rejected", "open"))) == ["D7"]  # D7 stands in for the rejected D5, which stood in for D3
    assert _ids(decisions.effective(chain("rejected", "rejected"))) == ["D3", "D5", "D7"]
    assert _ids(decisions.effective(chain("open", "rejected"))) == ["D5", "D7"]  # D7 rejected: D5 is back, and D5 still answers D3
    assert decisions.supersede_problem(_parse(_dl(1, "supersedes D2: a"), _dl(2, "supersedes D1: b"))) is True  # cycle
    for lines in ((_dl(2, "supersedes D2: a"),),  # self-reference
                  (_dl(1, "supersedes D2: a"), _dl(2, "b", "rejected")),  # a lower id supersedes a higher one
                  (_dl(2, "supersedes D9: a"),),  # unknown target
                  (_dl(2, "supersedes D0: a"),)):
        p = _parse(*lines)
        assert decisions.supersede_problem(p) is True and decisions.effective(p) == p, lines  # invalid edges drop nothing
    assert decisions.supersede_problem(_parse(d3, _dl(5, "supersedes D3: a"))) is False
    assert decisions.supersede_problem(_parse(_dl(1, "a"))) is False


def test_override_cannot_be_superseded():
    p = _parse(_dl(1, "skip l2", kind="override"), _dl(2, "supersedes D1: a"))
    assert decisions.supersede_problem(p) is True
    assert _ids(decisions.effective(p)) == ["D1", "D2"]  # the override stays in the set


def test_unposted_keeps_an_answered_open_decision_the_po_never_saw():
    p = _parse(_dl(3, "old", "rejected"), _dl(4, "old open"), _dl(5, "supersedes D4: new"))
    assert [d["id"] for d in decisions.unposted(p)] == ["D4", "D5"]  # answering D4 must not hide it from the PO
    assert _plan_of(p)["lines"] == ["D4", "D5"]
    p = _parse(_dl(4, "old open", posted=True), _dl(5, "supersedes D4: new"))
    assert [d["id"] for d in decisions.unposted(p)] == ["D5"]
    p = _parse(_dl(4, "old open"), _dl(5, "supersedes D4: new", "rejected"))
    assert [d["id"] for d in decisions.unposted(p)] == ["D4"]
    assert "supersedes D4: new" in _plan_of(_parse(_dl(4, "a", "rejected"), _dl(5, "supersedes D4: new")))["body"]


def _addmd(md, text="my reply", supersedes=None, kind="decision"):
    return decisions.add(md, kind, text, "w", supersedes=supersedes)


def test_add_supersedes_flag_validates_target_exists_and_is_rejected_or_open():
    md = (decisions.render_file(1001) + _dl(1, "a", "rejected") + _dl(2, "b", posted=True) + _dl(3, "c", "validated")
          + _dl(4, "skip l2", kind="override"))
    new, d_id = _addmd(md, supersedes="D1")
    assert d_id == "D5" and new.endswith("- [ ] D5: supersedes D1: my reply → _(open)_ · kind: decision · why: w\n")
    assert decisions.parse_decisions(new)["open"][-1]["supersedes"] == "D1" and not decisions.supersede_problem(decisions.parse_decisions(new))
    assert _addmd(md, supersedes="D2")[1] == "D5"  # an open target too
    for bad, msg in (("D9", "not in the decisions file"), ("D3", "rejected or open"), ("D4", "override"), ("3", "D<n>"), ("D", "D<n>"),
                     ("d1", "D<n>"), ("D1234567", "D<n>"), ("D１", "D<n>"), ("D1 ", "D<n>"), ("", "D<n>")):
        with pytest.raises(ValueError) as e:
            _addmd(md, supersedes=bad)
        assert msg in str(e.value) and "my reply" not in str(e.value), bad
    # a second LIVE superseder of the same target is refused, naming the existing one
    md2, _ = _addmd(md, supersedes="D1")
    with pytest.raises(ValueError, match="D5"):
        _addmd(md2, supersedes="D1")
    with pytest.raises(ValueError, match="post D5 first"):  # D5 is open and the PO has not seen it
        _addmd(md2, supersedes="D5")
    md2p = decisions._marked(md2, _plan_of(decisions.parse_decisions(md2))["hashes"], 7)
    assert _addmd(md2p, supersedes="D5")[1] == "D6"  # a chain is fine once D5 is posted
    # once that superseder is rejected it is not live: the target may be answered again
    md3 = md2.replace("- [ ] D5: supersedes D1: my reply → _(open)_ · kind: decision · why: w",
                      "- [x] D5: supersedes D1: my reply → rejected · by: PO · note: no")
    assert decisions.parse_decisions(md3)["rejected"][-1]["id"] == "D5"
    assert _addmd(md3, supersedes="D1")[1] == "D6"
    # the prefix cannot be typed by hand, with or without the flag
    for sup in (None, "D1"):
        with pytest.raises(ValueError, match="supersedes D<n>"):
            _addmd(md, text="supersedes D1: sneaky", supersedes=sup)
    assert decisions.add(md, "decision", "mentions supersedes D1: later", "w")[1] == "D5"  # only a leading prefix counts


def test_add_supersedes_cli(tmp_path, monkeypatch, capsys):
    f = _add_env(tmp_path, monkeypatch)
    f.parent.mkdir()
    f.write_text(decisions.render_file(1001) + _dl(1, "use v1", "rejected"))
    assert _add("--supersedes", "D1", text="use v2 as the PO asked") == 0
    assert json.loads(capsys.readouterr().out)["id"] == "D2"
    assert f.read_text().endswith("- [ ] D2: supersedes D1: use v2 as the PO asked → _(open)_ · kind: decision · why: unspecified\n")
    before = f.read_bytes()
    assert _add("--supersedes", "D1") == 2 and _add("--supersedes", "D7") == 2 and _add("--supersedes", "x") == 2
    assert f.read_bytes() == before
    err = capsys.readouterr().err
    assert "D2" in err and "not in the decisions file" in err
    assert _rc(lambda: main(["decisions", "1001", "--supersedes", "D1"])) == 2 and f.read_bytes() == before


def test_a_superseder_marker_covers_its_id_so_a_copy_on_another_id_does_not_match():
    d9 = _first({"id": "D9", "kind": "decision", "text": "supersedes D3: x", "why": "w", "status": "open", "by": "", "note": "",
                 "posted": None, "used": ""})
    d10 = {**d9, "id": "D10"}
    assert decisions.marker_hash(d9) != decisions.marker_hash(d10)
    plain = {**d9, "text": "x", "supersedes": None}
    plain.pop("supersedes")
    assert decisions.marker_hash(plain) == decisions.line_hash("x")  # an ordinary line: text only, as before


def test_supersede_unposted_lists_every_superseder_and_every_open_target_without_a_matching_marker():
    f = decisions.supersede_unposted
    base = (_dl(3, "x", "rejected"),)
    for status, kind in (("open", "decision"), ("validated", "decision"), ("rejected", "decision"), ("open", "assumption"),
                         ("validated", "assumption")):
        assert [d["id"] for d in f(_parse(*base, _dl(9, "supersedes D3: y", status, kind)))] == ["D9"], (status, kind)
        assert f(_parse(*base, _dl(9, "supersedes D3: y", status, kind, posted=True))) == [], (status, kind)
    # a copy of a posted line's text and marker on another id does not match
    posted = _dl(9, "supersedes D3: y", "rejected", posted=True)
    assert [d["id"] for d in f(_parse(*base, posted, posted.replace("D9", "D10")))] == ["D10"]
    # an open target the effective set drops needs its own marker; an ordinary unposted open line is nobody's business here
    assert [d["id"] for d in f(_parse(_dl(4, "old"), _dl(5, "supersedes D4: n", posted=True)))] == ["D4"]
    assert f(_parse(_dl(4, "old", posted=True), _dl(5, "supersedes D4: n", posted=True))) == []
    assert f(_parse(_dl(4, "old"), _dl(5, "supersedes D4: n", "rejected", posted=True))) == []  # not dropped: re-activated
    assert f(_parse(_dl(1, "plain"), _dl(2, "plain too", "rejected"))) == []
    chain = _parse(base[0], _dl(5, "supersedes D3: a", "rejected"), _dl(7, "supersedes D5: b", posted=True))
    assert [d["id"] for d in f(chain)] == ["D5"]  # a chain linked by a line that was never posted


def test_masked_by_names_the_standing_superseder():
    p = _parse(_dl(3, "x", "rejected"), _dl(5, "supersedes D3: a"), _dl(6, "supersedes D3: b", "rejected"))
    assert decisions.masked_by(p, "D3") == "D5" and decisions.masked_by(p, "D5") is None and decisions.masked_by(p, "D6") is None
    assert decisions.masked_by(_parse(_dl(3, "x", "rejected"), _dl(5, "supersedes D3: a", "rejected")), "D3") is None


def test_resolve_on_a_superseded_decision_prints_a_notice_and_changes_nothing_else(tmp_path, monkeypatch, capsys):
    m, fake, led = _env(tmp_path, monkeypatch, _dl(3, "x").rstrip() + "\n" + _dl(5, "supersedes D3: a").rstrip() + "\n" + _dl(6, "plain").rstrip())
    _tty(monkeypatch)
    assert _resolve(m, "D3", "--rejected") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["notice"] == "D3 is superseded by D5: this verdict is masked while D5 stands (a rejected D5 would bring it back)"
    d = decisions.parse_decisions(led.read_text(encoding="utf-8"))
    assert [x["id"] for x in d["rejected"]] == ["D3"]  # the verdict is recorded as always
    assert _resolve(m, "D6", "--validated") == 0
    assert "notice" not in json.loads(capsys.readouterr().out)


# ---- P4-9: the `via` key (a verdict that `--sync` took from an approver's comment; T28 writes it, the grammar and `resolve` read it now) ----
S1 = "- [ ] D1: Use cursor paging → _(open)_ · kind: decision · why: the story names no page size"
S2 = "- [ ] D2: Cap at 50 rows → _(open)_ · kind: assumption · why: the story is silent"
S3 = "- [ ] D3: Skip audit log → _(open)_ · kind: decision · why: out of scope"
def test_via_key_is_validated():
    ok = "- [x] D2: Cap → validated · by: approver · note: fine · via: 123"
    d = decisions.parse_decisions(_file(ok))["validated"][0]
    assert d["via"] == "123" and decisions.render_line(d) == ok
    for bad in ("- [x] D2: Cap → validated · by: approver · note: fine · via: 0123", "- [x] D2: Cap → validated · by: PO · via: abc",
                "- [x] D2: Cap → validated · by: PO · via: ", "- [x] D2: Cap → validated · by: PO · via: 12 3", "- [x] D2: Cap → validated · by: PO · via: 1234567890123",
                "- [x] D2: Cap → validated · by: PO · via: 1 · via: 2", "- [x] D2: Cap → validated · by: PO · via: ١٢٣",
                "- [ ] D2: Cap → _(open)_ · kind: decision · why: w · via: 12"):
        r = decisions.parse_decisions(_file(bad))
        assert r["unparsed"] is True and not (r["open"] or r["validated"] or r["rejected"]), bad
    with pytest.raises(ValueError):
        decisions.resolve(_file(S1), "D1", "validated", "approver", "", False, "12; rm")
    # a terminal verdict over a synced one drops the via key: it no longer claims a comment
    synced = decisions.resolve(_file(S1), "D1", "validated", "approver", "", False, "12")
    assert " · via: 12" in synced and " · via:" not in decisions.resolve(synced, "D1", "rejected", "PO", "no", True)


def test_via_key_keeps_existing_fixture_splits(monkeypatch):
    """Every decision line that the existing tests write parses the same before and after `_KEYS` gained `via`."""
    root = Path(__file__).parent
    found = set()
    for f in sorted(root.glob("test_*.py")):
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"- \[[ xX]\] D\d+[^\"'\n]*", text.split("# ---- P4-9")[0]):  # the P4-9 tests themselves are about `via`
            found.add(m.group())
    found |= {OPEN, VAL, REJ, KEPT, OPEN2, S1, S2, S3, "- [x] D2: Cap → validated · by: PO · note: see via: 12 and so on", "- [x] D2: Cap → validated · by: PO · note: a via: b"}
    assert len(found) > 40
    new = {ln: decisions.parse_decisions(_file(ln)) for ln in found}
    keys = tuple(k for k in decisions._KEYS if k != "via")
    assert keys != decisions._KEYS
    monkeypatch.setattr(decisions, "_KEYS", keys)
    monkeypatch.setattr(decisions, "_SEP", re.compile(r"\s*·\s*(?=(?:" + "|".join(keys) + r")\s*:)"))
    for ln in sorted(found):
        assert decisions.parse_decisions(_file(ln)) == new[ln], ln



def test_dod_compiles_without_a_warning():
    import warnings
    src = Path(decisions.__file__).with_name("dod.py")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        compile(src.read_text(encoding="utf-8"), str(src), "exec")


# ---- `_read`: the checks run on the open descriptor ----
def test_read_allows_exactly_1_mib_and_refuses_one_byte_more(tmp_path):
    f = tmp_path / "Decisions_1.md"
    f.write_bytes(b"a" * (1 << 20))
    assert len(decisions._read(f)) == 1 << 20
    f.write_bytes(b"a" * ((1 << 20) + 1))
    with pytest.raises(ValueError, match="at most 1 MiB"):
        decisions._read(f)


def test_read_refuses_a_symlink_and_a_fifo_without_hanging_and_keeps_newlines_and_oserror(tmp_path):
    import signal
    target = tmp_path / "t.md"
    target.write_bytes(b"x\r\ny")
    link = tmp_path / "l.md"
    link.symlink_to(target)
    fifo = tmp_path / "f.md"
    os.mkfifo(fifo)
    signal.alarm(5)  # a FIFO that was opened blocking would hang here
    try:
        for bad in (link, fifo):
            with pytest.raises(ValueError, match="regular file"):
                decisions._read(bad)
    finally:
        signal.alarm(0)
    assert decisions._read(target) == "x\r\ny"  # newlines untouched
    with pytest.raises(FileNotFoundError):
        decisions._read(tmp_path / "missing.md")
    target.write_bytes(b"\xff\xfe")
    with pytest.raises(UnicodeError):
        decisions._read(target)


def test_read_refuses_a_file_that_grew_past_the_cap_after_the_size_check(tmp_path, monkeypatch):
    import types
    f = tmp_path / "Decisions_1.md"
    f.write_bytes(b"a" * ((1 << 20) + 5))
    real = os.fstat
    monkeypatch.setattr(os, "fstat", lambda fd: types.SimpleNamespace(st_mode=real(fd).st_mode, st_size=10))  # the size looked small when it was checked
    with pytest.raises(ValueError, match="at most 1 MiB"):
        decisions._read(f)
