"""
Utility modules for Octobatch TUI.
"""

from .pipelines import (
    get_pipelines_dir,
    scan_pipelines,
    load_pipeline_config,
    get_pipeline_path,
    list_pipeline_names,
)

from .runs import (
    get_runs_dir,
    scan_runs,
    get_active_runs,
    get_recent_runs,
    count_active_runs,
    calculate_dashboard_stats,
    format_token_count,
    format_elapsed_time,
)

from .formatting import (
    format_cost,
    format_tokens,
    format_duration,
    format_count,
    format_percent,
    format_progress_bar,
    format_progress_bar_from_counts,
    truncate_string,
    truncate_text,
    pad_string,
    BLOCK_FULL,
    BLOCK_EMPTY,
)

from .status import (
    get_status_symbol,
    get_status_color,
    parse_chunk_state,
    determine_step_status,
    determine_run_status,
    determine_chunk_status,
    calculate_step_progress,
    calculate_run_progress,
    CHECK,
    CROSS,
    CIRCLE_FILLED,
    CIRCLE_EMPTY,
)

__all__ = [
    # Pipeline utilities
    'get_pipelines_dir',
    'scan_pipelines',
    'load_pipeline_config',
    'get_pipeline_path',
    'list_pipeline_names',
    # Run utilities
    'get_runs_dir',
    'scan_runs',
    'get_active_runs',
    'get_recent_runs',
    'count_active_runs',
    'calculate_dashboard_stats',
    'format_token_count',
    'format_elapsed_time',
    # Formatting utilities
    'format_cost',
    'format_tokens',
    'format_duration',
    'format_count',
    'format_percent',
    'format_progress_bar',
    'format_progress_bar_from_counts',
    'truncate_string',
    'truncate_text',
    'pad_string',
    'BLOCK_FULL',
    'BLOCK_EMPTY',
    # Status utilities
    'get_status_symbol',
    'get_status_color',
    'parse_chunk_state',
    'determine_step_status',
    'determine_run_status',
    'determine_chunk_status',
    'calculate_step_progress',
    'calculate_run_progress',
    'CHECK',
    'CROSS',
    'CIRCLE_FILLED',
    'CIRCLE_EMPTY',
]
