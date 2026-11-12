"""Python 3.13 deprecates passing maxsplit/count to re.split/sub/subn positionally (the hosted CI
run on 3.13 printed the warning); a later Python makes it an error. Pass them by keyword."""
import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
# name -> number of positional args allowed (pattern, [repl,] string)
ALLOWED = {"split": 2, "sub": 3, "subn": 3}


def _offenders(tree, name):
    for node in ast.walk(tree):
        f = getattr(node, "func", None)
        if (isinstance(node, ast.Call) and isinstance(f, ast.Attribute) and f.attr in ALLOWED
                and isinstance(f.value, ast.Name) and f.value.id == "re" and len(node.args) > ALLOWED[f.attr]):
            yield f"{name}:{node.lineno} re.{f.attr}"


def test_no_positional_maxsplit_or_count():
    bad = [o for p in sorted(SRC.rglob("*.py")) for o in _offenders(ast.parse(p.read_text("utf-8")), p.name)]
    assert not bad, bad


def test_the_check_sees_a_positional_maxsplit():
    assert list(_offenders(ast.parse("import re\nre.split('a', s, 1)\n"), "x.py"))
    assert not list(_offenders(ast.parse("import re\nre.split('a', s, maxsplit=1)\n"), "x.py"))
