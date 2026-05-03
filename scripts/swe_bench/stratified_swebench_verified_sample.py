"""Create a stratified sample from SWE-bench Verified by repository.

Usage:
  python3 notebooks/stratified_swebench_verified_sample.py \
    --dataset-path /path/to/swebench_verified.jsonl \
    --per-repo 3 \
    --seed 42 \
    --output notebooks/swebench_verified_stratified_sample.jsonl

Notes:
- Expects JSONL records containing at least: `instance_id` and `repo`.
- Uses capped-per-repo sampling so each repo contributes up to `--per-repo` items.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def save_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def stratified_sample_by_repo(rows: list[dict], per_repo: int, seed: int) -> list[dict]:
    rng = random.Random(seed)

    by_repo: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        repo = row.get("repo", "unknown")
        by_repo[repo].append(row)

    sampled: list[dict] = []
    for repo in sorted(by_repo.keys()):
        pool = by_repo[repo]
        k = min(per_repo, len(pool))
        sampled.extend(rng.sample(pool, k))

    rng.shuffle(sampled)
    return sampled


def summarize(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[row.get("repo", "unknown")] += 1
    return dict(sorted(counts.items(), key=lambda kv: kv[0]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-path", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--per-repo", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rows = load_jsonl(args.dataset_path)
    sampled = stratified_sample_by_repo(rows, per_repo=args.per_repo, seed=args.seed)
    save_jsonl(args.output, sampled)

    full_counts = summarize(rows)
    sample_counts = summarize(sampled)

    print(f"Loaded: {len(rows)} records")
    print(f"Sampled: {len(sampled)} records")
    print("\nPer-repo counts in sample:")
    for repo, n in sample_counts.items():
        print(f"  {repo}: {n} / {full_counts.get(repo, 0)}")


if __name__ == "__main__":
    main()
