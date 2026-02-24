"""
Configuration edit screen for pipeline editor.

Split-panel design with pipeline visualization at top and step details at bottom.
Left/Right arrows navigate between steps, Up/Down navigate properties, C copies step context to clipboard.
"""

import re
from pathlib import Path
from typing import Optional

import yaml
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import Screen, ModalScreen
from textual.widgets import Static, OptionList
from textual.widgets.option_list import Option
from textual.binding import Binding
from textual.reactive import reactive
from textual import events

from .models import PipelineConfig
from .modals import EditStepModal

# Box drawing characters
BOX_H = "─"
BOX_V = "│"
BOX_TL = "┌"
BOX_TR = "┐"
BOX_BL = "└"
BOX_BR = "┘"


class ViewTemplateModal(ModalScreen):
    """Modal for viewing template content (read-only)."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
    ]

    CSS = """
    ViewTemplateModal {
        align: center middle;
    }

    #template-modal {
        width: 80%;
        height: 80%;
        border: solid $primary;
        background: $surface;
    }

    #template-title {
        dock: top;
        height: 1;
        background: $primary;
        padding: 0 1;
    }

    #template-content {
        height: 1fr;
        padding: 1;
    }

    #template-footer {
        dock: bottom;
        height: 1;
        background: $surface-darken-1;
        padding: 0 1;
    }
    """

    def __init__(self, title: str, content: str, **kwargs):
        super().__init__(**kwargs)
        self._title = title
        self._content = content

    def compose(self) -> ComposeResult:
        with Vertical(id="template-modal"):
            yield Static(f"[bold]{self._title}[/]", id="template-title")
            yield VerticalScroll(Static(self._content), id="template-content")
            yield Static("[cyan]Q[/]/[cyan]Esc[/]:close", id="template-footer")

    def action_close(self) -> None:
        self.dismiss()


class EditPropertyModal(ModalScreen):
    """Modal for editing a single property value."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    EditPropertyModal {
        align: center middle;
    }

    #property-modal {
        width: 60%;
        height: auto;
        border: solid $primary;
        background: $surface;
        padding: 1;
    }

    #property-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    #property-input {
        width: 100%;
        margin-bottom: 1;
    }

    #button-row {
        height: auto;
        align: center middle;
    }
    """

    def __init__(self, property_name: str, current_value: str, **kwargs):
        super().__init__(**kwargs)
        self.property_name = property_name
        self.current_value = current_value

    def compose(self) -> ComposeResult:
        from textual.widgets import Input, Button
        from textual.containers import Horizontal

        with Vertical(id="property-modal"):
            yield Static(f"Edit: {self.property_name}", id="property-title")
            yield Input(value=self.current_value, id="property-input")
            with Horizontal(id="button-row"):
                yield Button("Save", variant="primary", id="save-btn")
                yield Button("Cancel", variant="default", id="cancel-btn")

    def on_mount(self) -> None:
        from textual.widgets import Input
        self.query_one("#property-input", Input).focus()

    def on_button_pressed(self, event) -> None:
        from textual.widgets import Input
        if event.button.id == "save-btn":
            value = self.query_one("#property-input", Input).value
            self.dismiss(value)
        else:
            self.dismiss(None)

    def on_input_submitted(self, event) -> None:
        from textual.widgets import Input
        value = self.query_one("#property-input", Input).value
        self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class EditConfigScreen(Screen):
    """Screen for editing a pipeline configuration with split-panel design."""

    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("q", "quit_app", "Quit"),
        Binding("Q", "quit_app", "Quit", show=False),
        Binding("a", "add_step", "Add Step"),
        Binding("e", "edit_step", "Edit Step"),
        Binding("d", "delete_step", "Delete Step"),
        Binding("c", "copy_context", "Copy Context"),
        Binding("v", "view_template", "View Template"),
        Binding("left", "prev_step", "Prev", show=False),
        Binding("right", "next_step", "Next", show=False),
        Binding("s", "save", "Save"),
        Binding("tab", "noop", "", show=False),  # Disable tab
    ]

    CSS = """
    EditConfigScreen {
        layout: grid;
        grid-size: 1;
        grid-rows: 1 auto 1fr 1;
    }

    #edit-header {
        height: 1;
        padding: 0 1;
        background: $primary;
    }

    #pipeline-panel {
        height: auto;
        padding: 1;
        border-bottom: solid $secondary;
    }

    #details-panel {
        height: 1fr;
        padding: 0;
        border-top: heavy $primary;
    }

    #details-list {
        height: 1fr;
        padding: 0;
    }

    OptionList {
        height: 1fr;
        scrollbar-gutter: stable;
    }

    OptionList > .option-list--option-highlighted {
        background: $primary-darken-2;
    }

    #edit-footer {
        height: 1;
        background: $surface-darken-1;
        padding: 0 1;
    }

    .section-title {
        text-style: bold;
        margin-bottom: 1;
    }

    .detail-row {
        margin-bottom: 0;
    }

    .detail-label {
        color: $text-muted;
    }
    """

    # Track selected step with reactive property
    selected_step_index = reactive(0, init=False)
    # Track which panel has focus: "top" or "bottom"
    focus_panel = reactive("top", init=False)

    def __init__(self, config: PipelineConfig, **kwargs):
        super().__init__(**kwargs)
        self.config = config
        self.modified = False
        self._non_run_steps: list[dict] = []
        self._detail_items: list[tuple[str, str, str]] = []  # (key, label, value)

    def compose(self) -> ComposeResult:
        yield Static(f"[bold]Edit Configuration: {self.config.name}[/]", id="edit-header")
        yield Vertical(id="pipeline-panel")
        yield Vertical(id="details-panel")
        yield Static(self._render_footer(), id="edit-footer")

    def _render_footer(self) -> str:
        """Render footer with high-contrast hotkey colors."""
        return (
            "[cyan]←→[/]:step  "
            "[cyan]↑↓[/]:props  "
            "[cyan]A[/]dd  "
            "[cyan]E[/]dit  "
            "[cyan]D[/]elete  "
            "[cyan]C[/]opy  "
            "[cyan]V[/]iew  "
            "[cyan]S[/]ave  "
            "[cyan]Esc[/]:back  "
            "[cyan]Q[/]:quit"
        )

    def on_mount(self) -> None:
        """Initialize the view."""
        self._refresh_steps_list()
        self._update_pipeline_panel()
        self._update_details_panel()

    def on_key(self, event: events.Key) -> None:
        """Intercept keys before widgets handle them."""
        if event.key == "tab":
            event.prevent_default()
            event.stop()
        elif event.key == "left":
            self.action_prev_step()
            event.prevent_default()
            event.stop()
        elif event.key == "right":
            self.action_next_step()
            event.prevent_default()
            event.stop()
        elif event.key == "up":
            self._handle_up()
            event.prevent_default()
            event.stop()
        elif event.key == "down":
            self._handle_down()
            event.prevent_default()
            event.stop()
        elif event.key == "enter":
            self._handle_enter()
            event.prevent_default()
            event.stop()

    def _handle_up(self) -> None:
        """Handle up key - navigate within bottom panel or switch to top."""
        if self.focus_panel == "bottom":
            try:
                option_list = self.query_one("#details-list", OptionList)
                if option_list.highlighted is not None and option_list.highlighted > 0:
                    option_list.action_cursor_up()
                else:
                    # At top of list, switch to pipeline panel
                    self.focus_panel = "top"
                    self._update_panel_focus()
            except NoMatches:
                self.focus_panel = "top"
                self._update_panel_focus()
        # If already in top panel, up does nothing

    def _handle_down(self) -> None:
        """Handle down key - switch to bottom panel or navigate within it."""
        if self.focus_panel == "top":
            # Switch to bottom panel
            self.focus_panel = "bottom"
            self._update_panel_focus()
            try:
                option_list = self.query_one("#details-list", OptionList)
                if option_list.option_count > 0:
                    option_list.highlighted = 0
            except NoMatches:
                pass
        else:
            # Navigate within bottom panel
            try:
                option_list = self.query_one("#details-list", OptionList)
                option_list.action_cursor_down()
            except NoMatches:
                pass

    def _handle_enter(self) -> None:
        """Handle enter key - edit selected property."""
        if self.focus_panel == "bottom" and self._detail_items:
            try:
                option_list = self.query_one("#details-list", OptionList)
                highlighted = option_list.highlighted
                if highlighted is not None and highlighted < len(self._detail_items):
                    key, label, value = self._detail_items[highlighted]
                    if key in ("name", "prompt_template", "description"):
                        self._edit_property(key, label, value)
            except NoMatches:
                pass

    def _edit_property(self, key: str, label: str, value: str) -> None:
        """Open modal to edit a property."""
        self.app.push_screen(
            EditPropertyModal(label, value),
            lambda result: self._on_property_edit_result(key, result)
        )

    def _on_property_edit_result(self, key: str, result: Optional[str]) -> None:
        """Handle property edit result."""
        if result is not None and self._non_run_steps and self.selected_step_index < len(self._non_run_steps):
            step = self._non_run_steps[self.selected_step_index]
            step[key] = result
            self.config.save()
            self.modified = True
            self._refresh_steps_list()
            self._update_pipeline_panel()
            self._update_details_panel()
            self.notify(f"Updated {key}")

    def _update_panel_focus(self) -> None:
        """Update visual state based on which panel has focus."""
        self._update_pipeline_panel()
        self._update_details_panel()

    def _refresh_steps_list(self) -> None:
        """Refresh the list of non-run-scope steps."""
        self._non_run_steps = [s for s in self.config.steps if s.get("scope") != "run"]
        # Ensure selected index is valid
        if self.selected_step_index >= len(self._non_run_steps):
            self.selected_step_index = max(0, len(self._non_run_steps) - 1)

    def watch_selected_step_index(self, new_index: int) -> None:
        """Update panels when selection changes."""
        self._update_pipeline_panel()
        self._update_details_panel()

    def watch_focus_panel(self, new_focus: str) -> None:
        """Update panels when focus changes."""
        self._update_pipeline_panel()
        self._update_details_panel()

    def _update_pipeline_panel(self) -> None:
        """Update the pipeline visualization."""
        try:
            container = self.query_one("#pipeline-panel", Vertical)
        except NoMatches:
            return

        # Add indicator for panel focus
        focus_indicator = "[bold cyan]>[/] " if self.focus_panel == "top" else "  "
        content = self._render_pipeline()
        new_content = f"{focus_indicator}[bold]Pipeline Steps[/]\n{content}"

        # Try to update existing widget instead of removing/recreating
        try:
            pipeline_widget = self.query_one("#pipeline-content", Static)
            pipeline_widget.update(new_content)
        except NoMatches:
            # First time - create the widget
            container.remove_children()
            container.mount(Static(new_content, id="pipeline-content"))

    def _render_pipeline(self) -> str:
        """Render the pipeline as connected boxes."""
        steps = self._non_run_steps
        if not steps:
            return "[dim]No pipeline steps defined. Press A to add a step.[/]"

        selected = self.selected_step_index
        is_focused = self.focus_panel == "top"
        box_width = 15
        ARROW_STR = "────▶"

        def make_box_line(char_left: str, char_fill: str, char_right: str, is_selected: bool) -> str:
            content = char_fill * box_width
            if is_selected and is_focused:
                return f"[bold cyan]{char_left}{content}{char_right}[/]"
            elif is_selected:
                return f"[dim cyan]{char_left}{content}{char_right}[/]"
            return f"{char_left}{content}{char_right}"

        def make_content_line(text: str, char_side: str, is_selected: bool) -> str:
            # Remove markup for length calculation
            plain_text = re.sub(r'\[/?[^\]]+\]', '', text)
            padding = box_width - len(plain_text)
            left_pad = padding // 2
            right_pad = padding - left_pad
            padded = " " * left_pad + text + " " * right_pad
            if is_selected and is_focused:
                return f"[bold cyan]{char_side}[/][reverse]{padded}[/reverse][bold cyan]{char_side}[/]"
            elif is_selected:
                return f"[dim cyan]{char_side}[/]{padded}[dim cyan]{char_side}[/]"
            return f"{char_side}{padded}{char_side}"

        lines = []

        # Row 1: Top borders
        row = []
        for i, step in enumerate(steps):
            row.append(make_box_line(BOX_TL, BOX_H, BOX_TR, i == selected))
            if i < len(steps) - 1:
                row.append("     ")
        lines.append("  " + "".join(row))

        # Row 2: Step names (handle long names)
        step_name_rows = []
        for step in steps:
            name = step.get("name", "").upper()
            # Add loop indicator if step has loop_until
            if step.get("loop_until"):
                name = f"{name} ↻"
            if len(name) <= box_width - 2:
                step_name_rows.append([name])
            else:
                words = name.replace("_", " ").split()
                row1, row2 = [], []
                current = row1
                current_len = 0
                for word in words:
                    if current_len + len(word) + (1 if current else 0) <= box_width - 2:
                        current.append(word)
                        current_len += len(word) + (1 if len(current) > 1 else 0)
                    else:
                        current = row2
                        current.append(word)
                        current_len = len(word)
                step_name_rows.append([" ".join(row1), " ".join(row2)] if row2 else [" ".join(row1)])

        max_name_rows = max(len(r) for r in step_name_rows) if step_name_rows else 1

        for row_idx in range(max_name_rows):
            row = []
            for i, step in enumerate(steps):
                name_parts = step_name_rows[i]
                text = name_parts[row_idx] if row_idx < len(name_parts) else ""
                row.append(make_content_line(text, BOX_V, i == selected))
                if i < len(steps) - 1:
                    if row_idx == max_name_rows // 2:
                        row.append(ARROW_STR)
                    else:
                        row.append("     ")
            lines.append("  " + "".join(row))

        # Row 3: Step number
        row = []
        for i, step in enumerate(steps):
            step_num = f"Step {i + 1}"
            row.append(make_content_line(step_num, BOX_V, i == selected))
            if i < len(steps) - 1:
                row.append("     ")
        lines.append("  " + "".join(row))

        # Row 4: Bottom borders
        row = []
        for i, step in enumerate(steps):
            row.append(make_box_line(BOX_BL, BOX_H, BOX_BR, i == selected))
            if i < len(steps) - 1:
                row.append("     ")
        lines.append("  " + "".join(row))

        return "\n".join(lines)

    def _update_details_panel(self) -> None:
        """Update the step details panel with interactive list."""
        try:
            container = self.query_one("#details-panel", Vertical)
        except NoMatches:
            return

        if not self._non_run_steps:
            container.remove_children()
            container.mount(Static("[dim]No step selected[/]"))
            return

        if self.selected_step_index >= len(self._non_run_steps):
            container.remove_children()
            container.mount(Static("[dim]No step selected[/]"))
            return

        step = self._non_run_steps[self.selected_step_index]

        # Build detail items list
        self._detail_items = []
        name = step.get("name", "")
        template = step.get("prompt_template", "--")
        description = step.get("description", "--")

        self._detail_items.append(("name", "Name", name))
        self._detail_items.append(("prompt_template", "Template", template))
        self._detail_items.append(("description", "Description", description))

        # Add output schema fields (read-only display)
        output_schema = step.get("output_schema", {})
        for field_name, field_def in output_schema.items():
            field_type = field_def.get("type", "unknown")
            field_desc = field_def.get("description", "")
            display = f"{field_name}: {field_type}"
            if field_desc:
                display += f" - {field_desc}"
            self._detail_items.append((f"schema_{field_name}", f"Schema: {field_name}", display))

        # Add validation rules (read-only display)
        validation = step.get("validation", {})
        for field_name, rules in validation.items():
            if isinstance(rules, dict):
                rule_strs = [f"{k}={v}" for k, v in rules.items()]
                display = f"{field_name}: {', '.join(rule_strs)}"
            else:
                display = f"{field_name}: {rules}"
            self._detail_items.append((f"validation_{field_name}", f"Validation: {field_name}", display))

        # Add expression step fields (read-only display)
        if step.get("scope") == "expression":
            # Show init expressions if present
            init_exprs = step.get("init", {})
            if init_exprs:
                init_names = ", ".join(init_exprs.keys())
                self._detail_items.append(("init", "Init", init_names))

            # Show loop condition if present
            loop_until = step.get("loop_until")
            if loop_until:
                self._detail_items.append(("loop_until", "Loop Condition", loop_until))
                max_iter = step.get("max_iterations", 1000)
                self._detail_items.append(("max_iterations", "Max Iterations", str(max_iter)))

            # Show expressions
            expressions = step.get("expressions", {})
            if expressions:
                expr_names = ", ".join(expressions.keys())
                self._detail_items.append(("expressions", "Expressions", expr_names))

        # Focus indicator
        focus_indicator = "[bold cyan]>[/] " if self.focus_panel == "bottom" else "  "

        # Try to update existing widgets instead of removing/recreating
        try:
            title_widget = self.query_one("#details-title", Static)
            title_widget.update(f"{focus_indicator}[bold]Step Details: {name}[/] [dim](Enter to edit, V to view template)[/]")
        except NoMatches:
            # First time - need to create widgets
            container.remove_children()
            title = Static(f"{focus_indicator}[bold]Step Details: {name}[/] [dim](Enter to edit, V to view template)[/]", id="details-title")
            container.mount(title)

            # Create option list
            option_list = OptionList(id="details-list")
            for key, label, value in self._detail_items:
                if key in ("name", "prompt_template", "description"):
                    option_list.add_option(Option(f"[cyan]{label}:[/] {value}", id=key))
                else:
                    option_list.add_option(Option(f"[dim]{label}:[/] {value}", id=key))
            container.mount(option_list)

            # Add config path info
            container.mount(Static(f"\n[dim]Config:[/] {self.config.config_path}", id="details-config-path"))
            return

        # Update existing option list
        try:
            option_list = self.query_one("#details-list", OptionList)
            option_list.clear_options()
            for key, label, value in self._detail_items:
                if key in ("name", "prompt_template", "description"):
                    option_list.add_option(Option(f"[cyan]{label}:[/] {value}", id=key))
                else:
                    option_list.add_option(Option(f"[dim]{label}:[/] {value}", id=key))
        except NoMatches:
            pass

        # Update config path
        try:
            config_path_widget = self.query_one("#details-config-path", Static)
            config_path_widget.update(f"\n[dim]Config:[/] {self.config.config_path}")
        except NoMatches:
            pass

    # --- Navigation ---

    def action_noop(self) -> None:
        """Do nothing - used to disable keys."""
        pass

    def action_prev_step(self) -> None:
        """Move to previous step."""
        if self.selected_step_index > 0:
            self.selected_step_index -= 1
            # Stay in top panel focus when navigating steps
            self.focus_panel = "top"

    def action_next_step(self) -> None:
        """Move to next step."""
        if self.selected_step_index < len(self._non_run_steps) - 1:
            self.selected_step_index += 1
            # Stay in top panel focus when navigating steps
            self.focus_panel = "top"

    def action_go_back(self) -> None:
        """Go back to config list."""
        self.dismiss(self.modified)

    def action_quit_app(self) -> None:
        """Quit the application."""
        self.app.exit()

    # --- Step Management ---

    def action_add_step(self) -> None:
        """Add a new step."""
        self.app.push_screen(EditStepModal(None, self.config), self._on_step_edit_result)

    def action_edit_step(self) -> None:
        """Edit the selected step (full step modal)."""
        if self._non_run_steps and self.selected_step_index < len(self._non_run_steps):
            step = self._non_run_steps[self.selected_step_index]
            self.app.push_screen(EditStepModal(step, self.config), self._on_step_edit_result)

    def _on_step_edit_result(self, result: Optional[dict]) -> None:
        """Handle result from step edit modal."""
        if result:
            self.modified = True
            self._refresh_steps_list()
            self._update_pipeline_panel()
            self._update_details_panel()

    def action_delete_step(self) -> None:
        """Delete the selected step."""
        if not self._non_run_steps or self.selected_step_index >= len(self._non_run_steps):
            return

        step = self._non_run_steps[self.selected_step_index]
        all_steps = self.config.config.get("pipeline", {}).get("steps", [])
        if step in all_steps:
            all_steps.remove(step)
            self.config.save()
            self.modified = True
            self._refresh_steps_list()
            self._update_pipeline_panel()
            self._update_details_panel()
            self.notify(f"Deleted step '{step.get('name')}'")

    def action_save(self) -> None:
        """Save the configuration."""
        self.config.save()
        self.notify("Configuration saved")
        self.modified = False

    # --- View Template ---

    def action_view_template(self) -> None:
        """View the template file in a modal."""
        if not self._non_run_steps or self.selected_step_index >= len(self._non_run_steps):
            self.notify("No step selected", severity="warning")
            return

        step = self._non_run_steps[self.selected_step_index]
        template_name = step.get("prompt_template", "")

        if not template_name:
            self.notify("No template defined for this step", severity="warning")
            return

        template_path = self._find_template_path(template_name)
        if template_path and template_path.exists():
            try:
                content = template_path.read_text()
                title = f"Template: {template_path.name}"
                self.app.push_screen(ViewTemplateModal(title, content))
            except Exception as e:
                self.notify(f"Error reading template: {e}", severity="error")
        else:
            self.notify(f"Template file not found: {template_name}", severity="warning")

    # --- Copy Context ---

    def action_copy_context(self) -> None:
        """Copy step context (YAML config + template) to clipboard."""
        if not self._non_run_steps or self.selected_step_index >= len(self._non_run_steps):
            self.notify("No step selected", severity="warning")
            return

        step = self._non_run_steps[self.selected_step_index]
        step_name = step.get("name", "unknown")

        # Build the context string
        lines = [f"[Context for Step: {step_name}]", ""]

        # YAML config snippet for this step
        lines.append("YAML Config:")
        yaml_snippet = yaml.dump(step, default_flow_style=False, sort_keys=False)
        lines.append(yaml_snippet)

        # Template content
        template_name = step.get("prompt_template", "")
        if template_name:
            template_path = self._find_template_path(template_name)
            if template_path and template_path.exists():
                lines.append(f"Template ({template_path}):")
                try:
                    template_content = template_path.read_text()
                    lines.append(template_content)
                except Exception as e:
                    lines.append(f"[Error reading template: {e}]")
            else:
                lines.append(f"Template ({template_name}):")
                lines.append("[Template file not found]")
        else:
            lines.append("Template: [None defined]")

        context_str = "\n".join(lines)

        # Copy to clipboard
        try:
            import pyperclip
            pyperclip.copy(context_str)
            self.notify("Step context copied to clipboard!")
        except ImportError:
            self.notify("pyperclip not installed - cannot copy to clipboard", severity="warning")
        except Exception as e:
            self.notify(f"Copy failed: {e}", severity="error")

    def _find_template_path(self, template_name: str) -> Optional[Path]:
        """Find the template file path."""
        # Check relative to config directory
        config_dir = self.config.base_dir

        # Try common locations
        candidates = [
            config_dir / template_name,
            config_dir / "templates" / template_name,
            config_dir / f"{template_name}.j2",
            config_dir / "templates" / f"{template_name}.j2",
        ]

        # Also check if template_name already has path
        if "/" in template_name or "\\" in template_name:
            candidates.insert(0, Path(template_name))
            candidates.insert(1, config_dir / template_name)

        for path in candidates:
            if path.exists():
                return path

        return None
