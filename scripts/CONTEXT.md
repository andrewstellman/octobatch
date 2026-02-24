# Scripts Directory Context
> **File:** `scripts/CONTEXT.md`

## 1. Purpose

This directory contains the core orchestration logic for the Octobatch batch processing system. It manages the complete lifecycle of batch processing runs including initialization, API submission, polling, validation, retries, and real-time execution modes.

## 2. Key Components

### orchestrate.py (Main Orchestrator - ~5,900 lines)
The central CLI tool managing all batch processing operations.

**CLI Modes (mutually exclusive except `--init` + `--realtime`):**
- `--init`: Initialize a new run (generate units, create chunks, snapshot config)
- `--tick`: Advance run by one step (poll, collect, validate, submit)
- `--status`: Show current run status as JSON
- `--watch`: Automatically poll until completion (with cost/timeout limits)
- `--retry-failures`: Create retry chunks from failed units
- `--validate-config`: Validate config structure and expressions
- `--revalidate`: Re-run validation on existing failures without API calls
- `--realtime`: Synchronous API execution (2x cost, immediate results)
- `--ps`: List all runs with status, progress, cost (no `--run-dir` needed)
- `--info`: Print detailed run information
- `--verify`: Check run integrity — find missing, duplicated, or orphaned units
- `--repair`: Create retry chunks for missing units found by `--verify`
- `--quiet` / `-q`: Suppress console output (log files still written)
- `--json`: Output in JSON format (used with `--ps`, `--info`, `--verify`)
- `--version`: Print version and exit

**Key Functions:**
- `init_run()`: Creates run directory structure, generates units, partitions into chunks
- `tick_run()`: Two-phase loop (poll in-flight batches, submit pending batches); handles expression steps locally before API submission; logs delta summaries on activity ticks, enriched heartbeat on idle ticks, step transitions via `[STEP]` prefix; throttle messages collapsed to single summary per tick
- `watch_run()`: Automatic polling with cost/timeout guards; writes PID file at start
- `realtime_run()`: Synchronous API calls for immediate processing; checks `is_expression_step()` before API calls; writes PID file at start
- `check_prerequisites()`: Validates API key before starting run
- `mark_run_failed/paused/complete/running()`: Status management helpers; `mark_run_running()` also writes `os.getpid()` to `manifest["metadata"]["pid"]`
- `cleanup_pid_file()`: Intentionally a no-op — PID file persists after exit so TUI can detect dead processes via `os.kill(pid, 0)`
- `_log_api_key_status()`: Logs AVAILABLE/MISSING status for all provider API keys at run start
- `run_step_realtime()`: Includes idempotency check — skips steps where validated output already has all expected items; checks `.bak` files to bypass 90% fallback during retry
- `retry_validation_failures(run_dir, manifest, log_file)`: Resets chunks with validation failures for retry — rotates `{step}_failures.jsonl` to `.bak`, preserves hard failures, resets chunk state to `{step}_PENDING`, returns count of archived failures; `.bak` file also signals idempotency bypass

**CLI Tool Handlers:**
- `_handle_ps(args)`: Lists all runs using `scan_runs()` + `get_enhanced_run_status()`. Formats as text table or JSON (`--json`).
- `_handle_info(args)`: Detailed run info — loads manifest, shows pipeline steps with per-step valid counts, chunk states. JSON mode outputs full manifest.
- `_handle_verify(args)`: Calls `verify_run()` from `run_tools.py`. Reports per-step missing/duplicated units.
- `_handle_repair(args)`: Calls `repair_run()` from `run_tools.py`. Creates retry chunks for missing units. Requires `--yes` or interactive confirmation.

**Signal Handling:**
- `SIGINT` / `SIGTERM`: Graceful shutdown — save manifest, mark run paused, terminate children
- `SIGPIPE`: `SIG_DFL` to prevent `BrokenPipeError` marking healthy runs as failed
- `SIGUSR1`: Dumps current Python stack trace to RUN_LOG.txt with `[DEBUG]` prefix for diagnosing hung processes; falls back to stderr if log unavailable; Unix-only (guarded by `hasattr(signal, 'SIGUSR1')`)

**Expression Step Functions:**
- `run_expression_step()`: Executes expression-only steps (no LLM call); supports `init`, `loop_until`, and `max_iterations` for iterative simulations
- `is_expression_step(config, step_name)`: Check if step has `scope: expression`
- `get_expression_step_config(config, step_name)`: Get full config for expression step

**Loop Support in Expression Steps:**
- `init` expressions are evaluated once before the loop starts
- `expressions` are evaluated on each iteration with iteration-specific seeds
- `loop_until` condition is checked after each iteration; loop exits when True
- `max_iterations` (default 1000) prevents infinite loops
- Each iteration uses seed: `(base_seed + iteration) & 0x7FFFFFFF` for deterministic randomness

### octobatch_utils.py (~480 lines)
Shared utilities for file I/O, logging, telemetry, and manifest summary generation.

**Key Functions:**
- `__version__`: Version string (`"1.0.0"`)
- `load_manifest()` / `save_manifest()`: Atomic manifest operations; `save_manifest()` also writes `.manifest_summary.json` (~300 bytes) for fast TUI startup
- `_build_summary()`: Extracts lightweight summary from manifest (status, progress, units, cost, tokens, mode, pipeline, timestamps)
- `_compute_summary_cost()`: Best-effort cost calculation from model registry pricing
- `load_config()`: YAML config loading
- `load_jsonl()` / `load_jsonl_by_id()` / `write_jsonl()` / `append_jsonl()`: JSONL file handling (supports gzipped files)
- `log_message()`: Timestamped logging to RUN_LOG.txt with optional stderr echo
- `trace_log()`: Timestamped trace lines to TRACE_LOG.txt for request-level telemetry; always written (not gated behind flags); best-effort (never fails the caller)
- `compute_cost()`: Cost calculation from token counts

### run_tools.py (~250 lines)
CLI tools for run verification and repair. Addresses QUALITY.md Scenario 1 (Silent Attrition).

**Key Functions:**
- `verify_run(run_dir)`: Check run integrity by comparing expected units against actual outputs per pipeline step. Returns structured result with per-step counts of missing, duplicated, and orphaned units.
- `repair_run(run_dir)`: Create retry chunks for missing units. Calls `verify_run()` internally, creates new chunks at the step where units went missing, copies unit data from previous step's validated output.
- `parse_json_response()`: JSON parsing with markdown code block extraction and LLM quirk handling (trailing commas, `+N` numbers)
- `create_interpreter()`: Safe asteval interpreter for validation expressions

### config_validator.py (~746 lines)
Comprehensive config validation with expression testing.

**Key Functions:**
- `validate_config()`: Validates required sections, pipeline steps, file references
- `validate_expression()`: Tests asteval expressions with mock context
- `validate_config_run()`: Full validation with summary output

### octobatch_step.py (~258 lines)
Jinja2 template rendering for prompt preparation.

**Key Functions:**
- Reads units from stdin, renders templates, outputs prompts to stdout
- Domain-agnostic (no knowledge of specific pipeline content)
- Used by `prepare_prompts()` in orchestrate.py

### realtime_provider.py (~207 lines)
Synchronous API calls using the provider abstraction layer.

**Key Functions:**
- `run_realtime()`: Processes prompts with rate limiting and retry logic; accepts optional `trace_callback(unit_id, duration_secs, status_str)` for request-level telemetry
- `_make_provider_call()`: Single API call via `provider.generate_realtime()` with JSON parsing
- `FatalProviderError`: Raised for auth/billing errors (400/401/403) that abort the entire run
- Exponential backoff on rate limits (429, 503, timeouts)

### schema_validator.py (~561 lines)
JSON Schema validation (Draft 2020-12) with LLM-aware type coercion.

**Key Functions:**
- Reads from stdin, validates against schema, writes valid to stdout
- Failure records with detailed error paths written to stderr
- `coerce_data()`: Recursive type coercion before validation — str→int, str→float, str→bool, float→int, string→array, enum normalization
- `_unwrap_response()`: Detects and unwraps double-wrapped LLM responses (JSON string with markdown fences inside a `response` key)
- `[COERCE]` telemetry on stderr for each type conversion

### validator.py (~648 lines)
Expression-based business logic validation.

**Key Functions:**
- Required fields, type checking, range/enum validation
- Custom rules using asteval expressions
- Warning vs error levels

### generate_units.py (~663 lines)
Unit generation with multiple combination strategies.

**Key Functions:**
- `generate_units()`: Main dispatcher that routes to strategy-specific functions
- `generate_permutation_units()`: All permutations without replacement (default)
- `generate_cross_product_units()`: Cartesian product of items from different groups
- `generate_direct_units()`: Each item becomes one unit directly

**Unit Generation Strategies:**
- **permutation** (default): Items are drawn from a single pool and arranged in all permutations without replacement. Example: 22 cards × 3 positions = 22×21×20 = 9,240 units.
- **cross_product**: Items are organized into groups (one per position). Result is Cartesian product of all groups. Example: 3 characters × 3 situations = 9 units.
- **direct**: Each item in the source becomes one unit directly (no combination). Example: 5 hands = 5 units.

Supports chunking with `--chunk-size` flag and item limiting with `--max-units`.

## 3. Data Flow

```
Config (YAML) + Items (YAML/JSONL)
    │
    ▼
generate_units.py → units.jsonl (all permutations)
    │
    ▼
orchestrate.py --init → chunks/chunk_NNN/units.jsonl
    │
    ▼
orchestrate.py --watch (or --realtime)
    │
    ├── [Per Chunk, Per Step]
    │   │
    │   ▼
    │   octobatch_step.py → {step}_prompts.jsonl
    │   │
    │   ▼
    │   API (Batch or Realtime) → {step}_results.jsonl
    │   │
    │   ▼
    │   schema_validator.py | validator.py
    │   │
    │   ├── {step}_validated.jsonl (success)
    │   └── {step}_failures.jsonl (failures)
    │
    ▼
MANIFEST.json (state updates after each operation)
    │
    ▼
RUN_LOG.txt (operational log) + TRACE_LOG.txt (per-request telemetry)
```

## 4. Architectural Decisions

### Two-Step Launch for Batch Mode
`--init` and `--watch` are mutually exclusive flags. The TUI uses a two-step process:
1. `orchestrate.py --init --pipeline X --run-dir Y` (blocking, creates manifest)
2. `orchestrate.py --watch --run-dir Y` (background, monitors progress)

This separation allows the TUI to verify initialization succeeded before starting the watcher.

### Realtime Mode Combined Flags
`--init --realtime` CAN be combined because `--realtime` is outside the mutually exclusive group. This enables single-command realtime execution for the TUI.

### Atomic Manifest Updates
Manifest writes use `tempfile + rename` pattern for crash safety. The manifest is updated after every state change, enabling run resumption from any crash point.

### Subprocess Pipeline for Validation
Validators are chained via stdin/stdout: `schema_validator.py | validator.py`. This allows independent testing and flexible composition. Failures appear on stderr.

### Idempotent Tick Operations
`tick_run()` can be called repeatedly without side effects. It checks current state before acting and only processes chunks in appropriate states.

### Token Accounting Separation
Initial vs retry tokens are tracked separately in metadata, enabling accurate cost attribution and supporting cost caps with correct accounting.

## 5. Key Patterns & Conventions

### State Machine Pattern
Chunk states follow: `{step}_PENDING` → `{step}_SUBMITTED` → next step or `VALIDATED`/`FAILED`

### Error Handling
- API errors: Caught in tick_run(), chunk marked for retry, warning logged
- Validation errors: Written to failures.jsonl with unit_id preserved
- Fatal errors: `mark_run_failed()` called, traceback logged to RUN_LOG.txt

### Configuration Snapshot
Config + templates + schemas are copied to run directory on init, enabling independent re-runs even if source config changes.

### Subprocess Management
Active subprocesses tracked in `_active_subprocesses` list. Signal handlers (SIGINT/SIGTERM) terminate children gracefully.

## 6. Recent Changes

### CLI Tools & TUI Startup Perf (Feb 21, 2026) — Latest
- **CLI tools**: Added `--version`, `--ps`, `--info`, `--verify`, `--repair`, `--json` flags to orchestrate.py. `--ps` lists all runs as a formatted table. `--info` prints detailed run info with pipeline step funnel. `--verify` checks run integrity (missing/duplicated units per step). `--repair` creates retry chunks for missing units. All reuse TUI utility functions (pure, no Textual dependency).
- **`run_tools.py`** (new): `verify_run()` and `repair_run()` functions for run integrity checking and repair. Addresses QUALITY.md Scenario 1 (Silent Attrition).
- **`__version__`**: Added `__version__ = "1.0.0"` to `octobatch_utils.py`.
- **Launcher scripts**: `octobatch` and `octobatch-tui` shell wrappers in project root for convenience.
- **Manifest summary cache**: `save_manifest()` now writes `.manifest_summary.json` (~300 bytes) alongside `MANIFEST.json`. The TUI reads this for the home screen DataTable instead of parsing multi-MB manifests.
- **Async TUI startup**: HomeScreen renders immediately and loads data in a background thread via `@work(thread=True)`. Auto-poll refresh also runs in background thread.

### Orchestrator Enhancements (Feb 21, 2026)
- **Enhanced `--watch` output**: `tick_run()` now logs delta summaries when batches complete (e.g., `[TICK] +95 valid, +3 failed | step: score_coherence | 74/93 chunks complete | $1.24 spent`), enriched heartbeat during idle periods (idle duration, active step, chunk state breakdown, cost), and `[STEP]` prefix for step transitions
- **Collapsed throttle logging**: Single `[THROTTLE]` summary per tick instead of one line per pending chunk; deduplication via poll_status_cache suppresses repeated identical counts
- **Manifest PID on resume**: `mark_run_running()` writes `os.getpid()` to `manifest["metadata"]["pid"]` alongside the PID file, ensuring both agree on current process identity
- **PID file alignment**: `cleanup_pid_file()` is now a no-op — PID file persists after exit for TUI dead-process detection via `os.kill(pid, 0)`. Removed PID file deletion from 4 locations in `runs.py`
- **SIGUSR1 signal handler**: Dumps Python stack trace to RUN_LOG.txt with `[DEBUG]` prefix; falls back to stderr if log unavailable; Unix-only (`hasattr(signal, 'SIGUSR1')`)
- **Request-level telemetry**: New `trace_log()` function in `octobatch_utils.py` writes to `TRACE_LOG.txt` per run. Batch operations logged at SUBMIT/POLL/COLLECT points in `orchestrate.py`. Realtime API calls logged via `trace_callback` parameter in `run_realtime()`
- **`--quiet` flag**: Suppresses console output by redirecting stdout to devnull; log files (RUN_LOG.txt, TRACE_LOG.txt) still written
- **Generated CONTEXT.md files**: Created `scripts/providers/CONTEXT.md` and `scripts/tui/widgets/CONTEXT.md`; updated `ai_context/BOOTSTRAP_CODE.md` Required Reading list

### Retry Validation Failures & Funnel Display
- **`retry_validation_failures(run_dir, manifest, log_file)`**: Archives validation failures to `.bak`, preserves hard failures in original file, resets chunk state to `{step}_PENDING`. Called from `realtime_run()` and `watch_run()` before the "all terminal" early exit — if validation failures are found, they are archived and the run continues instead of exiting.
- **`.bak` idempotency bypass**: `run_step_realtime()`'s 90% fallback (treat step as complete if `valid >= 90% * expected` and failures == 0) now checks for `.bak` file — if present, the fallback is skipped so the step will be re-processed. `.bak` files are cleaned up after step completes or on SKIP.
- **TUI retry path**: `reset_unit_retries()` in `runs.py` creates `.bak` signal before modifying failures, ensuring the orchestrator won't skip the retried step.

### Failure Categorization & Per-Step Provider Overrides
- **`categorize_step_failures(run_dir, step_name)`**: Scans `_failures.jsonl` files, returns `{"validation": N, "hard": N, "total": N}` based on `failure_stage` field (`schema_validation`/`validation` → retryable, `pipeline_internal` → hard)
- **Per-step provider/model**: Steps can specify `provider` and/or `model` in config.yaml; `get_step_provider()` resolves with CLI override tracking via manifest metadata (`cli_provider_override`, `cli_model_override`)
- **Provider resolution**: CLI flags > Step config > Global config > registry `default_model`
- **Standardized on GOOGLE_API_KEY**: Removed all GEMINI_API_KEY references across codebase
- **Quiet tick logging**: Poll deduplication via `.poll_status_cache.json` per chunk; TICK summary suppressed on no-op ticks; 60-second heartbeat when idle; removed "Starting tick" noise
- **mark_run_complete fix**: Removed `if current_status != "failed"` guard so failed→complete transition works when all chunks are terminal
- **SIGPIPE/BrokenPipeError handling**: `signal.signal(SIGPIPE, SIG_DFL)` at top of `main()` + `BrokenPipeError` catch prevents healthy runs from being marked failed when piped through `| head`
- **Manifest consistency auto-fix**: `check_manifest_consistency()` detects and corrects stale manifest status

### v1.0 Multi-Provider Support
- **Supported providers**: Gemini, OpenAI, Anthropic (all with batch API support)
- **Registry-driven pricing**: All pricing from `scripts/providers/models.yaml`, not pipeline configs
- **Streaming I/O for validation**: Fixes memory issues with large LLM outputs
- **Early abort improvement**: Now skips retry loops on auth/billing errors (400/401/403)
- **Built-in gzip post-processing**: `type: gzip` post-process steps for compressing output files

### Failure Inspection Tooling
- **raw_response capture**: Before overwriting failure record's `input` field with step input, the original validator output (raw LLM response) is saved as `raw_response`
- **Max Units observability**: Command line logged to RUN_LOG.txt, max_units stored in manifest metadata
- **--max-units fix**: `args.max_units` is now correctly applied in `init_run()` after unit generation, before chunking

### Pipeline Name in Manifest
During `--init`, the manifest now stores:
- `metadata.pipeline_name`: Derived from `config["pipeline"]["name"]` or config directory name
- `metadata.start_time`: ISO timestamp when run was initialized

This allows the TUI to display the pipeline name for runs.

### Prerequisite Checking
Added `check_prerequisites()` function:
- Validates GOOGLE_API_KEY is set before starting
- Called by `watch_run()` and `realtime_run()` before processing
- Returns error message if missing, or None if OK
- Run is marked as failed if prerequisites not met

### Crash Handling with Status Fields
Added status management functions:
- `mark_run_failed(run_dir, error_message)`: Sets `status: "failed"`, logs traceback
- `mark_run_paused(run_dir)`: Sets `status: "paused"` on Ctrl+C
- `mark_run_complete(run_dir)`: Sets `status: "complete"` on success
- `mark_run_running(run_dir)`: Clears paused state on resume

Both `--watch` and `--realtime` execution wrapped in try/except:
- `KeyboardInterrupt` → marks as paused, exits 130
- Other exceptions → marks as failed, re-raises

### Two-Step TUI Launch
TUI now uses separate init and watch commands for batch mode to avoid flag conflicts.

## 7. Current State & Known Issues

### Working Features
- Full batch and realtime execution modes
- Retry logic with configurable max attempts
- Cost tracking and cost caps
- Status management for crash recovery
- Resume capability from any state
- Terminal state verification before marking runs complete

### Recent Bug Fixes (Feb 7, 2026)

**Realtime resume chunk state bug**: `realtime_run()` now checks `is_run_terminal(manifest, max_retries)` before returning exit code 0. Non-terminal chunks are logged with their stuck state. This prevents runs from being marked "complete" when chunks are stuck at intermediate `_PENDING` states.

**mark_run_complete guard**: In `main()`, the `--realtime` exit handler now loads the manifest and checks `is_run_terminal()` before calling `mark_run_complete()`. Previously, exit code 0 (which meant zero failure files) would unconditionally mark the run complete, even with non-terminal chunks.

**analyze_results.py final-step-only**: `load_results()` now reads `MANIFEST.json` to determine the final pipeline step and only loads `{last_step}_validated.jsonl` files. Previously it globbed `*_validated.jsonl` across all steps, loading 4x the data for a 4-step pipeline. Falls back to loading all files if the manifest is unavailable.

**mark_run_running allows resume of premature completions**: `mark_run_running()` now checks chunk states when the run is "complete" or "failed". If any chunks are non-terminal (not VALIDATED/FAILED), the status is updated to "running" to allow the resume. Previously the guard `if current_status not in ("complete", "failed")` blocked all transitions from terminal states, even when chunks still needed processing.

**Convergence loop in realtime_run**: The `for step in pipeline` loop was single-pass — chunks advancing through multiple steps during one execution would strand at intermediate `_PENDING` states. Now wrapped in an outer convergence loop (`max_passes = len(pipeline) + 1`) that re-scans pipeline steps until `is_run_terminal`, zero progress, or cost cap reached. Retry logic stays inside the inner per-step loop.

### Known Limitations
- `--retry-failures` creates new chunks but doesn't automatically re-run them

### Technical Debt
- Large orchestrate.py file could be split into modules
- Some validation logic duplicated between config_validator and validator

## 8. Testing

### Manual Testing
```bash
# Validate a config
python scripts/orchestrate.py --validate-config --config pipelines/Example/config.yaml

# Initialize a test run
python scripts/orchestrate.py --init --pipeline Example --run-dir runs/test_run --max-units 5

# Run in realtime mode
python scripts/orchestrate.py --realtime --run-dir runs/test_run --yes

# Watch batch mode
python scripts/orchestrate.py --watch --run-dir runs/test_run --interval 10
```

### Testing Crash Handling
```bash
# Start a run and Ctrl+C to test pause
python scripts/orchestrate.py --watch --run-dir runs/test_run

# Check manifest status
cat runs/test_run/MANIFEST.json | jq '.status'
```

### Key Test Files
- `runs/*/MANIFEST.json`: Check status field and `metadata.pid`
- `runs/*/RUN_LOG.txt`: Check for FAILED/PAUSED entries, SIGUSR1 traces
- `runs/*/TRACE_LOG.txt`: Per-request telemetry (API calls, batch operations)
- `runs/*/orchestrator.log`: Check watch mode output
