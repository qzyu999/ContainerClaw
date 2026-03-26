"""
AgentContext: Isolated execution environment for a single agent.

This is the critical abstraction for subagent support. Each AgentContext
bundles an agent with its own context window, tool dispatcher, and publisher,
enabling independent operation as an asyncio.Task or separate process.

For the main agent loop, the StageModerator creates one context per agent
in the pool. For subagents (future), spawning is simply:

    ctx = AgentContext.create(agent, session_id, fluss_client)
    asyncio.create_task(ctx.run())

Properties:
    - Own context window (doesn't pollute main conversation)
    - Own tool sandbox (via ToolDispatcher)
    - Publishes results to the shared Fluss log (visible to moderator + UI)
    - Can be independently killed/paused via cancellation
"""

from agent import GeminiAgent
from context import ContextManager
from publisher import FlussPublisher
from fluss_client import FlussClient
from tools import ToolDispatcher


class AgentContext:
    """Isolated runtime environment for a single agent.

    Bundles the agent, its context, tools, and a publisher into
    an independently operable unit.

    Args:
        agent: The GeminiAgent instance.
        session_id: Fluss session to publish/subscribe to.
        fluss_client: Shared FlussClient for scanner creation.
        table: Fluss chat table handle.
        tool_dispatcher: Optional ToolDispatcher for tool-augmented execution.
        parent_actor: Attribution for provenance tracking (e.g., "Moderator" or
                      the spawning agent's ID for subagent chains).
    """

    def __init__(
        self,
        agent: GeminiAgent,
        session_id: str,
        fluss_client: FlussClient,
        table,
        tool_dispatcher: ToolDispatcher | None = None,
        parent_actor: str = "",
    ):
        self.agent = agent
        self.session_id = session_id
        self.fluss = fluss_client
        self.table = table
        self.parent_actor = parent_actor

        # Independent components — not shared with other contexts
        self.context = ContextManager()
        self.tool_dispatcher = tool_dispatcher
        self.publisher = None  # Created on start()
        self._scanner = None
        self._running = False

    @property
    def agent_id(self) -> str:
        return self.agent.agent_id

    @property
    def persona(self) -> str:
        return self.agent.persona

    async def start(self):
        """Initialize the publisher and scanner for this context."""
        self.publisher = FlussPublisher(
            self.table,
            self.session_id,
            on_message=self._on_message,
        )
        await self.publisher.start()

        self._scanner = await self.fluss.create_scanner(self.table)
        self._running = True

    async def stop(self):
        """Stop the publisher and mark as not running."""
        self._running = False
        if self.publisher:
            await self.publisher.stop()

    async def publish(self, content: str, m_type: str = "output"):
        """Publish a message from this agent to the shared Fluss log.

        Automatically sets actor_id and parent_actor for provenance tracking.
        """
        if self.publisher:
            await self.publisher.publish(
                actor_id=self.agent_id,
                content=content,
                m_type=m_type,
                parent_actor=self.parent_actor,
            )

    async def _on_message(self, actor_id: str, content: str, ts: int):
        """Callback for immediate memory update from publisher."""
        self.context.add_message(actor_id, content, ts)

    def get_context_window(self) -> list[dict]:
        """Return the context window for this agent."""
        return self.context.get_window()

    @classmethod
    def create(
        cls,
        agent: GeminiAgent,
        session_id: str,
        fluss_client: FlussClient,
        table,
        tool_dispatcher: ToolDispatcher | None = None,
        parent_actor: str = "",
    ) -> "AgentContext":
        """Factory method for creating an AgentContext."""
        return cls(
            agent=agent,
            session_id=session_id,
            fluss_client=fluss_client,
            table=table,
            tool_dispatcher=tool_dispatcher,
            parent_actor=parent_actor,
        )
