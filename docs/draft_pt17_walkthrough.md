# Walkthrough: Stream-Centric Reconciliation Pivot

## Summary

Implemented the three surgeries from [draft_pt17.md](file:///.../containerclaw/docs/draft_pt17.md) to shift ContainerClaw from an imperative orchestration engine to a stream-centric reconciliation backbone. The Bridge of Sighs is eliminated, the event loop never blocks, and the system is always responsive to commands.

## Files Changed

### Modified Files

| File | Surgery | Change |
|:---|:---|:---|
| [main.py](file:///.../containerclaw/agent/src/main.py) | S1+S2+S3 | Full rewrite: `grpc.aio`, async handlers, ReconciliationController wiring |
| [tools.py](file:///.../containerclaw/agent/src/tools.py) | S1 | `asyncio.to_thread` for 5 tools + async [ProjectBoard](file:///.../containerclaw/agent/src/tools.py#179-353) methods |
| [schemas.py](file:///.../containerclaw/agent/src/schemas.py) | S3 | Added `AGENT_STATUS_SCHEMA` and table constant |
| [fluss_client.py](file:///.../containerclaw/agent/src/fluss_client.py) | S3 | Added `status_table` initialization |
| [commands.py](file:///.../containerclaw/agent/src/commands.py) | S3 | `/stop` uses `reconciler.halt()`, `/automation=` exits SUSPENDED |

### New Files

| File | Purpose |
|:---|:---|
| [reconciler.py](file:///.../containerclaw/agent/src/reconciler.py) | [ReconciliationController](file:///.../containerclaw/agent/src/reconciler.py#42-293) — state machine (IDLE→ELECTING→EXECUTING→PUBLISHING→SUSPENDED) |
| [heartbeat.py](file:///.../containerclaw/agent/src/heartbeat.py) | [HeartbeatEmitter](file:///.../containerclaw/agent/src/heartbeat.py#22-116) — liveness publisher with 50ms watchdog |

## Key Architectural Changes

### 1. Event Loop Never Blocks (Surgery 1)

All synchronous file I/O tools now delegate to worker threads:

```diff
 # RepoMapTool — was: os.walk + ast.parse ON the event loop
 async def execute(self, agent_id, params):
-    for root, dirs, files in os.walk(base_dir):
-        content = file_path.read_text(...)
-        tree = ast.parse(content)
+    return await asyncio.to_thread(self._build_map)
```

Same pattern applied to [SurgicalEditTool](file:///.../containerclaw/agent/src/tools.py#518-586), [AdvancedReadTool](file:///.../containerclaw/agent/src/tools.py#588-629), [CreateFileTool](file:///.../containerclaw/agent/src/tools.py#475-516), and `ProjectBoard._publish_event`.

### 2. Bridge of Sighs Eliminated (Surgery 2)

```diff
-# OLD: Threading bridge with 10s timeout bomb
-server = grpc.server(ThreadPoolExecutor(max_workers=10))
-future = asyncio.run_coroutine_threadsafe(poll_async(), self.loop)
-batches = future.result(timeout=10)  # ← THE KNOT
+# NEW: Native async — same event loop, no bridge
+server = grpc.aio.server()
+batches = await FlussClient.poll_async(scanner, timeout_ms=500)
```

### 3. Reconciliation Controller (Surgery 3)

The moderator's sequential `poll→elect→execute→publish` pipeline is replaced by a state machine where election and execution are dispatched as independent `asyncio.Task`s:

```
Main Loop (< 600ms/tick):     Dispatched Tasks:
  ┌─ poll stream ─────────┐    ┌─ election (LLM call) ──┐
  ├─ process commands ─────┤    ├─ tool execution ───────┤
  ├─ reconcile state ──────┤    └─ result publishing ────┘
  ├─ emit heartbeat ───────┤
  └─ yield to event loop ──┘
```

## Verification Results

| Check | Result |
|:---|:---|
| `run_coroutine_threadsafe` in code | ✅ Only in docstring comment |
| `threading.Lock` | ✅ Zero results |
| `concurrent.futures` imports | ✅ Zero results |
| `import threading` | ✅ Zero results |
| `os.walk` on event loop | ✅ Only inside `asyncio.to_thread` |
| All gRPC handlers async | ✅ Verified |

> [!IMPORTANT]
> These changes require rebuild of the `claw-agent` Docker image to take effect. The gRPC wire protocol is unchanged — the UI bridge does not need any modifications.
