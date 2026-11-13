"""The approvals engine: the command grammar, the newest-valid-command rule, the plan hash and the gate in front of an apply (T15), the comment-mode trust
code (T27: `counts`, `pin`, `recheck`, `still_present`, `approver_is_token`, the bot / minimized / app rules in `guard`) and its wiring (T28: `require` in
comment mode, `check`, the pins, the verb `ghp approvals`).

Ported from ado's `approvals`. Comment mode (`approval: "comment"` on the TARGET branch, `target_trust`; never the working tree, X4):
- `require` (every `--apply` whose plan writes to GitHub, through `plan.arm_or_refuse`'s `approve` hook, before `Gh` is armed): the newest valid approver command over the
  verb's gates (`VERB_GATES`, one target) must be `/approve <gate> <hash>` with `hash = gate_hash(gate, issue, the rendered plan)`, newer than every
  approval request for that hash, with no newer malformed approver line (ado's `_decide`). Then the PINS (below) of that plan must hold. `done` needs a pin.
- `check` (`ghp approvals check <n> --gate <g>`): a review gate by the hash of its artifacts (`artifacts`), a wait gate by what the comments show
  (`wait_state`), `approve-done` by the hash of `.ghp/plan-done.md` (the pin `done` needs); the other apply gates are checked by their own `--apply`.
- Pins (`.ghp/approvals/<n>.json`, written by `check` and `sync` when a gate is met): the approving comment (`pin`) and EVERY approver command comment seen
  for the gate's targets (a malformed one that could be about them included). At apply time, on the SAME fetch: every pinned comment must still be there and
  unchanged (a deleted or edited later `/reject` refuses), `recheck` must pass for the approval, and `newest` must still return the pinned approval's id (a
  NEWER `/reject` refuses). Pins are ADVISORY HARDENING, NOT PROOF: the pin file is in the working tree, so whoever can write it (the agent, a PR that
  commits `.ghp/`) can delete, edit or forge it, which removes the deletion protection exactly as deleting it does (and a forged pin of a live approval
  satisfies `done`). Pins only protect against a deleter who cannot write the working tree. A pin of a gate in the decision's set whose hash is not the
  current plan's REFUSES (it is never silently ignored). Pins outside the tree or signed pins: task T28e.

THE LIMITS OF APPROVAL BY COMMENT (`LIMITS`, shown in `ghp --help` and in the output of `ghp strict mode|check|verify` when the config says approval is
"comment"; docs/trust-and-limits.md): any writer on the repository can edit or delete any comment. An edit leaves the editor and `lastEditedAt` on GitHub,
so the guard SEES it and stops counting the approval: tamper-evident, not tamper-proof. A DELETION leaves nothing `ghp` can see: deleting an approver's later
`/reject` brings an earlier `/approve` back, unless the `/reject` was pinned by a sync or check before it was deleted.

`counts(c, approvers, token_id)` (T27): a comment counts only when ALL hold, checked in this order (the first failure is the reason, a fixed code of `REASONS`):
the author type is exactly `User`; the author id is an exact int (not a bool) and not the token's own id; it is in `approvers` (a set of exact ints); the
comment is not edited (`Comment.edited`); not minimized; not made by a GitHub App (`app is None`); and its first command line is a valid command
(`parse_command`: ASCII only, no invisible or bidi character anywhere in the line). `author_association` is NEVER read (OWNER, MEMBER or COLLABORATOR prove
nothing: the numeric id in `approvers`, read from the target branch, is the only authority). NOTE on the plan's wording: T27 says "`edit_count == 1`";
the live-verified fact (T13) is that a never-edited comment has `userContentEdits.totalCount` 0, so "not edited" is `not c.edited`
(`last_edited_at is None and edit_count == 0`). The verb is the caller's: `counts` says the line is a trustworthy command of an approver, `newest` reads which.

What changed for GitHub (every other rule is ado's, unchanged):
- A comment is a `comments.Comment` with a Markdown `body` and a numeric `author.id`; identities are numbers, never logins. ado's HTML layer (quote
  stripping over tags, `dod._text`) is gone: `comments.first_command_line` (T08) is the one reader of the first line (quote, fence, HTML comment,
  indented code, an invisible character: not a command) and `comments.is_plain_command_line` is the strict ASCII gate in front of the grammar.
  A first line that those refuse but that STILL LOOKS like a command (indented `/approve D1`, `/appr<ZWSP>ove D1`) is a malformed line, counted, never
  a command (it may be a retraction typed wrong).
- The whole first line must be plain ASCII (letters, digits, space, `/ _ . : # , -`), a note included: a note with other punctuation is a malformed
  line. Leading and trailing spaces and tabs of the line are ignored, as in ado; a tab or any non-ASCII blank inside it makes the line malformed.
- A line longer than `MAX_LINE` and a comment longer than `MAX_RAW` are malformed lines (counted), never parsed further: nothing below is superlinear
  anyway, the cap only bounds the work.
- `guard` (the ado rules that need no tracker): `excluded-author`, `not-approver`, `edited` (`Comment.edited`: any sign of an edit, fail closed; ado's
  "identical text in every version" exception has no GitHub counterpart). `on-behalf`, `authorized_as` and the bypass bit (`comment_mode_allowed`) are
  ADO-only and gone.
- A `Comments` that lists dropped comments (`.notes`) is refused (`_load`, `newest`, `recheck`): a dropped comment could be a retraction. `Rows` keeps `.notes`.
- `done` and `activate` are apply gates here (`approve-done`, `approve-activate`, plan files `.ghp/plan-done.md`, `.ghp/plan-activate.md`; ado's
  done and single-item verbs were not comment-approved). The gate ids are ado's twelve plus those two; the `ghp-lifecycle` step ids are the shipped workflow's (`STEP_IDS`).
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

from . import comments
from .config import UNATTENDED_DEFAULTS, ConfigInvalid, Trust
from .issues import Actor

KIT_MARKER = "[spec-kit-ghp:"  # every comment the kit posts starts with one (questions, decisions, ...)
MAX_RAW = 256 * 1024  # characters: a comment longer than this is never read for a command (a malformed line, counted)
MAX_LINE = 2000  # characters: a longer first line is a malformed line (the grammar's longest legal line is under 700)
NOTE_CAP, ANSWER_CAP = 300, 500


class ApprovalRefused(ValueError):
    """A refusal with a fixed text (never GitHub text): the verbs map it to exit 2."""


E_MODE = "E_APPROVAL_MODE: 'approval' must be \"terminal\" or \"comment\"; nothing is approved"
LIMITS = ("Approval by comment is tamper-evident, not tamper-proof: any writer on the repository can edit or delete any comment. An edit leaves the editor "
          "and the last-edited time on GitHub, so ghp can see it and stops counting that approval, but it cannot prevent it. A deletion leaves nothing ghp "
          "can see: if a writer deletes an approver's later /reject, the earlier /approve counts again.")
# every `reason` that `counts` and `recheck` can return; a test pins that nothing else is returned
REASONS = ("ok", "not-a-comment", "bad-token", "bad-approvers", "bot-author", "not-approver", "excluded-author", "edited", "minimized", "app-unknown",
           "app-performed", "not-a-command", "bad-pin", "dropped", "deleted", "changed-since-sync")
E_DROPPED = ("comment approval: {n} comment(s) could not be read or attributed (dropped at the join), and one of them could be a retraction, "
             "so nothing is counted.")


def excluded(me, found, ci_identity=None):
    """Identity ids (ints) that can never count as an approver: the token identity `me` (`questions._me(gh)`; anything but a positive integer raises
    ValueError, never a guess), the author of any comment whose text starts with a `[spec-kit-ghp:` marker (the kit's posting identities) and, when given,
    `ci_identity` (the CI token identity; not a positive integer: ValueError, never skipped silently). `found` is `comments.fetch` output."""
    out = {_id(me, "the token identity")}
    if ci_identity is not None:
        out.add(_id(ci_identity, "the CI token identity"))
    for c in found:
        if isinstance(c.body, str) and c.body.lstrip().startswith(KIT_MARKER):
            out.add(c.author.id)
    return out


def _id(v, what):
    if type(v) is not int or v < 1:
        raise ValueError("%s is not a numeric user id; nothing counted" % what)
    return v


def _ids(values, what):
    """`values` as a set of user ids: anything that is not an exact int (a login, a bool, a string) is ValueError, never dropped (a dropped
    exclusion would let an excluded identity count)."""
    out = set()
    for v in values:
        out.add(_id(v, what))
    return out


# ---- the command grammar -----------------------------------------------------------------------------------------------------------------------

_NONEMPTY = re.compile(r"[^\r\n]+")
_CMD = re.compile(r"/(approve|reject|answer)[ \t]+(D[1-9][0-9]{0,8}|Q[1-9][0-9]{0,8}|[a-z][a-z0-9-]{2,40})(?:[ \t]+([0-9a-f]{64}))?(?:[ \t]*:[ \t]*(.+))?")
_DQ = re.compile(r"[DQ][0-9]+")


def _first_line(body):
    """The first line of `body` that holds more than spaces and tabs, as written; None when there is none. Lines end at \\n, \\r\\n or \\r."""
    for m in _NONEMPTY.finditer(body):
        if m.group().strip(" \t"):
            return m.group()
    return None


def _note(raw):
    """A note or answer as one line: the line is plain ASCII already (no control, markup or mention character), so only blanks are collapsed."""
    return " ".join(raw.split())


_MARKS = " \t*_#`~[(\\-+"  # Markdown emphasis, heading, code, link, escape and bullet characters an approver may wrap a command in
_DIGITS = "0123456789"


def _unmark(s):
    """`s` without leading Markdown markers: emphasis, heading, code, strike, link and escape characters, bullets and one `1.` / `1)` list number. `>` is NOT
    one (a quote is not the approver's own line). Each step is one `lstrip` or one scan: linear."""
    s = s.lstrip(_MARKS)
    i = 0
    while i < len(s) and s[i] in _DIGITS:
        i += 1
    return s[i + 1:].lstrip(_MARKS) if 0 < i < len(s) and s[i] in ".)" else s


def _looks_like_command(line):
    """Does the line start like a command, however disguised: any case, full-width forms, invisible characters before it, Markdown formatting around it
    (`**/reject D3**`, `# /reject D3`, a code span, a list item: a visible retraction must be counted, ado read the same rendered text), `/approved`,
    `/approve-all`? A quote (`>`) does not count."""
    head = "".join(c for c in line[:200] if unicodedata.category(c) != "Cf")
    return _unmark(unicodedata.normalize("NFKC", head)).casefold().startswith(("/approve", "/reject", "/answer"))


def _parse(body):
    """(command or None, bad): `bad` is None, or for a first line that looks like a command (or a comment or line too long to read) but breaks the
    grammar, the word it names as its target, as typed ("" when there is none): the caller decides whether that line is about its target."""
    if not isinstance(body, str):
        return None, None
    if len(body) > MAX_RAW:
        return None, ""
    raw = _first_line(body)
    if raw is None:
        return None, None
    line = raw.strip(" \t")
    if len(line) > MAX_LINE:
        return None, ""
    if all(unicodedata.category(c) in ("Cf", "Zs", "Zl", "Zp", "Cc") for c in line):
        return None, ""  # a first line that only LOOKS empty (zero-width characters): fail closed, but counted
    if not _looks_like_command(line):
        return None, None
    words = _unmark(line).split(None, 2)
    named = words[1].split(":")[0] if len(words) > 1 else ""
    if comments.first_command_line(raw) is None or not comments.is_plain_command_line(line):
        return None, named  # indented code or an invisible character, or not plain ASCII: it looks like a command, so it is counted
    m = _CMD.fullmatch(line)
    if not m:
        return None, named
    verb, target, hash_, note = m.groups()
    if verb == "answer":
        ok = target[0] == "Q" and hash_ is None and note is not None
    else:
        ok = (target[0] == "D" and hash_ is None) if _DQ.fullmatch(target) else hash_ is not None
    if not ok:
        return None, named
    text = "" if note is None else _note(note)[:ANSWER_CAP if verb == "answer" else NOTE_CAP]  # capped, never refused: a long reject must still be a reject
    if note is not None and not text:
        return None, named
    return {"verb": verb, "target": target, "hash": hash_, "text": text}, None


class Rows(list):
    """A list of comments that remembers what `_parse` made of each one (`parsed`: id(row) -> (row, body, result)), so every comment is parsed ONCE per run
    however many targets `newest` is asked about. Per run by construction (the cache lives on the list a caller got from one fetch, never in the module); a row whose
    `body` is no longer the same string is parsed again. `cached` wraps a plain list, and a filtered copy shares the cache. `.notes` (what `comments.fetch`
    dropped) is kept from the list it wraps, or given."""

    def __init__(self, rows=(), parsed=None, notes=None):
        super().__init__(rows)
        self.parsed = {} if parsed is None else parsed
        self.notes = list(getattr(rows, "notes", ()) if notes is None else notes)


def cached(rows):
    """`rows` as `Rows` (the same object when it already is one)."""
    return rows if isinstance(rows, Rows) else Rows(rows)


def _parsed(rows, c):
    """`_parse(c.body)`, once per row and body string for the `Rows` it comes from."""
    body = c.body
    hit = getattr(rows, "parsed", {}).get(id(c))
    if hit is not None and hit[0] is c and hit[1] is body:
        return hit[2]
    res = _parse(body)
    if isinstance(rows, Rows):
        rows.parsed[id(c)] = (c, body, res)
    return res


def parse_command(body):
    """The command in a comment's Markdown body, or None: `{"verb", "target", "hash", "text"}`. ONLY the first non-empty line counts (not a quote, fence,
    HTML comment, indented code or a line with an invisible character), it must be plain ASCII and it must be the whole command:
    `/approve|/reject D<n>[: note]`, `/answer Q<n>: text`, `/approve|/reject <gate-id> <64 lowercase hex>[: note]` (a gate needs the hash, a D or Q target
    must not carry one). A note is cut to 300 characters, an answer to 500 (capped, not refused: a long `/reject` must stay a reject). No error is ever
    raised for hostile input."""
    return _parse(body)[0]


# ---- the kit's own comments ------------------------------------------------------------------------------------------------------------------

def marked(c, marker):
    """Does comment `c` claim to be a kit comment with `marker` (its first non-blank line, invisible characters dropped, NFKC, any case, quote and Markdown
    markers stripped, starts with it)? A disguised one counts: the callers then require it to be a plain kit posting (`kit_lines`) or refuse."""
    body = c.body if isinstance(c, comments.Comment) and isinstance(c.body, str) else ""
    line = _first_line(body[:MAX_RAW]) or ""
    head = "".join(ch for ch in line[:400] if unicodedata.category(ch) != "Cf")
    return unicodedata.normalize("NFKC", head).lstrip(" \t>*_#`~(\\-+").casefold().startswith(marker.casefold())  # `[` starts the marker: kept


def kit_lines(body, marker):
    """The lines of a kit comment as the kit writes it (`<marker> <16 hex of sha256(content)>\\n<content>`, `questions.with_marker` and
    `decisions.with_marker`): `[marker, *content lines]`; `[]` for anything else (fail closed): another first line, a hash that is not the content's, a body
    over MAX_RAW, or any control, invisible or bidi character in it. Linear."""
    if not isinstance(body, str) or len(body) > MAX_RAW:
        return []
    head, nl, content = body.replace("\r\n", "\n").partition("\n")
    if not nl or head != "%s %s" % (marker, hashlib.sha256(content.encode("utf-8", "surrogatepass")).hexdigest()[:16]):
        return []
    if any(ch != "\n" and (comments.is_invisible(ch) or unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Zl", "Zp")) for ch in content):
        return []
    return [marker] + content.split("\n")


# ---- guards ----------------------------------------------------------------------------------------------------------------------------------

def _when(s):
    """An ISO-8601 UTC timestamp (`...T..:..:..[.f]Z`, 1-7 fraction digits) as a datetime, else None. Compared as times: 'ss.5Z' is later than 'ss.25Z'."""
    m = re.fullmatch(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,7}))?Z", s) if isinstance(s, str) else None
    if not m:
        return None
    try:
        y, mo, d, h, mi, sec = (int(x) for x in m.groups()[:6])
        return datetime.datetime(y, mo, d, h, mi, sec, int((m.group(7) or "0").ljust(6, "0")[:6]), tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def guard(c, approvers, excluded):
    """Why comment `c` cannot count as an approver's, or None when it can: `bot-author` (author type is not exactly `User`), `not-approver` (the author id
    is not an exact int, or not in `approvers`), `excluded-author` (the token identity, a kit posting identity, the CI identity, or `viewer_did_author` is not False), `edited` (`Comment.edited`:
    any sign of an edit), `minimized`, `app-unknown` (the REST comment had no `performed_via_github_app` key), `app-performed` (made through a GitHub App). `approvers` and `excluded` are sets of ints. `author_association` is never
    read. `counts` adds the command check and the argument checks."""
    if not isinstance(c.author, Actor) or c.author.type != "User":
        return "bot-author"
    if type(c.author.id) is not int:  # True == 1 and 1.0 == 1 in a set: only an exact int is an identity
        return "not-approver"
    if c.author.id in excluded or c.viewer_did_author is not False:  # GitHub's own "the token wrote this": a wrong token_id cannot let the agent approve
        return "excluded-author"
    if c.author.id not in approvers:
        return "not-approver"
    if c.edited:
        return "edited"
    if c.minimized is not False:  # fail closed: anything but a real False
        return "minimized"
    if c.app is comments.APP_UNKNOWN:  # the REST comment did not say: not "made by a person"
        return "app-unknown"
    return "app-performed" if c.app is not None else None


def counts(c, approvers, token_id):
    """`(True, "ok")` when comment `c` counts as an approver's command, else `(False, reason)` with `reason` one of `REASONS` (see the module docs for the
    rules and their order). Never raises: a wrong argument (`approvers` not a set of exact ints, `token_id` not a positive exact int, `c` not a `Comment`)
    is a refusal too."""
    if not isinstance(c, comments.Comment):
        return False, "not-a-comment"
    if type(token_id) is not int or token_id < 1:
        return False, "bad-token"
    if not isinstance(approvers, (set, frozenset)) or any(type(a) is not int or a < 1 for a in approvers):
        return False, "bad-approvers"
    why = guard(c, approvers, {token_id})
    if why is not None:
        return False, why
    return (True, "ok") if _parse(c.body)[0] is not None else (False, "not-a-command")


def approver_is_token(approvers, token_id):
    """True when an approver IS the token identity (the doctor check of T29 fails then: the agent's own comments would be the approver's). Exact ints only;
    a `token_id` that is not a positive int cannot be shown to differ, so it is True too (fail closed)."""
    return type(token_id) is not int or token_id < 1 or any(type(a) is int and a == token_id for a in approvers)


def pin(c):
    """What `sync` stores about an approving comment: its id, author id, the sha256 of its body and its `last_edited_at`. `recheck` compares at done time."""
    return {"id": c.id, "author": c.author.id, "body_sha256": hashlib.sha256(c.body.encode("utf-8", "surrogatepass")).hexdigest(),
            "last_edited_at": c.last_edited_at}


def _hits(pinned, found):
    return [c for c in found if isinstance(c, comments.Comment) and c.id == pinned["id"]]


def still_present(pinned, found):
    """Is the pinned comment still in the freshly fetched list `found`? (A deleted approval is gone: it is no approval.)"""
    return bool(_hits(pinned, found))


def recheck(pinned, found, approvers, token_id):
    """The done-time test of an approval pinned at sync (`pin`): `(True, "ok")` only when the comment is still in `found` (a fresh `comments.fetch`), is the
    same text by the same author, has the same `last_edited_at` (an edit by anyone since, a writer rewriting an approver's comment included, is
    `changed-since-sync`), and still passes `counts`. Gone: `deleted`. A malformed pin: `bad-pin`. A `found` that dropped comments (`.notes` not empty; a
    dropped comment could be a retraction): `dropped`.
    NECESSARY, NOT SUFFICIENT: it looks only at the pinned id. T28 must also run `newest` on the SAME fetch and require it to return the pinned id (a newer
    `/reject` is invisible here), and must pin every approver command id seen at sync and refuse at done when one is missing (a deleted `/reject`)."""
    if not (isinstance(pinned, dict) and type(pinned.get("id")) is int and type(pinned.get("author")) is int
            and isinstance(pinned.get("body_sha256"), str) and "last_edited_at" in pinned):
        return False, "bad-pin"
    if getattr(found, "notes", None):
        return False, "dropped"
    hits = _hits(pinned, found)
    if not hits:
        return False, "deleted"
    c = hits[0]
    if len(hits) > 1 or pin(c) != {k: pinned[k] for k in ("id", "author", "body_sha256", "last_edited_at")}:
        return False, "changed-since-sync"
    return counts(c, approvers, token_id)


_WELL_FORMED = re.compile(r"[DQ][1-9][0-9]*|[a-z][a-z0-9-]{2,40}")


def _elsewhere(word, target):
    """Is the target word of a malformed line (up to a colon) a DIFFERENT well-formed target? Anything else (punctuation: `D3.`, `(D3)`, `D3,`; `D03`;
    full-width digits; a typo; nothing) counts as possibly about `target`: a retraction typed wrong must be reported, never assumed to be elsewhere."""
    w = word.split(":")[0]
    return w != target and _WELL_FORMED.fullmatch(w) is not None


def _order(c):
    """Sort key (created time, id) of a comment, or None when its time cannot be read."""
    when = _when(c.created_at)
    return None if when is None or type(c.id) is not int else (when, c.id)


def newest(found, target, approvers, excluded, tally=None):
    """The newest VALID command for `target`, by (created time, id), as `{"verb", "target", "hash", "text", "id", "created", "author", "bad_newer"}`,
    else None. Valid = the grammar holds (`parse_command`) and `guard` returns None. An invalid comment is skipped, so a later invalid comment (the
    agent's, an edited or quoted one) never cancels an earlier valid one, while a later VALID `/reject` is the newest and does. Its `hash` is for
    the caller to compare with the gate's current hash. `author` is the numeric user id.
    A MALFORMED command line by an approver (author in `approvers`, not excluded) that is newer than the winner and does not name a
    different well-formed target (`_elsewhere`: `/reject D3.` and `/reject (D3)` count for D3) cannot be told from a retraction typed wrong, so it is counted in `bad_newer` (and in `tally`, also when there is no
    winner): the caller must show that count (fixed text, a number only) next to the verdict, never ignore it. So does ANY comment by an approver (not excluded)
    newer than the winner that is edited or minimized, whatever it says now (T28 review I2: on GitHub a writer can edit or hide an approver's later
    `/reject`, which would otherwise bring the earlier `/approve` back).
    `tally` (a dict) is filled with counts only: `bad` (command-looking lines that break the grammar, any author and target), `bad_newer`, `date`
    (an unreadable creation time) and one count per guard rule; no comment text is ever kept or printed.
    ApprovalRefused when `found` is a `Comments` / `Rows` that dropped comments (`.notes`): one of them could be a retraction. ValueError when
    `approvers` or `excluded` holds anything but numeric ids."""
    tally = {} if tally is None else tally
    count = lambda k, n=1: tally.__setitem__(k, tally.get(k, 0) + n)  # noqa: E731
    if getattr(found, "notes", None):
        raise ApprovalRefused(E_DROPPED.format(n=len(found.notes)))
    wanted, skip = _ids(approvers, "an approver"), _ids(excluded, "an excluded identity")
    rows, bads, hidden = [], [], []  # (order key, reason code) each
    for c in found:
        cmd, bad = _parsed(found, c) if isinstance(c, comments.Comment) else (None, None)
        if bad is not None:
            count("bad")
            if type(c.author.id) is int and c.author.id in wanted and c.author.id not in skip and not _elsewhere(bad, target):
                bads.append((_order(c), "malformed"))
        if (bad is None and isinstance(c, comments.Comment) and isinstance(c.author, Actor) and type(c.author.id) is int and c.author.id in wanted
                and c.author.id not in skip and (c.edited or c.minimized is not False)):
            hidden.append((_order(c), "edited" if c.edited else "minimized"))  # someone edited or hid it: it could have been a later retraction (fail closed)
        if cmd is None or cmd["target"] != target:
            continue
        key = _order(c)
        if key is None:
            count("date")
            continue
        rows.append((key, cmd, c))
    out = None
    for key, cmd, c in sorted(rows, key=lambda t: t[0], reverse=True):
        why = guard(c, wanted, skip)
        if why is None:
            out = (key, {**cmd, "id": c.id, "created": c.created_at, "author": c.author.id})
            break
        count(why)
    newer = [why for k, why in bads + hidden if k is None or out is None or k > out[0]]
    if newer:
        count("bad_newer", len(newer))
        _add_by(tally.setdefault("bad_newer_by", {}), {why: newer.count(why) for why in newer})
    return None if out is None else {**out[1], "bad_newer": len(newer)}


def _add_by(acc, by):
    """Add the reason counts `by` ({code: n}) into `acc`; returns `acc`."""
    for k, n in by.items():
        acc[k] = acc.get(k, 0) + n
    return acc


# ---- the gate in front of an apply ------------------------------------------------------------------------------------------------------------

VERB_GATES = {"questions-post": {"approve-questions", "approve-plan-questions", "approve-answers-questions"}, "push": {"approve-push", "approve-sync"},
              "decisions-post": {"approve-decisions"}, "pr-create": {"approve-pr"}, "branch-link": {"branch-link"},
              "done": {"approve-done"}, "activate": {"approve-activate"}}  # the ONLY applies a comment can authorise (`done`, `activate`: ghp's, see
# the module docs; `branch-link` is ado's and has no ghp verb: kept so the gate ids stay ado's, T43)
_REQUEST = re.compile(r"\[spec-kit-ghp:approval-request\][ \t]+([a-z][a-z0-9-]{2,40})[ \t]+([0-9a-f]{64})")  # the kit's request comment (any author: fail closed)
E_MISSING = ("comment approval: no valid approval for this plan on issue #{n}. A trusted approver must comment `/approve <gate> <hash>` (first line, "
             "unedited, newer than any approval request) with one of: {gates}.{bad}")
E_UNREADABLE = ("comment approval: the issue's comments are unreadable or incomplete (dropped at the join, a page limit or an unexpected answer), so "
                "nothing is approved.")
E_NO_APPROVERS = "comment approval: no approvers are configured on the target branch (`approvers` in ghp.config.json there), so nothing can be approved."
E_IDENTITY = "comment approval: the token identity cannot be established, so the comments cannot be attributed and nothing is approved."
E_NO_TARGET = ("the working tree's ghp.config.json sets approvers, approval or unattended, but there is no target branch to read them from "
               "(no origin/HEAD); nothing is approved")
E_TRUST_BASE_UNRESOLVED = ("E_TRUST_BASE_UNRESOLVED: in CI the target branch must be readable to know who may approve, but there is no origin/HEAD "
                           "(actions/checkout creates none); fetch the default branch and set it (`git fetch origin <default>` and "
                           "`git remote set-head origin <default>`); nothing is approved")
E_BASE_UNKNOWN = ("comment approval: the --base given is not a branch here (not fetched?), so its approvers cannot be read; fetch it "
                  "(`git fetch origin <base>`) and run it again; nothing is approved")
E_APPROVERS_DIFFER = ("comment approval: the default branch and the PR's target branch name different approvers, so it is unclear who may approve; "
                      "make `approvers` the same on both")
E_VERB = "comment approval: this verb cannot be approved by a comment; nothing is approved"
E_NOT_PINNED = ("comment approval: no approval of this plan was pinned for issue #{n}, so the story is not closed. Preview with "
                "`ghp done {n} --plan-out .ghp/plan-done.md`; once a trusted approver's `/approve approve-done <hash>` comment for that plan exists, "
                "`ghp approvals check {n} --gate approve-done` pins it.")
E_PIN_FILE = ("comment approval: the pin file .ghp/approvals/{n}.json cannot be read (a symlink, outside the working tree, over 1 MiB, not UTF-8 or not "
              "the shape ghp writes), so nothing is approved.")
E_PIN_GONE = ("comment approval: a comment pinned when the approval was read is gone or changed since (a deleted or edited /reject could bring an "
              "earlier /approve back), so nothing is approved. Run the sync again after checking the thread.")
E_PIN_GONE_REPIN = ("comment approval: a comment pinned by this step's earlier pin is gone or changed since (a deleted or edited /reject could bring an "
                    "earlier /approve back), so it is not pinned again and nothing is approved. Check the thread; only if the change is known to be harmless, "
                    "delete the pin file .ghp/approvals/{n}.json knowingly and run it again.")
E_PIN_STALE = ("comment approval: the pinned approval no longer holds ({why}), so nothing is approved.")
E_PIN_OTHER = ("comment approval: a pin of this step is for another plan (another hash), so the comments read at that sync are not this plan's; "
               "nothing is approved. Run `ghp approvals sync <run>` again for this plan.")
E_PIN_NEWER = ("comment approval: a newer command than the pinned approval exists for this plan (a newer /reject or approval), so nothing is approved; "
               "run the sync again.")


class ApprovalMissing(ApprovalRefused):
    """The apply has no valid approver comment for this plan (or cannot tell): exit 2, nothing written. Fixed text, never GitHub text."""


def gate_hash(gate_id, number, data):
    """What an approver's `/approve <gate> <hash>` names: sha256 over the gate id, the issue number and the plan bytes (64 lowercase hex)."""
    return hashlib.sha256(b"spec-kit-ghp/1\n" + gate_id.encode("utf-8") + b"\n" + str(number).encode("utf-8") + b"\n" + data).hexdigest()


def _config_in_use(repo):
    return next((p for p in (Path(os.environ.get("GHP_CONFIG") or "\0"), Path(repo) / "ghp.config.json") if p.is_file()), None)


def target_trust(repo=".", base=None):
    """The `Trust` every comment-mode decision uses: the target branch's ghp.config.json (`config.trusted`), never the working tree's (X4). The target is
    `origin/HEAD` and, when given, `base` (the PR's target; ApprovalRefused E_BASE_UNKNOWN when it is not a commit here while origin/HEAD is): comment mode on either wins, and
    two comment-mode targets must name the same approvers (ApprovalRefused otherwise). With no target at all: in CI (`GITHUB_ACTIONS` or `CI` is `true` or
    `1`, any case) ApprovalRefused E_TRUST_BASE_UNRESOLVED; else the defaults (terminal) when the working-tree config sets no trust key, else ConfigInvalid."""
    from . import config, strict
    home = strict.default_base(repo)
    other = strict.base_ref(repo, base) if base else ""
    if other and not strict._resolve_base(repo, other):
        if home:
            raise ApprovalRefused(E_BASE_UNKNOWN)  # never dropped silently beside origin/HEAD: comment mode on the PR's target could be the one that counts
        other = ""  # no target at all: the CI refusal or the documented local fallback below
    refs = [r for r in (home, other) if r]
    if not refs:
        if any(os.environ.get(v, "").strip().lower() in ("true", "1") for v in ("GITHUB_ACTIONS", "CI")):  # not secrets; no token is ever read
            raise ApprovalRefused(E_TRUST_BASE_UNRESOLVED)
        used = _config_in_use(repo)
        if used is not None and config._has_trust_keys(used):
            raise ConfigInvalid(E_NO_TARGET)
        return Trust([], "terminal", dict(UNATTENDED_DEFAULTS))
    trusts = [config.trusted(r, repo) for r in dict.fromkeys(refs)]
    comment = [t for t in trusts if t.approval == "comment"]
    if len(comment) == 2 and set(comment[0].approvers) != set(comment[1].approvers):
        raise ApprovalRefused(E_APPROVERS_DIFFER)
    return comment[0] if comment else trusts[0]


def _wkey(w):
    """The (created, id) order key of a `newest` result."""
    when = _when(w["created"])
    return None if when is None else (when, w["id"])


def _requests(found, hashes):
    """Creation keys of every approval-request comment for any (gate, hash) of the set; None in the list = a time that cannot be read."""
    return [_order(c) for c in found if isinstance(c.body, str) and (m := _REQUEST.match(c.body)) and hashes.get(m.group(1)) == m.group(2)]


def _load(gh, trust, ref, ci_identity=None):
    """(every comment of the issue as `Rows`, the excluded identities, the token id) after the approvers are known to be set; ApprovalMissing with a fixed
    text when the comments dropped any (`.notes`, read before anything else) or the token identity is unknown. A GitHub error propagates (exit 1)."""
    from . import questions
    if not trust.approvers:
        raise ApprovalMissing(E_NO_APPROVERS)
    found = comments.fetch(gh, ref)
    if found.notes:
        raise ApprovalMissing(E_UNREADABLE)
    try:
        me = questions._me(gh)
        return Rows(found), excluded(me, found, ci_identity), me
    except ValueError:
        raise ApprovalMissing(E_IDENTITY) from None


def _decide(gh, trust, ref, make_hashes, ci_identity=None):
    """ado's `_decide` over one fetch: `{"verdict", "bad", "hashes", "found", "skip", "me", "stale", "best"}`. `make_hashes()` gives {gate: hash}. These gates
    are ONE target: the newest valid command over all of them (a command with any other hash still counts, so a later reject or another plan's approval
    cancels) must be an `/approve` or `/reject` with that gate's hash, newer than every approval request for the set, with no newer malformed approver line:
    then `verdict` is "approve" or "reject", else None (`bad`: the count to show; `stale`: an `/approve` of this hash exists but a request is newer)."""
    hashes = make_hashes()
    found, skip, me = _load(gh, trust, ref, ci_identity)
    approvers = set(trust.approvers)

    not_counted = {}  # guard reason -> how many commands for these gates it set aside (first scan only): said in the refusal

    def scan(rows):
        wins, bad, by = {}, 0, {}
        for g in hashes:
            tally = {}
            wins[g] = newest(rows, g, approvers, skip, tally=tally)
            if tally.get("bad_newer", 0) > bad:
                bad, by = tally["bad_newer"], tally["bad_newer_by"]
            if rows is found:
                _add_by(not_counted, {k: tally[k] for k in GUARD_WORDS if tally.get(k)})
        return {g: w for g, w in wins.items() if w}, bad, by

    wins, bad, by = scan(found)
    g, best = max(wins.items(), key=lambda kv: _wkey(kv[1]), default=(None, None))
    out = {"verdict": None, "bad": bad, "bad_by": by, "hashes": hashes, "found": found, "skip": skip, "me": me, "stale": False, "best": best,
           "not_counted": not_counted}
    if best and best["hash"] == hashes[g]:
        reqs, cut = _requests(found, hashes), _wkey(best)
        fresh = None not in reqs and all(r < cut for r in reqs)
        out["stale"] = best["verb"] == "approve" and not fresh
        if fresh:
            # a malformed approver line anywhere in the set newer than the winner: only the comments newer than it are read again
            _, out["bad"], out["bad_by"] = scan(Rows([c for c in found if (k := _order(c)) is None or k > cut], found.parsed))
            if not out["bad"]:
                out["verdict"] = best["verb"]
    return out


BAD_NEWER_WORDS = {"malformed": "malformed command line(s) by an approver",  # T31c: one fixed wording per reason code of `bad_newer_by`
                   "edited": "approver comment(s) edited after posting",
                   "minimized": "approver comment(s) hidden (minimized)"}


def bad_newer_text(by, rel="newer"):
    """The fixed text for the reason counts `by` ({code: n}, codes of BAD_NEWER_WORDS): numbers and fixed words only, "" when there is none."""
    return "; ".join("%d %s %s" % (by[k], rel, w) for k, w in BAD_NEWER_WORDS.items() if by.get(k))


def _bad(by, what="for this plan"):
    text = bad_newer_text(by)
    return " %s %s: check the thread." % (text, what) if text else ""


GUARD_WORDS = {"bot-author": "by a bot or app account", "not-approver": "by an account that is not an approver",  # one fixed wording per `guard` code
               "excluded-author": ("by an excluded account (the signed-in or CI account, or an account that posted a comment starting with "
                                   "`[spec-kit-ghp:` on this issue, until that comment is deleted)"),
               "edited": "edited after posting", "minimized": "hidden (minimized)", "app-unknown": "with no record of whether an app made it",
               "app-performed": "made through a GitHub App"}


def _not_counted(by):
    """Why commands for these gates were set aside (`guard` counts {code: n}): numbers and fixed words only, "" when none was."""
    if not by:
        return ""
    return " %d /approve or /reject comment(s) for this plan's gates did not count: %s." % (
        sum(by.values()), "; ".join("%d %s" % (by[k], w) for k, w in GUARD_WORDS.items() if by.get(k)))


def _approve_for(gh, trust, ref, make_hashes, message, ci_identity=None):
    d = _decide(gh, trust, ref, make_hashes, ci_identity)
    if d["verdict"] != "approve":
        raise ApprovalMissing(message(d["hashes"], d["bad_by"]) + _not_counted(d["not_counted"]))
    return d


def require(gh, trust, ref, verb, plan_bytes, ci_identity=None, repo="."):
    """The gate in front of a GitHub write of `verb` (a key of `VERB_GATES`); called after the `--expect-plan` check, before the write is armed
    (`plan.arm_or_refuse`'s `approve` hook). `trust` is `target_trust()`, never the working tree's config. Terminal mode is a no-op: a person answers the
    gate in the terminal. Comment mode: the verb's gates are ONE target for this plan (`_decide`; each gate's hash is `gate_hash(g, number, plan_bytes)`),
    then the pins of this plan must hold (`_pins_hold`; `done` needs one). Any other mode is `E_APPROVAL_MODE`. Every refusal is ApprovalRefused /
    ApprovalMissing with a fixed text; reads only (GET and GraphQL queries); a GitHub error propagates."""
    if trust.approval == "terminal":
        return None
    if trust.approval != "comment":
        raise ApprovalRefused(E_MODE)
    if verb not in VERB_GATES:
        raise ApprovalRefused(E_VERB)
    n = ref.number
    d = _approve_for(gh, trust, ref, lambda: {g: gate_hash(g, n, plan_bytes) for g in sorted(VERB_GATES[verb])},
                     lambda hashes, bad: E_MISSING.format(n=int(n), gates=", ".join("%s %s" % gh_ for gh_ in hashes.items()), bad=_bad(bad)), ci_identity)
    _pins_hold(d, read_pins(repo, n), set(trust.approvers), required=verb == "done", number=n)
    return d


# ---- pins: what a sync or a check saw, compared at apply time on the same fetch as the decision ----------------------------------------------------

PIN_DIR = (".ghp", "approvals")
_HEX64 = re.compile(r"[0-9a-f]{64}")


def pin_path(repo, number):
    return os.path.join(str(repo), *PIN_DIR, "%d.json" % int(number))


def _pin_ok(p):
    return (isinstance(p, dict) and set(p) == {"id", "author", "body_sha256", "last_edited_at"} and type(p["id"]) is int and type(p["author"]) is int
            and isinstance(p["body_sha256"], str) and _HEX64.fullmatch(p["body_sha256"]) is not None and (p["last_edited_at"] is None or isinstance(p["last_edited_at"], str)))


def read_pins(repo, number):
    """{gate: {"hash", "approval": pin, "seen": [pin]}} of the pin file, {} when there is none; ApprovalMissing (E_PIN_FILE) for anything else."""
    from . import gate
    path = pin_path(repo, number)
    if not os.path.lexists(path):
        return {}
    text = gate._read(path, str(repo), set())
    try:
        data = json.loads(text) if text is not None else None
    except (ValueError, RecursionError):
        data = None
    gates = data.get("gates") if isinstance(data, dict) and type(data.get("version")) is int and data["version"] == 1 and type(data.get("issue")) is int and data["issue"] == int(number) >= 1 else None
    if not (isinstance(gates, dict) and all(isinstance(g, str) and isinstance(e, dict) and set(e) == {"hash", "approval", "seen"}
                                             and isinstance(e["hash"], str) and _HEX64.fullmatch(e["hash"]) and _pin_ok(e["approval"])
                                             and isinstance(e["seen"], list) and all(_pin_ok(x) for x in e["seen"]) for g, e in gates.items())):
        raise ApprovalMissing(E_PIN_FILE.format(n=int(number)))
    return gates


def _seen(d, approvers):
    """`pin` of EVERY approver command comment (not excluded) whose target is one of the decision's gates, or a malformed line that could be about one."""
    out = []
    for c in d["found"]:
        a = c.author.id if isinstance(c, comments.Comment) and isinstance(c.author, Actor) else None
        if type(a) is not int or a not in approvers or a in d["skip"]:
            continue
        cmd, bad = _parsed(d["found"], c)
        if (cmd is not None and cmd["target"] in d["hashes"]) or (bad is not None and not all(_elsewhere(bad, g) for g in d["hashes"])):
            out.append(pin(c))
    return out


def record_pins(repo, number, gate_id, d, approvers):
    """Write the pin of an approved decision `d` (`_decide` with verdict "approve") for `gate_id`: the approving comment and every approver command seen
    (`_seen`). Pins of the decision's other gates (the same verb set) are dropped; other gates' pins are kept. A local write (unique temp file + rename)."""
    from . import decisions
    found = {c.id: c for c in d["found"]}
    gates = {g: e for g, e in read_pins(repo, number).items() if g not in d["hashes"]}  # the verb's other gates: this sync replaces them
    gates[gate_id] = {"hash": d["hashes"][gate_id], "approval": pin(found[d["best"]["id"]]), "seen": _seen(d, approvers)}
    path = pin_path(repo, number)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    decisions._atomic_write(Path(path), json.dumps({"version": 1, "issue": int(number), "gates": gates}, indent=2, sort_keys=True) + "\n")
    return gates[gate_id]


def _seen_hold(entry, d):
    """Is every comment `entry` pinned still in the decision's fetch, exactly once and unchanged (`pin`)?"""
    now = {}
    for c in d["found"]:
        now.setdefault(c.id, []).append(c)
    return all(len(hits := now.get(p["id"], [])) == 1 and pin(hits[0]) == p for p in entry["seen"])


def _replaced_pins_hold(d, pins, number, message=E_PIN_GONE):
    """Before a pin of the decision's gates is replaced (any text, the same included), every comment it pinned must still be there and unchanged: a
    re-pin never forgives a deletion (T28e). ApprovalMissing(`message`) otherwise."""
    if not all(_seen_hold(e, d) for g, e in pins.items() if g in d["hashes"]):
        raise ApprovalMissing(message.format(n=int(number)))


def _pins_hold(d, pins, approvers, required=False, number=0):
    """The pins of THIS plan (an entry whose hash is the gate's current hash) on the decision's own fetch: every seen comment still there and unchanged
    (E_PIN_GONE), the approval passes `recheck` (E_PIN_STALE) and is still the newest command (`newest`'s winner, E_PIN_NEWER). No pin of this plan:
    fine, unless `required` (E_NOT_PINNED)."""
    if any(g in d["hashes"] and d["hashes"][g] != e["hash"] for g, e in pins.items()):
        raise ApprovalMissing(E_PIN_OTHER)  # a pin of this step for another plan: never ignored silently (the sync replaces it)
    entries = [e for g, e in pins.items() if d["hashes"].get(g) == e["hash"]]
    if not entries:
        if required:
            raise ApprovalMissing(E_NOT_PINNED.format(n=int(number)))
        return
    for e in entries:
        if not _seen_hold(e, d):
            raise ApprovalMissing(E_PIN_GONE)
        ok, why = recheck(e["approval"], d["found"], approvers, d["me"])
        if not ok:
            raise ApprovalMissing(E_PIN_STALE.format(why=why))
        if d["best"] is None or d["best"]["id"] != e["approval"]["id"]:
            raise ApprovalMissing(E_PIN_NEWER)


def approve_hook(gh, ref, verb, text, writes=True, repo=".", base=None):
    """The `approve` argument of `plan.arm_or_refuse` for a verb's `--apply`: None when the plan writes nothing, else a function that reads the target
    branch's trust and runs `require` over the rendered plan `text` (UTF-8)."""
    if not writes:
        return None
    return lambda: require(gh, target_trust(repo, base), ref, verb, text.encode("utf-8"), repo=repo)


# ---- `ghp approvals check` : the review gates by hash, the wait gates by what the comments show ---------------------------------------------------

GATES = ("review-spec", "approve-questions", "approve-plan-questions", "wait-po-answers", "approve-answers-questions", "review-plan", "approve-push",
         "approve-decisions", "approve-sync", "review-review", "approve-pr", "wait-po-validation", "approve-done",
         "approve-activate")  # ado's 12 and ghp's done and activate gates
REVIEW_GATES = ("review-spec", "review-plan", "review-review")  # approved by hash over their artifacts
WAIT_GATES = ("wait-po-answers", "wait-po-validation")  # met by what the comments show, no hash
E_GATE = "unknown gate: --gate must be one of the gates of the ghp-lifecycle workflow (docs/trust-and-limits.md)"
E_APPLY_GATE = "gate {gate} is not checked here: the apply it guards (`--apply`) requires the approver comment itself"
E_ARTIFACT = ("comment approval: an artifact this gate shows cannot be read (missing, a symlink, outside the working tree, over 1 MiB, not UTF-8, or not in "
              "the form the gate needs), so there is nothing to approve")
E_REVIEW = ("comment approval: no valid approval for gate {gate} on issue #{n}. A trusted approver must comment `/approve {gate} {hash}` (first line, "
            "unedited, newer than any approval request).{bad}")
_REVIEW_H, _FIDELITY_H = re.compile(r"^### Review\b"), re.compile(r"^### AC fidelity\b")


def _block(sec, heading):
    """The authored text of the newest live (not `(superseded)`) block of ledger section `sec` that starts with `heading`, up to the next `#`..`###` heading;
    fences and comments hide headings. None when there is none or the section cannot be told apart from its visible form."""
    from . import dod
    raw, vis = sec.splitlines(keepends=True), dod._visible(sec)[0].split("\n")
    if len(raw) != len(vis):
        return None
    live = [i for i, line in enumerate(vis) if heading.match(line) and not line.rstrip().endswith("(superseded)")]
    if not live:
        return None
    end = next((j for j in range(live[-1] + 1, len(vis)) if re.match(r"#{1,3} ", vis[j])), len(vis))
    return "".join(raw[live[-1]:end])


def _head(repo):
    """HEAD's full commit id (40 lowercase hex); ValueError(E_ARTIFACT) when git cannot say."""
    from . import prs
    try:
        head = prs._text(prs._git(repo, "rev-parse", "--verify", "HEAD^{commit}"), "HEAD").strip()
    except prs._Refuse:
        raise ValueError(E_ARTIFACT) from None
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError(E_ARTIFACT)
    return head


def artifacts(gate_id, number, cfg, repo="."):
    """The bytes a review gate shows its approver, for `gate_hash`: UTF-8 text of the listed artifacts, each framed as `<label> <byte count>\\n<bytes>\\n`
    (so no content can pass for a frame): `review-spec` = spec.md + ledger section 2; `review-plan` = plan.md + spec.md + ledger section 2; `review-review` =
    the newest live `### Review` block of ledger section 4, then (strict mode only) the newest live `### AC fidelity` block (an empty frame when there is
    none), then HEAD's full commit id. spec.md and plan.md sit in the ledger's `Spec dir`. Every file is read confined to `repo` (`gate._read`). ValueError
    (E_ARTIFACT) for anything missing or unreadable (an ambiguous ledger too)."""
    from . import dod, gate
    if gate_id not in REVIEW_GATES:
        raise ValueError(E_ARTIFACT)
    ledger = gate._read(os.path.join(str(repo), str(dod.dod_file(str(cfg.dod_path), int(number)))), str(repo), set())
    try:
        if ledger is None or dod.unclosed(ledger):
            raise ValueError(E_ARTIFACT)
        parts = []

        def spec_file(name):
            spec_dir = dod.ledger_spec_dir(ledger)
            text = gate._read(os.path.join(str(repo), spec_dir, name), str(repo), set()) if spec_dir else None
            if text is None:
                raise ValueError(E_ARTIFACT)
            parts.append((name, text))

        def ledger_part(label, text):
            if text is None:
                raise ValueError(E_ARTIFACT)
            parts.append((label, text))

        if gate_id == "review-plan":
            spec_file("plan.md")
        if gate_id != "review-review":
            spec_file("spec.md")
            ledger_part("ledger-2", dod.section(ledger, 2))
        else:
            sec4 = dod.section(ledger, 4)
            ledger_part("review", _block(sec4, _REVIEW_H) if sec4 is not None else None)
            if cfg.mode == "strict":
                parts.append(("fidelity", _block(sec4, _FIDELITY_H) or ""))
            parts.append(("head", _head(repo)))
    except dod.LedgerError:
        raise ValueError(E_ARTIFACT) from None
    return b"".join(("%s %d\n" % (label, len(b))).encode() + b + b"\n" for label, b in ((label, text.encode("utf-8")) for label, text in parts))


def check(gh, trust, ref, gate_id, cfg, repo=".", ci_identity=None):
    """`ghp approvals check`: a dict `{"gate", "mode", "met": True, ...}` when the gate is met, else ApprovalRefused / ApprovalMissing (fixed text; nothing
    from a comment). An unknown gate is refused first. Terminal mode is a no-op that reads nothing from GitHub. Comment mode: a review gate needs the newest
    valid approver command over the gate to be `/approve <gate> <hash of artifacts()>` (the shared `_approve_for`, so a request, a newer reject and newer
    malformed lines count as for the applies), and is then pinned (`record_pins`); `approve-done` the same over its plan file (`_view`, as `sync`), since
    `done --apply` requires that pin; a wait gate needs `wait_state` met; the other apply gates are checked by their own `--apply` (`require`) and are
    refused here."""
    if gate_id not in GATES:
        raise ApprovalRefused(E_GATE)
    if trust.approval != "comment":
        if trust.approval != "terminal":
            raise ApprovalRefused(E_MODE)
        return {"gate": gate_id, "mode": "terminal", "met": True, "note": "terminal mode: a person answers this gate in the terminal; nothing was read"}
    if gate_id in WAIT_GATES:
        return wait_check(gh, trust, ref, gate_id, cfg, repo, ci_identity)
    if gate_id not in REVIEW_GATES and gate_id != "approve-done":  # `done --apply` needs a pin of approve-done; no shipped run step pauses there
        raise ApprovalRefused(E_APPLY_GATE.format(gate=gate_id))
    n = ref.number
    d = _approve_for(gh, trust, ref, lambda: _view(gate_id, n, cfg, repo),  # approve-done: the hash of .ghp/plan-done.md, exactly as sync reads it
                     lambda hashes, bad: E_REVIEW.format(gate=gate_id, n=int(n), hash=hashes[gate_id], bad=_bad(bad)), ci_identity)
    pins = read_pins(repo, n)
    _replaced_pins_hold(d, pins, n)  # its pin of OLDER text is replaced below, but never forgives a deleted comment (T28e)
    pins = {g: e for g, e in pins.items() if g != gate_id or e["hash"] == d["hashes"][gate_id]}
    _pins_hold(d, pins, set(trust.approvers), number=n)  # a pin of the same text must still hold before it is replaced
    record_pins(repo, n, gate_id, d, set(trust.approvers))
    return {"gate": gate_id, "mode": "comment", "met": True, "pinned": d["best"]["id"]}


E_NOT_WAIT = "gate {gate} is not a wait gate: it is checked by `ghp approvals check` as a review or an apply gate"
E_WAIT = "comment approval: gate {gate} on issue #{n} is not met: {k} open ({ids}).{bad}"
E_CUT_OPEN = " {ids}: the question was last shown cut at 500 characters, so no answer closes it; shorten it in the ledger and post it again (`ghp questions <n> --post`)."
E_QUESTIONS = ("comment approval: the issue's questions comments cannot be trusted (one carries the questions marker but is not the token identity's own "
               "plain, unedited kit comment), so nothing is met.")
E_DECISIONS = ("comment approval: a decisions comment on the issue cannot be trusted (edited, disguised, cut, not a plain kit comment, or none of them is "
               "the token identity's own), so nothing is met.")
E_DECISIONS_FILE = ("comment approval: the issue's decisions file exists but cannot be read (a symlink, over 1 MiB, outside the repository or not UTF-8), "
                    "so which text each decision has cannot be told; nothing is met.")
E_AGE_UNKNOWN = "comment approval: how long the questions have been shown cannot be established from the issue's comments, so nothing is met."
E_NO_TIME = "comment approval: the server time cannot be established, so no question can be due under `assume_after_hours`; nothing is met."


def _server_now(gh):
    clock = getattr(gh, "server_now", None)  # shortcut: Gh has no server clock yet, so a due question is never assumed (fail closed); T28 report
    now = clock() if callable(clock) else None
    if now is None:
        raise ApprovalMissing(E_NO_TIME)
    return now


def _wait_answers(gh, trust, found, skip, me):
    """(open question ids, newer-line count, its reason counts, ids held open for being cut) of `wait-po-answers` from the comments only. Every comment that carries the
    questions marker (`marked`, so a disguised one counts) must be one of the token identity's own plain, unedited kit comments (`questions.own_comments`,
    `kit_lines`): else E_QUESTIONS. Those comments are the versions: a question OPEN in any of them stays open until the newest valid approver
    `/answer Q<n>` newer than the comment that started to show it, with no newer malformed approver line, or until it is due (`assume_after_hours`)."""
    from . import questions
    found = cached(found)
    hits = [c for c in found if marked(c, questions.MARKER)]
    if not hits:
        return [], 0, {}, []
    try:
        vs = questions.own_comments(found, me)
    except ValueError:
        raise ApprovalMissing(E_QUESTIONS) from None
    held = [questions.posted_open(v.body) for v in vs]
    if sorted(c.id for c in hits) != sorted(v.id for v in vs) or None in held:
        raise ApprovalMissing(E_QUESTIONS)
    lines = [set(kit_lines(v.body, questions.MARKER)) for v in vs]
    first, cut_last = {}, {}
    for i, h in enumerate(held):
        for q in h:
            first.setdefault(q["id"], i)
            cut_last[q["id"]] = len("%s: %s" % (q["id"], q["question"])) >= questions.MAX_LINE and q["question"].endswith("…")
    now_q = {q["id"]: "%s: %s" % (q["id"], q["question"]) for q in held[-1]}
    hours = trust.unattended.get("assume_after_hours")
    hours = hours if type(hours) is int and 1 <= hours <= 720 else None
    now, still, bad_total, by, cut_open = None, [], 0, {}, []
    for i in sorted(first, key=lambda q: int(q[1:])):
        if i in now_q:
            k = len(vs)
            while k > 0 and now_q[i] in lines[k - 1]:
                k -= 1
            when = _when(vs[k].created_at) if k < len(vs) else None
        else:
            when = _when(vs[first[i]].created_at)
        if when is None:
            raise ApprovalMissing(E_AGE_UNKNOWN)
        tally = {}
        win = newest(found, i, set(trust.approvers), skip, tally=tally)
        key = _wkey(win) if win else None
        if cut_last.get(i):  # its LAST showing was cut: an approver cannot answer text they cannot see in full, and the assume window does not close it
            still.append(i)
            cut_open.append(i)
            bad_total += tally.get("bad_newer", 0)
            _add_by(by, tally.get("bad_newer_by", {}))
            continue
        if win and win["verb"] == "answer" and not win["bad_newer"] and key and key > (when, float("inf")):
            continue  # answered; a tie with the posting loses
        if hours:
            now = now or _server_now(gh)
            if now - when >= datetime.timedelta(hours=hours):
                continue  # due
        still.append(i)
        bad_total += tally.get("bad_newer", 0)
        _add_by(by, tally.get("bad_newer_by", {}))
    return still, bad_total, by, cut_open


def decisions_text(cfg, repo, number):
    """The issue's decisions file for the wait gate (confined as the gate reads the ledger), or None when there is none; ApprovalMissing(E_DECISIONS_FILE)
    when it exists and cannot be read."""
    from . import decisions, gate
    path = decisions.decisions_file(os.path.join(str(repo), str(cfg.dod_path)), number)
    if not os.path.lexists(path):
        return None
    md = gate._read(path, str(repo), set())
    if md is None:
        raise ApprovalMissing(E_DECISIONS_FILE)
    return md


def _wait_validation(trust, found, skip, me, decisions_md=None):
    """(open decision ids, newer-line count, its reason counts) of `wait-po-validation` from the comments. Every comment that carries the decisions marker must be a plain,
    unedited kit posting with a readable time that shows the heading and only `D<n> (kind): ...` lines (E_DECISIONS otherwise), and at least one must be
    the token identity's. Every `D<n>` they list needs the newest valid `/approve|/reject D<n>` newer than the newest posting that shows it, with no newer
    malformed approver line; a decision whose newest posting is cut stays open. `decisions_md` (the working tree's file) can only keep an id OPEN."""
    from . import decisions
    found = cached(found)
    hits = [c for c in found if marked(c, decisions.MARKER)]
    if not hits:
        return [], 0, {}
    if not any(c.author.id == me for c in hits):
        raise ApprovalMissing(E_DECISIONS)
    posts, cut_at = {}, {}
    for c in hits:
        lines = kit_lines(c.body, decisions.MARKER)
        ids = [(decisions.POSTED_LINE.match(ln), ln) for ln in lines if ln not in (decisions.MARKER, decisions._HEAD)]
        if not lines or _order(c) is None or c.edited or decisions._HEAD not in lines or any(m is None for m, _ in ids):
            raise ApprovalMissing(E_DECISIONS)
        for m, ln in ids:
            i = "D%d" % int(m.group(1))
            if i not in posts or _order(c) > _order(posts[i]):
                posts[i] = c
                cut_at[i] = len(ln) >= decisions.MAX_COMMENT_LINE and ln.endswith("…")
    still, bad_total, by = [], 0, {}
    for i in sorted(posts, key=lambda d: int(d[1:])):
        tally = {}
        win = newest(found, i, set(trust.approvers), skip, tally=tally)
        key, pkey = (_wkey(win) if win else None), _order(posts[i])
        if cut_at[i] or not (win and win["verb"] in ("approve", "reject") and not win["bad_newer"] and key and key > pkey):
            still.append(i)
            bad_total += tally.get("bad_newer", 0)
            _add_by(by, tally.get("bad_newer_by", {}))
    if decisions_md is not None:
        parsed, index = decisions.parse_decisions(decisions_md), decisions.posting_index(found)
        for d in (d for k in ("open", "validated", "rejected") for d in parsed[k]):
            if d["id"] in posts and d["id"] not in still and decisions.superseded(d, index):
                still.append(d["id"])
    return sorted(still, key=lambda d: int(d[1:])), bad_total, by


def wait_state(gh, trust, ref, gate_id, ci_identity=None, decisions_md=None):
    """`{"met", "open", "bad_newer"[, "cut"]}` of a wait gate from the issue's comments; the ledger and the decisions file never MEET a gate (an agent can
    edit them). `decisions_md`: the text, or a zero-argument function called only after the comments were read. Fixed-text refusals only."""
    if gate_id not in WAIT_GATES:
        raise ApprovalRefused(E_NOT_WAIT.format(gate=gate_id) if gate_id in GATES else E_GATE)
    found, skip, me = _load(gh, trust, ref, ci_identity)
    cut = []
    if gate_id == "wait-po-answers":
        still, bad, by, cut = _wait_answers(gh, trust, found, skip, me)
    else:
        still, bad, by = _wait_validation(trust, found, skip, me, decisions_md() if callable(decisions_md) else decisions_md)
    return {"met": not still, "open": still, "bad_newer": bad, **({"bad_newer_by": by} if by else {}), **({"cut": cut} if cut else {})}


def wait_check(gh, trust, ref, gate_id, cfg, repo=".", ci_identity=None):
    st = wait_state(gh, trust, ref, gate_id, ci_identity, lambda: decisions_text(cfg, repo, ref.number))
    if not st["met"]:
        raise ApprovalMissing(E_WAIT.format(gate=gate_id, n=int(ref.number), k=len(st["open"]), ids=", ".join(st["open"]), bad=_bad(st.get("bad_newer_by", {}), "on these"))
                              + (E_CUT_OPEN.format(ids=", ".join(st["cut"])) if st.get("cut") else ""))
    return {"gate": gate_id, "mode": "comment", "met": True, "open": []}


# ---- the poller: `ghp approvals sync RUN_ID [--resume]` (ported from ado; the approval-request comment is NOT ported: T28 ruling) -------------------

APPLY_FILES = {"approve-questions": ".ghp/plan-questions.md", "approve-plan-questions": ".ghp/plan-questions-after-plan.md",
               "approve-answers-questions": ".ghp/plan-questions-after-answers.md", "approve-push": ".ghp/plan-push.md", "approve-decisions": ".ghp/plan-decisions.md",
               "approve-sync": ".ghp/plan-sync.md", "approve-pr": ".ghp/plan-pr.md", "approve-done": ".ghp/plan-done.md",
               "approve-activate": ".ghp/plan-activate.md"}  # the --plan-out of each apply gate (the shipped workflow's show_file, tests/test_workflow.py)
GATE_VERB = {g: v for v, gs in VERB_GATES.items() for g in gs if g in APPLY_FILES}
RUN_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]*")  # Spec Kit's own pattern (engine.py _RUN_ID_PATTERN)
RUN_STATUSES = ("created", "running", "paused", "completed", "failed", "aborted")
RUN_WORKFLOW = "ghp-lifecycle"
RUNS_DIR = (".specify", "workflows", "runs")
RUN_INPUT = "issue_number"  # the workflow input that holds the issue number (ado: `story_id`)
STEP_IDS = ("base-check", "pull", "stack-check", "review-spec", "check-review-spec", "questions-loop", "questions-preview", "approve-questions",
            "questions-apply", "questions-applied", "plan", "plan-questions-loop", "plan-questions-preview", "approve-plan-questions", "plan-questions-apply",
            "plan-questions-applied", "wait-po-answers", "check-wait-po-answers", "sync-po-answers", "plan-with-answers", "answers-questions-loop",
            "answers-questions-preview", "approve-answers-questions", "answers-questions-apply", "answers-questions-applied", "review-plan",
            "check-review-plan", "tasks", "push-loop", "push-preview", "approve-push", "push-apply", "push-applied", "strict-check-implement", "implement",
            "decisions-loop", "decisions-preview", "approve-decisions", "decisions-apply", "decisions-applied", "strict-verify", "sync-loop", "sync-preview",
            "approve-sync", "sync-apply", "sync-applied", "gate", "gate-check", "refresh-context", "review", "review-review", "check-review-review",
            "pr-clear", "pr-prepare", "pr-loop", "pr-preview", "approve-pr", "pr-apply", "pr-applied", "wait-po-validation", "sync-po-validation",
            "check-wait-po-validation")  # the shipped workflows/ghp-lifecycle step ids, in run order (a built-in list, never the installed file, which the
# agent could edit); tests/test_workflow.py pins it to the shipped file
LOOP_STEPS = {loop: (loop[:-5] + "-preview", "approve-" + loop[:-5], loop[:-5] + "-apply") for loop in STEP_IDS if loop.endswith("-loop")}  # each one-pass
# loop's own steps (`<name>-loop` holds `<name>-preview`, `approve-<name>`, `<name>-apply`; tests/test_workflow.py pins it to the shipped file)
_LOOP_N = re.compile(r"[0-9]{1,6}")  # nested loop steps are `<loop>:<step>:<n>`, ASCII digits only
MAX_RUN_ID = 128
RESUME_TIMEOUT = 6 * 3600  # seconds: a resume runs the workflow up to its next pause, agent steps included
_resume_run = subprocess.run  # the one subprocess call of the poller (tests replace it)
E_SYNC_TERMINAL = ("approvals sync works in comment mode only (`approval: \"comment\"` on the target branch): in terminal mode a person answers the gates "
                   "in a terminal; nothing was read or resumed")
E_RUN_ID = "the run id is not a Spec Kit run id (ASCII letters, digits, `-` and `_`, starting with a letter or digit, at most 128 characters)"
E_RUN = ("the run's state.json cannot be read or is not a run of the ghp-lifecycle workflow (missing, a symlink, over 1 MiB, not JSON, another workflow, or an "
         "unknown status, step or issue number), so nothing is done")
E_SYNC_GATE = "the run is paused at a step that is not one of the gates of the ghp-lifecycle workflow, so it is not resumed here"
E_SYNC_IDENTITY = ("the poller's token identity cannot be established, so the comments cannot be attributed and nothing is resumed (the poller must run with the "
                   "token identity that posted the kit's comments)")
E_RESUME = "`specify workflow resume` failed (exit status {code}); see `specify workflow status`"
E_RESUME_TIMEOUT = "`specify workflow resume` did not finish within the poller's time limit and was stopped; see `specify workflow status`"
E_RESUME_TOOLS = "`ghp` or `git` was not found on a safe PATH (absolute entries outside the project), so the resumed steps would fail; the run is not resumed"
E_RESUME_MISSING = ("the `specify` command was not found on a safe PATH (absolute entries outside the project; an empty or relative entry and one inside the "
                    "project are ignored) or could not be started")
E_NOTE_STALE = ("an approval of this exact text exists, but an approval request is newer than it, so it no longer counts: the approver must approve again")
E_NO_REQUESTS = "approval requests are not posted by this ghp version: the approver copies the `/approve <gate> <hash>` line from `ghp approvals check` or the plan"


class ResumeFailed(Exception):
    """`specify workflow resume` failed, timed out or could not start: exit 1 with a fixed text (the run's own output is never echoed)."""


def valid_run_id(run_id):
    return isinstance(run_id, str) and len(run_id) <= MAX_RUN_ID and RUN_ID.fullmatch(run_id) is not None


def _step_ok(step, ids=None):
    """A top-level step id, or a nested `<loop>:<step>:<n>` whose step is one of THAT loop's (`LOOP_STEPS`); each id must be one of `ids` (default: the
    shipped `STEP_IDS`)."""
    ids = STEP_IDS if ids is None else ids
    if not isinstance(step, str):
        return False
    parts = step.split(":")
    if len(parts) == 1:
        return step in ids
    return (len(parts) == 3 and parts[0] in ids and parts[1] in ids and parts[1] in LOOP_STEPS.get(parts[0], ())
            and _LOOP_N.fullmatch(parts[2]) is not None)


def _run_when(text):
    if not isinstance(text, str) or len(text) > 40:
        return None
    try:
        when = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo is not None else None


def read_run(repo, run_id):
    """`{"run_id", "status", "step", "number"}` of one run, from `<repo>/.specify/workflows/runs/<run id>/state.json`, which is UNTRUSTED (the agent can
    edit it): the run id is checked by Spec Kit's pattern BEFORE any path is built (ValueError E_RUN_ID), the file is read confined (no symlink, a plain
    directory under the runs directory, 1 MiB, UTF-8), the workflow id and the run id in it must match, the status must be a known one, the step one of the
    workflow's (`_step_ok`), the issue number (`inputs.issue_number`, `RUN_INPUT`; ado's `inputs.story_id`) an int and `updated_at` a time. Nothing else is read.
    ValueError (E_RUN) for anything else."""
    from . import gate, refs
    if not valid_run_id(run_id):
        raise ValueError(E_RUN_ID)
    base = os.path.realpath(str(repo))
    runs = os.path.join(base, *RUNS_DIR)
    path = os.path.join(runs, run_id)
    if os.path.realpath(runs) != runs or os.path.realpath(path) != path:  # .specify, workflows, runs or the run directory is a link
        raise ValueError(E_RUN)
    text = gate._read(os.path.join(path, "state.json"), base, set())
    try:
        data = json.loads(text) if text is not None else None
    except (ValueError, RecursionError):
        data = None
    inp = data.get("inputs") if isinstance(data, dict) else None
    n = inp.get(RUN_INPUT) if isinstance(inp, dict) else None
    state, step = (data.get("status"), data.get("current_step_id")) if isinstance(data, dict) else (None, None)
    if not (isinstance(data, dict) and data.get("workflow_id") == RUN_WORKFLOW and data.get("run_id") == run_id and type(n) is int
            and 1 <= n <= refs._MAX and isinstance(state, str) and state in RUN_STATUSES and _run_when(data.get("updated_at")) is not None
            and (_step_ok(step) or (step is None and state == "created"))):
        raise ValueError(E_RUN)
    return {"run_id": run_id, "status": state, "step": step or "", "number": n}


def _paused_gate(step):
    """The gate a paused run waits at (`<gate>`, or the middle of `<loop>:<gate>:<n>`), which must be one of `GATES`: checked before any request."""
    gate_id = step.split(":")[-2] if ":" in step else step
    if gate_id not in GATES:
        raise ValueError(E_SYNC_GATE)
    return gate_id


def _input(gate_id):
    return "verdict_" + gate_id.replace("-", "_")  # the workflow's input names are the underscore forms (tests/test_workflow.py)


def resume_argv(run_id, gate_id, verdict):
    """The argument list of the resume: the verdict for this gate, and every OTHER gate's verdict input reset to empty (a stale verdict would pass a later
    gate). One `--input` per pair. ValueError for a run id or a gate outside the fixed lists, or a verdict that is not approve or reject."""
    if not valid_run_id(run_id):
        raise ValueError(E_RUN_ID)
    if gate_id not in GATES or verdict not in ("approve", "reject"):
        raise ValueError(E_SYNC_GATE)
    pairs = ["%s=%s" % (_input(gate_id), verdict)] + ["%s=" % _input(o) for o in GATES if o != gate_id]
    return ["specify", "workflow", "resume", run_id] + [x for pair in pairs for x in ("--input", pair)]


def _plan_text(gate_id, repo):
    """The plan TEXT of an apply gate: its fixed plan file, read confined, without the `⚠` first line `plan.gate_preview` adds to a long plan (removed only
    when re-rendering the rest gives exactly the file). This is the text the apply hashes. ValueError (E_ARTIFACT) when it cannot be read."""
    from . import gate, plan
    rel = APPLY_FILES[gate_id]
    raw = gate._read(os.path.join(str(repo), rel), str(repo), set())
    if raw is None:
        raise ValueError(E_ARTIFACT)
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    first, nl, rest = text.partition("\n")
    return rest if nl and first.startswith("⚠") and plan.gate_preview(rest, rel) == text else text


def _view(gate_id, number, cfg, repo):
    """{gate: hash} of a review or apply gate's target (the verb's gates for an apply gate, as `require` builds them)."""
    if gate_id in REVIEW_GATES:
        return {gate_id: gate_hash(gate_id, number, artifacts(gate_id, number, cfg, repo))}
    data = _plan_text(gate_id, repo).encode("utf-8")
    return {g: gate_hash(g, number, data) for g in sorted(VERB_GATES[GATE_VERB[gate_id]])}


def _safe_path(repo):
    """PATH for the resume: absolute entries only, none inside the project or the current directory (symlinks resolved, `os.path.samestat`)."""
    tops = [os.stat(t) for t in {os.path.realpath(str(repo)), os.path.realpath(os.getcwd())} if os.path.isdir(t)]
    return os.pathsep.join(d for d in os.environ.get("PATH", "").split(os.pathsep) if d and os.path.isabs(d) and not _inside(os.path.realpath(d), tops))


def _inside(path, tops):
    while True:
        try:
            st = os.stat(path)
            if any(os.path.samestat(st, t) for t in tops):
                return True
        except OSError:
            pass  # a directory that does not exist holds nothing; its ancestors are still looked at
        parent = os.path.dirname(path)
        if parent == path:
            return False
        path = parent


def _resume(repo, run_id, gate_id, verdict):
    """Runs the resume: an argument list, no shell, no stdin, the run's output thrown away, a time limit, `specify` on a sanitized PATH (refused when it is
    not there; `ghp` and `git` must be on it too); the child gets that PATH and this environment without git's repository selectors (`config.git_env`)."""
    from . import config
    argv = resume_argv(run_id, gate_id, verdict)
    path = _safe_path(repo)
    exe = shutil.which("specify", path=path)
    if not exe:
        raise ResumeFailed(E_RESUME_MISSING)
    if not all(shutil.which(tool, path=path) for tool in ("ghp", "git")):
        raise ResumeFailed(E_RESUME_TOOLS)
    try:
        r = _resume_run([exe] + argv[1:], cwd=str(repo), env={**config.git_env(), "PATH": path}, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, timeout=RESUME_TIMEOUT, shell=False, check=False)
    except subprocess.TimeoutExpired:
        raise ResumeFailed(E_RESUME_TIMEOUT) from None
    except (OSError, subprocess.SubprocessError):
        raise ResumeFailed(E_RESUME_MISSING) from None
    if r.returncode != 0:
        raise ResumeFailed(E_RESUME.format(code=int(r.returncode)))


def sync(gh, trust, run_id, cfg, repo=".", resume=False, dry_run=False):
    """`ghp approvals sync RUN_ID`: `{"gate", "verdict": "approve"|"reject"|"waiting"}` (plus `bad_newer`, `note`, `pinned`, `resumed`) for the gate a run
    is paused at; `{"gate": None, "verdict": "waiting", "paused": False}` for a run that is not paused. Comment mode only. Order: the run id, state.json
    (untrusted, `read_run`), the gate id against `GATES`, all before any request; then the poller's own token identity, passed to `excluded` as the CI
    identity so it can NEVER count as an approver; then the verdict: a review or apply gate by hash (`_decide`), a wait gate by `wait_state` (met =
    approve). An approved review or apply gate is pinned (`record_pins`: the approving comment and every approver command seen). With `resume` and a
    verdict the run is read again (still paused at the very same step) and resumed. `dry_run` pins and resumes nothing. Fixed texts only."""
    from . import questions
    if trust.approval != "comment":
        raise ApprovalRefused(E_SYNC_TERMINAL)
    run = read_run(repo, run_id)
    if run["status"] != "paused":
        return {"gate": None, "verdict": "waiting", "paused": False}
    gate_id, n = _paused_gate(run["step"]), run["number"]
    from .refs import Ref
    ref = Ref(cfg.owner, cfg.repo, n)
    try:
        me = questions._me(gh)
    except ValueError:
        raise ApprovalMissing(E_SYNC_IDENTITY) from None
    out = {"gate": gate_id}
    if gate_id in WAIT_GATES:
        st = wait_state(gh, trust, ref, gate_id, ci_identity=me, decisions_md=lambda: decisions_text(cfg, repo, n))
        verdict, bad, by = ("approve" if st["met"] else "waiting"), st["bad_newer"], st.get("bad_newer_by", {})
    else:
        d = _decide(gh, trust, ref, lambda: _view(gate_id, n, cfg, repo), me)
        verdict, bad, by = d["verdict"] or "waiting", d["bad"], d["bad_by"]
        if d["stale"] and verdict == "waiting":
            out["note"] = E_NOTE_STALE
        elif verdict == "waiting" and trust.unattended.get("approval_requests") is True:
            out["note"] = E_NO_REQUESTS
        if verdict == "approve":  # the old pin, at any text, must still hold its comments before this approval counts (T28e)
            _replaced_pins_hold(d, read_pins(repo, n), n, E_PIN_GONE_REPIN)
        if verdict == "approve" and not dry_run:
            pinned_gate = gate_id if gate_id in d["hashes"] else next(g for g in d["hashes"] if g == d["best"]["target"])
            out["pinned"] = record_pins(repo, n, pinned_gate, d, set(trust.approvers))["approval"]["id"]
    out.update({"verdict": verdict, **({"bad_newer": bad, "bad_newer_by": by} if bad else {})})
    if resume:
        again = read_run(repo, run_id)
        go = verdict != "waiting" and not dry_run and again["status"] == "paused" and again["step"] == run["step"]
        if go:
            _resume(repo, run_id, gate_id, verdict)
        out["resumed"] = go
    return out


# ---- the verb ---------------------------------------------------------------------------------------------------------------------------------

def register(sub):
    p = sub.add_parser("approvals", help="comment mode: check that a gate is approved by an approver's comment (`check`), or sync a paused Spec Kit run (`sync`)")
    s = p.add_subparsers(dest="action", metavar="ACTION", required=True)
    c = s.add_parser("check", help="exit 0 when the gate is met, 2 (fixed text) until a valid `/approve <gate> <hash>` for the current artifacts exists; "
                                   "a no-op in terminal mode")
    c.add_argument("ref", help="5, #5, owner/repo#5 or an issue URL")
    c.add_argument("--gate", required=True, help="one of: " + ", ".join(GATES))
    y = s.add_parser("sync", help="the poller: read a paused Spec Kit run's state, record the approver's verdict for the gate it waits at (pins it) and, "
                                  "with --resume, resume the run; never counts its own comments")
    y.add_argument("run_id", help="the Spec Kit run id")
    y.add_argument("--resume", action="store_true", help="resume the run with the verdict (specify workflow resume)")
    y.add_argument("--dry-run", action="store_true", help="pin and resume nothing")


def run(args, gh):
    from . import issues
    from .config import Config
    if args.action == "check":
        if args.gate not in GATES:
            raise ApprovalRefused(E_GATE)
        ref = issues._resolve(args.ref)  # config, then the ref: a bad one is refused before any call
        trust = target_trust(".")
        out = check(gh, trust, ref, args.gate, Config.load(), ".")
        print(json.dumps(out, indent=2))
        return 0
    if not valid_run_id(args.run_id):
        raise ValueError(E_RUN_ID)
    trust = target_trust(".")
    if trust.approval != "comment":
        raise ApprovalRefused(E_SYNC_TERMINAL)
    try:
        out = sync(gh, trust, args.run_id, Config.load(), ".", resume=args.resume, dry_run=args.dry_run)
    except ResumeFailed as e:
        print("error: " + str(e), file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2))
    return 0
