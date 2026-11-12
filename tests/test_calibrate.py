"""`ghp calibrate` (T22; ado P4-16): the plane 8 golden set, `emit` (rubric + unlabelled items) and `score` (agreement, false passes).

No model is called anywhere: a model's answer is just a string here.
Seam: ghp has no commands/review.md until T32 (the 13 slash commands), so `REVIEW` is that file when it exists, else the plane 8 stand-in
`tests/fixtures/calibrate/review.md` (the plane 8 section of ado's review.md, renamed); T32 can delete the fixture."""
import json
import os
import shutil
from pathlib import Path

import pytest

from ghp import calibrate
from ghp.__main__ import main

ROOT = Path(__file__).resolve().parent.parent
REVIEW = ROOT / "commands/review.md" if (ROOT / "commands/review.md").is_file() else ROOT / "tests/fixtures/calibrate/review.md"
LABELS = ("asserts", "partial", "does not assert")
ITEMS = calibrate.load_items()
TRUTH = {i["id"]: i["label"] for i in ITEMS}


def _answer(**changes):
    return json.dumps({**TRUTH, **changes})


def _rubric(md):
    start = md.index("**Plane 8 — AC fidelity**")
    return md[start:md.index("\nPlane 8 writes verdicts")].rstrip("\n")


def test_emit_copies_rubric_verbatim():
    md = REVIEW.read_bytes().decode("utf-8")
    out = calibrate.emit(md, ITEMS)
    assert _rubric(md) in out
    assert "- AC-<n>: asserts|partial|does not assert" in out
    odd = "intro\n\n**Plane 8 — AC fidelity** (x)\n\n> line  one é \r\n> two\t\n\n\ntext after\n"  # trailing blanks, tabs, CRLF, NBSP are kept
    assert "**Plane 8 — AC fidelity** (x)\n\n> line  one é \r\n> two\t\n" in calibrate.emit(odd, ITEMS)
    with pytest.raises(ValueError):
        calibrate.emit("no rubric here", ITEMS)


def test_emit_has_no_labels():
    md = REVIEW.read_text(encoding="utf-8")
    swapped = [{**i, "label": LABELS[(LABELS.index(i["label"]) + 1) % 3]} for i in ITEMS]
    out = calibrate.emit(md, ITEMS)
    assert out == calibrate.emit(md, swapped)  # the label never reaches the output
    body = out[out.index("## Items"):]
    assert '"label"' not in body and all(i["id"] in body and i["ac"] in body and i["test"] in body for i in ITEMS)
    assert "Strict mode is on." in out and "answer only with the JSON object" in out
    assert '{"G1": "...", "G2": "...", ' in out and '"G12": "..."}' in out
    for lab in LABELS:
        assert lab in out.split("## Items")[0]  # the answer format names the three values


def test_score_threshold_and_false_pass():
    r = calibrate.score(ITEMS, _answer())
    assert (r["agreement"], r["false_pass"], r["ok"]) == (12, 0, True)
    assert set(r) == {"agreement", "false_pass", "soft_pass", "confusion", "ok"} and r["soft_pass"] == 0
    assert r["confusion"]["asserts"]["asserts"] == 4 and sum(sum(v.values()) for v in r["confusion"].values()) == 12
    two = calibrate.score(ITEMS, _answer(G2="partial", G7="asserts"))  # 10 of 12, no false pass: the boundary passes
    assert (two["agreement"], two["false_pass"], two["ok"]) == (10, 0, True)
    three = calibrate.score(ITEMS, _answer(G2="partial", G7="asserts", G4="partial"))
    assert (three["agreement"], three["ok"]) == (9, False)
    fp = calibrate.score(ITEMS, _answer(G1="asserts"))  # 11 of 12 but a `does not assert` judged `asserts`
    assert (fp["agreement"], fp["false_pass"], fp["ok"]) == (11, 1, False)
    assert fp["confusion"]["does not assert"]["asserts"] == 1
    soft = calibrate.score(ITEMS, _answer(G1="partial"))  # a miss in the safe direction is not a false pass
    assert (soft["agreement"], soft["false_pass"], soft["soft_pass"], soft["ok"]) == (11, 0, 1, True)  # informational: ok is unchanged
    assert calibrate.score(ITEMS, _answer(G1="partial", G5="partial", G9="partial"))["soft_pass"] == 3
    assert calibrate.score(ITEMS, _answer(G2="partial"))["soft_pass"] == 0  # only a `does not assert` pair counts
    assert calibrate.score(ITEMS, "Here you go:\n```json\n" + _answer() + "\n```\nDone {not json}")["ok"] is True
    assert calibrate.score(ITEMS, "x" * 10 + " " + _answer() + " trailing")["ok"] is True  # the first `{` starts the object


@pytest.mark.parametrize("text", [
    json.dumps({**TRUTH, "G13": "asserts"}),  # unknown id
    json.dumps({**TRUTH, "G1": "ASSERTS"}),  # not one of the three values
    json.dumps({**TRUTH, "G1": "asserts - looks fine"}),  # an extra value
    json.dumps({**TRUTH, "G1": ["asserts"]}),
    json.dumps({**TRUTH, "G1": None}),
    json.dumps({k: v for k, v in TRUTH.items() if k != "G5"}),  # a missing id is refused, not scored
    '{"G1": "asserts", "G1": "partial", ' + ", ".join(f'"{k}": "{v}"' for k, v in TRUTH.items() if k != "G1") + "}",  # a duplicate id
    "",
    "no json at all",
    "[1, 2] and no object",
    '{"a":' * 10000,  # ~50 KB: under the cap, so it reaches the recursion handler
    _answer()[:-1],  # truncated
    "x" * (64 * 1024 + 1),  # over 64 KiB
    " " * (64 * 1024) + _answer(),
])
def test_score_rejects_unknown_id_extra_value_oversize(text):
    with pytest.raises(ValueError) as e:
        calibrate.score(ITEMS, text)
    assert 0 < len(str(e.value)) < 120  # a short fixed text


def test_deep_nesting_under_the_cap_is_refused_by_the_recursion_handler():
    text = '{"a":' * 10000
    assert len(text) < 64 * 1024
    with pytest.raises(ValueError, match="nested too deep"):
        calibrate.parse(text)


def test_first_object_wins_and_a_stray_brace_in_prose_is_refused():
    good, bad = _answer(), _answer(G1="asserts", G2="partial", G3="asserts")
    assert calibrate.score(ITEMS, good + "\nand again: " + bad)["ok"] is True  # trailing objects are ignored
    assert calibrate.score(ITEMS, bad + "\n" + good)["ok"] is False  # the decoy first: only it is judged
    for prose in ('Format: {"G1": "...", "G2": "..."}\n', "I will answer in {JSON}:\n"):  # a `{` before the answer starts the (wrong) object
        with pytest.raises(ValueError):
            calibrate.score(ITEMS, prose + good)


def test_score_errors_never_echo_the_answer():
    for text in (json.dumps({**TRUTH, "G1": "ZZ-MODEL-TEXT", "ZZ-ID": "partial"}), "ZZ-MODEL-TEXT {oops", '{"G1": "asserts", "G1": "ZZ-MODEL-TEXT"}'):
        with pytest.raises(ValueError) as e:
            calibrate.score(ITEMS, text)
        assert "ZZ-" not in str(e.value)


def test_a_score_of_exactly_64_kib_is_read():
    pad = " " * (64 * 1024 - len(_answer().encode("utf-8")))
    assert calibrate.score(ITEMS, _answer() + pad)["ok"] is True


def test_the_golden_set_is_package_data_with_one_source_of_truth():
    import ghp
    # wherever `ghp` is imported from (the checkout when installed editable, site-packages otherwise), it is the package's own copy and equals the checkout's
    assert calibrate.GOLDEN.resolve() == Path(ghp.__file__).resolve().parent / "golden/fidelity.json" and calibrate.GOLDEN.is_file()
    assert calibrate.GOLDEN.read_bytes() == (ROOT / "src/ghp/golden/fidelity.json").read_bytes()
    assert not (ROOT / "tests/golden/fidelity.json").exists()  # one copy only
    import importlib.resources as res
    assert res.files("ghp").joinpath("golden/fidelity.json").is_file()
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"golden/*.json"' in pyproject.split("[tool.setuptools.package-data]")[1]


def test_golden_set_balanced():
    raw = json.loads((ROOT / "src/ghp/golden/fidelity.json").read_text(encoding="utf-8"))
    assert [i["id"] for i in raw] == [f"G{n}" for n in range(1, 13)]
    assert all(set(i) == {"id", "ac", "test", "lang", "label"} for i in raw)
    assert sorted(i["label"] for i in raw) == sorted(LABELS * 4) and {i["label"] for i in raw} == set(LABELS)
    assert {i["lang"] for i in raw} == {"Python", "JavaScript", "Apex"}
    for lab in LABELS:  # every language appears under every label
        assert {i["lang"] for i in raw if i["label"] == lab} == {"Python", "JavaScript", "Apex"}
    assert all(i["ac"].strip() and i["test"].strip() and len(i["test"]) < 800 for i in raw)
    text = json.dumps(raw)
    assert "contoso" in text.lower() or "fabrikam" in text.lower()
    assert calibrate.load_items() == raw


def test_load_items_refuses_a_bad_set(tmp_path):
    good = json.loads((ROOT / "src/ghp/golden/fidelity.json").read_text(encoding="utf-8"))
    for bad in (good[:11], good + [good[0]], [{**good[0], "label": "maybe"}] + good[1:], [{**good[0], "extra": 1}] + good[1:], {"a": 1}):
        p = tmp_path / "g.json"
        p.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(ValueError):
            calibrate.load_items(p)
    with pytest.raises(ValueError):
        calibrate.load_items(tmp_path / "missing.json")


# ---- the CLI ----
@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _source_checkout(proj):
    (proj / "commands").mkdir()
    shutil.copy(REVIEW, proj / "commands/review.md")
    (proj / "extension.yml").write_text('extension:\n  id: "ghp"\n', encoding="utf-8")


def test_emit_from_a_source_checkout(proj, capsys):
    _source_checkout(proj)
    assert main(["calibrate", "--emit", ".ghp/calibrate.md"]) == 0
    got = (proj / ".ghp/calibrate.md").read_text(encoding="utf-8")
    assert got == calibrate.emit(REVIEW.read_text(encoding="utf-8"), ITEMS)
    assert json.loads(capsys.readouterr().out)["items"] == 12
    assert [p.name for p in (proj / ".ghp").iterdir()] == ["calibrate.md"]  # no temp file left behind


def test_emit_from_the_installed_extension(proj):
    inst = proj / ".specify/extensions/ghp/commands"
    inst.mkdir(parents=True)
    (inst / "review.md").write_bytes(REVIEW.read_bytes())
    assert main(["calibrate", "--emit", ".ghp/c.md"]) == 0
    assert _rubric(REVIEW.read_text(encoding="utf-8")) in (proj / ".ghp/c.md").read_text(encoding="utf-8")


def test_emit_without_a_review_file_is_refused(proj, capsys):
    assert main(["calibrate", "--emit", ".ghp/c.md"]) == 2
    err = capsys.readouterr().err
    assert "commands/review.md not found" in err and ".specify/extensions/ghp/commands/review.md" in err and "root" in err and not (proj / ".ghp").exists()


def test_emit_refuses_a_path_outside_ghp_and_symlinks(proj, capsys):
    _source_checkout(proj)
    for bad in ("calibrate.md", "../x.md", ".ghp/../calibrate.md", str(proj / "out.md"), ".ghp/calibrate.txt"):
        assert main(["calibrate", "--emit", bad]) == 2, bad
    assert not (proj / "calibrate.md").exists() and not (proj / "out.md").exists()
    (proj / ".ghp").mkdir()
    (proj / "target.md").write_text("keep", encoding="utf-8")
    os.symlink(proj / "target.md", proj / ".ghp/link.md")
    os.symlink(proj, proj / ".ghp/dir")
    assert main(["calibrate", "--emit", ".ghp/link.md"]) == 2
    assert main(["calibrate", "--emit", ".ghp/dir/x.md"]) == 2
    assert (proj / "target.md").read_text(encoding="utf-8") == "keep"
    capsys.readouterr()


def test_emit_and_score_refuse_a_symlink_that_points_back_inside_ghp(proj, capsys):
    _source_checkout(proj)
    (proj / ".ghp/real").mkdir(parents=True)
    (proj / ".ghp/real/t.md").write_text("keep", encoding="utf-8")
    (proj / ".ghp/real/a.json").write_text(_answer(), encoding="utf-8")
    os.symlink(proj / ".ghp/real/t.md", proj / ".ghp/link.md")
    os.symlink(proj / ".ghp/real/a.json", proj / ".ghp/link.json")
    os.symlink(proj / ".ghp/real", proj / ".ghp/linkdir")
    assert main(["calibrate", "--emit", ".ghp/link.md"]) == 2
    assert main(["calibrate", "--emit", ".ghp/linkdir/t.md"]) == 2
    assert main(["calibrate", "--score", ".ghp/link.json"]) == 2
    assert main(["calibrate", "--score", ".ghp/linkdir/a.json"]) == 2
    assert (proj / ".ghp/real/t.md").read_text(encoding="utf-8") == "keep"
    assert main(["calibrate", "--score", ".ghp/real/a.json"]) == 0  # the real path is fine
    capsys.readouterr()


def test_a_failed_emit_leaves_the_old_file_and_no_temp_file(proj, monkeypatch, capsys):
    _source_checkout(proj)
    (proj / ".ghp").mkdir()
    (proj / ".ghp/c.md").write_text("old", encoding="utf-8")

    def boom(*a, **k):
        raise OSError("disk")
    monkeypatch.setattr(os, "replace", boom)
    assert main(["calibrate", "--emit", ".ghp/c.md"]) == 2
    assert (proj / ".ghp/c.md").read_text(encoding="utf-8") == "old" and [p.name for p in (proj / ".ghp").iterdir()] == ["c.md"]
    assert "cannot be written" in capsys.readouterr().err


def test_emit_overwrites_atomically(proj):
    _source_checkout(proj)
    (proj / ".ghp").mkdir()
    (proj / ".ghp/c.md").write_text("old", encoding="utf-8")
    assert main(["calibrate", "--emit", ".ghp/c.md"]) == 0
    assert (proj / ".ghp/c.md").read_text(encoding="utf-8").startswith("# ")


def _write(proj, name, text, mode="w"):
    (proj / ".ghp").mkdir(exist_ok=True)
    (proj / ".ghp" / name).write_text(text, encoding="utf-8") if mode == "w" else (proj / ".ghp" / name).write_bytes(text)
    return f".ghp/{name}"


def test_score_exit_codes(proj, capsys):
    assert main(["calibrate", "--score", _write(proj, "a.json", _answer())]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert main(["calibrate", "--score", _write(proj, "b.json", _answer(G1="asserts"))]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["false_pass"] == 1 and out["ok"] is False
    assert main(["calibrate", "--score", _write(proj, "c.json", _answer(G1="partial", G2="partial", G3="asserts"))]) == 1
    capsys.readouterr()


def test_score_refusals_are_exit_2_with_fixed_text(proj, capsys):
    bad = {"unknown": _write(proj, "u.json", json.dumps({**TRUTH, "ZZ-X": "asserts"})), "junk": _write(proj, "j.json", "ZZ-MODEL {nope"),
           "big": _write(proj, "big.json", "x" * (64 * 1024 + 1)), "binary": _write(proj, "bin.json", b"\xff\xfe{", mode="wb")}
    for name, path in bad.items():
        assert main(["calibrate", "--score", path]) == 2, name
        assert "ZZ-" not in capsys.readouterr().err
    assert main(["calibrate", "--score", ".ghp/missing.json"]) == 2
    assert main(["calibrate", "--score", "answer.json"]) == 2  # not under .ghp/
    (proj / "answer.json").write_text(_answer(), encoding="utf-8")
    assert main(["calibrate", "--score", "answer.json"]) == 2
    os.symlink(proj / "answer.json", proj / ".ghp/link.json")
    assert main(["calibrate", "--score", ".ghp/link.json"]) == 2
    capsys.readouterr()


def test_exactly_one_of_emit_and_score(capsys):
    for argv in (["calibrate"], ["calibrate", "--emit", ".ghp/a.md", "--score", ".ghp/a.json"]):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert e.value.code == 2


def test_the_repository_root_lookup_runs_git_with_a_clean_environment(proj, monkeypatch):
    """`_under_ghp` asks git for the top level: with `prs.git_env()` (no repository selector or config injection from the environment, no pager)."""
    import subprocess
    monkeypatch.setenv("GIT_DIR", "/nonexistent-elsewhere")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.pager")
    real, seen = subprocess.run, []

    def spy(*a, **kw):
        seen.append((a[0], kw))
        return real(*a, **kw)
    monkeypatch.setattr(calibrate.subprocess, "run", spy)
    assert calibrate.ghp_path(".ghp/c.md", ".md") == proj.resolve() / ".ghp/c.md"
    (argv, kw), = seen
    assert argv[:2] == ["git", "--no-pager"] and "GIT_DIR" not in kw["env"] and "GIT_CONFIG_KEY_0" not in kw["env"] and kw["stdin"] == subprocess.DEVNULL


def test_the_help_text_says_it_in_plain_words(capsys):
    """`plane 8` is a word of the review command, not of the CLI help."""
    for argv in (["--help"], ["calibrate", "--help"]):
        with pytest.raises(SystemExit):
            main(argv)
        out = " ".join(capsys.readouterr().out.split())
        assert "plane" not in out.lower(), argv
        if argv == ["--help"]:  # the verb list carries the line; `calibrate --help` has no description of its own
            assert "how far a model's check of ac-to-test fidelity can be trusted" in out.lower()
