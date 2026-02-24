"""
MainScreen for Octobatch TUI.

4-quadrant split-pane UI with pipeline and stats at top, toggleable detail panel at bottom.
Supports navigation: ←→ to switch steps, ↑↓ to navigate within panel.
Bottom panel toggles between Chunk View and Unit View with V key.
"""

import json
import re
from pathlib import Path

from typing import Any

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, HorizontalScroll, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import Screen, ModalScreen
from textual.widgets import Static, DataTable, Label, Tree
from textual.widgets.tree import TreeNode
from textual.reactive import reactive
from textual.binding import Binding
from textual import events
from textual import work

from version import __version__
from ..data import RunData, RealtimeProgress, load_run_data, format_tokens, format_time_remaining, _find_jsonl_file, _open_jsonl
from ..modals import LogModal, ArtifactModal
from ..config_editor import ConfigListScreen
from ..widgets import render_pipeline_boxes, make_progress_bar
from ..widgets.otto_widget import OttoWidget
from ..widgets.otto_orchestrator import OttoOrchestrator
from .common import (
    parse_chunk_state,
    get_step_status_from_chunks,
    get_chunk_status_symbol,
    get_step_status_symbol,
    get_resource_stats,
    set_os_terminal_title,
    _log,
)
from ..utils.runs import get_run_process_status, has_recent_errors, get_batch_timing, get_process_health


SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class LogTicker(Static):
    """Scrolling display of recent log entries from RUN_LOG.txt."""

    DEFAULT_CSS = """
    LogTicker {
        height: 6;
        border: solid $surface-lighten-2;
        padding: 0 1;
        background: $surface-darken-1;
    }
    """

    def __init__(self, run_dir: Path, max_lines: int = 4, **kwargs):
        super().__init__(**kwargs)
        self.run_dir = run_dir
        self.max_lines = max_lines  # 4 log lines + 1 header = 5 visible lines
        self._log_lines: list[str] = []
        self._spinner_index: int = 0
        self._is_running: bool = False

    def update_logs(self, is_running: bool = False) -> None:
        """Read latest log entries from RUN_LOG.txt and refresh display."""
        self._is_running = is_running
        self._spinner_index += 1  # Advance spinner each update

        log_file = self.run_dir / "RUN_LOG.txt"
        if not log_file.exists():
            self._log_lines = []
            self.update(self._render_lines())
            self.refresh()
            return

        try:
            # Read last N lines efficiently by seeking from end
            with open(log_file, "rb") as f:
                f.seek(0, 2)  # End of file
                file_size = f.tell()

                # Read last ~4KB (enough for several log lines)
                read_size = min(4096, file_size)
                f.seek(max(0, file_size - read_size))
                content = f.read().decode("utf-8", errors="replace")

                lines = content.strip().split("\n")
                self._log_lines = lines[-self.max_lines:]
                self.update(self._render_lines())
                self.refresh()
        except Exception:
            self._log_lines = []
            self.update(self._render_lines())
            self.refresh()

    def _render_lines(self) -> str:
        """Render the log lines with header (and spinner if running)."""
        if self._is_running:
            spinner = SPINNER_FRAMES[self._spinner_index % len(SPINNER_FRAMES)]
            header = f"[bold cyan]{spinner} Recent Activity[/]"
        else:
            header = "[bold cyan]Recent Activity[/]"

        if not self._log_lines:
            return f"{header}\n[dim]Waiting for activity...[/]"

        output = [header]
        for i, line in enumerate(self._log_lines):
            formatted = self._format_log_line(line)

            # Highlight newest line (last one)
            if i == len(self._log_lines) - 1:
                output.append(f"[bold]{formatted}[/]")
            else:
                output.append(f"[dim]{formatted}[/]")

        return "\n".join(output)

    def _format_log_line(self, line: str) -> str:
        """Format a log line for display, shortening timestamp."""
        # Convert [2026-01-28T19:11:03Z] [POLL] msg → [19:11:03] [POLL] msg
        match = re.match(r'\[[\d-]+T([\d:]+)Z?\]\s*(.+)', line)
        if match:
            time_part, rest = match.groups()
            formatted = f"[{time_part}] {rest}"
            # Truncate if too long
            if len(formatted) > 80:
                return formatted[:77] + "..."
            return formatted
        # Fallback: truncate if needed
        return line[:80] if len(line) > 80 else line


class UnitDetailModal(ModalScreen):
    """Modal showing unit details with expandable JSON tree, raw JSON, or LLM response."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("t", "view_tree", "Tree"),
        Binding("T", "view_tree", "Tree", show=False),
        Binding("r", "view_raw", "Raw"),
        Binding("R", "view_raw", "Raw", show=False),
        Binding("l", "view_response", "LLM"),
        Binding("L", "view_response", "LLM", show=False),
        Binding("c", "copy", "Copy"),
        Binding("C", "copy", "Copy", show=False),
    ]

    CSS = """
    UnitDetailModal {
        align: center middle;
    }

    #modal-container {
        width: 85%;
        height: 85%;
        max-width: 120;
        border: solid $primary;
        background: $surface;
        padding: 1 2;
    }

    #unit-header {
        height: auto;
        margin-bottom: 1;
        background: $primary;
        padding: 0 1;
    }

    #tree-label {
        height: 1;
        margin-bottom: 0;
    }

    #error-section {
        height: auto;
        max-height: 8;
        margin-bottom: 1;
        padding: 0 1;
        border: solid $error;
    }

    #json-tree {
        height: 1fr;
        scrollbar-gutter: stable;
        border: solid $secondary;
        padding: 0 1;
    }

    #raw-json-container {
        height: 1fr;
        scrollbar-gutter: stable;
        border: solid $secondary;
        padding: 0 1;
    }

    #raw-json {
        width: 100%;
    }

    #response-container {
        height: 1fr;
        scrollbar-gutter: stable;
        border: solid $warning;
        padding: 0 1;
    }

    #response-tree {
        height: 1fr;
    }

    .hidden {
        display: none;
    }

    #modal-footer {
        dock: bottom;
        height: 1;
        background: $surface-darken-1;
        text-align: center;
    }
    """

    # View mode: "tree", "raw", or "response"
    view_mode = reactive("tree", init=False)

    def __init__(self, unit: dict, **kwargs):
        super().__init__(**kwargs)
        self.unit = unit

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container"):
            # Header with unit info
            yield Static(self._render_header(), id="unit-header")

            # For failed units, show error section first
            if self.unit.get("status") == "failed":
                yield Static(self._render_errors(), id="error-section")

            # Tree label
            label = "[bold]Input Data:[/]" if self.unit.get("status") == "failed" else "[bold]Result Data:[/]"
            yield Static(label, id="tree-label")

            # JSON Tree view (input/context data)
            tree: Tree[dict] = Tree("data", id="json-tree")
            tree.root.expand()
            self._build_tree(tree.root, self.unit.get("data", {}))
            yield tree

            # Raw JSON view (hidden by default) - markup=False to avoid parsing issues
            with VerticalScroll(id="raw-json-container", classes="hidden"):
                yield Static(self._render_raw_json(), id="raw-json", markup=False)

            # LLM Response view (hidden by default) - for failed units with raw_response
            with Vertical(id="response-container", classes="hidden"):
                yield Static("[bold yellow]Raw LLM Response (Failed Validation)[/]", id="response-label")
                response_tree: Tree[dict] = Tree("response", id="response-tree")
                response_tree.root.expand()
                raw_response = self.unit.get("raw_response")
                if raw_response:
                    self._build_tree(response_tree.root, raw_response)
                else:
                    response_tree.root.add_leaf("[dim]No raw_response captured[/]")
                yield response_tree

            # Footer
            yield Static(self._render_modal_footer(), id="modal-footer")

    def _render_header(self) -> str:
        """Render modal header with unit info."""
        unit_id = self.unit.get("unit_id", "Unknown")
        step = self.unit.get("step", "Unknown")
        chunk = self.unit.get("chunk", "")
        status = self.unit.get("status", "unknown")

        if status == "valid":
            status_display = "[green]✓ valid[/]"
        else:
            status_display = "[red]✗ failed[/]"

        header = f"[bold]Unit:[/] {unit_id}"
        if chunk:
            header += f"  [dim]({chunk})[/]"
        header += f"\n[bold]Step:[/] {step}  [bold]Status:[/] {status_display}"

        return header

    def _render_errors(self) -> str:
        """Render error section for failed units."""
        lines = []
        failure_stage = self.unit.get("failure_stage", "unknown")
        lines.append(f"[bold red]Failure Stage:[/] {failure_stage}")

        errors = self.unit.get("errors", [])
        if errors:
            for err in errors:
                if isinstance(err, dict):
                    path = err.get("path", "")
                    message = err.get("message", str(err))
                    if path:
                        lines.append(f"  [red]•[/] [dim]{path}:[/] {message}")
                    else:
                        lines.append(f"  [red]•[/] {message}")
                else:
                    lines.append(f"  [red]•[/] {err}")

        return "\n".join(lines)

    def _build_tree(self, node: TreeNode, data: Any) -> None:
        """Recursively build the tree, expanding all nodes by default."""
        if isinstance(data, dict):
            node.expand()
            for key, value in data.items():
                if isinstance(value, (dict, list)):
                    # Branch node for nested structures
                    child = node.add(f"[bold cyan]{key}[/]")
                    self._build_tree(child, value)
                else:
                    # Leaf node with value
                    node.add_leaf(f"[cyan]{key}[/]: {self._format_value(value)}")

        elif isinstance(data, list):
            node.expand()
            for i, item in enumerate(data):
                if isinstance(item, (dict, list)):
                    child = node.add(f"[dim][{i}][/]")
                    self._build_tree(child, item)
                else:
                    node.add_leaf(f"[dim][{i}][/]: {self._format_value(item)}")
        else:
            # Single value at root (rare)
            node.add_leaf(self._format_value(data))

    def _format_value(self, value: Any) -> str:
        """Format a leaf value with syntax highlighting."""
        if isinstance(value, str):
            # Truncate long strings for display
            if len(value) > 60:
                return f'[green]"{value[:57]}..."[/]'
            return f'[green]"{value}"[/]'
        elif isinstance(value, bool):
            return f"[yellow]{str(value).lower()}[/]"
        elif isinstance(value, (int, float)):
            return f"[blue]{value}[/]"
        elif value is None:
            return "[dim]null[/]"
        return str(value)

    def _render_raw_json(self) -> str:
        """Render raw JSON with line numbers (plain text, no markup)."""
        if self.unit.get("status") == "failed":
            data = self.unit
        else:
            data = self.unit.get("data", {})

        raw = json.dumps(data, indent=2)
        lines = raw.split("\n")

        # Format with line numbers - plain text only (markup=False on the Static)
        formatted_lines = []
        for i, line in enumerate(lines, 1):
            formatted_lines.append(f"{i:4}  {line}")

        return "\n".join(formatted_lines)

    def _render_modal_footer(self) -> str:
        """Render footer based on current view mode."""
        has_response = self.unit.get("raw_response") is not None
        if self.view_mode == "tree":
            base = "↑↓:navigate  ←:collapse  →:expand  T:tree  R:raw"
            if has_response:
                base += "  L:llm-response"
            return base + "  C:copy  Esc:close"
        elif self.view_mode == "raw":
            base = "↑↓:scroll  T:tree  R:raw"
            if has_response:
                base += "  L:llm-response"
            return base + "  C:copy  Esc:close"
        else:  # response
            return "↑↓:navigate  ←:collapse  →:expand  T:tree  R:raw  L:llm-response  C:copy  Esc:close"

    def watch_view_mode(self, new_mode: str) -> None:
        """Toggle visibility when view mode changes."""
        try:
            tree = self.query_one("#json-tree", Tree)
            tree_label = self.query_one("#tree-label", Static)
            raw_container = self.query_one("#raw-json-container", VerticalScroll)
            response_container = self.query_one("#response-container", Vertical)
            footer = self.query_one("#modal-footer", Static)

            # Hide all views first
            tree.add_class("hidden")
            tree_label.add_class("hidden")
            raw_container.add_class("hidden")
            response_container.add_class("hidden")

            # Show the selected view
            if new_mode == "tree":
                tree.remove_class("hidden")
                tree_label.remove_class("hidden")
            elif new_mode == "raw":
                raw_container.remove_class("hidden")
            else:  # response
                response_container.remove_class("hidden")

            footer.update(self._render_modal_footer())
        except NoMatches:
            pass

    def action_view_tree(self) -> None:
        """Switch to tree view (input/context data)."""
        self.view_mode = "tree"

    def action_view_raw(self) -> None:
        """Switch to raw JSON view."""
        self.view_mode = "raw"

    def action_view_response(self) -> None:
        """Switch to LLM response view (for failed units)."""
        if self.unit.get("raw_response") is not None:
            self.view_mode = "response"
        else:
            self.notify("No raw_response captured for this unit", severity="warning")

    def action_close(self) -> None:
        """Close the modal."""
        self.dismiss(None)

    def action_copy(self) -> None:
        """Copy full JSON to clipboard (not truncated)."""
        try:
            import pyperclip

            if self.unit.get("status") == "failed":
                content = json.dumps(self.unit, indent=2)
            else:
                content = json.dumps(self.unit.get("data", {}), indent=2)

            pyperclip.copy(content)
            self.notify("Copied to clipboard")
        except ImportError:
            self.notify("pyperclip not installed", severity="warning")
        except Exception as e:
            self.notify(f"Copy failed: {e}", severity="error")


class MainScreen(Screen):
    """Main TUI screen with 4-quadrant layout: pipeline/stats at top, detail/chunk-stats at bottom."""

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("Q", "quit", "Quit", show=False),
        Binding("l", "show_log", "Log"),
        Binding("L", "show_log", "Log", show=False),
        Binding("a", "view_artifacts", "Files"),
        Binding("A", "view_artifacts", "Files", show=False),
        Binding("c", "show_config", "Config", show=False),
        Binding("C", "show_config", "Config", show=False),
        Binding("i", "process_info", "Info", show=False),
        Binding("I", "process_info", "Info", show=False),
        Binding("v", "toggle_view", "View"),
        Binding("V", "toggle_view", "View", show=False),
        Binding("escape", "go_back", "Back"),
        Binding("enter", "select_item", "Select"),
        Binding("tab", "noop", "", show=False),
        Binding("left", "step_prev", "Prev Step", show=False),
        Binding("right", "step_next", "Next Step", show=False),
        Binding("up", "nav_up", "Up", show=False),
        Binding("down", "nav_down", "Down", show=False),
        # Filter/sort bindings (only active in unit view)
        Binding("f", "cycle_status_filter", "Filter", show=False),
        Binding("F", "cycle_status_filter", "Filter", show=False),
        Binding("s", "cycle_sort", "Sort", show=False),
        Binding("S", "cycle_sort", "Sort", show=False),
        # Retry action for failed units
        Binding("r", "retry_failures", "Retry", show=False),
        Binding("R", "retry_failures", "Retry", show=False),
        # Diagnostic export
        Binding("d", "generate_diagnostic", "Diagnostic", show=False),
        Binding("D", "generate_diagnostic", "Diagnostic", show=False),
        # Archive run
        Binding("x", "archive_run", "Archive"),
        Binding("X", "archive_run", "Archive", show=False),
    ]

    CSS = """
    MainScreen {
        layout: grid;
        grid-size: 1;
        grid-rows: 1 1fr 1;
    }

    #header {
        height: 1;
        padding: 0 1;
        background: $primary;
    }

    #main-split {
        height: 1fr;
        width: 100%;
    }

    #left-column {
        width: 3fr;
        height: 100%;
    }

    #right-column {
        width: 1fr;
        height: 100%;
        border-left: solid $primary;
    }

    #pipeline-panel {
        height: auto;
        max-height: 12;
        padding: 1 0 0 0;
        border-bottom: solid $secondary;
    }

    #pipeline-panel.inactive {
        opacity: 0.7;
    }

    #pipeline-scroll {
        height: auto;
        max-height: 10;
        overflow-x: auto;
        overflow-y: hidden;
    }

    #pipeline-content {
        width: auto;
        min-width: 100%;
        padding: 0 1;
    }

    #log-ticker {
        height: 6;
        width: 100%;
        border-bottom: solid $secondary;
        margin: 0;
    }

    #detail-panel {
        height: 1fr;
        padding: 1;
    }

    #detail-panel.inactive {
        opacity: 0.7;
    }

    .stats-panel {
        width: 100%;
        height: auto;
        padding: 0 1;
    }

    #run-stats-panel {
        border-bottom: solid $secondary;
        min-height: 10;
    }

    #otto-container {
        height: 12;
        width: 30;
        border-bottom: solid $secondary;
    }

    #otto-status {
        width: 100%;
        height: 1;
        text-align: center;
        color: $text-muted;
    }

    #stats-scroll {
        height: 1fr;
        width: 100%;
    }

    #chunk-view, #unit-view {
        height: 1fr;
    }

    #footer {
        height: 1;
        background: $surface-darken-1;
        padding: 0 1;
    }

    DataTable {
        height: 1fr;
    }

    .detail-header {
        height: 1;
        text-style: bold;
        margin-bottom: 1;
    }

    .empty-state {
        text-align: center;
        padding: 2;
        color: $text-muted;
    }
    """

    # Selection state (init=False prevents watchers from firing during initialization)
    selected_step_index = reactive(0, init=False)
    selected_chunk_index = reactive(0, init=False)
    selected_unit_index = reactive(0, init=False)
    active_focus = reactive("pipeline", init=False)  # "pipeline" or "detail"

    # View toggle and filtering state
    current_view = reactive("unit", init=False)  # "chunk" or "unit"
    status_filter = reactive("all", init=False)   # all, valid, failed
    step_filter = reactive("all", init=False)     # all, or specific step name
    sort_by = reactive("unit_id", init=False)     # unit_id, status, step

    def __init__(self, run_data: RunData, **kwargs):
        super().__init__(**kwargs)
        self.run_data = run_data
        self._chunks_for_step: list = []
        self._all_units: list[dict] = []
        self._filtered_units: list[dict] = []
        self._unique_steps: list[str] = []
        self._chunk_table: DataTable | None = None
        self._unit_table: DataTable | None = None
        self._poll_timer = None
        self._spinner_index: int = 0
        self._refresh_active: bool = False
        self._units_loading: bool = False
        self._units_loaded: bool = False
        self._step_descriptions: dict[str, str] = {}
        self._batch_toast_shown: bool = False
        self._otto_orchestrator: OttoOrchestrator | None = None
        self._previous_chunk_states: dict[str, str] = {}
        self._previous_chunk_retries: dict[str, int] = {}
        self._idle_toast_shown: bool = False
        self._cached_max_retries: int | None = None
        self._last_pipeline_content: str = ""
        self._last_manifest_signature: tuple | None = None
        self._pending_units_refresh: bool = True
        self._failure_summary_cache: dict[str, list[tuple[str, str, int]]] = {}  # step -> [(stage, msg, count)]
        self._failure_summary_mtimes: dict[str, float] = {}  # step -> max mtime of failure files

    def compose(self) -> ComposeResult:
        yield Static(self._render_header(), id="header")

        # Root container for the 2-column split layout
        with Horizontal(id="main-split"):
            # LEFT COLUMN (Workspace: Pipeline -> Log -> Units)
            with Vertical(id="left-column"):
                with Vertical(id="pipeline-panel"):
                    yield HorizontalScroll(id="pipeline-scroll")
                yield LogTicker(self.run_data.run_dir, id="log-ticker")
                yield VerticalScroll(id="detail-panel")

            # RIGHT COLUMN (Sidebar: Otto fixed at top, stats scrollable below)
            with Vertical(id="right-column"):
                with Container(id="otto-container"):
                    yield OttoWidget(id="otto")
                    yield Label("Otto is waiting for his next job", id="otto-status")
                with VerticalScroll(id="stats-scroll"):
                    yield Static("", id="run-stats-panel", classes="stats-panel")
                    yield Static("", id="chunk-stats-panel", classes="stats-panel")

        yield Static(self._render_footer(), id="footer")

    def on_mount(self) -> None:
        """Initialize the view."""
        _log.debug(f"MainScreen.on_mount: steps={len(self.run_data.steps)}, chunks={len(self.run_data.chunks)}")

        # Initialize _last_manifest_status BEFORE any rendering, so that
        # _count_step_failures() correctly classifies retrying vs exhausted
        # on the very first render (avoids brief "exhausted" flash).
        try:
            manifest_path = self.run_data.run_dir / "MANIFEST.json"
            if manifest_path.exists():
                with open(manifest_path) as f:
                    manifest = json.load(f)
                self._last_manifest_status = manifest.get("status", "")
                self._last_manifest_signature = self._build_manifest_signature(manifest)
            else:
                self._last_manifest_status = ""
        except Exception:
            self._last_manifest_status = ""

        # Defer title update to next event-loop tick so the screen stack
        # has registered this screen as the active one.
        self.app.call_later(self._update_terminal_title)

        # Set initial step filter to match selected_step_index (0).
        # watch_selected_step_index doesn't fire on mount because the
        # value doesn't change, so units would load unfiltered ("all").
        if self.run_data.pipeline:
            self.step_filter = self.run_data.pipeline[0]

        self._load_step_descriptions()
        self._update_pipeline_panel()
        self._update_detail_panel()
        self._update_run_stats_panel()
        self._update_chunk_stats_panel()
        self._update_footer()
        # Initialize log ticker and start auto-refresh
        self._update_log_ticker()

        # Initialize Otto orchestrator
        try:
            otto_widget = self.query_one("#otto", OttoWidget)
            otto_label = self.query_one("#otto-status", Label)
            self._otto_orchestrator = OttoOrchestrator(otto_widget, status_label=otto_label)
            # Hide Otto on narrow terminals
            otto_container = self.query_one("#otto-container", Container)
            if self.app.size.width < 100:
                otto_container.display = False
        except Exception:
            self._otto_orchestrator = None

        # Seed previous chunk states so the first tick doesn't fire events for existing state
        self._seed_chunk_states()

        # Set initial Otto narrative immediately (don't wait for first 2s refresh tick)
        if self._otto_orchestrator:
            try:
                manifest_path = self.run_data.run_dir / "MANIFEST.json"
                if manifest_path.exists():
                    with open(manifest_path) as f:
                        manifest = json.load(f)
                    manifest_status = manifest.get("status", "")
                    # Detect zombie: manifest says "running" but process is dead
                    if manifest_status == "running":
                        try:
                            health = get_process_health(self.run_data.run_dir)
                            if health["status"] == "dead":
                                manifest_status = "zombie"
                        except Exception:
                            pass
                    context = self._build_otto_context(manifest)
                    providers = self._get_providers_from_config()
                    self._otto_orchestrator.update_narrative(manifest_status, providers, context=context)
            except Exception:
                pass

        # Use set_timer with self-rescheduling instead of set_interval
        # (set_interval doesn't work reliably on pushed screens)
        self._refresh_active = True
        self.set_timer(2.0, self._do_refresh)
        self.set_timer(5.0, self._do_unit_refresh)
        self._check_batch_idle_toast()

        # Splash screen is triggered from OctobatchApp.on_mount, not here

    def _check_batch_idle_toast(self) -> None:
        """Show a reassuring toast if a batch run has been idle for >60 seconds."""
        if self._batch_toast_shown:
            return
        if getattr(self.run_data, 'mode', 'batch') != 'batch':
            return

        try:
            manifest_path = self.run_data.run_dir / "MANIFEST.json"
            if not manifest_path.exists():
                return
            with open(manifest_path) as f:
                manifest = json.load(f)
            if manifest.get("status") != "running":
                return

            # Find the most recent state-change timestamp across all chunks
            from datetime import datetime, timezone
            latest_ts = None
            for chunk_data in manifest.get("chunks", {}).values():
                submitted_at = chunk_data.get("submitted_at")
                if submitted_at:
                    try:
                        ts = datetime.fromisoformat(submitted_at.replace("Z", "+00:00"))
                        if latest_ts is None or ts > latest_ts:
                            latest_ts = ts
                    except (ValueError, TypeError):
                        pass

            if latest_ts is None:
                return

            elapsed = (datetime.now(timezone.utc) - latest_ts).total_seconds()
            if elapsed > 60:
                self._batch_toast_shown = True
                # Determine provider(s) from run config
                server_msg = "Your job is processing via batch API."
                try:
                    import yaml
                    config_path = self.run_data.run_dir / "config" / "config.yaml"
                    if config_path.exists():
                        with open(config_path) as cf:
                            config = yaml.safe_load(cf)
                        providers = set()
                        global_provider = config.get("api", {}).get("provider", "")
                        if global_provider:
                            providers.add(global_provider)
                        for step_cfg in config.get("pipeline", {}).get("steps", []):
                            if isinstance(step_cfg, dict) and step_cfg.get("provider"):
                                providers.add(step_cfg["provider"])
                        if len(providers) == 1:
                            provider_name = next(iter(providers))
                            display_names = {
                                "gemini": "Google",
                                "openai": "OpenAI",
                                "anthropic": "Anthropic",
                            }
                            display = display_names.get(provider_name, provider_name.title())
                            server_msg = f"Your job is running on {display}'s servers."
                except Exception:
                    pass
                self.notify(
                    f"Batch processing can take several minutes. {server_msg}",
                    timeout=8,
                )
        except Exception:
            pass

    def _seed_chunk_states(self) -> None:
        """Snapshot current chunk states so the first diff tick doesn't fire spurious events."""
        try:
            manifest_path = self.run_data.run_dir / "MANIFEST.json"
            if not manifest_path.exists():
                return
            with open(manifest_path) as f:
                manifest = json.load(f)
            for name, data in manifest.get("chunks", {}).items():
                self._previous_chunk_states[name] = data.get("state", "")
                self._previous_chunk_retries[name] = data.get("retries", 0)
        except Exception:
            pass

    def _diff_chunk_states(self) -> None:
        """Compare current manifest chunk states to previous, fire Otto events on changes."""
        if not self._otto_orchestrator:
            return
        try:
            manifest_path = self.run_data.run_dir / "MANIFEST.json"
            if not manifest_path.exists():
                return
            with open(manifest_path) as f:
                manifest = json.load(f)

            run_id = self.run_data.run_name
            manifest_status = manifest.get("status", "")
            chunks = manifest.get("chunks", {})
            had_events = False

            for name, data in chunks.items():
                state = data.get("state", "")
                retries = data.get("retries", 0)
                prev_state = self._previous_chunk_states.get(name, "")
                prev_retries = self._previous_chunk_retries.get(name, 0)

                if state != prev_state:
                    had_events = True
                    if state == "VALIDATED":
                        self._otto_orchestrator.on_chunk_complete(run_id)
                    elif prev_state and state != prev_state:
                        self._otto_orchestrator.on_chunk_advance(run_id)

                if retries > prev_retries:
                    had_events = True
                    self._otto_orchestrator.on_chunk_retry(run_id)

                self._previous_chunk_states[name] = state
                self._previous_chunk_retries[name] = retries

            # Run-level completion
            old_run_status = getattr(self, '_last_manifest_status', None)
            if manifest_status == "complete" and old_run_status == "running":
                self._otto_orchestrator.on_run_complete(run_id)
                self.notify(
                    f"\U0001f419 Otto finished {run_id}!",
                    title="Run Complete",
                    timeout=8,
                )

            # Idle detection
            if manifest_status != "running" and not self._idle_toast_shown:
                all_terminal = all(
                    data.get("state", "") in ("VALIDATED", "FAILED")
                    for data in chunks.values()
                ) if chunks else False
                if all_terminal and not had_events:
                    self._idle_toast_shown = True
                    self.notify(
                        "\U0001f419 Otto is taking a nap...",
                        title="All Done",
                        timeout=5,
                    )

            # Update Otto narrative based on run state and providers
            providers = self._get_providers_from_config()
            context = self._build_otto_context(manifest)
            self._otto_orchestrator.update_narrative(manifest_status, providers, context=context)

            self._last_manifest_status = manifest_status
        except Exception:
            pass

    def _get_providers_from_config(self) -> set[str] | None:
        """Extract provider names from the run's config.yaml."""
        providers: set[str] = set()
        try:
            import yaml
            config_path = self.run_data.run_dir / "config" / "config.yaml"
            if config_path.exists():
                with open(config_path) as cf:
                    config = yaml.safe_load(cf)
                global_provider = config.get("api", {}).get("provider", "")
                if global_provider:
                    providers.add(global_provider)
                for step_cfg in config.get("pipeline", {}).get("steps", []):
                    if isinstance(step_cfg, dict) and step_cfg.get("provider"):
                        providers.add(step_cfg["provider"])
        except Exception:
            pass
        return providers or None

    def _build_otto_context(self, manifest: dict) -> dict:
        """Build context dict for Otto narrative from manifest data."""
        context: dict = {}
        chunks = manifest.get("chunks", {})

        # Check for failed steps in chunk states
        for name, data in chunks.items():
            state = data.get("state", "")
            if "_FAILED" in state:
                failed_step = state.rsplit("_FAILED", 1)[0]
                if "failed_step" not in context:
                    context["failed_step"] = failed_step

        # Count validation failures from disk across all pipeline steps.
        # The manifest's "failed" field is unreliable — it may be 0 for completed
        # runs that have validation failures in {step}_failures.jsonl files.
        total_failures = 0
        try:
            for step in self.run_data.steps:
                counts = self._count_step_failures(step.name)
                total_failures += counts.get("total", 0)
        except Exception:
            # Fallback: sum manifest chunk failed counts
            for name, data in chunks.items():
                if data.get("state") == "FAILED":
                    total_failures += 1
                total_failures += data.get("failed", 0)

        if total_failures:
            context["failure_count"] = total_failures
        return context

    def on_unmount(self) -> None:
        """Stop polling when screen unmounts."""
        self._refresh_active = False  # Stop the refresh loop
        if self._poll_timer:
            self._poll_timer.stop()
            self._poll_timer = None

    def on_screen_suspend(self) -> None:
        """Called when screen is suspended (covered by another screen)."""
        self._refresh_active = False  # Pause refresh while suspended

    def on_screen_resume(self) -> None:
        """Restart refresh when screen resumes (e.g., after modal closes)."""
        self._update_terminal_title()
        if not self._refresh_active:
            self._refresh_active = True
            self.set_timer(2.0, self._do_refresh)
            self.set_timer(5.0, self._do_unit_refresh)

    def on_resize(self, event: events.Resize) -> None:
        """Show/hide Otto based on terminal width."""
        try:
            otto_container = self.query_one("#otto-container", Container)
            otto_container.display = event.size.width >= 100
        except Exception:
            pass

    def _build_manifest_signature(self, manifest: dict) -> tuple:
        """Create a compact refresh signature from manifest metadata/chunks."""
        chunks = manifest.get("chunks", {})
        state_counts: dict[str, int] = {}
        total_items = 0
        total_valid = 0
        total_failed = 0
        total_retries = 0
        for chunk in chunks.values():
            state = chunk.get("state", "")
            state_counts[state] = state_counts.get(state, 0) + 1
            total_items += int(chunk.get("items", 0) or 0)
            total_valid += int(chunk.get("valid", 0) or 0)
            total_failed += int(chunk.get("failed", 0) or 0)
            total_retries += int(chunk.get("retries", 0) or 0)
        return (
            manifest.get("updated"),
            manifest.get("status"),
            len(chunks),
            total_items,
            total_valid,
            total_failed,
            total_retries,
            tuple(sorted(state_counts.items())),
        )

    def _read_manifest_for_refresh(self) -> tuple[dict, tuple] | None:
        """Read MANIFEST.json and return manifest plus refresh signature.

        Includes live process status in the signature so PID/process changes
        trigger UI updates even when manifest content is unchanged.
        """
        manifest_path = self.run_data.run_dir / "MANIFEST.json"
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
        except Exception:
            return None
        manifest_sig = self._build_manifest_signature(manifest)
        proc = get_run_process_status(self.run_data.run_dir)
        proc_sig = (
            bool(proc.get("alive", False)),
            proc.get("pid"),
            proc.get("source"),
            bool(has_recent_errors(self.run_data.run_dir)),
        )
        return manifest, (manifest_sig, proc_sig)

    def _do_refresh(self) -> None:
        """Perform refresh and schedule next one.

        Cheap work (spinner, log ticker) runs every tick.  Expensive work
        (manifest reload, pipeline panel, stats panel, Otto diff) is gated
        behind a manifest signature check so we skip disk I/O on no-op ticks.
        """
        if not self._refresh_active:
            return

        try:
            # --- Cheap work: always do ---
            self._spinner_index += 1
            self._update_log_ticker()
            self._update_header_stats()

            # --- Expensive work: only when manifest changed ---
            manifest_refresh = self._read_manifest_for_refresh()
            if manifest_refresh is None:
                if self._refresh_active:
                    self.set_timer(2.0, self._do_refresh)
                return
            manifest, signature = manifest_refresh
            if signature == self._last_manifest_signature:
                # Nothing changed on disk — reschedule and return early
                if self._refresh_active:
                    self.set_timer(2.0, self._do_refresh)
                return
            self._last_manifest_signature = signature
            self._pending_units_refresh = True

            # Track status before reload to detect transitions
            old_status = getattr(self, '_last_manifest_status', None)

            self._reload_realtime_progress(manifest)  # Reload realtime progress from manifest

            # For running runs, reload RunData so step states stay current
            if getattr(self, '_last_manifest_status', None) == "running":
                try:
                    self.run_data = load_run_data(self.run_data.run_dir)
                except Exception:
                    pass

            self._update_pipeline_panel()  # Refresh pipeline boxes for live progress
            self._update_run_stats_panel()
            self._diff_chunk_states()  # Fire Otto animations for state changes

            # Detect transition to terminal state (running -> complete/failed)
            # (Terminal title is synced inside _update_run_stats_panel above.)
            new_status = getattr(self, '_last_manifest_status', None)
            had_transition = old_status == "running" and new_status in ("complete", "failed")
            if had_transition:
                # Run just finished - force full reload from disk
                self.run_data = load_run_data(self.run_data.run_dir)
                self._clear_unit_cache()
                self._update_pipeline_panel()
                self._start_unit_load()
                self._update_run_stats_panel()
                self._update_chunk_stats_panel()
                self._refresh_active = False
                return

            # Stop refresh for already-terminal runs (after first tick so Otto gets set)
            if new_status in ("complete", "failed") and not had_transition:
                self._refresh_active = False
                return
        except Exception:
            pass  # Silently ignore refresh errors

        # Schedule next refresh if still active
        if self._refresh_active:
            self.set_timer(2.0, self._do_refresh)

    def _do_unit_refresh(self) -> None:
        """Refresh units from disk for running runs (5s interval, threaded).

        Only reloads after the main refresh loop has observed a manifest change.
        """
        if not self._refresh_active:
            return
        status = getattr(self, '_last_manifest_status', None)
        if status == "running" and self.current_view == "unit" and not self._units_loading:
            if self._pending_units_refresh:
                self._pending_units_refresh = False
                self._units_loaded = False
                self._start_unit_load()
        if self._refresh_active:
            self.set_timer(5.0, self._do_unit_refresh)

    def _clear_unit_cache(self) -> None:
        """Explicitly clear large lists before reloads for memory hygiene."""
        self._all_units.clear()
        self._filtered_units.clear()
        self._all_units = []
        self._filtered_units = []
        self._units_loaded = False

    def _start_unit_load(self) -> None:
        """Start loading units in a background worker."""
        if self._units_loading:
            return
        self._units_loading = True
        step = self.step_filter if self.step_filter != "all" else None
        self._load_units_worker(step)

    @work(thread=True, exclusive=True, group="main-screen-units")
    def _load_units_worker(self, step_name: str | None) -> None:
        """Load units from disk in a worker thread."""
        try:
            units = self._load_all_units(step_name=step_name)
            self.app.call_from_thread(self._on_units_loaded, units, step_name)
        except Exception:
            self._units_loading = False

    def _on_units_loaded(self, units: list[dict], loaded_step: str | None) -> None:
        """Handle completed unit load on the main thread."""
        self._units_loading = False
        current_step = self.step_filter if self.step_filter != "all" else None
        if current_step != loaded_step:
            # Step changed while loading - reload for the new step
            self._clear_unit_cache()
            self._start_unit_load()
            return
        self._all_units.clear()
        self._filtered_units.clear()
        self._all_units = units
        self._units_loaded = True
        self._unique_steps = (
            list(self.run_data.pipeline)
            if self.run_data.pipeline
            else sorted(set(u["step"] for u in units))
        )
        try:
            self._update_detail_panel()
            self._update_chunk_stats_panel()
        except Exception:
            pass

    def _reload_realtime_progress(self, manifest: dict | None = None) -> None:
        """Reload realtime progress and status changes from manifest.

        Also detects status changes (e.g., failed -> running when run is restarted)
        and reloads full run data to clear stale error messages.
        """
        try:
            if manifest is None:
                manifest_path = self.run_data.run_dir / "MANIFEST.json"
                if not manifest_path.exists():
                    return
                with open(manifest_path) as f:
                    manifest = json.load(f)

            # Check for status change that requires full reload
            # (e.g., failed run restarted, or running run completed)
            manifest_status = manifest.get("status", "")
            current_mode = getattr(self.run_data, 'mode', 'batch')

            # Detect if run was restarted (manifest shows running but we have stale data)
            needs_full_reload = False
            if manifest_status == "running" and manifest.get("restarted_at"):
                # Run was restarted - check if we have stale error data
                if getattr(self.run_data, 'total_failed', 0) > 0:
                    needs_full_reload = True

            # Also reload if status changed to/from terminal states
            old_status = getattr(self, '_last_manifest_status', None)
            if old_status is not None and old_status != manifest_status:
                needs_full_reload = True
            self._last_manifest_status = manifest_status

            if needs_full_reload:
                # Reload full run data to get updated status/errors
                self.run_data = load_run_data(self.run_data.run_dir)
                # Clear cached units so unit table reloads from disk
                self._clear_unit_cache()
                # Rebuild the detail panel (unit or chunk table)
                self._update_detail_panel()

            # Update realtime progress from manifest
            metadata = manifest.get("metadata", {})
            rt_data = metadata.get("realtime_progress", {})

            if rt_data:
                self.run_data.realtime_progress = RealtimeProgress(
                    units_completed=rt_data.get("units_completed", 0),
                    units_total=rt_data.get("units_total", 0),
                    tokens_so_far=rt_data.get("tokens_so_far", 0),
                    cost_so_far=rt_data.get("cost_so_far", 0.0),
                    estimated_remaining_seconds=rt_data.get("estimated_remaining_seconds", 0),
                )
            else:
                self.run_data.realtime_progress = None
        except Exception:
            pass  # Silently ignore errors

    def _load_step_descriptions(self) -> None:
        """Load step descriptions from the run's config snapshot (once at mount)."""
        try:
            config_path = self.run_data.run_dir / "config" / "config.yaml"
            if config_path.exists():
                import yaml
                with open(config_path) as f:
                    config = yaml.safe_load(f)
                for step_cfg in config.get("pipeline", {}).get("steps", []):
                    if isinstance(step_cfg, dict):
                        name = step_cfg.get("name", "")
                        desc = step_cfg.get("description", "")
                        if name and desc:
                            self._step_descriptions[name] = desc
        except Exception:
            pass

    def _update_header_stats(self) -> None:
        """Update the header widget with current resource stats."""
        try:
            header = self.query_one("#header", Static)
            header.update(self._render_header())
        except NoMatches:
            pass

    def _update_log_ticker(self) -> None:
        """Update the log ticker widget."""
        try:
            log_ticker = self.query_one("#log-ticker", LogTicker)
        except NoMatches:
            return

        try:
            # Check if process is running
            proc_info = self._get_process_status()
            is_running = proc_info.get("status") == "running"
            log_ticker.update_logs(is_running=is_running)
        except Exception:
            # Fall back to updating without running status
            try:
                log_ticker.update_logs(is_running=False)
            except Exception:
                pass

    def on_key(self, event: events.Key) -> None:
        """Intercept keys before widgets handle them."""
        _log.debug(f"MainScreen.on_key: key={event.key}")
        if event.key == "tab":
            event.prevent_default()
            event.stop()
        elif event.key == "left":
            self.action_step_prev()
            event.prevent_default()
            event.stop()
        elif event.key == "right":
            self.action_step_next()
            event.prevent_default()
            event.stop()
        elif event.key == "up":
            self.action_nav_up()
            event.prevent_default()
            event.stop()
        elif event.key == "down":
            self.action_nav_down()
            event.prevent_default()
            event.stop()
        elif event.key == "enter":
            self.action_select_item()
            event.prevent_default()
            event.stop()

    def _render_header(self) -> str:
        """Render the header with version, run info and resource stats."""
        stats = get_resource_stats()
        if stats:
            return f"[bold]Octobatch[/] [dim]v{__version__}[/]  {self.run_data.run_name}                [dim]{stats}[/]"
        return f"[bold]Octobatch[/] [dim]v{__version__}[/]  {self.run_data.run_name}"

    def _update_terminal_title(self) -> None:
        """Update the OS terminal window/tab title with pipeline name and status.

        The Header widget (self.app.title) is only updated when this screen
        is the topmost screen.  The OS terminal title is always written via
        set_os_terminal_title(), which deduplicates internally — if another
        screen has already set a different title, our stale string won't
        match and the write will be skipped by the cache.
        """
        # Use the enhanced process-aware status (e.g. "process lost")
        # rather than the raw manifest status (which may still say "running").
        proc_info = self._get_process_status()
        status_map = {
            "running": "running",
            "stuck": "stuck",
            "process_lost": "process lost",
            "not_running": getattr(self, '_last_manifest_status', '') or 'unknown',
        }
        status = status_map.get(proc_info["status"], getattr(self, '_last_manifest_status', '') or 'unknown')
        pipeline = self.run_data.pipeline_name or self.run_data.run_name
        title = f"Octobatch v{__version__} \u2013 {pipeline} pipeline ({status})"
        if self.app.screen is self:
            self.app.title = title
        set_os_terminal_title(title)

    def _render_footer(self) -> str:
        """Render the footer with key bindings based on current view."""
        if self.current_view == "chunk":
            if self.active_focus == "pipeline":
                return "←→:step  ↑↓:panel  V:units  X:archive  A:files  D:diag  I:info  L:log  Esc:back  Q:quit"
            else:
                return "←→:step  ↑↓:chunk  Enter:details  V:units  X:archive  A:files  D:diag  I:info  L:log  Esc:back  Q:quit"
        else:
            # Unit view - show filter/sort state
            status_label = f"F:{self.status_filter[:3]}"
            sort_label = f"S:{self.sort_by[:4]}"
            # Count retryable (validation) failures for retry hint
            _val_stages = {"schema_validation", "validation"}
            retryable_count = sum(
                1 for u in self._filtered_units
                if u.get("status") == "failed"
                and u.get("failure_stage", "validation") in _val_stages
            )
            retry_hint = f"R:retry({retryable_count})" if retryable_count > 0 else ""
            return f"←→:step  ↑↓:select  Enter:detail  {status_label}  {sort_label}  {retry_hint}  X:archive  A:files  D:diag  V:chunks  I:info  Esc:back  Q:quit"

    # --- Unit Data Loading ---

    def _parse_step_from_state(self, state: str) -> str | None:
        """Extract step name from chunk state like 'generate_SUBMITTED'."""
        if not state:
            return None

        # States look like: generate_SUBMITTED, score_coherence_PENDING, VALIDATED
        if state in ("VALIDATED", "PENDING", "FAILED"):
            return None

        # Remove the status suffix
        for suffix in ["_SUBMITTED", "_PENDING", "_COMPLETE"]:
            if state.endswith(suffix):
                return state[:-len(suffix)]

        return None

    def _load_pending_units_for_chunk(
        self,
        chunk_dir: Path,
        current_step: str,
        chunk_state: str,
        completed_unit_ids: set
    ) -> list[dict]:
        """Load pending/processing units from batch input or prompts files.

        This loads from the actual processed files, NOT from units.jsonl which
        may contain all permutations before the limit was applied.
        """
        pending_units = []

        # Determine status based on chunk state
        if chunk_state.endswith("_SUBMITTED"):
            status = "processing"
        else:
            status = "pending"

        # Try several sources for pending unit IDs:
        # 1. Batch input file (Gemini format): {step}_input.jsonl
        # 2. Prompts file: {step}_prompts.jsonl
        # 3. Results file (for units still being processed): {step}_results.jsonl

        # Try batch input file first
        input_file = chunk_dir / f"{current_step}_input.jsonl"
        if input_file.exists():
            try:
                with _open_jsonl(input_file) as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                            unit_id = self._extract_unit_id_from_batch_request(data)
                            if unit_id and unit_id not in completed_unit_ids:
                                pending_units.append({
                                    "unit_id": unit_id,
                                    "step": current_step,
                                    "status": status,
                                    "data": None,
                                    "errors": None,
                                    "attempts": 0,
                                })
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                _log.debug(f"Error reading {input_file}: {e}")

            if pending_units:
                return pending_units

        # Try prompts file
        prompts_file = chunk_dir / f"{current_step}_prompts.jsonl"
        if prompts_file.exists():
            try:
                with _open_jsonl(prompts_file) as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                            unit_id = data.get("unit_id", "unknown")
                            if unit_id not in completed_unit_ids:
                                pending_units.append({
                                    "unit_id": unit_id,
                                    "step": current_step,
                                    "status": status,
                                    "data": data,
                                    "errors": None,
                                    "attempts": 0,
                                })
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                _log.debug(f"Error reading {prompts_file}: {e}")

        return pending_units

    def _extract_unit_id_from_batch_request(self, data: dict) -> str | None:
        """Extract unit_id from a batch API request format."""
        # Gemini batch format uses custom_id
        if "custom_id" in data:
            return data["custom_id"]
        # Check for metadata.unit_id
        if "metadata" in data and isinstance(data["metadata"], dict):
            if "unit_id" in data["metadata"]:
                return data["metadata"]["unit_id"]
        # Try direct unit_id field
        return data.get("unit_id")

    def _get_manifest_total_items(self) -> int:
        """Get total expected items from manifest chunk data."""
        manifest_path = self.run_data.run_dir / "MANIFEST.json"
        if not manifest_path.exists():
            return 0
        try:
            manifest = json.loads(manifest_path.read_text())
            return sum(
                chunk.get("items", 0)
                for chunk in manifest.get("chunks", {}).values()
            )
        except Exception:
            return 0

    def _get_unit_load_cap(self) -> int:
        """Load visible rows plus a small buffer, not the whole run."""
        visible_rows = max(20, self.size.height - 16)
        return min(1200, max(120, visible_rows * 4))

    def _load_all_units(self, step_name: str | None = None) -> list[dict]:
        """Load units from manifest and result files for a specific step.

        Stops reading JSONL files once the visible-row cap is reached.
        Header counts come from manifest data, not from loaded row count.

        Args:
            step_name: Pipeline step to load units for. If None, loads all steps.
        """
        units = []
        cap = self._get_unit_load_cap()
        run_dir = self.run_data.run_dir
        chunks_dir = run_dir / "chunks"

        # Load manifest to get chunk info
        manifest_path = run_dir / "MANIFEST.json"
        if not manifest_path.exists():
            _log.debug(f"No manifest found at {manifest_path}")
            return units

        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception as e:
            _log.debug(f"Error reading manifest: {e}")
            return units

        pipeline_steps = manifest.get("pipeline", [])
        # Determine which steps to load
        steps_to_load = [step_name] if step_name and step_name in pipeline_steps else pipeline_steps

        # Track completed unit IDs per chunk to avoid duplicates
        capped = False
        for chunk_name, chunk_data in manifest.get("chunks", {}).items():
            if capped:
                break
            chunk_dir = chunks_dir / chunk_name
            chunk_state = chunk_data.get("state", "")
            current_step = self._parse_step_from_state(chunk_state)

            completed_unit_ids = set()

            # Load completed/validated units from files
            for step in steps_to_load:
                if capped:
                    break
                # Check for validated results
                validated_path = _find_jsonl_file(chunk_dir / f"{step}_validated.jsonl")
                if validated_path:
                    try:
                        with _open_jsonl(validated_path) as f:
                            for line in f:
                                if len(units) >= cap:
                                    capped = True
                                    break
                                if line.strip():
                                    try:
                                        data = json.loads(line)
                                        unit_id = data.get("unit_id", "unknown")
                                        completed_unit_ids.add(unit_id)
                                        units.append({
                                            "unit_id": unit_id,
                                            "chunk": chunk_name,
                                            "step": step,
                                            "status": "valid",
                                            "data": data,
                                            "errors": None,
                                            "attempts": data.get("retry_count", 0) + 1,
                                        })
                                    except json.JSONDecodeError:
                                        continue
                    except Exception as e:
                        _log.debug(f"Error reading {validated_path}: {e}")

                if capped:
                    break

                # Check for failed results
                failures_path = _find_jsonl_file(chunk_dir / f"{step}_failures.jsonl")
                if failures_path:
                    try:
                        with _open_jsonl(failures_path) as f:
                            for line in f:
                                if len(units) >= cap:
                                    capped = True
                                    break
                                if line.strip():
                                    try:
                                        data = json.loads(line)
                                        unit_id = data.get("unit_id", "unknown")
                                        completed_unit_ids.add(unit_id)
                                        units.append({
                                            "unit_id": unit_id,
                                            "chunk": chunk_name,
                                            "step": step,
                                            "status": "failed",
                                            "data": data.get("input", data),
                                            "raw_response": data.get("raw_response"),  # LLM output that failed validation
                                            "errors": data.get("errors", []),
                                            "failure_stage": data.get("failure_stage", "unknown"),
                                            "attempts": data.get("retry_count", 0) + 1,
                                        })
                                    except json.JSONDecodeError:
                                        continue
                    except Exception as e:
                        _log.debug(f"Error reading {failures_path}: {e}")

            # For chunks that are in-progress or pending, load from batch/prompt files (not units.jsonl)
            # units.jsonl may contain ALL permutations, but we only want the actual limited set
            # Only load pending units if the chunk's current step matches what we're loading
            if not capped and chunk_state not in ("VALIDATED", "FAILED") and chunk_dir.exists() and current_step:
                if step_name is None or current_step == step_name:
                    pending_units = self._load_pending_units_for_chunk(
                        chunk_dir, current_step, chunk_state, completed_unit_ids
                    )
                    for pu in pending_units:
                        if len(units) >= cap:
                            capped = True
                            break
                        pu["chunk"] = chunk_name
                        units.append(pu)

        _log.debug(f"Loaded {len(units)} units for step={step_name or 'all'} (capped={capped})")
        return units

    def _get_filtered_units(self) -> list[dict]:
        """Get filtered and sorted units for display."""
        units = self._all_units

        # Apply status filter
        if self.status_filter != "all":
            units = [u for u in units if u["status"] == self.status_filter]

        # Apply step filter
        if self.step_filter != "all":
            units = [u for u in units if u["step"] == self.step_filter]

        # Apply sort
        if self.sort_by == "status":
            units = sorted(units, key=lambda u: (0 if u["status"] == "failed" else 1, u["unit_id"]))
        elif self.sort_by == "step":
            units = sorted(units, key=lambda u: (u["step"], u["unit_id"]))
        else:  # unit_id
            units = sorted(units, key=lambda u: u["unit_id"])

        return units

    # --- View Watchers ---

    def watch_selected_step_index(self, new_index: int) -> None:
        """Update when step selection changes."""
        _log.debug(f"watch_selected_step_index: {new_index}")
        self.selected_chunk_index = 0

        # When step changes, also filter units to that step (if we have steps)
        if self.run_data.pipeline and new_index < len(self.run_data.pipeline):
            step_name = self.run_data.pipeline[new_index]
            self.step_filter = step_name
            self.selected_unit_index = 0
            # Clear cached units so they reload scoped to the new step
            self._clear_unit_cache()

        self._update_pipeline_panel()
        self._update_detail_panel()
        self._update_run_stats_panel()
        self._update_chunk_stats_panel()
        self._update_footer()

    def watch_active_focus(self, new_focus: str) -> None:
        """Update visual focus indicators."""
        _log.debug(f"watch_active_focus: {new_focus}")
        try:
            pipeline_panel = self.query_one("#pipeline-panel", Vertical)
            detail_panel = self.query_one("#detail-panel", VerticalScroll)

            if new_focus == "pipeline":
                pipeline_panel.remove_class("inactive")
                detail_panel.add_class("inactive")
            else:
                pipeline_panel.add_class("inactive")
                detail_panel.remove_class("inactive")
        except NoMatches:
            pass
        self._update_footer()

    def watch_current_view(self, new_view: str) -> None:
        """Update when view changes between chunk and unit."""
        _log.debug(f"watch_current_view: {new_view}")
        self._update_detail_panel()
        self._update_chunk_stats_panel()
        self._update_footer()

    # --- Panel Updates ---

    def _update_footer(self) -> None:
        """Update footer text."""
        try:
            footer = self.query_one("#footer", Static)
            footer.update(self._render_footer())
        except NoMatches:
            pass

    def _get_process_status(self) -> dict:
        """Get current process status for this run."""
        proc_status = get_run_process_status(self.run_data.run_dir)
        has_errors = has_recent_errors(self.run_data.run_dir)
        manifest_status = None
        try:
            manifest_path = self.run_data.run_dir / "MANIFEST.json"
            if manifest_path.exists():
                with open(manifest_path) as f:
                    manifest = json.load(f)
                manifest_status = manifest.get("status")
        except Exception:
            manifest_status = None

        # Determine overall status
        if proc_status.get("alive"):
            if has_errors:
                status = "stuck"
            else:
                status = "running"
        elif manifest_status == "running":
            # Manifest claims active processing but current PID source is dead.
            status = "process_lost"
        else:
            status = "not_running"

        return {
            "status": status,
            "has_errors": has_errors,
            "pid": proc_status.get("pid"),
            "alive": proc_status.get("alive", False),
            "manifest_status": manifest_status,
        }

    def _update_run_stats_panel(self) -> None:
        """Update run stats with totals, projections, and selected step info."""
        try:
            panel = self.query_one("#run-stats-panel", Static)
        except NoMatches:
            return

        # Get cost and tokens - calculate from manifest if not available
        total_cost = getattr(self.run_data, 'total_cost', 0) or 0
        total_tokens = getattr(self.run_data, 'total_tokens', 0) or 0

        # If cost is 0, try to calculate from manifest tokens
        if total_cost == 0 and self.run_data.run_dir:
            cost, tokens = self._calculate_cost_from_manifest()
            if cost > 0:
                total_cost = cost
            if tokens > 0:
                total_tokens = tokens

        duration = getattr(self.run_data, 'elapsed_time', '--:--') or '--:--'
        mode = getattr(self.run_data, 'mode', 'unknown') or 'unknown'
        mode_display = "Realtime" if mode == "realtime" else "Batch"

        # Check for max_units (stored in RunData from manifest metadata)
        max_units_str = ""
        if self.run_data and self.run_data.max_units is not None:
            max_units_str = f"\nMax Units:     {self.run_data.max_units}"

        content = f"""[bold]Run Stats[/]
────────────────────
Total Cost:    ${total_cost:.4f}
Total Tokens:  {total_tokens:,}
Duration:      {duration}
Mode:          {mode_display}{max_units_str}
"""

        # Add realtime progress section for running realtime jobs
        rt_progress = getattr(self.run_data, 'realtime_progress', None)
        if rt_progress and rt_progress.units_total > 0:
            pct = rt_progress.progress_percent
            content += "\n[bold cyan]Realtime Progress[/]\n"
            content += "────────────────────\n"
            content += f"Progress:      {rt_progress.units_completed}/{rt_progress.units_total} ({pct:.1f}%)\n"
            content += f"Tokens:        {rt_progress.tokens_formatted}\n"
            content += f"Cost:          {rt_progress.cost_formatted}\n"
            if rt_progress.estimated_remaining_seconds > 0:
                content += f"Time Left:     {rt_progress.time_remaining_formatted}\n"
            # Throughput: compute from elapsed time and units completed
            throughput = self._compute_realtime_throughput(rt_progress)
            if throughput:
                content += f"Rate:          {throughput}\n"
            # Current unit from last log line
            current_unit = self._get_current_unit_from_log()
            if current_unit:
                content += f"Processing:    [cyan]{current_unit}[/]\n"

        # Add projections if we have progress (fallback for batch mode or no realtime data)
        proj = self._calculate_projections(total_cost)
        proj_pct = proj.get("progress_percent", 0)
        proj_status = proj.get("manifest_status", "")
        if proj_pct > 0 and not rt_progress:
            content += "────────────────────\n"
            if proj_status == "complete":
                content += "Progress:      [green]100% / Complete[/]\n"
            elif proj_status == "failed":
                content += f"Progress:      [red]{proj_pct:.1f}% / Failed[/]\n"
            else:
                content += f"Progress:      {proj_pct:.1f}%\n"
                if proj.get("projected_cost"):
                    content += f"Est. Total:    ${proj['projected_cost']:.2f}\n"
                if proj.get("eta"):
                    content += f"ETA:           {proj['eta']}\n"

        # Add process status section with timing
        proc_info = self._get_process_status()
        content += "\n[bold]Process Status[/]\n────────────────────\n"

        if proc_info["status"] == "stuck":
            content += "[yellow]⚠ STUCK[/]\n"
            if proc_info["pid"]:
                content += f"PID: {proc_info['pid']}\n"
            content += "[yellow]Errors in log[/]\n"
            content += "[dim]Press I for details[/]\n"
        elif proc_info["status"] == "running":
            spinner = SPINNER_FRAMES[self._spinner_index % len(SPINNER_FRAMES)]
            content += f"[green]{spinner} RUNNING[/]\n"
            if proc_info["pid"]:
                content += f"PID: {proc_info['pid']}\n"
            # Add timing for running processes
            timing = self._get_timing_display()
            if timing:
                content += f"{timing}\n"
        elif proc_info["status"] == "process_lost":
            content += "[bold red]✗ PROCESS LOST[/]\n"
            if proc_info["pid"]:
                content += f"Last PID: {proc_info['pid']}\n"
            content += "[dim]Manifest says running, but current PID is dead[/]\n"
            content += "[dim]Press R to resume watcher[/]\n"
        else:
            # Not running - check manifest status for completion state
            manifest_status = proc_info.get("manifest_status")
            if manifest_status == "complete":
                content += "[bold green]🏁 COMPLETE[/]\n"
            elif manifest_status == "failed":
                content += "[bold red]❌ FAILED[/]\n"
            elif manifest_status == "paused":
                content += "[yellow]⏸ PAUSED[/]\n"
            else:
                content += "[dim]○ NOT RUNNING[/]\n"

        if self.run_data.steps and self.selected_step_index < len(self.run_data.steps):
            step = self.run_data.steps[self.selected_step_index]
            step_cost = getattr(step, 'cost', 0) or 0
            passed = getattr(step, 'completed', 0) or 0
            total = getattr(step, 'total', 0) or 0

            # Use helper to get ACTUAL categorized failures from failure files on disk
            failure_cats = self._count_step_failures(step.name)
            val_fails = failure_cats["validation"]
            hard_fails = failure_cats["hard"]
            failed = failure_cats["total"]

            # Derive processing/pending count (units not yet passed or failed)
            processing = max(0, total - passed - failed)

            # Check if this step has loop_until
            loop_info = ""
            config = None
            step_config = None
            try:
                config_path = self.run_data.run_dir / "config" / "config.yaml"
                if config_path.exists():
                    import yaml
                    with open(config_path) as f:
                        config = yaml.safe_load(f)
                    step_configs = config.get("pipeline", {}).get("steps", [])
                    if self.selected_step_index < len(step_configs):
                        step_config = step_configs[self.selected_step_index]
            except Exception:
                pass

            has_loop = step_config and isinstance(step_config, dict) and step_config.get("loop_until")
            step_display_name = step.name.upper()
            if has_loop:
                step_display_name = f"{step_display_name} [magenta]↻[/]"

            # Build optional description line (dim italic, truncated if very long)
            step_desc = self._step_descriptions.get(step.name, "")
            desc_line = ""
            if step_desc:
                if len(step_desc) > 120:
                    step_desc = step_desc[:117] + "..."
                desc_line = f"\n[dim italic]{step_desc}[/]"

            # Build provider/model display lines from config
            provider_line = ""
            model_line = ""
            if config and isinstance(config, dict):
                global_provider = config.get("api", {}).get("provider", "")
                global_model = config.get("api", {}).get("model", "")
                step_prov = step_config.get("provider", "") if step_config and isinstance(step_config, dict) else ""
                step_mdl = step_config.get("model", "") if step_config and isinstance(step_config, dict) else ""
                display_provider = step_prov or global_provider
                display_model = step_mdl or global_model
                prov_tag = "override" if step_prov else "default"
                mdl_tag = "override" if step_mdl else "default"
                if display_provider:
                    provider_line = f"\nProvider:  [cyan]{display_provider}[/] [dim]({prov_tag})[/]"
                if display_model:
                    model_line = f"\nModel:     [cyan]{display_model}[/] [dim]({mdl_tag})[/]"

            # Build failure display lines (split by retry state)
            failure_lines = ""
            retrying = failure_cats.get("retrying", 0)
            exhausted = failure_cats.get("exhausted", 0)
            max_retry_attempt = failure_cats.get("max_retry_attempt", 0)
            max_retries_cfg = failure_cats.get("max_retries", 5)

            if retrying > 0:
                attempt_display = max_retry_attempt + 1  # 0-based -> 1-based
                failure_lines += f"Retrying:      [yellow]{retrying}[/] [dim](attempt {attempt_display}/{max_retries_cfg})[/]\n"
            if exhausted > 0:
                failure_lines += f"Failed:        [dark_orange]{exhausted}[/] [dim](max retries)[/]\n"
            if hard_fails > 0:
                failure_lines += f"Failed:        [red]{hard_fails}[/]\n"
            if retrying == 0 and exhausted == 0 and hard_fails == 0:
                failure_lines += "Failed:        0\n"

            content += f"""
[bold]Selected Step[/]
────────────────────
{step_display_name}{desc_line}{provider_line}{model_line}
Passed:        {passed}
{failure_lines}"""
            if processing > 0:
                content += f"Processing:    {processing}\n"

            # Add loop info if this is a looping step
            if has_loop:
                loop_until = step_config.get("loop_until", "")
                content += f"\n[bold]Loop Info:[/]\n"
                content += f"  Condition: [cyan]{loop_until}[/]\n"

                # Calculate average iterations from validated output
                try:
                    chunks_dir = self.run_data.run_dir / "chunks"
                    if chunks_dir.exists():
                        iterations_list = []
                        for chunk_dir in chunks_dir.glob("chunk_*"):
                            if not chunk_dir.is_dir():
                                continue
                            validated_path = _find_jsonl_file(chunk_dir / f"{step.name}_validated.jsonl")
                            if validated_path:
                                with _open_jsonl(validated_path) as f:
                                    for line in f:
                                        if line.strip():
                                            try:
                                                record = json.loads(line)
                                                iters = record.get("_metadata", {}).get("iterations")
                                                if iters is not None:
                                                    iterations_list.append(iters)
                                            except json.JSONDecodeError:
                                                pass
                        if iterations_list:
                            avg_iters = sum(iterations_list) / len(iterations_list)
                            content += f"  Avg Iters: [green]{avg_iters:.1f}[/]\n"
                except Exception:
                    pass

            # Failure Summary section (grouped by error pattern)
            if failed > 0:
                summary = self._get_failure_summary(step.name)
                if summary:
                    content += "\n[bold]Failure Summary[/]\n"
                    content += "────────────────────\n"
                    val_entries = [(p, c) for s, p, c in summary if s == "validation"]
                    hard_entries = [(p, c) for s, p, c in summary if s == "hard"]
                    if val_entries:
                        total_val = sum(c for _, c in val_entries)
                        content += f"[yellow]⚠ {total_val} validation[/]\n"
                        for pattern, count in val_entries[:3]:
                            short = pattern[:40] if len(pattern) > 40 else pattern
                            content += f"  {short}: {count}\n"
                    if hard_entries:
                        total_hard = sum(c for _, c in hard_entries)
                        content += f"[red]✗ {total_hard} hard failures[/]\n"
                        for pattern, count in hard_entries[:3]:
                            short = pattern[:40] if len(pattern) > 40 else pattern
                            content += f"  {short}: {count}\n"

        panel.update(content)

        # Sync the OS terminal title every time stats are rendered so the
        # title always reflects the current manifest state.  This covers the
        # initial on_mount render *and* every subsequent _do_refresh tick.
        self._update_terminal_title()

    def _compute_realtime_throughput(self, rt_progress) -> str:
        """Compute throughput string from realtime progress and run start time.

        Returns something like '~4.2s/unit' or empty string.
        """
        if not rt_progress or rt_progress.units_completed <= 0:
            return ""
        try:
            started = getattr(self.run_data, 'started', None)
            if not started:
                return ""
            from datetime import datetime, timezone
            if isinstance(started, str):
                started_dt = datetime.fromisoformat(started.replace('Z', '+00:00'))
            elif isinstance(started, datetime):
                started_dt = started
            else:
                return ""
            if started_dt.tzinfo is None:
                started_dt = started_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            elapsed = (now - started_dt).total_seconds()
            if elapsed <= 0:
                return ""
            secs_per_unit = elapsed / rt_progress.units_completed
            if secs_per_unit < 1:
                return f"~{secs_per_unit:.2f}s/unit"
            elif secs_per_unit < 60:
                return f"~{secs_per_unit:.1f}s/unit"
            else:
                mins = secs_per_unit / 60
                return f"~{mins:.1f}m/unit"
        except Exception:
            return ""

    def _get_current_unit_from_log(self) -> str:
        """Get the most recently processed unit ID from the last log line.

        Parses lines like: [14:30:15] [5/20] unit_name ✓ ...
        Returns the unit_id or empty string.
        """
        try:
            log_file = self.run_data.run_dir / "RUN_LOG.txt"
            if not log_file.exists():
                return ""
            with open(log_file, "rb") as f:
                f.seek(0, 2)
                file_size = f.tell()
                read_size = min(2048, file_size)
                f.seek(max(0, file_size - read_size))
                content = f.read().decode("utf-8", errors="replace")
            lines = content.strip().split("\n")
            # Search from the end for a line with [N/M] pattern
            for line in reversed(lines):
                match = re.search(r'\[\d+/\d+\]\s+(\S+)\s+[✓✗]', line)
                if match:
                    unit_id = match.group(1)
                    if len(unit_id) > 30:
                        unit_id = unit_id[:27] + "..."
                    return unit_id
        except Exception:
            pass
        return ""

    def _calculate_projections(self, current_cost: float) -> dict:
        """Calculate projected cost and time based on current progress."""
        run_dir = self.run_data.run_dir
        manifest_path = run_dir / "MANIFEST.json"

        if not manifest_path.exists():
            return {}

        try:
            with open(manifest_path) as f:
                manifest = json.load(f)

            manifest_status = manifest.get("status", "")

            # Terminal runs: return fixed progress, no ETA
            if manifest_status == "complete":
                return {"progress_percent": 100, "manifest_status": "complete"}
            if manifest_status == "failed":
                # Calculate actual progress for display
                from scripts.tui.utils.runs import get_run_progress
                pct = get_run_progress(manifest)
                return {"progress_percent": pct, "manifest_status": "failed"}

            # Get progress from chunks
            chunks = manifest.get("chunks", {})
            total_units = sum(c.get("items", 0) for c in chunks.values())
            completed_units = sum(
                c.get("valid", 0) + c.get("failed", 0)
                for c in chunks.values()
            )

            if total_units == 0 or completed_units == 0:
                return {}

            progress_ratio = completed_units / total_units

            # Project cost
            projected_cost = None
            if progress_ratio > 0.01 and current_cost > 0:  # At least 1% progress
                projected_cost = current_cost / progress_ratio

            # Project time using elapsed time from run_data
            eta = None
            elapsed_str = getattr(self.run_data, 'elapsed_time', '')
            if elapsed_str and progress_ratio > 0.01:
                elapsed_seconds = self._parse_elapsed_time(elapsed_str)
                if elapsed_seconds and elapsed_seconds > 0:
                    projected_seconds = elapsed_seconds / progress_ratio
                    remaining_seconds = projected_seconds - elapsed_seconds
                    if remaining_seconds > 0:
                        eta = self._format_eta(remaining_seconds)

            return {
                "projected_cost": projected_cost,
                "eta": eta,
                "progress_percent": progress_ratio * 100,
                "manifest_status": manifest_status,
            }
        except Exception:
            return {}

    def _parse_elapsed_time(self, elapsed_str: str) -> int | None:
        """Parse elapsed time string to seconds."""
        try:
            # Format: "1:23:45" or "23:45" or "45s" etc
            if not elapsed_str or elapsed_str == '--:--':
                return None

            parts = elapsed_str.replace('s', '').replace('m', '').replace('h', '').split(':')
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            elif len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            elif len(parts) == 1:
                return int(parts[0])
        except (ValueError, IndexError):
            pass
        return None

    def _format_eta(self, seconds: float) -> str:
        """Format ETA in human-readable form."""
        seconds = int(seconds)
        if seconds < 60:
            return f"~{seconds}s"
        elif seconds < 3600:
            mins = seconds // 60
            return f"~{mins}m"
        else:
            hours = seconds // 3600
            mins = (seconds % 3600) // 60
            if mins > 0:
                return f"~{hours}h {mins}m"
            return f"~{hours}h"

    def _calculate_cost_from_manifest(self) -> tuple[float, int]:
        """Calculate cost from manifest token counts."""
        run_dir = self.run_data.run_dir
        manifest_path = run_dir / "MANIFEST.json"

        if not manifest_path.exists():
            return (0.0, 0)

        try:
            with open(manifest_path) as f:
                manifest = json.load(f)

            # Get tokens from metadata
            metadata = manifest.get("metadata", {})
            input_tokens = metadata.get("initial_input_tokens", 0) + metadata.get("retry_input_tokens", 0)
            output_tokens = metadata.get("initial_output_tokens", 0) + metadata.get("retry_output_tokens", 0)
            total_tokens = input_tokens + output_tokens

            # Also check chunk-level tokens
            if total_tokens == 0:
                for chunk_data in manifest.get("chunks", {}).values():
                    input_tokens += chunk_data.get("input_tokens", 0)
                    output_tokens += chunk_data.get("output_tokens", 0)
                total_tokens = input_tokens + output_tokens

            # Load pricing from config
            config_name = manifest.get("config", "")
            config_path = run_dir / config_name if config_name else None
            input_rate = 0.075  # Default Gemini Flash Batch pricing
            output_rate = 0.30

            if config_path and config_path.exists():
                try:
                    import yaml
                    with open(config_path) as f:
                        config = yaml.safe_load(f)
                    pricing = config.get("api", {}).get("pricing", {})
                    input_rate = pricing.get("input_per_million", input_rate)
                    output_rate = pricing.get("output_per_million", output_rate)
                except Exception:
                    pass

            cost = (input_tokens / 1_000_000 * input_rate) + (output_tokens / 1_000_000 * output_rate)
            return (cost, total_tokens)
        except Exception:
            return (0.0, 0)

    def _get_timing_display(self) -> str:
        """Get Last/Next timing for batch mode."""
        run_dir = self.run_data.run_dir
        mode = getattr(self.run_data, 'mode', 'batch')

        if mode != "batch":
            return ""

        try:
            health = get_process_health(run_dir)

            if health["status"] == "hung":
                mins = int(health.get("last_activity_seconds", 0) // 60)
                return f"[yellow]HUNG {mins}m[/]"

            timing = get_batch_timing(run_dir, poll_interval=self.run_data.poll_interval)
            last_tick = timing.get("last_tick_seconds")
            next_tick = timing.get("next_tick_seconds")

            # Format timing
            if last_tick is not None:
                if last_tick < 60:
                    last_str = f"{int(last_tick)}s"
                else:
                    last_str = f"{int(last_tick // 60)}m"
            else:
                last_str = "--"

            if next_tick is not None:
                if next_tick < 0:
                    next_str = "[bold bright_green]now[/]"
                elif next_tick < 1:
                    next_str = "[bold bright_green]0s[/]"
                elif next_tick < 60:
                    next_str = f"{int(next_tick)}s"
                else:
                    next_str = f"{int(next_tick // 60)}m"
            else:
                next_str = "--"

            return f"Last: {last_str} | Next: {next_str}"
        except Exception:
            return ""

    def _update_chunk_stats_panel(self) -> None:
        """Update chunk stats panel based on current view."""
        try:
            panel = self.query_one("#chunk-stats-panel", Static)
        except NoMatches:
            return

        if self.current_view == "chunk":
            # Chunk view - show selected chunk stats
            if self._chunks_for_step and self.selected_chunk_index < len(self._chunks_for_step):
                chunk = self._chunks_for_step[self.selected_chunk_index]
                current_step, status, _ = parse_chunk_state(chunk.state, self.run_data.pipeline)
                step_display = current_step if current_step not in ("pending", "unknown") else "pending"
                symbol = get_chunk_status_symbol(chunk.state, self.run_data.pipeline)

                # Show errors with warning if any
                if chunk.failed > 0:
                    errors_display = f"[red]⚠ {chunk.failed}[/]"
                else:
                    errors_display = str(chunk.failed)

                content = f"""[bold]Chunk Stats[/]
\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
{chunk.name}
Units:         {chunk.valid}/{chunk.total}
Status:        {symbol} {step_display}
Errors:        {errors_display}
"""
            else:
                content = "[dim]No chunk selected[/]"
        else:
            # Unit view - show manifest totals plus current cached row window.
            manifest_total = sum(c.total for c in self.run_data.chunks)
            manifest_valid = sum(c.valid for c in self.run_data.chunks)
            manifest_failed = sum(c.failed for c in self.run_data.chunks)
            progress_pct = int((manifest_valid / manifest_total) * 100) if manifest_total > 0 else 0
            total = len(self._all_units)
            valid_count = sum(1 for u in self._all_units if u["status"] == "valid")
            failed_count = sum(1 for u in self._all_units if u["status"] == "failed")
            pending_count = max(0, manifest_total - manifest_valid - manifest_failed)
            processing_count = sum(1 for u in self._all_units if u["status"] == "processing")
            showing = len(self._filtered_units)

            content = f"""[bold]Unit Stats[/]
\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
Manifest:      {manifest_valid}/{manifest_total} ({progress_pct}%)
Cached rows:   {showing}/{total}
[green]Valid:[/]         {valid_count}
[red]Failed:[/]        {failed_count}"""

            # Only show pending/processing if there are any
            if pending_count > 0 or processing_count > 0:
                content += f"""
[dim]Pending:[/]       {pending_count}
[yellow]Processing:[/]   {processing_count}"""

            content += f"""

[bold]Filters[/]
\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
Status (F):    {self.status_filter}
Step (←→):     {self.step_filter}
Sort (S):      {self.sort_by}
"""

        panel.update(content)

    def _update_pipeline_panel(self, force: bool = False) -> None:
        """Update the pipeline visualization.

        Skips re-render if pipeline content hasn't changed, preserving
        the user's horizontal scroll position across refresh cycles.
        """
        content = self._render_pipeline_content()
        try:
            static = self.query_one("#pipeline-content", Static)
            if not force and content == self._last_pipeline_content:
                return  # No change — preserve scroll position
            self._last_pipeline_content = content
            static.update(content)
        except NoMatches:
            # First call — mount it into the scroll container
            self._last_pipeline_content = content
            try:
                scroll = self.query_one("#pipeline-scroll", HorizontalScroll)
                scroll.mount(Static(content, id="pipeline-content"))
            except NoMatches:
                return
        self._scroll_to_selected_step()

    def _scroll_to_selected_step(self) -> None:
        """Scroll the pipeline to keep the selected step visible."""
        try:
            scroll = self.query_one("#pipeline-scroll", HorizontalScroll)
            # Each step box is box_width(18) + 2 borders + arrow_gap(5) = 25 chars
            step_width = 23
            target_x = max(0, self.selected_step_index * step_width - 10)
            scroll.scroll_to(x=target_x, animate=False)
        except Exception:
            pass

    def _render_pipeline_content(self) -> str:
        """Render the pipeline visualization as text."""
        steps = self.run_data.steps
        pipeline = self.run_data.pipeline
        chunks = self.run_data.chunks

        def get_status(step, idx):
            return get_step_status_from_chunks(step.name, idx, chunks, pipeline)

        def get_failures(step, idx):
            """Count failures for this step."""
            return self._count_step_failures(step.name)

        def get_batch_detail(step, idx):
            """Return (submitted, pending) chunk counts for this step, or None."""
            # Only show batch detail when progress shows 0 completed
            if step.completed > 0:
                return None
            submitted_count = 0
            pending_count = 0
            step_name = step.name
            for chunk in chunks:
                if chunk.state == f"{step_name}_SUBMITTED":
                    submitted_count += 1
                elif chunk.state == f"{step_name}_PENDING":
                    pending_count += 1
            if submitted_count > 0 or pending_count > 0:
                return (submitted_count, pending_count)
            return None

        # Load step configs to check for loop_until
        step_configs = None
        try:
            config_path = self.run_data.run_dir / "config" / "config.yaml"
            if config_path.exists():
                import yaml
                with open(config_path) as f:
                    config = yaml.safe_load(f)
                step_configs = config.get("pipeline", {}).get("steps", [])
        except Exception:
            pass  # Gracefully handle missing/invalid config

        # Build funnel data: per-step valid counts from on-disk validated files
        # This shows each step's own throughput (valid_output / input_count)
        # instead of global progress, making bottlenecks immediately visible.
        step_funnel = {}
        step_valid_counts = {}
        total_units = sum(c.total for c in chunks) if chunks else 0
        for i, step_name in enumerate(pipeline):
            valid = self._count_step_valid(step_name)
            step_valid_counts[step_name] = valid
            if i == 0:
                step_input = total_units
            else:
                step_input = step_valid_counts[pipeline[i - 1]]
            step_funnel[step_name] = (valid, step_input)

        return render_pipeline_boxes(
            steps=steps,
            selected_index=self.selected_step_index,
            get_step_status=get_status,
            get_status_symbol=get_step_status_symbol,
            get_failures=get_failures,
            step_configs=step_configs,
            get_batch_detail=get_batch_detail,
            step_funnel=step_funnel,
        )

    def _get_max_retries(self) -> int:
        """Get max_retries from the run's config snapshot (cached)."""
        if self._cached_max_retries is not None:
            return self._cached_max_retries
        try:
            import yaml
            config_path = self.run_data.run_dir / "config" / "config.yaml"
            if config_path.exists():
                with open(config_path) as f:
                    config = yaml.safe_load(f)
                self._cached_max_retries = config.get("max_retries", 5)
            else:
                self._cached_max_retries = 5
        except Exception:
            self._cached_max_retries = 5
        return self._cached_max_retries

    def _count_step_failures(self, step_name: str) -> dict:
        """Categorize failures for a specific step by scanning failure files.

        Returns:
            {"validation": count, "hard": count, "total": count,
             "retrying": count, "exhausted": count,
             "max_retry_attempt": int, "max_retries": int}
        """
        validation_stages = {"schema_validation", "validation"}
        run_dir = self.run_data.run_dir
        chunks_dir = run_dir / "chunks"
        max_retries = self._get_max_retries()
        is_running = getattr(self, '_last_manifest_status', None) == "running"

        if not chunks_dir.exists():
            return {"validation": 0, "hard": 0, "total": 0,
                    "retrying": 0, "exhausted": 0,
                    "max_retry_attempt": 0, "max_retries": max_retries}

        validation = 0
        hard = 0
        retrying = 0
        exhausted = 0
        max_retry_attempt = 0
        for chunk_dir in chunks_dir.glob("chunk_*"):
            if not chunk_dir.is_dir():
                continue
            failures_path = _find_jsonl_file(chunk_dir / f"{step_name}_failures.jsonl")
            if failures_path:
                try:
                    with _open_jsonl(failures_path) as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                failure = json.loads(line)
                                stage = failure.get("failure_stage", "validation")
                                if stage in validation_stages:
                                    validation += 1
                                    retry_count = failure.get("retry_count", 0)
                                    if is_running and retry_count < max_retries:
                                        retrying += 1
                                        max_retry_attempt = max(max_retry_attempt, retry_count)
                                    else:
                                        exhausted += 1
                                else:
                                    hard += 1
                            except json.JSONDecodeError:
                                hard += 1
                except Exception:
                    pass
        return {"validation": validation, "hard": hard, "total": validation + hard,
                "retrying": retrying, "exhausted": exhausted,
                "max_retry_attempt": max_retry_attempt, "max_retries": max_retries}

    def _count_step_valid(self, step_name: str) -> int:
        """Count valid units for a step by scanning validated files on disk."""
        run_dir = self.run_data.run_dir
        chunks_dir = run_dir / "chunks"
        if not chunks_dir.exists():
            return 0
        count = 0
        for chunk_dir in chunks_dir.glob("chunk_*"):
            if not chunk_dir.is_dir():
                continue
            validated_path = _find_jsonl_file(chunk_dir / f"{step_name}_validated.jsonl")
            if validated_path:
                try:
                    with _open_jsonl(validated_path) as f:
                        count += sum(1 for line in f if line.strip())
                except Exception:
                    pass
        return count

    def _get_failure_summary(self, step_name: str) -> list[tuple[str, str, int]]:
        """Get grouped failure summary for a step. Cached; invalidated on file mtime change.

        Returns list of (stage, error_pattern, count) sorted by count descending.
        stage is 'validation' or 'hard'.
        """
        validation_stages = {"schema_validation", "validation"}
        run_dir = self.run_data.run_dir
        chunks_dir = run_dir / "chunks"
        if not chunks_dir.exists():
            return []

        # Check mtimes to see if cache is still valid
        max_mtime = 0.0
        for chunk_dir in chunks_dir.glob("chunk_*"):
            if not chunk_dir.is_dir():
                continue
            failures_path = _find_jsonl_file(chunk_dir / f"{step_name}_failures.jsonl")
            if failures_path:
                try:
                    max_mtime = max(max_mtime, failures_path.stat().st_mtime)
                except OSError:
                    pass

        cached_mtime = self._failure_summary_mtimes.get(step_name, 0.0)
        if step_name in self._failure_summary_cache and max_mtime == cached_mtime:
            return self._failure_summary_cache[step_name]

        # Scan and group failures
        from collections import Counter
        groups: Counter = Counter()  # (stage, pattern) -> count

        for chunk_dir in chunks_dir.glob("chunk_*"):
            if not chunk_dir.is_dir():
                continue
            failures_path = _find_jsonl_file(chunk_dir / f"{step_name}_failures.jsonl")
            if not failures_path:
                continue
            try:
                with _open_jsonl(failures_path) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            failure = json.loads(line)
                            stage_raw = failure.get("failure_stage", "validation")
                            stage = "validation" if stage_raw in validation_stages else "hard"
                            # Extract a short error pattern
                            error_msg = failure.get("error_message", "") or failure.get("error", "") or ""
                            if not error_msg:
                                # Try to get from nested validation_errors
                                val_errors = failure.get("validation_errors", [])
                                if val_errors and isinstance(val_errors, list):
                                    error_msg = str(val_errors[0])[:80]
                            # Truncate and normalize for grouping
                            pattern = error_msg[:80].strip() if error_msg else "unknown"
                            groups[(stage, pattern)] += 1
                        except json.JSONDecodeError:
                            groups[("hard", "parse error")] += 1
            except Exception:
                pass

        # Sort by count descending, take top 5
        result = [(stage, pattern, count) for (stage, pattern), count in groups.most_common(5)]
        self._failure_summary_cache[step_name] = result
        self._failure_summary_mtimes[step_name] = max_mtime
        return result

    def _update_detail_panel(self) -> None:
        """Update the detail panel based on current view."""
        try:
            container = self.query_one("#detail-panel", VerticalScroll)
        except NoMatches:
            return

        container.remove_children()

        if self.current_view == "chunk":
            self._render_chunk_view(container)
        else:
            self._render_unit_view(container)

    def _render_chunk_view(self, container: VerticalScroll) -> None:
        """Render chunks for the selected step."""
        pipeline = self.run_data.pipeline
        step_idx = self.selected_step_index
        step_name = pipeline[step_idx] if step_idx < len(pipeline) else "unknown"

        # Filter chunks at or past this step
        chunks_at_step = []
        for chunk in self.run_data.chunks:
            _, _, chunk_step_idx = parse_chunk_state(chunk.state, pipeline)
            if chunk_step_idx >= step_idx or chunk.state == "VALIDATED":
                chunks_at_step.append(chunk)

        if not chunks_at_step:
            chunks_at_step = self.run_data.chunks

        self._chunks_for_step = chunks_at_step

        # Header showing step info
        step_data = self.run_data.steps[step_idx] if step_idx < len(self.run_data.steps) else None
        if step_data:
            step_status = get_step_status_from_chunks(step_name, step_idx, self.run_data.chunks, pipeline)
            symbol = get_step_status_symbol(step_status)
            progress_bar = make_progress_bar(step_data.completed, step_data.total, 30)
            pct = int((step_data.completed / step_data.total) * 100) if step_data.total > 0 else 0
            step_num = step_idx + 1
            total_steps = len(pipeline)
            header = f"[bold]{step_name.upper()}[/] {symbol}  Step {step_num} of {total_steps}\n[{progress_bar}] {pct}%"
        else:
            header = f"[bold]{step_name.upper()}[/]"

        container.mount(Static(header, classes="detail-header"))

        # Create DataTable for chunks
        table = DataTable()
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.add_columns("", "Chunk", "Progress", "Count", "Errors", "Status")

        for chunk in chunks_at_step:
            progress_bar = make_progress_bar(chunk.valid, chunk.total, 20)
            count = f"{chunk.valid}/{chunk.total}"
            symbol = get_chunk_status_symbol(chunk.state, pipeline)
            current_step, status, _ = parse_chunk_state(chunk.state, pipeline)
            step_display = current_step if current_step not in ("pending", "unknown") else "pending"

            # Show error count with indicator
            if chunk.failed > 0:
                errors_display = f"[red]⚠ {chunk.failed}[/]"
            else:
                errors_display = "[dim]0[/]"

            table.add_row(symbol, chunk.name, progress_bar, count, errors_display, step_display)

        container.mount(table)
        self._chunk_table = table

        if self.selected_chunk_index < len(chunks_at_step):
            table.move_cursor(row=self.selected_chunk_index)

    def _render_unit_view(self, container: VerticalScroll) -> None:
        """Render the unit view with all units."""
        # If units haven't been loaded yet, start async load and show indicator
        if not self._units_loaded:
            if not self._units_loading:
                self._start_unit_load()
            container.mount(Static("[dim]Loading units...[/]", classes="empty-state"))
            self._unit_table = None
            return

        self._filtered_units = self._get_filtered_units()

        # Header uses manifest totals (source of truth), not loaded row count.
        loaded_total = len(self._all_units)
        manifest_total = self._get_manifest_total_items()
        showing = len(self._filtered_units)
        header = f"[bold]All Units[/]  ({showing} cached rows, {manifest_total} total units)"
        container.mount(Static(header, classes="detail-header"))

        if not self._filtered_units:
            # Determine appropriate empty message
            if loaded_total == 0 and manifest_total > 0:
                # Have expected units but none loaded yet - waiting for results
                empty_msg = f"[dim]Waiting for results... ({manifest_total} units pending)[/]"
            elif loaded_total == 0:
                # No units at all
                empty_msg = "[dim]No units found[/]"
            else:
                # Have units but filter excludes all
                empty_msg = "[dim]No units found"
                if self.status_filter != "all" or self.step_filter != "all":
                    empty_msg += " - try changing filters"
                empty_msg += "[/]"
            container.mount(Static(empty_msg, classes="empty-state"))
            self._unit_table = None
            return

        # Create DataTable for units
        table = DataTable()
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.add_columns("", "Unit ID", "Chunk", "Step", "Attempts", "Status")

        for unit in self._filtered_units:
            unit_id = unit["unit_id"]
            if len(unit_id) > 35:
                unit_id = unit_id[:32] + "..."

            chunk_name = unit.get("chunk", "—")
            attempts = unit.get("attempts", 1)
            attempts_display = str(attempts) if attempts > 1 else "[dim]1[/]"

            status = unit["status"]
            if status == "valid":
                symbol = "[green]✓[/]"
                status_text = "valid"
            elif status == "failed":
                retry_count = unit.get("attempts", 1) - 1  # attempts = retry_count + 1
                max_retries = self._get_max_retries()
                is_running = getattr(self, '_last_manifest_status', None) == "running"
                failure_stage = unit.get("failure_stage", "unknown")
                is_validation = failure_stage in ("schema_validation", "validation")

                if is_validation and is_running and retry_count < max_retries:
                    symbol = "[yellow]↻[/]"
                    status_text = f"retrying ({retry_count + 1}/{max_retries})"
                elif is_validation:
                    symbol = "[dark_orange]⚠[/]"
                    status_text = "failed"
                else:
                    symbol = "[red]✗[/]"
                    status_text = "failed"
            elif status == "processing":
                symbol = "[yellow]●[/]"
                status_text = "processing"
            else:  # pending
                symbol = "[dim]○[/]"
                status_text = "pending"

            table.add_row(symbol, unit_id, chunk_name, unit["step"], attempts_display, status_text)

        container.mount(table)
        self._unit_table = table

        # Ensure selection is within bounds
        if self.selected_unit_index >= len(self._filtered_units):
            self.selected_unit_index = max(0, len(self._filtered_units) - 1)

        if self._filtered_units:
            table.move_cursor(row=self.selected_unit_index)

    # --- Actions ---

    def action_quit(self) -> None:
        """Quit the application."""
        _log.debug("MainScreen.action_quit called")
        self.app.exit()

    def action_show_log(self) -> None:
        """Open log modal (tail-loads last 200 lines, no full file read)."""
        log_path = self.run_data.run_dir / "RUN_LOG.txt"
        self.app.push_screen(LogModal(log_path=log_path))

    def action_view_artifacts(self) -> None:
        """Open artifact viewer modal."""
        self.app.push_screen(ArtifactModal(self.run_data.run_dir))

    def action_show_config(self) -> None:
        """Open configuration editor."""
        self.app.push_screen(ConfigListScreen())

    def action_process_info(self) -> None:
        """Open process info for this run."""
        from .process_info import ProcessInfoScreen
        self.app.push_screen(ProcessInfoScreen(self.run_data.run_dir))

    def action_go_back(self) -> None:
        """Go back to Home screen."""
        _log.debug("action_go_back: returning to home screen")
        if len(self.app.screen_stack) > 1:
            self.app.pop_screen()

    def action_archive_run(self) -> None:
        """Archive this run."""
        # Read manifest status for safety check
        manifest_path = self.run_data.run_dir / "MANIFEST.json"
        status = ""
        if manifest_path.exists():
            try:
                import json as _json
                with open(manifest_path) as f:
                    manifest = _json.load(f)
                status = manifest.get("status", "")
            except (ValueError, OSError):
                pass

        if not status:
            self.notify("Cannot archive: manifest missing or unreadable", severity="warning")
            return
        terminal_statuses = {"complete", "failed", "killed"}
        if status not in terminal_statuses:
            self.notify("Can only archive completed, failed, or killed runs", severity="warning")
            return

        run_name = self.run_data.run_name
        run_path = self.run_data.run_dir
        is_archived = run_path.parent.name == "_archive"
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
                action = "Unarchived" if is_archived else "Archived"
                shutil.move(str(run_path), str(dest))
                self.notify(f"{action} '{run_name}'")
                # Go back to home screen since run path is now invalid
                if len(self.app.screen_stack) > 1:
                    self.app.pop_screen()
            except Exception as e:
                self.notify(f"Archive failed: {e}", severity="error")

        self.app.push_screen(
            ArchiveConfirmModal(run_name, is_unarchive=is_archived),
            on_confirm
        )

    def action_noop(self) -> None:
        """Do nothing - used to disable keys like tab."""
        pass

    def action_toggle_view(self) -> None:
        """Toggle between chunk view and unit view."""
        _log.debug(f"action_toggle_view: current={self.current_view}")
        if self.current_view == "chunk":
            self.current_view = "unit"
            # Reset unit selection
            self.selected_unit_index = 0
            # Clear cached units to force reload
            self._clear_unit_cache()
        else:
            self.current_view = "chunk"

    def action_step_prev(self) -> None:
        """Select previous pipeline step."""
        _log.debug(f"action_step_prev: current={self.selected_step_index}")
        if self.selected_step_index > 0:
            self.selected_step_index -= 1

    def action_step_next(self) -> None:
        """Select next pipeline step."""
        max_index = len(self.run_data.steps) - 1
        _log.debug(f"action_step_next: current={self.selected_step_index}, max={max_index}")
        if self.selected_step_index < max_index:
            self.selected_step_index += 1

    def action_nav_up(self) -> None:
        """Navigate up in the current view."""
        _log.debug(f"action_nav_up: focus={self.active_focus}, view={self.current_view}")

        if self.current_view == "unit":
            # Unit view - move up in unit list
            if self.selected_unit_index > 0:
                self.selected_unit_index -= 1
                self._sync_unit_cursor()
        elif self.active_focus == "detail":
            # Chunk view - either move up in chunks or switch to pipeline
            if self.selected_chunk_index > 0:
                self.selected_chunk_index -= 1
                self._sync_chunk_cursor()
                self._update_chunk_stats_panel()
            else:
                self.active_focus = "pipeline"
        # If in pipeline, do nothing

    def action_nav_down(self) -> None:
        """Navigate down in the current view."""
        _log.debug(f"action_nav_down: focus={self.active_focus}, view={self.current_view}")

        if self.current_view == "unit":
            # Unit view - move down in unit list
            if self.selected_unit_index < len(self._filtered_units) - 1:
                self.selected_unit_index += 1
                self._sync_unit_cursor()
        elif self.active_focus == "pipeline":
            # Switch from pipeline to detail panel
            self.active_focus = "detail"
        else:
            # Chunk view - move down in chunks list
            max_idx = len(self._chunks_for_step) - 1
            if self.selected_chunk_index < max_idx:
                self.selected_chunk_index += 1
                self._sync_chunk_cursor()
                self._update_chunk_stats_panel()

    def _sync_chunk_cursor(self) -> None:
        """Sync chunk table cursor to match selection state."""
        if self._chunk_table is not None:
            try:
                self._chunk_table.move_cursor(row=self.selected_chunk_index)
            except Exception:
                pass

    def _sync_unit_cursor(self) -> None:
        """Sync unit table cursor to match selection state."""
        if self._unit_table is not None:
            try:
                self._unit_table.move_cursor(row=self.selected_unit_index)
            except Exception:
                pass

    def action_select_item(self) -> None:
        """Handle Enter key based on current view."""
        _log.debug(f"action_select_item: view={self.current_view}")

        if self.current_view == "unit":
            self._show_unit_detail()
        else:
            # Chunk view - show chunk details in stats panel (already visible)
            # Could expand to show more detail in future
            pass

    def _show_unit_detail(self) -> None:
        """Show detail modal for selected unit."""
        if not self._filtered_units:
            return

        if self.selected_unit_index >= len(self._filtered_units):
            return

        unit = self._filtered_units[self.selected_unit_index]
        _log.debug(f"Showing unit detail for: {unit.get('unit_id')}")
        self.app.push_screen(UnitDetailModal(unit))

    # --- Filter/Sort Actions (Unit View Only) ---

    def action_cycle_status_filter(self) -> None:
        """Cycle status filter (only in unit view)."""
        if self.current_view != "unit":
            return

        filters = ["all", "valid", "failed", "pending", "processing"]
        try:
            idx = filters.index(self.status_filter)
        except ValueError:
            idx = 0
        self.status_filter = filters[(idx + 1) % len(filters)]
        self.selected_unit_index = 0
        self._update_detail_panel()
        self._update_chunk_stats_panel()
        self._update_footer()

    def action_retry_failures(self) -> None:
        """Retry validation failures for current step (only in unit view)."""
        if self.current_view != "unit":
            self.notify("Switch to unit view (V) to retry failures", severity="warning")
            return

        # Count failed units in current filter
        validation_stages = {"schema_validation", "validation"}
        failed_units = [u for u in self._filtered_units if u["status"] == "failed"]
        if not failed_units:
            self.notify("No failed units to retry", severity="information")
            return

        # Split into retryable (validation) vs non-retryable (hard)
        retryable = [u for u in failed_units
                     if u.get("failure_stage", "validation") in validation_stages]
        hard = [u for u in failed_units
                if u.get("failure_stage", "validation") not in validation_stages]

        if not retryable:
            if hard:
                self.notify(f"{len(hard)} hard failures cannot be retried", severity="warning")
            else:
                self.notify("No failed units to retry", severity="information")
            return

        if hard:
            self.notify(
                f"Retrying {len(retryable)} validation failures ({len(hard)} hard failures skipped)",
                severity="information",
            )

        # Retry only validation failures
        self._do_retry(retryable)

    def _do_retry(self, failed_units: list[dict]) -> None:
        """Reset failed units and spawn orchestrator to process retries."""
        from datetime import datetime
        from ..utils.runs import reset_unit_retries, resume_orchestrator, get_process_health

        run_dir = self.run_data.run_dir

        # Check if orchestrator is already running - don't modify anything!
        health = get_process_health(run_dir)
        if health["status"] == "running":
            self.notify("Run is already running - wait for it to finish or pause first", severity="warning")
            return

        step_name = self.step_filter if self.step_filter != "all" else None

        try:
            # 1. Reset the failure state
            reset_count = reset_unit_retries(
                run_dir,
                step_name=step_name,
                unit_ids=[u["unit_id"] for u in failed_units]
            )

            if reset_count == 0:
                self.notify("No units were reset", severity="warning")
                return

            # 2. Update manifest status to allow reprocessing
            manifest_path = run_dir / "MANIFEST.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["status"] = "running"  # Change from "complete" or "failed"
            manifest["retry_requested_at"] = datetime.now().isoformat()
            manifest.pop("error_message", None)  # Clear any error message

            # Atomic write
            tmp_path = manifest_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(manifest, indent=2))
            tmp_path.rename(manifest_path)

            # 3. Spawn orchestrator to process retries
            mode = getattr(self.run_data, 'mode', 'batch') or 'batch'
            orchestrator_mode = "realtime" if mode == "realtime" else "watch"
            success = resume_orchestrator(run_dir, mode=orchestrator_mode)

            if success:
                self.notify(f"Retrying {reset_count} units - orchestrator started", severity="information")
            else:
                self.notify(f"Reset {reset_count} units but failed to start orchestrator", severity="error")

            # 4. Refresh the view and restart the refresh loop
            self._clear_unit_cache()
            self._last_manifest_status = "running"
            self._update_detail_panel()
            self._update_chunk_stats_panel()
            self._update_pipeline_panel()
            self._update_run_stats_panel()
            self._update_log_ticker()

            # Restart the auto-refresh loop (it stops when a run becomes terminal)
            if not self._refresh_active:
                self._refresh_active = True
                self.set_timer(2.0, self._do_refresh)
                self.set_timer(5.0, self._do_unit_refresh)

        except Exception as e:
            self.notify(f"Retry failed: {e}", severity="error")

    def action_cycle_sort(self) -> None:
        """Cycle sort order (only in unit view)."""
        if self.current_view != "unit":
            return

        sorts = ["unit_id", "status", "step"]
        idx = sorts.index(self.sort_by)
        self.sort_by = sorts[(idx + 1) % len(sorts)]
        self.selected_unit_index = 0
        self._update_detail_panel()
        self._update_chunk_stats_panel()
        self._update_footer()

    def action_generate_diagnostic(self) -> None:
        """Open the Diagnostics Screen for interactive failure analysis."""
        from .diagnostics_screen import DiagnosticsScreen
        self.app.push_screen(DiagnosticsScreen(self.run_data.run_dir))
