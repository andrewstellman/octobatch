"""
Configuration list screen for pipeline editor.

Shows a list of all available pipeline configurations.
"""

from pathlib import Path
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Static, DataTable
from textual.binding import Binding

from ..utils.pipelines import get_pipelines_dir
from .models import PipelineConfig, discover_pipelines
from .modals import NewConfigModal, RenameConfigModal, DeleteConfigModal


class ConfigListScreen(ModalScreen):
    """Modal screen listing all pipeline configurations."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "quit_app", "Quit"),
        Binding("Q", "quit_app", "Quit", show=False),
        Binding("n", "new_config", "New"),
        Binding("e", "edit_config", "Edit"),
        Binding("r", "rename_config", "Rename"),
        Binding("d", "delete_config", "Delete"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("enter", "edit_config", "Select"),
    ]

    CSS = """
    ConfigListScreen {
        align: center middle;
    }

    #config-modal {
        width: 70%;
        height: 70%;
        border: solid $primary;
        background: $surface;
    }

    #config-title {
        dock: top;
        height: 1;
        background: $primary;
        color: $text;
        text-align: center;
        padding: 0 1;
    }

    #config-table-container {
        height: 1fr;
        padding: 1;
    }

    #config-footer {
        dock: bottom;
        height: 1;
        background: $surface-darken-1;
        padding: 0 1;
    }

    DataTable {
        height: 1fr;
    }
    """

    def __init__(self, pipelines_dir: Optional[Path] = None, **kwargs):
        super().__init__(**kwargs)
        self.pipelines_dir = pipelines_dir or get_pipelines_dir()
        self.configs: list[PipelineConfig] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="config-modal"):
            yield Static("Pipeline Configurations", id="config-title")
            yield Vertical(id="config-table-container")
            yield Static("N:new  E:edit  R:rename  D:delete  Esc:back  Q:quit", id="config-footer")

    def on_mount(self) -> None:
        """Load configurations and populate table."""
        self._refresh_table()

    def _refresh_table(self) -> None:
        """Refresh the configurations table."""
        self.configs = discover_pipelines(self.pipelines_dir)
        container = self.query_one("#config-table-container", Vertical)

        # Try to update existing table
        try:
            table = self.query_one("#config-table", DataTable)
            table.clear()
            for config in self.configs:
                table.add_row(
                    config.name,
                    f"{config.step_count} steps",
                    config.items_source or "[dim]--[/]"
                )
            table.focus()
            return
        except NoMatches:
            pass

        # First time - create the table
        container.remove_children()

        if not self.configs:
            container.mount(Static("[dim]No configurations found[/]\n"))
            container.mount(Static("Press [N] to create a new configuration"))
        else:
            table = DataTable(id="config-table")
            table.cursor_type = "row"
            table.zebra_stripes = True
            table.add_columns("Name", "Steps", "Items Source")

            for config in self.configs:
                table.add_row(
                    config.name,
                    f"{config.step_count} steps",
                    config.items_source or "[dim]--[/]"
                )

            container.mount(table)
            # Focus the table so Enter key works immediately
            table.focus()

    def action_close(self) -> None:
        """Close the modal."""
        self.dismiss(None)

    def action_quit_app(self) -> None:
        """Quit the application."""
        self.app.exit()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle row selection (Enter key) in the DataTable."""
        # This is triggered when user presses Enter on a row in the focused DataTable
        self.action_edit_config()

    def action_cursor_up(self) -> None:
        try:
            table = self.query_one("#config-table", DataTable)
            table.action_cursor_up()
        except NoMatches:
            pass

    def action_cursor_down(self) -> None:
        try:
            table = self.query_one("#config-table", DataTable)
            table.action_cursor_down()
        except NoMatches:
            pass

    def _get_selected_config(self) -> Optional[PipelineConfig]:
        """Get the currently selected configuration."""
        try:
            table = self.query_one("#config-table", DataTable)
            if table.cursor_row is not None and table.cursor_row < len(self.configs):
                return self.configs[table.cursor_row]
        except NoMatches:
            pass
        return None

    def action_new_config(self) -> None:
        """Create a new configuration."""
        self.app.push_screen(NewConfigModal(self.pipelines_dir), self._on_new_config_result)

    def _on_new_config_result(self, name: Optional[str]) -> None:
        """Handle result from new config modal."""
        if name:
            self._refresh_table()

    def action_edit_config(self) -> None:
        """Edit the selected configuration."""
        config = self._get_selected_config()
        if config:
            # Import here to avoid circular imports
            from .edit_screen import EditConfigScreen
            self.app.push_screen(EditConfigScreen(config), self._on_edit_result)

    def _on_edit_result(self, result: Optional[bool]) -> None:
        """Handle result from edit screen."""
        self._refresh_table()

    def action_rename_config(self) -> None:
        """Rename the selected configuration."""
        config = self._get_selected_config()
        if config:
            self.app.push_screen(RenameConfigModal(config), self._on_rename_result)

    def _on_rename_result(self, new_name: Optional[str]) -> None:
        """Handle result from rename modal."""
        if new_name:
            self._refresh_table()

    def action_delete_config(self) -> None:
        """Delete the selected configuration."""
        config = self._get_selected_config()
        if config:
            self.app.push_screen(DeleteConfigModal(config), self._on_delete_result)

    def _on_delete_result(self, deleted: bool) -> None:
        """Handle result from delete modal."""
        if deleted:
            self._refresh_table()
