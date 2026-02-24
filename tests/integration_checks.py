#!/usr/bin/env python3
"""Quality check helpers for integration tests."""

import json
import gzip
import sys
from pathlib import Path


def load_validated(run_dir: str, step_name: str) -> list[dict]:
    """Load all validated JSONL records for a step across all chunks."""
    run_path = Path(run_dir)
    chunks_dir = run_path / "chunks"
    records = []
    if not chunks_dir.exists():
        return records
    for chunk_dir in sorted(chunks_dir.glob("chunk_*")):
        if not chunk_dir.is_dir():
            continue
        for suffix in [".jsonl", ".jsonl.gz"]:
            f = chunk_dir / f"{step_name}_validated{suffix}"
            if f.exists():
                opener = gzip.open if suffix.endswith(".gz") else open
                with opener(f, "rt") as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            try:
                                records.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
    return records


def load_failures(run_dir: str, step_name: str) -> list[dict]:
    """Load all failure JSONL records for a step across all chunks."""
    run_path = Path(run_dir)
    chunks_dir = run_path / "chunks"
    records = []
    if not chunks_dir.exists():
        return records
    for chunk_dir in sorted(chunks_dir.glob("chunk_*")):
        if not chunk_dir.is_dir():
            continue
        for suffix in [".jsonl", ".jsonl.gz"]:
            f = chunk_dir / f"{step_name}_failures{suffix}"
            if f.exists():
                opener = gzip.open if suffix.endswith(".gz") else open
                with opener(f, "rt") as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            try:
                                records.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
    return records


def get_manifest(run_dir: str) -> dict:
    """Load MANIFEST.json."""
    p = Path(run_dir) / "MANIFEST.json"
    if p.exists():
        return json.loads(p.read_text())
    return {}


def get_final_step(run_dir: str) -> str:
    """Get the last pipeline step name."""
    manifest = get_manifest(run_dir)
    # Pipeline is a top-level field, not nested under metadata
    pipeline = manifest.get("pipeline", [])
    if pipeline:
        return pipeline[-1]
    return ""


def check_sailor(run_dir: str) -> dict:
    """Quality checks for DrunkenSailor pipeline."""
    final_step = get_final_step(run_dir)
    if not final_step:
        # Try common step names
        final_step = "analyze"

    validated = load_validated(run_dir, final_step)
    failures = load_failures(run_dir, final_step)
    manifest = get_manifest(run_dir)

    results = {
        "pipeline": "DrunkenSailor",
        "final_step": final_step,
        "validated_count": len(validated),
        "failure_count": len(failures),
        "status": manifest.get("status", "unknown"),
        "checks": {}
    }

    if not validated:
        results["checks"]["outcome_balance"] = {"pass": False, "detail": "No validated records"}
        results["checks"]["outcome_valid_enum"] = {"pass": False, "detail": "No validated records"}
        return results

    # Check outcome_valid_enum
    valid_outcomes = {"fell_in_water", "reached_ship"}
    outcomes = []
    for r in validated:
        outcome = r.get("outcome", "")
        outcomes.append(outcome)

    invalid = [o for o in outcomes if o not in valid_outcomes]
    results["checks"]["outcome_valid_enum"] = {
        "pass": len(invalid) == 0,
        "detail": f"{len(invalid)}/{len(outcomes)} invalid" if invalid else f"All {len(outcomes)} valid",
        "invalid_samples": invalid[:5] if invalid else []
    }

    # Check outcome_balance
    fell_count = sum(1 for o in outcomes if o == "fell_in_water")
    total = len(outcomes)
    ratio = fell_count / total if total > 0 else 0
    results["checks"]["outcome_balance"] = {
        "pass": 0.25 <= ratio <= 0.75,
        "detail": f"fell_in_water: {fell_count}/{total} ({ratio:.1%})",
        "ratio": ratio
    }

    return results


def check_blackjack(run_dir: str) -> dict:
    """Quality checks for Blackjack pipeline."""
    final_step = get_final_step(run_dir)
    if not final_step or final_step == "score_coherence":
        final_step = "analyze_difficulty"

    validated = load_validated(run_dir, final_step)
    failures = load_failures(run_dir, final_step)
    manifest = get_manifest(run_dir)

    results = {
        "pipeline": "Blackjack",
        "final_step": final_step,
        "validated_count": len(validated),
        "failure_count": len(failures),
        "status": manifest.get("status", "unknown"),
        "checks": {}
    }

    if not validated:
        results["checks"]["all_strategies_present"] = {"pass": False, "detail": "No validated records"}
        results["checks"]["result_valid_enum"] = {"pass": False, "detail": "No validated records"}
        results["checks"]["accuracy_above_gate"] = {"pass": False, "detail": "No validated records"}
        return results

    # Check all_strategies_present
    required = {"The Pro", "The Gambler", "The Coward"}
    found_strategies = set()
    for r in validated:
        # Strategy might be in various fields
        strat = r.get("strategy_name", "") or r.get("strategy", "")
        if not strat:
            # Check in input/context
            for key in ["input", "context"]:
                if isinstance(r.get(key), dict):
                    strat = r[key].get("strategy_name", "") or r[key].get("strategy", "")
                    if strat:
                        break
        if strat:
            found_strategies.add(strat)

    missing = required - found_strategies
    results["checks"]["all_strategies_present"] = {
        "pass": len(missing) == 0,
        "detail": f"Found: {found_strategies}" if not missing else f"Missing: {missing}",
    }

    # Check result_valid_enum
    valid_results = {"player_wins", "dealer_wins", "push"}
    game_results = []
    for r in validated:
        res = r.get("result", "")
        game_results.append(res)

    invalid = [r for r in game_results if r not in valid_results]
    results["checks"]["result_valid_enum"] = {
        "pass": len(invalid) == 0,
        "detail": f"{len(invalid)}/{len(game_results)} invalid" if invalid else f"All {len(game_results)} valid",
        "invalid_samples": invalid[:5] if invalid else []
    }

    # Check accuracy_above_gate
    accuracies = []
    below_gate = []
    for r in validated:
        acc = r.get("accuracy")
        if acc is not None:
            try:
                acc = float(acc)
                accuracies.append(acc)
                if acc < 0.7:
                    below_gate.append(acc)
            except (ValueError, TypeError):
                pass

    results["checks"]["accuracy_above_gate"] = {
        "pass": len(below_gate) == 0 and len(accuracies) > 0,
        "detail": f"{len(below_gate)} below 0.7 gate" if below_gate else f"All {len(accuracies)} >= 0.7",
        "min_accuracy": min(accuracies) if accuracies else None,
    }

    return results


def check_npc(run_dir: str) -> dict:
    """Quality checks for NPCDialog pipeline.

    Actual schema uses: final_tone (string enum), personality_consistency (number 0-1,
    gate >= 0.6), mood_responsiveness (number 0-1, gate >= 0.6).
    Final step is score_consistency.
    """
    # Try to get final step from manifest, fall back to score_consistency
    final_step = get_final_step(run_dir)
    if not final_step or final_step == "score":
        final_step = "score_consistency"

    validated = load_validated(run_dir, final_step)
    failures = load_failures(run_dir, final_step)
    manifest = get_manifest(run_dir)

    results = {
        "pipeline": "NPCDialog",
        "final_step": final_step,
        "validated_count": len(validated),
        "failure_count": len(failures),
        "status": manifest.get("status", "unknown"),
        "checks": {}
    }

    if not validated:
        results["checks"]["tone_valid_enum"] = {"pass": False, "detail": "No validated records"}
        results["checks"]["personality_above_gate"] = {"pass": False, "detail": "No validated records"}
        results["checks"]["mood_responsiveness_above_gate"] = {"pass": False, "detail": "No validated records"}
        return results

    # Check tone_valid_enum (actual pipeline enum from config validation)
    valid_tones = {"warm", "cold", "nervous", "hostile", "mysterious"}
    tones = []
    for r in validated:
        tone = r.get("final_tone", "")
        tones.append(tone)

    invalid = [t for t in tones if t not in valid_tones]
    results["checks"]["tone_valid_enum"] = {
        "pass": len(invalid) == 0,
        "detail": f"{len(invalid)}/{len(tones)} invalid" if invalid else f"All {len(tones)} valid",
        "invalid_samples": invalid[:5] if invalid else [],
        "tone_distribution": {t: tones.count(t) for t in set(tones)},
    }

    # Check personality_above_gate (personality_consistency >= 0.6)
    personalities = []
    below = []
    for r in validated:
        val = r.get("personality_consistency")
        if val is not None:
            try:
                val = float(val)
                personalities.append(val)
                if val < 0.6:
                    below.append(val)
            except (ValueError, TypeError):
                pass

    results["checks"]["personality_above_gate"] = {
        "pass": len(below) == 0 and len(personalities) > 0,
        "detail": f"{len(below)} below 0.6 gate" if below else f"All {len(personalities)} >= 0.6",
        "min_value": min(personalities) if personalities else None,
    }

    # Check mood_responsiveness_above_gate (mood_responsiveness >= 0.4)
    responsiveness = []
    below_mood = []
    for r in validated:
        val = r.get("mood_responsiveness")
        if val is not None:
            try:
                val = float(val)
                responsiveness.append(val)
                if val < 0.4:
                    below_mood.append(val)
            except (ValueError, TypeError):
                pass

    results["checks"]["mood_responsiveness_above_gate"] = {
        "pass": len(below_mood) == 0 and len(responsiveness) > 0,
        "detail": f"{len(below_mood)} below 0.4 gate" if below_mood else f"All {len(responsiveness)} >= 0.4",
        "min_value": min(responsiveness) if responsiveness else None,
    }

    return results


def get_cost_and_duration(run_dir: str) -> dict:
    """Get cost and duration from manifest."""
    manifest = get_manifest(run_dir)
    metadata = manifest.get("metadata", {})

    cost = 0
    # Try realtime progress cost
    rt = manifest.get("realtime_progress", {})
    if rt:
        cost = rt.get("cost_so_far", 0)

    # Try metadata tokens for cost estimate
    if cost == 0:
        tokens = metadata.get("total_tokens", 0) or 0
        retry_tokens = metadata.get("retry_tokens", 0) or 0
        total_tokens = tokens + retry_tokens
        # Rough estimate
        cost = total_tokens * 0.000001  # Very rough

    # Duration
    start = metadata.get("start_time", "")
    end = manifest.get("completed_at", "")
    duration = "--"
    if start and end:
        from datetime import datetime
        try:
            s = datetime.fromisoformat(start.replace('Z', '+00:00'))
            e = datetime.fromisoformat(end.replace('Z', '+00:00'))
            delta = e - s
            secs = int(delta.total_seconds())
            if secs >= 3600:
                duration = f"{secs // 3600}h {(secs % 3600) // 60}m {secs % 60}s"
            elif secs >= 60:
                duration = f"{secs // 60}m {secs % 60}s"
            else:
                duration = f"{secs}s"
        except Exception:
            pass

    return {"cost": cost, "duration": duration, "status": manifest.get("status", "unknown")}


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: integration_checks.py <pipeline> <run_dir>")
        sys.exit(1)

    pipeline = sys.argv[1]
    run_dir = sys.argv[2]

    if pipeline == "DrunkenSailor":
        result = check_sailor(run_dir)
    elif pipeline == "Blackjack":
        result = check_blackjack(run_dir)
    elif pipeline == "NPCDialog":
        result = check_npc(run_dir)
    else:
        print(f"Unknown pipeline: {pipeline}")
        sys.exit(1)

    info = get_cost_and_duration(run_dir)
    result.update(info)

    print(json.dumps(result, indent=2, default=str))
