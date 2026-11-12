"""`ghp decisions N`: the assumptions and decisions of an issue, one `D<n>` line each in `<dod_path>/Decisions_<n>.md` (a local file); `--add` records one,
`--resolve` records a human's verdict, `--post` sends the unposted open lines as ONE marked issue comment (preview, human gate, `--apply`).

Ported from ado's `decisions` (T16; the first 12 names were the T10/T12 seam). What changed for GitHub:
- The comment is Markdown, lines joined by `\\n`: the first line is `[spec-kit-ghp:decisions] <16 hex of the sha256 of the rest>`, then the heading, then one
  `D<n> (<kind>): <text> - why: <why>` line per unposted decision. It goes through `comments.add`, so it is the only write and `Gh` never repeats it. A comment is
  never edited. Only the NEWEST comment of the token's own numeric user id that starts with the marker counts: when its whole text is the planned text and it was
  never edited, the post is `adopt` (no send: the lines are marked with that comment's id), so a run that posted but could not write the file does not post twice.
- A write that may have been applied (timeout, no HTTP response, a reply without an id, any 5xx) is never repeated: the comments are read again, the comment is
  looked for by its exact text, and only a definite 4xx skips that read. No second attempt is made; the error says so. A body over `comments.MAX_COMMENT` is refused
  in the preview.
- Text from the decisions file is untrusted: control, bidi and invisible characters become `\\uXXXX`, `@name` (an email address too) becomes `[mention removed]`
  (`comments.NO_MENTION`), `& < >` are escaped. Everything printed goes through `comments._show`.
- `--sync` is a no-op with a fixed note while `approval` is `terminal` on the target branch; in comment mode (T28) it is ado's `sync` / `apply_sync`.
T28 ported `sync`, `apply_sync`, `posting_index`, `superseded`, `_posting_rule`, `_posting_key` over GitHub comments (a posting is a plain kit comment,
`approvals.kit_lines`, never edited) and the approvers guard of `--resolve`. `strict override` (the verb that calls `add_override` and `apply_override`) is in `strict.py` (T23).
"""
import datetime
import errno
import hashlib
import html
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

from . import approvals, comments, gate, issues, plan as write_gate
from .config import Config
from .dod import _visible, safe_dir
from .gh import GhError, WriteNotArmed
from .questions import _me, _same

KINDS = ("decision", "assumption", "override")
_OPEN = "_(open)_"
# `- [ ] D1: <text> → _(open)_ · kind: <k> · why: <reason>`; `- [x] D1: <text> → validated|rejected · by: <role> · note: <text>`
# (either also takes ` · posted: <comment id>:<hash8>` and ` · used: <date>`; a resolved one ` · via: <comment id>`, the approver's comment `--sync` took the verdict from); lenient: spacing, `->`, `*`/`+` bullet.
_CAND = re.compile(r"^\s*(?:[-*+]\s+)?\[([ xX])\]\s*D(\d+)(.*)$")
# Linear on any input (T16; ado's `\s*` before the first token, and `(.*?)\s*$`, were quadratic over a run of blanks: 4 s to 35 s on 30,000): no pattern starts with
# `\s*` where it is searched, each unbounded `\s*` follows a literal that anchors the scan, and the text is trimmed with `str.strip()` (the same characters as `\s`)
# by the caller. The pieces left at the edges of a match are stripped by `_line`, so the results are the ones ado's patterns gave (tests/test_decisions.py compares).
_COLON = re.compile(r"\s*:(.*)$")
_STATUS = re.compile(r"(?:→|->)\s*(_\(open\)_|validated|rejected)(?=\s*(?:·|$))")
_KEYS = ("kind", "why", "by", "note", "posted", "used", "via")
_SEP = re.compile(r"·\s*(?=(?:" + "|".join(_KEYS) + r")\s*:)")
MAX_LINE = 2000  # a longer D line is malformed (and the grammar's longest legal line is far shorter)
_POSTED = re.compile(r"(\d+):([0-9a-f]{8})")
_VIA = re.compile(r"[1-9][0-9]{0,11}")  # a comment id: ASCII digits only, no leading zero
_SUP = re.compile(r"supersedes D([0-9]{1,6}): ")  # the only place a supersedes relation is read from: the start of the text
SUP_HIDDEN = re.compile(r"\bD\d+(?!\d)\s*:\s*supersedes\s+D\d")  # the same words on a line that is not a decision line


def decisions_file(dod_dir, number):
    """`<dod_path>/Decisions_<id>.md`: every assumption and decision of an issue, one `D<n>` line each."""
    return Path(dod_dir) / f"Decisions_{number}.md"


def render_file(number):
    """A new decisions file: a title and a format note. The note is a code span inside a paragraph, so it does not
    parse as a decision line (`parse_decisions` only reads lines that start with a checkbox)."""
    return (f"# Decisions — US{number}\n\n"
            "Format, one line each: `- [ ] D1: <text> → _(open)_ · kind: decision · why: <reason>`; "
            "a human records the outcome.\n\n")


def line_hash(text):
    """First 8 hex of sha256 of the text with whitespace collapsed: an edited line no longer matches its `posted:` marker."""
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()[:8]


def marker_hash(d):
    """The hash a `posted:` marker carries for the parsed decision `d`: the text alone, but for an override or a superseder the
    id, the text and the reason (what the PO read): an edited reason or a marker copied from another line no longer matches."""
    return line_hash(f"{d['id']}|{d['text']}|{d['why']}" if d["kind"] == "override" or d.get("supersedes") else d["text"])


_STATES = ("open", "validated", "rejected")


def _answers(parsed):
    """(entries by id, {target: [superseder ids]} over the valid edges, a bad edge was seen, {id: stands}). An edge is bad when its
    target is unknown or an override, or its superseder id is not higher (that covers self-reference and cycles); a bad edge
    changes nothing here. A decision `stands` unless it is rejected and no decision that stands supersedes it: the head of a chain
    answers everything below it."""
    ents = {d["id"]: d for k in _STATES for d in parsed[k]}
    by, bad = {}, False
    for d in ents.values():
        t = d.get("supersedes")
        if t is None:
            continue
        if t not in ents or ents[t]["kind"] == "override" or int(t[1:]) >= int(d["id"][1:]):
            bad = True
        else:
            by.setdefault(t, []).append(d["id"])
    stands = {}
    for i in sorted(ents, key=lambda i: -int(i[1:])):  # a superseder has the higher id, so it is decided first
        stands[i] = ents[i]["status"] != "rejected" or any(stands[s] for s in by.get(i, ()))
    return ents, by, bad, stands


def supersede_problem(parsed):
    """True when a `supersedes D<n>: ` line is invalid: unknown target, an override as target, a superseder that is not
    higher than its target (self-reference, a cycle). Strict mode reports it as `decisions-invalid`, the gate fails closed."""
    return _answers(parsed)[2]


def _stale(d):
    return not d["posted"] or d["posted"]["hash"] != marker_hash(d)


def supersede_unposted(parsed):
    """The decisions in a supersedes relation the PO has not seen as they read, by id order: every line that supersedes another
    (whatever its status or kind) and every open line an effective superseder drops, each without a `posted:` marker or with one
    that no longer matches `marker_hash`. The marker is a local claim, never checked against GitHub."""
    kept = {d["id"] for d in effective(parsed)["open"]}
    out = [d for k in _STATES for d in parsed[k] if _stale(d) and (d.get("supersedes") or (k == "open" and d["id"] not in kept))]
    return sorted(out, key=lambda d: int(d["id"][1:]))


def masked_by(parsed, d_id):
    """The id of the decision that stands as superseder of `d_id` (so its own verdict is not counted now), else None."""
    _, by, _, stands = _answers(parsed)
    return next((s for s in by.get(d_id, ()) if stands[s]), None)


def effective(parsed):
    """`parsed` without the decisions a live superseder answers (a superseded line stays in the file, only the set the gates
    count changes). A superseder is live when it is not rejected, or when it is itself answered by a live one; a rejected
    superseder with nothing live above it re-activates its target. Invalid edges drop nothing."""
    _, by, _, stands = _answers(parsed)
    gone = {t for t, ss in by.items() if any(stands[s] for s in ss)}
    return {**{k: [d for d in parsed[k] if d["id"] not in gone] for k in _STATES}, "unparsed": parsed["unparsed"]}


def unposted(parsed):
    """The lines the PO has not seen as they now read: the open lines, then the override lines that are open or validated
    (a validated override still has to reach the thread), each without a `posted:` marker or with one that no longer matches.
    An open line a superseder answers stays in: the PO must see it before it is retired. One predicate for `plan_post` and the
    strict `override-unposted` rule."""
    lines = parsed["open"] + [d for d in parsed["validated"] if d["kind"] == "override"]
    return [d for d in lines if not d["posted"] or d["posted"]["hash"] != marker_hash(d)]


def _line(raw):
    """One file line -> the decision dict, or None when it is a `D` line that breaks the grammar."""
    m = _CAND.match(raw)
    c = _COLON.match(m.group(3)) if m else None
    if not c:
        return None
    done = m.group(1) in "xX"
    rest = c.group(1).strip()
    st = list(_STATUS.finditer(rest))  # the LAST status arrow: a `→` inside the text must not split it
    if not st or (st[-1].group(1) == _OPEN) == done:
        return None
    s = st[-1]
    text, tail = rest[:s.start()].strip(), rest[s.end():]
    first, *parts = _SEP.split(tail)
    kv = {}
    for p in parts:
        k, _, v = p.partition(":")
        if k.strip() in kv:
            return None
        kv[k.strip()] = v.strip()
    kind = kv.get("kind", "decision")
    posted = _POSTED.fullmatch(kv["posted"]) if "posted" in kv else None
    bad = (first.strip() or not text or kind not in KINDS or ("posted" in kv and not posted)
           or (not done and not (kv.get("kind") and kv.get("why")))
           or (done and not kv.get("by")) or (not done and ("by" in kv or "note" in kv or "via" in kv))
           or ("via" in kv and not _VIA.fullmatch(kv["via"])))
    if bad:
        return None
    sup = _SUP.match(text)
    return {"id": f"D{m.group(2)}", "kind": kind, "text": text, "why": kv.get("why", ""),
            "status": "open" if not done else s.group(1), "by": kv.get("by", ""), "note": kv.get("note", ""),
            "posted": {"comment_id": int(posted.group(1)), "hash": posted.group(2)} if posted else None,
            "used": kv.get("used", ""), **({"supersedes": f"D{sup.group(1)}"} if sup else {}), **({"via": kv["via"]} if "via" in kv else {})}


def has_decision_lines(md):
    """True when the decisions file has a `D<n>` checkbox line outside fences and comments, well-formed or not."""
    return any(_CAND.match(ln) for ln in _visible(md)[0].splitlines())


def parse_decisions(md):
    """The decisions file -> {'open': [...], 'validated': [...], 'rejected': [...], 'unparsed': bool}. Lines inside code
    fences or <!-- --> comments are ignored; the first line of a duplicate id wins. `unparsed` is true when a `D` line
    breaks the grammar (it is left out) or a fence or comment is left open. A text that starts with `supersedes D<n>: `
    adds `supersedes: "D<n>"` to its entry (no other entry has the key). Empty text -> all empty."""
    out = {"open": [], "validated": [], "rejected": []}
    body, out["unparsed"] = _visible(md)
    seen = set()
    for raw in body.splitlines():
        if not _CAND.match(raw):  # linear: only the `- [ ] D<n>` prefix is matched here
            continue
        d = _line(raw) if len(raw) <= MAX_LINE else None
        if d is None:
            out["unparsed"] = True
        elif d["id"] not in seen:
            seen.add(d["id"])
            out[d["status"]].append(d)
    return out


def render_line(d):
    """The exact file line for a decision; `parse_decisions` reads it back unchanged."""
    if d["status"] == "open":
        parts = [f"- [ ] {d['id']}: {d['text']} → {_OPEN}", f"kind: {d['kind']}", f"why: {d['why']}"]
    else:
        parts = [f"- [x] {d['id']}: {d['text']} → {d['status']}", f"by: {d['by']}", f"note: {d['note']}".rstrip()]
        if d.get("via"):
            parts.append(f"via: {d['via']}")
        if d["kind"] != "decision":  # resolving must not lose that a line was an override or assumption
            parts.append(f"kind: {d['kind']}")
        if d["why"]:
            parts.append(f"why: {d['why']}")
    if d["posted"]:
        parts.append(f"posted: {d['posted']['comment_id']}:{d['posted']['hash']}")
    if d["used"]:
        parts.append(f"used: {d['used']}")
    return " · ".join(parts).rstrip()


# ---- the comment ----
MARKER = "[spec-kit-ghp:decisions]"
_HEAD = "Decisions and assumptions made during development; please validate or reject:"
MAX_COMMENT_LINE = 500
_CTRL = re.compile("[\x00-\x1f\x7f-\x9f  ]")
POSTED_LINE = re.compile(r"D([0-9]{1,9}) \((?:decision|assumption|override)\): ")  # a decision line of a posted comment: the id, digits only
_MARK = re.compile(r"·\s*posted\s*:\s*\d+:[0-9a-f]{8}(?=\s*(?:·|$))")  # the blanks before it are trimmed by `_marked`


def _scrub(text):
    """Every `@name` (an email address too) becomes `[mention removed]` (`comments._MENTION`, linear; the result holds no `@` that could start a mention). An
    address or @mention typed in a decision is replaced, never refused. Free-text names cannot be detected: the preview shows the exact comment before posting."""
    return comments._MENTION.sub(comments.NO_MENTION, text)


def _raw_line(d):
    return f"{d['id']} ({d['kind']}): {d['text']}" + (f" — why: {d['why']}" if d["why"] else "")


def _plain_line(d):
    """The line as a reader sees it before the Markdown escape: mentions replaced, every unprintable character as `\\uXXXX`, one line."""
    return comments._show(_scrub(_raw_line(d)))


def _comment_line(d):
    """(`D<n> (<kind>): <text> — why: <why>` defanged, shown printable, capped at MAX_COMMENT_LINE chars with …, then `& < >` escaped; was it cut)."""
    text = _plain_line(d)
    cut = len(text) > MAX_COMMENT_LINE
    return html.escape(text[:MAX_COMMENT_LINE - 1] + "…" if cut else text, quote=False), cut


def comment_line_is_lossy(d):
    """Does the posted comment line differ from the file's words (cut at MAX_COMMENT_LINE, or a mention or an unprintable character replaced)? Then a match
    between the file and the comment proves less than it seems: a changed tail or a swapped address would still match."""
    return _comment_line(d)[1] or _plain_line(d) != _raw_line(d)


def with_marker(content):
    """The whole comment: the marker and the hash of `content` on the first line, then `content`."""
    return f"{MARKER} {hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]}\n{content}"


def _newest_own(gh, ref, me):
    """(the `Comments` read, the newest comment of user id `me` whose text starts with the marker, or None)."""
    found = comments.fetch(gh, ref)
    mine = [c for c in found if c.author.id == me and c.body.startswith(MARKER)]  # ascending id: the last is the newest
    return found, (mine[-1] if mine else None)


def plan_post(gh, ref, parsed):
    """Read-only: {'action': none|add|adopt, 'body', 'comment_id', 'lines': [ids], 'cut': [ids], 'hashes': {id: hash8}, 'me'}. Always a NEW comment (never an
    edit). Lists every open line that is unposted or whose text no longer matches its `posted:` hash (`unposted`); other resolved lines are never posted.
    Nothing to post reads nothing. `adopt`: the newest own marked comment is exactly the planned text and was never edited (a post whose file write failed):
    nothing is sent, the lines get that comment's id. ValueError: a text over the limit, an identity that cannot be told, or an add while a comment could
    not be read reliably (it could be our own)."""
    todo = unposted(parsed)
    plan = {"action": "none", "body": "", "comment_id": None, "lines": [d["id"] for d in todo], "cut": [], "hashes": {d["id"]: marker_hash(d) for d in todo}, "me": None}
    if not todo:
        return plan
    shown = [_comment_line(d) for d in todo]
    body = with_marker("\n".join([_HEAD] + [t for t, _ in shown]))
    if len(body) > comments.MAX_COMMENT:
        raise ValueError("the comment would be %d characters, over the limit of %s; nothing posted: shorten or resolve some decisions"
                         % (len(body), format(comments.MAX_COMMENT, ",")))
    me = _me(gh)
    found, last = _newest_own(gh, ref, me)
    adopt = _same(last, body) and not last.edited  # an edited comment is not what the approver was shown
    if not adopt and found.notes:
        raise ValueError("the comments could not be read reliably (%s); nothing posted" % "; ".join(comments._show(n) for n in found.notes[:3]))
    return {**plan, "action": "adopt" if adopt else "add", "body": body, "comment_id": last.id if adopt else None, "me": me,
            "cut": [d["id"] for d, (_, cut) in zip(todo, shown) if cut]}


def render_plan(ref, plan, unparsed=False, no_file=False):
    action = {"none": "no unposted decisions; nothing to post", "add": "ADD a new comment (a decisions comment is never edited)",
              "adopt": "ADOPT comment %s (it is already on the issue with exactly this text; nothing is sent)" % plan["comment_id"]}[plan["action"]]
    lines = [f"# Plan: post decisions to issue #{ref.number}", "", "Action: " + action, ""]
    if no_file:
        lines += ["Note: no decisions file; nothing to post.", ""]
    if unparsed:
        lines += ["Note: a decision line breaks the grammar or the decisions file has an unclosed fence or comment; those lines are not included.", ""]
    if plan["cut"]:
        lines += [f"Note: {', '.join(plan['cut'])} cut to {MAX_COMMENT_LINE} characters (…).", ""]
    if plan["action"] in ("add", "adopt"):
        lines += ["Comment text:", ""] + [f"> {comments._show(x)}" for x in plan["body"].split("\n")] + [""]
        lines += [f"After posting, {', '.join(plan['lines'])} {'gets' if len(plan['lines']) == 1 else 'get'} ` · posted: <comment id>:<hash>` in the decisions file.", ""]
    return "\n".join(lines)


def _marked(md, hashes, comment_id):
    """The decisions file text with ` · posted: <comment_id>:<hash8>` on exactly the lines in `hashes` ({id: hash8});
    ValueError when one of them is missing or edited since the plan."""
    out, todo = [], dict(hashes)
    for raw, v in zip(md.splitlines(keepends=True), _visible(md)[0].split("\n")):
        text = raw.splitlines()[0]  # splitlines also ends a line at U+2028, \x0c, \x85, \x1c: keep that terminator
        eol = raw[len(text):]
        d = _line(v) if _CAND.match(v) and len(v) <= MAX_LINE else None
        if d and d["id"] in todo:
            if marker_hash(d) != todo[d["id"]]:
                break  # edited since the plan: left in `todo`, so the check below refuses
            del todo[d["id"]]
            if d["posted"]:  # only a parsed marker is replaced; marker-like text inside the decision stays
                found = list(_MARK.finditer(text))
                if not found:  # the marker the grammar read is not in the raw text: an inline <!-- --> comment sits in it
                    raise ValueError(f"{d['id']} holds an inline <!-- --> comment or fence that a rewrite would drop; edit it by hand")
                last = found[-1]  # the real one is in the tail, after the status arrow
                text = (text[:last.start()].rstrip() + text[last.end():]).rstrip()
            raw = f"{text} · posted: {comment_id}:{marker_hash(d)}{eol}"
        out.append(raw)
    if todo:
        raise ValueError(f"the decisions file changed since the plan ({', '.join(sorted(todo))}); nothing posted")
    return "".join(out)


def _atomic_write(path, text):
    """Write `text` over `path` through a uniquely named temp file in the same directory (mode kept, 0644 for a new
    file), then os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.chmod(tmp, stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def apply_post(gh, ref, plan, file_path):
    """Post exactly the planned comment once (`add`), or take the id of the one already on the issue (`adopt`), then mark its lines in the decisions file (tmp
    file + os.replace), re-reading the file after the post. {} for none, else {'comment_id', 'how': posted|found|adopted}: `found` is a write that failed in a way
    that may have applied it and whose re-read found it (nothing is sent again). The file is checked before the post, so a changed line posts nothing. A definite
    4xx is raised as is. Any other failure re-reads the comments; when the comment is not there the error is raised with a note: there is no second attempt
    (run the verb again after looking at the issue)."""
    if plan["action"] not in ("add", "adopt"):
        return {}
    path = Path(file_path)
    try:
        _marked(_read(path), plan["hashes"], 0)  # refuses before any write
    except (OSError, UnicodeError) as e:
        raise ValueError(f"cannot read the decisions file ({e.__class__.__name__}); nothing posted")
    if plan["action"] == "adopt":
        cid, how = plan["comment_id"], "adopted"
    else:
        try:
            cid, how = comments.add(gh, ref, plan["body"]), "posted"
        except WriteNotArmed:
            raise  # a gate that never armed it: nothing was sent, so there is nothing to look for
        except GhError as e:
            if e.status is not None and 400 <= e.status < 500:
                raise
            _, last = _newest_own(gh, ref, plan["me"])
            if not (_same(last, plan["body"]) and not last.edited):
                # the note comes FIRST: GhError cuts its detail at 400 characters, and the cut must take the end of GitHub's message
                raise GhError(e.code, e.status, f"not found on re-read and not retried: check the issue before running again; {e.detail}") from None
            cid, how = last.id, "found"
    try:
        _atomic_write(path, _marked(_read(path), plan["hashes"], cid))
    except (OSError, UnicodeError, ValueError) as e:
        pairs = "; ".join(f"{i}: · posted: {cid}:{h}" for i, h in plan["hashes"].items())
        raise ValueError(f"comment {cid} was posted but the decisions file could not be updated ({e}); append to the lines by hand: {pairs} "
                         "(or run the verb again: it finds the comment and marks the lines)")
    return {"comment_id": cid, "how": how}


_BY = re.compile(r"[A-Za-z0-9 ]{1,40}")
MAX_NOTE = 300
_MARKUP = re.compile(r"<!--|-->|`{3}|~{3}")  # would open or close a comment or fence in the decisions file


def _ids(parsed):
    return {d["id"]: d["status"] for k in ("open", "validated", "rejected") for d in parsed[k]}


def resolve(md, d_id, status, by, note, force=False, via=None):
    """The file text with the first `d_id` line rewritten as validated|rejected; its kind, why, posted and used
    markers and its line ending are kept, every other byte is unchanged. `via` (a comment id, digits only; `sync` passes the approver's
    comment) is written as ` · via: <id>`; without it an old `via` is dropped, since the new verdict no longer comes from that comment. A line that is already resolved needs `force`.
    ValueError for a bad status, role or note, an unknown id, a line holding an inline comment, or a result that
    `parse_decisions` would not read back the same over the whole file."""
    by, note = by.strip(), note.strip()
    if via is not None and not (isinstance(via, str) and _VIA.fullmatch(via)):
        raise ValueError("via must be a comment id (digits only)")
    if status not in ("validated", "rejected"):
        raise ValueError("the status must be validated or rejected")
    if not _BY.fullmatch(by):
        raise ValueError("--by must be 1-40 letters, digits or spaces (a role, never a name)")
    if len(note) > MAX_NOTE or _CTRL.search(note) or _MARKUP.search(note):
        raise ValueError(f"--note must be at most {MAX_NOTE} characters with no control characters, line breaks, "
                         "`<!--`, `-->` or code fences")
    out, found = [], False
    for raw, v in zip(md.splitlines(keepends=True), _visible(md)[0].split("\n")):
        d = _line(v) if not found and _CAND.match(v) and len(v) <= MAX_LINE else None
        if d and d["id"] == d_id:
            found = True
            if v != raw.splitlines()[0]:
                raise ValueError(f"{d_id} holds an inline <!-- --> comment or fence that a rewrite would drop; edit it by hand")
            if d["status"] != "open" and not force:
                raise ValueError(f"{d_id} is already {d['status']} (by {d['by']}); pass --force to overwrite that verdict")
            new = {**{k: v for k, v in d.items() if k != "via"}, "status": status, "by": by, "note": note, **({"via": via} if via else {})}
            line = render_line(new)
            if len(line) > MAX_LINE or _line(line) != new:  # over-long, or the note collides with the grammar
                raise ValueError(f"the resolved line would be longer than {MAX_LINE} characters or would not read back "
                                 "(a note must not contain ` · kind:`-style markers or `→ validated`)")
            raw = line + raw[len(raw.splitlines()[0]):]  # splitlines also ends a line at U+2028, \x0c, \x85, \x1c
        out.append(raw)
    if not found:
        raise ValueError(f"no decision {d_id} in the decisions file (see `ghp decisions <n>`)")
    new_md = "".join(out)
    a, b = md.splitlines(keepends=True), new_md.splitlines(keepends=True)
    before, after = parse_decisions(md), parse_decisions(new_md)
    if (len(a) != len(b) or sum(x != y for x, y in zip(a, b)) != 1 or after["unparsed"] != before["unparsed"]
            or _ids(after) != {**_ids(before), d_id: status}):
        raise ValueError(f"the rewrite of {d_id} would change more than that line or break the file; nothing written")
    return new_md


_MAX_FILE = 1 << 20


def _read(path):
    """The decisions file's text, newlines untouched. ValueError for a symlink, a file that is not a regular file or one over 1 MiB (the PR controls the tree: a
    link must not make a write copy another file's text into the ledger); a missing or unreadable file is an OSError. The checks run on the OPEN descriptor
    (O_NOFOLLOW; O_NONBLOCK so a FIFO cannot hang the open) and at most 1 MiB + 1 byte is read from it, so a swap or a growth after the check changes nothing."""
    refusal = ValueError("the decisions file must be a regular file of at most 1 MiB, not a symlink; nothing written")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError as e:
        if e.errno == errno.ELOOP:  # O_NOFOLLOW met a symlink
            raise refusal from None
        raise
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > _MAX_FILE:
            raise refusal
        data = f.read(_MAX_FILE + 1)
    if len(data) > _MAX_FILE:  # grew past the cap while it was read
        raise refusal
    return data.decode("utf-8")


def apply_resolve(file_path, d_id, status, by, note, force=False):
    """Rewrite one line of the decisions file in place (unique temp file + os.replace). Refuses when the file changed
    between the read and the write."""
    path = Path(file_path)
    try:
        md = _read(path)
        new = resolve(md, d_id, status, by, note, force)
        if _read(path) != md:
            raise ValueError("the decisions file changed while resolving; nothing written, run it again")
        _atomic_write(path, new)
    except (OSError, UnicodeError) as e:
        raise ValueError(f"cannot update the decisions file {path} ({e.__class__.__name__}: {e}); nothing written")


# ---- `ghp decisions <n> --sync`: approvers' `/approve D<n>` and `/reject D<n>` comments become verdicts (a local write; T28, ported from ado) ----
SYNC_RULES = ("not-posted", "edited", "posting-lossy", "posting-missing", "posting-mismatch", "posting-markup", "posting-edited", "posting-superseded",
              "posting-disguised", "bad-newer", "resolved-locally", "no-verdict", "older-than-posting", "bad-comment-id", "up-to-date", "unwritable")  # why a line was left alone; fixed names
SYNC_BY = "approver"  # the role written on a synced line (never a name)
_VERDICT = {"approve": "validated", "reject": "rejected"}


def _sync_note(text):
    """A comment's note as a safe `note:` value: mentions replaced, control characters, `<!--`, `-->`, fences and the grammar's own separators (`·`, `→`,
    `->`) turned into blanks or dashes, whitespace collapsed, cut to MAX_NOTE. Never refused: the verdict must land with or without it."""
    t = _scrub(text.replace("·", "-").replace("→", "-").replace("->", "-"))
    return " ".join(_MARKUP.sub(" ", _CTRL.sub(" ", t)).split())[:MAX_NOTE]


_NEVER = (datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), 0)
_LAST = (datetime.datetime.max.replace(tzinfo=datetime.timezone.utc), float("inf"))  # the order key of a marked comment whose time cannot be read


def posting_index(rows):
    """{n: (comment, whitespace-normalised lines it shows for `D<n>`)}: the NEWEST decisions comment that lists each id, built in ONE pass (linear in the
    comment bytes). The ONE reading of "newest posting" that `--sync`, the `wait-po-validation` gate and the done-time cross-check share: any author, edited
    or not (it is a claim to check, not evidence), newest by created time then id; a comment that is not a plain kit posting lists nothing here, but the
    newest key of such a marked comment (one with an unreadable time counts as the newest) is kept under "unreadable": what it lists cannot be told."""
    best, out = {}, {}
    for c in rows:
        if not approvals.marked(c, MARKER):
            continue
        key, lines = approvals._order(c), approvals.kit_lines(c.body, MARKER)
        if key is None or not lines:
            out["unreadable"] = max(out.get("unreadable", _NEVER), _LAST if key is None else key)
            continue
        shown = {}
        for ln in lines:
            m = POSTED_LINE.match(ln)
            if m:
                shown.setdefault(int(m.group(1)), []).append(" ".join(ln.split()))
        for n, lines in shown.items():
            if n not in best or key > best[n][0]:
                best[n] = (key, c, tuple(lines))
    out.update({n: (c, lines) for n, (_, c, lines) in best.items()})
    return out


def superseded(d, index):
    """Does a posted line `d` point at anything but the NEWEST posting of its id (`posting_index`), or differ from the line that posting shows?"""
    best, shown = index.get(int(d["id"][1:]), (None, ()))
    return bool(d["posted"] and best is not None
                and (int(d["posted"]["comment_id"]) != best.id or " ".join(_comment_line(d)[0].split()) not in shown))


def _posting_rule(d, rows, excluded, index):
    """(rule, posting comment): why the verdict on `d` cannot be read yet, else (None, the comment that posted it). The `posted:` marker is a claim in a
    PR-controlled file: the comment it names must exist, be a plain kit posting (`approvals.kit_lines`: marker line with the content hash) by a kit identity
    (`excluded`), hold `d`'s line as it reads NOW, be UNEDITED (`Comment.edited`), have no newer marked comment that
    cannot be read (T28d) and be the NEWEST decisions comment that lists `d`."""
    if not d["posted"]:
        return "not-posted", None
    if d["posted"]["hash"] != marker_hash(d):
        return "edited", None
    if comment_line_is_lossy(d):
        return "posting-lossy", None
    c = rows.get(int(d["posted"]["comment_id"]))
    if c is None or approvals._order(c) is None:
        return "posting-missing", None
    if not (isinstance(c.body, str) and c.body.startswith(MARKER)) or c.author.id not in excluded:
        return "posting-mismatch", None
    lines = {" ".join(x.split()) for x in approvals.kit_lines(c.body, MARKER)}
    if not lines:
        return "posting-markup", None
    if " ".join(_comment_line(d)[0].split()) not in lines:
        return "posting-mismatch", None
    if c.edited:
        return "posting-edited", None
    if index.get("unreadable", _NEVER) > approvals._order(c):  # a newer marked comment that cannot be read could be a newer posting of `d`
        return "posting-disguised", None
    return ("posting-superseded", c) if superseded(d, index) else (None, c)


def _posting_key(post):
    """The (created, id) key a verdict must beat (a kit posting is never edited: an edited one is refused before this)."""
    return approvals._order(post)


def _check_ids(md, parsed):
    ids = ["D%s" % m.group(2) for ln in _visible(md)[0].splitlines() if (m := _CAND.match(ln))]
    if parsed["unparsed"] or len(ids) != len(set(ids)) or not all(re.fullmatch(r"D[0-9]{1,9}", i) for i in ids):  # ids are printed: ASCII digits only
        raise ValueError("the decisions file is malformed (a line that breaks the grammar, an unclosed fence or comment, or a repeated id); nothing written")
    return ids


def sync(md, comment_rows, approvers, excluded):
    """(the new decisions file text, {"applied", "skipped": {id: rule}, "status", "bad_newer": {id: n}, "masked", ["no_longer_valid"]}): ado's `sync` over
    `comments.fetch` output (the caller refused one that dropped comments). For every `D<n>` line the newest VALID `/approve|/reject D<n>`
    (`approvals.newest`) is taken when the line's posting holds (`_posting_rule`) and the verdict is NEWER than the posting, and the line is open or was
    itself written by a sync (`via`): a verdict a human wrote is never overwritten. An approver's verdict that cannot land is ValueError (nothing written)."""
    parsed = parse_decisions(md)
    _check_ids(md, parsed)
    comment_rows = approvals.cached(comment_rows)
    rows, index = {c.id: c for c in comment_rows}, posting_index(comment_rows)
    applied, skipped, status, bad_newer, bad_by, blocked, lost, out = [], {}, {}, {}, {}, [], [], md
    for d in sorted((d for k in _STATES for d in parsed[k]), key=lambda d: int(d["id"][1:])):
        i = d["id"]
        rule, post = _posting_rule(d, rows, excluded, index)
        if rule is None and d["status"] != "open" and not d.get("via"):
            rule = "resolved-locally"
        tally = {}
        if rule:
            skipped[i] = rule
            if d.get("via"):  # a synced verdict whose evidence can no longer be read: a newer opposite command must not be dropped silently
                win = approvals.newest(comment_rows, i, approvers, excluded, tally=tally)
                bad_newer[i], bad_by[i] = tally.get("bad_newer", 0), tally.get("bad_newer_by", {})
                if win is not None and _VERDICT.get(win["verb"]) not in (None, d["status"]):
                    blocked.append(i)
            continue
        win = approvals.newest(comment_rows, i, approvers, excluded, tally=tally)
        bad_newer[i], bad_by[i] = tally.get("bad_newer", 0), tally.get("bad_newer_by", {})
        if win is None or win["verb"] not in _VERDICT:
            skipped[i] = "no-verdict"
            if d.get("via"):
                lost.append(i)
            continue
        if bad_newer[i]:  # a newer approver line that cannot be read could be a retraction: the wait gate and the cross-check hold it open too
            skipped[i] = "bad-newer"
            if d.get("via"):
                lost.append(i)
            continue
        key, cid, pkey = approvals._wkey(win), str(win["id"]), _posting_key(post)
        if pkey is None:
            skipped[i] = "posting-edited"
        elif key is None or key <= pkey:
            skipped[i] = "older-than-posting"
        elif not _VIA.fullmatch(cid):
            skipped[i] = "bad-comment-id"
        elif d["status"] == _VERDICT[win["verb"]] and d.get("via") == cid:
            skipped[i] = "up-to-date"
        else:
            for note in (_sync_note(win["text"]), ""):  # a note that collides with the grammar must not stop the verdict
                try:
                    out = resolve(out, i, _VERDICT[win["verb"]], SYNC_BY, note, True, cid)
                    applied.append(i)
                    status[i] = _VERDICT[win["verb"]]
                    break
                except ValueError:
                    continue
            else:
                skipped[i] = "unwritable"
                blocked.append(i)
        if d.get("via") and i not in applied and skipped.get(i) != "up-to-date":
            lost.append(i)
    if blocked:
        raise ValueError("an approver's newer verdict cannot be written to the decisions file for %s, so the earlier verdict would stand; fix the line and "
                         "run it again; nothing written" % ", ".join(blocked))
    a, b = md.splitlines(keepends=True), out.splitlines(keepends=True)
    if len(a) != len(b) or sum(x != y for x, y in zip(a, b)) != len(applied):
        raise ValueError("the sync would change more than the target lines; nothing written")
    after = parse_decisions(out)
    return out, {"applied": applied, "skipped": skipped, "status": status, "bad_newer": bad_newer, "bad_newer_by": bad_by,
                 "masked": {i: m for i in applied if (m := masked_by(after, i))}, **({"no_longer_valid": lost} if lost else {})}


def apply_sync(file_path, comment_rows, approvers, excluded):
    """Run `sync` over the decisions file and write the result in place (unique temp file + os.replace, only when something changed). Refuses (ValueError,
    nothing written) for a symlink, a file that is not a regular file or is over 1 MiB, a file that changed meanwhile, or one that does not read back as
    written (the old text is then put back). Returns the `sync` report."""
    path = Path(file_path)
    try:
        md = _read(path)
        new, report = sync(md, comment_rows, approvers, excluded)
        if new != md:
            if _read(path) != md:
                raise ValueError("the decisions file changed while syncing; nothing written, run it again")
            _atomic_write(path, new)
            if _read(path) != new:
                _atomic_write(path, md)
                raise ValueError("the decisions file did not read back as written; nothing recorded")
    except (OSError, UnicodeError) as e:
        raise ValueError("cannot update the decisions file (%s); nothing written" % e.__class__.__name__)
    return report


MAX_TEXT = 1500
_ADD_KINDS = ("decision", "assumption")  # `override` is only created by `add_override` (the strict override command)


def _supersedes_target(md, target):
    """Refuse (ValueError, fixed texts) unless `target` is a decision of `md` the developer may answer: it exists, is not an
    override, is rejected or open (not validated), and no live decision answers it yet."""
    if not re.fullmatch(r"D[0-9]{1,6}", target if isinstance(target, str) else ""):
        raise ValueError("--supersedes must be D<n>: the letter D and one to six digits")
    ents, by, _, stands = _answers(parse_decisions(md))
    if target not in ents:
        raise ValueError("--supersedes names a decision that is not in the decisions file")
    if ents[target]["kind"] == "override":
        raise ValueError("--supersedes cannot answer an override")
    if ents[target]["status"] == "validated":
        raise ValueError("--supersedes can only answer a rejected or open decision, not a validated one")
    if ents[target]["status"] == "open" and _stale(ents[target]):
        raise ValueError(f"post {target} first (`ghp decisions <n> --post`): the PO has not seen it, so it cannot be superseded")
    live = [s for s in by.get(target, ()) if stands[s]]
    if live:
        raise ValueError(f"{target} is already superseded by {live[0]}: supersede {live[0]} instead, or have the PO reject it first")


def add(md, kind, text, why="", _kinds=_ADD_KINDS, _min_why=0, supersedes=None):
    """(the file text with one new open `D<n>` line appended, "D<n>"). n = highest id in the file + 1 (a malformed or
    duplicate line still counts), grammar-exact. @mentions and addresses are replaced as in a posted comment.
    ValueError (never echoing the text) for a bad kind, a text of 0 or over MAX_TEXT characters, a why over MAX_NOTE,
    control characters, line breaks, `<!--`, `-->`, code fences, or a line that would not read back (so a file with an
    unclosed fence or comment, which would hide the new line, is refused too). `supersedes` ("D<n>"): the line's text then
    starts with `supersedes D<n>: ` (see `_supersedes_target` for what is refused); the prefix cannot be typed by hand."""
    if kind not in _kinds:
        raise ValueError("--kind must be decision or assumption")
    for name, v in (("the text", text), ("--why", why)):  # before strip(), which would drop a trailing line break
        if _CTRL.search(v) or _MARKUP.search(v):
            raise ValueError(f"{name} must not contain control characters, line breaks, `<!--`, `-->` or code fences")
    text, why = text.strip(), why.strip()
    if kind != "override":  # an override reason is refused for markup, never rewritten
        text, why = _scrub(text), _scrub(why)
    why = why or "unspecified"
    if not 1 <= len(text) <= MAX_TEXT:
        raise ValueError(f"the text must be 1 to {MAX_TEXT} characters")
    if not _min_why <= len(why) <= MAX_NOTE:
        raise ValueError(f"--why must be {f'{_min_why} to ' if _min_why else 'at most '}{MAX_NOTE} characters")
    if _SUP.match(text):
        raise ValueError("the text must not start with `supersedes D<n>: `; use --supersedes D<n>")
    if supersedes is not None:
        _supersedes_target(md, supersedes)
        text = f"supersedes {supersedes}: {text}"
    nums = [int(n) for n in re.findall(r"(?m)^[ \t]*(?:<!--[ \t]*)?(?:[-*+][ \t]+)?\[[ xX]\][ \t]*D(\d+)", md)]  # raw text: an id inside a comment or fence is taken too
    d_id = f"D{max(nums, default=0) + 1}"
    new = {"id": d_id, "kind": kind, "text": text, "why": why, "status": "open", "by": "", "note": "",
           "posted": None, "used": "", **({"supersedes": supersedes} if supersedes is not None else {})}
    line = render_line(new)
    eol = "\r\n" if "\r\n" in md else "\n"
    new_md = md + ("" if not md or md.endswith(("\n", "\r")) else eol) + line + eol
    before, after = parse_decisions(md), parse_decisions(new_md)
    if len(line) > MAX_LINE or _line(line) != new or _ids(after) != {**_ids(before), d_id: "open"} \
            or after["unparsed"] != before["unparsed"]:
        raise ValueError("the line would not read back from the decisions file (a text or --why holding ` · kind:`-style "
                         "markers or `→ validated`, or an unclosed code fence or comment in the file); nothing written")
    return new_md, d_id


def _apply(file_path, number, build):
    # shortcut: two concurrent writers can lose a line (no lock); fine for a local human/agent command, add a lock if parallel agents write
    """Append the line `build(md) -> (new md, id)` makes, creating the file from `render_file` when it is missing (unique
    temp file + os.replace). Refuses when the file changed or appeared between the read and the write, and when the file
    on disk does not read back as exactly the new text (the old text is then put back). Returns the new id."""
    path = Path(file_path)
    try:
        exists = path.exists()
        md = _read(path) if exists else render_file(number)
        new, d_id = build(md)
        if path.exists() != exists or (exists and _read(path) != md):
            raise ValueError("the decisions file changed while adding; nothing written, run it again")
        _atomic_write(path, new)
        if _read(path) != new:
            if exists:
                _atomic_write(path, md)
            raise ValueError("the decisions file did not read back as written; nothing recorded")
    except (OSError, UnicodeError) as e:
        raise ValueError(f"cannot update the decisions file {path} ({e.__class__.__name__}: {e}); nothing written")
    return d_id


def apply_add(file_path, number, kind, text, why="", supersedes=None):
    return _apply(file_path, number, lambda md: add(md, kind, text, why, supersedes=supersedes))


OVERRIDE_RULES = ("test-command", "l2", "open-question", "placeholder")  # the strict rules an override may skip
MIN_REASON = 20


def add_override(md, rule, reason):
    """(the file text with one new open `kind: override` line `skip <rule>` whose why is `reason`, "D<n>"), as `add`
    (same grammar, read-back and refusals) but the reason (MIN_REASON..MAX_NOTE characters once stripped) is stored as
    typed: markup is refused, nothing is scrubbed (the posted comment scrubs it like any line)."""
    if rule not in OVERRIDE_RULES:
        raise ValueError(f"the rule must be one of: {', '.join(OVERRIDE_RULES)}")
    if not isinstance(reason, str):
        raise ValueError("--reason is required")
    if _scrub(reason) != reason:
        raise ValueError("--reason must not contain an e-mail address or an @mention (a posted decision replaces them): describe it without")
    parsed = parse_decisions(md)
    live = [d for d in parsed["open"] + parsed["validated"] if d["kind"] == "override" and d["text"] == f"skip {rule}"]
    if live:
        raise ValueError(f"an override of {rule} is already in force ({live[0]['id']}); reject it first to record a new one")
    try:
        return add(md, "override", f"skip {rule}", reason, ("override",), MIN_REASON)
    except ValueError as e:
        raise ValueError(str(e).replace("--why", "--reason")) from None


def apply_override(file_path, number, rule, reason):
    return _apply(file_path, number, lambda md: add_override(md, rule, reason))


_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def stamp_used(md, ids, date):
    """The file text with ` · used: <date>` appended to the lines of the decisions in `ids`, once: a line that already
    has `used` keeps its date. Pure: the caller writes it (`_atomic_write`). Every other byte, line endings included,
    is unchanged. ValueError (nothing returned) for a bad date, an id that is not a line of the file, or a line holding
    an inline comment or fence that a rewrite would drop."""
    if not isinstance(date, str) or not _DATE.fullmatch(date):
        raise ValueError("the date must be YYYY-MM-DD")
    out, todo = [], set(ids)
    for raw, v in zip(md.splitlines(keepends=True), _visible(md)[0].split("\n")):
        text = raw.splitlines()[0]  # the terminator may be U+2028 etc.: keep it as it is
        d = _line(v) if _CAND.match(v) and len(v) <= MAX_LINE else None
        if d and d["id"] in todo:
            todo.discard(d["id"])
            if v != text:
                raise ValueError(f"{d['id']} holds an inline <!-- --> comment or fence that a rewrite would drop; edit it by hand")
            if not d["used"]:
                raw = f"{text} · used: {date}{raw[len(text):]}"
        out.append(raw)
    if todo:
        raise ValueError(f"no such decision in the decisions file: {', '.join(sorted(todo))}")
    return "".join(out)


def apply_stamp(file_path, number, ids):
    """Stamp `used: <today>` on the override lines in `ids` (an apply path that relied on them, just before it sends). Same write as
    `apply_add`: temp file + os.replace, whole-file read-back, ValueError (nothing written) on any failure."""
    from datetime import date
    return _apply(file_path, number, lambda md: (stamp_used(md, ids, date.today().isoformat()), None))


# ---- the verb ----
E_NOT_COMMENT_MODE = "terminal mode (`approval` is not \"comment\" on the target branch): nothing to sync"  # fixed text; the comment mode is T27, its sync T28


def register(sub):
    p = sub.add_parser("decisions", help="list the open, validated and rejected D<n> lines of Decisions_<n>.md as JSON; --add records one; --post previews "
                                         "sending the unposted open ones as one new issue comment (nothing is sent without --apply)")
    p.add_argument("ref", help="5, #5, owner/repo#5 or an issue URL")
    p.add_argument("--post", action="store_true", help="preview posting the unposted decisions as one new comment")
    p.add_argument("--if-any", action="store_true", help="with --post/--plan-out: a missing decisions file is a no-op (exit 0, nothing to post) instead of exit 2")
    p.add_argument("--add", metavar="TEXT", help="record one assumption or decision: appends the next D<n> line to <dod_path>/Decisions_<n>.md (created when "
                                                 "missing). A local write; needs --kind")
    p.add_argument("--kind", help="with --add: decision or assumption")
    p.add_argument("--why", help="with --add: the reason (at most 300 characters)")
    p.add_argument("--supersedes", metavar="D<n>", help="with --add: this decision answers the rejected (or still open) D<n>: the line starts with "
                                                        "`supersedes D<n>: ` and the old line is left as it is")
    p.add_argument("--resolve", metavar="D<n>", help="a human records the PO's verdict on this line (local edit of the decisions file; with --validated|--rejected, "
                                                     "optional --by and --note). Refuses without a terminal so an agent shell cannot do it by accident; this is not a "
                                                     "security boundary, the strict gate and the git history are the enforcement")
    p.add_argument("--force", action="store_true", help="with --resolve: overwrite the verdict of a resolved line")
    p.add_argument("--sync", action="store_true", help="record the approvers' `/approve D<n>` and `/reject D<n>` comments as verdicts (comment mode only; in "
                                                       "terminal mode a no-op that says so)")
    p.add_argument("--if-comment-mode", action="store_true", help="with --sync: the same no-op (the workflow's sync step passes it)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--validated", dest="verdict", action="store_const", const="validated")
    g.add_argument("--rejected", dest="verdict", action="store_const", const="rejected")
    p.add_argument("--by", help="with --resolve: the role that decided (default PO; never a name)")
    p.add_argument("--note", help="with --resolve: a short note (at most 300 characters)")
    write_gate.add_write_flags(p)


def _check_flags(args):
    """Every refusal of a flag combination (ValueError, exit 2) comes before the config, the file or GitHub is touched."""
    previewing = args.post or args.plan_out is not None
    gating = previewing or args.apply or args.expect_plan is not None
    if args.apply and not args.post:
        raise ValueError("--apply needs --post")
    if args.if_comment_mode and not args.sync:
        raise ValueError("--if-comment-mode needs --sync")
    if args.add is not None:
        if gating or args.resolve is not None or args.verdict or args.by is not None or args.note is not None or args.force:
            raise ValueError("--add cannot be combined with --post, --apply, --plan-out, --expect-plan or --resolve options")
        if args.kind is None:
            raise ValueError("--add needs --kind decision|assumption")
    elif args.kind is not None or args.why is not None or args.supersedes is not None:
        raise ValueError("--kind, --why and --supersedes need --add")
    if args.if_any and (args.add is not None or args.resolve is not None or not previewing):
        raise ValueError("--if-any needs --post or --plan-out and cannot be combined with --add or --resolve")
    if args.sync and (gating or args.if_any or args.add is not None or args.resolve is not None or args.verdict or args.by is not None or args.note is not None
                      or args.force):
        raise ValueError("--sync cannot be combined with any other decisions option")
    if args.resolve is not None:
        if gating:
            raise ValueError("--resolve cannot be combined with --post, --apply, --plan-out or --expect-plan")
        if not args.verdict:
            raise ValueError("--resolve needs --validated or --rejected")
    elif args.verdict or args.by is not None or args.note is not None or args.force:
        raise ValueError("--validated, --rejected, --by, --note and --force need --resolve")


def _path(ref):
    return decisions_file(safe_dir(Config.load().dod_path), ref.number)


E_SYNC_NO_APPROVERS = "--sync needs approvers on the target branch (`approvers` in ghp.config.json there); none are configured; nothing written"
E_SYNC_FILE = "cannot read the decisions file (missing, a symlink, outside the working tree, over 1 MiB or not UTF-8); nothing written"
E_SYNC_DROPPED = ("the issue's comments could not all be read or attributed (dropped at the join), so a newer retraction could be missing; nothing written")
E_SYNC_IDENTITY = "cannot tell which GitHub identity this token is, so the comments cannot be attributed; nothing written"
E_RESOLVE_APPROVERS = ("--resolve is refused: approvers are configured on the target branch, so verdicts come from their `/approve D<n>` and "
                       "`/reject D<n>` comments: run `ghp decisions <n> --sync`")


def _sync_decisions(gh, ref, trust):
    """`decisions --sync` in comment mode: approvers' `/approve D<n>` and `/reject D<n>` comments become verdicts in the local decisions file (no GitHub
    write, no gate: a local write). Every refusal (exit 2, fixed text, nothing written) comes before anything is changed."""
    if not trust.approvers:
        raise ValueError(E_SYNC_NO_APPROVERS)
    path = _path(ref)
    if not os.path.lexists(path) or gate._read(path, ".", set()) is None:
        raise ValueError(E_SYNC_FILE)
    found = comments.fetch(gh, ref)
    if found.notes:  # read before anything else: a dropped comment could be a retraction
        raise ValueError(E_SYNC_DROPPED)
    try:
        skip = approvals.excluded(_me(gh), found)
    except ValueError:  # `_me` puts the client's error text into its message
        raise ValueError(E_SYNC_IDENTITY) from None
    report = apply_sync(path, found, set(trust.approvers), skip)
    notice = ["%s: %s: check the thread" % (i, approvals.bad_newer_text(by, "later")) for i, by in report["bad_newer_by"].items() if by]
    notice += ["%s is superseded by %s: this verdict is masked while %s stands (a rejected %s would bring it back)" % (i, m, m, m) for i, m in report["masked"].items()]
    if report.get("no_longer_valid"):  # the file keeps the old verdict; the wait gate and the cross-check no longer agree with it
        print("approval-no-longer-valid: %s" % ", ".join(report["no_longer_valid"]), file=sys.stderr)
    print(json.dumps({"id": ref.number, **report, **({"notice": notice} if notice else {})}, indent=2))
    return 0


def _resolve_decision(args, ref):
    """`--resolve`: the PO's verdict is recorded by a human. Refused when the target branch names approvers (their comments are the verdicts); the
    terminal is tested before anything is read or written."""
    if approvals.target_trust(".").approvers:
        raise ValueError(E_RESOLVE_APPROVERS)
    if not (sys.stdin and sys.stdin.isatty()):
        raise ValueError("--resolve records the PO's verdict, which only a human may do: run it in your own terminal "
                         "(this shell has no terminal on stdin; a guard against accidents, not a security boundary)")
    path = _path(ref)
    if not path.exists():
        raise ValueError(f"no decisions file at {comments._show(path)}; record one with `ghp decisions {ref.number} --add \"<text>\" --kind decision`")
    by, note = args.by if args.by is not None else "PO", args.note or ""
    try:
        masked = masked_by(parse_decisions(_read(path)), args.resolve)
    except (OSError, UnicodeError, ValueError):
        masked = None  # apply_resolve reports an unreadable file
    apply_resolve(path, args.resolve, args.verdict, by, note, args.force)
    out = {"id": ref.number, "resolved": args.resolve, "status": args.verdict, "by": by.strip(), "note": note.strip()}
    if masked:
        out["notice"] = f"{args.resolve} is superseded by {masked}: this verdict is masked while {masked} stands (a rejected {masked} would bring it back)"
    print(json.dumps(out, indent=2))


def run(args, gh):
    _check_flags(args)
    if args.resolve is not None:
        return _resolve_decision(args, issues._resolve(args.ref)) or 0
    ref = issues._resolve(args.ref)  # config, then the ref: a bad one is refused before any call
    if args.sync:
        trust = approvals.target_trust(".")  # the TARGET branch's mode and approvers, never the working tree's (X4)
        if trust.approval != "comment":  # terminal mode: nothing to read, nothing to write
            print(json.dumps({"id": ref.number, "synced": False, "note": E_NOT_COMMENT_MODE}, indent=2))
            return 0
        return _sync_decisions(gh, ref, trust)
    path = _path(ref)
    if args.add is not None:  # a local write: no token, no request
        d_id = apply_add(path, ref.number, args.kind, args.add, args.why or "", args.supersedes)
        print(json.dumps({"id": d_id, "file": str(path)}, indent=2))
        return 0
    previewing = args.post or args.plan_out is not None
    if not os.path.lexists(path):  # only `--add` creates it
        if not (previewing and args.if_any):
            raise ValueError(f"no decisions file at {comments._show(path)}; record one with `ghp decisions {ref.number} --add \"<text>\" --kind decision`")
        md, path = "", None
    elif (md := gate._read(path, ".", set())) is None:
        raise ValueError(f"cannot read the decisions file {comments._show(path)} (a symlink, outside the working tree, over 1 MiB or not UTF-8)")
    parsed = parse_decisions(md)
    if not previewing:
        print(json.dumps(parsed, indent=2))  # ASCII-escaped (the default): no raw bidi or C1 control character from the file reaches the terminal
        return 0
    plan = plan_post(gh, ref, parsed)  # read only
    text = render_plan(ref, plan, parsed["unparsed"], path is None)
    write_gate.arm_or_refuse(args, gh, text, approvals.approve_hook(gh, ref, "decisions-post", text, plan["action"] == "add"))
    print(text)
    if args.apply:
        res = apply_post(gh, ref, plan, path)
        cid = res.get("comment_id")
        print({"posted": f"posted comment {cid}", "found": f"found on re-read: comment {cid} was posted before the error; nothing sent again",
               "adopted": f"found on the issue: comment {cid} already holds this text; nothing sent", None: "nothing sent: no unposted decisions"}[res.get("how")])
    return 0
