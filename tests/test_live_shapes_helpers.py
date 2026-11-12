"""T13b: the pure helpers of tests/live/test_fixture_shapes.py, offline. A path counts as null or empty only if it is so in EVERY item of a list."""
from live.test_fixture_shapes import diff, key_paths, uncompared


def test_null_in_one_item_does_not_hide_the_sub_paths_of_others():
    live = {"nodes": [{"actor": None}, {"actor": {"login": "x"}}]}
    fixture = {"nodes": [{"actor": {"login": "x", "name": "n"}}]}
    assert "nodes[].actor" not in key_paths(live)[1]
    assert diff(fixture, live)[0] == ["nodes[].actor.name"]  # fixture-only, no longer hidden by the null item
    assert uncompared(fixture, live) == []


def test_null_in_every_item_is_still_uncompared_and_hides_sub_paths():
    live = {"nodes": [{"actor": None}, {"actor": None}]}
    fixture = {"nodes": [{"actor": {"login": "x"}}]}
    assert "nodes[].actor" in key_paths(live)[1]
    assert diff(fixture, live)[0] == []
    assert uncompared(fixture, live) == ["nodes[].actor"]


def test_empty_list_and_missing_in_an_item():
    assert key_paths({"a": []})[1] == {"a"}
    assert key_paths([{"a": None}, {}])[1] == set()  # absent in one item: not null there
    assert key_paths([{"a": None}, {"a": []}])[1] == {"[].a"}
