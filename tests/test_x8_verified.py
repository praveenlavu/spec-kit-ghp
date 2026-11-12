"""T31d: facts the T31 live run OBSERVED (docs/evidence/T31.md) are out of the plan's unverified lists, in a verified list with their evidence,
and stated as observed in docs/trust-and-limits.md and the comments.py note."""
import re
from pathlib import Path

import pytest

from ghp import comments

ROOT = Path(__file__).resolve().parent.parent
PLAN_FILE = ROOT / "docs" / "plans" / "PLAN-0.1.md"


def plan():
    """The plan is internal: scripts/export_public.py leaves it out, so a test that reads it skips in the public export (and only there)."""
    if not PLAN_FILE.is_file():
        pytest.skip("docs/plans/PLAN-0.1.md is internal working material, not in the public export")
    return PLAN_FILE.read_text(encoding="utf-8")
TRUST = " ".join((ROOT / "docs" / "trust-and-limits.md").read_text(encoding="utf-8").split())


def section(n):
    m = re.search(r"^## %d\. .*?(?=^## %d\. )" % (n, n + 1), plan(), re.M | re.S)
    assert m, n
    return m.group(0)


def unverified_text():
    """Section 8 above its verified list, and section 9's paragraphs that add to the section 8 list."""
    s8 = section(8).split("**Verified live", 1)[0]
    s9 = section(9)
    adds = re.findall(r"\*\*Additions to the section 8 unverified list.*?\*\*\n(.*?)(?=\n\n)", s9, re.S)
    joins = re.findall(r"^- Unverified, to join section 8:.*$", s9, re.M)
    assert adds and joins
    return s8 + "\n".join(adds + joins)


def test_the_observed_facts_left_the_unverified_lists():
    text = unverified_text()
    for gone in ("the shape of an app- or bot-authored comment", "the bot comment shape", "the Actions-token `GET /user` 403",
                 "beyond the observed 0 and 2", "sends one `GET /user`"):
        assert gone not in text, gone


def test_section_8_lists_them_as_verified_with_the_evidence():
    verified = section(8).split("**Verified live", 1)[1]
    assert "docs/evidence/T31.md" in verified
    for fact in ("15368", "`performed_via_github_app` is `null`", "`userContentEdits.totalCount` is 2 after one edit", "`isMinimized: true`",
                 "`minimizedReason`", "REST `GET` of it is a 404", "no `CommentDeletedEvent`", "`__typename: User`", "`Bot`", "HTTP 403"):
        assert fact in verified, fact
    assert "deletion by another writer" in verified  # what was NOT observed stays said


def test_comments_note_and_trust_page_say_observed():
    assert "UNVERIFIED (no" not in comments.__doc__ and "2 after one edit" in comments.__doc__ and "T31" in comments.__doc__
    assert "Observed in a live run" in TRUST and "evidence/T31.md" in TRUST and "a direct read of it answers 404" in TRUST and "no deletion event" in TRUST
    assert "deletion of another person's comment by a writer was not tried" in TRUST
    assert "GitHub Actions app" in TRUST and "isMinimized" in TRUST


CI_RUN = ROOT / "docs" / "evidence" / "CI-RUN.md"


def test_the_green_hosted_run_is_recorded_and_left_the_unverified_lists():
    """T45-AC6: hosted run 42861951070 at bf48d35 was green on every job; the record exists and the two items moved to verified."""
    record = " ".join(CI_RUN.read_text(encoding="utf-8").split())
    for fact in ("42861951070", "`bf48d35`", "Python 3.9", "Python 3.13", "Lint the workflows", "success"):
        assert fact in record, fact
    for gone in ("**A green hosted CI run.**", "**Python 3.9 on hosted CI.**"):
        assert gone not in unverified_text(), gone
    verified = section(8).split("**Verified live", 1)[1]
    assert "docs/evidence/CI-RUN.md" in verified and "42861951070" in verified and "`bf48d35`" in verified


def test_plan_header_is_no_longer_a_draft_and_superseded_acs_are_marked():
    """T62: the plan says it is being built, the two ACs whose tests were removed by design say so, and section 9 records the audit's bookkeeping."""
    t = plan()
    status = re.search(r"^\*\*Status:\*\* .*$", t, re.M).group(0)
    assert "DRAFT" not in status and "No product code exists" not in status and "Nothing in this plan may be built" not in status
    ac = lambda task, n: re.search(r"^\*\*%s .*?^- AC%d (.*?)$" % (task, n), t, re.M | re.S).group(1)
    assert "(Superseded by T27" in ac("T04", 4) and "(Superseded by T28" in ac("T15", 2)
    s9 = section(9).split("**Bookkeeping at the roadmap audit (T62", 1)[1]  # the block itself: the ids occur in older notes too
    for needle in ("T25c", "T26f", "`b35e42d`", "T46", "`a1bdcfb`", "T47", "T55", "T56", "T57", "T58", "T59", "T60", "T05i",
                   "`ab7c1a5`", "`02d1961`", "`5d4274f`", "`cef288a`", "`3d72888`", "`f9372e0`", "`3e2142e`"):
        assert needle in s9, needle
    assert re.search(r"^- T05i .*settled", s9, re.M)


FORK_LABEL = "**Fork and other-repository refusals of `ghp done`.**"


def test_t26f_done_pr_facts_are_verified_and_the_fork_refusals_stay_unverified():
    """T26h: the live `done --pr` run (docs/evidence/T26f.md) answered the header's two open notes; the fork and other-repository refusals stay open."""
    from ghp import done
    doc = " ".join(done.__doc__.split())
    assert "has not run live" not in doc and "returns null (not a GraphQL error)" not in doc
    assert "VERIFIED live in T26f (docs/evidence/T26f.md)" in doc and "GraphQL NOT_FOUND error, not null" in doc and "`closer: null`" in doc
    still = doc.split("UNVERIFIED:", 1)[1]
    assert all(code in still for code in ("E_PR_FORK", "E_PR_FOREIGN", "E_CLOSER_FOREIGN"))
    verified = section(8).split("**Verified live", 1)[1]
    assert "docs/evidence/T26f.md" in verified and "`closer: null`" in verified and "E_PR_NOT_FOUND" in verified
    assert FORK_LABEL in unverified_text() and FORK_LABEL in TRUST


def test_the_lifecycle_run_is_in_the_verified_list_and_its_gaps_stay_said():
    verified = section(8).split("**Verified live", 1)[1]
    line = next(ln for ln in verified.splitlines() if ln.startswith("- T34 "))
    for fact in ("docs/evidence/T34.md", "terminal mode", "`claude` integration", "PR #204", "comment mode", "the poller", "not run live"):
        assert fact in line, fact
