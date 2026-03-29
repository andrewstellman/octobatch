# Code Review Fix Prompt — 2026-03-25

## Context

Three independent code reviews of `scripts/orchestrate.py` and `scripts/realtime_provider.py` found 7 actionable items. Read the full reports in `quality/code_reviews/` for details. Read `quality/QUALITY.md` for the fitness scenarios referenced below.

## Design Decision (not a code bug)

**Retry budgets are per-invocation, not per-unit-lifetime.** The `retry_count` reset at `orchestrate.py:5472` is intentional — when a user re-runs `--realtime`, exhausted units get a fresh retry budget. This is the expected behavior. However, this design choice is not documented in the specs. As part of this fix batch:

- Update `specs/VALIDATION.md` to state that retry budgets reset on each `--realtime` invocation
- Update `quality/QUALITY.md` Scenario 6 to clarify that retry isolation refers to within-invocation isolation (separating retryable from hard failures), not cross-invocation durability
- Update the comment at `orchestrate.py:5472` to reference the spec section

## Fix 1: Add failure_stage filter to realtime retries

**Source:** GPT-5.4 report, line 5032
**Scenario:** 6 (Retry Isolation Must Preserve Good Work)

`run_realtime_retries()` retries any failure with `retry_count < max_retries` without checking `failure_stage`. It should only retry `schema_validation` and `validation` failures. `pipeline_internal` failures are non-retryable.

**Where to fix:** `scripts/orchestrate.py` around line 5032. After reading the failure record, check `failure.get("failure_stage")` and skip if it's not in `{"schema_validation", "validation"}`.

**Verify:** Add a test in `quality/test_functional.py` that creates a failure with `failure_stage: "pipeline_internal"` and confirms `run_realtime_retries()` skips it.

## Fix 2: has_retryable should check failure_stage, not just file existence

**Source:** GPT-5.4 report, line 4008
**Scenario:** 1 (Manifest Integrity), 6 (Retry Isolation)

The terminalization guard at line 4008 treats any non-empty `*_failures.jsonl` as retryable. If a chunk only has `pipeline_internal` failures, it stays non-terminal forever — no code path can advance it, but nothing marks it terminal either.

**Where to fix:** `scripts/orchestrate.py` around line 4008. Instead of just checking `failures_file.exists() and failures_file.stat().st_size > 0`, read the file and check whether any record has `failure_stage in {"schema_validation", "validation"}`.

**Verify:** Add a test that creates a chunk with only `pipeline_internal` failures and confirms the terminalization guard marks it terminal.

## Fix 3: check_prerequisites should respect CLI provider override

**Source:** GPT-5.4 report, line 503
**Scenario:** 3 (Provider Override Drift)

`check_prerequisites()` unions all providers from config without consulting `manifest.metadata.cli_provider_override`. A run forced to Gemini can be blocked for a missing OpenAI key it will never use.

**Where to fix:** `scripts/orchestrate.py` at `check_prerequisites()` (line 503). Add an optional `manifest` parameter. If `manifest.metadata.cli_provider_override` is True, only check the key for `config.api.provider` instead of unioning all step-level providers.

**Verify:** Add a test that passes a config with step-level OpenAI overrides but a manifest with `cli_provider_override: True` and `api.provider: gemini`, and confirm it only requires `GOOGLE_API_KEY`.

## Fix 4: Fix failed count undercount after retry merge

**Source:** GPT-5.4 report, line 5299
**Scenario:** 1 (Manifest Integrity), 6 (Retry Isolation)

After merging retry results back, `chunk_data["failed"]` only counts remaining failures whose `unit_id` is in `retryable_failures`, excluding hard failures that were preserved in the same file.

**Where to fix:** `scripts/orchestrate.py` around line 5299. Change the count to `len(remaining_failures)` — all failures remaining in the file, not just the ones that were retried.

**Verify:** Add a test that has both retryable and hard failures in a chunk, runs a retry merge, and confirms `chunk_data["failed"]` equals the total remaining failure count.

## Fix 5: Pass RNG to evaluate_condition for loop_until

**Source:** Claude Opus report, line 4379
**Scenario:** 4 (Statistical Bias), 7 (Expression Determinism)

`evaluate_condition(loop_until_expr, output_unit)` doesn't pass the persistent `rng` instance as the third argument. If a `loop_until` expression uses `random.*`, it gets an unseeded interpreter.

**Where to fix:** `scripts/orchestrate.py` line 4379. Change `evaluate_condition(loop_until_expr, output_unit)` to `evaluate_condition(loop_until_expr, output_unit, rng)`.

**Verify:** Add a test with a `loop_until` expression that uses `random.random()` and confirm it produces identical results across two evaluations with the same seed.

## Fix 6: Catch AuthenticationError explicitly before ProviderError

**Source:** Claude Opus report, realtime_provider.py lines 91-107
**Scenario:** 2 (Broken Pipelines Stopped Before Spend)

`AuthenticationError` is a subclass of `ProviderError` and gets caught by the generic handler. The code then string-matches for "400"/"401"/"403" in the error message, which is fragile. An `AuthenticationError` with a message like "Invalid API key" (no HTTP code) would be treated as non-fatal.

**Where to fix:** `scripts/realtime_provider.py` around line 91. Add an `except AuthenticationError` clause before the `except ProviderError` clause, and raise `FatalProviderError` unconditionally.

**Verify:** Add a test that raises `AuthenticationError("Invalid API key")` (no HTTP code in the message) and confirm it's treated as fatal.

## Execution Instructions

1. Read `quality/QUALITY.md` and the three code review reports in `quality/code_reviews/`.
2. Implement fixes 1-6 and the spec/docs clarification.
3. For each fix, add or update a test in `quality/test_functional.py`.
4. Run `pytest quality/test_functional.py -v` — all tests must pass.
5. Run `pytest tests/` — all 1083 existing tests must still pass.
6. Commit each fix separately with a clear message referencing the finding and scenario.
