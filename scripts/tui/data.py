"""
Data loading and parsing for Octobatch TUI.

Handles loading MANIFEST.json, report.json, failures, and log files.
"""

import gzip
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


def _find_jsonl_file(base_path: Path) -> Path | None:
    """Find a JSONL file, checking for gzipped version if plain doesn't exist."""
    if base_path.exists():
        return base_path
    gz_path = Path(str(base_path) + '.gz')
    if gz_path.exists():
        return gz_path
    return None


def _open_jsonl(path: Path):
    """Open a JSONL file, handling both plain and gzipped formats."""
    if path.suffix == '.gz':
        return gzip.open(path, 'rt', encoding='utf-8')
    return open(path, 'r', encoding='utf-8')


@dataclass
class StepStatus:
    """Status of a pipeline step."""
    name: str
    completed: int = 0
    total: int = 0
    cost: float = 0.0
    state: str = "pending"  # pending, running, complete

    @property
    def symbol(self) -> str:
        """Get status symbol."""
        if self.state == "complete":
            return "[green]\u2713[/]"  # checkmark
        elif self.state == "running":
            return "[yellow]\u25cf[/]"  # filled circle
        else:
            return "[dim]\u25cb[/]"  # empty circle

    @property
    def progress_str(self) -> str:
        """Get progress string."""
        if self.state == "pending":
            return "--/--"
        return f"{self.completed}/{self.total}"

    @property
    def cost_str(self) -> str:
        """Get cost string."""
        if self.cost > 0:
            return f"${self.cost:.4f}"
        return "--"


@dataclass
class ChunkStatus:
    """Status of a chunk."""
    name: str
    step: str
    state: str  # PENDING, COMPLETE, etc.
    valid: int = 0
    failed: int = 0
    total: int = 0

    @property
    def symbol(self) -> str:
        """Get status symbol."""
        if "COMPLETE" in self.state or self.state == "VALIDATED":
            return "[green]\u2713[/]"
        elif "PENDING" in self.state and self.step:
            return "[yellow]\u25cf[/]"
        else:
            return "[dim]\u25cb[/]"

    @property
    def progress_str(self) -> str:
        """Get progress string."""
        if self.total == 0:
            return "--"
        return f"{self.valid}/{self.total}"


@dataclass
class Failure:
    """A validation failure record."""
    unit_id: str
    chunk_name: str
    step: str
    errors: list[str] = field(default_factory=list)
    retry_count: int = 0
    raw_record: dict = field(default_factory=dict)

    @property
    def error_summary(self) -> str:
        """Get first error as summary."""
        if self.errors:
            first = self.errors[0]
            if len(first) > 40:
                return first[:37] + "..."
            return first
        return "Unknown error"

    def to_dict(self) -> dict:
        """Convert to dict for modal display."""
        return {
            "unit_id": self.unit_id,
            "chunk_name": self.chunk_name,
            "step": self.step,
            "errors": self.errors,
            "retry_count": self.retry_count,
            **self.raw_record
        }


@dataclass
class RealtimeProgress:
    """Realtime progress data from manifest for running jobs."""
    units_completed: int = 0
    units_total: int = 0
    tokens_so_far: int = 0
    cost_so_far: float = 0.0
    estimated_remaining_seconds: int = 0

    @property
    def progress_percent(self) -> float:
        """Get progress as a percentage."""
        if self.units_total == 0:
            return 0.0
        return (self.units_completed / self.units_total) * 100

    @property
    def tokens_formatted(self) -> str:
        """Format tokens with K/M suffix."""
        return format_tokens(self.tokens_so_far)

    @property
    def cost_formatted(self) -> str:
        """Format cost as currency."""
        return f"${self.cost_so_far:.4f}"

    @property
    def time_remaining_formatted(self) -> str:
        """Format time remaining as Xm Ys or Xh Ym."""
        return format_time_remaining(self.estimated_remaining_seconds)


def format_tokens(tokens: int) -> str:
    """Format token count with K/M suffix for compact display."""
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}M"
    elif tokens >= 1_000:
        return f"{tokens / 1_000:.1f}K"
    else:
        return str(tokens)


def format_time_remaining(seconds: int) -> str:
    """Format time remaining as ~Xm Ys or ~Xh Ym."""
    if seconds <= 0:
        return "--"
    elif seconds < 60:
        return f"~{seconds}s"
    elif seconds < 3600:
        minutes = seconds // 60
        secs = seconds % 60
        if secs == 0:
            return f"~{minutes}m"
        return f"~{minutes}m {secs}s"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if minutes == 0:
            return f"~{hours}h"
        return f"~{hours}h {minutes}m"


@dataclass
class RunData:
    """All data for a run."""
    run_dir: Path
    run_name: str = ""
    pipeline_name: str = ""
    mode: str = "batch"  # batch or realtime
    elapsed_time: str = ""
    total_cost: float = 0.0
    max_units: int | None = None  # Max units applied during run init
    poll_interval: int = 30  # Seconds between batch ticks

    # Pipeline
    pipeline: list[str] = field(default_factory=list)
    steps: list[StepStatus] = field(default_factory=list)

    # Chunks
    chunks: list[ChunkStatus] = field(default_factory=list)

    # Summary stats
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    pass_rate: float = 0.0
    total_valid: int = 0
    total_failed: int = 0

    # Realtime progress (for running jobs)
    realtime_progress: RealtimeProgress | None = None

    # Failures (loaded on demand)
    _failures: list[Failure] | None = None

    @property
    def failures(self) -> list[Failure]:
        """Get failures, loading if needed."""
        if self._failures is None:
            self._failures = list(load_failures(self.run_dir, self.pipeline))
        return self._failures

    @property
    def failure_count(self) -> int:
        """Get failure count without loading all failures."""
        return self.total_failed


def parse_state(state_str: str) -> tuple[str, str]:
    """
    Parse a chunk state string into (step, status).

    Examples:
        "generate_COMPLETE" -> ("generate", "COMPLETE")
        "PENDING" -> ("", "PENDING")
    """
    if "_" in state_str:
        parts = state_str.rsplit("_", 1)
        return parts[0], parts[1]
    return "", state_str


def load_manifest(run_dir: Path) -> dict:
    """Load MANIFEST.json from run directory."""
    manifest_path = run_dir / "MANIFEST.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"MANIFEST.json not found in {run_dir}")

    with open(manifest_path) as f:
        return json.load(f)


def load_report(run_dir: Path) -> dict | None:
    """Load report.json if it exists."""
    report_path = run_dir / "report.json"
    if not report_path.exists():
        return None

    with open(report_path) as f:
        return json.load(f)


def load_log(run_dir: Path) -> str:
    """Load RUN_LOG.txt contents."""
    log_path = run_dir / "RUN_LOG.txt"
    if not log_path.exists():
        return ""

    with open(log_path) as f:
        return f.read()


def load_failures(run_dir: Path, pipeline: list[str]) -> Iterator[Failure]:
    """
    Load failures from all chunks.

    Yields Failure objects for each failure record found.
    """
    chunks_dir = run_dir / "chunks"
    if not chunks_dir.exists():
        return

    for chunk_dir in sorted(chunks_dir.iterdir()):
        if not chunk_dir.is_dir():
            continue

        chunk_name = chunk_dir.name

        for step in pipeline:
            failures_path = _find_jsonl_file(chunk_dir / f"{step}_failures.jsonl")
            if not failures_path:
                continue

            with _open_jsonl(failures_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        unit_id = record.get("unit_id", "unknown")
                        errors = record.get("errors", [])
                        error_msgs = []
                        for err in errors:
                            if isinstance(err, dict):
                                error_msgs.append(err.get("message", str(err)))
                            else:
                                error_msgs.append(str(err))

                        yield Failure(
                            unit_id=unit_id,
                            chunk_name=chunk_name,
                            step=step,
                            errors=error_msgs,
                            retry_count=record.get("retry_count", 0),
                            raw_record=record
                        )
                    except json.JSONDecodeError:
                        continue


def load_validated_units(run_dir: Path, chunk_name: str, step: str) -> Iterator[dict]:
    """
    Load validated units from a chunk/step.

    Yields unit dicts for lazy loading.
    """
    validated_path = _find_jsonl_file(run_dir / "chunks" / chunk_name / f"{step}_validated.jsonl")
    if not validated_path:
        return

    with _open_jsonl(validated_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


@dataclass
class UnitRecord:
    """A unit record for display in the TUI."""
    unit_id: str
    chunk_name: str
    step: str
    status: str  # "valid" or "failed"
    data: dict = field(default_factory=dict)

    @property
    def status_symbol(self) -> str:
        """Get status symbol."""
        if self.status == "valid":
            return "[green]\u2713[/]"
        else:
            return "[red]\u2717[/]"


def load_chunk_units(run_dir: Path, chunk_name: str, pipeline: list[str]) -> list[UnitRecord]:
    """
    Load all units from a chunk (both validated and failed).

    Args:
        run_dir: Path to the run directory
        chunk_name: Name of the chunk
        pipeline: Pipeline steps to check

    Returns:
        List of UnitRecord objects
    """
    units = []
    chunk_dir = run_dir / "chunks" / chunk_name

    if not chunk_dir.exists():
        return units

    # Use the last pipeline step for validated units
    last_step = pipeline[-1] if pipeline else None

    if last_step:
        # Load validated units from last step
        validated_path = _find_jsonl_file(chunk_dir / f"{last_step}_validated.jsonl")
        if validated_path:
            with _open_jsonl(validated_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        unit_id = record.get("unit_id", "unknown")
                        units.append(UnitRecord(
                            unit_id=unit_id,
                            chunk_name=chunk_name,
                            step=last_step,
                            status="valid",
                            data=record
                        ))
                    except json.JSONDecodeError:
                        continue

    # Load failures from all steps
    for step in pipeline:
        failures_path = _find_jsonl_file(chunk_dir / f"{step}_failures.jsonl")
        if failures_path:
            with _open_jsonl(failures_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        unit_id = record.get("unit_id", "unknown")
                        units.append(UnitRecord(
                            unit_id=unit_id,
                            chunk_name=chunk_name,
                            step=step,
                            status="failed",
                            data=record
                        ))
                    except json.JSONDecodeError:
                        continue

    return units


def format_elapsed_time(seconds: float) -> str:
    """Format elapsed time as H:MM:SS or M:SS."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def load_run_data(run_dir: Path) -> RunData:
    """
    Load all run data from a run directory.

    Args:
        run_dir: Path to the run directory

    Returns:
        RunData object with all loaded data
    """
    run_dir = Path(run_dir)

    # Load manifest
    manifest = load_manifest(run_dir)

    # Load report (optional)
    report = load_report(run_dir)

    # Basic info
    run_name = run_dir.name
    pipeline = manifest.get("pipeline", [])

    # Determine mode: check report first, then manifest metadata, then fallback heuristic
    metadata = manifest.get("metadata", {})
    if report and report.get("mode"):
        mode = report.get("mode")
    elif metadata.get("mode"):
        mode = metadata.get("mode")
    else:
        # Fallback heuristic for old data without mode field
        mode = "realtime" if metadata.get("initial_input_tokens", 0) > 0 else "batch"

    # Get elapsed time from report if available, otherwise calculate
    elapsed_seconds = 0
    if report:
        timing = report.get("timing", {})
        elapsed_seconds = timing.get("total_duration_seconds", 0)

    if elapsed_seconds == 0:
        # Fallback: calculate from manifest timestamps
        created = manifest.get("created", "")
        updated = manifest.get("updated", "")
        if created and updated:
            from datetime import datetime
            try:
                start_time = datetime.fromisoformat(created.replace("Z", "+00:00"))
                end_time = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                elapsed_seconds = (end_time - start_time).total_seconds()
            except (ValueError, TypeError):
                pass

    # Get cost from report
    total_cost = 0.0
    if report:
        cost_data = report.get("cost", {})
        total_cost = cost_data.get("estimated_cost_usd", 0.0)

    # Token counts from report or manifest
    input_tokens = 0
    output_tokens = 0
    if report:
        cost_data = report.get("cost", {})
        input_tokens = cost_data.get("total_input_tokens", 0)
        output_tokens = cost_data.get("total_output_tokens", 0)
    else:
        input_tokens = metadata.get("initial_input_tokens", 0) + metadata.get("retry_input_tokens", 0)
        output_tokens = metadata.get("initial_output_tokens", 0) + metadata.get("retry_output_tokens", 0)
    total_tokens = input_tokens + output_tokens

    # Get summary from report
    total_valid = 0
    total_failed = 0
    pass_rate = 0.0
    if report:
        summary = report.get("summary", {})
        total_valid = summary.get("validated", 0)
        total_failed = summary.get("failed", 0)
        pass_rate = summary.get("pass_rate", 0.0)

    # Build step statuses from report.by_step or manifest chunks
    steps = []
    by_step = report.get("by_step", {}) if report else {}
    total_units = report.get("summary", {}).get("total_units", 0) if report else 0

    # If no report data, calculate from manifest chunks
    chunks_data = manifest.get("chunks", {})
    manifest_status = manifest.get("status", "unknown")
    run_is_terminal = manifest_status in ("complete", "failed", "killed")
    if not by_step and chunks_data:
        # Calculate total units from chunks
        total_units = sum(c.get("items", 0) for c in chunks_data.values())

        # Build step progress from chunk states
        for step_name in pipeline:
            step_idx = pipeline.index(step_name)
            passed = 0
            failed = 0
            step_started = False  # Track if any chunk has reached this step

            for chunk_data in chunks_data.values():
                chunk_state = chunk_data.get("state", "")

                # Check if chunk has completed this step
                if chunk_state == "VALIDATED":
                    # Fully complete - all steps done
                    passed += chunk_data.get("valid", 0)
                    failed += chunk_data.get("failed", 0)
                    step_started = True  # Completed means it started
                elif "_" in chunk_state:
                    # Parse state like "step_name_SUBMITTED" or "step_name_PENDING"
                    state_step, status = parse_state(chunk_state)
                    chunk_step_idx = pipeline.index(state_step) if state_step in pipeline else -1

                    # If chunk is at or past this step, the step has started
                    if chunk_step_idx >= step_idx:
                        step_started = True

                    # If chunk is past this step, count its valid/failed
                    if chunk_step_idx > step_idx:
                        passed += chunk_data.get("valid", 0)
                        failed += chunk_data.get("failed", 0)
                    elif chunk_step_idx == step_idx and status in ("SUBMITTED", "PENDING", "FAILED"):
                        # Currently on this step - in progress or failed at it
                        if status == "FAILED":
                            failed += chunk_data.get("failed", 0)

            total_processed = passed + failed
            if total_processed == 0 and not step_started:
                # Step hasn't started yet
                state = "pending"
            elif run_is_terminal and step_started:
                # Run is done — step can't be "running" if the run is terminal.
                # This handles the case where chunk "failed" count is 0 but units
                # were lost to exhausted validation failures, making
                # total_processed < total_units.
                state = "complete"
            elif total_processed < total_units:
                # Step has started but not finished (shows 0/Total instead of --/--)
                state = "running"
            else:
                state = "complete"

            steps.append(StepStatus(
                name=step_name,
                completed=passed,
                total=total_units,
                cost=0.0,
                state=state
            ))
    else:
        # Use report data
        for step_name in pipeline:
            step_data = by_step.get(step_name, {})
            passed = step_data.get("passed", 0)
            failed = step_data.get("failed", 0)
            total = passed + failed

            # Determine state
            if total == 0:
                state = "pending"
            elif total < total_units:
                state = "running"
            else:
                state = "complete"

            steps.append(StepStatus(
                name=step_name,
                completed=passed,
                total=total_units if total_units > 0 else total,
                cost=0.0,
                state=state
            ))

    # If no report, fall back to manifest-based calculation
    if not report:
        chunks_data = manifest.get("chunks", {})
        for chunk_data in chunks_data.values():
            total_valid += chunk_data.get("valid", 0)
            total_failed += chunk_data.get("failed", 0)
        total_processed = total_valid + total_failed
        pass_rate = (total_valid / total_processed * 100) if total_processed > 0 else 0.0

    # Process chunks
    chunks_data = manifest.get("chunks", {})
    chunks = []

    for chunk_name, chunk_data in sorted(chunks_data.items()):
        state = chunk_data.get("state", "PENDING")
        chunk_items = chunk_data.get("items", 0)
        valid = chunk_data.get("valid", 0)
        failed = chunk_data.get("failed", 0)

        # Determine display step for chunk
        if state == "VALIDATED":
            # Fully complete - show last pipeline step
            display_step = pipeline[-1] if pipeline else "complete"
        elif "_" in state:
            # In progress - show current step
            step, status = parse_state(state)
            display_step = step
        else:
            display_step = "pending"

        chunks.append(ChunkStatus(
            name=chunk_name,
            step=display_step,
            state=state,
            valid=valid,
            failed=failed,
            total=chunk_items
        ))

    # Pipeline name from metadata
    from .utils.runs import get_run_pipeline_name
    pipeline_name = get_run_pipeline_name(manifest, run_dir)

    # Get max_units from metadata (if set during init, with backwards compat for "limit")
    max_units = metadata.get("max_units") or metadata.get("limit")

    # Load realtime progress data (for running realtime jobs)
    realtime_progress = None
    rt_data = metadata.get("realtime_progress", {})
    if rt_data:
        realtime_progress = RealtimeProgress(
            units_completed=rt_data.get("units_completed", 0),
            units_total=rt_data.get("units_total", 0),
            tokens_so_far=rt_data.get("tokens_so_far", 0),
            cost_so_far=rt_data.get("cost_so_far", 0.0),
            estimated_remaining_seconds=rt_data.get("estimated_remaining_seconds", 0),
        )

    poll_interval = metadata.get("poll_interval", 30)

    return RunData(
        run_dir=run_dir,
        run_name=run_name,
        pipeline_name=pipeline_name,
        mode=mode,
        elapsed_time=format_elapsed_time(elapsed_seconds),
        total_cost=total_cost,
        max_units=max_units,
        poll_interval=poll_interval,
        pipeline=pipeline,
        steps=steps,
        chunks=chunks,
        total_tokens=total_tokens,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        pass_rate=pass_rate,
        total_valid=total_valid,
        total_failed=total_failed,
        realtime_progress=realtime_progress
    )
