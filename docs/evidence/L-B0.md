# L-B0 evidence: preflight and read-only, released ghp 0.1.0 against R1

Run on 2026-11-12 (UTC ~23:10 to 23:30; stamp in `$LIVE/stamp`) by the B0 live-test agent. All data is fictional. Plan: `docs/plans/PLAN-LIVE-0.1.md` batch B0 (L-001..L-014).

- Account: `praveenlavu` only (`GET /user` -> `praveenlavu 22502189`). Every `gh`/`ghp` command ran with the per-command prefix
  `GH_TOKEN=$(gh auth token -u praveenlavu) `. `gh auth switch`, `refresh` and `login` were not run. Only the owner's own account was used. No token appears in any log, output or this file (token-shape scan of `$LIVE/log` and `$LIVE/work` outputs: 0 hits).
- Target: R1 = private repo `praveenlavu/ghp-spike-scratch` (`main`) and user Project 3 (`PVT_kwHOAVdbLc4Bqvdw`). Terminal approval mode.
  The only repository paths in the request log are `repos/praveenlavu/ghp-spike-scratch/...`, `user` and `graphql`. `ghp-live-comment` (B5) and
  `ghp-consumer-scratch` (T39) were not touched (L-006's foreign-repo case was refused locally with 0 requests).
- **Writes: none.** Every case's request log shows `writes=0` (54 logs; table at the end). B0 wrote nothing to GitHub.
- Product under test: the RELEASED 0.1.0, `pip install "spec-kit-ghp @ git+https://github.com/praveenlavu/spec-kit-ghp.git@v0.1.0"` into `$LIVE/venv`
  (resolved commit `cefd9fa4ca0ebefe7790fd9669b87fa683cd420a`, `ghp 0.1.0`). Python 3.9.6, gh 2.96.0. No product code was changed.

`$LIVE = <scratch>/live`

## Harness (built in B0, scratchpad only)

| File | What |
|---|---|
| `$LIVE/env.sh` | `LIVE`, `GHP_REAL_GH=<path to gh>` (saved before PATH changes), `PATH=$LIVE/bin:$LIVE/venv/bin:$PATH`. No token. |
| `$LIVE/bin/gh` + `bin/reqlog.py` | Request log. One JSON line per `gh` process to `$GHP_REQ_LOG`: time, argv (`-f/-F` values redacted), method (`-X`/`--method`, else `POST` when a body is given, else `GET`), path, `graphql_write` (`ghp.gh.is_graphql_write` on the stdin body for `--input -`), mutation names, `query_head` (first 120 chars of the package-built query text; variables never logged), `write`. `gh auth token` is logged as `{"auth_token": true}` only. stdin is passed through, stdout untouched. Defaults are fixed inside the wrapper because `ghp doctor` scrubs the environment for its `gh --version` probe (first doctor run showed `gh unknown` for that harness reason; re-run after the fix, see L-003). |
| `$LIVE/bin/writes.py <log>` | Prints `api_calls / reads / writes / auth_token` and lists writes. Write = method not GET/HEAD, except a `graphql` POST whose body is a query (`graphql_write` false) counts as a read; `graphql_write` true is always a write. Self-checked offline with a fake real gh: a mutation body, a PATCH and a `-f` POST counted as writes, a GraphQL query and a GET as reads. |
| `$LIVE/bin/fx.py` | Fixture/probe tool with every subcommand of section 0 (bodies via stdin JSON, never argv). Its write subcommands were NOT exercised in B0 (read-only batch); first use is B1. |
| `$LIVE/work/b0` | Clone of R1 via the per-command credential helper (`git credential fill` showed `username=praveenlavu` only). `ghp.config.json` (R1 config) and `.ghp/` listed in `.git/info/exclude`. `origin/HEAD -> origin/main`. L-010..L-014 ran on local branch `feature/gh196-issue` (from `origin/feature/gh196-issue`, head `fa15b15`). git clone/fetch traffic is not in the gh request log (git talks HTTPS itself); it is read-only. |

## Cases

### L-001 guard and target — PASS
- `G gh api user --jq ...` -> `praveenlavu 22502189`, exit 0.
- `G gh api repos/praveenlavu/ghp-spike-scratch --jq '[.private,.default_branch]'` -> `[true,"main"]`, exit 0.
- `G gh api graphql --input - < wf.json` (viewer `user(login:"praveenlavu").projectV2(number:3).workflows`) -> `Auto-add sub-issues to project=true`, `Auto-close issue=false`, `Item added to project=true`, `Item closed=false`, `Pull request linked to issue=true`, `Pull request merged=true`, exit 0. Same as section 0: no change.
- Log: reads 3, writes 0.

### L-002 released CLI installs and reports its surface — PASS
- `ghp --version` -> `ghp 0.1.0`, exit 0 (`command -v ghp` = `$LIVE/venv/bin/ghp`).
- `ghp --help`, exit 0: verbs exactly `get comments project stack dod rules questions decisions push activate branch pr gate calibrate kits strict done status approvals doctor setup` (21). Epilog holds the comment-approval limits text ("Approval by comment is tamper-evident, not tamper-proof ... the earlier /approve counts again.").
- `ghp push --help`, exit 0: shows `--apply`, `--plan-out FILE`, `--expect-plan FILE`, `--workflow`.
- Log: 0 api calls.

### L-003 doctor on the real setup after the Project toggle — PASS
- `G ghp doctor --json > d.json` (in `work/b0`, branch `main`), exit 0. Rows:
  `git/specify/shallow pass`, `gh pass gh 2.96.0`, `identity pass signed in as user id 22502189 (praveenlavu)`, `scopes pass the token has the scopes repo and project`,
  `config pass`, `config-target warn the target branch has no ghp.config.json yet ...`, `extension warn`, `workflow warn` (not installed; CLI works without),
  `approvers pass`, `writers pass`, `project-workflows warn Other enabled workflows add or move cards on their own: 'Auto-add sub-issues to project', 'Item added to project', 'Pull request linked to issue', 'Pull request merged'. Check ... that none of them sets Done`,
  `protection warn branch protection is not available on a private repository of a free GitHub account ...`, `overlap pass`, `stack warn`, `kits pass`.
- `Auto-close issue` and `Item closed` are absent from the warning: per `doctor.py` (lines 353-354) each is named only when enabled, so their absence is the "off" reading. This settles the UNVERIFIED item: the workflows list after the UI toggle is read correctly.
- `protection` is the mapped form of the 403 `Upgrade to GitHub Pro` answer (doctor header: matched case-insensitively). No row `fail`, so exit 0 is right.
- `G ghp doctor --offline --json`, exit 0: `identity`, `scopes`, `project-workflows`, `protection` = `unknown (offline)`; local rows unchanged.
- Log: online reads 4, writes 0; offline 0 api calls.

### L-004 project show after the toggle — PASS
- `G ghp project show`, exit 0: `Todo f75ad846 <- status_todo`, `In Progress 47fc9ee4 <- status_active`, `Done 98236657 <- status_done`; workflows `#1 Item closed disabled`, `#2 Pull request merged enabled`, `#3 Auto-close issue disabled`, `#4 Auto-add sub-issues to project enabled`, `#5 Pull request linked to issue enabled`, `#6 Item added to project enabled`.
- Log: reads 2, writes 0.

### L-005 a PR number is refused by every issue verb — FAIL (finding B0-F1)
| Command | Exit | Output |
|---|---|---|
| `G ghp get 204` | 2 | `E_NOT_AN_ISSUE: praveenlavu/ghp-spike-scratch#204 is a pull request, not an issue` |
| `G ghp dod 204` | 2 | `E_NOT_AN_ISSUE ...` |
| `G ghp push 204` | 2 | `no --from given and the ledger dod/DoD_204.md has no readable spec dir; pass --from PATH or run \`ghp dod 204 --spec-dir DIR\`` (**no E_NOT_AN_ISSUE**) |
| `G ghp done 204` | 2 | `E_NO_LEDGER: cannot read the ledger dod/DoD_204.md ...; run \`ghp dod 204\` first` (named refusal; allowed by Expected) |
| `G ghp activate 204` | 2 | `E_NOT_AN_ISSUE ...` |
- `dod/DoD_204.md` not created; `git status` clean. Log: reads 3, writes 0.
- Diagnostic (read-only, after the deviation): `G ghp push 204 --from .ghp/probe/tasks.md` (copy of T34's tasks.md inside the clone) -> exit 2 `E_NOT_AN_ISSUE`, 1 read, 0 writes. So `push` does refuse a PR once it reaches GitHub; bare `push 204` stops earlier on the missing ledger and its advice (`ghp dod 204 --spec-dir`) leads to a verb that then says it is a PR. (A `--from` outside the repository is refused: `cannot read ... not a plain file inside the repository`, 0 requests.)

### L-006 ref forms — PASS
- `#196`, `praveenlavu/ghp-spike-scratch#196`, `https://github.com/praveenlavu/ghp-spike-scratch/issues/196`: exit 0 each, identical JSON (sha1 `9cb01bc9...` x3), 1 read each.
- `.../pull/204` -> exit 2 `E_PULL_REQUEST_URL: '...pull/204' is a pull request, not an issue`, 0 api calls.
- `praveenlavu/ghp-consumer-scratch#1` -> exit 2 `E_FOREIGN_REPO: praveenlavu/ghp-consumer-scratch is not praveenlavu/ghp-spike-scratch`, 0 api calls.

### L-007 issue that does not exist — PASS
- `G ghp get 999999` -> exit 1, stdout empty, stderr one line `error: E_GH_HTTP (HTTP 404): Not Found`, token-shape scan 0. Log: 1 read (no retry), writes 0.

### L-008 config refusals cost no request; exit 3 where mapped — FAIL (findings B0-F2..B0-F5; every exit code and every 0-request expectation held)
Each in its own `git init` folder under `$LIVE/work/l008/`.
| Sub | Command | Exit | Output (abbrev.) | api calls |
|---|---|---|---|---|
| a no config | `ghp get 196` | 2 | `No ghp config found. Run \`ghp setup\` ...` | 0 |
| b `gh_token` | `ghp get 196` | 2 | `...ghp.config.json: top-level entry #3: token settings are not supported (ghp never reads a token; gh does its own auth)`. Value not shown; **key not named either** (B0-F4) | 0 |
| c org owner type | `get 196` | 2 | `E_ORG_PROJECT_UNSUPPORTED: 'project_owner_type' must be "user" ...` | 0 |
| c | `gate 196` / `strict check 196 --phase implement` / `done 196` / `branch --id-from feature/gh196-x` | 3 / 3 / 3 / 3 | `E_ORG_PROJECT_UNSUPPORTED ...` | 0 each |
| c | `status 196` (and `--json`) | 3 | `Issue #196: waiting on stage config. Next: ghp doctor / Why: ghp.config.json is invalid`; **does not name E_ORG_PROJECT_UNSUPPORTED** (B0-F2) | 0 |
| d `dod_gate` | `ghp get 196` | 2 | `` `dod_gate` was renamed to `gate` in 0.1.1; move `dod_gate.block` to `gate.block` `` — **names a version 0.1.1 that does not exist; the release is 0.1.0** (B0-F3) | 0 |
| e `GHP_CONFIG=other.json` with `approvers` | `ghp get 196` | 0 | **not refused**, issue JSON (B0-F5) | 1 read |
| e (diagnostic, in `work/b0` with origin/HEAD, `GHP_CONFIG=$LIVE/work/l008/e/other.json`) | `approvals check 196 --gate review-spec` | 2 | `the config file in use is not the repo-root ghp.config.json and sets approvers, approval or unattended: those keys are read only from ghp.config.json on the target branch; move them there` (E_NONROOT_TRUST text) | 0 |
| e (same) | `doctor --offline` | 1 | `fail approvers` with the same text | 0 |
| e (in `l008/e`, no origin) | `approvals check` / `doctor --offline` | 2 / 1 | `... no target branch to read them from (no origin/HEAD); nothing is approved` | 0 |
- Writes 0 in every log.

### L-009 a Project that is not the user's — PASS
- `project_number: 999` -> `G ghp project show` exit 1 `E_GH_GRAPHQL (HTTP 200): Could not resolve to a ProjectV2 with the number 999.` 1 read.
- `project_owner: "github"`, `project_number: 1` -> exit 1 `E_GH_GRAPHQL (HTTP 200): Could not resolve to a User with the login of 'github'.` 1 read.
- Named GitHub-error mapping, exit 1 (the plan's "1 for a GitHub error"). Writes 0. The org Project is never reached because the query is user-scoped.

### L-010 Status option renamed (via config) — PASS
- `status_active: "Doing"` -> `G ghp activate 196` exit 2 `E_STATUS_OPTION_MISSING: Status option 'Doing' (status_active) not found; options found: 'Todo', 'In Progress', 'Done'`. 2 reads, 0 writes.
- `status_todo: "Backlog"` -> `G ghp push 196` exit 2 `E_STATUS_OPTION_MISSING: Status option 'Backlog' (status_todo) not found; ...`. 2 reads, 0 writes.
- Config restored to the R1 config afterwards; Project options were not touched.

### L-011 status on the T34 story — PASS (settles two UNVERIFIED items)
- `G ghp status 196` exit 0: `waiting on stage merged. Next: git fetch origin`, `Sub-issues: 0 of 7 closed`, `Board column: In Progress`.
- `--json` exit 0: `stage merged`, `next "git fetch origin"`, `board_checked true`, `progress {total 7, completed 0}`, `column In Progress`, `on_board true`.
- `--offline` exit 0: `board: not checked (offline): open-at-done, questions-posted, board-tasks, board-sync, decisions-posted, pr-link`, 0 api calls.
- `--catch-up` exit 0: same plus `Catch-up: nothing to catch up.`
- Online runs: 29 reads each (22 GraphQL, 7 REST), 0 writes.
- GraphQL read `closedByPullRequestsReferences(first:5,includeClosedPrs:false)` + `subIssuesSummary` -> `[{"number":204,"state":"OPEN"}]`, `{"total":7,"completed":0,"percentCompleted":0}`. An OPEN PR is listed in closing references; the sub-issue summary is present in GraphQL.

### L-012 terminal-mode approvals make no request — PASS
- `--gate review-spec` -> exit 0, `"mode": "terminal"`, `"met": true`, note `... nothing was read`. 0 api calls.
- `--gate review-everything` -> exit 2, `unknown gate: --gate must be one of the gates of the ghp-lifecycle workflow (docs/trust-and-limits.md)` (the `E_GATE` constant). 0 api calls.
- `--gate approve-push` -> exit 0, terminal mode, `met: true` (recorded: terminal mode returns before the gate kind is checked, as the plan predicted). 0 api calls.
- `cp -R <an earlier test project> $LIVE/work/b0-run`; there `G ghp approvals sync 987e533b --dry-run` -> exit 2 `approvals sync works in comment mode only ... nothing was read or resumed` (E_SYNC_TERMINAL). 0 api calls. The source folder was only read.

### L-013 read-local verbs on the T34 story — PASS
- `G ghp comments 196` exit 0: 3 marked comments (`questions` x2, `decisions` x1), each `author 22502189 (User)`, `edits 0`; wrote `.ghp/context/196.md`. 2 reads.
- `G ghp rules 196` exit 0: `from []`, `problems []`. 1 read.
- `G ghp gate 196 --src . --base origin/main --summary-out g.md` exit 0: AC-1, AC-2 mapped, `drift {evaluated true, drift false}`, advisory open decisions D1..D8, `blocking false`. 1 read.
- `ghp gate 196 --offline` exit 0: `drift {evaluated false, note "offline — drift not checked"}`. 0 api calls. Writes 0 throughout.

### L-014 strict check reads the board live — PASS
Working-tree config with `"mode":"strict"` added (restored afterwards).
- `ghp strict mode` exit 0: `{"mode":"strict","source":"pull request"}` (main has no config, so the working tree is the source). 0 api calls.
- `strict check 196 --phase implement` exit 2: findings `test-command` (strict.test_command not set), `stack-profile-missing`. 20 reads, 0 writes; the log shows the Project read (`user(login).projectV2(number)` x1) and per-sub-issue `projectItems` + `fieldValueByName("Status")` reads (7 each). No board finding.
- `--phase pr --base origin/main` exit 2: `evidence-missing` (AC-1, AC-2), `record-missing`, `fidelity-missing`, `stack-profile-missing`; warning `trust-introducing PR`. 0 api calls.
- `--phase done --base origin/main` exit 2: the pr findings plus `decision-open` (D1..D8). 0 api calls.

## Request-log counts (all writes 0)

| Case | api calls (reads) | writes |
|---|---|---|
| L-001 | 3 | 0 |
| L-002 | 0 | 0 |
| L-003 online / offline | 4 / 0 | 0 |
| L-004 | 2 | 0 |
| L-005 (+ diagnostic) | 3 (+1) | 0 |
| L-006 x5 | 1,1,1,0,0 | 0 |
| L-007 | 1 | 0 |
| L-008 (20 logs) | 0 each, except e-get 1 (+ diagnostic e2-get 1) | 0 |
| L-009 a / b | 1 / 1 | 0 |
| L-010 activate / push | 2 / 2 | 0 |
| L-011 text / json / offline / catch-up / gql | 29 / 29 / 0 / 29 / 1 | 0 |
| L-012 x4 | 0 | 0 |
| L-013 comments / rules / gate / gate-offline | 2 / 1 / 1 / 0 | 0 |
| L-014 mode / impl / pr / done | 0 / 20 / 0 / 0 | 0 |
| clone (git helper only) | 0 | 0 |

## Created objects and cleanup

| Object | Where | Cleanup |
|---|---|---|
| none on GitHub | — | nothing to clean (0 writes) |
| `$LIVE/venv`, `$LIVE/bin/*`, `$LIVE/env.sh` | scratchpad | kept: harness for B1..B8 |
| `$LIVE/work/b0` clone (local branch `feature/gh196-issue`, untracked `.ghp/context/196.md`, config restored to R1 config) | scratchpad | kept; nothing pushed |
| `$LIVE/work/b0-run` copy of the T34 run folder | scratchpad | kept; source folder untouched |
| `$LIVE/work/l008`, `l009` temp config folders | scratchpad | kept as evidence |
| `$LIVE/log/*.jsonl` (54 logs) | scratchpad | kept as evidence |

## Findings

Severity: 1 = unsafe (write without apply, wrong target, token shown, approval counted wrongly, another account used); 2 = documented flow blocked or wrong, wrong exit/error code against the docs; 3 = minor (message, ordering, or a plan expectation that does not match the product's stated design).
No severity-1 finding: 0 writes in every case, no token in any output, only `praveenlavu` used.

- **B0-F1 (sev 3) `ghp push <PR number>` without `--from` refuses on the missing ledger, not with E_NOT_AN_ISSUE.** Exit 2 and nothing written, so safe, but the advice (`ghp dod 204 --spec-dir DIR`) leads to a verb that then says it is a PR. With `--from` it does refuse `E_NOT_AN_ISSUE`. Same ordering for `done 204` (E_NO_LEDGER), which the plan allowed. Candidate: check the ref kind before the ledger, or note it in the plan.
- **B0-F2 (sev 3) `ghp status` on an org-owned Project config says only "ghp.config.json is invalid"**, exit 3, without the code `E_ORG_PROJECT_UNSUPPORTED` that every other verb names. The next step (`ghp doctor`) would show it.
- **B0-F3 (sev 3) the `dod_gate` refusal says it "was renamed to `gate` in 0.1.1"**, but the released version is 0.1.0 and no 0.1.1 exists (`src/ghp/config.py:164`). The text names a release that is not there.
- **B0-F4 (sev 3, plan expectation) the token-key refusal does not name the key.** The plan says "names the key, never the value". The product is designed not to echo the key (`tests/test_config.py::test_token_settings_are_refused_without_echoing_the_key`) and says `top-level entry #3`. The product matches its own test; the plan's wording should be corrected (or the owner decides the key should be named).
- **B0-F5 (sev 3, plan scope) a non-root `GHP_CONFIG` file that sets `approvers` is not refused by `ghp get`** (exit 0, 1 read). The refusal (E_NONROOT_TRUST text) happens only in verbs that read trust keys (`approvals check` exit 2, `doctor` `fail approvers` exit 1), as `config.trusted()` documents. Nothing is trusted from that file, so it is safe. The plan's L-008(e) step should name a trust-reading verb.

Harness note (not a product finding): the first `ghp doctor` run showed `gh unknown` because doctor scrubs the environment and the wrapper then lost `GHP_REAL_GH`; the wrapper now has fixed defaults and the re-run shows `gh pass gh 2.96.0`.
