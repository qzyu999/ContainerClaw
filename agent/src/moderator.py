import asyncio
import pyarrow as pa
from typing import List

import config
from fluss_client import FlussClient
from publisher import FlussPublisher
from context import ContextManager
from election import ElectionProtocol
from tool_executor import ToolExecutor
from agent import GeminiAgent

from tools import ToolDispatcher
from commands import create_default_dispatcher

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
        self.last_replayed_offset = 0

        self.command_dispatcher = create_default_dispatcher()

        # Decomposed components
        self.context = ContextManager()
        self.election = ElectionProtocol()
        self.executor = None  # Initialized in run() after tool_dispatcher check
        self.publisher = None  # Initialized in run() after _handle_single_message is bound

    # ── Fluss I/O ──────────────────────────────────────────────────

    async def publish(self, actor_id, content, m_type="output",
                      tool_name="", tool_success=False, parent_actor=""):
        """Publish a message via the batched FlussPublisher."""
        try:
            await self.publisher.publish(
                actor_id=actor_id,
                content=content,
                m_type=m_type,
                tool_name=tool_name,
                tool_success=tool_success,
                parent_actor=parent_actor,
            )
            print(f"📝 [Moderator] Published: {actor_id} ({m_type})")
        except Exception as e:
            print(f"❌ [Moderator] Failed to publish: {e}")
            import traceback
            traceback.print_exc()

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

        if self.fluss:
            self.scanner = await self.fluss.create_scanner(self.table, start_ts=start_ts if start_ts > 0 else None)
        else:
            self.scanner = await self.table.new_scan().create_record_batch_log_scanner()
            for b in range(16):
                self.scanner.subscribe(bucket_id=b, start_offset=0)

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

    async def _handle_single_message(self, actor_id, content, ts) -> bool:
        """Process a single message. Returns True if human message (non-command)."""
        if not self.context.add_message(actor_id, content, ts):
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

        if batches:
            self.context.sort()
            self.context.trim()

        return any_human_interrupted

    async def _poll_once(self) -> bool:
        """Poll the Fluss scanner once and process. Returns True if human interrupted."""
        batches = await FlussClient.poll_async(self.scanner, timeout_ms=600)
        return await self._process_batches(batches)

    # ── Main Orchestration Loop ────────────────────────────────────

    async def run(self, autonomous_steps=0):
        """Main moderator loop: poll → elect → execute → repeat."""
        self.base_budget = autonomous_steps
        self.current_steps = 0

        # Initialize FlussPublisher with immediate memory callback
        self.publisher = FlussPublisher(
            self.table,
            self.session_id,
            on_message=self._handle_single_message,
        )
        await self.publisher.start()

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
        await self.publish("Moderator", f"Multi-Agent System Online. ConchShell: {conchshell_status}.", "thought")
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

                await asyncio.sleep(1.0)
                context_window = self.context.get_window()

                # Election
                winner, election_log, is_job_done = await self.election.run_election(
                    self.agents, self.roster_str, context_window, self.publish
                )

                await self.publish("Moderator", f"Election Summary:\n{election_log}", "voting")

                if is_job_done:
                    print("🎉 [Moderator] Job is complete! Pausing the multi-agent loop.")
                    await self.publish("Moderator", "Consensus: Task Complete.", "finish")
                    if self.tool_dispatcher:
                        self.tool_dispatcher.cleanup()
                    self.current_steps = 0
                    continue

                if winner:
                    winning_agent = next(a for a in self.agents if a.agent_id == winner)
                    print(f"🧠 [Moderator] {winner} won the election. Executing...")
                    await self.publish("Moderator", f"🏆 Winner: {winner}", "thought")

                    # Execution
                    if self.executor:
                        resp = await self.executor.execute_with_tools(
                            winning_agent,
                            check_halt_fn=lambda: self.current_steps == 0,
                        )
                    else:
                        resp = await ToolExecutor.execute_text_only(
                            winning_agent, self.context.get_window
                        )

                    if resp and "[WAIT]" not in resp:
                        print(f"📢 [{winner} says]: {resp}")
                        await self.publish(winner, resp, "output")
                    else:
                        print(f"💤 [{winner}] chose to WAIT or failed to respond. Nudging...")
                        await self.publish("Moderator", f"💤 {winner} is waiting. Nudging...", "thought")
                        nudge_text = f"@{winner}, you won the election but chose to WAIT. Could you briefly explain why so the team knows what you're waiting for?"
                        await self.publish("Moderator", nudge_text, "system")
                        await self._poll_once()
                        nudge_context = self.context.get_window()
                        resp = await winning_agent._think(nudge_context)

                        if resp:
                            print(f"📢 [{winner} explanation]: {resp}")
                            await self.publish(winner, resp, "output")
                        else:
                            print(f"❌ [{winner}] remains silent after nudge.")

                await self.publish("Moderator", "Cycle complete.", "checkpoint")

            await asyncio.sleep(1)