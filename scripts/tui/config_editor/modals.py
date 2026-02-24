"""
Modal dialogs for pipeline configuration editor.

Contains modals for creating, renaming, deleting configs and editing steps.
"""

from pathlib import Path
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Static, Input, Button, Label
from textual.binding import Binding

from .models import PipelineConfig, save_yaml


class NewConfigModal(ModalScreen):
    """Modal for creating a new configuration."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    NewConfigModal {
        align: center middle;
    }

    #new-config-modal {
        width: 50%;
        height: auto;
        border: solid $primary;
        background: $surface;
        padding: 1;
    }

    #new-config-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    .form-row {
        height: auto;
        margin-bottom: 1;
    }

    .form-label {
        width: 15;
    }

    Input {
        width: 1fr;
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

    def __init__(self, pipelines_dir: Path, **kwargs):
        super().__init__(**kwargs)
        self.pipelines_dir = pipelines_dir

    def compose(self) -> ComposeResult:
        with Vertical(id="new-config-modal"):
            yield Static("New Pipeline Configuration", id="new-config-title")
            with Horizontal(classes="form-row"):
                yield Label("Name:", classes="form-label")
                yield Input(placeholder="my_pipeline", id="name-input")
            with Horizontal(id="button-row"):
                yield Button("Create", variant="primary", id="create-btn")
                yield Button("Cancel", variant="default", id="cancel-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "create-btn":
            self._create_config()
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._create_config()

    def _create_config(self) -> None:
        """Create the new configuration."""
        name_input = self.query_one("#name-input", Input)
        name = name_input.value.strip()

        if not name:
            self.notify("Please enter a name", severity="error")
            return

        # Validate name (alphanumeric and underscores only)
        if not all(c.isalnum() or c == '_' for c in name):
            self.notify("Name must be alphanumeric with underscores only", severity="error")
            return

        # Check if already exists
        config_dir = self.pipelines_dir / name
        if config_dir.exists():
            self.notify(f"Configuration '{name}' already exists", severity="error")
            return

        # Create directory structure
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "templates").mkdir(exist_ok=True)
        (config_dir / "schemas").mkdir(exist_ok=True)

        # Create skeleton config
        skeleton_config = {
            "pipeline": {
                "steps": [
                    {
                        "name": "generate",
                        "description": "Generate content",
                        "prompt_template": "generate.jinja2"
                    }
                ]
            },
            "api": {
                "provider": "gemini",
                "model": "gemini-2.0-flash-001"
            },
            "processing": {
                "chunk_size": 100,
                "items": {
                    "source": "items.yaml",
                    "key": "items"
                }
            },
            "prompts": {
                "template_dir": "templates"
            },
            "schemas": {
                "schema_dir": "schemas"
            }
        }

        save_yaml(config_dir / "config.yaml", skeleton_config)

        # Create empty items file
        save_yaml(config_dir / "items.yaml", {"items": []})

        # Create template stub
        template_path = config_dir / "templates" / "generate.jinja2"
        template_path.write_text("""# Generation Template
#
# Available variables:
# - {{ item }}: The current item being processed
# - {{ position }}: Position information if applicable
#
# Write your prompt here:

Generate content for: {{ item }}
""")

        self.notify(f"Created configuration '{name}'")
        self.dismiss(name)

    def action_cancel(self) -> None:
        self.dismiss(None)


class RenameConfigModal(ModalScreen):
    """Modal for renaming a configuration."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    RenameConfigModal {
        align: center middle;
    }

    #rename-modal {
        width: 50%;
        height: auto;
        border: solid $primary;
        background: $surface;
        padding: 1;
    }

    #rename-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    .form-row {
        height: auto;
        margin-bottom: 1;
    }

    .form-label {
        width: 15;
    }

    Input {
        width: 1fr;
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

    def __init__(self, config: PipelineConfig, **kwargs):
        super().__init__(**kwargs)
        self.config = config

    def compose(self) -> ComposeResult:
        with Vertical(id="rename-modal"):
            yield Static(f"Rename Configuration: {self.config.name}", id="rename-title")
            with Horizontal(classes="form-row"):
                yield Label("New name:", classes="form-label")
                yield Input(value=self.config.name, id="name-input")
            with Horizontal(id="button-row"):
                yield Button("Rename", variant="primary", id="rename-btn")
                yield Button("Cancel", variant="default", id="cancel-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "rename-btn":
            self._rename_config()
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._rename_config()

    def _rename_config(self) -> None:
        """Rename the configuration."""
        name_input = self.query_one("#name-input", Input)
        new_name = name_input.value.strip()

        if not new_name or new_name == self.config.name:
            self.dismiss(None)
            return

        # Validate name
        if not all(c.isalnum() or c == '_' for c in new_name):
            self.notify("Name must be alphanumeric with underscores only", severity="error")
            return

        # Check if already exists
        new_dir = self.config.base_dir.parent / new_name
        if new_dir.exists():
            self.notify(f"Configuration '{new_name}' already exists", severity="error")
            return

        # Rename directory
        import shutil
        shutil.move(str(self.config.base_dir), str(new_dir))

        self.notify(f"Renamed to '{new_name}'")
        self.dismiss(new_name)

    def action_cancel(self) -> None:
        self.dismiss(None)


class DeleteConfigModal(ModalScreen):
    """Modal for confirming configuration deletion."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("y", "confirm", "Yes"),
        Binding("n", "cancel", "No"),
    ]

    CSS = """
    DeleteConfigModal {
        align: center middle;
    }

    #delete-modal {
        width: 50%;
        height: auto;
        border: solid $error;
        background: $surface;
        padding: 1;
    }

    #delete-title {
        text-align: center;
        text-style: bold;
        color: $error;
        margin-bottom: 1;
    }

    #delete-message {
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

    def __init__(self, config: PipelineConfig, **kwargs):
        super().__init__(**kwargs)
        self.config = config

    def compose(self) -> ComposeResult:
        with Vertical(id="delete-modal"):
            yield Static("Delete Configuration", id="delete-title")
            yield Static(
                f"Are you sure you want to delete '{self.config.name}'?\n"
                "This will remove all files in the configuration directory.",
                id="delete-message"
            )
            with Horizontal(id="button-row"):
                yield Button("[Y]es, Delete", variant="error", id="delete-btn")
                yield Button("[N]o, Cancel", variant="default", id="cancel-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "delete-btn":
            self._delete_config()
        else:
            self.dismiss(False)

    def _delete_config(self) -> None:
        """Delete the configuration."""
        import shutil
        shutil.rmtree(str(self.config.base_dir))
        self.notify(f"Deleted '{self.config.name}'")
        self.dismiss(True)

    def action_confirm(self) -> None:
        self._delete_config()

    def action_cancel(self) -> None:
        self.dismiss(False)


class EditStepModal(ModalScreen):
    """Modal for editing a pipeline step."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    EditStepModal {
        align: center middle;
    }

    #step-modal {
        width: 60%;
        height: auto;
        border: solid $primary;
        background: $surface;
        padding: 1;
    }

    #step-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    .form-row {
        height: auto;
        margin-bottom: 1;
    }

    .form-label {
        width: 15;
    }

    Input {
        width: 1fr;
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

    def __init__(self, step: Optional[dict], config: PipelineConfig, **kwargs):
        super().__init__(**kwargs)
        self.step = step
        self.config = config
        self.is_new = step is None

    def compose(self) -> ComposeResult:
        title = "Add Step" if self.is_new else f"Edit Step: {self.step.get('name', '')}"
        with Vertical(id="step-modal"):
            yield Static(title, id="step-title")
            with Horizontal(classes="form-row"):
                yield Label("Step name:", classes="form-label")
                yield Input(
                    value="" if self.is_new else self.step.get("name", ""),
                    placeholder="generate",
                    id="name-input"
                )
            with Horizontal(classes="form-row"):
                yield Label("Template:", classes="form-label")
                yield Input(
                    value="" if self.is_new else self.step.get("prompt_template", ""),
                    placeholder="generate.jinja2",
                    id="template-input"
                )
            with Horizontal(classes="form-row"):
                yield Label("Description:", classes="form-label")
                yield Input(
                    value="" if self.is_new else self.step.get("description", ""),
                    placeholder="Generate content",
                    id="desc-input"
                )
            with Horizontal(id="button-row"):
                yield Button("Save", variant="primary", id="save-btn")
                yield Button("Cancel", variant="default", id="cancel-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            self._save_step()
        else:
            self.dismiss(None)

    def _save_step(self) -> None:
        """Save the step."""
        name = self.query_one("#name-input", Input).value.strip()
        template = self.query_one("#template-input", Input).value.strip()
        desc = self.query_one("#desc-input", Input).value.strip()

        if not name:
            self.notify("Step name is required", severity="error")
            return

        step_data = {
            "name": name,
            "prompt_template": template,
            "description": desc
        }

        steps = self.config.config.get("pipeline", {}).get("steps", [])

        if self.is_new:
            # Add new step (before run-scope steps)
            run_scope_idx = next((i for i, s in enumerate(steps) if s.get("scope") == "run"), len(steps))
            steps.insert(run_scope_idx, step_data)
        else:
            # Update existing step
            for i, s in enumerate(steps):
                if s.get("name") == self.step.get("name"):
                    steps[i] = step_data
                    break

        self.config.save()
        self.notify(f"{'Added' if self.is_new else 'Updated'} step '{name}'")
        self.dismiss(step_data)

    def action_cancel(self) -> None:
        self.dismiss(None)
