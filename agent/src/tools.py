"""
ConchShell Tool Infrastructure for ContainerClaw agents.

Each agent receives a scoped ToolSet based on their role. The ToolDispatcher
routes tool calls to the correct implementation and enforces rate limits.
"""

import asyncio
import json
import subprocess
import time
import pyarrow as pa
import config
from schemas import BOARD_EVENTS_SCHEMA, BOARD_COMMENT_EVENTS_SCHEMA, DEFAULT_BUCKET_COUNT
import ast
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Callable, Awaitable


# ---------------------------------------------------------------------------
# Core Abstractions
# ---------------------------------------------------------------------------

@dataclass
class ToolResult:
    """Structured result from a tool execution."""
    success: bool
    output: str
    error: str | None = None
    artifacts: list[str] = field(default_factory=list)


class Tool:
    """Base class for all ConchShell tools."""
    name: str = ""
    description: str = ""

    def get_schema(self) -> dict:
        """Return JSON Schema for this tool's parameters.

        Subclasses override to declare their expected input shape.
        The schema is sent to Gemini as a function declaration.
        """
        return {"type": "object", "properties": {}}

    async def execute(self, agent_id: str, params: dict, 
                      publish_fn: Optional[Callable[[bytes], Awaitable[None]]] = None) -> ToolResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# DiffTool — Git diff wrapper
# ---------------------------------------------------------------------------

class DiffTool(Tool):
    name = "diff"
    description = f"Show the git diff for a file in {config.WORKSPACE_ROOT} (vs HEAD)."

    def __init__(self, session_shell=None):
        super().__init__()
        self.session_shell = session_shell

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": f"File path relative to {config.WORKSPACE_ROOT} to diff."
                }
            },
            "required": ["path"]
        }

    async def execute(self, agent_id: str, params: dict,
                      publish_fn: Optional[Callable[[bytes], Awaitable[None]]] = None) -> ToolResult:
        rel_path = params.get("path", "")
        command = f"git diff HEAD -- {rel_path}"
        
        if self.session_shell:
            # Route through the persistent shell to share environment/state
            return await self.session_shell.execute(agent_id, {"command": command}, publish_fn=publish_fn)
            
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["git", "diff", "HEAD", "--", rel_path],
                capture_output=True, text=True, cwd=config.WORKSPACE_ROOT, timeout=config.TOOL_TIMEOUTS.diff,
            )
            diff_text = result.stdout if result.returncode == 0 else ""
            if not diff_text:
                return ToolResult(success=True, output="No differences found.")
            return ToolResult(success=True, output=diff_text[:config.TOOLS.output_limit_chars])
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


# ---------------------------------------------------------------------------
# TestRunnerTool — Code execution & validation
# ---------------------------------------------------------------------------

class TestRunnerTool(Tool):
    name = "test_runner"
    description = (
        f"Run test suites in {config.WORKSPACE_ROOT}. Supports pytest (default) and "
        "generic commands. Use runner='pytest' with args like 'tests/' "
        "or runner='generic' with the full command."
    )

    def __init__(self, session_shell=None):
        super().__init__()
        self.session_shell = session_shell

    SUPPORTED_RUNNERS = {
        "pytest": "python -m pytest {args} --tb=short -q",
        "generic": "{args}",
    }
    TIMEOUT = config.TOOL_TIMEOUTS.test_runner

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "runner": {
                    "type": "string",
                    "description": "Test runner to use: 'pytest' or 'generic'.",
                    "enum": ["pytest", "generic"],
                },
                "args": {
                    "type": "string",
                    "description": "Arguments to pass to the test runner.",
                }
            },
            "required": ["args"]
        }

    async def execute(self, agent_id: str, params: dict,
                      publish_fn: Optional[Callable[[bytes], Awaitable[None]]] = None) -> ToolResult:
        runner = params.get("runner", "pytest")
        args = params.get("args", "")

        if runner not in self.SUPPORTED_RUNNERS:
            return ToolResult(success=False, output="", error=f"Unknown runner: {runner}")

        command = self.SUPPORTED_RUNNERS[runner].format(args=args)

        if self.session_shell:
            # Route through the persistent shell to share environment (virtualenvs, PATH, etc)
            return await self.session_shell.execute(agent_id, {"command": command, "timeout": self.TIMEOUT}, publish_fn=publish_fn)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=config.WORKSPACE_ROOT,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.TIMEOUT
            )
            return ToolResult(
                success=proc.returncode == 0,
                output=stdout.decode(errors="replace")[:config.TOOLS.output_limit_chars],
                error=stderr.decode(errors="replace")[:config.TOOLS.output_limit_chars // 2] if proc.returncode != 0 else None,
            )
        except asyncio.TimeoutError:
            proc.kill()
            return ToolResult(
                success=False, output="",
                error=f"Test run timed out after {self.TIMEOUT}s.",
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


# ---------------------------------------------------------------------------
# ProjectBoard + BoardTool — Shared project management
# ---------------------------------------------------------------------------

class ProjectBoard:
    """Project board backed by Fluss board_events + board_comment_events tables.
    
    W-2: Replaces the old JSON file persistence. Mutations are stored
    as append-only events in Fluss. State is rebuilt by replaying the
    event log on startup (crash recovery).
    
    Supports structured comment threads on board items with anti-sprawl
    guardrails (comment budgets, auto-summarization, task creation throttle).
    All limits sourced from config.BOARD_COMMENTS.
    
    Falls back to JSON file if board_table is not available.
    """

    def __init__(self, session_id: str, board_table=None, board_comment_table=None):
        self.session_id = session_id
        self.board_table = board_table
        self.board_comment_table = board_comment_table
        self.board_dir = Path(config.WORKSPACE_ROOT) / ".claw_state"
        self.board_path = self.board_dir / f"{session_id}_board.json"  # Fallback only
        self.items: list[dict] = []
        self.comments: dict[str, list[dict]] = {}  # item_id → list of comment dicts
        self._writer = None
        self._comment_writer = None
        self._pa_schema = None

        if self.board_table:
            self._pa_schema = BOARD_EVENTS_SCHEMA
            self._writer = self.board_table.new_append().create_writer()
        else:
            self._load()

        if self.board_comment_table:
            self._comment_writer = self.board_comment_table.new_append().create_writer()

    async def initialize(self):
        """Async initialization: Replay board_events + comment_events logs."""
        if not self.board_table:
            return
            
        try:
            # Replay board events
            scanner = await self.board_table.new_scan().create_record_batch_log_scanner()
            scanner.subscribe_buckets(
                {b: 0 for b in range(DEFAULT_BUCKET_COUNT)}
            )
            
            while True:
                batches = await scanner._async_poll_batches(500)
                if not batches:
                    break
                # Unwrap Fluss RecordBatch → pyarrow RecordBatch
                batches = [b.batch for b in batches]
                
                for poll in batches:
                    for i in range(poll.num_rows):
                        # Filter by session_id
                        if poll.column("session_id")[i].as_py() != self.session_id:
                            continue
                        action = poll.column("action")[i].as_py()
                        if action == "create":
                            self.items.append({
                                "id": poll.column("item_id")[i].as_py(),
                                "type": poll.column("item_type")[i].as_py(),
                                "title": poll.column("title")[i].as_py(),
                                "description": poll.column("description")[i].as_py(),
                                "status": poll.column("status")[i].as_py(),
                                "assigned_to": poll.column("assigned_to")[i].as_py() or None,
                                "created_at": poll.column("ts")[i].as_py() / 1000,
                                "last_reason": "",
                            })
                        elif action == "update_status":
                            item_id = poll.column("item_id")[i].as_py()
                            new_status = poll.column("status")[i].as_py()
                            reason = poll.column("reason")[i].as_py() if "reason" in poll.schema.names else ""
                            for item in self.items:
                                if item["id"] == item_id:
                                    item["status"] = new_status
                                    if reason:
                                        item["last_reason"] = reason
                                    break
                        elif action == "delete":
                            item_id = poll.column("item_id")[i].as_py()
                            self.items = [item for item in self.items if item["id"] != item_id]
            print(f"📋 [ProjectBoard] Replayed {len(self.items)} board items from Fluss.")
        except Exception as e:
            print(f"⚠️ [ProjectBoard] Fluss replay failed, falling back to JSON: {e}")
            self._load()

        # Replay comment events
        if not self.board_comment_table:
            return
        try:
            scanner = await self.board_comment_table.new_scan().create_record_batch_log_scanner()
            scanner.subscribe_buckets(
                {b: 0 for b in range(DEFAULT_BUCKET_COUNT)}
            )
            while True:
                batches = await scanner._async_poll_batches(500)
                if not batches:
                    break
                batches = [b.batch for b in batches]
                for poll in batches:
                    for i in range(poll.num_rows):
                        if poll.column("session_id")[i].as_py() != self.session_id:
                            continue
                        action = poll.column("action")[i].as_py()
                        item_id = poll.column("item_id")[i].as_py()
                        comment_id = poll.column("comment_id")[i].as_py()

                        if action == "add" or action == "summarize":
                            comment = {
                                "comment_id": comment_id,
                                "item_id": item_id,
                                "author": poll.column("author")[i].as_py(),
                                "category": poll.column("category")[i].as_py(),
                                "content": poll.column("content")[i].as_py(),
                                "ts": poll.column("ts")[i].as_py(),
                                "archived": poll.column("archived")[i].as_py(),
                            }
                            self.comments.setdefault(item_id, []).append(comment)
                        elif action == "archive":
                            # Mark existing comment as archived
                            for c in self.comments.get(item_id, []):
                                if c["comment_id"] == comment_id:
                                    c["archived"] = True
                                    break
            total = sum(len(v) for v in self.comments.values())
            print(f"💬 [ProjectBoard] Replayed {total} comments from Fluss.")
        except Exception as e:
            print(f"⚠️ [ProjectBoard] Comment replay failed: {e}")

    async def _publish_event(self, action, item_id, item_type="", title="",
                             description="", status="", assigned_to="",
                             actor="Moderator", reason=""):
        """Write a board mutation event to Fluss (non-blocking)."""
        if not self._writer:
            return
        try:
            batch = pa.RecordBatch.from_arrays([
                pa.array([self.session_id], type=pa.string()),
                pa.array([int(time.time() * 1000)], type=pa.int64()),
                pa.array([action], type=pa.string()),
                pa.array([item_id], type=pa.string()),
                pa.array([item_type], type=pa.string()),
                pa.array([title], type=pa.string()),
                pa.array([description], type=pa.string()),
                pa.array([status], type=pa.string()),
                pa.array([assigned_to or ""], type=pa.string()),
                pa.array([actor], type=pa.string()),
                pa.array([reason or ""], type=pa.string()),
            ], schema=self._pa_schema)
            self._writer.write_arrow_batch(batch)
            if hasattr(self._writer, "flush"):
                await self._writer.flush()
            print(f"📋 [ProjectBoard] Published {action} event for {item_id}")
        except Exception as e:
            print(f"⚠️ [ProjectBoard] Failed to write to Fluss: {e}")

    async def _publish_comment_event(self, action: str, comment: dict):
        """Write a comment mutation event to Fluss."""
        if not self._comment_writer:
            return
        try:
            batch = pa.RecordBatch.from_arrays([
                pa.array([self.session_id], type=pa.string()),
                pa.array([comment["ts"]], type=pa.int64()),
                pa.array([comment["item_id"]], type=pa.string()),
                pa.array([comment["comment_id"]], type=pa.string()),
                pa.array([action], type=pa.string()),
                pa.array([comment["author"]], type=pa.string()),
                pa.array([comment["category"]], type=pa.string()),
                pa.array([comment["content"]], type=pa.string()),
                pa.array([comment["archived"]], type=pa.bool_()),
            ], schema=BOARD_COMMENT_EVENTS_SCHEMA)
            self._comment_writer.write_arrow_batch(batch)
            if hasattr(self._comment_writer, "flush"):
                await self._comment_writer.flush()
        except Exception as e:
            print(f"⚠️ [ProjectBoard] Failed to write comment to Fluss: {e}")

    def _load(self):
        """Fallback: load from JSON file."""
        try:
            if self.board_path.exists():
                self.items = json.loads(self.board_path.read_text())
        except Exception:
            self.items = []

    def _save(self):
        """Fallback: atomic save to JSON file (only used when Fluss unavailable)."""
        self.board_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.board_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(self.items, indent=2))
        tmp_path.replace(self.board_path)

    async def create_item(
        self, item_type: str, title: str,
        description: str = "", assigned_to: str | None = None,
        actor: str = "Moderator",
    ) -> dict | None:
        # Anti-sprawl: task creation throttle
        bc = config.BOARD_COMMENTS
        window = bc.item_creation_window_s
        now = time.time()
        recent_creates = sum(
            1 for item in self.items
            if item["created_at"] > (now - window)
        )
        if recent_creates >= bc.max_items_per_cycle:
            return None  # Throttled

        item = {
            "id": f"{item_type[:1].upper()}-{len(self.items) + 1:03d}",
            "type": item_type,
            "title": title,
            "description": description,
            "status": "todo",
            "assigned_to": assigned_to,
            "created_at": now,
            "last_reason": "",
        }
        self.items.append(item)
        if self.board_table:
            await self._publish_event(
                "create", item["id"], item_type, title,
                description, "todo", assigned_to or "", actor,
            )
        else:
            self._save()
        return item

    async def update_status(self, item_id: str, status: str,
                            actor: str = "Moderator",
                            reason: str = "") -> dict | None:
        for item in self.items:
            if item["id"] == item_id:
                item["status"] = status
                item["last_reason"] = reason
                if self.board_table:
                    await self._publish_event(
                        "update_status", item_id, status=status,
                        actor=actor, reason=reason,
                    )
                else:
                    self._save()
                # Auto-post a status_change comment
                if reason:
                    await self.add_comment(
                        item_id=item_id,
                        author=actor,
                        category="status_change",
                        content=f"→ {status}: {reason}",
                    )
                return item
        return None

    async def delete_item(self, item_id: str, actor: str = "Moderator") -> bool:
        initial_count = len(self.items)
        self.items = [item for item in self.items if item["id"] != item_id]
        if len(self.items) < initial_count:
            if self.board_table:
                await self._publish_event("delete", item_id, actor=actor)
            else:
                self._save()
            return True
        return False

    # ── Comment Thread Methods ──────────────────────────────────────

    async def add_comment(self, item_id: str, author: str,
                          category: str, content: str) -> dict | None:
        """Add a structured comment to a board item.

        Returns the comment dict on success, None if item not found or throttled.
        Enforces comment budget via auto-summarization.
        """
        # Verify item exists
        if not any(item["id"] == item_id for item in self.items):
            return None

        bc = config.BOARD_COMMENTS
        max_chars = bc.comment_max_chars

        # Count active (non-archived) comments
        active_comments = [
            c for c in self.comments.get(item_id, [])
            if not c.get("archived", False)
        ]

        if len(active_comments) >= bc.max_comments_per_item:
            await self._summarize_oldest(item_id, count=bc.summarize_count)

        comment = {
            "comment_id": str(uuid.uuid4()),
            "item_id": item_id,
            "author": author,
            "category": category,
            "content": content[:max_chars],
            "ts": int(time.time() * 1000),
            "archived": False,
        }
        self.comments.setdefault(item_id, []).append(comment)
        await self._publish_comment_event("add", comment)
        return comment

    async def _summarize_oldest(self, item_id: str, count: int | None = None):
        """Compress the oldest N active comments into a single summary comment."""
        bc = config.BOARD_COMMENTS
        count = count or bc.summarize_count
        trunc = bc.summary_line_truncate
        max_chars = bc.comment_max_chars

        active = [c for c in self.comments.get(item_id, []) if not c.get("archived", False)]
        to_summarize = active[:count]

        if not to_summarize:
            return

        summary_lines = []
        for c in to_summarize:
            summary_lines.append(f"[{c['category']}] {c['author']}: {c['content'][:trunc]}")

        summary_content = "📦 Archived summary:\n" + "\n".join(summary_lines)

        # Archive the originals
        for c in to_summarize:
            c["archived"] = True
            await self._publish_comment_event("archive", c)

        # Create the summary comment
        summary = {
            "comment_id": str(uuid.uuid4()),
            "item_id": item_id,
            "author": "System",
            "category": "summary",
            "content": summary_content[:max_chars],
            "ts": int(time.time() * 1000),
            "archived": False,
        }
        self.comments[item_id].append(summary)
        await self._publish_comment_event("summarize", summary)
        print(f"📦 [ProjectBoard] Summarized {len(to_summarize)} comments for {item_id}")

    def get_active_comments(self, item_id: str) -> list[dict]:
        """Return non-archived comments for an item, chronologically."""
        return [
            c for c in self.comments.get(item_id, [])
            if not c.get("archived", False)
        ]

    def get_item_detail(self, item_id: str) -> str | None:
        """Return formatted detail view of a board item with its comment thread."""
        item = next((i for i in self.items if i["id"] == item_id), None)
        if not item:
            return None

        icon = {"todo": "⬜", "in_progress": "🟡", "done": "✅"}.get(item["status"], "❓")
        assignee = f" → {item['assigned_to']}" if item.get("assigned_to") else ""
        lines = [
            f"{icon} [{item['id']}] {item['title']}{assignee}",
            f"   Type: {item['type']} | Status: {item['status']}",
        ]
        if item.get("description"):
            lines.append(f"   Description: {item['description']}")
        if item.get("last_reason"):
            lines.append(f"   Last reason: {item['last_reason']}")

        active = self.get_active_comments(item_id)
        if active:
            lines.append(f"\n   💬 {len(active)} comments:")
            cat_icons = {
                "analysis": "🔍", "finding": "💡", "conclusion": "✅",
                "blocker": "🚧", "status_change": "🔄", "summary": "📦",
            }
            for c in active:
                ci = cat_icons.get(c["category"], "💬")
                ago = self._relative_time(c["ts"])
                lines.append(f"   {ci} [{c['category']}] {c['author']} · {ago}")
                lines.append(f"      \"{c['content']}\"")
        else:
            lines.append("\n   No comments yet.")

        return "\n".join(lines)

    def prune_stale(self):
        """Mark items as stale if they haven't been updated in N cycles."""
        bc = config.BOARD_COMMENTS
        threshold = bc.stale_threshold_cycles
        cycle_dur = bc.stale_cycle_duration_s
        now = time.time()
        for item in self.items:
            if item["status"] == "in_progress":
                comments = self.comments.get(item["id"], [])
                last_activity = max(
                    [c["ts"] / 1000 for c in comments if not c.get("archived", False)],
                    default=item["created_at"]
                )
                item["_stale"] = (now - last_activity) > (threshold * cycle_dur)
            else:
                item["_stale"] = False

    def get_board_summary(self) -> str:
        if not self.items:
            return "Board is empty. No items have been created yet."
        lines = []
        for item in self.items:
            icon = {"todo": "⬜", "in_progress": "🟡", "done": "✅"}.get(
                item["status"], "❓"
            )
            assignee = f" → {item['assigned_to']}" if item.get("assigned_to") else ""
            stale = " ⚠️STALE" if item.get("_stale") else ""
            line = f"{icon} [{item['id']}] {item['title']}{assignee}{stale}"

            # Comment summary
            active = self.get_active_comments(item["id"])
            if active:
                last = active[-1]
                cat_icons = {
                    "analysis": "🔍", "finding": "💡", "conclusion": "✅",
                    "blocker": "🚧", "status_change": "🔄", "summary": "📦",
                }
                ci = cat_icons.get(last["category"], "💬")
                ago = self._relative_time(last["ts"])
                preview = last["content"][:60]
                line += f"\n   💬 {len(active)} · Last: {ci} \"{preview}\" ({ago})"

            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _relative_time(ts_ms: int) -> str:
        """Convert ms timestamp to human-readable relative time."""
        diff = time.time() - (ts_ms / 1000)
        if diff < 60:
            return "just now"
        elif diff < 3600:
            return f"{int(diff / 60)} min ago"
        elif diff < 86400:
            return f"{int(diff / 3600)} hr ago"
        return f"{int(diff / 86400)} day ago"


class BoardTool(Tool):
    name = "board"
    description = (
        "Interact with the shared project board. Use this to track work items "
        "and document your reasoning as structured comments.\n\n"
        "GUIDELINES:\n"
        "- Use 'comment' to document significant findings, not routine updates.\n"
        "- Each comment must add NEW information. Do not repeat what's already there.\n"
        "- Use categories: 'analysis' (investigating), 'finding' (discovered fact), "
        "'conclusion' (final determination), 'blocker' (need help).\n"
        "- When updating status, always provide a 'reason'.\n"
        "- Before creating a new item, check if one already covers your intent.\n"
        "- The board has a comment budget per item. Be concise.\n\n"
        "Actions: 'create' (type, title, description, assigned_to), "
        "'update' (item_id, status, reason), 'delete' (item_id), 'list', "
        "'comment' (item_id, category, content), 'view' (item_id — shows full thread)."
    )

    def __init__(self, board: ProjectBoard, write_access: bool = True):
        self.board = board
        self.write_access = write_access

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Board action: 'create', 'update', 'delete', 'list', 'comment', or 'view'.",
                    "enum": ["create", "update", "delete", "list", "comment", "view"],
                },
                "type": {
                    "type": "string",
                    "description": "Item type for 'create': 'epic', 'story', or 'task'.",
                },
                "title": {
                    "type": "string",
                    "description": "Title for 'create'.",
                },
                "description": {
                    "type": "string",
                    "description": "Description for 'create'.",
                },
                "assigned_to": {
                    "type": "string",
                    "description": "Agent name to assign for 'create'.",
                },
                "item_id": {
                    "type": "string",
                    "description": "Item ID (e.g. 'T-001') for 'update', 'delete', 'comment', or 'view'.",
                },
                "status": {
                    "type": "string",
                    "description": "New status for 'update': 'todo', 'in_progress', or 'done'.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why this status change is being made (required for 'update').",
                },
                "category": {
                    "type": "string",
                    "description": "Comment category for 'comment': 'analysis', 'finding', 'conclusion', or 'blocker'.",
                    "enum": ["analysis", "finding", "conclusion", "blocker"],
                },
                "content": {
                    "type": "string",
                    "description": "Comment body for 'comment'. Be concise and actionable.",
                },
            },
            "required": ["action"]
        }

    async def execute(self, agent_id: str, params: dict,
                      publish_fn: Optional[Callable[[bytes], Awaitable[None]]] = None) -> ToolResult:
        action = params.get("action", "list")

        if action == "list":
            self.board.prune_stale()
            return ToolResult(success=True, output=self.board.get_board_summary())

        if action == "view":
            item_id = params.get("item_id", "")
            if not item_id:
                return ToolResult(success=False, output="", error="'view' requires 'item_id'.")
            detail = self.board.get_item_detail(item_id)
            if detail:
                return ToolResult(success=True, output=detail)
            return ToolResult(success=False, output="", error=f"Item {item_id} not found.")

        if not self.write_access:
            return ToolResult(success=False, output="", error="Read-only board access.")

        if action == "create":
            title = params.get("title", "Untitled")
            item = await self.board.create_item(
                item_type=params.get("type", "task"),
                title=title,
                description=params.get("description", ""),
                assigned_to=params.get("assigned_to"),
                actor=agent_id,
            )
            if item is None:
                return ToolResult(
                    success=False, output="",
                    error="Task creation throttled — too many items created recently. "
                          "Wait before creating more.",
                )
            return ToolResult(
                success=True,
                output=f"Created {item['id']}: {item['title']}",
            )

        if action == "update":
            item_id = params.get("item_id", "")
            status = params.get("status", "")
            reason = params.get("reason", "")
            if not item_id or not status:
                return ToolResult(
                    success=False, output="",
                    error="'update' requires 'item_id' and 'status'.",
                )
            if not reason:
                return ToolResult(
                    success=False, output="",
                    error="'update' requires a 'reason' explaining why the status is changing.",
                )
            item = await self.board.update_status(
                item_id, status, actor=agent_id, reason=reason,
            )
            if item:
                return ToolResult(
                    success=True,
                    output=f"Updated {item['id']} → {item['status']} (reason: {reason})",
                )
            return ToolResult(
                success=False, output="",
                error=f"Item {item_id} not found.",
            )

        if action == "comment":
            item_id = params.get("item_id", "")
            category = params.get("category", "analysis")
            content = params.get("content", "")
            if not item_id or not content:
                return ToolResult(
                    success=False, output="",
                    error="'comment' requires 'item_id' and 'content'.",
                )
            result = await self.board.add_comment(
                item_id=item_id,
                author=agent_id,
                category=category,
                content=content,
            )
            if result:
                return ToolResult(
                    success=True,
                    output=f"Comment added to {item_id} [{category}].",
                )
            return ToolResult(
                success=False, output="",
                error=f"Item {item_id} not found or comment budget exceeded.",
            )

        if action == "delete":
            item_id = params.get("item_id", "")
            if not item_id:
                return ToolResult(
                    success=False, output="",
                    error="'delete' requires 'item_id'.",
                )
            
            # Verify status is 'done' before deletion
            target_item = next((item for item in self.board.items if item["id"] == item_id), None)
            if not target_item:
                return ToolResult(success=False, output="", error=f"Item {item_id} not found.")
            
            if target_item["status"] != "done":
                return ToolResult(
                    success=False, output="",
                    error=f"Item {item_id} has status '{target_item['status']}'. Only 'done' items can be deleted."
                )
            
            if await self.board.delete_item(item_id, actor=agent_id):
                return ToolResult(success=True, output=f"Deleted {item_id} from board.")
            return ToolResult(success=False, output="", error=f"Failed to delete {item_id}.")

        return ToolResult(success=False, output="", error=f"Unknown action: {action}")


# ---------------------------------------------------------------------------
# SWE-bench Advanced Tools
# ---------------------------------------------------------------------------

class CreateFileTool(Tool):
    name = "create_file"
    description = f"Create a new file in {config.WORKSPACE_ROOT}. Fails if the file already exists."

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": f"File path relative to {config.WORKSPACE_ROOT}."
                },
                "content": {
                    "type": "string",
                    "description": "The initial content of the new file."
                }
            },
            "required": ["path", "content"]
        }

    async def execute(self, agent_id: str, params: dict,
                      publish_fn: Optional[Callable[[bytes], Awaitable[None]]] = None) -> ToolResult:
        path = Path(config.WORKSPACE_ROOT) / params.get("path", "")
        if not path.resolve().is_relative_to(Path(config.WORKSPACE_ROOT)):
            return ToolResult(success=False, output="", error="Path traversal denied.")
        if path.exists():
            return ToolResult(success=False, output="", error=f"File already exists: {params.get('path')}. Use surgical_edit to modify it.")
        return await asyncio.to_thread(self._create_file, params, path)

    def _create_file(self, params: dict, path: Path) -> ToolResult:
        """File creation in worker thread — never blocks the event loop."""
        content = params.get("content", "")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return ToolResult(
                success=True,
                output=f"Successfully created {params.get('path')} ({len(content)} bytes).",
                artifacts=[str(path)]
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class SurgicalEditTool(Tool):
    name = "surgical_edit"
    description = "Edits a file by replacing a specific block of text with a new block."

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": f"File path relative to {config.WORKSPACE_ROOT}."},
                "old_str": {"type": "string", "description": "The exact string block to replace."},
                "new_str": {"type": "string", "description": "The new string block to insert."}
            },
            "required": ["path", "old_str", "new_str"]
        }

    async def execute(self, agent_id: str, params: dict,
                      publish_fn: Optional[Callable[[bytes], Awaitable[None]]] = None) -> ToolResult:
        path = Path(config.WORKSPACE_ROOT) / params.get("path", "")
        if not path.resolve().is_relative_to(Path(config.WORKSPACE_ROOT)):
            return ToolResult(success=False, output="", error="Path traversal denied.")
        if not path.exists():
            return ToolResult(success=False, output="", error=f"File not found: {path}")

        old_str = params.get("old_str", "")
        if not old_str:
            return ToolResult(success=False, output="", error="old_str cannot be empty.")

        return await asyncio.to_thread(self._perform_edit, params, path)

    def _perform_edit(self, params: dict, path: Path) -> ToolResult:
        """File I/O in worker thread — never blocks the event loop."""
        old_str = params.get("old_str", "")
        new_str = params.get("new_str", "")

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = path.read_text(encoding="latin-1")
            except Exception as e:
                return ToolResult(success=False, output="", error=f"Failed to read file with utf-8 or latin-1: {e}")
        except Exception as e:
            return ToolResult(success=False, output="", error=f"Failed to read file: {e}")

        # Detect original line ending to prevent Line-Ending Clobber
        newline = "\r\n" if "\r\n" in content else "\n"

        # Normalize line endings to avoid Whitespace Traps
        content_norm = content.replace("\r\n", "\n")
        old_str_norm = old_str.replace("\r\n", "\n")
        
        # Normalize new_str to prevent Frankenstein mixed line endings
        new_str_norm = new_str.replace("\r\n", "\n")

        count = content_norm.count(old_str_norm)
        if count == 0:
            return ToolResult(success=False, output="", error="old_str not found in the file. Please provide more context or check exact spelling.")
        if count > 1:
            return ToolResult(success=False, output="", error="old_str appears multiple times. Please provide more context to make it unique.")
        
        try:
            # Replace on the normalized content to enforce exact matches
            new_content = content_norm.replace(old_str_norm, new_str_norm)
            if newline == "\r\n":
                new_content = new_content.replace("\n", "\r\n")
            path.write_text(new_content, encoding="utf-8", newline="")
            return ToolResult(success=True, output=f"Successfully replaced 1 occurrence in {path.relative_to(config.WORKSPACE_ROOT)}", artifacts=[str(path)])
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class AdvancedReadTool(Tool):
    name = "advanced_read"
    description = "Reads a specific window of lines from a file with prepended line numbers."

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": f"File path relative to {config.WORKSPACE_ROOT}."},
                "start_line": {"type": "integer", "description": "1-indexed start line."},
                "end_line": {"type": "integer", "description": "1-indexed end line (inclusive)."}
            },
            "required": ["path", "start_line", "end_line"]
        }

    async def execute(self, agent_id: str, params: dict,
                      publish_fn: Optional[Callable[[bytes], Awaitable[None]]] = None) -> ToolResult:
        path = Path(config.WORKSPACE_ROOT) / params.get("path", "")
        if not path.resolve().is_relative_to(Path(config.WORKSPACE_ROOT)):
            return ToolResult(success=False, output="", error="Path traversal denied.")
        if not path.exists():
            return ToolResult(success=False, output="", error=f"File not found: {path}")

        start_line = params.get("start_line", 1)
        end_line = params.get("end_line", 1)
        if start_line < 1 or end_line < start_line:
            return ToolResult(success=False, output="", error="Invalid line range.")

        return await asyncio.to_thread(self._read_lines, path, start_line, end_line)

    def _read_lines(self, path: Path, start_line: int, end_line: int) -> ToolResult:
        """File read in worker thread — never blocks the event loop."""
        try:
            lines = path.read_text(errors="replace").splitlines()
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))
            
        extracted = []
        for i in range(start_line - 1, min(end_line, len(lines))):
            extracted.append(f"{i+1}: {lines[i]}")
        
        return ToolResult(success=True, output="\n".join(extracted))


class RepoMapTool(Tool):
    name = "repo_map"
    description = "Generates a Table of Contents of the repository using AST (file paths, classes, functions)."

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {},
            "required": []
        }

    async def execute(self, agent_id: str, params: dict,
                      publish_fn: Optional[Callable[[bytes], Awaitable[None]]] = None) -> ToolResult:
        return await asyncio.to_thread(self._build_map)

    def _build_map(self) -> ToolResult:
        """Runs entirely in a worker thread — never touches the event loop."""
        base_dir = Path(config.WORKSPACE_ROOT)
        output_lines = []
        parsed_files = 0
        max_files = config.TOOLS.repo_map_limit
        
        try:
            for root, dirs, files in os.walk(base_dir):
                # skip hidden and unwanted dirs (Performance Wall fix)
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('venv', '__pycache__', 'node_modules', 'tests', 'docs')]
                for file in files:
                    if parsed_files >= max_files:
                        output_lines.append(f"\n... Reached max_files limit ({max_files}). Stopping map generation.")
                        return ToolResult(success=True, output="\n".join(output_lines))
                        
                    if not file.endswith(".py"):
                        continue
                    file_path = Path(root) / file
                    parsed_files += 1
                    try:
                        content = file_path.read_text(errors="ignore")
                        tree = ast.parse(content)
                        rel_path = file_path.relative_to(base_dir)
                        
                        class MapVisitor(ast.NodeVisitor):
                            def __init__(self):
                                self.lines = []
                                self.indent = "  "
                            def visit_ClassDef(self, node):
                                self.lines.append(f"{self.indent}class {node.name}:")
                                old_indent = self.indent
                                self.indent += "  "
                                self.generic_visit(node)
                                self.indent = old_indent
                            def visit_FunctionDef(self, node):
                                self.lines.append(f"{self.indent}def {node.name}(...):")
                            def visit_AsyncFunctionDef(self, node):
                                self.lines.append(f"{self.indent}async def {node.name}(...):")
                                
                        visitor = MapVisitor()
                        visitor.visit(tree)
                        if visitor.lines:
                            output_lines.append(f"\n{rel_path}:")
                            output_lines.extend(visitor.lines)
                    except Exception:
                        pass # Ignore AST parse errors for robust mapping
            return ToolResult(success=True, output="\n".join(output_lines) if output_lines else "No Python files with classes/functions found.")
        except Exception as e:
            return ToolResult(success=False, output="", error=f"Repo map failed: {e}")


class StructuredSearchTool(Tool):
    name = "structured_search"
    description = "A wrapper for grep with result limits preventing 'grep bombs'."

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The term/regex to search for."},
                "include_glob": {"type": "string", "description": "Optional glob like '*.py'."},
                "page": {"type": "integer", "description": "1-indexed page number (50 results/page)."}
            },
            "required": ["query"]
        }

    async def execute(self, agent_id: str, params: dict,
                      publish_fn: Optional[Callable[[bytes], Awaitable[None]]] = None) -> ToolResult:
        query = params.get("query", "")
        if not query:
            return ToolResult(success=False, output="", error="Query cannot be empty.")
        
        include_glob = params.get("include_glob")
        page = params.get("page", 1)
        limit = config.SEARCH_LIMITS.results_per_page
        
        # Capping the max initial search results to prevent inefficiency at scale
        cmd = ["grep", "-rnI", "-m", str(config.SEARCH_LIMITS.max_total_matches)]
        if include_glob:
            cmd.extend(["--include", include_glob])
        cmd.append(query)
        cmd.append(".")
        
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=config.WORKSPACE_ROOT
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=config.TOOL_TIMEOUTS.search)
            if proc.returncode not in (0, 1):
                return ToolResult(success=False, output="", error=f"Grep failed with code {proc.returncode}")
            
            lines = stdout.decode(errors="replace").splitlines()
            if not lines:
                return ToolResult(success=True, output="No matches found.")
            
            start_idx = (page - 1) * limit
            end_idx = start_idx + limit
            page_lines = lines[start_idx:end_idx]
            
            output = "\n".join(page_lines)
            if len(lines) > end_idx:
                output += f"\n\n... Total matches: {len(lines)}. Call again with page={page+1} to see more."
                
            return ToolResult(success=True, output=output)
        except asyncio.TimeoutError:
            proc.kill()
            return ToolResult(success=False, output="", error="Grep timed out.")
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class LinterTool(Tool):
    name = "linter"
    description = "Quickly checks a python file for syntax or indentation errors using py_compile."

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": f"File path relative to {config.WORKSPACE_ROOT} to lint."}
            },
            "required": ["path"]
        }

    async def execute(self, agent_id: str, params: dict,
                      publish_fn: Optional[Callable[[bytes], Awaitable[None]]] = None) -> ToolResult:
        rel_path = params.get("path", "")
        path = Path(config.WORKSPACE_ROOT) / rel_path
        if not path.resolve().is_relative_to(Path(config.WORKSPACE_ROOT)):
            return ToolResult(success=False, output="", error="Path traversal denied.")
            
        try:
            proc = await asyncio.create_subprocess_exec(
                "python", "-m", "py_compile", str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=config.WORKSPACE_ROOT
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=config.TOOL_TIMEOUTS.linter)
            if proc.returncode == 0:
                return ToolResult(success=True, output=f"Syntax OK: {rel_path}")
            else:
                return ToolResult(success=False, output=stdout.decode(errors="replace"), error=stderr.decode(errors="replace"))
        except asyncio.TimeoutError:
            proc.kill()
            return ToolResult(success=False, output="", error="Linter timed out.")
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class SessionShellTool(Tool):
    name = "session_shell"
    description = "A persistent shell tool maintaining state (cd, export) across calls."
    
    def __init__(self, sandbox_manager):
        super().__init__()
        self.sandbox_manager = sandbox_manager
        self.sessions: dict[str, Any] = {} # In remote mode, this might track container state or just be empty

    def cleanup(self):
        # In Container/Sidecar mode, cleanup is handled by the orchestrator
        # or container removal.
        self.sessions.clear()
    
    DEFAULT_TIMEOUT = config.TOOL_TIMEOUTS.shell

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute."},
                "timeout": {"type": "integer", "description": f"Optional timeout in seconds (default {config.TOOL_TIMEOUTS.shell})."}
            },
            "required": ["command"]
        }

    async def execute(self, agent_id: str, params: dict,
                      publish_fn: Optional[Callable[[bytes], Awaitable[None]]] = None) -> ToolResult:
        command = params.get("command", "")
        if not command:
            return ToolResult(success=False, output="", error="No command provided.")

        # Logic: in Remote/Sidecar mode, state is maintained by the sidecar itself
        # if we keep the same container ID. SandboxManager handles the routing.
        
        async def wrapped_publish(chunk: bytes):
            if publish_fn:
                await publish_fn(chunk)

        exit_code, output = await self.sandbox_manager.execute(
            command, agent_id, wrapped_publish
        )
        
        return ToolResult(
            success=(exit_code == 0),
            output=output[:config.TOOLS.output_limit_chars] if output else f"Command exited with code {exit_code}. No output.",
            error=f"Exit code: {exit_code}" if exit_code != 0 else None,
        )

# ---------------------------------------------------------------------------
# DelegateTool — Spawn parallel subagents
# ---------------------------------------------------------------------------

class DelegateTool(Tool):
    """Spawn a parallel subagent to work on a specific subtask.

    The subagent works independently with its own context and tools.
    Results appear in the main stream as they complete (non-blocking).
    """
    name = "delegate"
    description = (
        "Spawn a parallel subagent to work on a specific subtask. "
        "The subagent works independently with its own tools and context. "
        "Results appear in the main stream as they complete. "
        "Use for tasks that can run in parallel (research, file edits, tests)."
    )

    def __init__(self, subagent_manager=None, available_tools=None):
        super().__init__()
        self.subagent_manager = subagent_manager  # Set after creation
        self.available_tools = available_tools or []

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Clear description of the subtask to delegate.",
                },
                "persona": {
                    "type": "string",
                    "description": (
                        "Persona/role for the subagent. "
                        "Defaults to 'General-purpose software engineer'."
                    ),
                },
                "timeout_s": {
                    "type": "integer",
                    "description": "Max seconds before timeout (default 120).",
                },
                "tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional list of tool names to grant. "
                        "If omitted, all standard tools are available."
                    ),
                },
            },
            "required": ["task"],
        }

    async def execute(self, agent_id: str, params: dict) -> ToolResult:
        if not self.subagent_manager:
            return ToolResult(
                success=False, output="",
                error="SubagentManager not initialized.",
            )

        task_desc = params.get("task", "")
        if not task_desc:
            return ToolResult(
                success=False, output="",
                error="'task' is required.",
            )

        persona = params.get("persona", "General-purpose software engineer")
        timeout_s = params.get("timeout_s", 120)
        tool_names = params.get("tools")
        parent_event_id = params.pop("_parent_event_id", "")  # Injected by ToolExecutor

        try:
            task_id = await self.subagent_manager.spawn(
                task_desc=task_desc,
                agent_persona=persona,
                tool_names=tool_names,
                available_tools=self.available_tools,
                timeout_s=timeout_s,
                parent_event_id=parent_event_id,
            )
            return ToolResult(
                success=True,
                output=(
                    f"Subagent {task_id} spawned successfully.\n"
                    f"Persona: {persona}\n"
                    f"Task: {task_desc}\n"
                    f"Timeout: {timeout_s}s\n"
                    f"Results will appear in the main stream as the subagent works."
                ),
            )
        except RuntimeError as e:
            return ToolResult(success=False, output="", error=str(e))
        except Exception as e:
            return ToolResult(success=False, output="", error=f"Spawn failed: {e}")


# ---------------------------------------------------------------------------
# ToolDispatcher — Routes tool calls + enforces rate limits
# ---------------------------------------------------------------------------

class ToolDispatcher:
    """Routes tool calls to the correct tool for a given agent.

    Per-agent tool authorization is enforced: an agent can only call tools
    that are in their assigned ToolSet.
    """

    def __init__(self, toolsets: dict[str, list[Tool]]):
        """
        Args:
            toolsets: Mapping of agent_id → list of authorized Tool instances.
        """
        self.toolsets = toolsets
        # Build per-agent lookup: agent_id → {tool_name: Tool}
        self._lookup: dict[str, dict[str, Tool]] = {}
        for agent_id, tools in toolsets.items():
            self._lookup[agent_id] = {t.name: t for t in tools}

    def cleanup(self):
        """Invoke cleanup on all tools that support it (e.g. closing background processes)."""
        seen_tools = set()
        for agent_tools in self._lookup.values():
            for tool in agent_tools.values():
                if tool not in seen_tools:
                    if hasattr(tool, "cleanup"):
                        tool.cleanup()
                    seen_tools.add(tool)
        print(f"🐚 [ConchShell] Cleanup completed for {len(seen_tools)} tools.")

    def get_tools_for_agent(self, agent_id: str) -> list[Tool]:
        """Return the list of tools available to an agent."""
        return self.toolsets.get(agent_id, [])

    async def execute(
        self, agent_id: str, tool_name: str, params: dict,
        publish_fn: Optional[Callable[[bytes], Awaitable[None]]] = None
    ) -> ToolResult:
        """Execute a tool call for an agent, enforcing authorization."""
        agent_tools = self._lookup.get(agent_id, {})
        if tool_name not in agent_tools:
            return ToolResult(
                success=False, output="",
                error=f"Agent {agent_id} is not authorized to use tool '{tool_name}'.",
            )

        tool = agent_tools[tool_name]

        try:
            return await tool.execute(agent_id, params, publish_fn=publish_fn)
        except Exception as e:
            return ToolResult(success=False, output="", error=f"Tool error: {e}")

# ---------------------------------------------------------------------------
# ExecuteInSandboxTool — Explicit Orchestration (Mode C)
# ---------------------------------------------------------------------------

class ExecuteInSandboxTool(Tool):
    name = "execute_in_sandbox"
    description = (
        "Spins up an ephemeral container with the specified image, "
        "runs a command, and returns the result. Use for tasks requiring "
        "isolated environments (e.g.Node.js, Terraform, specialized Python)."
    )

    def __init__(self, sandbox_manager):
        super().__init__()
        self.sandbox_manager = sandbox_manager

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "image": {
                    "type": "string",
                    "description": "Docker image to run (e.g. 'node:18', 'python:3.11')."
                },
                "command": {
                    "type": "string",
                    "description": "Shell command to execute."
                }
            },
            "required": ["image", "command"]
        }

    async def execute(self, agent_id: str, params: dict,
                      publish_fn: Optional[Callable[[bytes], Awaitable[None]]] = None) -> ToolResult:
        image = params.get("image")
        command = params.get("command")
        
        if not image or not command:
            return ToolResult(success=False, output="", error="Image and command required.")

        exit_code, output = await self.sandbox_manager.execute(
            command, agent_id, publish_fn, image=image
        )
        
        return ToolResult(
            success=(exit_code == 0),
            output=output[:config.TOOLS.output_limit_chars] if output else f"Command exited with code {exit_code}. No output.",
            error=f"Exit code: {exit_code}" if exit_code != 0 else None,
        )
