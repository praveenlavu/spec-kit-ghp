# ghp-lifecycle workflow

Runs one GitHub story issue from spec to an open pull request with the
[`ghp` Spec Kit extension](../../README.md): pull the story, plan, tasks, push sub-issues,
implement, gate, review, open the PR. Every GitHub write is a preview, a human gate, then an
apply. The run ends at the open PR; the story closes after the merge, with `speckit.ghp.done`
(never inside the run).

It is a port of spec-kit-ado's `ado-lifecycle` workflow, with the same steps and gates. The differences are listed at the end.

## Install and run

The workflow is not part of the extension package: install it on its own, from a checkout of this repository.

```bash
specify workflow add --dev <path-to-spec-kit-ghp>/workflows/ghp-lifecycle
specify workflow run ghp-lifecycle -i issue_number=42
```

Before the first run, `origin/HEAD` must be set (`git remote set-head origin -a`). The first step, `base-check`, stops the
run when it is not: `ghp` reads who may approve from the branch `origin/HEAD` points at, and the gate uses it as its base.
In CI, `actions/checkout` sets no `origin/HEAD`: fetch the default branch and run `git remote set-head origin <default>`
first (see [docs/trust-and-limits.md](../../docs/trust-and-limits.md)).

## Inputs

| Input | Type | Default | Meaning |
|---|---|---|---|
| `issue_number` | number | required | The story's issue number. It is the only value put into a shell command: each step that uses it checks first that it is a positive whole number and stops (exit 2) otherwise: the `number` type (checked on `run --input` and `resume --input`) still lets negatives and floats through. A value edited into the run's `state.json` is not re-checked by Spec Kit; the guard is no boundary against that, as whoever can edit that file can edit the installed workflow too. |
| `integration` | string | `auto` | The integration every command step runs on; `auto` uses the one the project was initialized with. |
| `review_model` | string | empty | The model of the `review` step only (empty = the integration's default). |
| `verdict_<gate>` (12 inputs) | string: `""`, `approve` or `reject` | empty | One per gate, with underscores: `verdict_review_spec`, `verdict_approve_questions`, `verdict_approve_plan_questions`, `verdict_wait_po_answers`, `verdict_approve_answers_questions`, `verdict_review_plan`, `verdict_approve_push`, `verdict_approve_decisions`, `verdict_approve_sync`, `verdict_review_review`, `verdict_approve_pr`, `verdict_wait_po_validation`. Empty leaves the gate as it is: it asks at a terminal, and without one it pauses. A non-empty one passes or rejects the gate without asking. **Anyone who can run `specify` can set them** (`specify workflow run ... -i verdict_<gate>=approve`, `specify workflow resume <run> --input ...`, or by editing the run's `state.json`), so a verdict is never the approval: see "Where approval is enforced". The kit's own poller, `ghp approvals sync <run> --resume`, sets them in comment mode. |

## Steps and gates

Command steps run the agent once with `--workflow`, so they make no GitHub writes and never stop to ask: open questions
and decisions go to the ledger. Approval happens only at gate steps; rejecting any gate aborts the run.

| Step | What happens | Gate |
|---|---|---|
| `base-check` | Stops the run (exit 2) unless `origin/HEAD` is set | |
| `pull` | Story to spec and ledger | |
| `stack-check` | Strict mode: `ghp stack show --check` stops the run unless the stack profile is approved; advisory: exit 0 | `review-spec` |
| `check-review-spec` | `ghp approvals check <n> --gate review-spec` (a no-op in terminal mode) | |
| `questions-loop` | Preview, then post the open questions to the issue | `approve-questions` |
| `plan` | Plan phase | |
| `plan-questions-loop` | Preview, then post the questions the plan raised (a changed set is a new comment; no comment is edited) | `approve-plan-questions` |
| `wait-po-answers` | Where you wait for the product owner's replies | `wait-po-answers` |
| `check-wait-po-answers`, `sync-po-answers` | Comment mode: the gate's check, then `ghp questions <n> --sync --if-comment-mode` writes the answers into the ledger; terminal mode: no-ops | |
| `plan-with-answers` | Plan phase again with `--close-assumed` | |
| `answers-questions-loop` | Preview, then post any question the revised plan raised | `approve-answers-questions`, then `review-plan` |
| `check-review-plan` | `ghp approvals check <n> --gate review-plan` | |
| `tasks` | Tasks phase | |
| `push-loop` | Preview, then create or update the sub-issues | `approve-push` |
| `strict-check-implement` | Strict mode: `ghp strict check <n> --phase implement`; advisory: exit 0 | |
| `implement` | Implement phase | |
| `decisions-loop` | Preview, then post the recorded decisions as one comment (`--if-any`: nothing to post without a decisions file) | `approve-decisions` |
| `strict-verify` | Strict mode: `ghp strict verify <n>` (timeout 7200 s); advisory: exit 0 | |
| `sync-loop` | Preview, then push again: new or renamed tasks reach their sub-issues; a ticked task closes nothing (no issue is opened or closed) | `approve-sync` |
| `gate`, `gate-check` | The DoD gate; `gate-check` runs `ghp gate` on the branch diff against `origin/HEAD` | |
| `refresh-context` | `ghp comments <n>` re-reads the discussion for the review | |
| `review` | Review of the branch diff | `review-review` |
| `check-review-review` | `ghp approvals check <n> --gate review-review` | |
| `pr-clear`, `pr-prepare`, `pr-loop` | Clear old PR files, build the PR body, preview, then push the head branch and open the PR | `approve-pr`, then `wait-po-validation` |
| `sync-po-validation`, `check-wait-po-validation` | Comment mode: `ghp decisions <n> --sync --if-comment-mode`, then the gate's check; terminal mode: no-ops | |

## Where approval is enforced

- **Verdict inputs only un-pause.** Spec Kit keeps a verdict across resumes, and anyone who can run `specify` can set one (above), so a
  verdict input is never the approval.
- **Comment mode** (`approval: "comment"` on the target branch; [docs/trust-and-limits.md](../../docs/trust-and-limits.md)): the authority is
  the CLI. The five review and wait gates (`review-spec`, `review-plan`, `review-review`, `wait-po-answers`, `wait-po-validation`) are each
  followed by `ghp approvals check <n> --gate <gate>`, which exits 2 (fixed text) unless an approver's comment for the current artifacts
  exists. The seven write gates are followed by their apply, which refuses without an approver's `/approve <gate> <hash>` comment for exactly
  that plan. A verdict supplied without the matching comment, or a plan that changed after the approval, stops the run. Only a comment by an
  approver (a numeric user id in `approvers` on the target branch) counts; the agent never writes one.
- **Terminal mode** (no `approval: "comment"` on the target branch): `ghp approvals check` and the two sync steps
  (`ghp questions <n> --sync --if-comment-mode`, `ghp decisions <n> --sync --if-comment-mode`) exit 0 and read nothing. The approval is a
  person answering the gate at a terminal. **Nothing technical stops a script or an agent that passes the verdict inputs**: the gate then
  passes with nothing shown, and the apply only checks that the plan is the one the preview wrote. Terminal mode trusts whoever runs `specify`.
- **The poller.** `ghp approvals sync <run> --resume` reads the gate the run is paused at, looks for the approver's comment, and resumes the
  run with `specify workflow resume <run> --input verdict_<gate>=<approve|reject>` plus one empty `--input verdict_<other>=` per other gate
  (one `--input` per pair), so a stale verdict cannot pass a later gate. The resume restarts at the paused gate, so the check runs next.
  `ghp` does not post approval-request comments: the approver copies the `/approve <gate> <hash>` line from `ghp approvals check` or the plan.

## Stopping, resuming and recovering

- `specify workflow status [run]` lists runs and where each stopped; `specify workflow resume <run>` continues a run that is `paused` or
  `failed`. A resume re-runs **only the unfinished step** and the steps after it; completed steps are not repeated, and nothing earlier is
  re-checked.
- A gate needs a terminal. Without one (a script, CI, an agent shell) the gate **pauses** the run instead of asking: run
  `specify workflow resume <run>` in a terminal to answer it.
- A check or sync step that exits 2 ends the run FAILED at that step. Fix the cause (in comment mode: get the approver's comment), then
  `specify workflow resume <run>` re-runs it. The poller only resumes a run that is paused at a gate.
- A run killed with `kill -9` (or a closed laptop) stays `running` in its `state.json`, and `resume` refuses a `running` run. The manual
  fix is for a person to set `"status": "failed"` in that `state.json` by hand (the kit never edits Spec Kit's files, and the agent never
  edits it), then `specify workflow resume <run>`: it re-runs the step that was running.
- If you reject a gate the run is `aborted`, and `resume` refuses it. `review-review` is the gate to reject when implement reported
  `INCOMPLETE` tasks. Fix the work (finish the tasks, address the review), then bring the later steps up to date by hand, in this order
  (`ghp status <n> --catch-up` prints the commands for the stages your story has not met yet):
  1. `ghp decisions <n> --post --if-any --plan-out .ghp/plan-decisions.md` (preview), and after a yes
     `ghp decisions <n> --post --if-any --apply --expect-plan .ghp/plan-decisions.md`: decisions recorded after `decisions-loop` are
     not posted otherwise.
  2. Strict mode: `ghp strict verify <n>`: the verification record in ledger section 4 describes the old code until you run it again.
  3. `ghp push <n> --plan-out .ghp/plan-sync.md` (preview), and after a yes `ghp push <n> --apply --expect-plan .ghp/plan-sync.md`:
     tasks added or renamed after `sync-loop` do not reach their sub-issues otherwise.
  4. `speckit.ghp.gate <n> --workflow` and `speckit.ghp.review <n> --workflow`: the gate and review results in ledger section 4
     describe the old code until you run them again.
  5. Open the PR with `speckit.ghp.pr <n>`: it shows the preview and waits for a yes before it pushes or opens anything.
- A `failed` run whose apply refused a changed plan (`<name>-applied` failed) needs a new run: a resume re-runs only that step and fails again.
- A push that stopped part way (`push-apply` failed with `push stopped at`) is not finished by a resume, although `ghp push` says a
  re-run resumes: that holds for `ghp push` itself, not for the workflow. A resume re-runs the apply with the plan you approved; some
  sub-issues exist now, and the approved plan no longer matches GitHub, so the apply refuses it (exit 2, a `.new` plan) and the run fails.
  Start a new run of the story: its agent steps run again, the comment steps post only what changed, and its push preview reads what
  exists and plans only the rest (the sub-issues made before show as unchanged). Outside the workflow, `ghp push <n>` (preview, yes,
  apply) finishes it, and `ghp status <n> --catch-up` lists the steps still to do by hand.
- A run paused at `wait-po-validation` is resumed with `specify workflow resume <run>` once the verdicts are recorded; an `aborted` one needs
  only `ghp decisions <n> --resolve` (terminal mode) or `ghp decisions <n> --sync` (comment mode) by hand before `speckit.ghp.done`.

## Why each write is a one-pass loop

Each write is three steps in a `do-while` with `max_iterations: 1`: the preview writes `.ghp/plan-<name>.md`, the gate
shows that file, and the apply runs the same command with `--apply --expect-plan` on that file. If the plan changed since
the preview, the apply refuses (exit 2) and writes the new plan to `<file>.new`; the loop does not repeat and the
`<name>-applied` step after it stops the run (exit 1): start a new run. The loop must not repeat, because a verdict
supplied by a resume stays in the inputs and would pass the gate again with no human looking at the new plan.
A plain `specify workflow resume` re-runs only the failed step, so it never applies the new plan either.

## Exit codes

Every other non-zero exit of a step (1, 2 or 3) stops the run; nothing is masked. Fix the cause, then
`specify workflow resume <run>` re-runs the failed step. A rejected gate aborts the run: it cannot be resumed (see "Stopping, resuming and recovering").

## Differences from ado-lifecycle

- The input is `issue_number` (ado: `story_id`), and every shell step checks it is a positive whole number before use.
- `base-check` is new: the run stops at once when `origin/HEAD` is not set (ado fell back to `origin/main`).
- `gate-check` stops on every non-zero exit of `ghp gate`, and when the changed files cannot be listed (ado went on for exit 2 and without the list).
- `refresh-context` stops the run when `ghp comments` fails (ado only warned).

## Not verified yet

A run end to end against GitHub has been made once, in terminal mode with the `claude` integration, from a fresh story to an
open pull request (records of the live runs: [docs/evidence](https://github.com/praveenlavu/spec-kit-ghp/tree/main/docs/evidence)). A run in comment mode (`approval: "comment"`) and the poller (`ghp approvals sync <run> --resume`)
have not run live. The contract test (`tests/test_speckit_contract.py`) has run on
Spec Kit 1.2.4.dev0 only; the minimum, 1.2.1, has not been tried.
