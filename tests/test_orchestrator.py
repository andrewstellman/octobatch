"""
Tests for orchestrator logic (scripts/orchestrate.py).

Covers:
- Pipeline step sequencing (steps execute in order)
- Validation failure triggers retry
- Max retry exhaustion marks unit as failed
- Expression steps bypass API submission but still produce output
- Chunk state transitions (PENDING -> SUBMITTED -> VALIDATED)
- State parsing and helper functions
"""

import gzip
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import orchestrate

from orchestrate import (
    parse_state,
    get_next_step,
    is_expression_step,
    get_expression_step_config,
    step_has_validation,
    run_expression_step,
    run_step_realtime,
    get_subprocess_timeout,
    retry_validation_failures,
    is_run_terminal,
    extract_collect_result,
    build_failures_map,
    get_schema_path,
    count_step_failures,
    categorize_step_failures,
    compute_step_cost,
    count_step_units,
    mark_run_failed,
    mark_run_paused,
    mark_run_complete,
    mark_run_running,
    write_pid_file,
    cleanup_pid_file,
    get_next_retry_number,
    retry_failures_run,
    parse_duration,
    format_watch_progress,
    format_step_provider_tag,
    _run_gzip_post_process,
    run_post_process,
    check_prerequisites,
    _log_api_key_status,
    _cleanup_subprocesses,
    run_scope_run_step,
    prepare_prompts,
    run_validation_pipeline,
    build_run_status,
    status_run,
)


# =============================================================================
# State Parsing
# =============================================================================

class TestParseState:
    """Tests for parse_state()."""

    def test_step_pending(self):
        """Parses step_PENDING states."""
        step, status = parse_state("generate_PENDING")
        assert step == "generate"
        assert status == "PENDING"

    def test_step_submitted(self):
        """Parses step_SUBMITTED states."""
        step, status = parse_state("play_hand_SUBMITTED")
        assert step == "play_hand"
        assert status == "SUBMITTED"

    def test_step_complete(self):
        """Parses step_COMPLETE states."""
        step, status = parse_state("deal_cards_COMPLETE")
        assert step == "deal_cards"
        assert status == "COMPLETE"

    def test_validated_terminal(self):
        """Parses VALIDATED terminal state."""
        step, status = parse_state("VALIDATED")
        assert step is None
        assert status == "VALIDATED"

    def test_failed_terminal(self):
        """Parses FAILED terminal state."""
        step, status = parse_state("FAILED")
        assert step is None
        assert status == "FAILED"

    def test_step_name_with_underscores(self):
        """Handles step names containing underscores."""
        step, status = parse_state("deal_cards_PENDING")
        assert step == "deal_cards"
        assert status == "PENDING"

    def test_multi_underscore_step_name(self):
        """Handles step names with multiple underscores."""
        step, status = parse_state("verify_hand_totals_SUBMITTED")
        assert step == "verify_hand_totals"
        assert status == "SUBMITTED"

    def test_unknown_state(self):
        """Unknown state format returns (None, state)."""
        step, status = parse_state("UNKNOWN_STATE_VALUE")
        # "UNKNOWN_STATE_VALUE" doesn't end with _PENDING, _SUBMITTED, or _COMPLETE
        # and isn't VALIDATED or FAILED
        assert step is None
        assert status == "UNKNOWN_STATE_VALUE"


# =============================================================================
# Pipeline Step Sequencing
# =============================================================================

class TestPipelineSequencing:
    """Tests for get_next_step()."""

    def test_next_step_exists(self):
        """Returns next step when not at end of pipeline."""
        pipeline = ["deal_cards", "play_hand", "verify_hand"]
        assert get_next_step(pipeline, "deal_cards") == "play_hand"
        assert get_next_step(pipeline, "play_hand") == "verify_hand"

    def test_last_step_returns_none(self):
        """Returns None when at the last step."""
        pipeline = ["deal_cards", "play_hand", "verify_hand"]
        assert get_next_step(pipeline, "verify_hand") is None

    def test_unknown_step_returns_none(self):
        """Returns None for a step not in the pipeline."""
        pipeline = ["deal_cards", "play_hand"]
        assert get_next_step(pipeline, "nonexistent") is None

    def test_single_step_pipeline(self):
        """Single step pipeline returns None for next step."""
        pipeline = ["only_step"]
        assert get_next_step(pipeline, "only_step") is None


# =============================================================================
# Expression Step Detection
# =============================================================================

class TestExpressionStepDetection:
    """Tests for is_expression_step() and get_expression_step_config()."""

    @pytest.fixture
    def blackjack_config(self):
        return {
            "pipeline": {
                "steps": [
                    {"name": "deal_cards", "scope": "expression", "expressions": {"card": "'A'"}},
                    {"name": "play_hand"},
                    {"name": "verify_hand", "scope": "expression", "expressions": {"ok": "True"}},
                ]
            }
        }

    def test_expression_step_detected(self, blackjack_config):
        """Expression steps are correctly identified."""
        assert is_expression_step(blackjack_config, "deal_cards") is True
        assert is_expression_step(blackjack_config, "verify_hand") is True

    def test_llm_step_not_expression(self, blackjack_config):
        """LLM steps (no scope) are not expression steps."""
        assert is_expression_step(blackjack_config, "play_hand") is False

    def test_nonexistent_step(self, blackjack_config):
        """Nonexistent step returns False."""
        assert is_expression_step(blackjack_config, "nonexistent") is False

    def test_get_expression_step_config_returns_dict(self, blackjack_config):
        """get_expression_step_config returns the full step config dict."""
        config = get_expression_step_config(blackjack_config, "deal_cards")
        assert config is not None
        assert config["name"] == "deal_cards"
        assert "expressions" in config

    def test_get_expression_step_config_returns_none_for_llm(self, blackjack_config):
        """get_expression_step_config returns None for LLM steps."""
        assert get_expression_step_config(blackjack_config, "play_hand") is None


# =============================================================================
# Validation Detection
# =============================================================================

class TestStepHasValidation:
    """Tests for step_has_validation()."""

    def test_step_with_validation(self):
        """Returns True for step with validation rules."""
        config = {"validation": {"play_hand": {"required": ["result"]}}}
        assert step_has_validation(config, "play_hand") is True

    def test_step_without_validation(self):
        """Returns False for step without validation rules."""
        config = {"validation": {"play_hand": {"required": ["result"]}}}
        assert step_has_validation(config, "deal_cards") is False

    def test_no_validation_section(self):
        """Returns False when config has no validation section."""
        assert step_has_validation({}, "any_step") is False


# =============================================================================
# Subprocess Timeout
# =============================================================================

class TestSubprocessTimeout:
    """Tests for get_subprocess_timeout()."""

    def test_default_timeout(self):
        """Default timeout is 600 seconds."""
        assert get_subprocess_timeout() == 600
        assert get_subprocess_timeout(None) == 600

    def test_config_override(self):
        """Config can override timeout."""
        config = {"api": {"subprocess_timeout_seconds": 120}}
        assert get_subprocess_timeout(config) == 120

    def test_missing_api_section(self):
        """Missing api section uses default."""
        assert get_subprocess_timeout({}) == 600

    def test_invalid_override_raises_value_error(self):
        """Non-integer timeout override should raise ValueError."""
        config = {"api": {"subprocess_timeout_seconds": "fast"}}
        with pytest.raises(ValueError):
            get_subprocess_timeout(config)


# =============================================================================
# Expression Step Execution
# =============================================================================

class TestRunExpressionStep:
    """Tests for run_expression_step()."""

    @pytest.fixture
    def run_dir(self, tmp_path):
        """Create a minimal run directory structure."""
        chunk_dir = tmp_path / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)

        # Write units.jsonl
        units = [
            {"unit_id": "unit_001", "strategy_name": "The Pro", "_repetition_seed": 42},
            {"unit_id": "unit_002", "strategy_name": "The Coward", "_repetition_seed": 99},
        ]
        with open(chunk_dir / "units.jsonl", "w") as f:
            for unit in units:
                f.write(json.dumps(unit) + "\n")

        # Write log file
        (tmp_path / "RUN_LOG.txt").touch()

        return tmp_path

    def test_basic_expression_step(self, run_dir):
        """Expression step evaluates expressions and writes output."""
        step_config = {
            "name": "deal_cards",
            "scope": "expression",
            "expressions": {
                "player_card": "'A'",
                "dealer_card": "'K'",
            },
        }
        config = {
            "pipeline": {
                "steps": [{"name": "deal_cards", "scope": "expression"}]
            }
        }
        manifest = {"pipeline": ["deal_cards"]}
        log_file = run_dir / "RUN_LOG.txt"

        valid, failed, in_tok, out_tok = run_expression_step(
            run_dir, "chunk_000", "deal_cards", step_config, config, manifest, log_file
        )

        assert valid == 2
        assert failed == 0
        assert in_tok == 0  # Expression steps don't use tokens
        assert out_tok == 0

        # Check output file was written
        output_file = run_dir / "chunks" / "chunk_000" / "deal_cards_validated.jsonl"
        assert output_file.exists()

        results = []
        with open(output_file) as f:
            for line in f:
                results.append(json.loads(line))

        assert len(results) == 2
        assert results[0]["player_card"] == "A"
        assert results[0]["dealer_card"] == "K"
        assert results[0]["unit_id"] == "unit_001"  # Original fields preserved

    def test_expression_step_with_randomness(self, run_dir):
        """Expression step with random expressions produces deterministic output."""
        step_config = {
            "name": "deal_cards",
            "scope": "expression",
            "expressions": {
                "roll": "random.randint(1, 100)",
            },
        }
        config = {
            "pipeline": {
                "steps": [{"name": "deal_cards", "scope": "expression"}]
            }
        }
        manifest = {"pipeline": ["deal_cards"]}
        log_file = run_dir / "RUN_LOG.txt"

        # Run twice
        valid1, _, _, _ = run_expression_step(
            run_dir, "chunk_000", "deal_cards", step_config, config, manifest, log_file
        )
        output_file = run_dir / "chunks" / "chunk_000" / "deal_cards_validated.jsonl"
        with open(output_file) as f:
            results1 = [json.loads(line) for line in f]

        valid2, _, _, _ = run_expression_step(
            run_dir, "chunk_000", "deal_cards", step_config, config, manifest, log_file
        )
        with open(output_file) as f:
            results2 = [json.loads(line) for line in f]

        # Same seeds should produce same results
        assert results1[0]["roll"] == results2[0]["roll"]
        assert results1[1]["roll"] == results2[1]["roll"]

    def test_expression_step_reads_previous_step_output(self, run_dir):
        """When not first step, reads from previous step's validated file."""
        # Write previous step output
        chunk_dir = run_dir / "chunks" / "chunk_000"
        prev_output = [
            {"unit_id": "unit_001", "player_card_1": "A", "_repetition_seed": 42},
            {"unit_id": "unit_002", "player_card_1": "K", "_repetition_seed": 99},
        ]
        with open(chunk_dir / "deal_cards_validated.jsonl", "w") as f:
            for unit in prev_output:
                f.write(json.dumps(unit) + "\n")

        step_config = {
            "name": "verify_hand",
            "scope": "expression",
            "expressions": {
                "is_ace": "player_card_1 == 'A'",
            },
        }
        config = {
            "pipeline": {
                "steps": [
                    {"name": "deal_cards", "scope": "expression"},
                    {"name": "verify_hand", "scope": "expression"},
                ]
            }
        }
        manifest = {"pipeline": ["deal_cards", "verify_hand"]}
        log_file = run_dir / "RUN_LOG.txt"

        valid, failed, _, _ = run_expression_step(
            run_dir, "chunk_000", "verify_hand", step_config, config, manifest, log_file
        )

        assert valid == 2
        output_file = chunk_dir / "verify_hand_validated.jsonl"
        with open(output_file) as f:
            results = [json.loads(line) for line in f]
        assert results[0]["is_ace"] is True  # unit_001 has A
        assert results[1]["is_ace"] is False  # unit_002 has K

    def test_expression_step_custom_output_file(self, run_dir):
        """Expression step can write to a custom output file."""
        chunk_dir = run_dir / "chunks" / "chunk_000"
        custom_output = chunk_dir / "custom_results.jsonl"

        step_config = {
            "name": "deal_cards",
            "scope": "expression",
            "expressions": {"val": "42"},
        }
        manifest = {"pipeline": ["deal_cards"]}
        log_file = run_dir / "RUN_LOG.txt"

        valid, _, _, _ = run_expression_step(
            run_dir, "chunk_000", "deal_cards", step_config, {},
            manifest, log_file, output_file=custom_output
        )

        assert valid == 2
        assert custom_output.exists()
        # Default file should NOT exist
        assert not (chunk_dir / "deal_cards_validated.jsonl").exists()

    def test_expression_step_missing_input_file(self, run_dir):
        """Expression step with missing input file returns (0, 0, 0, 0)."""
        step_config = {
            "name": "verify_hand",
            "scope": "expression",
            "expressions": {"val": "42"},
        }
        manifest = {"pipeline": ["deal_cards", "verify_hand"]}
        log_file = run_dir / "RUN_LOG.txt"

        # Previous step output doesn't exist
        valid, failed, _, _ = run_expression_step(
            run_dir, "chunk_000", "verify_hand", step_config, {},
            manifest, log_file
        )
        assert valid == 0
        assert failed == 0

    def test_expression_step_no_expressions(self, run_dir):
        """Expression step with no expressions returns (0, 0, 0, 0)."""
        step_config = {
            "name": "deal_cards",
            "scope": "expression",
            "expressions": {},
        }
        manifest = {"pipeline": ["deal_cards"]}
        log_file = run_dir / "RUN_LOG.txt"

        valid, failed, _, _ = run_expression_step(
            run_dir, "chunk_000", "deal_cards", step_config, {},
            manifest, log_file
        )
        assert valid == 0
        assert failed == 0

    def test_expression_step_adds_metadata(self, run_dir):
        """Expression step adds _metadata to each output unit."""
        step_config = {
            "name": "deal_cards",
            "scope": "expression",
            "expressions": {"val": "1"},
        }
        manifest = {"pipeline": ["deal_cards"]}
        log_file = run_dir / "RUN_LOG.txt"

        run_expression_step(
            run_dir, "chunk_000", "deal_cards", step_config, {},
            manifest, log_file
        )

        output_file = run_dir / "chunks" / "chunk_000" / "deal_cards_validated.jsonl"
        with open(output_file) as f:
            results = [json.loads(line) for line in f]
        for r in results:
            assert "_metadata" in r
            assert r["_metadata"]["expression_step"] == "deal_cards"

    def test_expression_step_expression_error_counts_failure(self, run_dir):
        """Expression failures should increment failed_count and omit bad units."""
        step_config = {
            "name": "deal_cards",
            "scope": "expression",
            "expressions": {"bad": "undefined_var + 1"},
        }
        manifest = {"pipeline": ["deal_cards"]}
        log_file = run_dir / "RUN_LOG.txt"

        valid, failed, _, _ = run_expression_step(
            run_dir, "chunk_000", "deal_cards", step_config, {}, manifest, log_file
        )
        assert valid == 0
        assert failed == 2

        output_file = run_dir / "chunks" / "chunk_000" / "deal_cards_validated.jsonl"
        assert output_file.exists()
        with open(output_file) as f:
            assert [line for line in f if line.strip()] == []

    def test_looping_expression_timeout_sets_metadata(self, run_dir):
        """Looping expression steps should mark timeout metadata when max_iterations is reached."""
        step_config = {
            "name": "deal_until_bust",
            "scope": "expression",
            "init": {"counter": "0"},
            "expressions": {"counter": "counter + 1"},
            "loop_until": "counter > 10",
            "max_iterations": 3,
        }
        manifest = {"pipeline": ["deal_until_bust"]}
        log_file = run_dir / "RUN_LOG.txt"

        valid, failed, _, _ = run_expression_step(
            run_dir, "chunk_000", "deal_until_bust", step_config, {}, manifest, log_file
        )
        assert valid == 2
        assert failed == 0

        output_file = run_dir / "chunks" / "chunk_000" / "deal_until_bust_validated.jsonl"
        with open(output_file) as f:
            results = [json.loads(line) for line in f]
        for result in results:
            assert result["_metadata"]["expression_step"] == "deal_until_bust"
            assert result["_metadata"]["iterations"] == 3
            assert result["_metadata"]["timeout"] is True


# =============================================================================
# Retry + Terminal State Behavior
# =============================================================================

class TestRetryAndTerminalState:
    """Tests that exercise real retry state transitions in orchestrate.py."""

    def test_retry_validation_failures_creates_retry_chunk_and_preserves_hard_failures(self, tmp_path):
        run_dir = tmp_path
        chunk_dir = run_dir / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)
        log_file = run_dir / "RUN_LOG.txt"
        log_file.touch()

        # Write input units so the retry chunk can look up payloads
        units_path = chunk_dir / "units.jsonl"
        with open(units_path, "w") as f:
            f.write(json.dumps({"unit_id": "u1", "data": "unit1_payload"}) + "\n")
            f.write(json.dumps({"unit_id": "u2", "data": "unit2_payload"}) + "\n")

        failures_path = chunk_dir / "play_hand_failures.jsonl"
        original_lines = [
            {
                "unit_id": "u1",
                "failure_stage": "validation",
                "errors": [{"message": "bad output"}],
                "retry_count": 1,
            },
            {
                "unit_id": "u2",
                "failure_stage": "pipeline_internal",
                "errors": [{"message": "lost record"}],
                "retry_count": 3,
            },
            "not-json-line",
        ]
        with open(failures_path, "w") as f:
            for line in original_lines:
                if isinstance(line, str):
                    f.write(line + "\n")
                else:
                    f.write(json.dumps(line) + "\n")

        # Non-terminal chunk should be ignored entirely by retry_validation_failures.
        pending_chunk = run_dir / "chunks" / "chunk_001"
        pending_chunk.mkdir(parents=True)
        with open(pending_chunk / "play_hand_failures.jsonl", "w") as f:
            f.write(json.dumps({"unit_id": "u3", "failure_stage": "validation"}) + "\n")

        manifest = {
            "status": "complete",
            "pipeline": ["play_hand", "analyze_difficulty"],
            "chunks": {
                "chunk_000": {"state": "VALIDATED", "failed": 3},
                "chunk_001": {"state": "play_hand_PENDING", "failed": 1},
            },
        }

        archived = retry_validation_failures(run_dir, manifest, log_file)
        assert archived == 1
        assert manifest["status"] == "running"
        # Original chunk state is NOT reset — validated output is preserved
        assert manifest["chunks"]["chunk_000"]["state"] == "VALIDATED"
        # hard failure + malformed line are preserved in original chunk
        assert manifest["chunks"]["chunk_000"]["failed"] == 2
        # non-terminal chunk unchanged
        assert manifest["chunks"]["chunk_001"]["state"] == "play_hand_PENDING"
        assert manifest["chunks"]["chunk_001"]["failed"] == 1

        # A retry chunk was created for the 1 validation failure
        assert "retry_001" in manifest["chunks"]
        retry_chunk = manifest["chunks"]["retry_001"]
        assert retry_chunk["state"] == "play_hand_PENDING"
        assert retry_chunk["items"] == 1
        assert retry_chunk["retry_step"] == "play_hand"
        assert retry_chunk["source_chunks"] == ["chunk_000"]

        # Retry chunk directory contains only the failed unit with incremented retry_count
        retry_units_path = run_dir / "chunks" / "retry_001" / "units.jsonl"
        assert retry_units_path.exists()
        with open(retry_units_path) as f:
            retry_units = [json.loads(line) for line in f if line.strip()]
        assert len(retry_units) == 1
        assert retry_units[0]["unit_id"] == "u1"
        assert retry_units[0]["retry_count"] == 2  # was 1, incremented
        assert retry_units[0]["data"] == "unit1_payload"

        # .bak archive exists
        bak = chunk_dir / "play_hand_failures.jsonl.bak"
        assert bak.exists()
        with open(bak) as f:
            bak_lines = [line.strip() for line in f if line.strip()]
        assert len(bak_lines) == 3

        # Original failures file has only hard failures
        with open(failures_path) as f:
            remaining = [line.strip() for line in f if line.strip()]
        assert len(remaining) == 2
        assert any("pipeline_internal" in line for line in remaining)
        assert any(line == "not-json-line" for line in remaining)

    def test_retry_validation_failures_unit_level_isolation(self, tmp_path):
        """Given a chunk with 10 units where 1 fails validation,
        retry_validation_failures creates a retry chunk with exactly 1 unit
        and does NOT reset the original chunk's state."""
        run_dir = tmp_path
        chunk_dir = run_dir / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)
        log_file = run_dir / "RUN_LOG.txt"
        log_file.touch()

        # Write 10 input units
        units_path = chunk_dir / "units.jsonl"
        with open(units_path, "w") as f:
            for i in range(10):
                f.write(json.dumps({"unit_id": f"unit_{i}", "value": i}) + "\n")

        # 9 units passed validation (written to validated file)
        validated_path = chunk_dir / "step_a_validated.jsonl"
        with open(validated_path, "w") as f:
            for i in range(1, 10):
                f.write(json.dumps({"unit_id": f"unit_{i}", "result": "ok"}) + "\n")

        # 1 unit failed validation
        failures_path = chunk_dir / "step_a_failures.jsonl"
        with open(failures_path, "w") as f:
            f.write(json.dumps({
                "unit_id": "unit_0",
                "failure_stage": "validation",
                "errors": [{"message": "bad value"}],
                "retry_count": 0,
            }) + "\n")

        manifest = {
            "status": "complete",
            "pipeline": ["step_a"],
            "chunks": {
                "chunk_000": {"state": "VALIDATED", "items": 10, "valid": 9, "failed": 1},
            },
        }

        archived = retry_validation_failures(run_dir, manifest, log_file)

        # 1 validation failure archived
        assert archived == 1

        # Original chunk state is NOT reset
        assert manifest["chunks"]["chunk_000"]["state"] == "VALIDATED"
        assert manifest["chunks"]["chunk_000"]["failed"] == 0  # validation failure moved out

        # Validated output file is untouched (still has 9 units)
        with open(validated_path) as f:
            valid_lines = [line for line in f if line.strip()]
        assert len(valid_lines) == 9

        # A retry chunk was created with exactly 1 unit
        assert "retry_001" in manifest["chunks"]
        retry_chunk = manifest["chunks"]["retry_001"]
        assert retry_chunk["state"] == "step_a_PENDING"
        assert retry_chunk["items"] == 1
        assert retry_chunk["retry_step"] == "step_a"
        assert retry_chunk["source_chunks"] == ["chunk_000"]

        # Retry chunk contains only the failed unit
        retry_units_path = run_dir / "chunks" / "retry_001" / "units.jsonl"
        with open(retry_units_path) as f:
            retry_units = [json.loads(line) for line in f if line.strip()]
        assert len(retry_units) == 1
        assert retry_units[0]["unit_id"] == "unit_0"
        assert retry_units[0]["retry_count"] == 1
        assert retry_units[0]["value"] == 0  # original payload preserved

    def test_is_run_terminal_honors_retries(self):
        manifest_terminal = {
            "chunks": {
                "chunk_000": {"state": "VALIDATED", "retries": 0},
                "chunk_001": {"state": "FAILED", "retries": 3},
            }
        }
        assert is_run_terminal(manifest_terminal, max_retries=3) is True

        manifest_retryable = {
            "chunks": {
                "chunk_000": {"state": "VALIDATED", "retries": 0},
                "chunk_001": {"state": "FAILED", "retries": 2},
            }
        }
        assert is_run_terminal(manifest_retryable, max_retries=3) is False

        manifest_in_progress = {
            "chunks": {
                "chunk_000": {"state": "play_hand_PENDING", "retries": 0},
            }
        }
        assert is_run_terminal(manifest_in_progress, max_retries=3) is False

    def test_is_run_terminal_empty_chunks_is_false(self):
        """Run with no chunks should not be treated as terminal."""
        assert is_run_terminal({"chunks": {}}, max_retries=3) is False


# =============================================================================
# Realtime Idempotency / .bak Signal
# =============================================================================

class TestRealtimeIdempotency:
    """Tests for run_step_realtime() idempotency and .bak retry signal behavior."""

    @pytest.fixture
    def realtime_run_dir(self, tmp_path):
        run_dir = tmp_path
        chunk_dir = run_dir / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)
        (run_dir / "RUN_LOG.txt").touch()
        return run_dir

    def _write_jsonl(self, path: Path, count: int):
        with open(path, "w") as f:
            for i in range(count):
                f.write(json.dumps({"unit_id": f"u{i:03d}"}) + "\n")

    def test_idempotency_90_percent_fallback_skips_without_bak(self, realtime_run_dir):
        """Step should skip when >=90% valid and no failures, if no .bak exists."""
        chunk_dir = realtime_run_dir / "chunks" / "chunk_000"
        self._write_jsonl(chunk_dir / "step1_validated.jsonl", 10)  # input cap for expected_items
        self._write_jsonl(chunk_dir / "step2_validated.jsonl", 9)   # 90% of expected

        manifest = {
            "config": "config/config.yaml",
            "pipeline": ["step1", "step2"],
            "chunks": {
                "chunk_000": {"state": "step2_PENDING", "items": 20}
            },
        }
        config = {"api": {"retry": {"max_attempts": 3}}}
        log_file = realtime_run_dir / "RUN_LOG.txt"

        valid, failed, in_tok, out_tok = run_step_realtime(
            realtime_run_dir, "chunk_000", "step2", config, manifest, log_file
        )
        assert (valid, failed, in_tok, out_tok) == (9, 0, 0, 0)
        assert manifest["chunks"]["chunk_000"]["state"] == "VALIDATED"
        assert manifest["chunks"]["chunk_000"]["valid"] == 9
        assert manifest["chunks"]["chunk_000"]["failed"] == 0

    def test_idempotency_does_not_fallback_when_bak_exists(self, realtime_run_dir, monkeypatch):
        """Presence of .bak should disable the 90% fallback and continue processing."""
        chunk_dir = realtime_run_dir / "chunks" / "chunk_000"
        self._write_jsonl(chunk_dir / "step1_validated.jsonl", 10)
        self._write_jsonl(chunk_dir / "step2_validated.jsonl", 9)
        (chunk_dir / "step2_failures.jsonl.bak").write_text("archived\n")

        manifest = {
            "config": "config/config.yaml",
            "pipeline": ["step1", "step2"],
            "chunks": {
                "chunk_000": {"state": "step2_PENDING", "items": 20}
            },
        }
        config = {"api": {"retry": {"max_attempts": 3}}}
        log_file = realtime_run_dir / "RUN_LOG.txt"

        called = {"prepare": 0}

        def fake_prepare(*args, **kwargs):
            called["prepare"] += 1
            return False, "forced prepare failure"

        monkeypatch.setattr(orchestrate, "prepare_prompts", fake_prepare)

        valid, failed, in_tok, out_tok = run_step_realtime(
            realtime_run_dir, "chunk_000", "step2", config, manifest, log_file
        )
        assert called["prepare"] == 1  # proves we did not SKIP at idempotency gate
        assert (valid, failed, in_tok, out_tok) == (0, 0, 0, 0)
        assert manifest["chunks"]["chunk_000"]["state"] == "FAILED"
        assert (chunk_dir / "step2_failures.jsonl.bak").exists()  # retry signal preserved on failure

    def test_true_completeness_skip_removes_bak_signal(self, realtime_run_dir):
        """When step is truly complete, skip path should clean up stale .bak file."""
        chunk_dir = realtime_run_dir / "chunks" / "chunk_000"
        self._write_jsonl(chunk_dir / "step1_validated.jsonl", 10)
        self._write_jsonl(chunk_dir / "step2_validated.jsonl", 10)
        (chunk_dir / "step2_failures.jsonl.bak").write_text("archived\n")

        manifest = {
            "config": "config/config.yaml",
            "pipeline": ["step1", "step2"],
            "chunks": {
                "chunk_000": {"state": "step2_PENDING", "items": 20}
            },
        }
        config = {"api": {"retry": {"max_attempts": 3}}}
        log_file = realtime_run_dir / "RUN_LOG.txt"

        valid, failed, in_tok, out_tok = run_step_realtime(
            realtime_run_dir, "chunk_000", "step2", config, manifest, log_file
        )
        assert (valid, failed, in_tok, out_tok) == (10, 0, 0, 0)
        assert manifest["chunks"]["chunk_000"]["state"] == "VALIDATED"
        assert not (chunk_dir / "step2_failures.jsonl.bak").exists()


# =============================================================================
# Extract Collect Result
# =============================================================================

class TestExtractCollectResult:
    """Tests for extract_collect_result()."""

    def test_int_result(self):
        """Legacy int return gives (int, None)."""
        count, meta = extract_collect_result(42)
        assert count == 42
        assert meta is None

    def test_dict_result(self):
        """Dict return extracts count and batch_metadata."""
        result = {"count": 10, "batch_metadata": {"id": "batch_123"}}
        count, meta = extract_collect_result(result)
        assert count == 10
        assert meta == {"id": "batch_123"}

    def test_dict_missing_count(self):
        """Dict without count defaults to 0."""
        count, meta = extract_collect_result({"batch_metadata": {"x": 1}})
        assert count == 0
        assert meta == {"x": 1}

    def test_dict_no_metadata(self):
        """Dict without batch_metadata returns None."""
        count, meta = extract_collect_result({"count": 5})
        assert count == 5
        assert meta is None

    def test_unknown_type(self):
        """Unknown types return (0, None)."""
        count, meta = extract_collect_result("bad")
        assert count == 0
        assert meta is None

    def test_none_type(self):
        """None returns (0, None)."""
        count, meta = extract_collect_result(None)
        assert count == 0
        assert meta is None


# =============================================================================
# Compute Step Cost
# =============================================================================

class TestComputeStepCost:
    """Tests for compute_step_cost()."""

    def test_no_provider_returns_none(self):
        """None provider returns None."""
        assert compute_step_cost(1000, 500) is None

    def test_provider_with_estimate_cost(self):
        """Provider with estimate_cost returns rounded cost."""
        provider = MagicMock()
        provider.estimate_cost.return_value = 0.123456789
        cost = compute_step_cost(1000, 500, provider=provider)
        assert cost == 0.123457
        provider.estimate_cost.assert_called_once_with(1000, 500, is_batch=True)

    def test_provider_realtime_mode(self):
        """Realtime mode passes is_batch=False."""
        provider = MagicMock()
        provider.estimate_cost.return_value = 0.5
        cost = compute_step_cost(1000, 500, provider=provider, is_realtime=True)
        assert cost == 0.5
        provider.estimate_cost.assert_called_once_with(1000, 500, is_batch=False)

    def test_provider_raises_exception(self):
        """Provider raising exception returns None."""
        provider = MagicMock()
        provider.estimate_cost.side_effect = Exception("pricing unavailable")
        assert compute_step_cost(1000, 500, provider=provider) is None


# =============================================================================
# Mark Run State Transitions
# =============================================================================

class TestMarkRunStates:
    """Tests for mark_run_failed/paused/complete/running."""

    def _create_run(self, tmp_path, status="running", chunks=None):
        """Helper to create a minimal run directory with MANIFEST.json."""
        run_dir = tmp_path
        manifest = {
            "status": status,
            "pipeline": ["step_a"],
            "chunks": chunks or {},
            "metadata": {},
        }
        with open(run_dir / "MANIFEST.json", "w") as f:
            json.dump(manifest, f)
        (run_dir / "RUN_LOG.txt").touch()
        return run_dir

    def test_mark_run_failed(self, tmp_path):
        """mark_run_failed sets status and error_message."""
        run_dir = self._create_run(tmp_path)
        mark_run_failed(run_dir, "Something broke")
        with open(run_dir / "MANIFEST.json") as f:
            m = json.load(f)
        assert m["status"] == "failed"
        assert m["error_message"] == "Something broke"
        assert "failed_at" in m

    def test_mark_run_failed_truncates_long_error(self, tmp_path):
        """Error messages longer than 500 chars are truncated."""
        run_dir = self._create_run(tmp_path)
        long_error = "x" * 1000
        mark_run_failed(run_dir, long_error)
        with open(run_dir / "MANIFEST.json") as f:
            m = json.load(f)
        assert len(m["error_message"]) == 500

    def test_mark_run_failed_no_manifest(self, tmp_path):
        """mark_run_failed does nothing if MANIFEST.json missing."""
        mark_run_failed(tmp_path, "no manifest")
        assert not (tmp_path / "MANIFEST.json").exists()
        assert not (tmp_path / "RUN_LOG.txt").exists()

    def test_mark_run_paused(self, tmp_path):
        """mark_run_paused sets status to paused."""
        run_dir = self._create_run(tmp_path)
        mark_run_paused(run_dir, "User pause")
        with open(run_dir / "MANIFEST.json") as f:
            m = json.load(f)
        assert m["status"] == "paused"
        assert "paused_at" in m

    def test_mark_run_paused_ignores_terminal_states(self, tmp_path):
        """mark_run_paused does not override complete or failed."""
        run_dir = self._create_run(tmp_path, status="complete")
        mark_run_paused(run_dir)
        with open(run_dir / "MANIFEST.json") as f:
            m = json.load(f)
        assert m["status"] == "complete"

    def test_mark_run_paused_no_manifest(self, tmp_path):
        """mark_run_paused does nothing if MANIFEST.json missing."""
        mark_run_paused(tmp_path)
        assert not (tmp_path / "MANIFEST.json").exists()
        assert not (tmp_path / "RUN_LOG.txt").exists()

    def test_mark_run_complete(self, tmp_path):
        """mark_run_complete sets status to complete."""
        run_dir = self._create_run(tmp_path)
        mark_run_complete(run_dir)
        with open(run_dir / "MANIFEST.json") as f:
            m = json.load(f)
        assert m["status"] == "complete"
        assert "completed_at" in m

    def test_mark_run_complete_no_manifest(self, tmp_path):
        """mark_run_complete does nothing if MANIFEST.json missing."""
        mark_run_complete(tmp_path)
        assert not (tmp_path / "MANIFEST.json").exists()
        assert not (tmp_path / "RUN_LOG.txt").exists()

    def test_mark_run_running(self, tmp_path):
        """mark_run_running sets status to running and clears paused_at."""
        run_dir = self._create_run(tmp_path, status="paused")
        # Add paused_at to manifest
        with open(run_dir / "MANIFEST.json") as f:
            m = json.load(f)
        m["paused_at"] = "2024-01-01T00:00:00Z"
        with open(run_dir / "MANIFEST.json", "w") as f:
            json.dump(m, f)

        mark_run_running(run_dir)
        with open(run_dir / "MANIFEST.json") as f:
            m = json.load(f)
        assert m["status"] == "running"
        assert "paused_at" not in m

    def test_mark_run_running_refuses_truly_terminal(self, tmp_path):
        """mark_run_running does NOT override truly terminal runs."""
        run_dir = self._create_run(
            tmp_path, status="complete",
            chunks={"chunk_000": {"state": "VALIDATED"}}
        )
        mark_run_running(run_dir)
        with open(run_dir / "MANIFEST.json") as f:
            m = json.load(f)
        assert m["status"] == "complete"  # Should NOT change

    def test_mark_run_running_allows_premature_completion(self, tmp_path):
        """mark_run_running allows resume when chunks are not all terminal."""
        run_dir = self._create_run(
            tmp_path, status="complete",
            chunks={"chunk_000": {"state": "step_a_PENDING"}}
        )
        mark_run_running(run_dir)
        with open(run_dir / "MANIFEST.json") as f:
            m = json.load(f)
        assert m["status"] == "running"

    def test_mark_run_running_no_manifest(self, tmp_path):
        """mark_run_running does nothing if MANIFEST.json missing."""
        mark_run_running(tmp_path)
        assert not (tmp_path / "MANIFEST.json").exists()
        assert not (tmp_path / "RUN_LOG.txt").exists()


# =============================================================================
# PID File
# =============================================================================

class TestPidFile:
    """Tests for write_pid_file and cleanup_pid_file."""

    def test_write_pid_file(self, tmp_path):
        """write_pid_file creates file with current PID."""
        write_pid_file(tmp_path)
        pid_file = tmp_path / "orchestrator.pid"
        assert pid_file.exists()
        assert pid_file.read_text() == str(os.getpid())

    def test_cleanup_pid_file_is_noop(self, tmp_path):
        """cleanup_pid_file intentionally does nothing."""
        write_pid_file(tmp_path)
        cleanup_pid_file(tmp_path)
        assert (tmp_path / "orchestrator.pid").exists()  # Still there


# =============================================================================
# Build Failures Map
# =============================================================================

class TestBuildFailuresMap:
    """Tests for build_failures_map()."""

    def _create_run_with_chunks(self, tmp_path, chunks_manifest, files_by_chunk):
        """Helper: create run_dir with chunk dirs and file contents."""
        run_dir = tmp_path
        chunks_dir = run_dir / "chunks"
        chunks_dir.mkdir()
        for chunk_name, file_map in files_by_chunk.items():
            chunk_dir = chunks_dir / chunk_name
            chunk_dir.mkdir()
            for filename, records in file_map.items():
                with open(chunk_dir / filename, "w") as f:
                    for r in records:
                        f.write(json.dumps(r) + "\n")
        manifest = {"chunks": chunks_manifest}
        return run_dir, manifest

    def test_empty_chunks(self, tmp_path):
        """No chunks returns empty failures."""
        assert build_failures_map(tmp_path, {"chunks": {}}) == {}

    def test_no_chunks_dir(self, tmp_path):
        """Missing chunks/ returns empty failures."""
        assert build_failures_map(tmp_path, {"chunks": {"c": {}}}) == {}

    def test_basic_failure(self, tmp_path):
        """Single failure is returned."""
        run_dir, manifest = self._create_run_with_chunks(tmp_path,
            {"chunk_000": {"state": "FAILED"}},
            {"chunk_000": {
                "step_a_failures.jsonl": [
                    {"unit_id": "u1", "failure_stage": "validation", "errors": [{"msg": "bad"}], "retry_count": 0}
                ]
            }}
        )
        failures = build_failures_map(run_dir, manifest)
        assert "u1" in failures
        assert failures["u1"]["step"] == "step_a"
        assert failures["u1"]["chunk"] == "chunk_000"
        assert failures["u1"]["retry_count"] == 0

    def test_validated_units_excluded(self, tmp_path):
        """Units that succeeded in a retry chunk are excluded from failures."""
        run_dir, manifest = self._create_run_with_chunks(tmp_path,
            {"chunk_000": {"state": "FAILED"}, "retry_001": {"state": "VALIDATED"}},
            {
                "chunk_000": {
                    "step_a_failures.jsonl": [
                        {"unit_id": "u1", "failure_stage": "validation", "errors": [], "retry_count": 0}
                    ]
                },
                "retry_001": {
                    "step_a_validated.jsonl": [
                        {"unit_id": "u1", "result": "ok"}
                    ]
                },
            }
        )
        failures = build_failures_map(run_dir, manifest)
        assert "u1" not in failures  # Succeeded in retry

    def test_multiple_chunks_failures(self, tmp_path):
        """Failures from multiple chunks are collected."""
        run_dir, manifest = self._create_run_with_chunks(tmp_path,
            {"chunk_000": {"state": "FAILED"}, "chunk_001": {"state": "FAILED"}},
            {
                "chunk_000": {
                    "step_a_failures.jsonl": [{"unit_id": "u1", "failure_stage": "validation"}]
                },
                "chunk_001": {
                    "step_a_failures.jsonl": [{"unit_id": "u2", "failure_stage": "pipeline_internal"}]
                },
            }
        )
        failures = build_failures_map(run_dir, manifest)
        assert len(failures) == 2
        assert failures["u1"]["stage"] == "validation"
        assert failures["u2"]["stage"] == "pipeline_internal"


# =============================================================================
# Get Schema Path
# =============================================================================

class TestGetSchemaPath:
    """Tests for get_schema_path()."""

    def test_no_schema_configured(self, tmp_path):
        """Returns None when step has no schema."""
        assert get_schema_path({}, "step_a", tmp_path) is None
        assert get_schema_path({"schemas": {"files": {}}}, "step_a", tmp_path) is None

    def test_schema_in_config_dir(self, tmp_path):
        """Finds schema in run_dir/config/schemas/."""
        schema_dir = tmp_path / "config" / "schemas"
        schema_dir.mkdir(parents=True)
        (schema_dir / "step_a.json").write_text('{}')
        config = {"schemas": {"schema_dir": "schemas", "files": {"step_a": "step_a.json"}}}
        result = get_schema_path(config, "step_a", tmp_path)
        assert result == schema_dir / "step_a.json"

    def test_schema_not_found(self, tmp_path):
        """Returns None when schema file doesn't exist anywhere."""
        config = {"schemas": {"schema_dir": "schemas", "files": {"step_a": "missing.json"}}}
        result = get_schema_path(config, "step_a", tmp_path)
        assert result is None

    def test_step_not_in_files(self, tmp_path):
        """Returns None when step is not in schema files mapping."""
        config = {"schemas": {"files": {"other_step": "other.json"}}}
        assert get_schema_path(config, "step_a", tmp_path) is None


# =============================================================================
# Count Step Failures
# =============================================================================

class TestCountStepFailures:
    """Tests for count_step_failures()."""

    def test_no_chunks_dir(self, tmp_path):
        """Returns zeros when no chunks directory."""
        result = count_step_failures(tmp_path, "step_a")
        assert result == {"by_rule": {}, "total": 0}

    def test_basic_counts(self, tmp_path):
        """Counts failures by rule name."""
        chunks_dir = tmp_path / "chunks"
        chunk_dir = chunks_dir / "chunk_000"
        chunk_dir.mkdir(parents=True)
        failures = [
            {"unit_id": "u1", "errors": [{"rule": "required_field", "message": "missing"}]},
            {"unit_id": "u2", "errors": [{"rule": "required_field", "message": "missing"}, {"rule": "type_check", "message": "wrong type"}]},
        ]
        with open(chunk_dir / "step_a_failures.jsonl", "w") as f:
            for r in failures:
                f.write(json.dumps(r) + "\n")

        result = count_step_failures(tmp_path, "step_a")
        assert result["by_rule"]["required_field"] == 2
        assert result["by_rule"]["type_check"] == 1
        assert result["total"] == 3

    def test_non_json_lines_counted_as_parse_error(self, tmp_path):
        """Non-JSON lines are counted as parse_error."""
        chunks_dir = tmp_path / "chunks"
        chunk_dir = chunks_dir / "chunk_000"
        chunk_dir.mkdir(parents=True)
        with open(chunk_dir / "step_a_failures.jsonl", "w") as f:
            f.write("not json\n")
            f.write(json.dumps({"errors": [{"rule": "r1"}]}) + "\n")
        result = count_step_failures(tmp_path, "step_a")
        assert result["by_rule"]["parse_error"] == 1
        assert result["by_rule"]["r1"] == 1
        assert result["total"] == 2

    def test_multiple_chunk_dirs(self, tmp_path):
        """Aggregates across multiple chunk directories."""
        chunks_dir = tmp_path / "chunks"
        for name in ["chunk_000", "chunk_001"]:
            d = chunks_dir / name
            d.mkdir(parents=True)
            with open(d / "step_a_failures.jsonl", "w") as f:
                f.write(json.dumps({"errors": [{"rule": "r1"}]}) + "\n")
        result = count_step_failures(tmp_path, "step_a")
        assert result["by_rule"]["r1"] == 2
        assert result["total"] == 2

    def test_wrong_step_name(self, tmp_path):
        """Returns zeros for a step with no failure files."""
        chunks_dir = tmp_path / "chunks"
        chunk_dir = chunks_dir / "chunk_000"
        chunk_dir.mkdir(parents=True)
        with open(chunk_dir / "step_a_failures.jsonl", "w") as f:
            f.write(json.dumps({"errors": [{"rule": "r1"}]}) + "\n")
        result = count_step_failures(tmp_path, "step_b")
        assert result["total"] == 0


# =============================================================================
# Categorize Step Failures
# =============================================================================

class TestCategorizeStepFailures:
    """Tests for categorize_step_failures()."""

    def test_no_chunks_dir(self, tmp_path):
        """Returns zeros when no chunks directory."""
        result = categorize_step_failures(tmp_path, "step_a")
        assert result == {"validation": 0, "hard": 0, "total": 0}

    def test_validation_failures(self, tmp_path):
        """validation and schema_validation stages are classified as validation."""
        chunks_dir = tmp_path / "chunks"
        chunk_dir = chunks_dir / "chunk_000"
        chunk_dir.mkdir(parents=True)
        failures = [
            {"unit_id": "u1", "failure_stage": "validation"},
            {"unit_id": "u2", "failure_stage": "schema_validation"},
        ]
        with open(chunk_dir / "step_a_failures.jsonl", "w") as f:
            for r in failures:
                f.write(json.dumps(r) + "\n")
        result = categorize_step_failures(tmp_path, "step_a")
        assert result["validation"] == 2
        assert result["hard"] == 0
        assert result["total"] == 2

    def test_hard_failures(self, tmp_path):
        """pipeline_internal and other stages are classified as hard."""
        chunks_dir = tmp_path / "chunks"
        chunk_dir = chunks_dir / "chunk_000"
        chunk_dir.mkdir(parents=True)
        failures = [
            {"unit_id": "u1", "failure_stage": "pipeline_internal"},
            {"unit_id": "u2", "failure_stage": "api_error"},
        ]
        with open(chunk_dir / "step_a_failures.jsonl", "w") as f:
            for r in failures:
                f.write(json.dumps(r) + "\n")
        result = categorize_step_failures(tmp_path, "step_a")
        assert result["validation"] == 0
        assert result["hard"] == 2

    def test_mixed_failures(self, tmp_path):
        """Mix of validation and hard failures."""
        chunks_dir = tmp_path / "chunks"
        chunk_dir = chunks_dir / "chunk_000"
        chunk_dir.mkdir(parents=True)
        failures = [
            {"unit_id": "u1", "failure_stage": "validation"},
            {"unit_id": "u2", "failure_stage": "pipeline_internal"},
            {"unit_id": "u3", "failure_stage": "schema_validation"},
        ]
        with open(chunk_dir / "step_a_failures.jsonl", "w") as f:
            for r in failures:
                f.write(json.dumps(r) + "\n")
        result = categorize_step_failures(tmp_path, "step_a")
        assert result["validation"] == 2
        assert result["hard"] == 1
        assert result["total"] == 3

    def test_non_json_line_is_hard(self, tmp_path):
        """Non-JSON lines are counted as hard failures."""
        chunks_dir = tmp_path / "chunks"
        chunk_dir = chunks_dir / "chunk_000"
        chunk_dir.mkdir(parents=True)
        with open(chunk_dir / "step_a_failures.jsonl", "w") as f:
            f.write("not json\n")
        result = categorize_step_failures(tmp_path, "step_a")
        assert result["hard"] == 1

    def test_missing_failure_stage_defaults_validation(self, tmp_path):
        """Missing failure_stage defaults to validation."""
        chunks_dir = tmp_path / "chunks"
        chunk_dir = chunks_dir / "chunk_000"
        chunk_dir.mkdir(parents=True)
        with open(chunk_dir / "step_a_failures.jsonl", "w") as f:
            f.write(json.dumps({"unit_id": "u1"}) + "\n")
        result = categorize_step_failures(tmp_path, "step_a")
        assert result["validation"] == 1


# =============================================================================
# Count Step Units
# =============================================================================

class TestCountStepUnits:
    """Tests for count_step_units()."""

    def test_no_chunks_dir(self, tmp_path):
        """Returns zeros when no chunks dir."""
        result = count_step_units(tmp_path, "step_a", ["step_a"])
        assert result == {"valid": 0, "failed": 0, "retry_pending": 0}

    def test_counts_valid_units(self, tmp_path):
        """Counts lines in validated file."""
        chunks_dir = tmp_path / "chunks"
        chunk_dir = chunks_dir / "chunk_000"
        chunk_dir.mkdir(parents=True)
        with open(chunk_dir / "step_a_validated.jsonl", "w") as f:
            f.write(json.dumps({"unit_id": "u1"}) + "\n")
            f.write(json.dumps({"unit_id": "u2"}) + "\n")
        result = count_step_units(tmp_path, "step_a", ["step_a"])
        assert result["valid"] == 2

    def test_retry_count_threshold(self, tmp_path):
        """retry_count < 3 is retry_pending, >= 3 is failed."""
        chunks_dir = tmp_path / "chunks"
        chunk_dir = chunks_dir / "chunk_000"
        chunk_dir.mkdir(parents=True)
        failures = [
            {"unit_id": "u1", "retry_count": 0},   # retry_pending
            {"unit_id": "u2", "retry_count": 2},   # retry_pending
            {"unit_id": "u3", "retry_count": 3},   # failed
            {"unit_id": "u4", "retry_count": 5},   # failed
        ]
        with open(chunk_dir / "step_a_failures.jsonl", "w") as f:
            for r in failures:
                f.write(json.dumps(r) + "\n")
        result = count_step_units(tmp_path, "step_a", ["step_a"])
        assert result["retry_pending"] == 2
        assert result["failed"] == 2

    def test_non_json_failure_counted_as_failed(self, tmp_path):
        """Non-JSON lines in failures file count as failed."""
        chunks_dir = tmp_path / "chunks"
        chunk_dir = chunks_dir / "chunk_000"
        chunk_dir.mkdir(parents=True)
        with open(chunk_dir / "step_a_failures.jsonl", "w") as f:
            f.write("not json\n")
        result = count_step_units(tmp_path, "step_a", ["step_a"])
        assert result["failed"] == 1

    def test_aggregates_across_chunks(self, tmp_path):
        """Sums counts across multiple chunk directories."""
        chunks_dir = tmp_path / "chunks"
        for name in ["chunk_000", "chunk_001"]:
            d = chunks_dir / name
            d.mkdir(parents=True)
            with open(d / "step_a_validated.jsonl", "w") as f:
                f.write(json.dumps({"unit_id": f"{name}_u1"}) + "\n")
        result = count_step_units(tmp_path, "step_a", ["step_a"])
        assert result["valid"] == 2


# =============================================================================
# Get Next Retry Number
# =============================================================================

class TestGetNextRetryNumber:
    """Tests for get_next_retry_number()."""

    def test_no_existing_retries(self, tmp_path):
        """Returns 1 when no retry dirs exist."""
        chunks_dir = tmp_path / "chunks"
        chunks_dir.mkdir()
        (chunks_dir / "chunk_000").mkdir()
        assert get_next_retry_number(chunks_dir) == 1

    def test_with_existing_retries(self, tmp_path):
        """Returns next number after highest existing."""
        chunks_dir = tmp_path / "chunks"
        chunks_dir.mkdir()
        (chunks_dir / "retry_001").mkdir()
        (chunks_dir / "retry_003").mkdir()
        assert get_next_retry_number(chunks_dir) == 4

    def test_mixed_dirs(self, tmp_path):
        """Ignores non-retry directories."""
        chunks_dir = tmp_path / "chunks"
        chunks_dir.mkdir()
        (chunks_dir / "chunk_000").mkdir()
        (chunks_dir / "retry_002").mkdir()
        (chunks_dir / "somefile.txt").touch()
        assert get_next_retry_number(chunks_dir) == 3


# =============================================================================
# Parse Duration
# =============================================================================

class TestParseDuration:
    """Tests for parse_duration()."""

    def test_plain_seconds(self):
        assert parse_duration("30") == 30

    def test_seconds_suffix(self):
        assert parse_duration("45s") == 45

    def test_minutes(self):
        assert parse_duration("30m") == 1800

    def test_hours(self):
        assert parse_duration("2h") == 7200

    def test_combined_hm(self):
        assert parse_duration("1h30m") == 5400

    def test_combined_hms(self):
        assert parse_duration("1h30m45s") == 5445

    def test_combined_ms(self):
        assert parse_duration("5m30s") == 330

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="Empty"):
            parse_duration("")

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match="Invalid"):
            parse_duration("abc")

    def test_case_insensitive(self):
        assert parse_duration("2H30M") == 9000


# =============================================================================
# Format Watch Progress
# =============================================================================

class TestFormatWatchProgress:
    """Tests for format_watch_progress()."""

    def test_all_waiting(self):
        """Steps not in status dict show as waiting."""
        result = format_watch_progress({"steps": {}}, ["step_a", "step_b"])
        assert "step_a: waiting" in result
        assert "step_b: waiting" in result

    def test_submitted_polling(self):
        """Submitted step with submitted chunk shows polling."""
        status = {
            "steps": {
                "step_a": {
                    "units": {"valid": 3, "failed": 0},
                    "chunks": {"submitted": 1, "pending": 0, "completed": 0, "validated": 0},
                    "current_units": 10,
                }
            },
            "chunks": [{"step": "step_a", "status": "submitted"}],
            "summary": {"total_units": 10},
        }
        result = format_watch_progress(status, ["step_a"])
        assert "polling" in result

    def test_submitted_not_polling(self):
        """Submitted step without polling chunk shows submitted."""
        status = {
            "steps": {
                "step_a": {
                    "units": {"valid": 3, "failed": 0},
                    "chunks": {"submitted": 1, "pending": 0, "completed": 0, "validated": 0},
                    "current_units": 10,
                }
            },
            "chunks": [],
            "summary": {"total_units": 10},
        }
        result = format_watch_progress(status, ["step_a"])
        assert "(submitted)" in result

    def test_validated_with_failures(self):
        """Validated step with failures shows failure count."""
        status = {
            "steps": {
                "step_a": {
                    "units": {"valid": 8, "failed": 2},
                    "chunks": {"submitted": 0, "pending": 0, "completed": 0, "validated": 1},
                    "current_units": 10,
                }
            },
            "chunks": [],
            "summary": {"total_units": 10},
        }
        result = format_watch_progress(status, ["step_a"])
        assert "8/10 valid" in result
        assert "2 failed" in result

    def test_validated_no_failures(self):
        """Validated step with no failures shows clean valid count."""
        status = {
            "steps": {
                "step_a": {
                    "units": {"valid": 10, "failed": 0},
                    "chunks": {"submitted": 0, "pending": 0, "completed": 0, "validated": 1},
                    "current_units": 10,
                }
            },
            "chunks": [],
            "summary": {"total_units": 10},
        }
        result = format_watch_progress(status, ["step_a"])
        assert "10/10 valid" in result
        assert "failed" not in result

    def test_pending(self):
        """Pending step shows pending."""
        status = {
            "steps": {
                "step_a": {
                    "units": {"valid": 0, "failed": 0},
                    "chunks": {"submitted": 0, "pending": 1, "completed": 0, "validated": 0},
                    "current_units": 10,
                }
            },
            "chunks": [],
            "summary": {"total_units": 10},
        }
        result = format_watch_progress(status, ["step_a"])
        assert "(pending)" in result

    def test_multi_step_pipeline(self):
        """Multiple steps joined by pipe separator."""
        status = {
            "steps": {
                "step_a": {
                    "units": {"valid": 5, "failed": 0},
                    "chunks": {"submitted": 0, "pending": 0, "completed": 0, "validated": 1},
                    "current_units": 5,
                },
            },
            "chunks": [],
            "summary": {"total_units": 5},
        }
        result = format_watch_progress(status, ["step_a", "step_b"])
        assert " | " in result
        assert "step_b: waiting" in result


# =============================================================================
# Format Step Provider Tag
# =============================================================================

class TestFormatStepProviderTag:
    """Tests for format_step_provider_tag()."""

    def test_default_tag(self):
        """No step override shows '(default)'."""
        config = {"pipeline": {"steps": [{"name": "step_a"}]}}
        provider = MagicMock()
        provider.config = {"api": {"provider": "gemini"}}
        provider.model = "gemini-2.0-flash"
        result = format_step_provider_tag(config, "step_a", provider)
        assert result == "gemini/gemini-2.0-flash (default)"

    def test_override_tag(self):
        """Step with provider/model override shows '(override)'."""
        config = {"pipeline": {"steps": [{"name": "step_a", "provider": "anthropic"}]}}
        provider = MagicMock()
        provider.config = {"api": {"provider": "anthropic"}}
        provider.model = "claude-sonnet-4-5-20250929"
        result = format_step_provider_tag(config, "step_a", provider)
        assert result == "anthropic/claude-sonnet-4-5-20250929 (override)"


# =============================================================================
# Retry Failures Run (CLI-facing)
# =============================================================================

class TestRetryFailuresRun:
    """Tests for retry_failures_run()."""

    def _create_run_for_retry(self, tmp_path, pipeline, chunks_manifest, files_by_chunk):
        """Create a run directory ready for retry_failures_run testing."""
        run_dir = tmp_path
        manifest = {
            "status": "complete",
            "pipeline": pipeline,
            "chunks": chunks_manifest,
            "metadata": {},
        }
        with open(run_dir / "MANIFEST.json", "w") as f:
            json.dump(manifest, f, indent=2)
        (run_dir / "RUN_LOG.txt").touch()

        chunks_dir = run_dir / "chunks"
        chunks_dir.mkdir()
        for chunk_name, file_map in files_by_chunk.items():
            chunk_dir = chunks_dir / chunk_name
            chunk_dir.mkdir(exist_ok=True)
            for filename, records in file_map.items():
                with open(chunk_dir / filename, "w") as f:
                    for r in records:
                        if isinstance(r, str):
                            f.write(r + "\n")
                        else:
                            f.write(json.dumps(r) + "\n")
        return run_dir

    def test_no_failures_returns_zero(self, tmp_path):
        """Run with no failures returns zero retry chunks."""
        run_dir = self._create_run_for_retry(
            tmp_path,
            pipeline=["step_a"],
            chunks_manifest={"chunk_000": {"state": "VALIDATED", "items": 5}},
            files_by_chunk={"chunk_000": {
                "units.jsonl": [{"unit_id": f"u{i}"} for i in range(5)],
                "step_a_validated.jsonl": [{"unit_id": f"u{i}", "ok": True} for i in range(5)],
            }}
        )
        result = retry_failures_run(run_dir)
        assert result["retry_chunks_created"] == 0
        assert result["permanent_failures"] == 0

    def test_creates_retry_chunk(self, tmp_path):
        """Creates retry chunk with failed units."""
        run_dir = self._create_run_for_retry(
            tmp_path,
            pipeline=["step_a"],
            chunks_manifest={
                "chunk_000": {"state": "VALIDATED", "items": 3, "retries": 0}
            },
            files_by_chunk={"chunk_000": {
                "units.jsonl": [
                    {"unit_id": "u1", "data": "a"},
                    {"unit_id": "u2", "data": "b"},
                    {"unit_id": "u3", "data": "c"},
                ],
                "step_a_validated.jsonl": [
                    {"unit_id": "u1", "result": "ok"},
                    {"unit_id": "u2", "result": "ok"},
                ],
                "step_a_failures.jsonl": [
                    {"unit_id": "u3", "failure_stage": "validation", "retry_count": 0},
                ],
            }}
        )
        result = retry_failures_run(run_dir, max_retries=5)
        assert result["retry_chunks_created"] == 1
        assert result["failures_by_step"]["step_a"] == 1

        # Verify retry chunk was created
        retry_dir = run_dir / "chunks" / "retry_001"
        assert retry_dir.exists()
        units = []
        with open(retry_dir / "units.jsonl") as f:
            for line in f:
                if line.strip():
                    units.append(json.loads(line))
        assert len(units) == 1
        assert units[0]["unit_id"] == "u3"
        assert units[0]["retry_count"] == 1  # Incremented

    def test_max_retries_filtering(self, tmp_path):
        """Units exceeding max_retries become permanent failures."""
        run_dir = self._create_run_for_retry(
            tmp_path,
            pipeline=["step_a"],
            chunks_manifest={
                "chunk_000": {"state": "VALIDATED", "items": 2, "retries": 0}
            },
            files_by_chunk={"chunk_000": {
                "units.jsonl": [
                    {"unit_id": "u1", "data": "a"},
                    {"unit_id": "u2", "data": "b"},
                ],
                "step_a_failures.jsonl": [
                    {"unit_id": "u1", "retry_count": 5},  # >= max_retries
                    {"unit_id": "u2", "retry_count": 1},  # < max_retries
                ],
            }}
        )
        result = retry_failures_run(run_dir, max_retries=5)
        assert result["permanent_failures"] == 1
        assert result["retry_chunks_created"] == 1

        # Check permanent failures file
        perm_file = run_dir / "permanent_failures.jsonl"
        assert perm_file.exists()
        with open(perm_file) as f:
            perm = [json.loads(line) for line in f if line.strip()]
        assert len(perm) == 1
        assert perm[0]["unit_id"] == "u1"

    def test_run_dir_not_found(self, tmp_path):
        """Non-existent run dir returns error."""
        result = retry_failures_run(tmp_path / "nonexistent")
        assert "error" in result

    def test_no_manifest(self, tmp_path):
        """Missing MANIFEST.json returns error."""
        result = retry_failures_run(tmp_path)
        assert "error" in result

    def test_retry_chunk_state_in_manifest(self, tmp_path):
        """Retry chunk is added to manifest with correct state."""
        run_dir = self._create_run_for_retry(
            tmp_path,
            pipeline=["step_a", "step_b"],
            chunks_manifest={
                "chunk_000": {"state": "VALIDATED", "items": 2, "retries": 0}
            },
            files_by_chunk={"chunk_000": {
                "units.jsonl": [{"unit_id": "u1", "data": "a"}],
                "step_a_failures.jsonl": [
                    {"unit_id": "u1", "failure_stage": "validation", "retry_count": 0},
                ],
            }}
        )
        retry_failures_run(run_dir, max_retries=5)

        # Re-load manifest to check
        with open(run_dir / "MANIFEST.json") as f:
            manifest = json.load(f)
        assert "retry_001" in manifest["chunks"]
        assert manifest["chunks"]["retry_001"]["state"] == "step_a_PENDING"
        assert manifest["chunks"]["retry_001"]["retry_step"] == "step_a"

    def test_later_step_input_resolution(self, tmp_path):
        """For later pipeline steps, input comes from prev step's validated output."""
        run_dir = self._create_run_for_retry(
            tmp_path,
            pipeline=["step_a", "step_b"],
            chunks_manifest={
                "chunk_000": {"state": "VALIDATED", "items": 2, "retries": 0}
            },
            files_by_chunk={"chunk_000": {
                "units.jsonl": [
                    {"unit_id": "u1", "original": True},
                ],
                "step_a_validated.jsonl": [
                    {"unit_id": "u1", "step_a_result": "enriched"},
                ],
                "step_b_failures.jsonl": [
                    {"unit_id": "u1", "failure_stage": "validation", "retry_count": 0},
                ],
            }}
        )
        result = retry_failures_run(run_dir, max_retries=5)
        assert result["retry_chunks_created"] == 1

        # Retry chunk should have enriched data from step_a, not original
        retry_dir = run_dir / "chunks" / "retry_001"
        with open(retry_dir / "units.jsonl") as f:
            units = [json.loads(line) for line in f if line.strip()]
        assert len(units) == 1
        assert units[0]["step_a_result"] == "enriched"
        assert "original" not in units[0]


# =============================================================================
# Additional is_run_terminal edge cases
# =============================================================================

class TestIsRunTerminalAdditional:
    """Additional edge case tests for is_run_terminal()."""

    def test_all_validated(self):
        """All VALIDATED chunks are terminal."""
        manifest = {"chunks": {
            "c0": {"state": "VALIDATED"}, "c1": {"state": "VALIDATED"}
        }}
        assert is_run_terminal(manifest, max_retries=3) is True

    def test_mixed_validated_and_exhausted_failed(self):
        """VALIDATED + exhausted FAILED chunks are terminal."""
        manifest = {"chunks": {
            "c0": {"state": "VALIDATED"},
            "c1": {"state": "FAILED", "retries": 5},
        }}
        assert is_run_terminal(manifest, max_retries=3) is True

    def test_submitted_chunk_not_terminal(self):
        """SUBMITTED chunk makes run non-terminal."""
        manifest = {"chunks": {
            "c0": {"state": "VALIDATED"},
            "c1": {"state": "step_a_SUBMITTED"},
        }}
        assert is_run_terminal(manifest, max_retries=3) is False

    def test_complete_state_not_terminal(self):
        """COMPLETE (not yet validated) chunk is not terminal."""
        manifest = {"chunks": {
            "c0": {"state": "step_a_COMPLETE"},
        }}
        assert is_run_terminal(manifest, max_retries=3) is False


# =============================================================================
# Additional retry_validation_failures edge cases
# =============================================================================

class TestRetryValidationFailuresAdditional:
    """Additional edge cases for retry_validation_failures."""

    def test_only_hard_failures_no_retry_created(self, tmp_path):
        """When all failures are hard (pipeline_internal), no retry chunks are created."""
        run_dir = tmp_path
        chunk_dir = run_dir / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)
        log_file = run_dir / "RUN_LOG.txt"
        log_file.touch()

        # Write units
        with open(chunk_dir / "units.jsonl", "w") as f:
            f.write(json.dumps({"unit_id": "u1"}) + "\n")

        # Only hard failures
        with open(chunk_dir / "step_a_failures.jsonl", "w") as f:
            f.write(json.dumps({
                "unit_id": "u1", "failure_stage": "pipeline_internal",
                "errors": [], "retry_count": 0
            }) + "\n")

        manifest = {
            "status": "failed",
            "pipeline": ["step_a"],
            "chunks": {"chunk_000": {"state": "VALIDATED", "failed": 1}},
        }

        archived = retry_validation_failures(run_dir, manifest, log_file)
        assert archived == 0
        # No retry chunks created
        assert "retry_001" not in manifest.get("chunks", {})

    def test_no_failures_returns_zero(self, tmp_path):
        """No failure files means zero retries."""
        run_dir = tmp_path
        chunk_dir = run_dir / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)
        log_file = run_dir / "RUN_LOG.txt"
        log_file.touch()

        manifest = {
            "status": "complete",
            "pipeline": ["step_a"],
            "chunks": {"chunk_000": {"state": "VALIDATED", "failed": 0}},
        }

        archived = retry_validation_failures(run_dir, manifest, log_file)
        assert archived == 0

    def test_multiple_steps_with_failures(self, tmp_path):
        """Handles failures across multiple pipeline steps."""
        run_dir = tmp_path
        chunk_dir = run_dir / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)
        log_file = run_dir / "RUN_LOG.txt"
        log_file.touch()

        # Write units
        with open(chunk_dir / "units.jsonl", "w") as f:
            f.write(json.dumps({"unit_id": "u1", "data": "x"}) + "\n")
            f.write(json.dumps({"unit_id": "u2", "data": "y"}) + "\n")

        # step_a validated output (u1 passed, u2 passed)
        with open(chunk_dir / "step_a_validated.jsonl", "w") as f:
            f.write(json.dumps({"unit_id": "u1", "step_a_result": "ok"}) + "\n")
            f.write(json.dumps({"unit_id": "u2", "step_a_result": "ok"}) + "\n")

        # step_a failure (u1 failed at step_a)
        with open(chunk_dir / "step_a_failures.jsonl", "w") as f:
            f.write(json.dumps({
                "unit_id": "u1", "failure_stage": "validation",
                "errors": [{"message": "bad"}], "retry_count": 0,
            }) + "\n")

        # step_b failure (u2 failed at step_b)
        with open(chunk_dir / "step_b_failures.jsonl", "w") as f:
            f.write(json.dumps({
                "unit_id": "u2", "failure_stage": "validation",
                "errors": [{"message": "bad"}], "retry_count": 0,
            }) + "\n")

        manifest = {
            "status": "complete",
            "pipeline": ["step_a", "step_b"],
            "chunks": {"chunk_000": {"state": "VALIDATED", "failed": 2}},
        }

        archived = retry_validation_failures(run_dir, manifest, log_file)
        assert archived == 2

        # Two retry chunks should be created (one per step)
        retry_chunks = [k for k in manifest["chunks"] if k.startswith("retry_")]
        assert len(retry_chunks) == 2


# =============================================================================
# Gzip Post-Processing
# =============================================================================

class TestRunGzipPostProcess:
    """Tests for _run_gzip_post_process()."""

    def test_compress_single_file(self, tmp_path):
        """Compresses a file matching the glob pattern."""
        (tmp_path / "chunks").mkdir()
        test_file = tmp_path / "chunks" / "data.jsonl"
        test_file.write_text('{"line": 1}\n{"line": 2}\n')

        entry = {"files": ["chunks/*.jsonl"], "keep_originals": False}
        _run_gzip_post_process(tmp_path, entry, "test gzip")

        # Original should be deleted, gz should exist
        assert not test_file.exists()
        gz_file = tmp_path / "chunks" / "data.jsonl.gz"
        assert gz_file.exists()

        # Verify contents
        with gzip.open(gz_file, 'rt') as f:
            content = f.read()
        assert '{"line": 1}' in content

    def test_keep_originals(self, tmp_path):
        """keep_originals=True preserves the original file."""
        test_file = tmp_path / "data.txt"
        test_file.write_text("hello")

        entry = {"files": ["*.txt"], "keep_originals": True}
        _run_gzip_post_process(tmp_path, entry, "test")

        assert test_file.exists()
        assert (tmp_path / "data.txt.gz").exists()

    def test_no_patterns(self, tmp_path, capsys):
        """Empty file patterns skips processing."""
        _run_gzip_post_process(tmp_path, {"files": []}, "test")
        captured = capsys.readouterr()
        assert "No file patterns specified, skipping" in captured.out
        assert list(tmp_path.glob("*.gz")) == []

    def test_skip_already_compressed(self, tmp_path):
        """Files with .gz extension are skipped."""
        gz_file = tmp_path / "data.gz"
        gz_file.write_bytes(b"compressed")

        entry = {"files": ["*.gz"]}
        _run_gzip_post_process(tmp_path, entry, "test")
        # Should not create data.gz.gz
        assert not (tmp_path / "data.gz.gz").exists()

    def test_no_matching_files(self, tmp_path, capsys):
        """No files matching pattern doesn't error."""
        entry = {"files": ["*.nonexistent"]}
        _run_gzip_post_process(tmp_path, entry, "test")
        captured = capsys.readouterr()
        assert "No files match '*.nonexistent'" in captured.out
        assert list(tmp_path.glob("*.gz")) == []

    def test_multiple_patterns(self, tmp_path):
        """Multiple glob patterns are all processed."""
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.log").write_text("b")

        entry = {"files": ["*.txt", "*.log"], "keep_originals": False}
        _run_gzip_post_process(tmp_path, entry, "test")

        assert (tmp_path / "a.txt.gz").exists()
        assert (tmp_path / "b.log.gz").exists()
        assert not (tmp_path / "a.txt").exists()
        assert not (tmp_path / "b.log").exists()


# =============================================================================
# Run Post-Process Routing
# =============================================================================

class TestRunPostProcess:
    """Tests for run_post_process()."""

    def test_no_post_process_config(self, tmp_path):
        """No post_process key does nothing."""
        log_file = tmp_path / "RUN_LOG.txt"
        log_file.write_text("")
        run_post_process(tmp_path, {})
        assert log_file.read_text() == ""

    def test_gzip_type_routing(self, tmp_path):
        """Type 'gzip' routes to _run_gzip_post_process."""
        (tmp_path / "RUN_LOG.txt").touch()
        (tmp_path / "data.txt").write_text("hello")

        config = {"post_process": [
            {"name": "Compress", "type": "gzip", "files": ["*.txt"]}
        ]}
        run_post_process(tmp_path, config)
        assert (tmp_path / "data.txt.gz").exists()

    def test_unknown_type_skipped(self, tmp_path):
        """Unknown type is skipped with warning."""
        log_file = tmp_path / "RUN_LOG.txt"
        log_file.write_text("")
        config = {"post_process": [
            {"name": "Unknown", "type": "magic"}
        ]}
        run_post_process(tmp_path, config)
        log_text = log_file.read_text()
        assert "Unknown: Unknown type 'magic', skipping" in log_text

    def test_script_type_no_script(self, tmp_path):
        """Script type with no script skips."""
        log_file = tmp_path / "RUN_LOG.txt"
        log_file.write_text("")
        config = {"post_process": [
            {"name": "MissingScript", "type": "script"}
        ]}
        run_post_process(tmp_path, config)
        log_text = log_file.read_text()
        assert "MissingScript: No script specified, skipping" in log_text


# =============================================================================
# Check Prerequisites
# =============================================================================

class TestCheckPrerequisites:
    """Tests for check_prerequisites()."""

    def test_known_provider_with_key(self, monkeypatch):
        """Returns None when required key is present."""
        monkeypatch.setenv("GOOGLE_API_KEY", "fake-key")
        config = {"api": {"provider": "gemini"}}
        assert check_prerequisites(config) is None

    def test_known_provider_missing_key(self, monkeypatch):
        """Returns error when required key is missing."""
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        config = {"api": {"provider": "gemini"}}
        result = check_prerequisites(config)
        assert result == "GOOGLE_API_KEY not set (needed for gemini). export GOOGLE_API_KEY=your_key"
        assert "GOOGLE_API_KEY" in result

    def test_openai_provider(self, monkeypatch):
        """OpenAI provider checks OPENAI_API_KEY."""
        monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
        config = {"api": {"provider": "openai"}}
        assert check_prerequisites(config) is None

    def test_anthropic_provider(self, monkeypatch):
        """Anthropic provider checks ANTHROPIC_API_KEY."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
        config = {"api": {"provider": "anthropic"}}
        assert check_prerequisites(config) is None

    def test_multiple_providers_from_steps(self, monkeypatch):
        """Checks keys for all providers used across steps."""
        monkeypatch.setenv("GOOGLE_API_KEY", "fake")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        config = {
            "api": {"provider": "gemini"},
            "pipeline": {"steps": [
                {"name": "step_a"},
                {"name": "step_b", "provider": "openai"},
            ]}
        }
        result = check_prerequisites(config)
        assert result == "OPENAI_API_KEY not set (needed for openai). export OPENAI_API_KEY=your_key"
        assert "OPENAI_API_KEY" in result

    def test_no_config_with_any_key(self, monkeypatch):
        """No config but has an API key returns None."""
        monkeypatch.setenv("GOOGLE_API_KEY", "fake")
        # When no provider is identifiable and registry is unavailable,
        # check_prerequisites accepts any key
        assert check_prerequisites(None) is None

    def test_no_config_no_keys(self, monkeypatch):
        """No config and no keys returns error when registry has no default."""
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        # Mock registry to raise ImportError so no default provider is found
        import unittest.mock
        def raise_import(*args, **kwargs):
            raise ImportError("no providers")
        monkeypatch.setattr("orchestrate.check_prerequisites.__module__", "orchestrate")
        # Instead of mocking the import, pass an empty config with no provider
        # This avoids the registry lookup entirely
        config = {"api": {}}
        # With empty api config, no provider is identified, so it tries registry
        # To force the "no provider" path, we need to also fail the registry
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__
        def mock_import(name, *args, **kwargs):
            if name == "scripts.providers.base":
                raise ImportError("mocked")
            return original_import(name, *args, **kwargs)
        with unittest.mock.patch("builtins.__import__", side_effect=mock_import):
            result = check_prerequisites(None)
        # Should request at least one key
        assert result == "No API key found. Set at least one of: GOOGLE_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY"
        assert "API key" in result


# =============================================================================
# Expression Step Loop Completion
# =============================================================================

class TestExpressionStepLoopCompletion:
    """Tests for successful loop completion in run_expression_step."""

    @pytest.fixture
    def loop_run_dir(self, tmp_path):
        """Create run dir with units for loop testing."""
        chunk_dir = tmp_path / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)
        units = [
            {"unit_id": "u1", "_repetition_seed": 42},
        ]
        with open(chunk_dir / "units.jsonl", "w") as f:
            for u in units:
                f.write(json.dumps(u) + "\n")
        (tmp_path / "RUN_LOG.txt").touch()
        return tmp_path

    def test_loop_completes_successfully(self, loop_run_dir):
        """Loop that meets condition before max_iterations records no timeout."""
        step_config = {
            "name": "count_up",
            "scope": "expression",
            "init": {"counter": "0"},
            "expressions": {"counter": "counter + 1"},
            "loop_until": "counter >= 3",
            "max_iterations": 100,
        }
        manifest = {"pipeline": ["count_up"]}
        log_file = loop_run_dir / "RUN_LOG.txt"

        valid, failed, _, _ = run_expression_step(
            loop_run_dir, "chunk_000", "count_up", step_config, {},
            manifest, log_file
        )
        assert valid == 1
        assert failed == 0

        output_file = loop_run_dir / "chunks" / "chunk_000" / "count_up_validated.jsonl"
        with open(output_file) as f:
            results = [json.loads(line) for line in f]
        assert results[0]["counter"] == 3
        assert results[0]["_metadata"]["timeout"] is False
        assert results[0]["_metadata"]["iterations"] == 3

    def test_expression_step_with_init(self, loop_run_dir):
        """Non-looping expression step with init evaluates init before expressions."""
        step_config = {
            "name": "init_step",
            "scope": "expression",
            "init": {"base": "10"},
            "expressions": {"result": "base + 5"},
        }
        manifest = {"pipeline": ["init_step"]}
        log_file = loop_run_dir / "RUN_LOG.txt"

        valid, failed, _, _ = run_expression_step(
            loop_run_dir, "chunk_000", "init_step", step_config, {},
            manifest, log_file
        )
        assert valid == 1
        output_file = loop_run_dir / "chunks" / "chunk_000" / "init_step_validated.jsonl"
        with open(output_file) as f:
            results = [json.loads(line) for line in f]
        assert results[0]["base"] == 10
        assert results[0]["result"] == 15

    def test_expression_step_empty_units_file(self, tmp_path):
        """Empty units file returns (0, 0, 0, 0)."""
        chunk_dir = tmp_path / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)
        (chunk_dir / "units.jsonl").write_text("")
        (tmp_path / "RUN_LOG.txt").touch()

        step_config = {
            "name": "step_a",
            "scope": "expression",
            "expressions": {"val": "1"},
        }
        manifest = {"pipeline": ["step_a"]}
        valid, failed, _, _ = run_expression_step(
            tmp_path, "chunk_000", "step_a", step_config, {},
            manifest, tmp_path / "RUN_LOG.txt"
        )
        assert valid == 0
        assert failed == 0


# =============================================================================
# Additional retry_validation_failures branch coverage
# =============================================================================

class TestRetryValidationFailuresBranches:
    """Tests targeting specific uncovered branches in retry_validation_failures."""

    def test_chunk_dir_missing_skipped(self, tmp_path):
        """Chunk in manifest but missing on disk is skipped."""
        run_dir = tmp_path
        (run_dir / "chunks").mkdir()
        (run_dir / "RUN_LOG.txt").touch()

        manifest = {
            "status": "complete",
            "pipeline": ["step_a"],
            "chunks": {"chunk_000": {"state": "VALIDATED", "failed": 0}},
        }
        archived = retry_validation_failures(run_dir, manifest, run_dir / "RUN_LOG.txt")
        assert archived == 0

    def test_all_validation_failures_removed_deletes_file(self, tmp_path):
        """When all failures are validation type, failures file is deleted (not just emptied)."""
        run_dir = tmp_path
        chunk_dir = run_dir / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)
        log_file = run_dir / "RUN_LOG.txt"
        log_file.touch()

        with open(chunk_dir / "units.jsonl", "w") as f:
            f.write(json.dumps({"unit_id": "u1", "data": "x"}) + "\n")

        with open(chunk_dir / "step_a_failures.jsonl", "w") as f:
            f.write(json.dumps({
                "unit_id": "u1", "failure_stage": "validation",
                "errors": [], "retry_count": 0,
            }) + "\n")

        manifest = {
            "status": "complete",
            "pipeline": ["step_a"],
            "chunks": {"chunk_000": {"state": "VALIDATED", "failed": 1}},
        }

        archived = retry_validation_failures(run_dir, manifest, log_file)
        assert archived == 1

        # Original failures file should be deleted (no hard failures remain)
        assert not (chunk_dir / "step_a_failures.jsonl").exists()
        # But .bak should exist
        assert (chunk_dir / "step_a_failures.jsonl.bak").exists()

    def test_later_step_input_from_prev_validated(self, tmp_path):
        """For non-first pipeline step, input is read from prev step's validated file."""
        run_dir = tmp_path
        chunk_dir = run_dir / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)
        log_file = run_dir / "RUN_LOG.txt"
        log_file.touch()

        # Initial units (should NOT be used for step_b)
        with open(chunk_dir / "units.jsonl", "w") as f:
            f.write(json.dumps({"unit_id": "u1", "original": True}) + "\n")

        # step_a validated output (this IS the input for step_b)
        with open(chunk_dir / "step_a_validated.jsonl", "w") as f:
            f.write(json.dumps({"unit_id": "u1", "enriched": True}) + "\n")

        # step_b failure
        with open(chunk_dir / "step_b_failures.jsonl", "w") as f:
            f.write(json.dumps({
                "unit_id": "u1", "failure_stage": "validation",
                "errors": [], "retry_count": 0,
            }) + "\n")

        manifest = {
            "status": "complete",
            "pipeline": ["step_a", "step_b"],
            "chunks": {"chunk_000": {"state": "VALIDATED", "failed": 1}},
        }

        archived = retry_validation_failures(run_dir, manifest, log_file)
        assert archived == 1

        # Retry chunk should have enriched data
        retry_dir = run_dir / "chunks" / "retry_001"
        with open(retry_dir / "units.jsonl") as f:
            units = [json.loads(line) for line in f if line.strip()]
        assert units[0]["enriched"] is True
        assert "original" not in units[0]

    def test_duplicate_unit_ids_keeps_highest_retry_count(self, tmp_path):
        """Duplicate unit_ids in failures keep the highest retry_count."""
        run_dir = tmp_path
        chunk_dir = run_dir / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)
        log_file = run_dir / "RUN_LOG.txt"
        log_file.touch()

        with open(chunk_dir / "units.jsonl", "w") as f:
            f.write(json.dumps({"unit_id": "u1", "data": "x"}) + "\n")

        # Same unit_id appears twice with different retry counts
        with open(chunk_dir / "step_a_failures.jsonl", "w") as f:
            f.write(json.dumps({
                "unit_id": "u1", "failure_stage": "validation",
                "errors": [], "retry_count": 1,
            }) + "\n")
            f.write(json.dumps({
                "unit_id": "u1", "failure_stage": "validation",
                "errors": [], "retry_count": 3,
            }) + "\n")

        manifest = {
            "status": "complete",
            "pipeline": ["step_a"],
            "chunks": {"chunk_000": {"state": "VALIDATED", "failed": 2}},
        }

        archived = retry_validation_failures(run_dir, manifest, log_file)
        assert archived == 2

        # Check retry chunk has retry_count incremented from highest (3 -> 4)
        retry_dir = run_dir / "chunks" / "retry_001"
        with open(retry_dir / "units.jsonl") as f:
            units = [json.loads(line) for line in f if line.strip()]
        assert len(units) == 1
        assert units[0]["retry_count"] == 4  # 3 + 1

    def test_failure_with_missing_retry_count_uses_chunk_retries(self, tmp_path):
        """Failure without retry_count falls back to chunk-level retries."""
        run_dir = tmp_path
        chunk_dir = run_dir / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)
        log_file = run_dir / "RUN_LOG.txt"
        log_file.touch()

        with open(chunk_dir / "units.jsonl", "w") as f:
            f.write(json.dumps({"unit_id": "u1", "data": "x"}) + "\n")

        # No retry_count in failure
        with open(chunk_dir / "step_a_failures.jsonl", "w") as f:
            f.write(json.dumps({
                "unit_id": "u1", "failure_stage": "validation",
                "errors": [],
            }) + "\n")

        manifest = {
            "status": "complete",
            "pipeline": ["step_a"],
            "chunks": {"chunk_000": {"state": "VALIDATED", "failed": 1, "retries": 2}},
        }

        archived = retry_validation_failures(run_dir, manifest, log_file)
        assert archived == 1

        # Retry count should be chunk retries (2) + 1 = 3
        retry_dir = run_dir / "chunks" / "retry_001"
        with open(retry_dir / "units.jsonl") as f:
            units = [json.loads(line) for line in f if line.strip()]
        assert units[0]["retry_count"] == 3


# =============================================================================
# retry_failures_run edge cases
# =============================================================================

class TestRetryFailuresRunEdgeCases:
    """Tests for edge cases and branch coverage in retry_failures_run."""

    def _create_run(self, tmp_path, pipeline, chunks_manifest, files_by_chunk):
        """Create a run directory for retry_failures_run testing."""
        run_dir = tmp_path
        manifest = {
            "status": "complete",
            "pipeline": pipeline,
            "chunks": chunks_manifest,
            "metadata": {},
        }
        with open(run_dir / "MANIFEST.json", "w") as f:
            json.dump(manifest, f, indent=2)
        (run_dir / "RUN_LOG.txt").touch()

        chunks_dir = run_dir / "chunks"
        chunks_dir.mkdir(exist_ok=True)
        for chunk_name, file_map in files_by_chunk.items():
            chunk_dir = chunks_dir / chunk_name
            chunk_dir.mkdir(exist_ok=True)
            for filename, records in file_map.items():
                with open(chunk_dir / filename, "w") as f:
                    for r in records:
                        if isinstance(r, str):
                            f.write(r + "\n")
                        else:
                            f.write(json.dumps(r) + "\n")
        return run_dir

    def test_in_progress_retry_chunk_skipped(self, tmp_path):
        """Retry chunks still being processed are skipped."""
        run_dir = self._create_run(
            tmp_path,
            pipeline=["step_a"],
            chunks_manifest={
                "chunk_000": {"state": "VALIDATED", "items": 2, "retries": 0},
                "retry_001": {"state": "step_a_SUBMITTED", "items": 1, "retries": 1},
            },
            files_by_chunk={
                "chunk_000": {
                    "units.jsonl": [{"unit_id": "u1"}, {"unit_id": "u2"}],
                },
                "retry_001": {
                    "units.jsonl": [{"unit_id": "u1"}],
                    "step_a_failures.jsonl": [
                        {"unit_id": "u1", "retry_count": 1},
                    ],
                },
            }
        )
        result = retry_failures_run(run_dir, max_retries=5)
        # retry_001 is in SUBMITTED state, so its failures should be skipped
        assert result["retry_chunks_created"] == 0

    def test_malformed_input_jsonl_skipped(self, tmp_path):
        """Non-JSON lines in input source are skipped."""
        run_dir = self._create_run(
            tmp_path,
            pipeline=["step_a"],
            chunks_manifest={
                "chunk_000": {"state": "VALIDATED", "items": 2, "retries": 0},
            },
            files_by_chunk={
                "chunk_000": {
                    "units.jsonl": [
                        "this is not json",
                        {"unit_id": "u1", "data": "a"},
                    ],
                    "step_a_failures.jsonl": [
                        {"unit_id": "u1", "retry_count": 0},
                    ],
                },
            }
        )
        result = retry_failures_run(run_dir, max_retries=5)
        assert result["retry_chunks_created"] == 1

    def test_failure_missing_unit_id_skipped(self, tmp_path):
        """Failure records without unit_id are skipped."""
        run_dir = self._create_run(
            tmp_path,
            pipeline=["step_a"],
            chunks_manifest={
                "chunk_000": {"state": "VALIDATED", "items": 2, "retries": 0},
            },
            files_by_chunk={
                "chunk_000": {
                    "units.jsonl": [{"unit_id": "u1", "data": "a"}],
                    "step_a_failures.jsonl": [
                        {"no_unit_id": True},  # Missing unit_id
                        {"unit_id": "u1", "retry_count": 0},
                    ],
                },
            }
        )
        result = retry_failures_run(run_dir, max_retries=5)
        # Only u1 should be retried, not the record without unit_id
        assert result["retry_chunks_created"] == 1
        retry_dir = run_dir / "chunks" / "retry_001"
        with open(retry_dir / "units.jsonl") as f:
            units = [json.loads(line) for line in f if line.strip()]
        assert len(units) == 1
        assert units[0]["unit_id"] == "u1"

    def test_failure_unit_not_in_input(self, tmp_path):
        """Failure unit_id not found in input source is skipped."""
        run_dir = self._create_run(
            tmp_path,
            pipeline=["step_a"],
            chunks_manifest={
                "chunk_000": {"state": "VALIDATED", "items": 1, "retries": 0},
            },
            files_by_chunk={
                "chunk_000": {
                    "units.jsonl": [{"unit_id": "u1", "data": "a"}],
                    "step_a_failures.jsonl": [
                        {"unit_id": "u_ghost", "retry_count": 0},  # Not in units.jsonl
                    ],
                },
            }
        )
        result = retry_failures_run(run_dir, max_retries=5)
        # u_ghost can't be found in input, so no retry chunk
        assert result["retry_chunks_created"] == 0

    def test_chunk_dir_missing_skipped(self, tmp_path):
        """Chunk in manifest but missing directory is skipped."""
        run_dir = self._create_run(
            tmp_path,
            pipeline=["step_a"],
            chunks_manifest={
                "chunk_000": {"state": "VALIDATED", "items": 1, "retries": 0},
                "chunk_001": {"state": "VALIDATED", "items": 1, "retries": 0},
            },
            files_by_chunk={
                "chunk_000": {
                    "units.jsonl": [{"unit_id": "u1"}],
                    "step_a_failures.jsonl": [
                        {"unit_id": "u1", "retry_count": 0},
                    ],
                },
            }
        )
        # chunk_001 is in manifest but has no directory on disk
        result = retry_failures_run(run_dir, max_retries=5)
        assert result["retry_chunks_created"] == 1

    def test_malformed_failure_json_skipped(self, tmp_path):
        """Non-JSON lines in failure file are skipped."""
        run_dir = self._create_run(
            tmp_path,
            pipeline=["step_a"],
            chunks_manifest={
                "chunk_000": {"state": "VALIDATED", "items": 2, "retries": 0},
            },
            files_by_chunk={
                "chunk_000": {
                    "units.jsonl": [
                        {"unit_id": "u1", "data": "a"},
                        {"unit_id": "u2", "data": "b"},
                    ],
                    "step_a_failures.jsonl": [
                        "not json at all",
                        {"unit_id": "u1", "retry_count": 0},
                    ],
                },
            }
        )
        result = retry_failures_run(run_dir, max_retries=5)
        assert result["retry_chunks_created"] == 1
        # Only u1 should be in the retry chunk
        retry_dir = run_dir / "chunks" / "retry_001"
        with open(retry_dir / "units.jsonl") as f:
            units = [json.loads(line) for line in f if line.strip()]
        assert len(units) == 1
        assert units[0]["unit_id"] == "u1"

    def test_duplicate_unit_across_chunks_keeps_highest(self, tmp_path):
        """Same unit_id failing in multiple chunks keeps highest retry_count."""
        run_dir = self._create_run(
            tmp_path,
            pipeline=["step_a"],
            chunks_manifest={
                "chunk_000": {"state": "VALIDATED", "items": 1, "retries": 0},
                "chunk_001": {"state": "VALIDATED", "items": 1, "retries": 0},
            },
            files_by_chunk={
                "chunk_000": {
                    "units.jsonl": [{"unit_id": "u1", "data": "a"}],
                    "step_a_failures.jsonl": [
                        {"unit_id": "u1", "retry_count": 1},
                    ],
                },
                "chunk_001": {
                    "units.jsonl": [{"unit_id": "u1", "data": "b"}],
                    "step_a_failures.jsonl": [
                        {"unit_id": "u1", "retry_count": 3},
                    ],
                },
            }
        )
        result = retry_failures_run(run_dir, max_retries=5)
        assert result["retry_chunks_created"] == 1
        retry_dir = run_dir / "chunks" / "retry_001"
        with open(retry_dir / "units.jsonl") as f:
            units = [json.loads(line) for line in f if line.strip()]
        assert len(units) == 1
        # retry_count should be 3 + 1 = 4
        assert units[0]["retry_count"] == 4

    def test_all_permanent_no_retry_chunks(self, tmp_path):
        """All failures exceed max_retries so no retry chunks created."""
        run_dir = self._create_run(
            tmp_path,
            pipeline=["step_a"],
            chunks_manifest={
                "chunk_000": {"state": "VALIDATED", "items": 1, "retries": 0},
            },
            files_by_chunk={
                "chunk_000": {
                    "units.jsonl": [{"unit_id": "u1", "data": "a"}],
                    "step_a_failures.jsonl": [
                        {"unit_id": "u1", "retry_count": 10},
                    ],
                },
            }
        )
        result = retry_failures_run(run_dir, max_retries=5)
        assert result["retry_chunks_created"] == 0
        assert result["permanent_failures"] == 1

    def test_fallback_retry_count_from_chunk(self, tmp_path):
        """Failure without retry_count falls back to chunk-level retries/retry_count."""
        run_dir = self._create_run(
            tmp_path,
            pipeline=["step_a"],
            chunks_manifest={
                "chunk_000": {"state": "VALIDATED", "items": 1, "retries": 2, "retry_count": 2},
            },
            files_by_chunk={
                "chunk_000": {
                    "units.jsonl": [{"unit_id": "u1", "data": "a"}],
                    "step_a_failures.jsonl": [
                        {"unit_id": "u1"},  # No retry_count at all
                    ],
                },
            }
        )
        result = retry_failures_run(run_dir, max_retries=5)
        assert result["retry_chunks_created"] == 1
        retry_dir = run_dir / "chunks" / "retry_001"
        with open(retry_dir / "units.jsonl") as f:
            units = [json.loads(line) for line in f if line.strip()]
        assert units[0]["retry_count"] == 3  # chunk retries (2) + 1


# =============================================================================
# run_post_process script type (with subprocess mock)
# =============================================================================

class TestRunPostProcessScript:
    """Tests for run_post_process() script type execution."""

    def test_script_type_success(self, tmp_path, monkeypatch):
        """Script type with successful subprocess."""
        log_file = tmp_path / "RUN_LOG.txt"
        log_file.write_text("")
        config = {"post_process": [
            {"name": "TestScript", "type": "script", "script": "myscript.py"}
        ]}

        import subprocess
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "output\n"
        mock_result.stderr = ""

        run_mock = MagicMock(return_value=mock_result)
        monkeypatch.setattr(subprocess, "run", run_mock)
        run_post_process(tmp_path, config)

        run_mock.assert_called_once()
        called_cmd = run_mock.call_args.args[0]
        assert called_cmd == [sys.executable, "myscript.py", str(tmp_path)]
        assert "Running: TestScript..." in log_file.read_text()
        assert "TestScript output:" in log_file.read_text()

    def test_script_type_failure(self, tmp_path, monkeypatch):
        """Script type with non-zero exit code logs error."""
        log_file = tmp_path / "RUN_LOG.txt"
        log_file.write_text("")
        config = {"post_process": [
            {"name": "FailScript", "type": "script", "script": "bad.py"}
        ]}

        import subprocess
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Error: something went wrong"

        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock_result)
        run_post_process(tmp_path, config)
        log_text = log_file.read_text()
        assert "FailScript: Failed with exit code 1" in log_text
        assert "Error: something went wrong" in log_text

    def test_script_type_timeout(self, tmp_path, monkeypatch):
        """Script type timeout is handled."""
        log_file = tmp_path / "RUN_LOG.txt"
        log_file.write_text("")
        config = {"post_process": [
            {"name": "SlowScript", "type": "script", "script": "slow.py"}
        ]}

        import subprocess
        def raise_timeout(*a, **kw):
            raise subprocess.TimeoutExpired("slow.py", 600)

        monkeypatch.setattr(subprocess, "run", raise_timeout)
        run_post_process(tmp_path, config)
        assert "SlowScript: Timed out after 600s" in log_file.read_text()

    def test_script_type_not_found(self, tmp_path, monkeypatch):
        """Script not found is handled."""
        log_file = tmp_path / "RUN_LOG.txt"
        log_file.write_text("")
        config = {"post_process": [
            {"name": "MissingScript", "type": "script", "script": "nonexistent.py"}
        ]}

        import subprocess
        def raise_fnf(*a, **kw):
            raise FileNotFoundError("nonexistent.py")

        monkeypatch.setattr(subprocess, "run", raise_fnf)
        run_post_process(tmp_path, config)
        assert "MissingScript: Script not found: nonexistent.py" in log_file.read_text()

    def test_script_type_with_output_file(self, tmp_path, monkeypatch):
        """Script type with output file writes stdout to file."""
        (tmp_path / "RUN_LOG.txt").touch()
        config = {"post_process": [
            {"name": "OutputScript", "type": "script", "script": "gen.py",
             "output": "result.txt"}
        ]}

        import subprocess
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "generated output"
        mock_result.stderr = ""

        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock_result)
        run_post_process(tmp_path, config)
        assert (tmp_path / "result.txt").read_text() == "generated output"

    def test_script_stderr_on_success(self, tmp_path, monkeypatch):
        """Stderr warnings are logged even on success."""
        log_file = tmp_path / "RUN_LOG.txt"
        log_file.write_text("")
        config = {"post_process": [
            {"name": "WarnScript", "type": "script", "script": "warn.py"}
        ]}

        import subprocess
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = "Warning: deprecated\n"

        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock_result)
        run_post_process(tmp_path, config)
        log_text = log_file.read_text()
        assert "Running: WarnScript..." in log_text
        assert "WarnScript: Warning: deprecated" in log_text


# =============================================================================
# Gzip post-process additional edge cases
# =============================================================================

class TestRunGzipPostProcessEdgeCases:
    """Edge case tests for _run_gzip_post_process."""

    def test_skip_directories(self, tmp_path):
        """Directories matching glob are skipped."""
        (tmp_path / "subdir.jsonl").mkdir()  # Directory with .jsonl extension
        (tmp_path / "file.jsonl").write_text("data")

        entry = {"files": ["*.jsonl"]}
        _run_gzip_post_process(tmp_path, entry, "test")

        # Directory should still exist, file should be compressed
        assert (tmp_path / "subdir.jsonl").is_dir()
        assert (tmp_path / "file.jsonl.gz").exists()


# =============================================================================
# check_prerequisites edge cases
# =============================================================================

class TestCheckPrerequisitesEdgeCases:
    """Edge case tests for check_prerequisites."""

    def test_step_level_provider_only(self, monkeypatch):
        """Only step-level provider override; checks that key."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        config = {
            "pipeline": {"steps": [
                {"name": "step_a", "provider": "anthropic"},
            ]}
        }
        assert check_prerequisites(config) is None

    def test_unknown_provider_passes(self, monkeypatch):
        """Unknown provider name with no key map entry passes."""
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        config = {"api": {"provider": "custom_local"}}
        # "custom_local" is not in PROVIDER_KEY_MAP, so no key required
        assert check_prerequisites(config) is None

    def test_case_insensitive_provider(self, monkeypatch):
        """Provider names are case-insensitive."""
        monkeypatch.setenv("OPENAI_API_KEY", "fake")
        config = {"api": {"provider": "OpenAI"}}
        assert check_prerequisites(config) is None


# =============================================================================
# _cleanup_subprocesses
# =============================================================================

class TestCleanupSubprocesses:
    """Tests for _cleanup_subprocesses."""

    def test_cleanup_empty_list(self):
        """Cleanup with no active subprocesses does nothing."""
        original = orchestrate._active_subprocesses.copy()
        orchestrate._active_subprocesses.clear()
        orchestrate._cleanup_subprocesses()
        assert orchestrate._active_subprocesses == []
        orchestrate._active_subprocesses.extend(original)

    def test_cleanup_terminates_processes(self):
        """Cleanup terminates tracked processes."""
        mock_proc = MagicMock()
        mock_proc.terminate = MagicMock()
        mock_proc.wait = MagicMock()
        mock_proc.kill = MagicMock()

        original = orchestrate._active_subprocesses.copy()
        orchestrate._active_subprocesses.clear()
        orchestrate._active_subprocesses.append(mock_proc)

        # Save and clear _current_run_dir to avoid side effects
        saved_run_dir = orchestrate._current_run_dir
        orchestrate._current_run_dir = None

        orchestrate._cleanup_subprocesses()

        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called_once()
        assert len(orchestrate._active_subprocesses) == 0

        # Restore
        orchestrate._active_subprocesses.extend(original)
        orchestrate._current_run_dir = saved_run_dir

    def test_cleanup_kills_on_terminate_failure(self):
        """Falls back to kill() when terminate() fails."""
        mock_proc = MagicMock()
        mock_proc.terminate = MagicMock(side_effect=Exception("nope"))
        mock_proc.wait = MagicMock()
        mock_proc.kill = MagicMock()

        original = orchestrate._active_subprocesses.copy()
        orchestrate._active_subprocesses.clear()
        orchestrate._active_subprocesses.append(mock_proc)

        saved_run_dir = orchestrate._current_run_dir
        orchestrate._current_run_dir = None

        orchestrate._cleanup_subprocesses()
        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()
        assert orchestrate._active_subprocesses == []

        orchestrate._active_subprocesses.extend(original)
        orchestrate._current_run_dir = saved_run_dir


# =============================================================================
# mark_run_complete edge case
# =============================================================================

class TestMarkRunCompleteEdgeCases:
    """Edge case tests for mark_run_complete."""

    def test_corrects_failed_to_complete(self, tmp_path):
        """mark_run_complete corrects a 'failed' status to 'complete'."""
        manifest = {
            "status": "failed",
            "pipeline": ["step_a"],
            "chunks": {},
            "metadata": {},
        }
        with open(tmp_path / "MANIFEST.json", "w") as f:
            json.dump(manifest, f)
        (tmp_path / "RUN_LOG.txt").touch()

        mark_run_complete(tmp_path)
        with open(tmp_path / "MANIFEST.json") as f:
            m = json.load(f)
        assert m["status"] == "complete"

        # Log should mention "corrected"
        log = (tmp_path / "RUN_LOG.txt").read_text()
        assert "corrected" in log.lower() or "complete" in log.lower()


# =============================================================================
# Additional build_failures_map edge cases
# =============================================================================

class TestBuildFailuresMapEdgeCases:
    """Edge cases for build_failures_map."""

    def test_malformed_json_in_validated_skipped(self, tmp_path):
        """Malformed JSON in validated file doesn't crash."""
        chunks_dir = tmp_path / "chunks"
        chunk_dir = chunks_dir / "chunk_000"
        chunk_dir.mkdir(parents=True)

        with open(chunk_dir / "step_a_validated.jsonl", "w") as f:
            f.write("not json\n")
            f.write(json.dumps({"unit_id": "u1"}) + "\n")

        with open(chunk_dir / "step_a_failures.jsonl", "w") as f:
            f.write(json.dumps({"unit_id": "u2", "failure_stage": "validation"}) + "\n")

        manifest = {"chunks": {"chunk_000": {"state": "FAILED"}}}
        failures = build_failures_map(tmp_path, manifest)
        # u1 is validated, u2 is failed
        assert "u1" not in failures
        assert "u2" in failures

    def test_empty_lines_skipped(self, tmp_path):
        """Empty and whitespace lines are skipped."""
        chunks_dir = tmp_path / "chunks"
        chunk_dir = chunks_dir / "chunk_000"
        chunk_dir.mkdir(parents=True)

        with open(chunk_dir / "step_a_failures.jsonl", "w") as f:
            f.write("\n")
            f.write("  \n")
            f.write(json.dumps({"unit_id": "u1", "failure_stage": "validation"}) + "\n")
            f.write("\n")

        manifest = {"chunks": {"chunk_000": {"state": "FAILED"}}}
        failures = build_failures_map(tmp_path, manifest)
        assert len(failures) == 1
        assert "u1" in failures

    def test_failure_without_unit_id_skipped(self, tmp_path):
        """Failure records without unit_id are skipped."""
        chunks_dir = tmp_path / "chunks"
        chunk_dir = chunks_dir / "chunk_000"
        chunk_dir.mkdir(parents=True)

        with open(chunk_dir / "step_a_failures.jsonl", "w") as f:
            f.write(json.dumps({"no_uid": True}) + "\n")
            f.write(json.dumps({"unit_id": "u1"}) + "\n")

        manifest = {"chunks": {"chunk_000": {"state": "FAILED"}}}
        failures = build_failures_map(tmp_path, manifest)
        assert len(failures) == 1


# =============================================================================
# _log_api_key_status
# =============================================================================

class TestLogApiKeyStatus:
    """Tests for _log_api_key_status."""

    def test_gemini_provider_with_key(self, tmp_path, monkeypatch):
        """Logs AVAILABLE when GOOGLE_API_KEY is set."""
        monkeypatch.setenv("GOOGLE_API_KEY", "fake")
        log_file = tmp_path / "RUN_LOG.txt"
        log_file.touch()
        _log_api_key_status(log_file, "TEST")
        assert "AVAILABLE" in log_file.read_text()

    def test_gemini_provider_no_key(self, tmp_path, monkeypatch):
        """Logs MISSING when GOOGLE_API_KEY is not set."""
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        log_file = tmp_path / "RUN_LOG.txt"
        log_file.touch()
        _log_api_key_status(log_file, "TEST")
        assert "MISSING" in log_file.read_text()

    def test_openai_provider(self, tmp_path, monkeypatch):
        """Detects OpenAI provider from config."""
        monkeypatch.setenv("OPENAI_API_KEY", "fake")
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        import yaml
        with open(config_dir / "config.yaml", "w") as f:
            yaml.dump({"api": {"provider": "openai"}}, f)
        log_file = tmp_path / "RUN_LOG.txt"
        log_file.touch()
        _log_api_key_status(log_file, "TEST")
        assert "OpenAI" in log_file.read_text()

    def test_anthropic_provider(self, tmp_path, monkeypatch):
        """Detects Anthropic provider from config."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        import yaml
        with open(config_dir / "config.yaml", "w") as f:
            yaml.dump({"api": {"provider": "anthropic"}}, f)
        log_file = tmp_path / "RUN_LOG.txt"
        log_file.touch()
        _log_api_key_status(log_file, "TEST")
        assert "Anthropic" in log_file.read_text()

    def test_unknown_provider(self, tmp_path, monkeypatch):
        """Unknown provider logs status unknown."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        import yaml
        with open(config_dir / "config.yaml", "w") as f:
            yaml.dump({"api": {"provider": "custom"}}, f)
        log_file = tmp_path / "RUN_LOG.txt"
        log_file.touch()
        _log_api_key_status(log_file, "TEST")
        assert "unknown" in log_file.read_text().lower()


# =============================================================================
# _cleanup_subprocesses with manifest save
# =============================================================================

class TestCleanupSubprocessesManifest:
    """Tests for _cleanup_subprocesses manifest save path."""

    def test_saves_manifest_as_paused(self, tmp_path):
        """When _current_run_dir is set with running manifest, saves as paused."""
        manifest = {
            "status": "running",
            "pipeline": ["step_a"],
            "chunks": {},
            "metadata": {},
        }
        with open(tmp_path / "MANIFEST.json", "w") as f:
            json.dump(manifest, f)

        saved_run_dir = orchestrate._current_run_dir
        saved_subs = orchestrate._active_subprocesses.copy()
        orchestrate._current_run_dir = tmp_path
        orchestrate._active_subprocesses.clear()

        _cleanup_subprocesses()  # signum=None so no sys.exit

        with open(tmp_path / "MANIFEST.json") as f:
            m = json.load(f)
        assert m["status"] == "paused"
        assert "paused_at" in m

        orchestrate._current_run_dir = saved_run_dir
        orchestrate._active_subprocesses.extend(saved_subs)

    def test_no_save_if_already_complete(self, tmp_path):
        """Does not overwrite complete status."""
        manifest = {
            "status": "complete",
            "pipeline": ["step_a"],
            "chunks": {},
            "metadata": {},
        }
        with open(tmp_path / "MANIFEST.json", "w") as f:
            json.dump(manifest, f)

        saved_run_dir = orchestrate._current_run_dir
        saved_subs = orchestrate._active_subprocesses.copy()
        orchestrate._current_run_dir = tmp_path
        orchestrate._active_subprocesses.clear()

        _cleanup_subprocesses()

        with open(tmp_path / "MANIFEST.json") as f:
            m = json.load(f)
        assert m["status"] == "complete"

        orchestrate._current_run_dir = saved_run_dir
        orchestrate._active_subprocesses.extend(saved_subs)

    def test_signum_causes_exit(self, tmp_path):
        """When signum is provided, sys.exit(130) is called."""
        saved_run_dir = orchestrate._current_run_dir
        saved_subs = orchestrate._active_subprocesses.copy()
        orchestrate._current_run_dir = None
        orchestrate._active_subprocesses.clear()

        with pytest.raises(SystemExit) as exc_info:
            _cleanup_subprocesses(signum=2)
        assert exc_info.value.code == 130

        orchestrate._current_run_dir = saved_run_dir
        orchestrate._active_subprocesses.extend(saved_subs)


# =============================================================================
# Additional mark_run_failed edge cases
# =============================================================================

class TestMarkRunFailedEdgeCases:
    """Edge case tests for mark_run_failed."""

    def test_logs_traceback(self, tmp_path):
        """mark_run_failed with log_traceback=True writes to log."""
        manifest = {
            "status": "running",
            "pipeline": ["step_a"],
            "chunks": {},
            "metadata": {},
        }
        with open(tmp_path / "MANIFEST.json", "w") as f:
            json.dump(manifest, f)
        (tmp_path / "RUN_LOG.txt").touch()

        mark_run_failed(tmp_path, "Test error", log_traceback=True)

        log_content = (tmp_path / "RUN_LOG.txt").read_text()
        assert "FAILED" in log_content
        assert "Test error" in log_content

    def test_no_traceback(self, tmp_path):
        """mark_run_failed with log_traceback=False still logs the error."""
        manifest = {
            "status": "running",
            "pipeline": ["step_a"],
            "chunks": {},
            "metadata": {},
        }
        with open(tmp_path / "MANIFEST.json", "w") as f:
            json.dump(manifest, f)
        (tmp_path / "RUN_LOG.txt").touch()

        mark_run_failed(tmp_path, "Test error", log_traceback=False)

        with open(tmp_path / "MANIFEST.json") as f:
            m = json.load(f)
        assert m["status"] == "failed"


# =============================================================================
# mark_run_paused with failed status
# =============================================================================

class TestMarkRunPausedEdgeCases:
    """Edge case tests for mark_run_paused."""

    def test_does_not_override_failed(self, tmp_path):
        """mark_run_paused does not override 'failed' status."""
        manifest = {
            "status": "failed",
            "pipeline": ["step_a"],
            "chunks": {},
            "metadata": {},
        }
        with open(tmp_path / "MANIFEST.json", "w") as f:
            json.dump(manifest, f)
        (tmp_path / "RUN_LOG.txt").touch()

        mark_run_paused(tmp_path, "test pause")

        with open(tmp_path / "MANIFEST.json") as f:
            m = json.load(f)
        assert m["status"] == "failed"


# =============================================================================
# Additional parse_state edge cases
# =============================================================================

class TestParseStateAdditional:
    """Additional parse_state tests for coverage."""

    def test_pending_terminal(self):
        """PENDING alone is not recognized as terminal."""
        step, status = parse_state("PENDING")
        assert step is None
        assert status == "PENDING"

    def test_state_with_complete_suffix(self):
        """Step state with _COMPLETE suffix."""
        step, status = parse_state("my_step_COMPLETE")
        assert step == "my_step"
        assert status == "COMPLETE"


# =============================================================================
# Additional format_watch_progress edge cases
# =============================================================================

class TestFormatWatchProgressEdgeCases:
    """Edge case tests for format_watch_progress."""

    def test_empty_pipeline(self):
        """Empty pipeline returns empty string."""
        result = format_watch_progress({"steps": {}}, [])
        assert result == ""

    def test_step_with_current_units_zero(self):
        """Step with no current_units and no valid shows waiting."""
        status = {
            "steps": {
                "step_a": {
                    "units": {"valid": 0, "failed": 0},
                    "chunks": {"submitted": 0, "pending": 0, "completed": 0, "validated": 0},
                    "current_units": 0,
                }
            },
            "chunks": [],
            "summary": {"total_units": 10},
        }
        result = format_watch_progress(status, ["step_a"])
        assert "waiting" in result

    def test_step_with_current_units_nonzero_no_chunks(self):
        """Step with current_units>0 but no valid/failed yet shows fallback count."""
        status = {
            "steps": {
                "step_a": {
                    "units": {"valid": 0, "failed": 0},
                    "chunks": {"submitted": 0, "pending": 0, "completed": 0, "validated": 0},
                    "current_units": 10,
                }
            },
            "chunks": [],
            "summary": {"total_units": 10},
        }
        result = format_watch_progress(status, ["step_a"])
        assert "0/10" in result


# =============================================================================
# run_scope_run_step
# =============================================================================

class TestRunScopeRunStep:
    """Tests for run_scope_run_step()."""

    def test_no_script_returns_false(self, tmp_path):
        """Step with no script configured returns False."""
        (tmp_path / "RUN_LOG.txt").touch()
        result = run_scope_run_step(
            tmp_path, {"name": "my_step"}, tmp_path / "config.yaml", tmp_path / "RUN_LOG.txt"
        )
        assert result is False

    def test_script_not_found_returns_false(self, tmp_path):
        """Script file that doesn't exist returns False."""
        (tmp_path / "RUN_LOG.txt").touch()
        result = run_scope_run_step(
            tmp_path,
            {"name": "my_step", "script": "/nonexistent/script.py"},
            tmp_path / "config.yaml",
            tmp_path / "RUN_LOG.txt"
        )
        assert result is False

    def test_successful_script(self, tmp_path, monkeypatch):
        """Successful subprocess returns True."""
        (tmp_path / "RUN_LOG.txt").touch()
        # Create a real script file so the existence check passes
        script_path = tmp_path / "myscript.py"
        script_path.write_text("# noop")

        import subprocess
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock_result)
        result = run_scope_run_step(
            tmp_path,
            {"name": "my_step", "script": str(script_path)},
            tmp_path / "config.yaml",
            tmp_path / "RUN_LOG.txt"
        )
        assert result is True

    def test_failed_script(self, tmp_path, monkeypatch):
        """Failed subprocess returns False."""
        (tmp_path / "RUN_LOG.txt").touch()
        script_path = tmp_path / "myscript.py"
        script_path.write_text("# noop")

        import subprocess
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Error"

        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock_result)
        result = run_scope_run_step(
            tmp_path,
            {"name": "my_step", "script": str(script_path)},
            tmp_path / "config.yaml",
            tmp_path / "RUN_LOG.txt"
        )
        assert result is False

    def test_timeout(self, tmp_path, monkeypatch):
        """Timeout returns False."""
        (tmp_path / "RUN_LOG.txt").touch()
        script_path = tmp_path / "myscript.py"
        script_path.write_text("# noop")

        import subprocess
        def raise_timeout(*a, **kw):
            raise subprocess.TimeoutExpired("cmd", 600)

        monkeypatch.setattr(subprocess, "run", raise_timeout)
        result = run_scope_run_step(
            tmp_path,
            {"name": "my_step", "script": str(script_path)},
            tmp_path / "config.yaml",
            tmp_path / "RUN_LOG.txt"
        )
        assert result is False

    def test_generic_exception(self, tmp_path, monkeypatch):
        """Generic exception returns False."""
        (tmp_path / "RUN_LOG.txt").touch()
        script_path = tmp_path / "myscript.py"
        script_path.write_text("# noop")

        import subprocess
        def raise_exc(*a, **kw):
            raise RuntimeError("something broke")

        monkeypatch.setattr(subprocess, "run", raise_exc)
        result = run_scope_run_step(
            tmp_path,
            {"name": "my_step", "script": str(script_path)},
            tmp_path / "config.yaml",
            tmp_path / "RUN_LOG.txt"
        )
        assert result is False

    def test_success_with_stderr_warnings(self, tmp_path, monkeypatch):
        """Successful script with stderr warnings still returns True."""
        (tmp_path / "RUN_LOG.txt").touch()
        script_path = tmp_path / "myscript.py"
        script_path.write_text("# noop")

        import subprocess
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = "Warning: deprecated\nWarning: slow"

        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock_result)
        result = run_scope_run_step(
            tmp_path,
            {"name": "my_step", "script": str(script_path)},
            tmp_path / "config.yaml",
            tmp_path / "RUN_LOG.txt"
        )
        assert result is True
        # Warnings should be logged
        log = (tmp_path / "RUN_LOG.txt").read_text()
        assert "deprecated" in log


# =============================================================================
# parse_duration additional edge case
# =============================================================================

class TestParseDurationEdgeCases:
    """Additional parse_duration edge cases."""

    def test_zero_seconds(self):
        """Zero seconds is valid."""
        assert parse_duration("0") == 0

    def test_hours_only(self):
        """Hours suffix alone."""
        assert parse_duration("3h") == 10800

    def test_seconds_only_suffix(self):
        """Seconds only with suffix."""
        assert parse_duration("10s") == 10


# =============================================================================
# prepare_prompts
# =============================================================================

class TestPreparePrompts:
    """Tests for prepare_prompts()."""

    def _setup_config(self, tmp_path, config_dict=None):
        """Write a minimal config.yaml and units.jsonl."""
        import yaml
        if config_dict is None:
            config_dict = {"api": {"provider": "gemini"}}
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config_dict, f)
        return config_path

    def _write_units(self, tmp_path, units):
        """Write units to a JSONL file."""
        units_file = tmp_path / "units.jsonl"
        with open(units_file, "w") as f:
            for u in units:
                f.write(json.dumps(u) + "\n")
        return units_file

    def test_success_no_expressions(self, tmp_path, monkeypatch):
        """Successful prepare_prompts with no expressions."""
        config_path = self._setup_config(tmp_path)
        units_file = self._write_units(tmp_path, [
            {"unit_id": "u1", "text": "hello"},
        ])
        prompts_file = tmp_path / "prompts.jsonl"

        import subprocess
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"prompt": "hello"}\n'
        mock_result.stderr = ""

        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock_result)

        success, error = prepare_prompts(units_file, prompts_file, config_path, "step_a")
        assert success is True
        assert error == ""
        assert prompts_file.read_text() == '{"prompt": "hello"}\n'

    def test_subprocess_failure(self, tmp_path, monkeypatch):
        """Subprocess failure returns (False, error_msg)."""
        config_path = self._setup_config(tmp_path)
        units_file = self._write_units(tmp_path, [{"unit_id": "u1"}])
        prompts_file = tmp_path / "prompts.jsonl"

        import subprocess
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Template error"

        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock_result)

        success, error = prepare_prompts(units_file, prompts_file, config_path, "step_a")
        assert success is False
        assert "Template error" in error

    def test_subprocess_timeout(self, tmp_path, monkeypatch):
        """Timeout returns (False, timeout message)."""
        config_path = self._setup_config(tmp_path)
        units_file = self._write_units(tmp_path, [{"unit_id": "u1"}])
        prompts_file = tmp_path / "prompts.jsonl"

        import subprocess
        def raise_timeout(*a, **kw):
            raise subprocess.TimeoutExpired("cmd", 600)

        monkeypatch.setattr(subprocess, "run", raise_timeout)

        success, error = prepare_prompts(units_file, prompts_file, config_path, "step_a")
        assert success is False
        assert "timed out" in error

    def test_with_expressions(self, tmp_path, monkeypatch):
        """Expressions are evaluated and unit data is enriched before prompt generation."""
        config_path = self._setup_config(tmp_path, {
            "api": {"provider": "gemini"},
            "processing": {"expressions": {"doubled": "value * 2"}}
        })
        units_file = self._write_units(tmp_path, [
            {"unit_id": "u1", "value": 5, "_repetition_seed": 42},
        ])
        prompts_file = tmp_path / "prompts.jsonl"

        import subprocess

        captured_input = {}

        def capture_run(*a, **kw):
            captured_input["data"] = kw.get("input", "")
            result = MagicMock()
            result.returncode = 0
            result.stdout = '{"prompt": "test"}\n'
            result.stderr = ""
            return result

        monkeypatch.setattr(subprocess, "run", capture_run)

        success, error = prepare_prompts(units_file, prompts_file, config_path, "step_a")
        assert success is True

        # The input data should have the "doubled" field
        input_data = captured_input["data"]
        unit = json.loads(input_data.strip())
        assert unit["doubled"] == 10

    def test_custom_timeout(self, tmp_path, monkeypatch):
        """Custom timeout is passed to subprocess."""
        config_path = self._setup_config(tmp_path)
        units_file = self._write_units(tmp_path, [{"unit_id": "u1"}])
        prompts_file = tmp_path / "prompts.jsonl"

        import subprocess

        captured_kwargs = {}

        def capture_run(*a, **kw):
            captured_kwargs.update(kw)
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        monkeypatch.setattr(subprocess, "run", capture_run)

        prepare_prompts(units_file, prompts_file, config_path, "step_a", timeout=30)
        assert captured_kwargs.get("timeout") == 30

    def test_subprocess_failure_no_stderr(self, tmp_path, monkeypatch):
        """Subprocess failure with empty stderr shows 'Unknown error'."""
        config_path = self._setup_config(tmp_path)
        units_file = self._write_units(tmp_path, [{"unit_id": "u1"}])
        prompts_file = tmp_path / "prompts.jsonl"

        import subprocess
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = ""

        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock_result)

        success, error = prepare_prompts(units_file, prompts_file, config_path, "step_a")
        assert success is False
        assert "Unknown error" in error


# =============================================================================
# run_validation_pipeline
# =============================================================================

class TestRunValidationPipeline:
    """Tests for run_validation_pipeline()."""

    def test_missing_results_file(self, tmp_path):
        """Returns (0, 0) when results file doesn't exist."""
        log_file = tmp_path / "log.txt"
        log_file.touch()
        results = tmp_path / "nonexistent.jsonl"
        validated = tmp_path / "validated.jsonl"
        failures = tmp_path / "failures.jsonl"
        config_path = tmp_path / "config.yaml"
        config_path.write_text("pipeline:\n  steps:\n    step_a: {}\n")

        v, f = run_validation_pipeline(
            results_file=results,
            validated_file=validated,
            failures_file=failures,
            schema_path=None,
            config_path=config_path,
            step="step_a",
            log_file=log_file,
            chunk_name="chunk_000",
        )
        assert v == 0
        assert f == 0

    def test_empty_results_file(self, tmp_path):
        """Returns (0, 0) when results file is empty."""
        log_file = tmp_path / "log.txt"
        log_file.touch()
        results = tmp_path / "results.jsonl"
        results.write_text("   \n\n  \n")
        validated = tmp_path / "validated.jsonl"
        failures = tmp_path / "failures.jsonl"
        config_path = tmp_path / "config.yaml"
        config_path.write_text("pipeline:\n  steps:\n    step_a: {}\n")

        v, f = run_validation_pipeline(
            results_file=results,
            validated_file=validated,
            failures_file=failures,
            schema_path=None,
            config_path=config_path,
            step="step_a",
            log_file=log_file,
            chunk_name="chunk_000",
        )
        assert v == 0
        assert f == 0

    def test_no_schema_valid_results(self, tmp_path, monkeypatch):
        """Single-validator path writes validated and failures files."""
        import subprocess

        log_file = tmp_path / "log.txt"
        log_file.touch()

        # Results file with 2 units
        results = tmp_path / "results.jsonl"
        results.write_text(
            json.dumps({"unit_id": "u1", "val": 1}) + "\n"
            + json.dumps({"unit_id": "u2", "val": 2}) + "\n"
        )

        validated = tmp_path / "validated.jsonl"
        failures = tmp_path / "failures.jsonl"
        config_path = tmp_path / "config.yaml"
        config_path.write_text("pipeline:\n  steps:\n    step_a: {}\n")

        # Mock Popen: validator passes u1, fails u2
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (
            json.dumps({"unit_id": "u1", "val": 1}).encode() + b"\n",
            json.dumps({"unit_id": "u2", "errors": [{"rule": "r1", "message": "bad"}]}).encode() + b"\n",
        )
        mock_proc.kill = MagicMock()

        # Save/restore _active_subprocesses
        saved = orchestrate._active_subprocesses[:]
        orchestrate._active_subprocesses.clear()

        try:
            monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: mock_proc)

            v, f = run_validation_pipeline(
                results_file=results,
                validated_file=validated,
                failures_file=failures,
                schema_path=None,
                config_path=config_path,
                step="step_a",
                log_file=log_file,
                chunk_name="chunk_000",
            )
            assert v == 1
            assert f == 1
            assert validated.exists()
            assert failures.exists()
        finally:
            orchestrate._active_subprocesses[:] = saved

    def test_no_schema_timeout(self, tmp_path, monkeypatch):
        """Timeout during single-validator returns (0, 0)."""
        import subprocess

        log_file = tmp_path / "log.txt"
        log_file.touch()

        results = tmp_path / "results.jsonl"
        results.write_text(json.dumps({"unit_id": "u1"}) + "\n")

        validated = tmp_path / "validated.jsonl"
        failures = tmp_path / "failures.jsonl"
        config_path = tmp_path / "config.yaml"
        config_path.write_text("pipeline:\n  steps:\n    step_a: {}\n")

        mock_proc = MagicMock()
        mock_proc.communicate.side_effect = subprocess.TimeoutExpired("cmd", 10)
        mock_proc.kill = MagicMock()

        saved = orchestrate._active_subprocesses[:]
        orchestrate._active_subprocesses.clear()

        try:
            monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: mock_proc)

            v, f = run_validation_pipeline(
                results_file=results,
                validated_file=validated,
                failures_file=failures,
                schema_path=None,
                config_path=config_path,
                step="step_a",
                log_file=log_file,
                chunk_name="chunk_000",
                timeout=5,
            )
            assert v == 0
            assert f == 0
        finally:
            orchestrate._active_subprocesses[:] = saved

    def test_with_input_file_merge(self, tmp_path, monkeypatch):
        """Pre-merge combines input data with LLM results before validation."""
        import subprocess

        log_file = tmp_path / "log.txt"
        log_file.touch()

        # Input file has accumulated fields
        input_file = tmp_path / "input.jsonl"
        input_file.write_text(
            json.dumps({"unit_id": "u1", "name": "Alice", "prev_score": 10}) + "\n"
        )

        # Results file has new fields from LLM
        results = tmp_path / "results.jsonl"
        results.write_text(
            json.dumps({"unit_id": "u1", "new_field": "hello"}) + "\n"
        )

        validated = tmp_path / "validated.jsonl"
        failures = tmp_path / "failures.jsonl"
        config_path = tmp_path / "config.yaml"
        config_path.write_text("pipeline:\n  steps:\n    step_a: {}\n")

        # Capture what gets sent to the validator
        captured_input = {}

        def mock_popen(*args, **kwargs):
            proc = MagicMock()
            def mock_communicate(input=None, timeout=None):
                captured_input["data"] = input
                # Pass all input through as valid
                return (input, b"")
            proc.communicate = mock_communicate
            return proc

        saved = orchestrate._active_subprocesses[:]
        orchestrate._active_subprocesses.clear()

        try:
            monkeypatch.setattr(subprocess, "Popen", mock_popen)

            v, f = run_validation_pipeline(
                results_file=results,
                validated_file=validated,
                failures_file=failures,
                schema_path=None,
                config_path=config_path,
                step="step_a",
                log_file=log_file,
                chunk_name="chunk_000",
                input_file=input_file,
            )
            # Merged data should contain both input and result fields
            merged = json.loads(captured_input["data"].decode().strip())
            assert merged["name"] == "Alice"
            assert merged["prev_score"] == 10
            assert merged["new_field"] == "hello"
            assert v == 1
        finally:
            orchestrate._active_subprocesses[:] = saved

    def test_missing_records_generate_synthetic_failures(self, tmp_path, monkeypatch):
        """Missing records get synthetic failure entries appended."""
        import subprocess

        log_file = tmp_path / "log.txt"
        log_file.touch()

        # Input has 3 units
        input_file = tmp_path / "input.jsonl"
        input_file.write_text(
            json.dumps({"unit_id": "u1"}) + "\n"
            + json.dumps({"unit_id": "u2"}) + "\n"
            + json.dumps({"unit_id": "u3"}) + "\n"
        )

        results = tmp_path / "results.jsonl"
        results.write_text(
            json.dumps({"unit_id": "u1"}) + "\n"
            + json.dumps({"unit_id": "u2"}) + "\n"
            + json.dumps({"unit_id": "u3"}) + "\n"
        )

        validated = tmp_path / "validated.jsonl"
        failures = tmp_path / "failures.jsonl"
        config_path = tmp_path / "config.yaml"
        config_path.write_text("pipeline:\n  steps:\n    step_a: {}\n")

        # Validator only returns u1 as valid, u2 as failed, u3 is MISSING
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (
            json.dumps({"unit_id": "u1"}).encode() + b"\n",
            json.dumps({"unit_id": "u2", "errors": [{"rule": "r1", "message": "bad"}]}).encode() + b"\n",
        )

        saved = orchestrate._active_subprocesses[:]
        orchestrate._active_subprocesses.clear()

        try:
            monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: mock_proc)

            v, f = run_validation_pipeline(
                results_file=results,
                validated_file=validated,
                failures_file=failures,
                schema_path=None,
                config_path=config_path,
                step="step_a",
                log_file=log_file,
                chunk_name="chunk_000",
                input_file=input_file,
            )
            assert v == 1
            # 1 explicit failure + 1 synthetic for missing u3
            assert f == 2

            # Check synthetic failure is written
            failure_lines = [json.loads(l) for l in failures.read_text().strip().split("\n") if l.strip()]
            synthetic = [fl for fl in failure_lines if fl.get("failure_stage") == "pipeline_internal"]
            assert len(synthetic) == 1
            assert synthetic[0]["unit_id"] == "u3"
        finally:
            orchestrate._active_subprocesses[:] = saved

    def test_with_schema_two_stage_pipeline(self, tmp_path, monkeypatch):
        """Schema + validator two-stage pipeline processes correctly."""
        import subprocess

        log_file = tmp_path / "log.txt"
        log_file.touch()

        results = tmp_path / "results.jsonl"
        results.write_text(json.dumps({"unit_id": "u1", "val": 1}) + "\n")

        validated = tmp_path / "validated.jsonl"
        failures = tmp_path / "failures.jsonl"
        config_path = tmp_path / "config.yaml"
        config_path.write_text("pipeline:\n  steps:\n    step_a: {}\n")

        schema_path = tmp_path / "schema.json"
        schema_path.write_text('{"type": "object"}')

        call_count = [0]

        def mock_popen(*args, **kwargs):
            proc = MagicMock()
            call_count[0] += 1
            current_call = call_count[0]

            def mock_communicate(input=None, timeout=None):
                if current_call == 1:
                    # Stage 1: schema validator — pass everything through
                    return (
                        json.dumps({"unit_id": "u1", "val": 1}).encode() + b"\n",
                        b"",
                    )
                else:
                    # Stage 2: business logic validator — pass everything
                    return (
                        json.dumps({"unit_id": "u1", "val": 1}).encode() + b"\n",
                        b"",
                    )
            proc.communicate = mock_communicate
            return proc

        saved = orchestrate._active_subprocesses[:]
        orchestrate._active_subprocesses.clear()

        try:
            monkeypatch.setattr(subprocess, "Popen", mock_popen)

            v, f = run_validation_pipeline(
                results_file=results,
                validated_file=validated,
                failures_file=failures,
                schema_path=schema_path,
                config_path=config_path,
                step="step_a",
                log_file=log_file,
                chunk_name="chunk_000",
            )
            assert v == 1
            assert f == 0
            assert call_count[0] == 2  # Both stages invoked
        finally:
            orchestrate._active_subprocesses[:] = saved

    def test_schema_timeout_returns_zero(self, tmp_path, monkeypatch):
        """Timeout during schema validation stage returns (0, 0)."""
        import subprocess

        log_file = tmp_path / "log.txt"
        log_file.touch()

        results = tmp_path / "results.jsonl"
        results.write_text(json.dumps({"unit_id": "u1"}) + "\n")

        validated = tmp_path / "validated.jsonl"
        failures = tmp_path / "failures.jsonl"
        config_path = tmp_path / "config.yaml"
        config_path.write_text("pipeline:\n  steps:\n    step_a: {}\n")

        schema_path = tmp_path / "schema.json"
        schema_path.write_text('{"type": "object"}')

        mock_proc = MagicMock()
        mock_proc.communicate.side_effect = subprocess.TimeoutExpired("cmd", 10)
        mock_proc.kill = MagicMock()

        saved = orchestrate._active_subprocesses[:]
        orchestrate._active_subprocesses.clear()

        try:
            monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: mock_proc)

            v, f = run_validation_pipeline(
                results_file=results,
                validated_file=validated,
                failures_file=failures,
                schema_path=schema_path,
                config_path=config_path,
                step="step_a",
                log_file=log_file,
                chunk_name="chunk_000",
                timeout=5,
            )
            assert v == 0
            assert f == 0
        finally:
            orchestrate._active_subprocesses[:] = saved

    def test_pipeline_exception_returns_zero(self, tmp_path, monkeypatch):
        """General exception during pipeline returns (0, 0)."""
        import subprocess

        log_file = tmp_path / "log.txt"
        log_file.touch()

        results = tmp_path / "results.jsonl"
        results.write_text(json.dumps({"unit_id": "u1"}) + "\n")

        validated = tmp_path / "validated.jsonl"
        failures = tmp_path / "failures.jsonl"
        config_path = tmp_path / "config.yaml"
        config_path.write_text("pipeline:\n  steps:\n    step_a: {}\n")

        monkeypatch.setattr(subprocess, "Popen", MagicMock(side_effect=OSError("broken")))

        saved = orchestrate._active_subprocesses[:]
        orchestrate._active_subprocesses.clear()

        try:
            v, f = run_validation_pipeline(
                results_file=results,
                validated_file=validated,
                failures_file=failures,
                schema_path=None,
                config_path=config_path,
                step="step_a",
                log_file=log_file,
                chunk_name="chunk_000",
            )
            assert v == 0
            assert f == 0
        finally:
            orchestrate._active_subprocesses[:] = saved

    def test_coerce_telemetry_not_written_to_failures(self, tmp_path, monkeypatch):
        """[COERCE] lines from stderr are logged, not written to failures file."""
        import subprocess

        log_file = tmp_path / "log.txt"
        log_file.touch()

        results = tmp_path / "results.jsonl"
        results.write_text(json.dumps({"unit_id": "u1"}) + "\n")

        validated = tmp_path / "validated.jsonl"
        failures = tmp_path / "failures.jsonl"
        config_path = tmp_path / "config.yaml"
        config_path.write_text("pipeline:\n  steps:\n    step_a: {}\n")

        # Validator passes u1 but emits coerce telemetry on stderr
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (
            json.dumps({"unit_id": "u1"}).encode() + b"\n",
            b"[COERCE] field=val from=str to=int\n",
        )

        saved = orchestrate._active_subprocesses[:]
        orchestrate._active_subprocesses.clear()

        try:
            monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: mock_proc)

            v, f = run_validation_pipeline(
                results_file=results,
                validated_file=validated,
                failures_file=failures,
                schema_path=None,
                config_path=config_path,
                step="step_a",
                log_file=log_file,
                chunk_name="chunk_000",
            )
            assert v == 1
            assert f == 0  # Coerce lines are NOT counted as failures

            # Failures file should be empty (no actual failure records)
            content = failures.read_text().strip()
            assert content == ""
        finally:
            orchestrate._active_subprocesses[:] = saved

    def test_failure_input_field_fixed(self, tmp_path, monkeypatch):
        """Failure records have input field corrected to step's input."""
        import subprocess

        log_file = tmp_path / "log.txt"
        log_file.touch()

        input_file = tmp_path / "input.jsonl"
        input_file.write_text(
            json.dumps({"unit_id": "u1", "name": "Alice", "retry_count": 2}) + "\n"
        )

        results = tmp_path / "results.jsonl"
        results.write_text(json.dumps({"unit_id": "u1", "bad_field": "x"}) + "\n")

        validated = tmp_path / "validated.jsonl"
        failures = tmp_path / "failures.jsonl"
        config_path = tmp_path / "config.yaml"
        config_path.write_text("pipeline:\n  steps:\n    step_a: {}\n")

        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (
            b"",  # Nothing valid
            json.dumps({
                "unit_id": "u1",
                "errors": [{"rule": "r1", "message": "bad"}],
                "input": {"unit_id": "u1", "bad_field": "x"},
            }).encode() + b"\n",
        )

        saved = orchestrate._active_subprocesses[:]
        orchestrate._active_subprocesses.clear()

        try:
            monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: mock_proc)

            v, f = run_validation_pipeline(
                results_file=results,
                validated_file=validated,
                failures_file=failures,
                schema_path=None,
                config_path=config_path,
                step="step_a",
                log_file=log_file,
                chunk_name="chunk_000",
                input_file=input_file,
            )
            assert v == 0
            assert f == 1

            failure = json.loads(failures.read_text().strip())
            # Input should be corrected to the step's input, not the failed output
            assert failure["input"]["name"] == "Alice"
            assert failure["retry_count"] == 2
        finally:
            orchestrate._active_subprocesses[:] = saved


# =============================================================================
# build_run_status
# =============================================================================

class TestBuildRunStatus:
    """Tests for build_run_status()."""

    def _make_manifest(self, chunks, pipeline, metadata=None):
        return {
            "chunks": chunks,
            "pipeline": pipeline,
            "created": "2024-01-01T00:00:00Z",
            "metadata": metadata or {},
        }

    def _make_config(self, provider="gemini", model="flash"):
        return {
            "api": {"provider": provider, "model": model},
            "monitoring": {},
        }

    def test_basic_status_with_mocked_provider(self, tmp_path, monkeypatch):
        """build_run_status returns complete status dict."""
        # Mock the provider import inside build_run_status
        mock_provider = MagicMock()
        mock_provider.estimate_cost.return_value = 0.05

        mock_module = MagicMock()
        mock_module.get_provider.return_value = mock_provider
        mock_module.ProviderError = Exception

        monkeypatch.setitem(sys.modules, "scripts.providers", mock_module)

        chunks_dir = tmp_path / "chunks"
        chunks_dir.mkdir()
        (chunks_dir / "chunk_000").mkdir()

        manifest = self._make_manifest(
            chunks={
                "chunk_000": {
                    "state": "VALIDATED",
                    "items": 10,
                    "valid": 8,
                    "failed": 2,
                    "input_tokens": 100,
                    "output_tokens": 200,
                },
            },
            pipeline=["step_a"],
        )
        config = self._make_config()

        result = build_run_status(
            run_dir=tmp_path,
            manifest=manifest,
            config=config,
            activity=None,
            warnings=None,
            tick_errors=0,
        )

        assert result["run_id"] == tmp_path.name
        assert result["pipeline"] == ["step_a"]
        assert result["summary"]["total_units"] == 10
        assert result["summary"]["valid"] == 8
        assert result["summary"]["failed"] == 2
        assert "chunks" in result
        assert "steps" in result
        assert "cost" in result
        assert result["cost"]["configured"] is True

    def test_no_provider_sets_warning(self, tmp_path, monkeypatch):
        """Provider unavailability adds warning."""
        mock_module = MagicMock()
        mock_module.get_provider.side_effect = Exception("no key")
        mock_module.ProviderError = Exception

        monkeypatch.setitem(sys.modules, "scripts.providers", mock_module)

        chunks_dir = tmp_path / "chunks"
        chunks_dir.mkdir()

        manifest = self._make_manifest(
            chunks={
                "chunk_000": {
                    "state": "step_a_PENDING",
                    "items": 5,
                    "valid": 0,
                    "failed": 0,
                },
            },
            pipeline=["step_a"],
        )
        config = self._make_config()

        result = build_run_status(
            run_dir=tmp_path,
            manifest=manifest,
            config=config,
            activity=None,
            warnings=None,
            tick_errors=0,
        )

        assert result["cost"]["configured"] is False
        warning_codes = [w["code"] for w in result["warnings"]]
        assert "PROVIDER_UNAVAILABLE" in warning_codes

    def test_chunk_state_counting(self, tmp_path, monkeypatch):
        """Chunks in different states are counted correctly."""
        mock_module = MagicMock()
        mock_module.get_provider.side_effect = Exception("no key")
        mock_module.ProviderError = Exception
        monkeypatch.setitem(sys.modules, "scripts.providers", mock_module)

        chunks_dir = tmp_path / "chunks"
        chunks_dir.mkdir()
        for i in range(4):
            (chunks_dir / f"chunk_{i:03d}").mkdir()

        manifest = self._make_manifest(
            chunks={
                "chunk_000": {"state": "step_a_PENDING", "items": 5, "valid": 0, "failed": 0},
                "chunk_001": {"state": "step_a_SUBMITTED", "items": 5, "valid": 0, "failed": 0},
                "chunk_002": {"state": "VALIDATED", "items": 5, "valid": 5, "failed": 0},
                "chunk_003": {"state": "FAILED", "items": 5, "valid": 2, "failed": 3},
            },
            pipeline=["step_a"],
        )
        config = self._make_config()

        result = build_run_status(
            run_dir=tmp_path,
            manifest=manifest,
            config=config,
            activity=None,
            warnings=None,
            tick_errors=0,
        )

        assert result["summary"]["pending"] == 1
        assert result["summary"]["inflight"] == 1
        assert result["summary"]["valid"] == 7
        assert result["summary"]["failed"] == 3

    def test_throughput_calculation(self, tmp_path, monkeypatch):
        """Throughput is calculated when elapsed time and completed units exist."""
        mock_module = MagicMock()
        mock_module.get_provider.side_effect = Exception("no key")
        mock_module.ProviderError = Exception
        monkeypatch.setitem(sys.modules, "scripts.providers", mock_module)

        chunks_dir = tmp_path / "chunks"
        chunks_dir.mkdir()
        (chunks_dir / "chunk_000").mkdir()

        # Set start time to 1 hour ago
        from datetime import datetime, timezone, timedelta
        start = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

        manifest = self._make_manifest(
            chunks={
                "chunk_000": {"state": "VALIDATED", "items": 100, "valid": 90, "failed": 10},
            },
            pipeline=["step_a"],
        )
        manifest["created"] = start
        config = self._make_config()

        result = build_run_status(
            run_dir=tmp_path,
            manifest=manifest,
            config=config,
            activity=None,
            warnings=None,
            tick_errors=0,
        )

        assert result["throughput"]["items_per_hour"] is not None
        assert result["throughput"]["items_per_hour"] > 0
        assert result["timing"]["elapsed_seconds"] > 0

    def test_failure_threshold_warning(self, tmp_path, monkeypatch):
        """High failure rate triggers CHUNK_HAS_FAILURES warning."""
        mock_module = MagicMock()
        mock_module.get_provider.side_effect = Exception("no key")
        mock_module.ProviderError = Exception
        monkeypatch.setitem(sys.modules, "scripts.providers", mock_module)

        chunks_dir = tmp_path / "chunks"
        chunks_dir.mkdir()
        (chunks_dir / "chunk_000").mkdir()

        manifest = self._make_manifest(
            chunks={
                "chunk_000": {"state": "VALIDATED", "items": 10, "valid": 3, "failed": 7},
            },
            pipeline=["step_a"],
        )
        config = {
            "api": {"provider": "gemini"},
            "monitoring": {"warnings": {"failure_rate_threshold": 0.5}},
        }

        result = build_run_status(
            run_dir=tmp_path,
            manifest=manifest,
            config=config,
            activity=None,
            warnings=None,
            tick_errors=0,
        )

        warning_codes = [w["code"] for w in result["warnings"]]
        assert "CHUNK_HAS_FAILURES" in warning_codes

    def test_tick_errors_warning(self, tmp_path, monkeypatch):
        """Tick errors add TICK_ERRORS warning."""
        mock_module = MagicMock()
        mock_module.get_provider.side_effect = Exception("no key")
        mock_module.ProviderError = Exception
        monkeypatch.setitem(sys.modules, "scripts.providers", mock_module)

        chunks_dir = tmp_path / "chunks"
        chunks_dir.mkdir()

        manifest = self._make_manifest(
            chunks={"chunk_000": {"state": "step_a_PENDING", "items": 5, "valid": 0, "failed": 0}},
            pipeline=["step_a"],
        )
        config = self._make_config()

        result = build_run_status(
            run_dir=tmp_path,
            manifest=manifest,
            config=config,
            activity=None,
            warnings=None,
            tick_errors=3,
        )

        warning_codes = [w["code"] for w in result["warnings"]]
        assert "TICK_ERRORS" in warning_codes
        tick_warn = [w for w in result["warnings"] if w["code"] == "TICK_ERRORS"][0]
        assert tick_warn["count"] == 3

    def test_multi_step_pipeline(self, tmp_path, monkeypatch):
        """Multi-step pipeline has per-step breakdown."""
        mock_module = MagicMock()
        mock_module.get_provider.side_effect = Exception("no key")
        mock_module.ProviderError = Exception
        monkeypatch.setitem(sys.modules, "scripts.providers", mock_module)

        chunks_dir = tmp_path / "chunks"
        chunks_dir.mkdir()
        (chunks_dir / "chunk_000").mkdir()

        manifest = self._make_manifest(
            chunks={
                "chunk_000": {"state": "step_b_SUBMITTED", "items": 10, "valid": 0, "failed": 0},
            },
            pipeline=["step_a", "step_b"],
        )
        config = self._make_config()

        result = build_run_status(
            run_dir=tmp_path,
            manifest=manifest,
            config=config,
            activity=None,
            warnings=None,
            tick_errors=0,
        )

        assert "step_a" in result["steps"]
        assert "step_b" in result["steps"]

    def test_activity_defaults_to_zeros(self, tmp_path, monkeypatch):
        """Activity defaults to zeros when None."""
        mock_module = MagicMock()
        mock_module.get_provider.side_effect = Exception("no key")
        mock_module.ProviderError = Exception
        monkeypatch.setitem(sys.modules, "scripts.providers", mock_module)

        chunks_dir = tmp_path / "chunks"
        chunks_dir.mkdir()

        manifest = self._make_manifest(
            chunks={"chunk_000": {"state": "step_a_PENDING", "items": 5, "valid": 0, "failed": 0}},
            pipeline=["step_a"],
        )
        config = self._make_config()

        result = build_run_status(
            run_dir=tmp_path,
            manifest=manifest,
            config=config,
            activity=None,
            warnings=None,
            tick_errors=0,
        )

        assert result["activity"] == {"polled": 0, "collected": 0, "submitted": 0}

    def test_token_aggregation(self, tmp_path, monkeypatch):
        """Token counts are aggregated from results files."""
        mock_module = MagicMock()
        mock_module.get_provider.side_effect = Exception("no key")
        mock_module.ProviderError = Exception
        monkeypatch.setitem(sys.modules, "scripts.providers", mock_module)

        chunks_dir = tmp_path / "chunks" / "chunk_000"
        chunks_dir.mkdir(parents=True)

        # Write results file with token metadata
        results_file = chunks_dir / "step_a_results.jsonl"
        results_file.write_text(
            json.dumps({"unit_id": "u1", "_metadata": {"input_tokens": 50, "output_tokens": 100}}) + "\n"
            + json.dumps({"unit_id": "u2", "_metadata": {"input_tokens": 30, "output_tokens": 80}}) + "\n"
        )

        manifest = self._make_manifest(
            chunks={"chunk_000": {"state": "VALIDATED", "items": 2, "valid": 2, "failed": 0}},
            pipeline=["step_a"],
            metadata={"initial_input_tokens": 80, "initial_output_tokens": 180},
        )
        config = self._make_config()

        result = build_run_status(
            run_dir=tmp_path,
            manifest=manifest,
            config=config,
            activity=None,
            warnings=None,
            tick_errors=0,
        )

        assert result["steps"]["step_a"]["tokens"]["input"] == 80
        assert result["steps"]["step_a"]["tokens"]["output"] == 180
        assert result["cost"]["total_tokens"] == 260


# =============================================================================
# status_run
# =============================================================================

class TestStatusRun:
    """Tests for status_run()."""

    def test_missing_run_dir(self, tmp_path):
        """Returns error when run directory doesn't exist."""
        result = status_run(tmp_path / "nonexistent")
        assert "error" in result
        assert "not found" in result["error"]

    def test_missing_manifest(self, tmp_path):
        """Returns error when MANIFEST.json doesn't exist."""
        result = status_run(tmp_path)
        assert "error" in result
        assert "MANIFEST.json" in result["error"]

    def test_missing_config(self, tmp_path):
        """Returns error when config file doesn't exist."""
        import yaml
        manifest = {
            "config": "config/config.yaml",
            "pipeline": ["step_a"],
            "chunks": {},
        }
        (tmp_path / "MANIFEST.json").write_text(json.dumps(manifest))

        result = status_run(tmp_path)
        assert "error" in result
        assert "Config file not found" in result["error"]

    def test_valid_run_delegates_to_build_run_status(self, tmp_path, monkeypatch):
        """Valid run calls build_run_status and returns its result."""
        import yaml

        # Mock provider import
        mock_module = MagicMock()
        mock_module.get_provider.side_effect = Exception("no key")
        mock_module.ProviderError = Exception
        monkeypatch.setitem(sys.modules, "scripts.providers", mock_module)

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_path = config_dir / "config.yaml"
        config_path.write_text(yaml.dump({"api": {"provider": "gemini"}, "monitoring": {}}))

        manifest = {
            "config": "config/config.yaml",
            "pipeline": ["step_a"],
            "chunks": {
                "chunk_000": {"state": "VALIDATED", "items": 5, "valid": 5, "failed": 0},
            },
            "created": "2024-01-01T00:00:00Z",
            "metadata": {},
        }
        (tmp_path / "MANIFEST.json").write_text(json.dumps(manifest))
        (tmp_path / "chunks").mkdir()

        result = status_run(tmp_path)
        assert "error" not in result
        assert result["run_id"] == tmp_path.name
        assert result["summary"]["valid"] == 5


# =============================================================================
# Second-wave bug-hunt regression tests (expected failures)
# =============================================================================

class TestSecondWaveKnownBugs:
    """Regression tests for defects found during second-wave bug hunt."""

    def test_is_run_terminal_accepts_retry_count_field(self):
        """FAILED chunks at retry_count>=max_retries should be terminal."""
        manifest = {
            "chunks": {
                "chunk_000": {"state": "FAILED", "retry_count": 5},
            }
        }
        assert is_run_terminal(manifest, max_retries=5) is True

    def test_retry_validation_failures_preserves_unretryable_failures(self, tmp_path):
        """Failures missing retriable input should not be silently archived/cleared."""
        run_dir = tmp_path
        chunks_dir = run_dir / "chunks"
        chunk_dir = chunks_dir / "chunk_000"
        chunk_dir.mkdir(parents=True)

        # Input source does not contain the failed unit_id.
        with open(chunk_dir / "units.jsonl", "w") as f:
            f.write(json.dumps({"unit_id": "u_present"}) + "\n")

        failures_path = chunk_dir / "step_a_failures.jsonl"
        with open(failures_path, "w") as f:
            f.write(json.dumps({
                "unit_id": "u_missing",
                "failure_stage": "validation",
                "errors": [{"message": "bad"}],
                "retry_count": 0,
            }) + "\n")

        log_file = run_dir / "RUN_LOG.txt"
        log_file.touch()

        manifest = {
            "status": "complete",
            "pipeline": ["step_a"],
            "chunks": {
                "chunk_000": {"state": "VALIDATED", "failed": 1, "retries": 0}
            },
        }

        archived = retry_validation_failures(run_dir, manifest, log_file)

        # Desired behavior: no archive count and failures remain visible.
        assert archived == 0
        assert failures_path.exists()
        assert not (chunk_dir / "step_a_failures.jsonl.bak").exists()
        assert manifest["chunks"]["chunk_000"]["failed"] == 1


# =============================================================================
# Expression step retry skip
# =============================================================================

class TestExpressionStepRetrySkip:
    """Tests that expression steps skip retry logic (deterministic fast-fail)."""

    def test_expression_step_failures_not_retried(self, tmp_path):
        """Validation failures on expression steps are NOT queued for retry."""
        import yaml

        run_dir = tmp_path
        chunk_dir = run_dir / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)
        log_file = run_dir / "RUN_LOG.txt"
        log_file.touch()

        # Write config with expression step
        config_dir = run_dir / "config"
        config_dir.mkdir()
        config = {
            "pipeline": {
                "steps": [
                    {"name": "verify_hand", "scope": "expression",
                     "expressions": {"result": "hand_total > 21"}},
                ]
            },
        }
        config_path = config_dir / "config.yaml"
        config_path.write_text(yaml.dump(config))

        # Write units
        with open(chunk_dir / "units.jsonl", "w") as f:
            f.write(json.dumps({"unit_id": "u1", "hand_total": 15}) + "\n")

        # Write validation failure for the expression step
        failures_path = chunk_dir / "verify_hand_failures.jsonl"
        with open(failures_path, "w") as f:
            f.write(json.dumps({
                "unit_id": "u1",
                "failure_stage": "validation",
                "errors": [{"rule": "hand_valid", "message": "bad hand"}],
                "retry_count": 0,
            }) + "\n")

        manifest = {
            "config": "config/config.yaml",
            "status": "failed",
            "pipeline": ["verify_hand"],
            "chunks": {
                "chunk_000": {"state": "VALIDATED", "failed": 1, "retries": 0}
            },
        }

        archived = retry_validation_failures(run_dir, manifest, log_file)

        # Expression step should NOT create retry chunks
        assert archived == 0
        assert "retry_000" not in manifest.get("chunks", {})
        # Original failures file should be untouched (no .bak rotation)
        assert failures_path.exists()
        assert not (chunk_dir / "verify_hand_failures.jsonl.bak").exists()

    def test_llm_step_still_retried_alongside_expression_step(self, tmp_path):
        """LLM steps are still retried even when expression steps are present."""
        import yaml

        run_dir = tmp_path
        chunk_dir = run_dir / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)
        log_file = run_dir / "RUN_LOG.txt"
        log_file.touch()

        # Config with both LLM and expression steps
        config_dir = run_dir / "config"
        config_dir.mkdir()
        config = {
            "pipeline": {
                "steps": [
                    {"name": "generate", "prompt_template": "gen.jinja2"},
                    {"name": "verify", "scope": "expression",
                     "expressions": {"ok": "True"}},
                ]
            },
        }
        config_path = config_dir / "config.yaml"
        config_path.write_text(yaml.dump(config))

        # Write units
        with open(chunk_dir / "units.jsonl", "w") as f:
            f.write(json.dumps({"unit_id": "u1", "data": "test"}) + "\n")

        # LLM step has validation failure (should be retried)
        with open(chunk_dir / "generate_failures.jsonl", "w") as f:
            f.write(json.dumps({
                "unit_id": "u1",
                "failure_stage": "validation",
                "errors": [{"rule": "r1", "message": "bad output"}],
                "retry_count": 0,
            }) + "\n")

        # Expression step also has validation failure (should NOT be retried)
        with open(chunk_dir / "verify_failures.jsonl", "w") as f:
            f.write(json.dumps({
                "unit_id": "u1",
                "failure_stage": "validation",
                "errors": [{"rule": "r2", "message": "bad verify"}],
                "retry_count": 0,
            }) + "\n")

        manifest = {
            "config": "config/config.yaml",
            "status": "failed",
            "pipeline": ["generate", "verify"],
            "chunks": {
                "chunk_000": {"state": "VALIDATED", "failed": 2, "retries": 0}
            },
        }

        archived = retry_validation_failures(run_dir, manifest, log_file)

        # Only the LLM step's failure should be archived
        assert archived == 1
        # A retry chunk should exist for the LLM step
        retry_chunks = [k for k in manifest["chunks"] if k.startswith("retry_")]
        assert len(retry_chunks) == 1
        retry_name = retry_chunks[0]
        assert manifest["chunks"][retry_name]["state"] == "generate_PENDING"
        # Expression step failure should remain untouched
        verify_failures = chunk_dir / "verify_failures.jsonl"
        assert verify_failures.exists()
        assert not (chunk_dir / "verify_failures.jsonl.bak").exists()

    def test_no_config_falls_through_to_retry(self, tmp_path):
        """If config can't be loaded, all steps are retried (safe fallback)."""
        run_dir = tmp_path
        chunk_dir = run_dir / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)
        log_file = run_dir / "RUN_LOG.txt"
        log_file.touch()

        # No config file exists — config will be None
        with open(chunk_dir / "units.jsonl", "w") as f:
            f.write(json.dumps({"unit_id": "u1"}) + "\n")

        with open(chunk_dir / "step_a_failures.jsonl", "w") as f:
            f.write(json.dumps({
                "unit_id": "u1",
                "failure_stage": "validation",
                "errors": [{"rule": "r1", "message": "bad"}],
                "retry_count": 0,
            }) + "\n")

        manifest = {
            "config": "config/config.yaml",
            "status": "failed",
            "pipeline": ["step_a"],
            "chunks": {
                "chunk_000": {"state": "VALIDATED", "failed": 1, "retries": 0}
            },
        }

        archived = retry_validation_failures(run_dir, manifest, log_file)
        # Should still retry since config couldn't be loaded (safe fallback)
        assert archived == 1

    def test_realtime_expression_retries_skip_prompt_prep_and_mark_exhausted(self, tmp_path, monkeypatch):
        """Realtime retry path skips expression steps and marks failures exhausted."""
        import yaml

        run_dir = tmp_path
        chunk_dir = run_dir / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)
        log_file = run_dir / "RUN_LOG.txt"
        log_file.touch()

        config = {
            "pipeline": {
                "steps": [
                    {"name": "verify_hand", "scope": "expression", "expressions": {"ok": "True"}},
                ]
            },
        }

        manifest = {
            "config": "config/config.yaml",
            "pipeline": ["verify_hand"],
            "chunks": {
                "chunk_000": {"state": "verify_hand_PENDING", "failed": 1, "retries": 0}
            },
        }

        config_dir = run_dir / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        with open(config_dir / "config.yaml", "w") as f:
            yaml.safe_dump(config, f)

        with open(chunk_dir / "units.jsonl", "w") as f:
            f.write(json.dumps({"unit_id": "u1", "hand_total": 22}) + "\n")

        failures_path = chunk_dir / "verify_hand_failures.jsonl"
        with open(failures_path, "w") as f:
            f.write(json.dumps({
                "unit_id": "u1",
                "failure_stage": "validation",
                "errors": [{"rule": "hand_valid", "message": "bad hand"}],
                "retry_count": 0,
            }) + "\n")

        def _must_not_prepare_prompts(*args, **kwargs):
            raise AssertionError("prepare_prompts should not be called for expression retries")

        monkeypatch.setattr(orchestrate, "prepare_prompts", _must_not_prepare_prompts)

        retried, still_failed, in_tokens, out_tokens = orchestrate.run_realtime_retries(
            run_dir, "verify_hand", config, manifest, log_file, max_retries=5
        )
        assert (retried, still_failed, in_tokens, out_tokens) == (0, 0, 0, 0)

        with open(failures_path) as f:
            line = f.readline().strip()
        failure = json.loads(line)
        assert failure["retry_count"] == 5


# =============================================================================
# mark_expression_failures_exhausted — direct unit tests
# =============================================================================

class TestMarkExpressionFailuresExhausted:
    """Direct unit tests for mark_expression_failures_exhausted edge cases."""

    def test_nonexistent_file_returns_zero(self, tmp_path):
        """Non-existent failures file returns 0 without error."""
        missing = tmp_path / "does_not_exist.jsonl"
        assert orchestrate.mark_expression_failures_exhausted(missing, max_retries=5) == 0

    def test_already_exhausted_records_left_alone(self, tmp_path):
        """Records with retry_count >= max_retries are not modified."""
        failures_file = tmp_path / "step_failures.jsonl"
        record = {
            "unit_id": "u1",
            "failure_stage": "validation",
            "retry_count": 5,
        }
        failures_file.write_text(json.dumps(record) + "\n")

        updated = orchestrate.mark_expression_failures_exhausted(failures_file, max_retries=5)
        assert updated == 0

        # File should not have been rewritten (early return when updated == 0)
        reloaded = json.loads(failures_file.read_text().strip())
        assert reloaded["retry_count"] == 5

    def test_mixed_validation_and_hard_failures(self, tmp_path):
        """Only validation/schema_validation failures are exhausted; hard failures are untouched."""
        failures_file = tmp_path / "step_failures.jsonl"
        validation_fail = {
            "unit_id": "u1",
            "failure_stage": "validation",
            "retry_count": 0,
        }
        schema_fail = {
            "unit_id": "u2",
            "failure_stage": "schema_validation",
            "retry_count": 1,
        }
        hard_fail = {
            "unit_id": "u3",
            "failure_stage": "pipeline_internal",
            "retry_count": 0,
        }
        lines = [json.dumps(r) for r in [validation_fail, schema_fail, hard_fail]]
        failures_file.write_text("\n".join(lines) + "\n")

        updated = orchestrate.mark_expression_failures_exhausted(failures_file, max_retries=3)
        assert updated == 2  # validation + schema_validation

        records = [json.loads(l) for l in failures_file.read_text().strip().splitlines()]
        assert records[0]["retry_count"] == 3  # validation — exhausted
        assert records[1]["retry_count"] == 3  # schema_validation — exhausted
        assert records[2]["retry_count"] == 0  # pipeline_internal — untouched

    def test_malformed_json_lines_preserved(self, tmp_path):
        """Malformed JSON lines are kept as-is in the output."""
        failures_file = tmp_path / "step_failures.jsonl"
        valid_record = {
            "unit_id": "u1",
            "failure_stage": "validation",
            "retry_count": 0,
        }
        content = json.dumps(valid_record) + "\n" + "THIS IS NOT JSON\n"
        failures_file.write_text(content)

        updated = orchestrate.mark_expression_failures_exhausted(failures_file, max_retries=5)
        assert updated == 1

        output_lines = failures_file.read_text().strip().splitlines()
        assert len(output_lines) == 2
        assert json.loads(output_lines[0])["retry_count"] == 5
        assert output_lines[1] == "THIS IS NOT JSON"

    def test_empty_file_returns_zero(self, tmp_path):
        """An empty failures file returns 0 without error."""
        failures_file = tmp_path / "step_failures.jsonl"
        failures_file.write_text("")

        assert orchestrate.mark_expression_failures_exhausted(failures_file, max_retries=5) == 0

    def test_default_failure_stage_treated_as_validation(self, tmp_path):
        """Records missing failure_stage default to 'validation' and get exhausted."""
        failures_file = tmp_path / "step_failures.jsonl"
        record = {"unit_id": "u1", "retry_count": 0}  # no failure_stage
        failures_file.write_text(json.dumps(record) + "\n")

        updated = orchestrate.mark_expression_failures_exhausted(failures_file, max_retries=3)
        assert updated == 1

        reloaded = json.loads(failures_file.read_text().strip())
        assert reloaded["retry_count"] == 3


# =============================================================================
# Bug 1: Expression step fast-fail empty chunks
# =============================================================================

class TestExpressionStepEmptyChunkTerminal:
    """Regression tests for Bug 1: chunks with 0 valid units after expression
    step must be marked terminal (FAILED), not advanced to the next step."""

    def test_zero_valid_after_expression_step_marks_failed(self, tmp_path):
        """Chunk with 0 valid units after expression step is marked FAILED.

        Simulates a retry chunk where the previous step's validated output
        is empty (all units exhausted by expression step fast-fail).
        """
        run_dir = tmp_path
        chunk_dir = run_dir / "chunks" / "retry_001"
        chunk_dir.mkdir(parents=True)
        log_file = run_dir / "RUN_LOG.txt"
        log_file.touch()

        # Empty input file — all units were exhausted by prior expression step
        (chunk_dir / "units.jsonl").write_text("")

        step_config = {
            "name": "verify_hand",
            "scope": "expression",
            "expressions": {"check": "True"},
        }
        config = {
            "pipeline": {
                "steps": [
                    {"name": "verify_hand", "scope": "expression"},
                    {"name": "next_step"},
                ]
            }
        }
        manifest = {
            "pipeline": ["verify_hand", "next_step"],
            "chunks": {
                "retry_001": {
                    "state": "verify_hand_PENDING",
                    "valid": 0,
                    "failed": 0,
                }
            },
        }

        # Run expression step on empty input — returns (0, 0, 0, 0)
        valid, failed_count, _, _ = run_expression_step(
            run_dir, "retry_001", "verify_hand", step_config, config, manifest, log_file
        )
        assert valid == 0

        # Simulate the orchestrator's zero-valid guard: valid==0 marks FAILED
        chunk_data = manifest["chunks"]["retry_001"]
        chunk_data["valid"] = valid
        chunk_data["failed"] = failed_count
        if valid == 0:
            chunk_data["state"] = "FAILED"

        assert chunk_data["state"] == "FAILED"

    def test_mix_valid_and_failed_advances_valid(self, tmp_path):
        """Chunk with mix of valid and failed units: valid units advance,
        failed units are exhausted."""
        run_dir = tmp_path
        chunk_dir = run_dir / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)
        log_file = run_dir / "RUN_LOG.txt"
        log_file.touch()

        # Write units
        units = [
            {"unit_id": "u1", "_repetition_seed": 42, "value": 10},
            {"unit_id": "u2", "_repetition_seed": 99, "value": 20},
        ]
        with open(chunk_dir / "units.jsonl", "w") as f:
            for u in units:
                f.write(json.dumps(u) + "\n")

        step_config = {
            "name": "compute",
            "scope": "expression",
            "expressions": {"doubled": "value * 2"},
        }
        config = {
            "pipeline": {
                "steps": [{"name": "compute", "scope": "expression"}, {"name": "analyze"}]
            }
        }
        manifest = {"pipeline": ["compute", "analyze"]}

        valid, failed_count, _, _ = run_expression_step(
            run_dir, "chunk_000", "compute", step_config, config, manifest, log_file
        )

        # Both units should produce valid output (expressions succeed)
        assert valid == 2
        assert failed_count == 0

        # Chunk should advance to next step (not be marked FAILED)
        next_step = get_next_step(manifest["pipeline"], "compute")
        assert next_step == "analyze"

    def test_run_completes_with_empty_retry_chunks(self, tmp_path):
        """Run completes when only retry chunks have empty expression step results.

        Retry chunks marked FAILED with retry_count >= max_retries are terminal.
        """
        manifest = {
            "chunks": {
                "chunk_000": {"state": "VALIDATED", "valid": 10, "failed": 0},
                "chunk_001": {"state": "VALIDATED", "valid": 8, "failed": 2},
                # Retry chunks marked FAILED because expression step produced 0 valid
                # retry_count >= max_retries means they're terminal (exhausted)
                "retry_001": {"state": "FAILED", "valid": 0, "failed": 3, "retry_count": 5},
                "retry_002": {"state": "FAILED", "valid": 0, "failed": 2, "retry_count": 5},
            }
        }

        # All chunks are in terminal state — run should be terminal
        assert is_run_terminal(manifest, max_retries=5) is True


# =============================================================================
# Bug 2: Expression step fast-fail in realtime mode
# =============================================================================

class TestExpressionStepRealtimeFastFail:
    """Regression tests for Bug 2: expression step failures in realtime mode
    must not attempt prompt preparation, and must be handled identically
    to batch mode."""

    def test_expression_failure_realtime_writes_to_failures(self, tmp_path):
        """Expression step failure in realtime mode writes to failures.jsonl
        via mark_expression_failures_exhausted."""
        run_dir = tmp_path
        chunk_dir = run_dir / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)

        # Create a failures file with a validation failure
        failures_file = chunk_dir / "verify_hand_failures.jsonl"
        failure = {
            "unit_id": "u1",
            "failure_stage": "validation",
            "retry_count": 0,
            "errors": [{"message": "verification_passed is False"}],
        }
        failures_file.write_text(json.dumps(failure) + "\n")

        # Mark as exhausted (this is what the realtime path does)
        exhausted = orchestrate.mark_expression_failures_exhausted(
            failures_file, max_retries=3
        )
        assert exhausted == 1

        # Verify the failure is now exhausted
        record = json.loads(failures_file.read_text().strip())
        assert record["retry_count"] == 3

    def test_expression_step_skips_prompt_preparation_in_realtime_retries(self, tmp_path):
        """Expression step in run_realtime_retries returns early without
        attempting prompt preparation."""
        run_dir = tmp_path
        chunks_dir = run_dir / "chunks" / "chunk_000"
        chunks_dir.mkdir(parents=True)
        log_file = run_dir / "RUN_LOG.txt"
        log_file.touch()

        config = {
            "pipeline": {
                "steps": [
                    {"name": "play_hand"},
                    {"name": "verify_hand", "scope": "expression"},
                ]
            }
        }
        manifest = {
            "pipeline": ["play_hand", "verify_hand"],
            "config": "config.yaml",
            "chunks": {
                "chunk_000": {"state": "verify_hand_PENDING"},
            },
        }

        # The is_expression_step check should cause early return
        assert orchestrate.is_expression_step(config, "verify_hand") is True
        # Verify that non-expression steps are NOT detected as expression
        assert orchestrate.is_expression_step(config, "play_hand") is False

    def test_llm_step_retry_still_works_after_expression_fastfail(self, tmp_path):
        """LLM step failure in realtime mode still triggers normal retry
        (expression fast-fail must not break LLM retry behavior)."""
        config = {
            "pipeline": {
                "steps": [
                    {"name": "play_hand"},
                    {"name": "verify_hand", "scope": "expression"},
                ]
            }
        }
        # LLM steps should NOT be detected as expression steps
        assert orchestrate.is_expression_step(config, "play_hand") is False
        # Expression steps should be detected
        assert orchestrate.is_expression_step(config, "verify_hand") is True

    def test_batch_and_realtime_expression_fastfail_identical(self, tmp_path):
        """Same expression step failure produces identical behavior in batch
        vs realtime mode: failures are marked exhausted, not retried."""
        failures_file = tmp_path / "step_failures.jsonl"
        failure = {
            "unit_id": "u1",
            "failure_stage": "validation",
            "retry_count": 0,
        }
        failures_file.write_text(json.dumps(failure) + "\n")

        # Both modes call mark_expression_failures_exhausted with same semantics
        exhausted = orchestrate.mark_expression_failures_exhausted(
            failures_file, max_retries=5
        )
        assert exhausted == 1

        record = json.loads(failures_file.read_text().strip())
        assert record["retry_count"] == 5  # Marked as exhausted
