"""The DoD ledger `dod/DoD_<n>.md` (n = the issue number), ported from ado's `dod` with a narrow rewrite (T11).

Source of the acceptance criteria: the lines under the issue body's `## Acceptance criteria` heading, up to the next ATX heading of level 1 or 2 or the end of the body.
- The heading is the exact text `## Acceptance criteria` at the start of a line, trailing blanks allowed, case-sensitive. `### ...`, `# ...`, setext
  underlines, an indented or `##`-closed heading and other spellings are not the heading.
- A heading inside a fenced code block or inside an HTML comment is not a heading (it is code or hidden text), and a `## ` line there does not end the section.
- No heading: `AcError` E_AC_MISSING. Two headings: E_AC_DUPLICATE. Both are ValueErrors (exit 2) raised before anything is written; the messages never
  quote the issue text. A heading with nothing under it is not missing: the ledger holds a placeholder and the thin-AC banner.
- The issue body is untrusted. The lines are copied byte for byte (terminators kept as `\\n` or `\\r\\n`) except: a line break only Python sees
  (U+2028/2029, \\x0b, \\x0c, \\x1c-\\x1e, \\x85) becomes a space, a lone `\\r` becomes `\\n`, leading and trailing blank lines of the section are dropped, and a line
  that could open a fence, forge a `## ` heading or open a comment gets the reversible escape below (`ac_lines` undoes it). The text outside the section is
  section 1's Description, escaped the same way, with control characters removed.
- The section ends at the next visible ATX heading of level 1 or 2 (0-3 leading spaces, `#` or `##`, then a space, a tab or the end of the line), not
  one inside a fence or a comment. Known limit (X8): a setext heading (text over `---` or `===`) is not recognised and stays in the AC.
- Which lines are criteria (AC-n, `ac_stats`, the thin count; the ledger copy keeps every line): blank lines, the thin-AC banner `render_dod` writes (only as
  the first line of the block), the `_(none ...)_` placeholder, lines inside an HTML comment (also its delimiter lines, a comment cut short by the end of
  the section, and the comment part of a mixed line) and the delimiter lines of a fenced code block are not. A `>` quote IS a criterion. The lines INSIDE a
  fenced code block are criteria (they are visible text; a comment opener inside a fence is code, not a comment). Known limit: a fence nested in a list
  item (`- ```), or indented four spaces, is not seen as a fence. The hash covers the AC text as copied, hidden lines and blank lines included, so a
  whitespace-only edit reports drift (fails safe; ado's hash was over converted text).
- The banner cannot be forged: an AC line that starts like it (`> **Thin acceptance criteria**`, after any backslashes) is written with one more `\\`, so
  the banner `render_dod` writes (only for a thin AC) is the one line `ac_lines` skips, and `ac_stats` and `ac_lines` always agree.
- Known limit (X8, UNVERIFIED on GitHub): a comment directly followed by a heading on the same line (`<!-- c -->## Acceptance criteria`, `<!-- c -->## Notes`)
  counts as that heading in the heading scan and the section end (the scan blanks the comment); GitHub renders such a line as an HTML block.
- The file name is built from the issue number only; `safe_dir` confines the directory to the repository.
"""
import hashlib
import json
import os
import re
from datetime import date
from pathlib import Path

from . import issues, stack
from .config import Config
from .stack import CAP_TEXT, _clean  # noqa: F401  (stack imports only config at module level: no cycle)


class AcError(ValueError):
    """The issue body has no usable `## Acceptance criteria` heading. Exit 2 in the CLI (a ValueError); `code` names it."""

    def __init__(self, code, msg):
        super().__init__("%s: %s" % (code, msg))
        self.code = code


class LedgerError(ValueError):
    """The ledger cannot be read safely (today: more than one visible SECTION2 heading). Exit 2 in the CLI (a ValueError); `code` names it.
    Callers treat it as a refusal, never as 'no section' or 'no criteria'."""

    def __init__(self, code, msg):
        super().__init__("%s: %s" % (code, msg))
        self.code = code


AC_HEADING = "## Acceptance criteria"
_PY_BREAKS = re.compile("[\x0b\x0c\x1c-\x1e\x85\u2028\u2029]")  # line breaks str.splitlines sees and GitHub does not
_CONTROL = re.compile("[\x00-\x08\x0e-\x1b\x1f\x7f-\x9f]")  # no terminal escape in a header value or the Description (the AC is verbatim)

# Escaping for issue text written into the ledger (§1), so it can never open a fence or comment
# that hides later sections, or forge a `## ` heading. One scheme, per line: a leading `\` before a
# line-start ``` / ~~~ / # (after indent and one list marker), and `<!--` -> `&lt;!--` anywhere.
# A third case, AC lines only: a line that starts like the thin-AC banner (`> **Thin acceptance criteria**`, optionally after backslashes) gets one more `\`,
# so the banner `render_dod` writes is the only line `ac_lines` takes for it.
# `_unescape_line` is its inverse for `ac_lines` (lossy only for issue text that itself starts a line with `\` + fence/# or contains `&lt;!--`); `ac_hash` hashes the raw AC text, never this output.
_CTRL = re.compile(r"^([ \t]*(?:(?:[-*+]|\d+[.)])[ \t]+)?)(?=```|~~~|#)", re.M)


def _esc(text):
    text = "\n".join(text.splitlines())  # str.splitlines (U+2028, \x0c, \r ...) is how the readers here see lines
    return _CTRL.sub(lambda m: m.group(1) + "\\", text).replace("<!--", "&lt;!--")


def _hv(value):
    """A value for a ledger header line: one line (any line break becomes a space), no control characters, escaped like the title, so it can never open a comment."""
    return _esc(_CONTROL.sub("", " ".join(str(value).splitlines())))


# Everything from this marker onward is authored content preserved across --refresh.
SECTION2 = "## 2. Brainstorm & plan"
_SECTION2_RE = re.compile(r"^" + re.escape(SECTION2), re.M)
_FENCE_OPEN = re.compile(r"^ {0,3}(?:(`{3,})[^`]*$|(~{3,}))")


def _scan(md, fill, keep_code=False):
    """([md's lines with what renders as code or not at all replaced by `fill` per character],
    whether a fence or comment is still open at the end). Blanked: fenced code (CommonMark: closed
    only by the same character, at least as long, nothing but whitespace after) and <!-- --> comments.
    `keep_code` (criteria): the lines inside a fence stay as they are and only the delimiter lines are blanked."""
    out, fence, comment = [], None, False
    for line in md.splitlines():
        if fence:
            closing = re.fullmatch(rf" {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*", line)
            if closing:
                fence = None
            out.append(line if keep_code and not closing else fill * len(line))
            continue
        m = None if comment else _FENCE_OPEN.match(line)
        if m:
            fence = m.group(1) or m.group(2)
            out.append(fill * len(line))
            continue
        kept, pos = [], 0  # offsets, not `rest = rest[n:]` slices: a line of many comments would copy its tail once per comment
        while pos < len(line):
            if comment:
                end = line.find("-->", pos)
                if end < 0:
                    kept.append(fill * (len(line) - pos))
                    break
                kept.append(fill * (end + 3 - pos))
                comment, pos = False, end + 3
            else:
                start = line.find("<!--", pos)
                if start < 0:
                    kept.append(line[pos:])
                    break
                kept += [line[pos:start], fill * 4]
                pos, comment = start + 4, True
        out.append("".join(kept))
    return out, bool(fence or comment)


def _visible(md):
    """(md with fences and comments removed; lines stay 1:1 with md.splitlines(), whether one is still open)."""
    out, still_open = _scan(md, "")
    return "\n".join(out), still_open


def _mask(md):
    """md with fences and comments overwritten by spaces: same length as md, so an offset found here
    slices the original. Every line break becomes `\\n` (CRLF is kept), as `re.M` and `_visible` see lines."""
    out, _ = _scan(md, " ")
    return "".join(k + ("\r\n" if raw.endswith("\r\n") else "\n" if raw[len(k):] else "")
                   for k, raw in zip(out, md.splitlines(keepends=True)))


def unclosed(md):
    """True when a fence or comment is left open: everything after it is hidden, so callers fail closed."""
    return _scan(md, "")[1]


def _section2_marks(md):
    """Offsets of every line-anchored SECTION2 heading outside fences and comments. Inline mentions never count."""
    return [m.start() for m in _SECTION2_RE.finditer(_mask(md))]


def _section2_at(md):
    """Offset of the LAST visible SECTION2 heading, or -1. Only the writer (`write_dod`) uses it, after its own refusal of more than one: the
    readers go through `_section2_one`."""
    marks = _section2_marks(md)
    return marks[-1] if marks else -1


E_AMBIGUOUS = "E_LEDGER_AMBIGUOUS_SECTION2"


def _section2_one(md):
    """Offset of the one visible SECTION2 heading, or -1 when there is none. LedgerError when there is more than one: a human `## 2.` line in a later
    section (or a heading copied into section 1 of an old ledger) makes it impossible to tell where the authored sections start, and the last one
    would hand a reader the wrong text (sections 3 and 4 as None). Fixed text: the ledger is never quoted."""
    marks = _section2_marks(md)
    if len(marks) > 1:
        raise LedgerError(E_AMBIGUOUS, f"the ledger has more than one '{SECTION2}' heading outside code and comments; it cannot tell which one starts "
                          "the authored sections, so nothing is read from it. Remove or escape the extra heading by hand (for example put a backslash before the ##)")
    return marks[0] if marks else -1


def _repair_at(md):
    """Offset to splice from when `md` has an unclosed opener, else -1. One rule, no search: the only splice
    point is the FIRST raw line-start SECTION2 heading (section 1 ends there in a real ledger), and only when the
    text before it is unclosed (the opener is in section 1: old ledgers, before the issue text was escaped) and the
    suffix from it is balanced and still has `## 3.`, `## 4.`, `## 5.` in order. Anything else refuses."""
    m = _SECTION2_RE.search(md)
    if m and unclosed(md[:m.start()]) and not unclosed(md[m.start():]):
        vis, at = _mask(md[m.start():]), 0
        for n in (3, 4, 5):  # the earliest `## 3.`, then the earliest `## 4.` after it, ...: one pass (a lazy `.*?` chain rescans the text once per `## 3.`)
            h = re.compile(rf"^## {n}\.", re.M).search(vis, at)
            if not h:
                return -1
            at = h.end()
        return m.start()
    return -1


def section_span(ledger_md, n):
    """(start, end) offsets of the body of `## <n>.` (up to the next `## ` heading or the end), searched only after the
    SECTION2 marker, so issue text copied into section 1 can never stand in for it. Headings inside fences or
    comments are not headings. None if absent. LedgerError (E_LEDGER_AMBIGUOUS_SECTION2) when there is more than one visible marker."""
    vis = _mask(ledger_md)
    i = _section2_one(ledger_md)
    m = re.search(rf"^## {n}\.", vis[i:], re.M) if i != -1 else None
    if not m:
        return None
    start = i + m.end()
    nxt = re.search(r"^## ", vis[start:], re.M)
    return start, (start + nxt.start() if nxt else len(ledger_md))


def section(ledger_md, n):
    """Body of `## <n>.` (see section_span; the returned text is the authored original). None if absent; LedgerError when the ledger is ambiguous."""
    span = section_span(ledger_md, n)
    return ledger_md[span[0]:span[1]] if span else None


# ---- the AC of an issue body ----
def _line_end(line):
    """(content, terminator) of one line kept with its terminator: `\\n` and `\\r\\n` stay, anything else that ended it becomes `\\n`."""
    content = (line.splitlines() or [""])[0]
    term = line[len(content):]
    return content, term if term in ("", "\n", "\r\n") else "\n"


_ATX_12 = re.compile(r"^ {0,3}#{1,2}(?:[ \t]|$)")  # an ATX heading of level 1 or 2


def _ac_section(body):
    """([(content, terminator)] of the AC section's lines, [contents of every other line]). AcError when the heading is missing or twice."""
    body = _PY_BREAKS.sub(" ", body)
    lines = body.splitlines(keepends=True)
    visible = _scan(body, "")[0]  # 1:1 with `lines`; a line in a fence or comment is empty here
    heads = [i for i, v in enumerate(visible) if v.rstrip(" \t") == AC_HEADING]
    if not heads:
        raise AcError("E_AC_MISSING", "the issue body has no '%s' heading: it must be exactly that text at the start of a line, with optional trailing spaces or tabs "
                      "(an indented heading, a heading closed with ##, another case, a heading inside a code fence or an HTML comment is not accepted)" % AC_HEADING)
    if len(heads) > 1:
        raise AcError("E_AC_DUPLICATE", "the issue body has the '%s' heading %d times; keep one" % (AC_HEADING, len(heads)))
    h = heads[0]
    end = next((i for i in range(h + 1, len(lines)) if _ATX_12.match(visible[i])), len(lines))
    sect = lines[h + 1:end]
    a, b = 0, len(sect)
    while a < b and not sect[a].strip():
        a += 1
    while b > a and not sect[b - 1].strip():
        b -= 1
    rest = [_line_end(l)[0] for l in lines[:h] + lines[end:]]
    return [_line_end(l) for l in sect[a:b]], rest


def _ac_contents(issue):
    return [c for c, _ in _ac_section(issue.body)[0]]


_LIST_MARKER = re.compile(r"^(?:[-*+]|\d+[.)])(?:\s+|$)")  # `$`: a bare marker line
_AC_NONE = re.compile(r"^_\(none[^)]*\)_$")  # the placeholder render_dod writes when the issue has no AC text
_BANNER = re.compile(r"> \*\*Thin acceptance criteria\*\* \(\d+ chars\)\. Splitting this into AC-1…AC-n is judgment, not transcription — .*first\.")
_UNESCAPE_LINE = re.compile(r"^([ \t]*(?:(?:[-*+]|\d+[.)])[ \t]+)?)\\(?=```|~~~|#)")  # the escape `_esc` adds, with the indent and list marker kept
_BANNER_START = re.compile(r"\\*> \*\*Thin acceptance criteria\*\*")


def _esc_ac(line):
    """`_esc` for one AC line, plus: a line that starts like the banner gets a `\\`, so no AC text can pass for the banner (a forged one would make a criterion vanish)."""
    line = _esc(line)
    return "\\" + line if _BANNER_START.match(line) else line


def _unescape_line(line):
    line = re.sub(r"^\\(?=\\*> \*\*Thin acceptance criteria\*\*)", "", line)
    return _UNESCAPE_LINE.sub(r"\1", line).replace("&lt;!--", "<!--")


def ac_lines(ledger_md):
    """The AC lines of section 1's `### Acceptance criteria` block; AC-n is the n-th (see the module docstring for what is a criterion).
    The thin-AC banner (the first line of the block), blank lines and the `_(none ...)_` placeholder are
    skipped; leading list markers are stripped. Read only before SECTION2, like `section`. LedgerError when the ledger has more than one visible SECTION2."""
    ledger_md = ledger_md.replace("\r\n", "\n").replace("\r", "\n")  # a CRLF checkout (autocrlf) must read the same: `$` stops at `\n`, not `\r`
    vis, i = _mask(ledger_md), _section2_one(ledger_md)
    m = re.search(r"^### Acceptance criteria[ \t]*$", vis[:i] if i != -1 else vis, re.M)
    if not m:
        return []
    end = i if i != -1 else len(vis)
    nxt = re.search(r"^##", vis[m.end():end], re.M)
    lines = ledger_md[m.end():m.end() + nxt.start() if nxt else end].splitlines()
    first = next((n for n, l in enumerate(lines) if l.strip()), None)
    if first is not None and _BANNER.fullmatch(lines[first]):
        del lines[first]
    return _ac_line_list([_unescape_line(l) for l in lines])


def _ac_line_list(lines):
    """The criteria among the AC text's lines (escape already undone): what is left after comments and fence delimiters are cut, minus blanks, the
    placeholder and list markers."""
    out = []
    for line in _scan("\n".join(lines), "", keep_code=True)[0]:
        line = line.strip()
        if line and not _AC_NONE.match(line):
            if line := _LIST_MARKER.sub("", line):
                out.append(line)
    return out


# Real stories on the board run 300+ chars of AC; below this it cannot be
# decomposed into AC-1…AC-n without inventing requirements. Advisory only.
AC_THIN_CHARS = 150


def _thin(n_chars):
    return n_chars < AC_THIN_CHARS


def ac_stats(issue):
    """AC length (the one rule: sum of `ac_lines` lengths, markers stripped) + whether it is too
    thin to decompose. Never fabricates. AcError when the heading is missing or twice."""
    n = sum(map(len, _ac_line_list(_ac_contents(issue))))
    return {"ac_chars": n, "ac_thin": _thin(n)}


SPEC_DIR_UNSET = "_(set by speckit.ghp.pull)_"


def ac_hash(issue):
    """sha256 of the AC lines joined by `\\n` (their text, not their line ends) — only real edits change it. AcError when the heading is missing or twice."""
    return hashlib.sha256("\n".join(_ac_contents(issue)).encode("utf-8")).hexdigest()


def _header_value(ledger_md, label):
    m = re.search(rf"^- \*\*{re.escape(label)}:\*\*([^\n]*)$", ledger_md, re.M)  # blanks trimmed below: `[ \t]*(.*?)[ \t]*$` is quadratic on a blank run
    return m.group(1).strip(" \t") if m else None


def ledger_ac_hash(ledger_md):
    return _header_value(ledger_md, "AC hash") or None


def ledger_spec_dir(ledger_md):
    v = _header_value(ledger_md, "Spec dir")
    return None if not v or v == SPEC_DIR_UNSET else v


_H1 = re.compile(r"^## ", re.M)


def _header(md):
    """(the header block as lines, whether it can be trusted). The block is what comes before the first line-start `## ` heading
    outside fences and comments; text hidden in them is gone. Untrusted when the first raw line-start `## ` is not that heading (a comment
    or fence hides it: the opener sits in the header, e.g. an old title that was not escaped, and issue text could stand in for header
    lines), or when there is no heading and a fence or comment is still open. The readers below call that invalid."""
    vis, still_open = _visible(md)
    m = _H1.search(vis)
    raw = next((i for i, line in enumerate(md.splitlines()) if line.startswith("## ")), None)
    shown = vis.count("\n", 0, m.start()) if m else None
    return (vis[:m.start()] if m else vis).splitlines(), raw == shown and (bool(m) or not still_open)


def header_block(md):
    """The text above the first `## ` heading outside fences and comments (fences and comments removed). The only place `Rules from:` and
    `Rules reviewed:` are read, so issue text in section 1 can never stand in for them."""
    return "".join(line + "\n" for line in _header(md)[0])


_RULES_ID = r"[1-9][0-9]{0,8}"
_RULES_FROM = re.compile(rf"none|{_RULES_ID}(?:, {_RULES_ID})*")
MAX_RULES_FROM = 3  # the walk follows at most 3 links


def _look(label):
    """A line that names `label` as a header line in any spelling (list marker, blockquote prefix, emphasis, case). Linear: the blank and `>` runs,
    the optional list marker and the emphasis run are separated by characters none of them can match, so no run can be split two ways (adjacent
    `\\s*` runs made this quadratic on a hostile line; the accepted set is pinned by `test_the_label_matcher_accepts_exactly_the_pinned_spellings`)."""
    return re.compile(r"[\s>]*(?:(?:[-*+]|[0-9]+[.)])\s*)?(?:[*_]+\s*)?" + label.replace(" ", r"\s+") + r"\b", re.I)


def _labelled(md, label):
    """(present, value) for the header line `- **<label>:** <value>`. Exact form only (one space, no trailing blanks). Value None = malformed:
    a second line, an unclosed opener in the header, or a list line that names the label in any other way. A title or `Spec dir` that
    mentions the label is not a line of that kind (it does not begin with the label)."""
    lines, trusted = _header(md)
    look = _look(label)
    hits = [x for x in lines if look.match(x)]
    prefix = f"- **{label}:** "
    if not trusted:
        return True, None
    if not hits:
        return False, ""
    value = hits[0][len(prefix):]
    return True, value if len(hits) == 1 and hits[0].startswith(prefix) and value and value == value.strip() else None


def _parse_from(value):
    if not _RULES_FROM.fullmatch(value or ""):
        return None
    ids = [] if value == "none" else [int(t) for t in value.split(", ")]
    return ids if len(ids) <= MAX_RULES_FROM and len(set(ids)) == len(ids) else None


def _parse_reviewed(value):
    """{rule id: hash8} for `BR-1201-1@3f2a9c1b, ...` or `none`; None when malformed or an id repeats."""
    from .rules import TAG_RE  # rules imports this module at load time: import lazily
    if value == "none":
        return {}
    out = {}
    for tok in (value or "x").split(", "):
        rid, at, h = tok.partition("@")
        if not (at and TAG_RE.fullmatch(rid) and re.fullmatch(r"[0-9a-f]{8}", h)) or rid in out:
            return None
        out[rid] = h
    return out


def ledger_rules_from(md):
    """`{"present", "valid", "ids"}` for `- **Rules from:** 1201, 1200` (nearest first; issue numbers) or `none`, read from the header block only. Invalid
    (and `ids == []`): more than 3 ids, a repeated id, an id that is not 1 to 9 digits without a leading zero, a second line, any other spelling
    of the line. Absent is `present False, valid True`."""
    present, value = _labelled(md, "Rules from")
    ids = _parse_from(value) if present else []
    return {"present": present, "valid": ids is not None, "ids": ids or []}


def ledger_rules_reviewed(md):
    """`{"present", "valid", "hashes"}` for `- **Rules reviewed:** BR-1201-1@3f2a9c1b, ...` (or `none`), read from the header block only. The same
    rules as `ledger_rules_from`; ids match `rules.TAG_RE`, hashes are 8 lowercase hex, an id may appear once."""
    present, value = _labelled(md, "Rules reviewed")
    hashes = _parse_reviewed(value) if present else {}
    return {"present": present, "valid": hashes is not None, "hashes": hashes or {}}


MAX_EXTRA_DOD = 20


def stack_dod_items(profile):
    """`(stack id, item, source)` for every DoD item of an approved profile, in stack order (`stack.load_profile` returns only approved ones)."""
    return [(st.get("id"), i.get("item"), i.get("source")) for st in (profile or {}).get("stacks", []) for i in st.get("dod", [])]


def _extra_dod_lines(extra_dod):
    """Section 5 lines for stack DoD items: cleaned (control, separator and invisible characters, `<!--`, `-->`, fences), escaped, at most
    MAX_EXTRA_DOD; more than that adds one fixed line instead of dropping items silently."""
    extra = list(extra_dod)
    lines = [f"- [ ] {_esc(_clean(item))} (stack {_clean(sid, 40)}, {_clean(src, 12)})" for sid, item, src in extra[:MAX_EXTRA_DOD]]
    if len(extra) > MAX_EXTRA_DOD:
        lines.append(f"- [ ] {len(extra) - MAX_EXTRA_DOD} more stack DoD items were not seeded: run `ghp stack show --md`")
    return lines


def render_dod(issue, synced="", spec_dir="", extra_dod=(), rules_from=None, rules_reviewed=""):
    """`issue`: an `issues.Issue`. AcError when its body has no usable AC heading. `rules_from`: None writes no `Rules from:` line (the baseline); a
    list of issue numbers writes it (`none` when empty). `rules_reviewed`: the value of a `Rules reviewed:` line, written when not empty. Both are
    checked with the readers' own rules before they reach the header."""
    if "\n" in spec_dir or "\r" in spec_dir:
        raise ValueError("spec dir must be a single line (it is written into the ledger header)")
    spec_dir = _hv(spec_dir)
    header = []
    if rules_from is not None:
        if not isinstance(rules_from, (list, tuple)) or any(type(i) is not int for i in rules_from):
            raise ValueError("rules_from must be a list of issue numbers")
        value = ", ".join(map(str, rules_from)) or "none"
        if _parse_from(value) is None:
            raise ValueError("rules_from must hold at most 3 different issue numbers of 1 to 9 digits")
        header.append(f"- **Rules from:** {value}")
    if rules_reviewed:
        if _parse_reviewed(rules_reviewed) is None:
            raise ValueError("rules_reviewed must be BR-<n>-<m>@<8 hex> entries separated by ', ', each rule once")
        header.append(f"- **Rules reviewed:** {rules_reviewed}")
    n = issue.ref.number
    ac, rest = _ac_section(issue.body)
    n_ac = sum(map(len, _ac_line_list([c for c, _ in ac])))  # counted before escaping: human text only
    ac_text = "".join(_esc_ac(c) + (t if i < len(ac) - 1 else "") for i, (c, t) in enumerate(ac))  # terminators kept as they were, none after the last line
    ac_block = ac_text or "_(none in the issue — improve it)_"
    desc = _esc(_CONTROL.sub("", "\n".join(rest).strip()))
    if _thin(n_ac):
        ac_block = (
            f"> **Thin acceptance criteria** ({n_ac} chars). Splitting this into "
            "AC-1…AC-n is judgment, not transcription — record the assumptions and open "
            "questions it forces in section 2, and consider improving the issue "
            "first.\n\n" + ac_block
        )
    parts = [
        f"# DoD — #{n}: {_hv(issue.title)}",  # a line break in the title would let `\r```` start a fence line
        "",
        f"- **Issue:** {_hv(issue.ref)}",
        f"- **URL:** {_hv(issue.url)}",
        f"- **State:** {_hv(issue.state)}",
        f"- **Last synced:** {_hv(synced)}",
        f"- **AC hash:** {ac_hash(issue)}",
        f"- **Spec dir:** {spec_dir or SPEC_DIR_UNSET}",
        *header,
        "",
        f"## 1. Seeded from the issue (verbatim Markdown — do not edit; refresh via `ghp dod {n} --refresh`)",
        "",
        "### Acceptance criteria",
        ac_block,
        "",
        "### Description",
        desc or "_(none)_",
        "",
        SECTION2 + "  (fill before building — per discipline)",
        "- **Approach / chosen option + why:** _(fill in)_",
        "- **Assumptions:** _(fill in)_ "
        f"Each is also recorded in Decisions_{n}.md (`ghp decisions {n} --add`).",
        "- **Open questions → answers:** _(fill in as they arise)_",
        "  Format, one line each: `- [ ] Q1: <question> → _(open)_ · owner: PO`; answered: `- [x] Q1: <question> → <answer> · owner: PO`",
        "- **Use cases & edge cases:** _(fill in — each: covered-by test / N/A + why)_",
        "",
        "## 3. AC → tests (traceability)  (tag each AC as AC-1, AC-2…)",
        "",
        "Tests are free-form IDs: `Class.method`, `path::test_name` or `describe > it`.",
        "",
        "| AC | acceptance criterion | tests | commit/PR | status |",
        "|---|---|---|---|---|",
        "| AC-1 | _(what it must do)_ | _(test IDs, …)_ | _(commit / PR)_ | _(red→green)_ |",
        "",
        "## 4. Gate & review log  (appended by speckit.ghp.gate and speckit.ghp.review)",
        "_(none yet)_",
        "",
        "## 5. Definition of Done  (all ✅ to close)",
        "- [ ] every AC mapped to a passing real test",
        "- [ ] coverage meets the project's bar",
        "- [ ] no assertion-free tests",
        "- [ ] all assumptions / questions / edge cases resolved",
        "- [ ] gates green",
        *_extra_dod_lines(extra_dod),
        "",
    ]
    return "\n".join(parts)


class DodExists(ValueError):
    """Ledger already exists and neither --force nor --refresh was given (or a refresh/force is refused). Exit 2 in the CLI (a ValueError)."""


def dod_file(dod_dir, number):
    return Path(dod_dir) / f"DoD_{number}.md"


def _reviewed_line(md):
    """`(present, value)` of the `Rules reviewed:` header line; value None when it cannot be trusted (an untrusted header that names the label)."""
    if _header(md)[1]:
        return _labelled(md, "Rules reviewed")
    return any(_look("Rules reviewed").match(x) for x in md.splitlines()), None


def write_dod(issue, dod_dir, synced="", force=False, refresh=False, spec_dir="", extra_dod=(), rules_from=None, rules_reviewed=""):
    ac_hash(issue)  # AcError first: a body without a usable heading writes nothing, whatever the flags and the file on disk
    path = dod_file(dod_dir, issue.ref.number)
    if path.exists() and not force and not refresh:
        raise DodExists(f"{path} exists — pass --refresh to re-pull section 1, or --force to overwrite it "
                        "(--force refuses while it has a 'Rules reviewed:' line; remove the line by hand first, knowing the removal is traced).")
    old = path.read_text(encoding="utf-8") if refresh and path.exists() else None
    if force and not refresh and path.exists():  # --force would drop the hashes a human confirmed: refuse (a line of any form, even a malformed one)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if _reviewed_line(text)[0]:
            raise DodExists(f"{path} has a 'Rules reviewed:' line — refusing to --force (it would drop the hashes a human confirmed). "
                            "Review the rules and run `ghp rules <n> --confirm` in your own terminal, or remove the line by hand "
                            "(the removal shows in the git history and is compared by the gate and `strict check` with --base). "
                            "The branch history belongs to the PR author: a squash or rebase before pushing erases this trace.")
    if old is not None:
        spec_dir = spec_dir or ledger_spec_dir(old) or ""
        # written by `ghp rules --confirm`: kept like Spec dir, even when rules are off. A header that cannot be trusted (an old ledger's
        # unescaped `<!--` in its title hid the first heading) cannot be read: with no line that names the label it is rebuilt as before,
        # so teams not using rules see no change; with one (maybe forged from the Description) the refresh refuses.
        present, kept = _reviewed_line(old)
        if present and _parse_reviewed(kept) is None:
            raise DodExists(f"{path} has a malformed or untrusted 'Rules reviewed:' header line — refusing to --refresh "
                            "(it would drop the hashes a human confirmed, or copy a forged line). Fix the header by hand.")
        rules_reviewed = rules_reviewed or kept or ""
    # stack DoD items seed a fresh ledger only: a refresh keeps everything after section 1, section 5 included
    fresh = render_dod(issue, synced, spec_dir, () if old is not None else extra_dod, rules_from, rules_reviewed)
    if old is not None:
        idx = _repair_at(old) if unclosed(old) else _section2_at(old)
        if not unclosed(old) and len(_section2_marks(old)) > 1:  # which one is real? splicing at the last would drop everything above it
            raise DodExists(
                f"{path} has more than one '{SECTION2}' heading outside code and comments ({E_AMBIGUOUS}) — refusing to --refresh "
                "(it cannot tell which one starts your authored sections, and splicing at the wrong one would drop content). Remove or escape the extra "
                "heading by hand (for example put a backslash before the ##), or use --force to fully overwrite a ledger without a 'Rules reviewed:' line."
            )
        if unclosed(old) and idx == -1:
            raise DodExists(
                f"{path} has an unclosed code fence or comment outside section 1 — refusing to --refresh "
                "(it would splice from the wrong place). Close it by hand, or use --force to fully overwrite a ledger without a 'Rules reviewed:' line "
                "(with one, remove the line by hand first, knowing the removal is traced)."
            )
        if idx == -1:
            raise DodExists(
                f"{path} is missing the '{SECTION2}' marker — refusing to --refresh "
                "(would discard authored content). Fix the file by hand, or use --force to fully overwrite a ledger without a 'Rules reviewed:' line "
                "(with one, remove the line by hand first, knowing the removal is traced)."
            )
        # keep freshly-rendered header+section1, splice old section2..end
        head = fresh[:_section2_at(fresh)]
        fresh = head + old[idx:]
    path.parent.mkdir(parents=True, exist_ok=True)
    # write-then-rename so a crash or disk-full mid-write never truncates authored sections; newline="" keeps the AC's CRLF bytes as they are
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(fresh)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


# ---- the verb ----
def safe_dir(dod_dir):
    """`dod_dir` (the config's `dod_path` or `--dir`, both chosen by whoever controls the repo) as a relative directory that stays inside the
    current directory, symlinks resolved; ValueError otherwise (the message never repeats the path)."""
    p = Path(dod_dir)
    root = os.path.realpath(".")
    real = os.path.realpath(os.path.join(root, dod_dir))
    if p.is_absolute() or ".." in p.parts or os.path.commonpath([root, real]) != root:
        raise ValueError("E_DOD_DIR: the ledger directory (dod_path or --dir) must be a relative directory inside the repository")
    return dod_dir


def register(sub):
    p = sub.add_parser("dod", help="seed or refresh the DoD ledger dod/DoD_<n>.md from an issue (local file only)")
    p.add_argument("ref", help="5, #5, owner/repo#5 or an issue URL")
    p.add_argument("--force", action="store_true", help="overwrite an existing ledger")
    p.add_argument("--refresh", action="store_true", help="re-pull section 1, preserve authored sections")
    p.add_argument("--dir", help="output dir inside the repository (default: config dod_path)")
    p.add_argument("--spec-dir", help="Spec Kit feature dir recorded in the ledger (kept on --refresh)")


def run(args, gh):
    ref = issues._resolve(args.ref)  # config, then the ref: a bad one is refused before any call
    cfg = Config.load()
    dod_dir = safe_dir(args.dir or cfg.dod_path)
    issue = issues.get(gh, ref)  # NotAnIssue is a ValueError: exit 2
    existed = dod_file(dod_dir, ref.number).exists()
    from . import rules  # lazy: rules imports this module at load time
    # the walk (more GETs) only when rules are on; an ancestor GET error propagates before anything is written
    rules_from = [a["id"] for a in rules.walk(gh, ref, cfg.rules["types"], story=issue)] if cfg.rules["enabled"] else None
    path = write_dod(issue, dod_dir, synced=date.today().isoformat(), force=args.force, refresh=args.refresh, spec_dir=args.spec_dir or "",
                     extra_dod=stack_dod_items(stack.load_profile()),  # approved profile only; write_dod ignores it on --refresh
                     rules_from=rules_from)
    action = "refreshed" if (args.refresh and existed) else ("overwritten" if existed else "created")
    print(json.dumps({"ref": str(ref), "path": str(path), "action": action, **ac_stats(issue)}, indent=2))
    return 0
