<!-- Added by speckit.ghp.constitution (spec-kit-ghp). Append below existing articles; renumber only if the project's scheme requires it. -->

## GitHub delivery articles

### Article I — The story is the source of truth

The GitHub story's acceptance criteria are copied into the DoD ledger as verbatim text
and are not reworded in the spec. If the AC is wrong or thin, fix the story issue on GitHub and
refresh the ledger (`ghp dod <n> --refresh`); do not quietly reinterpret it.

### Article II — Every AC is proven by a named test

Each acceptance criterion (AC-1…AC-n) maps to at least one named, passing test with a
real assertion, recorded in ledger section 3 with the commit or PR that made it green.
An AC without a test is not done.

### Article III — A human approves every board change

No GitHub issue, Project status, comment, or link changes, and no PR is opened or
commented on, until a person has seen the exact change and said yes. The rules are in
`.specify/extensions/ghp/rules/hitl.md`.

### Article IV — Gates advise before they block

`ghp gate` and the multi-plane review report findings by default. A check blocks only
after the team opts it in (`gate.block`, `review_block_on` in `ghp.config.json`).
Missing information degrades to a note, never a false failure.

### Article V — No sensitive data or secrets in GitHub or git

Customer or member data never goes into issues, comments, URLs, ledgers,
or commits. Tokens are never accepted in chat, echoed, logged, or
committed; `gh` keeps the sign-in and `ghp` never reads it.

### Article VI — One task, one commit, naming its AC

Each task ends in exactly one focused commit whose message names the task and the AC it
satisfies. Work discovered mid-task becomes a new task, not a larger commit.
