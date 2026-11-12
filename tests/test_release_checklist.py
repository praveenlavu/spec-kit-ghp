"""T41: the release files and the checklist. One version value everywhere (AC1), `SECURITY.md` states the trust limits and a private reporting
route (AC2), and every checklist step has a `Who:` line, with the steps only the owner may do marked (AC3). The new pages also pass the public
lint (no internal ids, no private names, no email addresses, no tokens)."""
import re
from pathlib import Path

import pytest

import test_catalog
from test_catalog import bundle, catalog, lint, workflow
from test_manifest import manifest

ROOT = Path(__file__).resolve().parent.parent
CHECKLIST = ROOT / "docs" / "release-checklist.md"
PAGES = [ROOT / "CHANGELOG.md", ROOT / "SECURITY.md", ROOT / "VERSIONING.md", CHECKLIST]
FINAL = re.compile(r"^\d+\.\d+\.\d+$")


def text(p):
    return p.read_text(encoding="utf-8")


def flat(p):
    return " ".join(text(p).split())


# ---- AC1 ----
def test_version_is_consistent_everywhere():
    ext, wf, b = manifest(), workflow(), bundle()
    py = text(ROOT / "pyproject.toml")
    # pyproject has no number of its own: it reads the one in the package
    assert 'dynamic = ["version"]' in py and 'version = { attr = "ghp.__version__" }' in py
    assert not re.search(r'^version\s*=\s*"', py, re.M)
    found = {
        "ghp/__init__.py": re.search(r'^__version__ = "([^"]+)"$', text(ROOT / "src" / "ghp" / "__init__.py"), re.M).group(1),
        "extension.yml": ext["extension"]["version"],
        "extension.yml tool floor": re.fullmatch(r">=(.+)", [t for t in ext["requires"]["tools"] if t["name"] == "ghp"][0]["version"]).group(1),
        "workflow.yml": wf["workflow"]["version"],
        "catalog/extensions.json": catalog("extensions.json")["extensions"]["ghp"]["version"],
        "catalog/workflows.json": catalog("workflows.json")["workflows"]["ghp-lifecycle"]["version"],
        "bundle.yml": b["bundle"]["version"],
        "bundle.yml extension": b["provides"]["extensions"][0]["version"],
        "bundle.yml workflow": b["provides"]["workflows"][0]["version"],
        "CHANGELOG.md": re.search(r"^## (\S+)", text(ROOT / "CHANGELOG.md"), re.M).group(1),
    }
    assert set(found.values()) == {found["ghp/__init__.py"]}, found
    assert FINAL.match(found["ghp/__init__.py"]), "a release candidate carries a plain number, not a dev or pre-release one"
    assert found["ghp/__init__.py"] == "0.1.0"


def test_changelog_carries_the_release_date_and_the_catalogs_agree():
    """T72-AC1: the first release heading has a plain date, the workflow changelog and both catalogs carry the same one."""
    first = re.search(r"^## .*$", text(ROOT / "CHANGELOG.md"), re.M).group(0)
    m = re.fullmatch(r"## 0\.1\.0 \((\d{4}-\d\d-\d\d)\)", first)
    assert m, first
    day = m.group(1)
    assert re.search(r"^## 0\.1\.0 \(%s\)$" % day, text(ROOT / "workflows" / "ghp-lifecycle" / "CHANGELOG.md"), re.M)
    for name in ("extensions.json", "workflows.json"):
        stamps = set(re.findall(r'"(?:created|updated)_at": "([^"]+)"', text(ROOT / "catalog" / name)))
        assert stamps == {day + "T00:00:00Z"}, (name, stamps)


def test_versioning_names_the_one_number_and_the_tag_rule():
    t = flat(ROOT / "VERSIONING.md")
    for needle in ("one version number", "`src/ghp/__init__.py`", "`extension.yml`", "`workflows/ghp-lifecycle/workflow.yml`", "`catalog/extensions.json`", "`catalog/workflows.json`",
                   "`bundle/bundle.yml`", "`v0.1.0`", "Only the owner pushes a tag", "Before 1.0"):
        assert needle in t, needle


# ---- AC2 ----
def test_security_states_the_trust_limits_and_a_private_route():
    t = flat(ROOT / "SECURITY.md")
    assert "tamper-evident, not tamper-proof" in t
    assert "private vulnerability reporting" in t and "**Security** tab" in t and "Do not put details in a public issue" in t and "UNVERIFIED" in t
    assert "setuptools" in t and "build isolation" in t
    assert "Hosted runners have no token for user Projects" in t
    assert "[Trust and limits](docs/trust-and-limits.md)" in t


# ---- AC3 ----
def steps():
    body = text(CHECKLIST)
    parts = re.split(r"^### ", body, flags=re.M)[1:]
    out = []
    for p in parts:
        head, _, rest = p.partition("\n")
        rest = re.split(r"^## ", rest, flags=re.M)[0]
        out.append((head.strip(), rest))
    return out


def who(rest):
    return re.findall(r"^Who: (.+)$", rest, re.M)


def test_every_checklist_step_has_a_who_line():
    s = steps()
    assert len(s) >= 14
    for head, rest in s:
        assert len(who(rest)) == 1 and who(rest)[0] in ("Owner", "Controller"), head


def test_owner_only_steps_are_marked():
    for head, rest in steps():
        assert head.endswith("(owner only)") == (who(rest) == ["Owner"]), head


# what only the owner may do: each needle must appear in some step, and every step that names it is an owner step
OWNER_ONLY = ["Check the private repository", "Make the repository public", "github/spec-kit", "GHP_PROJECT_TOKEN", ".scrub-denylist", "git push", "delete_repo", "Auto-close",
              "Item closed", "private vulnerability reporting", "live lifecycle run"]


@pytest.mark.parametrize("needle", OWNER_ONLY)
def test_owner_only_work_is_never_a_controller_step(needle):
    named = [(head, who(rest)) for head, rest in steps() if needle in head + "\n" + rest]
    assert named, needle
    assert all(w == ["Owner"] for _, w in named), (needle, named)


def position(fragment):
    hits = [i for i, (head, _) in enumerate(steps()) if fragment in head]
    assert len(hits) == 1, fragment
    return hits[0]


def test_the_release_commit_is_dated_before_it_is_checked_and_tagged():
    """The commit that dates the changelog is the one the checks look at and the tag points at."""
    dated = position("Put the date on the changelog")
    checks = [position(f) for f in ("Read the hosted checks", "Run the full check with the private list", "Check who wrote the commits",
                                    "Review the whole branch")]
    public, reporting, tag = position("Make the repository public"), position("Turn on private vulnerability reporting"), position("Create and push the")
    assert dated < min(checks) and max(checks) < public < reporting < tag
    assert reporting < position("File the catalog entries")
    assert position("Add the checksum") > position("Attach the extension archive") > tag
    s = dict(steps())
    body = lambda frag: " ".join(s[[h for h in s if frag in h][0]].split())
    assert "This is the release commit" in body("Put the date on the changelog")
    assert "the exact commit the checks looked at" in body("Create and push the")


def test_the_checklist_does_not_claim_progress():
    t = flat(CHECKLIST)
    assert "Nothing on this list has been done yet" not in t
    assert "the steps, not their progress" in t and "already exists" in t and "skip the creation" in t


def test_the_checksum_is_of_a_release_file():
    s = dict(steps())
    body = " ".join(s[[h for h in s if "Add the checksum" in h][0]].split())
    assert "sha256" in body and "releases/download" in body and "tag archive" in body
    # The tag snapshot stays immutable; installers use main to receive the checksum.
    assert "goes only into the entry filed upstream (step 26) and into `catalog/extensions.json` on `main`" in body
    assert "served from the `v0.1.0` tag" in body and "carries no checksum" in body and "Owner decision: serve each `catalog_url` from `main`" in body
    for pin in ("test_what_is_not_known_yet_is_marked_and_nothing_is_invented", "add `sha256` once the release file exists",
                "test_extension_form_values_are_the_manifests"):
        assert pin in body, pin
    assert all(hasattr(test_catalog, name) for name in ("test_what_is_not_known_yet_is_marked_and_nothing_is_invented",
                                                        "test_extension_form_values_are_the_manifests"))


def test_the_attach_step_agrees_with_the_checksum_step():
    """The checksum lands on `main` after the tag (step 22, owner decision), so step 20 must not say the catalog checks one at install."""
    s = dict(steps())
    body = " ".join(s[[h for h in s if h.startswith("20. Attach")][0]].split())
    assert "Do not use GitHub's automatic archive of the tag: its bytes are not promised to stay the same" in body
    assert "step 22 takes the checksum it adds to `catalog/extensions.json` on `main` from the file attached here" in body
    assert "checks a checksum before an install" not in flat(CHECKLIST)


def test_the_checklist_defines_who_and_keeps_tokens_out_of_agent_hands():
    t = flat(CHECKLIST)
    for needle in ("**Owner** is the person who owns the repository", "**Controller** is the coordinating session", "never types a token",
                   "never pushes a tag", "The `0.1.0` tag is the owner's alone"):
        assert needle in t, needle


# ---- the public lint, for every new page ----
@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_new_pages_pass_the_public_lint(page):
    lint(page)


def test_the_private_list_step_warns_that_old_history_holds_the_pre_scrub_files():
    """T44-AC4: after the owner supplies the list, history is scanned too, and the public repository starts from a fresh history."""
    s = dict(steps())
    full = " ".join(s[[h for h in s if "Run the full check with the private list" in h][0]].split())
    public = " ".join(s[[h for h in s if "Make the repository public" in h][0]].split())
    assert "--history" in full and "old history still holds the files from before the clean-up" in full
    assert "fresh history" in full and "owner's decision" in full
    assert "fresh history" in public


def test_the_fresh_history_comes_from_the_export_script_and_the_owner_pushes_it():
    """T46-AC5: the fresh-history step names the one command, the review of the folder, and the owner-only remote and push."""
    s = dict(steps())
    head = [h for h in s if "Run the full check with the private list" in h][0]
    full = " ".join(s[head].split())
    assert "(owner only)" in head
    order = ["python3 scripts/export_public.py <dir>", "review the folder", "create the public repository",
             "only remote of that folder", "push"]
    at = 0
    for x in order:  # in this order, each after the one before
        at = full.lower().find(x, at)
        assert at != -1, x
    assert "never adds a remote" in full and "never pushes" in full


# ---- T54: the release files are built, checked and installed after the tag ----
RELEASE_STEPS = [  # (heading fragment, who, needles in the step's text)
    ("Create and push the", "Owner", ["git push"]),
    ("Build the extension file and the bundle file", "Controller", ["clean checkout of the `v0.1.0` tag", "python3 scripts/build_extension_zip.py <dir>",
                                                                    "byte for byte the same", "not across machines", "specify bundle validate --path bundle",
                                                                    "specify bundle build --path bundle --output <dir>", "ghp-0.1.0.zip",
                                                                    "spec-kit-ghp-extension-0.1.0.zip"]),
    ("Write the checksums file", "Controller", ["shasum -a 256 spec-kit-ghp-extension-0.1.0.zip ghp-0.1.0.zip > SHA256SUMS"]),
    ("Attach the extension archive and the bundle file", "Owner", ["spec-kit-ghp-extension-0.1.0.zip", "ghp-0.1.0.zip", "SHA256SUMS",
                                                                   "automatic archive of the tag"]),
    ("Check that every catalog address answers", "Controller", ["curl -sSIL", "200", "without signing in", "catalog_url", "raw workflow file",
                                                                "documentation and changelog"]),
    ("Add the checksum", "Controller", ["sha256", "SHA256SUMS"]),
    ("Install each part in a clean project, on two Spec Kit versions", "Controller", [
        "Spec Kit 1.2.1", "the newest Spec Kit", "specify init", "specify extension add ghp --from <download address>", "13 commands and 2 hooks",
        "specify workflow add <raw workflow address>", "specify extension add ghp --version 0.1.0", "specify workflow add ghp-lifecycle --version 0.1.0",
        "`requires`", "specify bundle install ./ghp-0.1.0.zip"]),
    ("Install the bundle by its id from a local catalog", "Controller", ["--policy install-allowed", "specify bundle install ghp", "UNVERIFIED"]),
    ("Fill in the testing details", "Controller", ["Testing Details", "TO CONFIRM", "only the boxes that were really done"]),
    ("File the catalog entries", "Owner", ["github/spec-kit"]),
]


def test_the_release_files_are_built_checked_and_installed_after_the_tag():
    at = [position(frag) for frag, _, _ in RELEASE_STEPS]
    assert at == sorted(at), [frag for frag, _, _ in RELEASE_STEPS]
    s = steps()
    assert [h.split(".", 1)[0] for h, _ in s] == [str(n) for n in range(1, len(s) + 1)]  # numbered in order, none skipped
    for (frag, owner, needles), i in zip(RELEASE_STEPS, at):
        head, rest = s[i]
        assert who(rest) == [owner], head
        body = " ".join(rest.split())
        for needle in needles:
            assert needle in body, (head, needle)


def test_the_release_commit_step_also_dates_the_catalogs():
    s = dict(steps())
    body = " ".join(s[[h for h in s if "Put the date on the changelog" in h][0]].split())
    assert "`created_at` and `updated_at`" in body and "catalog/extensions.json" in body and "catalog/workflows.json" in body


# ---- T70: which repository each step acts on after the fresh-history export ----
EVIDENCE = "https://github.com/praveenlavu/spec-kit-ghp/blob/main/docs/evidence/"


def body_of(frag):
    s = dict(steps())
    (head,) = [h for h in s if frag in h]
    return " ".join(s[head].split())


def in_order(text, parts):
    at = 0
    for x in parts:
        at = text.find(x, at)
        assert at != -1, x


def test_each_step_after_the_export_names_its_repository():
    assert "**This repository** stays private" in flat(CHECKLIST) and "makes a new root commit" in flat(CHECKLIST)
    assert position("Run the full check with the private list") < position("Read the hosted checks") < position("Check who wrote the commits")
    in_order(body_of("Run the full check with the private list"), [
        "python3 scripts/export_public.py <dir>", "git archive <release commit>", "diff -r -x .git <that folder> <dir>",
        "only the internal working notes", "Create the public repository, still private until step 15", "push from that folder"])
    hosted = body_of("Read the hosted checks")
    in_order(hosted, ["In this repository, the run on the release commit", "In the public repository, its own run on the export's commit",
                      "(" + EVIDENCE + "T42.md)"])
    assert "again in the export folder of step 10" in body_of("Check who wrote the commits")
    assert "the tree check of step 10 reports nothing but the internal working notes" in body_of("Review the whole branch")
    assert "the tag points at it" not in body_of("Put the date on the changelog")
    tag = body_of("Create and push the")
    for needle in ("In the export folder of step 10, tag its one commit `v0.1.0`", "push the tag to the public repository",
                   "not the release commit of this repository", "make a GitHub release from it in the public repository"):
        assert needle in tag, needle
    assert "`v0.1.0` tag of the public repository" in body_of("Build the extension file and the bundle file")
    checksum = body_of("Add the checksum")
    assert "a new commit on `main` of the public repository, made in the export folder" in checksum
    assert "the same change in a commit on `main` of this repository too" in checksum
    details = body_of("Fill in the testing details")
    assert "in the export folder for the public repository's `main`" in details and "the same change in this repository" in details


def test_the_live_lifecycle_step_is_done_and_points_at_its_record():
    head = [h for h, _ in steps() if "live lifecycle run" in h][0]
    assert head == "8. The live lifecycle run (owner only)"
    b = body_of("live lifecycle run")
    assert b.startswith("Who: Owner Done for 0.1.0:") and "(" + EVIDENCE + "T34.md)" in b


def test_the_consumer_run_of_the_workflows_is_the_last_step_after_going_public():
    s = steps()
    head, _ = s[-1]
    assert head == "27. Run the ready-made workflows in another repository (owner only)"
    assert position("Run the ready-made workflows") > position("Make the repository public")
    b = body_of("Run the ready-made workflows")
    for needle in ("After step 15", "at the `v0.1.0` tag", "hosted runners", "`GHP_PROJECT_TOKEN`", "only the owner creates that secret",
                   '"not yet run in another repository"', "(" + EVIDENCE + "T39.md)"):
        assert needle in b, needle
