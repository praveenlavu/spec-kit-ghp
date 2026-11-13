This is account-versus-bot, not a two-human test.

# C-R2 evidence: comment approval mode on R2 against ghp 0.1.1 (PLAN-LIVE-0.1 round 2, brief C-R2)

Run 2026-11-13, stamp `20261113-161647`, by the C-R2 executor agent.

- **Account.** Every `gh`, `ghp` and `git` network command used `praveenlavu` (user id 22502189; `GET /user` read first) through the per-command prefix `GH_TOKEN=$(gh auth token -u praveenlavu)`; git used the per-command credential helper (checked with `git credential fill`, only `username=praveenlavu` shown). No `gh auth switch/refresh/login`. No other account was used, not even for a read. No token typed, echoed, logged or written (scan of `log/`, `runs/`, `comments.txt`: 0 token patterns). No secret, PAT or setting change. The workflow used only its own `${{ github.token }}`.
- **Agent under test:** `github-actions[bot]` (41898282) on hosted runners. **Approver:** `praveenlavu` posting `/approve`, `/reject`, `/answer` through the REST API (`bin/fx.py`, `bin/post.sh`, bodies via stdin), as in B5.
- **Target.** Only `praveenlavu/ghp-live-comment` (public, no Project). Nothing else written.
- **Product under test.** Laptop: `pip install "spec-kit-ghp @ git+https://github.com/praveenlavu/spec-kit-ghp.git@v0.1.1"` -> `ghp 0.1.1`. Runner: `pip install ...@e440627644e6e5268ef3ce19ee4547f75799e03d`; `ghp 0.1.1` printed in every run. Product code not changed.
- **Harness** (`LIVE=.../scratchpad/live011r2`): B5's `bin/gh` request-log wrapper, `writes.py`, `fx.py`, `dispatch.sh`, `l049.sh`, `l049d.sh` (repointed to this LIVE dir and to stories #14/#16), new `post.sh` and `out.sh`. Workflow `.github/workflows/ghp-live.yml` = B5's with the install pinned to e440627, the `arg` charset widened to `[]:>` (through a variable regex), and new steps `persist` (bot commits `dod/`+`specs/` changes back to the ref), `pr-body`, `done-apply-nox`. Pins (`.ghp/approvals/`) live only inside one runner job, so every pin-then-apply sequence runs in ONE dispatch.
- **Config on `main`:** it had been removed by the B5 cleanup; restored in `543a014`: `{"owner":"praveenlavu","repo":"ghp-live-comment","approval":"comment","approvers":[22502189]}`. A first push had a YAML error in the new `arg` description (`: ` unquoted; GitHub refused the dispatch, HTTP 422 "does not have workflow_dispatch"), fixed in `1dd00d0` (harness bug, not product).

## Objects

| What | Ids |
|---|---|
| stories (label `ghp:live-test`) | #9 E (L-045..L-048, L-052, L-053, L-057, F-1 progression), #10 Na (edited approval), #11 Nb (see note), #12 Nc (reject), #13 Nd (laptop done), #21 Nb2 (plan changed), #14 F (L-049 a..i3), #15 G (L-056), #16 H (L-049 q/r/r2, L-050, L-051) |
| bot sub-issues | #17, #18, #19 (T001..T003 of #9) |
| PRs (opened and merged by praveenlavu as fixtures) | #20 (`Closes #9`..`#13`, merge `b457519`), #22 (`Closes #21`, merge `5a95a00`) |
| branches | `ghp-live/r2-20261113-161647`, `...-b`, `ghp-live/r2-trust` |
| main commits | `543a014` harness+config, `1dd00d0` YAML fix, `b457519` merge #20, `f7ba364` #13 ledger edit, `5a95a00` merge #22, `6a523fa` #21 extra tick, final cleanup commit |

## Workflow runs (`github-actions[bot]`, https://github.com/praveenlavu/ghp-live-comment/actions/runs/<id>)

| tag | run id | steps -> exit |
|---|---|---|
| R1-E | 43461738003 | probe 0, qpost **2**, dpost **2**, check-review-spec/plan/review **2**, wait-po-answers 0, wait-po-validation 0, check-approve-push **2**, push-preview 0, push-apply **2** |
| R2-E | 43461883914 | qpost **0**, dpost **0**, review-spec/plan/review **0** (pinned), wait gates **2**, push-preview 0, push-apply **0**, push-preview 0, push-apply **2**, show 0, persist 0 |
| R3-E | 43462092374 | push-preview 0, push-apply **0** (no-op), qsync 0, dsync 0, dresolve **2**, wait gates **0** |
| R4-E | 43462257491 | activate **2**, pr-preview 0, pr-apply **2**, projcfg: project-show **1**, doctor 0 |
| R5-E | 43462349231 | pr-preview 0, pr-apply **1** (HTTP 403) |
| F1-1 | 43462531269 | done-preview 0, done-apply **2** |
| F1-2 | 43462631756 | done-preview 0, done-apply **2** (E_NOT_PINNED), check-approve-done **0**, done-apply **0**, show 0, done-preview 0 (no-op) |
| F1-h10..h13 | 43462834872, 43462912084, 43463000228, 43463075974 | done-preview 0, done-apply 2 (hashes) |
| F1-neg10 / neg11 / neg12 | 43463204930 / 43463288896 / 43463387781 | check-approve-done 2, apply 2 / check 0, apply **0** / check 2, apply 2 |
| F1-b1, b2 (#13) | 43463606808, 43463693497 | check 0 / check 0, apply **0** |
| F1-b3, b4, b5 (#21) | 43463889379, 43463999148, 43464091229 | preview+apply 2 / check **0** / check **2**, apply **2** |
| L049-ok,a,b,c,d,e,f,g,h,i,ok,i2,i3 | 43464267136, 43464356634, 43464431658, 43464506797, 43464619553, 43464700538, 43464778829, 43464863806, 43464968450, 43465045080, 43465124618, 43465212540, 43465305869 | check-review-spec 0,2,2,2,2,2,2,2,2,**0**,0,2,2 |
| L049D-q, r, r2 | 43465403761, 43465478781, 43465554453 | bot-approve 0 + check 0; bot-approve 0 + check **2**; check 0 |
| L050b | 43465652590 | check-review-spec 0, notrust-check **2** |
| L050a | 43465751743 | bot-approve 0, check-review-spec **2** |
| L056 | 43466430442 | check-review-spec **0** |

## 1. Progression: F-1 (L-054) comment-mode `ghp done`

Fixture: #9 ledger ticked (`3718263`, `97aca60`), tasks pushed by the bot (#17..#19), PR #20 body `Closes #9` .. `Closes #13`, opened and merged by praveenlavu (`gh pr merge 20 --merge`; read back `merged true`, `merged_by praveenlavu`). The merge closed #9..#13 `completed`; praveenlavu then **reopened** them (PATCH `state: open`) so that `done` itself must close them (a stronger check of the write than the "already closed" path). Deviation from the brief, recorded.

**F-1 PASS (fixed in 0.1.1).**
- F1-1 `ghp done 9 --plan-out .ghp/plan-done.md` exit 0: `Merge: PR #20 into main, commit b457519a0cd8`, `Step 1: post the ledger comment`, `Step 2: close #9 as completed`, `Step 3: skip: no Project is configured`, `warning: 3 of 3 sub-issues are still open`, `digest: 8bda8c59...`. `--apply --expect-plan` exit **2**: `... with one of: approve-done 432d7e2e2e7ea9d2f1734a6b2fb0041ce034120febefd23ce35f8402bf36a21d.` Nothing written.
- praveenlavu `/approve approve-done 432d7e2e...` (comment 6858735656).
- F1-2 (one job): apply before the check exit **2** E_NOT_PINNED (wording under (e)); `ghp approvals check 9 --gate approve-done` exit **0** `{"gate": "approve-done", "mode": "comment", "met": true, "pinned": 6858735656}`; pin file shows `approve-done` hash `432d7e2e...`, approval author 22502189; `ghp done 9 --apply --expect-plan` exit **0**: `posted the ledger comment` / `closed #9 as completed`. A following preview: `Action: nothing to do: the ledger comment is posted (comment 6858747197), the issue is closed (completed) ...`.
- Read back (laptop, 0 writes): #9 `closed completed closed_by github-actions[bot] 2026-11-13T16:26:17Z`; events `closed praveenlavu (merge) -> reopened praveenlavu -> closed github-actions[bot]`. Comment 6858747197: author `github-actions[bot]` 41898282, `performed_via_github_app` 15368, body `**DoD complete for #9.** ... **Ledger:** [DoD_9.md](.../blob/b457519a.../dod/DoD_9.md) at merge commit `b457519a0cd8` (PR #20)` + marker `<!-- spec-kit-ghp:done:9 -->`.
- Writes of the done apply: 2 (1 comment, 1 close), shown by the read back (bot comments on #9 = 3: questions, decisions, ledger). Bot-side requests run on the runner and are not in the laptop request log.

**Negatives**
- **(a) PASS** edited approval, #10. `/approve approve-done 9ae45c35...` (6858812941), then PATCHed by praveenlavu (`(edited once, fictional)` appended). F1-neg10: check exit 2 and apply exit 2: `... 1 newer approver comment(s) edited after posting for this plan: check the thread. 1 /approve or /reject comment(s) for this plan's gates did not count: 1 edited after posting.` Fixed words and counts; no comment text. (A non-approver HUMAN cannot be posted with one human account; the edit is the stand-in, as the brief says.)
- **Executor slip, #11:** I approved #11 (6858813856) intending case (b) but my dispatch loop ran `done-apply` on it: check exit 0 (pinned 6858813856), apply exit **0**, #11 closed `completed` by `github-actions[bot]` with the ledger comment. A second, independent PASS of the positive path; case (b) was redone on #21.
- **Observation on #13 (first try at (b)):** after pinning (F1-b1 check 0), I added a section-3 row to `dod/DoD_13.md` on `main` (`f7ba364`). The done plan did not change (digest `a3ad7627...` before and after: the plan carries counts, not the section-3 text), so F1-b2 apply exit 0 and #13 closed `completed` by the bot. Not a product error (the comment links the ledger at the merge commit, which is what was approved); noted as O-2.
- **(b) PASS** plan changed after approval, #21 (PR #22). Hash `ce125315...`; `/approve` 6858920396; F1-b4 check exit **0** (pinned). Then `6a523fa` ticks a 6th DoD box on `main`. F1-b5: preview now `DoD checklist 6 of 6 ticked. The linked file at the merge commit has 5 of 5 ticked: the ticks counted here are not in it.` and `warning: the ledger at merge commit 5a95a002347b has 5 of 5 boxes ticked, not the 6 of 6 counted here from the working tree; the comment says so. For the linked file to show them, tick section 5 on the PR branch before the merge`; check exit **2** asking for `approve-done 42a5b38c...`; apply exit **2**. #21 stayed open (read back). This also shows the 0.1.1 "ticks not in the ledger at the merge commit" bullet **PASS**.
- **(c) PASS** reject, #12. `/approve` (6858814319) then `/reject approve-done f36ba2b2...` (6858814655). F1-neg12: check exit 2, apply exit 2 (`no valid approval for this plan ... approve-done f36ba2b2...`). The text does not name the reject (as in B5 L-049e). #12 stayed open.
- **(d) PASS** laptop as praveenlavu, #13 (before the observation above), clone on `main`, `origin/HEAD -> origin/main`; approval 6858815112 by praveenlavu. `ghp done 13 --plan-out` exit 0 (digest `a3ad7627...`, equal to the runner's). `--apply --expect-plan` exit **2**; `ghp approvals check 13 --gate approve-done` exit **2**; apply again exit **2**, each with `1 /approve or /reject comment(s) for this plan's gates did not count: 1 by an excluded account (the signed-in or CI account, or an account that posted a comment starting with `[spec-kit-ghp:` on this issue, until that comment is deleted).` No pin file was written (`.ghp/approvals` absent). Log `F1-d-laptop.jsonl`: reads=31 writes=0. #13 stayed open until the bot closed it in F1-b2.
- **(e) PASS** E_NOT_PINNED now: `comment approval: no approval of this plan was pinned for issue #9, so the story is not closed. Preview with `ghp done 9 --plan-out .ghp/plan-done.md`; once a trusted approver's `/approve approve-done <hash>` comment for that plan exists, `ghp approvals check 9 --gate approve-done` pins it.` That route was followed and worked (F1-2). E_APPLY_GATE for `approve-push` is unchanged (`gate approve-push is not checked here: the apply it guards (`--apply`) requires the approver comment itself`); `approve-done` is no longer refused by `check`.

## 2. Regression (compared with L-B5 on 0.1.0)

- **L-045 PASS (=0.1.0).** R1-E qpost exit 2 naming `approve-questions e4232fbe...`; `/approve` 6858632408; R2-E exit 0 `posted comment 6858643879` (bot). Probe viewer `github-actions[bot] 41898282 User`.
- **L-046 PASS (=0.1.0, one difference).** Preview `create 3 · update 0 · unchanged 0 · orphan 0`, `Project: no Project is configured`; apply before approval exit 2 `approve-push 974ad032...`, `approve-sync 3bbf...`; after `/approve` (6858634325) apply exit 0 `created 3 ... T001 #17 T002 #18 T003 #19`. No-op plan has another hash (`approve-push 1aa1120e...`), exit 2, then after `/approve` (6858661290) R3-E exit 0 `created 0, updated 0, failed 0`. Difference: no `label ghp:task does not exist` warning, because B5 created the label (environment, not a regression).
- **L-047 PASS (=0.1.0).** `/answer Q1: ...` (6858661722), `/approve D1` (6858662123), `/reject D2` (6858662478). qsync exit 0 `"answered": ["Q1"]`, diff `- [x] Q1: ... → 09:00 is the fictional default (PO comment 6858661722)`; dsync exit 0 `D1 validated`, `D2 rejected` with `via:` ids; dresolve exit 2 `--resolve is refused: approvers are configured on the target branch ...`.
- **L-048 PASS (=0.1.0).** review-spec/plan/review exit 2 then 0 with `pinned` 6858633209/6858633530/6858633939 (pin file printed, `version 1`, `last_edited_at null`); wait gates 2 (`1 open (Q1)`, `2 open (D1, D2)`) then 0; `check approve-push` exit 2 E_APPLY_GATE. O-1 still holds (R1-E wait gates exit 0 with `"open": []` before any questions/decisions comment).
- **L-049 PASS except (i) (=0.1.0).** Story #14, hash per sub-case read on the laptop (0 writes). ok 0 (6858956079); (a) ZWSP 2 `1 newer malformed command line(s)`; (b) RLM 2 `2 newer malformed`; (c) fenced 2; (d) bot approve 2, now with `1 /approve or /reject comment(s) ... did not count: 1 by a bot or app account` (new 0.1.1 text, **PASS**); (e) newer reject 2; (f) edited 2 `1 newer approver comment(s) edited after posting` + `did not count: 1 edited after posting`; (g) minimized 2 `1 newer approver comment(s) hidden (minimized)`; (h) old hash 2, asks for `119ec7ef...`; (i) mid-line `Quoting the kit: [spec-kit-ghp:approval-request] ...` exit **0** (pinned 6859062427) = F-B5-2 still open; ok 0; (i2) 2 and (i3) 2, now explained: `did not count: 1 by a bot or app account; 9 by an excluded account (... an account that posted a comment starting with `[spec-kit-ghp:` on this issue, until that comment is deleted)` (F-B5-3 diagnosability resolved). Story #16: (q) quoted request newer than approve: 0; (r) exact request: **2**; (r2) new approve: 0 (=0.1.0).
- **L-050 PASS (=0.1.0).** (a) branch `ghp-live/r2-trust` config `approvers [22502189, 41898282]` + spec change; bot `/approve review-spec 77409899...`; check exit 2 `did not count: 1 by a bot or app account`. (b) normal clone check 0, no-`origin/HEAD` clone exit 2 `E_TRUST_BASE_UNRESOLVED ...` after `fatal: ref refs/remotes/origin/HEAD is not a symbolic ref`.
- **L-051 PASS (=0.1.0).** Laptop, story #16 right after the bot met the gate with 6859132427: exit 2, `3 /approve or /reject comment(s) ... did not count: 3 by an excluded account ...`; reads=6 writes=0.
- **L-052 BLOCKED (environment) (=0.1.0).** pr-preview exit 0; apply before approval exit 2 naming `approve-pr 774e684c...`; after `/approve` (6858697486) R5-E exit 1 `E_GH_HTTP (HTTP 403): GitHub Actions is not permitted to create or approve pull requests.` `actions/permissions/workflow`: `can_approve_pull_request_reviews: false`. Setting not changed (owner's call).
- **L-053 PASS (=0.1.0).** `ghp activate 9 --apply` exit 2 `E_NO_PROJECT: activate needs a Project: set project_number in ghp.config.json`, no approval text.
- **L-055 NOT RUN.** In the plan it is a B4 case on R1 + Project 3 (a Spec Kit 1.2.1 project and an agent-driven `ghp-lifecycle` run); R1 is outside this brief's only target.
- **L-056 PASS (=0.1.0).** #15: 101 fillers by praveenlavu (1.25 s pacing), `/approve review-spec 70419b58...` as comment 102 (6859252166). Bot check exit 0 `"pinned": 6859252166`. Laptop `ghp comments 15` exit 0, last rows 6859251399 (filler 101) and 6859252166, no dropped-comment note, `wrote .ghp/context/15.md`; requests 3 GraphQL + REST `page=1`, `page=2`; reads=7 writes=0 (incl. the hash read).
- **L-057 PASS (improved).** `ghp project show` exit 1 `E_GH_GRAPHQL (HTTP 200): Could not resolve to a ProjectV2 with the number 3.`; `doctor --json` exit 0: `identity pass "signed in as user id 41898282"`, `scopes unknown`, `project-workflows unknown "GitHub found no such Project or repository for this sign-in: ..."` (was `HTTP 200 (unexpected)`: F-B5-5 **resolved**), `protection unknown (HTTP 403)`.

## 3. 0.1.1 changelog items checked here

| item | result |
|---|---|
| comment-mode `done` closes a story (check pins approve-done) | PASS (#9, also #11, #13) |
| refusal counts set-aside comments, fixed words | PASS (edited, bot, excluded) |
| done comment/plan say when ticks are not in the ledger at the merge commit | PASS (#21) |
| doctor "no such Project" wording | PASS |
| plan/`approvals check` read allowed | PASS (check approve-done) |
| `ghp pr create` always writes the real `Closes #N` line | PASS: the body ends with ghp's own `Closes #9`; but a body file that already holds `Closes #9` still gets a second one (F-B5-4 open) |

## Request-log counts (laptop, praveenlavu)

| log | reads | writes | writes are |
|---|---|---|---|
| setup, setup-dod | 15 | 8 | 8 issues (#9..#16) |
| owner-comments | 0 | 13 | approvals/answers/verdicts on #9, #21, #13 etc. |
| F1-neg | 0 | 6 | 5 approve/reject comments, 1 edit |
| F1-fixture, F1-b-fixture | 8 | 11 | PR #20 create+merge, 5 reopens; issue #21, PR #22 create+merge, reopen |
| L-049-*, L-049D-* | 1 | 22 | owner fixture comments, 1 edit, 1 minimize |
| L-056-fixture | 0 | 102 | 101 fillers + approval |
| dispatch | 153 | 39 | 39 `gh workflow run` (incl. one refused 422) |
| local checks/reads (F1-d-laptop, F1-readback, L-049-local, L-049D-local, L-050a, L-051, L-056-comments) | 107 | 0 | none |
| cleanup | 4 | 10 | 9 closes, workflow disable |
| **total gh writes** | | **211** | plus git pushes as praveenlavu (main: 6 commits; branches: fixture, ledger, 16 L-049 spec bumps, trust; 3 branch deletes) |

Bot writes (read back): comments 5 kit comments on #9/#11/#13 (questions, decisions, 3 ledger), 4 `bot-approve` comments, 3 sub-issues, 3 closes (#9, #11, #13), 1 `persist` commit on the fixture branch.

## Cleanup (read back after)

| Item | State |
|---|---|
| #9, #11, #13 | closed `completed` by `github-actions[bot]` (`ghp done`), left as evidence |
| #10, #12, #14, #15, #16, #21, #17, #18, #19 | closed `not_planned` |
| PRs #20, #22 | merged on purpose (fixtures); open PRs: 0 |
| branches | 3 deleted; only `main` |
| workflow | disabled, removed from `main`; `actions/workflows` total_count 0 |
| `main` | `README.md`, `ghp.config.json` (comment-mode config left in place per brief); fixture files removed |
| left in place | comments, labels, run records, history; R2 itself |

## Findings

- **F-1 RESOLVED** (was severity 2): comment-mode `done` closes a story via `approvals check <n> --gate approve-done` then `done --apply`. Repro: F1-2.
- **F-B5-2 still open (severity 3, docs):** a mid-line `[spec-kit-ghp:approval-request] ...` newer than a valid approve does not make the gate wait (L-049i exit 0). Repro: `l049.sh i`.
- **F-B5-4 still open (severity 3, cosmetic):** `pr create --body-file` with `Closes #9` already in it shows `Closes #9` twice in the body (R4-E preview).
- **F-B5-1 still open (severity 3, docs/doctor):** bot `pr create` needs "Allow GitHub Actions to create and approve pull requests"; `doctor` (0.1.1, run as the bot) has no row for it (now documented per the changelog; not checked here).
- **F-R2-1 (severity 3, wording):** the set-aside count says "for this plan's gates" but counts every older command for the same gate on the issue (L-049i2: `9 by an excluded account` on a plan that had 1). Repro: L-049 i2 run 43465212540.
- **O-2 (observation):** the done plan hashes counts, not section-3 text; a section-3 row added after approval leaves the approval valid (#13). The comment links the ledger at the merge commit, so the linked text is what was merged.
- **F-B5-3 and F-B5-5 RESOLVED.** O-1 unchanged.
