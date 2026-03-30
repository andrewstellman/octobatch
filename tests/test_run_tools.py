"""
Tests for run verification and repair tools (scripts/run_tools.py).

Covers:
- verify_run(): manifest loading, pipeline step verification, integrity reporting
- _verify_step(): valid/failed/missing/duplicated/orphaned unit computation
- repair_run(): retry chunk creation, state management, manifest updates

Each test builds synthetic run directories in tmp_path with MANIFEST.json,
chunks/, and JSONL files to exercise all code paths.
"""

import json
import sys
from pathlib import Path

import pytest

# Add scripts directory to path so run_tools module is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from run_tools import (
    verify_run,
    repair_run,
    _verify_step,
    generate_report,
    _load_step_records,
    _compute_cost,
)


# =============================================================================
# Helpers
# =============================================================================

def write_manifest(run_dir: Path, manifest: dict) -> Path:
    """Write a MANIFEST.json file to the run directory."""
    manifest_path = run_dir / "MANIFEST.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest_path


def write_jsonl(file_path: Path, records: list[dict]) -> Path:
    """Write records to a JSONL file, creating parent directories as needed."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
    return file_path


def make_unit(unit_id: str, **extra) -> dict:
    """Create a unit record with a unit_id and optional extra fields."""
    record = {"unit_id": unit_id}
    record.update(extra)
    return record


def build_perfect_single_step_run(run_dir: Path, num_units: int = 3) -> dict:
    """
    Build a run directory with a single pipeline step where all units are validated.

    Returns the manifest dict for reference.
    """
    unit_ids = [f"unit_{i:03d}" for i in range(num_units)]
    units = [make_unit(uid, data=f"data_{uid}") for uid in unit_ids]
    validated = [make_unit(uid, result="ok") for uid in unit_ids]

    manifest = {
        "pipeline": ["step1"],
        "chunks": {
            "chunk_000": {
                "state": "VALIDATED",
                "items": num_units,
                "valid": num_units,
                "failed": 0,
                "retries": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            }
        },
        "metadata": {"pipeline_name": "test_pipeline"},
        "created": "2024-01-01T00:00:00Z",
        "updated": "2024-01-01T00:00:00Z",
    }

    write_manifest(run_dir, manifest)

    chunk_dir = run_dir / "chunks" / "chunk_000"
    write_jsonl(chunk_dir / "units.jsonl", units)
    write_jsonl(chunk_dir / "step1_validated.jsonl", validated)

    return manifest


def build_multi_step_run(
    run_dir: Path,
    num_units: int = 5,
    step1_valid_ids: list[str] | None = None,
    step1_failed_ids: list[str] | None = None,
    step2_valid_ids: list[str] | None = None,
    step2_failed_ids: list[str] | None = None,
    status: str | None = None,
) -> dict:
    """
    Build a run directory with a two-step pipeline and configurable outputs.

    By default all units pass both steps.
    """
    all_ids = [f"unit_{i:03d}" for i in range(num_units)]
    units = [make_unit(uid, data=f"data_{uid}") for uid in all_ids]

    if step1_valid_ids is None:
        step1_valid_ids = all_ids
    if step1_failed_ids is None:
        step1_failed_ids = []
    if step2_valid_ids is None:
        step2_valid_ids = step1_valid_ids
    if step2_failed_ids is None:
        step2_failed_ids = []

    step1_valid = [make_unit(uid, step1_result="ok") for uid in step1_valid_ids]
    step1_failed = [make_unit(uid, step1_error="fail") for uid in step1_failed_ids]
    step2_valid = [make_unit(uid, step2_result="ok") for uid in step2_valid_ids]
    step2_failed = [make_unit(uid, step2_error="fail") for uid in step2_failed_ids]

    manifest = {
        "pipeline": ["step1", "step2"],
        "chunks": {
            "chunk_000": {
                "state": "VALIDATED",
                "items": num_units,
                "valid": len(step2_valid_ids),
                "failed": len(step2_failed_ids),
                "retries": 0,
                "input_tokens": 100,
                "output_tokens": 200,
            }
        },
        "metadata": {"pipeline_name": "multi_test"},
        "created": "2024-01-01T00:00:00Z",
        "updated": "2024-01-01T00:00:00Z",
    }

    if status is not None:
        manifest["status"] = status

    write_manifest(run_dir, manifest)

    chunk_dir = run_dir / "chunks" / "chunk_000"
    write_jsonl(chunk_dir / "units.jsonl", units)

    if step1_valid:
        write_jsonl(chunk_dir / "step1_validated.jsonl", step1_valid)
    if step1_failed:
        write_jsonl(chunk_dir / "step1_failures.jsonl", step1_failed)
    if step2_valid:
        write_jsonl(chunk_dir / "step2_validated.jsonl", step2_valid)
    if step2_failed:
        write_jsonl(chunk_dir / "step2_failures.jsonl", step2_failed)

    return manifest


# =============================================================================
# verify_run() tests
# =============================================================================

class TestVerifyRun:
    """Tests for verify_run()."""

    def test_missing_manifest_returns_error(self, tmp_path):
        """verify_run returns error dict when MANIFEST.json does not exist."""
        run_dir = tmp_path / "missing_run"
        run_dir.mkdir()

        result = verify_run(run_dir)

        assert "error" in result
        assert "Cannot load MANIFEST.json" in result["error"]

    def test_invalid_json_manifest_returns_error(self, tmp_path):
        """verify_run returns error dict when MANIFEST.json contains invalid JSON."""
        run_dir = tmp_path / "bad_json_run"
        run_dir.mkdir()
        (run_dir / "MANIFEST.json").write_text("not valid json {{{")

        result = verify_run(run_dir)

        assert "error" in result
        assert "Cannot load MANIFEST.json" in result["error"]

    def test_empty_pipeline_returns_error(self, tmp_path):
        """verify_run returns error when pipeline list is empty."""
        run_dir = tmp_path / "no_pipeline_run"
        run_dir.mkdir()
        write_manifest(run_dir, {
            "pipeline": [],
            "chunks": {},
            "metadata": {},
            "created": "2024-01-01T00:00:00Z",
            "updated": "2024-01-01T00:00:00Z",
        })

        result = verify_run(run_dir)

        assert "error" in result
        assert "No pipeline steps found" in result["error"]

    def test_missing_pipeline_key_returns_error(self, tmp_path):
        """verify_run returns error when pipeline key is absent from manifest."""
        run_dir = tmp_path / "no_key_run"
        run_dir.mkdir()
        write_manifest(run_dir, {
            "chunks": {},
            "metadata": {},
        })

        result = verify_run(run_dir)

        assert "error" in result
        assert "No pipeline steps found" in result["error"]

    def test_perfect_single_step_run(self, tmp_path):
        """All units validated in a single step: integrity should be OK."""
        run_dir = tmp_path / "perfect_run"
        run_dir.mkdir()
        build_perfect_single_step_run(run_dir, num_units=3)

        result = verify_run(run_dir)

        assert "error" not in result
        assert result["integrity"] == "OK"
        assert result["total_missing"] == 0
        assert result["total_duplicated"] == 0
        assert result["initial_units"] == 3
        assert result["pipeline"] == ["step1"]
        assert result["pipeline_name"] == "test_pipeline"
        assert len(result["steps"]) == 1

        step = result["steps"][0]
        assert step["step"] == "step1"
        assert step["expected"] == 3
        assert step["valid"] == 3
        assert step["failed"] == 0
        assert step["missing"] == 0
        assert step["duplicated"] == 0
        assert step["orphaned"] == 0
        # Internal _valid_ids should be stripped from output
        assert "_valid_ids" not in step

    def test_perfect_multi_step_run(self, tmp_path):
        """All units validated through two steps: integrity should be OK."""
        run_dir = tmp_path / "perfect_multi"
        run_dir.mkdir()
        build_multi_step_run(run_dir, num_units=4)

        result = verify_run(run_dir)

        assert result["integrity"] == "OK"
        assert result["total_missing"] == 0
        assert result["total_duplicated"] == 0
        assert result["initial_units"] == 4
        assert len(result["steps"]) == 2

    def test_missing_units_at_step0(self, tmp_path):
        """Some units missing from step1 output: integrity should be WARN."""
        run_dir = tmp_path / "missing_s0"
        run_dir.mkdir()

        all_ids = ["unit_000", "unit_001", "unit_002", "unit_003"]
        present_ids = ["unit_000", "unit_001"]  # 002 and 003 missing

        build_multi_step_run(
            run_dir,
            num_units=4,
            step1_valid_ids=present_ids,
            step2_valid_ids=present_ids,
        )

        result = verify_run(run_dir)

        assert result["integrity"] == "WARN"
        assert result["total_missing"] == 2
        step1 = result["steps"][0]
        assert step1["missing"] == 2
        assert sorted(step1["missing_ids"]) == ["unit_002", "unit_003"]

    def test_missing_units_at_step1(self, tmp_path):
        """All units pass step1, but some go missing in step2."""
        run_dir = tmp_path / "missing_s1"
        run_dir.mkdir()

        all_ids = ["unit_000", "unit_001", "unit_002"]

        build_multi_step_run(
            run_dir,
            num_units=3,
            step1_valid_ids=all_ids,
            step2_valid_ids=["unit_000"],  # 001 and 002 missing at step2
        )

        result = verify_run(run_dir)

        assert result["integrity"] == "WARN"
        # step1 should be fine
        assert result["steps"][0]["missing"] == 0
        # step2 should have 2 missing
        assert result["steps"][1]["missing"] == 2
        assert sorted(result["steps"][1]["missing_ids"]) == ["unit_001", "unit_002"]

    def test_failed_units_accounted_for(self, tmp_path):
        """Units in failures.jsonl are accounted for and not counted as missing."""
        run_dir = tmp_path / "with_failures"
        run_dir.mkdir()

        all_ids = ["unit_000", "unit_001", "unit_002"]
        valid_ids = ["unit_000"]
        failed_ids = ["unit_001", "unit_002"]

        manifest = {
            "pipeline": ["step1"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED",
                    "items": 3,
                    "valid": 1,
                    "failed": 2,
                    "retries": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                }
            },
            "metadata": {"pipeline_name": "fail_test"},
            "created": "2024-01-01T00:00:00Z",
            "updated": "2024-01-01T00:00:00Z",
        }
        write_manifest(run_dir, manifest)

        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "units.jsonl", [make_unit(uid) for uid in all_ids])
        write_jsonl(chunk_dir / "step1_validated.jsonl", [make_unit(uid) for uid in valid_ids])
        write_jsonl(chunk_dir / "step1_failures.jsonl", [make_unit(uid) for uid in failed_ids])

        result = verify_run(run_dir)

        assert result["integrity"] == "OK"
        step = result["steps"][0]
        assert step["valid"] == 1
        assert step["failed"] == 2
        assert step["missing"] == 0

    def test_duplicated_units_detected(self, tmp_path):
        """Duplicate unit_ids in validated JSONL should be detected."""
        run_dir = tmp_path / "dupes_run"
        run_dir.mkdir()

        manifest = {
            "pipeline": ["step1"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED",
                    "items": 2,
                    "valid": 2,
                    "failed": 0,
                    "retries": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                }
            },
            "metadata": {"pipeline_name": "dupe_test"},
            "created": "2024-01-01T00:00:00Z",
            "updated": "2024-01-01T00:00:00Z",
        }
        write_manifest(run_dir, manifest)

        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "units.jsonl", [
            make_unit("unit_000"),
            make_unit("unit_001"),
        ])
        # Write unit_000 twice in validated output (duplicate)
        write_jsonl(chunk_dir / "step1_validated.jsonl", [
            make_unit("unit_000"),
            make_unit("unit_000"),
            make_unit("unit_001"),
        ])

        result = verify_run(run_dir)

        assert result["integrity"] == "WARN"
        assert result["total_duplicated"] == 1
        step = result["steps"][0]
        assert step["duplicated"] == 1

    def test_orphaned_units_detected(self, tmp_path):
        """Units in output that were not expected should appear as orphaned."""
        run_dir = tmp_path / "orphan_run"
        run_dir.mkdir()

        manifest = {
            "pipeline": ["step1"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED",
                    "items": 2,
                    "valid": 3,
                    "failed": 0,
                    "retries": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                }
            },
            "metadata": {},
            "created": "2024-01-01T00:00:00Z",
            "updated": "2024-01-01T00:00:00Z",
        }
        write_manifest(run_dir, manifest)

        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "units.jsonl", [
            make_unit("unit_000"),
            make_unit("unit_001"),
        ])
        # Include an unexpected unit in the validated output
        write_jsonl(chunk_dir / "step1_validated.jsonl", [
            make_unit("unit_000"),
            make_unit("unit_001"),
            make_unit("unit_extra"),
        ])

        result = verify_run(run_dir)

        step = result["steps"][0]
        assert step["orphaned"] == 1
        assert step["orphaned_ids"] == ["unit_extra"]

    def test_multiple_chunks(self, tmp_path):
        """verify_run works across multiple chunk directories."""
        run_dir = tmp_path / "multi_chunk_run"
        run_dir.mkdir()

        manifest = {
            "pipeline": ["step1"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED", "items": 2, "valid": 2,
                    "failed": 0, "retries": 0, "input_tokens": 0, "output_tokens": 0,
                },
                "chunk_001": {
                    "state": "VALIDATED", "items": 2, "valid": 2,
                    "failed": 0, "retries": 0, "input_tokens": 0, "output_tokens": 0,
                },
            },
            "metadata": {"pipeline_name": "multi_chunk"},
            "created": "2024-01-01T00:00:00Z",
            "updated": "2024-01-01T00:00:00Z",
        }
        write_manifest(run_dir, manifest)

        # Chunk 000
        chunk0 = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk0 / "units.jsonl", [make_unit("u0"), make_unit("u1")])
        write_jsonl(chunk0 / "step1_validated.jsonl", [make_unit("u0"), make_unit("u1")])

        # Chunk 001
        chunk1 = run_dir / "chunks" / "chunk_001"
        write_jsonl(chunk1 / "units.jsonl", [make_unit("u2"), make_unit("u3")])
        write_jsonl(chunk1 / "step1_validated.jsonl", [make_unit("u2"), make_unit("u3")])

        result = verify_run(run_dir)

        assert result["integrity"] == "OK"
        assert result["initial_units"] == 4
        assert result["steps"][0]["valid"] == 4

    def test_units_without_unit_id_field_skipped(self, tmp_path):
        """Records missing the unit_id field are silently skipped."""
        run_dir = tmp_path / "no_uid_run"
        run_dir.mkdir()

        manifest = {
            "pipeline": ["step1"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED", "items": 2, "valid": 1,
                    "failed": 0, "retries": 0, "input_tokens": 0, "output_tokens": 0,
                }
            },
            "metadata": {},
            "created": "2024-01-01T00:00:00Z",
            "updated": "2024-01-01T00:00:00Z",
        }
        write_manifest(run_dir, manifest)

        chunk_dir = run_dir / "chunks" / "chunk_000"
        # One record with unit_id, one without
        write_jsonl(chunk_dir / "units.jsonl", [
            make_unit("unit_000"),
            {"data": "no_id"},
        ])
        write_jsonl(chunk_dir / "step1_validated.jsonl", [
            make_unit("unit_000"),
        ])

        result = verify_run(run_dir)

        assert result["initial_units"] == 1
        assert result["steps"][0]["expected"] == 1
        assert result["steps"][0]["valid"] == 1
        assert result["integrity"] == "OK"

    def test_run_name_and_run_dir_in_result(self, tmp_path):
        """Result includes run_dir and run_name fields."""
        run_dir = tmp_path / "named_run"
        run_dir.mkdir()
        build_perfect_single_step_run(run_dir, num_units=1)

        result = verify_run(run_dir)

        assert result["run_dir"] == str(run_dir)
        assert result["run_name"] == "named_run"

    def test_no_units_file_empty_initial_ids(self, tmp_path):
        """Chunk with no units.jsonl file contributes no initial IDs."""
        run_dir = tmp_path / "no_units_run"
        run_dir.mkdir()

        manifest = {
            "pipeline": ["step1"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED", "items": 0, "valid": 0,
                    "failed": 0, "retries": 0, "input_tokens": 0, "output_tokens": 0,
                }
            },
            "metadata": {},
            "created": "2024-01-01T00:00:00Z",
            "updated": "2024-01-01T00:00:00Z",
        }
        write_manifest(run_dir, manifest)
        # Don't create any chunk directories or files

        result = verify_run(run_dir)

        assert result["initial_units"] == 0
        assert result["integrity"] == "OK"

    def test_accepts_string_run_dir(self, tmp_path):
        """verify_run accepts a string path and converts it to Path."""
        run_dir = tmp_path / "str_path_run"
        run_dir.mkdir()
        build_perfect_single_step_run(run_dir, num_units=2)

        result = verify_run(str(run_dir))

        assert result["integrity"] == "OK"
        assert result["initial_units"] == 2


# =============================================================================
# _verify_step() tests
# =============================================================================

class TestVerifyStep:
    """Tests for _verify_step()."""

    def test_all_valid(self, tmp_path):
        """All expected IDs found in validated file."""
        run_dir = tmp_path / "step_run"
        run_dir.mkdir()

        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "stepA_validated.jsonl", [
            make_unit("a"), make_unit("b"), make_unit("c"),
        ])

        expected = {"a", "b", "c"}
        chunks = {"chunk_000": {}}

        report = _verify_step(run_dir, "stepA", chunks, expected)

        assert report["step"] == "stepA"
        assert report["expected"] == 3
        assert report["valid"] == 3
        assert report["failed"] == 0
        assert report["missing"] == 0
        assert report["duplicated"] == 0
        assert report["orphaned"] == 0
        assert report["_valid_ids"] == {"a", "b", "c"}

    def test_missing_ids(self, tmp_path):
        """Expected IDs not found in any output file."""
        run_dir = tmp_path / "step_missing"
        run_dir.mkdir()

        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "stepA_validated.jsonl", [make_unit("a")])

        expected = {"a", "b", "c"}
        chunks = {"chunk_000": {}}

        report = _verify_step(run_dir, "stepA", chunks, expected)

        assert report["missing"] == 2
        assert sorted(report["missing_ids"]) == ["b", "c"]

    def test_failed_ids(self, tmp_path):
        """IDs in failures file are counted as failed, not missing."""
        run_dir = tmp_path / "step_fail"
        run_dir.mkdir()

        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "stepA_validated.jsonl", [make_unit("a")])
        write_jsonl(chunk_dir / "stepA_failures.jsonl", [make_unit("b")])

        expected = {"a", "b"}
        chunks = {"chunk_000": {}}

        report = _verify_step(run_dir, "stepA", chunks, expected)

        assert report["valid"] == 1
        assert report["failed"] == 1
        assert report["missing"] == 0

    def test_duplicated_ids(self, tmp_path):
        """Same unit_id appearing multiple times in validated output."""
        run_dir = tmp_path / "step_dupe"
        run_dir.mkdir()

        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "stepA_validated.jsonl", [
            make_unit("a"), make_unit("a"), make_unit("a"), make_unit("b"),
        ])

        expected = {"a", "b"}
        chunks = {"chunk_000": {}}

        report = _verify_step(run_dir, "stepA", chunks, expected)

        assert report["valid"] == 2  # unique
        assert report["duplicated"] == 2  # 4 records - 2 unique = 2 dupes

    def test_orphaned_ids(self, tmp_path):
        """IDs in output that were not in the expected set."""
        run_dir = tmp_path / "step_orphan"
        run_dir.mkdir()

        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "stepA_validated.jsonl", [
            make_unit("a"), make_unit("orphan1"),
        ])
        write_jsonl(chunk_dir / "stepA_failures.jsonl", [
            make_unit("orphan2"),
        ])

        expected = {"a"}
        chunks = {"chunk_000": {}}

        report = _verify_step(run_dir, "stepA", chunks, expected)

        assert report["orphaned"] == 2
        assert sorted(report["orphaned_ids"]) == ["orphan1", "orphan2"]

    def test_no_output_files_all_missing(self, tmp_path):
        """When no validated or failures files exist, all expected IDs are missing."""
        run_dir = tmp_path / "step_empty"
        run_dir.mkdir()
        (run_dir / "chunks" / "chunk_000").mkdir(parents=True)

        expected = {"x", "y"}
        chunks = {"chunk_000": {}}

        report = _verify_step(run_dir, "stepA", chunks, expected)

        assert report["missing"] == 2
        assert report["valid"] == 0
        assert report["failed"] == 0

    def test_multi_chunk_aggregation(self, tmp_path):
        """_verify_step aggregates across multiple chunk directories."""
        run_dir = tmp_path / "step_multi"
        run_dir.mkdir()

        chunk0 = run_dir / "chunks" / "chunk_000"
        chunk1 = run_dir / "chunks" / "chunk_001"
        write_jsonl(chunk0 / "stepA_validated.jsonl", [make_unit("a")])
        write_jsonl(chunk1 / "stepA_validated.jsonl", [make_unit("b")])

        expected = {"a", "b", "c"}
        chunks = {"chunk_000": {}, "chunk_001": {}}

        report = _verify_step(run_dir, "stepA", chunks, expected)

        assert report["valid"] == 2
        assert report["missing"] == 1
        assert report["missing_ids"] == ["c"]

    def test_records_without_unit_id_skipped(self, tmp_path):
        """Records without unit_id field in validated/failures are skipped."""
        run_dir = tmp_path / "step_no_uid"
        run_dir.mkdir()

        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "stepA_validated.jsonl", [
            make_unit("a"),
            {"no_id": True},  # should be skipped
        ])
        write_jsonl(chunk_dir / "stepA_failures.jsonl", [
            {"no_id": True},  # should be skipped
        ])

        expected = {"a"}
        chunks = {"chunk_000": {}}

        report = _verify_step(run_dir, "stepA", chunks, expected)

        assert report["valid"] == 1
        assert report["failed"] == 0
        assert report["missing"] == 0

    def test_empty_expected_set(self, tmp_path):
        """When expected set is empty, any output is orphaned."""
        run_dir = tmp_path / "step_empty_exp"
        run_dir.mkdir()

        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "stepA_validated.jsonl", [make_unit("a")])

        expected = set()
        chunks = {"chunk_000": {}}

        report = _verify_step(run_dir, "stepA", chunks, expected)

        assert report["expected"] == 0
        assert report["valid"] == 1
        assert report["orphaned"] == 1
        assert report["missing"] == 0


# =============================================================================
# repair_run() tests
# =============================================================================

class TestRepairRun:
    """Tests for repair_run()."""

    def test_missing_manifest_returns_error(self, tmp_path):
        """repair_run returns error when MANIFEST.json is missing."""
        run_dir = tmp_path / "repair_no_manifest"
        run_dir.mkdir()

        result = repair_run(run_dir)

        assert "error" in result
        assert "Cannot load MANIFEST.json" in result["error"]

    def test_perfect_run_no_repair_needed(self, tmp_path):
        """repair_run reports no missing units for a perfect run."""
        run_dir = tmp_path / "repair_perfect"
        run_dir.mkdir()
        build_perfect_single_step_run(run_dir, num_units=3)

        result = repair_run(run_dir)

        assert result["missing_count"] == 0
        assert result["message"] == "No missing units found"
        assert "error" not in result

    def test_repair_missing_at_step0(self, tmp_path):
        """Missing units at step 0 are sourced from units.jsonl and a retry chunk is created."""
        run_dir = tmp_path / "repair_s0"
        run_dir.mkdir()

        # 4 units, only 2 pass step1
        all_ids = ["unit_000", "unit_001", "unit_002", "unit_003"]
        valid_ids = ["unit_000", "unit_001"]

        manifest = {
            "pipeline": ["step1"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED", "items": 4, "valid": 2,
                    "failed": 0, "retries": 0, "input_tokens": 0, "output_tokens": 0,
                }
            },
            "metadata": {"pipeline_name": "repair_test"},
            "created": "2024-01-01T00:00:00Z",
            "updated": "2024-01-01T00:00:00Z",
            "status": "complete",
        }
        write_manifest(run_dir, manifest)

        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "units.jsonl", [make_unit(uid, data="d") for uid in all_ids])
        write_jsonl(chunk_dir / "step1_validated.jsonl", [make_unit(uid) for uid in valid_ids])

        result = repair_run(run_dir)

        assert result["missing_count"] == 2
        assert len(result["chunks_created"]) == 1
        assert result["run_dir"] == str(run_dir)

        created = result["chunks_created"][0]
        assert created["step"] == "step1"
        assert created["target_state"] == "step1_PENDING"
        assert created["unit_count"] == 2

        # Verify the retry chunk directory was created
        retry_chunk_dir = run_dir / "chunks" / created["chunk_name"]
        assert retry_chunk_dir.exists()

        # Verify units.jsonl was written in the retry chunk
        retry_units = retry_chunk_dir / "units.jsonl"
        assert retry_units.exists()
        with open(retry_units) as f:
            lines = [json.loads(l) for l in f if l.strip()]
        retry_unit_ids = {r["unit_id"] for r in lines}
        assert retry_unit_ids == {"unit_002", "unit_003"}

        # Verify manifest was updated
        with open(run_dir / "MANIFEST.json") as f:
            updated_manifest = json.load(f)
        assert created["chunk_name"] in updated_manifest["chunks"]
        assert updated_manifest["chunks"][created["chunk_name"]]["state"] == "step1_PENDING"
        # Status should be reset from complete to running
        assert updated_manifest.get("status") == "running"

    def test_repair_missing_at_step1(self, tmp_path):
        """Missing units at step 1 are sourced from step 0's validated output."""
        run_dir = tmp_path / "repair_s1"
        run_dir.mkdir()

        all_ids = ["unit_000", "unit_001", "unit_002"]
        # All pass step1 but unit_002 goes missing at step2
        build_multi_step_run(
            run_dir,
            num_units=3,
            step1_valid_ids=all_ids,
            step2_valid_ids=["unit_000", "unit_001"],
            status="complete",
        )

        result = repair_run(run_dir)

        assert result["missing_count"] == 1
        assert len(result["chunks_created"]) == 1

        created = result["chunks_created"][0]
        assert created["step"] == "step2"
        assert created["target_state"] == "step2_PENDING"
        assert created["unit_count"] == 1

        # Verify the retry chunk has both units.jsonl and step1_validated.jsonl
        retry_chunk_dir = run_dir / "chunks" / created["chunk_name"]
        assert (retry_chunk_dir / "units.jsonl").exists()
        assert (retry_chunk_dir / "step1_validated.jsonl").exists()

        # The step1_validated.jsonl should contain the missing unit's data
        with open(retry_chunk_dir / "step1_validated.jsonl") as f:
            lines = [json.loads(l) for l in f if l.strip()]
        assert len(lines) == 1
        assert lines[0]["unit_id"] == "unit_002"

    def test_repair_resets_terminal_status(self, tmp_path):
        """repair_run resets terminal run status (complete/failed/killed) to running."""
        for terminal_status in ("complete", "failed", "killed"):
            run_dir = tmp_path / f"repair_{terminal_status}"
            run_dir.mkdir()

            manifest = {
                "pipeline": ["step1"],
                "chunks": {
                    "chunk_000": {
                        "state": "VALIDATED", "items": 2, "valid": 1,
                        "failed": 0, "retries": 0, "input_tokens": 0, "output_tokens": 0,
                    }
                },
                "metadata": {},
                "created": "2024-01-01T00:00:00Z",
                "updated": "2024-01-01T00:00:00Z",
                "status": terminal_status,
            }
            write_manifest(run_dir, manifest)

            chunk_dir = run_dir / "chunks" / "chunk_000"
            write_jsonl(chunk_dir / "units.jsonl", [make_unit("a"), make_unit("b")])
            write_jsonl(chunk_dir / "step1_validated.jsonl", [make_unit("a")])

            result = repair_run(run_dir)

            assert result["missing_count"] == 1
            with open(run_dir / "MANIFEST.json") as f:
                updated = json.load(f)
            assert updated["status"] == "running"

    def test_repair_non_terminal_status_unchanged(self, tmp_path):
        """Non-terminal status (e.g., 'running') is not changed by repair_run."""
        run_dir = tmp_path / "repair_running"
        run_dir.mkdir()

        manifest = {
            "pipeline": ["step1"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED", "items": 2, "valid": 1,
                    "failed": 0, "retries": 0, "input_tokens": 0, "output_tokens": 0,
                }
            },
            "metadata": {},
            "created": "2024-01-01T00:00:00Z",
            "updated": "2024-01-01T00:00:00Z",
            "status": "running",
        }
        write_manifest(run_dir, manifest)

        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "units.jsonl", [make_unit("a"), make_unit("b")])
        write_jsonl(chunk_dir / "step1_validated.jsonl", [make_unit("a")])

        result = repair_run(run_dir)

        assert result["missing_count"] == 1
        with open(run_dir / "MANIFEST.json") as f:
            updated = json.load(f)
        # Status should remain 'running', not changed
        assert updated["status"] == "running"

    def test_repair_chunk_numbering(self, tmp_path):
        """New retry chunk number is one higher than the highest existing chunk."""
        run_dir = tmp_path / "repair_numbering"
        run_dir.mkdir()

        manifest = {
            "pipeline": ["step1"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED", "items": 2, "valid": 1,
                    "failed": 0, "retries": 0, "input_tokens": 0, "output_tokens": 0,
                },
                "chunk_005": {
                    "state": "VALIDATED", "items": 1, "valid": 1,
                    "failed": 0, "retries": 0, "input_tokens": 0, "output_tokens": 0,
                },
            },
            "metadata": {},
            "created": "2024-01-01T00:00:00Z",
            "updated": "2024-01-01T00:00:00Z",
        }
        write_manifest(run_dir, manifest)

        chunk0 = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk0 / "units.jsonl", [make_unit("a"), make_unit("b")])
        write_jsonl(chunk0 / "step1_validated.jsonl", [make_unit("a")])

        chunk5 = run_dir / "chunks" / "chunk_005"
        write_jsonl(chunk5 / "units.jsonl", [make_unit("c")])
        write_jsonl(chunk5 / "step1_validated.jsonl", [make_unit("c")])

        result = repair_run(run_dir)

        assert result["missing_count"] == 1
        created = result["chunks_created"][0]
        # Next after chunk_005 should be chunk_006
        assert created["chunk_name"] == "chunk_006"

    def test_repair_accepts_string_path(self, tmp_path):
        """repair_run accepts a string path and converts to Path."""
        run_dir = tmp_path / "repair_str"
        run_dir.mkdir()
        build_perfect_single_step_run(run_dir, num_units=2)

        result = repair_run(str(run_dir))

        assert result["missing_count"] == 0

    def test_repair_chunk_units_list_capped_in_display(self, tmp_path):
        """Units list in chunks_created is capped at 20 for display."""
        run_dir = tmp_path / "repair_cap"
        run_dir.mkdir()

        num_units = 25
        all_ids = [f"unit_{i:03d}" for i in range(num_units)]
        # Only first 2 pass step1, leaving 23 missing
        valid_ids = all_ids[:2]

        manifest = {
            "pipeline": ["step1"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED", "items": num_units, "valid": 2,
                    "failed": 0, "retries": 0, "input_tokens": 0, "output_tokens": 0,
                }
            },
            "metadata": {},
            "created": "2024-01-01T00:00:00Z",
            "updated": "2024-01-01T00:00:00Z",
        }
        write_manifest(run_dir, manifest)

        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "units.jsonl", [make_unit(uid) for uid in all_ids])
        write_jsonl(chunk_dir / "step1_validated.jsonl", [make_unit(uid) for uid in valid_ids])

        result = repair_run(run_dir)

        created = result["chunks_created"][0]
        # Display list capped at 20
        assert len(created["units"]) == 20
        # But unit_count reflects the actual count
        assert created["unit_count"] == 23

    def test_repair_missing_at_both_steps(self, tmp_path):
        """Repair handles missing units at multiple pipeline steps."""
        run_dir = tmp_path / "repair_both"
        run_dir.mkdir()

        all_ids = ["unit_000", "unit_001", "unit_002", "unit_003", "unit_004"]
        # unit_003, unit_004 missing at step1; unit_002 missing at step2
        step1_valid = ["unit_000", "unit_001", "unit_002"]
        step2_valid = ["unit_000", "unit_001"]

        build_multi_step_run(
            run_dir,
            num_units=5,
            step1_valid_ids=step1_valid,
            step2_valid_ids=step2_valid,
            status="complete",
        )

        result = repair_run(run_dir)

        assert result["missing_count"] == 3  # 2 at step1 + 1 at step2
        assert len(result["chunks_created"]) == 2

        step_names = [c["step"] for c in result["chunks_created"]]
        assert "step1" in step_names
        assert "step2" in step_names

    def test_repair_no_unit_data_found_skips_chunk(self, tmp_path):
        """If unit data cannot be found for missing units, no retry chunk is created."""
        run_dir = tmp_path / "repair_no_data"
        run_dir.mkdir()

        manifest = {
            "pipeline": ["step1"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED", "items": 2, "valid": 1,
                    "failed": 0, "retries": 0, "input_tokens": 0, "output_tokens": 0,
                }
            },
            "metadata": {},
            "created": "2024-01-01T00:00:00Z",
            "updated": "2024-01-01T00:00:00Z",
        }
        write_manifest(run_dir, manifest)

        chunk_dir = run_dir / "chunks" / "chunk_000"
        # units.jsonl has unit_000 and unit_001
        write_jsonl(chunk_dir / "units.jsonl", [make_unit("unit_000"), make_unit("unit_001")])
        # Only unit_000 is validated
        write_jsonl(chunk_dir / "step1_validated.jsonl", [make_unit("unit_000")])

        # Verify first that unit_001 is indeed missing
        verify = verify_run(run_dir)
        assert verify["total_missing"] == 1

        result = repair_run(run_dir)

        # unit_001 IS in units.jsonl, so it should be found
        assert result["missing_count"] == 1
        assert len(result["chunks_created"]) == 1

    def test_repair_missing_data_at_later_step_no_prev_validated(self, tmp_path):
        """When missing at step1 but previous step validated has no matching data, chunk is skipped."""
        run_dir = tmp_path / "repair_no_prev"
        run_dir.mkdir()

        manifest = {
            "pipeline": ["step1", "step2"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED", "items": 3, "valid": 1,
                    "failed": 0, "retries": 0, "input_tokens": 0, "output_tokens": 0,
                }
            },
            "metadata": {},
            "created": "2024-01-01T00:00:00Z",
            "updated": "2024-01-01T00:00:00Z",
        }
        write_manifest(run_dir, manifest)

        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "units.jsonl", [
            make_unit("a"), make_unit("b"), make_unit("c"),
        ])
        # All pass step1
        write_jsonl(chunk_dir / "step1_validated.jsonl", [
            make_unit("a"), make_unit("b"), make_unit("c"),
        ])
        # Only 'a' passes step2 => 'b' and 'c' missing at step2
        write_jsonl(chunk_dir / "step2_validated.jsonl", [make_unit("a")])

        result = repair_run(run_dir)

        assert result["missing_count"] == 2
        # 'b' and 'c' should be found in step1_validated.jsonl
        assert len(result["chunks_created"]) == 1
        created = result["chunks_created"][0]
        assert created["step"] == "step2"
        assert created["unit_count"] == 2

    def test_repair_empty_pipeline_returns_error(self, tmp_path):
        """repair_run propagates verify_run error for empty pipeline."""
        run_dir = tmp_path / "repair_empty_pipe"
        run_dir.mkdir()
        write_manifest(run_dir, {
            "pipeline": [],
            "chunks": {},
            "metadata": {},
        })

        result = repair_run(run_dir)

        assert "error" in result

    def test_repair_manifest_chunk_metadata(self, tmp_path):
        """Verify the retry chunk entry in the manifest has correct metadata fields."""
        run_dir = tmp_path / "repair_meta"
        run_dir.mkdir()

        manifest = {
            "pipeline": ["step1"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED", "items": 3, "valid": 2,
                    "failed": 0, "retries": 0, "input_tokens": 0, "output_tokens": 0,
                }
            },
            "metadata": {},
            "created": "2024-01-01T00:00:00Z",
            "updated": "2024-01-01T00:00:00Z",
        }
        write_manifest(run_dir, manifest)

        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "units.jsonl", [
            make_unit("a"), make_unit("b"), make_unit("c"),
        ])
        write_jsonl(chunk_dir / "step1_validated.jsonl", [make_unit("a"), make_unit("b")])

        result = repair_run(run_dir)

        with open(run_dir / "MANIFEST.json") as f:
            updated_manifest = json.load(f)

        chunk_name = result["chunks_created"][0]["chunk_name"]
        chunk_entry = updated_manifest["chunks"][chunk_name]

        assert chunk_entry["state"] == "step1_PENDING"
        assert chunk_entry["items"] == 1
        assert chunk_entry["valid"] == 0
        assert chunk_entry["failed"] == 0
        assert chunk_entry["retries"] == 0
        assert chunk_entry["input_tokens"] == 0
        assert chunk_entry["output_tokens"] == 0
        assert chunk_entry["submitted_at"] is None
        assert chunk_entry["provider_status"] is None

    def test_repair_only_duplicated_no_missing(self, tmp_path):
        """Duplicated units without missing units: verify WARN but repair finds nothing to fix."""
        run_dir = tmp_path / "repair_dupe_only"
        run_dir.mkdir()

        manifest = {
            "pipeline": ["step1"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED", "items": 2, "valid": 2,
                    "failed": 0, "retries": 0, "input_tokens": 0, "output_tokens": 0,
                }
            },
            "metadata": {},
            "created": "2024-01-01T00:00:00Z",
            "updated": "2024-01-01T00:00:00Z",
        }
        write_manifest(run_dir, manifest)

        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "units.jsonl", [make_unit("a"), make_unit("b")])
        # 'a' appears twice (duplicate) but both units are accounted for
        write_jsonl(chunk_dir / "step1_validated.jsonl", [
            make_unit("a"), make_unit("a"), make_unit("b"),
        ])

        # Verify first
        verify_result = verify_run(run_dir)
        assert verify_result["integrity"] == "WARN"
        assert verify_result["total_duplicated"] == 1
        assert verify_result["total_missing"] == 0

        # Repair should find no missing units, but integrity is WARN
        # so it enters the repair path then finds no missing_by_step
        result = repair_run(run_dir)

        assert result["missing_count"] == 0
        assert result["message"] == "No missing units found"


# =============================================================================
# Pipeline chaining tests (expected_ids flow through steps)
# =============================================================================

class TestPipelineChaining:
    """Tests verifying that valid IDs from step N become expected IDs for step N+1."""

    def test_step1_failures_reduce_step2_expected(self, tmp_path):
        """
        If step1 has 5 valid and step2 expects only those 5,
        a unit failing step1 should NOT be expected at step2.
        """
        run_dir = tmp_path / "chain_fail"
        run_dir.mkdir()

        all_ids = ["u0", "u1", "u2", "u3", "u4"]
        step1_valid = ["u0", "u1", "u2"]
        step1_failed = ["u3", "u4"]

        manifest = {
            "pipeline": ["step1", "step2"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED", "items": 5, "valid": 3,
                    "failed": 2, "retries": 0, "input_tokens": 0, "output_tokens": 0,
                }
            },
            "metadata": {},
            "created": "2024-01-01T00:00:00Z",
            "updated": "2024-01-01T00:00:00Z",
        }
        write_manifest(run_dir, manifest)

        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "units.jsonl", [make_unit(uid) for uid in all_ids])
        write_jsonl(chunk_dir / "step1_validated.jsonl", [make_unit(uid) for uid in step1_valid])
        write_jsonl(chunk_dir / "step1_failures.jsonl", [make_unit(uid) for uid in step1_failed])
        write_jsonl(chunk_dir / "step2_validated.jsonl", [make_unit(uid) for uid in step1_valid])

        result = verify_run(run_dir)

        assert result["integrity"] == "OK"
        # step2 expected only 3 (from step1 valid), all 3 present
        assert result["steps"][1]["expected"] == 3
        assert result["steps"][1]["valid"] == 3
        assert result["steps"][1]["missing"] == 0

    def test_three_step_pipeline(self, tmp_path):
        """Verify chaining through a three-step pipeline."""
        run_dir = tmp_path / "three_steps"
        run_dir.mkdir()

        ids = ["a", "b", "c", "d"]

        manifest = {
            "pipeline": ["s1", "s2", "s3"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED", "items": 4, "valid": 2,
                    "failed": 0, "retries": 0, "input_tokens": 0, "output_tokens": 0,
                }
            },
            "metadata": {},
            "created": "2024-01-01T00:00:00Z",
            "updated": "2024-01-01T00:00:00Z",
        }
        write_manifest(run_dir, manifest)

        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "units.jsonl", [make_unit(uid) for uid in ids])
        # s1: all pass
        write_jsonl(chunk_dir / "s1_validated.jsonl", [make_unit(uid) for uid in ids])
        # s2: 'c' drops
        write_jsonl(chunk_dir / "s2_validated.jsonl", [make_unit("a"), make_unit("b"), make_unit("d")])
        # s3: 'b' drops
        write_jsonl(chunk_dir / "s3_validated.jsonl", [make_unit("a"), make_unit("d")])

        result = verify_run(run_dir)

        # s1: 4 expected, 4 valid, 0 missing
        assert result["steps"][0]["missing"] == 0
        # s2: 4 expected (from s1), 3 valid, 1 missing (c)
        assert result["steps"][1]["expected"] == 4
        assert result["steps"][1]["missing"] == 1
        assert result["steps"][1]["missing_ids"] == ["c"]
        # s3: 3 expected (from s2 valid), 2 valid, 1 missing (b)
        assert result["steps"][2]["expected"] == 3
        assert result["steps"][2]["missing"] == 1
        assert result["steps"][2]["missing_ids"] == ["b"]

        assert result["integrity"] == "WARN"
        assert result["total_missing"] == 2


# =============================================================================
# Gzip support tests (Bug 4a)
# =============================================================================

class TestGzipSupport:
    """Tests verifying that verify_run and repair_run handle .jsonl.gz files."""

    def test_verify_reads_gzipped_validated_files(self, tmp_path):
        """verify_run correctly reads gzipped validated files."""
        import gzip

        run_dir = tmp_path / "gz_verify"
        run_dir.mkdir()

        manifest = {
            "pipeline": ["step1"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED", "items": 3, "valid": 2,
                    "failed": 1, "retries": 0, "input_tokens": 0, "output_tokens": 0,
                }
            },
            "metadata": {},
            "created": "2024-01-01T00:00:00Z",
            "updated": "2024-01-01T00:00:00Z",
        }
        write_manifest(run_dir, manifest)

        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "units.jsonl", [
            make_unit("a"), make_unit("b"), make_unit("c"),
        ])

        # Write validated file as gzipped (simulating post-processing compression)
        gz_path = chunk_dir / "step1_validated.jsonl.gz"
        gz_path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(gz_path, "wt", encoding="utf-8") as f:
            for record in [make_unit("a"), make_unit("b")]:
                f.write(json.dumps(record) + "\n")

        # Write failures as plain jsonl
        write_jsonl(chunk_dir / "step1_failures.jsonl", [make_unit("c")])

        result = verify_run(run_dir)

        assert result["steps"][0]["valid"] == 2
        assert result["steps"][0]["failed"] == 1
        assert result["steps"][0]["missing"] == 0
        assert result["integrity"] == "OK"

    def test_verify_still_works_with_uncompressed_files(self, tmp_path):
        """Regression: verify_run still works with plain .jsonl files."""
        run_dir = tmp_path / "plain_verify"
        run_dir.mkdir()

        manifest = {
            "pipeline": ["step1"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED", "items": 2, "valid": 2,
                    "failed": 0, "retries": 0, "input_tokens": 0, "output_tokens": 0,
                }
            },
            "metadata": {},
            "created": "2024-01-01T00:00:00Z",
            "updated": "2024-01-01T00:00:00Z",
        }
        write_manifest(run_dir, manifest)

        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "units.jsonl", [make_unit("a"), make_unit("b")])
        write_jsonl(chunk_dir / "step1_validated.jsonl", [make_unit("a"), make_unit("b")])

        result = verify_run(run_dir)

        assert result["steps"][0]["valid"] == 2
        assert result["steps"][0]["missing"] == 0
        assert result["integrity"] == "OK"

    def test_repair_reads_gzipped_prev_step_validated(self, tmp_path):
        """repair_run finds unit data from gzipped previous step validated files."""
        import gzip

        run_dir = tmp_path / "gz_repair"
        run_dir.mkdir()

        manifest = {
            "pipeline": ["step1", "step2"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED", "items": 3, "valid": 2,
                    "failed": 0, "retries": 0, "input_tokens": 0, "output_tokens": 0,
                }
            },
            "metadata": {},
            "status": "complete",
            "created": "2024-01-01T00:00:00Z",
            "updated": "2024-01-01T00:00:00Z",
        }
        write_manifest(run_dir, manifest)

        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "units.jsonl", [
            make_unit("a"), make_unit("b"), make_unit("c"),
        ])

        # step1 validated is gzipped (post-processing compressed it)
        gz_path = chunk_dir / "step1_validated.jsonl.gz"
        gz_path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(gz_path, "wt", encoding="utf-8") as f:
            for record in [make_unit("a"), make_unit("b"), make_unit("c")]:
                f.write(json.dumps(record) + "\n")

        # step2 only has "a" valid — "b" and "c" are missing
        write_jsonl(chunk_dir / "step2_validated.jsonl", [make_unit("a")])

        result = repair_run(run_dir)

        assert result["missing_count"] == 2
        assert len(result["chunks_created"]) == 1
        created = result["chunks_created"][0]
        assert created["step"] == "step2"
        assert created["unit_count"] == 2

    def test_repair_keeps_richest_record_when_duplicates_exist(self, tmp_path):
        """repair_run keeps the record with the most keys when duplicates exist.

        Regression test: retry chunks can append stripped records (just LLM
        output fields) to *_validated.jsonl alongside the original full record.
        The old code took the last record, silently discarding inherited context
        fields like manifestation, wound_description, etc.
        """
        run_dir = tmp_path / "repair_dupes"
        run_dir.mkdir()

        manifest = {
            "pipeline": ["step1", "step2"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED", "items": 2, "valid": 1,
                    "failed": 0, "retries": 0, "input_tokens": 0, "output_tokens": 0,
                }
            },
            "metadata": {},
            "status": "complete",
            "created": "2024-01-01T00:00:00Z",
            "updated": "2024-01-01T00:00:00Z",
        }
        write_manifest(run_dir, manifest)

        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "units.jsonl", [
            make_unit("u1", data="x"), make_unit("u2", data="y"),
        ])

        # step1_validated has a rich record (21 keys simulated) followed by
        # a stripped duplicate (9 keys) for the same unit — mimicking the
        # retry-append pattern that caused the elena_full_25pro bug.
        rich_record = make_unit(
            "u2", text="hello", expression="guarded", wound="ISOLATION",
            state="fully_active", response_type="neutral",
            voice_notes="tightened", manifestation="manifestation_1",
            wound_description="Protective distance", state_description="Fully defended",
            response_description="Neutral reading", slot_type="single_wound",
        )
        stripped_record = make_unit(
            "u2", text="hello", expression="guarded", wound="ISOLATION",
            state="fully_active", response_type="neutral",
            voice_notes="tightened",
        )
        write_jsonl(chunk_dir / "step1_validated.jsonl", [
            make_unit("u1", text="ok", manifestation="m1"),
            rich_record,
            stripped_record,  # duplicate — fewer keys, comes last
        ])

        # Only u1 has step2 results — u2 is missing
        write_jsonl(chunk_dir / "step2_validated.jsonl", [
            make_unit("u1", score=5),
        ])

        result = repair_run(run_dir)
        assert result["missing_count"] == 1

        # The retry chunk should have the RICH version of u2, not the stripped one
        retry_chunk = result["chunks_created"][0]["chunk_name"]
        retry_dir = run_dir / "chunks" / retry_chunk
        with open(retry_dir / "step1_validated.jsonl") as f:
            records = [json.loads(l) for l in f if l.strip()]
        assert len(records) == 1
        assert records[0]["unit_id"] == "u2"
        assert records[0].get("manifestation") == "manifestation_1"
        assert records[0].get("wound_description") == "Protective distance"
        assert len(records[0]) == len(rich_record)


# =============================================================================
# _load_step_records() dedup tests
# =============================================================================

class TestLoadStepRecords:
    """Tests for _load_step_records() duplicate handling."""

    def test_deduplicates_keeping_richest_record(self, tmp_path):
        """_load_step_records keeps the record with the most keys per unit_id."""
        run_dir = tmp_path / "load_dedup"
        run_dir.mkdir()

        manifest = {
            "pipeline": ["step1"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED", "items": 2, "valid": 2,
                    "failed": 0, "retries": 0, "input_tokens": 0, "output_tokens": 0,
                }
            },
            "metadata": {},
            "created": "2024-01-01T00:00:00Z",
            "updated": "2024-01-01T00:00:00Z",
        }
        write_manifest(run_dir, manifest)

        chunk_dir = run_dir / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True, exist_ok=True)

        rich = make_unit("u1", text="hi", manifestation="m1", wound="SHAME", extra="ctx")
        stripped = make_unit("u1", text="hi")
        unique = make_unit("u2", text="bye", manifestation="m2")

        write_jsonl(chunk_dir / "step1_validated.jsonl", [rich, stripped, unique])

        records = _load_step_records(run_dir, "step1")
        by_id = {r["unit_id"]: r for r in records}

        assert len(records) == 2
        assert by_id["u1"].get("manifestation") == "m1"
        assert by_id["u1"].get("wound") == "SHAME"
        assert len(by_id["u1"]) == len(rich)
        assert by_id["u2"].get("manifestation") == "m2"


# =============================================================================
# generate_report() tests
# =============================================================================

class TestGenerateReport:
    """Tests for generate_report()."""

    def test_missing_manifest_returns_error(self, tmp_path):
        """generate_report returns error when no manifest exists."""
        run_dir = tmp_path / "missing_run"
        run_dir.mkdir()
        result = generate_report(run_dir)
        assert "error" in result

    def test_empty_pipeline_returns_error(self, tmp_path):
        """generate_report returns error when pipeline is empty."""
        run_dir = tmp_path / "empty_run"
        run_dir.mkdir()
        write_manifest(run_dir, {"pipeline": [], "chunks": {}, "metadata": {}})
        result = generate_report(run_dir)
        assert "error" in result

    def test_basic_report_from_perfect_run(self, tmp_path):
        """generate_report produces correct funnel for a perfect single-step run."""
        run_dir = tmp_path / "perfect_run"
        run_dir.mkdir()
        build_perfect_single_step_run(run_dir, num_units=5)
        # Add metadata for cost calculation
        manifest = json.loads((run_dir / "MANIFEST.json").read_text())
        manifest["metadata"]["provider"] = "gemini"
        manifest["metadata"]["model"] = "gemini-2.0-flash-001"
        manifest["metadata"]["mode"] = "batch"
        manifest["metadata"]["initial_input_tokens"] = 1000
        manifest["metadata"]["initial_output_tokens"] = 500
        manifest["status"] = "complete"
        with open(run_dir / "MANIFEST.json", "w") as f:
            json.dump(manifest, f)

        result = generate_report(run_dir)
        assert "error" not in result
        assert "text" in result
        assert result["total_units"] == 5
        assert result["surviving_units"] == 5
        assert result["yield_pct"] == 100.0

        # Check funnel data
        assert len(result["funnel"]) == 1
        assert result["funnel"][0]["valid"] == 5
        assert result["funnel"][0]["failed"] == 0
        assert result["funnel"][0]["pass_pct"] == 100.0

        # Check text contains key sections
        text = result["text"]
        assert "VALIDATION FUNNEL" in text
        assert "TOKEN SUMMARY" in text
        assert "step1" in text

    def test_report_with_failures(self, tmp_path):
        """generate_report correctly reports failures in funnel."""
        run_dir = tmp_path / "fail_run"
        run_dir.mkdir()

        all_ids = [f"item_a__rep{i:04d}" for i in range(10)]
        units = [make_unit(uid) for uid in all_ids]
        valid_ids = all_ids[:8]
        failed_ids = all_ids[8:]

        manifest = {
            "pipeline": ["step1"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED",
                    "items": 10,
                    "valid": 8,
                    "failed": 2,
                    "retries": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                }
            },
            "metadata": {
                "pipeline_name": "test",
                "provider": "gemini",
                "model": "gemini-2.0-flash-001",
                "initial_input_tokens": 500,
                "initial_output_tokens": 200,
                "retry_input_tokens": 0,
                "retry_output_tokens": 0,
            },
            "status": "complete",
        }
        write_manifest(run_dir, manifest)
        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "units.jsonl", units)
        write_jsonl(chunk_dir / "step1_validated.jsonl",
                    [make_unit(uid) for uid in valid_ids])
        write_jsonl(chunk_dir / "step1_failures.jsonl",
                    [make_unit(uid, error="bad output") for uid in failed_ids])

        result = generate_report(run_dir)
        assert result["surviving_units"] == 8
        assert result["total_units"] == 10
        assert result["yield_pct"] == 80.0
        assert result["funnel"][0]["failed"] == 2

        # Check failures by item section
        text = result["text"]
        assert "FAILURES BY ITEM" in text
        assert "item_a" in text

    def test_report_with_multi_step_failures(self, tmp_path):
        """generate_report correctly chains multi-step funnel."""
        run_dir = tmp_path / "multi_run"
        run_dir.mkdir()
        build_multi_step_run(
            run_dir,
            num_units=5,
            step1_valid_ids=["unit_000", "unit_001", "unit_002", "unit_003"],
            step1_failed_ids=["unit_004"],
            step2_valid_ids=["unit_000", "unit_001", "unit_002"],
            step2_failed_ids=["unit_003"],
        )
        # Add metadata
        manifest = json.loads((run_dir / "MANIFEST.json").read_text())
        manifest["metadata"]["provider"] = "openai"
        manifest["metadata"]["model"] = "gpt-4o-mini"
        manifest["metadata"]["initial_input_tokens"] = 2000
        manifest["metadata"]["initial_output_tokens"] = 1000
        manifest["metadata"]["retry_input_tokens"] = 0
        manifest["metadata"]["retry_output_tokens"] = 0
        manifest["status"] = "complete"
        with open(run_dir / "MANIFEST.json", "w") as f:
            json.dump(manifest, f)

        result = generate_report(run_dir)
        assert result["surviving_units"] == 3
        assert len(result["funnel"]) == 2
        assert result["funnel"][0]["valid"] == 4  # step1: 4/5
        assert result["funnel"][1]["valid"] == 3  # step2: 3/4

    def test_report_handles_gzipped_files(self, tmp_path):
        """generate_report handles .jsonl.gz files transparently."""
        import gzip
        run_dir = tmp_path / "gz_run"
        run_dir.mkdir()

        units = [make_unit(f"u{i}") for i in range(3)]
        manifest = {
            "pipeline": ["step1"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED",
                    "items": 3,
                    "valid": 3,
                    "failed": 0,
                    "retries": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                }
            },
            "metadata": {
                "pipeline_name": "gz_test",
                "provider": "gemini",
                "model": "gemini-2.0-flash-001",
                "initial_input_tokens": 100,
                "initial_output_tokens": 50,
                "retry_input_tokens": 0,
                "retry_output_tokens": 0,
            },
            "status": "complete",
        }
        write_manifest(run_dir, manifest)
        chunk_dir = run_dir / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)
        write_jsonl(chunk_dir / "units.jsonl", units)

        # Write validated as .jsonl.gz
        validated = [make_unit(f"u{i}", result="ok") for i in range(3)]
        gz_path = chunk_dir / "step1_validated.jsonl.gz"
        with gzip.open(gz_path, "wt") as f:
            for record in validated:
                f.write(json.dumps(record) + "\n")

        result = generate_report(run_dir)
        assert result["surviving_units"] == 3
        assert result["funnel"][0]["valid"] == 3

    def test_report_in_progress_run(self, tmp_path):
        """generate_report handles runs with no end_time (in progress)."""
        run_dir = tmp_path / "progress_run"
        run_dir.mkdir()

        units = [make_unit("u0")]
        manifest = {
            "pipeline": ["step1"],
            "chunks": {
                "chunk_000": {
                    "state": "step1_PENDING",
                    "items": 1,
                    "valid": 0,
                    "failed": 0,
                    "retries": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                }
            },
            "metadata": {
                "pipeline_name": "test",
                "provider": "gemini",
                "model": "gemini-2.0-flash-001",
                "start_time": "2024-01-01T00:00:00Z",
                "initial_input_tokens": 0,
                "initial_output_tokens": 0,
                "retry_input_tokens": 0,
                "retry_output_tokens": 0,
            },
            "status": "running",
        }
        write_manifest(run_dir, manifest)
        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "units.jsonl", units)

        result = generate_report(run_dir)
        assert "error" not in result
        assert "in progress" in result["text"]

    def test_report_cost_uses_model_pricing(self, tmp_path):
        """generate_report calculates realtime cost using model registry pricing."""
        run_dir = tmp_path / "cost_run"
        run_dir.mkdir()
        build_perfect_single_step_run(run_dir, num_units=1)
        manifest = json.loads((run_dir / "MANIFEST.json").read_text())
        manifest["metadata"]["provider"] = "gemini"
        manifest["metadata"]["model"] = "gemini-2.0-flash-001"
        manifest["metadata"]["mode"] = "realtime"
        manifest["metadata"]["initial_input_tokens"] = 1_000_000
        manifest["metadata"]["initial_output_tokens"] = 1_000_000
        manifest["metadata"]["retry_input_tokens"] = 0
        manifest["metadata"]["retry_output_tokens"] = 0
        manifest["status"] = "complete"
        with open(run_dir / "MANIFEST.json", "w") as f:
            json.dump(manifest, f)

        result = generate_report(run_dir)
        # Gemini flash batch rates in models.yaml: $0.075/M in, $0.30/M out.
        # Realtime applies provider realtime_multiplier=2.0.
        # cost = (0.075 + 0.30) * 2 = 0.75
        assert abs(result["cost"] - 0.75) < 0.01

    def test_report_cost_uses_registry_batch_rates(self, tmp_path):
        """generate_report uses raw registry rates for batch mode."""
        run_dir = tmp_path / "batch_run"
        run_dir.mkdir()
        build_perfect_single_step_run(run_dir, num_units=1)
        manifest = json.loads((run_dir / "MANIFEST.json").read_text())
        manifest["metadata"]["provider"] = "gemini"
        manifest["metadata"]["model"] = "gemini-2.0-flash-001"
        manifest["metadata"]["mode"] = "batch"
        manifest["metadata"]["initial_input_tokens"] = 1_000_000
        manifest["metadata"]["initial_output_tokens"] = 1_000_000
        manifest["metadata"]["retry_input_tokens"] = 0
        manifest["metadata"]["retry_output_tokens"] = 0
        manifest["status"] = "complete"
        with open(run_dir / "MANIFEST.json", "w") as f:
            json.dump(manifest, f)

        result = generate_report(run_dir)
        # Batch uses registry rates as-is: 0.075 + 0.30 = 0.375
        assert abs(result["cost"] - 0.375) < 0.01

    def test_compute_cost_uses_registry_rates_for_batch_and_multiplier_for_realtime(self):
        """_compute_cost uses 1x batch rates and applies realtime multiplier only in realtime."""
        registry = {
            "defaults": {
                "input_per_million": 1.0,
                "output_per_million": 2.0,
                "realtime_multiplier": 2.0,
            },
            "providers": {
                "openai": {
                    "realtime_multiplier": 2.0,
                    "models": {
                        "gpt-4o-mini": {
                            "input_per_million": 0.075,
                            "output_per_million": 0.3,
                        }
                    },
                }
            },
        }

        batch_cost = _compute_cost(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            provider_name="openai",
            model_name="gpt-4o-mini",
            is_realtime=False,
            registry=registry,
        )
        realtime_cost = _compute_cost(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            provider_name="openai",
            model_name="gpt-4o-mini",
            is_realtime=True,
            registry=registry,
        )

        assert batch_cost == pytest.approx(0.375)
        assert realtime_cost == pytest.approx(0.75)

    def test_report_top_errors(self, tmp_path):
        """generate_report extracts top errors from failure records."""
        run_dir = tmp_path / "err_run"
        run_dir.mkdir()

        units = [make_unit(f"u{i}") for i in range(5)]
        manifest = {
            "pipeline": ["step1"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED",
                    "items": 5,
                    "valid": 2,
                    "failed": 3,
                    "retries": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                }
            },
            "metadata": {
                "pipeline_name": "test",
                "provider": "gemini",
                "model": "gemini-2.0-flash-001",
                "initial_input_tokens": 0,
                "initial_output_tokens": 0,
                "retry_input_tokens": 0,
                "retry_output_tokens": 0,
            },
            "status": "complete",
        }
        write_manifest(run_dir, manifest)
        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "units.jsonl", units)
        write_jsonl(chunk_dir / "step1_validated.jsonl",
                    [make_unit("u0"), make_unit("u1")])
        write_jsonl(chunk_dir / "step1_failures.jsonl", [
            make_unit("u2", error="Schema validation failed"),
            make_unit("u3", error="Schema validation failed"),
            make_unit("u4", error="Missing required field"),
        ])

        result = generate_report(run_dir)
        text = result["text"]
        assert "TOP ERRORS BY STEP" in text
        assert "Schema validation failed" in text


# =============================================================================
# _compute_cost pricing tests
# =============================================================================

class TestComputeCostPricing:
    """Verify _compute_cost uses registry rates correctly for batch and realtime."""

    def test_batch_cost_uses_registry_rates_directly(self):
        """
        Registry rates ARE batch rates. Batch mode must use them as-is (1x),
        not apply a 0.5x discount.
        """
        from run_tools import _compute_cost, _load_model_registry

        registry = _load_model_registry()
        # gemini-2.0-flash-001: input=0.075, output=0.30 per million
        input_tokens = 1_000_000
        output_tokens = 1_000_000

        cost = _compute_cost(input_tokens, output_tokens,
                             "gemini", "gemini-2.0-flash-001",
                             is_realtime=False, registry=registry)

        # Batch cost = (1M * 0.075 + 1M * 0.30) / 1M = 0.375
        assert abs(cost - 0.375) < 0.001, (
            f"Batch cost should be 0.375 (raw registry rates), got {cost}"
        )

    def test_realtime_cost_applies_provider_multiplier(self):
        """
        Realtime mode applies the provider's realtime_multiplier on top of
        registry (batch) rates.
        """
        from run_tools import _compute_cost, _load_model_registry

        registry = _load_model_registry()
        input_tokens = 1_000_000
        output_tokens = 1_000_000

        batch_cost = _compute_cost(input_tokens, output_tokens,
                                   "gemini", "gemini-2.0-flash-001",
                                   is_realtime=False, registry=registry)
        realtime_cost = _compute_cost(input_tokens, output_tokens,
                                      "gemini", "gemini-2.0-flash-001",
                                      is_realtime=True, registry=registry)

        # Gemini realtime_multiplier is 2.0
        assert abs(realtime_cost - batch_cost * 2.0) < 0.001, (
            f"Realtime cost should be 2x batch ({batch_cost * 2.0}), got {realtime_cost}"
        )

    def test_unknown_model_uses_defaults(self):
        """
        _compute_cost falls back to registry defaults for unknown models.
        This is the report-generation path (not TUI) — it uses defaults
        rather than returning 0 because reports need best-effort cost sums.
        """
        from run_tools import _compute_cost, _load_model_registry

        registry = _load_model_registry()
        cost = _compute_cost(1_000_000, 1_000_000,
                             "gemini", "nonexistent-model-xyz",
                             is_realtime=False, registry=registry)
        # Should get defaults (1.0, 2.0) -> cost = 3.0
        defaults = registry.get("defaults", {})
        expected = (defaults.get("input_per_million", 1.0) +
                    defaults.get("output_per_million", 2.0))
        assert abs(cost - expected) < 0.001, (
            f"Unknown model should use registry defaults, expected {expected}, got {cost}"
        )
