#!/usr/bin/env python3
"""
diagnostics.py - Failure diagnostic export for Octobatch pipelines.

Generates a comprehensive Markdown report of pipeline failures including:
- Run overview and stats
- Error summary grouped by message
- Sample failures with rendered prompts
- Config snapshot (config.yaml, templates, schemas)
"""

import gzip
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


def _open_jsonl_for_read(path: Path):
    """Open a JSONL file for reading, handling both plain and gzipped formats."""
    if path.suffix == '.gz':
        return gzip.open(path, 'rt', encoding='utf-8', errors='replace')
    return open(path, 'r', encoding='utf-8', errors='replace')


class DiagnosticExporter:
    """Export diagnostic reports for failed pipeline runs."""

    def generate_report(self, run_dir: Path) -> str:
        """
        Generate a comprehensive diagnostic report for a pipeline run.

        Args:
            run_dir: Path to the run directory

        Returns:
            Formatted Markdown string with diagnostic information
        """
        run_dir = Path(run_dir)

        # Load manifest
        manifest = self._load_manifest(run_dir)
        if not manifest:
            return "# Error\n\nCould not load MANIFEST.json"

        # Load all failures
        failures = self._load_all_failures(run_dir)
        if not failures:
            return "# Pipeline Diagnostic\n\nNo failures found in this run."

        # Build prompt index for lookups
        prompt_index = self._build_prompt_index(run_dir)

        # Group failures by error message
        error_groups = self._group_failures_by_error(failures)

        # Determine the failed step (from first failure)
        failed_step = self._get_failed_step(failures)

        # Build the report
        sections = []

        # Header
        sections.append("# Pipeline Failure Diagnostic\n")

        # Run Overview
        sections.append(self._generate_overview(run_dir, manifest, failures))

        # Error Summary
        sections.append(self._generate_error_summary(error_groups))

        # Sample Failures (top 3 error types, 1 sample each)
        sections.append(self._generate_sample_failures(error_groups, prompt_index))

        # Config Snapshot
        sections.append(self._generate_config_snapshot(run_dir, failed_step))

        return "\n".join(sections)

    def _load_manifest(self, run_dir: Path) -> dict | None:
        """Load MANIFEST.json from run directory."""
        manifest_path = run_dir / "MANIFEST.json"
        if not manifest_path.exists():
            return None
        try:
            with open(manifest_path, "r", encoding="utf-8", errors="replace") as f:
                return json.load(f)
        except Exception:
            return None

    def _load_all_failures(self, run_dir: Path) -> list[dict]:
        """Load all failures from all chunks."""
        failures = []
        chunks_dir = run_dir / "chunks"
        if not chunks_dir.exists():
            return failures

        for chunk_dir in sorted(chunks_dir.iterdir()):
            if not chunk_dir.is_dir():
                continue
            # Check both plain and gzipped failure files
            failure_files = list(chunk_dir.glob("*_failures.jsonl")) + list(chunk_dir.glob("*_failures.jsonl.gz"))
            for failure_file in failure_files:
                try:
                    with _open_jsonl_for_read(failure_file) as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                try:
                                    failure = json.loads(line)
                                    # Track which chunk/file it came from
                                    failure["_source_chunk"] = chunk_dir.name
                                    failure["_source_file"] = failure_file.name
                                    failures.append(failure)
                                except json.JSONDecodeError:
                                    pass
                except Exception:
                    pass
        return failures

    def _build_prompt_index(self, run_dir: Path) -> dict[str, dict]:
        """Build an index of unit_id -> prompt data from all prompts files."""
        index = {}
        chunks_dir = run_dir / "chunks"
        if not chunks_dir.exists():
            return index

        for chunk_dir in sorted(chunks_dir.iterdir()):
            if not chunk_dir.is_dir():
                continue
            for prompts_file in chunk_dir.glob("*_prompts.jsonl"):
                try:
                    with open(prompts_file, "r", encoding="utf-8", errors="replace") as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                try:
                                    prompt_data = json.loads(line)
                                    unit_id = prompt_data.get("unit_id")
                                    if unit_id:
                                        index[unit_id] = prompt_data
                                except json.JSONDecodeError:
                                    pass
                except Exception:
                    pass
        return index

    def _group_failures_by_error(self, failures: list[dict]) -> dict[str, list[dict]]:
        """Group failures by their primary error message."""
        groups: dict[str, list[dict]] = {}

        for failure in failures:
            # Get primary error message
            errors = failure.get("errors", [])
            if errors and isinstance(errors, list):
                # Use the first error's message as the grouping key
                first_error = errors[0]
                if isinstance(first_error, dict):
                    error_msg = first_error.get("message", "Unknown error")
                else:
                    error_msg = str(first_error)
            else:
                error_msg = "Unknown error"

            if error_msg not in groups:
                groups[error_msg] = []
            groups[error_msg].append(failure)

        return groups

    def _get_failed_step(self, failures: list[dict]) -> str | None:
        """Get the step name from failures."""
        if not failures:
            return None
        # Try to get from source file name (e.g., "generate_failures.jsonl" or "generate_failures.jsonl.gz")
        source_file = failures[0].get("_source_file", "")
        if source_file.endswith("_failures.jsonl.gz"):
            return source_file.replace("_failures.jsonl.gz", "")
        if source_file.endswith("_failures.jsonl"):
            return source_file.replace("_failures.jsonl", "")
        return None

    def _generate_overview(self, run_dir: Path, manifest: dict, failures: list[dict]) -> str:
        """Generate the Run Overview section."""
        metadata = manifest.get("metadata", {})
        chunks = manifest.get("chunks", {})

        # Calculate totals from chunks
        total_items = sum(c.get("items", 0) for c in chunks.values())
        total_valid = sum(c.get("valid", 0) for c in chunks.values())
        total_failed = sum(c.get("failed", 0) for c in chunks.values())

        # Use actual failure count if available
        if failures:
            total_failed = len(failures)

        # Calculate success rate
        if total_items > 0:
            success_rate = (total_valid / total_items) * 100
        else:
            success_rate = 0.0

        # Parse timestamp
        start_time = metadata.get("start_time", manifest.get("created", "Unknown"))
        if start_time != "Unknown":
            try:
                dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                start_time = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

        lines = [
            "## Run Overview",
            f"- **Pipeline:** {metadata.get('pipeline_name', 'Unknown')}",
            f"- **Run ID:** {run_dir.name}",
            f"- **Mode:** {metadata.get('mode', 'Unknown')}",
            f"- **Started:** {start_time}",
            f"- **Status:** {total_valid} valid, {total_failed} failed ({success_rate:.1f}% success)",
            "",
        ]

        # Add max_units if present (backwards compat: also check "limit")
        max_units = metadata.get("max_units") or metadata.get("limit")
        if max_units:
            lines.insert(5, f"- **Max Units:** {max_units}")

        return "\n".join(lines)

    def _generate_error_summary(self, error_groups: dict[str, list[dict]]) -> str:
        """Generate the Error Summary table."""
        lines = [
            "## Error Summary",
            "",
            "| Count | Error |",
            "|-------|-------|",
        ]

        # Sort by count descending
        sorted_errors = sorted(error_groups.items(), key=lambda x: len(x[1]), reverse=True)

        for error_msg, failures in sorted_errors:
            # Escape pipe characters in error message
            safe_msg = error_msg.replace("|", "\\|")
            lines.append(f"| {len(failures)} | {safe_msg} |")

        lines.append("")
        return "\n".join(lines)

    def _generate_sample_failures(
        self, error_groups: dict[str, list[dict]], prompt_index: dict[str, dict]
    ) -> str:
        """Generate Sample Failures section with top 3 error types."""
        lines = ["## Sample Failures", ""]

        # Sort by count descending, take top 3
        sorted_errors = sorted(error_groups.items(), key=lambda x: len(x[1]), reverse=True)[:3]

        for error_msg, failures in sorted_errors:
            # Take first failure as sample
            sample = failures[0]
            unit_id = sample.get("unit_id", "Unknown")

            lines.append(f"### Error: {error_msg}")
            lines.append("")
            lines.append(f"**Unit:** {unit_id}")
            lines.append("")

            # Validation Errors
            errors = sample.get("errors", [])
            if errors:
                lines.append("**Validation Errors:**")
                for err in errors:
                    if isinstance(err, dict):
                        path = err.get("path", [])
                        path_str = ".".join(str(p) for p in path) if path else "$"
                        msg = err.get("message", "Unknown")
                        lines.append(f"- Path: `{path_str}`")
                        lines.append(f"- Message: {msg}")
                    else:
                        lines.append(f"- {err}")
                lines.append("")

            # Raw LLM Response
            raw_response = sample.get("raw_response")
            if raw_response:
                lines.append("**Raw LLM Response:**")
                lines.append("```json")
                try:
                    # Pretty print the JSON, but exclude _metadata for readability
                    display_response = {k: v for k, v in raw_response.items() if k != "_metadata"}
                    lines.append(json.dumps(display_response, indent=2))
                except Exception:
                    lines.append(str(raw_response))
                lines.append("```")
                lines.append("")

            # Rendered Prompt
            prompt_data = prompt_index.get(unit_id)
            if prompt_data:
                prompt_text = prompt_data.get("prompt", "")
                if prompt_text:
                    lines.append("**Rendered Prompt:**")
                    lines.append("```")
                    lines.append(prompt_text.strip())
                    lines.append("```")
                    lines.append("")

        return "\n".join(lines)

    def _generate_config_snapshot(self, run_dir: Path, failed_step: str | None) -> str:
        """Generate Config Snapshot section."""
        lines = ["## Config Snapshot", ""]
        config_dir = run_dir / "config"

        # config.yaml
        config_file = config_dir / "config.yaml"
        if config_file.exists():
            lines.append("### config.yaml")
            lines.append("```yaml")
            try:
                with open(config_file, "r", encoding="utf-8", errors="replace") as f:
                    lines.append(f.read().strip())
            except Exception as e:
                lines.append(f"# Error reading config: {e}")
            lines.append("```")
            lines.append("")

        # Load config to get template and schema paths
        config = {}
        if config_file.exists():
            try:
                import yaml
                with open(config_file, "r", encoding="utf-8", errors="replace") as f:
                    config = yaml.safe_load(f) or {}
            except Exception:
                pass

        # Template for failed step
        if failed_step:
            templates_config = config.get("prompts", {})
            template_dir = templates_config.get("template_dir", "templates")
            templates = templates_config.get("templates", {})
            template_file = templates.get(failed_step)

            if template_file:
                template_path = config_dir / template_dir / template_file
                if template_path.exists():
                    lines.append(f"### Template: {template_file}")
                    lines.append("```jinja2")
                    try:
                        with open(template_path, "r", encoding="utf-8", errors="replace") as f:
                            lines.append(f.read().strip())
                    except Exception as e:
                        lines.append(f"# Error reading template: {e}")
                    lines.append("```")
                    lines.append("")

            # Schema for failed step
            schemas_config = config.get("schemas", {})
            schema_dir = schemas_config.get("schema_dir", "schemas")
            schema_files = schemas_config.get("files", {})
            schema_file = schema_files.get(failed_step)

            if schema_file:
                schema_path = config_dir / schema_dir / schema_file
                if schema_path.exists():
                    lines.append(f"### Schema: {schema_file}")
                    lines.append("```json")
                    try:
                        with open(schema_path, "r", encoding="utf-8", errors="replace") as f:
                            lines.append(f.read().strip())
                    except Exception as e:
                        lines.append(f"# Error reading schema: {e}")
                    lines.append("```")
                    lines.append("")

        return "\n".join(lines)


# Convenience function for command-line usage
def generate_diagnostic(run_dir: str | Path) -> str:
    """Generate a diagnostic report for the given run directory."""
    return DiagnosticExporter().generate_report(Path(run_dir))


def scan_step_health(run_dir: Path, pipeline: list[str]) -> list[dict]:
    """
    Scan disk files to build per-step health data.

    For each step, counts validated and failure records from actual disk files
    (not manifest). Returns a list of dicts suitable for the DiagnosticsScreen.

    Args:
        run_dir: Path to the run directory
        pipeline: List of step names from manifest

    Returns:
        List of dicts with keys: step, expected, valid, validation_failures,
        hard_failures, status
    """
    chunks_dir = run_dir / "chunks"
    if not chunks_dir.exists():
        return []

    validation_stages = {"schema_validation", "validation"}

    # First pass: count per-step valid and failures from disk
    step_data = []
    prev_valid = None

    for step_name in pipeline:
        valid_count = 0
        validation_fail_count = 0
        hard_fail_count = 0

        for chunk_dir in sorted(chunks_dir.iterdir()):
            if not chunk_dir.is_dir():
                continue

            # Count validated (check both plain and gzipped)
            validated_file = chunk_dir / f"{step_name}_validated.jsonl"
            validated_file_gz = chunk_dir / f"{step_name}_validated.jsonl.gz"
            vf = validated_file if validated_file.exists() else (validated_file_gz if validated_file_gz.exists() else None)
            if vf:
                try:
                    with _open_jsonl_for_read(vf) as f:
                        for line in f:
                            if line.strip():
                                valid_count += 1
                except Exception:
                    pass

            # Count failures by category (check both plain and gzipped)
            failures_file = chunk_dir / f"{step_name}_failures.jsonl"
            failures_file_gz = chunk_dir / f"{step_name}_failures.jsonl.gz"
            ff = failures_file if failures_file.exists() else (failures_file_gz if failures_file_gz.exists() else None)
            if ff:
                try:
                    with _open_jsonl_for_read(ff) as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                failure = json.loads(line)
                                stage = failure.get("failure_stage", "validation")
                                if stage in validation_stages:
                                    validation_fail_count += 1
                                else:
                                    hard_fail_count += 1
                            except json.JSONDecodeError:
                                pass
                except Exception:
                    pass

        # Expected = previous step's valid count (or total_units for step 1)
        if prev_valid is not None:
            expected = prev_valid
        else:
            # First step: expected = total items from manifest or sum of chunk inputs
            expected = 0
            for chunk_dir in sorted(chunks_dir.iterdir()):
                if not chunk_dir.is_dir():
                    continue
                # Count from units.jsonl (first step input)
                units_file = chunk_dir / "units.jsonl"
                if units_file.exists():
                    try:
                        with open(units_file, 'r') as f:
                            for line in f:
                                if line.strip():
                                    expected += 1
                    except Exception:
                        pass

        # Determine status indicator
        total_accounted = valid_count + validation_fail_count + hard_fail_count
        if expected > 0 and total_accounted == expected and validation_fail_count == 0 and hard_fail_count == 0:
            status = "ok"  # checkmark
        elif validation_fail_count > 0 or hard_fail_count > 0:
            status = "warning"  # has failures
        elif expected > 0 and total_accounted != expected:
            status = "mismatch"  # counts don't add up
        else:
            status = "ok"

        step_data.append({
            "step": step_name,
            "expected": expected,
            "valid": valid_count,
            "validation_failures": validation_fail_count,
            "hard_failures": hard_fail_count,
            "status": status,
        })

        prev_valid = valid_count

    return step_data


def verify_disk_vs_manifest(run_dir: Path, pipeline: list[str], manifest: dict) -> list[str]:
    """
    Compare disk file counts against manifest counts.

    Returns list of discrepancy descriptions (empty if all match).
    """
    chunks_dir = run_dir / "chunks"
    if not chunks_dir.exists():
        return []

    discrepancies = []
    manifest_chunks = manifest.get("chunks", {})

    for chunk_name, chunk_data in manifest_chunks.items():
        chunk_dir = chunks_dir / chunk_name
        if not chunk_dir.exists():
            discrepancies.append(f"{chunk_name}: directory missing from disk")
            continue

        manifest_valid = chunk_data.get("valid", 0)
        manifest_failed = chunk_data.get("failed", 0)

        # Count the last step's validated and failures from disk
        last_step = pipeline[-1] if pipeline else None
        if not last_step:
            continue

        disk_valid = 0
        validated_file = chunk_dir / f"{last_step}_validated.jsonl"
        validated_file_gz = chunk_dir / f"{last_step}_validated.jsonl.gz"
        vf = validated_file if validated_file.exists() else (validated_file_gz if validated_file_gz.exists() else None)
        if vf:
            try:
                with _open_jsonl_for_read(vf) as f:
                    disk_valid = sum(1 for line in f if line.strip())
            except Exception:
                pass

        disk_failed = 0
        failures_file = chunk_dir / f"{last_step}_failures.jsonl"
        failures_file_gz = chunk_dir / f"{last_step}_failures.jsonl.gz"
        ff = failures_file if failures_file.exists() else (failures_file_gz if failures_file_gz.exists() else None)
        if ff:
            try:
                with _open_jsonl_for_read(ff) as f:
                    disk_failed = sum(1 for line in f if line.strip())
            except Exception:
                pass

        if manifest_valid != disk_valid:
            discrepancies.append(
                f"{chunk_name}/{last_step}: manifest valid={manifest_valid}, disk valid={disk_valid}"
            )
        if manifest_failed != disk_failed:
            discrepancies.append(
                f"{chunk_name}/{last_step}: manifest failed={manifest_failed}, disk failed={disk_failed}"
            )

    return discrepancies


def get_step_failure_analysis(run_dir: Path, step_name: str) -> dict:
    """
    Analyze failures for a specific step — groups by category and error message.

    Returns:
        {
            "groups": [{"category": str, "error": str, "count": int}, ...],
            "samples": [{"unit_id": str, "stage": str, "error": str, "raw_response": str}, ...],
            "total": int
        }
    """
    chunks_dir = run_dir / "chunks"
    if not chunks_dir.exists():
        return {"groups": [], "samples": [], "total": 0}

    all_failures = []
    for chunk_dir in sorted(chunks_dir.iterdir()):
        if not chunk_dir.is_dir():
            continue
        failures_file = chunk_dir / f"{step_name}_failures.jsonl"
        failures_file_gz = chunk_dir / f"{step_name}_failures.jsonl.gz"
        ff = failures_file if failures_file.exists() else (failures_file_gz if failures_file_gz.exists() else None)
        if not ff:
            continue
        try:
            with _open_jsonl_for_read(ff) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            all_failures.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except Exception:
            pass

    if not all_failures:
        return {"groups": [], "samples": [], "total": 0}

    # Group by category + error message
    group_counter: Counter = Counter()
    for failure in all_failures:
        stage = failure.get("failure_stage", "validation")
        errors = failure.get("errors", [])
        if errors and isinstance(errors, list):
            first_error = errors[0]
            if isinstance(first_error, dict):
                error_msg = first_error.get("message", "Unknown error")
            else:
                error_msg = str(first_error)
        else:
            error_msg = "Unknown error"
        group_counter[(stage, error_msg)] += 1

    groups = [
        {"category": stage, "error": msg, "count": count}
        for (stage, msg), count in group_counter.most_common()
    ]

    # Sample failures (up to 3)
    samples = []
    for failure in all_failures[:3]:
        stage = failure.get("failure_stage", "validation")
        errors = failure.get("errors", [])
        if errors and isinstance(errors, list):
            first_error = errors[0]
            if isinstance(first_error, dict):
                error_msg = first_error.get("message", "Unknown error")
            else:
                error_msg = str(first_error)
        else:
            error_msg = "Unknown error"

        raw_response = failure.get("raw_response", "")
        if isinstance(raw_response, dict):
            raw_response = json.dumps(raw_response, indent=2)
        raw_str = str(raw_response)
        if len(raw_str) > 200:
            raw_str = raw_str[:200] + "..."

        samples.append({
            "unit_id": failure.get("unit_id", "Unknown"),
            "stage": stage,
            "error": error_msg,
            "raw_response": raw_str,
        })

    return {"groups": groups, "samples": samples, "total": len(all_failures)}
