# Code Review: orchestrate.py
**Reviewer:** Claude Opus 4.6
**Date:** 2026-03-25
**File:** `scripts/orchestrate.py` (7,513 lines)
**Protocol:** `quality/RUN_CODE_REVIEW.md`

---

## Focus Area 1: Run State Machine and Manifest Durability

### scripts/orchestrate.py

- **Line 4379:** `BUG` — `evaluate_condition(loop_until_expr, output_unit)` does not pass the persistent `rng` (SeededRandom) instance to `evaluate_condition()`, even though the function accepts `seed_or_rng` as its third parameter (expression_evaluator.py:177). The comment on line 4378 says "no randomness needed for condition check," but this assumption is wrong — a `loop_until` expression could reference `random.random()` (e.g., `"random.random() > 0.9 or position >= 10"`). Without the shared RNG, the condition would use an unseeded interpreter, breaking determinism and reproducibility.
  - **Expected:** `evaluate_condition(loop_until_expr, output_unit, rng)` — same RNG instance used by the expression block (line 4375)
  - **Actual:** RNG not passed; condition evaluation gets a fresh, unseeded interpreter
  - **Why it matters:** Threatens Scenario 4 (statistical bias) and Scenario 7 (expression determinism). A loop_until condition using randomness would produce different results on resume, violating the reproducibility contract. Even if no current pipeline uses random in loop_until, the API contract is broken.

- **Line 280-295 (`mark_run_complete()`):** `QUESTION` — This function unconditionally sets `status = "complete"` without verifying `is_run_terminal()`. The log message on line 300-301 says "Run status corrected from '{current_status}' to complete (all chunks terminal)" but the function itself does not verify that all chunks are terminal. Callers (lines 4020, 6065, 7418, 7462) do check `is_run_terminal()` before calling, so this is a contract-by-caller pattern rather than a self-enforcing function.
  - **Expected:** Function either verifies terminal state internally or documents that callers must check
  - **Actual:** No internal guard; relies on caller discipline
  - **Why it matters:** Threatens Scenario 1 (manifest integrity). If a future caller omits the `is_run_terminal()` check, a run could be marked complete with non-terminal chunks.

- **Line 1121 (`retry_validation_failures()`):** `QUESTION` — Unconditionally sets `manifest["status"] = "running"` if any failures were archived. This could override a `"paused"` or `"complete"` status. Callers (watch_run line 3984, realtime_run line 5382) call this early in execution, so in practice it's safe, but the function mutates status as a side effect without documenting the precondition.
  - **Expected:** Either guard against overriding terminal states or document that callers must call this before any terminal-state check
  - **Actual:** Unconditional status override
  - **Why it matters:** Threatens Scenario 1 (manifest integrity) if called at an unexpected point in the lifecycle.

---

## Focus Area 2: Validation Pipeline and Failure Classification

### scripts/orchestrate.py

- **Lines 1579-1603 (`run_validation_pipeline()` — synthetic failure generation):** No issues found. Missing units are correctly detected (input_count - valid_count - failed_count) and converted to `pipeline_internal` hard failures. Unit conservation is maintained.

- **Lines 1502-1540 (failure record correction):** No issues found. The three-level search for `_raw_text` (failure.input, failure top-level, input_data_for_unit) correctly recovers raw_response across both schema and business validation failure paths. Accumulated context is merged into failure records at lines 1529-1537.

### scripts/schema_validator.py

- **Lines 240-310 (`_unwrap_response()`):** No issues found. Correctly detects double-wrapped responses, strips markdown fences, removes trailing commas, and only merges missing keys back into the top-level object. Safety guarantee at lines 301-303 preserves existing fields.

- **Lines 88-137 (type coercion):** No issues found. Non-finite numbers (NaN, Infinity) correctly rejected at lines 105-106 and 115-116 before coercion. Telemetry logged to stderr.

### scripts/validator.py

- No issues found. Failure records correctly classified as `failure_stage: "validation"` (line 633). Unit conservation maintained between phases.

---

## Focus Area 3: Expression Determinism and Statistical Correctness

### scripts/orchestrate.py

- **Line 4379:** `BUG` — (Same as Focus Area 1 finding.) `evaluate_condition()` not receiving the persistent RNG. See above for full analysis.

- **Lines 4363-4375 (shared SeededRandom):** No issues found. A single `SeededRandom` instance is correctly shared between init expressions (line 4367) and loop expressions (line 4375). RNG state advances continuously as specified.

- **Lines 4383-4398 (loop timeout handling):** No issues found. Timeout sets `_metadata.timeout = True` and `_metadata.iterations = N`, logs WARN, but does NOT treat unit as failed. Unit continues through the pipeline per spec.

### scripts/expression_evaluator.py

- **Lines 158-168 (evaluation order):** No issues found. Expressions evaluated in dict insertion order (Python 3.7+ guarantee). Each result added to `interpreter.symtable` before next expression evaluates. YAML uses `yaml.safe_load()` which preserves key order.

- **Lines 200 (`_SYSTEM_FIELDS`):** No issues found. `_metadata`, `_raw_text`, and `_repetition_seed` correctly excluded from expression namespace.

### scripts/generate_units.py

- **Line 473 (seed derivation):** No issues found. Uses `hash(unit_id) & 0x7FFFFFFF` for base seed, `+ rep_id` for unique repetitions. Matches spec.

---

## Focus Area 4: Config Linting and the 4-Point Link Rule

Reviewed via `--validate-config` output during integration testing. DrunkenSailor and NPCDialog validate cleanly. Blackjack reports 15 expression dry-run errors because expression steps reference variables from upstream LLM steps (e.g., `player_cards_used`, `player_final_total`) that don't exist in mock context.

- **Blackjack validation errors:** `QUESTION` — The 15 errors from `--validate-config` on the Blackjack pipeline are false positives. Expression steps `verify_hand` and `verify_strategy` reference fields that come from the `play_hand` LLM step, which are unavailable during dry-run validation with mock context. The validator correctly uses mock data (empty strings, zeros) but these don't match the real data shapes from upstream steps.
  - **Expected:** Config validation either skips expressions that depend on upstream LLM step outputs, or documents that these errors are expected for expression steps following LLM steps
  - **Actual:** Errors reported without distinguishing between "expression references undefined variable" and "expression references variable from upstream LLM step"
  - **Why it matters:** Threatens Scenario 2 (broken pipelines stopped before spend). A real missing-variable bug in an expression step would be hidden among the false positives from upstream dependencies.

---

## Focus Area 5: Provider Resolution and External Failure Boundaries

### scripts/realtime_provider.py

- **Lines 91-107:** `BUG` — `AuthenticationError` (a subclass of `ProviderError`, defined at providers/base.py:309) is caught by the `except ProviderError` clause but not handled as inherently fatal. The code falls through to line 102-103 where it string-matches for HTTP codes ("400", "401", "403"). If the provider SDK's AuthenticationError message doesn't contain these codes (e.g., "Invalid API key" without a numeric code), the error is treated as a non-fatal `api_error` and the unit simply fails — instead of aborting the run immediately.
  - **Expected:** `AuthenticationError` should always raise `FatalProviderError`, regardless of error message content. Either catch `AuthenticationError` before `ProviderError`, or add an `isinstance()` check.
  - **Actual:** Relies on string matching for HTTP codes, which is fragile across provider SDKs
  - **Why it matters:** Threatens Scenario 2 (broken pipelines stopped before spend). An invalid API key could burn through all units with per-unit failures instead of aborting immediately.

### scripts/providers/__init__.py

- **Lines 127-145 (`get_step_provider()`):** No issues found. CLI override precedence is correctly implemented. CLI flags tracked via `cli_provider_override`/`cli_model_override` in manifest metadata (set during init_run at lines 3368-3369). Step-level overrides correctly suppressed when CLI flags are active.

### scripts/providers/gemini.py, openai.py, anthropic.py

- **Timeouts:** No issues found. All providers correctly set 120-second timeouts (gemini.py:176 at 120000ms, openai.py:114 at 120.0s, anthropic.py:114 at 120.0s).

---

## Focus Area 6: TUI Truthfulness and Operator Safety

Not reviewed — `scripts/orchestrate.py` is the review target. TUI code in `scripts/tui/` was out of scope for this review.

---

## Summary

| Classification | Count |
|---------------|-------|
| BUG | 2 |
| QUESTION | 3 |
| INCOMPLETE | 0 |

### BUG findings:

1. **Line 4379** — `evaluate_condition()` missing RNG parameter breaks determinism contract for loop_until conditions that use randomness. (Scenario 4, Scenario 7)
2. **Lines 91-107 (realtime_provider.py)** — `AuthenticationError` not reliably detected as fatal; relies on fragile string matching for HTTP codes. (Scenario 2)

### QUESTION findings:

1. **Line 280-295** — `mark_run_complete()` doesn't self-verify terminal state; relies on caller discipline.
2. **Line 1121** — `retry_validation_failures()` unconditionally overrides status to "running".
3. **Blackjack --validate-config** — False positive expression errors from upstream LLM dependencies hide real issues.

### Files reviewed with no findings:
- `scripts/schema_validator.py` — Clean. Type coercion, unwrap logic, non-finite rejection all correct.
- `scripts/validator.py` — Clean. Failure classification and unit conservation correct.
- `scripts/expression_evaluator.py` — Clean. Seed derivation, evaluation order, system field exclusion correct.
- `scripts/providers/__init__.py` — Clean. CLI override precedence correct.
- `scripts/providers/gemini.py`, `openai.py`, `anthropic.py` — Clean. Timeouts correct.

### Overall assessment: `FIX FIRST`

The two BUG findings are real correctness issues. The RNG bug (line 4379) violates the determinism contract that is central to expression step correctness. The AuthenticationError bug (realtime_provider.py:91-107) could cause wasted API spend on invalid credentials. Both should be fixed before the next release.
