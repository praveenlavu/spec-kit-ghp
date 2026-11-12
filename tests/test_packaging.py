import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

try:
    import tomllib  # 3.11+
except ImportError:  # dev-only fallback for 3.9/3.10; tomli is in the dev venv
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent


def _project():
    with open(ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]


def test_pyproject_declares_no_runtime_dependency():
    project = _project()
    assert "dependencies" not in project
    assert "dependencies" not in project.get("dynamic", [])


def test_requires_python_floor_is_3_9():
    assert _project()["requires-python"] == ">=3.9"


def _pip_list(py):
    out = subprocess.run(
        [str(py), "-m", "pip", "list", "--format=freeze"],
        check=True, capture_output=True, text=True,
    ).stdout
    return set(out.split())


def test_install_into_clean_venv_adds_only_the_package():
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, "-m", "venv", tmp + "/v"], check=True)
        py = Path(tmp) / "v" / ("Scripts" if os.name == "nt" else "bin") / "python"
        before = _pip_list(py)
        # The clean venv's bundled pip and setuptools are too old for [project]; borrow
        # the dev interpreter's (both come in via PYTHONPATH) for the install call only
        # (PYTHONPATH is not set for the `pip list` calls, so it cannot leak into the
        # comparison). --no-index without --no-deps: a declared dependency fails the install.
        dev_site = next((p for p in sys.path if p.endswith("site-packages")), None)
        assert dev_site, "no site-packages entry on sys.path to borrow setuptools from"
        env = dict(os.environ, PYTHONPATH=dev_site)
        subprocess.run(
            [str(py), "-m", "pip", "install", "--no-build-isolation",
             "--no-index", "--disable-pip-version-check", str(ROOT)],
            check=True, env=env, cwd=tmp,
        )
        added = _pip_list(py) - before
        assert len(added) == 1 and next(iter(added)).startswith("spec-kit-ghp=="), added
        # AC4 end to end: the installed console script runs.
        script = py.parent / ("ghp.exe" if os.name == "nt" else "ghp")
        out = subprocess.run([str(script), "--version"], check=True,
                             capture_output=True, text=True).stdout
        assert out == "ghp 0.1.0\n"


def test_wheel_contains_seeds_and_golden():
    """T10 AC2: the wheel carries every seed and the golden fidelity file, byte-identical to the checkout's. Built offline from a copy of
    the sources (so the checkout gets no build/ directory) with the dev interpreter's own setuptools and wheel."""
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "src-copy", Path(tmp) / "out"
        shutil.copytree(ROOT / "src", src / "src", ignore=shutil.ignore_patterns("*.egg-info", "__pycache__"))
        shutil.copy(ROOT / "pyproject.toml", src)
        subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-build-isolation", "--no-index", "--no-deps",
                        "--disable-pip-version-check", "-w", str(out), str(src)], check=True, capture_output=True, cwd=tmp)
        (wheel,) = out.glob("spec_kit_ghp-*.whl")
        with zipfile.ZipFile(wheel) as z:
            names = set(z.namelist())
            wanted = ["ghp/seeds/node-typescript.json", "ghp/seeds/python-pytest.json", "ghp/seeds/salesforce.json", "ghp/golden/fidelity.json"]
            assert set(wanted) <= names, sorted(names)
            assert not [n for n in names if n.startswith("ghp/seeds/") and n not in wanted]  # exactly the three seeds
            for n in wanted:
                assert z.read(n) == (ROOT / "src" / n).read_bytes(), n


def test_project_metadata_names_the_readme_the_repository_and_the_author():
    """T45-AC2: an index page shows a description, the repository and the author (a name only, no e-mail)."""
    project = _project()
    assert project["readme"] == "README.md" and project["description"]
    assert project["urls"]["Repository"] == "https://github.com/praveenlavu/spec-kit-ghp"
    assert project["authors"] == [{"name": "praveenlavu"}]


def test_sdist_leaves_out_the_tests_and_carries_the_readme():
    """T45-AC2: an offline sdist build ships no tests/ (they need fixtures it would not carry) and its metadata holds the README."""
    import tarfile
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src-copy"
        shutil.copytree(ROOT, src, ignore=shutil.ignore_patterns(".git", ".venv", "*.egg-info", "__pycache__", "build", ".superpowers", "spike"))
        out = Path(tmp) / "out"
        code = "import sys, setuptools.build_meta as b; print(b.build_sdist(sys.argv[1]))"
        subprocess.run([sys.executable, "-c", code, str(out)], check=True, capture_output=True, cwd=src)
        (sdist,) = out.glob("spec_kit_ghp-*.tar.gz")
        with tarfile.open(sdist) as t:
            names = t.getnames()
            info = t.extractfile([n for n in names if n.count("/") == 1 and n.endswith("/PKG-INFO")][0]).read().decode()
    assert not [n for n in names if "/tests/" in n], [n for n in names if "/tests/" in n][:5]
    assert any(n.endswith("/src/ghp/__main__.py") for n in names)
    assert "Description-Content-Type: text/markdown" in info and "spec-kit-ghp is an independent, community-made project" in info
    assert "Project-URL: Repository, https://github.com/praveenlavu/spec-kit-ghp" in info and "Author: praveenlavu" in info
    assert "@" not in "".join(ln for ln in info.splitlines() if ln.startswith("Author"))


def test_offline_install_docs_name_the_setuptools_floor():
    """T45-AC3: below setuptools 61 the PEP 621 metadata is ignored, so --no-build-isolation needs the floor said out loud."""
    for name in ("SECURITY.md", "CONTRIBUTING.md"):
        t = (ROOT / name).read_text(encoding="utf-8")
        assert '"setuptools>=61"' in t and "--no-build-isolation" in t, name
