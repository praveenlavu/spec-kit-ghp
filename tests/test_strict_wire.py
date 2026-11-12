# V3-12: strict mode wired into `ghp pr create`, `ghp done` and `ghp gate`; `strict check --base/--head`; override stamping.
import json
import re
import subprocess
from datetime import date
from types import SimpleNamespace

import pytest

from ghp import __main__ as m
from ghp import decisions, strict
from conftest import commit, git
from test_done import DoneHub
from test_prs import LIST, world
from test_strict import CMD, DEC, LEDGER, RF, RTAGS, _d, _pr, _rules, _rules_on, _squash, _story

PR_ARGS = ["pr", "create", "1001", "--title", "T", "--body-file", "body.md", "--base", "trunk", "--head", "main", "--allow-incomplete"]
TODAY = date.today().isoformat()


def spy_config(monkeypatch, holder):
    """ado's FakeClient carried the Config a run used (`client.config`); here `holder.config` is the Config `strict.check` / `strict.verify` was last given."""
    for name in ("check", "verify"):
        def spy(gh, cfg, *a, _real=getattr(strict, name), **k):
            holder.config = cfg
            return _real(gh, cfg, *a, **k)
        monkeypatch.setattr(strict, name, spy)


def _wire(tmp_path, monkeypatch, git_env, mode="strict", hub=False, committed=False, hook=None, more_issues=(), **kw):
    """The `_story` repo (branch main; `trunk` = the commit before the red commit) with a config, the CLI patched to a FakeClient
    and `gh` recorded: returns (main module, holder of the fake GitHub, list of PRs sent, story). `hub`: the fake GitHub is a `DoneHub` for
    issue 1001 (comments, the close, the facts query) instead of the one `pr create` reads."""
    cfg = {"owner": "octo", "repo": "demo", "strict": {"test_command": CMD}, **({"mode": mode} if mode else {})}
    client, s = _story(tmp_path, git_env, **kw, **({"config": cfg} if committed else {}))  # committed: the head holds its own config (CI runs read it)
    if hook:
        hook(tmp_path)  # a further commit on the story, before the untracked config and body.md exist
    git(tmp_path, "branch", "trunk", s.base)
    (tmp_path / "ghp.config.json").write_text(json.dumps(cfg))
    (tmp_path / "body.md").write_text("Body\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    monkeypatch.setenv("GHP_TOKEN", "t")
    gh = DoneHub(number=1001) if hub else world(numbers=(1001, *more_issues), default="trunk", head="main", base="trunk")  # the fake GitHub `pr create` / `done` read
    fake, sent = {"c": SimpleNamespace(calls=gh.calls, gh=gh, config=None)}, []
    spy_config(monkeypatch, fake["c"])
    monkeypatch.setattr(m.prs, "create_pr", lambda *a, **k: sent.append(a) or ({"number": 5, "url": "https://example.invalid/pr/5"}, "created"))
    monkeypatch.setattr(m.prs, "_head_commit", lambda head: strict._rev(".", f"refs/heads/{head}"))  # the real lookup (test_prs.env fakes it)
    monkeypatch.setattr(m.issues, "_nap", lambda s: None)
    return SimpleNamespace(main=lambda argv: m.main(argv, gh=gh), strict=strict, prs=m.prs), fake, sent, s


def _edit_ledger(tmp_path, old, new):
    text = LEDGER(tmp_path).read_text()
    assert old in text
    LEDGER(tmp_path).write_text(text.replace(old, new, 1))
    commit(tmp_path, "edit ledger")


# ---- pr create ----
CASES = {
    "evidence-missing": {},
    "record-missing": {"record": False},
    "fidelity-missing": {"fidelity": False},
    "decision-rejected": {"dec": decisions.render_file(1001) + _d(1, "use the v2 api", status="rejected")},
    "override-unposted": {"dec": decisions.render_file(1001) + _d(1, "skip l2", kind="override")},
}


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("rule", list(CASES))
def test_pr_create_strict_refuses_each_rule_without_prompt(tmp_path, monkeypatch, capsys, git_env, rule, apply):
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env, **CASES[rule])
    if rule == "evidence-missing":
        _edit_ledger(tmp_path, f"red {s.red} → green {s.green}", "done")
    before = DEC(tmp_path).read_bytes() if DEC(tmp_path).exists() else None
    assert mod.main(PR_ARGS + ["--plan-out", "out/plan-pr.md"] + (["--apply"] if apply else [])) == 2
    cap = capsys.readouterr()
    out = json.loads(cap.out)
    assert out["ok"] is False and rule in [f["rule"] for f in out["findings"]]
    fix = next(f["fix"] for f in out["findings"] if f["rule"] == rule)
    assert cap.err.startswith("error: strict check failed: ") and rule in cap.err and fix in cap.err
    assert "v2 api" not in cap.err + cap.out and "Seeded" not in cap.err  # decision or ledger text never shows
    assert not (tmp_path / "out").exists() and sent == [] and fake["c"].calls == []
    assert (DEC(tmp_path).read_bytes() if DEC(tmp_path).exists() else None) == before  # a refused run stamps nothing


def test_pr_create_strict_passes_a_clean_story(tmp_path, monkeypatch, capsys, git_env):
    mod, fake, sent, _ = _wire(tmp_path, monkeypatch, git_env)
    assert mod.main(PR_ARGS + ["--plan-out", "out/plan-pr.md"]) == 0
    assert (tmp_path / "out" / "plan-pr.md").is_file() and sent == []
    assert mod.main(PR_ARGS + ["--apply"]) == 0 and len(sent) == 1
    capsys.readouterr()


def test_advisory_pr_create_unchanged(tmp_path, monkeypatch, capsys, git_env):
    mod, fake, sent, _ = _wire(tmp_path, monkeypatch, git_env, mode=None, record=False, fidelity=False)  # strict would refuse this repo

    def boom(*a, **k):
        raise AssertionError("advisory mode never runs the strict check")
    monkeypatch.setattr(mod.strict, "check", boom)
    assert mod.main(PR_ARGS + ["--plan-out", "plan.md"]) == 0
    assert (tmp_path / "plan.md").is_file()
    assert mod.main(PR_ARGS + ["--apply"]) == 0 and len(sent) == 1
    capsys.readouterr()


def test_pr_apply_stamps_used_on_the_overrides_it_relied_on(tmp_path, monkeypatch, capsys, git_env):
    dec = decisions.render_file(1001) + _d(1, "skip l2", kind="override", posted=True) + _d(2, "plain")
    mod, fake, sent, _ = _wire(tmp_path, monkeypatch, git_env, dec=dec, record=False)  # record-missing is covered by `skip l2`
    before = DEC(tmp_path).read_bytes()
    assert mod.main(PR_ARGS + ["--plan-out", "plan.md"]) == 0
    assert DEC(tmp_path).read_bytes() == before and sent == []  # the preview never writes
    assert mod.main(PR_ARGS + ["--apply"]) == 0 and len(sent) == 1
    d1, d2 = decisions.parse_decisions(DEC(tmp_path).read_text())["open"]
    assert (d1["id"], d1["used"], d2["id"], d2["used"]) == ("D1", TODAY, "D2", "")
    assert mod.main(PR_ARGS + ["--apply"]) == 0 and decisions.parse_decisions(DEC(tmp_path).read_text())["open"][0]["used"] == TODAY
    capsys.readouterr()


def test_a_refused_pr_run_stamps_nothing_even_with_a_covering_override(tmp_path, monkeypatch, capsys, git_env):
    dec = decisions.render_file(1001) + _d(1, "skip l2", kind="override", posted=True)
    mod, fake, sent, _ = _wire(tmp_path, monkeypatch, git_env, dec=dec, record=False, fidelity=False)  # fidelity-missing stays
    before = DEC(tmp_path).read_bytes()
    assert mod.main(PR_ARGS + ["--apply"]) == 2
    assert DEC(tmp_path).read_bytes() == before and sent == []
    capsys.readouterr()


def test_a_plan_mismatch_stamps_nothing(tmp_path, monkeypatch, capsys, git_env):
    dec = decisions.render_file(1001) + _d(1, "skip l2", kind="override", posted=True)
    mod, fake, sent, _ = _wire(tmp_path, monkeypatch, git_env, dec=dec, record=False)
    (tmp_path / "plan.md").write_text("not the plan\n")
    before = DEC(tmp_path).read_bytes()
    assert mod.main(PR_ARGS + ["--apply", "--expect-plan", "plan.md"]) == 2
    assert DEC(tmp_path).read_bytes() == before and sent == []
    capsys.readouterr()


def test_pr_stamp_write_failure_refuses_before_anything_is_sent(tmp_path, monkeypatch, capsys, git_env):
    dec = decisions.render_file(1001) + _d(1, "skip l2", kind="override", posted=True)
    mod, fake, sent, _ = _wire(tmp_path, monkeypatch, git_env, dec=dec, record=False)
    before = DEC(tmp_path).read_bytes()

    def fail(path, text):
        raise OSError("disk full")
    monkeypatch.setattr(decisions, "_atomic_write", fail)
    assert mod.main(PR_ARGS + ["--apply"]) == 2
    cap = capsys.readouterr()
    assert "nothing written" in cap.err and sent == [] and not [c for c in fake["c"].calls if c[0] == "rest" and c[1] != "GET"]  # reads only: the PR was not created
    assert DEC(tmp_path).read_bytes() == before
    assert list(tmp_path.glob("dod/*.tmp")) == []


def test_pr_with_push_does_not_push_when_the_stamp_fails(tmp_path, monkeypatch, capsys, git_env):
    dec = decisions.render_file(1001) + _d(1, "skip l2", kind="override", posted=True)
    mod, fake, sent, _ = _wire(tmp_path, monkeypatch, git_env, dec=dec, record=False)
    pushed = []
    monkeypatch.setattr(mod.prs, "push_branch", lambda head: pushed.append(head) or {})
    monkeypatch.setattr(decisions, "_atomic_write", lambda *a: (_ for _ in ()).throw(OSError("full")))
    (tmp_path / ".ghp").mkdir()
    (tmp_path / ".ghp" / "pr-args.json").write_text(json.dumps({"title": "T", "base": "trunk", "head": "main"}))
    (tmp_path / ".ghp" / "pr-body.md").write_text("Body\n")
    assert mod.main(["pr", "create", "1001", "--from-prepared", "--push", "--allow-incomplete", "--apply"]) == 2
    assert pushed == [] and sent == []
    capsys.readouterr()


# ---- base and head ----
def _full(tmp_path, rev):
    return git(tmp_path, "rev-parse", rev)


def _check(tmp_path, client, **kw):
    return strict.check(client.gh, client.config, 1001, "pr", repo=str(tmp_path), **kw)


def test_base_range_two_old_commits_are_not_in_range(tmp_path, git_env):
    client, s = _story(tmp_path, git_env)
    assert _check(tmp_path, client, base=_full(tmp_path, s.base))["ok"] is True  # base before the red commit: both are this branch's work
    res = _check(tmp_path, client, base=_full(tmp_path, s.green))  # red and green are both already in the base
    assert _rules(res) == ["not-in-range", "not-in-range"]
    assert {f["detail"].split(" commit")[0].split(": ")[1] for f in res["findings"]} == {"the red", "the green"}
    assert _check(tmp_path, client)["ok"] is True  # no base given: no range check (strict check keeps it optional)


@pytest.mark.parametrize("base", ["no-such-branch", "", "--output=x", "-h"])
def test_unresolvable_base_is_base_unknown_and_never_overridable(tmp_path, git_env, base):
    dec = decisions.render_file(1001) + _d(1, "skip l2", kind="override", posted=True)
    client, s = _story(tmp_path, git_env, dec=dec)
    res = _check(tmp_path, client, base=base)
    assert _rules(res) == ["base-unknown"] and res["ok"] is False
    assert set(res["findings"][0]) == {"rule", "detail", "fix"}
    assert not (tmp_path / "x").exists()


def test_check_head_sha_reads_the_review_at_that_commit_on_a_detached_checkout(tmp_path, git_env):
    client, s = _story(tmp_path, git_env)
    head = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "checkout", "-q", "--detach")
    assert _rules(_check(tmp_path, client)) == ["review-blocks"]  # HEAD is detached: no branch to review
    assert _check(tmp_path, client, head=head, base=_full(tmp_path, s.base)) == {"phase": "pr", "mode": "strict", "ok": True, "findings": [],
                                                                "overrides_used": []}
    assert _check(tmp_path, client, head=head[:10], base=_full(tmp_path, s.base))["ok"] is True  # a short SHA is resolved to the commit
    # an older commit as the head: the record and the review are for a newer head
    commit(tmp_path, "code change", {"src/app.py": "x = 3\n"})
    assert _check(tmp_path, client, head=git(tmp_path, "rev-parse", "HEAD"), base=_full(tmp_path, s.base))["ok"] is False


def test_cli_strict_check_takes_base_and_head(tmp_path, monkeypatch, capsys, git_env):
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env)
    head = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "checkout", "-q", "--detach")
    assert mod.main(["strict", "check", "1001", "--phase", "pr", "--head", head, "--base", _full(tmp_path, s.base)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert mod.main(["strict", "check", "1001", "--phase", "pr", "--head", head, "--base", "nope"]) == 3  # an invalid target config is 3 (CI fails closed)
    cap = capsys.readouterr()  # V3-13: an unknown --base is refused before any check (the target's config cannot be read): fail closed
    assert cap.out == "" and "ghp.config.json at the target branch" in cap.err


def test_pr_create_derives_the_base_ref_origin_first(tmp_path, monkeypatch, capsys, git_env):
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env)
    assert mod.main(PR_ARGS + ["--apply"]) == 0 and len(sent) == 1  # no origin/trunk: the local `trunk` (before the red commit)
    git(tmp_path, "update-ref", "refs/remotes/origin/trunk", s.green)  # the remote base already holds red and green
    capsys.readouterr()
    assert mod.main(PR_ARGS + ["--apply"]) == 2
    assert [f["rule"] for f in json.loads(capsys.readouterr().out)["findings"]] == ["not-in-range", "not-in-range"]
    assert len(sent) == 1


def test_pr_create_with_an_unknown_base_is_base_unknown(tmp_path, monkeypatch, capsys, git_env):
    mod, fake, sent, _ = _wire(tmp_path, monkeypatch, git_env)
    argv = [a if a != "trunk" else "nosuchbranch" for a in PR_ARGS]
    assert mod.main(argv + ["--plan-out", "out/p.md"]) == 2
    assert [f["rule"] for f in json.loads(capsys.readouterr().out)["findings"]] == ["base-unknown"]
    assert not (tmp_path / "out").exists() and sent == []


def test_pr_create_with_push_checks_the_head_like_prs_review_blocks(tmp_path, monkeypatch, capsys, git_env):
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env)
    git(tmp_path, "update-ref", "refs/remotes/origin/main", s.base)  # the remote head differs from the reviewed local head
    pushed = []
    monkeypatch.setattr(mod.prs, "push_branch", lambda head: pushed.append(head) or {})
    (tmp_path / ".ghp").mkdir()
    (tmp_path / ".ghp" / "pr-args.json").write_text(json.dumps({"title": "T", "base": "trunk", "head": "main"}))
    (tmp_path / ".ghp" / "pr-body.md").write_text("Body\n")
    assert mod.main(["pr", "create", "1001", "--from-prepared", "--allow-incomplete"]) == 2  # without --push the remote must match
    assert "review-blocks" in capsys.readouterr().out
    assert mod.main(["pr", "create", "1001", "--from-prepared", "--push", "--allow-incomplete", "--apply"]) == 0
    assert pushed == ["main"] and len(sent) == 1


# ---- done ----
def _tick(tmp_path):
    _edit_ledger(tmp_path, "## 5. DoD\n", "## 5. DoD\n- [x] all done\n")


def test_done_strict_refuses_open_decision(tmp_path, monkeypatch, capsys, git_env):
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env, dec=decisions.render_file(1001) + _d(1, "pick the v2 api"))
    _tick(tmp_path)
    for extra in ([], ["--apply"]):
        assert mod.main(["done", "1001", "--plan-out", "out/plan.md", *extra]) == 2
        cap = capsys.readouterr()
        assert "decision-open" in [f["rule"] for f in json.loads(cap.out)["findings"]]
        assert cap.err.startswith("error: strict check failed: ") and "decision-open" in cap.err and "v2 api" not in cap.err + cap.out
        assert not (tmp_path / "out").exists() and fake["c"].calls == []


def _no_review(tmp_path, s):
    LEDGER(tmp_path).write_text(re.sub(r"### Review.*?(### AC fidelity)", r"\1", LEDGER(tmp_path).read_text(), flags=re.S))
    commit(tmp_path, "no review")


def _other_green(tmp_path, s):
    _edit_ledger(tmp_path, f"green {s.green}", f"green {s.red}")  # the record names another green than section 3 now does


_SUP = lambda n, t: _d(n, f"supersedes D{t}: use the v2 api as asked")  # unposted: no marker
DONE_CASES = {  # one case per pr/done rule family: (kwargs for the story, ledger change, rule)
    "decision-open": ({"dec": decisions.render_file(1001) + _d(1, "pick the v2 api")}, None),
    "decision-rejected": ({"dec": decisions.render_file(1001) + _d(1, "use the v2 api", status="rejected")}, None),
    "override-unposted": ({"dec": decisions.render_file(1001) + _d(1, "skip l2", kind="override")}, None),
    "supersedes-unposted": ({"dec": decisions.render_file(1001) + _d(1, "use the v1 api", status="rejected") + _SUP(2, 1)}, None),
    "decisions-invalid": ({"dec": decisions.render_file(1001) + _d(1, "ok") + "- [ ] D2: broken line without a status\n"}, None),
    "evidence-missing": ({}, lambda t, s: _edit_ledger(t, f"red {s.red} → green {s.green}", "done")),
    "record-missing": ({"record": False}, None),
    "record-incomplete": ({}, _other_green),  # `done` skips staleness, so this is its form of record-stale
    "fidelity-missing": ({"fidelity": False}, None),
    "review-missing": ({}, _no_review),
    "review-blocks": ({"review": "yes"}, None),
}


@pytest.mark.parametrize("rule", list(DONE_CASES))
def test_done_apply_strict_refuses_each_rule_family_and_sends_nothing(tmp_path, monkeypatch, capsys, git_env, rule):
    kw, change = DONE_CASES[rule]
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env, **kw)
    if change:
        change(tmp_path, s)
    _tick(tmp_path)
    before = DEC(tmp_path).read_bytes() if DEC(tmp_path).exists() else None
    assert mod.main(["done", "1001", "--apply", "--plan-out", "out/plan.md"]) == 2
    cap = capsys.readouterr()
    assert rule in [f["rule"] for f in json.loads(cap.out)["findings"]]
    assert cap.err.startswith("error: strict check failed: ") and rule in cap.err
    assert not (tmp_path / "out").exists() and fake["c"].calls == [] and sent == []  # no plan file, no request of any kind
    assert (DEC(tmp_path).read_bytes() if DEC(tmp_path).exists() else None) == before  # a refused run stamps nothing


@pytest.mark.parametrize("phase", ["pr", "done"])
def test_strict_check_never_executes_the_test_command(tmp_path, git_env, monkeypatch, phase):
    client, _ = _story(tmp_path, git_env)
    real_run, real_popen, git_calls = subprocess.run, subprocess.Popen, []

    def only_git(real):  # `subprocess.run` is built on Popen: both may start git and nothing else (the test command runs via /bin/sh)
        def wrapped(args, *a, **k):
            assert args[0] == "git", f"strict check ran {args[0]!r}"
            git_calls.append(args)
            return real(args, *a, **k)
        return wrapped

    def boom(*a, **k):
        raise AssertionError("strict check must not run the test command")
    monkeypatch.setattr(strict, "run_at", boom)
    monkeypatch.setattr(strict, "run_configured", boom)
    monkeypatch.setattr(subprocess, "run", only_git(real_run))
    monkeypatch.setattr(subprocess, "Popen", only_git(real_popen))
    res = _pr(tmp_path, client, phase)
    assert res["phase"] == phase and git_calls  # it did read git (a vacuous pass would read nothing)
    assert not any(f["rule"] in ("red-passes", "green-fails", "head-fails", "runner-error", "timeout") for f in res["findings"])


def test_done_strict_apply_stamps_used_then_sends(tmp_path, monkeypatch, capsys, git_env):
    dec = decisions.render_file(1001) + _d(1, "skip l2", kind="override", status="validated", posted=True)
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env, dec=dec, record=False, hub=True)
    _tick(tmp_path)
    before = DEC(tmp_path).read_bytes()
    assert mod.main(["done", "1001", "--plan-out", "p.md"]) == 0
    assert DEC(tmp_path).read_bytes() == before and fake["c"].gh.sent() == []
    assert mod.main(["done", "1001", "--apply"]) == 0
    assert decisions.parse_decisions(DEC(tmp_path).read_text())["validated"][0]["used"] == TODAY
    assert fake["c"].gh.writes_of() == ["comment", "close"]
    capsys.readouterr()


def test_done_stamp_write_failure_sends_nothing(tmp_path, monkeypatch, capsys, git_env):
    dec = decisions.render_file(1001) + _d(1, "skip l2", kind="override", status="validated", posted=True)
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env, dec=dec, record=False, hub=True)
    _tick(tmp_path)
    monkeypatch.setattr(decisions, "_atomic_write", lambda *a: (_ for _ in ()).throw(OSError("full")))
    assert mod.main(["done", "1001", "--apply"]) == 2
    assert fake["c"].gh.calls and fake["c"].gh.sent() == [] and "nothing written" in capsys.readouterr().err  # the plan's reads, no write


def test_done_strict_works_on_a_detached_head_with_red_and_green_gone(tmp_path, monkeypatch, capsys, git_env):
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env, hub=True)
    _tick(tmp_path)
    _squash(tmp_path, s)
    git(tmp_path, "checkout", "-q", "--detach")
    for b in ("squashed", "main", "trunk"):
        git(tmp_path, "branch", "-D", b)
    git(tmp_path, "reflog", "expire", "--expire=now", "--all")
    git(tmp_path, "gc", "-q", "--prune=now")
    assert subprocess.run(["git", "-C", str(tmp_path), "cat-file", "-e", s.green], capture_output=True).returncode != 0
    assert subprocess.run(["git", "-C", str(tmp_path), "symbolic-ref", "-q", "HEAD"], capture_output=True).returncode == 1
    assert mod.main(["done", "1001", "--apply"]) == 0
    assert "closed #1001 as completed" in capsys.readouterr().out and fake["c"].gh.issues[1001]["state"] == "closed"


def test_advisory_done_runs_no_strict_check(tmp_path, monkeypatch, capsys, git_env):
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env, mode=None, record=False, hub=True)
    _tick(tmp_path)
    monkeypatch.setattr(mod.strict, "check", lambda *a, **k: pytest.fail("strict check in advisory mode"))
    assert mod.main(["done", "1001", "--plan-out", "p.md"]) == 0 and (tmp_path / "p.md").is_file() and fake["c"].gh.sent() == []
    capsys.readouterr()


# ---- effective lists ----
def test_review_block_on_defaults_to_blocker_in_strict(tmp_path, monkeypatch, capsys, git_env):
    seen = []
    for mode, expect in (("strict", ["blocker"]), (None, [])):
        sub = tmp_path / (mode or "advisory")
        sub.mkdir()
        mod, fake, sent, s = _wire(sub, monkeypatch, git_env, mode=mode)
        real = mod.prs.review_blocks
        monkeypatch.setattr(mod.prs, "review_blocks", lambda wid, block_on, *a, _r=real, **k: seen.append(list(block_on)) or _r(wid, block_on, *a, **k))
        assert mod.main(PR_ARGS + ["--plan-out", "plan.md"]) == 0
        assert seen[-1] == expect
        monkeypatch.setattr(mod.prs, "review_blocks", real)
    capsys.readouterr()


def test_strict_pr_create_refuses_a_blocker_review_with_no_review_block_on_config(tmp_path, monkeypatch, capsys, git_env):
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env)
    _edit_ledger(tmp_path, "blocker 0", "blocker 1")
    assert mod.main(PR_ARGS + ["--plan-out", "out/p.md"]) == 2
    assert "review-blocks" in capsys.readouterr().out and not (tmp_path / "out").exists()


def _gate_repo(tmp_path, monkeypatch, block=()):
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", "gate": {"block": list(block)}}))
    (tmp_path / "dod").mkdir()
    from ghp.dod import SECTION2
    (tmp_path / "dod" / "DoD_1001.md").write_text(
        f"{SECTION2}\n## 3. AC\n| AC | x | tests | ev | status |\n|---|---|---|---|---|\n| AC-1 | x | Gone.testX | | red |\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)


def test_gate_in_strict_blocks_on_default_checks(tmp_path, monkeypatch, capsys):
    _gate_repo(tmp_path, monkeypatch, ())
    argv = ["gate", "1001", "--offline", "--src", str(tmp_path)]
    assert m.main(argv) == 0  # advisory, no gate.block: nothing blocks
    capsys.readouterr()
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", "mode": "strict"}))
    assert m.main(argv) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["blocking"] is True and "ac_traceability" in out["blocked_by"]


# ---- review fix round 1: default base, no tag shadowing, --head must be the checkout ----
def _rules_out(capsys):
    return [f["rule"] for f in json.loads(capsys.readouterr().out)["findings"]]


def test_strict_check_pr_defaults_the_base_to_origin_head(tmp_path, monkeypatch, capsys, git_env):
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env)
    argv = ["strict", "check", "1001", "--phase", "pr"]
    assert mod.main(argv) == 2 and _rules_out(capsys) == ["base-unknown"]  # no --base and no origin/HEAD: never a pass
    git(tmp_path, "update-ref", "refs/remotes/origin/trunk", s.green)  # the remote base already holds red and green
    git(tmp_path, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    assert mod.main(argv) == 2 and _rules_out(capsys) == ["not-in-range", "not-in-range"]
    git(tmp_path, "update-ref", "refs/remotes/origin/trunk", s.base)  # a base before the red commit
    assert mod.main(argv) == 0 and json.loads(capsys.readouterr().out)["ok"] is True
    git(tmp_path, "update-ref", "refs/remotes/origin/trunk", s.green)
    assert mod.main(argv + ["--base", _full(tmp_path, s.base)]) == 0  # an explicit --base wins over origin/HEAD
    capsys.readouterr()
    assert mod.main(["strict", "check", "1001", "--phase", "done"]) == 0  # done has no base


def test_the_default_base_is_never_overridable(tmp_path, monkeypatch, capsys, git_env):
    dec = decisions.render_file(1001) + _d(1, "skip l2", kind="override", posted=True)
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env, dec=dec)
    assert mod.main(["strict", "check", "1001", "--phase", "pr"]) == 2 and _rules_out(capsys) == ["base-unknown"]


def test_a_tag_never_stands_in_for_the_base_pr_create(tmp_path, monkeypatch, capsys, git_env):
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env)
    git(tmp_path, "update-ref", "refs/remotes/origin/trunk", s.green)  # the honest remote base holds red and green
    git(tmp_path, "tag", "origin/trunk", s.base)  # a tag named like the remote-tracking ref points before them
    assert strict.base_ref(str(tmp_path), "trunk") == "refs/remotes/origin/trunk"
    assert mod.main(PR_ARGS + ["--plan-out", "out/p.md"]) == 2 and _rules_out(capsys) == ["not-in-range", "not-in-range"]
    assert not (tmp_path / "out").exists() and sent == []


def test_a_tag_never_stands_in_for_the_local_base(tmp_path, monkeypatch, capsys, git_env):
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env)
    git(tmp_path, "branch", "-f", "trunk", s.green)
    git(tmp_path, "tag", "trunk", s.base)
    assert strict.base_ref(str(tmp_path), "trunk") == "refs/heads/trunk"
    assert mod.main(PR_ARGS + ["--plan-out", "out/p.md"]) == 2 and _rules_out(capsys) == ["not-in-range", "not-in-range"]
    assert not (tmp_path / "out").exists()


def test_strict_check_base_resolves_remote_then_local_then_a_full_commit_id_never_a_tag(tmp_path, git_env):
    client, s = _story(tmp_path, git_env)
    base, green = _full(tmp_path, s.base), _full(tmp_path, s.green)
    git(tmp_path, "tag", "v1", base)
    git(tmp_path, "tag", "origin/x", base)
    for name in ("v1", "origin/x", s.base, "nope"):  # a tag, a tag named like a remote ref, a short commit id, nothing
        assert _rules(_check(tmp_path, client, base=name)) == ["base-unknown"], name
    git(tmp_path, "update-ref", "refs/remotes/origin/x", green)
    git(tmp_path, "branch", "x", base)
    assert _rules(_check(tmp_path, client, base="origin/x")) == ["not-in-range"] * 2  # refs/remotes/origin/x, not the tag
    assert _rules(_check(tmp_path, client, base="x")) == []  # refs/remotes/x is absent: refs/heads/x (before red), not a tag
    assert _check(tmp_path, client, base=base)["ok"] is True and _rules(_check(tmp_path, client, base=green)) == ["not-in-range"] * 2
    assert _rules(_check(tmp_path, client, base="refs/tags/v1")) == ["base-unknown"]


def test_pr_create_head_must_be_the_checked_out_commit(tmp_path, monkeypatch, capsys, git_env):
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env)
    git(tmp_path, "branch", "other", s.base)  # a branch that is not what the working-tree ledger describes
    argv = [a if a != "main" else "other" for a in PR_ARGS]
    assert mod.main(argv + ["--plan-out", "out/p.md"]) == 2
    cap = capsys.readouterr()
    assert cap.err == "error: strict mode judges the working-tree ledger and decisions file: check out the --head branch first\n" and cap.out == ""
    assert not (tmp_path / "out").exists() and sent == [] and fake["c"].calls == []
    git(tmp_path, "branch", "-f", "other", "HEAD")  # another name for the same commit is fine
    fake["c"].gh.on_rest("GET", LIST % ("other", "trunk"), [])
    assert mod.main(argv + ["--plan-out", "out/p.md"]) == 0
    capsys.readouterr()


# ---- P2-5b: a strict project needs an approved stack profile at pr create and done ----
@pytest.mark.parametrize("draft", [False, True], ids=["missing", "draft"])
def test_pr_create_and_done_strict_refuse_a_missing_or_draft_stack_profile(tmp_path, monkeypatch, capsys, git_env, draft):
    from ghp import stack
    from conftest import profile
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env)
    if draft:
        (tmp_path / "ghp.stack.json").write_text(json.dumps(profile()))
    else:
        (tmp_path / "ghp.stack.json").unlink()
    rule = "stack-profile-draft" if draft else "stack-profile-missing"
    for argv in (PR_ARGS + ["--apply"], ["done", "1001", "--apply"]):
        assert mod.main(argv) == 2
        cap = capsys.readouterr()
        assert rule in [f["rule"] for f in json.loads(cap.out)["findings"]] and rule in cap.err
        assert "Run tests with" not in cap.out + cap.err and stack.PROFILE_FILE in cap.out
    assert sent == [] and fake["c"].calls == []


# ---- P3-10: business rules at `pr create`, `done` and `strict check` ----
def _rwire(tmp_path, monkeypatch, git_env, tags=RTAGS, enabled=True, **kw):
    """`_wire` for a story under business rules: Rules_1201.md is committed with the spec, the ledger gains the header lines and the tags,
    and `rules.enabled` goes into ghp.config.json (the CLI's config) when `enabled`."""
    (tmp_path / "dod").mkdir()
    (tmp_path / "dod" / "Rules_1201.md").write_text(RF, encoding="utf-8")
    out = _wire(tmp_path, monkeypatch, git_env, hook=lambda root: _rules_on(root, tags=tags), **kw)
    cfg = json.loads((tmp_path / "ghp.config.json").read_text())
    (tmp_path / "ghp.config.json").write_text(json.dumps({**cfg, **({"rules": {"enabled": True}} if enabled else {})}))
    return out


@pytest.mark.parametrize("apply", [False, True])
def test_pr_create_and_done_refuse_nothing_sent(tmp_path, monkeypatch, capsys, git_env, apply):
    mod, fake, sent, s = _rwire(tmp_path, monkeypatch, git_env, tags=" [BR-1201-1]")
    before = (LEDGER(tmp_path).read_bytes(), (tmp_path / "dod" / "Rules_1201.md").read_bytes())
    _tick(tmp_path)  # done needs section 5; pr create does not mind
    for argv in (PR_ARGS + ["--plan-out", "out/plan-pr.md"], ["done", "1001", "--plan-out", "out/plan.md"]):
        assert mod.main(argv + (["--apply"] if apply else [])) == 2
        cap = capsys.readouterr()
        out = json.loads(cap.out)
        f = next(f for f in out["findings"] if f["rule"] == "rule-uncovered")
        assert out["ok"] is False and "BR-1201-2" in f["detail"]
        assert cap.err.startswith("error: strict check failed: ") and "rule-uncovered" in cap.err and f["fix"] in cap.err
        assert "Customers earn" not in cap.err + cap.out and "Members earn" not in cap.err + cap.out
        assert not (tmp_path / "out").exists() and sent == [] and fake["c"].calls == []  # no plan file, no request of any kind
    assert LEDGER(tmp_path).read_bytes() != before[0] and (tmp_path / "dod" / "Rules_1201.md").read_bytes() == before[1]  # nothing written (only _tick)


def test_pr_create_passes_a_covered_story_under_rules_and_disabled_rules_change_nothing(tmp_path, monkeypatch, capsys, git_env):
    mod, fake, sent, s = _rwire(tmp_path, monkeypatch, git_env)
    assert mod.main(PR_ARGS + ["--plan-out", "out/plan-pr.md"]) == 0 and mod.main(PR_ARGS + ["--apply"]) == 0 and len(sent) == 1
    capsys.readouterr()


def test_strict_check_cli_reports_rules_findings_and_is_silent_with_rules_off(tmp_path, monkeypatch, capsys, git_env):
    mod, fake, sent, s = _rwire(tmp_path, monkeypatch, git_env, tags=" [BR-1201-1]")
    assert mod.main(["strict", "check", "1001", "--phase", "done"]) == 2  # done has no base ref to resolve
    out = json.loads(capsys.readouterr().out)
    assert [f["rule"] for f in out["findings"]] == ["rule-uncovered"] and fake["c"].calls == []
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", "mode": "strict", "strict": {"test_command": CMD}}))
    assert mod.main(["strict", "check", "1001", "--phase", "done"]) == 0  # the same repo with rules absent: nothing
    assert json.loads(capsys.readouterr().out)["findings"] == []


def test_strict_check_in_ci_judges_rules_from_the_target_branch_config(tmp_path, monkeypatch, capsys, git_env):
    """A PR that switches rules off in its own ghp.config.json (or drops the key) is still judged with them on: the target says so."""
    mod, fake, sent, s = _rwire(tmp_path, monkeypatch, git_env, tags=" [BR-1201-1]", enabled=False)
    head = git(tmp_path, "rev-parse", "HEAD")
    target = {"owner": "octo", "repo": "demo", "mode": "strict", "strict": {"test_command": CMD}, "rules": {"enabled": True}}
    git(tmp_path, "checkout", "-q", "-b", "target2", f"{s.red}~1")
    (tmp_path / "ghp.config.json").write_text(json.dumps(target))
    git(tmp_path, "add", "ghp.config.json")
    git(tmp_path, "commit", "-q", "-m", "target config")
    git(tmp_path, "checkout", "-q", "--detach", head)
    for own in ({"rules": {"enabled": False}}, {}):  # the PR's own config: rules off, or no key
        (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "octo", "repo": "demo", "mode": "strict",
                                                              "strict": {"test_command": CMD}, **own}))
        assert mod.main(["strict", "check", "1001", "--phase", "pr", "--head", head, "--base", "target2"]) == 2, own
        out = json.loads(capsys.readouterr().out)
        assert "rule-uncovered" in [f["rule"] for f in out["findings"]], own


# ---- T23 fix round 1 (review I1): strict `pr create` judges EVERY issue it closes, not the first one ----
ORDERS = [("1001", "4242"), ("4242", "1001")]  # a check of only the first, or only the last, issue passes one of the two orders


def _multi_argv(order, *extra):
    return ["pr", "create", *order, *PR_ARGS[3:], *extra]


@pytest.mark.parametrize("order", ORDERS, ids=["story-first", "story-last"])
def test_strict_pr_create_refuses_when_any_closed_issue_has_no_ledger(tmp_path, monkeypatch, capsys, git_env, order):
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env, more_issues=(4242,))
    assert mod.main(_multi_argv(order, "--plan-out", "out/p.md")) == 2
    cap = capsys.readouterr()
    out = json.loads(cap.out)
    bad = [f for f in out["findings"] if f["rule"] == "ledger-missing"]
    assert out["ok"] is False and len(bad) == 1 and bad[0]["detail"].startswith("#4242: ") and bad[0]["fix"] == "ghp dod 4242"
    assert all(f["detail"].startswith("#") for f in out["findings"])  # with several issues every finding names its issue
    assert cap.err.startswith("error: strict check failed: ") and not (tmp_path / "out").exists() and sent == []
    assert not [c for c in fake["c"].calls if c[0] == "rest" and c[1] != "GET"]


def test_strict_pr_create_checks_each_issue_once_and_in_the_given_order(tmp_path, monkeypatch, capsys, git_env):
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env, more_issues=(4242,))
    seen = []
    monkeypatch.setattr(strict, "check", lambda gh, cfg, n, phase, **k: seen.append((n, phase)) or {"ok": True, "overrides_used": []})
    mod.main(_multi_argv(("1001", "4242"), "--plan-out", "out/p.md"))
    capsys.readouterr()
    assert seen == [(1001, "pr"), (4242, "pr")]


@pytest.mark.parametrize("order", ORDERS, ids=["story-first", "story-last"])
def test_strict_pr_create_applies_the_review_block_to_every_closed_issue(tmp_path, monkeypatch, capsys, git_env, order):
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env, more_issues=(4242,))
    monkeypatch.setattr(strict, "check", lambda gh, cfg, n, phase, **k: {"ok": True, "overrides_used": []})
    asked = []
    monkeypatch.setattr(mod.prs, "review_blocks", lambda n, *a, **k: asked.append(n) or ("the review of #4242 blocks" if n == 4242 else None))
    assert mod.main(_multi_argv(order, "--plan-out", "out/p.md")) == 2
    err = capsys.readouterr().err
    assert "refusing to open a PR: #4242: the review of #4242 blocks" in err and not (tmp_path / "out").exists() and sent == []
    assert asked == [int(n) for n in order][:order.index("4242") + 1]  # stops at the first that blocks


def test_advisory_pr_create_still_reviews_only_the_first_issue(tmp_path, monkeypatch, capsys, git_env):
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env, mode=None, more_issues=(4242,))
    asked = []
    monkeypatch.setattr(mod.prs, "review_blocks", lambda n, *a, **k: asked.append(n) or None)
    mod.main(_multi_argv(("1001", "4242"), "--plan-out", "out/p.md"))
    capsys.readouterr()
    assert asked == [1001]
