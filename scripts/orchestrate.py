#!/usr/bin/env python3
"""
orchestrate.py - Main orchestrator for batch processing runs.

Manages the lifecycle of batch processing runs:
- --init: Set up a new run directory with config snapshot and generated units
- --tick: Advance the run by one step (poll, collect, validate, submit)
- --status: Show current run status
- --watch: Automatically poll until completion (with optional cost/time limits)
- --retry-failures: Create retry chunks from failed units
- --validate-config: Validate config file and expressions before running

Usage:
    # Initialize with a config file path:
    python orchestrate.py --config config/example_config.yaml --run-dir runs/run_001 --init

    # Initialize with a pipeline name (from pipelines/ folder):
    python orchestrate.py --pipeline MyPipeline --run-dir runs/run_001 --init
    python orchestrate.py -p "NPC Dialog" -r runs/npc_test --init --max-units 5

    # Other operations:
    python orchestrate.py --run-dir runs/run_001 --tick
    python orchestrate.py --run-dir runs/run_001 --status
    python orchestrate.py --run-dir runs/run_001 --watch
    python orchestrate.py --run-dir runs/run_001 --watch --interval 30 --max-cost 1.00
    python orchestrate.py --run-dir runs/run_001 --watch --timeout 30m
    python orchestrate.py --run-dir runs/run_001 --retry-failures
    python orchestrate.py --config config/example_config.yaml --validate-config
"""

import argparse
import atexit
import hashlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# Type alias for batch collect result (can be int or CollectResult dict)
CollectResultType = int | dict

try:
    from asteval import Interpreter
    ASTEVAL_AVAILABLE = True
except ImportError:
    ASTEVAL_AVAILABLE = False

# Add parent directory to path for module imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from expression_evaluator import evaluate_expressions, get_expressions, SeededRandom

from octobatch_utils import (
    __version__,
    load_manifest,
    save_manifest,
    load_jsonl,
    load_jsonl_by_id,
    append_jsonl,
    log_message,
    trace_log,
    format_elapsed_time,
    compute_cost,
    parse_json_response,
)

from config_validator import (
    validate_config,
    validate_config_run,
    get_pipeline_steps,
    get_chunk_scope_steps,
    get_run_scope_steps,
    get_step_config,
    get_item_source_path,
)

try:
    from scripts.providers import get_provider, get_step_provider, ProviderError
    from scripts.providers.base import BatchStatus
except ImportError:
    get_provider = None  # Will error at runtime if provider is needed
    get_step_provider = None
    ProviderError = Exception
    BatchStatus = None

# Default timeout for subprocess calls (10 minutes)
# Can be overridden via config's api.subprocess_timeout_seconds
SUBPROCESS_TIMEOUT_DEFAULT = 600

# Log progress during long loops if more than this many items
# This helps prevent false "stale" detection during large batch operations
PROGRESS_LOG_THRESHOLD = 10

# Track active subprocesses for cleanup on interrupt
_active_subprocesses: list[subprocess.Popen] = []


def get_subprocess_timeout(config: dict | None = None) -> int:
    """Get subprocess timeout from config, falling back to default.

    Reads api.subprocess_timeout_seconds from pipeline config.
    """
    if config:
        timeout = config.get("api", {}).get("subprocess_timeout_seconds")
        if timeout is not None:
            return int(timeout)
    return SUBPROCESS_TIMEOUT_DEFAULT

# Track current run directory for PID file cleanup
_current_run_dir: Path | None = None

# Maximum time to spend saving manifest on SIGINT before exiting anyway
SIGINT_SAVE_TIMEOUT = 5


def write_pid_file(run_dir: Path) -> None:
    """Write orchestrator PID to file for process tracking."""
    global _current_run_dir
    pid_file = run_dir / "orchestrator.pid"
    pid_file.write_text(str(os.getpid()))
    _current_run_dir = run_dir


def cleanup_pid_file(run_dir: Path = None) -> None:
    """PID file cleanup — intentionally a no-op.

    The PID file persists after the orchestrator exits so that the TUI can
    detect dead processes via os.kill(pid, 0). A stale PID with a dead
    process is correctly reported as "process lost" / "detached", which is
    the desired behavior. Deleting the file would remove this signal.
    """
    pass


def _cleanup_subprocesses(signum=None, frame=None):
    """Terminate all tracked child processes and save manifest on SIGINT/SIGTERM."""
    for proc in _active_subprocesses:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    _active_subprocesses.clear()

    # Best-effort manifest save as paused with timeout guard
    if _current_run_dir is not None:
        deadline = time.monotonic() + SIGINT_SAVE_TIMEOUT
        try:
            manifest_path = _current_run_dir / "MANIFEST.json"
            if manifest_path.exists() and time.monotonic() < deadline:
                manifest = load_manifest(_current_run_dir)
                current_status = manifest.get("status")
                if current_status not in ("complete", "failed") and time.monotonic() < deadline:
                    manifest["status"] = "paused"
                    manifest["paused_at"] = datetime.now(timezone.utc).isoformat()
                    save_manifest(_current_run_dir, manifest)
        except Exception:
            pass  # Best-effort — don't let save failure prevent exit

    if signum is not None:
        sys.exit(130)  # Standard exit code for SIGINT


# Register signal handlers for graceful cleanup
signal.signal(signal.SIGINT, _cleanup_subprocesses)
signal.signal(signal.SIGTERM, _cleanup_subprocesses)


def _log_api_key_status(log_file: Path, mode_tag: str) -> None:
    """Log whether the active provider has a valid API key."""
    # Detect which provider is configured from the run's config
    provider = "gemini"  # default
    run_dir = log_file.parent
    config_path = run_dir / "config" / "config.yaml"
    if config_path.exists():
        try:
            with open(config_path) as f:
                cfg = yaml.safe_load(f)
            provider = cfg.get("api", {}).get("provider", "gemini").lower()
        except Exception:
            pass

    # Check the relevant key for the active provider
    if provider == "gemini":
        has_key = bool(os.environ.get("GOOGLE_API_KEY"))
        log_message(log_file, mode_tag, f"Gemini API key (GOOGLE_API_KEY): {'AVAILABLE' if has_key else 'MISSING'}")
    elif provider == "openai":
        has_key = bool(os.environ.get("OPENAI_API_KEY"))
        log_message(log_file, mode_tag, f"OpenAI API key: {'AVAILABLE' if has_key else 'MISSING'}")
    elif provider == "anthropic":
        has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        log_message(log_file, mode_tag, f"Anthropic API key: {'AVAILABLE' if has_key else 'MISSING'}")
    else:
        log_message(log_file, mode_tag, f"Provider '{provider}' — key status unknown")


# =============================================================================
# Run Status Management
# =============================================================================

def mark_run_failed(run_dir: Path, error_message: str, log_traceback: bool = True) -> None:
    """
    Mark a run as failed in the manifest and log the error.

    Args:
        run_dir: Path to run directory
        error_message: Error message to store
        log_traceback: If True, log full traceback to RUN_LOG.txt
    """
    import traceback

    manifest_path = run_dir / "MANIFEST.json"
    if not manifest_path.exists():
        return

    try:
        manifest = load_manifest(run_dir)
        manifest["status"] = "failed"
        manifest["error_message"] = error_message[:500]  # Truncate long errors
        manifest["failed_at"] = datetime.now(timezone.utc).isoformat()
        save_manifest(run_dir, manifest)

        # Log to RUN_LOG.txt
        log_file = run_dir / "RUN_LOG.txt"
        log_message(log_file, "FAILED", f"Run failed: {error_message}")
        if log_traceback:
            tb = traceback.format_exc()
            if tb and tb != "NoneType: None\n":
                log_message(log_file, "TRACEBACK", tb)

    except Exception as e:
        # Best-effort logging, don't fail on logging failures
        print(f"Warning: Could not update manifest for failed run: {e}", file=sys.stderr)


def mark_run_paused(run_dir: Path, reason: str = "Run interrupted by user") -> None:
    """
    Mark a run as paused in the manifest.

    Args:
        run_dir: Path to run directory
        reason: Reason for pause
    """
    manifest_path = run_dir / "MANIFEST.json"
    if not manifest_path.exists():
        return

    try:
        manifest = load_manifest(run_dir)
        # Only mark as paused if not already in a terminal state
        current_status = manifest.get("status")
        if current_status not in ("complete", "failed"):
            manifest["status"] = "paused"
            manifest["paused_at"] = datetime.now(timezone.utc).isoformat()
            save_manifest(run_dir, manifest)

        # Log to RUN_LOG.txt
        log_file = run_dir / "RUN_LOG.txt"
        log_message(log_file, "PAUSED", reason)

    except Exception as e:
        print(f"Warning: Could not update manifest for paused run: {e}", file=sys.stderr)


def mark_run_complete(run_dir: Path) -> None:
    """
    Mark a run as complete in the manifest.

    Args:
        run_dir: Path to run directory
    """
    manifest_path = run_dir / "MANIFEST.json"
    if not manifest_path.exists():
        return

    try:
        manifest = load_manifest(run_dir)
        current_status = manifest.get("status")
        manifest["status"] = "complete"
        manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
        save_manifest(run_dir, manifest)

        # Log to RUN_LOG.txt
        log_file = run_dir / "RUN_LOG.txt"
        if current_status == "failed":
            log_message(log_file, "COMPLETE", f"Run status corrected from '{current_status}' to complete (all chunks terminal)")
        else:
            log_message(log_file, "COMPLETE", "Run completed successfully")

    except Exception as e:
        print(f"Warning: Could not update manifest for completed run: {e}", file=sys.stderr)


def mark_run_running(run_dir: Path) -> None:
    """
    Mark a run as running in the manifest (clears paused status on resume).

    Args:
        run_dir: Path to run directory
    """
    manifest_path = run_dir / "MANIFEST.json"
    if not manifest_path.exists():
        return

    try:
        manifest = load_manifest(run_dir)
        current_status = manifest.get("status")

        if current_status in ("complete", "failed"):
            # Check if chunks are actually terminal — if not, this was
            # a premature completion and we should allow the resume
            chunks = manifest.get("chunks", {})
            all_terminal = all(
                c.get("state", "") in ("VALIDATED", "FAILED")
                for c in chunks.values()
            )
            if all_terminal:
                return  # Truly complete/failed, don't override

        manifest["status"] = "running"
        # Clear paused timestamp if resuming
        if "paused_at" in manifest:
            del manifest["paused_at"]
        # Store current PID in manifest as backup for diagnostic tools
        if "metadata" not in manifest:
            manifest["metadata"] = {}
        manifest["metadata"]["pid"] = os.getpid()
        save_manifest(run_dir, manifest)

    except Exception as e:
        print(f"Warning: Could not update manifest for running run: {e}", file=sys.stderr)


def _run_gzip_post_process(run_dir: Path, entry: dict, name: str) -> None:
    """Run a built-in gzip post-processing step.

    Compresses files matching glob patterns relative to run_dir.

    Args:
        run_dir: Path to run directory
        entry: Post-process config entry with 'files' and optional 'keep_originals'
        name: Display name for logging
    """
    import gzip as gzip_module

    time_str = datetime.now().strftime("%H:%M:%S")
    file_patterns = entry.get("files", [])
    keep_originals = entry.get("keep_originals", False)

    if not file_patterns:
        print(f"[{time_str}] [POST-PROCESS] {name}: No file patterns specified, skipping")
        return

    compressed_count = 0
    error_count = 0

    for pattern in file_patterns:
        matched_files = list(run_dir.glob(pattern))
        if not matched_files:
            print(f"[{time_str}] [POST-PROCESS] {name}: No files match '{pattern}'")
            continue

        for file_path in matched_files:
            # Skip already-compressed files
            if file_path.suffix == '.gz':
                continue
            # Skip directories
            if file_path.is_dir():
                continue

            gz_path = file_path.with_suffix(file_path.suffix + '.gz')
            try:
                with open(file_path, 'rb') as f_in:
                    with gzip_module.open(gz_path, 'wb') as f_out:
                        # Copy in chunks to handle large files
                        while True:
                            chunk = f_in.read(8192)
                            if not chunk:
                                break
                            f_out.write(chunk)

                compressed_count += 1

                if not keep_originals:
                    file_path.unlink()

            except Exception as e:
                error_count += 1
                print(f"[{time_str}] [POST-PROCESS] {name}: Error compressing {file_path.name}: {e}")
                # Clean up partial gz file if it exists
                if gz_path.exists():
                    try:
                        gz_path.unlink()
                    except Exception:
                        pass

    status = f"Compressed {compressed_count} file(s)"
    if keep_originals:
        status += " (originals kept)"
    if error_count > 0:
        status += f", {error_count} error(s)"
    print(f"[{time_str}] [POST-PROCESS] {name}: {status}")


def run_post_process(run_dir: Path, config: dict) -> None:
    """
    Execute post-processing scripts defined in config.

    Args:
        run_dir: Path to run directory
        config: Loaded config dict
    """
    post_process = config.get("post_process")
    if not post_process:
        return

    # Get log file for logging to RUN_LOG.txt
    log_file = run_dir / "RUN_LOG.txt"

    for entry in post_process:
        name = entry.get("name", "Unnamed")
        entry_type = entry.get("type", "script")

        if entry_type == "gzip":
            _run_gzip_post_process(run_dir, entry, name)
            continue
        elif entry_type != "script":
            log_message(log_file, "POST-PROCESS", f"{name}: Unknown type '{entry_type}', skipping")
            continue

        # Script type processing
        script = entry.get("script")
        args = entry.get("args", [])
        output_file = entry.get("output")

        if not script:
            log_message(log_file, "POST-PROCESS", f"{name}: No script specified, skipping")
            continue

        log_message(log_file, "POST-PROCESS", f"Running: {name}...")

        # Construct command: python script run_dir args...
        cmd = [sys.executable, script, str(run_dir)] + args

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT_DEFAULT
            )

            if result.returncode != 0:
                log_message(log_file, "POST-PROCESS", f"{name}: Failed with exit code {result.returncode}")
                if result.stderr:
                    # Print first few lines of stderr
                    stderr_lines = result.stderr.strip().split('\n')[:5]
                    for line in stderr_lines:
                        log_message(log_file, "POST-PROCESS", f"  {line}")
                continue

            # Handle output
            if output_file:
                output_path = run_dir / output_file
                output_path.write_text(result.stdout)
                log_message(log_file, "POST-PROCESS", f"{name}: Output written to {output_path}")
            else:
                # Print stdout with prefix
                if result.stdout.strip():
                    log_message(log_file, "POST-PROCESS", f"{name} output:")
                    for line in result.stdout.strip().split('\n'):
                        log_message(log_file, "POST-PROCESS", f"  {line}")

            # Print any stderr warnings even on success
            if result.stderr and result.stderr.strip():
                stderr_lines = result.stderr.strip().split('\n')[:10]
                for line in stderr_lines:
                    log_message(log_file, "POST-PROCESS", f"{name}: {line}")

        except subprocess.TimeoutExpired:
            log_message(log_file, "POST-PROCESS", f"{name}: Timed out after {SUBPROCESS_TIMEOUT_DEFAULT}s")
        except FileNotFoundError:
            log_message(log_file, "POST-PROCESS", f"{name}: Script not found: {script}")
        except Exception as e:
            log_message(log_file, "POST-PROCESS", f"{name}: Error: {e}")


def check_prerequisites(config: dict | None = None) -> str | None:
    """
    Check required environment variables before starting.

    Collects all providers that could be used (global config, per-step config,
    registry default) and checks for the required API key for each.

    Args:
        config: Optional config dict to determine provider

    Returns:
        Error message string if prerequisites not met, None if OK
    """
    PROVIDER_KEY_MAP = {
        "gemini": ("GOOGLE_API_KEY", "export GOOGLE_API_KEY=your_key"),
        "openai": ("OPENAI_API_KEY", "export OPENAI_API_KEY=your_key"),
        "anthropic": ("ANTHROPIC_API_KEY", "export ANTHROPIC_API_KEY=your_key"),
    }

    # Collect all providers that could be needed
    needed_providers = set()

    if config:
        # Global api.provider
        global_provider = config.get("api", {}).get("provider")
        if global_provider:
            needed_providers.add(global_provider.lower())

        # Per-step providers
        steps = config.get("pipeline", {}).get("steps", [])
        for step in steps:
            step_provider = step.get("provider")
            if step_provider:
                needed_providers.add(step_provider.lower())

    # If no provider specified anywhere, check the registry default
    if not needed_providers:
        try:
            from scripts.providers.base import LLMProvider
            registry = LLMProvider.load_model_registry()
            default_provider = registry.get("default_provider")
            if default_provider:
                needed_providers.add(default_provider.lower())
        except Exception:
            pass

    # If still no provider identified, accept any key
    if not needed_providers:
        has_any_key = (
            os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
        )
        if not has_any_key:
            return "No API key found. Set at least one of: GOOGLE_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY"
        return None

    # Check that each needed provider has its API key
    missing = []
    for provider_name in sorted(needed_providers):
        key_info = PROVIDER_KEY_MAP.get(provider_name)
        if key_info:
            env_var, hint = key_info
            if not os.environ.get(env_var):
                missing.append(f"{env_var} not set (needed for {provider_name}). {hint}")

    if missing:
        return "; ".join(missing)

    return None


def extract_collect_result(result: CollectResultType) -> tuple[int, dict | None]:
    """
    Extract count and batch_metadata from collect() result.

    Handles both legacy int return and new CollectResult dict.

    Returns:
        (count, batch_metadata) - metadata is None for legacy int returns
    """
    if isinstance(result, int):
        return result, None
    elif isinstance(result, dict):
        return result.get("count", 0), result.get("batch_metadata")
    else:
        return 0, None


def build_failures_map(run_dir: Path, manifest: dict) -> dict:
    """
    Build failures map by scanning *_failures.jsonl files.

    Derives the current failures from the filesystem rather than storing
    in the manifest. This ensures failures that have been successfully
    retried are automatically excluded.

    Returns:
        Dict mapping unit_id to failure info:
        {
            "unit_id": {
                "chunk": "chunk_002",
                "step": "validate",
                "stage": "validation",
                "errors": [...],
                "retry_count": 0
            }
        }
    """
    failures = {}
    chunks_dir = run_dir / "chunks"

    if not chunks_dir.exists():
        return failures

    # First, collect ALL validated unit_ids across all chunks (including retries)
    # Key is (step, unit_id) to track which step validated each unit
    all_validated_ids: dict[tuple[str, str], str] = {}

    for chunk_name, chunk_data in manifest.get("chunks", {}).items():
        chunk_dir = chunks_dir / chunk_name
        if not chunk_dir.exists():
            continue

        for validated_file in chunk_dir.glob("*_validated.jsonl"):
            step = validated_file.stem.replace("_validated", "")
            try:
                with open(validated_file) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            unit_id = data.get("unit_id")
                            if unit_id:
                                all_validated_ids[(step, unit_id)] = chunk_name
                        except json.JSONDecodeError:
                            continue
            except OSError:
                continue

    # Now scan failure files, excluding units that succeeded anywhere
    for chunk_name, chunk_data in manifest.get("chunks", {}).items():
        chunk_dir = chunks_dir / chunk_name
        if not chunk_dir.exists():
            continue

        for failure_file in chunk_dir.glob("*_failures.jsonl"):
            step = failure_file.stem.replace("_failures", "")

            try:
                with open(failure_file) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            failure = json.loads(line)
                            unit_id = failure.get("unit_id")
                            if not unit_id:
                                continue

                            # Skip if this unit succeeded anywhere (including retry chunks)
                            if (step, unit_id) in all_validated_ids:
                                continue

                            failures[unit_id] = {
                                "chunk": chunk_name,
                                "step": step,
                                "stage": failure.get("failure_stage", "validation"),
                                "errors": failure.get("errors", []),
                                "retry_count": failure.get("retry_count", 0)
                            }
                        except json.JSONDecodeError:
                            continue
            except OSError:
                continue

    return failures


def parse_state(state: str) -> tuple[str, str]:
    """
    Parse a chunk state into (step, status).

    Examples:
        "generate_PENDING" -> ("generate", "PENDING")
        "my_step_SUBMITTED" -> ("my_step", "SUBMITTED")
        "VALIDATED" -> (None, "VALIDATED")
        "FAILED" -> (None, "FAILED")
    """
    if state in ("VALIDATED", "FAILED"):
        return (None, state)

    # Split from the right to handle step names with underscores
    for suffix in ("_PENDING", "_SUBMITTED", "_COMPLETE"):
        if state.endswith(suffix):
            step = state[:-len(suffix)]
            status = suffix[1:]  # Remove leading underscore
            return (step, status)

    # Unknown state format
    return (None, state)


def get_next_step(pipeline: list[str], current_step: str) -> str | None:
    """
    Get the next step in the pipeline after current_step.

    Returns None if current_step is the last step.
    """
    try:
        idx = pipeline.index(current_step)
        if idx + 1 < len(pipeline):
            return pipeline[idx + 1]
        return None
    except ValueError:
        return None


def get_schema_path(config: dict, step: str, run_dir: Path) -> Path | None:
    """Get the schema file path for a step."""
    schemas_config = config.get("schemas", {})
    schema_dir = schemas_config.get("schema_dir", "schemas")
    files = schemas_config.get("files", {})

    schema_file = files.get(step)
    if not schema_file:
        return None

    # Look in config directory first (copied with config)
    config_schema_dir = run_dir / "config" / schema_dir
    if (config_schema_dir / schema_file).exists():
        return config_schema_dir / schema_file

    # Fall back to original location
    scripts_dir = Path(__file__).parent
    original_schema_dir = scripts_dir.parent / "config" / schema_dir
    if (original_schema_dir / schema_file).exists():
        return original_schema_dir / schema_file

    return None


def count_step_failures(run_dir: Path, step_name: str) -> dict:
    """
    Count failures by rule name from all {step}_failures.jsonl files.

    Returns:
        {
            "by_rule": {"rule_name": count, ...},
            "total": total_count
        }
    """
    chunks_dir = run_dir / "chunks"
    by_rule: dict[str, int] = {}
    total = 0

    if not chunks_dir.exists():
        return {"by_rule": by_rule, "total": total}

    for chunk_dir in chunks_dir.iterdir():
        if not chunk_dir.is_dir():
            continue

        failures_file = chunk_dir / f"{step_name}_failures.jsonl"
        if not failures_file.exists():
            continue

        try:
            with open(failures_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        failure = json.loads(line)
                        errors = failure.get("errors", [])
                        for error in errors:
                            rule = error.get("rule", "unknown")
                            by_rule[rule] = by_rule.get(rule, 0) + 1
                            total += 1
                    except json.JSONDecodeError:
                        # Non-JSON line, count as unknown error
                        by_rule["parse_error"] = by_rule.get("parse_error", 0) + 1
                        total += 1
        except OSError:
            pass

    return {"by_rule": by_rule, "total": total}


def categorize_step_failures(run_dir: Path, step_name: str) -> dict:
    """Categorize failures into validation vs hard failures for a step.

    Scans {step}_failures.jsonl files in all chunk directories and categorizes
    each failure based on its failure_stage field:
    - "schema_validation" or "validation" → validation (retryable)
    - "pipeline_internal" or anything else → hard (not retryable)

    Returns:
        {"validation": count, "hard": count, "total": count}
    """
    validation_stages = {"schema_validation", "validation"}
    chunks_dir = run_dir / "chunks"
    validation = 0
    hard = 0

    if not chunks_dir.exists():
        return {"validation": 0, "hard": 0, "total": 0}

    for chunk_dir in chunks_dir.iterdir():
        if not chunk_dir.is_dir():
            continue
        failures_file = chunk_dir / f"{step_name}_failures.jsonl"
        if not failures_file.exists():
            continue
        try:
            with open(failures_file) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        failure = json.loads(line)
                        stage = failure.get("failure_stage", "validation")
                        if stage in validation_stages:
                            validation += 1
                        else:
                            hard += 1
                    except json.JSONDecodeError:
                        hard += 1
        except OSError:
            pass

    return {"validation": validation, "hard": hard, "total": validation + hard}


def retry_validation_failures(run_dir: Path, manifest: dict, log_file: Path) -> int:
    """Reset chunks with validation failures so they can be retried.

    For each step/chunk that has validation failures:
    - Rotates {step}_failures.jsonl to {step}_failures.jsonl.bak
    - Preserves hard failures (pipeline_internal) in the failures file
    - Resets chunk state to {step}_PENDING for the earliest failing step

    The .bak file also serves as a signal to run_step_realtime's idempotency
    check to not treat the step as already complete.

    Args:
        run_dir: Path to run directory
        manifest: Loaded manifest dict (will be mutated)
        log_file: Path to log file

    Returns:
        Total number of validation failures archived.
    """
    validation_stages = {"schema_validation", "validation"}
    pipeline = manifest.get("pipeline", [])
    chunks = manifest.get("chunks", {})
    chunks_dir = run_dir / "chunks"
    archived_total = 0

    for chunk_name, chunk_data in chunks.items():
        chunk_dir = chunks_dir / chunk_name
        if not chunk_dir.exists():
            continue

        # State machine guard: only process chunks in terminal states.
        # Any other state (PENDING/SUBMITTED/PROCESSING) must be left alone —
        # resetting in-flight chunks would orphan batches at the provider.
        chunk_state = chunk_data.get("state", "")
        is_terminal = (
            chunk_state.endswith("_VALIDATED")
            or chunk_state.endswith("_FAILED")
            or chunk_state in ("VALIDATED", "FAILED", "complete")
        )
        if not is_terminal:
            continue

        reset_to_step = None  # Track earliest step needing reset

        for step in pipeline:
            failures_file = chunk_dir / f"{step}_failures.jsonl"
            if not failures_file.exists():
                continue

            # Categorize failures
            validation_failures = []
            hard_failures = []
            try:
                with open(failures_file) as f:
                    for line in f:
                        line_s = line.strip()
                        if not line_s:
                            continue
                        try:
                            failure = json.loads(line_s)
                            stage = failure.get("failure_stage", "validation")
                            if stage in validation_stages:
                                validation_failures.append(line_s)
                            else:
                                hard_failures.append(line_s)
                        except json.JSONDecodeError:
                            hard_failures.append(line_s)
            except OSError:
                continue

            if not validation_failures:
                continue

            # Rotate to .bak (archive the full original file)
            bak_path = chunk_dir / f"{step}_failures.jsonl.bak"
            try:
                shutil.copy2(failures_file, bak_path)
            except OSError:
                pass

            # Rewrite failures file with only hard failures, or delete if none
            if hard_failures:
                with open(failures_file, 'w') as f:
                    for line_s in hard_failures:
                        f.write(line_s + '\n')
            else:
                try:
                    failures_file.unlink()
                except OSError:
                    pass

            archived_total += len(validation_failures)

            # Track the earliest pipeline step with failures
            if reset_to_step is None:
                reset_to_step = step

            log_message(log_file, "RETRY",
                f"Resetting {chunk_name}/{step} for retry "
                f"({len(validation_failures)} failure{'s' if len(validation_failures) != 1 else ''} archived)")

        # Reset chunk state to the earliest step that had failures
        if reset_to_step:
            chunk_data["state"] = f"{reset_to_step}_PENDING"
            # Recalculate failed count from remaining hard failures
            remaining = 0
            for step in pipeline:
                ff = chunk_dir / f"{step}_failures.jsonl"
                if ff.exists():
                    try:
                        with open(ff) as f:
                            remaining += sum(1 for line in f if line.strip())
                    except OSError:
                        pass
            chunk_data["failed"] = remaining

    if archived_total > 0:
        manifest["status"] = "running"
        save_manifest(run_dir, manifest)

    return archived_total


def compute_step_cost(
    input_tokens: int,
    output_tokens: int,
    provider=None,
    is_realtime: bool = False
) -> float | None:
    """
    Calculate estimated cost for a step based on token usage using the provider.

    Args:
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        provider: LLMProvider instance with estimate_cost() method
        is_realtime: Whether this is realtime mode (affects pricing)

    Returns:
        Estimated cost in USD, or None if provider is not available.
    """
    if provider is None:
        return None

    try:
        # Use provider's built-in pricing via estimate_cost()
        # is_batch is the opposite of is_realtime
        cost = provider.estimate_cost(input_tokens, output_tokens, is_batch=not is_realtime)
        return round(cost, 6)
    except Exception:
        return None


def format_step_provider_tag(config: dict, step_name: str, provider_instance) -> str:
    """
    Build a 'provider/model (default|override)' string for logging.

    Returns e.g. 'gemini/gemini-2.0-flash (default)' or
    'anthropic/claude-sonnet-4-5-20250929 (override)'.
    """
    step_cfg = get_step_config(config, step_name)
    has_override = step_cfg and (step_cfg.get("provider") or step_cfg.get("model"))
    prov_name = provider_instance.config.get("api", {}).get("provider", "unknown")
    model_name = provider_instance.model or "unknown"
    tag = "override" if has_override else "default"
    return f"{prov_name}/{model_name} ({tag})"


def count_step_units(run_dir: Path, step_name: str, pipeline: list[str]) -> dict:
    """
    Count valid and failed units for a specific step.

    Returns:
        {
            "valid": count of valid units,
            "failed": count of failed units,
            "retry_pending": count of units waiting for retry
        }
    """
    chunks_dir = run_dir / "chunks"
    valid = 0
    failed = 0
    retry_pending = 0

    if not chunks_dir.exists():
        return {"valid": valid, "failed": failed, "retry_pending": retry_pending}

    for chunk_dir in chunks_dir.iterdir():
        if not chunk_dir.is_dir():
            continue

        # Count valid units from validated file
        validated_file = chunk_dir / f"{step_name}_validated.jsonl"
        if validated_file.exists():
            try:
                with open(validated_file) as f:
                    valid += sum(1 for line in f if line.strip())
            except OSError:
                pass

        # Count failed units and retry_pending from failures file
        failures_file = chunk_dir / f"{step_name}_failures.jsonl"
        if failures_file.exists():
            try:
                with open(failures_file) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            failure = json.loads(line)
                            retry_count = failure.get("retry_count", 0)
                            # Items with retry_count < 3 are retry_pending
                            # Items with retry_count >= 3 are failed
                            if retry_count < 3:
                                retry_pending += 1
                            else:
                                failed += 1
                        except json.JSONDecodeError:
                            failed += 1
            except OSError:
                pass

    return {"valid": valid, "failed": failed, "retry_pending": retry_pending}


def run_validation_pipeline(
    results_file: Path,
    validated_file: Path,
    failures_file: Path,
    schema_path: Path | None,
    config_path: Path,
    step: str,
    log_file: Path,
    chunk_name: str,
    input_file: Path | None = None,
    timeout: int | None = None
) -> tuple[int, int]:
    """
    Run results through validation pipeline.

    Pipeline: schema_validator.py | validator.py

    Args:
        results_file: The output being validated (from LLM)
        validated_file: Where to write valid results
        failures_file: Where to write failures
        schema_path: JSON schema for structural validation
        config_path: Config with business logic rules
        step: Pipeline step name
        log_file: Run log file
        chunk_name: Name of the chunk being processed
        input_file: The step's INPUT file (for fixing failure records)
                   For first step: units.jsonl
                   For later steps: previous step's validated output
        timeout: Subprocess timeout in seconds (default: SUBPROCESS_TIMEOUT_DEFAULT)

    Returns (valid_count, failed_count)
    """
    effective_timeout = timeout if timeout is not None else SUBPROCESS_TIMEOUT_DEFAULT

    scripts_dir = Path(__file__).parent
    schema_validator = scripts_dir / "schema_validator.py"
    validator = scripts_dir / "validator.py"

    # Read raw LLM results
    try:
        with open(results_file) as f:
            raw_data = f.read()
    except Exception as e:
        log_message(log_file, "ERROR", f"[CRASH] {chunk_name}/{step}: Cannot read results file: {e}")
        return (0, 0)

    if not raw_data.strip():
        return (0, 0)

    # Load input data by unit_id for pre-merging (accumulated fields from prior steps)
    input_by_unit_id = {}
    if input_file and input_file.exists():
        try:
            with open(input_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                        uid = item.get("unit_id")
                        if uid:
                            input_by_unit_id[uid] = item
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            log_message(log_file, "WARN", f"{chunk_name}/{step}: Cannot read input file for pre-merge: {e}")

    # Pre-merge: combine raw LLM output with accumulated input data BEFORE validation.
    # This ensures validation rules can reference fields from earlier pipeline steps.
    # Merge order: input fields first (base), then raw LLM output overwrites (new data).
    merged_lines = []
    for line in raw_data.strip().split('\n'):
        if not line.strip():
            continue
        try:
            result_item = json.loads(line)
            unit_id = result_item.get("unit_id")
            if unit_id and unit_id in input_by_unit_id:
                merged = {**input_by_unit_id[unit_id], **result_item}
                merged_lines.append(json.dumps(merged))
            else:
                merged_lines.append(line)
        except json.JSONDecodeError:
            merged_lines.append(line)

    input_data = '\n'.join(merged_lines) + '\n' if merged_lines else ''

    # Log validation start with unit count
    input_count = len(merged_lines)
    log_message(log_file, "VALIDATE", f"{chunk_name}/{step}: Starting validation of {input_count} units (timeout={effective_timeout}s)")

    # Run validation stages sequentially using communicate() to avoid pipe deadlock.
    # Each stage runs to completion before the next starts. A single wall-clock
    # timeout covers the entire pipeline — remaining time is carried forward.
    pipeline_start = time.time()
    stderr = b''

    try:
        if schema_path and schema_path.exists():
            # Stage 1: Schema validation
            log_message(log_file, "VALIDATE", f"{chunk_name}/{step}: Running schema validation...")
            p1 = subprocess.Popen(
                [sys.executable, str(schema_validator), "--schema", str(schema_path), "--quiet"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            _active_subprocesses.append(p1)
            try:
                p1_stdout, p1_stderr = p1.communicate(input=input_data.encode(), timeout=effective_timeout)
            except subprocess.TimeoutExpired:
                p1.kill()
                p1.communicate(timeout=5)
                elapsed = time.time() - pipeline_start
                log_message(log_file, "ERROR", f"Validation timeout for {chunk_name}/{step} during schema validation ({elapsed:.0f}s elapsed, limit={effective_timeout}s)")
                return (0, 0)
            finally:
                if p1 in _active_subprocesses:
                    _active_subprocesses.remove(p1)

            # Count schema validation results for progress logging
            schema_passed = sum(1 for line in p1_stdout.decode().strip().split('\n') if line.strip())
            # Only count lines that are valid JSON failure records (skip telemetry like [COERCE])
            schema_failed = 0
            for _sline in p1_stderr.decode().strip().split('\n'):
                if not _sline.strip():
                    continue
                try:
                    json.loads(_sline)
                    schema_failed += 1
                except json.JSONDecodeError:
                    pass  # Telemetry line, not a failure record
            elapsed_s1 = time.time() - pipeline_start
            log_message(log_file, "VALIDATE", f"{chunk_name}/{step}: Schema validation complete: {schema_passed} passed, {schema_failed} failed ({elapsed_s1:.1f}s)")

            # Accumulate stderr (schema failures)
            stderr = p1_stderr

            # Reconstruct full merged data for Phase 2 validation. The schema
            # validator preserves all fields, but we merge from the original step
            # input to ensure accumulated fields from prior pipeline steps are available.
            # Use the unit_ids that passed schema validation to select the
            # corresponding ORIGINAL pre-merged lines (which have all fields).
            passed_unit_ids = set()
            for line in p1_stdout.decode().strip().split('\n'):
                if line.strip():
                    try:
                        item = json.loads(line)
                        uid = item.get('unit_id')
                        if uid:
                            passed_unit_ids.add(uid)
                    except json.JSONDecodeError:
                        pass

            p2_input_lines = []
            for line in input_data.strip().split('\n'):
                if line.strip():
                    try:
                        item = json.loads(line)
                        if item.get('unit_id') in passed_unit_ids:
                            p2_input_lines.append(line)
                    except json.JSONDecodeError:
                        pass
            p2_input = ('\n'.join(p2_input_lines) + '\n').encode() if p2_input_lines else b''

            # Stage 2: Business logic validation on schema-valid records (with full merged data)
            remaining_timeout = max(10, effective_timeout - (time.time() - pipeline_start))
            log_message(log_file, "VALIDATE", f"{chunk_name}/{step}: Running business logic validation on {len(p2_input_lines)} records ({int(remaining_timeout)}s remaining)...")
            p2 = subprocess.Popen(
                [sys.executable, str(validator), "--config", str(config_path), "--step", step, "--quiet"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            _active_subprocesses.append(p2)
            try:
                stdout, p2_stderr = p2.communicate(input=p2_input, timeout=remaining_timeout)
            except subprocess.TimeoutExpired:
                p2.kill()
                p2.communicate(timeout=5)
                elapsed = time.time() - pipeline_start
                log_message(log_file, "ERROR", f"Validation timeout for {chunk_name}/{step} during business logic validation ({elapsed:.0f}s elapsed, limit={effective_timeout}s)")
                return (0, 0)
            finally:
                if p2 in _active_subprocesses:
                    _active_subprocesses.remove(p2)

            # Accumulate stderr (business logic failures)
            stderr += p2_stderr

        else:
            # Single validator — no schema stage
            log_message(log_file, "VALIDATE", f"{chunk_name}/{step}: Running business logic validation (no schema)...")
            p1 = subprocess.Popen(
                [sys.executable, str(validator), "--config", str(config_path), "--step", step, "--quiet"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            _active_subprocesses.append(p1)
            try:
                stdout, stderr = p1.communicate(input=input_data.encode(), timeout=effective_timeout)
            except subprocess.TimeoutExpired:
                p1.kill()
                p1.communicate(timeout=5)
                elapsed = time.time() - pipeline_start
                log_message(log_file, "ERROR", f"Validation timeout for {chunk_name}/{step} ({elapsed:.0f}s elapsed, limit={effective_timeout}s)")
                return (0, 0)
            finally:
                if p1 in _active_subprocesses:
                    _active_subprocesses.remove(p1)

        total_elapsed = time.time() - pipeline_start
        log_message(log_file, "VALIDATE", f"{chunk_name}/{step}: Subprocess pipeline complete ({total_elapsed:.1f}s)")

    except Exception as e:
        log_message(log_file, "ERROR", f"Validation pipeline error for {chunk_name}/{step}: {e}")
        return (0, 0)

    # Process validation results with crash protection
    # Note: data was pre-merged before validation, so validated output already
    # contains accumulated fields from prior steps. Just write directly.
    try:
        # Write validated output (already pre-merged with input data)
        stdout_text = stdout.decode()
        merge_counter = 0
        merge_last_log_time = time.time()
        with open(validated_file, 'w') as f:
            for line in stdout_text.strip().split('\n'):
                if not line.strip():
                    continue
                try:
                    json.loads(line)  # Validate it's valid JSON
                    merge_counter += 1
                    if merge_counter % 10 == 0:
                        now = time.time()
                        duration = now - merge_last_log_time
                        log_message(log_file, "VALIDATE", f"{chunk_name}: Writing unit {merge_counter}/{input_count} (Last 10 took {duration:.2f}s)")
                        merge_last_log_time = now
                    f.write(line + '\n')
                except json.JSONDecodeError:
                    f.write(line + '\n')

        # Process failures: fix the input field to contain step's INPUT, not failed OUTPUT
        stderr_text = stderr.decode()
        failed_count = 0
        coerce_lines = []  # Collect [COERCE] telemetry for summarized logging

        # Write failures with corrected input field
        with open(failures_file, 'w') as f:
            for line in stderr_text.strip().split('\n'):
                if not line.strip():
                    continue

                try:
                    failure = json.loads(line)
                except json.JSONDecodeError:
                    # Not JSON — telemetry or log line, NOT a failure record.
                    # Do NOT write to failures JSONL under any circumstances.
                    stripped = line.strip()
                    if stripped.startswith('[COERCE]'):
                        # Collect for summarized logging (avoid flooding at scale)
                        coerce_lines.append(stripped)
                    elif stripped.startswith('['):
                        # Other telemetry prefix like [WARN], [INFO]
                        log_message(log_file, "VALIDATE", stripped)
                    else:
                        log_message(log_file, "WARN", f"Unexpected non-JSON stderr: {stripped[:200]}")
                    continue

                # Check if this is a failure record (has unit_id and errors)
                unit_id = failure.get("unit_id")
                if unit_id and "errors" in failure:
                    # Fix the input field: use step's INPUT, not the failed OUTPUT
                    if unit_id in input_by_unit_id:
                        input_data_for_unit = input_by_unit_id[unit_id]
                        # Preserve original LLM text from _raw_text field.
                        # Check (in priority order):
                        #   1. failure["input"]["_raw_text"] — the merged output dict from the validator
                        #   2. failure["_raw_text"] — top-level (e.g. from schema_validator parse failure)
                        #   3. input_data_for_unit["_raw_text"] — step input (unlikely but safe)
                        validator_input = failure.get("input")
                        raw_text = None
                        if isinstance(validator_input, dict):
                            raw_text = validator_input.get("_raw_text")
                        if raw_text is None:
                            raw_text = failure.get("_raw_text")
                        if raw_text is None:
                            raw_text = input_data_for_unit.get("_raw_text")
                        if raw_text is not None:
                            failure["raw_response"] = raw_text

                        failure["input"] = input_data_for_unit
                        # Preserve retry_count from input for retry tracking
                        if "retry_count" in input_data_for_unit:
                            failure["retry_count"] = input_data_for_unit["retry_count"]
                    # else: keep original input (might already be correct for first step)

                f.write(json.dumps(failure) + '\n')
                failed_count += 1

        # Log coercion telemetry: individual lines for small counts, summary for large
        if coerce_lines:
            if len(coerce_lines) <= 5:
                for cl in coerce_lines:
                    log_message(log_file, "VALIDATE", cl)
            else:
                log_message(log_file, "VALIDATE", f"Type coercion applied: {len(coerce_lines)} fields coerced")

        # Count results and track which unit_ids we've seen
        valid_unit_ids = set()
        failed_unit_ids = set()

        for line in stdout.decode().strip().split('\n'):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                uid = item.get("unit_id")
                if uid:
                    valid_unit_ids.add(uid)
            except json.JSONDecodeError:
                pass

        for line in stderr_text.strip().split('\n'):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                uid = item.get("unit_id")
                if uid:
                    failed_unit_ids.add(uid)
            except json.JSONDecodeError:
                pass

        valid_count = len(valid_unit_ids)

        # Verify completeness: check for missing records
        missing_count = input_count - valid_count - failed_count

        if missing_count > 0:
            log_message(
                log_file, "WARN",
                f"{chunk_name}: Validation count mismatch - input={input_count}, valid={valid_count}, failed={failed_count}, missing={missing_count}"
            )

            # Generate synthetic failure records for missing items
            # These are items that were lost during the validation pipeline
            all_seen = valid_unit_ids | failed_unit_ids
            with open(failures_file, 'a') as f:
                for uid, input_item in input_by_unit_id.items():
                    if uid not in all_seen:
                        synthetic_failure = {
                            "unit_id": uid,
                            "failure_stage": "pipeline_internal",
                            "step": step,
                            "input": input_item,
                            "errors": [{"rule": "pipeline_internal", "message": "Record lost during validation pipeline"}],
                            "retry_count": input_item.get("retry_count", 0)
                        }
                        f.write(json.dumps(synthetic_failure) + '\n')
                        failed_count += 1

        log_message(log_file, "VALIDATE", f"{chunk_name}/{step}: Validation complete: {valid_count} valid, {failed_count} failed")
        return (valid_count, failed_count)

    except Exception as e:
        log_message(log_file, "ERROR", f"[CRASH] {chunk_name}/{step}: Validation output processing crashed: {e}")
        import traceback
        log_message(log_file, "ERROR", f"[CRASH] {chunk_name}/{step}: Traceback: {traceback.format_exc()}")
        return (0, 0)


def prepare_prompts(
    units_file: Path,
    prompts_file: Path,
    config_path: Path,
    step: str,
    timeout: int | None = None
) -> tuple[bool, str]:
    """
    Prepare prompts using octobatch_step.py.

    If config has processing.expressions, evaluates them for each unit
    before template rendering. Uses _repetition_seed for reproducibility.

    Args:
        timeout: Subprocess timeout in seconds (default: SUBPROCESS_TIMEOUT_DEFAULT)

    Returns (success, error_message). Error message is empty on success.
    """
    effective_timeout = timeout if timeout is not None else SUBPROCESS_TIMEOUT_DEFAULT
    scripts_dir = Path(__file__).parent
    octobatch = scripts_dir / "octobatch_step.py"

    # Load config to check for expressions
    with open(config_path) as f:
        config = yaml.safe_load(f)

    expressions = get_expressions(config)

    # Read and optionally process units
    with open(units_file) as f:
        input_lines = f.readlines()

    # If expressions defined, evaluate them for each unit
    if expressions:
        processed_lines = []
        for line in input_lines:
            line = line.strip()
            if not line:
                continue
            try:
                unit = json.loads(line)
                # Get seed - use _repetition_seed if present, otherwise hash of unit_id + step_name
                seed = unit.get("_repetition_seed", int(hashlib.sha256((unit.get("unit_id", "") + step).encode()).hexdigest(), 16) & 0x7FFFFFFF)

                # Evaluate expressions with unit data as context
                try:
                    expr_results = evaluate_expressions(expressions, unit, seed)
                    # Add expression results to unit data for template rendering
                    unit.update(expr_results)
                except ValueError as e:
                    return False, f"Expression evaluation failed for {unit.get('unit_id')}: {e}"

                processed_lines.append(json.dumps(unit))
            except json.JSONDecodeError as e:
                return False, f"Invalid JSON in units file: {e}"

        input_data = "\n".join(processed_lines) + "\n"
    else:
        input_data = "".join(input_lines)

    try:
        result = subprocess.run(
            [sys.executable, str(octobatch), "--config", str(config_path), "--step", step],
            input=input_data,
            capture_output=True,
            text=True,
            timeout=effective_timeout
        )
    except subprocess.TimeoutExpired:
        return False, f"Prompt generation timed out (>{effective_timeout}s)"

    if result.returncode != 0:
        # Capture stderr for error message
        error_msg = result.stderr.strip() if result.stderr else "Unknown error"
        return False, error_msg

    with open(prompts_file, 'w') as f:
        f.write(result.stdout)

    return True, ""


def is_run_terminal(manifest: dict, max_retries: int) -> bool:
    """
    Check if all chunks in a run are in terminal states.

    A chunk is terminal if:
    - state == "VALIDATED", OR
    - state == "FAILED" AND retry_count >= max_retries

    Args:
        manifest: The loaded MANIFEST.json
        max_retries: Maximum retry attempts configured for this run

    Returns:
        True if all chunks are terminal, False otherwise
    """
    chunks = manifest.get("chunks", {})

    if not chunks:
        return False

    for chunk_name, chunk_data in chunks.items():
        state = chunk_data.get("state", "")

        if state == "VALIDATED":
            continue

        if state == "FAILED":
            retries = chunk_data.get("retries", 0)
            if retries >= max_retries:
                continue
            # Failed but has retries remaining
            return False

        # Any other state (PENDING, SUBMITTED, COMPLETE, etc.) is not terminal
        return False

    return True


def run_scope_run_step(
    run_dir: Path,
    step_config: dict,
    config_path: Path,
    log_file: Path
) -> bool:
    """
    Execute a single run-scope pipeline step.

    Args:
        run_dir: Path to the run directory
        step_config: The step configuration dict (from pipeline.steps)
        config_path: Path to the config file
        log_file: Path to RUN_LOG.txt

    Returns:
        True if script succeeded, False otherwise
    """
    step_name = step_config.get("name", "unknown")
    script_path = step_config.get("script")

    if not script_path:
        log_message(log_file, "RUN_STEP", f"Step '{step_name}' has no script configured")
        return False

    scripts_dir = Path(__file__).parent

    # Resolve script path relative to project root
    if not Path(script_path).is_absolute():
        full_script_path = scripts_dir.parent / script_path
    else:
        full_script_path = Path(script_path)

    if not full_script_path.exists():
        log_message(log_file, "RUN_STEP", f"Script not found for step '{step_name}': {script_path}")
        return False

    log_message(log_file, "RUN_STEP", f"Running step '{step_name}': {script_path}")

    try:
        result = subprocess.run(
            [
                sys.executable,
                str(full_script_path),
                "--run-dir", str(run_dir),
                "--step-name", step_name,
                "--config", str(config_path)
            ],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_DEFAULT
        )

        if result.returncode == 0:
            # Print any stderr warnings even on success
            if result.stderr and result.stderr.strip():
                stderr_lines = result.stderr.strip().split('\n')[:10]
                for line in stderr_lines:
                    log_message(log_file, "RUN_STEP", f"{step_name}: {line}")
            log_message(log_file, "RUN_STEP", f"Completed step '{step_name}'")
            return True
        else:
            log_message(log_file, "RUN_STEP", f"Failed step '{step_name}' (exit {result.returncode})")
            if result.stderr:
                log_message(log_file, "RUN_STEP", f"  stderr: {result.stderr.strip()}")
            return False

    except subprocess.TimeoutExpired:
        log_message(log_file, "RUN_STEP", f"Timeout for step '{step_name}' (>{SUBPROCESS_TIMEOUT_DEFAULT}s)")
        return False
    except Exception as e:
        log_message(log_file, "RUN_STEP", f"Error running step '{step_name}': {e}")
        return False


def build_run_status(
    run_dir: Path,
    manifest: dict,
    config: dict,
    activity: dict | None = None,
    warnings: list | None = None,
    tick_errors: int = 0
) -> dict:
    """
    Build the complete status JSON for a run.

    Used by both --status and --tick to ensure consistent output format.

    Args:
        run_dir: Path to run directory
        manifest: Loaded manifest dict
        config: Loaded config dict
        activity: Optional activity dict from tick (polled, collected, submitted)
                  If None, defaults to zeros.
        warnings: Optional list of warning dicts from tick processing
                  If None, will generate warnings based on current state.
        tick_errors: Number of errors that occurred during this tick (for warning)

    Returns:
        Complete status dict with all telemetry fields
    """
    from scripts.providers import get_provider, ProviderError

    chunks = manifest["chunks"]
    pipeline = manifest["pipeline"]

    # Get API config
    api_config = config.get("api", {})
    provider_name = api_config.get("provider", "unknown")
    model = api_config.get("model", "unknown")

    # Initialize provider for cost calculations
    provider = None
    try:
        provider = get_provider(config)
    except (ProviderError, Exception):
        pass  # Provider unavailable - costs will be None

    # Get monitoring config
    monitoring_config = config.get("monitoring", {})
    warning_config = monitoring_config.get("warnings", {})
    failure_threshold = warning_config.get("failure_rate_threshold", 0.0)
    long_running_mins = warning_config.get("long_running_minutes")
    high_token_threshold = warning_config.get("high_token_usage")

    # Current time
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Count current states
    inflight = 0
    pending = 0
    completed = 0
    failed = 0

    for chunk_data in chunks.values():
        step, status = parse_state(chunk_data["state"])
        if status == "SUBMITTED":
            inflight += 1
        elif status == "PENDING":
            pending += 1
        elif status == "VALIDATED":
            completed += 1
        elif status == "FAILED":
            failed += 1

    # Count total units and valid/failed across all chunks
    total_units = sum(c.get("items", 0) for c in chunks.values())
    total_valid = sum(c.get("valid", 0) for c in chunks.values())
    total_failed = sum(c.get("failed", 0) for c in chunks.values())

    # Compute timing
    run_started = manifest.get("created")
    elapsed_seconds = 0
    if run_started:
        try:
            start_dt = datetime.fromisoformat(run_started.replace("Z", "+00:00"))
            elapsed_seconds = int((now - start_dt).total_seconds())
        except (ValueError, TypeError):
            pass

    # Calculate throughput
    completed_units = total_valid + total_failed
    items_per_hour = None
    estimated_remaining_seconds = None
    estimated_remaining_human = None

    if elapsed_seconds > 0 and completed_units > 0:
        elapsed_hours = elapsed_seconds / 3600
        items_per_hour = round(completed_units / elapsed_hours, 1)

        # Estimate remaining time
        remaining_units = total_units - completed_units
        if remaining_units > 0 and items_per_hour > 0:
            remaining_hours = remaining_units / items_per_hour
            estimated_remaining_seconds = int(remaining_hours * 3600)
            estimated_remaining_human = format_elapsed_time(estimated_remaining_seconds)

    # Build per-step breakdown with comprehensive stats
    steps_breakdown: dict[str, dict] = {}
    chunks_dir = run_dir / "chunks"
    num_chunks = len(chunks)

    # Initialize chunk state counts and unit counts per step
    chunk_states: dict[str, dict[str, int]] = {}
    step_current_units: dict[str, int] = {}
    for step_name in pipeline:
        chunk_states[step_name] = {
            "pending": 0,
            "submitted": 0,
            "completed": 0,
            "validated": 0,
            "failed": 0
        }
        step_current_units[step_name] = 0

    # Count chunk states per step and track units at each step
    for chunk_name, chunk_data in chunks.items():
        state = chunk_data.get("state", "")
        step, status = parse_state(state)
        chunk_items = chunk_data.get("items", 0)

        if status == "PENDING" and step in chunk_states:
            chunk_states[step]["pending"] += 1
            step_current_units[step] += chunk_items
        elif status == "SUBMITTED" and step in chunk_states:
            chunk_states[step]["submitted"] += 1
            step_current_units[step] += chunk_items
        elif status == "COMPLETE" and step in chunk_states:
            chunk_states[step]["completed"] += 1
            step_current_units[step] += chunk_items
        elif status == "VALIDATED" and pipeline:
            # VALIDATED chunks have completed all steps - count on final step
            final_step = pipeline[-1]
            chunk_states[final_step]["validated"] += 1
            step_current_units[final_step] += chunk_items
        elif status == "FAILED":
            # For FAILED chunks, determine which step failed by checking
            # which step's failures file exists (last one with failures)
            chunk_dir = chunks_dir / chunk_name
            failed_step = None
            if chunk_dir.exists():
                for s in reversed(pipeline):
                    failures_file = chunk_dir / f"{s}_failures.jsonl"
                    if failures_file.exists():
                        failed_step = s
                        break
            if failed_step and failed_step in chunk_states:
                chunk_states[failed_step]["failed"] += 1
                step_current_units[failed_step] += chunk_items
            elif pipeline:
                # Fallback to first step if no failures file found
                chunk_states[pipeline[0]]["failed"] += 1
                step_current_units[pipeline[0]] += chunk_items

    # Aggregate token counts per step from results files
    step_tokens: dict[str, dict[str, int]] = {}
    for step_name in pipeline:
        step_tokens[step_name] = {"input": 0, "output": 0}

    for chunk_name, chunk_data in chunks.items():
        chunk_dir = chunks_dir / chunk_name
        if not chunk_dir.exists():
            continue

        for step_name in pipeline:
            # Count tokens for this step from results file metadata
            results_file = chunk_dir / f"{step_name}_results.jsonl"
            if results_file.exists():
                try:
                    with open(results_file) as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                result = json.loads(line)
                                metadata = result.get("_metadata", {})
                                step_tokens[step_name]["input"] += metadata.get("input_tokens", 0)
                                step_tokens[step_name]["output"] += metadata.get("output_tokens", 0)
                            except json.JSONDecodeError:
                                continue
                except OSError:
                    pass

    # Build comprehensive per-step breakdown
    for step_name in pipeline:
        # Get unit counts for this step
        unit_counts = count_step_units(run_dir, step_name, pipeline)

        # Get error breakdown for this step
        error_breakdown = count_step_failures(run_dir, step_name)

        # Calculate step cost using provider's pricing
        input_tokens = step_tokens[step_name]["input"]
        output_tokens = step_tokens[step_name]["output"]
        step_cost = compute_step_cost(input_tokens, output_tokens, provider)

        # Calculate step throughput based on validated chunks for this step
        validated_chunks = chunk_states[step_name]["validated"]
        valid_units = unit_counts["valid"]

        step_units_per_hour = None
        step_chunks_per_hour = None
        if elapsed_seconds > 0 and valid_units > 0:
            elapsed_hours = elapsed_seconds / 3600
            step_units_per_hour = round(valid_units / elapsed_hours, 1)
        if elapsed_seconds > 0 and validated_chunks > 0:
            elapsed_hours = elapsed_seconds / 3600
            step_chunks_per_hour = round(validated_chunks / elapsed_hours, 2)

        # Calculate current_chunks as sum of all chunk states for this step
        step_chunk_states = chunk_states[step_name]
        current_chunks = (
            step_chunk_states["pending"] +
            step_chunk_states["submitted"] +
            step_chunk_states["completed"] +
            step_chunk_states["validated"] +
            step_chunk_states["failed"]
        )

        steps_breakdown[step_name] = {
            "current_chunks": current_chunks,
            "current_units": step_current_units[step_name],
            "chunks": chunk_states[step_name],
            "units": unit_counts,
            "tokens": step_tokens[step_name],
            "cost": {
                "estimated_usd": step_cost
            },
            "timing": {
                "avg_batch_seconds": None,  # Would need batch timing data in manifest
                "total_processing_seconds": None  # Would need per-step timing tracking
            },
            "errors": error_breakdown,
            "throughput": {
                "units_per_hour": step_units_per_hour,
                "chunks_per_hour": step_chunks_per_hour
            }
        }

    # Build warnings list - start with passed warnings or empty list
    if warnings is None:
        warnings = []

    # Always add state-based warnings
    # Check for chunk failure warnings based on threshold
    for chunk_name, chunk_data in chunks.items():
        chunk_items = chunk_data.get("items", 0)
        chunk_failed = chunk_data.get("failed", 0)
        if chunk_items > 0 and chunk_failed > 0:
            failure_rate = chunk_failed / chunk_items
            if failure_threshold is not None and failure_rate >= failure_threshold:
                warnings.append({
                    "code": "CHUNK_HAS_FAILURES",
                    "message": f"{chunk_name} has {chunk_failed} failed items ({failure_rate:.0%})",
                    "chunk": chunk_name,
                    "failed": chunk_failed,
                    "rate": round(failure_rate, 2)
                })

        # Check for high token usage
        if high_token_threshold is not None:
            chunk_input = chunk_data.get("input_tokens", 0)
            chunk_output = chunk_data.get("output_tokens", 0)
            chunk_total_tokens = chunk_input + chunk_output
            if chunk_total_tokens > high_token_threshold:
                warnings.append({
                    "code": "HIGH_TOKEN_USAGE",
                    "message": f"{chunk_name} used {chunk_total_tokens:,} tokens (threshold: {high_token_threshold:,})",
                    "chunk": chunk_name,
                    "tokens": chunk_total_tokens,
                    "threshold": high_token_threshold
                })

        # Check for long-running chunks
        if long_running_mins is not None:
            submitted_at = chunk_data.get("submitted_at")
            step, status = parse_state(chunk_data["state"])
            if status == "SUBMITTED" and submitted_at:
                try:
                    submitted_dt = datetime.fromisoformat(submitted_at.replace("Z", "+00:00"))
                    minutes_elapsed = (now - submitted_dt).total_seconds() / 60
                    if minutes_elapsed > long_running_mins:
                        warnings.append({
                            "code": "LONG_RUNNING_CHUNK",
                            "message": f"{chunk_name} has been processing for {minutes_elapsed:.1f} minutes (threshold: {long_running_mins})",
                            "chunk": chunk_name,
                            "minutes": round(minutes_elapsed, 1),
                            "threshold": long_running_mins
                        })
                except (ValueError, TypeError):
                    pass

    # Add warning if provider unavailable for cost estimation
    if provider is None:
        warnings.append({
            "code": "PROVIDER_UNAVAILABLE",
            "message": "Provider not available for cost estimation (check API key)"
        })

    # Add warning for tick errors if any
    if tick_errors > 0:
        warnings.append({
            "code": "TICK_ERRORS",
            "message": f"{tick_errors} error{'s' if tick_errors != 1 else ''} occurred during this tick",
            "count": tick_errors
        })

    # Build chunks array with detailed info
    chunks_array = []
    for chunk_name in sorted(chunks.keys()):
        chunk_data = chunks[chunk_name]
        step, status = parse_state(chunk_data["state"])

        chunk_info = {
            "name": chunk_name,
            "state": chunk_data["state"],
            "status": status.lower() if status else "unknown",
            "items": chunk_data.get("items", 0),
            "valid": chunk_data.get("valid", 0),
            "failed": chunk_data.get("failed", 0)
        }

        # Add step if present
        if step:
            chunk_info["step"] = step

        # Add provider_status if present (for debugging)
        if chunk_data.get("provider_status"):
            chunk_info["provider_status"] = chunk_data["provider_status"]

        # Add token info if present
        if chunk_data.get("input_tokens"):
            chunk_info["input_tokens"] = chunk_data["input_tokens"]
        if chunk_data.get("output_tokens"):
            chunk_info["output_tokens"] = chunk_data["output_tokens"]

        chunks_array.append(chunk_info)

    # Get token totals from manifest metadata
    metadata = manifest.get("metadata", {})
    initial_input = metadata.get("initial_input_tokens", 0)
    initial_output = metadata.get("initial_output_tokens", 0)
    retry_input = metadata.get("retry_input_tokens", 0)
    retry_output = metadata.get("retry_output_tokens", 0)
    total_input = initial_input + retry_input
    total_output = initial_output + retry_output

    # Compute costs using provider's pricing
    total_cost = None
    retry_cost = None
    if provider is not None:
        try:
            total_cost = round(provider.estimate_cost(total_input, total_output, is_batch=True), 6)
            retry_cost = round(provider.estimate_cost(retry_input, retry_output, is_batch=True), 6)
        except Exception:
            pass

    # Default activity if not provided
    if activity is None:
        activity = {"polled": 0, "collected": 0, "submitted": 0}

    # Build enhanced status output
    # Important fields at bottom so they're visible without scrolling
    return {
        "timestamp": now_str,
        "run_id": run_dir.name,
        "pipeline": pipeline,

        "chunks": chunks_array,

        "throughput": {
            "completed_units": completed_units,
            "items_per_hour": items_per_hour,
            "estimated_remaining_seconds": estimated_remaining_seconds,
            "estimated_remaining_human": estimated_remaining_human
        },

        "monitoring": {
            "failure_rate_threshold": failure_threshold,
            "long_running_minutes": long_running_mins,
            "high_token_usage": high_token_threshold
        },

        "cost": {
            "initial_input_tokens": initial_input,
            "initial_output_tokens": initial_output,
            "retry_input_tokens": retry_input,
            "retry_output_tokens": retry_output,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "estimated_cost_usd": total_cost,
            "retry_cost_usd": retry_cost,
            "configured": provider is not None,
            "message": None if provider else "Provider unavailable for cost estimation"
        },

        "timing": {
            "run_started": run_started,
            "elapsed_seconds": elapsed_seconds,
            "elapsed_human": format_elapsed_time(elapsed_seconds)
        },

        "system": {
            "provider": provider_name,
            "model": model
        },

        # These last so they're visible at bottom of terminal output
        "warnings": warnings,

        "failures": build_failures_map(run_dir, manifest),

        "activity": activity,

        "steps": steps_breakdown,

        "summary": {
            "total_units": total_units,
            "valid": total_valid,
            "failed": total_failed,
            "pending": pending,
            "inflight": inflight
        }
    }


def tick_run(run_dir: Path, max_retries: int = 5) -> dict:
    """
    Execute one tick of the orchestration loop.

    Args:
        run_dir: Path to the run directory
        max_retries: Maximum retry attempts per unit (for terminal check)

    Returns enhanced status dict for JSON output with:
    - timestamp: Current UTC timestamp
    - run_id: Name of run directory
    - summary: Unit counts (total, valid, failed, pending, inflight)
    - activity: What happened this tick (polled, collected, submitted)
    - chunks: Array of per-chunk status details
    - cost: Token counts and estimated cost
    - timing: Run timing information
    - system: Provider and model info
    """
    # Validate run directory
    if not run_dir.exists():
        print(f"Error: Run directory not found: {run_dir}", file=sys.stderr)
        return {"error": "Run directory not found"}

    manifest_path = run_dir / "MANIFEST.json"
    if not manifest_path.exists():
        print(f"Error: MANIFEST.json not found in {run_dir}", file=sys.stderr)
        return {"error": "MANIFEST.json not found"}

    log_file = run_dir / "RUN_LOG.txt"

    # Load manifest
    manifest = load_manifest(run_dir)
    pipeline = manifest["pipeline"]
    chunks = manifest["chunks"]

    # Initialize or load metadata section with migration for old format
    if "metadata" not in manifest:
        manifest["metadata"] = {
            "initial_input_tokens": 0,
            "initial_output_tokens": 0,
            "retry_input_tokens": 0,
            "retry_output_tokens": 0
        }
    else:
        # Migrate old format (total_*) to new format (initial_* + retry_*)
        metadata = manifest["metadata"]
        if "total_input_tokens" in metadata and "initial_input_tokens" not in metadata:
            # Old format - treat totals as initial (no way to know retry breakdown)
            metadata["initial_input_tokens"] = metadata.pop("total_input_tokens", 0)
            metadata["initial_output_tokens"] = metadata.pop("total_output_tokens", 0)
            metadata["retry_input_tokens"] = 0
            metadata["retry_output_tokens"] = 0
            # Remove old cost_usd field (will be recomputed)
            metadata.pop("cost_usd", None)
        # Ensure all fields exist
        metadata.setdefault("initial_input_tokens", 0)
        metadata.setdefault("initial_output_tokens", 0)
        metadata.setdefault("retry_input_tokens", 0)
        metadata.setdefault("retry_output_tokens", 0)
    metadata = manifest["metadata"]

    # Track warnings for this tick
    warnings = []

    # Load config from run directory
    config_path = run_dir / manifest["config"]
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Get API config for provider initialization
    api_config = config.get("api", {})
    max_inflight = api_config.get("max_inflight_batches", 10)

    # Per-step provider cache for batch operations (supports step-level overrides)
    _step_provider_cache = {}

    def get_provider_for_step(step_name: str):
        if step_name not in _step_provider_cache:
            _step_provider_cache[step_name] = get_step_provider(config, step_name, manifest)
        return _step_provider_cache[step_name]

    # Validate that at least the global provider works
    try:
        get_provider(config)
    except (ProviderError, ValueError, ImportError) as e:
        log_message(log_file, "ERROR", f"Failed to initialize provider: {e}")
        print(f"Error: Failed to initialize provider: {e}", file=sys.stderr)
        return {"error": str(e)}

    # Track activity
    polled = 0
    collected = 0
    submitted = 0
    errors = 0
    state_changes = 0  # Chunks that changed poll status this tick

    # Snapshot valid/failed counts before this tick for delta computation
    _pre_valid = sum(c.get("valid", 0) for c in chunks.values())
    _pre_failed = sum(c.get("failed", 0) for c in chunks.values())

    # Track previous poll status per chunk to suppress repeated log lines
    prev_poll_status = {}
    prev_poll_status_file = run_dir / ".poll_status_cache.json"
    _last_activity_file = run_dir / ".last_activity_ts"
    try:
        if prev_poll_status_file.exists():
            with open(prev_poll_status_file) as f:
                prev_poll_status = json.load(f)
    except Exception:
        prev_poll_status = {}

    # Load last activity timestamp for heartbeat
    _now = time.time()
    try:
        if _last_activity_file.exists():
            _last_activity_ts = float(_last_activity_file.read_text().strip())
        else:
            _last_activity_ts = _now
    except Exception:
        _last_activity_ts = _now

    # Track tokens collected this tick (separate initial vs retry)
    tick_initial_input_tokens = 0
    tick_initial_output_tokens = 0
    tick_retry_input_tokens = 0
    tick_retry_output_tokens = 0

    # Count current states
    inflight = 0
    pending = 0
    completed = 0
    failed = 0

    # Phase 1: Poll in-flight batches
    # Pre-count submitted chunks for progress logging
    submitted_chunks = [(name, data) for name, data in chunks.items()
                        if parse_state(data["state"])[1] == "SUBMITTED"]
    total_submitted = len(submitted_chunks)

    for poll_idx, (chunk_name, chunk_data) in enumerate(submitted_chunks):
        step, status = parse_state(chunk_data["state"])
        inflight += 1
        batch_id = chunk_data.get("batch_id")

        # Compute elapsed time since submission
        elapsed_str = ""
        submitted_at_str = chunk_data.get("submitted_at")
        if submitted_at_str:
            try:
                submitted_at_dt = datetime.strptime(submitted_at_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                elapsed_secs = (datetime.now(timezone.utc) - submitted_at_dt).total_seconds()
                if elapsed_secs >= 3600:
                    elapsed_str = f", {int(elapsed_secs // 3600)}h{int((elapsed_secs % 3600) // 60)}m elapsed"
                elif elapsed_secs >= 60:
                    elapsed_str = f", {int(elapsed_secs // 60)}m elapsed"
                else:
                    elapsed_str = f", {int(elapsed_secs)}s elapsed"
            except (ValueError, TypeError):
                pass

        # Log progress for large batch operations
        if total_submitted > PROGRESS_LOG_THRESHOLD and poll_idx % 10 == 0:
            log_message(log_file, "PROGRESS", f"Polling chunk {poll_idx + 1}/{total_submitted}{elapsed_str}")

        if not batch_id:
            log_message(log_file, "WARN", f"{chunk_name}: SUBMITTED but no batch_id")
            continue

        try:
            poll_result = get_provider_for_step(step).get_batch_status(batch_id)
            polled += 1

            poll_status = poll_result["status"]  # BatchStatus enum
            progress = poll_result.get("progress", "?")

            # Capture provider_status for debugging
            raw_provider_status = poll_result.get("provider_status")
            if raw_provider_status:
                chunk_data["provider_status"] = raw_provider_status

            # Log with status name for readability — only when status changes
            status_name = poll_status.value if hasattr(poll_status, 'value') else str(poll_status)
            poll_key = f"{status_name}:{progress}"
            if poll_key != prev_poll_status.get(chunk_name):
                progress_detail = f"{progress}" if progress != "?" else "waiting"
                log_message(log_file, "POLL", f"{chunk_name}: {chunk_data['state']} -> {status_name} ({progress_detail}{elapsed_str})")
                _prov_name = config.get("api", {}).get("provider", "unknown")
                trace_log(run_dir, f"[BATCH] POLL   {_prov_name} {chunk_name} batch_id={batch_id} | {status_name}")
                prev_poll_status[chunk_name] = poll_key
                state_changes += 1

            if poll_status == BatchStatus.COMPLETED:
                # Collect results
                chunk_dir = run_dir / "chunks" / chunk_name
                results_file = chunk_dir / f"{step}_results.jsonl"

                # Log progress for collecting when many chunks
                if total_submitted > PROGRESS_LOG_THRESHOLD:
                    log_message(log_file, "PROGRESS", f"Collecting results for chunk {poll_idx + 1}/{total_submitted}")

                try:
                    # Download results using provider
                    results, batch_metadata = get_provider_for_step(step).download_batch_results(batch_id)

                    # Write results in the format the orchestrator expects
                    # Collect non-JSON responses as pipeline_internal failures
                    batch_non_json_failures = []
                    with open(results_file, 'w') as f:
                        for result in results:
                            output = {"unit_id": result["unit_id"]}
                            # Preserve original LLM text before parsing
                            raw_text = result.get("content", "")
                            if raw_text:
                                output["_raw_text"] = raw_text
                                parsed = parse_json_response(raw_text)
                                if parsed and isinstance(parsed, dict):
                                    output.update(parsed)
                                else:
                                    # Non-JSON response — record as pipeline_internal failure
                                    batch_non_json_failures.append({
                                        "unit_id": result["unit_id"],
                                        "failure_stage": "pipeline_internal",
                                        "raw_response": raw_text,
                                        "errors": [{"path": "$", "rule": "pipeline_internal", "message": "LLM response is not valid JSON"}],
                                        "retry_count": 0
                                    })
                                    continue  # Don't write to results file
                            if result.get("error"):
                                output["error"] = result["error"]
                            output["_metadata"] = {
                                "input_tokens": result.get("input_tokens", 0),
                                "output_tokens": result.get("output_tokens", 0),
                                "model": get_provider_for_step(step).model
                            }
                            f.write(json.dumps(output) + "\n")

                    result_count = len(results)
                    collected += 1
                    log_message(log_file, "COLLECT", f"{chunk_name}: Downloaded {result_count} results")
                    _prov_name = config.get("api", {}).get("provider", "unknown")
                    trace_log(run_dir, f"[BATCH] COLLECT {_prov_name} {chunk_name} batch_id={batch_id} | {result_count} results")

                    # Accumulate token counts from batch metadata
                    if batch_metadata:
                        batch_input = batch_metadata.get("total_input_tokens", 0)
                        batch_output = batch_metadata.get("total_output_tokens", 0)

                        # Check if this is a retry chunk
                        is_retry = chunk_data.get("retry_step") is not None or chunk_name.startswith("retry_")

                        # Add to appropriate tick totals
                        if is_retry:
                            tick_retry_input_tokens += batch_input
                            tick_retry_output_tokens += batch_output
                        else:
                            tick_initial_input_tokens += batch_input
                            tick_initial_output_tokens += batch_output

                        # Store per-chunk token info
                        chunk_data["input_tokens"] = chunk_data.get("input_tokens", 0) + batch_input
                        chunk_data["output_tokens"] = chunk_data.get("output_tokens", 0) + batch_output

                        log_message(
                            log_file, "TOKENS",
                            f"{chunk_name}: {batch_input} input, {batch_output} output tokens{' (retry)' if is_retry else ''}"
                        )

                    # Run validation
                    validated_file = chunk_dir / f"{step}_validated.jsonl"
                    failures_file = chunk_dir / f"{step}_failures.jsonl"
                    schema_path = get_schema_path(config, step, run_dir)

                    # Determine step's input file for fixing failure records
                    # First step uses units.jsonl, later steps use previous step's output
                    step_idx = pipeline.index(step) if step in pipeline else 0
                    if step_idx == 0:
                        step_input_file = chunk_dir / "units.jsonl"
                    else:
                        prev_step = pipeline[step_idx - 1]
                        step_input_file = chunk_dir / f"{prev_step}_validated.jsonl"

                    valid_count, failed_count = run_validation_pipeline(
                        results_file, validated_file, failures_file,
                        schema_path, config_path, step, log_file, chunk_name,
                        input_file=step_input_file,
                        timeout=get_subprocess_timeout(config)
                    )

                    # Append non-JSON failures (pipeline_internal) to failures file
                    if batch_non_json_failures:
                        with open(failures_file, 'a') as ff:
                            for nj_failure in batch_non_json_failures:
                                ff.write(json.dumps(nj_failure) + '\n')
                        failed_count += len(batch_non_json_failures)
                        log_message(log_file, "VALIDATE", f"{chunk_name}/{step}: {len(batch_non_json_failures)} non-JSON responses categorized as pipeline_internal")

                    log_message(log_file, "VALIDATE", f"{chunk_name}: {valid_count} valid, {failed_count} failed")

                    # Update chunk
                    chunk_data["valid"] = valid_count
                    chunk_data["failed"] = failed_count

                    # If zero valid and some failed, mark chunk FAILED — don't advance
                    if valid_count == 0 and failed_count > 0:
                        log_message(log_file, "STOP", f"{chunk_name}: Step {step} produced 0 valid units out of {failed_count}. Marking chunk as FAILED.")
                        chunk_data["state"] = "FAILED"
                        chunk_data["batch_id"] = None
                        chunk_data["submitted_at"] = None
                        chunk_data["provider_status"] = None
                        prev_poll_status.pop(chunk_name, None)
                        failed += 1
                        save_manifest(run_dir, manifest)
                    else:
                        # Determine next state
                        next_step = get_next_step(pipeline, step)
                        if next_step:
                            new_state = f"{next_step}_PENDING"
                        else:
                            # Last step - mark as validated
                            new_state = "VALIDATED"

                        log_message(log_file, "STATE", f"{chunk_name}: {chunk_data['state']} -> {new_state}")
                        chunk_data["state"] = new_state
                        chunk_data["batch_id"] = None
                        chunk_data["submitted_at"] = None
                        chunk_data["provider_status"] = None
                        prev_poll_status.pop(chunk_name, None)

                        # Save manifest after each state change
                        save_manifest(run_dir, manifest)

                except Exception as e:
                    log_message(log_file, "ERROR", f"{chunk_name}: Collection failed: {e}")
                    errors += 1
                    warnings.append({
                        "code": "COLLECTION_ERROR",
                        "message": f"{chunk_name}: Collection failed: {e}",
                        "chunk": chunk_name
                    })

            elif poll_status == BatchStatus.FAILED:
                error_msg = poll_result.get("error", "Unknown error")
                log_message(log_file, "ERROR", f"{chunk_name}: Batch failed: {error_msg}")
                warnings.append({
                    "code": "API_BATCH_FAILED",
                    "message": f"{chunk_name}: Batch failed: {error_msg}",
                    "chunk": chunk_name
                })

                # Mark for retry (reset to PENDING with incremented retry count)
                chunk_data["retries"] = chunk_data.get("retries", 0) + 1
                if chunk_data["retries"] >= max_retries:
                    log_message(log_file, "ERROR", f"{chunk_name}: Max retries ({max_retries}) exceeded — marking FAILED")
                    chunk_data["state"] = "FAILED"
                else:
                    chunk_data["state"] = f"{step}_PENDING"
                chunk_data["batch_id"] = None
                chunk_data["submitted_at"] = None
                chunk_data["provider_status"] = None
                prev_poll_status.pop(chunk_name, None)
                errors += 1

                save_manifest(run_dir, manifest)

            # If status is "processing" or "pending", leave as SUBMITTED

        except Exception as e:
            log_message(log_file, "ERROR", f"{chunk_name}: Poll failed: {e}")
            errors += 1
            warnings.append({
                "code": "POLL_ERROR",
                "message": f"{chunk_name}: Poll failed: {e}",
                "chunk": chunk_name
            })

    # Phase 2: Submit new batches
    # Recount inflight after polling
    inflight = sum(1 for c in chunks.values() if parse_state(c["state"])[1] == "SUBMITTED")
    _logged_batch_steps = set()  # Track which steps have had provider logged
    throttled_count = 0

    for chunk_name, chunk_data in chunks.items():
        step, status = parse_state(chunk_data["state"])

        if status == "PENDING":
            pending += 1

            # Log provider/model once per step
            if step not in _logged_batch_steps:
                _logged_batch_steps.add(step)
                try:
                    _step_prov = get_provider_for_step(step)
                    provider_tag = format_step_provider_tag(config, step, _step_prov)
                    log_message(log_file, "BATCH", f"Running {step} with {provider_tag}")
                except Exception:
                    pass

            # Check if this is an expression step (no API call needed)
            # Expression steps handle their own input file resolution
            if is_expression_step(config, step):
                step_config = get_expression_step_config(config, step)
                log_message(log_file, "EXPRESSION", f"{chunk_name}: Running expression step '{step}' locally")

                try:
                    valid, failed_count, _, _ = run_expression_step(
                        run_dir, chunk_name, step, step_config, config, manifest, log_file
                    )

                    # Update chunk counts
                    chunk_data["valid"] = valid
                    chunk_data["failed"] = failed_count

                    # Advance to next step or mark as validated
                    next_step = get_next_step(pipeline, step)
                    if next_step:
                        chunk_data["state"] = f"{next_step}_PENDING"
                        log_message(log_file, "EXPRESSION", f"{chunk_name}: {step} complete ({valid} valid, {failed_count} failed) -> {next_step}_PENDING")
                    else:
                        chunk_data["state"] = "VALIDATED"
                        log_message(log_file, "EXPRESSION", f"{chunk_name}: {step} complete ({valid} valid, {failed_count} failed) -> VALIDATED")
                        completed += 1

                    save_manifest(run_dir, manifest)

                except Exception as e:
                    log_message(log_file, "ERROR", f"{chunk_name}: Expression step '{step}' failed: {e}")
                    errors += 1
                    warnings.append({
                        "code": "EXPRESSION_ERROR",
                        "message": f"{chunk_name}: Expression step '{step}' failed: {e}",
                        "chunk": chunk_name
                    })

                continue  # Skip API batch submission for expression steps

            if inflight >= max_inflight:
                throttled_count += 1
                continue

            chunk_dir = run_dir / "chunks" / chunk_name

            # Determine input file for prompt preparation
            # For retry chunks, we need to check if we're still on the retry step or have progressed
            retry_step = chunk_data.get("retry_step")
            is_retry_chunk = retry_step is not None or chunk_name.startswith("retry_")

            if is_retry_chunk and retry_step == step:
                # This is the step being retried - use units.jsonl (pre-populated with correct input)
                units_file = chunk_dir / "units.jsonl"
            elif is_retry_chunk and retry_step != step:
                # Retry chunk has progressed past its initial retry step
                # Use previous step's validated output from THIS chunk
                step_idx = pipeline.index(step)
                prev_step = pipeline[step_idx - 1]
                units_file = chunk_dir / f"{prev_step}_validated.jsonl"

                if not units_file.exists():
                    log_message(log_file, "WARN", f"{chunk_name}: Previous step output not found: {units_file}")
                    continue
            elif step != pipeline[0]:
                # Regular chunks for steps after first need previous step's validated output
                step_idx = pipeline.index(step)
                prev_step = pipeline[step_idx - 1]
                units_file = chunk_dir / f"{prev_step}_validated.jsonl"

                if not units_file.exists():
                    log_message(log_file, "WARN", f"{chunk_name}: Previous step output not found: {units_file}")
                    continue
            else:
                # First step of regular chunk uses units.jsonl
                units_file = chunk_dir / "units.jsonl"

            prompts_file = chunk_dir / f"{step}_prompts.jsonl"

            # Guard: skip submission if input file is empty (0 valid units from prior step)
            if units_file.exists():
                line_count = sum(1 for line in open(units_file) if line.strip())
                if line_count == 0:
                    log_message(log_file, "STOP", f"{chunk_name}: Input file {units_file.name} is empty (0 units). Marking chunk as FAILED.")
                    chunk_data["state"] = "FAILED"
                    failed += 1
                    save_manifest(run_dir, manifest)
                    continue

            # Prepare prompts
            success, error_msg = prepare_prompts(units_file, prompts_file, config_path, step)
            if not success:
                log_message(log_file, "ERROR", f"{chunk_name}: Failed to prepare prompts: {error_msg}")
                errors += 1
                warnings.append({
                    "code": "PROMPT_PREP_ERROR",
                    "message": f"{chunk_name}: Failed to prepare prompts: {error_msg}",
                    "chunk": chunk_name
                })
                continue

            # Submit batch using provider
            try:
                # Convert raw prompts to provider-specific batch format
                formatted_file = prompts_file.with_suffix('.batch.jsonl')
                with open(prompts_file) as f_in, open(formatted_file, 'w') as f_out:
                    for line in f_in:
                        line = line.strip()
                        if not line:
                            continue
                        raw_prompt = json.loads(line)
                        unit_id = raw_prompt.get("unit_id", "")
                        prompt_text = raw_prompt.get("prompt", "")
                        # Format for this provider's batch API
                        formatted = get_provider_for_step(step).format_batch_request(unit_id, prompt_text)
                        f_out.write(json.dumps(formatted) + "\n")

                # Upload and create batch
                step_provider = get_provider_for_step(step)
                file_id = step_provider.upload_batch_file(formatted_file)
                batch_id = step_provider.create_batch(file_id)

                submitted += 1
                inflight += 1

                chunk_data["state"] = f"{step}_SUBMITTED"
                chunk_data["batch_id"] = batch_id
                chunk_data["submitted_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

                log_message(log_file, "SUBMIT", f"{chunk_name}: {step}_PENDING -> {step}_SUBMITTED ({batch_id})")
                _prov_name = config.get("api", {}).get("provider", "unknown")
                trace_log(run_dir, f"[BATCH] SUBMIT {_prov_name} {chunk_name} batch_id={batch_id}")

                # Log batch estimate on first submission
                if submitted == 1:
                    log_message(log_file, "INFO", "Batch submitted. Typical wait: 5-20min depending on provider and batch size.")

                # Save manifest after each submission
                save_manifest(run_dir, manifest)

            except Exception as e:
                log_message(log_file, "ERROR", f"{chunk_name}: Submit failed: {e}")
                errors += 1
                warnings.append({
                    "code": "SUBMIT_ERROR",
                    "message": f"{chunk_name}: Submit failed: {e}",
                    "chunk": chunk_name
                })

        elif status == "VALIDATED":
            completed += 1
        elif status == "FAILED":
            failed += 1

    # Log throttle summary (single line instead of per-chunk spam)
    if throttled_count > 0:
        _prev_throttle = prev_poll_status.get("_throttle_count", 0)
        if throttled_count != _prev_throttle:
            log_message(log_file, "THROTTLE",
                        f"{throttled_count} chunks waiting (max_inflight_batches: {max_inflight}, {inflight} submitted)")
            prev_poll_status["_throttle_count"] = throttled_count
    elif prev_poll_status.get("_throttle_count", 0) > 0:
        # Throttle cleared — reset cached count
        prev_poll_status["_throttle_count"] = 0

    # Update manifest metadata with accumulated tokens
    metadata["initial_input_tokens"] += tick_initial_input_tokens
    metadata["initial_output_tokens"] += tick_initial_output_tokens
    metadata["retry_input_tokens"] += tick_retry_input_tokens
    metadata["retry_output_tokens"] += tick_retry_output_tokens

    # Save manifest with updated metadata
    save_manifest(run_dir, manifest)

    # Persist poll status cache for next tick
    try:
        with open(prev_poll_status_file, 'w') as f:
            json.dump(prev_poll_status, f)
    except Exception:
        pass

    # Determine if anything meaningful happened this tick
    tick_had_activity = (state_changes > 0 or collected > 0 or submitted > 0 or errors > 0)

    # Update last-activity timestamp when something happens
    if tick_had_activity:
        try:
            _last_activity_file.write_text(str(_now))
        except Exception:
            pass

    # Compute cumulative cost for tick summaries
    _total_in = metadata.get("initial_input_tokens", 0) + metadata.get("retry_input_tokens", 0)
    _total_out = metadata.get("initial_output_tokens", 0) + metadata.get("retry_output_tokens", 0)
    try:
        _tick_provider = get_provider(config)
    except Exception:
        _tick_provider = None
    _cumulative_cost = compute_step_cost(_total_in, _total_out, _tick_provider, is_realtime=False)
    _cost_str = f"${_cumulative_cost:.2f}" if _cumulative_cost is not None else "$?"

    # Identify the "active" step — the earliest step with non-terminal chunks
    _active_step = None
    for _s in pipeline:
        if any(parse_state(c["state"])[0] == _s for c in chunks.values()):
            _active_step = _s
            break

    # Step transition logging — log once when a new step becomes active
    if _active_step:
        _prev_active = prev_poll_status.get("_active_step")
        if _prev_active != _active_step:
            _step_num = pipeline.index(_active_step) + 1
            log_message(log_file, "STEP", f"Advancing to {_active_step} (step {_step_num}/{len(pipeline)})")
            prev_poll_status["_active_step"] = _active_step

    # Compute chunk completion counts
    _chunks_complete = sum(1 for c in chunks.values() if c.get("state") in ("VALIDATED", "FAILED"))
    _chunks_total = len(chunks)

    # Log tick summary with delta or enriched heartbeat
    if tick_had_activity:
        # Delta summary: what changed this tick
        _post_valid = sum(c.get("valid", 0) for c in chunks.values())
        _post_failed = sum(c.get("failed", 0) for c in chunks.values())
        _delta_valid = _post_valid - _pre_valid
        _delta_failed = _post_failed - _pre_failed
        _step_label = f"step: {_active_step}" if _active_step else "idle"
        _parts = []
        if _delta_valid:
            _parts.append(f"+{_delta_valid} valid")
        if _delta_failed:
            _parts.append(f"+{_delta_failed} failed")
        if not _parts:
            _parts.append(f"{collected} collected, {submitted} submitted")
        _parts.append(_step_label)
        _parts.append(f"{_chunks_complete}/{_chunks_total} chunks complete")
        _parts.append(f"{_cost_str} spent")
        log_message(log_file, "TICK", " | ".join(_parts))
    elif (_now - _last_activity_ts) >= 60:
        # Enriched heartbeat with status snapshot
        _idle_secs = int(_now - _last_activity_ts)
        _idle_min = _idle_secs // 60
        _idle_sec = _idle_secs % 60
        _idle_str = f"{_idle_min}m{_idle_sec:02d}s" if _idle_min else f"{_idle_sec}s"
        _still_submitted = sum(1 for c in chunks.values() if parse_state(c["state"])[1] == "SUBMITTED")
        _still_pending = sum(1 for c in chunks.values() if parse_state(c["state"])[1] == "PENDING")
        _step_label = _active_step or "idle"
        log_message(log_file, "TICK",
                    f"Idle {_idle_str} | {_step_label}: {_chunks_complete}/{_chunks_total} chunks ({_still_submitted} submitted, {_still_pending} pending) | {_cost_str} spent")
        try:
            _last_activity_file.write_text(str(_now))
        except Exception:
            pass

    tick_input_tokens = tick_initial_input_tokens + tick_retry_input_tokens
    tick_output_tokens = tick_initial_output_tokens + tick_retry_output_tokens
    if tick_input_tokens > 0 or tick_output_tokens > 0:
        retry_suffix = ""
        if tick_retry_input_tokens > 0 or tick_retry_output_tokens > 0:
            retry_suffix = f" (retry: {tick_retry_input_tokens} in, {tick_retry_output_tokens} out)"
        tick_cost = compute_step_cost(tick_input_tokens, tick_output_tokens, _tick_provider, is_realtime=False)
        cost_suffix = f" | ${tick_cost:.4f}" if tick_cost is not None else ""
        log_message(
            log_file, "TICK",
            f"Tokens this tick: {tick_input_tokens} input, {tick_output_tokens} output{retry_suffix}{cost_suffix}"
        )

    # Check if run is now terminal and run-scope steps should execute
    if is_run_terminal(manifest, max_retries):
        run_scope_steps = get_run_scope_steps(config)
        completed_run_steps = manifest.get("completed_run_steps", [])

        for step_config in run_scope_steps:
            step_name = step_config.get("name")
            if not step_name:
                continue

            # Skip if already completed
            if step_name in completed_run_steps:
                continue

            log_message(log_file, "RUN_STEP", f"Run is terminal, executing run-scope step: {step_name}")

            success = run_scope_run_step(run_dir, step_config, config_path, log_file)

            # Track completed step (even on failure - they can re-run manually if needed)
            if "completed_run_steps" not in manifest:
                manifest["completed_run_steps"] = []
            manifest["completed_run_steps"].append(step_name)
            save_manifest(run_dir, manifest)

    # Build status using shared function
    activity = {
        "polled": polled,
        "collected": collected,
        "submitted": submitted
    }

    return build_run_status(
        run_dir=run_dir,
        manifest=manifest,
        config=config,
        activity=activity,
        warnings=warnings,
        tick_errors=errors
    )


def init_run(
    config_path: Path,
    run_dir: Path,
    max_units: int | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
    used_default_provider: bool = False,
    repeat_override: int | None = None
) -> bool:
    """
    Initialize a new run directory.

    Args:
        config_path: Path to source config.yaml
        run_dir: Path to run directory (must not exist)
        max_units: Optional limit on units for testing
        provider_override: Optional provider to override config (gemini/openai/anthropic)
        model_override: Optional model to override config
        used_default_provider: True if provider was resolved from registry default
        repeat_override: Optional repeat count to override config

    Returns:
        True if successful, False otherwise
    """
    # Validate config path
    if not config_path.exists():
        print(f"Error: Config file not found: {config_path}", file=sys.stderr)
        return False

    # Validate generate_units.py exists
    scripts_dir = Path(__file__).parent
    generate_units_path = scripts_dir / "generate_units.py"
    if not generate_units_path.exists():
        print(f"Error: generate_units.py not found: {generate_units_path}", file=sys.stderr)
        return False

    # Validate run directory doesn't exist
    if run_dir.exists():
        print(f"Error: Run directory already exists: {run_dir}", file=sys.stderr)
        return False

    # Load and validate config
    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"Error: Invalid YAML in config file: {e}", file=sys.stderr)
        return False

    errors = validate_config(config)
    if errors:
        print("Error: Invalid config:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return False

    # Get pipeline steps and chunk size
    # Get chunk-scope steps for manifest pipeline (drives chunk state machine)
    pipeline_steps = get_chunk_scope_steps(config)
    if not pipeline_steps:
        print("Error: No chunk-scope pipeline steps defined", file=sys.stderr)
        return False
    chunk_size = config["processing"]["chunk_size"]
    first_step = pipeline_steps[0]

    # Create directory structure
    run_dir.mkdir(parents=True)
    config_dir = run_dir / "config"
    config_dir.mkdir()
    input_dir = run_dir / "input"
    input_dir.mkdir()
    chunks_dir = run_dir / "chunks"
    chunks_dir.mkdir()

    # Initialize log file
    log_file = run_dir / "RUN_LOG.txt"
    log_message(log_file, "INIT", "Starting new run")
    log_message(log_file, "INIT", f"Command: {' '.join(sys.argv)}")
    log_message(log_file, "INIT", f"Config: {config_path}")
    if provider_override:
        if used_default_provider:
            log_message(log_file, "INIT", f"Using default provider: {provider_override}")
        else:
            log_message(log_file, "INIT", f"Provider override: {provider_override}")
    if model_override:
        log_message(log_file, "INIT", f"Model override: {model_override}")

    # Copy config file to run directory
    dest_config_path = config_dir / "config.yaml"
    shutil.copy2(config_path, dest_config_path)

    # Apply CLI/TUI overrides to the snapshotted config
    # This ensures --tick/--watch/--retry-failures use the correct settings
    if provider_override or model_override or repeat_override:
        with open(dest_config_path) as f:
            snapshot_config = yaml.safe_load(f)
        if provider_override or model_override:
            if 'api' not in snapshot_config:
                snapshot_config['api'] = {}
            if provider_override:
                snapshot_config['api']['provider'] = provider_override
            if model_override:
                snapshot_config['api']['model'] = model_override
        if repeat_override:
            if 'processing' not in snapshot_config:
                snapshot_config['processing'] = {}
            snapshot_config['processing']['repeat'] = repeat_override
            log_message(log_file, "INIT", f"Applied override: repeat={repeat_override}")
        with open(dest_config_path, 'w') as f:
            yaml.dump(snapshot_config, f, default_flow_style=False, sort_keys=False)
        if provider_override or model_override:
            log_message(log_file, "INIT", f"Applied overrides: provider={provider_override}, model={model_override}")

    # Copy item source file if external
    item_source_path = get_item_source_path(config, config_path)
    if item_source_path:
        if not item_source_path.exists():
            print(f"Error: Item source file not found: {item_source_path}", file=sys.stderr)
            # Clean up
            shutil.rmtree(run_dir)
            return False
        dest_item_source = config_dir / item_source_path.name
        shutil.copy2(item_source_path, dest_item_source)
        log_message(log_file, "INIT", f"Copied item source: {item_source_path.name}")

    # Copy templates directory if it exists
    templates_dir_name = config.get("prompts", {}).get("template_dir", "templates")
    src_templates = config_path.parent / templates_dir_name
    if src_templates.exists():
        dst_templates = config_dir / templates_dir_name
        shutil.copytree(src_templates, dst_templates)
        log_message(log_file, "INIT", f"Copied templates directory: {templates_dir_name}")

    # Copy schemas directory if it exists
    schemas_dir_name = config.get("schemas", {}).get("schema_dir", "schemas")
    src_schemas = config_path.parent / schemas_dir_name
    if src_schemas.exists():
        dst_schemas = config_dir / schemas_dir_name
        shutil.copytree(src_schemas, dst_schemas)
        log_message(log_file, "INIT", f"Copied schemas directory: {schemas_dir_name}")

    log_message(log_file, "INIT", "Config snapshot saved to run directory")

    # Generate full units file
    units_file = input_dir / "units.jsonl"
    cmd = [
        sys.executable,
        str(generate_units_path),
        "--config", str(dest_config_path),
        "--output", str(units_file),
        "--quiet"
    ]
    if max_units:
        cmd.extend(["--max-units", str(max_units)])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_DEFAULT)
    except subprocess.TimeoutExpired:
        print(f"Error: generate_units.py timed out (>{SUBPROCESS_TIMEOUT_DEFAULT}s)", file=sys.stderr)
        shutil.rmtree(run_dir)
        return False

    if result.returncode != 0:
        print(f"Error: generate_units.py failed: {result.stderr}", file=sys.stderr)
        shutil.rmtree(run_dir)
        return False

    # Count units
    with open(units_file) as f:
        total_units = sum(1 for line in f if line.strip())

    # Get strategy and position count for log message
    strategy = config["processing"].get("strategy", "permutation")
    positions = config["processing"].get("positions", [])
    items_config = config["processing"]["items"]

    # Always try to determine actual item count from source (not limited)
    actual_item_count = "?"
    if item_source_path and item_source_path.exists():
        with open(item_source_path) as f:
            items_data = yaml.safe_load(f)
            # Try common patterns
            if isinstance(items_data, list):
                actual_item_count = len(items_data)
            elif isinstance(items_data, dict):
                # Use the key specified in config (processing.items.key)
                items_key = items_config.get("key")
                if items_key and items_key in items_data and isinstance(items_data[items_key], list):
                    actual_item_count = len(items_data[items_key])

    # For display in non-repeat log messages
    item_count = max_units if max_units else actual_item_count

    # Log message varies by strategy and repeat count
    # Read from snapshot config to get actual values (including any --repeat override)
    with open(dest_config_path) as f:
        snapshot_config = yaml.safe_load(f)
    snapshot_processing = snapshot_config.get("processing", {})
    repeat_count = snapshot_processing.get("repeat", 1)

    if repeat_count > 1:
        # For repeat strategy, calculate base count from actual_item_count (not from limited total_units)
        if strategy == "direct" and isinstance(actual_item_count, int):
            base_count = actual_item_count
        else:
            # Fallback: derive from total_units
            base_count = total_units // repeat_count
        pre_limit_count = base_count * repeat_count
        # If max_units was applied and reduced the count, note it
        if max_units and total_units < pre_limit_count:
            log_message(log_file, "INIT", f"Generated {total_units} units ({base_count} base × {repeat_count} repetitions, limited from {pre_limit_count})")
        else:
            log_message(log_file, "INIT", f"Generated {total_units} units ({base_count} base × {repeat_count} repetitions)")
    elif strategy == "direct":
        log_message(log_file, "INIT", f"Generated {total_units} units (direct strategy)")
    elif strategy == "cross_product":
        log_message(log_file, "INIT", f"Generated {total_units} units (cross_product: {len(positions)} groups)")
    else:
        log_message(log_file, "INIT", f"Generated {total_units} units ({len(positions)} positions x {item_count} items)")

    # Generate chunked units
    cmd = [
        sys.executable,
        str(generate_units_path),
        "--config", str(dest_config_path),
        "--output-dir", str(chunks_dir),
        "--chunk-size", str(chunk_size),
        "--quiet"
    ]
    if max_units:
        cmd.extend(["--max-units", str(max_units)])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_DEFAULT)
    except subprocess.TimeoutExpired:
        print(f"Error: generate_units.py chunking timed out (>{SUBPROCESS_TIMEOUT_DEFAULT}s)", file=sys.stderr)
        shutil.rmtree(run_dir)
        return False

    if result.returncode != 0:
        print(f"Error: generate_units.py chunking failed: {result.stderr}", file=sys.stderr)
        shutil.rmtree(run_dir)
        return False

    # Count chunks and build manifest
    chunk_dirs = sorted(chunks_dir.iterdir())
    chunks_manifest = {}

    for chunk_dir in chunk_dirs:
        if chunk_dir.is_dir() and chunk_dir.name.startswith("chunk_"):
            chunk_units_file = chunk_dir / "units.jsonl"
            if chunk_units_file.exists():
                with open(chunk_units_file) as f:
                    item_count_in_chunk = sum(1 for line in f if line.strip())

                chunks_manifest[chunk_dir.name] = {
                    "state": f"{first_step}_PENDING",
                    "batch_id": None,
                    "items": item_count_in_chunk,
                    "valid": 0,
                    "failed": 0,
                    "retries": 0
                }

    num_chunks = len(chunks_manifest)
    log_message(log_file, "INIT", f"Created {num_chunks} chunks (chunk_size={chunk_size})")
    log_message(log_file, "INIT", f"Pipeline: {' -> '.join(pipeline_steps)}")

    # Determine pipeline name from config or directory
    pipeline_name = config.get("pipeline", {}).get("name")
    if not pipeline_name:
        # Fall back to config directory name (e.g., "NPC Dialog" from pipelines/NPC Dialog/)
        pipeline_name = config_path.parent.name

    # Resolve the actual provider and model names for manifest metadata.
    # Resolution: CLI override > config > registry default.
    resolved_provider = provider_override
    if not resolved_provider:
        resolved_provider = config.get("api", {}).get("provider")
    if not resolved_provider:
        try:
            from scripts.providers.base import LLMProvider
            registry = LLMProvider.load_model_registry()
            resolved_provider = registry.get("default_provider")
        except Exception:
            pass

    resolved_model = model_override
    if not resolved_model:
        resolved_model = config.get("api", {}).get("model")
    if not resolved_model and resolved_provider:
        try:
            from scripts.providers.base import LLMProvider
            registry = LLMProvider.load_model_registry()
            provider_data = registry.get("providers", {}).get(resolved_provider, {})
            resolved_model = provider_data.get("default_model")
        except Exception:
            pass

    # Create manifest
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = {
        "config": "config/config.yaml",  # Config snapshot location in run directory
        "created": now,
        "updated": now,
        "status": "pending",
        "pipeline": pipeline_steps,
        "chunks": chunks_manifest,
        "metadata": {
            "pipeline_name": pipeline_name,
            "start_time": now,
            "max_units": max_units,
            "provider": resolved_provider,
            "model": resolved_model,
            "cli_provider_override": bool(provider_override),
            "cli_model_override": bool(model_override),
            "initial_input_tokens": 0,
            "initial_output_tokens": 0,
            "retry_input_tokens": 0,
            "retry_output_tokens": 0
        }
    }

    save_manifest(run_dir, manifest)

    log_message(log_file, "INIT", "Run initialized successfully")

    # Print summary
    print(f"Run initialized: {run_dir}")
    print(f"  Units: {total_units}")
    print(f"  Chunks: {num_chunks}")
    print(f"  Pipeline: {' -> '.join(pipeline_steps)}")

    return True


def status_run(run_dir: Path) -> dict:
    """
    Get current status of run.

    Returns status dict matching tick_run() output format.
    """
    if not run_dir.exists():
        return {"error": "Run directory not found"}

    manifest_path = run_dir / "MANIFEST.json"
    if not manifest_path.exists():
        return {"error": "MANIFEST.json not found"}

    manifest = load_manifest(run_dir)

    # Load config from run directory
    config_path = run_dir / manifest["config"]
    if not config_path.exists():
        return {"error": "Config file not found in run directory"}

    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Use shared status builder (activity=None for status command)
    return build_run_status(
        run_dir=run_dir,
        manifest=manifest,
        config=config,
        activity=None,
        warnings=None,
        tick_errors=0
    )


def get_next_retry_number(chunks_dir: Path) -> int:
    """
    Find the next available retry chunk number.

    Scans existing retry_NNN directories and returns the next number.
    """
    max_num = 0
    for item in chunks_dir.iterdir():
        if item.is_dir() and item.name.startswith("retry_"):
            try:
                num = int(item.name.split("_")[1])
                max_num = max(max_num, num)
            except (IndexError, ValueError):
                pass
    return max_num + 1


def retry_failures_run(run_dir: Path, max_retries: int = 5) -> dict:
    """
    Create retry chunks from failed units.

    Scans all chunks for *_failures.jsonl files, groups by step,
    and creates new retry chunks for units that haven't exceeded max_retries.

    Args:
        run_dir: Path to the run directory
        max_retries: Maximum retry attempts per item (default: 5)

    Returns status dict for JSON output.
    """
    # Validate run directory
    if not run_dir.exists():
        print(f"Error: Run directory not found: {run_dir}", file=sys.stderr)
        return {"error": "Run directory not found"}

    manifest_path = run_dir / "MANIFEST.json"
    if not manifest_path.exists():
        print(f"Error: MANIFEST.json not found in {run_dir}", file=sys.stderr)
        return {"error": "MANIFEST.json not found"}

    log_file = run_dir / "RUN_LOG.txt"
    log_message(log_file, "RETRY", f"Scanning for failures (max_retries={max_retries})...")

    # Load manifest
    manifest = load_manifest(run_dir)
    chunks = manifest["chunks"]

    chunks_dir = run_dir / "chunks"

    # Step 1: Scan for failures grouped by step
    # Structure: {step: {unit_id: {"unit": {...}, "source_chunk": str, "retry_count": int}}}
    failures_by_step: dict[str, dict[str, dict]] = {}

    for chunk_name, chunk_data in chunks.items():
        # Skip retry chunks that are still being processed (not VALIDATED/FAILED)
        if chunk_name.startswith("retry_"):
            step, status = parse_state(chunk_data["state"])
            if status not in ("VALIDATED", "FAILED"):
                # This retry is still in progress
                continue

        chunk_dir = chunks_dir / chunk_name
        if not chunk_dir.exists():
            continue

        # Get chunk-level retry count as fallback
        chunk_retries = chunk_data.get("retries", chunk_data.get("retry_count", 0))

        # Get pipeline for determining correct input sources
        pipeline = manifest.get("pipeline", [])

        # Find all failure files in this chunk
        for failure_file in chunk_dir.glob("*_failures.jsonl"):
            # Extract step name from filename (e.g., "generate_failures.jsonl" -> "generate")
            step = failure_file.stem.replace("_failures", "")

            if step not in failures_by_step:
                failures_by_step[step] = {}

            # Determine correct input source based on pipeline position
            # Do NOT trust failure.get("input") - always look up from source file
            step_idx = pipeline.index(step) if step in pipeline else 0
            if step_idx == 0:
                # First step - input is from units.jsonl
                input_source = chunk_dir / "units.jsonl"
            else:
                # Later step - input is previous step's validated output
                prev_step = pipeline[step_idx - 1]
                input_source = chunk_dir / f"{prev_step}_validated.jsonl"

            # Load input data by unit_id from the correct source
            input_by_unit_id = {}
            if input_source.exists():
                with open(input_source) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            item = json.loads(line)
                            uid = item.get("unit_id")
                            if uid:
                                input_by_unit_id[uid] = item
                        except json.JSONDecodeError:
                            continue

            # Read failures
            with open(failure_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        failure = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    unit_id = failure.get("unit_id")
                    if not unit_id:
                        # Skip records without unit_id
                        log_message(log_file, "WARN", f"Failure record missing unit_id in {failure_file}")
                        continue

                    # Always look up correct input from source file (don't trust failure["input"])
                    unit_data = input_by_unit_id.get(unit_id)
                    if not unit_data:
                        log_message(log_file, "WARN", f"Could not find input for {unit_id} in {input_source}")
                        continue

                    # Get per-item retry count, falling back to chunk-level retries
                    item_retry_count = failure.get("retry_count")
                    if item_retry_count is None:
                        item_retry_count = chunk_retries

                    # Deduplicate by unit_id - keep highest retry count
                    if unit_id not in failures_by_step[step]:
                        failures_by_step[step][unit_id] = {
                            "unit": unit_data,
                            "source_chunk": chunk_name,
                            "retry_count": item_retry_count
                        }
                    else:
                        # Keep the higher retry count if same unit appears multiple times
                        existing_count = failures_by_step[step][unit_id]["retry_count"]
                        if item_retry_count > existing_count:
                            failures_by_step[step][unit_id]["retry_count"] = item_retry_count

    # Step 2: Check for no failures
    total_failures = sum(len(units) for units in failures_by_step.values())
    if total_failures == 0:
        log_message(log_file, "RETRY", "No failures found")
        print("No failures to retry")
        return {
            "retry_chunks_created": 0,
            "failures_by_step": {},
            "permanent_failures": 0,
            "retry_chunks": []
        }

    # Log failures found
    for step, units in failures_by_step.items():
        # Count unique source chunks
        source_chunks = set(u["source_chunk"] for u in units.values())
        log_message(
            log_file, "RETRY",
            f"Found {len(units)} failures at step '{step}' across {len(source_chunks)} chunks"
        )

    # Step 3: Filter by max_retries and track permanent failures
    permanent_failures = []
    retryable_by_step: dict[str, dict[str, dict]] = {}

    for step, units in failures_by_step.items():
        retryable_by_step[step] = {}

        for unit_id, unit_info in units.items():
            if unit_info["retry_count"] >= max_retries:
                # This unit has exceeded max retries
                permanent_failures.append({
                    "unit_id": unit_id,
                    "step": step,
                    "retry_count": unit_info["retry_count"],
                    "source_chunk": unit_info["source_chunk"]
                })
            else:
                retryable_by_step[step][unit_id] = unit_info

    # Remove empty steps
    retryable_by_step = {k: v for k, v in retryable_by_step.items() if v}

    # Log permanent failures
    if permanent_failures:
        log_message(
            log_file, "RETRY",
            f"Skipped {len(permanent_failures)} units exceeding max_retries ({max_retries})"
        )

        # Write permanent failures to file
        perm_failures_file = run_dir / "permanent_failures.jsonl"
        with open(perm_failures_file, "a") as f:
            for pf in permanent_failures:
                f.write(json.dumps(pf) + "\n")

    # Step 4: Create retry chunks
    retry_chunks_created = []
    next_retry_num = get_next_retry_number(chunks_dir)

    for step, units in retryable_by_step.items():
        if not units:
            continue

        # Create retry chunk directory
        retry_name = f"retry_{next_retry_num:03d}"
        retry_dir = chunks_dir / retry_name
        retry_dir.mkdir()

        # Write units.jsonl - extract unit data from the info dict
        # Include incremented retry_count so validation can track it
        units_file = retry_dir / "units.jsonl"
        with open(units_file, "w") as f:
            for unit_id, unit_info in units.items():
                unit = unit_info["unit"].copy()
                # Ensure unit_id is set
                unit["unit_id"] = unit_id
                # Increment retry_count for this attempt
                unit["retry_count"] = unit_info["retry_count"] + 1
                f.write(json.dumps(unit) + "\n")

        # Calculate max retry count from units
        max_retry_count = max(u["retry_count"] for u in units.values())
        source_chunks = list(set(u["source_chunk"] for u in units.values()))

        # Add to manifest
        chunks[retry_name] = {
            "state": f"{step}_PENDING",
            "batch_id": None,
            "items": len(units),
            "valid": 0,
            "failed": 0,
            "retry_count": max_retry_count + 1,
            "source_chunks": source_chunks,
            "retry_step": step
        }

        retry_chunks_created.append({
            "name": retry_name,
            "step": step,
            "units": len(units)
        })

        log_message(
            log_file, "RETRY",
            f"Created {retry_name} with {len(units)} units for step '{step}'"
        )

        next_retry_num += 1

    # Step 5: Save manifest
    if retry_chunks_created:
        save_manifest(run_dir, manifest)

    # Log summary
    log_message(
        log_file, "RETRY",
        f"Retry setup complete: {len(retry_chunks_created)} retry chunks created, "
        f"{len(permanent_failures)} permanent failures"
    )

    # Build status output
    failures_count_by_step = {step: len(units) for step, units in failures_by_step.items()}

    return {
        "retry_chunks_created": len(retry_chunks_created),
        "failures_by_step": failures_count_by_step,
        "permanent_failures": len(permanent_failures),
        "retry_chunks": retry_chunks_created
    }


# Config validation moved to config_validator.py


# =============================================================================
# Watch Mode Functions
# =============================================================================

def parse_duration(duration_str: str) -> int:
    """
    Parse a duration string into seconds.

    Supports formats like:
    - "30" or "30s" -> 30 seconds
    - "30m" -> 30 minutes (1800 seconds)
    - "2h" -> 2 hours (7200 seconds)
    - "1h30m" -> 1 hour 30 minutes (5400 seconds)
    - "1h30m45s" -> 1 hour 30 minutes 45 seconds

    Args:
        duration_str: Duration string to parse

    Returns:
        Duration in seconds

    Raises:
        ValueError: If the format is invalid
    """
    if not duration_str:
        raise ValueError("Empty duration string")

    # Try parsing as plain integer (seconds)
    try:
        return int(duration_str)
    except ValueError:
        pass

    # Parse format like "1h30m45s"
    pattern = r'^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$'
    match = re.match(pattern, duration_str.lower().strip())

    if not match:
        raise ValueError(f"Invalid duration format: {duration_str}. Use formats like '30m', '2h', '1h30m'")

    hours, minutes, seconds = match.groups()

    if not any([hours, minutes, seconds]):
        raise ValueError(f"Invalid duration format: {duration_str}. Use formats like '30m', '2h', '1h30m'")

    total_seconds = 0
    if hours:
        total_seconds += int(hours) * 3600
    if minutes:
        total_seconds += int(minutes) * 60
    if seconds:
        total_seconds += int(seconds)

    return total_seconds


def format_watch_progress(status: dict, pipeline: list[str]) -> str:
    """
    Format a one-line progress string for watch mode.

    Args:
        status: Status dict from tick_run()
        pipeline: List of pipeline step names

    Returns:
        Formatted progress string like:
        "generate: 4/6 valid | score_coherence: 0/4 (submitted) | score_wounds: waiting"
    """
    steps_info = status.get("steps", {})
    chunks_list = status.get("chunks", [])
    total_units = status.get("summary", {}).get("total_units", 0)

    parts = []

    for step_name in pipeline:
        # Defensive access - step may not be initialized in status dict yet
        if step_name not in steps_info:
            parts.append(f"{step_name}: waiting")
            continue

        step_data = steps_info.get(step_name, {})
        units_data = step_data.get("units", {})
        chunks_data = step_data.get("chunks", {})

        valid = units_data.get("valid", 0)
        failed = units_data.get("failed", 0)
        current_units = step_data.get("current_units", 0)

        # Determine step status
        submitted = chunks_data.get("submitted", 0)
        pending = chunks_data.get("pending", 0)
        completed = chunks_data.get("completed", 0)
        validated = chunks_data.get("validated", 0)

        if submitted > 0:
            # Check if any chunk at this step is polling
            polling = False
            for chunk in chunks_list:
                chunk_step = chunk.get("step")
                chunk_status = chunk.get("status")
                if chunk_step == step_name and chunk_status == "submitted":
                    polling = True
                    break

            if polling:
                parts.append(f"{step_name}: {valid}/{current_units} (polling...)")
            else:
                parts.append(f"{step_name}: {valid}/{current_units} (submitted)")
        elif pending > 0:
            parts.append(f"{step_name}: {valid}/{current_units} (pending)")
        elif validated > 0 or (valid + failed) > 0:
            if failed > 0:
                parts.append(f"{step_name}: {valid}/{valid+failed} valid ({failed} failed)")
            else:
                parts.append(f"{step_name}: {valid}/{valid+failed} valid")
        elif current_units == 0 and valid == 0:
            parts.append(f"{step_name}: waiting")
        else:
            parts.append(f"{step_name}: {valid}/{current_units}")

    return " | ".join(parts)


def watch_run(
    run_dir: Path,
    interval: int = 5,
    max_cost: float | None = None,
    timeout_seconds: int | None = None,
    max_retries: int = 5
) -> int:
    """
    Watch a run, automatically polling until completion.

    Args:
        run_dir: Path to run directory
        interval: Seconds between polls (default: 5)
        max_cost: Optional maximum cost in USD to allow
        timeout_seconds: Optional timeout in seconds
        max_retries: Maximum retry attempts per unit

    Returns:
        Exit code:
        - 0: Pipeline completed (reached terminal state)
        - 1: Pipeline error
        - 2: Cost limit exceeded
        - 3: Timeout exceeded
        - 130: User interrupted (Ctrl+C)
    """
    # Load config for prerequisite check
    _watch_config = None
    _watch_config_path = run_dir / "config" / "config.yaml"
    if _watch_config_path.exists():
        with open(_watch_config_path) as f:
            _watch_config = yaml.safe_load(f)

    # Check prerequisites early
    prereq_error = check_prerequisites(_watch_config)
    if prereq_error:
        print(f"Error: {prereq_error}", file=sys.stderr)
        mark_run_failed(run_dir, prereq_error, log_traceback=False)
        return 1

    # Log detected API keys for diagnostics
    log_file = run_dir / "RUN_LOG.txt"
    _log_api_key_status(log_file, "WATCH")

    # Scan for validation failures that can be retried (runs at startup, not gated by terminal state)
    manifest = load_manifest(run_dir)
    if manifest:
        archived = retry_validation_failures(run_dir, manifest, log_file)
        if archived > 0:
            manifest = load_manifest(run_dir)
        elif is_run_terminal(manifest, max_retries):
            log_message(log_file, "WATCH", "All chunks already terminal — nothing to do")
            mark_run_complete(run_dir)
            return 0

    # Track if we've been interrupted
    interrupted = False

    def signal_handler(signum, frame):
        nonlocal interrupted
        interrupted = True
        print("\n[Interrupted] Stopping watch mode...")

    # Install signal handler for Ctrl+C
    original_handler = signal.signal(signal.SIGINT, signal_handler)

    # Register PID file for process tracking
    write_pid_file(run_dir)
    atexit.register(cleanup_pid_file, run_dir)

    try:
        return _watch_loop(
            run_dir=run_dir,
            interval=interval,
            max_cost=max_cost,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            interrupted_flag=lambda: interrupted
        )
    finally:
        # Restore original signal handler
        signal.signal(signal.SIGINT, original_handler)
        # Clean up PID file
        cleanup_pid_file(run_dir)


def _watch_loop(
    run_dir: Path,
    interval: int,
    max_cost: float | None,
    timeout_seconds: int | None,
    max_retries: int,
    interrupted_flag: callable
) -> int:
    """
    Internal watch loop implementation.

    Args:
        run_dir: Path to run directory
        interval: Seconds between polls
        max_cost: Optional maximum cost in USD
        timeout_seconds: Optional timeout in seconds
        max_retries: Maximum retry attempts
        interrupted_flag: Callable that returns True if interrupted

    Returns:
        Exit code (see watch_run docstring)
    """
    # Load manifest to get pipeline info
    manifest_path = run_dir / "MANIFEST.json"
    if not manifest_path.exists():
        print(f"Error: MANIFEST.json not found in {run_dir}", file=sys.stderr)
        return 1

    manifest = load_manifest(run_dir)
    pipeline = manifest.get("pipeline", [])

    # Save poll interval to manifest so the TUI can read it
    if "metadata" not in manifest:
        manifest["metadata"] = {}
    manifest["metadata"]["poll_interval"] = interval
    manifest["metadata"]["mode"] = "batch"
    save_manifest(run_dir, manifest)

    # Format startup message
    now = datetime.now()
    time_str = now.strftime("%H:%M:%S")

    parts = [f"interval={interval}s"]
    if max_cost is not None:
        parts.append(f"max_cost=${max_cost:.4f}")
    if timeout_seconds is not None:
        parts.append(f"timeout={format_elapsed_time(timeout_seconds)}")

    print(f"[{time_str}] Starting watch mode ({', '.join(parts)})")

    start_time = time.time()
    tick_count = 0

    while True:
        # Check for interruption
        if interrupted_flag():
            return 130

        # Run a tick
        tick_count += 1
        status = tick_run(run_dir, max_retries=max_retries)

        # Check for errors
        if status.get("error"):
            now = datetime.now()
            time_str = now.strftime("%H:%M:%S")
            print(f"[{time_str}] Error: {status.get('error')}")
            return 1

        # Format and print progress
        now = datetime.now()
        time_str = now.strftime("%H:%M:%S")
        progress = format_watch_progress(status, pipeline)
        print(f"[{time_str}] {progress}")

        # Check cost limit
        cost_info = status.get("cost", {})
        estimated_cost = cost_info.get("estimated_cost_usd")

        if max_cost is not None and estimated_cost is not None:
            if estimated_cost > max_cost:
                print(f"[{time_str}] ! Cost limit exceeded: ${estimated_cost:.4f} > ${max_cost:.4f} max")
                print(f"[{time_str}] Pipeline paused. Run without --max-cost to continue.")
                return 2

        # Check timeout
        elapsed = time.time() - start_time
        if timeout_seconds is not None and elapsed > timeout_seconds:
            print(f"[{time_str}] ! Timeout exceeded: {format_elapsed_time(int(elapsed))}")
            print(f"[{time_str}] Pipeline still running. Re-run --watch to continue monitoring.")
            return 3

        # Check if terminal
        # Reload manifest to get updated state
        manifest = load_manifest(run_dir)
        if is_run_terminal(manifest, max_retries):
            # Run post-processing scripts before printing completion message
            config_path = run_dir / manifest["config"]
            with open(config_path) as f:
                config = yaml.safe_load(f)
            run_post_process(run_dir, config)

            # Pipeline complete!
            summary = status.get("summary", {})
            total_valid = summary.get("valid", 0)
            total_failed = summary.get("failed", 0)
            total = total_valid + total_failed

            failure_rate = 0
            if total > 0:
                failure_rate = (total_failed / total) * 100

            if total_failed == 0:
                print(f"[{time_str}] Pipeline complete: {total_valid} valid")
            else:
                print(f"[{time_str}] Pipeline complete: {total_valid} valid, {total_failed} failed ({failure_rate:.0f}% failure rate)")

            # Show token and cost summary
            manifest = load_manifest(run_dir)
            metadata = manifest.get("metadata", {})
            total_input = metadata.get("initial_input_tokens", 0) + metadata.get("retry_input_tokens", 0)
            total_output = metadata.get("initial_output_tokens", 0) + metadata.get("retry_output_tokens", 0)
            total_tokens = total_input + total_output

            if total_tokens > 0:
                # Load config to get provider for cost calculation
                config_path = run_dir / manifest.get("config", "config.yaml")
                with open(config_path) as f:
                    config = yaml.safe_load(f)
                try:
                    batch_provider = get_provider(config)
                    total_cost = compute_step_cost(total_input, total_output, batch_provider, is_realtime=False)
                except Exception:
                    total_cost = None

                if total_cost is not None:
                    print(f"[{time_str}] Total: {total_tokens:,} tokens ({total_input:,} in + {total_output:,} out) | ${total_cost:.4f}")
                else:
                    print(f"[{time_str}] Total: {total_tokens:,} tokens ({total_input:,} in + {total_output:,} out)")

            # Show output locations
            report_path = run_dir / "report.json"
            outputs_dir = run_dir / "outputs"
            if report_path.exists():
                print(f"[{time_str}] Report: {report_path}")
            if outputs_dir.exists():
                print(f"[{time_str}] Outputs: {outputs_dir}/")

            return 0

        # Check for interruption before sleeping
        if interrupted_flag():
            return 130

        # Wait for next tick
        time.sleep(interval)


# =============================================================================
# Expression Step Functions
# =============================================================================

def run_expression_step(
    run_dir: Path,
    chunk_name: str,
    step: str,
    step_config: dict,
    config: dict,
    manifest: dict,
    log_file: Path,
    progress_callback: callable = None
) -> tuple[int, int, int, int]:
    """
    Run an expression-only step (no LLM call).

    Evaluates expressions from step config for each unit and writes directly
    to validated output file.

    Args:
        run_dir: Path to run directory
        chunk_name: Name of chunk being processed
        step: Pipeline step name
        step_config: Step configuration dict containing 'expressions'
        config: Loaded config dict
        manifest: Loaded manifest dict
        log_file: Path to log file
        progress_callback: Optional callback for progress reporting

    Returns:
        Tuple of (valid_count, failed_count, input_tokens, output_tokens)
        Note: tokens are always 0 for expression steps
    """
    from expression_evaluator import evaluate_expressions, evaluate_condition, SeededRandom

    chunk_dir = run_dir / "chunks" / chunk_name
    pipeline = manifest["pipeline"]

    # Determine input file
    if step != pipeline[0]:
        step_idx = pipeline.index(step)
        prev_step = pipeline[step_idx - 1]
        input_file = chunk_dir / f"{prev_step}_validated.jsonl"
    else:
        input_file = chunk_dir / "units.jsonl"

    validated_file = chunk_dir / f"{step}_validated.jsonl"

    # Get expressions and loop configuration from step config
    expressions = step_config.get("expressions", {})
    init_expressions = step_config.get("init", {})
    loop_until_expr = step_config.get("loop_until")
    max_iterations = step_config.get("max_iterations", 1000)

    if not expressions:
        log_message(log_file, "WARN", f"{chunk_name}/{step}: No expressions defined for expression step")
        return (0, 0, 0, 0)

    # Load input units
    if not input_file.exists():
        log_message(log_file, "ERROR", f"{chunk_name}/{step}: Input file not found: {input_file}")
        return (0, 0, 0, 0)

    units = []
    with open(input_file) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    units.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    if not units:
        log_message(log_file, "WARN", f"{chunk_name}/{step}: No input units found")
        return (0, 0, 0, 0)

    # Process each unit
    valid_count = 0
    failed_count = 0
    validated_units = []

    for unit in units:
        unit_id = unit.get("unit_id", "unknown")

        # Get seed - use _repetition_seed if present, otherwise hash of unit_id + step_name
        seed = unit.get("_repetition_seed", int(hashlib.sha256((unit_id + step).encode()).hexdigest(), 16) & 0x7FFFFFFF)

        try:
            output_unit = unit.copy()

            if loop_until_expr:
                # Looping expression step
                iteration = 0
                condition_met = False

                # Create a persistent RNG for this unit - advances state naturally through loop
                rng = SeededRandom(seed)

                # Evaluate init expressions ONCE before loop starts
                if init_expressions:
                    init_results = evaluate_expressions(init_expressions, output_unit, rng)
                    output_unit.update(init_results)

                while iteration < max_iterations:
                    iteration += 1

                    # Evaluate expressions using the persistent RNG
                    # (RNG state advances naturally, no re-seeding)
                    expr_results = evaluate_expressions(expressions, output_unit, rng)
                    output_unit.update(expr_results)

                    # Check loop condition (no randomness needed for condition check)
                    if evaluate_condition(loop_until_expr, output_unit):
                        condition_met = True
                        break

                # Timeout only if loop exited without the condition being satisfied
                timed_out = not condition_met

                # Record iteration count and timeout flag
                if "_metadata" not in output_unit:
                    output_unit["_metadata"] = {}
                output_unit["_metadata"]["expression_step"] = step
                output_unit["_metadata"]["iterations"] = iteration
                output_unit["_metadata"]["timeout"] = timed_out

                if timed_out:
                    # Timeout is not a failure — unit continues through the pipeline
                    log_message(log_file, "WARN", f"{chunk_name}/{step}: Unit {unit_id} reached max_iterations ({max_iterations}) without satisfying loop_until")

                    if progress_callback:
                        progress_callback(unit_id, True, "timeout", 0, 0, f"Loop reached max_iterations ({max_iterations})")

            else:
                # Non-looping expression step
                # Create a single SeededRandom instance shared between init and expressions
                rng = SeededRandom(seed)

                # Still support init for non-looping steps (evaluated before expressions)
                if init_expressions:
                    init_results = evaluate_expressions(init_expressions, output_unit, rng)
                    output_unit.update(init_results)

                expr_results = evaluate_expressions(expressions, output_unit, rng)
                output_unit.update(expr_results)

                # Add metadata
                if "_metadata" not in output_unit:
                    output_unit["_metadata"] = {}
                output_unit["_metadata"]["expression_step"] = step

            validated_units.append(output_unit)
            valid_count += 1

            # Report progress (skip if already reported in timeout block above)
            if progress_callback and not (loop_until_expr and timed_out):
                progress_callback(unit_id, True, None, 0, 0)

        except Exception as e:
            log_message(log_file, "ERROR", f"{chunk_name}/{step}: Expression error for {unit_id}: {e}")
            failed_count += 1
            if progress_callback:
                progress_callback(unit_id, False, "expression_error", 0, 0, str(e))

    # Write validated output
    with open(validated_file, 'w') as f:
        for unit in validated_units:
            f.write(json.dumps(unit) + "\n")

    return (valid_count, failed_count, 0, 0)


def is_expression_step(config: dict, step_name: str) -> bool:
    """Check if a step is an expression-only step (scope: expression)."""
    steps = config.get("pipeline", {}).get("steps", [])
    for step in steps:
        if step.get("name") == step_name:
            return step.get("scope") == "expression"
    return False


def get_expression_step_config(config: dict, step_name: str) -> dict | None:
    """Get the full config dict for an expression step."""
    steps = config.get("pipeline", {}).get("steps", [])
    for step in steps:
        if step.get("name") == step_name and step.get("scope") == "expression":
            return step
    return None


# =============================================================================
# Real-time Mode Functions
# =============================================================================

def run_step_realtime(
    run_dir: Path,
    chunk_name: str,
    step: str,
    config: dict,
    manifest: dict,
    log_file: Path,
    progress_callback: callable = None
) -> tuple[int, int, int, int]:
    """
    Run a single step in real-time mode using synchronous API calls.

    Args:
        run_dir: Path to run directory
        chunk_name: Name of chunk being processed
        step: Pipeline step name
        config: Loaded config dict
        manifest: Loaded manifest dict
        log_file: Path to log file
        progress_callback: Optional callback(unit_id, success, error_type, input_tokens, output_tokens) for progress

    Returns:
        Tuple of (valid_count, failed_count, input_tokens, output_tokens)
    """
    from realtime_provider import run_realtime, FatalProviderError
    from scripts.providers import get_step_provider, ProviderError

    chunk_dir = run_dir / "chunks" / chunk_name
    pipeline = manifest["pipeline"]
    chunk_data = manifest["chunks"][chunk_name]
    config_path = run_dir / manifest["config"]
    subprocess_timeout = get_subprocess_timeout(config)

    # Idempotency check: skip step if output (valid + failed) already covers expected items.
    # This prevents resumed runs from blindly reprocessing completed steps.
    # For steps after the first, expected_items is capped by the input file size
    # (earlier steps may have filtered out units, so chunk_data["items"] overstates).
    expected_items = chunk_data.get("items", 0)
    if step != pipeline[0]:
        step_idx = pipeline.index(step)
        prev_step = pipeline[step_idx - 1]
        input_path = chunk_dir / f"{prev_step}_validated.jsonl"
        if input_path.exists():
            with open(input_path, 'r', encoding='utf-8') as f:
                input_count = sum(1 for line in f if line.strip())
            expected_items = min(expected_items, input_count)

    if expected_items > 0:
        validated_path = chunk_dir / f"{step}_validated.jsonl"
        failures_path = chunk_dir / f"{step}_failures.jsonl"

        existing_valid_count = 0
        if validated_path.exists():
            with open(validated_path, 'r', encoding='utf-8') as f:
                existing_valid_count = sum(1 for line in f if line.strip())

        existing_failed_count = 0
        if failures_path.exists():
            with open(failures_path, 'r', encoding='utf-8') as f:
                existing_failed_count = sum(1 for line in f if line.strip())

        total_processed = existing_valid_count + existing_failed_count

        # Fallback: if no failures file exists (or is empty) but we have valid
        # output covering >=90% of expected items, treat the step as complete.
        # Some units may have been silently filtered by validation without being
        # written to the failures file.
        # Skip this fallback if a .bak file exists — that means failures were
        # archived for retry, and we should reprocess the missing units.
        bak_path = chunk_dir / f"{step}_failures.jsonl.bak"
        if existing_valid_count > 0 and existing_failed_count == 0:
            if not bak_path.exists() and existing_valid_count >= expected_items * 0.9:
                total_processed = expected_items  # Treat as complete

        if total_processed >= expected_items:
            log_message(log_file, "SKIP",
                f"{chunk_name}/{step}: already complete "
                f"({existing_valid_count} valid, {existing_failed_count} failed, "
                f"expected {expected_items})")
            # Advance chunk state so the convergence loop picks up the next step
            next_step = get_next_step(pipeline, step)
            chunk_data["state"] = f"{next_step}_PENDING" if next_step else "VALIDATED"
            chunk_data["valid"] = existing_valid_count
            chunk_data["failed"] = existing_failed_count
            # Clean up retry signal if present
            bak_path.unlink(missing_ok=True)
            return (existing_valid_count, existing_failed_count, 0, 0)

    # Determine input file for prompt preparation
    retry_step = chunk_data.get("retry_step")
    is_retry_chunk = retry_step is not None or chunk_name.startswith("retry_")

    if is_retry_chunk and retry_step == step:
        units_file = chunk_dir / "units.jsonl"
    elif is_retry_chunk and retry_step != step:
        step_idx = pipeline.index(step)
        prev_step = pipeline[step_idx - 1]
        units_file = chunk_dir / f"{prev_step}_validated.jsonl"
        if not units_file.exists():
            log_message(log_file, "ERROR",
                f"Cannot run {step}: previous step {prev_step} has no validated output "
                f"(missing {units_file.name})")
            chunk_data["state"] = "FAILED"
            return (0, 0, 0, 0)
    elif step != pipeline[0]:
        step_idx = pipeline.index(step)
        prev_step = pipeline[step_idx - 1]
        units_file = chunk_dir / f"{prev_step}_validated.jsonl"
        if not units_file.exists():
            log_message(log_file, "ERROR",
                f"Cannot run {step}: previous step {prev_step} has no validated output "
                f"(missing {units_file.name})")
            chunk_data["state"] = "FAILED"
            return (0, 0, 0, 0)
    else:
        units_file = chunk_dir / "units.jsonl"

    prompts_file = chunk_dir / f"{step}_prompts.jsonl"
    results_file = chunk_dir / f"{step}_results.jsonl"
    validated_file = chunk_dir / f"{step}_validated.jsonl"
    failures_file = chunk_dir / f"{step}_failures.jsonl"

    # Prepare prompts
    success, error_msg = prepare_prompts(units_file, prompts_file, config_path, step,
                                         timeout=subprocess_timeout)
    if not success:
        log_message(log_file, "ERROR", f"{chunk_name}: Failed to prepare prompts: {error_msg}")
        chunk_data["state"] = "FAILED"
        return (0, 0, 0, 0)

    # Load prompts
    prompts = []
    with open(prompts_file) as f:
        for line in f:
            line = line.strip()
            if line:
                prompts.append(json.loads(line))

    if not prompts:
        log_message(log_file, "WARN", f"{chunk_name}: No prompts to process")
        return (0, 0, 0, 0)

    # Initialize provider using dependency injection (with per-step overrides)
    try:
        provider = get_step_provider(config, step, manifest)
    except ProviderError as e:
        log_message(log_file, "ERROR", f"Failed to initialize provider: {e}")
        return (0, 0, 0, 0)

    # Extract retry config settings
    retry_config = config.get("api", {}).get("retry", {})
    rt_max_retries = retry_config.get("max_attempts", 3)
    rt_initial_backoff = retry_config.get("initial_delay_seconds", 1.0)
    rt_backoff_multiplier = retry_config.get("backoff_multiplier", 2)

    # Trace callback for per-request telemetry
    _prov_name = config.get("api", {}).get("provider", "unknown")
    def _trace_cb(unit_id, duration_secs, status_str):
        trace_log(run_dir, f"[API] {_prov_name} {chunk_name} {unit_id} | {duration_secs:.2f}s | {status_str}")

    # Make real-time API calls using provider abstraction
    try:
        results = run_realtime(
            prompts, provider,
            max_retries=rt_max_retries,
            initial_backoff=rt_initial_backoff,
            backoff_multiplier=rt_backoff_multiplier,
            progress_callback=progress_callback,
            trace_callback=_trace_cb
        )
    except FatalProviderError:
        raise  # Auth/billing errors must abort the entire run
    except ProviderError as e:
        log_message(log_file, "ERROR", f"{chunk_name}: Real-time API error: {e}")
        return (0, 0, 0, 0)

    # Track tokens
    total_input_tokens = 0
    total_output_tokens = 0

    # Write results to file (same format as batch collection)
    # Collect non-JSON responses to write as pipeline_internal failures
    non_json_failures = []
    with open(results_file, 'w') as f:
        for result in results:
            unit_id = result.get("unit_id")
            metadata = result.get("_metadata", {})

            total_input_tokens += metadata.get("input_tokens", 0)
            total_output_tokens += metadata.get("output_tokens", 0)

            # Check if this is an error result
            if "error" in result:
                f.write(json.dumps({
                    "unit_id": unit_id,
                    "error": result["error"],
                    "_metadata": metadata
                }) + '\n')
            # Check if realtime_provider already parsed and merged the JSON
            # (has fields other than unit_id, response, _metadata, _raw_text)
            elif any(k not in ("unit_id", "response", "_metadata", "_raw_text") for k in result.keys()):
                # Already parsed - write directly
                f.write(json.dumps(result) + '\n')
            # Raw response needs parsing
            elif "response" in result:
                response = result["response"]
                if response:
                    try:
                        # Try to parse as JSON
                        parsed = json.loads(response) if isinstance(response, str) else response
                        # Ensure unit_id is present
                        if isinstance(parsed, dict):
                            parsed["unit_id"] = unit_id
                            parsed["_metadata"] = metadata
                            if "_raw_text" in result:
                                parsed["_raw_text"] = result["_raw_text"]
                            f.write(json.dumps(parsed) + '\n')
                        else:
                            # Non-JSON dict response — categorize as pipeline_internal
                            non_json_failures.append({
                                "unit_id": unit_id,
                                "failure_stage": "pipeline_internal",
                                "raw_response": response if isinstance(response, str) else str(response),
                                "errors": [{"path": "$", "rule": "pipeline_internal", "message": "LLM response is not a JSON object"}],
                                "retry_count": 0
                            })
                    except json.JSONDecodeError:
                        # Non-JSON response — categorize as pipeline_internal
                        non_json_failures.append({
                            "unit_id": unit_id,
                            "failure_stage": "pipeline_internal",
                            "raw_response": response,
                            "errors": [{"path": "$", "rule": "pipeline_internal", "message": "LLM response is not valid JSON"}],
                            "retry_count": 0
                        })
                else:
                    # Empty response — categorize as pipeline_internal
                    non_json_failures.append({
                        "unit_id": unit_id,
                        "failure_stage": "pipeline_internal",
                        "raw_response": "",
                        "errors": [{"path": "$", "rule": "pipeline_internal", "message": "Empty LLM response"}],
                        "retry_count": 0
                    })
            else:
                # No data in result — categorize as pipeline_internal
                non_json_failures.append({
                    "unit_id": unit_id,
                    "failure_stage": "pipeline_internal",
                    "raw_response": None,
                    "errors": [{"path": "$", "rule": "pipeline_internal", "message": "No response data from LLM"}],
                    "retry_count": 0
                })

    # Run validation pipeline
    schema_path = get_schema_path(config, step, run_dir)

    # Determine input file for validation (same logic as tick_run)
    if step == pipeline[0]:
        step_input_file = chunk_dir / "units.jsonl"
    else:
        step_idx = pipeline.index(step)
        prev_step = pipeline[step_idx - 1]
        step_input_file = chunk_dir / f"{prev_step}_validated.jsonl"

    valid_count, failed_count = run_validation_pipeline(
        results_file, validated_file, failures_file,
        schema_path, config_path, step, log_file, chunk_name,
        input_file=step_input_file,
        timeout=subprocess_timeout
    )

    # Append non-JSON failures (pipeline_internal) to failures file
    if non_json_failures:
        with open(failures_file, 'a') as f:
            for nj_failure in non_json_failures:
                f.write(json.dumps(nj_failure) + '\n')
        failed_count += len(non_json_failures)
        log_message(log_file, "VALIDATE", f"{chunk_name}/{step}: {len(non_json_failures)} non-JSON responses categorized as pipeline_internal")

    # Update chunk data
    chunk_data["valid"] = valid_count
    chunk_data["failed"] = failed_count
    chunk_data["input_tokens"] = chunk_data.get("input_tokens", 0) + total_input_tokens
    chunk_data["output_tokens"] = chunk_data.get("output_tokens", 0) + total_output_tokens

    # If zero valid and some failed, mark chunk FAILED — don't advance
    if valid_count == 0 and failed_count > 0:
        log_message(log_file, "STOP", f"{chunk_name}: Step {step} produced 0 valid units out of {failed_count}. Marking chunk as FAILED.")
        chunk_data["state"] = "FAILED"
        chunk_data["batch_id"] = None
        chunk_data["submitted_at"] = None
        chunk_data["provider_status"] = None
        return (valid_count, failed_count, total_input_tokens, total_output_tokens)

    # Determine next state
    next_step = get_next_step(pipeline, step)
    if next_step:
        new_state = f"{next_step}_PENDING"
    else:
        new_state = "VALIDATED"

    chunk_data["state"] = new_state
    chunk_data["batch_id"] = None
    chunk_data["submitted_at"] = None
    chunk_data["provider_status"] = None

    # Clean up retry signal file now that step completed
    bak_path = chunk_dir / f"{step}_failures.jsonl.bak"
    bak_path.unlink(missing_ok=True)

    return (valid_count, failed_count, total_input_tokens, total_output_tokens)


def run_realtime_retries(
    run_dir: Path,
    step: str,
    config: dict,
    manifest: dict,
    log_file: Path,
    max_retries: int = 5
) -> tuple[int, int, int, int]:
    """
    Run retries for failed units at a specific step.

    Scans all chunks for failures at the given step, filters to those
    with retry_count < max_retries, and re-runs them through the API.

    Args:
        run_dir: Path to run directory
        step: Pipeline step name to retry
        config: Loaded config dict
        manifest: Loaded manifest dict
        log_file: Path to log file
        max_retries: Maximum retry attempts per item

    Returns:
        Tuple of (retried_count, still_failed_count, input_tokens, output_tokens)
    """
    from realtime_provider import run_realtime, FatalProviderError
    from scripts.providers import get_step_provider, ProviderError

    chunks = manifest["chunks"]
    pipeline = manifest["pipeline"]
    chunks_dir = run_dir / "chunks"
    config_path = run_dir / manifest["config"]

    # Collect retryable failures across all chunks
    # Structure: {unit_id: {"input": {...}, "chunk_name": str, "retry_count": int}}
    retryable_failures = {}

    for chunk_name, chunk_data in chunks.items():
        chunk_dir = chunks_dir / chunk_name
        failures_file = chunk_dir / f"{step}_failures.jsonl"

        if not failures_file.exists():
            continue

        # Determine the input source for this step
        step_idx = pipeline.index(step) if step in pipeline else 0
        if step_idx == 0:
            input_source = chunk_dir / "units.jsonl"
        else:
            prev_step = pipeline[step_idx - 1]
            input_source = chunk_dir / f"{prev_step}_validated.jsonl"

        # Load input data by unit_id
        input_by_unit_id = {}
        if input_source.exists():
            with open(input_source) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                        uid = item.get("unit_id")
                        if uid:
                            input_by_unit_id[uid] = item
                    except json.JSONDecodeError:
                        continue

        # Read failures
        with open(failures_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    failure = json.loads(line)
                except json.JSONDecodeError:
                    continue

                unit_id = failure.get("unit_id")
                if not unit_id:
                    continue

                retry_count = failure.get("retry_count", 0)

                # Check if retryable
                if retry_count >= max_retries:
                    continue

                # Get input data
                input_data = input_by_unit_id.get(unit_id)
                if not input_data:
                    continue

                # Deduplicate - keep the one with highest retry_count
                if unit_id not in retryable_failures:
                    retryable_failures[unit_id] = {
                        "input": input_data,
                        "chunk_name": chunk_name,
                        "retry_count": retry_count
                    }
                elif retry_count > retryable_failures[unit_id]["retry_count"]:
                    retryable_failures[unit_id]["retry_count"] = retry_count

    if not retryable_failures:
        return (0, 0, 0, 0)

    # Prepare prompts for retryable failures
    # Write temporary units file and prepare prompts
    retry_units_file = run_dir / f".retry_{step}_units.jsonl"
    retry_prompts_file = run_dir / f".retry_{step}_prompts.jsonl"

    with open(retry_units_file, 'w') as f:
        for unit_id, info in retryable_failures.items():
            unit = info["input"].copy()
            unit["unit_id"] = unit_id
            unit["retry_count"] = info["retry_count"] + 1  # Increment for this attempt
            f.write(json.dumps(unit) + '\n')

    # Prepare prompts
    retry_timeout = get_subprocess_timeout(config)
    success, error_msg = prepare_prompts(retry_units_file, retry_prompts_file, config_path, step,
                                         timeout=retry_timeout)
    if not success:
        log_message(log_file, "ERROR", f"Failed to prepare retry prompts: {error_msg}")
        retry_units_file.unlink(missing_ok=True)
        return (0, 0, 0, 0)

    # Load prompts
    prompts = []
    with open(retry_prompts_file) as f:
        for line in f:
            line = line.strip()
            if line:
                prompts.append(json.loads(line))

    if not prompts:
        retry_units_file.unlink(missing_ok=True)
        retry_prompts_file.unlink(missing_ok=True)
        return (0, 0, 0, 0)

    # Initialize provider using dependency injection (with per-step overrides)
    try:
        provider = get_step_provider(config, step, manifest)
    except ProviderError as e:
        log_message(log_file, "ERROR", f"Failed to initialize provider for retry: {e}")
        retry_units_file.unlink(missing_ok=True)
        retry_prompts_file.unlink(missing_ok=True)
        return (0, 0, 0, 0)

    # Extract retry config settings
    retry_cfg = config.get("api", {}).get("retry", {})
    retry_max = retry_cfg.get("max_attempts", 3)
    retry_backoff = retry_cfg.get("initial_delay_seconds", 1.0)
    retry_multiplier = retry_cfg.get("backoff_multiplier", 2)

    # Trace callback for per-request telemetry
    _prov_name = config.get("api", {}).get("provider", "unknown")
    def _trace_cb(unit_id, duration_secs, status_str):
        trace_log(run_dir, f"[API] {_prov_name} retry {unit_id} | {duration_secs:.2f}s | {status_str}")

    # Make API calls using provider abstraction
    try:
        results = run_realtime(prompts, provider, max_retries=retry_max, initial_backoff=retry_backoff, backoff_multiplier=retry_multiplier, trace_callback=_trace_cb)
    except FatalProviderError:
        raise  # Auth/billing errors must abort — propagate to caller
    except ProviderError as e:
        log_message(log_file, "ERROR", f"Retry API error: {e}")
        retry_units_file.unlink(missing_ok=True)
        retry_prompts_file.unlink(missing_ok=True)
        return (0, 0, 0, 0)

    # Track tokens
    total_input_tokens = 0
    total_output_tokens = 0

    # Write results to temp file
    # Collect non-JSON responses to write as pipeline_internal failures
    retry_non_json_failures = []
    retry_results_file = run_dir / f".retry_{step}_results.jsonl"
    with open(retry_results_file, 'w') as f:
        for result in results:
            unit_id = result.get("unit_id")
            metadata = result.get("_metadata", {})
            total_input_tokens += metadata.get("input_tokens", 0)
            total_output_tokens += metadata.get("output_tokens", 0)

            # Carry forward retry_count from the input unit
            result_retry_count = retryable_failures.get(unit_id, {}).get("retry_count", 0) + 1

            # Check if this is an error result
            if "error" in result:
                f.write(json.dumps({
                    "unit_id": unit_id,
                    "error": result["error"],
                    "_metadata": metadata
                }) + '\n')
            elif any(k not in ("unit_id", "response", "_metadata", "_raw_text") for k in result.keys()):
                # Already parsed - write directly
                f.write(json.dumps(result) + '\n')
            elif "response" in result:
                response = result["response"]
                if response:
                    try:
                        parsed = json.loads(response) if isinstance(response, str) else response
                        if isinstance(parsed, dict):
                            parsed["unit_id"] = unit_id
                            parsed["_metadata"] = metadata
                            if "_raw_text" in result:
                                parsed["_raw_text"] = result["_raw_text"]
                            f.write(json.dumps(parsed) + '\n')
                        else:
                            # Non-JSON dict response — categorize as pipeline_internal
                            retry_non_json_failures.append({
                                "unit_id": unit_id,
                                "failure_stage": "pipeline_internal",
                                "raw_response": response if isinstance(response, str) else str(response),
                                "errors": [{"path": "$", "rule": "pipeline_internal", "message": "LLM response is not a JSON object"}],
                                "retry_count": result_retry_count
                            })
                    except json.JSONDecodeError:
                        # Non-JSON response — categorize as pipeline_internal
                        retry_non_json_failures.append({
                            "unit_id": unit_id,
                            "failure_stage": "pipeline_internal",
                            "raw_response": response,
                            "errors": [{"path": "$", "rule": "pipeline_internal", "message": "LLM response is not valid JSON"}],
                            "retry_count": result_retry_count
                        })
                else:
                    # Empty response — categorize as pipeline_internal
                    retry_non_json_failures.append({
                        "unit_id": unit_id,
                        "failure_stage": "pipeline_internal",
                        "raw_response": "",
                        "errors": [{"path": "$", "rule": "pipeline_internal", "message": "Empty LLM response"}],
                        "retry_count": result_retry_count
                    })
            else:
                # No data in result — categorize as pipeline_internal
                retry_non_json_failures.append({
                    "unit_id": unit_id,
                    "failure_stage": "pipeline_internal",
                    "raw_response": None,
                    "errors": [{"path": "$", "rule": "pipeline_internal", "message": "No response data from LLM"}],
                    "retry_count": result_retry_count
                })

    # Validate results
    retry_validated_file = run_dir / f".retry_{step}_validated.jsonl"
    retry_failures_file = run_dir / f".retry_{step}_failures.jsonl"

    schema_path = get_schema_path(config, step, run_dir)

    valid_count, failed_count = run_validation_pipeline(
        retry_results_file, retry_validated_file, retry_failures_file,
        schema_path, config_path, step, log_file, "retry",
        input_file=retry_units_file,
        timeout=retry_timeout
    )

    # Append non-JSON failures (pipeline_internal) to retry failures file
    if retry_non_json_failures:
        with open(retry_failures_file, 'a') as f:
            for nj_failure in retry_non_json_failures:
                f.write(json.dumps(nj_failure) + '\n')
        failed_count += len(retry_non_json_failures)
        log_message(log_file, "VALIDATE", f"retry/{step}: {len(retry_non_json_failures)} non-JSON responses categorized as pipeline_internal")

    # Merge results back into the original chunk files
    # For each validated result, append to the original chunk's validated file
    # For each failed result, replace in the original chunk's failures file

    # Track which units passed and which failed
    validated_units = set()
    if retry_validated_file.exists():
        with open(retry_validated_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                    unit_id = item.get("unit_id")
                    if unit_id:
                        validated_units.add(unit_id)
                        # Append to original chunk's validated file
                        chunk_name = retryable_failures[unit_id]["chunk_name"]
                        chunk_validated = chunks_dir / chunk_name / f"{step}_validated.jsonl"
                        with open(chunk_validated, 'a') as vf:
                            vf.write(json.dumps(item) + '\n')
                except json.JSONDecodeError:
                    continue

    # For failures, we need to update the original failures files
    # Remove old failure records for units we retried, add new failure records
    failed_units = {}
    if retry_failures_file.exists():
        with open(retry_failures_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                    unit_id = item.get("unit_id")
                    if unit_id:
                        failed_units[unit_id] = item
                except json.JSONDecodeError:
                    continue

    # Update each chunk's failures file
    chunks_to_update = set(info["chunk_name"] for info in retryable_failures.values())
    for chunk_name in chunks_to_update:
        chunk_failures_file = chunks_dir / chunk_name / f"{step}_failures.jsonl"
        if not chunk_failures_file.exists():
            continue

        # Read existing failures, filter out retried units
        remaining_failures = []
        with open(chunk_failures_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                    unit_id = item.get("unit_id")
                    # Keep if not in our retry set
                    if unit_id not in retryable_failures:
                        remaining_failures.append(item)
                except json.JSONDecodeError:
                    continue

        # Add back units that failed again (with updated retry_count)
        for unit_id, failure in failed_units.items():
            if retryable_failures.get(unit_id, {}).get("chunk_name") == chunk_name:
                remaining_failures.append(failure)

        # Write back
        with open(chunk_failures_file, 'w') as f:
            for failure in remaining_failures:
                f.write(json.dumps(failure) + '\n')

        # Update chunk valid/failed counts
        chunk_data = manifest["chunks"][chunk_name]
        chunk_data["valid"] = chunk_data.get("valid", 0) + sum(
            1 for uid in validated_units
            if retryable_failures.get(uid, {}).get("chunk_name") == chunk_name
        )
        chunk_data["failed"] = len([
            f for f in remaining_failures
            if f.get("unit_id") in retryable_failures
        ])

    # Clean up temp files
    retry_units_file.unlink(missing_ok=True)
    retry_prompts_file.unlink(missing_ok=True)
    retry_results_file.unlink(missing_ok=True)
    retry_validated_file.unlink(missing_ok=True)
    retry_failures_file.unlink(missing_ok=True)

    return (valid_count, failed_count, total_input_tokens, total_output_tokens)


def realtime_run(
    run_dir: Path,
    max_retries: int = 5,
    skip_confirmation: bool = False
) -> int:
    """
    Run pipeline in real-time mode with synchronous API calls.

    Args:
        run_dir: Path to run directory
        max_retries: Maximum retry attempts per unit
        skip_confirmation: Skip cost confirmation prompt

    Returns:
        Exit code (0 = success, 1 = error)
    """
    # Validate run directory
    manifest_path = run_dir / "MANIFEST.json"
    if not manifest_path.exists():
        print(f"Error: MANIFEST.json not found in {run_dir}", file=sys.stderr)
        return 1

    # Load config for prerequisite check
    _rt_config = None
    _rt_config_path = run_dir / "config" / "config.yaml"
    if _rt_config_path.exists():
        with open(_rt_config_path) as f:
            _rt_config = yaml.safe_load(f)

    # Check prerequisites early
    prereq_error = check_prerequisites(_rt_config)
    if prereq_error:
        print(f"Error: {prereq_error}", file=sys.stderr)
        mark_run_failed(run_dir, prereq_error, log_traceback=False)
        return 1

    log_file = run_dir / "RUN_LOG.txt"

    # Log detected API keys for diagnostics
    _log_api_key_status(log_file, "REALTIME")

    # Register PID file for process tracking
    write_pid_file(run_dir)
    atexit.register(cleanup_pid_file, run_dir)

    # Load manifest and config
    manifest = load_manifest(run_dir)
    pipeline = manifest["pipeline"]
    chunks = manifest["chunks"]

    # Scan for validation failures that can be retried (runs at startup, not gated by terminal state)
    archived = retry_validation_failures(run_dir, manifest, log_file)
    if archived > 0:
        # Manifest was mutated by retry_validation_failures — reload
        manifest = load_manifest(run_dir)
        pipeline = manifest["pipeline"]
        chunks = manifest["chunks"]
    elif is_run_terminal(manifest, max_retries):
        log_message(log_file, "REALTIME", "All chunks already terminal — nothing to do")
        mark_run_complete(run_dir)
        return 0

    config_path = run_dir / manifest["config"]
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Count total units
    total_units = sum(c.get("items", 0) for c in chunks.values())

    # Cost safety warning for large runs
    if total_units > 50 and not skip_confirmation:
        print(f"Warning: Real-time mode costs 2x batch rate. Running {total_units} units in real-time.")
        print("Consider using batch mode for large runs. Continue? [y/N] ", end="", flush=True)
        try:
            response = input().strip().lower()
            if response not in ('y', 'yes'):
                print("Aborted.")
                return 0
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 0

    # Initialize metadata if needed
    if "metadata" not in manifest:
        manifest["metadata"] = {
            "initial_input_tokens": 0,
            "initial_output_tokens": 0,
            "retry_input_tokens": 0,
            "retry_output_tokens": 0
        }

    # Mark this run as realtime mode for cost calculation
    manifest["metadata"]["mode"] = "realtime"
    save_manifest(run_dir, manifest)

    # Get provider and realtime settings
    from scripts.providers import get_provider, get_step_provider, ProviderError
    from realtime_provider import FatalProviderError

    api_config = config.get("api", {})
    provider_name = api_config.get("provider", "gemini")
    realtime_config = api_config.get("realtime", {})
    cost_cap = realtime_config.get("cost_cap_usd")

    # Initialize fallback provider instance for cost estimation
    realtime_provider = None
    try:
        realtime_provider = get_provider(config)
    except (ProviderError, Exception):
        pass  # Provider unavailable - costs will show as $0

    # Print start message
    time_str = datetime.now().strftime("%H:%M:%S")
    print(f"[{time_str}] Real-time mode (2x cost)")
    log_message(log_file, "REALTIME", "Starting real-time execution")

    # Reset retry counts for fresh retry attempts this invocation
    # Each --realtime run gets a fresh set of retries
    chunks_dir = run_dir / "chunks"
    for chunk_name in chunks:
        chunk_dir = chunks_dir / chunk_name
        for step in pipeline:
            failures_file = chunk_dir / f"{step}_failures.jsonl"
            if failures_file.exists():
                failures = []
                with open(failures_file) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                failure = json.loads(line)
                                failure["retry_count"] = 0
                                failures.append(failure)
                            except json.JSONDecodeError:
                                continue
                with open(failures_file, 'w') as f:
                    for failure in failures:
                        f.write(json.dumps(failure) + '\n')

    # Track totals
    total_valid = 0
    total_failed = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_retried = 0
    cumulative_cost = 0.0
    cost_cap_reached = False

    # Check if this is a re-run (no pending chunks)
    has_pending = any(
        parse_state(c["state"])[1] == "PENDING"
        for c in chunks.values()
    )

    # Convergence loop: re-scan pipeline until no more chunks advance
    max_passes = len(pipeline) + 1
    for pass_num in range(max_passes):
        progress_this_pass = 0

        if pass_num > 0:
            time_str = datetime.now().strftime("%H:%M:%S")
            print(f"[{time_str}] Convergence pass {pass_num + 1}: re-scanning pipeline for advanced chunks...")
            log_message(log_file, "REALTIME", f"Convergence pass {pass_num + 1}")

        # Process each step in pipeline order
        for step in pipeline:
            # Check cost cap before processing
            if cost_cap is not None and cumulative_cost >= cost_cap:
                time_str = datetime.now().strftime("%H:%M:%S")
                remaining = sum(
                    1 for c in chunks.values()
                    if parse_state(c["state"])[1] == "PENDING"
                )
                print(f"[{time_str}] Cost cap reached (${cost_cap:.4f}). Stopping with {remaining} chunks incomplete.")
                log_message(log_file, "REALTIME", f"Cost cap reached: ${cumulative_cost:.4f} >= ${cost_cap:.4f}")
                cost_cap_reached = True
                break

            # Initialize error tracking for this step (MUST be before 'if chunks_for_step'
            # so variables are available for retry logic even if no chunks need this step)
            last_error_msg = [None]
            consecutive_same_error = [0]
            first_error_shown = [False]
            total_errors_this_step = [0]
            abort_requested = [False]

            # Find chunks that need this step
            chunks_for_step = []
            for chunk_name, chunk_data in chunks.items():
                current_step, status = parse_state(chunk_data["state"])
                if current_step == step and status == "PENDING":
                    chunks_for_step.append(chunk_name)

            if chunks_for_step:
                # Get step-specific provider for cost estimation
                step_cost_provider = realtime_provider
                try:
                    step_cost_provider = get_step_provider(config, step, manifest)
                except Exception:
                    pass  # Fall back to global provider

                # Count units for this step
                step_units = sum(chunks[c].get("items", 0) for c in chunks_for_step)

                # Log provider/model for this step
                provider_tag = format_step_provider_tag(config, step, step_cost_provider)
                time_str = datetime.now().strftime("%H:%M:%S")
                print(f"[{time_str}] Running {step} with {provider_tag}")
                log_message(log_file, "REALTIME", f"Running {step} with {provider_tag}")

                print(f"[{time_str}] Running {step} ({step_units} units)...")

                step_start = time.time()
                step_valid = 0
                step_failed = 0
                step_in_tokens = 0
                step_out_tokens = 0

                # Progress counter for this step (thread-safe with lock)
                progress_lock = threading.Lock()
                progress_count = [0]  # Use list for mutable closure
                running_input_tokens = [0]
                running_output_tokens = [0]
                running_cost = [0.0]
                last_manifest_update = [0]  # Track units since last manifest update
                cost_cap_hit = [False]  # Set True when per-unit cost cap check fires

                # Determine logging frequency based on total units
                if step_units < 20:
                    log_interval = 1  # Log every unit
                elif step_units <= 100:
                    log_interval = 10  # Log every 10 units
                else:
                    log_interval = 25  # Log every 25 units

                # Helper to format time remaining
                def format_time_remaining(seconds: int) -> str:
                    if seconds < 60:
                        return f"~{seconds}s left"
                    elif seconds < 3600:
                        minutes = seconds // 60
                        secs = seconds % 60
                        if secs == 0:
                            return f"~{minutes}m left"
                        return f"~{minutes}m {secs}s left"
                    else:
                        hours = seconds // 3600
                        minutes = (seconds % 3600) // 60
                        return f"~{hours}h {minutes}m left"

                # Helper to calculate cost from tokens (uses step-specific provider)
                def calculate_running_cost(in_tokens: int, out_tokens: int) -> float:
                    cost = compute_step_cost(in_tokens, out_tokens, step_cost_provider, is_realtime=True)
                    return cost if cost is not None else 0.0

                def progress_callback(unit_id: str, success: bool, error_type: str | None,
                                      input_tokens: int = 0, output_tokens: int = 0,
                                      error_message: str | None = None):
                    """Print progress after each unit completes."""
                    with progress_lock:
                        progress_count[0] += 1
                        count = progress_count[0]
                        running_input_tokens[0] += input_tokens
                        running_output_tokens[0] += output_tokens
                        running_cost[0] = calculate_running_cost(running_input_tokens[0], running_output_tokens[0])

                        # Calculate time remaining
                        elapsed = time.time() - step_start
                        if count > 0:
                            avg_time_per_unit = elapsed / count
                            remaining_units = step_units - count
                            remaining_seconds = int(avg_time_per_unit * remaining_units)
                        else:
                            remaining_seconds = 0

                        # Determine if we should show extended info (tokens, cost, time)
                        show_extended = (count % log_interval == 0) or (count == step_units)
                        total_tokens = running_input_tokens[0] + running_output_tokens[0]

                        # Error tracking for deduplication and early abort
                        show_error_detail = False
                        is_repeated_error = False
                        if not success:
                            total_errors_this_step[0] += 1
                            # Normalize error message for comparison
                            error_key = error_message or error_type or "unknown"
                            if error_key == last_error_msg[0]:
                                consecutive_same_error[0] += 1
                                is_repeated_error = True
                            else:
                                consecutive_same_error[0] = 1
                                last_error_msg[0] = error_key
                                is_repeated_error = False

                            # Show full error detail for first error only
                            if not first_error_shown[0] and error_message:
                                show_error_detail = True
                                first_error_shown[0] = True
                                # Log error to RUN_LOG.txt
                                log_message(log_file, "REALTIME_ERROR", f"{unit_id}: {error_message}")

                            # Check for auth/billing errors that should abort
                            if error_message:
                                error_lower = error_message.lower()
                                is_auth_error = any(term in error_lower for term in [
                                    "401", "403", "authentication", "unauthorized", "forbidden",
                                    "invalid api key", "api key invalid", "billing", "quota exceeded",
                                    "insufficient_quota", "rate limit exceeded"
                                ])
                                # If first batch (first chunk, all units fail with same auth error)
                                if is_auth_error and count <= 10 and total_errors_this_step[0] == count:
                                    abort_requested[0] = True

                    time_str = datetime.now().strftime("%H:%M:%S")

                    # Build output line
                    if success:
                        status_char = "✓"
                        error_suffix = ""
                    else:
                        status_char = "✗"
                        if error_type == "rate_limit":
                            error_suffix = " (rate limit)"
                        elif error_type == "timeout":
                            error_suffix = " (timeout)"
                        elif error_type == "api_error":
                            error_suffix = " (API error)"
                        else:
                            error_suffix = f" ({error_type or 'error'})"

                    if show_extended and total_tokens > 0:
                        time_remaining = format_time_remaining(remaining_seconds) if remaining_seconds > 0 else ""
                        if time_remaining:
                            print(f"[{time_str}] [{count}/{step_units}] {unit_id} {status_char}{error_suffix} | {total_tokens:,} tokens | ${running_cost[0]:.4f} | {time_remaining}", flush=True)
                        else:
                            print(f"[{time_str}] [{count}/{step_units}] {unit_id} {status_char}{error_suffix} | {total_tokens:,} tokens | ${running_cost[0]:.4f}", flush=True)
                    else:
                        print(f"[{time_str}] [{count}/{step_units}] {unit_id} {status_char}{error_suffix}", flush=True)

                    # Show detailed error message for first failure
                    if show_error_detail and error_message:
                        # Truncate very long error messages
                        display_msg = error_message[:500] + "..." if len(error_message) > 500 else error_message
                        print(f"         └─ Error: {display_msg}", flush=True)

                    # Update manifest periodically (every 10 units) for TUI integration
                    with progress_lock:
                        if count - last_manifest_update[0] >= 10 or count == step_units:
                            last_manifest_update[0] = count
                            # Update manifest with progress data
                            manifest["realtime_progress"] = {
                                "step": step,
                                "units_completed": count,
                                "units_total": step_units,
                                "tokens_so_far": total_tokens,
                                "cost_so_far": running_cost[0],
                                "estimated_remaining_seconds": remaining_seconds
                            }
                            # Atomic write to prevent TUI crashes on partial reads
                            manifest_path = run_dir / "MANIFEST.json"
                            temp_path = run_dir / "MANIFEST.json.tmp"
                            try:
                                with open(temp_path, 'w') as f:
                                    json.dump(manifest, f, indent=2)
                                os.replace(temp_path, manifest_path)
                            except Exception:
                                # If atomic write fails, try direct write
                                try:
                                    temp_path.unlink(missing_ok=True)
                                except Exception:
                                    pass

                    # Per-unit cost cap check
                    if cost_cap is not None and not cost_cap_hit[0]:
                        if cumulative_cost + running_cost[0] >= cost_cap:
                            cost_cap_hit[0] = True
                            return False  # Signal run_realtime to stop processing remaining units

                for chunk_name in chunks_for_step:
                    # Check if this is an expression-only step (no LLM call)
                    if is_expression_step(config, step):
                        step_config = get_expression_step_config(config, step)
                        valid, failed, in_tokens, out_tokens = run_expression_step(
                            run_dir, chunk_name, step, step_config, config, manifest, log_file,
                            progress_callback=progress_callback
                        )
                        # Advance chunk state after expression step completes
                        # (run_step_realtime does this internally, but expression steps don't)
                        chunk_data = manifest["chunks"][chunk_name]
                        chunk_data["valid"] = valid
                        chunk_data["failed"] = failed
                        next_step = get_next_step(pipeline, step)
                        if next_step:
                            chunk_data["state"] = f"{next_step}_PENDING"
                        else:
                            chunk_data["state"] = "VALIDATED"
                    else:
                        try:
                            valid, failed, in_tokens, out_tokens = run_step_realtime(
                                run_dir, chunk_name, step, config, manifest, log_file,
                                progress_callback=progress_callback
                            )
                        except FatalProviderError as e:
                            log_message(log_file, "ERROR", f"Fatal provider error — aborting run: {e}")
                            print(f"\n[FATAL] Auth/billing error — aborting run: {e}", flush=True)
                            save_manifest(run_dir, manifest)
                            mark_run_failed(run_dir, f"Fatal provider error: {e}")
                            return 1
                    step_valid += valid
                    step_failed += failed
                    step_in_tokens += in_tokens
                    step_out_tokens += out_tokens
                    total_input_tokens += in_tokens
                    total_output_tokens += out_tokens

                    # Save manifest after each chunk
                    manifest["metadata"]["initial_input_tokens"] += in_tokens
                    manifest["metadata"]["initial_output_tokens"] += out_tokens
                    save_manifest(run_dir, manifest)

                    # Per-unit cost cap: check if the progress callback detected cap hit
                    if cost_cap is not None and cost_cap_hit[0]:
                        time_str = datetime.now().strftime("%H:%M:%S")
                        print(f"[{time_str}] Cost cap reached (${cost_cap:.4f}). Stopped during {chunk_name}.")
                        log_message(log_file, "REALTIME", f"Cost cap reached at unit level: ${cumulative_cost + running_cost[0]:.4f} >= ${cost_cap:.4f}")
                        cost_cap_reached = True
                        break

                    # Check for early abort (auth/billing errors affecting all units)
                    if abort_requested[0]:
                        time_str = datetime.now().strftime("%H:%M:%S")
                        error_desc = last_error_msg[0] or "authentication/billing error"
                        # Truncate for display
                        if len(error_desc) > 100:
                            error_desc = error_desc[:100] + "..."
                        print(f"\n[{time_str}] ABORTING: All units failed with same error.", flush=True)
                        print(f"         └─ {error_desc}", flush=True)
                        print(f"         └─ Check your API key and billing status.", flush=True)
                        log_message(log_file, "REALTIME", f"Early abort: all {total_errors_this_step[0]} units failed with: {last_error_msg[0]}")
                        break

                # Calculate step cost using provider's pricing
                step_cost = compute_step_cost(
                    step_in_tokens, step_out_tokens, realtime_provider, is_realtime=True
                )
                if step_cost is not None:
                    cumulative_cost += step_cost

                step_elapsed = time.time() - step_start
                time_str = datetime.now().strftime("%H:%M:%S")

                # Format step summary with cost
                step_tokens = step_in_tokens + step_out_tokens
                if step_cost is not None:
                    cost_str = f" | {step_tokens:,} tokens | ${step_cost:.4f}"
                else:
                    cost_str = f" | {step_tokens:,} tokens"

                if step_failed > 0:
                    print(f"[{time_str}] {step}: {step_valid}/{step_valid + step_failed} valid ({step_elapsed:.1f}s){cost_str}")
                else:
                    print(f"[{time_str}] {step}: {step_valid}/{step_valid} valid ({step_elapsed:.1f}s){cost_str}")

                total_valid += step_valid
                total_failed += step_failed


            progress_this_pass += len(chunks_for_step)

            # Run retries for this step (whether we just ran it or it's from a previous run)
            retry_round = 1
            while retry_round <= max_retries:
                # Skip retries if early abort was triggered (auth/billing errors)
                if abort_requested[0]:
                    time_str = datetime.now().strftime("%H:%M:%S")
                    print(f"[{time_str}] Skipping retries — early abort triggered.", flush=True)
                    log_message(log_file, "REALTIME", "Skipping retries due to early abort (auth/billing error)")
                    break

                # Check cost cap before retry
                if cost_cap is not None and cumulative_cost >= cost_cap:
                    time_str = datetime.now().strftime("%H:%M:%S")
                    print(f"[{time_str}] Cost cap reached (${cost_cap:.4f}). Stopping retries.")
                    log_message(log_file, "REALTIME", f"Cost cap reached during retries: ${cumulative_cost:.4f}")
                    cost_cap_reached = True
                    break

                retried, still_failed, retry_in, retry_out = run_realtime_retries(
                    run_dir, step, config, manifest, log_file, max_retries
                )

                if retried == 0 and still_failed == 0:
                    # No retryable failures
                    break

                # Track retry tokens and cost
                manifest["metadata"]["retry_input_tokens"] += retry_in
                manifest["metadata"]["retry_output_tokens"] += retry_out
                save_manifest(run_dir, manifest)

                total_input_tokens += retry_in
                total_output_tokens += retry_out

                retry_cost = compute_step_cost(
                    retry_in, retry_out, realtime_provider, is_realtime=True
                )
                if retry_cost is not None:
                    cumulative_cost += retry_cost

                time_str = datetime.now().strftime("%H:%M:%S")
                # Show output whenever a retry attempt was made (even if all failed)
                if retried > 0 or still_failed > 0:
                    retry_tokens = retry_in + retry_out
                    if retry_cost is not None:
                        cost_str = f" | {retry_tokens:,} tokens | ${retry_cost:.4f}"
                    else:
                        cost_str = f" | {retry_tokens:,} tokens"
                    print(f"[{time_str}] {step} retry {retry_round}/{max_retries}: {retried}/{retried + still_failed} valid{cost_str}", flush=True)
                    if retried > 0:
                        total_retried += retried
                        total_valid += retried

                if still_failed == 0:
                    # All retries succeeded
                    break

                retry_round += 1

            # Break step loop if cost cap reached during retries
            if cost_cap_reached:
                break

        # Convergence break conditions
        if cost_cap_reached:
            break
        if is_run_terminal(manifest, max_retries):
            break
        if progress_this_pass == 0:
            break

    # Run run-scope steps
    if is_run_terminal(manifest, max_retries):
        run_scope_steps = get_run_scope_steps(config)
        completed_run_steps = manifest.get("completed_run_steps", [])

        for step_config in run_scope_steps:
            step_name = step_config.get("name")
            if not step_name or step_name in completed_run_steps:
                continue

            success = run_scope_run_step(run_dir, step_config, config_path, log_file)
            if success:
                completed_run_steps.append(step_name)
                manifest["completed_run_steps"] = completed_run_steps
                save_manifest(run_dir, manifest)

    # Run post-processing scripts if configured
    run_post_process(run_dir, config)

    # Final summary
    time_str = datetime.now().strftime("%H:%M:%S")

    # Count remaining failures across all chunks
    remaining_failures = 0
    for chunk_name, chunk_data in chunks.items():
        chunk_dir = run_dir / "chunks" / chunk_name
        for step in pipeline:
            failures_file = chunk_dir / f"{step}_failures.jsonl"
            if failures_file.exists():
                with open(failures_file) as f:
                    remaining_failures += sum(1 for line in f if line.strip())

    if total_retried > 0:
        if remaining_failures > 0:
            print(f"[{time_str}] Pipeline complete: {total_valid} valid, {total_retried} retried, {remaining_failures} failed")
        else:
            print(f"[{time_str}] Pipeline complete: {total_valid} valid, {total_retried} retried, 0 failed")
    elif remaining_failures > 0:
        print(f"[{time_str}] Pipeline complete: {total_valid} valid, {remaining_failures} failed")
    else:
        print(f"[{time_str}] Pipeline complete: {total_valid} valid, 0 failed")

    # Show total cost summary
    total_tokens = total_input_tokens + total_output_tokens
    if cumulative_cost > 0:
        print(f"[{time_str}] Total: {total_tokens:,} tokens ({total_input_tokens:,} in + {total_output_tokens:,} out) | ${cumulative_cost:.4f}")
    elif total_tokens > 0:
        print(f"[{time_str}] Total: {total_tokens:,} tokens ({total_input_tokens:,} in + {total_output_tokens:,} out)")

    # Show output locations
    report_path = run_dir / "report.json"
    if report_path.exists():
        print(f"[{time_str}] Report: {report_path}")

    log_message(log_file, "REALTIME", f"Complete: {total_valid} valid, {total_retried} retried, {remaining_failures} failed, cost=${cumulative_cost:.4f}")

    # Check if all chunks reached terminal state
    if not is_run_terminal(manifest, max_retries):
        non_terminal = []
        for chunk_name, chunk_data in chunks.items():
            state = chunk_data.get("state", "")
            if state not in ("VALIDATED", "FAILED"):
                non_terminal.append(f"{chunk_name}={state}")
        if non_terminal:
            time_str = datetime.now().strftime("%H:%M:%S")
            print(f"[{time_str}] Warning: {len(non_terminal)} chunks not terminal: {', '.join(non_terminal[:5])}")
            log_message(log_file, "REALTIME", f"Non-terminal chunks: {', '.join(non_terminal)}")
        return 1

    return 0 if remaining_failures == 0 else 1


def revalidate_failures(run_dir: Path, step_name: str, use_source_config: bool = False) -> dict:
    """
    Re-validate existing failure records for a specific step without API calls.

    Feeds raw_response from failure records through the current validation pipeline
    (schema + business logic) to promote units that now pass updated validators.

    Args:
        run_dir: Path to run directory
        step_name: Pipeline step name to re-validate
        use_source_config: If True, use source pipeline config instead of run snapshot

    Returns:
        Dict with results: {"promoted": N, "still_failing": N, "errors": N}
    """
    log_file = run_dir / "RUN_LOG.txt"
    log_message(log_file, "REVALIDATE", f"Starting re-validation for step '{step_name}'")

    # Safety: check that no orchestrator is running on this run
    pid_file = run_dir / "orchestrator.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)  # Check if process is alive
            return {"error": f"Orchestrator is running (PID {pid}). Stop it before re-validating."}
        except (ProcessLookupError, ValueError):
            pass  # Process is dead — safe to proceed

    # Also check manifest status
    manifest = load_manifest(run_dir)
    if not manifest:
        return {"error": "Cannot load manifest"}

    if manifest.get("status") == "running":
        return {"error": "Run status is 'running'. Stop the orchestrator before re-validating."}

    # Resolve config paths
    if use_source_config:
        # Use source pipeline config
        pipeline_name = manifest.get("metadata", {}).get("pipeline_name")
        if not pipeline_name:
            return {"error": "Cannot determine source pipeline name from manifest"}
        pipelines_dir = Path(__file__).parent.parent / "pipelines"
        config_path = pipelines_dir / pipeline_name / "config.yaml"
        if not config_path.exists():
            return {"error": f"Source pipeline config not found: {config_path}"}
        config_base_dir = pipelines_dir / pipeline_name
    else:
        # Use run's config snapshot
        config_path = run_dir / manifest.get("config", "config/config.yaml")
        if not config_path.exists():
            return {"error": f"Run config not found: {config_path}"}
        config_base_dir = run_dir / "config"

    # Load config
    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)
    except Exception as e:
        return {"error": f"Cannot load config: {e}"}

    # Verify step exists in pipeline
    pipeline = manifest.get("pipeline", [])
    if step_name not in pipeline:
        return {"error": f"Step '{step_name}' not in pipeline: {pipeline}"}

    # Resolve schema path
    schemas_config = config.get("schemas", {})
    schema_dir = schemas_config.get("schema_dir", "schemas")
    schema_files = schemas_config.get("files", {})
    schema_file = schema_files.get(step_name)
    schema_path = None
    if schema_file:
        candidate = config_base_dir / schema_dir / schema_file
        if candidate.exists():
            schema_path = candidate
        else:
            # Fallback: look in run's config dir
            fallback = run_dir / "config" / schema_dir / schema_file
            if fallback.exists():
                schema_path = fallback

    # Validation stages that are retryable
    validation_stages = {"schema_validation", "validation"}

    scripts_dir = Path(__file__).parent
    schema_validator = scripts_dir / "schema_validator.py"
    validator = scripts_dir / "validator.py"

    chunks_dir = run_dir / "chunks"
    if not chunks_dir.exists():
        return {"error": "No chunks directory found"}

    total_promoted = 0
    total_still_failing = 0
    total_errors = 0

    for chunk_dir in sorted(chunks_dir.iterdir()):
        if not chunk_dir.is_dir():
            continue

        failures_file = chunk_dir / f"{step_name}_failures.jsonl"
        validated_file = chunk_dir / f"{step_name}_validated.jsonl"

        if not failures_file.exists():
            continue

        # Load failure records, keeping raw lines for byte-identical preservation
        failures = []  # list of (parsed_dict, raw_line_str)
        try:
            with open(failures_file, 'r', encoding='utf-8') as ff:
                for raw_line in ff:
                    stripped = raw_line.strip()
                    if not stripped:
                        continue
                    try:
                        failures.append((json.loads(stripped), stripped))
                    except json.JSONDecodeError:
                        continue
        except Exception:
            continue
        if not failures:
            continue

        # Filter to retryable failures (schema_validation + validation only)
        retryable = []
        hard_failures = []
        for parsed, raw_line in failures:
            stage = parsed.get("failure_stage", "validation")
            if stage in validation_stages:
                retryable.append(parsed)
            else:
                hard_failures.append(parsed)

        if not retryable:
            log_message(log_file, "REVALIDATE", f"{chunk_dir.name}/{step_name}: No retryable failures (all hard failures)")
            total_still_failing += len(hard_failures)
            continue

        # Process each retryable failure: parse raw_response and build validation input
        # parse_errors stores the original raw JSONL line for byte-identical preservation
        revalidation_lines = []
        parse_errors_raw = []  # raw line strings, not dicts
        for record in retryable:
            raw_response = record.get("raw_response", "")
            unit_id = record.get("unit_id", "")
            if not raw_response:
                # Find the original raw line for this record
                for parsed, raw_line in failures:
                    if parsed is record:
                        parse_errors_raw.append(raw_line)
                        break
                continue

            # Parse raw_response using the same markdown extraction as the normal pipeline
            parsed = parse_json_response(raw_response)
            if parsed is None:
                # Find the original raw line for this record
                for p, raw_line in failures:
                    if p is record:
                        parse_errors_raw.append(raw_line)
                        break
                continue

            # Reconstruct merged data: {**input_context, **parsed_result}
            input_context = record.get("input", {})
            if isinstance(input_context, dict):
                merged = {**input_context, **parsed}
            else:
                merged = parsed

            revalidation_lines.append(json.dumps(merged))

        if not revalidation_lines:
            log_message(log_file, "REVALIDATE", f"{chunk_dir.name}/{step_name}: All {len(retryable)} failures have unparseable raw_response")
            total_errors += len(parse_errors_raw)
            total_still_failing += len(hard_failures) + len(parse_errors_raw)
            continue

        input_data = '\n'.join(revalidation_lines) + '\n'

        # Run Phase 1: Schema validation (if schema exists)
        newly_validated = []
        # Start with hard failures (re-serialized) + parse_errors (raw lines, byte-identical)
        still_failing_records = list(hard_failures)  # dicts — will be json.dumps'd
        still_failing_raw_lines = list(parse_errors_raw)  # raw strings — written verbatim

        try:
            if schema_path and schema_path.exists():
                p1 = subprocess.Popen(
                    [sys.executable, str(schema_validator), "--schema", str(schema_path), "--quiet"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                p1_stdout, p1_stderr = p1.communicate(input=input_data.encode(), timeout=300)

                # Collect schema-passed unit_ids
                passed_unit_ids = set()
                for line in p1_stdout.decode().strip().split('\n'):
                    if line.strip():
                        try:
                            item = json.loads(line)
                            uid = item.get('unit_id')
                            if uid:
                                passed_unit_ids.add(uid)
                        except json.JSONDecodeError:
                            pass

                # Collect schema failures
                for line in p1_stderr.decode().strip().split('\n'):
                    if not line.strip():
                        continue
                    try:
                        failure = json.loads(line)
                        if failure.get("unit_id") and "errors" in failure:
                            # Find original record and update error info
                            orig = next((r for r in retryable if r.get("unit_id") == failure.get("unit_id")), None)
                            if orig:
                                updated = dict(orig)
                                updated["errors"] = failure.get("errors", [])
                                updated["failure_stage"] = "schema_validation"
                                still_failing_records.append(updated)
                    except json.JSONDecodeError:
                        pass

                # Build Phase 2 input from only schema-passed records
                p2_input_lines = []
                for line in revalidation_lines:
                    try:
                        item = json.loads(line)
                        if item.get('unit_id') in passed_unit_ids:
                            p2_input_lines.append(line)
                    except json.JSONDecodeError:
                        pass

                p2_input = ('\n'.join(p2_input_lines) + '\n').encode() if p2_input_lines else b''
            else:
                # No schema — all go to Phase 2
                p2_input = input_data.encode()

            # Run Phase 2: Business logic validation
            if p2_input:
                p2 = subprocess.Popen(
                    [sys.executable, str(validator), "--config", str(config_path), "--step", step_name, "--quiet"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                p2_stdout, p2_stderr = p2.communicate(input=p2_input, timeout=300)

                # Collect validated units
                for line in p2_stdout.decode().strip().split('\n'):
                    if line.strip():
                        try:
                            item = json.loads(line)
                            newly_validated.append(item)
                        except json.JSONDecodeError:
                            pass

                # Collect business logic failures
                for line in p2_stderr.decode().strip().split('\n'):
                    if not line.strip():
                        continue
                    try:
                        failure = json.loads(line)
                        if failure.get("unit_id") and "errors" in failure:
                            orig = next((r for r in retryable if r.get("unit_id") == failure.get("unit_id")), None)
                            if orig:
                                updated = dict(orig)
                                updated["errors"] = failure.get("errors", [])
                                updated["failure_stage"] = "validation"
                                still_failing_records.append(updated)
                    except json.JSONDecodeError:
                        pass

        except subprocess.TimeoutExpired:
            log_message(log_file, "ERROR", f"{chunk_dir.name}/{step_name}: Validation subprocess timed out during re-validation")
            total_errors += len(retryable)
            continue
        except Exception as e:
            log_message(log_file, "ERROR", f"{chunk_dir.name}/{step_name}: Re-validation error: {e}")
            total_errors += len(retryable)
            continue

        # Atomic writes: append promoted units to validated file, rewrite failures file
        if newly_validated:
            with open(validated_file, 'a') as f:
                for item in newly_validated:
                    f.write(json.dumps(item) + '\n')

        # Write still-failing records to temp file, then atomic rename
        tmp_failures = failures_file.with_suffix('.jsonl.tmp')
        with open(tmp_failures, 'w') as f:
            # Write parse_error records byte-for-byte (raw original lines)
            for raw_line in still_failing_raw_lines:
                f.write(raw_line + '\n')
            # Write schema/logic/hard failure records (re-serialized)
            for record in still_failing_records:
                f.write(json.dumps(record) + '\n')
        os.replace(str(tmp_failures), str(failures_file))

        chunk_promoted = len(newly_validated)
        chunk_still_failing = len(still_failing_records) + len(still_failing_raw_lines)
        total_promoted += chunk_promoted
        total_still_failing += chunk_still_failing
        total_errors += len(parse_errors_raw)

        log_message(log_file, "REVALIDATE",
                    f"{chunk_dir.name}/{step_name}: {chunk_promoted} promoted, "
                    f"{chunk_still_failing} still failing, {len(parse_errors_raw)} parse errors")

    # Update manifest counts
    if total_promoted > 0:
        manifest = load_manifest(run_dir)
        chunks = manifest.get("chunks", {})
        for chunk_name, chunk_data in chunks.items():
            chunk_dir = chunks_dir / chunk_name
            validated_file = chunk_dir / f"{step_name}_validated.jsonl"
            failures_file = chunk_dir / f"{step_name}_failures.jsonl"

            # Recount from disk
            valid_count = 0
            if validated_file.exists():
                valid_count = sum(1 for line in open(validated_file) if line.strip())
            failed_count = 0
            if failures_file.exists():
                failed_count = sum(1 for line in open(failures_file) if line.strip())

            chunk_data["valid"] = valid_count
            chunk_data["failed"] = failed_count

        manifest["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        save_manifest(run_dir, manifest)

    summary = f"{step_name}: {total_promoted}/{total_promoted + total_still_failing} failures now pass validation. {total_still_failing} still failing."
    log_message(log_file, "REVALIDATE", summary)
    print(f"[REVALIDATE] {summary}")

    return {
        "promoted": total_promoted,
        "still_failing": total_still_failing,
        "errors": total_errors
    }


## ============================================================
## CLI Tool Handlers (--ps, --info, --verify, --repair)
## ============================================================

def _handle_ps(args):
    """Handle --ps: list all runs with status, progress, cost."""
    # Import TUI utility functions (pure, no Textual dependency)
    sys.path.insert(0, str(Path(__file__).parent))
    from tui.utils.runs import scan_runs, get_enhanced_run_status

    runs_data = scan_runs()

    # Enhance status with process state for non-terminal runs
    for run in runs_data:
        run["status"] = get_enhanced_run_status(Path(run["path"]), run["status"])

    # Sort: active first, then by started time descending
    active_statuses = {"running", "stuck", "zombie", "active", "detached", "paused"}
    active = [r for r in runs_data if r["status"] in active_statuses]
    inactive = [r for r in runs_data if r["status"] not in active_statuses]

    def sort_time(r):
        started = r.get("started")
        if started is None:
            return ""
        if isinstance(started, str):
            return started
        if hasattr(started, 'isoformat'):
            return started.isoformat()
        return ""

    active.sort(key=sort_time, reverse=True)
    inactive.sort(key=sort_time, reverse=True)
    sorted_runs = active + inactive

    if args.json:
        # JSON output
        output = []
        for r in sorted_runs:
            started = r.get("started")
            if hasattr(started, 'isoformat'):
                started = started.isoformat()
            output.append({
                "run_dir": str(r["path"]),
                "name": r["name"],
                "status": r["status"],
                "progress": r.get("progress", 0),
                "total_units": r.get("total_units", 0),
                "valid_units": r.get("valid_units", 0),
                "failed_units": r.get("unit_failure_count", 0),
                "cost": r.get("cost_value", 0),
                "duration": r.get("duration", "--"),
                "mode": r.get("mode_display", r.get("mode", "batch")),
                "pipeline": r.get("pipeline_name", ""),
                "started": started or "",
            })
        print(json.dumps(output, indent=2))
        return

    # Human-readable table
    if not sorted_runs:
        print("No runs found.")
        return

    # Column headers and widths
    headers = ["Run", "Prog", "Units", "Valid", "Fail", "Cost", "Duration", "Mode", "Status"]
    rows = []
    total_cost = 0.0

    for r in sorted_runs:
        name = r["name"]
        progress = f"{r.get('progress', 0)}%"
        total_units = str(r.get("total_units", 0))
        valid_units = str(r.get("valid_units", 0))
        fail_count = r.get("unit_failure_count", 0)
        failed = str(fail_count)
        cost_val = r.get("cost_value", 0)
        total_cost += cost_val or 0
        cost = f"${cost_val:.2f}" if cost_val and cost_val > 0 else "--"
        duration = r.get("duration", "--")
        mode = r.get("mode_display", r.get("mode", "batch"))
        status = r["status"]

        # Annotate status with failure count and current step
        if fail_count > 0 and status == "complete":
            status_text = f"complete \u26a0 ({fail_count})"
        elif status in ("running", "active") and r.get("pipeline"):
            # Show current step for active runs
            status_text = status
        else:
            status_text = status

        rows.append([name, progress, total_units, valid_units, failed, cost, duration, mode, status_text])

    # Calculate column widths
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(val))

    # Print header
    header_line = "  ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    print(header_line)

    # Print rows
    for row in rows:
        line = "  ".join(val.ljust(col_widths[i]) for i, val in enumerate(row))
        print(line)

    # Print summary
    print(f"\n{len(sorted_runs)} runs | Total cost: ${total_cost:.2f}")


def _handle_info(args):
    """Handle --info: print detailed run information."""
    sys.path.insert(0, str(Path(__file__).parent))
    from tui.utils.runs import (
        load_manifest as tui_load_manifest,
        get_run_process_status,
        get_run_status,
        get_run_progress,
        get_run_cost_value,
        get_run_tokens,
        get_run_mode,
        get_run_pipeline_name,
        get_run_unit_failure_count,
        get_run_duration,
    )

    run_dir = args.run_dir
    manifest = load_manifest(run_dir)
    if not manifest:
        print(f"Error: Cannot load MANIFEST.json from {run_dir}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(manifest, indent=2, default=str))
        return

    # Gather data
    status = get_run_status(manifest)
    progress = get_run_progress(manifest)
    cost_value = get_run_cost_value(manifest)
    total_tokens = get_run_tokens(manifest)
    mode = get_run_mode(manifest)
    pipeline_name = get_run_pipeline_name(manifest, run_dir)
    duration = get_run_duration(manifest, run_dir)
    metadata = manifest.get("metadata", {})
    pipeline = manifest.get("pipeline", [])
    chunks = manifest.get("chunks", {})

    # Provider info
    provider = metadata.get("provider", "")
    model = metadata.get("model", "")

    # Process info
    proc_info = get_run_process_status(run_dir)
    pid = proc_info.get("pid")
    alive = proc_info.get("alive", False)

    # Started
    start_time = metadata.get("start_time", manifest.get("created", ""))

    # Token breakdown
    initial_input = metadata.get("initial_input_tokens", 0) or 0
    initial_output = metadata.get("initial_output_tokens", 0) or 0
    retry_input = metadata.get("retry_input_tokens", 0) or 0
    retry_output = metadata.get("retry_output_tokens", 0) or 0
    initial_total = initial_input + initial_output
    retry_total = retry_input + retry_output

    # Unit counts
    total_units = sum(c.get("items", 0) for c in chunks.values())
    valid_units = sum(c.get("valid", 0) for c in chunks.values())
    failed_units = get_run_unit_failure_count(manifest)

    # Print header
    print(f"Run: {run_dir.name}")
    if pipeline_name:
        print(f"Pipeline: {pipeline_name}")
    if provider:
        provider_str = provider
        if model:
            provider_str += f" ({model})"
        print(f"Provider: {provider_str}")
    print(f"Mode: {mode}")
    print(f"Status: {status}")
    if pid:
        pid_status = "alive" if alive else "dead"
        print(f"PID: {pid} ({pid_status})")
    if start_time:
        print(f"Started: {start_time}")
    print(f"Duration: {duration}")

    # Progress section
    print(f"\nProgress: {progress}% ({valid_units}/{total_units} units)")
    print(f"Valid: {valid_units}")
    print(f"Failed: {failed_units}")
    cost_str = f"${cost_value:.4f}" if cost_value else "--"
    print(f"Cost: {cost_str}")
    token_str = f"{total_tokens:,}" if total_tokens else "--"
    if initial_total or retry_total:
        token_str += f" (initial: {initial_total:,} + retry: {retry_total:,})"
    print(f"Tokens: {token_str}")

    # Pipeline steps
    if pipeline:
        print(f"\nPipeline Steps:")
        for i, step_name in enumerate(pipeline):
            # Count valid units for this step by scanning files
            step_valid = 0
            for chunk_name in chunks:
                chunk_dir = run_dir / "chunks" / chunk_name
                validated_file = chunk_dir / f"{step_name}_validated.jsonl"
                if validated_file.exists():
                    try:
                        with open(validated_file) as f:
                            step_valid += sum(1 for line in f if line.strip())
                    except OSError:
                        pass

            # Determine step input count
            if i == 0:
                step_input = total_units
            else:
                # Input is valid from previous step
                prev_step = pipeline[i - 1]
                step_input = 0
                for chunk_name in chunks:
                    chunk_dir = run_dir / "chunks" / chunk_name
                    prev_validated = chunk_dir / f"{prev_step}_validated.jsonl"
                    if prev_validated.exists():
                        try:
                            with open(prev_validated) as f:
                                step_input += sum(1 for line in f if line.strip())
                        except OSError:
                            pass

            # Determine step status
            if step_valid >= step_input > 0:
                symbol = "\u2713"  # checkmark
            elif step_valid > 0:
                symbol = "\u25cf"  # filled circle (in progress)
            else:
                symbol = "\u25cb"  # empty circle

            # Current step indicator
            current = ""
            if step_valid > 0 and step_valid < step_input:
                current = "  \u2190 current"

            print(f"  {step_name:30s} {symbol} {step_valid}/{step_input}{current}")

    # Chunks
    if chunks:
        print(f"\nChunks: {len(chunks)}")
        for chunk_name in sorted(chunks.keys()):
            chunk_data = chunks[chunk_name]
            state = chunk_data.get("state", "UNKNOWN")
            items = chunk_data.get("items", 0)
            valid = chunk_data.get("valid", 0)
            print(f"  {chunk_name}  {state}  {valid}/{items} valid")


def _handle_verify(args):
    """Handle --verify: check run integrity."""
    from run_tools import verify_run

    result = verify_run(args.run_dir)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return

    run_dir = args.run_dir
    print(f"Verifying run: {run_dir.name}")
    print(f"Pipeline: {result.get('pipeline_name', '')} ({len(result.get('pipeline', []))} steps)")
    print(f"Expected units: {result.get('initial_units', 0)}")
    print()

    total_missing = 0
    total_duplicated = 0
    all_missing_details = []

    for step_report in result.get("steps", []):
        step_name = step_report["step"]
        expected = step_report["expected"]
        valid = step_report["valid"]
        failed = step_report["failed"]
        missing = step_report["missing"]
        duplicated = step_report.get("duplicated", 0)

        total_missing += missing
        total_duplicated += duplicated

        print(f"Step: {step_name}")
        print(f"  Expected: {expected} | Valid: {valid} | Failed: {failed} | Missing: {missing}")

        if missing == 0 and duplicated == 0:
            print(f"  \u2713 All units accounted for")
        else:
            if missing > 0:
                print(f"  \u2717 {missing} units missing")
            if duplicated > 0:
                print(f"  \u2717 {duplicated} units duplicated")

        if step_report.get("missing_ids"):
            all_missing_details.extend([
                (uid, step_name) for uid in step_report["missing_ids"]
            ])
        print()

    # Summary
    if total_missing == 0 and total_duplicated == 0:
        integrity = "OK"
    else:
        integrity = f"WARN ({total_missing} missing, {total_duplicated} duplicated)"
    print(f"Summary:")
    print(f"  Total missing: {total_missing}")
    print(f"  Total duplicated: {total_duplicated}")
    print(f"  Run integrity: {integrity}")

    # Missing unit details
    if all_missing_details:
        print(f"\nMissing units:")
        for uid, step in all_missing_details[:50]:  # Cap display at 50
            print(f"  {uid} (missing at: {step})")
        if len(all_missing_details) > 50:
            print(f"  ... and {len(all_missing_details) - 50} more")


def _handle_repair(args):
    """Handle --repair: create retry chunks for missing units."""
    from run_tools import repair_run

    # Require --yes or interactive confirmation
    if not args.yes:
        response = input("This will modify the run manifest. Continue? [y/N] ")
        if response.lower() not in ('y', 'yes'):
            print("Aborted.")
            return 1

    result = repair_run(args.run_dir)

    if result.get("error"):
        print(f"Error: {result['error']}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    missing_count = result.get("missing_count", 0)
    if missing_count == 0:
        print("No missing units found. Run integrity is OK.")
        return 0

    print(f"Repairing run: {args.run_dir.name}")
    print(f"Found {missing_count} missing units")
    print()

    for chunk_info in result.get("chunks_created", []):
        print(f"Creating retry chunk: {chunk_info['chunk_name']} ({chunk_info['unit_count']} units)")
        for unit in chunk_info.get("units", [])[:10]:
            print(f"  {unit['unit_id']} \u2192 reset to {unit['target_state']}")
        if chunk_info["unit_count"] > 10:
            print(f"  ... and {chunk_info['unit_count'] - 10} more")

    print(f"\nManifest updated. Resume with:")
    print(f"  python scripts/orchestrate.py --watch --run-dir {args.run_dir}")
    print(f"  python scripts/orchestrate.py --realtime --run-dir {args.run_dir}")
    return 0


def main():
    # Force UTF-8 encoding on stdout/stderr so Windows console doesn't choke
    # on non-ASCII characters (e.g., pipeline names, log messages).
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    # Load .env file from current directory or parents
    from dotenv import load_dotenv
    load_dotenv()

    # Restore default SIGPIPE handling so piped output (e.g., | head) causes a
    # clean exit instead of a BrokenPipeError that marks the run as failed.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    # SIGUSR1 dumps stack trace to log for debugging hung processes (Unix-only)
    if hasattr(signal, "SIGUSR1"):
        def _sigusr1_handler(signum, frame):
            trace = ''.join(traceback.format_stack(frame))
            if _current_run_dir:
                log_file = _current_run_dir / "RUN_LOG.txt"
                try:
                    log_message(log_file, "DEBUG", f"SIGUSR1 received — stack trace:\n{trace}")
                    return
                except Exception:
                    pass
            # Fallback if log_message unavailable
            sys.stderr.write(f"[DEBUG] SIGUSR1 received — stack trace:\n{trace}\n")

        signal.signal(signal.SIGUSR1, _sigusr1_handler)

    parser = argparse.ArgumentParser(
        description="Orchestrator for batch processing runs"
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"octobatch {__version__}"
    )

    # Common arguments
    parser.add_argument(
        "--run-dir", "-r",
        type=Path,
        help="Path to run directory (required for most operations)"
    )

    # Mode selection (not required - --realtime can be used alone or with --init)
    mode_group = parser.add_mutually_exclusive_group(required=False)
    mode_group.add_argument(
        "--init",
        action="store_true",
        help="Initialize a new run"
    )
    mode_group.add_argument(
        "--tick",
        action="store_true",
        help="Advance the run by one step"
    )
    mode_group.add_argument(
        "--status",
        action="store_true",
        help="Show current run status"
    )
    mode_group.add_argument(
        "--watch",
        action="store_true",
        help="Automatically poll until pipeline completion"
    )
    mode_group.add_argument(
        "--retry-failures",
        action="store_true",
        help="Create retry chunks from failed units"
    )
    mode_group.add_argument(
        "--validate-config",
        action="store_true",
        help="Validate config file and expressions"
    )
    mode_group.add_argument(
        "--revalidate",
        action="store_true",
        help="Re-run validation on existing failures without API calls (requires --step)"
    )
    mode_group.add_argument(
        "--ps",
        action="store_true",
        help="List all runs with status, progress, cost"
    )
    mode_group.add_argument(
        "--info",
        action="store_true",
        help="Print detailed run information"
    )
    mode_group.add_argument(
        "--verify",
        action="store_true",
        help="Check run integrity — find missing, duplicated, or orphaned units"
    )
    mode_group.add_argument(
        "--repair",
        action="store_true",
        help="Create retry chunks for missing units found by --verify"
    )

    # Output format
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output in JSON format (used with --ps, --info, --verify)"
    )

    # Config-related arguments
    parser.add_argument(
        "--config", "-c",
        type=Path,
        help="Path to config.yaml (required for --init and --validate-config, unless --pipeline is used)"
    )
    parser.add_argument(
        "--pipeline", "-p",
        type=str,
        help="Pipeline name from pipelines/ folder (alternative to --config for --init)"
    )
    parser.add_argument(
        "--max-units", "--limit",
        type=int,
        dest="max_units",
        help="Limit to first N units (for testing)"
    )
    parser.add_argument(
        "--repeat",
        type=int,
        help="Override processing.repeat count"
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="Maximum retry attempts per item (default: 5)"
    )

    # Watch mode options
    parser.add_argument(
        "--interval",
        type=int,
        default=5,
        help="Seconds between polls in watch mode (default: 5)"
    )
    parser.add_argument(
        "--max-cost",
        type=float,
        help="Stop if estimated cost exceeds this amount (USD)"
    )
    parser.add_argument(
        "--timeout",
        type=str,
        help="Stop after this duration (e.g., '30m', '2h', '1h30m')"
    )

    # Real-time mode options
    parser.add_argument(
        "--realtime",
        action="store_true",
        help="Run pipeline with synchronous API calls (2x cost, seconds instead of minutes)"
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip confirmation prompts (for scripting)"
    )
    parser.add_argument(
        "--provider",
        type=str,
        choices=["gemini", "openai", "anthropic"],
        help="Override provider from pipeline config"
    )
    parser.add_argument(
        "--model",
        type=str,
        help="Override model from pipeline config"
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress console output (log files still written)"
    )

    # Re-validation options
    parser.add_argument(
        "--step",
        type=str,
        help="Step name (used with --revalidate)"
    )
    parser.add_argument(
        "--use-source-config",
        action="store_true",
        help="Use source pipeline config instead of run snapshot (used with --revalidate)"
    )

    args = parser.parse_args()

    # --quiet: redirect stdout to devnull (log files are unaffected)
    if args.quiet:
        sys.stdout = open(os.devnull, 'w')

    # Validate that at least one mode is selected
    has_mode = any([args.init, args.tick, args.status, args.watch,
                    args.retry_failures, args.validate_config, args.realtime,
                    args.revalidate, args.ps, args.info, args.verify, args.repair])
    if not has_mode:
        parser.error("One of --init, --tick, --status, --watch, --retry-failures, --validate-config, --revalidate, --realtime, --ps, --info, --verify, or --repair is required")

    # Handle --ps (doesn't need --run-dir)
    if args.ps:
        _handle_ps(args)
        sys.exit(0)

    # Handle --validate-config (doesn't need --run-dir)
    if args.validate_config:
        if not args.config:
            parser.error("--config is required with --validate-config")

        result = validate_config_run(args.config)
        sys.exit(0 if result["valid"] else 1)

    # All other operations require --run-dir
    if not args.run_dir and not args.ps:
        parser.error("--run-dir is required for this operation")

    # Handle --info
    if args.info:
        if not args.run_dir.exists():
            parser.error(f"Run directory not found: {args.run_dir}")
        _handle_info(args)
        sys.exit(0)

    # Handle --verify
    if args.verify:
        if not args.run_dir.exists():
            parser.error(f"Run directory not found: {args.run_dir}")
        _handle_verify(args)
        sys.exit(0)

    # Handle --repair
    if args.repair:
        if not args.run_dir.exists():
            parser.error(f"Run directory not found: {args.run_dir}")
        exit_code = _handle_repair(args)
        sys.exit(exit_code)

    # Handle --revalidate
    if args.revalidate:
        if not args.step:
            parser.error("--step is required with --revalidate")
        if not args.run_dir.exists():
            parser.error(f"Run directory not found: {args.run_dir}")

        result = revalidate_failures(
            run_dir=args.run_dir,
            step_name=args.step,
            use_source_config=args.use_source_config
        )
        if result.get("error"):
            print(f"Error: {result['error']}", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    # Handle --init (possibly combined with --realtime)
    if args.init:
        # Determine config path from either --config or --pipeline
        if args.pipeline:
            # Resolve pipeline path from pipelines/ folder
            pipelines_dir = Path(__file__).parent.parent / "pipelines"
            config_path = pipelines_dir / args.pipeline / "config.yaml"
            if not config_path.exists():
                parser.error(f"Pipeline not found: {args.pipeline} (looked for {config_path})")
        elif args.config:
            config_path = args.config
        else:
            parser.error("--config or --pipeline is required with --init")

        # Load config to resolve provider/model
        with open(config_path) as f:
            _pre_config = yaml.safe_load(f)
        _api = _pre_config.get("api", {})

        # CLI overrides are ONLY set when the user explicitly passes --provider/--model.
        # Config-level and registry defaults are handled at runtime by get_provider()/
        # get_step_provider(), so we must NOT pass them as overrides — that would crush
        # per-step provider/model settings in the config.
        cli_provider = getattr(args, 'provider', None)
        cli_model = getattr(args, 'model', None)

        # Validate that a provider+model will be resolvable at runtime
        # (either from CLI, config, or registry default)
        effective_provider = cli_provider or _api.get("provider")
        if not effective_provider:
            try:
                from scripts.providers.base import LLMProvider
                registry = LLMProvider.load_model_registry()
                effective_provider = registry.get("default_provider", "gemini")
            except Exception:
                effective_provider = "gemini"

        effective_model = cli_model or _api.get("model")
        if not effective_model:
            try:
                from scripts.providers.base import LLMProvider
                provider_info = LLMProvider.get_provider_info(effective_provider)
                effective_model = provider_info.get("default_model")
            except Exception:
                pass

        if not effective_model:
            parser.error(
                f"--model is required (pipeline config has no api.model default and "
                f"no default_model found in registry for '{effective_provider}')"
            )

        success = init_run(
            config_path=config_path,
            run_dir=args.run_dir,
            max_units=args.max_units,
            provider_override=cli_provider,
            model_override=cli_model,
            used_default_provider=False,
            repeat_override=args.repeat,
        )
        if not success:
            sys.exit(1)

        # If --realtime is also set, continue to realtime execution
        if not args.realtime:
            sys.exit(0)

    elif args.tick:
        status = tick_run(args.run_dir, max_retries=args.max_retries)
        print(json.dumps(status, indent=2))

        # Exit with error if there were errors
        if status.get("error") or status.get("errors", 0) > 0:
            sys.exit(1)
        sys.exit(0)

    elif args.status:
        status = status_run(args.run_dir)
        print(json.dumps(status, indent=2))

        if status.get("error"):
            sys.exit(1)
        sys.exit(0)

    elif args.watch:
        # Validate run directory exists
        if not args.run_dir.exists():
            print(f"Error: Run directory not found: {args.run_dir}", file=sys.stderr)
            sys.exit(1)

        # Parse timeout if provided
        timeout_seconds = None
        if args.timeout:
            try:
                timeout_seconds = parse_duration(args.timeout)
            except ValueError as e:
                parser.error(str(e))

        # Mark run as running (clears paused state if resuming)
        mark_run_running(args.run_dir)

        # Run watch loop with crash handling
        try:
            exit_code = watch_run(
                run_dir=args.run_dir,
                interval=args.interval,
                max_cost=args.max_cost,
                timeout_seconds=timeout_seconds,
                max_retries=args.max_retries
            )
            # Check terminal state and mark accordingly
            if exit_code == 0:
                mark_run_complete(args.run_dir)
            elif exit_code == 130:
                # User interrupted - mark as paused
                mark_run_paused(args.run_dir)
            # exit_code 2 (cost limit) and 3 (timeout) leave run in current state
            sys.exit(exit_code)
        except KeyboardInterrupt:
            mark_run_paused(args.run_dir)
            sys.exit(130)
        except Exception as e:
            mark_run_failed(args.run_dir, str(e))
            raise

    elif args.retry_failures:
        status = retry_failures_run(args.run_dir, max_retries=args.max_retries)
        print(json.dumps(status, indent=2))

        if status.get("error"):
            sys.exit(1)
        sys.exit(0)

    # Handle --realtime (either standalone or after --init above)
    if args.realtime:
        # Validate run directory exists
        if not args.run_dir.exists():
            print(f"Error: Run directory not found: {args.run_dir}", file=sys.stderr)
            sys.exit(1)

        # Mark run as running (clears paused state if resuming)
        mark_run_running(args.run_dir)

        # Run realtime with crash handling
        try:
            exit_code = realtime_run(
                run_dir=args.run_dir,
                max_retries=args.max_retries,
                skip_confirmation=args.yes
            )
            # Check result and mark accordingly
            if exit_code in (0, 1):
                # exit_code 0 = all passed, exit_code 1 = remaining failures
                # Either way, if all chunks are terminal, mark complete
                manifest = load_manifest(args.run_dir)
                if is_run_terminal(manifest, args.max_retries):
                    mark_run_complete(args.run_dir)
                elif exit_code == 0:
                    # realtime_run returned 0 but chunks aren't terminal
                    # Don't mark complete — run needs investigation
                    print("Warning: Run returned success but not all chunks are terminal.", file=sys.stderr)
            sys.exit(exit_code)
        except KeyboardInterrupt:
            mark_run_paused(args.run_dir)
            sys.exit(130)
        except Exception as e:
            mark_run_failed(args.run_dir, str(e))
            raise


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        # Piped output closed (e.g., | head). Not an error.
        # Silence stderr as well to avoid "Exception ignored" messages.
        try:
            sys.stdout.close()
        except Exception:
            pass
        try:
            sys.stderr.close()
        except Exception:
            pass
        sys.exit(0)
    except Exception:
        # Global crash handler: write full traceback to the run's RUN_LOG.txt
        # so crashes from spawned subprocesses are never silent.
        import traceback
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)

        # Best-effort: find run_dir from sys.argv and log the traceback
        run_dir = None
        for i, arg in enumerate(sys.argv):
            if arg in ("--run-dir", "-r") and i + 1 < len(sys.argv):
                run_dir = Path(sys.argv[i + 1])
                break
        if run_dir and run_dir.exists():
            try:
                log_file = run_dir / "RUN_LOG.txt"
                log_message(log_file, "CRASH", f"Unhandled exception in main()")
                log_message(log_file, "TRACEBACK", tb)
            except Exception:
                pass
        sys.exit(1)
