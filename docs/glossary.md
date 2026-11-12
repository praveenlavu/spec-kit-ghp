# Glossary

Plain-words meanings for the terms used in this project.

**Acceptance criterion.** One line in a story that says what "done" means, such as "the page shows the opening hours".
A story usually has a few.

**Advisory mode.** The default way of working. Checks report problems, and block only the ones you list in `ghp.config.json`.

**Approval by comment.** An optional mode where an approver approves a step by writing a comment such as `/approve` on the issue,
instead of typing yes in a terminal. See [Trust and limits](trust-and-limits.md).

**Approver.** A person whose approval counts. Approvers are listed by their numeric GitHub user id in `ghp.config.json` on the target branch.

**Board.** A GitHub Project shown as columns of cards. See "Project".

**Business rules.** Rules written on a parent issue (an Epic or a Feature) that every story under it must follow. Off by default.

**Classic token.** The older kind of GitHub personal access token, with broad permissions called scopes. The CI workflows need one
to move cards on a user's board, because the newer fine-grained tokens cannot.

**Closing keyword.** A word such as `Closes`, `Fixes` or `Resolves` followed by an issue number, for example `Closes #12`, in a pull request.
When the pull request is merged into the default branch, GitHub closes that issue.

**Constitution.** The rules file of a Spec Kit project (`.specify/memory/constitution.md`). `speckit.ghp.constitution` adds six GitHub rules to it.

**Default branch.** The main branch of a repository, usually `main`.

**Definition of done (DoD).** The checklist a story must pass before it can be closed.
`ghp` keeps it in a file per story, next to your code.

**Dry run.** Running a command without `--apply`. It prints what would change and sends nothing to GitHub.

**Exit code.** The number a command returns when it ends. `0` means success, `1` a GitHub call failed or a check found a blocking problem,
`2` the command refused and wrote nothing (also a missing or invalid configuration, for most commands), and `3` the configuration or a checklist file is invalid
(only for `ghp gate`, `ghp strict`, `ghp done`, `ghp status` and `ghp branch --id-from`). [CI on GitHub](ci-github.md#exit-codes) has the full table.

**Extension.** An add-on for Spec Kit. This project is one: it adds the `/speckit.ghp.*` slash commands.

**Fork.** A copy of a repository under another account. GitHub gives pull requests from a fork fewer permissions.

**Gate.** A check that must pass before the work moves on. `ghp gate` checks that every acceptance criterion is linked to a named
test before review.

**GitHub Actions.** GitHub's built-in system for running jobs, such as tests, on every pull request.

**`gh`.** The GitHub command-line tool. `ghp` asks it to make every call to GitHub, so `gh` is what signs you in.

**Issue.** A GitHub issue: a numbered item in a repository, such as #12. `ghp` uses one issue per story and one per task.

**Kit.** A Spec Kit add-on, for example one for a certain programming language. It can write the spec, the plan or the tasks in its own way; `ghp` checks the result.

**Ledger.** The checklist file for one story, for example `dod/DoD_12.md`.
It links each acceptance criterion to the test that proves it, and records questions and decisions.

**`origin/HEAD`.** Your local git's record of which branch is the default one on GitHub. `ghp` uses it to find the target branch's approvers.
Set it with `git remote set-head origin main`.

**Pin.** A local record, in `.ghp/approvals/`, of an approval comment and the comments around it. `ghp` checks it again before it writes,
so a deleted or changed comment is noticed.

**Plan digest.** A short fingerprint of a preview, printed as `digest:` at its end.
If anything in the plan changes, the digest changes too, so an approved plan cannot be swapped quietly.

**Preview.** What a command prints before it changes anything: the plan of every change it would make. You approve the preview, then apply it.

**Project.** A GitHub Project: a board of cards for your issues. `ghp` supports Projects owned by a user account.

**Project item.** A card on a GitHub Project board. Each issue you add to the board becomes one item.

**Project workflows.** A Project's own automations, such as "Item closed" (closing an issue moves its card to Done).
They are set in the Project's Workflows screen. They are not the same as GitHub Actions workflows.

**Pull request.** A request to merge a branch's changes into another branch, usually the default branch.

**Reusable workflow.** A GitHub Actions workflow that another repository calls with one `uses:` line, instead of copying its steps.

**Scope.** A permission of a GitHub token. `gh auth login --scopes project` asks for the `project` scope, which lets `gh` read and change your board.

**Slash command.** A command you type in your AI coding agent, such as `/speckit.ghp.push`. It tells the agent what to run and where to stop for your yes.

**Spec Kit.** An open-source toolkit for spec-driven development: you write a spec, then a plan, then a task list, often with an AI coding agent.

**Status.** The board column an item sits in, such as "Todo", "In Progress" or "Done".
Moving an item to "Done" closes its issue while the Project's "Auto-close issue" workflow is on.

**Story.** The issue that describes one piece of work. Its tasks become sub-issues under it.

**Strict mode.** An optional, stricter way of working. It checks that each test was seen failing before the code made it pass.

**Sub-issue.** An issue that sits under another issue. `ghp push` makes each task a sub-issue of its story.

**Tamper-evident.** A change can be noticed afterwards, but not prevented. Approval by comment is tamper-evident, not tamper-proof.

**Target branch.** The branch a pull request merges into, usually the default branch.

**Task.** One line of a Spec Kit `tasks.md` file, such as `T001 Add the greeting page`. `ghp push` turns each task into an issue.

**Trust (approvers).** The list of people whose approval counts. It is read only from the branch you merge into,
so a pull request cannot add its own approvers.

**Workflow (Spec Kit).** `ghp-lifecycle`: a Spec Kit workflow that runs the steps of a story in order and stops at each approval.
