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