### scripts/orchestrate.py
- **Line 874:** **[BUG]** The new guard only skips `*_SUBMITTED` and `*_PROCESSING`, but the spec requires retry recovery to run **only** on terminal chunks (`VALIDATED`/`FAILED`). Expected an explicit terminal-state allowlist; current logic still processes non-terminal states like `*_PENDING`, which violates the prescribed state-machine invariant and can mutate active runs.
- **Line 5548:** **[INCOMPLETE]** Parse-error records are rewritten through `json.dumps(record)` instead of being written back byte-identically. The spec explicitly requires unparseable records to be preserved untouched in the failures file.

### scripts/run_tools.py
- **Line 206:** **[INCOMPLETE]** Unknown-step handling now skips safely, but does so silently. The fix spec requires a graceful skip **with warning** so operators can detect manifest/pipeline drift.

### scripts/find_and_mark_missing.py
- **Line 51:** **[INCOMPLETE]** Config read/parse failures are swallowed (`except Exception: pass`), then step classification falls back to treating all pipeline steps as LLM steps. Expected behavior per spec is scope-based filtering from config; this fallback can reintroduce expression-step misclassification.

### scripts/tui/screens/home_screen.py
- **Line 1203:** **[BUG]** `"killed"` was removed from archive-eligible terminal statuses. Expected `"killed"` to remain if it is a real manifest state; `mark_run_as_killed()` still writes `status = "killed"` in `scripts/tui/utils/runs.py`, so killed runs are now incorrectly blocked from archive/unarchive here.

### scripts/tui/screens/main_screen.py
- **Line 2726:** **[BUG]** Same regression as HomeScreen: `"killed"` removed from archive-eligible terminal statuses even though killed manifests still exist. Expected parity with actual status lifecycle; current behavior prevents archiving killed runs from MainScreen.

### Summary
- Total findings by severity: **BUG: 3**, **QUESTION: 0**, **INCOMPLETE: 3**
- Files reviewed with no findings: `scripts/config_validator.py`, `scripts/tui/utils/diagnostics.py`, `ai_context/DEVELOPMENT_CONTEXT.md`
- Overall assessment: **FIX FIRST**
