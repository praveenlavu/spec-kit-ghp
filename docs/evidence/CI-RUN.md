# Hosted CI evidence: the first fully green run

This repository's own `ci.yml` workflow ("Tests") on GitHub's hosted runners.

- **Run.** 42861951070.
- **Commit.** `bf48d35` (`main`).
- **Jobs.** `Python 3.9` (tests, the secret and private-name check without a list, the package build): success. `Python 3.13` (the same steps): success. `Lint the workflows` (actionlint 1.7.12, digest checked): success.
- **Conclusion.** success.
- **Earlier run.** Run 42834452605 failed only in actionlint (`ghp-link-on-merge.yml`, shellcheck SC1010), fixed in `104bd2a`.

Not covered by this run: the reusable workflows in a consumer repository, the Spec Kit contract on the oldest and newest supported versions, and the private-name check with the owner's list. Those stay in the unverified list.
