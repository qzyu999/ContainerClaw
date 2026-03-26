# Phase 3: FlussPublisher & AgentService Cleanup

## Goal
Introduce batched writes via `FlussPublisher`, consolidate session CRUD into [FlussClient](file:///.../containerclaw/agent/src/fluss_client.py#21-138), and slim [main.py](file:///.../containerclaw/agent/src/main.py) from 567 → ~350 LoC.

---

## 1. FlussPublisher — Batched Writes

### [NEW] [publisher.py](file:///.../containerclaw/agent/src/publisher.py)

Batched, async-safe publisher that buffers writes and flushes on a timer or batch size threshold.

```python
class FlussPublisher:
    def __init__(self, table, schema, session_id, flush_interval_ms=100, max_batch_size=50):
        ...
    async def publish(self, **fields) -> str:  # returns event_id
        ...
    async def flush(self):
        ...
```

**Impact:** During a 10-tool-call agent turn, reduces 20+ individual `writer.flush()` network round-trips to 1-3 batched flushes.

### [MODIFY] [moderator.py](file:///.../containerclaw/agent/src/moderator.py)

Replace inline `self.writer` + `pa.RecordBatch.from_arrays()` in [publish()](file:///.../containerclaw/agent/src/moderator.py#45-74) with `self.publisher.publish(...)`.

---

## 2. Session CRUD → FlussClient

### [MODIFY] [fluss_client.py](file:///.../containerclaw/agent/src/fluss_client.py)

Add three methods from main.py:
- [create_session(session_id, title)](file:///.../containerclaw/bridge/src/bridge.py#60-80)
- [list_sessions() → list[dict]](file:///.../containerclaw/bridge/src/bridge.py#40-59)
- [fetch_history(session_id) → list[dict]](file:///.../containerclaw/agent/src/main.py#483-551)

### [MODIFY] [main.py](file:///.../containerclaw/agent/src/main.py)

Replace inline [_create_session_async](file:///.../containerclaw/agent/src/main.py#455-482), [_list_sessions_async](file:///.../containerclaw/agent/src/main.py#371-402), [_fetch_history_async](file:///.../containerclaw/agent/src/main.py#483-551) with `self.fluss.create_session(...)` etc. Removes ~140 LoC of inline Fluss I/O from AgentService.

---

## Execution Order

1. Create `publisher.py` (new file, zero risk)
2. Wire into moderator's [publish()](file:///.../containerclaw/agent/src/moderator.py#45-74) 
3. Move session CRUD to [FlussClient](file:///.../containerclaw/agent/src/fluss_client.py#21-138)
4. Update [main.py](file:///.../containerclaw/agent/src/main.py) to use new FlussClient methods

## Verification

```bash
claw.sh clean && claw.sh up
```
Test: session list → create session → send message → election + tools → `/stop` → page refresh → verify history.
