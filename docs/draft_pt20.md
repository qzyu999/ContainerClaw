# Draft Part 20: Snorkel — High-Fidelity Agent Observability

This document outlines the technical design and implementation strategy for **Snorkel**, a dedicated observability feature that allows human operators to "dive" into the exact context window an agent or subagent perceived at any specific point in time.

## 1. Problem Statement: The Scaling "Black Box"
While the Spacetime DAG provides excellent causal visualization, it faces two primary challenges as sessions extend:
* **UX Breakdown**: High-turn sessions (100+ messages) make SVG-based graphs difficult to navigate and parse for specific text data.
* **Context Opacity**: Without Snorkel, the operator cannot verify if an agent's failure was due to a logic error or because critical information was truncated/filtered out of its context window by the system's `max_history` constraints.

## 2. The Snorkel Solution: Table-Centric Telemetry
Snorkel introduces a logstash-like UI tab designed for high-density information retrieval. Unlike the DAG, which focuses on causality, the Snorkel tab focuses on **perspective**.

### Key UI Components:
* **Streaming Log Table**: A virtualized, sortable table displaying every event in the `chatroom` Fluss table (Timestamp, Actor, Type, Content Snippet).
* **The Snorkel Action**: A dedicated button on each log entry that triggers the **Reconstruction Engine** to show exactly what that agent "saw" when the event was generated.
* **Perspective HUD**: A side-panel that renders the reconstructed context window using the agent's specific role-mapping (e.g., converting `actor_id` to OpenAI-style `user` or `assistant` roles).

## 3. Technical Implementation: The Reconstruction Engine
To maintain a "Zero-Bloat" data strategy, Snorkel does not store a copy of the context window for every message. Instead, it reconstructs the state on-demand.

### The Reconstruction Workflow:
1.  **Pinpointing**: The UI sends the `session_id`, `ts` (timestamp), and the target `agent_id` to the backend.
2.  **Historical Query**: The backend queries the `chatroom` table for all messages in that session where `ts <= target_ts`.
3.  **Deterministic Filtering**: The engine applies the system-wide constraints defined in `config.yaml`, such as `max_history_messages` and `max_history_chars`.
4.  **Perspective Rendering**: The engine processes the resulting message list through the agent's `_format_history()` logic, ensuring the human sees exactly what was sent to the LLM Gateway.

## 4. Architectural Integration
* **Configuration**: The Snorkel engine will be the primary consumer of the `prompts` and `settings` blocks in `config.yaml`, ensuring that the UI perfectly mirrors the production agent's filtering logic.
* **Causality Linking**: While Snorkel is a separate tab, it remains linked to the DAG. Users can click "Snorkel" from the DAG's metadata panel to jump directly to that event's reconstructed window.
* **Stream-Centricity**: By using the deterministic `parent_event_id` and `edge_type` fields in the Fluss schema, Snorkel can even reconstruct the windows of parallel subagents without polluting the main session history.

## 5. Success Metrics
* **No $O(N^2)$ Storage**: Context windows are derived, not duplicated.
* **Debug Precision**: Operators can pinpoint the exact turn where a prompt or model-specific truncation caused a hallucination or failure.
* **Scalability**: The table-based view allows for efficient browsing of thousands of turns through virtualized scrolling.

---

To provide a professional-grade observability experience, the Snorkel UI must balance high-density data with intuitive "dive" mechanics. Below is the expanded detail for **Draft Part 20**, focusing on the interface architecture, schema, and analytical tools.

# Draft Part 20: Snorkel — High-Fidelity Agent Observability (Expanded)

## 1. UI Architecture & Layout
The Snorkel interface should mimic a modernized ELK (Elastic-Logstash-Kibana) stack, optimized for streaming agentic workflows.

* **Primary View (Virtualized Table)**: A high-performance, sortable table that allows the operator to scroll through thousands of events without performance degradation.
* **Split-Pane Inspector**: Selecting any row in the table opens a right-hand "Perspective HUD." This pane renders the **Reconstructed Context Window**—exactly what the LLM received as input—including system instructions and formatted history.
* **The "Snorkel" Button**: A primary action on each row that triggers the reconstruction engine to fetch and filter historical messages relative to that event’s timestamp.

## 2. Table Schema & Column Definitions
The Snorkel log table directly reflects the `chatroom` Fluss schema while adding derived telemetry:

| Column | Description | Source |
| :--- | :--- | :--- |
| **Timestamp (TS)** | Precise UTC time of event creation. | `chatroom.ts` |
| **Actor** | The Agent ID (e.g., Alice) or system component (e.g., Moderator). | `chatroom.actor_id` |
| **Type** | Event category: `thought`, `action`, `output`, `voting`, or `system`. | `chatroom.type` |
| **Content Snippet**| A truncated preview of the message content for quick scanning. | `chatroom.content` |
| **Snorkel** | **[Action Button]**: Triggers the Reconstruction Engine for this event. | UI Component |
| [IGNORE FOR NOW - NOT TO BE IMPLEMENTED YET] **Causal Link** | A "jump" icon that navigates the user back to the event's node in the DAG. | `chatroom.parent_event_id` |
| [IGNORE FOR NOW - NOT TO BE IMPLEMENTED YET] **Context Size** | *Derived*: The total character/token count of the context window at this turn. | Backend Calculation |
| [IGNORE FOR NOW - NOT TO BE IMPLEMENTED YET] **Risk Score** | Visual indicator of the safety/hallucination risk for that turn. | `ActivityEvent.risk_score` |

## 3: The Reconstruction Engine — Fast & Efficient Derivation
To avoid an $O(N^2)$ storage explosion, Snorkel does not save a copy of the context window at every turn. Instead, it uses a **Deterministic Reconstruction Engine** to derive the agent's exact state in milliseconds.

#### 1. On-Click Interaction
When the "Snorkel" button is clicked for a specific row, the UI sends the `session_id`, the target event `ts` (timestamp), and the `agent_id` to the backend.

#### 2. The 4-Step Reconstruction Pipeline
The backend performs a stateless reconstruction that perfectly mirrors the agent's actual runtime logic:
* **Step 1: Historical Pinpointing**: The engine queries the `chatroom` Fluss table for all records where `session_id` matches and `ts <= target_ts`. Because Fluss is a high-performance stream store, this range-scan is extremely fast.
* **Step 2: Constraint Application**: The engine retrieves `max_history_messages` and `max_history_chars` from the `config.yaml` settings. It filters the resulting message list, dropping older messages exactly as the live system would.
* **Step 3: Perspective Formatting**: The filtered history is passed through the agent's specific `_format_history` method. This ensures the human sees the system notes and role mappings (e.g., "assistant" for the agent's own past messages, "user" for others) that the model actually perceived.
* **Step 4: System Instruction Injection**: The engine prepends the active `persona` and `system` instructions from the configuration for that specific turn.

#### 3. Why it is Efficient
* **Zero Storage Bloat**: No redundant context copies are stored in the database. The "Ground Truth" is rebuilt on the fly from the existing event log.
* **Deterministic Speed**: Since the historical records are immutable and the filtering rules (messages/chars) are fixed in `config.yaml`, the reconstruction is idempotent and executes within the same time-profile as a standard LLM turn.
* **Stateless Scaling**: The engine doesn't need to track running agents; it only needs the Fluss log, allowing for retrospective "Snorkeling" into sessions that ended days ago.

## 4. Advanced Filters & Navigation
To manage extended sessions, Snorkel provides specialized filtering logic:

* **Actor Isolation**: Toggle visibility for specific agents (e.g., "Show only Bob's thoughts and tool calls").
* [IGNORE FOR NOW - NOT TO BE IMPLEMENTED YET] **Subagent Branching**: A "Focus on Subagent" filter that leverages `edge_type` to isolate messages belonging to a specific spawning event, hiding the main chatroom noise.
* [IGNORE FOR NOW - NOT TO BE IMPLEMENTED YET] **Content Search**: Full-text search across the `content` field to find specific tool outputs or variables.
* [IGNORE FOR NOW - NOT TO BE IMPLEMENTED YET] **Safety Threshold**: A slider to filter for "High Risk" events only (e.g., events where `risk_score > 0.7`).
* **System Mute**: Quickly hide `checkpoint` and `moderator` system notes to focus purely on agent logic.

## 5. Visualizations & Health Metrics
Embedded within the Snorkel tab are small-scale "Sparkline" visualizations to monitor the health of the context window:

* [IGNORE FOR NOW - NOT TO BE IMPLEMENTED YET] **Context Pressure Gauge**: A horizontal bar showing how close the current window is to the `max_history_chars` limit before truncation occurs.
* [IGNORE FOR NOW - NOT TO BE IMPLEMENTED YET] **Token Velocity**: A line chart showing the speed of context growth (tokens per turn), helping to identify "chatty" agents that may need prompt adjustments.
* [IGNORE FOR NOW - NOT TO BE IMPLEMENTED YET] **Truncation Heatmap**: A visual indicator in the Perspective HUD showing which historical messages were excluded or "faded out" during the last turn due to window constraints.

## 6. [IGNORE FOR NOW - NOT TO BE IMPLEMENTED YET] Integration: The "DAG-to-Snorkel" Jump
The Spacetime DAG and Snorkel tab are bidirectional. Clicking "Snorkel Perspective" on a node in `DagView.tsx` will not only jump the user to the correct row in the log table but automatically trigger the **Step 1: Historical Pinpointing** workflow described above. This allows an operator to verify the *cause* of a specific branch in the DAG by immediately seeing the *context* that fed into it.