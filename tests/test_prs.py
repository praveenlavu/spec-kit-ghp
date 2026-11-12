"""T21: `ghp pr create` and `ghp.prs`, ported from ado tests/test_prs.py (rename table; the tracker seam is `FakeGh`; the Azure Repos tests, the
`pr comment` verb and the ADO artifact-link repair are dropped with their code, D9). New for GitHub: the `Closes #N` body, the base-branch warning,
creation through `Gh.rest` (one POST, never repeated; an unknown outcome re-reads the PR list), the plan digest and the escapes.

Dropped from ado (reason; owner): test_azure_create_payload, test_azure_comment_payload, test_open_apply_azure_posts,
test_comment_azure_and_github, test_azure_description_capped_at_4000, test_azure_description_at_limit_untouched, test_github_body_not_capped
(there is no Azure description cap; the 60,000 cap has its own tests), test_azure_plan_shows_truncated_description, test_pr_allow_incomplete_azure_is_draft,
test_pr_apply_repairs_a_dropped_work_item_link, test_ensure_links_* (5 tests, ADO artifact links), test_azure_pr_plan_says_the_story_link_may_be_added,
test_pr_command_doc_stop_and_offer_rule (needs commands/pr.md: T32), test_github_args_list_form and test_open_apply_github_calls_gh (`gh pr create`
argv; replaced by the REST POST tests), test_github_gets_the_body_that_was_approved_not_a_swapped_file and
test_github_body_reaches_gh_as_utf8_bytes (re-written for the REST body on stdin below), test_hostile_title_goes_to_gh_as_one_argv_item
(there is no title in argv any more: test_a_hostile_title_travels_only_in_the_json_body_never_in_argv), test_expect_plan_matches_a_long_plan (ado's `_plan`
internals; the same end to end below). The comment-mode wiring (`--check-committed`,
approvals.require, review-review) is T28's; strict mode is T23's."""
import json
import os
import subprocess
import time
from argparse import Namespace
from pathlib import Path

import pytest

import ghp.__main__ as cli
from conftest import FakeGh, FakeRunner, commit, git, http
from ghp import comments, dod, issues, plan as write_gate, prs
from ghp.gh import Gh, GhError, GhTimeout, WriteNotArmed
from ghp.config import Config
from ghp.refs import Ref
from rules_support import raw_issue, serve


# ---- helpers: ledgers (ado conftest ledger_md / review_md), a config, a fake GitHub ----
def review_md(sha, blocks="no", superseded=False, blocker=0, major=0):
    tail = " (superseded)" if superseded else ""
    verdict = "yes — `review_block_on` = [blocker] and 1 such finding(s) remain" if blocks == "yes" else "no"
    return (f"### Review — 2026-10-28 — `main...feat` @ `{sha}`{tail}\n\n"
            f"- **Confirmed by severity:** blocker {blocker} · major {major} · minor 0\n"
            f"- **Blocks PR:** {verdict}\n\n")


def ledger_md(*blocks, spec_dir="specs/001-x"):
    return (f"# DoD\n- **Spec dir:** {spec_dir}\n\n{dod.SECTION2}\n\n## 3. AC\n\n"
            "## 4. Gate & review log  (appended by speckit.ghp.gate and speckit.ghp.review)\n\n"
            + "".join(blocks) + "## 5. Checklist\n- [ ] x\n")


PULLS = "repos/octo/demo/pulls"
LIST = "repos/octo/demo/pulls?head=octo:%s&base=%s&state=open&per_page=100"


def raw_pr(n=5, head="feat", base="main", state="open", draft=False, full="octo/demo", title=None, body=None):
    return {"title": title, "body": body, "number": n, "html_url": f"https://github.com/octo/demo/pull/{n}", "state": state, "draft": draft,
            "head": {"ref": head, "repo": {"full_name": full}}, "base": {"ref": base}}


def posts(gh):
    return [c for c in gh.calls if c[0] == "rest" and c[1] == "POST"]


def world(numbers=(7,), default="main", existing=None, head="feat", base="main", post=None, children=None, state="open", gh=None):
    """A FakeGh for `pr create`: the issues, the repository, the PR list of the head, and the POST (default: echoes what was asked)."""
    gh = serve(*[raw_issue(n, state=state) for n in numbers], gh=gh or FakeGh())
    gh.on_rest("GET", "repos/octo/demo", {"default_branch": default})
    gh.on_rest("GET", LIST % (head, base), [] if existing is None else [existing])
    gh.on_rest("POST", PULLS, post or (lambda b: raw_pr(5, b["head"], b["base"], draft=b["draft"])))
    if children is not None:
        gh.on_rest("GET", "repos/octo/demo/issues/%d/sub_issues?per_page=100&page=1" % numbers[0], children)
    return gh


def env(tmp_path, monkeypatch, body="Body text\n", scan_commits=False, **cfg):
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", **cfg}))
    (tmp_path / "body.md").write_text(body, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    monkeypatch.setattr(prs, "_head_commit", lambda head: "")  # most tests fake every subprocess; the head-commit tests keep the real lookup
    if not scan_commits:  # most tests run in a plain directory with no git history; the commit-message tests (T21a) read a real repository
        monkeypatch.setattr(prs, "commit_messages", lambda base, head, repo=".": [])
    monkeypatch.setattr(issues, "_nap", lambda s: None)


ARGS = ["pr", "create", "7", "--title", "Add x", "--body-file", "body.md", "--base", "main", "--head", "feat"]


def no_subprocess(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("subprocess.run must not be called")
    monkeypatch.setattr(subprocess, "run", boom)
    # the comment-mode gate reads the target branch's config with git (T28); these tests are about the GitHub door, in terminal mode
    from ghp import approvals
    from ghp.config import Trust
    if not getattr(approvals.target_trust, "keep", False):
        monkeypatch.setattr(approvals, "target_trust", lambda repo=".", base=None: Trust([], "terminal", {}))


def prepared(tmp_path, args=None, body="Prepared body\n"):
    (tmp_path / ".ghp").mkdir(exist_ok=True)
    (tmp_path / ".ghp" / "pr-args.json").write_text(json.dumps(args or {"title": "Add x", "base": "main", "head": "feat"}))
    (tmp_path / ".ghp" / "pr-body.md").write_text(body, encoding="utf-8")


def sent_body(gh):
    return posts(gh)[0][3]["body"]


# ---- AC1/AC2/AC6: the title, the body, the keyword ----
def test_title_gets_ref():
    assert prs.with_ref("Add x", 1001) == "Add x #1001"
    assert prs.with_ref("Add #1001 x", 1001) == "Add #1001 x"
    assert prs.with_ref("Add #10011", 1001) == "Add #10011 #1001"
    assert prs.with_ref("(#1001)", 1001) == "(#1001)"
    assert prs.with_ref("C#1001", 1001) == "C#1001 #1001" and prs.with_ref("a&#1001;", 1001) == "a&#1001; #1001"  # a word or an HTML entity is no reference


def test_multiple_issues_repeat_the_keyword():
    assert prs.closes_line([1]) == "Closes #1"
    assert prs.closes_line([1, 2]) == "Closes #1, closes #2"
    assert prs.closes_line([3, 1, 2]) == "Closes #3, closes #1, closes #2"
    title, body, _ = prs.compose("Add x", "Text\n", [1, 2], "SUMMARY")
    assert title == "Add x #1 #2" and body == "Text\n\nSUMMARY\n\nCloses #1, closes #2\n"


def test_pr_body_has_closes_and_ref(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch)
    (tmp_path / "dod").mkdir()
    (tmp_path / "dod" / "DoD_7.md").write_text(ledger_md().replace("- [ ] x", "- [x] a\n- [ ] b\n- [x] c") + "")
    gh = world()
    assert cli.main(ARGS + ["--apply"], gh=gh) == 0
    (call,) = posts(gh)
    assert call[2] == PULLS
    payload = call[3]
    assert payload["title"] == "Add x #7" and payload["head"] == "feat" and payload["base"] == "main" and payload["draft"] is False
    assert payload["body"] == ("Body text\n\n**Ledger summary** (dod/DoD_7.md): 0 acceptance criteria; DoD checklist 2 of 3 ticked.\n\n"
                               "Closes #7\n")
    assert "created PR #5: https://github.com/octo/demo/pull/5" in capsys.readouterr().out


def test_two_issues_close_with_one_keyword_each(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    gh = world((1, 2))
    assert cli.main(["pr", "create", "1", "2", *ARGS[3:], "--apply"], gh=gh) == 0
    payload = posts(gh)[0][3]
    assert payload["title"] == "Add x #1 #2" and payload["body"].endswith("Closes #1, closes #2\n")


def test_the_ledger_summary_copies_no_ledger_text(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    (tmp_path / "dod").mkdir()
    evil = "# DoD — #7: @victim <script>\n" + ledger_md("EVIL @victim https://evil.example\n")
    (tmp_path / "dod" / "DoD_7.md").write_text(evil)
    gh = world()
    assert cli.main(ARGS + ["--apply"], gh=gh) == 0
    body = sent_body(gh)
    assert "victim" not in body and "evil" not in body.lower() and "<script>" not in body and "@" not in body


def test_an_unclosed_fence_in_the_ledger_is_named_not_summarised(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    (tmp_path / "dod").mkdir()
    (tmp_path / "dod" / "DoD_7.md").write_text("```\nopen fence\n" + ledger_md())
    gh = world()
    assert cli.main(ARGS + ["--apply"], gh=gh) == 0
    assert "unclosed code fence or comment; not summarised" in sent_body(gh)


def test_mentions_in_the_title_and_body_are_defanged_and_counted(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch, body="Thanks @alice and @​bob\n")
    gh = world()
    args = ARGS[:4] + ["Add @carol's x"] + ARGS[5:]
    assert cli.main(args + ["--apply"], gh=gh) == 0
    payload = posts(gh)[0][3]
    assert "@" not in payload["title"] + payload["body"] and payload["title"].startswith("Add [mention removed]'s x")
    assert "3 @mention(s) were removed" in capsys.readouterr().out


@pytest.mark.parametrize("text", [
    "Closes #42", "closed #42", "CLOSE #42", "fix #42", "Fixes #42", "fixed #42", "RESOLVE #42", "resolves #42", "Resolved #42",
    "Fixes:#42", "Closes: #42", "Closes:   #42", "Closes:\n#42", "closes\n\n#42", "fixes#42", "Closes\t#42",
    "Resolves octo/demo#42", "Closes: octo/demo#42", "Fixes OCTO/Demo#42",
    "Closes https://github.com/octo/demo/issues/42", "fixes http://github.com/octo/demo/issues/42", "Resolves https://www.github.com/octo/demo/pull/42",
    "Closes: https://github.com/octo/demo/issues/42",
    "<!-- Closes #42 -->", "<!--\nfixes #42\n-->", "Closes <!-- hidden --> #42", "text <!-- never closed\nCloses #42",
    "```\nFixes #42\n```", "> Closes #42", "`closes #42`", "x" * 5 + " Closes #42 " + "y" * 5, "éfixes #42",
])
def test_every_closing_form_is_read(text):
    assert prs.closing_targets(text, "octo", "demo") == {("octo", "demo", 42)} or {(o.lower(), r.lower(), n) for o, r, n in prs.closing_targets(text, "octo", "demo")} == {("octo", "demo", 42)}
    assert prs.other_closes([text], "octo", "demo", [7]) == ["octo/demo#42"] or prs.other_closes([text], "octo", "demo", [7])[0].lower() == "octo/demo#42"
    assert prs.other_closes([text], "octo", "demo", [42]) == []  # the intended issue is not "other"


R2_FORMS = [  # (text, the targets it names beyond nothing intended); every string is from the re-review, none is known to close on GitHub
    ("**Closes** #99", ["octo/demo#99"]), ("Closes **#1**", ["octo/demo#1"]), ("Closes _#1_", ["octo/demo#1"]), ("Closes ~~#1~~", ["octo/demo#1"]),
    ("Closes <b>#1</b>", ["octo/demo#1"]), ("Closes** #1**", ["octo/demo#1"]), ("Closes (#1)", ["octo/demo#1"]), ("Closes [#1]", ["octo/demo#1"]),
    ("Closes `#1`", ["octo/demo#1"]),
    ("Closes &#35;99", ["octo/demo#99"]), ("Closes &num;1", ["octo/demo#1"]), ("Closes&#x23;1", ["octo/demo#1"]), ("Closes #&#49;", ["octo/demo#1"]),
    ("&#67;loses #1", ["octo/demo#1"]), ("Closes&nbsp;#1", ["octo/demo#1"]), ("Closes&#32;#1", ["octo/demo#1"]), ("Closes \\#99", ["octo/demo#99"]),
    ("Closes &amp;#35;1", ["octo/demo#1"]), ("Closes &amp;amp;#35;1", ["octo/demo#1"]),
    ("Closes <https://github.com/octo/demo/issues/99>", ["octo/demo#99"]), ("Closes www.github.com/octo/demo/issues/99", ["octo/demo#99"]),
    ("Closes github.com/o/r/issues/1", ["o/r#1"]), ("Closes [link](https://github.com/o/r/issues/1)", ["o/r#1"]),
    ("Closes https://github.com/o/r/pull/1", ["o/r#1"]), ("Closes GH-99", ["octo/demo#99"]), ("Closes gh-1", ["octo/demo#1"]),
    ("Closes #099", ["octo/demo#99"]), ("Closes #007", []), ("Closes #042", ["octo/demo#42"]),
    ("Closes https://github.com/o/r/issues/007", ["o/r#7"]),
    ("Closes jdoe_acme/r#1", ["jdoe_acme/r#1"]), ("Closes a--b/r#1", ["a--b/r#1"]), ("Closes a-/r#1", ["a-/r#1"]), ("Closes o.w/r.x#1", ["o.w/r.x#1"]),
    ("Closes\u200b#1", ["octo/demo#1"]), ("Closes \u200b#1", ["octo/demo#1"]), ("Closes\u200b #1", ["octo/demo#1"]), ("Closes #\u200b1", ["octo/demo#1"]),
    ("Clo\u200bses #1", ["octo/demo#1"]), ("Clo\u00adses #1", ["octo/demo#1"]), ("Clo\u202eses #1", ["octo/demo#1"]),
    ("Clo<!-- -->ses #1", ["octo/demo#1"]), ("Closes <!-- x --> #1", ["octo/demo#1"]), ("Closes #<!---->1", ["octo/demo#1"]),
    ("Closes : #1", ["octo/demo#1"]), ("Closes - #1", ["octo/demo#1"]), ("Closes -> #1", ["octo/demo#1"]), ("Closes\u2014#1", ["octo/demo#1"]),
    ("fixed-#42", ["octo/demo#42"]), ("\uff23loses #1", ["octo/demo#1"]),   # a fullwidth C folds to C
    ("Closes #9, #1", ["octo/demo#1", "octo/demo#9"]), ("Closes #9 and #1", ["octo/demo#1", "octo/demo#9"]),
    ("Closes #9/#1", ["octo/demo#1", "octo/demo#9"]), ("Closes #9 #1", ["octo/demo#1", "octo/demo#9"]),
    ("Closes #7, #99", ["octo/demo#99"]), ("Closes #9, a/b#8, GH-7 and https://github.com/c/d/issues/6", ["a/b#8", "c/d#6", "octo/demo#9"]),
    ("Closes " + ", ".join("#%d" % n for n in range(100, 140)), ["octo/demo#%d" % n for n in range(100, 140)]),
    ("This fixes the flow described in section #3?", ["octo/demo#3"]),   # prose is flagged on purpose: the user rewords or passes --allow-extra-closes
]


@pytest.mark.parametrize("text,expected", R2_FORMS)
def test_closing_forms_that_markup_entities_and_lists_could_hide_are_read(text, expected):
    got = prs.other_closes([text], "octo", "demo", [7])
    assert [g.lower() for g in got] == sorted(e.lower() for e in expected)


def test_a_zero_width_character_cannot_hide_a_reference_in_the_plan_either(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch, body="Closes\u200b #99\n")
    gh = world()
    assert cli.main(ARGS + ["--apply"], gh=gh) == 2 and gh.calls == []


@pytest.mark.parametrize("dod_path", ["Fixes #99", "Closes octo/demo#99", "resolves https://github.com/o/r/issues/5"])
def test_the_ledger_summary_is_scanned_like_the_rest_of_the_sent_body(tmp_path, monkeypatch, capsys, dod_path):
    env(tmp_path, monkeypatch, dod_path=dod_path)
    gh = world()
    assert cli.main(ARGS + ["--apply"], gh=gh) == 2
    assert "E_BODY_CLOSES_OTHERS" in capsys.readouterr().err and gh.calls == []


def test_the_scan_covers_what_is_sent_not_what_was_given(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch, body="Closes @alice #99\n")  # the mention is defanged, the keyword still reaches #99
    gh = world()
    assert cli.main(ARGS + ["--apply"], gh=gh) == 2 and gh.calls == []


def test_our_own_lines_and_ordinary_prose_without_a_keyword_are_never_extra():
    for numbers, line in (([7], "Closes #7"), ([7, 8], "Closes #7, closes #8"), ([1, 2, 3], "Closes #1, closes #2, closes #3")):
        assert prs.closes_line(numbers) == line
        _, body, _ = prs.compose("Add x", "Some text about #99 and #98\n", numbers, "**Ledger summary** (dod/DoD_7.md): 3 acceptance criteria; DoD checklist 1 of 4 ticked.")
        assert prs.other_closes([prs.compose("Add x", "", numbers, "S")[0], prs.without_generated(body, numbers)], "octo", "demo", numbers) == []
        assert prs.without_generated(body, numbers).endswith("ticked.\n\n") and line not in prs.without_generated(body, numbers)
    assert prs.other_closes(["Closes #7, closes #8"], "octo", "demo", [7, 8]) == []
    assert prs.other_closes(["Closes #7, closes #8"], "octo", "demo", [7]) == ["octo/demo#8"]


def test_character_references_that_never_settle_are_refused_not_ignored():
    deep = "Closes &" + "amp;" * 40 + "#35;1"
    assert prs.other_closes([deep], "octo", "demo", [7]) == ["character references nested too deeply to read"]
    assert prs.other_closes(["Closes &amp;#35;1"], "octo", "demo", [7]) == ["octo/demo#1"]


@pytest.mark.parametrize("text", ["Closes in the morning", "disclosed #42", "prefixes #42", "refix #42", "see #42", "Closes # 42", "closes #0",
                                  "Closes https://github.com/octo/demo/issues/", "Closes https://evil.example/octo/demo/issues/42",
                                  "closes " + "x" * 80 + " #42", "closes #", "Closes GH-", "Closes gh-x", "closes 42", "the closest #42", "fixture #42"])
def test_text_that_is_no_closing_form_is_not_read(text):
    assert prs.closing_targets(text, "octo", "demo") == set()


def test_a_cross_repository_target_is_never_the_intended_one_and_is_named():
    assert prs.other_closes(["Closes evil/x#7"], "octo", "demo", [7]) == ["evil/x#7"]  # same number as ours, other repository
    assert prs.other_closes(["fixes other/repo#99"], "octo", "demo", [7]) == ["other/repo#99"]
    assert prs.other_closes(["Closes https://github.com/evil/x/issues/7"], "octo", "demo", [7]) == ["evil/x#7"]
    assert prs.other_closes(["Closes OCTO/DEMO#7", "Closes #7", "closes https://github.com/octo/demo/pull/7"], "octo", "demo", [7]) == []
    assert prs.other_closes(["Closes #9", "Closes #8, closes #9"], "octo", "demo", [7]) == ["octo/demo#8", "octo/demo#9"]


def test_a_closing_keyword_for_another_issue_is_refused_with_nothing_sent(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch, body="Fixes #99 and resolves octo/demo#98, `closes #97`\n```\nfixes #96\n```\nCloses #7\n")
    gh = world()
    assert cli.main(ARGS + ["--apply"], gh=gh) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: E_BODY_CLOSES_OTHERS:") and all("octo/demo#%d" % n in err for n in (96, 97, 98, 99)) and "octo/demo#7," not in err
    assert gh.calls == [] and not gh.armed and err.count("\n") == 1
    assert cli.main(ARGS, gh=gh) == 2 and gh.calls == []  # a preview refuses too: there is nothing to approve


@pytest.mark.parametrize("where", ["title", "body", "hidden", "url"])
def test_the_title_the_body_and_hidden_text_are_all_scanned(tmp_path, monkeypatch, where):
    body = {"body": "Resolves evil/x#7\n", "hidden": "ok\n<!-- closes #42 -->\n", "url": "Closes https://github.com/octo/demo/issues/42\n"}.get(where, "ok\n")
    env(tmp_path, monkeypatch, body=body)
    title = "Fixes #42: add x" if where == "title" else "Add x"
    gh = world()
    assert cli.main(ARGS[:4] + [title] + ARGS[5:] + ["--apply"], gh=gh) == 2 and gh.calls == []


def test_the_flag_accepts_the_extra_closes_shows_them_and_binds_them_in_the_digest(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch, body="Also fixes #99\n")
    assert cli.main(ARGS + ["--plan-out", "p.md"], gh=world()) == 2
    assert cli.main(ARGS + ["--allow-extra-closes", "--plan-out", "p.md"], gh=world()) == 0
    plan = (tmp_path / "p.md").read_text()
    assert "\u26a0 the title or body would also close octo/demo#99 on merge (accepted with --allow-extra-closes)" in plan
    gh = world()
    assert cli.main(ARGS + ["--allow-extra-closes", "--apply", "--expect-plan", "p.md"], gh=gh) == 0 and len(posts(gh)) == 1
    assert digest(prs.render_plan("T", "b", "main", "feat", [1], allow_extra=True)) != digest(prs.render_plan("T", "b", "main", "feat", [1]))


def test_our_own_closes_line_and_a_plain_body_are_not_extra(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch, body="see #99 for background\nCloses #7\nfixes octo/demo#7 and GH-7 and #007\n")
    assert cli.main(ARGS + ["--apply"], gh=world()) == 0


# ---- AC4: preview sends nothing; apply posts once through the arming gate ----
def test_pr_create_dry_run_sends_nothing(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch)
    gh = world()
    no_subprocess(monkeypatch)
    assert cli.main(ARGS + ["--plan-out", "out/plan.md"], gh=gh) == 0
    out = capsys.readouterr().out
    assert not gh.armed and posts(gh) == [] and all(c[1] == "GET" for c in gh.calls)
    plan = (tmp_path / "out" / "plan.md").read_text()
    assert plan + "\n" == out and "| title | Add x #7 |" in plan and "Closes #7" in plan and "| existing PR | none |" in plan


def test_apply_posts_exactly_once_after_the_gate_armed_it(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    gh = world()
    no_subprocess(monkeypatch)  # no `gh pr create`, no git: the one door is Gh.rest
    assert cli.main(ARGS + ["--apply"], gh=gh) == 0
    assert gh.armed and len(posts(gh)) == 1


def test_create_pr_is_refused_by_an_unarmed_gh_with_no_call():
    gh = world()
    with pytest.raises(WriteNotArmed):
        prs.create_pr(gh, Config("octo", "demo"), "t", "b", "main", "feat", False)
    assert gh.calls == []


def test_the_real_transport_gets_the_body_as_utf8_json_on_stdin_and_no_gh_pr_create(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch, body="Change → done \U0001F680 café\n")
    read = lambda n: http(200, raw_issue(n))  # noqa: E731
    runner = FakeRunner(read(7), http(200, {"default_branch": "main"}), http(200, []),
                        http(201, raw_pr(5)))
    assert cli.main(ARGS + ["--apply"], gh=Gh(runner=runner, sleep=lambda s: None)) == 0
    argv, stdin = runner.calls[-1]["argv"], runner.calls[-1]["stdin"]
    assert argv[:5] == ["gh", "api", "-X", "POST", PULLS] and "pr" not in argv[:2]
    sent = json.loads(stdin.decode("ascii"))
    assert sent["body"].startswith("Change → done \U0001F680 café\n") and sent["head"] == "feat"
    assert [c["argv"][3] for c in runner.calls] == ["GET", "GET", "GET", "POST"]


def test_a_hostile_title_travels_only_in_the_json_body_never_in_argv(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    title = 'x $(touch pwned) `touch pwned2` "q" ; rm -rf ~'
    runner = FakeRunner(http(200, raw_issue(7)), http(200, {"default_branch": "main"}), http(200, []), http(201, raw_pr(5)))
    assert cli.main(ARGS[:4] + [title] + ARGS[5:] + ["--apply"], gh=Gh(runner=runner, sleep=lambda s: None)) == 0
    assert all(title not in " ".join(c["argv"]) and "touch" not in " ".join(c["argv"]) for c in runner.calls)
    assert json.loads(runner.calls[-1]["stdin"].decode("ascii"))["title"] == title + " #7" and not (tmp_path / "pwned").exists()


def test_the_body_is_read_once_a_file_swapped_after_the_plan_is_never_sent(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    real = write_gate.arm_or_refuse

    def swap_then_arm(args, gh, rendered, approve=None):
        real(args, gh, rendered, approve)
        (tmp_path / "body.md").write_text("SWAPPED after the plan\n", encoding="utf-8")
    monkeypatch.setattr(prs.write_gate, "arm_or_refuse", swap_then_arm)
    gh = world()
    assert cli.main(ARGS + ["--apply"], gh=gh) == 0
    assert "Body text" in sent_body(gh) and "SWAPPED" not in sent_body(gh)


# ---- AC5: the base is not the default branch ----
def test_non_default_base_warns_that_closes_is_ignored(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch)
    gh = world(base="release/1.0")
    assert cli.main(ARGS[:-4] + ["--base", "release/1.0", "--head", "feat", "--plan-out", "p.md"], gh=gh) == 0
    plan = (tmp_path / "p.md").read_text()
    assert ("⚠ base 'release/1.0' is not the default branch 'main': Closes keywords are ignored; `ghp done` will close it") in plan
    capsys.readouterr()
    assert cli.main(ARGS + ["--plan-out", "q.md"], gh=world()) == 0
    assert "default branch" not in (tmp_path / "q.md").read_text()


def test_a_different_default_branch_name_is_compared_exactly(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    assert cli.main(ARGS + ["--plan-out", "p.md"], gh=world(default="Main")) == 0
    assert "is not the default branch 'Main'" in (tmp_path / "p.md").read_text()


# ---- an open PR already exists / an unknown outcome is re-read, never repeated ----
def test_an_existing_open_pr_for_the_head_means_nothing_is_created(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch)
    gh = world(existing=raw_pr(9))
    assert cli.main(ARGS + ["--plan-out", "p.md"], gh=gh) == 0
    assert "| existing PR | #9 is already open for this head and base; nothing will be created |" in (tmp_path / "p.md").read_text()
    capsys.readouterr()
    assert cli.main(ARGS + ["--apply"], gh=gh) == 0
    assert gh.armed and posts(gh) == [] and "nothing sent: PR #9 is already open" in capsys.readouterr().out


def test_a_pr_from_a_fork_or_another_base_is_not_ours(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    gh = world()
    gh.on_rest("GET", LIST % ("feat", "main"), [raw_pr(9, full="other/demo"), raw_pr(10, base="dev"), raw_pr(11, state="closed")])
    assert cli.main(ARGS + ["--apply"], gh=gh) == 0 and len(posts(gh)) == 1


def test_two_open_prs_for_the_same_head_and_base_are_ambiguous(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch)
    gh = world()
    gh.on_rest("GET", LIST % ("feat", "main"), [raw_pr(9), raw_pr(10)])
    assert cli.main(ARGS + ["--apply"], gh=gh) == 2
    assert "E_PR_AMBIGUOUS" in capsys.readouterr().err and not gh.armed and posts(gh) == []


@pytest.mark.parametrize("failure", [lambda b: (_ for _ in ()).throw(GhTimeout(write=True)),
                                     lambda b: (_ for _ in ()).throw(GhError("E_GH_HTTP", 502, "Bad Gateway")),
                                     lambda b: {"unexpected": "reply"},
                                     lambda b: raw_pr(5, head="another-branch")])
def test_an_unknown_outcome_rereads_and_uses_the_pr_it_finds(tmp_path, monkeypatch, capsys, failure):
    env(tmp_path, monkeypatch)
    asked = {}

    def post(b):
        asked.update(b)
        return failure(b)
    gh = world(post=post)
    reads = iter(range(10))
    # the plan's read, then a lagging re-read, then the PR with what was asked
    gh.on_rest("GET", LIST % ("feat", "main"), lambda _: [] if next(reads) < 2 else [raw_pr(5, title=asked["title"], body=asked["body"])])
    assert cli.main(ARGS + ["--apply"], gh=gh) == 0
    assert len(posts(gh)) == 1  # the write is never repeated
    assert "found PR #5 (opened before the error; nothing sent again)" in capsys.readouterr().out


@pytest.mark.parametrize("change", [{"title": "Another title"}, {"body": "someone else's text"}, {"draft": True}])
def test_a_pr_found_on_reread_that_is_not_the_approved_one_is_reported_not_claimed(tmp_path, monkeypatch, capsys, change):
    env(tmp_path, monkeypatch)
    asked = {}

    def post(b):
        asked.update(b)
        raise GhTimeout(write=True)
    gh = world(post=post)
    reads = iter(range(10))
    gh.on_rest("GET", LIST % ("feat", "main"), lambda _: [] if next(reads) < 1 else [raw_pr(5, **{"title": asked["title"], "body": asked["body"].replace("\n", "\r\n"), **change})])
    assert cli.main(ARGS + ["--apply"], gh=gh) == 1
    out = capsys.readouterr().out
    assert "not the approved ones" in out and "found PR #5" not in out and len(posts(gh)) == 1


def test_an_unknown_outcome_with_no_pr_found_raises_with_a_note_and_sends_nothing_again(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch)
    waits = []
    monkeypatch.setattr(issues, "_nap", waits.append)
    gh = world(post=lambda b: (_ for _ in ()).throw(GhTimeout(write=True)))
    assert cli.main(ARGS + ["--apply"], gh=gh) == 1
    err = capsys.readouterr().err
    assert "outcome unknown" in err and "nothing was retried" in err and err.count("\n") == 1
    assert len(posts(gh)) == 1 and waits == list(issues.REREAD_WAITS)


def test_a_rejected_create_is_not_reread_and_not_repeated(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch)
    gh = world(post=lambda b: (_ for _ in ()).throw(GhError("E_GH_HTTP", 422, "Validation Failed")))
    assert cli.main(ARGS + ["--apply"], gh=gh) == 1
    assert len(posts(gh)) == 1 and "E_GH_HTTP (HTTP 422)" in capsys.readouterr().err
    assert gh.calls[-1][1] == "POST"  # nothing is read or sent after the rejected POST


def test_a_failing_reread_exits_1_with_the_original_unknown_outcome(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch)
    gh = world(post=lambda b: (_ for _ in ()).throw(GhTimeout(write=True)))
    answers = iter([[], GhError("E_GH_HTTP", 500, "boom")])

    def listing(_):
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a
    gh.on_rest("GET", LIST % ("feat", "main"), listing)
    assert cli.main(ARGS + ["--apply"], gh=gh) == 1
    assert "E_GH_TIMEOUT" in capsys.readouterr().err and len(posts(gh)) == 1


def test_a_crlf_body_file_found_on_reread_is_the_approved_pr(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch)
    (tmp_path / "body.md").write_bytes(b"line one\r\nline two   \r\n")
    asked = {}

    def post(b):
        asked.update(b)
        raise GhTimeout(write=True)
    gh = world(post=post)
    reads = iter(range(10))
    for stored in ("lf", "crlf", "trimmed"):
        def listing(_, stored=stored):
            if next(reads) < 1:
                return []
            body = asked["body"]
            body = body.replace("\r\n", "\n") if stored == "lf" else body.replace("\r\n", "\n").replace("\n", "\r\n") if stored == "crlf" else "\n".join(l.rstrip() for l in body.split("\n")).rstrip("\n")
            return [raw_pr(5, title=asked["title"], body=body)]
        gh.on_rest("GET", LIST % ("feat", "main"), listing)
        reads = iter(range(10))
        assert cli.main(ARGS + ["--apply"], gh=gh) == 0, stored
        assert "found PR #5" in capsys.readouterr().out


def test_same_text_ignores_only_line_ends_and_trailing_blanks():
    assert prs._same_text("a\r\nb  \r\n", "a\nb\n\n") and prs._same_text("a\rb", "a\nb")
    assert not prs._same_text("a b", "a  b") and not prs._same_text(" a", "a") and not prs._same_text("a\n\nb", "a\nb")


# ---- the inputs: closed issues, pull requests, strict mode ----
def test_a_closed_issue_is_warned(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    assert cli.main(ARGS + ["--plan-out", "p.md"], gh=world(state="closed")) == 0
    assert "⚠ #7 is already closed" in (tmp_path / "p.md").read_text()


def test_a_pull_request_number_is_refused_before_anything_is_planned(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch)
    gh = world()
    gh.on_rest("GET", "repos/octo/demo/issues/7", {**raw_issue(7), "pull_request": {"url": "x"}})
    assert cli.main(ARGS + ["--apply"], gh=gh) == 2
    assert "E_NOT_AN_ISSUE" in capsys.readouterr().err and not gh.armed and posts(gh) == []


def test_the_same_issue_twice_is_refused(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    gh = world()
    assert cli.main(["pr", "create", "7", "#7", *ARGS[3:]], gh=gh) == 2 and gh.calls == []


# T23: `test_strict_mode_is_refused_until_the_strict_checks_exist` is gone with E_STRICT_UNBUILT; strict `pr create` is tested in test_strict_wire.py.


def test_the_head_branch_tied_to_another_issue_is_warned(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    gh = world(head="feature/gh12-other")
    assert cli.main(ARGS[:-2] + ["--head", "feature/gh12-other", "--plan-out", "p.md"], gh=gh) == 0
    assert "the head branch is tied to #12, which this PR does not close" in (tmp_path / "p.md").read_text()


def test_open_errors_exit_2(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch)
    gh = world()
    assert cli.main(["pr", "create", "7", "--title", "t", "--body-file", "nope.md", "--base", "a", "--head", "b"], gh=gh) == 2
    assert "cannot read the body file nope.md" in capsys.readouterr().err
    assert cli.main(["pr", "create", "7", "--title", "t", "--body-file", "body.md", "--base", "a"], gh=gh) == 2  # all four are required
    assert gh.calls == []
    with pytest.raises(SystemExit) as e:  # an action is required
        cli.main(["pr"], gh=gh)
    assert e.value.code == 2


def test_a_body_file_that_is_a_symlink_or_outside_the_repo_is_not_read(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch)
    secret = tmp_path.parent / (tmp_path.name + "-secret.txt")
    secret.write_text("TOP SECRET\n")
    (tmp_path / "link.md").symlink_to(secret)
    gh = world()
    for path in ("link.md", str(secret)):
        assert cli.main(ARGS[:6] + [path] + ARGS[7:] + ["--plan-out", "p.md"], gh=gh) == 2
        assert "TOP SECRET" not in capsys.readouterr().err and not (tmp_path / "p.md").exists()
    assert gh.calls == []


@pytest.mark.parametrize("bad", ["x" * 300, "two\nlines", "bidi ‮ text", "", " padded "])
def test_a_title_that_is_not_one_printable_line_is_refused(tmp_path, monkeypatch, bad):
    env(tmp_path, monkeypatch)
    gh = world()
    assert cli.main(ARGS[:4] + [bad] + ARGS[5:], gh=gh) == 2 and not gh.armed and posts(gh) == []
    assert gh.calls == [] or all(c[1] == "GET" for c in gh.calls)


def test_a_body_over_the_limit_is_refused_not_cut(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch, body="y" * 60000)
    gh = world()
    assert cli.main(ARGS + ["--apply"], gh=gh) == 2
    assert "E_BODY_TOO_LONG" in capsys.readouterr().err and posts(gh) == []
    title, body, _ = prs.compose("t", "z" * 59000, [7], "S")
    assert len(body) <= prs.MAX_BODY


# ---- prepared arguments ----
def test_open_from_prepared(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch)
    prepared(tmp_path)
    gh = world()
    assert cli.main(["pr", "create", "7", "--from-prepared", "--plan-out", ".ghp/plan-pr.md"], gh=gh) == 0
    assert posts(gh) == []
    plan = (tmp_path / ".ghp" / "plan-pr.md").read_text()
    assert "Add x #7" in plan and "Prepared body" in plan
    assert cli.main(["pr", "create", "7", "--from-prepared", "--apply"], gh=gh) == 0
    payload = posts(gh)[0][3]
    assert payload["title"] == "Add x #7" and payload["body"].startswith("Prepared body\n") and (payload["head"], payload["base"]) == ("feat", "main")


def test_from_prepared_errors_exit_2(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch)
    gh = world()
    assert cli.main(["pr", "create", "7", "--from-prepared"], gh=gh) == 2  # nothing prepared
    assert ".ghp/pr-args.json" in capsys.readouterr().err
    prepared(tmp_path, {"title": "t", "base": "main"})  # no head
    assert cli.main(["pr", "create", "7", "--from-prepared"], gh=gh) == 2
    (tmp_path / ".ghp" / "pr-args.json").write_text(json.dumps({"title": "t", "base": "main", "head": "-x; rm"}))
    assert cli.main(["pr", "create", "7", "--from-prepared"], gh=gh) == 2  # option-like head refused
    (tmp_path / ".ghp" / "pr-args.json").write_text("[1, 2]")
    assert cli.main(["pr", "create", "7", "--from-prepared"], gh=gh) == 2
    for extra in (["--title", "t"], ["--body-file", "body.md"], ["--base", "a"], ["--head", "b"]):
        assert cli.main(["pr", "create", "7", "--from-prepared"] + extra, gh=gh) == 2
    assert cli.main(["pr", "create", "7"], gh=gh) == 2  # explicit flags required without --from-prepared
    assert cli.main(["pr", "create", "7", "--push", "--title", "t", "--body-file", "body.md", "--base", "a", "--head", "b"], gh=gh) == 2
    assert gh.calls == []


def test_a_prepared_file_that_is_a_symlink_is_not_read(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch)
    prepared(tmp_path)
    (tmp_path / ".ghp" / "pr-body.md").unlink()
    secret = tmp_path.parent / (tmp_path.name + "-key.txt")
    secret.write_text("TOP SECRET\n")
    (tmp_path / ".ghp" / "pr-body.md").symlink_to(secret)
    assert cli.main(["pr", "create", "7", "--from-prepared"], gh=world()) == 2
    assert "TOP SECRET" not in capsys.readouterr().err


def test_push_is_list_form_and_only_with_apply(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch)
    prepared(tmp_path)
    gh = world()
    seen = []

    def run(args, **kw):
        seen.append((args, kw))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    no_subprocess(monkeypatch)  # the target-branch trust read (T28) stubbed: only the push is a subprocess here
    monkeypatch.setattr(subprocess, "run", run)
    assert cli.main(["pr", "create", "7", "--from-prepared", "--push"], gh=gh) == 0  # dry run: no push, and the plan says it would
    assert seen == [] and "| push | yes: git push -u origin feat, before the PR is created |" in capsys.readouterr().out
    assert cli.main(["pr", "create", "7", "--from-prepared", "--push", "--apply"], gh=gh) == 0
    assert seen[0][0] == ["git", "push", "-u", "origin", "feat"] and seen[0][1].get("shell") is None and len(posts(gh)) == 1
    # a failed push stops before the PR is created, and a url's credentials never reach the output
    seen.clear()
    capsys.readouterr()
    gh = world()
    err = "fatal: unable to access 'https://user:ghp_" + "A" * 36 + "@github.com/octo/demo.git/': denied"
    monkeypatch.setattr(subprocess, "run", lambda a, **k: (seen.append(a), subprocess.CompletedProcess(a, 1, "", err))[1])
    assert cli.main(["pr", "create", "7", "--from-prepared", "--push", "--apply"], gh=gh) == 1
    out = capsys.readouterr().out
    assert len(seen) == 1 and posts(gh) == [] and "denied" in out and "ghp_" + "A" * 36 not in out and "user:" not in out


def test_push_is_skipped_when_a_pr_is_already_open(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    prepared(tmp_path)
    no_subprocess(monkeypatch)
    assert cli.main(["pr", "create", "7", "--from-prepared", "--push", "--apply"], gh=world(existing=raw_pr(9))) == 0


def test_plan_out_contains_full_body(tmp_path, monkeypatch):
    long_body = "".join(f"line {i} of the body\n" for i in range(40))
    env(tmp_path, monkeypatch, body=long_body)
    assert len(long_body) > 120
    assert cli.main(ARGS + ["--plan-out", "p1.md"], gh=world()) == 0
    plan = (tmp_path / "p1.md").read_text()
    assert "## PR body (as it will be sent" in plan and long_body.rstrip("\n") in plan
    prepared(tmp_path, body=long_body)
    assert cli.main(["pr", "create", "7", "--from-prepared", "--plan-out", "p2.md"], gh=world()) == 0
    assert long_body.rstrip("\n") in (tmp_path / "p2.md").read_text()


@pytest.mark.parametrize("bad", ["+main", ":main", "a:b", "--force", "x..y", "a//b", "a/", "a.lock", "a@{u}", "a b", "", "-x", "a\nb", "é"])
def test_branch_names_strict(tmp_path, monkeypatch, bad):
    env(tmp_path, monkeypatch)
    gh = world()
    no_subprocess(monkeypatch)
    for key in ("head", "base"):
        prepared(tmp_path, {"title": "t", "base": "main", "head": "feat", key: bad})
        assert cli.main(["pr", "create", "7", "--from-prepared", "--push", "--apply"], gh=gh) == 2
    assert cli.main(["pr", "create", "7", "--title", "t", "--body-file", "body.md", "--base", "main", f"--head={bad}", "--apply"], gh=gh) == 2
    assert gh.calls == [] and not gh.armed
    with pytest.raises(ValueError):
        prs.create_pr(gh, Config("octo", "demo"), "t", "b", "main", bad, False)  # the library refuses too, before a call
    with pytest.raises(ValueError):
        prs.push_branch(bad)


def test_branch_name_feature_accepted(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    prepared(tmp_path, {"title": "t", "base": "release/1.0", "head": "feature/gh7-login"})
    assert cli.main(["pr", "create", "7", "--from-prepared", "--apply"], gh=world(base="release/1.0", head="feature/gh7-login")) == 0


# ---- unfinished work never reaches a normal PR ----
def spec(tmp_path, tasks_md):
    (tmp_path / "dod").mkdir(exist_ok=True)
    (tmp_path / "dod" / "DoD_7.md").write_text("- **Spec dir:** specs/001-x\n")
    (tmp_path / "specs" / "001-x").mkdir(parents=True, exist_ok=True)
    (tmp_path / "specs" / "001-x" / "tasks.md").write_text(tasks_md)


OPEN_TASKS = "- [x] T001 Done\n- [ ] T002 Open\n- [ ] T003 Open too\n"


@pytest.mark.parametrize("apply", [False, True])
def test_pr_refuses_unticked_tasks(tmp_path, monkeypatch, capsys, apply):
    env(tmp_path, monkeypatch)
    spec(tmp_path, OPEN_TASKS)
    gh = world()
    assert cli.main(ARGS + ["--plan-out", "out/plan.md"] + (["--apply"] if apply else []), gh=gh) == 2
    err = capsys.readouterr().err
    assert "T002, T003" in err and "T001" not in err
    assert not (tmp_path / "out").exists() and posts(gh) == [] and not gh.armed


def test_pr_allow_incomplete_is_a_draft(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    spec(tmp_path, OPEN_TASKS)
    gh = world()
    assert cli.main(ARGS + ["--allow-incomplete", "--plan-out", "plan.md"], gh=gh) == 0
    assert "| draft | yes — 2 unticked task(s): T002, T003 |" in (tmp_path / "plan.md").read_text()
    assert cli.main(ARGS + ["--allow-incomplete", "--apply"], gh=gh) == 0
    assert posts(gh)[0][3]["draft"] is True


def test_pr_all_ticked_is_not_draft(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    spec(tmp_path, "- [x] T001 Done\n")
    gh = world()
    assert cli.main(ARGS + ["--apply"], gh=gh) == 0
    assert posts(gh)[0][3]["draft"] is False


def test_pr_no_tasks_md_proceeds_with_not_checked_line(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    assert cli.main(ARGS + ["--plan-out", "plan.md"], gh=world()) == 0  # no ledger at all
    assert "| tasks | not checked (no tasks.md) |" in (tmp_path / "plan.md").read_text()
    (tmp_path / "dod").mkdir()
    (tmp_path / "dod" / "DoD_7.md").write_text("- **Spec dir:** specs/missing\n")  # ledger, but no tasks.md
    assert cli.main(ARGS + ["--plan-out", "plan2.md"], gh=world()) == 0
    assert "not checked (no tasks.md)" in (tmp_path / "plan2.md").read_text()


def test_pr_tasks_md_without_task_lines_is_not_all_ticked(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    spec(tmp_path, "# Tasks\n\nnothing parseable\n")
    assert cli.main(ARGS + ["--plan-out", "plan.md"], gh=world(children=[])) == 0
    plan = (tmp_path / "plan.md").read_text()
    assert "not checked (tasks.md lists no tasks)" in plan and "all 0" not in plan


def child(n, tid, state="open", reason=None):
    return {**raw_issue(n, labels=["ghp:task"], body=f"<!-- spec-kit-ghp:task:{tid} -->"), "state": state, "state_reason": reason}


def test_pr_refuses_when_tasks_md_is_empty_but_the_story_tracks_tasks(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch)
    spec(tmp_path, "")
    gh = world(children=[child(21, "T001"), child(22, "T002"), child(23, "T009", "closed", "not_planned")])
    assert cli.main(ARGS + ["--plan-out", "plan.md"], gh=gh) == 2
    err = capsys.readouterr().err
    assert "T001, T002" in err and "unticked" in err and "T009" not in err


def test_pr_empty_tasks_md_with_allow_incomplete_is_a_draft_that_says_why(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    spec(tmp_path, "")
    assert cli.main(ARGS + ["--allow-incomplete", "--plan-out", "plan.md"], gh=world(children=[child(21, "T001")])) == 0
    assert "tasks.md lists no tasks but 1 task sub-issue(s) are tracked (T001); was it emptied?" in (tmp_path / "plan.md").read_text()


def test_pr_empty_tasks_md_with_nothing_tracked_is_still_not_checked(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    spec(tmp_path, "")
    assert cli.main(ARGS + ["--plan-out", "plan.md"], gh=world(children=[])) == 0
    assert "not checked (tasks.md lists no tasks)" in (tmp_path / "plan.md").read_text()


def test_pr_a_failing_children_read_is_not_a_refusal(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    spec(tmp_path, "")
    gh = world()
    gh.on_rest("GET", "repos/octo/demo/issues/7/sub_issues?per_page=100&page=1", lambda _: (_ for _ in ()).throw(GhError("E_GH_HTTP", 500, "x")))
    assert cli.main(ARGS + ["--plan-out", "plan.md"], gh=gh) == 0


# ---- review_block_on is enforced by `pr create` itself ----
def test_pr_create_refuses_while_review_blocking_is_configured_and_no_review_exists(tmp_path, monkeypatch, capsys, git_env):
    env(tmp_path, monkeypatch, review_block_on=["blocker"])
    git(tmp_path, "init", "-q", "-b", "feat")
    commit(tmp_path, "base", {"README.md": "x\n"})
    gh = world()
    assert cli.main(ARGS + ["--apply"], gh=gh) == 2
    err = capsys.readouterr().err
    assert "refusing to open a PR: no review logged: feat has no committed ledger dod/DoD_7.md" in err and gh.calls == []


# ---- the plan: cells, escapes, digest, head commit, --expect-plan ----
def test_render_plan_single_line_cells():
    out = prs.render_plan("T|x\n#1001", "line1\x1b[2J\nline2 | y", "main", "feat", [1001])
    table, _, sent = out.partition("## PR body (as it will be sent")
    assert "| title | T\\|x\\n#1001 |" in table and all(l.startswith("|") for l in table.splitlines() if "line2" in l)
    assert "\x1b" not in out and "line1\\u001b[2J\nline2 | y" in sent  # the escape, not the control character, reaches the terminal


def test_pr_body_preview_escaped_once():
    plan = prs.render_plan("T", "a|b \\ c", "main", "feat", [1001])
    assert "| body | 7 chars: a\\|b \\\\ c |" in plan


def digest(plan):
    return next(l for l in plan.splitlines() if l.startswith("digest: "))


def test_plans_of_different_prs_never_look_the_same():
    base = dict(title="T", body="b", base="main", head="feat", numbers=[1])
    seen = {digest(prs.render_plan(**base))}
    for change in ({"body": "b⠀"}, {"body": "b\\u2800"}, {"body": "b "}, {"body": "b\r"}, {"title": "T⠀"}, {"head": "feat2"}, {"base": "dev"},
                   {"numbers": [2]}, {"numbers": [1, 2]}, {"draft": True}, {"head_sha": "a" * 40}, {"tasks_row": ("tasks", "x")}, {"warnings": ["w"]},
                   {"existing": (3, "u")}, {"push": True}):
        d = digest(prs.render_plan(**{**base, **change}))
        assert d not in seen
        seen.add(d)
    assert len(seen) == 16
    shown = lambda body: prs.render_plan(**{**base, "body": body}).split("\n\n## PR body")[0].split("digest")[0]  # noqa: E731
    assert shown("x⠀y") == shown("x⠀y") and prs.render_plan(**{**base, "body": "x⠀y"}) != prs.render_plan(**{**base, "body": "x\\u2800y"})


def test_head_commit_is_shown_only_as_a_full_commit_id():
    assert f"| head commit | {'a' * 40} |" in prs.render_plan("T", "b", "main", "feat", [1], head_sha="a" * 40)
    with pytest.raises(ValueError):
        prs.render_plan("T", "b", "main", "feat", [1], head_sha="abc")


def test_pr_apply_refuses_when_plan_changed(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch)
    prepared(tmp_path)
    gh = world()
    assert cli.main(["pr", "create", "7", "--from-prepared", "--plan-out", ".ghp/plan-pr.md"], gh=gh) == 0
    (tmp_path / ".ghp" / "pr-body.md").write_text("Body edited after approval\n", encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["pr", "create", "7", "--from-prepared", "--apply", "--expect-plan", ".ghp/plan-pr.md"], gh=gh) == 2
    assert "plan changed since approval" in capsys.readouterr().err and not gh.armed and posts(gh) == []
    assert "Body edited after approval" in (tmp_path / ".ghp" / "plan-pr.md.new").read_text()


def test_pr_apply_proceeds_when_plan_matches(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    prepared(tmp_path)
    gh = world()
    assert cli.main(["pr", "create", "7", "--from-prepared", "--plan-out", ".ghp/plan-pr.md"], gh=gh) == 0
    assert cli.main(["pr", "create", "7", "--from-prepared", "--apply", "--expect-plan", ".ghp/plan-pr.md"], gh=gh) == 0
    assert len(posts(gh)) == 1


def test_a_body_that_differs_only_by_an_invisible_character_refuses_the_apply(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch, body="same​\n")
    gh = world()
    assert cli.main(ARGS + ["--plan-out", "p.md"], gh=gh) == 0
    (tmp_path / "body.md").write_text("same‌\n", encoding="utf-8")  # two different invisible characters: the display shows both as escapes
    assert cli.main(ARGS + ["--apply", "--expect-plan", "p.md"], gh=gh) == 2 and not gh.armed


def test_an_empty_expect_plan_refuses_the_apply(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch)
    gh = world()
    assert cli.main(ARGS + ["--apply", "--expect-plan", ""], gh=gh) == 2 and not gh.armed and posts(gh) == []


def test_long_pr_plan_warns_first_line_and_keeps_full_text(tmp_path, monkeypatch):
    long_body = "".join(f"line {i}\n" for i in range(250))
    env(tmp_path, monkeypatch, body=long_body)
    assert cli.main(ARGS + ["--plan-out", "p.md"], gh=world()) == 0
    plan = (tmp_path / "p.md").read_text()
    first = plan.splitlines()[0]
    assert first.startswith("⚠ Preview longer than the approval gate can show (")
    assert "open p.md and read it in full" in first and f"({len(plan.splitlines()) - 1} lines)" in first
    assert "line 249" in plan and "Plan for pull request" in plan


def test_expect_plan_matches_a_long_plan(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch, body="".join(f"row {i}\n" for i in range(300)))
    assert cli.main(ARGS + ["--plan-out", "p.md"], gh=world()) == 0
    assert (tmp_path / "p.md").read_text().startswith("\u26a0 ")  # the approved file carries the warning line, and still matches
    gh = world()
    assert cli.main(ARGS + ["--apply", "--expect-plan", "p.md"], gh=gh) == 0 and len(posts(gh)) == 1
    (tmp_path / "body.md").write_text("".join(f"row {i}\n" for i in range(301)))
    assert cli.main(ARGS + ["--apply", "--expect-plan", "p.md"], gh=world()) == 2


def test_short_pr_plan_has_no_warning(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    assert cli.main(ARGS + ["--plan-out", "p.md"], gh=world()) == 0
    assert "⚠ Preview" not in (tmp_path / "p.md").read_text()


HOSTILE_TITLE = 'x $(touch pwned) `touch pwned2` "q" \'s\' ; rm -rf ~ | <b>'


def test_hostile_title_in_prepared_args_reaches_payload_unchanged_and_runs_nothing(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    prepared(tmp_path, {"title": HOSTILE_TITLE, "base": "main", "head": "feat"})
    no_subprocess(monkeypatch)
    gh = world()
    assert cli.main(["pr", "create", "7", "--from-prepared", "--apply"], gh=gh) == 0
    assert posts(gh)[0][3]["title"] == HOSTILE_TITLE + " #7"
    assert not (tmp_path / "pwned").exists() and not (tmp_path / "pwned2").exists()


# ---- head commit (real git) ----
def git_repo_with_head(tmp_path):
    git(tmp_path, "init", "-q", "-b", "feat")
    return commit(tmp_path, "docs: first", {"f.txt": "one\n"})


def test_the_plan_names_the_head_commit(tmp_path, monkeypatch, git_env):
    env(tmp_path, monkeypatch)
    monkeypatch.undo()  # keep the real head-commit lookup ...
    monkeypatch.setattr(prs, "commit_messages", lambda base, head, repo=".": [])  # (these repositories have no base branch to compare with)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    git_repo_with_head(tmp_path)
    full = git(tmp_path, "rev-parse", "refs/heads/feat")
    assert cli.main(ARGS + ["--plan-out", "plan.md"], gh=world()) == 0
    assert f"| head commit | {full} |" in (tmp_path / "plan.md").read_text()


def test_a_commit_added_after_approval_refuses_the_apply(tmp_path, monkeypatch, capsys, git_env):
    env(tmp_path, monkeypatch)
    monkeypatch.undo()
    monkeypatch.setattr(prs, "commit_messages", lambda base, head, repo=".": [])
    monkeypatch.chdir(tmp_path)
    git_repo_with_head(tmp_path)
    prepared(tmp_path)
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo"}))
    gh = world()
    assert cli.main(["pr", "create", "7", "--from-prepared", "--push", "--plan-out", ".ghp/plan-pr.md"], gh=gh) == 0
    commit(tmp_path, "feat: added while paused at the gate", {"f.txt": "two\n"})  # the human approved the plan above, not this commit
    real_run, seen = subprocess.run, []

    def run(args, *a, **k):
        if args[:2] == ["git", "push"]:
            seen.append(args)
            raise AssertionError("an unapproved commit must not be pushed")
        return real_run(args, *a, **k)
    monkeypatch.setattr(subprocess, "run", run)
    capsys.readouterr()
    assert cli.main(["pr", "create", "7", "--from-prepared", "--push", "--apply", "--expect-plan", ".ghp/plan-pr.md"], gh=gh) == 2
    assert "plan changed since approval" in capsys.readouterr().err and seen == [] and posts(gh) == [] and not gh.armed
    assert "| head commit |" in (tmp_path / ".ghp" / "plan-pr.md.new").read_text()


def test_an_unchanged_head_still_applies(tmp_path, monkeypatch, git_env):
    env(tmp_path, monkeypatch)
    monkeypatch.undo()
    monkeypatch.setattr(prs, "commit_messages", lambda base, head, repo=".": [])
    monkeypatch.chdir(tmp_path)
    git_repo_with_head(tmp_path)
    prepared(tmp_path)
    gh = world()
    assert cli.main(["pr", "create", "7", "--from-prepared", "--plan-out", ".ghp/plan-pr.md"], gh=gh) == 0
    assert cli.main(["pr", "create", "7", "--from-prepared", "--apply", "--expect-plan", ".ghp/plan-pr.md"], gh=gh) == 0 and len(posts(gh)) == 1


def test_the_body_limit_is_exact():
    overhead = len(prs.compose("t", "", [7], "S")[1])  # "S\n\nCloses #7\n"
    _, body, _ = prs.compose("t", "z" * (prs.MAX_BODY - overhead - 2), [7], "S")  # + the blank line between text and summary
    assert len(body) == prs.MAX_BODY
    with pytest.raises(prs.PrError, match="E_BODY_TOO_LONG"):
        prs.compose("t", "z" * (prs.MAX_BODY - overhead - 1), [7], "S")


def test_a_reply_that_names_another_repository_or_number_is_not_our_pr():
    cfg = Config("octo", "demo")
    assert prs._pr(raw_pr(5), cfg)["number"] == 5
    for bad in ({"html_url": "https://github.com/evil/demo/pull/5"}, {"html_url": "https://github.com/octo/other/pull/5"},
                {"html_url": "https://github.com/octo/demo/pull/6"}, {"html_url": "https://example.com/octo/demo/pull/5"}, {"number": True},
                {"number": "5"}, {"head": None}, {"base": {}}):
        with pytest.raises(GhError) as e:
            prs._pr({**raw_pr(5), **bad}, cfg)
        assert e.value.code == "E_GH_BAD_BODY"


def test_a_create_reply_for_another_repository_is_an_unknown_outcome_and_is_reread(tmp_path, monkeypatch, capsys):
    env(tmp_path, monkeypatch)
    gh = world(post=lambda b: {**raw_pr(5), "html_url": "https://github.com/evil/demo/pull/5"})
    assert cli.main(ARGS + ["--apply"], gh=gh) == 1 and len(posts(gh)) == 1
    assert "outcome unknown" in capsys.readouterr().err


def test_a_mention_in_the_ledger_path_is_defanged_in_the_summary(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch, dod_path="@team/dod")
    gh = world()
    assert cli.main(ARGS + ["--apply"], gh=gh) == 0
    body = sent_body(gh)
    assert "@" not in body and "no readable ledger [mention removed] for #7" in body


# ---- references in text; linear regexes ----
@pytest.mark.parametrize("text,expected", [
    ("Fixes #12", 12), ("Add x #7", 7), ("see #7 and #7 again", 7), ("#7", 7), ("(#7)", 7),
    ("see #7 and #8", None),                                            # two different issues are ambiguous
    ("```\n#7\n```\nnothing else", None), ("~~~\n#7\n~~~", None),       # code fences
    ("<!-- #7 -->", None), ("> quoted #7\nreal #8", 8),                  # comments and quotes
    ("use `#7` here", None), ("use `#7` and #8", 8),                     # code spans
    ("C#7", None), ("\u00e9#7", None), ("\u0665#7", None), ("a/#7", None), ("&#39;", None), ("#0", None), ("#07", None), ("#2147483648", None), ("#" + "9" * 5000, None),
    ("#2147483647", 2147483647), ("no reference", None), ("", None), (None, None), (7, None),
])
def test_issue_from_text(text, expected):
    assert prs.issue_from_text(text) == expected


def test_the_closing_scanner_is_linear_on_hostile_shapes():
    shapes = ["closes " * 100000, "closes #1 " * 100000, "Closes " + "#1, " * 100000, "Closes " + "&#35;" * 100000, "Closes &#35;1 " * 50000,
              "&amp;" * 60000 + "#35;1", "&" + "amp;" * 60000 + "#35;1", "closes" + " " * 300000 + "#1", "closes" + "\n" * 300000 + "#1",
              "fixes " + "a-" * 150000, "fixes " + "a/" * 150000 + "#1", ("fixes " + "a" * 39 + "/" + "b" * 100 + "#1 ") * 2000,
              "fixes https://www.github.com/" * 10000, "closes <!--" * 25000, "closes <!-- -->#1 " * 20000, "-->" * 100000, "closes #" + "9" * 300000,
              "closes #" + "0" * 300000 + "1", "clo\u200bses #1 " * 20000, "\ufdfa" * 16000 + " closes #1", "closes _#1_ " * 30000, "closes (" * 100000,
              "Fixes **#9**, [a](https://github.com/o/r/issues/1) &#35;7 " * 6000]
    start = time.monotonic()
    for shape in shapes:
        prs.other_closes([shape], "octo", "demo", [7])
    assert time.monotonic() - start < 10  # measured: the slowest shape 0.3 s, all together under 2 s
    assert prs.other_closes(["closes #1 " * 3], "octo", "demo", [1]) == []


def test_hostile_text_is_read_in_linear_time():
    probes = ["#" * 300000, "#1" * 150000, "`" * 300000, "`a" * 150000, "close" + " " * 300000, "closes:" + " " * 300000, "gh" + "9" * 300000,
              "#" + "9" * 300000, ">" * 300000, "```\n" * 75000, "<!--" * 75000, "feature/" * 40000, "fix " * 75000 + "#", "a/" * 150000 + "#1",
              "closes " * 40000, "closes: " * 40000, "closes a-" * 30000, "fixes a/" * 40000, "fixes " + "a." * 150000, "fixes " + "a" * 300000,
              "fixes https://github.com/" * 12000, "closes <!--" * 25000, "<!--" * 75000, "-->" * 100000, "<!-- -->" * 40000,
              "fixes " + "a-" * 150000 + "/x#1", "fixes " + "a" * 39 + "/" + "b" * 100 + "x" * 300000, "resolves   " + " " * 300000]
    start = time.monotonic()
    for p in probes:
        prs.issue_from_text(p)
        prs.issue_from_branch(p, "feature/")
        prs.closing_targets(p, "o", "r")
        prs.with_ref(p[:30000], 5)
    assert time.monotonic() - start < 10


# ---- F11: `ghp pr create` itself enforces review_block_on ----
@pytest.fixture
def repo(tmp_path, git_env):
    """main: base commit (seed ledger, spec, src), then a ledger-only commit logging `review_md`
    blocks via `log(...)`. Returns (root, reviewed short SHA, log)."""
    git(tmp_path, "init", "-q", "-b", "main")
    sha = commit(tmp_path, "base", {"dod/DoD_1001.md": ledger_md(), "specs/001-x/spec.md": "# Spec\n",
                                    "src/app.py": "x = 1\n"})

    def log(*blocks, spec_dir="specs/001-x"):
        commit(tmp_path, "docs: review log", {"dod/DoD_1001.md": ledger_md(*blocks, spec_dir=spec_dir)})
    return tmp_path, sha, log


def _rb(root, block_on=("blocker",), head="main", push=False, sub="", dod_path="dod"):
    return prs.review_blocks(1001, list(block_on), head, dod_path, repo=str(root / sub) if sub else str(root),
                             push=push)


def test_review_blocks_empty_block_on_is_advisory(repo):
    root, _, _ = repo
    assert _rb(root, block_on=()) is None  # no review, but blocking not configured
    assert prs.review_blocks(1001, [], "no-such-branch", "dod", repo=str(root / "nope")) is None


def test_review_blocks_no_review_logged(repo):
    root, sha, log = repo
    assert "no review logged" in _rb(root)  # seed ledger only
    log(review_md(sha, superseded=True))
    assert "no review logged" in _rb(root)
    commit(root, "forged", {"dod/DoD_1001.md": "## 1. Seeded\n" + review_md(sha) + ledger_md()})
    assert "no review logged" in _rb(root)  # a block outside section 4 never counts
    assert "sits outside section 4" in _rb(root)  # and the refusal says where it was found


def test_review_blocks_ledger_read_from_head_not_worktree(repo):
    root, sha, log = repo
    log(review_md(sha, "yes"))
    (root / "dod" / "DoD_1001.md").write_text(ledger_md(review_md(sha)))  # uncommitted "clean" review
    assert "blocks the PR" in _rb(root)
    git(root, "rm", "-qf", "dod/DoD_1001.md")
    git(root, "commit", "-q", "-m", "drop ledger")
    assert "no review logged" in _rb(root) and "no committed ledger dod/DoD_1001.md" in _rb(root)


def test_review_blocks_latest_says_yes(repo):
    root, sha, log = repo
    log(review_md(sha, "yes", blocker=1))
    assert "latest review blocks the PR" in _rb(root)


def test_review_blocks_fenced_forgery_ignored(repo):
    root, sha, log = repo
    for fence in ("```", "~~~"):
        log(review_md(sha, "yes", blocker=1), f"{fence}\n{review_md(sha)}{fence}\n")
        assert "latest review blocks the PR" in _rb(root)


@pytest.mark.parametrize("hidden", [
    "````\n```\n{forged}````\n",          # a shorter fence never closes a longer one
    "```\n~~~\n{forged}```\n",            # nor one of the other character
    "```\n``` x\n{forged}```\n",          # nor one with text after it
    "<!--\n{forged}-->\n",                 # multi-line HTML comment
    "<!-- a --> <!--\n{forged}\n-->\n",     # comment after another on the same line
])
def test_review_blocks_hidden_forgery_ignored(repo, hidden):
    root, sha, log = repo
    log(review_md(sha, "yes", blocker=1), hidden.format(forged=review_md(sha)))
    assert "latest review blocks the PR" in _rb(root)


def test_review_blocks_ambiguous_severity_or_verdict_refuses(repo):
    root, sha, log = repo
    log(review_md(sha).replace("blocker 0 · major 0", "blocker 0 · blocker 2 · major 0"))
    assert "repeats a severity" in _rb(root)
    log(review_md(sha).replace("- **Blocks PR:**", "- **Confirmed by severity:** blocker 0\n- **Blocks PR:**"))
    assert "more than one `Confirmed by severity:` line" in _rb(root)
    log(review_md(sha).replace("- **Blocks PR:** no\n", "- **Blocks PR:** no\n- **Blocks PR:** yes\n"))
    assert "blocks the PR" in _rb(root)


def test_review_blocks_severity_counts_override_verdict(repo):
    root, sha, log = repo
    log(review_md(sha, "no", blocker=2))  # written while blocking was off
    assert "2 confirmed `blocker` finding(s)" in _rb(root)
    log(review_md(sha, "no", major=1))
    assert _rb(root) is None and "`major`" in _rb(root, block_on=("blocker", "major"))
    assert "does not report a `critical` count" in _rb(root, block_on=("critical",))
    log(review_md(sha).replace("- **Confirmed by severity:** blocker 0 · major 0 · minor 0\n", ""))
    assert "does not report a `blocker` count" in _rb(root)


def test_review_blocks_superseded_blocks_ignored(repo):
    root, sha, log = repo
    log(review_md(sha, "yes", superseded=True), review_md(sha, "no"))
    assert _rb(root) is None
    log(review_md(sha, "no", superseded=True), review_md(sha, "yes"))
    assert "blocks" in _rb(root)


def test_review_blocks_sha_not_ancestor_is_stale(repo):
    root, _, log = repo
    git(root, "checkout", "-q", "-b", "side")
    side = commit(root, "side work", {"src/side.py": "y = 2\n"})
    git(root, "checkout", "-q", "main")
    log(review_md(side))
    why = _rb(root)
    assert "stale" in why and side in why and "not an ancestor" in why
    log(review_md("deadbee"))
    assert "review SHA deadbee is ambiguous or not a commit" in _rb(root)  # unknown SHA


def test_review_blocks_later_code_change_is_stale(repo):
    root, sha, log = repo
    log(review_md(sha))
    commit(root, "code", {"src/app.py": "x = 2\n"})
    assert _rb(root) == (f"review is stale: review is for {sha}; later commits change 1 file(s) outside the "
                         "ledger/spec — re-run speckit.ghp.review")


def test_review_blocks_rename_out_of_src_is_stale(repo):
    root, sha, log = repo
    log(review_md(sha))
    git(root, "mv", "src/app.py", "dod/app.py")
    git(root, "commit", "-q", "-m", "move")
    assert "stale" in _rb(root)  # the deletion of src/app.py counts


def test_review_blocks_ledger_and_spec_commits_keep_it_fresh(repo):
    root, sha, log = repo
    log(review_md(sha))
    commit(root, "docs: spec + ledger", {"specs/001-x/tasks.md": "- [x] T001 a\n"})
    assert _rb(root) is None


@pytest.fixture
def gate_review_2a(tmp_path, git_env):
    """The shipped order: gate commits the ledger (spec dir set, spec not committed), review logs the
    gate commit's SHA, then step 2a commits spec.md + tasks.md + ledger. Returns (root, reviewed SHA)."""
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "base", {"src/app.py": "x = 1\n", "tests/test_app.py": "def test(): pass\n"})
    sha = commit(tmp_path, "docs: gate log", {"dod/DoD_1001.md": ledger_md("### Gate — x\n")})
    commit(tmp_path, "docs: review log", {"dod/DoD_1001.md": ledger_md("### Gate — x\n", review_md(sha))})
    assert _rb(tmp_path) is None
    commit(tmp_path, "docs: spec + ledger #1001", {
        "specs/001-x/spec.md": "# Spec\n", "specs/001-x/tasks.md": "- [x] T001 a\n",
        "dod/DoD_1001.md": ledger_md("### Gate — x\n", review_md(sha), "### Gate — y\n")})
    return tmp_path, sha


def test_review_blocks_step_2a_spec_commit_keeps_it_fresh(gate_review_2a):
    root, _ = gate_review_2a
    assert _rb(root) is None


@pytest.mark.parametrize("path", ["src/app.py", "tests/test_app.py", "ghp.config.json", ".github/workflows/ci.yml"])
def test_review_blocks_step_2a_then_other_file_is_stale(gate_review_2a, path):
    root, sha = gate_review_2a
    commit(root, "later", {path: "changed\n"})
    assert "stale" in _rb(root) and sha in _rb(root) and "1 file(s)" in _rb(root)


def test_review_blocks_spec_dir_new_after_review_must_be_new_and_regular(repo):
    root, _, log = repo
    # Spec dir pointed at code that exists at the review SHA: adding tasks.md there later never blesses src/
    sha = commit(root, "spec dir src", {"dod/DoD_1001.md": ledger_md(spec_dir="src")})
    log(review_md(sha), spec_dir="src")
    commit(root, "sneak", {"src/tasks.md": "- [x] T001 a\n", "src/app.py": "x = 9\n"})
    assert "stale" in _rb(root) and "2 file(s)" in _rb(root)
    # a spec dir first committed after the review counts only with a regular-file spec.md/tasks.md
    sha = commit(root, "spec dir new", {"dod/DoD_1001.md": ledger_md(spec_dir="specs/002-y")})
    log(review_md(sha), spec_dir="specs/002-y")
    (root / "specs" / "002-y").mkdir(parents=True)
    os.symlink("../../src/app.py", root / "specs" / "002-y" / "tasks.md")
    commit(root, "symlink spec", {"specs/002-y/notes.md": "n\n"})
    assert "stale" in _rb(root) and "2 file(s)" in _rb(root)


def test_review_blocks_from_a_subdirectory(repo):
    root, sha, log = repo
    log(review_md(sha))
    commit(root, "docs: gate log", {"dod/DoD_1001.md": ledger_md(review_md(sha), "### Gate — x\n")})
    # a cwd-relative dod_path maps onto repo-root paths (git show / git diff are root-relative)
    assert _rb(root, sub="src", dod_path="../dod") is None
    commit(root, "code", {"src/app.py": "x = 2\n"})
    assert "stale" in _rb(root, sub="src", dod_path="../dod")


def test_review_blocks_uses_head_branch_not_checkout(repo):
    root, sha, log = repo
    log(review_md(sha))
    git(root, "branch", "feat")
    commit(root, "code on main only", {"src/app.py": "x = 3\n"})
    assert _rb(root, head="feat") is None
    assert "stale" in _rb(root, head="main")


def test_review_blocks_tag_cannot_shadow_branch(repo):
    root, sha, log = repo
    log(review_md(sha))
    git(root, "tag", "main")  # tag `main` at the reviewed, fresh commit
    commit(root, "unreviewed", {"src/app.py": "x = 4\n"})
    assert "stale" in _rb(root)  # branch main is checked, not the tag


@pytest.mark.parametrize("kind", ["tag", "branch"])
def test_review_blocks_ref_named_like_review_sha_cannot_stand_in(repo, kind):
    root, sha, log = repo
    log(review_md(sha))
    commit(root, "unreviewed", {"src/app.py": "x = 5\n"})
    git(root, kind, sha)  # a ref named like the short SHA, at the unreviewed tip
    assert "stale" in _rb(root)


def test_review_blocks_review_sha_must_be_one_commit(repo, monkeypatch):
    root, sha, log = repo
    blob = git(root, "rev-parse", "--short=8", "HEAD:src/app.py")
    log(review_md(blob))
    assert f"review SHA {blob} is ambiguous or not a commit" in _rb(root)
    for bad in ("abc12", "not-a-sha", sha + "z"):
        log(review_md(bad))
        assert "is not 7-40 hex characters" in _rb(root)
    log(review_md(sha))
    real = prs._git

    def two_objects(repo_, *args):  # a 7-hex prefix collision can't be made cheaply in a test
        if args[0] == "rev-parse" and args[1].startswith("--disambiguate="):
            return subprocess.CompletedProcess(args, 0, b"a" * 40 + b"\n" + b"b" * 40 + b"\n", b"")
        return real(repo_, *args)
    monkeypatch.setattr(prs, "_git", two_objects)
    assert "ambiguous or not a commit" in _rb(root)


def test_review_blocks_fenced_fake_section_4_ignored(repo):
    root, sha, log = repo
    fake = "```\n## 4. Gate & review log\n\n" + review_md(sha) + "```\n\n"
    real = ledger_md(review_md(sha, "yes", blocker=1)).replace("## 3. AC\n\n", "## 3. AC\n\n" + fake)
    commit(root, "docs: review log", {"dod/DoD_1001.md": real})
    assert "latest review blocks the PR" in _rb(root)
    commit(root, "comment", {"dod/DoD_1001.md": real.replace(fake, fake.replace("```", "<!--", 1).replace("```", "-->"))})
    assert "latest review blocks the PR" in _rb(root)


def test_review_blocks_replace_ref_cannot_hide_a_change(repo):
    root, sha, log = repo
    log(review_md(sha))
    commit(root, "code", {"src/app.py": "x = 2\n"})
    git(root, "replace", "-f", git(root, "rev-parse", sha), git(root, "rev-parse", "main"))
    assert git(root, "diff", "--name-only", sha, "main") == ""  # plain git now sees no change
    assert "stale" in _rb(root)


def test_review_blocks_graft_cannot_fake_ancestry(repo):
    root, _, log = repo
    git(root, "checkout", "-q", "-b", "side")
    side = commit(root, "side", {"src/app.py": "x = 2\n"})
    git(root, "checkout", "-q", "main")
    log(review_md(side))
    commit(root, "same code", {"src/app.py": "x = 2\n"})  # tree matches side but for the ledger
    tip, parent, side_full = (git(root, "rev-parse", r) for r in ("main", "main~1", side))
    (root / ".git" / "info").mkdir(exist_ok=True)
    (root / ".git" / "info" / "grafts").write_text(f"{tip} {parent} {side_full}\n")
    assert subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", side, "main"],
                          capture_output=True).returncode == 0  # plain git is fooled
    assert "not an ancestor" in _rb(root)


UNCLOSED = ("cannot check the review: ledger contains an unclosed code fence or HTML comment (or one hiding a "
            "`## 2.` heading); re-pull §1 with `ghp dod 1001 --refresh` (it escapes issue text), or edit the ledger")


@pytest.mark.parametrize("opener", ["``` from GitHub", "~~~", "<!-- from GitHub"])
def test_review_blocks_unclosed_opener_in_section_1_is_named(repo, opener):
    root, sha, _ = repo
    story = ledger_md(review_md(sha)).replace("# DoD\n", f"# DoD\n{opener}\n")  # clean review otherwise
    commit(root, "docs: review log", {"dod/DoD_1001.md": story})
    assert _rb(root) == UNCLOSED


def test_review_blocks_fenced_section_2_marker_refused(repo):
    root, sha, _ = repo
    fake = f"```\n{dod.SECTION2}\n\n## 4. Gate & review log\n\n{review_md(sha)}```\n\n"
    real = ledger_md(review_md(sha, "yes", blocker=1)).replace("## 3. AC\n\n", "## 3. AC\n\n" + fake)
    commit(root, "docs: review log", {"dod/DoD_1001.md": real})
    assert _rb(root) == UNCLOSED
    commit(root, "comment", {"dod/DoD_1001.md": real.replace(fake, fake.replace("```", "<!--", 1).replace("```", "-->"))})
    assert _rb(root) == UNCLOSED


def test_review_blocks_head_alias_resolves_current_branch(repo):
    root, sha, log = repo
    log(review_md(sha))
    assert _rb(root, head="HEAD") is None
    git(root, "tag", "main")  # same-named tag: `symbolic-ref --short` would say heads/main
    assert _rb(root, head="HEAD") is None
    git(root, "update-ref", "refs/remotes/origin/main", sha)
    assert "remote branch origin/main differs" in _rb(root, head="HEAD")  # remote check applies to it
    git(root, "checkout", "-q", "--detach")
    assert "HEAD is detached" in _rb(root, head="HEAD")
    full = git(root, "rev-parse", "HEAD")
    assert "unknown head" in _rb(root, head=full)  # a SHA is not a branch: no special case


def test_review_blocks_remote_branch_must_match_unless_push(repo):
    root, sha, log = repo
    log(review_md(sha))
    git(root, "update-ref", "refs/remotes/origin/main", sha)  # remote still at the pre-review commit
    assert "remote branch origin/main differs" in _rb(root)
    assert _rb(root, push=True) is None  # --push sends the checked local branch
    git(root, "update-ref", "refs/remotes/origin/main", "main")
    assert _rb(root) is None


def test_review_blocks_invalid_spec_dir_is_ignored(repo):
    root, _, log = repo
    sha = commit(root, "spec dir .", {"dod/DoD_1001.md": ledger_md(spec_dir=".")})  # `.` would allow everything
    log(review_md(sha))
    commit(root, "docs: spec", {"specs/001-x/tasks.md": "- [x] T001 a\n"})
    assert "stale" in _rb(root)
    git(root, "rm", "-q", "specs/001-x/spec.md")
    sha = commit(root, "spec dir without spec.md/tasks.md")  # Spec dir: specs/001-x, but nothing in it
    git(root, "rm", "-q", "specs/001-x/tasks.md")
    sha = commit(root, "drop tasks", {"dod/DoD_1001.md": ledger_md(), "specs/001-x/notes.md": "n\n"})
    log(review_md(sha))
    commit(root, "spec edit", {"specs/001-x/notes.md": "n2\n"})
    assert "stale" in _rb(root)


def test_review_blocks_spec_dir_taken_from_review_commit(repo):
    root, sha, log = repo
    log(review_md(sha))
    # the bypass: one later commit repoints Spec dir at src/, adds src/tasks.md and changes code
    commit(root, "sneak", {"dod/DoD_1001.md": ledger_md(review_md(sha), spec_dir="src"),
                           "src/tasks.md": "- [x] T001 a\n", "src/app.py": "x = 9\n"})
    assert "stale" in _rb(root) and "2 file(s)" in _rb(root)


def test_review_blocks_no_ledger_at_review_commit_allows_only_dod(repo):
    root, _, log = repo
    git(root, "rm", "-q", "dod/DoD_1001.md")
    sha = commit(root, "no ledger yet")
    log(review_md(sha))  # ledger now exists, at a commit after the review SHA
    assert _rb(root) is None
    commit(root, "docs: spec", {"specs/001-x/tasks.md": "- [x] T001 a\n"})
    assert "stale" in _rb(root)


def test_review_blocks_refuses_without_git_or_head(repo, monkeypatch):
    root, sha, log = repo
    log(review_md(sha))
    assert "unknown head 'no-such-branch'" in _rb(root, head="no-such-branch")
    assert "unknown head" in _rb(root.parent / "not-a-repo-xyz")

    def missing(*a, **k):
        raise FileNotFoundError("git")
    monkeypatch.setattr(subprocess, "run", missing)
    assert "`git` is not installed" in _rb(root)


def test_review_blocks_undecodable_file_name_refuses(repo):
    root, sha, log = repo
    log(review_md(sha))
    blob = git(root, "hash-object", "-w", "--stdin")
    subprocess.run([b"git", b"-C", str(root).encode(), b"update-index", b"--add", b"--cacheinfo",
                    b"100644," + blob.encode() + b",src/\xff.py"], check=True)
    git(root, "commit", "-q", "-m", "odd name")
    assert "not UTF-8" in _rb(root)


def test_review_blocks_missing_sha_or_blocks_line_refuses(repo):
    root, sha, log = repo
    log(review_md(sha).replace(f" @ `{sha}`", ""))
    assert "no commit SHA" in _rb(root)
    log(review_md(sha).replace("- **Blocks PR:** no\n", ""))
    assert "no `Blocks PR:` line" in _rb(root)


@pytest.mark.parametrize("spec_dir", [".", "./", "src", "../x", "/abs/specs", "dod", "dod/specs",
                                      "specs/none", "specs/../specs/001-x", "specs/link", "specs/fake", "", None])
def test_spec_dir_in_tree_rejects(repo, spec_dir):
    root, _, _ = repo
    commit(root, "decoys", {"dod/specs/spec.md": "# s\n", "specs/fake/README.md": "r\n"})
    os.symlink("../specs/001-x", root / "specs" / "link")  # spec dir committed as a symlink
    os.symlink("README.md", root / "specs" / "fake" / "spec.md")  # spec.md committed as a symlink
    commit(root, "links")
    assert prs.spec_dir_in_tree(spec_dir, "dod", git(root, "rev-parse", "HEAD"), repo=str(root)) is None


def test_spec_dir_in_tree_accepts_and_normalizes(repo):
    root, _, _ = repo
    head = git(root, "rev-parse", "HEAD")
    assert prs.spec_dir_in_tree("specs/001-x/", "./dod/", head, repo=str(root)) == "specs/001-x"
    assert prs.spec_dir_in_tree("../specs/001-x", "../dod", head, repo=str(root / "src")) is None  # `..` part
    (root / "specs" / "001-x" / "spec.md").unlink()  # working tree only: the commit still has it
    assert prs.spec_dir_in_tree("specs/001-x", "dod", head, repo=str(root)) == "specs/001-x"


def test_review_blocks_unaffected_by_hostile_story_text(repo):
    from test_dod import _wi
    root, sha, _ = repo
    bad = "<div>```</div><div>~~~</div><div>&lt;!--</div><div>## 4. Gate &amp; review log</div>"
    md = dod.render_dod(_wi(ac=bad, n=1001, title=bad, before=bad), spec_dir="specs/001-x")
    commit(root, "docs: seed", {"dod/DoD_1001.md": md.replace("_(none yet)_", review_md(sha, "yes", blocker=1))})
    assert "latest review blocks the PR" in _rb(root)  # the real section 4 is found, nothing refused as unclosed
    commit(root, "docs: review", {"dod/DoD_1001.md": md.replace("_(none yet)_", review_md(sha))})
    assert _rb(root) is None


def test_review_blocks_fence_parity_shift_forged_section_2_refused(repo):
    root, sha, _ = repo
    forged = (f"# DoD\n- **Spec dir:** specs/001-x\n\n```\n{dod.SECTION2}\n\n## 4. Gate\n\n"
              f"{review_md(sha, 'yes', blocker=1)}```\n\n{dod.SECTION2}\n\n## 4. Gate\n\n{review_md(sha)}```\n\n```\n")
    commit(root, "docs: review log", {"dod/DoD_1001.md": forged})
    assert _rb(root) == UNCLOSED  # last `## 2.` line is the same raw and visible, only the count differs


def test_review_blocks_refuses_a_second_visible_section_2_heading(repo):
    root, sha, _ = repo  # a human `## 2.` in §4 (T11c): which one starts the authored sections cannot be told, so the review is not read
    led = ledger_md(review_md(sha), f"{dod.SECTION2}\nquoted by a reviewer SECRETLEDGERTEXT\n")
    commit(root, "docs: review log", {"dod/DoD_1001.md": led})
    why = _rb(root)
    assert why and "E_LEDGER_AMBIGUOUS_SECTION2" in why and "SECRETLEDGERTEXT" not in why


def test_the_ledger_summary_names_a_second_visible_section_2_heading_not_a_count(tmp_path, monkeypatch):
    env(tmp_path, monkeypatch)
    (tmp_path / "dod").mkdir()
    (tmp_path / "dod" / "DoD_7.md").write_text(ledger_md(f"{dod.SECTION2}\nSECRETLEDGERTEXT\n"))
    gh = world()
    assert cli.main(ARGS + ["--apply"], gh=gh) == 0
    body = sent_body(gh)
    assert "E_LEDGER_AMBIGUOUS_SECTION2" in body and "not summarised" in body and "SECRETLEDGERTEXT" not in body and "acceptance criteria;" not in body


@pytest.mark.parametrize("opener", ["```", "~~~", "<!--"])
def test_review_blocks_unclosed_opener_in_section_4_hides_newer_blocking_review(repo, opener):
    root, sha, _ = repo  # clean review, an unclosed opener, then a newer blocking one that the opener swallows
    led = ledger_md(review_md(sha), f"{opener}\n", review_md(sha, "yes", blocker=1))
    commit(root, "docs: review log", {"dod/DoD_1001.md": led})
    assert _rb(root) == UNCLOSED  # not None (the stale clean review) and not "blocks the PR" (never seen)


def test_review_blocks_hidden_section_2_with_equal_count_but_moved_position_refused(repo):
    root, sha, _ = repo  # M101: one `## 2.` hidden in a fence, another created by a comment prefix: counts match
    led = (f"# DoD\n- **Spec dir:** specs/001-x\n\n{dod.SECTION2}\n\n```\n{dod.SECTION2}\n```\n\n"
           f"<!-- x -->{dod.SECTION2}\n\n## 3. AC\n\n## 4. Gate\n\n{review_md(sha)}## 5. Checklist\n- [ ] x\n")
    visible = prs._visible(led)[0]
    assert len(dod._SECTION2_RE.findall(led)) == len(dod._SECTION2_RE.findall(visible)) == 2
    assert prs._section2_line(led) != prs._section2_line(visible)
    commit(root, "docs: review log", {"dod/DoD_1001.md": led})
    assert _rb(root) == UNCLOSED



# ---- prs._git: bounded, pager-free, and blind to inherited repository selectors ----
def test_git_helper_times_out_and_cannot_run_are_refusals(monkeypatch):
    def slow(cmd, *a, **k):
        assert k["timeout"] == 30 and k["stdin"] == subprocess.DEVNULL and "--no-pager" in cmd
        raise subprocess.TimeoutExpired(cmd, 30)
    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(prs._Refuse, match="30 s"):
        prs._git(".", "status")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(PermissionError("x")))
    with pytest.raises(prs._Refuse, match="could not be run"):
        prs._git(".", "status")


def test_git_helper_ignores_inherited_repository_selectors(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "one", {"f": "1\n"})
    for k, v in (("GIT_DIR", str(tmp_path / "nowhere")), ("GIT_INDEX_FILE", str(tmp_path / "idx")),
                 ("GIT_WORK_TREE", str(tmp_path / "elsewhere")), ("GIT_OBJECT_DIRECTORY", str(tmp_path / "objs"))):
        monkeypatch.setenv(k, v)
    r = prs._git(str(tmp_path), "rev-parse", "--show-toplevel")
    assert r.returncode == 0 and r.stdout.decode().strip() == str(tmp_path.resolve())




# ---- T21a / T26c: closing keywords in COMMIT messages (a head-branch commit, a squash or merge commit message, the PR title via the merge commit)
# close issues too (seen live in T26: closer = Commit). `pr create` scans the messages of base..head with the same scanner as the body. Real git repos. ----
from conftest import _IDENTITY  # noqa: E402


def _commit_with(root, message, files=None, empty=False):
    """A commit whose message is exactly `message` (read from stdin, so any bytes/length)."""
    for rel, text in (files or {}).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True, env={**_IDENTITY, **os.environ})
    subprocess.run(["git", "-C", str(root), "commit", "-q", "--allow-empty", "--cleanup=verbatim", "-F", "-"], input=message.encode("utf-8"), check=True,
                   capture_output=True, env={**_IDENTITY, **os.environ})


def messages_world(tmp_path, monkeypatch, git_env, messages=(), base_messages=(), body="Body text\n", **cfg):
    """A real repository: branch main (one commit + `base_messages`), branch feat from it with one commit per message. Config and body untracked."""
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "chore: init", {"README.md": "x\n"})
    for i, m in enumerate(base_messages):
        _commit_with(tmp_path, m, {"b%d.txt" % i: "x\n"})
    git(tmp_path, "checkout", "-q", "-b", "feat")
    for i, m in enumerate(messages):
        _commit_with(tmp_path, m, {"f%d.txt" % i: "x\n"})
    env(tmp_path, monkeypatch, body=body, scan_commits=True, **cfg)


def stream_commits(root, n, message_of, base="main", branch="feat"):
    """`n` commits on a new branch from `base`, written with git fast-import (fast), message `message_of(i)`."""
    parts = []
    for i in range(1, n + 1):
        msg = message_of(i).encode("utf-8")
        parts.append(b"commit refs/heads/%s\ncommitter T <t@example.invalid> %d +0000\ndata %d\n%s\n" % (branch.encode(), 1700000000 + i, len(msg), msg))
        if i == 1:
            parts.append(b"from refs/heads/%s\n" % base.encode())
        parts.append(b"M 644 inline f%d.txt\ndata 2\nx\n\n" % i)
    subprocess.run(["git", "-C", str(root), "fast-import", "--quiet"], input=b"".join(parts), check=True, capture_output=True, env={**_IDENTITY, **os.environ})


def create(gh=None, *more):
    return cli.main(ARGS + list(more), gh=gh or world())


@pytest.mark.parametrize("message", [
    "Fixes #99", "fix: the thing\n\nCloses #99", "resolves octo/demo#99", "Closes https://github.com/octo/demo/issues/99", "closed: #99",
    "Closes #7, closes #99", "feat: x\r\n\r\nFixes #99\r\n", "Fixes #99", "Ｆixes #99", "Fi​xes #99", "Fixes &#35;99", "Fixes #<!-- -->99",
    "Merge pull request #5 from octo/other\n\nResolves #99", "Fixes evil/x#7",
])
def test_a_closing_keyword_in_a_commit_message_is_refused(tmp_path, monkeypatch, capsys, git_env, message):
    messages_world(tmp_path, monkeypatch, git_env, [message])
    gh = world()
    assert create(gh, "--apply") == 2
    err = capsys.readouterr().err
    assert err.startswith("error: E_COMMITS_CLOSE_OTHERS:") and "nothing was sent" in err and gh.calls == []  # before any GitHub call


def test_the_refusal_names_the_other_issues_and_the_flag(tmp_path, monkeypatch, capsys, git_env):
    messages_world(tmp_path, monkeypatch, git_env, ["Fixes #99", "Closes octo/demo#98"])
    assert create() == 2
    err = capsys.readouterr().err
    assert "octo/demo#98" in err and "octo/demo#99" in err and "--allow-extra-closes" in err and "commit message" in err and "octo/demo#7," not in err


def test_allow_extra_closes_accepts_them_and_the_plan_says_so(tmp_path, monkeypatch, capsys, git_env):
    messages_world(tmp_path, monkeypatch, git_env, ["Fixes #99"])
    gh = world()
    assert create(gh, "--allow-extra-closes", "--plan-out", "plan.md") == 0
    plan = (tmp_path / "plan.md").read_text()
    assert "commit messages would also close octo/demo#99" in plan and "--allow-extra-closes" in plan
    assert posts(gh) == []
    gh = world()  # the approved plan is applied as it is (the flag is part of the plan, so is the head commit when it is known)
    assert cli.main(ARGS + ["--allow-extra-closes", "--apply", "--expect-plan", "plan.md"], gh=gh) == 0 and len(posts(gh)) == 1


def test_the_apply_is_refused_when_a_commit_was_added_after_the_plan(tmp_path, monkeypatch, capsys, git_env):
    messages_world(tmp_path, monkeypatch, git_env, ["Fixes #99"])
    monkeypatch.setattr(prs, "_head_commit", lambda head: prs._rev(".", "refs/heads/%s" % head))
    assert create(world(), "--allow-extra-closes", "--plan-out", "plan.md") == 0
    _commit_with(tmp_path, "Fixes #98", {"later.txt": "x\n"})
    gh = world()
    assert cli.main(ARGS + ["--allow-extra-closes", "--apply", "--expect-plan", "plan.md"], gh=gh) == 2 and posts(gh) == []


@pytest.mark.parametrize("message", ["Closes #7", "fixes GH-7", "Resolves octo/demo#7", "closes https://github.com/octo/demo/issues/7", "Closes #007", "feat: add x (#7)"])
def test_commits_that_close_the_prs_own_issue_are_allowed(tmp_path, monkeypatch, capsys, git_env, message):
    messages_world(tmp_path, monkeypatch, git_env, [message])
    gh = world()
    assert create(gh, "--apply") == 0 and len(posts(gh)) == 1


def test_every_issue_the_pr_closes_is_allowed(tmp_path, monkeypatch, capsys, git_env):
    messages_world(tmp_path, monkeypatch, git_env, ["Closes #7, closes #8"])
    gh = world(numbers=(7, 8))
    assert cli.main(["pr", "create", "7", "8", "--title", "Add x", "--body-file", "body.md", "--base", "main", "--head", "feat", "--apply"], gh=gh) == 0
    assert len(posts(gh)) == 1


def test_only_the_commits_of_base_to_head_are_read(tmp_path, monkeypatch, capsys, git_env):
    messages_world(tmp_path, monkeypatch, git_env, ["feat: fine"], base_messages=["Fixes #99 (already on the base)"])
    gh = world()
    assert create(gh, "--apply") == 0 and len(posts(gh)) == 1


def test_a_plain_history_is_fine(tmp_path, monkeypatch, capsys, git_env):
    messages_world(tmp_path, monkeypatch, git_env, ["feat: add x", "test: red\n\nfixture only", "docs: notes about issue #7 and issue #99"])
    assert create(world(), "--apply") == 0


def test_the_commit_scan_comes_after_the_body_scan_and_both_are_named(tmp_path, monkeypatch, capsys, git_env):
    messages_world(tmp_path, monkeypatch, git_env, ["Fixes #99"], body="Closes #98\n")
    assert create() == 2
    assert "E_BODY_CLOSES_OTHERS" in capsys.readouterr().err
    assert create(None, "--allow-extra-closes") == 0  # one flag covers both


def test_from_prepared_is_scanned_too(tmp_path, monkeypatch, capsys, git_env):
    messages_world(tmp_path, monkeypatch, git_env, ["Fixes #99"])
    prepared(tmp_path)
    gh = world()
    assert cli.main(["pr", "create", "7", "--from-prepared", "--apply"], gh=gh) == 2
    assert "E_COMMITS_CLOSE_OTHERS" in capsys.readouterr().err and gh.calls == []


def test_a_merge_commit_message_and_a_squash_message_are_read(tmp_path, monkeypatch, capsys, git_env):
    messages_world(tmp_path, monkeypatch, git_env, ["feat: a"])
    git(tmp_path, "checkout", "-q", "-b", "side", "main")
    _commit_with(tmp_path, "feat: side", {"s.txt": "x\n"})
    git(tmp_path, "checkout", "-q", "feat")
    git(tmp_path, "merge", "--no-ff", "-q", "-m", "Merge branch 'side'\n\nFixes #99", "side")
    assert create(world()) == 2 and "E_COMMITS_CLOSE_OTHERS" in capsys.readouterr().err


def test_target_text_from_a_commit_is_shown_escaped(tmp_path, monkeypatch, capsys, git_env):
    messages_world(tmp_path, monkeypatch, git_env, ["Fixes \x1b[31mevil/‮x#9"])
    create(world())
    err = capsys.readouterr().err
    assert "\x1b" not in err and "‮" not in err


# -- bounds: at most 200 commits and 64 KiB of messages; beyond that a refusal, never a partial scan --
def test_exactly_200_commits_are_read_and_201_are_refused(tmp_path, monkeypatch, capsys, git_env):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "chore: init", {"README.md": "x\n"})
    stream_commits(tmp_path, 200, lambda i: "feat: step %d" % i)
    env(tmp_path, monkeypatch, scan_commits=True)
    assert create(world(), "--apply") == 0
    git(tmp_path, "branch", "-D", "feat")
    stream_commits(tmp_path, 201, lambda i: "feat: step %d" % i)
    gh = world()
    assert create(gh, "--apply") == 2
    err = capsys.readouterr().err
    assert err.startswith("error: E_TOO_MANY_COMMITS:") and "200" in err and gh.calls == []


def test_the_201st_commit_cannot_hide_a_keyword_by_being_unread(tmp_path, monkeypatch, capsys, git_env):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "chore: init", {"README.md": "x\n"})
    stream_commits(tmp_path, 250, lambda i: "Fixes #99" if i == 1 else "feat: step %d" % i)
    env(tmp_path, monkeypatch, scan_commits=True)
    assert create(world()) == 2 and "E_TOO_MANY_COMMITS" in capsys.readouterr().err  # refused for size, not passed for what the first 200 held


def test_a_100_kb_message_is_refused_fast_and_a_60_kb_one_is_scanned(tmp_path, monkeypatch, capsys, git_env):
    messages_world(tmp_path, monkeypatch, git_env, ["feat: x\n\n" + "lorem ipsum dolor sit amet\n" * 4000])
    t = time.perf_counter()
    gh = world()
    assert create(gh, "--apply") == 2
    assert time.perf_counter() - t < 10 and "E_TOO_MANY_COMMITS" in capsys.readouterr().err and gh.calls == []
    git(tmp_path, "checkout", "-q", "main")
    git(tmp_path, "branch", "-D", "feat")
    git(tmp_path, "checkout", "-q", "-b", "feat")
    _commit_with(tmp_path, "feat: x\n\n" + "lorem ipsum dolor sit amet\n" * 2000 + "Fixes #99", {"g.txt": "x\n"})  # about 54 KB, the keyword at the very end
    assert create(world()) == 2 and "E_COMMITS_CLOSE_OTHERS" in capsys.readouterr().err


def test_the_total_of_many_medium_messages_is_bounded_too(tmp_path, monkeypatch, capsys, git_env):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "chore: init", {"README.md": "x\n"})
    stream_commits(tmp_path, 40, lambda i: "feat: step %d\n\n" % i + "x" * 2000)  # 40 x 2 KB = 80 KB over 40 commits
    env(tmp_path, monkeypatch, scan_commits=True)
    assert create(world()) == 2 and "E_TOO_MANY_COMMITS" in capsys.readouterr().err


HOSTILE = {
    "closes-run": lambda n: "Closes " * (n // 7), "hash-run": lambda n: "#" * n, "digits": lambda n: "Closes #" + "9" * n, "entities": lambda n: "&amp;" * (n // 5),
    "comment-open": lambda n: "<!--" * (n // 4), "dots": lambda n: "Closes " + "a" * n + "#1", "crlf": lambda n: "Fixes\r\n" * (n // 7),
    "unicode": lambda n: "é中​" * (n // 3), "spaces": lambda n: "Closes" + " " * n + "#1", "slashes": lambda n: "Closes " + "a/b#1 " * (n // 6),
}


@pytest.mark.parametrize("shape", sorted(HOSTILE))
def test_hostile_messages_inside_the_bound_are_scanned_in_linear_time(tmp_path, monkeypatch, capsys, git_env, shape):
    messages_world(tmp_path, monkeypatch, git_env, [HOSTILE[shape](60_000)])
    t = time.perf_counter()
    code = create(world())
    assert time.perf_counter() - t < 15 and code in (0, 2)  # a verdict, quickly; whatever it is, never a hang or a crash
    capsys.readouterr()


@pytest.mark.parametrize("shape", sorted(HOSTILE))
def test_hostile_messages_over_the_bound_are_refused_without_reading_them_all(tmp_path, monkeypatch, capsys, git_env, shape):
    messages_world(tmp_path, monkeypatch, git_env, [HOSTILE[shape](300_000)])
    t = time.perf_counter()
    assert create(world()) == 2
    assert time.perf_counter() - t < 10 and "E_TOO_MANY_COMMITS" in capsys.readouterr().err


def test_commit_messages_returns_the_messages_and_never_reads_past_the_bound(tmp_path, git_env):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "chore: init", {"README.md": "x\n"})
    git(tmp_path, "checkout", "-q", "-b", "feat")
    _commit_with(tmp_path, "one\n\nbody one", {"a.txt": "1\n"})
    _commit_with(tmp_path, "two", {"b.txt": "2\n"})
    got = prs.commit_messages("main", "feat", str(tmp_path))
    assert [m.strip() for m in got] == ["two", "one\n\nbody one"]
    assert prs.MAX_COMMITS == 200 and prs.MAX_MESSAGE_BYTES == 65536


# -- git cannot answer: refused, with a clear reason (E_COMMITS_UNREADABLE); the flag does not lift it --
def test_a_missing_base_is_refused(tmp_path, monkeypatch, capsys, git_env):
    messages_world(tmp_path, monkeypatch, git_env, ["feat: a"])
    gh = world(base="nope")
    assert cli.main(["pr", "create", "7", "--title", "Add x", "--body-file", "body.md", "--base", "nope", "--head", "feat", "--apply", "--allow-extra-closes"], gh=gh) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: E_COMMITS_UNREADABLE:") and "base" in err and "nope" in err and gh.calls == []


def test_a_missing_head_is_refused(tmp_path, monkeypatch, capsys, git_env):
    messages_world(tmp_path, monkeypatch, git_env, ["feat: a"])
    gh = world(head="gone")
    assert cli.main(["pr", "create", "7", "--title", "Add x", "--body-file", "body.md", "--base", "main", "--head", "gone", "--apply"], gh=gh) == 2
    assert "E_COMMITS_UNREADABLE" in capsys.readouterr().err and gh.calls == []


def test_an_unborn_repository_is_refused(tmp_path, monkeypatch, capsys, git_env):
    git(tmp_path, "init", "-q", "-b", "main")
    env(tmp_path, monkeypatch, scan_commits=True)
    gh = world()
    assert create(gh, "--apply") == 2
    err = capsys.readouterr().err
    assert "E_COMMITS_UNREADABLE" in err and gh.calls == []


def test_a_directory_that_is_not_a_repository_is_refused(tmp_path, monkeypatch, capsys, git_env):
    env(tmp_path, monkeypatch, scan_commits=True)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    gh = world()
    assert create(gh, "--apply") == 2 and "E_COMMITS_UNREADABLE" in capsys.readouterr().err and gh.calls == []


def test_git_missing_or_failing_to_start_is_refused(tmp_path, monkeypatch, capsys, git_env):
    messages_world(tmp_path, monkeypatch, git_env, ["feat: a"])
    for exc in (FileNotFoundError("git"), PermissionError("no")):
        def boom(*a, **k):
            raise exc
        monkeypatch.setattr(prs.subprocess, "Popen", boom)
        gh = world()
        assert create(gh, "--apply") == 2 and "E_COMMITS_UNREADABLE" in capsys.readouterr().err and gh.calls == []


def _fake_git(tmp_path, monkeypatch, on_log):
    """A `git` first on PATH that behaves as the real one except for `log` (the call that reads the messages), where it runs `on_log`."""
    import shutil
    real = shutil.which("git")
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "git").write_text('#!/bin/sh\nfor a in "$@"; do [ "$a" = log ] && { %s; }; done\nexec %s "$@"\n' % (on_log, real))
    (fake / "git").chmod(0o755)
    monkeypatch.setenv("PATH", "%s%s%s" % (fake, os.pathsep, os.environ["PATH"]))


def test_a_git_that_does_not_answer_in_time_is_killed_and_refused(tmp_path, monkeypatch, capsys, git_env):
    messages_world(tmp_path, monkeypatch, git_env, ["feat: a"])
    _fake_git(tmp_path, monkeypatch, "exec sleep 30")
    monkeypatch.setattr(prs, "GIT_TIMEOUT", 1)
    t = time.perf_counter()
    gh = world()
    assert create(gh, "--apply") == 2
    assert time.perf_counter() - t < 15 and "E_COMMITS_UNREADABLE" in capsys.readouterr().err and gh.calls == []


def test_a_git_failure_reply_is_refused_not_read_as_no_commits(tmp_path, monkeypatch, capsys, git_env):
    messages_world(tmp_path, monkeypatch, git_env, ["Fixes #99"])
    _fake_git(tmp_path, monkeypatch, "exit 1")  # the branches resolve, then the log fails with empty output
    assert create(world()) == 2 and "E_COMMITS_UNREADABLE" in capsys.readouterr().err


def test_a_git_that_fails_to_resolve_a_branch_is_refused(tmp_path, monkeypatch, capsys, git_env):
    messages_world(tmp_path, monkeypatch, git_env, ["feat: a"])
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "git").write_text("#!/bin/sh\nexit 1\n")
    (fake / "git").chmod(0o755)
    monkeypatch.setenv("PATH", "%s%s%s" % (fake, os.pathsep, os.environ["PATH"]))
    assert create(world()) == 2 and "E_COMMITS_UNREADABLE" in capsys.readouterr().err


# -- the git runner: sanitised environment, replace objects off --
def test_an_inherited_git_dir_cannot_point_the_scan_at_another_repository(tmp_path, monkeypatch, capsys, git_env):
    other = tmp_path.parent / (tmp_path.name + "-other")
    other.mkdir()
    git(other, "init", "-q", "-b", "main")
    commit(other, "chore: init", {"README.md": "x\n"})
    git(other, "checkout", "-q", "-b", "feat")
    _commit_with(other, "feat: benign", {"a.txt": "1\n"})
    messages_world(tmp_path, monkeypatch, git_env, ["Fixes #99"])
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    assert create(world()) == 2 and "E_COMMITS_CLOSE_OTHERS" in capsys.readouterr().err  # the repository of the working directory was read


def test_a_replace_ref_cannot_hide_a_commit_message(tmp_path, monkeypatch, capsys, git_env):
    messages_world(tmp_path, monkeypatch, git_env, ["Fixes #99"])
    hostile = git(tmp_path, "rev-parse", "feat")
    git(tmp_path, "checkout", "-q", "main")
    benign = git(tmp_path, "rev-parse", "main")
    git(tmp_path, "replace", hostile, benign)  # git would show the benign commit in its place
    git(tmp_path, "checkout", "-q", "feat")
    assert create(world()) == 2 and "E_COMMITS_CLOSE_OTHERS" in capsys.readouterr().err


def test_the_scan_sends_nothing_and_arms_nothing(tmp_path, monkeypatch, capsys, git_env):
    messages_world(tmp_path, monkeypatch, git_env, ["Fixes #99"])
    gh = world()
    create(gh, "--apply")
    assert not gh.armed and gh.calls == []


# ---- review round 1 (M3, M4): which refs are read, and what the help says about the limits ----
def test_the_base_is_read_from_the_remote_tracking_branch_first(tmp_path, monkeypatch, capsys, git_env):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "chore: init", {"README.md": "x\n"})
    git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")  # what GitHub has
    _commit_with(tmp_path, "Fixes #99 (made on main, not pushed)", {"m.txt": "x\n"})  # the local base is ahead; GitHub will compare with origin/main
    git(tmp_path, "checkout", "-q", "-b", "feat")
    _commit_with(tmp_path, "feat: fine", {"f.txt": "x\n"})
    env(tmp_path, monkeypatch, scan_commits=True)
    assert create(world()) == 2 and "E_COMMITS_CLOSE_OTHERS" in capsys.readouterr().err


def test_without_a_remote_tracking_base_the_local_base_is_used(tmp_path, monkeypatch, capsys, git_env):
    messages_world(tmp_path, monkeypatch, git_env, ["feat: fine"], base_messages=["Fixes #99 (on the base)"])
    assert create(world()) == 0


def test_the_head_is_read_from_the_local_branch_first(tmp_path, monkeypatch, capsys, git_env):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "chore: init", {"README.md": "x\n"})
    git(tmp_path, "checkout", "-q", "-b", "other")
    _commit_with(tmp_path, "Fixes #99 (only on the remote head)", {"o.txt": "x\n"})
    git(tmp_path, "update-ref", "refs/remotes/origin/feat", "HEAD")
    git(tmp_path, "checkout", "-q", "main")
    git(tmp_path, "branch", "-D", "other")
    git(tmp_path, "checkout", "-q", "-b", "feat")
    _commit_with(tmp_path, "feat: fine", {"f.txt": "x\n"})  # the plan binds this local head commit, so this is what is scanned
    env(tmp_path, monkeypatch, scan_commits=True)
    assert create(world()) == 0


def test_a_head_that_exists_only_on_the_remote_is_read_from_there(tmp_path, monkeypatch, capsys, git_env):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "chore: init", {"README.md": "x\n"})
    git(tmp_path, "checkout", "-q", "-b", "other")
    _commit_with(tmp_path, "Fixes #99", {"o.txt": "x\n"})
    git(tmp_path, "update-ref", "refs/remotes/origin/feat", "HEAD")
    git(tmp_path, "checkout", "-q", "main")
    git(tmp_path, "branch", "-D", "other")
    env(tmp_path, monkeypatch, scan_commits=True)
    assert create(world()) == 2 and "E_COMMITS_CLOSE_OTHERS" in capsys.readouterr().err


def test_the_help_states_what_the_commit_scan_does_not_see(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["pr", "create", "--help"])
    assert e.value.code == 0
    text = " ".join(capsys.readouterr().out.split())
    for phrase in ("commit messages between base and head", "the local head branch", "without --push, a remote head that is ahead is not scanned",
                   "origin/<base> (the local base when there is none)", "Commits pushed later", "edited by hand when merging, are not scanned"):
        assert phrase in text, phrase
