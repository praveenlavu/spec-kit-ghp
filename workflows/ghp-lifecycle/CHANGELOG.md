# Changelog

Changes to the `ghp-lifecycle` workflow. The workflow carries the same version as the `ghp` extension; the whole project's history is in the [repository changelog](../../CHANGELOG.md).

## 0.1.0 (2026-11-12)

- First version: pull the story, plan, tasks, push the tasks as sub-issues, implement, gate, review and open the pull request. Every GitHub write is a preview, a human gate, then an apply of exactly the previewed plan. The run ends at the open pull request; the story closes after the merge.
