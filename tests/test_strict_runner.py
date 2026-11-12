"""strict.run_at / strict.scrubbed_env: the L2 test runner (D§5). Real temp git repos, tiny `sh` commands."""
import os
import shlex
import signal
import subprocess
import sys
import textwrap
import tempfile
import time

import pytest

from ghp import strict
from conftest import commit, git


@pytest.fixture
def repo(tmp_path, git_env):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    commit(r, "one", {"f.txt": "v1\n"})
    return r


@pytest.fixture
def made(monkeypatch):
    """The temp dirs run_at creates."""
    dirs, real = [], tempfile.mkdtemp

    def spy(*a, **k):
        dirs.append(real(*a, **k))
        return dirs[-1]
    monkeypatch.setattr(strict.tempfile, "mkdtemp", spy)
    return dirs


def _dead(pid):
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    pytest.fail("the process survived")


def _gone(repo, made):
    assert made and not any(os.path.exists(d) for d in made)
    assert "ghp-strict-" not in git(repo, "worktree", "list", "--porcelain")
    assert git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_runs_command_at_the_sha_not_head(repo):
    one = git(repo, "rev-parse", "HEAD")
    commit(repo, "two", {"f.txt": "v2\n"})
    assert strict.run_at(str(repo), one[:8], "grep -q v1 f.txt", 30)["exit"] == 0  # HEAD would say 1
    assert strict.run_at(str(repo), git(repo, "rev-parse", "--short", "HEAD"), "grep -q v1 f.txt", 30)["exit"] == 1


def test_result_shape_and_no_output_returned(repo):
    res = strict.run_at(str(repo), git(repo, "rev-parse", "HEAD"), "echo SECRET-LINE; echo more >&2; echo x", 30)
    assert set(res) == {"sha", "exit", "timed_out", "seconds", "stdout_lines", "stderr_lines"}
    assert res["sha"] == git(repo, "rev-parse", "HEAD") and res["exit"] == 0 and res["timed_out"] is False
    assert (res["stdout_lines"], res["stderr_lines"]) == (2, 1) and "SECRET-LINE" not in repr(res)


def test_nonzero_exit_reported(repo):
    res = strict.run_at(str(repo), git(repo, "rev-parse", "HEAD"), "exit 7", 30)
    assert res["exit"] == 7 and res["timed_out"] is False


def test_timeout_kills_the_whole_group_and_reports(repo, tmp_path):
    pidfile = tmp_path / "pid"
    t0 = time.monotonic()
    res = strict.run_at(str(repo), git(repo, "rev-parse", "HEAD"), f"sleep 60 & echo $! > {shlex.quote(str(pidfile))}; wait", 1)
    assert res["timed_out"] is True and res["exit"] is None and time.monotonic() - t0 < 20
    _dead(int(pidfile.read_text()))  # the background sleep belongs to the killed group


@pytest.mark.parametrize("command,timeout", [("true", 30), ("exit 3", 30), ("sleep 60", 1)])
def test_worktree_removed_on_success_failure_and_timeout(repo, made, command, timeout):
    strict.run_at(str(repo), git(repo, "rev-parse", "HEAD"), command, timeout)
    _gone(repo, made)


# A process started in the background of a non-interactive shell (CI, `cmd &`) inherits SIGINT as IGNORED, and Python then installs no handler: the
# child restores Python's own, so the test does not depend on how pytest was launched (that was the whole flake).
_CHILD = ("import signal, sys; signal.signal(signal.SIGINT, signal.default_int_handler); sys.path.insert(0, sys.argv[1]); from ghp import strict\n"
          "strict.run_at(sys.argv[2], sys.argv[3], 'echo $$ > ' + sys.argv[4] + '; sleep 60 & wait', 30)")


def test_checkout_removed_and_group_killed_on_a_real_sigint(repo, tmp_path):
    """A real SIGINT, sent to a CHILD python (never to the pytest process, no threads): no timing between a signal and the test's own frames."""
    head, pidfile, tmp = git(repo, "rev-parse", "HEAD"), tmp_path / "pid", tmp_path / "tmp"
    tmp.mkdir()
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    child = subprocess.Popen([sys.executable, "-c", _CHILD, src, str(repo), head, str(pidfile)], cwd=tmp_path, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, env={**os.environ, "TMPDIR": str(tmp)})  # its temp dirs are exactly the ones under `tmp`
    try:
        deadline = time.monotonic() + 90  # SIGINT only once the shell has written its pid, however slow the clone is
        while time.monotonic() < deadline and child.poll() is None and not (pidfile.exists() and pidfile.read_text().strip()):
            time.sleep(0.05)
        assert child.poll() is None and pidfile.exists() and pidfile.read_text().strip(), "the command never started"
        assert [d for d in os.listdir(tmp) if d.startswith("ghp-strict-")]  # the checkout exists while the command runs
        child.send_signal(signal.SIGINT)
        _, err = child.communicate(timeout=60)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate()
    assert child.returncode != 0 and "KeyboardInterrupt" in err, (child.returncode, err)
    assert os.listdir(tmp) == []  # the temp dir is gone
    assert git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1
    _dead(int(pidfile.read_text()))


def test_runs_in_a_temp_checkout_outside_the_repo(repo, tmp_path):
    out = tmp_path / "cwd"
    strict.run_at(str(repo), git(repo, "rev-parse", "HEAD"), f"pwd -P > {shlex.quote(str(out))}", 30)
    cwd = out.read_text().strip()
    assert os.path.basename(os.path.dirname(cwd)).startswith("ghp-strict-")
    assert not os.path.realpath(cwd).startswith(os.path.realpath(str(repo)) + os.sep)


def test_env_is_scrubbed_of_tokens(repo, tmp_path, monkeypatch):
    for k, v in {"GHP_TOKEN": "a", "GH_TOKEN": "b", "FOO_TOKEN": "c", "MY_SECRET": "d", "AZURE_DEVOPS_EXT_PAT": "e",
                 "SYSTEM_ACCESSTOKEN": "f", "AWS_REGION": "g", "SSH_AUTH_SOCK": "/s", "GIT_SSH_COMMAND": "x", "PYTHONHOME": "/p",
                 "UNLISTED_THING": "h"}.items():
        monkeypatch.setenv(k, v)
    out = tmp_path / "env.txt"
    assert strict.run_at(str(repo), git(repo, "rev-parse", "HEAD"), f"env > {shlex.quote(str(out))}", 30)["exit"] == 0
    names = {line.split("=", 1)[0] for line in out.read_text().splitlines() if "=" in line}
    assert "PATH" in names and "HOME" in names
    for bad in ("GHP_TOKEN", "GH_TOKEN", "FOO_TOKEN", "MY_SECRET", "AZURE_DEVOPS_EXT_PAT", "SYSTEM_ACCESSTOKEN", "AWS_REGION",
                "SSH_AUTH_SOCK", "GIT_SSH_COMMAND", "PYTHONHOME", "UNLISTED_THING"):
        assert bad not in names, bad
    assert os.path.join(os.path.expanduser("~")) not in out.read_text().replace(os.environ["PATH"], "")


def test_scrubbed_env_allow_list_then_deny_list():
    env = {"PATH": "/bin", "HOME": "/real/home", "LANG": "C", "LC_ALL": "C", "TERM": "x", "TMPDIR": "/t", "VIRTUAL_ENV": "/v",
           "JAVA_HOME": "/j", "PYTHONHOME": "/p", "PYTHONPATH": "/pp", "GHP_TOKEN": "t", "LC_MY_KEY": "k", "X_CREDENTIALS": "c",
           "GITHUB_TOKEN": "g", "AWS_PROFILE": "p", "OTHER": "o"}
    out = strict.scrubbed_env(env, "/fresh")
    assert out == {"PATH": "/bin", "HOME": "/fresh", "LANG": "C", "LC_ALL": "C", "TERM": "x", "TMPDIR": "/t", "VIRTUAL_ENV": "/v",
                   "JAVA_HOME": "/j", "PYTHONDONTWRITEBYTECODE": "1"}
    assert "HOME" in strict.scrubbed_env({}) and strict.scrubbed_env({})["HOME"] != "/real/home"


_SDK = {"GOROOT": "/sdk/go", "FLUTTER_ROOT": "/sdk/flutter", "DOTNET_ROOT": "/sdk/dotnet", "ANDROID_HOME": "/sdk/android",
        "ANDROID_SDK_ROOT": "/sdk/android", "RUSTUP_HOME": "/sdk/rustup"}


def test_cargo_home_gradle_pub_cache_not_passed(repo, tmp_path, monkeypatch):
    for k in ("CARGO_HOME", "GRADLE_USER_HOME", "PUB_CACHE"):
        monkeypatch.setenv(k, "/home/dana/secret-dir")
        assert k not in strict.scrubbed_env({k: "/home/dana/secret-dir"})
    out = tmp_path / "env.txt"
    assert strict.run_at(str(repo), git(repo, "rev-parse", "HEAD"), f"env > {shlex.quote(str(out))}", 30)["exit"] == 0
    assert "/home/dana/secret-dir" not in out.read_text()


def test_sf_and_sfdx_vars_not_passed(repo, tmp_path, monkeypatch):
    bad = {"SF_ACCESS_TOKEN": "a", "SF_TARGET_ORG": "o", "SF_USE_GENERIC_UNIX_KEYCHAIN": "true", "SFDX_AUTH_URL": "u",
           "SFDX_DEFAULTUSERNAME": "d", "SALESFORCE_CLI_TOKEN": "t"}
    assert strict.scrubbed_env(bad) == {"HOME": "/nonexistent", "PYTHONDONTWRITEBYTECODE": "1"}
    for k, v in bad.items():
        monkeypatch.setenv(k, v)
    out = tmp_path / "env.txt"
    assert strict.run_at(str(repo), git(repo, "rev-parse", "HEAD"), f"env > {shlex.quote(str(out))}", 30)["exit"] == 0
    names = {line.split("=", 1)[0] for line in out.read_text().splitlines() if "=" in line}
    assert not names & set(bad)


def test_sdk_names_passed_when_set(repo, tmp_path, monkeypatch):
    assert strict.scrubbed_env(_SDK) == {**_SDK, "HOME": "/nonexistent", "PYTHONDONTWRITEBYTECODE": "1"}
    assert not set(_SDK) & set(strict.scrubbed_env({}))  # only when set
    for k, v in _SDK.items():
        monkeypatch.setenv(k, v)
    out = tmp_path / "env.txt"
    assert strict.run_at(str(repo), git(repo, "rev-parse", "HEAD"), f"env > {shlex.quote(str(out))}", 30)["exit"] == 0
    seen = dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line)
    assert {k: seen.get(k) for k in _SDK} == _SDK


@pytest.mark.parametrize("value", ["relative/sdk", "", ".", "~/sdk", "/sdk/go\nPATH=/evil", "/sdk\x00/go", "/sdk/\x1b[31m", "/sdk/go\u2028x", "/sdk/\x7f"])
def test_sdk_path_value_must_be_absolute_without_control_chars(value):
    for k in _SDK:
        assert k not in strict.scrubbed_env({k: value})
    assert strict.scrubbed_env({"GOROOT": "/ok/go"})["GOROOT"] == "/ok/go"


def test_home_is_temp(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", os.path.expanduser("~"))
    out = tmp_path / "home.txt"
    strict.run_at(str(repo), git(repo, "rev-parse", "HEAD"), f"printf %s \"$HOME\" > {shlex.quote(str(out))}", 30)
    home = out.read_text()
    assert home != os.environ["HOME"] and os.path.basename(os.path.dirname(home)).startswith("ghp-strict-")
    assert strict._git_env({"HOME": "/real"}, "/fresh")["HOME"] == "/fresh"


def test_deny_pass_still_drops_secret_suffixes_of_allowed_names(monkeypatch):
    monkeypatch.setattr(strict, "_ENV_ALLOW", strict._ENV_ALLOW + ("DOTNET_API_KEY", "TOOL_SECRET", "X_PASSWORD", "Y_TOKEN", "GH_TOKEN"))
    env = {"DOTNET_API_KEY": "/k", "TOOL_SECRET": "/s", "X_PASSWORD": "/p", "Y_TOKEN": "/t", "GH_TOKEN": "/g", "GOROOT": "/ok"}
    assert strict.scrubbed_env(env) == {"GOROOT": "/ok", "HOME": "/nonexistent", "PYTHONDONTWRITEBYTECODE": "1"}


def test_unknown_sha_error(repo, made):
    for bad in ("deadbeef", "0" * 40, "zzzzzzz", "abc", "--help", "main", "HEAD", ""):
        with pytest.raises(ValueError) as e:
            strict.run_at(str(repo), bad, "true", 30)
        assert bad not in str(e.value) or bad == ""
    assert not made  # nothing was created before the SHA was proven


def test_a_blob_or_tree_is_not_a_commit(repo):
    tree = git(repo, "rev-parse", "HEAD^{tree}")
    with pytest.raises(ValueError):
        strict.run_at(str(repo), tree, "true", 30)


def test_a_relative_write_in_the_checkout_does_not_touch_the_original_tree(repo):
    (repo / "f.txt").write_text("dirty\n")  # an uncommitted change
    (repo / "untracked.txt").write_text("u\n")
    before = git(repo, "status", "--porcelain=v1", "-uall")
    head = git(repo, "rev-parse", "HEAD")
    res = strict.run_at(str(repo), head, "echo hi > created.txt; echo x >> f.txt; test ! -e untracked.txt; grep -q v1 f.txt", 30)
    assert res["exit"] == 0  # a clean checkout of the commit: no dirty or untracked file leaks in
    assert git(repo, "status", "--porcelain=v1", "-uall") == before and git(repo, "rev-parse", "HEAD") == head
    assert not (repo / "created.txt").exists() and (repo / "f.txt").read_text() == "dirty\n"


def test_worktree_add_failure_is_a_fixed_error(repo, made, monkeypatch):
    real = strict._git

    def fail(r, *args, **kw):
        if "clone" in args:
            return subprocess.CompletedProcess(args, 128, b"", b"fatal: SECRET-PATH /x/y")
        return real(r, *args, **kw)
    monkeypatch.setattr(strict, "_git", fail)
    with pytest.raises(ValueError) as e:
        strict.run_at(str(repo), git(repo, "rev-parse", "HEAD"), "true", 30)
    assert "SECRET-PATH" not in str(e.value) and str(made[0]) not in str(e.value)
    assert made and not os.path.exists(made[0])


def test_temp_dir_inside_the_repo_is_refused(repo, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(repo))
    with pytest.raises(ValueError):
        strict.run_at(str(repo), git(repo, "rev-parse", "HEAD"), "true", 30)
    assert not any(p.name.startswith("ghp-strict-") for p in repo.iterdir())


def test_run_configured_uses_the_config_command_and_timeout(repo, cfg):
    cfg.strict = {"test_command": "exit 5", "test_timeout": 9}
    assert strict.run_configured(cfg, str(repo), git(repo, "rev-parse", "HEAD"))["exit"] == 5
    cfg.strict = {"test_command": None, "test_timeout": 9}
    with pytest.raises(ValueError):
        strict.run_configured(cfg, str(repo), git(repo, "rev-parse", "HEAD"))


def test_repo_hooks_do_not_run_during_checkout(repo, tmp_path):
    hook, marker = repo / ".git" / "hooks" / "post-checkout", tmp_path / "hook-ran"
    hook.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\n")
    hook.chmod(0o755)
    assert strict.run_at(str(repo), git(repo, "rev-parse", "HEAD"), "true", 30)["exit"] == 0
    assert not marker.exists()


def test_a_filter_planted_by_one_run_never_runs_in_the_next_checkout(repo, tmp_path, monkeypatch):
    """The checkout is a throwaway clone: a config the red run writes is gone, and the user's config is never touched."""
    commit(repo, "attrs", {".gitattributes": "f.txt filter=ev\n"})
    red = git(repo, "rev-parse", "HEAD")
    green = commit(repo, "green", {"f.txt": "v3\n"})
    leak = tmp_path / "leak.txt"
    monkeypatch.setenv("GHP_TOKEN", "ZQCHAIN")
    plant = f"git config filter.ev.smudge \"sh -c 'echo \\$GHP_TOKEN >> {leak}; cat'\"; git config filter.ev.required false"
    strict.run_at(str(repo), red, plant + "; exit 1", 30)
    assert strict.run_at(str(repo), green, "true", 30)["exit"] == 0
    assert not leak.exists()
    assert subprocess.run(["git", "-C", str(repo), "config", "--get", "filter.ev.smudge"], capture_output=True).returncode == 1


@pytest.mark.parametrize("var", ["GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE"])
def test_parent_git_env_is_ignored_and_the_users_index_untouched(repo, monkeypatch, var):
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "rm", "-q", "--cached", "f.txt")  # staged deletion
    (repo / "new.txt").write_text("n\n")
    git(repo, "add", "new.txt")
    idx, status = (repo / ".git" / "index").read_bytes(), git(repo, "status", "--porcelain=v1")
    monkeypatch.setenv(var, {"GIT_INDEX_FILE": str(repo / ".git" / "index"), "GIT_DIR": str(repo / ".git"),
                             "GIT_WORK_TREE": str(repo)}[var])
    assert strict.run_at(str(repo), head, "test -f f.txt && test ! -e new.txt", 30)["exit"] == 0
    monkeypatch.delenv(var)
    assert (repo / ".git" / "index").read_bytes() == idx and git(repo, "status", "--porcelain=v1") == status


def test_the_clone_carries_no_credentials_or_remote_from_the_users_config(repo, tmp_path):
    git(repo, "config", "http.https://example.invalid/.extraheader", "AUTHORIZATION: basic ZZTOKEN")
    git(repo, "config", "credential.helper", "store --file=ZZHELPER")
    out = tmp_path / "cfg"
    res = strict.run_at(str(repo), git(repo, "rev-parse", "HEAD"), f"cat .git/config > {shlex.quote(str(out))}; git remote >> {shlex.quote(str(out))}", 30)
    assert res["exit"] == 0
    text = out.read_text()
    assert "[core]" in text and "ZZTOKEN" not in text and "ZZHELPER" not in text and "origin" not in text and "extraheader" not in text


def test_the_command_env_has_no_parent_git_variables(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_ASKPASS", "/x")
    monkeypatch.setenv("GIT_SSH_COMMAND", "x")
    out = tmp_path / "env"
    strict.run_at(str(repo), git(repo, "rev-parse", "HEAD"), f"env > {shlex.quote(str(out))}", 30)
    env = dict(l.split("=", 1) for l in out.read_text().splitlines() if "=" in l)
    assert "GIT_ASKPASS" not in env and "GIT_SSH_COMMAND" not in env
    assert env.get("GIT_TERMINAL_PROMPT") == "0" and env.get("GIT_CONFIG_NOSYSTEM") == "1" and env.get("GIT_CONFIG_GLOBAL") == os.devnull


def test_output_is_counted_not_buffered(repo, tmp_path):
    """100 MB of output in ~52M lines: the old implementation held it all (hundreds of MB to GB of RSS)."""
    script = tmp_path / "mem.py"
    script.write_text(textwrap.dedent(f"""
        import resource, subprocess, sys
        sys.path.insert(0, {str(os.path.join(os.path.dirname(strict.__file__), '..'))!r})
        from ghp import strict
        sha = subprocess.run(["git", "-C", {str(repo)!r}, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        unit = 1 if sys.platform == "darwin" else 1024
        before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit
        res = strict.run_at({str(repo)!r}, sha, "yes | head -c 104857600", 120)
        after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit
        print(res["stdout_lines"], res["exit"], (after - before) // 2**20)
        """))
    r = subprocess.run([sys.executable, "-I", str(script)], capture_output=True, text=True, timeout=300,
                       env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull})
    assert r.returncode == 0, r.stderr[-300:]
    lines, code, grown_mb = (int(x) for x in r.stdout.split())
    assert lines == 52428800 and code == 0 and grown_mb < 50


def test_last_line_without_a_newline_is_counted(repo):
    res = strict.run_at(str(repo), git(repo, "rev-parse", "HEAD"), "printf 'a\\nb'; printf 'c\\n' >&2", 30)
    assert (res["stdout_lines"], res["stderr_lines"]) == (2, 1)


def test_read_only_and_mode_000_dirs_do_not_defeat_cleanup(repo, made):
    cmd = ("mkdir -p $HOME/go/pkg/mod/x/y && echo a > $HOME/go/pkg/mod/x/y/f && chmod -R 555 $HOME/go; "
           "mkdir -p sub/deep && echo b > sub/deep/f && chmod 000 sub/deep && chmod 000 sub; mkdir ro && touch ro/f && chmod 555 ro")
    assert strict.run_at(str(repo), git(repo, "rev-parse", "HEAD"), cmd, 30)["exit"] == 0
    _gone(repo, made)


def test_seconds_stop_at_the_process_exit_not_the_pipe_drain(repo):
    # an escapee in its own session keeps the pipes open for 2 s after the command exited
    cmd = f"{shlex.quote(sys.executable)} -c \"import subprocess; subprocess.Popen(['sleep', '2'], start_new_session=True)\""
    res = strict.run_at(str(repo), git(repo, "rev-parse", "--short", "HEAD"), cmd, 30)
    assert res["exit"] == 0 and res["seconds"] < 1.5


def test_only_standard_lc_names_pass():
    from ghp import strict
    env = {"LC_ALL": "C", "LC_CTYPE": "C", "LC_SFDX_AUTH_URL": "force://x", "LC_MY_DATA": "d", "LC_": "e"}
    out = strict.scrubbed_env(env, home="/h")
    assert out["LC_ALL"] == "C" and out["LC_CTYPE"] == "C"
    assert not [k for k in out if k.startswith("LC_") and k not in ("LC_ALL", "LC_CTYPE")]


def test_checkout_step_has_no_end_of_options_and_takes_a_full_hex_id(repo, monkeypatch):
    """git 2.39 (Debian bookworm) rejects `checkout --detach --end-of-options`; the id is git's own 40-hex output, so none is needed."""
    seen, real = [], strict._git
    monkeypatch.setattr(strict, "_git", lambda where, *a, **kw: (seen.append(a), real(where, *a, **kw))[1])
    strict.run_at(str(repo), git(repo, "rev-parse", "HEAD")[:12], "true", 30)
    co = [a for a in seen if "checkout" in a]
    assert len(co) == 1 and "--end-of-options" not in co[0] and len(co[0][-1]) == 40 and set(co[0][-1]) <= set("0123456789abcdef")
