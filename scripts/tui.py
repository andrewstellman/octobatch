#!/usr/bin/env python3
"""
tui.py - Entry point for Octobatch TUI.

Usage:
    python scripts/tui.py              # Show HomeScreen with runs/pipelines
    python scripts/tui.py runs/my_run  # Open specific run directly
    python scripts/tui.py --dump       # Print home screen data to stdout
    python scripts/tui.py --dump --run-dir runs/my_run  # Print run detail to stdout
    python scripts/tui.py --dump --json # JSON output for machine-readable verification
    python scripts/tui.py --help
"""

import argparse
import sys
from pathlib import Path


def main():
    """Main entry point."""
    # Load .env file from current directory or parents
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Octobatch TUI - Terminal interface for batch processing runs"
    )
    parser.add_argument(
        "run_dir_positional",
        type=Path,
        nargs="?",
        default=None,
        metavar="run_dir",
        help="Path to the run directory (optional - shows HomeScreen if not provided)"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging to tui_debug.log"
    )
    parser.add_argument(
        "--dump",
        action="store_true",
        help="Render screen data to stdout and exit (no interactive terminal)"
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        dest="run_dir_flag",
        help="Run directory for --dump mode detail view"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output as JSON (used with --dump)"
    )

    args = parser.parse_args()

    # Resolve run_dir: --run-dir flag takes precedence, then positional arg
    run_dir = args.run_dir_flag or args.run_dir_positional

    # Handle --dump mode (no interactive terminal)
    if args.dump:
        from tui_dump import dump_home, dump_run
        if run_dir is not None:
            if not run_dir.exists():
                print(f"Error: Run directory not found: {run_dir}", file=sys.stderr)
                sys.exit(1)
            manifest_path = run_dir / "MANIFEST.json"
            if not manifest_path.exists():
                print(f"Error: MANIFEST.json not found in {run_dir}", file=sys.stderr)
                sys.exit(1)
            sys.exit(dump_run(run_dir, args.json_output))
        else:
            sys.exit(dump_home(args.json_output))

    # Check for textual
    try:
        import textual  # noqa: F401
    except ImportError:
        print("Error: textual package not installed.", file=sys.stderr)
        print("Install with: pip install textual", file=sys.stderr)
        sys.exit(1)

    # Validate run directory if provided
    if run_dir is not None:
        if not run_dir.exists():
            print(f"Error: Run directory not found: {run_dir}", file=sys.stderr)
            sys.exit(1)

        manifest_path = run_dir / "MANIFEST.json"
        if not manifest_path.exists():
            print(f"Error: MANIFEST.json not found in {run_dir}", file=sys.stderr)
            sys.exit(1)

    # Run the TUI
    from tui.app import run_tui
    run_tui(run_dir, debug=args.debug)


if __name__ == "__main__":
    main()
