# Phase 2: Async Migration & Agent Isolation

## Goal
Migrate all Fluss polling to the native `async for` API (Issue #424), extract [GeminiAgent](file:///.../containerclaw/agent/src/moderator.py#19-327) to its own module, and add UUID-based dedup. Plus includes Phase 1 bugfix.

> [!IMPORTANT]
> Phase 1 bugfix included: [publish()](file:///.../containerclaw/agent/src/moderator.py#356-383) was calling `self.context.add_message()` instead of `self._handle_single_message()`, causing human messages to be pre-deduped before the poll loop detected them. Fix already applied.

---

## 1. Extract GeminiAgent → `agent.py`

### [NEW] [agent.py](file:///.../containerclaw/agent/src/agent.py)

Move [GeminiAgent](file:///.../containerclaw/agent/src/moderator.py#19-327) (lines 19-326, 308 LoC) from [moderator.py](file:///.../containerclaw/agent/src/moderator.py) into its own module. This is a pure move — no behavioral changes. [moderator.py](file:///.../containerclaw/agent/src/moderator.py) drops from 586 → 260 LoC.

### [MODIFY] [moderator.py](file:///.../containerclaw/agent/src/moderator.py)

Add `from agent import GeminiAgent` and remove the class body.

---

## 2. Async For Migration (Issue #424)

Replace all `asyncio.to_thread(scanner.poll_arrow)` patterns with native `async for batch in scanner`.

### Affected Files

| File | Method | Current | New |
|---|---|---|---|
| [fluss_client.py](file:///.../containerclaw/agent/src/fluss_client.py) | [create_scanner()](file:///.../containerclaw/agent/src/fluss_client.py#88-118) | Returns scanner for manual poll | Add `create_async_scanner()` returning async-iterable scanner |
| [moderator.py](file:///.../containerclaw/agent/src/moderator.py) | [_replay_history()](file:///.../containerclaw/agent/src/moderator.py#384-439) | `while True: poll = await asyncio.to_thread(scanner.poll_arrow)` | `async for batch in scanner` |
| [moderator.py](file:///.../containerclaw/agent/src/moderator.py) | [run()](file:///.../containerclaw/agent/src/moderator.py#493-587) main loop | `while True: poll = await asyncio.to_thread(scanner.poll_arrow)` | Two-task design: reader task + action loop |
| [moderator.py](file:///.../containerclaw/agent/src/moderator.py) | [_poll_once()](file:///.../containerclaw/agent/src/moderator.py#486-490) | `asyncio.to_thread(scanner.poll_arrow)` | `scanner._async_poll_batches(600)` |
| [main.py](file:///.../containerclaw/agent/src/main.py) | [_list_sessions_async()](file:///.../containerclaw/agent/src/main.py#366-397) | [poll_arrow](file:///.../containerclaw/fluss-rust/bindings/python/src/table.rs#2102-2140) with 5 empty polls | `async for batch in scanner` |
| [main.py](file:///.../containerclaw/agent/src/main.py) | [_fetch_history_async()](file:///.../containerclaw/agent/src/main.py#478-550) | [poll_arrow](file:///.../containerclaw/fluss-rust/bindings/python/src/table.rs#2102-2140) with 10 empty polls | `async for batch in scanner` |
| [tools.py](file:///.../containerclaw/agent/src/tools.py) | `ProjectBoard.initialize()` | [poll_arrow](file:///.../containerclaw/fluss-rust/bindings/python/src/table.rs#2102-2140) via `to_thread` | `async for batch in scanner` |

### Design Note: The Main Loop Subtlety

The moderator's [run()](file:///.../containerclaw/agent/src/moderator.py#493-587) loop is special — it needs to both **tail the log indefinitely** and **react to messages by running elections**. Naive `async for` would block forever. The solution is to poll with a timeout and process in a request-response pattern:

```python
# In FlussClient — new helper
async def poll_async(self, scanner, timeout_ms=500):
    """Single async poll, returns list of RecordBatch (may be empty)."""
    batches = await scanner._async_poll_batches(timeout_ms)
    return batches if batches else []
```

The main loop stays structurally the same but uses true async polling instead of `asyncio.to_thread()`.

---

## 3. UUID-Based Idempotency

### [MODIFY] [schemas.py](file:///.../containerclaw/agent/src/schemas.py)

Add `event_id` field to `CHATROOM_SCHEMA`:

```python
CHATROOM_SCHEMA = pa.schema([
    pa.field("event_id", pa.string()),     # NEW: UUID dedup key
    pa.field("session_id", pa.string()),
    # ... rest unchanged
])
```

### [MODIFY] [context.py](file:///.../containerclaw/agent/src/context.py)

Switch dedup key from `f"{ts}-{actor_id}"` to `event_id`.

### [MODIFY] [moderator.py](file:///.../containerclaw/agent/src/moderator.py)

[publish()](file:///.../containerclaw/agent/src/moderator.py#356-383) generates `uuid.uuid4()` and includes it in the batch.

> [!WARNING]
> Adding `event_id` to the schema is a **breaking change** for existing Fluss tables. Requires `claw.sh clean` to reset tables.

---

## Execution Order

1. **GeminiAgent extraction** (zero risk, pure move)
2. **`async for` migration** — `FlussClient.poll_async()` helper, then update all 7 call sites
3. **UUID dedup** — schema change, context change, publish change

## Verification

```bash
claw.sh clean && claw.sh up
```
Create session → send message → verify election + tool execution → `/stop` → `/automation=3` → refresh page → verify history loads.
