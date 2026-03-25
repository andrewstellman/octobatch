# Code Review Protocol: Octobatch

## Bootstrap (Read First)

Before reviewing code, read these files in order:

1. `quality/QUALITY.md` — quality constitution and the eight fitness scenarios
2. `AGENTS.md` — project bootstrap, setup, design decisions, and coverage expectations
3. `ai_context/PROJECT_CONTEXT.md` — architecture and system data flow
4. `scripts/CONTEXT.md` — orchestrator internals and operational constraints
5. `specs/ORCHESTRATOR.md`, `specs/VALIDATION.md`, `specs/EXPRESSION.md`, `specs/PROVIDERS.md`, `specs/TUI.md` — intent specs for comparison

## What to Check

### Focus Area 1: Run State Machine and Manifest Durability

**Where:** `scripts/orchestrate.py`, `scripts/octobatch_utils.py`

**What:** Chunk state transitions, terminal-state detection, manifest save paths, retry-state mutation, summary/cache usage, and any code that can mark a run `complete`, `failed`, `paused`, or `running`.

**Why:** This is where silent attrition, duplicate work, and wrong operator actions originate. Scenario 1 and Scenario 6 both depend on these paths staying exact.

### Focus Area 2: Validation Pipeline and Failure Classification

**Where:** `scripts/orchestrate.py::run_validation_pipeline()`, `scripts/schema_validator.py`, `scripts/validator.py`

**What:** Rescue logic for wrapped JSON, structured failure records, `failure_stage` classification, unit conservation between schema and business validation, and handling of malformed provider output.

**Why:** A regression here turns recoverable LLM drift into wasted retries or, worse, silently accepted bad data.

### Focus Area 3: Expression Determinism and Statistical Correctness

**Where:** `scripts/expression_evaluator.py`, `scripts/generate_units.py`, expression-step paths in `scripts/orchestrate.py`

**What:** Seed derivation, repetition ordering, evaluation order, loop timeout handling, and any change to `_repetition_seed`, `unit_id`, or expression context rules.

**Why:** These modules produce output that can be numerically wrong while still looking structurally valid. Scenario 4 and Scenario 7 exist because small seeding mistakes create large hidden bias.

### Focus Area 4: Config Linting and the 4-Point Link Rule

**Where:** `scripts/config_validator.py`, pipeline `config.yaml` files, `pipelines/TOOLKIT.md`

**What:** Missing or mismatched step/template/schema/validation links, expression-step exemptions, run-scope and fan-out edge cases, and validation error reporting.

**Why:** This is the cheapest place to stop broken pipelines before provider spend begins.

### Focus Area 5: Provider Resolution and External Failure Boundaries

**Where:** `scripts/providers/__init__.py`, `scripts/realtime_provider.py`, `scripts/providers/*.py`

**What:** CLI-vs-step override precedence, auth/rate-limit categorization, timeout handling, and provider-specific parsing assumptions.

**Why:** Incorrect resolution or transient/fatal error classification makes test runs lie about which model actually executed and whether retries are safe.

### Focus Area 6: TUI Truthfulness and Operator Safety

**Where:** `scripts/tui/`, `scripts/tui_dump.py`, `scripts/tui/utils/runs.py`

**What:** Status display logic, detached/zombie detection, dump-mode fidelity, archive/resume safety checks, and any code that interprets PID files or cached summaries.

**Why:** The TUI is the operator's control surface. If it misreports state, humans make destructive decisions based on stale or partial truth.

## Guardrails

- **Line numbers are mandatory.** If you cannot cite a specific line, do not include the finding.
- **Read function bodies, not just signatures.** Names like `retry_validation_failures()` and `mark_run_complete()` are not evidence that the implementation is correct.
- **If unsure whether something is a bug or intentional, label it `QUESTION`, not `BUG`.**
- **Grep before claiming missing.** If you think a feature is absent, search for it first. If it exists elsewhere, that is a location or integration issue, not a missing feature.
- **Do NOT suggest style changes, refactors, or naming cleanups.** Only report correctness, safety, durability, or spec-divergence issues.
- **Check the scenario impact.** For each finding, say which QUALITY scenario it threatens.

## Output Format

Save findings to `quality/code_reviews/YYYY-MM-DD-reviewer.md`.

For each reviewed file:

### path/to/file.py
- **Line NNN:** `BUG` / `QUESTION` / `INCOMPLETE` — concise description of the problem.
  - **Expected:** What the code or spec requires
  - **Actual:** What the code currently does
  - **Why it matters:** Which QUALITY scenario or spec requirement this threatens

### Summary
- Total findings by severity/classification
- Files reviewed with no findings
- Overall assessment: `SHIP IT` / `FIX FIRST` / `NEEDS DISCUSSION`

## Review Checklist

- Verify manifest writes stay atomic and canonical
- Verify unit conservation across validation and retry paths
- Verify expression logic is deterministic and seed-stable
- Verify provider/model resolution matches the documented precedence
- Verify TUI status logic reflects manifest truth, not cached assumptions
- Verify new tests target outcomes, not just mechanisms
