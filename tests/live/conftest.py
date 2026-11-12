"""Live guard for tests that talk to GitHub. Nothing here calls GitHub at import time.

Live tests run only with GHP_LIVE=1; otherwise every test in this directory is skipped. A live run needs
GHP_LIVE_ACCOUNT, which must be the owner's account `praveenlavu` (case-insensitive); anything else, and the forbidden accounts
always, is refused before any call. The forbidden accounts are never written in this repository: they come from the owner's
gitignored `.scrub-denylist` (one name per line) and/or GHP_FORBIDDEN_ACCOUNTS (comma- or space-separated). With neither, every
live test is refused (fail closed). Then the viewer read (`Gh.viewer()`, GraphQL `viewer`, through the real `ghp.gh` transport) must return that same login.
That viewer read is itself one GitHub read, made with whatever token `gh` has active, before the refusal can happen: set
GH_TOKEN per command (below) so that token is the owner's. The autouse fixture makes every test in this directory depend on
the guard, so no live test reaches GitHub without it. `gh` does its own authentication: nothing here reads a token.
Run: GHP_LIVE=1 GHP_LIVE_ACCOUNT=praveenlavu GH_TOKEN=$(gh auth token -u praveenlavu) python3 -m pytest tests/live -q
"""
import os
import re
from pathlib import Path

import pytest

from ghp.gh import Gh

DENYLIST = Path(__file__).resolve().parents[2] / ".scrub-denylist"  # owner-supplied, gitignored
ALLOWED = "praveenlavu"  # the only account a live run may declare (the targets below are this account's scratch repo)
STORY_TITLE = "Live story: opening hours (fictional)"  # T13's fixtures, found by label AND this exact title
SUB_TITLE = "Live sub-issue: render the hours (fictional)"
OWNER, REPO, PROJECT = "praveenlavu", "ghp-spike-scratch", 3  # the scratch repository and its user Project (fictional data only)
REPO_PATH = "repos/%s/%s" % (OWNER, REPO)


class LiveRefused(Exception):
    pass


def forbidden_accounts(env, path=DENYLIST):
    """Lower-cased names from GHP_FORBIDDEN_ACCOUNTS and the denylist file (blank lines and `#` comments skipped)."""
    names = set(re.split(r"[\s,]+", (env.get("GHP_FORBIDDEN_ACCOUNTS") or "").lower()))
    if path.is_file():
        names |= {ln.strip().lower() for ln in path.read_text(encoding="utf-8").splitlines() if not ln.strip().startswith("#")}
    return names - {""}


def require_live_account(env, gh, denylist=DENYLIST):
    """Raise LiveRefused unless this environment may talk to GitHub; return the verified login. Checks the environment first
    (no call is made without a forbidden-account list, or for a forbidden or missing account), then the viewer read."""
    forbidden = forbidden_accounts(env, denylist)
    if not forbidden:
        raise LiveRefused("no forbidden-account list: supply the owner's .scrub-denylist or GHP_FORBIDDEN_ACCOUNTS (fail closed)")
    want = (env.get("GHP_LIVE_ACCOUNT") or "").strip()
    if not want:
        raise LiveRefused("GHP_LIVE_ACCOUNT is not set; a live run names its account")
    if want.lower() in forbidden:
        raise LiveRefused("GHP_LIVE_ACCOUNT names a forbidden account; refused always")
    if want.lower() != ALLOWED:
        raise LiveRefused("GHP_LIVE_ACCOUNT must be %r (the scratch repo is that account's), not %r" % (ALLOWED, want))
    login = gh.viewer().login
    if login.lower() in forbidden:
        raise LiveRefused("the viewer read returned a forbidden account; refused always")
    if login.lower() != want.lower():
        raise LiveRefused("the viewer read returned %r, not the declared account %r" % (login, want))
    return login


def _skip_unless_live():
    if os.environ.get("GHP_LIVE") != "1":
        pytest.skip("live test: set GHP_LIVE=1 (and GHP_LIVE_ACCOUNT) to run")


@pytest.fixture(autouse=True)
def _live_only(request):
    _skip_unless_live()
    request.getfixturevalue("gh")  # every live test passes the guard first, whether or not it asks for `gh`


@pytest.fixture(scope="session")
def gh():
    """A real transport, after the guard. A refusal fails the test instead of skipping it."""
    _skip_unless_live()  # a session fixture is set up before the autouse one above
    real = Gh()
    try:
        require_live_account(os.environ, real)
    except LiveRefused as e:
        pytest.fail("live guard refused: %s" % e, pytrace=False)
    return real


@pytest.fixture(scope="session")
def scratch(gh):
    """Numbers of the fictional objects created by hand for T13 (docs/evidence/T13.md): the issue with the label ghp:live-test
    and exactly the title STORY_TITLE (one sub-issue titled SUB_TITLE, one comment), and any pull request in the repo."""
    found = gh.rest("GET", REPO_PATH + "/issues?labels=ghp:live-test&state=all&per_page=100")
    stories = [i["number"] for i in found if i["title"] == STORY_TITLE and "pull_request" not in i]
    assert len(stories) == 1, "expected exactly one issue labelled ghp:live-test titled %r, found %s" % (STORY_TITLE, stories)
    story = stories[0]
    subs = [i["number"] for i in gh.rest("GET", "%s/issues/%d/sub_issues" % (REPO_PATH, story)) if i["title"] == SUB_TITLE]
    prs = gh.rest("GET", REPO_PATH + "/pulls?state=all&per_page=1")
    assert len(subs) == 1 and prs, "the live story needs its sub-issue %r and the repo needs one pull request" % SUB_TITLE
    return {"story": story, "sub": subs[0], "pr": prs[0]["number"]}


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """A scratch working directory with a ghp.config.json for the live repo; files the verbs write land here."""
    (tmp_path / "ghp.config.json").write_text(
        '{"owner": "%s", "repo": "%s", "project_owner": "%s", "project_number": %d}' % (OWNER, REPO, OWNER, PROJECT))
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path
