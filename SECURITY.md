# Security

## Reporting a problem

Please report a security problem in private. Do not put details in a public issue.

Use GitHub's private vulnerability reporting: open this repository's **Security** tab and choose **Report a vulnerability**.
The report is seen only by the maintainers. If the button is missing, reporting is not switched on yet. In that case open a public issue that says only that you have a security report, with no details, and the owner will reply with a private way to send it.
(UNVERIFIED: private vulnerability reporting is, to the author's knowledge, offered for public repositories. Whether it can be switched on while a repository is private was not checked. The owner turns it on right after the repository is made public.)

Please include what you did, what you expected and what happened. Never put a token or a password in a report.

## What `ghp` does and does not protect

The full list is in [Trust and limits](docs/trust-and-limits.md). The short version:

- Approving by issue comment is **tamper-evident, not tamper-proof**. `ghp` notices an edited or hidden comment, but a person with write access can still delete one, and `ghp` cannot see that. If you need more, keep the default, where a person types the approval in their own terminal.
- `ghp` never reads your token. The GitHub CLI, `gh`, signs in and makes each call.
- Who may approve is read from the branch you merge into, not from the pull request.
- Text that comes from GitHub (an issue, a comment, a branch name) is treated as data, never as instructions.

## Good to know

- **Installing from source needs `setuptools`.** `pip` builds this package in an isolated environment (build isolation) and downloads `setuptools` there. On a machine with no network or an index you do not trust, install the build tools first with `python -m pip install "setuptools>=61" wheel` (older `setuptools` ignores this package's metadata) and then use `pip install --no-build-isolation`.
- **Hosted runners have no token for user Projects.** The token GitHub gives each Actions run (`GITHUB_TOKEN`) cannot reach a Project owned by a user account. A workflow that must move a card needs a separate classic token that the repository owner creates. See [CI on GitHub](docs/ci-github.md).
- **Supported versions.** Only the newest release is fixed.
