"""`ghp questions N`: the open and answered questions of the ledger's section 2 (read only), and `--post`: one marked comment on the issue.

Ported from ado's `questions` (T14: list and post; T28: `--sync`). What changed for GitHub:
- The comment is Markdown, lines joined by `\\n`. The first line is `[spec-kit-ghp:questions] <16 hex of the sha256 of the rest>`.
- A comment is never edited: when the question set changes the new text is a NEW comment, and only the NEWEST comment of the token's own user id that
  starts with the marker counts. If its whole text is the planned text the post is `unchanged`, so re-posting never duplicates (AC3).
- A write that may have been applied (timeout, no HTTP response, a reply without an id, any 5xx) is never repeated: the comments are read again, the
  comment is looked for by its exact text, and only a definite 4xx skips that read (AC4). No second attempt is made; the error says so.
- A text over `comments.MAX_COMMENT` characters is refused in the preview (AC5).
- Identities are numeric user ids: `_me` is `gh.viewer().id`.
Everything shown or posted from the ledger is untrusted text: control, bidi and invisible characters become `\\uXXXX` and `@name` becomes `[mention removed]`
(no notification can be forged). Every pattern below is linear on hostile input (a long run of blanks): no `\\s*` before an optional part, no lazy
group before `\\s*$`, no `finditer` over blanks.
"""
import datetime
import hashlib
import html
import json
import re
import sys

from . import approvals, comments, gate, issues, plan as write_gate
from .config import Config
from .dod import dod_file, safe_dir, section, section_span, _visible
from .gh import GhError

UNPARSED_NOTE = "§2 has an unclosed code fence or comment; questions could not be read"
_OPEN = "_(open)_"
# `- [ ] Q1: <question> → <answer | _(open)_> · owner: <role>`; lenient: spacing, `->`, `*`/`+` bullet, no owner.
_LINE = re.compile(r"^\s*(?:[-*+]\s+)?\[([ xX])\]\s*Q(\d+)\s*:(.*)$")
_EMPTY_ASSUMED = re.compile(r"assumed\s*:\s*$", re.I)
_OWNER = re.compile(r"\s*owner\s*:(.*)$", re.I)


def _parts(rest):
    """(question, answer, owner) of the text after `Q<n>:`; owner is `?` when the line names none. The owner is the text after the LAST `·` or `|`
    when it reads `owner: <name>`; the question and the answer split at the LAST `→` (a `->` in either must not split it), only without one at the
    FIRST `->`. Plain string searches, so a long run of blanks costs one pass."""
    owner, cut = "?", max(rest.rfind("·"), rest.rfind("|"))
    if cut >= 0 and (o := _OWNER.match(rest, cut + 1)):
        rest, owner = rest[:cut], o.group(1).strip() or "?"
    i, width = rest.rfind("→"), 1
    if i < 0:
        i, width = rest.find("->"), 2
    question, answer = (rest[:i], rest[i + width:]) if i >= 0 else (rest, "")
    return question.strip(), answer.strip(), owner


def parse_questions(ledger_md):
    """Ledger section 2 -> {'open': [...], 'answered': [...]}. Open: box `[ ]` or answer `_(open)_`/empty.
    Lines inside code fences or <!-- --> comments are ignored; `unparsed` is true when a fence or comment is
    left open in section 2 (later questions are then unreadable). No section 2 -> both empty."""
    out = {"open": [], "answered": []}
    body, out["unparsed"] = _visible(section(ledger_md, 2) or "")
    for line in body.splitlines():
        if not (m := _LINE.match(line)):
            continue
        question, answer, owner = _parts(m.group(3))
        # a bare `assumed:` (nothing after it) records no assumption, so the question stays open
        done = m.group(1) in "xX" and answer not in ("", _OPEN) and not _EMPTY_ASSUMED.match(answer)
        item = {"id": f"Q{m.group(2)}", "question": question, "owner": owner}
        out["answered" if done else "open"].append({**item, "answer": answer} if done else item)
    first = {}
    for q in out["open"]:
        first.setdefault(q["id"], q)  # duplicate ids are listed once, the first wins
    out["open"] = list(first.values())
    if out["unparsed"]:
        out["open"].append({"id": "?", "question": UNPARSED_NOTE, "owner": "?"})
    return out


MARKER = "[spec-kit-ghp:questions]"
_HEAD = "Open questions for the product owner:"
_ASSUMED_HEAD = "The team assumed these; reply if one is wrong:"
ANSWERED = "All open questions are answered."
MAX_LINE = 500


def _line(q):
    """(`Q<n>: <question>` capped at MAX_LINE chars with a trailing …, was it cut)."""
    text = f"{q['id']}: {q['question']}"
    return (text[:MAX_LINE - 1] + "…", True) if len(text) > MAX_LINE else (text, False)


def _safe(text):
    """Ledger text for a comment or the terminal: unprintable characters as `\\uXXXX`, every `@name` (see `comments._MENTION`; an email address too) as
    `[mention removed]` (no `@` is left), `& < >` escaped."""
    return html.escape(comments._MENTION.sub(comments.NO_MENTION, comments._show(text)), quote=False)


def _assumed(answered):
    """Answered questions whose answer is an `assumed: …` (accepted by the operator without the product owner)."""
    return [q for q in answered if q.get("answer", "").lower().startswith("assumed")]


def comment_text(open_questions, assumed=()):
    """The comment's lines after the marker line: heading, one `Q<n>: <question>` line each (no owners or names), then the questions the team settled by
    assumption with that assumption. '' when there is neither. The unparsed-section note (id '?') is not a question and is left out."""
    qs = [q for q in open_questions if q["id"] != "?"]
    if not qs and not assumed:
        return ""
    parts = ([_HEAD] + [_safe(_line(q)[0]) for q in qs]) if qs else []
    if assumed:
        parts += [_ASSUMED_HEAD] + [_safe(_line({**q, "question": f"{q['question']} → {q['answer']}"})[0]) for q in assumed]
    return "\n".join(parts)


def with_marker(content):
    """The whole comment: the marker and the hash of `content` on the first line, then `content`."""
    return f"{MARKER} {hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]}\n{content}"


def _me(gh):
    """The authenticated numeric user id. Unresolvable -> ValueError: never guess whose comment it is."""
    try:
        return gh.viewer().id
    except GhError as e:
        raise ValueError(f"cannot tell which GitHub comments are yours ({e}); nothing posted")


def _newest_own(gh, ref, me):
    """(the `Comments` read, the newest comment of user id `me` whose text starts with the marker, or None)."""
    found = comments.fetch(gh, ref)
    mine = [c for c in found if c.author.id == me and c.body.startswith(MARKER)]  # ascending id: the last is the newest
    return found, (mine[-1] if mine else None)


def _same(c, body):
    return c is not None and c.body.replace("\r\n", "\n") == body


def plan_post(gh, ref, parsed):
    """Read-only: {'action': none|add|unchanged, 'body', 'comment_id', 'cut', 'me'}. `unchanged` only when the newest own marked comment is exactly the
    planned text. With every question answered and an own marked comment on the issue, the answered note is a new comment (once). ValueError: a text over
    the limit, an identity that cannot be told, or an add while a comment could not be read reliably (it could be our own)."""
    qs = [q for q in parsed["open"] if q["id"] != "?"]
    body = with_marker(comment_text(qs, _assumed(parsed["answered"])) or ANSWERED)
    if len(body) > comments.MAX_COMMENT:
        raise ValueError("the comment would be %d characters, over the limit of %s; nothing posted: answer or shorten some questions"
                         % (len(body), format(comments.MAX_COMMENT, ",")))
    cut, me = [q["id"] for q in qs if _line(q)[1]], _me(gh)
    found, last = _newest_own(gh, ref, me)
    if _same(last, body):
        return {"action": "unchanged", "body": body, "comment_id": last.id, "cut": cut, "me": me}
    action = "add" if qs or last is not None else "none"
    if action == "add" and found.notes:
        raise ValueError("the comments could not be read reliably (%s); nothing posted" % "; ".join(comments._show(n) for n in found.notes[:3]))
    return {"action": action, "body": body if action == "add" else "", "comment_id": None, "cut": cut, "me": me}


def render_plan(ref, plan, unparsed=False):
    verdict = {"none": "no open questions; nothing to post", "unchanged": "unchanged; nothing to post", "add": "ADD a new comment"}
    quote = lambda t: [f"> {comments._show(x)}" for x in t.split("\n")]  # noqa: E731
    lines = [f"# Plan: post open questions to issue #{ref.number}", "", f"Action: {verdict[plan['action']]}", ""]
    if unparsed:
        lines += ["Note: ledger section 2 has an unclosed fence or comment; later questions are not included.", ""]
    if plan["cut"]:
        lines += [f"Note: {', '.join(plan['cut'])} cut to {MAX_LINE} characters (…).", ""]
    if plan["action"] == "add":
        lines += ["Comment text:", ""] + quote(plan["body"]) + [""]
    return "\n".join(lines)


def apply_post(gh, ref, plan):
    """Post exactly the planned comment; (what, comment id or None): `none`/`unchanged` send nothing, `posted`, or `found` (the write failed in a way that
    may have applied it and the re-read found it: nothing is sent again). A definite 4xx is raised as is. Any other failure re-reads the comments; when
    the comment is not there the error is raised with a note: there is no second attempt (run the verb again after looking at the issue)."""
    if plan["action"] != "add":
        return plan["action"], plan["comment_id"]
    try:
        return "posted", comments.add(gh, ref, plan["body"])
    except GhError as e:
        if e.status is not None and 400 <= e.status < 500:
            raise
        _, last = _newest_own(gh, ref, plan["me"])
        if _same(last, plan["body"]):
            return "found", last.id
        # the note comes FIRST: GhError cuts its detail at 400 characters, and the cut must take the end of GitHub's message
        raise GhError(e.code, e.status, f"not found on re-read and not retried: check the issue before running again; {e.detail}") from None


# ---- `ghp questions <n> --sync`: an approver's `/answer Q<n>: text` comment becomes the PO's answer (a local write; T28, ported from ado) ----------
SYNC_RULES = ("has-answer", "posting-lossy", "not-posted", "no-answer", "bad-newer", "older-than-posting", "bad-comment-id")  # why an open question was left alone
MAX_LEDGER_LINE = 2000  # a question line longer than this is malformed (checked before any pattern runs on it)
MAX_ANSWER = 500
E_MALFORMED = ("the ledger's section 2 is malformed (a missing section, an unclosed fence or comment, a repeated or non-ASCII question id, or a question line "
               "over %d characters); nothing written" % MAX_LEDGER_LINE)
E_AGE = "question age cannot be established from the issue's comments"
E_TIME = "the server time cannot be established, so no question can be due under `assume_after_hours`; nothing written"
_CAND = re.compile(r"\s*(?:[-*+]\s+)?\[[ xX]\]\s*Q\d")
_CID = re.compile(r"[1-9][0-9]{0,11}")  # a comment id: ASCII digits only, no leading zero
_ID = re.compile(r"Q[0-9]{1,9}")
_SYNCED = re.compile(r"\(PO comment ([0-9]+)\)$")  # the provenance a sync writes at the end of an answer
_PO_COMMENT = re.compile(r"\bPO\s+comment\b", re.I)
_SEPARATORS = re.compile(r"->|[·|→]")


def check_ledger(md):
    """`parse_questions(md)` for a sync, or ValueError (E_MALFORMED): no section 2, an unclosed fence or comment, a repeated question id, an id that is not
    `Q` + ASCII digits, or a question line over MAX_LEDGER_LINE (checked BEFORE the patterns run on it)."""
    span = section_span(md, 2)
    if span is None:
        raise ValueError(E_MALFORMED)
    cands = [v for v in _visible(md[span[0]:span[1]])[0].split("\n") if _CAND.match(v)]
    if any(len(v) > MAX_LEDGER_LINE for v in cands):
        raise ValueError(E_MALFORMED)
    ids = ["Q%s" % m.group(2) for v in cands if (m := _LINE.match(v))]
    parsed = parse_questions(md)
    if parsed["unparsed"] or len(ids) != len(set(ids)) or not all(_ID.fullmatch(i) for i in ids):
        raise ValueError(E_MALFORMED)
    return parsed


def _fail(why):
    return ValueError("%s (%s); nothing written" % (E_AGE, why))


def own_comments(rows, me):
    """Every `[spec-kit-ghp:questions]` comment of the token identity `me`, oldest first: on GitHub the kit posts a NEW comment for every change (never an
    edit), so these are the "versions" ado read from one comment. ValueError (E_AGE + a fixed reason) when a creation time is unreadable (`date-shape`) or one
    of them was edited (`edited`): the agent's own comment, rewritten, is not what the PO was shown."""
    from . import approvals
    mine = [c for c in rows if c.author.id == me and isinstance(c.body, str) and c.body.startswith(MARKER)]
    if any(approvals._order(c) is None for c in mine):
        raise _fail("date-shape")
    if any(c.edited for c in mine):
        raise _fail("edited")
    return sorted(mine, key=approvals._order)


def posted_open(body):
    """[{"id", "question"}] shown as OPEN in one kit questions comment, in comment order, or None (not a plain kit comment, never an empty list): the
    `ANSWERED` text, or the open-questions heading or the assumed heading after the marker line; in the open part every non-empty line is `Q<n>: text`
    with no repeated id; everything after the assumed heading is ignored."""
    from . import approvals
    lines = approvals.kit_lines(body, MARKER)
    if lines == [MARKER, ANSWERED]:
        return []
    if len(lines) < 2 or lines[1] not in (_HEAD, _ASSUMED_HEAD):
        return None
    out, on = [], False
    for p in lines[1:]:
        if p == _HEAD:
            on = True
        elif p == _ASSUMED_HEAD:
            break
        elif on and p:
            m = re.fullmatch(r"(Q[1-9][0-9]{0,8}): (.+)", p)
            if not m:
                return None
            out.append({"id": m.group(1), "question": m.group(2)})
    return out if len({q["id"] for q in out}) == len(out) else None


def ages(open_questions, rows, me):
    """{Q<n>: datetime (UTC)}: for each open question, the creation time of the first of the unbroken run of the token's own questions comments, ending at
    the newest, that all show its CURRENT line (`_safe(_line(q)[0])`, exactly as posted): a line that was changed and changed back restarts at the change
    back. A question whose line is cut or not in the newest comment has no entry. ValueError (E_AGE) as `own_comments`, or when there is no such comment."""
    from . import approvals
    want = {q["id"]: _safe(_line(q)[0]) for q in open_questions if q["id"] != "?" and not _line(q)[1]}
    if not want:
        return {}
    vs = own_comments(rows, me)
    if not vs:
        raise _fail("no-comment")
    held = [set(approvals.kit_lines(v.body, MARKER)) for v in vs]
    out = {}
    for qid, line in want.items():
        i = len(vs)
        while i > 0 and line in held[i - 1]:
            i -= 1
        if i < len(vs):
            out[qid] = approvals._when(vs[i].created_at)
    return out


def _answer_text(text):
    """An approver's answer as ledger text: the grammar's separators (`→`, `->`, `·`, `|`) as `-`, control characters, `<!--`, `-->` and fences as blanks,
    whitespace collapsed, `PO comment` neutralised (only the sync writes `(PO comment <id>)`), an answer that would read as the team's assumption
    (`assumed...`) prefixed so it never does, cut to MAX_ANSWER. May be empty."""
    from . import decisions
    t = _SEPARATORS.sub("-", text)
    t = " ".join(decisions._MARKUP.sub(" ", decisions._CTRL.sub(" ", t)).split())
    t = _PO_COMMENT.sub("PO-comment", t)
    if t.lower().startswith("assumed"):
        t = "PO answer: " + t
    return t[:MAX_ANSWER].strip()


def sync(md, comment_rows, approvers, excluded, now, assume_after_hours, asked):
    """(the new ledger text, {"answered", "open", "due", "skipped": {id: rule}, "bad_newer": {id: n}, "ignored"}): ado's `sync`, ids and fixed rule names
    only. For every OPEN question whose line has no answer yet, is not cut and is shown in the kit's comment, the newest VALID `/answer Q<n>: text`
    (`approvals.newest`) is taken when it is NEWER than the comment that started to show the line (a tie loses) and no newer approver line is malformed,
    edited or hidden (`bad-newer`). The line becomes
    `- [x] Q<n>: <question> → <answer> (PO comment <id>) · owner: PO`; every other byte is unchanged. `due`: still open and at least
    `assume_after_hours` old by `now` (the server's clock). ValueError (nothing written) for a malformed ledger or an answer that cannot be written."""
    from . import approvals
    comment_rows = approvals.cached(comment_rows)
    parsed = check_ledger(md)
    hours = assume_after_hours
    if hours is not None:
        if type(hours) is not int or not 1 <= hours <= 720:
            raise ValueError("assume_after_hours must be a whole number of hours from 1 to 720; nothing written")
        if not isinstance(now, datetime.datetime) or now.tzinfo is None:
            raise ValueError(E_TIME)
    a, b = section_span(md, 2)
    raws = md[a:b].splitlines(keepends=True)
    lines = _visible(md[a:b])[0].split("\n")
    at = {}
    for n, v in enumerate(lines[:len(raws)]):
        if _CAND.match(v) and (m := _LINE.match(v)):
            at["Q%s" % m.group(2)] = (n, m)
    written, skipped, bad_newer, bad_by, blocked, still = {}, {}, {}, {}, [], []
    for q in sorted(parsed["open"], key=lambda q: int(q["id"][1:])):
        i = q["id"]
        n, m = at[i]
        _, answer, _ = _parts(m.group(3))
        if answer not in ("", _OPEN) and not _EMPTY_ASSUMED.match(answer):
            skipped[i] = "has-answer"
        elif _line(q)[1]:
            skipped[i] = "posting-lossy"
        elif not isinstance(asked.get(i), datetime.datetime) or asked[i].tzinfo is None:
            skipped[i] = "not-posted"
        else:
            tally = {}
            win = approvals.newest(comment_rows, i, approvers, excluded, tally=tally)
            bad_newer[i], bad_by[i] = tally.get("bad_newer", 0), tally.get("bad_newer_by", {})
            key, cid = (approvals._wkey(win) if win else None), str(win["id"]) if win else ""
            if win is None or win["verb"] != "answer":
                skipped[i] = "no-answer"
            elif bad_newer[i]:  # a newer approver line that cannot be read could be a retraction: the wait gate holds it open too (T28d)
                skipped[i] = "bad-newer"
            elif key is None or key <= (asked[i], float("inf")):  # a tie with the posting's time loses
                skipped[i] = "older-than-posting"
            elif not _CID.fullmatch(cid):
                skipped[i] = "bad-comment-id"
            else:
                text = _answer_text(win["text"])
                v, raw = lines[n], raws[n]
                eol = raw[len(raw.splitlines()[0]):]
                new = "%sx%s %s → %s (PO comment %s) · owner: PO" % (v[:m.start(1)], v[m.end(1):m.start(3)], q["question"], text, cid)
                if not text or v != raw.splitlines()[0] or len(new) > MAX_LEDGER_LINE:
                    blocked.append(i)
                else:
                    raws[n] = new + eol
                    written[i] = {"id": i, "question": q["question"], "owner": "PO", "answer": "%s (PO comment %s)" % (text, cid)}
                continue
        still.append(i)
    ignored, lost = [], []
    for q in sorted(parsed["answered"], key=lambda q: int(q["id"][1:])):  # an answer is never replaced, but a newer valid /answer on it is reported
        tally = {}
        win = approvals.newest(comment_rows, q["id"], approvers, excluded, tally=tally)
        named = _SYNCED.search(q["answer"])
        if win is not None and win["verb"] == "answer" and (named is None or named.group(1) != str(win["id"])):
            ignored.append(q["id"])
        elif named is not None and tally.get("bad_newer"):  # a synced answer with a newer malformed, edited or hidden approver line (T28e, as decisions)
            lost.append(q["id"])
    if blocked:
        raise ValueError("an approver's answer cannot be written to the ledger for %s (an inline comment on the line, an empty answer or a line over %d "
                         "characters); fix the line and run it again; nothing written" % (", ".join(blocked), MAX_LEDGER_LINE))
    out = md[:a] + "".join(raws) + md[b:]
    old_lines, new_lines = md.splitlines(keepends=True), out.splitlines(keepends=True)
    after = parse_questions(out)
    by_id = lambda ls: sorted(ls, key=lambda x: int(x["id"][1:]))  # noqa: E731
    if (len(old_lines) != len(new_lines) or sum(x != y for x, y in zip(old_lines, new_lines)) != len(written) or after["unparsed"]
            or after["open"] != [q for q in parsed["open"] if q["id"] not in written]
            or by_id(after["answered"]) != by_id(parsed["answered"] + list(written.values()))):
        raise ValueError("the sync would change more than the answered lines, or they do not read back; nothing written")
    due = []
    if hours is not None:
        limit = datetime.timedelta(hours=hours)
        due = [i for i in still if isinstance(asked.get(i), datetime.datetime) and now - asked[i] >= limit]
    return out, {"answered": list(written), "open": still, "due": due, "skipped": skipped, "bad_newer": bad_newer, "bad_newer_by": bad_by, "ignored": ignored,
                 **({"no_longer_valid": lost} if lost else {})}


def apply_sync(file_path, md, comment_rows, approvers, excluded, now, assume_after_hours, asked):
    """Run `sync` over `md` (the ledger text the caller read and computed `asked` for) and write the result in place (unique temp file + os.replace, only
    when something changed). Refuses (ValueError, nothing written) for a symlink, a file that is not a regular file or is over 1 MiB, a file that is no
    longer exactly `md`, or one that does not read back as written (the old text is then put back). Returns the `sync` report."""
    from pathlib import Path
    from . import decisions
    path = Path(file_path)
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > decisions._MAX_FILE:
            raise ValueError("the ledger must be a regular file of at most 1 MiB, not a symlink; nothing written")
        new, report = sync(md, comment_rows, approvers, excluded, now, assume_after_hours, asked)
        if decisions._read(path) != md:
            raise ValueError("the ledger changed while syncing; nothing written, run it again")
        if new != md:
            decisions._atomic_write(path, new)
            if decisions._read(path) != new:
                decisions._atomic_write(path, md)
                raise ValueError("the ledger did not read back as written; nothing recorded")
    except (OSError, UnicodeError) as e:
        raise ValueError("cannot update the ledger (%s); nothing written" % e.__class__.__name__)
    return report


E_NOT_COMMENT_MODE = "terminal mode (`approval` is not \"comment\" on the target branch): nothing to sync"


def _sync_questions(gh, ref, path, trust):
    """`questions --sync` in comment mode: approvers' `/answer Q<n>: text` comments become the PO's answers in the local ledger (no GitHub write, no gate)
    and the questions that have waited `unattended.assume_after_hours` (by SERVER time) are listed as due. Every refusal (exit 2, fixed text, nothing
    written) comes before anything is changed."""
    from . import approvals
    if not trust.approvers:
        raise ValueError("--sync needs approvers on the target branch (`approvers` in ghp.config.json there); none are configured; nothing written")
    md = gate._read(path, ".", set())
    if md is None:
        raise ValueError("cannot read the ledger (missing, a symlink, outside the working tree, over 1 MiB or not UTF-8); nothing written")
    parsed = check_ledger(md)
    found = comments.fetch(gh, ref)
    if found.notes:  # read before anything else: a dropped comment could be a newer answer or a retraction
        raise ValueError("the issue's comments could not all be read or attributed (dropped at the join), so a newer answer could be missing; nothing written")
    try:
        me = _me(gh)
        skip = approvals.excluded(me, found)
    except ValueError:
        raise ValueError("cannot tell which GitHub identity this token is, so the comments cannot be attributed; nothing written") from None
    hours = trust.unattended.get("assume_after_hours")
    asked = ages(parsed["open"], found, me)
    now = None
    if hours is not None:
        clock = getattr(gh, "server_now", None)  # shortcut: Gh has no server clock yet (UNVERIFIED Date header), so assume_after_hours refuses; T28 report
        now = clock() if callable(clock) else None
        if now is None:
            raise ValueError(E_TIME)
    report = apply_sync(path, md, found, set(trust.approvers), skip, now, hours, asked)
    notice = ["%s: %s: check the thread" % (i, approvals.bad_newer_text(by, "later")) for i, by in report["bad_newer_by"].items() if by]
    notice += ["%s: answered already: a later /answer was not applied; correct the ledger by hand" % i for i in report["ignored"]]
    if report.get("no_longer_valid"):  # the ledger keeps the synced answer; a newer approver line cannot be read (as decisions --sync)
        print("answer-no-longer-valid: %s" % ", ".join(report["no_longer_valid"]), file=sys.stderr)
    print(json.dumps({"id": ref.number, **report, **({"notice": notice} if notice else {})}, indent=2))
    return 0


def register(sub):
    p = sub.add_parser("questions", help="list the ledger's open and answered questions as JSON; --post previews posting the open ones as one issue "
                                         "comment (nothing is sent without --apply)")
    p.add_argument("ref", help="5, #5, owner/repo#5 or an issue URL")
    p.add_argument("--post", action="store_true", help="preview posting the open questions as one comment (add or nothing)")
    p.add_argument("--sync", action="store_true", help="record the approvers' `/answer Q<n>: text` comments as answers in the ledger (comment mode only; in "
                                                       "terminal mode a no-op that says so)")
    p.add_argument("--if-comment-mode", action="store_true", help="with --sync: the same no-op (the workflow's sync step passes it)")
    write_gate.add_write_flags(p)


def run(args, gh):
    if args.apply and not args.post:
        raise ValueError("--apply needs --post")
    if args.if_comment_mode and not args.sync:
        raise ValueError("--if-comment-mode needs --sync")
    if args.sync and (args.post or args.plan_out is not None or args.expect_plan is not None):
        raise ValueError("--sync cannot be combined with --post, --apply, --plan-out or --expect-plan")
    ref = issues._resolve(args.ref)  # config, then the ref: a bad one is refused before any call
    path = dod_file(safe_dir(Config.load().dod_path), ref.number)
    if args.sync:
        trust = approvals.target_trust(".")  # the TARGET branch's mode and approvers, never the working tree's (X4)
        if trust.approval != "comment":
            print(json.dumps({"id": ref.number, "synced": False, "note": E_NOT_COMMENT_MODE}, indent=2))
            return 0
        return _sync_questions(gh, ref, path, trust)
    md = gate._read(path, ".", set())
    if md is None:
        raise ValueError(f"cannot read ledger {path}; run `ghp dod {ref.number}` first")
    parsed = parse_questions(md)
    if not (args.post or args.plan_out):
        print(json.dumps(parsed, indent=2))  # ASCII-escaped (the default): no raw bidi or C1 control character from the ledger reaches the terminal
        return 0
    plan = plan_post(gh, ref, parsed)  # read only
    text = render_plan(ref, plan, parsed["unparsed"])
    write_gate.arm_or_refuse(args, gh, text, approvals.approve_hook(gh, ref, "questions-post", text, plan["action"] == "add"))
    print(text)
    if args.apply:
        what, cid = apply_post(gh, ref, plan)
        print({"posted": f"posted comment {cid}", "found": f"found on re-read: comment {cid} was posted before the error; nothing sent again",
               "unchanged": "nothing sent: unchanged", "none": "nothing sent: no open questions"}[what])
    return 0
