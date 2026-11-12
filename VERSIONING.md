# Versioning

`ghp`, the Spec Kit extension, the `ghp-lifecycle` workflow, the catalog entries and the bundle share one version number.
It follows [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`.

## Where the number lives

It is written in these places, and a test (`tests/test_release_checklist.py`) fails if they disagree:

- `src/ghp/__init__.py` (the source of truth; `pyproject.toml` reads it from there)
- `extension.yml`, including the minimum `ghp` version it asks for
- `workflows/ghp-lifecycle/workflow.yml`
- `catalog/extensions.json` and `catalog/workflows.json`
- `bundle/bundle.yml`
- the newest heading in `CHANGELOG.md`

To change the version, change all of them in one commit.

## What a number means

- **PATCH** (`0.1.1`): a fix. Nothing a person relies on changes.
- **MINOR** (`0.2.0`): a new command, option or workflow step. Raising the minimum Spec Kit version is also a MINOR change.
- **MAJOR** (`1.0.0`): something a person relies on is removed or works differently.

Before 1.0, a MINOR release may also change or remove something. The changelog says so when it does.

## Tags

A release is a tag named after the number with a `v` in front, such as `v0.1.0`. The catalog entries point at that tag.
Only the owner pushes a tag. A tag is never moved or reused: a mistake gets a new PATCH number.

## Before a release

While the number is unreleased, the changelog heading says `(unreleased)`. The [release checklist](docs/release-checklist.md) lists every step and who does it.
