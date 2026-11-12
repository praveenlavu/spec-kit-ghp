# What ghp does on GitHub

This page lists everything `ghp` reads from GitHub and everything it writes, so you know exactly what it can change.

## How it talks to GitHub

- Every call goes through the GitHub CLI, `gh`. `gh` signs you in and holds your token.
- `ghp` never reads, asks for, stores or prints a token. No setting can hold one.
- Every write is shown first as a preview. Nothing is sent until you run the same command with `--apply`.

## The six commands that write to GitHub

| Command | What it writes |
|---|---|
| `ghp push <n> --apply` | Creates one issue per task, as a sub-issue of the story, with the label `ghp:task`. Adds each to the board in the "Todo" column. Renames a task issue when its title changed in `tasks.md`. |
| `ghp activate <n> --apply` | Moves the story's card to "In Progress" (adding it to the board first if needed). |
| `ghp pr create <n> --apply` | Opens one pull request for the story. With `--from-prepared --push` it first pushes your branch with `git push`. |
| `ghp questions <n> --post --apply` | Posts the story's open questions as one issue comment. |
| `ghp decisions <n> --post --apply` | Posts the open decisions as one issue comment. |
| `ghp done <n> --apply` | Posts the checklist summary as a comment, closes the story, and moves its card to "Done". |

The reusable workflow `ghp-link-on-merge.yml` also comments on and closes issues after a merge. See [CI on GitHub](ci-github.md).

## Commands that only read GitHub

`ghp get`, `ghp project show`, `ghp status`, `ghp gate`, `ghp doctor`, and the dry run (no `--apply`) of every command above.

Some commands read GitHub and then write only local files: `ghp comments` (a copy of the thread under `.ghp/context/`), `ghp dod` (the checklist file), `ghp rules` (the rules file), `ghp approvals check` (a record of the approval under `.ghp/approvals/`), and `ghp questions --sync`, `ghp decisions --sync` and `ghp approvals sync`.

## Commands that work locally

`ghp branch` (local git only, no network), `ghp setup` (writes `ghp.config.json`; it reads once who is signed in), `ghp stack`, `ghp kits`, `ghp calibrate`, and `ghp strict` (except that its checks may read the board).

## What ghp never does

- It never deletes anything on GitHub: no issue, comment, pull request, branch, label or card.
- It never edits or hides anyone's comment.
- It never creates a Project, and never changes a Project's columns, fields or workflows.
- It never uploads files or attachments.
- It never merges a pull request. It pushes your branch only when you ask, with `ghp pr create --push` (the preview says so).
- It never changes repository settings, branch protection, secrets or permissions.
- It never approves on a person's behalf.

## What GitHub does on its own

GitHub's own features can change things without `ghp`:

- `Closes #12` in a merged pull request closes issue 12 (only for merges into the default branch).
- A Project's built-in workflows can move cards and close issues. See [Trust and limits](trust-and-limits.md#known-limits-of-github) for the two to turn off.
