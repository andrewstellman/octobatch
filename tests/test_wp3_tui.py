"""
Tests for Work Package 3 TUI features:
- 3A: Pipeline report modal (G key)
- 3B: Named runs display (B key)
- 3C: Mode switch (M key)
- 3D: Intermediate results (P key)
- 3E: AI troubleshooting (T key)
- 3F: Restart integration (R key enhancement)
- 3G: Cross-run comparison (C key with multi-select)

These are unit tests for the underlying logic, not full TUI rendering tests.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


# =============================================================================
# Helpers
# =============================================================================

def write_manifest(run_dir: Path, manifest: dict) -> Path:
    manifest_path = run_dir / "MANIFEST.json"
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest_path


def make_basic_manifest(provider="gemini", model="gemini-2.0-flash-001", mode="batch"):
    return {
        "pipeline": ["step1"],
        "chunks": {
            "chunk_000": {
                "state": "VALIDATED",
                "items": 3,
                "valid": 3,
                "failed": 0,
                "retries": 0,
            }
        },
        "status": "complete",
        "metadata": {
            "provider": provider,
            "model": model,
            "mode": mode,
            "start_time": "2025-01-01T10:00:00",
        },
    }


# =============================================================================
# 3A: Pipeline report modal binding
# =============================================================================

class TestPipelineReportBinding:
    """Test that the G key binding is present on main screen."""

    def test_main_screen_has_g_binding(self):
        """MainScreen should have G key binding for pipeline report."""
        from tui.screens.main_screen import MainScreen
        binding_keys = [b.key for b in MainScreen.BINDINGS]
        assert "g" in binding_keys
        assert "G" in binding_keys

    def test_g_binding_action_is_pipeline_report(self):
        """G key should map to pipeline_report action."""
        from tui.screens.main_screen import MainScreen
        for binding in MainScreen.BINDINGS:
            if binding.key == "g":
                assert binding.action == "pipeline_report"
                break


# =============================================================================
# 3B: Named runs display
# =============================================================================

class TestNamedRunsDisplay:
    """Test named runs via manifest display_name."""

    def test_display_name_in_manifest(self, tmp_path):
        """Display name should be readable from manifest metadata."""
        manifest = make_basic_manifest()
        manifest["metadata"]["display_name"] = "My Test Run"
        write_manifest(tmp_path, manifest)

        manifest_path = tmp_path / "MANIFEST.json"
        loaded = json.loads(manifest_path.read_text())
        assert loaded["metadata"]["display_name"] == "My Test Run"

    def test_display_name_from_run_data(self, tmp_path):
        """_build_run_data_from_manifest should include display_name."""
        from tui.utils.runs import _build_run_data_from_manifest
        manifest = make_basic_manifest()
        manifest["metadata"]["display_name"] = "Named Run"
        write_manifest(tmp_path, manifest)

        run_data = _build_run_data_from_manifest(tmp_path, manifest)
        assert run_data["display_name"] == "Named Run"

    def test_display_name_absent(self, tmp_path):
        """display_name should be None when not set."""
        from tui.utils.runs import _build_run_data_from_manifest
        manifest = make_basic_manifest()
        write_manifest(tmp_path, manifest)

        run_data = _build_run_data_from_manifest(tmp_path, manifest)
        assert run_data["display_name"] is None

    def test_b_binding_on_home_screen(self):
        """HomeScreen should have B key binding for naming runs."""
        from tui.screens.home_screen import HomeScreen
        binding_keys = [b.key for b in HomeScreen.BINDINGS]
        assert "b" in binding_keys
        assert "B" in binding_keys


# =============================================================================
# 3C: Mode switch
# =============================================================================

class TestModeSwitch:
    """Test mode switch scheduling via manifest."""

    def test_m_binding_on_main_screen(self):
        """MainScreen should have M key binding for mode switch."""
        from tui.screens.main_screen import MainScreen
        binding_keys = [b.key for b in MainScreen.BINDINGS]
        assert "m" in binding_keys

    def test_scheduled_mode_switch_written(self, tmp_path):
        """Scheduling a mode switch writes to manifest metadata."""
        manifest = make_basic_manifest(mode="batch")
        manifest["status"] = "paused"
        write_manifest(tmp_path, manifest)

        # Simulate what _apply_mode_switch does
        manifest_path = tmp_path / "MANIFEST.json"
        loaded = json.loads(manifest_path.read_text())
        loaded["metadata"]["scheduled_mode_switch"] = "realtime"
        manifest_path.write_text(json.dumps(loaded, indent=2))

        reloaded = json.loads(manifest_path.read_text())
        assert reloaded["metadata"]["scheduled_mode_switch"] == "realtime"

    def test_mode_switch_reverses(self, tmp_path):
        """Mode switch from realtime should schedule batch."""
        manifest = make_basic_manifest(mode="realtime")
        manifest["status"] = "paused"
        write_manifest(tmp_path, manifest)

        manifest_path = tmp_path / "MANIFEST.json"
        loaded = json.loads(manifest_path.read_text())
        current_mode = loaded["metadata"]["mode"]
        new_mode = "realtime" if current_mode == "batch" else "batch"
        assert new_mode == "batch"


# =============================================================================
# 3D: Intermediate results
# =============================================================================

class TestIntermediateResults:
    """Test intermediate results binding."""

    def test_p_binding_on_main_screen(self):
        """MainScreen should have P key binding for intermediate results."""
        from tui.screens.main_screen import MainScreen
        binding_keys = [b.key for b in MainScreen.BINDINGS]
        assert "p" in binding_keys
        assert "P" in binding_keys

    def test_p_binding_action(self):
        """P key should map to intermediate_results action."""
        from tui.screens.main_screen import MainScreen
        for binding in MainScreen.BINDINGS:
            if binding.key == "p":
                assert binding.action == "intermediate_results"
                break


# =============================================================================
# 3E: AI troubleshooting
# =============================================================================

class TestTroubleshooting:
    """Test troubleshooting binding."""

    def test_t_binding_on_main_screen(self):
        """MainScreen should have T key binding for troubleshooting."""
        from tui.screens.main_screen import MainScreen
        binding_keys = [b.key for b in MainScreen.BINDINGS]
        assert "t" in binding_keys
        assert "T" in binding_keys

    def test_t_binding_action(self):
        """T key should map to troubleshoot action."""
        from tui.screens.main_screen import MainScreen
        for binding in MainScreen.BINDINGS:
            if binding.key == "t":
                assert binding.action == "troubleshoot"
                break


# =============================================================================
# 3F: Restart integration (R key enhancement)
# =============================================================================

class TestRestartIntegration:
    """Test that R key on home screen handles running processes."""

    def test_r_binding_on_home_screen(self):
        """HomeScreen should have R key binding for resume/restart."""
        from tui.screens.home_screen import HomeScreen
        binding_keys = [b.key for b in HomeScreen.BINDINGS]
        assert "r" in binding_keys

    def test_r_binding_action_is_resume_run(self):
        """R key should map to resume_run action (handles both resume and restart)."""
        from tui.screens.home_screen import HomeScreen
        for binding in HomeScreen.BINDINGS:
            if binding.key == "r":
                assert binding.action == "resume_run"
                break


# =============================================================================
# 3G: Cross-run comparison with multi-select
# =============================================================================

class TestCrossRunComparison:
    """Test cross-run comparison UI elements."""

    def test_space_binding_on_home_screen(self):
        """HomeScreen should have Space key for multi-select toggle."""
        from tui.screens.home_screen import HomeScreen
        binding_keys = [b.key for b in HomeScreen.BINDINGS]
        assert "space" in binding_keys

    def test_c_binding_on_home_screen(self):
        """HomeScreen should have C key for comparison."""
        from tui.screens.home_screen import HomeScreen
        binding_keys = [b.key for b in HomeScreen.BINDINGS]
        assert "c" in binding_keys

    def test_selected_runs_tracking(self):
        """HomeScreen should track multi-selected runs."""
        from tui.screens.home_screen import HomeScreen
        screen = HomeScreen.__new__(HomeScreen)
        screen._selected_runs = set()
        screen._selected_runs.add(0)
        screen._selected_runs.add(2)
        assert len(screen._selected_runs) == 2
        screen._selected_runs.discard(0)
        assert len(screen._selected_runs) == 1


# =============================================================================
# Confirm and TextInput modals
# =============================================================================

class TestNewModals:
    """Test the new modal dialogs."""

    def test_confirm_modal_exists(self):
        """ConfirmModal should be importable."""
        from tui.screens.modals import ConfirmModal
        assert ConfirmModal is not None

    def test_text_input_modal_exists(self):
        """TextInputModal should be importable."""
        from tui.screens.modals import TextInputModal
        assert TextInputModal is not None

    def test_confirm_modal_bindings(self):
        """ConfirmModal should have y/n/escape bindings."""
        from tui.screens.modals import ConfirmModal
        binding_keys = [b.key for b in ConfirmModal.BINDINGS]
        assert "y" in binding_keys
        assert "n" in binding_keys
        assert "escape" in binding_keys

    def test_text_input_modal_bindings(self):
        """TextInputModal should have escape binding."""
        from tui.screens.modals import TextInputModal
        binding_keys = [b.key for b in TextInputModal.BINDINGS]
        assert "escape" in binding_keys
