"""T40: the catalog-ready pack (nothing is published). The catalog entries and the bundle are copies of `extension.yml` and `workflow.yml`
values (AC1); `docs/catalog-submission.md` is a draft that says nothing is published and lists what is not yet checked (AC2).

The catalog shape is the one in a local copy of Spec Kit's own repository (the catalog files and the publishing guides): that is read, not
run against the live catalog, so the draft says so."""
import importlib.util
import json
import os
import re
from pathlib import Path

import pytest

from test_docs import BANNED, FORBIDDEN, ID_ALLOWED_IN_CODE, INTERNAL_ID, _code_spans, _labels, _md_section, _text
from test_manifest import load_manifest, manifest
from test_workflow import load_workflow

ROOT = Path(__file__).resolve().parent.parent
DRAFT = ROOT / "docs" / "catalog-submission.md"
TRUST = ROOT / "docs" / "trust-and-limits.md"
EMAIL = re.compile(r"[\w.+-]+@(?!v?\d)[\w-]+\.[\w.-]+")  # `repo@v0.1.0` (a git tag) and `ghp@0.1.0` (a pinned part) are not addresses
BUNDLE_README = ROOT / "bundle" / "README.md"


def catalog(name):
    return json.loads((ROOT / "catalog" / name).read_text(encoding="utf-8"))


def workflow():
    return load_workflow((ROOT / "workflows" / "ghp-lifecycle" / "workflow.yml").read_text(encoding="utf-8"))


def bundle():
    return load_manifest((ROOT / "bundle" / "bundle.yml").read_text(encoding="utf-8"))


def lint(page, allowed=()):
    """The public-docs rules of the docs tests, for a page that is not in their fixed list; also used by test_release_checklist."""
    spec = importlib.util.spec_from_file_location("scrub_check", ROOT / "scripts" / "scrub_check.py")
    scrub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scrub)
    t = _text(page)
    assert scrub.scan_text(page.name, t, scrub.load_denylist(os.environ, ROOT / scrub.DENYLIST_FILE)) == []
    assert not EMAIL.search(t), "%s: an email address" % page.name
    for pat in FORBIDDEN:
        assert not pat.search(t), "%s: %s" % (page.name, pat.pattern)
    code = _code_spans(t)
    links = [m.span(1) for m in re.finditer(r"\]\(([^)\s]+)\)", t)]
    for m in INTERNAL_ID.finditer(t):
        if any(a <= m.start() < b for a, b in links):
            continue
        assert any(a <= m.start() < b for a, b in code) and m.group(0) in ID_ALLOWED_IN_CODE, "%s: internal id %s" % (page.name, m.group(0))
    prose = t
    for a in allowed:  # an exact string the page needs (a tag name) is not prose
        prose = prose.replace(a, " " * len(a))
    for pat in BANNED:
        assert not pat.search(prose), "%s: %s" % (page.name, pat.pattern)
    for target in re.findall(r"\]\(([^)\s]+)\)", t):
        if not re.match(r"(https?:|mailto:)", target):
            assert (page.parent / target.split("#")[0]).exists(), "%s: broken link %s" % (page.name, target)


# ---- AC1 ----
def test_catalog_entries_match_manifests():
    ext, wf = manifest(), workflow()
    e = catalog("extensions.json")["extensions"]
    assert list(e) == ["ghp"]
    e = e["ghp"]
    x = ext["extension"]
    for key in ("id", "name", "description", "version", "author", "repository", "homepage", "license"):
        assert e[key] == x[key], key
    assert e["requires"]["speckit_version"] == ext["requires"]["speckit_version"] == ">=1.2.1"
    assert e["requires"]["tools"] == ext["requires"]["tools"]
    assert e["provides"] == {"commands": len(ext["provides"]["commands"]), "hooks": len(ext["hooks"])}
    assert e["provides"]["commands"] == 13
    assert e["tags"] == ext["tags"]
    w = catalog("workflows.json")["workflows"]
    assert list(w) == ["ghp-lifecycle"]
    w = w["ghp-lifecycle"]
    y = wf["workflow"]
    for key in ("id", "name", "description", "version", "author"):
        assert w[key] == y[key], key
    assert w["requires"]["speckit_version"] == ">=1.2.1"
    assert w["repository"] == x["repository"] and w["license"] == x["license"]
    assert w["tags"] and set(w["tags"]) <= set(ext["tags"])


def test_catalog_requires_equal_the_manifests():
    """T48: Spec Kit refuses a pinned or bundle workflow install whose downloaded `requires` differs from the catalog entry's
    (`specify_cli/workflows/_commands.py`), so each entry carries its manifest's whole `requires` block, unchanged."""
    assert catalog("extensions.json")["extensions"]["ghp"]["requires"] == manifest()["requires"]
    assert catalog("workflows.json")["workflows"]["ghp-lifecycle"]["requires"] == workflow()["requires"]
    assert "integrations" in workflow()["requires"]  # the key the old entry dropped


def test_the_extension_file_and_the_bundle_file_have_different_names():
    """T49: `specify bundle build` names its file `<bundle id>-<version>.zip` (specify_cli/bundles/packager.py); both files go on one release, so
    the extension file has its own name, and the checklist and the submission page use that name."""
    e, b = catalog("extensions.json")["extensions"]["ghp"], bundle()["bundle"]
    name = e["download_url"].rsplit("/", 1)[1]
    assert name != "%s-%s.zip" % (b["id"], b["version"])
    assert name == "spec-kit-ghp-extension-%s.zip" % e["version"]
    for page in (ROOT / "docs" / "release-checklist.md", DRAFT):
        assert name in _text(page), page.name


def test_catalog_addresses_name_the_release_tag():
    ext, wf = manifest()["extension"], workflow()["workflow"]
    e = catalog("extensions.json")["extensions"]["ghp"]
    w = catalog("workflows.json")["workflows"]["ghp-lifecycle"]
    # a file attached to the release, never GitHub's automatic archive of the tag (its bytes are not promised to stay the same)
    assert e["download_url"] == "%s/releases/download/v%s/spec-kit-ghp-extension-%s.zip" % (ext["repository"], ext["version"], ext["version"])
    assert w["url"] == "https://raw.githubusercontent.com/praveenlavu/spec-kit-ghp/v%s/workflows/ghp-lifecycle/workflow.yml" % wf["version"]
    assert e["verified"] is False  # only the Spec Kit maintainers can verify an entry


def test_checksum_is_absent_or_real_and_points_at_a_release_file():
    """No release file exists yet, so there is no checksum; once there is one it must be a SHA-256 of a release file, never of the tag archive."""
    e = catalog("extensions.json")["extensions"]["ghp"]
    assert re.fullmatch(r"[0-9a-f]{64}", e["sha256"])
    assert "/releases/download/" in e["download_url"] and "/archive/" not in e["download_url"]


def test_bundle_pins_the_manifest_versions():
    ext, wf, b = manifest(), workflow(), bundle()
    assert b["schema_version"] == "1.0"
    assert b["bundle"]["version"] == ext["extension"]["version"] == wf["workflow"]["version"]
    assert b["bundle"]["id"] == "ghp" and b["bundle"]["author"] == ext["extension"]["author"] and b["bundle"]["license"] == ext["extension"]["license"]
    assert b["requires"]["speckit_version"] == ext["requires"]["speckit_version"]
    assert b["provides"]["extensions"] == [{"id": ext["extension"]["id"], "version": ext["extension"]["version"]}]
    assert b["provides"]["workflows"] == [{"id": wf["workflow"]["id"], "version": wf["workflow"]["version"]}]
    assert b["requires"]["tools"] == [x["name"] for x in ext["requires"]["tools"]]
    assert b["tags"] and set(b["tags"]) <= set(ext["tags"])


def test_bundle_readme_pins_the_components_and_passes_the_lint():
    """The bundle page states the role, the two pinned parts, the install steps and that the release exists; its values are the manifest's."""
    b, e = bundle(), catalog("extensions.json")["extensions"]["ghp"]
    t = _text(BUNDLE_README)
    flat = " ".join(t.split())
    assert "Released as `v0.1.1`." in t and "Not published yet" not in flat and "No release exists" not in flat
    assert "for a %s" % b["bundle"]["role"] in flat
    for kind, part in (("Extension", b["provides"]["extensions"][0]), ("Workflow", b["provides"]["workflows"][0])):
        assert "| %s | `%s` | %s |" % (kind, part["id"], part["version"]) in t, kind
        assert "`%s@%s`" % (part["id"], part["version"]) in t, kind
    v = b["bundle"]["version"]
    assert "pipx install git+https://github.com/praveenlavu/spec-kit-ghp@v%s" % v in t
    for name, verb in (("extensions.json", "extension"), ("workflows.json", "workflow")):
        assert "specify %s catalog add %s" % (verb, catalog(name)["catalog_url"]) in t, name
    artifact = "%s-%s.zip" % (b["bundle"]["id"], v)  # the name `specify bundle build` gives it
    assert "specify bundle install ./%s" % artifact in t and "/releases/download/v%s/%s" % (v, artifact) in t
    assert e["download_url"] in t  # installing the parts on their own uses the extension's own file
    lint(BUNDLE_README)


def test_the_address_check_reads_a_git_tag_as_no_email():
    assert not EMAIL.search("pipx install git+https://github.com/o/r@v0.1.0") and not EMAIL.search("pins `ghp@0.1.0`")
    assert EMAIL.search("write to someone@example.org") and EMAIL.search("a@vendor.example")


WF_DIR = ROOT / "workflows" / "ghp-lifecycle"


def test_workflow_folder_has_the_license_and_a_changelog():
    """T52: Spec Kit's workflow guide marks LICENSE required in the workflow folder and CHANGELOG.md recommended."""
    assert (WF_DIR / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
    newest = re.search(r"^## (\S+)(.*)$", _text(WF_DIR / "CHANGELOG.md"), re.M)
    assert newest.group(1) == workflow()["workflow"]["version"] and re.fullmatch(r" \(\d{4}-\d\d-\d\d\)", newest.group(2))
    assert "[repository changelog](../../CHANGELOG.md)" in _text(WF_DIR / "CHANGELOG.md")
    lint(WF_DIR / "CHANGELOG.md")


def test_catalog_files_are_not_installed_with_the_extension():
    ignore = (ROOT / ".extensionignore").read_text(encoding="utf-8").splitlines()
    assert "catalog/" in ignore and "bundle/" in ignore


# ---- AC2 ----
def test_draft_says_it_is_a_draft_and_nothing_is_published():
    t = _text(DRAFT)
    assert "DRAFT" in "\n".join(t.splitlines()[:3])
    flat = " ".join(t.split())
    assert "Nothing is filed with Spec Kit" in flat and "whether to publish is the owner's decision" in flat.lower()
    assert "the release `v0.1.0` exists" in flat.lower()
    assert "**Verified" not in t  # nothing in this draft is a verified result
    assert "not GitHub's automatic archive" in flat  # the address is a release file
    assert "installed by its id from a local catalog" in flat
    assert "Every value is copied" not in flat  # the download address and the verified flag are not copied


def test_draft_lists_every_unverified_item_of_the_trust_page():
    trust = _labels(_md_section(_text(TRUST), "Unverified in live runs"))
    draft = _labels(_md_section(_text(DRAFT), "Not yet checked on real GitHub"))
    assert draft == trust and len(draft) >= 38  # 40 until T45 moved the two hosted-CI items to verified
    own = _labels(_md_section(_text(DRAFT), "Not yet checked for the catalog"))
    assert len(own) >= 11 and "Optional fields." in own and "The bundle file and its catalogs." in own


def test_draft_names_where_each_entry_is_filed():
    flat = " ".join(_text(DRAFT).split())
    for needle in ("catalog/extensions.json", "catalog/workflows.json", "bundle/bundle.yml", "issue form", "pull request"):
        assert needle in flat, needle


@pytest.mark.parametrize("name", ["extensions.json", "workflows.json"])
def test_catalog_files_hold_no_email_or_secret(name):
    t = (ROOT / "catalog" / name).read_text(encoding="utf-8")
    assert not EMAIL.search(t)
    assert not re.search(r"gh[pousr]_[A-Za-z0-9]{36,}|github_pat_", t)


# ---- T53: the paste-ready forms ----
FENCE = re.compile(r"^```([\w-]*)\n(.*?)^```", re.M | re.S)
TAG_HITL = ("issue-tracking, hitl, workflow", '"hitl",')  # the catalog tag `hitl` is a tag name, not prose (the lint bans the word in prose)
EXT_CHECKS = ["Extension installs successfully via download URL", "All commands execute without errors", "Documentation is complete and accurate",
              "No security vulnerabilities identified", "Tested on at least one real project"]
EXT_REQS = ["Valid `extension.yml` manifest included", "README.md with installation and usage instructions", "LICENSE file included",
            "GitHub release created with version tag", "All command files exist and are properly formatted",
            "Extension ID follows naming conventions (lowercase-with-hyphens)"]
WF_CHECKS = ["Valid workflow.yml (passes `specify workflow info`)", "README.md with description, inputs, and step graph", "LICENSE file included",
             "GitHub release created with raw YAML URL", "Workflow tested end-to-end with `specify workflow run`", "All gate steps have clear review messages",
             "Input prompts are descriptive", "Added to workflows/catalog.community.json (alphabetical order)"]
BUNDLE_CHECKS = ["Validation succeeds with `specify bundle validate --path <bundle-directory>`",
                 "Build succeeds with `specify bundle build --path <bundle-directory>` and produces the submitted artifact",
                 "Bundle installs successfully from the built artifact",
                 "The submitted distribution path was tested end to end, including bundle-ID installation from an install-allowed catalog when a catalog "
                 "entry is proposed",
                 "Installation was tested in a clean Spec Kit project",
                 "Required component catalogs are documented and were included in testing, or no extra catalogs are required",
                 "Documentation is complete and accurate"]
BUNDLE_REQS = ["Valid `bundle.yml` manifest included", "README.md explains the bundle's intended role, installed components, and installation steps",
               "LICENSE file included", "GitHub release created with a version tag", "Bundle ID matches the manifest and follows naming conventions",
               "Every extension, preset, workflow, and step reference is pinned where the manifest requires a version"]


def _form(heading):
    """The text under `## <heading>` up to the next `## ` heading that is not inside a fenced block (the pull request body holds one)."""
    t = _text(DRAFT)
    fences = [m.span() for m in FENCE.finditer(t)]
    start = t.index("\n## %s\n" % heading)
    ends = [m.start() for m in re.finditer(r"^## ", t, re.M) if m.start() > start + 1 and not any(a <= m.start() < b for a, b in fences)]
    return t[start:ends[0] if ends else len(t)]


def _fields(section):
    return dict(re.findall(r"^\| ([^|]+?) \| `([^`]*)` \|$", section, re.M))


def _block(section, label):
    """The first fenced block after the bold label `**<label>**`."""
    m = re.search(r"^\*\*%s\*\*[^\n]*\n\n```([\w-]*)\n(.*?)^```" % re.escape(label), section, re.M | re.S)
    assert m, label
    return m.group(2)


def _boxes(section, label):
    m = re.search(r"^\*\*%s\*\*[^\n]*\n\n((?:- \[[ x]\] [^\n]*\n)+)" % re.escape(label), section, re.M)
    assert m, label
    return re.findall(r"^- \[[ x]\] (.*)$", m.group(1), re.M)


def tag_url(version, path):
    return "https://raw.githubusercontent.com/praveenlavu/spec-kit-ghp/v%s/%s" % (version, path)


def blob_url(version, path):
    assert (ROOT / path).is_file(), path  # a documentation or changelog address names a file this repository has
    return "https://github.com/praveenlavu/spec-kit-ghp/blob/v%s/%s" % (version, path)


def test_catalog_files_carry_the_address_and_dates_fields():
    stamp = re.compile(r"\d{4}-\d\d-\d\dT00:00:00Z")
    e, w = catalog("extensions.json"), catalog("workflows.json")
    v = manifest()["extension"]["version"]
    assert e["catalog_url"] == "https://raw.githubusercontent.com/praveenlavu/spec-kit-ghp/main/catalog/extensions.json"
    assert w["catalog_url"] == "https://raw.githubusercontent.com/praveenlavu/spec-kit-ghp/main/catalog/workflows.json"
    ext, wf = e["extensions"]["ghp"], w["workflows"]["ghp-lifecycle"]
    assert ext["documentation"] == blob_url(v, "README.md") and ext["changelog"] == blob_url(v, "CHANGELOG.md")
    assert wf["documentation"] == blob_url(v, "workflows/ghp-lifecycle/README.md")
    assert wf["changelog"] == blob_url(v, "workflows/ghp-lifecycle/CHANGELOG.md")
    dates = {e["updated_at"], w["updated_at"], ext["created_at"], ext["updated_at"], wf["created_at"], wf["updated_at"]}
    assert len(dates) == 1 and stamp.fullmatch(dates.pop())  # one date, the release date (set with the changelog date)


def test_extension_form_values_are_the_manifests():
    sec, x, e = _form("1. Extension submission"), manifest(), catalog("extensions.json")["extensions"]["ghp"]
    m = x["extension"]
    assert _fields(sec) == {
        "Issue title": "[Extension]: Add %s" % m["id"], "Extension ID": m["id"], "Extension Name": m["name"], "Version": m["version"],
        "Description": m["description"], "Author": m["author"], "Repository URL": m["repository"], "Download URL": e["download_url"],
        "License": m["license"], "Homepage": m["homepage"], "Documentation URL": e["documentation"], "Changelog URL": e["changelog"],
        "Required Spec Kit Version": x["requires"]["speckit_version"], "Number of Commands": str(len(x["provides"]["commands"])),
        "Number of Hooks": str(len(x["hooks"])), "Tags": ", ".join(x["tags"])}
    assert json.loads(_block(sec, "Proposed Catalog Entry")) == {"ghp": e}
    tools = _block(sec, "Required Tools").splitlines()
    assert [ln.split()[1] for ln in tools] == [t["name"] for t in x["requires"]["tools"]]
    for ln, t in zip(tools, x["requires"]["tools"]):
        assert ("(%s) - required" % t["version"] if "version" in t else " - required") in ln and t["required"] is True
    assert "pipx install git+https://github.com/praveenlavu/spec-kit-ghp@v%s" % m["version"] in tools[0]
    assert _boxes(sec, "Testing Checklist") == EXT_CHECKS and _boxes(sec, "Submission Requirements") == EXT_REQS
    usage = _block(sec, "Example Usage")
    assert "specify extension add %s --from %s" % (m["id"], e["download_url"]) in usage


def test_workflow_pull_request_values_are_the_manifests():
    sec, y, w = _form("2. Workflow submission"), workflow()["workflow"], catalog("workflows.json")["workflows"]["ghp-lifecycle"]
    assert _fields(sec) == {"Pull request title": "Add %s to community catalog" % y["id"], "Branch": "add-%s" % y["id"], "Workflow ID": y["id"],
                            "Workflow Name": y["name"], "Version": y["version"], "Repository": w["repository"], "Raw workflow URL": w["url"]}
    assert json.loads(_block(sec, "Catalog entry")) == {"ghp-lifecycle": w}
    body = _block(sec, "Pull request body")
    for needle in ("**Workflow Name**: %s" % y["name"], "**Workflow ID**: %s" % y["id"], "**Version**: %s" % y["version"], "**Repository**: %s" % w["repository"]):
        assert needle in body, needle
    assert re.findall(r"^- \[[ x]\] (.*)$", body, re.M) == WF_CHECKS
    assert "workflows/catalog.community.json" in sec and "sorted by id" in sec


def test_bundle_form_values_are_the_manifests():
    sec, b, x = _form("3. Bundle submission"), bundle(), manifest()
    m, v = b["bundle"], b["bundle"]["version"]
    download = "https://github.com/praveenlavu/spec-kit-ghp/releases/download/v%s/%s-%s.zip" % (v, m["id"], v)  # what `specify bundle build` names it
    assert {k: f for k, f in _fields(sec).items()} == {
        "Issue title": "[Bundle]: Add %s" % m["id"], "Bundle ID": m["id"], "Bundle Name": m["name"], "Version": v, "Role or Team": m["role"],
        "Description": m["description"], "Author": m["author"], "Repository URL": x["extension"]["repository"], "Download URL": download,
        "Documentation URL": blob_url(v, "bundle/README.md"), "License": m["license"], "Required Spec Kit Version": b["requires"]["speckit_version"],
        "Tags": ", ".join(b["tags"])}
    assert "| Integration Target | leave empty" in sec
    assert _block(sec, "Components Provided").splitlines() == (["- extensions: %s@%s" % (p["id"], p["version"]) for p in b["provides"]["extensions"]]
                                                               + ["- workflows: %s@%s" % (p["id"], p["version"]) for p in b["provides"]["workflows"]])
    assert _block(sec, "Required Component Catalogs").splitlines() == ["- Extensions: " + catalog("extensions.json")["catalog_url"],
                                                                       "- Workflows: " + catalog("workflows.json")["catalog_url"]]
    assert json.loads(_block(sec, "Proposed Catalog Entry")) == {m["id"]: {
        "id": m["id"], "name": m["name"], "version": v, "role": m["role"], "description": m["description"], "author": m["author"],
        "license": m["license"], "download_url": download, "repository": x["extension"]["repository"],
        "requires": {"speckit_version": b["requires"]["speckit_version"]},
        "provides": {"extensions": len(b["provides"]["extensions"]), "presets": 0, "steps": 0, "workflows": len(b["provides"]["workflows"])},
        "tags": b["tags"], "verified": False}}
    assert _boxes(sec, "Testing Checklist") == BUNDLE_CHECKS and _boxes(sec, "Submission Requirements") == BUNDLE_REQS
    usage = _block(sec, "Example Usage")
    assert "specify bundle install ./%s-%s.zip" % (m["id"], v) in usage
    for name in ("extensions.json", "workflows.json"):
        assert catalog(name)["catalog_url"] in usage


def test_what_is_not_known_yet_is_marked_and_nothing_is_invented():
    t = _text(DRAFT)
    assert "TO CONFIRM" not in t  # every placeholder was replaced by what was run
    for heading in ("1. Extension submission", "3. Bundle submission"):
        details = _block(_form(heading), "Testing Details")
        assert "Spec Kit 1.2.1" in details and "Spec Kit 1.4.0" in details, heading
    assert catalog("extensions.json")["extensions"]["ghp"]["sha256"] == "24f87a15f8ce3b21db462f1599cebe9d087ab7e3ca71030e36efc97c8cd77488" and "\"sha256\": \"24f87a15f8ce3b21db462f1599cebe9d087ab7e3ca71030e36efc97c8cd77488\"" in t  # the line of SHA256SUMS
    assert "add `sha256` once the release file exists" not in t


@pytest.mark.parametrize("page", [DRAFT], ids=lambda p: p.name)
def test_the_submission_page_passes_the_lint_with_the_tag_allowed(page):
    lint(page, allowed=TAG_HITL)


def test_the_catalog_wording_says_main_not_the_tag():
    """T66-AC1: the catalogs are served from main (decision T64); no page still says the release serves them from its tag."""
    for page in ("bundle/README.md", "docs/catalog-submission.md"):
        t = " ".join((ROOT / page).read_text(encoding="utf-8").split())
        assert "serves from its tag" not in t and "serves two install-allowed catalogs from its tag" not in t, page
        assert "`main` branch" in t, page


def test_only_the_boxes_that_were_really_done_are_ticked():
    """A tick is a claim about the published release; the judgements (documentation, security, gate wording, the catalog pull request) stay empty."""
    t = _text(DRAFT)
    empty = [l[6:] for l in t.splitlines() if l.startswith("- [ ] ")]
    assert sorted(empty) == sorted(["All commands execute without errors", "Documentation is complete and accurate", "No security vulnerabilities identified",
        "Tested on at least one real project", "All gate steps have clear review messages", "Input prompts are descriptive",
        "Added to workflows/catalog.community.json (alphabetical order)", "Documentation is complete and accurate"])
