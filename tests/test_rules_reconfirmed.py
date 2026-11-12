# P3-10b: `rules.reconfirmed`: the ledger's `Rules reviewed:` against the TARGET branch's copy of the ledger (ported from ado, T12). Only the pure
# comparison runs here; the git/gate/strict integration tests of ado's file are in test_rules_reconfirmed_ci.py (skipped until T23/T37; the gate landed in T22).
import pytest

from ghp import rules

A, B, C = "aaaaaaaa", "bbbbbbbb", "cccccccc"


def test_the_pure_comparison():
    old = {"BR-1201-1": A, "BR-1201-2": B, "BR-9-1": C}
    assert rules.reconfirmed(old, {"BR-1201-1": A, "BR-1201-2": C, "BR-1201-3": B}) == [
        {"id": "BR-9-1", "old": C, "new": None}, {"id": "BR-1201-2", "old": B, "new": C}]
    assert rules.reconfirmed({}, {"BR-1201-1": A}) == [] and rules.reconfirmed(old, old) == []
    many = {f"BR-5-{n}": A for n in range(1, 31)}
    text = rules.reconfirmed_text(rules.reconfirmed(many, {}))
    assert text.count(" -> none") == 20 and text.endswith("and 10 more")
