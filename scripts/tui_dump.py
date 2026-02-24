"""
Headless dump functions for Octobatch TUI.

Used by tui.py --dump to render screen data to stdout without launching
the interactive terminal. Also importable by tests.
"""

import json
import sys
from pathlib import Path


def dump_home(as_json: bool) -> int:
    """Dump home screen data (all runs) to stdout."""
    from version import __version__
    from tui.utils.runs import scan_runs

    runs = scan_runs()

    if as_json:
        output = []
        for r in runs:
            output.append({
                "name": r["name"],
                "status": r["status"],
                "progress": r["progress"],
                "total_units": r.get("total_units", 0),
                "valid_units": r.get("valid_units", 0),
                "failed_units": r.get("unit_failure_count", 0),
                "cost": r.get("cost_value", 0),
                "total_tokens": r.get("total_tokens", 0),
                "mode": r.get("mode_display", r.get("mode", "")),
                "duration": r.get("duration", "--"),
                "pipeline_name": r.get("pipeline_name", ""),
                "started": r.get("started").isoformat() if r.get("started") else None,
            })
        print(json.dumps(output, indent=2))
        return 0

    # Formatted text table
    print(f"Octobatch v{__version__}")
    print()

    if not runs:
        print("No runs found.")
        return 0

    # Header
    fmt = "{:<30s} {:>5s} {:>6s} {:>6s} {:>6s} {:>10s} {:>10s} {:>8s} {:>10s}"
    header = fmt.format(
        "Run", "Prog", "Units", "Valid", "Fail", "Cost", "Tokens", "Mode", "Status"
    )
    print(header)
    print("-" * len(header))

    for r in runs:
        progress = f"{r['progress']}%"
        total_units = str(r.get("total_units", 0))
        valid = str(r.get("valid_units", 0))
        failed = str(r.get("unit_failure_count", 0))
        cost = r.get("cost", "--")
        tokens = str(r.get("total_tokens", 0))
        mode = r.get("mode_display", r.get("mode", ""))
        status = r["status"]

        print(fmt.format(
            r["name"][:30], progress, total_units, valid, failed,
            cost, tokens, mode, status
        ))

    return 0


def dump_run(run_dir: Path, as_json: bool) -> int:
    """Dump run detail data to stdout."""
    from tui.data import load_run_data
    from tui.utils.runs import load_manifest

    manifest = load_manifest(run_dir)
    if manifest is None:
        print(f"Error: Could not load manifest from {run_dir}", file=sys.stderr)
        return 1

    run_data = load_run_data(run_dir)
    metadata = manifest.get("metadata", {})
    manifest_status = manifest.get("status", "unknown")

    # Compute unit counts from manifest chunks
    chunks_data = manifest.get("chunks", {})
    total_units = sum(c.get("items", 0) for c in chunks_data.values())
    valid = run_data.total_valid
    failed = max(0, total_units - valid) if manifest_status in ("complete", "failed") else run_data.total_failed

    # Tokens
    initial_in = metadata.get("initial_input_tokens", 0) or 0
    initial_out = metadata.get("initial_output_tokens", 0) or 0
    retry_in = metadata.get("retry_input_tokens", 0) or 0
    retry_out = metadata.get("retry_output_tokens", 0) or 0
    total_tokens = initial_in + initial_out + retry_in + retry_out

    # Duration
    duration = "--"
    created = manifest.get("created", "")
    completed = manifest.get("completed_at", manifest.get("updated", ""))
    if created and completed:
        from datetime import datetime
        try:
            s = datetime.fromisoformat(created.replace('Z', '+00:00'))
            e = datetime.fromisoformat(completed.replace('Z', '+00:00'))
            delta = e - s
            secs = int(delta.total_seconds())
            if secs >= 3600:
                duration = f"{secs // 3600}h {(secs % 3600) // 60}m {secs % 60}s"
            elif secs >= 60:
                duration = f"{secs // 60}m {secs % 60}s"
            else:
                duration = f"{secs}s"
        except (ValueError, TypeError):
            pass

    # Cost
    cost_value = 0.0
    rt = manifest.get("realtime_progress", {})
    if rt:
        cost_value = rt.get("cost_so_far", 0)

    # Pipeline steps
    steps_info = []
    for step in run_data.steps:
        steps_info.append({
            "name": step.name,
            "valid": step.completed,
            "total": step.total,
            "state": step.state,
        })

    if as_json:
        output = {
            "name": run_dir.name,
            "status": manifest_status,
            "pipeline_name": metadata.get("pipeline_name", ""),
            "provider": metadata.get("provider", ""),
            "model": metadata.get("model", ""),
            "mode": metadata.get("mode", ""),
            "total_units": total_units,
            "valid_units": valid,
            "failed_units": failed,
            "cost": cost_value,
            "total_tokens": total_tokens,
            "input_tokens": initial_in + retry_in,
            "output_tokens": initial_out + retry_out,
            "duration": duration,
            "steps": steps_info,
        }
        print(json.dumps(output, indent=2))
        return 0

    # Formatted text output
    print(f"Run: {run_dir.name}")
    print(f"  Status:   {manifest_status}")
    print(f"  Pipeline: {metadata.get('pipeline_name', '')}")
    print(f"  Provider: {metadata.get('provider', '')} / {metadata.get('model', '')}")
    print(f"  Mode:     {metadata.get('mode', '')}")
    print(f"  Units:    {valid}/{total_units} valid, {failed} failed")
    print(f"  Cost:     ${cost_value:.4f}")
    print(f"  Tokens:   {total_tokens:,} ({initial_in + retry_in:,} in + {initial_out + retry_out:,} out)")
    print(f"  Duration: {duration}")
    print()
    print("Pipeline Steps:")

    step_fmt = "  {:<30s} {:>8s} {:>10s}"
    print(step_fmt.format("Step", "Valid", "State"))
    print("  " + "-" * 50)
    for step in steps_info:
        valid_str = f"{step['valid']}/{step['total']}"
        print(step_fmt.format(step["name"], valid_str, step["state"]))

    return 0
