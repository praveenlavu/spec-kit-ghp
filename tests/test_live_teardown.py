"""The T31 live test must leave `main` as it found it: a config that was there before comes back, one that was not is removed. Offline, local git."""
import subprocess
from types import SimpleNamespace

import pytest

from live.test_approver_vs_bot import TRUST, WF_PATH, WORKFLOW, _put_main, config_on_main


def _git(*a, cwd=None):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


def _world(tmp_path, config):
    bare, seed, clone = tmp_path / "r.git", tmp_path / "seed", tmp_path / "clone"
    _git("init", "-q", "--bare", "-b", "main", str(bare))
    _git("clone", "-q", str(bare), str(seed))
    (seed / "README.md").write_text("fictional\n")
    if config is not None:
        (seed / "ghp.config.json").write_text(config)
    _git("add", "-A", cwd=seed)
    _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "seed", cwd=seed)
    _git("push", "-q", "origin", "HEAD:main", cwd=seed)
    _git("clone", "-q", str(bare), str(clone))
    return SimpleNamespace(clone=clone)


@pytest.mark.parametrize("config", ['{"approval": "comment", "approvers": [1]}\n', None])
def test_teardown_restores_what_main_held_before(tmp_path, config):
    w = _world(tmp_path, config)
    before = config_on_main(w)
    assert before == config
    _put_main(w, {WF_PATH: WORKFLOW, "ghp.config.json": TRUST})
    assert config_on_main(w) == TRUST
    _put_main(w, {WF_PATH: None, "ghp.config.json": before})
    assert config_on_main(w) == config and not (w.clone / WF_PATH).exists()
