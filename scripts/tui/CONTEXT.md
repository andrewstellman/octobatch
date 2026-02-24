# TUI Package Context
> **File:** `scripts/tui/CONTEXT.md`

## 1. Purpose

This package provides a Terminal User Interface (TUI) for the Octobatch batch processing system, built on the Textual framework. It offers a dashboard for monitoring runs, creating new runs, and managing pipeline configurations.

## 2. Key Components

### app.py (Main Entry Point)
- **OctobatchApp**: Extends `textual.app.App`
- **run_tui(run_dir, debug)**: Entry function to start the app
- Debug logging infrastructure with `tui_debug.log`
- Initializes with optional `run_dir` parameter

### Package Structure
```
tui/
├── app.py              # Main Textual app (pushes SplashScreen on mount)
├── data.py             # Data models (RunData, StepStatus, etc.)
├── __init__.py         # Package exports
├── screens/            # Screen classes (HomeScreen, MainScreen, SplashScreen, etc.)
├── config_editor/      # Pipeline configuration editor
├── utils/              # Utility functions (runs, pipelines, formatting)
└── widgets/            # Reusable widget components
    ├── otto_widget.py      # Animated Otto the Octopus mascot (8fps)
    ├── otto_orchestrator.py # Pipeline event → animation adapter
    ├── pipeline_view.py    # Pipeline step box rendering
    ├── stats_panel.py      # Stats panel widgets
    └── progress_bar.py     # Progress bar widget
```

### data.py (Data Models)
**Dataclasses for run information:**
- `RunData`: Complete run information (run_dir, pipeline, steps, chunks, costs)
- `StepStatus`: Pipeline step status with symbol/progress properties
- `ChunkStatus`: Chunk progress information
- `Failure`: Validation failure records
- `UnitRecord`: Individual unit with status

**Key Function:**
- `load_run_data(run_dir)`: Loads MANIFEST.json, report.json, logs into RunData

## 3. Data Flow

```
OctobatchApp.on_mount()
    │
    ├── No run_dir → HomeScreen (dashboard)
    │   │
    │   └── scan_runs() → _runs_data
    │       │
    │       ├── Active runs (status in {active, detached, paused})
    │       └── Recent runs (status in {complete, failed, pending})
    │
    └── With run_dir → load_run_data() → RunData → MainScreen
        │
        └── Displays pipeline, chunks, units from RunData

Navigation Flow:
HomeScreen ─┬─ N → NewRunModal → subprocess launch
            ├─ Enter → MainScreen (selected run)
            ├─ P → ConfigListScreen (pipeline editor)
            └─ R → Resume detached/paused run
```

## 4. Architectural Decisions

### Screen Stack Navigation
- HomeScreen is the root (never popped)
- MainScreen pushed on top (popped with Escape)
- Modals pushed as overlays (dismissed with result)

### Reactive State Pattern
Uses Textual's `reactive` decorator for state management:
```python
selected_index = reactive(0, init=False)

def watch_selected_index(self, new_index: int) -> None:
    self._update_selection()
```
Changes trigger watchers that update UI components.

### Pure Utility Functions
Formatting and status functions in `utils/` have no UI dependencies:
- Can be tested independently
- Reusable across screens
- Centralized in `__init__.py` for clean imports

### Data Loading Strategy
- Lazy loading: Units only loaded when drilling into a chunk
- Limited display: Max 500 units for performance
- Manifest is source of truth for run state

### Subprocess Detachment
Background processes use `start_new_session=True`:
```python
subprocess.Popen(cmd, stdout=log_file, stderr=log_file, start_new_session=True)
```
This allows the orchestrator to continue after TUI exits.

## 5. Key Patterns & Conventions

### Widget Update vs Recreate
To avoid `DuplicateIds` errors with Textual's async removal:
```python
# GOOD: Update existing widget
try:
    widget = self.query_one("#my-widget", Static)
    widget.update(new_content)
except NoMatches:
    self.mount(Static(new_content, id="my-widget"))

# BAD: Remove and recreate (can cause DuplicateIds)
container.remove_children()  # Async!
container.mount(Static(content, id="my-widget"))  # May conflict
```

### Focus Management
Three-layer system:
1. **Screen level**: Tab switches between modals
2. **Panel level**: Up/Down switches between top/bottom panels
3. **Item level**: Arrow keys within lists/tables

### CSS Conventions
- ID selectors for specific elements: `#header`, `#footer`
- Class selectors for reusable styles: `.stats-card`, `.selected`
- Rich markup for text: `[bold]`, `[green]`, `[dim]`

### Error Notifications
Use `self.app.notify()` or `self.notify()`:
```python
self.app.notify("Run started successfully", severity="information")
self.app.notify("Failed to resume run", severity="error")
```

## 6. Recent Changes

### Archive Feature & Dead Code Cleanup (Latest)
- **Archive/Unarchive**: `X` key on home screen and detail screen archives terminal runs (moves to `runs/_archive/`); `H` key toggles show/hide archived runs on home screen. `ArchiveConfirmModal` in `screens/modals.py` provides confirmation dialog. `scan_runs()` in `utils/runs.py` accepts `include_archived` parameter to include `runs/_archive/` in scan results. Only terminal runs (complete/failed) can be archived — running runs are rejected with a safety check.
- **Dead code removed**: `widgets.py` (shadowed by `widgets/` package — 4 unused Message subclasses), `styles.py` (CSS constants and `combine_css()` never imported by any screen)

### Pipeline Funnel Display, Retry .bak, Otto Narrator
- **Pipeline funnel display**: Each step shows per-step throughput (Valid/Input) instead of global progress; `_count_step_valid()` scans `{step}_validated.jsonl` on disk; Step 1 input = total_units, Step N input = valid from Step N-1; passed as `step_funnel` dict to `render_pipeline_boxes()`; flag emoji uses funnel data when available
- **Retry .bak mechanism**: `reset_unit_retries()` creates `.bak` signal before modifying failures; orchestrator's `retry_validation_failures()` rotates failures to `.bak` and resets chunk state; `.bak` signals idempotency bypass in `run_step_realtime()`
- **Otto status narrator**: `OttoOrchestrator.update_narrative()` called from `_diff_chunk_states()` every refresh tick and immediately on mount; extracts providers from run config; handles terminal states with actionable text: complete ("All done!" or "Done — N failures. Press R to retry."), failed ("Stopped at {step}" or "Run failed. Check logs (L)"), paused ("Run paused. Press R to resume."), zombie ("Process lost. Press R to resume."); running uses provider-specific messages ("Waiting for Gemini..."). Otto's face reflects state: `INNER_HAPPY` for clean complete, `INNER_DEAD` (✗ _ ✗) for failed/zombie, `INNER_SLEEPY` for paused. `set_mood(face)` on OttoWidget sets a persistent face override that takes priority over all other face logic. Zombie detection on mount via `get_process_health()` — manifest "running" + dead process → synthetic "zombie" status
- **Splash screen key passthrough**: Keys typed during splash pass through to underlying HomeScreen; transparent ModalScreen overlay doesn't capture input

### Validation vs Hard Failure Differentiation & TUI Polish
- **Failure categorization**: `_count_step_failures()` returns `{"validation": N, "hard": N, "total": N, "retrying": N, "exhausted": N, "max_retry_attempt": int, "max_retries": int}` based on `failure_stage` and `retry_count` fields; compares `retry_count` against `max_retries` from config snapshot; treats all as exhausted when orchestrator not running (checked via `_last_manifest_status`). `max_retries` cached in `_cached_max_retries` (loaded from `{run_dir}/config/config.yaml`, default 5). `_last_manifest_status` initialized from manifest at the top of `on_mount()` before any rendering calls — prevents brief "exhausted" flash on initial render of running pipelines.
- **Retry state visibility**: Pipeline boxes show `↻ N retrying` (yellow) for auto-retrying failures and `⚠ N exhausted` (dark_orange) for exhausted failures separately; sidebar shows "Retrying: N (attempt M/max)" and "Failed: N (max retries)"; unit table shows `↻ retrying (M/N)` for active retries, `⚠ failed` for exhausted validation, `✗ failed` for hard failures. Terminal runs show all validation failures as exhausted. All failure text fits within `box_width=18` — `_format_count()` abbreviates large numbers (1000→1K, 1500→1.5K).
- **Retry validation only**: `action_retry_failures()` filters to validation failures (`schema_validation`/`validation`); hard failures (`pipeline_internal`) skipped with notification; footer retry hint shows retryable count only
- **Home screen "complete ⚠"**: Completed runs with failures show "complete ⚠ (N)" status text; Failed column shows yellow for complete runs (validation), red for failed runs (hard)
- **Otto the Octopus**: Animated mascot widget in detail screen right column; OttoOrchestrator bridges chunk state changes to tentacle transfer animations with per-run color coding; responsive: hidden on terminals < 120 columns
- **Splash screen**: Toast-styled bottom-right overlay with animated Otto; auto-dismisses after 10 seconds; Escape or X button to close; pushed from `OctobatchApp.on_mount()`
- **Scrollable stats sidebar**: Stats panels wrapped in `VerticalScroll`; Otto stays fixed above
- **Batch pipeline boxes**: Show "📤 N processing" / "⏳ N pending" instead of "● 0/3" when batch chunks haven't produced results
- **Batch patience toast**: Provider-aware reassuring toast when batch idle >60 seconds; reads config to detect provider
- **Progress/ETA fix**: Three-branch display for complete (green 100%), failed (red N%), running (with ETA)
- **Recent Activity refresh fix**: Restart refresh loop timers on retry/resume
- **Log viewer key bindings**: S=save, C=copy, P=copy path in LogModal
- **"Use Pipeline Config" default**: New run modal defaults to pipeline config instead of forcing provider selection
- **Removed welcome toast**: Old `self.notify("Welcome to Octobatch")` replaced by splash screen

### v1.0 TUI Enhancements
- **API key warning**: Non-blocking warning on home screen when GOOGLE_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY are missing
- **Provider/model dropdowns**: New Run modal includes provider and model selection from registry
- **Cheapest model auto-selection**: Models sorted by cost (input + output per million), cheapest pre-selected
- **Scrollable modal**: New Run modal uses VerticalScroll for short terminals
- **Threaded unit table refresh**: `_reload_realtime_progress()` uses background thread to prevent UI blocking
- **Provider/model validation**: Start button validates provider and model are selected before launching
- **Expression step display**: Pipeline visualization shows expression steps without template/schema indicators

### Failure Inspection & Unit Detail Improvements
- **UnitDetailModal view modes**: Three view modes accessible via keybindings:
  - `T`: Tree view (input/context data)
  - `R`: Raw JSON view (full failure record)
  - `L`: LLM Response view (raw_response from failed validation)
- **raw_response display**: Failed units with `raw_response` field show the LLM's actual output that failed validation
- **Selected Step stats fix**: Panel now uses `_count_step_failures()` for actual failure counts from disk, shows "Processing" count separately
- **Max Units display**: Run Stats panel shows "Max Units: N" when max_units was applied during init

### Auto-Polling & Pause Functionality
- **Async startup**: HomeScreen renders immediately, launches `_start_background_scan()` in a background thread via `@work(thread=True)`. DataTable populates when scan completes. No synchronous blocking on mount.
- **Auto-polling**: HomeScreen polls for manifest changes every 2.5 seconds in a background thread
- **Pause action**: `P` key sends SIGINT to gracefully pause running orchestrator
- **Batch timing**: Active run cards show "Last: Xs | Next: Ys" for batch mode
- **Step progress**: Active run cards show current step and progress (e.g., "generate_dialog 3/9")
- **Pipeline name**: Active run cards display pipeline name from manifest
- **Navigation change**: `L` for pipelines (was `P`), `P` now pauses

### MainScreen Improvements
- **Default view**: Opens in Unit View instead of Chunk View
- **Chunk column**: Unit View table includes chunk name column
- **Footer on mount**: Footer correctly updates when screen loads

### Run Categorization
- **Paused runs**: Now appear in Recent Runs section (not Active)
- **Completion sorting**: Recent runs sorted by completion/failure time (most recent first)

### Error Visibility & Process Status
- **MainScreen Process Status Panel**: Shows RUNNING/STUCK/Not running with PID
- **MainScreen Errors Column**: Chunk table shows error count per chunk
- **ProcessInfoScreen Enhancements**: STUCK detection, log copying (C/E keys)
- **Failure Notifications**: Only NEW failures trigger toasts (not historical on startup)

### Navigation Refactoring
- Process Info (I key) moved from HomeScreen to MainScreen only
- Consistent Q to quit from all screens
- K key on HomeScreen: kills live process OR marks zombie as failed

### Detached Run Detection
Runs without explicit `status: "running"` but with non-terminal chunk states are marked as "detached" with yellow warning indicator.

### Resume Capability (R Key)
HomeScreen can resume detached/paused runs:
- Detects mode (batch/realtime) from manifest
- Launches appropriate orchestrator command
- Appends to `orchestrator.log`

### Status Symbols
Extended symbol set:
- `✓` (green): Complete
- `✗` (red): Failed
- `●` (green): Running (active)
- `⚠` (yellow): Stuck/Detached
- `⏸` (cyan): Paused
- `○` (dim): Pending
- `💀` (red): Zombie

### Layout Ordering Fix
Active Runs section now always appears above Recent Runs, using `mount(widget, before=other)`.

### DataTable Scroll Fix
Recent Runs table now scrolls parent VerticalScroll to keep selected row visible.

## 7. Current State & Known Issues

### Working Features
- Dashboard with stats, active runs, recent runs
- Run creation with NewRunModal
- Pipeline navigation and chunk/unit inspection
- Resume capability for detached/paused runs
- Pipeline configuration editor
- Process status visibility (RUNNING/STUCK/ZOMBIE)
- Error count visibility in chunk table
- Log copying from ProcessInfoScreen
- Smart failure notifications (no toast spam on startup)
- Expression step pipelines display correctly in run detail view

### Known Limitations
- No real-time progress updates (requires manual refresh)
- Large unit lists capped at 500 for performance

### Technical Debt
- Some screens have large CSS blocks inline
- Could extract more shared components to widgets/

## 8. Testing

### Manual Testing
```bash
# Start TUI without specific run
python -c "from scripts.tui import run_tui; run_tui()"

# Start TUI with specific run
python -c "from scripts.tui import run_tui; run_tui('runs/test_run')"

# Enable debug logging
python -c "from scripts.tui import run_tui; run_tui(debug=True)"
```

### Key Test Scenarios
1. **Dashboard Load**: Verify stats cards populate correctly
2. **Run Selection**: Navigate with arrows, open with Enter
3. **New Run**: Press N, fill form, verify subprocess starts
4. **Resume**: Select detached run, press R, verify orchestrator launches
5. **Navigation**: Enter drills down, Escape goes back
6. **Refresh**: Press R (lowercase) to reload data

### Debug Log
Check `tui_debug.log` in working directory for detailed operation logs.
