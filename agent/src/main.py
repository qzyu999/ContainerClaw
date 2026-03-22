import os
import signal
import sys
import time
import subprocess
import threading
import asyncio
import grpc
import concurrent.futures
from pathlib import Path
import config
from moderator import StageModerator, GeminiAgent
from tools import (
    ToolDispatcher, ProjectBoard,
    DiffTool, TestRunnerTool, BoardTool,
    SurgicalEditTool, AdvancedReadTool, RepoMapTool,
    StructuredSearchTool, LinterTool, SessionShellTool,
    CreateFileTool,
)
import fluss
import pyarrow as pa

# Generated gRPC stubs
import agent_pb2
import agent_pb2_grpc

# Language detection map for ReadFile
LANG_MAP = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".java": "java", ".go": "go", ".rs": "rust", ".rb": "ruby",
    ".md": "markdown", ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".html": "html", ".css": "css", ".sh": "bash", ".sql": "sql",
    ".txt": "plaintext", ".toml": "toml", ".xml": "xml",
    ".c": "c", ".cpp": "cpp", ".h": "c", ".hpp": "cpp",
}

class AgentService(agent_pb2_grpc.AgentServiceServicer):
    def __init__(self, fluss_conn, table, board_table=None):
        self.session_id = config.CLAW_SESSION_ID
        self.is_running = True
        self.fluss_conn = fluss_conn
        self.table = table
        self.board_table = board_table
        self.board = None  # Set during moderator init
        
        # Start the Moderator in a background thread
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._run_moderator_thread, daemon=True).start()

    def _run_moderator_thread(self):
        asyncio.set_event_loop(self.loop)
        # Load API Key
        try:
            api_key = open("/run/secrets/gemini_api_key", "r").read().strip()
        except:
            api_key = os.getenv("GEMINI_API_KEY")

        agents = [
            GeminiAgent("Alice", "Software architect.", api_key),
            GeminiAgent("Bob", "Project manager.", api_key),
            GeminiAgent("Carol", "Software engineer.", api_key),
            GeminiAgent("David", "Software QA tester.", api_key),
            GeminiAgent("Eve", "Business user.", api_key)
        ]

        # ── ConchShell: Per-agent tool authorization ──
        conchshell_enabled = config.CONCHSHELL_ENABLED
        tool_dispatcher = None

        if conchshell_enabled:
            board = ProjectBoard(board_table=self.board_table)
            self.board = board  # Expose for GetBoard RPC

            session_shell = SessionShellTool()
            test_runner = TestRunnerTool(session_shell=session_shell)
            diff = DiffTool(session_shell=session_shell)
            board_rw = BoardTool(board, write_access=True)

            # SWE-bench Advanced Tools
            surgical_edit = SurgicalEditTool()
            advanced_read = AdvancedReadTool()
            repo_map = RepoMapTool()
            structured_search = StructuredSearchTool()
            linter = LinterTool()
            create_file = CreateFileTool()

            common_tools = [
                board_rw, test_runner, diff,
                surgical_edit, advanced_read, repo_map, structured_search,
                linter, session_shell, create_file
            ]

            toolsets = {
                "Alice": common_tools,
                "Bob":   common_tools,
                "Carol": common_tools,
                "David": common_tools,
                "Eve":   common_tools,
            }
            tool_dispatcher = ToolDispatcher(toolsets)
            print("🐚 [ConchShell] Tool dispatcher initialized with per-agent authorization.")
        else:
            print("🐚 [ConchShell] Disabled — agents will use text-only mode.")

        autonomous_steps = config.AUTONOMOUS_STEPS
        self.moderator = StageModerator(
            self.table, agents,
            tool_dispatcher=tool_dispatcher,
        )
        print("--- ⚖️ STAGE ACTIVE (Democratic Moderator) ---")
        self.loop.run_until_complete(self.moderator.run(autonomous_steps=autonomous_steps))

    def ExecuteTask(self, request, context):
        print(f"📥 Received task from UI: {request.prompt}")
        
        future = asyncio.run_coroutine_threadsafe(
            self.moderator.publish("Human", request.prompt), 
            self.loop
        )
        
        def done_callback(f):
            try:
                f.result()
                print("📝 Successfully wrote 'Human' message to Fluss.")
            except Exception as e:
                print(f"❌ FAILED to write to Fluss: {e}")

        future.add_done_callback(done_callback)
        return agent_pb2.TaskStatus(accepted=True, message="Task received.")

    def StreamActivity(self, request, context):
        """Stream real-time events from Fluss to the UI via gRPC.
        
        W-1: Replaces the old in-memory queue.Queue approach.
        Creates a dedicated Fluss scanner that tails the log
        and yields events as they arrive.
        """
        session_id = request.session_id
        
        try:
            # Create a Fluss scanner in the asyncio loop
            future = asyncio.run_coroutine_threadsafe(
                self._create_sse_scanner(), self.loop
            )
            scanner = future.result(timeout=10)

            # Send Handshake
            yield agent_pb2.ActivityEvent(
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                type="thought",
                content=f"Connected to session: {session_id}"
            )

            while self.is_running:
                if not context.is_active():
                    break
                try:
                    # Synchronous poll — this blocks for up to 500ms
                    poll = scanner.poll_arrow(timeout_ms=500)
                    if poll.num_rows == 0:
                        continue

                    ts_arr = poll.column("ts")
                    actor_arr = poll.column("actor_id")
                    content_arr = poll.column("content")
                    type_arr = poll.column("type")

                    for i in range(poll.num_rows):
                        ts_ms = ts_arr[i].as_py()
                        actor_id = actor_arr[i].as_py()
                        content = content_arr[i].as_py()
                        e_type = type_arr[i].as_py()

                        if isinstance(actor_id, bytes): actor_id = actor_id.decode('utf-8')
                        if isinstance(content, bytes): content = content.decode('utf-8')
                        if isinstance(e_type, bytes): e_type = e_type.decode('utf-8')

                        ts_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts_ms / 1000))

                        yield agent_pb2.ActivityEvent(
                            timestamp=ts_iso,
                            type=e_type or "output",
                            content=content,
                            actor_id=actor_id
                        )
                except Exception as e:
                    print(f"⚠️ [StreamActivity] Poll error: {e}")
                    continue
        finally:
            print(f"🔌 Cleanly disconnected from session: {session_id}")

    async def _create_sse_scanner(self):
        """Create a Fluss scanner positioned at the tail of the log.
        
        The scanner starts at offset 0 but quickly catches up to head.
        New events appear in real-time as they are published.
        """
        scanner = await self.table.new_scan().create_record_batch_log_scanner()
        # Subscribe starting at the current tail so we only see NEW events
        # (GetHistory handles backfill on page load)
        scanner.subscribe(bucket_id=0, start_offset=0)
        # Drain existing records to reach the tail
        while True:
            poll = scanner.poll_arrow(timeout_ms=300)
            if poll.num_rows == 0:
                break
        return scanner

    # ── Workspace Explorer gRPC Handlers ──

    def ListWorkspace(self, request, context):
        """Recursively list all files in /workspace."""
        entries = []
        workspace = Path("/workspace")
        if not workspace.exists():
            return agent_pb2.WorkspaceResponse(files=[])

        for p in sorted(workspace.rglob("*")):
            # Skip .git internals but keep .gitkeep
            rel = str(p.relative_to(workspace))
            if ".git" in rel.split(os.sep) and not rel.endswith(".gitkeep"):
                continue
            try:
                entries.append(agent_pb2.FileEntry(
                    path=rel,
                    is_directory=p.is_dir(),
                    size_bytes=p.stat().st_size if p.is_file() else 0,
                    modified_at=time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ",
                        time.gmtime(p.stat().st_mtime),
                    ),
                ))
            except OSError:
                continue  # Skip unreadable entries
        return agent_pb2.WorkspaceResponse(files=entries)

    def ReadFile(self, request, context):
        """Read a single file from /workspace."""
        path = Path("/workspace") / request.path
        if not path.resolve().is_relative_to(Path("/workspace")):
            context.abort(grpc.StatusCode.PERMISSION_DENIED, "Path traversal denied")
        if not path.exists() or path.is_dir():
            context.abort(grpc.StatusCode.NOT_FOUND, f"File not found: {request.path}")

        lang = LANG_MAP.get(path.suffix, "plaintext")
        try:
            content = path.read_text(errors="replace")[:1_000_000]  # 1MB cap
        except Exception as e:
            context.abort(grpc.StatusCode.INTERNAL, f"Failed to read file: {e}")

        return agent_pb2.FileResponse(content=content, language=lang, path=request.path)

    def DiffFile(self, request, context):
        """Generate a diff for a file (vs git HEAD or vs empty)."""
        path = Path("/workspace") / request.path
        if not path.resolve().is_relative_to(Path("/workspace")):
            context.abort(grpc.StatusCode.PERMISSION_DENIED, "Path traversal denied")

        modified = ""
        if path.exists() and path.is_file():
            try:
                modified = path.read_text(errors="replace")
            except Exception:
                pass

        # Try git diff if the workspace is a git repo
        original = ""
        diff_text = ""
        try:
            result = subprocess.run(
                ["git", "show", f"HEAD:{request.path}"],
                capture_output=True, text=True, cwd="/workspace", timeout=5,
            )
            if result.returncode == 0:
                original = result.stdout
        except Exception:
            pass

        try:
            diff_result = subprocess.run(
                ["git", "diff", "HEAD", "--", request.path],
                capture_output=True, text=True, cwd="/workspace", timeout=5,
            )
            if diff_result.returncode == 0:
                diff_text = diff_result.stdout
        except Exception:
            pass

        return agent_pb2.DiffResponse(
            original=original, modified=modified, diff_text=diff_text
        )

    def GetBoard(self, request, context):
        """Return project board items from in-memory state (Fluss-backed)."""
        if not self.board:
            return agent_pb2.BoardResponse(items=[])
        
        proto_items = []
        for item in self.board.items:
            proto_items.append(agent_pb2.BoardItem(
                id=item.get("id", ""),
                type=item.get("type", ""),
                title=item.get("title", ""),
                description=item.get("description", ""),
                status=item.get("status", ""),
                assigned_to=item.get("assigned_to") or "",
                created_at=item.get("created_at", 0.0),
            ))
        return agent_pb2.BoardResponse(items=proto_items)

    def GetHistory(self, request, context):
        """Fetch full chat history from Fluss."""
        print(f"📜 [Agent] Fetching history for session: {request.session_id}")
        future = asyncio.run_coroutine_threadsafe(self._fetch_history_async(), self.loop)
        try:
            events = future.result(timeout=30)
            print(f"✅ [Agent] History fetched: {len(events)} messages.")
            return agent_pb2.HistoryResponse(events=events)
        except Exception as e:
            import traceback
            error_msg = f"GetHistory error: {e}\n{traceback.format_exc()}"
            print(f"❌ {error_msg}")
            context.abort(grpc.StatusCode.INTERNAL, error_msg)

    async def _fetch_history_async(self):
        # We use a scanner that starts at 0 and reads until the end
        scanner = await self.table.new_scan().create_record_batch_log_scanner()
        scanner.subscribe(bucket_id=0, start_offset=0)
        
        events = []
        while True:
            # Poll with a short timeout.
            poll = await asyncio.to_thread(scanner.poll_arrow, timeout_ms=500)
            if poll.num_rows == 0:
                break
            
            # Use raw Arrow columns (faster and avoids pandas dependency issues)
            ts_arr = poll.column("ts")
            actor_arr = poll.column("actor_id")
            content_arr = poll.column("content")
            
            for i in range(poll.num_rows):
                ts_ms = ts_arr[i].as_py()
                actor_id = actor_arr[i].as_py()
                content = content_arr[i].as_py()
                
                # Conversion to string if needed
                if isinstance(actor_id, bytes): actor_id = actor_id.decode('utf-8')
                if isinstance(content, bytes): content = content.decode('utf-8')

                ts_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts_ms / 1000))
                
                # Determine type — match moderator.py behavior
                # Now we read it directly from Fluss!
                try:
                    e_type = poll.column("type")[i].as_py()
                    if isinstance(e_type, bytes): e_type = e_type.decode('utf-8')
                except (KeyError, ValueError, IndexError):
                    # Fallback for old records without 'type'
                    e_type = "thought" if actor_id == "Moderator" else "output"
                
                events.append(agent_pb2.ActivityEvent(
                    timestamp=ts_iso,
                    type=e_type,
                    content=content,
                    actor_id=actor_id
                ))
        return events

async def init_infrastructure():
    print("🛰️ Initializing Fluss Infrastructure...")
    fluss_config = fluss.Config({"bootstrap.servers": config.FLUSS_BOOTSTRAP_SERVERS})
    
    # Retry connection — coordinator may not be listening yet
    conn = None
    for attempt in range(30):
        try:
            conn = await fluss.FlussConnection.create(fluss_config)
            print("✅ Connected to Fluss Coordinator.")
            break
        except Exception as e:
            print(f"⏳ Waiting for Fluss Coordinator (attempt {attempt+1}/30)... {e}")
            await asyncio.sleep(2)
    
    if not conn:
        raise Exception("❌ Failed to connect to Fluss Coordinator after 30 attempts.")
    
    admin = await conn.get_admin()
    
    table_path = fluss.TablePath("containerclaw", "chatroom")
    await admin.create_database("containerclaw", ignore_if_exists=True)
    
    schema = pa.schema([
        pa.field("ts", pa.int64()),
        pa.field("actor_id", pa.string()),
        pa.field("content", pa.string()),
        pa.field("type", pa.string()),
        pa.field("tool_name", pa.string()),
        pa.field("tool_success", pa.bool_()),
        pa.field("parent_actor", pa.string()),
    ])
    descriptor = fluss.TableDescriptor(fluss.Schema(schema), bucket_count=1)

    # 1. Wait for Metadata/Creation
    print("⏳ Waiting for Table Creation...")
    for attempt in range(15):
        try:
            await admin.create_table(table_path, descriptor, ignore_if_exists=True)
            print(f"✅ Coordinator confirmed: {table_path} exists.")
            break
        except Exception as e:
            await asyncio.sleep(3)

    # 2. Wait for Data Plane Visibility (THE FIX)
    print("💎 Attempting to connect to Data Plane...")
    table = None
    for attempt in range(10):
        try:
            table = await conn.get_table(table_path)
            print("🚀 Successfully connected to Table Data Plane.")
            break
        except Exception as e:
            print(f"⏳ Table not found in local metadata yet (attempt {attempt+1}/10)...")
            await asyncio.sleep(2)
            
    if not table:
        raise Exception("❌ Failed to resolve Table Data Plane after 10 attempts.")
    
    # Check if schema needs update (migration/purge required)
    try:
        table_info = table.get_table_info()
        column_count = table_info.get_column_count()
        if column_count < 7:
            print(f"⚠️ [Infrastructure] Fluss table has an OLD schema ({column_count} columns, expected 7).")
            print("⚠️ [Infrastructure] PLEASE RUN: ./claw.sh clean && ./claw.sh up")
            print("⚠️ [Infrastructure] This is required to apply the new 7-column schema.")
    except Exception as e:
        print(f"⚠️ [Infrastructure] Could not verify schema: {e}")

    # ── W-2: Board events table ──
    board_path = fluss.TablePath("containerclaw", "board_events")
    board_schema = pa.schema([
        pa.field("ts", pa.int64()),
        pa.field("action", pa.string()),         # "create" | "update_status"
        pa.field("item_id", pa.string()),
        pa.field("item_type", pa.string()),       # "epic" | "story" | "task"
        pa.field("title", pa.string()),
        pa.field("description", pa.string()),
        pa.field("status", pa.string()),          # "todo" | "in_progress" | "done"
        pa.field("assigned_to", pa.string()),
        pa.field("actor", pa.string()),           # which agent made the change
    ])
    board_descriptor = fluss.TableDescriptor(fluss.Schema(board_schema), bucket_count=1)

    for attempt in range(15):
        try:
            await admin.create_table(board_path, board_descriptor, ignore_if_exists=True)
            print(f"✅ Coordinator confirmed: {board_path} exists.")
            break
        except Exception:
            await asyncio.sleep(3)

    board_table = None
    for attempt in range(10):
        try:
            board_table = await conn.get_table(board_path)
            print("🚀 Board table connected.")
            break
        except Exception:
            await asyncio.sleep(2)

    if not board_table:
        print("⚠️ [Infrastructure] Board table not available — falling back to JSON persistence.")
        
    return conn, table, board_table

def serve():
    # 1. Block until Fluss is ready
    conn, table, board_table = asyncio.run(init_infrastructure())
    
    # 2. Start gRPC
    server = grpc.server(concurrent.futures.ThreadPoolExecutor(max_workers=10))
    agent_service = AgentService(conn, table, board_table)
    agent_pb2_grpc.add_AgentServiceServicer_to_server(agent_service, server)
    server.add_insecure_port('0.0.0.0:50051')
    server.start()
    print("🚀 Agent gRPC Server Online on port 50051.")
    server.wait_for_termination()

if __name__ == "__main__":
    serve()