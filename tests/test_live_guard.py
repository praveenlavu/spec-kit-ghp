"""T13-AC1, T44-AC2: the live guard (offline, with a fake gh): it refuses every account but the declared one, the forbidden ones
always, and every live run when no forbidden-account list is supplied. Only fictional names here: the real list is the owner's
gitignored `.scrub-denylist` (or GHP_FORBIDDEN_ACCOUNTS)."""
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import FakeGh
from ghp.gh import Viewer
from live import conftest as live

CLIENT = "fictional-client"  # stands in for the owner's forbidden account
FORBID = {"GHP_FORBIDDEN_ACCOUNTS": "other-fictional, " + CLIENT}


class CountingViewer(FakeGh):
    def __init__(self, login):
        super().__init__(viewer=Viewer(1, login, ["repo"]))
        self.asked = 0

    def viewer(self):
        self.asked += 1
        return super().viewer()


@pytest.fixture
def no_file(tmp_path):
    return tmp_path / "missing-denylist"


def test_live_guard_refuses_other_accounts(no_file):
    ok = CountingViewer("praveenlavu")
    assert live.require_live_account({**FORBID, "GHP_LIVE_ACCOUNT": "praveenlavu"}, ok, no_file) == "praveenlavu"
    assert live.require_live_account({**FORBID, "GHP_LIVE_ACCOUNT": "PraveenLavu"}, CountingViewer("praveenlavu"), no_file) == "praveenlavu"
    cases = [
        ({}, "praveenlavu", False),  # no declared account: no call
        ({"GHP_LIVE_ACCOUNT": "  "}, "praveenlavu", False),
        ({"GHP_LIVE_ACCOUNT": "praveenlavu"}, "someone-else", True),  # token belongs to another account
        ({"GHP_LIVE_ACCOUNT": "someone-else"}, "someone-else", False),  # pinned to the owner's account: refused before any call
        ({"GHP_LIVE_ACCOUNT": "someone-else"}, "praveenlavu", False),
        ({"GHP_LIVE_ACCOUNT": CLIENT}, CLIENT, False),  # declared as the client's: refused before any call
        ({"GHP_LIVE_ACCOUNT": CLIENT.upper()}, CLIENT, False),
        ({"GHP_LIVE_ACCOUNT": "praveenlavu"}, CLIENT, True),  # the token is the client's
        ({"GHP_LIVE_ACCOUNT": "praveenlavu"}, CLIENT.upper(), True),
    ]
    for env, login, asks in cases:
        gh = CountingViewer(login)
        with pytest.raises(live.LiveRefused):
            live.require_live_account({**FORBID, **env}, gh, no_file)
        assert gh.asked == int(asks), (env, login)


def test_a_forbidden_account_is_refused_by_name_even_when_it_is_the_allowed_one(no_file):
    """The forbidden list wins: a list naming the owner's account refuses it before any call; a forbidden viewer is named so."""
    gh = CountingViewer("praveenlavu")
    with pytest.raises(live.LiveRefused, match="forbidden"):
        live.require_live_account({"GHP_FORBIDDEN_ACCOUNTS": "PraveenLavu", "GHP_LIVE_ACCOUNT": "praveenlavu"}, gh, no_file)
    assert gh.asked == 0
    with pytest.raises(live.LiveRefused, match="forbidden"):
        live.require_live_account({**FORBID, "GHP_LIVE_ACCOUNT": "praveenlavu"}, CountingViewer(CLIENT.upper()), no_file)


def test_live_guard_fails_closed_without_a_forbidden_list(no_file):
    """No GHP_FORBIDDEN_ACCOUNTS and no .scrub-denylist: every live run is refused before any call, the owner's account too."""
    for env in ({"GHP_LIVE_ACCOUNT": "praveenlavu"}, {"GHP_LIVE_ACCOUNT": "praveenlavu", "GHP_FORBIDDEN_ACCOUNTS": " , "}):
        gh = CountingViewer("praveenlavu")
        with pytest.raises(live.LiveRefused, match=r"\.scrub-denylist"):
            live.require_live_account(env, gh, no_file)
        assert gh.asked == 0


def test_forbidden_accounts_come_from_the_file_and_the_environment(tmp_path, no_file):
    f = tmp_path / "denylist"
    f.write_text("# the owner's list\n\n  Fictional-Client  \nsecond-name\n")
    assert live.forbidden_accounts({}, f) == {"fictional-client", "second-name"}
    assert live.forbidden_accounts({"GHP_FORBIDDEN_ACCOUNTS": "A1,b2  C3\nd4"}, no_file) == {"a1", "b2", "c3", "d4"}
    assert live.forbidden_accounts({"GHP_FORBIDDEN_ACCOUNTS": "x"}, f) == {"x", "fictional-client", "second-name"}
    assert live.forbidden_accounts({}, no_file) == set()


def test_importing_the_live_conftest_makes_no_call(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a subprocess was started at import time")
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    spec = importlib.util.spec_from_file_location("live_conftest_copy", Path(live.__file__))
    spec.loader.exec_module(importlib.util.module_from_spec(spec))  # a fresh copy, so the shared module is untouched


FAKE_GH = """#!%s
import json, os, sys
with open(os.environ["FAKE_GH_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
print("HTTP/2.0 200 OK\\r\\nX-Oauth-Scopes: repo\\r\\n\\r\\n"
      + json.dumps({"data": {"viewer": {"databaseId": 7, "login": os.environ["FAKE_GH_LOGIN"], "__typename": "User"}}}))
"""


def run_live_suite(tmp_path, account, login, forbidden=CLIENT):
    """The real live directory under GHP_LIVE=1 with a fake `gh` first on PATH. Returns (pytest summary line, gh argv list)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "gh"
    fake.write_text(FAKE_GH % sys.executable)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "gh.log"
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"], "GHP_LIVE": "1", "FAKE_GH_LOG": str(log), "FAKE_GH_LOGIN": login}
    for k in ("GH_TOKEN", "GITHUB_TOKEN", "GHP_CONFIG", "GHP_FORBIDDEN_ACCOUNTS"):
        env.pop(k, None)
    if forbidden is not None:
        env["GHP_FORBIDDEN_ACCOUNTS"] = forbidden
    if account is None:
        env.pop("GHP_LIVE_ACCOUNT", None)
    else:
        env["GHP_LIVE_ACCOUNT"] = account
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    done = subprocess.run([sys.executable, "-m", "pytest", "tests/live", "-q", "-p", "no:cacheprovider"], cwd=root, env=env,
                          capture_output=True, text=True, timeout=120)
    calls = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    return done.stdout.strip().splitlines()[-1], calls


@pytest.mark.parametrize("account, login, user_checks", [
    (CLIENT, CLIENT, 0),  # declared as the client account: refused before any call
    (None, "praveenlavu", 0),  # no declared account
    ("praveenlavu", CLIENT, 1),  # the active token is the client's: the /user check is the one call, then refused
    ("praveenlavu", "someone-else", 1),
])
def test_no_live_test_reaches_github_when_the_guard_refuses(tmp_path, account, login, user_checks):
    summary, calls = run_live_suite(tmp_path, account, login)
    assert "passed" not in summary and "error" in summary, summary  # every live test (the unguarded-looking ones too) errors on the guard
    assert len(calls) == user_checks and all(c[:4] == ["api", "-X", "POST", "graphql"] for c in calls), calls  # the viewer read


def test_no_live_test_reaches_github_without_a_forbidden_list(tmp_path):
    """T44-AC2: fail closed. The owner's account and a matching token, but no list: every live test errors and gh is never called."""
    if live.DENYLIST.is_file():
        pytest.skip("the owner's .scrub-denylist is present in this checkout: the no-list case cannot be run here")
    summary, calls = run_live_suite(tmp_path, "praveenlavu", "praveenlavu", forbidden=None)
    assert "passed" not in summary and "error" in summary, summary
    assert calls == [], calls
