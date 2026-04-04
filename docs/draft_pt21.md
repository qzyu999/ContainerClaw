# Draft Part 25: The Anchor Protocol — Dynamic Steering & Context Stabilization

This document formalizes the **Anchor Protocol**, an integrated memory and steering framework for the **ContainerClaw** swarm. It replaces the "Seahorse" concept with a more robust model of **Contextual Grounding**.

## 1. The Tiered Memory Stack (The Anchor System)


To maintain productivity at $t \to \infty$, the `ContextBuilder` assembles a layered payload categorized by its update frequency and injection point.

**Static Components (System Prepend)**:
* **The Spine (`SELF.md`)**: Permanent agent DNA, safety constraints, and core identity.
* **System Prompt**: Technical formatting rules and standard tool definitions.

**Dynamic Components (In-Flight Context)**:
* **`MEMORY.json` (L2 - Deferred for MVP)**: A stateful, compressed summary of the "Forgotten Zone."
* **Project Board Info (L3 - Surface HUD)**: Currently active tasks, sprint status, and board-level context.
* **The Flesh (Sliding Window)**: The most recent $N$ messages of raw chat history for high-fidelity nuance.
* **The Anchor (Postpended Steering)**: The absolute final injection. This contains technical directives and mission-critical guidance.
* **Wiki / Knowledge Base (L5 - Knowledge Items - Deferred for MVP)**: Curated, project-specific knowledge base and reference material.

**MVP Scope**: The initial implementation focuses on the **Spine**, the **Sliding Window**, and the **Anchor**. `MEMORY.json` and Wiki integration are slated for Phase 2+.

### 1.1 The "Turtle" (The Sea Chariot) Metaphor
The context window is a finite resource. To maintain progress, the agent behaves like a **"Turtle"** — which functions as the **Sea Chariot** — navigating a nebulous density of problems. 

In this model, the **Sea Horses** (the agents) pull the context window forward, while the human operator stands on the **Anchor Platform** to pull the **Reins** (the persistent anchor message) to stabilize the direction of the swarm.

Instead of an infinite "Grand Unified Memory," the Turtle utilizes **Critical Forgetting**: 
* **Manageable Scale**: Building a context window that is dense but compact, adapting to the immediate workspace.
* **Day 1 Velocity**: Like a new engineer jumping into a job, the Turtle starts "busy from day 1." It doesn't know the full past, but leverages imperfect documentation and HUD context to scale and prune operations in real-time.
* **Pivoting**: To progress through time, the agent must let go of the "nebulous" old sprawl to prioritize the high-density metadata required for the current mission.

**The "Turtle" Density Projection ($\rho$):**

```text
       Information Density ρ(t)
               ^
               |           .-----..
               |          /       \
               |        ./         \.  <-- The Turtle's Focus Zone
               |      ./             \.
               |    ./                 \.
               +---+----------------------+------> Time (t)
               t=0                          t=∞
```

$$ \rho(t) = \frac{1}{\sigma\sqrt{2\pi}} e^{-\frac{1}{2}\left(\frac{t-\mu}{\sigma}\right)^2} $$



## 2. Phase 2: Implementing the `MEMORY.json` Janitor (Future Work)
The **Janitor** is the background engine for the Turtle's "Critical Forgetting." It triggers when the sliding window reaches a threshold, reading "evicted" messages to update the structured JSON state.

**Example `MEMORY.json` Schema**:
```json
{
  "project_context": {
    "target_repo": "qzyu999/containerclaw",
    "active_subtask": "compaction_logic_fix",
    "environment_state": "sandboxed_docker"
  },
  "execution_state": {
    "known_bugs": ["AttributeError in fluss_client.py:42"],
    "successful_tools": ["ls", "cat", "grep"]
  },
  "steering_delta": "Moving from exploratory search to surgical edits."
}
```

## 3. Differentiating Intent (Human) vs. Steering (Anchor)
In a real dev team, "Human Messages" are the **Customer Intent** ("I need search working"), whereas the **Anchor** represents the **Senior Lead's Execution Guide** ("Use the v2 endpoint, check the linter output first").
* **Intent**: Stored in the `chatroom` Fluss table.
* **Steering**: Stored in the `anchor_message` Fluss table.

## 4. Telemetry & Observability: Tracking the Reins
Every update to the Anchor is logged with a timestamp in a dedicated Fluss table. When an operator "dives" into a trace using **Snorkel**, the engine reconstructs the exact Anchor that was active at that moment. This allows for a "Post-Mortem" on steering: *Did the agent fail because the lead (the Anchor) gave the wrong technical direction?*.

## 5. The "Surfboard" (Project Management)
The **Surfboard** is a play on the **Project Board**. It acts as the central task-tracking interface for the agents.

*   **Pivotal Context**: The agent's anchor must include instructions to be mindful of the Surfboard state — reviewing, adding, and editing tasks as they progress. 
*   **Execution Alignment**: When an agent "looks" at the context window, the Surfboard status (L3 HUD) provides the immediate milestone grounding.

## 6. The Anchor Tab (UI Control Plane)
The **Anchor Tab** is a new dedicated window alongside **Metrics**, **DAG**, and **Snorkel** on the right sidebar. It serves as the "steering wheel" for the human operator.

### 6.1 The Sea Chariot & The Reins
In this model, the **Turtle** (the context window) is the **Sea Chariot** and the agents are the **Sea Horses** pulling it forward. 

The **Anchor Tab** serves as the **Anchor Platform** where the human operator stands to pull the **Reins** (the anchor message). These reins provide stable, persistent reminders that steer the agents without the conversational drift of a standard chat message.

### 6.2 The Play as an Object (Shakespearean Inference)
We treat the entire chat history as an **Object** ($Chat \in \mathbb{O}$). Instead of simple Q&A, the LLM performs inference on a "Play" where:
1. **The Spine** (Pre-pended) defines the actors and the stage sets.
2. **The Chatroom** (History) is the ongoing dialogue.
3. **The Anchor** (Post-pended) provides the final stage directions and stable missions.

### 6.3 UI Layout: Real-time Control
The **Anchor Tab** provides a two-pane interface for immediate "re-steering" without the need to inject noisy user messages into the chat history.

*   **Top Pane (Context Tail)**: Shows the last $N$ messages of the sliding window for a quick "state of the world" review.
*   **Bottom Pane (Steering)**: A minimalist field to update the current constant mission.

```text
+---------------------------------------------------------------------+
| [ Chatroom ] [ Explorer ] [ DAG ] [ Metrics ] [ Snorkel ] [ *Anchor ] |
+---------------------------------------------------------------------+
| Latest Chatroom Messages                                             |
| ...                                                                 |
| Alice (Arch): Thinking about the Fluss architecture...              |
| Bob (PM): Executing board action [Update Task 42]                   |
+---------------------------------------------------------------------+
| CURRENT ANCHORING MESSAGE                                           |
|                                                                     |
| "Focus on refactoring the fluss_client.py. Ignore tests             |
| until the core stream logic is stable for deployment."              |
|                                                                     |
+---------------------------------------------------------------------+
| [ Template: Refactor ▼ ]                              [ DROP ANCHOR ] |
+---------------------------------------------------------------------+
```

### 6.4 Implementation Detail: The Anchor Pipeline
To implement the Anchor Tab, the following technical changes are required across the stack:

#### 1. Schema Definition (`agent/src/schemas.py`)
Add the `ANCHOR_MESSAGE_SCHEMA` to establish the persistence layer for human steering.
```python
ANCHOR_MESSAGE_SCHEMA = pa.schema([
    pa.field("session_id", pa.string()),
    pa.field("ts", pa.int64()),
    pa.field("content", pa.string()),
    pa.field("author", pa.string()),  # Tracking which operator set the anchor
])
ANCHOR_MESSAGE_TABLE = "anchor_message"
```

#### 2. Infrastructure Initialization (`agent/src/fluss_client.py`)
Update the `FlussClient.connect()` method to ensure the table is bootstrapped on startup.
*   **Action**: Add `self.anchor_table = await self._ensure_table(ANCHOR_MESSAGE_TABLE, ANCHOR_MESSAGE_SCHEMA)` to the initialization loop.
*   **Action**: Implement `fetch_latest_anchor(session_id)` to retrieve the most recent record for injection.

#### 3. Context Injection (`shared/context_builder.py`)
Modify `ContextBuilder.build_payload()` to accept an optional `anchor_text` parameter.
*   **Logic**: The `anchor_text` must be injected as the **absolute final message** in the payload (role: "user" or "system" depending on model sensitivity) to ensure it stays in the most recent attention head.
*   **Token Guard**: Ensure the anchor text is prioritized in the character budget alongside the system prompt.

#### 4. Agent Integration (`agent/src/agent.py`)
Update the `LLMAgent._call_gateway()` function to orchestrate the fetch:
```python
# Pseudo-logic for final payload assembly
latest_anchor = await fluss.fetch_latest_anchor(self.session_id)
messages = ContextBuilder.build_payload(
    raw_messages=history,
    anchor_text=latest_anchor,
    ...
)
```

#### 5. Bridge & UI
*   **Bridge (`bridge/src/main.py`)**: Add a POST endpoint `/session/{id}/anchor` that writes a new record to the `anchor_message` table.
*   **UI (`ui/src/components/AnchorView.tsx`)**: New component implementing the two-pane layout, fetching the chat tail via `/history` and pushing updates via the new bridge endpoint.




## 7. Appendix: Deterministic Context Reconstruction
To maintain accountability in a long-running swarm, we must be able to perform a "post-mortem" on any specific inference event. This process, known as **Snorkel Reconstruction**, allows an operator to see the exact context window an agent utilized at $t_{target}$.

### 7.1 The Reconstruction Algorithm
Given a session $S$, a target timestamp $t_{target}$, and an agent $A$, the bit-perfect context window $W$ is derived as follows:

1.  **Temporal History Scan**: Query the `chatroom` Fluss table for all records $R$ where $R.session\_id = S$ and $R.ts \le t_{target}$. Sort $R$ chronologically.
2.  **Anchor Retrieval**: Query the `anchor_message` Fluss table for the **latest record** $L$ where $L.session\_id = S$ and $L.ts \le t_{target}$.
3.  **Parameter Extraction**:
    *   Retrieve the steering mission: $Anchor = L.content$.
    *   Retrieve the history limit: $H = L.max\_history\_chars$ (falling back to `config.yaml` if null).
4.  **Payload Assembly**: Execute the `ContextBuilder.build_payload` function with:
    *   `raw_messages = R`
    *   `system_prompt = get_persona(A)`
    *   `anchor_text = Anchor`
    *   `max_history_chars = H`

### 7.2 The Token Guard (Budget Allocation)
The reconstruction respects the same priority-based character budget used during live inference:
$$ Budget_{Total} \ge len(System) + len(Anchor) + \sum len(History_{trimmed}) $$

Where the **History Window** is dynamically "squeezed" to ensure the **Anchor Mission** (the steering intent) is always fully present in the latest attention heads of the LLM.

## 8. Benchmark: Anchor Stability
We evaluate success by the **Context Drift Ratio**: the frequency with which an agent ignores the Anchor to follow a stale instruction in the chat history.
**Target**: <2% Drift. By positioning the Anchor as the final word at $t_{now}$, we ensure the agentic chariot remains stabilized against the information stream.