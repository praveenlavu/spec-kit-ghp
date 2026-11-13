"""T31 (LIVE): approver versus agent. This is account-versus-bot, not a two-human test: the agent is `github-actions[bot]` (id 41898282, the
Actions token of a throwaway workflow), the approver is `praveenlavu` (id 22502189, the owner's token on this machine). Scratch repo
`praveenlavu/ghp-spike-scratch` by default, or the owner's repository named by GHP_LIVE_REPO (Actions must be able to start a job there,
for example the public `praveenlavu/ghp-live-comment`); fictional data only.

Setup (module fixture `world`, idempotent): the scratch repo's DEFAULT branch gets the throwaway workflow `.github/workflows/ghp-t31.yml` and a
`ghp.config.json` with `approval: "comment"`, `approvers: [22502189]` (the trust keys are read only from the target branch, so they must be on
`main`); a branch `ghp-live/t31-<stamp>` adds the vendored ghp (`git archive HEAD src` of this repository), a fictional ledger with one open
question, a spec file and a decisions file with D1. The workflow clones that branch (so `origin/HEAD` resolves, as `E_TRUST_BASE_UNRESOLVED`
needs) and runs `python3 -m ghp ...` with the workflow's own token for each word of its `steps` input.

Live, REST `GET /user` answers HTTP 403 to the Actions token (the probe step pins it). The first T31 run needed a test-only shim for that
(finding T31a); since T31a `Gh.viewer` reads GraphQL `viewer` itself, so no shim exists any more and the whole chain runs as shipped.

Teardown: closes the issue (not_planned), deletes the branch, disables the workflow and removes the workflow file and the trust config from
`main` (a commit by `praveenlavu`). Every git call gets a credential helper that asks `gh auth token -u praveenlavu` (every other helper reset; no
token in argv or a file); every `gh` call inherits the GH_TOKEN the live guard verified. Each workflow run is waited for at most 120 s.
Findings go to RECORD, dumped to $GHP_T31_RECORD (a path) for docs/evidence/T31.md.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest

from ghp import approvals, comments
from ghp.gh import Gh, GhError
from ghp.refs import Ref
from live.conftest import OWNER, PROJECT, REPO, REPO_PATH

STAMP = time.strftime("%Y%m%d-%H%M%S")
FULL = "%s/%s" % (OWNER, REPO)
OWNER_ID, BOT_ID = 22502189, 41898282
ROOT = Path(__file__).resolve().parents[2]
HELPER = "!f() { echo username=%s; echo password=$(gh auth token -u %s); }; f" % (OWNER, OWNER)  # the token never leaves the helper
IDENTITY = ("-c", "user.name=%s" % OWNER, "-c", "user.email=%d+%s@users.noreply.github.com" % (OWNER_ID, OWNER))
RUN_WAIT = 120  # seconds: the longest a workflow run is waited for
WF = "ghp-t31.yml"
WF_PATH = ".github/workflows/" + WF
TRUST = '{"owner": "%s", "repo": "%s", "project_owner": "%s", "project_number": %d, "approval": "comment", "approvers": [%d]}\n' % (
    OWNER, REPO, OWNER, PROJECT, OWNER_ID)
WORKFLOW = """name: ghp-t31
# Throwaway T31 live-test workflow (fictional data): runs the vendored ghp as github-actions[bot]. No secret is used or printed.
on:
  workflow_dispatch:
    inputs:
      issue:
        required: true
        type: string
      steps:
        required: true
        type: string
permissions:
  issues: write
  contents: read
jobs:
  t31:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - name: ghp as the workflow token
        env:
          GH_TOKEN: ${{ github.token }}
          ISSUE: ${{ inputs.issue }}
          STEPS: ${{ inputs.steps }}
          REPO: ${{ github.repository }}
          REF: ${{ github.ref_name }}
        run: |
          case "$ISSUE" in ''|*[!0-9]*) echo "T31 bad issue"; exit 2;; esac
          gh repo clone "$REPO" w -- -q --branch "$REF"
          cd w
          set +e  # every step reports its own exit status
          ghp() { python3 -m ghp "$@"; }
          export PYTHONPATH="$PWD/vendor/ghp/src"
          for s in $STEPS; do
            echo "T31 begin $s"
            case "$s" in
              probe) python3 --version; git rev-parse --abbrev-ref origin/HEAD
                     gh api user >/dev/null 2>user.err; echo "T31 rest-user exit=$? $(tr -s '\\n' ' ' < user.err | cut -c1-200)"
                     gh api graphql -f query='query { viewer { login databaseId __typename } }';;
              questions) ghp questions "$ISSUE" --post --apply;;
              decisions) ghp decisions "$ISSUE" --post --apply;;
              check-validation) ghp approvals check "$ISSUE" --gate wait-po-validation;;
              check-review) ghp approvals check "$ISSUE" --gate review-spec;;
              bot-approve) gh issue comment "$ISSUE" --repo "$REPO" --body "/approve D1";;
              *) echo "T31 unknown step"; false;;
            esac
            rc=$?; echo; echo "T31 end $s exit=$rc"  # the echo ends a last line printed without a newline
          done
"""
STORY_BODY = "# Story\n\nAs a fictional visitor I pick a pickup window (T31 live test).\n\n## Acceptance criteria\n\n1. AC1 a window can be chosen\n"
QUESTION = "- [ ] Q1: Which fictional pickup window is the default? → _(open)_ · owner: PO"
SPEC = "# Spec (fictional)\n\nA visitor picks one 30-minute pickup window at the fictional bakery.\n"
RECORD = {"stamp": STAMP, "runs": {}}
EDITS = """query($id: ID!) { node(id: $id) { ... on IssueComment { databaseId lastEditedAt isMinimized minimizedReason
  editor { login } author { login __typename } userContentEdits(first: 10) { totalCount nodes { editedAt editor { login } } } } } }"""
DELETED = """query($owner: String!, $repo: String!, $n: Int!) { repository(owner: $owner, name: $repo) { issue(number: $n) {
  timelineItems(last: 20, itemTypes: [COMMENT_DELETED_EVENT]) { totalCount nodes { ... on CommentDeletedEvent { createdAt actor { login }
  deletedCommentAuthor { login } } } } } } }"""


def git(w, *args, cwd=None):
    r = subprocess.run(["git", "-c", "credential.helper=", "-c", "credential.helper=" + HELPER, *IDENTITY, *args], cwd=str(cwd or w.clone),
                       capture_output=True, text=True, timeout=120, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    assert r.returncode == 0, (args, r.stderr[-400:])
    return r.stdout.strip()


def ghp_local(w, *args):
    """The vendored ghp (the code the workflow runs) on this machine, cwd = the clone on the branch, the owner's token (inherited GH_TOKEN)."""
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "GITHUB_ACTIONS", "GHP_CONFIG")}
    env["PYTHONPATH"] = str(w.clone / "vendor" / "ghp" / "src")
    r = subprocess.run([sys.executable, "-m", "ghp", *args], cwd=str(w.clone), capture_output=True, text=True, timeout=120, env=env)
    return r.returncode, (r.stdout + r.stderr).strip()


def _log_lines(text):
    for raw in text.splitlines():
        line = re.sub(r"^﻿?\d{4}-\d\d-\d\dT[0-9:.]+Z ", "", raw.split("\t", 2)[-1])
        if line.startswith("T31 ") or not line.startswith(("\x1b", "##[")):
            yield line


def dispatch(w, steps, label):
    """Run the workflow on the branch with these steps; {step: {"exit": int, "out": text}} from its log. Waits <= RUN_WAIT s."""
    r = subprocess.run(["gh", "workflow", "run", WF, "-R", FULL, "--ref", w.branch, "-f", "issue=%d" % w.n, "-f", "steps=" + steps],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-400:]
    m = re.search(r"/actions/runs/([0-9]+)", r.stdout + r.stderr)
    assert m, "gh workflow run printed no run URL: %r" % (r.stdout + r.stderr)[-300:]
    run = m.group(1)
    subprocess.run(["gh", "run", "watch", run, "-R", FULL], capture_output=True, text=True, timeout=RUN_WAIT)
    out, log = {}, ""
    for _ in range(10):  # the log can lag the run's completion by a few seconds
        v = subprocess.run(["gh", "run", "view", run, "-R", FULL, "--json", "status,conclusion,url"], capture_output=True, text=True, timeout=60)
        meta = json.loads(v.stdout or "{}")
        if meta.get("status") == "completed":
            log = subprocess.run(["gh", "run", "view", run, "-R", FULL, "--log"], capture_output=True, text=True, timeout=60).stdout
            if "T31 end" in log:
                break
        time.sleep(3)
    assert meta.get("conclusion") == "success", meta
    cur = None
    for line in _log_lines(log):
        if (b := re.match(r"T31 begin (\S+)$", line)):
            cur = b.group(1)
            out[cur] = {"out": []}
        elif (e := re.match(r"T31 end (\S+) exit=([0-9]+)$", line)) and e.group(1) == cur:
            out[cur]["exit"], out[cur]["out"], cur = int(e.group(2)), "\n".join(out[cur]["out"]), None
        elif cur:
            out[cur]["out"].append(line)
    assert all("exit" in v for v in out.values()) and list(out) == steps.split(), (steps, out)
    RECORD["runs"][label] = {"id": int(run), "url": meta["url"], "steps": steps, "result": out}
    return out


def say(w, body):
    """A comment by the owner (the approver); its id."""
    return w.ag.rest("POST", "%s/issues/%d/comments" % (REPO_PATH, w.n), {"body": body})["id"]


def fetch(w):
    found = comments.fetch(w.ag, w.ref)
    return found, {c.id: c for c in found}


def shape(c):
    return {"id": c.id, "author": [c.author.id, c.author.login, c.author.type], "edited": c.edited, "edit_count": c.edit_count,
            "last_edited_at": c.last_edited_at, "minimized": c.minimized, "app": c.app, "viewer_did_author": c.viewer_did_author}


def _put_main(w, files):
    """Make `main` hold exactly these files' contents (None removes the file); one commit, pushed, when anything differs."""
    git(w, "checkout", "-q", "main")
    git(w, "pull", "-q", "--ff-only")
    changed = []
    for rel, text in files.items():
        p = w.clone / rel
        if text is None and p.exists():
            git(w, "rm", "-q", rel)
            changed.append(rel)
        elif text is not None and (not p.exists() or p.read_text() != text):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
            git(w, "add", rel)
            changed.append(rel)
    if changed:
        git(w, "commit", "-q", "-m", "T31 live: %s the throwaway workflow and trust config (fictional)" % ("add" if files[WF_PATH] else "remove"))
        git(w, "push", "-q", "origin", "main")
    return changed, git(w, "rev-parse", "HEAD")


@pytest.fixture(scope="module")
def world(gh, tmp_path_factory):
    w = SimpleNamespace(root=tmp_path_factory.mktemp("t31"), n=None, branch="ghp-live/t31-%s" % STAMP, pushed=False)
    w.ag = Gh()  # the owner's direct reads and writes (setup, approvals, edits, teardown)
    w.ag.arm()
    w.clone = w.root / "scratch"
    git(w, "clone", "-q", "https://github.com/%s.git" % FULL, str(w.clone), cwd=w.root)
    try:
        RECORD["main_setup"] = _put_main(w, {WF_PATH: WORKFLOW, "ghp.config.json": TRUST})
        for _ in range(10):  # a just-pushed workflow takes a moment to register; a disabled one (an earlier teardown) is enabled again
            try:
                w.ag.rest("PUT", "repos/%s/actions/workflows/%s/enable" % (FULL, WF))
                break
            except GhError:
                time.sleep(3)
        w.n = w.ag.rest("POST", REPO_PATH + "/issues", {"title": "T31 live story: bakery pickup window (fictional)", "body": STORY_BODY,
                                                        "labels": ["ghp:live-test"]})["number"]
        w.ref = Ref(OWNER, REPO, w.n)
        git(w, "checkout", "-q", "-b", w.branch)
        vendor = w.clone / "vendor" / "ghp"
        vendor.mkdir(parents=True)
        tar = subprocess.run(["git", "-C", str(ROOT), "archive", "HEAD", "src"], capture_output=True, timeout=60, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(vendor)], input=tar, timeout=60, check=True)
        RECORD["vendored_from"] = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
        rc, out = ghp_local(w, "dod", str(w.n))
        assert rc == 0, out
        ledger = w.clone / "dod" / ("DoD_%d.md" % w.n)
        text = ledger.read_text()
        fmt = next(ln for ln in text.split("\n") if ln.startswith("  Format, one line each: `- [ ] Q1"))
        spec_dir = "specs/t31-%d" % w.n
        assert "- **Spec dir:** _(set by speckit.ghp.pull)_" in text
        text = text.replace(fmt, fmt + "\n" + QUESTION, 1).replace("- **Spec dir:** _(set by speckit.ghp.pull)_", "- **Spec dir:** " + spec_dir, 1)
        ledger.write_text(text)
        (w.clone / spec_dir).mkdir(parents=True)
        (w.clone / spec_dir / "spec.md").write_text(SPEC)
        rc, out = ghp_local(w, "decisions", str(w.n), "--add", "Pickup windows are 30 minutes long (fictional)", "--kind", "decision",
                            "--why", "keeps the fictional board simple")
        assert rc == 0, out
        git(w, "add", "vendor", "dod", spec_dir)
        git(w, "commit", "-q", "-m", "T31 live: vendored ghp and fictional ledger for #%d" % w.n)
        git(w, "push", "-q", "origin", w.branch)
        w.pushed = True
        RECORD.update(issue=w.n, branch=w.branch)
        yield w
    finally:
        clean = RECORD.setdefault("cleanup", {})
        if w.n:
            w.ag.rest("PATCH", "%s/issues/%d" % (REPO_PATH, w.n), {"state": "closed", "state_reason": "not_planned"})
            clean["issue_closed_not_planned"] = w.n
        if w.pushed:
            w.ag.rest("DELETE", "%s/git/refs/heads/%s" % (REPO_PATH, urllib.parse.quote(w.branch, safe="/")))
            clean["branch_deleted"] = w.branch
        try:
            w.ag.rest("PUT", "repos/%s/actions/workflows/%s/disable" % (FULL, WF))
            clean["workflow_disabled"] = WF
        except GhError as e:
            clean["workflow_disable_error"] = str(e)
        clean["main_restore"] = _put_main(w, {WF_PATH: None, "ghp.config.json": None})
        if os.environ.get("GHP_T31_RECORD"):
            Path(os.environ["GHP_T31_RECORD"]).write_text(json.dumps(RECORD, indent=2, default=str))


def _hash(text, gate):
    m = re.search(r"\b%s ([0-9a-f]{64})\b" % re.escape(gate), text)
    assert m, "no hash for %s in %r" % (gate, text[-600:])
    return m.group(1)


def test_ac1_as_written(world):
    """AC1 as written, first half: ghp run by the workflow token tells its own id (GraphQL `viewer`, T31a) although REST `GET /user` is 403, and
    comment mode's real gate shows: the bot may not post the questions before an approver approves this exact plan."""
    w = world
    r = dispatch(w, "probe questions", "A")
    probe = r["probe"]["out"]
    RECORD["probe"] = probe
    assert "origin/main" in probe and re.search(r"Python 3\.(9|[1-9][0-9])", probe)
    assert "T31 rest-user exit=1 gh: Resource not accessible by integration (HTTP 403)" in probe
    viewer = json.loads(probe[probe.index('{"data"'):].split("\n")[0])["data"]["viewer"]
    assert viewer == {"login": "github-actions[bot]", "databaseId": BOT_ID, "__typename": "User"}
    assert r["questions"]["exit"] == 2 and "no valid approval for this plan on issue #%d" % w.n in r["questions"]["out"], r
    w.q_hash = _hash(r["questions"]["out"], "approve-questions")
    found, _ = fetch(w)
    assert not [c for c in found if c.author.id == BOT_ID], "the bot posted nothing in run A"


def test_ac1_chain_as_the_bot(world):
    """AC1 as written, second half, no shim: the approver approves the plan hash the bot's refusal printed, the bot posts the questions (`questions
    --post --apply` exits 0), then the decisions; the bot's own `/approve D1` does not meet the gate (AC3), the approver's `/approve D1` does:
    `approvals check` exits 0 as the bot."""
    w = world
    w.approvals = {"approve-questions": say(w, "/approve approve-questions " + w.q_hash)}
    r = dispatch(w, "questions decisions", "B")
    assert r["questions"]["exit"] == 0 and (m := re.search(r"posted comment ([0-9]+)", r["questions"]["out"])), r
    w.q_comment = int(m.group(1))
    assert r["decisions"]["exit"] == 2 and "no valid approval for this plan" in r["decisions"]["out"]
    w.approvals["approve-decisions"] = say(w, "/approve approve-decisions " + _hash(r["decisions"]["out"], "approve-decisions"))
    r = dispatch(w, "decisions bot-approve check-validation check-review", "C")
    assert r["decisions"]["exit"] == 0, r
    assert r["bot-approve"]["exit"] == 0 and (m := re.search(r"#issuecomment-([0-9]+)", r["bot-approve"]["out"])), r
    w.bot_approve = int(m.group(1))
    # AC3: the bot's own /approve D1 is refused: the validation gate stays open on D1
    assert r["check-validation"]["exit"] == 2 and "1 open (D1)" in r["check-validation"]["out"], r
    assert r["check-review"]["exit"] == 2 and "no valid approval for gate review-spec" in r["check-review"]["out"]
    w.approvals["D1"] = say(w, "/approve D1")
    w.approvals["review-spec"] = say(w, "/approve review-spec " + _hash(r["check-review"]["out"], "review-spec"))
    r = dispatch(w, "check-validation check-review", "D")
    assert r["check-validation"]["exit"] == 0 and '"met": true' in r["check-validation"]["out"], r
    assert r["check-review"]["exit"] == 0 and '"pinned": %d' % w.approvals["review-spec"] in r["check-review"]["out"], r
    found, by_id = fetch(w)
    assert not found.notes
    for cid in (w.q_comment, w.bot_approve):
        assert by_id[cid].author.id == BOT_ID and by_id[cid].author.type == "Bot"
    RECORD["bot_comments"] = [shape(by_id[w.q_comment]), shape(by_id[w.bot_approve])]
    RECORD["owner_approvals"] = {k: shape(by_id[v]) for k, v in w.approvals.items()}
    RECORD["bot_comment_guard_reason"] = approvals.guard(by_id[w.bot_approve], {OWNER_ID}, {BOT_ID})


def test_ac2_owner_token_check_exits_2(world):
    """AC2 / D12: the same checks run with the owner's token (on this machine) exit 2: the approver IS the token identity, so the approval is the
    token's own comment and never counts (rule 3)."""
    w = world
    git(w, "fetch", "-q", "origin")
    git(w, "checkout", "-q", w.branch)
    rc, out = ghp_local(w, "approvals", "check", str(w.n), "--gate", "review-spec")
    RECORD["ac2_review_spec"] = [rc, out]
    assert rc == 2 and "no valid approval for gate review-spec on issue #%d" % w.n in out
    rc, out = ghp_local(w, "approvals", "check", str(w.n), "--gate", "wait-po-validation")
    RECORD["ac2_wait_po_validation"] = [rc, out]
    assert rc == 2
    found, by_id = fetch(w)
    owner_d1 = by_id[w.approvals["D1"]]
    RECORD["ac2_counts_owner_token"] = approvals.counts(owner_d1, {OWNER_ID}, OWNER_ID)
    assert approvals.counts(owner_d1, {OWNER_ID}, OWNER_ID) == (False, "excluded-author")
    assert owner_d1.viewer_did_author is True  # GitHub's own "the token wrote this" (read with the owner's token)


def test_ac3_approver_edit_stops_counting(world):
    """AC3: the approver edits their `/approve D1` (the first line stays a valid command): it no longer counts; the bot's check exits 2."""
    w = world
    cid = w.approvals["D1"]
    w.ag.rest("PATCH", "%s/issues/comments/%d" % (REPO_PATH, cid), {"body": "/approve D1\n\n(edited once, fictional)"})
    _, by_id = fetch(w)
    c = by_id[cid]
    RECORD["approver_edit"] = {"comment": shape(c), "graphql": w.ag.graphql(EDITS, {"id": c.node_id})["node"]}
    assert c.edited and c.last_edited_at is not None
    r = dispatch(w, "check-validation", "E")
    assert r["check-validation"]["exit"] == 2 and "1 open (D1)" in r["check-validation"]["out"], r
    RECORD["approver_edit_check"] = r["check-validation"]["out"]
    assert "1 newer approver comment(s) edited after posting" in r["check-validation"]["out"], r  # T31c wording, not "malformed"


def test_ac3_writer_rewrite_of_the_bot_comment(world):
    """AC3: a writer (the owner) rewrites the bot's comment: GitHub records the editor and lastEditedAt. ghp's Comment carries lastEditedAt and
    the edit count (so `edited` is True), not the editor."""
    w = world
    cid = w.bot_approve
    w.ag.rest("PATCH", "%s/issues/comments/%d" % (REPO_PATH, cid), {"body": "/approve D1 (rewritten by a writer, fictional)"})
    _, by_id = fetch(w)
    c = by_id[cid]
    g = w.ag.graphql(EDITS, {"id": c.node_id})["node"]
    RECORD["writer_rewrite"] = {"comment": shape(c), "graphql": g, "comment_fields": sorted(f for f in comments.Comment.__dataclass_fields__)}
    assert g["editor"]["login"] == OWNER and g["author"]["login"] == "github-actions" and g["lastEditedAt"]
    assert c.edited and c.author.id == BOT_ID and "editor" not in comments.Comment.__dataclass_fields__


def test_unverified_shapes(world):
    """The UNVERIFIED items with real comments: `performed_via_github_app` of the bot's comments, a minimized comment, a deleted comment's trace."""
    w = world
    raw = {c["id"]: c for c in w.ag.rest("GET", "%s/issues/%d/comments?per_page=100" % (REPO_PATH, w.n))}
    RECORD["performed_via_github_app"] = {str(cid): raw[cid].get("performed_via_github_app", "<key missing>") for cid in (w.q_comment, w.bot_approve)}
    RECORD["owner_performed_via_github_app"] = raw[w.approvals["review-spec"]].get("performed_via_github_app", "<key missing>")
    RECORD["bot_rest_user"] = {k: raw[w.bot_approve]["user"][k] for k in ("login", "id", "type")}
    mid = say(w, "T31 throwaway note to minimize (fictional)")
    _, by_id = fetch(w)
    w.ag.graphql("mutation($id: ID!) { minimizeComment(input: {subjectId: $id, classifier: OUTDATED}) { minimizedComment { isMinimized } } }",
                 {"id": by_id[mid].node_id})
    _, by_id = fetch(w)
    RECORD["minimized"] = {"comment": shape(by_id[mid]), "graphql": w.ag.graphql(EDITS, {"id": by_id[mid].node_id})["node"]}
    assert by_id[mid].minimized is True
    did = say(w, "T31 throwaway note to delete (fictional)")
    _, before = fetch(w)
    assert did in before
    w.ag.rest("DELETE", "%s/issues/comments/%d" % (REPO_PATH, did))
    found, after = fetch(w)
    try:
        w.ag.rest("GET", "%s/issues/comments/%d" % (REPO_PATH, did))
        gone = "still readable"
    except GhError as e:
        gone = str(e)
    tl = w.ag.graphql(DELETED, {"owner": OWNER, "repo": REPO, "n": w.n})["repository"]["issue"]["timelineItems"]
    RECORD["deleted"] = {"in_fetch_after": did in after, "notes_after": [str(n) for n in found.notes], "rest_get": gone, "timeline": tl}
    assert did not in after and not found.notes
