"""P3-4b: the hierarchy walk and `ghp rules <n>` (GETs only; every GET before any file; fixed texts; ids, counts, keys and hashes in JSON), and
`ghp rules <n> --confirm` (P3-7: the human-only re-confirmation, a real terminal required). Ported from ado (T12). Tracker seam: ado's work-item
dicts and FakeClient became GitHub REST issues served by a FakeGh (`rules_support`): an Epic/Feature is an issue labelled `ghp:epic` /
`ghp:feature` whose body holds the Markdown section, its parent is `parent_issue_url`. `test_ado_decisions_add_and_post_work_for_a_feature_id` lives in
test_decisions.py (T16: the `decisions` verb). New: the label, three-level, deep-chain and other-repository tests of the walk (AC3, AC4)."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import FakeGh, TypedStdin, run_in_pty, shown_code, terminal
from ghp import decisions, dod, issues, rules, stack
from ghp.config import Config
from ghp.gh import GhError
from ghp.refs import Ref
from rules_support import desc, fail, make_issue, path, raw_issue, serve


def wi(n, kind, body=None, parent=None, title="Points on purchases", **kw):
    return raw_issue(n, kind, body, parent, title, **kw)


STORY = wi(1001, None, "## Acceptance criteria\nAs a member\n", parent=1201, title="Award points at checkout")
STORY_ISSUE = make_issue(1001, body="## Acceptance criteria\nAs a member\n", title="Award points at checkout", parent=1201)
FEATURE = wi(1201, "Feature", desc((1, "approved"), (2, "draft")), parent=1200, title="Secret feature title")
EPIC = wi(1200, "Epic", desc((1, "approved"), (3, "retired")), title="Loyalty programme")
TYPES = ["Epic", "Feature"]
REF = Ref("octo", "demo", 1001)


def cfg(**rules_cfg):
    c = Config(owner="octo", repo="demo")
    c.rules = {"enabled": True, "types": TYPES, **rules_cfg}
    return c


def env(tmp_path, monkeypatch, raws, rules_cfg=None, dod_path=None, gh=None):
    """A repository in `tmp_path` (config octo/demo) and a FakeGh serving `raws`; returns (the CLI module, the FakeGh)."""
    import ghp.__main__ as m
    data = {"owner": "octo", "repo": "demo"}
    if rules_cfg is not None:
        data["rules"] = rules_cfg
    if dod_path:
        data["dod_path"] = dod_path
    (tmp_path / "ghp.config.json").write_text(json.dumps(data))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    monkeypatch.setattr(rules, "_today", lambda: "2026-11-03")
    return m, serve(*raws, gh=gh)


def run(m, gh, capsys, *args):
    code = m.main(["rules", *args], gh=gh)
    cap = capsys.readouterr()
    return code, (json.loads(cap.out) if cap.out.strip() else None), cap.err


def ran(gh):
    return [(c[1], c[2]) for c in gh.calls]


def files(tmp_path):
    return sorted(p.name for p in tmp_path.rglob("Rules_*") if p.is_file())


# ---- walk ----

def test_walk_story_feature_epic():
    gh = serve(STORY, FEATURE, EPIC)
    got = rules.walk(gh, REF, TYPES)
    assert [(g["id"], g["type"]) for g in got] == [(1201, "Feature"), (1200, "Epic")]
    assert got[0]["issue"].body == FEATURE["body"] and got[0]["parsed"]["found"]
    assert [r["id"] for r in got[0]["parsed"]["rules"]] == ["BR-1201-1", "BR-1201-2"]
    assert [r["id"] for r in got[1]["parsed"]["rules"]] == ["BR-1200-1", "BR-1200-3"]
    assert [c[:2] for c in gh.calls] == [("rest", "GET")] * 3
    assert [c[2] for c in gh.calls] == [path(1001), path(1201), path(1200)]


def test_walk_skips_unlisted_types_and_ancestors_without_section():
    release = wi(1300, "Release", desc((1, "approved")), parent=1201)  # not in rules.types: walked through, never kept
    story = wi(1001, None, "x", parent=1300)
    feature = wi(1201, "Feature", desc((1, "approved")), parent=1200)
    epic = wi(1200, "Epic", "Just a description, no rules here.")
    got = rules.walk(serve(story, release, feature, epic), REF, TYPES)
    assert [g["id"] for g in got] == [1201]  # the Epic has no section: not in `from`
    got = rules.walk(serve(story, release, feature, epic), REF, ["feature", "EPIC"])  # types are case-folded
    assert [g["id"] for g in got] == [1201]
    assert rules.walk(serve(wi(1001, "Epic", desc((1, "approved")))), REF, TYPES) == []  # the story itself never counts
    no_body = wi(1200, "Epic")  # an empty body
    assert rules.walk(serve(wi(1001, None, None, parent=1200), no_body), REF, TYPES) == []


def test_walk_keeps_an_ancestor_whose_problem_would_otherwise_vanish():
    hidden = wi(1200, "Epic", "<!-- never closed " + desc((1, "approved")))  # the comment swallows the whole section
    got = rules.walk(serve(wi(1001, None, "x", parent=1200), hidden), REF, TYPES)
    assert [g["id"] for g in got] == [1200] and not got[0]["parsed"]["found"]
    assert [p["issue"] for p in got[0]["parsed"]["problems"]] == ["unclosed-markup"]


def test_walk_stops_at_3_links_and_on_cycle():
    chain = [wi(1001, None, "x", parent=1100)] + [wi(1100 + i, "Epic", desc((1, "approved")), parent=1101 + i) for i in range(5)]
    gh = serve(*chain)
    got = rules.walk(gh, REF, TYPES)
    assert [g["id"] for g in got] == [1100, 1101, 1102] and len(gh.calls) == 4  # story + 3 links; the fourth is never fetched
    loop = [wi(1001, None, "x", parent=1200), wi(1200, "Feature", desc((1, "approved")), parent=1001)]
    gh = serve(*loop)
    assert [g["id"] for g in rules.walk(gh, REF, TYPES)] == [1200] and len(gh.calls) == 2  # the parent that is the story: stop
    self_loop = [wi(1001, None, "x", parent=1200), wi(1200, "Feature", desc((1, "approved")), parent=1200)]
    gh = serve(*self_loop)
    assert [g["id"] for g in rules.walk(gh, REF, TYPES)] == [1200] and len(gh.calls) == 2


@pytest.mark.parametrize("url", ["https://api.github.com/repos/octo/demo/issues/abc", "https://api.github.com/repos/octo/demo/issues/",
                                 "https://api.github.com/repos/octo/demo/issues/1200?x=1", "https://api.github.com/repos/octo/demo/issues/0",
                                 "https://api.github.com/repos/octo/demo/issues/-5", "https://api.github.com/repos/octo/demo/issues/12\n",
                                 "https://api.github.com/repos/octo/demo/issues/١٢", "https://dev.azure.com/contoso/p/_apis/wit/workItems/1200",
                                 "https://api.github.com/repos/octo/demo/pulls/1200", "", 12, ["1200"]])
def test_walk_bad_parent_url_is_a_named_error_and_nothing_else_is_fetched(url):
    """Tracker seam: ado stopped the walk with `bad-parent-link`; a GitHub parent link comes in the one `parent_issue_url` field, which
    `issues.get` validates, so a bad one is an E_GH_BAD_BODY (exit 1) before any ancestor is fetched."""
    story = dict(STORY, parent_issue_url=url)
    gh = serve(story, EPIC)
    with pytest.raises(GhError) as e:
        rules.walk(gh, REF, TYPES)
    assert e.value.code == "E_GH_BAD_BODY" and len(gh.calls) == 1


def test_walk_an_answer_for_another_issue_is_an_error():
    gh = serve(STORY)
    gh.on_rest("GET", path(1201), dict(FEATURE, number=1999))  # GitHub answered with another issue than the one asked for
    with pytest.raises(GhError) as e:
        rules.walk(gh, REF, TYPES)
    assert e.value.code == "E_GH_BAD_BODY" and len(gh.calls) == 2


# ---- new (AC3, AC4): parents, labels, three levels, deep chains, other repositories ----

def test_walk_uses_parents_and_labels():
    """AC3: the walk follows `Issue.parent`; an issue is a rule holder only when it is LABELLED `ghp:epic` / `ghp:feature`."""
    story = wi(1001, None, "x", parent=1300)
    unlabelled = wi(1300, None, desc((9, "approved")), parent=1201, labels=["area:loyalty"])  # rules in the body, no holder label: walked through
    wrong_case = wi(1201, None, desc((1, "approved")), parent=1200, labels=["GHP:Feature"])  # labels are case-insensitive on GitHub
    story_label = wi(1200, None, desc((2, "approved")), labels=["ghp:story"])  # the story label is never a holder (rules.types may not name it)
    gh = serve(story, unlabelled, wrong_case, story_label)
    got = rules.walk(gh, REF, TYPES)
    assert [(g["id"], g["type"]) for g in got] == [(1201, "Feature")]
    assert [c[2] for c in gh.calls] == [path(1001), path(1300), path(1201), path(1200)]
    # the type names come from the config: Epic only
    assert [g["id"] for g in rules.walk(serve(story, unlabelled, wrong_case, story_label), REF, ["Epic"])] == []
    only_epic = wi(1200, None, desc((2, "approved")), labels=["ghp:epic", "area:x"])
    assert [g["id"] for g in rules.walk(serve(story, unlabelled, wrong_case, only_epic), REF, ["Epic"])] == [1200]
    # the parent link, not a title or a body, says where an issue sits: an issue that names no parent ends the chain
    orphan = wi(1001, None, "Epic #1200 is my parent", parent=None)
    gh = serve(orphan, only_epic)
    assert rules.walk(gh, REF, TYPES) == [] and len(gh.calls) == 1


def test_walk_stops_at_three_levels():
    """AC3: story + three ancestors are read; the fourth and fifth are never fetched even though they are holders."""
    holders = [wi(1100 + i, "Epic" if i % 2 else "Feature", desc((1, "approved")), parent=1101 + i) for i in range(5)]
    gh = serve(wi(1001, None, "x", parent=1100), *holders[:3])  # the 4th is not served: fetching it would fail the FakeGh
    got = rules.walk(gh, REF, TYPES)
    assert [g["id"] for g in got] == [1100, 1101, 1102] and [c[2] for c in gh.calls] == [path(n) for n in (1001, 1100, 1101, 1102)]
    assert rules.MAX_LINKS == 3 and dod.MAX_RULES_FROM == 3


def test_deep_chain_is_cut_at_limit_without_error():
    """AC4: a chain 8 levels deep (live: GitHub nests at most 7 below the story) does not break the walk: it is cut at 3, and the levels
    beyond are not even requested."""
    chain = [wi(1001, None, "x", parent=1100)] + [wi(1100 + i, "Epic", desc((i + 1, "approved")), parent=1101 + i) for i in range(8)]
    assert len(chain) == 9
    gh = serve(*chain[:4])  # only the story and three ancestors exist for the FakeGh; the 4th ancestor is a link nobody follows
    got = rules.walk(gh, REF, TYPES)
    assert [g["id"] for g in got] == [1100, 1101, 1102] and len(gh.calls) == 4
    gh = serve(*chain[:4])
    problems = []
    assert len(rules.walk(gh, REF, TYPES, problems)) == 3 and problems == []  # a cut is not an error (as in ado)


def test_a_parent_in_another_repository_ends_the_chain_with_a_note(tmp_path, monkeypatch, capsys):
    """D7: rules are read from the configured repository only. The foreign parent is NOT fetched (the FakeGh would fail on it); the chain
    ends there and the walk says so with the fixed key `foreign-parent` and the number of the issue whose parent it is."""
    foreign = wi(1201, "Feature", desc((1, "approved")), parent=("other", "elsewhere", 5))
    story = wi(1001, None, "x", parent=1201)
    gh, problems = serve(story, foreign), []
    got = rules.walk(gh, REF, TYPES, problems)
    assert [g["id"] for g in got] == [1201] and len(gh.calls) == 2  # the Feature (same repo) is kept; its foreign parent is not read
    assert problems == [{"id": 1201, "rule": None, "level": "error", "issue": "foreign-parent"}] and "foreign-parent" in rules.WALK_ISSUES
    direct = wi(1001, None, "x", parent=("other", "elsewhere", 5))
    gh, problems = serve(direct), []
    assert rules.walk(gh, REF, TYPES, problems) == [] and problems[0]["id"] == 1001 and len(gh.calls) == 1
    same_name = wi(1001, None, "x", parent=("OCTO", "Demo", 1201))  # GitHub names are case-insensitive: the same repository
    assert [g["id"] for g in rules.walk(serve(same_name, FEATURE, EPIC), REF, TYPES)] == [1201, 1200]
    # through the verb: shown in the JSON, nothing from the foreign repository is written
    m, gh = env(tmp_path, monkeypatch, [story, foreign], {"enabled": True})
    code, out, _ = run(m, gh, capsys, "1001")
    assert code == 0 and out["from"] == [1201] and {"id": 1201, "rule": None, "level": "error", "issue": "foreign-parent"} in out["problems"]
    assert "elsewhere" not in json.dumps(out) and files(tmp_path) == ["Rules_1201.md"]


def test_a_fetched_story_is_not_fetched_again():
    gh = serve(STORY, FEATURE, EPIC)
    rules.walk(gh, REF, TYPES, story=issues.get(serve(STORY), REF))
    assert [c[2] for c in gh.calls] == [path(1201), path(1200)]


# ---- the verb ----

def test_get_only(tmp_path, monkeypatch, capsys):
    m, gh = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    code, out, _ = run(m, gh, capsys, "1001")
    assert code == 0 and files(tmp_path) == ["Rules_1200.md", "Rules_1201.md"]
    assert ran(gh) == [("GET", path(i)) for i in (1001, 1201, 1200)]
    assert all(c[0] == "rest" and c[1] == "GET" and c[3] is None for c in gh.calls) and not gh.armed


def test_json_shape_and_files(tmp_path, monkeypatch, capsys):
    m, gh = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    code, out, _ = run(m, gh, capsys, "1001")
    assert code == 0 and list(out) == ["from", "files", "rules", "problems", "changed", "decisions", "notes"]
    assert out["from"] == [1201, 1200] and out["changed"] == [1201, 1200] and out["decisions"] == []
    assert out["files"] == [str(Path("dod/Rules_1201.md")), str(Path("dod/Rules_1200.md"))]
    assert out["rules"] == {"approved": 2, "draft": 1, "retired": 1}
    assert out["problems"] == []
    text = (tmp_path / "dod" / "Rules_1201.md").read_text(encoding="utf-8")
    pf = rules.parse_file(text, 1201)  # the toolkit's own file reads back, with the walk's rules in it
    assert not pf["invalid"] and pf["format"] == rules.FORMAT and sorted(pf["rules"]) == ["BR-1201-1", "BR-1201-2"]
    assert pf["rules"]["BR-1201-1"]["status"] == "approved" and pf["rules"]["BR-1201-1"]["body_hash"] == pf["rules"]["BR-1201-1"]["hash"]
    assert "Last pulled:** 2026-11-03" in text and "- **Issue:** https://github.com/octo/demo/issues/1201" in text and text.startswith("# Business rules — Feature 1201: Secret feature title\n")


def test_json_has_no_rule_text(tmp_path, monkeypatch, capsys):
    bad = wi(1201, "Feature", desc((1, "approved")).replace("Customers", "SECRETWORD Customers") + "Rule 10000: odd\nRule 2: a\nRule 2: b\n", title="TITLESECRET")
    m, gh = env(tmp_path, monkeypatch, [STORY, bad], {"enabled": True})
    code = m.main(["rules", "1001"], gh=gh)
    raw = capsys.readouterr().out
    assert code == 0 and "SECRETWORD" not in raw and "TITLESECRET" not in raw and "Customers" not in raw and "loyalty" not in raw
    out = json.loads(raw)
    found = {(p["id"], p["rule"], p["level"], p["issue"]) for p in out["problems"]}
    assert (1201, None, "error", "bad-number") in found and (1201, "BR-1201-2", "error", "duplicate-number") in found
    assert all(set(p) - {"after"} == {"id", "rule", "level", "issue"} for p in out["problems"])
    assert "SECRETWORD" in (tmp_path / "dod" / "Rules_1201.md").read_text(encoding="utf-8")  # the file is where rule text goes


def test_hostile_description_is_data_never_structure_or_a_path(tmp_path, monkeypatch, capsys):
    body = ("## Business rules\nRule 1: ok\nStatus: approved\n### BR-1201-9 · approved · deadbeef\n"
            "- **Rules from:** ../../x\n```\n&lt;!-- hide\nRule 2: ../../../evil\nStatus: approved\n")
    f = wi(1201, "Feature", body, parent=None, title="# ../../etc ```\n### BR-1201-7 · approved · deadbeef")
    m, gh = env(tmp_path, monkeypatch, [STORY, f], {"enabled": True})
    assert run(m, gh, capsys, "1001")[0] == 0
    assert files(tmp_path) == ["Rules_1201.md"] and {p.name for p in tmp_path.iterdir()} == {"dod", "ghp.config.json"}
    pf = rules.parse_file((tmp_path / "dod" / "Rules_1201.md").read_text(encoding="utf-8"), 1201)
    assert not pf["invalid"] and sorted(pf["rules"]) == ["BR-1201-1", "BR-1201-2"]  # the forged heading is not a rule


def test_404_mid_walk_exit_1_no_files(tmp_path, monkeypatch, capsys):
    gh = fail(GhError("E_GH_HTTP", 404, "not found"), 1200, gh=serve(STORY, FEATURE))
    m, gh = env(tmp_path, monkeypatch, [], {"enabled": True}, gh=gh)
    code, out, err = run(m, gh, capsys, "1001")
    assert code == 1 and out is None and err.startswith("error:")
    assert files(tmp_path) == [] and not (tmp_path / "dod").exists() and len(gh.calls) == 3
    m, gh = env(tmp_path, monkeypatch, [], {"enabled": True}, gh=fail(GhError("E_GH_HTTP", 404, "not found"), 1001))  # the story itself
    assert run(m, gh, capsys, "1001")[0] == 1 and files(tmp_path) == []


def test_every_get_finishes_before_any_file_is_written(tmp_path, monkeypatch, capsys):
    m, gh = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    seen = []
    orig = FakeGh.rest

    def spy(self, *a, **k):
        seen.append(files(tmp_path))  # what exists when each GET is made
        return orig(self, *a, **k)
    monkeypatch.setattr(FakeGh, "rest", spy)
    assert run(m, gh, capsys, "1001")[0] == 0
    assert seen == [[], [], []]


def test_an_auth_error_during_the_walk_is_exit_1(tmp_path, monkeypatch, capsys):
    gh = fail(GhError("E_GH_HTTP", 401, "Bad credentials"), 1201, gh=serve(STORY))
    m, gh = env(tmp_path, monkeypatch, [], {"enabled": True}, gh=gh)
    assert run(m, gh, capsys, "1001")[0] == 1 and files(tmp_path) == []


def test_a_pull_request_as_a_parent_is_refused_and_nothing_is_written(tmp_path, monkeypatch, capsys):
    pr = dict(FEATURE, pull_request={"url": "x"})
    m, gh = env(tmp_path, monkeypatch, [STORY, pr], {"enabled": True})
    code, out, err = run(m, gh, capsys, "1001")
    assert code == 2 and out is None and err.startswith("error:") and files(tmp_path) == []


def test_no_ancestor_with_a_section_writes_nothing(tmp_path, monkeypatch, capsys):
    m, gh = env(tmp_path, monkeypatch, [dict(STORY, parent_issue_url=None)], {"enabled": True})
    code, out, _ = run(m, gh, capsys, "1001")
    assert code == 0 and out == {"from": [], "files": [], "rules": {"approved": 0, "draft": 0, "retired": 0}, "problems": [], "changed": [], "decisions": [], "notes": 0}
    assert files(tmp_path) == [] and not (tmp_path / "dod").exists()


def test_rerun_without_a_change_in_the_issues_rewrites_nothing(tmp_path, monkeypatch, capsys):
    m, gh = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    assert run(m, gh, capsys, "1001")[0] == 0
    f1, f2 = tmp_path / "dod" / "Rules_1201.md", tmp_path / "dod" / "Rules_1200.md"
    first = (f1.read_bytes(), f2.read_bytes())
    ino = (f1.stat().st_ino, f2.stat().st_ino)
    m, gh = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    monkeypatch.setattr(rules, "_today", lambda: "2027-01-09")  # another day: only the `pulled` stamp would differ
    code, out, _ = run(m, gh, capsys, "1001")
    assert code == 0 and out["changed"] == [] and out["from"] == [1201, 1200] and len(out["files"]) == 2
    assert (f1.read_bytes(), f2.read_bytes()) == first and (f1.stat().st_ino, f2.stat().st_ino) == ino  # not even replaced
    assert b"2026-11-03" in first[0] and b"2027-01-09" not in first[0]
    # an issue changed one rule: that file is rewritten, with the new date; the other is left alone
    feature = wi(1201, "Feature", desc((1, "approved"), (2, "approved")), parent=1200)
    m, gh = env(tmp_path, monkeypatch, [STORY, feature, EPIC], {"enabled": True})
    monkeypatch.setattr(rules, "_today", lambda: "2027-01-09")
    code, out, _ = run(m, gh, capsys, "1001")
    assert out["changed"] == [1201] and f2.read_bytes() == first[1] and b"2027-01-09" in f1.read_bytes()
    assert out["rules"] == {"approved": 3, "draft": 0, "retired": 1}


def test_a_hand_edited_file_is_replaced_and_crlf_is_kept(tmp_path, monkeypatch, capsys):
    m, gh = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    run(m, gh, capsys, "1001")
    f = tmp_path / "dod" / "Rules_1201.md"
    good = f.read_bytes()
    f.write_bytes(good.replace(b"approved \xc2\xb7", b"retired \xc2\xb7", 1))  # a PR edit
    m, gh = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    code, out, _ = run(m, gh, capsys, "1001")
    assert out["changed"] == [1201] and f.read_bytes() == good
    f.write_bytes(good.replace(b"\n", b"\r\n"))
    m, gh = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    code, out, _ = run(m, gh, capsys, "1001")
    assert out["changed"] == [] and f.read_bytes() == good.replace(b"\n", b"\r\n")  # same content, its own terminator: untouched


def test_existing_symlink_or_non_file_is_refused_and_nothing_is_written(tmp_path, monkeypatch, capsys):
    (tmp_path / "dod").mkdir()
    (tmp_path / "elsewhere.md").write_text("keep me")
    os.symlink(tmp_path / "elsewhere.md", tmp_path / "dod" / "Rules_1200.md")
    m, gh = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    code, out, err = run(m, gh, capsys, "1001")
    assert code == 2 and out is None and err.startswith("error:") and "elsewhere" not in err
    assert (tmp_path / "elsewhere.md").read_text() == "keep me" and files(tmp_path) == ["Rules_1200.md"]  # no Rules_1201.md either
    assert (tmp_path / "dod" / "Rules_1200.md").is_symlink()
    (tmp_path / "dod" / "Rules_1200.md").unlink()
    (tmp_path / "dod" / "Rules_1200.md").mkdir()  # a directory in the way
    m, gh = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    assert run(m, gh, capsys, "1001")[0] == 2 and not (tmp_path / "dod" / "Rules_1201.md").exists()


def test_unwritable_dod_path_exit_2(tmp_path, monkeypatch, capsys):
    (tmp_path / "blocked").write_text("a file where the directory should be")
    m, gh = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True}, dod_path="blocked")
    code, out, err = run(m, gh, capsys, "1001")
    assert code == 2 and out is None and err.startswith("error:")
    assert (tmp_path / "blocked").read_text() == "a file where the directory should be"


@pytest.mark.parametrize("bad", ["../outside", "/abs/dir", "a/../../b"])
def test_a_dod_path_outside_the_repository_is_refused_before_any_call(tmp_path, monkeypatch, capsys, bad):
    m, gh = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True}, dod_path=bad)
    code, out, err = run(m, gh, capsys, "1001")
    assert code == 2 and out is None and gh.calls == [] and "E_DOD_DIR" in err


def test_feature_decisions_listed_with_counts(tmp_path, monkeypatch, capsys):
    (tmp_path / "dod").mkdir()
    (tmp_path / "dod" / "Decisions_1201.md").write_text(
        "# Decisions\n\n"  # (ado: decisions.render_file(1201); the verb that writes the header is T16)
        "- [ ] D1: Ask the PO → _(open)_ · kind: decision · why: w\n- [ ] D2: Second → _(open)_ · kind: assumption · why: w\n"
        "- [x] D3: Skip audit → rejected · by: PO · note: no\n- [x] D4: Cap → validated · by: PO · note: ok\n", encoding="utf-8")
    (tmp_path / "dod" / "Decisions_1001.md").write_text("# Decisions\n\n- [ ] D1: a story one → _(open)_ · kind: decision · why: w\n")
    m, gh = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    code, out, _ = run(m, gh, capsys, "1001")
    assert code == 0 and out["decisions"] == [{"id": 1201, "open": 2, "rejected": 1}]  # only a kept ancestor's file; none for the Epic
    assert "Ask the PO" not in json.dumps(out)
    # a symlinked decisions file is refused before anything is written
    (tmp_path / "dod" / "Decisions_1200.md").symlink_to(tmp_path / "dod" / "Decisions_1201.md")
    for p in tmp_path.glob("dod/Rules_*"):
        p.unlink()
    m, gh = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    assert run(m, gh, capsys, "1001")[0] == 2 and files(tmp_path) == []


# ---- off, exit codes ----

@pytest.mark.parametrize("rules_cfg", [None, {"enabled": False}, {"types": ["Epic"]}])
def test_works_whatever_rules_enabled_says(tmp_path, monkeypatch, capsys, rules_cfg):
    m, gh = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], rules_cfg)
    code, out, _ = run(m, gh, capsys, "1001")
    types = (rules_cfg or {}).get("types", TYPES)
    want = [1200] if types == ["Epic"] else [1201, 1200]  # the default types (Epic, Feature) apply when `rules` has none
    assert code == 0 and out["from"] == want and files(tmp_path) == sorted(f"Rules_{i}.md" for i in want)
    assert all(c[1] == "GET" for c in gh.calls)


def test_two_ancestors_problems_can_be_told_apart(tmp_path, monkeypatch, capsys):
    both = [wi(1201, "Feature", desc((1, "approved")) + "Rule 10000: x\n", parent=1200), wi(1200, "Epic", desc((1, "approved")) + "Rule 10000: x\n")]
    m, gh = env(tmp_path, monkeypatch, [STORY, *both], {"enabled": True})
    out = run(m, gh, capsys, "1001")[1]
    assert [(p["id"], p["issue"]) for p in out["problems"]] == [(1201, "bad-number"), (1200, "bad-number")]


def test_the_storys_own_answered_number_must_match(tmp_path, monkeypatch, capsys):
    with pytest.raises(GhError):
        rules.walk(serve(gh=FakeGh().on_rest("GET", path(1001), dict(STORY, number=1002))), REF, TYPES)
    m, gh = env(tmp_path, monkeypatch, [], {"enabled": True}, gh=FakeGh().on_rest("GET", path(1001), dict(STORY, number=1002)))
    code, out, err = run(m, gh, capsys, "1001")
    assert code == 1 and out is None and err.startswith("error:") and files(tmp_path) == [] and len(gh.calls) == 1


def test_cli_rules_exit_codes(tmp_path, monkeypatch, capsys):
    m, gh = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    assert m.main(["rules", "1001"], gh=gh) == 0
    capsys.readouterr()
    for argv in (["rules"], ["rules", "1001", "--bogus"]):  # argparse
        with pytest.raises(SystemExit) as e:
            m.main(argv, gh=FakeGh())
        assert e.value.code == 2, argv
    for ref in ("abc", "0", "-3", "2147483648", "other/repo#5", "https://github.com/octo/demo/pull/5"):  # refused before any call
        gh = FakeGh()
        assert m.main(["rules", ref], gh=gh) == 2 and gh.calls == [], ref
    capsys.readouterr()
    m, gh = env(tmp_path, monkeypatch, [], {"enabled": True}, gh=fail(GhError("E_GH_HTTP", 500, "down"), 1001))
    assert m.main(["rules", "1001"], gh=gh) == 1
    assert "error:" in capsys.readouterr().err


def test_main_help_lists_the_verb(capsys):
    from ghp.__main__ import main
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "rules" in capsys.readouterr().out


# ---- `ghp rules <n> --confirm` (P3-7): the hashes a human reviewed go into the ledger header; local only ----

FROM_LINE = "- **Rules from:** 1201, 1200\n"
CMD = ["rules", "1001", "--confirm"]


class Ctx:
    """A pulled repo (Rules_1201.md, Rules_1200.md) and a ledger with `Rules from: 1201, 1200` in `tmp_path`."""

    def __init__(self, tmp_path, m):
        self.root, self.m = tmp_path, m
        self.ledger = tmp_path / "dod" / "DoD_1001.md"
        self.h = {r: v["hash"] for w in (1200, 1201) for r, v in rules.parse_file(self.rules_text(w), w)["rules"].items()}

    def rules_text(self, wid):
        return (self.root / "dod" / f"Rules_{wid}.md").read_text(encoding="utf-8")

    def text(self):
        return self.ledger.read_bytes().decode("utf-8")

    def put(self, md):
        self.ledger.write_bytes(md.encode("utf-8"))

    def record(self, value, eol="\n"):
        """Hand-write a `Rules reviewed:` line under `Rules from:` (as an earlier --confirm would have)."""
        md = self.text()
        assert FROM_LINE in md
        self.put(md.replace(FROM_LINE, FROM_LINE + f"- **Rules reviewed:** {value}\n"))

    def value(self):
        return self.text().split("- **Rules reviewed:** ")[1].split("\n")[0].rstrip("\r")

    def want(self):
        return f"BR-1200-1@{self.h['BR-1200-1']}, BR-1201-1@{self.h['BR-1201-1']}"

    def run(self, capsys, *extra):
        code = self.m.main([*CMD, *extra], gh=NoGh())
        cap = capsys.readouterr()
        return code, (json.loads(cap.out) if cap.out.strip().startswith("{") else None), cap.out, cap.err


class NoGh:
    """--confirm is local only: any call on this transport fails the test."""

    def __getattr__(self, name):
        raise AssertionError("--confirm must not reach GitHub")


@pytest.fixture
def ctx(tmp_path, monkeypatch, capsys):
    m, gh = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    assert m.main(["rules", "1001"], gh=gh) == 0
    capsys.readouterr()
    dod.write_dod(STORY_ISSUE, "dod", rules_from=[1201, 1200])
    return Ctx(tmp_path, m)


def refusal(recorded="none", changed="none", removed="none"):
    return rules.CONFIRM_TERMINAL.format(recorded=recorded, changed=changed, removed=removed)


def leftovers(ctx):
    return sorted(p.name for p in (ctx.root / "dod").iterdir() if p.name.endswith(".tmp"))


def test_first_confirm_without_tty_writes(ctx, capsys):
    before = ctx.text()
    code, out, raw, err = ctx.run(capsys)
    assert code == 0 and err == ""
    assert ctx.value() == ctx.want() and ctx.want().startswith("BR-1200-1@")  # sorted by issue, then number; the draft BR-1201-2 and the retired BR-1200-3 are not in scope
    assert ctx.text() == before.replace(FROM_LINE, FROM_LINE + f"- **Rules reviewed:** {ctx.want()}\n")
    assert dod.ledger_rules_reviewed(ctx.text()) == {"present": True, "valid": True, "hashes": {"BR-1200-1": ctx.h["BR-1200-1"], "BR-1201-1": ctx.h["BR-1201-1"]}}
    assert out == {"id": 1001, "reviewed": ctx.want(), "added": ["BR-1200-1", "BR-1201-1"], "changed": [], "removed": [], "written": True}
    assert "Customers earn" not in raw and leftovers(ctx) == []


def test_confirm_is_a_no_op_when_nothing_changed(ctx, capsys):
    assert ctx.run(capsys)[0] == 0
    first = ctx.ledger.stat()
    code, out, _, _ = ctx.run(capsys)
    assert code == 0 and out["written"] is False and out["added"] == out["changed"] == out["removed"] == []
    assert ctx.ledger.stat().st_ino == first.st_ino and ctx.ledger.stat().st_mtime_ns == first.st_mtime_ns


def test_a_recorded_line_in_another_order_is_not_a_change(ctx, capsys):
    ctx.record(f"BR-1201-1@{ctx.h['BR-1201-1']}, BR-1200-1@{ctx.h['BR-1200-1']}")
    before = ctx.text()
    code, out, _, _ = ctx.run(capsys)
    assert code == 0 and out["written"] is False and ctx.text() == before


def test_new_ids_are_added_without_a_terminal_and_the_old_ones_kept(ctx, capsys):
    ctx.record(f"BR-1200-1@{ctx.h['BR-1200-1']}")
    code, out, _, _ = ctx.run(capsys)
    assert code == 0 and out["added"] == ["BR-1201-1"] and out["changed"] == []
    assert ctx.value() == ctx.want()


def test_changed_hash_refused_without_tty(ctx, capsys):
    ctx.record(f"BR-1200-1@{ctx.h['BR-1200-1']}, BR-1201-1@aaaaaaaa")
    before = ctx.text()
    code, out, raw, err = ctx.run(capsys)
    assert code == 2 and out is None and raw == ""
    assert err == f"error: {refusal(changed='BR-1201-1')}\n" and ctx.text() == before
    assert "aaaaaaaa" not in err and ctx.h["BR-1201-1"] not in err and leftovers(ctx) == []


def test_changed_hash_with_tty_rewrites_only_that_line(ctx, capsys, monkeypatch):
    ctx.record(f"BR-1200-1@{ctx.h['BR-1200-1']}, BR-1201-1@aaaaaaaa")
    before = ctx.text()
    stdin = terminal(monkeypatch)
    code, out, raw, err = ctx.run(capsys)
    assert code == 0 and err == "" and stdin.lines == 1
    assert "BR-1201-1" in raw and "aaaaaaaa" in raw and ctx.h["BR-1201-1"] in raw and "Customers earn" not in raw
    after = ctx.text()
    assert after == before.replace("BR-1201-1@aaaaaaaa", f"BR-1201-1@{ctx.h['BR-1201-1']}")
    assert after.count("\n") == before.count("\n") and leftovers(ctx) == []
    assert json.loads(raw[raw.index("{"):]) == {"id": 1001, "reviewed": ctx.want(), "added": [], "changed": ["BR-1201-1"], "removed": [], "written": True}


def test_dropping_a_recorded_id_needs_the_terminal_too(ctx, capsys, monkeypatch):
    ctx.record(f"BR-1200-1@{ctx.h['BR-1200-1']}, BR-1200-2@bbbbbbbb, BR-1201-1@{ctx.h['BR-1201-1']}")  # BR-1200-2 is in no file
    before = ctx.text()
    code, _, _, err = ctx.run(capsys)
    assert code == 2 and err == f"error: {refusal(removed='BR-1200-2')}\n" and ctx.text() == before
    terminal(monkeypatch)
    code, out, raw, _ = ctx.run(capsys)
    assert code == 0 and "BR-1200-2" in raw and "bbbbbbbb" in raw and out is None  # the preview comes first, the JSON after it
    assert ctx.value() == ctx.want()


def test_an_excluded_rule_leaves_the_record_only_through_the_terminal(ctx, capsys, monkeypatch):
    md = "- [ ] D1: out of scope: BR-1201-1 \u2192 _(open)_ \u00b7 kind: decision \u00b7 why: not for this story\n"  # the line `decisions.add` writes
    d1 = decisions.parse_decisions(md)["open"][0]
    (ctx.root / "dod" / "Decisions_1001.md").write_text(md.rstrip("\n") + f" · posted: 5:{decisions.marker_hash(d1)}\n", encoding="utf-8")
    ctx.record(f"BR-1200-1@{ctx.h['BR-1200-1']}, BR-1201-1@{ctx.h['BR-1201-1']}")
    assert ctx.run(capsys)[0] == 2
    terminal(monkeypatch)
    code, _, raw, _ = ctx.run(capsys)
    assert code == 0 and ctx.value() == f"BR-1200-1@{ctx.h['BR-1200-1']}"


def test_nothing_in_scope_records_none(ctx, capsys):
    ctx.put(ctx.text().replace(FROM_LINE, "- **Rules from:** none\n"))
    code, out, _, _ = ctx.run(capsys)
    assert code == 0 and ctx.value() == "none" and out["reviewed"] == "none"
    assert dod.ledger_rules_reviewed(ctx.text())["valid"]


def test_confirm_survives_refresh(ctx, capsys):
    assert ctx.run(capsys)[0] == 0
    line = f"- **Rules reviewed:** {ctx.want()}\n"
    dod.write_dod(STORY_ISSUE, "dod", refresh=True, rules_from=[1201, 1200])
    assert line in ctx.text() and ctx.text().count("Rules reviewed") == 1
    dod.write_dod(STORY_ISSUE, "dod", refresh=True)  # rules off: the line is kept too
    assert line in ctx.text()


def test_missing_ledger_exit_2(ctx, capsys):
    ctx.ledger.unlink()
    code, out, raw, err = ctx.run(capsys)
    assert code == 2 and out is None and err == f"error: {rules.CONFIRM_NO_LEDGER}\n" and not ctx.ledger.exists()


def test_crlf_ledger_keeps_its_terminator_on_insert_and_on_replace(ctx, capsys, monkeypatch):
    ctx.put(ctx.text().replace("\n", "\r\n"))
    before = ctx.ledger.read_bytes()
    assert ctx.run(capsys)[0] == 0
    after = ctx.ledger.read_bytes()
    assert after == before.replace(b"- **Rules from:** 1201, 1200\r\n", b"- **Rules from:** 1201, 1200\r\n- **Rules reviewed:** " + ctx.want().encode() + b"\r\n")
    assert after.count(b"\n") == after.count(b"\r\n")
    ctx.put(ctx.text().replace(f"BR-1201-1@{ctx.h['BR-1201-1']}", "BR-1201-1@aaaaaaaa"))
    before = ctx.ledger.read_bytes()
    terminal(monkeypatch)
    assert ctx.run(capsys)[0] == 0
    assert ctx.ledger.read_bytes() == before.replace(b"BR-1201-1@aaaaaaaa", f"BR-1201-1@{ctx.h['BR-1201-1']}".encode())
    assert ctx.ledger.read_bytes().count(b"\n") == ctx.ledger.read_bytes().count(b"\r\n")


def test_refresh_after_a_crlf_confirm_keeps_the_line(ctx, capsys):
    ctx.put(ctx.text().replace("\n", "\r\n"))
    assert ctx.run(capsys)[0] == 0
    dod.write_dod(STORY_ISSUE, "dod", refresh=True, rules_from=[1201, 1200])
    assert f"- **Rules reviewed:** {ctx.want()}\n" in ctx.text()


def test_a_symlinked_ledger_is_refused_and_nothing_is_written(ctx, capsys):
    real = ctx.root / "real_ledger.md"
    real.write_bytes(ctx.ledger.read_bytes())
    ctx.ledger.unlink()
    ctx.ledger.symlink_to(real)
    before = real.read_bytes()
    code, out, raw, err = ctx.run(capsys)
    assert code == 2 and err == f"error: {rules.CONFIRM_LEDGER}\n" and real.read_bytes() == before and ctx.ledger.is_symlink() and leftovers(ctx) == []


def test_a_hard_linked_or_non_regular_ledger_is_refused(ctx, capsys):
    os.link(ctx.ledger, ctx.root / "other_name.md")
    assert ctx.run(capsys)[0] == 2 and "Rules reviewed" not in ctx.text()
    os.unlink(ctx.root / "other_name.md")
    ctx.ledger.unlink()
    ctx.ledger.mkdir()
    code, _, _, err = ctx.run(capsys)
    assert code == 2 and err == f"error: {rules.CONFIRM_LEDGER}\n"


def test_a_rule_whose_text_no_longer_matches_its_heading_hash_is_not_recordable(ctx, capsys):
    path = ctx.root / "dod" / "Rules_1201.md"
    path.write_text(path.read_text(encoding="utf-8").replace("Customers earn 1 points per euro.", "Customers earn 100 points per euro."), encoding="utf-8")
    before = ctx.text()
    code, out, raw, err = ctx.run(capsys)
    assert code == 2 and raw == "" and ctx.text() == before
    assert err == "error: " + rules.CONFIRM_HASH.format(ids="BR-1201-1") + "\n" and "100 points" not in err


def test_an_invalid_or_missing_rules_file_is_refused(ctx, capsys):
    before = ctx.text()
    path = ctx.root / "dod" / "Rules_1200.md"
    good = path.read_text(encoding="utf-8")
    path.write_text(good + "\n<!-- hide -->\n", encoding="utf-8")
    assert ctx.run(capsys)[3] == f"error: {rules.CONFIRM_FILES}\n"
    path.write_text(good.replace("rules-format: 2", "rules-format: 3"), encoding="utf-8")
    assert ctx.run(capsys)[3] == f"error: {rules.CONFIRM_FILES}\n"
    path.unlink()
    code, _, _, err = ctx.run(capsys)
    assert code == 2 and err == f"error: {rules.CONFIRM_FILES}\n" and ctx.text() == before


def test_a_symlinked_rules_file_is_refused(ctx, capsys):
    path = ctx.root / "dod" / "Rules_1200.md"
    real = ctx.root / "copy.md"
    real.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(real)
    assert ctx.run(capsys)[3] == f"error: {rules.CONFIRM_FILES}\n" and "Rules reviewed" not in ctx.text()


def test_an_unreadable_decisions_file_is_refused(ctx, capsys):
    (ctx.root / "dod" / "Decisions_1001.md").mkdir()
    code, _, _, err = ctx.run(capsys)
    assert code == 2 and err == f"error: {rules.CONFIRM_DECISIONS}\n" and "Rules reviewed" not in ctx.text()


@pytest.mark.parametrize("name,edit", [
    ("opener in the title hides the first heading", lambda md: md.replace("# DoD — #1001: ", "# DoD — #1001: x <!-- ", 1)),
    ("a fence hides the first heading", lambda md: md.replace("\n- **Issue:**", "\n```\n- **Issue:**", 1)),
    ("two Rules reviewed lines", lambda md: md.replace(FROM_LINE, FROM_LINE + "- **Rules reviewed:** none\n- **Rules reviewed:** none\n")),
    ("a malformed Rules reviewed line", lambda md: md.replace(FROM_LINE, FROM_LINE + "- **Rules reviewed:** BR-1201-1 deadbeef\n")),
    ("a quoted Rules reviewed line", lambda md: md.replace(FROM_LINE, FROM_LINE + "> - **Rules reviewed:** none\n")),
    ("a repeated id", lambda md: md.replace(FROM_LINE, FROM_LINE + "- **Rules reviewed:** BR-1201-1@aaaaaaaa, BR-1201-1@bbbbbbbb\n")),
    ("a malformed Rules from line", lambda md: md.replace(FROM_LINE, "- **Rules from:** 1201,1200\n")),
    ("two Rules from lines", lambda md: md.replace(FROM_LINE, FROM_LINE + FROM_LINE)),
])
def test_an_untrusted_or_malformed_header_is_refused(ctx, capsys, monkeypatch, name, edit):
    ctx.put(edit(ctx.text()))
    before = ctx.ledger.read_bytes()
    for tty in (False, True):
        if tty:
            terminal(monkeypatch)
        code, out, raw, err = ctx.run(capsys)
        assert code == 2 and err == f"error: {rules.CONFIRM_HEADER}\n" and ctx.ledger.read_bytes() == before, name
    assert leftovers(ctx) == []


def test_a_ledger_without_rules_from_is_refused(ctx, capsys):
    ctx.put(ctx.text().replace(FROM_LINE, ""))
    code, _, _, err = ctx.run(capsys)
    assert code == 2 and err == f"error: {rules.CONFIRM_NO_FROM}\n" and "Rules reviewed" not in ctx.text()


def test_a_forged_line_in_section_1_is_not_the_header(ctx, capsys):
    ctx.put(ctx.text().replace("### Description\n", "### Description\n- **Rules reviewed:** BR-1201-1@aaaaaaaa\n"))
    code, out, _, _ = ctx.run(capsys)
    assert code == 0 and out["added"] == ["BR-1200-1", "BR-1201-1"] and ctx.value() == ctx.want()


def test_ghpconfig_set_is_refused(ctx, capsys, monkeypatch, tmp_path_factory):
    other = tmp_path_factory.mktemp("elsewhere") / "c.json"
    other.write_text(json.dumps({"owner": "octo", "repo": "demo", "dod_path": "elsewhere"}))
    monkeypatch.setenv("GHP_CONFIG", str(other))
    code, _, _, err = ctx.run(capsys)
    assert code == 2 and err == f"error: {rules.CONFIRM_ENV}\n" and "Rules reviewed" not in ctx.text()


def test_the_confirm_code_covers_the_ledger_and_the_value(ctx):
    ctx.record(f"BR-1200-1@{ctx.h['BR-1200-1']}, BR-1201-1@aaaaaaaa")
    a = rules.prepare_confirm("dod", 1001)
    assert a["needs_human"] and a["changed"] == ["BR-1201-1"] and a["code"] == rules.prepare_confirm("dod", 1001)["code"]
    ctx.put(ctx.text().replace("Last synced:** ", "Last synced:** x"))
    assert rules.prepare_confirm("dod", 1001)["code"] != a["code"]


# ---- the typed approval code (the same mechanism as `ghp stack approve`) ----

def stale(ctx):
    ctx.record(f"BR-1200-1@{ctx.h['BR-1200-1']}, BR-1201-1@aaaaaaaa")
    return ctx.ledger.read_bytes()


@pytest.mark.parametrize("answer", ["deadbeef0000\n", "\n", "", "yes\n", KeyboardInterrupt])
def test_a_wrong_or_missing_code_confirms_nothing(ctx, capsys, monkeypatch, answer):
    before = stale(ctx)
    terminal(monkeypatch, answer)
    code, out, raw, err = ctx.run(capsys)
    assert code == 2 and err == f"error: {stack.E_NOT_CONFIRMED}\n" and ctx.ledger.read_bytes() == before and leftovers(ctx) == []


def test_the_code_is_never_read_from_a_pipe(ctx, capsys, monkeypatch):
    before = stale(ctx)

    class Piped(TypedStdin):
        def isatty(self):
            return False
    monkeypatch.setattr(sys, "stdin", Piped())
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    code, _, raw, err = ctx.run(capsys)
    assert code == 2 and err == f"error: {refusal(changed='BR-1201-1')}\n" and "approval code" not in raw and ctx.ledger.read_bytes() == before


def test_stdout_must_be_a_terminal_too(ctx, capsys, monkeypatch):
    before = stale(ctx)
    monkeypatch.setattr(sys, "stdin", TypedStdin())
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    code, _, raw, err = ctx.run(capsys)
    assert code == 2 and err == f"error: {refusal(changed='BR-1201-1')}\n" and "approval code" not in raw and ctx.ledger.read_bytes() == before


def test_the_ledger_edited_between_the_prompt_and_the_code_is_refused(ctx, capsys, monkeypatch):
    before = stale(ctx)

    def edit(code):
        ctx.ledger.write_bytes(before.replace(b"Last synced:** ", b"Last synced:** x"))
    terminal(monkeypatch, None, before=edit)
    code, _, _, err = ctx.run(capsys)
    assert code == 2 and err == f"error: {rules.CONFIRM_SWAPPED}\n" and b"BR-1201-1@aaaaaaaa" in ctx.ledger.read_bytes()


def test_a_rules_file_edited_between_the_prompt_and_the_code_is_refused(ctx, capsys, monkeypatch):
    before = stale(ctx)
    path = ctx.root / "dod" / "Rules_1201.md"

    def edit(code):
        path.write_text(path.read_text(encoding="utf-8").replace("Customers earn 1 points per euro.", "Customers earn 100 points per euro."), encoding="utf-8")
    terminal(monkeypatch, None, before=edit)
    code, _, _, err = ctx.run(capsys)
    assert code == 2 and err.startswith("error: ") and "nothing" in err and ctx.ledger.read_bytes() == before


def test_the_ledger_replaced_by_a_symlink_before_the_write_is_refused(ctx, monkeypatch):
    before = stale(ctx)
    prep = rules.prepare_confirm("dod", 1001)
    real = ctx.root / "elsewhere.md"
    real.write_bytes(before)
    ctx.ledger.unlink()
    ctx.ledger.symlink_to(real)
    with pytest.raises(ValueError) as e:
        prep["write"]()
    assert str(e.value) == rules.CONFIRM_SWAPPED and real.read_bytes() == before and ctx.ledger.is_symlink() and leftovers(ctx) == []


def test_a_write_error_has_one_fixed_text(ctx, monkeypatch):
    before = stale(ctx)
    prep = rules.prepare_confirm("dod", 1001)
    monkeypatch.setattr(stack, "_atomic_replace", lambda *a, **k: (_ for _ in ()).throw(ValueError(stack._E_BACK)))
    with pytest.raises(ValueError) as e:
        prep["write"]()
    assert str(e.value) == rules.CONFIRM_WRITE and ctx.ledger.read_bytes() == before


def test_the_text_about_to_be_renamed_is_checked(ctx, monkeypatch):
    stale(ctx)
    prep = rules.prepare_confirm("dod", 1001)
    seen = {}
    orig = stack._atomic_replace

    def spy(path, text, check=None, expect=None):
        seen["check"] = check
        return orig(path, text, check, expect)
    monkeypatch.setattr(stack, "_atomic_replace", spy)
    prep["write"]()
    good = ctx.ledger.read_bytes()
    seen["check"](good)
    for bad in (good.replace(b"BR-1201-1@", b"BR-1201-2@"), good.replace(b"Rules from:** 1201, 1200", b"Rules from:** 1201"),
                good.replace(b"Last synced:** ", b"Last synced:** x")):
        with pytest.raises(ValueError) as e:
            seen["check"](bad)
        assert str(e.value) == rules.CONFIRM_WRITE


@pytest.mark.parametrize("eol", ["\r", "\u2028"])
def test_an_unusual_line_terminator_on_the_line_is_refused(ctx, capsys, eol):
    ctx.put(ctx.text().replace(FROM_LINE, "- **Rules from:** 1201, 1200" + eol + "- **Note:** x\n"))
    before = ctx.ledger.read_bytes()
    code, _, _, err = ctx.run(capsys)
    assert code == 2 and err.startswith("error: ") and ctx.ledger.read_bytes() == before


def test_early_typed_code_is_flushed_and_the_same_code_typed_after_the_prompt_confirms(ctx):
    stale(ctx)
    before = ctx.ledger.read_bytes()
    early = rules.prepare_confirm("dod", 1001)["code"]
    code, screen, err = run_in_pty(CMD, ctx.root, early=early, answer="")  # then end of input at the prompt
    assert code == 2 and stack.E_NOT_CONFIRMED in err and ctx.ledger.read_bytes() == before, (screen, err)
    code, screen, err = run_in_pty(CMD, ctx.root)
    assert code == 0 and ctx.value() == ctx.want(), (screen, err)
    assert "BR-1201-1" in screen and "aaaaaaaa" in screen and "Customers earn" not in screen


def test_a_real_process_without_terminals_refuses_a_re_confirmation(ctx):
    stale(ctx)
    before = ctx.ledger.read_bytes()
    for kw in ({"stdout_pty": False}, {"stdin_pty": False}, {"stdout_pty": False, "stdin_pty": False}):
        code, screen, err = run_in_pty(CMD, ctx.root, **kw)
        assert code == 2 and refusal(changed="BR-1201-1") in err and "approval code" not in screen and ctx.ledger.read_bytes() == before, kw
    code, screen, err = run_in_pty(CMD, ctx.root, answer="not-the-code")
    assert code == 2 and ctx.ledger.read_bytes() == before


def test_a_real_process_without_terminals_still_adds_new_ids(ctx):
    code, screen, err = run_in_pty(CMD, ctx.root, stdout_pty=False, stdin_pty=False)
    assert code == 0 and ctx.value() == ctx.want() and '"written": true' in screen, (screen, err)


def test_confirm_makes_no_request_and_needs_no_token(ctx, monkeypatch):
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    assert ctx.m.main(CMD, gh=NoGh()) == 0


def test_help_and_parser_for_confirm(capsys):
    from ghp.__main__ import main
    with pytest.raises(SystemExit):
        main(["rules", "--help"])
    assert "--confirm" in capsys.readouterr().out
    for argv in (["rules", "--confirm"], ["rules", "1001", "--confirm", "--apply"]):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert e.value.code == 2, argv


def test_run_in_pty_kills_a_child_that_waits_forever_within_the_timeout():
    import time
    start = time.monotonic()
    for kw in ({}, {"stdout_pty": False}):
        with pytest.raises(pytest.fail.Exception) as e:
            run_in_pty([], ".", timeout=2, argv=[sys.executable, "-c", "import time; time.sleep(60)"], **kw)
        assert "did not finish within 2 s and was killed" in str(e.value)
    assert time.monotonic() - start < 15


def test_the_new_line_takes_the_terminator_of_the_files_first_line(ctx, capsys, monkeypatch):
    mixed = ctx.text().replace("\n", "\r\n", 1)  # the first line is CRLF, every other line LF
    ctx.put(mixed)
    assert ctx.run(capsys)[0] == 0
    want = f"- **Rules reviewed:** {ctx.want()}"
    assert ctx.ledger.read_bytes() == mixed.replace(FROM_LINE, FROM_LINE + want + "\r\n").encode()
    stale_line = f"- **Rules reviewed:** BR-1200-1@{ctx.h['BR-1200-1']}, BR-1201-1@aaaaaaaa\n"  # an LF line in a file whose first line is CRLF
    ctx.put(ctx.text().replace(want + "\r\n", stale_line))
    before = ctx.text()
    terminal(monkeypatch)
    assert ctx.run(capsys)[0] == 0
    assert ctx.ledger.read_bytes() == before.replace(stale_line, want + "\r\n").encode()  # rewritten with the first line's terminator, the rest as it was
    lf = mixed.replace("\r\n", "\n", 1)  # the reverse: first line LF, the Rules from line CRLF
    ctx.put(lf.replace(FROM_LINE, FROM_LINE.replace("\n", "\r\n")))
    assert ctx.run(capsys)[0] == 0
    assert ctx.ledger.read_bytes() == lf.replace(FROM_LINE, FROM_LINE.replace("\n", "\r\n") + want + "\n").encode()


def test_json_carries_locators_and_the_notes_count(tmp_path, monkeypatch, capsys):
    html = ("## Business rules\nIntro ZZINTRO prose.\nR1: ZZSECRET\nStatus: approved\n" + desc((1, "approved"))[len("## Business rules\n"):]
            + "Rule 2 - ZZSECRET\nStatus approved\n")
    m, gh = env(tmp_path, monkeypatch, [STORY, wi(1201, "Feature", html)], {"enabled": True})
    code = m.main(["rules", "1001"], gh=gh)
    raw = capsys.readouterr().out
    out = json.loads(raw)
    assert code == 0 and "ZZSECRET" not in raw and "ZZINTRO" not in raw
    assert out["notes"] == 1
    probs = [(p["issue"], p["after"]) for p in out["problems"] if "after" in p]
    assert probs == [("unparsed-rule-text", rules.FIRST), ("bad-rule-line", "BR-1201-1"), ("bad-status-line", "BR-1201-1")]
    assert all(p["rule"] is None and p["level"] == "error" and p["id"] == 1201 for p in out["problems"] if "after" in p)
    text = (tmp_path / "dod" / "Rules_1201.md").read_text(encoding="utf-8")
    assert "## Notes" in text and "ZZSECRET" not in text and "ZZINTRO" not in text
    pf = rules.parse_file(text, 1201)
    assert not pf["invalid"] and pf["notes"] == [rules.NOTE_INTRO] and sorted(pf["rules"]) == ["BR-1201-1"]


# ---- P3-13c: a confirm without a terminal records the free new ids even when another id changed or was dropped ----

EPIC3 = wi(1200, "Epic", desc((1, "approved"), (2, "approved"), (3, "retired")), title="Loyalty programme")  # three approved rules in scope: BR-1200-1, BR-1200-2, BR-1201-1


@pytest.fixture
def ctx3(tmp_path, monkeypatch, capsys):
    m, gh = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC3], {"enabled": True})
    assert m.main(["rules", "1001"], gh=gh) == 0
    capsys.readouterr()
    dod.write_dod(STORY_ISSUE, "dod", rules_from=[1201, 1200])
    return Ctx(tmp_path, m)


def rec(ctx, *ids, **other):
    """`Rules reviewed:` text: the current hash of each id in `ids`, then `id=hash` pairs from `other` (keys with `_` for `-`)."""
    return ", ".join([f"{r}@{ctx.h[r]}" for r in ids] + [f"{r.replace('_', '-')}@{h}" for r, h in other.items()])


def test_no_terminal_one_changed_two_new_records_the_two_new_and_refuses_the_changed(ctx3, capsys):
    ctx3.record("BR-1201-1@aaaaaaaa")
    before = ctx3.text()
    code, out, raw, err = ctx3.run(capsys)
    assert code == 2 and out is None and raw == ""
    assert err == f"error: {refusal(recorded='BR-1200-1, BR-1200-2', changed='BR-1201-1')}\n"
    assert "aaaaaaaa" not in err and ctx3.h["BR-1201-1"] not in err and "Customers earn" not in err
    assert ctx3.value() == rec(ctx3, "BR-1200-1", "BR-1200-2", BR_1201_1="aaaaaaaa")  # the old record untouched, the two new ids in
    assert dod.ledger_rules_reviewed(ctx3.text())["hashes"] == {"BR-1200-1": ctx3.h["BR-1200-1"], "BR-1200-2": ctx3.h["BR-1200-2"], "BR-1201-1": "aaaaaaaa"}
    assert [x for x in ctx3.text().splitlines() if "Rules reviewed" not in x] == [x for x in before.splitlines() if "Rules reviewed" not in x]  # only that line differs
    assert leftovers(ctx3) == []


def test_the_free_part_is_one_atomic_write(ctx3, capsys, monkeypatch):
    ctx3.record("BR-1201-1@aaaaaaaa")
    calls = []
    real = stack._atomic_replace
    monkeypatch.setattr(stack, "_atomic_replace", lambda *a, **k: calls.append(a[0]) or real(*a, **k))
    assert ctx3.run(capsys)[0] == 2
    assert len(calls) == 1


def test_no_terminal_a_dropped_id_and_a_new_id(ctx3, capsys):
    ctx3.record(f"BR-1200-1@{ctx3.h['BR-1200-1']}, BR-1200-9@bbbbbbbb, BR-1201-1@{ctx3.h['BR-1201-1']}")  # BR-1200-9 is in no file; BR-1200-2 is new
    code, _, raw, err = ctx3.run(capsys)
    assert code == 2 and raw == "" and err == f"error: {refusal(recorded='BR-1200-2', removed='BR-1200-9')}\n"
    assert ctx3.value() == rec(ctx3, "BR-1200-1", "BR-1200-2", BR_1200_9="bbbbbbbb", BR_1201_1=ctx3.h["BR-1201-1"])


def test_no_terminal_changed_and_dropped_with_nothing_new_writes_nothing(ctx3, capsys):
    ctx3.record(f"BR-1200-1@{ctx3.h['BR-1200-1']}, BR-1200-2@{ctx3.h['BR-1200-2']}, BR-1201-1@aaaaaaaa, BR-1200-9@bbbbbbbb")
    before, st = ctx3.ledger.read_bytes(), ctx3.ledger.stat()
    code, _, _, err = ctx3.run(capsys)
    assert code == 2 and err == f"error: {refusal(changed='BR-1201-1', removed='BR-1200-9')}\n"
    assert ctx3.ledger.read_bytes() == before and ctx3.ledger.stat().st_ino == st.st_ino and ctx3.ledger.stat().st_mtime_ns == st.st_mtime_ns


def test_with_a_terminal_and_the_code_everything_is_recorded(ctx3, capsys, monkeypatch):
    ctx3.record("BR-1201-1@aaaaaaaa")
    stdin = terminal(monkeypatch)
    code, out, raw, err = ctx3.run(capsys)
    assert code == 0 and err == "" and stdin.lines == 1
    assert ctx3.value() == rec(ctx3, "BR-1200-1", "BR-1200-2", "BR-1201-1")
    assert json.loads(raw[raw.index("{"):]) == {"id": 1001, "reviewed": ctx3.value(), "added": ["BR-1200-1", "BR-1200-2"], "changed": ["BR-1201-1"], "removed": [], "written": True}


@pytest.mark.parametrize("answer", ["deadbeef0000\n", "", KeyboardInterrupt])
def test_a_wrong_code_records_nothing_not_even_the_new_ids(ctx3, capsys, monkeypatch, answer):
    ctx3.record("BR-1201-1@aaaaaaaa")
    before = ctx3.ledger.read_bytes()
    terminal(monkeypatch, answer)
    code, _, _, err = ctx3.run(capsys)
    assert code == 2 and err == f"error: {stack.E_NOT_CONFIRMED}\n" and ctx3.ledger.read_bytes() == before and leftovers(ctx3) == []


def test_an_untrusted_or_malformed_header_writes_nothing_even_for_new_ids(ctx3, capsys):
    ctx3.record("BR-1201-1@aaaaaaaa")
    bad = ctx3.text().replace("BR-1201-1@aaaaaaaa", "BR-1201-1@aaaaaaaa, BR-1201-1@aaaaaaaa")  # a duplicate id: malformed
    ctx3.put(bad)
    code, _, raw, err = ctx3.run(capsys)
    assert code == 2 and err == f"error: {rules.CONFIRM_HEADER}\n" and raw == "" and ctx3.text() == bad
    ctx3.put(ctx3.text().replace("- **Rules from:** 1201, 1200\n", "<!--\n- **Rules from:** 1201, 1200\n-->\n- **Rules from:** 1201, 1200\n", 1))
    before = ctx3.ledger.read_bytes()
    assert ctx3.run(capsys)[0] == 2 and ctx3.ledger.read_bytes() == before


def test_a_hash_mismatch_rule_stops_everything_new_ids_included(ctx3, capsys):
    ctx3.record("BR-1201-1@aaaaaaaa")
    f = ctx3.root / "dod" / "Rules_1200.md"
    f.write_text(f.read_text(encoding="utf-8").replace("Customers earn 1 points per euro.", "Customers earn nothing."), encoding="utf-8")
    before = ctx3.ledger.read_bytes()
    code, _, raw, err = ctx3.run(capsys)
    assert code == 2 and "BR-1200-1" in err and raw == "" and ctx3.ledger.read_bytes() == before


def test_crlf_ledger_keeps_its_terminators_in_the_free_write(ctx3, capsys):
    md = ctx3.text().replace("\n", "\r\n")
    ctx3.put(md)
    ctx3.put(ctx3.text().replace(FROM_LINE.replace("\n", "\r\n"), FROM_LINE.replace("\n", "\r\n") + "- **Rules reviewed:** BR-1201-1@aaaaaaaa\r\n"))
    code, _, _, err = ctx3.run(capsys)
    assert code == 2 and "BR-1200-1" in err
    raw = ctx3.ledger.read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n") and ctx3.value() == rec(ctx3, "BR-1200-1", "BR-1200-2", BR_1201_1="aaaaaaaa")


def test_the_refusal_text_is_fixed_and_lists_ids_only(ctx3):
    assert "{recorded}" in rules.CONFIRM_TERMINAL and "{changed}" in rules.CONFIRM_TERMINAL and "{removed}" in rules.CONFIRM_TERMINAL
    prep = {"added": [f"BR-1-{n}" for n in range(1, 26)], "changed": ["BR-1201-1"], "removed": []}
    text = rules.confirm_refusal(prep)
    assert "and 5 more" in text and "BR-1-20" in text and "BR-1-21" not in text and "Changed since confirmed: BR-1201-1" in text and "Dropped: none" in text
    assert "own terminal" in text and "not a security boundary" in text


def test_a_real_process_without_terminals_records_the_new_ids_and_exits_2(ctx3):
    base = ctx3.text()
    for kw in ({"stdout_pty": False}, {"stdin_pty": False}, {"stdout_pty": False, "stdin_pty": False}):
        ctx3.put(base)
        ctx3.record("BR-1201-1@aaaaaaaa")
        code, screen, err = run_in_pty(CMD, ctx3.root, **kw)
        assert code == 2 and refusal(recorded="BR-1200-1, BR-1200-2", changed="BR-1201-1") in err, kw
        assert "approval code" not in screen and ctx3.value() == rec(ctx3, "BR-1200-1", "BR-1200-2", BR_1201_1="aaaaaaaa"), kw
