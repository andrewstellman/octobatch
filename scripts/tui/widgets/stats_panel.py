"""
Stats panel widgets for Octobatch TUI.

Provides reusable stat display panels for runs, chunks, and units.
"""

from textual.widgets import Static


class RunStatsPanel(Static):
    """
    Panel displaying run-level statistics.

    Shows total cost, tokens, duration, mode, and selected step info.
    """

    DEFAULT_CSS = """
    RunStatsPanel {
        border-left: solid $primary;
        padding: 0 1;
        min-width: 25;
    }
    """

    def __init__(self, **kwargs):
        super().__init__("", **kwargs)
        self._run_data = None
        self._selected_step_index = 0

    def update_stats(
        self,
        run_data,
        selected_step_index: int = 0
    ) -> None:
        """
        Update the stats panel with new data.

        Args:
            run_data: RunData object with run information
            selected_step_index: Index of currently selected step
        """
        self._run_data = run_data
        self._selected_step_index = selected_step_index
        self.update(self._render())

    def _render(self) -> str:
        """Render the stats content."""
        if self._run_data is None:
            return "[dim]No data[/]"

        # Handle empty/new runs gracefully
        total_cost = getattr(self._run_data, 'total_cost', 0) or 0
        total_tokens = getattr(self._run_data, 'total_tokens', 0) or 0
        duration = getattr(self._run_data, 'elapsed_time', '--:--') or '--:--'
        mode = getattr(self._run_data, 'mode', 'unknown') or 'unknown'
        mode_display = "Realtime" if mode == "realtime" else "Batch"

        content = f"""[bold]Run Stats[/]
────────────────────
Total Cost:    ${total_cost:.4f}
Total Tokens:  {total_tokens:,}
Duration:      {duration}
Mode:          {mode_display}
"""

        # Add selected step info if steps exist
        steps = getattr(self._run_data, 'steps', [])
        if steps and self._selected_step_index < len(steps):
            step = steps[self._selected_step_index]
            step_cost = getattr(step, 'cost', 0) or 0
            completed = getattr(step, 'completed', 0) or 0
            total = getattr(step, 'total', 0) or 0
            failed = total - completed if total > completed else 0

            content += f"""
[bold]Selected Step[/]
────────────────────
{step.name.upper()}
Passed:        {completed}
Failed:        {failed}
"""

        return content


class ChunkStatsPanel(Static):
    """
    Panel displaying chunk or unit statistics.

    Shows different content based on detail level:
    - Level 0 (chunks): chunk name, units, status, retries
    - Level 1 (units): unit name, status, step
    """

    DEFAULT_CSS = """
    ChunkStatsPanel {
        border-left: solid $primary;
        padding: 0 1;
        min-width: 25;
    }
    """

    def __init__(self, **kwargs):
        super().__init__("", **kwargs)
        self._detail_level = 0
        self._chunk = None
        self._unit = None
        self._pipeline = []

    def update_chunk_stats(self, chunk, pipeline: list, parse_chunk_state: callable, get_chunk_status_symbol: callable) -> None:
        """
        Update to show chunk statistics.

        Args:
            chunk: ChunkStatus object
            pipeline: List of pipeline step names
            parse_chunk_state: Function to parse chunk state
            get_chunk_status_symbol: Function to get status symbol
        """
        self._detail_level = 0
        self._chunk = chunk
        self._pipeline = pipeline
        self._parse_chunk_state = parse_chunk_state
        self._get_chunk_status_symbol = get_chunk_status_symbol
        self.update(self._render())

    def update_unit_stats(self, unit) -> None:
        """
        Update to show unit statistics.

        Args:
            unit: UnitRecord object
        """
        self._detail_level = 1
        self._unit = unit
        self.update(self._render())

    def clear_stats(self) -> None:
        """Clear the stats panel."""
        self._chunk = None
        self._unit = None
        self.update("[dim]No selection[/]")

    def _render(self) -> str:
        """Render the appropriate stats content."""
        if self._detail_level == 1:
            return self._render_unit_stats()
        return self._render_chunk_stats()

    def _render_chunk_stats(self) -> str:
        """Render chunk statistics."""
        if self._chunk is None:
            return "[dim]No chunk selected[/]"

        current_step, status, _ = self._parse_chunk_state(self._chunk.state, self._pipeline)
        step_display = current_step if current_step not in ("pending", "unknown") else "pending"
        symbol = self._get_chunk_status_symbol(self._chunk.state, self._pipeline)

        return f"""[bold]Chunk Stats[/]
────────────────────
{self._chunk.name}
Units:         {self._chunk.valid}/{self._chunk.total}
Status:        {symbol} {step_display}
Retries:       {self._chunk.failed}
"""

    def _render_unit_stats(self) -> str:
        """Render unit statistics."""
        if self._unit is None:
            return "[dim]No unit selected[/]"

        status = "[green]✓ valid[/]" if self._unit.status == "valid" else "[red]✗ failed[/]"
        unit_name = self._unit.unit_id
        if len(unit_name) > 20:
            unit_name = unit_name[:17] + "..."

        return f"""[bold]Unit Stats[/]
────────────────────
{unit_name}
Status:        {status}
Step:          {self._unit.step}
"""
