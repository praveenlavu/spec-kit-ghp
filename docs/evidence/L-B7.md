# L-B7 evidence: regression of the earlier live tasks on the release (L-059 live pytest suite at v0.1.0, L-060 install route on Spec Kit 1.4.0)

Run on 2026-11-13 (UTC 00:34 to about 01:05; batch stamp `20261113-003430` in `$LIVE/stamp-b7`; the suite's own stamp `20261112-193536` is local time) by the B7 live-test agent. All data is fictional. Plan: `docs/plans/PLAN-LIVE-0.1.md` batch B7 (L-059, L-060).

- Only the owner's own account was used.
- Account: `praveenlavu` (`GET /user` -> `praveenlavu 22502189`). Every `gh`, `specify` and `pytest` command ran with the per-command prefix `GH_TOKEN=$(gh auth token -u praveenlavu) `. `gh auth switch/refresh/login` were not run. The clone used the per-command credential helper from section 0. Scan of `$LIVE/log/b7`, `$LIVE/work/b7out` and `t26.json`: 0 token-shaped strings, 0 hits of the other account's name.
- Forbidden-account list: `.scrub-denylist` copied with `cp` from the private dev repository into the clone (git-ignored; `git status` clean). It was not opened, printed or searched. The guard (`tests/live/conftest.py`, `DENYLIST` at the repository root) read it itself.
- Target: R1 = `praveenlavu/ghp-spike-scratch` (`main`) + user Project 3, R1 alone. `ghp-live-comment`, `live-r2` and `ghp-consumer-scratch` were not touched. The T34 objects were not touched: before and after B7, open `ghp:live-test` issues `[196,195]`, open PRs `[204]`, branches `feature/gh196-issue ghp-spike/auto ghp-spike/link main`.
- Product: a fresh clone of `praveenlavu/spec-kit-ghp` at tag `v0.1.0` (`cefd9fa4ca0e`) in `$LIVE/work/b7`, own venv `.venv` (Python 3.9.6; pip upgraded inside the venv because pip 21.2 cannot do an editable install from `pyproject.toml`), `pip install -e . pytest` -> `spec-kit-ghp 0.1.0` editable from the clone. No product code changed.

`$LIVE = <scratch>/live`; request logs `$LIVE/log/b7/{pre,l059,l059-readpatch,l060,diag,post}.jsonl`; console outputs `$LIVE/work/b7out/`.

## Project 3 workflow switches (read before and after; GraphQL `user.projectV2(number:3).workflows`, exit 0)

`Auto-add sub-issues to project=true`, `Auto-close issue=false`, `Item added to project=true`, `Item closed=false`, `Pull request linked to issue=true`, `Pull request merged=true`. Same before and after the run; same as B0 and section 0. Not toggled (UI only; not this batch's).

## Pre-state of R1 (reads only, `pre.jsonl`: 9 reads, 0 writes)

`main` HEAD `faa2f59` (merge of PR #239, B4), top-level `.github README.md auto-note.md dod l_b2 l_b3 l_b4 specs spike-note.md t26`, `.github/workflows` = `ghp-spike-bot.yml` only. No `ghp.config.json`, no `ghp-t31.yml`. Newest issue/PR number 239.

## L-059 the live suite at v0.1.0 — FAIL against the plan's Expected (no product regression found; environment changes and one harness limit; see findings)

Command (writes as praveenlavu to R1 and Project 3, in the background, `$LIVE/work/b7`):
`GHP_REQ_LOG=$LIVE/log/b7/l059.jsonl GHP_LIVE=1 GHP_LIVE_ACCOUNT=praveenlavu GH_TOKEN=$(gh auth token -u praveenlavu) GHP_T26_RECORD=$LIVE/evidence/t26.json .venv/bin/python -m pytest tests/live -q -rxX -rf`

**Summary line: `11 failed, 29 passed, 8 errors in 1110.61s (0:18:30)`, exit 1.** No xfail, no xpass. Request log: 857 api calls, 707 reads, 150 writes.

| Test (48 collected) | Result | Cause |
|---|---|---|
| `test_write_path.py` (8 tests: AC1 x2, T14a, AC2, AC3, T17, T18, AC4) | 8 passed | |
| `test_pr_done.py::test_ac3_done_closes_an_open_story_and_the_item_reaches_done` | passed | |
| `test_pr_done.py::test_ac5_merge_into_a_non_default_branch_ignores_closes_and_done_closes` (T26a) | passed | passes since T26f, as expected |
| `test_pr_done.py::test_t26f_done_pr_refusals`, the 3 probes (`pr_plumbing`, `closing_forms`, `commit_closer`), `test_probe_workflows_shape` | 5 passed | |
| `test_read_path.py`: `input_dash...`, 14 x `test_query_names_exist_in_the_live_schema[...]` | 15 passed | |
| `test_pr_done.py::test_ac4_board_drag_to_done_closes_the_story_without_the_gate` | FAILED `#245 is still open 60 s after its item was set to Done` | **environment** (Auto-close issue off), as the plan expected |
| `test_pr_done.py::test_ac1_branch_pr_create_merge_closes_with_closer_pull_request_and_item_done` | FAILED `the item of #241 is not at Done 60 s after the close` | **environment** (Item closed off); not in the plan's Expected |
| `test_pr_done.py::test_ac2_done_after_the_merge_posts_the_ledger_comment_once` | FAILED writes = comment + `updateProjectV2ItemFieldValue` (test expects the comment only) | **environment** (Item closed off: the item was still In Progress, so `done` set Done itself, as section 0 predicts); not in the plan's Expected |
| `test_pr_done.py::test_t26f_done_pr_after_a_default_branch_merge_posts_only_the_ledger_comment` | FAILED closer = PR #249 (correct), `at_done` False | **environment** (Item closed off); not in the plan's Expected |
| `test_read_path.py::test_project_show` | FAILED `all(w.enabled for w in flows)` False | **environment**: the test pins "all six enabled on Project 3"; two are off now; not in the plan's Expected |
| `test_approver_vs_bot.py::test_ac1_as_written` | FAILED run `43338302540` conclusion `failure` | **environment**: the job never started. Annotation: the job was not started (an account-side limit) (Actions billing, as T42 once recorded) |
| `test_approver_vs_bot.py`: `test_ac1_chain_as_the_bot`, `test_ac3_approver_edit_stops_counting`, `test_ac3_writer_rewrite_of_the_bot_comment`, `test_unverified_shapes` | 4 FAILED `AttributeError` (`q_hash`, `approvals`, `bot_approve`, `q_comment`) | cascade of the billing refusal (each needs state the first test sets) |
| `test_approver_vs_bot.py::test_ac2_owner_token_check_exits_2` | FAILED `approvals check --gate wait-po-validation` exit 0, test expects 2 | cascade: no decisions comment was ever posted (the bot chain never ran). `approvals._wait_validation` returns "nothing open" when no comment carries the decisions marker (`if not hits: return [], 0, {}`), so 0 is the coded answer for that state. The `review-spec` half of the same test gave the expected 2. Not a product regression; see note N2 |
| `test_fixture_shapes.py::test_fixture_keys_match_live` and 7 `test_read_path.py` tests (`get_story_and_its_sub_issue`, `a_body_written...`, `get_refuses_a_pull_request`, `comments`, `dod_and_rules`, `status_events_and_project_item_of_the_story`, `every_query_in_the_code_is_valid_live`) | 8 ERROR at setup: `expected exactly one issue labelled ghp:live-test titled 'Live story: opening hours (fictional)', found []` | **harness limit** (finding B7-F1): the `scratch` fixture reads one page of 100 `ghp:live-test` issues, newest first; R1 now has 130 and the T13 story #19 is the 130th |

**Product behaviour versus the switch state (checked, consistent):**
- AC4 drag: #245's item went `'' -> Done` by `praveenlavu` (the test's raw write) and the issue stayed open (`t26.json` `ac4.state ["open", null]`, `workflows_enabled [["Auto-close issue", false], ["Item closed", false]]`). With Auto-close off GitHub does not close it. No `ghp` write involved; the gate was not bypassed.
- AC1/AC2 on #241: the merge of PR #242 closed #241 (Closes keyword, as before); the item stayed In Progress (Item closed off). `ghp done 241 --apply` then posted the ledger comment and set Done itself. Status history of #241: `github-project-automation '' -> Todo`, `github-project-automation Todo -> In Progress`, `praveenlavu In Progress -> Done`. The `done` plan states this step (#243's plan in `t26.json`: "Step 3: wait up to 30 s (reads only) for the Project's own workflow to move the item to Done, then set Status to Done if it is still not there / Status: In Progress -> Done"); the apply did not close the issue again (`"closed #" not in out` held).
- AC5 / T26f `done --pr 247` on #246: writes comment, `PATCH issues/246` (close), `updateProjectV2ItemFieldValue`; result "Status is Done (set by ghp)"; the re-run sent nothing ("already done").
- So `ghp done` setting Done itself, the path never run live before (section 0, L-033/L-034), now ran three times and matched its plan.

**Supplementary read-only re-run (characterisation, not the as-written result).** To still cover the 8 blocked tests, `tests/live/conftest.py` in the disposable clone got one local, uncommitted change: `&direction=asc` on the fixture's issue list (the oldest 100 first). `pytest tests/live/test_read_path.py tests/live/test_fixture_shapes.py -q` -> **`1 failed, 22 passed in 33.86s`**: all 8 earlier errors pass, the one failure is `test_project_show` (switch state, as above); `fixture_only=[]` for `issue_story.json`, `issue_task.json`, `issue_pull_request.json` (the T13 to T31 fixture shapes still match). Request log `l059-readpatch.jsonl`: 56 reads, 0 writes. The change was reverted afterwards (`git checkout`; `git status` clean).

**Not covered by this run:** the T31 approver-versus-bot chain (6 tests) did not run at all because Actions refused the job. It needs a re-run once Actions can start jobs on that repository (or on a public one).

### `t26.json` record (summary)

- `issues_created` 24: 241, 243, 245, 246, 248, 250, 251, 256 to 271, 288. `prs_created` 24: 242, 244, 247, 249, 252 to 255, 272 to 287.
- `ac1` #241 / PR #242 (`Pickup slots #241`, body `Closes #241`); `ac2_dry_run` 8 calls, 0 writes; `ac3` #243 / PR #244, `done_after_merge_close_s null` (Item closed off), reopen moved the item out of Done in 0.5 s (`In Progress`); `ac4` as above; `ac5` #246 on side base, plan warning "is not the default branch ... `ghp done` will close it", not closed by the merge, `done` without `--pr` exit 2 `E_NOT_MERGED`, with `--pr 247` exit 0 (3 writes), second run 0 writes.
- `t26f_default` #248 / PR #249: closed event `COMPLETED`, closer `PullRequest #249`, merged into `main`; `t26f_refusals` #250/#251: `E_PR_NOT_LINKED` for a mention-only PR (#253), exit 2, 0 writes.
- `pr_list_lag`: 4 PRs, each listed on the first poll (0.48 to 0.65 s). `forms` f01..f16 recorded as before. `commit_closer` #288 closed in 3.0 s by commit `5f0cded` on `main`.
- `workflows.raw`: 6 nodes, the two closing workflows `enabled: false`.
- `cleanup_prs_closed` [252, 254, 255]; `cleanup_branches_deleted` 22 (every pushed branch); `cleanup_issues_closed_not_planned` [245, 250, 251, 257, 261, 263, 264, 265, 271]. The others were closed by their merges or by `done`.

## L-060 install route on Spec Kit 1.4.0 — PASS

New empty folder `$LIVE/work/b7-l060b` (`ls -A` count 0). Spec Kit `uvx --from git+https://github.com/github/spec-kit@v1.4.0 specify` (`CLI Version 1.4.0`). Release file `ghp-0.1.0.zip` (2061 bytes) downloaded from the v0.1.0 release into its own folder `$LIVE/work/b7-dl`; `shasum -a 256 -c SHA256SUMS` -> `ghp-0.1.0.zip: OK`.

| Step | Exit | Output |
|---|---|---|
| `specify init --here --force --non-interactive --integration claude --script sh` | 0 | |
| `specify extension catalog add https://raw.githubusercontent.com/praveenlavu/spec-kit-ghp/main/catalog/extensions.json --name ghp --install-allowed` | 0 | |
| `specify workflow catalog add https://raw.githubusercontent.com/praveenlavu/spec-kit-ghp/main/catalog/workflows.json --name ghp` | 0 | |
| `specify bundle install $LIVE/work/b7-dl/ghp-0.1.0.zip` | 0 | `✓ Installed 'ghp' (2 added, 0 already present).` |
| `specify extension list` | 0 | `ghp` / `Commands: 13 \| Hooks: 2 \| Priority: 10 \| Status: Enabled` |
| `specify workflow list` | 0 | `GitHub issue lifecycle (ghp-lifecycle) v0.1.0` |

GitHub writes: 0 (`l060.jsonl`: 0 api calls through `gh`; the one other `gh` process is the release download; Spec Kit's own fetches of the raw catalog files and of its source are anonymous HTTPS GETs that the `gh` wrapper does not see). A first attempt in `$LIVE/work/b7-l060` did not run (`zsh` did not split the `specify` command held in a variable, exit 127, the folder stayed empty); harness slip, not a finding.

## Cleanup read-back (after the run; `post.jsonl`: 21 reads, 0 writes)

| Check | Result |
|---|---|
| Open `ghp:live-test` issues | `[196,195]` = the T34 objects, unchanged. Every issue the run created is closed (e.g. #240 and #245 `not_planned`; #241, #243, #248, #288 `completed`). |
| Open PRs | `[204]` (T34), unchanged |
| Branches | `feature/gh196-issue ghp-spike/auto ghp-spike/link main`, same as before: no stray branch |
| R1 `main` files | `.github README.md auto-note.md dod l_b2 l_b3 l_b4 specs spike-note.md t26`; `.github/workflows` = `ghp-spike-bot.yml` only. No `ghp.config.json`, no `ghp-t31.yml` |
| `main` history of the temporary files | `8cd7e41` add the throwaway workflow and trust config (00:35:38Z), `e07f353` remove them (00:36:57Z), both by the suite's teardown |
| Actions workflows | `ghp-spike-bot` active only |
| `main` HEAD | `5f0cded` "T26 direct commit probe (fictional)" plus the T26 probe merges #272 to #287: fixture commits, expected, like the earlier batches' |
| Project 3 switches | unchanged (above) |

Left on purpose (policy: never delete issues, items, labels; comments stay as evidence): the 25 closed issues (#240 T31 story and the 24 above), their Project items (e.g. #245's item at Done on a closed issue), the closed and merged PRs, the comments, the `main` history and the failed workflow run record `43338302540`.

## Expected versus actual

| Case | Expected | Actual | Verdict |
|---|---|---|---|
| L-059 | all pass, no xfail, except `test_ac4_board_drag...` (Auto-close off); T26a passes; T31a passes; `fixture_only=[]` | 29 passed, 11 failed, 8 errors, no xfail. T26a passes. ac4 fails for the expected reason. 4 more T26 tests and `test_project_show` fail because Item closed and Auto-close are off (the tests pin the old automation). The 6 T31 tests fail because Actions refused the job (billing). 8 setup errors from the fixture's 100-issue page; with the page fixed locally they pass and `fixture_only=[]` | **FAIL** (environment changes + harness limit; no product regression observed) |
| L-060 | "2 added", 13 commands, 2 hooks, 0 GitHub writes | exactly that | **PASS** |

## Findings

No severity-1 finding: every `ghp` write in the T26 tests matched its plan; no write went to another repository or Project; no token anywhere; only `praveenlavu` was used; no approval counted when it should not (the T31 chain did not run).

- **B7-F1 (severity 3, test harness; not product).** `tests/live/conftest.py` fixture `scratch` lists `ghp:live-test` issues with one `per_page=100` page, newest first. R1 now holds 130 such issues (the live batches add fixtures), so the T13 story #19 is not found and 8 read-path tests error at setup. `&direction=asc` (or pagination) fixes it; with that one change the 8 pass. The suite cannot pass as released on today's R1.
- **B7-F2 (severity 2 against readiness criterion 2; plan expectation, environment).** The plan says the board-drag test is the only allowed difference. With Item closed and Auto-close off, 4 more T26 tests (`test_ac1_branch_pr_create_merge...`, `test_ac2_done_after_the_merge...`, `test_t26f_done_pr_after_a_default_branch_merge...`) and `test_read_path.py::test_project_show` also fail, because they assert the old switch state. The product's behaviour in each matched the switch state (`done` set Done itself, as planned). Owner choice: switch Auto-close and Item closed on for one re-run (UI only), or amend criterion 2 / the tests to accept both states.
- **B7-F3 (severity 2 for readiness; environment, not product).** Actions on R1 refused the T31 workflow job for an account-side reason. The T31 regression (6 tests) did not run. Re-run `tests/live/test_approver_vs_bot.py` once Actions minutes are available (or R1 public).
- **B7-N1 (note, docs, severity 3).** `bundle/README.md` line 3 still says "**Not published yet.** No release exists: the `v0.1.0` tag, the release files and the catalog addresses below do not exist", at the tag and on `main`, although the release, its files and the catalog addresses now exist and installed in L-060. `docs/catalog-submission.md` line 395 has a similar "No release exists" sentence (seen at the tag only).
- **B7-N2 (note, design, UNVERIFIED as intended).** `approvals check --gate wait-po-validation` is met (exit 0) when no decisions comment was ever posted, even though the branch's decisions file holds an open D1: the file can only keep a posted id open (`_wait_validation`). In the lifecycle the decisions post and its approval gate come first, so this is low risk; recorded for the owner to confirm it is intended.

Harness notes (no finding): pip 21.2 in the venv needed an upgrade for the editable install; the suite took 1110 s (T31: 674 s), most of it the bounded 60 s waits for Done/close that no automation now performs.
