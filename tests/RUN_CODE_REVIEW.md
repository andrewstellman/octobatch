# Code Review

## Bootstrap

Read the following context files to understand the project:
1. ai_context/QUALITY.md
2. ai_context/PROJECT_CONTEXT.md
3. scripts/CONTEXT.md
4. scripts/tui/CONTEXT.md
5. scripts/tui/screens/CONTEXT.md
6. scripts/tui/utils/CONTEXT.md

## Review the Changes

Review the recent code changes using: `git diff HEAD~5`

If the user specifies particular files or folders, review only those.
If the user specifies a different diff range, use that instead.

## What to Check

For each changed file, review the diff and its surrounding context:

1. **Logical correctness** — Does the code do what it claims? Edge cases, off-by-one errors, unhandled exceptions?
2. **Architectural coherence** — Does the change follow existing patterns? Is logic in the right layer (data logic in utils, not in UI screens)?
3. **State management** — For TUI changes: are reactive properties updated correctly? Could the change leave the UI in an inconsistent state?
4. **Error handling** — Are exceptions caught appropriately? Could a failure crash the application or silently corrupt data?
5. **Completeness** — Are all call sites updated? If a function signature changed, are all callers updated? If a new keybinding was added, is it in all relevant footer variants?
6. **Side effects** — Could the change break existing functionality that wasn't directly modified?

## Guardrails

- **Line numbers are mandatory.** If you cannot cite a specific line, do not include the finding.
- **Read function bodies, not just signatures.** Don't assume a function works correctly based on its name.
- **If unsure whether something is a bug or intentional**, flag it as a QUESTION rather than a BUG.
- **Do NOT suggest style changes, refactors, or improvements.** Only flag things that are incorrect or could cause failures.

## Output Format

Write your review to `tests/code_reviews/CODE_REVIEW_[TOOL_NAME]_[YYYY-MM-DD].md` using this format:

### filename.py
- **Line NNN:** [BUG / QUESTION / INCOMPLETE] Description of the issue. What you expected vs. what you see. Why it matters.

End with a summary:
- Total findings by severity (BUG / QUESTION / INCOMPLETE)
- Files reviewed with no findings
- Overall assessment: **SHIP IT** / **FIX FIRST** / **NEEDS DISCUSSION**
