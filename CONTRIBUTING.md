# Contributing

Thank you for helping. This page says how to run the tests and how changes are made here.

## Set up and run the tests

`ghp` uses only the Python standard library. The tests need `pytest`.

```bash
python3 -m venv .venv
.venv/bin/pip install -e . pytest setuptools wheel
.venv/bin/python -m pytest -q
```

Run the suite a second time the way GitHub's hosted CI sees it. Some checks behave differently in CI, and this run catches that:

```bash
GITHUB_ACTIONS=true CI=true .venv/bin/python -m pytest -q
```

Then check that no secret or private name slipped in:

```bash
.venv/bin/python scripts/scrub_check.py
```

To build without network access (no build isolation), the build tools must already be there, and `setuptools` must be 61 or later:

```bash
.venv/bin/python -m pip install "setuptools>=61" wheel
.venv/bin/python -m pip install --no-build-isolation -e .
```

The tests never call GitHub. Tests against real GitHub live in `tests/live/` and are skipped unless a maintainer turns them on.

**Live run hygiene.** A live run that starts an AI agent (the workflow's agent steps do) uses a clean agent home: no global agent config, no plugins and no hooks of yours. An agent reads its global instructions on start and follows them, so your own rules (for example how you sign in to `gh`) would change what the run does and what it proves. Point the agent's home or config folder at a throwaway folder in your scratch area, sign the agent in there, and never copy your own settings into it. Before the run, check the agent's list of loaded instructions and plugins: it should show only the project's own files. Which setting moves the home differs per agent; check your agent's documentation.

**Live target and Project switches.** The live tests write to a private scratch repository of the owner's account by default. Set `GHP_LIVE_REPO` to another repository of that same account (for example `GHP_LIVE_REPO=praveenlavu/ghp-live-comment`) to run there instead; any other owner is refused before the first call, and the other live-run guards stay as they are. Use it for the tests that need GitHub Actions (`tests/live/test_approver_vs_bot.py`), because a private repository may not be allowed to start Actions jobs on the account; with the variable unset nothing changes. The pull request and `ghp done` tests read the Project's two closing switches, "Auto-close issue" and "Item closed", once at the start and expect what that state does: with "Auto-close issue" off, moving a card to Done does not close its issue; with "Item closed" off, a merged pull request closes the story through its `Closes #<n>` line but the card stays where it was, and `ghp done` then sets Done itself. The suite never changes them: they are set by hand in the Project's settings.

## How changes are made

- **Plan first.** Before code, write down what the change does and how you will know it works: a short list of checks that a test can prove.
- **Test first.** Write the test, watch it fail, then write the code that makes it pass.
- **One task, one commit.** Each commit does one thing. Do not mix unrelated changes, and do not split one change over several commits.
- **Stay inside the task.** Anything else you notice becomes a new task, not part of this one.

## Commit messages

The subject line is checked on every pull request. It must have one of these shapes:

```text
T<nn> <short name>: AC <numbers>
docs: <short summary>
chore: <short summary>
```

The first shape is for a planned task. `T<nn>` is the task's number in the project's plan (two or three digits, and an optional letter after it).
`AC` lists which of the task's checks (its acceptance criteria) the commit meets.
Subjects that start with `Merge ` (merge commits) are accepted too.

Not working on a planned task? Use `docs:` for a change to the documentation and `chore:` for anything else, including a code fix.
A maintainer may turn a code change into a planned task.

The body says what changed, the command that proves it with its last line of output, and anything not yet verified.

## Data and names

- Use **fictional data only**: made-up users, repositories and issues, such as `acme-demo/hello-spec` and issue #12.
- Never name a real client, employer, customer or person in a file, a commit or an issue.
- Never commit a token, a password or a real `ghp.config.json`.

## Reporting a security problem

Please do not open a public issue. See `SECURITY.md` in the top folder of this repository for how to report it privately.

## Licence

By contributing you agree that your work is released under the MIT licence in [LICENSE](LICENSE).
