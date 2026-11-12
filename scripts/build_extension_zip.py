#!/usr/bin/env python3
"""Build the extension archive that the release carries: HEAD's tracked files, minus what `.extensionignore` keeps out.

Usage: python3 scripts/build_extension_zip.py <out_dir>

Writes <out_dir>/spec-kit-ghp-extension-<version>.zip (the version is read from `extension.yml`). The name differs from the
bundle file that `specify bundle build` makes (`<bundle id>-<version>.zip`), so both can be attached to one release.
The archive is the same bytes on every run from the same commit: entries sorted by path, one fixed timestamp, and two fixed
modes (0755 for a file git marks executable, 0644 otherwise). Standard library only, offline; it never calls gh.
Exit: 0 done, 2 refused, 3 git failed.
"""
from __future__ import annotations

import io
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
NAME = "spec-kit-ghp-extension-%s.zip"
STAMP = (1980, 1, 1, 0, 0, 0)  # the earliest time a zip can hold


def patterns(text: str) -> List[str]:
    """The `.extensionignore` lines this script understands; anything else is refused rather than guessed."""
    out = []
    for line in text.splitlines():
        line = line.strip().replace("\\", "/")
        if not line or line.startswith("#"):
            continue
        # shortcut: no wildcards or `!` negations (the file has none); add a full gitignore matcher if it ever needs them
        if re.search(r"[*?\[\]!]", line):
            raise ValueError("unsupported .extensionignore pattern: %r" % line)
        out.append(line)
    return out


def ignored(rel: str, pats: List[str]) -> bool:
    """Git-style: `x/` is a folder anywhere (anchored if it holds another `/`), `x` a file or folder; a leading `/` anchors."""
    parts = rel.split("/")
    for p in pats:
        folder = p.endswith("/")
        body = p.strip("/")
        anchored = p.startswith("/") or "/" in body
        n = body.count("/") + 1
        starts = [0] if anchored else range(len(parts))
        for i in starts:
            hit = parts[i:i + n] == body.split("/")
            if hit and (not folder or i + n < len(parts)):
                return True
    return rel == ".extensionignore"  # Spec Kit always leaves the ignore file itself out


def zip_mode(member: tarfile.TarInfo) -> int:
    """The zip external attribute: 0755 for a file git marks executable, 0644 otherwise."""
    return (0o100755 if member.mode & 0o111 else 0o100644) << 16


def build(out_dir: Path) -> Path:
    tar = tarfile.open(fileobj=io.BytesIO(subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=ROOT, check=True,
                                                         capture_output=True).stdout))
    files = {m.name: m for m in tar.getmembers() if m.isfile()}
    if "extension.yml" not in files or ".extensionignore" not in files:
        raise ValueError("HEAD has no extension.yml or .extensionignore")
    pats = patterns(tar.extractfile(files[".extensionignore"]).read().decode("utf-8-sig"))
    version = re.search(r'^  version: "([^"]+)"$', tar.extractfile(files["extension.yml"]).read().decode("utf-8"), re.M).group(1)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / (NAME % version)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in sorted(files):
            if ignored(rel, pats):
                continue
            info = zipfile.ZipInfo(rel, date_time=STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3  # unix, so the mode below is read on every platform
            info.external_attr = zip_mode(files[rel])
            z.writestr(info, tar.extractfile(files[rel]).read())
    return path


def main(argv: List[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        print(build(Path(argv[1]).expanduser().resolve()))
    except subprocess.CalledProcessError as e:
        print("build_extension_zip: git failed: %s" % e.stderr.decode(errors="replace").strip(), file=sys.stderr)
        return 3
    except ValueError as e:
        print("build_extension_zip: %s" % e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
