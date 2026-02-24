"""
Splash screen showing Otto the Octopus on startup.

Appears as a floating overlay in the bottom-right corner.
Auto-dismisses after 10 seconds, or on Escape / clicking the X button.
Supports type-through: any non-escape key dismisses the splash and
forwards the key to the underlying screen.
"""

import random

from textual import events
from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Button, Label

from ..widgets.otto_widget import OttoWidget


class SplashScreen(ModalScreen):
    """Non-blocking Otto splash overlay in the bottom-right corner."""

    CSS = """
    SplashScreen {
        background: transparent;
        align: right bottom;
    }

    #splash-container {
        width: auto;
        min-width: 40;
        height: auto;
        margin: 1 2;
        padding: 1 2;
        background: $panel;
        border-left: thick $success;
        border-top: none;
        border-right: none;
        border-bottom: none;
        layer: overlay;
        align: center middle;
    }

    #splash-otto {
        width: auto;
        height: 10;
        content-align: center middle;
    }

    #splash-label {
        width: 100%;
        text-align: center;
        text-style: bold;
        color: $text;
        margin-top: 1;
    }

    #splash-close {
        dock: right;
        width: 3;
        min-width: 3;
        height: 1;
        margin: 0;
        padding: 0;
        background: transparent;
        border: none;
        color: $text-muted;
    }

    #splash-close:hover {
        color: $text;
        background: $surface;
    }
    """

    def compose(self) -> ComposeResult:
        with Container(id="splash-container"):
            yield Button("\u2715", id="splash-close", variant="default")
            yield OttoWidget(id="splash-otto")
            yield Label("\U0001f419 Welcome to Octobatch!", id="splash-label")

    def on_mount(self) -> None:
        self.set_timer(10.0, self._auto_dismiss)
        # Kick off a welcome animation sequence on Otto
        otto = self.query_one("#splash-otto", OttoWidget)
        colors = ["bright_cyan", "bright_magenta", "bright_green", "bright_yellow"]
        # Stagger a few transfers so Otto looks lively
        for i, delay in enumerate([0.3, 0.9, 1.6, 2.5]):
            from_tip = random.randint(1, 3)
            to_tip = random.randint(4, 6)
            color = colors[i % len(colors)]
            self.set_timer(delay, lambda f=from_tip, t=to_tip, c=color: otto.start_transfer(f, t, c))
        # Wave a flag near the end
        self.set_timer(3.5, otto.trigger_flag)

    def _auto_dismiss(self) -> None:
        if self.is_current:
            self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        """Type-through: dismiss splash and forward non-escape keys."""
        event.prevent_default()
        event.stop()
        self.dismiss(None)
        if event.key != "escape":
            self.app.post_message(events.Key(event.key, event.character))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "splash-close":
            self.dismiss(None)
