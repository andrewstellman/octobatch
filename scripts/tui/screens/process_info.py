"""
ProcessInfoScreen for Octobatch TUI.

Shows detailed process diagnostics for a selected run.
"""

import json
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Static
from textual.binding import Binding
from textual.css.query import NoMatches

from ..utils.runs import get_process_diagnostics, kill_run_process, has_recent_errors
from .common import _log


class ProcessInfoScreen(Screen):
    """Screen showing detailed process diagnostics for a run."""

    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("q", "quit_app", "Quit"),
        Binding("Q", "quit_app", "Quit", show=False),
        Binding("k", "kill_process", "Kill"),
        Binding("K", "kill_process", "Kill", show=False),
        Binding("c", "copy_log", "Copy Log"),
        Binding("C", "copy_log", "Copy Log", show=False),
        Binding("e", "copy_errors", "Copy Errors"),
        Binding("E", "copy_errors", "Copy Errors", show=False),
        Binding("r", "refresh", "Refresh"),
    ]

    CSS = """
    ProcessInfoScreen {
        layout: grid;
        grid-size: 1;
        grid-rows: auto 1fr auto;
    }

    #header {
        height: 1;
        padding: 0 1;
        background: $primary;
    }

    #content {
        padding: 1;
    }

    .section-box {
        margin-bottom: 1;
        border: solid $surface-darken-2;
        padding: 1;
        height: auto;
    }

    #log-section {
        height: auto;
        max-height: 20;
    }

    #footer {
        height: 1;
        background: $surface-darken-1;
        padding: 0 1;
    }
    """

    def __init__(self, run_dir: Path, **kwargs):
        super().__init__(**kwargs)
        self._run_dir = Path(run_dir).resolve()
        self._run_name = self._run_dir.name
        self._diagnostics = {}
        self._manifest = {}
        self._refresh_timer = None

    def _load_manifest(self) -> dict:
        """Load the run manifest."""
        manifest_path = self._run_dir / "MANIFEST.json"
        if manifest_path.exists():
            try:
                return json.loads(manifest_path.read_text())
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def compose(self) -> ComposeResult:
        """Compose the screen with static widgets that will be updated."""
        yield Static(f"[bold]Process Info: {self._run_name}[/]", id="header")
        with VerticalScroll(id="content"):
            yield Static("Loading...", id="status-section", classes="section-box")
            yield Static("", id="cmdline-section", classes="section-box")
            yield Static("", id="pidfile-section", classes="section-box")
            yield Static("", id="log-section", classes="section-box")
        yield Static("K:kill  C:copy log  E:copy errors  r:refresh  Esc:back  Q:quit", id="footer")

    def on_mount(self) -> None:
        """Load diagnostics on mount and start auto-refresh."""
        self._load_and_update()

    def on_screen_suspend(self) -> None:
        """Cancel the refresh timer when screen is suspended."""
        if self._refresh_timer:
            self._refresh_timer.stop()
            self._refresh_timer = None

    def on_screen_resume(self) -> None:
        """Restart the refresh timer when screen is resumed."""
        self._load_and_update()

    def on_unmount(self) -> None:
        """Stop the refresh timer when screen is unmounted."""
        if self._refresh_timer:
            self._refresh_timer.stop()
            self._refresh_timer = None

    def _load_and_update(self) -> None:
        """Load diagnostics and update the display."""
        # Cancel any existing timer to prevent overlapping timer chains
        if self._refresh_timer:
            self._refresh_timer.stop()
            self._refresh_timer = None
        self._diagnostics = get_process_diagnostics(self._run_dir)
        self._manifest = self._load_manifest()
        self._update_display()
        # Schedule next refresh using recursive set_timer (not set_interval)
        # set_interval doesn't fire reliably on pushed screens in Textual
        self._refresh_timer = self.set_timer(5.0, self._load_and_update)

    def _update_display(self) -> None:
        """Update all display sections with current diagnostics."""
        d = self._diagnostics

        # Update Process Status Section
        self._update_status_section(d)

        # Update Command Line Section
        self._update_cmdline_section(d)

        # Update PID File Section
        self._update_pidfile_section(d)

        # Update Log Section
        self._update_log_section(d)

    def _update_status_section(self, d: dict) -> None:
        """Update the process status section."""
        alive = d.get("alive", False)
        pid = d.get("pid")
        source = d.get("source")
        manifest_status = self._manifest.get("status", "unknown")
        has_errors = has_recent_errors(self._run_dir)

        lines = ["[bold]Process Status[/]", ""]

        if alive:
            # Check for recent errors to determine if stuck
            if has_errors:
                lines.append(f"  Status:    [yellow]⚠ STUCK[/]")
                lines.append(f"  [yellow]Process running but encountering errors[/]")
            else:
                lines.append(f"  Status:    [green]● RUNNING[/]")
        else:
            # Show status based on manifest
            if manifest_status == "complete":
                lines.append(f"  Status:    [green]✓ COMPLETE[/]")
                lines.append(f"  [dim]Process not running (run completed successfully)[/]")
            elif manifest_status == "failed":
                lines.append(f"  Status:    [red]✗ FAILED[/]")
                error_msg = self._manifest.get("error_message", "Unknown error")
                lines.append(f"  Error:     [red]{error_msg}[/]")
            elif manifest_status == "paused":
                lines.append(f"  Status:    [yellow]⏸ PAUSED[/]")
                lines.append(f"  [dim]Process not running (run paused)[/]")
            elif manifest_status == "running":
                # Manifest says running but process is dead - zombie
                lines.append(f"  Status:    [red]○ ZOMBIE[/]")
                lines.append(f"  [dim]Manifest shows 'running' but no process found[/]")
            else:
                lines.append(f"  Status:    [red]○ NOT RUNNING[/]")

        lines.append("")
        lines.append(f"  PID:       {pid if pid else '[dim]None[/]'}")
        lines.append(f"  Source:    {source if source else '[dim]None[/]'}")

        if alive:
            cpu = d.get("cpu_percent")
            mem = d.get("memory_mb")
            duration = d.get("run_duration")
            create_time = d.get("create_time")

            if cpu is not None:
                lines.append(f"  CPU:       {cpu:.1f}%")
            if mem is not None:
                lines.append(f"  Memory:    {mem:.1f} MB")
            if duration:
                lines.append(f"  Running:   {duration}")
            if create_time:
                lines.append(f"  Started:   {create_time.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            # Show manifest timestamps for completed runs
            if manifest_status == "complete" and self._manifest.get("completed_at"):
                lines.append(f"  Completed: {self._manifest['completed_at']}")
            elif manifest_status == "failed" and self._manifest.get("failed_at"):
                lines.append(f"  Failed at: {self._manifest['failed_at']}")

        try:
            section = self.query_one("#status-section", Static)
            section.update("\n".join(lines))
        except NoMatches:
            pass

    def _update_cmdline_section(self, d: dict) -> None:
        """Update the command line section."""
        cmdline = d.get("cmdline")
        manifest_status = self._manifest.get("status", "unknown")

        if cmdline:
            # Wrap long command lines
            wrapped = self._wrap_text(cmdline, 76)
            content = f"[bold]Command Line[/]\n\n  [dim]{wrapped}[/]"
        else:
            # Show different message based on run status
            if manifest_status == "complete":
                content = "[bold]Command Line[/]\n\n  [dim]Run completed - process no longer active[/]"
            elif manifest_status == "failed":
                content = "[bold]Command Line[/]\n\n  [dim]Run failed - process no longer active[/]"
            elif manifest_status == "paused":
                content = "[bold]Command Line[/]\n\n  [dim]Run paused - process not running[/]"
            else:
                content = "[bold]Command Line[/]\n\n  [dim]No process running[/]"

        try:
            section = self.query_one("#cmdline-section", Static)
            section.update(content)
        except NoMatches:
            pass

    def _update_pidfile_section(self, d: dict) -> None:
        """Update the PID file section."""
        manifest_status = self._manifest.get("status", "unknown")

        # For completed/failed runs, PID file is not applicable
        if manifest_status in ("complete", "failed"):
            lines = ["[bold]PID File[/]", ""]
            lines.append("  Status:    [dim]N/A (run finished)[/]")
            try:
                section = self.query_one("#pidfile-section", Static)
                section.update("\n".join(lines))
            except NoMatches:
                pass
            return

        pid_exists = d.get("pid_file_exists", False)
        pid_content = d.get("pid_file_content", "")
        pid_file_path = self._run_dir / "orchestrator.pid"

        lines = ["[bold]PID File[/]", ""]
        lines.append(f"  Path:      {pid_file_path}")

        if pid_exists:
            lines.append(f"  Status:    [green]Exists[/]")
            lines.append(f"  Content:   {pid_content}")
        else:
            lines.append(f"  Status:    [yellow]Missing[/]")

        try:
            section = self.query_one("#pidfile-section", Static)
            section.update("\n".join(lines))
        except NoMatches:
            pass

    def _update_log_section(self, d: dict) -> None:
        """Update the recent log section."""
        log_lines = d.get("recent_log_lines", [])
        has_errors = d.get("has_errors", False)

        title = "[bold]Recent Log[/]"
        if has_errors:
            title += " [red](has errors)[/]"

        lines = [title, ""]

        if log_lines:
            for line in log_lines:
                escaped = self._escape_markup(line)
                if "[ERROR]" in line:
                    lines.append(f"  [red]{escaped}[/]")
                elif "[WARN]" in line or "[WARNING]" in line:
                    lines.append(f"  [yellow]{escaped}[/]")
                else:
                    lines.append(f"  {escaped}")
        else:
            lines.append("  [dim]No log entries[/]")

        try:
            section = self.query_one("#log-section", Static)
            section.update("\n".join(lines))
        except NoMatches:
            pass

    def _wrap_text(self, text: str, width: int) -> str:
        """Wrap text to specified width."""
        if len(text) <= width:
            return text
        lines = []
        current = text
        while current:
            if len(current) <= width:
                lines.append(current)
                break
            # Find last space within width
            break_at = current.rfind(' ', 0, width)
            if break_at == -1:
                break_at = width
            lines.append(current[:break_at])
            current = current[break_at:].lstrip()
        return '\n           '.join(lines)  # Indent continuation lines

    def _escape_markup(self, text: str) -> str:
        """Escape Rich markup characters in text."""
        return text.replace("[", r"\[").replace("]", r"\]")

    def action_go_back(self) -> None:
        """Go back to home screen."""
        self.app.pop_screen()

    def action_quit_app(self) -> None:
        """Quit the application."""
        self.app.exit()

    def action_kill_process(self) -> None:
        """Kill the process for this run."""
        if not self._diagnostics.get("alive"):
            self.notify("No running process to kill", severity="warning")
            return

        pid = self._diagnostics.get("pid")
        if kill_run_process(self._run_dir):
            self.notify(f"Sent SIGTERM to PID {pid}")
            # Refresh after a short delay to show updated status
            self.set_timer(1.0, self._load_and_update)
        else:
            self.notify("Failed to kill process", severity="error")

    def action_refresh(self) -> None:
        """Manual refresh of diagnostics."""
        self._load_and_update()
        self.notify("Refreshed")

    def action_copy_log(self) -> None:
        """Copy the recent log content to clipboard."""
        try:
            import pyperclip
        except ImportError:
            self.notify("pyperclip not installed", severity="warning")
            return

        log_file = self._run_dir / "RUN_LOG.txt"
        if not log_file.exists():
            self.notify("No log file found", severity="warning")
            return

        try:
            content = log_file.read_text()
            lines = content.strip().split("\n")
            # Copy last 100 lines (or full log if shorter)
            recent = "\n".join(lines[-100:])

            pyperclip.copy(recent)
            self.notify(f"Copied {min(len(lines), 100)} log lines to clipboard")
        except Exception as e:
            self.notify(f"Failed to copy: {e}", severity="error")

    def action_copy_errors(self) -> None:
        """Copy only ERROR lines from log to clipboard (deduplicated)."""
        try:
            import pyperclip
        except ImportError:
            self.notify("pyperclip not installed", severity="warning")
            return

        log_file = self._run_dir / "RUN_LOG.txt"
        if not log_file.exists():
            self.notify("No log file found", severity="warning")
            return

        try:
            content = log_file.read_text()
            error_lines = [line for line in content.split("\n") if "[ERROR]" in line]

            if not error_lines:
                self.notify("No errors found in log")
                return

            # Deduplicate consecutive identical errors
            unique_errors = []
            last_error = None
            for line in error_lines:
                if line != last_error:
                    unique_errors.append(line)
                    last_error = line

            pyperclip.copy("\n".join(unique_errors))
            self.notify(f"Copied {len(unique_errors)} unique error lines to clipboard")
        except Exception as e:
            self.notify(f"Failed to copy: {e}", severity="error")
