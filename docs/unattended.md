# Unattended runs

This page is for running the `ghp-lifecycle` workflow with nobody at a terminal. The approvers answer in the story's comment thread, and one command, `ghp approvals sync`, reads those comments and moves the paused run on. You run it on a schedule, so it works as a poller. It is a way to follow a process, not a security boundary: read the limits at the end.

## Before you start

- Comment mode must be on **in the branch you merge into** (the target branch, usually `main`): `"approval": "comment"` and a list of `approvers` (numeric user ids) in `ghp.config.json` there. See [Configuration](configuration.md). In terminal mode `ghp approvals sync` refuses with a fixed text and makes no request.
- The account the poller runs as is never an approver. Its own comments never count, even if its id is in the list.
- Use Spec Kit 1.2.1 or later. Only 1.2.4.dev0 has been tried ([compatibility](compatibility.md)).

## Where it can run

On your own machine, a server or a virtual machine that stays up. **Not on a throwaway CI runner.** A Spec Kit run keeps its state in `.specify/workflows/runs/<run id>/state.json` inside the project folder, and the run also needs that folder, the plan files under `.ghp/`, the coding agent's sign-in and the `gh` sign-in. None of these survives a runner that is thrown away after each job. The [ready-made CI workflows](ci-github.md) do not run the poller.

## Set it up

1. Start the run as usual: `specify workflow run ghp-lifecycle -i issue_number=42`. It pauses at the first gate and exits.
2. Find the run id. `specify workflow status` lists the runs and their state.
3. Run the poller from the project folder, as the same user as the run, on a schedule (for example every five minutes with `cron`):

   ```bash
   ghp approvals sync <run id> --resume
   ```

   Try it first with `--dry-run`: it prints what it would decide and records and resumes nothing.

## What one poll does

In order, and nothing after a refusal:

1. It checks the run id (ASCII letters, digits, `-` and `_`, at most 128 characters), then reads the run's `state.json`. That file is treated as untrusted: the agent could have edited it.
2. A run that is not paused does nothing and prints `"paused": false`.
3. The step it is paused at must be one of the workflow's 12 gates; anything else is refused.
4. It reads the story's comments and decides: `approve`, `reject` or `waiting`. The rules are the same as for an `--apply`: an approver's newest valid command counts, an approval is for the exact plan or text it names, and a newer malformed approver line means `waiting`, never `approve`. An approval is pinned (recorded in `.ghp/approvals/<issue>.json`), as [Trust and limits](trust-and-limits.md) explains.
5. With `--resume` and a verdict, it reads the run again and, if it is still paused at the same step, runs `specify workflow resume` with that verdict. Every other gate's verdict is reset to empty, so a stale one cannot pass a later gate. `specify`, `ghp` and `git` must be on your `PATH` in folders outside the project; the resume has a six-hour limit and its output is thrown away.

It prints the result as JSON. Exit codes: 0 done (including `waiting`), 1 the resume failed or a GitHub read failed, 2 refused with a fixed text.

## Not in this version

- **No approval-request comments.** `ghp` does not post a comment asking an approver to look. `unattended.approval_requests` is accepted and reported, but nothing is posted. Tell your approvers yourself, and give them the `/approve <step> <hash>` line that `ghp` prints when an approval is missing.
- **No assumed answers.** `unattended.assume_after_hours` cannot be used yet: `ghp` has no trusted clock, so a sync that would need it refuses.

## Limits

- **Anyone who can run `specify` on that machine can answer a gate.** A verdict passed to `specify workflow resume` by hand, or written into `state.json`, is not an approval. The check that holds is the one at the apply: every `--apply` in comment mode needs the approver's comment for the exact plan.
- **The comments are read twice, at the poll and at the apply.** A comment deleted or edited in between makes the apply refuse; the pin records what the poll saw.
- **It has not been run on a real story.** The poller is tested offline, and the resume command it builds is tested against the real Spec Kit engine.

These items from the [list of behaviours not checked on real GitHub](trust-and-limits.md#unverified-in-live-runs) matter most here:

- **Two-person approval.** Approval was tested with one person and one bot, not with two people.
- **A comment deleted by another writer.** Only a comment deleted by its own author was observed.
- **The comment join race.** A comment posted, changed or deleted between `ghp`'s two reads of the comment list is dropped, never counted.
- **The Spec Kit minimum version.** The installs were run on Spec Kit 1.2.1 and 1.4.0. The contract tests and the live lifecycle run used 1.2.4.dev0 only, so running the workflow on 1.2.1 or 1.4.0 has not been tried.
