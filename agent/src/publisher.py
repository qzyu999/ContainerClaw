"""
Batched Fluss publisher for ContainerClaw.

Buffers writes and flushes on a timer or batch-size threshold,
reducing per-message network round-trips to batched flushes.
Memory updates (via callback) happen immediately on publish —
only the Fluss network flush is deferred.
"""

import asyncio
import time
import uuid
import pyarrow as pa

from schemas import CHATROOM_SCHEMA


class FlussPublisher:
    """Batched, async-safe publisher for the chatroom log.

    Writes are buffered in memory and flushed to Fluss either when:
    - The buffer reaches `max_batch_size` records, or
    - `flush_interval_s` seconds have elapsed since the last flush.

    The `on_message` callback fires immediately on publish (before flush)
    so the moderator's in-memory context stays up-to-date without waiting
    for the network round-trip.

    Usage:
        publisher = FlussPublisher(table, session_id)
        await publisher.start()   # starts background flush timer
        event_id = await publisher.publish("Alice", "Hello!", "output")
        await publisher.stop()    # final flush + cancel timer
    """

    def __init__(
        self,
        table,
        session_id: str,
        on_message=None,
        flush_interval_s: float = 0.1,
        max_batch_size: int = 50,
    ):
        self.table = table
        self.session_id = session_id
        self.on_message = on_message  # async callback(actor_id, content, ts)
        self.flush_interval_s = flush_interval_s
        self.max_batch_size = max_batch_size

        self._schema = CHATROOM_SCHEMA
        self._writer = table.new_append().create_writer()
        self._buffer: list[dict] = []
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None

    async def start(self):
        """Start the background flush timer."""
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def stop(self):
        """Flush remaining buffer and cancel the background timer."""
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        await self.flush()

    async def publish(
        self,
        actor_id: str,
        content: str,
        m_type: str = "output",
        tool_name: str = "",
        tool_success: bool = False,
        parent_actor: str = "",
        parent_event_id: str = "",
        edge_type: str = "SEQUENTIAL",
    ) -> str:
        """Buffer a message for batched flush. Returns the event_id (UUID).

        The on_message callback fires immediately so in-memory state
        is always current, even before the Fluss flush occurs.
        """
        event_id = str(uuid.uuid4())
        ts = int(time.time() * 1000)

        record = {
            "event_id": event_id,
            "session_id": self.session_id,
            "ts": ts,
            "actor_id": actor_id,
            "content": content,
            "type": m_type,
            "tool_name": tool_name,
            "tool_success": tool_success,
            "parent_actor": parent_actor,
            "parent_event_id": parent_event_id,
            "edge_type": edge_type,
        }

        # Fire callback immediately (memory update)
        if self.on_message:
            await self.on_message(actor_id, content, ts)

        async with self._lock:
            self._buffer.append(record)
            if len(self._buffer) >= self.max_batch_size:
                await self._flush_locked()

        return event_id

    async def flush(self):
        """Flush all buffered records to Fluss."""
        async with self._lock:
            await self._flush_locked()

    async def _flush_locked(self):
        """Internal flush — must be called with self._lock held."""
        if not self._buffer:
            return

        records = self._buffer
        self._buffer = []
        n = len(records)

        try:
            batch = pa.RecordBatch.from_pydict(
                {
                    "event_id": [r["event_id"] for r in records],
                    "session_id": [r["session_id"] for r in records],
                    "ts": [r["ts"] for r in records],
                    "actor_id": [r["actor_id"] for r in records],
                    "content": [r["content"] for r in records],
                    "type": [r["type"] for r in records],
                    "tool_name": [r["tool_name"] for r in records],
                    "tool_success": [r["tool_success"] for r in records],
                    "parent_actor": [r["parent_actor"] for r in records],
                    "parent_event_id": [r["parent_event_id"] for r in records],
                    "edge_type": [r["edge_type"] for r in records],
                },
                schema=self._schema,
            )
            self._writer.write_arrow_batch(batch)
            if hasattr(self._writer, "flush"):
                await self._writer.flush()
            print(f"📤 [Publisher] Flushed {n} records to Fluss.")
        except Exception as e:
            print(f"❌ [Publisher] Flush failed ({n} records): {e}")
            import traceback
            traceback.print_exc()

    async def _flush_loop(self):
        """Background timer that periodically flushes the buffer."""
        try:
            while True:
                await asyncio.sleep(self.flush_interval_s)
                await self.flush()
        except asyncio.CancelledError:
            pass
