# CURRENT_VALIDATION.md — As-Is Intent Specification

## Purpose

This document describes what Octobatch's validation pipeline **should** do based on our design discussions. Written from intent, not code inspection. Gaps between this spec and the actual implementation are defects.

**Quality mapping:** Maps primarily to QUALITY.md Scenario 8 (Validation Catches Real Errors).

---

## Validation Philosophy

Every LLM response is untrusted. LLMs produce plausible-but-wrong output: correct JSON structure with invalid values, trailing commas, string-typed numbers, missing fields. The validation pipeline is the only gate between LLM hallucination and corrupted data.

Validation is a two-phase process:
1. **Schema validation** — Is the response structurally correct?
2. **Business logic validation** — Is the response semantically correct?

Both phases must pass for a unit to be accepted.

---

## The 4-Point Link Rule

Every LLM pipeline step requires four synchronized entries in the config:

1. **Step name** in `pipeline.steps[].name`
2. **Template mapping** in `prompts.templates.{step_name}`
3. **Schema mapping** in `schemas.files.{step_name}`
4. **Validation rules** in `validation.{step_name}`

If any of the four is missing or misnamed, the step will fail. `--validate-config` catches these mismatches before runtime.

Expression steps are exempt — they only need the step entry in `pipeline.steps`.

---

## Pre-Validation Sanitization

Before the two-phase validation pipeline runs, the orchestrator must extract valid JSON from the raw LLM response. LLMs frequently wrap their JSON output in markdown code blocks or include preamble text.

### Markdown Block Extraction

The intended behavior is that the orchestrator (or the realtime provider) strips markdown code fences before parsing:

- `` ```json { ... } `` `` → extracted to `{ ... }`
- `` ``` { ... } `` `` → extracted to `{ ... }`
- Raw JSON without fences → used as-is

This happens before the response reaches `schema_validator.py`. The schema validator expects clean JSONL input, not markdown-wrapped text.

### Non-JSON Responses

When a provider returns something that isn't JSON at all (e.g., a 500 error HTML page, a plain text apology, or a truncated response):

- The orchestrator should catch the JSON parse failure
- Log the raw response for debugging
- Record the unit as a `pipeline_internal` failure (not `schema_validation`, since it never reached schema validation)
- Preserve the raw text in `raw_response` for the failure record

### Batch vs Realtime Differences

- **Realtime mode:** The realtime provider (`realtime_provider.py`) handles markdown extraction during response parsing, before returning results to the orchestrator
- **Batch mode:** The orchestrator extracts results from the provider's batch response format (e.g., Gemini's batch result JSONL), then handles markdown extraction before feeding to validation

**Note from review:** The exact behavior of `raw_response` capture across both execution modes should be verified against the code. The intent is that `raw_response` preserves the LLM's original text before any parsing or extraction, in both batch and realtime modes.

---

## Phase 1: Schema Validation

### Implementation

`scripts/schema_validator.py`

### Behavior

- Reads JSONL from stdin (one JSON object per line)
- Validates each object against the JSON Schema for the step (Draft 2020-12)
- **The schema validator preserves all fields** — it validates structure but does not strip unrecognized fields from the output. Note: Response unwrapping is an intentional exception. When the validator detects a double-encoded JSON response (a string containing JSON inside a JSON field), it unwraps the inner content. This is mutation of a parsing artifact, not a violation of field preservation.
- **Valid units:** Written to stdout as JSONL (with all original fields intact)
- **Failed units:** Written to stderr as JSONL failure records
- Each unit must contain a `unit_id` field for tracking

### JSON Schema Standards

- Schemas live in `pipelines/{pipeline}/schemas/{step_name}.json`
- Snapshotted to `{run_dir}/config/schemas/` on `--init`
- **Never use `additionalProperties: false`** — the orchestrator injects `unit_id` and `_metadata` fields into every response before validation. Setting `additionalProperties: false` will reject these injected fields.

### Automatic Type Coercion

Before strict schema validation, the schema validator applies automatic type coercion to rescue units where the LLM returned minor type mismatches or formatting issues. This saves units that would otherwise fail for trivial reasons, avoiding unnecessary retries. Each coercion is logged per-field: `[COERCE] field_name: "5" → 5 (string → int)`.

The full coercion surface:

| Coercion | Description |
|----------|-------------|
| String → integer | `"5"` → `5` |
| String → float | `"3.14"` → `3.14` |
| String → boolean | `"true"` → `true`, `"false"` → `false` |
| Float → integer | `5.0` → `5` (whole numbers only) |
| String → array | Via JSON parsing (e.g., `"[1,2,3]"` → `[1,2,3]`), with single-value wrapping fallback (e.g., `"foo"` → `["foo"]`) |
| Enum normalization | Strips `"shadow "` prefix, splits on `" \| "` delimiter, case-insensitive matching. **Domain-specific (Tarot):** this handles Shadow-prefixed card names and multi-value enum fields in the Tarot pipeline. |
| Response unwrapping | Handles double-wrapped LLM responses where JSON is nested inside a `"response"` key, with possible markdown code fences inside the value |
| Trailing comma removal | Removes trailing commas before `}` or `]` to fix common LLM JSON formatting errors |
| `$ref` resolution | Resolves JSON Schema `$ref` pointers so coercion can traverse into referenced sub-schemas |

### What Schema Validation Catches

After type coercion, schema validation catches remaining structural errors:

- Missing required fields
- Wrong data types that couldn't be coerced
- Values outside enum lists
- Array length violations
- Number range violations (minimum, maximum)
- Malformed JSON (trailing commas, missing brackets, unclosed strings)

---

## Phase 2: Business Logic Validation

### Implementation

`scripts/validator.py`

### Behavior

- Reads JSONL from stdin (with all accumulated fields from prior pipeline steps)
- Applies expression-based rules from the `validation:` section of config
- **Valid units:** Written to stdout
- **Failed units:** Written to stderr as failure records
- Uses `asteval` for safe expression evaluation

### Validation Rule Types

#### Required Fields
```yaml
validation:
  step_name:
    required:
      - field_a
      - field_b
```
Checks that all listed fields are present and non-null.

#### Type Checking
```yaml
    types:
      score: number
      reasoning: string
      items: array
```
Validates field types. Supported types: `string`, `number`, `boolean`, `object`, `array`.

#### Enum Validation
```yaml
    enums:
      tone: ["warm", "cold", "nervous", "hostile", "mysterious"]
```
Validates that the field value is in the allowed list. Matching is case-insensitive.

#### Range Validation
```yaml
    ranges:
      score: [1, 10]
      probability: [0.0, 1.0]
```
Validates that numeric field values fall within `[min, max]` bounds (inclusive).

#### Expression Rules
```yaml
    rules:
      - name: personality_threshold
        expr: "personality_consistency >= 0.6"
        error: "Personality consistency {personality_consistency} is below threshold 0.6"
        level: error
      - name: mood_warning
        expr: "mood_responsiveness >= 0.4"
        error: "Low mood responsiveness: {mood_responsiveness}"
        level: warning
      - name: wound_count_check
        expr: "wound_count == len([v for v in wounds.values() if v > 0])"
        error: "wound_count {wound_count} doesn't match actual non-zero wounds"
        level: error
        when: "'wounds' in dir() and 'wound_count' in dir()"
```

Each rule has:
- `name`: Identifier for the rule
- `expr`: An asteval expression that must evaluate to `True`
- `error`: Error message template with `{field_name}` interpolation
- `level`: `error` (fails the unit, triggers retry) or `warning` (logs only, unit still passes)
- `when` (optional): An asteval expression that must evaluate to `True` for the rule to fire. If the `when` condition is false or the referenced fields don't exist, the rule is skipped. This enables conditional rule execution.

Rules can reference any field from the LLM response and any accumulated field from prior pipeline steps, since the data is pre-merged before validation.

---

## The Validation Pipeline (Orchestrator Integration)

### Data Flow

The orchestrator's `run_validation_pipeline()` function manages the two-phase process:

```
LLM Response (raw)
    │
    ▼
Pre-merge: {**input_item, **raw_result}
    │
    ▼
Phase 1: schema_validator.py
  - Input: Pre-merged JSONL via stdin
  - Valid → stdout (all fields preserved)
  - Failed → stderr (failure records)
    │
    ▼
Python processing: Collect Phase 1 results, prepare Phase 2 input
    │
    ▼
Phase 2: validator.py
  - Input: JSONL with all accumulated fields
  - Valid → stdout
  - Failed → stderr (failure records)
    │
    ▼
Output files:
  - {step}_validated.jsonl (passed both phases)
  - {step}_failures.jsonl (failed either phase)
```

### Pre-Merge Step

Before validation, the orchestrator merges the raw LLM output with the input context:

```python
merged = {**input_item, **raw_result}
```

The merge order ensures LLM output overwrites any same-named fields from the input. This gives the validation pipeline access to all accumulated data from prior steps.

### Subprocess Execution

The two phases run as sequential subprocess calls using `communicate()`. Phase 1 completes fully before Phase 2 starts:

1. Start `schema_validator.py`, feed pre-merged JSONL to stdin, collect stdout/stderr via `communicate()`
2. Process Phase 1 results in Python — collect passed units, log counts
3. Start `validator.py`, feed passed units to stdin, collect stdout/stderr via `communicate()`

Each phase has a wall-clock timeout. The remaining time from Phase 1 is carried forward to Phase 2. The total timeout is configurable via `api.subprocess_timeout_seconds` (default 600 seconds).

**Design note:** An earlier implementation chained the validators via pipes (`schema_validator.py | validator.py` with 4 threads managing the streams), but this deadlocked on batches of 85+ units due to pipe buffer limits. The sequential `communicate()` approach handles arbitrary data sizes correctly.

### Progress Logging Between Phases

The orchestrator logs:
- Schema validation count (how many passed Phase 1)
- Business logic validation start
- Per-phase timing

---

## Failure Records

### Structure

When a unit fails validation, a failure record is written to `{step}_failures.jsonl`:

```json
{
  "unit_id": "the_fool-the_magician-the_priestess",
  "failure_stage": "schema_validation",
  "input": { ... },
  "raw_response": "The raw text the LLM returned before any parsing",
  "errors": [
    {
      "path": "$.score",
      "message": "'high' is not of type 'number'"
    }
  ],
  "retry_count": 0
}
```

### Fields

| Field | Description |
|-------|-------------|
| `unit_id` | Which unit failed |
| `failure_stage` | Where it failed: `schema_validation`, `validation`, or `pipeline_internal` |
| `input` | The step's input context (for debugging and retry) |
| `raw_response` | The original LLM output text, preserved before any JSON parsing |
| `errors` | Array of error objects with `path` and `message` |
| `retry_count` | Number of attempts so far |

### Failure Categories

| Stage | Retryable | Description |
|-------|-----------|-------------|
| `schema_validation` | Yes | LLM output failed JSON schema checks. A different response may pass. |
| `validation` | Yes | LLM output passed schema but failed business logic rules. A different response may pass. |
| `pipeline_internal` | No | Records were lost in the pipeline (e.g., missing input file, subprocess crash). Retrying won't help — indicates a system error. |

### Error Field Names

Both `schema_validator.py` and `validator.py` emit both `"path"` and `"rule"` fields in error records. Schema validation errors include a `"path"` (a JSON pointer like `$.score`) and business logic validation errors include a `"rule"` (the business logic rule name like `personality_threshold`). Both fields are present in all error records for consistent downstream consumption.

### raw_response Preservation

Before overwriting the failure record's `input` field with the step input context, the original validator output (the raw LLM text) is saved in `raw_response`. This enables debugging by showing exactly what the LLM said, even after the failure record is enriched with input context.

### Re-Validation Support

The combination of `raw_response` (original LLM text) and `input` (accumulated context from prior steps) in failure records enables re-validation: feeding existing responses through updated validators without re-calling the LLM. See ORCHESTRATOR.md §Re-Validation for the full pipeline.

---

## Retry Routing

### From the Orchestrator

At the start of `watch_run()` and `realtime_run()`:
1. Scan all chunks for failures with `failure_stage` of `schema_validation` or `validation`
2. Archive to `{step}_failures.jsonl.bak`
3. Preserve `pipeline_internal` failures in the original file
4. Reset affected chunks to `{step}_PENDING`

### From the TUI

The TUI's retry function (`reset_unit_retries()` in `runs.py`):
1. Creates `.bak` signal file before modifying failures
2. Resets chunk state to `{step}_PENDING`
3. The `.bak` file ensures the orchestrator won't skip the step via the 90% idempotency fallback

### Retry Count Tracking

Each failure record's `retry_count` increments on each attempt. After `api.retry.max_attempts` (or `processing.validation_retry.max_attempts`), the unit stays in the failures file permanently.

---

## Zero Valid Units Guard

When a step produces 0 valid units and N failures, the chunk must be marked FAILED immediately. It must not advance to the next step, because:
- The next step would receive an empty input file
- Batch mode: the provider API rejects empty batches with 400 INVALID_ARGUMENT
- This triggers infinite retries since the empty-batch error is transient-looking
- The root cause (all units failed validation) is obscured

Both `tick_run()` (batch) and `run_step_realtime()` (realtime) check for this condition.

---

## Config Validation (`--validate-config`)

`scripts/config_validator.py` validates pipeline configs before runtime:

### What It Checks

1. **Required config sections** — `pipeline`, `prompts`, `schemas`, `api`, `processing` all present
2. **4-Point Link Rule** — Every step has matching entries in templates, schemas, and validation
3. **Template existence** — Referenced Jinja2 files exist on disk
4. **Schema validity** — Referenced JSON schemas parse correctly
5. **Expression syntax** — Expression steps' expressions evaluate without error using mock context
6. **Validation rule syntax** — Business logic expressions parse correctly

### What It Doesn't Check

- Whether templates produce valid prompts for the LLM
- Whether schemas match what the LLM actually returns
- Whether validation rules are too strict or too lenient
- Runtime behavior (API connectivity, batch processing)

These require actual test runs, preferably with `--max-units 5`.

---

## Streaming I/O

**Known limitation:** The validation pipeline currently reads full result sets into memory rather than streaming. At current chunk sizes (~100 units) this is not a problem. Streaming implementation is planned (DEF-015) to support larger chunk sizes.

---

## Injected Fields

The orchestrator injects metadata fields into every LLM response before validation:

- `unit_id` — The unit's unique identifier
- `_metadata` — Processing metadata (timestamps, token counts, etc.)

Schemas must not use `additionalProperties: false` or these injected fields will cause validation failures.

---

## Known Issues and Technical Debt

1. **Sequential subprocess execution** — The two-phase pipeline runs sequentially, not in parallel. For large batches this adds latency. The original threaded pipe approach was faster but deadlocked.
2. **Validation timeout is shared** — A single timeout covers both phases. If Phase 1 is slow, Phase 2 may time out even if it would be fast.
3. **Hardcoded count validation discovered late** — Our Tarot pipeline initially hardcoded `== 8` for story counts, but the actual count varies (4 base + 0-6 inversions). Fixed with dynamic `len(all_stories)`. A reminder that validation rules are only as good as their expressions.
4. **~~Inconsistent error field names (DEF-022)~~ Resolved** — Both `schema_validator.py` and `validator.py` now emit both `"path"` and `"rule"` fields in error records for consistent downstream consumption.
5. **~~`raw_response` contains parsed dicts (DEF-004/DEF-005)~~ Resolved** — Both the realtime path (`realtime_provider.py`) and the batch path (`orchestrate.py` line 2262) inject `_raw_text` containing the original unparsed LLM response string before validation.
