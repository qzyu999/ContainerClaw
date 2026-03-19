"""
SWE-bench Results Aggregator.

Saves per-instance results to JSON and produces summary tables in CSV format.
"""

import csv
import json
import os
from datetime import datetime
from pathlib import Path


def save_result(instance_id: str, result: dict, output_dir: str = "./results") -> Path:
    """Save a single instance result to JSON.

    Args:
        instance_id: SWE-bench instance ID
        result: Evaluation result dict from evaluator.py
        output_dir: Directory to save results

    Returns:
        Path to the saved JSON file
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    result_file = out / f"{instance_id.replace('/', '_')}.json"
    result["saved_at"] = datetime.utcnow().isoformat() + "Z"
    result_file.write_text(json.dumps(result, indent=2, default=str))
    print(f"💾 Result saved: {result_file}")
    return result_file


def load_results(output_dir: str = "./results") -> list[dict]:
    """Load all result JSON files from the output directory."""
    out = Path(output_dir)
    results = []
    for f in sorted(out.glob("*.json")):
        if f.name == "summary.json":
            continue
        try:
            results.append(json.loads(f.read_text()))
        except Exception as e:
            print(f"⚠️  Failed to load {f}: {e}")
    return results


def generate_summary(output_dir: str = "./results") -> dict:
    """Generate summary statistics from all results and save as JSON + CSV.

    Returns:
        Summary dict with aggregate metrics
    """
    results = load_results(output_dir)
    out = Path(output_dir)

    if not results:
        print("⚠️  No results found.")
        return {}

    total = len(results)
    resolved = sum(1 for r in results if r.get("resolved", False))
    partial = sum(1 for r in results if r.get("partial_resolve", False))
    errored = sum(1 for r in results if r.get("error"))

    avg_patch_size = (
        sum(r.get("agent_patch_size", 0) for r in results) / total
        if total > 0 else 0
    )
    avg_turns = (
        sum(r.get("turns", 0) for r in results) / total
        if total > 0 else 0
    )
    avg_wall_clock = (
        sum(r.get("wall_clock_s", 0) for r in results) / total
        if total > 0 else 0
    )

    summary = {
        "total_instances": total,
        "resolved": resolved,
        "resolve_rate": round(resolved / total * 100, 1) if total > 0 else 0,
        "partial_resolve": partial,
        "partial_resolve_rate": round(partial / total * 100, 1) if total > 0 else 0,
        "errored": errored,
        "avg_patch_size_lines": round(avg_patch_size, 1),
        "avg_turns": round(avg_turns, 1),
        "avg_wall_clock_s": round(avg_wall_clock, 1),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }

    # Save summary JSON
    summary_file = out / "summary.json"
    summary_file.write_text(json.dumps(summary, indent=2))
    print(f"\n📊 Summary saved: {summary_file}")

    # Save per-instance CSV
    csv_file = out / "summary.csv"
    with open(csv_file, "w", newline="") as f:
        fieldnames = [
            "instance_id", "repo", "resolved", "partial_resolve",
            "tests_passed", "tests_failed", "tests_total",
            "agent_patch_size", "turns", "wall_clock_s", "error",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            writer.writerow(r)
    print(f"📄 CSV saved: {csv_file}")

    # Print summary table
    print(f"\n{'='*50}")
    print(f"  SWE-bench Results Summary")
    print(f"{'='*50}")
    print(f"  Total instances:     {total}")
    print(f"  Resolved:            {resolved} ({summary['resolve_rate']}%)")
    print(f"  Partial resolve:     {partial} ({summary['partial_resolve_rate']}%)")
    print(f"  Errored:             {errored}")
    print(f"  Avg patch size:      {summary['avg_patch_size_lines']} lines")
    print(f"  Avg turns:           {summary['avg_turns']}")
    print(f"  Avg wall clock:      {summary['avg_wall_clock_s']}s")
    print(f"{'='*50}\n")

    return summary
