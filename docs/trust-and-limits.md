# Trust and limits

This page says what `ghp` can promise, what it cannot, and what has not been tested on real GitHub yet.
Words in **bold** on first use are explained in the [glossary](glossary.md).

## The short version

- `ghp` writes to GitHub only after a person approves a preview. (One exception you opt into: the [link-on-merge workflow](ci-github.md) comments on and closes issues when a pull request is merged; the merge is the approval there.) It never reads your token: the GitHub CLI, `gh`, signs in and makes each call.
- Who may approve is read from the branch you merge into (usually `main`), never from the pull request. So a pull request cannot add its own approvers.
- Approving by issue comment is **tamper-evident, not tamper-proof**: `ghp` notices an edited or hidden comment, but it cannot stop a person with write access from deleting one.
- If you need more than that, keep the default, `"approval": "terminal"`: a person types the approval in their own terminal.

## Known limits of GitHub

These come from GitHub itself. `ghp` cannot change them, so plan around them.

- **Any writer can edit or delete any comment.** Anyone with write access to a repository can edit or delete any comment on its issues, including an approver's.
- **Free private repositories have no branch protection or rulesets.** On a free GitHub account, a private repository cannot protect its default branch, so nothing on GitHub stops a pull request from being merged without checks. `ghp doctor` warns about this.
- **The Actions token cannot reach user Projects.** The token GitHub gives every Actions run (`GITHUB_TOKEN`) cannot see a Project owned by a user account. A workflow that must move a card needs a separate token that the repository owner creates (see [CI on GitHub](ci-github.md)).
- **Moving to Done closes the issue.** While the board's "Auto-close issue" workflow is on, dragging a card to "Done" closes its issue at once, with no approval step. Close stories with `ghp done` instead.
- **A Project has six built-in workflows, and two of them should be turned off.** The six are: "Item added to project", "Item closed", "Pull request merged", "Auto-close issue", "Auto-add sub-issues to project" and "Pull request linked to issue". Turn off **Auto-close issue** (a drag to Done closes the issue) and **Item closed** (closing an issue moves its card to Done on its own). There is no API to switch them off: open the Project, choose the menu, then **Workflows**, and turn each one off there. `ghp doctor` and `ghp project show` show which ones are on.
- **"Auto-add sub-issues to project" can beat `ghp push` to the board.** When it is on, GitHub may put a new sub-issue on the Project a moment before `ghp push` adds it, and GitHub then answers "Content already exists in this project". `ghp push` expects this: it reads the board, finds the card and carries on. It does not send the add again. You can leave the workflow on.
- **A fine-grained personal access token cannot use user Projects.** GitHub's fine-grained tokens do not work with Projects owned by a user account. Use `gh auth login` (or, for CI, a classic token).
- **Reopening an issue does not move its card back.** A reopened issue stays in the "Done" column. Move it back by hand.

## Approvals by comment

With `"approval": "comment"` in `ghp.config.json` on the branch you merge into, a person listed in `approvers` approves a step by commenting on the issue.

A comment counts only when all of these are true:

- the author is a real user: not a bot, not an app, and not the account `ghp` runs as;
- the author's numeric user id is in `approvers`;
- the comment was never edited or hidden;
- its first line is a valid command, such as `/approve D1`. Invisible characters or right-to-left marks in that line make it not count.

A person's role on the repository (owner, member, collaborator) is never looked at.

### What comment approval does not protect against

In short: if a writer deletes comments before `ghp` reads them, `ghp` cannot tell.

- **Any writer can edit or delete any comment.** That includes an approver's comment.
- **An edit is visible; `ghp` refuses it.** GitHub keeps the editor and the edit time (`lastEditedAt`) of an edited comment, and a hidden comment reads `isMinimized` with a reason. Observed in a live run ([evidence](https://github.com/praveenlavu/spec-kit-ghp/blob/main/docs/evidence/T31.md)): one edit makes GitHub's edit count 2. `ghp` does not count an edited or hidden comment. A comment posted by a workflow's own token belongs to the bot and carries the GitHub Actions app, so it never counts either. That is why the guard is tamper-evident, not tamper-proof: it can notice an edit, it cannot stop one.
- **A deletion leaves nothing to see.** Observed in a live run ([evidence](https://github.com/praveenlavu/spec-kit-ghp/blob/main/docs/evidence/T31.md)): after a comment's author deleted it, it was gone from the comment list, a direct read of it answers 404, and the issue timeline showed no deletion event. The deletion of another person's comment by a writer was not tried, because only one human account was available. So if a writer deletes an approver's later `/reject`, the earlier `/approve` would count again.
- **Pins narrow that gap.** When `ghp approvals sync` or `ghp approvals check` reads an approval, it saves a record of it in `.ghp/approvals/<issue>.json`. This record is called a **pin**. The pin also lists every approver command seen for the same step, malformed ones too. Before the step is applied, and before you close the story, `ghp` reads the comments again. It refuses if a pinned comment is gone or changed, or if the approval no longer counts. It also refuses if a newer command exists, or if the pin is for another plan.
- **Replacing a pin needs the old comments intact.** A pin is replaced when the text was revised, or after a `/reject` and a new `/approve` (run `ghp approvals sync` again). Before it replaces a pin, `ghp` checks that every comment the old pin recorded is still there and unchanged. If one is gone, nothing is pinned or approved until a person has read the thread and deleted `.ghp/approvals/<issue>.json` on purpose.
- **Pins help, but they are not proof.** The pin file lives in your working tree. Whoever can write that file (the agent, or a pull request that commits `.ghp/`) can delete, edit or forge it. That removes the protection, just as deleting it does. For example, a pin written by hand from a live `/approve` satisfies `ghp done`. Pins only help against someone who can delete comments but cannot write your working tree. A deletion made before the sync or check is not seen either.
- **A later edited or hidden comment blocks the step.** If an approver's comment that is newer than the approval is edited or hidden, the approval is refused, whatever the comment says now. Otherwise a writer could edit or hide an approver's later `/reject` and bring the earlier `/approve` back. So an approver who edits a later comment of their own blocks the step until they post a new command.
- **Anyone who can comment can delay an approval.** A comment that starts with `ghp`'s approval-request marker for the step and its plan (`[spec-kit-ghp:approval-request] <step> <hash>`), posted after the approval, makes the step wait instead of approving it (delete that comment, or approve again). Only a comment that starts with the marker counts: the marker later in a comment, or in a quoted (`>`) line, does not make the step wait. An approval is checked once, before the first write: a `/reject` posted while a long apply runs is not seen by that apply.

### Where the list of approvers comes from

Who may approve is read from the branch you merge into, through `origin/HEAD` (the remote's default branch, as your local git knows it). For `pr create` only, the pull request's `--base` is used instead; a `--base` that is not a branch here is refused when `origin/HEAD` is set (fetch it first). Without `origin/HEAD`, the case below applies.

If `origin/HEAD` is missing, nothing can be checked:

- On your own machine, `ghp` uses the defaults when the working tree sets no approval keys. (So a branch that deletes the keys turns comment mode off locally.)
- In CI (`GITHUB_ACTIONS` or `CI` set to `true` or `1`, in any letter case), it refuses with `E_TRUST_BASE_UNRESOLVED`.

That refusal is wider than the applies. It also stops `ghp done` (a dry run too, for its cross-check), `questions --sync`, `decisions --sync`, `decisions --resolve`, `approvals check` and `approvals sync`, and it applies to repositories in terminal mode as well.

`actions/checkout` creates no `origin/HEAD`, and `--base` helps only `pr create`. So a CI job must fetch the default branch and set it before running `ghp`: `git fetch origin <default>` and `git remote set-head origin <default>`. The ready-made workflows do this for you.

The CI check reads the process environment. It guards against a misconfigured job (or a pull request that only deletes the approval keys), not against a process that unsets the variables. A stale `origin/HEAD` (not fetched since the approvers changed) gives the old approvers until the next fetch.

### Which commands need an approval comment

- Every `--apply` that writes to GitHub needs, in comment mode, an approver's `/approve <step> <hash>` comment for the exact plan being applied: `questions --post`, `decisions --post`, `push`, `activate`, `pr create` and `done`. `push --apply` always needs it, even when it has nothing to send. A changed plan has another hash, so it is not approved.
- Closing a story (`ghp done --apply`) also needs a pinned approval of its plan. Write the preview with `ghp done <n> --plan-out .ghp/plan-done.md`. After the approver's `/approve approve-done <hash>` comment for that plan, `ghp approvals check <n> --gate approve-done` pins it; then run the apply. The check pins only a comment that counts, by the rules above.
- `ghp questions <n> --sync` and `ghp decisions <n> --sync` copy approvers' `/answer`, `/approve` and `/reject` comments into the checklist file and the decisions file. These are local files; nothing is written to GitHub. A line stays open when a newer approver line is malformed, edited or hidden (`bad-newer`), or when a newer comment that claims to be `ghp`'s decisions comment cannot be read (`posting-disguised`).
- `ghp approvals sync <run>` reads a paused Spec Kit run, records the verdict for the step it waits at and, with `--resume`, resumes it. The account it runs as never counts as an approver.

### Not in this version

[Unattended runs](unattended.md) explains how to run `ghp approvals sync` on a schedule, and its limits.

- `ghp` does not post approval-request comments. `unattended.approval_requests` is reported, not acted on.
- `unattended.assume_after_hours` cannot be used yet. `ghp` has no trusted server clock, so a sync that would need one refuses.
- `ghp strict` prints these limits, in plain words, whenever the config says approval is `comment` (in the `notes` field of its output and as a `note:` line on the error stream).

## Unverified in live runs

This repository's own hosted CI has run. Its first run found one problem in the workflow linter (actionlint), since fixed; run 42861951070 then passed every job: Python 3.9, Python 3.13 and actionlint ([record](https://github.com/praveenlavu/spec-kit-ghp/blob/main/docs/evidence/CI-RUN.md)).

These behaviours have not been checked against real GitHub. `ghp` is built to fail safely around each one, but treat them as unknown.
Some items are technical; each one only matters if you rely on that exact behaviour.

- **Two-person approval.** Approval was tested with one person and one bot, not with two people.
- **A comment deleted by another writer.** Only a comment deleted by its own author was observed.
- **The true size cap of a comment.** A 65,537-character comment was accepted; the real limit is unknown. `ghp` refuses comments above 60,000 characters.
- **The unit of that cap.** Whether GitHub counts that limit in characters or in bytes.
- **Token refresh and expiry.** Whether a `gh` sign-in token expires or refreshes on its own.
- **Fine-grained tokens and user Projects.** Documented by GitHub as unsupported, never tried. `ghp doctor` shows their scopes as `unknown`.
- **The scopes header of fine-grained tokens.** Whether GitHub tells a client which permissions a fine-grained token has.
- **A token with only the repo scope.** Whether a token without the `project` scope can do every issue and comment read.
- **A token without the project scope.** That such a token fails the board reads with a clear scope error.
- **Switching off a Project's built-in workflows by API.** No such API was found; none was tried.
- **The workflows list after a change in the Project's screen.** What the list of board workflows looks like after you switch one off by hand.
- **Organisation-owned Projects.** Refused in this version.
- **A user id after a rename.** That a person's numeric user id stays the same when they rename their account.
- **The app field in GraphQL.** Whether GitHub's GraphQL API says which app posted a comment (`ghp` asks the REST API instead).
- **Linked branches.** GitHub's "linked branch" feature is not used.
- **Secondary rate limits.** GitHub's extra rate limits and its "retry after" answer were never triggered; the retry code is tested only offline.
- **A write that fails with a server error.** Whether a write that GitHub answers with a server error may still have happened.
- **A write that ends with a 1xx or 3xx status.** How such an answer to a write behaves.
- **The automation's actor name.** That GitHub's board automation always shows as `github-project-automation` (seen, not documented).
- **Automation delay at Done.** How long the board automation can take; `ghp done` waits about 30 seconds, plus the time of the reads (about 35 seconds in the live runs), for the board automation before it sets Done itself.
- **Issue forms.** How GitHub's issue forms render the text `ghp` reads.
- **Attachments.** Whether any API for file attachments exists (none was found).
- **Bodies typed in the web editor.** How GitHub's web editor stores text you type or paste there.
- **Name length limits.** The maximum length of an owner name (39) and a repository name (100).
- **The write detector.** That `ghp`'s check for GraphQL writes reads every document the way GitHub does.
- **Rare Markdown in issue text.** Setext headings, link reference definitions, HTML blocks, a comment directly before a heading on one line, and code fences inside list items.
- **Look-alike characters.** Letters from other alphabets that look like ASCII are not detected; `ghp` compares exact ASCII.
- **Edit counts after several edits.** GitHub's edit count after more than one edit.
- **The comment join race.** A comment posted, changed or deleted between `ghp`'s two reads of the comment list is dropped, never counted.
- **The newest 300 issues.** That the newest 300 issues always hold every task of an earlier push on a busy repository.
- **Two pushes at once.** Two `ghp push` runs on the same story at the same time.
- **Labels set by an issue template.** An issue template that adds the `ghp:task` label makes anyone's issue look like a task.
- **The sub-issue summary in GraphQL.** The exact shape of GitHub's sub-issue counts.
- **An open pull request in the closing references.** That GitHub lists an open pull request with `Closes #12` as a closing reference before it is merged.
- **A closer commit in a fork network.** Which repository GitHub reports for a closing commit that came from a fork.
- **Fork and other-repository refusals of `ghp done`.** `ghp done` refuses a pull request named with `--pr` that comes from a fork or belongs to another repository, and a story closed by a commit of another repository; those refusals have not run against real GitHub.
- **Board and strict checks on GitHub and Actions.** The board check and `strict check` on real GitHub and in Actions. `strict verify` ran once in Actions in another repository, on a pull request where it failed; a passing run in Actions has not been tried.
- **The reusable workflows in a consumer repository.** The four ready-made workflows have run once in another repository on GitHub's hosted runners, pinned to the 0.1.0 release commit, by an AI agent on a public test repository: a passing gate, a failing gate, a pull request whose own config file is ignored, and link-on-merge without the `GHP_PROJECT_TOKEN` secret. Not run there: link-on-merge with that secret, a merge into a branch other than the default one, pull requests from forks (and their read-only token), a private consumer repository, and the re-run when a pull request's target branch is changed (the `edited` event).
- **The Spec Kit minimum version.** The installs were run on Spec Kit 1.2.1 and 1.4.0. The contract tests and the live lifecycle run used 1.2.4.dev0 only, so running the workflow on 1.2.1 or 1.4.0 has not been tried.
