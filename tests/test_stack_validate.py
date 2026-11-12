"""Profile validation (V3 piece 2, P2-2a): `stack.parse_json` and `stack.validate`, a trust boundary.

The profile is attacker-controlled in CI and model-written at creation, so every rule is checked for what the error may carry: a fixed
text and the position in the profile, never a value."""
import copy
import datetime
import json
import re

import pytest

from ghp import stack

MARKER = "ZZ-UNTRUSTED"


def good():
    """A full valid profile (every field of D§5 used once)."""
    return {
        "schema": 1, "version": 3, "status": "approved",
        "stacks": [{
            "id": "python", "root": ".", "kit": None,
            "detected": {"confidence": "high", "evidence": ["pyproject.toml"]},
            "commands": {
                "test": {"run": "PYTHONPATH=src python3 -m pytest -q", "source": "S1"},
                "lint": {"run": "ruff check .", "source": "S2"},
                "format_check": {"run": "ruff format --check . | tee out.txt", "source": "unverified"},
                "build": None,
                "coverage": {"run": "npm run evaluate && pytest --cov", "source": "S1"},
                "security": [{"run": "pip-audit", "source": "S2"}],
                "org_only": [{"run": "sf apex run test --wait 10", "source": "S3"}],
            },
            "conventions": [{"rule": "Test files are named test_*.py under tests/", "source": "S3"}],
            "dod": [{"item": "No new lint findings", "source": "S2"}],
        }, {"id": "web-app", "root": "apps/Web_1/x-y.z", "commands": {"test": {"run": None, "why": "Pega has no local test run"}}}],
        "sources": {
            "S1": {"kind": "official", "url": "https://docs.pytest.org/en/stable/explanation/goodpractices.html", "path": None,
                   "retrieved": "2026-10-30", "evidence": "Run tests with python -m pytest."},
            "S2": {"kind": "community", "url": "https://docs.astral.sh/ruff/#the-ruff-linter", "retrieved": "2026-10-30",
                   "evidence": "ruff check lints."},
            "S3": {"kind": "repo", "path": "package.json", "retrieved": "2026-10-30", "evidence": "scripts.test"},
        },
        "approval": {"by": "tech lead", "date": "2026-10-30", "sha256": "a" * 64, "decision": "US1234 D4"},
    }


def minimal():
    return {"schema": 1, "version": 1, "status": "draft",
            "stacks": [{"id": "python", "root": ".", "commands": {"test": {"run": "pytest -q", "source": "unverified"}}}]}


def put(p, path, value):
    """Set a value at a dotted path of keys/indexes (`stacks.0.commands.test.run`)."""
    node, keys = p, path.split(".")
    for k in keys[:-1]:
        node = node[int(k)] if isinstance(node, list) else node[k]
    node[int(keys[-1]) if isinstance(node, list) else keys[-1]] = value
    return p


def paths(p):
    return [e["path"] for e in stack.validate(p)]


def test_valid_minimal_and_full_profiles():
    assert stack.validate(minimal()) == []
    assert stack.validate(good()) == []
    # the same through the bytes parser
    assert stack.validate(stack.parse_json(json.dumps(good()).encode(), stack.MAX_PROFILE)) == []


def test_constants():
    assert stack.PROFILE_FILE == "ghp.stack.json" and stack.MAX_PROFILE == 256 * 1024


# (id, dotted path to set, value, position reported) -- exactly one error, at exactly that position
BAD = [
    ("http_url", "sources.S1.url", "http://docs.pytest.org/x", "sources.S1.url"),
    ("ftp_url", "sources.S1.url", "ftp://docs.pytest.org/x", "sources.S1.url"),
    ("no_host", "sources.S1.url", "https:///x", "sources.S1.url"),
    ("userinfo", "sources.S1.url", "https://dana:pw@docs.pytest.org/x", "sources.S1.url"),
    ("userinfo_name_only", "sources.S1.url", "https://dana@docs.pytest.org/x", "sources.S1.url"),
    ("url_space", "sources.S1.url", "https://docs.pytest.org/a b", "sources.S1.url"),
    ("url_backslash", "sources.S1.url", "https://docs.pytest.org\\@evil.example/", "sources.S1.url"),
    ("url_non_ascii", "sources.S1.url", "https://docs.pytest.org/é", "sources.S1.url"),
    ("url_backslash_in_path", "sources.S1.url", "https://docs.example/a\\b", "sources.S1.url"),
    ("url_bad_port", "sources.S1.url", "https://docs.pytest.org:99999/", "sources.S1.url"),
    ("url_token_query", "sources.S1.url", "https://docs.pytest.org/x?token=abc", "sources.S1.url"),
    ("url_key_query", "sources.S1.url", "https://docs.pytest.org/x?a=1&api_key=abc", "sources.S1.url"),
    ("url_sig_query", "sources.S1.url", "https://docs.pytest.org/x?X-Amz-Signature=abc", "sources.S1.url"),
    ("url_password_query", "sources.S1.url", "https://docs.pytest.org/x?Password=abc", "sources.S1.url"),
    ("url_secret_semicolon", "sources.S1.url", "https://docs.pytest.org/x?a=1;secret=abc", "sources.S1.url"),
    ("url_encoded_key", "sources.S1.url", "https://docs.pytest.org/x?%74oken=abc", "sources.S1.url"),
    ("url_double_encoded_key", "sources.S1.url", "https://docs.pytest.org/x?%2574oken=abc", "sources.S1.url"),
    ("url_bare_token_query", "sources.S1.url", "https://docs.pytest.org/x?token", "sources.S1.url"),
    ("url_fragment_secret", "sources.S1.url", "https://docs.pytest.org/x#access_token=abc", "sources.S1.url"),
    ("url_too_long", "sources.S1.url", "https://docs.pytest.org/" + "a" * 500, "sources.S1.url"),
    ("url_secret_pattern", "sources.S1.url", "https://docs.pytest.org/ghp_" + "A" * 36, "sources.S1.url"),
    ("ghp_string", "sources.S1.evidence", "use ghp_" + "A" * 36, "sources.S1.evidence"),
    ("github_pat_string", "stacks.0.conventions.0.rule", "github_pat_" + "A" * 22, "stacks[0].conventions[0].rule"),
    ("ado_pat_string", "stacks.0.dod.0.item", "x " + "a" * 52 + " y", "stacks[0].dod[0].item"),
    ("aws_key_string", "approval.by", "AKIA" + "A" * 16, "approval.by"),
    ("private_key_string", "sources.S1.evidence", "-----BEGIN " + "RSA PRIVATE KEY-----", "sources.S1.evidence"),
    ("secret_in_command", "stacks.0.commands.lint.run", "echo ghp_" + "A" * 36, "stacks[0].commands.lint.run"),
    ("dangling_source", "stacks.0.commands.test.source", "S9", "stacks[0].commands.test.source"),
    ("dangling_source_convention", "stacks.0.conventions.0.source", "S9", "stacks[0].conventions[0].source"),
    ("dangling_source_dod", "stacks.0.dod.0.source", "S9", "stacks[0].dod[0].source"),
    ("dangling_source_list", "stacks.0.commands.security.0.source", "S9", "stacks[0].commands.security[0].source"),
    ("source_bad_format", "stacks.0.commands.test.source", "s1", "stacks[0].commands.test.source"),
    ("source_leading_zero", "stacks.0.commands.test.source", "S01", "stacks[0].commands.test.source"),
    ("source_not_string", "stacks.0.commands.test.source", 1, "stacks[0].commands.test.source"),
    ("source_missing_on_run", "stacks.0.commands.test.source", None, "stacks[0].commands.test.source"),
    ("duplicate_stack_id", "stacks.1.id", "python", "stacks[1].id"),
    ("id_upper", "stacks.0.id", "Python", "stacks[0].id"),
    ("id_empty", "stacks.0.id", "", "stacks[0].id"),
    ("id_too_long", "stacks.0.id", "a" * 41, "stacks[0].id"),
    ("id_underscore", "stacks.0.id", "my_stack", "stacks[0].id"),
    ("id_not_string", "stacks.0.id", 7, "stacks[0].id"),
    ("root_dotdot", "stacks.0.root", "../x", "stacks[0].root"),
    ("root_dotdot_middle", "stacks.0.root", "a/../b", "stacks[0].root"),
    ("root_dotdot_only", "stacks.0.root", "..", "stacks[0].root"),
    ("root_absolute", "stacks.0.root", "/etc", "stacks[0].root"),
    ("root_hidden", "stacks.0.root", ".hidden", "stacks[0].root"),
    ("root_hidden_segment", "stacks.0.root", "a/.git", "stacks[0].root"),
    ("root_dash_segment", "stacks.0.root", "a/-rf", "stacks[0].root"),
    ("root_empty_segment", "stacks.0.root", "a//b", "stacks[0].root"),
    ("root_trailing_slash", "stacks.0.root", "a/", "stacks[0].root"),
    ("root_empty", "stacks.0.root", "", "stacks[0].root"),
    ("root_dot_slash", "stacks.0.root", "./a", "stacks[0].root"),
    ("root_space", "stacks.0.root", "a b", "stacks[0].root"),
    ("root_backslash", "stacks.0.root", "a\\b", "stacks[0].root"),
    ("root_newline", "stacks.0.root", "a\nb", "stacks[0].root"),
    ("root_too_long", "stacks.0.root", "a" * 201, "stacks[0].root"),
    ("root_github_token", "stacks.0.root", "ghp_" + "A" * 36, "stacks[0].root"),
    ("root_aws_key", "stacks.0.root", "AKIA" + "A" * 16, "stacks[0].root"),
    ("root_ado_pat", "stacks.0.root", "a" * 52, "stacks[0].root"),
    ("root_not_string", "stacks.0.root", None, "stacks[0].root"),
    ("command_multi_line", "stacks.0.commands.lint.run", "ruff check .\nrm -rf x", "stacks[0].commands.lint.run"),
    ("command_cr", "stacks.0.commands.lint.run", "ruff check .\rrm", "stacks[0].commands.lint.run"),
    ("command_line_separator", "stacks.0.commands.lint.run", "ruff check", "stacks[0].commands.lint.run"),
    ("command_nel", "stacks.0.commands.lint.run", "ruff\x85check", "stacks[0].commands.lint.run"),
    ("command_cyrillic_lookalike", "stacks.0.commands.lint.run", "\u0440\u0443test -q", "stacks[0].commands.lint.run"),
    ("command_zero_width_joiner", "stacks.0.commands.lint.run", "p\u200dytest -q", "stacks[0].commands.lint.run"),
    ("command_zero_width_non_joiner", "stacks.0.commands.lint.run", "p\u200cytest -q", "stacks[0].commands.lint.run"),
    ("command_lrm", "stacks.0.commands.lint.run", "pytest\u200e -q", "stacks[0].commands.lint.run"),
    ("command_rlm", "stacks.0.commands.lint.run", "pytest\u200f -q", "stacks[0].commands.lint.run"),
    ("command_tab", "stacks.0.commands.lint.run", "pytest\t-q", "stacks[0].commands.lint.run"),
    ("command_non_ascii_build", "stacks.0.commands.build", {"run": "m\u00e0ke", "source": "S1"}, "stacks[0].commands.build.run"),
    ("command_non_ascii_test", "stacks.0.commands.test.run", "p\u0443test", "stacks[0].commands.test.run"),
    ("command_non_ascii_org_only", "stacks.0.commands.org_only.0.run", "s\u0435 apex", "stacks[0].commands.org_only[0].run"),
    ("command_nul", "stacks.0.commands.lint.run", "ruff\x00check", "stacks[0].commands.lint.run"),
    ("command_too_long", "stacks.0.commands.lint.run", "a" * 501, "stacks[0].commands.lint.run"),
    ("command_empty", "stacks.0.commands.lint.run", "", "stacks[0].commands.lint.run"),
    ("command_not_object", "stacks.0.commands.lint", "ruff check .", "stacks[0].commands.lint"),
    ("test_command_null", "stacks.0.commands.test", None, "stacks[0].commands.test"),
    ("test_null_without_why", "stacks.1.commands.test", {"run": None}, "stacks[1].commands.test.why"),
    ("retrieved_tomorrow", "sources.S1.retrieved", (datetime.date.today() + datetime.timedelta(days=2)).isoformat(), "sources.S1.retrieved"),
    ("retrieved_not_a_date", "sources.S1.retrieved", "2026-13-45", "sources.S1.retrieved"),
    ("retrieved_compact", "sources.S1.retrieved", "20261030", "sources.S1.retrieved"),
    ("retrieved_not_string", "sources.S1.retrieved", 20261030, "sources.S1.retrieved"),
    ("approval_date_future", "approval.date", (datetime.date.today() + datetime.timedelta(days=2)).isoformat(), "approval.date"),
    ("approval_sha_short", "approval.sha256", "abc", "approval.sha256"),
    ("approval_sha_upper", "approval.sha256", "A" * 64, "approval.sha256"),
    ("approval_not_object", "approval", "tech lead", "approval"),
    ("unknown_top_key", "zzz", 1, "<key #7>"),
    ("unknown_stack_key", "stacks.0.zzz", 1, "stacks[0].<key #8>"),
    ("unknown_command_key", "stacks.0.commands.zzz", 1, "stacks[0].commands.<key #8>"),
    ("unknown_run_key", "stacks.0.commands.lint.zzz", 1, "stacks[0].commands.lint.<key #3>"),
    ("unknown_source_key", "sources.S1.zzz", 1, "sources.S1.<key #6>"),
    ("unknown_detected_key", "stacks.0.detected.zzz", 1, "stacks[0].detected.<key #3>"),
    ("unknown_convention_key", "stacks.0.conventions.0.zzz", 1, "stacks[0].conventions[0].<key #3>"),
    ("unknown_approval_key", "approval.zzz", 1, "approval.<key #5>"),
    ("source_id_not_s_n", "sources.X1", {"kind": "repo", "path": "a", "retrieved": "2026-10-30", "evidence": "e"}, "sources.<key #4>"),
    ("schema_two", "schema", 2, "schema"),
    ("schema_true", "schema", True, "schema"),
    ("schema_float", "schema", 1.0, "schema"),
    ("schema_string", "schema", "1", "schema"),
    ("version_zero", "version", 0, "version"),
    ("version_bool", "version", True, "version"),
    ("version_string", "version", "3", "version"),
    ("status_other", "status", "changed-since-approval", "status"),
    ("kind_other", "sources.S1.kind", "blog", "sources.S1.kind"),
    ("official_without_url", "sources.S1.url", None, "sources.S1.url"),
    ("repo_without_path", "sources.S3.path", None, "sources.S3.path"),
    ("confidence_other", "stacks.0.detected.confidence", "low", "stacks[0].detected.confidence"),
    ("kit_bad", "stacks.0.kit", "SF/x", "stacks[0].kit"),
    ("stacks_not_list", "stacks", {}, "stacks"),
    ("sources_not_object", "sources", [], "sources"),
    ("rule_too_long", "stacks.0.conventions.0.rule", "a" * 301, "stacks[0].conventions[0].rule"),
    ("item_too_long", "stacks.0.dod.0.item", "a" * 301, "stacks[0].dod[0].item"),
    ("evidence_too_long", "sources.S1.evidence", "a" * 301, "sources.S1.evidence"),
    ("why_too_long", "stacks.1.commands.test.why", "a" * 301, "stacks[1].commands.test.why"),
    ("detected_evidence_too_long", "stacks.0.detected.evidence.0", "a" * 301, "stacks[0].detected.evidence[0]"),
    ("rule_control_char", "stacks.0.conventions.0.rule", "a\x1b[31mb", "stacks[0].conventions[0].rule"),
    ("rule_line_separator", "stacks.0.conventions.0.rule", "a b", "stacks[0].conventions[0].rule"),
    ("rule_paragraph_separator", "stacks.0.conventions.0.rule", "a b", "stacks[0].conventions[0].rule"),
    ("rule_nel", "stacks.0.conventions.0.rule", "a\x85b", "stacks[0].conventions[0].rule"),
    ("rule_newline", "stacks.0.conventions.0.rule", "a\nb", "stacks[0].conventions[0].rule"),
    ("rule_lone_surrogate", "stacks.0.conventions.0.rule", "a\ud800b", "stacks[0].conventions[0].rule"),
    ("rule_not_string", "stacks.0.conventions.0.rule", ["x"], "stacks[0].conventions[0].rule"),
    ("evidence_list_too_long", "stacks.0.detected.evidence", ["a"] * 21, "stacks[0].detected.evidence"),
    ("conventions_too_many", "stacks.0.conventions", [{"rule": "r", "source": "S1"}] * 51, "stacks[0].conventions"),
    ("dod_too_many", "stacks.0.dod", [{"item": "i", "source": "S1"}] * 31, "stacks[0].dod"),
    ("security_too_many", "stacks.0.commands.security", [{"run": "x", "source": "S1"}] * 21, "stacks[0].commands.security"),
    ("stacks_too_many", "stacks", [{"id": "a", "root": "."}] * 9, "stacks"),
    ("sources_too_many", "sources", {f"S{n}": {} for n in range(1, 102)}, "sources"),
]


@pytest.mark.parametrize("name,where,value,position", BAD, ids=[b[0] for b in BAD])
def test_rejects(name, where, value, position):
    p = good()
    if name == "source_missing_on_run":
        del p["stacks"][0]["commands"]["test"]["source"]
    elif name == "stacks_too_many":
        p["stacks"] = value
    else:
        if name in ("sources_not_object", "sources_too_many"):  # no source to refer to: keep the refs out of the way
            p = json.loads(re.sub(r'"S[0-9]"', '"unverified"', json.dumps(p)))
            p["sources"] = {}
        put(p, where, value)
    errors = stack.validate(p)
    assert [e["path"] for e in errors] == [position], errors
    assert all(isinstance(e["error"], str) and e["error"] for e in errors)


@pytest.mark.parametrize("tail", [
    "sudo make install", "curl https://x.example", "wget https://x.example", "Invoke-WebRequest https://x.example", "CURL x",
    "make | sh", "make |bash", "make |   bash -s", "eval $FOO", "echo `id`", "echo $(id)", "x && sudo y", "SUDO make"])
def test_rejects_tripwire_token(tail):
    p = good()
    p["stacks"][0]["commands"]["lint"]["run"] = tail
    assert paths(p) == ["stacks[0].commands.lint.run"]
    p = good()
    p["stacks"][0]["commands"]["org_only"][0]["run"] = tail
    assert paths(p) == ["stacks[0].commands.org_only[0].run"]


@pytest.mark.parametrize("ok", ["npm run evaluate", "make | shellcheck", "pip install curlify", "pytest -q | tee out.txt", "echo $HOME"])
def test_tripwire_does_not_refuse_similar_words(ok):
    p = good()
    p["stacks"][0]["commands"]["lint"]["run"] = ok
    assert stack.validate(p) == []


@pytest.mark.parametrize("root", ["-rf", "-", "--x", "a/-rf", "-r/b"])
def test_rejects_root_leading_dash(root):
    p = good()
    p["stacks"][0]["root"] = root
    assert paths(p) == ["stacks[0].root"]


@pytest.mark.parametrize("root", [".", "src", "apps/web", "a/b/c", "_x", "A1/b.c-d_e", "9"])
def test_accepts_roots(root):
    p = good()
    p["stacks"][0]["root"] = root
    assert stack.validate(p) == []


# the exact fixed texts of a few rules, so a redundant or dead branch cannot survive
@pytest.mark.parametrize("where,value,text", [
    ("stacks.0.root", "../x", "must be \".\" or"),
    ("stacks.0.root", "ghp_" + "A" * 36, "matches a secret pattern"),
    ("stacks.0.commands.lint.run", "a\nb", "must be one line of printable ASCII"),
    ("stacks.0.commands.lint.run", "\u0440\u0443test", "must be one line of printable ASCII"),
    ("stacks.0.commands.lint.run", "curl x", "contains a refused command token"),
    ("sources.S1.url", "http://x.example/", "must use https"),
    ("sources.S1.url", "https://u:p@x.example/", "must not contain user information"),
    ("sources.S1.url", "https://x.example/?token=1", "must not carry a token, key, signature, password or secret parameter"),
    ("stacks.0.commands.test.source", "S9", "refers to a source that is not defined"),
    ("stacks.1.id", "python", "is a duplicate stack id"),
    ("sources.S1.evidence", "a\x1bb", "contains a control character"),
    ("sources.S1.evidence", "a" * 301, "is longer than 300 characters"),
    ("schema", 2, "must be 1"),
])
def test_error_texts_are_fixed(where, value, text):
    errors = stack.validate(put(good(), where, value))
    assert len(errors) == 1 and errors[0]["error"].startswith(text), errors


def test_detected_without_evidence_is_one_error():
    p = good()
    del p["stacks"][0]["detected"]["evidence"]
    assert stack.validate(p) == [{"path": "stacks[0].detected.evidence", "error": "is required"}]


def test_today_is_the_later_of_local_and_utc():
    assert stack._today() == max(datetime.date.today(), datetime.datetime.now(datetime.timezone.utc).date())


# Accepted limit: the query-key check percent-decodes twice, so a triple-encoded key (`%252574oken`) is not caught. The URL is never
# fetched by the CLI and the human reads it in the approval preview; this check is a tripwire against copied links, not a boundary.


def test_future_date_boundary():
    p = good()
    today = max(datetime.date.today(), datetime.datetime.now(datetime.timezone.utc).date())
    p["sources"]["S1"]["retrieved"] = today.isoformat()
    assert stack.validate(p) == []
    p["sources"]["S1"]["retrieved"] = (today + datetime.timedelta(days=1)).isoformat()
    assert paths(p) == ["sources.S1.retrieved"]


def test_missing_required_keys():
    assert sorted(paths({})) == ["schema", "stacks", "status", "version"]
    assert sorted(paths({"schema": 1, "version": 1, "status": "draft", "stacks": [{}]})) == ["stacks[0].id", "stacks[0].root"]
    p = good()
    del p["stacks"][0]["commands"]["test"]["run"]
    assert paths(p) == ["stacks[0].commands.test.run"]
    p = good()
    p["approval"] = {}
    assert sorted(paths(p)) == ["approval.by", "approval.date", "approval.sha256"]


def test_null_test_run_with_why_is_valid():
    p = minimal()
    p["stacks"][0]["commands"]["test"] = {"run": None, "why": "Apex tests need a live org"}
    assert stack.validate(p) == []


def test_non_object_top_level():
    for v in ([], "x", 3, None, True):
        assert stack.validate(v) == [{"path": "profile", "error": "must be an object"}]


def test_error_count_is_capped():
    p = minimal()
    p["stacks"] = [{"id": "a%d" % i, "root": ".", "conventions": [{"rule": 1, "source": 2}] * 50} for i in range(8)]
    errors = stack.validate(p)
    assert len(errors) == stack.MAX_ERRORS + 1
    assert errors[-1] == {"path": "profile", "error": "further errors omitted"}


def _fill(node, marker):
    """Every string value becomes a marked, invalid-looking value; every object gains a marked key."""
    if isinstance(node, str):
        return marker + "\n"
    if isinstance(node, dict):
        out = {k: _fill(v, marker) for k, v in node.items()}
        out[marker] = marker
        return out
    if isinstance(node, list):
        return [_fill(v, marker) for v in node]
    return node


def test_error_never_echoes_value():
    variants = [_fill(good(), MARKER), _fill(minimal(), MARKER)]
    renamed = good()
    renamed["sources"] = {MARKER: v for v in renamed["sources"].values()}
    variants.append(renamed)
    variants.append(put(good(), "sources.S1.url", f"https://{MARKER}:{MARKER}@docs.example/?token={MARKER}#{MARKER}"))
    variants.append(put(good(), "stacks.0.root", MARKER + "/../x"))
    variants.append(put(good(), "stacks.0.commands.lint.run", f"sudo {MARKER}\n{MARKER}"))
    variants.append(put(good(), "stacks.0.id", MARKER))
    variants.append(put(good(), "stacks.0.commands.test.source", MARKER))
    variants.append(put(good(), "status", MARKER))
    variants.append(put(good(), "sources.S1.kind", MARKER))
    variants.append(put(good(), "sources.S1.retrieved", MARKER))
    variants.append(put(good(), "approval.sha256", MARKER))
    variants.append(put(good(), "stacks.0.kit", MARKER))
    variants.append(put(good(), "stacks.0.detected.confidence", MARKER))
    for v in variants:
        errors = stack.validate(v)
        assert errors, v
        blob = json.dumps(errors)
        assert MARKER not in blob and MARKER.lower() not in blob.lower()
        for e in errors:  # positions come from fixed key names, indexes and source ids only
            assert re.fullmatch(r"[A-Za-z0-9_.\[\]<># ]+", e["path"]), e
            assert not re.search(r"[^\x20-\x7e]", e["error"]), e
    # and the same through the bytes parser's errors
    for raw in (b'{"a":1,"a":2}', b"\xff", b"{" + MARKER.encode(), MARKER.encode(), b"[" * 10, b"NaN"):
        with pytest.raises(ValueError) as exc:
            stack.parse_json(raw, 1000)
        assert MARKER not in str(exc.value)


# ---- parse_json

def test_rejects_duplicate_json_key():
    for raw in (b'{"a": 1, "a": 2}', b'{"x": {"a": 1, "a": 2}}', b'{"x": [{"a": 1, "a": 2}]}'):
        with pytest.raises(ValueError, match="duplicate key"):
            stack.parse_json(raw, 1000)


def test_rejects_duplicate_source_id_and_stack_key():
    raw = (json.dumps(good()).replace('"S2": {', '"S1": {', 1)).encode()  # a second "S1" in sources
    with pytest.raises(ValueError, match="duplicate key"):
        stack.parse_json(raw, stack.MAX_PROFILE)
    raw = b'{"schema": 1, "version": 1, "status": "draft", "stacks": [{"id": "a", "id": "b", "root": "."}]}'
    with pytest.raises(ValueError, match="duplicate key"):
        stack.parse_json(raw, stack.MAX_PROFILE)


def test_rejects_oversize_profile():
    cap = stack.MAX_PROFILE
    body = json.dumps({"x": ""})[:-2].encode()
    exact = body + b"a" * (cap - len(body) - 2) + b'"}'
    assert len(exact) == cap and stack.parse_json(exact, cap) == {"x": "a" * (cap - len(body) - 2)}
    with pytest.raises(ValueError, match=str(cap)):
        stack.parse_json(exact + b" ", cap)  # 256 KiB + 1
    with pytest.raises(ValueError, match="larger"):
        stack.parse_json(b"x" * (cap + 1), cap)


@pytest.mark.parametrize("raw,text", [
    (b"\xff\xfe{}", "UTF-8"), (b"\xef\xbb\xbf{}", "valid JSON"), (b"{", "valid JSON"), (b"", "valid JSON"), (b"{'a': 1}", "valid JSON"),
    (b"NaN", "valid JSON"), (b"[Infinity]", "valid JSON"), (b"[-Infinity]", "valid JSON"), (b"[1e400]", "valid JSON"),
    (b'{"a": -1e999}', "valid JSON"), (b"[" * 100000, "valid JSON"), (b'{"a": 1} x', "valid JSON")])
def test_parse_json_fixed_errors(raw, text):
    with pytest.raises(ValueError, match=text):
        stack.parse_json(raw, 1 << 20)


def test_parse_json_accepts_any_json_value_and_bytearray():
    assert stack.parse_json(b' {"a": [1, "b", null]} ', 100) == {"a": [1, "b", None]}
    assert stack.parse_json(bytearray(b"[1]"), 100) == [1]
    with pytest.raises(ValueError, match="bytes"):
        stack.parse_json("{}", 100)


def test_validate_does_not_mutate_its_input():
    p = good()
    before = copy.deepcopy(p)
    stack.validate(p)
    assert p == before
