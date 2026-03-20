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
from moderator import StageModerator, GeminiAgent
from tools import (
    ToolDispatcher, ProjectBoard,
    ShellTool, FileReadTool, FileWriteTool, DiffTool,
    TestRunnerTool, BoardTool,
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
    def __init__(self, fluss_conn, table):
        self.session_id = os.getenv("CLAW_SESSION_ID", "default-session")
        self.is_running = True
        self.event_queues = {} 
        self.fluss_conn = fluss_conn
        self.table = table
        
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
        conchshell_enabled = os.getenv("CONCHSHELL_ENABLED", "true").lower() == "true"
        tool_dispatcher = None

        if conchshell_enabled:
            board = ProjectBoard()

            # Shared tool instances
            shell = ShellTool()
            file_read = FileReadTool()
            file_write = FileWriteTool()
            diff = DiffTool()
            test_runner = TestRunnerTool()
            board_rw = BoardTool(board, write_access=True)
            board_ro = BoardTool(board, write_access=False)

            toolsets = {
                "Alice": [shell, board_rw, file_read, file_write, test_runner, diff],
                "Bob":   [shell, board_rw, file_read, file_write, test_runner, diff],
                "Carol": [shell, board_rw, file_read, file_write, test_runner, diff],
                "David": [shell, board_rw, file_read, file_write, test_runner, diff],
                "Eve":   [shell, board_rw, file_read, file_write, test_runner, diff],
            }
            tool_dispatcher = ToolDispatcher(toolsets)
            print("🐚 [ConchShell] Tool dispatcher initialized with per-agent authorization.")
        else:
            print("🐚 [ConchShell] Disabled — agents will use text-only mode.")

        autonomous_steps = int(os.getenv("AUTONOMOUS_STEPS", "-1"))
        self.moderator = StageModerator(
            self.table, agents, self._bridge_to_ui,
            tool_dispatcher=tool_dispatcher,
        )
        print("--- ⚖️ STAGE ACTIVE (Democratic Moderator) ---")
        self.loop.run_until_complete(self.moderator.run(autonomous_steps=autonomous_steps))

    def _bridge_to_ui(self, actor_id, content, e_type):
        q = self._get_queue(self.session_id)
        q.put(agent_pb2.ActivityEvent(
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            type=e_type,
            content=content,
            actor_id=actor_id
        ))

    def _get_queue(self, session_id):
        if session_id not in self.event_queues:
            import queue
            self.event_queues[session_id] = queue.Queue()
        return self.event_queues[session_id]

    def ExecuteTask(self, request, context):
        print(f"📥 Received task from UI: {request.prompt}")
        
        # We wrap this in a future so we can catch errors in the logs
        future = asyncio.run_coroutine_threadsafe(
            self.moderator.publish("Human", request.prompt), 
            self.loop
        )
        
        # Add a logging callback
        def done_callback(f):
            try:
                f.result()
                print("📝 Successfully wrote 'Human' message to Fluss.")
            except Exception as e:
                print(f"❌ FAILED to write to Fluss: {e}")

        future.add_done_callback(done_callback)
        return agent_pb2.TaskStatus(accepted=True, message="Task received.")

    def StreamActivity(self, request, context):
        session_id = request.session_id
        q = self._get_queue(session_id)
        
        try:
            # Send Handshake
            yield agent_pb2.ActivityEvent(
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                type="thought",
                content=f"Connected to session: {session_id}"
            )

            while self.is_running:
                # Check if the client is still there
                if not context.is_active():
                    break
                try:
                    event = q.get(timeout=1.0)
                    yield event
                except:
                    continue
        finally:
            print(f"🔌 Cleanly disconnected from session: {session_id}")

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
    config = fluss.Config({"bootstrap.servers": "coordinator-server:9123"})
    
    # Retry connection — coordinator may not be listening yet
    conn = None
    for attempt in range(30):
        try:
            conn = await fluss.FlussConnection.create(config)
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
    
    # Define Schema
    schema = pa.schema([
        pa.field("ts", pa.int64()), 
        pa.field("actor_id", pa.string()), 
        pa.field("content", pa.string())
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
        
    return conn, table

def serve():
    # 1. Block until Fluss is ready
    conn, table = asyncio.run(init_infrastructure())
    
    # 2. Start gRPC
    server = grpc.server(concurrent.futures.ThreadPoolExecutor(max_workers=10))
    agent_service = AgentService(conn, table)
    agent_pb2_grpc.add_AgentServiceServicer_to_server(agent_service, server)
    server.add_insecure_port('0.0.0.0:50051')
    server.start()
    print("🚀 Agent gRPC Server Online on port 50051.")
    server.wait_for_termination()

if __name__ == "__main__":
    serve()