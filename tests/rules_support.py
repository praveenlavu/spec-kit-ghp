"""Shared fixtures of the T12 rules tests (tracker-seam: ado's work-item dicts became GitHub REST issues served by a FakeGh).
`raw_issue` is a REST issue (the shape `issues.get` reads), `serve` a FakeGh that answers GET for the issues it is given, `desc` a Markdown
Business rules section."""
import json
from pathlib import Path

from conftest import FakeGh
from ghp import issues
from ghp.refs import Ref

FIX = Path(__file__).parent / "fixtures" / "gh"
OWNER, REPO = "octo", "demo"


def path(n, owner=OWNER, repo=REPO):
    return f"repos/{owner}/{repo}/issues/{n}"


def raw_issue(n, kind=None, body=None, parent=None, title="Points on purchases", labels=None, owner=OWNER, repo=REPO, state="open"):
    """`kind` 'Feature' labels the issue `ghp:feature` (None: `ghp:story`); `labels` replaces that. `parent`: an issue number of the same
    repository, or an `(owner, repo, number)` tuple, or None."""
    d = json.loads((FIX / "issue_story.json").read_text(encoding="utf-8"))
    names = labels if labels is not None else ["ghp:" + (kind or "story").casefold()]
    base = f"https://api.github.com/repos/{owner}/{repo}"
    d.update(number=n, id=9100000000 + n, node_id="I_kwDOFictional%04d" % n, title=title, body=body if body is not None else "", state=state,
             labels=[{"id": i + 1, "name": x} for i, x in enumerate(names)], url=f"{base}/issues/{n}", repository_url=base,
             html_url=f"https://github.com/{owner}/{repo}/issues/{n}")
    if parent is None:
        d["parent_issue_url"] = None
    else:
        o, r, p = (owner, repo, parent) if isinstance(parent, int) else parent
        d["parent_issue_url"] = f"https://api.github.com/repos/{o}/{r}/issues/{p}"
    return d


def make_issue(n=1201, **kw):
    return issues._issue(raw_issue(n, **kw), Ref(kw.get("owner", OWNER), kw.get("repo", REPO), n))


def serve(*raws, gh=None):
    """A FakeGh answering GET for each raw issue (by the owner/repo/number it carries)."""
    gh = gh or FakeGh()
    for r in raws:
        base = r["repository_url"].split("/repos/", 1)[1]
        gh.on_rest("GET", f"repos/{base}/issues/{r['number']}", r)
    return gh


def fail(err, n, owner=OWNER, repo=REPO, gh=None):
    """A FakeGh whose GET of issue `n` raises `err`."""
    def boom(_):
        raise err
    return (gh or FakeGh()).on_rest("GET", path(n, owner, repo), boom)


def desc(*specs, text="Customers earn {n} points per euro.", extra=""):
    """A Business rules section in Markdown: (n, status) per rule, every label present (no warnings)."""
    return "## Business rules\n\n" + "".join(
        f"Rule {n}: {text.format(n=n)}\nWhy: loyalty\nExample: spend 10\nOwner: Loyalty product owner\nStatus: {s}\n\n" for n, s in specs) + extra
