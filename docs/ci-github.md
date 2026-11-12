# CI on GitHub

This repository ships four ready-made GitHub Actions workflows. Your repository can call them on every pull request.
A workflow that another repository calls is a **reusable workflow**: you do not copy its steps, you point at it with one `uses:` line.

> [!NOTE]
> These workflows are tested here, offline, but they have not yet run in another repository on GitHub's hosted runners.
> Treat the first run in your repository as a test. See [Trust and limits](trust-and-limits.md#unverified-in-live-runs).

## The four workflows

| Workflow | When it runs | What it does |
|---|---|---|
| `ghp-gate.yml` | Each pull request | Finds the issue from the branch name and runs `ghp gate`. When the target branch is in strict mode, it also runs `ghp strict check`. The result goes to the job summary. |
| `ghp-branch-guard.yml` | Each pull request | Checks that the branch name names an issue (such as `feature/gh12-say-hello`) and that the issue exists. |
| `ghp-strict-verify.yml` | Each pull request | Only in strict mode: runs your test command on the recorded commits, to show each test failed before the fix and passes after it. Otherwise it does nothing. |
| `ghp-link-on-merge.yml` | When a pull request is merged | Comments `Merged in #<pr>` on each issue the pull request closes, and closes any that are still open. With `close-story: true` it also runs `ghp done --apply`. |

The "target branch" is the branch the pull request merges into, usually `main`.

## Add them to your repository

Save this file as `.github/workflows/ghp.yml` in your repository:

```yaml
name: ghp checks

on:
  pull_request:
    types: [opened, synchronize, reopened, edited, closed]

permissions:
  contents: read

jobs:
  gate:
    if: github.event.action != 'closed'
    permissions:
      contents: read
      issues: read
    uses: praveenlavu/spec-kit-ghp/.github/workflows/ghp-gate.yml@REPLACE-WITH-ONE-REVIEWED-COMMIT-SHA
    with:
      ghp_ref: REPLACE-WITH-ONE-REVIEWED-COMMIT-SHA

  branch-guard:
    if: github.event.action != 'closed'
    permissions:
      contents: read
      issues: read
    uses: praveenlavu/spec-kit-ghp/.github/workflows/ghp-branch-guard.yml@REPLACE-WITH-ONE-REVIEWED-COMMIT-SHA
    with:
      ghp_ref: REPLACE-WITH-ONE-REVIEWED-COMMIT-SHA

  strict-verify:
    if: github.event.action != 'closed'
    permissions:
      contents: read
    uses: praveenlavu/spec-kit-ghp/.github/workflows/ghp-strict-verify.yml@REPLACE-WITH-ONE-REVIEWED-COMMIT-SHA
    with:
      ghp_ref: REPLACE-WITH-ONE-REVIEWED-COMMIT-SHA
      allow-override: false

  link:
    if: github.event.action == 'closed' && github.event.pull_request.merged == true
    permissions:
      contents: read
      issues: write
    uses: praveenlavu/spec-kit-ghp/.github/workflows/ghp-link-on-merge.yml@REPLACE-WITH-ONE-REVIEWED-COMMIT-SHA
    with:
      ghp_ref: REPLACE-WITH-ONE-REVIEWED-COMMIT-SHA
      close-story: false
    secrets:
      GHP_PROJECT_TOKEN: ${{ secrets.GHP_PROJECT_TOKEN }}
```

Then replace every `REPLACE-WITH-ONE-REVIEWED-COMMIT-SHA` with the same commit, as explained below. Left as it is, the file fails on purpose.

### Pin one commit

Each job names the workflow file with `@<commit>` and the `ghp` to install with `ghp_ref`. Use the same **full 40-character commit SHA** of this repository in both places, a commit you have read.

- A tag such as `v0.1.0` can be moved later to point at other code. A commit SHA cannot. (`ghp_ref` accepts a `vX.Y.Z` tag, but a SHA is safer.)
- `ghp_ref` has no default, so it can never quietly change.

### Why `edited` is in the list

Changing a pull request's target branch after it is opened fires only the `edited` event. Without `edited`, checks that passed against the old target branch stay green when the new target branch is a strict one.

### Permissions

Each job asks for exactly what its workflow needs. A called workflow never gets more than its caller gives it. With the Actions token, only the link job can write, and only to issues. The optional project token (below) is broader.

## The project token (optional)

The token GitHub gives every Actions run cannot see a Project owned by a user account. So to move a card on your board from CI, the link job needs a second token:

1. The **repository owner** creates a classic personal access token with the `repo` and `project` scopes. (A fine-grained token cannot use user Projects.)
2. The owner saves it as a repository secret named `GHP_PROJECT_TOKEN` (Settings, then Secrets and variables, then Actions).
3. Set `close-story: true` on the link job.

A classic token with the `repo` scope is not limited to this repository: it can read and write every repository its owner can. Give it an expiry date, and if you can, create it on a separate account that has access only to this repository.

Never paste the token anywhere else: not in a file, a comment, an issue, a chat or a command line. `ghp` itself never reads it; the workflow hands it to `gh`.

Without the secret, the link job still comments and closes issues, and says the board was not moved.

## Who may approve, in CI

`ghp` reads who may approve from the target branch through `origin/HEAD`, which `actions/checkout` does not create. The ready-made workflows check out the full history (`fetch-depth: 0`) and run `git remote set-head origin <default branch>` for you.

If you call `ghp` from your own job instead, do the same before the first `ghp` command:

```bash
git fetch origin main
git remote set-head origin main
```

Otherwise `ghp` stops with `E_TRUST_BASE_UNRESOLVED` (in CI it never guesses). Details are in [Trust and limits](trust-and-limits.md#where-the-list-of-approvers-comes-from).

## Exit codes

Every `ghp` command ends with one of these codes:

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | A GitHub call failed, or a check found a blocking problem (`ghp gate`, `ghp doctor`). |
| `2` | Refused, and nothing was written. The message says why. Most commands also use `2` for a missing or invalid `ghp.config.json`, and `ghp strict check` and `ghp strict verify` use it for their findings. |
| `3` | Only `ghp gate`, `ghp strict`, `ghp done`, `ghp status` and `ghp branch --id-from`: `ghp.config.json` is invalid, or a checklist file is ambiguous. |

The workflows fail the job on any code other than `0`. In a strict repository, `1`, `2` and `3` are all failures.

## Limits

- **Pull requests from forks.** GitHub gives a fork's pull request a read-only token and no secrets. The checks still run. The link job prints a notice and writes nothing; GitHub's own `Closes #12` still works on the default branch.
- **Merges into another branch.** GitHub ignores `Closes #12` when the target is not the default branch. The link job closes nothing then; finish the story with `ghp done <n> --pr <pr>`.
- **A private copy of this repository.** The workflows install `ghp` with `pip` from `github.com/praveenlavu/spec-kit-ghp`. That repository must be readable by your runner, which today means public; there is no token input for the install in this version.
- **Strict mode and the board.** The strict checks in CI (`ghp strict check --phase pr` and `ghp strict verify`) never read the Project board, so the Actions token's blindness to user Projects does not affect them. The board check runs only in `ghp strict check <n> --phase implement`, which a person runs on their own machine.
- **Running your tests on pull request code.** `ghp-strict-verify.yml` runs your project's test command on the pull request's code. The workflow gives it no secret, but the code runs with full control of the runner: it can read the job's read-only token from memory and use the network. Require approval for pull requests from outside contributors (a repository setting) and use GitHub-hosted runners, which are thrown away after each job.
- **Branch names.** Branch names may use letters, digits, `.`, `_`, `/` and `-` (and `#` in the pull request's branch), and no `..`. Anything else fails the job.
- **Package install.** While installing `ghp`, `pip` fetches its build tool (`setuptools`) from PyPI without a fixed version or hash check.
- **Secrets and pull requests from the same repository.** GitHub gives repository secrets to a pull request whose branch is in the same repository, and the job runs that pull request's own code. This repository's own CI passes the optional `SCRUB_DENYLIST` secret (the private word list of the leaked-names check) to a step that runs a script from the pull request's checkout, so anyone who can push a branch here could print the list. Set that secret only in a repository whose contributors are all trusted with it. A pull request from a fork gets no secret.

## Checking workflow files

[actionlint](https://github.com/rhysd/actionlint) is a linter for GitHub Actions files. This repository's own CI runs a pinned version of it on its workflows (its first run found one problem, since fixed). You can run it on your own `.github/workflows/ghp.yml` too.
