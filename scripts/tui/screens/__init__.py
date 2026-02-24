"""
Screens package for Octobatch TUI.

Contains HomeScreen, MainScreen, and NewRunModal.
"""

from .common import (
    BOX_H, BOX_V, BOX_TL, BOX_TR, BOX_BL, BOX_BR,
    ARROW, BLOCK_FULL, BLOCK_EMPTY, CHECK, CIRCLE_FILLED, CIRCLE_EMPTY,
    make_progress_bar,
    parse_chunk_state,
    get_step_status_from_chunks,
    get_chunk_status_symbol,
    get_step_status_symbol,
)

from .home_screen import HomeScreen
from .main_screen import MainScreen
from .new_run_modal import NewRunModal
from .process_info import ProcessInfoScreen
from .splash_screen import SplashScreen

__all__ = [
    # Screens
    'HomeScreen',
    'MainScreen',
    'NewRunModal',
    'ProcessInfoScreen',
    'SplashScreen',
    # Constants
    'BOX_H', 'BOX_V', 'BOX_TL', 'BOX_TR', 'BOX_BL', 'BOX_BR',
    'ARROW', 'BLOCK_FULL', 'BLOCK_EMPTY', 'CHECK', 'CIRCLE_FILLED', 'CIRCLE_EMPTY',
    # Helper functions
    'make_progress_bar',
    'parse_chunk_state',
    'get_step_status_from_chunks',
    'get_chunk_status_symbol',
    'get_step_status_symbol',
]
