"""
OttoOrchestrator - Bridges pipeline events to Otto animations.

Translates chunk state transitions into OttoWidget animation calls.
Also manages a narrative status label that describes what Otto is doing.
"""

from __future__ import annotations

import random

from textual.widgets import Label

from .otto_widget import OttoWidget, INNER_HAPPY, INNER_SLEEPY, INNER_DEAD


# Color pool for consistent per-run coloring
_COLOR_POOL = [
    "bright_red",
    "bright_blue",
    "bright_green",
    "bright_yellow",
    "bright_magenta",
    "bright_cyan",
]

# Provider name → friendly "Waiting for..." message
_PROVIDER_LABELS = {
    "gemini": "Waiting for Gemini...",
    "google": "Waiting for Gemini...",
    "anthropic": "Waiting for Claude...",
    "openai": "Waiting for OpenAI...",
}

DEFAULT_NARRATIVE = "Otto is waiting for his next job"


class OttoOrchestrator:
    """Adapter that fires Otto animations in response to pipeline events.

    Each method is fire-and-forget. OttoWidget handles concurrent
    animations internally via its transfer queue.
    """

    def __init__(self, widget: OttoWidget, status_label: Label | None = None) -> None:
        self._widget = widget
        self._status_label = status_label
        self._color_map: dict[str, str] = {}
        self._color_index: int = 0
        self._last_narrative: str = ""

    def _color_for_run(self, run_id: str) -> str:
        """Return a consistent color for a given run_id."""
        if run_id not in self._color_map:
            self._color_map[run_id] = _COLOR_POOL[self._color_index % len(_COLOR_POOL)]
            self._color_index += 1
        return self._color_map[run_id]

    def on_chunk_advance(self, run_id: str) -> None:
        """A chunk advanced forward in the pipeline."""
        color = self._color_for_run(run_id)
        from_tip = random.randint(1, 4)
        to_tip = random.randint(from_tip + 1, 6)
        self._widget.start_transfer(from_tip=from_tip, to_tip=to_tip, color=color)

    def on_chunk_retry(self, run_id: str) -> None:
        """A chunk was retried (backward transfer)."""
        color = self._color_for_run(run_id)
        from_tip = random.randint(3, 6)
        to_tip = random.randint(1, from_tip - 1)
        self._widget.start_transfer(from_tip=from_tip, to_tip=to_tip, color=color)

    def on_chunk_complete(self, run_id: str) -> None:
        """A chunk reached VALIDATED (full sweep)."""
        color = self._color_for_run(run_id)
        self._widget.start_transfer(from_tip=1, to_tip=6, color=color)

    def on_run_complete(self, run_id: str) -> None:
        """The entire run completed — wave the flag."""
        self._widget.trigger_flag()

    def _set_narrative(self, text: str) -> None:
        """Update the status label if text changed."""
        if text != self._last_narrative and self._status_label is not None:
            self._last_narrative = text
            self._status_label.update(text)

    def update_narrative(
        self,
        manifest_status: str,
        providers: set[str] | None = None,
        context: dict | None = None,
    ) -> None:
        """Update the narrative label and Otto's mood based on run state.

        Args:
            manifest_status: Current manifest status ("running", "complete", "failed", etc.)
                             Also accepts synthetic statuses like "zombie" from the TUI.
            providers: Set of provider names for currently active steps (e.g. {"gemini"}).
            context: Optional dict with extra info (e.g. {"failed_step": "score_coherence", "failure_count": 3}).
        """
        ctx = context or {}

        if manifest_status == "complete":
            failure_count = ctx.get("failure_count", 0)
            if failure_count:
                self._set_narrative(f"Done — {failure_count} validation failure{'s' if failure_count != 1 else ''}. Press R to retry.")
                self._widget.set_mood(None)
            else:
                self._set_narrative("All done! Everything passed.")
                self._widget.trigger_flag()
                self._widget.set_mood(INNER_HAPPY)
            return

        if manifest_status == "failed":
            failed_step = ctx.get("failed_step")
            failure_count = ctx.get("failure_count", 0)
            if failed_step and failure_count:
                self._set_narrative(f"Stopped at {failed_step} — all units failed validation.")
            else:
                self._set_narrative("Run failed. Check logs (L) for details.")
            self._widget.set_mood(INNER_DEAD)
            return

        if manifest_status == "paused":
            self._set_narrative("Run paused. Press R to resume.")
            self._widget.set_mood(INNER_SLEEPY)
            return

        if manifest_status not in ("running", ""):
            # Zombie, detached, or unknown status
            self._set_narrative("Process lost. Press R to resume.")
            self._widget.set_mood(INNER_DEAD)
            return

        if manifest_status != "running":
            self._set_narrative(DEFAULT_NARRATIVE)
            self._widget.set_mood(None)
            return

        # Running — clear any terminal mood and check providers
        self._widget.set_mood(None)

        if not providers:
            self._set_narrative("Otto is orchestrating...")
            return

        # Normalize to lowercase
        providers = {p.lower() for p in providers}

        # Map each provider to its label
        labels = set()
        for p in providers:
            label = _PROVIDER_LABELS.get(p)
            if label:
                labels.add(label)

        if len(labels) == 1:
            self._set_narrative(labels.pop())
        elif len(labels) > 1:
            self._set_narrative("Otto is orchestrating...")
        else:
            # Unknown provider(s)
            self._set_narrative("Otto is orchestrating...")
