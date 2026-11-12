import dataclasses

import pytest

from ghp.refs import Ref, RefError, parse_ref

DEFAULT = Ref("acme-labs", "widget_kit", 1)


def code_of(text, default=None):
    with pytest.raises(RefError) as exc:
        parse_ref(text, default)
    assert exc.value.msg
    return exc.value.code


def test_str_is_owner_repo_hash_number():
    assert str(Ref("acme-labs", "widget_kit", 5)) == "acme-labs/widget_kit#5"


def test_ref_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        Ref("a", "b", 1).number = 2


@pytest.mark.parametrize("text", [
    "5",
    "#5",
    "acme-labs/widget_kit#5",
    "https://github.com/acme-labs/widget_kit/issues/5",
])
def test_parse_accepts_each_form(text):
    assert parse_ref(text, DEFAULT) == Ref("acme-labs", "widget_kit", 5)


def test_parse_accepts_full_forms_without_default():
    want = Ref("acme", "repo.v2", 12)
    assert parse_ref("acme/repo.v2#12") == want
    assert parse_ref("https://github.com/acme/repo.v2/issues/12") == want


BAD = ["0", "-1", "05", "5.0", "", " ", "\t", "\n", "#", " 5", "5 ", "5\n", "#5\n",
       "#05", "##5", "abc", "٥", "１２", "1e3", "+5"]
PR_URLS = ["https://github.com/acme-labs/widget_kit/pull/5",
           "https://github.com/other/thing/pull/5"]


@pytest.mark.parametrize("text,code", [(t, "E_BAD_REF") for t in BAD] +
                         [(u, "E_PULL_REQUEST_URL") for u in PR_URLS])
def test_parse_rejects_malformed_and_pr_urls(text, code):
    assert code_of(text, DEFAULT) == code


def test_parse_rejects_pr_urls_with_a_named_code():
    for url in ("https://github.com/acme-labs/widget_kit/pull/5",
                "https://github.com/other/thing/pull/5"):
        assert code_of(url, DEFAULT) == "E_PULL_REQUEST_URL"
        assert code_of(url) == "E_PULL_REQUEST_URL"


@pytest.mark.parametrize("text", [
    "https://github.com/acme-labs/widget_kit/issues/0",
    "https://github.com/acme-labs/widget_kit/issues/05",
    "https://github.com/acme-labs/widget_kit/issues/5/",
    "https://github.com/acme-labs/widget_kit/issues/5?x=1",
    "https://github.com/acme-labs/widget_kit/issues/5#issuecomment-1",
    "https://github.com/acme-labs/widget_kit/issues/5/extra",
    "https://github.com/acme-labs/widget_kit/issues",
    "https://github.com/acme-labs/widget_kit/discussions/5",
    "http://github.com/acme-labs/widget_kit/issues/5",
    "https://evil.example/acme-labs/widget_kit/issues/5",
    "https://github.com.evil.example/acme-labs/widget_kit/issues/5",
    "https://user@github.com/acme-labs/widget_kit/issues/5",
    "acme-labs/widget_kit",
    "acme-labs/widget_kit#",
    "acme-labs/widget_kit#0",
    "acme-labs/widget_kit#05",
    "acme-labs/widget_kit#5#6",
    "/widget_kit#5",
    "acme-labs/#5",
])
def test_parse_rejects_other_malformed_forms(text):
    assert code_of(text, DEFAULT) == "E_BAD_REF"


@pytest.mark.parametrize("text", [
    "../widget_kit#5",
    "acme-labs/..#5",
    "acme-labs/.#5",
    "acme-labs/a/b#5",
    "acme-labs/a b#5",
    "-acme/widget_kit#5",
    "acme-/widget_kit#5",
    "ac_me/widget_kit#5",
    "ac--me/widget_kit#5",
    "ac.me/widget_kit#5",
    "a" * 40 + "/widget_kit#5",
    "acme/" + "r" * 101 + "#5",
    "https://github.com/../widget_kit/issues/5",
    "https://github.com/acme-labs/../issues/5",
    "https://github.com/acme-labs/%2e%2e/issues/5",
])
def test_parse_rejects_path_tricks_and_bad_names(text):
    assert code_of(text, DEFAULT) == "E_BAD_REF"


def _forms(n):
    return [str(n), "#%s" % n, "acme-labs/widget_kit#%s" % n,
            "https://github.com/acme-labs/widget_kit/issues/%s" % n]


@pytest.mark.parametrize("n", ["9" * 5000, str(2 ** 31), str(2 ** 31 + 1), "9" * 10, "1" + "0" * 10])
def test_out_of_range_numbers_are_refused_in_every_form(n):
    for text in _forms(n):
        assert code_of(text, DEFAULT) == "E_BAD_REF"


def test_largest_int32_number_is_accepted_in_every_form():
    for text in _forms(str(2 ** 31 - 1)):
        assert parse_ref(text, DEFAULT).number == 2 ** 31 - 1


def test_bare_number_without_default_is_an_error():
    assert code_of("5") == "E_NEEDS_DEFAULT"
    assert code_of("#5") == "E_NEEDS_DEFAULT"


def test_malformed_without_default_is_still_bad_ref():
    assert code_of("0") == "E_BAD_REF"
    assert code_of("#") == "E_BAD_REF"


def test_foreign_repo_is_refused():
    assert code_of("other/widget_kit#5", DEFAULT) == "E_FOREIGN_REPO"
    assert code_of("acme-labs/other#5", DEFAULT) == "E_FOREIGN_REPO"
    assert code_of("https://github.com/other/thing/issues/5", DEFAULT) == "E_FOREIGN_REPO"


def test_owner_and_repo_compare_case_insensitively():
    assert parse_ref("ACME-Labs/Widget_Kit#5", DEFAULT) == Ref("acme-labs", "widget_kit", 5)
    assert parse_ref("https://github.com/ACME-LABS/WIDGET_KIT/issues/7", DEFAULT) == \
        Ref("acme-labs", "widget_kit", 7)
    assert parse_ref("Acme/Repo#5") == Ref("Acme", "Repo", 5)


def test_error_carries_code_and_msg():
    err = RefError("E_BAD_REF", "nope")
    assert (err.code, err.msg) == ("E_BAD_REF", "nope")
    assert "E_BAD_REF" in str(err) and "nope" in str(err)
