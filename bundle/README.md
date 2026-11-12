# ghp bundle

**Not published yet.** No release exists: the `v0.1.0` tag, the release files and the catalog addresses below do not exist until the owner makes the release. Nothing here has been installed from a release.

The bundle sets up a Spec Kit project to run GitHub issues through Spec Kit with a person's approval before every GitHub write. It is for a developer who keeps stories as GitHub issues and tracks them on a GitHub Project owned by their user account.

## What it installs

| Component | Id | Version | What it is |
|---|---|---|---|
| Extension | `ghp` | 0.1.0 | The 13 `speckit.ghp.*` slash commands and 2 optional hooks. |
| Workflow | `ghp-lifecycle` | 0.1.0 | One story from spec to an open pull request, with a gate before every GitHub write. |

It does **not** install the `ghp` command-line program, which the commands and the workflow's steps call. Install that from the same tag first (step 1). The bundle, the extension, the workflow and the program carry the same version; mixing versions is not supported. You need Spec Kit 1.2.1 or later (only 1.2.4.dev0 has been tried, see [compatibility](../docs/compatibility.md)), Python 3.9 or later, `git`, and the GitHub CLI `gh`, signed in.

## Install

1. Install the `ghp` program from the release tag, then run `ghp setup` in your own terminal:

   ```bash
   pipx install git+https://github.com/praveenlavu/spec-kit-ghp@v0.1.0
   ```

2. Add the two component catalogs. The bundle pins `ghp@0.1.0` and `ghp-lifecycle@0.1.0`. Spec Kit's community catalogs are for discovery only, so `specify bundle install` finds the two parts in these catalogs, which this repository serves from its `main` branch, so the checksum added after the tag reaches you:

   ```bash
   specify extension catalog add https://raw.githubusercontent.com/praveenlavu/spec-kit-ghp/main/catalog/extensions.json --name ghp --install-allowed
   specify workflow catalog add https://raw.githubusercontent.com/praveenlavu/spec-kit-ghp/main/catalog/workflows.json --name ghp
   ```

   An install-allowed catalog installs what it lists without the untrusted-source question. These two list only the extension and the workflow at version 0.1.0, each pointing at its `v0.1.0` release file. Read them, and the [workflow file](https://github.com/praveenlavu/spec-kit-ghp/blob/v0.1.0/workflows/ghp-lifecycle/workflow.yml), before you add them.

3. Install the bundle from the release file. It installs the extension and the workflow:

   ```bash
   curl -L -o ghp-0.1.0.zip https://github.com/praveenlavu/spec-kit-ghp/releases/download/v0.1.0/ghp-0.1.0.zip
   specify bundle install ./ghp-0.1.0.zip
   ```

   Without the catalogs, install the two parts on their own instead: `specify extension add ghp --from https://github.com/praveenlavu/spec-kit-ghp/releases/download/v0.1.0/spec-kit-ghp-extension-0.1.0.zip` and `specify workflow add --dev <path-to-spec-kit-ghp>/workflows/ghp-lifecycle`.

Then run one story:

```bash
specify workflow run ghp-lifecycle -i issue_number=42
```

To build the bundle file yourself: `specify bundle build --path bundle --output <dir>` writes `ghp-0.1.0.zip` (the bundle id and its version). The extension file of the release has a different name, `spec-kit-ghp-extension-0.1.0.zip`, so the two never collide.

## Human gates

Every GitHub write in the workflow is three steps: a preview that writes a plan file, a gate that shows it to you, and an apply that sends exactly that plan. Reject at any gate and the run stops. The run ends at the open pull request; it never closes the story. The story closes after the merge, with `speckit.ghp.done` or the optional link-on-merge workflow. The [workflow page](../workflows/ghp-lifecycle/README.md) lists each step and gate.
