# ContainerClaw — Multi-Agent Collaboration via Apache Fluss

> **Complementary to:** [draft.md](file:///Users/jaredyu/Desktop/open_source/containerclaw/docs/draft.md) and [draft_pt2.md](file:///Users/jaredyu/Desktop/open_source/containerclaw/docs/draft_pt2.md)  
> **Focus:** Multi-Agent Workflows, Shared Stream Architecture, and Agent-to-Agent Collaboration  
> **Version:** 0.1.0-draft-pt3  

---

## 1. Overview: From Logging to Collaboration

In Phases 1 and 2, Apache Fluss was conceptualized primarily as a **secure audit trail**—an immutable record of what an autonomous agent did. In Phase 3, we elevate Fluss from a passive observer to the **Collaboration Hub**.

By leveraging Fluss's ability to handle high-throughput event streams with unified table/stream querying, we can move beyond parallel execution toward true **multi-agent collaboration**. Instead of individual agents working in silos, they participate in a shared "chatroom" where the stream of thoughts, actions, and observations is public to all participants in a session.

---

## 2. The "Chatroom" Experience

The core innovation is a **Shared Event Stream**. Every agent in a session appends its internal "thoughts" and external "actions" to the same Fluss table. 

### 2.1 Multi-Agent Concurrent Workflows
We move from a 1:1 (User:Agent) model to a 1:N (User:Agents) model. 

| Role | Responsibility |
|---|---|
| **Primary Agent (The Doer)** | Executes the main task, writes code, and runs tools. |
| **Secondary Agent (The Reviewer/Critic)** | Observes the Primary's logs in real-time, provides feedback, or suggests course corrections via the shared stream. |

### 2.2 Conceptual Data Flow

```mermaid
graph TD
    User[👤 Human User]
    
    subgraph "Apache Fluss Collaboration Hub"
        Stream[("Shared Event Stream<br/>(Session ID: xyz)")]
        Table[("Global State Table<br/>(Shared Memory)")]
    end
    
    Agent1["🤖 Agent A (Primary)"]
    Agent2["🤖 Agent B (Collaborator)"]
    
    User -->|Prompts| Stream
    Agent1 -->|Append Thoughts/Actions| Stream
    Agent2 -->|Append Feedback/Observations| Stream
    
    Agent1 --|Subscribes to| Stream
    Agent2 --|Subscribes to| Stream
    
    Agent1 <-->|Read/Write| Table
    Agent2 <-->|Read/Write| Table
```

---

## 3. Real-Time Collaboration via Apache Fluss

Apache Fluss provides the unique capability to treat logs as both a **stream** (for real-time reaction) and a **table** (for random access and historical context).

### 3.1 Shared History Subscription
Agents do not just send logs; they **subscribe** to the logs of other agents. When Agent A performs an action, Agent B receives that event through the Fluss stream and can immediately process it.

### 3.2 Global State Table
Beyond the event stream, Fluss tables can store shared state like:
- **Shared Variables**: Environment configurations known to all agents.
- **Task Board**: A list of tasks that agents can claim and update.
- **Resource Locks**: Coordinating access to specific files in the `/workspace`.

---

## 4. The Shared Chatroom Schema (MVP)

To make the MVP as simple and effective as possible, we implement a **Unified History Injected** model. Agent A and Agent B do not have separate silos of context; they both consume the exact same Fluss log as their "short-term memory."

### 4.1 Multiplexed Perspective
The prompt for each agent is calibrated to define their specific identity within the shared room:

- **Agent A Perspective**: "You are Agent A (The Coder). You see messages from Human, yourself (Agent A), and your collaborator (Agent B)."
- **Agent B Perspective**: "You are Agent B (The Reviewer). You see messages from Human, yourself (Agent B), and your collaborator (Agent A)."

In the Fluss stream, every entry is tagged with `actor_id`. When Agent A runs its loop, it fetches the log and renders the chat history by mapping `actor_id` to these labels.

### 4.2 Representative Interaction
```text
[Human]: Please implement a login function.
[Agent A (Thinking)]: I'll start by creating the auth utility.
[Agent A (Tool)]: Writing to src/auth.py...
[Agent B (Thinking)]: I see Agent A is writing the auth utility. I should check for salt hashing.
[Agent B (Message)]: @Agent A, don't forget to use a strong salt for the password hashing!
[Agent A (Thinking)]: Good point from Agent B. I'll update the logic.
```

---

## 5. Selective Participation & Agency

A critical requirement for a natural "chatroom" experience is that agents should not be forced into a rigid lock-step response cycle. 

### 5.1 The "Wait & Observe" Mechanic
Instead of a mandatory "Plan -> Act" sequence, agents are granted the agency to remain silent. If an agent observes the stream and determines that no intervention is needed (e.g., "Agent A has this under control" or "Nothing for me to review yet"), it can emit a `noop` or `observation-only` event.

### 5.2 Lightweight Interventions
Agents can also choose "low-friction" responses:
- **LGTM (Looks Good To Me)**: A simple acknowledgement to signal they are following along but have no objections.
- **Silent Thought**: Logging a thought to the stream (visible to others) without executing a disruptive tool.

### 5.3 Parallel Autonomy
Since Fluss is the source of truth, multiple agents can be "active" at once. Agent B can provide a review *while* Agent A is mid-task, and Agent A will "hear" that feedback in its next loop iteration.

---

## 6. MVP Implementation Highlights

### 6.1 Log-to-Prompt Injection
The Agent's `_run_loop` is upgraded to:
1. Fetch latest $N$ events from `http://fluss:9092/v1/logs/<session_id>`.
2. Format them into a human-readable chat transcript.
3. Replace the standard history with this "Chatroom Transcript."

### 6.2 Agency Logic
The LLM response schema is expanded to include a `wait` or `stay_silent` flag:
```json
{
  "thought": "Agent A is doing fine, I'll just keep watching.",
  "action": "wait",
  "reason": "No review needed yet."
}
```

---

> **Conclusion:** By treating the agent log as a real-time collaboration medium rather than a secondary dump, ContainerClaw enables a new class of resilient, multi-perspective autonomous workflows.
