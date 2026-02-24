"""
Automated TUI tests using Textual's run_test() framework.

Tests that TUI screens load without crashing and basic interactions work.
"""

import sys
from pathlib import Path

import pytest

# Add scripts directory to path so tui package is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


@pytest.fixture
def any_run_dir():
    """Find an existing run directory for testing, if any."""
    runs_dir = Path(__file__).parent.parent / "runs"
    if not runs_dir.exists():
        return None
    for d in sorted(runs_dir.iterdir()):
        if d.is_dir() and (d / "MANIFEST.json").exists():
            return d
    return None


class TestHomeScreen:
    """Tests for the HomeScreen."""

    @pytest.mark.asyncio
    async def test_home_screen_loads(self):
        """HomeScreen loads without crashing."""
        from tui.app import OctobatchApp

        app = OctobatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            # Dismiss splash screen
            await pilot.press("escape")
            await pilot.pause()

            # HomeScreen should be on the screen stack
            from tui.screens import HomeScreen
            screens = [type(s).__name__ for s in app.screen_stack]
            assert "HomeScreen" in screens

    @pytest.mark.asyncio
    async def test_data_table_exists(self):
        """DataTable widget is present on HomeScreen."""
        from tui.app import OctobatchApp
        from textual.widgets import DataTable

        app = OctobatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("escape")
            await pilot.pause()
            # Allow background scan to complete
            await pilot.pause(delay=2.0)

            # DataTable should exist somewhere in the widget tree
            tables = app.screen.query(DataTable)
            assert len(tables) > 0, "Expected at least one DataTable on HomeScreen"

    @pytest.mark.asyncio
    async def test_data_table_populates(self, any_run_dir):
        """DataTable populates with runs if any exist."""
        from tui.app import OctobatchApp
        from textual.widgets import DataTable

        app = OctobatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("escape")
            # Wait for background scan
            await pilot.pause(delay=3.0)

            tables = app.screen.query(DataTable)
            if not tables:
                pytest.skip("No DataTable found")

            table = tables.first()
            if any_run_dir is not None:
                # If runs exist, the table should have rows
                assert table.row_count > 0, "Expected rows in DataTable when runs/ has entries"
            # If no runs, row_count == 0 is acceptable

    @pytest.mark.asyncio
    async def test_enter_opens_main_screen(self, any_run_dir):
        """Pressing Enter on a run opens MainScreen."""
        if any_run_dir is None:
            pytest.skip("No runs available for testing")

        from tui.app import OctobatchApp
        from textual.widgets import DataTable

        app = OctobatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("escape")
            await pilot.pause(delay=3.0)

            tables = app.screen.query(DataTable)
            if not tables or tables.first().row_count == 0:
                pytest.skip("No runs in DataTable")

            # Press Enter to open detail view
            await pilot.press("enter")
            await pilot.pause(delay=1.0)

            from tui.screens import MainScreen
            screens = [type(s).__name__ for s in app.screen_stack]
            assert "MainScreen" in screens, f"Expected MainScreen after Enter, got: {screens}"

    @pytest.mark.asyncio
    async def test_escape_returns_to_home(self, any_run_dir):
        """Pressing Escape from MainScreen returns to HomeScreen."""
        if any_run_dir is None:
            pytest.skip("No runs available for testing")

        from tui.app import OctobatchApp
        from textual.widgets import DataTable

        app = OctobatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("escape")
            await pilot.pause(delay=3.0)

            tables = app.screen.query(DataTable)
            if not tables or tables.first().row_count == 0:
                pytest.skip("No runs in DataTable")

            # Enter detail view
            await pilot.press("enter")
            await pilot.pause(delay=1.0)

            # Escape back
            await pilot.press("escape")
            await pilot.pause(delay=0.5)

            from tui.screens import HomeScreen
            assert isinstance(app.screen, HomeScreen), \
                f"Expected HomeScreen after Escape, got: {type(app.screen).__name__}"


class TestDumpMode:
    """Tests for the --dump CLI mode (no Textual dependency)."""

    def test_dump_home_text(self, capsys):
        """--dump produces text output."""
        from tui_dump import dump_home
        code = dump_home(as_json=False)
        assert code == 0
        captured = capsys.readouterr()
        # Should have header line
        assert "Run" in captured.out
        assert "Status" in captured.out

    def test_dump_home_json(self, capsys):
        """--dump --json produces valid JSON."""
        from tui_dump import dump_home
        import json as json_mod
        code = dump_home(as_json=True)
        assert code == 0
        captured = capsys.readouterr()
        data = json_mod.loads(captured.out)
        assert isinstance(data, list)

    def test_dump_run_text(self, capsys, any_run_dir):
        """--dump --run-dir produces text output for a run."""
        if any_run_dir is None:
            pytest.skip("No runs available")
        from tui_dump import dump_run
        code = dump_run(any_run_dir, as_json=False)
        assert code == 0
        captured = capsys.readouterr()
        assert "Run:" in captured.out
        assert "Status:" in captured.out
        assert "Pipeline Steps:" in captured.out

    def test_dump_run_json(self, capsys, any_run_dir):
        """--dump --run-dir --json produces valid JSON for a run."""
        if any_run_dir is None:
            pytest.skip("No runs available")
        from tui_dump import dump_run
        import json as json_mod
        code = dump_run(any_run_dir, as_json=True)
        assert code == 0
        captured = capsys.readouterr()
        data = json_mod.loads(captured.out)
        assert "name" in data
        assert "status" in data
        assert "steps" in data
        assert isinstance(data["steps"], list)

    def test_dump_run_nonexistent(self, capsys):
        """--dump --run-dir with nonexistent dir returns error."""
        from tui_dump import dump_run
        fake_dir = Path("/tmp/nonexistent_octobatch_run")
        code = dump_run(fake_dir, as_json=False)
        assert code == 1


class TestStepState:
    """Tests for step state determination in terminal runs."""

    def test_completed_with_failures_shows_complete(self):
        """Steps show 'complete' not 'running' when run is terminal but chunk failed count is 0."""
        from tui.data import load_run_data

        # Find a completed run with failures (Blackjack OpenAI or Anthropic)
        runs_dir = Path(__file__).parent.parent / "runs"
        target = None
        for name in ["blackjack_9hands_openai", "blackjack_9hands_anthropic"]:
            candidate = runs_dir / name
            if candidate.exists() and (candidate / "MANIFEST.json").exists():
                import json
                manifest = json.loads((candidate / "MANIFEST.json").read_text())
                if manifest.get("status") == "complete":
                    chunks = manifest.get("chunks", {})
                    total_valid = sum(c.get("valid", 0) for c in chunks.values())
                    total_items = sum(c.get("items", 0) for c in chunks.values())
                    if total_valid < total_items:
                        target = candidate
                        break

        if target is None:
            pytest.skip("No completed-with-failures Blackjack run found")

        run_data = load_run_data(target)
        for step in run_data.steps:
            assert step.state == "complete", \
                f"Step '{step.name}' shows '{step.state}' but run is terminal — expected 'complete'"


class TestGenerateUnits:
    """Tests for generate_units.py interleaving fix."""

    def test_interleaved_repetition(self):
        """With repeat > 1, units should be interleaved across items."""
        from generate_units import generate_units

        config = {
            "processing": {
                "strategy": "direct",
                "repeat": 3,
                "items": {
                    "source": "test.yaml",
                    "key": "items",
                    "name_field": "id",
                },
            }
        }
        items_data = {
            "items": [
                {"id": "alpha"},
                {"id": "beta"},
                {"id": "gamma"},
            ]
        }

        units = generate_units(config, items_data)
        assert len(units) == 9  # 3 items x 3 reps

        # First 3 units should be rep 0 of each item (interleaved)
        assert units[0]["unit_id"] == "alpha__rep0000"
        assert units[1]["unit_id"] == "beta__rep0000"
        assert units[2]["unit_id"] == "gamma__rep0000"

        # Next 3 should be rep 1
        assert units[3]["unit_id"] == "alpha__rep0001"
        assert units[4]["unit_id"] == "beta__rep0001"
        assert units[5]["unit_id"] == "gamma__rep0001"

        # Last 3 should be rep 2
        assert units[6]["unit_id"] == "alpha__rep0002"
        assert units[7]["unit_id"] == "beta__rep0002"
        assert units[8]["unit_id"] == "gamma__rep0002"

    def test_max_units_representative_sample(self):
        """--max-units with interleaving gives representative sample."""
        from generate_units import generate_units

        config = {
            "processing": {
                "strategy": "direct",
                "repeat": 100,
                "items": {
                    "source": "test.yaml",
                    "key": "items",
                    "name_field": "id",
                },
            }
        }
        items_data = {
            "items": [
                {"id": "A"},
                {"id": "B"},
                {"id": "C"},
            ]
        }

        units = generate_units(config, items_data)
        # Take first 9 (simulating --max-units 9)
        first_9 = units[:9]
        ids = {u["unit_id"].split("__")[0] for u in first_9}
        # All 3 items should be represented
        assert ids == {"A", "B", "C"}
