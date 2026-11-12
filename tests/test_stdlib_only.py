import ast
import importlib.util
import sys
import sysconfig
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"


def _is_stdlib(name):
    top = name.split(".")[0]
    if top in sys.builtin_module_names:
        return True
    spec = importlib.util.find_spec(top)
    origin = getattr(spec, "origin", None)
    if spec is None or origin in (None, "frozen"):
        return spec is not None and origin == "frozen"
    origin = str(Path(origin).resolve())
    stdlib = str(Path(sysconfig.get_paths()["stdlib"]).resolve())
    return origin.startswith(stdlib) and "site-packages" not in origin


def offending_imports(root):
    bad = []
    for path in Path(root).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module]
            else:
                continue
            bad += [(path.name, n) for n in names
                    if n.split(".")[0] != "ghp" and not _is_stdlib(n)]
    return bad


def test_src_imports_only_stdlib_and_ghp():
    assert offending_imports(SRC / "ghp") == []


def test_the_scan_catches_a_planted_third_party_import():
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "planted.py").write_text("import yaml\nimport json\n")
        (Path(tmp) / "planted2.py").write_text("from yaml import safe_load\n")
        assert sorted(offending_imports(tmp)) == [("planted.py", "yaml"), ("planted2.py", "yaml")]
