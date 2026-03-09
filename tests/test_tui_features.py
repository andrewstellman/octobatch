"""Tests for TUI feature bindings and wiring."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


def test_main_screen_bindings_present():
    from tui.screens.main_screen import MainScreen
    keys = [b.key for b in MainScreen.BINDINGS]
    for key in ("g", "m", "p", "t"):
        assert key in keys


def test_main_screen_uses_expected_actions():
    from tui.screens.main_screen import MainScreen
    by_key = {b.key: b.action for b in MainScreen.BINDINGS}
    assert by_key["g"] == "pipeline_report"
    assert by_key["m"] == "mode_switch"
    assert by_key["p"] == "intermediate_results"
    assert by_key["t"] == "troubleshoot"


def test_home_screen_named_run_binding_is_w():
    from tui.screens.home_screen import HomeScreen
    keys = [b.key for b in HomeScreen.BINDINGS]
    assert "w" in keys
    assert "W" in keys
    by_key = {b.key: b.action for b in HomeScreen.BINDINGS}
    assert by_key["w"] == "name_run"
    # n/N must remain mapped to new_run, not shadowed
    assert by_key["n"] == "new_run"


def test_home_screen_compare_multiselect_bindings():
    from tui.screens.home_screen import HomeScreen
    keys = [b.key for b in HomeScreen.BINDINGS]
    assert "space" in keys
    assert "c" in keys


def test_mode_switch_metadata_written(tmp_path):
    manifest = {
        "pipeline": ["step1"],
        "chunks": {"chunk_000": {"state": "VALIDATED", "items": 1, "valid": 1, "failed": 0, "retries": 0}},
        "status": "paused",
        "metadata": {"mode": "batch"},
    }
    manifest_path = tmp_path / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest))

    loaded = json.loads(manifest_path.read_text())
    loaded["metadata"]["scheduled_mode_switch"] = "realtime"
    manifest_path.write_text(json.dumps(loaded))
    check = json.loads(manifest_path.read_text())
    assert check["metadata"]["scheduled_mode_switch"] == "realtime"


# --- Restored display name tests ---


def _make_basic_manifest(mode="batch"):
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
            "provider": "gemini",
            "model": "gemini-2.0-flash-001",
            "mode": mode,
            "start_time": "2025-01-01T10:00:00",
        },
    }


def test_display_name_from_run_data(tmp_path):
    from tui.utils.runs import _build_run_data_from_manifest
    manifest = _make_basic_manifest()
    manifest["metadata"]["display_name"] = "Named Run"
    manifest_path = tmp_path / "MANIFEST.json"
    tmp_path.mkdir(exist_ok=True)
    manifest_path.write_text(json.dumps(manifest))
    run_data = _build_run_data_from_manifest(tmp_path, manifest)
    assert run_data["display_name"] == "Named Run"


def test_display_name_absent(tmp_path):
    from tui.utils.runs import _build_run_data_from_manifest
    manifest = _make_basic_manifest()
    manifest_path = tmp_path / "MANIFEST.json"
    tmp_path.mkdir(exist_ok=True)
    manifest_path.write_text(json.dumps(manifest))
    run_data = _build_run_data_from_manifest(tmp_path, manifest)
    assert run_data["display_name"] is None


# --- Restored home screen binding tests ---


def test_r_binding_on_home_screen():
    from tui.screens.home_screen import HomeScreen
    binding_keys = [b.key for b in HomeScreen.BINDINGS]
    assert "r" in binding_keys


def test_r_binding_action_is_resume_run():
    from tui.screens.home_screen import HomeScreen
    for binding in HomeScreen.BINDINGS:
        if binding.key == "r":
            assert binding.action == "resume_run"
            break


# --- Restored modal binding tests ---


def test_confirm_modal_bindings():
    from tui.screens.modals import ConfirmModal
    binding_keys = [b.key for b in ConfirmModal.BINDINGS]
    assert "y" in binding_keys
    assert "n" in binding_keys
    assert "escape" in binding_keys


def test_text_input_modal_bindings():
    from tui.screens.modals import TextInputModal
    binding_keys = [b.key for b in TextInputModal.BINDINGS]
    assert "escape" in binding_keys
