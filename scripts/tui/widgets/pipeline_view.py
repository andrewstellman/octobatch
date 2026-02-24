"""
Pipeline visualization widget for Octobatch TUI.

Renders a horizontal pipeline as connected boxes with arrows.
"""

import re
from rich.cells import cell_len
from textual.widgets import Static
from textual.containers import Vertical

from .progress_bar import BOX_H, BOX_V, BOX_TL, BOX_TR, BOX_BL, BOX_BR


def _format_count(n: int) -> str:
    """Format count, abbreviating large numbers with K suffix."""
    if n >= 10000:
        return f"{n/1000:.0f}K"
    elif n >= 1000:
        return f"{n/1000:.1f}K"
    return str(n)


def _format_progress(progress_str: str) -> str:
    """Format progress string, abbreviating large numbers."""
    # Handle "--/--" or similar
    if "--" in progress_str:
        return progress_str

    # Parse "1234/5678" format
    match = re.match(r'(\d+)/(\d+)', progress_str)
    if match:
        completed = int(match.group(1))
        total = int(match.group(2))
        return f"{_format_count(completed)}/{_format_count(total)}"

    return progress_str


def render_pipeline_boxes(
    steps: list,
    selected_index: int,
    get_step_status: callable,
    get_status_symbol: callable,
    box_width: int = 18,
    get_failures: callable = None,
    step_configs: list = None,
    get_batch_detail: callable = None,
    step_funnel: dict = None,
) -> str:
    """
    Render a pipeline visualization as connected boxes with arrows.

    Args:
        steps: List of step objects (must have .name and .progress_str attributes)
        selected_index: Index of the currently selected step
        get_step_status: Callable(step, index) -> status string
        get_status_symbol: Callable(status) -> symbol string
        box_width: Inner width of each box
        get_failures: Optional callable(step, index) -> dict {"validation": N, "hard": N, "total": N}
        step_configs: Optional list of step config dicts to check for loop_until
        get_batch_detail: Optional callable(step, index) -> (submitted, pending) counts
        step_funnel: Optional dict of {step_name: (valid_count, input_count)} for funnel view.
                     When provided, each step shows its own throughput instead of global progress.

    Returns:
        Multi-line string rendering of the pipeline
    """
    if not steps:
        return "[dim]No pipeline steps[/]"

    ARROW_STR = "────▶"

    def make_box_line(char_left: str, char_fill: str, char_right: str, selected: bool) -> str:
        """Create a line of the box."""
        content = char_fill * box_width
        if selected:
            return f"[bold cyan]{char_left}{content}{char_right}[/]"
        return f"{char_left}{content}{char_right}"

    def make_content_line(text: str, char_side: str, selected: bool) -> str:
        """Create a content line centered in the box."""
        # Remove markup for length calculation
        plain_text = re.sub(r'\[/?[^\]]+\]', '', text)
        padding = box_width - cell_len(plain_text)
        left_pad = padding // 2
        right_pad = padding - left_pad
        padded = " " * left_pad + text + " " * right_pad
        if selected:
            # Use reverse video for selected step content to make it stand out
            return f"[bold cyan]{char_side}[/][reverse]{padded}[/reverse][bold cyan]{char_side}[/]"
        return f"{char_side}{padded}{char_side}"

    lines = []

    # Row 1: Top borders
    row = []
    for i, step in enumerate(steps):
        selected = (i == selected_index)
        row.append(make_box_line(BOX_TL, BOX_H, BOX_TR, selected))
        if i < len(steps) - 1:
            row.append("     ")  # Space for arrow row
    lines.append("".join(row))

    # Row 2: Step names (may need 2 rows for long names)
    step_name_rows = []
    for i, step in enumerate(steps):
        name = step.name.upper()

        # Build indicator suffix separately to avoid markup splitting during word-wrap
        suffix_parts = []
        if step_configs and i < len(step_configs):
            step_cfg = step_configs[i]
            if isinstance(step_cfg, dict) and step_cfg.get("loop_until"):
                suffix_parts.append("[magenta]↻loops[/magenta]")

        if step_funnel and step.name in step_funnel:
            funnel_valid, funnel_input = step_funnel[step.name]
            if funnel_valid == funnel_input and funnel_input > 0:
                suffix_parts.append("[green]🏁[/green]")
        else:
            progress_match = re.match(r'(\d+)/(\d+)', step.progress_str)
            if progress_match:
                completed = int(progress_match.group(1))
                total = int(progress_match.group(2))
                if completed == total and total > 0:
                    suffix_parts.append("[green]🏁[/green]")

        suffix = (" " + " ".join(suffix_parts)) if suffix_parts else ""
        suffix_plain = re.sub(r'\[/?[^\]]+\]', '', suffix)
        suffix_width = cell_len(suffix_plain)

        # Word-wrap the plain name, then append suffix to the last line
        name_width = cell_len(name)
        if name_width + suffix_width <= box_width - 2:
            step_name_rows.append([name + suffix])
        else:
            # Split the plain name into multiple lines (no markup in word-wrap input)
            words = name.replace("_", " ").split()
            row1, row2 = [], []
            current = row1
            current_len = 0
            for word in words:
                word_width = cell_len(word)
                if current_len + word_width + (1 if current else 0) <= box_width - 2:
                    current.append(word)
                    current_len += word_width + (1 if len(current) > 1 else 0)
                else:
                    current = row2
                    current.append(word)
                    current_len = word_width
            # Append suffix to the last line, or overflow to new row
            if row2:
                last_line = " ".join(row2)
                last_line_width = cell_len(last_line)
                if last_line_width + suffix_width <= box_width - 2:
                    step_name_rows.append([" ".join(row1), last_line + suffix])
                else:
                    # Suffix doesn't fit on last line, give it its own row
                    step_name_rows.append([" ".join(row1), last_line, suffix.strip()])
            else:
                last_line = " ".join(row1)
                last_line_width = cell_len(last_line)
                if last_line_width + suffix_width <= box_width - 2:
                    step_name_rows.append([last_line + suffix])
                else:
                    # Suffix doesn't fit, put it on row 2
                    step_name_rows.append([last_line, suffix.strip()])

    max_name_rows = max(len(r) for r in step_name_rows)

    for row_idx in range(max_name_rows):
        row = []
        for i, step in enumerate(steps):
            selected = (i == selected_index)
            name_parts = step_name_rows[i]
            text = name_parts[row_idx] if row_idx < len(name_parts) else ""
            row.append(make_content_line(text, BOX_V, selected))
            if i < len(steps) - 1:
                # Arrow only on middle row
                if row_idx == max_name_rows // 2:
                    row.append(ARROW_STR)
                else:
                    row.append("     ")
        lines.append("".join(row))

    # Row 3: Status line with symbol and progress (abbreviated for large numbers)
    row = []
    for i, step in enumerate(steps):
        selected = (i == selected_index)
        step_status = get_step_status(step, i)
        symbol = get_status_symbol(step_status)

        # Use funnel view (per-step throughput) when available
        if step_funnel and step.name in step_funnel:
            funnel_valid, funnel_input = step_funnel[step.name]
            if funnel_valid == 0 and funnel_input == 0:
                progress = "--/--"
            else:
                progress = _format_progress(f"{funnel_valid}/{funnel_input}")
        else:
            progress = _format_progress(step.progress_str)

        # In batch mode, show submitted/pending counts instead of "0/N"
        batch_detail = get_batch_detail(step, i) if get_batch_detail else None
        if batch_detail:
            submitted_count, pending_count = batch_detail
            if submitted_count > 0:
                status_text = f"[cyan]\U0001f4e4 {_format_count(submitted_count)} processing[/]"
            elif pending_count > 0:
                status_text = f"[yellow]\u23f3 {_format_count(pending_count)} pending[/]"
            else:
                status_text = f"{symbol} {progress}"
        else:
            status_text = f"{symbol} {progress}"

        row.append(make_content_line(status_text, BOX_V, selected))
        if i < len(steps) - 1:
            row.append("     ")
    lines.append("".join(row))

    # Row 4+: Failures lines (retrying yellow, exhausted dark_orange, hard red)
    if get_failures:
        # Collect categorized failures for each step
        step_failures = []
        has_any_retrying = False
        has_any_exhausted = False
        has_any_hard = False
        for i, step in enumerate(steps):
            failures = get_failures(step, i)
            if isinstance(failures, dict):
                retrying_count = failures.get("retrying", 0)
                exhausted_count = failures.get("exhausted", 0)
                hard_count = failures.get("hard", 0)
                # Backward compat: if no retrying/exhausted keys, fall back to validation
                if retrying_count == 0 and exhausted_count == 0:
                    exhausted_count = failures.get("validation", 0)
            else:
                # Backward compat: treat int as all exhausted
                retrying_count = 0
                exhausted_count = failures if failures else 0
                hard_count = 0
            step_failures.append((retrying_count, exhausted_count, hard_count))
            if retrying_count > 0:
                has_any_retrying = True
            if exhausted_count > 0:
                has_any_exhausted = True
            if hard_count > 0:
                has_any_hard = True

        if has_any_retrying:
            row = []
            for i, step in enumerate(steps):
                selected = (i == selected_index)
                count = step_failures[i][0]
                text = f"[yellow]↻ {_format_count(count)} retrying[/]" if count > 0 else ""
                row.append(make_content_line(text, BOX_V, selected))
                if i < len(steps) - 1:
                    row.append("     ")
            lines.append("".join(row))

        if has_any_exhausted:
            row = []
            for i, step in enumerate(steps):
                selected = (i == selected_index)
                count = step_failures[i][1]
                text = f"[dark_orange]⚠ {_format_count(count)} exhausted[/]" if count > 0 else ""
                row.append(make_content_line(text, BOX_V, selected))
                if i < len(steps) - 1:
                    row.append("     ")
            lines.append("".join(row))

        if has_any_hard:
            row = []
            for i, step in enumerate(steps):
                selected = (i == selected_index)
                hard_count = step_failures[i][2]
                hard_text = f"[red]✗ {_format_count(hard_count)} failed[/]" if hard_count > 0 else ""
                row.append(make_content_line(hard_text, BOX_V, selected))
                if i < len(steps) - 1:
                    row.append("     ")
            lines.append("".join(row))

    # Bottom borders
    row = []
    for i, step in enumerate(steps):
        selected = (i == selected_index)
        row.append(make_box_line(BOX_BL, BOX_H, BOX_BR, selected))
        if i < len(steps) - 1:
            row.append("     ")
    lines.append("".join(row))

    return "\n".join(lines)


class PipelineView(Vertical):
    """
    A widget that displays a horizontal pipeline visualization.

    The pipeline is rendered as connected boxes with arrows showing
    the flow from one step to the next.
    """

    def __init__(
        self,
        steps: list = None,
        selected_index: int = 0,
        get_step_status: callable = None,
        get_status_symbol: callable = None,
        **kwargs
    ):
        super().__init__(**kwargs)
        self._steps = steps or []
        self._selected_index = selected_index
        self._get_step_status = get_step_status or (lambda s, i: "pending")
        self._get_status_symbol = get_status_symbol or (lambda s: "○")

    def compose(self):
        yield Static(self._render())

    def _render(self) -> str:
        """Render the pipeline visualization."""
        return render_pipeline_boxes(
            self._steps,
            self._selected_index,
            self._get_step_status,
            self._get_status_symbol
        )

    def update_selection(self, selected_index: int) -> None:
        """Update the selected step and refresh."""
        self._selected_index = selected_index
        self.remove_children()
        self.mount(Static(self._render()))

    def update_steps(self, steps: list) -> None:
        """Update the steps list and refresh."""
        self._steps = steps
        self.remove_children()
        self.mount(Static(self._render()))
