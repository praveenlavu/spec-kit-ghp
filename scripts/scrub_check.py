#!/usr/bin/env python3
"""Scan tracked files (and optionally git history) for denylisted terms and secrets.

Hits print as path:line:kind. The denylist term itself is never printed.
Exit: 0 clean, 1 hits, 2 empty denylist (--require-denylist), 3 git/IO failure.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Mapping, Optional, Tuple

DENYLIST_FILE = ".scrub-denylist"
SECRETS = [
    ("github-token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("github-pat", re.compile(r"github_pat_[A-Za-z0-9_]{22,}")),
    ("ado-pat", re.compile(r"\b[a-z0-9]{52}\b")),
    ("aws-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]


def load_denylist(env: Mapping[str, str], path: Path) -> List[str]:
    raw = env.get("SCRUB_DENYLIST") or (path.read_text() if path.is_file() else "")
    lines = (ln.strip() for ln in raw.splitlines())
    return [ln.lower() for ln in lines if ln and not ln.startswith("#")]


def scan_text(name: str, text: str, denylist: List[str]) -> List[Tuple[str, int, str]]:
    hits = []
    for no, line in enumerate(text.splitlines(), 1):
        low = line.lower()
        for i, term in enumerate(denylist, 1):
            if term in low:
                hits.append((name, no, f"denylist#{i}"))
        for label, pat in SECRETS:
            if pat.search(line):
                hits.append((name, no, f"secret:{label}"))
    return hits


def _git(*args: str) -> bytes:
    p = subprocess.run(["git", *args], capture_output=True)
    if p.returncode:
        raise RuntimeError(f"git {args[0]} failed")  # git's stderr may echo paths; omit it
    return p.stdout


def _scan_history(denylist: List[str]) -> List[Tuple[str, int, str]]:
    # fuller format also emits author/committer names+emails and commit messages
    log = _git("log", "-p", "--all", "--no-decorate", "--format=fuller").decode(errors="replace")
    hits, sha, base = [], "", 0
    for no, line in enumerate(log.splitlines()):
        if line.startswith("commit ") and len(line) == 47:
            sha, base = line[7:], no
            continue
        hits += [(f"history:{sha}", no - base, k) for _, _, k in scan_text("", line, denylist)]
    return hits


def _scan_index(denylist: List[str]) -> List[Tuple[str, int, str]]:
    """Scan the staged blobs (what a commit would contain), not the working copy."""
    hits = []
    for entry in _git("ls-files", "-s", "-z").decode(errors="replace").split("\0"):
        meta, _, rel = entry.partition("\t")
        mode, sha = meta.split()[:2] if meta else ("", "")
        if not rel or rel == DENYLIST_FILE or mode == "160000":
            continue
        data = _git("cat-file", "blob", sha)
        if b"\0" in data:
            continue
        hits += scan_text(rel, data.decode(errors="replace"), denylist)
    return hits


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--history", action="store_true", help="also scan git log -p --all")
    ap.add_argument("--require-denylist", action="store_true", help="exit 2 if denylist is empty")
    args = ap.parse_args(argv)

    try:
        denylist = load_denylist(os.environ, Path(DENYLIST_FILE))
        if args.require_denylist and not denylist:
            print("scrub_check: denylist is empty", file=sys.stderr)
            return 2
        hits = _scan_index(denylist)
        if args.history:
            hits += _scan_history(denylist)
    except (RuntimeError, OSError) as e:
        print(f"scrub_check: {e if isinstance(e, RuntimeError) else 'IO error'}", file=sys.stderr)
        return 3

    for name, no, kind in hits:
        print(f"{name}:{no}:{kind}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
