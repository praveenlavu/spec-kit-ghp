"""T23: every regex and loop of strict.py and kits.py that reads PR-controlled text is linear. Each runs on 300,000 characters of the shapes that make a backtracking
engine quadratic (long runs of spaces, repeated prefixes, near-matches) and must finish well inside a fixed deadline. ado's patterns were measured and are kept as they are
(all of them linear: measured 0.001 to 0.09 s here); this test is the guard that a later edit does not make one quadratic. The deadline is generous (2 s): a quadratic
pattern on 300,000 characters takes minutes."""
import time

import pytest

from ghp import dod, kits, strict

N = 300_000
SHAPES = {
    "spaces": " " * N, "red-spaces": "red" + " " * N + "x", "red-colon": "red:" * (N // 4), "red-hex-spaces": "red abcdef0" + " " * N,
    "red-hex-arrow": "red abcdef0 -> " + " " * N + "x", "green-repeat": "green a" * (N // 7), "pairs": "red abcdef0 -> green abcdef0 " * (N // 30),
    "hex-run": "red " + "a" * N, "arrows": "->" * (N // 2), "arrows-spaced": "-> " * (N // 3), "open-paren": "_(" * (N // 2),
    "fill-repeat": "_(fill " * (N // 7), "stars": "- **" * (N // 4), "ticks": "`*_" * (N // 3), "d-ids": "D1 " * (N // 3), "d-digits": "D" + "1" * N,
    "dashes": "- " * (N // 2), "hashes": "#" * N,
}
LEDGER = f"# D\n- **Spec dir:** s\n\n{dod.SECTION2}\n\n## 3. AC\n\n## 4. Log\n### Verification — 2026-01-01 @ abcdef012345\n"
FLAGS = lambda: {x: [] for x in (*kits._FLAGS, "allowed_tools")}  # noqa: E731

CASES = {
    "parse_evidence": lambda v: strict.parse_evidence(v),
    "_bullet_problem one line": lambda v: strict._bullet_problem([v], "Approach"),
    "_bullet_problem many lines": lambda v: strict._bullet_problem(["- **Approach:** a"] + v.split(" "), "Approach"),
    "_PLACEHOLDER": lambda v: strict._PLACEHOLDER.search(v),
    "_hidden_lines": lambda v: strict._hidden_lines(v + "\n" + v, 1),
    "_ids": lambda v: strict._ids([v]),
    "_STATUS_ARROW": lambda v: strict._STATUS_ARROW.search(v),
    "_D_TOKEN": lambda v: strict._D_TOKEN.search(v),
    "_V_HEADING": lambda v: strict._V_HEADING.findall(v),
    "_F_HEADING": lambda v: strict._F_HEADING.findall(v),
    "_F_LINE": lambda v: strict._F_LINE.fullmatch("- AC-1: asserts — " + v),
    "_V_AC": lambda v: strict._V_AC.fullmatch(v),
    "_PR_HEAD": lambda v: strict._PR_HEAD.findall(v),
    "parse_fidelity": lambda v: strict.parse_fidelity("## 2. x\n## 4. Log\n### AC fidelity — 2026-01-01 @ abcdef0\n" + v + "\n"),
    "validate_record": lambda v: strict.validate_record(LEDGER + ("- AC-1: red abcdef012345 exit 1 · green abcdef012345 exit 0 · L1 ok\n" * 3000) + v
                                                       + "\n- head: abcdef012345 exit 0\n", [], ".", "HEAD", "dod", None, 1, squash_ok=True, command=None),
    "_with_record": lambda v: strict._with_record(LEDGER + v + "\n## 5. D\n", "### Verification — 2026-01-01 @ x", ["- a"]),
    "kits._manifest_facts": lambda v: kits._manifest_facts(v + "\nhooks:\n  x:\n    optional: false\n"),
    "kits._manifest_facts lines": lambda v: kits._manifest_facts("\n".join(v[i:i + 500] for i in range(0, len(v), 500))),
    "kits._body_hits": lambda v: kits._body_hits("a.md", "\n".join(v[i:i + 900] for i in range(0, N, 900)), FLAGS()),
    "kits._repository": lambda v: kits._repository("extension:\n  repository: " + v),
    "kits._scalar": lambda v: kits._scalar(v),
    "kits._key": lambda v: kits._key(v + ": x"),
}


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("case", CASES)
def test_a_300000_character_hostile_input_is_linear(case, shape):
    t0 = time.monotonic()
    CASES[case](SHAPES[shape])
    assert time.monotonic() - t0 < 2.0
