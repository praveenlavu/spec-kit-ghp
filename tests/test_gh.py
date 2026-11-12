import json
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import FakeClock, FakeGh, FakeRunner, bot_runner, http, viewer_reply
from ghp import gh as ghmod
from ghp.gh import (Gh, GhError, GhMissing, GhTimeout, RateLimited, ScopeMissing, Viewer, WriteNotArmed,
                    is_graphql_write, parse_include, subprocess_runner)

SRC = Path(__file__).resolve().parent.parent / "src" / "ghp"
CANARY = "gh" + "p_" + "A1b2C3d4E5" * 4  # runtime-built: a token-shaped string that appears in no file
VERSION_HEADER = "X-GitHub-Api-Version: 2022-11-28"
RATE_OK = {"x-ratelimit-remaining": "4000"}


def make(*results, armed=False):
    clock, runner = FakeClock(), FakeRunner(*results)
    g = Gh(runner=runner, sleep=clock.sleep, clock=clock, wall=clock)
    if armed:
        g.arm()
    return g, runner, clock


def gql_ok(data=None):
    return http(200, {"data": data or {}})


def gql_err(*errors, headers=None):
    return http(200, {"data": None, "errors": list(errors)}, headers)


# --- AC1, AC2, AC3 ---------------------------------------------------------------------------------------------

def test_request_body_goes_through_stdin_never_argv():
    g, runner, _ = make(http(201, {"id": 7}), armed=True)
    g.rest("POST", "repos/o/r/issues", {"title": "SECRET-TITLE", "body": "SECRET-BODY"})
    call = runner.calls[0]
    assert call["argv"] == ["gh", "api", "-X", "POST", "repos/o/r/issues", "-H", VERSION_HEADER, "-i", "--input", "-"]
    assert json.loads(call["stdin"]) == {"title": "SECRET-TITLE", "body": "SECRET-BODY"}
    assert not any("SECRET" in a for a in call["argv"])


def test_graphql_query_and_variables_go_through_stdin():
    g, runner, _ = make(gql_ok({"x": 1}))
    assert g.graphql("query($n:Int!){ a(n:$n) }", {"n": 3}) == {"x": 1}
    call = runner.calls[0]
    assert call["argv"] == ["gh", "api", "-X", "POST", "graphql", "-H", VERSION_HEADER, "-i", "--input", "-"]
    assert json.loads(call["stdin"]) == {"query": "query($n:Int!){ a(n:$n) }", "variables": {"n": 3}}


def test_a_call_without_a_body_has_no_stdin_and_no_input_flag():
    g, runner, _ = make(http(200, [1]))
    assert g.rest("GET", "user/repos") == [1]
    assert runner.calls[0]["stdin"] is None
    assert "--input" not in runner.calls[0]["argv"]


def test_api_version_header_is_pinned():
    g, runner, _ = make(http(200, {}), gql_ok(), http(200, {}), armed=True)
    g.rest("GET", "a")
    g.graphql("{ viewer { id } }")
    g.rest("PATCH", "b", {"x": 1})
    for call in runner.calls:
        argv = call["argv"]
        assert argv[argv.index("-H") + 1] == VERSION_HEADER


def test_parse_include_output():
    raw = ('HTTP/2.0 200 OK\r\nContent-Type: application/json\r\nX-RateLimit-Remaining: 12\r\n'
           'Retry-After: 5\r\n\r\n{"a": [1, 2]}')
    status, headers, body = parse_include(raw)
    assert status == 200
    assert headers == {"content-type": "application/json", "x-ratelimit-remaining": "12", "retry-after": "5"}
    assert body == {"a": [1, 2]}
    assert parse_include("HTTP/2.0 204 No Content\r\nx: y\r\n\r\n") == (204, {"x": "y"}, None)
    assert parse_include("HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 201 Created\r\nk: v\r\n\r\n{}")[0:2] == (201, {"k": "v"})
    assert parse_include("not http at all")[0] is None


def test_an_error_status_raises_with_status_and_message():
    g, _, _ = make(http(404, {"message": "Not Found"}))
    with pytest.raises(GhError) as e:
        g.rest("GET", "repos/o/missing")
    assert e.value.status == 404 and "Not Found" in str(e.value)


def test_no_http_response_raises_with_scrubbed_stderr():
    g, _, _ = make((1, "", f"error connecting to api.github.com {CANARY}"))
    with pytest.raises(GhError) as e:
        g.rest("GET", "user")
    assert e.value.status is None and "api.github.com" in str(e.value)


# --- AC4 -------------------------------------------------------------------------------------------------------

def test_graphql_errors_in_http_200_raise():
    g, _, _ = make(gql_err({"type": "NOT_FOUND", "message": "Could not resolve to a Repository"}))
    with pytest.raises(GhError) as e:
        g.graphql("{ repository(owner:\"o\", name:\"r\") { id } }")
    assert "Could not resolve" in str(e.value) and e.value.status == 200


def test_graphql_error_keeps_each_type_and_path():
    """T26g-AC1: the caller can tell a NOT_FOUND at one path from any other error; odd shapes become None, never a crash."""
    g, _, _ = make(gql_err({"type": "NOT_FOUND", "path": ["repository", "issueOrPullRequest"], "message": "Could not resolve"},
                           {"type": 7, "path": "x", "message": "odd"}, {"message": "bare"}))
    with pytest.raises(GhError) as e:
        g.graphql("{ repository(owner:\"o\", name:\"r\") { id } }")
    assert e.value.code == "E_GH_GRAPHQL"
    assert e.value.graphql_errors == [("NOT_FOUND", ("repository", "issueOrPullRequest")), (None, None), (None, None)]
    assert GhError("E_GH_HTTP", 404).graphql_errors == []


def test_insufficient_scopes_names_the_missing_scope():
    msg = ("The 'createProjectV2' field requires one of the following scopes: ['project'], but your token has only "
           "been granted the: ['gist', 'read:org', 'repo', 'workflow'] scopes. Please modify your token's scopes.")
    g, _, _ = make(gql_err({"type": "INSUFFICIENT_SCOPES", "message": msg}), armed=True)
    with pytest.raises(ScopeMissing) as e:
        g.graphql("mutation { createProjectV2(input:{}) { clientMutationId } }")
    assert e.value.scopes == ["project"]
    assert "project" in str(e.value) and "gh auth refresh" in str(e.value)
    assert isinstance(e.value, GhError)


def test_insufficient_scopes_lists_every_scope_named():
    msg = "requires one of the following scopes: ['read:project', 'project'], but your token has only"
    g, _, _ = make(gql_err({"type": "INSUFFICIENT_SCOPES", "message": msg}))
    with pytest.raises(ScopeMissing) as e:
        g.graphql("{ viewer { id } }")
    assert e.value.scopes == ["read:project", "project"]


# --- AC5 -------------------------------------------------------------------------------------------------------

def test_read_retries_after_retry_after_header():
    g, runner, clock = make(http(403, {"message": "secondary rate limit"}, {"Retry-After": "7"}), http(200, {"ok": 1}))
    assert g.rest("GET", "x") == {"ok": 1}
    assert clock.sleeps == [7] and len(runner.calls) == 2


def test_read_waits_for_reset_when_remaining_is_zero():
    g, runner, clock = make(None, http(200, {"ok": 1}))
    runner.results[0] = http(403, {"message": "API rate limit exceeded"},
                             {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(clock.now) + 30)})
    assert g.rest("GET", "x") == {"ok": 1}
    assert clock.sleeps == [30]


def test_a_wait_over_the_cap_raises_without_sleeping():
    g, runner, clock = make(http(403, {}, {"Retry-After": "121"}))
    with pytest.raises(RateLimited) as e:
        g.rest("GET", "x")
    assert e.value.wait == 121 and clock.sleeps == [] and len(runner.calls) == 1
    g, runner, clock = make(None)
    runner.results[0] = http(429, {}, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(clock.now) + 3600)})
    with pytest.raises(RateLimited) as e:
        g.rest("GET", "x")
    assert e.value.wait == 3600 and clock.sleeps == []


def test_a_wait_of_exactly_the_cap_is_taken():
    g, _, clock = make(http(429, {}, {"Retry-After": "120"}), http(200, {}))
    g.rest("GET", "x")
    assert clock.sleeps == [120]


def test_read_gives_up_after_three_attempts():
    limited = http(403, {"message": "rate limit"}, {"Retry-After": "2"})
    g, runner, clock = make(limited, limited, limited, http(200, {}))
    with pytest.raises(RateLimited):
        g.rest("GET", "x")
    assert len(runner.calls) == 3 and clock.sleeps == [2, 2]


def test_a_403_without_rate_limit_signals_is_not_retried():
    g, runner, _ = make(http(403, {"message": "Upgrade to GitHub Pro"}, RATE_OK), http(200, {}))
    with pytest.raises(GhError) as e:
        g.rest("GET", "x")
    assert not isinstance(e.value, RateLimited) and e.value.status == 403 and len(runner.calls) == 1


def test_graphql_rate_limit_in_a_200_body_is_detected():
    err = {"type": "RATE_LIMITED", "message": "API rate limit exceeded for user ID 1."}
    g, runner, clock = make(None, gql_ok({"viewer": 1}))
    runner.results[0] = gql_err(err, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(clock.now) + 10)})
    assert g.graphql("{ viewer { id } }") == {"viewer": 1}
    assert clock.sleeps == [10]


def test_graphql_rate_limit_on_a_mutation_raises_and_is_not_retried():
    err = {"type": "RATE_LIMITED", "message": "API rate limit exceeded"}
    g, runner, clock = make(gql_err(err, headers={"Retry-After": "3"}), gql_ok(), armed=True)
    with pytest.raises(RateLimited) as e:
        g.graphql("mutation { x }")
    assert e.value.wait == 3 and len(runner.calls) == 1 and clock.sleeps == []


def test_read_retries_a_5xx_and_a_timeout_then_succeeds():
    g, runner, clock = make(http(502, {"message": "bad gateway"}), subprocess.TimeoutExpired("gh", 1), http(200, {"ok": 1}))
    assert g.rest("GET", "x") == {"ok": 1}
    assert len(runner.calls) == 3 and len(clock.sleeps) == 2


def test_a_read_that_keeps_timing_out_raises_ghtimeout():
    t = subprocess.TimeoutExpired("gh", 1)
    g, runner, _ = make(t, t, t)
    with pytest.raises(GhTimeout) as e:
        g.rest("GET", "x")
    assert len(runner.calls) == 3 and "unknown whether applied" not in str(e.value)


# --- AC6 -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("result", [
    http(500, {"message": "boom"}),
    http(502, {"message": "bad gateway"}),
    http(429, {"message": "slow down"}, {"Retry-After": "1"}),
    http(403, {"message": "rate limit"}, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1"}),
    subprocess.TimeoutExpired("gh", 1),
])
def test_write_is_never_retried(result):
    g, runner, clock = make(result, http(200, {}), armed=True)
    with pytest.raises(GhError):
        g.rest("POST", "repos/o/r/issues", {"title": "t"})
    assert len(runner.calls) == 1 and clock.sleeps == []


@pytest.mark.parametrize("query", [
    "mutation { a }",
    "mutation M($a: ID!) { a(id: $a) { id } }",
    "  \n\t mutation { a }",
    "﻿mutation { a }",
])
def test_graphql_mutation_is_a_write(query):
    assert is_graphql_write(query)
    g, runner, _ = make(gql_ok())
    with pytest.raises(WriteNotArmed):
        g.graphql(query)
    assert runner.calls == []


def test_leading_comment_does_not_hide_a_mutation():
    q = "# fetch nothing\n  # query { viewer { id } }\n\nmutation Do { a }"
    assert is_graphql_write(q)
    assert is_graphql_write("#c\r\n#d\rmutation { a }")


@pytest.mark.parametrize("query", [
    '{ a(s: """x\\"""y""") }',                          # a valid escaped delimiter inside a read
    '{ a(s: "x#y", t: "q\\"r") } # it\'s "quoted',     # # in a string, escaped quote, quote in a comment
    "query { viewer { id } }",
    "{ viewer { id } }",
    "query Q { viewer { login } }  # mutation in a trailing comment",
    '{ search(query: "mutation { x }") { issueCount } }',
    "query Q { mutationlike }",
])
def test_queries_are_reads(query):
    assert not is_graphql_write(query)


@pytest.mark.parametrize("query", [
    "fragment F on Issue { id }\nmutation { a }",       # named risk: a fragment before the operation
    "query Q { a }\nmutation M { b }",                    # named risk: several operations in one document
    "MUTATION { a }",                                     # not a keyword in GraphQL, but fail closed
    '{ a(s: "unterminated) }',                            # unparsable: fail closed
    # a block string holds \""" (an escaped delimiter): the scanner must not close it early and lose step
    'fragment F on Issue { labels(first: 1, after: """x\\"""y""") { totalCount } }\n'
    'mutation { addComment(input: {subjectId: "S", body: """p\\"""q"""}) { subject { ...F } } }',
    '{ a(s: """x\\\\""" ) }\nmutation { b }',              # \\""" : the second backslash escapes the delimiter, the block never closes
    '{ a(s: """never closed) }\nmutation { b }',          # unterminated block string
    '{ a }\n}\nmutation { b }',                           # unbalanced close: ambiguous
    '{ a { b }\nmutation { c }',                           # unbalanced open: ambiguous
    '{ a(s: "x#y") }\n# it\'s "quoted\nmutation { b }',    # a string with # and a comment with quotes do not hide it
])
def test_ambiguous_documents_are_treated_as_writes(query):
    assert is_graphql_write(query)


# --- AC7 -------------------------------------------------------------------------------------------------------

def test_writes_are_paced_one_second_apart():
    g, runner, clock = make(http(200, {}), http(200, {}), http(200, {}), http(200, {}), armed=True)
    t0 = clock.now
    g.rest("POST", "a", {})
    assert clock.sleeps == []                       # the first write is not delayed
    clock.now += 0.4
    g.rest("PATCH", "b", {})
    assert clock.sleeps == [pytest.approx(0.6)]     # 1 s since the previous write, no more
    g.rest("DELETE", "c")
    assert clock.sleeps[1:] == [pytest.approx(1.0)]
    clock.now += 5
    g.rest("PUT", "d")
    assert len(clock.sleeps) == 2                   # already more than 1 s apart: no sleep
    assert clock.now - t0 >= 3


def test_reads_are_not_paced():
    g, _, clock = make(http(200, {}), http(200, {}), http(200, {}), http(200, {}), armed=True)
    g.rest("GET", "a")
    g.rest("GET", "b")
    g.graphql("{ viewer { id } }")
    g.rest("GET", "c")
    assert clock.sleeps == []


# --- AC8 -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE", "post"])
def test_write_before_arm_raises_and_spawns_nothing(method):
    g, runner, clock = make(http(200, {}))
    with pytest.raises(WriteNotArmed):
        g.rest(method, "repos/o/r/issues", {"title": "t"})
    assert runner.calls == [] and clock.sleeps == []


def test_reads_work_before_arm_and_arm_unlocks_writes():
    g, runner, _ = make(http(200, {"a": 1}), http(201, {"b": 2}))
    assert g.rest("GET", "x") == {"a": 1}
    g.arm()
    assert g.rest("POST", "y", {}) == {"b": 2}
    assert len(runner.calls) == 2


# --- AC9 -------------------------------------------------------------------------------------------------------

def test_write_timeout_says_the_outcome_is_unknown():
    g, _, _ = make(subprocess.TimeoutExpired("gh", 60), armed=True)
    with pytest.raises(GhTimeout) as e:
        g.rest("POST", "repos/o/r/issues", {"title": "t"})
    assert "unknown whether applied; re-read before retrying" in str(e.value)


def test_missing_gh_binary_has_an_install_hint():
    g, _, _ = make(FileNotFoundError(2, "No such file", "gh"))
    with pytest.raises(GhMissing) as e:
        g.rest("GET", "user")
    assert "https://cli.github.com" in str(e.value) and "gh auth login" in str(e.value)


def test_missing_gh_binary_through_the_real_runner(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # an empty directory: no `gh`
    with pytest.raises(GhMissing):
        Gh().rest("GET", "user")


# --- AC10 ------------------------------------------------------------------------------------------------------

def test_no_source_reads_a_token_variable():
    banned = ["GH_" + "TOKEN", "GITHUB_" + "TOKEN", "gh auth " + "token"]
    files = sorted(SRC.rglob("*.py"))
    assert files
    for path in files:
        text = path.read_text()
        for word in banned:
            assert word not in text, f"{path.name} mentions {word}"


def test_error_messages_scrub_token_shaped_strings():
    g, _, _ = make(
        (1, "", f"gh: something failed, token {CANARY} was rejected"),
        http(404, {"message": f"oops {CANARY}"}),
        gql_err({"type": "X", "message": f"bad {CANARY}"}),
    )
    for call in (lambda: g.rest("GET", "a"), lambda: g.rest("GET", "b"), lambda: g.graphql("{ a }")):
        with pytest.raises(GhError) as e:
            call()
        assert CANARY not in str(e.value) and "***" in str(e.value)


def test_scrub_covers_the_token_families():
    for tok in ("gho_" + "x" * 30, "ghs_" + "y" * 30, "github_pat_" + "Z" * 30):
        assert ghmod.scrub(f"a {tok} b") == "a *** b"
    assert ghmod.scrub("Authorization: Bearer abc.def") == "Authorization: ***"
    assert ghmod.scrub("plain text") == "plain text"


def test_viewer_scopes_none_when_header_absent():
    g, runner, _ = make(viewer_reply(22502189, "octo"))
    assert g.viewer() == Viewer(id=22502189, login="octo", scopes=None, typename="User")
    call = runner.calls[0]
    assert call["argv"] == ["gh", "api", "-X", "POST", "graphql", "-H", VERSION_HEADER, "-i", "--input", "-"]
    assert json.loads(call["stdin"]) == {"query": ghmod.VIEWER_QUERY}  # T31a: GraphQL viewer, never REST GET /user (403 for the Actions token)


def test_viewer_reads_x_oauth_scopes_from_the_same_call():
    g, runner, _ = make(viewer_reply(5, "octo", scopes="gist, project, read:org, repo, workflow"))
    v = g.viewer()
    assert v.id == 5 and v.scopes == ["gist", "project", "read:org", "repo", "workflow"] and len(runner.calls) == 1
    g, _, _ = make(viewer_reply(5, "octo", scopes=""))
    assert g.viewer().scopes == []


def test_viewer_records_the_typename():
    g, _, _ = make(viewer_reply(41898282, "github-actions[bot]", typename="User"))  # T31: the Actions token is a User in `viewer`
    assert g.viewer() == Viewer(41898282, "github-actions[bot]", None, "User")
    g, _, _ = make(viewer_reply(7, "some-app[bot]", typename="Bot"))
    assert g.viewer().typename == "Bot"
    assert Viewer(1, "a", None).typename is None  # existing three-field constructors keep working


def test_viewer_is_a_read_so_the_write_gate_is_untouched():
    assert not is_graphql_write(ghmod.VIEWER_QUERY)
    g, _, _ = make(viewer_reply())  # not armed
    assert g.viewer().id == 22502189 and not g.armed


def test_viewer_works_where_rest_user_would_403():
    """T31a-AC3: a runner that answers 403 to REST `user` (as GitHub does to the Actions token) and the GraphQL viewer to the rest."""
    runner = bot_runner()
    assert Gh(runner=runner).viewer().id == 41898282 and runner.paths == ["graphql"]


def test_viewer_graphql_errors_stand_with_no_rest_fallback():
    g, runner, _ = make(gql_err({"type": "FORBIDDEN", "message": "nope"}))
    with pytest.raises(GhError) as e:
        g.viewer()
    assert e.value.code == "E_GH_GRAPHQL" and len(runner.calls) == 1  # one path, fail closed: no second call


def test_no_product_code_sends_rest_get_user():
    rest_user = re.compile(r"""["']GET["']\s*,\s*["']/?user["']""")
    for f in sorted(SRC.rglob("*.py")):
        assert not rest_user.search(f.read_text(encoding="utf-8")), f


# --- the real subprocess runner, against a stub `gh` on PATH ---------------------------------------------------

@pytest.fixture
def stub_gh(tmp_path, monkeypatch):
    """A tiny python `gh` on PATH that records argv/stdin/one env var and answers like `gh api -i`."""
    log = tmp_path / "log.json"
    script = tmp_path / "bin" / "gh"
    script.parent.mkdir()
    script.write_text(f"""#!{sys.executable}
import json, os, sys, time
mode = os.environ.get("STUB_MODE", "ok")
if mode == "sleep":
    time.sleep(30)
json.dump({{"argv": sys.argv[1:], "stdin": sys.stdin.read(), "env": os.environ.get("STUB_ENV_PROBE")}}, open({str(log)!r}, "w"))
if mode == "fail":
    sys.stderr.write("boom " + os.environ.get("STUB_SECRET", ""))
    sys.exit(1)
sys.stdout.write("HTTP/2.0 200 OK\\r\\nX-OAuth-Scopes: repo\\r\\n\\r\\n" + json.dumps({{"id": 1, "login": "octo"}}))
""")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(script.parent))
    return log


def test_real_runner_passes_argv_stdin_and_environment_through(stub_gh, monkeypatch):
    monkeypatch.setenv("STUB_ENV_PROBE", "passed-through")
    g = Gh()
    g.arm()
    assert g.rest("POST", "repos/o/r/issues", {"title": "t"}) == {"id": 1, "login": "octo"}
    seen = json.loads(stub_gh.read_text())
    assert seen["argv"] == ["api", "-X", "POST", "repos/o/r/issues", "-H", VERSION_HEADER, "-i", "--input", "-"]
    assert json.loads(seen["stdin"]) == {"title": "t"}
    assert seen["env"] == "passed-through"


def test_real_runner_failure_is_scrubbed(stub_gh, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "fail")
    monkeypatch.setenv("STUB_SECRET", CANARY)
    with pytest.raises(GhError) as e:
        Gh().rest("GET", "user")
    assert "boom" in str(e.value) and CANARY not in str(e.value)


def test_real_runner_times_out(stub_gh, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "sleep")
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess_runner(["gh", "api"], None, 0.3)
    assert ghmod.TIMEOUT >= 10


# --- the fakes later tasks rely on ------------------------------------------------------------------------------

def test_fakegh_answers_and_enforces_the_arm_gate():
    fake = FakeGh().on_rest("GET", "user", {"id": 1}).on_rest("POST", "x", lambda b: {"got": b}).on_graphql("viewer", {"v": 1})
    assert fake.rest("GET", "user") == {"id": 1} and fake.graphql("{ viewer { id } }") == {"v": 1}
    with pytest.raises(WriteNotArmed):
        fake.rest("POST", "x", {"a": 1})
    fake.arm()
    assert fake.rest("POST", "x", {"a": 1}) == {"got": {"a": 1}}
    assert fake.calls[-1] == ("rest", "POST", "x", {"a": 1})
    with pytest.raises(AssertionError):
        fake.rest("GET", "other")


# --- review round 1 ----------------------------------------------------------------------------------------------

def test_malformed_status_line_is_no_response_not_a_crash():
    assert parse_include("HTTP/2.0\r\n\r\n{}")[0] is None
    g, _, _ = make((0, "HTTP/2.0\r\n\r\n{}", ""))
    with pytest.raises(GhError) as e:
        g.rest("GET", "x")
    assert e.value.code == "E_GH_NO_RESPONSE"


def test_a_text_body_that_starts_with_http_is_not_reparsed_as_headers():
    status, headers, body = parse_include("HTTP/2.0 200 OK\r\nk: v\r\n\r\nHTTP/1.1 500 nope")
    assert (status, headers, body) == (200, {"k": "v"}, "HTTP/1.1 500 nope")
    g, _, _ = make(http(200, "HTTP/1.1 500 nope"))
    with pytest.raises(GhError) as e:
        g.rest("GET", "x")
    assert e.value.code == "E_GH_BAD_BODY"


@pytest.mark.parametrize("value", ["-5", "0", "-1"])
def test_a_nonpositive_retry_after_sleeps_at_least_one_second(value):
    g, _, clock = make(http(429, {}, {"Retry-After": value}), http(200, {}))
    g.rest("GET", "x")
    assert clock.sleeps == [1]


def test_a_past_reset_sleeps_at_least_one_second():
    g, runner, clock = make(None, http(200, {}))
    runner.results[0] = http(403, {}, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(clock.now) - 50)})
    g.rest("GET", "x")
    assert clock.sleeps == [1]


def _vw(**over):
    v = {"databaseId": 7, "login": "o", "__typename": "User"}
    v.update(over)
    return {k: x for k, x in v.items() if x is not ...}


@pytest.mark.parametrize("body", [
    [], "text", {"data": None}, {"data": {}}, {"data": {"viewer": None}}, {"data": {"viewer": []}},
    {"data": {"viewer": _vw(databaseId="7")}}, {"data": {"viewer": _vw(databaseId=...)}}, {"data": {"viewer": _vw(databaseId=True)}},
    {"data": {"viewer": _vw(databaseId=0)}}, {"data": {"viewer": _vw(databaseId=-3)}}, {"data": {"viewer": _vw(databaseId=7.0)}},
    {"data": {"viewer": _vw(databaseId=None)}}, {"data": {"viewer": _vw(login=3)}}, {"data": {"viewer": _vw(login=...)}},
    {"data": {"viewer": _vw(__typename=...)}}, {"data": {"viewer": _vw(__typename=5)}},
    {"id": 7, "login": "o"},  # the old REST shape is not accepted
])
def test_viewer_with_an_unexpected_body_raises_gherror(body):
    g, _, _ = make(http(200, body))
    with pytest.raises(GhError) as e:
        g.viewer()
    assert e.value.code == "E_GH_BAD_BODY"


@pytest.mark.parametrize("path", [
    None, 5, "", "-x", "a b", "repos/{owner}/{repo}/issues", "repos/o/r}", "https://evil.example/x", "//host/x?u=http://x",
    "graphql", "/graphql", "GraphQL", "/graphql/", "graphql?x=1", "/GRAPHQL#f",
])
def test_rest_refuses_dangerous_paths(path):
    g, runner, _ = make(http(200, {}), armed=True)
    with pytest.raises((ValueError, GhError)):
        g.rest("GET", path)
    assert runner.calls == []


def test_a_write_without_an_http_response_says_the_outcome_is_unknown():
    g, _, _ = make((1, "", "connection reset"), armed=True)
    with pytest.raises(GhError) as e:
        g.rest("POST", "repos/o/r/issues", {"title": "t"})
    assert e.value.code == "E_GH_NO_RESPONSE" and "unknown whether applied; re-read before retrying" in str(e.value)


def test_a_write_with_a_non_json_2xx_says_the_outcome_is_unknown():
    g, _, _ = make(http(201, "<html>oops</html>"), armed=True)
    with pytest.raises(GhError) as e:
        g.rest("POST", "repos/o/r/issues", {"title": "t"})
    assert e.value.code == "E_GH_BAD_BODY" and "unknown whether applied; re-read before retrying" in str(e.value)


def test_a_read_without_a_response_does_not_claim_an_unknown_outcome():
    g, _, _ = make(*[(1, "", "connection reset")])
    with pytest.raises(GhError) as e:
        g.rest("GET", "x")
    assert "unknown whether applied" not in str(e.value)


def test_scrub_masks_json_shaped_and_header_authorization_values():
    pat = "github_pat_" + "A1" * 15
    bearer = "gho" + "_" + "Q" * 30
    for text in ('{"Authorization":"Bearer ' + bearer + '"}', '{"authorization": "token abc123"}', "Authorization: Bearer " + bearer,
                 "authorization=basic dXNlcjpwdw==", "pat " + pat):
        out = ghmod.scrub(text)
        assert bearer not in out and "abc123" not in out and "dXNlcjpwdw" not in out and pat not in out and "***" in out
    assert ghmod.scrub('{"Authorization":"Bearer x1"}') == '{"Authorization":"***"}'
    sha = "a" * 40
    assert ghmod.scrub(f"commit {sha}") == f"commit {sha}"  # a git sha is not a secret


def test_scope_missing_without_named_scopes_has_a_sensible_message():
    msg = str(ScopeMissing([]))
    assert "one of: )" not in msg and "gh auth refresh" in msg and "did not name" in msg


# --- T05h transport hardening: hostile-server and hostile-caller cases ---------------------------------------------

def raw_status(status, body=None):
    """Like `http()`, but a status of 0 is spelled `000` (what a three-digit status line carries)."""
    return http(status, body) if status else (0, "HTTP/2.0 000 X\r\n\r\n{}", "")


def _is_named_gh_error(exc):
    return isinstance(exc, GhError) and exc.code.startswith("E_GH_")


@pytest.mark.parametrize("body", [
    {"data": {}, "errors": 5},
    {"data": None, "errors": "boom"},
    {"data": None, "errors": {"message": "x"}},
    {"data": {}, "errors": True},
])
def test_ac1_a_non_list_errors_value_raises_gherror(body):
    g, _, _ = make(http(200, body))
    with pytest.raises(GhError) as e:
        g.graphql("{ viewer { id } }")
    assert e.value.code == "E_GH_BAD_BODY"


@pytest.mark.parametrize("message", [5, ["a", "b"], {"k": "v"}, None])
def test_ac1_a_non_str_insufficient_scopes_message_raises_gherror(message):
    g, _, _ = make(gql_err({"type": "INSUFFICIENT_SCOPES", "message": message}))
    with pytest.raises(GhError) as e:
        g.graphql("{ viewer { id } }")
    assert _is_named_gh_error(e.value)


@pytest.mark.parametrize("status", [403, 404, 422, 429, 500])
@pytest.mark.parametrize("message", [["a", "b"], 5, {"k": "v"}])
def test_ac1_a_non_str_error_message_raises_gherror(status, message):
    g, _, _ = make(http(status, {"message": message}), http(status, {"message": message}), http(status, {"message": message}))
    with pytest.raises(GhError) as e:
        g.rest("GET", "x")
    assert _is_named_gh_error(e.value) and (e.value.status == status or isinstance(e.value, RateLimited))  # a 429 is still a rate limit


def test_ac1_a_null_error_message_is_just_no_message():
    g, _, _ = make(http(404, {"message": None}))
    with pytest.raises(GhError) as e:
        g.rest("GET", "x")
    assert e.value.code == "E_GH_HTTP" and e.value.status == 404


@pytest.mark.parametrize("data", [[1], 5, "text", [], 0, "", True])
def test_ac1_a_non_dict_data_raises_gherror(data):
    g, _, _ = make(http(200, {"data": data}))
    with pytest.raises(GhError) as e:
        g.graphql("{ viewer { id } }")
    assert e.value.code == "E_GH_BAD_BODY" and "unknown whether applied" not in str(e.value)


def test_ac1_a_non_dict_data_on_a_mutation_says_the_outcome_is_unknown():
    g, runner, _ = make(http(200, {"data": [1]}), armed=True)
    with pytest.raises(GhError) as e:
        g.graphql("mutation { a }")
    assert e.value.code == "E_GH_BAD_BODY" and "unknown whether applied; re-read before retrying" in str(e.value)
    assert len(runner.calls) == 1


def test_ac1_missing_or_null_data_is_still_an_empty_dict():
    g, _, _ = make(http(200, {}), http(200, {"data": None}))
    assert g.graphql("{ a }") == {} and g.graphql("{ a }") == {}


@pytest.mark.parametrize("query", [None, 5, b"{ a }", ["{ a }"], {"q": 1}])
def test_ac2_a_non_str_query_is_refused_before_any_spawn(query):
    g, runner, _ = make(gql_ok(), armed=True)
    with pytest.raises((GhError, ValueError)):
        g.graphql(query)
    assert runner.calls == []


@pytest.mark.parametrize("method", ["GET", "HEAD", "get"])
@pytest.mark.parametrize("body", [{}, {"a": 1}, [], ""])
def test_ac3_a_body_on_get_or_head_is_refused_before_any_spawn(method, body):
    g, runner, _ = make(http(200, {}))
    with pytest.raises(ValueError):
        g.rest(method, "repos/o/r", body)
    assert runner.calls == []


GRAPHQL_VARIANTS = ["%67raphql", "./graphql", "graphql;x", "graphql?x", "/graphql/", "GRAPHQL", "GraphQL/", "repos/o/r/../../graphql",
                    "//graphql", "graphql//", "graphql#f", "%2fgraphql", "graphql%3fx", "graphql%3bx", "%2567raphql", "\\graphql",
                    "x/../graphql", "graphql/.", "%2e/graphql", "graphql;", "repos/o/%2e%2e/graphql"]


@pytest.mark.parametrize("path", GRAPHQL_VARIANTS)
def test_ac3_graphql_path_variants_are_refused_by_rest_before_any_spawn(path):
    g, runner, _ = make(http(200, {}), armed=True)
    for method in ("GET", "POST"):
        with pytest.raises(ValueError):
            g.rest(method, path, None if method == "GET" else {"query": "mutation { a }"})
    assert runner.calls == []


@pytest.mark.parametrize("path", [
    "repos/o/graphql/issues/1", "repos/graphql/graphql", "repos/o/graphql", "repos/o/graphql/", "repos/o/r/contents/graphql",
    "search/issues?q=graphql", "search/issues?q=repo%3Ao%2Fgraphql&per_page=100", "repos/o/graphql-js/issues",
    "repos/o/graphqlx", "xgraphql", "orgs/graphql/repos?x=/graphql", "repos/o/r/issues?labels=a%20b", "repos/o/r/git/refs/heads/graphql",
])
def test_ac3_legitimate_paths_mentioning_graphql_are_allowed(path):
    g, runner, _ = make(http(200, {"ok": 1}))
    assert g.rest("GET", path) == {"ok": 1}
    assert len(runner.calls) == 1


def test_ac3_graphql_still_goes_through_graphql():
    g, runner, _ = make(gql_ok({"x": 1}))
    assert g.graphql("{ viewer { id } }") == {"x": 1}
    assert runner.calls[0]["argv"][4] == "graphql"


@pytest.mark.parametrize("status", [100, 101, 301, 302, 304, 399, 0])
@pytest.mark.parametrize("write", ["rest", "graphql"])
def test_ac4_a_write_with_a_final_non_2xx_non_error_status_is_unknown_outcome_and_not_retried(status, write):
    g, runner, clock = make(raw_status(status, {"data": {}}), gql_ok(), armed=True)
    with pytest.raises(GhError) as e:
        if write == "rest":
            g.rest("POST", "repos/o/r/issues", {"title": "t"})
        else:
            g.graphql("mutation { a }")
    assert _is_named_gh_error(e.value) and e.value.status == status
    assert "unknown whether applied; re-read before retrying" in str(e.value)
    assert len(runner.calls) == 1 and clock.sleeps == []


@pytest.mark.parametrize("status", [100, 301, 302, 304, 0])
@pytest.mark.parametrize("read", ["rest", "graphql"])
def test_ac4_a_read_with_such_a_status_raises_a_plain_gherror(status, read):
    g, runner, _ = make(raw_status(status, {"data": {}}), gql_ok(), gql_ok())
    with pytest.raises(GhError) as e:
        g.rest("GET", "x") if read == "rest" else g.graphql("{ a }")
    assert _is_named_gh_error(e.value) and e.value.status == status
    assert "unknown whether applied" not in str(e.value) and len(runner.calls) == 1


def test_ac4_an_unparseable_status_on_a_write_is_unknown_outcome():
    g, runner, _ = make((0, "HTTP/2.0 abc\r\n\r\n{}", ""), armed=True)
    with pytest.raises(GhError) as e:
        g.rest("DELETE", "repos/o/r/issues/1")
    assert "unknown whether applied; re-read before retrying" in str(e.value) and len(runner.calls) == 1


@pytest.mark.parametrize("status", [200, 201, 202, 204])
def test_ac4_a_2xx_status_is_still_success(status):
    g, _, _ = make(http(status, {"a": 1} if status != 204 else None), armed=True)
    g.rest("POST", "x", {})


@pytest.mark.parametrize("path", GRAPHQL_VARIANTS + [None, 5, "", "-x", "a b", "repos/{owner}/x", "https://evil.example/x"])
def test_ac5_fakegh_rest_refuses_what_the_real_transport_refuses(path):
    fake = FakeGh().on_rest("GET", path if isinstance(path, str) else "x", {})
    with pytest.raises((ValueError, GhError)):
        fake.rest("GET", path)
    assert fake.calls == []


def test_ac5_fakegh_rest_refuses_a_body_on_get_and_uses_the_gh_validator():
    fake = FakeGh().on_rest("GET", "user", {"id": 1})
    with pytest.raises(ValueError):
        fake.rest("GET", "user", {"a": 1})
    assert fake.calls == []
    import conftest
    assert "check_rest_request" in conftest.FakeGh.rest.__code__.co_names
    assert fake.rest("GET", "user") == {"id": 1}


@pytest.mark.parametrize("query", [
    "{ a ( } mutation { b ) }",                 # the T05 review case: the old depth counter ended at 0 and never saw `mutation`
    "{ a [ } mutation { b ] }",
    "( { ) } mutation { x }",                   # interleaved
    "{ ( } )",
    "{ a ( } query { b ) }",                    # no mutation, but ambiguous: fail closed
    "{ a ) ( }",
    "{ a ] }",
    "] mutation { a }",
    "{ [ ( ] ) }",
    "{ a } {",
    "{ a } }",
    "((((",
    "{{{{",
    "}",
])
def test_ac6_unbalanced_and_interleaved_brackets_are_writes(query):
    assert is_graphql_write(query)


@pytest.mark.parametrize("query", [
    "{ a(x: [1, {b: 2}]) { c } }",
    "query Q($a: [ID!]!) { n(ids: $a) { id } }",
    "{ a } # ( [ { unbalanced in a comment",
    '{ a(s: "}}))]]") }',
    '{ a(s: """ ) } ] """) { b } }',
    '{ a(s: "x \\" ) }") }',
    "{ a }\n#{\n{ b }",
    "",
])
def test_ac6_brackets_in_comments_strings_and_block_strings_are_ignored(query):
    assert not is_graphql_write(query)


@pytest.mark.parametrize("query", [
    "{ a } # {\nmutation { b }",
    '{ a(s: "(") }\nmutation { b }',
    '{ a(s: """(""") }\nmutation { b }',
    "{ a(x: [1]) }\nmutation { b }",
])
def test_ac6_a_bracket_in_a_comment_or_string_cannot_hide_a_mutation(query):
    assert is_graphql_write(query)


@pytest.mark.parametrize("make_doc", [
    lambda n: "(" * n,
    lambda n: "{" * n,
    lambda n: "{" * (n // 2) + "}" * (n // 2),
    lambda n: "{(" * (n // 4) + ")}" * (n // 4),
    lambda n: '"' * n,
    lambda n: '"a' * (n // 2),
    lambda n: '""' * (n // 2),
    lambda n: '"""' * (n // 3),
    lambda n: '"""\\"""' * (n // 7),
    lambda n: "#" + "x" * n,
    lambda n: "# c\n" * (n // 4),
    lambda n: "#\r" * (n // 2),
    lambda n: "{ a }" + " # (" * (n // 4),
    lambda n: "a" * n,
    lambda n: "{ " + "mutationx " * (n // 10) + "}",
    lambda n: "[" * (n // 2) + ")" * (n // 2),
    lambda n: "\\" * n,
    lambda n: '"' + "\\" * n,
])
def test_ac6_hostile_300k_documents_are_scanned_in_linear_time(make_doc):
    import time
    doc = make_doc(300_000)
    started = time.perf_counter()
    is_graphql_write(doc)
    elapsed = time.perf_counter() - started
    assert elapsed < 2.0, f"{elapsed:.2f} s for {len(doc)} characters"


# --- T05h fix round 1 (review minors) --------------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["graphql.", "graphql..", "graphql.json", "graphql%00", "graphql%0a", "graphql%09", "graphql%20", "%20graphql",
                                  "graphql:", "graphql~", "ｇｒａｐｈｑｌ", ";graphql", "/;graphql/", ".graphql", "graphql%2e", "％67raphql",
                                  "graphql\x00", "GraphQL.JSON"])
def test_r1_more_graphql_spellings_are_refused_by_rest_and_the_fake(path):
    g, runner, _ = make(http(200, {}), armed=True)
    for method in ("GET", "POST"):
        with pytest.raises(ValueError):
            g.rest(method, path, None if method == "GET" else {})
    assert runner.calls == []
    if not any(c.isspace() for c in path):
        fake = FakeGh().on_rest("GET", path, {})
        with pytest.raises(ValueError):
            fake.rest("GET", path)


@pytest.mark.parametrize("path", ["repos/o/graphql/issues/1", "search/issues?q=graphql", "search/issues?q=graphql.json", "repos/o/graphql.js",
                                  "repos/graphql/graphql.json", "user", "rate_limit", "repos/o/r/contents/graphql.md"])
def test_r1_a_name_that_merely_contains_graphql_is_still_allowed(path):
    g, runner, _ = make(http(200, {"ok": 1}))
    assert g.rest("GET", path) == {"ok": 1}
    assert len(runner.calls) == 1


def test_r1_scopes_regex_is_linear_on_a_hostile_message():
    import time
    msg = "following scopes: [" * 15790
    g, _, _ = make(gql_err({"type": "INSUFFICIENT_SCOPES", "message": msg}))
    started = time.perf_counter()
    with pytest.raises(ScopeMissing) as e:
        g.graphql("{ a }")
    assert e.value.scopes == [] and time.perf_counter() - started < 2.0


def test_r1_merging_scopes_over_many_errors_is_linear_and_deduplicated():
    import time
    errs = [{"type": "INSUFFICIENT_SCOPES", "message": f"following scopes: ['s{i}', 's{i}', 'x'], but"} for i in range(20_000)]
    g, _, _ = make(gql_err(*errs))
    started = time.perf_counter()
    with pytest.raises(ScopeMissing) as e:
        g.graphql("{ a }")
    assert time.perf_counter() - started < 2.0
    assert len(e.value.scopes) == 20_001 and e.value.scopes[:3] == ["s0", "x", "s1"]


@pytest.mark.parametrize("method", ["POST ", None, 5, b"POST", "OPTIONS", "TRACE", "", ["GET"]])
def test_r1_fakegh_and_gh_refuse_the_same_methods(method):
    g, runner, _ = make(http(200, {}), armed=True)
    with pytest.raises(ValueError):
        g.rest(method, "repos/o/r", None)
    assert runner.calls == []
    fake = FakeGh().on_rest("GET", "repos/o/r", {})
    with pytest.raises(ValueError):
        fake.rest(method, "repos/o/r")
    assert fake.calls == []


@pytest.mark.parametrize("query", [
    '{ a(s: "x\\\nmutation { b }") }',        # \ then LF inside a string
    '{ a(s: "x\\\rmutation { b }") }',        # \ then CR
    '{ a(s: "x\\\r\nmutation { b }") }',
    '{ a(s: "x\\',                              # \ at the very end
])
def test_r1_a_backslash_before_a_line_end_in_a_string_is_a_write(query):
    assert is_graphql_write(query)


def test_r1_a_backslash_escape_in_a_string_is_still_a_read():
    assert not is_graphql_write('{ a(s: "x\\ny\\"z\\\\") }')


@pytest.mark.parametrize("query", ["mutation٣{a}", "mutationé {a}", "{ a } ٣", "{ éa }", "ｍutation { a }", "{ a }\nfoo٣"])
def test_r1_a_non_ascii_name_character_outside_a_string_is_a_write(query):
    assert is_graphql_write(query)


@pytest.mark.parametrize("query", ['{ a(s: "é٣") }', "{ a } # é٣", '{ a(s: """é٣""") }', "{ a }​", "{ a }﻿", "{ a } "])
def test_r1_non_ascii_inside_strings_comments_and_blanks_is_not_a_write(query):
    assert not is_graphql_write(query)


# --- T26d: a 4xx reply's `errors[]` (the 422 "A pull request already exists" reason) is kept, bounded and escaped -----------------------

def _reply(status, *errors, message="Validation Failed", **more):
    return http(status, {"message": message, "errors": list(errors), **more})


def _raised(*results, write=False, body=None, method="GET"):
    g, runner, _ = make(*results, armed=write)
    with pytest.raises(GhError) as e:
        g.rest(method, "repos/o/r/pulls", body)
    return e.value


def test_t26d_a_422_keeps_the_reason_the_live_run_lost():
    e = _raised(_reply(422, {"resource": "PullRequest", "code": "custom", "message": "A pull request already exists for octo:feat."}),
                write=True, method="POST", body={"title": "t"})
    assert e.code == "E_GH_HTTP" and e.status == 422
    assert str(e) == ("E_GH_HTTP (HTTP 422): Validation Failed (errors: resource=PullRequest code=custom "
                      "message=A pull request already exists for octo:feat.)")


def test_t26d_the_fields_come_in_a_fixed_order_and_errors_are_joined():
    e = _raised(_reply(422, {"message": "m1", "code": "c1", "field": "f1", "resource": "R1"}, {"code": "c2"}))
    assert str(e) == "E_GH_HTTP (HTTP 422): Validation Failed (errors: resource=R1 field=f1 code=c1 message=m1; code=c2)"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 410, 422, 451])
def test_t26d_every_4xx_carries_it(status):
    e = _raised(_reply(status, {"code": "missing"}, message="Bad"))
    assert e.status == status and str(e).endswith("Bad (errors: code=missing)" if status != 401 else "(errors: code=missing)")


def test_t26d_a_5xx_or_a_rate_limit_is_unchanged():
    e = _raised(*[_reply(500, {"code": "x"}, message="Server Error")] * 3)
    assert str(e) == "E_GH_HTTP (HTTP 500): Server Error"
    e = _raised(_reply(403, {"code": "x"}, message="API rate limit exceeded"), _reply(403, {"code": "x"}, message="API rate limit exceeded"),
                _reply(403, {"code": "x"}, message="API rate limit exceeded"))
    assert isinstance(e, RateLimited) and "errors:" not in str(e)


@pytest.mark.parametrize("errors", [None, [], {}, "x", 5, [None], [5, "s", ["a"]], [{}], [{"value": "v", "url": "u", "other": "o"}], [{"code": 5, "message": None, "field": ["a"]}]])
def test_t26d_without_a_usable_errors_array_the_text_is_exactly_what_it_was(errors):
    body = {"message": "Validation Failed"}
    if errors is not None:
        body["errors"] = errors
    e = _raised(http(422, body))
    assert str(e) == "E_GH_HTTP (HTTP 422): Validation Failed" and e.detail == "Validation Failed"


def test_t26d_other_error_texts_and_codes_are_unchanged():
    assert str(_raised(http(404, {"message": "Not Found"}))) == "E_GH_HTTP (HTTP 404): Not Found"
    assert str(_raised(http(401, {"message": "Bad credentials"}))) == "E_GH_HTTP (HTTP 401): Bad credentials (not signed in? run `gh auth login`)"
    e = _raised(http(422, {"message": ["a"], "errors": [{"code": "x"}]}))
    assert e.code == "E_GH_BAD_BODY" and "errors:" not in str(e)  # a message that is not a string is still that error


def test_t26d_only_strings_of_the_four_named_keys_count_and_bodies_and_headers_never_do():
    e = _raised(_reply(422, {"resource": "R", "value": "SECRET-VALUE", "url": "https://x", "field": 5, "code": ["c"], "message": {"k": "SECRET-NESTED"},
                             "extra": {"message": "SECRET-DEEP"}}, "SECRET-STR", 7, None, ["SECRET-LIST"], {"errors": [{"message": "SECRET-INNER"}]}),
                write=True, method="POST", body={"title": "SECRET-REQUEST-BODY"})
    text = str(e)
    assert text.endswith("(errors: resource=R)") and "SECRET" not in text


def test_t26d_hostile_values_are_escaped_ascii_and_control_free():
    hostile = "line1\nline2\r\n\x1b[31mred\x1b[0m\t\x00\x7f ‮evil é中\U0001f600 \ud800 \\n"
    e = _raised(_reply(422, {"resource": hostile, "field": hostile, "code": hostile, "message": hostile}))
    text = str(e)
    assert text.isascii() and not any(ord(c) < 32 or ord(c) == 127 for c in text) and e.detail.isascii()
    assert "\\x1b[31mred" in text and "\\u202e" in text and "\\xe9" in text and "\\U0001f600" in text and "\\n" in text
    assert len(e.detail) <= 400 + 500


@pytest.mark.parametrize("size", [30_000, 300_000, 5_000_000])
def test_t26d_huge_values_and_huge_arrays_are_bounded_and_fast(size):
    import time
    big = "x\n" * size
    t = time.perf_counter()
    e = _raised(_reply(422, {"message": big, "code": big, "field": "é" * size, "resource": big}))
    assert len(e.detail) <= 400 + 500 and len(str(e)) <= 560 and time.perf_counter() - t < 5
    t = time.perf_counter()
    e = _raised(http(422, {"message": "Validation Failed", "errors": [{"code": "c%d" % i} for i in range(min(size, 200_000))]}))
    assert len(e.detail) <= 400 + 500 and time.perf_counter() - t < 5


def test_t26d_the_summary_is_at_most_500_characters_and_says_when_it_was_cut():
    e = _raised(_reply(422, *[{"message": "m" * 150, "code": "c%d" % i} for i in range(20)]))
    summary = e.detail[len("Validation Failed"):]
    assert 400 <= len(summary) <= 500 and summary.startswith(" (errors: ") and summary.endswith("...)")
    e = _raised(_reply(422, {"message": "short"}))
    assert not e.detail.endswith("...)")


def test_t26d_a_token_shaped_value_is_scrubbed():
    e = _raised(_reply(422, {"message": "token %s here" % CANARY, "code": "ghs_" + "Z" * 30}))
    assert CANARY not in str(e) and "ghs_" + "Z" * 30 not in str(e) and "***" in str(e)


def test_t26d_the_message_part_keeps_its_own_cap():
    e = _raised(_reply(422, {"code": "c"}, message="M" * 1000))
    assert e.detail.startswith("M" * 400 + " (errors: code=c)") and len(e.detail) == 400 + len(" (errors: code=c)")


def test_t26d_a_non_list_non_dict_body_is_left_alone():
    assert str(_raised(http(422, "plain text"))) == "E_GH_HTTP (HTTP 422): plain text"
    assert str(_raised(http(422, [1, 2]))) == "E_GH_HTTP (HTTP 422): [1, 2]"


def test_t26d_each_value_is_cut_at_200_characters_before_anything_else_is_done_to_it():
    assert len(ghmod._escaped("x" * 1000)) == 200 and len(ghmod._escaped("é" * 1000)) == 200 and ghmod._escaped("\n" * 1000) == "\\x0a" * 50


def test_t26d_only_the_first_20_entries_are_looked_at():
    assert ghmod.errors_summary({"errors": [{}] * 20 + [{"code": "late"}]}) == ""
    assert ghmod.errors_summary({"errors": [{}] * 19 + [{"code": "last"}]}) == " (errors: code=last)"


# ---- review round 1 (M5, M6): scrub before any cut; never cut an escape in half ----
@pytest.mark.parametrize("prefix", [0, 150, 185, 190, 199, 200, 5000])
@pytest.mark.parametrize("token", ["ghp_" + "A1b2C3d4E5" * 4, "github_pat_" + "Z9y8X7w6V5" * 4, "ghs_" + "Q" * 30])
def test_t26d_a_token_longer_than_the_cut_leaks_no_fragment(prefix, token):
    text = ghmod._escaped("a" * prefix + token + " tail")
    assert token[:8] not in text and token[4:14] not in text and token[-10:] not in text
    e = _raised(_reply(422, {"message": "a" * prefix + token}))
    assert token[:8] not in str(e) and token[4:14] not in str(e)
    assert ("***" in str(e)) == (prefix <= 197)  # the scrubbed value is cut after: a token is whole (`***`) or not shown at all


def test_t26d_the_detail_scrub_order_is_unchanged():
    e = _raised(http(422, {"message": "x" * 390 + "ghp_" + "A1b2C3d4E5" * 4}))
    assert "ghp_" not in str(e) and "A1b2C3d4E5" not in str(e)


@pytest.mark.parametrize("filler", range(190, 205))
@pytest.mark.parametrize("char,escape", [("é", "\\xe9"), ("\n", "\\x0a"), ("‮", "\\u202e"), ("\U0001f600", "\\U0001f600"), ("\ud800", "\\ud800")])
def test_t26d_an_escape_is_never_cut_in_half(filler, char, escape):
    value = "a" * filler + char + "b"
    pieces = ["a"] * filler + [escape, "b"]
    valid = ["".join(pieces[:i]) for i in range(len(pieces) + 1)]
    out = ghmod._escaped(value)
    assert out in valid and len(out) <= 200
    assert out == max((v for v in valid if len(v) <= 200), key=len)  # the longest clean prefix that fits


def test_t26d_the_cut_of_the_whole_summary_does_not_split_an_escape_either():
    cut = 0
    for n in range(100, 200):
        s = ghmod.errors_summary({"errors": [{"message": "a" * n}, {"message": "é" * 50}, {"message": "\U0001f600" * 20}]})
        assert len(s) <= 500
        if s.endswith("...)"):
            cut += 1
            assert not re.search(r"\\(?:x[0-9a-f]?|u[0-9a-f]{0,3}|U[0-9a-f]{0,7})\.\.\.\)$", s), s
    assert cut > 90  # the cut really was exercised at many positions


def test_t26d_the_escape_of_a_long_hostile_value_is_still_fast():
    import time
    t = time.perf_counter()
    for value in ("é" * 5_000_000, "authorization: " + " " * 300_000, "\x00" * 5_000_000, "ghp_" + "A" * 5_000_000):
        assert len(ghmod._escaped(value)) <= 200
    assert time.perf_counter() - t < 10
