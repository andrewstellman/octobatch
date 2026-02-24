"""
Main Textual application for Octobatch TUI.
"""

import logging
from pathlib import Path
from typing import Optional

from textual.app import App

from version import __version__
from .data import load_run_data, RunData
from .screens import MainScreen, HomeScreen, SplashScreen

# Debug logger for TUI
_log = logging.getLogger("octobatch.tui.app")


class OctobatchApp(App):
    """Octobatch TUI Application."""

    TITLE = f"Octobatch v{__version__}"

    CSS = """
    Screen {
        background: $surface;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

    def __init__(self, run_dir: Optional[Path] = None, **kwargs):
        super().__init__(**kwargs)
        self.run_dir = Path(run_dir) if run_dir else None
        self.run_data: RunData | None = None

    def exit(self, result=None, return_code=0, message=None):
        """Override exit to log when app exits."""
        import traceback
        _log.debug(f"OctobatchApp.exit called: result={result}, return_code={return_code}, message={message}")
        _log.debug(f"Exit traceback:\n{''.join(traceback.format_stack())}")
        super().exit(result=result, return_code=return_code, message=message)

    def on_mount(self) -> None:
        """Load data and push appropriate screen when app starts."""
        _log.debug("OctobatchApp.on_mount called")

        if self.run_dir is None:
            # No run_dir provided, show HomeScreen
            _log.debug("No run_dir, pushing HomeScreen")
            self.push_screen(HomeScreen())
            self.push_screen(SplashScreen())
            return

        try:
            self.run_data = load_run_data(self.run_dir)
            _log.debug(f"Loaded run data, pushing MainScreen. screen_stack before: {len(self.screen_stack)}")
            self.push_screen(MainScreen(self.run_data))
            self.push_screen(SplashScreen())
            _log.debug(f"screen_stack after push: {len(self.screen_stack)}")
        except FileNotFoundError as e:
            self.exit(message=str(e))
        except Exception as e:
            self.exit(message=f"Error loading run data: {e}")

    def action_quit(self) -> None:
        """Quit the application."""
        _log.debug("OctobatchApp.action_quit called")
        self.exit()


def run_tui(run_dir: Optional[Path] = None, debug: bool = False) -> None:
    """Run the TUI application.

    Args:
        run_dir: Optional path to a run directory. If not provided, shows HomeScreen.
        debug: Enable debug logging to tui_debug.log
    """
    if debug:
        # Enable debug logging to file with immediate flushing
        handler = logging.FileHandler("tui_debug.log", mode='w')
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))

        # Create a custom handler that flushes immediately
        class FlushingHandler(logging.FileHandler):
            def emit(self, record):
                super().emit(record)
                self.flush()

        handler = FlushingHandler("tui_debug.log", mode='w')
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))

        # Add handler to all our loggers
        for logger_name in ['octobatch.tui.app', 'octobatch.tui.screens']:
            logger = logging.getLogger(logger_name)
            logger.setLevel(logging.DEBUG)
            logger.addHandler(handler)

        _log.debug(f"Starting TUI with run_dir={run_dir}")
    app = OctobatchApp(run_dir)
    app.run()

    # Force reset terminal state even if Textual crashes mid-cleanup.
    # Subprocesses spawned from the TUI can inherit raw terminal mode,
    # leaving the user's shell in a broken state after exit.
    import os
    if os.name != 'nt':
        os.system('stty sane 2>/dev/null')
