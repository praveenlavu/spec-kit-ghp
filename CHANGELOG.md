# Changelog

All notable changes to `ghp` are listed here, newest first.
The version number rules are in [VERSIONING.md](VERSIONING.md).

## 0.1.0 (2026-11-12)

The first release. It has not been tagged or published: see the [release checklist](docs/release-checklist.md).

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
