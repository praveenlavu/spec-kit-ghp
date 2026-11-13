# L-B4 evidence: `ghp-lifecycle` end to end on Spec Kit 1.4.0, the floor 1.2.1 replay, released ghp 0.1.0 against R1

Run on 2026-11-13 (UTC 00:13 to 00:31; batch stamp `20261113-001312` in `$LIVE/stamp-b4`) by the B4 live-test agent. All data is fictional. Plan: `docs/plans/PLAN-LIVE-0.1.md` batch B4 (L-044, L-044b, L-055, L-056a).

- Only the owner's own account was used.
- Account: `praveenlavu` (`GET /user` -> `praveenlavu 22502189`). Every `gh`/`ghp`/fixture/`specify` command of the controller ran with the per-command prefix `GH_TOKEN=$(gh auth token -u praveenlavu) `. The workflow runs got `GH_TOKEN` in the process environment only, set by the runner `$LIVE/b4bin/wf.sh` (as T34's `run.sh` and B3's `b3run.sh`); never in argv, a file or a log. `gh auth switch/refresh/login` were not run. Scan of `log/b4`, `work/b4out`, `b4bin`, both runs' `state.json`/`log.jsonl` and the 13 agent transcripts: 0 token hits, 0 hits of the other account's name. git used each clone's own credential config (every github.com helper reset, then one helper printing `username=praveenlavu` and the `gh auth token -u praveenlavu` output); `git credential fill` showed `username=praveenlavu` only.
- Target: R1 = `praveenlavu/ghp-spike-scratch` (`main`, `origin/HEAD` -> `main`) + user Project 3, terminal approval mode, R1 alone. `ghp-live-comment`, `live-r2` and `ghp-consumer-scratch` were not touched. T34 objects untouched: before and after B4, open `ghp:live-test` issues `[196, 195]`, open PRs `[204]`, remote branches `feature/gh196-issue ghp-spike/auto ghp-spike/link main`.
- Product: released 0.1.0 (the B0 venv, `ghp 0.1.0`). Extension from the release file `https://github.com/praveenlavu/spec-kit-ghp/releases/download/v0.1.0/spec-kit-ghp-extension-0.1.0.zip` ("Untrusted Source ... Continue? [y/N]" answered `y` by the controller); workflow from `https://raw.githubusercontent.com/praveenlavu/spec-kit-ghp/v0.1.0/workflows/ghp-lifecycle/workflow.yml` (`ghp-lifecycle` v0.1.0). No product code changed.
- Spec Kit: `uvx --from git+https://github.com/github/spec-kit@v1.4.0 specify` (`$LIVE/work/b4`, `integration.json` version 1.4.0, commands as skills `speckit-ghp-*`) and `@v1.2.1` (`$LIVE/work/b4f`, `CLI Version 1.2.1`). Both: `specify init --here --force --non-interactive --integration claude --script sh`; `extension list` -> `Commands: 13 | Hooks: 2 | Status: Enabled`. On 1.2.1 the harness's first `extension add --from <url>` (no name) failed `Missing argument 'extension'`; `extension add ghp --from <url>` (the documented form, `docs/catalog-submission.md`) installed it. Harness slip, not a product finding.
- **Who answered.** Every gate verdict below (`specify workflow resume <run> --input verdict_<gate>=approve|reject`), the PO verdicts recorded with `ghp decisions --resolve`, the DoD section 5 ticks and the `done` approval are **controller answers**: given by the controller agent (an AI agent running this batch) after reading the shown file or artifact, not by an independent human and not by a product owner. No product owner exists in this test.

`$LIVE = <scratch>/live`; request logs `$LIVE/log/b4/<id>.jsonl` (one per `specify` invocation and per controller command); console outputs `$LIVE/work/b4out/<id>.out`; tools `$LIVE/b4bin/` (`setup.sh`, `wf.sh`, `st.py` state summary, `order.py` preview/gate/apply order, `wstep.py` write-to-step mapping, `ptyrun.py`).

## Agent set-up

- Spawned by Spec Kit through `SPECKIT_INTEGRATION_CLAUDE_EXTRA_ARGS` = `--model sonnet --permission-mode bypassPermissions --disallowedTools 'Bash(gh:*)' 'Bash(git push:*)' 'Bash(curl:*)' WebFetch WebSearch --setting-sources project,local --strict-mcp-config --mcp-config <empty {"mcpServers":{}}> --no-chrome` (the B3 isolation: real home, user setting source excluded, MCP empty). Spec Kit appends `--output-format json`. stdin `/dev/null`, so every gate pauses.
- Transcripts: 8 sessions in the 1.4.0 project (pull, plan, plan-with-answers, tasks, implement, gate, review, pr-prepare), 5 in the 1.2.1 project (L-055 pull; L-056a pull, plan, plan-with-answers, tasks). Every assistant message `claude-sonnet-5-5`. No agent ran `gh` or `git push` (0 such commands, 0 permission denials in the transcripts); every logged `gh` process came from `ghp`, git's helper or the controller. Model usage: 13 agent steps (cost not summed: Spec Kit consumes the agent's JSON output).

## Fixtures (writes as praveenlavu to R1 and Project 3, via fx.py; `fixtures.jsonl`: 4 writes)

| Object | What |
|---|---|
| S9 = #230 `L-044 20261113-001312 story S9: farewell line (fictional)` | label `ghp:live-test`, AC1/AC2; added to Project 3 (Todo by the Project's "Item added" automation 00:13:32Z) |
| S10 = #231 `L-055 20261113-001312 story S10: tour badge (fictional)` | label `ghp:live-test`, AC1/AC2; added to Project 3 |

## Cases

### L-044 full run on Spec Kit 1.4.0 to `completed` — PASS

Run **`e013c5cd`** (`specify workflow run ghp-lifecycle -i issue_number=230`, 00:14:17Z; `completed` at 00:22:32Z). Each resume was `specify workflow resume e013c5cd --input verdict_<gate>=approve`, given after reading the file named:

| Log | Gate answered (controller) | What was read first | Steps run until the next pause | Writes (request log) |
|---|---|---|---|---|
| r0 | (run) | | base-check, pull (agent), stack-check | 0 (5 reads) |
| r1 | review-spec | `spec.md`, ledger section 2 (Q1, Q2 open) | check-review-spec, questions-preview | 0 |
| r2 | approve-questions | `plan-questions.md`: `ADD a new comment`, Q1, Q2 | questions-apply, -applied, plan (agent: D1..D3 local), plan-questions-preview | 1 `POST issues/230/comments` |
| r3 | approve-plan-questions | `unchanged; nothing to post` | apply (no-op), applied | 0 |
| r4 | wait-po-answers (no PO reply: accepts assumptions, as T34) | | check-wait-po-answers, sync-po-answers, plan-with-answers (agent: Q1, Q2 closed `assumed:`), answers-questions-preview | 0 |
| r5 | approve-answers-questions | `ADD a new comment` with the two assumed answers | apply, applied | 1 `POST issues/230/comments` |
| r6 | review-plan | ledger section 2, `plan.md` | check-review-plan, tasks (agent: T001..T007), push-preview | 0 |
| r7 | approve-push | `plan-push.md`: `create 7 · update 0 · unchanged 0 · orphan 0`, digest `d3e6fcf8...` | push-apply (`created 7, updated 0, failed 0`: #232..#238), push-applied, strict-check-implement, implement (agent: 6 local commits, 4 tests green), decisions-preview | 21 = 7 x (`POST issues`, `addProjectV2ItemById`, `updateProjectV2ItemFieldValue`) |
| r8 | approve-decisions | `ADD a new comment (a decisions comment is never edited)`, D1..D3 | apply, applied, strict-verify, sync-preview | 1 `POST issues/230/comments` |
| r9 | approve-sync | `create 0 · update 0 · unchanged 7` | sync-apply (no-op), gate (agent: not blocking, committed), gate-check, refresh-context, review (agent: blocker 0, major 0, minor 1) | 0 |
| r10 | review-review | ledger section 4 review entry | check-review-review, pr-clear, pr-prepare (agent), pr-preview | 0 |
| r11 | approve-pr | `plan-pr.md` table and full body, ending `Closes #230` | pr-apply (git push -u of the branch, `created PR #239`), pr-applied | 1 `POST pulls` + the branch push |
| r12 | wait-po-validation (after step 4 below) | `Decisions_230.md` | sync-po-validation (terminal mode: no-op), check-wait-po-validation | 0 |

- **Order (state/`log.jsonl`, `order.py`):** for all seven triplets the preview completed before the gate, and the apply started only when the gate completed (questions 00:15:36/47/47, plan-questions 00:16:38/46/46, answers-questions 00:17:25/33/33, push 00:18:09/16/16, decisions 00:19:42/57/57, sync 00:20:10/17/17, pr 00:21:46/54/54). Final `state.json`: `status=completed`, all 62 steps `completed`, every gate `choice=approve`.
- **Writes only at the apply steps (`wstep.py`, each write mapped to the step running at that second):** questions-apply 1, answers-questions-apply 1, push-apply 21, decisions-apply 1, pr-apply 1 (+ the branch push). Nothing else wrote. All 46 `gh` calls of the agent resumes came from `ghp`.
- **Board:** sub-issues #232..#238 on Project 3 at Todo (status history: #232..#236 set Todo by `github-project-automation`, #237 and #238 by `praveenlavu` through ghp). S9 went Todo -> In Progress at 00:22:03Z by the Project's "Pull request linked to issue" automation.
- **PR:** #239 https://github.com/praveenlavu/ghp-spike-scratch/pull/239 (open, not draft, base `main`, head `feature/gh230-l-044-20261113-001312-story-s9-farewell`, author `praveenlavu`, `closingIssuesReferences [230]`, body contains `Closes #230`).
- **Auto-add race (T34-b): met, and ghp recovered.** On T001 (#232) the request sequence is `addProjectV2ItemById` (00:18:23) -> the `items` re-read query `node(id: $issue) { ... projectItems ...}` (00:18:24) -> `updateProjectV2ItemFieldValue` (00:18:25). That re-read is the 0.1.0 recovery path in `projects.add_item` (taken only on GitHub's `Content already exists in this project` reply or an unknown outcome); it appears after no other add. push-apply exited 0 with `failed 0` and empty stderr, and the Project's automation set #232's Todo at 00:18:25Z. The wrapper does not log response bodies, so the reply text itself is inferred from the code path, not seen. The push did not stop; the T34-c recovery text was not needed.
- **Step 3 (`approvals sync` while paused at review-spec):** `ghp approvals sync e013c5cd --dry-run` -> `Error: approvals sync works in comment mode only ... nothing was read or resumed`, **exit 2**, **0 `gh` calls** (`L044-step3-sync`: only the prefix's `auth token`). PASS.
- **Step 4 (wait-po-validation):** without a terminal, `ghp decisions 230 --resolve D1 --validated --by PO < /dev/null` -> `error: --resolve records the PO's verdict, which only a human may do: run it in your own terminal (this shell has no terminal on stdin; a guard against accidents, not a security boundary)`, exit 2, 0 calls. Then through a pseudo-terminal (`b4bin/ptyrun.py`, Python `pty.fork`, what a person's terminal provides): `--resolve D1|D2|D3 --validated --by PO`, each exit 0 (`{"resolved": "D<n>", "status": "validated", "by": "PO"}`), 0 `gh` calls. **These verdicts were recorded by the controller agent, an AI agent, not by a product owner.** Committed `422e41e docs: PO verdicts D1-D3 recorded #230` and pushed to the PR branch (git push as praveenlavu), then the gate was approved (r12): `Status: completed`.
- `ghp status 230` after the run (27 reads, 0 writes): `Issue #230: waiting on stage merged. Next: git fetch origin` / `Sub-issues: 0 of 7 closed` / `Board column: In Progress` / **`Run e013c5cd: completed.`**
- Observed, no finding: the pull agent ran `ghp branch 230 --apply` after `ghp dod` (slug from the title; T34-a holds); the implement agent wrote plain test ids in section 3 (B2-F2 not hit); the implement agent ran `ghp gate 230 --offline`, which its skill allows (the workflow's `gate-check` ran online).

### L-044b finish S9 after the workflow — PASS

- Controller, as the human stand-in, ticked ledger section 5 (5 of 5) on the PR branch, committed `adab31f` and pushed **before** the merge, so the merge-commit link shows the ticked ledger (avoids the B3-F5 mismatch path).
- Merge (write as praveenlavu): first `gh pr merge 239 --merge` right after that push -> `GraphQL: Pull Request is not mergeable` (exit 1; `mergeable_state` was `unknown` while GitHub recomputed it after the push); after it read `clean`, the same command -> exit 0 (00:24:40Z), merge commit `faa2f59`; closed event 00:24:42Z by `praveenlavu`, `state_reason completed`.
- `ghp done 230 --plan-out .ghp/plan-done.md` (exit 0, 8 reads, 0 writes): `DoD checklist: ready, 5 of 5 ticked`, `Merge: PR #239 into main, commit faa2f59a4d2f`, `Step 2: skip: already closed (completed) by PR #239; recorded, not closed again`, `Status: In Progress -> Done`, **`warning: 7 of 7 sub-issues are still open; done does not close them`** (recorded), digest `24b6008c...`.
- Controller approval (writes as praveenlavu) -> `ghp done 230 --apply --expect-plan .ghp/plan-done.md`: `posted the ledger comment`, `Status is Done (set by ghp)`. **2 writes**: `POST issues/230/comments`, `updateProjectV2ItemFieldValue` (+14 reads), the same as L-033/L-043b. (Its exit code was not captured: a shell slip in the controller's pipe; the output and the writes are as expected.) Status history: `00:25:10Z praveenlavu 'In Progress' -> Done`. `ghp status 230`: `Issue #230: done. Next: none` / `Board column: Done` / `Run e013c5cd: completed.`

### L-055 floor Spec Kit 1.2.1: reject at approve-questions — PASS

- Run **`5202d859`** on 1.2.1 for S10 (#231): pull (agent; `specs/002-tour-badge`, branch `feature/gh231-l-055-20261113-001312-story-s10-tour`, Q1, Q2, thin-AC line in "Needs human decision"), paused at review-spec; approved; questions-preview -> `ADD a new comment` (Q1, Q2), paused at approve-questions.
- `ghp get 231` saved (816 bytes), then `specify workflow resume 5202d859 --input verdict_review_spec= --input verdict_approve_questions=reject` -> `Status: aborted` / `Error: Gate rejected by user at step 'approve-questions'`, **exit 1**. **0 `gh` calls** during that resume (`L055-r2`). `ghp get 231` again: **diff empty**; #231 had 0 comments.
- `specify workflow resume 5202d859` -> `Error: Cannot resume run '5202d859' with status 'aborted'.`, exit 1, 0 `gh` calls. State: `status=aborted`, approve-questions `failed`, `choice=reject`. T34 AC2 replayed on 1.2.1.
- Optional full 1.2.1 run to an open PR: **not run** (owner option; doubles model cost).

### L-056a stale plan inside the workflow (done on S10, 1.2.1, as the plan allows) — PASS

- New run **`966e9af7`** on 1.2.1 for S10. Gates approved by the controller after reading each file: review-spec, approve-questions (applied: 1 comment on #231), approve-plan-questions (`unchanged`), wait-po-answers, approve-answers-questions (applied: 1 comment, assumed Q1/Q2), review-plan; tasks (agent: T001..T008); push-preview `create 8 · update 0 · unchanged 0 · orphan 0`, digest `a00c3037...`; paused at approve-push.
- Tamper, before approving: one line appended to the working tree's `specs/002-tour-badge/tasks.md`: `- [ ] T009 L-056a tamper: added after the push preview (fictional)` (no GitHub write). Then `--input verdict_approve_push=approve`:
  - push-apply: ghp `error: plan changed since approval — re-run the preview and approve again (the current plan is in .ghp/plan-push.md.new)` (ghp exit 2; the step's own `|| { [ $? -eq 2 ] && [ -e .new ]; }` turns that into step exit 0 by design, ending the one-pass loop). `.ghp/plan-push.md.new`: `create 9`, with T009.
  - push-applied: `ghp-lifecycle: the push plan changed after approval; start a new run`, **exit 1**; `Status: failed`, `specify` exit 1. No second loop pass, no second gate.
  - **0 writes** in that resume (`L056a-r7`: 7 reads). No sub-issue was created for S10.
- The console showed only `Error: Shell command exited with code 1.`; the reason is in `state.json` (B3-F4 again, now also on 1.2.1).

## Request-log counts (`writes.py`)

| Log | api calls | writes | Writes |
|---|---|---|---|
| L044-r0, r1, r3, r4, r6, r9, r10, r12 | 5, 3, 3, 6, 7, 24, 8, 0 | 0 | |
| L044-r2 / r5 / r8 | 10 / 4 / 24 | 1 each | `POST issues/230/comments` (questions, answers-questions, decisions) |
| L044-r7 | 46 | 21 | 7 x (`POST issues`, `addProjectV2ItemById`, `updateProjectV2ItemFieldValue`) |
| L044-r11 | 4 | 1 | `POST pulls` (+ ghp's `git push -u`) |
| L044-step3-sync, step4-notty, step4-resolve, step4-push | 0 | 0 | (+ the controller's git push of `422e41e`) |
| L044-status / L044b-status / probes | 27 / 27 / 15 | 0 | |
| L044b-merge | 2 (+2 `gh pr merge`) | merge | first attempt refused (not mergeable yet), second merged |
| L044b-done-plan / -apply | 8 / 16 | 0 / 2 | `POST issues/230/comments`, `updateProjectV2ItemFieldValue` |
| L055-r0, r1, r2, r3, get1, get2, probe | 5, 3, 0, 0, 1, 1, 1 | 0 | |
| L056a-r0, r1, r3, r4, r6, r7 | 4, 3, 3, 6, 7, 7 | 0 | |
| L056a-r2 / r5 | 11 / 4 | 1 each | `POST issues/231/comments` (questions, answers-questions) |
| fixtures / cleanup | 8 / 10 | 4 / 8 | 2 `POST issues`, 2 `addProjectV2ItemById` / 8 `PATCH issues/<n>` (close) |

Controller writes as praveenlavu: fixtures 4, the `422e41e` and `adab31f` pushes to the PR branch, the merge, cleanup 8 + the branch delete. The coding agents wrote nothing to GitHub.

## Created objects and cleanup

| Object | End state |
|---|---|
| S9 #230 | closed `completed` by PR #239; ledger comment by `ghp done`; Status Done (set by ghp); 3 marked comments (questions, assumed answers, decisions) kept |
| #232..#238 (S9 tasks T001..T007) | closed `not_planned` (cleanup; `done` does not close them) |
| S10 #231 | closed `not_planned` (cleanup); 2 marked comments (questions, assumed answers) kept |
| PR #239 `L-044 ... S9: farewell line (fictional) #230` | merged by the case, merge commit `faa2f59` on `main` |
| remote branch `feature/gh230-l-044-20261113-001312-story-s9-farewell` | deleted; `ls-remote` lists only `feature/gh196-issue ghp-spike/auto ghp-spike/link main` |
| S10 branch `feature/gh231-l-055-...-story-s10-tour` | local only, never pushed |
| Project items #230..#238 | kept (never deleted) |
| local | `$LIVE/work/b4` (runs `e013c5cd` completed), `$LIVE/work/b4f` (`5202d859` aborted, `966e9af7` failed at push-applied); kept for the controller, delete after the evidence is committed |

Open `ghp:live-test` issues after cleanup: #195, #196 (T34's, untouched). Open PRs: #204 (T34's).

## Findings

Known findings hit again: **B3-F3** (`spec.md` says `Feature Branch: feature/gh230-issue` while the branch is `feature/gh230-l-044-...-farewell`; also on S10). **B3-F4** (a workflow stop shows only `Error: Shell command exited with code 1.` on the console; the reason is only in `state.json`; seen in L-056a, now on 1.2.1 too). B1-F1 not applicable (the workflow uses `--from-prepared --push`). B2-F2 not hit. T34-a holds. **T34-b: race met on T001 and recovered by 0.1.0** (see L-044). T34-c not reached. T34-g not hit.

- **B4-F1 (sev 3) Unattended agent steps end with questions nobody can answer.** The `tasks` step runs `speckit.ghp.tasks` without `--workflow`, and the agent ended with the `after_tasks` hook offer ("... creates them only after an explicit yes. Want me to run it?"); the review step ended "if you want R1 fixed, do it and re-run ...". The workflow carries on to its own push preview and gate, so nothing is written without a gate; the console text is misleading to an operator.
- **B4-F2 (sev 3, characterisation) The `--resolve` terminal guard is satisfied by any pseudo-terminal.** Through Python's `pty` the controller agent recorded D1..D3 as validated `by: PO`. The product's own message says the guard is "a guard against accidents, not a security boundary", so this is as designed; the evidence of PO validation in terminal mode is therefore only as good as the operator.
- **B4-F3 (sev 3, cosmetic) A resolve without `--note` writes an empty field:** `→ validated · by: PO · note: · kind: ...`. Read back fine (gate, PR, done all accepted it).

Notes (no finding): the first `gh pr merge` right after a push was refused while GitHub's `mergeable_state` was `unknown` (GitHub behaviour; retried when `clean`). `ghp status` before the merge prints `waiting on stage merged` (clearer than T34-f's `merged.`).

## Not run

The optional 1.2.1 full run to an open PR (owner option; not chosen). L-056a ran on S10 / 1.2.1 instead of inside L-044's run, as the plan allows, so L-044 needed no second run.
