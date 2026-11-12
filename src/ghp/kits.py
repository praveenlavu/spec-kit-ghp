"""Kit digests and `ghp kits verify` (local, read-only): which Spec Kit extensions are installed, what a human pinned in
`ghp.kits.lock.json`, and whether the kit's files and the agent copies Spec Kit registered still match. Everything read from the repo
(the `.registry`, kit files, agent copies, the lockfile) is hostile: no symlink is followed, reads are confined and capped, a path with a
control character is refused before any hashing, and errors and findings are fixed texts that carry no kit text. Process enforcement,
not a security boundary: a local digest says "unchanged since a human looked", not "safe"."""
import hashlib
import json
import os
import re
import stat
import sys

from . import stack, strict
from .config import ConfigInvalid, _has_control

LOCK_FILE = "ghp.kits.lock.json"
REGISTRY = ".specify/extensions/.registry"
OWN_EXTENSION = "ghp"  # the toolkit's own extension id: installed with it, reviewed as code, never a kit to pin (local and CI rules alike)
MAX_REGISTRY = 1 << 20   # bytes
MAX_FILE = 16 << 20      # bytes per kit file or agent copy; larger is refused (fail closed)
MAX_FILES = 5000         # kit files hashed per kit
_REASONS = ("symlink", "unreadable", "empty", "control-char", "attributes", "unknown", "incomplete-scan")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")  # a registered command or skill name: one path component
_VERSION = re.compile(r"[0-9A-Za-z.+-]{1,40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_BLOB = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_CHANGED = "changed"  # dir_digest_at's answer for a symlink, submodule, odd mode or odd path: never equal to a pinned digest
FIX = "review the kit, then have a human run `ghp kits pin <id> --apply` in their own terminal window (`ghp kits pin <id>` previews it)"
SYMLINK_HINT = " (a `--dev` install links its agent copies, so it cannot be pinned: install the kit normally)"


class KitError(Exception):
    """A kit input that cannot be trusted; `reason` is one of symlink, unreadable, empty, control-char, attributes, unknown, incomplete-scan (the message is that word only)."""

    def __init__(self, reason):
        assert reason in _REASONS
        super().__init__(reason)
        self.reason = reason


def _stat(root, rel):
    """lstat of `rel` (a repo-relative path) with every component checked: a symlink anywhere on the way is KitError("symlink"),
    an entry that does not exist is FileNotFoundError, anything else KitError("unreadable")."""
    path, st = str(root), None
    for part in rel.split("/"):
        path = os.path.join(path, part)
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            raise
        except OSError:
            raise KitError("unreadable") from None
        if stat.S_ISLNK(st.st_mode):
            raise KitError("symlink")
    return st


def _read_regular(root, rel, cap, missing_ok=False):
    """The bytes of regular file `rel` under `root` (no symlink, at most `cap` bytes); None when absent and `missing_ok`."""
    try:
        st = _stat(root, rel)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise KitError("unreadable") from None
    if not stat.S_ISREG(st.st_mode) or st.st_size > cap:
        raise KitError("unreadable")
    try:
        fd = os.open(os.path.join(str(root), rel), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        raise KitError("unreadable") from None
    with os.fdopen(fd, "rb") as f:
        data = f.read(cap + 1)
    if len(data) > cap:
        raise KitError("unreadable")
    return data


def _check_id(kit_id):
    if not (isinstance(kit_id, str) and stack._ID.fullmatch(kit_id)):
        raise KitError("unreadable")
    return kit_id


def _names(v):
    if v is None:
        return []
    if not (isinstance(v, list) and all(isinstance(n, str) and _NAME.fullmatch(n) for n in v)):
        raise ValueError
    return list(v)


def installed(root="."):
    """The kits in `.specify/extensions/.registry`: `[{"id", "enabled", "version", "registered": {"commands", "skills"}}]` in registry
    order, `ghp` left out. A missing `enabled` is True (as upstream). An entry whose id fails `[a-z0-9-]{1,40}` or whose fields are
    malformed becomes `{"id": None, "n": <1-based position>}` and is reported unpinned, never skipped. No registry: `[]`. A registry that
    is a symlink, not a regular file, over 1 MiB, not valid JSON, has a duplicate key or the wrong shape: KitError (fail closed)."""
    raw = _read_regular(root, REGISTRY, MAX_REGISTRY, missing_ok=True)
    if raw is None:
        return []
    try:
        data = stack.parse_json(raw, MAX_REGISTRY)
    except ValueError:
        raise KitError("unreadable") from None
    if not (isinstance(data, dict) and isinstance(data.get("extensions"), dict)):
        raise KitError("unreadable")
    out = []
    for n, (kit_id, e) in enumerate(data["extensions"].items(), 1):
        if kit_id == OWN_EXTENSION:
            continue
        try:
            if not (isinstance(kit_id, str) and stack._ID.fullmatch(kit_id) and isinstance(e, dict)):
                raise ValueError
            enabled = e.get("enabled", True)
            rc = e.get("registered_commands", {})
            if not (isinstance(enabled, bool) and isinstance(rc, dict)):
                raise ValueError
            version = e.get("version")
            out.append({"id": kit_id, "enabled": enabled, "version": version if isinstance(version, str) and _VERSION.fullmatch(version) else None,
                        "registered": {"commands": _names(rc.get("claude")), "skills": _names(e.get("registered_skills"))}})
        except ValueError:
            out.append({"id": None, "n": n})
    return out


def _digest(entries):
    """sha256 over sorted `<blob id>  <relpath>` lines (blob ids are SHA-1 in SHA-1 repositories)."""
    h = hashlib.sha256()
    for blob, rel in sorted(entries, key=lambda t: t[1].encode("utf-8")):
        h.update(f"{blob}  {rel}\n".encode("utf-8"))
    return h.hexdigest()


def _excluded(kit_id, rel):
    """Project settings the user edits, not vetted: only at the top of the kit dir."""
    return "/" not in rel and (rel == "local-config.yml" or rel.endswith("-config.local.yml") or rel == f"{kit_id}-config.yml")


def _paths(raw, prefix):
    """The NUL-separated paths in `raw` as text; a path that is not UTF-8 is unreadable, one with a control character is refused."""
    out = []
    for b in raw.split(b"\0"):
        if not b:
            continue
        try:
            text = b.decode("utf-8")
        except UnicodeDecodeError:
            raise KitError("unreadable") from None
        if _has_control(text):
            raise KitError("control-char")
        if not text.startswith(prefix):
            raise KitError("unreadable")
        out.append(text)
    return out


def dir_digest(root, kit_id):
    """The digest of the regular files git tracks or would track under `.specify/extensions/<id>/` in the working tree (same set as the
    commit once committed), minus local and kit config files. KitError: symlink or non-regular file, unreadable, control-char path,
    empty set. Blob ids come from `git hash-object --stdin-paths`, so CRLF/`core.autocrlf` give the committed value."""
    prefix = f".specify/extensions/{_check_id(kit_id)}/"
    _stat_dir(root, prefix[:-1])
    listed = strict._git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--end-of-options", "--", prefix)
    if listed is None or listed.returncode:
        raise KitError("unreadable")
    listed_paths = sorted(set(_paths(listed.stdout, prefix)))  # all checked before git hashes
    if len(listed_paths) > MAX_FILES:
        raise KitError("unreadable")
    for p in listed_paths:  # excluded files too: a symlinked <id>-config.yml is refused, not skipped
        try:
            st = _stat(root, p)
        except FileNotFoundError:
            raise KitError("unreadable") from None
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE:
            raise KitError("unreadable")
    paths = [p for p in listed_paths if not _excluded(kit_id, p[len(prefix):])]
    if not paths:
        raise KitError("empty")
    _refuse_content_attributes(root, paths)
    hashed = strict._git(root, "hash-object", "--stdin-paths", stdin="".join(p + "\n" for p in paths).encode("utf-8"), timeout=120)
    ids = hashed.stdout.decode("ascii", "replace").split("\n")[:-1] if hashed is not None and not hashed.returncode else []
    if len(ids) != len(paths) or not all(_BLOB.fullmatch(i) for i in ids):
        raise KitError("unreadable")
    return _digest(zip(ids, (p[len(prefix):] for p in paths)))


def _refuse_content_attributes(root, paths):
    """KitError("attributes") when `.gitattributes` (or any attributes source) sets `ident`, `filter` or `working-tree-encoding` on a kit
    path: `hash-object` would run that rewrite, so the local digest could differ from the committed one (or run a filter driver).
    `text` and `eol` stay allowed: they are what makes CRLF and `core.autocrlf` agree with the commit."""
    r = strict._git(root, "check-attr", "-z", "--stdin", "ident", "filter", "working-tree-encoding", stdin="\0".join(paths).encode("utf-8") + b"\0")
    fields = r.stdout.split(b"\0")[:-1] if r is not None and not r.returncode else None
    if fields is None or len(fields) != 9 * len(paths):
        raise KitError("unreadable")
    if any(v not in (b"unspecified", b"unset") for v in fields[2::3]):
        raise KitError("attributes")


def _stat_dir(root, rel):
    try:
        st = _stat(root, rel)
    except FileNotFoundError:
        raise KitError("empty") from None
    if not stat.S_ISDIR(st.st_mode):
        raise KitError("unreadable")


def dir_digest_at(repo, commit, kit_id):
    """The same digest from the files of `commit` (a 40-hex id; ValueError otherwise) that git tracks under the kit dir: what CI sees.
    None when the commit tracks no (non-excluded) file there; "changed" when an entry is a symlink or submodule (mode 120000/160000),
    any other non-regular mode or a path with a control character; KitError("unreadable") when git cannot answer."""
    if not (isinstance(commit, str) and re.fullmatch(r"[0-9a-fA-F]{40}", commit)):
        raise ValueError("commit must be a resolved 40-hex commit id")
    prefix = f".specify/extensions/{_check_id(kit_id)}/"
    listed = strict._git(repo, "ls-tree", "-r", "-z", "--full-tree", "--end-of-options", commit, "--", prefix)
    if listed is None or listed.returncode:
        raise KitError("unreadable")
    entries = []
    for rec in listed.stdout.split(b"\0"):
        if not rec:
            continue
        meta, _, raw_path = rec.partition(b"\t")
        fields = meta.decode("ascii", "replace").split(" ")
        try:
            path = raw_path.decode("utf-8")
        except UnicodeDecodeError:
            return _CHANGED
        if len(fields) != 3 or not path.startswith(prefix):
            raise KitError("unreadable")
        rel = path[len(prefix):]
        if _has_control(path):
            return _CHANGED
        if fields[0] not in ("100644", "100755") or fields[1] != "blob" or not _BLOB.fullmatch(fields[2]):
            return _CHANGED  # judged before the exclusion: a symlinked <id>-config.yml is changed, not skipped
        if _excluded(kit_id, rel):
            continue
        entries.append((fields[2], rel))
    return _digest(entries) if entries else None


def _agent_files(root, registered):
    """The Claude copies Spec Kit registered, by the exact registered names (never a glob: `sf` vs `sf-extra`). A skill name is
    `.claude/skills/<name>/SKILL.md`; a command name `speckit.<a>.<b>` is the skill `speckit-<a>-<b>` when that exists (Spec Kit's
    skills layout, what a current install writes), else `.claude/commands/<name>.md`."""
    paths = set()
    for name in registered.get("skills", []):
        paths.add(f".claude/skills/{name}/SKILL.md")
    for name in registered.get("commands", []):
        skill = ".claude/skills/speckit-" + (name[len("speckit."):] if name.startswith("speckit.") else name).replace(".", "-") + "/SKILL.md"
        try:
            _stat(root, skill)
            paths.add(skill)
        except FileNotFoundError:
            paths.add(f".claude/commands/{name}.md")
    return sorted(paths)


def agents_sha256(root, registered):
    """sha256 over `<sha256 of the bytes>  <relpath>` lines of the agent copies named in `registered` ({"commands", "skills"}). A symlink
    (a `--dev` install), a missing, non-regular, oversized or unreadable copy is KitError."""
    for name in (*registered.get("commands", []), *registered.get("skills", [])):
        if not (isinstance(name, str) and _NAME.fullmatch(name)):
            raise KitError("unreadable")
    h = hashlib.sha256()
    for rel in _agent_files(root, registered):
        h.update(f"{hashlib.sha256(_read_regular(root, rel, MAX_FILE)).hexdigest()}  {rel}\n".encode("utf-8"))
    return h.hexdigest()


def read_lock(raw):
    """The lockfile in `raw` (bytes) as `{"schema": 1, "kits": {id: {version, source, catalog_sha256, dir_digest, agents_sha256}}}`.
    Invalid JSON, a duplicate key, another schema, a bad kit id, a non-object entry or a digest that is not 64 lowercase hex characters
    (`catalog_sha256` may be null) is ConfigInvalid with a fixed text. `source` that is not an acceptable https URL and `version` that
    is not `[0-9A-Za-z.+-]{1,40}` are stored as null."""
    why = stack.lock_error(raw)
    if why:
        raise ConfigInvalid(f"{LOCK_FILE} {why}")
    kits = {}
    for n, (kit_id, e) in enumerate(stack.parse_json(raw, stack.MAX_PROFILE)["kits"].items(), 1):
        if not (isinstance(e, dict) and all(isinstance(e.get(k), str) and _HEX64.fullmatch(e[k]) for k in ("dir_digest", "agents_sha256"))
                and (e.get("catalog_sha256") is None or (isinstance(e["catalog_sha256"], str) and _HEX64.fullmatch(e["catalog_sha256"])))):
            raise ConfigInvalid(f"{LOCK_FILE}: kit #{n} is not an object with 64-hex `dir_digest` and `agents_sha256`")
        src, ver = e.get("source"), e.get("version")
        kits[kit_id] = {"version": ver if isinstance(ver, str) and _VERSION.fullmatch(ver) else None,
                        "source": src if isinstance(src, str) and len(src) <= stack.CAP_URL and stack._url_error(src) is None else None,
                        "catalog_sha256": e.get("catalog_sha256"), "dir_digest": e["dir_digest"], "agents_sha256": e["agents_sha256"]}
    return {"schema": 1, "kits": kits}


def load_lock(root="."):
    """`read_lock` of the working-tree `ghp.kits.lock.json` (regular file, no symlink, at most 256 KiB); absent: no kit is pinned."""
    raw = _read_regular(root, LOCK_FILE, stack.MAX_PROFILE, missing_ok=True)
    return {"schema": 1, "kits": {}} if raw is None else read_lock(raw)


def _matches(root, kit, pinned):
    """(True, None) when the kit's files, agent copies and version agree with the lock, else (False, reason or None)."""
    try:
        ok = (dir_digest(root, kit["id"]) == pinned["dir_digest"] and agents_sha256(root, kit["registered"]) == pinned["agents_sha256"]
              and (pinned["version"] is None or pinned["version"] == kit["version"]))
        return ok, None
    except KitError as e:
        return False, e.reason


def verify(root, lock):
    """`{"kits": [{"id", "state"[, "reason"]}], "findings": [{"rule", "kit", "fix"}]}` for the kits in the registry against
    `lock` (from `read_lock`): `pinned`; `unpinned` (not in the lock, or an id/entry that could not be read: `id` null with `n`);
    `changed` (a hash or the version differs, or a file could not be read: `reason` names why); `stale` (locked but not installed; with `reason` `files-remain` and a `kit-changed` finding when the registry file exists and the kit's files are still on disk).
    A disabled kit is judged like an enabled one: disabling only flips `enabled`, the agent copies stay invocable. Findings are `kit-unpinned` and `kit-changed`; the texts are fixed and carry no kit text."""
    out, findings, seen = [], [], set()
    for kit in installed(root):
        if kit["id"] is None:
            out.append({"id": None, "n": kit["n"], "state": "unpinned"})
            findings.append({"rule": "kit-unpinned", "kit": f"#{kit['n']}", "fix": FIX})
            continue
        seen.add(kit["id"])
        pinned = lock["kits"].get(kit["id"])
        if pinned is None:
            out.append({"id": kit["id"], "state": "unpinned"})
            findings.append({"rule": "kit-unpinned", "kit": kit["id"], "fix": FIX})
            continue
        ok, reason = _matches(root, kit, pinned)
        row = {"id": kit["id"], "state": "pinned" if ok else "changed"}
        if reason:
            row["reason"] = reason
        out.append(row)
        if not ok:
            findings.append({"rule": "kit-changed", "kit": kit["id"], "fix": FIX})
    has_registry = os.path.lexists(os.path.join(str(root), REGISTRY))  # installed() above read it, or raised: a present one was readable
    for k in lock["kits"]:
        if k in seen:
            continue
        row = {"id": k, "state": "stale"}
        # only a registry that exists can have lost the entry: upstream says not to commit it, so a fresh clone has none and sees nothing
        if has_registry and os.path.lexists(os.path.join(str(root), ".specify", "extensions", k)):
            row["reason"] = "files-remain"
            findings.append({"rule": "kit-changed", "kit": k, "fix": FIX})
        out.append(row)
    out.sort(key=lambda r: (r["id"] is None, r["id"] or "", r.get("n", 0)))
    return {"kits": out, "findings": findings}


# ---- scan: facts for the human who reviews a kit before pinning (a heuristic aid, not a security verdict) ----
MAX_MANIFEST = 1 << 20  # bytes
MAX_BODY = 1 << 20      # bytes per command body or script
MAX_BODIES = 200
MAX_LINE = 1000         # characters; a longer line is skipped unscanned and flags the file unreadable (keeps every regex input small)
MAX_LISTED = 50         # entries per list or `at` (counts stay exact)
_HOOK = re.compile(r"[a-z_]{1,40}")
_TOOL = re.compile(r"[A-Za-z0-9._-]{1,40}")
_RELFILE = re.compile(r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*")
_SHELL_LANGS = ("", "bash", "sh", "shell", "zsh", "console", "powershell", "pwsh", "ps1")
_FLAGS = {  # fixed patterns of the design; only `network` has a second, shell-only pattern
    "network": re.compile(r"\b(?:curl|wget)\b|Invoke-WebRequest"),
    "installs": re.compile(r"\bpip3?\s+install\b|\bnpm\s+install\b|\bsf\s+plugins\s+install\b|\bbrew\b"),
    "writes": re.compile(r"\bgit\s+push\b|--apply\b|\bgh\s+pr\s+create\b|\brm\s+-(?:rf|fr)\b|\bsudo\b"),
    "secrets": re.compile(r"TOKEN|PASSWORD|_KEY"),
}
_URL = re.compile(r"https?://")
_ALLOWED = re.compile(r"allowed-tools\s*:")


def _scalar(v):
    """A YAML scalar's text: quotes removed, a trailing ` # comment` dropped; '' when unterminated."""
    v = (v or "").strip()
    if v[:1] in ("'", '"'):
        end = v.find(v[0], 1)
        return v[1:end] if end > 0 else ""
    return v.split(" #", 1)[0].strip()


def _key(s):
    """(key, value text) when `s` is `key: value` or `key:` (a quoted key may hold anything), else None. No regex: linear in len(s)."""
    if s[:1] in ("'", '"'):
        end = s.find(s[0], 1)
        rest = s[end + 1:].lstrip() if end > 0 else ""
        key, i = s[1:end], 0
        if not rest.startswith(":"):
            return None
    else:
        i = s.find(":")
        key, rest = (s[:i].rstrip(), s[i:]) if i > 0 else ("", "")
        if not key or key[0] == "#":
            return None
    val = rest[1:]
    return (key, val.strip()) if not val or val[0].isspace() else None


def _manifest_facts(text):
    """Line scanner over the fixed keys of `extension.yml`: `requires.tools[].name`, `provides.commands[]`/`scripts[]` (count and `file`)
    and `hooks.<event>` with its `optional`. Not a YAML parser: a block scalar's lines are skipped, nothing else is interpreted. A line
    over MAX_LINE characters or starting with a tab is skipped unseen; returns `bad` True for the first kind."""
    top = sub = event = None
    key_ind = item_ind = skip = None
    tools, files, hooks = [], [], {}
    counts = {"commands": 0, "scripts": 0}
    bad = False
    for raw in text.split("\n"):
        if len(raw) > MAX_LINE:
            bad = True
            continue
        line = raw.rstrip()
        s = line.lstrip(" ")
        if not s or s.startswith(("#", "\t")):
            continue
        ind = len(line) - len(s)
        if skip is not None and ind > skip:
            continue
        skip = None
        dash = s.startswith("-") and (len(s) == 1 or s[1] in " \t")
        if dash:
            s = s[1:].strip()
        m = _key(s) if s else None
        if ind == 0 and not dash:
            top, sub, event, key_ind, item_ind = (m[0] if m else None), None, None, None, None
            continue
        if top is None or m is None:
            continue
        key, val = m
        if val[:1] in ("|", ">"):
            skip = ind
        if key_ind is None and not dash:
            key_ind = ind
        if top == "hooks":
            if not dash and ind == key_ind:
                event = key
                hooks.setdefault(event, False)
            elif event in hooks and key == "optional" and _scalar(val).lower() == "false":
                hooks[event] = True  # True: at least one hook of the event is not optional
        elif top in ("provides", "requires"):
            if not dash and ind == key_ind:
                sub, item_ind = key, None
            elif sub in ("commands", "scripts", "tools"):
                if dash and (item_ind is None or ind == item_ind):
                    item_ind = ind
                    if sub != "tools":
                        counts[sub] += 1
                if key == "file" and sub in ("commands", "scripts") and item_ind is not None and ind >= item_ind:
                    files.append(_scalar(val))
                elif key == "name" and sub == "tools" and item_ind is not None and ind >= item_ind and dash:
                    tools.append(_scalar(val))
    return counts, tools, files, hooks, bad


def _body_hits(rel, text, flags):
    """Add the line hits of one command body (`.md`: frontmatter and fences are tracked) or script to `flags`. A line over MAX_LINE
    characters is skipped unseen; returns True if there was one. A leading `---` with no closing `---` is not frontmatter: all is scanned."""
    is_md = rel.endswith(".md")
    lines = text.split("\n")
    front = is_md and lines[0].strip() == "---" and any(ln.strip() == "---" for ln in lines[1:])
    fence, bad = None, False
    for n, line in enumerate(lines, 1):
        if len(line) > MAX_LINE:
            bad = True
            continue
        shell = not is_md
        if front:
            if n > 1 and line.strip() == "---":
                front = False
            elif _ALLOWED.match(line):
                flags["allowed_tools"].append(n)
            continue
        if is_md:
            t = line.lstrip()
            if fence is None and t[:3] in ("```", "~~~"):
                fence = (t[0], (t[3:].split() or [""])[0].strip("{}.").lower() in _SHELL_LANGS)
                continue
            if fence is not None and t.startswith(fence[0] * 3):
                fence = None
                continue
            shell = fence is not None and fence[1]
        for name, pat in _FLAGS.items():
            if pat.search(line) or (name == "network" and shell and _URL.search(line)):
                flags[name].append(n)
    return bad


def _cap(items):
    return items if len(items) <= MAX_LISTED else items[:MAX_LISTED] + ["<more>"]


def scan(root, kit_id):
    """Facts about `.specify/extensions/<kit_id>/` for the human reviewer: `{"commands", "hooks", "scripts", "tools", "flags":
    {network, installs, writes, secrets, allowed_tools: {"count", "at": ["<relpath>:<line>"]}}}` plus `"unreadable": true` when the
    manifest, or a command or script file it names, could not be read (missing, symlink, oversized, odd path): the scan is then
    incomplete, never a pass. Output holds counts, kit-relative paths through `stack.safe_path`, and a hook (`[a-z_]{1,40}`) or tool
    name (`[A-Za-z0-9._-]{1,40}`) only when it matches, else `<other>`: never kit text. A heuristic aid, not a security verdict."""
    hits = {k: [] for k in (*_FLAGS, "allowed_tools")}
    out = {"commands": 0, "hooks": [], "scripts": 0, "tools": [], "flags": {}}
    paths = {}  # relpath -> its line hits
    try:
        base = f".specify/extensions/{_check_id(kit_id)}/"
        counts, tools, files, hooks, bad = _manifest_facts(_read_regular(root, base + "extension.yml", MAX_MANIFEST).decode("utf-8", "replace"))
    except KitError:
        counts, tools, files, hooks, bad = {"commands": 0, "scripts": 0}, [], [], {}, True
    out.update(counts)
    out["tools"] = _cap([t if _TOOL.fullmatch(t) else "<other>" for t in tools])
    out["hooks"] = _cap([(e if _HOOK.fullmatch(e) else "<other>") + ("" if req else " (optional)") for e, req in hooks.items()])
    for attempt, rel in enumerate(dict.fromkeys(files), 1):
        if attempt > MAX_BODIES:  # attempts, not only reads: a manifest naming thousands of missing files stops here too
            bad = True
            break
        if len(rel) > 200 or not _RELFILE.fullmatch(rel) or ".." in rel.split("/") or "." in rel.split("/"):
            bad = True
            continue
        try:
            text = _read_regular(root, base + rel, MAX_BODY).decode("utf-8", "replace")
        except KitError:
            bad = True
            continue
        found = {k: [] for k in hits}
        bad = _body_hits(rel, text, found) or bad
        paths[rel] = found
    for n, (rel, found) in enumerate(paths.items(), 1):
        shown = stack.safe_path(rel, n)
        for k, lines in found.items():
            hits[k] += [f"{shown}:{ln}" for ln in lines]
    out["flags"] = {k: {"count": len(v), "at": v[:MAX_LISTED]} for k, v in hits.items()}
    if bad:
        out["unreadable"] = True
    return out


# ---- pin: the human's approval record in ghp.kits.lock.json (a terminal is checked by the CLI; this is process enforcement, not a sandbox) ----
CHECKLIST = (  # fixed text, printed in the pin preview; nothing here comes from the kit
    "Read every command and script of the kit: does any prompt text steer the agent against you, or does a script do more than its name says?",
    "Open each file:line listed under scan.flags and decide whether that network call, install, write or secret name is justified.",
    "Judge the maintainer: who publishes the kit, and do you trust them with your repository and your tools?",
    "Check that the licence and the way the kit works fit your project and your process.",
    "The pin records your approval, nothing more: <id>-config.yml and local config files are not vetted, and a --dev install cannot be pinned.",
)
_E_LOCK = f"{LOCK_FILE} cannot be trusted (a symlink, not a regular file, or invalid); nothing was changed"
_E_LOCK_BACK = f"{LOCK_FILE} could not be read back identical; check the file"
_E_CHANGED = "the kit or the lockfile changed since the preview; nothing was changed"
_E_SWAPPED = "the kit changed while you were reading it: nothing was pinned"


def _repository(text):
    """`extension.repository` of a manifest when it is an acceptable https URL, else None (a line scan, as `scan`)."""
    in_ext = False
    for raw in text.split("\n"):
        s = raw.strip()
        if len(raw) > MAX_LINE or raw.startswith("\t") or not s or s.startswith("#"):
            continue
        m = _key(s)
        if not raw[0].isspace():
            in_ext = bool(m and m[0] == "extension")
        elif in_ext and m and m[0] == "repository":
            url = _scalar(m[1])
            return url if len(url) <= stack.CAP_URL and stack._url_error(url) is None else None
    return None


def plan_pin(root, kit_id):
    """What pinning `kit_id` would record: `{"id", "version", "source", "catalog_sha256", "dir_digest", "agents_sha256", "scan",
    "human_checklist"}`. `catalog_sha256` is always None (Spec Kit's catalog cache is not read). KitError `unknown` when the id is not an
    installed kit (the id is never echoed); `symlink`, `empty`, `control-char`, `attributes` or `unreadable` from the digests, so a `--dev`
    install (symlinked agent copies), a symlinked kit dir and an empty kit dir cannot be pinned."""
    kit = next((k for k in installed(root) if k["id"] == kit_id), None) if isinstance(kit_id, str) else None
    if kit is None:
        raise KitError("unknown")
    base = f".specify/extensions/{kit_id}/"
    digest, agents = dir_digest(root, kit_id), agents_sha256(root, kit["registered"])
    manifest = _read_regular(root, base + "extension.yml", MAX_MANIFEST, missing_ok=True)
    facts = scan(root, kit_id)
    if facts.get("unreadable"):  # an incomplete scan is never a pass: fix the kit (or reinstall it), then pin
        raise KitError("incomplete-scan")
    return {"id": kit_id, "version": kit["version"], "source": _repository(manifest.decode("utf-8", "replace")) if manifest else None,
            "catalog_sha256": None, "dir_digest": digest, "agents_sha256": agents, "scan": facts, "human_checklist": list(CHECKLIST)}


def _lock_entry(plan, by):
    sc = plan["scan"]
    return {"version": plan["version"], "source": plan["source"], "catalog_sha256": plan["catalog_sha256"], "dir_digest": plan["dir_digest"],
           "agents_sha256": plan["agents_sha256"], "reviewed": {"by": by, "date": stack._today().isoformat()},
           "scan": {"commands": sc["commands"], "hooks": sc["hooks"], "scripts": sc["scripts"], "tools": sc["tools"],
                    "flags": {k: v["count"] for k, v in sc["flags"].items()}}}


def pin_code(plan, by):
    """The approval code (`stack.approval_code`) over exactly the lock entry that `apply_pin` writes for `plan` and `by`, except the date."""
    entry = _lock_entry(plan, by)
    entry["reviewed"] = {"by": by}
    return stack.approval_code({"id": plan["id"], "entry": entry})


def apply_pin(root, plan, by, expect_code=None):
    """Write `plan` (from `plan_pin`) into `<root>/ghp.kits.lock.json` as one kit entry with `reviewed {by, date}` and a compact `scan`,
    keys sorted, the file's line terminator kept, every other kit entry untouched. Refused with a fixed ValueError and nothing changed:
    a bad `by` (same rule as `stack approve --by`), a kit or agent copy that no longer matches `plan`, an existing lockfile that is a
    symlink, not a regular file, invalid, or changed while writing. With `expect_code` (what the human typed) the kit is read again and the
    code recomputed right before the write: a different code refuses. Written through a temp file in the same directory and `os.replace`."""
    if not (isinstance(by, str) and stack._ROLE.fullmatch(by)):
        raise ValueError(stack.E_ROLE)
    fresh = plan_pin(root, plan["id"])
    if expect_code is not None and pin_code(fresh, by) != expect_code:
        raise ValueError(_E_SWAPPED)
    if any(fresh[k] != plan[k] for k in ("version", "source", "dir_digest", "agents_sha256", "scan")):
        raise ValueError(_E_CHANGED)
    try:
        old = _read_regular(root, LOCK_FILE, stack.MAX_PROFILE, missing_ok=True)
        data = {"schema": 1, "kits": {}} if old is None else stack.parse_json(old, stack.MAX_PROFILE)
        if old is not None:
            read_lock(old)
    except (KitError, ValueError, ConfigInvalid):
        raise ValueError(_E_LOCK) from None
    data["kits"][plan["id"]] = _lock_entry(fresh, by)
    eol = (re.search(r"\r\n|\n|\r", old.decode("utf-8")) or ["\n"])[0] if old else "\n"
    text = json.dumps(data, indent=2, sort_keys=True).replace("\n", eol) + eol
    path = os.path.join(str(root), LOCK_FILE)

    def check(raw):  # before the rename: the bytes read back from the temp file are a lockfile that holds exactly this entry
        back = stack.parse_json(raw, stack.MAX_PROFILE)
        if stack.lock_error(raw) or back != data or read_lock(raw)["kits"][plan["id"]]["dir_digest"] != plan["dir_digest"]:
            raise ValueError(_E_LOCK)
        if old is None and os.path.lexists(path):
            raise ValueError(_E_CHANGED)
    try:
        stack._atomic_replace(path, text, check, old)
    except ValueError as e:
        raise ValueError(_E_LOCK_BACK if str(e) == stack._E_BACK else str(e)) from None
    return data["kits"][plan["id"]]


# ---- the verbs: `ghp kits verify|pin` (local: no GitHub call, no config) ----
def register(sub):
    p = sub.add_parser("kits", help="Spec Kit extensions against the lockfile ghp.kits.lock.json (local, read-only)")
    ksub = p.add_subparsers(dest="kits_command", metavar="ACTION", required=True)
    ksub.add_parser("verify", help="List the installed kits and whether each is pinned, changed or unpinned (JSON); exit 2 on any kit "
                    "that is unpinned or changed since a human pinned it, or when the registry or a kit file cannot be trusted")
    q = ksub.add_parser("pin", help="A human pins one installed kit in ghp.kits.lock.json (a terminal is needed for --apply): records digests of the kit "
                        "files and agent copies, the scan facts and who reviewed it. Preview by default: digests, scan counts with file:line and a "
                        "fixed 5-line checklist, never kit text. A --dev install, a symlink and an empty kit dir cannot be pinned. A human approval "
                        "record, process enforcement and not a sandbox")
    q.add_argument("id", help="an installed kit id (a-z, 0-9, -)")
    q.add_argument("--by", default="tech lead", metavar="ROLE", help="a role, never a name (letters, digits, spaces; at most 40; default: tech lead)")
    q.add_argument("--apply", action="store_true", help="write the lockfile entry (default: preview, nothing written; both stdin and stdout must be terminals)")
    q.set_defaults(_parser=q)


def _verify(args):
    """`ghp kits verify`: local and read-only (no token, no config). JSON; exit 2 on a kit that is unpinned or changed, or when the registry
    or a file cannot be trusted (a fixed error text; a bad lockfile is ConfigInvalid, exit 2 here too)."""
    try:
        out = verify(".", load_lock("."))
    except KitError as e:
        print(f"error: kits check failed: {e.reason}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2))
    if out["findings"]:
        print(f"error: kits verify failed: {len(out['findings'])} finding(s)", file=sys.stderr)
        return 2
    return 0


def _pin(args):
    """`ghp kits pin ID [--by ROLE] [--apply]`: a human's approval record of one installed kit in `ghp.kits.lock.json`. The preview prints the
    digests, the scan facts and the fixed checklist only (never kit text, so any output is safe). `--apply` needs a terminal on BOTH stdin
    and stdout, tested before any file is read (a guard against accidents, not a security boundary: a pseudo-terminal gives a tty); it
    prints that preview, then `approval code: <12 hex>`, and pins only when the human types the code (the code covers the lock entry; the
    kit is read again and the code recomputed right before the write)."""
    if not stack._ROLE.fullmatch(args.by):
        args._parser.error(stack.E_ROLE)
    if args.apply and not (sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty()):
        raise ValueError("pinning a kit records that a human reviewed it, which only a human may do: run it in your own terminal "
                         "(stdin and stdout must both be a terminal, so the full preview is seen; a guard against accidents, not a security boundary)")
    try:
        prior = load_lock(".")  # a bad lockfile is refused here too, in the preview as well as at --apply
        plan = plan_pin(".", args.id)
        shown = {k: v for k, v in plan.items() if k != "source"}  # the manifest's URL is kit text: it is recorded, not printed
        out = {"applied": False, **shown, "source_recorded": plan["source"] is not None}
        if args.id in prior["kits"]:
            out["note"] = "this kit is already pinned: --apply REPLACES its entry (version, digests, scan, reviewer)"
        if args.apply:
            print(json.dumps(out, indent=2))
            code = pin_code(plan, args.by)
            if not stack.confirm_code(code):
                raise ValueError(stack.E_NOT_CONFIRMED)
            apply_pin(".", plan, args.by, code)
            out["applied"] = True
    except KitError as e:
        print(f"error: kits pin refused: {e.reason}" + (SYMLINK_HINT if e.reason == "symlink" else ""), file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2))  # after --apply: the pinned digests (dir_digest, agents_sha256) as written
    return 0


def run(args, gh):
    return _pin(args) if args.kits_command == "pin" else _verify(args)
