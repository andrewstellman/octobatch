# Screens Directory Context
> **File:** `scripts/tui/screens/CONTEXT.md`

## 1. Purpose

This directory contains the main screen classes for the Octobatch TUI. Each screen represents a distinct view in the application with its own layout, key bindings, and navigation logic.

## 2. Key Components

### home_screen.py (HomeScreen - Dashboard)
**Purpose:** Central dashboard showing statistics, active runs, and recent runs.

**Layout:**
```
┌─────────────────────────────────────────────────────┐
│ Octobatch                                    [header]│
├──────────┬──────────┬──────────┬──────────┬────────┤
│Total Runs│  Tokens  │   Cost   │ Pipelines│ [stats]│
├──────────┴──────────┴──────────┴──────────┴────────┤
│ Unified DataTable                                   │
│ │   │ Run           │Prog│Units│Valid│Fail│Cost│Dur │Mode    │Started│Status   │
│ │ ⠋ │ running_run   │ 45%│  50 │  23 │  2 │$1.2│02:15│realtime│Feb 10 │running  │
│ │ ✓ │ completed_run │100%│ 600 │ 596 │  0 │$12 │15:30│batch   │Feb 09 │complete │
│ │ ⚠ │ partial_run   │100%│ 600 │ 596 │  4 │$12 │15:30│batch   │Feb 09 │complete ⚠ (4)│
│ │ ✗ │ failed_run    │  0%│  50 │   0 │  0 │ -- │00:05│mixed   │Feb 08 │failed   │
├─────────────────────────────────────────────────────┤
│ N:new  Enter:open  P:pause  R:resume  K:kill  L:pipelines  Q:quit│
└─────────────────────────────────────────────────────┘
```

**Key Bindings:**
| Key | Action | Description |
|-----|--------|-------------|
| Q/q | quit | Exit application |
| N/n | new_run | Open NewRunModal |
| Enter | open_run | Open MainScreen for selected run |
| P/p | pause_run | Pause running orchestrator (sends SIGINT) |
| R | resume_run | Resume detached/paused run |
| K/k | kill_run | Kill process (if alive) or mark zombie as failed |
| D/d | delete_run | Delete selected run (hidden) |
| X/x | archive_run | Archive terminal run (moves to runs/_archive/) |
| H/h | toggle_archived | Show/hide archived runs |
| L/l | show_pipelines | Open ConfigListScreen |
| W/w | name_run | Set display name for a run |
| C/c | compare_runs | Compare selected runs side by side |
| Space | toggle_select | Multi-select toggle for run comparison |
| Ctrl+R/F5 | refresh | Reload data (hidden - auto-refresh handles this) |
| ↑/↓ | cursor_up/down | Navigate runs list |

**Async Startup & Auto-Polling:**
- `on_mount()` renders the screen layout immediately (columns, stats cards, API key check), then launches `_start_background_scan()` via `@work(thread=True, exclusive=True, group="home-scan")`
- `_start_background_scan()` calls `scan_runs()` and `scan_pipelines()` in a background thread, posts results to the main thread via `app.call_from_thread(self._on_scan_complete, ...)`
- `_on_scan_complete()` populates the DataTable, starts spinner/auto-refresh timers
- Auto-poll runs every 2.5 seconds in a background thread; `_bg_refresh()` checks manifest mtimes and re-scans only if changes detected
- `action_refresh()` and `on_screen_resume()` both use `_start_background_scan()` (no synchronous data loading)

**API Key Warning:**
- Non-blocking warning displayed when GOOGLE_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY are missing
- Uses `check_missing_api_keys()` helper from common.py
- Warning is informational only (doesn't block TUI functionality)

**Failure Notification System:**
- `_notified_failures: set[str]` tracks runs already notified about
- `_initial_load_complete: bool` prevents toasts for historical failures on startup
- Only NEW failures (detected after app start) trigger toast notifications

**Status Display Reference (for interpreting TUI screenshots):**
| Symbol | Color | Status Text | Meaning |
|--------|-------|-------------|---------|
| ⠋/◐ (spinner) | green | `running (step)` | Orchestrator actively processing |
| ✓ | green | `complete` | All units validated, zero failures |
| ⚠ | yellow | `complete ⚠ (N)` | Run finished but N units have validation failures (retryable) |
| ✗ | red | `failed` or `failed: <error>` | Run-level failure or hard failures |
| ? | yellow | `detached — R to restart` | No active process but non-terminal chunks |
| ⏸ | cyan | `paused` | User interrupted with P key |
| ⚠ | yellow | `stuck — has errors` | Process alive but encountering errors |
| 💀 | red | `zombie — R to restart` | Manifest says running but process dead |

**Failed Column Colors:**
- Yellow number: Complete run with validation failures (retryable with R key in detail view)
- Red number: Failed run with hard/system failures

**Reactive Properties:**
- `selected_index`: Currently selected run (0-indexed across active + recent)

### main_screen.py (MainScreen - Run Details)
**Purpose:** Detailed view of a single run with 4-quadrant layout and toggleable bottom panel (Unit View default, Chunk View toggle).

**Layout:**
```
┌─────────────────────────────────┬──────────────────┐
│ Pipeline Visualization          │ Run Stats Panel  │
│ ┌────────┐   ┌────────┐        │ Cost: $12.34     │
│ │GENERATE│──▶│VALIDATE│        │ Tokens: 100,332  │
│ └────────┘   └────────┘        │ Duration: 1:23   │
│                                 │                  │
│                                 │ Process Status   │
│                                 │ ⚠ STUCK          │
│                                 │ PID: 12345       │
├─────────────────────────────────┼──────────────────┤
│ Detail Panel (chunks OR units)  │ Stats Panel      │
│ │ Icon │ Name  │ Prog │ Err │  │ (context-aware)  │
│ │  ✓   │ c_001 │ ████ │  0  │  │                  │
│ │  ●   │ c_002 │ ██░░ │ ⚠ 4 │  │                  │
├─────────────────────────────────┴──────────────────┤
│ ←→:step  ↑↓:nav  V:units  I:info  Esc:back  Q:quit │
└────────────────────────────────────────────────────┘
```

**Expression Step Display:**
Expression steps (`scope: expression`) appear in the pipeline visualization like regular steps but show:
- No associated template file
- Zero token cost
- Immediate completion (no API polling)

**Key Bindings:**
| Key | Action | Description |
|-----|--------|-------------|
| Q/q | quit | Exit application |
| L/l | show_log | Open LogModal |
| A/a | view_artifacts | Open artifact viewer modal |
| C/c | show_config | Open ConfigListScreen |
| I/i | process_info | Open ProcessInfoScreen for this run |
| V/v | toggle_view | Toggle between Chunk View and Unit View |
| X/x | archive_run | Archive this run (terminal runs only) |
| G/g | pipeline_report | Pipeline validation funnel report modal |
| M/m | mode_switch | Schedule batch↔realtime mode switch |
| P/p | intermediate_results | View validated output for selected step |
| T/t | troubleshoot | AI troubleshooting (renders failure context, sends to LLM) |
| D/d | generate_diagnostic | Open Diagnostics Screen for failure analysis |
| R/r | retry_failures | Retry validation failures for selected step |
| Escape | go_back | Return to HomeScreen |
| Enter | select_item | Show unit detail (unit view only) |
| ←/→ | prev/next_step | Navigate pipeline steps |
| ↑/↓ | nav_up/down | Navigate within panel |
| F/f | cycle_status_filter | Filter: All → Valid → Failed (unit view) |
| S/s | cycle_sort | Sort: unit_id → status → step (unit view) |

**Pipeline Funnel Display:**
Each step shows per-step throughput instead of global progress:
```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│     GENERATE     │────▶│ SCORE COHERENCE  │────▶│  SCORE WOUNDS    │
│   ● 406/500      │     │   ● 262/406      │     │   ● 222/262      │
└──────────────────┘     └──────────────────┘     └──────────────────┘
```
- Step 1 input = global total_units (500)
- Step N input = valid count from Step N-1 (e.g., 406 → 262)
- `_count_step_valid()` scans `{step}_validated.jsonl` files on disk
- Flag emoji (🏁) uses funnel data when available
- Passed as `step_funnel` dict to `render_pipeline_boxes()`

**Failure Display (pipeline boxes and sidebar):**
Failures are categorized by `failure_stage` from `_failures.jsonl`:
- **Validation failures** (yellow `⚠`): `failure_stage` is `"schema_validation"` or `"validation"` — LLM returned bad output (retryable with R key)
- **Hard failures** (red `✗`): `failure_stage` is `"pipeline_internal"` — lost records or pipeline errors (not retryable)

Pipeline boxes show separate rows:
```
⚠ 5 valid. fail    (yellow — validation failures for this step)
✗ 2 failed          (red — hard failures for this step)
```

Selected Step sidebar shows:
```
Passed:        95
Validation:    5     (yellow — retryable)
Failed:        2     (red — not retryable)
```

**Otto Status Narrator:**
`OttoOrchestrator.update_narrative()` called from `_diff_chunk_states()` every refresh tick:
- Extracts providers from run's `config/config.yaml` (global `api.provider` + per-step overrides)
- Shows "Waiting for Gemini..." / "Waiting for Claude..." when single provider active
- Shows "Otto is orchestrating..." when multiple providers or unknown
- Shows "Otto finished the job!" on completion, "Otto hit a snag..." on failure

**Process Status Section (in Run Stats panel):**
- `● RUNNING` (green): Process alive, no errors
- `⚠ STUCK` (yellow): Process alive but encountering errors
- `○ Not running` (dim): Process not found

**Chunk Table Columns:**
- Icon, Chunk name, Progress bar, Count (valid/total), **Errors** (⚠ N if >0), Status

**Reactive Properties:**
- `selected_step_index`: Current pipeline step (0-indexed)
- `selected_chunk_index`: Current chunk in list
- `selected_unit_index`: Current unit in list
- `active_focus`: "pipeline" or "detail"
- `current_view`: "unit" (default) or "chunk"
- `status_filter`: "all", "valid", or "failed"
- `sort_by`: "unit_id", "status", or "step"

**Toggle View Feature:**
Press `V` to toggle between:
- **Unit View** (default): Shows all units with chunk column, filtering and sorting
- **Chunk View**: Shows chunks for selected step with progress bars

**Unit View Table Columns:**
- Icon, Unit ID, Chunk (e.g., "chunk_000"), Step, Status

In Unit View, the right stats panel shows filter summary instead of chunk details.

### new_run_modal.py (NewRunModal - Run Creation)
**Purpose:** Modal form for creating and starting a new batch run.

**Form Fields:**
1. Pipeline selector (dropdown) - syncs provider/model from selected pipeline's config
2. Run name input (auto-generated if empty)
3. Provider selector (dropdown) - populated from models.yaml registry
4. Model selector (dropdown) - sorted by cost, cheapest pre-selected
5. Max Units input (optional)
6. Mode radio (Batch/Realtime)

**Key Bindings:**
| Key | Action | Description |
|-----|--------|-------------|
| Escape | cancel | Close modal |

**Key Methods:**
- `_populate_models(provider, preselect_model)`: Populates model dropdown sorted by cost, optionally pre-selects a specific model
- `_sync_provider_from_pipeline()`: Reads pipeline config and pre-selects matching provider/model
- `_start_run()`: Validates provider/model selection before launching

**Launch Logic (via run_launcher.py):**
- **Batch mode**: Two-step process
  1. `orchestrate.py --init --provider X --model Y` (blocking, 60s timeout)
  2. `orchestrate.py --watch` (background)
- **Realtime mode**: Single combined command
  - `orchestrate.py --init --realtime --provider X --model Y --yes` (background)

### run_launcher.py (Run Launcher Functions)
**Purpose:** Extracted launch logic from new_run_modal.py for separation of concerns.

**Functions:**
- `start_realtime_run(orchestrate_path, pipeline, run_dir, max_units, provider, model)`: Launches realtime run
- `start_batch_run(orchestrate_path, pipeline, run_dir, max_units, provider, model)`: Launches batch run (init + watch)

### common.py (Shared Utilities)
**Constants:**
- Box drawing: `BOX_H`, `BOX_V`, `BOX_TL`, `BOX_TR`, `BOX_BL`, `BOX_BR`
- Status symbols: `CHECK`, `CIRCLE_FILLED`, `CIRCLE_EMPTY`

**Helper Functions:**
- `parse_chunk_state(state, pipeline)`: Parse state string
- `get_step_status_from_chunks()`: Determine step status
- `get_chunk_status_symbol()`: Get colored symbol for chunk
- `make_progress_bar()`: Create ASCII progress bar
- `set_os_terminal_title(title)`: Write xterm OSC title escape sequence to `/dev/tty` (bypasses Textual's stdout capture); deduplicated via module-level `_last_os_title` cache

### modals.py (Shared Modals)
**ArchiveConfirmModal:** Confirmation dialog for archiving runs; triggered by `X` key on home screen and detail screen
**ConfirmModal:** Generic yes/no confirmation modal (used by mode switch, etc.)
**TextInputModal:** Text input modal (used by named runs, etc.)

Note: `LogModal`, `DetailModal`, `FailureDetailModal`, `ArtifactModal` are in `scripts/tui/modals.py` (package level, not screens directory).

### UnitDetailModal (in main_screen.py)
**Purpose:** Interactive JSON tree view for unit data with multiple view modes.

**Features:**
- Three view modes: Tree (input data), Raw (full JSON), LLM Response (raw_response)
- Expandable/collapsible tree structure for JSON data
- Syntax highlighting (cyan for keys, green for strings, blue for numbers, yellow for booleans)
- Long strings truncated to 60 chars in display (full value on copy)
- Error section for failed units showing failure stage and error messages
- Shows chunk name in header
- LLM Response view shows raw_response field (what LLM returned before validation failed)

**Key Bindings:**
| Key | Action | Description |
|-----|--------|-------------|
| ↑/↓ | navigate | Move between tree nodes |
| ← | collapse | Collapse current node |
| → | expand | Expand current node |
| T/t | view_tree | Switch to tree view (input/context data) |
| R/r | view_raw | Switch to raw JSON view |
| L/l | view_response | Switch to LLM response view (failed units only) |
| C/c | copy | Copy full JSON to clipboard |
| Escape | close | Close modal |

**View Modes:**
- **Tree (T)**: Shows input/context data as expandable tree. Default view.
- **Raw (R)**: Shows full JSON with line numbers. Useful for copying.
- **LLM Response (L)**: Shows `raw_response` field - the actual LLM output that failed validation. Only available for failed units with captured raw_response.

### diagnostics_screen.py (DiagnosticsScreen - Interactive Failure Analysis)
**Purpose:** Step-by-step failure analysis screen with revalidation support. Accessed via `D` key from MainScreen.

**Key Bindings:**
| Key | Action | Description |
|-----|--------|-------------|
| Escape | go_back | Return to MainScreen |
| Q/q | quit_app | Exit application |
| ←/→ | step_prev/step_next | Navigate pipeline steps |
| V/v | revalidate | Re-run validation on failures for current step |
| S/s | save_diagnostic | Save diagnostic report to file |
| C/c | copy_diagnostic | Copy diagnostic to clipboard |

### TroubleshootPromptModal (in main_screen.py)
**Purpose:** AI-assisted troubleshooting. Renders failure context into a Jinja2 template, lets user edit the prompt, select a provider/model, and send via realtime API.

**Features:**
- Full-screen modal with prompt TextArea editor
- Provider/model selector (auto-defaults to cheapest configured option)
- Copy prompt to clipboard (C key)
- Send or Cancel buttons

**Template:** Uses `scripts/templates/troubleshoot.jinja2` with failure summary, top errors, sample failures, and config snapshot.

**Key Bindings:**
| Key | Action | Description |
|-----|--------|-------------|
| Escape | cancel | Close modal |
| C/c | copy | Copy prompt to clipboard |

### splash_screen.py (SplashScreen - Otto Welcome Overlay)
**Purpose:** Non-blocking animated Otto splash overlay on app startup.

**Layout:** Toast-styled bottom-right floating overlay with green left border.

**Features:**
- Transparent ModalScreen background with `align: right bottom`
- Container styled like a Textual toast: `background: $panel`, `border-left: thick $success`
- Contains animated OttoWidget with staggered transfers (0.3s, 0.9s, 1.6s, 2.5s) and flag wave at 3.5s
- X button (✕) docked top-right to close
- Auto-dismisses after 10 seconds
- Escape key also dismisses

**Key Bindings:**
| Key | Action | Description |
|-----|--------|-------------|
| Escape | dismiss_splash | Close overlay |

**Triggered from:** `OctobatchApp.on_mount()` in `app.py`, pushed after both HomeScreen and MainScreen.

### process_info.py (ProcessInfoScreen - Process Diagnostics)
**Purpose:** Detailed view of process diagnostics for a selected run. Accessed via `I` key from MainScreen.

**Layout:**
```
┌─────────────────────────────────────────────────────┐
│ Process Info: run_name                       [header]│
├─────────────────────────────────────────────────────┤
│ Process Status                                       │
│   Status:       ⚠ STUCK / ● RUNNING / ✓ COMPLETE   │
│   PID:          12345                               │
│   Source:       pid_file / discovered               │
│   CPU:          2.5%                                │
│   Memory:       45.2 MB                             │
│   Running:      5 min ago                           │
│   Started:      2025-01-24 10:30:00                 │
├─────────────────────────────────────────────────────┤
│ Command Line                                         │
│   python orchestrate.py --run-dir ...               │
├─────────────────────────────────────────────────────┤
│ PID File                                             │
│   Status:       N/A (run finished)  [for complete/failed]│
│   -- OR --                                           │
│   File:         Exists / Missing    [for active/paused]│
│   Content:      12345                               │
├─────────────────────────────────────────────────────┤
│ Recent Log (has errors)                              │
│   [INFO] Processing chunk...                        │
│   [ERROR] Failed to validate...                     │
├─────────────────────────────────────────────────────┤
│ K:kill  C:copy log  E:copy errors  r:refresh  Q:quit│
└─────────────────────────────────────────────────────┘
```

**Key Bindings:**
| Key | Action | Description |
|-----|--------|-------------|
| Escape | go_back | Return to MainScreen |
| Q/q | quit_app | Exit application |
| K/k | kill_process | Kill the orchestrator process |
| C/c | copy_log | Copy last 100 log lines to clipboard |
| E/e | copy_errors | Copy ERROR lines to clipboard (deduplicated) |
| r | refresh | Reload diagnostics |

**Status Display:**
- `● RUNNING` (green): Process alive, no errors
- `⚠ STUCK` (yellow): Process alive but encountering errors
- `✓ COMPLETE` (green): Run completed successfully
- `✗ FAILED` (red): Run failed with error message
- `⏸ PAUSED` (yellow): Run paused by user
- `○ ZOMBIE` (red): Manifest says running but no process found

**Copy Features:**
- `C` copies last 100 lines from RUN_LOG.txt
- `E` copies only ERROR lines, with consecutive duplicates removed
- Uses pyperclip library with graceful fallback

**Data Source:**
Uses `get_process_diagnostics()` from `runs.py` which returns:
- `pid`: Process ID if found
- `alive`: Whether process is running
- `source`: How PID was found ("pid_file" or "discovered")
- `cmdline`: Full command line if process alive
- `cpu_percent`: CPU usage if alive
- `memory_mb`: Memory usage in MB if alive
- `create_time`: Process start time if alive
- `run_duration`: How long process has been running
- `pid_file_exists`: Whether orchestrator.pid exists
- `pid_file_content`: Content of PID file if exists
- `recent_log_lines`: Last 10 lines of RUN_LOG.txt
- `has_errors`: Whether recent logs contain errors

Also uses `has_recent_errors()` to detect stuck status when process is alive.

## 3. Data Flow

```
HomeScreen
    │
    ├── scan_runs() → _runs_data
    │   ├── _active_runs (running, stuck, zombie, active, detached)
    │   └── _recent_runs (complete, failed, paused, pending) - sorted by completion time
    │
    ├── N → NewRunModal
    │   │
    │   └── _start_run() → subprocess.Popen
    │       │
    │       └── Result → _on_new_run_result() → refresh + open MainScreen
    │
    ├── Enter → MainScreen(run_data)
    │   │
    │   ├── Unit View (default)
    │   │   ├── _load_all_units() → _all_units
    │   │   │   ├── Scan *_validated.jsonl → valid units
    │   │   │   └── Scan *_failures.jsonl → failed units
    │   │   ├── F/S → Filter and sort
    │   │   └── Enter → UnitDetailModal(unit)
    │   │
    │   ├── V → Chunk View
    │   │   ├── _chunks_for_step (filtered by selected step)
    │   │   ├── _get_process_status() → shows in Run Stats panel
    │   │   └── ↑↓ to navigate chunks
    │   │
    │   └── I → ProcessInfoScreen(run_dir)
    │       │
    │       ├── get_process_diagnostics() → _diagnostics
    │       │   ├── Check PID file
    │       │   ├── Get process status (psutil)
    │       │   ├── has_recent_errors() → STUCK detection
    │       │   └── Read recent log lines
    │       │
    │       ├── K → kill_run_process()
    │       ├── C → copy log (last 100 lines)
    │       └── E → copy errors (deduplicated)
    │
    ├── K → kill_run_process() (if alive) OR mark_run_as_failed() (if zombie)
    │
    └── R → action_resume_run() → subprocess.Popen
```

## 4. Architectural Decisions

### Unified Selection Index
HomeScreen uses a single `selected_index` across both active run cards and recent runs table. This enables seamless keyboard navigation.

### Two-Step Batch Launch
Batch mode uses separate `--init` and `--watch` commands because these flags are mutually exclusive in orchestrate.py's argparse config.

### Toggle View Pattern
MainScreen uses `current_view` reactive property to toggle between Chunk View and Unit View. The view is re-rendered by clearing and repopulating the detail panel container.

### Step-Scoped Unit Loading
Units are loaded scoped to the currently selected step (`_load_all_units(step_name=...)`), then cached in `_all_units`. When the step changes, the cache is cleared and units reload for the new step. `_unique_steps` is derived from the manifest pipeline list, not from loaded units. This reduces I/O and memory by ~75% for multi-step pipelines.

### Threaded Unit Loading
Unit loading (`_load_all_units`) runs in a background thread to avoid blocking the Textual event loop. The pattern:
1. `_render_unit_view()` checks `_units_loaded`; if false, shows "Loading..." and calls `_start_unit_load()`
2. `_start_unit_load()` guards with `_units_loading` flag, spawns a daemon thread running `_bg_load_units()`
3. `_bg_load_units()` calls `_load_all_units()` in the thread, then uses `app.call_from_thread()` to deliver results
4. `_on_units_loaded()` runs on the main thread: validates the step hasn't changed, sets `_all_units` and `_units_loaded`, then calls `_update_detail_panel()`
5. All sites that clear `_all_units = []` also set `_units_loaded = False` to trigger reload
6. A separate `_do_unit_refresh()` timer (5s interval) triggers background reloads for running runs in unit view

### Panel-Based Focus
MainScreen uses `active_focus` to determine which panel receives keyboard input, allowing context-sensitive navigation.

### Widget Update Pattern
To avoid DuplicateIds with Textual's async `remove_children()`:
```python
try:
    existing = self.query_one("#widget-id", Static)
    existing.update(new_content)
except NoMatches:
    self.mount(Static(new_content, id="widget-id"))
```

## 5. Key Patterns & Conventions

### Status Symbol Mapping
```python
symbols = {
    "complete": "[green]✓[/]",
    "failed": "[red]✗[/]",
    "active": "[green]●[/]",
    "detached": "[yellow]⚠[/]",
    "paused": "[cyan]⏸[/]",
    "pending": "[dim]○[/]",
}
```

### Screen Callbacks
When pushing screens with result handling:
```python
self.app.push_screen(NewRunModal(pipelines), self._on_new_run_result)

def _on_new_run_result(self, result):
    if result is None:
        return  # User cancelled
    # Process result...
```

### Key Interception
To prevent default widget behavior (like Tab):
```python
def on_key(self, event: events.Key) -> None:
    if event.key == "tab":
        event.prevent_default()
        event.stop()
```

### Spinner Animation
For active runs, a timer updates spinner frames:
```python
SPINNER_FRAMES = ["◐", "◓", "◑", "◒"]
self._spinner_timer = self.set_interval(0.25, self._animate_spinner)
```

## 6. Recent Changes

### Dynamic Terminal Title & Version Display (Latest)
- **Dynamic terminal title via `self.app.title`**: Home screen shows run counts (e.g. "Octobatch v1.1.0 – Main Screen (10 runs)"), detail screen shows pipeline name and status (e.g. "Octobatch v1.1.0 – Blackjack pipeline (complete)"). OS terminal window title set via `set_os_terminal_title()` in `common.py` which writes xterm OSC escape sequence to `/dev/tty`, bypassing Textual's stdout capture. Title deduplication via `_last_os_title` cache prevents flicker from background polls. `self.app.title` (Header widget) guarded by `self.app.screen is self` to prevent stale screen updates; OSC write is unguarded but cache-protected. Detail screen uses enhanced process-aware status (e.g. "process lost" instead of raw manifest "running").
- **Version displayed in TUI header from `scripts/version.py`**: Single source of truth `__version__` in `scripts/version.py`, imported by both screens' `_render_header()` methods. Shown as `[bold]Octobatch[/] [dim]v{__version__}[/]` in the visual header bar. Also used in `--version` CLI flag and `--dump` text output.

### Archive Feature
- **Archive keybinding (X)**: HomeScreen and MainScreen both support `X` to archive a terminal run. Moves run directory to `runs/_archive/`. Guarded by terminal-only safety check (cannot archive running/paused runs). Uses `ArchiveConfirmModal` for confirmation.
- **Show/hide archived (H)**: HomeScreen `H` key toggles `_show_archived` flag. When enabled, `scan_runs(include_archived=True)` includes runs from `runs/_archive/` in the DataTable. Archived runs display with a visual indicator.
- **scan_runs() include_archived parameter**: `utils/runs.py` `scan_runs()` accepts `include_archived=bool` to optionally scan `runs/_archive/` alongside `runs/`.

### Pipeline Funnel Display, Retry .bak, Otto Narrator
- **Pipeline funnel display**: Each step shows per-step throughput (Valid/Input) instead of global progress; `_count_step_valid()` scans `{step}_validated.jsonl` on disk; Step 1 input = total_units, Step N input = valid from Step N-1; passed as `step_funnel` dict to `render_pipeline_boxes()`; flag emoji uses funnel data
- **Otto status narrator**: `OttoOrchestrator.update_narrative()` called from `_diff_chunk_states()` every refresh tick; extracts providers from config; shows provider-specific narrative labels ("Waiting for Gemini...", "Waiting for Claude...", "Otto is orchestrating...", "Otto finished the job!", "Otto hit a snag...")
- **Splash screen key passthrough**: Keys typed during splash pass through to underlying HomeScreen; transparent ModalScreen doesn't capture input
- **Retry .bak in TUI**: `reset_unit_retries()` creates `.bak` signal before modifying failures; ensures orchestrator's idempotency check won't skip retried steps

### Validation vs Hard Failure Differentiation & TUI Polish
- **Failure categorization in pipeline boxes**: `_count_step_failures()` returns `{"validation": N, "hard": N, "total": N}`; pipeline boxes show yellow "⚠ N valid. fail" for validation failures and red "✗ N failed" for hard failures as separate rows
- **Sidebar stats split**: Selected Step panel shows "Validation: N" (yellow) and "Failed: N" (red) instead of single "Failed: N"; only lines with count > 0 are shown
- **Retry validation only**: `action_retry_failures()` filters to validation failures (`schema_validation`/`validation`); hard failures (`pipeline_internal`) skipped with notification; footer retry hint shows retryable count only
- **Home screen "complete ⚠"**: `_get_run_status_text()` shows "complete ⚠ (N)" for complete runs with failures; Failed column shows yellow for complete runs (validation), red for failed runs (hard)
- **Otto integration in MainScreen**: OttoWidget in right column with OttoOrchestrator; `_diff_chunk_states()` detects advances/retries/completions; responsive hiding on narrow terminals via `on_resize()`
- **Scrollable stats sidebar**: Stats wrapped in `VerticalScroll(id="stats-scroll")`; Otto fixed above in `Container(id="otto-container")`
- **SplashScreen**: Toast-styled overlay pushed from app.py; animated Otto with transfers and flag wave; 10s auto-dismiss; X button and Escape to close
- **Batch pipeline boxes**: `get_batch_detail` callback in `render_pipeline_boxes()` shows "📤 N processing" / "⏳ N pending" per step
- **Batch patience toast**: Provider-aware toast when batch idle >60s; reads config to detect provider(s)
- **Progress/ETA fix**: `_calculate_projections()` returns early for terminal runs with `manifest_status`; three-branch display
- **Recent Activity refresh fix**: `_do_retry` restarts refresh timers if `_refresh_active` is False
- **Log viewer (LogModal)**: S=save, C=copy, P=copy path; `markup=False`; standalone ModalScreen
- **"Use Pipeline Config" default**: New run modal maps to `None` overrides so step-level config takes effect
- **Removed welcome toast**: `self.notify("Welcome to Octobatch")` removed from HomeScreen `on_mount()`

### v1.0 Provider/Model Support
- **HomeScreen API key warning**: Non-blocking warning when API keys are missing (GOOGLE, OPENAI, ANTHROPIC)
- **NewRunModal provider/model dropdowns**: Select provider and model from registry
- **Cheapest model auto-selection**: Models sorted by cost (input + output per million), cheapest pre-selected
- **Pipeline config sync**: Selecting pipeline pre-selects its configured provider/model
- **Provider/model validation**: Start button validates provider and model before launching
- **Scrollable modal**: NewRunModal uses VerticalScroll for short terminals
- **run_launcher.py**: Launch logic extracted to separate module for separation of concerns
- **Threaded unit refresh**: `_reload_realtime_progress()` in MainScreen uses background thread

### Failure Inspection & Unit Detail Improvements
- **UnitDetailModal view modes**: Three view modes (T:tree, R:raw, L:llm-response) for inspecting failed units
- **raw_response display**: Failed units show the LLM's actual output that failed validation
- **Selected Step stats fix**: Panel uses `_count_step_failures()` for actual counts, shows "Processing" separately
- **Max Units display**: Run Stats panel shows max_units when one was applied
- **Dual-highlight fix**: `_render_recent_runs()` enforces inactive state on refresh

### Auto-Polling & Pause
- **HomeScreen Auto-Polling**: Polls every 2.5 seconds for manifest changes, auto-refreshes UI
- **Pause Action**: `P` key sends SIGINT to gracefully pause running orchestrator
- **Step Progress Display**: Active run cards show current step and progress (e.g., "generate_dialog 3/9")
- **Batch Timing Display**: Active run cards show "Last: Xs | Next: Ys" for batch mode runs
- **Pipeline Name Display**: Active run cards show pipeline name from manifest
- **Navigation Change**: `L` for pipelines (was `P`), `P` now pauses
- **Run Categorization**: Paused runs now appear in Recent Runs (not Active)
- **Completion Sorting**: Recent runs sorted by completion/failure time (most recent first)

### MainScreen Improvements
- **Default Unit View**: MainScreen now opens in Unit View instead of Chunk View
- **Chunk Column**: Unit View table includes chunk name column (e.g., "chunk_000")
- **Footer on Mount**: Footer correctly updates when screen loads
- **Grid Layout Fix**: Fixed CSS grid layout for proper footer visibility

### ProcessInfoScreen Improvements
- **N/A for Completed Runs**: PID file section shows "N/A (run finished)" for complete/failed runs

### Error Visibility Enhancements
- **MainScreen Process Status**: Added "Process Status" section to Run Stats panel showing RUNNING/STUCK/Not running
- **MainScreen Errors Column**: Chunk table now includes "Errors" column with `⚠ N` indicator
- **ProcessInfoScreen STUCK Detection**: Uses `has_recent_errors()` to show `⚠ STUCK` when process alive with errors
- **ProcessInfoScreen Copy Features**: Added `C` to copy log, `E` to copy errors (deduplicated)

### Failure Notification System
- HomeScreen tracks `_notified_failures` set to prevent duplicate toasts
- `_initial_load_complete` flag prevents toasts for historical failures on startup
- Only NEW failures trigger toast notifications

### Navigation Refactoring
- Process Info (I key) moved from HomeScreen to MainScreen only
- HomeScreen I binding removed; access via MainScreen → I
- Consistent Q to quit across all screens

### Zombie Cleanup
- K key on HomeScreen is context-aware: kills live process OR marks zombie as failed
- Uses `mark_run_as_failed()` to update manifest (PID file persists for diagnostics)

### Process Management System
Added robust process tracking and kill functionality:
- **PID File**: Orchestrator writes `orchestrator.pid` on startup; PID file persists after exit (TUI detects dead processes via `os.kill(pid, 0)`)
- **Process Discovery**: If PID file is stale or missing, scans running processes to find orchestrator
- **PID Verification**: Validates command line args before killing to prevent PID recycling issues
- **Enhanced Status**: HomeScreen now shows `running`, `stuck` (running with errors), or `zombie` (dead process)
- **Kill Binding**: Press `K` to kill the selected run's orchestrator process

New status indicators:
- `[green]●[/]` running - Process alive and healthy
- `[yellow]⚠[/]` stuck - Process running but has recent errors in log
- `[red]💀[/]` zombie - Manifest says running but process is dead

### Toggle View Feature
MainScreen now supports toggling between Chunk View and Unit View:
- Press `V` to toggle views
- Unit View shows all units from all chunks in a flat list
- Filter by status (F) or change sort order (S)
- Enter on a unit opens UnitDetailModal with full JSON

### Removed ResultsInspectorScreen
The separate ResultsInspectorScreen has been removed. Its functionality is now integrated into MainScreen as the Unit View toggle.

### Resume Capability
Added `action_resume_run()` to HomeScreen:
- Validates run status is "detached" or "paused"
- Determines mode from manifest
- Launches appropriate orchestrator command
- Refreshes UI after launch

### Detached Run Display
Active run cards now show different indicators:
- `[yellow]⚠ Detached[/]` for detached runs
- `[cyan]⏸ Paused[/]` for paused runs
- Error message snippet if present

### Layout Ordering Fix
Active Runs section uses `mount(active_section, before=recent_section)` to ensure correct ordering after navigation.

### Scroll Fix for DataTable
`_highlight_recent_run()` now calls `_scroll_to_recent_row()` to scroll parent VerticalScroll when navigating.

## 7. Current State & Known Issues

### Working Features
- Dashboard with real-time stats
- Run creation with subprocess management
- Pipeline visualization and navigation
- Chunk view with step filtering
- Unit view with filter/sort capabilities
- Resume capability
- Kill stuck/zombie processes
- Process discovery for orphaned runs
- Log and config viewing

### Recent Bug Fixes (Feb 7, 2026)

**Step-scoped unit loading**: `_load_all_units()` now accepts a `step_name` parameter and only loads units for that step. Previously it loaded all steps into one flat list (e.g., 2400 rows for a 600-unit, 4-step pipeline). The cache is cleared when the step changes via `watch_selected_step_index`.

**Live progress updates on home screen**: A dedicated `_do_progress_tick()` timer (every 5 seconds, separate from the 1-second duration ticker) re-reads manifests for running rows and updates Progress, Cost, and Status columns via `table.update_cell()` — no full table rebuild needed. Started alongside the spinner and duration ticker in `on_mount`, `on_screen_resume`, and `_apply_refresh`.

**Dedicated Units/Valid/Failed columns**: Refactored DataTable columns to: Run | Progress | Units | Valid | Failed | Cost | Duration | Mode | Started | Status. Valid uses green text, Failed uses red text when > 0. Status column is now clean state only (no appended failure info). The `_do_progress_tick()` updates Valid and Failed cells live for running rows. `get_run_unit_failure_count()` computes `total_units - total_valid` for terminal runs (chunk `failed` field was unreliable). `scan_runs()` now includes `total_units` and `valid_units` fields.

**Mode column**: Shows "batch", "realtime", or "mixed" (batch run resumed in realtime — detected when chunks have `batch_id` AND metadata.mode is "realtime"). `scan_runs()` provides `mode_display` field. The 5-second progress ticker refreshes Mode for running rows since mode can change if a user resumes in a different mode.

**Threaded unit loading (keyboard lag fix)**: `_load_all_units()` now runs in a background thread instead of synchronously on the Textual event loop. Previously, `_do_refresh()` ran every 0.5s and synchronously loaded ~2400 JSONL entries for running runs, starving the event loop and causing keystrokes to pile up. Now: refresh timer is 2.0s (lightweight — pipeline/stats only), unit refresh is a separate 5.0s timer that triggers a background thread. The UI shows "Loading..." while units load asynchronously.

### Known Limitations
- Delete run not implemented (D key exists but shows "not yet implemented" notification)
- Process management requires `psutil` package

### Technical Debt
- Some repeated status symbol logic could be consolidated

## 8. Testing

### Manual Testing
```bash
# Test HomeScreen
python -c "from scripts.tui import run_tui; run_tui()"

# Test with many runs
# Verify scrolling works with Down arrow

# Test New Run
# Press N, select pipeline, start run
# Verify subprocess appears in process list

# Test Resume
# Find detached run, press R
# Check orchestrator.log for resume message
```

### Key Test Scenarios
1. **Navigation**: Arrows move selection, Enter drills down, Escape goes back
2. **New Run**: Form validation, subprocess launch, auto-navigation
3. **Resume**: Only enabled for detached/paused, launches correct mode
4. **Refresh**: Ctrl+R or F5 reloads all data and updates display
5. **Layout**: Active section always above Recent section
6. **Toggle View**: V switches between Chunk and Unit view
7. **Unit Filtering**: F cycles status, S cycles sort
8. **Unit Detail**: Enter on unit in Unit View opens detail modal
9. **Kill Process**: K kills stuck/zombie processes, status updates after refresh
10. **Process Discovery**: Orphaned processes (no PID file) are discovered and can be killed
11. **Process Info**: From MainScreen, I opens ProcessInfoScreen showing diagnostics
12. **Active Card PID**: Active run cards show PID when process is alive
13. **STUCK Detection**: Runs with errors show `⚠ STUCK` in MainScreen stats and ProcessInfoScreen
14. **Copy Log**: In ProcessInfoScreen, C copies last 100 log lines, E copies unique error lines
15. **Zombie Cleanup**: K on zombie in HomeScreen marks as failed via manifest update
16. **No Toast Spam**: App startup doesn't show toasts for historical failures
