"""
Agent Trace Archiver — Captures the full ConchShell agent conversation
log for auditing, ablation studies, and debugging false negatives.

For each SWE-bench instance, this module:
    1. Fetches the conversation history from the Bridge API
    2. Saves the full event stream as structured JSON
    3. Records any workspace diff at the time of archival
    4. Extracts summary statistics (turns, tool calls, agents involved)

These traces are critical for:
    - Debugging: Why did the agent fail on this instance?
    - Ablation: Which agent contributed the most?
    - Reproducibility: Full audit trail for reviewers
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def archive_traces(
    session_id: str,
    bridge_url: str,
    instance_id: str,
    output_dir: str,
    workspace_dir: str = "./workspace",
    extra_metadata: dict | None = None,
) -> Path:
    """Dump the full agent conversation and workspace state for one instance.

    Args:
        session_id: The ContainerClaw session ID used for this instance
        bridge_url: URL of the Bridge API (e.g., http://localhost:5001)
        instance_id: SWE-bench instance ID
        output_dir: Root output directory for the run
        workspace_dir: Path to the agent workspace
        extra_metadata: Any additional metadata to record

    Returns:
        Path to the trace archive directory for this instance
    """
    safe_id = instance_id.replace("/", "__")
    archive_dir = Path(output_dir) / "traces" / safe_id
    archive_dir.mkdir(parents=True, exist_ok=True)

    # 1. Fetch conversation history from Bridge
    raw_conversation = _fetch_conversation(bridge_url, session_id)
    # The bridge returns {"status": "ok", "events": [...]} — extract the list
    if isinstance(raw_conversation, dict) and "events" in raw_conversation:
        conversation = raw_conversation
        event_list = raw_conversation["events"]
    elif isinstance(raw_conversation, list):
        conversation = {"events": raw_conversation, "status": "ok"}
        event_list = raw_conversation
    else:
        conversation = {"events": [], "status": "ok"}
        event_list = []

    (archive_dir / "conversation.json").write_text(
        json.dumps(conversation, indent=2, default=str)
    )
    stats = _extract_stats(event_list) if event_list else {"error": "No events fetched"}

    # 2. Save workspace git log (last 20 commits if available)
    git_log = _get_git_log(workspace_dir)
    if git_log:
        (archive_dir / "git_log.txt").write_text(git_log)

    # 3. Save workspace git diff (the agent's changes)
    git_diff = _get_git_diff(workspace_dir)
    if git_diff:
        (archive_dir / "agent_patch.diff").write_text(git_diff)

    # 4. Save metadata + stats
    metadata = {
        "instance_id": instance_id,
        "session_id": session_id,
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "stats": stats,
    }
    if extra_metadata:
        metadata["extra"] = extra_metadata

    (archive_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, default=str)
    )

    event_count = stats.get("total_events", 0)
    print(f"📼 Trace archived: {safe_id} ({event_count} events)")
    return archive_dir


def _fetch_conversation(bridge_url: str, session_id: str) -> list | None:
    """Fetch conversation history from the Bridge API."""
    try:
        import requests

        # Try the history endpoint (gRPC-backed)
        resp = requests.get(
            f"{bridge_url}/history/{session_id}",
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json()

        # Fallback: try the workspace endpoint for basic session info
        resp = requests.get(
            f"{bridge_url}/workspace/{session_id}",
            timeout=10,
        )
        if resp.status_code == 200:
            return [{"type": "workspace_state", "data": resp.json()}]

    except ImportError:
        print("  ⚠️  'requests' not installed — skipping conversation fetch")
    except Exception as e:
        print(f"  ⚠️  Failed to fetch conversation: {e}")

    return None


def _extract_stats(conversation: list) -> dict:
    """Extract summary statistics from a conversation event list."""
    stats = {
        "total_events": 0,
        "agent_messages": 0,
        "tool_calls": 0,
        "tool_results": 0,
        "elections": 0,
        "agents_involved": set(),
    }

    if not isinstance(conversation, list):
        return {"total_events": 0, "note": "Conversation is not a list"}

    for event in conversation:
        if not isinstance(event, dict):
            continue
        stats["total_events"] += 1

        event_type = event.get("type", "")
        actor = event.get("actor_id", "") or event.get("actor", "")
        content = event.get("content", "")

        if actor and actor not in ("Moderator", "Human", "System"):
            stats["agents_involved"].add(actor)

        if event_type == "thought":
            stats["agent_messages"] += 1
            if "Election" in content:
                stats["elections"] += 1
        elif event_type == "tool_call":
            stats["tool_calls"] += 1
        elif event_type == "tool_result":
            stats["tool_results"] += 1

    # Convert set to list for JSON serialization
    stats["agents_involved"] = sorted(stats["agents_involved"])
    return stats


def _get_git_log(workspace_dir: str, max_entries: int = 20) -> str:
    """Get recent git log from the workspace."""
    try:
        result = subprocess.run(
            ["git", "log", f"-{max_entries}", "--oneline", "--no-decorate"],
            capture_output=True, text=True,
            cwd=workspace_dir, timeout=10,
        )
        return result.stdout if result.returncode == 0 else ""
    except Exception:
        return ""


def _get_git_diff(workspace_dir: str) -> str:
    """Get the full git diff from the workspace (staged + unstaged)."""
    try:
        # Stage everything first to capture new files
        subprocess.run(
            ["git", "add", "-A"],
            capture_output=True, cwd=workspace_dir, timeout=30,
        )
        result = subprocess.run(
            ["git", "diff", "--cached", "HEAD"],
            capture_output=True, text=True,
            cwd=workspace_dir, timeout=30,
        )
        return result.stdout if result.returncode == 0 else ""
    except Exception:
        return ""
