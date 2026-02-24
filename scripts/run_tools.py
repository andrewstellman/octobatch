"""
run_tools.py - CLI tools for run verification and repair.

Provides verify_run() and repair_run() functions for checking run integrity
and creating retry chunks for missing units. These address QUALITY.md
Scenario 1 (Silent Attrition).
"""

import json
import os
import tempfile
from pathlib import Path

from octobatch_utils import load_manifest, save_manifest, load_jsonl


def verify_run(run_dir: Path) -> dict:
    """
    Check run integrity by comparing expected units against actual outputs.

    For each pipeline step, compares expected unit IDs (from previous step's
    valid output) against actual valid + failed unit IDs. Reports missing,
    duplicated, and orphaned units.

    Args:
        run_dir: Path to the run directory

    Returns:
        Structured result dict with per-step reports and summary
    """
    run_dir = Path(run_dir)
    try:
        manifest = load_manifest(run_dir)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return {"error": f"Cannot load MANIFEST.json from {run_dir}: {e}"}

    pipeline = manifest.get("pipeline", [])
    chunks = manifest.get("chunks", {})
    metadata = manifest.get("metadata", {})

    if not pipeline:
        return {"error": "No pipeline steps found in manifest"}

    # Get initial unit IDs from chunk unit files
    initial_ids = set()
    for chunk_name in sorted(chunks.keys()):
        chunk_dir = run_dir / "chunks" / chunk_name
        units_file = chunk_dir / "units.jsonl"
        if units_file.exists():
            for record in load_jsonl(units_file):
                uid = record.get("unit_id")
                if uid:
                    initial_ids.add(uid)

    result = {
        "run_dir": str(run_dir),
        "run_name": run_dir.name,
        "pipeline_name": metadata.get("pipeline_name", ""),
        "pipeline": pipeline,
        "initial_units": len(initial_ids),
        "steps": [],
    }

    # Track expected IDs flowing through the pipeline
    expected_ids = initial_ids.copy()

    for step_idx, step_name in enumerate(pipeline):
        step_report = _verify_step(
            run_dir, step_name, chunks, expected_ids
        )
        result["steps"].append(step_report)

        # The valid IDs from this step become the expected IDs for the next step
        expected_ids = step_report["_valid_ids"]

    # Summary
    total_missing = sum(s["missing"] for s in result["steps"])
    total_duplicated = sum(s.get("duplicated", 0) for s in result["steps"])
    result["total_missing"] = total_missing
    result["total_duplicated"] = total_duplicated
    result["integrity"] = "OK" if total_missing == 0 and total_duplicated == 0 else "WARN"

    # Remove internal _valid_ids from output
    for step in result["steps"]:
        step.pop("_valid_ids", None)

    return result


def _verify_step(
    run_dir: Path,
    step_name: str,
    chunks: dict,
    expected_ids: set,
) -> dict:
    """
    Verify a single pipeline step.

    Scans {step}_validated.jsonl and {step}_failures.jsonl across all chunks.
    Compares against expected_ids.

    Returns step report dict with valid_ids set for pipeline chaining.
    """
    valid_ids = set()
    failed_ids = set()
    valid_count_with_dupes = 0

    for chunk_name in sorted(chunks.keys()):
        chunk_dir = run_dir / "chunks" / chunk_name

        # Scan validated file
        validated_file = chunk_dir / f"{step_name}_validated.jsonl"
        if validated_file.exists():
            for record in load_jsonl(validated_file):
                uid = record.get("unit_id")
                if uid:
                    valid_count_with_dupes += 1
                    valid_ids.add(uid)

        # Scan failures file
        failures_file = chunk_dir / f"{step_name}_failures.jsonl"
        if failures_file.exists():
            for record in load_jsonl(failures_file):
                uid = record.get("unit_id")
                if uid:
                    failed_ids.add(uid)

    # Units accounted for = valid union failed
    accounted_ids = valid_ids | failed_ids

    # Missing = expected but not accounted for
    missing_ids = expected_ids - accounted_ids

    # Duplicated = appeared more than once in valid files
    duplicated = max(0, valid_count_with_dupes - len(valid_ids))

    # Orphaned = accounted for but not expected
    orphaned_ids = accounted_ids - expected_ids

    return {
        "step": step_name,
        "expected": len(expected_ids),
        "valid": len(valid_ids),
        "failed": len(failed_ids),
        "missing": len(missing_ids),
        "duplicated": duplicated,
        "orphaned": len(orphaned_ids),
        "missing_ids": sorted(list(missing_ids)),  # Full list for repair_run
        "orphaned_ids": sorted(list(orphaned_ids)),
        "_valid_ids": valid_ids,  # Internal: passed to next step
    }


def repair_run(run_dir: Path) -> dict:
    """
    Create retry chunks for missing units found by verify_run().

    For each missing unit, determines which step it went missing at and
    creates a new retry chunk with the unit data from the previous step's
    validated output. The chunk state is set to {missing_step}_PENDING.

    Args:
        run_dir: Path to the run directory

    Returns:
        Result dict with repair details or error
    """
    run_dir = Path(run_dir)

    # First, run verification to find missing units
    verify_result = verify_run(run_dir)
    if verify_result.get("error"):
        return verify_result

    if verify_result.get("integrity") == "OK":
        return {"missing_count": 0, "message": "No missing units found"}

    try:
        manifest = load_manifest(run_dir)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return {"error": f"Cannot load MANIFEST.json from {run_dir}: {e}"}

    pipeline = verify_result.get("pipeline", [])
    chunks = manifest.get("chunks", {})

    # Collect all missing units with their target step
    # We need to re-run verify to get the _valid_ids (stripped from output)
    # Instead, gather missing units per step from the verify result
    missing_by_step = {}
    for step_report in verify_result.get("steps", []):
        step_name = step_report["step"]
        missing_ids = step_report.get("missing_ids", [])
        if missing_ids:
            missing_by_step[step_name] = set(missing_ids)

    if not missing_by_step:
        return {"missing_count": 0, "message": "No missing units found"}

    total_missing = sum(len(ids) for ids in missing_by_step.values())
    chunks_created = []

    # For each step with missing units, create a retry chunk
    for step_name, missing_ids in missing_by_step.items():
        if step_name not in pipeline:
            # Step name not in pipeline — manifest may have been modified
            continue
        step_idx = pipeline.index(step_name)

        # Get the unit data from the previous step's validated output
        # (or from the initial units file for step 0)
        unit_data = {}
        if step_idx == 0:
            # Get from initial units files
            for chunk_name in sorted(chunks.keys()):
                chunk_dir = run_dir / "chunks" / chunk_name
                units_file = chunk_dir / "units.jsonl"
                if units_file.exists():
                    for record in load_jsonl(units_file):
                        uid = record.get("unit_id")
                        if uid and uid in missing_ids:
                            unit_data[uid] = record
        else:
            # Get from previous step's validated output
            prev_step = pipeline[step_idx - 1]
            for chunk_name in sorted(chunks.keys()):
                chunk_dir = run_dir / "chunks" / chunk_name
                validated_file = chunk_dir / f"{prev_step}_validated.jsonl"
                if validated_file.exists():
                    for record in load_jsonl(validated_file):
                        uid = record.get("unit_id")
                        if uid and uid in missing_ids:
                            unit_data[uid] = record

        if not unit_data:
            continue

        # Create a new retry chunk
        existing_chunks = sorted(chunks.keys())
        if existing_chunks:
            last_num = max(
                int(c.replace("chunk_", "")) for c in existing_chunks
                if c.startswith("chunk_") and c.replace("chunk_", "").isdigit()
            )
            new_chunk_num = last_num + 1
        else:
            new_chunk_num = 0

        new_chunk_name = f"chunk_{new_chunk_num:03d}"
        new_chunk_dir = run_dir / "chunks" / new_chunk_name
        new_chunk_dir.mkdir(parents=True, exist_ok=True)

        # Write units file for the retry chunk
        units_list = list(unit_data.values())
        units_file = new_chunk_dir / "units.jsonl"
        with open(units_file, "w") as f:
            for record in units_list:
                f.write(json.dumps(record) + "\n")

        # If resuming from a later step, also write the previous step's
        # validated output so the pipeline can pick up from there
        if step_idx > 0:
            prev_step = pipeline[step_idx - 1]
            prev_validated_file = new_chunk_dir / f"{prev_step}_validated.jsonl"
            with open(prev_validated_file, "w") as f:
                for record in units_list:
                    f.write(json.dumps(record) + "\n")

        # Set chunk state in manifest
        target_state = f"{step_name}_PENDING"
        manifest["chunks"][new_chunk_name] = {
            "state": target_state,
            "items": len(units_list),
            "valid": 0,
            "failed": 0,
            "retries": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "submitted_at": None,
            "provider_status": None,
        }

        # If the run was in a terminal state, reset to running so
        # the orchestrator will pick it up
        if manifest.get("status") in ("complete", "failed", "killed"):
            manifest["status"] = "running"

        chunks_created.append({
            "chunk_name": new_chunk_name,
            "step": step_name,
            "target_state": target_state,
            "unit_count": len(units_list),
            "units": [
                {"unit_id": r.get("unit_id", ""), "target_state": target_state}
                for r in units_list[:20]  # Cap for display
            ],
        })

    # Save updated manifest
    if chunks_created:
        save_manifest(run_dir, manifest)

    return {
        "missing_count": total_missing,
        "chunks_created": chunks_created,
        "run_dir": str(run_dir),
    }
