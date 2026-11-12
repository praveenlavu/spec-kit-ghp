#!/usr/bin/env python3
"""Check commit subjects, one per line on stdin (from `git log --format=%s BASE..HEAD`).

A subject passes when it names a task and its acceptance criteria (`T38a Short title: AC 1,2`),
or starts with `docs:`, `chore:` or `Merge `. Exit: 0 all pass, 1 any fails or no commits.
"""
import re
import sys

TASK = re.compile(r"^T[0-9]{2,3}[a-z]? .+: AC .+")
OTHER = ("docs:", "chore:", "Merge ")


def ok(subject):
    return bool(TASK.match(subject)) or subject.startswith(OTHER)


def _safe(s):
    """Subjects are PR text: no control characters (no workflow commands, no terminal escapes), cut short."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f  ]", " ", s)[:200]


def main(stdin=sys.stdin):
    subjects = [ln.rstrip("\n") for ln in stdin]
    bad = [s for s in subjects if not ok(s)]
    for s in bad:
        print("::error::want 'T38a Title: AC 1,2', docs: or chore:, got: " + _safe(s))
    if not subjects:
        print("::error::no commits to check")
        return 1
    print("%d of %d commit subjects pass." % (len(subjects) - len(bad), len(subjects)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
