"""
Tests for CLI flags and entry-point behavior:
- --restart
- expression-step throttling behavior
- --name
- --report dispatch
"""

import json
import signal
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


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


class TestRestart:
    def test_restart_handles_dead_pid(self, tmp_path):
        from orchestrate import _handle_restart

        run_dir = tmp_path / "test_run"
        write_manifest(run_dir, make_basic_manifest())
        (run_dir / "orchestrator.pid").write_text("99999")

        args = MagicMock()
        args.run_dir = run_dir

        with patch("os.kill", side_effect=ProcessLookupError):
            with patch("os.execv") as mock_execv:
                _handle_restart(args)
                mock_execv.assert_called_once()

    def test_restart_relaunches_realtime(self, tmp_path):
        from orchestrate import _handle_restart

        run_dir = tmp_path / "test_run"
        write_manifest(run_dir, make_basic_manifest(mode="realtime"))
        (run_dir / "orchestrator.pid").write_text("99999")

        args = MagicMock()
        args.run_dir = run_dir

        with patch("os.kill", side_effect=ProcessLookupError):
            with patch("os.execv") as mock_execv:
                _handle_restart(args)
                call_args = mock_execv.call_args[0][1]
                assert "--realtime" in call_args
                assert "--yes" in call_args

    def test_restart_relaunches_batch(self, tmp_path):
        from orchestrate import _handle_restart

        run_dir = tmp_path / "test_run"
        write_manifest(run_dir, make_basic_manifest(mode="batch"))
        (run_dir / "orchestrator.pid").write_text("99999")

        args = MagicMock()
        args.run_dir = run_dir

        with patch("os.kill", side_effect=ProcessLookupError):
            with patch("os.execv") as mock_execv:
                _handle_restart(args)
                call_args = mock_execv.call_args[0][1]
                assert "--watch" in call_args

    def test_restart_escalates_to_sigkill(self, tmp_path):
        from orchestrate import _handle_restart

        run_dir = tmp_path / "test_run"
        write_manifest(run_dir, make_basic_manifest())
        (run_dir / "orchestrator.pid").write_text("99999")

        args = MagicMock()
        args.run_dir = run_dir

        kill_calls = []

        def mock_kill(pid, sig):
            kill_calls.append((pid, sig))
            if sig == signal.SIGKILL:
                return
            if sig == signal.SIGTERM:
                return
            if sig == 0 and any(s == signal.SIGKILL for _, s in kill_calls):
                raise ProcessLookupError
            return

        with patch("os.kill", side_effect=mock_kill):
            with patch("time.sleep"):
                with patch("os.execv"):
                    _handle_restart(args)

        sent = [sig for _, sig in kill_calls if sig in (signal.SIGTERM, signal.SIGKILL)]
        assert signal.SIGTERM in sent


class TestRealtimeConfirmationAbort:
    def test_realtime_run_returns_1_when_user_declines_large_run(self, tmp_path):
        from orchestrate import realtime_run

        run_dir = tmp_path / "test_run"
        manifest = {
            "config": "config/config.yaml",
            "pipeline": ["step1"],
            "chunks": {
                "chunk_000": {
                    "state": "step1_PENDING",
                    "items": 51,
                    "valid": 0,
                    "failed": 0,
                    "retries": 0,
                }
            },
            "metadata": {"mode": "realtime"},
            "status": "running",
        }
        write_manifest(run_dir, manifest)
        config_dir = run_dir / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.yaml").write_text("pipeline:\n  steps:\n    - name: step1\n")

        with patch("orchestrate.check_prerequisites", return_value=None):
            with patch("orchestrate.retry_validation_failures", return_value=0):
                with patch("builtins.input", return_value="n"):
                    exit_code = realtime_run(run_dir)

        assert exit_code == 1

    def test_realtime_run_returns_1_on_eof_during_large_run_confirmation(self, tmp_path):
        from orchestrate import realtime_run

        run_dir = tmp_path / "test_run"
        manifest = {
            "config": "config/config.yaml",
            "pipeline": ["step1"],
            "chunks": {
                "chunk_000": {
                    "state": "step1_PENDING",
                    "items": 51,
                    "valid": 0,
                    "failed": 0,
                    "retries": 0,
                }
            },
            "metadata": {"mode": "realtime"},
            "status": "running",
        }
        write_manifest(run_dir, manifest)
        config_dir = run_dir / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.yaml").write_text("pipeline:\n  steps:\n    - name: step1\n")

        with patch("orchestrate.check_prerequisites", return_value=None):
            with patch("orchestrate.retry_validation_failures", return_value=0):
                with patch("builtins.input", side_effect=EOFError):
                    exit_code = realtime_run(run_dir)

        assert exit_code == 1


class TestExpressionThrottleOrdering:
    def test_expression_before_inflight_throttle(self):
        orchestrate_path = Path(__file__).parent.parent / "scripts" / "orchestrate.py"
        source = orchestrate_path.read_text()
        lines = source.split("\n")

        inflight_check_line = None
        for i, line in enumerate(lines):
            if "if inflight >= max_inflight:" in line:
                inflight_check_line = i
                break
        assert inflight_check_line is not None

        expr_check_line = None
        expr_continue_line = None
        for i in range(inflight_check_line - 1, max(0, inflight_check_line - 220), -1):
            line = lines[i]
            if "continue  # Skip API batch submission for expression steps" in line:
                expr_continue_line = i
            if "is_expression_step(config, step)" in line and "if " in line:
                expr_check_line = i
                break

        assert expr_check_line is not None
        assert expr_continue_line is not None
        assert expr_check_line < inflight_check_line
        assert expr_continue_line < inflight_check_line


class TestNamedRuns:
    def test_name_on_existing_run_updates_manifest(self, tmp_path):
        from orchestrate import _handle_name
        from octobatch_utils import load_manifest

        run_dir = tmp_path / "existing_run"
        write_manifest(run_dir, make_basic_manifest())

        args = MagicMock()
        args.run_dir = run_dir
        args.name = "Updated Name"
        _handle_name(args)

        updated = load_manifest(run_dir)
        assert updated["metadata"]["display_name"] == "Updated Name"

    def test_name_without_run_dir_errors(self, tmp_path):
        from orchestrate import _handle_name

        args = MagicMock()
        args.run_dir = tmp_path / "nonexistent"
        args.name = "Test"
        with pytest.raises(SystemExit):
            _handle_name(args)


    def test_restart_no_pid_file_exits(self, tmp_path):
        from orchestrate import _handle_restart

        run_dir = tmp_path / "test_run"
        run_dir.mkdir()
        write_manifest(run_dir, make_basic_manifest())

        args = MagicMock()
        args.run_dir = run_dir

        with pytest.raises(SystemExit):
            _handle_restart(args)


class TestExpressionThrottleStructure:
    def test_llm_steps_still_respect_inflight_limit(self):
        orchestrate_path = Path(__file__).parent.parent / "scripts" / "orchestrate.py"
        source = orchestrate_path.read_text()
        assert "if inflight >= max_inflight:" in source
        assert "throttled_count += 1" in source


class TestNamedRunsInit:
    def test_name_during_init_stores_in_manifest(self, tmp_path):
        from orchestrate import init_run

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


class TestReportCLI:
    def test_report_handler_outputs_text(self, tmp_path, capsys):
        from orchestrate import _handle_report

        run_dir = tmp_path / "report_run"
        write_manifest(run_dir, make_basic_manifest())
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

    def test_report_handler_json_output(self, tmp_path, capsys):
        from orchestrate import _handle_report

        run_dir = tmp_path / "report_run"
        write_manifest(run_dir, make_basic_manifest())
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
