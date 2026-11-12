"""Business rules written by a product owner in the Markdown body of an Epic/Feature issue (ported from ado `rules`, T12): normalise the
Markdown to plain lines, then parse `Rule <n>:` blocks (the parser is ado's, unchanged). The input is UNTRUSTED text (anyone with edit
rights on the issue can change it): no rule is dropped silently (anything doubtful becomes a named problem), text never becomes structure,
and problems carry fixed keys only, never rule text. Every pass is linear: input is capped, scans use str.find or classes that cannot
overlap, and the email and mention scrubs only ever see input shaped so they cannot backtrack badly (see `normalise`).

The reader (what is new in ghp): the input is the issue body, which is Markdown. Headings, emphasis, links, code marks, tables and fences are
read as Markdown; HTML that a body may also carry (GitHub renders a subset, and hides `<!-- -->`) is still read as ado read it, so the
golden fixtures of ado (HTML) give the same rules. `walk` follows `Issue.parent` at most 3 levels up and keeps only issues labelled
`ghp:epic` / `ghp:feature` (the `rules.types` of the config) in the SAME repository as the story (D7): a parent in another repository ends
the chain with a visible problem, because rules are never read from a repository the config does not name."""
import hashlib
import html
import json
import os
import re
import stat
import sys
import unicodedata
from datetime import date
from pathlib import Path

from . import __version__, decisions, dod, issues, stack
from .config import Config
from .gh import GhError
from .refs import Ref

BULLETS = "•·▪◦○●■□►▸➢➤✓✔§-*–—"  # fixed set; `o` only when alone, list numbers `1.`/`1)`; nothing else is stripped (`€5 off` stays)
MAX_RAW = 1 << 20  # the largest body read (ado's Description limit); a bigger input is refused, not truncated
MAX_SECTION = 64 * 1024
MAX_RULES = 100
MAX_RULE = 2000

ISSUES = {  # key -> fixed text; level is carried by each problem
    "no-number": "a `Rule:` line has no number",
    "bad-number": "a rule number is not a whole number from 1 to 9999",
    "bad-rule-line": "a line looks like a rule but is not written `Rule <n>:`",
    "no-section-heading": "rules appear without a `Business rules` heading above them",
    "duplicate-number": "two rules share a number, so neither can be cited",
    "unknown-status": "the Status is not approved, draft or retired",
    "bad-status-line": "a Status line is not written `Status: <value>`",
    "two-status": "a rule has more than one Status line",
    "rule-too-long": "a rule is longer than 2000 characters",
    "section-too-big": "the Business rules section is over 64 KiB or 100 rules, or the issue body is over 1 MiB",
    "no-parsable-rules": "the Business rules section holds no `Rule <n>:` line",
    "unparsed-rule-text": "a line looks like the start of a rule but is not written `Rule <n>:` (before the first rule or inside a rule), or a Status, Why, Example or Owner line sits above the first rule",
    "unclosed-markup": "an unclosed comment, style, script or xml block hides the text after it",
    "no-status": "no Status line (treated as draft)",
    "no-why": "no Why line",
    "no-example": "no Example line",
    "no-owner": "no Owner line",
}
_WARNINGS = frozenset(("no-status", "no-why", "no-example", "no-owner"))


FIRST = "before the first rule"  # the locator of a section-level problem above every rule; otherwise the id of the rule it follows
NOTE_INTRO = "text between the Business rules heading and the first rule is not a rule and is not copied into this file"
NOTE_EMPTY = "the Business rules section is empty"
NOTES = (NOTE_INTRO, NOTE_EMPTY)  # fixed texts: a note never carries issue text


def _problem(rule, issue, after=None):
    """`after` (section-level problems only) says where to look in the issue without echoing text: a rule id, or `FIRST`."""
    p = {"rule": rule, "level": "warning" if issue in _WARNINGS else "error", "issue": issue}
    if after is not None:
        p["after"] = after
    return p


# ---- normalise ----

_ELEMENTS = {n: re.compile(r"</" + n + r"(?![A-Za-z0-9:_-])", re.I) for n in ("style", "script", "xml")}
_ELEMENT_OPEN = re.compile(r"<(style|script|xml)(?![A-Za-z0-9:_-])", re.I)
_STRAY_LT = re.compile(r"<(?![A-Za-z/!?][^<>]*>)")  # a `<` that opens no complete tag is text (browsers agree)
# numeric/named entities that unescape to a space or tab: made non-breaking so a run of them cannot make the blank-trimming pass quadratic
_SPACE_ENTITY = re.compile(r"&#0*(?:32|9)(?![0-9]);?|&#[xX]0*(?:20|9)(?![0-9A-Fa-f]);?|&Tab;")
_ZS = re.compile("[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]")
# Cf format characters (soft hyphen, Arabic letter mark, Mongolian vowel separator, zero-width and bidi marks, word joiner, BOM), written as escapes
_INVISIBLE = re.compile("[\u00ad\u061c\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff]")
_CONTROL = re.compile("[\x00-\x08\x0e-\x1f\x7f-\x9f]")
_LEAD = re.compile("(?:\\s|[" + re.escape(BULLETS) + "](?![0-9])|o(?=\\s|$))+")  # a sign glued to a digit stays: `-10 points`
_LIST_NUMBER = re.compile(r"[0-9]{1,3}[.)](?:\s+|$)")


def _drop_comments(s):
    """Every `<!--...-->` removed; returns (text, unclosed). An unclosed one drops the rest. A Word downlevel-revealed pair
    `<!--[if x]-->text<!--[endif]-->` is two complete comments, so the text between stays."""
    out, i = [], 0
    while True:
        a = s.find("<!--", i)
        if a < 0:
            out.append(s[i:])
            return "".join(out), False
        out.append(s[i:a])
        b = s.find("-->", a + 2)  # a + 2: `<!-->` and `<!--->` are complete (empty) comments, as in HTML
        if b < 0:
            return "".join(out), True
        i = b + 3


def _drop_elements(s):
    """`<style>`, `<script>`, `<xml>` elements removed (any case); returns (text, unclosed)."""
    out, i = [], 0
    while True:
        m = _ELEMENT_OPEN.search(s, i)
        if not m:
            out.append(s[i:])
            return "".join(out), False
        out.append(s[i:m.start()])
        close = _ELEMENTS[m.group(1).lower()].search(s, m.end())
        end = s.find(">", close.end()) if close else -1
        if end < 0:
            return "".join(out), True
        i = end + 1


# ---- embedded HTML -> text (ado `dod._text`, kept here: a body may carry HTML, and ado's fixtures are HTML) ----

def _list_item(stack_):
    """re.sub callback for <ol>/<ul>/<li> tags: `n. ` inside an <ol>, `- ` otherwise."""
    def tok(m):
        close, tag = m.group(1), m.group(2).lower()
        if tag != "li":
            if not close:
                stack_.append(0 if tag == "ol" else None)
            elif stack_:
                stack_.pop()
        elif not close:
            if stack_ and stack_[-1] is not None:
                stack_[-1] += 1
                return f"\n{stack_[-1]}. "
            return "\n- "
        return "\n"
    return tok


def _strip_tags(s):
    """`re.sub(r"<[^>]+>", "", s)` in ONE pass: the regex rescanned to the end of the text for every `<` that has no `>`. The next `>` is found
    once while it lies ahead; `<>` is not a tag (it stays), a `<` with no `>` after it stays with everything after it."""
    out, i, gt, n = [], 0, -1, len(s)
    while True:
        j = s.find("<", i)
        if j < 0:
            break
        if gt < j:
            gt = s.find(">", j)
            if gt < 0:
                break
        if gt == j + 1:  # `<>`: `[^>]+` needs one character
            out.append(s[i:j + 2])
            i = j + 2
            continue
        out.append(s[i:j])
        i = gt + 1
    out.append(s[i:])
    return "".join(out)


def _html_text(raw):
    r"""Embedded HTML -> plain text, deterministic and linear: no pattern has two adjacent optional runs, tags are cut by `_strip_tags`."""
    if not raw:
        return ""
    s = raw.replace("\r", "")
    s = re.sub(r"(?i)<\s*br\s*/?>", "\n", s)
    s = re.sub(r"(?i)<\s*(?:(/)\s*)?(ol|ul|li)\b[^>]*>", _list_item([]), s)
    s = re.sub(r"(?i)<\s*/\s*t[dh]\s*>\s*(?=<\s*t[dh][\s>])", " | ", s)  # cell separator
    s = re.sub(r"(?i)<\s*(?:/\s*)?(p|div|tr|h[1-6])\b[^>]*>", "\n", s)
    s = _strip_tags(s)
    s = html.unescape(s)
    s = "\n".join(line.rstrip(" \t") for line in s.split("\n"))  # trailing blanks of every line (`[ \t]+\n`, quadratic on a long blank run)
    s = re.sub(r"\n{2,}", "\n", s)
    return s.strip()


# ---- Markdown -> text, one line at a time (the body of a GitHub issue) ----
# Only what a PO types around a rule is read as Markdown: a `Business rules` heading, bold/italic marks, links, code marks, table borders,
# backslash escapes and `+` list marks. A `#` line that is not that heading, a `>` quote and every other mark are left as typed, so the
# parser flags them (`bad-rule-line`) instead of reading structure into them.
# fullmatch on the right-stripped line, no two adjacent optional runs of blanks: a failing match is linear (a hostile line of blanks cannot stall it)
_MD_HEADING = re.compile(r"[ ]{0,3}#{1,6}[ \t]+(business\s+rules)(?:[ \t]*:)?(?:[ \t]+#+)?", re.I)
_FENCE = re.compile(r"[ ]{0,3}(?:`{3,}|~{3,})")
_TABLE_RULE = re.compile(r"[|:\- \t]+")
_STRONG = re.compile(r"(?<!\\)(?:\*\*|__)")  # an escaped mark (`\*`) is text
_EMPH = re.compile(r"(?<![\w*_\\])([*_])(?=[^\s*_])([^*_]{1,300}?)(?<=[^\s*_\\])\1(?![\w*_])")  # the class cannot cross a mark: linear
_LINK = re.compile(r"!?\[([^\[\]]{0,500})\]\([^()\s]{0,2000}\)")
_ESCAPED = re.compile(r"\\([!-/:-@\[-`{-~])")


def _markdown_line(line):
    """One normalised line -> its text, or None for a line that is only Markdown syntax (a fence line, a table rule)."""
    if _FENCE.match(line):
        return None
    m = _MD_HEADING.fullmatch(line.rstrip())
    if m:
        return m.group(1)
    t = line.strip()
    if t.startswith("|") and t.endswith("|") and len(t) > 1:
        if _TABLE_RULE.fullmatch(t):
            return None
        line = t.strip("|").strip()
    t = line.lstrip(" \t")  # a `+ ` list mark (no regex: `[ \t]*\+` is quadratic under search on a long run of blanks)
    if t[:1] == "+" and t[1:2] in (" ", "\t"):
        line = t[1:].lstrip(" \t")
    line = _LINK.sub(r"\1", line)
    line = _EMPH.sub(r"\2", _STRONG.sub("", line))
    return _ESCAPED.sub(r"\1", line).replace("`", "")


_mentions = re.compile(r"(?<![\w@])@\w[\w-]*(?:/[\w.-]+)?")  # a typed `@login` (or `@org/team`) is a GitHub mention: it reads `@user` (the committed file names no one)
_EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_LOCAL = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._%+'-")  # the local-part class of _EMAIL


def _emails(s):
    """`_EMAIL.sub("[email]", s)` in linear time (ado `comments.scrub_emails`): `_EMAIL` itself is quadratic on a long run of address
    characters, so it is matched once per `@`, anchored where the local part starts (at most 64 characters back) and ending at most 255 after:
    each character is looked at a bounded number of times, whatever the input. shortcut: an address longer than the RFC limits is cut there
    and its first characters stay; widen if that matters."""
    out, i = [], 0
    for at in re.finditer("@", s):
        p = at.start()
        if p < i or p == 0 or s[p - 1] not in _LOCAL or s.find(".", p, p + 256) < 0:
            continue  # cheap rejects: no address character before, no dot in the host window
        lo = p
        while lo > i and p - lo < 64 and s[lo - 1] in _LOCAL:
            lo -= 1
        m = _EMAIL.match(s, lo, min(len(s), p + 256))
        if m and m.end() > p:
            out.append(s[i:lo])
            out.append("[email]")
            i = m.end()
    out.append(s[i:])
    return "".join(out)


def normalise(markdown):
    """Markdown (or HTML-bearing Markdown) -> (lines, problems). Order: comments, style/script/xml elements, emails, embedded HTML to text, line
    breaks, space separators, invisible characters, then per line the Markdown marks, one leading run of BULLETS/whitespace and one list
    number, mentions, whitespace collapsed, empty lines dropped. Problems are `unclosed-markup` (text after it is dropped) or `section-too-big`
    (over 1 MiB: nothing is read)."""
    if len(markdown) > MAX_RAW:
        return [], [_problem(None, "section-too-big")]
    problems = []
    s, bad1 = _drop_comments(markdown)
    s, bad2 = _drop_elements(s)
    if bad1 or bad2:
        problems.append(_problem(None, "unclosed-markup"))
    s = _STRAY_LT.sub("&lt;", s)
    s = _emails(_INVISIBLE.sub("", s))  # invisible characters out first: `jane<ZWSP>.doe@` is one address
    s = _SPACE_ENTITY.sub(" ", s).replace("\u00a0", " ").replace("\t", " ")  # no [ \t] left for the blank-trimming pass to backtrack over
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = _emails(_INVISIBLE.sub("", _ZS.sub(" ", _html_text(s))))  # again: entity-encoded and span-split addresses only exist now
    lines = []
    for line in s.splitlines():
        line = _markdown_line(_CONTROL.sub(" ", line))
        if line is None:
            continue
        m = _LEAD.match(line)
        line = line[m.end():] if m else line
        m = _LIST_NUMBER.match(line)
        line = " ".join(_mentions.sub("@user", line[m.end():] if m else line).split())
        if line:
            lines.append(line)
    return lines, problems


# ---- parse ----

_START = re.compile(r"rule\s+([0-9]+)\s*:", re.I)
_NO_NUMBER = re.compile(r"rule\s*[:：]", re.I)
_TOKEN = re.compile(r"rule\s+([^\s:]+)\s*:", re.I)
_BAD_LINE = re.compile(r"rule\s*(?:[0-9]|[.\-–—]\s*[0-9])", re.I)
_STATUS_LINE = re.compile(r"status\s*:", re.I)
_STATUS = re.compile(r"status\s*:\s*(approved|draft|retired)\.?$", re.I)
_BAD_STATUS = re.compile(r"status\s*(?:[^\w\s:]|(?:approved|draft|retired)(?![A-Za-z0-9]))", re.I)  # any punctuation but `:` after the word
_LABELS = {k: re.compile(k + r"\s*:", re.I) for k in ("why", "example", "owner")}


_LABEL = re.compile(r"(why|example|owner)\s*:\s*", re.I)


def rule_hash(n, status, body):
    """8 hex over the number, the status value and the body: body[0] (the statement) and the other lines with whitespace collapsed
    and `Why :` / `WHY:` read as `why:`. Valid Status lines are left out (the value is already in the prefix), so their spelling
    and position never change the hash."""
    rest = [_LABEL.sub(lambda m: m.group(1).lower() + ": ", x, count=1) for x in body[1:] if not _STATUS.match(x)]
    return decisions.line_hash(f"{n}|{status}|" + " ".join(body[:1] + rest))


_MARKER = re.compile(r"[^\w\s]*\w?[^\w\s]*|[0-9]+(?:[.)][0-9]*)*[.)]?")  # a bullet, a one-character token (`Ø`, `v`, `a)`), `1.1`
_SYMBOL = re.compile(r"[^\w\s]+")
_RULE_WORD = re.compile(r"(?<![A-Za-z0-9])rule\s*\d", re.I)
_RULE_LABEL = re.compile(r"rule\s*\d[^\s:\uff1a]{0,10}\s*[:\uff1a]", re.I)  # bounded: no backtracking over a long token
_SECOND_RULE = re.compile(r"(?<![A-Za-z0-9])rule\s*\d+\s*[:\uff1a]", re.I)


def _behind_marker(line):
    """`Rule n: ...` behind a Word/Wingdings bullet or other short marker (`Ø Rule 2:`, `a) Rule 2:`, `1 | Rule 2:`, `> Rule 2:`).
    Prose (`See Rule 2`, `the rule 2 times`) has a word before it and is not flagged."""
    head = line[:100]
    m = _RULE_WORD.search(head)
    if not m or not m.start():
        return False
    tokens = head[:m.start()].split()
    if not tokens or len(tokens) > 3 or not all(_MARKER.fullmatch(t) for t in tokens):
        return False
    return all(_SYMBOL.fullmatch(t) for t in tokens) or bool(_RULE_LABEL.match(head, m.start()))


_LOOKALIKE = ("\u0430a \u0435e \u043eo \u0440p \u0441c \u0445x \u0443y \u043ak \u043cm \u0442t \u043dh \u0432b \u0456i \u0455s \u0458j \u04cfl "
              "\u03bfo \u03b5e \u03c1p \u03c5u \u03b9i \u03bak \u03bdv \u03c4t \u03b1a \u03b2b")  # Cyrillic/Greek lower-case letter + the Latin one it imitates
_CONFUSABLE = {ord(w[0]): w[1] for w in _LOOKALIKE.split()}  # applied after casefold


def _fold(s):
    """The first 120 characters as a matching key only (the stored text is never folded): NFKC, case-folded, look-alike letters
    read as Latin. Bounded, so linear over any input."""
    return unicodedata.normalize("NFKC", s[:120]).casefold().translate(_CONFUSABLE)


def _classify(line):
    """None (ordinary text), ("rule", n, rest) or ("bad", issue). A bad Rule line still ends the rule above it, so its Status
    line can never be read as the neighbour's. Fullwidth letters and digits are matched through NFKC (the key only, the stored
    text is untouched) and are an error, not a rule."""
    found = _classify_start(line)
    if found is None and (_behind_marker(line) or (not line[:120].isascii() and _classify_start(_fold(line)) is not None)):
        return ("bad", "bad-rule-line")
    return found


def _classify_start(line):
    if line[:4].lower() != "rule":
        return None
    m = _START.match(line)
    if m:
        digits = m.group(1).lstrip("0")
        if not digits or len(digits) > 4 or int(digits) > 9999:  # len first: int() of a huge digit run raises on 3.11+
            return ("bad", "bad-number")
        return ("rule", int(digits), line[m.end():].strip())
    if _NO_NUMBER.match(line):
        return ("bad", "no-number")
    m = _TOKEN.match(line)
    if m and re.search(r"\d", m.group(1)):
        return ("bad", "bad-number")
    if _BAD_LINE.match(line):
        return ("bad", "bad-rule-line")
    return None


def _status(lines):
    """(status, issues, has_status_line) from the Status lines of one block; errors only, `no-status` is the caller's."""
    marked = [x for x in lines if _STATUS_LINE.match(x)]
    valid = [_STATUS.match(x) for x in marked]
    issues, status = [], "draft"
    if len(marked) > 1:
        issues.append("two-status")
    if not all(valid):  # `Status:` followed by anything but a status word
        issues.append("unknown-status")
    if len(marked) == 1 and valid[0]:
        status = valid[0].group(1).lower()
    bad = any(_BAD_STATUS.match(x) for x in lines if not _STATUS_LINE.match(x))
    if bad:
        issues.append("bad-status-line")
    return status, issues, bool(marked) or bad


def _build(wid, n, rest, lines):
    """One rule: (R, problems). Status errors leave the status `draft`, which binds nobody."""
    rid = f"BR-{wid}-{n}"
    body = [rest] + lines
    probs = ["bad-rule-line"] if _SECOND_RULE.search(rest) else []  # `Rule 1: a. Rule 2: b.` on one line: the second one would be lost inside the first
    status, st, has = _status(lines)
    probs += st + ([] if has else ["no-status"])
    if len(" ".join(body)) > MAX_RULE:
        probs.append("rule-too-long")
    for k, label in (("why", "no-why"), ("example", "no-example"), ("owner", "no-owner")):
        if not any(_LABELS[k].match(x) for x in lines):
            probs.append(label)
    return {"id": rid, "n": n, "status": status, "body": body, "hash": rule_hash(n, status, body)}, [_problem(rid, i) for i in probs]


_LABELLED = re.compile(r"(?:status|why|example|owner)\s*[:\uff1a]", re.I)
_RULEISH = re.compile(r"(?:business\s*)?(?:rule|r\s*[0-9]|br\s*(?:[0-9]|[-\u2010-\u2015\u2212]))")  # on a folded line: how a PO might misspell `Rule 1:`
_LABEL_SHAPE = re.compile(r"(?:business\s*)?(?:rules?\s+(?:no\.?\s*)?[^\s:\uff1a]{1,12}|r\s*[0-9]+|br[\s\u2010-\u2015\u2212-]*[0-9]+(?:-[0-9]+)?)\s*[:\uff1a]")


def _forms(line):
    """The folded line and the same line without up to 3 leading marker tokens (`Ø`, `a)`, `(1)`, `[1]`, `1 -`, `>`): the keys the
    checks below read. Bounded to 120 characters, so linear."""
    f = _fold(line)
    s = f
    for _ in range(3):
        parts = s.split(None, 1)
        if len(parts) < 2 or not _MARKER.fullmatch(parts[0]):
            break
        s = parts[1]
    return (f, s) if s != f else (f,)


def _unparsed(line):
    """An intro line (above the first rule start) that is a rule or one of its labelled lines in a shape the grammar does not accept."""
    forms = _forms(line)
    if forms[0].rstrip(":") == "business rules":  # a repeated heading is not a rule
        return False
    return any(_LABELLED.match(f) or _BAD_STATUS.match(f) or _RULEISH.match(f) or _LABEL_SHAPE.match(f) for f in forms)


def _rule_like_body(line):
    """A line inside a valid rule that begins a rule the grammar does not accept (`Rule one:`, `R1:`, `BR-2:`, `Rules 2:`, `Rule No. 2:`,
    `Business rule 2:`, behind a marker or not): it would otherwise be merged into the rule above with no word. Needs the colon, so prose
    (`Rules: none`, `Rule of thumb`, `Rule 2 above applies`) stays body text; the Status/Why/Example/Owner labels are the rule's own."""
    return any(not _LABELLED.match(f) and _LABEL_SHAPE.match(f) for f in _forms(line))


def parse(lines, wid, problems=()):
    """Normalised lines -> {"found", "rules", "duplicates", "problems", "notes"}. `wid` is the issue number of the holder.
    `rules`: R = {"id": "BR-<wid>-<n>", "n", "status", "body", "hash"}, ordered as written; body[0] is the text after `Rule <n>:` (maybe
    empty) and the rest are the following lines verbatim, up to the next rule start. A number written twice is in neither `rules`
    nor citable: both copies are in `duplicates`. `found` is true when there is a heading or any rule-looking line, so a problem
    is never hidden behind "no section"."""
    if type(wid) is not int or not 0 < wid < 10 ** 9:
        raise ValueError("wid must be an issue number")
    out = {"found": False, "rules": [], "duplicates": [], "problems": [dict(p) for p in problems], "notes": []}
    probs = out["problems"]
    kinds = [_classify(x) for x in lines]
    head = next((i for i, x in enumerate(lines) if x.casefold() in ("business rules", "business rules:")), None)
    if any(kinds[:head]):  # a rule-looking line above the heading (or with no heading at all)
        probs.append(_problem(None, "no-section-heading"))
        out["found"] = True
    if head is None:
        return out
    out["found"] = True
    section, kinds = lines[head + 1:], kinds[head + 1:]
    if sum(len(x) + 1 for x in section) > MAX_SECTION or sum(1 for k in kinds if k and k[0] == "rule") > MAX_RULES:
        probs.append(_problem(None, "section-too-big"))
        return out
    blocks, intro = [], []
    for x, k in zip(section, kinds):
        if k:
            blocks.append([k, []])
        elif blocks:
            blocks[-1][1].append(x)
        else:
            intro.append(x)
    counts = {}
    for k, _ in blocks:
        if k[0] == "rule":
            counts[k[1]] = counts.get(k[1], 0) + 1
    if section and not counts:
        probs.append(_problem(None, "no-parsable-rules"))
    elif not section:
        out["notes"].append(NOTE_EMPTY)
    bad = [_unparsed(x) for x in intro]
    if any(bad):  # a rule written in a way the grammar does not accept must not vanish as "intro text"
        probs.append(_problem(None, "unparsed-rule-text", FIRST))
    if not all(bad):
        out["notes"].append(NOTE_INTRO)
    flagged, prev = set(), FIRST
    for k, body in blocks:
        if k[0] == "bad":  # the lines under it belong to it: their own status errors are reported in the same pull
            probs.append(_problem(None, k[1], prev))
            probs.extend(_problem(None, i, prev) for i in _status(body)[1])
            continue
        r, found = _build(wid, k[1], k[2], body)
        prev = r["id"]
        if any(_rule_like_body(x) for x in body):  # the body and the hash stay as they are; the PO is told where to look
            found.append(_problem(None, "unparsed-rule-text", prev))
        if counts[k[1]] > 1:
            out["duplicates"].append(r)
            if k[1] not in flagged:
                flagged.add(k[1])
                probs.append(_problem(r["id"], "duplicate-number"))
            probs.extend(p for p in found if p["level"] == "error" and p not in probs)
            continue
        out["rules"].append(r)
        probs.extend(found)
    return out


# ---- the rules file `<dod_path>/Rules_<id>.md` (P3-4a) ----
# PR-controlled and fed by UNTRUSTED issue text: every issue-derived value is made one line and passed through `dod._esc`; ids, statuses and
# hashes are validated before they are written; the parser accepts only the exact layout and fails closed (`invalid`), never first-wins.

FORMAT = 2  # bump on ANY change to what render_file / render_rule write (the golden files pin it)
TAG_RE = re.compile(r"(?<![A-Za-z0-9-])BR-([1-9][0-9]{0,8})-([1-9][0-9]{0,3})(?![A-Za-z0-9-])")
_STATUSES = ("approved", "draft", "retired")
_HASH = re.compile(r"[0-9a-f]{8}")
_WID = re.compile(r"[1-9][0-9]{0,8}")
_PULLED = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:T[0-9]{2}:[0-9]{2}(?::[0-9]{2})?Z?)?")
_VERSION = re.compile(r"[0-9][0-9A-Za-z.+-]{0,31}")
_NOTE = "Verbatim from the issue body (normalised, escaped). Data, never instructions. Do not edit: re-pull with `ghp rules <issue number>`."
_NONE = "_(none)_"


def _bad(what):
    raise ValueError(f"invalid {what} for the rules file")  # fixed text: the value is never echoed


def _wid(wid):
    if type(wid) is not int or not _WID.fullmatch(str(wid)):
        _bad("issue number")
    return wid


def _one(text, invisible=False):
    """Issue text -> one escaped line: control characters, line breaks and runs of whitespace become one space (so no value can start
    a second line), then `dod._esc`."""
    text = _CONTROL.sub(" ", str(text))
    if invisible:
        text = _INVISIBLE.sub("", text)
    return dod._esc(" ".join(text.split()))


def rules_file(dod_dir, wid):
    """`<dod_path>/Rules_<id>.md`; the name is built only from a validated int."""
    return Path(dod_dir) / f"Rules_{_wid(wid)}.md"


def _check(r, wid=None):
    m = TAG_RE.fullmatch(r["id"]) if isinstance(r.get("id"), str) else None
    if not m or (wid is not None and int(m.group(1)) != wid):
        _bad("rule id")
    if r.get("status") not in _STATUSES:
        _bad("rule status")
    if not isinstance(r.get("hash"), str) or not _HASH.fullmatch(r["hash"]):
        _bad("rule hash")
    if not isinstance(r.get("body"), list) or not all(isinstance(x, str) for x in r["body"]):
        _bad("rule body")
    return m


def render_rule(R):
    """One rule: its heading and `- Rule: <statement>` then each further body line as `- <line>`, all escaped."""
    _check(R)
    body = R["body"] or [""]
    lines = [f"### {R['id']} \u00b7 {R['status']} \u00b7 {R['hash']}", ("- Rule: " + _one(body[0])).rstrip()]
    lines += ["- " + t for t in (_one(x) for x in body[1:]) if t]
    return "\n".join(lines) + "\n"


def _problem_line(p, wid):
    if p.get("issue") not in ISSUES or p.get("level") not in ("error", "warning"):
        _bad("problem")
    rid, after = p.get("rule"), p.get("after")
    if rid is not None:
        _check({"id": rid, "status": "draft", "hash": "0" * 8, "body": []}, wid)
    where = rid or "section"
    if after is not None:
        if rid is not None or not isinstance(after, str):
            _bad("problem")
        if after != FIRST:
            _check({"id": after, "status": "draft", "hash": "0" * 8, "body": []}, wid)
        where += f" ({FIRST if after == FIRST else 'after ' + after})"
    return f"- {p['level']} {where}: {p['issue']} \u2014 {ISSUES[p['issue']]}"


def _problem_lines(parsed, wid):
    plines = [_problem_line(p, wid) for p in parsed["problems"]]
    for r in parsed.get("duplicates", ()):
        _check(r, wid)
        plines.append(f"- copy of {r['id']} \u00b7 {r['hash']}: " + _one(" / ".join(r["body"])))
    return plines


def render_file(issue, parsed, pulled, version, kind="Issue"):
    """The whole file (R§5): header, `## Rules` ordered by number, `## Problems` (problem keys and rule ids; the copies of a duplicated
    number are listed there, escaped, and are not rules), then `## Notes` when the parser made any (fixed texts). Deterministic. `issue`: an
    `issues.Issue` (its number, repository and title are used); `kind`: the holder type shown in the first line (`Epic`, `Feature`)."""
    wid = _wid(issue.ref.number)
    if not isinstance(pulled, str) or not _PULLED.fullmatch(pulled):
        _bad("pulled date")
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        _bad("version")
    url = f"https://github.com/{issue.ref.owner}/{issue.ref.repo}/issues/{wid}"  # the Ref is validated by parse_ref; no issue text
    out = [f"# Business rules \u2014 {_one(kind or 'Issue', True)} {wid}: {_one(issue.title or '', True)}",
           "", f"- **Issue:** {url}", f"- **Last pulled:** {pulled} \u00b7 rules-format: {FORMAT} \u00b7 spec-kit-ghp {version}", "", _NOTE, "", "## Rules", ""]
    for r in parsed["rules"]:
        m = _check(r, wid)
        if type(r.get("n")) is not int or r["n"] != int(m.group(2)):
            _bad("rule id")
    rs = sorted(parsed["rules"], key=lambda r: r["n"])
    out += [x for r in rs for x in (render_rule(r).rstrip("\n"), "")] or [_NONE, ""]
    plines = _problem_lines(parsed, wid)
    notes = list(parsed.get("notes") or ())
    if not all(n in NOTES for n in notes):
        _bad("note")
    tail = ["## Notes", ""] + ["- " + n for n in notes] + [""] if notes else []  # only when there is a note, so a file without one is unchanged
    return "\n".join(out + ["## Problems", ""] + (plines or [_NONE]) + [""] + tail)


def read_file(path, root, dod_path):
    """The text of a rules file, else None: `gate._read` (no symlink, real path inside `root`, regular file, 1 MiB, UTF-8) plus the real
    path must lie inside the real `dod_path`. Nothing outside is opened; a refusal says nothing about why."""
    try:
        real, top = os.path.realpath(path), os.path.realpath(dod_path)
        if os.path.commonpath([real, top]) != top or os.stat(real).st_nlink != 1:  # a hard link may name a file outside the repo
            return None
    except (OSError, ValueError, TypeError):
        return None
    from . import gate  # lazy: gate will import this module
    return gate._read(path, root, set())


_HEADING = re.compile(r"### (BR-[^ ]*) \u00b7 ([a-z]+) \u00b7 ([0-9a-f]{8})")
_PULLED_LINE = re.compile(r"- \*\*Last pulled:\*\* \S+ \u00b7 rules-format: ([1-9][0-9]{0,3}) \u00b7 spec-kit-ghp \S+")


_UNESCAPE = re.compile(r"^\\(?=```|~~~|#)|&lt;!--")


def _unescape(line):
    """The inverse of `dod._esc` for one line (ado `dod._unescape`; lossy only for text that itself holds the marker)."""
    return _UNESCAPE.sub(lambda m: "<!--" if m.group(0) != "\\" else "", line)


def _body_hash(rid, status, lines):
    """`rule_hash` over the body rebuilt from the rendered lines (`- Rule: <statement>`, then `- <line>`; `_unescape` undoes the
    escaping), or None when the lines are not in that shape."""
    if not lines or not (lines[0] == "- Rule:" or lines[0].startswith("- Rule: ")):
        return None
    body = [_unescape(lines[0][8:])]
    for x in lines[1:]:
        if not x.startswith("- "):
            return None
        body.append(_unescape(x[2:]))
    return rule_hash(int(TAG_RE.fullmatch(rid).group(2)), status, body)


def parse_file(md, wid):
    """Rules file text -> {"format": int|None, "rules": {id: {"status", "hash", "section", "body_hash"}}, "problems": [lines], "notes": [texts], "invalid": bool}.
    `section` is the heading line and the lines under it. `hash` is the heading's; `body_hash` is `rule_hash` of the body rebuilt from
    those lines (None if they are not in the rendered shape), so text edited under an unchanged heading shows as `body_hash != hash`.
    `_esc` is not exactly reversible (a line that starts with a list marker and then a fence or `#`, such as `+ ~~~`; a literal backslash before a fence or `#`; a literal `&lt;!--` in the source text), so for
    such rules `body_hash` may differ from `hash` on a clean file; that is reported as a mismatch and fails closed. Anything that is not the exact layout makes the file `invalid` (and `rules`
    empty): a heading-like line that is not canonical or names another issue, a duplicate heading or header line, a missing or
    repeated `## Rules` / `## Problems` / `## Notes`, or any fence or comment (a rendered file has none, so hiding text is itself the finding)."""
    wid = _wid(wid)
    out = {"format": None, "rules": {}, "problems": [], "notes": [], "invalid": False}
    vis, still_open = dod._visible(md)
    raw, lines = md.splitlines(), vis.split("\n")
    if still_open or lines[:len(raw)] != raw:
        out["invalid"] = True
        return out
    state, rule_list, pulled = 0, {}, []  # state: 0 header, 1 rules, 2 problems, 3 notes
    cur = None
    for i, line in enumerate(raw):
        if re.match(r"[ \t]*#", line):
            m = _HEADING.fullmatch(line) if state == 1 else None
            tag = TAG_RE.fullmatch(m.group(1)) if m else None
            if i == 0 and line.startswith("# Business rules "):
                continue
            if line == "## Rules" and state == 0:
                state, cur = 1, None
            elif line == "## Problems" and state == 1:
                state, cur = 2, None
            elif line == "## Notes" and state == 2:
                state, cur = 3, None
            elif tag and int(tag.group(1)) == wid and m.group(2) in _STATUSES and m.group(1) not in rule_list:
                cur = rule_list[m.group(1)] = {"status": m.group(2), "hash": m.group(3), "lines": [line]}
            else:
                out["invalid"] = True
                return out
        elif state == 0 and line.startswith("- **Last pulled:**"):
            pulled.append(line)
        elif state == 1 and cur:
            cur["lines"].append(line)
        elif state == 2 and line.startswith("- "):
            out["problems"].append(line)
        elif state == 3 and line.startswith("- "):
            out["notes"].append(line[2:])
    if len(raw) < 2 or state not in (2, 3) or not raw[0].startswith("# Business rules ") or len(pulled) > 1:
        out["invalid"] = True
        return out
    m = _PULLED_LINE.fullmatch(pulled[0]) if pulled else None
    out["format"] = int(m.group(1)) if m else None
    for k, v in rule_list.items():
        sec = "\n".join(v["lines"]).rstrip()
        out["rules"][k] = {"status": v["status"], "hash": v["hash"], "section": sec, "body_hash": _body_hash(k, v["status"], sec.split("\n")[1:])}
    return out


# ---- the hierarchy walk and `ghp rules <n>` (P3-4b): GETs only; every GET finishes before any file is written ----

WALK_ISSUES = {"foreign-parent": "an issue's parent lives in another repository, so the walk stops there: rules are read from the configured repository only"}
MAX_LINKS = 3


def _key_of(ref):
    return (ref.owner.lower(), ref.repo.lower(), ref.number)  # GitHub names are case-insensitive


def walk(gh, ref, types, problems=None, story=None):
    """GET the story, then follow `Issue.parent` at most 3 links up (a visited set stops a cycle; an 8th nesting level is simply never reached).
    Returns, nearest first, `{"id": issue number, "type", "issue", "parsed"}` for each ancestor labelled `ghp:<type>` (case-folded) for a `type`
    of `types` and that holds a Business rules section or a problem in it (an unclosed comment hides the section, which must not pass as "no
    section"). Only the story's own repository is read (D7): a parent in another repository ends the chain and appends `foreign-parent` to
    `problems` (as `{"id": the issue number whose parent it is, ...}`); the foreign issue is never fetched. Any GitHub error propagates:
    nothing is partial. `story`: the already-fetched `Issue` of `ref` (saves one GET), else it is read here."""
    want = [(f"ghp:{str(t).casefold()}", str(t)) for t in types]
    here, out = (ref.owner.lower(), ref.repo.lower()), []
    issue = story if story is not None else issues.get(gh, ref)
    seen = {_key_of(issue.ref)}
    for _ in range(MAX_LINKS):
        parent = issue.parent
        if parent is None or _key_of(parent) in seen:
            break
        if (parent.owner.lower(), parent.repo.lower()) != here:
            if problems is not None:
                problems.append({"id": issue.ref.number, "rule": None, "level": "error", "issue": "foreign-parent"})
            break
        issue = issues.get(gh, Ref(ref.owner, ref.repo, parent.number))  # the configured spelling of the repository, whatever case the link used
        seen.add(_key_of(issue.ref))
        labels = {x.casefold() for x in issue.labels}
        kind = next((name for label, name in want if label in labels), None)
        if kind is not None:
            lines, probs = normalise(issue.body)
            parsed = parse(lines, issue.ref.number, probs)
            if parsed["found"] or parsed["problems"]:
                out.append({"id": issue.ref.number, "type": kind, "issue": issue, "parsed": parsed})
    return out


def _today():
    return date.today().isoformat()


def _current(path, wid):
    """The text of the file about to be replaced, None when absent. A symlink, a non-regular file or an unreadable one is refused (a fixed
    text), so nothing is ever written through or over it."""
    if not os.path.lexists(path):
        return None
    try:
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_RAW:
            raise OSError
        with open(path, encoding="utf-8", newline="") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        raise ValueError(f"the rules file of issue {wid} exists but is not a readable regular file; move it away and pull again")


def _render(item, pulled, current):
    """The new text of one file. `pulled` is kept from `current` when that gives identical bytes, so a re-pull with nothing new in the issue
    is no change (the date alone never makes a diff); the file's CRLF terminator is kept."""
    kept = re.search(r"^- \*\*Last pulled:\*\* (\S+) \u00b7", (current or "").split("\n## Rules\n", 1)[0], re.M)
    crlf = bool(current) and current.splitlines(keepends=True)[0].endswith("\r\n")
    for stamp in ((kept.group(1),) if kept and _PULLED.fullmatch(kept.group(1)) else ()) + (pulled,):
        text = render_file(item["issue"], item["parsed"], stamp, __version__, item["type"])
        text = text.replace("\n", "\r\n") if crlf else text
        if text == current:
            return text, False
    return text, True


def pull(gh, cfg, ref):
    """`ghp rules <n>`: walk, then (all GETs done) write `<dod_path>/Rules_<ancestor number>.md` for each kept ancestor whose bytes change.
    Returns ids, counts, keys and paths only, never rule text or a title; every problem carries the number of the issue it belongs to. Works
    whatever `rules.enabled` says (only prompts, the gate and strict read it). ValueError (exit 2): a file that cannot be replaced,
    an unreadable decisions file, a failed write; nothing is written before every check has passed."""
    problems = []
    found = walk(gh, ref, cfg.rules["types"], problems)
    from . import gate  # lazy: gate will import this module
    plan, decs, counts, notes = [], [], {"approved": 0, "draft": 0, "retired": 0}, 0
    for it in found:
        path = rules_file(cfg.dod_path, it["id"])
        cur = _current(path, it["id"])
        text, changed = _render(it, _today(), cur)
        plan.append((it["id"], path, text, changed))
        problems += [{"id": it["id"], **p} for p in it["parsed"]["problems"]]
        notes += len(it["parsed"]["notes"])
        for r in it["parsed"]["rules"]:
            counts[r["status"]] += 1
        dpath = decisions.decisions_file(cfg.dod_path, it["id"])
        if os.path.lexists(dpath):
            md = gate._read(dpath, ".", set())  # confined like the gate's reads
            if md is None:
                raise ValueError(f"the decisions file of issue {it['id']} exists but is not a readable regular file")
            d = decisions.parse_decisions(md)
            decs.append({"id": it["id"], "open": len(d["open"]), "rejected": len(d["rejected"])})
    for wid, path, text, changed in plan:
        if changed:
            try:
                decisions._atomic_write(path, text)
                with open(path, encoding="utf-8", newline="") as f:
                    ok = f.read() == text
            except OSError:
                ok = False
            if not ok:
                raise ValueError(f"could not write the rules file of issue {wid}")
    return {"from": [p[0] for p in plan], "files": [str(p[1]) for p in plan], "rules": counts, "problems": problems,
            "changed": [p[0] for p in plan if p[3]], "decisions": decs, "notes": notes}


# ---- tags in the ledger and the scope of a story (P3-6): pure functions over PR-controlled files ----

_RUN = re.compile(r"[A-Za-z0-9-]+")  # a tag is a whole run of these (TAG_RE's look-arounds), so a run is a tag or it is not
MAX_UNKNOWN = 100
MAX_TOKEN = 40


def tags(ledger_md):
    """`{"rows": {"AC-<n>": [rule ids]}, "unknown": [str]}` from cell 1 (the criterion) of the section 3 rows, found by the SAME
    visible-line walk as `gate.parse_section3` (a row in a fence or comment is not a row; a duplicate AC row adds its tags to the same list).
    Every run of letters, digits and `-` that holds `BR-` and is not exactly a `TAG_RE` match (`BR-1201-0`, `xBR-1201-1`, `BR-1201-1a`)
    goes to `unknown`, cut to 40 characters, at most 100: it is a count for the caller to report, never text to echo. Linear."""
    from . import gate  # lazy: gate will import this module
    out, unknown = {}, []
    for line in dod._visible(dod.section(ledger_md, 3) or "")[0].splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not gate._AC_RE.match(cells[0]):
            continue
        ids = out.setdefault(cells[0], [])
        for m in _RUN.finditer(cells[1] if len(cells) > 1 else ""):
            if TAG_RE.fullmatch(m.group()):
                if m.group() not in ids:
                    ids.append(m.group())
            elif "BR-" in m.group() and len(unknown) < MAX_UNKNOWN and m.group()[:MAX_TOKEN] not in unknown:
                unknown.append(m.group()[:MAX_TOKEN])
    return {"rows": out, "unknown": unknown}


_OUT = "out of scope:"
_OUT_LOOSE = re.compile(r"(?:supersedes\s*D[0-9]+\s*:\s*)*out\s+of\s+scope", re.I)  # whatever reads like an exclusion, prefixes included, is reported if it breaks the grammar
MAX_EXCLUDED = 50


def _exclusion_text(d):
    t = d["text"]
    m = decisions._SUP.match(t)  # one leading `supersedes D<n>: ` only
    return t[m.end():] if m else t


def exclusion_ids(d):
    """The rule ids a parsed decision excludes: its text (after one leading `supersedes D<n>: `) is exactly `out of scope:` and
    1 to 50 comma-separated `TAG_RE` ids, spaces only, nothing else. Otherwise None (a duplicate id is listed once)."""
    t = _exclusion_text(d)
    if not t.startswith(_OUT):
        return None
    parts = [p.strip(" ") for p in t[len(_OUT):].split(",")]
    if len(parts) > MAX_EXCLUDED or not all(TAG_RE.fullmatch(p) for p in parts):
        return None
    return list(dict.fromkeys(parts))


def rejected_exclusion(d):
    """True for a parsed decision line that is a well-formed exclusion (`exclusion_ids` is not None) of kind decision or assumption (the kinds
    `scope` reads) whose `posted:` marker matches its CURRENT text (the PO saw exactly this exclusion). With rules enabled the PO's rejection of
    such a line is carried out by the coverage check (the rule is back in scope), so `decision-rejected` and the gate's `open_decisions` leave it
    out; any other rejected line (a malformed exclusion, an override, an unposted line, a line re-worded after it was posted) still blocks."""
    return (d["kind"] != "override" and exclusion_ids(d) is not None and bool(d["posted"])
            and d["posted"]["hash"] == decisions.marker_hash(d))


def local_files(cfg, ledger_md, root, base=None):
    """`{issue number: parse_file result}` for the rules files that the ledger's `Rules from:` names and that can be read: only digits-only ids of a
    well-formed header line ever build a path, each file is read as confined as the ledger (`read_file`: no symlink, no hard link, a regular file of
    at most 1 MiB, UTF-8, under `dod_path` and `root`); an unreadable one is simply absent (the coverage check reports it as missing). `base`: the
    directory `cfg.dod_path` is relative to (None: the working directory)."""
    frm, files = dod.ledger_rules_from(ledger_md), {}
    dod_path = cfg.dod_path if base is None else Path(base) / cfg.dod_path
    for wid in frm["ids"] if frm["valid"] else []:
        text = read_file(rules_file(dod_path, wid), root, dod_path)
        if text is not None:
            files[wid] = parse_file(text, wid)
    return files


def _key(rid):
    m = TAG_RE.fullmatch(rid)
    return int(m.group(1)), int(m.group(2))


def scope(files, rules_from, decisions_parsed, phase):
    """`{"in_scope": [ids], "excluded": [ids], "bad_exclusions": ["D<n>"]}`, ids by (issue, number). `files` maps a issue number (int)
    to its `parse_file` result, `rules_from` is the ledger's id list. In scope: every `approved` rule of those files (an `invalid` file
    or a missing id gives none; the caller reports it; a rule whose `body_hash` differs stays in scope, the caller reports it) minus the
    rules a counted exclusion names. Over `decisions.effective`, a decision line counts when it is of kind decision or assumption, its
    text is exactly `exclusion_ids`' grammar, it carries a `posted:` marker equal to `decisions.marker_hash` of its CURRENT text, and
    it is open or validated (phase `pr`) / validated (phase `done`). A rejected exclusion excludes nothing and is not reported. A line
    that starts like an exclusion (after any number of `supersedes D<n>:` prefixes, however spaced) but breaks the grammar (not rejected) is in `bad_exclusions`. ValueError for any other phase."""
    if phase not in ("pr", "done"):
        raise ValueError("phase must be pr or done")
    live = ("validated",) if phase == "done" else ("open", "validated")
    eff = decisions.effective(decisions_parsed)
    named, bad = set(), []
    for k in ("open", "validated"):
        for d in eff[k]:
            if d["kind"] == "override":
                continue
            ids = exclusion_ids(d)
            if ids is None:
                if _OUT_LOOSE.match(d["text"].lstrip()):
                    bad.append(d["id"])
            elif k in live and d["posted"] and d["posted"]["hash"] == decisions.marker_hash(d):
                named.update(ids)
    approved = set()
    for wid in rules_from:
        f = files.get(wid) if type(wid) is int else None
        if isinstance(f, dict) and not f.get("invalid"):
            approved.update(r for r, v in f["rules"].items() if TAG_RE.fullmatch(r) and _key(r)[0] == wid
                            and (v["status"] == "approved" or v.get("body_hash") != v["hash"]))  # a mismatch/None: the heading status is untrusted
    return {"in_scope": sorted(approved - named, key=_key), "excluded": sorted(approved & named, key=_key),
            "bad_exclusions": sorted(set(bad), key=lambda i: int(i[1:]))}


# ---- coverage: the local half of the gate's `rule_coverage` (P3-8); pure over PR-controlled files, ids and fixed keys out ----

_AC_CANON = re.compile(r"AC-([1-9][0-9]{0,8})")  # the one spelling of a row that counts as AC-<n> (not `AC-01`, not fullwidth digits)
_PROBLEM_LINE = re.compile(r"- (?:error|warning) (section(?: \((?:after BR-[0-9]+-[0-9]+|" + FIRST + r")\))?|BR-[0-9]+-[0-9]+): ([a-z-]+) \u2014 (.*)")
_COPY_LINE = re.compile(r"- copy of (BR-[0-9]+-[0-9]+) \u00b7 [0-9a-f]{8}: ")


def _file_problems(wid, lines):
    """`<rule id|issue number>:<key>` for each error problem line of a rules file, in order, once each. The level comes from the KEY (a PR
    cannot lower an error by editing the word in the line), the text must be the fixed one, and a line of any other shape is `<wid>:?`:
    nothing from the line is ever echoed."""
    out = []
    for x in lines:
        m, c = _PROBLEM_LINE.fullmatch(x), _COPY_LINE.match(x)
        if m and m.group(2) in ISSUES and m.group(3) == ISSUES[m.group(2)] and (m.group(1).startswith("section") or TAG_RE.fullmatch(m.group(1))):
            item = None if m.group(2) in _WARNINGS else f"{wid if m.group(1).startswith('section') else m.group(1)}:{m.group(2)}"
        elif c and TAG_RE.fullmatch(c.group(1)):
            item = f"{c.group(1)}:duplicate-number"
        else:
            item = f"{wid}:?"
        if item and item not in out:
            out.append(item)
    return out


def coverage(ledger_md, sources, files, rules_from, reviewed, decisions_parsed, phase, trace=None):
    """The local findings of a story's business rules: `{"in_scope", "covered", "excluded", "uncovered", "unknown", "unconfirmed", "changed",
    "problems", "missing", "format", "header"}`, every value a list of rule ids, issue numbers or fixed keys (never file or ledger text).
    `files`: `{issue number (int): parse_file result}` for the files that could be read; `rules_from` / `reviewed` are the results of
    `dod.ledger_rules_from` / `dod.ledger_rules_reviewed`; `decisions_parsed` is `decisions.parse_decisions`; `phase` is `pr` or `done`.
    - `missing`: an id of `Rules from:` with no usable file (absent, unreadable or `invalid`); `format`: a file whose `rules-format` is not
      this kit's (re-pull); both get no per-rule judgement. `header`: `rules-from` when `Rules from:` is absent or malformed (fix:
      `ghp dod <n> --refresh`), `rules-reviewed` when `Rules reviewed:` is malformed.
    - `in_scope` / `excluded`: `scope(...)`. `covered`: an in-scope rule cited in a row `AC-<n>` (1 <= n <= the AC lines of section 1) whose
      tests include one that is present; an AC with several rows counts only when EVERY one of its rows has a present test (`tags` merges
      duplicate rows, so one row with an absent test must not be hidden by another). `uncovered`: in scope and not covered.
    - `unknown`: a cited well-formed id found in no readable file (ids of `missing` / `format` files are left to those findings), and
      one `?` per malformed `BR-` token in the table (a count: the token is PR text and is not repeated).
    - `unconfirmed`: an in-scope rule with no recorded hash whose file text matches its heading hash: the first confirm is free
      (`ghp rules <n> --confirm`, no terminal). `changed`: a human's, never free: an in-scope rule whose heading hash is not the one in
      `Rules reviewed:`, a rule whose file text no longer matches its heading hash (recorded or not: `--confirm` refuses it), and any
      recorded id that is still `approved` in a file and has another hash, in scope or not. A malformed `Rules reviewed:` line makes nothing free (every in-scope
      rule is `changed`, next to the `rules-reviewed` header finding). The two lists never share an id. A recorded id that left the files or is no longer approved is not judged here (the live drift check sees it).
    - `problems`: `_file_problems` over the readable files. Warnings are not findings.
    `trace`: the `gate.check_traceability` result for this ledger and these sources when the caller already has it (the gate does), so each
    test is looked up once; else it is computed here."""
    from . import gate  # lazy: gate imports this module
    ids = rules_from["ids"] if rules_from["valid"] else []
    good, missing, fmt = {}, [], []
    for w in ids:
        f = files.get(w)
        if not isinstance(f, dict) or f.get("invalid"):
            missing.append(w)
        elif f.get("format") != FORMAT:
            fmt.append(w)
        else:
            good[w] = f
    header = (["rules-from"] if not (rules_from["present"] and rules_from["valid"]) else []) + (
        ["rules-reviewed"] if reviewed["present"] and not reviewed["valid"] else [])
    sc = scope(good, list(good), decisions_parsed, phase)
    rule = {r: v for f in good.values() for r, v in f["rules"].items()}
    tagged, ac_count = tags(ledger_md), len(dod.ac_lines(ledger_md))
    trace = trace or gate.check_traceability(gate.parse_section3(ledger_md), sources, ac_count)
    tested = {ac: ac not in trace["unmapped"] for ac in trace["mapped"]}  # an AC with a row that has no found test is in `unmapped`
    cited = set()
    for ac, ids_ in tagged["rows"].items():
        m = _AC_CANON.fullmatch(ac)
        if m and int(m.group(1)) <= ac_count and tested.get(ac):
            cited.update(ids_)
    left = set(missing) | set(fmt)
    seen = {r for ids_ in tagged["rows"].values() for r in ids_}
    unknown = sorted((r for r in seen if r not in rule and _key(r)[0] not in left), key=_key) + ["?"] * len(tagged["unknown"])
    recorded = reviewed["hashes"]
    judged = set(sc["in_scope"]) | {r for r in recorded if r in rule and rule[r]["status"] == "approved"}
    sound = {r for r in judged if rule[r].get("body_hash") == rule[r]["hash"]}  # a hash mismatch is never "free to confirm": `--confirm` refuses it
    free = not reviewed["present"] or reviewed["valid"]  # a malformed record cannot be extended by `--confirm` (a human repairs it): nothing is free then
    unconfirmed = sorted((r for r in sc["in_scope"] if free and r in sound and r not in recorded), key=_key)
    changed = sorted((r for r in judged if r not in unconfirmed and (recorded.get(r) != rule[r]["hash"] or r not in sound)), key=_key)
    return {"in_scope": sc["in_scope"], "covered": [r for r in sc["in_scope"] if r in cited], "excluded": sc["excluded"],
            "uncovered": [r for r in sc["in_scope"] if r not in cited], "unknown": unknown, "unconfirmed": unconfirmed, "changed": changed,
            "problems": [p for w in sorted(good) for p in _file_problems(w, good[w]["problems"])],
            "missing": sorted(missing), "format": sorted(fmt), "header": header}


# ---- live drift (P3-9): the committed rules files against the rules live in the issues; GETs only, ids and fixed words out ----

DRIFT_UNREACHABLE = "GitHub could not be read, or the gate's token cannot read the Epic/Feature — rule drift not checked"
DRIFT_OFFLINE_NOTE = "rule drift not checked (offline, or the live check was not run)"
DRIFT_OFFLINE = {"evaluated": False, "drift": [], "unreachable": False, "note": DRIFT_OFFLINE_NOTE}  # what `--offline` passes on; never mutated


RECONFIRM_UNREADABLE = "rules reconfirmed: target ledger unreadable"  # the target branch's ledger could not be read: `reconfirmed` is omitted, never []
RECONFIRM_TOO_LONG = "rules reconfirmed: history too long to compare"  # more ledger versions in base..head than strict.MAX_HISTORY
RECONFIRM_FIRST_PR = ("rules reconfirmed: target branch has no ledger for this story; first PR: reconfirmation cannot be compared")  # [] would look like a pass
RECONFIRM_BRANCH_UNREADABLE = "rules reconfirmed: a ledger version in the branch history is unreadable"
RECONFIRM_NOTES = (RECONFIRM_UNREADABLE, RECONFIRM_BRANCH_UNREADABLE, RECONFIRM_TOO_LONG, RECONFIRM_FIRST_PR)
RECONFIRM_NOTE = "differs from the earlier recorded hash: check the ledger history"  # a note until piece 4's approver-comment path can clear it (then a block)


def reconfirmed(old, new):
    """`[{"id", "old", "new"}]` for every id of `old` (`{id: hash8}`, the target branch's `Rules reviewed:`) that `new` (the head's) does not record
    (`new` None) or records with another hash, sorted by (issue, number). Ids new at the head are not listed. Ids and 8-hex hashes only."""
    return [{"id": r, "old": h, "new": new.get(r)}
            for r, h in sorted(old.items(), key=lambda kv: tuple(int(x) for x in kv[0][3:].split("-"))) if new.get(r) != h]


def reconfirmed_text(items, cap=20):
    """`BR-1201-1 (3f2a9c1b -> none), ...` for `reconfirmed` items, at most `cap` of them then `and N more`."""
    shown = ", ".join(f"{i['id']} ({i['old']} -> {i['new'] or 'none'})" for i in items[:cap])
    return shown + (f" and {len(items) - cap} more" if len(items) > cap else "")
_STRUCTURE = (GhError, AttributeError, TypeError, KeyError, ValueError)  # GitHub unreachable, or an answer that is not an issue (NotAnIssue is a ValueError)


def _error_lines(lines):
    return [x for x in lines if not x.startswith("- warning ")]  # a warning is not a finding, so editing one is not drift


def drift(gh, ref, cfg, ledger_md, files, decisions_parsed):
    """`{"evaluated", "drift", "unreachable", "note"}`: what differs between the committed files and the rules LIVE in the issues. Every GET is made
    here by `rules.walk` from `ref` only (the issue given on the command line; the ledger and the files never name an id to fetch), and a
    caller in `__main__` passes the result to the pure `run_gate`. `drift` lists, sorted by (issue, number), the rule ids in the LIVE scope
    (approved in the issue now, minus the counted exclusions) whose file section is missing or differs line for line from `render_rule(live)` (a matching section pins the hash and the body, so `body_hash` is not consulted); then `"chain"` when the live walk differs from `Rules from:`; then `"problems"` when the live error
    problems differ from the file's `## Problems` lines. Draft, retired and excluded rules are ignored; a file with another rules-format is
    left to coverage (`format`); a file that is absent, invalid or without a readable rules-format (the `Last pulled` line deleted or mangled) counts as empty. Lines are compared as `splitlines()`, so CRLF is no
    drift. Any GitHub or token error (401/403/404, network) or an answer that is not an issue: `unreachable`, nothing is partial."""
    try:
        found = walk(gh, ref, cfg.rules["types"])
    except _STRUCTURE:
        return {"evaluated": False, "drift": [], "unreachable": True, "note": DRIFT_UNREACHABLE}
    live_from = [a["id"] for a in found]
    live_rule = {r["id"]: r for a in found for r in a["parsed"]["rules"]}
    fake = {a["id"]: {"invalid": False, "rules": {r["id"]: {"status": r["status"], "hash": r["hash"], "body_hash": r["hash"]} for r in a["parsed"]["rules"]}}
            for a in found}
    in_scope = scope(fake, live_from, decisions_parsed, "pr")["in_scope"]  # the live rules, not the file's
    ids, problems = set(), False
    for a in found:
        f = files.get(a["id"])
        ok = isinstance(f, dict) and not f.get("invalid") and f.get("format") is not None  # no readable format (`Last pulled` deleted or mangled): an empty file
        if ok and f["format"] != FORMAT:
            continue
        have, lines = (f["rules"], f["problems"]) if ok else ({}, [])
        for rid in in_scope:
            if _key(rid)[0] != a["id"]:
                continue
            fr, lr = have.get(rid), live_rule[rid]
            try:
                want = render_rule(lr).splitlines()
            except ValueError:
                want = None
            if fr is None or want is None or fr["section"].splitlines() != want:
                ids.add(rid)
        try:
            problems = problems or _error_lines(_problem_lines(a["parsed"], a["id"])) != _error_lines(lines)
        except ValueError:
            problems = True
    out = sorted(ids, key=_key) + (["chain"] if live_from != dod.ledger_rules_from(ledger_md)["ids"] else []) + (["problems"] if problems else [])
    return {"evaluated": True, "drift": out, "unreachable": False, "note": None}


# ---- `ghp rules <n> --confirm` (P3-7): the rule hashes a human reviewed go into the ledger header; local only, no network ----

CONFIRM_NO_LEDGER = "there is no ledger for this issue; run `ghp dod <issue number>` first"
CONFIRM_LEDGER = "the ledger is not a readable regular UTF-8 file inside dod_path (a symlink, a hard link, over 1 MiB, not UTF-8); nothing was changed"
CONFIRM_HEADER = "the ledger header cannot be trusted or has a malformed `Rules from:` or `Rules reviewed:` line; fix the header by hand, nothing was changed"
CONFIRM_NO_FROM = "the ledger has no `Rules from:` line; run `ghp dod <issue number> --refresh` with rules enabled first, nothing was changed"
CONFIRM_FILES = "a rules file named in `Rules from:` is missing, unreadable, invalid or in another rules-format; run `ghp rules <issue number>` first, nothing was changed"
CONFIRM_DECISIONS = "the decisions file exists but is not a readable regular file; nothing was changed"
CONFIRM_HASH = ("a rule in scope does not match its heading hash, so the file text is not what was reviewed ({ids}); "
                "run `ghp rules <issue number>` to copy it again, nothing was changed")
CONFIRM_TERMINAL = ("recorded without a terminal (new rules): {recorded}. Needs a human: re-confirming a rule whose recorded hash changed, or dropping a recorded one, "
                    "records that a human re-read it, so the human runs `ghp rules <issue number> --confirm` in their own terminal after reading the change "
                    "(stdin and stdout must both be a terminal; a guard against accidents, not a security boundary). Changed since confirmed: {changed}. Dropped: {removed}")
CONFIRM_SWAPPED = "the ledger, a rules file or the decisions file changed while you were reading: nothing was approved"
CONFIRM_WRITE = "the ledger could not be written or read back identical; nothing was confirmed (check the file)"
CONFIRM_ENV = "GHP_CONFIG is set: Config.load would read that file, not ./ghp.config.json, so dod_path could differ from the committed one; unset it to confirm"
_REVIEWED = "- **Rules reviewed:** "


def _line_at(md, label):
    """Index of the header line that names `label`, else None (a valid header has at most one)."""
    look = dod._look(label)
    return next((i for i, x in enumerate(dod._header(md)[0]) if look.match(x)), None)


def _with_reviewed(md, value):
    """`md` with `- **Rules reviewed:** <value>`: that line rewritten in place, or a new one under `Rules from:`. The line written ends with the
    terminator of the file's FIRST line (the rest of the bytes stay as they are, a mixed file stays mixed). None when the line it sits on is not
    shown exactly as written (hidden text), or that line or the first one does not end in `\n` / `\r\n`."""
    lines, shown = md.splitlines(keepends=True), dod._header(md)[0]
    at = _line_at(md, "Rules reviewed")
    src = at if at is not None else _line_at(md, "Rules from")
    if src is None or src >= len(lines):
        return None
    body = lines[src].splitlines()[0]
    eol = lines[0][len(lines[0].splitlines()[0]):]
    if body != shown[src] or lines[src][len(body):] not in ("\n", "\r\n") or eol not in ("\n", "\r\n"):
        return None
    lines[src:src + 1] = [lines[src], _REVIEWED + value + eol] if at is None else [_REVIEWED + value + eol]
    return "".join(lines)


def prepare_confirm(dod_path, story_id):
    """Read everything `ghp rules <n> --confirm` needs and write nothing: `{"reviewed", "added", "changed", "removed", "needs_human", "code",
    "write"}`. The `pr` scope (P3-6) is computed from the committed rules files named by the ledger's `Rules from:` and the story's decisions
    file; the hashes to record are the HEADING hashes of the rules in scope, which must equal the hash of the text in the file. `reviewed` is
    the new value of `Rules reviewed:` (sorted by issue, then number), `added` / `changed` / `removed` the ids that differ from what the ledger
    records now. `needs_human` is true when an already-recorded id would get another hash or leave the record (a human's act). `code` is the
    `stack.approval_code` over the story, the ledger as read and the new value. `write_free()` records only the new ids (every recorded id left as it is) with the same guarantees; `write()` replaces
    the ledger atomically (tmp file in the same directory, the bytes read now are required, read back; the file's own line terminator is kept) and returns whether it wrote. ValueError with a fixed
    text (ids and hashes only, no file text) for every refusal: nothing is written."""
    path = dod.dod_file(dod_path, story_id)
    if not os.path.lexists(path):
        raise ValueError(CONFIRM_NO_LEDGER)
    md = read_file(path, ".", dod_path)
    if md is None:
        raise ValueError(CONFIRM_LEDGER)
    dod._section2_one(md)  # LedgerError (exit 2): a ledger the readers refuse is not stamped as reviewed
    frm, rev = dod.ledger_rules_from(md), dod.ledger_rules_reviewed(md)
    if not (dod._header(md)[1] and frm["valid"] and rev["valid"]):
        raise ValueError(CONFIRM_HEADER)
    if not frm["present"]:
        raise ValueError(CONFIRM_NO_FROM)
    files = {}
    for wid in frm["ids"]:
        text = read_file(rules_file(dod_path, wid), ".", dod_path)
        f = parse_file(text, wid) if text is not None else None
        if f is None or f["invalid"] or f["format"] != FORMAT:
            raise ValueError(CONFIRM_FILES)
        files[wid] = f
    dpath = decisions.decisions_file(dod_path, story_id)
    dmd = read_file(dpath, ".", dod_path) if os.path.lexists(dpath) else ""
    if dmd is None:
        raise ValueError(CONFIRM_DECISIONS)
    in_scope = scope(files, frm["ids"], decisions.parse_decisions(dmd), "pr")["in_scope"]
    rule = {r: files[_key(r)[0]]["rules"][r] for r in in_scope}
    if bad := [r for r, v in rule.items() if v["body_hash"] != v["hash"]]:
        raise ValueError(CONFIRM_HASH.format(ids=", ".join(bad)))
    new, old = {r: v["hash"] for r, v in rule.items()}, rev["hashes"]
    added = [r for r in new if r not in old]
    changed = [r for r in new if r in old and old[r] != new[r]]
    removed = sorted((r for r in old if r not in new), key=_key)

    def plan(hashes, noop):  # (the value, write): `hashes` is the whole record to leave in the ledger
        value = ", ".join(f"{r}@{h}" for r, h in sorted(hashes.items(), key=lambda kv: _key(kv[0]))) or "none"
        text = md if noop else _with_reviewed(md, value)
        if text is None:
            raise ValueError(CONFIRM_HEADER)

        def check(raw):  # what is about to replace the ledger: header trusted, the same Rules from, exactly this record, nothing else moved
            back = raw.decode("utf-8")
            if not (dod._header(back)[1] and dod.ledger_rules_from(back) == frm and dod.ledger_rules_reviewed(back) == {"present": True, "valid": True, "hashes": hashes}
                    and _without(back) == _without(md)):
                raise ValueError(CONFIRM_WRITE)

        def write():
            if noop:
                return False
            try:
                stack._atomic_replace(path, text, check, md.encode("utf-8"))
            except ValueError as e:
                raise ValueError(CONFIRM_SWAPPED if str(e) == stack._E_CHANGED else CONFIRM_WRITE) from None
            return True
        return value, write

    value, write = plan(new, rev["present"] and old == new)
    _, write_free = plan({**old, **{r: new[r] for r in added}}, not added)  # the part no terminal is needed for: new ids only, every recorded id untouched
    code = stack.approval_code({"story": story_id, "ledger_sha256": hashlib.sha256(md.encode("utf-8")).hexdigest(), "reviewed": value})
    return {"reviewed": value, "added": added, "changed": changed, "removed": removed, "needs_human": bool(changed or removed), "code": code,
            "old": old, "new": new, "write": write, "write_free": write_free}


def _without(md):
    """`md` minus its `Rules reviewed:` header line (as `_with_reviewed` places it): what a confirm must leave byte for byte."""
    at = _line_at(md, "Rules reviewed")
    lines = md.splitlines(keepends=True)
    return "".join(lines[:at] + lines[at + 1:]) if at is not None else md


def confirm_refusal(prep, cap=20):
    """The fixed exit-2 text of a confirm without a terminal that still has changed or dropped ids: which ids were recorded (the free new ones,
    already written) and which need a human's own terminal. Rule ids only (each is a validated `BR-<id>-<n>`), at most `cap` per list."""
    def ids(v):
        return (", ".join(v[:cap]) + (f" and {len(v) - cap} more" if len(v) > cap else "")) or "none"
    return CONFIRM_TERMINAL.format(recorded=ids(prep["added"]), changed=ids(prep["changed"]), removed=ids(prep["removed"]))


def confirm_preview(story_id, prep):
    """What a human reads before typing the code: ids and hashes only, never rule text."""
    out = [f"Confirming the business rules of issue {story_id}: the ledger will record that you reviewed them.", ""]
    out += [f"changed: {r}  {prep['old'][r]} -> {prep['new'][r]}" for r in prep["changed"]]
    out += [f"removed: {r}  (was {prep['old'][r]})" for r in prep["removed"]]
    out += [f"added: {r}  ({prep['new'][r]})" for r in prep["added"]]
    out += ["", "Read each changed rule in its Rules_<id>.md file first: a hash says nothing about the text.",
            "A terminal check stops accidents; it is not a security boundary, and the record is a statement, not proof."]
    return "\n".join(out)


# ---- the verb: `ghp rules <ref> [--confirm]` ----

def _confirm(ref, dod_path):
    """`ghp rules <n> --confirm`: records in the ledger header the rule hashes a human reviewed (local only: no token, no request). New ids
    are added by anyone, also when another id changed or was dropped (written first, in one atomic write, then exit 2 naming the two lists). An
    already-recorded id that would get another hash, or leave the record, is a human re-reading a changed rule: it
    needs a terminal on stdin AND stdout (a guard against accidents, not a security boundary: a pseudo-terminal gives a tty), prints the ids
    and old/new hashes, and writes only when the typed `approval code` matches; everything is read again and the code recomputed right
    before the write."""
    prep = prepare_confirm(dod_path, ref.number)
    if prep["needs_human"]:
        if not (sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty()):
            prep["write_free"]()  # the new ids are free: recorded in one atomic write before the refusal for the rest
            raise ValueError(confirm_refusal(prep))
        print(confirm_preview(ref.number, prep))
        if not stack.confirm_code(prep["code"]):
            raise ValueError(stack.E_NOT_CONFIRMED)
        again = prepare_confirm(dod_path, ref.number)
        if again["code"] != prep["code"]:
            raise ValueError(CONFIRM_SWAPPED)
        prep = again
    written = prep["write"]()
    return {"id": ref.number, "reviewed": prep["reviewed"], "added": prep["added"], "changed": prep["changed"], "removed": prep["removed"], "written": written}


def register(sub):
    p = sub.add_parser("rules", help="pull the business rules of the issue's Epic/Feature ancestors into <dod_path>/Rules_<n>.md "
                       "(reads GitHub with GET only); --confirm records the reviewed rule hashes in the ledger (local only)")
    p.add_argument("ref", help="5, #5, owner/repo#5 or an issue URL")
    p.add_argument("--confirm", action="store_true", help="local only: record in the ledger header the hashes of the rules in this issue's scope "
                   "(`Rules reviewed:`). New ids need no terminal; re-confirming a changed hash or dropping a recorded id needs your own terminal "
                   "and a typed code")


def run(args, gh):
    if args.confirm and os.environ.get("GHP_CONFIG"):  # before Config.load: that would read the other file
        raise ValueError(CONFIRM_ENV)
    ref = issues._resolve(args.ref)  # config, then the ref: a bad one is refused before any call
    cfg = Config.load()
    dod_path = dod.safe_dir(cfg.dod_path)
    out = _confirm(ref, dod_path) if args.confirm else pull(gh, cfg, ref)
    print(json.dumps(out, indent=2))
    return 0
