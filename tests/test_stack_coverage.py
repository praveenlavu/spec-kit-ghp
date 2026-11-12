"""P2-16a: a profile with no test command makes any strict.test_command drift, one coverage rule for `stack show --check` and strict, and
detected roots a profile cannot name are listed but never counted as `stack-unprofiled`."""
import json

import pytest

# T10/T23: the whole file was one importorskip of ado's test_ci_trust / test_strict / test_docs helpers; T23 ported the first two, so it runs. The doc test reads
# `docs/stack-kits.md`, which lands with T38 (it skips itself, naming the owner, until the file exists).
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _flat(path):
    if not (ROOT / path).exists():
        pytest.skip(f"{path} lands with T38 (docs)")
    return " ".join((ROOT / path).read_text().split())


from ghp import gate, stack
from ghp.__main__ import main
from conftest import approve, commit, git, profile
from test_ci_trust import ORG, STRICT, _ci, _gate, _gate_pr, _md, _rules_of, _run
from test_stack_show import config, put
from test_strict import _impl, _only, _rules, _stk

NULL_WHY = "tests run on the platform"
OVERRIDE = "ghp strict override"


def null_profile(**kw):
    p = profile(**kw)
    p["stacks"][0]["commands"]["test"] = {"run": None, "why": NULL_WHY}
    return p


def _show(capsys, *flags):
    code = main(["stack", "show", *flags])
    return code, json.loads(capsys.readouterr().out)


def test_null_test_command_profile_flags_any_config_command(tmp_path, monkeypatch, capsys, git_env, real_detect):
    approved = approve(null_profile())
    # local `stack show --check`: strict config with a hand-set command is drift; without one it is clean
    loc = tmp_path / "loc"
    loc.mkdir()
    git(loc, "init", "-q")
    commit(loc, "init", {"pyproject.toml": "[project]\n"})
    put(loc, approved)
    monkeypatch.chdir(loc)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    config(loc, "strict")
    code, res = _show(capsys, "--check")
    assert (code, res["notes"], res["test_command"]["drift"]) == (0, [], False)
    config(loc, "strict", test_command="echo hi")
    code, res = _show(capsys, "--check")
    assert code == 2 and res["test_command"] == {"profile": None, "config": "echo hi", "drift": True}
    assert res["notes"] and all(n.startswith("stack-profile:") for n in res["notes"]) and "echo hi" not in json.dumps(res["notes"])
    # local `strict check`
    d = tmp_path / "strict"
    d.mkdir()
    f = _only(_impl(_stk(d, git_env, approved, test_command="echo hi"), d), "stack-profile-drift")
    assert "echo hi" not in json.dumps(f) and "defines no test command" in f["detail"] and "ghp stack approve --apply" in f["fix"]
    # CI: the target branch's profile has no test command; the PR only adds strict.test_command
    c = tmp_path / "ci"
    c.mkdir()
    _ci(c, monkeypatch, git_env, {"ghp.stack.json": json.dumps(approved)}, target_cfg=STRICT, head_cfg={**STRICT, "strict": {"test_command": "echo hi"}})
    rc, out = _run(capsys)
    drift = [x for x in out["findings"] if x["rule"] == "stack-profile-drift"]
    assert rc == 2 and len(drift) == 1 and "at the head commit" in drift[0]["detail"] and "echo hi" not in json.dumps(out)
    # CI: the target's own config already carries a command its null-test profile does not define
    t = tmp_path / "tgt"
    t.mkdir()
    _ci(t, monkeypatch, git_env, {"ghp.stack.json": json.dumps(approved)}, target_cfg={**STRICT, "strict": {"test_command": "echo hi"}},
        head_cfg={**STRICT, "strict": {"test_command": "echo hi"}})
    rc, out = _run(capsys)
    assert rc == 2 and _rules_of(out) == ["stack-profile-drift"] and "at the head commit" not in out["findings"][0]["detail"]
    # control: no command anywhere, in CI, is consistent with a null test (only the unset-command finding, which an l2 override waives)
    k = tmp_path / "ctl"
    k.mkdir()
    _ci(k, monkeypatch, git_env, {"ghp.stack.json": json.dumps(approved)}, target_cfg=STRICT, head_cfg=STRICT)
    assert _rules_of(_run(capsys)[1]) == ["test-command"]


def test_test_command_fix_text_points_to_override_when_profile_exists(tmp_path, git_env, real_detect):
    old = "set strict.test_command in ghp.config.json"
    d = tmp_path / "null"
    d.mkdir()
    res = _impl(_stk(d, git_env, approve(null_profile()), test_command=None), d)
    f = [x for x in res["findings"] if x["rule"] == "test-command"][0]
    assert old not in f["fix"] and OVERRIDE in f["fix"] and " l2 --reason " in f["fix"] and "a human" in f["fix"] and "own terminal" in f["fix"]
    for n, prof in enumerate((profile(), None)):  # a draft is a profile that exists; none is the old text
        e = tmp_path / f"p{n}"
        e.mkdir()
        f = [x for x in _impl(_stk(e, git_env, prof, test_command=None), e)["findings"] if x["rule"] == "test-command"][0]
        assert (old in f["fix"]) == (prof is None) and (OVERRIDE in f["fix"]) == (prof is not None)
    h = tmp_path / "defined"  # a defined test command: approval sets it, or a human overrides
    h.mkdir()
    f = [x for x in _impl(_stk(h, git_env, approve(profile()), test_command=None), h)["findings"] if x["rule"] == "test-command"][0]
    assert old not in f["fix"] and "ghp stack approve --apply" in f["fix"] and OVERRIDE in f["fix"]


def _case(files, pid="python", root="."):
    p = profile()
    p["stacks"][0].update(id=pid, root=root)
    p["stacks"][0]["commands"]["test"] = {"run": None, "why": NULL_WHY}
    return approve(p), files


COVERAGE = {
    "same-root-other-family": (("python", "."), {"pyproject.toml": "[project]\n", "package.json": "{}\n", "tsconfig.json": "{}\n"}, ["./typescript"]),
    "same-root-node-vs-python": (("python", "."), {"pyproject.toml": "[project]\n", "package.json": "{}\n"}, ["./node"]),
    "nested-root": (("python", "."), {"pyproject.toml": "[project]\n", "web/package.json": "{}\n"}, ["web/node"]),
    "nested-other-stack": (("node", "."), {"package.json": "{}\n", "api/pyproject.toml": "[project]\n"}, ["api/python"]),
    "same-family-ts-for-node": (("typescript", "."), {"package.json": "{}\n"}, []),
    "same-family-flutter-for-dart": (("flutter", "."), {"pubspec.yaml": "name: x\n"}, []),
    "other-family-flutter-for-node": (("flutter", "."), {"package.json": "{}\n"}, ["./node"]),
    "free-id-covers-root": (("web-app", "."), {"package.json": "{}\n"}, []),
    "profile-root-elsewhere": (("python", "api"), {"pyproject.toml": "[project]\n"}, ["./python"]),
    "covered-both": (("python", "."), {"pyproject.toml": "[project]\n"}, []),
}


@pytest.mark.parametrize("case", COVERAGE)
def test_show_check_and_strict_agree_on_family_coverage(tmp_path, monkeypatch, capsys, git_env, real_detect, case):
    (pid, root), files, want = COVERAGE[case]
    prof, files = _case(files, pid, root)
    client = _stk(tmp_path, git_env, prof, files=files, test_command=None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    config(tmp_path, "strict")
    code, res = _show(capsys, "--check")
    shown = sorted(n.split()[1] + "/" + n.split()[2].strip("()") for n in res["notes"] if n.startswith("stack-unprofiled:"))
    out = _impl(client, tmp_path)
    detail = [x["detail"] for x in out["findings"] if x["rule"] == "stack-unprofiled"]
    want = sorted(want)
    assert shown == want, (shown, want)  # the same root/id pairs, from both
    assert (code == 2) == bool(want) and bool(detail) == bool(want)
    for w in want:
        r, i = w.rsplit("/", 1)
        assert f"{r} ({i})" in detail[0]


def test_unrepresentable_roots_are_ignored_but_listed(tmp_path, monkeypatch, capsys, git_env, real_detect):
    git(tmp_path, "init", "-q")
    files = {"pyproject.toml": "[project]\n", ".github/tools/package.json": "{}\n", "my app/package.json": "{}\n", "café/package.json": "{}\n",
             "docs/.hidden/package.json": "{}\n"}
    commit(tmp_path, "init", files)
    res = stack.detect(str(tmp_path))
    assert [(s["id"], s["root"]) for s in res["stacks"]] == [("python", ".")] and res["outcome"] == "detected"
    ig = res["ignored_roots"]
    assert ig["count"] == 4 and len(ig["paths"]) == 4 and ".github/tools" in ig["paths"]
    assert "my app" not in json.dumps(res) and "café" not in json.dumps(res) and sum(p.startswith("<path #") for p in ig["paths"]) == 2
    # at most 5 are listed, the count is the whole
    many = {f"d{n} x/package.json": "{}\n" for n in range(7)}
    commit(tmp_path, "more", many)
    ig = stack.detect(str(tmp_path))["ignored_roots"]
    assert ig["count"] == 11 and len(ig["paths"]) == 5
    # a repo with nothing unrepresentable has no key at all (the output is unchanged)
    clean = tmp_path / "clean"
    clean.mkdir()
    git(clean, "init", "-q")
    commit(clean, "init", {"pyproject.toml": "[project]\n"})
    assert "ignored_roots" not in stack.detect(str(clean))
    # an unrepresentable-only repo is `unknown`, and `ghp stack detect` prints the key
    only = tmp_path / "only"
    only.mkdir()
    git(only, "init", "-q")
    commit(only, "init", {".github/tools/package.json": "{}\n"})
    res = stack.detect(str(only))
    assert res["outcome"] == "unknown" and res["stacks"] == [] and res["ignored_roots"] == {"count": 1, "paths": [".github/tools"]}
    assert main(["stack", "detect", "--repo", str(only)]) == 0
    assert json.loads(capsys.readouterr().out)["ignored_roots"]["count"] == 1
    # neither strict nor `show --check` counts them
    d = tmp_path / "strict"
    d.mkdir()
    prof, _ = _case({})
    client = _stk(d, git_env, prof, files=files, test_command=None)
    assert "stack-unprofiled" not in _rules(_impl(client, d))
    monkeypatch.chdir(d)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    config(d, "strict")
    code, shown = _show(capsys, "--check")
    assert code == 0 and shown["notes"] == []


@pytest.mark.parametrize("root", [".", "a", "a/b", "a-b_c.d/e", "x" * 200])
def test_representable_roots_match_the_validator(root):
    assert stack.representable_root(root)


@pytest.mark.parametrize("root", [".github", "a/.b", "a b", "café", "../x", "-rf", "a//b", "", "x" * 201, "a/", "/a", "a\nb"])
def test_unrepresentable_roots_match_the_validator(root):
    p = profile()
    p["stacks"][0]["root"] = root
    assert not stack.representable_root(root) and stack.validate(p)


IGNORED = {".tools/package.json": "{}\n", "web app/package.json": "{}\n"}


def test_ignored_roots_are_a_warning_never_a_finding(tmp_path, monkeypatch, capsys, git_env, real_detect):
    note = gate.ignored_roots_note(2)
    assert note == "2 detected root(s) cannot be named in a profile and are not covered by it: review them as code"
    # local strict check, every phase that runs the stack rules: a warning beside the findings, ok unchanged, no path in it
    d = tmp_path / "local"
    d.mkdir()
    client = _stk(d, git_env, approve(profile()), files=IGNORED)
    res = _impl(client, d)
    assert res == {"phase": "implement", "mode": "strict", "ok": True, "findings": [], "overrides_used": [], "warnings": [note]}
    for phase in ("pr", "done"):
        assert _impl(client, d, phase)["warnings"][-1] == note
    assert ".tools" not in json.dumps(res) and "web app" not in json.dumps(res)
    c = tmp_path / "clean"  # nothing ignored: no warning key at all
    c.mkdir()
    assert "warnings" not in _impl(_stk(c, git_env, approve(profile())), c)
    # CI with --base: the PR adds the ignored roots; still rc 0, one warning
    ci = tmp_path / "ci"
    ci.mkdir()
    _ci(ci, monkeypatch, git_env, {"ghp.stack.json": json.dumps(approve(profile()))}, head_files=IGNORED)
    rc, out = _run(capsys)
    assert (rc, out["ok"], out["findings"], out["warnings"]) == (0, True, [], [note])
    # the gate comment carries the same line once, and the gate is not blocked by it
    g = tmp_path / "gate"
    g.mkdir()
    summary = _gate_pr(g, monkeypatch, git_env, STRICT, ORG, head_files=IGNORED)
    rc, out = _gate(capsys, summary, "--base", "origin/main")
    assert out["ignored_roots_note"] == note and _md(summary).splitlines().count(note) == 1
    n = tmp_path / "none"
    n.mkdir()
    summary = _gate_pr(n, monkeypatch, git_env, STRICT, ORG)
    assert "ignored_roots_note" not in _gate(capsys, summary, "--base", "origin/main")[1]
    # a tampered key is not rendered
    assert "x" not in gate.render_summary({"ignored_roots_note": "x\n# evil", "config": "?", "note": "n"})


def test_ignored_roots_warning_text_is_pinned_in_the_docs():
    text = "detected root(s) cannot be named in a profile and are not covered by it: review them as code"
    for doc in ("docs/stack-kits.md", "README.md", "CHANGELOG.md"):
        assert text in _flat(doc), doc
    assert "It is not silent either" not in _flat("docs/stack-kits.md")
