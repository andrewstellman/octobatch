# Quality Constitution: Octobatch

## Purpose

Octobatch is only fit for use if it can process large structured LLM workloads without silently losing units, corrupting run state, or producing plausible-but-wrong output. In this project, "quality" does not mean that a few local helpers return the expected shape. It means the orchestrator, validator, expression engine, and provider abstraction preserve unit identity and correctness from pipeline config through final validated artifacts.

This quality bar follows three principles. **Deming:** quality must be built into persistent project context so every AI session inherits the same expectations around manifests, validation, retries, and seeded randomness. **Juran:** fitness for use here means reproducible pipelines, durable crash recovery, and domain outputs that survive real LLM formatting drift instead of failing or corrupting silently. **Crosby:** the cost of encoding these expectations in tests and protocols is lower than debugging a half-finished batch run, a biased Monte Carlo result, or a broken validation chain after API spend has already happened.

## Coverage Targets

| Subsystem | Target | Why |
|-----------|--------|-----|
| `scripts/orchestrate.py` | 90%+ | This is the state machine that can silently lose, duplicate, or misclassify work if retries, validation, or terminal-state logic drift. Scenario 6 exists because retry isolation is easy to break while preserving superficially correct status output. |
| `scripts/schema_validator.py` | 90%+ | This module is the last structural gate before corrupted LLM output becomes "validated" data. Scenario 5 and Scenario 8 both show that rescue logic and non-finite number rejection must stay exact. |
| `scripts/expression_evaluator.py` + `scripts/generate_units.py` | 95%+ | Octobatch relies on deterministic local computation for repeatable simulation work. Scenario 4 exists because weak seeding can produce statistically wrong results that still look clean. |
| `scripts/config_validator.py` | 85%+ | The 4-Point Link Rule is the cheapest place to stop broken pipelines before any provider spend. A regression here turns design mistakes into runtime failures. |
| `scripts/run_tools.py` + `scripts/tui/utils/` | 80%+ | These modules do not generate provider output, but they are where operators decide whether a run is healthy, resumable, or safe to repair. Wrong reporting here hides real failures. |

## Coverage Theater Prevention

The following do **not** count as meaningful quality work in Octobatch:

- Asserting that a run directory exists without checking `MANIFEST.json`, chunk state, and validated/failure artifacts.
- Asserting that a helper returned a list or dict without checking exact `unit_id`, field values, retry counts, or state transitions.
- Testing Monte Carlo or expression logic with a single seed and calling it "deterministic."
- Mocking provider or TUI dependencies and asserting only that the mock was called.
- Checking that `_validated.jsonl` files exist without proving unit conservation or value correctness.
- Declaring a pipeline healthy because status is `complete` while ignoring failed units, `.bak` retry archives, or missing downstream artifacts.
- Treating schema validation as "works" because malformed input raises an error, without checking whether recoverable LLM drift is actually rescued.

## Fitness-to-Purpose Scenarios

### Scenario 1: Manifest Integrity After Crash

**Requirement tag:** [Req: inferred — from `octobatch_utils.save_manifest()` behavior]

**What happened:** Octobatch persists all run truth in `MANIFEST.json`, then derives `.manifest_summary.json` as a cache for the TUI. If the canonical manifest stops being atomic, or if downstream code starts trusting the cache instead of the manifest, a laptop sleep or mid-write crash can leave the UI and CLI disagreeing about run state. At scale, that means operators resume, kill, or archive the wrong run while 100+ chunk states still depend on the manifest.

**The requirement:** `save_manifest()` must keep `MANIFEST.json` authoritative and atomic, and callers must be able to load the canonical manifest even if the summary cache is stale or corrupted.

**How to verify:** Run `test_scenario_1_manifest_integrity_after_crash` in `quality/test_functional.py`.

---

### Scenario 2: Broken Pipelines Must Fail Before Spend

**Requirement tag:** [Req: formal — `specs/VALIDATION.md` § The 4-Point Link Rule]

**What happened:** LLM steps require synchronized step, template, schema, and validation entries. If config lint weakens, a pipeline with a missing mapping still initializes, prompts get generated, and provider spend begins before the failure is discovered. That turns a naming mistake into wasted API calls across every chunk.

**The requirement:** `validate_config_run()` must reject incomplete LLM steps before runtime and identify which link is missing.

**How to verify:** Run `test_scenario_2_broken_pipeline_stopped_before_spend` in `quality/test_functional.py`.

---

### Scenario 3: Provider Override Drift

**Requirement tag:** [Req: formal — `specs/PROVIDERS.md` § Provider/Model Resolution]

**What happened:** Octobatch supports global config, per-step overrides, and CLI overrides. If CLI override tracking drifts, a supposedly forced all-provider test can quietly fall back to a step-level override. The run still "works," but the cost, latency, and model behavior no longer match the operator's intent.

**The requirement:** `providers.get_step_provider()` must honor CLI override metadata over step-level provider/model settings, and only apply step overrides when CLI flags were not used.

**How to verify:** Run `test_scenario_3_provider_override_drift_prevented` in `quality/test_functional.py`.

---

### Scenario 4: Statistical Bias Hidden by Clean Output

**Requirement tag:** [Req: formal — `specs/EXPRESSION.md` § Seeded Randomness]

**What happened:** The earlier Drunken Sailor bug proved that sequential or poorly distributed seeds can produce statistically biased outcomes that still look internally consistent. A 300-repeat batch can complete cleanly, validate cleanly, and still be wrong in a way only distribution-aware checks would catch.

**The requirement:** `generate_units()` and the expression system must produce deterministic but well-distributed repetition seeds, and repeated units must be interleaved so `--max-units` samples remain representative.

**How to verify:** Run `test_scenario_4_statistical_seed_bias_defense` in `quality/test_functional.py` and the Drunken Sailor distribution checks in `tests/integration_checks.py`.

---

### Scenario 5: Markdown-Wrapped JSON Must Be Rescued, Not Lost

**Requirement tag:** [Req: formal — `specs/VALIDATION.md` § Pre-Validation Sanitization]

**What happened:** Real LLMs routinely return fenced JSON, nested `response` payloads, and trailing commas. If `_unwrap_response()` or `validate_line()` regresses, valid content gets thrown away as malformed input and the system spends retries on formatting noise instead of real model mistakes.

**The requirement:** schema validation must unwrap nested JSON responses, remove common trailing-comma drift, and preserve the recovered structured fields for downstream validation.

**How to verify:** Run `test_scenario_5_markdown_wrapped_json_rescued` in `quality/test_functional.py`.

---

### Scenario 6: Retry Isolation Must Preserve Good Work

**Requirement tag:** [Req: inferred — from `orchestrate.retry_validation_failures()` behavior]

**What happened:** Retrying an entire chunk because one or two units failed replays successful work, increases spend, and can create duplicate or contradictory artifacts. The retry-isolation logic exists precisely because unit-level retries are safer than chunk resets when a 100-unit chunk contains 98 good records and 2 bad ones. This scenario is about isolation within a single invocation: retryable validation failures must be separated from hard failures during that run, even though a later `--realtime` invocation intentionally starts with a fresh retry budget.

**The requirement:** `retry_validation_failures()` must create retry chunks only for retryable validation failures, archive the original failure file to `.bak`, and leave hard failures plus successful units in place. Within an invocation, the terminalization logic must treat chunks with only hard failures as exhausted, not as retryable work.

**How to verify:** Run `test_scenario_6_retry_isolation_preserves_good_work` in `quality/test_functional.py`.

---

### Scenario 7: Definition Order and Determinism Are the Contract

**Requirement tag:** [Req: formal — `specs/EXPRESSION.md` § Evaluation Order]

**What happened:** Expression steps replace paid LLM calls with free deterministic computation. If expression order or RNG stability drifts, later expressions can read stale values or different random results on resume, turning "deterministic" business logic into hidden nondeterminism.

**The requirement:** `evaluate_expressions()` must evaluate expressions in definition order, inject each result into subsequent context, and return identical output for the same seed and context.

**How to verify:** Run `test_scenario_7_expression_order_and_determinism_are_contract` in `quality/test_functional.py`.

---

### Scenario 8: Non-Finite Numbers Never Become Validated Output

**Requirement tag:** [Req: formal — `specs/VALIDATION.md` § What Schema Validation Catches]

**What happened:** `NaN`, `Infinity`, and `-Infinity` are especially dangerous in LLM and post-processed outputs because they pass through Python more easily than they pass through real JSON. If they are coerced or ignored, downstream analytics and reports can quietly consume impossible values.

**The requirement:** `schema_validator.validate_line()` must surface non-finite numbers as structured schema failures, never as silently accepted output.

**How to verify:** Run `test_scenario_8_non_finite_numbers_never_sneak_through` in `quality/test_functional.py`.

## AI Session Quality Discipline

1. Read this file before editing orchestration, validation, provider, or TUI state logic.
2. Treat `MANIFEST.json` as the single source of truth; any cache or summary file is secondary.
3. Before trusting a change, run `pytest quality/test_functional.py -v` and then `pytest tests/`.
4. For any change touching retries, validation, seeded randomness, or provider resolution, add or update a functional test that exercises the actual code path.
5. Do not weaken thresholds, retry rules, or coverage targets without citing the scenario that justifies the change.
6. If a new failure mode is discovered, add a scenario here before ending the session.

## The Human Gate

These still require human judgment:

- Whether pipeline prompts and schemas are semantically fit for a new domain.
- Whether integration-test API spend is acceptable for a given release cycle.
- Whether output text quality is good enough for user-facing pipelines.
- Whether provider/model tradeoffs favor cost, latency, or scoring accuracy.
- Whether a spec-audit finding reflects a real product decision or a stale spec.
