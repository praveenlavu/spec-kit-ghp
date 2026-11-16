# C-R3 evidence: live re-run of L-061 (two simultaneous `ghp push --apply`) on released ghp 0.1.2

Run on 2026-11-16 (UTC from 15:07; batch stamp `20261116-150713` in `$LIVE/stamp`) by the C-R3 live-test executor (an AI agent). All data is fictional. Baseline: `live011/evidence/C-R1a.md` case L-061 (0.1.1: 6 sub-issues, finding C-F1). Change under test: `CHANGELOG.md` 0.1.2 (per-story lock on this machine, up to 300 s, then re-read).

- Account: `praveenlavu` only (`G gh api user` -> `praveenlavu 22502189`). Every `gh`/`ghp`/fixture/git-network command ran with the per-command prefix `GH_TOKEN=$(gh auth token -u praveenlavu) ` (written `G` below); the clone uses the clone-local helper printing `username=praveenlavu` and `password=$(gh auth token -u praveenlavu)`. `gh auth switch/refresh/login` not run. No other account was used, not even for a read. No token in any output, log or this file.
- Target: R1 = `praveenlavu/ghp-spike-scratch` (private, `main` at `90f809e`) + user Project 3, terminal approval mode (`ghp.config.json` = `{"owner":"praveenlavu","repo":"ghp-spike-scratch","project_owner":"praveenlavu","project_number":3}`, untracked). Project 2 not touched.
- Product: released **0.1.2**, `pip install "spec-kit-ghp @ git+https://github.com/praveenlavu/spec-kit-ghp.git@v0.1.2"` into a NEW `$LIVE/venv`; `direct_url.json` commit `8c09f73c0a49effe5d3edfc0c9b93d4a4e7718f2` (requested_revision `v0.1.2`); `ghp --version` -> `ghp 0.1.2`. No product code changed.
- Harness: `live011/bin` (wrapper `gh` + `reqlog.py`, `writes.py`, `fx.py`) and `env.sh` copied to `$LIVE`; only change: `live011` -> `live012` in the wrapper default paths and `env.sh`. Fresh clone `$LIVE/work`.
- Approval: terminal mode, as in C-R1a L-061: the plan was previewed once (`L-061-plan`) and approved by the executing agent (an AI agent, not an independent human); both apply runs were `G ghp push 391 --apply` with stdin `/dev/null` and no `--expect-plan` (same as C-R1a; push in terminal mode asks no typed code, `plan.arm_or_refuse` arms on `--apply`). No run was refused, so no redo was needed.

`$LIVE = <scratch>/live012`; logs `$LIVE/log/`, outputs `$LIVE/out/`, ledger and tasks copied to `$LIVE/keep/`.

## Objects

| Object | Number | Made by |
|---|---|---|
| S13 `L-061 20261116-150713 story S13: two pushes on 0.1.2 (fictional)`, label `ghp:live-test`, AC1 | **#391** | fx.py (1 write) |
| Ledger `dod/DoD_391.md` (spec dir `specs/c-r3-l061`), `tasks.md` T001..T003 `Probe task one/two/three (fictional)` | local only | `ghp dod` (1 read), editor |
| Tasks T001, T002, T003 | **#392, #393, #394** | `ghp push` run B |

## Cases

| Case | Command | Exit | Output (abbrev.) | reads/writes | Result |
|---|---|---|---|---|---|
| prep | `G ghp push 391` (preview) | 0 | `create 3 · update 0 · unchanged 0 · orphan 0`, digest `e378af97...` (same digest as 0.1.1's L-061 plan) | 7/0 | ok |
| **A** (L-061) | two `G ghp push 391 --apply` started in the background from one shell, then `wait`; both start at epoch `1794841658.094` (15:07:38 UTC) | A 0, B 0 | **B**: plan `create 3`, `created 3, updated 0, failed 0` `T001 #392 T002 #393 T003 #394`; first api call 15:07:38, last 15:08:00, ended 15:08:00.8 (22.7 s). **A**: first api call **15:08:00** (waited ~22 s on the lock, no call before), plan `create 0 · update 0 · unchanged 3 · orphan 0`, digest `63b5f423...`, `created 0, updated 0, failed 0`, ended 15:08:08 (30.3 s). POST issues (all from B): 15:07:43, 15:07:49, 15:07:55. `fx sub-issues 391` -> **exactly 3**: #392 T001, #393 T002, #394 T003, no duplicates | B 13/9 (3x POST issues, 3x addProjectV2ItemById, 3x updateProjectV2ItemFieldValue); A 12/0 | **PASS** (0.1.1 FAIL C-F1 fixed: 6 -> 3 sub-issues, 18 -> 9 writes) |
| **B** | `G ghp push 391` (preview), then `G ghp push 391 --apply` | 0, 0 | both `create 0 · update 0 · unchanged 3 · orphan 0`, `unchanged: T001, T002, T003 (3)`; apply `created 0, updated 0, failed 0`; **no `E_TASK_MARKER_AMBIGUOUS`** | 12/0 each | **PASS** |
| **C** | lock timeout (300 s) | - | not forced live (per brief). Unit test present: `spec-kit-ghp/tests/test_push.py:1056-1062` (second `_story_lock(ref, wait=0.3)` refused while held; another story not blocked; released after exit). Source: `ghp/push.py` `_story_lock`, message `another \`ghp push --apply\` of <owner>/<repo>#<n> is still running on this machine; wait for it to finish, then run again` | 0 | **PASS (unit-tested, not live)** |

## Write counts (request log)

| Source | Writes |
|---|---|
| product (`ghp`, previewed and approved) | 9 (run B only; run A 0, third run 0) |
| fixtures (fx.py) | 5: story 1, cleanup 4 |
| git | 0 (clone only; nothing pushed) |
| previews, dod, sub-issues read | 0 |

## Cleanup

| Object | State |
|---|---|
| #391 S13, #392..#394 | closed `not_planned` (Project items of #392..#394 left, as C-R1a) |
| R1 `main` | `90f809e` before and after (`git ls-remote origin main`); no branch, commit or file pushed |
| local | `dod/DoD_391.md`, `specs/c-r3-l061/`, `ghp.config.json` untracked in the scratch clone only |

## Findings

- None new at sev 1-2. C-F1 (0.1.1, sev 2) is fixed for two runs on the same machine.
- Note (sev 3, observation, not a defect): the lock file `$TMPDIR/ghp-push-<uid>-<hash>.lock` (0 bytes, mode 0600) stays after the run (older ones from 2026-11-15 present too). Harmless: the lock is the flock, released on close (`os.close(fd)  # closing releases the lock`); the code never removes the file. Repro: `ls $TMPDIR/ghp-push-*` after any `push --apply`.
- Not covered (as the changelog says): two runs from different machines, and Windows (no lock).
