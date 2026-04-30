"""
ReconciliationController: State-machine-driven moderator loop.

Replaces the imperative poll→elect→execute pipeline with a
reconciliation loop where:
  1. The loop body NEVER blocks (all work dispatched as tasks)
  2. Commands are always responsive (processed every tick)
  3. State transitions are explicit and auditable
  4. The heartbeat proves liveness to external observers
  5. The causal backbone (parent_event_id) is threaded through
     every publish call for deterministic DAG reconstruction

The controller wraps the existing StageModerator components (election,
context, publisher, executor) and re-sequences them around a state
machine rather than a sequential pipeline.

States:
  IDLE        - Awaiting human input or autonomous budget > 0
  ELECTING    - Election in progress (async task)
  EXECUTING   - Agent executing with tools (async task)
  PUBLISHING  - Agent response being published
  SUSPENDED   - /stop received, system quiescent

Backbone Tracking:
  self.backbone_id holds the event_id of the most recent
  backbone-advancing event. It is updated at exactly four points:
    1. Boot event ("System Online")
    2. Human message arrival (captured from incoming Fluss batch)
    3. Agent final output
    4. Cycle checkpoint

Usage:
    controller = ReconciliationController(moderator, heartbeat_emitter)
    await controller.run(autonomous_steps=5)
"""

import asyncio
import enum

from fluss_client import FlussClient


class State(enum.Enum):
    IDLE = "idle"
    ELECTING = "electing"
    EXECUTING = "executing"
    PUBLISHING = "publishing"
    SUSPENDED = "suspended"


class ReconciliationController:
    """State-machine wrapper over StageModerator.

    The controller owns the main loop and dispatches election and
    execution as independent asyncio.Tasks. The loop body itself
    is guaranteed to complete in < 50ms (the poll timeout + processing).

    The controller does NOT replace the moderator — it delegates to
    the moderator's components (election, executor, publisher, context).
    """

    def __init__(self, moderator, heartbeat_emitter=None):
        self.mod = moderator
        self.heartbeat = heartbeat_emitter
        self.state = State.IDLE

        # Head of the linear backbone — the event_id of the most recent
        # backbone-advancing event. Promoted to instance attribute because
        # _run_election_and_execute() runs in a separate asyncio.Task
        # scope and cannot access run()'s local variables.
        self.backbone_id: str = ""

        # Lazy boot: the boot event is deferred until first activation
        # to avoid ghost boot events from placeholder sessions.
        self._booted: bool = False

        # Tracked async task for cancellation
        self._election_task: asyncio.Task | None = None

        # Buffer for human interrupts that arrive during election/execution.
        # The stream is consumed once — if we don't remember the interrupt,
        # it's lost by the time the state returns to IDLE.
        self._pending_human_interrupt = False

        # Permanent halt flag — once set, the main loop exits.
        # Distinguished from SUSPENDED (which is resumable via human message).
        self._halted: bool = False

    async def run(self, autonomous_steps: int = 0):
        """Main reconciliation loop. Never blocks."""
        self.mod.base_budget = autonomous_steps
        self.mod.current_steps = 0

        # Initialize publisher and executor (same as moderator.run)
        from publisher import FlussPublisher
        from tool_executor import ToolExecutor

        self.mod.publisher = FlussPublisher(
            self.mod.table,
            self.mod.session_id,
            on_message=self.mod._handle_single_message,
        )
        await self.mod.publisher.start()

        # Wire SubagentManager publisher
        if hasattr(self.mod, "subagent_manager") and self.mod.subagent_manager:
            self.mod.subagent_manager.publisher = self.mod.publisher

        # Initialize ToolExecutor
        if self.mod.tool_dispatcher:
            self.mod.executor = ToolExecutor(
                self.mod.tool_dispatcher,
                publish_fn=self.mod.publish,
                get_context_fn=self.mod.context.get_window,
                poll_fn=self.mod._poll_once,
            )

        await self.mod._replay_history()

        print(f"⚖️ [Reconciler] Active with agents: {self.mod.agent_names}")

        if self.mod.base_budget != 0:
            print(f"🤖 [Reconciler] Autonomous Mode: {self.mod.base_budget} steps.")

        # Resume from replayed history if autonomous
        if self.mod.last_replayed_offset > 0 and self.mod.base_budget != 0:
            self.mod.current_steps = self.mod.base_budget
            print(
                f"🔄 [Reconciler] Resuming from replayed history ({self.mod.last_replayed_offset} msgs)."
            )
        else:
            self.mod.current_steps = 0

        # Start heartbeat
        if self.heartbeat:
            await self.heartbeat.start()

        # ── The Reconciliation Loop ──
        # Invariant: this loop body completes in < 600ms (poll timeout + processing)
        while not self._halted:
            try:
                # 1. Poll stream (non-blocking, bounded by timeout_ms)
                batches = await FlussClient.poll_async(self.mod.scanner, timeout_ms=500)
                human_interrupted = await self.mod._process_batches(batches)

                # 2. Update heartbeat state
                if self.heartbeat:
                    self.heartbeat.update_state(self.state.value)

                # 3. State-driven reconciliation
                match self.state:
                    case State.IDLE:
                        if self._should_activate(human_interrupted):
                            self._pending_human_interrupt = False  # Consumed
                            await self._ensure_booted()
                            # Capture human event as backbone head before dispatching
                            if self.mod._last_human_event_id:
                                self.backbone_id = self.mod._last_human_event_id
                                self.mod._last_human_event_id = ""
                            self.state = State.ELECTING
                            self._election_task = asyncio.create_task(
                                self._run_election_and_execute()
                            )

                    case State.ELECTING | State.EXECUTING | State.PUBLISHING:
                        # Buffer human interrupts that arrive mid-cycle
                        if human_interrupted:
                            self._pending_human_interrupt = True
                            print(
                                "📌 [Reconciler] Human message buffered — will trigger after current cycle."
                            )
                        # Check if the dispatched task has completed
                        if self._election_task and self._election_task.done():
                            self._election_task = None
                            self.state = State.IDLE
                            self.backbone_id = await self.mod.publish(
                                "Moderator",
                                "Cycle complete.",
                                "checkpoint",
                                parent_event_id=self.backbone_id,
                                edge_type="SEQUENTIAL",
                            )

                    case State.SUSPENDED:
                        # If permanently halted, don't respond to anything
                        if self._halted:
                            continue
                        # A human message should always wake the system up
                        # and immediately trigger an election (the message was
                        # already consumed from the stream — waiting for the
                        # next tick would miss it)
                        if human_interrupted:
                            await self._ensure_booted()
                            if self.mod._last_human_event_id:
                                self.backbone_id = self.mod._last_human_event_id
                                self.mod._last_human_event_id = ""
                            self.state = State.ELECTING
                            self._election_task = asyncio.create_task(
                                self._run_election_and_execute()
                            )
                            print(
                                "🔄 [Reconciler] Human message — SUSPENDED → ELECTING."
                            )
                        # Automation budget restored via /automation=N
                        elif self.mod.base_budget != 0 and self.mod.current_steps != 0:
                            self.state = State.IDLE
                            print(
                                "🔄 [Reconciler] Budget restored — exiting SUSPENDED → IDLE."
                            )

                # 4. Yield to event loop (minimal sleep to prevent spin)
                await asyncio.sleep(0.1)

            except asyncio.CancelledError:
                print("🛑 [Reconciler] Cancelled.")
                break
            except Exception as e:
                print(f"❌ [Reconciler] Error in main loop: {e}")
                import traceback

                traceback.print_exc()
                await asyncio.sleep(1)

        # Cleanup
        print(
            f"🏁 [Reconciler] Loop exited for session {self.mod.session_id}. Halted={self._halted}"
        )
        if self.heartbeat:
            await self.heartbeat.stop()

    def _should_activate(self, human_interrupted: bool) -> bool:
        """Decide whether to start an election cycle."""
        if human_interrupted:
            return True
        if self._pending_human_interrupt:
            return True
        if self.mod.current_steps != 0:
            return True
        return False

    async def _run_election_and_execute(self):
        """Run election + execution as a single async task.

        This is dispatched from the main loop as an asyncio.Task,
        so the main loop continues to poll, process commands, and
        emit heartbeats while this runs.

        Backbone threading: every publish() call passes parent_event_id
        and edge_type to maintain the deterministic causal DAG.
        """
        try:
            if self.heartbeat:
                self.heartbeat.update_state("electing")

            # Decrement autonomous budget
            if not self._was_human_trigger():
                if self.mod.current_steps > 0:
                    self.mod.current_steps -= 1
                print(
                    f"🤖 [Autonomous Turn] "
                    f"{self.mod.current_steps if self.mod.current_steps >= 0 else 'inf'} "
                    f"steps remaining..."
                )

            # ── Anchor Fetch ──────────────────────────────────
            # Fetch latest human steering directive before each logic cycle
            anchor_text = await self.mod.fluss.fetch_latest_anchor(self.mod.session_id)
            for agent in self.mod.agents:
                agent.anchor_text = anchor_text

            # Election start — branches from backbone
            election_start_id = await self.mod.publish(
                "Moderator",
                "🗳️ Starting Election...",
                "thought",
                parent_event_id=self.backbone_id,
                edge_type="SEQUENTIAL",
            )
            self.backbone_id = election_start_id

            await asyncio.sleep(1.0)
            context_window = self.mod.context.get_window()

            # Election — pass parent so internal round messages can chain
            winner, election_log, is_job_done = await self.mod.election.run_election(
                self.mod.agents,
                self.mod.roster_str,
                context_window,
                self.mod.publish,
                parent_event_id=election_start_id,
            )

            # Summary: child of election-start (collapsible detail, not backbone)
            await self.mod.publish(
                "Moderator",
                f"Election Summary:\n{election_log}",
                "voting",
                parent_event_id=election_start_id,
                edge_type="SEQUENTIAL",
            )

            if is_job_done:
                print("🎉 [Reconciler] Job is complete!")
                self.backbone_id = await self.mod.publish(
                    "Moderator",
                    "Consensus: Task Complete.",
                    "finish",
                    parent_event_id=self.backbone_id,
                    edge_type="SEQUENTIAL",
                )
                if self.mod.tool_dispatcher:
                    self.mod.tool_dispatcher.cleanup()
                self.mod.current_steps = 0
                return

            if winner:
                self.state = State.EXECUTING
                if self.heartbeat:
                    self.heartbeat.update_state("executing", f"Agent: {winner}")

                winning_agent = next(a for a in self.mod.agents if a.agent_id == winner)
                print(f"🧠 [Reconciler] {winner} won. Executing...")

                # Winner announcement: child of election-start
                winner_id = await self.mod.publish(
                    "Moderator",
                    f"🏆 Winner: {winner}",
                    "thought",
                    parent_event_id=election_start_id,
                    edge_type="SEQUENTIAL",
                )
                self.backbone_id = winner_id

                # Execution — pass winner_id so tool calls chain from it
                if self.mod.executor:
                    resp = await self.mod.executor.execute_with_tools(
                        winning_agent,
                        check_halt_fn=lambda: self.mod.current_steps == 0,
                        parent_event_id=winner_id,
                    )
                else:
                    from tool_executor import ToolExecutor

                    resp = await ToolExecutor.execute_text_only(
                        winning_agent, self.mod.context.get_window
                    )

                self.state = State.PUBLISHING
                if self.heartbeat:
                    self.heartbeat.update_state("publishing")

                if resp and "[WAIT]" not in resp:
                    print(f"📢 [{winner} says]: {resp}")
                    # Agent output advances the backbone
                    self.backbone_id = await self.mod.publish(
                        winner,
                        resp,
                        "output",
                        parent_event_id=winner_id,
                        edge_type="SEQUENTIAL",
                    )
                else:
                    print(f"💤 [{winner}] chose to WAIT. Nudging...")
                    await self.mod.publish(
                        "Moderator",
                        f"💤 {winner} is waiting. Nudging...",
                        "thought",
                        parent_event_id=winner_id,
                        edge_type="SEQUENTIAL",
                    )
                    nudge_text = (
                        f"@{winner}, you won the election but chose to WAIT. "
                        f"Could you briefly explain why so the team knows what you're waiting for?"
                    )
                    await self.mod.publish(
                        "Moderator",
                        nudge_text,
                        "system",
                        parent_event_id=winner_id,
                        edge_type="SEQUENTIAL",
                    )
                    await self._poll_once()
                    nudge_context = self.mod.context.get_window()
                    resp = await winning_agent._think(nudge_context)
                    if resp:
                        print(f"📢 [{winner} explanation]: {resp}")
                        self.backbone_id = await self.mod.publish(
                            winner,
                            resp,
                            "output",
                            parent_event_id=winner_id,
                            edge_type="SEQUENTIAL",
                        )

        except asyncio.CancelledError:
            print("🛑 [Reconciler] Election/execution cancelled.")
            raise
        except Exception as e:
            print(f"❌ [Reconciler] Election/execution error: {e}")
            import traceback

            traceback.print_exc()
        finally:
            if self.heartbeat:
                self.heartbeat.update_state("idle")

    async def _ensure_booted(self):
        """Publish the ROOT boot event on first activation (lazy init).

        By deferring the boot event to the first real activation (human
        message or autonomous trigger), we avoid ghost boot events from
        placeholder sessions like 'user-session' that never receive traffic.
        """
        if self._booted:
            return
        self._booted = True

        conchshell_status = "enabled" if self.mod.tool_dispatcher else "disabled"
        self.backbone_id = await self.mod.publish(
            "Moderator",
            f"Multi-Agent System Online (Reconciliation Mode). ConchShell: {conchshell_status}.",
            "thought",
            parent_event_id="",
            edge_type="ROOT",
        )
        print(
            f"🚀 [Reconciler] Boot event published for session {self.mod.session_id}."
        )

    def _was_human_trigger(self) -> bool:
        """Check if the last context message was from a human."""
        msgs = self.mod.context.all_messages
        if msgs:
            last = msgs[-1]
            actor = last.get("actor_id", "")
            return actor == "Human" or str(actor).startswith("Discord/")
        return False

    async def _poll_once(self) -> bool:
        """Poll Fluss scanner once — delegates to moderator."""
        return await self.mod._poll_once()

    def halt(self):
        """Permanently halt this reconciler — the main loop will exit.

        Unlike SUSPENDED (which can be resumed by a human message),
        a halted reconciler is dead. Used by the SWE-bench harness
        to prevent zombie sessions from burning autonomous budget
        after a timeout.
        """
        self._halted = True
        self.state = State.SUSPENDED
        if self._election_task and not self._election_task.done():
            self._election_task.cancel()
            self._election_task = None
        self.mod.base_budget = 0
        self.mod.current_steps = 0

        # Also halt any running subagents
        if hasattr(self.mod, "subagent_manager") and self.mod.subagent_manager:
            asyncio.create_task(self.mod.subagent_manager.cancel_all())

        print("🛑 [Reconciler] Permanently halted — loop will exit.")
