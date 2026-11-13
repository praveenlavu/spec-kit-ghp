"""The live pull-request tests read the Project's two closing switches before they choose what to expect (offline, with a fake gh)."""
import json
from pathlib import Path

import pytest

from conftest import FakeGh
from ghp.config import Config
from live.test_pr_done import after_merge_writes, closing_switches

WORKFLOWS = json.loads((Path(__file__).resolve().parent / "fixtures" / "gh" / "project_workflows.json").read_text())
CFG = Config(owner="o", repo="r", project_owner="o", project_number=3)


def test_each_closing_switch_is_read_on_its_own():
    """The fixture has "Item closed" on and "Auto-close issue" off: the two are not mixed up."""
    gh = FakeGh().on_graphql("workflows", WORKFLOWS)
    assert closing_switches(gh, CFG) == {"auto_close": False, "item_closed": True}


def test_a_missing_closing_switch_stops_the_test_instead_of_guessing():
    nodes = [n for n in WORKFLOWS["user"]["projectV2"]["workflows"]["nodes"] if n["name"] != "Item closed"]
    data = json.loads(json.dumps(WORKFLOWS))
    data["user"]["projectV2"]["workflows"]["nodes"] = nodes
    with pytest.raises(AssertionError, match="Item closed"):
        closing_switches(FakeGh().on_graphql("workflows", data), CFG)


def test_done_after_a_merge_sets_done_itself_only_when_item_closed_is_off():
    comment = "POST repos/o/r/issues/7/comments"
    assert after_merge_writes("o/r", 7, item_closed=True) == [comment]
    assert after_merge_writes("o/r", 7, item_closed=False) == [comment, "graphql mutation updateProjectV2ItemFieldValue"]
