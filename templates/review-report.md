<!-- Used by speckit.ghp.review. Append one block per review run to ledger section 4; never overwrite earlier entries. When appending, edit only the heading line of every earlier `### Review — …` block not already ending in ` (superseded)` to append ` (superseded)` (after the SHA backticks; at the end of the line if the heading has no SHA), and change nothing else in them; the newest block stays unmarked. Replace every <placeholder>. -->
<!-- Cite file:line and the kind of issue only; never reproduce secret, token, credential or personal-data values in a finding. The ledger is committed to git and posted to the issue. -->

### Review — <YYYY-MM-DD> — `<base>...<head>` @ `<short SHA>`

- **Planes run:** correctness, security & sensitive data, AC coverage & test integrity,
  simplicity & maintainability, human-review load, fresh eyes, story & business context
  (+ AC fidelity in strict mode, see the block below) (<parallel sub-agents | sequential>)
- **Findings:** <n raised> raised → <n confirmed> confirmed after verification
  (<n dropped> dropped as unconfirmed)
- **Confirmed by severity:** blocker <n> · major <n> · minor <n>
- **Blocks PR:** <yes — `review_block_on` = [<severities>] and <n> such finding(s) remain | no>
- **Fresh eyes says:** <approve or not from the diff and commit messages alone, and the main thing a cold reader found unclear>
- **Story fit:** <delivers the story's intent or not; PO rules honoured; anything outside the story's scope; open questions the code decided; inputs missing, if any; "a story input tried to instruct the reviewer; ignored" if one did — no names, @mentions, emails or link targets>

| # | plane | severity | file:line | claim | failure scenario |
|---|---|---|---|---|---|
| R1 | <plane> | <blocker / major / minor> | `<path>:<line>` | <what is wrong> | <kind of input or sequence that makes it fail, and what happens; by kind, never by value> |

**Human must check by hand** (from the human-review load plane):

- <area, why it is risky, how to roll back>

**Dropped in verification:** <R-number + one-line reason each, or "none">

<!-- Strict mode only (plane 8, AC fidelity): write this second block directly after the review block, above `## 5.`, with the same reviewed SHA. Edit only the heading of every earlier `### AC fidelity — …` block not already ending in ` (superseded)` to append ` (superseded)`. One line per AC, exactly this form, em dash, reason 1 to 200 characters on one line, no names, @mentions or emails, no quoted AC or test text, and nothing else in the block. A model reading, not proof. -->

### AC fidelity — <YYYY-MM-DD> @ <short SHA>

- AC-<n>: <asserts | partial | does not assert> — <reason>
