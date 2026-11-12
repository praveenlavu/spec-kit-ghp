# Quick start

This guide takes you from nothing to your first tasks on a GitHub Project board.
It takes about ten minutes (an estimate, not timed).

The examples use made-up names: the user `acme-demo`, the repository `hello-spec`, story issue #12 and Project number 3.
Swap in your own.

## Before you start

You need:

- **Python 3.9 or later** and **git**.
- **The GitHub CLI, `gh`.** Install it from [cli.github.com](https://cli.github.com/).
- **A Spec Kit project.** That is a folder where you ran `specify init`, with a spec and a `tasks.md` file.
- **A repository** for the issues, and **a GitHub Project** (a board) owned by your user account.
- **A story issue** in that repository. This is the issue the tasks will hang under.

> [!NOTE]
> In this release only Projects owned by a user account work. Projects owned by an organisation are refused.

## Step 1: Sign in with `gh`

`ghp` never handles your password or token. It asks `gh` to make every call, and `gh` signs in for you.

```bash
gh auth login --scopes project
gh auth status
```

The `project` scope (a permission of the sign-in) lets `gh` read and update your Project board. `gh auth status` should say you are logged in.

## Step 2: Install `ghp`

```bash
pipx install git+https://github.com/praveenlavu/spec-kit-ghp.git@v0.1.0
ghp --version
```

Expected output (the version number may differ):

```text
ghp 0.1.0
```

No `pipx`? Run `python3 -m pip install --user git+https://github.com/praveenlavu/spec-kit-ghp.git@v0.1.0` instead.
Both install the 0.1.0 release; the `v0.1.0` tag is created when 0.1.0 is released, and until then these lines do not work.

## Step 3: Create `ghp.config.json`

In the top folder of your Spec Kit project, run `ghp setup` and answer its questions. It asks for no password or token. It checks that `gh` is
signed in, then writes `ghp.config.json` for you. It never replaces an existing file unless you add `--force`.

Or create the file yourself, named `ghp.config.json`:

```json
{
  "owner": "acme-demo",
  "repo": "hello-spec",
  "project_owner": "acme-demo",
  "project_number": 3
}
```

What each value means:

| Key | What to put there |
|---|---|
| `owner` | The user that owns the repository. |
| `repo` | The repository name. |
| `project_owner` | The user that owns the Project board. |
| `project_number` | The board's number, from its web address: `github.com/users/acme-demo/projects/3`. |

Everything else has a sensible default. The board columns are expected to be named "Todo", "In Progress" and "Done".
If yours differ, set `status_todo`, `status_active` and `status_done`.
Every setting is explained in [Configuration](configuration.md).

## Step 4: Check that `ghp` can see your repository and board

These two commands only read. They change nothing.

```bash
ghp project show
```

Expected output (the ids will differ):

```text
Project: acme-demo #3 (PVT_kwFictional0003)
Status options (field PVTSSF_FictionalStatus):
  Todo                 f75ad846  <- status_todo
  In Progress          47fc9ee4  <- status_active
  Done                 98236657  <- status_done
Workflows:
  #1 Item closed                        disabled
  #2 Pull request merged                enabled
  #3 Auto-close issue                   disabled
  #4 Auto-add sub-issues to project     enabled
  #5 Pull request linked to issue       enabled
  #6 Item added to project              disabled
```

The arrows show which column `ghp` uses for each stage. The "Workflows" are GitHub's own board automations.
Turn off "Item closed" and "Auto-close issue" in your Project's Workflows screen: with them on, a card or an issue can reach "Done" without the approval step. [Trust and limits](trust-and-limits.md#known-limits-of-github) explains why.

```bash
ghp get 12
```

This prints the story as JSON. A shortened example:

```text
{
  "ref": "acme-demo/hello-spec#12",
  "title": "Say hello",
  "state": "open",
  "sub_total": 0,
  "sub_done": 0,
  "url": "https://github.com/acme-demo/hello-spec/issues/12"
}
```

## Step 5: Push your task list, preview first

Run it without `--apply` first. This is a dry run: it shows the plan and sends nothing.

```bash
ghp push 12 --from specs/001-hello/tasks.md
```

Expected output (the digest is shortened here):

```text
# Plan: push tasks to #12 as sub-issues
create 3 · update 0 · unchanged 0 · orphan 0

Project: new items start at 'Todo'

| action | task | title | issue | sub-issue | project item |
|---|---|---|---|---|---|
| create | T001 | T001 Add the greeting page | - | new sub-issue of #12 | add to the Project, Todo |
| create | T002 | T002 Show the opening hours | - | new sub-issue of #12 | add to the Project, Todo |
| create | T003 | T003 Write the page test | - | new sub-issue of #12 | add to the Project, Todo |

digest: 5c0f...
```

Read the plan. If it looks right, run the same command with `--apply`:

```bash
ghp push 12 --from specs/001-hello/tasks.md --apply
```

The plan prints again, then the result:

```text
created 3, updated 0, failed 0
  T001 #13
  T002 #14
  T003 #15
```

Each task is now an issue under story #12, and a card in the "Todo" column of your board.
Run the command again later and it only changes what changed in `tasks.md`.

## What next

- Start the story's checklist file: `ghp dod 12 --spec-dir specs/001-hello`.
- Move the story to "In Progress": `ghp activate 12`, then again with `--apply`.
- Create the feature branch: `ghp branch 12 --apply`.
- Open the pull request: `ghp pr create 12`, then again with `--apply`.

`ghp pr create` also reads the commit messages between your base and head branch, as they are on your computer right now, and refuses a closing word such as `Closes #99` that names another issue. It cannot see commits you push later, or a merge message you edit by hand when you merge.

To run the same checks on every pull request, see [CI on GitHub](ci-github.md).

Every command explains itself with `--help`. New words are in the [glossary](glossary.md).

## Troubleshooting

**Something is not working and I do not know what.**
Run `ghp doctor`. It reads your setup and prints one line per check: `pass`, `warn`, `fail` or `unknown`, with what to do next.
It never changes anything. Without a network, `ghp doctor --offline` still checks everything on your computer.

**`gh` says I am not logged in, or a command fails with a `gh` error.**
Run `gh auth status`. If you are not signed in, run `gh auth login --scopes project`.
If you are signed in but the board cannot be read, add the scope: `gh auth refresh --scopes project`.

**"No ghp config found".**
`ghp` looks for `ghp.config.json` in the folder you run it from. Run it from your project's top folder.

**The board is not found, or it is the wrong board.**
Check `project_number` against the board's web address. Check that `project_owner` is the user that owns the board.
Then run `ghp project show` again.

**My Project belongs to an organisation.**
This release supports only Projects owned by a user account. Organisation Projects are refused with `E_ORG_PROJECT_UNSUPPORTED`.
Create the board under your user account for now.

**I merged the pull request, but the story is still open.**
`Closes #12` in a pull request only closes the issue when the pull request is merged into the repository's default branch (usually `main`).
If you merged into another branch, GitHub ignores it. Finish the story yourself: `ghp done 12`. If that says no merged pull request is tied
to the story, name the pull request: `ghp done 12 --pr 44`, then again with `--apply`. The pull request must be merged, belong to this
repository, and carry a closing keyword for the story, such as `Closes #12`, in its title or body. A mention is not enough: without the keyword
`ghp done` refuses with `E_PR_NOT_LINKED` (exit code 2) and writes nothing. Edit the pull request text and run it again.

**I ran a command and nothing changed on GitHub.**
That is the dry run. Without `--apply`, `ghp` only prints what it would do. Add `--apply` when you are happy with the plan.

**A command stopped with exit code 2.**
Exit code 2 means "refused, nothing was written". The message says why. Common reasons: a missing or invalid `ghp.config.json`,
an issue number that is really a pull request, or a plan that changed since you approved it.
Fix the cause and run the command again. It is safe to retry.

**A command stopped with exit code 1 or 3.**
Exit code 1 means a call to GitHub failed, or a check found a blocking problem (`ghp gate`, `ghp doctor`).
Exit code 3 comes only from `ghp gate`, `ghp strict`, `ghp done`, `ghp status` and `ghp branch --id-from`: `ghp.config.json` is invalid,
or a checklist file is ambiguous (for example, two headings both start with `## 2.`). The message names the problem.
The other commands report an invalid `ghp.config.json` with exit code 2. The full table is in [CI on GitHub](ci-github.md#exit-codes).
