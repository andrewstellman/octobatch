"""
Run discovery and status utilities.

Scans the runs/ directory for valid batch processing runs.
Includes process discovery for tracking orchestrator processes.
"""

import gzip
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional


def _find_jsonl_file(base_path: Path) -> Path | None:
    """Find a JSONL file, checking for gzipped version if plain doesn't exist."""
    if base_path.exists():
        return base_path
    gz_path = Path(str(base_path) + '.gz')
    if gz_path.exists():
        return gz_path
    return None


def _open_jsonl_for_read(path: Path):
    """Open a JSONL file for reading, handling both plain and gzipped formats."""
    if path.suffix == '.gz':
        return gzip.open(path, 'rt', encoding='utf-8')
    return open(path, 'r', encoding='utf-8')

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


def get_runs_dir() -> Path:
    """
    Get the runs directory path.

    Returns the runs/ directory relative to the project root.
    """
    # This file is at scripts/tui/utils/runs.py
    # Project root is parent of scripts/
    project_root = Path(__file__).parent.parent.parent.parent
    return project_root / "runs"


def load_manifest(run_path: Path) -> Optional[Dict[str, Any]]:
    """Load MANIFEST.json from a run directory."""
    manifest_path = run_path / "MANIFEST.json"
    if not manifest_path.exists():
        return None
    try:
        with open(manifest_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def check_manifest_consistency(run_path: Path, manifest: Dict[str, Any]) -> bool:
    """Check if all chunks are terminal but status is not 'complete', and auto-correct.

    A chunk is terminal if its state is VALIDATED or FAILED.
    If all chunks are terminal but the manifest status isn't 'complete',
    this corrects the status and writes the fix to disk.

    Args:
        run_path: Path to run directory
        manifest: The loaded manifest dict (will be mutated if corrected)

    Returns:
        True if the manifest was corrected, False if no change needed.
    """
    chunks = manifest.get("chunks", {})
    if not chunks:
        return False

    status = manifest.get("status", "")
    if status == "complete":
        return False

    # Check if every chunk is in a terminal state
    for chunk_data in chunks.values():
        state = chunk_data.get("state", "")
        if state not in ("VALIDATED", "FAILED"):
            return False

    # All chunks are terminal but status != complete — auto-correct
    manifest["status"] = "complete"
    manifest["completed_at"] = datetime.now().isoformat()

    manifest_path = run_path / "MANIFEST.json"
    try:
        tmp_path = manifest_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(manifest, indent=2))
        tmp_path.rename(manifest_path)
    except Exception:
        return False

    # Best-effort log entry
    log_file = run_path / "RUN_LOG.txt"
    try:
        with open(log_file, "a") as f:
            ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
            f.write(f"[{ts}] [AUTO-FIX] Run status corrected to complete (all chunks terminal)\n")
    except Exception:
        pass

    return True


def get_run_status(manifest: Dict[str, Any]) -> str:
    """
    Determine run status from manifest.

    First checks the manifest's "status" field (set by orchestrator crash handling).
    Falls back to inferring status from chunk states.

    Returns:
        "complete" - Run finished successfully
        "failed" - Run crashed or has permanent failures
        "paused" - Run was interrupted by user
        "detached" - Run appears active but orchestrator may not be running
        "active" - Run is in progress (if TUI has subprocess tracking)
        "pending" - Not started or needs retry
    """
    # Check explicit status field first (set by orchestrator)
    explicit_status = manifest.get("status")
    if explicit_status in ("complete", "failed", "paused", "killed"):
        return explicit_status

    # Infer status from chunk states
    chunks = manifest.get("chunks", {})
    if not chunks:
        return "pending"

    total = len(chunks)
    validated = sum(1 for c in chunks.values() if c.get("state") == "VALIDATED")
    failed = sum(1 for c in chunks.values() if c.get("state") == "FAILED")
    pending = sum(1 for c in chunks.values() if c.get("state") == "PENDING")

    if validated == total:
        return "complete"
    elif failed > 0:
        return "failed"
    elif pending == total:
        return "pending"
    else:
        # Run is not complete but not pending either
        # If explicit status is "running", it's actively running
        # Otherwise, it's detached (orchestrator may have died)
        if explicit_status == "running":
            return "active"
        else:
            # No explicit status and not terminal - likely detached
            return "detached"


def get_run_progress(manifest: Dict[str, Any]) -> int:
    """Calculate progress percentage (0-100) from manifest.

    Uses step-level granularity: for a 2-step pipeline, completing step 1
    on all chunks yields 50% progress (not 0%).

    Terminal runs return 100 (complete) or the actual progress (failed).
    """
    status = manifest.get("status", "")
    if status == "complete":
        return 100

    chunks = manifest.get("chunks", {})
    if not chunks:
        return 0

    pipeline = manifest.get("pipeline", [])
    total_steps = len(pipeline) if pipeline else 1
    total_chunks = len(chunks)

    completed_steps = 0
    for chunk_data in chunks.values():
        state = chunk_data.get("state", "")
        if state == "VALIDATED":
            completed_steps += total_steps
        elif state in ("FAILED", "PENDING", ""):
            pass  # 0 completed steps
        elif "_" in state:
            # State like "score_SUBMITTED" — steps before this one are done
            step_name = state.rsplit("_", 1)[0]
            if step_name in pipeline:
                completed_steps += pipeline.index(step_name)

    total_work = total_chunks * total_steps
    return int((completed_steps / total_work) * 100) if total_work > 0 else 0


def get_run_tokens(manifest: Dict[str, Any]) -> int:
    """Get total tokens from manifest.

    Reads from metadata fields: initial_input_tokens, initial_output_tokens,
    retry_input_tokens, retry_output_tokens.
    """
    metadata = manifest.get("metadata", {})

    # Sum all token counts from metadata
    initial_input = metadata.get("initial_input_tokens", 0) or 0
    initial_output = metadata.get("initial_output_tokens", 0) or 0
    retry_input = metadata.get("retry_input_tokens", 0) or 0
    retry_output = metadata.get("retry_output_tokens", 0) or 0

    return initial_input + initial_output + retry_input + retry_output


def _load_model_registry() -> dict:
    """Load the model registry from scripts/providers/models.yaml."""
    registry_path = Path(__file__).parent.parent.parent / "providers" / "models.yaml"
    if registry_path.exists():
        try:
            import yaml
            with open(registry_path) as f:
                return yaml.safe_load(f)
        except Exception:
            pass
    return {}


def _get_model_pricing(manifest: Dict[str, Any]) -> tuple:
    """Look up input/output pricing from the model registry based on manifest metadata.

    Returns (input_per_million, output_per_million, realtime_multiplier).
    Falls back to Gemini batch rates if provider/model unknown.
    """
    metadata = manifest.get("metadata", {})
    provider_name = metadata.get("provider") or metadata.get("cli_provider") or "gemini"
    model_name = metadata.get("model") or metadata.get("cli_model")
    mode = metadata.get("mode", "batch")

    registry = _load_model_registry()
    providers = registry.get("providers", {})
    provider_data = providers.get(provider_name, {})
    realtime_multiplier = provider_data.get("realtime_multiplier", 2.0)

    # Try to find the model in the provider's model list
    if model_name:
        models = provider_data.get("models", {})
        model_data = models.get(model_name, {})
        if model_data:
            input_rate = model_data.get("input_per_million", 0.075)
            output_rate = model_data.get("output_per_million", 0.3)
            multiplier = realtime_multiplier if mode == "realtime" else 1.0
            return input_rate * multiplier, output_rate * multiplier

    # Try default model for the provider
    default_model = provider_data.get("default_model")
    if default_model:
        models = provider_data.get("models", {})
        model_data = models.get(default_model, {})
        if model_data:
            input_rate = model_data.get("input_per_million", 0.075)
            output_rate = model_data.get("output_per_million", 0.3)
            multiplier = realtime_multiplier if mode == "realtime" else 1.0
            return input_rate * multiplier, output_rate * multiplier

    # Fallback to Gemini batch rates
    return 0.075, 0.30


def get_run_cost_value(manifest: Dict[str, Any]) -> float:
    """Calculate cost from manifest token counts using model registry pricing."""
    metadata = manifest.get("metadata", {})

    # Get token counts
    initial_input = metadata.get("initial_input_tokens", 0) or 0
    initial_output = metadata.get("initial_output_tokens", 0) or 0
    retry_input = metadata.get("retry_input_tokens", 0) or 0
    retry_output = metadata.get("retry_output_tokens", 0) or 0

    total_input = initial_input + retry_input
    total_output = initial_output + retry_output

    # Look up pricing from model registry
    input_rate, output_rate = _get_model_pricing(manifest)

    input_cost = (total_input / 1_000_000) * input_rate
    output_cost = (total_output / 1_000_000) * output_rate

    return round(input_cost + output_cost, 4)


def get_run_cost(manifest: Dict[str, Any]) -> str:
    """Get formatted cost string from manifest."""
    cost = get_run_cost_value(manifest)
    if cost > 0:
        return f"${cost:.4f}"
    return "--"


def get_run_mode(manifest: Dict[str, Any]) -> str:
    """Get run mode from manifest."""
    metadata = manifest.get("metadata", {})
    return metadata.get("mode", "batch") or "batch"


def get_run_pipeline_name(manifest: Dict[str, Any], run_path: Optional[Path] = None) -> str:
    """Get pipeline name from manifest or config.

    Args:
        manifest: The manifest dict
        run_path: Optional path to run directory for config fallback

    Returns:
        Pipeline name or empty string if not found
    """
    # First try manifest metadata
    metadata = manifest.get("metadata", {})
    name = metadata.get("pipeline_name", "")
    if name:
        return name

    # Fallback: try to read from config file
    if run_path:
        config_path = run_path / "config" / "config.yaml"
        if config_path.exists():
            try:
                import yaml
                config = yaml.safe_load(config_path.read_text())
                name = config.get("pipeline", {}).get("name", "")
                if name:
                    return name
            except Exception:
                pass

    return ""


def get_run_start_time(manifest: Dict[str, Any]) -> Optional[datetime]:
    """Get run start time from manifest."""
    metadata = manifest.get("metadata", {})
    start_str = metadata.get("start_time")
    if start_str:
        try:
            return datetime.fromisoformat(start_str.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            pass
    return None


def get_run_duration(manifest: Dict[str, Any], run_path: Path) -> str:
    """Get formatted duration string for a run.

    For terminal runs: uses completed_at/failed_at/killed_at or manifest mtime.
    For running runs: uses current time.

    Returns:
        "MM:SS" if < 1 hour, "H:MM:SS" if >= 1 hour, "--" if no start time.
    """
    start = get_run_start_time(manifest)
    if not start:
        return "--"

    status = manifest.get("status", "")

    if status in ("complete", "failed", "killed"):
        # Try explicit end timestamps
        end = None
        for key in ("completed_at", "failed_at", "killed_at"):
            end_str = manifest.get(key)
            if end_str:
                try:
                    end = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
                    # Ensure timezone-aware (some timestamps lack tz info)
                    if end.tzinfo is None and start.tzinfo is not None:
                        end = end.replace(tzinfo=start.tzinfo)
                    break
                except (ValueError, AttributeError):
                    pass
        # Fall back to manifest file mtime
        if end is None:
            manifest_path = run_path / "MANIFEST.json"
            if manifest_path.exists():
                try:
                    mtime = manifest_path.stat().st_mtime
                    end = datetime.fromtimestamp(mtime, tz=start.tzinfo)
                except OSError:
                    pass
        if end is None:
            return "--"
    else:
        # Running — use current time
        end = datetime.now(tz=start.tzinfo)

    elapsed = max(0, (end - start).total_seconds())
    hours = int(elapsed // 3600)
    minutes = int((elapsed % 3600) // 60)
    seconds = int(elapsed % 60)

    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def get_run_failure_count(manifest: Dict[str, Any]) -> int:
    """Count failed chunks in manifest."""
    chunks = manifest.get("chunks", {})
    return sum(1 for c in chunks.values() if c.get("state") == "FAILED")


def get_run_unit_failure_count(manifest: Dict[str, Any]) -> int:
    """Count total failed units across all chunks.

    For terminal runs (complete/failed/killed): computes total_units - total_valid,
    which is more reliable than summing 'failed' fields (those may not be updated
    correctly after retries or mid-pipeline failures).

    For running runs: sums the 'failed' field as a best-effort in-progress count.
    """
    chunks = manifest.get("chunks", {})
    if not chunks:
        return 0

    status = manifest.get("status", "")
    if status in ("complete", "failed", "killed"):
        total_units = sum(c.get("items", 0) for c in chunks.values())
        total_valid = sum(c.get("valid", 0) for c in chunks.values())
        if total_units > 0:
            return max(0, total_units - total_valid)

    return sum(c.get("failed", 0) for c in chunks.values())


def get_run_error_message(manifest: Dict[str, Any]) -> Optional[str]:
    """Get error message from manifest if run failed."""
    return manifest.get("error_message")


def get_batch_timing(run_dir: Path, poll_interval: int = 30) -> Dict[str, Any]:
    """Get last tick and next tick times for a batch run.

    Parses RUN_LOG.txt to find the most recent [TICK] entry and calculates
    timing information for display.

    Args:
        run_dir: Path to run directory
        poll_interval: Expected seconds between ticks (default: 30)

    Returns:
        Dict with:
        - last_tick: datetime of last tick or None
        - next_tick: expected datetime of next tick or None
        - last_tick_ago: human-readable time since last tick (e.g., "5s")
        - last_tick_seconds: raw seconds since last tick
        - next_tick_in: human-readable time until next tick (e.g., "25s")
        - next_tick_seconds: raw seconds until next tick (can be negative if overdue)
    """
    import re
    from datetime import datetime as dt, timezone

    result = {
        "last_tick": None,
        "next_tick": None,
        "last_tick_ago": "--",
        "last_tick_seconds": 0,
        "next_tick_in": "--",
        "next_tick_seconds": 0,
    }

    log_file = run_dir / "RUN_LOG.txt"
    if not log_file.exists():
        return result

    try:
        content = log_file.read_text()
        # Find all TICK lines - format: [2026-01-24T18:22:31Z] [TICK]
        tick_lines = [l for l in content.split("\n") if "[TICK]" in l]
        if not tick_lines:
            return result

        last_tick_line = tick_lines[-1]
        # Parse timestamp: [2026-01-24T18:22:31Z] [TICK]
        match = re.match(r'\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?)\]', last_tick_line)
        if match:
            timestamp_str = match.group(1)
            # Handle both Z and no-Z formats
            if not timestamp_str.endswith('Z'):
                timestamp_str += 'Z'
            last_tick = dt.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            now = dt.now(timezone.utc)

            elapsed = (now - last_tick).total_seconds()
            result["last_tick"] = last_tick
            result["last_tick_ago"] = _format_duration(elapsed)
            result["last_tick_seconds"] = elapsed

            # next_tick_seconds can be negative if overdue
            next_tick_in = poll_interval - elapsed
            result["next_tick_seconds"] = next_tick_in
            if next_tick_in > 0:
                result["next_tick_in"] = _format_duration(next_tick_in)
            else:
                result["next_tick_in"] = "due"
            result["next_tick"] = last_tick.replace(
                second=int(last_tick.second + poll_interval)
            )
    except Exception:
        pass

    return result


def _format_duration(seconds: float) -> str:
    """Format seconds as human-readable duration."""
    seconds = max(0, seconds)
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    else:
        return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"


def get_run_updated(run_path: Path) -> Optional[datetime]:
    """Get last modification time of the manifest."""
    manifest_path = run_path / "MANIFEST.json"
    if manifest_path.exists():
        try:
            mtime = manifest_path.stat().st_mtime
            return datetime.fromtimestamp(mtime)
        except OSError:
            pass
    return None


def scan_runs(runs_dir: Optional[Path] = None, include_archived: bool = False) -> List[Dict[str, Any]]:
    """
    Scan runs directory for valid runs.

    Prefers .manifest_summary.json (fast path, ~300 bytes) over full MANIFEST.json
    (1-5MB). Falls back to full manifest if summary is missing.

    Args:
        runs_dir: Optional path to runs directory. Defaults to project's runs/
        include_archived: If True, also scan runs/_archive/ directory.

    Returns list of dicts with:
    - name: run folder name
    - path: Path to run folder
    - status: "active", "complete", "failed", "pending"
    - progress: 0-100 percent
    - cost: total cost string
    - cost_value: numeric cost
    - total_tokens: token count
    - updated: last update timestamp (datetime or None)
    - started: start timestamp (datetime or None)
    - pipeline: list of pipeline step names
    - pipeline_name: name of the pipeline
    - mode: "batch" or "realtime"
    - total_chunks: number of chunks
    - validated_chunks: number of validated chunks
    - failed_chunks: number of failed chunks
    - is_archived: True if run is in _archive directory
    """
    if runs_dir is None:
        runs_dir = get_runs_dir()

    runs = []

    if not runs_dir.exists():
        return runs

    for folder in sorted(runs_dir.iterdir(), reverse=True):  # Most recent first
        if not folder.is_dir():
            continue

        # Skip hidden directories and _archive directory
        if folder.name.startswith('.') or folder.name == '_archive':
            continue

        summary_path = folder / ".manifest_summary.json"
        manifest_path = folder / "MANIFEST.json"

        if summary_path.exists():
            # Fast path: read lightweight summary
            try:
                run_data = _build_run_data_from_summary(folder, summary_path)
                if run_data:
                    run_data["is_archived"] = False
                    runs.append(run_data)
                    continue
            except Exception:
                pass  # Fall through to full manifest

        # Fallback: load full manifest
        manifest = load_manifest(folder)
        if manifest is None:
            continue

        run_data = _build_run_data_from_manifest(folder, manifest)
        run_data["is_archived"] = False
        runs.append(run_data)

    # Optionally scan archived runs
    if include_archived:
        archive_dir = runs_dir / "_archive"
        if archive_dir.exists():
            for folder in sorted(archive_dir.iterdir(), reverse=True):
                if not folder.is_dir():
                    continue
                if folder.name.startswith('.'):
                    continue

                summary_path = folder / ".manifest_summary.json"

                if summary_path.exists():
                    try:
                        run_data = _build_run_data_from_summary(folder, summary_path)
                        if run_data:
                            run_data["is_archived"] = True
                            runs.append(run_data)
                            continue
                    except Exception:
                        pass

                try:
                    manifest = load_manifest(folder)
                    if manifest is None:
                        continue

                    run_data = _build_run_data_from_manifest(folder, manifest)
                    run_data["is_archived"] = True
                    runs.append(run_data)
                except Exception:
                    pass  # Skip corrupt/incomplete archived runs silently

    return runs


def _build_run_data_from_summary(
    folder: Path, summary_path: Path
) -> Optional[Dict[str, Any]]:
    """
    Build run data dict from a .manifest_summary.json file.

    Returns the same dict structure as _build_run_data_from_manifest().
    """
    with open(summary_path) as f:
        summary = json.load(f)

    status = summary.get("status", "pending")
    cost_value = summary.get("cost", 0.0)
    cost = f"${cost_value:.4f}" if cost_value > 0 else "--"
    mode = summary.get("mode", "batch")

    # Parse started timestamp
    started = None
    started_str = summary.get("started")
    if started_str:
        try:
            started = datetime.fromisoformat(started_str.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            pass

    # Updated: use manifest file mtime for consistency
    updated = get_run_updated(folder)

    # Duration: compute from started + status
    duration = "--"
    if started:
        manifest_status = summary.get("status", "")
        if manifest_status in ("complete", "failed", "killed"):
            # Use manifest file mtime as end time
            manifest_file = folder / "MANIFEST.json"
            if manifest_file.exists():
                try:
                    mtime = manifest_file.stat().st_mtime
                    end = datetime.fromtimestamp(mtime, tz=started.tzinfo)
                    elapsed = max(0, (end - started).total_seconds())
                    hours = int(elapsed // 3600)
                    minutes = int((elapsed % 3600) // 60)
                    seconds = int(elapsed % 60)
                    if hours > 0:
                        duration = f"{hours}:{minutes:02d}:{seconds:02d}"
                    else:
                        duration = f"{minutes:02d}:{seconds:02d}"
                except OSError:
                    pass
        else:
            # Running — use current time
            end = datetime.now(tz=started.tzinfo)
            elapsed = max(0, (end - started).total_seconds())
            hours = int(elapsed // 3600)
            minutes = int((elapsed % 3600) // 60)
            seconds = int(elapsed % 60)
            if hours > 0:
                duration = f"{hours}:{minutes:02d}:{seconds:02d}"
            else:
                duration = f"{minutes:02d}:{seconds:02d}"

    pipeline = summary.get("pipeline", [])

    return {
        "name": folder.name,
        "path": folder,
        "status": status,
        "progress": summary.get("progress", 0),
        "cost": cost,
        "cost_value": cost_value,
        "total_tokens": summary.get("total_tokens", 0),
        "updated": updated,
        "started": started,
        "pipeline": pipeline,
        "pipeline_name": summary.get("pipeline_name", ""),
        "mode": mode,
        "mode_display": mode,  # Mixed mode detection requires full manifest chunks
        "total_chunks": 0,  # Not available from summary
        "validated_chunks": 0,
        "failed_chunks": 0,
        "total_units": summary.get("total_units", 0),
        "valid_units": summary.get("valid_units", 0),
        "unit_failure_count": summary.get("failed_units", 0),
        "error_message": summary.get("error_message"),
        "poll_interval": 30,  # Default; fine for display purposes
        "duration": duration,
    }


def _build_run_data_from_manifest(
    folder: Path, manifest: Dict[str, Any]
) -> Dict[str, Any]:
    """Build run data dict from a full MANIFEST.json (original slow path)."""
    status = get_run_status(manifest)
    progress = get_run_progress(manifest)
    cost = get_run_cost(manifest)
    cost_value = get_run_cost_value(manifest)
    total_tokens = get_run_tokens(manifest)
    updated = get_run_updated(folder)
    started = get_run_start_time(manifest)
    mode = get_run_mode(manifest)
    pipeline_name = get_run_pipeline_name(manifest, folder)
    failed_chunks = get_run_failure_count(manifest)
    unit_failure_count = get_run_unit_failure_count(manifest)
    error_message = get_run_error_message(manifest)

    chunks = manifest.get("chunks", {})
    pipeline = manifest.get("pipeline", [])

    # Detect mixed mode: started as batch (has batch_ids) but resumed in realtime
    mode_display = mode
    if mode == "realtime":
        has_batch_ids = any(c.get("batch_id") for c in chunks.values())
        if has_batch_ids:
            mode_display = "mixed"

    return {
        "name": folder.name,
        "path": folder,
        "status": status,
        "progress": progress,
        "cost": cost,
        "cost_value": cost_value,
        "total_tokens": total_tokens,
        "updated": updated,
        "started": started,
        "pipeline": pipeline,
        "pipeline_name": pipeline_name,
        "mode": mode,
        "mode_display": mode_display,
        "total_chunks": len(chunks),
        "validated_chunks": sum(1 for c in chunks.values() if c.get("state") == "VALIDATED"),
        "failed_chunks": failed_chunks,
        "total_units": sum(c.get("items", 0) for c in chunks.values()),
        "valid_units": sum(c.get("valid", 0) for c in chunks.values()),
        "unit_failure_count": unit_failure_count,
        "error_message": error_message,
        "poll_interval": manifest.get("metadata", {}).get("poll_interval", 30),
        "duration": get_run_duration(manifest, folder),
    }


def get_active_runs(runs_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Get active, detached, and paused runs (anything that might need attention)."""
    all_runs = scan_runs(runs_dir)
    # Include active, detached, and paused runs
    active_statuses = {"active", "detached", "paused"}
    return [r for r in all_runs if r["status"] in active_statuses]


def get_recent_runs(runs_dir: Optional[Path] = None, limit: int = 10) -> List[Dict[str, Any]]:
    """Get recent completed/failed/pending runs (terminal or idle states)."""
    all_runs = scan_runs(runs_dir)
    # Exclude active, detached, and paused (those go in active section)
    terminal_statuses = {"complete", "failed", "pending"}
    non_active = [r for r in all_runs if r["status"] in terminal_statuses]
    return non_active[:limit]


def count_active_runs(runs_dir: Optional[Path] = None) -> int:
    """Count number of active runs."""
    return len(get_active_runs(runs_dir))


def calculate_dashboard_stats(runs: List[Dict[str, Any]], pipeline_count: int) -> Dict[str, Any]:
    """
    Calculate aggregate stats for dashboard display.

    Args:
        runs: List of run dicts from scan_runs
        pipeline_count: Number of pipelines

    Returns dict with:
    - total_runs: count of runs
    - total_tokens: sum of tokens
    - total_tokens_formatted: formatted token string (e.g., "1.2M")
    - total_cost: sum of costs
    - total_cost_formatted: formatted cost string (e.g., "$1.47")
    - pipeline_count: number of pipelines
    """
    total_runs = len(runs)
    total_tokens = sum(r.get('total_tokens', 0) or 0 for r in runs)
    total_cost = sum(r.get('cost_value', 0) or 0 for r in runs)

    return {
        'total_runs': total_runs,
        'total_tokens': total_tokens,
        'total_tokens_formatted': format_token_count(total_tokens),
        'total_cost': total_cost,
        'total_cost_formatted': f"${total_cost:.2f}" if total_cost > 0 else "$0.00",
        'pipeline_count': pipeline_count,
    }


def format_token_count(tokens: int) -> str:
    """Format token count for display (e.g., "1.2M", "456K")."""
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}M"
    elif tokens >= 1_000:
        return f"{tokens / 1_000:.1f}K"
    else:
        return str(tokens)


def format_elapsed_time(start_time: Optional[datetime]) -> str:
    """Format elapsed time since start as human-readable string."""
    if start_time is None:
        return "--"

    now = datetime.now(start_time.tzinfo) if start_time.tzinfo else datetime.now()
    delta = now - start_time

    total_seconds = int(delta.total_seconds())
    if total_seconds < 60:
        return f"{total_seconds} sec ago"
    elif total_seconds < 3600:
        minutes = total_seconds // 60
        return f"{minutes} min ago"
    elif total_seconds < 86400:
        hours = total_seconds // 3600
        return f"{hours} hr ago"
    else:
        days = total_seconds // 86400
        return f"{days} day{'s' if days != 1 else ''} ago"


# --- Process Discovery and Management ---

def get_run_process_status(run_dir: Path) -> Dict[str, Any]:
    """
    Determine if orchestrator for this run is actually alive.

    Returns:
        {"alive": True/False, "pid": int|None, "source": "pid_file"|"discovered"|None}
    """
    if not PSUTIL_AVAILABLE:
        # Can't check process status without psutil
        return {"alive": False, "pid": None, "source": None, "error": "psutil not installed"}

    pid_file = run_dir / "orchestrator.pid"
    run_dir_str = str(run_dir.resolve())

    # Step 1: Check PID file
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            if _verify_orchestrator_process(pid, run_dir_str):
                return {"alive": True, "pid": pid, "source": "pid_file"}
            else:
                # Stale PID file - process died or PID recycled.
                # Leave the file for diagnostic purposes; the dead PID
                # signals "process lost" to the TUI.
                pass
        except (ValueError, FileNotFoundError):
            pass

    # Step 2: Discovery - scan for orphaned process
    discovered_pid = _discover_orchestrator_process(run_dir_str)
    if discovered_pid:
        # Adopt it - write PID file for future
        try:
            pid_file.write_text(str(discovered_pid))
        except Exception:
            pass
        return {"alive": True, "pid": discovered_pid, "source": "discovered"}

    return {"alive": False, "pid": None, "source": None}


def _verify_orchestrator_process(pid: int, run_dir_str: str) -> bool:
    """Verify PID is an orchestrator for this specific run directory."""
    if not PSUTIL_AVAILABLE:
        return False

    try:
        proc = psutil.Process(pid)
        cmdline = " ".join(proc.cmdline())
        # Must be orchestrate.py AND for this specific run
        return "orchestrate.py" in cmdline and run_dir_str in cmdline
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False


def _discover_orchestrator_process(run_dir_str: str) -> Optional[int]:
    """Scan all processes to find orchestrator for this run directory."""
    if not PSUTIL_AVAILABLE:
        return None

    for proc in psutil.process_iter(['pid', 'cmdline']):
        try:
            cmdline = " ".join(proc.info['cmdline'] or [])
            if "orchestrate.py" in cmdline and run_dir_str in cmdline:
                return proc.info['pid']
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return None


def kill_run_process(run_dir: Path) -> bool:
    """
    Kill the orchestrator process for a run.

    Returns True if signal sent, False if no process found.
    """
    status = get_run_process_status(run_dir)
    if status["alive"] and status["pid"]:
        try:
            os.kill(status["pid"], signal.SIGTERM)
            # PID file intentionally left on disk — the stale PID
            # lets the TUI detect the dead process as "process lost".
            return True
        except ProcessLookupError:
            pass
    return False


def has_recent_errors(run_dir: Path, num_lines: int = 50) -> bool:
    """Check if RUN_LOG.txt has unrecovered ERROR entries.

    Compares the timestamp of the most recent [ERROR] against the most recent
    healthy activity ([POLL], [SUBMIT], [COLLECT], [VALIDATE], [STATE], [TICK],
    [EXPRESSION], [PROGRESS], [INFO]). If a healthy event is newer than the last
    error, the run has recovered and this returns False.
    """
    log_file = run_dir / "RUN_LOG.txt"
    if not log_file.exists():
        return False
    try:
        content = log_file.read_text()
        lines = content.strip().split("\n")[-num_lines:]

        # Track line indices of errors vs recovery events (later index = more recent)
        last_error_idx = -1
        last_recovery_idx = -1
        recovery_levels = {
            "[POLL]", "[SUBMIT]", "[COLLECT]", "[VALIDATE]", "[STATE]",
            "[TICK]", "[EXPRESSION]", "[PROGRESS]", "[INFO]", "[BATCH]",
            "[TOKENS]", "[STOP]",
        }

        for idx, line in enumerate(lines):
            if "[ERROR]" in line:
                last_error_idx = idx
            elif any(level in line for level in recovery_levels):
                last_recovery_idx = idx

        # No errors at all
        if last_error_idx < 0:
            return False

        # Error exists but recovery event is more recent — run recovered
        if last_recovery_idx > last_error_idx:
            return False

        # Error is the most recent significant event — still stuck
        return True
    except Exception:
        return False


def get_process_health(run_dir: Path) -> Dict[str, Any]:
    """
    Determine process health based on PID status and log activity.

    The stale threshold is dynamic based on chunk count, since large runs
    with many chunks may take longer between log updates during polling/collecting.

    Returns:
        {
            "status": "running" | "hung" | "dead",
            "pid": int | None,
            "last_activity_seconds": float | None,
            "stale_threshold": float,  # Current threshold in seconds
        }
    """
    run_dir = Path(run_dir)
    proc_status = get_run_process_status(run_dir)

    # Calculate dynamic stale threshold based on chunk count
    # Base: 5 minutes, plus 30 seconds per chunk, capped at 30 minutes
    base_threshold = 300  # 5 minutes
    per_chunk_seconds = 30
    max_threshold = 1800  # 30 minutes

    chunk_count = 0
    manifest_path = run_dir / "MANIFEST.json"
    if manifest_path.exists():
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
                chunk_count = len(manifest.get("chunks", {}))
        except Exception:
            pass

    stale_threshold = min(base_threshold + (chunk_count * per_chunk_seconds), max_threshold)

    result = {
        "status": "dead",
        "pid": proc_status.get("pid"),
        "last_activity_seconds": None,
        "stale_threshold": stale_threshold,
    }

    if not proc_status.get("alive"):
        return result

    # Process is running - check log activity
    log_file = run_dir / "RUN_LOG.txt"
    if log_file.exists():
        try:
            mtime = log_file.stat().st_mtime
            last_activity = time.time() - mtime
            result["last_activity_seconds"] = last_activity

            # Use dynamic threshold for stale detection
            if last_activity > stale_threshold:
                result["status"] = "hung"
            else:
                result["status"] = "running"
        except Exception:
            result["status"] = "running"
    else:
        # No log file yet - probably just started
        result["status"] = "running"

    return result


def get_enhanced_run_status(run_dir: Path, manifest_status: str) -> str:
    """
    Get enhanced status considering manifest AND process state.

    Checks manifest for explicit failure indicators first (error_message field),
    then falls back to process state detection.

    Args:
        run_dir: Path to run directory
        manifest_status: Status from manifest (from get_run_status)

    Returns:
        Enhanced status: "running", "stuck", "zombie", "failed", or original manifest_status
    """
    # First check manifest for explicit failure indicators
    manifest_path = run_dir / "MANIFEST.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())

            # Check explicit status field
            explicit_status = manifest.get("status")
            if explicit_status == "killed":
                return "killed"
            if explicit_status == "failed":
                return "failed"
            if explicit_status == "complete":
                return "complete"
            if explicit_status == "paused":
                return "paused"

            # Check for error message in manifest (even if status says "running")
            if manifest.get("error_message"):
                return "failed"
        except (json.JSONDecodeError, IOError):
            pass

    # For terminal states from get_run_status, return as-is
    if manifest_status in ("complete", "failed", "pending", "killed"):
        return manifest_status

    # For non-terminal states, check actual process
    proc_status = get_run_process_status(run_dir)

    if proc_status["alive"]:
        # Process is running - check for errors in log
        if has_recent_errors(run_dir):
            return "stuck"  # Running but erroring
        return "running"
    else:
        # Process is dead but manifest says it should be running
        if manifest_status in ("running", "active", "detached"):
            return "zombie"  # Dead process, stale manifest
        return manifest_status


def get_process_diagnostics(run_dir: Path) -> Dict[str, Any]:
    """
    Get comprehensive process diagnostics for a run.

    Returns dict with:
    - pid: Process ID if found
    - alive: Whether process is running
    - source: How PID was found ("pid_file", "discovered", or None)
    - cmdline: Full command line if process alive
    - cpu_percent: CPU usage if alive
    - memory_mb: Memory usage in MB if alive
    - create_time: Process start time if alive
    - run_duration: How long process has been running
    - pid_file_exists: Whether orchestrator.pid exists
    - pid_file_content: Content of PID file if exists
    - recent_log_lines: Last 10 lines of RUN_LOG.txt
    - has_errors: Whether recent logs contain errors
    """
    result = {
        "pid": None,
        "alive": False,
        "source": None,
        "cmdline": None,
        "cpu_percent": None,
        "memory_mb": None,
        "create_time": None,
        "run_duration": None,
        "pid_file_exists": False,
        "pid_file_content": None,
        "recent_log_lines": [],
        "has_errors": False,
    }

    # Check PID file
    pid_file = run_dir / "orchestrator.pid"
    if pid_file.exists():
        result["pid_file_exists"] = True
        try:
            result["pid_file_content"] = pid_file.read_text().strip()
        except Exception:
            pass

    # Get process status
    proc_status = get_run_process_status(run_dir)
    result["pid"] = proc_status.get("pid")
    result["alive"] = proc_status.get("alive", False)
    result["source"] = proc_status.get("source")

    # If process is alive, get detailed info
    if result["alive"] and result["pid"] and PSUTIL_AVAILABLE:
        try:
            proc = psutil.Process(result["pid"])
            result["cmdline"] = " ".join(proc.cmdline())
            result["cpu_percent"] = proc.cpu_percent(interval=0.1)
            result["memory_mb"] = round(proc.memory_info().rss / (1024 * 1024), 1)
            result["create_time"] = datetime.fromtimestamp(proc.create_time())
            result["run_duration"] = format_elapsed_time(result["create_time"])
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    # Get recent log lines
    log_file = run_dir / "RUN_LOG.txt"
    if log_file.exists():
        try:
            content = log_file.read_text()
            lines = content.strip().split("\n")
            result["recent_log_lines"] = lines[-10:]
            result["has_errors"] = any("[ERROR]" in line for line in result["recent_log_lines"])
        except Exception:
            pass

    return result


def reset_unit_retries(
    run_dir: Path,
    step_name: Optional[str] = None,
    unit_ids: Optional[List[str]] = None
) -> int:
    """
    Reset failed units so they can be retried.

    This removes entries from *_failures.jsonl files and resets the chunk state
    so the orchestrator will re-process them on the next run.

    Args:
        run_dir: Path to run directory
        step_name: Optional step name to filter (if None, reset all steps)
        unit_ids: Optional list of unit IDs to reset (if None, reset all)

    Returns:
        Number of units reset
    """
    chunks_dir = run_dir / "chunks"
    if not chunks_dir.exists():
        return 0

    reset_count = 0
    chunks_to_update = set()

    for chunk_dir in sorted(chunks_dir.glob("chunk_*")):
        if not chunk_dir.is_dir():
            continue

        # Find failure files (both plain and gzipped)
        pattern = f"{step_name}_failures.jsonl" if step_name else "*_failures.jsonl"
        pattern_gz = f"{step_name}_failures.jsonl.gz" if step_name else "*_failures.jsonl.gz"
        failure_files = list(chunk_dir.glob(pattern)) + list(chunk_dir.glob(pattern_gz))

        for failures_file in failure_files:
            if not failures_file.exists():
                continue

            # Get step name from filename (handle .gz suffix)
            fname = failures_file.name
            if fname.endswith('.gz'):
                fname = fname[:-3]
            step = fname.replace("_failures.jsonl", "")

            # Read current failures
            try:
                with _open_jsonl_for_read(failures_file) as f:
                    lines = [line.strip() for line in f if line.strip()]
            except Exception:
                continue

            # Filter out units to retry
            remaining = []
            reset_units = []
            for line in lines:
                try:
                    data = json.loads(line)
                    uid = data.get("unit_id", "")
                    if unit_ids is None or uid in unit_ids:
                        # This unit should be reset
                        reset_units.append(data)
                        reset_count += 1
                    else:
                        remaining.append(line)
                except json.JSONDecodeError:
                    remaining.append(line)

            if reset_units:
                # Determine the plain path (for writing back)
                plain_path = chunk_dir / f"{step}_failures.jsonl"

                # Create .bak signal for orchestrator's idempotency check.
                # This tells run_step_realtime not to apply the 90% fallback
                # that would otherwise skip the step.
                bak_path = chunk_dir / f"{step}_failures.jsonl.bak"
                if not bak_path.exists() and plain_path.exists():
                    try:
                        import shutil
                        shutil.copy2(plain_path, bak_path)
                    except Exception:
                        pass

                # Write back remaining failures (always to plain file)
                if remaining:
                    plain_path.write_text("\n".join(remaining) + "\n")
                else:
                    # No failures left, remove the file
                    try:
                        plain_path.unlink()
                    except Exception:
                        pass

                # If we read from a .gz file, remove it
                if failures_file.suffix == '.gz':
                    try:
                        failures_file.unlink()
                    except Exception:
                        pass

                chunks_to_update.add((chunk_dir, step))

    # Update manifest to reset chunk states
    if chunks_to_update:
        manifest_path = run_dir / "MANIFEST.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text())
                chunks = manifest.get("chunks", {})

                for chunk_dir, step in chunks_to_update:
                    chunk_name = chunk_dir.name
                    if chunk_name in chunks:
                        chunk_data = chunks[chunk_name]
                        # Reset chunk to pending state for this step
                        chunk_data["state"] = f"{step}_PENDING"
                        # Reset retry counter if present
                        if "retries" in chunk_data:
                            chunk_data["retries"] = 0
                        # Recalculate failed count by counting remaining failures
                        remaining_failures = 0
                        fail_files = list(chunk_dir.glob("*_failures.jsonl")) + list(chunk_dir.glob("*_failures.jsonl.gz"))
                        for fail_file in fail_files:
                            try:
                                with _open_jsonl_for_read(fail_file) as f:
                                    remaining_failures += sum(1 for line in f if line.strip())
                            except Exception:
                                pass
                        chunk_data["failed"] = remaining_failures

                # Clear failed/complete status - will be set to running by caller
                if manifest.get("status") in ("failed", "complete"):
                    manifest["status"] = "running"
                    manifest.pop("error_message", None)

                # Atomic write
                tmp_path = manifest_path.with_suffix(".tmp")
                tmp_path.write_text(json.dumps(manifest, indent=2))
                tmp_path.rename(manifest_path)
            except Exception:
                pass

    return reset_count


def mark_run_as_failed(run_dir: Path, error_message: str = "Marked as failed by user") -> bool:
    """
    Mark a run as failed by updating its manifest.

    Args:
        run_dir: Path to the run directory
        error_message: Error message to record in manifest

    Returns:
        True if successfully updated, False otherwise
    """
    manifest_path = run_dir / "MANIFEST.json"
    if not manifest_path.exists():
        return False

    try:
        manifest = json.loads(manifest_path.read_text())
        manifest["status"] = "failed"
        manifest["error_message"] = error_message
        manifest["failed_at"] = datetime.now().isoformat()

        # Atomic write using temp file
        tmp_path = manifest_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(manifest, indent=2))
        tmp_path.rename(manifest_path)

        return True
    except Exception:
        return False


def mark_run_as_killed(run_dir: Path) -> bool:
    """
    Mark a run as killed by user by updating its manifest.

    Args:
        run_dir: Path to the run directory

    Returns:
        True if successfully updated, False otherwise
    """
    manifest_path = run_dir / "MANIFEST.json"
    if not manifest_path.exists():
        return False

    try:
        manifest = json.loads(manifest_path.read_text())
        manifest["status"] = "killed"
        manifest["error_message"] = "Run killed by user"
        manifest["killed_at"] = datetime.now().isoformat()

        # Atomic write using temp file
        tmp_path = manifest_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(manifest, indent=2))
        tmp_path.rename(manifest_path)

        return True
    except Exception:
        return False


def resume_orchestrator(run_dir: Path, mode: str = "watch") -> bool:
    """
    Spawn an orchestrator process to continue/retry a run.

    Touches the manifest timestamp before spawning so the TUI sees
    recent activity immediately. If the spawn fails, reverts manifest
    status to its previous state.

    Args:
        run_dir: Path to the run directory
        mode: "watch" for batch mode, "realtime" for realtime mode

    Returns:
        True if process started successfully
    """
    run_dir = Path(run_dir).resolve()

    # Get project root (runs are in project_root/runs/)
    project_root = run_dir.parent.parent

    # Build command
    cmd = [
        sys.executable,  # Current Python interpreter
        str(project_root / "scripts" / "orchestrate.py"),
        "--run-dir", str(run_dir),
        f"--{mode}",
    ]

    # Touch manifest timestamp so TUI sees fresh activity
    manifest_path = run_dir / "MANIFEST.json"
    previous_status = None
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
            previous_status = manifest.get("status")
            manifest["updated"] = datetime.now().isoformat()
            tmp_path = manifest_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(manifest, indent=2))
            tmp_path.rename(manifest_path)
        except Exception:
            pass

    # Touch RUN_LOG.txt so get_process_health sees recent activity
    # (prevents HUNG display between manifest update and first orchestrator log)
    run_log = run_dir / "RUN_LOG.txt"
    try:
        with open(run_log, "a") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ')}] [RESUME] Orchestrator resuming...\n")
    except Exception:
        pass

    # Open log file to append output
    log_file = run_dir / "orchestrator.log"

    try:
        stderr_log_path = run_dir / "orchestrator_stderr.log"
        with open(log_file, "a") as log, open(stderr_log_path, "a") as stderr_log:
            log.write(f"\n--- Resume started at {datetime.now().isoformat()} ---\n")
            log.write(f"Command: {' '.join(cmd)}\n")
            log.flush()

            # Spawn detached process
            # stdin=DEVNULL prevents child from inheriting raw terminal state
            # stderr to file captures crash output that would otherwise be lost
            # Detach child from TUI session: setsid on Unix,
            # CREATE_NEW_PROCESS_GROUP|CREATE_NO_WINDOW on Windows.
            if sys.platform == "win32":
                detach = {
                    "creationflags": (
                        subprocess.CREATE_NEW_PROCESS_GROUP
                        | subprocess.CREATE_NO_WINDOW
                    ),
                }
            else:
                detach = {"start_new_session": True}

            process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=stderr_log,
                cwd=project_root,
                **detach,
            )

        # Give it a moment to start and write PID file
        time.sleep(0.5)

        # Verify it started
        pid_file = run_dir / "orchestrator.pid"
        started = pid_file.exists() or process.poll() is None

        if not started:
            # Process failed to start - revert manifest status
            _revert_manifest_status(manifest_path, previous_status)
            try:
                with open(log_file, "a") as log:
                    log.write(f"Orchestrator process exited immediately (poll={process.poll()})\n")
            except Exception:
                pass

        return started

    except Exception as e:
        # Spawn failed - revert manifest status
        _revert_manifest_status(manifest_path, previous_status)
        # Log the error
        try:
            with open(log_file, "a") as log:
                log.write(f"Failed to start orchestrator: {e}\n")
        except Exception:
            pass
        return False


def _revert_manifest_status(manifest_path: Path, previous_status: str | None) -> None:
    """Revert manifest status after a failed orchestrator spawn."""
    if previous_status is None or not manifest_path.exists():
        return
    try:
        manifest = json.loads(manifest_path.read_text())
        manifest["status"] = previous_status
        tmp_path = manifest_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(manifest, indent=2))
        tmp_path.rename(manifest_path)
    except Exception:
        pass
