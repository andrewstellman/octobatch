"""
Common constants and helper functions for TUI screens.
"""

import logging
import os

# Import shared constants and utilities from widgets
from ..widgets.progress_bar import (
    BOX_H, BOX_V, BOX_TL, BOX_TR, BOX_BL, BOX_BR,
    BLOCK_FULL, BLOCK_EMPTY,
    CHECK, CIRCLE_FILLED, CIRCLE_EMPTY,
    ARROW,
    make_progress_bar,
)

# Import pure status utilities
from ..utils.status import parse_chunk_state

# Debug logger for TUI
_log = logging.getLogger("octobatch.tui.screens")


def get_step_status_from_chunks(step_name: str, step_index: int, chunks: list, pipeline: list[str]) -> str:
    """
    Determine a pipeline step's status by examining all chunks.

    Args:
        step_name: The step to check
        step_index: Index of this step in the pipeline
        chunks: List of ChunkStatus objects
        pipeline: Full pipeline list

    Returns:
        "complete" - All chunks have passed this step
        "in_progress" - At least one chunk is at this step
        "pending" - No chunks have reached this step yet
    """
    if not chunks:
        return "pending"

    has_in_progress = False
    all_complete = True

    for chunk in chunks:
        _, status, chunk_step_idx = parse_chunk_state(chunk.state, pipeline)

        if status == "complete":
            # Chunk is VALIDATED - all steps complete
            continue
        elif status == "in_progress":
            if chunk_step_idx == step_index:
                # Chunk is currently at this step
                has_in_progress = True
                all_complete = False
            elif chunk_step_idx > step_index:
                # Chunk has passed this step
                continue
            else:
                # Chunk hasn't reached this step yet
                all_complete = False
        else:
            # status == "pending" - chunk hasn't started
            all_complete = False

    if all_complete:
        return "complete"
    elif has_in_progress:
        return "in_progress"
    else:
        # Check if any chunk has passed this step (even if not all complete)
        for chunk in chunks:
            _, status, chunk_step_idx = parse_chunk_state(chunk.state, pipeline)
            if status == "complete" or chunk_step_idx > step_index:
                # At least one chunk has passed this step
                # Some chunks are past this step, none currently at it
                return "complete"
        return "pending"


def get_chunk_status_symbol(chunk_state: str, pipeline: list[str]) -> str:
    """
    Get status symbol for a chunk based on its state.

    Returns:
        ✓ (green) - VALIDATED
        ● (yellow) - In progress (state contains step name with suffix)
        ○ (dim) - PENDING or hasn't started
    """
    _, status, _ = parse_chunk_state(chunk_state, pipeline)

    if status == "complete":
        return f"[green]{CHECK}[/]"
    elif status == "in_progress":
        return f"[yellow]{CIRCLE_FILLED}[/]"
    else:
        return f"[dim]{CIRCLE_EMPTY}[/]"


def get_step_status_symbol(status: str) -> str:
    """
    Get status symbol for a pipeline step.

    Args:
        status: "complete", "in_progress", or "pending"
    """
    if status == "complete":
        return f"[green]{CHECK}[/]"
    elif status == "in_progress":
        return f"[yellow]{CIRCLE_FILLED}[/]"
    else:
        return f"[dim]{CIRCLE_EMPTY}[/]"


def check_missing_api_keys() -> list[str]:
    """
    Check which provider API keys are missing from the environment.

    Loads provider config from models.yaml and checks env_var/alt_env_var.

    Returns:
        List of missing API key names (primary env_var names only)
    """
    try:
        from providers.base import LLMProvider
        providers = LLMProvider.get_all_providers()
    except Exception:
        # Fall back to empty if we can't load providers
        return []

    missing = []
    for provider_name, provider_info in providers.items():
        env_var = provider_info.get("env_var", "")
        alt_env_var = provider_info.get("alt_env_var", "")

        # Check if either key is set
        has_key = False
        if env_var and os.environ.get(env_var):
            has_key = True
        if alt_env_var and os.environ.get(alt_env_var):
            has_key = True

        if not has_key and env_var:
            missing.append(env_var)

    return missing


def get_resource_stats() -> str:
    """Get TUI process memory and CPU usage as a compact string.

    Returns something like 'MEM: 45MB | CPU: 2.1%'.
    Returns empty string if psutil is unavailable.
    """
    try:
        import psutil
        proc = psutil.Process()
        mem_bytes = proc.memory_info().rss
        mem_mb = mem_bytes / (1024 * 1024)
        cpu_pct = proc.cpu_percent(interval=0)
        return f"MEM: {mem_mb:.0f}MB | CPU: {cpu_pct:.1f}%"
    except Exception:
        return ""


_last_os_title: str = ""


def set_os_terminal_title(title: str) -> None:
    """Set the OS terminal window/tab title via ANSI OSC escape sequence.

    Textual redirects stdout at the fd level (os.dup2) so both sys.stdout
    and sys.__stdout__ feed into Textual's internal pipe — writes there
    only appear when Textual's event loop ticks (i.e. on the next keypress).

    Opening /dev/tty gives us a fresh fd to the real controlling terminal,
    completely independent of any redirections.

    A module-level ``_last_os_title`` cache deduplicates writes: if the
    title hasn't changed since the last successful write, we skip the
    syscall entirely.  This prevents background polls from re-emitting the
    same title and avoids any visible flicker.
    """
    global _last_os_title
    if title == _last_os_title:
        return
    try:
        if os.name == "nt":
            # Windows: use ctypes to set the console title directly
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW(title)
        else:
            # Unix: write OSC escape via /dev/tty to bypass Textual's
            # stdout capture (os.dup2 redirection).
            fd = os.open("/dev/tty", os.O_WRONLY | os.O_NOCTTY)
            try:
                os.write(fd, f"\x1b]2;{title}\x07".encode())
            finally:
                os.close(fd)
        _last_os_title = title
    except (OSError, AttributeError):
        pass
