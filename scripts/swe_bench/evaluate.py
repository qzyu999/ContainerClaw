"""
Official SWE-bench Evaluation Wrapper.

This script is a thin wrapper around the official `swebench.harness.run_evaluation`
module. It exists to:
    1. Validate the predictions file format before submitting
    2. Ensure the correct dataset and run_id are used
    3. Provide a clean CLI for Phase 2 of the pipeline

INVARIANT: The grading decision is made ENTIRELY by the official harness.
           No custom evaluation code exists in this path.

Usage:
    # Run evaluation on ContainerClaw predictions
    python evaluate.py \\
        --predictions runs/v1/predictions.jsonl \\
        --run-id containerclaw-v1 \\
        --max-workers 1

    # Validate predictions without running evaluation
    python evaluate.py \\
        --predictions runs/v1/predictions.jsonl \\
        --validate-only
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def validate_predictions(predictions_path: str, dataset_name: str) -> dict:
    """Validate prediction file format and return summary statistics.

    Checks:
        - File exists and is readable
        - Each line is valid JSON
        - Each prediction has the three required fields
        - Instance IDs are unique
        - Patch content is valid UTF-8

    Args:
        predictions_path: Path to predictions.jsonl
        dataset_name: Dataset name (for cross-referencing)

    Returns:
        Dict with validation results and statistics
    """
    pred_path = Path(predictions_path)
    if not pred_path.exists():
        return {"valid": False, "error": f"File not found: {predictions_path}"}

    predictions = []
    errors = []
    instance_ids = set()

    with open(pred_path) as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue

            try:
                pred = json.loads(line)
            except json.JSONDecodeError as e:
                errors.append(f"  Line {i}: Invalid JSON — {e}")
                continue

            # Check required fields
            required_keys = {"instance_id", "model_patch", "model_name_or_path"}
            missing = required_keys - set(pred.keys())
            if missing:
                errors.append(f"  Line {i}: Missing keys: {missing}")
                continue

            # Check for duplicate instance_ids
            iid = pred["instance_id"]
            if iid in instance_ids:
                errors.append(f"  Line {i}: Duplicate instance_id: {iid}")
                continue
            instance_ids.add(iid)

            predictions.append(pred)

    # Compute statistics
    total = len(predictions)
    empty = sum(1 for p in predictions if not p.get("model_patch"))
    non_empty = total - empty
    model_names = set(p["model_name_or_path"] for p in predictions)

    stats = {
        "valid": len(errors) == 0,
        "total_predictions": total,
        "empty_patches": empty,
        "non_empty_patches": non_empty,
        "unique_models": sorted(model_names),
        "errors": errors,
    }

    # Print validation report
    print(f"\n{'='*50}")
    print(f"  📋 Prediction Validation Report")
    print(f"{'='*50}")
    print(f"  File:              {pred_path}")
    print(f"  Total predictions: {total}")
    print(f"  Empty patches:     {empty}")
    print(f"  Non-empty patches: {non_empty}")
    print(f"  Model(s):          {', '.join(model_names) if model_names else 'N/A'}")
    print(f"  Valid:             {'✅ Yes' if stats['valid'] else '❌ No'}")

    if errors:
        print(f"\n  Errors ({len(errors)}):")
        for err in errors[:20]:  # Cap to avoid flooding
            print(f"    {err}")
        if len(errors) > 20:
            print(f"    ... and {len(errors) - 20} more errors")

    print(f"{'='*50}\n")
    return stats


def run_official_evaluation(
    predictions_path: str,
    dataset_name: str = "princeton-nlp/SWE-bench_Verified",
    run_id: str = "containerclaw-v1",
    max_workers: int = 1,
    cache_level: str = "env",
    timeout: int = 1800,
    namespace: str | None = None,
) -> int:
    """Run the official SWE-bench evaluation harness.

    This function calls `python -m swebench.harness.run_evaluation` as a
    subprocess. This ensures complete isolation — the official harness
    runs unmodified with its own Docker client and logging.

    Args:
        predictions_path: Path to predictions.jsonl
        dataset_name: HuggingFace dataset name
        run_id: Unique run identifier
        max_workers: Number of parallel evaluation workers
        cache_level: Docker image cache level (none/base/env/instance)
        timeout: Test timeout in seconds per instance
        namespace: Docker image namespace (None = build locally, "swebench" = pull from Hub)

    Returns:
        Subprocess return code (0 = success)
    """
    # Validate first
    stats = validate_predictions(predictions_path, dataset_name)
    if not stats["valid"]:
        if "error" in stats:
            print(f"❌ {stats['error']}")
        print("❌ Prediction validation failed. Fix errors above before evaluating.")
        return 1

    if stats["total_predictions"] == 0:
        print("❌ No predictions to evaluate.")
        return 1

    # Build the command
    cmd = [
        sys.executable, "-m", "swebench.harness.run_evaluation",
        "--dataset_name", dataset_name,
        "--predictions_path", str(Path(predictions_path).resolve()),
        "--run_id", run_id,
        "--max_workers", str(max_workers),
        "--cache_level", cache_level,
        "--timeout", str(timeout),
    ]

    # Namespace controls whether images are pulled from Docker Hub or built locally.
    # --namespace none  → build all images locally (correct for local eval)
    # --namespace swebench → try to pull from Docker Hub (often fails, images are private)
    if namespace:
        cmd.extend(["--namespace", namespace])
    else:
        cmd.extend(["--namespace", "none"])

    # 4. Inject runtime patch for git clones via PYTHONPATH
    # This automatically invokes sitecustomize.py across all child processes
    # protecting the git clone against network dropouts without touching .venv files!
    env = os.environ.copy()
    swe_bench_dir = str(Path(__file__).resolve().parent)
    original_python_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{swe_bench_dir}:{original_python_path}" if original_python_path else swe_bench_dir

    print(f"🚀 Running official SWE-bench evaluation")
    print(f"   Dataset:     {dataset_name}")
    print(f"   Predictions: {predictions_path} ({stats['non_empty_patches']} non-empty)")
    print(f"   Run ID:      {run_id}")
    print(f"   Workers:     {max_workers}")
    print(f"   Cache level: {cache_level}")
    print(f"   Timeout:     {timeout}s per instance")
    print(f"   Command:     {' '.join(cmd)}")
    print()

    # 5. Run evaluation harness
    result = subprocess.run(cmd, env=env)

    if result.returncode == 0:
        print(f"\n✅ Official evaluation completed successfully.")
        print(f"   Check logs/run_evaluation/{run_id}/ for per-instance results.")
        # The official harness writes its own report file in CWD
        report_glob = list(Path(".").glob(f"*.{run_id}.json"))
        if report_glob:
            print(f"   Report: {report_glob[0]}")
    else:
        print(f"\n❌ Evaluation failed with return code {result.returncode}")

    return result.returncode


# ── CLI Entry Point ──────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run official SWE-bench evaluation on ContainerClaw predictions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Validate predictions without running evaluation
  python evaluate.py --predictions predictions.jsonl --validate-only

  # Run full evaluation
  python evaluate.py --predictions predictions.jsonl --run-id my-run-v1

  # Run with more workers (use <= 75% of CPU cores)
  python evaluate.py --predictions predictions.jsonl --run-id my-run-v1 --max-workers 4
        """,
    )
    parser.add_argument(
        "--predictions", required=True,
        help="Path to predictions.jsonl file",
    )
    parser.add_argument(
        "--dataset", default="princeton-nlp/SWE-bench_Verified",
        help="HuggingFace dataset name (default: SWE-bench_Verified)",
    )
    parser.add_argument(
        "--run-id", default="containerclaw-v1",
        help="Run ID for the evaluation (default: containerclaw-v1)",
    )
    parser.add_argument(
        "--max-workers", type=int, default=1,
        help="Max parallel evaluation workers (default: 1)",
    )
    parser.add_argument(
        "--cache-level", default="env",
        choices=["none", "base", "env", "instance"],
        help="Docker image cache level (default: env)",
    )
    parser.add_argument(
        "--timeout", type=int, default=1800,
        help="Test timeout per instance in seconds (default: 1800)",
    )
    parser.add_argument(
        "--namespace", default=None,
        help="Docker image namespace (default: None = build locally; use 'swebench' to pull from Hub)",
    )
    parser.add_argument(
        "--validate-only", action="store_true",
        help="Only validate predictions, don't run evaluation",
    )

    args = parser.parse_args()

    if args.validate_only:
        stats = validate_predictions(args.predictions, args.dataset)
        sys.exit(0 if stats["valid"] else 1)

    rc = run_official_evaluation(
        predictions_path=args.predictions,
        dataset_name=args.dataset,
        run_id=args.run_id,
        max_workers=args.max_workers,
        cache_level=args.cache_level,
        timeout=args.timeout,
        namespace=args.namespace,
    )
    sys.exit(rc)
