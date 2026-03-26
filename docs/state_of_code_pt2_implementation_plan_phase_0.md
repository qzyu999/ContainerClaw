# Phased Refactoring: Implementation Plan

## Goal
Refactor ContainerClaw's codebase to be modular, idempotent, and stream-centric per the analysis in [state_of_code_pt1.md](file:///Users/.../containerclaw/docs/state_of_code_pt1.md) and [state_of_code_pt2.md](file:///Users/.../containerclaw/docs/state_of_code_pt2.md). Phased to allow testing between each phase.

## Phase 0: Foundation Extractions (This Session)

Zero-risk, mechanical extractions. No behavioral changes — purely reorganizing existing code into proper modules.

---

### Component 1: `schemas.py`

#### [NEW] [schemas.py](file:///Users/.../containerclaw/agent/src/schemas.py)
- Extract `CHATROOM_SCHEMA`, `SESSIONS_SCHEMA`, `BOARD_EVENTS_SCHEMA` as module-level constants
- Add `event_id` (pa.string()) field to `CHATROOM_SCHEMA` as the first field

#### [MODIFY] [main.py](file:///Users/.../containerclaw/agent/src/main.py)
- Replace inline schema definitions in [init_infrastructure()](file:///Users/.../containerclaw/agent/src/main.py#585-666) (lines 602-655) with imports from `schemas.py`

#### [MODIFY] [moderator.py](file:///Users/.../containerclaw/agent/src/moderator.py)
- Replace inline `self.pa_schema` definition (lines 345-354) with import from `schemas.py`

#### [MODIFY] [tools.py](file:///Users/.../containerclaw/agent/src/tools.py)
- Replace inline `self._pa_schema` definition in `ProjectBoard.__init__()` (lines 198-209) with import from `schemas.py`

---

### Component 2: `fluss_client.py`

#### [NEW] [fluss_client.py](file:///Users/.../containerclaw/agent/src/fluss_client.py)
- `FlussClient` class encapsulating:
  - `connect()` — connection + retry logic currently in [init_infrastructure()](file:///Users/.../containerclaw/agent/src/main.py#585-666)
  - `ensure_tables()` — create database + all 3 tables
  - `create_tailing_scanner(table, start_ts=None)` — dynamic bucket discovery via [get_table_info()](file:///Users/.../containerclaw/fluss-rust/bindings/python/src/table.rs#662-666), timestamp seeking
  - Store `conn`, `admin`, `chat_table`, `sessions_table`, `board_table` as instance attributes

#### [MODIFY] [main.py](file:///Users/.../containerclaw/agent/src/main.py)
- Replace [init_infrastructure()](file:///Users/.../containerclaw/agent/src/main.py#585-666) function with `FlussClient.connect()` + `FlussClient.ensure_tables()`
- `AgentService.__init__()` takes a `FlussClient` instead of individual table refs
- All scanner creation uses `FlussClient.create_tailing_scanner()`

#### [MODIFY] [moderator.py](file:///Users/.../containerclaw/agent/src/moderator.py)
- `StageModerator.__init__()` takes `FlussClient` instead of [table](file:///Users/.../containerclaw/fluss-rust/bindings/python/src/table.rs#739-755), `sessions_table`, `fluss_conn`
- [_replay_history()](file:///Users/.../containerclaw/agent/src/moderator.py#383-456) uses `FlussClient.create_tailing_scanner()`
- Replace all hardcoded `range(16)` with dynamic bucket discovery

---

### Component 3: `commands.py`

#### [NEW] [commands.py](file:///Users/.../containerclaw/agent/src/commands.py)
- `CommandDispatcher` class with `register(prefix, handler)` and `dispatch(content) -> bool`
- Built-in handlers for `/stop` and `/automation=X`
- Returns `True` if content was a command (should not trigger election)

#### [MODIFY] [moderator.py](file:///Users/.../containerclaw/agent/src/moderator.py)
- Replace command parsing in [_handle_single_message()](file:///Users/.../containerclaw/agent/src/moderator.py#457-504) (lines 477-491) with `CommandDispatcher.dispatch()`
- Initialize `CommandDispatcher` in `StageModerator.__init__()`

---

## Verification Plan

> [!IMPORTANT]
> There is no existing test suite. Verification is functional: rebuild Docker images and confirm the system works end-to-end.

### Functional Test (User Manual)
1. Run `docker compose build claw-agent` to verify the refactored code compiles without import errors
2. Run `docker compose up -d` to start the full stack
3. Open the UI at `http://localhost:3000`
4. Create a new session
5. Send a message like "Hello, what can you do?" and verify agents respond
6. Send `/automation=3` and verify the moderator logs show "Step budget updated to: 3"
7. Send `/stop` and verify agents halt
8. Refresh the page and confirm chat history loads correctly
9. Check Docker logs (`docker compose logs claw-agent`) for any import errors or schema mismatches

---

## Future Phases (Not This Session)

### Phase 1: Moderator Decomposition
- Extract `ElectionProtocol`, `ToolExecutor`, `ContextManager`, `FlussPublisher`, `BudgetTracker`

### Phase 2: Agent Isolation + RipCurrent SDK
- Create `AgentContext`, `SubagentManager`, `RipCurrentConnector`

### Phase 3: Advanced Optimizations
- Write batching, checkpointing, PK session table, `async for` migration
