#!/usr/bin/env python3
"""Write HEAD's tracked files, minus the internal working notes, into a new folder with one fresh commit.

Usage: python3 scripts/export_public.py <out_dir>

<out_dir> must be missing or empty and outside this repository. The commit's author and committer are this repository's own
user.name and user.email (the global git config is never read). It never adds a remote, never pushes and never calls gh:
publishing is the owner's step (docs/release-checklist.md). Exit: 0 done, 2 refused, 3 git failed.
"""
from __future__ import annotations

import fnmatch
import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import List, Optional

INTERNAL = ("SPIKE-*.md", "spike/", "docs/plans/")  # a pattern ending in / is a folder; the others match top-level files only
MESSAGE = "Initial public release candidate\n\nCo-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>\n"
ROOT = Path(__file__).resolve().parent.parent
LOCAL_ONLY = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}  # never the global identity or hooks


def internal(rel: str) -> bool:
    return any(rel.startswith(p) if p.endswith("/") else ("/" not in rel and fnmatch.fnmatch(rel, p)) for p in INTERNAL)


def _git(cwd: Path, *args: str, env: Optional[dict] = None, data: Optional[bytes] = None) -> bytes:
    p = subprocess.run(["git", *args], cwd=cwd, input=data, capture_output=True, env={**os.environ, **LOCAL_ONLY, **(env or {})})
    if p.returncode:
        raise RuntimeError("git %s failed: %s" % (args[0], p.stderr.decode(errors="replace").strip()))
    return p.stdout


def _config(key: str) -> str:
    p = subprocess.run(["git", "config", "--get", key], cwd=ROOT, capture_output=True, text=True, env={**os.environ, **LOCAL_ONLY})
    return p.stdout.strip()


def main(argv: List[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    out = Path(argv[1]).expanduser().resolve()
    if out == ROOT or ROOT in out.parents:
        print("export_public: %s is inside this repository; use a folder outside it" % out, file=sys.stderr)
        return 2
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        print("export_public: %s exists and is not empty" % out, file=sys.stderr)
        return 2
    name, email = _config("user.name"), _config("user.email")
    if not name or not email:
        print("export_public: this repository has no user.name and user.email of its own (git config --local)", file=sys.stderr)
        return 2
    who = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}
    try:
        tar = tarfile.open(fileobj=io.BytesIO(_git(ROOT, "archive", "--format=tar", "HEAD")))
        members = [m for m in tar.getmembers() if not m.isdir() and not internal(m.name)]  # folders come with their files
        out.mkdir(parents=True, exist_ok=True)
        if sys.version_info >= (3, 12):
            tar.extractall(out, members=members, filter="data")
        else:  # our own HEAD from git archive: no absolute paths or links out of the folder
            tar.extractall(out, members=members)
        _git(out, "init", "-q")
        _git(out, "symbolic-ref", "HEAD", "refs/heads/main")
        _git(out, "config", "user.name", name)
        _git(out, "config", "user.email", email)
        _git(out, "add", "-A", "-f")  # -f: a tracked file that the exported .gitignore matches is still exported
        _git(out, "commit", "-q", "--no-verify", "-F", "-", env=who, data=MESSAGE.encode())
    except (RuntimeError, OSError, tarfile.TarError) as e:
        print("export_public: %s" % e, file=sys.stderr)
        return 3
    files = len(_git(out, "ls-files").splitlines())
    print("export_public: %d files, one commit in %s; no remote. Review it, then follow docs/release-checklist.md." % (files, out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
