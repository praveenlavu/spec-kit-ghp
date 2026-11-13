# Compatibility

What `ghp` needs, and what it has actually been tried on. A version listed as "tried" was run; anything else is not claimed.

## Tools

| Tool | Needs | Tried on |
|---|---|---|
| Python | 3.9 or later. Only the standard library: `ghp` installs nothing else. | 3.9.6 on macOS (the full test suite). The hosted CI runs 3.9 and 3.13; both passed in hosted run 42861951070. |
| git | Any recent version. | The version on the author's macOS machine. |
| GitHub CLI (`gh`) | Signed in with `gh auth login`. `ghp` sends every GitHub call through it. | `gh` 2.96.0 on the author's machine, where the live runs ran (the run records do not name the version). |
| Spec Kit (`specify`) | 1.2.1 or later, for the extension and the workflow. | The installs (the extension, the workflow and the bundle) on 1.2.1 and 1.4.0. The workflow runs and the contract tests on 1.2.4.dev0 only. |

## GitHub features used

- **GitHub Issues**, including **sub-issues** (an issue under another issue). Each task becomes a sub-issue of its story.
- **GitHub Projects** owned by a **user account** (the newer Projects, sometimes called Projects v2). `ghp` reads the board and sets each card's "Status".
- **Issue comments**, for the checklist summary at the end and, if you turn it on, approval by comment.
- **Pull requests**, opened with GitHub's REST API.
- **GitHub Actions**, only if you use the [ready-made workflows](ci-github.md).

## Not supported, or not tried

| What | State |
|---|---|
| Projects owned by an organisation | Not supported: refused with `E_ORG_PROJECT_UNSUPPORTED`. |
| GitHub Enterprise Server | UNVERIFIED. Never tried. |
| Windows | UNVERIFIED. Never tried. |
| Fine-grained personal access tokens | Not supported for Projects: GitHub does not let them use user Projects. |
| The older "Projects (classic)" | Not supported. |

See [Trust and limits](trust-and-limits.md#unverified-in-live-runs) for the behaviours that have not been checked on real GitHub.
