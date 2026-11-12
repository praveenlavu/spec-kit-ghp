"""The shipped `salesforce` seed profile (V3 piece 2, P2-13): valid, still a draft, every item cited or `unverified`, no refused
command token, and the one test command the design fixes (D§8). The sources were fetched by the builder on the retrieval date; this
file checks the shape, not the web (no network in unit tests)."""
import datetime
import re
from pathlib import Path

from ghp import stack

SEED = Path(stack.SEEDS_DIR) / "salesforce.json"


def seed():
    return stack.parse_json(SEED.read_bytes(), stack.MAX_PROFILE)


def strings(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for k, x in v.items():
            yield k
            yield from strings(x)
    elif isinstance(v, list):
        for x in v:
            yield from strings(x)


def commands(profile):
    for s in profile["stacks"]:
        for v in s["commands"].values():
            yield from (v if isinstance(v, list) else [v] if v else [])


def refs(profile):
    yield from (c["source"] for c in commands(profile))
    for s in profile["stacks"]:
        yield from (i["source"] for i in s["conventions"] + s["dod"])


def test_seed_validates():
    data = seed()
    assert stack.validate(data) == []
    assert data["status"] == "draft" and "approval" not in data
    assert [s["id"] for s in data["stacks"]] == ["salesforce"] and data["stacks"][0]["root"] == "."


def test_every_item_cited_or_unverified():
    data = seed()
    cited = set()
    for r in refs(data):
        assert r == "unverified" or r in data["sources"], r
        cited.add(r)
    assert set(data["sources"]) <= cited, "a source nothing cites"
    for s in data["sources"].values():
        assert s["kind"] in ("official", "implementation") and s["url"].startswith("https://") and len(s["evidence"]) <= 300


def test_dates_not_future():
    for s in seed()["sources"].values():
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", s["retrieved"])
        assert datetime.date.fromisoformat(s["retrieved"]) <= datetime.date.today()


def test_no_tripwire_tokens():
    data = seed()
    for c in commands(data):
        assert c["run"].isascii() and "\n" not in c["run"] and not stack._TRIPWIRE.search(c["run"]), c["run"]
    assert not [t for t in strings(data) if "`" in t or "$(" in t]


def test_test_command_exact():
    assert seed()["stacks"][0]["commands"]["test"]["run"] == "npm ci --ignore-scripts && npm run test:unit"


def test_apex_is_org_only():
    """Apex tests and deploys need a live org: they sit in `org_only` (the agent runs them only on the human's yes; strict never does)."""
    cmds = seed()["stacks"][0]["commands"]
    org = [c["run"] for c in cmds["org_only"]]
    assert any(r.startswith("sf apex run test ") for r in org), org
    assert all(r.startswith("sf ") for r in org), org
    others = [c["run"] for k, v in cmds.items() if k != "org_only" for c in (v if isinstance(v, list) else [v])]
    assert not [r for r in others if re.search(r"\bsf\b(?! code-analyzer)|apex", r)], others  # Code Analyzer needs no org; nothing else may call sf or Apex
    assert not [r for r in others if "npx" in r.split()], others
