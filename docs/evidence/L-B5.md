This is account-versus-bot, not a two-human test.

# L-B5 evidence: comment approval mode with the Actions bot as the agent (PLAN-LIVE-0.1 batch B5)

Run 2026-11-12, stamp `20261112-231447`, by the B5 executor agent.

- **Account.** Every `gh`, `ghp` and `git push` used `praveenlavu` (user id 22502189, `GET /user` read first) through the per-command prefix `GH_TOKEN=$(gh auth token -u praveenlavu)`; git used the per-command credential helper (checked with `git credential fill`, only `username=praveenlavu` shown). No `gh auth switch/refresh/login`. Only the owner's own account was used. No token was typed, echoed, logged or written (scan of logs and run logs for token patterns: none). No secret or PAT was created. The workflow used only its own `${{ github.token }}` (masked `***` in the run logs).
- **Approver comments.** The `/approve`, `/reject` and `/answer` comments were posted by the agent through the owner's own account `praveenlavu`, as PLAN-LIVE-0.1 section 3 batch B5 instructs ("Approvals are posted as praveenlavu with `fx comment`"). It is the owner's own account and repository; the owner agreed in advance. The agent identity under test is `github-actions[bot]` (41898282).
- **Target.** R2 = `praveenlavu/ghp-live-comment` (public by owner decision, so hosted Actions run without billing; default branch `main`; no Project). Nothing else was written: not R1, not Project 3, not `ghp-consumer-scratch`, not the product repositories.
- **Product under test.** Released `ghp 0.1.0`: laptop venv `pip install "spec-kit-ghp @ git+https://github.com/praveenlavu/spec-kit-ghp.git@v0.1.0"` (`ghp --version` = `ghp 0.1.0`); runner `pip install ...@cefd9fa4ca0ebefe7790fd9669b87fa683cd420a` (tag `v0.1.0` dereferences to that commit; `ghp 0.1.0` printed in every run). Product code was not changed.
- **Harness** (`$LIVE=.../scratchpad/live-r2`): `bin/gh` request-log wrapper (argv, method, path, `graphql_write` via `ghp.gh.is_graphql_write` on the stdin body, never stdout/stdin/tokens), `bin/writes.py` (a GraphQL POST counts as a write only when it holds a mutation), `bin/fx.py` (bodies on stdin only), `bin/dispatch.sh` (dispatch, `gh run watch`, `gh run view --log` into `runs/<id>.log`), `bin/l049.sh`/`l049d.sh` (L-049 drivers). Request logs `log/*.jsonl`. Bot-side requests happen on the runner and are not in the request log; their effect is read back from GitHub and from the run logs.
- **Workflow** `.github/workflows/ghp-live.yml` (on `main` and the fixture branches): `workflow_dispatch` inputs `issue` (digits checked), `steps` (`[a-z0-9 -]` checked), `arg` (checked charset); permissions `issues/contents/pull-requests: write`; no third-party action; venv + pip install at the tag commit; `gh repo clone "$GITHUB_REPOSITORY" w -- --branch "$REF"` (sets `origin/HEAD -> origin/main`, printed each run); `bash +e`; prints `L end <step> exit=<n>`. Pins and diffs are printed by the `show` step (no artifact upload, to keep the workflow free of any action).
- **Config on `main`** (commit `e19b3a6`): `{"owner":"praveenlavu","repo":"ghp-live-comment","approval":"comment","approvers":[22502189]}`.

## Objects

| What | Ids |
|---|---|
| label | `ghp:live-test` (created); `ghp:task` (created by the bot's push) |
| stories | #1 A (L-045..L-048, L-052..L-054, L-057), #2 B (L-049, L-051), #7 C (L-056), #8 D (L-049 request variants, L-050) |
| bot sub-issues | #3, #4, #5 (T001..T003 of #1) |
| PR | #6 (opened and merged by praveenlavu as the L-054 fixture, see L-052) |
| branches | `ghp-live/b5-20261112-231447` (fixtures for #1/#2), `...-b` (fixtures #2/#7/#8 after the merge), `ghp-live/b5-trust` (L-050a) |
| main commits | `e19b3a6` add harness; `f26fc12` merge of PR #6; `036d2b0`, `1bb16af`, `66ae1ca` cleanup |

## Workflow runs (all `github-actions[bot]`, https://github.com/praveenlavu/ghp-live-comment/actions/runs/<id>)

| tag | run id | steps -> exit |
|---|---|---|
| R1-A | 43327466239 | probe 0, qpost **2**, dpost 2, check-review-spec/plan/review 2, check-wait-po-answers 0, check-wait-po-validation 0, check-approve-push **2**, push-preview 0, push-apply 2 |
| R2-A | 43327639969 | qpost **0**, dpost 0, check-review-spec/plan/review **0** (pinned), check-wait-po-answers 2, check-wait-po-validation 2, push-preview 0, push-apply **0**, push-preview 0, push-apply **2** |
| R3-A | 43327855117 | push-preview 0, push-apply **0**, qsync 0, dsync 0, dresolve **2**, check-wait-po-answers **0**, check-wait-po-validation **0** |
| R4-A | 43328021689 | activate **2**, pr-preview 2, pr-apply 2 (`--push needs --from-prepared`), projcfg: project show **1**, doctor 0 |
| R5-A, R6-A | 43328119874, 43328181677 | pr-preview/pr-apply 2 (harness: `--head` required) |
| R7-A | 43328255116 | pr-preview 0, pr-apply **2** (approval needed) after tasks ticked |
| R8-A | 43328338595 | pr-preview 0, pr-apply **1** (GitHub 403) |
| L054-1 | 43328510711 | done-preview 0, done-apply **2** (approval needed) |
| L054-2 | 43328600763 | done-preview 0, done-apply **2** (E_NOT_PINNED), check-approve-done **2**, sync-dry **2**, sync-crafted(approve-done) **2** |
| L054-3 | 43328687862 | sync-crafted(approve-pr) 2 (control: state accepted, plan file missing) |
| L049-ok/a/b/c/d/e/f/g/h/i/ok/i2/i3 | 43328827338, 43328947423, 43329009483, 43329068963, 43329149694, 43329203623, 43329259937, 43329346775, 43329422992, 43329500837, 43329574370, 43329673226, 43329744816 | check-review-spec 0,2,2,2,(bot-approve 0) 2,2,2,2,2,**0**,0,2,2 |
| L049D-q/r/r2 | 43330076299, 43330145491, 43330206792 | bot-approve 0 + check 0; bot-approve 0 + check **2**; check 0 |
| L050b | 43330325605 | check-review-spec 0, notrust-check **2** |
| L050a | 43330429463 | bot-approve 0, check-review-spec **2** |
| L056 | 43331066935 | check-review-spec **0** |

## Cases

**L-045 PASS** (REG, questions as the bot need the owner's approval).
- R1-A `ghp questions 1 --post --apply` exit 2: `comment approval: no valid approval for this plan on issue #1 ... with one of: approve-answers-questions c9b5..., approve-plan-questions e975..., approve-questions 85f3bd24...`.
- praveenlavu `/approve approve-questions 85f3bd24...` (comment 6838726165).
- R2-A exit 0: `posted comment 6838738752`. Read back: author `github-actions[bot]` 41898282, `performed_via_github_app.id` 15368. Probe: GraphQL viewer `{"login":"github-actions[bot]","databaseId":41898282,"__typename":"User"}`.
- Laptop writes: 1 owner comment (log `owner-comments.jsonl`).

**L-046 PASS** (push in comment mode without a Project; label preflight).
- Preview (R1-A, R2-A): `create 3 · update 0 · unchanged 0 · orphan 0`, `Project: no Project is configured; no Project items are added`, `⚠ label ghp:task does not exist; GitHub creates it only when the token has push access`. Apply before approval exit 2 naming `approve-push 05e32b87...`, `approve-sync d1fc...`.
- praveenlavu `/approve approve-push 05e32b87...` (6838727063). R2-A apply exit 0: `created 3, updated 0, failed 0 / T001 #3 / T002 #4 / T003 #5`. Read back: #3..#5 by `github-actions[bot]`, label `ghp:task`, sub-issues of #1. No Project call (no Project configured; the plan says so).
- Second apply, recorded which: the plan is NOT unchanged after the first apply (it becomes `create 0 · unchanged 3`, digest `b01c...`), so it has another hash and R2-A exits **2** (`approve-push 27dd01a9...`). After praveenlavu's `/approve approve-push 27dd01a9...` (6838768056), R3-A exits 0 with `created 0, updated 0, failed 0` (issue count unchanged). So "requires approval even with nothing to send" holds, with a fresh approval of the no-op plan.

**L-047 PASS** (`--sync`; `--resolve` refused).
- praveenlavu: `/answer Q1: 09:00 is the fictional default` (6838768544), `/approve D1` (6838769019), `/reject D2` (6838769381). Decisions had been posted by the bot (R2-A, comment 6838740044, after `/approve approve-decisions c3c46e20...` 6838726653).
- R3-A `qsync` exit 0 `"answered": ["Q1"]`; diff: `- [x] Q1: ... → 09:00 is the fictional default (PO comment 6838768544)`. `dsync` exit 0 `"status": {"D1": "validated", "D2": "rejected"}`; diff `→ validated · by: approver · via: 6838769019`, `→ rejected · ... via: 6838769381`. Sync writes are local files only.
- `dresolve` (`ghp decisions 1 --resolve D1 --validated --by PO`) exit 2: `--resolve is refused: approvers are configured on the target branch, so verdicts come from their /approve D<n> and /reject D<n> comments ...` (the text of `E_RESOLVE_APPROVERS`; the code name itself is not printed). It is refused before the no-terminal check.
- Plan wording: `/answer Q1 fictional answer` (no colon) is malformed in ghp's grammar (`_CMD` needs `Q<n>: text`); the colon form was used.

**L-048 PASS** (review and wait gates as the bot, pins).
- R1-A: `check --gate review-spec|review-plan|review-review` exit 2, each printing `/approve <gate> <hash>` (1a1470ed..., b8d63e3b..., 7abe4266...). praveenlavu approved each (6838727494, 6838728351, 6838728800). R2-A: each exit 0 `"met": true, "pinned": <id>`; `.ghp/approvals/1.json` printed with `version 1`, per gate `hash`, `approval {author 22502189, body_sha256, id, last_edited_at null}` and `seen`.
- Wait gates: R2-A `wait-po-answers` exit 2 `1 open (Q1)`, `wait-po-validation` exit 2 `2 open (D1, D2)`; R3-A after `/answer` and `/approve D1` `/reject D2`: both exit 0 `"met": true`.
- `check --gate approve-push` exit 2: `gate approve-push is not checked here: the apply it guards (--apply) requires the approver comment itself` (E_APPLY_GATE).
- Observation O-1: in R1-A, BEFORE any questions or decisions comment existed, both wait gates exited 0 (`"open": []`) although the ledger had an open Q1 and the decisions file open D1, D2. By code design (`wait_state`: "the ledger and the decisions file never MEET a gate"; no posting = nothing to wait for), but noted for the owner.

**L-049** (untrusted lines; story #2, each sub-case after a spec change to a new hash; hash read on the laptop by `ghp approvals check 2 --gate review-spec`, 0 writes, and equal to the runner's). Positive control first and last: valid `/approve review-spec <h>` exit 0 (6838936704; 6839047318).
- (a) `/appr<U+200B>ove review-spec <h>` exit 2, `1 newer malformed command line(s) by an approver for this plan` **PASS**.
- (b) `... <h><U+200F>` exit 2, `2 newer malformed ...` **PASS**.
- (c) fenced block with `  /approve ...` exit 2 (not a command; first line is the fence) **PASS**.
- (d) bot's own `/approve review-spec <h>` (bot-approve step, exit 0) then check exit 2 **PASS** (reason `bot-author` per code; the text does not name it).
- (e) valid approve (6838996963) then newer `/reject review-spec <h>` (6838997273): exit 2 **PASS** (text does not name the reject).
- (f) valid approve then PATCH by the owner: exit 2 `1 newer approver comment(s) edited after posting` **PASS**.
- (g) valid approve, newer owner note minimized (`isMinimized true, outdated`): exit 2 `1 newer approver comment(s) hidden (minimized)` **PASS**.
- (h) approve old hash a9f48e63..., then spec change: exit 2, text asks for the new hash 25ab3671... **PASS**.
- (i) as worded, `Quoting the kit: [spec-kit-ghp:approval-request] review-spec <h> (fictional)` newer than a valid approve: exit **0** (pinned 6839037123) **FAIL vs Expected** (finding F-B5-2). Variants:
  - (i2) owner comment starting with `[spec-kit-ghp:approval-request] review-spec <h>`: exit 2. Cause (read back with ghp's own code on the laptop): the comment starts with the kit marker, so `excluded()` makes its author, the approver 22502189, an excluded "kit posting identity" for the whole issue; from then on every owner approval on #2 is `excluded-author` (finding F-B5-3). (i3) `> [spec-kit-ghp:...]` on #2 exit 2 for the same reason, not because of the quote.
  - Story #8, request posted by the bot: (q) `> [spec-kit-ghp:approval-request] review-spec <h>` (a quote) newer than a valid approve: exit **0** (the quote does not make it wait). (r) exact `[spec-kit-ghp:approval-request] review-spec <h>` newer than the approve: exit **2** (waits) **PASS**; (r2) a new owner approve newer than the request: exit 0.
- 0 GitHub writes by any check (laptop logs: only the owner's fixture comments; `L-049-local.jsonl` 42 reads, 0 writes).

**L-050** (trust).
- (a) **PASS**: branch `ghp-live/b5-trust` config `approvers: [22502189, 41898282]` + spec change (new hash bf5c4bc2...); only the bot approved; check on that ref exit 2. Not separable live: whether it failed because trust is read from `main` or because the bot is excluded anyway (both hold by code).
- (b) **PASS**: `git clone --no-checkout` + `git remote set-head origin -d` (`fatal: ref refs/remotes/origin/HEAD is not a symbolic ref`), check exit 2 `E_TRUST_BASE_UNRESOLVED: in CI the target branch must be readable ...`, while the same check in the normal clone of the same run exit 0.

**L-051 PASS** (D12). Laptop, clone on `...-b`, `origin/HEAD -> origin/main`: `ghp approvals check 2 --gate review-spec` exit 2 `no valid approval ...` right after the bot's check met the gate with the same comment 6838936704. ghp's own `approvals.counts(<6838936704>, {22502189}, 22502189)` = `(False, 'excluded-author')`. Log: 3 reads, 0 writes.

**L-052 BLOCKED (environment), partly PASS** (pr create as the bot).
- Harness corrections (plan wording, not product): `--push` without `--from-prepared` is refused (`--push needs --from-prepared`, exit 2); `--head` is required; so the steps ran `ghp pr create 1 --title ... --body-file pr-body.md --base main --head <branch>` with the branch pushed by praveenlavu. `ghp` also refused unticked tasks (`3 unticked task(s) ... pass --allow-incomplete`), as documented.
- Before approval exit 2 naming `approve-pr f6c3e9c3...` **PASS**. After `/approve approve-pr f6c3e9c3...` (6838855388) the approval was accepted and the write was attempted: exit 1 `E_GH_HTTP (HTTP 403): GitHub Actions is not permitted to create or approve pull requests.` The repository's `actions/permissions/workflow` reads `can_approve_pull_request_reviews: false` (GitHub's default "Allow GitHub Actions to create and approve pull requests" off). Not changed by the agent (a repository security setting: the owner's call). No PR was opened by the bot (finding F-B5-1).
- The preview body shows `Closes #1` twice (body file + ghp's own line) (finding F-B5-4).

**L-053 PASS.** `ghp activate 1 --apply` exit 2 `E_NO_PROJECT: activate needs a Project: set project_number in ghp.config.json`; no approval text appears, so the Project check runs before the approval lookup.

**L-054 PASS; F-1 CONFIRMED** (done in comment mode).
- Deviation: the bot could not open the PR (L-052), so praveenlavu opened PR #6 (`Closes #1`) from the fixture branch and merged it (`gh pr merge 6 --merge`); #1 closed `completed` by PR #6. `done` only needs a merged closer, so the F-1 question is unaffected.
- `ghp done 1 --plan-out .ghp/plan-done.md` (on `main`) exit 0: `DoD checklist: ready, 5 of 5 ticked`, `Merge: PR #6 into main`, `Step 2: skip: already closed (completed) by PR #6`, `Step 3: skip: no Project is configured`, ledger comment shown, `warning: 3 of 3 sub-issues are still open`.
- `ghp done 1 --apply --expect-plan .ghp/plan-done.md` exit 2 naming `approve-done 8376b234...`. praveenlavu `/approve approve-done 8376b234...` (6838898639).
- Same apply again (L054-2) exit **2**: `comment approval: no approval of this plan was pinned for issue #1 (ghp approvals sync <run> pins it when the approver's comment is read), so the story is not closed.` (E_NOT_PINNED). So the approval comment itself was accepted; only the pin is missing.
- `ghp approvals check 1 --gate approve-done` exit **2** E_APPLY_GATE (`gate approve-done is not checked here ...`).
- `ghp approvals sync x --dry-run` exit **2** E_RUN (no such run). A run state crafted paused at `approve-done` (`sync-crafted`) exit **2** E_RUN: `approve-done` is not a shipped step (`STEP_IDS`). Control (L054-3): the identical crafted state paused at `approve-pr` passes `read_run` and fails later (E_ARTIFACT, no `.ghp/plan-pr.md`), so the refusal above is the step id, not the harness file.
- Verdict: **F-1 CONFIRMED.** In comment mode `ghp done --apply` requires a pin of `approve-done`; `check` refuses that gate, `sync` only targets a run paused at a shipped step and the workflow has no `approve-done` step, and no other verb writes that pin. E_NOT_PINNED's own text points to `ghp approvals sync <run>`, which cannot pin it. The only remaining route is a hand-written pin file (docs: "a pin written by hand from a live /approve satisfies ghp done"), which was NOT done. #1 got no ledger comment. Nothing unsafe was written. Severity 2.

**L-056 PASS** (101+ comments). Story #7: 101 filler comments by praveenlavu (paced 1.25 s), then `/approve review-spec 9e80d7e0...` as comment 102 (6839242415). Bot check exit 0 `"pinned": 6839242415` (found on page 2). Laptop `ghp comments 7` exit 0: 102 rows, no dropped-comment note, `wrote .ghp/context/7.md`; requests: 2 GraphQL queries + REST `page=1` and `page=2`, 0 writes.

**L-057 PASS** (Actions token vs user Project). Working-tree config + `project_owner praveenlavu, project_number 3`. `ghp project show` exit 1 `E_GH_GRAPHQL (HTTP 200): Could not resolve to a ProjectV2 with the number 3.` `ghp doctor --json` exit 0: `identity pass "signed in as user id 41898282"`, `scopes unknown` (no X-OAuth-Scopes header for the Actions token), `project-workflows unknown "GitHub answered HTTP 200 (unexpected)"` (finding F-B5-5), `protection unknown (HTTP 403)`. Read only.

**L-058 NOT RUN:** owner option A/B not chosen.

## Request-log counts (laptop, praveenlavu)

| log | reads | writes | writes are |
|---|---|---|---|
| setup | 4 | 5 | label, issues #1 #2, (#7 #8) |
| owner-comments | 0 | 12 | approvals/answers/verdicts on #1 |
| L-049-* / L-049D-* | 1 | 26 | owner fixture comments, 1 edit, 1 minimize (GraphQL `minimizeComment`) |
| L-049-local, L-049D-local, L-050a, L-051, L-056-comments, probes | 70 | 0 | none (every ghp check/comments run on the laptop: 0 writes) |
| L-054-fixture | 4 | 2 | PR #6 create, merge |
| L-056-fixture | 6 | 102 | 101 fillers + approval |
| dispatch | 120 | 30 | 30 `gh workflow run` dispatches |
| cleanup | 6 | 7 | 6 closes, workflow disable |

## Cleanup (read back after)

| Item | State |
|---|---|
| issues #2, #3, #4, #5, #7, #8 | closed `not_planned` |
| issue #1 | closed `completed` by the merge of PR #6 (left as evidence) |
| PR #6 | merged on purpose (L-054); no open PR |
| branches | all three deleted; only `main` remains |
| workflow | `ghp-live.yml` disabled, then removed from `main` (`036d2b0`); `actions/workflows` total_count 0 |
| `ghp.config.json` | removed from `main` (`1bb16af`) |
| fixture files merged by PR #6 (`dod/`, `specs/`, `pr-body.md`, `src_fictional.txt`) | removed from `main` (`66ae1ca`); `main` holds only `README.md` |
| left in place | comments (evidence), labels, run records, `main` history; R2 itself (owner-only delete) |

## Findings

- **F-1 (severity 2) CONFIRMED.** Comment-mode `ghp done --apply` needs an `approve-done` pin that no supported command can create (evidence L-054). So `speckit.ghp.done` and `ghp-link-on-merge` with `close-story: true` cannot close a story in comment mode without a hand-made pin. New task: fix (e.g. let `approvals check` or `done` itself pin `approve-done`) or state it as a limit in `docs/trust-and-limits.md`, owner signs off.
- **F-B5-1 (severity 3, docs/doctor; owner may rate 2).** With the bot as agent, `pr create --apply` needs the repository setting "Allow GitHub Actions to create and approve pull requests", which is off by default; the docs do not say so and `doctor` does not check it. ghp behaved correctly (approval checked, one write attempted, exit 1 named 403, not retried).
- **F-B5-2 (severity 3, docs/plan).** An approval request is recognised only when the comment STARTS with `[spec-kit-ghp:approval-request] <gate> <hash>` (`_REQUEST.match`). A mid-line mention or a Markdown quote (`> ...`) does not make the step wait. `docs/trust-and-limits.md` ("A comment that looks like ghp's approval request, or quotes a comment ghp posted, makes the step wait") overstates it for review/apply gates. Not unsafe (ghp posts no requests; the approval still is the approver's).
- **F-B5-3 (severity 3, diagnosability).** An approver who posts any comment starting with `[spec-kit-ghp:` becomes an excluded identity for that issue (until the comment is deleted), and every later approval of theirs fails with the generic `no valid approval ...` text. More generally the refusal never says why a present `/approve` did not count (bot-author, newer reject, excluded author). Fail-closed, but hard to diagnose.
- **F-B5-4 (severity 3, cosmetic).** `pr create --body-file` whose body already holds `Closes #1` gets a second `Closes #1` line appended.
- **F-B5-5 (severity 3, wording).** doctor's `project-workflows` row says `GitHub answered HTTP 200 (unexpected)` when the Project cannot be resolved (a GraphQL NOT_FOUND inside HTTP 200).
- **O-1 (observation, owner to decide).** Wait gates are met vacuously when no questions/decisions comment has been posted, even with open items in the ledger/decisions file (by design: local files never meet a gate).
- **Plan wording (not product):** `/answer Q1 <text>` needs `Q1: <text>`; `pr create --push` needs `--from-prepared`, and `--head` is required without it; L-046's "plan unchanged" second apply is in fact a changed (no-op) plan; the E_RESOLVE_APPROVERS code name is not printed, only its text.
