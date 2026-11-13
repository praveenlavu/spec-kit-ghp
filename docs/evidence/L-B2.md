# L-B2 evidence: CLI lifecycle in terminal mode, board automation OFF, released ghp 0.1.0 against R1

Run on 2026-11-12 (UTC 23:29 to 23:50; batch stamp `20261112-232950` in `$LIVE/stamp-b2`) by the B2 live-test agent. All data is fictional. Plan: `docs/plans/PLAN-LIVE-0.1.md` batch B2 (L-024..L-037, L-036a).

- Only the owner's own account was used.
- Account: `praveenlavu` (`GET /user` -> `["praveenlavu",22502189]`). Every `gh`/`ghp`/fixture command ran with the per-command prefix `GH_TOKEN=$(gh auth token -u praveenlavu) `. git used the scratch clone's own config (every github.com helper reset, then one helper printing `username=praveenlavu` and `password=$(gh auth token -u praveenlavu)`, the T34 way, so ghp's own `git push` used it too); `git credential fill` showed `username=praveenlavu` only (password line filtered, never shown). `gh auth switch/refresh/login` were not run. No token in any output, log or this file (scan of `log/b2` and `work/b2out`: 0 hits; the other account's name: 0 hits).
- Target: R1 = `praveenlavu/ghp-spike-scratch` (`main`) + user Project 3 (`PVT_kwHOAVdbLc4Bqvdw`), terminal approval mode, R1 alone. Every logged request path is `repos/praveenlavu/ghp-spike-scratch/...`, `graphql` or `user`. `ghp-live-comment`, `live-r2` and `ghp-consumer-scratch` were not touched. T34 objects untouched: after B2, #196 OPEN, PR #204 OPEN unmerged, remote branches `feature/gh196-issue ghp-spike/auto ghp-spike/link main` (same as before B2).
- **Project 3 switches at start (read from GitHub, 23:29Z):** `Auto-add sub-issues to project=true`, `Auto-close issue=false`, `Item added to project=true`, `Item closed=false`, `Pull request linked to issue=true`, `Pull request merged=true`. Re-read at the end: identical.
- Product: released 0.1.0 in `$LIVE/venv` (`ghp --version` -> `ghp 0.1.0`), reused from B0/B1 unchanged. No product code changed.
- Gate answers: every product write below was previewed (`--plan-out` or the default preview), and the approval was given by the controller agent, an AI agent, not an independent human (as in T34). Apply runs used `--expect-plan` wherever the case did.
- Harness: B0/B1 harness reused. One harness change: `fx.py add-sub <parent> <child>` (REST `POST issues/{n}/sub_issues`, body via stdin) added for L-036a. Helper `$LIVE/b2fn.sh` (`r` = run ghp with its own log and captured out/err). `gh pr merge` and git push/clone are not `gh api` calls, so the wrapper counts the merge as `other_gh=1` and git traffic is not in the log; both are listed by hand below.

`$LIVE = <scratch>/live`; logs `$LIVE/log/b2/` (product `L-*.jsonl`, fixtures `fx-*.jsonl`, probes `probe-*.jsonl`, `stage-*.jsonl`, `cleanup.jsonl`, `final.jsonl`); outputs and copies `$LIVE/work/b2out/`; clone `$LIVE/work/b2`.

## Objects (writes as praveenlavu to R1 and Project 3)

| Object | What |
|---|---|
| S3 = #207 `L-B2 20261112-232950 story: pickup hours (fictional)` | label `ghp:live-test`, AC1, AC2 (fixture) |
| #208..#211 | S3 tasks T001..T004, created by `ghp push` (L-028) |
| PR #212 | `Pickup hours #207`, head `feature/gh207-l-b2-20261112-232950-story-pickup-hours`, created by `ghp pr create` (L-031), merged by the agent (L-033), merge commit `d51ef30` on `main` |
| S4 = #213 `L-034 20261112-232950 story S4: desk name (fictional)` | fixture, added to Project 3 (fixture) |
| side branch `ghp-live/l034-20261112-232950` | pushed by the agent from `origin/main` (fixture) |
| PR #214 | `Desk name #213`, base the side branch, created by `ghp pr create` (L-034), merged into the side branch by the agent |
| S5 = #215 `L-036 ... story S5: board drag (fictional)` | fixture, added to Project 3, Status set to Done by fx ("drag") |
| E1 = #216 (`ghp:epic`), F1 = #217 (`ghp:feature`, sub-issue of E1), S6 = #218 (sub-issue of F1) | fixtures for L-036a; labels `ghp:epic` and `ghp:feature` were created by GitHub on first use |
| comments on #207 | 6839056801, 6839061693, 6839075929 (questions), 6839145833 (decisions), ledger comment (done); on #213 the ledger comment |

## Stage progression of S3 (`ghp status 207 --json` after each case)

| after | stage | next |
|---|---|---|
| L-025 | plan | `speckit.ghp.plan 207` |
| L-026 | plan | `speckit.ghp.plan 207` |
| L-027 (2 questions posted) | **questions-answered** (earlier than `plan`: B2-F5) | `speckit.ghp.plan 207` |
| L-027 (answered in the ledger) | **questions-posted** (B2-F5) | `ghp questions 207 --post --plan-out ...` |
| L-027 (3rd comment "All open questions are answered.") | board-tasks | `ghp push 207 --plan-out .ghp/plan-push.md` |
| L-028 | tasks-ticked | `speckit.ghp.implement 207` |
| L-029 | tasks-ticked | `speckit.ghp.implement 207` |
| L-030 (before the section-4 log) | gate | `speckit.ghp.gate 207` |
| L-030 (gate and review blocks logged, committed) | pr-link | `speckit.ghp.pr 207` |
| L-031 / L-032 | merged | `git fetch origin` |
| L-033 | done | `none` ("every stage is met") |
| L-035 (reopened) | open-at-done (expected) | `move #207 out of Done by hand in the Project, then run ghp activate 207` |
| L-035 (closed again) | done | `none` |

The gate and review blocks in ledger section 4 were written by the agent in the format of `commands/gate.md` / `commands/review.md` (the slash commands are B3's scope), with `ghp gate`'s real result, then committed (`docs: review log #207`).

## Cases

### L-024 setup writes the config with one read — PASS (B2-F1)
| Step | Command (in empty `work/b2-setup`) | Exit | Output (abbrev.) | reads / writes |
|---|---|---|---|---|
| 1 | `setup --owner praveenlavu --repo ghp-spike-scratch --project 3 --project-owner praveenlavu --status-todo Todo --status-active 'In Progress' --status-done Done --profile starter` | 0 | `Signed in as praveenlavu (user id 22502189)...`; file holds owner, repo, project_owner, project_number, 3 status names, `mode: advisory`: names and numbers only | 1 (`query { viewer { databaseId login __typename } }`) / 0 |
| 2 | same, no `--force` | 2 | `error: ghp.config.json already exists here and is left as it is; run \`ghp setup --force\` ...`; file byte-identical (`cmp`) | 0 / 0 |
| 3 | same + `--profile starter --approver 22502189` (literal, no `--force`) | 2 | the E_EXISTS text (the existing file is refused first) | 0 / 0 |
| 3b | same + `--force` | 2 | `error: --approver is for the team and strict profiles; the starter profile has no approvers ...`; file unchanged | 1 / 0 |
| 4 | `--profile team --approver 22502189 --force` | 2 | `error: an approver cannot be the account you are signed in as: the agent's own comments would approve themselves ...` (E_APPROVER_SELF); file unchanged | 1 / 0 |
- The texts match `setup_cmd.py` constants `E_EXISTS`, `E_APPROVER_PROFILE`, `E_APPROVER_SELF`, but the code names are not printed (B2-F1).

### L-025 story to ledger — PASS
- fx: S3 = #207 (1 write `POST issues`).
- `ghp dod 207 --spec-dir specs/l-b2` -> exit 0, `{"path":"dod/DoD_207.md","action":"created","ac_chars":82,"ac_thin":true}`, 1 read / 0 writes.
- `ghp status 207` -> exit 0, `Issue #207: waiting on stage plan ... Board: this issue is not on the Project`. 7 reads / 0 writes. `next` is past `ledger`.

### L-026 activate a story that is not on the board — PASS
- Preview `activate 207 --plan-out .ghp/plan-activate.md` -> exit 0: `Status: (not on the Project) -> In Progress`, `Step 1: add #207 to the Project`, `Step 2: set Status to In Progress, then read it back`. 3 reads / 0 writes.
- Apply `--apply --expect-plan` -> exit 0, `moved #207 to In Progress`. 5 reads / 2 writes: `addProjectV2ItemById` (23:31:25), `updateProjectV2ItemFieldValue` (23:31:28), then a Status read-back (23:31:28).
- `fx status-history 207`: `github-project-automation '' -> Todo` and `praveenlavu Todo -> In Progress`, both at 23:31:29Z. So the "Item added to project" automation DID set Todo first (T20 finding 4: recorded). (A read right after the apply returned `[]`; the events appeared a few seconds later.)
- Rerun `activate 207 --apply` -> exit 0, `nothing sent: already In Progress`, 4 reads / 0 writes.

### L-027 questions posted; a changed set is a NEW comment — PASS (B2-F5)
- Ledger section 2: Q1, Q2 open. Preview -> `Action: ADD a new comment`, marker `[spec-kit-ghp:questions] 888b7c45778c460d`. Apply -> `posted comment 6839056801`, 1 write `POST issues/207/comments`.
- Q2 text changed. Preview -> `ADD a new comment`, marker `d96deec8a446703d`. Apply -> `posted comment 6839061693`, 1 write `POST`.
- Read-back: comment 6839056801 `created_at = updated_at = 23:31:59Z` (unchanged); 0 PATCH in all L-027 logs. Both comments start with the questions marker.
- Extra step (not in the plan): after the answers were recorded in the ledger, `status` asked for `ghp questions 207 --post`; its preview was `ADD a new comment` "All open questions are answered." (marker `f5f84b7321dffdd3`); approved and applied (`posted comment 6839075929`, 1 write).

### L-028 push four tasks with auto-add ON — PASS (race seen on 1 of 4)
- tasks.md T001..T004 (T002 `[P]`). Preview -> `create 4 · update 0 · unchanged 0 · orphan 0`, each `new sub-issue of #207`, `add to the Project, Todo`. 7 reads / 0 writes.
- Apply `--apply --expect-plan .ghp/plan-push.md` -> exit 0, `created 4, updated 0, failed 0`, T001 #208, T002 #209, T003 #210, T004 #211. 16 reads / 12 writes: per task `POST issues`, `addProjectV2ItemById`, `updateProjectV2ItemFieldValue` (+ read-back). No stop.
- Read-back: `fx sub-issues 207` -> #208..#211 open; each has label `ghp:task`, parent #207, Project 3 Status `Todo`.
- Race: for T004 (#211) only, ghp's `addProjectV2ItemById` (23:33:40) was followed by a re-read of the issue's `projectItems` (23:33:41) before the Status write. By `projects.py` (lines 239-251) that re-read happens only after GitHub's `Content already exists in this project` reply or an unknown outcome, so T004 hit the auto-add race and the T34b handling carried on. T001..T003 had no re-read (no race). The wrapper does not log response bodies, so the reply text itself is inferred from the code path, not seen. `fx status-history 211`: one event, `github-project-automation '' -> Todo` at 23:33:43Z.
- Write gaps: writes at 23:33:20, 22, 24, 26, 28, 30, 32, 34, 36, 38, 40, 42: minimum 2 s at the log's 1 s resolution (>= 1.0 s).

### L-029 rename and orphan — PASS
- T002's title changed, T004 line deleted. Preview `push 207 --plan-out q.md` -> `create 0 · update 1 · unchanged 2 · orphan 1` (`update T002 ... -> ... for AC1, AC2 and Sunday | #209`, `orphan T004 | #211`).
- Apply `--expect-plan q.md` -> exit 0, `created 0, updated 1, failed 0`; exactly 1 write `PATCH issues/209`; nothing for #211, which stays open.
- Rerun `push 207 --apply` -> `create 0 · update 0 · unchanged 3 · orphan 1`, 0 writes (12 reads). First live run of the update apply.

### L-030 branch, gate with live AC drift, decisions — steps 1-5 PASS, step 6 FAIL against the plan (B2-F2, B2-F3)
1. `branch 207` -> `{"branch": "feature/gh207-l-b2-20261112-232950-story-pickup-hours", "action": "create", "applied": false}`; `--apply` -> created and checked out. Slug from the ledger title. 0 api calls.
2. `branch --id-from feature/gh207-x` -> `207`, exit 0, 0 api calls.
3. `l_b2/pickup.py` + `l_b2/test_pickup.py` (3 tests, OK), tasks and DoD ticked, ledger section 3 rows; commits `f82f358`, `284a74b` (each names #207).
4. `gate 207 --src . --base origin/main` -> exit 0, `drift {evaluated true, drift false}`, `blocking false`, 1 read. With the test ids written in backticks the traceability said `2 AC without a known test` (B2-F2); without backticks `all AC mapped`.
5. `fx patch-body 207` (AC2 -> `closed today`), config `gate.block:["ac_drift"]`: gate -> **exit 1**, `drift true`, `blocked_by ["ac_drift"]`, `blocking true`, 1 read / 0 writes. `--offline` -> exit 0, `drift evaluated false, "offline — drift not checked"`, 0 api calls (drift only online). Body restored (`fx patch-body`), gate -> exit 0, `drift false`.
6. `decisions 207 --add 'Sunday is closed at the fictional desk' --kind decision --why 'PO answer to Q1'` -> `D1`, 0 api calls. Preview `--post` -> `ADD a new comment (a decisions comment is never edited)`. Apply -> `posted comment 6839145833`, 1 write `POST`. **`decisions 207 --resolve D1 --validated --by PO` -> exit 2, `error: --resolve records the PO's verdict, which only a human may do: run it in your own terminal (this shell has no terminal on stdin; ...)`, 0 api calls; file unchanged.** Not circumvented (no pseudo-terminal was faked). D1 stays open (B2-F3).

### L-031 PR opened with `--push`; a second create — PASS (B1-F1 hit again)
- The plan's literal form (`--title ... --body-file ... --base main --push`) is refused by 0.1.0 (B1-F1); the documented form `--from-prepared --push` was used (`.ghp/pr-args.json` `{"title":"Pickup hours","base":"main","head":"feature/gh207-..."}`, `.ghp/pr-body.md`).
- Preview -> exit 0: title `Pickup hours #207`, `tasks all 3 ticked`, `push yes: git push -u origin feature/gh207-..., before the PR is created`, `existing PR none`, body ends `Closes #207`. 3 reads / 0 writes; `ls-remote` 0 lines.
- Apply `--expect-plan .ghp/plan-pr.md` -> exit 0, `created PR #212`. Log: 1 write `POST pulls`; ghp's own `git push -u origin feature/gh207-...` ran with the clone's praveenlavu helper (2 extra `auth_token` lines). PR author `praveenlavu`, head sha `bfaf96a`, title ends `#207`, body ends `Closes #207` (once; B1-F2 not hit since the body file had no keyword).
- `Pull request linked to issue` kept S3 at In Progress (no new Status event).
- Second dry run -> exit 0, `existing PR | #212 is already open for this head and base; nothing will be created`, 0 writes. (The `push yes` row still shows; an apply of it was not tried: note only, UNVERIFIED whether it would push.)

### L-032 status before merge — PASS (B2-F6)
- `status 207` -> `Issue #207: waiting on stage merged. Next: git fetch origin` / `Why: the default branch does not hold this issue's latest review or gate result yet (merge the PR, then fetch)` / `Board column: In Progress`. Reads as waiting, not as merged. 21 reads / 0 writes.

### L-033 merge, then `done` sets Done ITSELF — PASS (first live run of this path)
1. Agent merge (write as praveenlavu): `gh pr merge 212 -R praveenlavu/ghp-spike-scratch --merge` -> exit 0; `mergedAt 23:40:39Z`, merge commit `d51ef300b1b7`.
2. ClosedEvent on #207: `23:40:41Z`, actor `praveenlavu`, `stateReason COMPLETED`, `closer {__typename PullRequest, number 212}`. S3's item Status at 23:40:4x and again at 23:41:14 (35 s after the merge): `In Progress` (Item closed is off). The PR has no Project item.
3. `git fetch`; preview `done 207 --plan-out .ghp/plan-done.md` -> exit 0: `Merge: PR #212 into main, commit d51ef300b1b7`, `Step 2: skip: already closed (completed) by PR #212; recorded, not closed again`, `Step 3: set Status to Done if it is still not there`, `Status: In Progress -> Done`, `warning: 4 of 4 sub-issues are still open; done does not close them`. 8 reads / 0 writes.
4. Apply `--apply --expect-plan .ghp/plan-done.md` -> exit 0, `posted the ledger comment` / `Status is Done (set by ghp)`, 12.6 s wall time. 14 reads / 2 writes: `POST issues/207/comments` (23:41:23), `updateProjectV2ItemFieldValue` (23:41:26), read-back (23:41:27). No PATCH of the issue, no E_STATUS_READBACK_MISMATCH, no 30 s wait (the issue was already closed).
5. `fx status-history 207`: `23:31:29Z github-project-automation '' -> Todo`, `23:31:29Z praveenlavu Todo -> In Progress`, **`23:41:27Z praveenlavu 'In Progress' -> Done`**. No automation event after the merge: **Done was set by ghp (as praveenlavu), 48 s after the merge, 46 s after the ClosedEvent.**
6. Rerun `done 207 --apply` -> exit 0, `nothing sent: already done`, 9 reads / 0 writes.

### L-034 `done` closes an open story, waits, then writes Status — PASS except the timing tolerance (B2-F4)
1. Fixtures (writes as praveenlavu): S4 = #213, `fx add-item 213` (automation set Todo at 23:42:39Z), side branch `ghp-live/l034-20261112-232950` pushed from `origin/main`. `dod 213 --spec-dir specs/l-034` (1 read), `branch 213 --apply` -> `feature/gh213-l-034-20261112-232950-story-s4-desk-name`, code + test + ticked ledger, commit `d668291`.
   `pr create 213 --from-prepared --push` (base = side) preview: `⚠ base 'ghp-live/l034-20261112-232950' is not the default branch 'main': Closes keywords are ignored; \`ghp done\` will close it`; applied after approval -> `created PR #214`, 1 write `POST pulls` + ghp's push. Agent merge `gh pr merge 214 --merge` (into the side branch) -> exit 0; #213 still `open`.
2. Preview `done 213 --pr 214 --plan-out d.md` -> exit 0: `Merge: PR #214 into ghp-live/l034-..., commit 55e04ad1a377 (given with --pr; GitHub did not link it)`, `Step 2: close #213 as completed`, `Step 3: wait up to 30 s (reads only) for the Project's own workflow to move the item to Done, then set Status to Done if it is still not there`, `Status: Todo -> Done`. 9 reads / 0 writes.
   Apply `--apply --expect-plan d.md` -> exit 0, `closed #213 as completed` / `Status is Done (set by ghp)`, 47.1 s wall time. 27 reads / 3 writes:
   `POST issues/213/comments` 23:43:40 -> `PATCH issues/213` 23:43:42 -> 11 Status reads 23:43:43, 47, 50, 54, 57, 44:01, 04, 08, 11, 14, 18 (read-only) -> `updateProjectV2ItemFieldValue` 23:44:19 -> read-back 23:44:20.
   **Measured: polling window 35 s; PATCH to Status write 37 s** (expected 30 s ± 3 s: B2-F4). ClosedEvent 23:43:42Z by `praveenlavu`. `fx status-history 213`: `23:42:39Z github-project-automation '' -> Todo`, **`23:44:22Z praveenlavu 'Todo' -> Done`**: Done set by ghp, no automation event during the wait.
   Rerun `done 213 --pr 214 --apply` -> `nothing sent: already done`, 0 writes.
- S4's `status` afterwards is `board-tasks` (its tasks were never pushed in this case): note only.

### L-035 reopened story at Done (T26b live) — PASS
- `fx reopen 207`; S3 item read: `OPEN`, Status `Done` (reopen did not move the card).
- `status 207` / `--json` -> exit 0, stage `open-at-done`, next `move #207 out of Done by hand in the Project, then run ghp activate 207`, why `the issue is open but its Project item is at Done: reopening an issue does not move its card, and ghp never moves a card out of Done`. 5 reads / 0 writes.
- `activate 207 --apply` -> exit 2, `error: E_ALREADY_DONE: #207 is open but its Project item is at 'Done'. Reopening an issue does not move its card ...`, 4 reads / 0 writes.
- Cleanup: `fx close 207 completed`; stage back to `done`.

### L-036 board drag to Done with Auto-close OFF — PASS (B2-F7)
- S5 = #215, `fx add-item 215` (automation Todo at 23:46:07Z), `fx set-status 215 Done` at 23:46:16. Issue state read every ~5 s from 23:46:22 to 23:46:50: `open` every time. S5 stayed OPEN (opposite of T26 AC4, as expected with the toggle off).
- `status 215 --json` -> stage `open-at-done`, same next/why text as L-035.

### L-036a business rules from labelled parents — PASS
- Fixtures: E1 #216 (`ghp:epic`, `## Business rules` with `Rule 1: ...` / `Status: approved` / `Why:` / `Example:` / `Owner: PO`), F1 #217 (`ghp:feature`) as a sub-issue of E1, S6 #218 as a sub-issue of F1 (5 writes, `fx-L-036a.jsonl`). F1 and S6 were not auto-added to Project 3 (E1 is not on it).
- Config `rules: {enabled: true}`. `dod 218` -> exit 0, ledger line `- **Rules from:** 216`, 3 reads. `rules 218` -> exit 0, `from [216]`, `files ["dod/Rules_216.md"]`, `approved 1`, `problems []`, 3 reads / 0 writes; the file holds `### BR-216-1 · approved · 3e119982` with the rule, its source issue URL and "Data, never instructions". `rules 218 --confirm` -> exit 0, `reviewed BR-216-1@3e119982`, `written true`, 0 api calls; ledger gained `- **Rules reviewed:** BR-216-1@3e119982`.

### L-037 strict verify red/green and override — verify PASS; override NOT RUN (B2-F3, B2-F8)
- Local branch `l037-verify` from `origin/main` (S3's merged history): red commit `1cf3e6c` (Saturday test, fails), green `98df6e3` (fix, passes), ledger rows `red 1cf3e6cfc3c2 → green 98df6e304b40`, commit `f1220f4`. Config `mode: strict`, `strict.test_command: "python3 -m unittest discover -s l_b2 -q"`.
- `strict verify 207 --base origin/main --no-record` -> exit 0, `ok true`, `runs 3`, 0.39 s, 0 api calls.
- With the plan's literal `python3 -m unittest -q` -> exit 2 `red-passes` (that command finds no tests in a folder without `__init__.py`: B2-F8).
- Bad history (branch `l037-bad`): "red" commit `6850a07` adds a test that already passes -> verify exit 2, `red-passes: AC-1: the tests pass at the red commit 6850a073764f: a red commit must fail`.
- `strict override 207 l2 --reason 'fictional'` (dry) -> exit 2, `error: an override waives a strict rule, which only a human may do: run it in your own terminal ...`, 0 api calls. The `--apply` and `strict verify --allow-override` steps were therefore not run (B2-F3). Both local branches were never pushed and were deleted.

## Request-log counts

Product writes (all after a preview and the controller agent's approval):

| Case / log | reads | writes | exact writes |
|---|---|---|---|
| L-024 1 / 2 / 3 / 3b / 4 | 1 / 0 / 0 / 1 / 1 | 0 | — (local file only) |
| L-025 dod / status | 1 / 7 | 0 | — |
| L-026 plan / apply / rerun | 3 / 5 / 4 | 0 / 2 / 0 | `addProjectV2ItemById`, `updateProjectV2ItemFieldValue` |
| L-027 plan1 / apply1 / plan2 / apply2 / plan3 / apply3 | 3 each | 0 / 1 / 0 / 1 / 0 / 1 | `POST issues/207/comments` x3 |
| L-028 plan / apply | 7 / 16 | 0 / 12 | `POST issues` x4, `addProjectV2ItemById` x4, `updateProjectV2ItemFieldValue` x4 |
| L-029 plan / apply / rerun | 12 / 12 / 12 | 0 / 1 / 0 | `PATCH issues/209` |
| L-030 branch, id-from, gates x6, dec-add, dec-plan, dec-apply, resolve | 0,0,1,1,1,1,0,1 / 0 / 3 / 3 / 0 | 0 except dec-apply 1 | `POST issues/207/comments` |
| L-031 plan / apply / rerun | 3 / 3 / 3 | 0 / 1 / 0 | `POST pulls` (+ ghp's `git push -u origin feature/gh207-...`) |
| L-032 text / json | 21 / 21 | 0 | — |
| L-033 plan / apply / rerun | 8 / 14 / 9 | 0 / 2 / 0 | `POST issues/207/comments`, `updateProjectV2ItemFieldValue` |
| L-034 dod / branch / pr-plan / pr-apply / plan / apply / rerun | 1 / 0 / 3 / 3 / 9 / 27 / 10 | 0,0,0,1,0,3,0 | `POST pulls` (+ ghp's `git push`); `POST issues/213/comments`, `PATCH issues/213`, `updateProjectV2ItemFieldValue` |
| L-035 status / json / activate | 5 / 5 / 4 | 0 | — |
| L-036 json | (in log) | 0 | — |
| L-036a dod / rules / confirm | 3 / 3 / 0 | 0 | — |
| L-037 all | 0 | 0 | — |
| `stage-*` status runs (17) | 7..21 each | 0 | — |
| **Total product** | | **26** API writes + 2 git pushes by ghp | |

Agent (not product) writes as praveenlavu: `gh pr merge 212` and `gh pr merge 214` (`L-033-merge.jsonl`, `L-034-merge.jsonl`: `other_gh=1` each).

| Fixture log (fx.py / git, not product) | writes |
|---|---|
| `fx-L-025` | 1 (`POST issues` #207) |
| `fx-L-030-drift` / `fx-L-030-restore` | 1 / 1 (`PATCH issues/207` body) |
| `fx-L-034` | 2 (`POST issues` #213, `addProjectV2ItemById`) + git push of the side branch |
| `fx-L-035` | 2 (`PATCH issues/207` reopen, close completed) |
| `fx-L-036` | 3 (`POST issues` #215, `addProjectV2ItemById`, `updateProjectV2ItemFieldValue` Done); 1 earlier `fx add-item` without a number failed before any write (harness typo) |
| `fx-L-036a` | 5 (`POST issues` x3, `POST issues/216/sub_issues`, `POST issues/217/sub_issues`) |
| `cleanup` | 8 (`PATCH issues/{208,209,210,211,215,216,217,218}` close not_planned) + 3 git branch deletes |

## Created objects and cleanup

| Object | Cleanup / end state (read back) |
|---|---|
| #207 S3 | closed `completed` (by PR #212; reopened and re-closed `completed` in L-035); Project item `Done` (set by ghp) |
| #208..#211 S3 tasks | closed `not_planned`; Project items left at Todo |
| #213 S4 | closed `completed` by `ghp done`; Project item `Done` (set by ghp) |
| #215 S5 | closed `not_planned`; Project item left at `Done` (policy: never delete items) |
| #216 E1, #217 F1, #218 S6 | closed `not_planned` (not on the Project) |
| PR #212, PR #214 | merged by the case (state `closed`, `merged=true`) |
| remote branches `feature/gh207-l-b2-...`, `feature/gh213-l-034-...`, `ghp-live/l034-20261112-232950` | deleted; `ls-remote` now lists only `feature/gh196-issue ghp-spike/auto ghp-spike/link main` |
| local branches `l037-verify`, `l037-bad` and the feature/side branches | deleted (`l037-*` never pushed) |
| `main` of R1 | now at merge commit `d51ef30` (PR #212: `l_b2/`, `specs/l-b2/`, `dod/DoD_207.md`, `dod/Decisions_207.md`); left as the case's merged result |
| labels `ghp:epic`, `ghp:feature` | created by GitHub on first use; left (policy: never delete labels) |
| comments | left as evidence |
| ledgers, plans, `.new`-free plan files, outputs | copied to `$LIVE/work/b2out/` |

## Findings

Severity: 1 = unsafe; 2 = documented flow blocked or wrong, wrong exit/error code against the docs; 3 = minor (message, ordering, or a plan expectation that does not match the product's stated design).
No severity-1 finding: every product write was previewed and approved first and matched its plan exactly; no write was retried; only `praveenlavu` was used; no token appears anywhere.
Known findings hit again: B1-F1 (plan's literal `pr create` form, L-031/L-034: used `--from-prepared --push`). B1-F2 not hit (the body file had no closing keyword).

- **B2-F1 (sev 3) `ghp setup` refusals print no `E_*` code.** E_EXISTS, E_APPROVER_PROFILE and E_APPROVER_SELF exist only as constant names in `setup_cmd.py`; the printed line is `error: <text>`, while other verbs print `error: E_ALREADY_DONE: ...`. Exit codes (2) and file safety are right. Also, the plan's step 3 without `--force` reaches E_EXISTS first (plan wording).
- **B2-F2 (sev 3) `ghp gate` does not recognise test ids written in backticks in ledger section 3.** `` `PickupTest.test_monday` `` -> `2 AC without a known test`; the same ids without backticks -> `all AC mapped`. The section-3 hint itself shows ids in backticks, and `parse_evidence` strips backticks in the status cell. In strict mode (where `ac_traceability` always blocks) this would block a correct ledger.
- **B2-F3 (sev 3, plan) `decisions --resolve` and `strict override` (even its dry print) refuse without a terminal on stdin** ("only a human may do: run it in your own terminal ... a guard against accidents, not a security boundary"), exit 2, 0 requests. That is the product's stated design; the plan expected the agent to run them. Not circumvented. Not run: L-030 step 6 resolve (D1 stays open in `dod/Decisions_207.md` on `main`), L-037 `override --apply` and `strict verify --allow-override`. The owner can run these in their own terminal.
- **B2-F4 (sev 3) `done`'s wait is 30 s of sleep plus read time.** `WAIT=30`, `POLL=3`: 11 reads and 10 sleeps; live the polling window was 35 s and PATCH -> Status write 37 s (plan: 30 s ± 3 s). The plan text says "wait up to 30 s". Harmless (reads only); wording or the loop could be deadline-based.
- **B2-F5 (sev 3, plan) the status stage moves backwards when questions are added, and an extra comment is needed after answering.** Posting Q1/Q2 moved S3 from `plan` back to `questions-answered`; answering them in the ledger moved it to `questions-posted`, whose next step is a third questions comment ("All open questions are answered."). Status reports the current state, so this is by design; the plan's "the stage must never move backwards" does not hold for a story that gains questions mid-way, and L-027 should list the third comment.
- **B2-F6 (sev 3) before the merge `status` gives `Next: git fetch origin`** while the PR is still open; the Why line says "merge the PR, then fetch". The headline reads as waiting (L-032 passes), but the Next command is premature.
- **B2-F7 (sev 3) the open-at-done text assumes a reopen.** For S5, never closed and dragged to Done, status says "reopening an issue does not move its card". Accurate for L-035, misleading for a drag.
- **B2-F8 (sev 3, plan) L-037's `strict.test_command: "python3 -m unittest -q"` finds no tests** in `l_b2/` (no `__init__.py`; Python 3.9 exits 0 with 0 tests), so verify reports `red-passes`. Used `python3 -m unittest discover -s l_b2 -q`. The plan should name a command that finds the tests.
- Notes (no finding): `ghp status` for S4 after `done` is `board-tasks` (its tasks were never pushed, by case design). The second `pr create` dry run still shows `push yes` next to "nothing will be created"; whether an apply would push was not tried (UNVERIFIED).
