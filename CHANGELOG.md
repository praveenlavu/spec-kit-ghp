# Changelog

All notable changes to `ghp` are listed here, newest first.
The version number rules are in [VERSIONING.md](VERSIONING.md).

## 0.1.1 (2026-11-12)

A fix release. Nothing is removed. Read the note on `ghp done` before you upgrade.

Fixed:

- In comment mode, `ghp done` can now close a story. Run `ghp approvals check <n> --gate approve-done` after the approver's `/approve` to record the approval, then `ghp done <n> --apply`. The approver must be another account than the one `ghp` runs as. Link-on-merge still cannot close a story in comment mode: keep `close-story: false` there.
- `ghp gate` reads test ids written in backticks in section 3 of the ledger (`` `path::test_name` ``) the same as plain ones; before, such an acceptance criterion was reported as having no known test.
- The comment `ghp done` posts says when the ticks it counted are not in the ledger file at the merge commit it links, and the plan warns before the yes. **Note for upgraders:** this changes the done plan, so a done plan approved under 0.1.0 is refused after upgrading; show it again with `ghp done <n>` and approve it again.
- The push preview lists the lines of `tasks.md` that look like a task but are not read as one, with what a task line must look like. What is planned and written does not change.
- `ghp push` with a pull request number and no ledger refuses with `E_NOT_AN_ISSUE` instead of advising a command that then refuses too.
- When a comment-mode apply is refused, the refusal says how many `/approve` or `/reject` comments were set aside and why (for example: by an account that is not an approver, edited, or from a bot), in fixed words, never the comment's text.
- `ghp decisions <n> --resolve` without `--note` no longer writes an empty note field. Lines written the old way still read the same.

Changed:

- Every `ghp setup` refusal starts with its error code (`error: E_EXISTS: ...`), like the other commands.
- `ghp status` names `E_ORG_PROJECT_UNSUPPORTED` for a Project owned by an organisation; before the merge its next step is the merge, not a fetch.
- `ghp status` and `ghp activate` explain an open issue whose card is at Done by both causes: the card was moved to Done without closing the issue, or the issue was reopened.
- `ghp doctor` says what is true when GitHub finds no such Project or repository for the sign-in, instead of "GitHub answered HTTP 200 (unexpected)".
- `ghp branch <n>` marks a preview made without a title as not final and names `ghp dod <n>`.
- The `dod_gate` refusal no longer names a release: it says the key was renamed to `gate`.
- The reusable gate workflow names the blocking rule ids in the job log, and its strict check also runs when the gate fails. The actions in every workflow are pinned to their Node 24 releases (`actions/checkout` 5.1.0, `actions/setup-python` 6.3.0).
- The `ghp-lifecycle` workflow's task step, and the review step, end with a short summary instead of a question nobody can answer before the next gate.
- The plan command no longer forbids `ghp approvals check`, a read that never approves.
- Docs: on Spec Kit 1.4.0 with Claude Code the slash commands are named with hyphens (`/speckit-ghp-pull 12`); the repository setting a workflow needs to open a pull request ("Allow GitHub Actions to create and approve pull requests"); only a comment that starts with the request marker makes a step wait; `ghp done` waits about 30 seconds plus the reads (about 35 seconds in the live runs); a `git fetch` between the done preview and the apply changes the plan. The bundle page says the release exists.

Development:

- The live test suite reads every page of its test issues, follows the Project's closing switches, and can target another repository of the owner's account (`GHP_LIVE_REPO`).

## 0.1.0 (2026-11-12)

The first release, tagged `v0.1.0` and published on 2026-11-12. The steps are in the [release checklist](docs/release-checklist.md).

Added:

- The `ghp` command-line tool (21 commands) that turns a Spec Kit task list into GitHub issues and sub-issues on a GitHub Project, one preview and one explicit yes at a time.
- A Spec Kit extension, `ghp`, with 13 slash commands and 2 optional hooks.
- A Spec Kit workflow, `ghp-lifecycle`, from a GitHub story issue to an open pull request, with a human gate before every write.
- Approval by comment, as an option. It is tamper-evident, not tamper-proof: see [Trust and limits](docs/trust-and-limits.md).
- Four ready-made GitHub Actions workflows for a consumer repository: gate, branch guard, strict verify and link on merge.
- A checklist of acceptance criteria per story, checked by `ghp gate` before a pull request.
- Catalog entries and a bundle, ready to file but not filed: see [Catalog submission](docs/catalog-submission.md).

Known limits:

- Only Projects owned by a user account are supported.
- A list of behaviours not yet checked on real GitHub is in [Trust and limits](docs/trust-and-limits.md#unverified-in-live-runs).
