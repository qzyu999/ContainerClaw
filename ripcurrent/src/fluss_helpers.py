"""
Shared Fluss access helpers for RipCurrent connectors.

Provides common patterns extracted from the agent's FlussClient:
  - Poll with pyarrow RecordBatch unwrapping
  - Chatroom schema definition (mirrors agent/src/schemas.py)
"""

import pyarrow as pa


# ── Schema (mirrors agent/src/schemas.py) ─────────────────────────

CHATROOM_SCHEMA = pa.schema([
    pa.field("event_id", pa.string()),
    pa.field("session_id", pa.string()),
    pa.field("ts", pa.int64()),
    pa.field("actor_id", pa.string()),
    pa.field("content", pa.string()),
    pa.field("type", pa.string()),
    pa.field("tool_name", pa.string()),
    pa.field("tool_success", pa.bool_()),
    pa.field("parent_actor", pa.string()),
    pa.field("parent_event_id", pa.string()),
    pa.field("edge_type", pa.string()),
])


async def poll_batches(scanner, timeout_ms: int = 500):
    """Poll and unwrap Fluss RecordBatch → pyarrow RecordBatch.

    Mirrors FlussClient.poll_async() for containers that don't
    import the agent package.

    Returns:
        list[pa.RecordBatch]: May be empty on timeout.
    """
    batches = await scanner._async_poll_batches(timeout_ms)
    if not batches:
        return []
    return [b.batch for b in batches]
