# CURRENT_ORCHESTRATOR.md — As-Is Intent Specification

## Purpose

This document describes what the Octobatch orchestrator and CLI **should** do based on our design intent. It is written from memory of design discussions, not from reading the code. Gaps between this spec and the actual implementation are defects to be triaged.

---

## CLI Interface

### Entry Point

`python scripts/orchestrate.py [flags]`

### Initialization Flags

| Flag | Purpose |
|------|---------|
| `--init` | Initialize a new run from a pipeline config |
| `--pipeline NAME` | Pipeline name (looks in `pipelines/{NAME}/config.yaml`) |
| `--run-dir PATH` | Explicit run directory path |
| `--provider PROVIDER` | Override provider for all steps (gemini/openai/anthropic) |
| `--model MODEL` | Override model for all steps |
| `--max-units N` | Limit the number of units processed (for testing/cost control) |
| `--yes` | Skip confirmation prompts |

### Execution Flags

| Flag | Purpose |
|------|---------|
| `--watch` | Start batch mode — submit batches and poll for completion |
| `--realtime` | Start realtime mode — process units one at a time via direct API calls |
| `--run-dir PATH` | Required for execution — specifies which run to process |

### Operational Flags

| Flag | Purpose |
|------|---------|
| `--tick` | Advance the run by one step (single tick, no loop) |
| `--status` | Show current run status as JSON |
| `--retry-failures` | Create retry chunks from failed units |
| `--repeat N` | Override `processing.repeat` count |
| `--max-retries N` | Maximum retry attempts per item (default 5) |
| `--interval N` | Seconds between polls in watch mode (default 5) |
| `--max-cost USD` | Stop if estimated cost exceeds amount (USD) |
| `--timeout DURATION` | Stop after duration (e.g., `30m`, `2h`) |
| `--quiet` | Suppress console output (log files still written) |
| `--revalidate` | Re-run validation on existing failures without API calls (requires `--step`) |
| `--step STEP` | Step name (used with `--revalidate`) |
| `--use-source-config` | Use source pipeline config instead of run snapshot (used with `--revalidate`) |

### Utility Flags

| Flag | Purpose |
|------|---------|
| `--version` | Print version and exit |
| `--ps` | List all runs with status, progress, cost |
| `--info` | Print detailed run information (requires `--run-dir`) |
| `--verify` | Check run integrity — find missing, duplicated, or orphaned units (requires `--run-dir`) |
| `--repair` | Create retry chunks for missing units found by `--verify` (requires `--run-dir` and `--yes`) |
| `--json` | Output in JSON format (used with `--ps`, `--info`, `--verify`) |
| `--validate-config` | Validate a pipeline config without running it (checks schema, templates, expressions) |
| `--config PATH` | Path to config file (used with --validate-config) |

### Flag Combinations

- `--init --pipeline Tarot --run-dir runs/my_run --provider gemini --yes` — Initialize and confirm
- `--init --pipeline Tarot --run-dir runs/my_run --realtime --yes` — Initialize and immediately start realtime
- `--watch --run-dir runs/my_run` — Resume/start batch mode on existing run
- `--realtime --run-dir runs/my_run --yes` — Resume/start realtime mode on existing run
- `--ps` — List all runs
- `--ps --json` — List all runs as JSON
- `--info --run-dir runs/my_run` — Show run details
- `--verify --run-dir runs/my_run` — Check run integrity
- `--repair --run-dir runs/my_run --yes` — Fix missing units
- `--verify --run-dir runs/my_run --json` — Integrity report as JSON

### Provider/Model Resolution

Priority order (highest wins):
1. CLI flags (`--provider`, `--model`)
2. Per-step config in pipeline YAML (`provider`/`model` on step definition)
3. Global `api.provider` / `api.model` in pipeline config
4. Registry default model from `scripts/providers/models.yaml`

CLI flags override everything — `--provider openai` forces all steps to use OpenAI regardless of per-step config. This is tracked in the manifest metadata as `cli_provider_override` and `cli_model_override`.

---

## Initialization Process

When `--init` is specified:

1. **Load pipeline config** from `pipelines/{NAME}/config.yaml`
2. **Snapshot config** — Copy config.yaml, templates, schemas, and items file into `runs/{run_dir}/config/`. This snapshot makes the run self-contained and reproducible even if the source pipeline changes.
3. **Generate units** — Based on processing strategy:
   - `permutation`: Generate all ordered permutations from items (e.g., 22P3 = 9,240 for Major Arcana)
   - `cross_product`: Cartesian product
   - `direct`: Items used as-is
4. **Apply --max-units** — If specified, truncate the unit list after generation
5. **Chunk units** — Split into chunks of `processing.chunk_size` (default 100)
6. **Write chunk files** — Each chunk gets `chunks/chunk_NNN/units.jsonl`
7. **Create MANIFEST.json** — Initial state with all chunks at `{first_step}_PENDING`
8. **Store metadata** — Pipeline name, start time, provider/model overrides, max_units

---

## The Manifest

`MANIFEST.json` is the single source of truth for run state. It contains:

### Top-Level Fields

| Field | Description |
|-------|-------------|
| `config` | Relative path to config snapshot |
| `created` | ISO timestamp of run initialization |
| `updated` | ISO timestamp of last manifest write |
| `status` | Overall run status: `running`, `paused`, `complete`, `failed`, `killed` |
| `pipeline` | Ordered list of step names |
| `chunks` | Map of chunk_name → chunk state object |
| `metadata` | Token counts, cost tracking, mode, pipeline name, overrides |
| `completed_run_steps` | List of run-scoped steps that have finished (e.g., extract_units) |

### Chunk State Object

| Field | Description |
|-------|-------------|
| `state` | Current state string (e.g., `generate_PENDING`, `score_coherence_SUBMITTED`, `VALIDATED`) |
| `batch_id` | Provider batch ID when submitted (null otherwise) |
| `items` | Number of units in the chunk |
| `valid` | Count of valid units (cumulative across steps) |
| `failed` | Count of failed units |
| `retries` | Number of retry attempts |
| `submitted_at` | ISO timestamp of last batch submission |
| `provider_status` | Last known provider-side status |
| `input_tokens` | Cumulative input tokens for this chunk |
| `output_tokens` | Cumulative output tokens for this chunk |

### Token Accounting

Tracked separately in metadata:
- `initial_input_tokens` / `initial_output_tokens` — First-attempt tokens
- `retry_input_tokens` / `retry_output_tokens` — Retry tokens

This separation enables accurate cost attribution and cost cap enforcement.

### Manifest Write Pattern

Manifests must be written atomically: write to a temp file, then rename. This prevents corruption if the process is killed mid-write.

### Manifest Summary

Every `save_manifest()` call also writes `.manifest_summary.json` — a lightweight summary (~300 bytes) containing status, progress, unit counts, cost, tokens, mode, pipeline name, and timestamps. The TUI reads this file for the home screen DataTable instead of parsing the full manifest. The summary is derived from the manifest and is not authoritative — the full MANIFEST.json remains the single source of truth.

### Manifest Lifecycle

Not all manifest fields exist at init. Fields are lazily populated as the run progresses — convention over configuration.

| Phase | Fields present |
|-------|---------------|
| **Init** | `config`, `created`, `updated`, `status` (`"running"`), `pipeline`, `chunks` (each with `state` and `items`), `metadata` (pipeline name, overrides, mode) |
| **Execution** | `batch_id`, `submitted_at`, `provider_status`, `valid`, `failed`, `retries`, `input_tokens`, `output_tokens` — added to chunk objects as batches are submitted and polled |
| **Completion** | `completed_run_steps` — populated as run-scoped steps finish; `status` transitions to `"complete"` or `"failed"` |
| **Pause/Resume** | `paused_at` — set on graceful shutdown, cleared by `mark_run_running()` on resume |

Fields that have not yet been populated simply do not exist in the JSON. Consumers should treat missing fields as null/default rather than raising errors.

---

## State Machine

### Chunk States

Each chunk progresses through the pipeline steps:

```
{step}_PENDING → {step}_SUBMITTED → (next step)_PENDING → ... → VALIDATED
                                                                    or
                                                                  FAILED
```

For a 6-step pipeline (generate → inversion_analysis → merge_stories → score_coherence → score_wounds → generate_fragments):

```
generate_PENDING
generate_SUBMITTED        (batch mode only)
inversion_analysis_PENDING
inversion_analysis_SUBMITTED
merge_stories_PENDING     (expression step — processed locally, no SUBMITTED state)
score_coherence_PENDING
score_coherence_SUBMITTED
score_wounds_PENDING
score_wounds_SUBMITTED
generate_fragments_PENDING
generate_fragments_SUBMITTED
VALIDATED                 (terminal — all steps complete)
FAILED                    (terminal — unrecoverable error)
```

### State Transitions

**Batch mode (`--watch`):**
1. `{step}_PENDING` → `{step}_SUBMITTED`: Batch file uploaded to provider, batch ID stored
2. `{step}_SUBMITTED` → `(next_step)_PENDING`: Batch completed, results downloaded, validated, chunk advances
3. `{step}_SUBMITTED` → `{step}_PENDING`: Batch failed, chunk reset for retry

**Realtime mode (`--realtime`):**
1. `{step}_PENDING` → `(next_step)_PENDING`: All units processed one-by-one, validated, chunk advances (no SUBMITTED state)

**Expression steps:**
1. `{step}_PENDING` → `(next_step)_PENDING`: Evaluated locally, instant, zero API cost

### Terminal States

- `VALIDATED`: All pipeline steps complete, all units valid or failed
- `FAILED`: Unrecoverable error (e.g., previous step output missing, auth failure)
- `killed`: The TUI's kill flow writes "killed" to the manifest status when a user terminates a zombie process

---

## Batch Mode (`--watch`)

### Execution Loop

The `watch_run()` function runs a poll loop:

0. **Retry validation failures (once, before loop starts)** — Scan for validation failures that can be retried. Archive failures to `.bak`, reset chunk state to `{step}_PENDING`
1. **Poll submitted batches** — For chunks in `{step}_SUBMITTED`, check provider status. If complete, download results, validate, and advance. If failed, reset to PENDING for retry.
2. **Submit pending batches and handle expression steps** — For chunks in `{step}_PENDING` (LLM steps), prepare the batch file and submit to the provider. Respect `max_inflight_batches` limit (default from config, typically 5-10). Chunks exceeding the limit are skipped (throttled). Expression steps in PENDING state are evaluated locally and advanced immediately.
3. **Check terminal state** — If all chunks are VALIDATED or FAILED, mark run complete and exit. A run with status 'complete' means all chunks have reached a terminal state (VALIDATED or FAILED). It does not imply all units passed — check the valid/failed counts for the actual outcome. In realtime mode, this applies regardless of whether `realtime_run()` returns exit code 0 (all passed) or 1 (remaining validation failures) — either way, if `is_run_terminal()` is true, `mark_run_complete()` is called.
4. **Sleep** — Wait `poll_interval_seconds` (default 5) before next tick
5. **Repeat**

### Batch Submission

For each pending chunk:
1. Read `units.jsonl` and previous step's `_validated.jsonl` to build prompts
2. Render Jinja2 templates with unit data as context
3. Write batch request file (provider-specific format)
4. Upload to provider's batch API
5. Store batch_id in manifest
6. Transition to `{step}_SUBMITTED`

### Batch Polling

For each submitted chunk:
1. Query provider for batch status using stored batch_id
2. If completed: download results, run validation pipeline, advance state
3. If failed: log error, reset to PENDING (up to retry limit)
4. If still processing: no action, continue polling

### Throttling

`max_inflight_batches` (from `api.max_inflight_batches` in config) limits concurrent submitted batches. When the limit is reached, additional pending chunks are logged as `[THROTTLE]` and skipped until a slot opens.

Throttling is logged as a single summary line per tick showing the count of waiting chunks and the inflight limit. Repeated identical counts are suppressed via the poll status cache.

### Quiet Tick Logging

The poll loop uses deduplication to avoid logging identical state on every tick. A single `.poll_status_cache.json` per run (stored at the run directory level) tracks the last logged state for all chunks. An approximately 60-second heartbeat is logged when idle (aligned to the next poll tick after 60 seconds of inactivity).

---

## Realtime Mode (`--realtime`)

### Execution Loop

The `realtime_run()` function processes all steps sequentially:

1. **Retry validation failures** — Same as batch mode: archive to `.bak`, reset chunks
2. **For each pipeline step:**
   a. Skip expression steps (handled separately)
   b. For each chunk in `{step}_PENDING`:
      - Process each unit individually via direct API call
      - Validate each response
      - Write valid results to `{step}_validated.jsonl`
      - Write failures to `{step}_failures.jsonl` with `raw_response` preserved
   c. After all units: run validation, advance chunk state
3. **Run expression steps** inline between LLM steps
4. **Run run-scoped steps** (e.g., extract_units) after all chunks complete

### Idempotency Check (90% Fallback)

Before processing a step, `run_step_realtime()` checks if the step is already substantially complete: if `valid_count >= 90% * expected` and `failures == 0`, it SKIPs the step. This prevents re-processing on resume.

**The `.bak` bypass:** When validation failures have been archived to `.bak` for retry, the idempotency check is overridden. The presence of a `.bak` file signals that the step was intentionally reset and should NOT use the 90% fallback. `.bak` files are cleaned up after the step completes.

### Cost Cap

In realtime mode, `api.realtime.cost_cap_usd` (from config) stops processing if cumulative cost exceeds the cap. Cost is calculated from token counts using registry pricing.

### Auto-Retry

`api.realtime.auto_retry` (default true) enables automatic retry of failed API calls within a step. Failed units are retried up to `api.retry.max_attempts` with exponential backoff (`initial_delay_seconds` × `backoff_multiplier`).

---

## Validation Pipeline

### Two-Phase Validation

After receiving LLM output (batch download or realtime response):

1. **Schema validation** — JSON Schema validation against `schemas/{step}.json`. Catches structural errors (wrong types, missing fields, malformed JSON).
2. **Business logic validation** — Expression-based rules in the pipeline config. Catches semantic errors (net_sum doesn't match path direction, wound_count doesn't match actual non-zero scores).

### Subprocess Pipeline

Validators are chained via stdin/stdout: `schema_validator.py | validator.py`. This allows independent testing and flexible composition.

### Output Files

Per chunk, per step:
- `{step}_results.jsonl` — Raw LLM responses
- `{step}_validated.jsonl` — Units that passed both validation phases
- `{step}_failures.jsonl` — Units that failed, with:
  - `unit_id`
  - `failure_stage`: `schema_validation`, `validation`, or `pipeline_internal`
  - `input`: The step's input context
  - `raw_response`: The original LLM output (preserved for debugging)
  - `errors`: Array of error objects with path and message
  - `retry_count`

### Failure Categories

- `schema_validation` and `validation` — Retryable. The LLM returned bad output; a different response may pass.
- `pipeline_internal` — Not retryable. Records were lost in the pipeline (e.g., missing input file). Indicates a system error.

### Validation Timeout

`api.subprocess_timeout_seconds` (default 600) limits how long the validation subprocess can run. This prevents hangs during validation of large batches.

### Re-Validation (`--revalidate`)

Re-runs the validation pipeline on existing failure records without making API calls. Used when schemas or business rules have been updated and existing raw responses may now pass.

Requires `--step` to specify which step to re-validate (schemas and rules are per-step).

The re-validation pipeline for each failure record:
1. Parse `raw_response` (apply markdown block extraction)
2. Run Phase 1: Schema validation against current schema
3. Merge parsed result with original input: `{**failure_record['input'], **parsed_result}`
4. Run Phase 2: Business logic validation against current rules
5. Promote passing units to `{step}_validated.jsonl`; write still-failing records to a temp file and atomically rename over the original `{step}_failures.jsonl`

By default, uses the run's config snapshot. With `--use-source-config`, uses the source pipeline's schemas and rules (convenient for iterating on validation rules without manually copying files).

Cannot be run on an active run (orchestrator must not be running).

---

## Retry Logic

### Batch Mode Retries

When a batch fails (provider returns error):
- Chunk is reset to `{step}_PENDING`
- On next tick, it will be resubmitted
- After `api.retry.max_attempts`, chunk is marked FAILED

### Realtime Mode Retries

Within a step, failed units are collected and retried:
- Up to `api.retry.max_attempts` (default 3) retry passes
- Each pass resubmits only the failed units
- Exponential backoff between retries
- After all retries exhausted, remaining failures stay in `{step}_failures.jsonl`

### Cross-Step Retry (Validation Failure Recovery)

At the start of both `watch_run()` and `realtime_run()`:
1. Scan all chunks for validation failures (`schema_validation` or `validation` stage)
2. Archive failures to `{step}_failures.jsonl.bak`
3. Preserve hard failures (`pipeline_internal`) in the original file
4. Reset affected chunks to `{step}_PENDING`
5. The main loop then reprocesses those chunks

**Known issue:** This resets the entire chunk, not just the failed units. All units in the chunk are reprocessed through the failed step and all subsequent steps. This cascading behavior caused 4,300 units to be reprocessed for 308 actual failures.

---

## Signal Handling

### SIGINT (Ctrl+C)

The orchestrator should catch SIGINT and gracefully shut down:
- Save manifest state
- Mark run as "paused"
- Terminate any active child processes
- Exit cleanly

**Known issue:** SIGINT is not responsive when the process is blocked inside `time.sleep()` in the poll loop or inside synchronous HTTP requests in provider classes. The user may need to press Ctrl+C multiple times or wait for the current operation to complete.

**watch_run() SIGINT behavior:** When `watch_run()` is active, it installs a local SIGINT handler that sets an interrupt flag rather than immediately saving state. If the process is in `time.sleep()` during the poll interval, there may be a delay of up to N seconds (where N is the poll interval, default 5) before the manifest is saved and the run is marked paused. This is a known limitation; the global SIGINT handler is restored when `watch_run()` exits.

### SIGTERM

Should behave identically to SIGINT — graceful shutdown.

### SIGPIPE

`signal.signal(SIGPIPE, SIG_DFL)` is set at the top of `main()` to prevent `BrokenPipeError` from marking healthy runs as failed when output is piped through commands like `head`.

### SIGUSR1

Dumps the current Python stack trace to RUN_LOG.txt with `[DEBUG]` prefix. Used for diagnosing hung processes without interrupting execution. Send via `kill -USR1 <pid>`.

Unix-only (macOS/Linux). Not available on Windows.

---

## PID Management

### PID File

The orchestrator writes its PID to `orchestrator.pid` in the run directory on startup. The PID file is the single source of truth for process identity. On resume, the PID file is overwritten with the new process's PID. The current PID is also written to `manifest["metadata"]["pid"]` via `mark_run_running()`. The PID file remains the primary source of truth; the manifest PID is a backup for diagnostic tools. The TUI checks the PID file (not a cached value) on every refresh tick.

---

## Crash Recovery

### Detection

If the orchestrator crashes or is killed:
- The manifest's `status` remains "running" but the PID is dead
- The TUI detects this as "detached" or "process lost"
- Chunks in `{step}_SUBMITTED` have batches that may still be processing server-side

### Recovery Process

1. User starts the orchestrator again with `--watch` or `--realtime` on the same `--run-dir`
2. Orchestrator reads existing manifest
3. `mark_run_running()` updates manifest `status` to `"running"` and clears `paused_at`
4. PID file is overwritten with the new process's PID
5. For submitted batches: polls for completion (they may have finished while we were down)
6. For pending chunks: processes normally
7. For validated chunks: skips (already done)

### Pre-Flight Failure Recovery

If a run fails during pre-flight checks (e.g., missing API key) before any units are submitted, all chunks remain in their initial `{first_step}_PENDING` state. These runs can be resumed normally — the TUI detects this condition (failed status + all chunks at initial PENDING) and routes to the resume path instead of the retry path. The orchestrator's `mark_run_running()` resets the status from "failed" to "running" and processing begins from the start.

### Guard: Previous Step Output

Before processing a step, the orchestrator verifies that the previous step's `_validated.jsonl` exists. If missing, the chunk is marked FAILED to prevent `FileNotFoundError` crashes. This guard exists in both batch and realtime mode.

### Verify and Repair Workflow

After a run completes (or is resumed after a crash), use `--verify` to check that all expected units are accounted for:

```bash
python scripts/orchestrate.py --verify --run-dir runs/my_run
```

If missing units are found, use `--repair` to create retry chunks:

```bash
python scripts/orchestrate.py --repair --run-dir runs/my_run --yes
python scripts/orchestrate.py --realtime --run-dir runs/my_run
```

This workflow directly addresses the Silent Attrition incident (QUALITY.md Scenario 1) where 1,693 units were silently lost after a crash.

---

## Expression Steps

Expression steps run locally without API calls:

1. Detect `scope: expression` in step config
2. Evaluate expressions using `asteval` for each unit
3. Write results directly to `{step}_validated.jsonl`
4. Advance chunk state to next step
5. Zero cost, instant execution

Expression steps have no `SUBMITTED` state — they go directly from `PENDING` to the next step.

### Seeded Randomness

Expression steps use `random.seed(hash(unit_id + step_name + str(repetition_seed)))` for reproducible results. The `_repetition_seed` ensures different results across Monte Carlo repeats of the same unit.

### Loop Until

Expression steps can loop with `loop_until` condition:
- `init` block evaluated once before loop
- `expressions` evaluated each iteration
- Loop exits when `loop_until` condition is true
- `max_iterations` safety limit (default 1000)
- Output includes `iterations` count and `timeout` flag

---

## Run-Scoped Steps

Some steps operate on the entire run, not per-chunk. These are configured as pipeline steps with `scope: run` and execute after all chunks reach a terminal state.

### Configured Run-Scoped Steps

- `extract_units`: An external script (`scripts/extract_units.py`) that must be configured as a run-scoped step in the pipeline config. It reads all `generate_fragments_validated.jsonl` files across chunks, extracts individual units as gzipped JSON files to `outputs/units/`
- Uses `filename_expression` from config to name output files
- Supports `compression: gzip` for compressed output

`extract_units` is idempotent — it clears the output directory before writing. Running it twice produces the same result.

### Run-Scope Script Execution Framework

Run-scoped steps can also execute arbitrary configured scripts. Each script is invoked with:

```
script_path --run-dir <run_dir> --step-name <step_name> --config <config_path>
```

Completed run-scoped steps are tracked in the manifest's `completed_run_steps` list. On resume, steps already in `completed_run_steps` are skipped to ensure idempotency.

---

## Post-Processing

After a run completes (all chunks terminal, run-scoped steps finished), the pipeline can execute post-processing operations configured in the pipeline config.

### Framework

Post-processing is configured under the `post_process` key in the pipeline config. It supports:

- **Arbitrary scripts** — External scripts executed after run completion with the run directory as context
- **Built-in gzip** — Compress output files (e.g., validated JSONL files) to reduce storage

### Execution

Post-processing steps run sequentially after all pipeline steps and run-scoped steps have completed. If the run is resumed and post-processing has already been performed, it is skipped based on completion tracking.

---

## Cost Tracking

- Token counts recorded in manifest per chunk and aggregated in metadata
- Separated into initial vs retry tokens
- Cost calculated using pricing from `scripts/providers/models.yaml`
- Realtime mode applies `realtime_multiplier` (typically 2.0)
- TUI displays per-run and aggregate costs
- Cost cap in realtime mode stops processing when exceeded

---

## Logging

### RUN_LOG.txt

Every run has a log file at `runs/{run_dir}/RUN_LOG.txt` with timestamped entries for:
- Step starts and completions
- Batch submissions and completions
- Validation results
- Errors and retries
- Token counts and cost

### Console Output

The orchestrator logs to both the console and RUN_LOG.txt:
- `[BATCH]` / `[REALTIME]` — Mode indicator
- `[SUBMIT]` — Batch submission with chunk and batch_id
- `[POLL]` — Batch polling results
- `[COLLECT]` — Batch result download
- `[VALIDATE]` — Validation start, progress, and results
- `[EXPRESSION]` — Expression step evaluation
- `[SKIP]` — Step skipped (idempotency or already complete)
- `[RETRY]` — Retry initiation
- `[ERROR]` — Errors with details
- `[THROTTLE]` — Max inflight reached
- `[INFO]` — General information
- `[TOKENS]` — Token count updates
- `[STEP]` — Pipeline advancing to next step
- `[TICK]` — 60-second heartbeat with status snapshot: idle duration, active step, chunk state breakdown, cumulative cost. When state changes occur during a tick, a delta summary is logged instead: units gained/lost, active step, chunks completed, cost.
- `[API]` — Logged to TRACE_LOG.txt only (not RUN_LOG.txt or console)

### TRACE_LOG.txt

Every run has a trace file at `runs/{run_dir}/TRACE_LOG.txt` with per-request telemetry:
- Every outgoing API call logged as a single line on completion with provider, chunk, unit, duration, and status code
- Batch submissions, polls, and result collections with batch IDs
- This file is always written and is never gated behind flags
- The TUI and diagnostic tools can read this file for real-time request monitoring

TRACE_LOG.txt is separate from RUN_LOG.txt to keep operational logs readable while preserving full request-level detail for debugging.

### Known Logging Issues

- No ETA in `--watch` output (cost is now shown in heartbeat and delta summaries)
- Blind spot between "Starting real-time execution" and first unit result

---

## Configuration

### Key Config Fields

```yaml
pipeline:
  name: "Pipeline Name"
  steps:
    - name: step_name
      prompt_template: template.jinja2
      provider: gemini          # Optional per-step override
      model: gemini-2.0-flash   # Optional per-step override
      scope: expression         # For expression steps
      expressions: {}           # For expression steps
      loop_until: "condition"   # For looping expressions
      max_iterations: 1000      # Safety limit

api:
  provider: gemini
  model: gemini-2.0-flash-001
  max_inflight_batches: 10    # Recommended: 10-20 for Gemini. Start at 10, increase if no rate limit errors.
  poll_interval_seconds: 5
  retry:
    max_attempts: 5
    initial_delay_seconds: 30
    backoff_multiplier: 2
  realtime:
    cost_cap_usd: 50.0
    auto_retry: true
  subprocess_timeout_seconds: 600

processing:
  strategy: permutation      # permutation | cross_product | direct
  chunk_size: 100
  validation_retry:
    max_attempts: 3
```

### Config Snapshot

On `--init`, the entire config directory (config.yaml, templates/, schemas/, items files) is copied into the run directory. This means:
- Runs are self-contained and reproducible
- Changing the source pipeline doesn't affect existing runs
- Each run can be re-run independently

---

## Environment

### API Keys

| Provider | Environment Variable |
|----------|---------------------|
| Gemini | `GOOGLE_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` |

### .env Support

The orchestrator loads `.env` files automatically via `python-dotenv`. Place a `.env` file in the project root with `KEY=value` pairs. Environment variables set explicitly take precedence over `.env` values.

---

## Known Issues and Technical Debt (From Design Discussions)

1. **Retry cascading** — Retry resets entire chunks, not individual failed units. 308 failures caused 4,300 units to be reprocessed.
2. **SIGINT unresponsiveness** — Blocked during API calls and sleeps.
3. **No watchdog** — Deferred. Provider-level timeouts (120s for Gemini) handle the most common case. A process-level watchdog for detecting hung API calls beyond provider timeouts is planned for a future release.
4. ~~**No --verify/--repair**~~ — Implemented. Use `--verify` to check run integrity and `--repair` to create retry chunks for missing units. See §Verify and Repair Workflow.
5. **No --units-file** — Planned flag for targeted runs from a unit list.
6. **No --merge** — Planned flag for combining runs.
