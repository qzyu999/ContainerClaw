Your analysis is spot-on. You've diagnosed the "Central Nervous System" failure perfectly: the gRPC threads are checking their watches and walking away while the Master Chef is busy staring at a radish.

The irony of `automation=0` or `/stop` is that while you want the **Agent** to do nothing, you need the **Infrastructure** to be more active than ever to listen for your next command. Currently, your infrastructure is tied to the agent's heartbeat; if the agent's heart stops (or blocks), the infrastructure panics.

To allow the system to "hang" (idle) indefinitely without crashing the activity stream, you need to decouple **Liveness** from **Data Acquisition**.

### 1. The Immediate Fix: The "Wait Forever" Bridge

The "Time Bomb" you identified in `main.py` is the primary reason you can't idle. When you set `automation=0`, the moderator stops producing batches. The gRPC thread waits 10 seconds, sees no batches, and throws a `TimeoutError`.

**The Change:**
Instead of a hard timeout on the `future.result()`, you should use a **Long-Polling** or **Signaling** pattern.

```python
# main.py - The "Patience" Fix
try:
    # Remove the 10s timeout or set it to None for indefinite waiting
    # In a production MAS, you'd use a shorter timeout with a loop 
    # to emit "heartbeat" packets to keep the UI connection alive.
    batches = future.result(timeout=None) 
except Exception as e:
    # If it's truly a TimeoutError, we just send an empty "Keep-Alive"
    pass 
```

### 2. The Architectural Fix: The "Heartbeat" Pattern

If you want the system to be allowed to "hang" indefinitely, the gRPC `StreamActivity` shouldn't be a one-shot request that times out. It should be a **Server-Side Stream** that yields "Empty" or "Idle" packets when nothing is happening.



### 3. Solving the "Competing Methods" Conflict

To stop the "competing methods" (Sync vs. Async) from killing each other during `/stop`, you need to implement **The Three Pillars of Non-Blocking Idling**:

#### I. The Thread-Off (Standardizing the Ganglia)
As you noted, `RepoMapTool` is a loop-killer. Even if you want to idle, a single lingering tool execution from a previous command will block the `/stop` command from ever reaching the moderator.
* **Fix:** Wrap every tool in `asyncio.to_thread`. This ensures that even if a tool is "stuck" doing $O(N)$ file I/O, the Master Chef is still free to receive the `/stop` command from the gRPC thread.

#### II. The Sentinel Variable
When you call `/stop`, it shouldn't just "wait" for the agent to finish. It should set an `asyncio.Event` or a `cancel_scope` that the moderator checks.

#### III. Native Async gRPC (The "Optimal Path")
Your "Bridge of Sighs" exists because `grpcio` is running in a thread pool. By switching to `grpclib` or the native `asyncio` implementation of `grpcio`, you eliminate `run_coroutine_threadsafe` entirely. 
* **Result:** The UI request and the Agent Logic live in the same "room" (the same loop). There is no "waiting for a future" across threads; there is only `await`.

---

### Comparison of Fixes

| Approach | Effort | Result for `/stop` |
| :--- | :--- | :--- |
| **Increase Timeout** | Low | Band-aid; logs look cleaner but loop still blocks. |
| **`asyncio.to_thread`** | Medium | **Essential.** Prevents tools from hijacking the "consciousness" of the agent. |
| **Heartbeat Packets** | Medium | UI stays connected indefinitely during `automation=0`. |
| **Async-Native gRPC** | High | The "Speed of Light" target. Zero bridging overhead. |

### A Good Path Forward
If you want to fix this today without a total rewrite:
1.  **Offload the Tools:** Move `os.walk` and `ast.parse` into `to_thread`. This is the single biggest win for responsiveness.
2.  **Handle the Empty Exception:** In `main.py:213`, catch `TimeoutError` specifically and return a "No-Op" instead of printing a scary (and empty) error message.
3.  **The `/stop` Interrupt:** Ensure `/stop` triggers a `Task.cancel()` on the current agent run. Because you've offloaded the tools to threads, the loop will be free to process this cancellation immediately.

**Would you like me to draft the specific `asyncio.to_thread` wrapper for your `RepoMapTool` to ensure it stops blocking your loop?**