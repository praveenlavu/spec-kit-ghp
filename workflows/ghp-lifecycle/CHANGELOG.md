# Changelog

Changes to the `ghp-lifecycle` workflow. The workflow carries the same version as the `ghp` extension; the whole project's history is in the [repository changelog](../../CHANGELOG.md).

## 0.1.1 (2026-11-12)

- Fixed: the `tasks` step runs its command with `--workflow`, so it, like the review step, ends with a short summary instead of a question nobody can answer before the next gate. The steps, the gates and their order are unchanged.
- Fixed: in comment mode the story can now be closed after the merge with `ghp done`, once `ghp approvals check <n> --gate approve-done` has recorded the approval. The done plan changed, so a done plan approved under 0.1.0 must be shown and approved again.

## 0.1.0 (2026-11-12)

- First version: pull the story, plan, tasks, push the tasks as sub-issues, implement, gate, review and open the pull request. Every GitHub write is a preview, a human gate, then an apply of exactly the previewed plan. The run ends at the open pull request; the story closes after the merge.
