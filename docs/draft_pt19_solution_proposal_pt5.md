# Solution Proposal Part 5: Deterministic DAG via In-Reply-To Causality

## Executive Summary

The current DAG pipeline is **fundamentally broken** because it attempts to reconstruct causal relationships by *heuristic self-joining* the chatroom log — guessing parentage from `parent_actor` name + timestamp proximity. This is equivalent to trying to reconstruct who replied to whom in a busy email thread by looking only at sender names and clock times, then hoping the newest message from that sender within ±1 second is the parent. The result: **36 events produce exactly 1 edge** (ROOT → Moderator), because the `LEFT JOIN` on `parent_actor = actor_id` within a 1-second window is a non-unique, non-deterministic match that collapses nearly everything to `ROOT`.

The fix is architectural, not surgical. We must adopt the **In-Reply-To** pattern: each event carries an explicit, immutable reference to the specific event it responds to, and this reference is recorded **at event creation time** (i.e., at the speed of the writer), not inferred later. This transforms the Flink pipeline from a 60-line heuristic join into a 5-line deterministic projection.

---

## Table of Contents

1. [First Principles: The Physics of Causality](#1-first-principles-the-physics-of-causality)
2. [Diagnostic: Why the Current Pipeline Fails](#2-diagnostic-why-the-current-pipeline-fails)
3. [The In-Reply-To Pattern](#3-the-in-reply-to-pattern)
4. [Architecture Overview](#4-architecture-overview)
5. [Detailed Code Changes](#5-detailed-code-changes)
    - [5.1 Schema Layer](#51-schema-layer-schemasspy)
    - [5.2 Publisher Layer](#52-publisher-layer-publisherpy)
    - [5.3 Moderator Layer](#53-moderator-layer-moderatorpy)
    - [5.4 Tool Executor Layer](#54-tool-executor-layer-tool_executorpy)
    - [5.5 Subagent Layer](#55-subagent-layer-subagent_managerpy--agent_contextpy)
    - [5.6 Flink Pipeline Layer](#56-flink-pipeline-layer-dagpipelinejava)
    - [5.7 Telemetry Sink Tables](#57-telemetry-sink-tables-telemetryjobjava)
6. [Invariants and Correctness Proof](#6-invariants-and-correctness-proof)
7. [Migration Strategy](#7-migration-strategy)
8. [Verification Plan](#8-verification-plan)

---

## 1. First Principles: The Physics of Causality

### The Speed of Light Constraint

In any distributed system, the **speed of light** is the ultimate limiting factor for information propagation. When Agent A produces event `e₁` and Agent B then produces event `e₂` in response, the causal relationship `e₁ → e₂` is a **physical fact** that exists at the moment `e₂` is created. At that moment, the writer of `e₂` necessarily holds a reference to `e₁` (or at minimum, knows *what* it is replying to) — because that's what triggered the write.

**This is the fastest possible time to record causality.** Any later reconstruction adds latency, ambiguity, and potential for error.

### Information-Theoretic Argument

Once the writer discards the causal link and publishes only the payload + timestamp, that information is **irrecoverably lost**. No downstream pipeline — no matter how clever its joins or windows — can deterministically recover it. This is not a bug in the Flink SQL; it is a consequence of the **Data Processing Inequality**:

> If `X → Y → Z` is a Markov chain, then `I(X;Z) ≤ I(X;Y)`.

The mutual information between the DAG structure (`X`) and the noisy timestamp-based reconstruction (`Z`) is strictly less than the mutual information between the DAG structure and the event at creation time (`Y`). **You cannot recover signal that was never encoded.**

### The Email Analogy

The current system is like an email server that strips all `In-Reply-To` and `References` headers before storing messages. To reconstruct threads, a downstream process tries to match `From:` fields against previous messages within a time window. This works for a quiet inbox; it collapses catastrophically when:

- Multiple agents send near-simultaneously (the 1000ms window captures many candidates)
- The same actor sends multiple messages (elections emit 3 messages per round in the same millisecond — tally, summary, winner — all with `ts` identical)
- A subagent's `parent_actor` is `""` (empty string), causing the `LEFT JOIN` to produce no match, collapsing to `ROOT`

The fix: **keep the headers.**

---

## 2. Diagnostic: Why the Current Pipeline Fails

### 2.1 The Broken Self-Join

From [`DagPipeline.java`](../telemetry/src/main/java/com/containerclaw/telemetry/DagPipeline.java):

```sql
FROM fluss_catalog.containerclaw.chatroom c1
LEFT JOIN fluss_catalog.containerclaw.chatroom c2
  ON c1.session_id = c2.session_id
  AND c1.parent_actor = c2.actor_id      -- ⚠️ name-based, non-unique
  AND c2.ts <= c1.ts + 1000              -- ⚠️ allows future parents
```

**Failure modes observed in the production logs:**

| Event | `parent_actor` | What the JOIN finds | Result |
|-------|---------------|-------------------|--------|
| Moderator election messages (tally, summary, winner) | `""` (empty) | No match → `c2.event_id IS NULL` | All collapse to `ROOT` |
| Subagent `Sub/bde8ee35` output | `Subagent/bde8ee35` | Looks for `actor_id = 'Subagent/bde8ee35'` — **no such actor exists** | Collapses to `ROOT` |
| Tool result `[Tool Result for Bob] board` | `Bob` | Finds *any* Bob event within 1s — which Bob event? | Non-deterministic |
| Human messages | `""` | No match | `ROOT` |
| Carol's delegate calls (5 events, all at `ts ≈ 1775001115845`) | `""` | No match for most | `ROOT` |

**Out of 36+ chatroom events, only 1 edge survives**: `ROOT → Moderator|dd2de7b0...`

### 2.2 The parent_actor ≠ parent_event Problem

The schema has `parent_actor` (a string like `"Carol"`, `"Sub/bcb81551"`, or `"Subagent/bde8ee35"`), but this is an **actor name**, not an **event pointer**. The relationship is:

```
parent_actor: "Who spawned me?" (ambiguous — the actor has many events)
parent_event_id: "Which specific event am I replying to?" (deterministic)
```

Looking at the log data, the `parent_actor` field is:
- **Empty** for 90%+ of events (Moderator election messages, human input, agent output)
- **Set** only for Moderator tool-result events (e.g., `parent_actor: 'Bob'` for `[Tool Result for Bob]`)
- **Inconsistently formatted** even when present (e.g., `'Subagent/bde8ee35'` vs `'Sub/bde8ee35'`)

### 2.3 Timestamp Collision

The election protocol is the most dramatic example. When the Moderator publishes election results, it emits **3 events with identical timestamps**:

```python
# From the logs — all at ts: 1775001017871
'Round 1 Tally: ...'      # type: thought
'Election Summary: ...'   # type: voting
'🏆 Winner: Bob'          # type: thought
```

The `ROW_NUMBER() OVER (PARTITION BY c1.event_id ORDER BY c2.ts DESC)` cannot disambiguate between them because `ts` is not unique within an actor.

```mermaid
graph LR
    subgraph "What Actually Happened (Ground Truth)"
        H1["Human: Hi Alice"] --> E1["Election Round 1"]
        E1 --> ET["Tally: Alice=5"]
        E1 --> ES["Summary"]
        E1 --> EW["🏆 Winner: Alice"]
        EW --> A1["Alice: Oatmeal Recipe"]
        A1 --> CP1["Cycle complete"]
    end

    subgraph "What the Pipeline Reconstructed"
        ROOT["ROOT"]
        ROOT --> M1["Moderator|dd2de..."]
    end
```

### 2.4 The JSON Aggregation Anti-Pattern

```sql
JSON_ARRAYAGG(JSON_OBJECT('parent' VALUE parent_id, ...)) AS edges_json
```

This collapses ALL edges for a session into a single JSON blob in `dag_summaries`. Problems:

1. **No incremental SSE**: The UI must re-parse the entire blob on every update
2. **No partial recomputation**: If one edge changes, the entire blob is rewritten
3. **State retention pressure**: The `GROUP BY session_id` forces Flink to retain all edges in state, bounded by the 10-minute `idleStateRetention` — meaning after 10 minutes of inactivity, the DAG simply **disappears**

---

## 3. The In-Reply-To Pattern

### Core Concept

Every event published to the chatroom carries an **optional** `parent_event_id` field. This field is the UUID of the **specific event** that this event responds to. It is:

- **Recorded at creation time** (zero additional latency)
- **Immutable once written** (append-only log semantics preserved)
- **Optional by design** (ROOT events have `parent_event_id = ""`)
- **A known fact, not a guess** (the writer has this information in local scope)

This is the exact analog of the `In-Reply-To:` header in RFC 2822 (email), or the `thread_ts` in Slack's API. The key insight from the human's concern is preserved:

> **"Maintain the independence of all operations and merely append a known fact to an existing process"**

The operations remain independent. No agent needs to "know about" the DAG, track global state, or wait for anything. Each writer simply appends one additional string field — a UUID it already holds in local scope — to the record it was already going to write.

### Why This Preserves the Flywheel

```mermaid
sequenceDiagram
    participant H as Human
    participant M as Moderator
    participant F as Fluss Log
    participant FK as Flink Pipeline

    Note over H,FK: CURRENT: Flywheel with information loss
    H->>F: {content: "Hi Alice", parent_event_id: ""}
    M->>F: {content: "Election...", parent_event_id: ""}
    M->>F: {content: "🏆 Winner: Alice", parent_event_id: ""}
    Note over FK: Flink JOIN: "Which Moderator event is parent?" → ??
    FK->>FK: Heuristic fails → ROOT

    Note over H,FK: PROPOSED: Flywheel with In-Reply-To header
    H->>F: {content: "Hi Alice", event_id: "a390", parent_event_id: ""}
    M->>F: {content: "Election...", event_id: "545c", parent_event_id: "a390"}
    M->>F: {content: "🏆 Winner: Alice", event_id: "2102", parent_event_id: "545c"}
    Note over FK: Flink SELECT: parent_event_id → deterministic edge
    FK->>FK: Direct projection → correct DAG
```

**No new network calls. No new dependencies. No blocking. The push-to-log operation is identical** — it just carries one more column that the writer already knows.

---

## 4. Architecture Overview

### Current Architecture (Broken)

```mermaid
flowchart TD
    subgraph Writers["Writers (Independent — ✅ Good)"]
        MOD[Moderator]
        AGT[Agents]
        SUB[Subagents]
        HUM[Human]
    end

    subgraph FlussLog["Fluss Chatroom Log"]
        LOG[(Append-Only Log<br/>event_id, actor_id, content,<br/>ts, parent_actor, type)]
    end

    subgraph FlinkPipeline["Flink Pipeline (❌ Broken)"]
        SJ[Self-JOIN chatroom c1<br/>LEFT JOIN chatroom c2<br/>ON parent_actor = actor_id<br/>AND ts ≤ ts + 1000]
        RN[ROW_NUMBER partition<br/>by event_id]
        JA[JSON_ARRAYAGG<br/>collapse to blob]
    end

    subgraph Sinks["Sink Tables"]
        DS[dag_summaries<br/>PK: session_id<br/>edges_json blob]
        DE[dag_events<br/>PK: session_id, child_id]
    end

    Writers --> LOG
    LOG --> SJ --> RN --> JA --> DS
    SJ --> RN --> DE

    style SJ fill:#ff6b6b,stroke:#c92a2a,color:#fff
    style RN fill:#ff6b6b,stroke:#c92a2a,color:#fff
    style JA fill:#ff6b6b,stroke:#c92a2a,color:#fff
```

### Proposed Architecture (Deterministic)

```mermaid
flowchart TD
    subgraph Writers["Writers (Independent — ✅ Unchanged)"]
        MOD[Moderator]
        AGT[Agents]
        SUB[Subagents]
        HUM[Human]
    end

    subgraph FlussLog["Fluss Chatroom Log"]
        LOG[(Append-Only Log<br/>event_id, actor_id, content,<br/>ts, parent_event_id, edge_type, type)]
    end

    subgraph FlinkPipeline["Flink Pipeline (✅ Simple Projection)"]
        SEL["SELECT<br/>  parent_event_id → parent_id<br/>  event_id → child_id<br/>  edge_type<br/>FROM chatroom"]
    end

    subgraph Sinks["Sink Tables"]
        DE2["dag_edges<br/>PK: session_id, child_id<br/>parent_id, edge_type, ts"]
    end

    Writers --> LOG
    LOG --> SEL --> DE2

    style SEL fill:#51cf66,stroke:#2b8a3e,color:#fff
```

### Causal Graph: What the DAG Should Look Like

Reconstructed from the production logs with correct parentage:

```mermaid
graph TD
    ROOT((ROOT))

    %% Session boot
    ROOT --> |SEQUENTIAL| BOOT["Moderator: System Online<br/>6ab2ec42"]

    %% Human asks Alice
    ROOT --> |SEQUENTIAL| H1["Human: Hi Alice, oatmeal?<br/>a390d88c"]
    H1 --> |SEQUENTIAL| E1["Moderator: Election Round 1<br/>545cf3ec"]
    E1 --> |SEQUENTIAL| TALLY1["Moderator: Tally Alice=5<br/>88d3546a"]
    E1 --> |SEQUENTIAL| SUMM1["Moderator: Summary<br/>8ed8ec27"]
    E1 --> |SEQUENTIAL| WIN1["Moderator: 🏆 Winner: Alice<br/>21020ef8"]
    WIN1 --> |SEQUENTIAL| ALICE["Alice: Oatmeal Recipe<br/>98eb5ea4"]
    ALICE --> |SEQUENTIAL| CP1["Moderator: Cycle complete<br/>4cb1d292"]

    %% Human asks Bob
    ROOT --> |SEQUENTIAL| H2["Human: Bob, add to board<br/>a7fca5d2"]
    H2 --> |SEQUENTIAL| E2["Moderator: Election Round 1<br/>4c35010c"]
    E2 --> |SEQUENTIAL| WIN2["Moderator: 🏆 Winner: Bob<br/>3d2464b9"]
    WIN2 --> |SEQUENTIAL| BOB_TOOL["Bob: $ board create<br/>1765baa5"]
    BOB_TOOL --> |SEQUENTIAL| BOB_RES["Bob: ✅ Created T-001<br/>404d9aa6"]
    BOB_RES --> |SEQUENTIAL| BOB_OUT["Bob: Task Created<br/>43f172c5"]

    %% Human asks Carol
    ROOT --> |SEQUENTIAL| H3["Human: Carol, spawn agents<br/>13ec6acc"]
    H3 --> |SEQUENTIAL| WIN3["Moderator: 🏆 Winner: Carol<br/>a02941a5"]
    WIN3 --> |SEQUENTIAL| CAROL_DEL1["Carol: $ delegate<br/>faf5b7d1"]

    %% Spawns
    CAROL_DEL1 --> |SPAWN| SUB_BDE["🔱 Sub/bde8ee35<br/>90bf5218"]
    SUB_BDE --> |SEQUENTIAL| SUB_BDE_OUT["Sub/bde8ee35: Promotion<br/>b139ae7e"]
    SUB_BDE_OUT --> |RETURN| CONV_BDE["Moderator: 🏁 bde8ee35 done<br/>9e72afb7"]

    CAROL_DEL1 --> |SPAWN| SUB_BCB["🔱 Sub/bcb81551<br/>5b274f8d"]
    SUB_BCB --> |SEQUENTIAL| SUB_BCB_TOOL["Sub/bcb81551: $ repo_map<br/>e7ffc029"]
    SUB_BCB --> |SEQUENTIAL| SUB_BCB_TO["Sub/bcb81551: ⏰ Timeout<br/>0bf68afa"]
    SUB_BCB_TO --> |RETURN| CONV_BCB["Moderator: 🏁 bcb81551 done<br/>a1679619"]

    CAROL_DEL1 --> |SPAWN| SUB_F0E["🔱 Sub/f0e7b250<br/>6d189b6a"]
    SUB_F0E --> |SEQUENTIAL| SUB_F0E_TOOL["Sub/f0e7b250: $ repo_map<br/>f70edc34"]
    SUB_F0E --> |SEQUENTIAL| SUB_F0E_TO["Sub/f0e7b250: ⏰ Timeout<br/>a1e8c91e"]
    SUB_F0E_TO --> |RETURN| CONV_F0E["Moderator: 🏁 f0e7b250 done<br/>a3378dd3"]

    style ROOT fill:#6c757d,color:#fff
    style SUB_BDE fill:#7950f2,color:#fff
    style SUB_BCB fill:#7950f2,color:#fff
    style SUB_F0E fill:#7950f2,color:#fff
```

---

## 5. Detailed Code Changes

### 5.1 Schema Layer (`schemas.py`)

**What changes:** Add two new fields to `CHATROOM_SCHEMA`.

**Why:**
- `parent_event_id` (string): The UUID of the event this record replies to. Empty string `""` for root events (human input, system boot). This is the In-Reply-To header.
- `edge_type` (string): One of `SEQUENTIAL`, `SPAWN`, `RETURN`, or `ROOT`. This records the *nature* of the causal link, not just its existence. Without this, the UI cannot distinguish "this agent continued working" from "this agent spawned a child".

**Defending the decision to put edge_type on the source event:**
The alternative would be to compute `edge_type` in Flink from the `type` field. But the `type` field represents the *content type* of the event (`thought`, `output`, `action`, `voting`, `system`, `checkpoint`, `convergence`), not the *causal relationship*. One content type can participate in different edge types: a `system` message might be a `SPAWN` (subagent creation) or a `SEQUENTIAL` (timeout notification). Only the writer knows the intent.

```diff
 CHATROOM_SCHEMA = pa.schema([
     pa.field("event_id", pa.string()),      # UUID — primary dedup key
     pa.field("session_id", pa.string()),
     pa.field("ts", pa.int64()),
     pa.field("actor_id", pa.string()),
     pa.field("content", pa.string()),
     pa.field("type", pa.string()),
     pa.field("tool_name", pa.string()),
     pa.field("tool_success", pa.bool_()),
     pa.field("parent_actor", pa.string()),
+    pa.field("parent_event_id", pa.string()),  # In-Reply-To: UUID of causal parent
+    pa.field("edge_type", pa.string()),         # SEQUENTIAL | SPAWN | RETURN | ROOT
 ])
```

**Backward compatibility:** Both new fields default to `""`. Existing events with `parent_event_id = ""` are treated as root nodes. The `parent_actor` field is **retained but deprecated** — it continues to be written for backward-compatible log consumers, but is no longer used by the DAG pipeline.

---

### 5.2 Publisher Layer (`publisher.py`)

**What changes:** Accept and propagate the two new fields through the publish path.

**Why:** The publisher is the single write path for all chatroom events. It must carry the new fields from the caller to the Fluss log. The change is purely structural — adding two more fields to the record dict and the PyArrow batch construction.

**Critical design decision: `publish()` returns `event_id`.**
This is already the case (the method returns a UUID string). This return value is what enables the In-Reply-To pattern: the caller publishes an event, receives its `event_id`, and passes that as `parent_event_id` to the next publish call. This is **zero-cost** — the UUID is generated at the top of the method and returned at the bottom. No new computation.

```diff
     async def publish(
         self,
         actor_id: str,
         content: str,
         m_type: str = "output",
         tool_name: str = "",
         tool_success: bool = False,
         parent_actor: str = "",
+        parent_event_id: str = "",
+        edge_type: str = "SEQUENTIAL",
     ) -> str:

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
+            "parent_event_id": parent_event_id,
+            "edge_type": edge_type,
         }
```

And in `_flush_locked()`, the batch construction adds the two new arrays:

```diff
             batch = pa.RecordBatch.from_pydict(
                 {
                     "event_id": [r["event_id"] for r in records],
                     ...
                     "parent_actor": [r["parent_actor"] for r in records],
+                    "parent_event_id": [r["parent_event_id"] for r in records],
+                    "edge_type": [r["edge_type"] for r in records],
                 },
                 schema=self._schema,
             )
```

---

### 5.3 Moderator Layer (`moderator.py`)

**What changes:** The `publish()` wrapper and the `run()` method now thread `parent_event_id` through the control flow.

**Why — the Moderator is the primary orchestator:**
The Moderator governs the election → execution → cycle loop. At each stage, it publishes events and knows the causal predecessor:

1. **System boot** → `parent_event_id=""`, `edge_type="ROOT"`
2. **Election start** → `parent_event_id` = the event that triggered the election (human input or previous cycle checkpoint)
3. **Election tally/summary/winner** → `parent_event_id` = the election-start event
4. **Agent output** → `parent_event_id` = the winner-announcement event
5. **Cycle checkpoint** → `parent_event_id` = the agent's output event

The Moderator doesn't need to "track the DAG" — it simply passes forward the `event_id` it just received. This is a **local variable**, not global state.

```diff
     async def publish(self, actor_id, content, m_type="output",
-                      tool_name="", tool_success=False, parent_actor=""):
+                      tool_name="", tool_success=False, parent_actor="",
+                      parent_event_id="", edge_type="SEQUENTIAL"):
         """Publish a message via the batched FlussPublisher."""
         try:
-            await self.publisher.publish(
+            return await self.publisher.publish(
                 actor_id=actor_id,
                 content=content,
                 m_type=m_type,
                 tool_name=tool_name,
                 tool_success=tool_success,
                 parent_actor=parent_actor,
+                parent_event_id=parent_event_id,
+                edge_type=edge_type,
             )
-            print(f"📝 [Moderator] Published: {actor_id} ({m_type})")
         except Exception as e:
             print(f"❌ [Moderator] Failed to publish: {e}")
+            return ""
```

**In `run()`:**

```python
async def run(self, autonomous_steps=0):
    ...
    # Boot event — no parent, this is a ROOT
    boot_id = await self.publish(
        "Moderator",
        f"Multi-Agent System Online. ConchShell: {conchshell_status}.",
        "thought",
        parent_event_id="",
        edge_type="ROOT",
    )

    # Track the "head" of the causal chain
    last_event_id = boot_id

    while True:
        ...
        if human_interrupted or (self.current_steps != 0):
            ...
            # Election start — caused by the last event (human input or checkpoint)
            election_start_id = await self.publish(
                "Moderator", f"🗳️ Election Round 1...", "thought",
                parent_event_id=last_event_id,
                edge_type="SEQUENTIAL",
            )

            winner, election_log, is_job_done = await self.election.run_election(...)

            # Election summary — child of election start
            await self.publish(
                "Moderator", f"Election Summary:\n{election_log}", "voting",
                parent_event_id=election_start_id,
                edge_type="SEQUENTIAL",
            )

            if winner:
                # Winner announcement — child of election start
                winner_id = await self.publish(
                    "Moderator", f"🏆 Winner: {winner}", "thought",
                    parent_event_id=election_start_id,
                    edge_type="SEQUENTIAL",
                )

                # Agent execution — the result is a child of the winner announcement
                resp = await self.executor.execute_with_tools(
                    winning_agent,
                    check_halt_fn=...,
                    parent_event_id=winner_id,  # NEW: pass context down
                )

                if resp and "[WAIT]" not in resp:
                    last_event_id = await self.publish(
                        winner, resp, "output",
                        parent_event_id=winner_id,
                        edge_type="SEQUENTIAL",
                    )

            # Checkpoint — closes the cycle
            last_event_id = await self.publish(
                "Moderator", "Cycle complete.", "checkpoint",
                parent_event_id=last_event_id,
                edge_type="SEQUENTIAL",
            )
```

**Critical insight: `_handle_single_message` does NOT need modification.** It only processes *incoming* messages from the log for context building. The causal metadata is only needed on the *write* path. The read path remains untouched.

---

### 5.4 Tool Executor Layer (`tool_executor.py`)

**What changes:** The executor accepts a `parent_event_id` parameter and threads it through tool call events.

**Why:** When an agent calls a tool, the ToolExecutor publishes multiple events:
1. The tool invocation (`$ board create ...`) — child of the winner announcement
2. The tool result (`✅ Created T-001`) — child of the tool invocation
3. The Moderator's result relay (`[Tool Result for Bob]`) — child of the tool invocation

Each event's `parent_event_id` is the event that *caused* it. The tool invocation is caused by the winner announcement. The tool result is caused by the tool invocation. This is a **strictly linear** chain within a tool round.

```diff
-    async def execute_with_tools(self, agent, check_halt_fn) -> str | None:
+    async def execute_with_tools(self, agent, check_halt_fn,
+                                  parent_event_id: str = "") -> str | None:

         ...
+        current_parent = parent_event_id

         for round_num in range(config.MAX_TOOL_ROUNDS):
             ...
             for call in fn_calls:
                 ...
                 # Publish tool invocation
-                await self.publish(
+                tool_call_id = await self.publish(
                     agent.agent_id,
                     f"$ {tool_name} {json.dumps(tool_args)[:200]}",
                     "action",
+                    parent_event_id=current_parent,
+                    edge_type="SEQUENTIAL",
                 )

                 result = await self.dispatcher.execute(...)

                 # Publish tool result (child of tool call)
-                await self.publish(
+                tool_result_id = await self.publish(
                     agent.agent_id,
                     f"{'✅' if result.success else '❌'} {result_summary[:500]}",
                     "action",
+                    parent_event_id=tool_call_id,
+                    edge_type="SEQUENTIAL",
                 )

                 # Moderator relay (child of tool call)
                 await self.publish(
                     "Moderator", tool_result_content, "action",
                     tool_name=tool_name,
                     tool_success=result.success,
                     parent_actor=agent.agent_id,
+                    parent_event_id=tool_call_id,
+                    edge_type="SEQUENTIAL",
                 )

+                current_parent = tool_result_id  # Chain for next tool call
```

**Note:** The `publish` function used by ToolExecutor is injected via `publish_fn` callback. The moderator's `self.publish` already accepts the new parameters. For subagents, the `_subagent_publish` wrapper in `subagent_manager.py` must pass them through as `**kwargs`.

---

### 5.5 Subagent Layer (`subagent_manager.py` + `agent_context.py`)

**What changes:** Subagent spawning records a `SPAWN` edge from the delegate tool call to the subagent's first event. Subagent completion records a `RETURN` edge.

**Why:** The delegate tool is the only place where the DAG branches. When Carol calls `delegate(task="...")`, the resulting subagent must be linked to Carol's delegate call with `edge_type="SPAWN"`. When the subagent finishes (or times out), the convergence event links back with `edge_type="RETURN"`.

#### In `SubagentManager.spawn()`:

The `spawn()` method needs to know the `event_id` of the delegate tool call that triggered it. This is passed from the `DelegateTool.execute()` method.

```diff
     async def spawn(
         self,
         task_desc: str,
         agent_persona: str = "General-purpose software engineer",
         tool_names: list[str] | None = None,
         available_tools: list[Tool] | None = None,
         timeout_s: int = 120,
+        parent_event_id: str = "",
     ) -> str:
         ...
         # Create the isolated context
         ctx = AgentContext.create(
             agent=agent,
             session_id=self.session_id,
             fluss_client=self.fluss,
             table=self.table,
             tool_dispatcher=dispatcher,
             parent_actor=f"Subagent/{task_id}",
+            parent_event_id=parent_event_id,
         )

         # System announcement — this is a SPAWN edge
-        await self.publisher.publish(
+        spawn_event_id = await self.publisher.publish(
             actor_id="Moderator",
             content=f"🔱 Spawned subagent {task_id} ({agent_persona}): {task_desc}",
             m_type="system",
+            parent_event_id=parent_event_id,
+            edge_type="SPAWN",
         )

         # Pass spawn_event_id to the subagent so it can chain from it
-        async_task = asyncio.create_task(
-            self._run_subagent(task_id, ctx, task_desc, timeout_s)
-        )
+        async_task = asyncio.create_task(
+            self._run_subagent(task_id, ctx, task_desc, timeout_s,
+                               parent_event_id=spawn_event_id)
+        )
```

#### In `AgentContext.publish()`:

The context tracks `parent_event_id` for its own event chain:

```diff
     async def publish(self, content: str, m_type: str = "output",
+                       parent_event_id: str = "",
+                       edge_type: str = "SEQUENTIAL"):
         if self.publisher:
-            await self.publisher.publish(
+            return await self.publisher.publish(
                 actor_id=self.agent_id,
                 content=content,
                 m_type=m_type,
                 parent_actor=self.parent_actor,
+                parent_event_id=parent_event_id,
+                edge_type=edge_type,
             )
+        return ""
```

#### Convergence (subagent completion):

In `_run_subagent()`'s `finally` block:

```diff
         finally:
             ...
             # Publish convergence event — RETURN edge back to main thread
-            await self.publisher.publish(
+            await self.publisher.publish(
                 actor_id="Moderator",
                 content=f"🏁 Subagent {task_id} completed.",
                 m_type="convergence",
+                parent_event_id=last_subagent_event_id,
+                edge_type="RETURN",
             )
```

#### In `DelegateTool.execute()`:

The delegate tool needs to capture the last published `event_id` from the tool executor's context so it can pass it to the subagent:

```diff
     async def execute(self, agent_id: str, params: dict) -> ToolResult:
         ...
         try:
             task_id = await self.subagent_manager.spawn(
                 task_desc=task_desc,
                 agent_persona=persona,
                 tool_names=tool_names,
                 available_tools=self.available_tools,
                 timeout_s=timeout_s,
+                parent_event_id=params.get("_parent_event_id", ""),
             )
```

The `_parent_event_id` is injected by the ToolExecutor when it invokes the delegate tool. This is an internal convention — the underscore prefix signals it's metadata, not a user-facing parameter.

---

### 5.6 Flink Pipeline Layer (`DagPipeline.java`)

**What changes:** Delete the self-join, window function, and JSON aggregation. Replace with a simple `SELECT` projection.

**Why:** With `parent_event_id` recorded in the log, the Flink pipeline doesn't need to *infer* anything. It simply reads the columns and constructs the edge.

#### New `getDeltaInsertSql()`:

```java
public static String getDeltaInsertSql() {
    return
        "INSERT INTO fluss_catalog.containerclaw.dag_edges\n"
        + "SELECT\n"
        + "    session_id,\n"
        + "    CASE\n"
        + "        WHEN parent_event_id = '' OR parent_event_id IS NULL THEN 'ROOT'\n"
        + "        ELSE parent_event_id\n"
        + "    END AS parent_id,\n"
        + "    event_id AS child_id,\n"
        + "    actor_id AS child_actor,\n"
        + "    CASE\n"
        + "        WHEN edge_type IS NOT NULL AND edge_type <> '' THEN edge_type\n"
        + "        ELSE 'SEQUENTIAL'\n"
        + "    END AS edge_type,\n"
        + "    CASE\n"
        + "        WHEN `type` IN ('finish', 'done', 'checkpoint', 'convergence') THEN 'DONE'\n"
        + "        WHEN `type` = 'action' THEN 'THINKING'\n"
        + "        ELSE 'ACTIVE'\n"
        + "    END AS status,\n"
        + "    ts AS updated_at\n"
        + "FROM fluss_catalog.containerclaw.chatroom";
}
```

**What was deleted:**

| Deleted Component | Lines | Why It Existed | Why It's No Longer Needed |
|---|---|---|---|
| `LEFT JOIN chatroom c2 ON ...` | 4 | Tried to find parent by actor name + time | `parent_event_id` gives us the exact parent |
| `ROW_NUMBER() OVER (...)` | 1 | Disambiguated multiple join matches | No join = no ambiguity |
| `WHERE rn = 1` | 1 | Picked "best" match from ROW_NUMBER | Not needed |
| `JSON_ARRAYAGG(JSON_OBJECT(...))` | 8 | Collapsed all edges into one blob | Replaced by per-edge rows |
| `getSnapshotInsertSql()` | 25 | Full-blob snapshot for polling API | Replaced by direct scan of `dag_edges` |

#### The snapshot pipeline is now optional:

The `dag_summaries` table (JSON blob per session) was needed because the old `dag_events` table was unreliable. With deterministic edges, the `dag_edges` table is the source of truth. The bridge can either:

1. **Scan `dag_edges`** for a full DAG load on session open
2. **Use `dag_summaries`** as a materialized view (if desired for O(1) lookup)

For simplicity, we recommend keeping only `dag_edges` and having the bridge perform a filtered scan. This eliminates the JSON aggregation entirely.

---

### 5.7 Telemetry Sink Tables (`TelemetryJob.java`)

**What changes:** Replace `dag_summaries` and `dag_events` with a single `dag_edges` table with richer schema.

```java
// New: deterministic per-edge table
tableEnv.executeSql(
    "CREATE TABLE IF NOT EXISTS fluss_catalog." + database + ".dag_edges ("
    + "    session_id STRING,"
    + "    parent_id STRING,"           // parent event_id or 'ROOT'
    + "    child_id STRING,"            // child event_id
    + "    child_actor STRING,"         // actor_id for UI label
    + "    edge_type STRING,"           // SEQUENTIAL | SPAWN | RETURN | ROOT
    + "    status STRING,"              // ACTIVE | THINKING | DONE
    + "    updated_at BIGINT,"
    + "    PRIMARY KEY (session_id, child_id) NOT ENFORCED"
    + ") WITH ('bucket.num' = '4', 'bucket.key' = 'session_id')"
);
```

The `actor_heads` table remains as-is (it's a simple projection).

The statement set becomes:

```java
stmtSet.addInsertSql(DagPipeline.getActorHeadsInsertSql());
stmtSet.addInsertSql(DagPipeline.getDeltaInsertSql());
stmtSet.addInsertSql(MetricsPipeline.getInsertSql());
// Removed: getSnapshotInsertSql()  — no longer needed
```

---

## 6. Invariants and Correctness Proof

### Invariant 1: Every non-root event has exactly one parent

**Proof:** The `parent_event_id` is set by the writer at creation time. It is either:
- `""` (root event) → `parent_id = "ROOT"` in the DAG
- A specific UUID → the writer held this UUID in local scope

There is no code path where `parent_event_id` is set to a non-existent UUID because:
1. The UUID comes from a previous `publish()` return value
2. `publish()` generates the UUID synchronously before returning
3. The UUID is a local variable, not read from the network

### Invariant 2: Causality flows strictly backward in time

**Proof:** If event `e₂` has `parent_event_id = e₁.event_id`, then `e₁` was published *before* `e₂` (because `e₂`'s writer needed `e₁.event_id` to construct the record, and `e₁.event_id` was only available after `e₁` was published). Therefore `e₁.ts < e₂.ts` in all cases where the wall clock is monotonic.

**Note:** In the degenerate case where the wall clock is non-monotonic (NTP adjustment), `ts` might not be ordered, but `parent_event_id` is still correct. This is a strict upgrade over the current system, which relies on `ts` ordering AND name matching AND proximity windowing.

### Invariant 3: The DAG is acyclic

**Proof:** Since causality flows strictly backward in time (Invariant 2), and each event has at most one parent (Invariant 1), the graph is a **forest** (set of trees). A forest is acyclic by definition.

### Invariant 4: The flywheel is preserved

**Proof:** No agent, tool, or moderator code waits for any new network call, downstream pipeline, or external state as a result of these changes. The only new operation is passing a UUID string (which is already in local scope) as an additional argument to `publish()`.

**Performance impact:** One additional string field per record. At 36 characters (UUID v4), this adds ~36 bytes per event. For a session with 1000 events, this is 36KB — negligible.

---

## 7. Migration Strategy

### Phase 1: Schema Extension (Non-Breaking)

1. Add `parent_event_id` and `edge_type` to `CHATROOM_SCHEMA` in `schemas.py`
2. Add corresponding fields to `publisher.py` with default values `""` and `"SEQUENTIAL"`
3. **All existing code continues to work unchanged** — new fields default to empty strings

### Phase 2: Writer Instrumentation (Incremental)

Instrument writers one at a time, in order of causal importance:

1. **`moderator.py`** — Boot, election, winner, checkpoint events
2. **`tool_executor.py`** — Tool call and result chains
3. **`subagent_manager.py`** — SPAWN and RETURN events
4. **`agent_context.py`** — Subagent-internal event chains

Each change can be tested independently. The DAG pipeline gracefully handles events with `parent_event_id = ""` by mapping them to `ROOT`.

### Phase 3: Pipeline Replacement

1. Drop the old `dag_summaries` and `dag_events` tables
2. Create the new `dag_edges` table
3. Deploy the simplified `DagPipeline` SQL
4. Update the bridge's `/telemetry/dag/<session_id>` endpoint to scan `dag_edges`

### Phase 4: Cleanup

1. Remove the `getSnapshotInsertSql()` method from `DagPipeline.java`
2. Remove the `_lookup_dag_snapshot()` function from `bridge.py`
3. Mark `parent_actor` as deprecated in `schemas.py` (keep for backward compat)

---

## 8. Verification Plan

### 8.1 Unit Test: Schema Roundtrip

Write a test that creates a `RecordBatch` with all schema fields (including new ones), serializes via `FlussPublisher._flush_locked()`, and verifies the batch contains the expected fields.

### 8.2 Integration Test: Causal Chain

Run a minimal moderator loop:

```
Human → Election → Winner → Agent Output → Checkpoint
```

Inspect the chatroom log and verify:
1. Every event except the human message has a non-empty `parent_event_id`
2. Following the `parent_event_id` chain from `Checkpoint` back to `ROOT` visits every event in order
3. No event's `parent_event_id` references a non-existent `event_id`

### 8.3 Integration Test: Spawn/Return

Trigger a delegate tool call and verify:
1. The spawn event has `edge_type = "SPAWN"` and `parent_event_id = delegate_tool_call_event_id`
2. The subagent's events chain via `SEQUENTIAL` edges
3. The convergence event has `edge_type = "RETURN"` and `parent_event_id = last_subagent_event_id`

### 8.4 Flink Pipeline Test

Run the simplified SQL against a Fluss table with the new schema and verify:
1. `dag_edges` contains one row per chatroom event
2. Every `parent_id` either equals `"ROOT"` or matches an existing `child_id`
3. The `edge_type` column correctly reflects `SPAWN`/`RETURN`/`SEQUENTIAL`

### 8.5 End-to-End: Inspect DAG Script

Run `scripts/inspect_dag.py` and verify:
1. `dag_edges` table contains >1 edges (vs. the current 1)
2. Edges form a valid tree/forest when traversed
3. Subagent edges correctly show spawn/return topology

---

## Summary of Changes

| File | Change | Impact |
|------|--------|--------|
| [`schemas.py`](../agent/src/schemas.py) | +2 fields to `CHATROOM_SCHEMA` | Schema definition |
| [`publisher.py`](../agent/src/publisher.py) | +2 params to `publish()`, +2 arrays in batch | Write path |
| [`moderator.py`](../agent/src/moderator.py) | Thread `parent_event_id` through run loop | Orchestration |
| [`tool_executor.py`](../agent/src/tool_executor.py) | Chain tool call events via `parent_event_id` | Tool execution |
| [`subagent_manager.py`](../agent/src/subagent_manager.py) | SPAWN/RETURN edges on spawn/converge | Subagent lifecycle |
| [`agent_context.py`](../agent/src/agent_context.py) | Accept/pass `parent_event_id` | Subagent write path |
| [`tools.py`](../agent/src/tools.py) (DelegateTool) | Pass `parent_event_id` to `spawn()` | Delegate integration |
| [`DagPipeline.java`](../telemetry/src/main/java/com/containerclaw/telemetry/DagPipeline.java) | Replace 60-line join with 15-line SELECT | Flink pipeline |
| [`TelemetryJob.java`](../telemetry/src/main/java/com/containerclaw/telemetry/TelemetryJob.java) | Replace `dag_summaries`/`dag_events` with `dag_edges` | Sink tables |
| [`bridge.py`](../bridge/src/bridge.py) | Update DAG endpoints to read `dag_edges` | API layer |

**Total complexity change:** Net negative. The Flink pipeline goes from ~60 lines of fragile SQL to ~15 lines of deterministic projection. The Python changes add ~30 lines of argument threading across 6 files. The system becomes **simpler overall** because the hard part (inferring causality) is eliminated entirely.
