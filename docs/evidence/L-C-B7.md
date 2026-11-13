# C-B7 evidence: live pytest suite at v0.1.1 (L-059) and install route on Spec Kit 1.4.0 (L-060)

Run 2026-11-13 (UTC about 17:06 to 17:23), batch stamp `20261113-170615`. All data fictional.
`$LIVE = <scratch>/live011c`

- Account: `praveenlavu` (`GET /user` -> `praveenlavu 22502189`). Every gh/pytest/git-network/uvx command ran with `GH_TOKEN=$(gh auth token -u praveenlavu)`; no auth switch/refresh/login. Git used the per-command credential helper (`git credential fill` -> `username=praveenlavu`). Token-pattern scan of `$LIVE/log` and `$LIVE/evidence`: 0 files.
- Forbidden-account list: `.scrub-denylist` copied with `cp` into the clone (not opened, printed or searched; `git status` clean). The guard in `tests/live/conftest.py` read it.
- Targets: R1 `praveenlavu/ghp-spike-scratch` + user Project 3 (suite), R2 `praveenlavu/ghp-live-comment` (public; Actions tests only).
- Product: fresh clone of `praveenlavu/spec-kit-ghp` at `v0.1.1` (`e440627644e6`) in `$LIVE/work/b7`, own `.venv` (pip upgraded inside), `pip install -e . pytest` -> `spec-kit-ghp 0.1.1`. No product code changed.

## Deviation from the brief's literal command (with cause)

`GHP_LIVE_REPO` is read once at import (`conftest.py` line 46: `OWNER, REPO, PROJECT = ALLOWED, live_repo(os.environ), 3`) and applies to EVERY live test. Setting it to R2 for the whole suite would point the board tests and the T13 read-path fixtures at R2 (no Project, no T13 story). So the suite ran in two parts, as the brief's targets intend:
- Run 1 (R1 + Project 3): `... pytest tests/live -q -rxX -rf --ignore=tests/live/test_approver_vs_bot.py` without `GHP_LIVE_REPO`, `GHP_REQ_LOG=$LIVE/log/l059.jsonl`, `GHP_T26_RECORD=$LIVE/evidence/t26.json`.
- Run 2 (R2): `GHP_LIVE_REPO=praveenlavu/ghp-live-comment ... pytest tests/live/test_approver_vs_bot.py -q -rxX -rf`, `GHP_REQ_LOG=$LIVE/log/l059-r2.jsonl`, `GHP_T31_RECORD=$LIVE/evidence/t31.json`.
The two parts touch disjoint repositories and ran in parallel.

## Pre-state (`log/pre.jsonl`: 24 reads, 0 writes)

- Project 3 switches: Auto-add sub-issues=true, Auto-close issue=false, Item added=true, Item closed=false, PR linked=true, PR merged=true (not changed; same after).
- R1: private, open `ghp:live-test` [196,195], open PRs [204], branches `feature/gh196-issue ghp-spike/auto ghp-spike/link main`, main `414a33a`, workflows `ghp-spike-bot.yml` only, no `ghp.config.json`/`ghp-t31.yml`.
- R2: public, main `f40c434`, files `README.md ghp.config.json` (blob `110fe9f3`, `{"owner":"praveenlavu","repo":"ghp-live-comment","approval":"comment","approvers":[22502189]}`, saved to `$LIVE/r2-config-before.json`), no workflow files, branches `main`, 0 open `ghp:live-test`. `actions/permissions/workflow` -> `can_approve_pull_request_reviews:false` on both repos.

## L-059 — PASS

| Run | Summary line | Exit | Request log |
|---|---|---|---|
| 1 (R1 + Project 3, 42 tests) | `42 passed in 930.78s (0:15:30)` | 0 | 919 api calls: 772 reads, 147 writes (+ git pushes/merges outside `gh`) |
| 2 (R2, `test_approver_vs_bot.py`, 6 tests) | `6 passed in 143.34s (0:02:23)` | 0 | 48 api calls: 32 reads, 16 writes; plus 5 `gh workflow run` dispatches and 3 git pushes |

Total 48 passed, 0 failed, 0 errors, no xfail/xpass. Flaky re-runs: none needed (nothing failed).

Progression versus 0.1.0 (L-B7: 11 failed, 29 passed, 8 errors):
- B7-F1 fixed: the paginated `find_scratch` found the T13 story; all read-path tests pass; `fixture_only=[]` for all 9 fixture files (issue_story, issue_task, issue_pull_request, comments_rest, comments_graphql, project_fields, project_workflows, project_items, status_events).
- B7-F2 fixed: the switch-aware T26 tests pass with Auto-close and Item closed OFF. `t26.json` ac4: #342 `state ["open", null]`, `workflows_enabled [["Auto-close issue", false], ["Item closed", false]]` (card at Done, issue stays open, as the switch state says). `test_project_show` passes.
- B7-F3 resolved by target: on public R2 the T31 chain ran on Actions. `t31.json`: issue #23, branch `ghp-live/t31-20261113-120726`, vendored from `e440627`; runs A 43469653819 (probe 0, questions 2), B 43469707436 (questions 0, decisions 2), C 43469768798 (decisions 0, bot-approve 0, check-validation 2, check-review 2), D 43469834370 (check-validation 0, check-review 0), E 43469901582 (check-validation 2); each exit as the test asserts.

`t26.json`: issues created 24 (338..385), PRs created 24 (339..384); cleanup PRs closed [349, 351, 352], issues closed not_planned [342, 347, 348, 354, 358, 360, 361, 362, 368], branches deleted 25.

## L-060 — PASS

New empty folder `$LIVE/work/b7-l060` (`ls -A` count 0). Release v0.1.1 assets: `ghp-0.1.1.zip 2100, SHA256SUMS 179, spec-kit-ghp-extension-0.1.1.zip 137559`; `shasum -a 256 -c` -> `ghp-0.1.1.zip: OK` (the line for the extension zip says not found only because that zip was not downloaded).

| Step (`uvx --from git+https://github.com/github/spec-kit@v1.4.0 specify`, `CLI Version 1.4.0`) | Exit | Output |
|---|---|---|
| `init --here --force --non-interactive --integration claude --script sh` | 0 | |
| `extension catalog add .../main/catalog/extensions.json --name ghp --install-allowed` | 0 | `Config saved to .specify/extension-catalogs.yml` |
| `workflow catalog add .../main/catalog/workflows.json --name ghp` | 0 | |
| `bundle install $LIVE/work/b7-dl/ghp-0.1.1.zip` | 0 | `✓ Installed 'ghp' (2 added, 0 already present).` |
| `extension list` | 0 | `Spec Kit for GitHub Projects - human-gated lifecycle (v0.1.1)` / `Commands: 13 \| Hooks: 2 \| Priority: 10 \| Status: Enabled` |
| `workflow list` | 0 | `GitHub issue lifecycle (ghp-lifecycle) v0.1.1` |

GitHub writes: 0 (`log/l060.jsonl`: 0 api calls; 2 non-api gh processes = release view/download).

## Cleanup read-back (`log/post.jsonl`, `log/post-r2.jsonl`; reads only)

| Check | Result |
|---|---|
| R1 open `ghp:live-test` | [196,195] (T34, unchanged); none from this run |
| R1 open PRs | [204] (unchanged) |
| R1 branches | `feature/gh196-issue ghp-spike/auto ghp-spike/link main` (unchanged; no stray branch) |
| R1 main | HEAD `90f809e` "T26 direct commit probe (fictional)" (fixture commits as in earlier batches); top level has no `ghp.config.json`; `.github/workflows` = `ghp-spike-bot.yml` only (no `ghp-t31.yml`) |
| Project 3 switches | unchanged |
| R2 open `ghp:live-test` | 0 (#23 closed not_planned by teardown) |
| R2 branches | `main` only (T31 branch deleted) |
| R2 workflow files | none (`ghp-t31.yml` removed by teardown commit `472ea64`, workflow disabled) |
| R2 `ghp.config.json` | REMOVED by the teardown (`472ea64`, see C-B7-F1); restored by me with one `PUT contents` (`1eab4be`, `log/restore-r2.jsonl`); read-back byte-identical (`diff` -> IDENTICAL, blob `110fe9f3`, same as before) |
| Clone | `git status` clean |

## GitHub writes (all as praveenlavu)

R1/Project 3: 147 API writes (log) + git pushes of ~25 fixture branches and the direct-commit probe (git, outside the `gh` log). R2: 16 API writes + 5 workflow dispatches + 3 git pushes (2 main commits, 1 branch) + 1 config restore = 25.

## Findings

- **C-B7-F1 (severity 2, live test harness, not product).** With `GHP_LIVE_REPO` set to a repository that already has `ghp.config.json` on `main` (R2, comment mode), `test_approver_vs_bot.py` overwrites it during setup and its teardown deletes it (`_put_main(w, {WF_PATH: None, "ghp.config.json": None})`), so the owner's config is lost. Reproduce: R2 main with a config -> run the test with `GHP_LIVE_REPO=praveenlavu/ghp-live-comment` -> `contents` lists only `README.md` (commit `472ea64`). Fix idea: snapshot and restore the prior content.
- **C-B7-F2 (severity 3, harness/docs).** `GHP_LIVE_REPO` is global for the whole suite (conftest line 46), and the conftest doc says to use it for the Actions tests. The suite cannot run in one command with R1 for the board tests and R2 for the Actions tests; it needs two runs as above. Reproduce: run `pytest tests/live` with `GHP_LIVE_REPO=praveenlavu/ghp-live-comment` -> the read-path and board tests target R2.
- No product finding. Only `praveenlavu` was used, and no write went outside R1, Project 3 or R2.
