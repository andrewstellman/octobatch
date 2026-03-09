"""
run_tools.py - CLI tools for run verification, repair, and reporting.

Provides verify_run(), repair_run(), and generate_report() functions for
checking run integrity, creating retry chunks for missing units, and
generating detailed run reports. These address QUALITY.md Scenario 1
(Silent Attrition).
"""

import json
import os
import tempfile
from collections import Counter
from pathlib import Path

import yaml

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

    # Load config to detect fan-out steps
    fan_out_steps = set()
    config_path = run_dir / "config" / "config.yaml"
    if config_path.exists():
        try:
            config = yaml.safe_load(config_path.read_text())
            for step_def in config.get("pipeline", {}).get("steps", []):
                if step_def.get("scope") == "fan_out":
                    fan_out_steps.add(step_def["name"])
        except Exception:
            pass

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
        if step_name in fan_out_steps:
            # Fan-out steps produce child IDs, not matching parent IDs.
            # Collect all valid IDs from fan-out output as the new expected set.
            valid_ids = set()
            for chunk_name_iter in sorted(chunks.keys()):
                chunk_dir = run_dir / "chunks" / chunk_name_iter
                validated_file = chunk_dir / f"{step_name}_validated.jsonl"
                for record in load_jsonl(validated_file):
                    uid = record.get("unit_id")
                    if uid:
                        valid_ids.add(uid)
            step_report = {
                "step": step_name,
                "scope": "fan_out",
                "expected": len(expected_ids),
                "valid": len(valid_ids),
                "failed": 0,
                "missing": 0,
                "duplicated": 0,
                "orphaned": 0,
                "_valid_ids": valid_ids,
            }
            result["steps"].append(step_report)
            expected_ids = valid_ids
            continue

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

        # Scan validated file (supports both .jsonl and .jsonl.gz)
        validated_file = chunk_dir / f"{step_name}_validated.jsonl"
        for record in load_jsonl(validated_file):
            uid = record.get("unit_id")
            if uid:
                valid_count_with_dupes += 1
                valid_ids.add(uid)

        # Scan failures file
        failures_file = chunk_dir / f"{step_name}_failures.jsonl"
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


def _load_model_registry():
    """Load model pricing registry from models.yaml."""
    models_path = Path(__file__).parent / "providers" / "models.yaml"
    if models_path.exists():
        with open(models_path) as f:
            return yaml.safe_load(f)
    return {}


def _compute_cost(input_tokens, output_tokens, provider_name, model_name, is_realtime, registry):
    """Compute cost from token counts using model registry pricing."""
    defaults = registry.get("defaults", {})
    providers = registry.get("providers", {})
    provider_data = providers.get(provider_name, {})
    models = provider_data.get("models", {})
    model_data = models.get(model_name, {})

    input_rate = model_data.get("input_per_million", defaults.get("input_per_million", 1.0))
    output_rate = model_data.get("output_per_million", defaults.get("output_per_million", 2.0))

    cost = (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000

    # Batch mode gets 50% discount (standard across providers)
    if not is_realtime:
        cost *= 0.5

    return cost


def generate_report(run_dir: Path, failures_by: str | None = None) -> dict:
    """
    Generate a detailed run report with validation funnel, failure analysis,
    and cost summary.

    Args:
        run_dir: Path to the run directory
        failures_by: Optional field name to group failures by (e.g., 'strategy_name').
                     If None, defaults to grouping by item (unit_id prefix before __rep).

    Returns:
        Dict with 'text' key containing formatted report, or 'error' key on failure.
        Also includes structured data keys for programmatic use.
    """
    run_dir = Path(run_dir)
    try:
        manifest = load_manifest(run_dir)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return {"error": f"Cannot load MANIFEST.json from {run_dir}: {e}"}

    pipeline = manifest.get("pipeline", [])
    chunks = manifest.get("chunks", {})
    metadata = manifest.get("metadata", {})
    status = manifest.get("status", "unknown")

    if not pipeline:
        return {"error": "No pipeline steps found in manifest"}

    # Load model registry for cost calculation
    registry = _load_model_registry()
    provider_name = metadata.get("provider", "")
    model_name = metadata.get("model", "")
    mode = metadata.get("mode", "batch")
    is_realtime = mode == "realtime"
    pipeline_name = metadata.get("pipeline_name", run_dir.name)
    display_name = metadata.get("display_name", "")

    # Compute timing
    start_time = metadata.get("start_time", "")
    end_time = metadata.get("end_time", "")
    duration_str = _format_duration(start_time, end_time)

    # Get initial unit count
    initial_ids = set()
    for chunk_name in sorted(chunks.keys()):
        chunk_dir = run_dir / "chunks" / chunk_name
        units_file = chunk_dir / "units.jsonl"
        if units_file.exists():
            for record in load_jsonl(units_file):
                uid = record.get("unit_id")
                if uid:
                    initial_ids.add(uid)

    total_units = len(initial_ids)

    # Load config to detect fan-out steps
    fan_out_steps = set()
    config_path = run_dir / "config" / "config.yaml"
    if config_path.exists():
        try:
            run_config = yaml.safe_load(config_path.read_text())
            for step_def in run_config.get("pipeline", {}).get("steps", []):
                if step_def.get("scope") == "fan_out":
                    fan_out_steps.add(step_def["name"])
        except Exception:
            pass

    # Build validation funnel per step
    funnel = []
    expected_ids = initial_ids.copy()
    all_failures = {}  # step -> list of failure records

    for step_name in pipeline:
        valid_ids = set()
        failed_ids = set()
        failure_records = []

        for chunk_name in sorted(chunks.keys()):
            chunk_dir = run_dir / "chunks" / chunk_name

            validated_file = chunk_dir / f"{step_name}_validated.jsonl"
            for record in load_jsonl(validated_file):
                uid = record.get("unit_id")
                if uid:
                    valid_ids.add(uid)

            failures_file = chunk_dir / f"{step_name}_failures.jsonl"
            for record in load_jsonl(failures_file):
                uid = record.get("unit_id")
                if uid:
                    failed_ids.add(uid)
                    failure_records.append(record)

        valid_count = len(valid_ids)
        failed_count = len(failed_ids)

        if step_name in fan_out_steps:
            # Fan-out steps: show expansion boundary
            funnel.append({
                "step": step_name,
                "scope": "fan_out",
                "valid": valid_count,
                "failed": 0,
                "lost": 0,
                "total": len(expected_ids),
                "pass_pct": 0,
                "yield_pct": 0,
                "parent_count": len(expected_ids),
            })
            # After fan-out, total_units resets to child count for yield calculation
            total_units = valid_count
            expected_ids = valid_ids.copy()
        else:
            cumulative_lost = total_units - valid_count if total_units > 0 else 0
            pass_pct = (valid_count / len(expected_ids) * 100) if expected_ids else 0
            yield_pct = (valid_count / total_units * 100) if total_units > 0 else 0

            funnel.append({
                "step": step_name,
                "valid": valid_count,
                "failed": failed_count,
                "lost": cumulative_lost,
                "total": len(expected_ids),
                "pass_pct": pass_pct,
                "yield_pct": yield_pct,
            })

            if failure_records:
                all_failures[step_name] = failure_records

            expected_ids = valid_ids.copy()

    # Final surviving units
    surviving = len(expected_ids) if expected_ids else 0
    overall_yield = (surviving / total_units * 100) if total_units > 0 else 0

    # Failures by item or by custom field
    failures_by_group = {}
    group_names = set()
    failures_by_section_title = "FAILURES BY ITEM"
    if failures_by:
        failures_by_section_title = f"FAILURES BY {failures_by.upper()}"
    for step_name, records in all_failures.items():
        for record in records:
            if failures_by:
                group_val = record.get(failures_by)
                if group_val is None:
                    group_val = "(missing)"
                else:
                    group_val = str(group_val)
            else:
                uid = record.get("unit_id", "")
                group_val = uid.rsplit("__rep", 1)[0] if "__rep" in uid else uid
            group_names.add(group_val)
            key = (step_name, group_val)
            failures_by_group[key] = failures_by_group.get(key, 0) + 1

    # Top errors by step
    top_errors = {}
    for step_name, records in all_failures.items():
        error_counter = Counter()
        for record in records:
            # Look for error messages in various fields
            error_msg = (
                record.get("verification_details")
                or record.get("strategy_verification_details")
                or record.get("error")
                or record.get("validation_error")
                or ""
            )
            if isinstance(error_msg, dict):
                error_msg = json.dumps(error_msg)
            if error_msg:
                # Truncate long error messages
                if len(error_msg) > 100:
                    error_msg = error_msg[:97] + "..."
                error_counter[error_msg] += 1
        if error_counter:
            top_errors[step_name] = error_counter.most_common(3)

    # Token summary
    initial_in = metadata.get("initial_input_tokens", 0)
    initial_out = metadata.get("initial_output_tokens", 0)
    retry_in = metadata.get("retry_input_tokens", 0)
    retry_out = metadata.get("retry_output_tokens", 0)
    total_in = initial_in + retry_in
    total_out = initial_out + retry_out
    retry_total = retry_in + retry_out
    total_tokens = total_in + total_out
    retry_pct = (retry_total / total_tokens * 100) if total_tokens > 0 else 0

    # Cost calculation
    cost = _compute_cost(total_in, total_out, provider_name, model_name, is_realtime, registry)
    cost_per_unit = (cost / surviving) if surviving > 0 else 0

    # Post-processing files
    post_proc_files = []
    for f in sorted(run_dir.iterdir()) if run_dir.exists() else []:
        if f.name in ("strategy_comparison.txt", "outcome_distribution.csv",
                       "results.csv", "strategy_comparison.csv"):
            post_proc_files.append(f.name)

    # Format the text report
    lines = []
    sep = "=" * 78
    dash = "-" * 74

    header_name = display_name or str(run_dir)
    lines.append(sep)
    lines.append(f"  RUN: {header_name}")
    lines.append(f"  Pipeline: {pipeline_name}")
    lines.append(f"  Provider: {provider_name} / {model_name} ({mode})")
    lines.append(f"  Status: {status} | Chunks: {len(chunks)} | Duration: {duration_str}")
    lines.append(f"  Units: {total_units} started -> {surviving} survived ({overall_yield:.1f}% yield)")
    lines.append(f"  Cost: ${cost:.4f} (${cost_per_unit:.6f} per valid unit)")
    lines.append(sep)

    # Validation funnel
    lines.append("")
    lines.append("  VALIDATION FUNNEL")
    lines.append(f"  {dash}")
    lines.append(f"  {'Step':<26s}{'Valid':>6s}{'Failed':>8s}{'Lost':>7s}{'Total':>8s}{'Pass %':>8s}{'Yield':>8s}")
    lines.append(f"  {dash}")
    for entry in funnel:
        if entry.get("scope") == "fan_out":
            parent_count = entry.get("parent_count", 0)
            lines.append(
                f"  {entry['step']:<26s}"
                f"{entry['valid']:>6d} units created from {parent_count} parents"
            )
        else:
            lines.append(
                f"  {entry['step']:<26s}"
                f"{entry['valid']:>6d}"
                f"{entry['failed']:>8d}"
                f"{entry['lost']:>7d}"
                f"{entry['total']:>8d}"
                f"{entry['pass_pct']:>7.1f}%"
                f"{entry['yield_pct']:>7.1f}%"
            )
    lines.append(f"  {dash}")

    # Failures by group (item or custom field)
    if failures_by_group:
        sorted_groups = sorted(group_names)
        lines.append("")
        lines.append(f"  {failures_by_section_title}")
        lines.append(f"  {dash}")
        # Header
        group_header = f"  {'Step':<26s}"
        for grp in sorted_groups:
            group_header += f"{grp:>14s}"
        group_header += f"{'Total':>8s}"
        lines.append(group_header)
        lines.append(f"  {dash}")

        # Rows (only steps with failures)
        for step_name in pipeline:
            step_total = 0
            row = f"  {step_name:<26s}"
            has_failures = False
            for grp in sorted_groups:
                count = failures_by_group.get((step_name, grp), 0)
                step_total += count
                row += f"{count:>14d}"
                if count > 0:
                    has_failures = True
            row += f"{step_total:>8d}"
            if has_failures:
                lines.append(row)

        lines.append(f"  {dash}")

    # Top errors
    if top_errors:
        lines.append("")
        lines.append("  TOP ERRORS BY STEP")
        lines.append(f"  {dash}")
        for step_name, errors in top_errors.items():
            lines.append(f"  {step_name}:")
            for msg, count in errors:
                lines.append(f"    [{count:>4d}x] {msg}")
        lines.append(f"  {dash}")

    # Token summary
    lines.append("")
    lines.append("  TOKEN SUMMARY")
    lines.append(f"  {dash}")
    lines.append(f"  Input tokens:       {total_in:>12,}")
    lines.append(f"  Output tokens:      {total_out:>12,}")
    if retry_total > 0:
        lines.append(f"  Retry tokens:       {retry_total:>12,}  ({retry_pct:.1f}% overhead)")
    lines.append(f"  {dash}")

    # Post-processing
    if post_proc_files:
        lines.append("")
        lines.append("  POST-PROCESSING OUTPUT")
        lines.append(f"  {dash}")
        for fname in post_proc_files:
            lines.append(f"  {fname}")
        lines.append(f"  {dash}")

    text = "\n".join(lines)

    return {
        "text": text,
        "run_dir": str(run_dir),
        "pipeline_name": pipeline_name,
        "status": status,
        "total_units": total_units,
        "surviving_units": surviving,
        "yield_pct": overall_yield,
        "cost": cost,
        "cost_per_unit": cost_per_unit,
        "funnel": funnel,
        "token_summary": {
            "input": total_in,
            "output": total_out,
            "retry": retry_total,
        },
    }


def _format_duration(start_time: str, end_time: str) -> str:
    """Format duration between two ISO timestamps, or 'in progress' if no end time."""
    if not start_time:
        return "unknown"
    if not end_time:
        return "in progress"

    from datetime import datetime, timezone
    try:
        start = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        delta = end - start
        total_seconds = int(delta.total_seconds())
        if total_seconds < 0:
            return "unknown"
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"
    except (ValueError, TypeError):
        return "unknown"


def _resolve_run_dir(run_name: str) -> Path:
    """Resolve a short run name to a full path.

    If run_name starts with 'runs/', use as-is. Otherwise prepend 'runs/'.
    If the resulting path doesn't exist, search runs/ for a matching prefix.
    """
    path = Path(run_name)
    if path.exists():
        return path

    # Try prepending runs/
    runs_path = Path("runs") / run_name
    if runs_path.exists():
        return runs_path

    # Search runs/ for prefix match
    runs_dir = Path("runs")
    if runs_dir.exists():
        matches = [d for d in runs_dir.iterdir() if d.is_dir() and d.name.startswith(run_name)]
        if len(matches) == 1:
            return matches[0]

    return path  # Return as-is; caller will handle non-existence


def compare_runs(run_names: list[str]) -> dict:
    """
    Compare 2+ runs side by side.

    Args:
        run_names: List of run directory names or paths.

    Returns:
        Dict with 'text' key containing formatted comparison table,
        'markdown_path' with saved file path, or 'error' key on failure.
    """
    if len(run_names) < 2:
        return {"error": "At least 2 runs are required for comparison"}

    registry = _load_model_registry()
    run_data = []

    for run_name in run_names:
        run_dir = _resolve_run_dir(run_name)
        if not run_dir.exists():
            return {"error": f"Run directory not found: {run_name}"}

        try:
            manifest = load_manifest(run_dir)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            return {"error": f"Cannot load manifest from {run_name}: {e}"}

        metadata = manifest.get("metadata", {})
        pipeline = manifest.get("pipeline", [])
        chunks = manifest.get("chunks", {})
        provider = metadata.get("provider", "unknown")
        model = metadata.get("model", "unknown")
        mode = metadata.get("mode", "batch")
        is_realtime = mode == "realtime"
        display_name = metadata.get("display_name", "")

        # Count units
        initial_ids = set()
        for chunk_name in sorted(chunks.keys()):
            chunk_dir = run_dir / "chunks" / chunk_name
            units_file = chunk_dir / "units.jsonl"
            if units_file.exists():
                for record in load_jsonl(units_file):
                    uid = record.get("unit_id")
                    if uid:
                        initial_ids.add(uid)

        total_units = len(initial_ids)

        # Walk pipeline to find surviving units
        expected_ids = initial_ids.copy()
        for step_name in pipeline:
            valid_ids = set()
            for chunk_name in sorted(chunks.keys()):
                chunk_dir = run_dir / "chunks" / chunk_name
                validated_file = chunk_dir / f"{step_name}_validated.jsonl"
                for record in load_jsonl(validated_file):
                    uid = record.get("unit_id")
                    if uid:
                        valid_ids.add(uid)
            expected_ids = valid_ids

        surviving = len(expected_ids)
        pass_rate = (surviving / total_units * 100) if total_units > 0 else 0

        # Token counts and cost
        initial_in = metadata.get("initial_input_tokens", 0)
        initial_out = metadata.get("initial_output_tokens", 0)
        retry_in = metadata.get("retry_input_tokens", 0)
        retry_out = metadata.get("retry_output_tokens", 0)
        total_in = initial_in + retry_in
        total_out = initial_out + retry_out
        cost = _compute_cost(total_in, total_out, provider, model, is_realtime, registry)
        cost_per_unit = (cost / surviving) if surviving > 0 else 0

        # Read post-processing output (strategy_comparison.txt)
        strategy_data = {}
        strat_file = run_dir / "strategy_comparison.txt"
        if strat_file.exists():
            strategy_data = _parse_strategy_comparison(strat_file)

        run_info = {
            "name": display_name or run_dir.name,
            "run_dir": str(run_dir),
            "provider": provider,
            "model": model,
            "mode": mode,
            "total_units": total_units,
            "valid_units": surviving,
            "pass_rate": pass_rate,
            "cost": cost,
            "cost_per_unit": cost_per_unit,
            "strategy_data": strategy_data,
        }
        run_data.append(run_info)

    # Build comparison table
    col_width = max(18, max(len(r["name"]) + 2 for r in run_data))
    label_width = 20

    lines = []
    lines.append("Cross-Run Comparison")
    lines.append("=" * 20)
    lines.append("")

    # Header
    header = f"{'':>{label_width}}"
    for r in run_data:
        header += f"| {r['name']:<{col_width - 2}s}"
    lines.append(header)

    sep = f"{'-' * label_width}"
    for _ in run_data:
        sep += f"|{'-' * (col_width - 1)}"
    lines.append(sep)

    # Rows
    def add_row(label, values):
        row = f"{label:<{label_width}s}"
        for v in values:
            row += f"| {v:<{col_width - 2}s}"
        lines.append(row)

    add_row("Provider/Model", [f"{r['provider']}/{r['model'][:col_width-len(r['provider'])-3]}" for r in run_data])
    add_row("Mode", [r["mode"] for r in run_data])
    add_row("Total Units", [str(r["total_units"]) for r in run_data])
    add_row("Valid Units", [str(r["valid_units"]) for r in run_data])
    add_row("Pass Rate", [f"{r['pass_rate']:.1f}%" for r in run_data])
    add_row("Cost", [f"${r['cost']:.4f}" for r in run_data])
    add_row("Cost/Valid Unit", [f"${r['cost_per_unit']:.6f}" for r in run_data])

    # Strategy data (if any runs have it)
    all_strategies = set()
    for r in run_data:
        all_strategies.update(r["strategy_data"].keys())

    if all_strategies:
        lines.append(sep)
        for strat in sorted(all_strategies):
            values = []
            for r in run_data:
                net = r["strategy_data"].get(strat, {}).get("net")
                if net is not None:
                    sign = "+" if net > 0 else ""
                    values.append(f"{sign}{net}")
                else:
                    values.append("—")
            add_row(f"{strat} Net", values)

    text = "\n".join(lines)

    # Save markdown file
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    md_path = Path(f"comparison_{ts}.md")
    md_content = f"# {lines[0]}\n\n```\n{text}\n```\n"
    md_path.write_text(md_content)

    return {
        "text": text,
        "runs": run_data,
        "markdown_path": str(md_path),
    }


def _parse_strategy_comparison(path: Path) -> dict:
    """Parse a strategy_comparison.txt file into a dict of {strategy: {net: int, ...}}."""
    result = {}
    try:
        lines = path.read_text().strip().split("\n")
        for line in lines:
            if "|" not in line or line.startswith("-") or line.startswith("Group"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 6:
                continue
            name = parts[0]
            if not name or name in ("Group", "Total:"):
                continue
            try:
                net = int(parts[5])
                result[name] = {"net": net}
            except (ValueError, IndexError):
                continue
    except Exception:
        pass
    return result


def compare_hands(run_dir1: Path, run_dir2: Path,
                  unit_id: str | None = None,
                  sample: int | None = None,
                  step: str = "play_hand") -> dict:
    """
    Compare how two runs played the same dealt hands.

    Given two runs with identical seeds, matches units by unit_id and
    compares outcomes from the specified LLM step.

    Args:
        run_dir1: First run directory
        run_dir2: Second run directory
        unit_id: If set, show detailed comparison for this specific unit
        sample: If set, show N random divergent pairs
        step: Pipeline step to compare (default: play_hand)

    Returns:
        Dict with 'text' key containing formatted comparison, or 'error'.
    """
    run_dir1 = Path(run_dir1)
    run_dir2 = Path(run_dir2)

    for rd in [run_dir1, run_dir2]:
        if not rd.exists():
            return {"error": f"Run directory not found: {rd}"}

    # Load validated records from the specified step
    records1 = _load_step_records(run_dir1, step)
    records2 = _load_step_records(run_dir2, step)

    if not records1:
        return {"error": f"No validated records found for step '{step}' in {run_dir1}"}
    if not records2:
        return {"error": f"No validated records found for step '{step}' in {run_dir2}"}

    # Load manifests for display names
    try:
        m1 = load_manifest(run_dir1)
        m2 = load_manifest(run_dir2)
    except Exception as e:
        return {"error": f"Cannot load manifests: {e}"}

    name1 = m1.get("metadata", {}).get("display_name", "") or run_dir1.name
    name2 = m2.get("metadata", {}).get("display_name", "") or run_dir2.name
    model1 = m1.get("metadata", {}).get("model", "unknown")
    model2 = m2.get("metadata", {}).get("model", "unknown")

    # Match by unit_id
    by_id1 = {r.get("unit_id"): r for r in records1 if r.get("unit_id")}
    by_id2 = {r.get("unit_id"): r for r in records2 if r.get("unit_id")}

    common_ids = sorted(set(by_id1.keys()) & set(by_id2.keys()))
    only_in_1 = sorted(set(by_id1.keys()) - set(by_id2.keys()))
    only_in_2 = sorted(set(by_id2.keys()) - set(by_id1.keys()))

    if not common_ids:
        return {"error": "No common unit IDs found between runs"}

    # If single unit_id requested, show detailed comparison
    if unit_id:
        if unit_id not in by_id1 or unit_id not in by_id2:
            return {"error": f"Unit ID '{unit_id}' not found in both runs"}
        r1 = by_id1[unit_id]
        r2 = by_id2[unit_id]
        return _format_single_hand_diff(unit_id, r1, r2, name1, name2, model1, model2)

    # Compare all common units
    compare_fields = ["result", "player_final_total", "dealer_final_total",
                      "first_action", "player_busted", "dealer_busted"]
    divergent = []
    identical = []

    for uid in common_ids:
        r1 = by_id1[uid]
        r2 = by_id2[uid]
        diffs = {}
        for field in compare_fields:
            v1 = r1.get(field)
            v2 = r2.get(field)
            if v1 != v2:
                diffs[field] = (v1, v2)
        if diffs:
            divergent.append({"unit_id": uid, "diffs": diffs})
        else:
            identical.append(uid)

    # Build output
    lines = []
    lines.append(f"Hand-by-Hand Comparison: {step}")
    lines.append("=" * 60)
    lines.append(f"  Run 1: {name1} ({model1})")
    lines.append(f"  Run 2: {name2} ({model2})")
    lines.append(f"  Common units: {len(common_ids)}")
    lines.append(f"  Identical: {len(identical)}")
    lines.append(f"  Divergent: {len(divergent)}")
    if only_in_1:
        lines.append(f"  Only in Run 1: {len(only_in_1)}")
    if only_in_2:
        lines.append(f"  Only in Run 2: {len(only_in_2)}")
    lines.append("")

    # Show divergent pairs
    show_list = divergent
    if sample and sample < len(divergent):
        import random
        show_list = random.sample(divergent, sample)

    if show_list:
        lines.append("DIVERGENT HANDS")
        lines.append("-" * 60)
        for entry in show_list:
            uid = entry["unit_id"]
            lines.append(f"  {uid}:")
            for field, (v1, v2) in entry["diffs"].items():
                lines.append(f"    {field}: {v1} vs {v2}")
        lines.append("")

    text = "\n".join(lines)

    return {
        "text": text,
        "common_count": len(common_ids),
        "identical_count": len(identical),
        "divergent_count": len(divergent),
        "divergent": divergent[:100],  # Cap for programmatic use
        "only_in_run1": only_in_1[:50],
        "only_in_run2": only_in_2[:50],
    }


def _load_step_records(run_dir: Path, step_name: str) -> list[dict]:
    """Load all validated records for a given step from all chunks."""
    try:
        manifest = load_manifest(run_dir)
    except Exception:
        return []

    chunks = manifest.get("chunks", {})
    records = []
    for chunk_name in sorted(chunks.keys()):
        chunk_dir = run_dir / "chunks" / chunk_name
        validated_file = chunk_dir / f"{step_name}_validated.jsonl"
        for record in load_jsonl(validated_file):
            records.append(record)
    return records


def _format_single_hand_diff(unit_id, r1, r2, name1, name2, model1, model2):
    """Format a detailed comparison of a single unit across two runs."""
    lines = []
    lines.append(f"Detailed Comparison: {unit_id}")
    lines.append("=" * 60)
    lines.append(f"  Run 1: {name1} ({model1})")
    lines.append(f"  Run 2: {name2} ({model2})")
    lines.append("")

    # Get all fields from both records
    all_fields = sorted(set(list(r1.keys()) + list(r2.keys())))

    lines.append(f"  {'Field':<30s}{'Run 1':<25s}{'Run 2':<25s}{'Match':<6s}")
    lines.append(f"  {'-' * 86}")

    for field in all_fields:
        v1 = r1.get(field)
        v2 = r2.get(field)
        v1_str = str(v1)[:22] if v1 is not None else "—"
        v2_str = str(v2)[:22] if v2 is not None else "—"
        match = "✓" if v1 == v2 else "✗"
        lines.append(f"  {field:<30s}{v1_str:<25s}{v2_str:<25s}{match}")

    text = "\n".join(lines)
    return {"text": text, "unit_id": unit_id, "run1": r1, "run2": r2}
