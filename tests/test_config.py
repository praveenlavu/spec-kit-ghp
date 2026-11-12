import json
import os
import subprocess
from pathlib import Path

import pytest

from ghp import rules
from ghp.config import REVIEW_SEVERITIES, STRICT_GATE_BLOCK, STRICT_REVIEW_BLOCK, Config, ConfigError, ConfigInvalid
from ghp.dod import SECTION2, ac_hash
from ghp.gate import GATE_CHECKS, run_gate, valid_glob  # the one definition of both (config imports them lazily)
from test_dod import _wi

ROOT = Path(__file__).resolve().parent.parent
BASE = {"owner": "example-org", "repo": "example-repo"}
HINT = "use the numeric user id (`gh api users/<login> --jq .id`)"
UNATTENDED_DEFAULT = {"assume_after_hours": None, "approval_requests": False}


# ---- helpers: throwaway repositories, a local identity on every commit, no global git config ----
@pytest.fixture
def git_env(monkeypatch):
    """Isolate git from the user's config (signing, hooks)."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("GHP_CONFIG", raising=False)


def git(repo, *a):
    ident = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false"]
    return subprocess.run(["git", "-C", str(repo), *ident, *a], check=True, capture_output=True, text=True).stdout.strip()


def commit(root, msg, files=None):
    for rel, text in (files or {}).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", msg)
    return git(root, "rev-parse", "HEAD")


def _ref_repo(tmp_path, cfg=json.dumps({**BASE, "mode": "strict"}), name="ghp.config.json"):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "base", {"README.md": "x\n", **({name: cfg} if cfg is not None else {})})
    return tmp_path


def _write(tmp_path, **extra):
    (tmp_path / "ghp.config.json").write_text(json.dumps({**BASE, **extra}))
    return Config.load(start_dir=tmp_path)


_load = _write


# ---- AC1: the ported loader tests (rename table) ----
def test_load_from_cwd_file(tmp_path):
    (tmp_path / "ghp.config.json").write_text(json.dumps(BASE))
    cfg = Config.load(start_dir=tmp_path)
    assert (cfg.owner, cfg.repo) == ("example-org", "example-repo")


def test_env_var_path_wins(tmp_path, monkeypatch):
    env_file = tmp_path / "other.json"
    env_file.write_text(json.dumps({"owner": "envorg", "repo": "r2"}))
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "cwdorg", "repo": "r1"}))
    monkeypatch.setenv("GHP_CONFIG", str(env_file))
    assert Config.load(start_dir=tmp_path).owner == "envorg"


def test_missing_required_key_raises(tmp_path):
    (tmp_path / "ghp.config.json").write_text(json.dumps({"owner": "x"}))
    with pytest.raises(ConfigError, match="repo"):
        Config.load(start_dir=tmp_path)


def test_no_config_anywhere_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("GHP_CONFIG", raising=False)
    with pytest.raises(ConfigError, match="ghp setup"):
        Config.load(start_dir=tmp_path)


def test_malformed_json_raises_config_error(tmp_path):
    (tmp_path / "ghp.config.json").write_text("{not valid json")
    with pytest.raises(ConfigError, match="invalid JSON"):
        Config.load(start_dir=tmp_path)


def test_dod_path_defaults_to_dod(tmp_path):
    assert _write(tmp_path).dod_path == "dod"


def test_dod_path_from_config(tmp_path):
    assert _write(tmp_path, dod_path="docs/dod").dod_path == "docs/dod"


def test_defaults_for_new_keys(tmp_path):
    cfg = _write(tmp_path)
    assert cfg.gate == {"tests": {}, "block": []}
    assert cfg.attachments_allow == [".md", ".svg", ".txt"]
    assert cfg.review_block_on == []
    assert cfg.done_state == "Done"
    assert cfg.repository == ""


def test_custom_done_state(tmp_path):
    assert _write(tmp_path, done_state="Closed").done_state == "Closed"


def test_partial_gate_block_only_gets_tests_default(tmp_path):
    assert _write(tmp_path, gate={"block": ["assertion"]}).gate == {"tests": {}, "block": ["assertion"]}


def test_partial_gate_tests_only_gets_block_default(tmp_path):
    cfg = _write(tmp_path, gate={"tests": {"apex": "y"}})
    assert cfg.gate == {"tests": {"apex": "y"}, "block": []}


def test_string_attachments_allow_is_config_error(tmp_path):
    with pytest.raises(ConfigError, match="attachments_allow"):
        _write(tmp_path, attachments_allow=".md")


def test_non_string_entry_in_attachments_allow_is_config_error(tmp_path):
    with pytest.raises(ConfigError, match="attachments_allow"):
        _write(tmp_path, attachments_allow=[".md", 1])


@pytest.mark.parametrize("key", ["gate", "stack"])
def test_non_object_gate_stack_is_config_error(tmp_path, key):
    with pytest.raises(ConfigError, match=key):
        _write(tmp_path, **{key: None})


def test_top_level_non_object_is_config_error(tmp_path):
    (tmp_path / "ghp.config.json").write_text("[]")
    with pytest.raises(ConfigError, match="ghp.config.json.*object"):
        Config.load(start_dir=tmp_path)


def test_gate_tests_not_object_is_config_error(tmp_path):
    with pytest.raises(ConfigError, match="gate.tests"):
        _write(tmp_path, gate={"tests": []})


@pytest.mark.parametrize("block", ["assertion", [1], None, {"a": 1}])
def test_gate_block_not_list_of_strings_is_config_error(tmp_path, block):
    with pytest.raises(ConfigError, match="gate.block"):
        _write(tmp_path, gate={"block": block})


def test_unreadable_config_is_config_error_naming_file(tmp_path, monkeypatch):
    (tmp_path / "ghp.config.json").write_text(json.dumps(BASE))
    monkeypatch.delenv("GHP_CONFIG", raising=False)

    def deny(self, *a, **k):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(Path, "read_text", deny)
    with pytest.raises(ConfigError, match="ghp.config.json"):
        Config.load(start_dir=tmp_path)


def test_removed_dod_gate_key_is_config_error(tmp_path):
    with pytest.raises(ConfigError, match="dod_gate.*renamed.*gate.block"):
        _write(tmp_path, dod_gate={"block": ["assertion"]})


@pytest.mark.parametrize("tests,entry", [
    ({"*.py": 5}, "#1"), ({"*.py": "("}, "#1"), ({"": "x"}, "#1"), ({"*.py": "x", "a.py": ""}, "#2"),
    ({"*.py": "x" * 501}, "#1"),                     # regex over the 500 cap
    ({"x" * 201: "x"}, "#1"),                        # glob over the 200 cap
    ({"src/[": "x"}, "#1"), ({"[!]": "x"}, "#1"),    # unbalanced class: the matcher would not take it
    ({"[z-a]": "x"}, "#1"),                          # a class the regex engine rejects
])
def test_bad_gate_tests_entry_is_config_error_naming_the_entry_not_the_text(tmp_path, tests, entry):
    with pytest.raises(ConfigInvalid, match="gate.tests' entry " + entry) as e:
        _write(tmp_path, gate={"tests": tests})
    assert not any(k and k in str(e.value) for k in tests if len(k) < 100) and "xxxx" not in str(e.value)


def test_gate_tests_at_the_caps_and_valid_classes_load(tmp_path):
    tests = {"x" * 200: "y" * 500, "**/*.test.[jt]s": "expect", "a[xy]b": "z"}
    assert _write(tmp_path, gate={"tests": tests}).gate["tests"] == tests


@pytest.mark.parametrize("p", [5, "", None, ["d"]])
def test_bad_dod_path_is_config_error(tmp_path, p):
    with pytest.raises(ConfigError, match="dod_path"):
        _write(tmp_path, dod_path=p)


def test_removed_state(tmp_path):
    assert _write(tmp_path).removed_state == "Removed"
    assert _write(tmp_path, removed_state="Cut").removed_state == "Cut"


@pytest.mark.parametrize("key", ["removed_state", "done_state", "status_todo", "status_active", "status_done"])
@pytest.mark.parametrize("bad", ["", 3, None])
def test_state_names_must_be_nonempty_strings(tmp_path, key, bad):
    with pytest.raises(ConfigError, match=key):
        _write(tmp_path, **{key: bad})


def test_review_block_on_loaded(tmp_path):
    assert _write(tmp_path, review_block_on=["blocker", "major"]).review_block_on == ["blocker", "major"]


@pytest.mark.parametrize("bad", ["blocker", ["blocker", 1], {"a": 1}, None])
def test_review_block_on_must_be_list_of_strings(tmp_path, bad):
    with pytest.raises(ConfigError, match="review_block_on"):
        _write(tmp_path, review_block_on=bad)


@pytest.mark.parametrize("name", ["ac_traceability", "assertion", "ac_drift", "open_questions",
                                  "commits_without_story", "open_decisions", "rule_coverage", "rule_drift"])
def test_known_gate_block_name_accepted(tmp_path, name):
    assert _write(tmp_path, gate={"block": [name]}).gate["block"] == [name]


def test_unknown_gate_block_name_is_config_error_listing_valid_set(tmp_path):
    with pytest.raises(ConfigError) as e:
        _write(tmp_path, gate={"block": ["assertion", "bogus_check"]})
    assert "gate.block[1]: unknown check (valid: " in str(e.value) and "bogus_check" not in str(e.value)
    assert "(valid: " + ", ".join(GATE_CHECKS) + ")" in str(e.value)
    assert "open_decisions, rule_coverage, rule_drift)" in str(e.value)


def test_gate_block_names_are_case_sensitive(tmp_path):
    with pytest.raises(ConfigError, match=r"gate\.block\[0\]: unknown check") as e:
        _write(tmp_path, gate={"block": ["Assertion"]})
    assert "Assertion" not in str(e.value).replace("(valid: ", "").split("unknown check")[0]


@pytest.mark.parametrize("name", ["blocker", "major", "minor"])
def test_known_review_block_on_severity_accepted(tmp_path, name):
    assert _write(tmp_path, review_block_on=[name]).review_block_on == [name]


@pytest.mark.parametrize("bad", ["critical", "Blocker", ""])
def test_unknown_review_block_on_severity_is_config_error_listing_valid_set(tmp_path, bad):
    with pytest.raises(ConfigError) as e:
        _write(tmp_path, review_block_on=["major", bad])
    assert "review_block_on[1]: unknown severity (valid: " in str(e.value)
    assert not bad or bad not in str(e.value)
    assert "(valid: blocker, major, minor)" in str(e.value)
    assert REVIEW_SEVERITIES == ("blocker", "major", "minor")


# ---- AC1 (new keys): owner, repo, project_owner, project_number, status names ----
@pytest.mark.parametrize("owner", ["../x", ".hidden", "a/b", "a b", "a?b", "-x", "a" * 40, "", 5, ["x"], "_x", "a.b"])
def test_bad_owner_is_config_error(tmp_path, owner):
    with pytest.raises(ConfigError, match="owner"):
        _write(tmp_path, owner=owner)


@pytest.mark.parametrize("owner", ["example-org", "Fab-rikam1", "a", "a" * 39])
def test_valid_owner_accepted(tmp_path, owner):
    assert _write(tmp_path, owner=owner).owner == owner


@pytest.mark.parametrize("repo", ["../x", "a/b", "a b", "a?b", ".", "..", "a" * 101, 5, ["x"]])
def test_bad_repo_is_config_error(tmp_path, repo):
    with pytest.raises(ConfigError, match="'repo'"):
        _write(tmp_path, repo=repo)


@pytest.mark.parametrize("repo", ["example-repo", "my_repo.v2", ".github", "a" * 100])
def test_valid_repo_accepted(tmp_path, repo):
    assert _write(tmp_path, repo=repo).repo == repo


def test_owner_trailing_newline_is_config_error(tmp_path):
    with pytest.raises(ConfigError, match="owner"):
        _write(tmp_path, owner="example-org\n")


def test_new_key_defaults(tmp_path):
    cfg = _write(tmp_path)
    assert (cfg.project_owner, cfg.project_owner_type, cfg.project_number) == ("example-org", "user", None)
    assert (cfg.status_todo, cfg.status_active, cfg.status_done) == ("Todo", "In Progress", "Done")


def test_new_keys_from_config(tmp_path):
    cfg = _write(tmp_path, project_owner="someone", project_number=7, status_todo="Backlog", status_active="Doing", status_done="Shipped")
    assert (cfg.project_owner, cfg.project_number) == ("someone", 7)
    assert (cfg.status_todo, cfg.status_active, cfg.status_done) == ("Backlog", "Doing", "Shipped")


@pytest.mark.parametrize("bad", [0, -1, "3", 1.5, True, None, [1]])
def test_project_number_must_be_a_positive_integer(tmp_path, bad):
    with pytest.raises(ConfigInvalid, match="'project_number' must be a positive integer"):
        _write(tmp_path, project_number=bad)


@pytest.mark.parametrize("bad", ["a/b", "", 5, "a b"])
def test_bad_project_owner_is_config_error(tmp_path, bad):
    with pytest.raises(ConfigInvalid, match="project_owner"):
        _write(tmp_path, project_owner=bad)


# ---- the ado keys that ghp dropped are refused by name; no token setting is accepted ----
@pytest.mark.parametrize("key", ["organization", "project", "api_version", "story_type", "task_type", "repo_host"])
def test_dropped_ado_key_is_refused_by_name(tmp_path, key):
    with pytest.raises(ConfigInvalid, match=f"'{key}' is not a ghp key"):
        _write(tmp_path, **{key: "x"})


@pytest.mark.parametrize("key", ["token", "GH_TOKEN", "github_token", "Api-Token", "pat_token_file"])
def test_token_settings_are_refused_without_echoing_the_key(tmp_path, key):
    with pytest.raises(ConfigInvalid, match="token settings are not supported") as e:
        _write(tmp_path, **{key: "SECRETVALUE"})
    assert f"'{key}'" not in str(e.value) and "SECRETVALUE" not in str(e.value)


def test_config_module_never_reads_a_token():
    src = (ROOT / "src" / "ghp" / "config.py").read_text()
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        assert name not in src


# ---- AC5: an organization-owned Project is refused (D8) ----
def test_organization_project_owner_is_refused(tmp_path):
    for bad in ("organization", "org", "Organization", "team", "", None, True):
        with pytest.raises(ConfigInvalid, match="E_ORG_PROJECT_UNSUPPORTED") as e:
            _write(tmp_path, project_owner="example-org", project_owner_type=bad)
        assert 'must be "user"' in str(e.value)
    assert _write(tmp_path, project_owner="example-org", project_owner_type="user").project_owner_type == "user"


def test_organization_project_owner_is_refused_from_the_target_branch_too(tmp_path, git_env):
    repo = _ref_repo(tmp_path, json.dumps({**BASE, "project_owner_type": "organization"}))
    with pytest.raises(ConfigInvalid, match="E_ORG_PROJECT_UNSUPPORTED"):
        Config.load_from_ref("refs/heads/main", repo=str(repo))


# ---- F31: control characters are rejected in every string of the (PR-supplied) config ----
BAD_CHARS = {"newline": "\n", "nul": "\x00", "esc": "\x1b", "ls": " ", "ps": " ", "zwsp": "​",
             "del": "\x7f", "c1": "\x85", "private": "", "rlo": "‮", "bidi-isolate": "⁦",
             "bom": "﻿", "soft-hyphen": "­", "tag": "\U000e0041", "surrogate": "\ud800"}
# (config-with-a-hostile-hole, key path in the message); `{}` is replaced by "a<bad>b"
HOLES = [
    ({"repo": "{}"}, "repo"),
    ({"project_owner": "{}"}, "project_owner"),
    ({"project_owner_type": "{}"}, "project_owner_type"),
    ({"status_todo": "{}"}, "status_todo"),
    ({"status_active": "{}"}, "status_active"),
    ({"status_done": "{}"}, "status_done"),
    ({"repository": "{}"}, "repository"),
    ({"dod_path": "{}"}, "dod_path"),
    ({"done_state": "{}"}, "done_state"),
    ({"removed_state": "{}"}, "removed_state"),
    ({"branch_prefix": "{}"}, "branch_prefix"),
    ({"attachments_allow": [".md", "{}"]}, "attachments_allow[1]"),
    ({"stack": {"phases": {"{}": "x"}}}, "stack.phases"),
    ({"stack": {"phases": {"plan": "{}"}}}, "stack.phases.<entry #1>"),
    ({"gate": {"tests": {"{}": "x"}}}, "gate.tests"),
    ({"gate": {"tests": {"src/**": "{}"}}}, "gate.tests.<entry #1>"),
    ({"{}": 1}, "top level"),
]


def _fill(node, bad):
    if isinstance(node, str):
        return node.replace("{}", "a" + bad + "b")
    if isinstance(node, dict):
        return {_fill(k, bad): _fill(v, bad) for k, v in node.items()}
    if isinstance(node, list):
        return [_fill(v, bad) for v in node]
    return node


@pytest.mark.parametrize("name", BAD_CHARS)
@pytest.mark.parametrize("hole,where", HOLES)
def test_control_character_in_any_config_string_is_rejected(tmp_path, hole, where, name):
    bad = BAD_CHARS[name]
    (tmp_path / "ghp.config.json").write_text(json.dumps({**BASE, **_fill(hole, bad)}))
    with pytest.raises(ConfigInvalid) as e:
        Config.load(start_dir=tmp_path)
    msg = str(e.value)
    assert "control character" in msg and where in msg, msg
    assert "a" + bad + "b" not in msg and bad not in msg  # never echoes the offending value
    assert "src/**" not in msg and "phases.plan" not in msg  # nor a user-chosen key


def test_control_character_is_rejected_in_required_keys_and_nested_lists(tmp_path):
    for cfg in ({"owner": "exam\nple", "repo": "r"},
                {**BASE, "gate": {"block": ["x\n"]}},
                {**BASE, "stack": {"phases": {"plan": ["ok", ["deep\u2028"]]}}}):
        (tmp_path / "ghp.config.json").write_text(json.dumps(cfg))
        with pytest.raises(ConfigInvalid, match="control character"):
            Config.load(start_dir=tmp_path)


GOOD_STRINGS = ["Équipe", "Terminé", "日本語", "می‌خواهم", "क्‍ष", "a‎b‏c", "a b",
                "\U0001F600", "\U0001F468‍\U0001F469‍\U0001F467", "͸", "\U0001FAE8"]  # last two: Cn


@pytest.mark.parametrize("s", GOOD_STRINGS)
def test_ordinary_non_ascii_text_and_unassigned_code_points_stay_valid(tmp_path, s):
    (tmp_path / "ghp.config.json").write_text(json.dumps(
        {**BASE, "status_done": s, "done_state": s, "gate": {"tests": {s + "/**": s}}}, ensure_ascii=False),
        encoding="utf-8")
    cfg = Config.load(start_dir=tmp_path)
    assert cfg.status_done == s and cfg.done_state == s


# ---- mode, strict and the effective lists ----
def test_mode_defaults_to_advisory(tmp_path):
    cfg = _load(tmp_path)
    assert cfg.mode == "advisory"
    assert cfg.strict == {"test_command": None, "test_timeout": 300}


def test_mode_rejects_unknown_value(tmp_path):
    for bad in ("Strict", "off", "", None, True, ["strict"]):
        with pytest.raises(ConfigInvalid, match="'mode'"):
            _load(tmp_path, mode=bad)
    assert _load(tmp_path, mode="strict").mode == "strict"


def test_strict_object_keys_validated(tmp_path):
    cfg = _load(tmp_path, strict={"test_command": "pytest -q", "test_timeout": 60})
    assert cfg.strict == {"test_command": "pytest -q", "test_timeout": 60}
    assert _load(tmp_path, strict={"test_timeout": 3600}).strict == {"test_command": None, "test_timeout": 3600}
    for bad in ([], "x", None, 5):  # strict not an object
        with pytest.raises(ConfigInvalid, match="'strict' must be a JSON object"):
            _load(tmp_path, strict=bad)
    for strict, where in (({"test_command": ""}, "strict.test_command"), ({"test_command": 5}, "strict.test_command"),
                          ({"test_command": "x" * 501}, "strict.test_command"),
                          ({"test_timeout": 0}, "strict.test_timeout"), ({"test_timeout": 3601}, "strict.test_timeout"),
                          ({"test_timeout": "60"}, "strict.test_timeout"), ({"test_timeout": True}, "strict.test_timeout"),
                          ({"test_timeout": 1.5}, "strict.test_timeout")):
        with pytest.raises(ConfigInvalid, match=where.replace(".", r"\.")):
            _load(tmp_path, strict=strict)
    with pytest.raises(ConfigInvalid, match="control character"):
        _load(tmp_path, strict={"test_command": "pytest\n::error::x"})
    with pytest.raises(ConfigInvalid) as e:  # unknown key: named by position, never echoed
        _load(tmp_path, strict={"secret-key-name": 1})
    assert "strict entry #1" in str(e.value) and "secret-key-name" not in str(e.value)


def test_effective_lists_in_strict_union_project_and_defaults(tmp_path):
    cfg = _load(tmp_path, mode="strict", review_block_on=["major"], gate={"block": ["assertion", "ac_drift"]})
    assert cfg.effective_review_block_on() == ["major", "blocker"]
    assert cfg.effective_gate_block() == ["assertion", "ac_drift", "ac_traceability", "open_questions",
                                          "commits_without_story"]
    cfg = _load(tmp_path, mode="strict", review_block_on=["blocker"], gate={"block": list(STRICT_GATE_BLOCK)})
    assert cfg.effective_review_block_on() == ["blocker"] and cfg.effective_gate_block() == list(STRICT_GATE_BLOCK)
    cfg = _load(tmp_path, mode="strict")
    assert cfg.effective_review_block_on() == ["blocker"] and cfg.effective_gate_block() == list(STRICT_GATE_BLOCK)


def test_effective_lists_unchanged_in_advisory(tmp_path):
    cfg = _load(tmp_path, review_block_on=["major"], gate={"block": ["ac_drift"]})
    assert cfg.effective_review_block_on() == ["major"] and cfg.effective_gate_block() == ["ac_drift"]
    cfg = _load(tmp_path, mode="advisory")
    assert cfg.effective_review_block_on() == [] and cfg.effective_gate_block() == []
    cfg.review_block_on.append("minor")  # the returned lists are copies
    assert cfg.effective_review_block_on() == ["minor"]
    cfg.effective_gate_block().append("x")
    assert cfg.effective_gate_block() == []


def test_floor_only_when_enabled_and_strict(tmp_path):
    floor = list(STRICT_GATE_BLOCK)
    assert _load(tmp_path, mode="strict", rules={"enabled": True}).effective_gate_block() == floor + ["rule_coverage", "rule_drift"]
    assert _load(tmp_path, mode="strict", rules={"enabled": False}).effective_gate_block() == floor
    assert _load(tmp_path, mode="strict").effective_gate_block() == floor  # rules absent
    assert _load(tmp_path, mode="advisory", rules={"enabled": True}).effective_gate_block() == []  # advisory reports, never floors
    assert _load(tmp_path, rules={"enabled": True}, gate={"block": ["rule_coverage"]}).effective_gate_block() == ["rule_coverage"]
    both = _load(tmp_path, mode="strict", rules={"enabled": True}, gate={"block": ["rule_coverage", "assertion"]})
    assert both.effective_gate_block() == ["rule_coverage", "assertion", "ac_traceability", "open_questions", "commits_without_story", "rule_drift"]
    assert _load(tmp_path, rules={"enabled": True}, gate={"block": ["rule_drift"]}).effective_gate_block() == ["rule_drift"]


def test_gate_checks_match_run_gate_output_keys():
    """run_gate asks `k in block` for every key of its findings, fired or not, so the sentinel records
    those keys: they must equal GATE_CHECKS. blocked_by (every check fires here) must match too. (T04 dropped it; T22 re-adds it.)"""
    class _Everything:  # True for every key, and remembers each key asked about
        def __init__(self):
            self.asked = set()

        def __contains__(self, key):
            self.asked.add(key)
            return True
    block = _Everything()
    ledger = (f"# DoD — #1001\n- **AC hash:** {ac_hash(_wi('old'))}\n{SECTION2}\n"
              "- [ ] Q1: which database? → _(open)_ · owner: lead\n"
              "## 3. AC → tests (traceability)\n| AC | acceptance criterion | tests | commit/PR | status |\n"
              "|---|---|---|---|---|\n| AC-1 | does X | Gone.testX |  | red |\n## 4. Gate log\n")
    r = run_gate(ledger, {}, {"BTest.cls": "class BTest {}"}, _wi("new"), block=block,
                 commits=(["abc1234"], None), rules_ctx={"files": {}, "drift": rules.DRIFT_OFFLINE},  # an enabled rules context: no `Rules from:` line is a finding
                 decisions_md="- [ ] D1: use paging → _(open)_ · kind: decision · why: unspecified\n")
    assert block.asked == set(GATE_CHECKS)
    assert sorted(r["blocked_by"]) == sorted(GATE_CHECKS)


def test_config_has_one_definition_of_the_checks_and_the_glob_rule():
    import ghp.config as config
    assert not hasattr(config, "GATE_CHECKS") and not hasattr(config, "valid_glob")  # T04 carry-over: no copy left in config
    assert valid_glob("**/test_*.py") and valid_glob("a[!b]c") and not valid_glob("**/[!]") and not valid_glob("[a") and not valid_glob("")
    assert not valid_glob("x" * 201) and not valid_glob("a\nb") and not valid_glob("a\0b")


def test_config_has_the_target_profile_blob_slot():
    assert Config("o", "r").target_profile_blob is None  # strict (T23) fills it; `stack.profile_for` reads it when `from_target`


def test_strict_gate_block_names_are_valid_checks():
    assert set(STRICT_GATE_BLOCK) <= set(GATE_CHECKS)
    assert set(STRICT_REVIEW_BLOCK) <= set(REVIEW_SEVERITIES)


# ---- Config.load_from_ref (the target branch's config, read with git, same validator) ----
def test_load_from_ref_reads_the_file_at_the_ref_not_the_working_tree(tmp_path, git_env, monkeypatch):
    repo = _ref_repo(tmp_path)
    (repo / "ghp.config.json").write_text('{"owner": "other", "repo": "p"}')  # the working tree is the PR's: ignored
    monkeypatch.setenv("GHP_CONFIG", str(repo / "ghp.config.json"))
    cfg = Config.load_from_ref("refs/heads/main", repo=str(repo))
    assert (cfg.owner, cfg.mode) == ("example-org", "strict")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    sha = git(repo, "rev-parse", "HEAD")
    for ref in ("origin/main", "main", sha):  # remote-tracking first, then a local branch, then a full commit id
        assert Config.load_from_ref(ref, repo=str(repo)).mode == "strict", ref


def test_load_from_ref_records_the_resolved_commit(tmp_path, git_env):
    repo = _ref_repo(tmp_path)
    assert Config.load_from_ref("main", repo=str(repo)).source_commit == git(repo, "rev-parse", "HEAD")
    assert Config(owner="example-org", repo="example-repo").source_commit is None


def test_load_from_ref_validates_like_load(tmp_path, git_env):
    bad = ['{"owner": "example-org"}', "[1]", "{not json", '{"owner": "example-org", "repo": "p", "mode": "x"}',
           '{"owner": "example-org", "repo": "p", "gate": {"block": ["nope"]}}',
           '{"owner": "example-org", "repo": "p\\u0007"}']
    for n, text in enumerate(bad):
        repo = tmp_path / str(n)
        repo.mkdir()
        _ref_repo(repo, text)
        with pytest.raises(ConfigInvalid) as e:
            Config.load_from_ref("refs/heads/main", repo=str(repo))
        (repo / "ghp.config.json").write_text(text)  # the same text through Config.load gives the same complaint
        with pytest.raises(ConfigInvalid) as e2:
            Config.load(start_dir=repo)
        assert str(e.value).split(": ", 1)[1] == str(e2.value).split(": ", 1)[1]
        assert str(repo) not in str(e.value) and "\x07" not in str(e.value)


def test_load_from_ref_is_none_when_the_file_does_not_exist_at_the_ref(tmp_path, git_env):
    repo = _ref_repo(tmp_path, cfg=None)
    assert Config.load_from_ref("refs/heads/main", repo=str(repo)) is None
    repo2 = tmp_path / "r2"
    repo2.mkdir()
    _ref_repo(repo2, json.dumps(BASE), name="sub/ghp.config.json")  # only a nested file
    assert Config.load_from_ref("refs/heads/main", repo=str(repo2)) is None


def test_load_from_ref_fails_closed(tmp_path, git_env, monkeypatch):
    repo = _ref_repo(tmp_path)
    git(repo, "tag", "v1")
    git(repo, "tag", "origin/main")  # a tag named like a remote-tracking branch is not the base
    for ref in ("", "nope", "origin/main", "v1", "refs/tags/v1", "--output=x", "-h", "--help", "HEAD", "main:ghp.config.json",
                "refs/heads/nope", "abc123", "x" * 300):
        with pytest.raises(ConfigInvalid) as e:
            Config.load_from_ref(ref, repo=str(repo))
        assert ref not in str(e.value) or ref == ""
    assert not (repo / "x").exists()
    with pytest.raises(ConfigInvalid):  # not a repository at all
        Config.load_from_ref("refs/heads/main", repo=str(tmp_path / "missing"))
    monkeypatch.setenv("PATH", "")  # git cannot run
    with pytest.raises(ConfigInvalid):
        Config.load_from_ref("refs/heads/main", repo=str(repo))


def test_load_from_ref_a_missing_blob_is_a_git_failure_not_an_absent_file(tmp_path, git_env):
    repo = _ref_repo(tmp_path)
    blob = git(repo, "rev-parse", "main:ghp.config.json")
    os.remove(repo / ".git" / "objects" / blob[:2] / blob[2:])
    with pytest.raises(ConfigInvalid):
        Config.load_from_ref("refs/heads/main", repo=str(repo))


def test_load_from_ref_refuses_a_big_file_a_symlink_and_a_directory(tmp_path, git_env):
    pad = ' ' * (1024 * 1024)
    repo = _ref_repo(tmp_path, json.dumps(BASE) + pad)  # 1 MiB + the object
    with pytest.raises(ConfigInvalid):
        Config.load_from_ref("refs/heads/main", repo=str(repo))
    repo2 = tmp_path / "ok"
    repo2.mkdir()
    exact = json.dumps(BASE)
    _ref_repo(repo2, exact + " " * (1024 * 1024 - len(exact)))  # exactly 1 MiB is fine
    assert Config.load_from_ref("refs/heads/main", repo=str(repo2)).owner == "example-org"
    repo3 = tmp_path / "link"
    repo3.mkdir()
    git(repo3, "init", "-q", "-b", "main")
    commit(repo3, "base", {"real.json": json.dumps(BASE)})
    os.symlink("real.json", repo3 / "ghp.config.json")
    commit(repo3, "link")
    with pytest.raises(ConfigInvalid):
        Config.load_from_ref("refs/heads/main", repo=str(repo3))
    repo4 = tmp_path / "dir"
    repo4.mkdir()
    git(repo4, "init", "-q", "-b", "main")
    commit(repo4, "base", {"ghp.config.json/inner": "x"})
    with pytest.raises(ConfigInvalid):
        Config.load_from_ref("refs/heads/main", repo=str(repo4))


def test_load_from_ref_ignores_an_inherited_git_dir(tmp_path, git_env, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    _ref_repo(other, '{"owner": "wrong", "repo": "p"}')
    repo = tmp_path / "repo"
    repo.mkdir()
    _ref_repo(repo)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    assert Config.load_from_ref("refs/heads/main", repo=str(repo)).owner == "example-org"


def test_deeply_nested_config_is_invalid_not_a_crash(tmp_path, git_env):
    """A nested value that the parser or the validator cannot walk is ConfigInvalid, never an uncaught RecursionError (exit 1)."""
    refused = 0
    for depth in (200, 500, 900, 5000, 200000):  # the shallow ones are valid configs (an unknown key is allowed)
        text = '{"owner": "example-org", "repo": "p", "x": ' + "[" * depth + "]" * depth + "}"
        repo = tmp_path / f"d{depth}"
        repo.mkdir()
        _ref_repo(repo, text)
        (repo / "ghp.config.json").write_text(text)
        for load in (lambda: Config.load_from_ref("refs/heads/main", repo=str(repo)), lambda: Config.load(start_dir=repo)):
            try:
                load()
            except ConfigInvalid as e:
                refused += 1
                assert "[[[" not in str(e)
    assert refused >= 4  # at least the 5000 and 200000 levels, through both loaders


# ---- the `rules` object ----
def test_rules_defaults(tmp_path):
    from ghp.config import RULES_DEFAULTS
    assert RULES_DEFAULTS == {"enabled": False, "types": ["Epic", "Feature"]}
    cfg = _load(tmp_path)
    assert cfg.rules == RULES_DEFAULTS and cfg.rules is not RULES_DEFAULTS and cfg.rules["types"] is not RULES_DEFAULTS["types"]


def test_rules_partial_object_keeps_defaults(tmp_path):
    assert _load(tmp_path, rules={"enabled": True}).rules == {"enabled": True, "types": ["Epic", "Feature"]}
    assert _load(tmp_path, rules={"types": ["Initiative"]}).rules == {"enabled": False, "types": ["Initiative"]}
    assert _load(tmp_path, rules={}).rules == {"enabled": False, "types": ["Epic", "Feature"]}


def test_rules_rejects_non_bool_enabled(tmp_path):
    for bad in ("true", 1, 0, None, [True]):
        with pytest.raises(ConfigInvalid, match="'rules.enabled' must be true or false"):
            _load(tmp_path, rules={"enabled": bad})
    for bad in ([], "x", None, 5, True):
        with pytest.raises(ConfigInvalid, match="'rules' must be a JSON object"):
            _load(tmp_path, rules=bad)


def test_rules_types_shape(tmp_path):
    assert _load(tmp_path, rules={"types": ["A"] * 10}).rules["types"] == ["A"] * 10
    assert _load(tmp_path, rules={"types": ["x" * 64]}).rules["types"] == ["x" * 64]
    for bad in ([], ["A"] * 11, "Epic", None, [1], [""], ["x" * 65], ["Epic", None], {"a": "b"}):
        with pytest.raises(ConfigInvalid, match="'rules.types' must be a list of 1-10 non-empty strings of at most 64 characters") as e:
            _load(tmp_path, rules={"types": bad})
        assert "x" * 65 not in str(e.value)


def test_rules_unknown_key_named_by_position(tmp_path):
    with pytest.raises(ConfigInvalid, match=r"rules entry #2: unknown key \(valid: enabled, types\)") as e:
        _load(tmp_path, rules={"enabled": True, "secret-key-name": 1})
    assert "secret-key-name" not in str(e.value)


def test_rules_types_may_not_contain_the_story_type(tmp_path):
    for types in (["Epic", "story"], ["STORY"]):
        with pytest.raises(ConfigInvalid, match=r"rules\.types\[\d\]: a story type cannot carry rules") as e:
            _load(tmp_path, rules={"types": types})
        assert "story" not in str(e.value).replace("a story type", "").lower()


def test_rules_from_target_branch_in_strict(tmp_path, git_env):
    repo = _ref_repo(tmp_path, json.dumps({**BASE, "mode": "strict", "rules": {"enabled": True}}))
    (repo / "ghp.config.json").write_text(json.dumps({**BASE, "mode": "strict", "rules": {"enabled": False}}))  # the PR's head
    assert Config.load(start_dir=repo).rules["enabled"] is False
    assert Config.load_from_ref("refs/heads/main", repo=str(repo)).rules["enabled"] is True


def test_rules_shape_applies_from_target_branch(tmp_path, git_env):
    for n, (rules, msg) in enumerate((({"types": ["story"]}, r"rules\.types\[0\]: a story type cannot carry rules"),
                                      ({"enabled": 1}, "'rules.enabled' must be true or false"),
                                      ({"types": ["Epic", 5]}, "'rules.types' must be a list"))):
        (tmp_path / str(n)).mkdir()
        repo = _ref_repo(tmp_path / str(n), json.dumps({**BASE, "mode": "strict", "rules": rules}))
        with pytest.raises(ConfigInvalid, match=msg):
            Config.load_from_ref("refs/heads/main", repo=str(repo))


# ---- trust keys: defaults and shape ----
def test_trust_keys_default_when_absent(tmp_path):
    cfg = _load(tmp_path)
    assert (cfg.approvers, cfg.approval, cfg.unattended) == ([], "terminal", UNATTENDED_DEFAULT)
    assert _load(tmp_path, unattended={}).unattended == UNATTENDED_DEFAULT and _load(tmp_path, unattended={}).unattended is not UNATTENDED_DEFAULT
    full = _load(tmp_path, approvers=[2250, 77], approval="terminal", unattended={"assume_after_hours": 720, "approval_requests": True})
    assert (full.approvers, full.approval, full.unattended) == ([2250, 77], "terminal", {"assume_after_hours": 720, "approval_requests": True})
    assert len(_load(tmp_path, approvers=list(range(1, 21))).approvers) == 20  # 20 is the cap
    assert _load(tmp_path, unattended={"assume_after_hours": 1}).unattended == {"assume_after_hours": 1, "approval_requests": False}


@pytest.mark.parametrize("extra, where", [
    ({"approvers": "SECRETVALUE"}, "'approvers' must be a list"),
    ({"approvers": {"a": 1}}, "'approvers' must be a list"),
    ({"approvers": list(range(1, 22))}, "'approvers' must be a list of at most 20"),  # 21 entries
    ({"approvers": [7, 1.5]}, "approvers[1]: not a numeric user id"),
    ({"approvers": [7, None]}, "approvers[1]: not a numeric user id"),
    ({"approvers": [0]}, "approvers[0]: not a numeric user id"),
    ({"approvers": [-5]}, "approvers[0]: not a numeric user id"),
    ({"approvers": [7, 8, 7]}, "approvers[2]: duplicate of an earlier entry"),  # duplicates are rejected, not collapsed
    ({"approval": "SECRETVALUE"}, "'approval' must be \"terminal\" or \"comment\""),
    ({"approval": "Comment"}, "'approval' must be"),
    ({"approval": True}, "'approval' must be"),
    ({"approval": None}, "'approval' must be"),
    ({"unattended": "SECRETVALUE"}, "'unattended' must be a JSON object"),
    ({"unattended": [1]}, "'unattended' must be a JSON object"),
    ({"unattended": {"SECRETVALUE": 1}}, "unattended entry #1: unknown key"),
    ({"unattended": {"assume_after_hours": 0}}, "'unattended.assume_after_hours' must be an integer from 1 to 720"),
    ({"unattended": {"assume_after_hours": 721}}, "'unattended.assume_after_hours' must be an integer from 1 to 720"),
    ({"unattended": {"assume_after_hours": True}}, "'unattended.assume_after_hours'"),
    ({"unattended": {"assume_after_hours": "5"}}, "'unattended.assume_after_hours'"),
    ({"unattended": {"assume_after_hours": 1.5}}, "'unattended.assume_after_hours'"),
    ({"unattended": {"assume_after_hours": None}}, "'unattended.assume_after_hours'"),
    ({"unattended": {"approval_requests": 1}}, "'unattended.approval_requests' must be true or false"),
    ({"unattended": {"approval_requests": "yes"}}, "'unattended.approval_requests' must be true or false"),
])
def test_trust_keys_validated(tmp_path, extra, where):
    with pytest.raises(ConfigInvalid) as e:
        _load(tmp_path, **extra)
    assert where in str(e.value)
    assert "SECRETVALUE" not in str(e.value), "a value was echoed"


def test_trust_keys_validated_from_the_target_branch_too(tmp_path, git_env):
    repo = _ref_repo(tmp_path, json.dumps({**BASE, "approvers": [7, "octocat"]}))
    with pytest.raises(ConfigInvalid, match=r"approvers\[1\]: not a numeric user id") as e:
        Config.load_from_ref("refs/heads/main", repo=str(repo))
    assert "octocat" not in str(e.value)


# ---- AC2: approvers are numeric user ids ----
def test_approvers_must_be_integers(tmp_path):
    for bad in (["12345"], [7, "8"], [True], [7, False], [1.0], [[7]], [{"id": 7}]):  # a bool is not an integer
        with pytest.raises(ConfigInvalid, match=r"approvers\[\d\]: not a numeric user id"):
            _load(tmp_path, approvers=bad)
    assert _load(tmp_path, approvers=[7, 8]).approvers == [7, 8]


def test_login_in_approvers_names_the_numeric_id_hint(tmp_path):
    with pytest.raises(ConfigInvalid) as e:
        _load(tmp_path, approvers=[7, "octocat"])
    assert "approvers[1]" in str(e.value) and HINT in str(e.value)
    assert "octocat" not in str(e.value)  # the hint names the path, never the entered text


# ---- T27 AC7: `approval: "comment"` is accepted (the T04-AC4 refusal and E_APPROVAL_UNBUILT are gone) ----
def test_comment_mode_is_accepted(tmp_path, git_env):
    import ghp.config
    assert not hasattr(ghp.config, "E_APPROVAL_UNBUILT")
    assert _load(tmp_path, approval="comment").approval == "comment"
    assert _load(tmp_path, approval="terminal").approval == "terminal"
    from ghp.config import trusted
    (tmp_path / "t").mkdir()
    repo = _ref_repo(tmp_path / "t", json.dumps({**BASE, "approval": "comment"}))
    assert trusted("main", repo=str(repo)).approval == "comment"
    with pytest.raises(ConfigInvalid, match="'approval' must be"):
        _load(tmp_path, approval="Comment")


# ---- AC3: the trust keys come from the target branch only ----
G1, G2 = 2250, 8812


def _trust_repo(tmp_path, target, head=None, origin=True):
    """A repository whose `main` holds `target` as ghp.config.json; the working tree gets `head` (uncommitted) when given."""
    repo = _ref_repo(tmp_path, None if target is None else json.dumps(target))
    if origin:
        git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    if head is not None:
        (repo / "ghp.config.json").write_text(head if isinstance(head, str) else json.dumps(head))
    return repo


def test_trusted_reads_the_target_branch(tmp_path, git_env):
    from ghp.config import trusted
    repo = _trust_repo(tmp_path, {**BASE, "approvers": [G1], "unattended": {"assume_after_hours": 24, "approval_requests": True}})
    t = trusted("main", repo=str(repo))
    assert (t.approvers, t.approval, t.unattended) == ([G1], "terminal", {"assume_after_hours": 24, "approval_requests": True})
    assert trusted("refs/heads/main", repo=str(repo)).approvers == [G1]
    assert trusted("origin/main", repo=str(repo)).approvers == [G1]


def test_trust_keys_ignore_the_working_tree(tmp_path, git_env):
    from ghp.config import trusted
    # the PR (working tree) adds an approver and an unattended window: the target has none of it
    repo = _trust_repo(tmp_path, BASE, {**BASE, "approvers": [G2], "unattended": {"assume_after_hours": 1}})
    t = trusted("main", repo=str(repo))
    assert (t.approvers, t.approval, t.unattended) == ([], "terminal", UNATTENDED_DEFAULT)
    # and the other way round: removing the target's approvers in the PR changes nothing
    (tmp_path / "two").mkdir()
    repo2 = _trust_repo(tmp_path / "two", {**BASE, "approvers": [G1]}, BASE)
    assert trusted("main", repo=str(repo2)).approvers == [G1]
    (repo2 / "ghp.config.json").write_text("{ not json")  # an unparsable working tree is not read either
    assert trusted("main", repo=str(repo2)).approvers == [G1]
    (repo2 / "ghp.config.json").write_text(json.dumps({**BASE, "approvers": [G2]}))  # an edited approvers list changes nothing
    assert trusted("origin/main", repo=str(repo2)).approvers == [G1]


def test_missing_config_on_base_means_no_approvers(tmp_path, git_env):
    from ghp.config import trusted
    repo = _trust_repo(tmp_path, None, {**BASE, "approvers": [G1]})  # the PR adds the config: it is not trusted
    t = trusted("main", repo=str(repo))
    assert (t.approvers, t.approval, t.unattended) == ([], "terminal", UNATTENDED_DEFAULT)


def test_trusted_returns_only_the_three_trust_keys(tmp_path, git_env):
    from ghp.config import Trust, trusted
    t = trusted("main", repo=str(_trust_repo(tmp_path, {**BASE, "approvers": [G1]})))
    assert isinstance(t, Trust) and t._fields == ("approvers", "approval", "unattended") and t.approvers == [G1]
    assert not hasattr(t, "owner") and not hasattr(t, "mode")


def test_trusted_unreadable_target_refuses(tmp_path, git_env):
    from ghp.config import trusted
    repo = _trust_repo(tmp_path, BASE)
    git(repo, "commit", "-q", "--allow-empty", "-m", "second")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")  # origin/main has a reflog entry and a parent now
    for base in ("origin/nope", "refs/heads/nope", "not a ref", "--upload-pack=x", "main@{0}", "origin/main@{1}", "origin/main~0", "origin/main~1",
                 "origin/main^", "origin/main^0", "origin/main^{commit}", "origin/main:ghp.config.json", "main..origin/main", "origin/main\n", " origin/main",
                 "origin/ main", "origin/main\t", "-x", "@{-1}", "HEAD~1", "", None, 5):
        with pytest.raises(ConfigInvalid) as e:
            trusted(base, repo=str(repo))
        assert not isinstance(base, str) or base.strip() not in str(e.value) or base.strip() == "", base  # fixed text: the base is never echoed
    assert trusted("origin/main", repo=str(repo)).approvers == []  # the plain names still work
    assert trusted("refs/remotes/origin/main", repo=str(repo)).approvers == []
    (tmp_path / "bad").mkdir()
    bad = _trust_repo(tmp_path / "bad", {**BASE, "approvers": "SECRETVALUE"})  # invalid at the target
    with pytest.raises(ConfigInvalid) as e:
        trusted("main", repo=str(bad))
    assert "SECRETVALUE" not in str(e.value)
    (tmp_path / "junk").mkdir()
    junk = _trust_repo(tmp_path / "junk", None)
    commit(junk, "junk", {"ghp.config.json": "{ not json"})
    with pytest.raises(ConfigInvalid):
        trusted("main", repo=str(junk))
    with pytest.raises(ConfigInvalid):
        trusted("origin/main", repo=str(tmp_path / "missing-dir"))


# ---- a config file in use that is not the repo-root file must not carry trust keys (ported from ado's trust-minors) ----
def _nonroot(repo, data, monkeypatch, name="config/ghp.json"):
    p = repo / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data))
    monkeypatch.setenv("GHP_CONFIG", str(p))
    return p


@pytest.mark.parametrize("key,val", [("approvers", [G1]), ("approval", "terminal"), ("unattended", {"assume_after_hours": 24})])
def test_a_non_root_config_with_a_trust_key_is_invalid(tmp_path, git_env, monkeypatch, key, val):
    from ghp.config import E_NONROOT_TRUST, trusted
    repo = _trust_repo(tmp_path, {**BASE, "approvers": [G1]})
    _nonroot(repo, {**BASE, key: val}, monkeypatch)
    for base in ("main", "origin/main"):
        with pytest.raises(ConfigInvalid) as e:
            trusted(base, repo=str(repo))
        assert str(e.value) == E_NONROOT_TRUST and "config/ghp.json" not in str(e.value)


def test_a_non_root_config_without_trust_keys_and_the_root_file_keep_working(tmp_path, git_env, monkeypatch):
    from ghp.config import trusted
    repo = _trust_repo(tmp_path, {**BASE, "approvers": [G1]})
    _nonroot(repo, {**BASE, "dod_path": "dod"}, monkeypatch)
    assert trusted("main", repo=str(repo)).approvers == [G1]  # the non-root file is only the rest of the config
    monkeypatch.setenv("GHP_CONFIG", str(repo / "ghp.config.json"))  # the root file itself may carry trust keys (it is read from the target)
    (repo / "ghp.config.json").write_text(json.dumps({**BASE, "approvers": [G2]}))
    assert trusted("main", repo=str(repo)).approvers == [G1]
    monkeypatch.setenv("GHP_CONFIG", str(repo / "config" / ".." / "ghp.config.json"))  # the same file by another spelling
    assert trusted("main", repo=str(repo)).approvers == [G1]


def test_a_subproject_config_with_trust_keys_is_refused(tmp_path, git_env):
    from ghp.config import E_NONROOT_TRUST, trusted
    repo = _trust_repo(tmp_path, BASE)
    svc = repo / "svc"
    svc.mkdir()
    (svc / "ghp.config.json").write_text(json.dumps({**BASE, "approvers": [G1]}))
    with pytest.raises(ConfigInvalid) as e:
        trusted("origin/main", repo=str(svc))
    assert str(e.value) == E_NONROOT_TRUST
    (svc / "ghp.config.json").write_text(json.dumps(BASE))  # without trust keys it works
    assert trusted("origin/main", repo=str(svc)).approvers == []


def test_an_unreadable_non_root_config_is_refused(tmp_path, git_env, monkeypatch):
    from ghp.config import trusted
    repo = _trust_repo(tmp_path, BASE)
    other = repo / "other.json"
    other.write_text("{ not json")
    monkeypatch.setenv("GHP_CONFIG", str(other))
    with pytest.raises(ConfigInvalid, match="the config file cannot be read"):
        trusted("main", repo=str(repo))


# ---- the template ----
def test_template_loads_and_names_no_dropped_key(tmp_path):
    text = (ROOT / "ghp.config.template.json").read_text()
    (tmp_path / "ghp.config.json").write_text(text)
    cfg = Config.load(start_dir=tmp_path)
    assert (cfg.owner, cfg.repo, cfg.project_number, cfg.project_owner_type) == ("example-org", "example-repo", 1, "user")
    assert (cfg.status_todo, cfg.status_active, cfg.status_done) == ("Todo", "In Progress", "Done")
    assert (cfg.approvers, cfg.approval) == ([], "terminal")
    keys = json.loads(text)
    from ghp.config import DROPPED_KEYS
    assert not set(keys) & set(DROPPED_KEYS) and not any("token" in k.lower() for k in keys)


# ---- T23: one git runner (config's) for config, strict, kits and stack; the target's lockfile text is carried like its profile's ----
def test_the_git_helpers_are_defined_once_and_shared():
    from ghp import config, prs, strict
    for name in ("_git", "_out", "_rev", "_commit", "_resolve_base", "_blob_at"):
        assert getattr(strict, name) is getattr(config, name), name
    assert prs.git_env is config.git_env


def test_git_runner_closes_stdin_has_a_deadline_and_an_environment_without_selectors(tmp_path, monkeypatch):
    from ghp import config
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    env = config.git_env()
    assert "GIT_DIR" not in env and "GIT_CONFIG_COUNT" not in env and env["GIT_TERMINAL_PROMPT"] == "0" and env["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert config._git(tmp_path, "--version", timeout=30).returncode == 0
    assert config._git(tmp_path, "hash-object", "--stdin", stdin=b"x\n").stdout.strip() == b"587be6b4c3f93f93c489c0111bba5596147a26cb"
    assert config._git(tmp_path, "hash-object", "--stdin").stdout.strip() == b"e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"  # stdin closed: empty input
    assert config._git(tmp_path, "--version", timeout=0.000001) is None  # past the deadline: a failure, never a pass
    assert config._out(tmp_path, "no-such-subcommand") is None


def test_a_config_starts_with_no_target_blobs():
    assert Config("o", "r").target_lock_blob is None and Config("o", "r").target_profile_blob is None
