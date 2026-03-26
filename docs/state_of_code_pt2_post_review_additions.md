# Review Action Items: Walkthrough

## P0: Critical Fixes

### Ripcurrent Hardening
- [ripcurrent/main.py](file:///.../containerclaw/ripcurrent/src/main.py): Split [init_fluss()](file:///.../containerclaw/ripcurrent/src/main.py#41-73) into 2 phases — connection retry (30 attempts) then table-wait retry (30 attempts). Previously crashed if agent hadn't created tables yet.
- Added [_get_num_buckets()](file:///.../containerclaw/ripcurrent/src/main.py#74-78) helper using `admin.get_table_info()` — replaced both `range(16)` with dynamic bucket counts.

### StreamActivity Dedup
- [main.py](file:///.../containerclaw/agent/src/main.py): Changed dedup key from `ts-actor_id-content[:50]` to `event_id` UUID with fallback.

## P1: Cleanup

- **Dead code removal**: Removed `range(16)` fallback in [moderator.py](file:///.../containerclaw/agent/src/moderator.py) (FlussClient is always provided)
- **[NEW]** [fluss_helpers.py](file:///.../containerclaw/ripcurrent/src/fluss_helpers.py): Shared `CHATROOM_SCHEMA` + [poll_batches()](file:///.../containerclaw/ripcurrent/src/fluss_helpers.py#27-40) for ripcurrent — eliminates inline schema and raw [_async_poll_batches](file:///.../containerclaw/fluss-rust/bindings/python/src/table.rs#2312-2367) unwrapping

## P2: Enhancements

### AgentContext
- **[NEW]** [agent_context.py](file:///.../containerclaw/agent/src/agent_context.py) (131 LoC): Isolated runtime wrapping agent + context + publisher + tools. Factory `AgentContext.create()` for future subagent spawning.

### Sessions PK Table
- [fluss_client.py](file:///.../containerclaw/agent/src/fluss_client.py): [_ensure_table()](file:///.../containerclaw/agent/src/fluss_client.py#80-102) now accepts `primary_keys` param. Sessions created with `primary_keys=["session_id"]`. [create_session()](file:///.../containerclaw/bridge/src/bridge.py#60-80) uses [new_upsert()](file:///.../containerclaw/fluss-rust/bindings/python/src/table.rs#697-728) instead of [new_append()](file:///.../containerclaw/fluss-rust/bindings/python/src/table.rs#640-661).

## Module Breakdown (Final)

| Module | LoC | Role |
|--------|-----|------|
| main.py | 442 | gRPC routing + workspace |
| agent.py | 325 | GeminiAgent (LLM) |
| fluss_client.py | 284 | Fluss infra + session CRUD |
| moderator.py | 271 | Orchestration loop |
| publisher.py | 155 | Batched writes |
| agent_context.py | 131 | Isolated agent runtime |
| context.py | 85 | Dedup + windowing |
| schemas.py | 62 | Table schemas |
| ripcurrent/main.py | 263 | Discord connector |
| ripcurrent/fluss_helpers.py | 39 | Shared Fluss patterns |

> [!IMPORTANT]
> Schema change (sessions → PK table) requires `claw.sh clean` before restart.
