"""T17b: the offline schema checks read the tracked subset fixture (tests/fixtures/gh/schema.subset.graphql), never the
git-ignored spike output, so a clean checkout (hosted CI) passes. The subset is cut from the saved public schema by
scripts/extract_schema_subset.py."""
import importlib.util
import re
from pathlib import Path

import pytest

from ghp import comments, done, gh, projects, status

TESTS = Path(__file__).resolve().parent
FIXTURE = TESTS / "fixtures" / "gh" / "schema.subset.graphql"
_spec = importlib.util.spec_from_file_location("extract_schema_subset", TESTS.parent / "scripts" / "extract_schema_subset.py")
ex = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ex)

BUILTIN = {"query", "mutation", "on", "true", "false", "null", "String", "Int", "ID", "Boolean", "__typename"}


def declared(text):
    """Every name the SDL declares: types, fields, arguments, enum values, union members."""
    text = re.sub(r"^#.*$", "", text, flags=re.M)
    return set(re.findall(r"^(?:type|interface|union|enum|input) (\w+)", text, re.M)) \
        | set(re.findall(r"^  (\w+)", text, re.M)) | set(re.findall(r"^    (\w+):", text, re.M)) \
        | set(re.findall(r"^  \| (\w+)", text, re.M))


def used(doc):
    doc = re.sub(r'"(?:[^"\\]|\\.)*"', "", doc)
    doc = re.sub(r"\$\w+(\s*:\s*[\[\]\w!]+)?", "", doc)  # variables, and their declared types
    return set(re.findall(r"[A-Za-z_]\w*", doc)) - BUILTIN


def documents():
    return [*projects.QUERIES.values(), *projects.MUTATIONS.values(), comments._QUERY, *done.QUERIES.values(), *status.QUERIES.values(), gh.VIEWER_QUERY]


def test_no_test_reads_the_ignored_spike_directory():
    me = Path(__file__).resolve()
    for f in sorted(TESTS.rglob("*.py")):
        if f.resolve() != me:
            text = f.read_text(encoding="utf-8")
            assert "spike/out" not in text and '"spike" / "out"' not in text, f


def test_fixture_has_every_name_the_code_queries():
    names = declared(FIXTURE.read_text(encoding="utf-8"))
    for doc in documents():
        assert used(doc) <= names, sorted(used(doc) - names)


def test_the_name_check_is_not_vacuous():
    assert "noSuchFieldAnywhere" not in declared(FIXTURE.read_text(encoding="utf-8"))
    assert used("query { user(login: $o) { noSuchFieldAnywhere } }") - declared(FIXTURE.read_text(encoding="utf-8")) == {"noSuchFieldAnywhere"}


def test_fixture_is_small_public_and_says_where_it_came_from():
    text = FIXTURE.read_text(encoding="utf-8")
    assert len(text.encode()) < 150_000 and '"""' not in text and "@deprecated" not in text
    assert text.startswith("# GitHub's public GraphQL schema") and "public documentation" in text
    assert "scripts/extract_schema_subset.py" in text and "2026-11-08" in text


SRC = '''"""doc"""
type A implements Node & Other @docsCategory(name: "x") {
  """f"""
  a: Int
    @deprecated(reason: "no (really)")
  b(
    """arg"""
    x: Int
  ): String
  c: Int
}

enum E {
  ONE
}
'''


def test_extractor_keeps_listed_fields_and_whole_types_and_strips_docs():
    out = ex.extract(SRC, ["A:b", "E"])
    assert out == "type A implements Node & Other {\n  b(\n    x: Int\n  ): String\n}\n\nenum E {\n  ONE\n}\n"


@pytest.mark.parametrize("target", ["Nope", "A:zzz"])
def test_extractor_refuses_a_missing_type_or_field(target):
    with pytest.raises(KeyError):
        ex.extract(SRC, [target])
