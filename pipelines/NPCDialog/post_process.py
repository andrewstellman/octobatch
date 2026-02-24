"""
NPC Dialog Pipeline - Retry Analysis

Shows which NPC/mood combinations had the most validation failures (retries),
indicating which combinations are hardest for the LLM to get right.

Usage: python pipelines/NPCDialog/post_process.py <run_dir>
"""

import json
import sys
from collections import defaultdict
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print("Usage: python post_process.py <run_dir>", file=sys.stderr)
        sys.exit(1)

    run_dir = Path(sys.argv[1])
    chunks_dir = run_dir / "chunks"

    if not chunks_dir.exists():
        print("No chunks directory found.")
        return

    # Count failures per unit_id across both steps
    failures_per_unit = defaultdict(int)

    for failures_file in chunks_dir.glob("*/*_failures.jsonl"):
        with open(failures_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    unit_id = record.get("unit_id", "unknown")
                    failures_per_unit[unit_id] += 1
                except json.JSONDecodeError:
                    continue

    if not failures_per_unit:
        print("No retries detected — all dialogs passed validation on first attempt.")
        return

    # Parse unit_id to extract NPC and mood
    # Cross-product unit_ids are formatted as: npc_id__mood_id__topic_id
    combo_failures = defaultdict(int)
    npc_failures = defaultdict(int)
    mood_failures = defaultdict(int)

    for unit_id, count in failures_per_unit.items():
        parts = unit_id.split("__")
        if len(parts) >= 2:
            npc_id = parts[0]
            mood_id = parts[1]
            combo_key = f"{npc_id} / {mood_id}"
            combo_failures[combo_key] += count
            npc_failures[npc_id] += count
            mood_failures[mood_id] += count
        else:
            combo_failures[unit_id] += count

    # Print report
    print("Retry Analysis: Hardest NPC/Mood Combinations")
    print("=" * 55)
    print()

    # Sort by most retries
    sorted_combos = sorted(combo_failures.items(), key=lambda x: -x[1])

    print(f"{'NPC / Mood':<40} {'Retries':>8}")
    print("-" * 55)
    for combo, count in sorted_combos:
        print(f"{combo:<40} {count:>8}")

    print()
    total_retries = sum(failures_per_unit.values())
    total_units_retried = len(failures_per_unit)
    print(f"Total retries: {total_retries} across {total_units_retried} units")

    # Summary by NPC
    print()
    print("Retries by NPC Personality")
    print("-" * 35)
    for npc, count in sorted(npc_failures.items(), key=lambda x: -x[1]):
        print(f"  {npc:<25} {count:>5}")

    # Summary by mood
    print()
    print("Retries by Player Mood")
    print("-" * 35)
    for mood, count in sorted(mood_failures.items(), key=lambda x: -x[1]):
        print(f"  {mood:<25} {count:>5}")


if __name__ == "__main__":
    main()
