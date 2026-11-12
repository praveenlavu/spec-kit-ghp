"""T14 tests: `questions` (list, post), ported from ado tests/test_questions.py (the parse, comment-text and post tests; rename table, tracker-seam
fixtures: a FakeGh serving GraphQL + REST comments replaces ado's FakeClient). The gate tests (`test_gate_*`, T22) are ported at the end of the parse section. Not ported here: the docs test (T38), the config test
(already in test_config) and the whole `--sync` half (T28). New for GitHub: the marker carries a hash and a changed question set is a NEW comment (comments
are never edited), the post is idempotent by that marker, a write that may have been applied is re-read before anything else (AC4), the 60,000 cap (AC5)."""
import copy
import hashlib
import itertools
import json
import re
import time
from pathlib import Path

import pytest

import ghp.__main__ as cli
from conftest import FakeGh
from ghp import comments as cmod, issues
from ghp import questions as qmod
from ghp.dod import SECTION2, render_dod
from ghp.gh import GhError, GhTimeout, Viewer, WriteNotArmed
from ghp.gate import render_summary, run_gate
from ghp.questions import UNPARSED_NOTE, parse_questions
from ghp.refs import Ref

FIX = Path(__file__).parent / "fixtures" / "gh"
QS = """- [ ] Q1: Are contractors included? → _(open)_ · owner: PO
- [x] Q2: Which locale? → en-GB only (PO comment 2026-07-01) · owner: PO
-   [ ]  Q3 :  Retention period ->   _(open)_
- [ ] Q4: Export format? → CSV · owner: Dev
- [x] Q5: Cap? → _(open)_ · owner: PO
"""


def _ledger(body, tail="## 3. AC\n"):
    return f"# DoD\n{SECTION2}\n- **Approach:** x\n{body}{tail}"


def test_parse_open_answered_lenient():
    r = parse_questions(_ledger(QS))
    assert r["open"] == [
        {"id": "Q1", "question": "Are contractors included?", "owner": "PO"},
        {"id": "Q3", "question": "Retention period", "owner": "?"},
        {"id": "Q4", "question": "Export format?", "owner": "Dev"},  # box [ ] with an answer is still open
        {"id": "Q5", "question": "Cap?", "owner": "PO"},            # [x] with _(open)_ is still open
    ]
    assert r["answered"] == [{"id": "Q2", "question": "Which locale?", "owner": "PO",
                              "answer": "en-GB only (PO comment 2026-07-01)"}]


def test_parse_ignores_fences_comments_and_other_sections():
    body = ("```\n- [ ] Q1: fenced → _(open)_ · owner: PO\n```\n<!-- - [ ] Q2: hidden → _(open)_ -->\n"
            "<!--\n- [ ] Q3: multi → _(open)_\n-->\n- [ ] Q4: real → _(open)_ · owner: PO\n")
    r = parse_questions(_ledger(body, "## 3. AC\n- [ ] Q9: elsewhere → _(open)_\n"))
    assert [q["id"] for q in r["open"]] == ["Q4"] and r["answered"] == []


def test_parse_no_section2():
    assert parse_questions("nothing") == {"open": [], "answered": [], "unparsed": False}


def test_dod_seed_has_format_hint_not_a_question():
    issue = issues.Issue(ref=Ref("octo", "demo", 1001), node_id="I_kwDOFictional0001", id=9100001001, title="T", body="## Acceptance criteria\nAC one\n",
                         state="open", state_reason=None, labels=[], milestone=None, author=issues.Actor(1001, "octo-author", "User"), parent=None,
                         sub_total=0, sub_done=0, url="https://github.com/octo/demo/issues/1001")
    md = render_dod(issue, synced="2026-07-14")
    assert "Format, one line each: `- [ ] Q1:" in md
    assert parse_questions(md) == {"open": [], "answered": [], "unparsed": False}
    assert "_(fill in as they arise)_" in md


def test_empty_assumed_answer_stays_open():
    led = _ledger("- [x] Q1: a? → assumed: · owner: PO\n- [x] Q2: b? → assumed:   \n- [x] Q3: c? → assumed: y · owner: PO\n")
    out = parse_questions(led)
    assert [q["id"] for q in out["open"]] == ["Q1", "Q2"] and [q["id"] for q in out["answered"]] == ["Q3"]


def test_unclosed_fence_or_comment_in_section2_is_unreadable():
    for opener in ("```\n", "<!--\n"):
        r = parse_questions(_ledger(opener + "- [ ] Q1: hidden → _(open)_ · owner: PO\n"))
        assert r["unparsed"] is True and r["open"] == [{"id": "?", "question": qmod.UNPARSED_NOTE, "owner": "?"}]
    assert "could not be read" in qmod.UNPARSED_NOTE
    assert parse_questions(_ledger(QS))["unparsed"] is False


def test_gate_open_questions_advisory_then_blocking_and_summary():
    led = _ledger(QS)
    r = run_gate(led, {}, {})
    assert r["open_questions"] == ["Q1", "Q3", "Q4", "Q5"] and not r["blocking"] and r["blocked_by"] == []
    assert "**Advisory" in render_summary(r) and "`Q1`, `Q3`, `Q4`, `Q5`" in render_summary(r)
    b = run_gate(led, {}, {}, block=["open_questions"])
    assert b["blocking"] and b["blocked_by"] == ["open_questions"]
    blocking = render_summary(b).partition("**Advisory")[0].partition("**Blocking")[2]
    assert "Open questions" in blocking and "`Q1`" in blocking


def test_gate_no_open_questions_never_blocks():
    r = run_gate(_ledger("- [x] Q1: a? → b · owner: PO\n"), {}, {}, block=["open_questions"])
    assert r["open_questions"] == [] and not r["blocking"]


def test_gate_questions_closed_as_assumed_are_not_open_but_an_open_one_still_is():
    closed = _ledger("- [x] Q1: a? → assumed: x is used · owner: PO\n- [x] Q2: b? → assumed: y · owner: PO\n")
    r = run_gate(closed, {}, {}, block=["open_questions"])
    assert r["open_questions"] == [] and not r["blocking"] and r["blocked_by"] == []
    mixed = run_gate(_ledger("- [x] Q1: a? → assumed: x · owner: PO\n- [ ] Q2: b? → _(open)_ · owner: PO\n"),
                     {}, {}, block=["open_questions"])
    assert mixed["open_questions"] == ["Q2"] and mixed["blocking"] and mixed["blocked_by"] == ["open_questions"]


def test_gate_unclosed_fence_or_comment_in_section2_is_unreadable_and_blocks():
    for opener in ("```\n", "<!--\n"):
        led = _ledger(opener + "- [ ] Q1: hidden → _(open)_ · owner: PO\n")
        r = run_gate(led, {}, {}, block=["open_questions"])
        assert r["open_questions"] == ["?"] and r["open_questions_note"] == UNPARSED_NOTE
        assert r["blocking"] and r["blocked_by"] == ["open_questions"]
        assert "could not be read" in render_summary(r)


def test_gate_open_questions_deduped_in_order():
    led = _ledger("- [ ] Q2: a → _(open)_\n- [ ] Q1: b → _(open)_\n- [ ] Q2: c → _(open)_\n")
    assert run_gate(led, {}, {})["open_questions"] == ["Q2", "Q1"]


def test_gate_absent_section2_with_block_does_not_block():
    r = run_gate("# DoD\n## 3. AC\n", {}, {}, block=["open_questions"])
    assert r["open_questions"] == [] and not r["blocking"]


def test_trailing_open_answer_wins_over_box_and_arrow_in_question():
    r = parse_questions(_ledger("- [x] Q1: Does A -> B hold? → _(open)_ · owner: PO\n"))
    assert r["answered"] == [] and r["open"] == [{"id": "Q1", "question": "Does A -> B hold?", "owner": "PO"}]


def test_arrow_split_rule_last_unicode_else_first_ascii():
    r = parse_questions(_ledger("- [x] Q1: Retention? → 30 days -> confirmed by PO · owner: PO\n"
                                "- [ ] Q2: Cap -> per user? -> _(open)_ · owner: PO\n"))
    assert r["answered"] == [{"id": "Q1", "question": "Retention?", "owner": "PO", "answer": "30 days -> confirmed by PO"}]
    assert r["open"] == [{"id": "Q2", "question": "Cap", "owner": "PO"}]  # first ASCII arrow, answer `per user? -> _(open)_`


def test_questions_json_dedupes_and_flags_unreadable_section():
    r = parse_questions(_ledger("- [ ] Q2: a → _(open)_\n- [ ] Q1: b → _(open)_\n- [ ] Q2: c → _(open)_\n"))
    assert [q["id"] for q in r["open"]] == ["Q2", "Q1"] and r["open"][0]["question"] == "a"
    u = parse_questions(_ledger("```\n- [ ] Q1: x → _(open)_\n"))
    assert u["open"][-1]["id"] == "?" and "could not be read" in u["open"][-1]["question"]


def test_hidden_section2_heading_and_questions_are_ignored():
    fenced = f"# DoD\n{SECTION2}\n- [ ] Q1: real → _(open)_ · owner: PO\n```\n## 3. AC\n- [ ] Q2: shown? → _(open)_\n```\n"
    assert [q["id"] for q in parse_questions(fenced)["open"]] == ["Q1"]


# ---- hostile ledger text: every pattern is linear (earlier ports were quadratic on a long blank run) ----
@pytest.mark.parametrize("line", [
    "- [ ] Q1:" + " " * 300000 + "x",
    "- [ ] Q1: a" + " " * 300000 + "· owner:" + " " * 300000 + "x",
    "- [ ] Q1: a" + " " * 300000 + "→" + " " * 300000 + "b",
    "- [ ] Q1: a" + " " * 300000 + "->" + " " * 300000 + "b",
    "- [x] Q1: a → assumed" + " " * 300000 + ":" + " " * 300000 + "x",
    "- " + " " * 300000 + "[ ] Q1: a",
    " " * 300000 + "- [ ] Q1: a",
    "- [ ] Q" + "1" * 300000 + " : a",
    "- [ ] Q1: a" + "·" * 100000 + " owner: " + " " * 100000,
])
def test_parse_is_linear_on_hostile_lines(line):
    start = time.monotonic()
    parse_questions(_ledger((line + "\n") * 3))
    assert time.monotonic() - start < 2


def test_owner_is_the_last_segment():
    r = parse_questions(_ledger("- [ ] Q1: a · owner: x · b → _(open)_ · owner: PO\n- [ ] Q2: a | OWNER :  Dana  \n- [ ] Q3: a · note · owner:\n"))
    assert [q["owner"] for q in r["open"]] == ["PO", "Dana", "?"]


# ---- the seam: a cwd whose ghp.config.json names octo/demo, a FakeGh serving the issue's comments, a ledger on disk ----
ME, OTHER = 1001, 2002
M = qmod.MARKER
OPEN2 = "- [ ] Q1: Are contractors <in> scope? → _(open)_ · owner: Dana Fictional\n- [ ] Q3: Retention? → _(open)_\n"
CONTENT = "Open questions for the product owner:\nQ1: Are contractors &lt;in&gt; scope?\nQ3: Retention?"


def with_marker_of(content):
    return f"{M} {_sha(content)}\n{content}"


def _sha(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


BODY = f"{M} {_sha(CONTENT)}\n{CONTENT}"


def fx(name):
    return json.loads((FIX / (name + ".json")).read_text(encoding="utf-8"))


def comment(cid, body, author=ME):
    """(GraphQL node, REST item) for one comment of `author` (a numeric user id)."""
    g, r = copy.deepcopy(fx("comments_graphql")["repository"]["issue"]["comments"]["nodes"][0]), copy.deepcopy(fx("comments_rest")[0])
    g["databaseId"] = r["id"] = cid
    g["id"] = r["node_id"] = "IC_kwDOFictional%d" % cid
    g["body"] = r["body"] = body
    g["author"]["databaseId"] = r["user"]["id"] = author
    g["viewerDidAuthor"] = author == ME
    return g, r


class Issue1001:
    """A FakeGh whose issue 1001 holds `self.items`; a POST of a comment appends one (unless `apply=False`, or it raises `fail` first)."""

    def __init__(self, items=(), fail=None, apply=True):
        self.items, self.fail, self.apply, self.next_id = list(items), fail, apply, 7000
        self.gh = FakeGh(viewer=Viewer(ME, "octo-author", ["repo"]))
        self.gh.on_graphql("userContentEdits", lambda v: {"repository": {"issue": {"comments": {
            "pageInfo": {"hasNextPage": False, "endCursor": "c"}, "nodes": [g for g, _ in self.items]}}}})
        self.gh.on_rest("GET", "repos/octo/demo/issues/1001/comments?per_page=100&page=1", lambda _: [r for _, r in self.items])
        self.gh.on_rest("POST", "repos/octo/demo/issues/1001/comments", self.post)

    def post(self, body):
        if self.apply:
            self.next_id += 1
            self.items.append(comment(self.next_id, body["body"]))
        if self.fail:
            raise self.fail
        return {"id": self.next_id, "body": body["body"]}

    @property
    def posts(self):
        return [c for c in self.gh.calls if c[0] == "rest" and c[1] != "GET"]

    @property
    def bodies(self):
        return [r["body"] for _, r in self.items]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo"}))
    (tmp_path / "dod").mkdir()
    return tmp_path


def ledger(repo, body=OPEN2):
    (repo / "dod" / "DoD_1001.md").write_text(_ledger(body), encoding="utf-8")


def run(capsys, thread, *args):
    code = cli.main(["questions", "1001", *args], gh=thread.gh)
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_questions_lists_open_and_answered_as_json(repo, capsys):
    (repo / "dod" / "DoD_1001.md").write_text(_ledger(QS), encoding="utf-8")
    t = Issue1001()
    code, out, _ = run(capsys, t)
    assert code == 0 and t.gh.calls == []
    parsed = json.loads(out)
    assert [q["id"] for q in parsed["open"]] == ["Q1", "Q3", "Q4", "Q5"] and parsed["answered"][0]["id"] == "Q2"


def test_questions_missing_ledger_is_refused_before_any_call(repo, capsys):
    t = Issue1001()
    code, _, err = run(capsys, t)
    assert code == 2 and "run `ghp dod 1001` first" in err and t.gh.calls == []


def test_comment_text_escapes_and_never_names_owners():
    parsed = parse_questions(_ledger(OPEN2))
    assert qmod.comment_text(parsed["open"]) == CONTENT
    assert "Dana" not in CONTENT and qmod.comment_text([]) == ""
    assert qmod.comment_text([{"id": "?", "question": "unparsed", "owner": "?"}]) == ""


def test_comment_text_is_safe_to_post():
    q = [{"id": "Q1", "question": "ping @octo-bob \x1b[31m red ‮ rtl & ​", "owner": "PO"}]
    text = qmod.comment_text(q)
    assert "@octo-bob" not in text and "[mention removed]" in text and "@" not in text
    assert "\x1b" not in text and "‮" not in text and "​" not in text and "\\u001b" in text and "\\u202e" in text and "&amp;" in text


HOSTILE = ["@@x", "é@bob", "x@bob", "@org/team", "@@org/team", "@a@b", "a@b.c", "\uff20bob", "\ufe6bbob", "@\u200bbob", "@\u202e\u2060bob", "(@bob)", "mail a@b.c.",
           "@\u200b@\u200bbob", "@@@a", "\u200b@bob", "@bob@"]
_INVIS = "".join(chr(c) for c in range(0x200b, 0x2010)) + "\u2060\u00ad\ufeff"


def _name_after_at(text):
    """True when an `@` (or its fullwidth or small form) is followed by a name character, once the invisible characters are removed."""
    plain = "".join(c for c in text if c not in _INVIS)
    return re.search("[@\uff20\ufe6b][A-Za-z0-9_]", plain) is not None


@pytest.mark.parametrize("text", HOSTILE)
def test_every_at_before_a_name_is_defanged(text):
    raw = cmod._MENTION.sub(cmod.NO_MENTION, text)  # the rule on the raw text: the name is gone too (invisibles between the `@` and the name)
    assert "bob" not in raw and "team" not in raw and "b.c" not in raw and cmod.NO_MENTION in raw
    for out in (raw, qmod._safe(text), cmod._escape(text)):  # `_safe` shows invisibles as `\\uXXXX` text first: no `@` is then followed by a name
        assert not _name_after_at(out)
    assert cmod.NO_MENTION in cmod._escape(text)


def test_the_placeholder_is_not_a_mention_and_the_defang_is_stable():
    assert "@" not in cmod.NO_MENTION and not any(c in cmod.NO_MENTION for c in "\uff20\ufe6b")
    for text in HOSTILE:
        once = cmod._MENTION.sub(cmod.NO_MENTION, text)
        assert cmod._MENTION.sub(cmod.NO_MENTION, once) == once
        assert qmod._safe(qmod._safe(text).replace("&amp;", "&")) == qmod._safe(text)  # a second pass (after the `&` escape is undone) changes nothing


def test_no_short_string_of_at_name_and_invisible_characters_survives_as_a_mention():
    parts = ["@", "\uff20", "\ufe6b", "\u200b", "\u2060", "a", "_", "9", "\u00e9", "/", ".", "-", " "]
    seen = 0
    for n in range(1, 5):
        for combo in itertools.product(parts, repeat=n):
            text = "".join(combo)
            out = cmod._MENTION.sub(cmod.NO_MENTION, text)
            assert not _name_after_at(out) and cmod._MENTION.sub(cmod.NO_MENTION, out) == out, text
            seen += 1
    assert seen > 28000


def test_text_without_a_mention_is_unchanged():
    for text in ("no at sign here", "price @ 5", "@ bob", "@-x", "@@", "tail @", "a & b", "\u00fcn\u00ef \u00a9 \u00e9", "e-mail: a at b dot c"):
        assert qmod._safe(text) == text.replace("&", "&amp;")
        assert cmod._MENTION.sub(cmod.NO_MENTION, text) == text


def test_the_mention_rule_is_linear():
    start = time.monotonic()
    for hostile in ("@" * 300000, "@" + "\u200b" * 300000, "@\u200b" * 150000 + "x", "@a" * 150000, "@a" + "." * 300000, "x@" * 150000):
        cmod._MENTION.sub(cmod.NO_MENTION, hostile)
        qmod._safe(hostile)
    assert time.monotonic() - start < 5


# ---- AC2 ----
def test_post_dry_run_sends_nothing(repo, capsys):
    ledger(repo)
    t = Issue1001([comment(5, "unmarked <div>chat</div>")])
    code, out, _ = run(capsys, t, "--post")
    assert code == 0 and t.posts == [] and not t.gh.armed
    assert "Action: ADD a new comment" in out
    assert f"> {M} {_sha(CONTENT)}\n> Open questions for the product owner:\n> Q1: Are contractors &lt;in&gt; scope?\n> Q3: Retention?" in out
    assert "Dana" not in out
    code, out, _ = run(capsys, t, "--plan-out", "p.md")  # --plan-out alone is a preview too
    assert code == 0 and t.posts == [] and "Action: ADD a new comment" in (repo / "p.md").read_text()


def test_plan_none_when_no_open_questions_and_no_own_comment(repo, capsys):
    ledger(repo, "- [x] Q1: a → b · owner: PO\n")
    t = Issue1001()
    code, out, _ = run(capsys, t, "--post", "--apply", "--plan-out", "p.md")
    assert code == 0 and t.posts == [] and "no open questions; nothing to post" in out
    assert "no open questions; nothing to post" in (repo / "p.md").read_text()


def test_apply_add_posts_one_comment(repo, capsys):
    ledger(repo)
    t = Issue1001([comment(5, "unmarked")])
    code, out, _ = run(capsys, t, "--post", "--apply")
    assert code == 0 and t.gh.armed and "posted comment 7001" in out
    assert t.posts == [("rest", "POST", "repos/octo/demo/issues/1001/comments", {"body": BODY})]


# ---- AC3 ----
def test_repost_finds_the_marker_first(repo, capsys):
    ledger(repo)
    t = Issue1001()
    assert run(capsys, t, "--post", "--apply")[0] == 0 and len(t.posts) == 1
    first_post = next(i for i, c in enumerate(t.gh.calls) if c[1] == "POST" and c[2].endswith("/comments"))
    assert any(c[0] == "graphql" for c in t.gh.calls[:first_post])  # the comments were read BEFORE the first write
    t.gh.calls.clear()
    code, out, _ = run(capsys, t, "--post", "--apply")
    assert code == 0 and "unchanged; nothing to post" in out
    assert t.posts == [] and t.bodies == [BODY]  # found by marker and hash, not duplicated
    assert any(c[0] == "graphql" for c in t.gh.calls)  # and it was found by reading


def test_a_changed_question_set_is_a_new_comment_never_an_edit(repo, capsys):
    ledger(repo)
    t = Issue1001([comment(9, BODY)])
    ledger(repo, OPEN2 + "- [ ] Q4: Added later? → _(open)_\n")
    code, out, _ = run(capsys, t, "--post", "--apply")
    assert code == 0 and "ADD a new comment" in out and len(t.posts) == 1 and t.posts[0][1] == "POST"
    assert len(t.bodies) == 2 and t.bodies[0] == BODY and "Q4: Added later?" in t.bodies[1] and t.bodies[1].split("\n")[0] != BODY.split("\n")[0]


def test_only_the_newest_own_marked_comment_counts(repo, capsys):
    ledger(repo)
    stale = f"{M} {_sha('x')}\nx"
    t = Issue1001([comment(7, BODY), comment(9, stale)])  # the set was changed after, and is back now: the comment on show is the stale one
    code, out, _ = run(capsys, t, "--post", "--apply")
    assert code == 0 and "ADD a new comment" in out and len(t.posts) == 1


def test_a_tampered_comment_with_the_same_marker_line_is_not_unchanged(repo, capsys):
    ledger(repo)
    t = Issue1001([comment(9, BODY + "\nQ9: slipped in")])
    code, out, _ = run(capsys, t, "--post")
    assert code == 0 and "ADD a new comment" in out


def test_unchanged_comment_is_a_noop_with_crlf(repo, capsys):
    ledger(repo)
    t = Issue1001([comment(9, BODY.replace("\n", "\r\n"))])
    code, out, _ = run(capsys, t, "--post", "--apply")
    assert code == 0 and "unchanged; nothing to post" in out and t.posts == []


def test_foreign_marked_comment_is_ignored(repo, capsys):
    ledger(repo)
    t = Issue1001([comment(3, BODY, author=OTHER)])  # someone else posted exactly our text: it is not ours
    code, out, _ = run(capsys, t, "--post", "--apply")
    assert code == 0 and "ADD a new comment" in out and len(t.posts) == 1
    t = Issue1001([comment(3, "Re: " + BODY)])  # our own comment, but the marker is not at its start
    assert "ADD a new comment" in run(capsys, t, "--post")[1]


def test_expect_plan_mismatch_exits_2_without_a_write(repo, capsys):
    ledger(repo)
    t = Issue1001()
    assert run(capsys, t, "--post", "--plan-out", "p.md")[0] == 0
    approved = (repo / "p.md").read_text()
    ledger(repo, OPEN2 + "- [ ] Q4: Added after approval? → _(open)_\n")
    code, _, err = run(capsys, t, "--post", "--apply", "--expect-plan", "p.md")
    assert code == 2 and "plan changed since approval" in err
    assert t.posts == [] and not t.gh.armed and (repo / "p.md").read_text() == approved and "Q4" in (repo / "p.md.new").read_text()
    code, _, _ = run(capsys, t, "--post", "--apply", "--expect-plan", "p.md.new")  # the re-approved plan applies
    assert code == 0 and len(t.posts) == 1


def test_unresolvable_identity_refuses_without_a_write(repo, capsys):
    ledger(repo)
    t = Issue1001()

    def broken():
        raise GhError("E_GH_HTTP", 403, "forbidden")
    t.gh.viewer = broken
    code, _, err = run(capsys, t, "--post", "--apply")
    assert code == 2 and "cannot tell which GitHub comments are yours" in err and t.posts == []


def test_identity_is_the_numeric_viewer_id_never_a_login(repo, capsys):
    ledger(repo)
    t = Issue1001([comment(3, BODY, author=OTHER)])
    other = [(g, r) for g, r in t.items]
    other[0][1]["user"]["login"] = "octo-author"  # same login, another id: not ours
    t.items[:] = other
    assert "ADD a new comment" in run(capsys, t, "--post")[1]
    assert qmod._me(t.gh) == ME


def test_all_answered_posts_the_answered_note_once_when_an_own_comment_exists(repo, capsys):
    ledger(repo, "- [x] Q1: a → b · owner: PO\n")
    done = "All open questions are answered."
    t = Issue1001([comment(9, BODY)])
    code, out, _ = run(capsys, t, "--post", "--apply")
    assert code == 0 and t.posts[0][3] == {"body": f"{M} {_sha(done)}\n{done}"}
    t.gh.calls.clear()
    assert run(capsys, t, "--post", "--apply")[0] == 0 and t.posts == []


def test_long_question_line_is_capped_and_marked(repo, capsys):
    ledger(repo, f"- [ ] Q1: {'x' * 600} → _(open)_\n")
    code, out, _ = run(capsys, Issue1001(), "--post")
    assert f"Q1 cut to {qmod.MAX_LINE} characters" in out
    line = next(x for x in out.splitlines() if x.startswith("> Q1: "))[2:]
    assert len(line) == qmod.MAX_LINE and line.endswith("…")


def test_apply_without_post_and_expect_plan_without_apply_are_refused(repo, capsys):
    ledger(repo)
    t = Issue1001()
    code, _, err = run(capsys, t, "--apply")
    assert code == 2 and "--apply needs --post" in err and t.gh.calls == [] and not t.gh.armed
    with pytest.raises(SystemExit) as e:
        run(capsys, t, "--post", "--expect-plan", "p.md")
    assert e.value.code == 2 and "--expect-plan needs --apply" in capsys.readouterr().err


def test_comment_lists_assumptions_after_the_open_questions():
    parsed = parse_questions(_ledger(OPEN2 + "- [x] Q4: Archived too? → assumed: archived are excluded · owner: PO\n"
                                     "- [x] Q5: Cap? → 50 per page · owner: PO\n"))
    text = qmod.comment_text(parsed["open"], qmod._assumed(parsed["answered"]))
    assert text.startswith(CONTENT) and text.endswith(f"\n{qmod._ASSUMED_HEAD}\nQ4: Archived too? → assumed: archived are excluded")
    assert "Q5" not in text  # a question the PO answered is not an assumption
    assert qmod.comment_text([], qmod._assumed(parsed["answered"])) == f"{qmod._ASSUMED_HEAD}\nQ4: Archived too? → assumed: archived are excluded"


def test_own_comment_gets_a_new_one_with_assumptions_when_nothing_is_open(repo, capsys):
    ledger(repo, "- [x] Q1: a → assumed: yes · owner: PO\n")
    t = Issue1001([comment(3, BODY)])
    assert run(capsys, t, "--post", "--apply")[0] == 0
    assert len(t.posts) == 1 and "assumed: yes" in t.posts[0][3]["body"] and "All open questions are answered" not in t.posts[0][3]["body"]


def test_the_json_list_never_prints_a_raw_bidi_or_control_character(repo, capsys):
    ledger(repo, "- [ ] Q1: a\u202eb\x9b2J\u200bc → _(open)_\n")
    code, out, _ = run(capsys, Issue1001())
    assert code == 0 and out.isascii() and "\u202e" not in out and "\x9b" not in out
    assert json.loads(out)["open"][0]["question"] == "a\u202eb\x9b2J\u200bc"  # escaped, not lost


def test_the_preview_escapes_what_a_terminal_would_act_on(repo, capsys):
    ledger(repo, "- [ ] Q1: red \x1b[31m and ‮ → _(open)_\n")
    code, out, _ = run(capsys, Issue1001(), "--post")
    assert "\x1b" not in out and "‮" not in out and "\\u001b" in out


# ---- AC4 ----
def test_post_after_timeout_rereads_before_any_retry(repo, capsys):
    ledger(repo)
    t = Issue1001(fail=GhTimeout(write=True), apply=True)  # the write timed out but GitHub did apply it
    code, out, _ = run(capsys, t, "--post", "--apply")
    assert code == 0 and len(t.posts) == 1 and t.bodies == [BODY]  # never sent twice
    post = next(i for i, c in enumerate(t.gh.calls) if c[1] == "POST" and c[2].endswith("/comments"))
    assert any(c[0] == "graphql" for c in t.gh.calls[post + 1:])  # a read came after the write
    assert "found on re-read" in out


def test_post_after_timeout_that_did_not_apply_is_reported_and_not_retried(repo, capsys):
    ledger(repo)
    t = Issue1001(fail=GhTimeout(write=True), apply=False)
    code, _, err = run(capsys, t, "--post", "--apply")
    assert code == 1 and len(t.posts) == 1 and t.bodies == []
    assert "E_GH_TIMEOUT" in err and "not found on re-read" in err and "not retried" in err


def test_a_definite_4xx_is_not_reread_or_retried(repo, capsys):
    ledger(repo)
    t = Issue1001(fail=GhError("E_GH_HTTP", 403, "Resource not accessible"), apply=False)
    code, _, err = run(capsys, t, "--post", "--apply")
    n_reads = sum(c[0] == "graphql" for c in t.gh.calls)
    assert code == 1 and len(t.posts) == 1 and "E_GH_HTTP" in err and n_reads == 1  # the plan's read only


def test_a_5xx_write_is_reread_and_found_when_it_applied(repo, capsys):
    ledger(repo)
    t = Issue1001(fail=GhError("E_GH_HTTP", 502, "bad gateway"), apply=True)
    code, out, _ = run(capsys, t, "--post", "--apply")
    post = next(i for i, c in enumerate(t.gh.calls) if c[1] == "POST" and c[2].endswith("/comments"))
    assert code == 0 and len(t.posts) == 1 and t.bodies == [BODY] and "found on re-read" in out
    assert any(c[0] == "graphql" for c in t.gh.calls[post + 1:])


def test_a_5xx_write_that_was_not_applied_is_reported_and_not_retried(repo, capsys):
    ledger(repo)
    t = Issue1001(fail=GhError("E_GH_HTTP", 502, "bad gateway"), apply=False)
    code, _, err = run(capsys, t, "--post", "--apply")
    assert code == 1 and len(t.posts) == 1 and t.bodies == [] and "HTTP 502" in err and "not found on re-read and not retried" in err


def test_the_reread_must_find_the_exact_text_not_just_the_marker(repo, capsys):
    ledger(repo)
    older = with_marker_of("Open questions for the product owner:\nQ9: an older set")
    t = Issue1001([comment(4, older)], fail=GhTimeout(write=True), apply=False)  # an own marked comment exists, but not the planned one
    code, out, err = run(capsys, t, "--post", "--apply")
    assert code == 1 and len(t.posts) == 1 and "found on re-read" not in out and "not found on re-read" in err


def test_the_not_retried_note_survives_a_long_github_message(repo, capsys):
    ledger(repo)
    t = Issue1001(fail=GhError("E_GH_HTTP", 502, "m" * 390), apply=False)
    code, _, err = run(capsys, t, "--post", "--apply")
    assert code == 1 and "not found on re-read and not retried: check the issue" in err


# ---- AC5 ----
def test_oversized_comment_is_refused(repo, capsys):
    ledger(repo, "".join(f"- [ ] Q{n}: {'x' * 600} → _(open)_\n" for n in range(1, 140)))
    for flags in (["--post"], ["--post", "--apply"]):
        t = Issue1001()
        code, out, err = run(capsys, t, *flags)
        assert code == 2 and "60,000" in err and t.posts == [] and "Q1: " not in out
    with pytest.raises(ValueError, match="60,000"):
        cmod.add(FakeGh().on_rest("POST", "repos/octo/demo/issues/1001/comments", {"id": 1}), Ref("octo", "demo", 1001), "x" * 60001)


def _questions_of_length(total):
    """Open questions whose final comment (marker line included) is exactly `total` characters."""
    n = (total - 100) // 210
    qs = [{"id": "Q%d" % k, "question": "x" * 200, "owner": "?"} for k in range(1, n + 1)]
    left = total - len(qmod.with_marker(qmod.comment_text(qs)))
    for q in qs:  # spread the rest so no line reaches MAX_LINE
        step = min(left, 250)
        q["question"] += "x" * step
        left -= step
    assert left == 0 and len(qmod.with_marker(qmod.comment_text(qs))) == total
    return {"open": qs, "answered": [], "unparsed": False}


def test_plan_post_allows_60000_and_refuses_60001_characters_of_the_final_body():
    ref = Ref("octo", "demo", 1001)
    ok = qmod.plan_post(Issue1001().gh, ref, _questions_of_length(60000))
    assert ok["action"] == "add" and len(ok["body"]) == 60000
    with pytest.raises(ValueError, match="60,000"):
        qmod.plan_post(Issue1001().gh, ref, _questions_of_length(60001))


def test_a_comment_of_exactly_the_cap_is_allowed():
    gh = FakeGh().on_rest("POST", "repos/octo/demo/issues/1001/comments", {"id": 1})
    gh.arm()
    assert cmod.add(gh, Ref("octo", "demo", 1001), "x" * 60000) == 1


# ---- the write itself ----
def test_add_refuses_an_unarmed_gh_and_sends_nothing():
    gh = FakeGh().on_rest("POST", "repos/octo/demo/issues/1001/comments", {"id": 1})
    with pytest.raises(WriteNotArmed):
        cmod.add(gh, Ref("octo", "demo", 1001), "hello")
    assert gh.calls == []


def test_add_rejects_a_malformed_response_as_unknown_outcome():
    gh = FakeGh().on_rest("POST", "repos/octo/demo/issues/1001/comments", {"id": "7"})
    gh.arm()
    with pytest.raises(GhError) as e:
        cmod.add(gh, Ref("octo", "demo", 1001), "hello")
    assert e.value.code == "E_GH_BAD_BODY" and "re-read" in str(e.value)


@pytest.mark.parametrize("body", ["", 5, None])
def test_add_refuses_an_empty_or_non_text_body(body):
    gh = FakeGh().on_rest("POST", "repos/octo/demo/issues/1001/comments", {"id": 1})
    gh.arm()
    with pytest.raises(ValueError):
        cmod.add(gh, Ref("octo", "demo", 1001), body)
    assert gh.calls == []


def test_a_dropped_comment_makes_an_add_refuse(repo, capsys):
    ledger(repo)
    t = Issue1001([comment(5, "x")])
    t.items[0][1]["node_id"] = "IC_other"  # the two reads disagree: the comment is dropped with a note, and could have been our own
    code, _, err = run(capsys, t, "--post", "--apply")
    assert code == 2 and "could not be read reliably" in err and t.posts == []
