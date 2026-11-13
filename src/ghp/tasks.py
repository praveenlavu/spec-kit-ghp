import re

_TASK = re.compile(r"^\s*- \[( |x|X)\] (T\d{3,})\b(.*)$")
_MARKERS = re.compile(r"\[P\]|\[US\d+\]")


_LOOKS_LIKE = re.compile(r"^\s*[-*+]\s*\[[ xX]?\]\s*T[A-Za-z0-9]")  # a checkbox line whose text starts like a task id


def not_tasks(md):
    """[(line number, line)] of the lines that look like a task but are not read as one (`- [ ] TX1 ...`): shown, so a task does not vanish."""
    return [(i, line.strip()) for i, line in enumerate(md.splitlines(), 1) if _LOOKS_LIKE.match(line) and not _TASK.match(line)]


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
