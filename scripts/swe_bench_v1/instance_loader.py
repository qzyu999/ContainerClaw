"""
SWE-bench Instance Loader.

Downloads instances from the princeton-nlp/SWE-bench_Lite dataset hosted
on HuggingFace and provides lookup by instance_id.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# Cache the dataset locally to avoid repeated downloads
CACHE_DIR = Path(__file__).parent / ".cache"


def load_dataset_cached(dataset_name: str = "princeton-nlp/SWE-bench_Verified",
                        split: str = "test") -> list[dict]:
    """Load SWE-bench dataset, caching locally as JSON for fast reuse."""
    cache_file = CACHE_DIR / f"{dataset_name.replace('/', '_')}_{split}.json"

    if cache_file.exists():
        print(f"📦 Loading cached dataset from {cache_file}")
        return json.loads(cache_file.read_text())

    print(f"⬇️  Downloading {dataset_name} (split={split}) from HuggingFace...")
    try:
        from datasets import load_dataset
        ds = load_dataset(dataset_name, split=split)
        items = [dict(row) for row in ds]

        # Cache for next time
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(items, indent=2, default=str))
        print(f"✅ Cached {len(items)} instances to {cache_file}")
        return items
    except Exception as e:
        print(f"❌ Failed to download dataset: {e}")
        sys.exit(1)


def load_instance(instance_id: str, dataset_name: str = "princeton-nlp/SWE-bench_Verified") -> dict:
    """Load a single SWE-bench instance by its instance_id.

    Returns a dict with keys:
        - instance_id: str
        - repo: str (e.g. "django/django")
        - base_commit: str
        - problem_statement: str
        - hints_text: str
        - test_patch: str (gold test patch to apply for evaluation)
        - patch: str (gold fix patch — NOT given to agents)
        - version: str
    """
    items = load_dataset_cached(dataset_name)

    for item in items:
        if item.get("instance_id") == instance_id:
            print(f"✅ Found instance: {instance_id}")
            print(f"   Repo: {item.get('repo')}")
            print(f"   Base commit: {item.get('base_commit', 'N/A')[:12]}")
            return item

    print(f"❌ Instance '{instance_id}' not found in dataset.")
    print(f"   Available instances ({len(items)} total):")
    for item in items[:10]:
        print(f"   - {item.get('instance_id')}")
    if len(items) > 10:
        print(f"   ... and {len(items) - 10} more")
    sys.exit(1)


def list_instances(dataset_name: str = "princeton-nlp/SWE-bench_Verified",
                   repo_filter: str | None = None) -> list[dict]:
    """List all available instances, optionally filtered by repo."""
    items = load_dataset_cached(dataset_name)
    if repo_filter:
        items = [i for i in items if repo_filter.lower() in i.get("repo", "").lower()]
    return items


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SWE-bench Instance Loader")
    parser.add_argument("--instance", help="Instance ID to load")
    parser.add_argument("--list", action="store_true", help="List all instances")
    parser.add_argument("--repo", help="Filter instances by repo name")
    parser.add_argument("--dataset", default="princeton-nlp/SWE-bench_Verified")
    args = parser.parse_args()

    if args.list or args.repo:
        instances = list_instances(args.dataset, args.repo)
        from tabulate import tabulate
        rows = [(i["instance_id"], i.get("repo", ""), i.get("version", ""))
                for i in instances[:50]]
        print(tabulate(rows, headers=["Instance ID", "Repo", "Version"]))
        print(f"\n{len(instances)} instances total.")
    elif args.instance:
        instance = load_instance(args.instance, args.dataset)
        print(f"\nProblem Statement:\n{instance.get('problem_statement', 'N/A')[:500]}")
    else:
        parser.print_help()
