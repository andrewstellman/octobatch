### scripts/run_tools.py
- **Line 147:** **BUG** `verify_run()` caps `missing_ids` to 200, and `repair_run()` later uses that truncated list as its source of truth for repairs. Expected all missing units to be repairable; actual behavior silently omits units beyond the first 200 per step.
- **Line 190:** **BUG** `repair_run()` consumes `step_report["missing_ids"]` (already truncated), so `total_missing` and created retry chunks can undercount/under-repair large attrition events.

### scripts/tui/screens/main_screen.py
- **Line 2724:** **BUG** Archive eligibility checks only `manifest["status"]` against terminal statuses. Expected terminal inference to align with app behavior (which also uses chunk-state-derived terminality), but runs with terminal chunk states and stale/empty status are rejected from archiving in this screen.
- **Line 2748:** **BUG** `shutil.move(run_path, dest)` can nest directories when `dest` already exists (e.g., `_archive/run_name/run_name`) instead of failing fast. Expected collision-safe behavior; current behavior can silently corrupt run layout.

### scripts/tui/screens/home_screen.py
- **Line 1225:** **BUG** Same move-collision issue as MainScreen (`shutil.move` to an existing directory can nest instead of replacing), which can silently produce an invalid archive/unarchive directory structure.

### scripts/tui/utils/diagnostics.py
- **Line 178:** **BUG** `_get_failed_step()` only detects files ending in `_failures.jsonl`, but `_load_all_failures()` also loads `_failures.jsonl.gz`. Expected failed step detection for both formats; with gzipped failures, failed step is not detected and the config snapshot can omit relevant template/schema context.

### scripts/find_and_mark_missing.py
- **Line 65:** **BUG** `final_step = llm_steps[-1]` crashes when the filtered pipeline has zero LLM steps. Expected graceful handling with a user-facing message; actual behavior raises `IndexError`.

### docs/examples/blackjack.md
- **Line 100:** **BUG** Rule uses `accuracy >= 0.7`, but the blackjack config/schema field is `strategy_accuracy`. Expected docs example to match runnable config; copied rule will fail with missing/undefined field.

### ai_context/DEVELOPMENT_CONTEXT.md
- **Line 126:** **INCOMPLETE** The context still advertises `setup.sh`, `setup.cmd`, `run.sh`, and `run.cmd`, but those launch/setup scripts were removed in this weekly diff. Expected context to point to current wrappers/entry points only.

### Summary
- Total findings by severity: **BUG: 8**, **QUESTION: 0**, **INCOMPLETE: 1**
- Files reviewed with no findings: `scripts/config_validator.py`, `scripts/octobatch_utils.py`, `scripts/realtime_provider.py`, `scripts/schema_validator.py`, `scripts/validator.py`, `scripts/tui/screens/diagnostics_screen.py`, `scripts/tui/screens/modals.py`, `scripts/tui/utils/runs.py`, `tests/integration_checks.py`, `tests/test_tui.py`, `docs/examples/npc-dialog.md`
- Overall assessment: **FIX FIRST**
