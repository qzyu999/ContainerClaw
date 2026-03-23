#!/usr/bin/env python3
"""
SWE-bench Harness — Main CLI Entry Point.

Orchestrates the full benchmark flow:
    load instance → setup workspace → boot ContainerClaw →
    submit problem → wait for agents → extract patch →
    evaluate → report results

Usage:
    # Single instance
    python run.py --instance django__django-16379 --timeout 300

    # Batch (all of SWE-bench Lite)
    python run.py --dataset swebench_lite --timeout 600 --output results/

    # Skip Docker (for testing the evaluation pipeline only)
    python run.py --instance django__django-16379 --skip-docker --skip-eval
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from instance_loader import load_instance, list_instances
from workspace_setup import setup_workspace, extract_patch
from evaluator import evaluate_patch
from results import save_result, generate_summary


BRIDGE_URL = os.getenv("BRIDGE_URL", "http://localhost:5001")
COMPOSE_FILE = Path(__file__).resolve().parent.parent.parent / "docker-compose.yml"
COMPOSE_OVERRIDE = Path(__file__).resolve().parent.parent.parent / "docker-compose.swebench.yml"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def wait_for_health(max_wait: int = 120) -> bool:
    """Poll the bridge until it responds, or timeout."""
    import requests

    print(f"⏳ Waiting for ContainerClaw to boot (max {max_wait}s)...")
    start = time.time()
    while time.time() - start < max_wait:
        try:
            resp = requests.get(f"{BRIDGE_URL}/workspace/swe-bench", timeout=5)
            if resp.status_code == 200:
                print(f"✅ ContainerClaw is ready ({int(time.time() - start)}s)")
                return True
        except Exception:
            pass
        time.sleep(5)

    print(f"❌ ContainerClaw did not start within {max_wait}s")
    return False


def submit_task(problem_statement: str, session_id: str = "swe-bench") -> bool:
    """Submit the problem statement to ContainerClaw via the bridge."""
    import requests

    print(f"📤 Submitting task ({len(problem_statement)} chars)...")
    try:
        resp = requests.post(f"{BRIDGE_URL}/task", json={
            "prompt": problem_statement,
            "session_id": session_id,
        }, timeout=30)
        data = resp.json()
        if data.get("status") == "ok":
            print(f"✅ Task submitted successfully")
            return True
        else:
            print(f"❌ Task submission failed: {data.get('message', 'unknown error')}")
            return False
    except Exception as e:
        print(f"❌ Failed to reach bridge: {e}")
        return False


def wait_for_completion(timeout: int, session_id: str = "swe-bench") -> int:
    """Wait for agents to finish (poll SSE stream for 'finish' events).

    Returns:
        Number of election turns observed
    """
    import requests

    print(f"⏳ Waiting for agents to complete (timeout: {timeout}s)...")
    start = time.time()
    turns = 0

    try:
        # Poll SSE stream
        resp = requests.get(
            f"{BRIDGE_URL}/events/{session_id}",
            stream=True, timeout=timeout + 10,
        )

        for line in resp.iter_lines(decode_unicode=True):
            if time.time() - start > timeout:
                print(f"⏰ Timeout reached ({timeout}s)")
                break

            if not line or not line.startswith("data: "):
                continue

            try:
                event = json.loads(line[6:])
                event_type = event.get("type", "")

                if event_type == "thought" and "Election" in event.get("content", ""):
                    turns += 1
                    elapsed = int(time.time() - start)
                    print(f"   🗳️  Turn {turns} ({elapsed}s elapsed)")

                if event_type == "finish":
                    elapsed = int(time.time() - start)
                    print(f"✅ Agents finished after {turns} turns ({elapsed}s)")
                    return turns

            except json.JSONDecodeError:
                continue

    except Exception as e:
        print(f"⚠️  SSE stream error: {e}")

    return turns


def run_single(instance_id: str, args) -> dict:
    """Run a single SWE-bench instance end-to-end."""
    workspace_dir = str(PROJECT_ROOT / "workspace")

    print(f"\n{'='*60}")
    print(f"  🧪 SWE-bench: {instance_id}")
    print(f"{'='*60}\n")

    start_time = time.time()

    # 1. Load instance
    instance = load_instance(instance_id, args.dataset)

    # 2. Setup workspace
    if not args.skip_setup:
        setup_workspace(instance, workspace_dir, install_deps=args.install_deps)
    else:
        print("⏭️  Skipping workspace setup")

    # 3. Boot ContainerClaw
    if not args.skip_docker:
        compose_cmd = [
            "docker", "compose",
            "-f", str(COMPOSE_FILE),
            "-f", str(COMPOSE_OVERRIDE),
            "up", "--build", "-d",
        ]
        print(f"🚀 Booting ContainerClaw...")
        result = subprocess.run(compose_cmd, capture_output=True, text=True,
                                cwd=str(PROJECT_ROOT), timeout=300)
        if result.returncode != 0:
            print(f"❌ Docker Compose failed: {result.stderr[:500]}")
            return {"instance_id": instance_id, "error": "Docker failed to start"}

        if not wait_for_health(max_wait=600):
            return {"instance_id": instance_id, "error": "Health check timeout"}
    else:
        print("⏭️  Skipping Docker (using running instance)")

    # 4. Submit task
    problem_statement = instance.get("problem_statement", "")
    if not submit_task(problem_statement):
        return {"instance_id": instance_id, "error": "Task submission failed"}

    # 5. Wait for completion
    turns = wait_for_completion(args.timeout)

    # 6. Extract patch
    agent_patch = extract_patch(workspace_dir)
    wall_clock = time.time() - start_time

    # 7. Evaluate
    if not args.skip_eval:
        eval_result = evaluate_patch(instance, agent_patch, workspace_dir)
    else:
        print("⏭️  Skipping evaluation")
        eval_result = {
            "instance_id": instance_id,
            "resolved": None,
            "tests_passed": 0,
            "tests_total": 0,
            "agent_patch_size": len(agent_patch.splitlines()),
        }

    # Add timing metadata
    eval_result["turns"] = turns
    eval_result["wall_clock_s"] = round(wall_clock, 1)
    eval_result["patch_size_lines"] = len(agent_patch.splitlines())

    # 8. Save result
    save_result(instance_id, eval_result, args.output)

    # 9. Cleanup Docker (unless skip)
    if not args.skip_docker and not args.keep_alive:
        print("🧹 Shutting down ContainerClaw...")
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE),
             "-f", str(COMPOSE_OVERRIDE), "down", "-v"],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT),
        )

    return eval_result


def main():
    parser = argparse.ArgumentParser(
        description="SWE-bench Benchmark Harness for ContainerClaw",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single instance
  python run.py --instance django__django-16379 --timeout 300

  # Batch run (SWE-bench Lite, first 10)
  python run.py --batch --limit 10 --timeout 600 --output results/

  # Debug: skip Docker and evaluation
  python run.py --instance django__django-16379 --skip-docker --skip-eval
        """,
    )

    # Instance selection
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--instance", help="Single SWE-bench instance ID")
    group.add_argument("--batch", action="store_true", help="Run all instances in dataset")
    parser.add_argument("--limit", type=int, help="Max instances for batch mode")
    parser.add_argument("--repo", help="Filter batch by repo name")

    # Configuration
    parser.add_argument("--dataset", default="princeton-nlp/SWE-bench_Lite",
                        help="HuggingFace dataset name")
    parser.add_argument("--timeout", type=int, default=600,
                        help="Max seconds per instance for agent execution")
    parser.add_argument("--output", default="results/",
                        help="Output directory for results")
    parser.add_argument("--install-deps", action="store_true",
                        help="Run pip install -e . in workspace")

    # Skip flags
    parser.add_argument("--skip-setup", action="store_true",
                        help="Skip workspace setup (use existing)")
    parser.add_argument("--skip-docker", action="store_true",
                        help="Skip Docker boot (use running instance)")
    parser.add_argument("--skip-eval", action="store_true",
                        help="Skip evaluation (just extract patch)")
    parser.add_argument("--keep-alive", action="store_true",
                        help="Don't shut down Docker after run")

    args = parser.parse_args()

    if args.instance:
        # Single instance mode
        result = run_single(args.instance, args)
        status = "✅ RESOLVED" if result.get("resolved") else "❌ NOT RESOLVED"
        print(f"\n{'='*60}")
        print(f"  Result: {status}")
        print(f"  Turns: {result.get('turns', 'N/A')}")
        print(f"  Wall clock: {result.get('wall_clock_s', 'N/A')}s")
        print(f"  Patch size: {result.get('patch_size_lines', 0)} lines")
        print(f"{'='*60}\n")

    elif args.batch:
        # Batch mode
        instances = list_instances(args.dataset, args.repo)
        if args.limit:
            instances = instances[:args.limit]

        print(f"\n🏁 Starting batch run: {len(instances)} instances")
        print(f"   Dataset: {args.dataset}")
        print(f"   Timeout: {args.timeout}s per instance")
        print(f"   Output: {args.output}\n")

        for i, inst in enumerate(instances):
            iid = inst["instance_id"]
            print(f"\n[{i+1}/{len(instances)}] {iid}")
            try:
                run_single(iid, args)
            except Exception as e:
                print(f"❌ Failed: {e}")
                save_result(iid, {"instance_id": iid, "error": str(e)}, args.output)

        # Generate summary
        generate_summary(args.output)


if __name__ == "__main__":
    main()
