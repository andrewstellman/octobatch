"""
Progress bar rendering utilities for Octobatch TUI.

Provides text-based progress bar rendering using block characters.
"""

from textual.widgets import Static

# Block drawing characters
BLOCK_FULL = "\u2588"  # █
BLOCK_EMPTY = "\u2591"  # ░

# Box drawing characters
BOX_H = "\u2500"  # ─
BOX_V = "\u2502"  # │
BOX_TL = "\u250c"  # ┌
BOX_TR = "\u2510"  # ┐
BOX_BL = "\u2514"  # └
BOX_BR = "\u2518"  # ┘

# Status symbols
CHECK = "\u2713"  # ✓
CROSS = "\u2717"  # ✗
CIRCLE_FILLED = "\u25cf"  # ●
CIRCLE_EMPTY = "\u25cb"  # ○

# Arrow
ARROW = "\u2500\u2500\u2500\u25b6"  # ───▶


def make_progress_bar(current: int, total: int, width: int = 20) -> str:
    """
    Create a text-based progress bar using block characters.

    Args:
        current: Current progress value
        total: Total/maximum value
        width: Width of the progress bar in characters

    Returns:
        String like "████████░░░░░░░░░░░░" representing progress
    """
    if total == 0:
        return BLOCK_EMPTY * width
    ratio = min(current / total, 1.0)
    filled = int(ratio * width)
    empty = width - filled
    return BLOCK_FULL * filled + BLOCK_EMPTY * empty


def format_progress_percent(current: int, total: int) -> str:
    """Format progress as a percentage string."""
    if total == 0:
        return "0%"
    pct = int((current / total) * 100)
    return f"{pct}%"


def format_progress_fraction(current: int, total: int) -> str:
    """Format progress as a fraction string like '24/30'."""
    return f"{current}/{total}"


class ProgressBar(Static):
    """A static widget displaying a text-based progress bar."""

    def __init__(
        self,
        current: int = 0,
        total: int = 100,
        width: int = 20,
        show_percent: bool = True,
        **kwargs
    ):
        self._current = current
        self._total = total
        self._width = width
        self._show_percent = show_percent
        super().__init__(self._render(), **kwargs)

    def _render(self) -> str:
        """Render the progress bar."""
        bar = make_progress_bar(self._current, self._total, self._width)
        if self._show_percent:
            pct = format_progress_percent(self._current, self._total)
            return f"[{bar}] {pct}"
        return f"[{bar}]"

    def update_progress(self, current: int, total: int | None = None) -> None:
        """Update the progress bar values and refresh display."""
        self._current = current
        if total is not None:
            self._total = total
        self.update(self._render())
