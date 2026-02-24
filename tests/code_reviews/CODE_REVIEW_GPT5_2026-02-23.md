### scripts/tui/screens/main_screen.py
- **Line 2724:** **BUG** Archive eligibility only checks `manifest["status"]`, so runs that are terminal by chunk state but have empty/missing status (which the app otherwise treats as terminal) cannot be archived from MainScreen. Expected behavior is consistent terminal detection (as in run status inference), but this path rejects such runs and blocks archive/unarchive from this screen.

### docs/examples/blackjack.md
- **Line 100:** **BUG** The validation rule uses `accuracy >= 0.7`, but the project’s blackjack pipeline validation field is `strategy_accuracy` (and the same section’s original phrasing/doc context refers to strategy accuracy). Expected the example rule to use the actual field name so copied configs validate; current snippet will fail with an undefined/missing field.

### Summary
- Total findings by severity: **BUG: 2**, **QUESTION: 0**, **INCOMPLETE: 0**
- Files reviewed with no findings: `scripts/config_validator.py`, `scripts/tui/screens/home_screen.py`, `scripts/tui/screens/modals.py`, `scripts/tui/utils/runs.py`, `docs/examples/npc-dialog.md`, `run.sh`, `run.cmd`, `setup.sh`, `setup.cmd`
- Overall assessment: **FIX FIRST**
