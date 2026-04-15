"""
Gold Prediction Generator — Sanity test for the evaluation pipeline.

Creates a predictions.jsonl file using the gold (known-correct) patches from
the SWE-bench dataset. Running the official evaluator on these predictions
should yield 100% resolution for all included instances.

If any gold-patch instance fails to resolve, the evaluation environment
has a problem that MUST be fixed before running ContainerClaw.

Usage:
    # Generate gold predictions for 5 random instances
    python create_gold_predictions.py --sample 5 --output gold_predictions.jsonl

    # Generate gold predictions for specific instances
    python create_gold_predictions.py \\
        --instances django__django-11133 django__django-10914 \\
        --output gold_predictions.jsonl

    # Generate gold predictions for ALL 500 instances (for full environment validation)
    python create_gold_predictions.py --all --output gold_predictions_all.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path


def create_gold_predictions(
    dataset_name: str = "princeton-nlp/SWE-bench_Verified",
    instance_ids: list[str] | None = None,
    sample_n: int | None = None,
    output_path: str = "gold_predictions.jsonl",
    model_name: str = "gold_patch_sanity_check",
    seed: int = 42,
) -> int:
    """Generate gold-patch predictions for evaluation sanity testing.

    Args:
        dataset_name: HuggingFace dataset name
        instance_ids: Specific instance IDs to include (None = all)
        sample_n: If set, randomly sample this many instances
        output_path: Output file path for predictions.jsonl
        model_name: model_name_or_path field value
        seed: Random seed for reproducible sampling

    Returns:
        Number of predictions generated
    """
    from instance_loader import load_dataset_cached

    print(f"📦 Loading dataset: {dataset_name}")
    instances = load_dataset_cached(dataset_name)

    if instance_ids:
        # Filter to specific instances
        id_set = set(instance_ids)
        instances = [i for i in instances if i["instance_id"] in id_set]
        missing = id_set - {i["instance_id"] for i in instances}
        if missing:
            print(f"⚠️  Instances not found: {missing}")

    if sample_n and sample_n < len(instances):
        random.seed(seed)
        instances = random.sample(instances, sample_n)

    # Try to sample from diverse repos
    if sample_n:
        repos = set(i.get("repo", "") for i in instances)
        print(f"   Sampled from {len(repos)} unique repos: {sorted(repos)}")

    # Generate predictions using gold patches
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    missing_patch = 0

    with open(out_path, "w") as f:
        for instance in instances:
            gold_patch = instance.get("patch", "")
            if not gold_patch:
                missing_patch += 1
                print(f"  ⚠️  No gold patch for {instance['instance_id']}")
                continue

            prediction = {
                "instance_id": instance["instance_id"],
                "model_patch": gold_patch,
                "model_name_or_path": model_name,
            }
            f.write(json.dumps(prediction) + "\n")
            count += 1

    print(f"\n✅ Generated {count} gold predictions → {out_path}")
    if missing_patch:
        print(f"⚠️  Skipped {missing_patch} instances with no gold patch")

    print(f"\nNext step: Run the official evaluation to verify your environment:")
    print(f"  python evaluate.py \\")
    print(f"      --predictions {out_path} \\")
    print(f"      --run-id gold_sanity_check \\")
    print(f"      --max-workers 1")
    print(f"\n  Expected result: 100% resolution rate for all {count} instances.")

    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate gold-patch predictions for SWE-bench evaluation sanity testing.",
    )
    parser.add_argument(
        "--dataset", default="princeton-nlp/SWE-bench_Verified",
        help="HuggingFace dataset name",
    )

    # Instance selection
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--instances", nargs="+",
        help="Specific instance IDs to include",
    )
    group.add_argument(
        "--sample", type=int,
        help="Randomly sample N instances",
    )
    group.add_argument(
        "--all", action="store_true",
        help="Generate predictions for all instances",
    )

    parser.add_argument(
        "--output", default="gold_predictions.jsonl",
        help="Output file path (default: gold_predictions.jsonl)",
    )
    parser.add_argument(
        "--model-name", default="gold_patch_sanity_check",
        help="Model name field (default: gold_patch_sanity_check)",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for sampling (default: 42)",
    )

    args = parser.parse_args()

    count = create_gold_predictions(
        dataset_name=args.dataset,
        instance_ids=args.instances,
        sample_n=args.sample if not args.all else None,
        output_path=args.output,
        model_name=args.model_name,
        seed=args.seed,
    )

    if count == 0:
        print("❌ No predictions generated.")
        sys.exit(1)
