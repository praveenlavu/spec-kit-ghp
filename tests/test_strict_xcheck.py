"""T28: the done-time cross-check `decisions-xcheck-*` (`strict.xcheck`, `xcheck_run`, `ghp done`), ported from ado tests/test_strict_xcheck.py.

Seam differences only (X5): a comment is a `comments.Comment` (numeric ids, Markdown body; helpers from tests/test_approvals_sync.py), a kit posting is the
plain kit comment (`decisions.with_marker`), "edited" is `Comment.edited` (no versions list), a dropped comment (`.notes`) stands for ado's truncated
list. Not ported (ADO only or no ghp counterpart): the bypass-bit and forged-author (`on_behalf`) cases, and the F1 `done --base` cases
(test_done_base_*, test_done_ci_*): `ghp done` has no `--base` (T24 ruling), its trust is origin/HEAD's (`approvals.target_trust`), pinned below."""
import json

import pytest

import ghp.__main__ as cli
from ghp import approvals, comments, decisions, strict
from ghp.comments import Comments, Note
from ghp.config import Trust
from ghp.gh import GhError, Viewer
from ghp.refs import Ref

from test_approvals_sync import EXCL, HUMAN, KIT, OTHER, S1, S2, S3, TOKEN, C, _dsync, _dfile, _kit, _posted, _vote

N = 7
SEKRET = "SEKRET-sentinel"
RULES = {r: "decisions-xcheck-%s" % r for r in ("marker", "dropped", "verdict", "truncated", "unreachable")}
REF = Ref("octo", "demo", N)


def _x(md, rows, approvers=(HUMAN,), excluded=EXCL, truncated=False):
    return strict.xcheck(md, Comments(rows), truncated, set(approvers), set(excluded), N)


def _rules(findings):
    return [f["rule"] for f in findings]


def _clean():
    md, body = _posted(S1, S2, S3)
    rows = [_kit(body), _vote(11, "/approve D1"), _vote(12, "/reject D2: no", minute=6)]
    return _dsync(md, rows)[0], rows


def test_clean_story_passes():
    new, rows = _clean()
    assert _x(new, rows) == []
    md, body = _posted(S1)
    assert _x(md, [_kit(body)]) == []
    assert _x("", []) == []


def test_a_later_reject_and_a_reposted_line_stay_clean():
    md, body = _posted(S1)
    rows = [_kit(body), _vote(11, "/approve D1"), _vote(13, "/reject D1: changed my mind", minute=9)]
    new = _dsync(md, rows)[0]
    assert "rejected" in new and _x(new, rows) == []
    edited, body2 = _posted(S1.replace("cursor paging", "offset paging"), cid=20)
    rows = [_kit(body), _kit(body2, cid=20, minute=3), _vote(21, "/approve D1", minute=6)]
    new = _dsync(edited, rows)[0]
    assert _x(new, rows) == []


def test_forged_marker_and_another_storys_marker():
    md, body = _posted(S1)
    h = decisions.line_hash("Use cursor paging")
    for forged in ("posted: 99:%s" % h, "posted: 10:%s" % ("0" * 8)):
        bad = md.replace("posted: 10:%s" % h, forged)
        assert bad != md and _rules(_x(bad, [_kit(body)])) == [RULES["marker"]], forged
    assert _rules(_x(md, [_kit(body, author=OTHER)], excluded={TOKEN})) == [RULES["marker"]]
    assert _rules(_x(md, [_kit("looks fine")])) == [RULES["marker"]]


def test_edited_line():
    md, body = _posted(S1, S2)
    edited = md.replace("Use cursor paging", "Use offset paging")
    assert _rules(_x(edited, [_kit(body)])) == [RULES["marker"]]
    rehashed = edited.replace(decisions.line_hash("Use cursor paging"), decisions.line_hash("Use offset paging"))
    assert _rules(_x(rehashed, [_kit(body)])) == [RULES["marker"]]


def test_deleted_line_and_deleted_file():
    md, body = _posted(S1, S2)
    gone = "\n".join(ln for ln in md.splitlines() if not ln.startswith("- [ ] D2")) + "\n"
    out = _x(gone, [_kit(body)])
    assert _rules(out) == [RULES["dropped"]] and "D2" in out[0]["detail"] and "D1" not in out[0]["detail"]
    assert _rules(_x("", [_kit(body)])) == [RULES["dropped"]]


def test_hand_written_verdict_with_or_without_via():
    md, body = _posted(S1, S2)
    for hand in (decisions.resolve(md, "D1", "validated", "PO", "agreed"), decisions.resolve(md, "D1", "validated", "approver", "", False, "777"),
                 decisions.resolve(md, "D1", "validated", "approver", "", False, "10")):
        assert _rules(_x(hand, [_kit(body)])) == [RULES["verdict"]], hand
    unposted = _dfile(S1)
    assert _rules(_x(decisions.resolve(unposted, "D1", "validated", "PO", ""), [])) == [RULES["verdict"]]


def test_via_pointing_at_the_agents_own_approve():
    md, body = _posted(S1)
    fake = decisions.resolve(md, "D1", "validated", "approver", "", False, "11")
    for author in (TOKEN, KIT, OTHER):
        assert _rules(_x(fake, [_kit(body), _vote(11, "/approve D1", minute=6, author=author)])) == [RULES["verdict"]], author
    assert _rules(_x(fake, [_kit(body), _vote(11, "/approve D1", minute=6, typ="Bot")])) == [RULES["verdict"]]
    assert _rules(_x(fake, [_kit(body), _vote(11, "> /approve D1", minute=6)])) == [RULES["verdict"]]  # quoted
    assert _x(fake, [_kit(body), _vote(11, "/approve D1", minute=6)]) == []


def test_verdict_older_than_its_posting():
    md, body = _posted(S1)
    fake = decisions.resolve(md, "D1", "validated", "approver", "", False, "8")
    rows = [_kit(body, minute=5), _vote(8, "/approve D1", minute=4)]
    assert _rules(_x(fake, rows)) == [RULES["verdict"]]
    tie = [_kit(C("x", 70).body and body, cid=70, minute=5), C("/approve D1", 10, minute=5)]  # same second (10:05:10): the id breaks the tie
    fake70 = decisions.resolve(_posted(S1, cid=70)[0], "D1", "validated", "approver", "", False, "10")
    assert tie[0].created_at == tie[1].created_at and _rules(_x(fake70, tie)) == [RULES["verdict"]]


def test_verdict_must_be_the_newest_valid_command_with_the_matching_verb():
    new, rows = _clean()
    assert _x(new, rows) == []
    assert _rules(_x(new, rows + [_vote(13, "/reject D1", minute=9)])) == [RULES["verdict"]]
    assert _rules(_x(new.replace("D1: Use cursor paging → validated", "D1: Use cursor paging → rejected"), rows)) == [RULES["verdict"]]
    assert _x(new, rows + [_vote(13, "/reject D1", minute=9, author=TOKEN)]) == []
    assert _rules(_x(new, rows + [_vote(13, "/reject D1 oops", minute=9)])) == [RULES["verdict"]]
    assert _rules(_x(new, rows, approvers=(OTHER,))) == [RULES["verdict"]]


def test_by_and_note_are_what_sync_wrote():
    new, rows = _clean()
    assert _rules(_x(new.replace("by: approver", "by: PO"), rows)) == [RULES["verdict"]]
    assert _rules(_x(new.replace("→ rejected · by: approver · note: no", "→ rejected · by: approver · note: a different reason"), rows)) == [RULES["verdict"]]


def test_a_via_taken_off_a_synced_line_is_dropped_and_unbacked():
    new, rows = _clean()
    stripped = new.replace(" · via: 11", "")
    assert stripped != new
    out = _x(stripped, rows)
    assert _rules(out) == [RULES["dropped"], RULES["verdict"]] and "D1" in out[0]["detail"] and "D2" not in out[0]["detail"]


def test_a_line_set_back_to_an_older_posting_is_dropped():
    old, body = _posted(S1, cid=10)
    new_md, body2 = _posted(S1.replace("cursor paging", "offset paging"), cid=20)
    rows = [_kit(body), _kit(body2, cid=20, minute=3)]
    assert _x(new_md, rows) == []
    assert _rules(_x(old, rows)) == [RULES["dropped"]]


def test_posting_comment_edited_lossy_or_not_plain():
    md, body = _posted(S1)
    assert _rules(_x(md, [_kit(body, edit_count=2, last_edited_at="2026-10-01T10:30:00Z")])) == [RULES["marker"]]
    assert _rules(_x(md, [_kit("<div>\n" + body)])) == [RULES["marker"]]
    lossy, body_l = _posted("- [ ] D1: Mail dana@example.invalid → _(open)_ · kind: decision · why: w")
    assert _rules(_x(lossy, [_kit(body_l)])) == [RULES["marker"]]


def test_an_unreadable_or_edited_decisions_comment_no_line_points_at_is_a_marker_finding():
    md, body = _posted(S1)
    extra = _kit("> " + body, cid=30, minute=3)  # a decisions comment whose lines are not a plain kit comment
    assert _rules(_x(md, [_kit(body), extra])) == [RULES["marker"]]
    extra = _kit(body, cid=31, minute=4, edit_count=2, last_edited_at="2026-10-01T10:40:00Z")  # ghp: an edited newer posting is also the newest one
    assert _rules(_x(md, [_kit(body), extra])) == [RULES["marker"], RULES["dropped"]]


def test_truncated_list():
    new, rows = _clean()
    assert _rules(_x(new, rows, truncated=True)) == [RULES["truncated"]]


def test_an_error_is_a_finding_not_a_pass(monkeypatch):
    new, rows = _clean()

    def boom(*a, **k):
        raise GhError("E_GH_HTTP", 500, SEKRET)
    monkeypatch.setattr(approvals, "newest", boom)
    out = _x(new, rows)
    assert _rules(out) == [RULES["unreachable"]] and SEKRET not in json.dumps(out)


def test_findings_hold_ids_and_fixed_texts_only():
    secret = "ZEBRA-sentinel"
    md, body = _posted(S1.replace("Use cursor paging", secret))
    bad = decisions.resolve(md, "D1", "validated", "PO", "note %s" % secret)
    out = _x(bad, [_kit(body + "\n" + secret)])
    assert out and secret not in json.dumps(out)
    for f in out:
        assert set(f) == {"rule", "detail", "fix"} and f["rule"] in RULES.values()


def test_a_file_that_cannot_be_read_line_by_line_is_dropped():
    md, body = _posted(S1)
    for bad in (md + S1 + "\n", md + "- [ ] D2: broken line\n", md + "<!-- never closed\n", md + "- [ ] D٣: x → _(open)_ · kind: decision · why: w\n"):
        assert _rules(_x(bad, [_kit(body)])) == [RULES["dropped"]], bad


def test_a_posting_that_starts_with_an_invisible_character_is_still_a_posting():
    md, body = _posted(S1)
    for disguise in ("​", "﻿", "**"):
        out = _x(md, [_kit(body), _kit(disguise + body, cid=30, minute=3)])
        assert _rules(out) == [RULES["marker"]], repr(disguise)


def test_marker_fix_text_names_a_non_approver_poster():
    md, body = _posted(S1)
    out = _x(md, [_kit(body, author=OTHER)], excluded={TOKEN})
    assert "non-approver identity" in out[0]["fix"] and "ghp decisions 7 --post" in out[0]["fix"]


# ---- xcheck_run: one fetch, the token identity and the CI identity excluded ----

class G:
    def __init__(self, me=TOKEN, fail=False):
        self.me, self.fail = me, fail

    def viewer(self):
        if self.fail:
            raise GhError("E_GH_HTTP", 500, SEKRET)
        return Viewer(self.me, "agent", ["repo"])


def _fetching(monkeypatch, rows, notes=(), fail=False):
    def fetch(gh, ref):
        if fail:
            raise GhError("E_GH_HTTP", 500, SEKRET)
        return Comments(list(rows), list(notes))
    monkeypatch.setattr(comments, "fetch", fetch)


def test_xcheck_run_clean_and_fixed_findings(monkeypatch):
    new, rows = _clean()
    rows = [_kit(rows[0].body, author=TOKEN)] + rows[1:]
    _fetching(monkeypatch, rows)
    assert strict.xcheck_run(G(), REF, new, [HUMAN]) == []
    for gh, fail in ((G(fail=True), False), (G(), True)):
        _fetching(monkeypatch, rows, fail=fail)
        out = strict.xcheck_run(gh, REF, new, [HUMAN])
        assert _rules(out) == [RULES["unreachable"]] and SEKRET not in json.dumps(out)
    _fetching(monkeypatch, rows)
    assert _rules(strict.xcheck_run(G(me="not an id"), REF, new, [HUMAN])) == [RULES["unreachable"]]


def test_xcheck_run_dropped_comments_and_ci_identity(monkeypatch):
    new, rows = _clean()
    _fetching(monkeypatch, rows, notes=[Note(comments.JOIN, 99, "only in REST")])
    assert _rules(strict.xcheck_run(G(), REF, new, [HUMAN])) == [RULES["truncated"]]
    ci = 900011
    md, body = _posted(S1)
    fake = decisions.resolve(md, "D1", "validated", "approver", "", False, "11")
    rows = [_kit(body), _vote(11, "/approve D1", minute=6, author=ci)]
    _fetching(monkeypatch, rows)
    assert strict.xcheck_run(G(), REF, fake, [ci]) == []
    assert _rules(strict.xcheck_run(G(), REF, fake, [ci], ci_identity=ci)) == [RULES["verdict"]]
    assert _rules(strict.xcheck_run(G(me=ci), REF, fake, [ci])) == [RULES["verdict"]]


def test_xcheck_run_refuses_a_ci_identity_that_is_not_an_id(monkeypatch):
    new, rows = _clean()
    _fetching(monkeypatch, rows)
    assert _rules(strict.xcheck_run(G(), REF, new, [HUMAN], ci_identity="nope")) == [RULES["unreachable"]]


# ---- ghp done ----

def _done_env(tmp_path, monkeypatch, md, rows, approvers=(HUMAN,), mode="advisory", target_mode=None):
    import test_done
    monkeypatch.setattr(test_done.issues, "_nap", lambda _s: None)
    test_done.repo(tmp_path, monkeypatch, cfg={"mode": mode})
    (tmp_path / "dod" / ("Decisions_%d.md" % test_done.N)).write_bytes(md.encode("utf-8"))
    trust = Trust(list(approvers), "comment" if approvers else "terminal", {})
    fake = lambda repo=".", base=None: trust  # noqa: E731
    fake.keep = True
    monkeypatch.setattr(approvals, "target_trust", fake)
    monkeypatch.setattr(strict, "default_base", lambda repo: "refs/remotes/origin/main" if target_mode else "")
    from ghp.config import Config
    monkeypatch.setattr(Config, "load_from_ref", classmethod(lambda cls, ref, repo=".": Config("octo", "demo", mode=target_mode)))
    _fetching(monkeypatch, rows)
    monkeypatch.setattr("ghp.questions._me", lambda gh: TOKEN)
    hub = test_done.DoneHub()
    return test_done, hub


def _forged():
    md, body = _posted(S1)
    return decisions.resolve(md, "D1", "validated", "approver", "", False, "777"), [_kit(body)]


def test_advisory_warns_only(tmp_path, monkeypatch, capsys):
    md, rows = _forged()
    td, hub = _done_env(tmp_path, monkeypatch, md, rows, approvers=())  # terminal mode: the cross-check does not run
    code, out, err = td.run(hub, capsys)
    assert code == 0 and "decisions-xcheck" not in err
    td, hub = _done_env(tmp_path, monkeypatch, md, rows)
    code, out, err = td.run(hub, capsys)
    assert code == 0 and err.startswith("warning: decisions-xcheck-verdict: ") and "D1" in err and not hub.armed


def test_strict_blocks_preview_and_apply_and_sends_nothing(tmp_path, monkeypatch, capsys):
    md, rows = _forged()
    for argv in ((), ("--apply",)):
        td, hub = _done_env(tmp_path, monkeypatch, md, rows, target_mode="strict")
        code, out, err = td.run(hub, capsys, *argv)
        assert code == 2 and json.loads(out)["findings"][0]["rule"] == RULES["verdict"] and not hub.armed and hub.writes == []


def test_strict_done_unreachable_fails_closed(tmp_path, monkeypatch, capsys):
    md, rows = _forged()
    td, hub = _done_env(tmp_path, monkeypatch, md, rows, target_mode="strict")
    _fetching(monkeypatch, rows, fail=True)
    code, out, err = td.run(hub, capsys)
    assert code == 2 and json.loads(out)["findings"][0]["rule"] == RULES["unreachable"] and SEKRET not in out + err


def test_not_run_without_approvers(tmp_path, monkeypatch, capsys):
    md, rows = _forged()
    td, hub = _done_env(tmp_path, monkeypatch, md, rows, approvers=(), target_mode="strict")
    monkeypatch.setattr(strict, "xcheck_run", lambda *a, **k: pytest.fail("the cross-check ran without approvers"))
    code, out, err = td.run(hub, capsys)
    assert code == 0 and "decisions-xcheck" not in out + err


def test_a_pr_that_sets_advisory_cannot_turn_its_own_cross_check_into_a_warning(tmp_path, monkeypatch, capsys):
    md, rows = _forged()
    td, hub = _done_env(tmp_path, monkeypatch, md, rows, mode="advisory", target_mode="strict")  # the working tree says advisory, the target strict
    code, out, err = td.run(hub, capsys)
    assert code == 2 and not hub.armed


def test_done_reads_approvers_only_from_the_target_branch(tmp_path, monkeypatch, capsys):
    """ghp's counterpart of ado's F1 cases: the approvers that switch the cross-check on are the target branch's; the working tree's are never read."""
    md, rows = _forged()
    td, hub = _done_env(tmp_path, monkeypatch, md, rows, approvers=())
    cfg = json.loads((tmp_path / "ghp.config.json").read_text())
    (tmp_path / "ghp.config.json").write_text(json.dumps({**cfg, "approvers": [HUMAN], "approval": "comment"}))  # the PR adds them: not believed
    code, out, err = td.run(hub, capsys)
    assert "decisions-xcheck" not in err


def test_an_unreadable_decisions_file_is_refused(tmp_path, monkeypatch, capsys):
    import os
    md, rows = _forged()
    td, hub = _done_env(tmp_path, monkeypatch, md, rows)
    path = tmp_path / "dod" / ("Decisions_%d.md" % td.N)
    path.unlink()
    os.symlink(tmp_path / "ghp.config.json", path)
    code, out, err = td.run(hub, capsys)
    assert code == 2 and "decisions file is not a regular file" in err and not hub.armed


def test_via_must_name_the_newest_valid_command_itself():
    md, body = _posted(S1)
    rows = [_kit(body), _vote(11, "/approve D1"), _vote(13, "/approve D1", minute=9)]
    synced = _dsync(md, rows)[0]
    assert "via: 13" in synced and _x(synced, rows) == []
    old_via = synced.replace("via: 13", "via: 11")  # an approval, but not the newest one: not what --sync would write now
    assert _rules(_x(old_via, rows)) == [RULES["verdict"]]
