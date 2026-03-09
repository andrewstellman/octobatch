#!/usr/bin/env python3
"""
Compare how two runs played the same dealt hands.

Given two runs with identical seeds (same pipeline, same repeat count),
shows how different models diverged on identical input.

Usage:
    python3 scripts/compare_hands.py run1 run2 [--unit-id <id>] [--sample N] [--step <step>]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from run_tools import compare_hands, _resolve_run_dir


def main():
    parser = argparse.ArgumentParser(
        description="Compare how two runs played the same dealt hands"
    )
    parser.add_argument("run1", help="First run directory or name")
    parser.add_argument("run2", help="Second run directory or name")
    parser.add_argument("--unit-id", help="Show detailed comparison for one specific unit")
    parser.add_argument("--sample", type=int, help="Show N random divergent pairs")
    parser.add_argument("--step", default="play_hand", help="Pipeline step to compare (default: play_hand)")

    args = parser.parse_args()

    run_dir1 = _resolve_run_dir(args.run1)
    run_dir2 = _resolve_run_dir(args.run2)

    result = compare_hands(
        run_dir1, run_dir2,
        unit_id=args.unit_id,
        sample=args.sample,
        step=args.step,
    )

    if result.get("error"):
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)

    print(result["text"])


if __name__ == "__main__":
    main()
