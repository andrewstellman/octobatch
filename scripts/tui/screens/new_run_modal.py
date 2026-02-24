"""
NewRunModal - Launchpad for creating new batch runs.

Modal dialog for selecting a pipeline, configuring run options, and starting
the orchestrator process in the background.
"""

from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static, Button, Input, Select, RadioButton, RadioSet
from textual.binding import Binding
from textual.css.query import NoMatches

from .common import _log
from .run_launcher import start_realtime_run, start_batch_run
from ..utils.pipelines import scan_pipelines
from ..utils.runs import get_runs_dir
from providers.base import LLMProvider


class NewRunModal(ModalScreen):
    """Launchpad modal for creating and starting a new run."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    NewRunModal {
        align: center middle;
    }

    #modal-container {
        width: 70;
        height: auto;
        max-height: 85%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }

    #modal-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    .form-section {
        margin-bottom: 1;
    }

    .form-row {
        height: auto;
        margin-bottom: 1;
    }

    .form-label {
        width: 14;
        padding-top: 1;
    }

    .form-input {
        width: 1fr;
    }

    Select {
        width: 1fr;
    }

    Input {
        width: 1fr;
    }

    RadioSet {
        width: 1fr;
        height: auto;
        layout: horizontal;
        padding: 0;
        border: none;
        background: transparent;
    }

    RadioButton {
        width: auto;
        padding: 0 2 0 0;
        background: transparent;
        border: none;
    }

    #mode-description {
        color: $text-muted;
        padding-left: 14;
        margin-bottom: 1;
    }

    #validation-error {
        color: $error;
        text-align: center;
        height: auto;
        margin-bottom: 1;
    }

    #buttons {
        height: auto;
        align: center middle;
        margin-top: 1;
    }

    Button {
        margin: 0 1;
    }

    #start-btn {
        min-width: 16;
    }

    #footer-hint {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }

    #form-scroll {
        height: 1fr;
        min-height: 5;
    }
    """

    def __init__(self, pipelines: Optional[List[Dict[str, Any]]] = None, **kwargs):
        """Initialize the modal.

        Args:
            pipelines: Optional list of pipeline dicts. If not provided,
                      will be loaded via scan_pipelines().
        """
        super().__init__(**kwargs)
        # Load pipelines if not provided
        self._pipelines = pipelines if pipelines is not None else scan_pipelines()
        self._selected_pipeline: Optional[Dict[str, Any]] = None
        self._runs_dir = get_runs_dir()
        self._scripts_dir = Path(__file__).parent.parent.parent
        # Track the last auto-generated name so we can update it when pipeline changes
        self._auto_generated_name: Optional[str] = None

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container"):
            yield Static("[bold]New Run - Launchpad[/]", id="modal-title")

            with VerticalScroll(id="form-scroll"):
                # Pipeline selector
                with Horizontal(classes="form-row"):
                    yield Static("Pipeline:", classes="form-label")
                    pipeline_options = [
                        (f"{p['name']} ({p['step_count']} steps)", p['name'])
                        for p in self._pipelines
                    ]
                    if pipeline_options:
                        yield Select(
                            pipeline_options,
                            id="pipeline-select",
                            prompt="Select a pipeline...",
                            classes="form-input"
                        )
                    else:
                        yield Static("[dim]No pipelines found[/]", classes="form-input")

                # Run name
                with Horizontal(classes="form-row"):
                    yield Static("Run Name:", classes="form-label")
                    yield Input(
                        placeholder="auto-generated if empty",
                        id="run-name-input",
                        classes="form-input"
                    )

                # Provider selector
                with Horizontal(classes="form-row"):
                    yield Static("Provider:", classes="form-label")
                    provider_options = [
                        ("Use Pipeline Config", "pipeline_default"),
                    ] + [
                        (f"{name.capitalize()} ({info.get('default_model', 'N/A')})", name)
                        for name, info in LLMProvider.get_all_providers().items()
                    ]
                    yield Select(
                        provider_options,
                        id="provider-select",
                        value="pipeline_default",
                        classes="form-input",
                    )

                # Model selector
                with Horizontal(classes="form-row"):
                    yield Static("Model:", classes="form-label")
                    yield Select(
                        [("Use Pipeline Config", "pipeline_default")],
                        id="model-select",
                        value="pipeline_default",
                        classes="form-input",
                        disabled=True,
                    )

                # Max Units (before Mode - most important fields at top)
                with Horizontal(classes="form-row"):
                    yield Static("Max Units:", classes="form-label")
                    yield Input(
                        placeholder="optional - leave blank for all units",
                        id="max-units-input",
                        classes="form-input"
                    )

                # Repeat count override
                with Horizontal(classes="form-row"):
                    yield Static("Repeat Count:", classes="form-label")
                    yield Input(
                        placeholder="Config default",
                        id="repeat-input",
                        classes="form-input"
                    )

                # Mode selector
                with Horizontal(classes="form-row"):
                    yield Static("Mode:", classes="form-label")
                    with RadioSet(id="mode-radio"):
                        yield RadioButton("Batch", id="mode-batch", value=True)
                        yield RadioButton("Realtime", id="mode-realtime")

                yield Static(
                    "[dim]Batch: Uses provider's batch API (cheaper, slower)[/]",
                    id="mode-description"
                )

                # Validation error display
                yield Static("", id="validation-error")

            # Buttons OUTSIDE scroll - always visible
            with Horizontal(id="buttons"):
                yield Button("Start Run", variant="primary", id="start-btn", disabled=True)
                yield Button("Cancel", variant="default", id="cancel-btn")

            yield Static("[cyan]Esc[/]:cancel", id="footer-hint")

    def on_mount(self) -> None:
        """Set up initial state."""
        # Don't auto-select or auto-generate name - wait for user to select a pipeline
        self._update_start_button()

    def on_select_changed(self, event: Select.Changed) -> None:
        """Handle pipeline or provider selection change."""
        if event.select.id == "pipeline-select":
            selected_name = event.value
            if selected_name != Select.BLANK:
                # Find the selected pipeline
                for p in self._pipelines:
                    if p["name"] == selected_name:
                        self._selected_pipeline = p
                        break
                self._generate_default_run_name()
                self._update_start_button()
                self._clear_error()
                # Sync provider/model from pipeline config
                self._sync_provider_from_pipeline()

        elif event.select.id == "provider-select":
            selected_provider = event.value
            if selected_provider == "pipeline_default":
                # Reset model to pipeline default and disable
                try:
                    model_select = self.query_one("#model-select", Select)
                    model_select.set_options([("Use Pipeline Config", "pipeline_default")])
                    model_select.value = "pipeline_default"
                    model_select.disabled = True
                except NoMatches:
                    pass
            elif selected_provider != Select.BLANK:
                try:
                    model_select = self.query_one("#model-select", Select)
                    model_select.disabled = False
                except NoMatches:
                    pass
                self._populate_models(selected_provider)

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        """Handle mode selection change."""
        if event.radio_set.id == "mode-radio":
            try:
                desc = self.query_one("#mode-description", Static)
                if event.pressed.id == "mode-batch":
                    desc.update("[dim]Batch: Uses provider's batch API (cheaper, slower)[/]")
                else:
                    desc.update("[dim]Realtime: Direct API calls (faster, ~2x cost)[/]")
            except NoMatches:
                pass

    def on_input_changed(self, event: Input.Changed) -> None:
        """Handle input changes."""
        if event.input.id == "run-name-input":
            self._update_start_button()
            self._clear_error()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button clicks."""
        if event.button.id == "start-btn":
            self._start_run()
        elif event.button.id == "cancel-btn":
            self.dismiss(None)

    def action_cancel(self) -> None:
        """Cancel and close modal."""
        self.dismiss(None)

    def _generate_default_run_name(self) -> None:
        """Generate a default run name based on pipeline and timestamp.

        Only generates if:
        - The field is empty, OR
        - The current value matches the last auto-generated name (user hasn't edited it)
        """
        if not self._selected_pipeline:
            return

        try:
            run_name_input = self.query_one("#run-name-input", Input)
            current_value = run_name_input.value.strip()

            # Generate if empty OR if current value is the previous auto-generated name
            should_generate = (
                not current_value or
                current_value == self._auto_generated_name
            )

            if should_generate:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                pipeline_name = self._selected_pipeline["name"]
                # Clean pipeline name for use in directory name
                clean_name = pipeline_name.replace(" ", "_").replace("-", "_").lower()
                new_name = f"{clean_name}_{timestamp}"
                run_name_input.value = new_name
                self._auto_generated_name = new_name
        except NoMatches:
            pass

    def _update_start_button(self) -> None:
        """Enable/disable start button based on validation."""
        try:
            start_btn = self.query_one("#start-btn", Button)
            start_btn.disabled = not self._is_valid()
        except NoMatches:
            pass

    def _is_valid(self) -> bool:
        """Check if the current form state is valid for starting a run."""
        # Must have a pipeline selected
        if not self._selected_pipeline:
            return False

        # Run name must not be empty (either user-provided or auto-generated)
        try:
            run_name_input = self.query_one("#run-name-input", Input)
            run_name = run_name_input.value.strip()
            if not run_name:
                return False
        except NoMatches:
            return False

        return True

    def _validate_run_name(self) -> Optional[str]:
        """Validate run name and return error message if invalid."""
        try:
            run_name_input = self.query_one("#run-name-input", Input)
            run_name = run_name_input.value.strip()

            if not run_name:
                return "Run name is required"

            # Check for invalid characters
            if not all(c.isalnum() or c in "_-" for c in run_name):
                return "Run name can only contain letters, numbers, underscores, and hyphens"

            # Check if run already exists
            run_dir = self._runs_dir / run_name
            if run_dir.exists():
                return f"Run '{run_name}' already exists"

            return None
        except NoMatches:
            return "Internal error"

    def _show_error(self, message: str) -> None:
        """Display an error message."""
        try:
            error_widget = self.query_one("#validation-error", Static)
            error_widget.update(f"[red]{message}[/]")
        except NoMatches:
            pass

    def _clear_error(self) -> None:
        """Clear any error message."""
        try:
            error_widget = self.query_one("#validation-error", Static)
            error_widget.update("")
        except NoMatches:
            pass

    def _get_mode(self) -> str:
        """Get the selected mode ('batch' or 'realtime')."""
        try:
            radio_set = self.query_one("#mode-radio", RadioSet)
            # Check which radio button is pressed
            if radio_set.pressed_button and radio_set.pressed_button.id == "mode-realtime":
                return "realtime"
        except NoMatches:
            pass
        return "batch"

    def _get_max_units(self) -> Optional[int]:
        """Get the max units value, or None if not specified."""
        try:
            max_units_input = self.query_one("#max-units-input", Input)
            max_units_str = max_units_input.value.strip()
            if max_units_str:
                return int(max_units_str)
        except (NoMatches, ValueError):
            pass
        return None

    def _get_repeat(self) -> Optional[int]:
        """Get the repeat value, or None if not specified."""
        try:
            repeat_input = self.query_one("#repeat-input", Input)
            repeat_str = repeat_input.value.strip()
            if repeat_str:
                return int(repeat_str)
        except (NoMatches, ValueError):
            pass
        return None

    def _get_provider(self) -> Optional[str]:
        """Get the selected provider, or None if not selected."""
        try:
            provider_select = self.query_one("#provider-select", Select)
            value = provider_select.value
            if value != Select.BLANK:
                return value
        except NoMatches:
            pass
        return None

    def _get_model(self) -> Optional[str]:
        """Get the selected model, or None if not selected."""
        try:
            model_select = self.query_one("#model-select", Select)
            value = model_select.value
            if value != Select.BLANK:
                return value
        except NoMatches:
            pass
        return None

    def _populate_models(self, provider_name: str, preselect_model: str = None) -> None:
        """Populate model dropdown based on selected provider.

        Args:
            provider_name: Provider to get models for
            preselect_model: Specific model to pre-select (e.g. from pipeline config).
                             If None, selects the cheapest model.
        """
        try:
            model_select = self.query_one("#model-select", Select)
            models = LLMProvider.get_provider_models(provider_name)

            # Sort models by total cost (input + output per million), cheapest first
            sorted_models = sorted(
                models.items(),
                key=lambda item: (
                    item[1].get("input_per_million", 999) +
                    item[1].get("output_per_million", 999)
                )
            )

            model_options = [
                (f"{info['display_name']} (${info['input_per_million']}/{info['output_per_million']})", model_id)
                for model_id, info in sorted_models
            ]

            model_select.set_options(model_options)

            # Pre-select: explicit model if given, otherwise cheapest
            if preselect_model and preselect_model in models:
                model_select.value = preselect_model
            elif sorted_models:
                model_select.value = sorted_models[0][0]  # cheapest model ID
        except NoMatches:
            pass

    def _sync_provider_from_pipeline(self) -> None:
        """Reset provider/model dropdowns to 'Use Pipeline Config' when pipeline changes.

        The pipeline's own config.yaml (and per-step overrides) will be used by the
        orchestrator. The TUI should not force CLI --provider/--model flags unless
        the user explicitly selects a specific provider.
        """
        if not self._selected_pipeline:
            return

        try:
            provider_select = self.query_one("#provider-select", Select)
            provider_select.value = "pipeline_default"
            model_select = self.query_one("#model-select", Select)
            model_select.set_options([("Use Pipeline Config", "pipeline_default")])
            model_select.value = "pipeline_default"
            model_select.disabled = True
        except NoMatches:
            pass

    def _start_run(self) -> None:
        """Validate and start the orchestrator process.

        Uses a two-step process:
        1. Initialize the run (blocking) - creates run directory and manifest
        2. Start execution (background) - either realtime or watch mode

        For realtime mode, --init --realtime can be combined in one step.
        For batch mode, --init and --watch must be separate (mutually exclusive).
        """
        # Final validation
        error = self._validate_run_name()
        if error:
            self._show_error(error)
            return

        if not self._selected_pipeline:
            self._show_error("Please select a pipeline")
            return

        try:
            run_name_input = self.query_one("#run-name-input", Input)
            run_name = run_name_input.value.strip()
        except NoMatches:
            self._show_error("Internal error")
            return

        pipeline_name = self._selected_pipeline["name"]
        mode = self._get_mode()
        max_units = self._get_max_units()
        repeat = self._get_repeat()
        provider = self._get_provider()
        model = self._get_model()

        # "pipeline_default" means don't pass CLI flags — let config.yaml control
        if provider == "pipeline_default":
            provider = None
            model = None
        elif model == "pipeline_default":
            model = None

        # Validate repeat if provided
        if repeat is not None and repeat < 1:
            self._show_error("Repeat count must be a positive integer")
            return

        run_dir = self._runs_dir / run_name
        orchestrate_path = self._scripts_dir / "orchestrate.py"

        _log.debug(f"Max units from input: {max_units} (type: {type(max_units).__name__ if max_units is not None else 'None'})")
        _log.debug(f"Starting run: pipeline={pipeline_name}, mode={mode}, max_units={max_units}, repeat={repeat}")

        if mode == "realtime":
            # Realtime mode: --init --realtime can be combined
            success, error = start_realtime_run(
                orchestrate_path, pipeline_name, run_dir, max_units,
                provider=provider, model=model, repeat=repeat
            )
        else:
            # Batch mode: need two-step process (--init then --watch)
            success, error = start_batch_run(
                orchestrate_path, pipeline_name, run_dir, max_units,
                provider=provider, model=model, repeat=repeat
            )

        if error:
            self._show_error(error)

        if success:
            result = {
                "run_name": run_name,
                "run_dir": run_dir,
                "pipeline": pipeline_name,
                "mode": mode,
                "max_units": max_units,
                "repeat": repeat,
                "provider": provider,
                "model": model,
            }
            self.dismiss(result)
