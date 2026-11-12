# Release checklist

Every step to release `ghp` 0.1.0, in order, with who does it. This page lists the steps, not their progress. The release is not published. The private repository already exists and `main` is pushed there; progress is written down in the [release evidence file](https://github.com/praveenlavu/spec-kit-ghp/blob/main/docs/evidence/T42.md), not here.

Two repositories take part. **This repository** stays private: the release commit is made and checked here. **The public repository** is created in step 10 from a fresh one-commit export of the release commit (`scripts/export_public.py` makes a new root commit, so its commit id differs from the release commit's). The tag, the GitHub release and its files belong to the public repository, and every commit after the tag is made there and repeated here; the steps from 9 on say which repository they act on.

## Who does what

- **Owner** is the person who owns the repository. Only the owner makes the repository public, creates a secret or a token, adds a permission to their own sign-in, pushes a tag, or files anything with another project. These steps are marked **(owner only)**.
- **Controller** is the coordinating session that runs the checks and makes the commits for the owner. It reads results and writes files. It never types a token, never pushes a tag, never changes who can see the repository and never files an entry with another project.

The `0.1.0` tag is the owner's alone. Every step has one `Who:` line.

## Steps

### 1. Run the whole test suite twice
Who: Controller

Run `python -m pytest -q`, then the same command with `GITHUB_ACTIONS=true CI=true` set, as in [Contributing](../CONTRIBUTING.md). Both must pass.

### 2. Check the one version number
Who: Controller

`python -m pytest -q tests/test_release_checklist.py` must pass. It checks that the version is the same in every file listed in [VERSIONING.md](../VERSIONING.md).

### 3. Run the secret and private-name check without a list
Who: Controller

Run `python3 scripts/scrub_check.py`. It must exit with 0. This is the check anyone can run; the owner's stricter run is a later step.

### 4. Check the catalog pack still matches
Who: Controller

`python -m pytest -q tests/test_catalog.py` must pass, and the "not yet checked" lists in [Catalog submission](catalog-submission.md) and [Trust and limits](trust-and-limits.md) must still be true. Move anything that has since been tried into the verified record.

### 5. Check the private repository and the code on it (owner only)
Who: Owner

The private repository `spec-kit-ghp` under the owner's own personal account should already exist, with `main` pushed to it. Check that it is still private and that `main` is up to date. If it does not exist, create it, then upload `main` with `git push`; if it does, skip the creation. Hosted checks need it. Use the owner's own sign-in; do not share a token.

### 6. Create the GHP_PROJECT_TOKEN secret (owner only)
Who: Owner

Create a classic personal access token with the `repo` and `project` scopes and an expiry date, then save it as the repository secret `GHP_PROJECT_TOKEN`, as [CI on GitHub](ci-github.md) describes. Nobody else types, reads or pastes it.

### 7. Turn off two Project workflows (owner only)
Who: Owner

In the test Project, open the menu, then **Workflows**, and turn off **Auto-close issue** and **Item closed**. There is no API for this. `ghp doctor` shows which ones are on.

### 8. The live lifecycle run (owner only)
Who: Owner

Done for 0.1.0: the owner ran one real story from issue to Done on a test Project in a private repository, typing at each gate, and the record is the [live lifecycle run evidence](https://github.com/praveenlavu/spec-kit-ghp/blob/main/docs/evidence/T34.md). Run it again only if a later change touches the commands or the workflow; the controller then writes the new record and the owner does the typing at each gate.

### 9. Put the date on the changelog
Who: Controller

Change `## 0.1.0 (unreleased)` in `CHANGELOG.md` and in `workflows/ghp-lifecycle/CHANGELOG.md` to the release date, set every `created_at` and `updated_at` in `catalog/extensions.json` and `catalog/workflows.json` to that date, and commit it. This is the release commit, in this repository. Every check below looks at it, and step 10 exports it. If any other commit is added before the tag, make the export again and run the checks again.

### 10. Run the full check with the private list and make the export (owner only)
Who: Owner

On the release commit, put the private word list in `.scrub-denylist` (it stays out of git) and run `python3 scripts/scrub_check.py --require-denylist --history`. It must exit with 0. Do not paste the list or the output anywhere.

The tracked files were cleaned of private names before the release, but the old history still holds the files from before the clean-up, so the `--history` scan reports them and making this repository public would publish them. The owner's decision is to use a fresh one-commit export for publication. Create the public repository from a fresh history that starts at the cleaned release commit, and keep this repository private:

1. Run `python3 scripts/export_public.py <dir>` with a new, empty folder outside this repository. It copies the release commit's files without the internal working notes into that folder and makes one commit there with this repository's own identity. It never adds a remote and never pushes.
2. In that folder, run the same scan, `python3 scripts/scrub_check.py --require-denylist --history`, and the test suite, then review the folder by hand.
3. Check that the export holds the release commit's files and nothing else: extract `git archive <release commit>` into another new, empty folder and run `diff -r -x .git <that folder> <dir>`. It may report only the internal working notes that the export leaves out, each as "Only in" the extracted folder.
4. Create the public repository, still private until step 15, add it as the only remote of that folder (not of this repository), and push from that folder with `git push`.

### 11. Read the hosted checks
Who: Controller

Read two hosted runs. Both must be green: both Python versions, the secret check, `actionlint`, commit lint, and the Spec Kit contract run on the oldest and newest supported versions.

1. In this repository, the run on the release commit.
2. In the public repository, its own run on the export's commit from step 10. That commit is the one the tag gets in step 17.

Write both run links in the [release evidence file](https://github.com/praveenlavu/spec-kit-ghp/blob/main/docs/evidence/T42.md).

### 12. Check who wrote the commits
Who: Controller

On the release commit in this repository, and again in the export folder of step 10, run `git log --format='%an <%ae>|%cn <%ce>' | sort -u`. Only the no-reply identity may appear in either.

### 13. Review the whole branch
Who: Controller

An independent review of the whole branch up to the release commit, with every finding fixed or written down as new work. The review also holds for the export's commit while the tree check of step 10 reports nothing but the internal working notes; if it reports anything else, make the export again.

### 14. Delete the test repositories (owner only)
Who: Owner

Test repositories made during the live run may be deleted. Deleting needs the `delete_repo` scope on the owner's own sign-in, which only the owner adds, in the owner's own terminal and only while no other session is using `gh`. Remove the scope again afterwards.

### 15. Make the repository public (owner only)
Who: Owner

Make the public repository of step 10 public, never this one, and only after the hosted checks, the full check with the private list, the commit check and the whole-branch review are done. The installs in [CI on GitHub](ci-github.md) need a public repository. Make public only a repository with a fresh history that the full check with the private list passed on, never one whose history still holds the files from before the clean-up.

### 16. Turn on private vulnerability reporting (owner only)
Who: Owner

Right after the public repository is made public, in its settings under Security, turn on private vulnerability reporting, so that [SECURITY.md](../SECURITY.md) tells the truth. UNVERIFIED: to the author's knowledge GitHub offers it for public repositories only. If it is already offered while the repository is private, turn it on before making the repository public. Do this before any catalog filing.

### 17. Create and push the 0.1.0 tag (owner only)
Who: Owner

In the export folder of step 10, tag its one commit `v0.1.0` and push the tag to the public repository with `git push`. It must be the exact commit the checks looked at: the export's commit, whose hosted run step 11 read, not the release commit of this repository (the export has its own root commit, so the two commit ids differ). Then make a GitHub release from it in the public repository. The catalog entries point at this release.

### 18. Build the extension file and the bundle file
Who: Controller

In a clean checkout of the `v0.1.0` tag of the public repository, build both files into one new folder outside the repository:

1. `python3 scripts/build_extension_zip.py <dir>` writes `spec-kit-ghp-extension-0.1.0.zip`. Run it twice: the two files must be byte for byte the same. That holds for one Python and zlib build, not across machines: compare the two files from one machine.
2. `specify bundle validate --path bundle` must pass.
3. `specify bundle build --path bundle --output <dir>` writes `ghp-0.1.0.zip`.

The two names must differ; `tests/test_catalog.py` checks the extension's name against the bundle's.

### 19. Write the checksums file
Who: Controller

In that folder, run `shasum -a 256 spec-kit-ghp-extension-0.1.0.zip ghp-0.1.0.zip > SHA256SUMS`. Keep the folder: the next step attaches these three files.

### 20. Attach the extension archive and the bundle file to the release (owner only)
Who: Owner

Attach the three files of the last two steps to the GitHub release: `spec-kit-ghp-extension-0.1.0.zip` (the address in `catalog/extensions.json`), `ghp-0.1.0.zip` (the bundle file) and `SHA256SUMS`. Do not use GitHub's automatic archive of the tag: its bytes are not promised to stay the same, and step 22 takes the checksum it adds to `catalog/extensions.json` on `main` from the file attached here.

### 21. Check that every catalog address answers
Who: Controller

With the owner's go, ask each address for its headers, without signing in: `curl -sSIL -o /dev/null -w '%{http_code}\n' <address>`. Each must end with `200`: the extension file and the bundle file under `releases/download/v0.1.0/`, the raw workflow file, the two catalog files (`catalog_url` in each), and the documentation and changelog addresses of both entries and of the bundle. Write the list and the answers in the evidence file.

### 22. Add the checksum of the release file
Who: Controller

With the owner's go to download it, take the SHA-256 of the extension file attached in step 20 and add it to `catalog/extensions.json` as `sha256`, in a new commit on `main` of the public repository, made in the export folder; the owner pushes it. Make the same change in a commit on `main` of this repository too, so the two trees still differ only by the internal working notes. It must equal its line in `SHA256SUMS`. The address must stay a `releases/download` address, never the tag archive. `tests/test_catalog.py` accepts a checksum only in that form. Do not move the tag.

The checksum goes only into the entry filed upstream (step 26) and into `catalog/extensions.json` on `main`. Owner decision: serve each `catalog_url` from `main`, including the bundle page's `catalog add` lines and the installs of step 23, so installers receive the checksum after this commit. The catalog snapshot served from the `v0.1.0` tag carries no checksum; do not move the tag.

The same commit flips the pins of `tests/test_catalog.py::test_what_is_not_known_yet_is_marked_and_nothing_is_invented`: `"sha256" not in` the extension entry, no 64-hex string in `docs/catalog-submission.md`, and its "add `sha256` once the release file exists" note. Put the checksum in that page's Proposed Catalog Entry too: `test_extension_form_values_are_the_manifests` requires it to equal the catalog entry.

### 23. Install each part in a clean project, on two Spec Kit versions
Who: Controller

Do this once with Spec Kit 1.2.1 (the oldest supported) and once with the newest Spec Kit, each in a new empty folder where `specify init` ran, with no other catalog added:

1. The extension from its download address: `specify extension add ghp --from <download address>`; `specify extension list` must show 13 commands and 2 hooks.
2. The workflow from the raw workflow address: `specify workflow add <raw workflow address>`, then `specify workflow info ghp-lifecycle`.
3. The two component catalogs as in [the bundle page](https://github.com/praveenlavu/spec-kit-ghp/blob/main/bundle/README.md), then a pinned install of each part: `specify extension add ghp --version 0.1.0` and `specify workflow add ghp-lifecycle --version 0.1.0`. This is the check that the catalog's `requires` equals the workflow file's.
4. The bundle from the attached file: `specify bundle install ./ghp-0.1.0.zip`, in another new project with the two catalogs added.

Write each command, the Spec Kit version and the result in the evidence file.

### 24. Install the bundle by its id from a local catalog
Who: Controller

Write a bundle catalog file that holds only the proposed bundle entry from [Catalog submission](catalog-submission.md), on this machine. Add it as an install-allowed source, `specify bundle catalog add <its file:// address> --id ghp-local --policy install-allowed`, then run `specify bundle install ghp` in a clean project with the two component catalogs added. UNVERIFIED: Spec Kit's source accepts `file://` and loopback `http://` addresses here; neither has been tried. Write the result in the evidence file.

### 25. Fill in the testing details
Who: Controller

Copy what steps 18 to 24 actually showed into the "Testing Details" of each form in [Catalog submission](catalog-submission.md), replacing every **TO CONFIRM**, and tick only the boxes that were really done. Commit it as step 22 does: in the export folder for the public repository's `main` (the owner pushes it), and the same change in this repository.

### 26. File the catalog entries (owner only)
Who: Owner

From the owner's own account, file the three entries with the Spec Kit project (`github/spec-kit`) as [Catalog submission](catalog-submission.md) describes: the extension through its issue form, the workflow by pull request and the bundle through its issue form. Whether to file at all is the owner's decision.

### 27. Run the ready-made workflows in another repository (owner only)
Who: Owner

After step 15, in a separate test repository of the owner's own account, call the four reusable workflows of [CI on GitHub](ci-github.md) from the public repository at the `v0.1.0` tag, on GitHub's hosted runners: a passing gate, a failing one with its message, a pull request whose own config file is ignored, and the warning case. Run the link-on-merge workflow once without the `GHP_PROJECT_TOKEN` secret and once with it; only the owner creates that secret. Until this runs, the ready-made workflows stay marked "not yet run in another repository". The controller writes the run links and the results in the [consumer run evidence file](https://github.com/praveenlavu/spec-kit-ghp/blob/main/docs/evidence/T39.md).
