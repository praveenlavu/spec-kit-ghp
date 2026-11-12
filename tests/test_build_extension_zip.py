"""T50: `scripts/build_extension_zip.py` builds the extension file the release carries: HEAD's tracked files minus `.extensionignore`, the same bytes
on every run (sorted entries, one fixed timestamp, fixed modes). The install from that file, with the real `specify`, is a contract test in
tests/test_speckit_contract.py (opt-in)."""
import importlib.util
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from test_manifest import COMMANDS, manifest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "build_extension_zip.py"


def _load():
    spec = importlib.util.spec_from_file_location("build_extension_zip", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bez = _load()
PATTERNS = bez.patterns((ROOT / ".extensionignore").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("ext")
    cp = subprocess.run([sys.executable, str(SCRIPT), str(out)], cwd=ROOT, capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr
    return Path(cp.stdout.strip())


def test_the_file_is_named_for_the_extension_not_the_bundle(built):
    assert built.name == "spec-kit-ghp-extension-%s.zip" % manifest()["extension"]["version"]
    assert built.name != "ghp-%s.zip" % manifest()["extension"]["version"]  # the bundle file's name


def test_two_builds_are_byte_identical(built, tmp_path):
    again = bez.build(tmp_path)
    assert again.read_bytes() == built.read_bytes()


def test_entries_are_sorted_with_one_timestamp_and_fixed_modes(built):
    with zipfile.ZipFile(built) as z:
        infos = z.infolist()
    names = [i.filename for i in infos]
    assert names == sorted(names) and len(names) == len(set(names))
    assert {i.date_time for i in infos} == {(1980, 1, 1, 0, 0, 0)}
    assert {i.external_attr >> 16 for i in infos} <= {0o100644, 0o100755}
    assert {i.create_system for i in infos} == {3}  # unix, so every unzip reads the modes


def test_an_executable_file_gets_0755_and_any_other_0644():
    """No tracked file that reaches the archive is executable today, so the 0755 branch is pinned on a fake tar member."""
    def member(mode):
        m = tarfile.TarInfo("bin/tool")
        m.mode = mode
        return m

    assert bez.zip_mode(member(0o755)) >> 16 == 0o100755 and bez.zip_mode(member(0o744)) >> 16 == 0o100755
    assert bez.zip_mode(member(0o644)) >> 16 == 0o100644


def test_the_manifest_and_every_command_file_are_inside(built):
    with zipfile.ZipFile(built) as z:
        names = set(z.namelist())
        assert z.read("extension.yml") == (ROOT / "extension.yml").read_bytes()
    assert {"commands/%s.md" % c for c in COMMANDS} <= names and len(COMMANDS) == 13
    assert {c["file"] for c in manifest()["provides"]["commands"]} <= names


def test_nothing_the_ignore_file_excludes_is_inside(built):
    with zipfile.ZipFile(built) as z:
        names = z.namelist()
    tracked = subprocess.run(["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    assert not [n for n in names if bez.ignored(n, PATTERNS)]
    assert ".extensionignore" not in names
    for prefix in ("src/", "tests/", "scripts/", "catalog/", "bundle/", "workflows/", ".github/", "docs/plans/", "docs/evidence/"):
        assert not [n for n in names if n.startswith(prefix)], prefix
    # every tracked file that is not excluded is inside: the file is the installed extension, no more and no less
    assert sorted(names) == sorted(f for f in tracked if not bez.ignored(f, PATTERNS))


@pytest.mark.parametrize("rel, out", [
    ("src/ghp/gh.py", True),            # `src/`: a folder, at the top
    ("a/src/x.py", True),               # a folder pattern without an inner `/` matches at any depth, as in git
    ("src", False),                     # a FILE named src is not the folder
    ("docs/plans/PLAN.md", True),       # an inner `/` anchors at the top
    ("x/docs/plans/PLAN.md", False),
    ("docs/quickstart.md", False),
    ("CLAUDE.md", True), ("rules/CLAUDE.md", True),  # a name without `/` matches at any depth
    ("CLAUDE.md.bak", False),
    ("pyproject.toml", True),
    (".extensionignore", True),         # Spec Kit always leaves the ignore file itself out
    ("commands/pull.md", False),
    ("extension.yml", False),
])
def test_the_matcher_follows_gitignore_for_the_forms_the_file_uses(rel, out):
    assert bez.ignored(rel, PATTERNS) is out


@pytest.mark.parametrize("line", ["*.pyc", "!keep.md", "a?b", "[ab]"])
def test_a_pattern_it_cannot_read_exactly_is_refused(line):
    with pytest.raises(ValueError):
        bez.patterns("src/\n" + line + "\n")


def test_comments_and_blank_lines_are_skipped_and_a_leading_slash_anchors():
    pats = bez.patterns("# a comment\n\n/top.md\n")
    assert pats == ["/top.md"]
    assert bez.ignored("top.md", pats) and not bez.ignored("sub/top.md", pats)


def test_wrong_usage_is_refused_with_exit_2():
    cp = subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, capture_output=True, text=True)
    assert cp.returncode == 2 and "Usage" in cp.stderr


def test_the_script_is_offline_and_standard_library_only():
    src = SCRIPT.read_text(encoding="utf-8")
    assert not re.search(r"\b(gh api|urllib|http\.client|socket|requests)\b", src)
    assert re.findall(r"^(?:import|from) (\w+)", src, re.M) and set(re.findall(r"^(?:import|from) (\w+)", src, re.M)) <= {
        "__future__", "io", "re", "subprocess", "sys", "tarfile", "zipfile", "pathlib", "typing"}
