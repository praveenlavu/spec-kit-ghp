---
description: "Check tools, connect to GitHub, and confirm which kit runs each lifecycle phase."
---

# Set up spec-kit-ghp

Gets a project ready for the GitHub lifecycle: tools present, `ghp.config.json` pointing at
the right repository and Project, and a **phase map** the human has confirmed. Makes
no GitHub changes.

## Prerequisites

- Follow `rules/hitl.md` throughout. In particular: never ask for, accept, or echo a token, and never run `gh auth` yourself.
- Run from the project root (the directory holding `.specify/`).

## User Input

```text
$ARGUMENTS
```

Consider the user input before proceeding (for example, a kit to prefer, or "reconfigure").

## Steps

### 1. Check tools

Run each and report a short table (tool, found version, required/optional):

- `python3 --version` — required.
- `ghp --version` — required. Missing → tell the user to install it
  (`pipx install git+https://github.com/praveenlavu/spec-kit-ghp.git@v0.1.1`) and stop.
- `git --version` — required. Missing → stop.
- `gh --version` — required: `ghp` asks `gh` to make every GitHub call.

### 2. Connect to GitHub

- If `ghp.config.json` already exists with `owner`, `repo` and `project_number`, show the
  values and skip to step 3 unless the user asked to reconfigure.
- Otherwise ask the user to run **`ghp setup` themselves in their own terminal** (in
  Claude Code: `! ghp setup`). It asks for the repository owner, the repository, and the
  Project number, checks that `gh` is signed in, and asks for no password or token. If `gh` is not
  signed in, the user signs in themselves in their own terminal; you never run `gh auth` and never see a token.
- For a first-time user suggest `ghp setup --profile starter` (`team` and `strict` add the gate lists or strict mode and ask for the optional `approvers`, numeric user ids read only from the target branch; see
  `docs/quickstart.md`); on an existing config it replaces nothing unless the user adds `--force`.
- `ghp setup` writes names and numbers only. If the file exists but is not a valid JSON object,
  it refuses to touch it and exits 2.
- Verify with `ghp doctor` (read-only), and with `ghp get <n>` only if the user gives an issue number.

### 3. Detect installed kits

- Read `.specify/extensions.yml` (if present) and every
  `.specify/extensions/<id>/extension.yml` except `ghp`.
- For each extension, list its `provides.commands` (name, aliases, description).
- Ignore extensions shown as disabled by `specify extension list`.

### 4. Propose the phase map

For each phase — `specify`, `plan`, `tasks`, `implement` — pick the candidate command
whose name or description best matches the phase (for example a kit's `stories` command
can own `tasks`). Rules:

- Propose only commands that exist in step 3. Leave a phase unmapped when nothing
  matches; unmapped phases run core `speckit.<phase>` (see
  `rules/routing.md`).
- Several kits installed → show the candidates per phase and let the human choose.
- No kits → propose an empty map.

Show the proposal as a table (phase → command → why it matched) and the exact JSON that
would be written:

```json
"stack": { "kit": "<id or empty>", "phases": { "specify": "...", "plan": "..." } }
```

### 5. Confirm and write

Wait for the human to confirm or edit the map. Do not write a guessed map. Then merge
`stack` into `ghp.config.json`, keeping every other key unchanged, and offer to add the
remaining optional keys from the template with their defaults (`status_todo`,
`status_active`, `status_done`, `done_state`, `branch_prefix`, `dod_path`, `gate`,
`attachments_allow`, `review_block_on`). Show the final file content (it contains no
secrets). Never write `strict.test_command` here (the user's `ghp setup --profile strict` asks for one only while no stack profile is approved): once a project has a stack profile it
comes only from `ghp stack approve --apply`, which the human runs in their own terminal
(a hand edit would differ from the approved command, which strict mode reports). Never write `approvers`, `approval` or `unattended`
here either: they are read only from the target branch, and the human sets them with `ghp setup --profile team`.

### 6. Stack profile

Run `speckit.ghp.stack` (pass `--workflow` if this command was given it): it detects the stack, offers an installed
kit or a kit search or a researched draft profile, and tells the human how to approve it. It installs nothing and
approves nothing. Skip it only if the human says so, and say that strict mode needs an approved profile.

### 7. Next step

Suggest `speckit.ghp.constitution`, then `speckit.ghp.pull <n>`.

## HITL checkpoints

- No GitHub writes in this command. Writing `ghp.config.json` is a local file change, but
  the phase map still needs the human's confirmation (step 5) because it decides which
  commands run later.
- If a token appears in chat at any point, do not use it; tell the user to revoke it on GitHub and
  sign in again with `gh` in their own terminal.
