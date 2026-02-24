"""
Run launcher - subprocess management for starting orchestrator runs.

Extracted from new_run_modal.py to separate UI concerns from process management.
"""

import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

from .common import _log


def _detach_kwargs() -> dict:
    """Return subprocess kwargs to detach child from the TUI's console.

    On Unix, ``start_new_session=True`` calls ``setsid()`` which fully
    detaches the child process group from the controlling terminal.

    On Windows there is no ``setsid``.  ``start_new_session`` only maps to
    ``CREATE_NEW_PROCESS_GROUP`` which is *not* enough — the child still
    shares the parent's console (held in raw mode by Textual) and may
    silently fail.  We add ``CREATE_NO_WINDOW`` so the child never tries
    to attach to or allocate a console window.
    """
    if sys.platform == "win32":
        return {
            "creationflags": (
                subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.CREATE_NO_WINDOW
            ),
        }
    return {"start_new_session": True}


def start_realtime_run(
    orchestrate_path: Path,
    pipeline_name: str,
    run_dir: Path,
    max_units: Optional[int] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    repeat: Optional[int] = None,
) -> Tuple[bool, Optional[str]]:
    """Start a realtime run with --init --realtime combined.

    Returns:
        Tuple of (success: bool, error_message: Optional[str])
    """
    cmd = [
        sys.executable,
        str(orchestrate_path),
        "--pipeline", pipeline_name,
        "--run-dir", str(run_dir),
        "--init",
        "--realtime",
        "--yes",
    ]
    if max_units is not None:
        cmd.extend(["--max-units", str(max_units)])
    if repeat is not None:
        cmd.extend(["--repeat", str(repeat)])
    if provider:
        cmd.extend(["--provider", provider])
    if model:
        cmd.extend(["--model", model])

    _log.debug(f"Realtime command: {' '.join(cmd)}")

    try:
        # Do NOT create run_dir here — orchestrate.py --init expects to
        # create it and will abort with "Run directory already exists" if
        # the directory is already present.  Place startup logs in the
        # parent directory (runs/) with a run-specific name.  Don't try
        # to move them afterward — Windows file locks prevent renames
        # while the subprocess still holds the file handles.
        parent = run_dir.parent
        parent.mkdir(parents=True, exist_ok=True)

        run_name = run_dir.name
        log_path = parent / f"startup_{run_name}.log"
        stderr_path = parent / f"startup_{run_name}_stderr.log"
        log_file = open(log_path, "w")
        stderr_file = open(stderr_path, "w")

        process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=stderr_file,
            **_detach_kwargs(),
        )

        _log.debug(f"Realtime process started with PID {process.pid}")

        try:
            returncode = process.wait(timeout=2.0)
            log_file.close()
            stderr_file.close()
            if returncode != 0:
                try:
                    # Read stderr first — that's where orchestrate.py
                    # writes its error messages.
                    error_content = stderr_path.read_text()[:500].strip()
                    if not error_content:
                        error_content = log_path.read_text()[:500].strip()
                    return False, f"Run failed: {error_content}" if error_content else f"Run failed with exit code {returncode}"
                except Exception:
                    return False, f"Run failed with exit code {returncode}"
            return True, None
        except subprocess.TimeoutExpired:
            # Process still running - expected for realtime
            return True, None

    except Exception as e:
        _log.debug(f"Failed to start realtime run: {e}")
        return False, f"Failed to start: {e}"


def start_batch_run(
    orchestrate_path: Path,
    pipeline_name: str,
    run_dir: Path,
    max_units: Optional[int] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    repeat: Optional[int] = None,
) -> Tuple[bool, Optional[str]]:
    """Start a batch run with two-step process: init then watch.

    Returns:
        Tuple of (success: bool, error_message: Optional[str])
    """
    # Step 1: Initialize (blocking)
    init_cmd = [
        sys.executable,
        str(orchestrate_path),
        "--pipeline", pipeline_name,
        "--run-dir", str(run_dir),
        "--init",
        "--yes",
    ]
    if max_units is not None:
        init_cmd.extend(["--max-units", str(max_units)])
    if repeat is not None:
        init_cmd.extend(["--repeat", str(repeat)])
    if provider:
        init_cmd.extend(["--provider", provider])
    if model:
        init_cmd.extend(["--model", model])

    _log.debug(f"Init command: {' '.join(init_cmd)}")

    try:
        result = subprocess.run(
            init_cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            error_msg = result.stderr[:300] if result.stderr else f"Exit code {result.returncode}"
            _log.debug(f"Init failed: {error_msg}")
            return False, f"Init failed: {error_msg}"

    except subprocess.TimeoutExpired:
        return False, "Initialization timed out"
    except Exception as e:
        _log.debug(f"Init exception: {e}")
        return False, f"Init error: {e}"

    # Step 2: Verify init succeeded
    if not run_dir.exists() or not (run_dir / "MANIFEST.json").exists():
        return False, "Run directory not created properly"

    _log.debug(f"Init succeeded, run_dir exists: {run_dir}")

    # Step 3: Start watch mode (background)
    watch_cmd = [
        sys.executable,
        str(orchestrate_path),
        "--run-dir", str(run_dir),
        "--watch",
    ]

    _log.debug(f"Watch command: {' '.join(watch_cmd)}")

    try:
        log_path = run_dir / "orchestrator.log"
        log_file = open(log_path, "w")
        stderr_path = run_dir / "orchestrator_stderr.log"
        stderr_file = open(stderr_path, "w")

        process = subprocess.Popen(
            watch_cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=stderr_file,
            **_detach_kwargs(),
        )

        _log.debug(f"Watch process started with PID {process.pid}")
        return True, None

    except Exception as e:
        _log.debug(f"Failed to start watch: {e}")
        return False, f"Failed to start watch: {e}"
