"""
HeartbeatEmitter: Agent liveness and state publishing.

Periodically writes the current agent state (idle, electing, executing,
suspended) to the Fluss agent_status table. This enables:

  1. The UI to show "is the system alive?" without inference.
  2. Monitoring agents (Layer 2) to detect stalls.
  3. The 50ms Rule watchdog to detect event loop blockage.

The emitter is a lightweight coroutine that runs on the main event loop
and never blocks.
"""

import asyncio
import time
import pyarrow as pa

from schemas import AGENT_STATUS_SCHEMA


class HeartbeatEmitter:
    """Publishes periodic liveness heartbeats to Fluss.

    Usage:
        emitter = HeartbeatEmitter(status_table, session_id)
        await emitter.start()
        emitter.update_state("executing", "Running RepoMapTool")
        ...
        await emitter.stop()
    """

    def __init__(
        self,
        status_table,
        session_id: str,
        agent_id: str = "Moderator",
        interval_s: float = 5.0,
    ):
        self.status_table = status_table
        self.session_id = session_id
        self.agent_id = agent_id
        self.interval_s = interval_s

        self._state = "idle"
        self._current_task = ""
        self._writer = status_table.new_append().create_writer() if status_table else None
        self._task: asyncio.Task | None = None
        self._last_loop_time: float = 0.0

    def update_state(self, state: str, current_task: str = ""):
        """Update the reported state (non-blocking, in-memory only)."""
        self._state = state
        self._current_task = current_task

    async def start(self):
        """Start the background heartbeat loop."""
        if self._writer:
            self._task = asyncio.create_task(self._heartbeat_loop())
            print(f"💓 [Heartbeat] Started for {self.session_id}")

    async def stop(self):
        """Stop the heartbeat loop."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            print(f"💓 [Heartbeat] Stopped for {self.session_id}")

    async def _heartbeat_loop(self):
        """Periodically write heartbeat to Fluss."""
        try:
            while True:
                loop_start = time.monotonic()
                await self._write_heartbeat()

                # 50ms Rule watchdog: detect event loop blockage
                if self._last_loop_time > 0:
                    delta_ms = (loop_start - self._last_loop_time) * 1000
                    expected_ms = self.interval_s * 1000
                    if delta_ms > expected_ms * 3:
                        print(
                            f"⚠️ [Heartbeat] Loop latency {delta_ms:.0f}ms "
                            f"(expected ~{expected_ms:.0f}ms) — event loop may be under load."
                        )
                self._last_loop_time = loop_start

                await asyncio.sleep(self.interval_s)
        except asyncio.CancelledError:
            pass

    async def _write_heartbeat(self):
        """Write a single heartbeat record.
        
        Note: Fluss writer.write_arrow_batch() is synchronous, but
        writer.flush() is a coroutine. Both are lightweight enough
        to run on the event loop without thread offloading.
        """
        if not self._writer:
            return
        try:
            now_ms = int(time.time() * 1000)
            batch = pa.RecordBatch.from_arrays([
                pa.array([self.session_id], type=pa.string()),
                pa.array([self.agent_id], type=pa.string()),
                pa.array([self._state], type=pa.string()),
                pa.array([now_ms], type=pa.int64()),
                pa.array([self._current_task], type=pa.string()),
            ], schema=AGENT_STATUS_SCHEMA)

            self._writer.write_arrow_batch(batch)
            if hasattr(self._writer, "flush"):
                await self._writer.flush()
        except Exception as e:
            print(f"⚠️ [Heartbeat] Write failed: {e}")
