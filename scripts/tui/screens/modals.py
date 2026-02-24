"""Shared modal dialogs for TUI screens."""

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Static, Button
from textual.binding import Binding


class ArchiveConfirmModal(ModalScreen):
    """Modal for confirming run archive/unarchive."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("y", "confirm", "Yes"),
        Binding("n", "cancel", "No"),
    ]

    CSS = """
    ArchiveConfirmModal {
        align: center middle;
    }

    #archive-modal {
        width: 50%;
        height: auto;
        border: solid $warning;
        background: $surface;
        padding: 1;
    }

    #archive-title {
        text-align: center;
        text-style: bold;
        color: $warning;
        margin-bottom: 1;
    }

    #archive-message {
        text-align: center;
        margin-bottom: 1;
    }

    #button-row {
        height: auto;
        align: center middle;
        margin-top: 1;
    }

    Button {
        margin: 0 1;
    }
    """

    def __init__(self, run_name: str, is_unarchive: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.run_name = run_name
        self.is_unarchive = is_unarchive

    def compose(self) -> ComposeResult:
        action = "Unarchive" if self.is_unarchive else "Archive"
        with Vertical(id="archive-modal"):
            yield Static(f"{action} Run", id="archive-title")
            yield Static(
                f"Are you sure you want to {action.lower()} '{self.run_name}'?",
                id="archive-message"
            )
            with Horizontal(id="button-row"):
                yield Button("Yes", variant="warning", id="archive-btn")
                yield Button("No", variant="default", id="cancel-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "archive-btn":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
