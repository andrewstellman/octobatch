#!/usr/bin/env python3
"""
analyze_results.py - Generic results analyzer for Monte Carlo simulations.

Reads validated results from the final pipeline step of Octobatch runs,
groups by a specified field, and provides flexible aggregation options.

Data source: {run_dir}/chunks/chunk_*/{last_step}_validated.jsonl
(reads MANIFEST.json to determine the last step; falls back to all files)

COUNTING MODE (--count-field):
  Count occurrences of each unique value and show percentages.
  Optionally calculate net score with --net-positive/--net-negative.

NUMERIC MODE (--numeric-field):
  Calculate statistics on a numeric field per group.
  Default stats: Count, Mean, Median, StdDev, Min, Max
  Custom stats via --custom-stat "Name=Expression"

OUTPUT FORMATS:
  --output-format table  (default) ASCII table with formatting
  --output-format csv    CSV format for data tools
  --output FILE          Write to file instead of stdout

EXAMPLES:

  # Count with net calculation (e.g., Blackjack win/loss)
  python scripts/analyze_results.py runs/blackjack \\
    --group-by strategy --count-field result \\
    --net-positive player_wins --net-negative dealer_wins \\
    --title "Blackjack Strategy Comparison"

  # CSV output to stdout
  python scripts/analyze_results.py runs/blackjack \\
    --group-by strategy --count-field result \\
    --output-format csv

  # CSV output to file
  python scripts/analyze_results.py runs/blackjack \\
    --group-by strategy --count-field result \\
    --output-format csv --output results.csv

  # Numeric analysis with custom stats to CSV
  python scripts/analyze_results.py runs/blackjack \\
    --group-by strategy --numeric-field player_final_total \\
    --custom-stat "Spread=max_val-min_val" \\
    --output-format csv --output stats.csv

CUSTOM STAT EXPRESSIONS:

  Available variables in expressions:
    data      - List of all values for this group
    count     - Number of values
    mean      - Arithmetic mean
    median    - Median value
    stdev     - Standard deviation (0 if n<2)
    variance  - Variance (0 if n<2)
    min_val   - Minimum value
    max_val   - Maximum value
    sum_val   - Sum of all values

  Example expressions:
    "CV=stdev/mean if mean != 0 else 0"     # Coefficient of Variation
    "Spread=max_val-min_val"                 # Range
    "SEM=stdev/sqrt(count)"                  # Standard Error of Mean
    "Pct95=sorted(data)[int(0.95*count)]"    # 95th percentile (approx)
"""

import argparse
import csv
import io
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Optional asteval for custom expressions
try:
    from asteval import Interpreter
    ASTEVAL_AVAILABLE = True
except ImportError:
    ASTEVAL_AVAILABLE = False


def load_results(run_dir: Path) -> list[dict]:
    """Load validated results from the final pipeline step's chunk directories."""
    results = []
    chunks_dir = run_dir / "chunks"

    if not chunks_dir.exists():
        print(f"Error: No chunks directory found at {chunks_dir}", file=sys.stderr)
        return results

    # Read manifest to determine the final pipeline step
    manifest_path = run_dir / "MANIFEST.json"
    last_step = None
    if manifest_path.exists():
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
            pipeline = manifest.get("pipeline", [])
            if pipeline:
                last_step = pipeline[-1]
        except Exception:
            pass

    if last_step:
        # Only load the final step's validated output
        result_files = sorted(chunks_dir.glob(f"chunk_*/{last_step}_validated.jsonl"))
        if not result_files:
            print(f"Error: No {last_step}_validated.jsonl files found in {chunks_dir}/chunk_*/", file=sys.stderr)
            return results
        print(f"Reading final step '{last_step}' results...", file=sys.stderr)
    else:
        # Fallback: no manifest or empty pipeline — load all validated files
        result_files = sorted(chunks_dir.glob("chunk_*/*_validated.jsonl"))
        if not result_files:
            print(f"Error: No *_validated.jsonl files found in {chunks_dir}/chunk_*/", file=sys.stderr)
            return results
        print("Warning: Could not determine pipeline — loading all validated files", file=sys.stderr)

    for result_file in result_files:
        try:
            with open(result_file, "r", encoding="utf-8", errors="replace") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        results.append(data)
                    except json.JSONDecodeError as e:
                        print(f"Warning: Invalid JSON at {result_file}:{line_num}: {e}", file=sys.stderr)
        except Exception as e:
            print(f"Warning: Could not read {result_file}: {e}", file=sys.stderr)

    return results


def aggregate_counts(
    results: list[dict],
    group_by: str,
    count_field: str
) -> tuple[dict[str, Counter], set[str], int]:
    """
    Aggregate results by grouping field and counting values.

    Returns:
        - Dict mapping group_value -> Counter of count_field values
        - Set of all unique count_field values seen
        - Number of skipped results
    """
    groups: dict[str, Counter] = defaultdict(Counter)
    all_values: set[str] = set()
    skipped = 0

    for result in results:
        group_value = result.get(group_by)
        if group_value is None:
            skipped += 1
            continue

        count_value = result.get(count_field)
        if count_value is None:
            skipped += 1
            continue

        group_key = str(group_value)
        count_key = str(count_value)

        groups[group_key][count_key] += 1
        all_values.add(count_key)

    return dict(groups), all_values, skipped


def aggregate_numeric(
    results: list[dict],
    group_by: str,
    numeric_field: str
) -> tuple[dict[str, list[float]], int]:
    """
    Aggregate results by grouping field, collecting numeric values.

    Returns:
        - Dict mapping group_value -> list of numeric values
        - Number of skipped results
    """
    groups: dict[str, list[float]] = defaultdict(list)
    skipped = 0

    for result in results:
        group_value = result.get(group_by)
        if group_value is None:
            skipped += 1
            continue

        numeric_value = result.get(numeric_field)
        if numeric_value is None:
            skipped += 1
            continue

        try:
            num = float(numeric_value)
        except (ValueError, TypeError):
            skipped += 1
            continue

        group_key = str(group_value)
        groups[group_key].append(num)

    return dict(groups), skipped


def calculate_stats(data: list[float]) -> dict[str, float]:
    """
    Calculate standard statistics for a list of numbers.

    Returns dict with: count, mean, median, stdev, variance, min_val, max_val, sum_val
    """
    n = len(data)
    if n == 0:
        return {
            "count": 0,
            "mean": 0.0,
            "median": 0.0,
            "stdev": 0.0,
            "variance": 0.0,
            "min_val": 0.0,
            "max_val": 0.0,
            "sum_val": 0.0,
        }

    mean_val = statistics.mean(data)
    median_val = statistics.median(data)
    sum_val = sum(data)
    min_val = min(data)
    max_val = max(data)

    # stdev/variance need n >= 2
    if n >= 2:
        stdev_val = statistics.stdev(data)
        var_val = statistics.variance(data)
    else:
        stdev_val = 0.0
        var_val = 0.0

    return {
        "count": n,
        "mean": mean_val,
        "median": median_val,
        "stdev": stdev_val,
        "variance": var_val,
        "min_val": min_val,
        "max_val": max_val,
        "sum_val": sum_val,
    }


def evaluate_custom_stat(
    expr: str,
    data: list[float],
    stats: dict[str, float]
) -> tuple[float | None, str | None]:
    """
    Evaluate a custom statistic expression using asteval.

    Args:
        expr: Expression string (e.g., "stdev/mean if mean != 0 else 0")
        data: List of numeric values
        stats: Pre-calculated statistics dict

    Returns:
        (value, error_message) - value is None if error, error_message is None if success
    """
    if not ASTEVAL_AVAILABLE:
        return None, "asteval not installed"

    try:
        interpreter = Interpreter()

        # Add data and stats to symbol table
        interpreter.symtable["data"] = data
        interpreter.symtable["count"] = stats["count"]
        interpreter.symtable["mean"] = stats["mean"]
        interpreter.symtable["median"] = stats["median"]
        interpreter.symtable["stdev"] = stats["stdev"]
        interpreter.symtable["variance"] = stats["variance"]
        interpreter.symtable["min_val"] = stats["min_val"]
        interpreter.symtable["max_val"] = stats["max_val"]
        interpreter.symtable["sum_val"] = stats["sum_val"]

        # Add useful math functions
        import math
        interpreter.symtable["sqrt"] = math.sqrt
        interpreter.symtable["log"] = math.log
        interpreter.symtable["log10"] = math.log10
        interpreter.symtable["exp"] = math.exp
        interpreter.symtable["abs"] = abs
        interpreter.symtable["sorted"] = sorted
        interpreter.symtable["len"] = len
        interpreter.symtable["sum"] = sum
        interpreter.symtable["min"] = min
        interpreter.symtable["max"] = max

        result = interpreter(expr)

        if interpreter.error:
            errors = "; ".join(str(e.get_error()) for e in interpreter.error)
            return None, errors

        return float(result), None

    except Exception as e:
        return None, str(e)


def parse_custom_stats(custom_stat_args: list[str]) -> list[tuple[str, str]]:
    """
    Parse custom stat arguments into (name, expression) tuples.

    Args:
        custom_stat_args: List of "Name=Expression" strings

    Returns:
        List of (name, expression) tuples
    """
    parsed = []
    for arg in custom_stat_args:
        if "=" not in arg:
            print(f"Warning: Invalid custom stat format '{arg}' - expected 'Name=Expression'", file=sys.stderr)
            continue
        name, expr = arg.split("=", 1)
        name = name.strip()
        expr = expr.strip()
        if not name or not expr:
            print(f"Warning: Invalid custom stat '{arg}' - name and expression required", file=sys.stderr)
            continue
        parsed.append((name, expr))
    return parsed


def calculate_net(counts: Counter, net_positive: list[str], net_negative: list[str]) -> int:
    """
    Calculate net score based on positive and negative value lists.

    Args:
        counts: Counter of value occurrences
        net_positive: List of values to count as +1 each
        net_negative: List of values to count as -1 each

    Returns:
        Net score (positive_count - negative_count)
    """
    positive_count = 0
    negative_count = 0

    net_positive_lower = [v.lower() for v in net_positive]
    net_negative_lower = [v.lower() for v in net_negative]

    for key, count in counts.items():
        key_lower = str(key).lower()
        if key_lower in net_positive_lower:
            positive_count += count
        elif key_lower in net_negative_lower:
            negative_count += count

    return positive_count - negative_count


def format_count_table(
    groups: dict[str, Counter],
    all_values: set[str],
    net_positive: list[str] | None,
    net_negative: list[str] | None,
    title: str | None
) -> str:
    """Format count results as an ASCII table."""
    lines = []
    show_net = bool(net_positive or net_negative)

    # Title
    if title:
        lines.append(f"\n{title}")
        lines.append("=" * len(title))
    else:
        lines.append("\nResults Analysis")
        lines.append("=" * 16)

    # Sort values for consistent column order
    sorted_values = sorted(all_values)

    # Calculate column widths
    group_col_width = max(len("Group"), max(len(g) for g in groups.keys()) if groups else 5)
    total_col_width = max(len("Total"), 6)

    # Use full value names for columns
    value_col_widths = {v: max(len(v), 8) for v in sorted_values}
    net_col_width = 8

    # Build header
    header_parts = [
        f"{'Group':<{group_col_width}}",
        f"{'Total':>{total_col_width}}"
    ]
    for val in sorted_values:
        header_parts.append(f"{val:>{value_col_widths[val]}}")
    if show_net:
        header_parts.append(f"{'Net':>{net_col_width}}")

    header = " | ".join(header_parts)
    separator = "-" * len(header)

    lines.append("")
    lines.append(header)
    lines.append(separator)

    # Calculate data rows with net scores for sorting
    row_data = []
    for group_name, counts in groups.items():
        total = sum(counts.values())

        if show_net:
            net = calculate_net(counts, net_positive or [], net_negative or [])
        else:
            net = 0

        row_parts = [
            f"{group_name:<{group_col_width}}",
            f"{total:>{total_col_width}}"
        ]

        for val in sorted_values:
            count = counts.get(val, 0)
            pct = (count / total * 100) if total > 0 else 0
            col_width = value_col_widths[val]
            row_parts.append(f"{pct:>{col_width - 1}.1f}%")

        if show_net:
            net_str = f"{net:+d}" if net != 0 else "0"
            row_parts.append(f"{net_str:>{net_col_width}}")

        # Sort key: net if showing, otherwise first value percentage
        if show_net:
            sort_key = net
        else:
            first_val = sorted_values[0] if sorted_values else ""
            sort_key = counts.get(first_val, 0)

        row_data.append((sort_key, group_name, " | ".join(row_parts)))

    # Sort by sort_key descending
    row_data.sort(key=lambda x: x[0], reverse=True)

    for _, _, row in row_data:
        lines.append(row)

    lines.append(separator)

    # Summary
    total_results = sum(sum(c.values()) for c in groups.values())
    lines.append(f"Total: {total_results} results")

    # Top group line
    if row_data:
        top_key, top_name, _ = row_data[0]
        if show_net:
            lines.append(f"Top group by net: {top_name} ({top_key:+d})")

    lines.append("")
    return "\n".join(lines)


def format_count_csv(
    groups: dict[str, Counter],
    all_values: set[str],
    net_positive: list[str] | None,
    net_negative: list[str] | None
) -> str:
    """Format count results as CSV."""
    output = io.StringIO()
    writer = csv.writer(output)
    show_net = bool(net_positive or net_negative)

    # Sort values for consistent column order
    sorted_values = sorted(all_values)

    # Header
    header = ["Group", "Total"] + sorted_values
    if show_net:
        header.append("Net")
    writer.writerow(header)

    # Calculate data rows with net scores for sorting
    row_data = []
    for group_name, counts in groups.items():
        total = sum(counts.values())

        if show_net:
            net = calculate_net(counts, net_positive or [], net_negative or [])
        else:
            net = 0

        row = [group_name, total]

        # Percentages as decimals (e.g., 0.36 instead of 36%)
        for val in sorted_values:
            count = counts.get(val, 0)
            pct = (count / total) if total > 0 else 0
            row.append(round(pct, 2))

        if show_net:
            row.append(net)

        # Sort key: net if showing, otherwise first value percentage
        sort_key = net if show_net else (counts.get(sorted_values[0], 0) if sorted_values else 0)
        row_data.append((sort_key, row))

    # Sort by sort_key descending
    row_data.sort(key=lambda x: x[0], reverse=True)

    for _, row in row_data:
        writer.writerow(row)

    return output.getvalue()


def format_numeric_table(
    groups: dict[str, list[float]],
    custom_stats: list[tuple[str, str]],
    title: str | None
) -> str:
    """Format numeric analysis results as an ASCII table."""
    lines = []

    # Title
    if title:
        lines.append(f"\n{title}")
        lines.append("=" * len(title))
    else:
        lines.append("\nNumeric Analysis")
        lines.append("=" * 16)

    # Standard stat columns
    std_cols = ["Count", "Mean", "Median", "StdDev", "Min", "Max"]
    custom_col_names = [name for name, _ in custom_stats]

    # Calculate column widths
    group_col_width = max(len("Group"), max(len(g) for g in groups.keys()) if groups else 5)
    stat_col_width = 8

    # Build header
    header_parts = [f"{'Group':<{group_col_width}}"]
    for col in std_cols:
        header_parts.append(f"{col:>{stat_col_width}}")
    for col in custom_col_names:
        col_width = max(len(col), stat_col_width)
        header_parts.append(f"{col:>{col_width}}")

    header = " | ".join(header_parts)
    separator = "-" * len(header)

    lines.append("")
    lines.append(header)
    lines.append(separator)

    # Track errors for reporting
    errors_encountered = []

    # Build rows with stats
    row_data = []
    for group_name, data in groups.items():
        stats = calculate_stats(data)

        row_parts = [f"{group_name:<{group_col_width}}"]

        # Standard stats
        row_parts.append(f"{stats['count']:>{stat_col_width}}")
        row_parts.append(f"{stats['mean']:>{stat_col_width}.2f}")
        row_parts.append(f"{stats['median']:>{stat_col_width}.2f}")
        row_parts.append(f"{stats['stdev']:>{stat_col_width}.2f}")
        row_parts.append(f"{stats['min_val']:>{stat_col_width}.1f}")
        row_parts.append(f"{stats['max_val']:>{stat_col_width}.1f}")

        # Custom stats
        for col_name, expr in custom_stats:
            col_width = max(len(col_name), stat_col_width)
            value, error = evaluate_custom_stat(expr, data, stats)
            if error:
                row_parts.append(f"{'Err':>{col_width}}")
                errors_encountered.append(f"  {group_name}.{col_name}: {error}")
            else:
                row_parts.append(f"{value:>{col_width}.2f}")

        # Sort by mean descending
        row_data.append((stats["mean"], group_name, " | ".join(row_parts)))

    # Sort by mean descending
    row_data.sort(key=lambda x: x[0], reverse=True)

    for _, _, row in row_data:
        lines.append(row)

    lines.append(separator)

    # Summary
    total_count = sum(len(d) for d in groups.values())
    lines.append(f"Total: {total_count} results")

    # Top group line
    if row_data:
        top_mean, top_name, _ = row_data[0]
        lines.append(f"Top group by mean: {top_name} ({top_mean:.2f})")

    # Report errors
    if errors_encountered:
        lines.append("")
        lines.append("Expression errors:")
        for err in errors_encountered[:5]:  # Limit to first 5
            lines.append(err)
        if len(errors_encountered) > 5:
            lines.append(f"  ... and {len(errors_encountered) - 5} more")

    lines.append("")
    return "\n".join(lines)


def format_numeric_csv(
    groups: dict[str, list[float]],
    custom_stats: list[tuple[str, str]]
) -> str:
    """Format numeric analysis results as CSV."""
    output = io.StringIO()
    writer = csv.writer(output)

    # Standard stat columns
    std_cols = ["Count", "Mean", "Median", "StdDev", "Min", "Max"]
    custom_col_names = [name for name, _ in custom_stats]

    # Header
    header = ["Group"] + std_cols + custom_col_names
    writer.writerow(header)

    # Build rows with stats
    row_data = []
    for group_name, data in groups.items():
        stats = calculate_stats(data)

        row = [
            group_name,
            stats["count"],
            round(stats["mean"], 2),
            round(stats["median"], 2),
            round(stats["stdev"], 2),
            round(stats["min_val"], 1),
            round(stats["max_val"], 1),
        ]

        # Custom stats - empty string on error for valid CSV
        for col_name, expr in custom_stats:
            value, error = evaluate_custom_stat(expr, data, stats)
            if error:
                row.append("")  # Empty string for errors in CSV
                print(f"Warning: {group_name}.{col_name}: {error}", file=sys.stderr)
            else:
                row.append(round(value, 2))

        # Sort by mean descending
        row_data.append((stats["mean"], row))

    # Sort by mean descending
    row_data.sort(key=lambda x: x[0], reverse=True)

    for _, row in row_data:
        writer.writerow(row)

    return output.getvalue()


def main():
    parser = argparse.ArgumentParser(
        description="Analyze Monte Carlo simulation results with flexible aggregation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:

  # Count with net calculation (e.g., Blackjack)
  python scripts/analyze_results.py runs/blackjack \\
    --group-by strategy --count-field result \\
    --net-positive player_wins --net-negative dealer_wins

  # Simple count without net
  python scripts/analyze_results.py runs/ab_test \\
    --group-by variant --count-field converted

  # Numeric analysis with default stats
  python scripts/analyze_results.py runs/sim \\
    --group-by model --numeric-field score

  # Numeric analysis with custom stats
  python scripts/analyze_results.py runs/blackjack \\
    --group-by strategy --numeric-field player_final_total \\
    --custom-stat "Spread=max_val-min_val" \\
    --custom-stat "CV=stdev/mean if mean != 0 else 0"

  # CSV output to stdout
  python scripts/analyze_results.py runs/blackjack \\
    --group-by strategy --count-field result \\
    --output-format csv

  # CSV output to file
  python scripts/analyze_results.py runs/blackjack \\
    --group-by strategy --count-field result \\
    --output-format csv --output results.csv

Custom stat expressions can use:
  data, count, mean, median, stdev, variance, min_val, max_val, sum_val
  sqrt(), log(), exp(), abs(), sorted(), len(), sum(), min(), max()
        """
    )

    parser.add_argument(
        "run_dir",
        type=Path,
        help="Path to run directory containing chunks/"
    )

    parser.add_argument(
        "--group-by",
        required=True,
        help="Field to group results by (e.g., 'strategy', 'model', 'variant')"
    )

    # Aggregation mode (mutually exclusive)
    agg_group = parser.add_mutually_exclusive_group(required=True)
    agg_group.add_argument(
        "--count-field",
        help="Field to count values of (shows percentages per group)"
    )
    agg_group.add_argument(
        "--numeric-field",
        help="Numeric field to analyze (shows mean, median, stdev, min, max)"
    )

    # Net calculation options (only for count mode)
    parser.add_argument(
        "--net-positive",
        action="append",
        default=[],
        metavar="VALUE",
        help="Value(s) to count as +1 for net calculation (can specify multiple)"
    )
    parser.add_argument(
        "--net-negative",
        action="append",
        default=[],
        metavar="VALUE",
        help="Value(s) to count as -1 for net calculation (can specify multiple)"
    )

    # Custom stats (only for numeric mode)
    parser.add_argument(
        "--custom-stat",
        action="append",
        default=[],
        metavar='"Name=Expression"',
        help="Custom statistic as 'Name=Expression' (can specify multiple)"
    )

    parser.add_argument(
        "--title",
        help="Title for the output table (ignored for CSV format)"
    )

    # Output format options
    parser.add_argument(
        "--output-format",
        choices=["table", "csv"],
        default="table",
        help="Output format: 'table' (default) or 'csv'"
    )

    parser.add_argument(
        "--output",
        type=Path,
        metavar="FILE",
        help="Write output to file instead of stdout"
    )

    args = parser.parse_args()

    # Validate arguments
    if (args.net_positive or args.net_negative) and not args.count_field:
        parser.error("--net-positive and --net-negative can only be used with --count-field")

    if args.custom_stat and not args.numeric_field:
        parser.error("--custom-stat can only be used with --numeric-field")

    if args.custom_stat and not ASTEVAL_AVAILABLE:
        print("Warning: asteval not installed. Custom stats will show 'Err' (or empty in CSV).", file=sys.stderr)
        print("Install with: pip install asteval", file=sys.stderr)

    # Validate run directory
    if not args.run_dir.exists():
        print(f"Error: Run directory not found: {args.run_dir}", file=sys.stderr)
        return 1

    # Load results
    print(f"Loading results from {args.run_dir}...", file=sys.stderr)
    results = load_results(args.run_dir)

    if not results:
        print("No results found to analyze.", file=sys.stderr)
        return 1

    print(f"Loaded {len(results)} results", file=sys.stderr)

    # Process based on mode
    if args.count_field:
        groups, all_values, skipped = aggregate_counts(results, args.group_by, args.count_field)

        if skipped > 0:
            print(f"Note: Skipped {skipped} results missing '{args.group_by}' or '{args.count_field}' fields", file=sys.stderr)

        if not groups:
            print(f"No results found with both '{args.group_by}' and '{args.count_field}' fields", file=sys.stderr)
            return 1

        if args.output_format == "csv":
            output = format_count_csv(
                groups, all_values,
                args.net_positive if args.net_positive else None,
                args.net_negative if args.net_negative else None
            )
        else:
            output = format_count_table(
                groups, all_values,
                args.net_positive if args.net_positive else None,
                args.net_negative if args.net_negative else None,
                args.title
            )

    else:  # numeric_field
        groups, skipped = aggregate_numeric(results, args.group_by, args.numeric_field)

        if skipped > 0:
            print(f"Note: Skipped {skipped} results missing '{args.group_by}' or '{args.numeric_field}' fields", file=sys.stderr)

        if not groups:
            print(f"No results found with both '{args.group_by}' and '{args.numeric_field}' fields", file=sys.stderr)
            return 1

        # Parse custom stats
        custom_stats = parse_custom_stats(args.custom_stat)

        if args.output_format == "csv":
            output = format_numeric_csv(groups, custom_stats)
        else:
            output = format_numeric_table(groups, custom_stats, args.title)

    # Write output
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Output written to {args.output}", file=sys.stderr)
    else:
        print(output)

    return 0


if __name__ == "__main__":
    exit(main())
