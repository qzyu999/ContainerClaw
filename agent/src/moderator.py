import asyncio
from typing import List

import config
import pyarrow as pa
from commands import create_default_dispatcher
from context import ContextManager
from election import ElectionProtocol
from fluss_client import FlussClient
from publisher import FlussPublisher
from tool_executor import ToolExecutor
from tools import ToolDispatcher

from agent import GeminiAgent


class StageModerator:
    def __init__(self, table, agents: List[GeminiAgent],
                 session_id: str,
                 tool_dispatcher: ToolDispatcher | None = None,
                 sessions_table=None,
                 fluss_client: FlussClient | None = None):
        self.table = table
        self.agents = agents
        self.session_id = session_id
        self.tool_dispatcher = tool_dispatcher
        self.sessions_table = sessions_table
        self.fluss = fluss_client
        self.agent_names = [a.agent_id for a in agents]
        self.roster_str = ", ".join([f"{a.agent_id} ({a.persona})" for a in agents])
        
        # Propagate roster to agents for team awareness
        for agent in self.agents:
            agent.roster_str = self.roster_str

        self.last_replayed_offset = 0

        self.command_dispatcher = create_default_dispatcher()

        # Decomposed components
        self.context = ContextManager()
        self.election = ElectionProtocol()
        self.executor = None  # Initialized in run() after tool_dispatcher check
        self.publisher = None  # Initialized in run() after _handle_single_message is bound
        self._publisher_ready = asyncio.Event()
        self._last_human_event_id = ""  # Captured from incoming batches for backbone linking

    # ── Fluss I/O ──────────────────────────────────────────────────

    async def publish(self, actor_id, content, m_type="output",
                      tool_name="", tool_success=False, parent_actor="",
                      parent_event_id="", edge_type="SEQUENTIAL"):
        """Publish a message via the batched FlussPublisher. Returns event_id."""
        try:
            # Wait for publisher if we are in early startup race
            if not self.publisher:
                print("⏳ [Moderator] Waiting for publisher to initialize...")
                try:
                    await asyncio.wait_for(self._publisher_ready.wait(), timeout=10.0)
                except asyncio.TimeoutError:
                    print("❌ [Moderator] Publisher initialization timed out.")
                    return ""

            return await self.publisher.publish(
                actor_id=actor_id,
                content=content,
                m_type=m_type,
                tool_name=tool_name,
                tool_success=tool_success,
                parent_actor=parent_actor,
                parent_event_id=parent_event_id,
                edge_type=edge_type,
            )
        except Exception as e:
            print(f"❌ [Moderator] Failed to publish: {e}")
            import traceback
            traceback.print_exc()
            return ""

    async def _replay_history(self):
        """Replay the Fluss log from session creation time to rebuild context."""
        start_ts = 0
        if self.sessions_table and self.fluss:
            try:
                scanner = await self.fluss.create_scanner(self.sessions_table)
                found = False
                for _ in range(5):
                    batches = await FlussClient.poll_async(scanner, timeout_ms=500)
                    for poll in batches:
                        if poll.num_rows == 0: continue
                        id_arr = poll["session_id"]
                        created_arr = poll["created_at"]
                        for i in range(poll.num_rows):
                            if id_arr[i].as_py() == self.session_id:
                                start_ts = int(created_arr[i].as_py())
                                print(f"📜 [Moderator] Session {self.session_id} started at {start_ts}.")
                                found = True
                                break
                        if found: break
                    if found: break
            except Exception as e:
                print(f"⚠️ [Moderator] Failed to lookup session start time: {e}")
                import traceback
                traceback.print_exc()

        self.scanner = await self.fluss.create_scanner(self.table, start_ts=start_ts if start_ts > 0 else None)

        total_replayed = 0
        while True:
            batches = await FlussClient.poll_async(self.scanner, timeout_ms=500)
            if not batches:
                break

            for poll in batches:
                event_id_arr = poll["event_id"]
                sess_arr = poll["session_id"]
                actor_arr = poll["actor_id"]
                content_arr = poll["content"]
                ts_arr = poll["ts"]

                for i in range(poll.num_rows):
                    if sess_arr[i].as_py() != self.session_id:
                        continue
                    self.context.add_message(
                        actor_arr[i].as_py(),
                        content_arr[i].as_py(),
                        ts_arr[i].as_py(),
                        event_id=event_id_arr[i].as_py(),
                    )
                    total_replayed += 1

        self.context.sort()
        self.last_replayed_offset = total_replayed
        print(f"✅ [Moderator] Replayed {total_replayed} messages from Fluss.")

    # ── Message Processing ─────────────────────────────────────────

    async def _handle_single_message(self, actor_id, content, ts, event_id="", m_type="output") -> bool:
        """Process a single message. Returns True if human message (non-command).

        The event_id kwarg is passed by the publisher's on_message callback,
        allowing us to capture _last_human_event_id at callback time (before
        the scanner path sees it as a duplicate).
        """
        if not self.context.add_message(actor_id, content, ts, event_id=event_id, m_type=m_type):
            return False  # Duplicate

        human_was_message = False
        is_human_source = (actor_id == "Human" or str(actor_id).startswith("Discord/"))

        if is_human_source:
            if actor_id == "Human":
                print(f"📢 [Human said]: {content}")
            else:
                print(f"📢 [Discord said ({actor_id})]: {content}")

            if await self.command_dispatcher.dispatch(content, self):
                pass  # Command handled
            else:
                human_was_message = True
                # Capture event_id for backbone tracking — this fires from the
                # publisher callback (which has event_id) BEFORE the scanner
                # sees it, avoiding the dedup race condition.
                if event_id:
                    self._last_human_event_id = event_id
                if hasattr(self, 'base_budget'):
                    self.current_steps = self.base_budget
                    if self.base_budget != 0:
                        print(f"🔄 [Moderator] Human input detected. Resetting budget to {self.base_budget} steps.")
        elif actor_id in self.agent_names:
            print(f"👂 [Heard] [{actor_id}]: {content}")

        return human_was_message

    async def _process_batches(self, batches) -> bool:
        """Process a list of RecordBatch from async poll. Returns True if human interrupted."""
        any_human_interrupted = False
        for poll in batches:
            if poll.num_rows > 0:
                df = poll.to_pandas()
                for _, row in df.iterrows():
                    sid = row.get("session_id")
                    if sid != self.session_id:
                        continue
                    if await self._handle_single_message(row['actor_id'], row['content'], row['ts']):
                        any_human_interrupted = True
                        # Capture the human event's ID to become the next backbone parent
                        self._last_human_event_id = row.get('event_id', '')

        if batches:
            self.context.sort()
            self.context.trim()

        return any_human_interrupted

    async def _poll_once(self) -> bool:
        """Poll the Fluss scanner once and process. Returns True if human interrupted."""
        batches = await FlussClient.poll_async(self.scanner, timeout_ms=600)
        return await self._process_batches(batches)

    # ── Main Orchestration Loop ────────────────────────────────────

    def _build_session_context(self) -> str:
        """Build a session context block for agent system prompts.

        This gives every agent full situational awareness about the
        current session's environment. Cost: ~80-120 tokens per prompt.
        """
        sandbox_mgr = getattr(self, 'sandbox_mgr', None)
        if not sandbox_mgr:
            return ""

        mode_desc = {
            "native": "local subprocess on the host machine",
            "implicit_proxy": f"Docker sidecar container ({sandbox_mgr.default_target})",
            "explicit_orchestrator": "dynamically provisioned Docker containers",
        }
        return (
            f"## Session Context\n"
            f"- **Session:** {self.session_id[:8]}\n"
            f"- **Runtime:** {mode_desc.get(sandbox_mgr.mode, 'unknown')}\n"
            f"- **Workspace:** {sandbox_mgr.workspace_root}\n"
            f"- **Crew:** {self.roster_str}\n"
        )

    async def run(self, autonomous_steps=0):
        """Main moderator loop: poll → elect → execute → repeat."""
        self.base_budget = autonomous_steps
        self.current_steps = 0
        self._last_human_event_id = ""  # Captured from incoming batches for backbone linking

        # ── Inject Session Context into Agent Prompts ──
        session_context = self._build_session_context()
        if session_context:
            for agent in self.agents:
                agent.session_context = session_context
            print(f"📋 [Moderator] Session context injected into {len(self.agents)} agents.")

        # Initialize FlussPublisher with immediate memory callback
        self.publisher = FlussPublisher(
            self.table,
            self.session_id,
            on_message=self._handle_single_message,
        )
        await self.publisher.start()
        self._publisher_ready.set()

        # Wire SubagentManager publisher (deferred from main.py)
        if hasattr(self, 'subagent_manager') and self.subagent_manager:
            self.subagent_manager.publisher = self.publisher

        # Initialize ToolExecutor with callbacks (deferred to here so publish is bound)
        if self.tool_dispatcher:
            self.executor = ToolExecutor(
                self.tool_dispatcher,
                publish_fn=self.publish,
                get_context_fn=self.context.get_window,
                poll_fn=self._poll_once,
            )

        await self._replay_history()

        conchshell_status = "enabled" if self.tool_dispatcher else "disabled"

        # Boot event — the backbone starts here (ROOT edge, no parent)
        backbone_id = await self.publish(
            "Moderator",
            f"Multi-Agent System Online. ConchShell: {conchshell_status}.",
            "thought",
            parent_event_id="",
            edge_type="ROOT",
        )

        print(f"⚖️ [Moderator] Active with agents: {self.agent_names}")
        print(f"🐚 [Moderator] ConchShell: {conchshell_status}")
        if self.base_budget != 0:
            print(f"🤖 [Moderator] Autonomous Mode: {self.base_budget} steps.")

        if self.last_replayed_offset > 0 and self.base_budget != 0:
            self.current_steps = self.base_budget
            print(f"🔄 [Moderator] Resuming autonomous mode from replayed history ({self.last_replayed_offset} msgs).")
        else:
            self.current_steps = 0

        while True:
            batches = await FlussClient.poll_async(self.scanner, timeout_ms=500)
            human_interrupted = await self._process_batches(batches)

            if human_interrupted or (self.current_steps != 0):
                if not human_interrupted:
                    if self.current_steps > 0:
                        self.current_steps -= 1
                    print(f"🤖 [Autonomous Turn] {self.current_steps if self.current_steps >= 0 else 'inf'} steps remaining...")

                # If a human triggered this cycle, their event IS the backbone parent
                if self._last_human_event_id:
                    backbone_id = self._last_human_event_id
                    self._last_human_event_id = ""  # Consume it

                await asyncio.sleep(1.0)
                
                # ── Anchor Fetch ──────────────────────────────────
                # Fetch latest human steering directive before each logic cycle
                anchor_text = await self.fluss.fetch_latest_anchor(self.session_id)
                for agent in self.agents:
                    agent.anchor_text = anchor_text
                
                context_window = self.context.get_window()

                # ── Election ──────────────────────────────────────
                # Election start is a child of the backbone
                election_start_id = await self.publish(
                    "Moderator", "🗳️ Starting Election...", "thought",
                    parent_event_id=backbone_id,
                    edge_type="SEQUENTIAL",
                )

                winner, election_log, is_job_done = await self.election.run_election(
                    self.agents, self.roster_str, context_window, self.publish,
                    parent_event_id=election_start_id,
                )

                # Tally + Summary: children of election-start (side branches, not backbone)
                await self.publish(
                    "Moderator", f"Election Summary:\n{election_log}", "voting",
                    parent_event_id=election_start_id,
                    edge_type="SEQUENTIAL",
                )

                if is_job_done:
                    print("🎉 [Moderator] Job is complete! Pausing the multi-agent loop.")
                    backbone_id = await self.publish(
                        "Moderator", "Consensus: Task Complete.", "finish",
                        parent_event_id=backbone_id,
                        edge_type="SEQUENTIAL",
                    )
                    if self.tool_dispatcher:
                        self.tool_dispatcher.cleanup()
                    self.current_steps = 0
                    continue

                if winner:
                    winning_agent = next(a for a in self.agents if a.agent_id == winner)
                    print(f"🧠 [Moderator] {winner} won the election. Executing...")

                    # Winner announcement: child of election-start
                    winner_id = await self.publish(
                        "Moderator", f"🏆 Winner: {winner}", "thought",
                        parent_event_id=election_start_id,
                        edge_type="SEQUENTIAL",
                    )

                    # ── Agent Execution ───────────────────────────
                    # Pass winner_id down; tool calls chain from it
                    if self.executor:
                        resp = await self.executor.execute_with_tools(
                            winning_agent,
                            check_halt_fn=lambda: self.current_steps == 0,
                            parent_event_id=winner_id,
                        )
                    else:
                        resp = await ToolExecutor.execute_text_only(
                            winning_agent, self.context.get_window
                        )

                    if resp and "[WAIT]" not in resp:
                        print(f"📢 [{winner} says]: {resp}")
                        # Agent output advances the backbone
                        backbone_id = await self.publish(
                            winner, resp, "output",
                            parent_event_id=winner_id,
                            edge_type="SEQUENTIAL",
                        )
                    else:
                        print(f"💤 [{winner}] chose to WAIT or failed to respond. Nudging...")
                        await self.publish(
                            "Moderator", f"💤 {winner} is waiting. Nudging...", "thought",
                            parent_event_id=winner_id,
                            edge_type="SEQUENTIAL",
                        )
                        nudge_text = f"@{winner}, you won the election but chose to WAIT. Could you briefly explain why so the team knows what you're waiting for?"
                        await self.publish(
                            "Moderator", nudge_text, "system",
                            parent_event_id=winner_id,
                            edge_type="SEQUENTIAL",
                        )
                        await self._poll_once()
                        nudge_context = self.context.get_window()
                        resp = await winning_agent._think(nudge_context)

                        if resp:
                            print(f"📢 [{winner} explanation]: {resp}")
                            backbone_id = await self.publish(
                                winner, resp, "output",
                                parent_event_id=winner_id,
                                edge_type="SEQUENTIAL",
                            )
                        else:
                            print(f"❌ [{winner}] remains silent after nudge.")

                # ── Checkpoint ────────────────────────────────────
                # Closes this cycle, becomes the backbone head for the next
                backbone_id = await self.publish(
                    "Moderator", "Cycle complete.", "checkpoint",
                    parent_event_id=backbone_id,
                    edge_type="SEQUENTIAL",
                )

            await asyncio.sleep(1)