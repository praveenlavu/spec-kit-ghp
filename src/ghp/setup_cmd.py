"""`ghp setup`: write `ghp.config.json` for this folder from a few answers. It asks NO secret and never prompts for a token (`gh` does its own
sign-in; this module reads no credential and runs no program except `git rev-parse` to find the top folder): it checks that `gh` works with one viewer read (`Gh.viewer`: GraphQL `viewer`), asks for the repository
owner and name, the number of a Project owned by a USER account (organization-owned Projects are refused in 0.1), the three Status names and a
profile, and writes names and numbers only (no node id, no option id, no token). Nothing is written to GitHub.

Profiles (ported from ado's `setup --profile`): `starter` = advisory mode; `team` = advisory plus a gate that blocks on `ac_traceability` and
`open_questions` and a review that blocks on `blocker`; `strict` = strict mode, with `strict.test_command` asked (or left to an approved stack profile).
`team` and `strict` both carry an `approvers` list of NUMERIC user ids (never logins) that setup never guesses: typed ids (or `--approver`) switch `approval`
to `comment`, none leaves the list empty and approval in the terminal. `--approver` with the `starter` profile is refused, never ignored.

The file is written to `ghp.config.json` in the current folder only: temp file in the same folder, mode 0644, then `os.link` (no overwrite, even in a race) or,
with `--force`, `os.replace`. Without `--force` an existing file (or link, or directory) of that name is never touched; with it the file is replaced whole.
Every answer is checked before anything is written and is never echoed in an error; everything printed goes through `comments._show`.
"""
import copy
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

from .comments import _show
from . import config as _config
from .config import CONFIG_NAME, MAX_APPROVERS, MAX_REF_CONFIG, Config, _has_control, valid_owner, valid_repo

PROFILES = {
    "starter": {"mode": "advisory"},
    "team": {"mode": "advisory", "gate": {"block": ["ac_traceability", "open_questions"]}, "review_block_on": ["blocker"]},
    "strict": {"mode": "strict"},
}
DEFAULTS = {"status_todo": "Todo", "status_active": "In Progress", "status_done": "Done"}
MAX_STATUS = 50  # characters of a status name (our own cap; GitHub's is not verified)
MAX_COMMAND = 500  # config.py: strict.test_command

E_TERMINAL = ("ghp setup asks questions and needs a terminal; run it in one, or give --owner and --repo (and, if you like, --project, --profile and the "
              "status names) so that it asks nothing")
E_EXISTS = f"{CONFIG_NAME} already exists here and is left as it is; run `ghp setup --force` to replace it whole (the old file is not kept)"
E_OWNER = "the owner must be a GitHub user or organization name (letters, digits and single hyphens, at most 39 characters)"
E_REPO = "the repository name must be 1 to 100 letters, digits, '.', '_' or '-' (and not '.' or '..')"
E_PROJECT = "the project number must be a whole number from 1 to 999999999 (it is in the Project's address: github.com/users/<you>/projects/<number>)"
E_PROJECT_OWNER = "the Project owner must be a GitHub user name (letters, digits and single hyphens, at most 39 characters)"
E_STATUS = f"a status name must be 1 to {MAX_STATUS} printable characters, without line breaks or control characters"
E_STATUS_SAME = "the three status names must be different from each other"
E_PROFILE = "the profile must be one of: " + ", ".join(PROFILES)
E_APPROVERS = (f"approvers must be 1 to {MAX_APPROVERS} different numeric user ids, never logins: `gh api users/<login> --jq .id` prints a person's id")
E_APPROVER_SELF = "an approver cannot be the account you are signed in as: the agent's own comments would approve themselves; ask another person for their id"
E_APPROVER_PROFILE = "--approver is for the team and strict profiles; the starter profile has no approvers (add --profile team or --profile strict)"
E_FLAG = "a flag value must not start or end with a space, or hold a control character"
E_COMMAND = f"the test command must be 1 to {MAX_COMMAND} printable characters on one line"
E_CANCELLED = "setup was cancelled or its input ended; nothing was written"
E_WRITE = f"{CONFIG_NAME} could not be written; nothing was changed"
E_READBACK = f"{CONFIG_NAME} did not read back as written; check the file"
_NUMBER = re.compile(r"[0-9]{1,9}")
_ID = re.compile(r"[0-9]{1,15}")


def register(sub):
    p = sub.add_parser("setup", help=f"write {CONFIG_NAME} here from a few answers (asks no secret; only Projects owned by a user account are supported)")
    p.add_argument("--owner", help="the user or organization that owns the repository")
    p.add_argument("--repo", help="the repository name")
    p.add_argument("--project", metavar="NUMBER", help="the number of your Project (a Project owned by a USER account; organization-owned Projects are not supported in 0.1)")
    p.add_argument("--project-owner", metavar="USER", help="the user account that owns the Project (default: the account you are signed in as)")
    p.add_argument("--status-todo", metavar="NAME", help="the Status column for new issues (default: Todo)")
    p.add_argument("--status-active", metavar="NAME", help="the Status column for work in progress (default: In Progress)")
    p.add_argument("--status-done", metavar="NAME", help="the Status column for finished stories (default: Done)")
    p.add_argument("--profile", choices=tuple(PROFILES), help="starter (default), team or strict")
    p.add_argument("--approver", action="append", metavar="USER_ID", help="team or strict profile: the NUMERIC user id of a person who may approve by comment (repeatable; "
                   "`gh api users/<login> --jq .id` prints it)")
    p.add_argument("--force", action="store_true", help=f"replace an existing {CONFIG_NAME} whole")


def _stdio_is_terminal():
    return bool(sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty())


def _ask(input_fn, label, default=""):
    try:
        text = input_fn(f"{label}{f' [{default}]' if default else ''}: ")
    except (EOFError, KeyboardInterrupt):
        raise ValueError(E_CANCELLED) from None
    return text.strip() or default


def _name(text):
    return isinstance(text, str) and 0 < len(text) <= MAX_STATUS and text.isprintable() and not _has_control(text)


def _approver_ids(items, who_id):
    """The typed ids (strings) as a list of ints, or ValueError: ASCII digits only, at least 1, all different, not the signed-in account."""
    ids = []
    for t in items:
        if not (isinstance(t, str) and _ID.fullmatch(t) and int(t) >= 1):
            raise ValueError(E_APPROVERS)
        ids.append(int(t))
    if not 1 <= len(ids) <= MAX_APPROVERS or len(set(ids)) != len(ids):
        raise ValueError(E_APPROVERS)
    if who_id in ids:
        raise ValueError(E_APPROVER_SELF)
    return ids


def _old_command(path):
    """The `strict.test_command` of the existing file at `path` when it is a regular file (not a link) of a valid shape and the command is valid, else None."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_REF_CONFIG:
            return None
        command = json.loads(path.read_text(encoding="utf-8")).get("strict", {}).get("test_command")
    except (OSError, ValueError, AttributeError, RecursionError):
        return None
    return command if isinstance(command, str) and _command_ok(command) else None


def _command_ok(command):
    return 0 < len(command) <= MAX_COMMAND and command.isprintable() and not _has_control(command)


def _strict_test_command(profile, path, input_fn, interactive, notes, force=False):
    """The `strict.test_command` to write for the strict profile, or None. With an approved stack profile it is never asked: under `--force` a valid command of the
    old file is kept, otherwise it is not in the new file and the note says so (`ghp stack approve --apply` is what writes it). Else asked (blank leaves it unset)."""
    if profile != "strict":
        return None
    from . import stack
    if stack.read_profile(str(Path.cwd()))["status"] == "approved":
        kept = _old_command(path) if force else None
        if kept:
            notes.append("the stack profile is approved: the strict.test_command of the old file was kept; `ghp stack approve --apply` replaces it when the profile changes.")
            return kept
        notes.append("the stack profile is already approved, but strict.test_command is NOT in the new file: only `ghp stack approve --apply` writes it "
                     "(approve again), or set it by hand in ghp.config.json.")
        return None
    command = _ask(input_fn, "Command that runs your tests in strict mode (blank to set it later)") if interactive else ""
    if command and not _command_ok(command):
        raise ValueError(E_COMMAND)
    if not command:
        notes.append("strict.test_command is not set: strict mode needs it (set it in the file, or run `ghp stack detect` and approve the profile).")
    return command or None


def _where_notes():
    """Plain lines about where this file will be read from: outside the git top folder, or with GHP_CONFIG set (Config.load prefers that path)."""
    out = []
    top = _config._out(".", "rev-parse", "--show-toplevel")
    if top is None:
        out.append("no git repository was found here: ghp reads approvers, approval and unattended only from ghp.config.json on the branch you merge into, "
                   "so run setup in your repository's top folder")
    elif not _config._same_file(top.decode("utf-8", "replace").strip(), "."):
        out.append("this is not the top folder of its git repository (" + _show(top.decode("utf-8", "replace").strip()) + "): ghp reads the repo-root "
                   "ghp.config.json for approvers, approval and unattended, so run setup there")
    pointed = os.environ.get("GHP_CONFIG")
    if pointed:
        out.append("GHP_CONFIG is set, so ghp reads " + _show(pointed, 200) + " and NOT the file written here; unset it, or write this file there")
    return out


def _write(path, text, force):
    """Temp file next to `path` (0644), then `os.link` (fails when the name exists: no overwrite without --force, even in a race) or, with force, `os.replace`."""
    data = text.encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o644)
        if force:
            os.replace(tmp, path)
        else:
            os.link(tmp, path)
    except FileExistsError:
        raise ValueError(E_EXISTS) from None
    except OSError:
        raise ValueError(E_WRITE) from None
    finally:
        Path(tmp).unlink(missing_ok=True)
    if path.is_symlink() or path.read_bytes() != data or stat.S_IMODE(path.stat().st_mode) != 0o644:
        raise ValueError(E_READBACK)


def run_setup(args, gh, input_fn=input, interactive=None):
    path = Path(CONFIG_NAME)
    if os.path.lexists(path) and not args.force:  # before anything is asked or sent
        raise ValueError(E_EXISTS)
    interactive = _stdio_is_terminal() if interactive is None else interactive
    if not interactive and (args.owner is None or args.repo is None):
        raise ValueError(E_TERMINAL)
    who = gh.viewer()  # `gh` missing or signed out: the GhError is the answer (exit 1)
    print(f"Signed in as {_show(who.login)} (user id {who.id}). ghp setup never asks for a token: gh does the sign-in.")
    me = who.login if valid_owner(who.login) else ""
    for line in _where_notes():
        print("Note: " + _show(line))

    def get(value, label, default, ok, error):
        if value is not None and (value != value.strip() or _has_control(value)):
            raise ValueError(E_FLAG)
        text = value if value is not None else (_ask(input_fn, label, default) if interactive else default)
        if not ok(text):
            raise ValueError(error)
        return text

    owner = get(args.owner, "Owner of the repository (a user or an organization)", me, valid_owner, E_OWNER)
    repo = get(args.repo, "Repository name", "", valid_repo, E_REPO)
    number = get(args.project, "Project number (only a Project owned by a user account works, not an organization's; it is in the address "
                 "github.com/users/<you>/projects/<number>; blank to leave the board out)", "", lambda t: t == "" or _NUMBER.fullmatch(t) and int(t) >= 1, E_PROJECT)
    project_owner = get(args.project_owner, "User account that owns the Project", me, valid_owner, E_PROJECT_OWNER) if number else None
    names = {key: get(getattr(args, key), f"Status column for {label}", DEFAULTS[key], _name, E_STATUS)
             for key, label in (("status_todo", "new issues"), ("status_active", "work in progress"), ("status_done", "finished stories"))}
    if len({n.casefold() for n in names.values()}) != 3:
        raise ValueError(E_STATUS_SAME)
    profile = get(args.profile, "Profile: starter, team or strict", "starter", lambda t: t in PROFILES, E_PROFILE)
    if args.approver is not None and profile not in ("team", "strict"):
        raise ValueError(E_APPROVER_PROFILE)

    data = {"owner": owner, "repo": repo}
    if number:
        data.update(project_owner=project_owner, project_number=int(number))
    data.update(names)
    data.update(copy.deepcopy(PROFILES[profile]))
    notes = []
    command = _strict_test_command(profile, path, input_fn, interactive, notes, args.force)
    if command:
        data["strict"] = {"test_command": command}
    if profile in ("team", "strict"):
        if args.approver is None and interactive:
            print("Approvers are the people who may approve by comment. Give their NUMERIC user ids, not their logins: "
                  "`gh api users/<login> --jq .id` prints one. Never your own id. They count only once this file is on the branch you merge into.")
            typed = _ask(input_fn, "Approver numeric user ids (not logins), comma-separated (blank to add them later)")
            items = typed.replace(",", " ").split()
        else:
            items = args.approver or []
        ids = _approver_ids(items, who.id) if items else []
        data["approvers"] = ids  # the placeholder is an empty list: the human fills it
        if ids:
            data["approval"] = "comment"
        else:
            notes.append("approvers is empty and approval stays in the terminal. To approve by comment, put the numeric user ids of the approvers in "
                         "\"approvers\" (`gh api users/<login> --jq .id`) and set \"approval\" to \"comment\"; ghp reads both only from the branch you merge into.")
    Config._from_data(data, CONFIG_NAME)  # the validator the verbs use: ConfigInvalid names the key, never the value
    existed = os.path.lexists(path)
    _write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n", args.force)
    print(f"Wrote {_show(str(Path.cwd() / CONFIG_NAME))}{' (replaced the old file)' if existed else ''}: names and numbers only, no token, no ids from GitHub.")
    for line in json.dumps(data, indent=2, ensure_ascii=False).splitlines():
        print("  " + _show(line))
    for note in notes:
        print("Note: " + _show(note))
    print("Next: run `ghp doctor` to check that everything can be reached.")
    return 0


def run(args, gh):
    return run_setup(args, gh)
