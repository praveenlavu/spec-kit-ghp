"""T15 tests: the approvals engine, terminal mode only, ported from ado tests/test_approvals.py (the grammar, `excluded`, `guard`, `newest`, `gate_hash`
and `require` tests; rename table, tracker-seam fixtures: a `comments.Comment` with a Markdown body and a numeric author id replaces ado's HTML row).

Changed for GitHub (see the `ghp.approvals` docstring): the first line is read by `comments.first_command_line`, and the whole line must be plain ASCII
(`comments.is_plain_command_line`), so the ado cases that are HTML (`<blockquote>`, `<p>`, gmail_quote, tag regexes, `&gt;`) became Markdown cases
(`> quote`, a fence, `<!--`, indented code, a list) and the ado note scrub test became "a note with other punctuation is a malformed line".
Not ported, with the owner:
- ADO-only, no GitHub counterpart (dropped): `on_behalf`, `authorized_as` (forged author), the bypass-rules bit (`bypass_bit`, `comment_mode_allowed`),
  the versions endpoint (`_edited`: `Comment.edited` replaces it; the "identical text in every version" exception does not exist), the HTML quote
  stripper (`strip_quotes`, `_NAME`), `connectionData` identity tests.
- Comment mode of `require` (`_decide`, `_load`, requests, stale approvals, the applies in comment mode): ported by T28 into tests/test_approvals_sync.py;
  one skipped placeholder below names them.
- `ado approvals check` (artifacts, review gates, wait gates, decisions file), the poller (`read_run`, `resume_argv`, `sync`): tests/test_approvals_sync.py (T28);
  the approval-request comments are not ported (T28 ruling).
New for GitHub: the notes refusal, ids must be ints, plain-ASCII gate, indented-code retraction, 30,000- and 1 MiB-character hostile bodies."""
import dataclasses
import hashlib
import time

import pytest

from ghp import approvals, comments
from ghp.comments import Comment, Comments, Note
from ghp.config import Trust
from ghp.issues import Actor

ME, KIT, HUMAN, CI, OTHER = 900001, 900002, 900003, 900004, 900005  # fictional numeric user ids
D = "2026-10-01T00:00:00Z"
H64 = "ab" * 32
D1, D2, D3 = "2026-10-01T10:00:00Z", "2026-10-01T10:00:00.5Z", "2026-10-01T10:00:00.25Z"


def _c(cid, body, date=D1, author=HUMAN, **kw):
    kw.setdefault("last_edited_at", None)
    kw.setdefault("edit_count", 0)
    return Comment(id=cid, node_id="IC_%d" % cid, author=Actor(author, "login%d" % author, "User"), body=body, created_at=date,
                   minimized=False, app=None, viewer_did_author=False, **kw)


def _p(body):
    return approvals.parse_command(body)


def _newest(raws, target="D3", approvers=(HUMAN,), excluded=(), tally=None):
    return approvals.newest(Comments(raws), target, set(approvers), set(excluded), tally=tally)


# ---- excluded -----------------------------------------------------------------------------------------------------------------------------------

def test_excluded_contains_token_and_kit_authors():
    found = [_c(1, "\n [spec-kit-ghp:questions]\nOpen questions", D, author=KIT), _c(2, "[spec-kit-ghp:decisions]", D, author=ME), _c(3, "/approve D1", D),
             _c(4, "see [spec-kit-ghp:questions] above", D, author=OTHER)]
    assert approvals.excluded(ME, found) == {ME, KIT}


def test_excluded_without_comments_is_just_the_token_identity():
    assert approvals.excluded(ME, []) == {ME}


def test_excluded_requires_a_numeric_token_identity():
    for bad in (None, "900001", " 900001 ", 0, -3, True, 1.0, "octocat"):
        with pytest.raises(ValueError):
            approvals.excluded(bad, [])


def test_excluded_adds_the_ci_identity():
    assert approvals.excluded(ME, [], ci_identity=CI) == {ME, CI}
    for bad in ("not-an-id", " ", "5", 0, True, 2.5):
        with pytest.raises(ValueError):
            approvals.excluded(ME, [], ci_identity=bad)
    assert approvals.excluded(ME, [], ci_identity=None) == {ME}


# ---- the grammar -------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("body,want", [
    ("/approve D3", {"verb": "approve", "target": "D3", "hash": None, "text": ""}),
    ("/approve D3\n", {"verb": "approve", "target": "D3", "hash": None, "text": ""}),
    ("/approve D3\r\nthanks", {"verb": "approve", "target": "D3", "hash": None, "text": ""}),
    ("/reject D12: too vague", {"verb": "reject", "target": "D12", "hash": None, "text": "too vague"}),
    ("/reject D3 :  spaced  note ", {"verb": "reject", "target": "D3", "hash": None, "text": "spaced note"}),
    ("/answer Q2: Use the blue one", {"verb": "answer", "target": "Q2", "hash": None, "text": "Use the blue one"}),
    (f"/approve review-spec {H64}", {"verb": "approve", "target": "review-spec", "hash": H64, "text": ""}),
    (f"/reject approve-push {H64}: not yet", {"verb": "reject", "target": "approve-push", "hash": H64, "text": "not yet"}),
    ("\n  \n\t \n/approve D3\n\nthanks", {"verb": "approve", "target": "D3", "hash": None, "text": ""}),  # blank lines first, then the first line
    ("  /approve D3  ", {"verb": "approve", "target": "D3", "hash": None, "text": ""}),  # up to 3 leading spaces and trailing blanks are ignored (as ado)
])
def test_parse_command_accepts_the_grammar(body, want):
    assert _p(body) == want


@pytest.mark.parametrize("body", [
    "", None, 5, b"/approve D3", "approve D3", "/Approve D3", "/approve", "/approve d3", "/approve D", "/approve D3x", "/approve D03", "/approve D0", "/answer Q02: x",
    "/approve D3 extra", "/approve D3:", "/approve D3:   ", "/answer Q2", "/answer Q2:", "/answer D3: yes", "/approve Q2", "/reject Q2: no", "/answer review-spec: x",
    "/approve review-spec", "/approve ab", "/approve a" + "b" * 41, "/approve D3 " + H64, "/answer Q2 " + H64 + ": x", "/approve D3 /approve D4 x",
    "x/approve D3", "\u200b/approve D3", "/approvals D3", "/approve-all D3",
    "<!-- /approve D3 -->", "> /approve D3", ">/approve D3", "   > /approve D3", "```\n/approve D3\n```", "~~~\n/approve D3", "    /approve D3", "\t/approve D3", "- /approve D3",
    "* /approve D3", "1. /approve D3", "# /approve D3", "## /approve D3", "`/approve D3`", "**/approve D3**", "[/approve D3]", "/approve D3 <!-- D4 -->",
    "/approve D3<span hidden>D4</span>", "/approve D3 &#8203;", "/approve D3\u200b", "/approve\u00a0D3", "/approve D3 \\", "/approve\tD3",
])
def test_parse_command_rejects_everything_else(body):
    assert _p(body) is None


def test_hash_must_be_64_hex():
    for bad in (H64[:-1], H64 + "a", H64.upper(), "g" * 64, H64[:-1] + "G", "\u00a0" + H64, "0x" + H64[2:]):
        assert _p(f"/approve review-spec {bad}") is None, bad
    assert _p(f"/approve review-spec {H64}")["hash"] == H64
    assert _p(f"/approve review-spec {'0' * 64}")["hash"] == "0" * 64


def test_notes_and_answers_are_capped():
    assert _p("/reject D3: " + "n" * 300)["text"] == "n" * 300
    assert _p("/reject D3: " + "n" * 301)["text"] == "n" * 300  # capped, never refused: a long reject must stay a reject
    assert _p("/reject D3: " + "n" * 1900)["text"] == "n" * 300
    assert _p("/answer Q2: " + "a" * 500)["text"] == "a" * 500
    assert _p("/answer Q2: " + "a" * 501)["text"] == "a" * 500


def test_a_note_with_anything_but_plain_ascii_is_a_malformed_line():
    """ado scrubbed mentions, markup and control characters from a note; here the whole line must be plain, so a note holding any of them is a
    malformed line (counted, never a command). T27 decides whether `/answer` text may be wider."""
    for note in ("mail dana@contoso.example", "ping @dana", "<a href='x'>x</a>", "\x1b[31mred\x1b[0m", "&lt;!-- x --&gt;", "```fence```", "it's fine", "(see D1)", "50%",
                 "caf\u00e9", "a\u200bb", "a\tb", "say \"yes\""):
        for verb in ("/reject D3: ", "/answer Q2: "):
            assert _p(verb + note) is None, (verb, note)
    assert _p("/answer Q2: ok, see D1/D2 #3 - done. Ref: A_b") is not None  # the plain set: letters digits space / _ . : # , -
    assert _p("/answer Q2:   ") is None  # nothing left


def test_a_quoted_or_hidden_approve_is_not_a_command():
    for body in ("> /approve D3", "> /approve D3\n> more", ">  /approve D3\n/approve D3", "<!-- x -->\n/approve D3", "<!--\n/approve D3\n-->", "```\n/approve D3\n```",
                 "> agent said this\n/approve D3", "> x\n\n/approve D3", "    /approve D3\n/approve D3"):
        assert _p(body) is None, body
    # a human's own first line still counts when something quoted comes AFTER it
    assert _p("/approve D3\n> /reject D3")["verb"] == "approve"
    assert _p("/approve D3\n```\n/reject D3\n```")["verb"] == "approve"
    assert _newest([_c(1, "> /approve D3\nI disagree")]) is None


def test_second_line_command_ignored():
    for body in ("Looks good\n/approve D3", "Thanks!\r/approve D3", "ok\n\n/approve D3", "- x\n/approve D3", "/approve D3 and also"):
        assert _p(body) is None, body
        assert _newest([_c(1, body)]) is None
    # the FIRST command wins over a second one: only the first line counts
    assert _p("/reject D3: no\n/approve D3")["verb"] == "reject"


def test_bad_lines_counted_not_echoed():
    secret = "SEKRET-TEXT-" + "z" * 5
    raws = [_c(1, f"/approve D3 {secret}"), _c(2, f"/reject D3 {secret} no colon", date=D2), _c(3, "just chatting", date=D3),
            _c(4, f"/approve review-spec {secret}", date="2026-10-01T10:00:01Z"), _c(5, "/approve D4", date="2026-10-01T10:00:02Z")]
    tally = {}
    assert _newest(raws, tally=tally) is None
    assert tally == {"bad": 3, "bad_newer": 2, "bad_newer_by": {"malformed": 2}}  # counted: chatting and a valid line for another target are not bad lines; two bad ones name D3 (the third names a gate)
    assert secret not in repr(tally)
    raws.append(_c(6, "/approve D3", date="2026-10-01T10:00:03Z"))
    tally = {}
    got = _newest(raws, tally=tally)
    assert got["id"] == 6 and got["bad_newer"] == 0 and tally == {"bad": 3}


def test_oversized_comment_or_line_is_a_bad_line_not_a_hang():
    tally = {}
    assert _newest([_c(1, "/approve D3" + " " * approvals.MAX_LINE)], tally=tally) is not None  # trailing blanks are blanks: the line is the command
    long_line = "/approve D3: " + "x" * approvals.MAX_LINE
    tally = {}
    assert _newest([_c(1, long_line)], tally=tally) is None and tally == {"bad": 1, "bad_newer": 1, "bad_newer_by": {"malformed": 1}}
    tally = {}
    assert _newest([_c(1, "/approve D3\n" + "y" * approvals.MAX_RAW)], tally=tally) is None and tally == {"bad": 1, "bad_newer": 1, "bad_newer_by": {"malformed": 1}}
    assert _p("x" * (approvals.MAX_RAW + 1)) is None
    assert _p("/approve D3\n" + "y" * (approvals.MAX_RAW - 12))["verb"] == "approve"  # exactly the cap still reads


def test_hostile_bodies_are_linear():
    """Every shape at 30,000 and at 1 MiB characters: `parse_command` and `newest`. ado's `dod._text` was quadratic on a long blank run; the rewritten
    reader finds the first non-blank line by one `finditer` and runs a bounded line through bounded patterns."""
    for big in (30_000, 200_000, 1 << 20):
        shapes = {
            "spaces": " " * big, "tabs": "\t" * big, "tabs-spaces": " \t" * (big // 2), "blank-lines": "\n" * big, "blank-lines-spaces": " \n" * (big // 2),
            "crlf": "\r\n" * (big // 2), "cr": "\r" * big, "nl-spaces": "\n  " * (big // 3), "nl-tabs": "\n\t" * (big // 2), "lt": "<" * big, "gt": ">" * big,
            "quotes": "> " * (big // 2), "quote-lines": ">\n" * (big // 2), "nbsp": "\u00a0" * big, "ideographic": "\u3000" * big, "zwsp": "\u200b" * big,
            "approve-spaces": "/approve D3" + " " * big, "approve-spaces-x": "/approve D3" + " " * big + "x", "approve-tabs-x": "/approve D3" + "\t" * big + "x",
            "approve-gate-spaces": "/approve review-spec" + " " * big, "approve-gate-hash-spaces": f"/approve review-spec {H64}" + " " * big + "x",
            "approve-colon-spaces": "/approve D3:" + " " * big, "reject-colon-x": "/reject D3:" + " " * big + "x", "verb-spaces": "/approve" + " " * big + "D3",
            "blank-then-command": "\n" * big + "/approve D3", "spaces-then-command": " " * big + "/approve D3", "blank-then-long": "\n" * (big // 2) + "x" * (big // 2),
            "long-note": "/reject D3: " + "n " * (big // 2), "long-gate": "/approve " + "a" * big, "digits": "/approve D" + "1" * big, "hex": "/approve gate " + "a" * big,
            "dash-lt": "/approve D3 " + "<!-- " * (big // 5), "fence": "```" * (big // 3), "tilde": "~~~" * (big // 3), "indent": " " * 4 + "/approve D3" + " " * big,
        }
        for name, body in shapes.items():
            t = time.time()
            _p(body)
            found = Comments([_c(1, body), _c(2, body, author=OTHER, date=D2)])
            approvals.newest(found, "D3", {HUMAN}, {ME})
            assert time.time() - t < 1.0, (big, name)


def test_look_alike_verbs_are_bad_lines():
    for line in ("/REJECT D3", "/Approve D3", "/approved D3", "/approve-all D3", "\uff0fapprove D3", "\u200b/approve D3", "/\uff21\uff30\uff30\uff32\uff2f\uff36\uff25 D3",
                 "/answers Q2: x", "/reject D3 reason"):
        assert _p(line) is None, line
        tally = {}
        assert _newest([_c(1, line)], tally=tally) is None and tally.get("bad") == 1, line
    for line in ("/path/to/file", "/ approve D3", "approve D3", "/app D3", "// approve", "> /approve D3", ">/approve D3"):
        tally = {}
        assert _newest([_c(1, line)], tally=tally) is None and "bad" not in tally, line


def test_indented_code_or_hidden_character_lines_that_look_like_commands_are_bad_lines():
    """`comments.first_command_line` refuses them as commands; they are still counted, because an approver may have meant a retraction."""
    base = [_c(1, "/approve D3", date=D1)]
    for line in ("    /reject D3", "\t/reject D3", "/reject D3\u200b", "\u200b/reject D3", "/reje\u200bct D3", "/reject\u00a0D3", "/reject D3 \u202e"):
        tally = {}
        got = _newest(base + [_c(2, line, date=D2)], tally=tally)
        assert got["id"] == 1 and got["bad_newer"] == 1 and tally == {"bad": 1, "bad_newer": 1, "bad_newer_by": {"malformed": 1}}, repr(line)


def test_a_malformed_later_line_by_an_approver_is_reported():
    base = [_c(1, "/approve D3", date=D1)]
    later = lambda text, **kw: _newest(base + [_c(2, text, date=D2, **kw)])  # noqa: E731
    got = later("/reject D3 changed my mind")
    assert got["id"] == 1 and got["bad_newer"] == 1  # the earlier approval stands, but the caller is told a retraction may have been typed wrong
    assert later("/REJECT D3")["bad_newer"] == 1 and later("/reject")["bad_newer"] == 1 and later("/reject d3: x")["bad_newer"] == 1
    assert later("/reject D4 changed my mind")["bad_newer"] == 0 and later("/reject review-spec x")["bad_newer"] == 0  # about another target
    assert _newest(base + [_c(2, "/reject D3 nope", date=D2, author=ME)], excluded={ME})["bad_newer"] == 0  # an excluded identity is not a retraction
    assert later("/reject D3 nope", author=OTHER)["bad_newer"] == 0  # not an approver
    assert _newest([_c(1, "/approve D3", date=D2), _c(2, "/reject D3 nope", date=D1)])["bad_newer"] == 0  # older than the winner
    tally = {}
    assert _newest([_c(2, "/reject D3 nope", date=D2)], tally=tally) is None and tally == {"bad": 1, "bad_newer": 1, "bad_newer_by": {"malformed": 1}}  # no winner: still counted
    # a long reject is a reject (capped), not a malformed line that leaves the approval standing
    got = _newest(base + [_c(2, "/reject D3: " + "n" * 1500, date=D2)])
    assert got["id"] == 2 and got["verb"] == "reject" and len(got["text"]) == 300 and got["bad_newer"] == 0
    assert "SEKRET" not in repr(later("/reject D3 SEKRET"))


def test_blanks_between_verb_and_target_are_a_bad_line_not_a_command():
    for sep in ("\u3000", "\u2003", "\u00a0", "\n", "\u200b", "\u0085", "\x0b", "\t", "\u202f"):
        line = f"/approve{sep}D3"
        assert _p(line) is None, repr(sep)
        tally = {}
        assert _newest([_c(1, line)], tally=tally) is None and tally.get("bad") == 1, repr(sep)
    for sep in ("\u3000", "\u00a0", "\t"):  # and not before the colon or between target and colon either
        assert _p(f"/reject D3{sep}: x") is None, repr(sep)


def test_a_first_line_that_only_looks_empty_is_a_bad_line():
    for body in ("\u200b", "\u200b\n/approve D3", "\ufeff/approve D3\n", "\u200b\u200b\n/approve D3", "\u2060\n/approve D3", "\u00a0", "\x0b"):
        assert _p(body) is None, repr(body)
        tally = {}
        assert _newest([_c(1, body)], tally=tally) is None and tally.get("bad") == 1, repr(body)
    for body in ("", " ", "\n", " \t \n \r\n", "\r\r"):
        tally = {}
        assert _newest([_c(1, body)], tally=tally) is None and "bad" not in tally, repr(body)


# ---- guard and newest ----------------------------------------------------------------------------------------------------------------------------

def test_guard_not_approver_excluded_and_edited():
    c = _c(1, "/approve D3")
    assert approvals.guard(c, {HUMAN}, set()) is None
    assert approvals.guard(c, set(), set()) == "not-approver"
    assert approvals.guard(c, {KIT}, set()) == "not-approver"
    assert approvals.guard(c, {HUMAN}, {HUMAN}) == "excluded-author"
    for kw in ({"last_edited_at": "2026-10-02T00:00:00Z"}, {"edit_count": 2}, {"edit_count": 1}):
        assert approvals.guard(_c(1, "/approve D3", **kw), {HUMAN}, set()) == "edited", kw
    assert approvals.guard(_c(1, "/approve D3", author=OTHER, edit_count=2), {HUMAN}, set()) == "not-approver"  # the cheaper rule names the cause


def test_edited_comment_never_counts():
    tally = {}
    assert _newest([_c(1, "/approve D3", edit_count=2)], tally=tally) is None and tally == {"edited": 1, "bad_newer": 1, "bad_newer_by": {"edited": 1}}  # no winner: counted (T28 I2)
    tally = {}
    got = _newest([_c(1, "/approve D3"), _c(2, "/reject D3", date=D2, last_edited_at="2026-10-02T00:00:00Z")], tally=tally)
    # a later edited reject is not valid, but since T28 (review I2: on GitHub a writer can edit an approver's comment) it is a newer malformed line
    assert got["id"] == 1 and got["bad_newer"] == 1 and tally == {"edited": 1, "bad_newer": 1, "bad_newer_by": {"edited": 1}}


def test_token_and_kit_authors_never_count():
    # an approver listed by mistake who is also the token identity or a kit author: excluded wins
    raws = [_c(1, "[spec-kit-ghp:decisions] x", author=KIT), _c(2, "/approve D3", date=D2, author=ME), _c(3, "/approve D3", date=D3, author=KIT),
            _c(4, "/approve D3", date="2026-10-01T10:00:09Z", author=CI)]
    tally = {}
    assert _newest(raws, approvers=(ME, KIT, CI), excluded={ME, KIT, CI}, tally=tally) is None and tally == {"excluded-author": 3}
    # and a non-approver agent that is not in the approvers list never counts either
    tally = {}
    assert _newest([_c(1, "/approve D3", author=ME)], approvers=(HUMAN,), tally=tally) is None and tally == {"not-approver": 1}


def test_newest_wins_ties_by_id():
    raws = [_c(5, "/reject D3: no", date=D1), _c(9, "/approve D3", date=D1), _c(7, "/reject D3: later id loses", date=D1)]
    assert _newest(raws)["id"] == 9 and _newest(raws)["verb"] == "approve"
    assert _newest(list(reversed(raws)))["id"] == 9
    # created dates compare as times, not as strings ("...00.5Z" is later than "...00.25Z" as a time, earlier as a string)
    assert D2 > D3 and _newest([_c(1, "/approve D3", date=D3), _c(2, "/reject D3: x", date=D2)])["id"] == 2
    assert _newest([_c(1, "/approve D3", date=D2), _c(2, "/reject D3", date="2026-10-01T10:00:00.45Z")])["id"] == 1
    # the target filters; the result carries no raw body and only the numeric author id
    one = _newest([_c(1, "/approve D4"), _c(2, "/approve D3: ok")])
    assert one == {"verb": "approve", "target": "D3", "hash": None, "text": "ok", "id": 2, "created": D1, "author": HUMAN, "bad_newer": 0}


def test_an_unreadable_creation_time_is_counted_and_never_wins():
    for bad in ("not a date", "", "2026-10-01 10:00:00", "2026-13-01T10:00:00Z", "2026-10-01T10:00:00+00:00"):
        tally = {}
        assert _newest([_c(1, "/approve D3", date=bad)], tally=tally) is None and tally == {"date": 1}, bad


def test_a_later_invalid_comment_never_cancels_a_valid_approval():
    raws = [_c(1, "/approve D3", date=D1),
            _c(2, "/reject D3: agent says no", date=D2, author=ME),  # the token identity
            _c(3, "/reject D3", date=D3.replace(".25", ".75"), author=HUMAN, edit_count=2),  # edited later
            _c(4, "/reject D3", date="2026-10-01T10:00:02Z", author=OTHER),  # not an approver
            _c(5, "> /reject D3", date="2026-10-01T10:00:03Z"),  # quoted
            _c(6, "/reject D3 trailing junk", date="2026-10-01T10:00:04Z")]
    tally = {}
    got = _newest(raws, excluded={ME}, tally=tally)
    assert got["id"] == 1 and got["verb"] == "approve"
    assert tally == {"bad": 1, "bad_newer": 2, "bad_newer_by": {"malformed": 1, "edited": 1}, "edited": 1, "excluded-author": 1, "not-approver": 1}
    assert got["bad_newer"] == 2  # the approver's later malformed line and (T28 review I2) the edited one are reported, not silently ignored
    # a later VALID reject does cancel it
    raws.append(_c(7, "/reject D3: changed my mind", date="2026-10-01T10:00:05Z"))
    got = _newest(raws, excluded={ME})
    assert got["id"] == 7 and got["verb"] == "reject"


def test_newest_hash_is_returned_for_the_caller_to_compare():
    old, new = "11" * 32, "22" * 32
    raws = [_c(1, f"/approve review-spec {old}"), _c(2, f"/approve review-spec {new}", date=D2)]
    assert _newest(raws, target="review-spec")["hash"] == new
    assert _newest(raws, target="review-plan") is None


def test_newest_does_not_mutate_or_echo():
    found = Comments([_c(1, "/approve D3: dana.contoso.example")])
    snap = repr(found)
    got = approvals.newest(found, "D3", {HUMAN}, set())
    assert repr(found) == snap and "body" not in got and "login" not in repr(got) and got["author"] == HUMAN


def test_bad_newer_counts_punctuated_and_malformed_targets():
    base = [_c(1, "/approve D3", date=D1)]
    later = lambda text: _newest(base + [_c(2, text, date=D2)])  # noqa: E731
    for line in ("/reject D3.", "/reject D3, not ok", "/reject (D3)", '/reject "D3"', "/reject D3!", "/reject D03", "/reject D\uff13", "/reject D3;", "/reject D-3",
                 "/reject d3", "/reject D3 reason", "/reject", "/reject  ", "/reject D3: ", "/reject <D3>", "/reject D 3", "/reject D3\u2026"):
        got = later(line)
        assert got["id"] == 1 and got["bad_newer"] == 1, line
    for line in ("/reject D4", "/reject D4: no", "/reject D4 because", "/reject Q2 x", "/reject review-spec", "/answer Q2", "/approve D10", "/reject D4.x"):
        assert later(line)["bad_newer"] == (1 if line == "/reject D4.x" else 0), line  # a different WELL-FORMED target is not counted; `D4.x` is not well formed
    # the other way round: the winner is for a gate and the malformed line names D3
    gate = [_c(1, f"/approve review-spec {H64}", date=D1), _c(2, "/reject D3 x", date=D2), _c(3, "/reject review-plan x", date=D3.replace(".25", ".75")),
            _c(4, "/reject review-spec", date="2026-10-01T10:00:02Z")]
    assert _newest(gate, target="review-spec")["bad_newer"] == 1  # only the line naming review-spec itself


def test_bad_newer_includes_a_retraction_over_the_read_limit():
    got = _newest([_c(1, "/approve D3", date=D1), _c(2, "/reject D3: " + "x" * 3000, date=D2)])
    assert got["id"] == 1 and got["bad_newer"] == 1  # the approval stands, so the caller must show this number


def test_a_list_that_dropped_comments_is_refused():
    found = Comments([_c(1, "/approve D3")], [Note("E_COMMENT_JOIN", 2, "only in REST")])
    with pytest.raises(approvals.ApprovalRefused) as e:
        approvals.newest(found, "D3", {HUMAN}, set())
    assert "1 comment(s)" in str(e.value) and "only in REST" not in str(e.value)
    assert approvals.newest(Comments([_c(1, "/approve D3")]), "D3", {HUMAN}, set())["id"] == 1  # no notes: fine
    rows = approvals.cached(found)  # the cache wrapper keeps the notes, so wrapping cannot launder them
    assert rows.notes == found.notes
    with pytest.raises(approvals.ApprovalRefused):
        approvals.newest(rows, "D3", {HUMAN}, set())
    assert approvals.Rows(list(found), notes=[]).notes == []  # a caller that looked at the notes says so


def test_approvers_and_exclusions_must_be_numeric_ids():
    for approvers, excl in (({"octocat"}, set()), ({True}, set()), ({HUMAN}, {"900001"}), ({HUMAN}, {str(ME)}), ({HUMAN}, {None}), ({str(HUMAN)}, set())):
        with pytest.raises(ValueError):
            approvals.newest(Comments([_c(1, "/approve D3")]), "D3", approvers, excl)


def test_non_comment_items_are_ignored():
    assert approvals.newest(["/approve D3", None, 5, {"body": "/approve D3"}, _c(1, "/approve D3")], "D3", {HUMAN}, set())["id"] == 1


# ---- gate_hash and require --------------------------------------------------------------------------------------------------------------------

def test_gate_hash_is_pinned():
    h = approvals.gate_hash("approve-push", 1001, b"plan")
    assert h == hashlib.sha256(b"spec-kit-ghp/1\napprove-push\n1001\nplan").hexdigest() and len(h) == 64
    assert len({h, approvals.gate_hash("approve-sync", 1001, b"plan"), approvals.gate_hash("approve-push", 1002, b"plan"),
                approvals.gate_hash("approve-push", 1001, b"plan2")}) == 4


def test_gate_hash_is_stable_for_identical_bytes_and_changes_with_any_byte():
    plan = b"# Plan: push\n| create | T001 |\n"
    base = approvals.gate_hash("approve-push", 7, plan)
    assert all(approvals.gate_hash("approve-push", 7, bytes(bytearray(plan))) == base for _ in range(3))
    assert approvals.gate_hash("approve-push", 7, plan + b"") == base
    changed = {approvals.gate_hash("approve-push", 7, plan[:i] + bytes([plan[i] ^ 1]) + plan[i + 1:]) for i in range(len(plan))}
    assert base not in changed and len(changed) == len(plan)  # every single-byte change gives its own hash
    assert approvals.gate_hash("approve-push", 7, plan + b"\n") != base and approvals.gate_hash("approve-push", 7, plan[:-1]) != base  # length counts
    assert approvals.gate_hash("approve-push", 7, plan.replace(b"\n", b"\r\n")) != base  # line endings count
    assert approvals.gate_hash("approve-push", 7, b"") != approvals.gate_hash("approve-push", 8, b"")
    assert approvals.gate_hash("a\nb", 7, b"c") != approvals.gate_hash("a", 7, b"b\nc")  # the frame cannot be shifted by a newline in a field


def test_verb_gates_are_the_five_applies():
    assert approvals.VERB_GATES == {"questions-post": {"approve-questions", "approve-plan-questions", "approve-answers-questions"},
                                    "push": {"approve-push", "approve-sync"}, "decisions-post": {"approve-decisions"}, "pr-create": {"approve-pr"},
                                    "branch-link": {"branch-link"}, "done": {"approve-done"}, "activate": {"approve-activate"}}  # `done`: ghp's own close gate (T28; ado's done had none)


class _NoGh:
    """A client that fails on any use: `require` reads nothing from GitHub."""

    def __getattr__(self, name):
        raise AssertionError("require touched gh." + name)


def test_terminal_mode_noop():
    for trust in (Trust([HUMAN], "terminal", {}), Trust([], "terminal", {})):
        for verb in sorted(approvals.VERB_GATES):
            assert approvals.require(_NoGh(), trust, None, verb, b"plan") is None


def test_any_approval_mode_but_terminal_is_refused():
    for mode in ("Terminal", "", None, "auto", 1, True, " terminal", "terminal ", "terminal\n", "\u200bterminal", "TERMINAL"):
        with pytest.raises(approvals.ApprovalRefused):
            approvals.require(_NoGh(), Trust([HUMAN], mode, {}), None, "push", b"plan")
    with pytest.raises(AttributeError):  # a trust without the key is not "terminal" either (never a silent pass)
        approvals.require(_NoGh(), object(), None, "push", b"plan")


# ---- fix round 1 (review of 6649231) --------------------------------------------------------------------------------------------------------------

def test_a_comment_author_id_that_is_not_an_exact_int_never_counts():
    """I1: `True == 1` and `1.0 == 1`, so a set membership test would count them as approver 1."""
    for bad in (True, 1.0, "1", None, 1 + 0j):
        c = Comment(id=1, node_id="IC_1", author=Actor(bad, "x", "User"), body="/approve D3", created_at=D1, last_edited_at=None, edit_count=0,
                    minimized=False, app=None, viewer_did_author=False)
        assert approvals.guard(c, {1}, set()) == "not-approver", repr(bad)
        assert approvals.guard(c, {1}, {1}) == "not-approver", repr(bad)  # not "excluded-author" either: the id is not an identity at all
        tally = {}
        assert approvals.newest([c], "D3", {1}, set(), tally=tally) is None and tally == {"not-approver": 1}, repr(bad)
    ok = _c(1, "/approve D3", author=1)
    assert approvals.guard(ok, {1}, set()) is None  # control: the real int 1 counts
    # a malformed line by such an author is only `bad`, never a newer retraction
    badline = Comment(id=2, node_id="IC_2", author=Actor(True, "x", "User"), body="/reject D3 oops", created_at=D2, last_edited_at=None, edit_count=0,
                      minimized=False, app=None, viewer_did_author=False)
    tally = {}
    got = approvals.newest([ok, badline], "D3", {1}, set(), tally=tally)
    assert got["id"] == 1 and got["bad_newer"] == 0 and tally == {"bad": 1}


FORMATTED = ["**/reject D3**", "# /reject D3", "## /reject D3", "`/reject D3`", "- /reject D3", "* /reject D3", "+ /reject D3", "1. /reject D3", "2) /reject D3",
             "_/reject D3_", "~~/reject D3~~", "[/reject D3]", "(/reject D3)", "\\/reject D3", "- **/reject D3**", "***/reject D3***",
             "  - `/reject D3`", "# **/reject D3**", "12. /reject D3", "\uff0a/reject D3", "\uff03 /reject D3"]


@pytest.mark.parametrize("line", FORMATTED)
def test_a_markdown_formatted_retraction_by_an_approver_is_counted(line):
    """I2: ado read `<b>/reject D3</b>` as a valid reject; here a formatted retraction is a counted malformed line, so the earlier approval no longer stands
    unreported. It is never a command (`parse_command` is None)."""
    assert _p(line) is None
    got = _newest([_c(1, "/approve D3", date=D1), _c(2, line, date=D2)])
    assert got["id"] == 1 and got["bad_newer"] == 1, line
    tally = {}
    assert _newest([_c(2, line, date=D2)], tally=tally) is None and tally == {"bad": 1, "bad_newer": 1, "bad_newer_by": {"malformed": 1}}
    assert _newest([_c(1, "/approve D3", date=D1), _c(2, line, date=D2, author=OTHER)])["bad_newer"] == 0  # not an approver's


def test_a_quote_is_still_not_counted_and_chat_with_markers_is_not_a_command():
    for line in ("> /reject D3", ">/reject D3", "  > /reject D3", "> **/reject D3**", "> - /reject D3", "**bold** /reject D3", "# Heading", "- a list item", "1. first", "1.5 ok", "**thanks**"):
        tally = {}
        assert _newest([_c(1, line)], tally=tally) is None and "bad" not in tally, line
    assert _p("/approve D3\n**/reject D3**")["verb"] == "approve"  # only the first line counts


def test_a_formatted_line_that_names_another_target_is_not_about_this_one():
    base = [_c(1, "/approve D3", date=D1)]
    assert _newest(base + [_c(2, "# /reject D4", date=D2)])["bad_newer"] == 0
    assert _newest(base + [_c(2, "- /reject review-spec x", date=D2)])["bad_newer"] == 0
    assert _newest(base + [_c(2, "1. /reject D3 why", date=D2)])["bad_newer"] == 1


def test_marker_stripping_is_linear_at_300000_characters():
    n = 300_000
    shapes = {"stars": "*" * n, "stars-cmd": "*" * n + "/reject D3", "hash": "#" * n, "dash-space": "- " * (n // 2), "digits": "1" * n, "digits-dot": "1" * n + ".",
              "list-chain": "1. " * (n // 3), "digits-dot-marks": "1." + "*" * n, "backslashes": "\\" * n, "backtick": "`" * n + "/approve D3", "mixed": "*_#`~[(-+ \t" * (n // 12),
              "cmd-then-marks": "/reject D3" + "*" * n, "marks-nl": "*\n" * (n // 2), "ticks-lines": "`\n" * (n // 2)}
    for name, body in shapes.items():
        t = time.time()
        _p(body)
        approvals.newest(Comments([_c(1, body), _c(2, body, author=OTHER, date=D2)]), "D3", {HUMAN}, {ME})
        assert time.time() - t < 1.0, name
    for name, body in {"stars-1990": "*" * 1990 + "/reject D3", "digits-1990": "1" * 1990 + ". /reject D3", "mixed-1990": "*_#`~[(-+ " * 199 + "/reject D3"}.items():
        t = time.time()
        for _ in range(200):
            _p(body)
        assert time.time() - t < 1.0, name


def test_a_malformed_approver_line_with_an_unreadable_date_counts_as_newer():
    """I3: its order cannot be told, so it may be newer than the winner (fail closed)."""
    base = [_c(1, "/approve D3", date=D1)]
    for bad_date in ("not a date", "", "2026-10-01 10:00:00"):
        tally = {}
        got = _newest(base + [_c(2, "/reject D3 typo", date=bad_date)], tally=tally)
        assert got["id"] == 1 and got["bad_newer"] == 1 and tally == {"bad": 1, "bad_newer": 1, "bad_newer_by": {"malformed": 1}}, bad_date
    assert _newest(base + [_c(2, "/reject D3 typo", date="2026-09-30T00:00:00Z")])["bad_newer"] == 0  # a readable OLDER date is harmless
    assert _newest(base + [_c(2, "/reject D3 typo", date="not a date", author=OTHER)])["bad_newer"] == 0  # not an approver


def test_an_identity_that_is_both_approver_and_excluded_is_refused():
    """M3 (as ado): excluded wins, and a malformed line by it is not a retraction."""
    raws = [_c(1, "/approve D3", author=HUMAN)]
    tally = {}
    assert approvals.newest(Comments(raws), "D3", {HUMAN}, {HUMAN}, tally=tally) is None and tally == {"excluded-author": 1}
    got = approvals.newest(Comments([_c(1, "/approve D3", author=OTHER), _c(2, "/reject D3 oops", date=D2, author=HUMAN)]), "D3", {HUMAN, OTHER}, {HUMAN})
    assert got["id"] == 1 and got["bad_newer"] == 0


def test_a_comment_id_that_is_not_an_int_has_no_order():
    """M2: `_order` is None for a non-int id (counted as `date`, never wins), a bool included."""
    for cid in ("5", 5.0, None, True):
        c = dataclasses.replace(_c(1, "/approve D3"), id=cid)
        assert approvals._order(c) is None, repr(cid)
        tally = {}
        assert _newest([c], tally=tally) is None and tally == {"date": 1}, repr(cid)
    assert approvals._order(_c(5, "x")) == (approvals._when(D1), 5)
