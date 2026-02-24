"""
octobatch_utils.py - Shared utilities for octobatch scripts.

Provides common functions for manifest handling, JSONL operations,
and error logging used across multiple scripts.
"""

import gzip
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from version import __version__  # noqa: F401 — re-exported for backwards compat


def load_config(config_path: Path) -> dict:
    """Load and parse a YAML config file.

    Args:
        config_path: Path to the YAML config file

    Returns:
        Parsed config dictionary

    Raises:
        FileNotFoundError: If config file doesn't exist
        yaml.YAMLError: If config file is invalid YAML
    """
    with open(config_path) as f:
        return yaml.safe_load(f)


def load_manifest(run_dir: Path) -> dict:
    """Load MANIFEST.json from a run directory."""
    manifest_path = run_dir / "MANIFEST.json"
    with open(manifest_path) as f:
        return json.load(f)


def save_manifest(run_dir: Path, manifest: dict) -> None:
    """
    Save MANIFEST.json atomically using temp file + rename.

    Updates the 'updated' timestamp automatically.
    Also writes .manifest_summary.json — a lightweight summary for fast TUI startup.
    """
    manifest_path = run_dir / "MANIFEST.json"

    # Update timestamp
    manifest["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with tempfile.NamedTemporaryFile(
        mode='w',
        dir=run_dir,
        suffix='.tmp',
        delete=False
    ) as f:
        json.dump(manifest, f, indent=2)
        temp_path = f.name

    os.replace(temp_path, manifest_path)

    # Write lightweight summary for fast TUI startup
    try:
        summary = _build_summary(manifest)
        summary_path = run_dir / ".manifest_summary.json"
        summary_tmp = summary_path.with_suffix('.tmp')
        with open(summary_tmp, 'w') as f:
            json.dump(summary, f)
        os.replace(str(summary_tmp), str(summary_path))
    except Exception:
        pass  # Best-effort — never fail the caller


def _build_summary(manifest: dict) -> dict:
    """
    Build a lightweight summary dict from a full manifest.

    Contains only the fields the HomeScreen DataTable needs (~300 bytes vs 1-5MB).
    Pure arithmetic on manifest fields — no TUI or provider dependencies.
    """
    chunks = manifest.get("chunks", {})
    metadata = manifest.get("metadata", {})
    pipeline = manifest.get("pipeline", [])

    # Status: use explicit field or infer from chunks
    status = manifest.get("status", "pending")
    if status not in ("complete", "failed", "paused", "killed"):
        if chunks:
            total = len(chunks)
            validated = sum(1 for c in chunks.values() if c.get("state") == "VALIDATED")
            failed = sum(1 for c in chunks.values() if c.get("state") == "FAILED")
            if validated == total:
                status = "complete"
            elif failed > 0:
                status = "failed"
            elif status == "running":
                status = "active"

    # Progress: step-level granularity
    progress = 0
    if status == "complete":
        progress = 100
    elif chunks and pipeline:
        total_steps = len(pipeline)
        total_chunks = len(chunks)
        completed_steps = 0
        for chunk_data in chunks.values():
            state = chunk_data.get("state", "")
            if state == "VALIDATED":
                completed_steps += total_steps
            elif "_" in state and state not in ("FAILED", "PENDING"):
                step_name = state.rsplit("_", 1)[0]
                if step_name in pipeline:
                    completed_steps += pipeline.index(step_name)
        total_work = total_chunks * total_steps
        if total_work > 0:
            progress = int((completed_steps / total_work) * 100)

    # Unit counts
    total_units = sum(c.get("items", 0) for c in chunks.values())
    valid_units = sum(c.get("valid", 0) for c in chunks.values())

    # Failed units: for terminal runs, total - valid is more reliable
    if status in ("complete", "failed", "killed"):
        failed_units = max(0, total_units - valid_units)
    else:
        failed_units = sum(c.get("failed", 0) for c in chunks.values())

    # Tokens
    initial_input = metadata.get("initial_input_tokens", 0) or 0
    initial_output = metadata.get("initial_output_tokens", 0) or 0
    retry_input = metadata.get("retry_input_tokens", 0) or 0
    retry_output = metadata.get("retry_output_tokens", 0) or 0
    total_tokens = initial_input + initial_output + retry_input + retry_output

    # Cost: compute from tokens + model registry pricing (best-effort)
    total_input = initial_input + retry_input
    total_output = initial_output + retry_output
    cost = _compute_summary_cost(total_input, total_output, metadata)

    # Current step: find the most advanced non-terminal chunk state
    current_step = ""
    if pipeline:
        max_step_index = -1
        for chunk_data in chunks.values():
            state = chunk_data.get("state", "")
            if state == "VALIDATED":
                max_step_index = max(max_step_index, len(pipeline) - 1)
            elif "_" in state:
                step_name = state.rsplit("_", 1)[0]
                if step_name in pipeline:
                    idx = pipeline.index(step_name)
                    max_step_index = max(max_step_index, idx)
        if 0 <= max_step_index < len(pipeline):
            current_step = pipeline[max_step_index]

    return {
        "status": status,
        "progress": progress,
        "total_units": total_units,
        "valid_units": valid_units,
        "failed_units": failed_units,
        "cost": cost,
        "total_tokens": total_tokens,
        "mode": metadata.get("mode", "batch") or "batch",
        "pipeline_name": metadata.get("pipeline_name", ""),
        "started": metadata.get("start_time", manifest.get("created", "")),
        "updated": manifest.get("updated", ""),
        "current_step": current_step,
        "error_message": manifest.get("error_message"),
        "pipeline": pipeline,
        "provider": metadata.get("provider", ""),
        "model": metadata.get("model", ""),
    }


def _compute_summary_cost(total_input: int, total_output: int, metadata: dict) -> float:
    """Compute cost from token counts using model registry pricing. Best-effort."""
    if total_input == 0 and total_output == 0:
        return 0.0

    # Default rates (Gemini batch)
    input_rate = 0.075
    output_rate = 0.30

    try:
        registry_path = Path(__file__).parent / "providers" / "models.yaml"
        if registry_path.exists():
            registry = yaml.safe_load(registry_path.read_text())
            provider_name = metadata.get("provider") or "gemini"
            model_name = metadata.get("model")
            mode = metadata.get("mode", "batch")

            providers = registry.get("providers", {})
            provider_data = providers.get(provider_name, {})
            realtime_multiplier = provider_data.get("realtime_multiplier", 2.0)

            # Try to find the model
            model_data = None
            if model_name:
                model_data = provider_data.get("models", {}).get(model_name)
            if not model_data:
                default_model = provider_data.get("default_model")
                if default_model:
                    model_data = provider_data.get("models", {}).get(default_model)

            if model_data:
                input_rate = model_data.get("input_per_million", 0.075)
                output_rate = model_data.get("output_per_million", 0.3)
                if mode == "realtime":
                    input_rate *= realtime_multiplier
                    output_rate *= realtime_multiplier
    except Exception:
        pass  # Use defaults

    input_cost = (total_input / 1_000_000) * input_rate
    output_cost = (total_output / 1_000_000) * output_rate
    return round(input_cost + output_cost, 4)


def load_jsonl(file_path: Path) -> list[dict]:
    """Load all records from a JSONL file, supporting both plain and gzipped formats."""
    file_path = Path(file_path)
    records = []

    # Check for gzipped version if plain doesn't exist
    if not file_path.exists():
        gz_path = Path(str(file_path) + '.gz')
        if gz_path.exists():
            file_path = gz_path
        else:
            return records

    # Read gzipped or plain file
    if file_path.suffix == '.gz':
        open_func = lambda p: gzip.open(p, 'rt', encoding='utf-8')
    else:
        open_func = lambda p: open(p, 'r', encoding='utf-8')

    with open_func(file_path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def load_jsonl_by_id(file_path: Path, id_field: str = "unit_id") -> dict[str, dict]:
    """Load JSONL file indexed by a specified field."""
    records = {}
    for record in load_jsonl(file_path):
        key = record.get(id_field)
        if key:
            records[key] = record
    return records


def append_jsonl(file_path: Path, record: dict) -> None:
    """Append a single record to a JSONL file.

    Args:
        file_path: Path to the JSONL file
        record: Dictionary to append as a JSON line
    """
    with open(file_path, "a") as f:
        f.write(json.dumps(record) + "\n")


def write_jsonl(file_path: Path, records: list[dict]) -> None:
    """Write a list of records to a JSONL file (overwrites existing).

    Creates parent directories if they don't exist.

    Args:
        file_path: Path to the JSONL file
        records: List of dictionaries to write as JSON lines
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")


def log_error(message: str, context: dict = None) -> None:
    """Log error to stderr in structured JSON format."""
    error_obj = {"error": message}
    if context:
        error_obj["context"] = context
    print(json.dumps(error_obj), file=sys.stderr)


def log_message(log_file: Path, level: str, message: str, echo_stderr: bool = True) -> None:
    """
    Append a timestamped log message to a log file and optionally echo to stderr.

    Args:
        log_file: Path to the log file
        level: Event type like POLL, COLLECT, SUBMIT, VALIDATE, TICK, ERROR
        message: Human-readable message
        echo_stderr: If True, also print to stderr for CLI visibility
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    time_short = datetime.now().strftime("%H:%M:%S")  # Local time for stderr

    # Write to log file
    with open(log_file, "a") as f:
        f.write(f"[{timestamp}] [{level}] {message}\n")
        f.flush()  # Ensure real-time visibility

    # Echo to stderr for CLI users
    if echo_stderr:
        print(f"[{time_short}] [{level}] {message}", file=sys.stderr, flush=True)


def trace_log(run_dir: Path, message: str) -> None:
    """
    Append a timestamped trace line to TRACE_LOG.txt for request-level telemetry.

    This file records every outgoing API call and batch operation. It is always
    written (not gated behind flags) and is separate from RUN_LOG.txt to keep
    operational logs readable while preserving full request-level detail.

    Args:
        run_dir: Path to the run directory (TRACE_LOG.txt is created here)
        message: Pre-formatted trace line (e.g., "[API] gemini chunk_003 unit_042 | 1.33s | 200")
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{datetime.now(timezone.utc).microsecond // 1000:03d}"
    trace_file = run_dir / "TRACE_LOG.txt"
    try:
        with open(trace_file, "a") as f:
            f.write(f"{timestamp} {message}\n")
            f.flush()
    except Exception:
        pass  # Best-effort — never fail the caller


def format_elapsed_time(seconds: int) -> str:
    """Format seconds as human-readable duration (e.g., '2h 15m 30s')."""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes}m {secs}s"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        return f"{hours}h {minutes}m {secs}s"


def compute_cost(
    input_tokens: int,
    output_tokens: int,
    pricing: dict | None
) -> float | None:
    """
    Compute cost from token counts and pricing config.

    Args:
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        pricing: Dict with 'input_per_million_tokens' and 'output_per_million_tokens'
                 or None if pricing not configured

    Returns:
        Estimated cost in USD, or None if pricing not configured
    """
    if not pricing:
        return None

    input_rate = pricing.get("input_per_million_tokens", 0)
    output_rate = pricing.get("output_per_million_tokens", 0)

    input_cost = (input_tokens / 1_000_000) * input_rate
    output_cost = (output_tokens / 1_000_000) * output_rate

    return round(input_cost + output_cost, 6)


def create_interpreter():
    """
    Create a safe asteval interpreter for validation expressions.

    The interpreter includes common safe builtins useful for validation.
    Used by both validator.py and config_validator.py.

    Returns:
        Configured asteval Interpreter instance
    """
    from asteval import Interpreter
    from io import StringIO

    # Use StringIO for error output to prevent asteval errors from polluting stderr
    # The errors are still captured in aeval.error list for programmatic access
    err_buffer = StringIO()
    aeval = Interpreter(err_writer=err_buffer)

    # Add safe builtins that are useful for validation
    aeval.symtable['sum'] = sum
    aeval.symtable['len'] = len
    aeval.symtable['min'] = min
    aeval.symtable['max'] = max
    aeval.symtable['abs'] = abs
    aeval.symtable['round'] = round
    aeval.symtable['all'] = all
    aeval.symtable['any'] = any
    aeval.symtable['sorted'] = sorted
    aeval.symtable['list'] = list
    aeval.symtable['dict'] = dict
    aeval.symtable['set'] = set
    aeval.symtable['str'] = str
    aeval.symtable['int'] = int
    aeval.symtable['float'] = float
    aeval.symtable['bool'] = bool
    aeval.symtable['isinstance'] = isinstance
    aeval.symtable['enumerate'] = enumerate
    aeval.symtable['zip'] = zip
    aeval.symtable['range'] = range

    return aeval


def parse_json_response(response_text: str) -> dict | None:
    """
    Parse JSON from response text, handling markdown code blocks.

    LLMs sometimes wrap JSON in markdown code blocks or produce slightly
    invalid JSON (like +4 instead of 4). This function handles those cases.

    Args:
        response_text: Raw response text from API

    Returns:
        Parsed JSON dict or None if parsing fails
    """
    if not response_text:
        return None

    text = response_text.strip()

    # Try to extract JSON from markdown code blocks
    if "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        if end > start:
            text = text[start:end].strip()
    elif "```" in text:
        start = text.find("```") + 3
        end = text.find("```", start)
        if end > start:
            text = text[start:end].strip()

    # Remove + prefix from numbers (not valid JSON but LLMs sometimes produce this)
    # Handle object values like: "key": +4 (the " before : indicates end of key, not inside string)
    text = re.sub(r'"\s*:\s*\+(\d)', r'": \1', text)
    # Handle array start like: [+4, ...]
    text = re.sub(r'\[\s*\+(\d)', r'[\1', text)
    # Handle array continuation like: ..., +4, ...]
    text = re.sub(r',\s*\+(\d)', r', \1', text)

    # Remove trailing commas before } or ] (invalid JSON but common LLM output)
    text = re.sub(r',\s*([}\]])', r'\1', text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
