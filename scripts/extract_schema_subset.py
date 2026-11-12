#!/usr/bin/env python3
"""Extract a small subset of GitHub's public GraphQL schema (SDL) for the offline schema tests.

Usage: extract_schema_subset.py SOURCE TARGET [TARGET ...] > subset.graphql
  TARGET is `Type` (the whole definition) or `Type:field,field` (only those fields of an object/interface).
Doc strings and directives are dropped; the layout of what is kept is the source's own. Nothing is followed
transitively: a type that a kept field returns is in the subset only if it is listed (the tests need exactly the
listed ones). Stdlib only; reads one file and prints the subset, nothing else. Exit 2 on a missing type or field.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List

DIRECTIVE = re.compile(r'[ \t]*@\w+\((?:[^()"]|"(?:[^"\\]|\\.)*")*\)')  # @deprecated(reason: "...(x)..."), @docsCategory(...)
START = re.compile(r"^(?:type|interface|union|enum|input|scalar) (\w+)", re.M)
FIELD = re.compile(r"^  (\w+)")


def blocks(text: str) -> Dict[str, List[str]]:
    """Every definition of the cleaned schema: name -> its lines."""
    text = re.sub(r'"""[\s\S]*?"""', "", text)
    text = DIRECTIVE.sub("", text)
    text = "\n".join(l.rstrip() for l in text.split("\n") if l.strip())
    marks = list(START.finditer(text))
    return {m.group(1): text[m.start(): marks[i + 1].start() if i + 1 < len(marks) else len(text)].strip().split("\n")
            for i, m in enumerate(marks)}


def pick(lines: List[str], name: str, wanted: List[str]) -> List[str]:
    """The definition keeping only the wanted fields (head, the wanted entries, the closing brace)."""
    end = next(i for i, l in enumerate(lines) if l.endswith("{")) + 1  # the head can span lines (`implements A &\n B {`)
    out, keep, seen = lines[:end], False, set()
    for line in lines[end:-1]:
        m = FIELD.match(line)
        if m:
            keep = m.group(1) in wanted
            seen.add(m.group(1))
        if keep:
            out.append(line)
    if set(wanted) - seen:
        raise KeyError("%s has no field %s" % (name, ", ".join(sorted(set(wanted) - seen))))
    return out + lines[-1:]


def extract(text: str, targets: List[str]) -> str:
    defs, out = blocks(text), []
    for t in targets:
        name, _, fields = t.partition(":")
        if name not in defs:
            raise KeyError("no definition named %s" % name)
        out.append("\n".join(pick(defs[name], name, fields.split(",")) if fields else defs[name]))
    return "\n\n".join(out) + "\n"


def main(argv: List[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        body = extract(Path(argv[1]).read_text(encoding="utf-8"), argv[2:])
    except (KeyError, OSError) as e:
        print("error: %s" % (e.args[0] if isinstance(e, KeyError) else e), file=sys.stderr)
        return 2
    header = (
        "# GitHub's public GraphQL schema (https://docs.github.com/public/fpt/schema.docs.graphql), as saved by spike/s0_schema.py\n"
        "# on 2026-11-08. GitHub publishes this schema as public documentation; it holds no client or secret data.\n"
        "# A SUBSET, doc strings and directives stripped: only the definitions the offline schema tests read.\n"
        "# Regenerate (after `python3 -I spike/s0_schema.py`):\n"
        "#   python3 scripts/extract_schema_subset.py spike/out/schema.docs.graphql \\\n"
        "#     %s \\\n#     > tests/fixtures/gh/schema.subset.graphql\n\n" % " \\\n#     ".join(argv[2:])
    )
    sys.stdout.write(header + body)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
