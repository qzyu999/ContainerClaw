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
from schemas import BOARD_EVENTS_SCHEMA, DEFAULT_BUCKET_COUNT
import ast
import os
import uuid
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
# DiffTool — Git diff wrapper
# ---------------------------------------------------------------------------

class DiffTool(Tool):
    name = "diff"
    description = "Show the git diff for a file in /workspace (vs HEAD)."

    def __init__(self, session_shell=None):
        super().__init__()
        self.session_shell = session_shell

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
        command = f"git diff HEAD -- {rel_path}"
        
        if self.session_shell:
            # Route through the persistent shell to share environment/state
            return await self.session_shell.execute(agent_id, {"command": command})
            
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

    def __init__(self, session_shell=None):
        super().__init__()
        self.session_shell = session_shell

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

        if self.session_shell:
            # Route through the persistent shell to share environment (virtualenvs, PATH, etc)
            return await self.session_shell.execute(agent_id, {"command": command, "timeout": self.TIMEOUT})

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

    def __init__(self, session_id: str, board_table=None):
        self.session_id = session_id
        self.board_table = board_table
        self.board_dir = Path("/workspace/.claw_state")
        self.board_path = self.board_dir / f"{session_id}_board.json"  # Fallback only
        self.items: list[dict] = []
        self._writer = None
        self._pa_schema = None

        if self.board_table:
            self._pa_schema = BOARD_EVENTS_SCHEMA
            self._writer = self.board_table.new_append().create_writer()
        else:
            self._load()

    async def initialize(self):
        """Async initialization: Replay board_events log to rebuild self.items."""
        if not self.board_table:
            return
            
        try:
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
                            })
                        elif action == "update_status":
                            item_id = poll.column("item_id")[i].as_py()
                            new_status = poll.column("status")[i].as_py()
                            for item in self.items:
                                if item["id"] == item_id:
                                    item["status"] = new_status
                                    break
                        elif action == "delete":
                            item_id = poll.column("item_id")[i].as_py()
                            self.items = [item for item in self.items if item["id"] != item_id]
            print(f"📋 [ProjectBoard] Replayed {len(self.items)} board items from Fluss.")
        except Exception as e:
            print(f"⚠️ [ProjectBoard] Fluss replay failed, falling back to JSON: {e}")
            self._load()

    async def _publish_event(self, action, item_id, item_type="", title="",
                             description="", status="", assigned_to="", actor="Moderator"):
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
            ], schema=self._pa_schema)
            self._writer.write_arrow_batch(batch)
            if hasattr(self._writer, "flush"):
                await self._writer.flush()
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
        """Fallback: atomic save to JSON file (only used when Fluss unavailable)."""
        self.board_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.board_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(self.items, indent=2))
        tmp_path.replace(self.board_path)

    async def create_item(
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
            await self._publish_event(
                "create", item["id"], item_type, title,
                description, "todo", assigned_to or "", actor,
            )
        else:
            self._save()
        return item

    async def update_status(self, item_id: str, status: str, actor: str = "Moderator") -> dict | None:
        for item in self.items:
            if item["id"] == item_id:
                item["status"] = status
                if self.board_table:
                    await self._publish_event("update_status", item_id, status=status, actor=actor)
                else:
                    self._save()
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
        "'update' (item_id, status), 'delete' (item_id), 'list' (no params)."
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
                    "description": "Board action: 'create', 'update', 'delete', or 'list'.",
                    "enum": ["create", "update", "delete", "list"],
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
            item = await self.board.create_item(
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
            item = await self.board.update_status(item_id, status)
            if item:
                return ToolResult(
                    success=True,
                    output=f"Updated {item['id']} → {item['status']}",
                )
            return ToolResult(
                success=False, output="",
                error=f"Item {item_id} not found.",
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
            
            if await self.board.delete_item(item_id):
                return ToolResult(success=True, output=f"Deleted {item_id} from board.")
            return ToolResult(success=False, output="", error=f"Failed to delete {item_id}.")

        return ToolResult(success=False, output="", error=f"Unknown action: {action}")


# ---------------------------------------------------------------------------
# SWE-bench Advanced Tools
# ---------------------------------------------------------------------------

class CreateFileTool(Tool):
    name = "create_file"
    description = "Create a new file in /workspace. Fails if the file already exists."

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
                    "description": "The initial content of the new file."
                }
            },
            "required": ["path", "content"]
        }

    async def execute(self, agent_id: str, params: dict) -> ToolResult:
        path = Path("/workspace") / params.get("path", "")
        if not path.resolve().is_relative_to(Path("/workspace")):
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
                "path": {"type": "string", "description": "File path relative to /workspace."},
                "old_str": {"type": "string", "description": "The exact string block to replace."},
                "new_str": {"type": "string", "description": "The new string block to insert."}
            },
            "required": ["path", "old_str", "new_str"]
        }

    async def execute(self, agent_id: str, params: dict) -> ToolResult:
        path = Path("/workspace") / params.get("path", "")
        if not path.resolve().is_relative_to(Path("/workspace")):
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
            return ToolResult(success=True, output=f"Successfully replaced 1 occurrence in {path.relative_to('/workspace')}", artifacts=[str(path)])
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class AdvancedReadTool(Tool):
    name = "advanced_read"
    description = "Reads a specific window of lines from a file with prepended line numbers."

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to /workspace."},
                "start_line": {"type": "integer", "description": "1-indexed start line."},
                "end_line": {"type": "integer", "description": "1-indexed end line (inclusive)."}
            },
            "required": ["path", "start_line", "end_line"]
        }

    async def execute(self, agent_id: str, params: dict) -> ToolResult:
        path = Path("/workspace") / params.get("path", "")
        if not path.resolve().is_relative_to(Path("/workspace")):
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

    async def execute(self, agent_id: str, params: dict) -> ToolResult:
        return await asyncio.to_thread(self._build_map)

    def _build_map(self) -> ToolResult:
        """Runs entirely in a worker thread — never touches the event loop."""
        base_dir = Path("/workspace")
        output_lines = []
        parsed_files = 0
        max_files = 500
        
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

    async def execute(self, agent_id: str, params: dict) -> ToolResult:
        query = params.get("query", "")
        if not query:
            return ToolResult(success=False, output="", error="Query cannot be empty.")
        
        include_glob = params.get("include_glob")
        page = params.get("page", 1)
        limit = 50
        
        # Capping the max initial search results to prevent inefficiency at scale
        cmd = ["grep", "-rnI", "-m", "500"]
        if include_glob:
            cmd.extend(["--include", include_glob])
        cmd.append(query)
        cmd.append(".")
        
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd="/workspace"
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
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
                "path": {"type": "string", "description": "File path relative to /workspace to lint."}
            },
            "required": ["path"]
        }

    async def execute(self, agent_id: str, params: dict) -> ToolResult:
        rel_path = params.get("path", "")
        path = Path("/workspace") / rel_path
        if not path.resolve().is_relative_to(Path("/workspace")):
            return ToolResult(success=False, output="", error="Path traversal denied.")
            
        try:
            proc = await asyncio.create_subprocess_exec(
                "python", "-m", "py_compile", str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd="/workspace"
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
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
    
    def __init__(self):
        super().__init__()
        self.sessions: dict[str, asyncio.subprocess.Process] = {}

    def cleanup(self):
        """Close persistent background processes (e.g. when agent session is destroyed)."""
        for agent_id, proc in list(self.sessions.items()):
            try:
                proc.kill()
            except Exception:
                pass
        self.sessions.clear()

    DEFAULT_TIMEOUT = 60

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute."},
                "timeout": {"type": "integer", "description": "Optional timeout in seconds (default 60)."}
            },
            "required": ["command"]
        }

    async def execute(self, agent_id: str, params: dict) -> ToolResult:
        command = params.get("command", "")
        timeout = params.get("timeout", self.DEFAULT_TIMEOUT)
        if not command:
            return ToolResult(success=False, output="", error="No command provided.")

        if agent_id not in self.sessions or self.sessions[agent_id].returncode is not None:
            proc = await asyncio.create_subprocess_shell(
                "/bin/bash",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd="/workspace"
            )
            self.sessions[agent_id] = proc
            
        proc = self.sessions[agent_id]
        boundary = f"---CONCHSHELL-DONE-{uuid.uuid4()}---"
        full_command = f"{command}\necho \"{boundary}\" $?\n"
        
        try:
            proc.stdin.write(full_command.encode())
            await proc.stdin.drain()
            
            output_lines = []
            return_code = -1
            
            while True:
                # Per-line timeout to catch hangs; total command timeout handled by wait_for wrapper?
                # Actually, simple per-line timeout is often enough to catch "dead" commands.
                # If we want a total command timeout, we'd wrap the whole block.
                line_bytes = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
                if not line_bytes:
                    if proc.returncode is None:
                        await asyncio.sleep(0.1) # brief wait for OS buffer
                    if proc.returncode is not None:
                        return_code = proc.returncode
                    break
                line = line_bytes.decode(errors="replace").rstrip('\n\r')
                if boundary in line:
                    parts = line.split(boundary)
                    if parts[0]:
                        output_lines.append(parts[0])
                    if len(parts) > 1 and parts[1].strip().lstrip('-').isdigit():
                        return_code = int(parts[1].strip())
                    break
                output_lines.append(line)
                
            out_str = "\n".join(output_lines)
            return ToolResult(success=(return_code == 0), output=out_str[:8192])
            
        except asyncio.TimeoutError:
            proc.kill()
            del self.sessions[agent_id]
            return ToolResult(success=False, output=f"Command timed out after {timeout}s. Session killed.", error="Timeout")
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))

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

        try:
            task_id = await self.subagent_manager.spawn(
                task_desc=task_desc,
                agent_persona=persona,
                tool_names=tool_names,
                available_tools=self.available_tools,
                timeout_s=timeout_s,
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
        self, agent_id: str, tool_name: str, params: dict
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
            return await tool.execute(agent_id, params)
        except Exception as e:
            return ToolResult(success=False, output="", error=f"Tool error: {e}")
