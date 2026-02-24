"""
HomeScreen for Octobatch TUI.

Dashboard layout with stats cards and a unified runs DataTable.
Active runs pinned to top with animated spinner. Auto-polling for manifest changes.
"""

import os
import signal

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Static, DataTable
from textual.binding import Binding
from textual.css.query import NoMatches
from textual import events, work

from datetime import datetime, timezone
from pathlib import Path

import json
import threading

from version import __version__
from ..utils import (
    scan_runs,
    scan_pipelines,
    calculate_dashboard_stats,
)
from ..utils.runs import (
    get_run_process_status,
    get_enhanced_run_status,
    get_run_status,
    get_run_progress,
    get_run_cost,
    get_run_unit_failure_count,
    get_process_health,
    kill_run_process,
    mark_run_as_killed,
    load_manifest,
    check_manifest_consistency,
    resume_orchestrator,
    reset_unit_retries,
)
from .common import _log, check_missing_api_keys, get_resource_stats, set_os_terminal_title


# Braille spinner frames for running rows
SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class HomeScreen(Screen):
    """Dashboard home screen with stats and unified runs table."""

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("Q", "quit", "Quit", show=False),
        Binding("tab", "noop", "", show=False),  # Disable tab
        Binding("n", "new_run", "New Run"),
        Binding("N", "new_run", "New Run", show=False),
        Binding("enter", "open_run", "Open"),
        Binding("d", "delete_run", "Delete", show=False),  # Hidden - dangerous
        Binding("D", "delete_run", "Delete", show=False),
        Binding("k", "kill_run", "Kill"),
        Binding("K", "kill_run", "Kill", show=False),
        Binding("l", "show_pipelines", "Pipelines"),
        Binding("L", "show_pipelines", "Pipelines", show=False),
        Binding("p", "pause_run", "Pause"),
        Binding("P", "pause_run", "Pause", show=False),
        Binding("r", "resume_run", "Resume"),
        Binding("R", "resume_run", "Resume", show=False),
        Binding("x", "archive_run", "Archive"),
        Binding("X", "archive_run", "Archive", show=False),
        Binding("h", "toggle_archived", "Archived", show=False),
        Binding("H", "toggle_archived", "Archived", show=False),
        Binding("ctrl+r", "refresh", "Refresh", show=False),
        Binding("f5", "refresh", "Refresh", show=False),
    ]

    CSS = """
    HomeScreen {
        layout: vertical;
    }

    #header {
        height: 1;
        padding: 0 1;
        background: $primary;
    }

    #api-key-warning {
        height: auto;
        padding: 0 1;
        color: $warning;
        display: none;
    }

    #api-key-warning.visible {
        display: block;
    }

    #stats-row {
        height: auto;
        padding: 1;
    }

    .stats-card {
        width: 1fr;
        height: auto;
        min-width: 15;
        border: solid $surface-darken-2;
        padding: 0 1;
        margin: 0 1;
        text-align: center;
    }

    .stats-label {
        color: $text-muted;
    }

    .stats-value {
        text-style: bold;
    }

    #runs-table {
        height: 1fr;
        min-height: 0;
    }

    #footer {
        height: 1;
        dock: bottom;
        background: $surface-darken-1;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._runs_data = []
        self._pipelines_data = []
        self._sorted_runs: list[dict] = []
        self._dashboard_stats = {}
        self._spinner_index = 0
        self._has_running_runs = False
        self._col_keys = []  # DataTable column keys for cell updates
        self._notified_failures: set[str] = set()
        self._initial_load_complete: bool = False
        self._last_manifest_times: dict[str, float] = {}
        self._refresh_active: bool = False
        self._spinner_active: bool = False
        self._pausing_run_ids: set[str] = set()
        self._cached_terminal_runs: dict[str, dict] = {}
        self._last_run_dir_count: int = -1
        self._refresh_in_progress: bool = False
        self._running_row_starts: dict[str, datetime] = {}  # row_key -> started_at for duration ticker
        self._batch_wait_toasts_shown: set[str] = set()  # run names that got the queue toast
        self._show_archived: bool = False

    def _render_header(self) -> str:
        """Render header with version and resource stats on the right."""
        stats = get_resource_stats()
        if stats:
            return f"[bold]Octobatch[/] [dim]v{__version__}[/]                              [dim]{stats}[/]"
        return f"[bold]Octobatch[/] [dim]v{__version__}[/]"

    def compose(self) -> ComposeResult:
        yield Static(self._render_header(), id="header")
        yield Static("", id="api-key-warning")
        yield Horizontal(id="stats-row")
        yield DataTable(id="runs-table")
        yield Static(self._render_footer(), id="footer")

    def on_mount(self) -> None:
        """Initialize UI immediately, load data in background thread."""
        _log.debug("HomeScreen.on_mount")
        # Welcome is handled by SplashScreen overlay pushed from app.py
        # Defer title update to next event-loop tick so the screen stack
        # has registered this screen as the active one.
        self.app.call_later(self._update_terminal_title)

        # Set up table columns
        table = self.query_one("#runs-table", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        self._col_keys = [
            table.add_column("", width=3),       # 0: icon
            table.add_column("Run"),              # 1: name
            table.add_column("Progress", width=9),# 2: progress %
            table.add_column("Units", width=6),   # 3: total units
            table.add_column("Valid", width=7),    # 4: valid units
            table.add_column("Failed", width=7),   # 5: failed units
            table.add_column("Cost", width=8),     # 6: cost
            table.add_column("ETA", width=8),      # 7: estimated time remaining
            table.add_column("Duration", width=9), # 8: duration
            table.add_column("Mode", width=9),     # 9: batch/realtime/mixed
            table.add_column("Started", width=13), # 10: started
            table.add_column("Status"),            # 11: status
        ]

        # Show loading state immediately, then scan in background
        self._populate_stats_cards()
        self._check_api_keys()
        table.focus()

        # Launch background scan
        self._start_background_scan()

    def on_unmount(self) -> None:
        """Stop polling when screen unmounts."""
        self._refresh_active = False
        self._spinner_active = False

    def on_screen_resume(self) -> None:
        """Called when screen becomes active again (after being covered by another screen)."""
        _log.debug("HomeScreen.on_screen_resume - refreshing data")
        self._update_terminal_title()
        # Focus the table
        try:
            self.query_one("#runs-table", DataTable).focus()
        except NoMatches:
            pass
        # Launch background scan (will repopulate UI and restart timers on completion)
        self._start_background_scan()

    def _check_api_keys(self) -> None:
        """Check for missing API keys and display warning if needed."""
        try:
            warning_widget = self.query_one("#api-key-warning", Static)
        except NoMatches:
            return

        missing = check_missing_api_keys()

        if not missing:
            warning_widget.update("")
            warning_widget.remove_class("visible")
        elif len(missing) == 3:
            warning_widget.update(
                "[yellow]\u26a0[/] No API keys found. Set GOOGLE_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY to get started."
            )
            warning_widget.add_class("visible")
        else:
            keys_str = ", ".join(missing)
            warning_widget.update(
                f"[yellow]\u26a0[/] Missing API keys: {keys_str} (set these to use those providers)"
            )
            warning_widget.add_class("visible")

    def on_key(self, event: events.Key) -> None:
        """Intercept keys that need special handling."""
        if event.key == "tab":
            event.prevent_default()
            event.stop()
        elif event.key == "enter":
            self.action_open_run()
            event.prevent_default()
            event.stop()

    # --- Data Loading ---

    def _load_data(self) -> None:
        """Load runs and pipelines data into a single sorted list."""
        self._runs_data = scan_runs(include_archived=self._show_archived)
        self._pipelines_data = scan_pipelines()

        # Enhance status with process state for non-terminal runs
        for run in self._runs_data:
            run["status"] = get_enhanced_run_status(
                Path(run["path"]),
                run["status"]
            )

        if not self._initial_load_complete:
            for run in self._runs_data:
                if run.get("status") == "failed":
                    self._notified_failures.add(run["name"])
            self._initial_load_complete = True
        else:
            self._check_for_new_failures(self._runs_data)

        # Update terminal run cache for background refresh optimization
        terminal_statuses = {"complete", "failed", "killed"}
        self._cached_terminal_runs = {
            str(r["path"]): r for r in self._runs_data
            if r["status"] in terminal_statuses
        }

        # Split into active (pinned to top) and inactive
        active_statuses = {"running", "stuck", "zombie", "active", "detached", "paused"}
        active = [
            r for r in self._runs_data
            if r["status"] in active_statuses or r.get("name", "") in self._pausing_run_ids
        ]
        inactive = [
            r for r in self._runs_data
            if r["status"] not in active_statuses and r.get("name", "") not in self._pausing_run_ids
        ]

        # Sort each group by start time descending (newest first)
        def sort_time(r):
            started = r.get("started")
            if started is None:
                return ""
            if isinstance(started, str):
                return started
            if hasattr(started, 'isoformat'):
                return started.isoformat()
            return ""

        active.sort(key=sort_time, reverse=True)
        inactive.sort(key=sort_time, reverse=True)

        self._sorted_runs = active + inactive[:10]
        self._has_running_runs = any(
            r["status"] in ("running", "active") for r in self._sorted_runs
        )

        self._dashboard_stats = calculate_dashboard_stats(
            self._runs_data, len(self._pipelines_data)
        )
        _log.debug(f"Loaded {len(self._runs_data)} runs, {len(self._pipelines_data)} pipelines")

    @work(thread=True, exclusive=True, group="home-scan")
    def _start_background_scan(self) -> None:
        """Scan runs in a background thread, then populate UI on main thread."""
        try:
            runs_data = scan_runs(include_archived=self._show_archived)
            pipelines_data = scan_pipelines()

            # Enhance status with process state for non-terminal runs
            for run in runs_data:
                run["status"] = get_enhanced_run_status(
                    Path(run["path"]),
                    run["status"]
                )

            self.app.call_from_thread(
                self._on_scan_complete, runs_data, pipelines_data
            )
        except Exception as e:
            _log.debug(f"Background scan error: {e}")

    def _on_scan_complete(self, runs_data, pipelines_data) -> None:
        """Apply scan results on the main UI thread."""
        self._runs_data = runs_data
        self._pipelines_data = pipelines_data

        if not self._initial_load_complete:
            for run in self._runs_data:
                if run.get("status") == "failed":
                    self._notified_failures.add(run["name"])
            self._initial_load_complete = True
        else:
            self._check_for_new_failures(self._runs_data)

        # Update terminal run cache
        terminal_statuses = {"complete", "failed", "killed"}
        self._cached_terminal_runs = {
            str(r["path"]): r for r in self._runs_data
            if r["status"] in terminal_statuses
        }

        # Split into active (pinned to top) and inactive
        active_statuses = {"running", "stuck", "zombie", "active", "detached", "paused"}
        active = [
            r for r in self._runs_data
            if r["status"] in active_statuses or r.get("name", "") in self._pausing_run_ids
        ]
        inactive = [
            r for r in self._runs_data
            if r["status"] not in active_statuses and r.get("name", "") not in self._pausing_run_ids
        ]

        # Sort each group by start time descending (newest first)
        def sort_time(r):
            started = r.get("started")
            if started is None:
                return ""
            if isinstance(started, str):
                return started
            if hasattr(started, 'isoformat'):
                return started.isoformat()
            return ""

        active.sort(key=sort_time, reverse=True)
        inactive.sort(key=sort_time, reverse=True)

        self._sorted_runs = active + inactive[:10]
        self._has_running_runs = any(
            r["status"] in ("running", "active") for r in self._sorted_runs
        )

        self._dashboard_stats = calculate_dashboard_stats(
            self._runs_data, len(self._pipelines_data)
        )

        # Populate UI
        self._populate_stats_cards()
        self._populate_runs_content()

        # Start spinner for running rows
        self._spinner_active = self._has_running_runs
        if self._spinner_active:
            self.set_timer(0.25, self._do_spinner)
            self.set_timer(1.0, self._do_duration_tick)
            self.set_timer(5.0, self._do_progress_tick)

        # Update terminal window title with run counts
        self._update_terminal_title()

        # Start auto-refresh
        self._refresh_active = True
        self.set_timer(2.5, self._do_auto_refresh)

    def _do_auto_refresh(self) -> None:
        """Auto-refresh using background thread to avoid blocking UI input."""
        if not self._refresh_active:
            return
        # Update header resource stats on every tick
        self._update_header_stats()
        if not self._refresh_in_progress:
            self._refresh_in_progress = True
            threading.Thread(target=self._bg_refresh, daemon=True).start()
        if self._refresh_active:
            self.set_timer(2.5, self._do_auto_refresh)

    def _bg_refresh(self) -> None:
        """Background thread: check for manifest changes, reload if needed.

        Only stats manifests for non-terminal (active) runs. Completed/failed
        runs are cached and skipped, avoiding expensive process-status checks.
        """
        try:
            from ..utils.runs import get_runs_dir
            runs_dir = get_runs_dir()
            if not runs_dir.exists():
                return

            # Count run directories (cheap check for new/deleted runs)
            run_dirs = [
                d for d in runs_dir.iterdir()
                if d.is_dir() and not d.name.startswith('.') and d.name != '_archive'
            ]
            if self._show_archived:
                archive_dir = runs_dir / "_archive"
                if archive_dir.exists():
                    run_dirs.extend(
                        d for d in archive_dir.iterdir()
                        if d.is_dir() and not d.name.startswith('.')
                    )
            current_count = len(run_dirs)
            count_changed = (current_count != self._last_run_dir_count)

            # Stat manifest files; only check non-terminal runs for changes
            active_changed = False
            current_times: dict[str, float] = {}
            for d in run_dirs:
                key = str(d)
                manifest = d / "MANIFEST.json"
                if not manifest.exists():
                    continue
                try:
                    mtime = manifest.stat().st_mtime
                    current_times[key] = mtime
                except OSError:
                    continue

                # Cached terminal runs don't need change detection
                if not count_changed and key in self._cached_terminal_runs:
                    continue

                old_mtime = self._last_manifest_times.get(key)
                if old_mtime is None or old_mtime != mtime:
                    active_changed = True

            # Detect removed runs
            if not count_changed:
                if set(current_times.keys()) != set(self._last_manifest_times.keys()):
                    count_changed = True

            process_state_changed = False
            if not count_changed and not active_changed:
                # Even when manifests are unchanged, process state may change
                # (e.g., watcher restarted with a new PID). Re-check dynamic
                # status for active-ish runs.
                dynamic_statuses = {"running", "active", "stuck", "zombie", "detached"}
                for run in self._runs_data:
                    status = run.get("status")
                    if status not in dynamic_statuses:
                        continue
                    run_path = Path(run.get("path", ""))
                    manifest = load_manifest(run_path)
                    if not manifest:
                        continue
                    base_status = get_run_status(manifest)
                    enhanced = get_enhanced_run_status(run_path, base_status)
                    if enhanced != status:
                        process_state_changed = True
                        break

            if not count_changed and not active_changed and not process_state_changed:
                return

            # Something changed — load fresh data (I/O in background thread)
            new_runs = scan_runs(include_archived=self._show_archived)
            new_pipelines = scan_pipelines()

            # Enhance status: skip expensive process checks for cached terminal runs
            terminal_statuses = {"complete", "failed", "killed"}
            for run in new_runs:
                path_key = str(run["path"])
                if (run["status"] in terminal_statuses
                        and path_key in self._cached_terminal_runs):
                    run["status"] = self._cached_terminal_runs[path_key]["status"]
                else:
                    run["status"] = get_enhanced_run_status(
                        Path(run["path"]), run["status"]
                    )

            # Post results to main UI thread
            self.app.call_from_thread(
                self._apply_refresh, new_runs, new_pipelines,
                current_times, current_count
            )
        except Exception:
            pass
        finally:
            self._refresh_in_progress = False

    def _apply_refresh(self, runs_data, pipelines_data, manifest_times, run_count):
        """Apply refreshed data on the main UI thread (called from background)."""
        self._last_manifest_times = manifest_times
        # Only update dir count if scan found all directories (manifest may still be writing)
        if len(runs_data) >= run_count or len(manifest_times) >= run_count:
            self._last_run_dir_count = run_count
        self._runs_data = runs_data
        self._pipelines_data = pipelines_data

        # Check for new failures and batch queue waits
        if self._initial_load_complete:
            self._check_for_new_failures(runs_data)
            self._check_batch_queue_wait(runs_data)

        # Update terminal run cache
        terminal_statuses = {"complete", "failed", "killed"}
        self._cached_terminal_runs = {
            str(r["path"]): r for r in runs_data
            if r["status"] in terminal_statuses
        }

        # Split into active (pinned to top) and inactive
        active_statuses = {"running", "stuck", "zombie", "active", "detached", "paused"}
        active = [
            r for r in runs_data
            if r["status"] in active_statuses or r.get("name", "") in self._pausing_run_ids
        ]
        inactive = [
            r for r in runs_data
            if r["status"] not in active_statuses and r.get("name", "") not in self._pausing_run_ids
        ]

        def sort_time(r):
            started = r.get("started")
            if started is None:
                return ""
            if isinstance(started, str):
                return started
            if hasattr(started, 'isoformat'):
                return started.isoformat()
            return ""

        active.sort(key=sort_time, reverse=True)
        inactive.sort(key=sort_time, reverse=True)

        self._sorted_runs = active + inactive[:10]
        self._has_running_runs = any(
            r["status"] in ("running", "active") for r in self._sorted_runs
        )

        self._dashboard_stats = calculate_dashboard_stats(
            runs_data, len(pipelines_data)
        )

        # Update UI
        self._populate_stats_cards()
        self._populate_runs_content()
        self._update_terminal_title()

        # Start spinner + progress ticker if needed and not already running
        if self._has_running_runs and not self._spinner_active:
            self._spinner_active = True
            self.set_timer(0.25, self._do_spinner)
            self.set_timer(1.0, self._do_duration_tick)
            self.set_timer(5.0, self._do_progress_tick)
        elif not self._has_running_runs:
            self._spinner_active = False

    def _check_for_new_failures(self, runs: list) -> None:
        """Show toast for newly failed runs (once per run). Skips archived runs."""
        for run in runs:
            if run.get("is_archived"):
                continue
            name = run["name"]
            status = run.get("status")

            if status == "failed" and name not in self._notified_failures:
                self._notified_failures.add(name)
                error_msg = run.get("error_message") or "Unknown error"
                if len(error_msg) > 80:
                    error_msg = error_msg[:77] + "..."
                self.notify(
                    f"Run '{name}' failed: {error_msg}",
                    severity="error",
                    timeout=8
                )

    def _check_batch_queue_wait(self, runs: list) -> None:
        """Show one-time educational toast when a batch run waits >2min in provider queue."""
        now = datetime.now(tz=timezone.utc)
        for run in runs:
            name = run.get("name", "")
            if name in self._batch_wait_toasts_shown:
                continue
            if run.get("mode") != "batch":
                continue
            if run.get("status") not in ("running", "active"):
                continue
            # Check if all chunks are in SUBMITTED state (waiting in provider queue)
            run_path = Path(run.get("path", ""))
            manifest_path = run_path / "MANIFEST.json"
            if not manifest_path.exists():
                continue
            try:
                manifest = json.loads(manifest_path.read_text())
                chunks = manifest.get("chunks", {})
                if not chunks:
                    continue
                # All chunks must be in a *_SUBMITTED state
                all_submitted = all(
                    c.get("state", "").endswith("_SUBMITTED")
                    for c in chunks.values()
                )
                if not all_submitted:
                    continue
                # Check how long it's been since the manifest was last updated
                mtime = manifest_path.stat().st_mtime
                age = (now - datetime.fromtimestamp(mtime, tz=timezone.utc)).total_seconds()
                if age < 120:
                    continue
            except Exception:
                continue

            # Get realtime cost multiplier
            multiplier = "2"
            try:
                from ..providers.base import LLMProvider
                config_path = run_path / "config" / "config.yaml"
                if config_path.exists():
                    import yaml
                    with open(config_path) as f:
                        run_config = yaml.safe_load(f)
                    provider = run_config.get("api", {}).get("provider", "")
                    if provider:
                        info = LLMProvider.get_provider_info(provider)
                        m = info.get("realtime_multiplier")
                        if m:
                            multiplier = f"{m:g}"
            except Exception:
                pass

            self._batch_wait_toasts_shown.add(name)
            self.notify(
                f"Batch jobs are queued by the provider and may take 5-15 minutes"
                f" to start processing. Realtime mode costs {multiplier}x as much"
                f" but runs instantly. Batch mode is cheaper for large runs.",
                title="\u23f3 Batch Queue",
                severity="information",
                timeout=8,
            )

    # --- UI Population ---

    def _populate_stats_cards(self) -> None:
        """Populate the stats cards row."""
        try:
            stats_row = self.query_one("#stats-row", Horizontal)
        except NoMatches:
            return

        stats_row.remove_children()

        stats = self._dashboard_stats
        cards = [
            ("Total Runs", str(stats.get('total_runs', 0))),
            ("Total Tokens", stats.get('total_tokens_formatted', '0')),
            ("Total Cost", stats.get('total_cost_formatted', '$0.00')),
            ("Pipelines", str(stats.get('pipeline_count', 0))),
        ]

        for label, value in cards:
            card = Vertical(classes="stats-card")
            card.compose_add_child(Static(f"[dim]{label}[/]", classes="stats-label"))
            card.compose_add_child(Static(f"[bold]{value}[/]", classes="stats-value"))
            stats_row.mount(card)

    def _populate_runs_content(self) -> None:
        """Populate the unified runs table."""
        try:
            table = self.query_one("#runs-table", DataTable)
        except NoMatches:
            return

        old_cursor = table.cursor_row
        table.clear()
        self._running_row_starts.clear()

        for i, run in enumerate(self._sorted_runs):
            symbol = self._get_status_symbol(run)
            name = run['name']
            if len(name) > 28:
                name = name[:25] + "..."
            if run.get("is_archived"):
                name = f"[dim]📦 {name}[/]"
            progress = f"{run['progress']}%"
            total_units = run.get('total_units', 0)
            valid_units = run.get('valid_units', 0)
            failed_units = run.get('unit_failure_count', 0)
            units_str = str(total_units) if total_units > 0 else "--"
            valid_str = f"[green]{valid_units}[/]" if valid_units > 0 else "--"
            # Color failures by type: yellow for complete runs (validation), red for failed runs (hard)
            if run['status'] == 'complete' and failed_units > 0:
                failed_str = f"[yellow]{failed_units}[/]"
            elif failed_units > 0:
                failed_str = f"[red]{failed_units}[/]"
            else:
                failed_str = "--"
            cost = run['cost']
            eta = self._compute_eta(run)
            duration = run.get('duration', '--')
            mode_display = run.get('mode_display', run.get('mode', 'batch'))
            started = self._format_started(run.get('started'))
            status_text = self._get_run_status_text(run)
            row_key = f"run-{i}"
            table.add_row(symbol, name, progress, units_str, valid_str, failed_str, cost, eta, duration, mode_display, started, status_text, key=row_key)
            # Track running rows for 1s duration ticker
            if run["status"] in ("running", "active") and run.get("started"):
                start_dt = run["started"]
                if isinstance(start_dt, str):
                    try:
                        start_dt = datetime.fromisoformat(start_dt.replace('Z', '+00:00'))
                    except (ValueError, AttributeError):
                        start_dt = None
                if start_dt is not None:
                    self._running_row_starts[row_key] = start_dt

        # Restore cursor position (clamped to valid range)
        if self._sorted_runs:
            new_cursor = min(old_cursor, len(self._sorted_runs) - 1)
            table.move_cursor(row=max(0, new_cursor))

    # --- Status Formatting ---

    def _get_status_symbol(self, run: dict) -> str:
        """Get status icon for a run row. Running rows get the animated spinner."""
        status = run['status']
        unit_failures = run.get('unit_failure_count', 0)
        run_name = run.get("name", "")

        if run_name in self._pausing_run_ids:
            return "[cyan]⏳[/]"
        if status in ("running", "active"):
            spinner = SPINNER_FRAMES[self._spinner_index]
            return f"[green]{spinner}[/]"
        if status == "complete" and unit_failures > 0:
            return "[yellow]⚠[/]"

        symbols = {
            "complete": "[green]✓[/]",
            "failed": "[red]✗[/]",
            "pending": "[dim]○[/]",
            "stuck": "[yellow]⚠[/]",
            "zombie": "[red]💀[/]",
            "killed": "[red]✗[/]",
            "detached": "[yellow]?[/]",
            "paused": "[cyan]⏸[/]",
        }
        return symbols.get(status, "?")

    def _get_run_status_text(self, run: dict) -> str:
        """Get display text for the Status column (clean state only)."""
        status = run['status']
        run_name = run.get("name", "")

        if run_name in self._pausing_run_ids:
            return "pausing..."

        if status in ("running", "active"):
            run_path = Path(run.get('path', ''))
            if run_path.exists():
                step = self._get_step_progress(run_path)
                if step != "--":
                    return f"running ({step})"
            return "running"

        if status == "complete":
            unit_failures = run.get('unit_failure_count', 0)
            if unit_failures > 0:
                return f"complete with {unit_failures} validation failure{'s' if unit_failures != 1 else ''}"
            return "complete"
        if status == "zombie":
            return "process lost — R to resume"
        if status == "stuck":
            return "stuck — has errors"
        if status == "detached":
            return "process lost — R to resume"
        if status == "failed":
            step_name = self._extract_failed_step(run)
            error_msg = run.get('error_message', '')
            if step_name:
                if error_msg:
                    brief = error_msg[:40]
                    return f"failed at {step_name}: {brief}"
                return f"failed at {step_name}"
            if error_msg:
                return f"failed: {error_msg[:50]}"
            return "failed"
        return status

    def _extract_failed_step(self, run: dict) -> str | None:
        """Extract the step name where the run failed from manifest chunk states."""
        try:
            run_path = Path(run.get('path', ''))
            manifest = load_manifest(run_path)
            if not manifest:
                return None
            for chunk_data in manifest.get("chunks", {}).values():
                state = chunk_data.get("state", "")
                if "_FAILED" in state:
                    return state.rsplit("_FAILED", 1)[0]
        except Exception:
            pass
        return None

    def _get_step_progress(self, run_path: Path) -> str:
        """Get current pipeline step and progress for display."""
        manifest = load_manifest(run_path)
        if not manifest:
            return "--"

        try:
            chunks = manifest.get("chunks", {})
            pipeline = manifest.get("pipeline", [])
            if not chunks or not pipeline:
                return "--"

            total_chunks = len(chunks)
            validated = 0
            current_step_counts: dict[str, dict] = {}

            for chunk_info in chunks.values():
                state = chunk_info.get("state", "")
                if state == "VALIDATED":
                    validated += 1
                    continue

                if "_" in state:
                    parts = state.rsplit("_", 1)
                    if len(parts) == 2:
                        step_name, step_state = parts
                        if step_name not in current_step_counts:
                            current_step_counts[step_name] = {"count": 0}
                        current_step_counts[step_name]["count"] += 1

            if current_step_counts:
                current_step = max(current_step_counts.keys(), key=lambda s: current_step_counts[s]["count"])
                return f"{current_step} {validated}/{total_chunks}"
            elif validated == total_chunks:
                return f"complete {validated}/{total_chunks}"
            else:
                return f"pending 0/{total_chunks}"

        except Exception:
            return "--"

    def _compute_eta(self, run: dict) -> str:
        """Compute ETA for a running run from summary timestamps.

        Returns a formatted string like '~12m', '~1h 30m', or '—' for non-running runs.
        """
        status = run.get("status", "")
        if status not in ("running", "active"):
            return "—"

        progress = run.get("progress", 0)
        if progress <= 0 or progress >= 100:
            return "—"

        started = run.get("started")
        if not started:
            return "—"

        try:
            if isinstance(started, str):
                started_dt = datetime.fromisoformat(started.replace('Z', '+00:00'))
            elif isinstance(started, datetime):
                started_dt = started
            else:
                return "—"

            if started_dt.tzinfo is None:
                started_dt = started_dt.replace(tzinfo=timezone.utc)

            now = datetime.now(timezone.utc)
            elapsed = now - started_dt
            if elapsed.total_seconds() <= 0:
                return "—"

            eta_seconds = elapsed.total_seconds() * (100 - progress) / progress
            if eta_seconds < 60:
                return "~<1m"
            elif eta_seconds < 3600:
                mins = int(eta_seconds / 60)
                return f"~{mins}m"
            else:
                hours = int(eta_seconds / 3600)
                mins = int((eta_seconds % 3600) / 60)
                if mins > 0:
                    return f"~{hours}h {mins}m"
                return f"~{hours}h"
        except (ValueError, AttributeError, TypeError):
            return "—"

    def _format_started(self, started) -> str:
        """Format start time for display in table."""
        if started is None:
            return "--"
        try:
            from datetime import datetime
            if isinstance(started, datetime):
                return started.strftime("%b %d %H:%M")
            if isinstance(started, str):
                dt = datetime.fromisoformat(started.replace('Z', '+00:00'))
                return dt.strftime("%b %d %H:%M")
        except (ValueError, AttributeError):
            pass
        return "--"

    # --- Spinner Animation ---

    def _do_spinner(self) -> None:
        """Wrapper for spinner using recursive set_timer pattern."""
        if not self._spinner_active:
            return
        self._animate_spinner()
        if self._spinner_active:
            self.set_timer(0.25, self._do_spinner)

    def _animate_spinner(self) -> None:
        """Update spinner icon cells for running rows (no full table re-render)."""
        if not self._has_running_runs:
            return

        self._spinner_index = (self._spinner_index + 1) % len(SPINNER_FRAMES)
        spinner = SPINNER_FRAMES[self._spinner_index]

        try:
            table = self.query_one("#runs-table", DataTable)
            for i, run in enumerate(self._sorted_runs):
                if run["status"] in ("running", "active"):
                    table.update_cell(f"run-{i}", self._col_keys[0], f"[green]{spinner}[/]")
        except NoMatches:
            pass

    def _do_duration_tick(self) -> None:
        """Update duration cells for running rows every second."""
        if not self._spinner_active:
            return
        if self._running_row_starts:
            try:
                table = self.query_one("#runs-table", DataTable)
                now = datetime.now(tz=timezone.utc)
                for row_key, start_dt in self._running_row_starts.items():
                    elapsed = max(0, (now - start_dt).total_seconds())
                    hours = int(elapsed // 3600)
                    minutes = int((elapsed % 3600) // 60)
                    seconds = int(elapsed % 60)
                    if hours > 0:
                        dur = f"{hours}:{minutes:02d}:{seconds:02d}"
                    else:
                        dur = f"{minutes:02d}:{seconds:02d}"
                    table.update_cell(row_key, self._col_keys[8], dur)
            except (NoMatches, Exception):
                pass
        if self._spinner_active:
            self.set_timer(1.0, self._do_duration_tick)

    def _do_progress_tick(self) -> None:
        """Re-read manifests for running rows and update Progress, Cost, Status cells."""
        if not self._spinner_active:
            return
        if self._sorted_runs:
            try:
                table = self.query_one("#runs-table", DataTable)
                for i, run in enumerate(self._sorted_runs):
                    rk = f"run-{i}"
                    if run.get("status") not in ("running", "active", "stuck", "zombie", "detached"):
                        continue
                    run_path = Path(run.get("path", ""))
                    manifest = load_manifest(run_path)
                    if not manifest:
                        continue

                    # Always recompute live status from current manifest + PID.
                    base_status = get_run_status(manifest)
                    run["status"] = get_enhanced_run_status(run_path, base_status)
                    table.update_cell(rk, self._col_keys[0], self._get_status_symbol(run))
                    table.update_cell(rk, self._col_keys[11], self._get_run_status_text(run))

                    # If no longer actively running, remove from duration ticker.
                    if run["status"] not in ("running", "active"):
                        if rk in self._running_row_starts:
                            del self._running_row_starts[rk]
                        continue

                    # Ensure running rows are tracked for duration updates.
                    if rk not in self._running_row_starts and run.get("started"):
                        start_dt = run["started"]
                        if isinstance(start_dt, str):
                            try:
                                start_dt = datetime.fromisoformat(start_dt.replace('Z', '+00:00'))
                            except (ValueError, AttributeError):
                                start_dt = None
                        if start_dt is not None:
                            self._running_row_starts[rk] = start_dt

                    # Auto-correct inconsistent status (all chunks terminal but not marked complete)
                    if check_manifest_consistency(run_path, manifest):
                        run["status"] = "complete"
                        table.update_cell(rk, self._col_keys[0], self._get_status_symbol(run))
                        table.update_cell(rk, self._col_keys[11], "[green]complete[/]")
                        table.update_cell(rk, self._col_keys[2], "100%")
                        if rk in self._running_row_starts:
                            del self._running_row_starts[rk]
                        continue  # Skip further updates — will move to Recent on next full refresh
                    # Progress
                    progress = get_run_progress(manifest)
                    table.update_cell(rk, self._col_keys[2], f"{progress}%")
                    run["progress"] = progress
                    # Valid / Failed
                    chunks = manifest.get("chunks", {})
                    valid = sum(c.get("valid", 0) for c in chunks.values())
                    failures = get_run_unit_failure_count(manifest)
                    table.update_cell(rk, self._col_keys[4], f"[green]{valid}[/]" if valid > 0 else "--")
                    table.update_cell(rk, self._col_keys[5], f"[red]{failures}[/]" if failures > 0 else "--")
                    run["valid_units"] = valid
                    run["unit_failure_count"] = failures
                    # Cost
                    cost = get_run_cost(manifest)
                    table.update_cell(rk, self._col_keys[6], cost)
                    run["cost"] = cost
                    # Mode (may change if user resumes in a different mode)
                    metadata = manifest.get("metadata", {})
                    manifest_mode = metadata.get("mode", "batch") or "batch"
                    mode_display = manifest_mode
                    if manifest_mode == "realtime":
                        has_batch_ids = any(c.get("batch_id") for c in chunks.values())
                        if has_batch_ids:
                            mode_display = "mixed"
                    table.update_cell(rk, self._col_keys[9], mode_display)
                    run["mode_display"] = mode_display
                    # ETA (recompute for running rows)
                    table.update_cell(rk, self._col_keys[7], self._compute_eta(run))
                    # Status text (e.g., "running (play_hand 2/6)")
                    step_text = self._get_step_progress(run_path)
                    if step_text != "--":
                        table.update_cell(rk, self._col_keys[11], f"running ({step_text})")
            except (NoMatches, Exception):
                pass
        # Update header with resource stats
        self._update_header_stats()
        if self._spinner_active:
            self.set_timer(5.0, self._do_progress_tick)

    def _update_header_stats(self) -> None:
        """Update the header widget with current resource stats."""
        try:
            header = self.query_one("#header", Static)
            header.update(self._render_header())
        except NoMatches:
            pass

    def _update_terminal_title(self) -> None:
        """Update the OS terminal window/tab title with run counts.

        The Header widget (self.app.title) is only updated when this screen
        is the topmost screen.  The OS terminal title is always written via
        set_os_terminal_title(), which deduplicates internally — if another
        screen has already set a different title, our stale string won't
        match and the write will be skipped by the cache.
        """
        n_runs = len(self._sorted_runs) if hasattr(self, '_sorted_runs') else 0
        if self._show_archived and hasattr(self, '_sorted_runs'):
            n_archived = sum(1 for r in self._sorted_runs if r.get("is_archived"))
            n_active = n_runs - n_archived
            title = f"Octobatch v{__version__} \u2013 Main Screen ({n_active} runs, {n_archived} archived)"
        else:
            title = f"Octobatch v{__version__} \u2013 Main Screen ({n_runs} runs)"
        if self.app.screen is self:
            self.app.title = title
        set_os_terminal_title(title)

    # --- Footer ---

    def _render_footer(self) -> str:
        """Render footer with key bindings."""
        archive_label = "H:hide archived" if self._show_archived else "H:show archived"
        return f"N:new  Enter:open  X:archive  P:pause  R:resume/retry  K:kill  {archive_label}  L:pipelines  Q:quit"

    # --- Helpers ---

    def _get_selected_run(self) -> dict | None:
        """Get the run dict for the currently selected table row."""
        try:
            table = self.query_one("#runs-table", DataTable)
            row_index = table.cursor_row
            if 0 <= row_index < len(self._sorted_runs):
                return self._sorted_runs[row_index]
        except NoMatches:
            pass
        return None

    def _follow_run(self, run_path: str) -> None:
        """Move table cursor to the run at the given path."""
        try:
            table = self.query_one("#runs-table", DataTable)
            for i, r in enumerate(self._sorted_runs):
                if str(r.get('path', '')) == run_path:
                    table.move_cursor(row=i)
                    return
        except NoMatches:
            pass

    # --- Actions ---

    def action_noop(self) -> None:
        """Do nothing - used to disable keys like tab."""
        pass

    def action_quit(self) -> None:
        """Quit the application."""
        _log.debug("HomeScreen.action_quit")
        self.app.exit()

    def action_new_run(self) -> None:
        """Create a new run."""
        from .new_run_modal import NewRunModal
        self.app.push_screen(NewRunModal(self._pipelines_data), self._on_new_run_result)

    def _on_new_run_result(self, result) -> None:
        """Handle result from new run modal."""
        if result is None:
            return

        run_name = result.get("run_name", "unknown")
        run_dir = result.get("run_dir")
        mode = result.get("mode", "batch")

        mode_text = "realtime" if mode == "realtime" else "batch"
        self.notify(f"Run '{run_name}' started in {mode_text} mode")

        self._load_data()
        self._populate_stats_cards()
        self._populate_runs_content()

        # Schedule a delayed refresh to pick up the new run once its manifest is written
        self.set_timer(1.5, lambda: (self._load_data(), self._populate_stats_cards(), self._populate_runs_content()))

        if run_dir:
            self.set_timer(2.0, lambda: self._check_run_started_ok(run_dir, run_name))

    def _check_run_started_ok(self, run_dir, run_name: str) -> None:
        """Check if a newly started run failed immediately."""
        manifest_path = run_dir / "MANIFEST.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text())
                if manifest.get("status") == "failed" or manifest.get("error_message"):
                    if run_name not in self._notified_failures:
                        self._notified_failures.add(run_name)
                        error = manifest.get("error_message", "Unknown error")
                        if len(error) > 80:
                            error = error[:77] + "..."
                        self.notify(f"Run '{run_name}' failed: {error}", severity="error", timeout=8)
                    self._load_data()
                    self._populate_runs_content()
            except (json.JSONDecodeError, IOError):
                pass

    def action_open_run(self) -> None:
        """Open the selected run."""
        run = self._get_selected_run()
        if not run:
            return

        _log.debug(f"Opening run: {run['name']} at path {run['path']}")

        try:
            from .main_screen import MainScreen
            from ..data import load_run_data
            run_data = load_run_data(run["path"])
            self.app.push_screen(MainScreen(run_data))
        except Exception as e:
            _log.debug(f"Error opening run: {e}")
            self.notify(f"Error opening run: {e}", severity="error")

    def action_delete_run(self) -> None:
        """Delete the selected run (with confirmation)."""
        run = self._get_selected_run()
        if not run:
            return
        self.notify(f"Delete not yet implemented for: {run['name']}", severity="warning")

    def action_archive_run(self) -> None:
        """Archive or unarchive the selected run."""
        run = self._get_selected_run()
        if not run:
            return

        # Only allow archiving terminal runs
        terminal_statuses = {"complete", "failed", "killed"}
        if run["status"] not in terminal_statuses:
            self.notify("Can only archive completed, failed, or killed runs", severity="warning")
            return

        run_path = Path(run["path"])
        is_archived = run.get("is_archived", False) or run_path.parent.name == "_archive"
        from .modals import ArchiveConfirmModal

        def on_confirm(result: bool) -> None:
            if not result:
                return
            import shutil
            if is_archived:
                # Unarchive: move from _archive back to runs dir
                dest = run_path.parent.parent / run_path.name
            else:
                # Archive: move to _archive subdirectory
                archive_dir = run_path.parent / "_archive"
                archive_dir.mkdir(exist_ok=True)
                dest = archive_dir / run_path.name

            if dest.exists():
                self.notify(f"Cannot move: destination already exists ({dest.name})", severity="error")
                return
            try:
                shutil.move(str(run_path), str(dest))
                action = "Unarchived" if is_archived else "Archived"
                self.notify(f"{action} '{run['name']}'")
                self._load_data()
                self._populate_stats_cards()
                self._populate_runs_content()
            except Exception as e:
                self.notify(f"Archive failed: {e}", severity="error")

        self.app.push_screen(
            ArchiveConfirmModal(run["name"], is_unarchive=is_archived),
            on_confirm
        )

    def action_toggle_archived(self) -> None:
        """Toggle visibility of archived runs."""
        self._show_archived = not self._show_archived
        label = "showing" if self._show_archived else "hiding"
        self.notify(f"Archived runs: {label}")
        self._load_data()
        self._populate_stats_cards()
        self._populate_runs_content()
        self._update_terminal_title()
        # Update footer to reflect new toggle state
        try:
            footer = self.query_one("#footer", Static)
            footer.update(self._render_footer())
        except NoMatches:
            pass

    def action_pause_run(self) -> None:
        """Pause the selected active run by sending SIGINT."""
        run = self._get_selected_run()
        if not run:
            return

        status = run.get("status", "")
        if status not in ("running", "active"):
            self.notify(f"Cannot pause run with status '{status}'", severity="warning")
            return

        run_dir = Path(run["path"])
        proc_status = get_run_process_status(run_dir)

        if not proc_status["alive"]:
            self.notify("Run is not currently running", severity="warning")
            return

        pid = proc_status["pid"]

        try:
            os.kill(pid, signal.SIGINT)
            self.notify(
                f"Pausing run... Sending SIGINT to PID {pid}. Press R to resume later.",
                timeout=5,
            )

            run_name = run.get("name", "")
            self._pausing_run_ids.add(run_name)

            # After 5 seconds, if still pausing, hint about force kill
            self.set_timer(5.0, lambda: self._pause_timeout_hint(run_name))
            self.set_timer(0.1, lambda: self._poll_for_pause_completion(run_dir, run_name, attempt=0))
        except ProcessLookupError:
            self.notify("Process not found", severity="error")
        except PermissionError:
            self.notify("Permission denied", severity="error")
        except Exception as e:
            self.notify(f"Failed to pause: {e}", severity="error")

    def _refresh_after_pause(self, run_dir: Path) -> None:
        """Refresh data after pausing a process and follow the run."""
        run_path = str(run_dir)
        self._load_data()
        self._populate_stats_cards()
        self._populate_runs_content()
        self._follow_run(run_path)

    def _poll_for_pause_completion(self, run_dir: Path, run_name: str, attempt: int) -> None:
        """Poll manifest until run shows 'paused' status or timeout."""
        MAX_ATTEMPTS = 600
        POLL_INTERVAL = 0.5

        if run_name not in self._pausing_run_ids:
            return

        manifest_path = run_dir / "MANIFEST.json"
        try:
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text())
                status = manifest.get("status", "")
                if status in ("paused", "complete", "failed"):
                    self._pausing_run_ids.discard(run_name)
                    self._refresh_after_pause(run_dir)
                    return
        except (json.JSONDecodeError, IOError):
            pass

        proc_status = get_run_process_status(run_dir)
        if not proc_status["alive"] and attempt >= 4:
            self._pausing_run_ids.discard(run_name)
            self._refresh_after_pause(run_dir)
            return

        if attempt < MAX_ATTEMPTS:
            self.set_timer(POLL_INTERVAL, lambda: self._poll_for_pause_completion(run_dir, run_name, attempt + 1))
        else:
            self._pausing_run_ids.discard(run_name)
            self._refresh_after_pause(run_dir)

    def _pause_timeout_hint(self, run_name: str) -> None:
        """Show force-kill hint if pause hasn't completed after 5 seconds."""
        if run_name in self._pausing_run_ids:
            self.notify(
                "Process still running. Press K to force kill.",
                severity="warning",
                timeout=5,
            )

    def action_kill_run(self) -> None:
        """Kill running process and update manifest, or mark zombie as killed."""
        run = self._get_selected_run()
        if not run:
            return

        status = run.get("status", "")
        if status not in ("running", "active", "stuck", "zombie", "detached"):
            self.notify("Select an active run to kill", severity="warning")
            return

        run_dir = Path(run["path"])
        proc_status = get_run_process_status(run_dir)

        if proc_status["alive"]:
            if kill_run_process(run_dir):
                # Update manifest so the run doesn't appear as a zombie
                mark_run_as_killed(run_dir)
                self.notify(f"Killed PID {proc_status['pid']}")
            else:
                self.notify("Failed to kill process", severity="error")
        else:
            # Process already dead (zombie) — just update manifest
            if mark_run_as_killed(run_dir):
                self.notify("Marked zombie run as killed")
            else:
                self.notify("Failed to update manifest", severity="error")

        run_path = str(run_dir)
        self.set_timer(0.5, lambda: self._refresh_after_kill(run_path))

    def _refresh_after_kill(self, run_path: str) -> None:
        """Refresh data after killing a process and follow the run."""
        self._load_data()
        self._populate_runs_content()
        self._follow_run(run_path)

    def action_show_pipelines(self) -> None:
        """Show pipelines management."""
        from ..config_editor import ConfigListScreen
        self.app.push_screen(ConfigListScreen())

    def action_refresh(self) -> None:
        """Refresh data via background scan."""
        _log.debug("HomeScreen.action_refresh")
        self._start_background_scan()

    def action_resume_run(self) -> None:
        """Resume, restart, or recover a run based on its current state."""
        import os
        import signal
        import time
        from datetime import datetime

        _log.debug("HomeScreen.action_resume_run")

        run = self._get_selected_run()
        if not run:
            self.app.notify("No run selected", severity="warning")
            return

        run_dir = Path(run.get('path', ''))
        manifest_status = run.get('status', 'unknown')
        mode = run.get('mode', 'batch')

        if not run_dir.exists():
            self.app.notify("Run path not found", severity="error")
            return

        health = get_process_health(run_dir)
        _log.debug(f"Resume: status={manifest_status}, health={health}")

        # Case 1: Healthy running process - nothing to do
        if health["status"] == "running":
            self.app.notify("Run is already running")
            return

        # Case 2: Hung process - kill first, then restart
        if health["status"] == "hung":
            pid = health.get("pid")
            hung_seconds = health.get("last_activity_seconds", 0)
            hung_minutes = int(hung_seconds // 60)

            self.app.notify(f"Killing hung process (PID {pid}) and restarting...")
            _log.debug(f"Killing hung process PID {pid}, inactive for {hung_minutes}m")

            try:
                if hung_minutes < 10:
                    os.kill(pid, signal.SIGTERM)
                    time.sleep(1.0)
                    try:
                        os.kill(pid, 0)
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    os.kill(pid, signal.SIGKILL)

                time.sleep(0.5)
            except ProcessLookupError:
                pass
            except Exception as e:
                self.app.notify(f"Failed to kill process: {e}", severity="error")
                return

            pid_file = run_dir / "orchestrator.pid"
            if pid_file.exists():
                try:
                    pid_file.unlink()
                except Exception:
                    pass

            self._update_manifest_for_restart(run_dir)
            self._start_orchestrator(run_dir, mode)
            return

        # Case 3: Dead process (zombie) or detached/paused/complete - restart or retry
        if health["status"] == "dead":
            if manifest_status == "complete":
                unit_failures = run.get('unit_failure_count', 0)
                if unit_failures == 0:
                    self.app.notify("Run completed with no failures", severity="information")
                    return
                self._retry_failed_units(run_dir, mode)
                return

            if manifest_status in ("failed", "killed"):
                # Check for pre-flight failure: all chunks still at initial PENDING
                # means the run failed before any units were submitted (e.g., missing API key).
                # These should be resumed, not retried.
                try:
                    manifest = load_manifest(run_dir)
                    chunks = manifest.get("chunks", {})
                    pipeline = manifest.get("pipeline", [])
                    first_step = pipeline[0] if pipeline else None
                    all_initial_pending = first_step and chunks and all(
                        c.get("state") == f"{first_step}_PENDING"
                        for c in chunks.values()
                    )
                    if all_initial_pending:
                        self.app.notify(f"Resuming pre-flight failed run '{run.get('name', '')}'...")
                        self._update_manifest_for_restart(run_dir)
                        self._start_orchestrator(run_dir, mode)
                        return
                except Exception:
                    pass  # Fall through to normal retry path

                self._retry_failed_units(run_dir, mode)
                return

            if manifest_status not in ('detached', 'paused', 'running', 'active', 'zombie', 'stuck'):
                self.app.notify(f"Cannot resume run with status '{manifest_status}'", severity="warning")
                return

            self.app.notify(f"Restarting run '{run.get('name', '')}'...")
            self._update_manifest_for_restart(run_dir)
            self._start_orchestrator(run_dir, mode)
            return

        self.app.notify(f"Cannot resume run in state: {manifest_status}", severity="warning")

    def _start_orchestrator(self, run_dir: Path, mode: str) -> None:
        """Start an orchestrator process for the run."""
        watch_mode = "watch" if mode.lower() == "batch" else "realtime"

        success = resume_orchestrator(run_dir, mode=watch_mode)

        if success:
            self.app.notify("Orchestrator started")
            run_path = str(run_dir)
            self.set_timer(1.0, lambda: self._refresh_and_follow_run(run_path))
        else:
            self.app.notify("Failed to start orchestrator", severity="error")

    def _refresh_and_follow_run(self, run_path: str) -> None:
        """Refresh data and move cursor to follow a run to its new position."""
        self.action_refresh()
        self._follow_run(run_path)

    def _retry_failed_units(self, run_dir: Path, mode: str) -> None:
        """Reset all failed units and spawn orchestrator to process retries."""
        _log.debug(f"_retry_failed_units: run_dir={run_dir}, mode={mode}")

        try:
            reset_count = reset_unit_retries(run_dir)

            if reset_count == 0:
                self.app.notify("No failed units to retry", severity="information")
                return

            orchestrator_mode = "realtime" if mode == "realtime" else "watch"
            success = resume_orchestrator(run_dir, mode=orchestrator_mode)

            if success:
                self.app.notify(
                    f"Retrying {reset_count} failed units...",
                    severity="information"
                )
            else:
                self.app.notify(
                    f"Reset {reset_count} units but failed to start orchestrator",
                    severity="error"
                )

            run_path = str(run_dir)
            self.set_timer(1.0, lambda: self._refresh_and_follow_run(run_path))

        except Exception as e:
            _log.debug(f"_retry_failed_units error: {e}")
            self.app.notify(f"Retry failed: {e}", severity="error")

    def _update_manifest_for_restart(self, run_dir: Path) -> None:
        """Update manifest to allow restart."""
        from datetime import datetime

        manifest_path = run_dir / "MANIFEST.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text())
                manifest["status"] = "running"
                manifest["restarted_at"] = datetime.now().isoformat()
                manifest["updated"] = datetime.now().isoformat()
                manifest.pop("error_message", None)

                tmp_path = manifest_path.with_suffix(".tmp")
                tmp_path.write_text(json.dumps(manifest, indent=2))
                tmp_path.rename(manifest_path)
            except Exception as e:
                _log.debug(f"Failed to update manifest for restart: {e}")
