"""T31b: every `/approve` or `/reject` example in docs/ and the public pages (T46) follows ghp's grammar (approvals.parse_command): a decision target `D<n>` takes NO
hash, a review or apply gate takes one. docs/evidence/ is excluded: those files record past runs, quoting the wrong form where it was found."""
import re
from pathlib import Path

import pytest

from ghp import approvals

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
HASH = r"(?:<hash>|<h>|[0-9a-f]{6,64})"
D_CMD = re.compile(r"/(?:approve|reject) (D(?:\d+|<n>))((?: +%s)?)" % HASH)
GATE_CMD = re.compile(r"/(?:approve|reject) (%s)\b((?: +%s)?)" % ("|".join(map(re.escape, approvals.GATES)), HASH))
HASHED = [g for g in approvals.GATES if g not in approvals.WAIT_GATES]  # a wait gate is met by comments, never approved by hash


def doc_files():
    """docs/ (without evidence), plus the command, rule and top-level pages: the public export has no plan, whose examples alone would leave
    docs/ without a gate example."""
    pages = [p for p in DOCS.rglob("*.md") if "evidence" not in p.relative_to(DOCS).parts]
    return sorted(pages + list((ROOT / "commands").glob("*.md")) + list((ROOT / "rules").glob("*.md")) + [p for p in ROOT.glob("*.md") if not p.name.startswith("SPIKE-")])  # the spike notes record the old form


def examples(pattern):
    return [(p.relative_to(ROOT).as_posix(), m.group(0), m.group(1), m.group(2)) for p in doc_files()
            for m in pattern.finditer(p.read_text(encoding="utf-8"))]


def test_decision_targets_take_no_hash():
    found = examples(D_CMD)
    assert found, "no `/approve D<n>` example in docs/: the check is vacuous"
    assert [f for f in found if f[3]] == []


def test_review_and_apply_gate_examples_carry_a_hash():
    found = [f for f in examples(GATE_CMD) if f[2] in HASHED]
    assert found, "no review- or apply-gate example in docs/: the check is vacuous"
    assert [f for f in found if not f[3]] == []


def test_the_patterns_agree_with_the_product_grammar():
    h = "a" * 64
    assert approvals.parse_command("/approve D1") and approvals.parse_command("/approve D1 " + h) is None
    assert approvals.parse_command("/approve review-spec " + h) and approvals.parse_command("/approve review-spec") is None
    assert D_CMD.search("/approve D1 <hash>").group(2) and not D_CMD.search("`/approve D1`").group(2)
    assert GATE_CMD.search("/approve review-spec <hash>").group(2) and not GATE_CMD.search("/approve review-spec`").group(2)


@pytest.mark.parametrize("text, bad", [("posts `/approve D1 <hash>`;", True), ("posts `/approve D1`;", False)])
def test_the_check_is_not_vacuous(text, bad):
    m = D_CMD.search(text)
    assert bool(m.group(2)) is bad
