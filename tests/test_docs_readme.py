"""The public face: README, quickstart and glossary stay honest, plain and link-clean."""
import os
import re
from pathlib import Path

import pytest

from ghp.__main__ import REGISTRY
from live.conftest import forbidden_accounts

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
DOCS = [README, ROOT / "docs" / "quickstart.md", ROOT / "docs" / "glossary.md"]
NOT_AFFILIATED = ("spec-kit-ghp is an independent, community-made project. It is not affiliated with, endorsed by, "
                  "or sponsored by GitHub, Inc., Microsoft, or the Spec Kit project.")
# Images allowed in the README: shields.io badges and this repo's own CI badge. Nothing else (no vendor logos).
BADGE = re.compile(r"^https://(img\.shields\.io/|github\.com/praveenlavu/spec-kit-ghp/actions/workflows/ci\.yml/badge\.svg)")
FORBIDDEN = [re.compile(p, re.I) for p in (r"\bspike", r"docs/plans")]  # the private names are the owner's denylist: below
TASK_ID = re.compile(r"\b(T\d{2}[a-z]?|AC\d+|[DGX]\d+)\b")  # internal plan ids, case-sensitive (Spec Kit task ids such as T001 are fine)
FENCE = re.compile(r"^```([\w-]*)\n(.*?)^```", re.M | re.S)
VERB = re.compile(r"(?<![\w/.-])ghp\s+([a-z][\w-]*)")


def text(p):
    return p.read_text(encoding="utf-8")


def coming_verbs():
    """`ghp <verb>` names in the README status table rows marked Coming."""
    rows = [ln for ln in text(README).splitlines() if ln.startswith("|") and "Coming" in ln]
    return {v for ln in rows for v in VERB.findall(ln)}


def test_readme_has_the_not_affiliated_line():
    assert NOT_AFFILIATED in text(README)


def test_readme_has_no_vendor_images():
    t = text(README)
    assert "<img" not in t.lower()
    for url in re.findall(r"!\[[^\]]*\]\(([^)\s]+)", t):
        assert BADGE.match(url) and "logo=" not in url, url


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_no_internal_names_or_ids(doc):
    t = text(doc)
    for pat in FORBIDDEN:
        assert not pat.search(t), pat.pattern
    assert not TASK_ID.findall(t)


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_no_denylisted_names(doc):
    """The owner's private names (client account, client, home user) come from the gitignored denylist, never from this file."""
    names = forbidden_accounts(os.environ)
    if not names:
        pytest.skip("no .scrub-denylist or GHP_FORBIDDEN_ACCOUNTS supplied: the owner's name check cannot run")
    t = text(doc).lower()
    hits = [i for i, n in enumerate(sorted(names), 1) if n in t]
    assert not hits, "denylist entries #%s found (names not printed)" % hits


def test_mermaid_blocks_are_well_formed():
    blocks = [b for doc in DOCS for lang, b in FENCE.findall(text(doc)) if lang == "mermaid"]
    assert blocks, "README should carry the How it works flowchart"
    for b in blocks:
        assert re.match(r"(flowchart|graph) (TD|TB|LR|RL|BT)\b", b.strip()), b[:40]
        assert "<" not in b, "no HTML in Mermaid labels"
        assert b.count('"') % 2 == 0
        stack = []
        for ch in b:
            if ch in "([{":
                stack.append(ch)
            elif ch in ")]}":
                assert stack and "([{"[")]}".index(ch)] == stack.pop(), b
        assert not stack, b


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_relative_links_resolve(doc):
    for target in re.findall(r"\]\(([^)\s]+)\)", text(doc)):
        if re.match(r"(https?:|mailto:|#)", target):
            continue
        assert (doc.parent / target.split("#")[0]).exists(), target


def test_every_ghp_verb_in_code_is_real_or_marked_coming():
    allowed = set(REGISTRY) | coming_verbs()
    for doc in DOCS[:2]:
        for lang, body in FENCE.findall(text(doc)):
            if lang == "mermaid":
                continue
            for verb in VERB.findall(body):
                assert verb in allowed, "%s: ghp %s is neither a verb nor in the Coming table" % (doc.name, verb)


def test_coming_table_names_only_verbs_that_do_not_exist_yet():
    coming = coming_verbs()
    # the 0.1.0 release shipped the last row that was coming; a row may be marked Coming again, but none must be
    assert "Coming in 0.1" not in text(README)
    assert not coming & set(REGISTRY), "a verb that works today is still marked Coming"
    rows = [ln for ln in text(README).splitlines() if ln.startswith("|") and "Coming" in ln]
    shipped = [f for ln in rows for f in re.findall(r"`([\w./-]+\.md)`", ln) if (ROOT / f).is_file()]
    assert not shipped, "a file that exists is still marked Coming: %s" % shipped


# ---- README install and help: the release pin, the bundle route, the likely failures, the comparison, the done --pr rule ----
QUICKSTART = ROOT / "docs" / "quickstart.md"
PIN = "git+https://github.com/praveenlavu/spec-kit-ghp.git@v0.1.1"


def flat(p):
    return " ".join(text(p).split())


def section(p, heading):
    m = re.search(r"^## %s\n(.*?)(?=^#{1,2} |\Z)" % re.escape(heading), text(p), re.M | re.S)
    assert m, heading
    return " ".join(m.group(1).split())


@pytest.mark.parametrize("doc", [README, QUICKSTART], ids=lambda p: p.name)
def test_install_is_pinned_to_the_release_tag(doc):
    t = text(doc)
    assert "pipx install %s\n" % PIN in t and "python3 -m pip install --user %s`" % PIN in t
    # every install address of this repository names the tag
    assert all(a.endswith("@v0.1.1") for a in re.findall(r"git\+https://github\.com/praveenlavu/spec-kit-ghp(?:\.git)?\S*?(?=[`\s])", t))
    assert "the `v0.1.1` tag is created when 0.1.1 is released" in flat(doc).replace("The `v0.1.1`", "the `v0.1.1`")


def test_readme_gives_the_bundle_route_as_the_bundle_page_does():
    t, bundle_page = text(README), text(ROOT / "bundle" / "README.md")
    for line in ("specify extension catalog add https://raw.githubusercontent.com/praveenlavu/spec-kit-ghp/main/catalog/extensions.json --name ghp --install-allowed",
                 "specify workflow catalog add https://raw.githubusercontent.com/praveenlavu/spec-kit-ghp/main/catalog/workflows.json --name ghp",
                 "curl -L -o ghp-0.1.1.zip https://github.com/praveenlavu/spec-kit-ghp/releases/download/v0.1.1/ghp-0.1.1.zip",
                 "specify bundle install ./ghp-0.1.1.zip"):
        assert line + "\n" in t and line in bundle_page and line in text(ROOT / "docs" / "catalog-submission.md"), line
    assert "(https://github.com/praveenlavu/spec-kit-ghp/blob/main/bundle/README.md)" in t  # bundle/ is not installed: a full address
    assert "The bundle file is attached to the 0.1.1 release." in flat(README) and "does not exist yet" not in flat(README)


def test_the_likely_failures_are_in_the_readme_with_their_real_exit_codes():
    s = section(README, "If something goes wrong")
    for row in ('| `E_GH_SCOPE_MISSING` (exit code 1) |', "`gh auth refresh --scopes project`",
                '| "No ghp config found" (exit code 2) |', "`E_ORG_PROJECT_UNSUPPORTED`", "The board is not found",
                '| "plan changed since approval" (exit code 2) |', "with `.new` added",
                "You merged, but the story is still open", "| `E_PR_NOT_LINKED` from `ghp done 12 --pr 44` (exit code 2) |",
                "must carry a closing keyword for the story, such as `Closes #12`. A mention is not enough."):
        assert row in s, row
    src = ROOT / "src" / "ghp"  # each code and message is the CLI's own
    assert '"E_GH_SCOPE_MISSING"' in text(src / "gh.py") and "No ghp config found" in text(src / "config.py")
    assert "plan changed since approval" in text(src / "plan.py") and '"E_PR_NOT_LINKED"' in text(src / "done.py")


def test_readme_says_how_it_differs_from_doing_it_by_hand():
    t = flat(README)
    assert "**How this differs from doing it by hand.** You can do every one of these steps yourself in GitHub's web pages or with `gh`" in t
    assert "`ghp` makes the same calls through `gh`." in t and "It has no web interface of its own." in t


def test_quickstart_says_done_pr_needs_a_closing_keyword():
    t = flat(QUICKSTART)
    assert "carry a closing keyword for the story, such as `Closes #12`, in its title or body. A mention is not enough" in t
    assert "`E_PR_NOT_LINKED` (exit code 2)" in t and "and mention the issue." not in t


def test_readme_and_quickstart_explain_both_slash_spellings():
    """Live on Spec Kit 1.4.0 with Claude Code the commands are skills `speckit-ghp-*`; the dotted form is an unknown skill there."""
    for p in (README, QUICKSTART):
        t = flat(p)
        assert "ith Spec Kit 1.4.0 and Claude Code they are installed as skills named with hyphens" in t, p
        assert "`/speckit-ghp-pull 12`" in t and "Unknown skill" in t and "`specify extension list` shows that the extension is installed" in t, p
