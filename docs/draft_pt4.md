# ContainerClaw — Multi-Agent Collaboration: Architectural Rigor

> **Complementary to:** [draft.md](file:///.../containerclaw/docs/draft.md), [draft_pt2.md](file:///.../containerclaw/docs/draft_pt2.md), and [draft_pt3.md](file:///.../containerclaw/docs/draft_pt3.md)  
> **Focus:** Technical Defense, Shared Bus Architecture, and Agency Mechanics  
> **Version:** 0.1.0-draft-pt4  

---

## 1. Architectural Evolution: The Bus-Centric Model

Phase 3 introduces a fundamental shift in ContainerClaw's topology. We are moving from a set of isolated spokes to a **Bus-Centric Architecture** where Apache Fluss serves as the "Common Knowledge Base" and communication medium.

### 1.1 Component Relationship Model
In this model, the "truth" of the session is not held by any single agent, but by the Fluss event stream.

```mermaid
graph TB
    subgraph "Control Plane"
        User[👤 Human User]
        Coordinator["🧠 Docker Compose<br/>(Service Discovery)"]
    end

    subgraph "Data Plane (Shared Bus)"
        Fluss[("📊 Apache Fluss<br/>(Unified Event Stream)")]
    end

    subgraph "Execution Plane (Sandboxed)"
        AgentA["🤖 Agent A<br/>(Primary Coder)"]
        AgentB["🤖 Agent B<br/>(Linter/Reviewer)"]
    end

    subgraph "Isolation Plane"
        Gateway["🔐 LLM Gateway<br/>(Credential Isolation)"]
        Workspace[("📁 Shared Workspace<br/>(File System)")]
    end

    User -->|gRPC/HTTP| AgentA
    User -->|gRPC/HTTP| AgentB
    AgentA -->|Append/Read| Fluss
    AgentB -->|Append/Read| Fluss
    AgentA -->|HTTP| Gateway
    AgentB -->|HTTP| Gateway
    AgentA -->|Read/Write| Workspace
    AgentB -->|Read/Write| Workspace
```

---

## 2. The Chatroom Sequence (Synchronous View)

To achieve the "Chatroom" effect, we use **Log-to-History Mapping**. Every agent poll result from Fluss is transformed into a natural language transcript that populates the LLM's `messages` array, creating a multiplexed perspective of the shared session.

```mermaid
sequenceDiagram
    participant Fluss as Apache Fluss
    participant AgentA as Agent A (Coder)
    participant AgentB as Agent B (Reviewer)

    Note over AgentA,AgentB: Concurrent Autonomous Loops

    AgentA->>Fluss: GET /v1/logs/session-01 (Current State)
    AgentA->>AgentA: Map Logs to "Human, Me, Collaborator"
    AgentA->>AgentA: LLM Inference (Think + Tool)
    AgentA->>Fluss: POST /v1/events (Action: Writing File)

    AgentB->>Fluss: GET /v1/logs/session-01 (Sees Agent A's Action)
    AgentB->>AgentB: Map Logs to "Human, Collaborator, Me"
    AgentB->>AgentB: LLM Inference (Determine need for intervention)
    
    alt Needs Intervention
        AgentB->>Fluss: POST /v1/events (Action: Message "@Agent A, check X")
    else No Intervention (Agency)
        AgentB->>Fluss: POST /v1/events (Action: [STAY_SILENT])
    end
```

---

## 3. Systematic Defense of the Implementation

The following design decisions are required to ensure the stability and coherence of the multi-agent system.

### 3.1 Persistent Identity via Actor ID
**Requirement**: Every event in Fluss must be tagged with an `actor_id` and `actor_type`.
**Defense**: Without explicit attribution, the mapping logic in the agents cannot distinguish between "Self" and "Collaborator." This would lead to a "Mirror Hallucination" where agents treat their own previous actions as if they were performed by someone else, or vice versa.

### 3.2 Chronological Truth via History Re-mapping
**Requirement**: Agents do not maintain an internal `history` list; instead, they reconstruct the transcript on every loop iteration from the Fluss source.
**Defense**: This ensures that even if Agent A's loop is faster than Agent B's, they both strictly adhere to the same chronological truth. It eliminates "state drift" between independent agent memories and ensures that feedback from one agent is immediately "heard" by the other.

### 3.3 Selective Participation via Agency Flag (`wait`)
**Requirement**: The LLM response schema must include a `wait` or `stay_silent` action.
**Defense**: In a real chatroom, forced responses lead to noise. If Agent B determines Agent A is performing correctly, a mandatory response (even a thought) can clutter the shared history and trigger unnecessary re-processing. The `wait` action allows for silent observation, which is critical for scaling beyond two agents.

---

## 4. Multi-Agent Scalability

While the MVP starts with two agents (Primary Coder and Secondary Reviewer), the **Bus-Centric Architecture** is designed for horizontal scaling:

1.  **Specialized Persona**: Adding a "Security Auditor" agent simply requires launching another container with a specific security-focused system prompt.
2.  **Stateless Execution**: Since the state is in Fluss and the Project Workspace, agents can be restarted, scaled, or replaced without session loss.
3.  **Conflict Resolution**: As agents share the `/workspace`, future iterations will integrate a distributed lock manager into the Fluss stream (e.g., `lock_acquired` events) to coordinate file access.

---

> **Design Defense Conclusion:** By elevating Apache Fluss from a logging sink to a shared communication bus, ContainerClaw moves from single-agent silos to a collaborative, human-like workflow environment that is technically robust and architecturally scalable.
