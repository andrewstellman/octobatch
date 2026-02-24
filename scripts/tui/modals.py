"""
Modal screens for Octobatch TUI.
"""

import json
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Static, Button, Input, Label
from textual.binding import Binding
from textual import work


class DetailModal(ModalScreen):
    """Modal for viewing detailed content (units, failures, logs)."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("s", "save", "Save"),
        Binding("c", "copy", "Copy", priority=True),
        Binding("C", "copy", "Copy", show=False, priority=True),
    ]

    CSS = """
    DetailModal {
        align: center middle;
    }

    #modal-container {
        width: 80%;
        height: 80%;
        border: solid $primary;
        background: $surface;
        padding: 1;
    }

    #modal-title {
        dock: top;
        height: 1;
        background: $primary;
        color: $text;
        text-align: center;
        text-style: bold;
    }

    #modal-content {
        height: 1fr;
        overflow-y: auto;
        padding: 1;
    }

    #modal-footer {
        dock: bottom;
        height: 1;
        text-align: center;
    }

    #save-container {
        display: none;
        dock: bottom;
        height: 3;
        padding: 0 1;
    }

    #save-container.visible {
        display: block;
    }

    #save-input {
        width: 100%;
    }
    """

    def __init__(
        self,
        title: str,
        content: str,
        content_type: str = "text",
        **kwargs
    ):
        super().__init__(**kwargs)
        self.title_text = title
        self.content_text = content
        self.content_type = content_type
        self.save_mode = False

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container"):
            yield Static(self.title_text, id="modal-title")
            yield Static(self.content_text, id="modal-content")
            with Horizontal(id="save-container"):
                yield Label("Filename: ")
                yield Input(placeholder="output.txt", id="save-input")
            yield Static("s:save c:copy Esc:close", id="modal-footer")

    def action_close(self) -> None:
        """Close the modal."""
        if self.save_mode:
            self.save_mode = False
            self.query_one("#save-container").remove_class("visible")
        else:
            self.dismiss(None)

    def action_save(self) -> None:
        """Toggle save mode to enter filename."""
        self.save_mode = True
        save_container = self.query_one("#save-container")
        save_container.add_class("visible")
        self.query_one("#save-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle filename submission."""
        filename = event.value.strip()
        if filename:
            try:
                path = Path(filename)
                with open(path, "w") as f:
                    f.write(self.content_text)
                self.notify(f"Saved to {path}")
            except Exception as e:
                self.notify(f"Error saving: {e}", severity="error")
        self.save_mode = False
        self.query_one("#save-container").remove_class("visible")

    def action_copy(self) -> None:
        """Copy content to clipboard."""
        try:
            import pyperclip
            pyperclip.copy(self.content_text)
            self.notify("Copied to clipboard")
        except ImportError:
            self.notify("pyperclip not installed", severity="warning")
        except Exception as e:
            self.notify(f"Copy failed: {e}", severity="error")


class UnitDetailModal(DetailModal):
    """Modal for viewing unit details."""

    def __init__(self, unit_data: dict, **kwargs):
        content = json.dumps(unit_data, indent=2)
        unit_id = unit_data.get("unit_id", "Unknown")
        super().__init__(
            title=f"Unit: {unit_id}",
            content=content,
            content_type="json",
            **kwargs
        )


class FailureDetailModal(DetailModal):
    """Modal for viewing failure details."""

    def __init__(self, failure_data: dict, **kwargs):
        # Format failure information
        lines = [
            f"Unit ID: {failure_data.get('unit_id', 'Unknown')}",
            f"Chunk: {failure_data.get('chunk_name', 'Unknown')}",
            f"Step: {failure_data.get('step', 'Unknown')}",
            f"Retry Count: {failure_data.get('retry_count', 0)}",
            "",
            "Errors:",
        ]

        errors = failure_data.get("errors", [])
        for i, err in enumerate(errors, 1):
            lines.append(f"  {i}. {err}")

        # Add input/output if available
        if "input" in failure_data:
            lines.append("")
            lines.append("Input:")
            lines.append(json.dumps(failure_data["input"], indent=2))

        if "output" in failure_data:
            lines.append("")
            lines.append("Output:")
            lines.append(json.dumps(failure_data["output"], indent=2))

        content = "\n".join(lines)
        super().__init__(
            title=f"Failure: {failure_data.get('unit_id', 'Unknown')}",
            content=content,
            content_type="text",
            **kwargs
        )


def _tail_read_lines(file_path: Path, num_lines: int = 200) -> tuple[list[str], int]:
    """Efficiently read the last N lines of a file using seek from end.

    Returns (lines, file_size_bytes).
    """
    if not file_path.exists():
        return [], 0

    file_size = file_path.stat().st_size
    if file_size == 0:
        return [], 0

    with open(file_path, "rb") as f:
        # Start with 8KB per expected line — generous for log files
        chunk_size = min(file_size, num_lines * 8192)
        lines: list[str] = []

        while True:
            f.seek(max(0, file_size - chunk_size))
            raw = f.read()
            lines = raw.decode("utf-8", errors="replace").splitlines()

            if len(lines) >= num_lines or chunk_size >= file_size:
                break
            # Double the chunk and retry
            chunk_size = min(file_size, chunk_size * 2)

    return lines[-num_lines:], file_size


def _format_file_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


class LogModal(ModalScreen):
    """Modal for viewing recent log entries from RUN_LOG.txt.

    Loads only the last 200 lines via seek from end instead of reading the
    entire file. Shows file size and approximate line count. Supports
    scrolling through the loaded tail.
    """

    TAIL_LINES = 200

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
        Binding("u", "load_older", "Older"),
        Binding("s", "save", "Save"),
        Binding("c", "copy", "Copy", priority=True),
        Binding("C", "copy", "Copy", show=False, priority=True),
        Binding("p", "copy_path", "Copy Path", priority=True),
    ]

    CSS = """
    LogModal {
        align: center middle;
    }

    #log-modal-container {
        width: 80%;
        height: 80%;
        border: solid $primary;
        background: $surface;
        padding: 1;
    }

    #log-modal-title {
        dock: top;
        height: 1;
        background: $primary;
        color: $text;
        text-align: center;
        text-style: bold;
    }

    #log-modal-scroll {
        height: 1fr;
        padding: 1;
    }

    #log-modal-content {
        width: 100%;
    }

    #log-modal-footer {
        dock: bottom;
        height: 1;
        text-align: center;
    }

    #log-save-container {
        display: none;
        dock: bottom;
        height: 3;
        padding: 0 1;
    }

    #log-save-container.visible {
        display: block;
    }

    #log-save-input {
        width: 100%;
    }
    """

    def __init__(self, log_path: Path | None = None, **kwargs):
        super().__init__(**kwargs)
        self._log_path = log_path
        self._save_mode = False
        # Tail-load asynchronously on mount to keep modal opening responsive.
        self._tail_lines_loaded = self.TAIL_LINES
        self._line_estimate: int | None = None
        self._lines: list[str] = []
        self._file_size: int = 0
        self._log_content = "[dim]Loading recent log lines...[/]"

    def compose(self) -> ComposeResult:
        from textual.containers import VerticalScroll

        with Vertical(id="log-modal-container"):
            yield Static("Run Log", id="log-modal-title")
            with VerticalScroll(id="log-modal-scroll"):
                yield Static(self._log_content, id="log-modal-content", markup=False)
            with Horizontal(id="log-save-container"):
                yield Label("Filename: ")
                yield Input(placeholder="output.txt", id="log-save-input")
            yield Static("q:close  u:older  s:save  c:copy  p:path  Esc:close", id="log-modal-footer")

    def on_mount(self) -> None:
        """Kick off async tail/telemetry loads and jump to latest content."""
        self._load_tail_async(self._tail_lines_loaded)
        self._load_telemetry_async()

    def _update_title(self) -> None:
        """Update title with file size and rough line-count telemetry."""
        try:
            title = self.query_one("#log-modal-title", Static)
        except Exception:
            return

        parts: list[str] = []
        if self._file_size > 0:
            parts.append(_format_file_size(self._file_size))
        if self._line_estimate is not None:
            parts.append(f"~{self._line_estimate:,} lines")
        if self._lines:
            parts.append(f"showing last {len(self._lines)} lines")

        if parts:
            title.update(f"Run Log  ({', '.join(parts)})")
        else:
            title.update("Run Log")

    @work(thread=True, exclusive=True, group="log-tail")
    def _load_tail_async(self, line_count: int) -> None:
        """Load last N lines in a worker thread."""
        lines: list[str] = []
        size = 0
        if self._log_path:
            lines, size = _tail_read_lines(self._log_path, line_count)
        content = "\n".join(lines) if lines else "(empty log)"
        self.app.call_from_thread(self._apply_tail_content, lines, size, content)

    @work(thread=True, exclusive=True, group="log-telemetry")
    def _load_telemetry_async(self) -> None:
        """Estimate line count from a file sample in the background."""
        if not self._log_path or not self._log_path.exists():
            return
        try:
            file_size = self._log_path.stat().st_size
            if file_size == 0:
                self.app.call_from_thread(self._apply_telemetry, file_size, 0)
                return
            with open(self._log_path, "rb") as f:
                sample_size = min(file_size, 256 * 1024)
                f.seek(max(0, file_size - sample_size))
                sample = f.read(sample_size)
            line_breaks = sample.count(b"\n")
            if line_breaks <= 0:
                estimate = 1
            else:
                bytes_per_line = sample_size / line_breaks
                estimate = max(1, int(file_size / max(1.0, bytes_per_line)))
            self.app.call_from_thread(self._apply_telemetry, file_size, estimate)
        except Exception:
            pass

    def _apply_tail_content(self, lines: list[str], file_size: int, content: str) -> None:
        self._lines = lines
        self._file_size = file_size
        self._log_content = content
        try:
            body = self.query_one("#log-modal-content", Static)
            body.update(self._log_content)
        except Exception:
            pass
        self._update_title()
        try:
            scroll = self.query_one("#log-modal-scroll")
            scroll.scroll_end(animate=False)
        except Exception:
            pass

    def _apply_telemetry(self, file_size: int, line_estimate: int | None) -> None:
        self._file_size = max(self._file_size, file_size)
        self._line_estimate = line_estimate
        self._update_title()

    def action_close(self) -> None:
        if self._save_mode:
            self._save_mode = False
            self.query_one("#log-save-container").remove_class("visible")
        else:
            self.dismiss(None)

    def action_load_older(self) -> None:
        """Expand tail window to include older lines."""
        if not self._log_path:
            return
        self._tail_lines_loaded = min(self._tail_lines_loaded + self.TAIL_LINES, 5000)
        self._load_tail_async(self._tail_lines_loaded)

    def action_save(self) -> None:
        self._save_mode = True
        save_container = self.query_one("#log-save-container")
        save_container.add_class("visible")
        self.query_one("#log-save-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        filename = event.value.strip()
        if filename:
            try:
                path = Path(filename)
                with open(path, "w") as f:
                    f.write(self._log_content)
                self.notify(f"Saved to {path}")
            except Exception as e:
                self.notify(f"Error saving: {e}", severity="error")
        self._save_mode = False
        self.query_one("#log-save-container").remove_class("visible")

    def action_copy(self) -> None:
        try:
            import pyperclip
            pyperclip.copy(self._log_content)
            self.notify("Log copied to clipboard")
        except ImportError:
            self.notify("pyperclip not installed", severity="warning")
        except Exception as e:
            self.notify(f"Copy failed: {e}", severity="error")

    def action_copy_path(self) -> None:
        if not self._log_path:
            self.notify("Log path not available", severity="warning")
            return
        try:
            import pyperclip
            pyperclip.copy(str(self._log_path))
            self.notify("Path copied")
        except ImportError:
            self.notify("pyperclip not installed", severity="warning")
        except Exception as e:
            self.notify(f"Copy failed: {e}", severity="error")

    def key_s(self, event) -> None:
        self.action_save()
        event.prevent_default()
        event.stop()

    def key_c(self, event) -> None:
        self.action_copy()
        event.prevent_default()
        event.stop()

    def key_C(self, event) -> None:
        self.action_copy()
        event.prevent_default()
        event.stop()

    def key_p(self, event) -> None:
        self.action_copy_path()
        event.prevent_default()
        event.stop()

    def key_q(self, event) -> None:
        self.action_close()
        event.prevent_default()
        event.stop()


class ArtifactModal(ModalScreen):
    """Modal for viewing generated artifacts (txt, csv, json files)."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("c", "copy", "Copy", priority=True),
        Binding("C", "copy", "Copy", show=False, priority=True),
    ]

    CSS = """
    ArtifactModal {
        align: center middle;
    }

    #artifact-container {
        width: 90%;
        height: 90%;
        border: solid $primary;
        background: $surface;
    }

    #artifact-sidebar {
        width: 25;
        height: 100%;
        border-right: solid $primary;
        padding: 1;
    }

    #artifact-sidebar-title {
        height: 1;
        text-style: bold;
        margin-bottom: 1;
    }

    #artifact-list {
        height: 1fr;
    }

    #artifact-content-area {
        width: 1fr;
        height: 100%;
        padding: 1;
    }

    #artifact-title {
        height: 1;
        text-style: bold;
        margin-bottom: 1;
    }

    #artifact-content {
        height: 1fr;
        overflow-y: auto;
    }

    #artifact-footer {
        dock: bottom;
        height: 1;
        text-align: center;
    }
    """

    def __init__(self, run_dir: Path, **kwargs):
        super().__init__(**kwargs)
        self.run_dir = Path(run_dir)
        self.artifacts = []
        self.selected_content = ""
        self.selected_name = ""
        self._load_artifacts()

    def _load_artifacts(self) -> None:
        """Find all artifact files in run directory and outputs/ subdirectory."""
        patterns = ["*.txt", "*.csv", "*.json", "*.jsonl"]
        EXCLUDE_FILES = {"MANIFEST.json", "RUN_LOG.txt", "orchestrator.log", "tui_debug.log"}
        self.artifacts = []

        # Scan run root and outputs/ subdirectory
        dirs_to_scan = [self.run_dir]
        outputs_dir = self.run_dir / "outputs"
        if outputs_dir.exists() and outputs_dir.is_dir():
            dirs_to_scan.append(outputs_dir)

        seen = set()
        for scan_dir in dirs_to_scan:
            for pattern in patterns:
                for path in scan_dir.glob(pattern):
                    if path.is_file() and path.name not in EXCLUDE_FILES and path not in seen:
                        self.artifacts.append(path)
                        seen.add(path)

        # Sort by relative path for consistent ordering
        self.artifacts.sort(key=lambda p: str(p.relative_to(self.run_dir)))

    def compose(self) -> ComposeResult:
        from textual.widgets import ListView, ListItem, Label
        from textual.containers import Horizontal, VerticalScroll

        with Horizontal(id="artifact-container"):
            with Vertical(id="artifact-sidebar"):
                yield Static("Artifacts", id="artifact-sidebar-title")
                with ListView(id="artifact-list"):
                    if self.artifacts:
                        for i, artifact in enumerate(self.artifacts):
                            try:
                                rel = str(artifact.relative_to(self.run_dir))
                            except ValueError:
                                rel = artifact.name
                            yield ListItem(Label(rel), id=f"artifact-{i}")
                    else:
                        yield ListItem(Label("[dim]No artifacts[/]"))
            with Vertical(id="artifact-content-area"):
                yield Static("Select a file", id="artifact-title")
                with VerticalScroll(id="artifact-content"):
                    yield Static("", id="artifact-text")
                yield Static("c:copy Esc:close", id="artifact-footer")

    def on_list_view_selected(self, event) -> None:
        """Handle artifact selection."""
        from textual.widgets import ListView
        list_view = self.query_one("#artifact-list", ListView)
        index = list_view.index

        if index is not None and index < len(self.artifacts):
            artifact_path = self.artifacts[index]
            try:
                self.selected_name = str(artifact_path.relative_to(self.run_dir))
            except ValueError:
                self.selected_name = artifact_path.name

            try:
                with open(artifact_path, 'r') as f:
                    self.selected_content = f.read()

                # Update display
                title = self.query_one("#artifact-title", Static)
                title.update(f"[bold]{self.selected_name}[/]")

                content = self.query_one("#artifact-text", Static)
                # Limit content display to avoid performance issues
                display_content = self.selected_content
                if len(display_content) > 50000:
                    display_content = display_content[:50000] + "\n\n[dim]... (truncated)[/]"
                content.update(display_content)

            except Exception as e:
                self.query_one("#artifact-text", Static).update(f"[red]Error reading file: {e}[/]")

    def action_close(self) -> None:
        """Close the modal."""
        self.dismiss(None)

    def action_copy(self) -> None:
        """Copy selected artifact content to clipboard."""
        if not self.selected_content:
            self.notify("No file selected", severity="warning")
            return

        try:
            import pyperclip
            pyperclip.copy(self.selected_content)
            self.notify(f"Copied {self.selected_name} to clipboard")
        except ImportError:
            self.notify("pyperclip not installed", severity="warning")
        except Exception as e:
            self.notify(f"Copy failed: {e}", severity="error")

    def key_c(self, event) -> None:
        """Handle 'c' key directly to ensure copy works."""
        self.action_copy()
        event.prevent_default()
        event.stop()

    def key_C(self, event) -> None:
        """Handle 'C' key directly."""
        self.action_copy()
        event.prevent_default()
        event.stop()
