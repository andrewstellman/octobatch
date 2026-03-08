# Bug Fixes for v1.0.3

Read ai_context/DEVELOPMENT_CONTEXT.md for codebase context before starting any fix.

These bugs are organized into two groups for execution:
- **Group A (Orchestrator/Core):** Bugs 1-4 — fix in scripts/orchestrate.py and scripts/expression_evaluator.py
- **Group B (TUI/CLI):** Bugs 5-7 — fix in TUI and CLI code

---

## GROUP A: Orchestrator / Core

---

## BUG 1: Zero-valid-units not handled for LLM validation steps (CRITICAL)

### Symptoms
When an LLM step's business logic validation rejects 100% of units in a chunk (schema validation passes, but all units fail business logic rules), the orchestrator advances the empty chunk to the next step. If the next step is an LLM step, it attempts to create a batch with zero prompts. The provider API (e.g., Gemini) returns 400 INVALID_ARGUMENT for the empty batch. The orchestrator retries the submission, gets the same 400, and loops forever.

### Root Cause
The v1.0.2 fix for empty chunks only applies to expression steps (scope: expression). The general case — any step producing 0 valid units after validation and retry exhaustion — is not handled. The orchestrator's step advancement logic does not check whether the validated output file is empty before advancing a chunk.

### How It Was Found
Gemini advisor identified this as a generalization of Bug 1 from v1.0.2. The expression step fix was scoped narrowly to expression steps only, but the same empty-chunk problem can occur after any validation step.

### Fix Required
Generalize the v1.0.2 empty-chunk terminal logic to ALL step types, not just expression steps:

1. After validation completes for ANY chunk at ANY step, check if the chunk has 0 valid units.
2. If 0 valid units remain and the chunk has exhausted its retries (or is an expression step), mark the chunk as TERMINAL/FAILED.
3. Do NOT advance the chunk to the next step.
4. This must work for both batch and realtime modes.

Important: this must NOT prevent normal retries. If a chunk has 0 valid units but still has retry attempts remaining (for LLM steps), the chunk should retry normally. Only mark as terminal when retries are exhausted AND valid count is still 0.

### Where to Look
- `scripts/orchestrate.py` — the step advancement logic. Look at the v1.0.2 fix for expression steps and generalize it.
- Search for where `{next_step}_PENDING` state is set after validation completes.
- Check both `tick_run()` (batch) and the realtime execution loop.

### How to Verify
1. Create a test with a validation rule that rejects ALL units (e.g., `expr: "false"` with level: error).
2. Run it with max_attempts: 1 so retries exhaust immediately.
3. Verify the run completes with status "complete" (not stuck in a loop).
4. Verify no 400 errors from empty batch submissions in the log.
5. Run `--verify` and confirm the failed chunk is properly accounted for.

### Tests Required
- Test: LLM step chunk with 0 valid after validation and retries exhausted → marked terminal
- Test: LLM step chunk with 0 valid but retries remaining → retries normally (NOT marked terminal prematurely)
- Test: mixed chunk (some valid, some failed, retries exhausted) → valid units advance, chunk not terminal
- Test: run completes when all chunks in a step drop to 0 valid after retry exhaustion

---

## BUG 2: Post-processing runs before all retries complete (HIGH)

### Symptoms
In a large batch run (e.g., bj_haiku_1k with 3,000 hands), the final LLM step (analyze_difficulty) completes for the main chunks and post-processing runs immediately, generating strategy_comparison.txt from 2,829 valid units. Meanwhile, 17 units are still retrying in an earlier step (play_hand). If those retries succeed, their results never appear in the post-processing output because it already ran.

The result: published analysis is based on incomplete data. The user sees a "complete" strategy_comparison.txt and assumes it includes all valid results, but it's missing the late-arriving retry results.

### Root Cause
Post-processing is triggered when the final step's chunks all reach a terminal state. But retry chunks from earlier steps can still be in flight — they haven't reached the final step yet. The post-processing trigger doesn't check whether ALL chunks across ALL steps are terminal.

### How It Was Found
Running bj_haiku_1k (1,000 repeats, Haiku 4.5 batch mode). The strategy_comparison.txt was generated while the TUI still showed 17 units retrying in play_hand. The post-processing output had 2,829 results instead of the eventual ~2,845 that would have been available if it waited.

### Fix Required
Post-processing must be a global pipeline barrier:

1. Before running post-processing, check that ALL chunks (including retry chunks) across ALL steps have reached a terminal state (VALIDATED or FAILED with retries exhausted).
2. If any chunk anywhere in the pipeline is still in-flight (PENDING, SUBMITTED, or has retries remaining), defer post-processing.
3. Post-processing should run exactly once, after the entire pipeline is quiescent.

### Where to Look
- `scripts/orchestrate.py` — search for where post-processing is triggered. Look for `post_process` or `run_post_process`.
- Check the condition that gates post-processing — it likely checks only the final step's chunk states.
- The `is_run_terminal()` function may be relevant — if it already checks all chunks across all steps, post-processing should be gated on it.

### How to Verify
1. Run a Blackjack pipeline with enough repeats to generate retries (30+ repeats).
2. Observe that post-processing does NOT run until all retries have completed or been exhausted.
3. Check that the final strategy_comparison.txt includes results from retried units that eventually succeeded.
4. Verify that post-processing runs exactly once (not re-triggered when late retries complete).

### Tests Required
- Test: post-processing does not run while any chunk in any step is non-terminal
- Test: post-processing runs after all chunks reach terminal state
- Test: post-processing includes results from retry chunks that succeeded

---

## BUG 3: Failure records missing upstream fields (MEDIUM)

### Symptoms
When units fail validation at expression steps (verify_hand, verify_strategy), the failure records in `{step}_failures.jsonl` do not include fields accumulated from prior pipeline steps. For example, a unit that fails at verify_strategy has no `strategy_name` field in its failure record — only the fields produced by the current step (verification_passed, verification_details, etc.).

This makes failure analysis difficult. To determine which strategy had the most failures, we had to parse the `unit_id` string (e.g., extract "pro" from "the_pro__rep0042") instead of reading a `strategy_name` field.

### Root Cause
When writing failure records, the orchestrator writes only the current step's output fields, not the accumulated context from all prior steps. The unit's full context (items fields + all prior step outputs) is available during expression evaluation but is not carried into the failure record.

### How It Was Found
Running failure analysis on the Haiku 4.5 Blackjack runs. The failure records showed `strategy_name: unknown` because that field (from the items file) was not present in the verify_hand failure output. We had to write custom Python to parse unit_id prefixes instead.

### Fix Required
When writing a unit to `{step}_failures.jsonl`, include the unit's accumulated context fields (from items and all prior validated steps) merged with the current step's output. The failure record should contain everything needed to analyze why the unit failed without cross-referencing other files.

### Where to Look
- `scripts/orchestrate.py` — search for where failure records are written to `_failures.jsonl`.
- Look at how the expression step evaluation receives its input context — the merged fields from prior steps should be available at that point.
- Check `run_validation_pipeline()` or wherever validation results are written to the failures file.

### How to Verify
1. Run a Blackjack pipeline (10 repeats is enough).
2. Check verify_hand_failures.jsonl for a failed unit.
3. Verify that the failure record contains `strategy_name`, `player_card_1`, `dealer_up_card`, and other fields from prior steps — not just the verification output fields.
4. Verify that existing passing behavior is unchanged — validated output files should still have the same format.

### Tests Required
- Test: failure record from expression step contains fields from items file (e.g., strategy_name)
- Test: failure record from expression step contains fields from prior validated steps (e.g., player_card_1 from deal_cards)
- Test: failure record from LLM step also includes accumulated context
- Test: validated output format is unchanged (no regression)

---

## BUG 4a: --verify doesn't handle gzipped validated files (MEDIUM)

### Symptoms
`--verify` reports all units as missing at every step when the pipeline has `type: gzip` post-processing that compresses validated output files. The data is present in `.jsonl.gz` files but `--verify` only looks for `.jsonl` files.

### Root Cause
`run_tools.py`'s `verify_run()` reads `{step}_validated.jsonl` but the gzip post-processing step renames these to `{step}_validated.jsonl.gz`. After compression, `--verify` sees 0 valid units at every step and reports the entire run as missing.

### How It Was Found
Running `--verify` on a completed Blackjack smoke test. All 15 units showed as missing at deal_cards despite the run completing successfully with 12 valid results.

### Fix Required
Update `verify_run()` in `scripts/run_tools.py` to check for both `.jsonl` and `.jsonl.gz` files. If the `.gz` version exists, decompress and read it.

### How to Verify
1. Run a Blackjack pipeline (it has gzip post-processing).
2. Run `--verify` and confirm it correctly reports valid/failed counts matching the actual run results.

### Tests Required
- Test: verify_run finds and reads gzipped validated files
- Test: verify_run still works with uncompressed files (regression)

---

## BUG 4: Expression evaluator init variable scoping in loops (MEDIUM)

### Symptoms
When a looping expression step's init block defines variables that reference upstream step output (e.g., `_dealer_hand: "[dealer_up_card, dealer_hole_card]"`), and the expressions block references those init variables (e.g., `_d_sum: "sum([_cv[c] for c in _dealer_hand])"`), the expressions block fails with:

```
NameError: name '_dealer_hand' is not defined
```

This only happens when init variables reference fields from prior pipeline steps. Simple literal init variables (like `position: "5"` in the Drunken Sailor pipeline) work correctly.

### Root Cause
Needs investigation. The expression evaluator in `scripts/expression_evaluator.py` handles init blocks and expression blocks for looping steps. Either:
- Init variables are not being injected into the expression namespace for the first iteration
- Init expressions that reference upstream context variables (dealer_up_card, dealer_hole_card) fail silently, leaving the init variable undefined
- The init block evaluates expressions independently (not sequentially), so later init expressions can't reference earlier ones

The Drunken Sailor pipeline works because its init variables use simple literals that don't reference upstream step output. However, `start_position` IS an upstream field reference — so the issue may be more specific than "upstream references don't work." It may be about complex expressions (list construction) vs simple variable references.

### How It Was Found
Attempting to implement the v9 deterministic dealer play-out for the Blackjack pipeline. The play_dealer expression step used a looping expression with init variables referencing deal_cards output. All units failed with NameError on the first expression that referenced an init variable.

### Fix Required
1. Read `scripts/expression_evaluator.py` thoroughly to understand how init blocks feed into expression blocks in looping steps.
2. Write a minimal reproduction test: a looping expression step where init references upstream context and expressions reference init variables.
3. Identify why init variables that reference upstream context are not available in the expressions namespace.
4. Fix the expression evaluator so that:
   - Init expressions are evaluated sequentially (each can reference prior init results)
   - Init variables referencing upstream step context resolve correctly
   - All init variables are available in the expressions namespace on the first iteration
   - Subsequent iterations can read/update these variables normally

### Where to Look
- `scripts/expression_evaluator.py` — the `evaluate_expressions()` function and any looping logic
- Look for how the `init` block is processed vs the `expressions` block
- Check how the unit context (upstream step fields) is injected into the evaluation namespace
- Compare with how the Drunken Sailor's init block (`position: "start_position"`) works

### How to Verify
1. Create a test that evaluates a looping expression with init referencing upstream context:
   ```python
   init = {"_hand": "[card1, card2]", "_total": "sum([int(c) for c in _hand])"}
   expressions = {"_new_total": "_total + 1"}
   context = {"card1": "5", "card2": "3"}
   ```
2. Verify _hand resolves to ["5", "3"], _total resolves to 8, _new_total resolves to 9.
3. Run the v9 Blackjack play_dealer step on a small test to confirm end-to-end.

### Tests Required
- Test: init expression referencing upstream context variable resolves correctly
- Test: init expressions evaluated sequentially (later init can reference earlier init)
- Test: init variables available in expressions block on first loop iteration
- Test: init variables with complex types (lists, dicts built from upstream fields) work
- Test: existing Drunken Sailor init behavior unchanged (regression)

---

## GROUP B: TUI / CLI

---

## BUG 5: ETA inconsistency between home and detail screens (MEDIUM)

### Symptoms
The home screen and detail screen show different ETA values for the same run. The home screen ETA flashes briefly (e.g., "~5m") then reverts to "--". The detail screen shows a different value (e.g., "~11s") that appears to be based on the next poll interval rather than actual pipeline completion time.

### Root Cause
Two different code paths calculate ETA independently. The home screen likely uses one method (possibly total units / throughput) while the detail screen uses another (possibly time to next poll tick). Neither persists its calculation across refreshes, and neither accounts for the full pipeline (multiple steps remaining).

### How It Was Found
Observing the TUI during the bj_haiku_1k batch run. The home screen briefly showed "~5m" which seemed reasonable, then went back to "--". The detail screen showed "~11s" which was clearly wrong for a run with 30+ minutes remaining.

### Fix Required
1. Create a single shared ETA calculation function used by both screens.
2. ETA should be based on: validated-units-per-minute throughput × remaining units across ALL pipeline steps (not just current step).
3. For batch mode: account for polling intervals and typical batch processing time.
4. Once any LLM step has produced results (giving us a throughput measurement), ETA should persist and update continuously — never flash and disappear.
5. ETA should represent time to full pipeline completion, not time to next step completion.

### Where to Look
- `scripts/tui/screens/home_screen.py` — search for ETA or `_compute_eta`
- `scripts/tui/screens/main_screen.py` — search for ETA calculation
- Look for any `eta` or `remaining` calculations in `scripts/tui/utils/`

### How to Verify
1. Start a Blackjack run with 30+ repeats in realtime mode.
2. Verify the home screen shows a stable ETA that counts down.
3. Open the detail screen and verify the same ETA value is shown.
4. Verify ETA does not flash or disappear after appearing.

### Tests Required
- Test: ETA calculation function returns consistent value regardless of which screen calls it
- Test: ETA accounts for remaining steps (not just current step)
- Test: ETA returns None/-- before any throughput data is available
- Test: ETA persists once throughput data exists (does not reset to --)

---

## BUG 6: TUI test environment coupling (LOW)

### Symptoms
Multiple TUI tests skip or behave differently based on whether local run data exists in the `runs/` directory. On a clean machine (no runs), some tests skip entirely. This means the test suite doesn't verify TUI behavior on clean installs.

### Root Cause
Tests check for the existence of local run directories and skip if they're not present, rather than creating synthetic test data.

### How It Was Found
Noted during the test coverage sprint. Cursor flagged multiple tests with runtime skips based on local data availability.

### Fix Required
1. Identify all TUI tests that skip based on local data availability (search for `pytest.skip` or conditional skips in test files).
2. For each, create synthetic test data using `tmp_path` fixture instead of depending on real run directories.
3. Tests should create minimal run directory structures with manifest files, so they work on any machine.

### Where to Look
- `tests/test_tui.py` — search for `skip`, `skipIf`, `pytest.mark.skip`, or conditional checks on file existence
- Any test that references `runs/` directly

### How to Verify
1. Rename `runs/` to `runs_backup/` temporarily.
2. Run `pytest tests/test_tui.py -v`.
3. All tests should pass with 0 skipped.
4. Rename back.

### Tests Required
- No new tests needed — the fix is making existing tests self-contained

---

## BUG 7: --watch missing delta summaries and heartbeat (LOW)

### Symptoms
The `--watch` output shows per-step valid/failed/submitted counts on each tick, but does not show what changed since the last tick. Between ticks, there is no heartbeat output, so the user can't tell if the process is alive or hung during long polling intervals.

### Root Cause
`format_watch_progress()` (orchestrate.py:3607-3670) renders the current state but doesn't compare against the previous tick's state to compute deltas. No heartbeat line is emitted between ticks.

### How It Was Found
Monitoring long batch runs where the output was static for minutes at a time. Couldn't tell if the process was alive or if anything was happening.

### Fix Required
1. Store the previous tick's state (valid counts, failed counts per step).
2. On each tick, compute deltas: "+5 valid, +2 failed since last tick" for each step that changed.
3. Between ticks (during the poll interval sleep), emit a brief heartbeat line every 30-60 seconds: something like `[HEARTBEAT] alive, waiting for batch results (45s since last tick)`.
4. Only log delta lines for steps that actually changed — don't spam unchanged steps.

### Where to Look
- `scripts/orchestrate.py` — `format_watch_progress()` around line 3607
- The polling loop in `watch_run()` where `time.sleep()` is called between ticks
- Look for where `prev_poll_status` is already cached (the throttle log collapse fix uses this)

### How to Verify
1. Run a batch pipeline with `--watch` and observe the output.
2. Verify delta lines appear when counts change (e.g., "+3 valid this tick").
3. Verify heartbeat lines appear during long waits between ticks.
4. Verify unchanged steps don't produce delta spam.

### Tests Required
- Test: delta calculation correctly identifies changes between two state snapshots
- Test: heartbeat interval is configurable or uses a reasonable default
- Test: no delta line emitted when state hasn't changed

---

## General Instructions

After fixing all bugs in a group:

1. Run the full test suite: `pytest tests/ --cov=scripts --cov-report=term-missing`
2. Verify no existing tests broke.
3. Verify coverage did not decrease.
4. Run TWO Blackjack integration tests to verify both execution paths:
   Realtime: `python3 scripts/orchestrate.py --init --pipeline Blackjack --run-dir runs/bugfix_realtime --repeat 10 --realtime --provider gemini --yes`
   Batch: `python3 scripts/orchestrate.py --init --pipeline Blackjack --run-dir runs/bugfix_batch --repeat 10 --provider gemini --yes`
   Then: `python3 scripts/orchestrate.py --watch --run-dir runs/bugfix_batch`
5. Verify both runs complete without errors.
6. For Bug 2: confirm post-processing output includes the correct number of valid units (matching the final validated count, not an intermediate count).
7. For Bug 7: verify the --watch output during the batch test shows delta summaries and heartbeat lines.
