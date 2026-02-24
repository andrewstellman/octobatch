# Widgets Directory Context
> **File:** `scripts/tui/widgets/CONTEXT.md`

## 1. Purpose

This directory contains reusable Textual widget components for the Octobatch TUI. Widgets are self-contained UI elements that handle their own rendering and animation, composed into screens by the TUI framework.

## 2. Key Components

### otto_widget.py (~882 lines)
Procedural animation system for Otto the Octopus mascot.

**Key Classes:**

| Class | Purpose |
|-------|---------|
| `OttoState` | Pure logic animation state (no Textual dependency) |
| `OttoWidget` | Textual widget wrapping OttoState with timer-driven rendering |
| `TentacleTransfer` | Dataclass tracking a block's journey through tentacle arms |
| `SideArmState` | Left/right side arm animation state |
| `Bubble` | Rising bubble particle |

**Animation Architecture:**
- Tick rate: 8 fps (125ms per frame)
- Layout: 10 rows — 6 bubble rows + head + face + tentacles + pool
- Render width: 28 characters

**Transfer System:**
- 6 tentacle tips, each mapping to 2 arm segments (12 total)
- Blocks move through arm paths with 3-phase lift/lower motion (LOW → FULL → HIGH)
- Completed blocks collect in pools under tips (max 3 per tip)
- Colors are assigned per-run for visual consistency

**Face Expressions:**
- 8 expressions: NORMAL, FOCUS, BLINK, LOOK_LEFT/RIGHT, HAPPY, SURPRISE, SLEEPY, DEAD
- Priority: mood_face > reactive > focus > override > sleepy > normal
- `mood_face` is a persistent override set by OttoOrchestrator for terminal states

**Idle Behaviors:**
- Face expressions cycle every 2-5 seconds when idle
- Tentacle behaviors (wave, wiggle) every 5-10 seconds
- Side arm animations (extend, tip flick, flag wave, puff) periodically
- Bubbles spawn and rise upward
- Sleepy mode after 30 seconds of inactivity

### otto_orchestrator.py (~165 lines)
Bridge between pipeline events and OttoWidget animations.

**OttoOrchestrator Class:**

| Method | Purpose |
|--------|---------|
| `on_chunk_advance(run_id)` | Forward transfer (tips 1-4 → 5-6) |
| `on_chunk_retry(run_id)` | Backward transfer (tips 3-6 → 1-5) |
| `on_chunk_complete(run_id)` | Full sweep transfer (1 → 6) |
| `on_run_complete()` | Trigger flag wave animation |
| `update_narrative(status, context)` | Update mood face and status label |

**Narrative States:**
- `"complete"` → Happy mood, "All done!" or failure count
- `"failed"` → Dead mood (✗ _ ✗), failure context shown
- `"paused"` → Sleepy mood
- `"running"` → Normal face, provider-specific waiting message
- Other/zombie → Dead mood, "Process lost"

**Color Management:**
- 6-color pool: red, blue, green, yellow, magenta, cyan
- Consistent color per run_id across all transfers

### pipeline_view.py (~341 lines)
Renders horizontal pipeline as connected ASCII boxes with arrows.

**render_pipeline_boxes() Function:**
Main rendering function creating multi-line pipeline visualization.

**Box Structure (per step):**
```
┌──────────────────┐
│   step_name  🏁  │
│   ● 95/100       │
│  ↻ 3 retrying    │
│  ⚠ 2 exhausted   │
└──────────────────┘
```

**Key Parameters:**
- `box_width`: Inner width (default 18). All text must fit within this.
- `step_funnel`: Dict of `{step_name: (valid_count, input_count)}` for per-step throughput
- `get_failures()`: Returns `{"validation": N, "hard": N, "total": N, "retrying": N, "exhausted": N}`
- `get_batch_detail()`: Returns `(submitted_count, pending_count)` for batch mode

**Failure Row Colors:**
- `[yellow]↻ N retrying[/]` — Active auto-retry
- `[dark_orange]⚠ N exhausted[/]` — Max retries reached
- `[red]✗ N failed[/]` — Hard/pipeline failures

**Box Width Constraint:** All rendered text must fit `box_width` (18 chars). `_format_count()` abbreviates large numbers (10000 → "10K"). Overflow causes clipping into adjacent boxes.

**Funnel Display:** When `step_funnel` is provided, shows per-step throughput (e.g., Generate "406/500" → Score "262/406" → Wounds "222/262") instead of global progress.

### stats_panel.py (~182 lines)
Statistics display panels for the detail screen sidebar.

**RunStatsPanel:**
- Displays: Total Cost, Total Tokens, Duration, Mode
- When a step is selected: step name, Passed count, Failed count

**ChunkStatsPanel:**
- Two detail levels (toggleable):
  - Chunk view: chunk name, units valid/total, status, retries
  - Unit view: unit ID, status (✓/✗), current step

### progress_bar.py (~95 lines)
Text-based progress bar rendering using block characters.

**Constants:**
- `BLOCK_FULL = "█"`, `BLOCK_EMPTY = "░"`
- Box drawing characters: `BOX_H`, `BOX_V`, `BOX_TL`, `BOX_TR`, `BOX_BL`, `BOX_BR`
- Status symbols: `CHECK = "✓"`, `CROSS = "✗"`, `CIRCLE_FILLED = "●"`, `CIRCLE_EMPTY = "○"`
- `ARROW = "───▶"`

**Functions:**
- `make_progress_bar(current, total, width)` → `"████████░░░░░░░░░░"`
- `format_progress_percent(current, total)` → `"75%"`
- `format_progress_fraction(current, total)` → `"24/30"`

## 3. Widget Lifecycle & Refresh Patterns

### OttoWidget
1. **Mount:** `on_mount()` sets interval timer at tick rate, calls `_update_display()`
2. **Tick loop:** Every 125ms, `_tick()` → `state.tick()` updates logic, `_update_display()` refreshes UI
3. **Display update:** Query each of 10 Static children by ID and update with rendered Rich text
4. **External events:** `start_transfer()`, `trigger_flag()`, `set_mood()` modify state; next tick renders

### PipelineView
1. **Mount:** `compose()` yields Static with initial rendered pipeline
2. **Selection change:** `update_selection()` removes children, mounts new Static
3. **Data change:** `update_steps()` removes children, mounts new Static

### StatsPanel
1. Data pushed via `update_stats()` / `update_chunk_stats()` / `update_unit_stats()`
2. Each call triggers `_render()` which regenerates the display text

## 4. Architectural Decisions

### Separation of State and Widget
`OttoState` contains all animation logic with zero Textual dependencies. `OttoWidget` is a thin wrapper that drives `OttoState.tick()` on a timer and maps rendered text to Static children. This allows unit testing of animation logic without a running TUI.

### Fixed Box Width
Pipeline boxes use a fixed `box_width` of 18 characters. This prevents layout shifts when data changes and ensures boxes align properly in horizontal layout. All text must be measured with `cell_len()` (Rich markup-aware) before rendering.

### Callback-Based Rendering
`render_pipeline_boxes()` takes callbacks (`get_step_status`, `get_failures`, `get_batch_detail`) rather than data objects. This decouples rendering from data fetching and allows the same renderer to work with different data sources.

## 5. Module Exports (__init__.py)

```python
from scripts.tui.widgets import (
    PipelineView, render_pipeline_boxes,
    RunStatsPanel, ChunkStatsPanel,
    make_progress_bar, ProgressBar,
    OttoWidget, OttoOrchestrator,
)
```
