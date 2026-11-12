"""Business-rules normaliser and parser (V3 piece 3, P3-3; ported from ado, T12). The input is the untrusted Markdown body of an issue (HTML
embedded in it is still read, as ado's fixtures are HTML): nothing may be lost silently and no text may become structure. Fixture bodies are
synthetic (see test_rules_fixtures.py). Deviations from ado: `plain` feeds Markdown, not one div per line; the ADO mention anchors became
`@login` mentions; the docs example is not ported (docs land in T38); the Markdown tests at the end are new."""
import re
import time
from pathlib import Path

import pytest

from ghp import rules
from rules_support import make_issue

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "rules"
WID = 1201


def fixture(name):
    return (FIX / (name + ".html")).read_bytes().decode("utf-8")


def norm(html):
    return rules.normalise(html)[0]


def parsed(html, wid=WID):
    lines, probs = rules.normalise(html)
    return rules.parse(lines, wid, probs)


def plain(text, wid=WID):
    """Parse typed text: a Markdown body, one rule line per line."""
    return parsed(text, wid)


def issues(p, level=None):
    return sorted(x["issue"] for x in p["problems"] if level in (None, x["level"]))


def by_n(p):
    return {r["n"]: r for r in p["rules"]}


# ---- fixtures ----

def test_po_typed_fixture():
    p = parsed(fixture("feature_po_typed"), 1201)
    assert p["found"] and p["notes"] == []
    rs = by_n(p)
    assert sorted(rs) == [1, 2, 3]
    assert [rs[n]["status"] for n in (1, 2, 3)] == ["approved", "draft", "approved"]  # `Status : Approved` is Rule 3's
    assert rs[3]["body"] == ["Points can't pay for delivery.", "Status : Approved"]  # the unnumbered Rule: below is not absorbed
    assert rs[1]["body"][0] == "Customers earn 1 point for every whole euro they spend."
    assert [x["issue"] for x in p["problems"] if x["rule"] is None] == ["no-number"]
    assert not [x for x in p["problems"] if x["level"] == "error" and x["rule"]]
    assert {"BR-1201-1"} .isdisjoint({x["rule"] for x in p["problems"]})  # Rule 1 has Why, Example, Owner and Status


def test_epic_typed_fixture():
    p = parsed(fixture("epic_po_typed"), 1200)
    assert [(r["id"], r["status"]) for r in p["rules"]] == [("BR-1200-1", "approved")] and issues(p) == ["no-example", "no-owner", "no-why"]
    assert p["rules"][0]["body"] == ["Members must be 18 or older.", "Status: approved"]


def test_word_paste_fixture():
    lines, probs = rules.normalise(fixture("feature_word_paste"))
    assert not probs and "\u200b" not in "".join(lines) and not any("Calibri" in x or "MsoNormal" in x for x in lines)
    p = rules.parse(lines, 1201, probs)
    rs = by_n(p)
    assert sorted(rs) == [1, 2, 4] and [rs[n]["status"] for n in (1, 2, 4)] == ["approved", "draft", "approved"]
    assert rs[2]["body"][1:3] == ["When the basket holds only a gift card, then no points are earned.",
                                  "When a gift card is bought with a normal item, then only the normal item earns points."]  # the `·` bullets
    assert "24.80 euros" in " ".join(rs[1]["body"])  # &nbsp; became a space
    assert [(x["rule"], x["issue"]) for x in p["problems"] if x["level"] == "error"] == [(None, "bad-rule-line")]  # `Rule 3 -` is loud


# ---- normalisation, one test per step ----

def test_comments_removed_non_greedy():
    assert norm("<div>A<!-- hidden Rule 9: x -->B</div><div>C<!-- x --></div>") == ["AB", "C"]
    assert norm("<div>a<!---->b</div><div>c<!-->d</div>") == ["ab", "cd"]


def test_word_downlevel_revealed_comment_keeps_text():
    assert norm("<p><!--[if !supportLists]-->·<!--[endif]-->Rule 1: x</p>") == ["Rule 1: x"]
    assert norm("<p><![if !supportLists]>o<![endif]> Rule 1: x</p>") == ["Rule 1: x"]
    assert norm("<p>a</p><!--[if gte mso 9]><xml><o:p>meta</o:p></xml><![endif]--><p>b</p>") == ["a", "b"]  # hidden form: gone


@pytest.mark.parametrize("tag", ["style", "script", "xml", "STYLE", "Script", "XmL"])
def test_style_script_xml_elements_removed(tag):
    assert norm(f"<div>a</div><{tag} type=x>p {{ color: red }} Rule 9: x</{tag}><div>b</div>") == ["a", "b"]
    assert norm(f"<div>a</div><{tag}>x</{tag } ><div>b</div>") == ["a", "b"]


@pytest.mark.parametrize("html", ["<div>a</div><!-- never closed <div>Rule 1: x</div>", "<div>a</div><style>p{}</div><div>Rule 1: x</div>",
                                  "<div>a</div><SCRIPT>x</div><div>Rule 1: x</div>", "<div>a</div><xml><div>Rule 1: x</div>",
                                  "<div>a</div><style>x</style", "<div>a</div><style"])
def test_unclosed_markup_is_error(html):
    lines, probs = rules.normalise(html)
    assert lines == ["a"] and probs == [{"rule": None, "level": "error", "issue": "unclosed-markup"}]  # text after it dropped, one error


def test_mention_and_email_scrubbed():
    assert norm("Ask @jane-doe or jane.doe@contoso.com or @Bob.") == ["Ask @user or [email] or @user."]
    assert norm('<div><a href="http://x/y">a link</a> and a@b</div>') == ["a link and a@b"]


def test_text_cannot_become_structure():
    """Escaped markup stays text; a stray `<` is text, not a tag that swallows the following words."""
    assert norm("<div>&lt;b&gt;Rule 1: x&lt;/b&gt;</div>") == ["<b>Rule 1: x</b>"]
    assert norm("<div>if a < b then Rule 1: x</div>") == ["if a < b then Rule 1: x"]
    assert norm("<div>5 <5 points <b>bold</b></div>") == ["5 <5 points bold"]
    assert norm("<div>x<a y</div><div>Rule 1: z</div>") == ["x<a y", "Rule 1: z"]


def test_entities_nbsp_and_crlf():
    assert norm("<div>24.80&nbsp;euros&#160;a&amp;b</div>") == ["24.80 euros a&b"]
    assert norm("<pre>Rule 1: a\r\nStatus: draft\rWhy: b\nOwner: c</pre>") == ["Rule 1: a", "Status: draft", "Why: b", "Owner: c"]
    assert norm("a\u2028b\x0bc\x85d") == ["a", "b", "c", "d"]


def test_space_separators_become_spaces_and_zero_width_removed():
    assert norm("<div>a\u2003b\u202fc\u3000d\u1680e</div>") == ["a b c d e"]
    assert norm("<div>Ru\u200bl\u2060e\ufeff 1\u200d:\u00ad x</div>") == ["Rule 1: x"]
    assert norm("<div>a\x00b\x1fc</div>") == ["a b c"]


@pytest.mark.parametrize("bullet", list(rules.BULLETS))
def test_each_bullet_is_stripped(bullet):
    assert norm(f"<div>{bullet}  Rule 1: x</div>") == ["Rule 1: x"]
    assert norm(f"<div>{bullet}\u00a0{bullet} Rule 1: x</div>") == ["Rule 1: x"]
    assert norm(f"<div>{bullet}Rule 1: x</div>") == ["Rule 1: x"]


def test_lone_o_and_list_numbers():
    assert norm("<div>o   Rule 1: x</div>") == ["Rule 1: x"]
    assert norm("<div>o</div><div>ok then</div><div>one more</div>") == ["ok then", "one more"]  # `o` only when alone
    assert norm("<div>1. Rule 1: x</div><div>2) Rule 2: y</div><div>3.Rule</div>") == ["Rule 1: x", "Rule 2: y", "3.Rule"]
    assert norm("<div>24.80 euros</div><div>2026. Plan</div>") == ["24.80 euros", "2026. Plan"]
    assert norm("<ol><li>Rule 1: x</li><li>Rule 2: y</li></ol>") == ["Rule 1: x", "Rule 2: y"]
    assert norm("<div>· 1. Rule 1: x</div><div>1. 2. y</div>") == ["Rule 1: x", "2. y"]  # one run, then one number


def test_sign_glued_to_a_digit_is_not_a_bullet():
    assert norm("<div>-10 points</div><div>- 10 points</div><div>*5 off</div><div>-x</div>") == ["-10 points", "10 points", "*5 off", "x"]


def test_euro_sign_not_stripped():
    assert norm("<div>€5 off</div><div>$ 5 off</div><div>+1 point</div><div>(a) b</div>") == ["€5 off", "$ 5 off", "+1 point", "(a) b"]


def test_li_div_and_empty_lines():
    assert norm("<ul><li><div><b>Rule 1:</b> x </div> </li> </ul>") == ["Rule 1: x"]
    assert norm("<div><br></div><div>  </div><div>\u200b</div><div>-</div><div>·</div><div>a\t \t b</div>") == ["a b"]
    assert norm("") == [] and norm("<div></div>") == []


def test_table_rows_render_with_pipes():
    assert norm("<table><tr><td>Tier</td><td>Points</td></tr><tr><td>Gold</td><td>2</td></tr></table>") == ["Tier | Points", "Gold | 2"]


def test_oversize_input_is_an_error_not_a_parse():
    lines, probs = rules.normalise("<div>a</div>" * 100000)
    assert lines == [] and [x["issue"] for x in probs] == ["section-too-big"]


# ---- parsing ----

def test_heading_variants_and_no_rules_before_it():
    for h in ("Business rules", "business RULES", "Business rules:", "**Business rules**".strip("*")):
        assert plain(f"{h}\nRule 1: x\nStatus: approved")["rules"][0]["status"] == "approved"
    assert not plain("Rule 1: x")["rules"]  # no heading: nothing becomes a rule
    assert not plain("Business rules (v2)\nRule 1: x")["rules"]
    p = plain("Business rulesX\nRule 1: x")
    assert p["found"] and issues(p) == ["no-section-heading"] and p["rules"] == []
    assert not plain("Intro text only")["found"] and plain("Intro text only")["problems"] == []
    p = plain("Rule 1: before\nBusiness rules\nRule 2: after\nStatus: draft")
    assert issues(p, "error") == ["no-section-heading"] and [r["n"] for r in p["rules"]] == [2]  # a rule above the heading is not silently dropped


def test_text_before_rule_1_is_a_note():
    p = plain("Business rules\nSome intro the PO wrote.\nRule 1: x\nStatus: draft")
    assert p["notes"] == [rules.NOTE_INTRO] and issues(p, "error") == [] and [r["n"] for r in p["rules"]] == [1]
    assert plain("Business rules\nRule 1: x")["notes"] == []


@pytest.mark.parametrize("line", ["Status: approved", "STATUS: APPROVED", "status:Approved", "Status : Approved", "Status:   approved.", "sTaTuS :\u00a0approved."])
def test_status_case_spacing_and_trailing_period(line):
    p = plain(f"Business rules\nRule 1: x\n{line}")
    assert p["rules"][0]["status"] == "approved" and "no-status" not in issues(p)


def test_leading_zeros_same_number():
    p = plain("Business rules\nRule 01: x\nRule 002 : y")
    assert [(r["n"], r["id"]) for r in p["rules"]] == [(1, "BR-1201-1"), (2, "BR-1201-2")]
    p = plain("Business rules\nRule 1: x\nRule 001: y")  # the same number once zeros are dropped
    assert issues(p, "error") == ["duplicate-number"]


def test_ids_stable_when_rule_inserted_above():
    a = plain("Business rules\nRule 2: b\nStatus: approved\nRule 5: e\nStatus: draft")
    b = plain("Business rules\nRule 1: new\nStatus: approved\nRule 2: b\nStatus: approved\nRule 5: e\nStatus: draft")
    assert [(r["id"], r["hash"]) for r in a["rules"]] == [(r["id"], r["hash"]) for r in b["rules"] if r["n"] != 1]


def test_same_rule_different_markup_same_hash():
    a = parsed("<div><b>Business rules</b></div><div><b>Rule 1:</b> Members  must be 18.</div><div>Status: approved</div>")
    b = parsed("<p>Business rules</p><ul><li><div>Rule&nbsp;1: Members must <i>be</i> 18.</div></li></ul><p>•\u200b Status: approved</p>")
    c = parsed("<div>Business rules</div><div>Rule 1: Members must be 18.</div><div>Status: approved</div>")
    assert a["rules"][0]["hash"] == b["rules"][0]["hash"] == c["rules"][0]["hash"] and len(a["rules"][0]["hash"]) == 8


def test_hash_changes_with_status_text_and_number():
    base = rules.rule_hash(1, "approved", ["x", "Status: approved"])
    assert len({base, rules.rule_hash(1, "draft", ["x", "Status: approved"]), rules.rule_hash(2, "approved", ["x", "Status: approved"]),
                rules.rule_hash(1, "approved", ["y", "Status: approved"])}) == 4
    from ghp import decisions
    assert base == decisions.line_hash("1|approved|x")  # the Status line is not hashed: the value is in the prefix
    a = plain("Business rules\nRule 1: x\nStatus: approved")["rules"][0]
    b = plain("Business rules\nRule 1: x\nStatus: draft")["rules"][0]
    assert a["hash"] != b["hash"]
    assert plain("Business rules\nRule 1: x\nStatus: approved")["rules"][0]["hash"] != plain("Business rules\nRule 1: y\nStatus: approved")["rules"][0]["hash"]


def test_unknown_label_stays_in_body():
    p = plain("Business rules\nRule 1: x\nNote: keep me\nSee also the appendix\nStatus: approved\nRule-based pricing applies here\nStatus quo applies")
    r = p["rules"][0]
    assert r["body"] == ["x", "Note: keep me", "See also the appendix", "Status: approved", "Rule-based pricing applies here", "Status quo applies"]
    assert issues(p, "error") == []


def test_wrapped_sentences_bullets_and_table_rows_belong_to_the_rule():
    p = plain("Business rules\nRule 1: Customers earn points\nwhen they pay.\n· When a, then b.\no When c, then d.\nTier | Points\nGold | 2\nStatus: approved")
    assert p["rules"][0]["body"] == ["Customers earn points", "when they pay.", "When a, then b.", "When c, then d.", "Tier | Points", "Gold | 2", "Status: approved"]


# one test per problem key

def test_no_number():
    p = plain("Business rules\nRule: x\nStatus: approved\nRule 2: y\nRule :z")
    assert issues(p, "error") == ["no-number", "no-number"] and [r["n"] for r in p["rules"]] == [2]
    assert by_n(p)[2]["status"] == "draft"  # the unnumbered rule's Status did not leak into Rule 2 or the unnumbered rule's neighbour


@pytest.mark.parametrize("line", ["Rule 1a: x", "Rule 1.1: x", "Rule 0: x", "Rule 10000: x", "Rule 10000 : x", "Rule #1: x", "Rule \u0661: x",
                                  "Rule " + "9" * 5000 + ": x", "Rule 1" + "0" * 5000 + ": x"])
def test_bad_number(line):
    p = plain(f"Business rules\nRule 1: ok\n{line}\nStatus: approved")
    assert issues(p, "error") == ["bad-number"] and [r["n"] for r in p["rules"]] == [1]
    assert by_n(p)[1]["status"] == "draft"  # the bad rule's Status line is not Rule 1's


def test_max_number_accepted():
    p = plain("Business rules\nRule 9999: x\nRule 0008 : y")
    assert [r["id"] for r in p["rules"]] == ["BR-1201-9999", "BR-1201-8"]


@pytest.mark.parametrize("line", ["Rule 3 - x", "Rule 3 \u2013 x", "Rule 3 \u2014 x", "Rule 3 x", "Rule3: x", "Rule-3: x", "Rule - 3: x", "Rule. 3 x", "Rule 3\uff1a x",
                                  "RULE 12 words: x"])
def test_bad_rule_line(line):
    p = plain(f"Business rules\nRule 1: ok\n{line}\nStatus: approved")
    assert issues(p, "error") == ["bad-rule-line"] and [r["n"] for r in p["rules"]] == [1] and by_n(p)[1]["status"] == "draft"


@pytest.mark.parametrize("line", ["Rule-based pricing applies", "Rules: none", "Rule of thumb: be kind", "Ruler 1: x", "Rule", "Rule of thumb: be kind", "Rule for gift cards: none", "A rule of thumb: be kind"])
def test_rule_words_in_body_text_are_not_errors(line):
    p = plain(f"Business rules\nRule 1: ok\n{line}")
    assert issues(p, "error") == [] and line in by_n(p)[1]["body"]


def test_no_section_heading():
    p = plain("Rule 1: x\nStatus: approved")
    assert p["found"] and issues(p) == ["no-section-heading"] and p["rules"] == []


def test_duplicate_number():
    p = plain("Business rules\nRule 1: x\nStatus: approved\nRule 2: y\nRule 1: z\nStatus: draft")
    assert [(x["rule"], x["issue"]) for x in p["problems"] if x["level"] == "error"] == [("BR-1201-1", "duplicate-number")]
    assert [r["n"] for r in p["rules"]] == [2]  # neither copy is citable
    assert [d["body"][0] for d in p["duplicates"]] == ["x", "z"]  # both copies are kept so they can be listed under Problems


def test_unknown_status():
    for line in ("Status: maybe", "Status:", "Status: approved by Bob", "Status: Approved, draft"):
        p = plain(f"Business rules\nRule 1: x\n{line}")
        assert issues(p, "error") == ["unknown-status"] and p["rules"][0]["status"] == "draft", line


@pytest.mark.parametrize("line", ["Status - Approved", "Status \u2013 approved", "Status\u2014approved", "Status = approved", "Status. approved", "Status; draft",
                                  "Status approved", "Status Draft", "Status-approved", "Status\uff1aapproved", "status retired"])
def test_bad_status_line(line):
    p = plain(f"Business rules\nRule 1: x\n{line}")
    assert issues(p, "error") == ["bad-status-line"] and p["rules"][0]["status"] == "draft" and "no-status" not in issues(p)


def test_status_quo_is_body_text():
    p = plain("Business rules\nRule 1: x\nStatus quo applies\nStatuses: none\nStatus: approved")
    assert issues(p, "error") == [] and p["rules"][0]["status"] == "approved" and "Status quo applies" in p["rules"][0]["body"]


def test_two_status():
    for second in ("Status: approved", "Status: draft", "Status: maybe"):
        p = plain(f"Business rules\nRule 1: x\nStatus: approved\n{second}")
        assert "two-status" in issues(p, "error") and p["rules"][0]["status"] == "draft", second


def test_rule_too_long():
    ok = plain("Business rules\nRule 1: " + "a" * 1980 + "\nStatus: draft")
    assert "rule-too-long" not in issues(ok)
    long = plain("Business rules\nRule 1: " + "a" * 1990 + "\nWhy: " + "b" * 20 + "\nStatus: draft")
    assert issues(long, "error") == ["rule-too-long"] and long["rules"][0]["id"] == "BR-1201-1"


def test_section_too_big_by_size_and_by_count():
    big = plain("Business rules\nRule 1: x\n" + "\n".join("w" * 1000 for _ in range(70)))
    assert big["found"] and issues(big) == ["section-too-big"] and big["rules"] == []
    many = plain("Business rules\n" + "\n".join(f"Rule {n}: x" for n in range(1, 102)))
    assert issues(many) == ["section-too-big"] and many["rules"] == []
    assert len(plain("Business rules\n" + "\n".join(f"Rule {n}: x" for n in range(1, 101)))["rules"]) == 100


def test_table_without_rules_is_no_parsable_rules():
    p = parsed("<div>Business rules</div><table><tr><td>Tier</td><td>Points</td></tr><tr><td>Gold</td><td>2</td></tr></table>")
    assert p["found"] and p["rules"] == [] and issues(p) == ["no-parsable-rules"]
    assert issues(plain("Business rules\nRule: x")) == ["no-number", "no-parsable-rules"]
    p = plain("Business rules")  # a heading alone is not an error
    assert p["found"] and p["rules"] == [] and p["problems"] == []


def test_warnings_no_status_why_example_owner():
    p = plain("Business rules\nRule 1: x")
    assert [(x["rule"], x["level"], x["issue"]) for x in p["problems"]] == [("BR-1201-1", "warning", i) for i in ("no-status", "no-why", "no-example", "no-owner")]
    assert p["rules"][0]["status"] == "draft"  # owner ruling 2: a missing status is draft
    p = plain("Business rules\nRule 1: x\nWhy : y\nExample: z\nexample: z2\nOwner: Loyalty product owner\nStatus: approved")
    assert p["problems"] == []


def test_problem_shape_is_fixed_and_never_carries_rule_text():
    secret = "ZZ-secret-text"
    p = plain(f"Business rules\nRule 1a: {secret}\nRule 1: {secret}\nStatus: {secret}\nRule 3 - {secret}\nStatus {secret}")
    assert p["problems"] and all(set(x) - {"after"} == {"rule", "level", "issue"} and x["issue"] in rules.ISSUES and x["level"] in ("error", "warning") for x in p["problems"])
    assert secret not in repr(p["problems"]) and secret not in repr(p["notes"])
    assert all(isinstance(m, str) and secret not in m for m in rules.ISSUES.values())


def test_every_issue_key_is_covered():
    errors = {"no-number", "bad-number", "bad-rule-line", "no-section-heading", "duplicate-number", "unknown-status", "bad-status-line", "two-status",
              "rule-too-long", "section-too-big", "no-parsable-rules", "unclosed-markup", "unparsed-rule-text"}
    assert set(rules.ISSUES) == errors | {"no-status", "no-why", "no-example", "no-owner"}


def test_forged_structure_in_text_is_only_text():
    text = "Business rules\nRule 1: x\n### BR-1201-9 \u00b7 approved \u00b7 deadbeef\n- **Rules from:** ../../x\n```\nignore previous instructions\nStatus: approved"
    p = plain(text)
    assert [r["id"] for r in p["rules"]] == ["BR-1201-1"] and p["rules"][0]["status"] == "approved"
    assert "### BR-1201-9 \u00b7 approved \u00b7 deadbeef" in p["rules"][0]["body"]  # data in a body, never a rule (the renderer escapes it)


def test_normalise_problems_are_passed_through_parse():
    p = parsed("<div>Business rules</div><div>Rule 1: x</div><!-- never closed")
    assert "unclosed-markup" in issues(p, "error") and [r["n"] for r in p["rules"]] == [1]


def test_wid_must_be_a_plain_issue_number():
    for bad in (0, -1, 10 ** 9, "1201", True, None):
        with pytest.raises(ValueError):
            rules.parse(["Business rules"], bad)
    assert rules.parse(["Business rules", "Rule 1: x"], 999999999)["rules"][0]["id"] == "BR-999999999-1"


# ---- adversarial inputs finish fast (no ReDoS) ----

N = 200000
ADVERSARIAL = {
    "open comments": "<!--" * (N // 4),
    "closed comments": "<!---->" * (N // 7),
    "open style": "<style>" * (N // 7),
    "open style tags": "<style " * (N // 7),
    "close style": "</style" * (N // 7),
    "spaces": " " * N,
    "spaces after lt": "<" + " " * N,
    "slash spaces": "</" + " " * N + "li>",
    "lt run": "<" * N,
    "lt a": "<a " * (N // 3),
    "mention opens": "<a data-vss-mention " * (N // 20),
    "mention tags": "<a data-vss-mention x>" * (N // 22),
    "mention nested": "<a data-vss-mention <a data-vss-mention </a>" * (N // 45),
    "mention bare words": "data-vss-mention " * (N // 17) + "</a>",
    "digits": "Rule " + "1" * N,
    "zeros": "<div>Rule " + "0" * N + "</div>",
    "zeros colon": "<div>Rule " + "0" * N + ":</div>",
    "token digits": "<div>Rule " + "a1" * (N // 2) + "</div>",
    "letters": "a" * N,
    "emails": "a@" * (N // 2),
    "email hosts": "a@b" + ".b" * (N // 2),
    "email local": "a" * (N // 2) + "@b" + ".b" * (N // 4),
    "ats": "@" * N,
    "email dots": "a.@b." * (N // 5),
    "email short hosts": "x.y@h.z " * (N // 8),
    "email long locals": ("a" * 60 + "@b.") * (N // 64),
    "space entities": "&#32;" * (N // 5),
    "space entities between tags": "&#32;<i></i>" * (N // 12),
    "spaces between tags": " <i></i>" * (N // 8),
    "tab entities": "&#x09;" * (N // 6) + "&#0000000000000000009" * 10,
    "td": "</td>" + " " * N + "x",
    "td repeat": "</td> " * (N // 6),
    "li": "<li>" * (N // 4),
    "ol li": "<ol><li>x</li></ol>" * (N // 18),
    "br": "<br>" * (N // 4),
    "newlines": "\n" * N,
    "bullets": "·" * N,
    "bullets and o": "o " * (N // 2),
    "status": "Status" + " " * N,
    "ampersands": "&" * N,
    "list numbers": "1) " * (N // 3),
}


@pytest.mark.parametrize("name", sorted(ADVERSARIAL))
def test_adversarial_inputs_are_fast(name):
    html = ADVERSARIAL[name]
    t = time.perf_counter()
    lines, probs = rules.normalise(html)
    p = rules.parse(["Business rules"] + lines + ["Rule 1: x"], 1201, probs)
    elapsed = time.perf_counter() - t
    assert elapsed < 1.0, f"{name}: {elapsed:.2f}s"
    assert isinstance(p["rules"], list)


def test_adversarial_parse_lines_are_fast():
    lines = ["Business rules", "Rule " + "1" * N + ":", "Status" + " " * N, "Rule 1 " + "x" * N, "Rule " + "a1" * (N // 2), "x" * N, "Rule 2: y"]
    t = time.perf_counter()
    rules.parse(lines, 1201)
    assert time.perf_counter() - t < 1.0


# ---- fix round 1 ----

def _h(*lines):
    return plain("Business rules\n" + "\n".join(lines))["rules"][0]["hash"]


def test_status_spelling_and_position_do_not_change_the_hash():
    base = _h("Rule 1: x", "Why: y", "Status: approved")
    for status in ("Status: Approved", "STATUS: approved", "Status : APPROVED.", "status:approved"):
        assert _h("Rule 1: x", "Why: y", status) == base, status
    assert _h("Rule 1: x", "Status: approved", "Why: y") == base  # moved within the rule
    assert _h("Rule 1: x", "Why: y", "Status: draft") != base  # a changed status value
    assert _h("Rule 1: x2", "Why: y", "Status: approved") != base  # a changed statement
    assert _h("Rule 1: x", "Why: y2", "Status: approved") != base


def test_label_spacing_and_case_do_not_change_the_hash():
    assert _h("Rule 1: x", "Why : y", "Owner:Loyalty product owner") == _h("Rule 1: x", "WHY:   y", "owner: Loyalty product owner")
    assert _h("Rule 1: x", "Example: a", "Example: b") != _h("Rule 1: x", "Example: b", "Example: a")  # order is content


def test_hash_of_stored_body_equals_the_parsed_hash():
    r = plain("Business rules\nRule 1: x\nWhy : y\nStatus : APPROVED.")["rules"][0]
    assert rules.rule_hash(r["n"], r["status"], r["body"]) == r["hash"]


@pytest.mark.parametrize("html", ["jane&#64;contoso.com", "jane@contoso&#46;com", "<span class=SpellE>jane.doe</span>@contoso.com",
                                  "jane.doe@<span>contoso.com</span>", "jane&#x40;contoso.com", "jane\u200b.doe@contoso.com", "JANE@CONTOSO.COM"])
def test_entity_encoded_and_span_split_emails_are_scrubbed(html):
    assert norm(f"<div>Ask {html} now</div>") == ["Ask [email] now"]


def test_a_typed_at_login_is_a_mention_and_is_scrubbed():
    assert norm("<div>Ask @Jane Doe now</div>") == ["Ask @user Doe now"]  # on GitHub `@name` is a mention (ado: only an anchor was)
    assert norm("<div>mail a@b.example, not @ alone, nor user@@x</div>") == ["mail [email], not @ alone, nor user@@x"]
    assert norm("@org/team and @a.b-c") == ["@user and @user.b-c"] and norm("(@Bob_corp)") == ["(@user)"]  # a login has no dot: the rest stays text


@pytest.mark.parametrize("prefix", ["\u00d8", "\u00fc", "v", "q", "n", "\uf0b7", "\u25c6", "\u2192", "\u2705", ">", "#", "##", "(", '"', "a)", "i.", "1.1", "1",
                                    "1 |", "| 1 |", "\u00d8 \u00fc", "(a)", "\u201c"])
@pytest.mark.parametrize("label", ["Rule 2: b", "RULE 2 : b", "rule 2:b"])
def test_rule_behind_a_marker_is_flagged_not_absorbed(prefix, label):
    p = plain(f"Business rules\nRule 1: a\nStatus: approved\n{prefix} {label}\nStatus: draft")
    assert issues(p, "error") == ["bad-rule-line"] and [r["n"] for r in p["rules"]] == [1]
    assert by_n(p)[1]["status"] == "approved" and by_n(p)[1]["body"] == ["a", "Status: approved"]  # nothing absorbed, no status mix-up


@pytest.mark.parametrize("line", ["See Rule 2", "See Rule 2: the details", "the rule 2 times", "Rule-based pricing", "Note - Rule 2 applies", "In Rule 2 we",
                                  "Fuel rule 2 costs", "truleX 2: x", "Ok Rule 2: b", "(see Rule 2)", "x y z w Rule 2: b"])
def test_prose_mentioning_a_rule_is_body_text(line):
    p = plain(f"Business rules\nRule 1: a\n{line}")
    assert issues(p, "error") == [] and by_n(p)[1]["body"][-1] == line


@pytest.mark.parametrize("line", ["\uff32\uff55\uff4c\uff45 2: b", "Rule 2\uff1a b", "\uff32\uff55\uff4c\uff45 \uff12\uff1a b"])
def test_fullwidth_rule_is_flagged_not_silent(line):
    p = plain(f"Business rules\nRule 1: a\n{line}")
    assert issues(p, "error") == ["bad-rule-line"] and [r["n"] for r in p["rules"]] == [1]
    assert by_n(p)[1]["body"] == ["a"]  # the fullwidth line is not stored anywhere; the error is the signal


def test_two_rules_on_one_line_is_an_error():
    p = plain("Business rules\nRule 1: Members pay. Rule 2: Guests pay.\nStatus: approved")
    assert [(x["rule"], x["issue"]) for x in p["problems"] if x["level"] == "error"] == [("BR-1201-1", "bad-rule-line")]
    assert [r["n"] for r in p["rules"]] == [1]
    assert issues(plain("Business rules\nRule 1: Members pay (see Rule 2) always.\nStatus: approved"), "error") == []


@pytest.mark.parametrize("line", ["Status, approved", "Status (approved)", "Status [draft]", "Status / approved", "Status! approved"])
def test_any_punctuation_but_colon_after_status_is_bad_status_line(line):
    p = plain(f"Business rules\nRule 1: x\n{line}")
    assert issues(p, "error") == ["bad-status-line"] and p["rules"][0]["status"] == "draft"


def test_rule_word_probes_are_fast():
    for text in ("v " + "Rule 1" * 40000, "(" * 50000 + "Rule 1: x", "1." * 50000 + " Rule 2: x", "Rule 1: " + "Rule " * 40000, "Rule 1: " + "Rule 1" * 40000):
        t = time.perf_counter()
        rules.parse(["Business rules", "Rule 1: a", text[:60000]], 1201)
        rules.normalise("<div>" + text + "</div>")
        assert time.perf_counter() - t < 1.0


def test_fullwidth_digit_after_rule_is_bad_number():
    p = plain("Business rules\nRule 1: a\nRule \uff12: b")
    assert issues(p, "error") == ["bad-number"] and [r["n"] for r in p["rules"]] == [1]


# ---- P3-13b: no rule is dropped silently, problems carry a locator, notes are shown ----

INTRO_RULEISH = ["Rule one: Gift cards earn no points.", "Rule A: x", "R1: x", "BR-1: x", "br-1201-1: x", "Rul\u0435 one: x", "R\u03c5le A: x", "Rule \u0430: x", "Ｒｕｌｅ ｏｎｅ: x", "Ｒ１: x", "Rules: gift cards earn no points", "R 1 x",
                 "Status: approved", "Why: loyalty policy", "Example: 24 points", "Owner: Loyalty product owner",
                 "Status approved", "Status, draft", "STATUS : APPROVED"]


@pytest.mark.parametrize("line", INTRO_RULEISH)
def test_rule_like_intro_text_is_an_error_not_silent(line):
    p = plain(f"Business rules\n{line}\nStatus: approved\nRule 2: y\nStatus: approved")
    assert [r["id"] for r in p["rules"]] == ["BR-1201-2"]
    errs = [x for x in p["problems"] if x["level"] == "error"]
    assert [(x["rule"], x["issue"], x["after"]) for x in errs if x["issue"] == "unparsed-rule-text"] == [(None, "unparsed-rule-text", rules.FIRST)]
    assert "unparsed-rule-text" in rules.ISSUES and not [x for x in p["problems"] if x["rule"] == "BR-1201-2" and x["level"] == "error"]


def test_unparsed_intro_text_reaches_the_file_without_its_text():
    secret = "Gift cards earn no ZZSECRET points."
    html = f"<p>Business rules</p><p>Rule one: {secret}</p><p>Status: approved</p><p>Rule 2: y</p><p>Status: approved</p>"
    md = rules.render_file(make_issue(1201), parsed(html), "2026-11-03", "0.3.0")
    assert "- error section (before the first rule): unparsed-rule-text — " + rules.ISSUES["unparsed-rule-text"] in md
    assert "ZZSECRET" not in md and "Rule one" not in md
    assert rules.parse_file(md, 1201)["invalid"] is False


def test_plain_intro_prose_is_a_note_not_an_error():
    p = plain("Business rules\nThe loyalty programme started in 2026.\nRule 1: x\nStatus: approved")
    assert issues(p, "error") == [] and p["notes"] == [rules.NOTE_INTRO]
    p = plain("Business rules\nRule one: x\nThe loyalty programme started in 2026.\nRule 1: x\nStatus: approved")  # both: an error and a note
    assert issues(p, "error") == ["unparsed-rule-text"] and p["notes"] == [rules.NOTE_INTRO]
    assert plain("Business rules\nRule one: x\nRule 1: x\nStatus: approved")["notes"] == []  # nothing but the error to say
    assert plain("Business rules")["notes"] == [rules.NOTE_EMPTY]


def test_intro_with_no_rule_at_all_still_reports_the_rule_like_line():
    p = plain("Business rules\nRule one: x\nStatus: approved")
    assert issues(p, "error") == ["no-parsable-rules", "unparsed-rule-text"]


def test_intro_notes_and_errors_in_a_real_word_paste():
    p = parsed("<p class=MsoNormal><span>Business rules</span></p><p>R1:&nbsp;Gift cards earn no points.</p><p>Status: approved</p><p>Rule 2: y</p>")
    assert issues(p, "error") == ["unparsed-rule-text"] and [r["n"] for r in p["rules"]] == [2]


def _loc(p):
    return [(x["issue"], x.get("after")) for x in p["problems"] if x["rule"] is None and "after" in x]


def test_locator_before_the_first_rule():
    p = plain("Business rules\nRule 1a: x\nRule 2: y\nStatus: approved")
    assert _loc(p) == [("bad-number", rules.FIRST)]


def test_locator_after_a_middle_rule():
    p = plain("Business rules\nRule 1: a\nStatus: approved\nRule 2: b\nStatus: approved\nRule 3 - c\nRule 4: d\nStatus: approved")
    assert _loc(p) == [("bad-rule-line", "BR-1201-2")]


def test_locator_after_the_last_rule_and_for_several_lines():
    p = plain("Business rules\nRule 1: a\nRule 2: b\nRule: c\nRule 3x: d")
    assert _loc(p) == [("no-number", "BR-1201-2"), ("bad-number", "BR-1201-2")]


def test_locator_follows_a_duplicate_and_is_not_set_for_whole_section_problems():
    p = plain("Business rules\nRule 1: a\nRule 1: b\nRule: c")
    assert _loc(p) == [("no-number", "BR-1201-1")]
    p = plain("Rule 1: a")
    assert [x["issue"] for x in p["problems"]] == ["no-section-heading"] and "after" not in p["problems"][0]
    assert all("after" not in x for x in plain("Business rules\nRule 1: a")["problems"])  # a rule's own problems name the rule


def test_locator_is_an_id_or_a_fixed_phrase_never_text():
    p = plain("Business rules\nZZ intro Rule one: x\nRule 1a: ZZSECRET\nRule 2: y\nRule 3 - ZZSECRET")
    assert {x["after"] for x in p["problems"] if "after" in x} <= {rules.FIRST, "BR-1201-2"}
    assert "ZZ" not in repr(p["problems"])


def test_two_errors_in_one_bad_block_are_both_reported():
    p = plain("Business rules\nRule 1: ok\nStatus: approved\nRule 2 - x\nStatus approved\nRule 3: y\nStatus: approved")
    assert [(x["issue"], x["after"]) for x in p["problems"] if x["rule"] is None] == [("bad-rule-line", "BR-1201-1"), ("bad-status-line", "BR-1201-1")]
    assert [r["n"] for r in p["rules"]] == [1, 3] and by_n(p)[1]["status"] == "approved"  # the bad block's lines still are not Rule 1's


def test_bad_block_status_errors_all_listed():
    p = plain("Business rules\nRule: x\nStatus: maybe\nStatus: draft\nRule 2: y\nStatus: approved")
    assert [x["issue"] for x in p["problems"] if x["rule"] is None] == ["no-number", "two-status", "unknown-status"]
    p = plain("Business rules\nRule 1a: x\nStatus: approved\nRule 2: y\nStatus: approved")
    assert issues(p, "error") == ["bad-number"]  # a valid Status line under a bad block adds nothing


def test_duplicate_copies_report_their_own_errors_too():
    p = plain("Business rules\nRule 1: a\nStatus: maybe\nRule 1: b\nStatus: approved")
    assert sorted(x["issue"] for x in p["problems"] if x["level"] == "error") == ["duplicate-number", "unknown-status"]


def test_fold_does_not_make_ordinary_text_a_rule():
    assert plain("Business rules\nRule 1: ok\nRésumé of the old Rule 2 is below\nStatus: approved")["problems"][0]["issue"] != "bad-rule-line"
    assert plain("Business rules\nBranding applies\nRoutine: daily\nRule 1: x\nStatus: approved")["notes"] == [rules.NOTE_INTRO]


@pytest.mark.parametrize("line", ["Rul\u0435 2: b", "R\u03c5le 2: b", "Ru\u04cfe 2: b"])
def test_look_alike_letters_in_a_rule_label_are_flagged_below_a_rule_too(line):
    p = plain(f"Business rules\nRule 1: ok\n{line}\nStatus: approved")
    assert issues(p, "error") == ["bad-rule-line"] and by_n(p)[1]["status"] == "draft"


def test_notes_and_locators_are_linear_on_1_mib():
    N1 = 1 << 20
    for chunk in ("<p>Rule one: x</p>", "<p>R1: " + "е" * 40 + "</p>", "<p>" + "Status: approved </p>", "<p>BR-1: x​</p>", "<p>Rule 1a: x</p><p>Status approved</p>",
                  "<p>Ｒｕｌｅ " + "x" * 90 + "</p>", "<p>intro prose here</p>", "<p>" + "R" * 100 + "</p>"):
        html = "<p>Business rules</p>" + chunk * (N1 // len(chunk) - 1)
        html = html[:N1]
        t = time.perf_counter()
        lines, probs = rules.normalise(html)
        p = rules.parse(lines, 1201, probs)
        rules.render_file(make_issue(1201), p, "2026-11-03", "0.3.0") if len(p["rules"]) <= rules.MAX_RULES else None
        assert time.perf_counter() - t < 1.0, chunk
    t = time.perf_counter()  # many bad blocks, under the 64 KiB cap so the problems are really rendered
    p = rules.parse(["Business rules"] + ["Rule 1a: x", "Status approved"] * 2000 + ["Rule 1: y"], 1201)
    assert len(p["problems"]) >= 4000 and "section-too-big" not in issues(p)
    rules.render_file(make_issue(1201), p, "2026-11-03", "0.3.0")
    body = ["Rule 1: a"] + ["\u00d8 Rule one: " + "x" * 20, "[1] R1: y", "\uff32\uff55\uff4c\uff45 " + "\u0435" * 60] * 250
    p = rules.parse(["Business rules"] + ["\u00d8 \u00d8 \u00d8 \u00d8 Rule one: x"] * 500 + body, 1201)
    assert "section-too-big" not in issues(p)
    assert time.perf_counter() - t < 1.0


def test_problems_of_the_new_key_and_notes_never_carry_text():
    secret = "ZZ-secret-text"
    p = plain(f"Business rules\nRule one: {secret}\nStatus: {secret}\nprose {secret}\nRule 2 - {secret}\nStatus {secret}\nRule 3: x")
    assert secret not in repr(p["problems"]) and secret not in repr(p["notes"])
    assert all(n in rules.NOTES for n in p["notes"])


MARKED = ["\u00d8 {}", "\uf0b7 {}", "v {}", "> {}", "a) {}", "(1) {}", "[1] {}", "1 - {}", "\u2022 {}", "\u00d8 \u00d8 a) {}"]
SHAPES = ["Rule one: Gift cards earn no points.", "Rule A: x", "R1: x", "BR-1: x", "BR-1201-2: x", "Business rule 2: x", "Business Rule A: x",
          "Rules 2: x", "Rule No. 2: x", "Rul\u0435 one: x", "\uff32\uff55\uff4c\uff45 \uff4f\uff4e\uff45: x", "BR\u20111: x"]


@pytest.mark.parametrize("marker", MARKED)
@pytest.mark.parametrize("shape", SHAPES)
def test_marked_rule_like_intro_line_is_an_error_even_without_a_status_line(marker, shape):
    p = plain("Business rules\n" + marker.format(shape) + "\nRule 2: y\nStatus: approved")
    assert [(x["issue"], x["after"]) for x in p["problems"] if x["level"] == "error"] == [("unparsed-rule-text", rules.FIRST)], (marker, shape)


@pytest.mark.parametrize("marker", ["{}"] + MARKED)
@pytest.mark.parametrize("shape", SHAPES)
def test_rule_like_line_below_a_valid_rule_is_an_error_and_changes_nothing(marker, shape):
    base = plain("Business rules\nRule 1: ok\nStatus: approved\nRule 2: y\nStatus: approved")
    p = plain("Business rules\nRule 1: ok\nStatus: approved\n" + marker.format(shape) + "\nRule 2: y\nStatus: approved")
    errs = [(x["rule"], x["issue"], x.get("after")) for x in p["problems"] if x["level"] == "error"]
    assert errs == [(None, "unparsed-rule-text", "BR-1201-1")], (marker, shape)
    assert [r["status"] for r in p["rules"]] == ["approved", "approved"]
    assert p["rules"][1] == base["rules"][1]
    assert p["rules"][0]["body"][:2] == base["rules"][0]["body"][:2] and len(p["rules"][0]["body"]) == 3  # the line stays in Rule 1's text, nothing is merged elsewhere


def test_body_rule_like_line_with_a_status_under_it_is_one_error_not_two_status():
    p = plain("Business rules\nRule 1: ok\nStatus: approved\nRule one: x\nStatus: draft")
    assert issues(p, "error") == ["two-status", "unparsed-rule-text"] and by_n(p)[1]["status"] == "draft"  # the two Status lines are visible; so is the cause
    p = plain("Business rules\nRule 1: ok\nRule one: x\nStatus: draft")
    assert issues(p, "error") == ["unparsed-rule-text"] and by_n(p)[1]["status"] == "draft"
    assert [x["after"] for x in p["problems"] if x["issue"] == "unparsed-rule-text"] == ["BR-1201-1"]


def test_body_rule_like_line_on_a_duplicate_copy_is_reported_once():
    p = plain("Business rules\nRule 1: a\nRule one: x\nRule 1: b\nRule one: y")
    assert issues(p, "error") == ["duplicate-number", "unparsed-rule-text"]


@pytest.mark.parametrize("line", ["Rules: none", "Rule of thumb: be kind", "Rule-based pricing: yes", "Rules for gift cards apply: always",
                                  "Ruler 1: x", "Rule", "Note: see Rule 2", "See Rule 2: the details", "Brand: Contoso", "Branch 1: x", "Bring 2: x",
                                  "Why: because a rule applies", "Example: Rule one: x", "Owner: Loyalty product owner", "Tier | Points", "Policy 1: gift cards",
                                  "Regel 1: x", "Business: retail", "A rule of thumb: be kind", "Brie 2: x"])
def test_ordinary_body_text_and_labels_are_not_flagged(line):
    p = plain(f"Business rules\nRule 1: ok\n{line}\nStatus: approved")
    assert issues(p, "error") == [], line
    assert line in by_n(p)[1]["body"]


@pytest.mark.parametrize("line", ["\u0405tatus: approved", "\uff33tatus: approved", "\uff33\uff54\uff41\uff54\uff55\uff53 : approved", "\u00d8 Status: approved", "\u0405tatus approved",
                                  "Why: x", "\u00d8 Example: x"])
def test_look_alike_and_marked_label_lines_in_the_intro_are_errors(line):
    assert issues(plain(f"Business rules\n{line}\nRule 1: ok\nStatus: approved"), "error") == ["unparsed-rule-text"]


def test_a_repeated_heading_line_in_the_intro_is_only_a_note():
    p = plain("Business rules\nBusiness rules:\nRule 1: ok\nStatus: approved")
    assert issues(p, "error") == [] and p["notes"] == [rules.NOTE_INTRO]


# ---- new in ghp (T12): the input is Markdown ----

MD_TWINS = {"epic_po_typed": 1200, "feature_po_typed": 1201, "feature_word_paste": 1201}


def test_the_markdown_twins_give_ados_golden_rules():
    """Each Markdown twin (new fixture) says what ado's HTML fixture says: the same rule files line for line."""
    for name, wid in MD_TWINS.items():
        md = (FIX / (name + ".md")).read_bytes().decode("utf-8")
        got = "\n".join(rules.render_rule(r) for r in parsed(md, wid)["rules"])
        assert got == (FIX / (name + ".golden.md")).read_bytes().decode("utf-8"), name
        assert issues(parsed(md, wid), "error") == issues(parsed(fixture(name), wid), "error"), name


def test_markdown_marks_around_a_rule_are_read():
    assert norm("## Business rules") == ["Business rules"] and norm("### business RULES:") == ["business RULES"] and norm("# Business rules ##") == ["Business rules"]
    for ok in ("## Business rules", "## Business rules:", "## Business rules :", "## Business  rules ##", "##\tBusiness rules\t", "# Business rules: #"):
        assert norm(ok) == ["Business rules"], ok  # the accepted heading spellings (with/without colon, closing hashes, any blanks)
    assert norm("  ### BUSINESS RULES:  ###  ") == ["BUSINESS RULES"]
    for no in ("## Business rules x", "####### Business rules", "## Business rules:: ", "##Business rules", "## Business rules#"):
        assert norm(no) == [no.strip()], no  # not the heading: left as typed (the parser then says what is wrong)
    assert norm("**Rule 1:** x") == ["Rule 1: x"] and norm("__Rule 1:__ x") == ["Rule 1: x"] and norm("**Rule 1**: x") == ["Rule 1: x"]
    assert norm("_Why:_ y") == ["Why: y"] and norm("*Why:* y") == ["Why: y"] and norm("***Rule 1:*** x") == ["Rule 1: x"]
    assert norm("Rule 1: see [the policy](https://example.org/p) and ![pic](https://example.org/a.png)") == ["Rule 1: see the policy and !pic".replace("!pic", "pic")]
    assert norm("Rule 1: use `SAVE10` and \\*not\\* bold") == ["Rule 1: use SAVE10 and *not* bold"]
    assert norm("- Rule 1: x\n* Rule 2: y\n+ Rule 3: z\n1. Rule 4: w") == ["Rule 1: x", "Rule 2: y", "Rule 3: z", "Rule 4: w"]
    assert norm("| Tier | Points |\n|---|:--:|\n| Gold | 2 |") == ["Tier | Points", "Gold | 2"]
    assert norm("```\nRule 1: in a fence\n```\n~~~sh\nx\n~~~") == ["Rule 1: in a fence", "x"]  # the fence lines are syntax, the lines inside are visible text


def test_marks_that_are_not_read_stay_as_typed_and_are_flagged():
    assert norm("## Rule 1: x") == ["## Rule 1: x"] and norm("> Rule 1: x") == ["> Rule 1: x"]  # a heading or quote is not a rule start
    p = plain("## Business rules\nRule 1: ok\nStatus: approved\n## Rule 2: b\nStatus: draft")
    assert issues(p, "error") == ["bad-rule-line"] and [r["n"] for r in p["rules"]] == [1] and by_n(p)[1]["status"] == "approved"
    assert norm("2*3*4 and snake_case_name and *5 off and a_b_") == ["2*3*4 and snake_case_name and *5 off and a_b_"]
    assert norm("[not a link] (x) and [a]\\(b)") == ["[not a link] (x) and [a](b)"]


def test_a_markdown_body_end_to_end():
    body = ("# Loyalty\n\nEarn points.\n\n## Business rules\n\n"
            "**Rule 1:** Customers earn *1 point* per euro, see [policy](https://example.org/p).\n_Why:_ loyalty\n_Example:_ spend 10\n"
            "_Owner:_ Loyalty product owner\n_Status:_ approved\n\n<!-- Rule 9: hidden --> \n\n**Rule 2:** x\n_Status:_ draft\n")
    p = parsed(body)
    assert p["found"] and [(r["id"], r["status"]) for r in p["rules"]] == [("BR-1201-1", "approved"), ("BR-1201-2", "draft")]
    assert p["rules"][0]["body"][0] == "Customers earn 1 point per euro, see policy." and issues(p, "error") == []


MD_ADVERSARIAL = {
    "stars": "*a " * (N // 3), "underscores": "_a " * (N // 3), "strong": "**a " * (N // 4), "mixed marks": "*a_" * (N // 3),
    "open links": "[a](" * (N // 4), "open brackets": "[" * N, "link text": "[" + "a" * N, "backticks": "`" * N, "pipes": "| a " * (N // 4),
    "pipe borders": "|" * N, "fences": "```\n" * (N // 4), "headings": "## Business rules\n" * (N // 17), "backslashes": "\\" * N,
    "heading trailing blanks": "## Business rules" + " " * N + "x", "heading trailing colon blanks": "## Business rules:" + " " * N + "x",
    "heading trailing tabs": "## Business rules" + "\t" * N + "x", "heading blanks between words": "## Business" + " " * N + "x",
    "heading ws pairs": "## Business" + " \t" * N, "heading hashes": "#" * N, "heading closing hashes": "## Business rules " + "# " * (N // 2) + "x",
    "heading colon runs": "## Business rules" + ": " * (N // 2) + "x", "heading spaces before": " " * N + "## Business rules",
    # the other Markdown regexes (fix round 1: every one that sees untrusted text has a hostile case)
    "link text then blanks": "[a](" + "b" * N, "link brackets": "[a]" * (N // 3), "link long text": ("[" + "a" * 499 + "](") * (N // 503),
    "link long target": ("[a](" + "b" * 1999) * (N // 2003), "emphasis long": ("*" + "a" * 299 + " ") * (N // 301),
    "emphasis closers": "*a" + " *" * (N // 2), "mentions": "@a" * (N // 2), "mention hyphens": "@" + "a-" * (N // 2),
    "mention team": "@a/" + "b." * (N // 2), "mention ats": "@a " * (N // 3), "table blanks": "|" + " " * N + "x", "table dashes": "|-" * (N // 2),
    "table colons": "|:" * (N // 2) + "|", "fence ticks": "`" * N + "x", "fence blanks": "   " + "~" * 3 + " " * N + "x", "plus blanks": " " * N + "x",
    "plus then blanks": "+" + " " * N + "x", "escapes then marks": "\\*" * (N // 2) + "x", "strong runs": "**_" * (N // 3),
    "plus items": "+ " * (N // 2), "escaped marks": "\\*" * (N // 2), "link parens": "[a](" + "(" * N, "emphasis words": "_x_ " * (N // 4),
}


@pytest.mark.parametrize("name", sorted(MD_ADVERSARIAL))
def test_markdown_adversarial_inputs_are_fast(name):
    t = time.perf_counter()
    lines, probs = rules.normalise(MD_ADVERSARIAL[name])
    rules.parse(["Business rules"] + lines + ["Rule 1: x"], 1201, probs)
    assert time.perf_counter() - t < 1.0, name


def test_invisible_characters_are_written_as_escapes():
    """SEC-1 (ado v0.3.1): no raw invisible or bidirectional character in the shipped source, and the regex that removes them matches
    exactly the documented code points (so it is written with `\\u` escapes, not with the characters)."""
    import unicodedata
    found = [f"{p}:{n}" for p in sorted(ROOT.joinpath("src").rglob("*.py"))
             for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if any(unicodedata.category(c) == "Cf" for c in line)]
    assert not found, f"raw Cf (format/bidi) characters in: {found}"
    expected = ({0xAD, 0x61C, 0x180E, 0xFEFF} | set(range(0x200B, 0x2010)) | set(range(0x202A, 0x202F))
                | set(range(0x2060, 0x2065)) | set(range(0x2066, 0x2070)))
    assert {cp for cp in range(0x10000) if rules._INVISIBLE.fullmatch(chr(cp))} == expected and len(expected) == 29
    assert norm("Ru​l‮e⁦ 1: x‏") == ["Rule 1: x"]  # and they are gone from the text the parser sees
