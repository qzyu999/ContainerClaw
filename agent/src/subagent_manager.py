"""
SubagentManager: Lifecycle management for parallel subagents.

Bridges the CNS (main chatroom's vote-based loop) and the ganglia
(autonomous subagent tasks). Each subagent is an AgentContext running
as an asyncio.Task with its own context window, tools, and publisher.

Subagents publish directly to the shared Fluss log — the UI, Discord,
and main moderator see their output in real-time. Convergence happens
naturally through the election protocol when subagent results appear
in the main context window.

Usage:
    manager = SubagentManager(fluss_client, table, session_id, publisher)
    task_id = await manager.spawn("Refactor auth", agent, tools, timeout_s=120)
    status = manager.get_status()               # dict of active subagents
    await manager.cancel(task_id)                # graceful stop
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field

import config
from agent_context import AgentContext
from fluss_client import FlussClient
from tool_executor import ToolExecutor
from tools import Tool, ToolDispatcher

from agent import LLMAgent


@dataclass
class SubagentHandle:
    """Tracking state for a running subagent."""

    task_id: str
    agent_id: str
    description: str
    context: AgentContext
    task: asyncio.Task
    started_at: float = field(default_factory=time.time)
    status: str = "running"


class SubagentManager:
    """Manages lifecycle of spawned subagents.

    Each subagent is an asyncio.Task wrapping an AgentContext.
    The manager tracks active tasks, enforces timeouts and concurrency
    limits, and publishes convergence results to the main stream.
    """

    MAX_CONCURRENT = 5

    def __init__(
        self,
        fluss_client: FlussClient,
        table,
        session_id: str,
        publisher,
        provider: str = "",
        model: str = "",
    ):
        self.fluss = fluss_client
        self.table = table
        self.session_id = session_id
        self.publisher = publisher  # Main stream publisher (for system messages)
        self.provider = provider or config.CONFIG.default_provider
        self.model = model or config.DEFAULT_MODEL
        self._active: dict[str, SubagentHandle] = {}
        self._file_locks: dict[str, str] = {}  # path → task_id

    async def spawn(
        self,
        task_desc: str,
        agent_persona: str = "General-purpose software engineer",
        tool_names: list[str] | None = None,
        available_tools: list[Tool] | None = None,
        timeout_s: int | None = None,
        parent_event_id: str = "",
    ) -> str:
        """Spawn a new subagent. Returns a task_id for tracking.

        Args:
            task_desc: Natural-language description of the subtask.
            agent_persona: Persona string for the spawned agent.
            tool_names: Optional list of tool names to scope access.
                        If None, all available_tools are granted.
            available_tools: Pool of Tool instances to select from.
            timeout_s: Maximum wall-clock time before forced termination.
            parent_event_id: The event_id of the delegate tool call that
                             triggered this spawn (for SPAWN edge).

        Returns:
            task_id (8-char UUID prefix) for status queries.

        Raises:
            RuntimeError if max concurrent limit is reached.
        """
        if timeout_s is None:
            timeout_s = config.CONFIG.subagent_ttl_seconds

        if len(self._active) >= self.MAX_CONCURRENT:
            raise RuntimeError(
                f"Max concurrent subagents ({self.MAX_CONCURRENT}) reached. "
                f"Wait for active tasks to finish or cancel one."
            )

        task_id = str(uuid.uuid4())[:8]
        agent_id = f"Sub/{task_id}"

        # Create a fresh agent with the requested persona
        agent = LLMAgent(
            agent_id, agent_persona, provider=self.provider, model=self.model
        )

        # Build tool scope
        tools = available_tools or []
        if tool_names and tools:
            tools = [t for t in tools if t.name in tool_names]

        # Create scoped ToolDispatcher for this subagent
        dispatcher = ToolDispatcher({agent_id: tools}) if tools else None

        # Create the isolated context
        ctx = AgentContext.create(
            agent=agent,
            session_id=self.session_id,
            fluss_client=self.fluss,
            table=self.table,
            tool_dispatcher=dispatcher,
            parent_actor=f"Subagent/{task_id}",
        )
        await ctx.start()

        # System announcement — this is a SPAWN edge
        spawn_event_id = await self.publisher.publish(
            actor_id="Moderator",
            content=f"🔱 Spawned subagent {task_id} ({agent_persona}): {task_desc}",
            m_type="system",
            parent_event_id=parent_event_id,
            edge_type="SPAWN",
        )

        # Launch the autonomous loop as a task, passing spawn_event_id for chaining
        async_task = asyncio.create_task(
            self._run_subagent(
                task_id, ctx, task_desc, timeout_s, parent_event_id=spawn_event_id
            )
        )

        handle = SubagentHandle(
            task_id=task_id,
            agent_id=agent_id,
            description=task_desc,
            context=ctx,
            task=async_task,
        )
        self._active[task_id] = handle

        print(f"🔱 [SubagentManager] Spawned {task_id}: {task_desc}")
        return task_id

    async def _run_subagent(
        self,
        task_id: str,
        ctx: AgentContext,
        task_desc: str,
        timeout_s: int,
        parent_event_id: str = "",
    ):
        """Execute a subagent's autonomous tool-calling loop with timeout."""
        last_event_id = parent_event_id  # Track head of subagent's event chain
        try:
            async with asyncio.timeout(timeout_s):
                # Seed the context with the task
                sys_msg = config.CONFIG.prompts.subagent_spawn.format(
                    task_desc=task_desc
                )
                ctx.context.add_message(
                    "Moderator",
                    sys_msg,
                    int(time.time() * 1000),
                )

                if ctx.tool_dispatcher:
                    # Bridge ToolExecutor's publish_fn signature to ctx's publisher.
                    # ToolExecutor calls: publish_fn(actor_id, content, m_type, **kw)
                    async def _subagent_publish(
                        actor_id, content, m_type="output", **kwargs
                    ):
                        if ctx.publisher:
                            return await ctx.publisher.publish(
                                actor_id=actor_id,
                                content=content,
                                m_type=m_type,
                                **kwargs,
                            )
                        return ""

                    executor = ToolExecutor(
                        ctx.tool_dispatcher,
                        publish_fn=_subagent_publish,
                        get_context_fn=ctx.get_context_window,
                        poll_fn=self._noop_poll,
                    )

                    for round_num in range(config.MAX_TOOL_ROUNDS):
                        result = await executor.execute_with_tools(
                            ctx.agent,
                            check_halt_fn=lambda: not ctx._running,
                            parent_event_id=last_event_id,
                        )
                        if result:
                            last_event_id = await ctx.publish(
                                result,
                                "output",
                                parent_event_id=last_event_id,
                                edge_type="SEQUENTIAL",
                            )
                            if "[DONE]" in result or "[STUCK]" in result:
                                break
                        else:
                            # Agent produced no text — done
                            break
                else:
                    # Text-only subagent (no tools)
                    context = ctx.get_context_window()
                    result = await ctx.agent._think(context)
                    if result:
                        last_event_id = await ctx.publish(
                            result,
                            "output",
                            parent_event_id=last_event_id,
                            edge_type="SEQUENTIAL",
                        )

        except TimeoutError:
            last_event_id = await ctx.publish(
                f"⏰ Subagent {task_id} timed out after {timeout_s}s.",
                "system",
                parent_event_id=last_event_id,
                edge_type="SEQUENTIAL",
            )
            print(f"⏰ [SubagentManager] {task_id} timed out.")
        except asyncio.CancelledError:
            last_event_id = await ctx.publish(
                f"🛑 Subagent {task_id} cancelled.",
                "system",
                parent_event_id=last_event_id,
                edge_type="SEQUENTIAL",
            )
            print(f"🛑 [SubagentManager] {task_id} cancelled.")
        except Exception as e:
            last_event_id = await ctx.publish(
                f"💥 Subagent {task_id} failed: {e}",
                "system",
                parent_event_id=last_event_id,
                edge_type="SEQUENTIAL",
            )
            print(f"💥 [SubagentManager] {task_id} error: {e}")
            import traceback

            traceback.print_exc()
        finally:
            await ctx.stop()
            self.release_locks(task_id)
            handle = self._active.pop(task_id, None)
            if handle:
                handle.status = "completed"

            # Publish convergence event — RETURN edge back to main thread
            await self.publisher.publish(
                actor_id="Moderator",
                content=f"🏁 Subagent {task_id} completed.",
                m_type="convergence",
                parent_event_id=last_event_id,
                edge_type="RETURN",
            )
            print(f"🏁 [SubagentManager] {task_id} finished.")

    @staticmethod
    async def _noop_poll() -> bool:
        """No-op poll for subagents (they don't check for human interrupts)."""
        return False

    # ── Status & Control ────────────────────────────────────────────

    async def cancel(self, task_id: str) -> bool:
        """Cancel a running subagent."""
        handle = self._active.get(task_id)
        if not handle:
            return False
        handle.task.cancel()
        return True

    async def cancel_all(self):
        """Cancel all running subagents."""
        for handle in list(self._active.values()):
            handle.task.cancel()

    def get_status(self) -> str:
        """Return a human-readable summary of active subagents."""
        if not self._active:
            return "No active subagents."
        lines = []
        now = time.time()
        for tid, h in self._active.items():
            elapsed = int(now - h.started_at)
            lines.append(f"  🔱 {tid} ({h.agent_id}): {h.description} [{elapsed}s]")
        return f"Active subagents ({len(self._active)}):\n" + "\n".join(lines)

    # ── Advisory File Locks ─────────────────────────────────────────

    def acquire_lock(self, path: str, task_id: str) -> bool:
        """Acquire an advisory lock on a file path."""
        if path in self._file_locks and self._file_locks[path] != task_id:
            return False
        self._file_locks[path] = task_id
        return True

    def release_locks(self, task_id: str):
        """Release all locks held by a subagent."""
        self._file_locks = {p: t for p, t in self._file_locks.items() if t != task_id}
