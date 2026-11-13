# C-T39 evidence: the four reusable workflows on hosted runners, called from another repository, pinned to 0.1.1

Run on 2026-11-13, UTC 17:26 to 17:47. Stamp `20261113-172637`. All data is fictional.

- **Account:** `praveenlavu` only, as `GH_TOKEN=$(gh auth token -u praveenlavu)` per command. git used the per-command credential helper (username
  `praveenlavu`), author `praveenlavu <22502189+praveenlavu@users.noreply.github.com>`. No `gh auth switch/refresh/login`. No other account used.
- **Target:** https://github.com/praveenlavu/ghp-consumer-scratch (public) only. No secret created (`actions/secrets total_count: 0` at the end),
  no repository setting changed.
- **Product version:** `gh api repos/praveenlavu/spec-kit-ghp/commits/v0.1.1 --jq .sha` -> `e440627644e6e5268ef3ce19ee4547f75799e03d`. Every
  `uses:` line in the caller is `praveenlavu/spec-kit-ghp/.github/workflows/<name>.yml@e440627644e6e5268ef3ce19ee4547f75799e03d` and every `ghp_ref`
  is the same SHA (8 occurrences, 0 of the old `cefd9fa`). 29 of the 31 non-skipped jobs print `ghp 0.1.1`; the other two install no ghp (see N2).
  No log prints `ghp 0.1.0`.
- **Request log:** `$LIVE/log/*.jsonl` (wrapper `$LIVE/bin/gh`). Reads/writes per file in "Counts".
- **Logs:** `$LIVE/logs/` (one file per job), `$LIVE = <scratch>/live011d`.

## State found and old fixtures cleaned (T39 0.1.0 run, all authored by praveenlavu, matching docs/evidence/T39.md)

| object | action | result |
|---|---|---|
| PR #6, #7, #8 (T39 fixtures) | closed unmerged | `6 closed false`, `7 closed false`, `8 closed false` |
| branches `feature/gh1..4-points`, `diag/actions` | deleted | `deleted ...` x5 |
| issues #2, #3, #4 (T39 fixtures) | closed `not_planned` | `closed not_planned` x3 |
| `release/1.0`, #1, PR #5 | left as they were | |

The three PR closes fired `closed` runs 43472818916/43472820801/43472825157: all `skipped` (unmerged close).

## Objects created

| object | what |
|---|---|
| label `ghp:live-test` | did not exist; created |
| issues #9, #10, #11 | `L-a|L-b|L-c 20261113-172637 Points on purchases, CI 0.1.1 (fictional)`, body `## Acceptance criteria / - AC-1: a purchase earns points` |
| main `c8cba0e` | caller `.github/workflows/ghp.yml` re-pinned 0.1.0 -> 0.1.1 SHA; `ghp.config.json` advisory (`gate.block: ["ac_traceability"]`) |
| main `16f16d7` | squash merge of PR #12 |
| main `47c19e9` | `ghp.config.json` `mode: strict` (for c, f, h) |
| PR #12 `feature/gh9-points` | clean ledger + test, body `Closes #9` |
| PR #13 `feature/gh10-points` | untraced ledger (`test_gone`), body `Refs #10`; later 8 small commits for (h) |
| PR #14 `feature/gh11-points` | `ghp.config.json` -> advisory (forged) + untraced ledger, body `Refs #11` |

## Cases (job links under https://github.com/praveenlavu/ghp-consumer-scratch/actions/runs/)

### (a) clean PR passes - PASS
PR #12, run 43472931469 (success). gate 132049246599 success (step 8 "Run the gate: success", step 9 "Run the strict check: skipped" = advisory
target); branch-guard 132049246709 success `Issue #9 exists.`; strict-verify 132049246136 success, notice `ghp strict verify skipped: the target
branch's ghp.config.json is not strict, so the test command is not run (it would come from the pull request)`. Expected: pass. Actual: pass.

### (b) PR that fails the gate - PASS
PR #13, run 43472935848 (failure), main advisory. gate 132049264156 **failure**, step 8 "Run the gate: failure"; branch-guard success `Issue #10 exists.`;
strict-verify success (skipped notice). Log/annotation:
`##[error]ghp gate exit 1 (1: a blocking finding; 2: refused; 3: invalid config or ledger) - blocking rule ids: ac_traceability; details in the job summary`.
Expected: gate fails on `ac_traceability`. Actual: as expected.

### (c) the PR's own ghp.config.json is ignored - PASS
main strict (`47c19e9`); PR #14 sets `mode: advisory` in its own config. Run 43473678436 (failure).
gate 132051827137 failure (step 8 gate failure, step 9 strict check **failure**); strict-verify 132051826643 failure; branch-guard success `Issue #11 exists.`
```
gate:          ##[error]ghp gate exit 1 (...) - blocking rule ids: ac_traceability; details in the job summary
gate:          ghp: error: strict check failed: 4 finding(s)
gate:          ##[error]ghp strict check exit 2 - rule ids: evidence-missing record-missing review-missing fidelity-missing
strict-verify: ##[error]ghp strict verify exit 2 - rule ids: evidence-missing test-command
```
The strict check step runs only when `ghp strict mode --base origin/main` says strict, and strict-verify re-ran instead of printing the advisory
"skipped" notice: the PR's advisory config was ignored, trust came from the target branch. Expected: still strict, fails. Actual: as expected.

### (d) link-on-merge without GHP_PROJECT_TOKEN - PASS
PR #12 squash-merged (`PUT pulls/12/merge` -> `true 16f16d7`). Run 43473132203 (success): link 132049945792 success; gate, branch-guard,
strict-verify skipped (the caller's `if:`). Steps: 3 "Extract the issue numbers: success", 4 "Comment on and close the issues: success",
5 "Install ghp"/8 "Close the stories with ghp done": skipped. Log: `#9: already closed, left as it is (never closed again, never reopened).`
Comment on #9 by `github-actions[bot]` (id 41898282), exact:
```
<!-- spec-kit-ghp:merged:12 -->
Merged in #12.

The Project board was not moved by this workflow: the GHP_PROJECT_TOKEN secret is not set.
```
#9 events: `labeled`, `referenced`, `closed` (praveenlavu, 17:28:26, GitHub's own `Closes #9`; state_reason `completed`). The with-secret half: NOT RUN
(no secret exists; not created, per brief).

### (e) the gate job log names the blocking rule ids - PASS
In 0.1.0 (F1) the line was `ghp gate exit 1 (...), see the job summary`. Now, in the log and the check-run annotation of b (job 132049264156),
c (132051827137) and every later b run: `... - blocking rule ids: ac_traceability; details in the job summary`.

### (f) strict check runs even when the gate step fails - PASS
Step list of gate job 132052731730 (PR #13 = case b, re-run on the strict main, run 43473939734), and the same in case c (132051827137):
```
7 Resolve the issue from the branch: success
8 Run the gate: failure
9 Run the strict check: failure      <- ran (0.1.0 run 43325939270: "skipped", F2)
```
Log: `ghp strict check exit 2 - rule ids: evidence-missing record-missing review-missing fidelity-missing`. Job conclusion failure (fails closed).
On the advisory target (run 43472935848) step 9 is `skipped` because mode is advisory, as the step's `if:` says.

### (g) Node 24 action pins, no Node 20 deprecation - PASS
All 11 runs from 17:27 on, 31 non-skipped jobs: logs and `check-runs/<id>/annotations` scanned (`$LIVE/bin/scan.sh`): `node20_hits=0`.
Every job: `Download action repository 'actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09'` (31) and
`'actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1'` (31). The annotations seen are only the ghp notices/errors and the cancel lines
in (h). In 0.1.0 every job had `Node.js 20 is deprecated ...` (F3).

### (h) concurrency on two quick pushes - PASS (after two retries)
The jobs' `concurrency: group: ${{ github.workflow }}-ghp-<job>-<PR number>, cancel-in-progress: true`.
1. Pushes 1 s apart (842afaa, fc8e885, 17:32:07/08): GitHub created ONE run (43473939734, for fc8e885). Not a concurrency test (N1).
2. Pushes 19 s apart (d352b6c 17:41:26, 58d0854 17:41:45): run 43475289538's gate and strict-verify had already completed (11 s jobs); its
   branch-guard 132057424941 was **cancelled**: `Canceling since a higher priority waiting request for ghp checks-ghp-branch-guard-13 exists`.
3. Pushes 15 s apart, plus a leftover push: run 43475978740 (213451b) **cancelled**, all three jobs cancelled mid-run, e.g. gate 132059774700
   `Canceling since a higher priority waiting request for ghp checks-ghp-gate-13 exists`; run 43476004663 (a1475ff) had strict-verify and
   branch-guard cancelled the same way by 0382dee.
Final state: PR #13 head `0382dee` checks = gate failure (the real `ac_traceability` finding), strict-verify failure (real `evidence-missing
test-command`), branch-guard **success**, link skipped. Every cancelled job has check conclusion `cancelled`, never `failure`; the newest head
carries a complete set of results. Expected: earlier run cancelled, nothing failed because of the cancel. Actual: as expected.

## Counts (request log; git pushes are not in the log)

| log | reads | writes |
|---|---|---|
| cleanup-old | 1 | 11 (3 PR closes, 5 branch deletes, 3 issue closes) |
| setup | 9 | 4 (label, 3 issues) |
| ab | 0 | 2 (PRs #12, #13) |
| d | 0 | 1 (merge #12) |
| c | 0 | 1 (PR #14) |
| h | 85 | 0 |
| reads (probes) | 162 | 0 |
| cleanup | 3 | 7 (2 PR closes, 3 branch deletes, 2 issue closes) |

API writes 26; git pushes 13 (main x2, 3 feature branches, 8 commits to `feature/gh10-points` for h); total 39 writes, all as praveenlavu,
all to praveenlavu/ghp-consumer-scratch. Workflow-made write: 1 comment on #9 by `github-actions[bot]`.

## Cleanup

| object | state |
|---|---|
| PR #13, #14 | closed unmerged |
| PR #12 | merged (case d) |
| branches `feature/gh9-points`, `feature/gh10-points`, `feature/gh11-points` | deleted |
| issues #10, #11 | closed `not_planned`; #9 closed `completed` by the merge |
| label `ghp:live-test` | kept (never deleted) |
| main | left at `47c19e9`: caller pinned to 0.1.1, `ghp.config.json` strict (as T39 left it); branches `main`, `release/1.0`; 0 open PRs |

## Findings

No product finding: every case matched "Expected". 0.1.0 findings F1 (gate log names no rule), F2 (strict check skipped after gate failure),
F3 (Node 20 actions) are fixed on hosted runners.

- **N1 (GitHub behaviour, sev 3).** Two pushes 1 s apart to a PR branch produced one `synchronize` run (for the second head only). Repro: push
  twice within ~1 s, list `actions/runs?branch=<b>`. Not a product issue; a concurrency test must space pushes ~10-15 s.
- **N2 (brief expectation vs design, sev 3).** Not every job log can show `ghp 0.1.1`: link-on-merge without the secret skips "Install ghp" by
  design (job 132049945792, same as 0.1.0), and a job cancelled before its install step (132059775271) never prints it.
- **N3 (GitHub presentation, sev 3).** A cancelled job's annotations are at level `failure` (`The operation was canceled.`), and a superseded run whose
  surviving gate genuinely failed shows run conclusion `failure` (43476004663); the check-run conclusions are `cancelled`. Cosmetic.

## Not run

- link-on-merge with `GHP_PROJECT_TOKEN` / `close-story: true` (no secret; owner action).
- The "close what is still open" path of link-on-merge (GitHub closed #9 first, as in 0.1.0).
- Job summaries are not readable through the REST API, so they were not quoted (the log lines now carry the rule ids).
