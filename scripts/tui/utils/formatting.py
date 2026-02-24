"""
Formatting utilities for Octobatch TUI.

Provides pure functions for formatting costs, tokens, durations, and other values.
These functions don't depend on Textual or any UI framework.
"""

# Block characters for progress bars
BLOCK_FULL = "\u2588"  # █
BLOCK_EMPTY = "\u2591"  # ░


def format_cost(cost: float, show_zero: bool = False) -> str:
    """
    Format a cost value as a USD string.

    Args:
        cost: Cost value in USD
        show_zero: If True, show "$0.0000" for zero instead of "--"

    Returns:
        Formatted string like "$0.0100" or "--" for invalid values
    """
    if cost < 0:
        return "--"
    if cost == 0 and not show_zero:
        return "--"
    return f"${cost:.4f}"


def format_tokens(tokens: int, show_zero: bool = False) -> str:
    """
    Format a token count with thousands separator.

    Args:
        tokens: Token count
        show_zero: If True, show "0" for zero instead of "--"

    Returns:
        Formatted string like "100,332" or "--" for invalid values
    """
    if tokens < 0:
        return "--"
    if tokens == 0 and not show_zero:
        return "--"
    return f"{tokens:,}"


def format_duration(seconds: float) -> str:
    """
    Format duration in seconds as a human-readable string.

    Args:
        seconds: Duration in seconds

    Returns:
        String like "1:23:45" (hours:minutes:seconds) or "2:13" (minutes:seconds)
        Returns "0:00" for zero, "--:--" for negative values
    """
    if seconds < 0:
        return "--:--"

    if seconds == 0:
        return "0:00"

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_count(current: int, total: int) -> str:
    """
    Format a count as 'current/total'.

    Args:
        current: Current count
        total: Total count

    Returns:
        String like "4/6"
    """
    return f"{current}/{total}"


def format_percent(current: int, total: int) -> str:
    """
    Format a ratio as a percentage.

    Args:
        current: Current count
        total: Total count

    Returns:
        String like "67%" or "0%" for zero total
    """
    if total == 0:
        return "0%"
    pct = int((current / total) * 100)
    return f"{pct}%"


def format_progress_bar(percent: float, width: int = 10) -> str:
    """
    Create a text-based progress bar using block characters.

    Args:
        percent: Progress percentage (0-100)
        width: Width of the progress bar in characters

    Returns:
        String like "█████░░░░░" representing progress
    """
    # Clamp percent to 0-100
    percent = max(0.0, min(100.0, percent))
    ratio = percent / 100.0
    filled = int(ratio * width)
    empty = width - filled
    return BLOCK_FULL * filled + BLOCK_EMPTY * empty


def format_progress_bar_from_counts(current: int, total: int, width: int = 10) -> str:
    """
    Create a text-based progress bar from current/total counts.

    Args:
        current: Current count
        total: Total count
        width: Width of the progress bar in characters

    Returns:
        String like "█████░░░░░" representing progress
    """
    if total <= 0:
        return BLOCK_EMPTY * width
    percent = (current / total) * 100
    return format_progress_bar(percent, width)


def truncate_string(s: str, max_length: int, suffix: str = "...") -> str:
    """
    Truncate a string to a maximum length.

    Args:
        s: String to truncate
        max_length: Maximum length (including suffix)
        suffix: Suffix to add when truncating (default "...")

    Returns:
        Truncated string with suffix if needed
    """
    if len(s) <= max_length:
        return s
    if max_length <= len(suffix):
        return suffix[:max_length]
    return s[:max_length - len(suffix)] + suffix


def truncate_text(text: str, max_len: int) -> str:
    """
    Truncate text to max length with ellipsis.

    Alias for truncate_string with default suffix.

    Args:
        text: Text to truncate
        max_len: Maximum length including "..."

    Returns:
        Truncated text
    """
    return truncate_string(text, max_len, "...")


def pad_string(s: str, width: int, align: str = "left") -> str:
    """
    Pad a string to a specific width.

    Args:
        s: String to pad
        width: Target width
        align: "left", "right", or "center"

    Returns:
        Padded string
    """
    if len(s) >= width:
        return s[:width]

    if align == "right":
        return s.rjust(width)
    elif align == "center":
        return s.center(width)
    else:  # left
        return s.ljust(width)
