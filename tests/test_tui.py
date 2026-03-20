"""
Automated TUI tests using Textual's run_test() framework.

Tests that TUI screens load without crashing and basic interactions work.
All tests use synthetic data and do not depend on local run directories.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Add scripts directory to path so tui package is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


# =============================================================================
# Helpers for creating synthetic run directories
# =============================================================================

def _create_synthetic_run(run_dir: Path, status: str = "complete",
                          pipeline: list[str] | None = None,
                          total_items: int = 5, valid: int = 4,
                          failed: int = 1) -> Path:
    """Create a minimal synthetic run directory with MANIFEST.json."""
    run_dir.mkdir(parents=True, exist_ok=True)
    if pipeline is None:
        pipeline = ["step1"]

    now = datetime.now(timezone.utc).isoformat()
    manifest = {
        "pipeline": pipeline,
        "config": "config/config.yaml",
        "status": status,
        "chunks": {
            "chunk_000": {
                "state": "VALIDATED" if status == "complete" else f"{pipeline[0]}_PENDING",
                "items": total_items,
                "valid": valid,
                "failed": failed,
                "retries": 0,
                "input_tokens": 100,
                "output_tokens": 200,
            }
        },
        "metadata": {
            "pipeline_name": "TestPipeline",
            "provider": "test",
            "model": "test-model",
            "mode": "batch",
            "initial_input_tokens": 100,
            "initial_output_tokens": 200,
            "retry_input_tokens": 0,
            "retry_output_tokens": 0,
        },
        "created": now,
        "updated": now,
    }

    with open(run_dir / "MANIFEST.json", "w") as f:
        json.dump(manifest, f, indent=2)

    # Create minimal config so load_run_data can find it
    config_dir = run_dir / "config"
    config_dir.mkdir(exist_ok=True)
    with open(config_dir / "config.yaml", "w") as f:
        f.write("pipeline:\n  steps:\n")
        for step in pipeline:
            f.write(f"    - name: {step}\n      scope: expression\n")

    # Create chunk directory
    chunk_dir = run_dir / "chunks" / "chunk_000"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    with open(chunk_dir / "units.jsonl", "w") as f:
        for i in range(total_items):
            f.write(json.dumps({"unit_id": f"unit_{i:03d}"}) + "\n")

    return run_dir


def _make_scan_entry(run_dir: Path, status: str = "complete") -> dict:
    """Create a scan_runs()-compatible entry for a synthetic run."""
    now = datetime.now(timezone.utc)
    return {
        "name": run_dir.name,
        "path": run_dir,
        "status": status,
        "progress": 100 if status == "complete" else 50,
        "cost": "$0.0001",
        "cost_value": 0.0001,
        "total_tokens": 300,
        "updated": now,
        "started": now,
        "total_units": 5,
        "valid_units": 4,
        "failed_units": 1,
        "pipeline_name": "TestPipeline",
        "mode": "batch",
    }


@pytest.fixture
def synthetic_run_dir(tmp_path):
    """Create a synthetic run directory for testing."""
    run_dir = tmp_path / "test_run"
    _create_synthetic_run(run_dir)
    return run_dir


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
    async def test_data_table_populates(self, tmp_path, monkeypatch):
        """DataTable populates with synthetic runs."""
        from tui.app import OctobatchApp
        import tui.screens.home_screen as home_screen_mod
        from textual.widgets import DataTable

        run_dir = tmp_path / "test_run"
        _create_synthetic_run(run_dir)
        synthetic_runs = [_make_scan_entry(run_dir)]

        monkeypatch.setattr(
            home_screen_mod,
            "get_enhanced_run_status",
            lambda _run_path, status: status,
        )
        # Monkeypatch scan_runs in the home_screen module to return synthetic data
        monkeypatch.setattr(
            home_screen_mod,
            "scan_runs",
            lambda *args, **kwargs: synthetic_runs,
        )

        app = OctobatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("escape")
            await pilot.pause(delay=3.0)
            # Force a synchronous refresh
            app.screen._load_data()
            app.screen._populate_runs_content()

            tables = app.screen.query(DataTable)
            assert tables, "Expected DataTable on HomeScreen"
            table = tables.first()
            assert table.row_count > 0, "Expected rows in DataTable with synthetic runs"

    @pytest.mark.asyncio
    async def test_enter_opens_main_screen(self, tmp_path, monkeypatch):
        """Pressing Enter on a run opens MainScreen."""
        from tui.app import OctobatchApp
        import tui.screens.home_screen as home_screen_mod
        import tui.screens.main_screen as main_screen_mod
        from textual.widgets import DataTable

        run_dir = tmp_path / "test_run"
        _create_synthetic_run(run_dir)
        synthetic_runs = [_make_scan_entry(run_dir)]

        monkeypatch.setattr(
            home_screen_mod,
            "scan_runs",
            lambda *args, **kwargs: synthetic_runs,
        )
        monkeypatch.setattr(
            home_screen_mod,
            "get_enhanced_run_status",
            lambda _run_path, status: status,
        )
        monkeypatch.setattr(
            main_screen_mod,
            "get_run_process_status",
            lambda _run_dir: {"alive": False, "pid": None, "source": None},
        )
        monkeypatch.setattr(main_screen_mod, "has_recent_errors", lambda _run_dir: False)

        app = OctobatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("escape")
            await pilot.pause(delay=3.0)
            # Force populate
            app.screen._load_data()
            app.screen._populate_runs_content()
            await pilot.pause(delay=0.5)

            tables = app.screen.query(DataTable)
            assert tables and tables.first().row_count > 0, "Expected rows in DataTable"

            # Press Enter to open detail view
            await pilot.press("enter")
            await pilot.pause(delay=1.0)

            from tui.screens import MainScreen
            screens = [type(s).__name__ for s in app.screen_stack]
            assert "MainScreen" in screens, f"Expected MainScreen after Enter, got: {screens}"

    @pytest.mark.asyncio
    async def test_escape_returns_to_home(self, tmp_path, monkeypatch):
        """Pressing Escape from MainScreen returns to HomeScreen."""
        from tui.app import OctobatchApp
        import tui.screens.home_screen as home_screen_mod
        import tui.screens.main_screen as main_screen_mod
        from textual.widgets import DataTable

        run_dir = tmp_path / "test_run"
        _create_synthetic_run(run_dir)
        synthetic_runs = [_make_scan_entry(run_dir)]

        monkeypatch.setattr(
            home_screen_mod,
            "scan_runs",
            lambda *args, **kwargs: synthetic_runs,
        )
        monkeypatch.setattr(
            home_screen_mod,
            "get_enhanced_run_status",
            lambda _run_path, status: status,
        )
        monkeypatch.setattr(
            main_screen_mod,
            "get_run_process_status",
            lambda _run_dir: {"alive": False, "pid": None, "source": None},
        )
        monkeypatch.setattr(main_screen_mod, "has_recent_errors", lambda _run_dir: False)

        app = OctobatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("escape")
            await pilot.pause(delay=3.0)
            # Force populate
            app.screen._load_data()
            app.screen._populate_runs_content()
            await pilot.pause(delay=0.5)

            tables = app.screen.query(DataTable)
            assert tables and tables.first().row_count > 0, "Expected rows in DataTable"

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
        # Should have header line or empty-state message
        assert "Run" in captured.out or "No runs found" in captured.out

    def test_dump_home_json(self, capsys):
        """--dump --json produces valid JSON."""
        from tui_dump import dump_home
        import json as json_mod
        code = dump_home(as_json=True)
        assert code == 0
        captured = capsys.readouterr()
        data = json_mod.loads(captured.out)
        assert isinstance(data, list)

    def test_dump_run_text(self, capsys, synthetic_run_dir):
        """--dump --run-dir produces text output for a synthetic run."""
        from tui_dump import dump_run
        code = dump_run(synthetic_run_dir, as_json=False)
        assert code == 0
        captured = capsys.readouterr()
        assert "Run:" in captured.out
        assert "Status:" in captured.out
        assert "Pipeline Steps:" in captured.out

    def test_dump_run_json(self, capsys, synthetic_run_dir):
        """--dump --run-dir --json produces valid JSON for a synthetic run."""
        from tui_dump import dump_run
        import json as json_mod
        code = dump_run(synthetic_run_dir, as_json=True)
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

    def test_completed_with_failures_shows_complete(self, tmp_path):
        """Steps show 'complete' not 'running' when run is terminal but has failures."""
        from tui.data import load_run_data

        # Create a synthetic completed run with failures
        run_dir = tmp_path / "completed_with_failures"
        _create_synthetic_run(
            run_dir,
            status="complete",
            pipeline=["deal_cards", "play_hand"],
            total_items=9,
            valid=7,
            failed=2,
        )

        run_data = load_run_data(run_dir)
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


class TestSharedETA:
    """Tests for the shared ETA calculation function (Bug 5)."""

    def test_compute_eta_seconds_basic(self):
        """compute_eta_seconds returns correct value for 50% progress."""
        from tui.utils.formatting import compute_eta_seconds
        # 50% done in 60 seconds → 60 seconds remaining
        result = compute_eta_seconds(60.0, 50.0)
        assert result == 60.0

    def test_compute_eta_seconds_consistent(self):
        """compute_eta_seconds returns same value regardless of caller."""
        from tui.utils.formatting import compute_eta_seconds
        # Both screens should get the same result for same inputs
        result1 = compute_eta_seconds(120.0, 40.0)
        result2 = compute_eta_seconds(120.0, 40.0)
        assert result1 == result2
        # 40% done in 120s → 180s remaining
        assert result1 == pytest.approx(180.0)

    def test_compute_eta_accounts_for_remaining(self):
        """ETA accounts for remaining work, not just current step."""
        from tui.utils.formatting import compute_eta_seconds
        # 10% done in 30s → 270s remaining
        result = compute_eta_seconds(30.0, 10.0)
        assert result == pytest.approx(270.0)

    def test_compute_eta_returns_none_before_throughput(self):
        """ETA returns None when no progress data is available."""
        from tui.utils.formatting import compute_eta_seconds
        assert compute_eta_seconds(0.0, 0.0) is None
        assert compute_eta_seconds(60.0, 0.0) is None
        assert compute_eta_seconds(0.0, 50.0) is None

    def test_compute_eta_returns_none_at_100_percent(self):
        """ETA returns None when progress is 100%."""
        from tui.utils.formatting import compute_eta_seconds
        assert compute_eta_seconds(300.0, 100.0) is None

    def test_format_eta_basic(self):
        """format_eta produces consistent formatting."""
        from tui.utils.formatting import format_eta
        assert format_eta(None) == "—"
        assert format_eta(30) == "~<1m"
        assert format_eta(120) == "~2m"
        assert format_eta(3661) == "~1h 1m"
        assert format_eta(7200) == "~2h"

    def test_format_eta_persists_once_available(self):
        """ETA doesn't reset to — once throughput data exists."""
        from tui.utils.formatting import compute_eta_seconds, format_eta
        # Simulate successive updates with increasing progress
        eta1 = format_eta(compute_eta_seconds(60.0, 20.0))
        eta2 = format_eta(compute_eta_seconds(120.0, 40.0))
        # Both should be valid, not —
        assert eta1 != "—"
        assert eta2 != "—"


# =============================================================================
# get_run_status — status inference from manifest
# =============================================================================

class TestGetRunStatus:
    """Tests for get_run_status() manifest status inference."""

    def test_explicit_running_with_failed_chunks_returns_active(self):
        """A running manifest with some failed chunks should return 'active',
        not 'failed'. The orchestrator is still processing other chunks."""
        from tui.utils.runs import get_run_status
        manifest = {
            "status": "running",
            "chunks": {
                "chunk_000": {"state": "VALIDATED"},
                "chunk_001": {"state": "FAILED"},
                "chunk_002": {"state": "score_PENDING"},
            },
        }
        assert get_run_status(manifest) == "active"

    def test_explicit_running_all_chunks_validated_returns_active(self):
        """Even if all chunks are validated, explicit 'running' wins."""
        from tui.utils.runs import get_run_status
        manifest = {
            "status": "running",
            "chunks": {
                "chunk_000": {"state": "VALIDATED"},
                "chunk_001": {"state": "VALIDATED"},
            },
        }
        assert get_run_status(manifest) == "active"

    def test_explicit_failed_returns_failed(self):
        """Manifest explicitly set to 'failed' returns 'failed'."""
        from tui.utils.runs import get_run_status
        manifest = {"status": "failed", "chunks": {}}
        assert get_run_status(manifest) == "failed"

    def test_explicit_complete_returns_complete(self):
        """Manifest explicitly set to 'complete' returns 'complete'."""
        from tui.utils.runs import get_run_status
        manifest = {"status": "complete", "chunks": {}}
        assert get_run_status(manifest) == "complete"

    def test_explicit_paused_returns_paused(self):
        """Manifest explicitly set to 'paused' returns 'paused'."""
        from tui.utils.runs import get_run_status
        manifest = {"status": "paused", "chunks": {}}
        assert get_run_status(manifest) == "paused"

    def test_no_status_failed_chunks_returns_failed(self):
        """Without explicit status, failed chunks infer 'failed'."""
        from tui.utils.runs import get_run_status
        manifest = {
            "chunks": {
                "chunk_000": {"state": "VALIDATED"},
                "chunk_001": {"state": "FAILED"},
            },
        }
        assert get_run_status(manifest) == "failed"

    def test_no_status_all_validated_returns_complete(self):
        """Without explicit status, all validated infers 'complete'."""
        from tui.utils.runs import get_run_status
        manifest = {
            "chunks": {
                "chunk_000": {"state": "VALIDATED"},
                "chunk_001": {"state": "VALIDATED"},
            },
        }
        assert get_run_status(manifest) == "complete"

    def test_no_status_all_pending_returns_pending(self):
        """Without explicit status, all pending infers 'pending'."""
        from tui.utils.runs import get_run_status
        manifest = {
            "chunks": {
                "chunk_000": {"state": "PENDING"},
                "chunk_001": {"state": "PENDING"},
            },
        }
        assert get_run_status(manifest) == "pending"

    def test_no_status_mixed_non_terminal_returns_detached(self):
        """Without explicit status and mixed states, returns 'detached'."""
        from tui.utils.runs import get_run_status
        manifest = {
            "chunks": {
                "chunk_000": {"state": "VALIDATED"},
                "chunk_001": {"state": "score_PENDING"},
            },
        }
        assert get_run_status(manifest) == "detached"

    def test_no_chunks_returns_pending(self):
        """Empty chunks dict returns 'pending'."""
        from tui.utils.runs import get_run_status
        manifest = {"chunks": {}}
        assert get_run_status(manifest) == "pending"
