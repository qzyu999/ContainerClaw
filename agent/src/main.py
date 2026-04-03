"""
ContainerClaw Agent — gRPC Service Entry Point.

Architecture (post-Surgery 2):
  All gRPC handlers are native async coroutines running on the same
  asyncio event loop as the StageModerator, FlussClient, and tools.
  The "Bridge of Sighs" (run_coroutine_threadsafe + future.result)
  is eliminated — no cross-paradigm bridging, no timeout bombs.

  Blocking I/O (file reads, subprocess calls, workspace listing) is
  offloaded to worker threads via asyncio.to_thread.
"""

import os
import time
import asyncio
import uuid
from pathlib import Path

import grpc
import grpc.aio

import config
from fluss_client import FlussClient
from moderator import StageModerator
from agent import LLMAgent
from tools import (
    ToolDispatcher, ProjectBoard, DelegateTool,
    DiffTool, TestRunnerTool, BoardTool,
    SurgicalEditTool, AdvancedReadTool, RepoMapTool,
    StructuredSearchTool, LinterTool, SessionShellTool,
    CreateFileTool,
)
from subagent_manager import SubagentManager
from reconciler import ReconciliationController
from heartbeat import HeartbeatEmitter

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
    return dt.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


class AgentService(agent_pb2_grpc.AgentServiceServicer):
    """Async-native gRPC service for the claw-agent.

    All handlers are async coroutines sharing the main event loop.
    No threading bridges, no cross-paradigm futures.
    """

    def __init__(self, fluss_client: FlussClient):
        self.is_running = True
        self.fluss = fluss_client
        self.table = fluss_client.chat_table
        self.board_table = fluss_client.board_table
        self.sessions_table = fluss_client.sessions_table
        self.moderators = {}  # session_id -> StageModerator
        self.reconcilers = {}  # session_id -> ReconciliationController
        self._init_locks = {}  # session_id -> asyncio.Lock (prevents double init)

    async def _get_moderator(self, session_id: str) -> StageModerator:
        """Get or create a moderator for a session. Fully async, no bridging."""
        if session_id in self.moderators:
            return self.moderators[session_id]

        # Per-session lock to prevent double initialization
        if session_id not in self._init_locks:
            self._init_locks[session_id] = asyncio.Lock()

        async with self._init_locks[session_id]:
            # Double-check after acquiring lock
            if session_id in self.moderators:
                return self.moderators[session_id]
            return await self._init_moderator(session_id)

    async def _init_moderator(self, session_id: str) -> StageModerator:
        """Initialize a new session moderator. Runs on the event loop."""
        print(f"🧠 [Agent] Initializing new session context: {session_id}")

        # Build agents from config.yaml roster
        cfg = config.CONFIG
        agents = []
        for agent_cfg in cfg.agents:
            agents.append(LLMAgent(
                agent_id=agent_cfg.name,
                persona=agent_cfg.persona,
                provider=agent_cfg.provider or cfg.default_provider,
                model=agent_cfg.model or cfg.default_model,
            ))
        print(f"🤖 [Agent] Roster: {[a.agent_id for a in agents]} (provider: {cfg.default_provider}, model: {cfg.default_model})")

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

            # DelegateTool — wired after SubagentManager is created below
            delegate_tool = DelegateTool(available_tools=common_tools)
            all_tools = common_tools + [delegate_tool]

            # Build per-agent toolsets from config
            # Each agent gets tools based on their roster config's "tools" field
            tool_registry = {t.name: t for t in all_tools}
            toolsets = {}
            for agent_cfg in cfg.agents:
                agent_tool_names = agent_cfg.resolved_tools(cfg.default_tools)
                agent_tools = [tool_registry[n] for n in agent_tool_names if n in tool_registry]
                toolsets[agent_cfg.name] = agent_tools
            tool_dispatcher = ToolDispatcher(toolsets)
            print(f"🐚 [ConchShell] Tool dispatcher initialized for session {session_id}.")
        else:
            print("🐚 [ConchShell] Disabled — agents will use text-only mode.")

        moderator = StageModerator(
            self.table, agents,
            session_id=session_id,
            tool_dispatcher=tool_dispatcher,
            sessions_table=self.sessions_table,
            fluss_client=self.fluss
        )
        moderator.board = board

        # Wire SubagentManager after moderator (needs publisher from moderator.run)
        if conchshell_enabled:
            subagent_mgr = SubagentManager(
                fluss_client=self.fluss,
                table=self.table,
                session_id=session_id,
                publisher=None,  # Set in moderator.run() after publisher init
                provider=cfg.default_provider,
                model=cfg.default_model,
            )
            moderator.subagent_manager = subagent_mgr
            # Wire back reference for delegate tool
            delegate_tool.subagent_manager = subagent_mgr

        self.moderators[session_id] = moderator

        # Create HeartbeatEmitter for liveness
        heartbeat = HeartbeatEmitter(
            status_table=self.fluss.status_table,
            session_id=session_id,
        )

        # Create ReconciliationController — replaces the imperative loop
        reconciler = ReconciliationController(moderator, heartbeat)
        self.reconcilers[session_id] = reconciler

        # Wire reconciler into moderator for /stop support
        moderator._reconciler = reconciler

        # Start the reconciler loop as a task on THIS event loop
        asyncio.create_task(reconciler.run(
            autonomous_steps=config.AUTONOMOUS_STEPS
        ))

        return moderator

    # ── Core RPC Handlers (all async) ──────────────────────────────

    async def ExecuteTask(self, request, context):
        """Accept a task from the UI and publish to Fluss."""
        print(f"📥 Received task from UI: {request.prompt} (Session: {request.session_id})")

        moderator = await self._get_moderator(request.session_id)
        try:
            await moderator.publish("Human", request.prompt)
            print("📝 Successfully wrote 'Human' message to Fluss.")
        except Exception as e:
            print(f"❌ FAILED to write to Fluss: {e}")

        return agent_pb2.TaskStatus(accepted=True, message="Task received.")

    async def StreamActivity(self, request, context):
        """Stream real-time events from Fluss to the UI via gRPC.

        Native async generator — no cross-thread bridging, no timeout bombs.
        Direct await on FlussClient.poll_async.
        """
        session_id = request.session_id
        start_ts = int(time.time() * 1000) - 2000  # Buffer for clock skew
        seen_keys = set()

        try:
            # 1. Send Handshake IMMEDIATELY to confirm connection
            yield agent_pb2.ActivityEvent(
                timestamp=ms_to_iso(int(time.time() * 1000)),
                type="thought",
                content=f"Connected to session: {session_id}"
            )

            # 2. Create optimized scanner (seek to start_ts) — direct await
            scanner = await self._create_sse_scanner(start_ts)

            while self.is_running:
                if context.cancelled():
                    break
                try:
                    # Direct await — no bridge, no future, no timeout bomb
                    batches = await FlussClient.poll_async(scanner, timeout_ms=500)
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

    # ── Workspace Explorer gRPC Handlers (async + to_thread) ──────

    async def ListWorkspace(self, request, context):
        """Recursively list all files in /workspace. File I/O offloaded to thread."""
        entries = await asyncio.to_thread(self._list_workspace_sync)
        return agent_pb2.WorkspaceResponse(files=entries)

    def _list_workspace_sync(self) -> list:
        """Synchronous workspace listing in worker thread."""
        entries = []
        workspace = Path("/workspace")
        if not workspace.exists():
            return entries

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
        return entries

    async def ReadFile(self, request, context):
        """Read a single file from /workspace. File I/O offloaded to thread."""
        path = Path("/workspace") / request.path
        if not path.resolve().is_relative_to(Path("/workspace")):
            await context.abort(grpc.StatusCode.PERMISSION_DENIED, "Path traversal denied")
        if not path.exists() or path.is_dir():
            await context.abort(grpc.StatusCode.NOT_FOUND, f"File not found: {request.path}")

        lang = LANG_MAP.get(path.suffix, "plaintext")
        try:
            content = await asyncio.to_thread(
                lambda: path.read_text(errors="replace")[:1_000_000]
            )
        except Exception as e:
            await context.abort(grpc.StatusCode.INTERNAL, f"Failed to read file: {e}")

        return agent_pb2.FileResponse(content=content, language=lang, path=request.path)

    async def DiffFile(self, request, context):
        """Generate a diff for a file (vs git HEAD or vs empty). Offloaded to thread."""
        path = Path("/workspace") / request.path
        if not path.resolve().is_relative_to(Path("/workspace")):
            await context.abort(grpc.StatusCode.PERMISSION_DENIED, "Path traversal denied")

        original, modified, diff_text = await asyncio.to_thread(
            self._diff_file_sync, request.path, path
        )

        return agent_pb2.DiffResponse(
            original=original, modified=modified, diff_text=diff_text
        )

    @staticmethod
    def _diff_file_sync(rel_path: str, path: Path) -> tuple[str, str, str]:
        """Synchronous diff computation in worker thread."""
        import subprocess
        modified = ""
        if path.exists() and path.is_file():
            try:
                modified = path.read_text(errors="replace")
            except Exception:
                pass

        original = ""
        diff_text = ""
        try:
            result = subprocess.run(
                ["git", "show", f"HEAD:{rel_path}"],
                capture_output=True, text=True, cwd="/workspace", timeout=5,
            )
            if result.returncode == 0:
                original = result.stdout
        except Exception:
            pass

        try:
            diff_result = subprocess.run(
                ["git", "diff", "HEAD", "--", rel_path],
                capture_output=True, text=True, cwd="/workspace", timeout=5,
            )
            if diff_result.returncode == 0:
                diff_text = diff_result.stdout
        except Exception:
            pass

        return original, modified, diff_text

    async def GetBoard(self, request, context):
        """Return project board items from in-memory state (Fluss-backed)."""
        moderator = await self._get_moderator(request.session_id)
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

    # ── Session Management (all async) ────────────────────────────

    async def ListSessions(self, request, context):
        """List sessions via FlussClient. Direct await — no bridging."""
        try:
            sessions = await self.fluss.list_sessions()
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
            await context.abort(grpc.StatusCode.INTERNAL, str(e))

    async def GetHistory(self, request, context):
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
            raw_events = await self.fluss.fetch_history(session_id)
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
            await context.abort(grpc.StatusCode.INTERNAL, str(e))

    async def CreateSession(self, request, context):
        """Create a new session via FlussClient. Direct await — no bridging."""
        session_id = str(uuid.uuid4())
        title = request.title or f"Chat {session_id[:8]}"
        print(f"🆕 [Agent] Creating session: {title} ({session_id})")

        try:
            result = await self.fluss.create_session(session_id, title)
            return agent_pb2.SessionEntry(
                session_id=result["session_id"],
                title=result["title"],
                created_at=result["created_at"],
                last_active_at=result["last_active_at"],
            )
        except Exception as e:
            print(f"❌ [Agent] CreateSession Error: {e}")
            await context.abort(grpc.StatusCode.INTERNAL, str(e))


async def serve():
    """Async-native gRPC server — single event loop, no threading bridges."""
    # 1. Connect to Fluss
    fluss_client = FlussClient(config.FLUSS_BOOTSTRAP_SERVERS)
    await fluss_client.connect()

    # 2. Start async gRPC server
    server = grpc.aio.server()
    agent_service = AgentService(fluss_client)
    agent_pb2_grpc.add_AgentServiceServicer_to_server(agent_service, server)
    server.add_insecure_port('0.0.0.0:50051')
    await server.start()
    print("🚀 Agent gRPC Server Online (async) on port 50051.")
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())