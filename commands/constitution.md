---
description: "Append the GitHub/HITL articles to the project constitution without replacing it."
---

# Add the GitHub articles to the constitution

Makes the HITL rules and the story-first discipline project law by adding six articles
to `.specify/memory/constitution.md`. Existing principles are kept exactly as they are.

## Prerequisites

- Follow `rules/hitl.md`.
- The articles to add: `templates/constitution-articles.md`.

## User Input

```text
$ARGUMENTS
```

Consider the user input before proceeding (for example, article wording tweaks or
numbering preferences).

## Steps

### 1. Read what is there

- Read `.specify/memory/constitution.md`. Note its article numbering scheme, its version
  line, and its sync-impact report comment if any.
- Read `templates/constitution-articles.md`.
- If the constitution already contains the "GitHub delivery articles" heading,
  compare the two. Identical → report "already present" and stop. Different → show the
  diff and ask whether to update just that block.

### 2. Run the core constitution command with these articles as additions

Run core `speckit.constitution` (as this agent runs it, e.g. `/speckit.constitution` or
`/speckit-constitution`) with input that says:

- append the six articles from `templates/constitution-articles.md` as a new section;
- do **not** remove, reword, or reorder any existing principle;
- renumber the new articles only if the project's scheme requires it, keeping titles;
- bump the constitution version as a MINOR change (new principles added) and record it
  in the sync-impact report;
- plus any user input above.

This command does not use phase routing: the constitution is always core's.

### 3. Check the result

- Every principle that existed before is still present with the same text. If any was
  removed or reworded, restore it and tell the user what you restored.
- All six GitHub articles (I–VI) are present.
- Show the user the added section and the new version line.

### 4. Next step

Suggest `speckit.ghp.pull <n>` to start work on a story.

## HITL checkpoints

- No GitHub writes. The constitution is a local file that reaches the team through PR
  review, so no in-chat approval is required — but if step 1 found a conflicting older
  copy of the GitHub block, ask before replacing it.
