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
    def __init__(self, fluss_conn, chat_table, board_table=None, sessions_table=None):
        self.is_running = True
        self.fluss_conn = fluss_conn
        self.table = chat_table
        self.board_table = board_table
        self.sessions_table = sessions_table
        self.moderators = {}  # session_id -> StageModerator
        
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
            fluss_conn=self.fluss_conn
        )
        moderator.board = board
        # Re-check to avoid race condition
        if session_id not in self.moderators:
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
                    # Poll via the event loop to avoid "no running event loop" errors
                    future = asyncio.run_coroutine_threadsafe(
                        asyncio.to_thread(scanner.poll_arrow, timeout_ms=500),
                        self.loop
                    )
                    poll = future.result(timeout=10)
                    if poll.num_rows == 0:
                        continue

                    # Use raw Arrow columns
                    ts_arr = poll.column("ts")
                    actor_arr = poll.column("actor_id")
                    content_arr = poll.column("content")
                    type_arr = poll.column("type")
                    sess_arr = poll.column("session_id")

                    for i in range(poll.num_rows):
                        # Filter by session_id
                        if sess_arr[i].as_py() != session_id:
                            continue

                        ts_ms = ts_arr[i].as_py()
                        actor_id = actor_arr[i].as_py()
                        content = content_arr[i].as_py()
                        e_type = type_arr[i].as_py()

                        # Per-connection deduplication
                        key = f"{ts_ms}-{actor_id}-{content[:50]}"
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
        scanner = await self.table.new_scan().create_record_batch_log_scanner()
        
        if start_ts:
            # Optimized: Seek to timestamp using admin.list_offsets
            admin = await self.fluss_conn.get_admin()
            table_path = self.table.get_table_path()
            # Query offsets for all 16 buckets at or after start_ts
            offsets = await admin.list_offsets(
                table_path, 
                list(range(16)), 
                fluss.OffsetSpec.timestamp(start_ts)
            )
            scanner.subscribe_buckets(offsets)
        else:
            # Fallback to tailing from the very beginning (rarely used for SSE)
            for b in range(16):
                scanner.subscribe(bucket_id=b, start_offset=0)
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

    # ── Phase 13: Session Management ──
    def ListSessions(self, request, context):
        """List historical sessions from the Fluss PK table."""
        if not self.sessions_table:
            return agent_pb2.SessionListResponse(sessions=[])
        
        try:
            # For PK tables, we can scan or just use a snapshot.
            # Using a simple record batch scanner since we want to list all.
            future = asyncio.run_coroutine_threadsafe(self._list_sessions_async(), self.loop)
            sessions = future.result(timeout=10)
            return agent_pb2.SessionListResponse(sessions=sessions)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"❌ [Agent] ListSessions Error: {e}")
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    async def _list_sessions_async(self):
        scanner = await self.sessions_table.new_scan().create_record_batch_log_scanner()
        for b in range(16):
            scanner.subscribe(bucket_id=b, start_offset=0)
        
        # Use a dict to deduplicate by session_id, keeping the latest version
        sessions_dict = {}
        empty_polls = 0
        while empty_polls < 5: # Tolerate a few empty polls before giving up
            poll = await asyncio.to_thread(scanner.poll_arrow, timeout_ms=500)
            if poll.num_rows == 0:
                empty_polls += 1
                continue
            
            empty_polls = 0
            # Use raw Arrow columns
            id_arr = poll.column("session_id")
            title_arr = poll.column("title")
            created_arr = poll.column("created_at")
            active_arr = poll.column("last_active_at")

            for i in range(poll.num_rows):
                sid = id_arr[i].as_py()
                sessions_dict[sid] = agent_pb2.SessionEntry(
                    session_id=sid,
                    title=title_arr[i].as_py(),
                    created_at=int(created_arr[i].as_py()),
                    last_active_at=int(active_arr[i].as_py())
                )
        
        # Return sorted by last active
        return sorted(sessions_dict.values(), key=lambda s: s.last_active_at, reverse=True)

    def CreateSession(self, request, context):
        """Register a new session in the Fluss PK table."""
        import uuid
        session_id = str(uuid.uuid4())
        now = int(time.time() * 1000)
        title = request.title or f"Chat {session_id[:8]}"
        
        print(f"🆕 [Agent] Creating session: {title} ({session_id})")
        
        future = asyncio.run_coroutine_threadsafe(self._create_session_async(session_id, title, now), self.loop)
        try:
            future.result(timeout=10)
            return agent_pb2.SessionEntry(
                session_id=session_id,
                title=title,
                created_at=now,
                last_active_at=now
            )
        except Exception as e:
            print(f"❌ [Agent] CreateSession Error: {e}")
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    async def _create_session_async(self, session_id, title, now):
        try:
            batch = pa.RecordBatch.from_arrays([
                pa.array([session_id], type=pa.string()),
                pa.array([title], type=pa.string()),
                pa.array([now], type=pa.int64()),
                pa.array([now], type=pa.int64()),
            ], schema=pa.schema([
                pa.field("session_id", pa.string()),
                pa.field("title", pa.string()),
                pa.field("created_at", pa.int64()),
                pa.field("last_active_at", pa.int64()),
            ]))
            
            # Use append to register in PK table
            writer = self.sessions_table.new_append().create_writer()
            writer.write_arrow_batch(batch)
            if hasattr(writer, "flush"):
                writer.flush()
            if hasattr(writer, "close"):
                writer.close()

            return agent_pb2.SessionEntry(
                session_id=session_id,
                title=title,
                created_at=now,
                last_active_at=now
            )
        except Exception as e:
            print(f"❌ [Agent] CreateSession Error: {e}")
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def GetHistory(self, request, context):
        """Fetch full chat history from Fluss."""
        print(f"📜 [Agent] Fetching history for session: {request.session_id}")
        future = asyncio.run_coroutine_threadsafe(self._fetch_history_async(request.session_id), self.loop)
        try:
            events = future.result(timeout=30)
            print(f"✅ [Agent] History fetched: {len(events)} messages.")
            return agent_pb2.HistoryResponse(events=events)
        except Exception as e:
            import traceback
            error_msg = f"GetHistory error: {e}\n{traceback.format_exc()}"
            print(f"❌ {error_msg}")
            context.abort(grpc.StatusCode.INTERNAL, error_msg)

    async def _fetch_history_async(self, session_id):
        # 1. Lookup session start time for optimized seeking
        start_ts = 0
        try:
            lookuper = self.sessions_table.new_lookup().create_lookuper()
            session_info = await lookuper.lookup({"session_id": session_id})
            if session_info:
                start_ts = session_info.get("created_at", 0)
                print(f"📍 [Agent] Session {session_id} started at {start_ts}. Seeking...")
        except Exception as e:
            print(f"⚠️ [Agent] Could not lookup session start time: {e}. Falling back to full scan.")

        # 2. Create scanner and seek if possible
        scanner = await self.table.new_scan().create_record_batch_log_scanner()
        if start_ts > 0:
            admin = await self.fluss_conn.get_admin()
            offsets = await admin.list_offsets(
                self.table.get_table_path(),
                list(range(16)),
                fluss.OffsetSpec.timestamp(start_ts)
            )
            scanner.subscribe_buckets(offsets)
        else:
            for b in range(16):
                scanner.subscribe(bucket_id=b, start_offset=0)
        
        events = []
        empty_polls = 0
        while empty_polls < 10: # Increased tolerance for chat history
            poll = await asyncio.to_thread(scanner.poll_arrow, timeout_ms=500)
            if poll.num_rows == 0:
                empty_polls += 1
                continue
            
            empty_polls = 0
            # Use raw Arrow columns (faster and avoids pandas dependency issues)
            session_arr = poll.column("session_id")
            ts_arr = poll.column("ts")
            actor_arr = poll.column("actor_id")
            content_arr = poll.column("content")
            
            for i in range(poll.num_rows):
                if session_arr[i].as_py() != session_id:
                    continue

                ts_ms = ts_arr[i].as_py()
                actor_id = actor_arr[i].as_py()
                content = content_arr[i].as_py()
                
                # Conversion to string if needed
                if isinstance(actor_id, bytes): actor_id = actor_id.decode('utf-8')
                if isinstance(content, bytes): content = content.decode('utf-8')

                ts_iso = ms_to_iso(ts_ms)
                
                # Determine type — match moderator.py behavior
                try:
                    e_type = poll.column("type")[i].as_py()
                    if isinstance(e_type, bytes): e_type = e_type.decode('utf-8')
                except (KeyError, ValueError, IndexError):
                    # Fallback for old records without 'type'
                    e_type = "thought" if actor_id == "Moderator" else "output"
                
                events.append({
                    "ts": ts_ms,
                    "proto": agent_pb2.ActivityEvent(
                        timestamp=ts_iso,
                        type=e_type,
                        content=content,
                        actor_id=actor_id
                    )
                })
        
        # Explicitly sort the entire history by millisecond timestamp
        events.sort(key=lambda x: x["ts"])
        return [e["proto"] for e in events]

async def init_infrastructure():
    print("🛰️ Initializing Fluss Infrastructure...")
    fluss_config = fluss.Config({"bootstrap.servers": config.FLUSS_BOOTSTRAP_SERVERS})
    
    global chat_table, sessions_table, board_table
    
    for attempt in range(30):
        try:
            conn = await fluss.FlussConnection.create(fluss_config)
            print("✅ Connected to Fluss.")
            admin = await conn.get_admin()
            
            # Database
            await admin.create_database("containerclaw", ignore_if_exists=True)
            
            # 1. Chatroom Table
            table_path = fluss.TablePath("containerclaw", "chatroom")
            schema = pa.schema([
                pa.field("session_id", pa.string()),
                pa.field("ts", pa.int64()),
                pa.field("actor_id", pa.string()),
                pa.field("content", pa.string()),
                pa.field("type", pa.string()),
                pa.field("tool_name", pa.string()),
                pa.field("tool_success", pa.bool_()),
                pa.field("parent_actor", pa.string()),
            ])
            descriptor = fluss.TableDescriptor(
                fluss.Schema(schema),
                hash_keys=["session_id"],
                bucket_count=16
            )
            await admin.create_table(table_path, descriptor, ignore_if_exists=True)
            chat_table = await conn.get_table(table_path)
            
            # 2. Sessions Table
            sessions_path = fluss.TablePath("containerclaw", "sessions")
            sessions_schema = pa.schema([
                pa.field("session_id", pa.string()),
                pa.field("title", pa.string()),
                pa.field("created_at", pa.int64()),
                pa.field("last_active_at", pa.int64()),
            ])
            sessions_descriptor = fluss.TableDescriptor(
                fluss.Schema(sessions_schema),
                primary_key=["session_id"]
            )
            await admin.create_table(sessions_path, sessions_descriptor, ignore_if_exists=True)
            sessions_table = await conn.get_table(sessions_path)
            
            # 3. Board Events Table
            board_path = fluss.TablePath("containerclaw", "board_events")
            board_schema = pa.schema([
                pa.field("session_id", pa.string()),
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
            board_descriptor = fluss.TableDescriptor(
                fluss.Schema(board_schema),
                hash_keys=["session_id"],
                bucket_count=16
            )
            await admin.create_table(board_path, board_descriptor, ignore_if_exists=True)
            board_table = await conn.get_table(board_path)
            
            print("🚀 All Fluss tables connected and ready.")
            return conn, chat_table, board_table, sessions_table

        except Exception as e:
            print(f"⏳ Fluss initialization failed (attempt {attempt+1}/30): {e}")
            await asyncio.sleep(3)
    
    raise Exception("❌ Failed to initialize Fluss after 30 attempts.")

def serve():
    # 1. Block until Fluss is ready
    conn, chat_table, board_table, sessions_table = asyncio.run(init_infrastructure())
    
    # 2. Start gRPC
    server = grpc.server(concurrent.futures.ThreadPoolExecutor(max_workers=10))
    agent_service = AgentService(conn, chat_table, board_table, sessions_table)
    agent_pb2_grpc.add_AgentServiceServicer_to_server(agent_service, server)
    server.add_insecure_port('0.0.0.0:50051')
    server.start()
    print("🚀 Agent gRPC Server Online on port 50051.")
    server.wait_for_termination()

if __name__ == "__main__":
    serve()