"""
Status utilities for Octobatch TUI.

Pure functions for determining and formatting status information.
These functions don't depend on Textual or any UI framework.
"""

# Status symbols (Unicode characters)
CHECK = "\u2713"  # ✓
CROSS = "\u2717"  # ✗
CIRCLE_FILLED = "\u25cf"  # ●
CIRCLE_EMPTY = "\u25cb"  # ○

# Valid status values
VALID_STATUSES = {"complete", "in_progress", "active", "pending", "failed", "unknown"}


def get_status_symbol(status: str) -> str:
    """
    Get the status symbol for a given status string.

    Args:
        status: One of "complete", "in_progress", "active", "pending", "failed"

    Returns:
        Unicode symbol: ✓ (complete), ● (in_progress/active), ✗ (failed), ○ (pending)
    """
    status = status.lower()
    if status == "complete":
        return CHECK
    elif status in ("in_progress", "active"):
        return CIRCLE_FILLED
    elif status == "failed":
        return CROSS
    else:
        return CIRCLE_EMPTY


def get_status_color(status: str) -> str:
    """
    Get the color name for a given status.

    Args:
        status: One of "complete", "in_progress", "active", "pending", "failed"

    Returns:
        Color name: "green", "yellow", "red", or "dim"
    """
    status = status.lower()
    if status == "complete":
        return "green"
    elif status in ("in_progress", "active"):
        return "yellow"
    elif status == "failed":
        return "red"
    else:
        return "dim"


def parse_chunk_state(state: str, pipeline: list[str]) -> tuple[str, str, int]:
    """
    Parse chunk state into (current_step, status, step_index).

    This is a pure function that parses chunk state strings.

    Args:
        state: The chunk state string (e.g., "VALIDATED", "generate_PENDING")
        pipeline: List of pipeline step names in order

    Returns:
        Tuple of (current_step, status, step_index):
        - current_step: The step name (or "complete" if VALIDATED, "pending" if PENDING)
        - status: "complete", "in_progress", or "pending"
        - step_index: Index in pipeline (-1 if pending, len(pipeline) if complete)
    """
    if state == "VALIDATED":
        return ("complete", "complete", len(pipeline))

    if state == "PENDING":
        return ("pending", "pending", -1)

    # Parse states like "generate_PENDING", "score_wounds_SUBMITTED"
    for i, step in enumerate(pipeline):
        if state.startswith(step + "_"):
            return (step, "in_progress", i)

    return ("unknown", "pending", -1)


def determine_step_status(
    step_index: int,
    chunk_states: list[tuple[str, int]]
) -> str:
    """
    Determine a pipeline step's status from chunk state information.

    Args:
        step_index: Index of this step in the pipeline
        chunk_states: List of (status, chunk_step_idx) tuples from parse_chunk_state

    Returns:
        "complete" - All chunks have passed this step
        "in_progress" - At least one chunk is at this step
        "pending" - No chunks have reached this step yet
    """
    if not chunk_states:
        return "pending"

    has_in_progress = False
    all_complete = True

    for status, chunk_step_idx in chunk_states:
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
        # Check if any chunk has passed this step
        for status, chunk_step_idx in chunk_states:
            if status == "complete" or chunk_step_idx > step_index:
                return "complete"
        return "pending"


def determine_run_status(step_statuses: list[str]) -> str:
    """
    Determine overall run status from individual step statuses.

    Args:
        step_statuses: List of status strings for each step

    Returns:
        "complete" - All steps complete
        "active" - At least one step in progress
        "failed" - Any step failed
        "pending" - No steps started
    """
    if not step_statuses:
        return "pending"

    has_active = False
    has_failed = False
    all_complete = True

    for status in step_statuses:
        if status == "failed":
            has_failed = True
            all_complete = False
        elif status in ("in_progress", "active"):
            has_active = True
            all_complete = False
        elif status == "pending":
            all_complete = False

    if has_failed:
        return "failed"
    elif all_complete:
        return "complete"
    elif has_active:
        return "active"
    else:
        return "pending"


def determine_chunk_status(chunk_state: str, pipeline: list[str]) -> str:
    """
    Determine chunk status from its state string.

    Args:
        chunk_state: The chunk state string
        pipeline: List of pipeline step names

    Returns:
        "complete", "in_progress", or "pending"
    """
    _, status, _ = parse_chunk_state(chunk_state, pipeline)
    return status


def calculate_step_progress(completed: int, total: int) -> float:
    """
    Calculate step progress as a ratio (0.0 to 1.0).

    Args:
        completed: Number of completed items
        total: Total number of items

    Returns:
        Progress ratio (0.0 to 1.0)
    """
    if total <= 0:
        return 0.0
    return min(completed / total, 1.0)


def calculate_run_progress(step_completions: list[tuple[int, int]]) -> float:
    """
    Calculate overall run progress from step completions.

    Args:
        step_completions: List of (completed, total) tuples for each step

    Returns:
        Overall progress ratio (0.0 to 1.0)
    """
    if not step_completions:
        return 0.0

    total_completed = sum(c for c, _ in step_completions)
    total_items = sum(t for _, t in step_completions)

    if total_items <= 0:
        return 0.0
    return min(total_completed / total_items, 1.0)
