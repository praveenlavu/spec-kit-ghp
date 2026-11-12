import re

_TASK = re.compile(r"^\s*- \[( |x|X)\] (T\d{3,})\b(.*)$")
_MARKERS = re.compile(r"\[P\]|\[US\d+\]")


def parse_tasks(md):
    out = []
    for line in md.splitlines():
        m = _TASK.match(line)
        if not m:
            continue
        mark, tid, rest = m.groups()
        out.append({
            "id": tid,
            "title": " ".join(_MARKERS.sub(" ", rest).split()),
            "done": mark != " ",
        })
    return out
