# Phase 1: Moderator Decomposition

## Goal
Decompose [StageModerator](file:///.../containerclaw/agent/src/moderator.py#329-858) (530 LoC) into focused, single-responsibility modules while keeping [GeminiAgent](file:///.../containerclaw/agent/src/moderator.py#19-327) (308 LoC) in-place. Zero behavioral change.

## Current Responsibility Map

```
StageModerator (530 LoC)
├── Fluss I/O         → publish(), _replay_history(), _poll_once(), _process_poll_result()
├── Election           → elect_leader() (3-round voting with debate tie-breaker)
├── Tool Execution     → _execute_with_tools(), _execute_text_only() (Gemini fn-calling loop + circuit breaker)
├── Context Management → _get_context_window() (token-aware windowing + budget fitting)
├── Budget/Run Loop    → run() (main loop: poll → election → execution → budget decrement)
└── Message Handling   → _handle_single_message() (dedup, command dispatch, human detection)
```

## Proposed Extraction

### 1. `election.py` — ElectionProtocol

#### [NEW] [election.py](file:///.../containerclaw/agent/src/election.py)

Extract [elect_leader()](file:///.../containerclaw/agent/src/moderator.py#752-829) (lines 752-828, ~77 LoC) into a standalone class:

```python
class ElectionProtocol:
    async def run_election(self, agents, roster_str, history, publish_fn) -> tuple[str|None, str, bool]:
        """Run 3-round election. Returns (winner, log, is_done)."""
```

**Dependencies:** Needs agent list, roster string, and a `publish_fn` callback for Fluss output. No direct Fluss access needed — the moderator passes its [publish()](file:///.../containerclaw/agent/src/moderator.py#830-858) as a callback.

---

### 2. `tool_executor.py` — ToolExecutor

#### [NEW] [tool_executor.py](file:///.../containerclaw/agent/src/tool_executor.py)

Extract [_execute_with_tools()](file:///.../containerclaw/agent/src/moderator.py#609-746) and [_execute_text_only()](file:///.../containerclaw/agent/src/moderator.py#747-751) (lines 609-750, ~142 LoC):

```python
class ToolExecutor:
    def __init__(self, tool_dispatcher, publish_fn, get_context_fn, poll_fn):
        ...
    
    async def execute(self, agent, check_interrupt_fn) -> str | None:
        """Run agent's turn with full fn-calling protocol + circuit breaker."""
```

**Dependencies:** Needs [ToolDispatcher](file:///.../containerclaw/agent/src/tools.py#863-913), `publish_fn` callback, `get_context_fn` callback (for refreshing context window), and `poll_fn` (for mid-turn Fluss sync). These are injected as callbacks to avoid circular imports.

---

### 3. `context.py` — ContextManager

#### [NEW] [context.py](file:///.../containerclaw/agent/src/context.py)

Extract [_get_context_window()](file:///.../containerclaw/agent/src/moderator.py#351-377) (lines 348-373, ~26 LoC) and the message history cache management:

```python
class ContextManager:
    def __init__(self, max_tokens, max_messages):
        self.all_messages = []
        self.history_keys = set()
    
    def get_window(self, size=None) -> list[dict]:
        """Token-budget-aware context windowing."""
    
    def add_message(self, actor_id, content, ts) -> bool:
        """Add a message, returns True if it was new (not a duplicate)."""
    
    def trim(self):
        """Trim in-memory cache to prevent unbounded growth."""
```

---

### 4. Slimmed [moderator.py](file:///.../containerclaw/agent/src/moderator.py) — StageModerator (orchestrator only)

#### [MODIFY] [moderator.py](file:///.../containerclaw/agent/src/moderator.py)

After extraction, [StageModerator](file:///.../containerclaw/agent/src/moderator.py#329-858) becomes a thin orchestrator (~200 LoC):

```python
class StageModerator:
    def __init__(self, ...):
        self.context = ContextManager(...)
        self.election = ElectionProtocol()
        self.executor = ToolExecutor(dispatcher, self.publish, 
                                      self.context.get_window, self._poll_once)
        self.command_dispatcher = create_default_dispatcher()
    
    async def run(self):           # Main loop (poll → elect → execute)
    async def publish(self, ...):  # Fluss write (stays here — it's the I/O boundary)
    async def _replay_history(self):  # Fluss replay (stays here — uses FlussClient)
    async def _poll_once(self):    # Single poll cycle
```

**publish() stays in moderator** because it's the I/O boundary — the thing that actually writes to Fluss. Everything else receives it as a callback.

---

## Execution Order

1. **context.py** first (zero dependencies, pure data structure)
2. **election.py** second (depends only on GeminiAgent's [_vote()](file:///.../containerclaw/agent/src/moderator.py#111-145) method)
3. **tool_executor.py** third (depends on ToolDispatcher + callbacks)
4. **Update moderator.py** last (wire all three together)

## Verification Plan

Same as Phase 0:
```bash
claw.sh clean && claw.sh up
```
Then: create session → send message → verify election + tool execution → `/stop` → `/automation=3` → refresh page.
