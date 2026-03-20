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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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

    async def execute(self, agent_id: str, params: dict) -> ToolResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# ShellTool — Sandboxed command execution
# ---------------------------------------------------------------------------

class ShellTool(Tool):
    name = "shell"
    description = (
        "Run a shell command in the /workspace directory. "
        "The container has no internet access. Use this to list files, "
        "inspect code, run scripts, or execute build commands."
    )

    BLOCKED_PATTERNS = {"rm -rf /", "dd if=", "mkfs", ":(){ :|:& };:"}
    TIMEOUT = 30

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute in /workspace."
                }
            },
            "required": ["command"]
        }

    async def execute(self, agent_id: str, params: dict) -> ToolResult:
        command = params.get("command", "")
        if not command:
            return ToolResult(success=False, output="", error="No command provided.")

        if any(blocked in command for blocked in self.BLOCKED_PATTERNS):
            return ToolResult(success=False, output="", error="Blocked command.")

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd="/workspace",
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.TIMEOUT
            )
            return ToolResult(
                success=proc.returncode == 0,
                output=stdout.decode(errors="replace")[:4096],
                error=stderr.decode(errors="replace")[:2048] if stderr else None,
            )
        except asyncio.TimeoutError:
            proc.kill()
            return ToolResult(
                success=False, output="",
                error=f"Command timed out after {self.TIMEOUT}s.",
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


# ---------------------------------------------------------------------------
# FileReadTool / FileWriteTool — Workspace I/O
# ---------------------------------------------------------------------------

class FileReadTool(Tool):
    name = "file_read"
    description = "Read the contents of a file in /workspace."

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to /workspace."
                }
            },
            "required": ["path"]
        }

    async def execute(self, agent_id: str, params: dict) -> ToolResult:
        path = Path("/workspace") / params.get("path", "")
        if not path.resolve().is_relative_to(Path("/workspace")):
            return ToolResult(success=False, output="", error="Path traversal denied.")
        if not path.exists():
            return ToolResult(success=False, output="", error=f"File not found: {path}")
        if path.is_dir():
            entries = sorted(str(p.relative_to(path)) for p in path.iterdir())
            return ToolResult(success=True, output="\n".join(entries))
        content = path.read_text(errors="replace")[:8192]
        return ToolResult(success=True, output=content)


class FileWriteTool(Tool):
    name = "file_write"
    description = "Write content to a file in /workspace. Creates parent directories as needed."

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to /workspace."
                },
                "content": {
                    "type": "string",
                    "description": "The full content to write to the file."
                }
            },
            "required": ["path", "content"]
        }

    async def execute(self, agent_id: str, params: dict) -> ToolResult:
        path = Path("/workspace") / params.get("path", "")
        if not path.resolve().is_relative_to(Path("/workspace")):
            return ToolResult(success=False, output="", error="Path traversal denied.")
        content = params.get("content", "")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            return ToolResult(
                success=True,
                output=f"Wrote {len(content)} bytes to {path.relative_to('/workspace')}",
                artifacts=[str(path)],
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


# ---------------------------------------------------------------------------
# DiffTool — Git diff wrapper
# ---------------------------------------------------------------------------

class DiffTool(Tool):
    name = "diff"
    description = "Show the git diff for a file in /workspace (vs HEAD)."

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to /workspace to diff."
                }
            },
            "required": ["path"]
        }

    async def execute(self, agent_id: str, params: dict) -> ToolResult:
        rel_path = params.get("path", "")
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["git", "diff", "HEAD", "--", rel_path],
                capture_output=True, text=True, cwd="/workspace", timeout=5,
            )
            diff_text = result.stdout if result.returncode == 0 else ""
            if not diff_text:
                return ToolResult(success=True, output="No differences found.")
            return ToolResult(success=True, output=diff_text[:8192])
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


# ---------------------------------------------------------------------------
# TestRunnerTool — Code execution & validation
# ---------------------------------------------------------------------------

class TestRunnerTool(Tool):
    name = "test_runner"
    description = (
        "Run test suites in /workspace. Supports pytest (default) and "
        "generic commands. Use runner='pytest' with args like 'tests/' "
        "or runner='generic' with the full command."
    )

    SUPPORTED_RUNNERS = {
        "pytest": "python -m pytest {args} --tb=short -q",
        "generic": "{args}",
    }
    TIMEOUT = 120

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

    async def execute(self, agent_id: str, params: dict) -> ToolResult:
        runner = params.get("runner", "pytest")
        args = params.get("args", "")

        if runner not in self.SUPPORTED_RUNNERS:
            return ToolResult(success=False, output="", error=f"Unknown runner: {runner}")

        command = self.SUPPORTED_RUNNERS[runner].format(args=args)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd="/workspace",
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.TIMEOUT
            )
            return ToolResult(
                success=proc.returncode == 0,
                output=stdout.decode(errors="replace")[:8192],
                error=stderr.decode(errors="replace")[:4096] if proc.returncode != 0 else None,
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
    """Project board backed by Fluss board_events table.
    
    W-2: Replaces the old JSON file persistence. Mutations are stored
    as append-only events in Fluss. State is rebuilt by replaying the
    event log on startup (crash recovery).
    
    Falls back to JSON file if board_table is not available.
    """

    def __init__(self, board_table=None):
        self.board_table = board_table
        self.board_path = Path("/workspace/.conchshell/board.json")  # Fallback only
        self.items: list[dict] = []
        self._writer = None
        self._pa_schema = None

        if self.board_table:
            self._pa_schema = pa.schema([
                pa.field("ts", pa.int64()),
                pa.field("action", pa.string()),
                pa.field("item_id", pa.string()),
                pa.field("item_type", pa.string()),
                pa.field("title", pa.string()),
                pa.field("description", pa.string()),
                pa.field("status", pa.string()),
                pa.field("assigned_to", pa.string()),
                pa.field("actor", pa.string()),
            ])
            self._writer = self.board_table.new_append().create_writer()
            self._replay_from_fluss()
        else:
            self._load()

    def _replay_from_fluss(self):
        """Replay board_events log to rebuild self.items."""
        import asyncio
        try:
            loop = asyncio.new_event_loop()
            scanner = loop.run_until_complete(
                self.board_table.new_scan().create_record_batch_log_scanner()
            )
            scanner.subscribe(bucket_id=0, start_offset=0)

            while True:
                poll = scanner.poll_arrow(timeout_ms=500)
                if poll.num_rows == 0:
                    break

                for i in range(poll.num_rows):
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
                        })
                    elif action == "update_status":
                        item_id = poll.column("item_id")[i].as_py()
                        new_status = poll.column("status")[i].as_py()
                        for item in self.items:
                            if item["id"] == item_id:
                                item["status"] = new_status
                                break
            loop.close()
            print(f"📋 [ProjectBoard] Replayed {len(self.items)} board items from Fluss.")
        except Exception as e:
            print(f"⚠️ [ProjectBoard] Fluss replay failed, falling back to JSON: {e}")
            self._load()

    def _publish_event(self, action, item_id, item_type="", title="",
                       description="", status="", assigned_to="", actor="Moderator"):
        """Write a board mutation event to Fluss."""
        if not self._writer:
            return
        try:
            batch = pa.RecordBatch.from_arrays([
                pa.array([int(time.time() * 1000)], type=pa.int64()),
                pa.array([action], type=pa.string()),
                pa.array([item_id], type=pa.string()),
                pa.array([item_type], type=pa.string()),
                pa.array([title], type=pa.string()),
                pa.array([description], type=pa.string()),
                pa.array([status], type=pa.string()),
                pa.array([assigned_to or ""], type=pa.string()),
                pa.array([actor], type=pa.string()),
            ], schema=self._pa_schema)
            self._writer.write_arrow_batch(batch)
            self._writer.flush()
            print(f"📋 [ProjectBoard] Published {action} event for {item_id}")
        except Exception as e:
            print(f"⚠️ [ProjectBoard] Failed to write to Fluss: {e}")

    def _load(self):
        """Fallback: load from JSON file."""
        try:
            if self.board_path.exists():
                self.items = json.loads(self.board_path.read_text())
        except Exception:
            self.items = []

    def _save(self):
        """Fallback: save to JSON file (only used when Fluss unavailable)."""
        self.board_path.parent.mkdir(parents=True, exist_ok=True)
        self.board_path.write_text(json.dumps(self.items, indent=2))

    def create_item(
        self, item_type: str, title: str,
        description: str = "", assigned_to: str | None = None,
        actor: str = "Moderator",
    ) -> dict:
        item = {
            "id": f"{item_type[:1].upper()}-{len(self.items) + 1:03d}",
            "type": item_type,
            "title": title,
            "description": description,
            "status": "todo",
            "assigned_to": assigned_to,
            "created_at": time.time(),
        }
        self.items.append(item)
        if self.board_table:
            self._publish_event(
                "create", item["id"], item_type, title,
                description, "todo", assigned_to or "", actor,
            )
        else:
            self._save()
        return item

    def update_status(self, item_id: str, status: str, actor: str = "Moderator") -> dict | None:
        for item in self.items:
            if item["id"] == item_id:
                item["status"] = status
                if self.board_table:
                    self._publish_event("update_status", item_id, status=status, actor=actor)
                else:
                    self._save()
                return item
        return None

    def get_board_summary(self) -> str:
        if not self.items:
            return "Board is empty. No items have been created yet."
        lines = []
        for item in self.items:
            icon = {"todo": "⬜", "in_progress": "🟡", "done": "✅"}.get(
                item["status"], "❓"
            )
            assignee = f" → {item['assigned_to']}" if item.get("assigned_to") else ""
            lines.append(f"{icon} [{item['id']}] {item['title']}{assignee}")
        return "\n".join(lines)


class BoardTool(Tool):
    name = "board"
    description = (
        "Interact with the shared project board. "
        "Actions: 'create' (type, title, description, assigned_to), "
        "'update' (item_id, status), 'list' (no params)."
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
                    "description": "Board action: 'create', 'update', or 'list'.",
                    "enum": ["create", "update", "list"],
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
                    "description": "Item ID for 'update' (e.g. 'T-001').",
                },
                "status": {
                    "type": "string",
                    "description": "New status for 'update': 'todo', 'in_progress', or 'done'.",
                },
            },
            "required": ["action"]
        }

    async def execute(self, agent_id: str, params: dict) -> ToolResult:
        action = params.get("action", "list")

        if action == "list":
            return ToolResult(success=True, output=self.board.get_board_summary())

        if not self.write_access:
            return ToolResult(success=False, output="", error="Read-only board access.")

        if action == "create":
            title = params.get("title", "Untitled")
            item = self.board.create_item(
                item_type=params.get("type", "task"),
                title=title,
                description=params.get("description", ""),
                assigned_to=params.get("assigned_to"),
            )
            return ToolResult(
                success=True,
                output=f"Created {item['id']}: {item['title']}",
            )

        if action == "update":
            item_id = params.get("item_id", "")
            status = params.get("status", "")
            if not item_id or not status:
                return ToolResult(
                    success=False, output="",
                    error="'update' requires 'item_id' and 'status'.",
                )
            item = self.board.update_status(item_id, status)
            if item:
                return ToolResult(
                    success=True,
                    output=f"Updated {item['id']} → {item['status']}",
                )
            return ToolResult(
                success=False, output="",
                error=f"Item {item_id} not found.",
            )

        return ToolResult(success=False, output="", error=f"Unknown action: {action}")


# ---------------------------------------------------------------------------
# ToolDispatcher — Routes tool calls + enforces rate limits
# ---------------------------------------------------------------------------

class ToolDispatcher:
    """Routes tool calls to the correct tool for a given agent.

    Per-agent tool authorization is enforced: an agent can only call tools
    that are in their assigned ToolSet.
    """

    MAX_TOOLS_PER_TURN = 5
    MAX_TOOLS_PER_CYCLE = 20

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

        self.cycle_counter = 0

    def reset_cycle(self):
        """Reset the per-cycle tool counter (call between autonomous cycles)."""
        self.cycle_counter = 0

    def get_tools_for_agent(self, agent_id: str) -> list[Tool]:
        """Return the list of tools available to an agent."""
        return self.toolsets.get(agent_id, [])

    async def execute(
        self, agent_id: str, tool_name: str, params: dict
    ) -> ToolResult:
        """Execute a tool call for an agent, enforcing authorization + rate limits."""
        if self.cycle_counter >= self.MAX_TOOLS_PER_CYCLE:
            return ToolResult(
                success=False, output="",
                error="Tool rate limit exceeded for this cycle.",
            )

        agent_tools = self._lookup.get(agent_id, {})
        if tool_name not in agent_tools:
            return ToolResult(
                success=False, output="",
                error=f"Agent {agent_id} is not authorized to use tool '{tool_name}'.",
            )

        self.cycle_counter += 1
        tool = agent_tools[tool_name]

        try:
            return await tool.execute(agent_id, params)
        except Exception as e:
            return ToolResult(success=False, output="", error=f"Tool error: {e}")
