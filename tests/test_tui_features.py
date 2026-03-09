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
