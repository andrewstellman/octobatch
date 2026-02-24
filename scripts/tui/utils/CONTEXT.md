# Utils Directory Context
> **File:** `scripts/tui/utils/CONTEXT.md`

## 1. Purpose

This directory contains pure utility functions for the Octobatch TUI. All functions are designed to be:
- **Pure**: No side effects, no UI dependencies
- **Testable**: Can be unit tested independently
- **Reusable**: Used across multiple screens and components

## 2. Key Components

### runs.py (Run Discovery & Status - ~1,500 lines)
**Primary Functions:**

| Function | Purpose |
|----------|---------|
| `get_runs_dir()` | Get runs directory path relative to project root |
| `load_manifest(run_path)` | Load MANIFEST.json from a run directory |
| `scan_runs(runs_dir)` | **Main function**: Scan all runs, return rich data. Prefers `.manifest_summary.json` (fast path) over full `MANIFEST.json` |
| `_build_run_data_from_summary()` | Build run data dict from lightweight summary file |
| `_build_run_data_from_manifest()` | Build run data dict from full manifest (fallback) |
| `get_active_runs(runs_dir)` | Filter runs with status in {active, detached, paused} |
| `get_recent_runs(runs_dir, limit)` | Get recent terminal/idle status runs |
| `calculate_dashboard_stats(runs, pipeline_count)` | Aggregate stats for dashboard |

**Status Functions:**

| Function | Purpose |
|----------|---------|
| `get_run_status(manifest)` | Determine status: complete/failed/paused/detached/active/pending |
| `get_enhanced_run_status(run_dir, base_status)` | Enhance status with process state (stuck/zombie) |
| `get_run_progress(manifest)` | Calculate progress percentage (0-100) |
| `get_run_tokens(manifest)` | Total tokens (initial + retry) |
| `get_run_cost_value(manifest)` | Calculate cost using Gemini pricing |
| `get_run_cost(manifest)` | Formatted cost string ("$0.0100") |
| `get_run_mode(manifest)` | Get mode: "batch" or "realtime" |
| `get_run_error_message(manifest)` | Get error message if run failed |

**Process Management Functions:**

| Function | Purpose |
|----------|---------|
| `get_run_process_status(run_dir)` | Get PID and alive status from PID file or discovery |
| `get_process_diagnostics(run_dir)` | Full diagnostics: PID, CPU, memory, cmdline, logs |
| `has_recent_errors(run_dir)` | Check if RUN_LOG.txt has recent ERROR entries |
| `kill_run_process(run_dir)` | Send SIGTERM to orchestrator process |
| `mark_run_as_failed(run_dir, error_message)` | Update manifest to mark run as failed (PID file persists) |
| `mark_run_as_killed(run_dir)` | Update manifest to mark run as killed (PID file persists) |
| `get_batch_timing(run_dir, poll_interval)` | Get last/next tick times for batch mode display |
| `load_manifest(run_path)` | Load MANIFEST.json from a run directory |

**Batch Timing Function:**
```python
def get_batch_timing(run_dir: Path, poll_interval: int = 30) -> dict:
    """Returns dict with:
    - last_tick: datetime of last TICK entry in log
    - next_tick: expected datetime of next tick
    - last_tick_ago: human-readable "5s" or "2m 30s"
    - next_tick_in: human-readable time until next tick
    """
```

**Status Determination Logic:**
```python
def get_run_status(manifest):
    # 1. Check explicit status field
    if manifest.get("status") in ("complete", "failed", "paused"):
        return manifest["status"]

    # 2. Infer from chunk states
    chunks = manifest.get("chunks", {})
    validated = count where state == "VALIDATED"
    failed = count where state == "FAILED"

    if validated == total:
        return "complete"
    elif failed > 0:
        return "failed"
    elif all pending:
        return "pending"
    elif manifest.get("status") == "running":
        return "active"
    else:
        return "detached"  # No explicit running status
```

### pipelines.py (Pipeline Discovery - ~250 lines)
**Primary Functions:**

| Function | Purpose |
|----------|---------|
| `get_pipelines_dir()` | Get pipelines directory path |
| `scan_pipelines()` | **Main function**: Scan all pipelines, return metadata |
| `load_pipeline_config(name)` | Load full YAML config for a pipeline |
| `get_pipeline_path(name)` | Get path to pipeline directory |
| `list_pipeline_names()` | Get list of available pipeline names |

**Helper Functions:**

| Function | Purpose |
|----------|---------|
| `get_step_names(steps)` | Extract step names, excluding run-scope |
| `filter_chunks_for_step(chunks, step_index, pipeline)` | Filter chunks at or past step |
| `calculate_pipeline_progress(steps)` | Calculate overall progress ratio |
| `get_step_by_name(steps, name)` | Find step by name |
| `get_step_by_index(steps, index)` | Get step by index with bounds check |

### formatting.py (Display Formatting - ~200 lines)
**Constants:**
```python
BLOCK_FULL = "█"   # Full block for progress bars
BLOCK_EMPTY = "░"  # Light shade for empty progress
```

**Formatting Functions:**

| Function | Example Output |
|----------|----------------|
| `format_cost(cost)` | "$0.0100" or "--" |
| `format_tokens(tokens)` | "100,332" or "--" |
| `format_duration(seconds)` | "1:23:45" or "2:13" |
| `format_count(current, total)` | "4/6" |
| `format_percent(current, total)` | "67%" |
| `format_progress_bar(percent, width)` | "█████░░░░░" |
| `truncate_string(s, max_len, suffix)` | "long_name..." |
| `pad_string(s, width, align)` | "  text  " |

**Progress Bar Example:**
```python
format_progress_bar(67, width=10)  # "██████░░░░"
format_progress_bar_from_counts(4, 6, width=10)  # "██████░░░░"
```

### status.py (Status Symbols & Colors - ~230 lines)
**Constants:**
```python
CHECK = "✓"
CROSS = "✗"
CIRCLE_FILLED = "●"
CIRCLE_EMPTY = "○"
```

**Symbol Functions:**

| Function | Purpose |
|----------|---------|
| `get_status_symbol(status)` | Get Unicode symbol for status |
| `get_status_color(status)` | Get color name for status |

**Status Symbol Mapping:**
```python
status → symbol, color
"complete" → ✓, green
"active" → ●, green
"failed" → ✗, red
"detached" → ⚠, yellow  # (defined elsewhere)
"paused" → ⏸, cyan      # (defined elsewhere)
"pending" → ○, dim
```

**State Parsing Functions:**

| Function | Purpose |
|----------|---------|
| `parse_chunk_state(state, pipeline)` | Parse "step_STATUS" → (step, status, index) |
| `determine_step_status(step_index, chunk_states)` | Complete/in_progress/pending |
| `determine_run_status(step_statuses)` | Overall run status |
| `determine_chunk_status(chunk_state, pipeline)` | Chunk status from state |

### __init__.py (Module Exports)
Centralizes all exports for clean imports:
```python
from scripts.tui.utils import (
    # From runs.py
    scan_runs, get_active_runs, get_recent_runs,
    calculate_dashboard_stats, get_run_status,

    # From pipelines.py
    scan_pipelines, load_pipeline_config,

    # From formatting.py
    format_cost, format_tokens, format_progress_bar,

    # From status.py
    get_status_symbol, parse_chunk_state,
)
```

## 3. Data Flow

```
MANIFEST.json files in runs/
    │
    ▼
runs.py:scan_runs()
    │
    ├── load_manifest() for each run
    ├── get_run_status() → status
    ├── get_run_progress() → progress %
    ├── get_run_cost() → cost string
    └── Returns: list[dict] with all run data
    │
    ▼
HomeScreen / MainScreen
    │
    ├── formatting.py for display
    │   ├── format_cost()
    │   ├── format_tokens()
    │   └── format_progress_bar()
    │
    └── status.py for symbols
        ├── get_status_symbol()
        └── get_status_color()
```

## 4. Architectural Decisions

### Pure Functions
All utilities are pure functions with no UI framework dependencies:
- Can be tested with standard pytest
- No Textual imports required
- Enables reuse in CLI tools if needed

### Manifest as Source of Truth
Run status is derived from MANIFEST.json:
1. First check explicit `status` field (set by crash handling)
2. Fall back to inferring from chunk states

### Registry-Driven Pricing
Cost calculation uses pricing from the centralized model registry (`scripts/providers/models.yaml`).
The `get_run_cost_value()` function retrieves rates based on the provider and model used for the run.

### Safe Defaults
Functions handle missing data gracefully:
```python
def format_cost(cost, show_zero=True):
    if cost is None or cost == 0:
        return "--" if not show_zero else "$0.0000"
    return f"${cost:.4f}"
```

## 5. Key Patterns & Conventions

### Path Resolution
Paths are resolved relative to project root:
```python
# utils/__init__.py is at scripts/tui/utils/__init__.py
# Project root is 4 levels up
project_root = Path(__file__).parent.parent.parent.parent
runs_dir = project_root / "runs"
```

### Status Inference Order
```python
# Priority order for status:
1. Explicit manifest["status"] (complete, failed, paused)
2. All chunks VALIDATED → "complete"
3. Any chunk FAILED → "failed"
4. All chunks PENDING → "pending"
5. manifest["status"] == "running" → "active"
6. Otherwise → "detached"
```

### Progress Calculation
```python
def get_run_progress(manifest):
    chunks = manifest.get("chunks", {})
    validated = count where state == "VALIDATED"
    return int((validated / total) * 100)
```

### Elapsed Time Formatting
```python
def format_elapsed_time(start_time):
    delta = now - start_time
    if delta < 60 seconds:
        return "N sec ago"
    elif delta < 1 hour:
        return "N min ago"
    elif delta < 1 day:
        return "N hr ago"
    else:
        return "N days ago"
```

## 6. Recent Changes

### Manifest Summary Cache & scan_runs() Fast Path (Latest)
- **`.manifest_summary.json` fast path**: `scan_runs()` now checks for `.manifest_summary.json` (~300 bytes) before loading the full `MANIFEST.json` (1-5MB). Summaries are written by the orchestrator's `save_manifest()` function. Falls back to full manifest if summary is missing (TUI is strictly read-only — no lazy generation).
- **`_build_run_data_from_summary()`**: New function that builds the same dict structure as `_build_run_data_from_manifest()` from the lightweight summary file.
- **`_build_run_data_from_manifest()`**: Extracted from `scan_runs()` — contains the original full-manifest parsing logic as a standalone function.

### Retry .bak Signal & Funnel Display Support
- **`.bak` signal in `reset_unit_retries()`**: Before modifying `{step}_failures.jsonl` during TUI retry, creates a `.bak` copy if one doesn't already exist. This signals the orchestrator's `run_step_realtime()` to bypass the 90% idempotency fallback so the step will actually be re-processed. Without this, retried steps with 19/20 valid + 0 failures would be SKIPped.
- **`_count_step_valid()` in main_screen.py**: New helper that scans `{step}_validated.jsonl` files on disk, returning per-step valid counts. Used by the funnel display to show accurate throughput per step (manifest's `chunk_data.valid` only reflects the last step's count).

### Failure Categorization & Manifest Fixes
- **`_count_step_failures()` returns categorized dict**: Now returns `{"validation": N, "hard": N, "total": N}` instead of int; categorizes by `failure_stage` field (`schema_validation`/`validation` → validation, `pipeline_internal` → hard); used by pipeline box rendering and sidebar stats panel
- **Manifest consistency auto-fix**: `check_manifest_consistency()` detects runs where all chunks are terminal but status isn't "complete"; auto-corrects and logs `[AUTO-FIX]`; called from TUI's `_do_progress_tick()`
- **Progress fix for completed runs**: `get_run_progress()` returns 100 when `manifest["status"] == "complete"` (was calculating from chunk states, could show 99% on runs with failed chunks)
- **Resume orchestrator subprocess fix**: `resume_orchestrator()` uses `stdin=subprocess.DEVNULL` + `start_new_session=True` to prevent terminal corruption
- **PID file persistence**: PID files are no longer deleted on process exit, kill, failure, or stale detection. The PID file persists so the TUI can detect dead processes via `os.kill(pid, 0)` — a stale PID with a dead process is correctly reported as "process lost"

### v1.0 Updates
- **Expression step state handling**: `parse_chunk_state()` correctly handles expression step transitions
- **Step type detection**: Status functions work with both LLM and expression steps

### Batch Timing Functions
Added timing display support for batch mode:
- `get_batch_timing(run_dir)`: Parses RUN_LOG.txt for TICK entries, returns timing info
- `_format_duration(seconds)`: Formats seconds as "5s", "2m 30s", or "1h 30m"
- `get_run_pipeline_name(manifest, run_path)`: Now accepts optional run_path for config fallback

### Process Management Functions
Added comprehensive process tracking:
- `get_run_process_status(run_dir)`: Returns PID and alive status
- `get_process_diagnostics(run_dir)`: Full diagnostics including CPU, memory, cmdline
- `has_recent_errors(run_dir)`: Checks RUN_LOG.txt for recent ERROR entries
- `kill_run_process(run_dir)`: Sends SIGTERM to orchestrator
- `mark_run_as_failed(run_dir, msg)`: Updates manifest (PID file persists for diagnostics)

### Enhanced Status Detection
- `get_enhanced_run_status()`: Combines manifest status with process state
- Returns "stuck" if running but has recent errors
- Returns "zombie" if manifest says running but process dead
- Returns "failed" if manifest has error_message field

### Detached Status Detection
Added "detached" status for runs that:
- Are not in terminal state (complete/failed)
- Don't have explicit `status: "running"` in manifest
- May indicate crashed orchestrator

### Error Message Support
Added `get_run_error_message(manifest)` to retrieve error messages from failed runs for display in TUI.

### Active Status Categories
`get_active_runs()` now includes three categories:
- `active`: Orchestrator confirmed running
- `detached`: May need resume
- `paused`: User interrupted

## 7. Current State & Known Issues

### Working Features
- Complete run scanning and status detection
- Pipeline discovery and config loading
- All formatting functions
- Status symbol mapping

### Known Limitations
- Token counts don't include realtime multiplier
- No caching (rescans on every call)

### Technical Debt
- Some status logic duplicated between runs.py and status.py
- Could add caching for expensive operations

## 8. Testing

### Unit Testing
```python
# Test status determination
def test_get_run_status_complete():
    manifest = {"chunks": {"c1": {"state": "VALIDATED"}}}
    assert get_run_status(manifest) == "complete"

def test_get_run_status_detached():
    manifest = {"chunks": {"c1": {"state": "generate_SUBMITTED"}}}
    assert get_run_status(manifest) == "detached"

# Test formatting
def test_format_cost():
    assert format_cost(0.0123) == "$0.0123"
    assert format_cost(None) == "--"

def test_format_progress_bar():
    assert format_progress_bar(50, 10) == "█████░░░░░"
```

### Manual Testing
```python
# Test runs scanning
from scripts.tui.utils import scan_runs
runs = scan_runs()
for r in runs:
    print(f"{r['name']}: {r['status']} {r['progress']}%")

# Test pipelines scanning
from scripts.tui.utils import scan_pipelines
pipelines = scan_pipelines()
for p in pipelines:
    print(f"{p['name']}: {p['step_count']} steps")
```
