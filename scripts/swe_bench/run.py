#!/usr/bin/env python3
"""
SWE-bench Harness — Main CLI Entry Point.

Orchestrates the AGENT HARNESS phase of the benchmark pipeline:
    load instance → setup workspace → boot ContainerClaw →
    submit problem → wait for agents → extract patch →
    save prediction (JSONL)

IMPORTANT: This script does NOT evaluate/grade predictions.
Grading is handled exclusively by the official SWE-bench harness
via `evaluate.py` (Phase 2). This strict separation ensures
results are credible and publishable.

Usage:
    # Single instance
    python run.py --instance django__django-11133 --model-name containerclaw-v1

    # Batch (all of SWE-bench Verified)
    python run.py --batch --model-name containerclaw-v1 --timeout 600

    # Batch with limit
    python run.py --batch --limit 10 --model-name containerclaw-v1

    # After agent harness completes, run official evaluation (Phase 2):
    python evaluate.py --predictions runs/<run_id>/predictions.jsonl --run-id <run_id>
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from instance_loader import load_instance, list_instances
from workspace_setup import setup_workspace, extract_patch
from prediction_writer import (
    save_prediction,
    combine_predictions,
    save_run_manifest,
    finalize_manifest,
)
from trace_archiver import archive_traces

# ── Smart Bootstrap for Local Dev ──
# Automatically resolve PYTHONPATH and config path when running manually from root
ROOT = Path(__file__).resolve().parent.parent.parent
if (ROOT / "agent" / "src").exists():
    sys.path.insert(0, str(ROOT / "agent" / "src"))
    sys.path.insert(0, str(ROOT))
if "CLAW_CONFIG_PATH" not in os.environ and (ROOT / "config.yaml").exists():
    os.environ["CLAW_CONFIG_PATH"] = str(ROOT / "config.yaml")

import config


BRIDGE_URL = os.getenv("BRIDGE_URL", "http://localhost:5001")
COMPOSE_FILE = Path(__file__).resolve().parent.parent.parent / "docker-compose.yml"

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


def _wait_for_reconciler_boot(session_id: str, max_wait: int = 60) -> bool:
    """Poll the SSE stream until the Reconciler publishes its boot message.

    The reconciler emits 'Multi-Agent System Online' once the FlussPublisher,
    ToolExecutor, and agent roster are fully initialized. We must wait for
    this before submitting the real task — otherwise the agents see the task
    arrive before their internal state machine is running, causing them to
    silently produce empty votes and [WAIT] forever.

    Returns True if booted, False on timeout.
    """
    import requests

    print(f"   ⏳ Waiting for agent reconciler to boot (max {max_wait}s)...")
    start = time.time()

    while time.time() - start < max_wait:
        try:
            resp = requests.get(
                f"{BRIDGE_URL}/events/{session_id}",
                stream=True,
                timeout=(5, 10),
            )
            for line in resp.iter_lines(decode_unicode=True):
                if time.time() - start > max_wait:
                    break
                if not line or not line.startswith("data: "):
                    continue
                try:
                    event = json.loads(line[6:])
                    content = event.get("content", "")
                    if "Multi-Agent System Online" in content:
                        elapsed = int(time.time() - start)
                        print(f"   ✅ Reconciler booted ({elapsed}s)")
                        return True
                except json.JSONDecodeError:
                    continue
        except requests.exceptions.ReadTimeout:
            continue
        except requests.exceptions.ConnectionError:
            time.sleep(2)
            continue
        except Exception:
            time.sleep(2)
            continue

    print(f"   ⚠️  Reconciler boot not confirmed within {max_wait}s (proceeding anyway)")
    return False


def submit_task(problem_statement: str) -> str:
    """Submit the problem statement to ContainerClaw via the bridge.
    Returns the generated session_id if successful, or empty string on failure.
    """
    import requests

    # 1. Initialize a new session
    print(f"🔌 Initializing session to boot agent components...")
    try:
        sess_resp = requests.post(f"{BRIDGE_URL}/sessions/new", json={"title": "SWE-Bench Run"}, timeout=60)
        sess_resp.raise_for_status()
        session_id = sess_resp.json().get("session", {}).get("session_id", "")
        if not session_id:
            print("❌ Failed to parse session ID from bridge")
            return ""

        # 2. WAKEUP PING:
        # ContainerClaw lazy-loads the Reconciler agent loop on the first /task call.
        # The first ExecuteTask triggers _init_moderator → asyncio.create_task(reconciler.run())
        # which asynchronously starts the FlussPublisher, replays history, and begins polling.
        # The warmup ping's "Human" message may fail to publish (publisher not ready yet),
        # but crucially it causes the reconciler background task to be scheduled.
        requests.post(f"{BRIDGE_URL}/task", json={
            "prompt": "[SWE-bench Internal Warmup Ping]",
            "session_id": session_id,
        }, timeout=30)

        # 3. Wait for the reconciler to fully boot.
        # Instead of a blind sleep, poll the SSE stream for the boot confirmation.
        # The reconciler publishes "Multi-Agent System Online" once all components
        # (FlussPublisher, ToolExecutor, agents) are initialized and the polling
        # loop is running. Only then is it safe to submit the real task.
        _wait_for_reconciler_boot(session_id, max_wait=60)

    except Exception as e:
        print(f"❌ Failed to reach bridge for session init/warmup: {e}")
        return ""

    print(f"📤 Submitting task ({len(problem_statement)} chars) to session {session_id}...")
    try:
        resp = requests.post(f"{BRIDGE_URL}/task", json={
            "prompt": problem_statement,
            "session_id": session_id,
        }, timeout=30)
        data = resp.json()
        if data.get("status") == "ok":
            print(f"✅ Task submitted successfully")
            return session_id
        else:
            print(f"❌ Task submission failed: {data.get('message', 'unknown error')}")
            return ""
    except Exception as e:
        print(f"❌ Failed to reach bridge: {e}")
        return ""


def wait_for_completion(timeout: int, session_id: str) -> int:
    """Wait for agents to finish by polling the SSE event stream.

    The SSE stream is backed by a gRPC StreamActivity call. It can have
    long gaps (agent doing git operations, LLM inference, etc.), so we
    use a short per-read timeout with reconnection rather than one long
    blocking read.

    Returns:
        Number of election turns observed
    """
    import requests

    print(f"⏳ Waiting for agents to complete (timeout: {timeout}s)...")
    start = time.time()
    turns = 0
    # How long to wait for data between events before reconnecting
    READ_TIMEOUT = 90

    while time.time() - start < timeout:
        try:
            resp = requests.get(
                f"{BRIDGE_URL}/events/{session_id}",
                stream=True,
                timeout=(10, READ_TIMEOUT),
            )

            for line in resp.iter_lines(decode_unicode=True):
                if time.time() - start > timeout:
                    print(f"⏰ Timeout reached ({timeout}s)")
                    return turns

                if not line or not line.startswith("data: "):
                    continue

                try:
                    event = json.loads(line[6:])
                    event_type = event.get("type", "")

                    if event_type == "thought" and "Election" in event.get("content", ""):
                        turns += 1
                        elapsed = int(time.time() - start)
                        print(f"   🗳️  Turn {turns} ({elapsed}s elapsed)")

                    if event_type == "telemetry":
                        content = event.get("content", "")
                        # Print telemetry chunks in green for visibility
                        print(f"\033[92m{content}\033[0m", end="", flush=True)

                    if event_type == "finish":
                        elapsed = int(time.time() - start)
                        print(f"✅ Agents finished after {turns} turns ({elapsed}s)")
                        return turns

                    if event_type == "error":
                        print(f"⚠️  Agent error: {event.get('content', '')}")

                except json.JSONDecodeError:
                    continue

        except requests.exceptions.ReadTimeout:
            elapsed = int(time.time() - start)
            print(f"   ⏳ Still waiting... ({elapsed}s elapsed, reconnecting)")
            continue
        except requests.exceptions.ConnectionError:
            elapsed = int(time.time() - start)
            if time.time() - start > timeout:
                break
            print(f"   🔌 Connection lost, retrying in 5s... ({elapsed}s elapsed)")
            time.sleep(5)
            continue
        except Exception as e:
            print(f"⚠️  SSE stream error: {e}")
            break

    print(f"⏰ Timeout reached ({timeout}s)")
    return turns

def _stop_mlx_server():
    """Kill any running MLX server spawned by run.py."""
    state_dir = PROJECT_ROOT / ".claw_state"
    pid_file = state_dir / "mlx.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 9)
            print(f"🧹 Stopped local MLX server (PID: {pid})")
        except Exception:
            pass
        pid_file.unlink(missing_ok=True)


CLAW_SH = PROJECT_ROOT / "claw.sh"


def _claw(command: str, *extra_args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a claw.sh command. Single source of truth for lifecycle management."""
    cmd = ["bash", str(CLAW_SH), command] + list(extra_args)
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        cwd=str(PROJECT_ROOT), timeout=600,
    )
    if check and result.returncode != 0:
        print(f"❌ claw.sh {command} failed:\n{result.stdout[-500:]}\n{result.stderr[-500:]}")
    return result


def _docker_compose_down():
    """Tear down the ContainerClaw Docker stack via claw.sh."""
    print("🧹 Shutting down ContainerClaw...")
    _claw("down", "--bench", check=False)


def run_single(instance_id: str, args) -> dict:
    """Run a single SWE-bench instance end-to-end.

    This function implements Phase 1 ONLY — it produces a prediction (the
    agent's patch) but does NOT evaluate/grade it. Grading is deferred to
    Phase 2 (the official SWE-bench harness).

    Returns:
        dict with instance_id, model_patch, wall_clock_s, turns, error
    """
    workspace_dir = str(PROJECT_ROOT / "workspace")

    print(f"\n{'='*60}")
    print(f"  🧪 SWE-bench: {instance_id}")
    print(f"{'='*60}\n")

    start_time = time.time()

    # 1. Load instance
    instance = load_instance(instance_id, args.dataset)

    # 2. Setup workspace (Deduplicated — ONLY call this once)
    if not args.skip_setup:
        setup_workspace(instance, workspace_dir, install_deps=args.install_deps)
    else:
        print("⏭️  Skipping workspace setup")

    # 3. Boot ContainerClaw + Run Agent + Extract Patch
    agent_patch = ""
    turns = 0
    error = None

    try:
        if not args.skip_docker:
            # Choice 1: Instructional Approach
            print(f"🚀 Verifying ContainerClaw services at {BRIDGE_URL}...")
            if not wait_for_health(max_wait=30):
                print("\n" + "="*60)
                print("❌ ContainerClaw is NOT running or not responding.")
                print("   Please start the stack in a separate terminal and try again:")
                print("   $ bash claw.sh up --bench")
                print("="*60 + "\n")
                return _make_result(instance_id, "", 0, time.time() - start_time, "Stack not running")
        else:
            print("⏭️  Skipping service verification")

        # 3. Submit task (Workspace is already setup)

        # 4. Submit task
        problem_statement = instance.get("problem_statement", "")
        session_id = submit_task(problem_statement)
        if not session_id:
            error = "Task submission failed"
            return _make_result(instance_id, "", 0, time.time() - start_time, error)

        # 5. Wait for completion
        turns = wait_for_completion(args.timeout, session_id)

        # 6. Extract patch (stages all changes, diffs against HEAD)
        agent_patch = extract_patch(workspace_dir)
        wall_clock = time.time() - start_time

        # 7. Save prediction checkpoint (official JSONL format)
        metadata = {
            "turns": turns,
            "wall_clock_s": round(wall_clock, 1),
            "repo": instance.get("repo", ""),
            "base_commit": instance.get("base_commit", ""),
            "timeout": args.timeout,
        }
        save_prediction(
            instance_id=instance_id,
            model_patch=agent_patch,
            model_name=args.model_name,
            predictions_dir=args.predictions_dir,
            metadata=metadata,
        )

        # 8. Archive agent traces (for auditing and ablation)
        if not args.skip_traces:
            try:
                archive_traces(
                    session_id=session_id,
                    bridge_url=BRIDGE_URL,
                    instance_id=instance_id,
                    output_dir=str(Path(args.predictions_dir).parent),
                    workspace_dir=workspace_dir,
                    extra_metadata=metadata,
                )
            except Exception as e:
                print(f"⚠️  Trace archival failed (non-fatal): {e}")

    finally:
        # 9. ALWAYS clean up Docker (unless skip or keep-alive)
        if not args.skip_docker and not args.keep_alive:
            _docker_compose_down()

    result = _make_result(instance_id, agent_patch, turns,
                          time.time() - start_time, error)
    return result


def _make_result(instance_id: str, patch: str, turns: int,
                 wall_clock: float, error: str | None) -> dict:
    """Build a standardized result dict."""
    return {
        "instance_id": instance_id,
        "patch_lines": len(patch.splitlines()) if patch else 0,
        "turns": turns,
        "wall_clock_s": round(wall_clock, 1),
        "error": error,
    }


def main():
    parser = argparse.ArgumentParser(
        description="SWE-bench Agent Harness for ContainerClaw (Phase 1)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single instance
  python run.py --instance django__django-11133 --model-name containerclaw-v1

  # Batch run (SWE-bench Verified, first 10)
  python run.py --batch --limit 10 --model-name containerclaw-v1

  # Full run (all 500 instances)
  python run.py --batch --model-name containerclaw-v1 --timeout 600

  # After agent harness, run official evaluation (Phase 2):
  python evaluate.py --predictions runs/<run_id>/predictions.jsonl --run-id <run_id>
        """,
    )

    # Instance selection
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--instance", help="Single SWE-bench instance ID")
    group.add_argument("--batch", action="store_true", help="Run all instances in dataset")
    parser.add_argument("--limit", type=int, help="Max instances for batch mode")
    parser.add_argument("--repo", help="Filter batch by repo name")

    # Model identification (REQUIRED for published results)
    parser.add_argument("--model-name", default="containerclaw-v1",
                        help="Model/system identifier for predictions (default: containerclaw-v1)")

    # Configuration
    parser.add_argument("--dataset", default="princeton-nlp/SWE-bench_Verified",
                        help="HuggingFace dataset name (default: SWE-bench_Verified)")
    parser.add_argument("--timeout", type=int, default=600,
                        help="Max seconds per instance for agent execution (default: 600)")
    parser.add_argument("--run-id", default=None,
                        help="Run ID (auto-generated if not provided)")
    parser.add_argument("--install-deps", action="store_true",
                        help="Run pip install -e . in workspace")

    # Output
    parser.add_argument("--predictions-dir", default=None,
                        help="Directory for prediction checkpoints (default: runs/<run_id>/predictions/)")

    # Skip flags
    parser.add_argument("--skip-setup", action="store_true",
                        help="Skip workspace setup (use existing)")
    parser.add_argument("--skip-docker", action="store_true",
                        help="Skip Docker boot (use running instance)")
    parser.add_argument("--skip-traces", action="store_true",
                        help="Skip agent trace archival")
    parser.add_argument("--keep-alive", action="store_true",
                        help="Don't shut down Docker after run")

    # Evaluation (Phase 2)
    parser.add_argument("--auto-evaluate", action="store_true",
                        help="Automatically run official evaluation after batch completes")
    parser.add_argument("--eval-max-workers", type=int, default=1,
                        help="Max workers for official evaluation (default: 1)")

    args = parser.parse_args()

    # Generate run ID if not provided
    if not args.run_id:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        args.run_id = f"run_{timestamp}"

    # Set predictions directory
    if not args.predictions_dir:
        args.predictions_dir = str(PROJECT_ROOT / "runs" / args.run_id / "predictions")

    run_dir = str(Path(args.predictions_dir).parent)

    if args.instance:
        # ── Single Instance Mode ──
        result = run_single(args.instance, args)
        status = "✅ PATCH COLLECTED" if not result.get("error") else "❌ FAILED"
        print(f"\n{'='*60}")
        print(f"  Result: {status}")
        print(f"  Turns: {result.get('turns', 'N/A')}")
        print(f"  Wall clock: {result.get('wall_clock_s', 'N/A')}s")
        print(f"  Patch size: {result.get('patch_lines', 0)} lines")
        if result.get("error"):
            print(f"  Error: {result['error']}")
        # Combine single prediction into JSONL for evaluate.py
        predictions_jsonl = str(Path(run_dir) / "predictions.jsonl")
        combine_predictions(args.predictions_dir, predictions_jsonl)

        print(f"\n  Prediction saved to: {args.predictions_dir}/")
        print(f"  To evaluate, run Phase 2:")
        print(f"    python evaluate.py --predictions {predictions_jsonl} --run-id {args.run_id}")
        print(f"{'='*60}\n")

    elif args.batch:
        # ── Batch Mode ──
        instances = list_instances(args.dataset, args.repo)
        if args.limit:
            instances = instances[:args.limit]

        # Save run manifest
        config_path = str(PROJECT_ROOT / "config.yaml")
        save_run_manifest(
            run_id=args.run_id,
            run_dir=run_dir,
            model_name=args.model_name,
            dataset_name=args.dataset,
            total_instances=len(instances),
            config_path=config_path,
        )

        print(f"\n🏁 Starting batch run: {len(instances)} instances")
        print(f"   Run ID:      {args.run_id}")
        print(f"   Model:       {args.model_name}")
        print(f"   Dataset:     {args.dataset}")
        print(f"   Timeout:     {args.timeout}s per instance")
        print(f"   Predictions: {args.predictions_dir}\n")

        completed = 0
        errors = 0

        for i, inst in enumerate(instances):
            iid = inst["instance_id"]

            # Checkpoint: skip if prediction already exists
            pred_file = Path(args.predictions_dir) / f"{iid.replace('/', '__')}.json"
            if pred_file.exists():
                print(f"⏩ Skipping {iid} (prediction already exists)")
                completed += 1
                continue

            print(f"\n[{i+1}/{len(instances)}] {iid}")
            try:
                result = run_single(iid, args)
                completed += 1
                if result.get("error"):
                    errors += 1
                    # Save a prediction even for errors (empty patch)
                    save_prediction(
                        instance_id=iid,
                        model_patch="",
                        model_name=args.model_name,
                        predictions_dir=args.predictions_dir,
                        metadata={"error": result["error"]},
                    )
            except KeyboardInterrupt:
                print("\n🛑 Batch run interrupted by user. Gracefully exiting...")
                # Ensure Docker is cleaned up
                if not args.skip_docker and not args.keep_alive:
                    _docker_compose_down()
                break
            except Exception as e:
                print(f"❌ Failed {iid}: {e}")
                errors += 1
                # Save error prediction for checkpointing
                save_prediction(
                    instance_id=iid,
                    model_patch="",
                    model_name=args.model_name,
                    predictions_dir=args.predictions_dir,
                    metadata={"error": str(e)},
                )

        # Finalize manifest
        finalize_manifest(run_dir)

        # Combine predictions into final JSONL
        predictions_jsonl = str(Path(run_dir) / "predictions.jsonl")
        total_combined = combine_predictions(args.predictions_dir, predictions_jsonl)

        # Print final summary
        print(f"\n{'='*60}")
        print(f"  📊 Batch Run Summary")
        print(f"{'='*60}")
        print(f"  Run ID:              {args.run_id}")
        print(f"  Instances attempted: {completed}")
        print(f"  Errors:              {errors}")
        print(f"  Predictions file:    {predictions_jsonl}")
        print(f"  Total predictions:   {total_combined}")

        if args.auto_evaluate:
            print(f"\n  🚀 Auto-evaluating with official SWE-bench harness...")
            from evaluate import run_official_evaluation
            run_official_evaluation(
                predictions_path=predictions_jsonl,
                dataset_name=args.dataset,
                run_id=args.run_id,
                max_workers=args.eval_max_workers,
            )
        else:
            print(f"\n  Next step — Run Phase 2 (official evaluation):")
            print(f"    python evaluate.py \\")
            print(f"        --predictions {predictions_jsonl} \\")
            print(f"        --run-id {args.run_id}")

        print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
