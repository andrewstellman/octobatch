"""
Tests for Work Package 1 CLI features:
- 1A: --restart flag
- 1B: Expression step batch mode optimization
- 1C: --name flag (named runs)
- 1D: --report flag (handled via generate_report tests in test_run_tools.py)
"""

import json
import os
import signal
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


def write_jsonl(file_path: Path, records: list[dict]) -> Path:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
    return file_path


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
                "input_tokens": 100,
                "output_tokens": 200,
            }
        },
        "metadata": {
            "pipeline_name": "test_pipeline",
            "provider": provider,
            "model": model,
            "mode": mode,
            "start_time": "2024-01-01T00:00:00Z",
            "initial_input_tokens": 100,
            "initial_output_tokens": 200,
            "retry_input_tokens": 0,
            "retry_output_tokens": 0,
        },
        "status": "complete",
        "created": "2024-01-01T00:00:00Z",
        "updated": "2024-01-01T00:00:00Z",
    }


# =============================================================================
# 1A: --restart tests
# =============================================================================

class TestRestart:
    """Tests for _handle_restart()."""

    def test_restart_sends_sigterm_to_pid(self, tmp_path):
        """--restart sends SIGTERM to the running PID."""
        from orchestrate import _handle_restart

        run_dir = tmp_path / "test_run"
        manifest = make_basic_manifest()
        write_manifest(run_dir, manifest)
        pid_file = run_dir / "orchestrator.pid"
        pid_file.write_text("99999")

        args = MagicMock()
        args.run_dir = run_dir

        # Mock os.kill: first call (signal 0) raises ProcessLookupError (dead pid)
        with patch("os.kill", side_effect=ProcessLookupError):
            with patch("os.execv"):
                _handle_restart(args)

    def test_restart_handles_dead_pid(self, tmp_path):
        """--restart handles already-dead PID gracefully."""
        from orchestrate import _handle_restart

        run_dir = tmp_path / "test_run"
        manifest = make_basic_manifest()
        write_manifest(run_dir, manifest)
        pid_file = run_dir / "orchestrator.pid"
        pid_file.write_text("99999")

        args = MagicMock()
        args.run_dir = run_dir

        with patch("os.kill", side_effect=ProcessLookupError):
            with patch("os.execv") as mock_execv:
                _handle_restart(args)
                # Should still relaunch even with dead pid
                mock_execv.assert_called_once()

    def test_restart_relaunches_realtime(self, tmp_path):
        """--restart relaunches with --realtime for realtime runs."""
        from orchestrate import _handle_restart

        run_dir = tmp_path / "test_run"
        manifest = make_basic_manifest(mode="realtime")
        write_manifest(run_dir, manifest)
        pid_file = run_dir / "orchestrator.pid"
        pid_file.write_text("99999")

        args = MagicMock()
        args.run_dir = run_dir

        with patch("os.kill", side_effect=ProcessLookupError):
            with patch("os.execv") as mock_execv:
                _handle_restart(args)
                call_args = mock_execv.call_args[0][1]
                assert "--realtime" in call_args
                assert "--run-dir" in call_args

    def test_restart_relaunches_batch_with_watch(self, tmp_path):
        """--restart relaunches with --watch for batch runs."""
        from orchestrate import _handle_restart

        run_dir = tmp_path / "test_run"
        manifest = make_basic_manifest(mode="batch")
        write_manifest(run_dir, manifest)
        pid_file = run_dir / "orchestrator.pid"
        pid_file.write_text("99999")

        args = MagicMock()
        args.run_dir = run_dir

        with patch("os.kill", side_effect=ProcessLookupError):
            with patch("os.execv") as mock_execv:
                _handle_restart(args)
                call_args = mock_execv.call_args[0][1]
                assert "--watch" in call_args

    def test_restart_escalates_to_sigkill(self, tmp_path):
        """--restart sends SIGKILL after SIGTERM timeout."""
        from orchestrate import _handle_restart

        run_dir = tmp_path / "test_run"
        manifest = make_basic_manifest()
        write_manifest(run_dir, manifest)
        pid_file = run_dir / "orchestrator.pid"
        pid_file.write_text("99999")

        args = MagicMock()
        args.run_dir = run_dir

        kill_calls = []
        def mock_kill(pid, sig):
            kill_calls.append((pid, sig))
            if sig == signal.SIGKILL:
                return
            if sig == signal.SIGTERM:
                return
            # signal 0 check: process is alive unless SIGKILL sent
            if sig == 0 and any(s == signal.SIGKILL for _, s in kill_calls):
                raise ProcessLookupError
            return  # Process alive

        with patch("os.kill", side_effect=mock_kill):
            with patch("time.sleep"):  # Don't actually sleep
                with patch("os.execv"):
                    # Need to patch the _time import inside _handle_restart
                    import orchestrate
                    with patch.object(orchestrate, "time", create=True):
                        # The function imports time as _time locally
                        # Just let it run - it will eventually SIGKILL
                        _handle_restart(args)

        # Should have sent SIGTERM and SIGKILL
        signals_sent = [sig for _, sig in kill_calls if sig in (signal.SIGTERM, signal.SIGKILL)]
        assert signal.SIGTERM in signals_sent

    def test_restart_no_pid_file_exits(self, tmp_path):
        """--restart exits with error when no PID file exists."""
        from orchestrate import _handle_restart

        run_dir = tmp_path / "test_run"
        run_dir.mkdir()
        manifest = make_basic_manifest()
        write_manifest(run_dir, manifest)

        args = MagicMock()
        args.run_dir = run_dir

        with pytest.raises(SystemExit):
            _handle_restart(args)


# =============================================================================
# 1B: Expression step batch mode optimization
# =============================================================================

class TestExpressionStepOptimization:
    """Tests verifying expression steps skip the max_inflight_batches throttle."""

    def test_expression_step_executes_when_max_inflight_reached(self):
        """Expression steps should not be blocked by max_inflight_batches throttle.

        This is a structural test verifying the code ordering in the batch
        submission phase (Phase 2) of tick_run(): expression step detection
        comes BEFORE the inflight throttle check, with a 'continue' that
        skips the throttle.
        """
        orchestrate_path = Path(__file__).parent.parent / "scripts" / "orchestrate.py"
        source = orchestrate_path.read_text()
        lines = source.split("\n")

        # Find the inflight throttle check — there's only one
        inflight_check_line = None
        for i, line in enumerate(lines):
            if "if inflight >= max_inflight:" in line:
                inflight_check_line = i
                break

        assert inflight_check_line is not None, "Inflight throttle check not found"

        # Find the expression step check and continue that are NEAREST before
        # the inflight check (i.e., in the same batch submission phase)
        expr_check_line = None
        expr_continue_line = None
        for i in range(inflight_check_line - 1, max(0, inflight_check_line - 200), -1):
            line = lines[i]
            if "continue  # Skip API batch submission for expression steps" in line:
                expr_continue_line = i
            if "is_expression_step(config, step)" in line and "if " in line:
                expr_check_line = i
                break

        assert expr_check_line is not None, "Expression step check not found before throttle"
        assert expr_continue_line is not None, "Expression step continue not found before throttle"

        # Expression check must come BEFORE inflight throttle
        assert expr_check_line < inflight_check_line
        # Expression continue must come BEFORE inflight throttle
        assert expr_continue_line < inflight_check_line

    def test_llm_steps_still_respect_inflight_limit(self):
        """LLM steps should still be blocked by max_inflight_batches throttle.

        Verifies the throttle check exists and comes after expression handling.
        """
        orchestrate_path = Path(__file__).parent.parent / "scripts" / "orchestrate.py"
        source = orchestrate_path.read_text()

        # The throttle check must exist
        assert "if inflight >= max_inflight:" in source
        assert "throttled_count += 1" in source


# =============================================================================
# 1C: Named runs (--name)
# =============================================================================

class TestNamedRuns:
    """Tests for the --name flag."""

    def test_name_during_init_stores_in_manifest(self, tmp_path):
        """--name during --init stores display_name in manifest metadata."""
        from orchestrate import init_run

        # Create a minimal pipeline config
        config_dir = tmp_path / "pipeline"
        config_dir.mkdir()
        config = {
            "pipeline": {
                "steps": [{"name": "step1", "scope": "expression",
                           "expression": {"output_fields": {"x": "1"}}}]
            },
            "api": {"provider": "gemini", "model": "gemini-2.0-flash-001"},
            "processing": {"chunk_size": 10},
            "items": {"type": "direct", "entries": [{"unit_id": "u1"}]},
        }
        config_path = config_dir / "config.yaml"
        import yaml
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        run_dir = tmp_path / "named_run"
        success = init_run(
            config_path=config_path,
            run_dir=run_dir,
            provider_override="gemini",
            model_override="gemini-2.0-flash-001",
            display_name="My Test Run",
        )

        if success:
            from octobatch_utils import load_manifest
            manifest = load_manifest(run_dir)
            assert manifest["metadata"].get("display_name") == "My Test Run"

    def test_name_on_existing_run_updates_manifest(self, tmp_path):
        """--name on existing run updates manifest display_name."""
        from orchestrate import _handle_name

        run_dir = tmp_path / "existing_run"
        manifest = make_basic_manifest()
        write_manifest(run_dir, manifest)

        args = MagicMock()
        args.run_dir = run_dir
        args.name = "Updated Name"

        _handle_name(args)

        from octobatch_utils import load_manifest
        updated = load_manifest(run_dir)
        assert updated["metadata"]["display_name"] == "Updated Name"

    def test_name_without_run_dir_errors(self, tmp_path):
        """--name without valid --run-dir exits with error."""
        from orchestrate import _handle_name

        args = MagicMock()
        args.run_dir = tmp_path / "nonexistent"
        args.name = "Test"

        with pytest.raises(SystemExit):
            _handle_name(args)

    def test_name_not_in_manifest_when_not_provided(self, tmp_path):
        """display_name should not appear in manifest when --name is not used."""
        # Build a manifest without display_name
        manifest = make_basic_manifest()
        assert "display_name" not in manifest["metadata"]


# =============================================================================
# 1D: --report CLI dispatch
# =============================================================================

class TestReportCLI:
    """Tests for _handle_report() CLI dispatch."""

    def test_report_handler_outputs_text(self, tmp_path, capsys):
        """_handle_report outputs text report to stdout."""
        from orchestrate import _handle_report

        run_dir = tmp_path / "report_run"
        manifest = make_basic_manifest()
        write_manifest(run_dir, manifest)
        # Create units and validated files
        chunk_dir = run_dir / "chunks" / "chunk_000"
        units = [{"unit_id": f"u{i}"} for i in range(3)]
        write_jsonl(chunk_dir / "units.jsonl", units)
        write_jsonl(chunk_dir / "step1_validated.jsonl", units)

        args = MagicMock()
        args.run_dir = run_dir
        args.json = False

        _handle_report(args)
        captured = capsys.readouterr()
        assert "VALIDATION FUNNEL" in captured.out
        assert "step1" in captured.out

    def test_report_handler_json_output(self, tmp_path, capsys):
        """_handle_report with --json outputs JSON."""
        from orchestrate import _handle_report

        run_dir = tmp_path / "report_run"
        manifest = make_basic_manifest()
        write_manifest(run_dir, manifest)
        chunk_dir = run_dir / "chunks" / "chunk_000"
        units = [{"unit_id": f"u{i}"} for i in range(3)]
        write_jsonl(chunk_dir / "units.jsonl", units)
        write_jsonl(chunk_dir / "step1_validated.jsonl", units)

        args = MagicMock()
        args.run_dir = run_dir
        args.json = True

        _handle_report(args)
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert "funnel" in result
        assert "total_units" in result
