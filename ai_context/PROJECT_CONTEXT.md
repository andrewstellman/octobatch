# Octobatch: Project Context & Architecture
> **File:** `ai_context/PROJECT_CONTEXT.md`
> **Scope:** This file documents stable architecture and design patterns.
> **Version:** v1.0
> For current work-in-progress, see `ai_context/DEVELOPMENT_CONTEXT.md`. For priorities, ask the user.

**Octobatch** is a local, open-source batch orchestration tool designed to manage multi-phase LLM workflows. It supports three providers (Gemini, OpenAI, Anthropic) with both batch and realtime execution modes, providing robust management for rate limiting, crash recovery, chunking, validation, and pipeline orchestration.

### Provider Abstraction Layer

Octobatch uses a centralized model registry (`scripts/providers/models.yaml`) for all provider and pricing information:

- **Supported providers:** Gemini, OpenAI, Anthropic (all with batch API support)
- **Pricing is registry-driven:** Pipeline configs do NOT contain pricing blocks. All pricing comes from the registry.
- **Provider/model resolution:** CLI flags > config values > registry `default_model`
- **Provider-agnostic pipelines:** Omit `api.provider` and `api.model` from config to require them at runtime
- **Maintenance:** Use `scripts/maintenance/update_models.py` to refresh pricing from provider APIs

## 1. Core Philosophy

### Unix-Pipe Architecture
Data flows between steps via standard I/O. A step reads JSONL from `stdin`, processes it (e.g., calls an LLM API), and writes results to `stdout`. This enables composable, testable pipeline steps.

### Declarative Configuration
Pipelines are defined in `config.yaml` within each pipeline folder. This file controls the flow, references Jinja2 templates, defines validation rules, and sets processing parameters.

### "Docs + Claude Code" Workflow
The TUI is not a full text editor. It is a **Context Bridge**. Users browse and visualize pipelines in the TUI, select a step, and use the **Copy Context (C)** feature to export the configuration + template to their clipboard. They then paste this into Claude Code (or another AI coding assistant) to perform edits. This avoids the complexity of building a full in-TUI editor while leveraging AI for sophisticated changes.

### Manifest as Source of Truth
All run state is persisted in `MANIFEST.json`. The orchestrator reads and writes this file atomically. If a process crashes, the manifest accurately reflects the last known state, enabling clean recovery.

## 2. System Architecture

### The Orchestrator (`scripts/orchestrate.py`)

The engine of the system. It runs as a subprocess and manages the lifecycle of a run.

**State Management:**
- Uses `MANIFEST.json` to track the state of every chunk
- Chunk states: `PENDING` → `SUBMITTED` → `COMPLETE` → `VALIDATED` (or `FAILED`)
- Run status field: `running`, `paused`, `failed`, `complete`

**Crash Recovery & Signal Handling:**
- Writes state atomically (write to .tmp, then rename)
- `KeyboardInterrupt` (Ctrl+C): Sets status to `paused`, exits cleanly with code 130
- Unhandled exceptions: Sets status to `failed`, logs traceback, records error message
- Successful completion: Sets status to `complete`

**Execution Modes:**
- **Batch Mode (`--watch`):** Submits jobs to batch API, polls for completion at intervals. Cost-effective for large workloads.
- **Realtime Mode (`--realtime`):** Executes requests synchronously. Faster for testing/debugging, costs ~2x batch rate.

**Key Flags:**
- `--init`: Initialize a new run (create directory structure, chunk input data)
- `--watch`: Poll batch API until completion (mutually exclusive with `--init`)
- `--realtime`: Run synchronously (can combine with `--init`)
- `--max-units N`: Process only N units (for testing)
- `--retry-failures`: Create retry batches for failed items
- `--provider`: Override provider (gemini/openai/anthropic)
- `--model`: Override model identifier
- `--quiet`: Suppress console output (log files still written)

### The TUI (`scripts/tui/`)

A Terminal User Interface built with the **Textual** framework. Provides visualization and control without requiring users to memorize CLI commands.

**Key Screens:**

1. **Home Screen (Dashboard)**
   - Stats cards: Total Runs, Total Tokens, Total Cost, Pipelines
   - Active Runs: Cards with progress bars, spinners, status indicators
   - Recent Runs: DataTable with completion status
   - Visual states: Active (green spinner), Detached (yellow ⚠), Failed (red ✗), Complete (green ✓), Complete ⚠ (yellow — has validation failures), Paused (⏸)
   - Failed column color: yellow for complete runs (validation failures), red for failed runs (hard failures)

2. **Run Detail (Main Screen)**
   - Pipeline visualization: Horizontal boxes showing step flow
   - Chunk/unit drill-down: See individual items and their status
   - Stats panel: Cost, tokens, duration, mode, validation failures (yellow), hard failures (red)

3. **Pipeline Editor**
   - Split-panel layout (top: visualization, bottom: details)
   - Navigate steps with ←→ arrows
   - Navigate properties with ↑↓ arrows
   - Copy Context (C): Export step config + template to clipboard
   - View Template (V): Read-only template viewer

4. **Launchpad (New Run Modal)**
   - Pipeline selector (populated from `pipelines/` folder)
   - Provider/model dropdowns (sorted by cost, "Use Pipeline Config" default)
   - Mode selector: Batch vs Realtime
   - Max Units input: For testing with subset of data
   - Two-step launch for batch mode: `--init` first, then `--watch`
   - Scrollable form for short terminals

5. **Splash Screen**
   - Toast-styled bottom-right overlay with animated Otto
   - Auto-dismisses after 10 seconds; close with Escape or X button
   - Triggered from `OctobatchApp.on_mount()` after main screen push

6. **Otto the Octopus Widget**
   - Animated mascot in detail screen's right sidebar (responsive: hidden on narrow terminals)
   - OttoOrchestrator bridges pipeline events (chunk advance/retry/complete) to animations
   - Tentacle transfers with per-run color coding, flag wave on run completion

**The "Resume" Model:**

When the TUI starts, it may find runs that are not in a terminal state but have no active subprocess (e.g., TUI was closed, computer restarted). These are marked as "Detached" rather than assumed failed, because:
- Batch jobs run on remote servers and may still be processing
- The local process dying doesn't mean the remote job died

Users can press `R` to Resume a detached run, which re-launches `--watch`. The watcher will either:
- Resume monitoring if the batch is still running
- Detect completion/failure and update the manifest accordingly

**Archive/Unarchive:**
- `X` key archives a terminal run (moves directory to `runs/_archive/`)
- `H` key toggles visibility of archived runs on the home screen
- Only terminal runs (complete/failed) can be archived — running runs are rejected with a safety check
- `ArchiveConfirmModal` requires confirmation before archiving
- `scan_runs(include_archived=True)` includes archived runs in the scan

### Pipeline Configuration (`pipelines/`)

Each pipeline lives in its own folder with a standard structure:

```
pipelines/
  MyPipeline/
    config.yaml        # Pipeline definition
    items.yaml         # Input data (or items.jsonl)
    templates/         # Jinja2 prompt templates
      generate.jinja2
      score.jinja2
    schemas/           # JSON schemas for validation
      output.json
```

**config.yaml structure:**
```yaml
name: MyPipeline
items_source: items.yaml
chunk_size: 100
max_retries: 5

pipeline:
  - name: generate
    template: generate.jinja2
    output_schema: output.json
    
  - name: score
    template: score.jinja2
    output_schema: score.json
```

## 3. Execution Lifecycle

### Creating & Editing Pipelines

1. **Define:** Create a folder in `pipelines/` with config.yaml, templates, and schemas
2. **Browse:** Open the TUI, press `P` to view Pipeline Configurations
3. **Edit:** Select a pipeline, navigate to a step, press `C` to copy context
4. **Refine:** Paste into Claude Code, ask for changes (e.g., "Add a confidence score field")
5. **Save:** Claude Code modifies the template/config files directly

### Running a Pipeline

1. **Launch:** Press `N` in TUI, select pipeline, choose mode, set max units (optional)
2. **Init:** System runs `orchestrate.py --init` to create run directory and chunk data
3. **Execute:** System spawns `orchestrate.py --watch` (batch) or completes with `--realtime`
4. **Monitor:** TUI polls `MANIFEST.json` to update progress display
5. **Resume:** If disconnected, detached runs can be resumed with `R` key

### Run Directory Structure

```
runs/
  my_run_20260122_143052/
    MANIFEST.json       # Source of truth for run state
    RUN_LOG.txt         # Human-readable execution log
    TRACE_LOG.txt       # Per-request telemetry (API calls, batch ops)
    orchestrator.pid    # PID file (persists after exit for dead-process detection)
    orchestrator.log    # Subprocess stdout/stderr
    config.yaml         # Copy of pipeline config used
    
    chunk_000/
      input.jsonl           # Input items for this chunk
      generate_results.jsonl    # Output from generate step
      generate_failures.jsonl   # Failed items from generate
      score_results.jsonl       # Output from score step
      final_validated.jsonl     # Items that passed all validation
```

### MANIFEST.json Structure

```json
{
  "config": "pipelines/MyPipeline/config.yaml",
  "created": "2026-01-22T14:30:00Z",
  "updated": "2026-01-22T15:45:00Z",
  "status": "running",
  "pipeline": ["generate", "score"],
  "metadata": {
    "mode": "batch",
    "max_units": null
  },
  "chunks": {
    "chunk_000": {
      "state": "score_SUBMITTED",
      "batch_id": "batch_abc123",
      "items": 100,
      "valid": 0,
      "failed": 0,
      "retries": 0
    }
  }
}
```

**Status values:**
- `running`: Orchestrator is actively processing
- `paused`: User interrupted with Ctrl+C
- `failed`: Unhandled exception occurred
- `complete`: All chunks reached terminal state

## 4. Key Design Decisions

### Why subprocess.Popen for run execution?
Keeps the TUI responsive. Long-running orchestration happens in a separate process, and the TUI simply polls the manifest for updates.

### Why two-step launch for batch mode?
The `--init` and `--watch` flags are mutually exclusive in the argument parser. Batch mode requires init first (blocking), then watch (background).

### Why CSS borders instead of ASCII art?
Textual's CSS system handles responsive layouts properly. ASCII art breaks at different terminal widths.

### Why "Detached" instead of assuming "Failed"?
Batch jobs run on remote servers. Closing your laptop doesn't kill a Google Batch job. The run might still be processing successfully.

### Why Copy Context instead of in-TUI editing?
Building a full text editor is complex. Leveraging Claude Code (or similar) for edits is more powerful and flexible. The TUI's job is visualization and context export.

## 5. Folder Structure Overview

```
project-root/
├── ai_context/
│   └── PROJECT_CONTEXT.md  # This file
├── scripts/
│   ├── orchestrate.py      # Main orchestration engine
│   ├── octobatch_step.py   # Generic step executor
│   ├── realtime_provider.py # Synchronous API calls
│   └── tui/
│       ├── app.py          # Textual App class
│       ├── tui.py          # Entry point
│       ├── screens/        # Screen classes
│       │   ├── home_screen.py
│       │   ├── main_screen.py
│       │   ├── new_run_modal.py
│       │   └── splash_screen.py
│       ├── widgets/        # Reusable widget components
│       │   ├── otto_widget.py      # Animated Otto mascot
│       │   ├── otto_orchestrator.py # Pipeline→animation adapter
│       │   ├── pipeline_view.py    # Pipeline step boxes
│       │   ├── stats_panel.py      # Stats panel widgets
│       │   └── progress_bar.py     # Progress bar widget
│       ├── config_editor/  # Pipeline editor
│       │   ├── list_screen.py
│       │   ├── edit_screen.py
│       │   └── models.py
│       └── utils/          # Helpers
│           ├── runs.py
│           ├── pipelines.py
│           └── formatting.py
├── pipelines/              # Pipeline configurations
│   ├── ExamplePipeline/
│   │   ├── config.yaml
│   │   ├── templates/
│   │   └── schemas/
├── runs/                   # Execution artifacts
│   ├── run_name/
│   │   ├── MANIFEST.json
│   │   └── chunk_*/
│   └── _archive/           # Archived runs (moved via TUI X key)
├── tests/
│   ├── code_reviews/       # Code review reports (Claude, GPT5)
│   ├── RUN_CODE_REVIEW.md  # How to run code reviews
│   ├── RUN_REGRESSION_TESTS.md  # How to run regression tests
│   └── README.md           # Test documentation
└── docs/
    └── prompts/            # Claude Code prompt templates
```

## 6. Development Patterns

### Adding a New Pipeline Step
1. Add step definition to `config.yaml`
2. Create Jinja2 template in `templates/`
3. Create JSON schema in `schemas/` (optional but recommended)
4. Test with `--realtime --max-units 3`

### Debugging a Failed Run
1. Check `orchestrator.log` for subprocess output
2. Check `RUN_LOG.txt` for orchestrator logs
3. Check `TRACE_LOG.txt` for per-request telemetry (API call durations, status codes)
4. Check `MANIFEST.json` for `error_message` field and `metadata.pid`
5. Look at `*_failures.jsonl` files for individual item errors
6. Send `kill -USR1 <pid>` to dump stack trace to RUN_LOG.txt (for hung processes)

### Testing Changes
- Use `--realtime --max-units 3` for fast iteration
- Batch mode is for production runs, not debugging
- The TUI's Resume feature helps recover from interruptions

## 7. Current State

### Dead Code Removed (Feb 24)
- `scripts/tui/widgets.py` — shadowed by `widgets/` package, unreachable
- `scripts/tui/styles.py` — CSS constants never imported by any screen
- `scripts/find_and_mark_missing.py` — superseded by `--verify`/`--repair` CLI tools
- `scripts/generate_report.py` — run-scope step not configured in any pipeline

### Recently Implemented
- **Archive/Unarchive:** `X` key archives terminal runs to `runs/_archive/`, `H` toggles archived visibility, `ArchiveConfirmModal` for confirmation, `scan_runs(include_archived)` parameter
- **Pipeline funnel display:** Each step shows per-step throughput (Valid/Input) instead of global progress; `_count_step_valid()` scans `{step}_validated.jsonl` on disk; Step 1 input = total_units, Step N input = valid from Step N-1; flag emoji uses funnel data
- **Retry validation failures (.bak mechanism):** `retry_validation_failures()` archives validation failures to `.bak`, preserves hard failures, resets chunk state to PENDING; `.bak` signals idempotency check to skip 90% fallback; applied to both `realtime_run()` and `watch_run()` early terminal checks; TUI `reset_unit_retries()` also creates `.bak`
- **Otto status narrator:** `OttoOrchestrator.update_narrative()` called from `_diff_chunk_states()` every refresh; extracts providers from config; shows "Waiting for Gemini..." / "Waiting for Claude..." / "Otto is orchestrating..."
- **Splash screen key passthrough:** Keys typed during splash pass through to underlying HomeScreen; transparent ModalScreen overlay
- **Validation vs hard failure differentiation:** `categorize_step_failures()` categorizes by `failure_stage` (schema_validation/validation → retryable yellow, pipeline_internal → hard red); pipeline boxes show "⚠ N valid. fail" (yellow) and "✗ N failed" (red); sidebar stats split into Validation/Failed; retry (R key) targets validation failures only; home screen shows "complete ⚠ (N)" for runs with validation failures
- **Per-step provider/model overrides:** Steps can specify `provider` and/or `model`; resolution: CLI > Step > Global; `get_step_provider()` with CLI override tracking via manifest metadata
- **Standardized on GOOGLE_API_KEY:** Removed all GEMINI_API_KEY references; matches Google's official documentation
- **Quiet tick logging:** Poll deduplication via `.poll_status_cache.json`; TICK summary suppressed when nothing changes; 60s heartbeat
- **Otto the Octopus mascot:** Animated widget with tentacle transfers, expressions, body sway, bubbles; OttoOrchestrator bridges pipeline events to animations; splash screen overlay on startup
- **Scrollable stats sidebar:** Stats panels in VerticalScroll, Otto fixed above
- **Batch pipeline boxes:** Show "📤 N processing" / "⏳ N pending" instead of "● 0/3" for batch mode steps
- **Batch patience toast:** Provider-aware reassuring toast when batch idle >60 seconds
- **Progress/ETA fix:** Complete runs show "100% / Complete"; failed runs show "N% / Failed"
- **Recent Activity refresh fix:** Restart refresh loop on retry/resume
- **Log viewer key bindings:** S=save, C=copy, P=copy path in LogModal
- **mark_run_complete fix:** Allows failed→complete transition when all chunks terminal
- **SIGPIPE/BrokenPipeError handling:** `signal.signal(SIGPIPE, SIG_DFL)` + BrokenPipeError catch prevents healthy runs from being marked failed
- **Manifest consistency auto-fix:** TUI detects and corrects stale manifest status on 5s tick
- **TUI "Use Pipeline Config" default:** New run modal defaults to pipeline config, not forcing a provider
- **Schema fixes:** Added missing enum values, new arc types, increased field maxLength, prompt template updates for direction/count field constraints
- **Coherence schema:** Required vocabulary_issues, turning_point_issues, path_mismatch_details fields
- **Looping expression steps:** `loop_until` enables iterative simulations (deal cards until bust, run until convergence)
- **Expression-only steps:** `scope: expression` steps perform local computation without LLM calls
- **Expression steps in batch mode:** `tick_run` now handles expression steps locally before API submission
- **v1.0 Multi-provider support:** Gemini, OpenAI, Anthropic all fully supported with batch APIs
- **Registry-driven pricing:** All pricing from `scripts/providers/models.yaml`, no pricing in pipeline configs
- **Gzip post-processing:** Built-in `type: gzip` post-process step for compression
- **Failure inspection tooling:** `raw_response` capture in failure records, UnitDetailModal with T/R/L view modes
- **TUI Auto-refresh:** Fixed using recursive `set_timer()` pattern (not `set_interval()`)
- **Pause functionality:** Transition state with polling to prevent zombie flicker
- Crash handling with status lifecycle (running/paused/failed/complete)
- Detached run detection and Resume capability
- Two-step batch launch (--init then --watch)
- Split-panel pipeline editor with Copy Context
- Home screen with Active/Recent runs sections
- Live progress logging with LogTicker widget

### Known Limitations
- No in-TUI text editing (by design - use Claude Code)
- No pause/resume for individual chunks (only full runs)
- Funnel display scans validated files on disk each refresh (acceptable for <50 chunks; may need caching for very large runs)

### Key Technical Learnings
- `set_interval()` doesn't work reliably on pushed Textual screens - use recursive `set_timer()`
- Subprocess pipes can deadlock on large data - use threading for concurrent read/write
- Always check `get_process_health()` before modifying manifest or spawning orchestrator

### Future Directions
See planning sessions in Claude.ai for current priorities and roadmap.
