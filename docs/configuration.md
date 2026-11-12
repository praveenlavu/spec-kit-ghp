# Configuration

`ghp` reads its settings from one file, `ghp.config.json`, in the top folder of your project.
`ghp setup` writes it for you. [ghp.config.template.json](../ghp.config.template.json) shows every key with its default.

Only `owner` and `repo` are required. Leave out any other key to get its default.
The file must never hold a token or password: a top-level key with "token" in its name is refused.

## Where the file is read from

- **On your machine,** `ghp` reads `ghp.config.json` in the folder you run it from.
  To use another file, set the environment variable `GHP_CONFIG` to its path.
- **The three approval keys** (`approvers`, `approval` and `unattended`) are read **only from the branch you merge into** (the target branch, usually `main`), with `git show`. A pull request that changes them changes nothing. If another file in use (for example one named by `GHP_CONFIG`) sets them, `ghp` refuses.
- **In CI, when the target branch says `"mode": "strict"`,** the checks use the target branch's whole file, and the pull request's copy is not read. So a pull request cannot make its own checks weaker.

The "Who may set it" column below uses two answers:

- **Your branch:** whoever edits `ghp.config.json` on the branch you work on (a pull request can change it).
- **Target branch only:** only a change merged into the target branch counts.

## Where your issues and board live

| Key | What it means | Default | Who may set it |
|---|---|---|---|
| `owner` | The user or organisation that owns the repository with your issues. | none (required) | Your branch |
| `repo` | The name of that repository. | none (required) | Your branch |
| `project_owner` | The user account that owns your GitHub Project (the board). | the same as `owner` | Your branch |
| `project_owner_type` | Who owns the board. Only `"user"` works in this version; anything else is refused with `E_ORG_PROJECT_UNSUPPORTED`. | `"user"` | Your branch |
| `project_number` | The board's number, from its web address: `github.com/users/<you>/projects/3` means `3`. Without it, `ghp push` creates issues without adding them to a board, and `ghp activate` refuses. | none | Your branch |

## Board columns

The board's "Status" field has one option per column. `ghp` finds each option by its name.

| Key | What it means | Default | Who may set it |
|---|---|---|---|
| `status_todo` | The column new task issues are put in. | `"Todo"` | Your branch |
| `status_active` | The column a story moves to when work starts (`ghp activate`). | `"In Progress"` | Your branch |
| `status_done` | The column a finished story moves to (`ghp done`). | `"Done"` | Your branch |

## Files and branches

| Key | What it means | Default | Who may set it |
|---|---|---|---|
| `dod_path` | The folder for each story's checklist file (`DoD_<n>.md`), rules file and decisions file. | `"dod"` | Your branch |
| `branch_prefix` | The start of a feature branch name. `ghp branch 12` names the branch `<branch_prefix>gh12-<short title>`. It may be empty. | `"feature/"` | Your branch |

## Checks

| Key | What it means | Default | Who may set it |
|---|---|---|---|
| `mode` | `"advisory"`: checks report problems but block only what you list. `"strict"`: a fixed set of checks always blocks, and each test must be seen failing before the code makes it pass. | `"advisory"` | Your branch |
| `gate` | Settings for `ghp gate`, the check that runs before review. It has the two keys below. | `{"tests": {}, "block": []}` | Your branch |
| `gate.tests` | Extra test files: each key is a file pattern (such as `"src/**/*.spec.ts"`) and each value is a regular expression that counts as an assertion in those files. | `{}` | Your branch |
| `gate.block` | Which findings of `ghp gate` fail the check (exit 1). The names are `ac_traceability` (an acceptance criterion has no test), `assertion` (a test has no assertion), `ac_drift` (the story's criteria changed since the checklist was made), `open_questions`, `commits_without_story` (a commit does not name the issue), `open_decisions`, `rule_coverage` and `rule_drift` (business rules). In strict mode `ac_traceability`, `assertion`, `open_questions` and `commits_without_story` are always added, and `rule_coverage` and `rule_drift` too when rules are on. | `[]` | Your branch |
| `review_block_on` | Which review findings stop `ghp pr create`: any of `"blocker"`, `"major"` and `"minor"`. In strict mode `"blocker"` is always added. | `[]` | Your branch |
| `strict` | Settings for strict mode. It has the two keys below. | `{"test_command": null, "test_timeout": 300}` | Your branch |
| `strict.test_command` | The command that runs your tests, such as `"pytest -q"` (1 to 500 characters). `ghp strict verify` runs it on older commits to show each test failed first and passed later. It is code from your project: it runs with your user's access. | none | Your branch |
| `strict.test_timeout` | How many seconds one test run may take, from 1 to 3600. | `300` | Your branch |
| `rules` | Settings for business rules: rules written on a parent issue that a story must follow. It has the two keys below. | `{"enabled": false, "types": ["Epic", "Feature"]}` | Your branch |
| `rules.enabled` | `true` makes `ghp dod` record which parent issues hold the story's rules, and `ghp gate` check that each rule is covered by a test (`ghp rules` copies the rules into a local file). | `false` | Your branch |
| `rules.types` | Which kinds of parent issue may hold rules: 1 to 10 names, each at most 64 characters. `"Story"` is not allowed. | `["Epic", "Feature"]` | Your branch |
| `stack` | A placeholder for the add-on ("kit") that does each step. `stack.kit` and `stack.phases` are accepted but not used in this version; `ghp stack` keeps its settings in `ghp.stack.json` instead. | `{"kit": "", "phases": {}}` | Your branch |
| `stack.kit` | Accepted, not used in this version. | `""` | Your branch |
| `stack.phases` | Accepted, not used in this version. | `{}` | Your branch |

## Approval (read only from the target branch)

These three keys decide who may approve a change. They are read only from the target branch, never from your working tree or a pull request.

| Key | What it means | Default | Who may set it |
|---|---|---|---|
| `approval` | `"terminal"`: a person types the approval in their own terminal. `"comment"`: an approver approves by commenting on the issue (see [Trust and limits](trust-and-limits.md)). | `"terminal"` | Target branch only |
| `approvers` | Who may approve in comment mode: up to 20 numeric GitHub user ids, never login names. Find an id with `gh api users/<login> --jq .id`. Never add the account the agent or CI runs as. | `[]` | Target branch only |
| `unattended` | Settings for runs with nobody at the terminal ([Unattended runs](unattended.md)). It has the two keys below. | `{"approval_requests": false}` | Target branch only |
| `unattended.approval_requests` | Accepted and reported, but `ghp` does not post approval-request comments in this version. | `false` | Target branch only |
| `unattended.assume_after_hours` | Hours (1 to 720) after which an unanswered question would be assumed. It cannot be used yet: `ghp` has no trusted clock, so a sync that needs it refuses. | none | Target branch only |

## Kept for older files

These keys are accepted so older files keep working. No command uses them in this version.

| Key | What it means | Default | Who may set it |
|---|---|---|---|
| `attachments_allow` | File types that could be attached. `ghp` never uploads attachments, so this has no effect. | `[".md", ".svg", ".txt"]` | Your branch |
| `done_state` | An older name for the Done column. Use `status_done`. | `"Done"` | Your branch |
| `removed_state` | The name of a "Removed" column. Not used. | `"Removed"` | Your branch |
| `repository` | An older way to name the repository. Use `owner` and `repo`. | `""` | Your branch |

## Keys that are refused

- Any top-level key with "token" in its name: `ghp` never reads a token; `gh` signs in.
- `dod_gate`: renamed to `gate`. Move `dod_gate.block` to `gate.block`.
- `organization`, `project`, `api_version`, `story_type`, `task_type` and `repo_host`: these belong to a different tool and are not `ghp` keys.

An invalid file stops every command with a message that names the key (never its value). Most commands then exit with code 2; `ghp gate`, `ghp strict`, `ghp done`, `ghp status` and `ghp branch --id-from` exit with code 3.
