# Catalog submission (DRAFT)

**DRAFT. Nothing is filed with Spec Kit.** This page holds the values to paste into Spec Kit's three submission routes on the day someone decides to list `ghp` in Spec Kit's community catalogs.
Whether to publish is the owner's decision, and it has not been made. The release `v0.1.0` exists, and its addresses were installed from it (see the Testing Details of each form). The values below are for 0.1.1; the same installs are re-run on the `v0.1.1` release after it exists.
Boxes are ticked only for what was done against the published 0.1.0 release; every box left empty is a judgement for the person filing.

**Two different numbers.** The catalog entry counts the extension's **slash commands**, from `extension.yml`: 13 commands (`speckit.ghp.*`) and 2 hooks. The `ghp` program is a separate thing with 21 commands of its own (`ghp --help`); those are not counted in any form.

**Two different files on the release.** The extension file is `spec-kit-ghp-extension-0.1.1.zip`, made by `python3 scripts/build_extension_zip.py <dir>`. The bundle file is `ghp-0.1.1.zip`, made by `specify bundle build` (its name is always the bundle id and its version). Both are attached to the `v0.1.1` release.

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

The entries keep `"verified": false`: only the Spec Kit maintainers can verify an entry. The extension entry gets the SHA-256 of the file attached to the release (the same value as its line in `SHA256SUMS`) once that file exists. The download address is a file attached to the release, not GitHub's automatic archive of the tag. The dates are the release date, 2026-11-12, the same as the changelog.

## 1. Extension submission

| Field | Value |
|---|---|
| Issue title | `[Extension]: Add ghp` |
| Extension ID | `ghp` |
| Extension Name | `Spec Kit for GitHub Projects - human-gated lifecycle` |
| Version | `0.1.1` |
| Description | `GitHub issue to Done through Spec Kit, with a human sign-off before every write.` |
| Author | `praveenlavu` |
| Repository URL | `https://github.com/praveenlavu/spec-kit-ghp` |
| Download URL | `https://github.com/praveenlavu/spec-kit-ghp/releases/download/v0.1.1/spec-kit-ghp-extension-0.1.1.zip` |
| License | `MIT` |
| Homepage | `https://github.com/praveenlavu/spec-kit-ghp` |
| Documentation URL | `https://github.com/praveenlavu/spec-kit-ghp/blob/v0.1.1/README.md` |
| Changelog URL | `https://github.com/praveenlavu/spec-kit-ghp/blob/v0.1.1/CHANGELOG.md` |
| Required Spec Kit Version | `>=1.2.1` |
| Number of Commands | `13` |
| Number of Hooks | `2` |
| Tags | `github-projects, issue-tracking, hitl, workflow, governance` |

**Required Tools**

```markdown
- ghp (>=0.1.1) - required; the command-line program from this repository: pipx install git+https://github.com/praveenlavu/spec-kit-ghp@v0.1.1
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

- [x] Extension installs successfully via download URL
- [ ] All commands execute without errors
- [ ] Documentation is complete and accurate
- [ ] No security vulnerabilities identified
- [ ] Tested on at least one real project

**Submission Requirements**

- [x] Valid `extension.yml` manifest included
- [x] README.md with installation and usage instructions
- [x] LICENSE file included
- [x] GitHub release created with version tag
- [x] All command files exist and are properly formatted
- [x] Extension ID follows naming conventions (lowercase-with-hyphens)

**Testing Details** (from what was run against the 0.1.0 release on 2026-11-12; the same installs are re-run on 0.1.1 after its release)

```markdown
**Tested on:**
- macOS (Darwin 27.2.0), with Spec Kit 1.2.1 (the minimum) and Spec Kit 1.4.0 (the newest release that day)

**Test project:** a new empty folder where `specify init` ran, for each Spec Kit version; the live scenarios ran against a private test repository and a test Project owned by a user account

**Test scenarios:**
1. Ran the ghp program from the v0.1.0 tag with uv (`ghp --version` printed 0.1.0); the pipx command was not run
2. Installed the extension from the download URL on both Spec Kit versions, and again by a pinned install from the catalog and through the bundle: 13 commands and 2 hooks shown by `specify extension list` each time
3. Ran the ghp-lifecycle workflow on a test story, from the pull to an open pull request, with every GitHub write previewed, approved at a gate, then applied (terminal mode only)
4. Ran ghp push with a plan that had gone stale and saw it refuse to write
5. Ran the offline test suite inside the public export: 7255 passed, 120 skipped
```

**Example Usage**

```bash
# Install the program and the extension
pipx install git+https://github.com/praveenlavu/spec-kit-ghp@v0.1.1
specify extension add ghp --from https://github.com/praveenlavu/spec-kit-ghp/releases/download/v0.1.1/spec-kit-ghp-extension-0.1.1.zip

# Connect, then pull a story
ghp setup
/speckit.ghp.pull 42
```

**Proposed Catalog Entry** (the entry of `catalog/extensions.json`)

```json
{
  "ghp": {
    "id": "ghp",
    "name": "Spec Kit for GitHub Projects - human-gated lifecycle",
    "description": "GitHub issue to Done through Spec Kit, with a human sign-off before every write.",
    "author": "praveenlavu",
    "version": "0.1.1",
    "download_url": "https://github.com/praveenlavu/spec-kit-ghp/releases/download/v0.1.1/spec-kit-ghp-extension-0.1.1.zip",
    "sha256": "24f87a15f8ce3b21db462f1599cebe9d087ab7e3ca71030e36efc97c8cd77488",
    "repository": "https://github.com/praveenlavu/spec-kit-ghp",
    "homepage": "https://github.com/praveenlavu/spec-kit-ghp",
    "documentation": "https://github.com/praveenlavu/spec-kit-ghp/blob/v0.1.1/README.md",
    "changelog": "https://github.com/praveenlavu/spec-kit-ghp/blob/v0.1.1/CHANGELOG.md",
    "license": "MIT",
    "requires": {
      "speckit_version": ">=1.2.1",
      "tools": [
        {
          "name": "ghp",
          "version": ">=0.1.1",
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
| Version | `0.1.1` |
| Repository | `https://github.com/praveenlavu/spec-kit-ghp` |
| Raw workflow URL | `https://raw.githubusercontent.com/praveenlavu/spec-kit-ghp/v0.1.1/workflows/ghp-lifecycle/workflow.yml` |

**Catalog entry** (the entry of `catalog/workflows.json`)

```json
{
  "ghp-lifecycle": {
    "id": "ghp-lifecycle",
    "name": "GitHub issue lifecycle",
    "description": "GitHub story issue to an open PR: spec, plan, tasks, push, implement, gate, review, PR. Every GitHub write is preview, human gate, apply. The story closes after merge.",
    "author": "praveenlavu",
    "version": "0.1.1",
    "url": "https://raw.githubusercontent.com/praveenlavu/spec-kit-ghp/v0.1.1/workflows/ghp-lifecycle/workflow.yml",
    "repository": "https://github.com/praveenlavu/spec-kit-ghp",
    "documentation": "https://github.com/praveenlavu/spec-kit-ghp/blob/v0.1.1/workflows/ghp-lifecycle/README.md",
    "changelog": "https://github.com/praveenlavu/spec-kit-ghp/blob/v0.1.1/workflows/ghp-lifecycle/CHANGELOG.md",
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
**Version**: 0.1.1
**Repository**: https://github.com/praveenlavu/spec-kit-ghp

### Checklist
- [x] Valid workflow.yml (passes `specify workflow info`)
- [x] README.md with description, inputs, and step graph
- [x] LICENSE file included
- [x] GitHub release created with raw YAML URL
- [x] Workflow tested end-to-end with `specify workflow run`
- [ ] All gate steps have clear review messages
- [ ] Input prompts are descriptive
- [ ] Added to workflows/catalog.community.json (alphabetical order)

The shell steps call the ghp program, which reaches GitHub through the GitHub CLI and its sign-in; the workflow README says so. Only the numeric issue number is put into a shell command, and each step checks it first.
```

The end-to-end run was made once, in terminal mode only, on a private test repository, from a story to an open pull request; the gate answers were typed by an AI agent that had read each preview, so it is not a claim of independent human review. The lifecycle also installed from the raw workflow address and by a pinned catalog install on both Spec Kit versions.

## 3. Bundle submission

| Field | Value |
|---|---|
| Issue title | `[Bundle]: Add ghp` |
| Bundle ID | `ghp` |
| Bundle Name | `Spec Kit for GitHub Projects` |
| Version | `0.1.1` |
| Role or Team | `developer` |
| Description | `The ghp extension and the ghp-lifecycle workflow together: GitHub issue to Done through Spec Kit, with a human sign-off before every write.` |
| Author | `praveenlavu` |
| Repository URL | `https://github.com/praveenlavu/spec-kit-ghp` |
| Download URL | `https://github.com/praveenlavu/spec-kit-ghp/releases/download/v0.1.1/ghp-0.1.1.zip` |
| Documentation URL | `https://github.com/praveenlavu/spec-kit-ghp/blob/v0.1.1/bundle/README.md` |
| License | `MIT` |
| Required Spec Kit Version | `>=1.2.1` |
| Integration Target | leave empty: the bundle pins no integration |
| Tags | `github-projects, workflow, governance` |

**Components Provided**

```markdown
- extensions: ghp@0.1.1
- workflows: ghp-lifecycle@0.1.1
```

**Required Component Catalogs**

```markdown
- Extensions: https://raw.githubusercontent.com/praveenlavu/spec-kit-ghp/main/catalog/extensions.json
- Workflows: https://raw.githubusercontent.com/praveenlavu/spec-kit-ghp/main/catalog/workflows.json
```

**Key Features**

```markdown
- Installs the ghp extension (13 slash commands, 2 hooks) and the ghp-lifecycle workflow in one step
- Both parts pinned to 0.1.1, the same version as the ghp program they call
- Every GitHub write in the workflow is a preview, a gate, then an apply
```

**Testing Checklist** (tick each only after doing it against the published release)

- [x] Validation succeeds with `specify bundle validate --path <bundle-directory>`
- [x] Build succeeds with `specify bundle build --path <bundle-directory>` and produces the submitted artifact
- [x] Bundle installs successfully from the built artifact
- [x] The submitted distribution path was tested end to end, including bundle-ID installation from an install-allowed catalog when a catalog entry is proposed
- [x] Installation was tested in a clean Spec Kit project
- [x] Required component catalogs are documented and were included in testing, or no extra catalogs are required
- [ ] Documentation is complete and accurate

**Submission Requirements**

- [x] Valid `bundle.yml` manifest included
- [x] README.md explains the bundle's intended role, installed components, and installation steps
- [x] LICENSE file included
- [x] GitHub release created with a version tag
- [x] Bundle ID matches the manifest and follows naming conventions
- [x] Every extension, preset, workflow, and step reference is pinned where the manifest requires a version

**Testing Details** (from what was run against the 0.1.0 release on 2026-11-12; the same installs are re-run on 0.1.1 after its release)

```markdown
**Tested on:**
- macOS (Darwin 27.2.0), with Spec Kit 1.2.1 (the minimum) and Spec Kit 1.4.0 (the newest release that day)

**Test project:** a new empty folder where `specify init` ran, with the two component catalogs added

**Test scenarios:**
1. Added the two component catalogs, served from `main`
2. Validated the bundle manifest (it validates only with those catalogs added to the project)
3. Built the release file from a clean clone of the tag and compared it with the attached ghp-0.1.0.zip: identical
4. Installed the bundle from the downloaded release file in a clean project
5. Installed the bundle by its id from a local install-allowed bundle catalog (a `file://` address)
6. Listed the installed extension: 13 commands and 2 hooks
```

**Example Usage**

```bash
# The program the extension and the workflow call
pipx install git+https://github.com/praveenlavu/spec-kit-ghp@v0.1.1

# The two component catalogs
specify extension catalog add https://raw.githubusercontent.com/praveenlavu/spec-kit-ghp/main/catalog/extensions.json --name ghp --install-allowed
specify workflow catalog add https://raw.githubusercontent.com/praveenlavu/spec-kit-ghp/main/catalog/workflows.json --name ghp

# The bundle file
curl -L -o ghp-0.1.1.zip https://github.com/praveenlavu/spec-kit-ghp/releases/download/v0.1.1/ghp-0.1.1.zip
specify bundle install ./ghp-0.1.1.zip

# Run one story
specify workflow run ghp-lifecycle -i issue_number=42
```

**Proposed Catalog Entry**

```json
{
  "ghp": {
    "id": "ghp",
    "name": "Spec Kit for GitHub Projects",
    "version": "0.1.1",
    "role": "developer",
    "description": "The ghp extension and the ghp-lifecycle workflow together: GitHub issue to Done through Spec Kit, with a human sign-off before every write.",
    "author": "praveenlavu",
    "license": "MIT",
    "download_url": "https://github.com/praveenlavu/spec-kit-ghp/releases/download/v0.1.1/ghp-0.1.1.zip",
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

Step 1 was done for 0.1.0 and is repeated for 0.1.1 after its release; nothing has been filed. In order:

1. The repository is public, the `v0.1.1` release carries the extension file, the bundle file and `SHA256SUMS`, and the checksum of the extension file is in the extension entry.
2. The extension is filed through Spec Kit's extension submission issue form, with the values in section 1. Spec Kit's guide says not to edit the catalog file with a pull request for an extension.
3. The workflow is filed as a pull request, with the values in section 2.
4. The bundle is filed through Spec Kit's bundle submission issue form, with the values in section 3.
5. Each filing is made by the owner, from the owner's own account. An agent never files one.

## Not yet checked for the catalog

- **The catalog format.** The entries and the form fields follow the catalog files, publishing guides and issue forms in a copy of Spec Kit's repository kept on the author's machine. They were not compared with the live repository, which may have changed since.
- **How each entry is filed.** The issue-form route for extensions, the pull-request route for workflows and the issue-form route for bundles come from the same copy of the guides. They have not been checked against the live repository.
- **The release file.** Checked on the 0.1.0 release: the download address (a file attached to the release, not GitHub's automatic archive of the tag), the raw workflow address and the two catalog addresses all answer, and the file matches its checksum. Not checked: the catalogs as served to someone who has them cached for a long time.
- **Installing from the release.** `specify extension add` from the release file and `specify workflow add` from the raw workflow address were run with the 0.1.0 release on Spec Kit 1.2.1 and 1.4.0. `specify extension add --from` stops at a trust question when no one is at the terminal; it was answered yes.
- **The bundle.** For 0.1.0: validated, built from a clean clone of the tag, installed from the release file and installed by its id from a local catalog, on both versions. Not checked: a bundle catalog served over the network (the local one used a `file://` address).
- **The bundle file and its catalogs.** The two component catalogs are served from `main`, so a change there reaches installers without a new tag.
- **Optional fields.** The entry has no category or effect. Some entries in the catalog have them, and the maintainers may add or ask for them.
- **The bundle role.** The role text `developer` is a choice, copied from an example in Spec Kit's own catalog.
- **The `ghp` program is not in the bundle.** The extension needs the `ghp` command-line tool, which is installed from this repository, not from Spec Kit. Whether the maintainers accept an entry that needs a separate install is not known.
- **Distribution name.** The owner selected `spec-kit-ghp` for the Python distribution; the console command remains `ghp`. Availability on a package index was not checked.
- **Tags and wording.** The tags and descriptions are the author's choice, and the maintainers may ask for changes.

## Not yet checked on real GitHub

These are the same items as the "Unverified in live runs" list on the [trust page](trust-and-limits.md#unverified-in-live-runs). A catalog reader should see them before installing.

- **Two-person approval.** Approval was tested with one person and one bot, not with two people.
- **A comment deleted by another writer.** Only a comment deleted by its own author was observed.
- **The exact size cap of a comment.** Comments of 65,537 and 100,000 characters were accepted, and one of 262,145 characters was refused (HTTP 422). Where the limit lies in between, and whether it counts characters or bytes, is unknown. `ghp` refuses comments above 60,000 characters.
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
- **The comment join race.** A comment posted, changed or deleted between `ghp`'s two reads of the comment list is dropped, never counted.
- **The newest 300 issues.** That the newest 300 issues always hold every task of an earlier push on a busy repository.
- **Labels set by an issue template.** An issue template that adds the `ghp:task` label makes anyone's issue look like a task.
- **The sub-issue summary in GraphQL.** The exact shape of GitHub's sub-issue counts.
- **An open pull request in the closing references.** That GitHub lists an open pull request with `Closes #12` as a closing reference before it is merged.
- **A closer commit in a fork network.** Which repository GitHub reports for a closing commit that came from a fork.
- **Fork and other-repository refusals of `ghp done`.** `ghp done` refuses a pull request named with `--pr` that comes from a fork or belongs to another repository, and a story closed by a commit of another repository; those refusals have not run against real GitHub.
- **Board and strict checks on GitHub and Actions.** The board check and `strict check` on real GitHub and in Actions. `strict verify` ran once in Actions in another repository, on a pull request where it failed; a passing run in Actions has not been tried.
- **The reusable workflows in a consumer repository.** The four ready-made workflows have run once in another repository on GitHub's hosted runners, pinned to the 0.1.0 release commit, by an AI agent on a public test repository: a passing gate, a failing gate, a pull request whose own config file is ignored, and link-on-merge without the `GHP_PROJECT_TOKEN` secret. Not run there: link-on-merge with that secret, a merge into a branch other than the default one, pull requests from forks (and their read-only token), a private consumer repository, and the re-run when a pull request's target branch is changed (the `edited` event).
- **The Spec Kit minimum version.** The installs were run on Spec Kit 1.2.1 and 1.4.0. The contract tests and the live lifecycle run used 1.2.4.dev0 only, so running the workflow on 1.2.1 or 1.4.0 has not been tried.
