"""
Prediction Writer — Interface to the official SWE-bench evaluation contract.

Produces per-instance prediction checkpoints and combines them into
the final predictions.jsonl file required by the official harness.

The three required fields per prediction are:
    - instance_id: str
    - model_patch: str (unified diff)
    - model_name_or_path: str

Checkpoint files store additional metadata (timing, config hash, etc.)
that is stripped when combining into the final JSONL.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def get_containerclaw_git_sha() -> str:
    """Return the current ContainerClaw git commit SHA, or 'unknown'."""
    project_root = Path(__file__).resolve().parent.parent.parent
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=str(project_root), timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def get_environment_snapshot() -> dict:
    """Capture the current environment for reproducibility."""
    return {
        "python_version": sys.version,
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "containerclaw_git_sha": get_containerclaw_git_sha(),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


def save_prediction(
    instance_id: str,
    model_patch: str,
    model_name: str,
    predictions_dir: str,
    metadata: dict | None = None,
) -> Path:
    """Save a single prediction as a checkpoint file.

    The checkpoint contains the three official fields plus any extra metadata
    (wall clock, turns, config hash, etc.) for auditing. The metadata is
    stripped when combining into the final predictions.jsonl.

    Args:
        instance_id: SWE-bench instance ID (e.g., "django__django-11133")
        model_patch: The agent's unified diff patch (may be empty string)
        model_name: Identifier for the model/system (e.g., "containerclaw-v1")
        predictions_dir: Directory to save checkpoint files
        metadata: Optional dict of extra metadata to store alongside

    Returns:
        Path to the saved checkpoint file
    """
    out = Path(predictions_dir)
    out.mkdir(parents=True, exist_ok=True)

    checkpoint = {
        # === Official fields (sent to evaluator) ===
        "instance_id": instance_id,
        "model_patch": model_patch,
        "model_name_or_path": model_name,
        # === Audit metadata (NOT sent to evaluator) ===
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "patch_lines": len(model_patch.splitlines()) if model_patch else 0,
        "patch_bytes": len(model_patch.encode("utf-8")) if model_patch else 0,
    }

    if metadata:
        checkpoint["_metadata"] = metadata

    checkpoint_file = out / f"{instance_id.replace('/', '__')}.json"
    checkpoint_file.write_text(json.dumps(checkpoint, indent=2, default=str))
    print(f"💾 Prediction saved: {checkpoint_file.name} ({checkpoint['patch_lines']} lines)")
    return checkpoint_file


def combine_predictions(predictions_dir: str, output_path: str) -> int:
    """Combine per-instance checkpoints into a single predictions.jsonl.

    Only writes the three official fields required by the SWE-bench harness.
    Skips any non-JSON files or malformed checkpoints.

    Args:
        predictions_dir: Directory containing per-instance checkpoint JSONs
        output_path: Path for the combined predictions.jsonl

    Returns:
        Number of predictions successfully combined
    """
    pred_dir = Path(predictions_dir)
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    empty_count = 0
    errors = []

    with open(out_path, "w") as f:
        for checkpoint_file in sorted(pred_dir.glob("*.json")):
            try:
                checkpoint = json.loads(checkpoint_file.read_text())
            except (json.JSONDecodeError, OSError) as e:
                errors.append(f"  ⚠️  {checkpoint_file.name}: {e}")
                continue

            # Validate required fields
            if "instance_id" not in checkpoint:
                errors.append(f"  ⚠️  {checkpoint_file.name}: missing instance_id")
                continue

            model_patch = checkpoint.get("model_patch", "")
            if not model_patch:
                empty_count += 1

            # Write ONLY the three official fields
            prediction = {
                "instance_id": checkpoint["instance_id"],
                "model_patch": model_patch,
                "model_name_or_path": checkpoint.get("model_name_or_path", "unknown"),
            }
            f.write(json.dumps(prediction) + "\n")
            count += 1

    print(f"\n{'='*50}")
    print(f"  📊 Predictions Combined")
    print(f"{'='*50}")
    print(f"  Total predictions: {count}")
    print(f"  Empty patches:     {empty_count}")
    print(f"  Non-empty patches: {count - empty_count}")
    print(f"  Output file:       {out_path}")

    if errors:
        print(f"  Errors:            {len(errors)}")
        for err in errors:
            print(err)

    print(f"{'='*50}\n")
    return count


def save_run_manifest(
    run_id: str,
    run_dir: str,
    model_name: str,
    dataset_name: str,
    total_instances: int,
    config_path: str | None = None,
) -> Path:
    """Save a run-level manifest capturing all reproducibility metadata.

    This manifest is the single source of truth for what was run, when, and
    with what configuration. It should be archived alongside the predictions
    and evaluation results.

    Args:
        run_id: Unique identifier for this run
        run_dir: Root directory for this run's artifacts
        model_name: Model identifier string
        dataset_name: HuggingFace dataset name
        total_instances: Number of instances in the batch
        config_path: Path to config.yaml (contents hashed, not stored)

    Returns:
        Path to the manifest file
    """
    import hashlib

    out = Path(run_dir)
    out.mkdir(parents=True, exist_ok=True)

    config_hash = "N/A"
    if config_path and Path(config_path).exists():
        config_content = Path(config_path).read_bytes()
        config_hash = hashlib.sha256(config_content).hexdigest()[:16]

    manifest = {
        "run_id": run_id,
        "model_name": model_name,
        "dataset_name": dataset_name,
        "total_instances": total_instances,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,  # Updated when run finishes
        "config_yaml_hash": config_hash,
        "environment": get_environment_snapshot(),
    }

    manifest_file = out / "manifest.json"
    manifest_file.write_text(json.dumps(manifest, indent=2))
    print(f"📋 Run manifest saved: {manifest_file}")
    return manifest_file


def finalize_manifest(run_dir: str) -> None:
    """Update the manifest with completion timestamp."""
    manifest_file = Path(run_dir) / "manifest.json"
    if manifest_file.exists():
        manifest = json.loads(manifest_file.read_text())
        manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
        manifest_file.write_text(json.dumps(manifest, indent=2))


# ── CLI Entry Point ──────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Combine per-instance prediction checkpoints into predictions.jsonl"
    )
    parser.add_argument(
        "--combine", required=True,
        help="Directory containing per-instance checkpoint JSON files",
    )
    parser.add_argument(
        "--output", required=True,
        help="Output path for the combined predictions.jsonl",
    )
    args = parser.parse_args()

    count = combine_predictions(args.combine, args.output)
    if count == 0:
        print("❌ No predictions found. Check the checkpoint directory.")
        sys.exit(1)
    print(f"✅ Combined {count} predictions into {args.output}")
