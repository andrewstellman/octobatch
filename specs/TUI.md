# CURRENT_TUI.md — As-Is Intent Specification

## Purpose

This document describes what Octobatch's Terminal User Interface **should** do based on our design discussions. Written from intent, not code inspection. Gaps between this spec and the actual implementation are defects.

**Quality mapping:** Maps to QUALITY.md Scenario 5 (PID File Alignment) for process detection, and Scenario 6 (Batch Mode Observability) for status display.

---

## Architecture

The TUI is built on the Textual framework and provides a dashboard for monitoring runs, creating new runs, managing pipelines, and inspecting results. It runs in the terminal and communicates with the orchestrator entirely through the filesystem (manifest files, log files, PID files).

### Key Principle: Manifest as Source of Truth

The TUI never communicates directly with the orchestrator process. All state is read from `MANIFEST.json`. The orchestrator writes manifest updates atomically; the TUI reads them on a polling cycle.

### Package Structure

```
scripts/tui.py              # CLI entry point (interactive + --dump flags)
scripts/tui_dump.py          # Headless dump functions (no Textual dependency)
scripts/tui/
├── app.py              # Main Textual app
├── data.py             # Data models (RunData, StepStatus, etc.)
├── modals.py           # DetailModal, UnitDetailModal, FailureDetailModal, LogModal, ArtifactModal
├── styles.py           # Shared CSS components
├── widgets.py          # Re-export module
├── screens/
│   ├── home_screen.py      # Dashboard
│   ├── main_screen.py      # Run detail
│   ├── splash_screen.py    # Startup splash
│   ├── new_run_modal.py    # Run creation form
│   ├── common.py           # Shared screen utilities
│   ├── process_info.py     # Process info screen
│   └── run_launcher.py     # Run launcher utilities
├── config_editor/          # Pipeline configuration viewer
│   ├── list_screen.py
│   ├── edit_screen.py
│   ├── models.py
│   └── modals.py           # Config editor modals
├── utils/              # Pure utility functions
│   ├── runs.py         # Run scanning, status detection
│   ├── pipelines.py    # Pipeline discovery
│   ├── formatting.py   # Cost, token, progress formatting
│   └── diagnostics.py  # Comprehensive diagnostic report generation
└── widgets/
    ├── otto_widget.py       # Animated Otto the Octopus mascot
    ├── otto_orchestrator.py # Pipeline event → animation adapter
    ├── pipeline_view.py     # Pipeline step box rendering
    ├── stats_panel.py       # Stats panel widgets
    └── progress_bar.py      # Progress bar widget
```

### Launch Flags

`tui.py` accepts the following flags:

| Flag | Description |
|------|-------------|
| `--debug` | Enable debug logging to `tui_debug.log`. Logs widget lifecycle events, polling activity, and key bindings for troubleshooting. |
| `--dump` | Render screen data to stdout and exit without launching the interactive terminal. Outputs a formatted text table of all runs (home view) or run detail. No Textual dependency required. |
| `--run-dir PATH` | Used with `--dump` to show detail for a specific run instead of the home summary. |
| `--json` | Used with `--dump` to output JSON instead of formatted text. Machine-readable format for scripting and CI verification. |

### Headless Dump Mode (`--dump`)

The `--dump` flag renders run data to stdout without launching the interactive Textual terminal. This is useful for CI pipelines, scripting, and verifying run state without a TTY.

**Implementation:** Dump functions live in `scripts/tui_dump.py` (separate from the `tui/` package to avoid import collisions). They use the same utility layer (`tui.utils.runs`, `tui.data`) as the interactive TUI but have no Textual dependency.

**Home view (no `--run-dir`):**
```bash
python scripts/tui.py --dump          # Formatted text table
python scripts/tui.py --dump --json   # JSON array of run objects
```

Text output shows: Run name, Progress %, Units, Valid, Fail, Cost, Tokens, Mode, Status.

JSON output includes: `name`, `status`, `progress`, `total_units`, `valid_units`, `failed_units`, `cost`, `total_tokens`, `mode`, `duration`, `pipeline_name`, `started`.

**Run detail view (`--run-dir`):**
```bash
python scripts/tui.py --dump --run-dir runs/my_run          # Formatted text
python scripts/tui.py --dump --run-dir runs/my_run --json   # JSON object
```

Text output shows: Run name, status, pipeline, provider/model, mode, unit counts, cost, tokens, duration, and a pipeline steps table.

JSON output includes all text fields plus per-step breakdown with `name`, `valid`, `total`, and `state`.

---

## Screens

### Home Screen (Dashboard)

The primary screen showing all runs, statistics, and navigation.

#### Layout

```
┌──────────────────────────────────────────────────────┐
│ Octobatch                                     [header]│
├──────────┬──────────┬──────────┬──────────┬──────────┤
│Total Runs│  Tokens  │   Cost   │ Pipelines│   [stats]│
├──────────┴──────────┴──────────┴──────────┴──────────┤
│ Unified DataTable                                     │
│ │ ⠋ │ running_run   │ 45%│ 50│ 23│  2│$1.2│02:15│…  │
│ │ ✓ │ completed_run │100%│600│596│  0│ $12│15:30│…  │
│ │ ⚠ │ partial_run   │100%│600│596│  4│ $12│15:30│…  │
│ │ ✗ │ failed_run    │  0%│ 50│  0│  0│  --|00:05│…  │
├──────────────────────────────────────────────────────┤
│ N:new  Enter:open  P:pause  R:resume  K:kill  Q:quit │
└──────────────────────────────────────────────────────┘
```

#### Unified DataTable

All runs appear in a single table (active runs above recent runs) with columns:
- Run name
- Progress percentage
- Units (total)
- Valid count
- Fail count
- Cost
- ETA (estimated time remaining for running runs, computed from summary timestamps using linear projection: `elapsed * (100 - progress) / progress`; shows "—" for non-running runs)
- Duration
- Mode (batch/realtime/mixed)
- Started timestamp
- Status with symbol

#### Status Display

| Symbol | Color | Status Text | Meaning |
|--------|-------|-------------|---------|
| ⠋/◐ (spinner) | green | `running (step)` | Orchestrator actively processing |
| ✓ | green | `complete` | All units validated, zero failures |
| ⚠ | yellow | `complete ⚠ (N)` | Finished with N validation failures (retryable) |
| ✗ | red | `failed` | Run-level failure or hard failures |
| ? | yellow | `detached` | No active process, may need resume (ASCII `?`, not Unicode `⸮`) |
| ⏸ | cyan | `paused` | User interrupted with SIGINT |
| ⏳ | cyan | `pausing` | SIGINT sent, waiting for manifest to update |
| 💀 | red | `zombie` | Manifest says running but process is dead |
| ○ | dim | `pending` | Run initialized but not yet started |
| ✗ | red | `killed` | User terminated a zombie process via TUI |

#### Key Bindings

| Key | Action |
|-----|--------|
| Q/q | Quit |
| N/n | Open New Run modal |
| Enter | Open selected run in detail view |
| P/p | Pause running run (sends SIGINT, 5-second toast with PID and resume hint; if process doesn't exit within 5 seconds, shows force-kill hint) |
| R | Resume detached/paused run (on a failed run with all chunks at initial PENDING, triggers resume instead of retry) |
| K/k | Kill process or mark zombie as failed |
| L/l | Open Pipeline Editor |
| D/d | Delete run (not yet implemented, hidden binding) |
| Ctrl+R / F5 | Refresh |
| ↑/↓ | Navigate runs |

#### Auto-Polling

- Initial data loading runs in a background thread. The home screen renders immediately with an empty DataTable; the table populates when the scan completes.
- Auto-poll refresh also runs in a background thread to prevent UI freezes on large runs.
- Polls every 2.5 seconds for manifest file changes
- Uses file modification time comparison to avoid unnecessary reloads
- Auto-refreshes UI when data changes detected

#### API Key Warning

Non-blocking warning displayed when `GOOGLE_API_KEY`, `OPENAI_API_KEY`, or `ANTHROPIC_API_KEY` are missing. Informational only — doesn't block TUI functionality.

#### Failure Notification

- Tracks which runs have already shown failure notifications
- Only NEW failures (detected after app start) trigger toast notifications
- Prevents toast spam on startup from historical failures

---

### Run Detail Screen (Main Screen)

Detailed view of a single run with pipeline visualization, unit table, and stats.

#### Layout

```
┌──────────────────────────────────────────────────────┐
│ Run: tarot_major_arcana                       [header]│
├───────────────────────────┬──────────────────────────┤
│                           │  [Otto Widget]           │
│  Pipeline Visualization   │──────────────────────────│
│  ┌──────┐ ┌──────┐ ┌──…  │  Run Stats               │
│  │ gen  │→│score │→│…    │  Status: Running          │
│  │95/100│ │80/95 │ │     │  Mode: Batch              │
│  └──────┘ └──────┘ └──…  │  Cost: $1.23              │
│                           │  Tokens: 50K/15K          │
│  Unit Table               │  Duration: 02:15:30       │
│  │unit_id     │status│…  │──────────────────────────│
│  │fool-mag-pr │valid │…  │  Selected Step            │
│  │mag-pr-emp  │failed│…  │  Passed: 95               │
│  │…           │…     │…  │  Validation: 5 (yellow)   │
│                           │  Failed: 0 (red)          │
├───────────────────────────┴──────────────────────────┤
│ ←→:step  ↑↓:unit  Enter:detail  R:retry  D:diag     │
└──────────────────────────────────────────────────────┘
```

#### Pipeline Visualization (Funnel Display)

Horizontal boxes showing each pipeline step:

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│     GENERATE     │────▶│ SCORE COHERENCE  │────▶│  SCORE WOUNDS    │
│   ● 406/500      │     │   ● 262/406      │     │   ● 222/262  🏁 │
└──────────────────┘     └──────────────────┘     └──────────────────┘
```

Each step shows:
- Step name
- Valid/Input count (input = valid from previous step)
- 🏁 flag when step is fully complete (valid == input)
- Expression steps displayed without template/schema indicators

For batch mode, boxes show `📤 N processing` / `⏳ N pending` instead of realtime counts.

Failure rows within each box:
- Yellow `⚠ N valid. fail` for validation failures (retryable)
- Red `✗ N failed` for hard failures (not retryable)

#### Unit Table

Displays individual units with their status. Features:
- Toggle between Chunk View and Unit View with V key
- Filter by status with F key (all/valid/failed)
- Sort by column with S key
- Lazy loading — implemented with a ~100 unit window around the cursor, handles 9,000+ unit runs without performance issues
- Threaded background loading to prevent UI blocking
- "Loading..." indicator while async load in progress

#### Stats Panel (Right Sidebar)

Scrollable panel showing:
- Run status
- Mode (batch/realtime/mixed)
- Max Units (if applied during init)
- Provider/Model
- Token counts (input/output)
- Estimated cost
- Duration
- For realtime runs: throughput (seconds per unit) and current unit being processed
- Per-step stats: Passed, Validation failures (yellow), Hard failures (red)
- "Processing" count shown separately from failures
- Failure Summary section groups failures by error type for the selected step, showing top patterns with counts (cached per step, invalidated on file modification time changes)

#### Otto Widget

Animated octopus mascot in the right sidebar:
- 8fps animation with tentacle transfers, expressions, body sway, bubbles
- OttoOrchestrator bridges pipeline events to animations
- `_diff_chunk_states()` in `main_screen.py` (lines 825-892) detects chunk advances, retries, completions. OttoOrchestrator receives events from main_screen but doesn't contain the diff logic.
- Responsive — hidden on narrow terminals via `on_resize()`
- Narrative text below Otto describes current state:
  - "Waiting for Gemini..." — single Gemini provider active
  - "Waiting for Claude..." — single Anthropic provider active
  - "Otto is orchestrating..." — multiple providers or unknown
  - "All done! Everything passed." — run complete
  - "Run failed. Check logs (L) for details." — run failed

#### Key Bindings

| Key | Action |
|-----|--------|
| ←/→ | Select pipeline step |
| ↑/↓ | Navigate unit table |
| Enter | Open unit detail modal |
| V | Toggle Chunk/Unit view |
| A | Open artifact viewer |
| L | Open log viewer |
| F | Filter units by status |
| S | Sort units |
| R | Retry validation failures |
| C/c | Show config |
| D | Run diagnostic |
| I | View process info (PID, CPU, memory) |
| Escape | Return to home screen |
| Q | Quit |

#### Retry Behavior

When R is pressed:
1. Only validation failures (`schema_validation`, `validation`) are retried
2. Hard failures (`pipeline_internal`) are skipped with a notification
3. Footer shows retryable count: `R:retry(94)`
4. Behind the scenes: failures rotated to `.bak`, chunk state reset to PENDING
5. Guard: checks if orchestrator process is running before modifying manifest

#### Auto-Refresh for Running Runs

- Pipeline and stats refresh every 2 seconds (lightweight)
- Unit table refresh every 5 seconds (background thread)
- Refresh stops after first tick for terminal (complete/failed) runs
- `_do_refresh()` reloads RunData every tick for running runs

---

### Unit Detail Modal

Modal overlay showing full details for a selected unit.

#### View Modes

| Key | View | Shows |
|-----|------|-------|
| T | Tree view | Structured display of input and context data |
| R | Raw JSON | Full JSON record for debugging |
| L | LLM Response | The `raw_response` field — the actual LLM output that failed validation |

The LLM Response view is the key debugging tool. It shows exactly what the LLM returned before any parsing, merging, or transformation.

---

### Splash Screen

Toast-styled overlay shown on app startup:
- Bottom-right position
- Animated Otto with tentacle transfers and flag wave
- Auto-dismisses after 10 seconds
- Close with Escape or X button
- Keys typed during splash pass through to underlying HomeScreen (transparent ModalScreen)

---

### New Run Modal (Launchpad)

Form for creating and launching a new run.

#### Fields

- **Pipeline selector** — populated from `pipelines/` folder
- **Provider dropdown** — from model registry, with "Use Pipeline Config" as default
- **Model dropdown** — sorted by cost (cheapest pre-selected), filtered by selected provider
- **Mode selector** — Batch or Realtime
- **Max Units input** — for testing with a subset of data
- **Run directory** — auto-generated or custom path

#### Behavior

- Selecting a pipeline pre-selects its configured provider/model
- "Use Pipeline Config" maps to `None` overrides so step-level config takes effect
- Provider and model validated before Start button enables
- Scrollable form for short terminals

#### Launch Process

- **Batch mode:** Two-step launch. `--init` first (blocking, creates manifest), then `--watch` (background subprocess). This separation ensures the TUI can verify initialization succeeded before starting the watcher.
- **Realtime mode:** Combined `--init --realtime` in a single command (allowed because `--realtime` is outside the mutually exclusive group).

After launch: 1.5-second delayed refresh to detect the new run.

---

### Pipeline Editor (Config Editor)

Browse and inspect pipeline configurations.

#### Screens

- **ConfigListScreen:** Lists all pipelines in `pipelines/` folder
- **ConfigEditScreen:** Split-panel layout showing pipeline steps and their details

#### Layout

```
┌──────────────────────────────────────────────────────┐
│ Pipeline: Tarot                               [header]│
├──────────────────────────────────────────────────────┤
│  Pipeline Visualization (top)                         │
│  ┌──────┐ ┌──────┐ ┌──────┐                         │
│  │ gen  │→│score │→│wound │  ← selected              │
│  └──────┘ └──────┘ └──────┘                         │
├──────────────────────────────────────────────────────┤
│  Step Details (bottom)                                │
│  Template: score_coherence_prompt.jinja2              │
│  Schema: score_coherence.json                         │
│  Description: Score story coherence on 1-3 scale     │
│  Validation rules: ...                                │
├──────────────────────────────────────────────────────┤
│ ←→:step  ↑↓:property  V:template  C:copy context    │
└──────────────────────────────────────────────────────┘
```

#### Key Bindings

| Key | Action |
|-----|--------|
| ←/→ | Navigate steps |
| ↑/↓ | Navigate properties |
| V | View template (read-only) |
| C | Copy context to clipboard (step config + template) |
| Escape | Back to home |

#### Design Decision: Copy Context, Not Edit

The TUI doesn't include a text editor. Instead, C copies the step's config and template to the clipboard for pasting into Claude Code or Cursor. The TUI's job is visualization and context export, not editing.

---

### Artifact Viewer

Browse output files in the run directory. Press A from run detail.
- Directory tree navigation
- File content viewer for text files
- Copy content to clipboard

---

### Log Viewer

View `RUN_LOG.txt` from run detail. Press L.

| Key | Action |
|-----|--------|
| C | Copy log content |
| S | Save log to file |
| P | Copy log file path |

Uses `markup=False` to prevent Textual from interpreting log content as Rich markup. Implemented as standalone ModalScreen.

---

### Process Info Screen

View orchestrator process details. Press I from run detail.

Shows:
- PID
- CPU usage
- Memory usage
- Process status (RUNNING/STUCK/ZOMBIE)
- Requires `psutil` package

| Key | Action |
|-----|--------|
| C | Copy last 100 log lines |
| E | Copy unique error lines |

#### STUCK Detection

A run is flagged as `⚠ STUCK` when errors are found in the recent log lines with no subsequent recovery events. `has_recent_errors()` compares the line index of the most recent `[ERROR]` against the most recent recovery event (`[POLL]`, `[SUBMIT]`, `[COLLECT]`, `[VALIDATE]`, `[STATE]`, `[TICK]`, etc.) in the last 50 log lines. If a recovery event is more recent than the last error, the run has recovered and is not stuck.

---

### Diagnostics Screen

Press D from run detail to open the Diagnostics Screen.

#### Sections

**Run Health:** Per-step completeness table showing expected, valid, validation failures, and hard failures — all counts from disk scanning (not manifest). Compares disk counts against manifest to detect discrepancies.

**Error Analysis:** For the selected step, groups failures by category and error message with counts. Shows sample failures with unit_id, stage, error details, and truncated raw response.

**Disk Verification:** Reports discrepancies between manifest state and actual disk files.

#### Key Bindings

| Key | Action |
|-----|--------|
| ←/→ | Select step for error analysis |
| V | Re-validate failures for selected step |
| S | Save diagnostic report to DIAGNOSTIC.md |
| C | Copy diagnostic to clipboard |
| Escape | Back to run detail |

#### Async Loading

Disk scanning runs in a background thread using `@work(thread=True)`. The screen displays immediately with a "Scanning..." indicator; data populates as it becomes available.

#### Re-Validate Subprocess

The V action launches `--revalidate` in a background thread via `@work(thread=True)` to avoid freezing the Textual event loop. A progress indicator renders while the subprocess runs. The screen refreshes automatically on completion.

---

## Process Management

### Process Detection

The TUI determines process status by:
1. Reading the PID from the manifest and/or PID file
2. Using `psutil` to check if the process is alive
3. Classifying as RUNNING, STUCK, ZOMBIE, or LOST

### Pausing (P key)

1. Sends SIGINT to the orchestrator process
2. Shows "⏳ Pausing..." indicator via `_pausing_run_ids` tracking
3. Polls manifest until status changes to "paused"
4. 2-second delayed refresh after pause

### Resuming (R key)

Only enabled for detached or paused runs. Launches `--watch` (batch) or `--realtime` as a background subprocess. The orchestrator reads the existing manifest and picks up where it left off.

### Killing (K key)

- If process is alive: kills the process
- If zombie (no process but manifest says running): marks manifest status as "killed"
- Orphaned processes (discovered without PID file) can also be killed

### Manifest Consistency Auto-Fix

`check_manifest_consistency()` runs on the TUI's 5-second progress tick:
- Detects runs where all chunks are terminal but manifest still says "running"
- Auto-corrects to "complete"
- Logs `[AUTO-FIX]` message

---

## Utility Layer

### Manifest Summary Cache

`scan_runs()` reads `.manifest_summary.json` (a lightweight ~300-byte file) instead of full MANIFEST.json files for the home screen DataTable. Summaries are written atomically by the orchestrator alongside every manifest update. For runs without summaries (pre-upgrade), `scan_runs()` falls back to loading the full manifest. The full manifest is only loaded when the user drills into a specific run.

### Pure Functions (No Textual Dependencies)

All utility functions in `utils/` are pure — no Textual imports, testable with standard pytest.

#### runs.py

- `scan_runs()` — Scan `runs/` directory for all runs
- `get_active_runs()` — Runs with status in {active, detached, paused}
- `get_recent_runs()` — Runs with status in {complete, failed, pending}
- `get_run_status()` — Derive status from manifest
- `calculate_dashboard_stats()` — Aggregate stats for home screen
- `reset_unit_retries()` — Archive failures and reset chunks for retry (creates `.bak` signal)

#### pipelines.py

- `scan_pipelines()` — Discover pipelines in `pipelines/` folder
- `load_pipeline_config()` — Load and parse config.yaml

#### formatting.py

- `format_cost()` — Cost with dollar sign, handles None/zero
- `format_tokens()` — Token count with K/M suffixes
- `format_progress_bar()` — Unicode block progress bar

#### status.py

- `get_status_symbol()` — Unicode symbol for status
- `get_status_color()` — Color name for status
- `parse_chunk_state()` — Parse "step_STATUS" into (step, status, index)
- `determine_step_status()` — Complete/in_progress/pending for a step
- `determine_run_status()` — Overall run status from step statuses

**Two status functions with different scopes:** Run status is determined by two functions with different scopes. `get_run_status()` in `runs.py` is used by the home screen and returns statuses including 'paused', 'detached', and 'zombie' based on process liveness checks. `determine_run_status()` in `status.py` derives status from step-level state and is used for internal pipeline progress tracking.

### Data Models (data.py)

Dataclasses for structured data:
- `RunData` — Complete run information (run_dir, pipeline, steps, chunks, costs)
- `StepStatus` — Pipeline step status with symbol/progress properties
- `ChunkStatus` — Chunk progress information
- `Failure` — Validation failure records
- `UnitRecord` — Individual unit with status

`load_run_data(run_dir)` loads MANIFEST.json into a RunData object.

### Shared CSS (styles.py)

CSS components combined with `combine_css(*parts)`:
- `HEADER_CSS`, `FOOTER_CSS`
- `MODAL_CSS`, `FORM_CSS`
- `STATS_PANEL_CSS`, `TABLE_CSS`, `PANEL_CSS`

---

## Design Decisions

### Why Textual?

Terminal-native, no browser required, works over SSH, rich widget library.

### Why CSS Borders Instead of ASCII Art?

Textual's CSS handles responsive layouts properly. ASCII art breaks at different terminal widths.

### Why "Detached" Instead of "Failed"?

Batch jobs run on remote servers. Closing your laptop doesn't kill a Google Batch job. The run might still be processing successfully. "Detached" means "no local process, but the remote work may be fine."

### Why Copy Context Instead of In-TUI Editing?

Building a full text editor is complex and duplicates what Claude Code / Cursor already do well. The TUI's job is visualization and context export.

### Why Recursive set_timer Instead of set_interval?

Textual's `set_interval()` callbacks are not reliably serviced on pushed screens. Recursive `set_timer()` (schedule next tick after current tick completes) works correctly for auto-refresh on screens added via `push_screen()`.

---

## Batch Mode Specific Behaviors

### Patience Toast

When a batch run has been idle for >60 seconds, a provider-aware reassuring toast appears (e.g., "Waiting for Gemini to process your batch..."). Reads config to detect which provider(s) are active.

### Pipeline Box Display

In batch mode, pipeline boxes show submission-oriented info:
- `📤 N processing` — N chunks submitted and being processed
- `⏳ N pending` — N chunks waiting to be submitted

### Progress and ETA

- Complete runs show "100% / Complete"
- Failed runs show "N% / Failed"
- Running runs show percentage and projected completion time

---

## Known Issues and Technical Debt

1. **PID detection bugs** — TUI sometimes shows "Active Run" after process is killed. Hard to distinguish Paused vs Crashed vs Stale. `get_process_health()` logic may need improvement. The PID file and manifest PID can diverge on resume (QUALITY.md Scenario 5).

2. **Delete run not implemented** — K kills the process but doesn't delete the run directory. Shows a warning.

3. **No real-time streaming** — TUI polls manifests on a timer. There's no streaming/push mechanism. Updates appear at the polling interval, not instantly.

4. **Large CSS blocks inline** — Some screens have large CSS blocks that could be extracted to `styles.py`.

5. **Mixed mode display** — A run started in batch and resumed in realtime shows "mixed" mode, but the TUI doesn't differentiate which chunks used which mode.

6. **Splash screen key passthrough** — Keys typed during the splash animation pass through to the underlying HomeScreen. This is intentional (transparent ModalScreen) but can cause unintended actions if the user types before the splash closes.

7. ~~**No automated TUI tests**~~ — Automated tests now exist in `tests/test_tui.py` using Textual's `run_test()` framework. Tests cover: HomeScreen loading, DataTable presence and population, Enter/Escape navigation, and headless dump mode (text and JSON output for both home and run detail views).

8. **Memory/CPU indicator in header** — Both HomeScreen and MainScreen display TUI process resource usage (MEM/CPU via `psutil.Process()`) in the header bar, updated every 2–5 seconds. This is diagnostic info, not primary content.
