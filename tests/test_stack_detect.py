"""`ghp stack detect` (V3 piece 2, P2-1): deterministic stack detection from the files tracked at a commit.

Real temp git repos; the paths are untrusted (a PR controls them), so the output is checked for what it must never carry."""
import json
import os
import subprocess

import pytest

from ghp import stack
from ghp.__main__ import main
from conftest import git, write_files


@pytest.fixture(autouse=True)
def _isolated_git(git_env):
    pass


def make(tmp_path, files=None, symlinks=None, commit=True):
    """A repo with `files` committed (`symlinks`: {path: target} committed as symlinks)."""
    git(tmp_path, "init", "-q")
    write_files(tmp_path, {"README.txt": "x\n", **(files or {})})
    for rel, target in (symlinks or {}).items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, tmp_path / rel)
    if commit:
        git(tmp_path, "add", "-A")
        git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def ids(res):
    return [(s["id"], s["root"], s["confidence"]) for s in res["stacks"]]


def cli(capsys, repo):
    code = main(["stack", "detect", "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


# ---- one test per D§4 row ----
def test_marker_python(tmp_path):
    res = stack.detect(make(tmp_path, {"pyproject.toml": "[project]\n"}))
    assert res["outcome"] == "detected" and ids(res) == [("python", ".", "high")]
    assert res["stacks"][0]["evidence"] == ["pyproject.toml"] and res["domain_hints"] == []


@pytest.mark.parametrize("name", ["setup.py", "setup.cfg", "requirements.txt", "requirements-dev.txt", "Pipfile"])
def test_python_medium_markers(tmp_path, name):
    res = stack.detect(make(tmp_path, {name: "x\n"}))
    assert ids(res) == [("python", ".", "medium")] and res["outcome"] == "unknown"


def test_marker_node_typescript(tmp_path):
    res = stack.detect(make(tmp_path, {"package.json": "{}"}))
    assert res["outcome"] == "detected" and ids(res) == [("node", ".", "high")]
    (tmp_path / "t").mkdir()
    assert ids(stack.detect(make(tmp_path / "t", {"package.json": "{}", "tsconfig.json": "{}"}))) == [("typescript", ".", "high")]


def test_typescript_replaces_node(tmp_path):
    res = stack.detect(make(tmp_path, {"web/package.json": "{}", "web/tsconfig.json": "{}", "api/package.json": "{}"}))
    assert ids(res) == [("node", "api", "high"), ("typescript", "web", "high")]
    assert res["stacks"][1]["evidence"] == ["web/package.json", "web/tsconfig.json"]
    (tmp_path / "x").mkdir()  # tsconfig.json alone is no marker
    assert stack.detect(make(tmp_path / "x", {"tsconfig.json": "{}"}))["outcome"] == "unknown"


def test_marker_dart_flutter(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "f").mkdir()
    dart = stack.detect(make(tmp_path / "d", {"pubspec.yaml": "name: a\nenvironment:\n  sdk: '>=3.0.0 <4.0.0'\n"}))
    flutter = stack.detect(make(tmp_path / "f", {"pubspec.yaml": "name: a\ndependencies:\n  flutter:\n    sdk: flutter\n"}))
    assert ids(dart) == [("dart", ".", "high")] and ids(flutter) == [("flutter", ".", "high")]


def test_pubspec_absorbs_android_gradle(tmp_path):
    res = stack.detect(make(tmp_path, {"pubspec.yaml": "dependencies:\n  flutter:\n    sdk: flutter\n",
                                       "android/build.gradle": "x\n", "android/app/build.gradle.kts": "x\n"}))
    assert ids(res) == [("flutter", ".", "high")]
    # a gradle project elsewhere is its own stack
    (tmp_path / "o").mkdir()
    res = stack.detect(make(tmp_path / "o", {"app/pubspec.yaml": "name: a\n", "app/android/build.gradle": "x\n",
                                             "server/build.gradle": "x\n"}))
    assert ids(res) == [("dart", "app", "high"), ("java", "server", "high")]


def test_pubspec_absorbs_desktop_runners(tmp_path):
    """Found on flutter/samples (P2-14): every app's generated linux/ and windows/ CMakeLists.txt were reported as c-cpp stacks."""
    res = stack.detect(make(tmp_path, {"pubspec.yaml": "dependencies:\n  flutter:\n    sdk: flutter\n",
                                       "linux/CMakeLists.txt": "x\n", "linux/flutter/CMakeLists.txt": "x\n", "windows/CMakeLists.txt": "x\n"}))
    assert ids(res) == [("flutter", ".", "high")] and res["outcome"] == "detected"
    (tmp_path / "o").mkdir()  # a CMake project beside the app, or a linux/ folder in a project without pubspec.yaml, is its own stack
    res = stack.detect(make(tmp_path / "o", {"app/pubspec.yaml": "name: a\n", "app/linux/CMakeLists.txt": "x\n", "native/CMakeLists.txt": "x\n",
                                             "kernel/linux/CMakeLists.txt": "x\n"}))
    assert ids(res) == [("dart", "app", "high"), ("c-cpp", "kernel/linux", "high"), ("c-cpp", "native", "high")]


def test_marker_salesforce(tmp_path):
    res = stack.detect(make(tmp_path, {"sfdx-project.json": "{}"}))
    assert ids(res) == [("salesforce", ".", "high")]
    (tmp_path / "m").mkdir()
    med = stack.detect(make(tmp_path / "m", {"force-app/main/default/classes/A.cls": "x\n"}))
    assert ids(med) == [("salesforce", ".", "medium")] and med["outcome"] == "unknown"
    assert med["stacks"][0]["evidence"] == ["force-app/"]


def test_sfdx_absorbs_package_json(tmp_path):
    pkg = json.dumps({"devDependencies": {"@salesforce/sfdx-lwc-jest": "1.0.0"}})
    res = stack.detect(make(tmp_path, {"sfdx-project.json": "{}", "package.json": pkg, "force-app/main/default/x/y.txt": "x\n"}))
    assert ids(res) == [("salesforce", ".", "high")] and res["outcome"] == "detected"
    assert res["stacks"][0]["hints"] == {"lwc_jest": True}
    assert res["stacks"][0]["evidence"] == ["sfdx-project.json", "force-app/"]
    # a package.json in another root is still a node stack
    (tmp_path / "o").mkdir()
    res = stack.detect(make(tmp_path / "o", {"sfdx-project.json": "{}", "package.json": "{}", "tools/package.json": "{}"}))
    assert ids(res) == [("salesforce", ".", "high"), ("node", "tools", "high")] and res["outcome"] == "multiple"


def test_marker_java(tmp_path):
    for i, (name, tool) in enumerate((("pom.xml", "maven"), ("build.gradle", "gradle"), ("build.gradle.kts", "gradle"))):
        sub = tmp_path / str(i)
        sub.mkdir()
        res = stack.detect(make(sub, {name: "x\n"}))
        assert ids(res) == [("java", ".", "high")] and res["stacks"][0]["hints"] == {"build_tool": tool}
    (tmp_path / "b").mkdir()
    both = stack.detect(make(tmp_path / "b", {"pom.xml": "x\n", "build.gradle": "x\n"}))
    assert both["stacks"][0]["hints"] == {"build_tool": "both"}


def test_marker_go(tmp_path):
    assert ids(stack.detect(make(tmp_path, {"go.mod": "module contoso.example/x\n"}))) == [("go", ".", "high")]


def test_marker_rust(tmp_path):
    assert ids(stack.detect(make(tmp_path, {"Cargo.toml": "[package]\n"}))) == [("rust", ".", "high")]


@pytest.mark.parametrize("name", ["App.csproj", "Lib.fsproj", "Contoso.sln"])
def test_marker_dotnet(tmp_path, name):
    assert ids(stack.detect(make(tmp_path, {f"src/{name}": "x\n"}))) == [("dotnet", "src", "high")]


def test_marker_c_cpp(tmp_path):
    (tmp_path / "m").mkdir()
    (tmp_path / "c").mkdir()
    assert ids(stack.detect(make(tmp_path, {"CMakeLists.txt": "x\n"}))) == [("c-cpp", ".", "high")]
    assert ids(stack.detect(make(tmp_path / "m", {"meson.build": "x\n"}))) == [("c-cpp", ".", "high")]
    mk = stack.detect(make(tmp_path / "c", {"Makefile": "x\n"}))
    assert ids(mk) == [("c-cpp", ".", "medium")] and mk["outcome"] == "unknown"


# ---- outcomes ----
def test_two_roots_is_multiple(tmp_path):
    res = stack.detect(make(tmp_path, {"backend/pyproject.toml": "[project]\n", "web/package.json": "{}", "web/tsconfig.json": "{}"}))
    assert res["outcome"] == "multiple" and ids(res) == [("python", "backend", "high"), ("typescript", "web", "high")]


def test_medium_only_reported_not_detected(tmp_path):
    res = stack.detect(make(tmp_path, {"requirements.txt": "flask\n", "Makefile": "all:\n"}))
    assert res["outcome"] == "unknown" and ids(res) == [("c-cpp", ".", "medium"), ("python", ".", "medium")]
    # a medium marker beside a high one of the same stack adds evidence, it does not make a second stack
    (tmp_path / "h").mkdir()
    res = stack.detect(make(tmp_path / "h", {"pyproject.toml": "[project]\n", "requirements.txt": "flask\n"}))
    assert ids(res) == [("python", ".", "high")] and res["stacks"][0]["evidence"] == ["pyproject.toml", "requirements.txt"]


def test_nothing_known_is_unknown(tmp_path):
    res = stack.detect(make(tmp_path, {"docs/appian/export.zip": "x\n", "rules/pega.json": "{}"}))
    assert res == {"outcome": "unknown", "stacks": [], "domain_hints": []}


def test_no_commit_is_unknown_with_note(tmp_path, capsys):
    make(tmp_path, {"pyproject.toml": "[project]\n"}, commit=False)  # staged-or-not, there is no commit
    git(tmp_path, "add", "-A")
    res = stack.detect(tmp_path)
    assert res == {"outcome": "unknown", "stacks": [], "domain_hints": [], "note": "commit first"}
    code, out, _ = cli(capsys, tmp_path)
    assert code == 0 and json.loads(out)["note"] == "commit first"


def test_untracked_marker_ignored(tmp_path):
    make(tmp_path, {"app/pyproject.toml": "[project]\n"})
    write_files(tmp_path, {"untracked/go.mod": "x\n"})
    write_files(tmp_path, {"staged/Cargo.toml": "x\n"})
    git(tmp_path, "add", "staged/Cargo.toml")  # staged is not committed: the commit decides
    res = stack.detect(tmp_path)
    assert ids(res) == [("python", "app", "high")]
    git(tmp_path, "rm", "-q", "--cached", "app/pyproject.toml")  # removed from the index only: still in the commit
    assert ids(stack.detect(tmp_path)) == [("python", "app", "high")]


def test_depth_over_3_ignored(tmp_path):
    res = stack.detect(make(tmp_path, {"a/b/pyproject.toml": "x\n", "a/b/c/go.mod": "x\n", "a/b/c/d/Cargo.toml": "x\n"}))
    assert ids(res) == [("python", "a/b", "high")]


def test_ls_tree_of_a_given_commit_not_head(tmp_path):
    make(tmp_path, {"go.mod": "x\n"})
    first = git(tmp_path, "rev-parse", "HEAD")
    write_files(tmp_path, {"Cargo.toml": "x\n"})
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "two")
    assert ids(stack.detect(tmp_path, first)) == [("go", ".", "high")]
    assert [i[0] for i in ids(stack.detect(tmp_path))] == ["go", "rust"]
    for bad in ("--output=x", "nosuchbranch", "refs/tags/v1", "../x", "0" * 40, ""):
        with pytest.raises(ValueError, match="^cannot read the commit$"):
            stack.detect(tmp_path, bad)


def test_not_a_repo_is_a_fixed_error(tmp_path, capsys):
    code, out, err = cli(capsys, tmp_path)
    assert code == 2 and out == "" and err == "error: cannot read git history here\n"


# ---- hints ----
def test_python_hints(tmp_path):
    res = stack.detect(make(tmp_path, {"pyproject.toml": "[tool.pytest.ini_options]\n", "src/pkg/__init__.py": ""}))
    assert res["stacks"][0]["hints"] == {"pytest": True, "src_layout": True}
    for i, files in enumerate(({"pyproject.toml": "[project]\n"}, {"pyproject.toml": "[project]\n", "pytest.ini": ""},
                               {"pyproject.toml": "[project]\n", "conftest.py": ""},
                               {"pyproject.toml": "[project]\n", "requirements.txt": "Pytest>=7\n"})):
        (tmp_path / str(i)).mkdir()
        h = stack.detect(make(tmp_path / str(i), files))["stacks"][0]["hints"]
        assert h == {"pytest": i != 0, "src_layout": False}, files
    (tmp_path / "n").mkdir()  # pytest-cov is not pytest
    assert stack.detect(make(tmp_path / "n", {"pyproject.toml": "x\n", "requirements.txt": "pytest-cov\n"}))["stacks"][0]["hints"]["pytest"] is False


def test_node_hints(tmp_path):
    pkg = json.dumps({"scripts": {"test": "jest"}})
    res = stack.detect(make(tmp_path, {"package.json": pkg, "pnpm-lock.yaml": ""}))
    assert res["stacks"][0]["hints"] == {"package_manager": "pnpm", "test_script": True}
    (tmp_path / "a").mkdir()
    amb = stack.detect(make(tmp_path / "a", {"package.json": "{}", "yarn.lock": "", "package-lock.json": ""}))
    assert amb["stacks"][0]["hints"] == {"package_manager": "ambiguous", "test_script": False}
    (tmp_path / "b").mkdir()
    bad = stack.detect(make(tmp_path / "b", {"package.json": "{not json", "package-lock.json": ""}))
    assert ids(bad) == [("node", ".", "high")] and bad["stacks"][0]["hints"] == {"package_manager": "npm", "test_script": False}


def test_media_and_pega_hints(tmp_path):
    for i, files in enumerate(({"pyproject.toml": 'dependencies = ["av>=10"]\n'}, {"requirements.txt": "ffmpeg-python==0.2\n", "setup.py": "x\n"},
                               {"CMakeLists.txt": "find_package(FFmpeg)\npkg_check_modules(libavcodec)\n"},
                               {"package.json": json.dumps({"dependencies": {"ffmpeg": "1"}})})):
        (tmp_path / str(i)).mkdir()
        assert stack.detect(make(tmp_path / str(i), files))["domain_hints"] == ["media"], files
    (tmp_path / "no").mkdir()  # a comment or a longer word is no media dependency
    assert stack.detect(make(tmp_path / "no", {"pyproject.toml": "# av\ndescription = 'avatar'\n"}))["domain_hints"] == []
    (tmp_path / "pega").mkdir()
    pega = stack.detect(make(tmp_path / "pega", {"package.json": json.dumps({"dependencies": {"@pega/react-sdk-components": "1"}}),
                                                  "tsconfig.json": "{}"}))
    assert ids(pega) == [("typescript", ".", "high")] and pega["stacks"][0]["hints"]["pega"] is True and pega["domain_hints"] == ["pega"]
    # Pega rulesets and Appian applications live in the platform, not in files: honestly unknown
    (tmp_path / "unk").mkdir()
    assert stack.detect(make(tmp_path / "unk", {"rulesets/MyApp.zip": "x\n", "appian/app.json": "{}"}))["outcome"] == "unknown"


# ---- hostile input ----
def test_symlinked_manifest_not_read(tmp_path):
    # the blob of a symlink is its target text: here a valid package.json that, if read, would set test_script and pega
    target = json.dumps({"scripts": {"test": "x"}, "dependencies": {"@pega/x": "1"}}, separators=(",", ":")).replace("/", "")
    res = stack.detect(make(tmp_path, symlinks={"package.json": target}))
    assert ids(res) == [("node", ".", "high")]
    assert res["stacks"][0]["hints"] == {"test_script": False} and res["domain_hints"] == []


def test_oversized_manifest_not_read(tmp_path):
    big = json.dumps({"scripts": {"test": "x"}, "dependencies": {"@pega/x": "1"}}) + " " * stack.MAX_MANIFEST
    small = json.dumps({"scripts": {"test": "x"}})
    res = stack.detect(make(tmp_path, {"big/package.json": big, "ok/package.json": small + " " * (stack.MAX_MANIFEST - len(small))}))
    by = {s["root"]: s for s in res["stacks"]}
    assert by["big"]["hints"] == {"test_script": False} and by["ok"]["hints"] == {"test_script": True} and res["domain_hints"] == []


def test_control_char_path_printed_as_placeholder(tmp_path, capsys):
    evil = "ev\x1b[31mil\nname"
    make(tmp_path, {f"{evil}/pyproject.toml": "x\n", "sp ace/go.mod": "x\n", "ok-dir/Cargo.toml": "x\n"})
    code, out, err = cli(capsys, tmp_path)
    assert code == 0 and "\x1b" not in out and "31m" not in out and "il\\nname" not in out and "sp ace" not in out
    res = json.loads(out)
    # (P2-16a) roots no profile can name are no stacks; they are counted and listed through safe_path, numbered after the stacks' own paths
    assert [(s["id"], s["root"], s["evidence"]) for s in res["stacks"]] == [("rust", "ok-dir", ["ok-dir/Cargo.toml"])]
    assert res["ignored_roots"] == {"count": 2, "paths": ["<path #3>", "<path #4>"]}


def test_non_utf8_path_is_a_placeholder(tmp_path):
    make(tmp_path, {"a/go.mod": "x\n"})
    blob = git(tmp_path, "rev-parse", "HEAD:README.txt")
    idx = f"100644 {blob}\t".encode() + b"caf\xe9/Cargo.toml\0"
    subprocess.run(["git", "-C", str(tmp_path), "update-index", "--add", "-z", "--index-info"], input=idx, check=True, capture_output=True)
    git(tmp_path, "commit", "-q", "-m", "latin1")
    res = stack.detect(tmp_path)
    assert [(s["id"], s["root"]) for s in res["stacks"]] == [("go", "a")]  # (P2-16a) no profile can name a latin-1 root: ignored, listed
    assert res["ignored_roots"] == {"count": 1, "paths": ["<path #3>"]}


def test_safe_path():
    assert stack.safe_path("a/b-c_d.e", 1) == "a/b-c_d.e" and stack.safe_path(".", 1) == "."
    for bad in ("", "a b", "a\nb", "a\x1bb", "é", "a" * 201, "a b", "<!--", "a`b", "a�b", "a\udce9b"):
        assert stack.safe_path(bad, 7) == "<path #7>"
    assert stack.safe_path("a" * 200, 1) == "a" * 200


def test_marker_candidate_cap_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(stack, "MAX_CANDIDATES", 3)
    with pytest.raises(ValueError, match="^too many files to inspect$"):
        stack.detect(make(tmp_path, {f"d{i}/go.mod": "x\n" for i in range(4)}))


def test_output_byte_identical_twice(tmp_path, capsys):
    make(tmp_path, {"web/package.json": "{}", "api/pyproject.toml": "x\n", "tools/go.mod": "x\n", "bad dir/Cargo.toml": "x\n",
                    "Makefile": "x\n", "docs/requirements.txt": "av\n"})
    a, b = cli(capsys, tmp_path), cli(capsys, tmp_path)
    assert a == b and a[0] == 0 and json.loads(a[1])["outcome"] == "multiple"


def test_detect_changes_no_file_and_reads_no_working_tree(tmp_path):
    make(tmp_path, {"package.json": '{"scripts":{"test":"jest"}}'})
    write_files(tmp_path, {"package.json": '{"scripts":{}}'})  # the working tree differs from the commit: the commit wins
    before = git(tmp_path, "status", "--porcelain")
    assert stack.detect(tmp_path)["stacks"][0]["hints"]["test_script"] is True
    assert git(tmp_path, "status", "--porcelain") == before


def test_cli_help_and_usage(capsys):
    with pytest.raises(SystemExit) as e:
        main(["stack"])
    assert e.value.code == 2
    assert "--repo" in subprocess.run(["python3", "-m", "ghp", "stack", "detect", "--help"], capture_output=True, text=True,
                                      env={**os.environ, "PYTHONPATH": str(__import__("pathlib").Path(__file__).parents[1] / "src")}).stdout


# ---- review round 1 ----
def test_other_owner_checkout_works_with_global_safe_directory(tmp_path, monkeypatch):
    """Container CI: the checkout belongs to another uid and the global config trusts it. One git environment throughout."""
    repo = make(tmp_path, {"go.mod": "x\n"})
    cfg = tmp_path.parent / (tmp_path.name + ".gitconfig")
    cfg.write_text("[safe]\n\tdirectory = *\n")
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    assert ids(stack.detect(repo)) == [("go", ".", "high")]
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)  # untrusted: refused with the fixed text, not a crash
    with pytest.raises(ValueError, match="^cannot read git history here$"):
        stack.detect(repo)


def test_read_budget_exhausted_is_noted_and_flutter_still_decided(tmp_path):
    files = {f"n{i:02d}/package.json": json.dumps({"scripts": {"test": "x"}}) for i in range(stack.MAX_READS + 1)}
    files["z/pubspec.yaml"] = "dependencies:\n  flutter:\n    sdk: flutter\n"
    res = stack.detect(make(tmp_path, files))
    by = {s["root"]: s for s in res["stacks"]}
    assert by["z"]["id"] == "flutter" and res["note"] == "hints incomplete" and res["outcome"] == "multiple"
    assert by["n00"]["hints"]["test_script"] is True and by[f"n{stack.MAX_READS:02d}"]["hints"]["test_script"] is False
    (tmp_path / "ok").mkdir()
    assert "note" not in stack.detect(make(tmp_path / "ok", {"a/package.json": "{}"}))


def test_duplicate_key_package_json_is_unreadable(tmp_path):
    res = stack.detect(make(tmp_path, {"package.json": '{"scripts": {"test": "a"}, "scripts": {}, "dependencies": {"@pega/x": "1"}}'}))
    assert res["stacks"][0]["hints"] == {"test_script": False} and res["note"] == "hints incomplete" and res["domain_hints"] == []


def test_sha256_repository_is_refused_with_a_fixed_text(tmp_path):
    subprocess.run(["git", "init", "-q", "--object-format=sha256", str(tmp_path)], check=True, capture_output=True)
    write_files(tmp_path, {"go.mod": "x\n"})
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "x")
    with pytest.raises(ValueError, match="^cannot read the commit$"):
        stack.detect(tmp_path)


def test_submodule_named_like_a_marker_is_skipped(tmp_path):
    make(tmp_path, {"a/Cargo.toml": "x\n"})
    git(tmp_path, "update-index", "--add", "--cacheinfo", f"160000,{git(tmp_path, 'rev-parse', 'HEAD')},go.mod")
    git(tmp_path, "commit", "-q", "-m", "gitlink")
    assert ids(stack.detect(tmp_path)) == [("rust", "a", "high")]


def test_a_tag_never_stands_in_for_a_branch(tmp_path):
    make(tmp_path, {"go.mod": "x\n"})
    old = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "tag", "rel")
    git(tmp_path, "tag", "only-a-tag")
    write_files(tmp_path, {"Cargo.toml": "x\n"})
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "two")
    git(tmp_path, "branch", "rel")  # a branch and a tag share the name: the branch wins
    assert [i[0] for i in ids(stack.detect(tmp_path, "rel"))] == ["go", "rust"]
    git(tmp_path, "branch", "-q", "oldb", old)
    assert [i[0] for i in ids(stack.detect(tmp_path, "oldb"))] == ["go"]
    with pytest.raises(ValueError, match="^cannot read the commit$"):
        stack.detect(tmp_path, "only-a-tag")


@pytest.mark.parametrize("files,expect", [
    ({"package.json": "{}", "packages/a/package.json": "{}", "packages/b/package.json": "{}"},
     [("node", ".", ["package.json", "packages/a/package.json", "packages/b/package.json"])]),  # npm workspaces
    ({"pom.xml": "x", "core/pom.xml": "x", "web/pom.xml": "x"}, [("java", ".", ["pom.xml", "core/pom.xml", "web/pom.xml"])]),  # maven modules
    ({"build.gradle": "x", "app/build.gradle": "x", "lib/build.gradle.kts": "x"},
     [("java", ".", ["build.gradle", "app/build.gradle", "lib/build.gradle.kts"])]),  # gradle multi-project
    ({"Cargo.toml": "x", "crates/a/Cargo.toml": "x"}, [("rust", ".", ["Cargo.toml", "crates/a/Cargo.toml"])]),  # cargo workspace
    ({"package.json": "{}", "pkgs/a/package.json": "{}", "pkgs/a/tsconfig.json": "{}"},
     [("node", ".", ["package.json", "pkgs/a/package.json", "pkgs/a/tsconfig.json"])]),  # typescript package in a node workspace: one family
    ({"a/package.json": "{}", "a/b/package.json": "{}", "c/package.json": "{}"},
     [("node", "a", ["a/package.json", "a/b/package.json"]), ("node", "c", ["c/package.json"])]),  # siblings stay apart
])
def test_nested_roots_of_the_same_stack_fold_into_the_ancestor(tmp_path, files, expect):
    res = stack.detect(make(tmp_path, files))
    assert [(s["id"], s["root"], s["evidence"]) for s in res["stacks"]] == expect
    assert res["outcome"] == ("detected" if len(expect) == 1 else "multiple")


def test_roots_of_different_stacks_stay_separate_in_a_mixed_repo(tmp_path):
    res = stack.detect(make(tmp_path, {"pyproject.toml": "x\n", "web/package.json": "{}", "web/x/package.json": "{}",
                                       "web/x/tsconfig.json": "{}", "services/api/pyproject.toml": "x\n"}))
    assert [(s["id"], s["root"], len(s["evidence"])) for s in res["stacks"]] == [("python", ".", 2), ("node", "web", 3)]
    assert res["outcome"] == "multiple"


def test_folded_workspace_keeps_the_pega_hint(tmp_path):
    res = stack.detect(make(tmp_path, {"package.json": "{}", "packages/a/package.json": json.dumps({"dependencies": {"@pega/x": "1"}})}))
    assert len(res["stacks"]) == 1 and res["stacks"][0]["hints"]["pega"] is True


def test_folded_evidence_is_capped_and_safe(tmp_path):
    files = {"package.json": "{}", "packages/bad name/package.json": "{}", **{f"packages/p{i:02d}/package.json": "{}" for i in range(20)}}
    ev = stack.detect(make(tmp_path, files))["stacks"][0]["evidence"]
    assert len(ev) == stack.MAX_EVIDENCE and ev[0] == "package.json" and all(e.startswith(("package", "<path #")) for e in ev)
