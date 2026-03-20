"""
Tests for scripts/octobatch_utils.py.

Covers:
- load_config: YAML loading and error cases
- load_manifest / save_manifest: round-trip, atomic write, summary generation
- _build_summary: status inference, progress, unit counts, tokens, cost, current step
- _compute_summary_cost: model registry pricing, defaults, realtime multiplier
- load_jsonl: plain, gzipped, .gz fallback, missing files, invalid JSON lines
- load_jsonl_by_id: indexing by field
- append_jsonl: appending single record
- write_jsonl: writing records, parent dir creation
- log_error: structured JSON to stderr
- log_message: timestamped log to file + optional stderr
- trace_log: best-effort trace logging
- format_elapsed_time: seconds, minutes, hours formatting
- compute_cost: pricing calculation and None pricing
- create_interpreter: safe builtins
- parse_json_response: markdown blocks, +N numbers, trailing commas
"""

import gzip
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from octobatch_utils import (
    _build_summary,
    _compute_summary_cost,
    append_jsonl,
    compute_cost,
    create_interpreter,
    format_elapsed_time,
    load_config,
    load_jsonl,
    load_jsonl_by_id,
    load_manifest,
    log_error,
    log_message,
    parse_json_response,
    save_manifest,
    trace_log,
    write_jsonl,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def minimal_manifest():
    """A minimal manifest with metadata and chunks."""
    return {
        "metadata": {
            "provider": "gemini",
            "model": "gemini-2.0-flash-001",
            "mode": "batch",
            "pipeline_name": "test-pipeline",
            "start_time": "2025-01-01T00:00:00Z",
            "initial_input_tokens": 1000,
            "initial_output_tokens": 500,
            "retry_input_tokens": 100,
            "retry_output_tokens": 50,
        },
        "pipeline": ["generate", "validate"],
        "chunks": {
            "chunk_000": {
                "state": "VALIDATED",
                "items": 10,
                "valid": 9,
                "failed": 1,
            },
            "chunk_001": {
                "state": "generate_DONE",
                "items": 10,
                "valid": 0,
                "failed": 0,
            },
        },
        "created": "2025-01-01T00:00:00Z",
    }


@pytest.fixture
def sample_jsonl_records():
    """Sample JSONL records for testing."""
    return [
        {"unit_id": "u1", "name": "Alice", "score": 95},
        {"unit_id": "u2", "name": "Bob", "score": 87},
        {"unit_id": "u3", "name": "Charlie", "score": 72},
    ]


# =============================================================================
# load_config
# =============================================================================

class TestLoadConfig:

    def test_load_valid_config(self, tmp_path):
        config_data = {"pipeline": {"steps": [{"name": "generate"}]}}
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(config_data))

        result = load_config(config_file)
        assert result == config_data

    def test_load_config_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.yaml")

    def test_load_config_empty_file(self, tmp_path):
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("")
        result = load_config(config_file)
        assert result is None


# =============================================================================
# load_manifest / save_manifest
# =============================================================================

class TestManifest:

    def test_load_manifest(self, tmp_path):
        manifest_data = {"status": "running", "chunks": {}}
        manifest_path = tmp_path / "MANIFEST.json"
        manifest_path.write_text(json.dumps(manifest_data))

        result = load_manifest(tmp_path)
        assert result == manifest_data

    def test_load_manifest_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_manifest(tmp_path)

    def test_save_manifest_creates_file(self, tmp_path, minimal_manifest):
        save_manifest(tmp_path, minimal_manifest)

        manifest_path = tmp_path / "MANIFEST.json"
        assert manifest_path.exists()

        loaded = json.loads(manifest_path.read_text())
        assert "updated" in loaded
        assert loaded["metadata"] == minimal_manifest["metadata"]

    def test_save_manifest_updates_timestamp(self, tmp_path, minimal_manifest):
        minimal_manifest["updated"] = "1999-01-01T00:00:00Z"
        save_manifest(tmp_path, minimal_manifest)

        loaded = json.loads((tmp_path / "MANIFEST.json").read_text())
        # The timestamp should have been updated to a recent time, not 1999
        assert loaded["updated"] != "1999-01-01T00:00:00Z"
        assert loaded["updated"].endswith("Z")

    def test_save_manifest_creates_summary(self, tmp_path, minimal_manifest):
        save_manifest(tmp_path, minimal_manifest)

        summary_path = tmp_path / ".manifest_summary.json"
        assert summary_path.exists()

        summary = json.loads(summary_path.read_text())
        assert "status" in summary
        assert "progress" in summary
        assert "total_units" in summary
        assert "cost" in summary

    def test_save_manifest_round_trip(self, tmp_path, minimal_manifest):
        save_manifest(tmp_path, minimal_manifest)
        loaded = load_manifest(tmp_path)

        assert loaded["metadata"] == minimal_manifest["metadata"]
        assert loaded["chunks"] == minimal_manifest["chunks"]
        assert loaded["pipeline"] == minimal_manifest["pipeline"]

    def test_save_manifest_atomic_replaces_existing(self, tmp_path, minimal_manifest):
        """Save twice and verify the file is properly replaced."""
        save_manifest(tmp_path, minimal_manifest)

        minimal_manifest["chunks"]["chunk_002"] = {"state": "PENDING", "items": 5, "valid": 0, "failed": 0}
        save_manifest(tmp_path, minimal_manifest)

        loaded = json.loads((tmp_path / "MANIFEST.json").read_text())
        assert "chunk_002" in loaded["chunks"]

    def test_save_manifest_no_temp_files_left(self, tmp_path, minimal_manifest):
        """After save, no .tmp files should remain."""
        save_manifest(tmp_path, minimal_manifest)

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0


# =============================================================================
# _build_summary
# =============================================================================

class TestBuildSummary:

    def test_basic_fields(self, minimal_manifest):
        summary = _build_summary(minimal_manifest)

        assert summary["mode"] == "batch"
        assert summary["pipeline_name"] == "test-pipeline"
        assert summary["provider"] == "gemini"
        assert summary["model"] == "gemini-2.0-flash-001"
        assert summary["pipeline"] == ["generate", "validate"]

    def test_status_complete_all_validated(self):
        manifest = {
            "chunks": {
                "c0": {"state": "VALIDATED", "items": 5, "valid": 5, "failed": 0},
                "c1": {"state": "VALIDATED", "items": 5, "valid": 5, "failed": 0},
            },
            "metadata": {},
            "pipeline": ["step1"],
        }
        summary = _build_summary(manifest)
        assert summary["status"] == "complete"
        assert summary["progress"] == 100

    def test_status_failed_has_failed_chunks(self):
        manifest = {
            "chunks": {
                "c0": {"state": "VALIDATED", "items": 5, "valid": 5, "failed": 0},
                "c1": {"state": "FAILED", "items": 5, "valid": 0, "failed": 5},
            },
            "metadata": {},
            "pipeline": ["step1"],
        }
        summary = _build_summary(manifest)
        assert summary["status"] == "failed"

    def test_status_active_from_running(self):
        manifest = {
            "status": "running",
            "chunks": {
                "c0": {"state": "step1_DONE", "items": 5, "valid": 0, "failed": 0},
            },
            "metadata": {},
            "pipeline": ["step1", "step2"],
        }
        summary = _build_summary(manifest)
        assert summary["status"] == "active"

    def test_status_running_with_failed_chunks_returns_active(self):
        """A running manifest with some failed chunks should return 'active',
        not 'failed'. The orchestrator is still processing other chunks."""
        manifest = {
            "status": "running",
            "chunks": {
                "c0": {"state": "VALIDATED", "items": 50, "valid": 50, "failed": 0},
                "c1": {"state": "FAILED", "items": 50, "valid": 49, "failed": 0},
                "c2": {"state": "score_PENDING", "items": 50, "valid": 50, "failed": 0},
            },
            "metadata": {},
            "pipeline": ["generate", "refine", "score"],
        }
        summary = _build_summary(manifest)
        assert summary["status"] == "active"

    def test_status_explicit_paused(self):
        manifest = {
            "status": "paused",
            "chunks": {"c0": {"state": "VALIDATED", "items": 5, "valid": 5, "failed": 0}},
            "metadata": {},
            "pipeline": ["step1"],
        }
        summary = _build_summary(manifest)
        assert summary["status"] == "paused"

    def test_status_explicit_killed(self):
        manifest = {
            "status": "killed",
            "chunks": {},
            "metadata": {},
            "pipeline": [],
        }
        summary = _build_summary(manifest)
        assert summary["status"] == "killed"

    def test_status_explicit_complete(self):
        manifest = {
            "status": "complete",
            "chunks": {},
            "metadata": {},
            "pipeline": [],
        }
        summary = _build_summary(manifest)
        assert summary["status"] == "complete"
        assert summary["progress"] == 100

    def test_status_explicit_failed(self):
        manifest = {
            "status": "failed",
            "chunks": {},
            "metadata": {},
            "pipeline": [],
        }
        summary = _build_summary(manifest)
        assert summary["status"] == "failed"

    def test_progress_partial(self):
        """Two chunks, two-step pipeline: one VALIDATED, one at step1."""
        manifest = {
            "chunks": {
                "c0": {"state": "VALIDATED", "items": 5, "valid": 5, "failed": 0},
                "c1": {"state": "generate_DONE", "items": 5, "valid": 0, "failed": 0},
            },
            "metadata": {},
            "pipeline": ["generate", "validate"],
        }
        summary = _build_summary(manifest)
        # c0: 2 steps completed, c1: generate is index 0, so 0 steps completed
        # total_work = 2 * 2 = 4, completed = 2 + 0 = 2
        # progress = int(2/4 * 100) = 50
        assert summary["progress"] == 50

    def test_progress_no_chunks(self):
        manifest = {"chunks": {}, "metadata": {}, "pipeline": ["step1"]}
        summary = _build_summary(manifest)
        assert summary["progress"] == 0

    def test_progress_no_pipeline(self):
        """With no pipeline but all VALIDATED chunks, status becomes complete -> 100%."""
        manifest = {
            "chunks": {"c0": {"state": "VALIDATED", "items": 5, "valid": 5, "failed": 0}},
            "metadata": {},
            "pipeline": [],
        }
        summary = _build_summary(manifest)
        # All chunks VALIDATED -> status "complete" -> progress 100
        assert summary["progress"] == 100

    def test_progress_no_pipeline_non_validated(self):
        """With no pipeline and non-validated chunks, progress stays 0."""
        manifest = {
            "status": "running",
            "chunks": {"c0": {"state": "step1_DONE", "items": 5, "valid": 0, "failed": 0}},
            "metadata": {},
            "pipeline": [],
        }
        summary = _build_summary(manifest)
        # No pipeline to calculate progress from, status not "complete"
        assert summary["progress"] == 0

    def test_unit_counts(self, minimal_manifest):
        summary = _build_summary(minimal_manifest)
        assert summary["total_units"] == 20  # 10 + 10
        assert summary["valid_units"] == 9   # 9 + 0

    def test_failed_units_terminal_status(self):
        """For terminal runs, failed = total - valid."""
        manifest = {
            "status": "complete",
            "chunks": {
                "c0": {"state": "VALIDATED", "items": 10, "valid": 8, "failed": 2},
            },
            "metadata": {},
            "pipeline": ["step1"],
        }
        summary = _build_summary(manifest)
        assert summary["failed_units"] == 2  # max(0, 10 - 8)

    def test_failed_units_active_status(self):
        """For active runs, failed comes from chunk 'failed' field."""
        manifest = {
            "status": "running",
            "chunks": {
                "c0": {"state": "step1_DONE", "items": 10, "valid": 0, "failed": 3},
            },
            "metadata": {},
            "pipeline": ["step1", "step2"],
        }
        summary = _build_summary(manifest)
        assert summary["failed_units"] == 3

    def test_token_aggregation(self, minimal_manifest):
        summary = _build_summary(minimal_manifest)
        # 1000 + 500 + 100 + 50 = 1650
        assert summary["total_tokens"] == 1650

    def test_token_aggregation_with_none_values(self):
        """None token values should be treated as 0."""
        manifest = {
            "chunks": {},
            "metadata": {
                "initial_input_tokens": None,
                "initial_output_tokens": 500,
                "retry_input_tokens": None,
                "retry_output_tokens": None,
            },
            "pipeline": [],
        }
        summary = _build_summary(manifest)
        assert summary["total_tokens"] == 500

    def test_token_aggregation_missing_keys(self):
        """Missing token keys should default to 0."""
        manifest = {"chunks": {}, "metadata": {}, "pipeline": []}
        summary = _build_summary(manifest)
        assert summary["total_tokens"] == 0

    def test_current_step_validated(self):
        manifest = {
            "chunks": {
                "c0": {"state": "VALIDATED", "items": 5, "valid": 5, "failed": 0},
            },
            "metadata": {},
            "pipeline": ["generate", "validate"],
        }
        summary = _build_summary(manifest)
        assert summary["current_step"] == "validate"

    def test_current_step_in_progress(self):
        manifest = {
            "chunks": {
                "c0": {"state": "generate_DONE", "items": 5, "valid": 0, "failed": 0},
            },
            "metadata": {},
            "pipeline": ["generate", "validate"],
        }
        summary = _build_summary(manifest)
        assert summary["current_step"] == "generate"

    def test_current_step_no_pipeline(self):
        manifest = {
            "chunks": {"c0": {"state": "VALIDATED", "items": 5, "valid": 5, "failed": 0}},
            "metadata": {},
            "pipeline": [],
        }
        summary = _build_summary(manifest)
        assert summary["current_step"] == ""

    def test_current_step_no_chunks(self):
        manifest = {"chunks": {}, "metadata": {}, "pipeline": ["step1"]}
        summary = _build_summary(manifest)
        assert summary["current_step"] == ""

    def test_current_step_picks_most_advanced(self):
        manifest = {
            "chunks": {
                "c0": {"state": "generate_DONE", "items": 5, "valid": 0, "failed": 0},
                "c1": {"state": "validate_DONE", "items": 5, "valid": 0, "failed": 0},
            },
            "metadata": {},
            "pipeline": ["generate", "validate", "finalize"],
        }
        summary = _build_summary(manifest)
        assert summary["current_step"] == "validate"

    def test_error_message_present(self):
        manifest = {
            "status": "failed",
            "error_message": "Something went wrong",
            "chunks": {},
            "metadata": {},
            "pipeline": [],
        }
        summary = _build_summary(manifest)
        assert summary["error_message"] == "Something went wrong"

    def test_error_message_absent(self):
        manifest = {"chunks": {}, "metadata": {}, "pipeline": []}
        summary = _build_summary(manifest)
        assert summary["error_message"] is None

    def test_mode_defaults_to_batch(self):
        manifest = {"chunks": {}, "metadata": {}, "pipeline": []}
        summary = _build_summary(manifest)
        assert summary["mode"] == "batch"

    def test_mode_none_defaults_to_batch(self):
        manifest = {"chunks": {}, "metadata": {"mode": None}, "pipeline": []}
        summary = _build_summary(manifest)
        assert summary["mode"] == "batch"

    def test_started_from_start_time(self):
        manifest = {
            "chunks": {},
            "metadata": {"start_time": "2025-06-01T12:00:00Z"},
            "pipeline": [],
            "created": "2025-06-01T11:00:00Z",
        }
        summary = _build_summary(manifest)
        assert summary["started"] == "2025-06-01T12:00:00Z"

    def test_started_falls_back_to_created(self):
        manifest = {
            "chunks": {},
            "metadata": {},
            "pipeline": [],
            "created": "2025-06-01T11:00:00Z",
        }
        summary = _build_summary(manifest)
        assert summary["started"] == "2025-06-01T11:00:00Z"

    def test_updated_field(self):
        manifest = {
            "chunks": {},
            "metadata": {},
            "pipeline": [],
            "updated": "2025-06-01T13:00:00Z",
        }
        summary = _build_summary(manifest)
        assert summary["updated"] == "2025-06-01T13:00:00Z"

    def test_pending_status_with_chunks_no_validated_no_failed(self):
        """If no chunks are VALIDATED or FAILED and status is not 'running', stays pending."""
        manifest = {
            "status": "pending",
            "chunks": {
                "c0": {"state": "step1_DONE", "items": 5, "valid": 0, "failed": 0},
            },
            "metadata": {},
            "pipeline": ["step1"],
        }
        summary = _build_summary(manifest)
        assert summary["status"] == "pending"

    def test_chunk_state_not_in_pipeline(self):
        """A chunk state whose step name is not in the pipeline list."""
        manifest = {
            "chunks": {
                "c0": {"state": "unknownstep_DONE", "items": 5, "valid": 0, "failed": 0},
            },
            "metadata": {},
            "pipeline": ["generate", "validate"],
        }
        summary = _build_summary(manifest)
        assert summary["current_step"] == ""

    def test_chunk_with_failed_state_excluded_from_progress(self):
        """FAILED state should not contribute progress steps."""
        manifest = {
            "chunks": {
                "c0": {"state": "FAILED", "items": 5, "valid": 0, "failed": 5},
            },
            "metadata": {},
            "pipeline": ["step1"],
        }
        summary = _build_summary(manifest)
        assert summary["progress"] == 0

    def test_chunk_with_pending_state_excluded_from_progress(self):
        """PENDING state should not contribute progress steps."""
        manifest = {
            "chunks": {
                "c0": {"state": "PENDING", "items": 5, "valid": 0, "failed": 0},
            },
            "metadata": {},
            "pipeline": ["step1"],
        }
        summary = _build_summary(manifest)
        assert summary["progress"] == 0

    def test_failed_units_killed_status(self):
        """For killed runs, failed = total - valid."""
        manifest = {
            "status": "killed",
            "chunks": {
                "c0": {"state": "VALIDATED", "items": 10, "valid": 7, "failed": 3},
            },
            "metadata": {},
            "pipeline": ["step1"],
        }
        summary = _build_summary(manifest)
        assert summary["failed_units"] == 3  # max(0, 10 - 7)

    def test_failed_units_failed_status(self):
        """For failed runs, failed = total - valid."""
        manifest = {
            "status": "failed",
            "chunks": {
                "c0": {"state": "FAILED", "items": 10, "valid": 0, "failed": 10},
            },
            "metadata": {},
            "pipeline": ["step1"],
        }
        summary = _build_summary(manifest)
        assert summary["failed_units"] == 10  # max(0, 10 - 0)


# =============================================================================
# _compute_summary_cost
# =============================================================================

class TestComputeSummaryCost:

    def test_zero_tokens_returns_zero(self):
        result = _compute_summary_cost(0, 0, {})
        assert result == 0.0

    def test_uses_model_registry_pricing(self):
        metadata = {
            "provider": "gemini",
            "model": "gemini-2.0-flash-001",
            "mode": "batch",
        }
        # gemini-2.0-flash-001: input=0.075, output=0.3
        result = _compute_summary_cost(1_000_000, 1_000_000, metadata)
        expected = 0.075 + 0.3
        assert result == round(expected, 4)

    def test_falls_back_to_default_model(self):
        metadata = {
            "provider": "gemini",
            "model": "nonexistent-model",
            "mode": "batch",
        }
        # Should fall back to default_model: gemini-2.0-flash-001 (input=0.075, output=0.3)
        result = _compute_summary_cost(1_000_000, 1_000_000, metadata)
        expected = 0.075 + 0.3
        assert result == round(expected, 4)

    def test_realtime_mode_multiplier(self):
        metadata = {
            "provider": "gemini",
            "model": "gemini-2.0-flash-001",
            "mode": "realtime",
        }
        # gemini realtime_multiplier is 2.0
        # input: 0.075 * 2 = 0.15, output: 0.3 * 2 = 0.6
        result = _compute_summary_cost(1_000_000, 1_000_000, metadata)
        expected = 0.15 + 0.6
        assert result == round(expected, 4)

    def test_missing_provider_defaults_to_gemini(self):
        metadata = {"mode": "batch"}
        result = _compute_summary_cost(1_000_000, 1_000_000, metadata)
        # Falls back to gemini provider, default model
        expected = 0.075 + 0.3
        assert result == round(expected, 4)

    def test_unknown_provider_uses_defaults(self):
        metadata = {
            "provider": "totally_unknown_provider",
            "model": "some-model",
            "mode": "batch",
        }
        # Unknown provider: no provider_data, no model_data, uses defaults
        result = _compute_summary_cost(1_000_000, 1_000_000, metadata)
        # Default rates: input=0.075, output=0.30
        expected = 0.075 + 0.30
        assert result == round(expected, 4)

    def test_openai_model_pricing(self):
        metadata = {
            "provider": "openai",
            "model": "gpt-4o",
            "mode": "batch",
        }
        # gpt-4o: input=1.25, output=5.0
        result = _compute_summary_cost(1_000_000, 1_000_000, metadata)
        expected = 1.25 + 5.0
        assert result == round(expected, 4)

    def test_anthropic_model_pricing(self):
        metadata = {
            "provider": "anthropic",
            "model": "claude-sonnet-4-20250514",
            "mode": "batch",
        }
        # claude-sonnet-4: input=1.5, output=7.5
        result = _compute_summary_cost(1_000_000, 1_000_000, metadata)
        expected = 1.5 + 7.5
        assert result == round(expected, 4)

    def test_small_token_counts(self):
        metadata = {
            "provider": "gemini",
            "model": "gemini-2.0-flash-001",
            "mode": "batch",
        }
        # 1000 input tokens, 500 output tokens
        result = _compute_summary_cost(1000, 500, metadata)
        expected = (1000 / 1_000_000) * 0.075 + (500 / 1_000_000) * 0.3
        assert result == round(expected, 4)

    def test_only_input_tokens(self):
        metadata = {
            "provider": "gemini",
            "model": "gemini-2.0-flash-001",
            "mode": "batch",
        }
        result = _compute_summary_cost(1_000_000, 0, metadata)
        assert result == round(0.075, 4)

    def test_only_output_tokens(self):
        metadata = {
            "provider": "gemini",
            "model": "gemini-2.0-flash-001",
            "mode": "batch",
        }
        result = _compute_summary_cost(0, 1_000_000, metadata)
        # 0 input + nonzero output will proceed to calculation
        assert result == round(0.3, 4)

    def test_no_model_name_uses_default(self):
        metadata = {
            "provider": "gemini",
            "model": None,
            "mode": "batch",
        }
        # model is None, should fall back to default_model
        result = _compute_summary_cost(1_000_000, 1_000_000, metadata)
        expected = 0.075 + 0.3
        assert result == round(expected, 4)


# =============================================================================
# load_jsonl
# =============================================================================

class TestLoadJsonl:

    def test_load_plain_jsonl(self, tmp_path, sample_jsonl_records):
        file_path = tmp_path / "data.jsonl"
        with open(file_path, "w") as f:
            for record in sample_jsonl_records:
                f.write(json.dumps(record) + "\n")

        result = load_jsonl(file_path)
        assert result == sample_jsonl_records

    def test_load_gzipped_jsonl(self, tmp_path, sample_jsonl_records):
        file_path = tmp_path / "data.jsonl.gz"
        with gzip.open(file_path, "wt", encoding="utf-8") as f:
            for record in sample_jsonl_records:
                f.write(json.dumps(record) + "\n")

        result = load_jsonl(file_path)
        assert result == sample_jsonl_records

    def test_load_gz_fallback(self, tmp_path, sample_jsonl_records):
        """If plain file doesn't exist but .gz does, load the .gz."""
        gz_path = tmp_path / "data.jsonl.gz"
        with gzip.open(gz_path, "wt", encoding="utf-8") as f:
            for record in sample_jsonl_records:
                f.write(json.dumps(record) + "\n")

        # Request the non-.gz path
        result = load_jsonl(tmp_path / "data.jsonl")
        assert result == sample_jsonl_records

    def test_load_missing_file_returns_empty(self, tmp_path):
        result = load_jsonl(tmp_path / "nonexistent.jsonl")
        assert result == []

    def test_load_missing_file_no_gz_returns_empty(self, tmp_path):
        """Neither plain nor .gz exists."""
        result = load_jsonl(tmp_path / "missing.jsonl")
        assert result == []

    def test_load_with_invalid_json_lines(self, tmp_path):
        file_path = tmp_path / "mixed.jsonl"
        with open(file_path, "w") as f:
            f.write('{"valid": true}\n')
            f.write("this is not json\n")
            f.write('{"also_valid": 42}\n')
            f.write("\n")  # empty line
            f.write("{broken json\n")

        result = load_jsonl(file_path)
        assert len(result) == 2
        assert result[0] == {"valid": True}
        assert result[1] == {"also_valid": 42}

    def test_load_empty_file(self, tmp_path):
        file_path = tmp_path / "empty.jsonl"
        file_path.write_text("")

        result = load_jsonl(file_path)
        assert result == []

    def test_load_file_with_only_whitespace_lines(self, tmp_path):
        file_path = tmp_path / "whitespace.jsonl"
        file_path.write_text("   \n\n  \n")

        result = load_jsonl(file_path)
        assert result == []

    def test_load_accepts_string_path(self, tmp_path, sample_jsonl_records):
        """load_jsonl should accept string paths too."""
        file_path = tmp_path / "data.jsonl"
        with open(file_path, "w") as f:
            for record in sample_jsonl_records:
                f.write(json.dumps(record) + "\n")

        result = load_jsonl(str(file_path))
        assert result == sample_jsonl_records


# =============================================================================
# load_jsonl_by_id
# =============================================================================

class TestLoadJsonlById:

    def test_index_by_default_field(self, tmp_path, sample_jsonl_records):
        file_path = tmp_path / "data.jsonl"
        with open(file_path, "w") as f:
            for record in sample_jsonl_records:
                f.write(json.dumps(record) + "\n")

        result = load_jsonl_by_id(file_path)
        assert "u1" in result
        assert "u2" in result
        assert "u3" in result
        assert result["u1"]["name"] == "Alice"

    def test_index_by_custom_field(self, tmp_path, sample_jsonl_records):
        file_path = tmp_path / "data.jsonl"
        with open(file_path, "w") as f:
            for record in sample_jsonl_records:
                f.write(json.dumps(record) + "\n")

        result = load_jsonl_by_id(file_path, id_field="name")
        assert "Alice" in result
        assert "Bob" in result
        assert result["Alice"]["unit_id"] == "u1"

    def test_records_without_id_field_skipped(self, tmp_path):
        file_path = tmp_path / "data.jsonl"
        with open(file_path, "w") as f:
            f.write('{"unit_id": "u1", "val": 1}\n')
            f.write('{"val": 2}\n')  # No unit_id
            f.write('{"unit_id": "u3", "val": 3}\n')

        result = load_jsonl_by_id(file_path)
        assert len(result) == 2
        assert "u1" in result
        assert "u3" in result

    def test_missing_file_returns_empty(self, tmp_path):
        result = load_jsonl_by_id(tmp_path / "nonexistent.jsonl")
        assert result == {}


# =============================================================================
# append_jsonl
# =============================================================================

class TestAppendJsonl:

    def test_append_to_new_file(self, tmp_path):
        file_path = tmp_path / "output.jsonl"
        record = {"id": "test", "value": 42}

        append_jsonl(file_path, record)

        lines = file_path.read_text().strip().split("\n")
        assert len(lines) == 1
        assert json.loads(lines[0]) == record

    def test_append_to_existing_file(self, tmp_path):
        file_path = tmp_path / "output.jsonl"
        file_path.write_text('{"existing": true}\n')

        append_jsonl(file_path, {"new": True})

        lines = file_path.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"existing": True}
        assert json.loads(lines[1]) == {"new": True}

    def test_append_multiple_records(self, tmp_path):
        file_path = tmp_path / "output.jsonl"

        for i in range(5):
            append_jsonl(file_path, {"index": i})

        lines = file_path.read_text().strip().split("\n")
        assert len(lines) == 5
        for i, line in enumerate(lines):
            assert json.loads(line) == {"index": i}


# =============================================================================
# write_jsonl
# =============================================================================

class TestWriteJsonl:

    def test_write_records(self, tmp_path, sample_jsonl_records):
        file_path = tmp_path / "output.jsonl"
        write_jsonl(file_path, sample_jsonl_records)

        lines = file_path.read_text().strip().split("\n")
        assert len(lines) == 3
        for i, line in enumerate(lines):
            assert json.loads(line) == sample_jsonl_records[i]

    def test_write_creates_parent_dirs(self, tmp_path):
        file_path = tmp_path / "deep" / "nested" / "dir" / "output.jsonl"
        records = [{"id": 1}]
        write_jsonl(file_path, records)

        assert file_path.exists()
        assert json.loads(file_path.read_text().strip()) == {"id": 1}

    def test_write_overwrites_existing(self, tmp_path):
        file_path = tmp_path / "output.jsonl"
        file_path.write_text('{"old": true}\n')

        write_jsonl(file_path, [{"new": True}])

        lines = file_path.read_text().strip().split("\n")
        assert len(lines) == 1
        assert json.loads(lines[0]) == {"new": True}

    def test_write_empty_list(self, tmp_path):
        file_path = tmp_path / "empty.jsonl"
        write_jsonl(file_path, [])

        assert file_path.exists()
        assert file_path.read_text() == ""


# =============================================================================
# log_error
# =============================================================================

class TestLogError:

    def test_log_error_message_only(self, capsys):
        log_error("something failed")

        captured = capsys.readouterr()
        error_output = json.loads(captured.err)
        assert error_output["error"] == "something failed"
        assert "context" not in error_output

    def test_log_error_with_context(self, capsys):
        log_error("file not found", context={"path": "/tmp/missing.txt", "code": 404})

        captured = capsys.readouterr()
        error_output = json.loads(captured.err)
        assert error_output["error"] == "file not found"
        assert error_output["context"]["path"] == "/tmp/missing.txt"
        assert error_output["context"]["code"] == 404

    def test_log_error_with_none_context(self, capsys):
        log_error("no context", context=None)

        captured = capsys.readouterr()
        error_output = json.loads(captured.err)
        assert error_output["error"] == "no context"
        assert "context" not in error_output

    def test_log_error_with_empty_context(self, capsys):
        log_error("empty context", context={})

        captured = capsys.readouterr()
        error_output = json.loads(captured.err)
        # Empty dict is falsy, so context should not be added
        assert "context" not in error_output


# =============================================================================
# log_message
# =============================================================================

class TestLogMessage:

    def test_log_message_writes_to_file(self, tmp_path):
        log_file = tmp_path / "run.log"
        log_message(log_file, "INFO", "starting process", echo_stderr=False)

        content = log_file.read_text()
        assert "[INFO]" in content
        assert "starting process" in content
        # Should have UTC timestamp in ISO format
        assert "T" in content
        assert "Z" in content

    def test_log_message_appends(self, tmp_path):
        log_file = tmp_path / "run.log"
        log_message(log_file, "INFO", "first message", echo_stderr=False)
        log_message(log_file, "ERROR", "second message", echo_stderr=False)

        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 2
        assert "first message" in lines[0]
        assert "second message" in lines[1]

    def test_log_message_echo_stderr(self, tmp_path, capsys):
        log_file = tmp_path / "run.log"
        log_message(log_file, "POLL", "checking status", echo_stderr=True)

        captured = capsys.readouterr()
        assert "[POLL]" in captured.err
        assert "checking status" in captured.err

    def test_log_message_no_echo_stderr(self, tmp_path, capsys):
        log_file = tmp_path / "run.log"
        log_message(log_file, "TICK", "silent message", echo_stderr=False)

        captured = capsys.readouterr()
        assert captured.err == ""

    def test_log_message_various_levels(self, tmp_path):
        log_file = tmp_path / "run.log"
        for level in ["POLL", "COLLECT", "SUBMIT", "VALIDATE", "TICK", "ERROR"]:
            log_message(log_file, level, f"msg-{level}", echo_stderr=False)

        content = log_file.read_text()
        for level in ["POLL", "COLLECT", "SUBMIT", "VALIDATE", "TICK", "ERROR"]:
            assert f"[{level}]" in content
            assert f"msg-{level}" in content


# =============================================================================
# trace_log
# =============================================================================

class TestTraceLog:

    def test_trace_log_writes_to_file(self, tmp_path):
        trace_log(tmp_path, "[API] gemini chunk_003 unit_042 | 1.33s | 200")

        trace_file = tmp_path / "TRACE_LOG.txt"
        assert trace_file.exists()

        content = trace_file.read_text()
        assert "[API] gemini chunk_003 unit_042 | 1.33s | 200" in content

    def test_trace_log_appends_multiple(self, tmp_path):
        trace_log(tmp_path, "message 1")
        trace_log(tmp_path, "message 2")
        trace_log(tmp_path, "message 3")

        content = (tmp_path / "TRACE_LOG.txt").read_text()
        lines = content.strip().split("\n")
        assert len(lines) == 3

    def test_trace_log_has_timestamp(self, tmp_path):
        trace_log(tmp_path, "test message")

        content = (tmp_path / "TRACE_LOG.txt").read_text()
        # Timestamp format: YYYY-MM-DDTHH:MM:SS.mmm
        import re
        assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}", content)

    def test_trace_log_best_effort_no_crash(self, tmp_path, monkeypatch):
        """Write failures are swallowed and no trace file is created."""
        calls = {"count": 0}

        def fail_open(*args, **kwargs):
            calls["count"] += 1
            raise OSError("disk full")

        monkeypatch.setattr("builtins.open", fail_open)
        trace_log(tmp_path, "should not crash")
        assert calls["count"] == 1
        assert not (tmp_path / "TRACE_LOG.txt").exists()

    def test_trace_log_creates_file(self, tmp_path):
        trace_file = tmp_path / "TRACE_LOG.txt"
        assert not trace_file.exists()

        trace_log(tmp_path, "first entry")
        assert trace_file.exists()


# =============================================================================
# format_elapsed_time
# =============================================================================

class TestFormatElapsedTime:

    def test_zero_seconds(self):
        assert format_elapsed_time(0) == "0s"

    def test_seconds_only(self):
        assert format_elapsed_time(45) == "45s"

    def test_one_second(self):
        assert format_elapsed_time(1) == "1s"

    def test_59_seconds(self):
        assert format_elapsed_time(59) == "59s"

    def test_exactly_one_minute(self):
        assert format_elapsed_time(60) == "1m 0s"

    def test_minutes_and_seconds(self):
        assert format_elapsed_time(135) == "2m 15s"

    def test_59_minutes_59_seconds(self):
        assert format_elapsed_time(3599) == "59m 59s"

    def test_exactly_one_hour(self):
        assert format_elapsed_time(3600) == "1h 0m 0s"

    def test_hours_minutes_seconds(self):
        assert format_elapsed_time(3600 + 15 * 60 + 30) == "1h 15m 30s"

    def test_large_value(self):
        assert format_elapsed_time(86400) == "24h 0m 0s"

    def test_multi_hour(self):
        assert format_elapsed_time(2 * 3600 + 30 * 60 + 45) == "2h 30m 45s"


# =============================================================================
# compute_cost
# =============================================================================

class TestComputeCost:

    def test_no_pricing_returns_none(self):
        assert compute_cost(1000, 500, None) is None

    def test_empty_pricing_returns_none(self):
        assert compute_cost(1000, 500, {}) is None

    def test_basic_cost_calculation(self):
        pricing = {
            "input_per_million_tokens": 1.0,
            "output_per_million_tokens": 2.0,
        }
        result = compute_cost(1_000_000, 1_000_000, pricing)
        assert result == 3.0

    def test_zero_tokens(self):
        pricing = {
            "input_per_million_tokens": 1.0,
            "output_per_million_tokens": 2.0,
        }
        result = compute_cost(0, 0, pricing)
        assert result == 0.0

    def test_small_token_count(self):
        pricing = {
            "input_per_million_tokens": 1.0,
            "output_per_million_tokens": 2.0,
        }
        result = compute_cost(1000, 500, pricing)
        expected = (1000 / 1_000_000) * 1.0 + (500 / 1_000_000) * 2.0
        assert result == round(expected, 6)

    def test_missing_pricing_keys_default_to_zero(self):
        pricing = {"some_other_key": 5.0}
        result = compute_cost(1_000_000, 1_000_000, pricing)
        assert result == 0.0

    def test_cost_precision(self):
        pricing = {
            "input_per_million_tokens": 0.075,
            "output_per_million_tokens": 0.3,
        }
        result = compute_cost(1_000_000, 1_000_000, pricing)
        assert result == 0.375


# =============================================================================
# create_interpreter
# =============================================================================

class TestCreateInterpreter:

    def test_returns_interpreter(self):
        aeval = create_interpreter()
        assert aeval is not None

    def test_has_safe_builtins(self):
        aeval = create_interpreter()
        expected_builtins = [
            "sum", "len", "min", "max", "abs", "round",
            "all", "any", "sorted", "list", "dict", "set",
            "str", "int", "float", "bool", "isinstance",
            "enumerate", "zip", "range",
        ]
        for name in expected_builtins:
            assert name in aeval.symtable, f"Missing builtin: {name}"

    def test_evaluate_simple_expression(self):
        aeval = create_interpreter()
        result = aeval("2 + 3")
        assert result == 5

    def test_evaluate_with_builtins(self):
        aeval = create_interpreter()
        assert aeval("len([1, 2, 3])") == 3
        assert aeval("sum([1, 2, 3])") == 6
        assert aeval("min(5, 3, 8)") == 3
        assert aeval("max(5, 3, 8)") == 8
        assert aeval("abs(-7)") == 7

    def test_evaluate_list_comprehension(self):
        aeval = create_interpreter()
        result = aeval("[x * 2 for x in range(3)]")
        assert result == [0, 2, 4]

    def test_evaluate_with_isinstance(self):
        aeval = create_interpreter()
        assert aeval('isinstance("hello", str)') is True
        assert aeval("isinstance(42, int)") is True

    def test_errors_captured_not_stderr(self, capsys):
        aeval = create_interpreter()
        # Evaluate something that causes an error
        result = aeval("undefined_variable")
        captured = capsys.readouterr()
        # asteval should capture errors internally, not print to stderr
        assert captured.err == ""


# =============================================================================
# parse_json_response
# =============================================================================

class TestParseJsonResponse:

    def test_plain_json(self):
        result = parse_json_response('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_in_markdown_code_block(self):
        text = '```json\n{"key": "value"}\n```'
        result = parse_json_response(text)
        assert result == {"key": "value"}

    def test_json_in_generic_code_block(self):
        text = '```\n{"key": "value"}\n```'
        result = parse_json_response(text)
        assert result == {"key": "value"}

    def test_plus_prefix_in_object_value(self):
        text = '{"score": +4, "rating": +10}'
        result = parse_json_response(text)
        assert result == {"score": 4, "rating": 10}

    def test_plus_prefix_in_array_start(self):
        text = '[+4, 5, 6]'
        result = parse_json_response(text)
        assert result == [4, 5, 6]

    def test_plus_prefix_in_array_continuation(self):
        text = '[1, +2, +3]'
        result = parse_json_response(text)
        assert result == [1, 2, 3]

    def test_trailing_comma_in_object(self):
        text = '{"a": 1, "b": 2,}'
        result = parse_json_response(text)
        assert result == {"a": 1, "b": 2}

    def test_trailing_comma_in_array(self):
        text = '[1, 2, 3,]'
        result = parse_json_response(text)
        assert result == [1, 2, 3]

    def test_empty_string_returns_none(self):
        assert parse_json_response("") is None

    def test_none_returns_none(self):
        assert parse_json_response(None) is None

    def test_invalid_json_returns_none(self):
        assert parse_json_response("this is not json at all") is None

    def test_whitespace_only_returns_none(self):
        assert parse_json_response("   ") is None

    def test_json_with_surrounding_text(self):
        # If there's no code block and the text isn't valid JSON, returns None
        text = 'Here is the result: {"key": "value"} hope that helps!'
        result = parse_json_response(text)
        assert result is None

    def test_markdown_block_with_surrounding_text(self):
        text = 'Here is the JSON:\n```json\n{"key": "value"}\n```\nHope that helps!'
        result = parse_json_response(text)
        assert result == {"key": "value"}

    def test_nested_json(self):
        text = '{"outer": {"inner": [1, 2, 3]}, "flag": true}'
        result = parse_json_response(text)
        assert result["outer"]["inner"] == [1, 2, 3]
        assert result["flag"] is True

    def test_combined_plus_and_trailing_comma(self):
        text = '{"a": +1, "b": +2,}'
        result = parse_json_response(text)
        assert result == {"a": 1, "b": 2}

    def test_json_array_response(self):
        text = '[{"id": 1}, {"id": 2}]'
        result = parse_json_response(text)
        assert len(result) == 2
        assert result[0]["id"] == 1

    def test_markdown_block_with_multiline_json(self):
        text = '```json\n{\n  "name": "test",\n  "value": 42\n}\n```'
        result = parse_json_response(text)
        assert result == {"name": "test", "value": 42}

    def test_plus_prefix_not_removed_from_string_values(self):
        """Plus signs inside string values should not be affected."""
        text = '{"phone": "+1-555-0123"}'
        result = parse_json_response(text)
        assert result == {"phone": "+1-555-0123"}

    def test_multiple_trailing_commas(self):
        text = '{"a": 1, "b": [1, 2,],}'
        result = parse_json_response(text)
        assert result == {"a": 1, "b": [1, 2]}
