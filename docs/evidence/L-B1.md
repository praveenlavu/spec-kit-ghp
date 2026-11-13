# L-B1 evidence: refusals before writes, released ghp 0.1.0 against R1

Run on 2026-11-12 (UTC 23:21 to 23:30; batch stamp `20261112-232156` in `$LIVE/stamp-b1`) by the B1 live-test agent. All data is fictional. Plan: `docs/plans/PLAN-LIVE-0.1.md` batch B1 (L-015..L-023).

- Only the owner's own account was used.
- Account: `praveenlavu` (`GET /user` -> `praveenlavu 22502189`). Every `gh`/`ghp`/`git` network command ran with the per-command prefix `GH_TOKEN=$(gh auth token -u praveenlavu) ` (git through the per-command credential helper; `git credential fill` showed `username=praveenlavu` only). `gh auth switch/refresh/login` were not run. No token in any output, log or this file (L-023 scan).
- Target: R1 = `praveenlavu/ghp-spike-scratch` (`main`) + user Project 3. Terminal approval mode. `ghp-live-comment`, `live-r2` and `ghp-consumer-scratch` were not touched. T34 objects (#195..#203, PR #204, branch `feature/gh196-issue`, its run) were not written; after B1, PR #204 OPEN, #196 OPEN, remote branches still `feature/gh196-issue ghp-spike/auto ghp-spike/link main`.
- Product: released 0.1.0 in `$LIVE/venv` (`ghp --version` -> `ghp 0.1.0`), reused from B0 unchanged. No product code changed.
- Harness: B0 harness reused, not rebuilt. **fx.py write subcommands proven here for the first time** (`issue`, `add-item`, `set-status`, `close`, `reopen`): each worked and each write appears in the request log (`fixtures.jsonl`, `L-017-*-fx.jsonl`, `L-019a-fx.jsonl`, `L-020f-fx.jsonl`, `cleanup.jsonl`). Fixture logs are separate files from product logs.
- **Product writes in B1: 0** (every product log `writes=0`; table at the end). All B1 writes were fixture setup/cleanup by fx.py.

`$LIVE = <scratch>/live`; logs `$LIVE/log/b1/`, outputs `$LIVE/work/b1out/`, clone `$LIVE/work/b1`.

## Fixtures (writes as praveenlavu to R1, via fx.py)

| Object | What | Log |
|---|---|---|
| S1 = #205 `L-B1 20261112-232156 story (fictional)` | label `ghp:live-test`, `## Acceptance criteria` AC1, AC2; added to Project 3 (item `PVTI_lAHOAVdbLc4BqvdwzhITUiM`, set to Todo by the Project's own "Item added" workflow) | `fixtures.jsonl`: POST issues x2, `addProjectV2ItemById` x1 |
| S2 = #206 `L-B1 20261112-232156 other story (fictional)` | same label, not on the board (read back: 0 project items) | `fixtures.jsonl` |
| Local | `ghp dod 205 --spec-dir specs/l-b1` (exit 0, 1 read), `specs/l-b1/tasks.md` T001, T002 `[P]`, T003 | `setup-dod.jsonl` |

## Cases

### L-015 `--expect-plan` without `--apply` — PASS
- `G ghp push 205 --expect-plan .ghp/p.md` -> exit 2, `ghp: error: --expect-plan needs --apply` (argparse). 0 api calls.

### L-016 every write verb's dry run sends nothing — PASS (two named refusals recorded; finding B1-F1, B1-F2)
| Step | Command | Exit | Output (abbrev.) | reads / writes |
|---|---|---|---|---|
| 1 | `questions 205 --post --plan-out .ghp/q.md` (Q1 added to ledger section 2) | 0 | `Action: ADD a new comment` + marked text `[spec-kit-ghp:questions] 8fc80a4c...` | 3 / 0 |
| 2 | `decisions 205 --add 'fictional decision' --kind decision --why fixture` then `decisions 205 --post --plan-out .ghp/d.md` | 0 / 0 | `D1` added locally; plan `ADD a new comment (a decisions comment is never edited)` | 0 / 0; 3 / 0 |
| 3 | `push 205 --from specs/l-b1/tasks.md --plan-out .ghp/p.md` | 0 | `create 3 · update 0 · unchanged 0 · orphan 0`, each `new sub-issue of #205`, `add to the Project, Todo`, digest `d59031cd...` | 7 / 0 |
| 4 | `activate 205 --plan-out .ghp/a.md` | 0 | `Status: Todo -> In Progress`, digest `f680dc55...` | 4 / 0 |
| 5 | `branch 205 --apply` | 0 | `feature/gh205-l-b1-20261112-232156-story-fictional`, created (local only) | 0 / 0 |
| 5 | plan's literal `pr create 205 --title 'L-B1' --body-file b.md --base main --push` | 2 | `error: --push needs --from-prepared` (named refusal; B1-F1) | 0 / 0 |
| 5 | `pr create 205 --from-prepared --push --plan-out .ghp/pr.md` (`.ghp/pr-args.json` title/base/head, `.ghp/pr-body.md` `Closes #205`) | 2 | `refusing to open a PR: 3 unticked task(s) in tasks.md: T001, T002, T003 ... --allow-incomplete` | 1 / 0 |
| 5 | same + `--allow-incomplete` | 0 | plan table: `draft yes`, `push yes: git push -u origin feature/gh205-..., before the PR is created`, `existing PR none`; body ends with a second `Closes #205` (B1-F2) | 3 / 0 |
| 5 | `git ls-remote --heads origin feature/gh205-...` | — | 0 lines: nothing pushed | — |
| 6 | `done 205 --plan-out .ghp/done.md` | 0 | `DoD checklist: NOT READY, 5 unticked: nothing will be sent, even with --apply` | 0 / 0 |

### L-017 stale plan refused, fresh plan to `.new` — PASS (done part skipped as the plan allows)
| Verb | Change after the preview | Apply | Exit | Output | `.new` | product writes |
|---|---|---|---|---|---|---|
| push | added T004 to tasks.md | `--apply --expect-plan .ghp/p.md` | 2 | `plan changed since approval — re-run the preview and approve again (the current plan is in .ghp/p.md.new)` | yes | 0 (7 reads) |
| activate | fixture `fx set-status 205 "In Progress"` (1 write, `L-017-act-fx.jsonl`) | `--apply --expect-plan .ghp/a.md` | 2 | same text, `.ghp/a.md.new` | yes | 0 (4 reads) |
| questions | Q1 text edited in the ledger | `--post --apply --expect-plan .ghp/q.md` | 2 | same text, `.ghp/q.md.new` | yes | 0 (3 reads) |
| done | ledger DoD ticked, then plan-out | — | 2 | `E_NOT_MERGED: no merged pull request ... tied to #205 ... --pr N (nothing was sent)` at the plan stage: done refuses before rendering, so the stale-plan step was skipped (as the plan says) | — | 0 (2 reads) |
- Cleanup: `fx set-status 205 Todo` (1 write, `L-017-cleanup-fx.jsonl`); title was never changed; ledger restored.

### L-018 tampered plan file — PASS
- `push 205 --plan-out .ghp/p18.md` (exit 0), then one character changed in a table cell (`shout` -> `shoUt` in T003's title), then `push 205 --from ... --apply --expect-plan .ghp/p18.md` -> exit 2 `plan changed since approval ... (.ghp/p18.md.new)`. 7 reads, 0 writes.

### L-019 activate refusals — PASS
- (a) fixture `fx close 206 not_planned` -> `G ghp activate 206 --apply` -> exit 2 `E_ISSUE_CLOSED: #206 is closed; activate does not move a closed issue`, 2 reads, 0 writes. Then `fx reopen 206`.
- (b) config without `project_number` -> `activate 205 --apply` -> exit 2 `E_NO_PROJECT: activate needs a Project: set project_number in ghp.config.json`, 0 api calls.
- (c) `status_active: "Done"` -> `activate 205 --apply` -> exit 2 `E_ACTIVE_IS_DONE: status_active 'Done' is the done option; activate never moves an item to Done ...`, 0 api calls.
- Read-back: #205 Status `Todo` (unchanged), #206 OPEN with 0 project items. Config restored.

### L-020 done refusals that need no merge — PASS (plan wording note B1-F3)
| Sub | Command | Exit | Output (abbrev.) | reads / writes |
|---|---|---|---|---|
| a | `done 206` (no ledger) | 2 | `E_NO_LEDGER: cannot read the ledger dod/DoD_206.md ...; run \`ghp dod 206\` first` | 0 / 0 |
| b | `ghp dod 206`; `done 206` (unticked) | 0 | preview: `DoD checklist: NOT READY, 5 unticked: nothing will be sent, even with --apply` (no E_ code in the preview, B1-F3) | 0 / 0 |
| b | `done 206 --apply` (unticked) | 2 | `E_DOD_NOT_TICKED: 5 DoD box(es) are not ticked ...; nothing was sent` | 0 / 0 |
| c | all boxes ticked, S2 open, no PR: `done 206` | 2 | `E_NOT_MERGED: ... name it with \`--pr N\` (nothing was sent)` | 2 / 0 |
| d | `done 206 --pr 99999` | **2** | `E_PR_NOT_FOUND: there is no pull request #99999 in praveenlavu/ghp-spike-scratch; nothing was sent` (T26g fix confirmed live) | 3 / 0 |
| e | `done 206 --pr 1 --pr 2` | 2 | usage + `ghp done: error: --pr was given twice; name one pull request` | 0 / 0 |
| f | fixture `fx close 206 not_planned`; `done 206` and `done 206 --apply` | 2 / 2 | `E_CLOSED_NOT_COMPLETED: #206 is closed as not_planned, not as completed ...; nothing was sent` | 1 / 0 each |
- S2 left closed `not_planned` (state after f).

### L-021 pr create closing-keyword scanner (dry runs with `--from-prepared --push`) — PASS
Tasks ticked in the working tree for (a)..(c) so the scanner is reached.
| Sub | Setup | Exit | Output (abbrev.) | reads / writes |
|---|---|---|---|---|
| a | body `Closes #205\nCloses #206` | 2 | `E_BODY_CLOSES_OTHERS: the title or body would also close praveenlavu/ghp-spike-scratch#206 on merge ...; nothing was sent` | 0 / 0 |
| b | local commit `L-B1 note (fictional)\n\nFixes #206`, body closes only #205 | 2 | `E_COMMITS_CLOSE_OTHERS: a commit message between base and head would also close ...#206 ...; nothing was sent` | 0 / 0 |
| c | same + `--allow-extra-closes` | 0 | plan rendered, `tasks all 3 ticked`, `⚠ commit messages would also close ...#206 on merge (accepted with --allow-extra-closes)` | 3 / 0 |
| d | commit removed (local reset), T002 unticked | 2 | `refusing to open a PR: 1 unticked task(s) in tasks.md: T002. Finish them, or pass --allow-incomplete ...` (no E_ code; note only) | 1 / 0 |
| d | + `--allow-incomplete` | 0 | `draft yes — 1 unticked task(s): T002` | 3 / 0 |
- `git ls-remote --heads origin feature/gh205-...` -> 0 lines after all runs: nothing was pushed.

### L-022 tasks.md refusals — FAIL against the plan's Expected (findings B1-F4, B1-F5); 0 writes, safe
| Sub | tasks.md | Exit | Output | reads / writes |
|---|---|---|---|---|
| dup | extra `- [ ] T002 Duplicate id` | 2 | `E_DUPLICATE_TASK_ID: tasks.md lists T002 more than once` | 0 / 0 |
| bad | extra `- [ ] TX1 Bad id` | **0** | plan `create 3`: the `TX1` line is not a task line (parser `^\s*- \[( |x|X)\] (T\d{3,})\b`), silently left out (B1-F4) | 7 / 0 |
| bad (diagnostic) | extra `- [ ] T1234567890 Ten-digit id` | 2 | `E_BAD_TASK_ID: task id T1234567890 is not T and 3 to 9 ASCII digits` | 0 / 0 |
| long | extra T004 with a ~1270-char title | **0** | plan `create 4`; T004 title cut to 256 with `…` and `(cut to 256)` shown (B1-F5) | 7 / 0 |
- E_DUPLICATE_TASK_ID and E_BAD_TASK_ID are reachable live; E_TITLE_TOO_LONG is not reachable from tasks.md by design (`push.py` cuts our titles to 256; MAX_CELL 1024 refuses only longer existing titles; `tests/test_push.py::test_title_of_exactly_256_is_not_cut_and_257_is_cut_and_marked`).

### L-023 errors are one scrubbed line — PASS
- `grep -E 'gh[pousr]_|github_pat_|Authorization'` over all B0 and B1 stderr/stdout captures (41 `.err` files) and every request log (`log/*.jsonl`, `log/b1/*.jsonl`): 0 files match.
- Every product stderr is a single `error: ...` line, except argparse usage errors (L-015 2 lines, L-020e 4 lines: usage text plus one error line).

## Request-log counts (product logs; fixture logs listed separately)

| Case | product api calls (reads) | product writes |
|---|---|---|
| L-015 | 0 | 0 |
| L-016 1 / 2add / 2 / 3 / 4 / 5branch / 5 / 5b / 5c / 6 | 3 / 0 / 3 / 7 / 4 / 0 / 0 / 1 / 3 / 0 | 0 |
| L-017 push / act / q / done-plan | 7 / 4 / 3 / 2 | 0 |
| L-018 plan / apply | 7 / 7 | 0 |
| L-019 a / b / c / readback | 2 / 0 / 0 / 1 | 0 |
| L-020 a / b / b-apply / b-dod / c / d / e / f / f-apply | 0 / 0 / 0 / 1 / 2 / 3 / 0 / 1 / 1 | 0 |
| L-021 a / b / c / d1 / d2 | 0 / 0 / 3 / 1 / 3 | 0 |
| L-022 dup / bad / bad2 / long | 0 / 7 / 0 / 7 | 0 |

| Fixture log (fx.py, not product) | writes |
|---|---|
| `fixtures.jsonl` | 3 (POST issues x2, `addProjectV2ItemById`) |
| `L-017-act-fx.jsonl` / `L-017-cleanup-fx.jsonl` | 1 / 1 (`updateProjectV2ItemFieldValue`) |
| `L-019a-fx.jsonl` | 2 (PATCH #206 close, reopen) |
| `L-020f-fx.jsonl` | 1 (PATCH #206 close not_planned) |
| `cleanup.jsonl` | 1 (PATCH #205 close not_planned) |
- Extra `auth_token` lines in L-016-5b/5c and L-021d2 come from the harness's own `git ls-remote` credential helper run with the same `GHP_REQ_LOG`, not from ghp.
- git clone and `ls-remote` traffic is not in the gh log (git talks HTTPS itself); both are read-only.

## Created objects and cleanup

| Object | Where | Cleanup |
|---|---|---|
| #205 S1 | R1 | closed `not_planned` (read back `CLOSED / NOT_PLANNED`) |
| #205 Project 3 item | Project 3 | left, Status `Todo` (policy: never delete items) |
| #206 S2 | R1 | closed `not_planned` (by L-020f; read back) |
| comments | — | none created (all questions/decisions were dry runs) |
| local branch `feature/gh205-l-b1-20261112-232156-story-fictional` (commit `3f7de1b`; `d771f63` reset away) | `$LIVE/work/b1` | deleted; never pushed (ls-remote 0 lines; remote branch list unchanged) |
| ledgers, tasks files, `.ghp/` plans and `.new` files | copied to `$LIVE/work/b1out/` | kept as evidence |
| `$LIVE/log/b1/*.jsonl` | scratchpad | kept as evidence |

## Findings

Severity: 1 = unsafe; 2 = documented flow blocked or wrong, wrong exit/error code against the docs; 3 = minor (message, ordering, or a plan expectation that does not match the product's stated design).
No severity-1 finding: 0 product writes in every case, no token in any output, only `praveenlavu` used.

- **B1-F1 (sev 3, plan) L-016 step 5's command is not valid for 0.1.0.** `--push` needs `--from-prepared`, and `--head` is required without it; the product refuses `error: --push needs --from-prepared`, exit 2, 0 requests. Ran the documented form (`--from-prepared --push`, as `commands/pr.md` uses) instead. Plan should be corrected.
- **B1-F2 (sev 3) `pr create` appends `Closes #205` even when the body already has it.** The rendered body carries the keyword twice. Harmless on GitHub; cosmetic.
- **B1-F3 (sev 3, plan) `done` preview with unticked boxes is exit 0 ("NOT READY ... nothing will be sent, even with --apply"); E_DOD_NOT_TICKED (exit 2) appears only with `--apply`.** Safe and clear; the plan expected the code on the preview.
- **B1-F4 (sev 3) a malformed task line such as `- [ ] TX1 ...` is silently not a task.** `push` plans `create 3` with no warning, so a task the author meant to push can vanish from the plan; only the counts show it. E_BAD_TASK_ID fires for ids that match `T\d{3,}` but break the 3..9 ASCII digit rule (live: `T1234567890`). Candidate: warn on `- [ ] T...` lines that are not parsed, or correct the plan.
- **B1-F5 (sev 3, plan) a tasks.md title over the cap is cut to 256 with `…` and marked `(cut to 256)`, not refused with E_TITLE_TOO_LONG.** That is the product's stated design (`push.py` `_title`, test above); E_TITLE_TOO_LONG guards only plan cells over 1024 characters. The plan's Expected should be corrected.
- Note (no finding): `pr create`'s unticked-task refusal has no `E_*` code in its text, unlike its siblings.
