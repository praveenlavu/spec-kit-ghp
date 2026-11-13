# C-R1a evidence: terminal approval mode on R1 + Project 3, released ghp 0.1.1 (B0, B1, B2, 0.1.1 progression, B8)

Run on 2026-11-13 (UTC from 16:17; batch stamp `20261113-161702` in `$LIVE/stamp`) by the C-R1a live-test executor (an AI agent). All data is fictional. Plan: `docs/plans/PLAN-LIVE-0.1.md` B0, B1, B2, B8; 0.1.0 baselines `docs/evidence/L-B0.md`..`L-B2.md`; changelog `ghp-public-export/CHANGELOG.md` section 0.1.1.

- Account: `praveenlavu` only (`G gh api user` -> `praveenlavu 22502189`). Every `gh`/`ghp`/fixture command ran with the per-command prefix `GH_TOKEN=$(gh auth token -u praveenlavu) ` (written `G` below); git used the clone-local helper printing `username=praveenlavu` and `password=$(gh auth token -u praveenlavu)` (`git credential fill` showed `username=praveenlavu` only). `gh auth switch/refresh/login` not run. Only the owner's own account was used. No token in any output, log or this file.
- Target: R1 = `praveenlavu/ghp-spike-scratch` (private, `main`) + user Project 3 (`PVT_kwHOAVdbLc4Bqvdw`), terminal approval mode. Project 2 and the Project UI settings were not touched. `tests/live` was not run.
- Product: released **0.1.1**, `pip install "spec-kit-ghp @ git+https://github.com/praveenlavu/spec-kit-ghp.git@v0.1.1"` into `$LIVE/venv`; `direct_url.json` commit `e440627644e6e5268ef3ce19ee4547f75799e03d`; `ghp --version` -> `ghp 0.1.1`. Python 3.9.6, gh 2.96.0. No product code changed.
- Harness: `$LIVE/bin` (request-log wrapper `gh` + `reqlog.py`, `writes.py`, `fx.py`) copied from the 0.1.0 harness; only change: the wrapper's fixed default paths now point at `live011` (its venv provides `ghp.gh.is_graphql_write` from 0.1.1). `$LIVE/env.sh` defines `r <id> ghp ...` (own log `log/<id>.jsonl`, out/err in `out/`, stdin `/dev/null`) and `fx`. Every log has one `auth_token` line from the `G` prefix itself.
- Gate answers: every product write was previewed first and approved by the executing agent (an AI agent, not an independent human), as in B2 on 0.1.0.

`$LIVE = <scratch>/live011`; logs `$LIVE/log/`, outputs `$LIVE/out/`, clone `$LIVE/work`.

## B0 (L-001..L-014) read-only on 0.1.1 — 14/14 PASS, 0 writes

| Case | Command(s) | Exit | Evidence (abbrev.) | reads/writes | Result |
|---|---|---|---|---|---|
| L-001 | `G gh api user`; `repos/..`; Project 3 workflows GraphQL | 0 | `praveenlavu 22502189`; `[true,"main"]`; `Auto-add sub-issues=true, Auto-close issue=false, Item added=true, Item closed=false, PR linked=true, PR merged=true` (unchanged) | 3/0 | PASS |
| L-002 | `ghp --version`, `--help`, `push --help` | 0 | `ghp 0.1.1`; 21 verbs `get comments project stack dod rules questions decisions push activate branch pr gate calibrate kits strict done status approvals doctor setup`; epilog `tamper-evident, not tamper-proof`; push shows `--apply --expect-plan --plan-out --workflow` | 0/0 | PASS |
| L-003 | `G ghp doctor --json`; `--offline --json` | 0 / 0 | identity pass `user id 22502189`, scopes pass `repo and project`, config pass, config-target warn, project-workflows warn naming only the 4 enabled ones (Auto-close/Item closed absent = off), protection warn `not available on a private repository of a free GitHub account`; offline: identity/scopes/project-workflows/protection `unknown (offline)` | 4/0; 0 api | PASS |
| L-004 | `G ghp project show` | 0 | `Todo f75ad846 <- status_todo`, `In Progress 47fc9ee4`, `Done 98236657`; `#1 Item closed disabled` ... `#3 Auto-close issue disabled`, others enabled | 2/0 | PASS |
| L-005 | `get/dod/push/done/activate 204` | 2 each | get, dod, **push**, activate: `E_NOT_AN_ISSUE: ...#204 is a pull request, not an issue`; done: `E_NO_LEDGER` (allowed). No `dod/DoD_204.md` | 1,1,1,0,1 / 0 | PASS (0.1.0 FAIL B0-F1 is **fixed**: push now names E_NOT_AN_ISSUE) |
| L-006 | 5 ref forms | 0,0,0,2,2 | three identical JSON (sha1 `9cb01bc9...` x3, same as 0.1.0); `E_PULL_REQUEST_URL`; `E_FOREIGN_REPO` | 1,1,1,0,0 / 0 | PASS |
| L-007 | `get 999999` | 1 | one line `error: E_GH_HTTP (HTTP 404): Not Found` | 1/0 | PASS |
| L-008 a | no config | 2 | `No ghp config found. Run \`ghp setup\` ...` | 0 | PASS |
| L-008 b | `gh_token` key | 2 | `top-level entry #5: token settings are not supported` (value not shown; key not named, same as 0.1.0 B0-F4 design) | 0 | PASS (as 0.1.0) |
| L-008 c | org owner type: get / gate / strict check / done / branch --id-from / status / status --json | 2,3,3,3,3,3,3 | all name `E_ORG_PROJECT_UNSUPPORTED`; **status now: `Why: ghp.config.json is invalid (E_ORG_PROJECT_UNSUPPORTED: only a Project owned by a user account is supported)`** | 0 each | PASS (0.1.0 B0-F2 **fixed**) |
| L-008 d | `dod_gate` | 2 | `` `dod_gate` was renamed to `gate`; move `dod_gate.block` to `gate.block` `` (no version named) | 0 | PASS (0.1.0 B0-F3 **fixed**) |
| L-008 e | `GHP_CONFIG=other.json` with approvers: get / approvals check (no origin) / in clone: approvals check, doctor --offline | 0 / 2 / 2 / 1 | get not refused (as 0.1.0, B0-F5 plan scope); `...no target branch to read them from (no origin/HEAD)`; E_NONROOT_TRUST text; `fail approvers` same text | 1 / 0 / 0 / 0 | PASS (as 0.1.0) |
| L-009 | `project_number 999`; `project_owner github` -> `project show` | 1 / 1 | `E_GH_GRAPHQL (HTTP 200): Could not resolve to a ProjectV2 with the number 999.`; `... to a User with the login of 'github'.` | 1/0 each | PASS |
| L-010 | `status_active: Doing` -> activate 196; `status_todo: Backlog` -> push 196 (on `feature/gh196-issue`) | 2 / 2 | `E_STATUS_OPTION_MISSING: Status option 'Doing' (status_active) not found; options found: 'Todo', 'In Progress', 'Done'`; same for `'Backlog' (status_todo)` | 2/0, 2/0 | PASS |
| L-011 | `status 196` text/json/offline/catch-up; GraphQL read | 0 x4 | `waiting on stage merged. Next: a person merges PR #204 on GitHub, then: git fetch origin` (0.1.0 B2-F6 **fixed**: next names the merge first); `progress {total 7, completed 0}`, `column In Progress`; offline `board: not checked (offline): open-at-done, ...`; `Catch-up: nothing to catch up.`; GraphQL `[{"number":204,"state":"OPEN"}]`, `{"total":7,"completed":0}` | 29/0, 29/0, 0, 29/0, 1/0 | PASS |
| L-012 | approvals check review-spec / review-everything / approve-push; `approvals sync 987e533b --dry-run` in a copy of the T34 run folder | 0 / 2 / 0 / 2 | `"mode": "terminal", "met": true, ... nothing was read`; `unknown gate: ...`; terminal met true; `approvals sync works in comment mode only ... nothing was read or resumed` | 0 api each | PASS |
| L-013 | `comments 196`; `rules 196`; `gate 196 --src . --base origin/main --summary-out`; `gate 196 --offline` | 0 x4 | 3 marked comments, each `author 22502189 (User)`, `edits 0`; rules `from [] problems []`; gate `all AC mapped`, `drift {evaluated true, drift false}`, `blocking false`; offline `offline — drift not checked` | 2,1,1,0 / 0 | PASS |
| L-014 | config `mode: strict`: `strict mode`; `strict check 196 --phase implement/pr/done` | 0 / 2 / 2 / 2 | `{"mode":"strict","source":"pull request"}`; implement `test-command, stack-profile-missing` (log has 1 `projectV2(number` read); pr `evidence-missing x2, record-missing, fidelity-missing, stack-profile-missing` + `trust-introducing PR` warning; done = pr + `decision-open` | 0, 20, 0, 0 / 0 | PASS (same findings as 0.1.0) |

## B1 (L-015..L-023) refusals before writes on 0.1.1 — 9/9 PASS, 0 product writes

Fixtures (fx.py, writes as praveenlavu): S1 = **#294** `L-B1 20261113-161702 story (fictional)` (label `ghp:live-test`, AC1/AC2, added to Project 3, item `PVTI_lAHOAVdbLc4BqvdwzhIgsK4`, Todo by automation); S2 = **#295** `L-B1 20261113-161702 other story (fictional)` (not on the board). Local: `ghp dod 294 --spec-dir specs/l-b1` (1 read), `specs/l-b1/tasks.md` T001, T002 `[P]`, T003, Q1 in ledger section 2. Fixture writes: `fx-B1` 3, `fx-L-017-act` 1, `fx-L-017-cleanup` 1, `fx-L-019a` 2, `fx-L-020f` 1, cleanup 1.

| Case | Command | Exit (0.1.0 -> 0.1.1) | Output (abbrev.) | reads/writes | Result |
|---|---|---|---|---|---|
| L-015 | `push 294 --expect-plan .ghp/p.md` | 2 -> 2 | `ghp: error: --expect-plan needs --apply` | 0/0 | PASS |
| L-016-1 | `questions 294 --post --plan-out` | 0 -> 0 | `Action: ADD a new comment`, `[spec-kit-ghp:questions] 89b5ff09b9036eb8` | 3/0 | PASS |
| L-016-2 | `decisions --add ...`; `decisions 294 --post --plan-out` | 0,0 -> 0,0 | `D1`; `ADD a new comment (a decisions comment is never edited)` | 0/0; 3/0 | PASS |
| L-016-3 | `push 294 --from specs/l-b1/tasks.md --plan-out` | 0 -> 0 | `create 3 · update 0 · unchanged 0 · orphan 0`, digest `e298436b...` | 7/0 | PASS |
| L-016-4 | `activate 294 --plan-out` | 0 -> 0 | `Status: Todo -> In Progress` | 4/0 | PASS |
| L-016-5 | `branch 294 --apply` (local); plan's literal `pr create --title --body-file --base main --push`; `--from-prepared --push`; `+ --allow-incomplete` | 0; 2; 2; 0 (same as 0.1.0) | `feature/gh294-l-b1-20261113-161702-story-fictional`; `error: --push needs --from-prepared` (B1-F1 plan wording, unchanged); `refusing to open a PR: 3 unticked task(s) ...`; plan `draft yes`, `push yes: git push -u origin ..., before the PR is created`; body ends with `Closes #294` twice (B1-F2 unchanged); `git ls-remote` 0 lines | 0,0,1,3 / 0 | PASS |
| L-016-6 | `done 294 --plan-out` | 0 -> 0 | `DoD checklist: NOT READY, 5 unticked: nothing will be sent, even with --apply` | 0/0 | PASS |
| L-017 | push (T004 added) / activate (fx set In Progress) / questions (Q1 edited): `--apply --expect-plan`; done plan-out with ticked DoD | 2,2,2,2 | `plan changed since approval — re-run the preview and approve again (the current plan is in .ghp/{p,a,q}.md.new)`, `.new` files exist; done: `E_NOT_MERGED ... (nothing was sent)` at plan stage (stale step skipped, as plan allows) | 7,4,3,2 / 0 | PASS |
| L-018 | p18.md tampered (`fictional`->`fictionaL` in table cells) then apply | 2 | `plan changed since approval ... .ghp/p18.md.new` | 7/0 | PASS |
| L-019 | a: #295 closed -> `activate 295 --apply`; b: no project_number; c: `status_active: Done` | 2,2,2 | `E_ISSUE_CLOSED`; `E_NO_PROJECT`; `E_ACTIVE_IS_DONE`; read-back #294 `Todo`, #295 OPEN 0 items | 2,0,0 / 0 | PASS |
| L-020 | a no ledger; b unticked preview / `--apply`; c ticked, no PR; d `--pr 99999`; e `--pr 1 --pr 2`; f closed not_planned (preview, apply) | 2; 0/2; 2; 2; 2; 2/2 (same as 0.1.0) | `E_NO_LEDGER`; `NOT READY ...`/`E_DOD_NOT_TICKED`; `E_NOT_MERGED ... --pr N`; `E_PR_NOT_FOUND: there is no pull request #99999 ...`; `--pr was given twice`; `E_CLOSED_NOT_COMPLETED` | 0;0/0;2;3;0;1/1 / 0 | PASS |
| L-021 | a body closes #295; b commit `Fixes #295`; c + `--allow-extra-closes`; d T002 unticked, then `--allow-incomplete` | 2,2,0,2,0 | `E_BODY_CLOSES_OTHERS`; `E_COMMITS_CLOSE_OTHERS`; plan with `⚠ commit messages would also close ...#295 (accepted with --allow-extra-closes)`; `refusing to open a PR: 1 unticked task(s) in tasks.md: T002`; `draft yes — 1 unticked task(s): T002`. ls-remote 0 lines | 0,0,3,1,3 / 0 | PASS |
| L-022 | dup T002; `TX1`; `T1234567890`; ~1300-char title | 2,0,2,0 (same codes as 0.1.0) | `E_DUPLICATE_TASK_ID`; **plan `create 3` now with `⚠ tasks.md line 6 is not read as a task (a task is \`- [ ] T\` and 3 to 9 digits, then a space): - [ ] TX1 Bad id (fictional)`, digest `e298436b...` identical to the plan without the line**; `E_BAD_TASK_ID`; `create 4`, `cut to 256` | 0,7,0,7 / 0 | PASS (0.1.0 B1-F4 fixed; B1-F5 design unchanged) |
| L-023 | grep `gh[pousr]_|github_pat_|Authorization` over all 91 `.err`, outputs and logs | — | 0 files match; every stderr one `error:` line except argparse (L-015 2 lines, L-020e 4 lines) | — | PASS |

B1 cleanup: #294 closed `not_planned` (Project item left at Todo), #295 closed `not_planned` (by L-020f); local branches deleted, never pushed; ledgers/plans copied to `$LIVE/keep/b1/`.

## B2 (L-024..L-037, L-036a) CLI lifecycle, terminal mode, automation OFF — 13 PASS, 2 PARTLY (TTY), 0 FAIL

Objects (writes as praveenlavu to R1 and Project 3): S3 = **#296** `L-B2 20261113-161702 story: pickup hours (fictional)`; tasks **#297..#300** (by `ghp push`); **PR #301** (by `ghp pr create`, merged by the agent, merge commit `2d053021`); S4 = **#302**, side branch `ghp-live/l034-20261113-161702`, **PR #303** (into the side branch); S5 = **#304**; E1 **#305** (`ghp:epic`), F1 **#306** (`ghp:feature`), S6 **#307**. Spec dir `specs/c-b2` (the 0.1.0 `specs/l-b2` already exists on main with a tasks.md, so the first `dod --spec-dir specs/l-b2` gave stage board-tasks; the local ledger was re-made with `specs/c-b2`: harness note).

Stage of S3 (`status 296 --json` after each case): L-025 `plan` -> L-026 `plan` -> L-027 (2 questions posted) `questions-answered` -> (answered in ledger) `questions-posted` -> (3rd comment) `plan` (no plan.md yet) -> L-028 + plan.md `tasks-ticked` -> L-029 `tasks-ticked` -> L-030 `gate` -> (gate/review log committed) `pr-link` -> L-031/L-032 `merged`, next `a person merges PR #301 on GitHub, then: git fetch origin` -> L-033 `done`/`none` -> L-035 reopened `open-at-done` -> closed `done`. Same backward step on questions as 0.1.0 (B2-F5, by design).

| Case | Commands | Exit | Evidence (abbrev.) | reads/writes | Result |
|---|---|---|---|---|---|
| L-024 | setup starter; again; `--approver` (no force); `+--force`; team `--approver 22502189 --force` | 0,2,2,2,2 | config holds names/numbers only, 1 read `query { viewer { databaseId login __typename } }`; **`error: E_EXISTS: ...`** (file byte-identical); `E_EXISTS` first; **`error: E_APPROVER_PROFILE: ...`**; **`error: E_APPROVER_SELF: an approver cannot be the account you are signed in as ...`** | 1,0,0,1,1 / 0 | PASS (0.1.0 B2-F1 **fixed**) |
| L-025 | fx S3; `dod 296 --spec-dir specs/c-b2`; status | 0 | ledger created `ac_chars 74`; stage `plan` | 1/0, 8/0 | PASS |
| L-026 | activate plan / apply `--expect-plan` / rerun | 0,0,0 | `(not on the Project) -> In Progress`; `moved #296 to In Progress`; `nothing sent: already In Progress`; history: `github-project-automation '' -> Todo` and `praveenlavu Todo -> In Progress` both 16:25:02Z (automation set Todo first, as on 0.1.0) | 3/0, 5/2 (`addProjectV2ItemById`, `updateProjectV2ItemFieldValue`), 4/0 | PASS |
| L-027 | Q1,Q2 post; Q2 edited, post; answered, 3rd post | 0 x6 | `ADD a new comment` each; comments 6858734574, 6858736019, 6858744358; first comment `created_at = updated_at = 16:25:41Z`; 0 PATCH | 3/1 per apply (`POST issues/296/comments`) | PASS |
| L-028 | tasks.md T001..T004 (T002 `[P]`) **+ malformed `- [ ] T5 ...`**; plan; apply | 0,0 | plan `create 4` + `⚠ tasks.md line 7 is not read as a task ...`; `created 4, updated 0, failed 0` #297..#300; each `parent=296 labels=ghp:task status=3:Todo`; write gaps >= 1 s; no ALREADY_ON_BOARD re-read this time (race not seen) | 7/0; 15/12 (4x POST issues, 4x add, 4x update) | PASS |
| L-029 | T002 renamed, T004 removed; plan; apply; rerun | 0,0,0 | `create 0 · update 1 · unchanged 2 · orphan 1` (orphan T004 #300); exactly `PATCH issues/298`; rerun `unchanged 3 · orphan 1` | 12/0, 12/1, 12/0 | PASS |
| L-030 1-5 | `branch 296` / `--apply`; `--id-from feature/gh296-x`; code+test (OK); gate; drift via `fx patch-body` + `gate.block:["ac_drift"]`; offline; restore | 0,0,0,0,1,0,0 | `feature/gh296-l-b2-20261113-161702-story-pickup-hours`; `296`; drift false; **exit 1 `blocked_by ["ac_drift"]`**; offline `drift not checked`; back to drift false | 0,0,1,1,0,1 / 0 | PASS |
| L-030 6 | decisions add / post plan / apply; `--resolve D1 --validated --by PO` | 0,0,0,2 | D1; comment 6858813862; resolve: `error: --resolve records the PO's verdict, which only a human may do: run it in your own terminal (this shell has no terminal on stdin ...)`, file unchanged (cmp) | 0,3/0,3/1,0 | PARTLY (needs a real TTY; not circumvented, as on 0.1.0) |
| L-031 | `pr create 296 --from-prepared --push` plan / apply / dry run again | 0,0,0 | title `Pickup hours #296`; body had a quoted `` `Closes #296` `` and the plan still appends a real last line `Closes #296`; apply: ghp pushed (`2cb62b2` on remote, 2 extra helper `auth_token` lines), `created PR #301`, PR body last line `Closes #296`, author praveenlavu; `closedByPullRequestsReferences [301]`; S3 stayed In Progress; rerun `existing PR #301 is already open ...; nothing will be created` | 3/0, 3/1 (`POST pulls`), 3/0 | PASS |
| L-032 | `status 296` | 0 | `waiting on stage merged. Next: a person merges PR #301 on GitHub, then: git fetch origin` | 21/0 | PASS |
| L-033 | agent `gh pr merge 301 --merge`; ClosedEvent; done plan; apply (timed); history; rerun | 0 x5 | ClosedEvent 16:31:39Z actor praveenlavu `COMPLETED`, closer `PullRequest 301`; Status still `In Progress` 30 s after merge; plan `skip: already closed (completed) by PR #301; recorded, not closed again`, `In Progress -> Done`, `warning: 4 of 4 sub-issues are still open`; apply 9 s: `posted the ledger comment` / `Status is Done (set by ghp)`, no PATCH; history `16:32:29Z praveenlavu 'In Progress' -> Done`; rerun `nothing sent: already done` | 8/0, 14/2 (`POST comments`, `updateProjectV2ItemFieldValue`), 9/0 | PASS |
| L-034 | S4 #302 on board; side branch; PR #303 into side (plan warns `Closes keywords are ignored; ghp done will close it`); agent merge; done `--pr 303` plan / apply; rerun | 0 | #302 open after merge; plan `close #302 as completed`, `wait up to 30 s`; apply 45 s wall: `POST comments` 16:34:03 -> `PATCH issues/302` 16:34:04 -> 12 Status reads 16:34:06..40 -> `updateProjectV2ItemFieldValue` 16:34:41 -> read-back; **PATCH to Status write 37 s** (0.1.1 docs now say about 35 s; same as 0.1.0); history `16:34:43Z praveenlavu 'Todo' -> Done`; closed by praveenlavu 16:34:05Z; rerun `nothing sent: already done` | 9/0, 27/3, 10/0 | PASS |
| L-035 | `fx reopen 296`; status; `activate --apply`; `fx close completed` | 0, 2 | **`Why: the issue is open but its Project item is at Done: the card was moved to Done without closing the issue, or the issue was reopened ...`**; **`E_ALREADY_DONE: ... the card was moved to Done without closing the issue, or the issue was reopened ...`**; stage back to `done` | 5/0, 4/0 | PASS (both causes named) |
| L-036 | S5 #304 add-item, `fx set-status Done` 16:35:39, watch 6 x 5 s; status | 0 | state `open` at 16:35:44..16:36:12; stage `open-at-done` with the same two-cause text (0.1.0 B2-F7 **fixed**) | 5/0 | PASS |
| L-036a | E1 #305 rule, F1 #306 sub of E1, S6 #307 sub of F1; `rules.enabled`; dod / rules / `--confirm` | 0,0,0 | `Rules from: 305`; `from [305]`, `approved 1`, `### BR-305-1 · approved · 3e119982`; `reviewed BR-305-1@3e119982`, `written true` | 3,3,0 / 0 | PASS |
| L-037 | strict verify (red `53a6098` -> green `2476025`, `python3 -m unittest discover -s c_b2 -q`); bad history; `strict override 296 l2` | 0, 2, 2 | `ok true, runs 3, 0.44 s`; `red-passes: AC-1: the tests pass at the red commit 13d81a1a2d88`; override `only a human may do: run it in your own terminal` | 0 api | PARTLY (verify PASS; override needs a real TTY, as on 0.1.0) |

## 0.1.1 changelog progression (terminal-mode bullets) — 12 PASS, 1 BLOCKED; 3 findings (C-F2..C-F4)

| Changelog bullet | Live check | Evidence | Result |
|---|---|---|---|
| `ghp gate` reads test ids in backticks in section 3 | ledger #296 section 3: backticks `` `c_b2/test_pickup.py::test_monday` ``, `` `PickupTest.test_sunday` `` / plain control / backticked unknown `` `c_b2/test_pickup.py::test_nowhere_l011` `` | `all AC mapped` / `all AC mapped` / `mapped [AC-1], unmapped [AC-2], "1 AC without a known test"`; 1 read each, 0 writes (`P-gate-*`) | PASS (0.1.0 B2-F2 fixed) |
| `ghp done` comment says when the ticks are not in the ledger at the merge commit; the plan warns before the yes; old plans refused | S7 **#308**: DoD unticked on branch, PR **#309** merged (`500007b5`), boxes ticked afterwards in the working tree only (merge commit 0 of 5) | plan: `warning: the ledger at merge commit 500007b52c0f has 0 of 5 boxes ticked, not the 5 of 5 counted here ...` and comment text `The linked file at the merge commit has 0 of 5 ticked: the ticks counted here are not in it`; plan approved, then `fx set-status 308 Todo`, apply `--expect-plan` -> exit 2 `plan changed since approval ... .new` (diff: `Status: In Progress -> Done` vs `Todo -> Done`), 0 writes; a done plan made by **0.1.0** (`$S/live/venv`) on the same state -> 0.1.1 apply exit 2 `plan changed since approval`, 0 writes; fresh plan applied: 2 writes (`POST comments`, Status), comment on GitHub carries the sentence, #308 `CLOSED/COMPLETED`, Status `Done`. Control: S3 (#296, ticks in the merge commit) plan had no such note | PASS |
| push preview lists malformed task lines; plan/writes unchanged | L-022 (`TX1`) and L-028 (`T5`) | warning line with the required form; L-022 digest `e298436b...` identical with and without the line; L-028 apply wrote exactly 4 issues for the 4 real tasks (12 writes as on 0.1.0) | PASS |
| `ghp push <PR>` without a ledger refuses `E_NOT_AN_ISSUE` | L-005 `push 204` | `error: E_NOT_AN_ISSUE: ...#204 is a pull request, not an issue`, exit 2, 1 read | PASS |
| every `ghp setup` refusal starts with its error code | L-024 + `P-setup-*`: E_EXISTS, E_APPROVER_PROFILE, E_APPROVER_SELF, E_TERMINAL, E_REPO, E_PROJECT, E_PROJECT_OWNER, E_FLAG (x2), E_STATUS, E_STATUS_SAME, E_APPROVERS, E_OWNER (`--owner=-bad`) | each `error: E_<CODE>: ...`, exit 2, config untouched; argparse `--profile gold` stays a usage error. **But `--owner bad--x` was accepted (exit 0, file written) although E_OWNER says "single hyphens"** (C-F3) | PASS (+C-F3) |
| `ghp status` names E_ORG_PROJECT_UNSUPPORTED for an org Project; before the merge, next is the merge | L-008c (`project_owner_type: organization`); L-011/L-032; extra read-only probe with `project_owner: "github"` | status `Why: ghp.config.json is invalid (E_ORG_PROJECT_UNSUPPORTED: ...)`, exit 3, 0 requests; `Next: a person merges PR #204 on GitHub, then: git fetch origin`. Probe: `status 196` with `project_owner: github` -> exit 0, `board: not checked (GitHub could not be read)`, no code; `activate 304` -> exit 1 `E_GH_GRAPHQL ... Could not resolve to a User with the login of 'github'` (3 / 2 reads, 0 writes) (C-F4) | PASS (+C-F4) |
| status/activate explain open-at-Done by both causes | L-035, L-036 | both texts name "moved to Done without closing" and "reopened" | PASS |
| doctor wording for a nonexistent Project / repository | `project_number 999`; `project_owner github`; `repo ghp-no-such-repo-l011` | `unknown project-workflows GitHub found no such Project or repository for this sign-in: it does not exist, or this sign-in cannot see it ...` (no "HTTP 200 (unexpected)"); repo: `fail protection GitHub does not know this repository, or the token cannot see it` (exit 1); 0 writes | PASS |
| `ghp branch <n>` without a title is marked not final and names `ghp dod <n>` | `branch 295` (no ledger) | `"final": false, "note": "not the final name: there is no ledger title yet, so the slug is the fallback 'issue'; after \`ghp dod 295\` this names the branch ..."`; `--apply` creates `feature/gh295-issue` (local, deleted) with no note | PASS |
| `dod_gate` refusal says the key was renamed to `gate` | L-008d | `` `dod_gate` was renamed to `gate`; move `dod_gate.block` to `gate.block` `` | PASS |
| `decisions --resolve` without `--note` writes no empty note field | L-030 step 6 | refused before any write without a terminal on stdin; faking a TTY would bypass a human-only guard | BLOCKED (needs the owner's own terminal) |
| `pr create` always writes a real `Closes #N` (dry run and apply); "push yes" next to "nothing will be created" | L-016-5, L-031, L-034, PR #309 | dry run and apply bodies end `Closes #296` even when the body quotes `` `Closes #296` ``; PR #301 body last line `Closes #296`. Push oddity: with PR #301 open, a new local commit `973baa5`; plan `push yes ...` + `existing PR #301 ... nothing will be created`; apply `--expect-plan` exit 0 `nothing sent: PR #301 is already open for this head and base`, 3 reads, 0 writes, no git helper call, remote head stayed `2cb62b2`: **apply does NOT push** (C-F2) | PASS (+C-F2) |
| (0.1.0 B2-F6) `status` before the merge | L-011, L-032 | next names the merge first | PASS |

Not applicable in terminal mode (not run here): comment-mode `ghp done` + `approve-done`, comment-mode set-aside counts, reusable gate workflow log/pins, lifecycle workflow summaries, plan command and docs bullets.

## B8 probes

| Case | What | Evidence | Result |
|---|---|---|---|
| L-061 | S12 **#310**, 3 tasks; two `G ghp push 310 --apply` started together | both exit 0, each `created 3, updated 0, failed 0` (A: #312,#314,#316; B: #311,#313,#315), 9 writes each; `fx sub-issues 310` -> **6 sub-issues, each task twice**; POST issues at 16:39:36, :36, :42, :43, :49, :50. Next `push 310` preview: exit 2 `E_TASK_MARKER_AMBIGUOUS: #311 and #312 both carry the marker of T001; not adopting either`, 0 writes | FAIL -> finding C-F1 (duplicates; later pushes stop safely) |
| L-067 | under #13 (chain 13<-12<-...<-7<-1) create #317 and `POST issues/13/sub_issues` | GitHub **HTTP 422 `You can't add more than 7 layers of sub-issues. To add a sub-issue, remove a parent issue at any level.`**; so depth 8 cannot exist and depth 9 was not attempted. `ghp get 13` -> `parent praveenlavu/ghp-spike-scratch#12`; `get 317` -> `parent null` | PASS (recorded) |
| L-065 | comments on probe issue #317 (praveenlavu's token via fx/gh api) | 65,536 / 65,537 / 100,000 ASCII **accepted** (read back exactly that many chars); 262,145 ASCII **HTTP 422 Validation Failed**; `é` x 32,769 (65,538 bytes) accepted; `€` x 21,846 (65,538 bytes) accepted. So the cap is above 100,000 and at most 262,145 characters, and 65,538-byte multibyte bodies pass (ghp's own 60,000 cap is well below) | PASS (recorded) |
| L-066 | comment 6859081292 edited twice | `userContentEdits.totalCount` 0 -> **2** after one edit -> **3** after two; `ghp comments 317` shows `edits 3 | edited` | PASS (recorded: 3) |
| L-062 | E_CLOSER_FOREIGN | needs a commit or PR of another repository closing an R1 issue; only R1 is allowed in this brief | BLOCKED |

## Request-log counts (GitHub writes, all as praveenlavu, all to R1 / Project 3)

| Source | API writes | Detail |
|---|---|---|
| product (`ghp`, each previewed and approved) | 47 | L-026 2, L-027 3, L-028 12, L-029 1, L-030 1, L-031 1, L-033 2, L-034 PR 1 + done 3, P-dn PR 1 + done 2, L-061 9+9 |
| product git pushes | 3 | branches of PR #301, #303, #309 |
| agent merges (`gh pr merge`) | 3 | #301, #303, #309 |
| fixtures/probes (fx.py) | 55 | issues, add-item/set-status, patch-body, reopen/close, sub-issue links, L-065 6 (1 refused 422), L-066 3, L-067 2 (1 refused 422), cleanup 17 |
| agent git | 1 push + 4 branch deletes | side branch; cleanup |
| B0, B1 and all previews, status, gate, doctor, setup runs | 0 | every such log `writes=0` |

## Created objects and cleanup

| Object | End state (read back) |
|---|---|
| #294 S1, #295 S2 (B1) | closed `not_planned`; #294 item left at Todo |
| #296 S3 | closed `completed` (PR #301; reopened/closed in L-035); item `Done` set by ghp |
| #297..#300 tasks | closed `not_planned`; items left at Todo |
| #302 S4 | closed `completed` by `ghp done`; item `Done` set by ghp |
| #304 S5 | closed `not_planned`; item left at Done |
| #305 E1, #306 F1, #307 S6 | closed `not_planned` |
| #308 S7 | closed `completed` (PR #309); item `Done` set by ghp |
| #310 S12, #311..#316 | closed `not_planned`; items left |
| #317 probe | closed `not_planned`; 6 probe comments left as evidence |
| PR #301, #309 (into main), #303 (into side) | merged by the cases |
| remote branches `feature/gh296-...`, `feature/gh302-...`, `feature/gh308-...`, `ghp-live/l034-20261113-161702` | deleted; `ls-remote` now `feature/gh196-issue ghp-spike/auto ghp-spike/link main` (as before) |
| R1 `main` | at `500007b` (merges of #301, #309: `c_b2/`, `c_p01/`, `specs/c-b2`, `specs/c-p01`, `dod/DoD_296.md`, `Decisions_296.md`, `DoD_308.md`), fictional, left as the cases' merged result like 0.1.0. **No `ghp.config.json` and no workflow added**: `.github/workflows` = `ghp-spike-bot.yml` only |
| Project 3 switches | re-read at end: unchanged (Auto-close issue=false, Item closed=false, others true) |
| local | clone `$LIVE/work` on `main`; untracked `dod/DoD_307.md`, `dod/Rules_305.md`; plans in `$LIVE/keep/` |

## Findings

Severity: 1 = unsafe; 2 = documented flow wrong or data duplicated; 3 = minor. No severity-1 finding: every product write was previewed and matched its plan; only praveenlavu; no token anywhere (scan of out/, log/: 0 hits).

- **C-F1 (sev 2) two `ghp push --apply` at once duplicate every task.** L-061: 3 tasks -> 6 sub-issues (#311..#316), both runs exit 0. A later push stops with `E_TASK_MARKER_AMBIGUOUS` (safe). Docs list "Two pushes at once" as untested; it is now known to duplicate. Repro: two `G ghp push <n> --apply` started in the same second on a story with 3 unpushed tasks.
- **C-F2 (sev 3) `pr create` plan says `push yes` but the apply pushes nothing when a PR is already open.** New local commit `973baa5`, apply -> `nothing sent: PR #301 is already open`, remote stays `2cb62b2`. Repro: open PR via `pr create --push --apply`, commit locally, run `pr create --from-prepared --push` (plan) then `--apply --expect-plan`.
- **C-F3 (sev 3) `ghp setup --owner bad--x` is accepted** (exit 0, file written) though E_OWNER says "single hyphens". Repro: `ghp setup --owner bad--x --repo r --force`.
- **C-F4 (sev 3) an organisation login in `project_owner` (without `project_owner_type`) is not named as E_ORG_PROJECT_UNSUPPORTED**: status says `board: not checked (GitHub could not be read)`, activate `E_GH_GRAPHQL ... Could not resolve to a User`. Repro: config `project_owner: "github"`, `ghp status 196`.
- Still as on 0.1.0 (design or plan wording, no new severity): B0-F4, B0-F5, B1-F1, B1-F2 (duplicate `Closes` when the body has one; now the stated "never skips" design), B1-F3, B1-F5, B2-F3 (TTY guards), B2-F5 (stage moves back on new questions), B2-F4 timing (now documented as about 35 s; measured 37 s).
- Fixed in 0.1.1 and confirmed live: B0-F1, B0-F2, B0-F3, B1-F4, B2-F1, B2-F2, B2-F6, B2-F7.
