"""
DiagnosticsScreen for Octobatch TUI.

Interactive run health diagnostics with per-step failure analysis,
disk verification, and re-validation support.
"""

import json
import subprocess
import sys
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Static
from textual.binding import Binding
from textual.css.query import NoMatches
from textual import work

from ..utils.diagnostics import (
    scan_step_health,
    verify_disk_vs_manifest,
    get_step_failure_analysis,
    generate_diagnostic,
)
from .common import _log


class DiagnosticsScreen(Screen):
    """Interactive diagnostics screen for run health analysis."""

    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("q", "quit_app", "Quit"),
        Binding("Q", "quit_app", "Quit", show=False),
        Binding("left", "step_prev", "Prev Step"),
        Binding("right", "step_next", "Next Step"),
        Binding("v", "revalidate", "Revalidate"),
        Binding("V", "revalidate", "Revalidate", show=False),
        Binding("s", "save_diagnostic", "Save"),
        Binding("S", "save_diagnostic", "Save", show=False),
        Binding("c", "copy_diagnostic", "Copy"),
        Binding("C", "copy_diagnostic", "Copy", show=False),
    ]

    CSS = """
    DiagnosticsScreen {
        layout: grid;
        grid-size: 1;
        grid-rows: auto 1fr auto;
    }

    #diag-header {
        height: 1;
        padding: 0 1;
        background: $primary;
    }

    #diag-content {
        padding: 1;
    }

    .diag-section {
        margin-bottom: 1;
        border: solid $surface-darken-2;
        padding: 1;
        height: auto;
    }

    #diag-health {
        height: auto;
    }

    #diag-disk {
        height: auto;
    }

    #diag-errors {
        height: auto;
    }

    #diag-samples {
        height: auto;
    }

    #diag-footer {
        height: 1;
        background: $surface-darken-1;
        padding: 0 1;
    }
    """

    def __init__(self, run_dir: Path, **kwargs):
        super().__init__(**kwargs)
        self._run_dir = Path(run_dir).resolve()
        self._run_name = self._run_dir.name
        self._manifest = {}
        self._pipeline = []
        self._step_health = []
        self._discrepancies = []
        self._selected_step_index = 0
        self._error_analysis = {}
        self._loading = True

    def compose(self) -> ComposeResult:
        yield Static(f"[bold]Diagnostics: {self._run_name}[/]", id="diag-header")
        with VerticalScroll(id="diag-content"):
            yield Static("Scanning...", id="diag-health", classes="diag-section")
            yield Static("", id="diag-disk", classes="diag-section")
            yield Static("", id="diag-errors", classes="diag-section")
            yield Static("", id="diag-samples", classes="diag-section")
        yield Static(
            "[bold]\u2190\u2192[/]:step  [bold]V[/]:revalidate  [bold]S[/]:save  [bold]C[/]:copy  [bold]Esc[/]:back",
            id="diag-footer"
        )

    def on_mount(self) -> None:
        """Load manifest and start background scan."""
        self._load_manifest()
        self._scan_in_background()

    def _load_manifest(self) -> None:
        """Load manifest data."""
        manifest_path = self._run_dir / "MANIFEST.json"
        if manifest_path.exists():
            try:
                self._manifest = json.loads(manifest_path.read_text())
                self._pipeline = self._manifest.get("pipeline", [])
            except Exception:
                self._manifest = {}
                self._pipeline = []

    @work(thread=True)
    def _scan_in_background(self) -> None:
        """Scan disk files in background thread."""
        try:
            step_health = scan_step_health(self._run_dir, self._pipeline)
            discrepancies = verify_disk_vs_manifest(self._run_dir, self._pipeline, self._manifest)

            # Get error analysis for initially selected step
            error_analysis = {}
            if self._pipeline:
                # Find first step with failures
                for i, sh in enumerate(step_health):
                    if sh.get("validation_failures", 0) > 0 or sh.get("hard_failures", 0) > 0:
                        self._selected_step_index = i
                        break
                if self._pipeline:
                    selected_step = self._pipeline[self._selected_step_index]
                    error_analysis = get_step_failure_analysis(self._run_dir, selected_step)

            # Update state on main thread
            self.app.call_from_thread(
                self._update_scan_results, step_health, discrepancies, error_analysis
            )
        except Exception as e:
            _log.debug(f"DiagnosticsScreen scan error: {e}")
            self.app.call_from_thread(
                self._show_scan_error, str(e)
            )

    def _update_scan_results(self, step_health, discrepancies, error_analysis) -> None:
        """Update display with scan results (called on main thread)."""
        self._step_health = step_health
        self._discrepancies = discrepancies
        self._error_analysis = error_analysis
        self._loading = False
        self._update_all_sections()

    def _show_scan_error(self, error_msg: str) -> None:
        """Show scan error."""
        self._loading = False
        try:
            section = self.query_one("#diag-health", Static)
            section.update(f"[red]Scan error: {error_msg}[/]")
        except NoMatches:
            pass

    def _update_all_sections(self) -> None:
        """Update all display sections."""
        self._update_health_section()
        self._update_disk_section()
        self._update_errors_section()
        self._update_samples_section()

    def _update_health_section(self) -> None:
        """Update the Run Health table."""
        lines = ["[bold]Run Health[/]", ""]

        if not self._step_health:
            lines.append("  No step data available")
        else:
            # Header
            lines.append("  {:<25s} {:>8s} {:>8s} {:>8s} {:>8s}  {}".format(
                "Step", "Expected", "Valid", "Val.Fail", "Hard", ""
            ))
            lines.append("  " + "\u2500" * 70)

            for i, sh in enumerate(self._step_health):
                step_name = sh["step"]
                if len(step_name) > 25:
                    step_name = step_name[:22] + "..."

                # Status indicator
                if sh["status"] == "ok":
                    indicator = "[green]\u2713[/]"
                elif sh["status"] == "warning":
                    indicator = "[yellow]\u26a0[/]"
                else:
                    indicator = "[red]\u2717[/]"

                # Highlight selected step
                prefix = "\u25b6 " if i == self._selected_step_index else "  "

                lines.append("{}{:<25s} {:>8d} {:>8d} {:>8d} {:>8d}  {}".format(
                    prefix,
                    step_name,
                    sh["expected"],
                    sh["valid"],
                    sh["validation_failures"],
                    sh["hard_failures"],
                    indicator,
                ))

        try:
            section = self.query_one("#diag-health", Static)
            section.update("\n".join(lines))
        except NoMatches:
            pass

    def _update_disk_section(self) -> None:
        """Update the Disk Verification section."""
        if not self._discrepancies:
            content = "[bold]Disk Verification[/]\n\n  [green]\u2713 All counts match disk files[/]"
        else:
            lines = [f"[bold]Disk Verification[/]  [yellow]\u26a0 {len(self._discrepancies)} discrepancies found[/]", ""]
            for d in self._discrepancies:
                lines.append(f"  [yellow]\u2022 {d}[/]")
            content = "\n".join(lines)

        try:
            section = self.query_one("#diag-disk", Static)
            section.update(content)
        except NoMatches:
            pass

    def _update_errors_section(self) -> None:
        """Update the Error Analysis section."""
        if not self._pipeline:
            return

        selected_step = self._pipeline[self._selected_step_index]
        total = self._error_analysis.get("total", 0)
        groups = self._error_analysis.get("groups", [])

        if total == 0:
            content = f"[bold]Error Analysis ({selected_step})[/]\n\n  [green]No failures[/]"
        else:
            lines = [f"[bold]Error Analysis ({selected_step} \u2014 {total} failures)[/]", ""]
            lines.append("  {:>6s} \u2502 {:<14s} \u2502 {}".format("Count", "Category", "Error"))
            lines.append("  " + "\u2500" * 70)
            for group in groups:
                category = group["category"]
                error = group["error"]
                if len(error) > 45:
                    error = error[:42] + "..."
                lines.append("  {:>6d} \u2502 {:<14s} \u2502 {}".format(
                    group["count"], category, error
                ))
            content = "\n".join(lines)

        try:
            section = self.query_one("#diag-errors", Static)
            section.update(content)
        except NoMatches:
            pass

    def _update_samples_section(self) -> None:
        """Update the Sample Failures section."""
        if not self._pipeline:
            return

        selected_step = self._pipeline[self._selected_step_index]
        samples = self._error_analysis.get("samples", [])
        total = self._error_analysis.get("total", 0)

        if not samples:
            try:
                section = self.query_one("#diag-samples", Static)
                section.update("")
            except NoMatches:
                pass
            return

        lines = [f"[bold]Sample Failures (showing {len(samples)} of {total})[/]", ""]

        for sample in samples:
            escaped_unit = self._escape_markup(sample["unit_id"])
            escaped_error = self._escape_markup(sample["error"])
            escaped_raw = self._escape_markup(sample["raw_response"])
            lines.append(f"  Unit: {escaped_unit}")
            lines.append(f"  Stage: {sample['stage']}")
            lines.append(f"  Error: {escaped_error}")
            lines.append(f"  Raw Response: {escaped_raw}")
            lines.append("")

        try:
            section = self.query_one("#diag-samples", Static)
            section.update("\n".join(lines))
        except NoMatches:
            pass

    def _escape_markup(self, text: str) -> str:
        """Escape Rich markup characters in text."""
        return str(text).replace("[", r"\[").replace("]", r"\]")

    def _load_error_analysis_for_step(self) -> None:
        """Load error analysis for the currently selected step."""
        if not self._pipeline:
            return
        selected_step = self._pipeline[self._selected_step_index]
        self._error_analysis = get_step_failure_analysis(self._run_dir, selected_step)
        self._update_errors_section()
        self._update_samples_section()
        self._update_health_section()  # Update selection indicator

    # -- Actions --

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_quit_app(self) -> None:
        self.app.exit()

    def action_step_prev(self) -> None:
        """Select previous step."""
        if self._pipeline and self._selected_step_index > 0:
            self._selected_step_index -= 1
            self._load_error_analysis_for_step()

    def action_step_next(self) -> None:
        """Select next step."""
        if self._pipeline and self._selected_step_index < len(self._pipeline) - 1:
            self._selected_step_index += 1
            self._load_error_analysis_for_step()

    def action_revalidate(self) -> None:
        """Re-validate failures for the selected step."""
        if not self._pipeline:
            self.notify("No pipeline steps available", severity="warning")
            return

        selected_step = self._pipeline[self._selected_step_index]
        total_failures = self._error_analysis.get("total", 0)

        if total_failures == 0:
            self.notify(f"No failures to re-validate for {selected_step}", severity="information")
            return

        self.notify(f"Re-validating {selected_step}...")
        self._run_revalidate(selected_step)

    @work(thread=True)
    def _run_revalidate(self, step_name: str) -> None:
        """Run --revalidate in a background thread to avoid freezing the UI."""
        try:
            scripts_dir = Path(__file__).parent.parent.parent
            result = subprocess.run(
                [
                    sys.executable,
                    str(scripts_dir / "orchestrate.py"),
                    "--revalidate",
                    "--run-dir", str(self._run_dir),
                    "--step", step_name,
                    "--use-source-config",
                ],
                capture_output=True,
                text=True,
                timeout=600,
                env={**__import__('os').environ, "PYTHONPATH": str(scripts_dir)},
            )

            if result.returncode == 0:
                output = result.stdout.strip()
                self.app.call_from_thread(
                    self.notify, f"Re-validation complete: {output}"
                )
            else:
                error = result.stderr.strip()
                self.app.call_from_thread(
                    self.notify, f"Re-validation failed: {error}", severity="error"
                )

            # Refresh data after revalidation
            self.app.call_from_thread(self._refresh_after_revalidate)

        except subprocess.TimeoutExpired:
            self.app.call_from_thread(
                self.notify, "Re-validation timed out (10 min limit)", severity="error"
            )
        except Exception as e:
            self.app.call_from_thread(
                self.notify, f"Re-validation error: {e}", severity="error"
            )

    def _refresh_after_revalidate(self) -> None:
        """Refresh all data after re-validation completes."""
        self._load_manifest()
        self._scan_in_background()

    def action_save_diagnostic(self) -> None:
        """Save full diagnostic report to DIAGNOSTIC.md."""
        try:
            report = generate_diagnostic(self._run_dir)
            output_path = self._run_dir / "DIAGNOSTIC.md"
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(report)

            try:
                relative_path = output_path.relative_to(Path.cwd())
            except ValueError:
                relative_path = output_path

            self.notify(f"Saved to {relative_path}")
        except Exception as e:
            self.notify(f"Save failed: {e}", severity="error")

    def action_copy_diagnostic(self) -> None:
        """Copy diagnostic report to clipboard."""
        try:
            import pyperclip
        except ImportError:
            self.notify("pyperclip not installed", severity="warning")
            return

        try:
            report = generate_diagnostic(self._run_dir)
            pyperclip.copy(report)
            self.notify("Diagnostic copied to clipboard")
        except Exception as e:
            self.notify(f"Copy failed: {e}", severity="error")
