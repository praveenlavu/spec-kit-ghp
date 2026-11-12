# Catalog submission (DRAFT)

**DRAFT. Nothing is published.** This page holds the values to paste into Spec Kit's three submission routes on the day someone decides to list `ghp` in Spec Kit's community catalogs.
Whether to publish is the owner's decision, and it has not been made. No release exists: the addresses below point at a tag, `v0.1.0`, that has not been created.
Anything marked **TO CONFIRM** must be filled in or checked against the real release before it is filed. Nothing on this page is a result: it is a form, filled in ahead.

**Two different numbers.** The catalog entry counts the extension's **slash commands**, from `extension.yml`: 13 commands (`speckit.ghp.*`) and 2 hooks. The `ghp` program is a separate thing with 21 commands of its own (`ghp --help`); those are not counted in any form.

**Two different files on the release.** The extension file is `spec-kit-ghp-extension-0.1.0.zip`, made by `python3 scripts/build_extension_zip.py <dir>`. The bundle file is `ghp-0.1.0.zip`, made by `specify bundle build` (its name is always the bundle id and its version). Both are attached to the `v0.1.0` release.

**Where each one is filed.** The extension and the bundle go through issue forms in `github/spec-kit` ("Extension Submission" and "Bundle Submission"). A workflow has no issue form: it is a pull request that adds the entry to `workflows/catalog.community.json`, as Spec Kit's workflow publishing guide says. Every value below is copied from `extension.yml`, `workflows/ghp-lifecycle/workflow.yml`, `bundle/bundle.yml` or the catalog files; `tests/test_catalog.py` fails when one drifts.

**The two catalogs the bundle needs.** Spec Kit's community catalogs are for discovery only, so a bundle cannot install its parts from them. The repository serves two install-allowed catalogs from its `main` branch, `catalog/extensions.json` and `catalog/workflows.json`:

- `https://raw.githubusercontent.com/praveenlavu/spec-kit-ghp/main/catalog/extensions.json`
- `https://raw.githubusercontent.com/praveenlavu/spec-kit-ghp/main/catalog/workflows.json`

## What is ready

| File | What it is |
|---|---|
| `catalog/extensions.json` | The catalog entry for the `ghp` extension. The values that also appear in `extension.yml` are copied from it: id, name, description, version, author, the Spec Kit minimum (1.2.1), the tools, the tags, the 13 commands and the 2 hooks. The download, documentation and changelog addresses, the dates and the `verified` flag are not in `extension.yml`. |
| `catalog/workflows.json` | The catalog entry for the `ghp-lifecycle` workflow, copied from `workflows/ghp-lifecycle/workflow.yml`, with the same `requires` block (Spec Kit refuses a pinned install whose downloaded `requires` differs). |
| `bundle/bundle.yml` and `bundle/README.md` | One bundle that installs the extension and the workflow together, each pinned to its version, and the page that explains it. |

The entries keep `"verified": false`: only the Spec Kit maintainers can verify an entry. They carry no checksum, because there is no release file to take one from. The download address is a file attached to the release, not GitHub's automatic archive of the tag. The dates are the day the pack was written: **TO CONFIRM**, set them to the release date with the changelog date.

## 1. Extension submission

| Field | Value |
|---|---|
| Issue title | `[Extension]: Add ghp` |
| Extension ID | `ghp` |
| Extension Name | `Spec Kit for GitHub Projects - human-gated lifecycle` |
| Version | `0.1.0` |
| Description | `GitHub issue to Done through Spec Kit, with a human sign-off before every write.` |
| Author | `praveenlavu` |
| Repository URL | `https://github.com/praveenlavu/spec-kit-ghp` |
| Download URL | `https://github.com/praveenlavu/spec-kit-ghp/releases/download/v0.1.0/spec-kit-ghp-extension-0.1.0.zip` |
| License | `MIT` |
| Homepage | `https://github.com/praveenlavu/spec-kit-ghp` |
| Documentation URL | `https://github.com/praveenlavu/spec-kit-ghp/blob/v0.1.0/README.md` |
| Changelog URL | `https://github.com/praveenlavu/spec-kit-ghp/blob/v0.1.0/CHANGELOG.md` |
| Required Spec Kit Version | `>=1.2.1` |
| Number of Commands | `13` |
| Number of Hooks | `2` |
| Tags | `github-projects, issue-tracking, hitl, workflow, governance` |

**Required Tools**

```markdown
- ghp (>=0.1.0) - required; the command-line program from this repository: pipx install git+https://github.com/praveenlavu/spec-kit-ghp@v0.1.0
- git - required
- gh - required; every GitHub call goes through the GitHub CLI, signed in with the project scope
```

**Key Features**

```markdown
- GitHub Issues and user-owned GitHub Projects only; organisation Projects are refused
- Pulls a story issue into a per-story checklist file with its acceptance criteria copied word for word
- Turns tasks.md into sub-issues on the story and cards on the Project; a dry run unless --apply is given
- Every GitHub write is a preview, a person's yes, then an apply of exactly the previewed plan; a changed plan is refused
- A check before review that every acceptance criterion has a named test, plus an optional strict mode that re-runs the approved test command
- Opens the pull request with "Closes #N" and closes the story after the merge, each after an explicit yes
- Optional approvals by issue comment from approvers listed on the target branch; the agent's own comments never count
- ghp doctor and ghp status: setup health and the next step for a story
- Optional ghp-lifecycle workflow (filed separately) with a gate before every GitHub write
- Standard library only; ghp never reads a token (gh signs in)
```

**Testing Checklist** (tick each only after doing it against the published release)

- [ ] Extension installs successfully via download URL
- [ ] All commands execute without errors
- [ ] Documentation is complete and accurate
- [ ] No security vulnerabilities identified
- [ ] Tested on at least one real project

**Submission Requirements**

- [ ] Valid `extension.yml` manifest included
- [ ] README.md with installation and usage instructions
- [ ] LICENSE file included
- [ ] GitHub release created with version tag
- [ ] All command files exist and are properly formatted
- [ ] Extension ID follows naming conventions (lowercase-with-hyphens)

**Testing Details** (**TO CONFIRM**: fill in what was actually run, from the release evidence file)

```markdown
**Tested on:**
- TO CONFIRM: operating system and version, with Spec Kit 1.2.1 and with the newest Spec Kit

**Test project:** TO CONFIRM: a clean Spec Kit project connected to a test repository and a test Project owned by a user account

**Test scenarios:**
1. Installed the ghp program with pipx from the v0.1.0 tag
2. Installed the extension from the download URL; TO CONFIRM: 13 commands and 2 hooks shown by specify extension list
3. Ran /speckit.ghp.setup and /speckit.ghp.pull on a test story
4. Ran ghp push and ghp done without --apply and saw no write; applied after review
5. Ran the offline test suite: TO CONFIRM: the passed and skipped counts
```

**Example Usage**

```bash
# Install the program and the extension
pipx install git+https://github.com/praveenlavu/spec-kit-ghp@v0.1.0
specify extension add ghp --from https://github.com/praveenlavu/spec-kit-ghp/releases/download/v0.1.0/spec-kit-ghp-extension-0.1.0.zip

# Connect, then pull a story
ghp setup
/speckit.ghp.pull 42
```

**Proposed Catalog Entry** (the entry of `catalog/extensions.json`; **TO CONFIRM**: add `sha256` once the release file exists)

```json
{
  "ghp": {
    "id": "ghp",
    "name": "Spec Kit for GitHub Projects - human-gated lifecycle",
    "description": "GitHub issue to Done through Spec Kit, with a human sign-off before every write.",
    "author": "praveenlavu",
    "version": "0.1.0",
    "download_url": "https://github.com/praveenlavu/spec-kit-ghp/releases/download/v0.1.0/spec-kit-ghp-extension-0.1.0.zip",
    "repository": "https://github.com/praveenlavu/spec-kit-ghp",
    "homepage": "https://github.com/praveenlavu/spec-kit-ghp",
    "documentation": "https://github.com/praveenlavu/spec-kit-ghp/blob/v0.1.0/README.md",
    "changelog": "https://github.com/praveenlavu/spec-kit-ghp/blob/v0.1.0/CHANGELOG.md",
    "license": "MIT",
    "requires": {
      "speckit_version": ">=1.2.1",
      "tools": [
        {
          "name": "ghp",
          "version": ">=0.1.0",
          "required": true
        },
        {
          "name": "git",
          "required": true
        },
        {
          "name": "gh",
          "required": true
        }
      ]
    },
    "provides": {
      "commands": 13,
      "hooks": 2
    },
    "tags": [
      "github-projects",
      "issue-tracking",
      "hitl",
      "workflow",
      "governance"
    ],
    "verified": false,
    "created_at": "2026-11-12T00:00:00Z",
    "updated_at": "2026-11-12T00:00:00Z"
  }
}
```

**Additional Context**

```markdown
- ghp needs Python 3.9 or later; the manifest cannot say so.
- It is not affiliated with GitHub, Microsoft or the Spec Kit project.
- Not yet checked on real GitHub: the list in docs/trust-and-limits.md, "Unverified in live runs".
```

## 2. Workflow submission

A pull request to `github/spec-kit` that adds the entry below to `workflows/catalog.community.json`, sorted by id.

| Field | Value |
|---|---|
| Pull request title | `Add ghp-lifecycle to community catalog` |
| Branch | `add-ghp-lifecycle` |
| Workflow ID | `ghp-lifecycle` |
| Workflow Name | `GitHub issue lifecycle` |
| Version | `0.1.0` |
| Repository | `https://github.com/praveenlavu/spec-kit-ghp` |
| Raw workflow URL | `https://raw.githubusercontent.com/praveenlavu/spec-kit-ghp/v0.1.0/workflows/ghp-lifecycle/workflow.yml` |

**Catalog entry** (the entry of `catalog/workflows.json`)

```json
{
  "ghp-lifecycle": {
    "id": "ghp-lifecycle",
    "name": "GitHub issue lifecycle",
    "description": "GitHub story issue to an open PR: spec, plan, tasks, push, implement, gate, review, PR. Every GitHub write is preview, human gate, apply. The story closes after merge.",
    "author": "praveenlavu",
    "version": "0.1.0",
    "url": "https://raw.githubusercontent.com/praveenlavu/spec-kit-ghp/v0.1.0/workflows/ghp-lifecycle/workflow.yml",
    "repository": "https://github.com/praveenlavu/spec-kit-ghp",
    "documentation": "https://github.com/praveenlavu/spec-kit-ghp/blob/v0.1.0/workflows/ghp-lifecycle/README.md",
    "changelog": "https://github.com/praveenlavu/spec-kit-ghp/blob/v0.1.0/workflows/ghp-lifecycle/CHANGELOG.md",
    "license": "MIT",
    "requires": {
      "speckit_version": ">=1.2.1",
      "integrations": {
        "any": [
          "claude",
          "copilot",
          "gemini",
          "opencode"
        ]
      }
    },
    "tags": [
      "github-projects",
      "workflow",
      "governance"
    ],
    "created_at": "2026-11-12T00:00:00Z",
    "updated_at": "2026-11-12T00:00:00Z"
  }
}
```

**Pull request body**

```markdown
## Workflow Submission

**Workflow Name**: GitHub issue lifecycle
**Workflow ID**: ghp-lifecycle
**Version**: 0.1.0
**Repository**: https://github.com/praveenlavu/spec-kit-ghp

### Checklist
- [ ] Valid workflow.yml (passes `specify workflow info`)
- [ ] README.md with description, inputs, and step graph
- [ ] LICENSE file included
- [ ] GitHub release created with raw YAML URL
- [ ] Workflow tested end-to-end with `specify workflow run`
- [ ] All gate steps have clear review messages
- [ ] Input prompts are descriptive
- [ ] Added to workflows/catalog.community.json (alphabetical order)

The shell steps call the ghp program, which reaches GitHub through the GitHub CLI and its sign-in; the workflow README says so. Only the numeric issue number is put into a shell command, and each step checks it first.
```

Tick "tested end-to-end" only after a real run (**TO CONFIRM**: none has been made yet).

## 3. Bundle submission

| Field | Value |
|---|---|
| Issue title | `[Bundle]: Add ghp` |
| Bundle ID | `ghp` |
| Bundle Name | `Spec Kit for GitHub Projects` |
| Version | `0.1.0` |
| Role or Team | `developer` |
| Description | `The ghp extension and the ghp-lifecycle workflow together: GitHub issue to Done through Spec Kit, with a human sign-off before every write.` |
| Author | `praveenlavu` |
| Repository URL | `https://github.com/praveenlavu/spec-kit-ghp` |
| Download URL | `https://github.com/praveenlavu/spec-kit-ghp/releases/download/v0.1.0/ghp-0.1.0.zip` |
| Documentation URL | `https://github.com/praveenlavu/spec-kit-ghp/blob/v0.1.0/bundle/README.md` |
| License | `MIT` |
| Required Spec Kit Version | `>=1.2.1` |
| Integration Target | leave empty: the bundle pins no integration |
| Tags | `github-projects, workflow, governance` |

**Components Provided**

```markdown
- extensions: ghp@0.1.0
- workflows: ghp-lifecycle@0.1.0
```

**Required Component Catalogs**

```markdown
- Extensions: https://raw.githubusercontent.com/praveenlavu/spec-kit-ghp/main/catalog/extensions.json
- Workflows: https://raw.githubusercontent.com/praveenlavu/spec-kit-ghp/main/catalog/workflows.json
```

**Key Features**

```markdown
- Installs the ghp extension (13 slash commands, 2 hooks) and the ghp-lifecycle workflow in one step
- Both parts pinned to 0.1.0, the same version as the ghp program they call
- Every GitHub write in the workflow is a preview, a gate, then an apply
```

**Testing Checklist** (tick each only after doing it against the published release)

- [ ] Validation succeeds with `specify bundle validate --path <bundle-directory>`
- [ ] Build succeeds with `specify bundle build --path <bundle-directory>` and produces the submitted artifact
- [ ] Bundle installs successfully from the built artifact
- [ ] The submitted distribution path was tested end to end, including bundle-ID installation from an install-allowed catalog when a catalog entry is proposed
- [ ] Installation was tested in a clean Spec Kit project
- [ ] Required component catalogs are documented and were included in testing, or no extra catalogs are required
- [ ] Documentation is complete and accurate

**Submission Requirements**

- [ ] Valid `bundle.yml` manifest included
- [ ] README.md explains the bundle's intended role, installed components, and installation steps
- [ ] LICENSE file included
- [ ] GitHub release created with a version tag
- [ ] Bundle ID matches the manifest and follows naming conventions
- [ ] Every extension, preset, workflow, and step reference is pinned where the manifest requires a version

**Testing Details** (**TO CONFIRM**: fill in what was actually run, from the release evidence file)

```markdown
**Tested on:**
- TO CONFIRM: operating system and version, with Spec Kit 1.2.1 and with the newest Spec Kit

**Test project:** TO CONFIRM: a clean Spec Kit project

**Test scenarios:**
1. Added the two component catalogs from the v0.1.0 tag
2. Validated the bundle manifest
3. Built the release file and compared it with the attached ghp-0.1.0.zip
4. Installed the bundle from that file in a clean project
5. Installed the bundle by its id from a local install-allowed bundle catalog
6. TO CONFIRM: listed the installed extension and workflow
```

**Example Usage**

```bash
# The program the extension and the workflow call
pipx install git+https://github.com/praveenlavu/spec-kit-ghp@v0.1.0

# The two component catalogs
specify extension catalog add https://raw.githubusercontent.com/praveenlavu/spec-kit-ghp/main/catalog/extensions.json --name ghp --install-allowed
specify workflow catalog add https://raw.githubusercontent.com/praveenlavu/spec-kit-ghp/main/catalog/workflows.json --name ghp

# The bundle file
curl -L -o ghp-0.1.0.zip https://github.com/praveenlavu/spec-kit-ghp/releases/download/v0.1.0/ghp-0.1.0.zip
specify bundle install ./ghp-0.1.0.zip

# Run one story
specify workflow run ghp-lifecycle -i issue_number=42
```

**Proposed Catalog Entry**

```json
{
  "ghp": {
    "id": "ghp",
    "name": "Spec Kit for GitHub Projects",
    "version": "0.1.0",
    "role": "developer",
    "description": "The ghp extension and the ghp-lifecycle workflow together: GitHub issue to Done through Spec Kit, with a human sign-off before every write.",
    "author": "praveenlavu",
    "license": "MIT",
    "download_url": "https://github.com/praveenlavu/spec-kit-ghp/releases/download/v0.1.0/ghp-0.1.0.zip",
    "repository": "https://github.com/praveenlavu/spec-kit-ghp",
    "requires": {
      "speckit_version": ">=1.2.1"
    },
    "provides": {
      "extensions": 1,
      "presets": 0,
      "steps": 0,
      "workflows": 1
    },
    "tags": [
      "github-projects",
      "workflow",
      "governance"
    ],
    "verified": false
  }
}
```

## What happens when someone files them

None of this has been done. In order:

1. The owner makes the repository public, creates the `v0.1.0` release and attaches the extension file and the bundle file to it (the steps are in `docs/release-checklist.md`). The checksum of the extension file is then added to the extension entry.
2. The extension is filed through Spec Kit's extension submission issue form, with the values in section 1. Spec Kit's guide says not to edit the catalog file with a pull request for an extension.
3. The workflow is filed as a pull request, with the values in section 2.
4. The bundle is filed through Spec Kit's bundle submission issue form, with the values in section 3.
5. Each filing is made by the owner, from the owner's own account. An agent never files one.

## Not yet checked for the catalog

- **The catalog format.** The entries and the form fields follow the catalog files, publishing guides and issue forms in a copy of Spec Kit's repository kept on the author's machine. They were not compared with the live repository, which may have changed since.
- **How each entry is filed.** The issue-form route for extensions, the pull-request route for workflows and the issue-form route for bundles come from the same copy of the guides. They have not been checked against the live repository.
- **The release file.** No release exists, so the download address (a file attached to the release, not GitHub's automatic archive of the tag), the raw workflow address, the catalog addresses and the checksum are all untested.
- **Installing from the release.** `specify extension add` from the release file, and `specify workflow add` from the raw workflow address, have not been tried. The extension file built by the script installed from a local address in a test, with 13 commands and 2 hooks.
- **The bundle.** Its structure was read with the bundle reader of Spec Kit 1.2.4.dev0 and found valid. It has not been built from the tag, installed or looked up in a catalog.
- **The bundle file and its catalogs.** The bundle form asks for a built bundle file, its address and the catalogs its parts come from. The catalogs are served from the tag, which does not exist yet.
- **Optional fields.** The entry has no category or effect. Some entries in the catalog have them, and the maintainers may add or ask for them.
- **The bundle role.** The role text `developer` is a choice, copied from an example in Spec Kit's own catalog.
- **The `ghp` program is not in the bundle.** The extension needs the `ghp` command-line tool, which is installed from this repository, not from Spec Kit. Whether the maintainers accept an entry that needs a separate install is not known.
- **Distribution name.** The owner selected `spec-kit-ghp` for the Python distribution; the console command remains `ghp`. Availability on a package index was not checked.
- **Tags and wording.** The tags and descriptions are the author's choice, and the maintainers may ask for changes.

## Not yet checked on real GitHub

These are the same items as the "Unverified in live runs" list on the [trust page](trust-and-limits.md#unverified-in-live-runs). A catalog reader should see them before installing.

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
- **Automation delay at Done.** How long the board automation can take; `ghp done` waits up to 30 seconds.
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
- **Board and strict checks on GitHub and Actions.** The board check, `strict verify` and `strict check` on real GitHub and in Actions.
- **The reusable workflows in a consumer repository.** The four ready-made workflows have not yet run in another repository on GitHub's hosted runners. Still unverified there: the read-only token of a pull request from a fork, the re-run when a pull request's target branch is changed (the `edited` event), and how the optional secret reaches the job.
- **The Spec Kit minimum version.** Spec Kit 1.2.1, the oldest supported version, has not been tried. The contract tests ran against 1.2.4.dev0 only.
