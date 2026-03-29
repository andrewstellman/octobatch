# Octobatch Development Context
> **File:** `ai_context/DEVELOPMENT_CONTEXT.md`
> **Status:** v1.1
> **Scope:** This file tracks technical state and learnings.
> Backlog priorities are communicated directly in chat prompts by the user.

## v1.1 Key Features (new in v1.1)
- **Fan-out steps** (`scope: fan_out`): Expand array fields into child units for one-to-many pipeline branching. Child IDs follow `{parent_id}__fan{NNN}` format. New chunks created respecting `processing.chunk_size`, registered as `{next_step}_PENDING`.
- **Analysis tooling** (`run_tools.py`): Cross-run comparison (`compare_runs`), hand-by-hand diff (`compare_hands`), failures-by-field reporting (`--failures-by`), run directory prefix resolution.
- **CLI enhancements**: `--restart` (stop and relaunch a run), `--name` (display name for runs), `--report` / `--report --json` (pipeline report from CLI), `--revalidate` (re-run validation on existing failures without API calls).
- **TUI enhancements**: Pipeline report modal (G key), mode switch scheduling (M key), intermediate results viewer (P key), AI troubleshooting with provider selection (T key), cross-run comparison with multi-select (Space + C keys), named runs (W key).
- **Stochastic testing**: AI-driven integration test runner (`tests/stochastic_runner.py`) that parses `ai_context/QUALITY.md` fitness scenarios and runs registered tests N times.
- **Expression step optimization**: Expression steps bypass `max_inflight_batches` throttle in batch mode (no API call needed).

## v1.0 Key Features
- **Multi-provider support**: Gemini, OpenAI, Anthropic (all with batch API support)
- **Batch and Realtime execution modes**: Cheaper batch processing or faster realtime API calls
- **TUI Dashboard**: Live progress monitoring, run management, diagnostics
- **Monte Carlo simulations**: Seeded randomness with `processing.expressions`
- **Multi-step pipelines**: Chain multiple LLM calls with data flow between steps
- **Gzip post-processing**: Built-in compression via `type: gzip` post-process steps
- **Cost estimation and tracking**: Registry-driven pricing from `scripts/providers/models.yaml`

## Architecture Notes
- **Pricing is registry-driven**: All pricing comes from `scripts/providers/models.yaml`. Pipeline configs do NOT contain pricing blocks.
- **Provider/model are optional in configs**: Can be specified at runtime via CLI flags (`--provider`, `--model`) or TUI selection
- **Resolution precedence**: CLI flags > Step config > Global config > registry default_model
- **Per-step provider/model**: Steps can specify `provider` and/or `model` in config.yaml; `get_step_provider()` resolves with CLI override tracking via manifest metadata
- **Fan-out step lifecycle**: Parent chunk reads array field → creates child units → packs into new chunks respecting `chunk_size` → parent chunk marked `VALIDATED` (terminal) → child chunks registered as `{next_step}_PENDING`. Report shows fan-out boundary as "N units created from M parents".
- **Scheduled mode switch**: TUI writes `manifest.metadata.scheduled_mode_switch` → orchestrator reads it in both `watch_run()` and `realtime_run()` → drains outstanding batches via `_drain_outstanding_batches()` → clears flag → calls the other mode's entry point.

<!--
PURPOSE: This file enables continuity between Claude Code sessions.
When starting a new session, paste this prompt:

    Read ai_context/DEVELOPMENT_CONTEXT.md and bootstrap yourself to continue development.

Claude will read this file, then follow the bootstrap instructions to load
additional context from CONTEXT.md files throughout the project.
-->

## Quick Start Prompt

```
Read ai_context/DEVELOPMENT_CONTEXT.md and bootstrap yourself to continue development.
```

---

## Bootstrap Instructions

When you receive the bootstrap prompt:

1. Read this entire file (ai_context/DEVELOPMENT_CONTEXT.md) - session state and recent work
2. Read the architecture overview:
   - `ai_context/PROJECT_CONTEXT.md` - System architecture, design patterns, folder structure
3. Read the component context files:
   - `scripts/CONTEXT.md` - Orchestrator and CLI tools
   - `scripts/tui/CONTEXT.md` - TUI application structure
   - `pipelines/CONTEXT.md` - Pipeline configuration
4. Review "Current Focus" to understand active work
5. Review "Active Bugs" for any debugging in progress
6. Ask the user: "I've loaded the context. Ready to continue with [Current Focus]. Should I proceed or is there something else you'd like to work on?"

---

## Last Updated
**Mar 21, 2026** - Added 5 active bugs (TUI UX issues from production use), 5 new technical learnings (#44-48), updated active runs status

## Current Focus
- v1.1 stabilization and testing
- TUI UX fixes: stuck-status management (BUG-2), pending-status resume (BUG-3), restart messaging (BUG-1)
- Rate limit handling improvements (BUG-5)
- Game content pipelines: TarotEnhance (9,240 triples), ElenaDialogue v2, Sallos demon dialog (design phase)

## Recently Completed
- **Mar 9**: v1.1 WP4 — Fan-out steps (`scope: fan_out`), `--revalidate` flag (already implemented), stochastic integration testing runner
- **Mar 9**: v1.1 WP3 — TUI enhancements: pipeline report modal (G), mode switch (M), intermediate results (P), AI troubleshooting (T), cross-run comparison (Space+C), named runs (W)
- **Mar 9**: v1.1 WP2 — Analysis tooling: `compare_runs()`, `compare_hands()`, `--failures-by` in `generate_report()`, `_resolve_run_dir()` prefix matching
- **Mar 9**: v1.1 WP1 — CLI enhancements: `--restart`, `--name`, `--report`/`--report --json`, expression step batch optimization
- **Mar 9**: Test file reorganization — renamed test_wp*.py to meaningful names (test_cli_flags.py, test_analysis.py, test_tui_features.py, test_fan_out.py, test_stochastic_runner.py). Audited for coverage theater; 7 tests removed as metric-padding. Run `pytest tests/` to verify current count.
- **Feb 24**: Dead code cleanup — removed 4 orphaned files: `scripts/tui/widgets.py` (shadowed by `widgets/` package), `scripts/tui/styles.py` (unused CSS constants, never imported), `scripts/find_and_mark_missing.py` (superseded by `--verify`/`--repair`), `scripts/generate_report.py` (unconfigured run-scope step). Cleaned up references in CONTEXT.md files and orchestrate.py comment.
- **Feb 24**: Code review cycle — 19 fixes + 3 corrections from verification probe across orchestrator, TUI, and provider code
- **Feb 24**: Archive/unarchive TUI feature — `X` key archives runs (moves to `runs/_archive/`), `H` key toggles show/hide archived runs on home screen. `ArchiveConfirmModal` in `screens/modals.py`. `scan_runs()` accepts `include_archived` parameter. Terminal-only safety check (cannot archive running runs).
- **Feb 24**: Integration tests — 10/10 runs pass, 70/70 checks pass across all 3 providers (Gemini, OpenAI, Anthropic)
- **Feb 24**: Documentation finalized — README, TOOLKIT, walkthroughs, contributing, tests/README
- **Feb 24**: Copy script created for release repo

## v1.0 Release (Feb 2-21)
<details>
<summary>Full v1.0 development history (click to expand)</summary>

- **Feb 21**: CLI tools (`--version`, `--ps`, `--info`, `--verify`, `--repair`) and TUI startup perf (manifest summary cache, async startup loading), launcher scripts
- **Feb 21**: 8 orchestrator enhancements — enhanced `--watch` output, collapsed throttle logging, manifest PID, SIGUSR1 handler, request-level telemetry (TRACE_LOG.txt), `--quiet` flag
- **Feb 14**: Extended schema coercion (response unwrapping, string→array, enum normalization), type coercion (str→int/float/bool, float→int), validation pipeline sequential communicate() fix
- **Feb 14**: TUI retry state visibility (retrying vs exhausted), Otto terminal narratives and mood faces, pipeline scroll preservation, failure display fixes
- **Feb 14**: Orchestrator stability — zero valid units stop, FileNotFoundError guard, configurable subprocess timeout (600s default), COERCE telemetry fix, false STUCK detection fix
- **Feb 14**: Validation pipeline fixes — pre-merge before subprocess, schema validator field reconstruction, namespace error fix, custom pipeline validation fixes
- **Feb 14**: Comprehensive documentation update (README, TUI guide, configuration, PIPELINE_GUIDE)
- **Feb 12**: Pipeline funnel display, retry via .bak mechanism, Otto status narrator, splash screen key passthrough
- **Feb 11**: Schema gap fixes, per-step provider/model override testing
- **Feb 7**: Validation vs hard failure differentiation, per-step provider/model overrides, Otto the Octopus integration, batch pipeline boxes, manifest consistency auto-fix, convergence loop, SIGPIPE handling, GOOGLE_API_KEY standardization, stability fixes, TUI improvements (threaded unit loading, mode column, failure counts, DataTable, log viewer)
- **Feb 2**: v1.0 release — multi-provider support (Gemini/OpenAI/Anthropic), registry-driven pricing, gzip post-processing, provider-agnostic pipelines
- **Jan 29-30**: Failure inspection tooling, auto-refresh, pause functionality, subprocess deadlock fix, live progress logging

</details>

## Active Bugs Being Debugged

### BUG-1: TUI Restart Dialog Message Implies Data Loss
- **Symptom**: Restart confirmation says "This will kill the current process and relaunch" — users interpret this as losing progress
- **Better message**: "This will stop the current orchestrator process and start a new one. All completed work is preserved — it will resume from where it left off."
- **Location**: `scripts/tui/screens/home_screen.py` or `main_screen.py` — restart confirmation dialog
- **Severity**: UX (misleading, not broken)
- **Discovered**: Mar 21, 2026

### BUG-2: Cannot Pause or Kill Runs With "stuck" Status
- **Symptom**: `Cannot pause run with status 'stuck'` — user cannot manage rate-limited runs
- **Root cause**: TUI status guards only allow pause/kill on "running" status. "stuck" (has errors, e.g. sustained 429s) is excluded.
- **Fix**: Allow pause and kill actions on "stuck" status. Stuck runs have an active orchestrator PID that needs stopping.
- **Location**: `scripts/tui/screens/home_screen.py` — status guard in pause/kill handlers
- **Severity**: UX blocking (user cannot manage rate-limited runs)
- **Discovered**: Mar 21, 2026

### BUG-3: Cannot Resume "pending" Runs From TUI
- **Symptom**: `Cannot resume run with status 'pending'` — newly initialized runs require CLI to start
- **Root cause**: TUI resume (R key) only works on paused/failed/zombie runs. Pending runs (initialized but never started) are excluded.
- **Fix**: Allow TUI to launch pending runs — detect pending status and invoke the appropriate CLI command (--watch or --realtime based on user selection).
- **Location**: `scripts/tui/screens/home_screen.py` — resume handler
- **Severity**: UX gap (forces CLI workaround)
- **Discovered**: Mar 21, 2026

### BUG-4: format_step_provider_tag Crashes When provider_instance Is None
- **Symptom**: `NoneType has no attribute 'config'` in `format_step_provider_tag()` when API key is missing
- **Root cause**: `get_provider()` throws on missing API key, caught by broad except, leaves `realtime_provider = None`. Then `format_step_provider_tag(realtime_provider)` dereferences it.
- **Fix**: Add defensive None check: `if provider_instance is None: return "unknown"`
- **Location**: `scripts/orchestrate.py` — `format_step_provider_tag()` around line 1166
- **Severity**: Crash (but only when API key is missing)
- **Discovered**: Mar 21, 2026

### BUG-5: Rate-Limited Runs Enter Tight Retry Loop Instead of Backing Off
- **Symptom**: Orchestrator retries 429 RESOURCE_EXHAUSTED every ~17 seconds indefinitely, flooding logs and burning quota check cycles
- **Root cause**: Batch retry logic doesn't distinguish sustained rate limits from transient errors. No exponential backoff for 429s.
- **Note**: Adaptive rate limiting infrastructure exists but may not be fully engaged for batch-mode 429 responses.
- **Fix**: Implement exponential backoff specifically for 429 responses, or auto-pause after N consecutive 429s with user notification.
- **Location**: `scripts/orchestrate.py` — batch retry loop, `scripts/providers/gemini.py` — error handling
- **Severity**: Resource waste + poor UX (run appears stuck with no recovery path)
- **Discovered**: Mar 21, 2026
- **See also**: FEATURE-1 (cross-process rate limit coordination)

### BUG-6: TUI Cost Calculation Uses Hardcoded Flash Pricing as Fallback
- **Symptom**: TUI shows drastically wrong cost for runs using Gemini 2.5 Pro. Output priced at $0.30/M (Flash batch) instead of $5.00/M (Pro batch) — a 17x undercount.
- **Root cause**: `_calculate_cost_from_manifest()` in `main_screen.py` line 2283 falls back to `input_rate = 0.075, output_rate = 0.30` when `config.api.pricing` block is missing. But pricing is registry-driven (`models.yaml`), not config-driven — so the fallback always triggers.
- **Fix**: Load pricing from `scripts/providers/models.yaml` using the manifest's provider+model, not from the pipeline config. The registry is the single source of truth for pricing.
- **Location**: `scripts/tui/screens/main_screen.py` — `_calculate_cost_from_manifest()` lines 2280-2295
- **Severity**: Critical — cost display is silently wrong by 10-17x for Pro models, giving false confidence that runs are cheap
- **Discovered**: Mar 21, 2026

### BUG-7: No Pre-Run Cost Estimate That Accounts for Fan-Out Expansion
- **Symptom**: No way to see projected total cost before committing to a run. The `--init` step doesn't estimate cost, and `cost_cap_usd` only applies to realtime mode.
- **Root cause**: Cost estimation only happens after batches return with token counts. For fan-out pipelines, the true unit count isn't known until fan-out completes, but a reasonable estimate could be computed from item data + expected fan-out multiplier.
- **Impact**: Elena run was expected to cost ~$100 but actual cost is ~$1,856. The fan-out from 5,281 → 51,712 units was the unaccounted multiplier.
- **Location**: `scripts/orchestrate.py` — `init_run()`, `scripts/tui/screens/new_run_modal.py`
- **Severity**: Critical — users commit to multi-hundred-dollar runs with no cost visibility
- **Discovered**: Mar 21, 2026
- **See also**: FEATURE-2 (cost controls)

## Backlog Reference

The backlog is managed in Claude.ai/Gemini planning sessions, not in this file.

This file tracks **technical state** (what was built, how it works, active bugs).
**Priorities and planning** are communicated directly in chat prompts.

### FEATURE-1: Cross-Process Rate Limit Coordination and PID Observability

**Motivation**: On Mar 21, 2026 four orchestrator processes were discovered running simultaneously against the same Gemini quota (an old `elena_full_26pro`, two copies of `elena_full_26pro_v2`, and `enhance_pro`). All four were stuck in 429 retry loops. Manifests showed `pid: None` so the TUI couldn't surface this. The user had to manually `ps aux | grep orchestrate` to find and kill them. This is a systemic gap — Octobatch has no awareness of sibling processes sharing the same API quota.

**Scope**: Three related capabilities that build on existing adaptive rate limiting infrastructure:

**1. Process Registry and PID Observability**
- Write a lightweight lockfile or PID registry (e.g., `runs/.orchestrator_pids.json`) updated by each orchestrator on start/stop
- Each entry: `{run_dir, pid, provider, model, started_at, last_heartbeat}`
- Heartbeat updated every tick cycle so stale entries can be detected (process died without cleanup)
- TUI home screen shows active orchestrator count and warns on duplicates for the same run
- `--status` CLI flag reads the registry and reports all active orchestrators
- On init/watch/realtime, check registry for existing process on same run_dir — warn or refuse if duplicate

**2. Cross-Process Rate Limit Coordination**
- Orchestrators sharing a provider+model pair should coordinate submission rates
- Option A (simple): File-based semaphore. Before submitting a batch, check how many sibling processes are active for same provider. Divide submission rate by N.
- Option B (better): Shared rate limit state file (e.g., `runs/.rate_state_{provider}.json`) recording last 429 timestamp, backoff level, and active process count. Each orchestrator reads before submitting, writes after 429. Exponential backoff is shared — one process hitting 429 backs off ALL siblings.
- Option C (best): Single submission queue. One process acts as rate-limit coordinator, others enqueue requests. Adds complexity but eliminates contention entirely.
- **Recommendation**: Start with Option B — it's lock-free (atomic file writes), requires minimal orchestrator changes, and handles the common case (2-3 concurrent runs).

**3. Adaptive Backoff for Sustained 429s**
- Existing adaptive rate limiting infrastructure may only cover realtime mode or per-request throttling
- Batch-mode 429 handling needs: exponential backoff (30s → 60s → 120s → 300s), cap at 5 min
- After N consecutive 429s (configurable, default 5), auto-pause the run and notify TUI
- TUI shows "Auto-paused: rate limited" with option to resume or schedule delayed restart
- Log the backoff state so resumed runs start with the last backoff interval, not from scratch

**Implementation notes**:
- PID registry (#1) is prerequisite for coordination (#2)
- Registry cleanup: use heartbeat staleness (>60s) to detect dead processes, not just PID existence (PIDs can be recycled)
- The TUI already has `get_process_health()` — extend it to read the registry
- `--init` should NOT register (no API calls yet). `--watch` and `--realtime` register on entry, deregister on exit/signal.
- Signal handlers (SIGTERM, SIGINT) must deregister before exit

**Priority**: High — this is the root cause of the Mar 21 rate limit incident and will recur whenever multiple runs target the same provider.

### FEATURE-2: Cost Controls, Estimation, and Budget Guardrails

**Motivation**: On Mar 21, 2026 a run estimated at ~$100 turned out to cost ~$1,856. The TUI showed wrong costs (17x undercount due to hardcoded Flash pricing fallback), there was no pre-run estimate, and no budget guardrail stopped the run. This is the most impactful UX gap — users need accurate cost visibility before, during, and after runs.

**Scope**: Five capabilities, roughly in priority order:

**1. Fix Registry-Driven Cost Calculation (BUG-6 fix)**
- `_calculate_cost_from_manifest()` must load pricing from `scripts/providers/models.yaml` using the run's provider+model from manifest metadata
- Never fall back to hardcoded rates — if model isn't in registry, show "cost unknown" rather than a wrong number
- Apply batch vs realtime pricing based on manifest mode
- This fix alone would have shown ~$347 spent instead of ~$20

**2. Pre-Run Cost Estimation at Init Time**
- `--init` should compute and display an estimated total cost before creating the run
- For fan-out pipelines: estimate fan-out multiplier from a sample of items (e.g., count average array length of the `field` being fanned out)
- Formula: `sum over LLM steps of (estimated_units × avg_input_tokens × input_rate + estimated_units × avg_output_tokens × output_rate)`
- Token estimates: use schema `maxLength` fields as upper bound for output, template size + item size for input
- Display: "Estimated cost: $1,200–$1,800 (fan-out expansion: ~10x from generate to refine/score)"
- For `--init` in CLI: print estimate and require `--yes` or interactive confirmation
- For TUI new run modal: show estimate before launch button

**3. Budget Cap for Batch Mode**
- Extend `cost_cap_usd` to work in batch mode, not just realtime
- After each batch completes, compute cumulative cost. If approaching cap (e.g., 80%), warn. If exceeding cap, auto-pause.
- Manifest tracks `metadata.cost_spent_usd` updated after each batch validation
- TUI shows budget bar: `$347 / $500 cap [=======>        ] 69%`
- CLI: `--cost-cap 500` flag, also settable in config.yaml `api.batch.cost_cap_usd`

**4. Accurate In-Flight Cost Projection**
- Current projection (`current_cost / progress_ratio`) is correct in principle but broken because input cost is wrong (BUG-6)
- After BUG-6 fix, also improve progress_ratio for fan-out pipelines: weight steps by expected unit count, not equally
- Example: generate=5,281 units (5%), expand_lines=0 cost (0%), refine=51,712 units (47.5%), score=51,712 units (47.5%)
- Show both "spent so far" and "projected total" prominently in TUI stats panel

**5. Post-Run Cost Report**
- `--report` should include a cost breakdown by step with actual token counts and pricing
- Compare actual vs estimated if pre-run estimate was computed
- Track cost per unit for benchmarking across runs

**Implementation notes**:
- #1 is a bug fix, ship immediately — the wrong pricing fallback is actively misleading
- #2 and #3 require token estimation heuristics — start conservative (overestimate rather than under)
- Fan-out multiplier estimation: read a sample of items, compute `mean(len(item[fan_out_field]))`, multiply by upstream unit count
- `models.yaml` is already the pricing source of truth — just need to make the TUI use it

**Priority**: Critical — cost visibility is a safety issue. Users are committing to $1,800 runs thinking they cost $100.

## Key Technical Learnings

> **IMPORTANT**: Always include the REASONING (the "Why") for each learning.
> This prevents future sessions from "refactoring" a deliberate decision.

### 1. Recursive `set_timer()` for Auto-Refresh on Pushed Screens
- **What**: Use recursive `set_timer()` instead of `set_interval()` for auto-refresh on screens added via `push_screen()`
- **Why**: Textual's `set_interval()` callbacks are not reliably serviced on pushed screens. The timer is created but the callback never fires. Recursive `set_timer()` works because each one-shot timer schedules the next.
- **Discovered**: Jan 30, 2026
- **Location**: `scripts/tui/screens/main_screen.py` - `_do_refresh()` method

### 2. Threading for Subprocess Pipe I/O
- **What**: Use threading when writing to subprocess stdin while reading from stdout/stderr
- **Why**: Writing to stdin while reading from stdout can deadlock if pipe buffers fill (64KB default). The "flush of closed file" error occurred because we closed stdout before finishing stdin writes. Threading allows concurrent read/write.
- **Discovered**: Jan 29, 2026
- **Location**: `scripts/orchestrate.py` - `run_validation_pipeline()`

### 3. Template Variable Naming
- **What**: Template errors like "'past_card' is undefined" indicate variable naming mismatch between pipeline steps
- **Why**: Each pipeline step passes data to the next. If step N outputs `card_past` but step N+1's template expects `past_card`, you get undefined variable errors. Check the output schema of previous step matches template variables.
- **Discovered**: Jan 30, 2026

### 4. Guard Against Duplicate Orchestrators
- **What**: Always check `get_process_health()` before modifying manifest or spawning orchestrator
- **Why**: Multiple orchestrators running simultaneously can corrupt manifest state. One might finish quickly and set status to "complete" while work is still in progress.
- **Discovered**: Jan 30, 2026
- **Location**: `scripts/tui/screens/main_screen.py` - `_do_retry()`

### 5. Full Refresh After State Changes
- **What**: After pause/kill/retry actions, call `action_refresh()` not just `_load_data()`
- **Why**: `_load_data()` updates internal state but doesn't refresh all UI elements. `action_refresh()` does full reload including stats cards and both run sections.
- **Discovered**: Jan 30, 2026
- **Location**: `scripts/tui/screens/home_screen.py` - `_refresh_after_pause()`

### 6. Transition States to Prevent UI Flicker
- **What**: Use a tracking set (e.g., `_pausing_run_ids`) to track items transitioning between states, with polling to detect completion
- **Why**: When an action triggers a state change (like pause), the process dies immediately but the manifest update takes milliseconds. During this gap, the run appears as "zombie" causing UI flicker. By tracking the transition explicitly and polling for completion, we can show an intermediate state ("Pausing...") instead of flickering to zombie and back.
- **Pattern**:
  1. Add item to tracking set before action
  2. Use `set_timer(0.1, poll_function)` to poll for completion
  3. Poll function checks for target state, removes from set when complete or on timeout
  4. Render functions check tracking set to show transition indicator
  5. Data loading functions keep transitioning items in appropriate list
- **Discovered**: Jan 30, 2026
- **Location**: `scripts/tui/screens/home_screen.py` - `_pausing_run_ids`, `_poll_for_pause_completion()`

### 7. Auto-Refresh Can Reset Widget State
- **What**: When using auto-refresh with DataTable, explicitly enforce selection/cursor state in the render method, not just in watchers
- **Why**: Watchers fire on change, but render fires every refresh cycle. If you only set cursor/selection state in watchers, auto-refresh will reset the widget to default state. The fix is to add an `else` branch in the render function to enforce inactive state when selection is elsewhere.
- **Discovered**: Jan 30, 2026
- **Location**: `scripts/tui/screens/home_screen.py` - `_render_recent_runs()`

### 8. Capture Data Before Transformation
- **What**: When debugging pipeline failures, capture raw output before merging/transforming
- **Why**: The orchestrator's validation pipeline merges LLM output with input context, then overwrites the failure record's `input` field with step input. Without capturing the raw LLM response first, you lose visibility into what the LLM actually returned. Save `raw_response` before overwriting.
- **Discovered**: Jan 30, 2026
- **Location**: `scripts/orchestrate.py` - `run_validation_pipeline()` failure processing

### 9. Guard mark_run_complete with is_run_terminal
- **What**: Always check `is_run_terminal(manifest, max_retries)` before calling `mark_run_complete()`
- **Why**: `realtime_run` returns exit code 0 based on `remaining_failures == 0`, which only counts failure file lines. A chunk stuck at a mid-pipeline `_PENDING` state (no failure files) passes this check. Without the terminal guard, the run gets marked "complete" with non-terminal chunks. The batch `--watch` path has the same risk.
- **Discovered**: Feb 7, 2026
- **Location**: `scripts/orchestrate.py` - `main()` at the `--realtime` exit handling

### 11. Convergence Loop for Multi-Step Realtime Execution
- **What**: Wrap `for step in pipeline` in an outer convergence loop that re-scans until no progress or terminal
- **Why**: The single-pass `for step in pipeline` processes each step once. If a chunk advances from step 1 to step 2 during the pass, it won't be picked up by step 2 (already iterated past). The convergence loop re-scans the pipeline, finding newly-advanced chunks on each pass. `max_passes = len(pipeline) + 1` prevents infinite loops. `progress_this_pass` tracks chunks processed per pass; zero progress means convergence.
- **Discovered**: Feb 7, 2026
- **Location**: `scripts/orchestrate.py` - `realtime_run()`, convergence loop wrapping `for step in pipeline:`

### 12. Subprocess stdin Must Be DEVNULL When Spawned From TUI
- **What**: Always pass `stdin=subprocess.DEVNULL` when spawning orchestrator subprocesses from the TUI
- **Why**: Textual puts the terminal into raw mode. Child processes inherit the parent's file descriptors by default, including stdin. If the child inherits the raw-mode stdin, it can fight with Textual for terminal input. On exit, the terminal's cooked mode isn't fully restored, leaving the user's shell broken (no echo, no line editing). `subprocess.DEVNULL` severs the child's stdin from the terminal entirely.
- **Discovered**: Feb 7, 2026
- **Location**: `scripts/tui/utils/runs.py` - `resume_orchestrator()`, `scripts/tui/screens/run_launcher.py` - both launch functions

### 13. Idempotency Check for Realtime Step Resumption
- **What**: Before running a step in `run_step_realtime()`, check if `{step}_validated.jsonl` + `{step}_failures.jsonl` already cover expected items
- **Why**: When resuming a run, the convergence loop re-enters `run_step_realtime()` for every chunk/step combination. Without an idempotency check, already-complete steps get reprocessed: prompts regenerated, API called again, tokens wasted. The expected item count must be capped by the input file size for non-first steps, because earlier steps may have filtered units (e.g., 100 items → step 1 passes 99 → step 2 expects 99, not 100). Both valid and failed counts are summed for the completeness check. A 90% fallback handles the case where validation silently filters units without writing to the failures file: if valid_count >= 90% of expected and failures file is empty/missing, the step is treated as complete.
- **Discovered**: Feb 7, 2026
- **Location**: `scripts/orchestrate.py` - `run_step_realtime()`, idempotency check at top of function

### 10. Step-Scoped Unit Loading in TUI
- **What**: `_load_all_units()` should be scoped to the selected step, not load all steps
- **Why**: A 600-unit, 4-step pipeline loads ~2400 rows when loading all steps. The step filter applies AFTER loading, so all the I/O and memory allocation happens regardless. Scoping to the selected step reduces loading by ~75% for multi-step pipelines. `_unique_steps` should come from the manifest pipeline list, not from loaded units.
- **Discovered**: Feb 7, 2026
- **Location**: `scripts/tui/screens/main_screen.py` - `_load_all_units()`, `_render_unit_view()`

### 14. SIGPIPE Handling for Piped Output
- **What**: Set `signal.signal(SIGPIPE, SIG_DFL)` at the top of `main()` and catch `BrokenPipeError` before the general `Exception` catch in the entry point
- **Why**: Python by default converts SIGPIPE into a `BrokenPipeError` exception. When orchestrator output is piped through a command that closes early (e.g., `| head -20`), the exception propagates up through the `except Exception` handler, which calls `mark_run_failed()` — marking a perfectly healthy run as failed. Restoring SIG_DFL makes the process exit silently on SIGPIPE like C programs do. The `BrokenPipeError` catch is a belt-and-suspenders fallback that closes stdout/stderr to suppress Python's "Exception ignored" message.
- **Discovered**: Feb 7, 2026
- **Location**: `scripts/orchestrate.py` - `main()` (SIGPIPE handler) and `if __name__ == "__main__"` (BrokenPipeError catch)

### 15. Manifest Status Can Diverge From Chunk States
- **What**: Always verify manifest status consistency before acting on it — all chunks may be terminal while status says "failed" or "running"
- **Why**: Several failure modes leave the manifest status inconsistent: BrokenPipeError marking a successful run as failed, SIGKILL preventing the `mark_run_complete` call, or zombie detection on a run that actually finished. The fix is defense-in-depth: (1) TUI's 5-second progress ticker runs `check_manifest_consistency()` to auto-correct stale status on running/failed/zombie runs; (2) `realtime_run()` and `watch_run()` check `is_run_terminal()` before doing any work, immediately marking complete and returning if all chunks are done. This prevents wasted API calls and incorrect dashboard display.
- **Discovered**: Feb 7, 2026
- **Location**: `scripts/tui/utils/runs.py` - `check_manifest_consistency()`, `scripts/tui/screens/home_screen.py` - `_do_progress_tick()`, `scripts/orchestrate.py` - early exit in `realtime_run()` and `watch_run()`

### 16. Per-Step Provider Resolution Requires CLI Override Tracking
- **What**: Store `cli_provider_override` and `cli_model_override` booleans in manifest metadata at init time; `get_step_provider()` checks these before applying step-level overrides
- **Why**: CLI flags are baked into the snapshotted config's `api` section during `init_run()`, making them indistinguishable from global config values. Without explicit tracking, step-level overrides would incorrectly override CLI flags. The manifest metadata flags let `get_step_provider()` know "the user explicitly passed --provider on the CLI" vs "this value came from config.yaml". The TUI must also cooperate: selecting "Use Pipeline Config" passes `None` as the override (not the resolved fallback), so the flags remain False.
- **Discovered**: Feb 7, 2026
- **Location**: `scripts/providers/__init__.py` - `get_step_provider()`, `scripts/orchestrate.py` - `init_run()` manifest metadata, `main()` CLI override logic

### 17. google-genai SDK Unconditionally Checks GEMINI_API_KEY
- **What**: Temporarily unset `GEMINI_API_KEY` from `os.environ` before creating `genai.Client()`, restore it in a `finally` block
- **Why**: The google-genai SDK's `_api_client.py` calls `get_env_api_key()` during `Client.__init__()` regardless of whether `api_key` was explicitly passed. This function warns when both `GOOGLE_API_KEY` and `GEMINI_API_KEY` are set. Since we standardized on `GOOGLE_API_KEY` but users may still have `GEMINI_API_KEY` in their environment, the warning fires every tick cycle. Modifying the SDK isn't an option, so env var manipulation at client creation is the workaround.
- **Discovered**: Feb 7, 2026
- **Location**: `scripts/providers/gemini.py` - `_init_client()`

### 18. Failure Categorization by failure_stage
- **What**: `categorize_step_failures()` in orchestrate.py and `_count_step_failures()` in main_screen.py categorize failures by `failure_stage` field: `schema_validation`/`validation` → retryable validation failures (yellow), `pipeline_internal` → hard failures (red)
- **Why**: Not all failures are retryable. Validation failures (LLM returned bad output) can be retried — a different LLM response may pass. But `pipeline_internal` failures mean records were lost in the pipeline and retrying won't help. The TUI now surfaces this distinction everywhere: pipeline boxes show separate yellow/red rows, sidebar stats split Validation/Failed, the R key only retries validation failures, and the home screen shows "complete ⚠ (N)" for runs with validation failures vs red "failed" for hard failures. API-level errors (rate limits, timeouts) are handled at the chunk level via automatic retries and never appear as unit-level failure records.
- **Discovered**: Feb 7, 2026
- **Location**: `scripts/orchestrate.py` - `categorize_step_failures()`, `scripts/tui/screens/main_screen.py` - `_count_step_failures()`, `action_retry_failures()`, `_update_run_stats_panel()`, `scripts/tui/widgets/pipeline_view.py` - `render_pipeline_boxes()`, `scripts/tui/screens/home_screen.py` - `_get_run_status_text()`

### 19. .bak Files as Retry Signal for Idempotency Bypass
- **What**: When retrying validation failures, rotate `{step}_failures.jsonl` to `.bak` before resetting chunk state. In `run_step_realtime()`, check for `.bak` existence before applying the 90% idempotency fallback. Clean up `.bak` files after step completes or on SKIP.
- **Why**: The 90% idempotency fallback in `run_step_realtime()` treats a step as complete if `valid_count >= 90% * expected` and `failures == 0`. After retry, failures are removed (archived to `.bak`) but valid count is still high (e.g., 19/20 = 95%), so the fallback triggers and SKIPs the step — defeating the retry. The `.bak` file serves dual purpose: (1) archives the original failures for audit trail, (2) signals the idempotency check that this step was recently reset for retry and should NOT use the 90% fallback. Without this signal, the retry path is dead code.
- **Discovered**: Feb 12, 2026
- **Location**: `scripts/orchestrate.py` - `retry_validation_failures()` (creates .bak), `run_step_realtime()` (checks .bak), `scripts/tui/utils/runs.py` - `reset_unit_retries()` (creates .bak from TUI path)

### 21. Guard Previous Step Output Before Processing Next Step
- **What**: In `run_step_realtime()`, always check that the previous step's `_validated.jsonl` file exists before attempting to prepare prompts. If missing, mark the chunk FAILED and return gracefully.
- **Why**: When a step fails (timeout, validation error, provider error), it may never create its `_validated.jsonl` output. The convergence loop then advances to the next step which tries to open the missing file, causing an unhandled `FileNotFoundError` that crashes the entire run. The batch mode already had this guard (lines 2313-2324 of orchestrate.py), but realtime mode did not. The fix also marks chunks FAILED when `prepare_prompts()` fails, preventing infinite retry loops where the chunk stays PENDING but can never succeed.
- **Discovered**: Feb 14, 2026
- **Location**: `scripts/orchestrate.py` - `run_step_realtime()`, lines ~3761-3800

### 22. Subprocess Timeout Must Be Configurable for Large Realtime Runs
- **What**: The 300-second `SUBPROCESS_TIMEOUT_DEFAULT` was too short for realtime runs with expensive models. Changed to 600s default with `api.subprocess_timeout_seconds` config override.
- **Why**: 19 units through Claude Sonnet in realtime mode legitimately takes 5+ minutes. The validation subprocess (which processes all chunk results) was timing out and returning `(0, 0)`, which meant no `_validated.jsonl` was created, which then triggered the FileNotFoundError in the next step. The timeout is now configurable per-pipeline since different models and chunk sizes have very different processing times.
- **Discovered**: Feb 14, 2026
- **Location**: `scripts/orchestrate.py` - `get_subprocess_timeout()`, `run_validation_pipeline()`, `prepare_prompts()`

### 23. Two-Stage Subprocess Pipes Deadlock on Large Input — Use Sequential communicate()
- **What**: The two-stage validation pipeline (`schema_validator.py | validator.py`) used 4 threads to pipe data between two subprocesses. This deadlocked for 85+ units (~131K tokens of coherence output) because pipe buffers filled up. Additionally, each `thread.join(timeout=T)` and `process.wait(timeout=T)` call waited independently, so the actual max wait was 4× the configured timeout (2400s instead of 600s).
- **Why**: Python's `subprocess.PIPE` buffers are OS-limited (~64KB on macOS). When p1 produces output faster than the `pipe_p1_to_p2` thread can forward it, p1 blocks on stdout write. Meanwhile p2 may block on stdin read waiting for data. The 8KB chunked read in the pipe thread can't prevent this when both processes have large stderr output that also needs draining. `communicate()` avoids this entirely — it uses internal threads to drain all pipes simultaneously and handles arbitrary data sizes correctly.
- **Fix**: Run schema_validator to completion with `communicate()`, take its stdout, feed it to validator with a second `communicate()`. Track wall-clock time and subtract elapsed from remaining timeout for stage 2.
- **Discovered**: Feb 14, 2026
- **Location**: `scripts/orchestrate.py` - `run_validation_pipeline()`

### 24. Validation Must See Accumulated Fields — Pre-Merge Before Subprocess
- **What**: Validation rules can reference fields from any prior pipeline step (e.g., score_coherence checking `all_stories` from merge_stories). The validator subprocess must receive pre-merged data, not raw LLM output.
- **Why**: The original flow ran validation on raw LLM output and merged with input data afterwards. This meant any validation rule referencing accumulated fields from earlier steps got NameError. The fix pre-merges `{**input_item, **raw_result}` before piping to the validation subprocess, so the validator sees all accumulated fields. The post-processing write loop is simplified since data is already merged.
- **Discovered**: Feb 14, 2026
- **Location**: `scripts/orchestrate.py` - `run_validation_pipeline()`

### 27. Schema Validator Strips Fields — Reconstruct Full Data Between Stages
- **What**: The two-stage validation pipeline (schema_validator → business logic validator) failed because the schema validator only outputs fields defined in the JSON schema. Accumulated fields from prior pipeline steps were stripped before the business logic validator saw them.
- **Why**: JSON Schema validation with `required` checks only requires listed fields but `schema_validator.py` outputs only the validated object (which is the LLM output conforming to the schema). Pre-merged fields like `all_stories`, `hand`, `total` from earlier expression/LLM steps are not in the schema and get dropped. The business logic validator then fails with NameError on rules referencing those fields.
- **Fix**: After schema validation completes, extract passed unit_ids from p1_stdout, then select the corresponding lines from the original pre-merged input_data (which has all fields). Feed this reconstructed data to the business logic validator instead of the stripped p1_stdout.
- **Discovered**: Feb 14, 2026
- **Location**: `scripts/orchestrate.py` - `run_validation_pipeline()`, between stage 1 and stage 2

### 25. Zero Valid Units Must Stop the Pipeline — Not Advance to Next Step
- **What**: When a step produces 0 valid units, the chunk must be marked FAILED. Advancing to the next step creates an empty input file, which causes the provider API to reject the batch with 400 INVALID_ARGUMENT, triggering infinite retries.
- **Why**: The original flow blindly advanced to `{next_step}_PENDING` regardless of valid count. An empty `_validated.jsonl` meant `prepare_prompts()` produced an empty `_prompts.jsonl`, which became an empty `.batch.jsonl`, which the API rejected. The PENDING→SUBMITTED→FAILED→PENDING cycle repeated forever. The fix checks `valid_count == 0 and failed_count > 0` in both batch and realtime paths and marks the chunk FAILED. A pre-submission guard also catches empty input files.
- **Discovered**: Feb 14, 2026
- **Location**: `scripts/orchestrate.py` - `tick_run()`, `run_step_realtime()`

### 20. Funnel Display Requires On-Disk Scanning (Not Manifest)
- **What**: Per-step valid counts for the funnel display come from scanning `{step}_validated.jsonl` files on disk via `_count_step_valid()`, not from `chunk_data.valid` in the manifest.
- **Why**: The manifest's `chunk_data.valid` field reflects the LAST pipeline step's validated count, not per-step counts. In a 3-step pipeline where the final step validates 222 units, ALL chunks report `valid=222` regardless of how many units each earlier step actually passed. This makes all steps show "222/500" instead of the correct funnel (e.g., Generate "406/500" → Score "262/406" → Wounds "222/262"). Scanning the actual `{step}_validated.jsonl` files gives accurate per-step counts. The funnel input for step N is the valid count of step N-1; step 1's input is the global `total_units`.
- **Discovered**: Feb 12, 2026
- **Location**: `scripts/tui/screens/main_screen.py` - `_count_step_valid()`, `_render_pipeline_content()`

### 26. STUCK Detection Must Compare Error Recency vs Recovery Recency
- **What**: `has_recent_errors()` was triggering false STUCK alarms. A run that hit a transient error but then successfully submitted new batches or received poll results was still shown as STUCK because the old approach just checked "any [ERROR] in last 20 lines."
- **Why**: Batch pipelines routinely encounter transient errors (API timeouts, 429 rate limits, validation failures) and recover automatically. The STUCK indicator should only fire when the error is the *most recent significant event* — meaning no recovery has happened since. The fix compares line indices (which reflect chronological order) of the last [ERROR] vs the last recovery event in a wider 50-line window.
- **Discovered**: Feb 14, 2026
- **Location**: `scripts/tui/utils/runs.py` - `has_recent_errors()`

### 28. Validation Rules Must Use Dynamic Counts — Never Hardcode Story Counts
- **What**: A custom pipeline's validation rules hardcoded `== 8` for expected item counts, but the actual count varies per unit (4 base items + 0-6 from a secondary generation step). This caused every downstream scoring and generation validation to fail.
- **Why**: The secondary generation step produces a variable number of items (0-6) depending on the input. After a merge step combines base + secondary items, the merged array can be 4-10 items. Validation rules must use `len(merged_array)` or `len(scores)` instead of hardcoded counts. Same principle applies to schema `oneOf` allowing string-typed integers — if business logic compares with `==`, both sides must be the same type.
- **Discovered**: Feb 14, 2026
- **Location**: Pipeline `config.yaml` (validation rules), pipeline schemas (count field type)

### 29. Funnel Display Gating Should Check Data, Not Step State
- **What**: The funnel progress display (`N/N` counts) was gated on `step.state == "pending"` which showed `--/--` for completed steps. Changed to check `funnel_valid == 0 and funnel_input == 0` instead.
- **Why**: `step.state` is derived from manifest chunk states via `StepStatus` objects built at mount time. For terminal runs, these can be stale or incorrectly set (e.g., steps after a `_FAILED` step get `state="pending"` even though they were never reached). The funnel data itself (from disk scanning via `_count_step_valid()`) is always fresh and accurate. Gating on the data directly avoids the stale-state problem entirely. Additionally, `_FAILED` chunk states were not handled in `data.py`'s status parsing — the failing step's failed units were lost.
- **Discovered**: Feb 14, 2026
- **Location**: `scripts/tui/widgets/pipeline_view.py` - `render_pipeline_boxes()`, `scripts/tui/data.py` - StepStatus construction

### 30. Initialize Otto Narrative on Mount for Terminal Runs
- **What**: `update_narrative()` must be called immediately after `OttoOrchestrator` init for runs that are already in a terminal state (complete/failed). The 2-second refresh tick is too late.
- **Why**: Otto's narrative only updated via `_diff_chunk_states()` in `_do_refresh()`, which fires 2 seconds after mount. For terminal runs opened after completion, the user sees "Otto is waiting for his next job" for 2 seconds, then it updates. Worse, for failed runs with a zombie/stuck status, the narrative might show "Waiting for Gemini..." because the old `_diff_chunk_states()` passed the provider but not the failure context. Calling `update_narrative()` with a `context` dict (containing `failed_step` and `failure_count` extracted from manifest) immediately on mount gives accurate feedback from the start.
- **Discovered**: Feb 14, 2026
- **Location**: `scripts/tui/screens/main_screen.py` - `on_mount()`, `scripts/tui/widgets/otto_orchestrator.py` - `update_narrative()`

### 31. Schema Validator Type Coercion — Postel's Law for LLM Output
- **What**: `schema_validator.py` now recursively traverses input data alongside the JSON Schema and coerces types before `jsonschema.validate()`. Supports str→int, str→float, str→bool, float→int. Follows `$ref` pointers via `_resolve_schema_node()`. Logs `[COERCE]` telemetry to stderr. Output is the coerced data.
- **Why**: LLMs frequently return data with slight type mismatches (e.g., `"2"` for `2`, `3.0` for `3`, `"true"` for `true`). These are semantically correct but fail strict JSON Schema validation. Rather than making schemas permissive with `oneOf` (which caused Learning #28's `int == str` bug in business logic), we coerce at the schema validation boundary — be liberal in what we accept from the LLM, but provide strict, correctly-typed data to the rest of the pipeline. Failed coercion (e.g., `"abc"` for an integer) leaves the value unchanged for jsonschema to report normally. This eliminates the need for `oneOf` type workarounds in schemas and prevents type-mismatch bugs in downstream business logic validation.
- **Discovered**: Feb 14, 2026
- **Location**: `scripts/schema_validator.py` - `coerce_data()`, `_resolve_schema_node()`, `_coerce_value()`

### 33. Preserve Scroll State Across TUI Refresh Cycles — Cache Rendered Content
- **What**: Before calling `static.update()` on a periodically refreshed widget, compare the new rendered content string against a cached copy of the last update. If unchanged, skip the update entirely (including any scroll-to-selected calls).
- **Why**: The TUI's 2-second refresh timer calls `_update_pipeline_panel()` which re-renders the pipeline boxes and calls `_scroll_to_selected_step()`. This resets the user's horizontal scroll position every tick — if the user scrolls right to see later pipeline steps, 2 seconds later it snaps back. Caching `self._last_pipeline_content` and comparing with string equality is cheap (the rendered string is small). When content hasn't changed (which is most ticks for idle or terminal runs), skipping the update preserves scroll position. The `force` parameter allows explicit re-renders (e.g., after step selection change or retry action) to bypass the cache.
- **Discovered**: Feb 14, 2026
- **Location**: `scripts/tui/screens/main_screen.py` - `_update_pipeline_panel()`, `self._last_pipeline_content`

### 38. Schema Coercion Does Not Propagate to Business Logic Validation
- **What**: Type coercion in `schema_validator.py` (stage 1) does NOT affect the data seen by the business logic validator (stage 2). The orchestrator reconstructs stage 2's input from the original pre-merged data (to preserve accumulated fields), discarding stage 1's coerced output. Validation rules that compare LLM-declared derived fields (e.g., `wound_count`) against computed values will silently fail on type mismatch (`int == str` → False).
- **Why**: The two-stage validation pipeline works like this: (1) schema_validator coerces types and validates structure, outputting coerced data to stdout. (2) The orchestrator extracts passed `unit_id`s from stage 1's stdout, then selects the corresponding lines from the ORIGINAL pre-merged input data (which has all accumulated fields but UN-coerced types). Stage 2 sees the original string values. This design was intentional (Learning #27) — schema validation strips accumulated fields, so the orchestrator must reconstruct from original data. The side effect is that coerced types are lost. Solutions: (a) compute derived fields in the rule expression itself (e.g., `len([v for v in ... if v != 0])` instead of comparing against declared `wound_count`), or (b) don't validate LLM-declared derived fields at all — they're informational and the schema already constrains their type.
- **Discovered**: Feb 14, 2026
- **Location**: `scripts/orchestrate.py` - `run_validation_pipeline()` (stage 2 input reconstruction), `pipelines/Tarot/config.yaml` - removed `all_wound_counts_valid` rule

### 37. Pipeline Box Text Must Fit box_width — Measure Before Rendering
- **What**: All text rendered inside pipeline boxes must fit within `box_width` (default 18). Use `cell_len()` to measure, and prefer shorter labels. Exhausted failures: `⚠ N exhausted` (max 16 chars) instead of `⚠ N failed (max retries)` (26 chars). Use `_format_count()` for numbers (1000→`1K`, 1500→`1.5K`).
- **Why**: `make_content_line()` computes `padding = box_width - cell_len(plain_text)`. When text exceeds `box_width`, padding goes negative and the text overflows the box boundaries, clipping into adjacent boxes or off-screen. At scale (500 units, 339 failures), `⚠ 339 failed (max retries)` was 26 chars. The fix is to use shorter labels. Key widths verified: retrying max 15, exhausted max 16, hard max 13 — all fit within 18.
- **Discovered**: Feb 14, 2026
- **Location**: `scripts/tui/widgets/pipeline_view.py` - failure row rendering, `_format_count()`

### 36. Summarize High-Volume Subprocess Telemetry Instead of Individual Logging
- **What**: When routing subprocess telemetry to the run log, collect lines by prefix and log a summary if count exceeds a threshold (5). Individual lines are logged only for small counts (debugging small runs).
- **Why**: The `[COERCE]` fix correctly filtered telemetry from failure records, but logging each `[COERCE]` line individually produced thousands of log lines at scale (500 units × 5 coerced fields = 2,500 lines). This bloated `RUN_LOG.txt` and slowed log viewing. The summary ("Type coercion applied: 2500 fields coerced") gives the same signal in one line. The threshold of 5 preserves individual detail for small/debug runs where each coercion is meaningful.
- **Discovered**: Feb 14, 2026
- **Location**: `scripts/orchestrate.py` - `run_validation_pipeline()`, coerce_lines collection and summary logging

### 35. Subprocess stderr Is a Shared Channel — Parse JSON vs Telemetry
- **What**: When a subprocess writes both structured failure records (JSON) and human-readable telemetry (like `[COERCE]` prefix lines) to stderr, the consumer MUST parse each line and only treat valid JSON as data records. Non-JSON lines are telemetry/logs and must never be written to JSONL data files or counted as records.
- **Why**: `schema_validator.py` writes `[COERCE]` telemetry to stderr alongside JSON failure records. `run_validation_pipeline()` in the orchestrator consumes stderr as failure data — it was counting every non-empty line as a failure and writing non-JSON lines to `{step}_failures.jsonl`. With type coercion active, each coerced field generated a `[COERCE]` line, so a 500-unit batch with 5 coerced fields each produced 2,500+ false failures. The fix: `json.loads()` each stderr line — successes are failure records, `JSONDecodeError` means telemetry. Telemetry is routed to the run log for visibility. This principle applies to any subprocess that shares stderr for both structured data and human-readable output.
- **Discovered**: Feb 14, 2026
- **Location**: `scripts/orchestrate.py` - `run_validation_pipeline()`, schema_failed counter (~line 1120), failures file writer (~line 1250)

### 34. Otto Mood Face for Terminal States — Persistent Visual Override
- **What**: Added `mood_face` attribute to `OttoState` and `set_mood(face)` method to `OttoWidget`. When set, `mood_face` takes priority in `_get_inner_face()` over reactive faces, active focus, idle overrides, and sleepy state. `update_narrative()` sets appropriate moods: `INNER_HAPPY` for clean complete, `INNER_DEAD` (✗ _ ✗) for failed/zombie, `INNER_SLEEPY` for paused, `None` for running (clears mood, returns to normal face logic).
- **Why**: Otto's face was driven entirely by animation state (transfers active → focus, idle → random expressions, long idle → sleepy). Terminal runs had no transfers, so Otto showed normal/sleepy face regardless of whether the run succeeded or crashed. The persistent `mood_face` override lets the orchestrator adapter set a lasting visual state that matches the narrative text. It's intentionally the highest priority in face resolution — a crashed run should always show dead eyes, even during a reactive face from a stale transfer animation completing. The `set_mood(None)` call when entering "running" state ensures the mood clears when a paused run is resumed.
- **Discovered**: Feb 14, 2026
- **Location**: `scripts/tui/widgets/otto_widget.py` - `INNER_DEAD`, `OttoState.mood_face`, `OttoWidget.set_mood()`; `scripts/tui/widgets/otto_orchestrator.py` - `update_narrative()` mood assignments

### 40. LLMs Double-Wrap Responses in JSON String with Markdown Fences
- **What**: Added `_unwrap_response()` to `schema_validator.py`. Detects when required top-level keys are missing but a `response` key exists as a string. Strips markdown code fences (`\`\`\`json ... \`\`\``), parses the inner JSON, and merges keys into the top-level object.
- **Why**: Gemini frequently wraps its actual JSON output inside `{"response": "\`\`\`json\n{...}\n\`\`\`"}`. This is a generic LLM behavior (not model-specific), so the fix is in the schema validator (the boundary where we accept LLM output). The detection is conservative: only triggers when a required key is actually missing AND the unwrapped inner JSON resolves the missing key. Safe to apply generically — doesn't modify data that already has its required keys.
- **Discovered**: Feb 14, 2026
- **Location**: `scripts/schema_validator.py` - `_unwrap_response()`, called from `validate_line()` between JSON parse and type coercion

### 41. LLMs Return Strings Where Arrays Are Expected — Generic String→Array Coercion
- **What**: Added string→array coercion in `coerce_data()`. When schema expects `type: array` but value is a string: (1) try `json.loads()` — handles cases where the LLM returned a JSON array as a string, (2) if items schema has a `tag` property, wrap as `[{"tag": value}]`, (3) generic fallback: wrap as `[value]`. Parsed arrays are recursively coerced.
- **Why**: LLMs sometimes return a bare string (`"SURRENDER"`) instead of the expected array (`[{"tag": "SURRENDER"}]`). The two-step approach (parse first, wrap second) handles both JSON-string-that-happens-to-be-an-array and bare-value-that-should-be-a-single-element-array. The `tag` property special case handles the common pattern where an enum tag is expected as an array of tagged objects.
- **Discovered**: Feb 14, 2026
- **Location**: `scripts/schema_validator.py` - `coerce_data()`, string→array section

### 42. LLMs Embellish Enum Values — Generic Enum Normalization
- **What**: Added enum normalization in `coerce_data()`. When a string value doesn't match any enum option: strip `"shadow "` prefix (case-insensitive), split on `" | "` delimiter (take first part), strip whitespace, lowercase, then re-check against lowercased enum values. If a match is found, coerce to the canonical enum value.
- **Why**: LLMs (especially Gemini) embellish enum values with prefixes (`"shadow integration"` → `"integration"`), compound values (`"integration | fragmentation"` → `"integration"`), or casing differences. These are semantically correct but fail strict enum validation. Normalizing at the schema boundary catches this generically for any enum field without requiring per-field rules. The normalization order matters: prefix stripping before delimiter splitting ensures `"shadow integration | fragmentation"` correctly yields `"integration"`.
- **Discovered**: Feb 14, 2026
- **Location**: `scripts/schema_validator.py` - `coerce_data()`, enum normalization section

### 43. Remove LLM Arithmetic Validation Rules — Downstream Should Compute
- **What**: Removed `all_net_sums_computed` (and previously `all_wound_counts_valid`) from Tarot `score_wounds` validation. These rules compared LLM-declared derived values (e.g., `net_sum` = sum of `impact_scores`) against computed values.
- **Why**: LLMs are unreliable at arithmetic — they frequently miscalculate sums, counts, and derived values even when the underlying data is correct. Validating LLM-declared arithmetic creates failures for data that is otherwise usable. The correct pattern: accept the LLM's raw data (individual scores, impacts, etc.) and compute derived values downstream. This also avoids the type coercion gap (Learning #38) where coerced types in stage 1 are lost by stage 2.
- **Discovered**: Feb 14, 2026
- **Location**: `pipelines/Tarot/config.yaml` - removed `all_net_sums_computed` and `all_wound_counts_valid` rules from `score_wounds` validation

### 39. Initialize Derived State Before First Render — Avoid Flash of Wrong Content
- **What**: When a widget's rendering depends on cached/derived state (like `_last_manifest_status`), that state must be initialized BEFORE the first render call in `on_mount()`. Otherwise the first frame briefly shows wrong content until an async timer sets the correct state.
- **Why**: `_count_step_failures()` used `getattr(self, '_last_manifest_status', None) == "running"` to decide if failures are "retrying" (active auto-retry) vs "exhausted" (needs manual intervention). On mount, `_last_manifest_status` was uninitialized (set only by `_diff_chunk_states()` on the 2s refresh tick). So `is_running` evaluated to `False`, causing all failures to flash as "exhausted" for ~0.5 seconds before correcting to "retrying." Fix: read the manifest and set `_last_manifest_status` at the top of `on_mount()`, before `_update_pipeline_panel()` and other render calls. General principle: any state read during rendering must be initialized before the first render.
- **Discovered**: Feb 14, 2026
- **Location**: `scripts/tui/screens/main_screen.py` - `on_mount()`, `_count_step_failures()`

### 32. Retry State Visibility — Distinguish Retrying vs Exhausted in TUI
- **What**: Validation failures are now split into "retrying" (orchestrator will auto-retry) and "exhausted" (max retries reached, needs manual `R` key). Uses `retry_count` from each failure record in `{step}_failures.jsonl` compared against `max_retries` from config. All failures treated as exhausted when orchestrator is not running.
- **Why**: Users couldn't tell at a glance whether the orchestrator was still auto-retrying failures or whether manual intervention was needed. Showing "⚠ 12 valid. fail" gave no indication of retry progress. The fix reads per-failure `retry_count` and compares against `max_retries` (cached from config snapshot). Running state is checked cheaply via `_last_manifest_status` (already tracked) rather than expensive `get_process_health()` calls. Pipeline boxes, sidebar stats, and unit table all show the distinction with different symbols (↻ yellow for retrying, ⚠ dark_orange for exhausted, ✗ red for hard failures).
- **Discovered**: Feb 14, 2026
- **Location**: `scripts/tui/screens/main_screen.py` - `_count_step_failures()`, `_get_max_retries()`, `_update_run_stats_panel()`, unit table rendering; `scripts/tui/widgets/pipeline_view.py` - `render_pipeline_boxes()` failure rows

### 49. Fan-Out Pipelines Multiply Cost Non-Obviously — Estimate Before Committing
- **What**: A 4-step pipeline with fan-out produced 51,712 child units from 5,281 parents (~10x expansion). The refine and score steps each process all 51,712 units at ~$14/1K output tokens (2.5 Pro batch). Total cost: ~$1,856 vs naively expected ~$100.
- **Why**: Cost estimation that only counts top-level items misses the fan-out multiplier entirely. For Elena: 5,281 generate calls → fan-out to 51,712 → 51,712 refine calls → 51,712 score calls = ~108K total API calls. The generate step was only 6% of total cost. Pre-run cost estimation MUST account for fan-out expansion by sampling the fan-out field from items to estimate the multiplier. Additionally, the TUI's `_calculate_cost_from_manifest()` used hardcoded Flash pricing ($0.30/M output) as fallback instead of the actual Pro pricing ($5.00/M output) — a 17x undercount that masked the true cost during the run.
- **Discovered**: Mar 21, 2026
- **Location**: `scripts/tui/screens/main_screen.py` - `_calculate_cost_from_manifest()`, `scripts/orchestrate.py` - `init_run()` (no pre-run estimate exists)

### 44. TUI Status Guards Must Include "stuck" for Pause/Kill Actions
- **What**: The TUI's pause and kill action handlers check manifest status against an allowlist (e.g., `status == "running"`). "stuck" is excluded, preventing users from managing rate-limited runs.
- **Why**: "stuck" status means the orchestrator detected errors (e.g., sustained 429 rate limits) but the PID is still active. The user needs to pause or kill to regain control, but the status guard blocks it. The same PID-based pause/kill logic that works for "running" applies equally to "stuck" — the difference is cosmetic, not functional.
- **Discovered**: Mar 21, 2026
- **Location**: `scripts/tui/screens/home_screen.py` — pause/kill action handlers

### 45. Large YAML Item Files Cause Slow Pipeline Init — Consider JSON Alternative
- **What**: A 48MB `items.yaml` (9,240 triples with full card definitions) takes significant time to parse during pipeline initialization. JSON would be much faster.
- **Why**: PyYAML's safe_load is a pure-Python parser that scales poorly with file size. JSON parsing (via C-optimized `json` module) is typically 10-50x faster for the same data. Since items files are machine-generated by ETL scripts (not hand-edited), YAML's readability advantage is minimal. Supporting `items.json` alongside `items.yaml` in `generate_units.py` would let ETL scripts choose the faster format.
- **Discovered**: Mar 21, 2026
- **Location**: `scripts/generate_units.py` — items file loading

### 46. asteval Does Not Support Generator Expressions — Use List Comprehensions
- **What**: Validation rules using `set(s['field'] for s in items)` fail with `NotImplementedError` in asteval. Must use `set([s['field'] for s in items])` instead.
- **Why**: asteval's safe expression evaluator intentionally omits generator expression support. List comprehensions are supported and functionally equivalent for the small collections used in validation rules. This applies to all validation `expr` fields in pipeline `config.yaml` files.
- **Discovered**: Mar 21, 2026
- **Location**: `pipelines/TarotEnhance/config.yaml` — validation rules `unique_registers` and `unique_thematic_tags`

### 47. ETL Scripts Must Handle Multiple Input Formats — Defensive Parsing
- **What**: `extract_triples.py` originally assumed `.json.gz` files with string card names and story-nested coherence data. Real output had plain `.json`, embedded card dicts, and top-level score arrays.
- **Why**: Pipeline output format evolves as the pipeline matures. ETL scripts bridging pipeline output into downstream pipelines must handle format variations defensively: check `isinstance()` before assuming string vs dict, prefer top-level arrays with fallback to nested data, glob for multiple file extensions. The alternative — fixing upstream output format — is impractical when processing historical runs.
- **Discovered**: Mar 21, 2026
- **Location**: `scripts/extract_triples.py` — `resolve_card()`, `extract_triple()`, file discovery patterns

### 48. Flash Models Inadequate for Complex Prompt Templates — Validate Before Full Runs
- **What**: Gemini 2.0 Flash achieved only 10.4% validation pass rate on TarotEnhance (931/8,932) vs 100% for 2.5-pro (5/5 test). Primary failure: empty `journal_entry` fields.
- **Why**: Complex prompts with multiple output fields, style constraints, and cross-field dependencies exceed flash-tier models' instruction following. The cost savings (~20x cheaper) are negated by retry volume and poor output quality. Always run a small test batch (5-10 units) with the target model before committing to a full run. This also validates schema constraints — the test run revealed `journal_entry.maxLength: 200` was too tight (bumped to 400).
- **Discovered**: Mar 21, 2026
- **Location**: `pipelines/TarotEnhance/` — observed during flash test run `enhance_flash_full`

## Active Runs

### elena_full_26pro_v2
- **Pipeline**: ElenaDialogue (4-step: generate → expand_lines → refine → score)
- **Provider**: Gemini 2.5 Pro (batch mode)
- **Status**: Stuck — sustained 429 RESOURCE_EXHAUSTED rate limits
- **PID**: 67922 (may need kill + restart)
- **Notes**: Improvements over v1: model upgrade to 2.5-pro, bug fixes (Jinja2 None handling, repair_run richest-record-wins)

### enhance_pro
- **Pipeline**: TarotEnhance (single-step: enhance)
- **Provider**: Gemini 2.5 Pro (batch mode)
- **Items**: 9,240 Major Arcana triples (22×21×20)
- **Status**: Stuck — sustained 429 RESOURCE_EXHAUSTED rate limits (concurrent with elena_full_26pro_v2)
- **PID**: 86415 (may need kill + restart)
- **Notes**: Both runs hit rate limits simultaneously. Kill both, restart one at a time. Estimated cost: ~$102 batch mode.

## Key Files Reference
- **Orchestrator**: `scripts/orchestrate.py`
- **TUI App**: `scripts/tui/app.py`
- **Home Screen**: `scripts/tui/screens/home_screen.py`
- **Main/Detail Screen**: `scripts/tui/screens/main_screen.py`
- **Splash Screen**: `scripts/tui/screens/splash_screen.py`
- **Run Utilities**: `scripts/tui/utils/runs.py`
- **Pipeline configs**: `pipelines/*/config.yaml`
- **Data loading**: `scripts/tui/data.py`
- **Pipeline visualization**: `scripts/tui/widgets/pipeline_view.py`
- **Otto Widget**: `scripts/tui/widgets/otto_widget.py`
- **Otto Orchestrator**: `scripts/tui/widgets/otto_orchestrator.py`

## Session Continuity Instructions

**Before ending a session or when making significant progress, UPDATE THIS FILE:**

1. Update "Last Updated" with date and description
2. Move completed items from "Current Focus" to "Recently Completed"
3. Update "Active Bugs" with any new findings
4. Add any new "Key Technical Learnings" (always include the WHY)
5. Update "Active Runs" status

**IMPORTANT:** When making significant architecture changes, also update `ai_context/PROJECT_CONTEXT.md`:
- New screens → Update "System Architecture" section
- New orchestrator features → Update "The Orchestrator" section
- New file structures → Update "Folder Structure Overview"
- New design decisions → Add to "Key Design Decisions"

This ensures the next session can pick up seamlessly.

---

## File Maintenance Guidelines

### DEVELOPMENT_CONTEXT.md (This File)
**Update frequency:** Every session or after significant progress
**What to update:**
- "Last Updated" timestamp and description
- Move completed items to "Recently Completed"
- Update "Current Focus" with next priorities
- Add new bugs to "Active Bugs" with findings
- Add new learnings to "Key Technical Learnings" (always include WHY)
- Update "Active Runs" status

### ai_context/PROJECT_CONTEXT.md
**Update frequency:** When architecture changes (weekly review recommended)
**What to update:**
- New screens or major UI changes
- New execution modes or orchestrator features
- Changes to file/folder structure
- New design patterns or decisions
- Update "Current State" section with recent implementations

### Component CONTEXT.md Files
**Update frequency:** When that component's API or structure changes significantly
**Files:** `scripts/CONTEXT.md`, `scripts/tui/CONTEXT.md`, `pipelines/CONTEXT.md`
