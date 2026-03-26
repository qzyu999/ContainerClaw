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
from fluss_client import FlussClient
from moderator import StageModerator
from agent import GeminiAgent
from tools import (
    ToolDispatcher, ProjectBoard,
    DiffTool, TestRunnerTool, BoardTool,
    SurgicalEditTool, AdvancedReadTool, RepoMapTool,
    StructuredSearchTool, LinterTool, SessionShellTool,
    CreateFileTool,
)

# Generated gRPC stubs
import agent_pb2
import agent_pb2_grpc

from datetime import datetime, timezone
LANG_MAP = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".java": "java", ".go": "go", ".rs": "rust", ".rb": "ruby",
    ".md": "markdown", ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".html": "html", ".css": "css", ".sh": "bash", ".sql": "sql",
    ".txt": "plaintext", ".toml": "toml", ".xml": "xml",
    ".c": "c", ".cpp": "cpp", ".h": "c", ".hpp": "cpp",
}

def ms_to_iso(ts_ms: int) -> str:
    """Convert millisecond timestamp to high-precision RFC 3339 string."""
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    # Format with 3 decimal places for milliseconds
    return dt.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

class AgentService(agent_pb2_grpc.AgentServiceServicer):
    def __init__(self, fluss_client: FlussClient):
        self.is_running = True
        self.fluss = fluss_client
        self.table = fluss_client.chat_table
        self.board_table = fluss_client.board_table
        self.sessions_table = fluss_client.sessions_table
        self.moderators = {}  # session_id -> StageModerator
        self.moderator_lock = threading.Lock()
        
        # Start a dedicated event loop for all moderators
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._run_event_loop, daemon=True).start()

    def _run_event_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _get_moderator(self, session_id):
        if session_id in self.moderators:
            return self.moderators[session_id]

        # Initialize moderator in the event loop if it's the first time
        future = asyncio.run_coroutine_threadsafe(self._init_moderator_async(session_id), self.loop)
        return future.result(timeout=30)

    async def _init_moderator_async(self, session_id):
        with self.moderator_lock:
            if session_id in self.moderators:
                return self.moderators[session_id]
            
            print(f"🧠 [Agent] Initializing new session context: {session_id}")
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

            board = ProjectBoard(session_id=session_id, board_table=self.board_table)
            await board.initialize()

            if conchshell_enabled:
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
                print(f"🐚 [ConchShell] Tool dispatcher initialized for session {session_id}.")
            else:
                print("🐚 [ConchShell] Disabled — agents will use text-only mode.")

            autonomous_steps = config.AUTONOMOUS_STEPS
            moderator = StageModerator(
                self.table, agents, 
                session_id=session_id, 
                tool_dispatcher=tool_dispatcher,
                sessions_table=self.sessions_table,
                fluss_client=self.fluss
            )
            moderator.board = board
            self.moderators[session_id] = moderator
            # Start the moderator loop in the shared event loop
            asyncio.create_task(moderator.run(autonomous_steps=int(os.getenv("AUTONOMOUS_STEPS", "-1"))))
            
            return self.moderators[session_id]

    def ExecuteTask(self, request, context):
        print(f"📥 Received task from UI: {request.prompt} (Session: {request.session_id})")
        
        moderator = self._get_moderator(request.session_id)
        future = asyncio.run_coroutine_threadsafe(
            moderator.publish("Human", request.prompt), 
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
        
        Optimized: Uses timestamp-based seeking to skip historical records.
        """
        session_id = request.session_id
        start_ts = int(time.time() * 1000) - 2000 # Buffer for clock skew/overlap
        seen_keys = set()
        
        try:
            # 1. Send Handshake IMMEDIATELY to confirm connection
            yield agent_pb2.ActivityEvent(
                timestamp=ms_to_iso(int(time.time() * 1000)),
                type="thought",
                content=f"Connected to session: {session_id}"
            )

            # 2. Create optimized scanner (seek to start_ts)
            future = asyncio.run_coroutine_threadsafe(
                self._create_sse_scanner(start_ts), self.loop
            )
            scanner = future.result(timeout=10)

            while self.is_running:
                if not context.is_active():
                    break
                try:
                    # Native async poll via Rust future_into_py
                    future = asyncio.run_coroutine_threadsafe(
                        FlussClient.poll_async(scanner, timeout_ms=500),
                        self.loop
                    )
                    batches = future.result(timeout=10)
                    if not batches:
                        continue

                    for poll in batches:
                        if poll.num_rows == 0:
                            continue

                        # Use dict-style access for pyarrow.RecordBatch
                        sess_arr = poll["session_id"]
                        actor_arr = poll["actor_id"]
                        content_arr = poll["content"]
                        ts_arr = poll["ts"]
                        type_arr = poll["type"]
                        event_id_arr = poll["event_id"]

                        for i in range(poll.num_rows):
                            # Filter by session_id
                            if sess_arr[i].as_py() != session_id:
                                continue

                            ts_ms = ts_arr[i].as_py()
                            actor_id = actor_arr[i].as_py()
                            content = content_arr[i].as_py()
                            e_type = type_arr[i].as_py()

                            # Per-connection deduplication via event_id UUID
                            eid = event_id_arr[i].as_py()
                            key = eid if eid else f"{ts_ms}-{actor_id}"
                            if key in seen_keys:
                                continue
                            seen_keys.add(key)

                            if isinstance(actor_id, bytes): actor_id = actor_id.decode('utf-8')
                            if isinstance(content, bytes): content = content.decode('utf-8')
                            if isinstance(e_type, bytes): e_type = e_type.decode('utf-8')

                            ts_iso = ms_to_iso(ts_ms)

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

    async def _create_sse_scanner(self, start_ts=None):
        """Create a Fluss scanner to tail the log, optionally seeking to a timestamp."""
        return await self.fluss.create_scanner(self.table, start_ts=start_ts)

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
        moderator = self._get_moderator(request.session_id)
        if not moderator or not moderator.board:
            return agent_pb2.BoardResponse(items=[])
        
        proto_items = []
        for item in moderator.board.items:
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

    # ── Session Management ──

    def ListSessions(self, request, context):
        """List sessions via FlussClient."""
        try:
            future = asyncio.run_coroutine_threadsafe(
                self.fluss.list_sessions(), self.loop
            )
            sessions = future.result(timeout=15)
            return agent_pb2.SessionListResponse(sessions=[
                agent_pb2.SessionEntry(
                    session_id=s["session_id"],
                    title=s["title"],
                    created_at=s["created_at"],
                    last_active_at=s["last_active_at"],
                ) for s in sessions
            ])
        except Exception as e:
            print(f"❌ [Agent] ListSessions Error: {e}")
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def GetHistory(self, request, context):
        """Fetch chat history — from memory if moderator active, else from Fluss."""
        session_id = request.session_id
        if session_id in self.moderators:
            moderator = self.moderators[session_id]
            events = [
                agent_pb2.ActivityEvent(
                    timestamp=ms_to_iso(msg["ts"]),
                    type=msg.get("type", "output"),
                    content=msg.get("content", ""),
                    actor_id=msg.get("actor_id", ""),
                ) for msg in moderator.context.all_messages
            ]
            return agent_pb2.HistoryResponse(events=events)

        try:
            future = asyncio.run_coroutine_threadsafe(
                self.fluss.fetch_history(session_id), self.loop
            )
            raw_events = future.result(timeout=60)
            events = [
                agent_pb2.ActivityEvent(
                    timestamp=ms_to_iso(e["ts"]),
                    type=e["type"],
                    content=e["content"],
                    actor_id=e["actor_id"],
                ) for e in raw_events
            ]
            return agent_pb2.HistoryResponse(events=events)
        except Exception as e:
            print(f"❌ [Agent] GetHistory Error: {e}")
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def CreateSession(self, request, context):
        """Create a new session via FlussClient."""
        import uuid
        session_id = str(uuid.uuid4())
        title = request.title or f"Chat {session_id[:8]}"
        print(f"🆕 [Agent] Creating session: {title} ({session_id})")

        try:
            future = asyncio.run_coroutine_threadsafe(
                self.fluss.create_session(session_id, title), self.loop
            )
            result = future.result(timeout=10)
            return agent_pb2.SessionEntry(
                session_id=result["session_id"],
                title=result["title"],
                created_at=result["created_at"],
                last_active_at=result["last_active_at"],
            )
        except Exception as e:
            print(f"❌ [Agent] CreateSession Error: {e}")
            context.abort(grpc.StatusCode.INTERNAL, str(e))

def serve():
    # 1. Block until Fluss is ready
    fluss_client = FlussClient(config.FLUSS_BOOTSTRAP_SERVERS)
    asyncio.run(fluss_client.connect())
    
    # 2. Start gRPC
    server = grpc.server(concurrent.futures.ThreadPoolExecutor(max_workers=10))
    agent_service = AgentService(fluss_client)
    agent_pb2_grpc.add_AgentServiceServicer_to_server(agent_service, server)
    server.add_insecure_port('0.0.0.0:50051')
    server.start()
    print("🚀 Agent gRPC Server Online on port 50051.")
    server.wait_for_termination()

if __name__ == "__main__":
    serve()