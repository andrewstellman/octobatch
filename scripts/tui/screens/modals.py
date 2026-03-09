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


class ConfirmModal(ModalScreen):
    """Generic confirmation modal with title and detail message."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("y", "confirm", "Yes"),
        Binding("n", "cancel", "No"),
    ]

    CSS = """
    ConfirmModal {
        align: center middle;
    }

    #confirm-modal {
        width: 50%;
        height: auto;
        border: solid $warning;
        background: $surface;
        padding: 1;
    }

    #confirm-title {
        text-align: center;
        text-style: bold;
        color: $warning;
        margin-bottom: 1;
    }

    #confirm-message {
        text-align: center;
        margin-bottom: 1;
    }

    #confirm-button-row {
        height: auto;
        align: center middle;
        margin-top: 1;
    }

    #confirm-button-row Button {
        margin: 0 1;
    }
    """

    def __init__(self, title: str, message: str = "", **kwargs):
        super().__init__(**kwargs)
        self._title = title
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-modal"):
            yield Static(self._title, id="confirm-title")
            if self._message:
                yield Static(self._message, id="confirm-message")
            with Horizontal(id="confirm-button-row"):
                yield Button("Yes", variant="warning", id="confirm-yes-btn")
                yield Button("No", variant="default", id="confirm-no-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-yes-btn":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class TextInputModal(ModalScreen):
    """Modal with a text input field."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    TextInputModal {
        align: center middle;
    }

    #text-input-modal {
        width: 50%;
        height: auto;
        border: solid $primary;
        background: $surface;
        padding: 1;
    }

    #text-input-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    #text-input-field {
        width: 100%;
        margin-bottom: 1;
    }

    #text-input-button-row {
        height: auto;
        align: center middle;
        margin-top: 1;
    }

    #text-input-button-row Button {
        margin: 0 1;
    }
    """

    def __init__(self, title: str, default: str = "", placeholder: str = "", **kwargs):
        super().__init__(**kwargs)
        self._title = title
        self._default = default
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        from textual.widgets import Input
        with Vertical(id="text-input-modal"):
            yield Static(self._title, id="text-input-title")
            yield Input(
                value=self._default,
                placeholder=self._placeholder,
                id="text-input-field",
            )
            with Horizontal(id="text-input-button-row"):
                yield Button("OK", variant="primary", id="text-input-ok-btn")
                yield Button("Cancel", variant="default", id="text-input-cancel-btn")

    def on_mount(self) -> None:
        from textual.widgets import Input
        self.query_one("#text-input-field", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        from textual.widgets import Input
        if event.button.id == "text-input-ok-btn":
            value = self.query_one("#text-input-field", Input).value
            self.dismiss(value)
        else:
            self.dismiss(None)

    def on_input_submitted(self, event) -> None:
        from textual.widgets import Input
        value = self.query_one("#text-input-field", Input).value
        self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss(None)
