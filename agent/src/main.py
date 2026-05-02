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

import asyncio
import os
import signal
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Generated gRPC stubs
import agent_pb2
import agent_pb2_grpc
import config
import grpc
import grpc.aio
from fluss_client import FlussClient
from heartbeat import HeartbeatEmitter
from moderator import StageModerator
from reconciler import ReconciliationController
from sandbox import SandboxManager
from subagent_manager import SubagentManager
from tools import (
    AdvancedReadTool,
    BoardTool,
    CreateFileTool,
    DelegateTool,
    DiffTool,
    ExecuteInSandboxTool,
    LinterTool,
    ProjectBoard,
    RepoMapTool,
    SessionShellTool,
    StructuredSearchTool,
    SurgicalEditTool,
    TestRunnerTool,
    ToolDispatcher,
)

from agent import LLMAgent
from shared.spine_loader import load_spine

LANG_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".md": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".html": "html",
    ".css": "css",
    ".sh": "bash",
    ".sql": "sql",
    ".txt": "plaintext",
    ".toml": "toml",
    ".xml": "xml",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
}


def ms_to_iso(ts_ms: int) -> str:
    """Convert millisecond timestamp to high-precision RFC 3339 string."""
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


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
        self.board_comment_table = fluss_client.board_comment_table
        self.sessions_table = fluss_client.sessions_table
        self.moderators = {}  # session_id -> StageModerator
        self.reconcilers = {}  # session_id -> ReconciliationController
        self._init_locks = {}  # session_id -> asyncio.Lock (prevents double init)
        self._session_configs = {}  # session_id -> {runtime_image, execution_mode}

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
            # Pull per-session config if set during CreateSession
            scfg = self._session_configs.get(session_id, {})
            return await self._init_moderator(
                session_id,
                runtime_image=scfg.get("runtime_image", ""),
                execution_mode=scfg.get("execution_mode", ""),
            )

    async def _init_moderator(
        self, session_id: str, runtime_image: str = "", execution_mode: str = ""
    ) -> StageModerator:
        """Initialize a new session moderator. Runs on the event loop.

        Args:
            session_id: Unique session identifier.
            runtime_image: Per-session runtime image override (e.g. "python:3.11").
            execution_mode: Per-session execution mode override.
        """
        print(f"🧠 [Agent] Initializing session: {session_id}")

        # Build agents from config.yaml roster
        cfg = config.CONFIG

        agents = []
        for agent_cfg in cfg.agents:
            # ── Loading SELF.md (Spine) Sectional Parsing ──
            spine_content = load_spine(agent_cfg.name)

            agents.append(
                LLMAgent(
                    agent_id=agent_cfg.name,
                    persona=agent_cfg.persona,
                    provider=agent_cfg.provider or cfg.default_provider,
                    model=agent_cfg.model or cfg.default_model,
                    spine=spine_content,
                )
            )
        print(
            f"🤖 [Agent] Roster: {[a.agent_id for a in agents]} (Spine Loaded: {bool(spine_content)})"
        )

        # ── ConchShell: Per-agent tool authorization ──
        conchshell_enabled = config.CONCHSHELL_ENABLED
        tool_dispatcher = None

        board = ProjectBoard(
            session_id=session_id,
            board_table=self.board_table,
            board_comment_table=self.board_comment_table,
        )
        await board.initialize()

        if conchshell_enabled:
            # ── Session-Scoped SandboxManager (Layered Defaults) ──
            # Session overrides → config.yaml → code defaults
            session_exec_mode = execution_mode or cfg.execution_mode
            # runtime_image is the Docker *image* (e.g. ghcr.io/...),
            # default_target_id is the container *name* (e.g. "swe-sidecar").
            # For implicit_proxy, we always look up by container name.
            session_runtime = cfg.sidecar_config.default_target_id

            sandbox_mgr = SandboxManager(
                mode=session_exec_mode,
                default_target=session_runtime,
                network=cfg.sidecar_config.network,
            )

            # ── Execution Mode Validation ──
            # The agent never provisions containers itself (no DinD).
            # Sidecars are provisioned by the orchestration layer
            # (docker-compose / k8s) and referenced by container name/ID.
            if session_exec_mode == "implicit_proxy":
                # Check Docker accessibility before attempting validation.
                # The agent container intentionally has NO Docker socket mount
                # (cap_drop: ALL, read_only, no-new-privileges).
                # Docker access is only available in SWE-bench overrides.
                docker_available = False
                try:
                    sandbox_mgr.client  # Triggers lazy connection + ping()
                    docker_available = True
                except RuntimeError:
                    pass

                if not docker_available:
                    print(
                        f"⚠️ [Agent] Docker not available — implicit_proxy mode requires"
                    )
                    print(f"    a pre-provisioned sidecar (docker-compose/k8s).")
                    print(f"    Falling back to native mode for this session.")
                    sandbox_mgr.mode = "native"
                elif session_runtime:
                    try:
                        sandbox_mgr.client.containers.get(session_runtime)
                        print(f"🐳 [Agent] Sidecar validated: {session_runtime}")
                    except Exception as e:
                        print(
                            f"⚠️ [Agent] Sidecar '{session_runtime}' not reachable: {e}"
                        )
                        print(f"    Falling back to native mode for this session.")
                        sandbox_mgr.mode = "native"
                else:
                    print(f"⚠️ [Agent] implicit_proxy mode but no target specified.")
                    print(f"    Falling back to native mode for this session.")
                    sandbox_mgr.mode = "native"
            elif session_exec_mode == "explicit_orchestrator":
                print(
                    f"🐳 [Agent] Explicit orchestrator mode — ephemeral containers on demand."
                )
            else:
                print(f"🐳 [Agent] Native execution mode for session {session_id[:8]}.")

            session_shell = SessionShellTool(sandbox_manager=sandbox_mgr)
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

            # Explicit Sidecar Orchestration
            execute_sandbox = ExecuteInSandboxTool(sandbox_manager=sandbox_mgr)

            common_tools = [
                board_rw,
                test_runner,
                diff,
                surgical_edit,
                advanced_read,
                repo_map,
                structured_search,
                linter,
                session_shell,
                create_file,
                execute_sandbox,
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
                agent_tools = [
                    tool_registry[n] for n in agent_tool_names if n in tool_registry
                ]
                toolsets[agent_cfg.name] = agent_tools
            tool_dispatcher = ToolDispatcher(toolsets)
            print(
                f"🐚 [ConchShell] Tool dispatcher initialized for session {session_id}."
            )
        else:
            sandbox_mgr = None
            print("🐚 [ConchShell] Disabled — agents will use text-only mode.")

        moderator = StageModerator(
            self.table,
            agents,
            session_id=session_id,
            tool_dispatcher=tool_dispatcher,
            sessions_table=self.sessions_table,
            fluss_client=self.fluss,
        )
        moderator.board = board
        moderator.sandbox_mgr = sandbox_mgr  # Expose for session context building

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
        asyncio.create_task(reconciler.run(autonomous_steps=config.AUTONOMOUS_STEPS))

        return moderator

    # ── Core RPC Handlers (all async) ──────────────────────────────

    async def ExecuteTask(self, request, context):
        """Accept a task from the UI and publish to Fluss."""
        print(
            f"📥 Received task from UI: {request.prompt} (Session: {request.session_id})"
        )

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
                content=f"Connected to session: {session_id}",
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

                            if isinstance(actor_id, bytes):
                                actor_id = actor_id.decode("utf-8")
                            if isinstance(content, bytes):
                                content = content.decode("utf-8")
                            if isinstance(e_type, bytes):
                                e_type = e_type.decode("utf-8")

                            ts_iso = ms_to_iso(ts_ms)

                            yield agent_pb2.ActivityEvent(
                                timestamp=ts_iso,
                                type=e_type or "output",
                                content=content,
                                actor_id=actor_id,
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
                entries.append(
                    agent_pb2.FileEntry(
                        path=rel,
                        is_directory=p.is_dir(),
                        size_bytes=p.stat().st_size if p.is_file() else 0,
                        modified_at=time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ",
                            time.gmtime(p.stat().st_mtime),
                        ),
                    )
                )
            except OSError:
                continue  # Skip unreadable entries
        return entries

    async def ReadFile(self, request, context):
        """Read a single file from /workspace. File I/O offloaded to thread."""
        path = Path("/workspace") / request.path
        if not path.resolve().is_relative_to(Path("/workspace")):
            await context.abort(
                grpc.StatusCode.PERMISSION_DENIED, "Path traversal denied"
            )
        if not path.exists() or path.is_dir():
            await context.abort(
                grpc.StatusCode.NOT_FOUND, f"File not found: {request.path}"
            )

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
            await context.abort(
                grpc.StatusCode.PERMISSION_DENIED, "Path traversal denied"
            )

        original, modified, diff_text = await asyncio.to_thread(
            self._diff_file_sync, request.path, path
        )

        return agent_pb2.DiffResponse(
            original=original, modified=modified, diff_text=diff_text
        )

    @staticmethod
    def _diff_file_sync(rel_path: str, path: Path) -> tuple[str, str, str]:
        """Synchronous diff computation in worker thread."""

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
                capture_output=True,
                text=True,
                cwd="/workspace",
                timeout=5,
            )
            if result.returncode == 0:
                original = result.stdout
        except Exception:
            pass

        try:
            diff_result = subprocess.run(
                ["git", "diff", "HEAD", "--", rel_path],
                capture_output=True,
                text=True,
                cwd="/workspace",
                timeout=5,
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
            # Build comment protos for this item
            active_comments = moderator.board.get_active_comments(item.get("id", ""))
            proto_comments = [
                agent_pb2.BoardComment(
                    comment_id=c.get("comment_id", ""),
                    item_id=c.get("item_id", ""),
                    author=c.get("author", ""),
                    category=c.get("category", ""),
                    content=c.get("content", ""),
                    ts=c.get("ts", 0),
                    archived=c.get("archived", False),
                )
                for c in active_comments
            ]
            proto_items.append(
                agent_pb2.BoardItem(
                    id=item.get("id", ""),
                    type=item.get("type", ""),
                    title=item.get("title", ""),
                    description=item.get("description", ""),
                    status=item.get("status", ""),
                    assigned_to=item.get("assigned_to") or "",
                    created_at=item.get("created_at", 0.0),
                    comments=proto_comments,
                    last_reason=item.get("last_reason", ""),
                )
            )
        return agent_pb2.BoardResponse(items=proto_items)

    async def GetBoardItem(self, request, context):
        """Return a single board item with its full comment thread."""
        moderator = await self._get_moderator(request.session_id)
        if not moderator or not moderator.board:
            await context.abort(grpc.StatusCode.NOT_FOUND, "Board not available")

        item_id = request.item_id
        item = next((i for i in moderator.board.items if i["id"] == item_id), None)
        if not item:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"Item {item_id} not found")

        all_comments = moderator.board.comments.get(item_id, [])
        proto_comments = [
            agent_pb2.BoardComment(
                comment_id=c.get("comment_id", ""),
                item_id=c.get("item_id", ""),
                author=c.get("author", ""),
                category=c.get("category", ""),
                content=c.get("content", ""),
                ts=c.get("ts", 0),
                archived=c.get("archived", False),
            )
            for c in all_comments
        ]

        proto_item = agent_pb2.BoardItem(
            id=item.get("id", ""),
            type=item.get("type", ""),
            title=item.get("title", ""),
            description=item.get("description", ""),
            status=item.get("status", ""),
            assigned_to=item.get("assigned_to") or "",
            created_at=item.get("created_at", 0.0),
            last_reason=item.get("last_reason", ""),
        )
        return agent_pb2.BoardItemDetail(
            item=proto_item,
            comments=proto_comments,
        )

    # ── Session Management (all async) ────────────────────────────

    async def ListSessions(self, request, context):
        """List sessions via FlussClient. Direct await — no bridging."""
        try:
            sessions = await self.fluss.list_sessions()
            return agent_pb2.SessionListResponse(
                sessions=[
                    agent_pb2.SessionEntry(
                        session_id=s["session_id"],
                        title=s["title"],
                        created_at=s["created_at"],
                        last_active_at=s["last_active_at"],
                    )
                    for s in sessions
                ]
            )
        except Exception as e:
            print(f"❌ [Agent] ListSessions Error: {e}")
            await context.abort(grpc.StatusCode.INTERNAL, str(e))

    async def GetHistory(self, request, context):
        """Fetch full chat history from Fluss (the complete, untrimmed log).

        Always reads from Fluss rather than the in-memory context buffer,
        because ContextManager.trim() evicts old messages to keep the LLM
        context window bounded. The audit trail must be complete.

        Applies two filters:
        1. Deduplication by (actor_id, content) — the reconciler's scanner can
           re-add events that the publisher callback already inserted, because
           the chatroom schema lacks an event_id column (dedup keys diverge).
        2. Telemetry exclusion — raw stdout byte-chunks are for Snorkel's
           real-time streaming, not conversation history.
        """
        session_id = request.session_id
        try:
            raw_events = await self.fluss.fetch_history(session_id)
            seen = set()
            events = []
            for e in raw_events:
                e_type = e["type"]
                if e_type == "telemetry":
                    continue
                dedup_key = (e["actor_id"], e["content"])
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                events.append(
                    agent_pb2.ActivityEvent(
                        timestamp=ms_to_iso(e["ts"]),
                        type=e_type,
                        content=e["content"],
                        actor_id=e["actor_id"],
                    )
                )
            return agent_pb2.HistoryResponse(events=events)
        except Exception as e:
            print(f"❌ [Agent] GetHistory Error: {e}")
            await context.abort(grpc.StatusCode.INTERNAL, str(e))

    async def CreateSession(self, request, context):
        """Create a new session via FlussClient. Direct await — no bridging."""
        session_id = str(uuid.uuid4())
        title = request.title or f"Chat {session_id[:8]}"
        runtime_image = getattr(request, "runtime_image", "") or ""
        execution_mode = getattr(request, "execution_mode", "") or ""
        print(f"🆕 [Agent] Creating session: {title} ({session_id})")
        if runtime_image or execution_mode:
            print(
                f"    Runtime: {runtime_image or '(default)'}, Mode: {execution_mode or '(default)'}"
            )

        # Store per-session config for deferred moderator init
        self._session_configs[session_id] = {
            "runtime_image": runtime_image,
            "execution_mode": execution_mode,
        }

        try:
            result = await self.fluss.create_session(session_id, title)

            # ── Auto-Drop Default Anchor ──
            default_anchor = config.CONFIG.get_default_anchor()
            if default_anchor:
                print(
                    f"⚓ [Agent] Auto-dropping default anchor for session: {session_id}"
                )
                await self.fluss.set_anchor(session_id, default_anchor)

            return agent_pb2.SessionEntry(
                session_id=result["session_id"],
                title=result["title"],
                created_at=result["created_at"],
                last_active_at=result["last_active_at"],
            )
        except Exception as e:
            print(f"❌ [Agent] CreateSession Error: {e}")
            await context.abort(grpc.StatusCode.INTERNAL, str(e))

    async def HaltSession(self, request, context):
        """Gracefully halt a running session by terminating its reconciler."""
        session_id = request.session_id
        if session_id in self.reconcilers:
            print(f"🛑 [Agent] Halting session {session_id} upon request.")
            self.reconcilers[session_id].halt()
        else:
            print(
                f"⚠️ [Agent] Halt requested for unknown/inactive session: {session_id}"
            )
        return agent_pb2.Empty()


async def serve():
    """Async-native gRPC server — single event loop, no threading bridges."""
    # 1. Connect to Fluss
    fluss_client = FlussClient(config.FLUSS_BOOTSTRAP_SERVERS)
    await fluss_client.connect()

    # 2. Start async gRPC server
    server = grpc.aio.server()
    agent_service = AgentService(fluss_client)
    agent_pb2_grpc.add_AgentServiceServicer_to_server(agent_service, server)
    server.add_insecure_port("0.0.0.0:50051")
    await server.start()
    print("🚀 Agent gRPC Server Online (async) on port 50051.")

    # Graceful shutdown handler

    loop = asyncio.get_running_loop()

    async def shutdown(sig):
        print(f"🛑 Received {sig.name}, shutting down server gracefully...")
        agent_service.is_running = False
        await server.stop(grace=1)
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        [task.cancel() for task in tasks]
        loop.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(s)))

    await server.wait_for_termination()


if __name__ == "__main__":
    try:
        asyncio.run(serve())
    except asyncio.CancelledError:
        pass
